from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.ingestion.categorize_domains import categorize_domains


class CategorizeDomainsTest(unittest.TestCase):
    def test_categorization_is_rerunnable_and_does_not_guess_primary_firm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                    ]
                ),
                encoding="utf-8",
            )

            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                for i in range(1, 22):
                    if i == 1:
                        domain = "single.example"
                    elif i <= 4:
                        domain = "shared.example"
                    elif i <= 19:
                        domain = "group.example"
                    else:
                        domain = "facebook.com"
                    connection.execute(
                        """
                        INSERT INTO firms (
                            bvd_id,
                            firm_name,
                            normalized_domain
                        )
                        VALUES (?, ?, ?)
                        """,
                        (f"ATFAKE{i:03d}", f"Fake Firm {i}", domain),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO domains (domain) VALUES (?)",
                        (domain,),
                    )

            first_summary = categorize_domains(project_root)
            second_summary = categorize_domains(project_root)

            with sqlite3.connect(db_path) as connection:
                rows = {
                    row[0]: row[1:]
                    for row in connection.execute(
                        """
                        SELECT
                            domain,
                            firm_count,
                            representation_level,
                            domain_category,
                            is_excluded,
                            exclusion_reason,
                            primary_bvd_id,
                            primary_firm_name
                        FROM domains
                        """
                    )
                }

            self.assertEqual(first_summary.total_domains, 4)
            self.assertEqual(second_summary.total_domains, 4)

            self.assertEqual(rows["single.example"][:5], (1, "single_firm", "company_or_group", 0, None))
            self.assertEqual(rows["shared.example"][:5], (3, "shared_domain", "company_or_group", 0, None))
            self.assertEqual(
                rows["group.example"][:5],
                (15, "shared_group_or_brand", "company_or_group", 0, None),
            )
            self.assertEqual(
                rows["facebook.com"][:5],
                (2, "social_or_low_value", "social_media", 1, "social_media_profile_domain"),
            )

            for row in rows.values():
                self.assertIsNone(row[5])
                self.assertIsNone(row[6])


if __name__ == "__main__":
    unittest.main()
