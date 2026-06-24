from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.validation.analyze_ecosystem_signal_patterns import analyze_ecosystem_signal_patterns


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
        VALUES (?, 'main_v1', 'main', 'gpt-5-mini', 'v2', 5, 5, 0,
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
    category: str,
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
        VALUES ('main_v1', ?, ?, ?, ?, ?, 'Description', ?, ?, 500, 200, ?)
        """,
        (scraped_page_id, discovered_page_id, domain, url, "AI platform", page_type, retained, preview),
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
            4, 'high', ?, 'yes', 'reason',
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
            category,
            ecosystem_score,
            role,
            evidence,
            reason,
        ),
    )


class EcosystemSignalPatternTest(unittest.TestCase):
    def test_pattern_discovery_exports_weighted_patterns_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            run_id = "validation_patterns"
            with sqlite3.connect(db_path) as connection:
                insert_run(connection, run_id)
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=1,
                    domain="ai.example",
                    url="https://ai.example/software/platform",
                    retained=1,
                    discovery_method="homepage",
                    discard_reason=None,
                    page_type="ai_technology",
                    category="technology_capabilities",
                    ecosystem_score=5,
                    role="ai_service_provider",
                    evidence="strong",
                    reason="Software platform uses machine learning automation.",
                    preview="software platform machine learning automation analytics",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=2,
                    domain="robot.example",
                    url="https://robot.example/software/platform",
                    retained=1,
                    discovery_method="sitemap",
                    discard_reason=None,
                    page_type="ai_technology",
                    category="technology_capabilities",
                    ecosystem_score=3,
                    role="unclear",
                    evidence="moderate",
                    reason="Software platform describes robotics automation.",
                    preview="software platform robotics automation data",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=3,
                    domain="bertrandt.com",
                    url="https://bertrandt.com/software/platform",
                    retained=0,
                    discovery_method="sitemap",
                    discard_reason="retention_cap",
                    page_type="unknown",
                    category="technology_capabilities",
                    ecosystem_score=3,
                    role="unclear",
                    evidence="moderate",
                    reason="Software platform and autonomous systems evidence.",
                    preview="software platform autonomous systems digitalisierung",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=4,
                    domain="plain.example",
                    url="https://plain.example/contact",
                    retained=0,
                    discovery_method="homepage",
                    discard_reason="retention_cap",
                    page_type="negative",
                    category="contact_location",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                    reason="Opening hours and contact.",
                    preview="contact address opening hours",
                )
                insert_page(
                    connection,
                    run_id=run_id,
                    scraped_page_id=5,
                    domain="legal.example",
                    url="https://legal.example/privacy",
                    retained=1,
                    discovery_method="homepage",
                    discard_reason=None,
                    page_type="negative",
                    category="legal_compliance",
                    ecosystem_score=1,
                    role="not_ai_related",
                    evidence="none",
                    reason="Privacy policy.",
                    preview="privacy cookie legal terms",
                )
                connection.commit()
                before_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]

            with patch(
                "ai_ecosystem.validation.analyze_ecosystem_signal_patterns.find_project_root",
                return_value=project_root,
            ):
                result = analyze_ecosystem_signal_patterns(run_id=run_id)

            self.assertEqual(result["summary"]["signal_pages"], 3)
            self.assertEqual(result["summary"]["non_signal_pages"], 2)
            self.assertEqual(result["summary"]["discarded_signal_pages"], 1)

            with Path(result["paths"]["themes"]).open("r", encoding="utf-8", newline="") as file:
                theme_rows = list(csv.DictReader(file))
            software = next(row for row in theme_rows if row["pattern"] == "software")
            self.assertEqual(software["signal_count"], "3")
            self.assertEqual(software["non_signal_count"], "0")
            self.assertGreater(float(software["ecosystem_score_weighted_frequency"]), 10)
            self.assertIn("ai.example", software["representative_domains"])
            self.assertIn("https://ai.example/software/platform", software["representative_urls"])

            with Path(result["paths"]["false_negative_comparison"]).open("r", encoding="utf-8", newline="") as file:
                false_rows = list(csv.DictReader(file))
            missed_software = [
                row for row in false_rows if row["comparison_type"] == "theme" and row["pattern"] == "software"
            ][0]
            self.assertEqual(missed_software["false_negative_count"], "1")
            self.assertIn("retention_cap", missed_software["common_discard_reasons"])

            with Path(result["paths"]["retention_cap_comparison"]).open("r", encoding="utf-8", newline="") as file:
                cap_rows = list(csv.DictReader(file))
            self.assertEqual(cap_rows[0]["domain"], "bertrandt.com")
            self.assertEqual(cap_rows[0]["discarded_signal_count"], "1")

            payload = json.loads(Path(result["paths"]["json"]).read_text(encoding="utf-8"))
            self.assertIn("what_creates_ecosystem_signal", payload["executive_summary"])
            self.assertIn("ecosystem_signal_most_likely_to_be_lost", payload["executive_summary"])
            self.assertIn("Milestone 3A.5i.1", (project_root / "docs" / "project_status.md").read_text())

            with sqlite3.connect(db_path) as connection:
                after_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]
            self.assertEqual(before_count, after_count)

    def test_missing_run_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            init_database(project_root)
            with patch(
                "ai_ecosystem.validation.analyze_ecosystem_signal_patterns.find_project_root",
                return_value=project_root,
            ):
                with self.assertRaisesRegex(ValueError, "No retention validation run found"):
                    analyze_ecosystem_signal_patterns(run_id="missing", update_project_status=False)


if __name__ == "__main__":
    unittest.main()
