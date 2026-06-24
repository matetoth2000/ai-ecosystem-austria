from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database, load_config


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(["database:", "  path: db/test.sqlite"]),
        encoding="utf-8",
    )


def columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()}


class AcquisitionSchemaTest(unittest.TestCase):
    def test_fresh_database_contains_acquisition_tables_and_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                scraped_columns = columns(connection, "scraped_pages")
                acquisition_run_columns = columns(connection, "acquisition_runs")
                discovered_columns = columns(connection, "discovered_pages")
                validation_run_columns = columns(connection, "retention_validation_runs")
                validation_result_columns = columns(connection, "retention_validation_results")
                validation_sample_columns = columns(connection, "retention_validation_sample")
                validation_input_columns = columns(connection, "retention_validation_inputs")
                discovered_indexes = {
                    row[1] for row in connection.execute("PRAGMA index_list(discovered_pages);").fetchall()
                }
                validation_result_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(retention_validation_results);"
                    ).fetchall()
                }
                validation_sample_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(retention_validation_sample);"
                    ).fetchall()
                }
                validation_input_indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(retention_validation_inputs);"
                    ).fetchall()
                }

            self.assertIn("scraped_page_id", scraped_columns)
            self.assertIn("candidate_page_id", scraped_columns)
            self.assertIn("discovered_page_id", scraped_columns)
            self.assertIn("final_url", scraped_columns)
            self.assertIn("content_length", scraped_columns)
            self.assertIn("meta_description", scraped_columns)
            self.assertIn("canonical_url", scraped_columns)
            self.assertIn("word_count_estimate", scraped_columns)
            self.assertIn("redirect_count", scraped_columns)
            self.assertIn("retained_at_discovery", scraped_columns)
            self.assertIn("acquisition_run_id", scraped_columns)
            self.assertIn("acquisition_run_id", acquisition_run_columns)
            self.assertIn("fetch_scope", acquisition_run_columns)
            self.assertIn("pages_attempted", acquisition_run_columns)
            self.assertIn("success_rate", acquisition_run_columns)
            self.assertIn("discovered_page_id", discovered_columns)
            self.assertIn("retained_at_discovery", discovered_columns)
            self.assertIn("discard_reason", discovered_columns)
            self.assertIn("idx_discovered_pages_run_domain_url_method", discovered_indexes)
            self.assertIn("idx_discovered_pages_domain_url", discovered_indexes)
            self.assertIn("idx_discovered_pages_run_retained", discovered_indexes)
            self.assertIn("idx_discovered_pages_domain_run", discovered_indexes)
            self.assertIn("run_id", validation_run_columns)
            self.assertIn("model_name", validation_run_columns)
            self.assertIn("prompt_version", validation_run_columns)
            self.assertIn("pages_selected", validation_run_columns)
            self.assertIn("status", validation_run_columns)
            self.assertIn("scraped_page_id", validation_result_columns)
            self.assertIn("candidate_page_id", validation_result_columns)
            self.assertIn("output_json", validation_result_columns)
            self.assertIn("firm_relevance_score", validation_result_columns)
            self.assertIn("ecosystem_relevance_score", validation_result_columns)
            self.assertIn("ecosystem_relevance_label", validation_result_columns)
            self.assertIn("ai_ecosystem_role", validation_result_columns)
            self.assertIn("ai_evidence_strength", validation_result_columns)
            self.assertIn("ai_relevance_reason", validation_result_columns)
            self.assertIn("keep_for_firm_profile", validation_result_columns)
            self.assertIn("idx_retention_validation_results_scraped_page_id", validation_result_indexes)
            self.assertIn("idx_retention_validation_results_status_created", validation_result_indexes)
            self.assertIn("sample_id", validation_sample_columns)
            self.assertIn("sample_version", validation_sample_columns)
            self.assertIn("sample_type", validation_sample_columns)
            self.assertIn("scraped_page_id", validation_sample_columns)
            self.assertIn("discovered_page_id", validation_sample_columns)
            self.assertIn("candidate_page_id", validation_sample_columns)
            self.assertIn("selection_reason", validation_sample_columns)
            self.assertIn("idx_retention_validation_sample_retained", validation_sample_indexes)
            self.assertIn("idx_retention_validation_sample_domain", validation_sample_indexes)
            self.assertIn("idx_retention_validation_sample_page_type", validation_sample_indexes)
            self.assertIn("idx_retention_validation_sample_discovered_page_id", validation_sample_indexes)
            self.assertIn("sample_id", validation_input_columns)
            self.assertIn("scraped_page_id", validation_input_columns)
            self.assertIn("discovered_page_id", validation_input_columns)
            self.assertIn("visible_text_length", validation_input_columns)
            self.assertIn("preview_char_count", validation_input_columns)
            self.assertIn("visible_text_preview", validation_input_columns)
            self.assertIn("idx_retention_validation_inputs_sample_id", validation_input_indexes)
            self.assertIn("idx_retention_validation_inputs_discovered_page_id", validation_input_indexes)

    def test_init_database_migrates_existing_scraped_pages_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = project_root / "db" / "test.sqlite"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE scraped_pages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_page_id INTEGER,
                        url TEXT NOT NULL,
                        domain TEXT,
                        http_status INTEGER,
                        content_type TEXT,
                        fetched_at TEXT,
                        html_path TEXT,
                        scrape_status TEXT DEFAULT 'pending',
                        error_message TEXT
                    )
                    """
                )

            init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                scraped_columns = columns(connection, "scraped_pages")
                acquisition_run_columns = columns(connection, "acquisition_runs")

            self.assertNotIn("id", scraped_columns)
            self.assertIn("scraped_page_id", scraped_columns)
            self.assertIn("final_url", scraped_columns)
            self.assertIn("content_length", scraped_columns)
            self.assertIn("meta_description", scraped_columns)
            self.assertIn("canonical_url", scraped_columns)
            self.assertIn("word_count_estimate", scraped_columns)
            self.assertIn("redirect_count", scraped_columns)
            self.assertIn("retained_at_discovery", scraped_columns)
            self.assertIn("acquisition_run_id", scraped_columns)
            self.assertIn("acquisition_run_id", acquisition_run_columns)
            self.assertIn("success_rate", acquisition_run_columns)

    def test_init_database_backfills_discovered_pages_from_candidate_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('example.at')")
                connection.execute(
                    """
                    INSERT INTO candidate_pages (
                        domain,
                        url,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        url_depth,
                        source_domain,
                        discovered_from_url,
                        last_discovered_at,
                        discovery_run_id
                    )
                    VALUES (
                        'example.at',
                        'https://example.at/about',
                        'https://example.at/about',
                        'homepage',
                        'about',
                        4,
                        1,
                        'example.at',
                        'https://example.at',
                        '2026-06-05T10:00:00+00:00',
                        'run_backfill'
                    )
                    """
                )
                connection.execute("DELETE FROM discovered_pages")
                connection.commit()

            init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT
                        domain,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        retained_at_discovery,
                        discard_reason,
                        discovery_run_id
                    FROM discovered_pages
                    """
                ).fetchone()

            self.assertEqual(
                row,
                (
                    "example.at",
                    "https://example.at/about",
                    "homepage",
                    "about",
                    4,
                    1,
                    None,
                    "run_backfill",
                ),
            )

    def test_project_config_contains_acquisition_defaults(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config = load_config(project_root)
        acquisition = config["acquisition"]
        validation = config["validation"]

        self.assertEqual(acquisition["fetch_scope"], "discovered")
        self.assertIn(acquisition["fetch_scope"], {"discovered", "retained"})
        self.assertEqual(acquisition["timeout_seconds"], 15)
        self.assertEqual(acquisition["retry_count"], 2)
        self.assertEqual(acquisition["max_workers"], 20)
        self.assertTrue(acquisition["skip_existing"])
        self.assertEqual(acquisition["progress_log_every_n_pages"], 25)
        self.assertFalse(validation["enabled"])
        self.assertEqual(validation["model"], "gpt-5-mini")
        self.assertEqual(validation["temperature"], 0)
        self.assertEqual(validation["max_input_chars"], 4000)
        self.assertEqual(validation["smoke_sample_size_per_group"], 5)
        self.assertEqual(validation["main_sample_size_per_group"], 300)
        self.assertEqual(validation["max_pages_per_domain"], 3)
        self.assertEqual(validation["prompt_version"], "v2")
        self.assertEqual(validation["random_seed"], 20260608)
        self.assertEqual(validation["retry_count"], 2)
        self.assertEqual(validation["request_timeout_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
