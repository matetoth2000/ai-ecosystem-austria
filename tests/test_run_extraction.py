from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.extraction.run_extraction import ExtractedDocument, run_extraction


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "extraction:",
                "  extraction_version: v_test",
                "  output_root: data/extracted",
                "  markdown_directory: markdown",
                "  text_directory: text",
                "  overwrite_existing: false",
            ]
        ),
        encoding="utf-8",
    )


def write_gzip_html(project_root: Path, relative_path: str, html: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as file:
        file.write(html.encode("utf-8"))


def insert_scraped_page(
    connection: sqlite3.Connection,
    *,
    url: str,
    domain: str,
    html_path: str | None,
    scrape_status: str = "success",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO scraped_pages (url, domain, html_path, scrape_status)
        VALUES (?, ?, ?, ?)
        """,
        (url, domain, html_path, scrape_status),
    )
    return int(cursor.lastrowid)


def insert_validation_sample_row(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    scraped_page_id: int,
    domain: str,
    url: str,
) -> None:
    cursor = connection.execute(
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
        VALUES (?, ?, ?, 'homepage', 'about', 80, 1, 'test_run')
        """,
        (domain, url, url),
    )
    discovered_page_id = int(cursor.lastrowid)
    connection.execute(
        """
        UPDATE scraped_pages
        SET discovered_page_id = ?
        WHERE scraped_page_id = ?
        """,
        (discovered_page_id, scraped_page_id),
    )
    connection.execute(
        """
        INSERT INTO retention_validation_sample (
            sample_id,
            sample_version,
            sample_type,
            scraped_page_id,
            discovered_page_id,
            domain,
            url,
            retained_at_discovery,
            page_type_guess,
            priority,
            discovery_method,
            selection_reason
        )
        VALUES (?, 'v1', 'smoke', ?, ?, ?, ?, 1, 'about', 80, 'homepage', 'test')
        """,
        (sample_id, scraped_page_id, discovered_page_id, domain, url),
    )


class RunExtractionTest(unittest.TestCase):
    def test_successful_extraction_writes_canonical_evidence_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            html_path = "data/raw/html/example.at/page.html.gz"
            write_gzip_html(
                project_root,
                html_path,
                """
                <html><body><main>Hello world</main>
                <a href="/internal" rel="nofollow">Internal Link</a>
                <a href="https://external.at/x">External site</a>
                <a href="mailto:test@example.at">Mail us</a>
                </body></html>
                """,
            )
            with sqlite3.connect(db_path) as connection:
                scraped_page_id = insert_scraped_page(
                    connection,
                    url="https://example.at/a",
                    domain="example.at",
                    html_path=html_path,
                )
                connection.commit()

            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                return_value=ExtractedDocument(
                    markdown="# Hello **world**\n\n- item one",
                    language="en",
                    metadata={"author": "Example Author"},
                ),
            ) as extract_mock:
                summary = run_extraction(page_limit=10, project_root=project_root)

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT scraped_page_id, extraction_version, extraction_status, language,
                           extracted_at, markdown_path, text_path, metadata_path,
                           content_sha256, extraction_error, extraction_run_id
                    FROM extracted_pages
                    """
                ).fetchone()
                feature = connection.execute(
                    """
                    SELECT title, word_count, heading_count, headings, heading_hierarchy,
                           paragraph_count, language, markdown_path, text_path
                    FROM page_features
                    """
                ).fetchone()
                links = connection.execute(
                    """
                    SELECT target_url, normalized_target_url, target_domain,
                           normalized_target_domain, anchor_text, anchor_word_count,
                           rel_attribute, internal_external, is_internal, link_order
                    FROM page_links
                    ORDER BY link_order
                    """
                ).fetchall()
                run = connection.execute(
                    """
                    SELECT extraction_run_id, selected_pages, processed_pages,
                           successful_pages, skipped_pages, failed_pages, finished_at
                    FROM extraction_runs
                    """
                ).fetchone()

            self.assertEqual(summary["pages_selected"], 1)
            self.assertEqual(summary["processed"], 1)
            self.assertEqual(summary["successful"], 1)
            self.assertEqual(row[0], scraped_page_id)
            self.assertEqual(row[1], "v_test")
            self.assertEqual(row[2], "success")
            self.assertEqual(row[3], "en")
            self.assertIsNotNone(row[4])
            markdown_path = project_root / row[5]
            text_path = project_root / row[6]
            metadata_path = project_root / row[7]
            self.assertTrue(markdown_path.is_file())
            self.assertTrue(text_path.is_file())
            self.assertTrue(metadata_path.is_file())
            self.assertEqual(markdown_path.name, "page.md")
            self.assertEqual(text_path.name, "page.txt")
            self.assertEqual(metadata_path.name, "metadata.json")
            self.assertEqual(markdown_path.parent, text_path.parent)
            self.assertEqual(markdown_path.parent.parent.name, "000000")
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), "# Hello **world**\n\n- item one\n")
            plain_text = text_path.read_text(encoding="utf-8").strip()
            self.assertEqual(plain_text, "Hello world\nitem one")
            expected_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
            self.assertEqual(row[8], expected_hash)
            self.assertIsNone(row[9])
            self.assertEqual(row[10], summary["extraction_run_id"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["scraped_page_id"], scraped_page_id)
            self.assertEqual(metadata["url"], "https://example.at/a")
            self.assertEqual(metadata["domain"], "example.at")
            self.assertEqual(metadata["extraction_version"], "v_test")
            self.assertEqual(metadata["language"], "en")
            self.assertEqual(metadata["markdown_filename"], "page.md")
            self.assertEqual(metadata["text_filename"], "page.txt")
            self.assertEqual(metadata["content_sha256"], expected_hash)
            self.assertEqual(metadata["word_count"], 4)
            self.assertEqual(metadata["trafilatura_metadata"]["author"], "Example Author")
            self.assertEqual(feature[0], None)
            self.assertEqual(feature[1], 4)
            self.assertEqual(feature[2], 1)
            self.assertEqual(json.loads(feature[3]), ["Hello world"])
            self.assertEqual(json.loads(feature[4]), [{"level": 1, "text": "Hello world"}])
            self.assertEqual(feature[5], 1)
            self.assertEqual(feature[6], "en")
            self.assertEqual(feature[7], row[5])
            self.assertEqual(feature[8], row[6])
            self.assertEqual(len(links), 2)
            self.assertEqual(links[0][1], "https://example.at/internal")
            self.assertEqual(links[0][2], "example.at")
            self.assertEqual(links[0][3], "example.at")
            self.assertEqual(links[0][4], "Internal Link")
            self.assertEqual(links[0][5], 2)
            self.assertEqual(links[0][6], "nofollow")
            self.assertEqual(links[0][7], "internal")
            self.assertEqual(links[0][8], 1)
            self.assertEqual(links[0][9], 1)
            self.assertEqual(links[1][3], "external.at")
            self.assertEqual(links[1][7], "external")
            self.assertEqual(links[1][8], 0)
            self.assertEqual(run[0], summary["extraction_run_id"])
            self.assertEqual(run[1], 1)
            self.assertEqual(run[2], 1)
            self.assertEqual(run[3], 1)
            self.assertEqual(run[4], 0)
            self.assertEqual(run[5], 0)
            self.assertIsNotNone(run[6])
            extract_mock.assert_called_once()

    def test_malformed_html_failure_is_recorded_and_run_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            write_gzip_html(project_root, "data/raw/html/example.at/bad.html.gz", "<html><body>")
            write_gzip_html(project_root, "data/raw/html/example.at/good.html.gz", "<html><body>good</body></html>")
            with sqlite3.connect(db_path) as connection:
                insert_scraped_page(
                    connection,
                    url="https://example.at/a",
                    domain="example.at",
                    html_path="data/raw/html/example.at/bad.html.gz",
                )
                insert_scraped_page(
                    connection,
                    url="https://example.at/b",
                    domain="example.at",
                    html_path="data/raw/html/example.at/good.html.gz",
                )
                connection.commit()

            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                side_effect=[RuntimeError("parser failed"), ExtractedDocument("good")],
            ):
                summary = run_extraction(project_root=project_root)

            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT extraction_status, extraction_error FROM extracted_pages ORDER BY scraped_page_id"
                ).fetchall()

            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["successful"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(rows[0][0], "failed")
            self.assertIn("extraction_error", rows[0][1])
            self.assertEqual(rows[1][0], "success")

    def test_empty_html_and_empty_extraction_are_recorded_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            write_gzip_html(project_root, "data/raw/html/example.at/empty.html.gz", "")
            with sqlite3.connect(db_path) as connection:
                insert_scraped_page(
                    connection,
                    url="https://example.at/empty",
                    domain="example.at",
                    html_path="data/raw/html/example.at/empty.html.gz",
                )
                connection.commit()

            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                side_effect=ValueError("empty extraction"),
            ):
                summary = run_extraction(project_root=project_root)

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT extraction_status, extraction_error FROM extracted_pages"
                ).fetchone()

            self.assertEqual(summary["failed"], 1)
            self.assertEqual(row[0], "failed")
            self.assertIn("empty_extraction", row[1])

    def test_unreadable_gzip_and_missing_html_are_recorded_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            bad_path = project_root / "data/raw/html/example.at/not_gzip.html.gz"
            bad_path.parent.mkdir(parents=True, exist_ok=True)
            bad_path.write_text("not gzip", encoding="utf-8")
            with sqlite3.connect(db_path) as connection:
                insert_scraped_page(
                    connection,
                    url="https://example.at/missing",
                    domain="example.at",
                    html_path="data/raw/html/example.at/missing.html.gz",
                )
                insert_scraped_page(
                    connection,
                    url="https://example.at/not-gzip",
                    domain="example.at",
                    html_path="data/raw/html/example.at/not_gzip.html.gz",
                )
                connection.commit()

            summary = run_extraction(project_root=project_root)

            with sqlite3.connect(db_path) as connection:
                errors = [
                    row[0]
                    for row in connection.execute(
                        "SELECT extraction_error FROM extracted_pages ORDER BY scraped_page_id"
                    )
                ]

            self.assertEqual(summary["failed"], 2)
            self.assertIn("missing_html", errors[0])
            self.assertIn("unreadable_gzip", errors[1])

    def test_skip_existing_and_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            html_path = "data/raw/html/example.at/page.html.gz"
            write_gzip_html(project_root, html_path, "<html><body>Hello</body></html>")
            with sqlite3.connect(db_path) as connection:
                insert_scraped_page(
                    connection,
                    url="https://example.at/a",
                    domain="example.at",
                    html_path=html_path,
                )
                connection.commit()

            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                return_value=ExtractedDocument("Hello", "en"),
            ) as extract_mock:
                first = run_extraction(project_root=project_root)
                second = run_extraction(project_root=project_root)
                forced = run_extraction(project_root=project_root, force_refresh=True)

            with sqlite3.connect(db_path) as connection:
                row_count = connection.execute("SELECT COUNT(*) FROM extracted_pages").fetchone()[0]
                row = connection.execute(
                    "SELECT markdown_path, text_path, metadata_path, content_sha256 FROM extracted_pages"
                ).fetchone()

            self.assertEqual(first["processed"], 1)
            self.assertEqual(second["processed"], 0)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(forced["processed"], 1)
            self.assertEqual(row_count, 1)
            self.assertEqual(extract_mock.call_count, 2)
            self.assertTrue((project_root / row[0]).is_file())
            self.assertTrue((project_root / row[1]).is_file())
            self.assertTrue((project_root / row[2]).is_file())
            self.assertEqual(row[3], hashlib.sha256(b"Hello").hexdigest())

    def test_page_limit_domain_filter_and_deterministic_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            for index, (domain, url) in enumerate(
                [
                    ("b.at", "https://b.at/2"),
                    ("a.at", "https://a.at/2"),
                    ("a.at", "https://a.at/1"),
                    ("a.at", "https://a.at/3"),
                ],
                start=1,
            ):
                html_path = f"data/raw/html/{domain}/{index}.html.gz"
                write_gzip_html(project_root, html_path, f"<html><body>{url}</body></html>")
                with sqlite3.connect(db_path) as connection:
                    insert_scraped_page(connection, url=url, domain=domain, html_path=html_path)
                    connection.commit()

            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                return_value=ExtractedDocument("content"),
            ):
                summary = run_extraction(project_root=project_root, page_limit=2, domain="a.at")

            with sqlite3.connect(db_path) as connection:
                urls = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT s.url
                        FROM extracted_pages e
                        JOIN scraped_pages s ON s.scraped_page_id = e.scraped_page_id
                        ORDER BY s.domain, s.url, s.scraped_page_id
                        """
                    )
                ]

            self.assertEqual(summary["pages_selected"], 2)
            self.assertEqual(urls, ["https://a.at/1", "https://a.at/2"])

    def test_sample_selection_progress_and_run_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            inserted: list[int] = []
            with sqlite3.connect(db_path) as connection:
                for index, sample_id in enumerate(["smoke_v1", "smoke_v1", "other_v1"], start=1):
                    domain = f"example{index}.at"
                    url = f"https://{domain}/page"
                    html_path = f"data/raw/html/{domain}/page.html.gz"
                    write_gzip_html(project_root, html_path, f"<html><body>{domain}</body></html>")
                    scraped_page_id = insert_scraped_page(
                        connection,
                        url=url,
                        domain=domain,
                        html_path=html_path,
                    )
                    insert_validation_sample_row(
                        connection,
                        sample_id=sample_id,
                        scraped_page_id=scraped_page_id,
                        domain=domain,
                        url=url,
                    )
                    inserted.append(scraped_page_id)
                connection.commit()

            output = io.StringIO()
            with patch(
                "ai_ecosystem.extraction.run_extraction.extract_html_content",
                return_value=ExtractedDocument("# Heading\n\nSample text", "en"),
            ):
                with redirect_stdout(output):
                    summary = run_extraction(
                        project_root=project_root,
                        sample_id="smoke_v1",
                        progress_every=1,
                    )

            with sqlite3.connect(db_path) as connection:
                run = connection.execute(
                    """
                    SELECT sample_id, selected_pages, processed_pages, successful_pages,
                           skipped_pages, failed_pages, finished_at
                    FROM extraction_runs
                    WHERE extraction_run_id = ?
                    """,
                    (summary["extraction_run_id"],),
                ).fetchone()
                extracted_ids = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT scraped_page_id
                        FROM extracted_pages
                        ORDER BY scraped_page_id
                        """
                    )
                ]

            self.assertEqual(summary["sample_id"], "smoke_v1")
            self.assertEqual(summary["pages_selected"], 2)
            self.assertEqual(run[0], "smoke_v1")
            self.assertEqual(run[1], 2)
            self.assertEqual(run[2], 2)
            self.assertEqual(run[3], 2)
            self.assertEqual(run[4], 0)
            self.assertEqual(run[5], 0)
            self.assertIsNotNone(run[6])
            self.assertEqual(extracted_ids, inserted[:2])
            self.assertIn("Progress: 1/2", output.getvalue())
            self.assertIn("ETA:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
