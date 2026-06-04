from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.homepage_discovery import read_domains_from_csv
from ai_ecosystem.discovery.pilot_sample import create_pilot_sample, create_stratified_pilot_sample


class PilotSampleTest(unittest.TestCase):
    def test_create_pilot_sample_and_read_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "discovery:",
                        "  pilot_mix:",
                        "    single_firm: 2",
                        "    shared_domain: 1",
                        "    shared_group_or_brand: 1",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                fixtures = [
                    ("a.example", "single_firm", 1, "ATFAKE001", "Firm A", "6201", "Wien"),
                    ("b.example", "single_firm", 1, "ATFAKE002", "Firm B", "6202", "Graz"),
                    ("c.example", "shared_domain", 3, "ATFAKE003", "Firm C", "6203", "Linz"),
                    ("d.example", "shared_group_or_brand", 12, "ATFAKE004", "Firm D", "6204", "Salzburg"),
                    ("facebook.com", "social_or_low_value", 20, "ATFAKE005", "Firm E", "6205", "Wien"),
                ]
                for domain, level, firm_count, bvd_id, firm_name, nace_code, city in fixtures:
                    connection.execute(
                        """
                        INSERT INTO domains (
                            domain,
                            representation_level,
                            domain_category,
                            firm_count,
                            is_excluded
                        )
                        VALUES (?, ?, 'company_or_group', ?, ?)
                        """,
                        (domain, level, firm_count, 1 if domain == "facebook.com" else 0),
                    )
                    connection.execute(
                        """
                        INSERT INTO firms (
                            bvd_id,
                            firm_name,
                            nace_code,
                            city,
                            normalized_domain
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (bvd_id, firm_name, nace_code, city, domain),
                    )

            output_path = project_root / "data" / "interim" / "sample.csv"
            with patch("ai_ecosystem.discovery.pilot_sample.find_project_root", return_value=project_root):
                create_pilot_sample(output_path)

            with output_path.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertEqual([row["domain"] for row in rows], ["a.example", "b.example", "c.example", "d.example"])
            self.assertEqual(read_domains_from_csv(output_path), ["a.example", "b.example", "c.example", "d.example"])
            self.assertEqual(read_domains_from_csv(output_path, limit=2), ["a.example", "b.example"])
            self.assertIn("nace_code", rows[0])
            self.assertIn("firm_name", rows[0])

    def test_create_stratified_pilot_sample_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "discovery:",
                        "  pilot_mix:",
                        "    single_firm: 4",
                        "    shared_domain: 2",
                        "    shared_group_or_brand: 1",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)

            with sqlite3.connect(db_path) as connection:
                for i in range(1, 13):
                    if i <= 6:
                        level = "single_firm"
                        firm_count = 1
                    elif i <= 10:
                        level = "shared_domain"
                        firm_count = 2
                    else:
                        level = "shared_group_or_brand"
                        firm_count = 12
                    domain = f"domain-{i}.example"
                    nace_code = ("J" if i % 3 == 0 else "M" if i % 3 == 1 else "G") + "620"
                    connection.execute(
                        """
                        INSERT INTO domains (
                            domain,
                            representation_level,
                            domain_category,
                            firm_count,
                            is_excluded
                        )
                        VALUES (?, ?, 'company_or_group', ?, 0)
                        """,
                        (domain, level, firm_count),
                    )
                    connection.execute(
                        """
                        INSERT INTO firms (
                            bvd_id,
                            firm_name,
                            nace_code,
                            city,
                            normalized_domain
                        )
                        VALUES (?, ?, ?, 'Wien', ?)
                        """,
                        (f"ATFAKE{i:03d}", f"Firm {i}", nace_code, domain),
                    )

            first_output = project_root / "data" / "interim" / "sample_1.csv"
            second_output = project_root / "data" / "interim" / "sample_2.csv"
            with patch("ai_ecosystem.discovery.pilot_sample.find_project_root", return_value=project_root):
                create_stratified_pilot_sample(first_output, seed=123)
                create_stratified_pilot_sample(second_output, seed=123)

            first_text = first_output.read_text(encoding="utf-8")
            second_text = second_output.read_text(encoding="utf-8")
            self.assertEqual(first_text, second_text)

            with first_output.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 7)
            self.assertEqual({row["sampling_seed"] for row in rows}, {"123"})
            self.assertIn("sector_section", rows[0])
            self.assertIn("nace_codes", rows[0])
            self.assertEqual(read_domains_from_csv(first_output), [row["domain"] for row in rows])


if __name__ == "__main__":
    unittest.main()
