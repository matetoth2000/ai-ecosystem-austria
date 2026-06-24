from __future__ import annotations

import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.acquisition.corpus_audit import evaluate_corpus
from ai_ecosystem.db.init_db import init_database


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(["database:", "  path: db/test.sqlite"]),
        encoding="utf-8",
    )


def insert_run(connection: sqlite3.Connection, run_id: str) -> None:
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
            success_rate
        )
        VALUES (?, 'sample_test', 'discovered', '2026-06-07T10:00:00+00:00',
                '2026-06-07T10:10:00+00:00', 4, 3, 1, 75.0)
        """,
        (run_id,),
    )


def insert_domain(connection: sqlite3.Connection, domain: str) -> None:
    connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))


def insert_discovered(
    connection: sqlite3.Connection,
    domain: str,
    normalized_url: str,
    method: str,
    retained: int,
) -> int:
    insert_domain(connection, domain)
    return connection.execute(
        """
        INSERT INTO discovered_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            retained_at_discovery,
            discovery_run_id
        )
        VALUES (?, ?, ?, ?, 'unknown', 0, ?, 'discovery_run')
        """,
        (domain, normalized_url, normalized_url, method, retained),
    ).lastrowid


def write_gzip(project_root: Path, rel_path: str, content: bytes) -> int:
    path = project_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as file:
        file.write(content)
    return path.stat().st_size


def insert_scraped(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    discovered_page_id: int,
    domain: str,
    url: str,
    status: str,
    html_path: str | None,
    language: str | None = "de",
    content_type: str | None = "text/html",
    word_count: int | None = 100,
    error_message: str | None = None,
    http_status: int | None = 200,
) -> None:
    connection.execute(
        """
        INSERT INTO scraped_pages (
            discovered_page_id,
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
        VALUES (?, ?, ?, ?, ?, ?, 1000, '2026-06-07T10:01:00+00:00',
                ?, ?, ?, 'Title', 'Description', ?, ?, ?, 0, 1, ?)
        """,
        (
            discovered_page_id,
            url,
            url if status == "success" else None,
            domain,
            http_status,
            content_type,
            html_path,
            status,
            error_message,
            url if status == "success" else None,
            language,
            word_count,
            run_id,
        ),
    )


class CorpusAuditTest(unittest.TestCase):
    def test_corpus_audit_exports_summary_domain_findings_examples_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            run_id = "run_corpus"
            html_path = "data/raw/html/a.example/page.html.gz"
            write_gzip(project_root, html_path, b"<html><body>hello world</body></html>")

            with sqlite3.connect(db_path) as connection:
                insert_run(connection, run_id)
                retained_homepage = insert_discovered(connection, "a.example", "https://a.example", "homepage", 1)
                non_retained_sitemap = insert_discovered(
                    connection, "a.example", "https://a.example/sitemap-page", "sitemap", 0
                )
                failed_sitemap = insert_discovered(connection, "b.example", "https://b.example/missing", "sitemap", 0)
                non_html = insert_discovered(connection, "b.example", "https://b.example/file.pdf", "sitemap", 0)
                insert_scraped(
                    connection,
                    run_id=run_id,
                    discovered_page_id=retained_homepage,
                    domain="a.example",
                    url="https://a.example",
                    status="success",
                    html_path=html_path,
                    language="ar-EG",
                    word_count=10,
                )
                insert_scraped(
                    connection,
                    run_id=run_id,
                    discovered_page_id=non_retained_sitemap,
                    domain="a.example",
                    url="https://a.example/sitemap-page",
                    status="success",
                    html_path=None,
                    language="de",
                    word_count=20000,
                )
                insert_scraped(
                    connection,
                    run_id=run_id,
                    discovered_page_id=failed_sitemap,
                    domain="b.example",
                    url="https://b.example/missing",
                    status="http_error",
                    html_path=None,
                    language=None,
                    content_type="text/html",
                    word_count=None,
                    error_message="HTTP status 404",
                    http_status=404,
                )
                insert_scraped(
                    connection,
                    run_id=run_id,
                    discovered_page_id=non_html,
                    domain="b.example",
                    url="https://b.example/file.pdf",
                    status="non_html",
                    html_path=None,
                    language=None,
                    content_type="application/pdf",
                    word_count=None,
                )
                connection.commit()

            with patch("ai_ecosystem.acquisition.corpus_audit.find_project_root", return_value=project_root):
                summary = evaluate_corpus(run_id)

            self.assertEqual(summary["json_summary"]["pages_attempted"], 4)
            self.assertEqual(summary["json_summary"]["failure_counts"]["http_4xx"], 1)
            retention = {row["group"]: row for row in summary["json_summary"]["retention_stats"]}
            self.assertEqual(retention["retained"]["attempted"], 1)
            self.assertEqual(retention["non_retained"]["attempted"], 3)
            methods = {row["group"]: row for row in summary["json_summary"]["discovery_method_stats"]}
            self.assertEqual(methods["homepage"]["attempted"], 1)
            self.assertEqual(methods["sitemap"]["attempted"], 3)
            self.assertTrue(any(row["example_type"] == "unusual_language_ar-EG" for row in summary["examples"]))
            self.assertTrue(any(row["finding_type"] == "largest_storage_usage" for row in summary["domain_findings"]))
            for path in summary["paths"].values():
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
