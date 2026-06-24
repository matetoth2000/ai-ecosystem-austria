from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.acquisition.html_fetcher import CandidatePage, FetchResult, upsert_scraped_page
from ai_ecosystem.acquisition.run_acquisition import run_acquisition
from ai_ecosystem.db.init_db import init_database


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "logs").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "",
                "acquisition:",
                "  fetch_scope: discovered",
                "  timeout_seconds: 1",
                "  retry_count: 0",
                "  max_workers: 1",
                "  skip_existing: true",
                "  force_refresh: false",
                "  progress_log_every_n_pages: 1",
                "",
                "discovery:",
                '  user_agent: "TestBot/0.1"',
            ]
        ),
        encoding="utf-8",
    )


def insert_sample(connection: sqlite3.Connection, sample_id: str, domains: list[str]) -> None:
    connection.execute(
        """
        INSERT INTO sample_sets (
            sample_id,
            sample_name,
            sample_size,
            created_at
        )
        VALUES (?, ?, ?, '2026-06-07T10:00:00+00:00')
        """,
        (sample_id, sample_id, len(domains)),
    )
    for index, domain in enumerate(domains, start=1):
        connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
        connection.execute(
            "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES (?, ?, ?)",
            (sample_id, domain, index),
        )


def insert_discovered(
    connection: sqlite3.Connection,
    domain: str,
    url: str,
    normalized_url: str,
    *,
    retained: int = 0,
    priority: int = 3,
    method: str = "sitemap",
) -> int:
    return connection.execute(
        """
        INSERT INTO discovered_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            url_depth,
            retained_at_discovery,
            discovery_run_id
        )
        VALUES (?, ?, ?, ?, 'news', ?, 1, ?, 'run_discovery')
        """,
        (domain, url, normalized_url, method, priority, retained),
    ).lastrowid


def insert_candidate(
    connection: sqlite3.Connection,
    domain: str,
    url: str,
    normalized_url: str,
    *,
    discovery_run_id: str = "run_discovery",
) -> int:
    return connection.execute(
        """
        INSERT INTO candidate_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            url_depth,
            discovery_run_id
        )
        VALUES (?, ?, ?, 'homepage', 'homepage', 8, 0, ?)
        """,
        (domain, url, normalized_url, discovery_run_id),
    ).lastrowid


def successful_result(candidate: CandidatePage, acquisition_run_id: str) -> FetchResult:
    return FetchResult(
        candidate_page_id=candidate.candidate_page_id,
        discovered_page_id=candidate.discovered_page_id,
        url=candidate.url,
        final_url=candidate.url,
        domain=candidate.domain,
        http_status=200,
        content_type="text/html",
        content_length=42,
        fetched_at="2026-06-07T10:00:00+00:00",
        html_path=f"data/raw/html/{candidate.domain}/test.html.gz",
        scrape_status="success",
        error_message=None,
        title="Test",
        meta_description="Description",
        canonical_url=candidate.normalized_url,
        language="en",
        word_count_estimate=2,
        redirect_count=0,
        retained_at_discovery=1,
        acquisition_run_id=acquisition_run_id,
    )


def timeout_result(candidate: CandidatePage, acquisition_run_id: str) -> FetchResult:
    return FetchResult(
        candidate_page_id=candidate.candidate_page_id,
        discovered_page_id=candidate.discovered_page_id,
        url=candidate.url,
        final_url=None,
        domain=candidate.domain,
        http_status=None,
        content_type=None,
        content_length=None,
        fetched_at="2026-06-07T10:00:00+00:00",
        html_path=None,
        scrape_status="timeout",
        error_message="timed out",
        title=None,
        meta_description=None,
        canonical_url=None,
        language=None,
        word_count_estimate=None,
        redirect_count=0,
        retained_at_discovery=1,
        acquisition_run_id=acquisition_run_id,
    )


def run_quietly(*args, **kwargs) -> dict[str, object]:
    with redirect_stdout(StringIO()):
        return run_acquisition(*args, **kwargs)


def run_with_output(*args, **kwargs) -> tuple[dict[str, object], str]:
    output = StringIO()
    with redirect_stdout(output):
        summary = run_acquisition(*args, **kwargs)
    return summary, output.getvalue()


