import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.benchmark_discovery import parse_worker_counts, run_benchmark


class BenchmarkDiscoveryTests(unittest.TestCase):
    def test_parse_worker_counts(self) -> None:
        self.assertEqual(parse_worker_counts(None), [1, 5, 10, 20, 50])
        self.assertEqual(parse_worker_counts("1, 3,5"), [1, 3, 5])
        with self.assertRaises(ValueError):
            parse_worker_counts("0")

    def test_run_benchmark_writes_json_csv_and_quality_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "logs").mkdir()
            (project_root / "db").mkdir()
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "discovery:",
                        "  max_workers: 1",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('one.example')")
                connection.execute("INSERT INTO domains (domain) VALUES ('two.example')")
                connection.execute(
                    """
                    INSERT INTO sample_sets (
                        sample_id, sample_name, sample_size, created_at
                    )
                    VALUES ('pilot_v0_2', 'Pilot', 2, '2026-05-31T00:00:00+00:00')
                    """
                )
                connection.execute(
                    "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_v0_2', 'one.example', 1)"
                )
                connection.execute(
                    "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_v0_2', 'two.example', 2)"
                )

            def fake_run_homepage_discovery(sample_id, max_workers, **kwargs):
                run_id = f"run_{max_workers}"
                retained = 3 if max_workers == 1 else 4
                summary = {
                    "run_id": run_id,
                    "duration_seconds": 10 if max_workers == 1 else 4,
                    "domains_per_second": 0.2 if max_workers == 1 else 0.5,
                    "domains_attempted": 2,
                    "domains_succeeded": 2,
                    "domains_failed": 0,
                    "robots_txt_successes": 1,
                    "robots_txt_failures": 1,
                    "sitemap_files_parsed": 2,
                    "sitemap_urls_discovered": 5,
                    "sitemap_successes": 2,
                    "sitemap_failures": 0,
                    "candidate_urls_discovered": 6 if max_workers == 1 else 7,
                    "candidate_urls_retained": retained,
                    "hard_negative_urls_filtered": 1,
                    "asset_static_urls_filtered": 2,
                }
                summary_path = project_root / "logs" / f"discovery_{run_id}_summary.json"
                summary_path.write_text(json.dumps(summary), encoding="utf-8")
                with sqlite3.connect(db_path) as connection:
                    for index in range(retained):
                        domain = "one.example" if index % 2 == 0 else "two.example"
                        page_type = "homepage" if index == 0 else "products"
                        normalized_url = f"https://{domain}/{run_id}/{index}"
                        connection.execute(
                            """
                            INSERT INTO candidate_pages (
                                domain,
                                url,
                                normalized_url,
                                discovery_method,
                                page_type_guess,
                                discovery_run_id
                            )
                            VALUES (?, ?, ?, 'homepage', ?, ?)
                            """,
                            (domain, normalized_url, normalized_url, page_type, run_id),
                        )
                return run_id

            with (
                patch("ai_ecosystem.discovery.benchmark_discovery.find_project_root", return_value=project_root),
                patch(
                    "ai_ecosystem.discovery.benchmark_discovery.run_homepage_discovery",
                    side_effect=fake_run_homepage_discovery,
                ),
            ):
                json_path, csv_path, records = run_benchmark("pilot_v0_2", [1, 5])

            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["workers"], 1)
            self.assertEqual(records[0]["retained_diff_vs_1_worker"], 0)
            self.assertEqual(records[1]["retained_diff_vs_1_worker"], 1)
            self.assertEqual(records[1]["discovered_diff_vs_1_worker"], 1)
            self.assertEqual(records[1]["coverage_diff_vs_1_worker"], 0)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sample_id"], "pilot_v0_2")
            self.assertEqual(len(payload["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
