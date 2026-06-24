from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.validation.analyze_retention_validation import analyze_retention_validation


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
        INSERT INTO retention_validation_runs (
            run_id,
            sample_id,
            sample_type,
            model_name,
            prompt_version,
            pages_selected,
            pages_completed,
            pages_failed,
            input_tokens,
            output_tokens,
            total_tokens,
            started_at,
            completed_at,
            status
        )
        VALUES (
            ?, 'main_v1', 'main', 'gpt-5-mini', 'v2', 6, 6, 0,
            1000, 500, 1500, '2026-06-10T10:00:00+00:00',
            '2026-06-10T10:10:00+00:00', 'completed'
        )
        """,
        (run_id,),
    )


def insert_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    scraped_page_id: int,
    domain: str,
    url: str,
    retained: int,
    page_type: str,
    category: str,
    firm_score: int,
    keep: str,
    ecosystem_score: int,
    role: str,
    evidence: str,
    reason: str = "reason",
) -> None:
    connection.execute(
        """
        INSERT INTO retention_validation_results (
            sample_id,
            validation_run_id,
            scraped_page_id,
            candidate_page_id,
            domain,
            url,
            retained_at_discovery,
            page_type_guess,
            prompt_version,
            model_name,
            input_chars,
            output_json,
            firm_relevance_score,
            firm_relevance_label,
            page_category_llm,
            keep_for_firm_profile,
            reason_short,
            evidence_snippet,
            language,
            ecosystem_relevance_score,
            ecosystem_relevance_label,
            ai_ecosystem_role,
            ai_evidence_strength,
            ai_relevance_reason,
            run_status,
            input_tokens,
            output_tokens,
            total_tokens
        )
        VALUES (
            'main_v1', ?, ?, NULL, ?, ?, ?, ?, 'v2', 'gpt-5-mini',
            100, '{}', ?, 'high', ?, ?, ?, 'snippet', 'de',
            ?, 'medium', ?, ?, ?, 'success', 10, 5, 15
        )
        """,
        (
            run_id,
            scraped_page_id,
            domain,
            url,
            retained,
            page_type,
            firm_score,
            category,
            keep,
            reason,
            ecosystem_score,
            role,
            evidence,
            reason,
        ),
    )


class AnalyzeRetentionValidationTest(unittest.TestCase):
    def test_analysis_exports_reports_and_updates_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            run_id = "validation_test"
            with sqlite3.connect(db_path) as connection:
                insert_run(connection, run_id)
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=1,
                    domain="ai.example",
                    url="https://ai.example/platform",
                    retained=1,
                    page_type="ai_technology",
                    category="technology_capabilities",
                    firm_score=5,
                    keep="yes",
                    ecosystem_score=5,
                    role="ai_service_provider",
                    evidence="strong",
                    reason="AI platform evidence",
                )
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=2,
                    domain="about.example",
                    url="https://about.example/about",
                    retained=1,
                    page_type="about",
                    category="company_description",
                    firm_score=4,
                    keep="yes",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                )
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=3,
                    domain="bad.example",
                    url="https://bad.example/privacy",
                    retained=1,
                    page_type="negative",
                    category="legal_compliance",
                    firm_score=2,
                    keep="no",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                )
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=4,
                    domain="miss.example",
                    url="https://miss.example/ml",
                    retained=0,
                    page_type="unknown",
                    category="technology_capabilities",
                    firm_score=4,
                    keep="yes",
                    ecosystem_score=4,
                    role="ai_developer_vendor",
                    evidence="moderate",
                    reason="Machine-learning product evidence",
                )
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=5,
                    domain="discard.example",
                    url="https://discard.example/contact",
                    retained=0,
                    page_type="negative",
                    category="contact_location",
                    firm_score=3,
                    keep="yes",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                )
                insert_result(
                    connection,
                    run_id=run_id,
                    scraped_page_id=6,
                    domain="discard.example",
                    url="https://discard.example/services",
                    retained=0,
                    page_type="products",
                    category="products_services",
                    firm_score=5,
                    keep="yes",
                    ecosystem_score=2,
                    role="unclear",
                    evidence="weak",
                )
                connection.commit()
                before_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]

            with patch("ai_ecosystem.validation.analyze_retention_validation.find_project_root", return_value=project_root):
                summary = analyze_retention_validation(run_id=run_id)

            self.assertEqual(len(summary["rows"]), 6)
            self.assertEqual(summary["effects"]["firm_average_difference"], -0.333)
            self.assertEqual(summary["effects"]["ecosystem_average_difference"], 0.0)
            self.assertEqual(len(summary["false_negatives"]), 1)
            self.assertEqual(summary["false_negatives"][0]["domain"], "miss.example")
            self.assertEqual(len(summary["false_positives"]), 1)
            self.assertEqual(summary["false_positives"][0]["domain"], "bad.example")

            page_type_path = Path(summary["paths"]["page_type"])
            with page_type_path.open("r", encoding="utf-8", newline="") as file:
                page_type_rows = {row["page_type_guess"]: row for row in csv.DictReader(file)}
            self.assertIn("retained_average_firm_relevance_score", page_type_rows["negative"])
            self.assertEqual(page_type_rows["negative"]["retained_count"], "1")
            self.assertEqual(page_type_rows["negative"]["discarded_count"], "1")

            ai_path = Path(summary["paths"]["ai_findings"])
            with ai_path.open("r", encoding="utf-8", newline="") as file:
                ai_rows = list(csv.DictReader(file))
            top_signal = [row for row in ai_rows if row["section"] == "top_ecosystem_signal_pages"]
            self.assertEqual(top_signal[0]["domain"], "ai.example")
            self.assertEqual(top_signal[0]["ai_evidence_strength"], "strong")

            json_payload = json.loads(Path(summary["paths"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(json_payload["false_negative_count"], 1)
            self.assertEqual(json_payload["false_positive_count"], 1)

            status_text = (project_root / "docs" / "project_status.md").read_text(encoding="utf-8")
            self.assertIn("Milestone 3A.5h", status_text)
            self.assertIn(run_id, status_text)

            with sqlite3.connect(db_path) as connection:
                after_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]
            self.assertEqual(before_count, after_count)

    def test_missing_run_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            init_database(project_root)
            with patch("ai_ecosystem.validation.analyze_retention_validation.find_project_root", return_value=project_root):
                with self.assertRaisesRegex(ValueError, "No retention validation run found"):
                    analyze_retention_validation(run_id="missing", update_project_status=False)


if __name__ == "__main__":
    unittest.main()
