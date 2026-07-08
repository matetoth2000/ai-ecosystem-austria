from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.extraction.config import (
    evidence_markdown_path,
    evidence_metadata_path,
    evidence_package_dir,
    evidence_shard,
    evidence_text_path,
    load_extraction_config,
    markdown_output_path,
    output_stem,
    text_output_path,
)
from ai_ecosystem.extraction.prepare_extraction import prepare_extraction


def write_temp_config(project_root: Path, include_extraction: bool = True) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    lines = [
        "database:",
        "  path: db/test.sqlite",
    ]
    if include_extraction:
        lines.extend(
            [
                "extraction:",
                "  extraction_version: v_test",
                "  output_root: data/extracted",
                "  markdown_directory: markdown_pages",
                "  text_directory: text_pages",
                "  overwrite_existing: true",
            ]
        )
    (project_root / "config" / "config.yaml").write_text("\n".join(lines), encoding="utf-8")


def columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()}


def index_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table_name});").fetchall()}


class ExtractionSchemaTest(unittest.TestCase):
    def test_fresh_database_contains_extraction_tables_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                extracted_columns = columns(connection, "extracted_pages")
                feature_columns = columns(connection, "page_features")
                link_columns = columns(connection, "page_links")
                extracted_indexes = index_names(connection, "extracted_pages")
                run_columns = columns(connection, "extraction_runs")
                link_indexes = index_names(connection, "page_links")

        self.assertTrue(
            {
                "extracted_page_id",
                "scraped_page_id",
                "extraction_version",
                "extraction_status",
                "markdown_path",
                "text_path",
                "metadata_path",
                "language",
                "extracted_at",
                "content_sha256",
                "extraction_run_id",
                "created_at",
            }.issubset(extracted_columns)
        )
        self.assertTrue(
            {
                "extracted_page_id",
                "title",
                "word_count",
                "heading_count",
                "headings",
                "heading_hierarchy",
                "paragraph_count",
                "page_type_guess",
                "language",
                "markdown_path",
                "text_path",
                "feature_version",
            }.issubset(feature_columns)
        )
        self.assertTrue(
            {
                "extracted_page_id",
                "source_url",
                "target_url",
                "normalized_target_url",
                "target_domain",
                "normalized_target_domain",
                "anchor_text",
                "anchor_word_count",
                "rel_attribute",
                "internal_external",
                "is_internal",
                "link_order",
            }.issubset(link_columns)
        )
        self.assertTrue(
            {
                "extraction_run_id",
                "sample_id",
                "extraction_version",
                "started_at",
                "finished_at",
                "selected_pages",
                "processed_pages",
                "successful_pages",
                "skipped_pages",
                "failed_pages",
                "configuration_hash",
            }.issubset(run_columns)
        )
        self.assertIn("idx_extracted_pages_scraped_page_id", extracted_indexes)
        self.assertIn("idx_extracted_pages_extraction_status", extracted_indexes)
        self.assertIn("idx_extracted_pages_unique_scraped_version", extracted_indexes)
        self.assertIn("idx_page_links_extracted_page_id", link_indexes)
        self.assertIn("idx_page_links_target_domain", link_indexes)

    def test_init_database_migrates_existing_partial_extraction_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = project_root / "db" / "test.sqlite"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE scraped_pages (
                        scraped_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_page_id INTEGER,
                        discovered_page_id INTEGER,
                        url TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE extracted_pages (
                        extracted_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scraped_page_id INTEGER NOT NULL,
                        extraction_version TEXT NOT NULL,
                        extraction_status TEXT DEFAULT 'pending',
                        markdown_path TEXT,
                        text_path TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE page_features (
                        extracted_page_id INTEGER PRIMARY KEY,
                        title TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE page_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        extracted_page_id INTEGER NOT NULL
                    )
                    """
                )

            init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                extracted_columns = columns(connection, "extracted_pages")
                run_columns = columns(connection, "extraction_runs")
                feature_columns = columns(connection, "page_features")
                link_columns = columns(connection, "page_links")
                extracted_indexes = index_names(connection, "extracted_pages")

        self.assertIn("language", extracted_columns)
        self.assertIn("extracted_at", extracted_columns)
        self.assertIn("metadata_path", extracted_columns)
        self.assertIn("content_sha256", extracted_columns)
        self.assertIn("extraction_run_id", extracted_columns)
        self.assertIn("configuration_hash", run_columns)
        self.assertIn("feature_version", feature_columns)
        self.assertIn("word_count", feature_columns)
        self.assertIn("headings", feature_columns)
        self.assertIn("heading_hierarchy", feature_columns)
        self.assertIn("markdown_path", feature_columns)
        self.assertIn("text_path", feature_columns)
        self.assertIn("target_domain", link_columns)
        self.assertIn("normalized_target_url", link_columns)
        self.assertIn("normalized_target_domain", link_columns)
        self.assertIn("anchor_word_count", link_columns)
        self.assertIn("rel_attribute", link_columns)
        self.assertIn("internal_external", link_columns)
        self.assertIn("link_order", link_columns)
        self.assertIn("idx_extracted_pages_unique_scraped_version", extracted_indexes)

    def test_extracted_pages_unique_scraped_page_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "INSERT INTO scraped_pages (url, scrape_status) VALUES ('https://example.at', 'success')"
                )
                scraped_page_id = connection.execute(
                    "SELECT scraped_page_id FROM scraped_pages"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO extracted_pages (scraped_page_id, extraction_version) VALUES (?, 'v1')",
                    (scraped_page_id,),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO extracted_pages (scraped_page_id, extraction_version) VALUES (?, 'v1')",
                        (scraped_page_id,),
                    )

    def test_load_extraction_config_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            configured = load_extraction_config(project_root)

        self.assertEqual(configured.extraction_version, "v_test")
        self.assertEqual(configured.output_root.name, "extracted")
        self.assertEqual(configured.markdown_directory.name, "markdown_pages")
        self.assertEqual(configured.text_directory.name, "text_pages")
        self.assertTrue(configured.overwrite_existing)

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root, include_extraction=False)

            default_config = load_extraction_config(project_root)

        self.assertEqual(default_config.extraction_version, "v1")
        self.assertEqual(default_config.output_root.name, "extracted")
        self.assertEqual(default_config.markdown_directory.name, "markdown")
        self.assertEqual(default_config.text_directory.name, "text")
        self.assertFalse(default_config.overwrite_existing)

    def test_deterministic_output_path_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            extraction_config = load_extraction_config(project_root)

            markdown_path = markdown_output_path(extraction_config, 42)
            text_path = text_output_path(extraction_config, 42)
            package_dir = evidence_package_dir(extraction_config, 1001)
            package_markdown = evidence_markdown_path(extraction_config, 1001)
            package_text = evidence_text_path(extraction_config, 1001)
            package_metadata = evidence_metadata_path(extraction_config, 1001)

        self.assertEqual(output_stem(42, "v test"), "v_test_scraped_page_000000000042")
        self.assertEqual(markdown_path.name, "v_test_scraped_page_000000000042.md")
        self.assertEqual(text_path.name, "v_test_scraped_page_000000000042.txt")
        self.assertEqual(markdown_path.parent.name, "markdown_pages")
        self.assertEqual(text_path.parent.name, "text_pages")
        self.assertEqual(evidence_shard(1), "000000")
        self.assertEqual(evidence_shard(1000), "000000")
        self.assertEqual(evidence_shard(1001), "000001")
        self.assertEqual(package_dir.parts[-3:], ("pages", "000001", "1001"))
        self.assertEqual(package_markdown.name, "page.md")
        self.assertEqual(package_text.name, "page.txt")
        self.assertEqual(package_metadata.name, "metadata.json")
        with self.assertRaises(ValueError):
            output_stem(0, "v1")

    def test_prepare_extraction_creates_directories_without_extracting_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            db_path, markdown_dir, text_dir = prepare_extraction(project_root)

            with sqlite3.connect(db_path) as connection:
                extracted_count = connection.execute("SELECT COUNT(*) FROM extracted_pages").fetchone()[0]
                feature_count = connection.execute("SELECT COUNT(*) FROM page_features").fetchone()[0]
                link_count = connection.execute("SELECT COUNT(*) FROM page_links").fetchone()[0]

            self.assertTrue(markdown_dir.is_dir())
            self.assertTrue(text_dir.is_dir())
            self.assertEqual(extracted_count, 0)
            self.assertEqual(feature_count, 0)
            self.assertEqual(link_count, 0)

            second_db_path, second_markdown_dir, second_text_dir = prepare_extraction(project_root)
            self.assertEqual(second_db_path, db_path)
            self.assertEqual(second_markdown_dir, markdown_dir)
            self.assertEqual(second_text_dir, text_dir)


if __name__ == "__main__":
    unittest.main()
