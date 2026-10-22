"""Offline parity and repeatable synthetic database-overhead comparison."""

import gzip
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import requests

from ai_ecosystem.acquisition.html_fetcher import (
    CandidatePage, existing_successful_scrape, fetch_candidate_page, upsert_scraped_page,
)
from ai_ecosystem.acquisition.run_acquisition import AcquisitionConfig, acquire_candidate
from ai_ecosystem.db.init_db import ensure_acquisition_schema, init_database
from test_html_fetcher import FakeResponse, FakeSession
from test_run_acquisition import (
    insert_discovered, insert_sample, run_quietly, successful_result, write_temp_config,
)


CONFIG = AcquisitionConfig(15, 2, 20, True, False, "TestBot", 25)
MODULE = "ai_ecosystem.acquisition.run_acquisition"


class AcquisitionPerformanceTest(unittest.TestCase):
    def fixture(self, root, count):
        write_temp_config(root)
        database = init_database(root)
        candidates = []
        with closing(sqlite3.connect(database)) as connection:
            insert_sample(connection, "sample", ["example.com"])
            for i in range(count):
                url = f"https://example.com/{i}"
                page_id = insert_discovered(connection, "example.com", url, url)
                candidates.append(CandidatePage(None, page_id, "example.com", url, url, "run_discovery"))
            connection.commit()
        return database, candidates

    def test_concurrent_persistence_single_schema_check_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, candidates = self.fixture(root, 80)

            def fetch(candidate, _root, run, *_args):
                return successful_result(candidate, run)

            with patch(f"{MODULE}.fetch_candidate_page", side_effect=fetch) as fetched:
                with patch(f"{MODULE}.ensure_acquisition_schema", wraps=ensure_acquisition_schema) as schema:
                    first = run_quietly("sample", "discovered", max_workers=20, project_root=root)
                    self.assertEqual(schema.call_count, 1)
                self.assertEqual(fetched.call_count, len(candidates))
                self.assertEqual(first["pages_fetched"], 80)
                fetched.reset_mock()
                second = run_quietly("sample", "discovered", max_workers=20, project_root=root)
                self.assertEqual(second["pages_skipped"], 80)
                fetched.assert_not_called()
                third = run_quietly("sample", "discovered", force_refresh=True, max_workers=20, project_root=root)
                self.assertEqual(third["pages_fetched"], 80)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 80)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            log = (root / "logs" / f"acquisition_{first['acquisition_run_id']}.log").read_text()
            self.assertEqual(log.count("page_timing "), 80)
            self.assertIn("database_write_seconds=", log)
            self.assertIn("setup_seconds=", log)

    def test_failed_write_rolls_back_and_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, candidates = self.fixture(root, 1)
            result = successful_result(candidates[0], "run")

            def fail_after_insert(connection, value):
                upsert_scraped_page(connection, value)
                raise RuntimeError("injected write failure")

            with patch(f"{MODULE}.fetch_candidate_page", return_value=result):
                with patch(f"{MODULE}.upsert_scraped_page", side_effect=fail_after_insert):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        acquire_candidate(database, root, candidates[0], "run", CONFIG)
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 0)
                self.assertEqual(acquire_candidate(database, root, candidates[0], "run", CONFIG).fetch_result, result)

    def test_completed_page_survives_interrupted_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _ = self.fixture(root, 4)
            submit = ThreadPoolExecutor.submit
            submitted = 0

            def interrupted_submit(executor, *args, **kwargs):
                nonlocal submitted
                submitted += 1
                if submitted == 2:
                    raise KeyboardInterrupt()
                future = submit(executor, *args, **kwargs)
                future.result()
                return future

            with patch(f"{MODULE}.fetch_candidate_page", side_effect=lambda c, r, run, *args: successful_result(c, run)):
                with patch.object(ThreadPoolExecutor, "submit", interrupted_submit):
                    with self.assertRaises(KeyboardInterrupt):
                        run_quietly("sample", "discovered", project_root=root)
                resumed = run_quietly("sample", "discovered", project_root=root)
            self.assertEqual(resumed["pages_skipped"], 1)
            self.assertEqual(resumed["pages_fetched"], 3)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 4)

    def test_stage_timing_preserves_html_metadata_and_retry_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = b'<html lang="en"><title>Company</title><body>Useful words</body></html>'
            candidate = CandidatePage(None, 1, "example.com", "https://example.com/", "https://example.com/", None)

            def response():
                return FakeResponse(url=candidate.url, headers={"Content-Type": "text/html"}, content=html)

            timing = {}
            session = FakeSession([requests.Timeout("timeout"), requests.Timeout("timeout"), response()])
            with patch("ai_ecosystem.acquisition.html_fetcher.utc_now_iso", return_value="fixed"):
                measured = fetch_candidate_page(candidate, root, "run", 15, 2, "TestBot", session, timing)
                original = fetch_candidate_page(candidate, root, "run", 15, 2, "TestBot", FakeSession([response()]))
            self.assertEqual(measured, original)
            self.assertEqual(session.calls, 3)
            self.assertEqual(set(timing), {"download", "html_processing", "html_storage"})
            self.assertTrue(all(value >= 0 for value in timing.values()))
            with gzip.open(root / measured.html_path, "rb") as stream:
                self.assertEqual(stream.read(), html)
            timing = {}
            failed = FakeSession([requests.Timeout("timeout")] * 3)
            result = fetch_candidate_page(candidate, root, "run", 15, 2, "TestBot", failed, timing)
            self.assertEqual(result.scrape_status, "timeout")
            self.assertEqual(failed.calls, 3)
            self.assertEqual(set(timing), {"download"})

    def test_repeatable_local_database_comparison(self):
        # No network; isolate the removed DDL using identical synthetic databases.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, candidates = self.fixture(root, 40)
            with closing(sqlite3.connect(database)) as connection:
                connection.executemany(
                    "INSERT INTO scraped_pages (url, domain, scrape_status) VALUES (?, 'example.com', 'timeout')",
                    ((f"https://example.com/old/{i}",) for i in range(50000)),
                )
                connection.commit()
                old_database = root / "old.sqlite"
                with closing(sqlite3.connect(old_database)) as target:
                    connection.backup(target)

            def old_worker(candidate):
                with closing(sqlite3.connect(old_database, timeout=60)) as connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    ensure_acquisition_schema(connection)
                    existing_successful_scrape(connection, None, candidate.discovered_page_id)
                result = successful_result(candidate, "run")
                with closing(sqlite3.connect(old_database, timeout=60)) as connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    ensure_acquisition_schema(connection)
                    upsert_scraped_page(connection, result)
                    connection.commit()

            start = time.perf_counter()
            for candidate in candidates:
                old_worker(candidate)
            old_seconds = time.perf_counter() - start
            with patch(f"{MODULE}.fetch_candidate_page", side_effect=lambda c, r, run, *args: successful_result(c, run)):
                start = time.perf_counter()
                for candidate in candidates:
                    acquire_candidate(database, root, candidate, "run", CONFIG)
                new_seconds = time.perf_counter() - start
            with closing(sqlite3.connect(database)) as current, closing(sqlite3.connect(old_database)) as old:
                columns = [row[1] for row in current.execute("PRAGMA table_info(scraped_pages)")
                           if row[1] not in {"created_at", "updated_at"}]
                query = f"SELECT {','.join(columns)} FROM scraped_pages ORDER BY scraped_page_id"
                self.assertEqual(current.execute(query).fetchall(), old.execute(query).fetchall())
            print(f"Offline DB comparison: 50000 existing rows, 40 pages; old={old_seconds:.3f}s new={new_seconds:.3f}s")
            # Deliberately no speed assertion: filesystem/cache timings vary by machine.
