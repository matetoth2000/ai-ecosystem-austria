from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.evaluate_homepage_discovery import evaluate_homepage_discovery


class EvaluateHomepageDiscoveryTest(unittest.TestCase):
    def test_evaluate_homepage_discovery_is_read_only_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(["database:", "  path: db/test.sqlite"]),
                encoding="utf-8",
            )
            sample_csv = project_root / "data" / "interim" / "sample.csv"
            sample_csv.write_text(
                "\n".join(
                    [
                        "domain",
                        "success.example",
                        "failure.example",
                        "missing.example",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                for domain in ("success.example", "failure.example", "missing.example"):
                    connection.execute("INSERT INTO domains (domain) VALUES (?)", (domain,))
                connection.execute(
                    """
                    INSERT INTO domain_discovery_status (
                        domain,
                        homepage_status,
                        discovery_error
                    )
                    VALUES ('success.example', 'success', NULL)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO domain_discovery_status (
                        domain,
                        homepage_status,
                        discovery_error
                    )
                    VALUES ('failure.example', 'failed', 'timeout while fetching homepage')
                    """
                )
                for index in range(20):
                    connection.execute(
                        """
                        INSERT INTO candidate_pages (
                            domain,
                            url,
                            normalized_url,
                            discovery_method,
                            priority,
                            page_type_guess
                        )
                        VALUES (?, ?, ?, 'homepage', ?, ?)
                        """,
                        (
                            "success.example",
                            f"https://success.example/page-{index}",
                            f"https://success.example/page-{index}",
                            10 if index == 0 else 3,
                            "ai" if index == 0 else "news",
                        ),
                    )

            export_csv = project_root / "data" / "interim" / "report.csv"
            with patch(
                "ai_ecosystem.discovery.evaluate_homepage_discovery.find_project_root",
                return_value=project_root,
            ):
                summary = evaluate_homepage_discovery(
                    sample_csv,
                    export_csv=export_csv,
                    retained_cap=20,
                )

            self.assertEqual(summary["sample_size"], 3)
            self.assertEqual(summary["attempted_domains"], 2)
            self.assertEqual(summary["homepage_success_count"], 1)
            self.assertEqual(summary["homepage_failure_count"], 1)
            self.assertEqual(summary["success_rate"], 50.0)
            self.assertEqual(summary["total_candidate_pages_from_homepage"], 20)
            self.assertEqual(summary["candidate_stats"]["max"], 20)
            self.assertEqual(summary["domains_hitting_retained_cap"], ["success.example"])
            self.assertEqual(set(summary["domains_with_zero_candidate_urls"]), {"failure.example", "missing.example"})
            self.assertTrue(export_csv.exists())
            with export_csv.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
