from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.discovery_utils import get_sample_domains, sample_exists
from ai_ecosystem.discovery.import_sample import import_sample, list_samples


class ImportSampleTest(unittest.TestCase):
    def test_import_sample_is_rerunnable_and_prevents_duplicate_domains(self) -> None:
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
                        "sample_rank,domain",
                        "2,b.example",
                        "1,a.example",
                        "3,a.example",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)

            first = import_sample(
                db_path=db_path,
                sample_id="pilot_test",
                sample_name="Pilot Test",
                csv_path=sample_csv,
                sample_description="first",
                sampling_strategy="stratified",
                sampling_seed=123,
            )
            second = import_sample(
                db_path=db_path,
                sample_id="pilot_test",
                sample_name="Pilot Test Updated",
                csv_path=sample_csv,
                sample_description="second",
                sampling_strategy="stratified",
                sampling_seed=123,
            )

            with sqlite3.connect(db_path) as connection:
                self.assertTrue(sample_exists(connection, "pilot_test"))
                self.assertEqual(get_sample_domains(connection, "pilot_test"), ["a.example", "b.example"])
                sample_row = connection.execute(
                    """
                    SELECT sample_name, sample_description, sampling_strategy, sampling_seed, sample_size
                    FROM sample_sets
                    WHERE sample_id = 'pilot_test'
                    """
                ).fetchone()
                domain_count = connection.execute(
                    "SELECT COUNT(*) FROM sample_domains WHERE sample_id = 'pilot_test'"
                ).fetchone()[0]

            self.assertEqual(first["sample_size"], 2)
            self.assertEqual(second["sample_size"], 2)
            self.assertEqual(domain_count, 2)
            self.assertEqual(
                sample_row,
                ("Pilot Test Updated", "second", "stratified", 123, 2),
            )
            rows = list_samples(db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sample_id"], "pilot_test")
            self.assertEqual(rows[0]["sample_size"], 2)


if __name__ == "__main__":
    unittest.main()
