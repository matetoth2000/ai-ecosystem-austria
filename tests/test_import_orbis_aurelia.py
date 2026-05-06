from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.ingestion.import_orbis_aurelia import import_orbis_aurelia


class OrbisAureliaImportTest(unittest.TestCase):
    def test_import_normalizes_domains_and_is_idempotent(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        fixture = repo_root / "tests" / "fixtures" / "orbis_sample.txt"

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "raw" / "orbis_aurelia").mkdir(parents=True)
            input_path = project_root / "data" / "raw" / "orbis_aurelia" / "orbis_sample.txt"
            shutil.copyfile(fixture, input_path)

            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "ingestion:",
                        "  orbis_aurelia_input_path: data/raw/orbis_aurelia/orbis_sample.txt",
                    ]
                ),
                encoding="utf-8",
            )

            db_path = init_database(project_root)
            first_summary = import_orbis_aurelia(project_root=project_root)
            second_summary = import_orbis_aurelia(project_root=project_root)

            with sqlite3.connect(db_path) as connection:
                firm_count = connection.execute("SELECT COUNT(*) FROM firms").fetchone()[0]
                domain_count = connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
                domains = {row[0] for row in connection.execute("SELECT domain FROM domains")}
                normalized = dict(
                    connection.execute("SELECT bvd_id, normalized_domain FROM firms ORDER BY bvd_id")
                )
                raw_url = connection.execute(
                    "SELECT raw_url FROM firms WHERE bvd_id = 'ATFAKE001'"
                ).fetchone()[0]

            self.assertEqual(first_summary.rows_read, 5)
            self.assertEqual(first_summary.firms_inserted, 5)
            self.assertEqual(first_summary.firms_updated, 0)
            self.assertEqual(first_summary.missing_urls, 1)
            self.assertEqual(first_summary.invalid_urls, 1)
            self.assertEqual(first_summary.unique_valid_domains_found, 2)
            self.assertEqual(first_summary.new_domains_inserted, 2)

            self.assertEqual(second_summary.firms_inserted, 0)
            self.assertEqual(second_summary.firms_updated, 5)
            self.assertEqual(second_summary.new_domains_inserted, 0)

            self.assertEqual(firm_count, 5)
            self.assertEqual(domain_count, 2)
            self.assertEqual(domains, {"example-robotics.at", "shared-domain.at"})
            self.assertEqual(normalized["ATFAKE001"], "example-robotics.at")
            self.assertEqual(normalized["ATFAKE002"], "shared-domain.at")
            self.assertIsNone(normalized["ATFAKE003"])
            self.assertIsNone(normalized["ATFAKE004"])
            self.assertEqual(raw_url, "example-robotics.at")


if __name__ == "__main__":
    unittest.main()
