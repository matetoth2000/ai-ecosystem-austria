from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.evaluate_discovery import evaluate_discovery
from ai_ecosystem.discovery.import_sample import import_sample


class EvaluateDiscoveryTest(unittest.TestCase):
    def test_evaluate_discovery_summarizes_methods_categories_and_exports_read_only(self) -> None:
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
                        "homepage-only.example",
                        "sitemap-only.example",
                        "both.example",
                        "neither.example",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                for domain in (
                    "homepage-only.example",
                    "sitemap-only.example",
                    "both.example",
                    "neither.example",
                ):
                    connection.execute("INSERT INTO domains (domain) VALUES (?)", (domain,))
                    connection.execute(
                        """
                        INSERT INTO domain_discovery_status (
                            domain,
                            homepage_status,
                            robots_status,
                            sitemap_status,
                            discovery_error
                        )
                        VALUES (?, 'success', 'success', 'success', NULL)
                        """,
                        (domain,),
                    )
                connection.execute(
                    """
                    INSERT INTO discovery_runs (
                        run_id,
                        started_at,
                        completed_at,
                        domains_attempted,
                        domains_succeeded,
                        domains_failed,
                        candidate_urls_discovered,
                        candidate_urls_retained,
                        notes
                    )
                    VALUES (
                        'run_eval',
                        '2026-05-28T10:00:00+00:00',
                        '2026-05-28T10:01:30+00:00',
                        4,
                        3,
                        1,
                        12,
                        6,
                        'test run'
                    )
                    """
                )
                rows = [
                    ("homepage-only.example", "https://homepage-only.example", "homepage", "homepage", 8),
                    ("homepage-only.example", "https://homepage-only.example/about", "homepage", "about", 4),
                    ("sitemap-only.example", "https://sitemap-only.example/product", "sitemap", "products", 6),
                    ("both.example", "https://both.example", "homepage", "homepage", 8),
                    ("both.example", "https://both.example/news", "sitemap", "news", 3),
                    ("both.example", "https://both.example/contact", "sitemap", "negative", -100),
                ]
                for domain, url, method, page_type, priority in rows:
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
                        VALUES (?, ?, ?, ?, ?, ?, 'run_eval')
                        """,
                        (domain, url, url, method, page_type, priority),
                    )
                before_count = connection.execute("SELECT COUNT(*) FROM candidate_pages").fetchone()[0]

            export_dir = project_root / "data" / "interim"
            with patch(
                "ai_ecosystem.discovery.evaluate_discovery.find_project_root",
                return_value=project_root,
            ):
                summary = evaluate_discovery(sample_csv, export_dir=export_dir)

            with sqlite3.connect(db_path) as connection:
                after_count = connection.execute("SELECT COUNT(*) FROM candidate_pages").fetchone()[0]

            self.assertEqual(before_count, after_count)
            self.assertEqual(summary["sample_size"], 4)
            self.assertEqual(summary["attempted_domains"], 4)
            self.assertEqual(summary["domains_with_any_retained_candidate_url"], 3)
            self.assertEqual(summary["domains_with_zero_retained_candidate_urls"], ["neither.example"])
            self.assertEqual(summary["total_retained_candidate_urls"], 6)
            self.assertEqual(summary["homepage_only_domains"], ["homepage-only.example"])
            self.assertEqual(summary["sitemap_only_domains"], ["sitemap-only.example"])
            self.assertEqual(summary["both_homepage_and_sitemap_domains"], ["both.example"])
            self.assertEqual(summary["neither_homepage_nor_sitemap_domains"], ["neither.example"])
            self.assertEqual(summary["total_homepage_candidates"], 3)
            self.assertEqual(summary["total_sitemap_candidates"], 3)
            self.assertEqual(summary["sitemap_only_retained_candidates"], 1)
            self.assertEqual(summary["page_type_counts"]["homepage"], 2)
            self.assertEqual(summary["page_type_counts"]["products"], 1)
            self.assertEqual(summary["page_type_by_method"][("sitemap", "negative")], 1)
            self.assertEqual(summary["latest_run"]["run_id"], "run_eval")
            self.assertEqual(summary["latest_run"]["duration_seconds"], 90.0)

            expected_files = [
                export_dir / "discovery_eval_sample_domain_summary.csv",
                export_dir / "discovery_eval_sample_method_summary.csv",
                export_dir / "discovery_eval_sample_category_summary.csv",
            ]
            for path in expected_files:
                self.assertTrue(path.exists())

            with expected_files[0].open("r", encoding="utf-8", newline="") as file:
                domain_rows = list(csv.DictReader(file))
            self.assertEqual(len(domain_rows), 4)
            both_row = next(row for row in domain_rows if row["domain"] == "both.example")
            self.assertEqual(both_row["homepage_candidates"], "1")
            self.assertEqual(both_row["sitemap_candidates"], "2")

            import_sample(
                db_path=db_path,
                sample_id="sample_test",
                sample_name="Sample Test",
                csv_path=sample_csv,
            )
            with patch(
                "ai_ecosystem.discovery.evaluate_discovery.find_project_root",
                return_value=project_root,
            ):
                sample_id_summary = evaluate_discovery(sample_id="sample_test")

            self.assertEqual(sample_id_summary["sample_id"], "sample_test")
            self.assertIsNone(sample_id_summary["input_csv"])
            self.assertEqual(sample_id_summary["total_retained_candidate_urls"], 6)
            self.assertEqual(sample_id_summary["sitemap_only_domains"], ["sitemap-only.example"])


if __name__ == "__main__":
    unittest.main()
