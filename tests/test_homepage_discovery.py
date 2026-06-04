from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.homepage_discovery import (
    FetchedHomepage,
    FetchedText,
    allowed_discovery_hosts,
    delete_discovery_candidate_pages_for_domains,
    extract_internal_candidate_urls,
    discover_sitemap_candidates,
    run_homepage_discovery,
    upsert_candidate_pages,
    update_domain_status,
)
from ai_ecosystem.discovery.normalization import normalize_url
from ai_ecosystem.discovery.robots_discovery import extract_sitemap_urls_from_robots
from ai_ecosystem.discovery.sitemap_discovery import parse_sitemap_xml


FAKE_HTML = """
<!doctype html>
<html>
  <head><title>Example GmbH</title></head>
  <body>
    <a href="/about">About</a>
    <a href="https://example.at/about/">Duplicate about</a>
    <a href="/products?utm_source=test&keep=1">Products</a>
    <a href="https://other.example/news">External</a>
    <a href="mailto:info@example.at">Mail</a>
    <a href="tel:+431234">Phone</a>
    <a href="javascript:void(0)">JS</a>
    <a href="#top">Fragment</a>
    <a href="team#people">Team fragment removed</a>
  </body>
</html>
"""

ASSET_HTML = """
<!doctype html>
<html>
  <head><title>Assets</title></head>
  <body>
    <a href="/uploads/banner.jpg">Banner</a>
    <a href="/cdn-cgi/l/email-protection">Cloudflare email</a>
    <a href="/assets/logo.svg">Logo</a>
    <a href="/styles/main.css">Styles</a>
    <a href="/brochure.pdf">Brochure</a>
    <a href="/about-us">About</a>
  </body>
</html>
"""

CANONICAL_HTML = """
<!doctype html>
<html>
  <head><title>Canonical</title></head>
  <body>
    <a href="http://www.example.at/">HTTP www root</a>
    <a href="https://example.at/">HTTPS root</a>
    <a href="https://www.example.at?s=">Empty search root</a>
    <a href="https://www.example.at/?utm_source=test">Tracking root</a>
    <a href="https://example.at?page_id=123">CMS page</a>
    <a href="https://careers.example.at/jobs">Real subdomain</a>
  </body>
</html>
"""

NEGATIVE_HTML = """
<!doctype html>
<html>
  <head><title>Negative</title></head>
  <body>
    <a href="/">Home</a>
    <a href="/cookie-policy">Cookie</a>
    <a href="/privacy-policy">Privacy</a>
    <a href="/datenschutz">Datenschutz</a>
    <a href="/login">Login</a>
    <a href="/checkout">Checkout</a>
    <a href="/agb">AGB</a>
    <a href="/impressum">Impressum</a>
    <a href="/kontakt">Kontakt</a>
    <a href="/contact">Contact</a>
    <a href="/legal">Legal</a>
    <a href="/about-us">About</a>
  </body>
</html>
"""


