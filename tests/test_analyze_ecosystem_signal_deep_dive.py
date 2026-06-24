from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.validation.analyze_ecosystem_signal_deep_dive import analyze_ecosystem_signal_deep_dive


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
            run_id, sample_id, sample_type, model_name, prompt_version,
            pages_selected, pages_completed, pages_failed, started_at, completed_at, status
        )
        VALUES (?, 'main_v1', 'main', 'gpt-5-mini', 'v2', 4, 4, 0,
                '2026-06-10T10:00:00+00:00', '2026-06-10T10:05:00+00:00', 'completed')
        """,
        (run_id,),
    )


def insert_page(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    scraped_page_id: int,
    domain: str,
    url: str,
    retained: int,
    discovery_method: str,
    discard_reason: str | None,
    page_type: str,
    ecosystem_score: int,
    role: str,
    evidence: str,
    reason: str,
    preview: str,
) -> None:
    connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
    discovered_page_id = connection.execute(
        """
        INSERT INTO discovered_pages (
            domain, url, normalized_url, discovery_method, page_type_guess,
            priority, retained_at_discovery, discard_reason, discovery_run_id
        )
        VALUES (?, ?, ?, ?, ?, 5, ?, ?, 'discovery_run')
        """,
        (domain, url, url, discovery_method, page_type, retained, discard_reason),
    ).lastrowid
    candidate_page_id = None
    if retained:
        candidate_page_id = connection.execute(
            """
            INSERT INTO candidate_pages (
                domain, url, normalized_url, discovery_method, page_type_guess, priority
            )
            VALUES (?, ?, ?, ?, ?, 5)
            """,
            (domain, url, url, discovery_method, page_type),
        ).lastrowid
    connection.execute(
        """
        INSERT INTO scraped_pages (
            scraped_page_id, discovered_page_id, candidate_page_id, url, domain,
            scrape_status, html_path, retained_at_discovery, acquisition_run_id
        )
        VALUES (?, ?, ?, ?, ?, 'success', 'data/raw/html/test.html.gz', ?, 'acq_run')
        """,
        (scraped_page_id, discovered_page_id, candidate_page_id, url, domain, retained),
    )
    connection.execute(
        """
        INSERT INTO retention_validation_sample (
            sample_id, sample_version, sample_type, scraped_page_id, discovered_page_id,
            candidate_page_id, domain, url, retained_at_discovery, page_type_guess,
            priority, discovery_method, selection_reason
        )
        VALUES ('main_v1', 'v1', 'main', ?, ?, ?, ?, ?, ?, ?, 5, ?, 'test')
        """,
        (
            scraped_page_id,
            discovered_page_id,
            candidate_page_id,
            domain,
            url,
            retained,
            page_type,
            discovery_method,
        ),
    )
    connection.execute(
        """
        INSERT INTO retention_validation_inputs (
            sample_id, scraped_page_id, discovered_page_id, domain, url, title,
            meta_description, page_type_guess, retained_at_discovery,
            visible_text_length, preview_char_count, visible_text_preview
        )
        VALUES ('main_v1', ?, ?, ?, ?, 'Title', 'Description', ?, ?, 500, 200, ?)
        """,
        (scraped_page_id, discovered_page_id, domain, url, page_type, retained, preview),
    )
    connection.execute(
        """
        INSERT INTO retention_validation_results (
            sample_id, validation_run_id, scraped_page_id, candidate_page_id,
            domain, url, retained_at_discovery, page_type_guess, prompt_version,
            model_name, firm_relevance_score, firm_relevance_label,
            page_category_llm, keep_for_firm_profile, reason_short,
            evidence_snippet, language, ecosystem_relevance_score,
            ecosystem_relevance_label, ai_ecosystem_role, ai_evidence_strength,
            ai_relevance_reason, run_status
        )
        VALUES (
            'main_v1', ?, ?, ?, ?, ?, ?, ?, 'v2', 'gpt-5-mini',
            4, 'high', 'technology_capabilities', 'yes', 'reason',
            'evidence', 'de', ?, 'medium', ?, ?, ?, 'success'
        )
        """,
        (
            run_id,
            scraped_page_id,
            candidate_page_id,
            domain,
            url,
            retained,
            page_type,
            ecosystem_score,
            role,
            evidence,
            reason,
        ),
    )


class EcosystemSignalDeepDiveTest(unittest.TestCase):
    def test_deep_dive_exports_signal_reports_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            run_id = "validation_deep"
            with sqlite3.connect(db_path) as connection:
                insert_run(connection, run_id)
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=1,
                    domain="ai.example",
                    url="https://ai.example/ai-platform",
                    retained=1,
                    discovery_method="homepage",
                    discard_reason=None,
                    page_type="ai_technology",
                    ecosystem_score=5,
                    role="ai_service_provider",
                    evidence="strong",
                    reason="Page describes an AI platform.",
                    preview="AI platform machine learning automation",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=2,
                    domain="bertrandt.com",
                    url="https://bertrandt.com/autonomes-fahren",
                    retained=0,
                    discovery_method="sitemap",
                    discard_reason="retention_cap",
                    page_type="unknown",
                    ecosystem_score=3,
                    role="unclear",
                    evidence="moderate",
                    reason="Autonomous driving and software evidence.",
                    preview="Autonomes Fahren Software Digitalisierung",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=3,
                    domain="plain.example",
                    url="https://plain.example/contact",
                    retained=0,
                    discovery_method="homepage",
                    discard_reason="retention_cap",
                    page_type="negative",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                    reason="No AI evidence.",
                    preview="contact address opening hours",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=4,
                    domain="research.example",
                    url="https://research.example/robotik",
                    retained=1,
                    discovery_method="sitemap",
                    discard_reason=None,
                    page_type="unknown",
                    ecosystem_score=3,
                    role="unclear",
                    evidence="moderate",
                    reason="Robotics research context.",
                    preview="Robotik Forschung Daten",
                )
                connection.commit()
                before_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]

            with patch(
                "ai_ecosystem.validation.analyze_ecosystem_signal_deep_dive.find_project_root",
                return_value=project_root,
            ):
                result = analyze_ecosystem_signal_deep_dive(run_id=run_id)

            self.assertEqual(result["summary"]["ecosystem_signal_pages"], 3)
            self.assertEqual(result["summary"]["false_negative_count"], 1)
            self.assertEqual(result["false_negative_root_causes"][0]["domain"], "bertrandt.com")
            self.assertIn("retention_cap", result["false_negative_root_causes"][0]["inferred_root_cause"])

            with Path(result["paths"]["ecosystem_signal_pages"]).open("r", encoding="utf-8", newline="") as file:
                signal_rows = list(csv.DictReader(file))
            self.assertEqual(signal_rows[0]["domain"], "ai.example")
            self.assertIn("ai", signal_rows[0]["interesting_terms"])

            with Path(result["paths"]["discovery_method_analysis"]).open("r", encoding="utf-8", newline="") as file:
                method_rows = {row["discovery_method"]: row for row in csv.DictReader(file)}
            self.assertEqual(method_rows["sitemap"]["ecosystem_signal_count"], "2")

            payload = json.loads(Path(result["paths"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["top_signal_domains"]["ai.example"], 1)
            self.assertIn("Milestone 3A.5i", (project_root / "docs" / "project_status.md").read_text())

            with sqlite3.connect(db_path) as connection:
                after_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]
            self.assertEqual(before_count, after_count)

    def test_missing_run_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            init_database(project_root)
            with patch(
                "ai_ecosystem.validation.analyze_ecosystem_signal_deep_dive.find_project_root",
                return_value=project_root,
            ):
                with self.assertRaisesRegex(ValueError, "No retention validation run found"):
                    analyze_ecosystem_signal_deep_dive(run_id="missing", update_project_status=False)


if __name__ == "__main__":
    unittest.main()
