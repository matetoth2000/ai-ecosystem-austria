from __future__ import annotations

import gzip
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from ai_ecosystem.acquisition.html_fetcher import (
    CandidatePage,
    FetchResult,
    acquire_one_candidate,
    classify_request_exception,
    fetch_candidate_page,
    html_storage_path,
    normalized_url_hash,
    select_candidate_pages,
    upsert_scraped_page,
)
from ai_ecosystem.db.init_db import init_database


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
        history: list[object] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.history = history or []


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "logs").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(["database:", "  path: db/test.sqlite"]),
        encoding="utf-8",
    )


def insert_candidate(connection: sqlite3.Connection, domain: str = "example.com") -> int:
    connection.execute("INSERT INTO domains (domain) VALUES (?)", (domain,))
    cursor = connection.execute(
        """
        INSERT INTO candidate_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority
        )
        VALUES (?, 'https://www.example.com/', 'https://example.com', 'homepage', 'homepage', 8)
        """,
        (domain,),
    )
    return cursor.lastrowid


class HtmlFetcherTest(unittest.TestCase):
    def test_deterministic_hash_uses_normalized_url(self) -> None:
        self.assertEqual(normalized_url_hash("https://foo.com"), normalized_url_hash("https://foo.com/"))
        self.assertEqual(normalized_url_hash("https://foo.com"), normalized_url_hash("https://www.foo.com/"))

    def test_fetch_html_stores_gzip_and_extracts_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            html = b"""
            <html lang="de">
              <head>
                <title>Example Title</title>
                <meta name="description" content="Example description">
                <link rel="canonical" href="https://www.example.com/">
              </head>
              <body>One two three four.</body>
            </html>
            """
            response = FakeResponse(
                url="https://www.example.com/",
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=html,
                history=[object()],
            )
            session = FakeSession([response])
            candidate = CandidatePage(1, None, "example.com", "https://www.example.com/", "https://example.com", None)

            result = fetch_candidate_page(candidate, project_root, "run_html", 1, 0, "TestBot", session=session)

            self.assertEqual(result.scrape_status, "success")
            self.assertEqual(result.title, "Example Title")
            self.assertEqual(result.meta_description, "Example description")
            self.assertEqual(result.canonical_url, "https://example.com")
            self.assertEqual(result.language, "de")
            self.assertEqual(result.word_count_estimate, 6)
            self.assertEqual(result.redirect_count, 1)
            self.assertIsNotNone(result.html_path)
            stored_path = project_root / result.html_path
            self.assertTrue(stored_path.exists())
            with gzip.open(stored_path, "rb") as file:
                self.assertEqual(file.read(), html)

    def test_non_html_is_recorded_without_saving_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            response = FakeResponse(
                url="https://example.com/file.pdf",
                headers={"Content-Type": "application/pdf", "Content-Length": "12"},
                content=b"%PDF content",
            )
            session = FakeSession([response])
            candidate = CandidatePage(1, None, "example.com", "https://example.com/file.pdf", "https://example.com/file.pdf", None)

            result = fetch_candidate_page(candidate, project_root, "run_pdf", 1, 0, "TestBot", session=session)

            self.assertEqual(result.scrape_status, "non_html")
            self.assertEqual(result.content_type, "application/pdf")
            self.assertEqual(result.content_length, 12)
            self.assertIsNone(result.html_path)
            self.assertFalse((project_root / "data" / "raw" / "html").exists())

    def test_retry_behavior_retries_timeout_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            response = FakeResponse(
                url="https://example.com",
                headers={"Content-Type": "text/html"},
                content=b"<html><body>ok</body></html>",
            )
            session = FakeSession([requests.Timeout("first timeout"), response])
            candidate = CandidatePage(1, None, "example.com", "https://example.com", "https://example.com", None)

            result = fetch_candidate_page(candidate, project_root, "run_retry", 1, 1, "TestBot", session=session)

            self.assertEqual(session.calls, 2)
            self.assertEqual(result.scrape_status, "success")

    def test_request_exception_classification_uses_requests_exceptions(self) -> None:
        self.assertEqual(classify_request_exception(requests.exceptions.Timeout("timeout")), "timeout")
        self.assertEqual(classify_request_exception(requests.exceptions.SSLError("ssl")), "ssl_error")
        self.assertEqual(
            classify_request_exception(requests.exceptions.ConnectionError("nodename nor servname provided")),
            "dns_error",
        )

    def test_upsert_and_skip_existing_prevent_duplicate_metadata_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                candidate_page_id = insert_candidate(connection)
                result = FetchResult(
                    candidate_page_id=candidate_page_id,
                    discovered_page_id=None,
                    url="https://www.example.com/",
                    final_url="https://example.com",
                    domain="example.com",
                    http_status=200,
                    content_type="text/html",
                    content_length=10,
                    fetched_at="2026-06-05T10:00:00+00:00",
                    html_path="data/raw/html/example.com/test.html.gz",
                    scrape_status="success",
                    error_message=None,
                    title="Old",
                    meta_description=None,
                    canonical_url="https://example.com",
                    language="en",
                    word_count_estimate=1,
                    redirect_count=0,
                    retained_at_discovery=1,
                    acquisition_run_id="old_run",
                )
                upsert_scraped_page(connection, result)
                connection.commit()

            candidate = CandidatePage(candidate_page_id, None, "example.com", "https://www.example.com/", "https://example.com", None)
            logger = logging.getLogger("test_skip_existing")
            with patch("ai_ecosystem.acquisition.html_fetcher.fetch_candidate_page") as fetch_mock:
                skipped = acquire_one_candidate(
                    db_path,
                    project_root,
                    candidate,
                    "new_run",
                    1,
                    0,
                    "TestBot",
                    skip_existing=True,
                    force_refresh=False,
                    logger=logger,
                )

            self.assertIsNone(skipped)
            fetch_mock.assert_not_called()
            with sqlite3.connect(db_path) as connection:
                row_count = connection.execute("SELECT COUNT(*) FROM scraped_pages").fetchone()[0]
            self.assertEqual(row_count, 1)

    def test_force_refresh_reacquires_and_updates_existing_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                candidate_page_id = insert_candidate(connection)
                old_result = FetchResult(
                    candidate_page_id=candidate_page_id,
                    discovered_page_id=None,
                    url="https://www.example.com/",
                    final_url="https://example.com",
                    domain="example.com",
                    http_status=200,
                    content_type="text/html",
                    content_length=10,
                    fetched_at="2026-06-05T10:00:00+00:00",
                    html_path="data/raw/html/example.com/old.html.gz",
                    scrape_status="success",
                    error_message=None,
                    title="Old",
                    meta_description=None,
                    canonical_url="https://example.com",
                    language="en",
                    word_count_estimate=1,
                    redirect_count=0,
                    retained_at_discovery=1,
                    acquisition_run_id="old_run",
                )
                upsert_scraped_page(connection, old_result)
                connection.commit()

            new_result = FetchResult(
                candidate_page_id=candidate_page_id,
                discovered_page_id=None,
                url="https://www.example.com/",
                final_url="https://example.com",
                domain="example.com",
                http_status=200,
                content_type="text/html",
                content_length=20,
                fetched_at="2026-06-05T11:00:00+00:00",
                html_path="data/raw/html/example.com/new.html.gz",
                scrape_status="success",
                error_message=None,
                title="New",
                meta_description=None,
                canonical_url="https://example.com",
                language="en",
                word_count_estimate=2,
                redirect_count=0,
                retained_at_discovery=1,
                acquisition_run_id="new_run",
            )
            candidate = CandidatePage(candidate_page_id, None, "example.com", "https://www.example.com/", "https://example.com", None)
            logger = logging.getLogger("test_force_refresh")
            with patch("ai_ecosystem.acquisition.html_fetcher.fetch_candidate_page", return_value=new_result) as fetch_mock:
                acquired = acquire_one_candidate(
                    db_path,
                    project_root,
                    candidate,
                    "new_run",
                    1,
                    0,
                    "TestBot",
                    skip_existing=True,
                    force_refresh=True,
                    logger=logger,
                )

            self.assertEqual(acquired, new_result)
            fetch_mock.assert_called_once()
            with sqlite3.connect(db_path) as connection:
                row = connection.execute("SELECT COUNT(*), title FROM scraped_pages").fetchone()
            self.assertEqual(row, (1, "New"))

    def test_upsert_discovered_result_updates_existing_candidate_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                candidate_page_id = insert_candidate(connection)
                discovered_page_id = connection.execute(
                    """
                    INSERT INTO discovered_pages (
                        domain,
                        url,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        retained_at_discovery,
                        discovery_run_id
                    )
                    VALUES (
                        'example.com',
                        'https://example.com',
                        'https://example.com',
                        'homepage',
                        'homepage',
                        8,
                        1,
                        'run_scope'
                    )
                    """
                ).lastrowid
                retained_result = FetchResult(
                    candidate_page_id=candidate_page_id,
                    discovered_page_id=None,
                    url="https://example.com",
                    final_url="https://example.com",
                    domain="example.com",
                    http_status=200,
                    content_type="text/html",
                    content_length=10,
                    fetched_at="2026-06-05T10:00:00+00:00",
                    html_path="data/raw/html/example.com/old.html.gz",
                    scrape_status="success",
                    error_message=None,
                    title="Retained",
                    meta_description=None,
                    canonical_url="https://example.com",
                    language="en",
                    word_count_estimate=1,
                    redirect_count=0,
                    retained_at_discovery=1,
                    acquisition_run_id="retained_run",
                )
                discovered_result = FetchResult(
                    candidate_page_id=candidate_page_id,
                    discovered_page_id=discovered_page_id,
                    url="https://example.com",
                    final_url="https://example.com",
                    domain="example.com",
                    http_status=200,
                    content_type="text/html",
                    content_length=20,
                    fetched_at="2026-06-05T11:00:00+00:00",
                    html_path="data/raw/html/example.com/new.html.gz",
                    scrape_status="success",
                    error_message=None,
                    title="Discovered",
                    meta_description=None,
                    canonical_url="https://example.com",
                    language="en",
                    word_count_estimate=2,
                    redirect_count=0,
                    retained_at_discovery=1,
                    acquisition_run_id="discovered_run",
                )
                upsert_scraped_page(connection, retained_result)
                upsert_scraped_page(connection, discovered_result)
                connection.commit()

                row = connection.execute(
                    """
                    SELECT COUNT(*), candidate_page_id, discovered_page_id, title
                    FROM scraped_pages
                    """
                ).fetchone()

            self.assertEqual(row, (1, candidate_page_id, discovered_page_id, "Discovered"))

    def test_storage_path_collapses_homepage_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self.assertEqual(
                html_storage_path(project_root, "foo.com", "https://foo.com"),
                html_storage_path(project_root, "foo.com", "https://www.foo.com/"),
            )

    def test_select_candidate_pages_uses_retained_scope_from_candidate_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.row_factory = sqlite3.Row
                candidate_page_id = insert_candidate(connection)
                selected = select_candidate_pages(connection, None, "retained")

            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].candidate_page_id, candidate_page_id)
            self.assertIsNone(selected[0].discovered_page_id)
            self.assertEqual(selected[0].page_type_guess, "homepage")
            self.assertEqual(selected[0].priority, 8)

    def test_select_candidate_pages_uses_discovered_scope_and_deduplicates_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                connection.row_factory = sqlite3.Row
                candidate_page_id = insert_candidate(connection)
                connection.execute(
                    "UPDATE candidate_pages SET discovery_run_id = 'run_scope' WHERE id = ?",
                    (candidate_page_id,),
                )
                retained_discovered_id = connection.execute(
                    """
                    INSERT INTO discovered_pages (
                        domain,
                        url,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        url_depth,
                        retained_at_discovery,
                        discovery_run_id
                    )
                    VALUES (
                        'example.com',
                        'https://example.com',
                        'https://example.com',
                        'homepage',
                        'homepage',
                        8,
                        0,
                        1,
                        'run_scope'
                    )
                    """
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO discovered_pages (
                        domain,
                        url,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        url_depth,
                        retained_at_discovery,
                        discard_reason,
                        discovery_run_id
                    )
                    VALUES (
                        'example.com',
                        'https://example.com',
                        'https://example.com',
                        'sitemap',
                        'products',
                        6,
                        0,
                        0,
                        'duplicate',
                        'run_scope'
                    )
                    """
                )
                discarded_discovered_id = connection.execute(
                    """
                    INSERT INTO discovered_pages (
                        domain,
                        url,
                        normalized_url,
                        discovery_method,
                        page_type_guess,
                        priority,
                        url_depth,
                        retained_at_discovery,
                        discard_reason,
                        discovery_run_id
                    )
                    VALUES (
                        'example.com',
                        'https://example.com/news',
                        'https://example.com/news',
                        'sitemap',
                        'news',
                        3,
                        1,
                        0,
                        'retention_cap',
                        'run_scope'
                    )
                    """
                ).lastrowid
                selected = select_candidate_pages(connection, None, "discovered")

            self.assertEqual(len(selected), 2)
            by_url = {candidate.normalized_url: candidate for candidate in selected}
            self.assertEqual(by_url["https://example.com"].candidate_page_id, candidate_page_id)
            self.assertEqual(by_url["https://example.com"].discovered_page_id, retained_discovered_id)
            self.assertEqual(by_url["https://example.com"].page_type_guess, "homepage")
            self.assertEqual(by_url["https://example.com/news"].candidate_page_id, None)
            self.assertEqual(by_url["https://example.com/news"].discovered_page_id, discarded_discovered_id)
            self.assertEqual(by_url["https://example.com/news"].page_type_guess, "news")
            self.assertEqual(by_url["https://example.com/news"].priority, 3)


if __name__ == "__main__":
    unittest.main()
