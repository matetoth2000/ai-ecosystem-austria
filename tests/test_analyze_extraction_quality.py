from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.extraction.analyze_extraction_quality import analyze_extraction_quality


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "extraction:",
                "  extraction_version: v1",
                "  output_root: data/extracted",
            ]
        ),
        encoding="utf-8",
    )


def write_text_file(project_root: Path, relative_path: str, content: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_gzip_html(project_root: Path, relative_path: str, content: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as file:
        file.write(content.encode("utf-8"))


def insert_sample_page(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    scraped_page_id: int,
    domain: str,
    url: str,
) -> None:
    connection.execute(
        """
        INSERT INTO discovered_pages (
            discovered_page_id,
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            retained_at_discovery,
            discovery_run_id
        )
        VALUES (?, ?, ?, ?, 'homepage', 'about', 80, 1, 'test_discovery')
        """,
        (scraped_page_id, domain, url, url),
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
        (sample_id, scraped_page_id, scraped_page_id, domain, url),
    )


def insert_extracted_page(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    scraped_page_id: int,
    domain: str,
    url: str,
    run_id: str,
    extraction_version: str,
    words: int,
    headings: int,
    language: str,
    content_sha256: str,
    internal_links: int = 0,
    external_links: int = 0,
) -> None:
    html_path = f"data/raw/html/{domain}/{scraped_page_id}.html.gz"
    markdown_path = f"data/extracted/pages/000000/{scraped_page_id}/page.md"
    text_path = f"data/extracted/pages/000000/{scraped_page_id}/page.txt"
    metadata_path = f"data/extracted/pages/000000/{scraped_page_id}/metadata.json"
    text = " ".join(f"word{i}" for i in range(words))
    markdown = "\n".join(f"{'#' * min(index + 1, 6)} Heading {index}" for index in range(headings))
    markdown = f"{markdown}\n\n{text}" if markdown else text
    write_gzip_html(project_root, html_path, "<html>" + ("x" * 5000) + "</html>")
    write_text_file(project_root, markdown_path, markdown)
    write_text_file(project_root, text_path, text)
    write_text_file(
        project_root,
        metadata_path,
        json.dumps({"scraped_page_id": scraped_page_id, "content_sha256": content_sha256}),
    )
    connection.execute(
        """
        INSERT INTO scraped_pages (
            scraped_page_id,
            discovered_page_id,
            url,
            domain,
            html_path,
            scrape_status,
            title,
            language
        )
        VALUES (?, ?, ?, ?, ?, 'success', ?, ?)
        """,
        (scraped_page_id, scraped_page_id, url, domain, html_path, f"Title {scraped_page_id}", language),
    )
    cursor = connection.execute(
        """
        INSERT INTO extracted_pages (
            scraped_page_id,
            extraction_version,
            extraction_status,
            markdown_path,
            text_path,
            metadata_path,
            language,
            extracted_at,
            content_sha256,
            extraction_run_id
        )
        VALUES (?, ?, 'success', ?, ?, ?, ?, '2026-07-08T10:00:00+00:00', ?, ?)
        """,
        (
            scraped_page_id,
            extraction_version,
            markdown_path,
            text_path,
            metadata_path,
            language,
            content_sha256,
            run_id,
        ),
    )
    extracted_page_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO page_features (
            extracted_page_id,
            title,
            word_count,
            heading_count,
            headings,
            heading_hierarchy,
            paragraph_count,
            page_type_guess,
            language,
            markdown_path,
            text_path,
            feature_version
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, 'about', ?, ?, ?, ?)
        """,
        (
            extracted_page_id,
            f"Title {scraped_page_id}",
            words,
            headings,
            json.dumps([f"Heading {index}" for index in range(headings)]),
            json.dumps([{"level": 1, "text": f"Heading {index}"} for index in range(headings)]),
            language,
            markdown_path,
            text_path,
            extraction_version,
        ),
    )
    order = 1
    for index in range(internal_links):
        connection.execute(
            """
            INSERT INTO page_links (
                extracted_page_id,
                source_url,
                target_url,
                normalized_target_url,
                target_domain,
                normalized_target_domain,
                anchor_text,
                anchor_word_count,
                internal_external,
                is_internal,
                link_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'internal', 1, ?)
            """,
            (
                extracted_page_id,
                url,
                f"https://{domain}/internal-{index}",
                f"https://{domain}/internal-{index}",
                domain,
                domain,
                "internal link",
                order,
            ),
        )
        order += 1
    for index in range(external_links):
        connection.execute(
            """
            INSERT INTO page_links (
                extracted_page_id,
                source_url,
                target_url,
                normalized_target_url,
                target_domain,
                normalized_target_domain,
                anchor_text,
                anchor_word_count,
                internal_external,
                is_internal,
                link_order
            )
            VALUES (?, ?, ?, ?, 'external.at', 'external.at', 'external link', 2, 'external', 0, ?)
            """,
            (
                extracted_page_id,
                url,
                f"https://external.at/{index}",
                f"https://external.at/{index}",
                order,
            ),
        )
        order += 1


class AnalyzeExtractionQualityTest(unittest.TestCase):
    def build_fixture(self, project_root: Path) -> Path:
        write_temp_config(project_root)
        db_path = init_database(project_root)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                INSERT INTO extraction_runs (
                    extraction_run_id,
                    sample_id,
                    extraction_version,
                    started_at,
                    finished_at,
                    selected_pages,
                    processed_pages,
                    successful_pages,
                    skipped_pages,
                    failed_pages,
                    configuration_hash
                )
                VALUES ('run_v1', 'smoke_v1', 'v1', '2026-07-08T10:00:00+00:00', '2026-07-08T10:01:00+00:00', 2, 2, 2, 0, 0, 'abc')
                """
            )
            connection.execute(
                """
                INSERT INTO extraction_runs (
                    extraction_run_id,
                    sample_id,
                    extraction_version,
                    started_at,
                    finished_at,
                    selected_pages,
                    processed_pages,
                    successful_pages,
                    skipped_pages,
                    failed_pages,
                    configuration_hash
                )
                VALUES ('run_v2', 'smoke_v1', 'v2', '2026-07-08T11:00:00+00:00', '2026-07-08T11:01:00+00:00', 1, 1, 1, 0, 0, 'def')
                """
            )
            insert_sample_page(connection, sample_id="smoke_v1", scraped_page_id=1, domain="a.at", url="https://a.at/one")
            insert_sample_page(connection, sample_id="smoke_v1", scraped_page_id=2, domain="b.at", url="https://b.at/two")
            insert_sample_page(connection, sample_id="main_v1", scraped_page_id=3, domain="c.at", url="https://c.at/three")
            insert_extracted_page(
                connection,
                project_root,
                scraped_page_id=1,
                domain="a.at",
                url="https://a.at/one",
                run_id="run_v1",
                extraction_version="v1",
                words=120,
                headings=2,
                language="de",
                content_sha256="samehash",
                internal_links=2,
                external_links=1,
            )
            insert_extracted_page(
                connection,
                project_root,
                scraped_page_id=2,
                domain="b.at",
                url="https://b.at/two",
                run_id="run_v1",
                extraction_version="v1",
                words=20,
                headings=0,
                language="en",
                content_sha256="samehash",
                internal_links=0,
                external_links=2,
            )
            insert_extracted_page(
                connection,
                project_root,
                scraped_page_id=3,
                domain="c.at",
                url="https://c.at/three",
                run_id="run_v2",
                extraction_version="v2",
                words=60,
                headings=1,
                language="de",
                content_sha256="otherhash",
                internal_links=1,
                external_links=0,
            )
            connection.commit()
        return db_path

    def test_report_generation_statistics_warnings_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self.build_fixture(project_root)
            report = analyze_extraction_quality(
                sample_id="smoke_v1",
                extraction_version="v1",
                run_id="run_v1",
                output_directory=project_root / "reports",
                project_root=project_root,
            )

            output_dir = Path(report["generated_report_paths"]["json"]).parent
            self.assertTrue((output_dir / "extraction_summary.csv").is_file())
            self.assertTrue((output_dir / "extraction_word_statistics.csv").is_file())
            self.assertTrue((output_dir / "extraction_duplicate_content.csv").is_file())
            self.assertEqual(report["summary"]["pages_analyzed"], 2)
            self.assertEqual(report["word_statistics"]["median"], 70.0)
            self.assertEqual(report["heading_statistics"]["pages_with_zero_headings"], 1)
            self.assertEqual(report["link_statistics"]["internal_links"], 2)
            self.assertEqual(report["link_statistics"]["external_links"], 3)
            self.assertEqual(report["warnings"]["pages_without_headings"], 1)
            self.assertEqual(report["warnings"]["duplicate_content_groups"], 1)

            with (output_dir / "extraction_duplicate_content.csv").open(encoding="utf-8") as file:
                duplicate_rows = list(csv.DictReader(file))
            self.assertEqual(duplicate_rows[0]["content_sha256"], "samehash")
            self.assertEqual(duplicate_rows[0]["number_of_pages"], "2")

            with (output_dir / "extraction_short_pages.csv").open(encoding="utf-8") as file:
                short_rows = list(csv.DictReader(file))
            self.assertEqual(short_rows[0]["domain"], "b.at")
            self.assertEqual(short_rows[0]["category"], "under_50_words")

    def test_sample_version_run_and_domain_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self.build_fixture(project_root)

            smoke_report = analyze_extraction_quality(
                sample_id="smoke_v1",
                extraction_version="v1",
                domain="a.at",
                output_directory=project_root / "reports",
                project_root=project_root,
            )
            self.assertEqual(smoke_report["summary"]["pages_analyzed"], 1)
            self.assertEqual(smoke_report["summary"]["domain_filter"], "a.at")

            main_report = analyze_extraction_quality(
                sample_id="main_v1",
                extraction_version="v2",
                output_directory=project_root / "reports",
                project_root=project_root,
            )
            self.assertEqual(main_report["summary"]["pages_analyzed"], 1)
            self.assertEqual(main_report["summary"]["sample"], "main_v1")
            self.assertEqual(main_report["summary"]["extraction_version"], "v2")

    def test_empty_sample_and_read_only_database_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            db_path = self.build_fixture(project_root)
            with sqlite3.connect(db_path) as connection:
                before = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ["extracted_pages", "page_features", "page_links", "extraction_runs"]
                }

            report = analyze_extraction_quality(
                sample_id="missing_sample",
                output_directory=project_root / "reports",
                project_root=project_root,
            )

            with sqlite3.connect(db_path) as connection:
                after = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ["extracted_pages", "page_features", "page_links", "extraction_runs"]
                }

            self.assertEqual(before, after)
            self.assertEqual(report["summary"]["pages_analyzed"], 0)
            self.assertEqual(report["word_statistics"]["count"], 0)
            self.assertTrue(Path(report["generated_report_paths"]["json"]).is_file())


if __name__ == "__main__":
    unittest.main()
