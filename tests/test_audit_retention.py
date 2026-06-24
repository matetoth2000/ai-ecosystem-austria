from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.audit_retention import export_retention_audit


class AuditRetentionTest(unittest.TestCase):
    def test_export_retention_audit_summarizes_latest_run(self) -> None:
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
                rows = [
                    ("old_run", "pilot_test", "a.example", "https://a.example", "retained", None),
                    ("latest_run", "pilot_test", "a.example", "https://a.example", "retained", None),
                    ("latest_run", "pilot_test", "a.example", "https://external.example", "discarded", "external_domain"),
                    ("latest_run", "pilot_test", "a.example", "https://a.example/logo.png", "discarded", "asset_static"),
                ]
                for run_id, sample_id, domain, url, decision, discard_reason in rows:
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
                        VALUES (?, ?, ?, ?, ?, 'homepage', 'unknown', 0, ?, ?)
                        """,
                        (run_id, sample_id, domain, url, url, decision, discard_reason),
                    )

            with patch(
                "ai_ecosystem.discovery.audit_retention.find_project_root",
                return_value=project_root,
            ):
                export_path, summary, run_id = export_retention_audit("pilot_test")

            self.assertEqual(run_id, "latest_run")
            self.assertEqual(summary["retained_urls"], 1)
            self.assertEqual(summary["discarded_urls"], 2)
            self.assertEqual(summary["retention_rate"], 33.33)
            self.assertEqual(summary["discard_reason_distribution"]["external_domain"], 1)
            self.assertEqual(summary["discard_reason_distribution"]["asset_static"], 1)
            self.assertTrue(export_path.exists())

            with export_path.open("r", encoding="utf-8", newline="") as file:
                exported_rows = list(csv.DictReader(file))
            self.assertEqual(len(exported_rows), 3)
            self.assertEqual(exported_rows[0]["run_id"], "latest_run")


if __name__ == "__main__":
    unittest.main()
