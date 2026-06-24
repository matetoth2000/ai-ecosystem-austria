from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.retention_quality_audit import export_retention_quality_audit


class RetentionQualityAuditTest(unittest.TestCase):
    def test_export_retention_quality_audit_writes_requested_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(["database:", "  path: db/test.sqlite"]),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO sample_sets (
                        sample_id, sample_name, sample_size, created_at
                    )
                    VALUES ('pilot_test', 'Pilot Test', 2, CURRENT_TIMESTAMP)
                    """
                )
                connection.executemany(
                    "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_test', ?, ?)",
                    [("one.example", 1), ("two.example", 2)],
                )
                audit_rows = [
                    ("one.example", "https://one.example/about", "homepage", "about", 4, "retained", None),
                    ("one.example", "https://one.example/services", "sitemap", "products", 6, "retained", None),
                    ("one.example", "https://one.example/page/2", "sitemap", "unknown", 0, "discarded", "retention_cap"),
                    ("one.example", "https://one.example/tag/news", "sitemap", "news", 3, "discarded", "retention_cap"),
                    ("two.example", "https://two.example/login", "homepage", None, None, "discarded", "hard_negative"),
                ]
                for domain, url, method, page_type, priority, decision, discard_reason in audit_rows:
                    connection.execute(
                        """
                        INSERT INTO url_retention_audit (
                            run_id,
                            sample_id,
                            domain,
                            url,
                            normalized_url,
                            discovery_method,
                            page_type_guess,
                            priority,
                            decision,
                            discard_reason
                        )
                        VALUES ('run_quality', 'pilot_test', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (domain, url, url, method, page_type, priority, decision, discard_reason),
                    )
                for domain, url, method, page_type, priority in [
                    ("one.example", "https://one.example/about", "homepage", "about", 4),
                    ("one.example", "https://one.example/services", "sitemap", "products", 6),
                ]:
                    connection.execute(
                        """
                        INSERT INTO candidate_pages (
                            domain,
                            url,
                            normalized_url,
                            discovery_method,
                            page_type_guess,
                            priority,
                            discovery_run_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'run_quality')
                        """,
                        (domain, url, url, method, page_type, priority),
                    )

            with patch(
                "ai_ecosystem.discovery.retention_quality_audit.find_project_root",
                return_value=project_root,
            ):
                summary, paths = export_retention_quality_audit("pilot_test")

            self.assertEqual(summary["run_id"], "run_quality")
            self.assertEqual(summary["retained_urls"], 2)
            self.assertEqual(summary["discarded_urls"], 3)
            self.assertEqual(summary["candidate_pages_count"], 2)
            self.assertEqual(summary["top_cap_pressure_domains"][0], ("one.example", 2))

            for path in paths.values():
                self.assertTrue(path.exists())

            with paths["domain_stats"].open("r", encoding="utf-8", newline="") as file:
                domain_rows = list(csv.DictReader(file))
            one = next(row for row in domain_rows if row["domain"] == "one.example")
            self.assertEqual(one["discovered_url_count"], "4")
            self.assertEqual(one["retained_url_count"], "2")
            self.assertEqual(one["retention_cap_discarded"], "2")

            with paths["quality_estimate"].open("r", encoding="utf-8", newline="") as file:
                quality_rows = list(csv.DictReader(file))
            labels = {(row["decision"], row["quality_estimate"]) for row in quality_rows}
            self.assertIn(("retained", "clearly_useful"), labels)
            self.assertIn(("discarded", "likely_low_value"), labels)


if __name__ == "__main__":
    unittest.main()
