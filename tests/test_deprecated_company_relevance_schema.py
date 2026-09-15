from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database


def write_project_config(project_root: Path) -> None:
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    (project_root / "db").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
            ]
        ),
        encoding="utf-8",
    )


def columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()}


def indexes(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table_name});").fetchall()}


class DeprecatedCompanyRelevanceSchemaTest(unittest.TestCase):
    def make_project(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        project_root = Path(temp_dir.name)
        write_project_config(project_root)
        return temp_dir, project_root

    def test_deprecated_tables_remain_available_for_historical_databases(self) -> None:
        temp_dir, project_root = self.make_project()
        with temp_dir:
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                run_columns = columns(connection, "company_relevance_runs")
                classification_columns = columns(connection, "company_relevance_classifications")
                classification_indexes = indexes(connection, "company_relevance_classifications")

        self.assertTrue(
            {
                "id",
                "classifier_id",
                "classifier_version",
                "config_id",
                "config_version",
                "config_hash",
                "status",
                "started_at",
                "completed_at",
                "selected_count",
                "processed_count",
                "reused_count",
                "keep_count",
                "exclude_count",
                "unknown_count",
                "failed_count",
                "scoring_run_id",
                "scoring_context_json",
                "failure_json",
                "run_scope",
                "is_production_candidate",
                "run_config_json",
            }.issubset(run_columns)
        )
        self.assertTrue(
            {
                "id",
                "company_id",
                "classification_run_id",
                "label",
                "confidence",
                "primary_reason_code",
                "reason_json",
                "evidence_json",
                "classifier_id",
                "classifier_version",
                "config_id",
                "config_version",
                "config_hash",
                "scoring_run_id",
                "scoring_context_json",
                "decision_json",
                "reused_from_classification_id",
            }.issubset(classification_columns)
        )
        self.assertIn("idx_company_relevance_classifications_company", classification_indexes)
        self.assertIn("idx_company_relevance_classifications_run", classification_indexes)
        self.assertIn("idx_company_relevance_classifications_label", classification_indexes)

    def test_deprecated_table_migration_is_idempotent(self) -> None:
        temp_dir, project_root = self.make_project()
        with temp_dir:
            db_path = project_root / "db" / "test.sqlite"
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE company_relevance_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                connection.execute(
                    "CREATE TABLE company_relevance_classifications (id INTEGER PRIMARY KEY AUTOINCREMENT)"
                )

            init_database(project_root)
            init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                self.assertIn("scoring_context_json", columns(connection, "company_relevance_runs"))
                self.assertIn("decision_json", columns(connection, "company_relevance_classifications"))


if __name__ == "__main__":
    unittest.main()
