from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.acquisition.evaluate_acquisition import evaluate_acquisition, failure_category
from ai_ecosystem.db.init_db import init_database


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "data" / "interim").mkdir(parents=True)
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(["database:", "  path: db/test.sqlite"]),
        encoding="utf-8",
    )


def insert_run(connection: sqlite3.Connection, run_id: str, completed_at: str) -> None:
    connection.execute(
        """
        INSERT INTO acquisition_runs (
            acquisition_run_id,
            sample_id,
            fetch_scope,
            started_at,
            completed_at,
            pages_attempted,
            pages_fetched,
            pages_failed,
            success_rate,
            notes
        )
        VALUES (?, 'sample_test', 'discovered', '2026-06-07T10:00:00+00:00', ?, 4, 3, 1, 75.0, 'test')
        """,
        (run_id, completed_at),
    )


def insert_scraped(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    domain: str,
    url: str,
    scrape_status: str,
    http_status: int | None,
    content_type: str | None,
    content_length: int | None,
    word_count: int | None,
    title: str | None = "Title",
    meta_description: str | None = "Description",
    canonical_url: str | None = "https://example.com",
    language: str | None = "de",
    error_message: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO scraped_pages (
            url,
            final_url,
            domain,
            http_status,
            content_type,
            content_length,
            fetched_at,
            html_path,
            scrape_status,
            error_message,
            title,
            meta_description,
            canonical_url,
            language,
            word_count_estimate,
            redirect_count,
            retained_at_discovery,
            acquisition_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, '2026-06-07T10:01:00+00:00', ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
        """,
        (
            url,
            url if scrape_status == "success" else None,
            domain,
            http_status,
            content_type,
            content_length,
            f"data/raw/html/{domain}/test.html.gz" if scrape_status == "success" else None,
            scrape_status,
            error_message,
            title,
            meta_description,
            canonical_url,
            language,
            word_count,
            run_id,
        ),
    )


class EvaluateAcquisitionTest(unittest.TestCase):
    def test_evaluate_acquisition_exports_reports_and_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_run(connection, "run_test", "2026-06-07T10:02:00+00:00")
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="a.example",
                    url="https://a.example",
                    scrape_status="success",
                    http_status=200,
                    content_type="text/html",
                    content_length=100,
                    word_count=10,
                    language="de",
                )
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="a.example",
                    url="https://a.example/about",
                    scrape_status="success",
                    http_status=200,
                    content_type="text/html",
                    content_length=300,
                    word_count=30,
                    language="en",
                    meta_description=None,
                )
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="b.example",
                    url="https://b.example/file.pdf",
                    scrape_status="non_html",
                    http_status=200,
                    content_type="application/pdf",
                    content_length=500,
                    word_count=None,
                    title=None,
                    meta_description=None,
                    canonical_url=None,
                    language=None,
                )
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="b.example",
                    url="https://b.example/slow",
                    scrape_status="timeout",
                    http_status=None,
                    content_type=None,
                    content_length=None,
                    word_count=None,
                    title=None,
                    meta_description=None,
                    canonical_url=None,
                    language=None,
                    error_message="timed out",
                )
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="c.example",
                    url="https://c.example/missing-1",
                    scrape_status="http_error",
                    http_status=404,
                    content_type="text/html",
                    content_length=100,
                    word_count=None,
                    title=None,
                    meta_description=None,
                    canonical_url=None,
                    language=None,
                    error_message="HTTP status 404",
                )
                insert_scraped(
                    connection,
                    run_id="run_test",
                    domain="c.example",
                    url="https://c.example/missing-2",
                    scrape_status="http_error",
                    http_status=410,
                    content_type="text/html",
                    content_length=100,
                    word_count=None,
                    title=None,
                    meta_description=None,
                    canonical_url=None,
                    language=None,
                    error_message="HTTP status 410",
                )
                connection.commit()

            with patch("ai_ecosystem.acquisition.evaluate_acquisition.find_project_root", return_value=project_root):
                summary = evaluate_acquisition(acquisition_run_id="run_test")

            self.assertEqual(summary["run"]["acquisition_run_id"], "run_test")
            self.assertEqual(summary["domain_stats"][0]["domain"], "a.example")
            self.assertEqual(summary["domain_stats"][0]["pages_fetched"], 2)
            self.assertEqual(summary["domain_stats"][1]["pages_fetched"], 1)
            self.assertEqual(summary["domain_stats"][1]["pages_failed"], 1)
            failure_categories = {row["failure_category"] for row in summary["failures"]}
            self.assertIn("timeout", failure_categories)
            self.assertIn("non_html", failure_categories)
            http_4xx_rows = [row for row in summary["failures"] if row["failure_category"] == "http_4xx"]
            self.assertEqual(len(http_4xx_rows), 1)
            self.assertEqual(http_4xx_rows[0]["count"], 2)
            self.assertEqual(summary["metadata_coverage"][0]["field"], "title")

            for path in summary["export_paths"].values():
                self.assertTrue(path.exists())

            with summary["export_paths"]["content_types"].open("r", encoding="utf-8", newline="") as file:
                content_rows = list(csv.DictReader(file))
            self.assertEqual(content_rows[0]["content_type"], "text/html")
            self.assertEqual(content_rows[0]["count"], "4")

            with summary["export_paths"]["summary"].open("r", encoding="utf-8", newline="") as file:
                summary_rows = list(csv.DictReader(file))
            self.assertTrue(any(row["metric_group"] == "http_status" and row["metric"] == "200" for row in summary_rows))
            self.assertTrue(any(row["metric_group"] == "word_count" and row["metric"] == "median" for row in summary_rows))
            self.assertTrue(
                any(
                    row["metric_group"] == "metadata_coverage" and row["metric"] == "title"
                    for row in summary_rows
                )
            )

    def test_latest_selects_most_recent_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_run(connection, "older_run", "2026-06-07T10:02:00+00:00")
                insert_run(connection, "newer_run", "2026-06-07T11:02:00+00:00")
                insert_scraped(
                    connection,
                    run_id="newer_run",
                    domain="example.com",
                    url="https://example.com",
                    scrape_status="success",
                    http_status=200,
                    content_type="text/html",
                    content_length=100,
                    word_count=10,
                )
                connection.commit()

            with patch("ai_ecosystem.acquisition.evaluate_acquisition.find_project_root", return_value=project_root):
                summary = evaluate_acquisition(latest=True)

            self.assertEqual(summary["run"]["acquisition_run_id"], "newer_run")

    def test_failure_category_maps_http_status_ranges(self) -> None:
        row_404 = {"scrape_status": "http_error", "http_status": 404}
        row_503 = {"scrape_status": "http_error", "http_status": 503}
        row_timeout = {"scrape_status": "timeout", "http_status": None}

        self.assertEqual(failure_category(row_404), "http_4xx")
        self.assertEqual(failure_category(row_503), "http_5xx")
        self.assertEqual(failure_category(row_timeout), "timeout")


if __name__ == "__main__":
    unittest.main()