class RunAcquisitionTest(unittest.TestCase):
    def test_discovered_scope_uses_deterministic_max_pages_and_tracks_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sample(connection, "sample_test", ["b.example", "a.example"])
                insert_discovered(connection, "b.example", "https://b.example/z", "https://b.example/z")
                insert_discovered(connection, "a.example", "https://a.example/b", "https://a.example/b")
                insert_discovered(connection, "a.example", "https://a.example/a", "https://a.example/a")
                connection.commit()

            seen_urls: list[str] = []

            def fake_fetch(candidate, _project_root, acquisition_run_id, *_args):
                seen_urls.append(candidate.normalized_url)
                return successful_result(candidate, acquisition_run_id)

            with patch("ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page", side_effect=fake_fetch):
                summary = run_quietly(
                    sample_id="sample_test",
                    fetch_scope="discovered",
                    max_pages=2,
                    project_root=project_root,
                )

            self.assertEqual(seen_urls, ["https://a.example/a", "https://a.example/b"])
            self.assertEqual(summary["pages_attempted"], 2)
            self.assertEqual(summary["pages_fetched"], 2)
            self.assertEqual(summary["pages_failed"], 0)

            with sqlite3.connect(db_path) as connection:
                run_row = connection.execute(
                    """
                    SELECT sample_id, fetch_scope, completed_at, pages_attempted, pages_fetched,
                           pages_failed, success_rate
                    FROM acquisition_runs
                    WHERE acquisition_run_id = ?
                    """,
                    (summary["acquisition_run_id"],),
                ).fetchone()
                scraped_count = connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0]

            self.assertEqual(run_row[0], "sample_test")
            self.assertEqual(run_row[1], "discovered")
            self.assertIsNotNone(run_row[2])
            self.assertEqual(run_row[3:6], (2, 2, 0))
            self.assertEqual(run_row[6], 100.0)
            self.assertEqual(scraped_count, 2)

    def test_retained_scope_selects_candidate_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sample(connection, "sample_test", ["example.com"])
                candidate_id = insert_candidate(
                    connection,
                    "example.com",
                    "https://example.com",
                    "https://example.com",
                )
                connection.commit()

            seen_candidate_ids: list[int | None] = []

            def fake_fetch(candidate, _project_root, acquisition_run_id, *_args):
                seen_candidate_ids.append(candidate.candidate_page_id)
                return successful_result(candidate, acquisition_run_id)

            with patch("ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page", side_effect=fake_fetch):
                summary = run_quietly(
                    sample_id="sample_test",
                    fetch_scope="retained",
                    max_pages=1,
                    project_root=project_root,
                )

            self.assertEqual(seen_candidate_ids, [candidate_id])
            self.assertEqual(summary["pages_fetched"], 1)

    def test_skip_existing_does_not_refetch_successful_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sample(connection, "sample_test", ["example.com"])
                discovered_id = insert_discovered(
                    connection,
                    "example.com",
                    "https://example.com",
                    "https://example.com",
                    retained=1,
                )
                old_result = FetchResult(
                    candidate_page_id=None,
                    discovered_page_id=discovered_id,
                    url="https://example.com",
                    final_url="https://example.com",
                    domain="example.com",
                    http_status=200,
                    content_type="text/html",
                    content_length=10,
                    fetched_at="2026-06-07T09:00:00+00:00",
                    html_path="data/raw/html/example.com/old.html.gz",
                    scrape_status="success",
                    error_message=None,
                    title="Old",
                    meta_description=None,
                    canonical_url="https://example.com",
                    language="en",
                    word_count_estimate=1,
                    redirect_count=0,
                    retained_at_discovery=1,
                    acquisition_run_id="old_run",
                )
                upsert_scraped_page(connection, old_result)
                connection.commit()

            with patch("ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page") as fetch_mock:
                summary, output = run_with_output(
                    sample_id="sample_test",
                    fetch_scope="discovered",
                    max_pages=1,
                    project_root=project_root,
                )

            fetch_mock.assert_not_called()
            self.assertEqual(summary["pages_attempted"], 1)
            self.assertEqual(summary["pages_fetched"], 0)
            self.assertEqual(summary["pages_failed"], 0)
            self.assertEqual(summary["pages_skipped"], 1)
            self.assertIn("Skipped: 1", output)
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0], 1)

    def test_failures_are_recorded_and_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sample(connection, "sample_test", ["example.com"])
                insert_discovered(connection, "example.com", "https://example.com", "https://example.com")
                connection.commit()

            def fake_fetch(candidate, _project_root, acquisition_run_id, *_args):
                return timeout_result(candidate, acquisition_run_id)

            with patch("ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page", side_effect=fake_fetch):
                summary = run_quietly(
                    sample_id="sample_test",
                    fetch_scope="discovered",
                    max_pages=1,
                    project_root=project_root,
                )

            self.assertEqual(summary["pages_fetched"], 0)
            self.assertEqual(summary["pages_failed"], 1)
            log_path = project_root / "logs" / f"acquisition_{summary['acquisition_run_id']}.log"
            self.assertTrue(log_path.exists())
            self.assertIn("status=timeout", log_path.read_text(encoding="utf-8"))
            with sqlite3.connect(db_path) as connection:
                row = connection.execute("SELECT scrape_status FROM scraped_pages").fetchone()
            self.assertEqual(row[0], "timeout")

    def test_run_finalizes_acquisition_row_when_executor_submission_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sample(connection, "sample_test", ["example.com"])
                insert_discovered(connection, "example.com", "https://example.com", "https://example.com")
                connection.commit()

            with patch(
                "ai_ecosystem.acquisition.run_acquisition.ThreadPoolExecutor.submit",
                side_effect=RuntimeError("submission failed"),
            ):
                with self.assertRaises(RuntimeError):
                    run_quietly(
                        sample_id="sample_test",
                        fetch_scope="discovered",
                        max_pages=1,
                        project_root=project_root,
                    )

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT completed_at, pages_attempted, pages_fetched, pages_failed, success_rate
                    FROM acquisition_runs
                    """
                ).fetchone()

            self.assertIsNotNone(row[0])
            self.assertEqual(row[1], 1)
            self.assertEqual(row[2], 0)
            self.assertEqual(row[3], 0)
            self.assertEqual(row[4], 0.0)


if __name__ == "__main__":
    unittest.main()
