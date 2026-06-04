import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.homepage_discovery import FetchedHomepage
from ai_ecosystem.discovery.investigate_discovery_misses import investigate_discovery_misses


class InvestigateDiscoveryMissesTests(unittest.TestCase):
    def test_investigation_writes_artifacts_for_zero_retained_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "logs").mkdir()
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "discovery:",
                        "  max_retained_candidate_urls_per_domain: 20",
                        "  request_timeout_seconds: 1",
                        "  user_agent: TestBot/0.1",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('zero.example')")
                connection.execute("INSERT INTO domains (domain) VALUES ('covered.example')")
                connection.execute(
                    """
                    INSERT INTO sample_sets (sample_id, sample_name, sample_size, created_at)
                    VALUES ('pilot_test', 'Pilot Test', 2, '2026-06-04T00:00:00+00:00')
                    """
                )
                connection.execute(
                    "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_test', 'zero.example', 1)"
                )
                connection.execute(
                    "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_test', 'covered.example', 2)"
                )
                connection.execute(
                    """
                    INSERT INTO domain_discovery_status (
                        domain, homepage_status, robots_status, sitemap_status, discovery_error
                    )
                    VALUES ('zero.example', 'success', 'failed', 'failed', NULL)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO candidate_pages (domain, url, normalized_url, discovery_method)
                    VALUES ('covered.example', 'https://covered.example/about', 'https://covered.example/about', 'homepage')
                    """
                )

            def fake_fetch_homepage(domain, timeout_seconds, user_agent, logger, retries=1):
                return FetchedHomepage(
                    requested_url="https://zero.example",
                    final_url="https://zero.example",
                    status_code=200,
                    html="<html><body><a href='https://external.example/about'>External</a></body></html>",
                    page_title="Zero",
                )

            with (
                patch(
                    "ai_ecosystem.discovery.investigate_discovery_misses.find_project_root",
                    return_value=project_root,
                ),
                patch(
                    "ai_ecosystem.discovery.investigate_discovery_misses.fetch_homepage",
                    side_effect=fake_fetch_homepage,
                ),
                patch(
                    "ai_ecosystem.discovery.investigate_discovery_misses.discover_robots_txt_for_hosts",
                    return_value=(None, [], "robots failed", 0, 1),
                ),
                patch(
                    "ai_ecosystem.discovery.investigate_discovery_misses.discover_sitemap_candidates",
                    return_value=[],
                ),
            ):
                csv_path, json_path, summary = investigate_discovery_misses("pilot_test")

            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            self.assertEqual(summary["total_zero_retained_domains"], 1)
            self.assertEqual(summary["count_potential_extraction_gap"], 1)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sample_id"], "pilot_test")
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("zero.example", csv_text)
            self.assertNotIn("covered.example", csv_text)


if __name__ == "__main__":
    unittest.main()