class HomepageDiscoveryTest(unittest.TestCase):
    def test_normalize_url_canonicalizes_homepage_and_query_variants(self) -> None:
        cases = {
            "https://example.com": "https://example.com",
            "https://example.com/": "https://example.com",
            "http://www.example.com/": "https://example.com",
            "https://example.com?s=": "https://example.com",
            "https://example.com?utm_source=test": "https://example.com",
            "https://example.com?utm_source=": "https://example.com",
            "https://example.com?fbclid=abc": "https://example.com",
            "https://example.com?gclid=": "https://example.com",
            "https://example.com?page_id=123": "https://example.com?page_id=123",
            "https://example.com?id=123": "https://example.com?id=123",
            "https://example.com/page/2": "https://example.com/page/2",
            "https://example.com?lang=de": "https://example.com?lang=de",
            "https://careers.example.com/": "https://careers.example.com",
        }

        for raw_url, expected in cases.items():
            with self.subTest(raw_url=raw_url):
                self.assertEqual(normalize_url(raw_url), expected)

    def test_extract_internal_candidate_urls_deduplicates_and_normalizes(self) -> None:
        candidates = extract_internal_candidate_urls(
            FAKE_HTML,
            "https://example.at/",
            "example.at",
        )
        normalized_urls = {candidate.normalized_url for candidate in candidates}

        self.assertEqual(
            normalized_urls,
            {
                "https://example.at/about",
                "https://example.at/products?keep=1",
                "https://example.at/team",
            },
        )
        self.assertEqual(len(candidates), 3)
        self.assertTrue(all(candidate.source_domain == "example.at" for candidate in candidates))
        self.assertTrue(
            all(candidate.discovered_from_url == "https://example.at" for candidate in candidates)
        )

    def test_extract_internal_candidate_urls_filters_assets_but_keeps_pdfs(self) -> None:
        stats: dict[str, int] = {}
        candidates = extract_internal_candidate_urls(
            ASSET_HTML,
            "https://example.at/",
            "example.at",
            stats=stats,
        )
        normalized_urls = {candidate.normalized_url for candidate in candidates}

        self.assertEqual(
            normalized_urls,
            {
                "https://example.at/about-us",
                "https://example.at/brochure.pdf",
            },
        )
        self.assertEqual(stats["asset_static_urls_filtered"], 4)

    def test_extract_internal_candidate_urls_collapses_homepage_variants(self) -> None:
        stats: dict[str, int] = {}
        candidates = extract_internal_candidate_urls(
            CANONICAL_HTML,
            "https://www.example.at/",
            "example.at",
            stats=stats,
        )
        normalized_urls = {candidate.normalized_url for candidate in candidates}

        self.assertEqual(
            normalized_urls,
            {
                "https://example.at",
                "https://example.at?page_id=123",
                "https://careers.example.at/jobs",
            },
        )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(stats["duplicate_urls_collapsed"], 3)
        self.assertEqual(stats["homepage_variants_collapsed"], 3)
        self.assertEqual(stats["query_urls_normalized"], 2)

    def test_extract_internal_candidate_urls_filters_hard_negatives_and_keeps_soft_negatives(self) -> None:
        stats: dict[str, int] = {}
        candidates = extract_internal_candidate_urls(
            NEGATIVE_HTML,
            "https://example.at/",
            "example.at",
            stats=stats,
        )
        by_url = {candidate.normalized_url: candidate.page_type_guess for candidate in candidates}

        self.assertEqual(
            by_url,
            {
                "https://example.at": "homepage",
                "https://example.at/about-us": "about",
                "https://example.at/contact": "negative",
                "https://example.at/impressum": "negative",
                "https://example.at/kontakt": "negative",
                "https://example.at/legal": "negative",
            },
        )
        self.assertEqual(stats["hard_negative_urls_filtered"], 6)

    def test_extract_internal_candidate_urls_accepts_canonical_alias_host(self) -> None:
        html = """
        <html><body>
          <a href="/about">Alias about</a>
          <a href="https://sample.example/about">Original about</a>
          <a href="https://third-party.example/about">External</a>
        </body></html>
        """
        stats: dict[str, int] = {}
        allowed_hosts = allowed_discovery_hosts("sample.example", "https://www.brand.example/")
        candidates = extract_internal_candidate_urls(
            html,
            "https://www.brand.example/",
            "sample.example",
            stats=stats,
            allowed_hosts=allowed_hosts,
        )

        self.assertEqual(
            {candidate.normalized_url for candidate in candidates},
            {
                "https://brand.example/about",
                "https://sample.example/about",
            },
        )
        self.assertEqual(stats["alias_candidate_urls_accepted"], 1)

    def test_candidate_page_upsert_and_domain_status_are_rerunnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(["database:", "  path: db/test.sqlite"]),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            candidates = extract_internal_candidate_urls(
                FAKE_HTML,
                "https://example.at/",
                "example.at",
            )

            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('example.at')")
                first_count = upsert_candidate_pages(
                    connection,
                    "example.at",
                    "Example GmbH",
                    "run_1",
                    "2026-05-26T10:00:00+00:00",
                    candidates,
                )
                second_count = upsert_candidate_pages(
                    connection,
                    "example.at",
                    "Example GmbH",
                    "run_2",
                    "2026-05-26T10:05:00+00:00",
                    candidates,
                )
                update_domain_status(
                    connection,
                    "example.at",
                    "success",
                    "2026-05-26T10:05:00+00:00",
                    "run_2",
                    successful_at="2026-05-26T10:05:00+00:00",
                )

                candidate_rows = connection.execute("SELECT COUNT(*) FROM candidate_pages").fetchone()[0]
                provenance_rows = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM candidate_pages
                    WHERE discovered_from_url = 'https://example.at'
                      AND discovery_method = 'homepage'
                      AND discovery_run_id = 'run_2'
                    """
                ).fetchone()[0]
                status = connection.execute(
                    """
                    SELECT homepage_status, discovery_run_id
                    FROM domain_discovery_status
                    WHERE domain = 'example.at'
                    """
                ).fetchone()

            self.assertEqual(first_count, 3)
            self.assertEqual(second_count, 3)
            self.assertEqual(candidate_rows, 3)
            self.assertEqual(provenance_rows, 3)
            self.assertEqual(status, ("success", "run_2"))

    def test_delete_discovery_candidate_pages_for_domains_scopes_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(["database:", "  path: db/test.sqlite"]),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                for domain in ("pilot.example", "other.example"):
                    connection.execute("INSERT INTO domains (domain) VALUES (?)", (domain,))
                rows = [
                    ("pilot.example", "https://pilot.example/homepage", "homepage"),
                    ("pilot.example", "https://pilot.example/sitemap", "sitemap"),
                    ("other.example", "https://other.example/homepage", "homepage"),
                ]
                for domain, url, method in rows:
                    connection.execute(
                        """
                        INSERT INTO candidate_pages (
                            domain,
                            url,
                            normalized_url,
                            discovery_method
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (domain, url, url, method),
                    )

                deleted = delete_discovery_candidate_pages_for_domains(connection, ["pilot.example"])
                remaining = {
                    row
                    for row in connection.execute(
                        "SELECT domain, discovery_method FROM candidate_pages ORDER BY domain, discovery_method"
                    )
                }

            self.assertEqual(deleted, 2)
            self.assertEqual(
                remaining,
                {
                    ("other.example", "homepage"),
                },
            )

    def test_extract_sitemap_urls_from_robots(self) -> None:
        robots_text = """
        User-agent: *
        Disallow: /admin
        Sitemap: https://example.at/sitemap.xml
        Sitemap: /nested-sitemap.xml
        """

        self.assertEqual(
            extract_sitemap_urls_from_robots(robots_text, "https://example.at/robots.txt"),
            [
                "https://example.at/sitemap.xml",
                "https://example.at/nested-sitemap.xml",
            ],
        )

    def test_parse_sitemap_xml_urlset_index_invalid_and_empty(self) -> None:
        urlset = """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.at/ki-lösungen</loc></url>
          <url><loc>https://external.example/page</loc></url>
        </urlset>
        """
        sitemap_index = """
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.at/child.xml</loc></sitemap>
        </sitemapindex>
        """
        empty = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'

        self.assertEqual(parse_sitemap_xml(urlset).urls[0], "https://example.at/ki-lösungen")
        self.assertEqual(parse_sitemap_xml(sitemap_index).sitemap_urls, ["https://example.at/child.xml"])
        self.assertEqual(parse_sitemap_xml(empty).urls, [])
        self.assertFalse(parse_sitemap_xml("<not xml").is_valid_xml)

    def test_discover_sitemap_candidates_recurses_filters_and_deduplicates(self) -> None:
        root_xml = """
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.at/child.xml</loc></sitemap>
        </sitemapindex>
        """
        child_xml = """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://www.example.at/ki-lösungen</loc></url>
          <url><loc>http://example.at/ki-lösungen/</loc></url>
          <url><loc>https://example.at/uploads/banner.jpg</loc></url>
          <url><loc>https://example.at/datenschutz</loc></url>
          <url><loc>https://example.at/brochure.pdf</loc></url>
          <url><loc>https://external.example/about</loc></url>
        </urlset>
        """
        fetched_by_url = {
            "https://example.at/sitemap.xml": FetchedText(
                requested_url="https://example.at/sitemap.xml",
                final_url="https://example.at/sitemap.xml",
                status_code=200,
                text=root_xml,
            ),
            "https://example.at/child.xml": FetchedText(
                requested_url="https://example.at/child.xml",
                final_url="https://example.at/child.xml",
                status_code=200,
                text=child_xml,
            ),
        }
        stats: dict[str, int] = {}

        def fake_fetch_text_url(url, domain, timeout_seconds, user_agent, logger, label):
            return fetched_by_url[url]

        with patch("ai_ecosystem.discovery.homepage_discovery.fetch_text_url", side_effect=fake_fetch_text_url):
            candidates = discover_sitemap_candidates(
                "example.at",
                ["https://example.at/sitemap.xml"],
                1,
                "TestBot/0.1",
                logging.getLogger("test"),
                {"discovery": {"sitemap_url_limit": 20, "max_sitemap_urls_per_domain": 1000}},
                stats,
            )

        by_url = {candidate.normalized_url: candidate for candidate in candidates}
        self.assertEqual(set(by_url), {"https://example.at/ki-lösungen", "https://example.at/brochure.pdf"})
        self.assertEqual(by_url["https://example.at/ki-lösungen"].discovery_method, "sitemap")
        self.assertEqual(by_url["https://example.at/ki-lösungen"].page_type_guess, "ai_technology")
        self.assertEqual(stats["sitemap_files_parsed"], 2)
        self.assertEqual(stats["sitemap_urls_discovered"], 6)
        self.assertEqual(stats["asset_static_urls_filtered"], 1)
        self.assertEqual(stats["hard_negative_urls_filtered"], 1)
        self.assertEqual(stats["duplicate_urls_collapsed"], 1)

    def test_run_homepage_discovery_writes_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / "config").mkdir()
            (project_root / "db").mkdir()
            (project_root / "logs").mkdir()
            (project_root / "data" / "interim").mkdir(parents=True)
            (project_root / "config" / "config.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: db/test.sqlite",
                        "discovery:",
                        "  max_discovered_urls_per_domain: 20",
                        "  max_retained_candidate_urls_per_domain: 20",
                        "  request_timeout_seconds: 1",
                        "  user_agent: TestBot/0.1",
                    ]
                ),
                encoding="utf-8",
            )
            sample_csv = project_root / "data" / "interim" / "sample.csv"
            sample_csv.write_text(
                "\n".join(["domain", "success.example", "failure.example"]),
                encoding="utf-8",
            )
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('success.example')")
                connection.execute("INSERT INTO domains (domain) VALUES ('failure.example')")

            def fake_fetch_homepage(domain, timeout_seconds, user_agent, logger, retries=1):
                if domain == "failure.example":
                    raise RuntimeError("test failure")
                return FetchedHomepage(
                    requested_url="https://success.example",
                    final_url="https://success.example/",
                    status_code=200,
                    html=FAKE_HTML.replace("example.at", "success.example"),
                    page_title="Success Example",
                )

            with (
                patch("ai_ecosystem.discovery.homepage_discovery.find_project_root", return_value=project_root),
                patch("ai_ecosystem.discovery.homepage_discovery.generate_discovery_run_id", return_value="run_test"),
                patch("ai_ecosystem.discovery.homepage_discovery.fetch_homepage", side_effect=fake_fetch_homepage),
                patch(
                    "ai_ecosystem.discovery.homepage_discovery.discover_robots_txt",
                    return_value=("https://success.example/robots.txt", [], None),
                ),
                patch("ai_ecosystem.discovery.homepage_discovery.discover_sitemap_candidates", return_value=[]),
            ):
                run_id = run_homepage_discovery(input_csv=sample_csv)

            with sqlite3.connect(db_path) as connection:
                run_row = connection.execute(
                    """
                    SELECT
                        run_id,
                        completed_at,
                        domains_attempted,
                        domains_succeeded,
                        domains_failed,
                        candidate_urls_discovered,
                        candidate_urls_retained
                    FROM discovery_runs
                    WHERE run_id = 'run_test'
                    """
                ).fetchone()
                candidate_run_ids = {
                    row[0]
                    for row in connection.execute("SELECT DISTINCT discovery_run_id FROM candidate_pages")
                }
                status_run_ids = {
                    row[0]
                    for row in connection.execute("SELECT DISTINCT discovery_run_id FROM domain_discovery_status")
                }

            self.assertEqual(run_id, "run_test")
            self.assertEqual(run_row[0], "run_test")
            self.assertIsNotNone(run_row[1])
            self.assertEqual(run_row[2], 2)
            self.assertEqual(run_row[3], 1)
            self.assertEqual(run_row[4], 1)
            self.assertEqual(run_row[5], 3)
            self.assertEqual(run_row[6], 3)
            self.assertEqual(candidate_run_ids, {"run_test"})
            self.assertEqual(status_run_ids, {"run_test"})
            self.assertTrue((project_root / "logs" / "discovery_run_test.log").exists())
            summary_path = project_root / "logs" / "discovery_run_test_summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["run_id"], "run_test")
            self.assertIsNotNone(summary["completed_at"])
            self.assertGreaterEqual(summary["duration_seconds"], 0)
            self.assertEqual(summary["domains_attempted"], 2)
            self.assertEqual(summary["domains_succeeded"], 1)
            self.assertEqual(summary["domains_failed"], 1)
            self.assertEqual(summary["candidate_urls_discovered"], 3)
            self.assertEqual(summary["candidate_urls_retained"], 3)
            self.assertEqual(summary["max_workers"], 1)
            self.assertGreaterEqual(summary["domains_per_second"], 0)
            self.assertEqual(summary["asset_static_urls_filtered"], 0)
            self.assertEqual(summary["duplicate_urls_collapsed"], 1)
            self.assertEqual(summary["homepage_variants_collapsed"], 0)
            self.assertEqual(summary["query_urls_normalized"], 1)
            self.assertEqual(summary["hard_negative_urls_filtered"], 0)
            self.assertEqual(summary["soft_negative_urls_retained"], 0)
            self.assertEqual(summary["homepage_success_rate"], 50.0)
            self.assertEqual(summary["sample_input_csv"], str(sample_csv.resolve()))
            self.assertEqual(summary["config"]["timeout"], 1)
            self.assertEqual(summary["config"]["max_retained_urls"], 20)
            self.assertEqual(summary["config"]["max_workers"], 1)
            self.assertEqual(summary["config"]["user_agent"], "TestBot/0.1")
            self.assertEqual(summary["top_failure_categories"], [{"category": "unknown", "count": 1}])


if __name__ == "__main__":
    unittest.main()
