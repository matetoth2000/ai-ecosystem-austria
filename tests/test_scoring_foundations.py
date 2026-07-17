from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.scoring.config import (
    REQUIRED_DIMENSIONS,
    load_knowledge_bases,
    load_scoring_profile,
    validate_required_dimensions,
    validate_scoring_foundation,
)


def write_temp_config(project_root: Path, kb_directory: str = "config/knowledge_bases") -> None:
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    (project_root / "db").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "scoring:",
                "  scoring_profile_id: test_profile",
                "  scoring_profile_version: v_test",
                f"  knowledge_base_directory: {kb_directory}",
                "  required_dimensions:",
                "    - firm_profile",
                "    - technology",
                "    - innovation",
                "    - ecosystem",
                "  dimension_weights:",
                "    firm_profile: 1.0",
                "    technology: 1.5",
                "    innovation: 1.2",
                "    ecosystem: 1.1",
                "  field_weights:",
                "    title: 2.0",
                "    headings: 1.5",
                "    text: 1.0",
                "  fusion_weights:",
                "    query: 1.0",
                "    keyword: 1.0",
                "    boost: 0.5",
                "    negative: -1.0",
            ]
        ),
        encoding="utf-8",
    )


def write_kb(project_root: Path, dimension: str, version: str = "v1") -> None:
    directory = project_root / "config" / "knowledge_bases"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{dimension}_{version}.yaml").write_text(
        "\n".join(
            [
                f"version: {version}",
                "",
                f"{dimension}:",
                "  node:",
                "    query_families:",
                "      - \"We describe concrete capability evidence.\"",
                "    keywords:",
                "      en:",
                "        - capability",
                "      de:",
                "        - fähigkeit",
                "    boost_terms:",
                "      en:",
                "        - implementation",
                "      de:",
                "        - umsetzung",
                "    negative_terms:",
                "      en:",
                "        - generic",
                "      de:",
                "        - allgemein",
            ]
        ),
        encoding="utf-8",
    )


def columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()}


def indexes(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table_name});").fetchall()}


class ScoringFoundationsTest(unittest.TestCase):
    def test_fresh_database_contains_scoring_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                kb_columns = columns(connection, "knowledge_bases")
                profile_columns = columns(connection, "scoring_profiles")
                run_columns = columns(connection, "scoring_runs")
                score_columns = columns(connection, "page_scores")
                score_indexes = indexes(connection, "page_scores")

        self.assertTrue(
            {
                "knowledge_base_id",
                "knowledge_base_version",
                "dimension",
                "source_path",
                "config_json",
                "created_at",
            }.issubset(kb_columns)
        )
        self.assertTrue(
            {
                "scoring_profile_id",
                "scoring_profile_version",
                "dimension_weights_json",
                "field_weights_json",
                "fusion_weights_json",
                "config_json",
            }.issubset(profile_columns)
        )
        self.assertTrue(
            {
                "scoring_run_id",
                "scoring_profile_id",
                "scoring_profile_version",
                "sample_id",
                "extraction_version",
                "pages_selected",
                "pages_processed",
                "pages_scored",
                "pages_skipped",
                "pages_failed",
                "runtime_seconds",
                "pages_per_second",
                "configuration_hash",
            }.issubset(run_columns)
        )
        self.assertTrue(
            {
                "extracted_page_id",
                "scraped_page_id",
                "domain",
                "url",
                "scoring_run_id",
                "dimension",
                "extraction_version",
                "query_score",
                "keyword_score",
                "boost_score",
                "negative_score",
                "final_score",
                "top_queries_json",
                "top_keywords_json",
                "top_boost_terms_json",
                "top_negative_terms_json",
                "knowledge_base_id",
                "knowledge_base_version",
                "scoring_profile_id",
                "scoring_profile_version",
                "created_at",
            }.issubset(score_columns)
        )
        self.assertIn("idx_page_scores_unique_run_page_dimension", score_indexes)
        self.assertIn("idx_page_scores_run_dimension", score_indexes)
        self.assertIn("idx_page_scores_score_identity", score_indexes)

    def test_init_database_migrates_partial_scoring_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = project_root / "db" / "test.sqlite"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE knowledge_bases (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                connection.execute("CREATE TABLE scoring_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                connection.execute("CREATE TABLE scoring_runs (scoring_run_id TEXT PRIMARY KEY)")
                connection.execute(
                    """
                    CREATE TABLE page_scores (
                        page_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        extracted_page_id INTEGER NOT NULL,
                        scoring_run_id TEXT NOT NULL,
                        dimension TEXT NOT NULL
                    )
                    """
                )

            init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                self.assertIn("knowledge_base_version", columns(connection, "knowledge_bases"))
                self.assertIn("fusion_weights_json", columns(connection, "scoring_profiles"))
                self.assertIn("configuration_hash", columns(connection, "scoring_runs"))
                self.assertIn("pages_skipped", columns(connection, "scoring_runs"))
                self.assertIn("runtime_seconds", columns(connection, "scoring_runs"))
                self.assertIn("extraction_version", columns(connection, "page_scores"))
                self.assertIn("top_negative_terms_json", columns(connection, "page_scores"))
                self.assertIn("knowledge_base_id", columns(connection, "page_scores"))

    def test_scoring_profile_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)

            profile = load_scoring_profile(project_root)

        self.assertEqual(profile.scoring_profile_id, "test_profile")
        self.assertEqual(profile.scoring_profile_version, "v_test")
        self.assertEqual(profile.required_dimensions, REQUIRED_DIMENSIONS)
        self.assertEqual(profile.dimension_weights["technology"], 1.5)
        self.assertEqual(profile.field_weights["title"], 2.0)
        self.assertEqual(profile.fusion_weights["negative"], -1.0)

    def test_knowledge_bases_load_and_required_dimensions_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            for dimension in REQUIRED_DIMENSIONS:
                write_kb(project_root, dimension)

            knowledge_bases = load_knowledge_bases(project_root)
            validate_required_dimensions(knowledge_bases)
            profile, validated = validate_scoring_foundation(project_root)

        self.assertEqual(set(knowledge_bases), set(REQUIRED_DIMENSIONS))
        self.assertEqual(knowledge_bases["technology"].version, "v1")
        self.assertEqual(knowledge_bases["firm_profile"].knowledge_base_id, "firm_profile")
        self.assertEqual(profile.scoring_profile_id, "test_profile")
        self.assertEqual(set(validated), set(REQUIRED_DIMENSIONS))

    def test_missing_required_dimension_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            write_kb(project_root, "firm_profile")

            knowledge_bases = load_knowledge_bases(project_root)

        with self.assertRaisesRegex(ValueError, "Missing required"):
            validate_required_dimensions(knowledge_bases)


if __name__ == "__main__":
    unittest.main()
