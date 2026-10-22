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
from ai_ecosystem.acquisition.html_fetcher import existing_successful_scrape, select_candidate_pages
from ai_ecosystem.acquisition.run_acquisition import AcquisitionConfig, acquire_candidate
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

    def test_run_homepage_discovery_retention_audit_is_optional(self) -> None:
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
                        "  retention_audit_enabled: false",
                        "  request_timeout_seconds: 1",
                        "  user_agent: TestBot/0.1",
                    ]
                ),
                encoding="utf-8",
            )
            sample_csv = project_root / "data" / "interim" / "sample.csv"
            sample_csv.write_text("\n".join(["domain", "success.example"]), encoding="utf-8")
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('success.example')")

            def fake_fetch_homepage(domain, timeout_seconds, user_agent, logger, retries=1):
                return FetchedHomepage(
                    requested_url="https://success.example",
                    final_url="https://success.example/",
                    status_code=200,
                    html=FAKE_HTML.replace("example.at", "success.example"),
                    page_title="Success Example",
                )

            with (
                patch("ai_ecosystem.discovery.homepage_discovery.find_project_root", return_value=project_root),
                patch(
                    "ai_ecosystem.discovery.homepage_discovery.generate_discovery_run_id",
                    side_effect=["no_audit_run", "audit_run"],
                ),
                patch("ai_ecosystem.discovery.homepage_discovery.fetch_homepage", side_effect=fake_fetch_homepage),
                patch(
                    "ai_ecosystem.discovery.homepage_discovery.discover_robots_txt_for_hosts",
                    return_value=("https://success.example/robots.txt", [], None, 1, 0),
                ),
                patch("ai_ecosystem.discovery.homepage_discovery.discover_sitemap_candidates", return_value=[]),
            ):
                run_homepage_discovery(input_csv=sample_csv)
                with sqlite3.connect(db_path) as connection:
                    audit_count = connection.execute("SELECT COUNT(*) FROM url_retention_audit").fetchone()[0]
                self.assertEqual(audit_count, 0)

                run_homepage_discovery(input_csv=sample_csv, enable_retention_audit=True)

            with sqlite3.connect(db_path) as connection:
                decision_counts = dict(
                    connection.execute(
                        """
                        SELECT decision, COUNT(*)
                        FROM url_retention_audit
                        GROUP BY decision
                        """
                    ).fetchall()
                )
                discard_reasons = dict(
                    connection.execute(
                        """
                        SELECT discard_reason, COUNT(*)
                        FROM url_retention_audit
                        WHERE decision = 'discarded'
                        GROUP BY discard_reason
                        """
                    ).fetchall()
                )
                candidate_count = connection.execute("SELECT COUNT(*) FROM candidate_pages").fetchone()[0]

            self.assertEqual(candidate_count, 3)
            self.assertEqual(decision_counts["retained"], 3)
            self.assertGreaterEqual(decision_counts["discarded"], 1)
            self.assertEqual(discard_reasons["duplicate"], 1)
            self.assertEqual(discard_reasons["external_domain"], 1)

    def test_run_homepage_discovery_populates_discovered_pages_before_retention_cap(self) -> None:
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
                        "  max_retained_candidate_urls_per_domain: 1",
                        "  retention_audit_enabled: false",
                        "  request_timeout_seconds: 1",
                        "  user_agent: TestBot/0.1",
                    ]
                ),
                encoding="utf-8",
            )
            sample_csv = project_root / "data" / "interim" / "sample.csv"
            sample_csv.write_text("\n".join(["domain", "example.at"]), encoding="utf-8")
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.execute("INSERT INTO domains (domain) VALUES ('example.at')")

            def fake_fetch_homepage(domain, timeout_seconds, user_agent, logger, retries=1):
                return FetchedHomepage(
                    requested_url="https://example.at",
                    final_url="https://example.at/",
                    status_code=200,
                    html=FAKE_HTML,
                    page_title="Example GmbH",
                )

            with (
                patch("ai_ecosystem.discovery.homepage_discovery.find_project_root", return_value=project_root),
                patch("ai_ecosystem.discovery.homepage_discovery.generate_discovery_run_id", return_value="discovered_run"),
                patch("ai_ecosystem.discovery.homepage_discovery.fetch_homepage", side_effect=fake_fetch_homepage),
                patch(
                    "ai_ecosystem.discovery.homepage_discovery.discover_robots_txt_for_hosts",
                    return_value=("https://example.at/robots.txt", [], None, 1, 0),
                ),
                patch("ai_ecosystem.discovery.homepage_discovery.discover_sitemap_candidates", return_value=[]),
            ):
                run_homepage_discovery(input_csv=sample_csv)

            with sqlite3.connect(db_path) as connection:
                candidate_count = connection.execute("SELECT COUNT(*) FROM candidate_pages").fetchone()[0]
                discovered_rows = connection.execute(
                    """
                    SELECT normalized_url, page_type_guess, priority, retained_at_discovery, discard_reason
                    FROM discovered_pages
                    ORDER BY retained_at_discovery DESC, normalized_url
                    """
                ).fetchall()

            self.assertEqual(candidate_count, 1)
            self.assertEqual(len(discovered_rows), 3)
            self.assertEqual(discovered_rows[0][3], 1)
            self.assertIsNone(discovered_rows[0][4])
            self.assertEqual(discovered_rows[1][3], 0)
            self.assertEqual(discovered_rows[1][4], "retention_cap")
            self.assertEqual(discovered_rows[2][3], 0)
            self.assertEqual(discovered_rows[2][4], "retention_cap")
            self.assertIn(("https://example.at/about", "about", 4, 1, None), discovered_rows)


class DiscoveryRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir()
        (self.root / "db").mkdir()
        (self.root / "logs").mkdir()
        (self.root / "config" / "config.yaml").write_text(
            "\n".join(
                [
                    "database:",
                    "  path: db/test.sqlite",
                    "discovery:",
                    "  request_timeout_seconds: 1",
                    "  user_agent: TestBot/0.1",
                ]
            ),
            encoding="utf-8",
        )
        self.db_path = init_database(self.root)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("INSERT INTO domains (domain) VALUES ('shared.example')")
            for index in (1, 2):
                sample_id = f"sample_{index}"
                company_id = f"C{index}"
                connection.execute(
                    "INSERT INTO firms (bvd_id, normalized_domain) VALUES (?, 'shared.example')",
                    (company_id,),
                )
                connection.execute(
                    "INSERT INTO sample_sets (sample_id, sample_name, created_at) VALUES (?, ?, '2026-10-07')",
                    (sample_id, sample_id),
                )
                connection.execute(
                    "INSERT INTO sample_domains (sample_id, domain) VALUES (?, 'shared.example')",
                    (sample_id,),
                )
                connection.execute(
                    """INSERT INTO sample_companies
                       (sample_id, company_id, sample_rank, normalized_domain, selection_reason)
                       VALUES (?, ?, 1, 'shared.example', 'test')""",
                    (sample_id, company_id),
                )

    def run_discovery(self, sample_id: str, run_id: str, paths: list[str]) -> None:
        html = "<html><body>" + "".join(f'<a href="/{path}">{path}</a>' for path in paths) + "</body></html>"
        homepage = FetchedHomepage(
            requested_url="https://shared.example",
            final_url="https://shared.example/",
            status_code=200,
            html=html,
            page_title="Shared",
        )
        with (
            patch("ai_ecosystem.discovery.homepage_discovery.find_project_root", return_value=self.root),
            patch("ai_ecosystem.discovery.homepage_discovery.generate_discovery_run_id", return_value=run_id),
            patch("ai_ecosystem.discovery.homepage_discovery.fetch_homepage", return_value=homepage),
            patch(
                "ai_ecosystem.discovery.homepage_discovery.discover_robots_txt_for_hosts",
                return_value=(None, [], None, 0, 0),
            ),
            patch("ai_ecosystem.discovery.homepage_discovery.discover_sitemap_candidates", return_value=[]),
        ):
            run_homepage_discovery(sample_id=sample_id)

    def save_acquired_page(self, path: str) -> tuple[int, int, int]:
        url = f"https://shared.example/{path}"
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            discovered_id = connection.execute(
                "SELECT discovered_page_id FROM discovered_pages WHERE normalized_url = ?", (url,)
            ).fetchone()[0]
            candidate_id = connection.execute(
                "SELECT id FROM candidate_pages WHERE normalized_url = ?", (url,)
            ).fetchone()[0]
            cursor = connection.execute(
                """INSERT INTO scraped_pages
                   (candidate_page_id, discovered_page_id, url, domain, scrape_status, html_path,
                    acquisition_run_id)
                   VALUES (?, ?, ?, 'shared.example', 'success', 'saved.html', 'acquisition_1')""",
                (candidate_id, discovered_id, url),
            )
            scraped_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO extracted_pages
                   (scraped_page_id, extraction_version, extraction_status, markdown_path)
                   VALUES (?, 'v1', 'success', 'saved.md')""",
                (scraped_id,),
            )
        return discovered_id, candidate_id, scraped_id

    def test_shared_site_refresh_reuses_acquired_pages_and_adds_new_urls(self) -> None:
        self.run_discovery("sample_1", "discovery_1", ["saved", "old-only", "stale"])
        saved_ids = {path: self.save_acquired_page(path) for path in ("saved", "old-only")}
        self.run_discovery("sample_2", "discovery_2", ["saved", "new"])

        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.row_factory = sqlite3.Row
            pages = select_candidate_pages(connection, "sample_2", "discovered")
            by_path = {page.normalized_url.rsplit("/", 1)[-1]: page for page in pages}
            self.assertEqual(set(by_path), {"saved", "old-only", "new"})
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM discovered_pages WHERE normalized_url = 'https://shared.example/saved'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                (by_path["saved"].discovered_page_id, by_path["saved"].candidate_page_id),
                saved_ids["saved"][:2],
            )
            self.assertEqual(by_path["saved"].discovery_run_id, "discovery_1")
            self.assertEqual(by_path["old-only"].discovery_run_id, "discovery_1")
            self.assertEqual(by_path["new"].discovery_run_id, "discovery_2")
            for path, (discovered_id, candidate_id, scraped_id) in saved_ids.items():
                self.assertEqual(
                    connection.execute(
                        "SELECT discovery_run_id FROM candidate_pages WHERE id = ?", (candidate_id,)
                    ).fetchone()[0], "discovery_1"
                )
                self.assertEqual(
                    tuple(connection.execute(
                        "SELECT discovered_page_id, candidate_page_id, acquisition_run_id "
                        "FROM scraped_pages WHERE scraped_page_id = ?", (scraped_id,)
                    ).fetchone()),
                    (discovered_id, candidate_id, "acquisition_1"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT extraction_status FROM extracted_pages WHERE scraped_page_id = ?",
                        (scraped_id,),
                    ).fetchone()[0], "success"
                )
            self.assertIsNone(
                existing_successful_scrape(
                    connection, by_path["new"].candidate_page_id, by_path["new"].discovered_page_id
                )
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM extracted_pages").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sample_companies").fetchone()[0], 2
            )

        config = AcquisitionConfig(1, 0, 1, True, False, "TestBot/0.1", 1)
        with patch("ai_ecosystem.acquisition.run_acquisition.fetch_candidate_page") as fetch:
            for path in ("saved", "old-only"):
                result = acquire_candidate(self.db_path, self.root, by_path[path], "acquisition_2", config)
                self.assertTrue(result.skipped_existing)
            fetch.assert_not_called()

        self.run_discovery("sample_2", "discovery_3", ["saved", "new"])
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM discovered_pages WHERE domain = 'shared.example'"
                ).fetchone()[0], 3
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM scraped_pages WHERE acquisition_run_id = 'acquisition_1'"
                ).fetchone()[0], 2
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_candidate_only_success_survives_refresh_and_is_skipped(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            candidate_id = connection.execute(
                """INSERT INTO candidate_pages
                   (domain, url, normalized_url, discovery_method, discovery_run_id)
                   VALUES ('shared.example', 'https://shared.example/legacy',
                           'https://shared.example/legacy', 'homepage', 'legacy_run')"""
            ).lastrowid
            connection.execute(
                """INSERT INTO scraped_pages
                   (candidate_page_id, url, domain, scrape_status, html_path)
                   VALUES (?, 'https://shared.example/legacy', 'shared.example', 'success', 'legacy.html')""",
                (candidate_id,),
            )
        self.run_discovery("sample_2", "discovery_2", ["legacy", "new"])
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            pages = select_candidate_pages(connection, "sample_2", "discovered")
            legacy = next(page for page in pages if page.normalized_url.endswith("/legacy"))
            self.assertEqual(legacy.candidate_page_id, candidate_id)
            self.assertEqual(legacy.discovery_run_id, "legacy_run")
            self.assertEqual(
                connection.execute("SELECT discovery_run_id FROM candidate_pages WHERE id = ?", (candidate_id,))
                .fetchone()[0], "legacy_run"
            )
            self.assertIsNotNone(
                existing_successful_scrape(connection, legacy.candidate_page_id, legacy.discovered_page_id)
            )

    def test_refresh_failure_rolls_back_cleanup(self) -> None:
        self.run_discovery("sample_1", "discovery_1", ["unacquired"])
        with patch(
            "ai_ecosystem.discovery.homepage_discovery.delete_discovery_candidate_pages_for_domains",
            side_effect=sqlite3.IntegrityError("injected cleanup failure"),
        ):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected cleanup failure"):
                self.run_discovery("sample_2", "discovery_2", ["new"])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM discovered_pages WHERE discovery_run_id = 'discovery_1'")
                .fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM candidate_pages WHERE discovery_run_id = 'discovery_1'")
                .fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM discovery_runs WHERE run_id = 'discovery_2'")
                .fetchone()[0], 0
            )


if __name__ == "__main__":
    unittest.main()
