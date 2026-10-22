from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import requests

from ai_ecosystem.acquisition.html_fetcher import CandidatePage, fetch_candidate_page
from ai_ecosystem.acquisition.run_acquisition import DomainFailureGuard, load_acquisition_config
from ai_ecosystem.db.init_db import init_database
from test_html_fetcher import FakeSession
from test_run_acquisition import (
    insert_discovered,
    insert_sample,
    run_quietly,
    successful_result,
    timeout_result,
    write_temp_config,
)


FETCH = "ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page"


def fixture(root: Path, a_count: int, b_count: int, threshold: int = 2) -> Path:
    write_temp_config(root)
    config_path = root / "config" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "  progress_log_every_n_pages: 1",
            f"  progress_log_every_n_pages: 1\n  consecutive_connection_failure_threshold: {threshold}",
        ),
        encoding="utf-8",
    )
    database = init_database(root)
    with sqlite3.connect(database) as connection:
        insert_sample(connection, "sample_test", ["a.example", "b.example"])
        for domain, count in (("a.example", a_count), ("b.example", b_count)):
            for index in range(count):
                url = f"https://{domain}/{index}"
                insert_discovered(connection, domain, url, url)
        connection.commit()
    return database


class DomainFailureGuardTest(unittest.TestCase):
    def test_defaults_response_reset_and_latched_deferral(self) -> None:
        config = load_acquisition_config({}, force_refresh=False)
        self.assertEqual(config.consecutive_connection_failure_threshold, 5)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            load_acquisition_config(
                {"acquisition": {"consecutive_connection_failure_threshold": 0}},
                force_refresh=False,
            )

        guard = DomainFailureGuard(5)
        candidate = CandidatePage(None, 1, "a.example", "https://a.example", "https://a.example", None)
        timeout = timeout_result(candidate, "run")
        http_error = replace(timeout, scrape_status="http_error", http_status=404)
        for _ in range(4):
            self.assertIsNone(guard.record_result("a.example", timeout))
        self.assertIsNone(guard.try_start("a.example"))
        guard.record_result("a.example", http_error)
        for _ in range(4):
            self.assertIsNone(guard.record_result("a.example", timeout))
        self.assertEqual(guard.record_result("a.example", timeout), "5_consecutive_connection_failures")
        self.assertEqual(guard.try_start("a.example"), "5_consecutive_connection_failures")
        self.assertIsNone(guard.try_start("b.example"))
        guard.record_result("a.example", successful_result(candidate, "run"))
        self.assertEqual(guard.try_start("a.example"), "5_consecutive_connection_failures")

    def test_connection_error_kinds_count_and_unrelated_error_does_not_reset(self) -> None:
        candidate = CandidatePage(None, 1, "a.example", "https://a.example", "https://a.example", None)
        result = timeout_result(candidate, "run")
        guard = DomainFailureGuard(4)
        for status in ("timeout", "dns_error", "ssl_error"):
            guard.record_result("a.example", replace(result, scrape_status=status))
        guard.record_result("a.example", replace(result, scrape_status="other_error"))
        self.assertEqual(
            guard.record_result("a.example", replace(result, scrape_status="connection_error")),
            "4_consecutive_connection_failures",
        )

    def test_retries_count_once_per_failed_page(self) -> None:
        candidate = CandidatePage(None, 1, "a.example", "https://a.example", "https://a.example", None)
        guard = DomainFailureGuard(2)
        with tempfile.TemporaryDirectory() as directory:
            for page in range(2):
                session = FakeSession([requests.Timeout("timeout")] * 3)
                result = fetch_candidate_page(candidate, Path(directory), "run", 15, 2, "TestBot", session)
                self.assertEqual(session.calls, 3)
                self.assertEqual(result.scrape_status, "timeout")
                reason = guard.record_result("a.example", result)
                self.assertEqual(reason, "2_consecutive_connection_failures" if page == 1 else None)

    def test_deferred_urls_remain_retryable_next_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = fixture(root, a_count=7, b_count=2)
            calls: list[str] = []

            def first_fetch(candidate, _root, run_id, *_args):
                calls.append(candidate.url)
                if candidate.domain == "b.example":
                    return successful_result(candidate, run_id)
                index = int(candidate.url.rsplit("/", 1)[1])
                if index == 1:
                    return replace(timeout_result(candidate, run_id), scrape_status="http_error", http_status=404)
                return timeout_result(candidate, run_id)

            with patch(FETCH, side_effect=first_fetch):
                first = run_quietly("sample_test", "discovered", max_workers=1, project_root=root)
            self.assertEqual(first["pages_selected"], 9)
            self.assertEqual(first["pages_attempted"], 6)
            self.assertEqual(first["pages_fetched"], 2)
            self.assertEqual(first["pages_failed"], 4)
            self.assertEqual(first["pages_deferred"], 3)
            self.assertEqual(first["pages_skipped"], 0)
            self.assertEqual(first["deferred_domains"], {
                "a.example": {"reason": "2_consecutive_connection_failures", "trigger_status": "timeout", "count": 3}
            })
            self.assertNotIn("https://a.example/4", calls)
            self.assertIn("https://b.example/0", calls)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 6)
                self.assertEqual(connection.execute(
                    "SELECT pages_attempted,pages_fetched,pages_failed FROM acquisition_runs WHERE acquisition_run_id=?",
                    (first["acquisition_run_id"],),
                ).fetchone(), (6, 2, 4))
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM discovered_pages WHERE domain='a.example'"
                ).fetchone()[0], 7)
            log = (root / "logs" / f"acquisition_{first['acquisition_run_id']}.log").read_text()
            self.assertIn("domain_deferred domain=a.example reason=2_consecutive_connection_failures", log)
            self.assertIn("deferred_summary domain=a.example", log)
            self.assertIn("url_deferred url=https://a.example/4 domain=a.example reason=2_consecutive_connection_failures", log)
            self.assertIn("pages_deferred=3", log)

            retried: list[str] = []

            def second_fetch(candidate, _root, run_id, *_args):
                retried.append(candidate.url)
                return successful_result(candidate, run_id)

            with patch(FETCH, side_effect=second_fetch):
                second = run_quietly("sample_test", "discovered", max_workers=1, project_root=root)
            self.assertEqual(second["pages_selected"], 9)
            self.assertEqual(second["pages_attempted"], 7)
            self.assertEqual(second["pages_fetched"], 7)
            self.assertEqual(second["pages_skipped"], 2)
            self.assertEqual(second["pages_deferred"], 0)
            self.assertEqual(second["deferred_domains"], {})
            self.assertEqual(len(retried), 7)
            self.assertIn("https://a.example/4", retried)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 9)

    def test_inflight_request_finishes_and_other_domain_progresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = fixture(root, a_count=9, b_count=2)
            release_first = Event()
            first_started = Event()
            other_started = Event()
            started: list[str] = []

            def fetch(candidate, _root, run_id, *_args):
                started.append(candidate.url)
                if candidate.url == "https://a.example/0":
                    first_started.set()
                    self.assertTrue(release_first.wait(10))
                    return successful_result(candidate, run_id)
                if candidate.domain == "a.example":
                    self.assertTrue(first_started.wait(10))
                    return timeout_result(candidate, run_id)
                other_started.set()
                return successful_result(candidate, run_id)

            try:
                with patch(FETCH, side_effect=fetch):
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            run_quietly, "sample_test", "discovered", max_workers=2, project_root=root
                        )
                        try:
                            self.assertTrue(other_started.wait(10), "queued other domain never progressed")
                            self.assertFalse(release_first.is_set())
                        finally:
                            release_first.set()
                        summary = future.result(timeout=10)
            finally:
                release_first.set()
            self.assertEqual(summary["pages_fetched"], 3)
            self.assertEqual(summary["pages_failed"], 2)
            self.assertEqual(summary["pages_attempted"], 5)
            self.assertEqual(summary["pages_deferred"], 6)
            self.assertEqual(
                {url for url in started if "a.example" in url},
                {f"https://a.example/{index}" for index in range(3)},
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 5)


if __name__ == "__main__":
    unittest.main()
