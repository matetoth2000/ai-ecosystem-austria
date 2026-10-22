"""Fetch candidate pages and store compressed raw HTML."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import requests
from bs4 import BeautifulSoup

from ai_ecosystem.db.init_db import (
    ensure_acquisition_schema,
    find_project_root,
    load_config,
    resolve_database_path,
)
from ai_ecosystem.discovery.discovery_utils import get_sample_domains
from ai_ecosystem.discovery.normalization import normalize_url


HTML_STATUSES = {"success", "non_html"}
ERROR_STATUSES = {
    "timeout",
    "http_error",
    "dns_error",
    "ssl_error",
    "connection_error",
    "other_error",
}


@contextmanager
def measure_stage(timings: dict[str, float] | None, name: str):
    """Record elapsed stage time, including failed attempts and lock waits."""
    started = time.perf_counter()
    try:
        yield
    finally:
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + time.perf_counter() - started


@dataclass(frozen=True)
class CandidatePage:
    candidate_page_id: int | None
    discovered_page_id: int | None
    domain: str
    url: str
    normalized_url: str
    discovery_run_id: str | None
    page_type_guess: str | None = None
    priority: int | None = None


@dataclass(frozen=True)
class FetchResult:
    candidate_page_id: int | None
    discovered_page_id: int | None
    url: str
    final_url: str | None
    domain: str
    http_status: int | None
    content_type: str | None
    content_length: int | None
    fetched_at: str
    html_path: str | None
    scrape_status: str
    error_message: str | None
    title: str | None
    meta_description: str | None
    canonical_url: str | None
    language: str | None
    word_count_estimate: int | None
    redirect_count: int
    retained_at_discovery: int
    acquisition_run_id: str


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_acquisition_run_id() -> str:
    return f"acquisition_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"


def normalized_url_hash(url: str) -> str:
    normalized = normalize_url(url) or url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def html_storage_path(project_root: Path, domain: str, normalized_url: str) -> Path:
    return project_root / "data" / "raw" / "html" / domain / f"{normalized_url_hash(normalized_url)}.html.gz"


def is_html_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "html" in content_type.lower()


def response_content_length(response: requests.Response) -> int:
    header_value = response.headers.get("Content-Length")
    if header_value:
        try:
            return int(header_value)
        except ValueError:
            pass
    return len(response.content or b"")


def extract_html_metadata(html: bytes, final_url: str | None) -> dict[str, str | int | None]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None

    description_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_description = None
    if description_tag:
        description_value = description_tag.get("content")
        meta_description = description_value.strip() if isinstance(description_value, str) else None

    canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    canonical_url = None
    if canonical_tag:
        canonical_value = canonical_tag.get("href")
        canonical_url = normalize_url(canonical_value) if isinstance(canonical_value, str) else None

    html_tag = soup.find("html")
    language = None
    if html_tag:
        language_value = html_tag.get("lang")
        language = language_value.strip() if isinstance(language_value, str) else None

    text = soup.get_text(" ", strip=True)
    word_count_estimate = len(re.findall(r"\b\w+\b", text))

    return {
        "title": title,
        "meta_description": meta_description,
        "canonical_url": canonical_url or (normalize_url(final_url) if final_url else None),
        "language": language,
        "word_count_estimate": word_count_estimate,
    }


def save_compressed_html(path: Path, html: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as file:
        file.write(html)


def setup_acquisition_logger(project_root: Path, acquisition_run_id: str) -> logging.Logger:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"acquisition.{acquisition_run_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_dir / f"acquisition_{acquisition_run_id}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def classify_request_exception(error: requests.RequestException) -> str:
    if isinstance(error, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(error, requests.exceptions.SSLError):
        return "ssl_error"
    if isinstance(error, requests.exceptions.ConnectionError):
        message = str(error).lower()
        if any(token in message for token in ("name or service not known", "nodename", "dns", "resolve")):
            return "dns_error"
        return "connection_error"
    return "other_error"


def request_with_retries(
    session: requests.Session,
    url: str,
    timeout_seconds: int,
    retry_count: int,
    user_agent: str,
) -> requests.Response:
    headers = {"User-Agent": user_agent}
    attempts = retry_count + 1
    last_error: requests.RequestException | None = None
    for _attempt in range(attempts):
        try:
            return session.get(url, timeout=timeout_seconds, headers=headers, allow_redirects=True)
        except requests.RequestException as error:
            last_error = error
    assert last_error is not None
    raise last_error


def select_candidate_pages(
    connection: sqlite3.Connection,
    sample_id: str | None,
    fetch_scope: str,
    limit: int | None = None,
    deterministic_limit_order: bool = False,
) -> list[CandidatePage]:
    if fetch_scope not in {"discovered", "retained"}:
        raise ValueError("acquisition.fetch_scope must be either 'discovered' or 'retained'")

    params: list[str | int] = []
    table_alias = "cp" if fetch_scope == "retained" else "dp"
    where_clauses = [f"{table_alias}.normalized_url IS NOT NULL"]
    if sample_id:
        domains = get_sample_domains(connection, sample_id)
        if not domains:
            raise ValueError(f"No domains found for sample_id: {sample_id}")
        placeholders = ",".join("?" for _ in domains)
        where_clauses.append(f"{table_alias}.domain IN ({placeholders})")
        params.extend(domains)

    if fetch_scope == "retained":
        order_by = (
            "cp.domain, cp.normalized_url"
            if deterministic_limit_order
            else "cp.domain, cp.priority DESC, cp.url_depth, cp.normalized_url"
        )
        query = f"""
            SELECT
                cp.id AS candidate_page_id,
                NULL AS discovered_page_id,
                cp.domain,
                cp.url,
                cp.normalized_url,
                cp.discovery_run_id,
                cp.page_type_guess,
                cp.priority
            FROM candidate_pages AS cp
            WHERE {" AND ".join(where_clauses)}
            ORDER BY {order_by}
        """
    else:
        order_by = (
            "domain, normalized_url"
            if deterministic_limit_order
            else "domain, priority DESC, url_depth, normalized_url"
        )
        query = f"""
            WITH ranked_discovered AS (
                SELECT
                    dp.discovered_page_id,
                    cp.id AS candidate_page_id,
                    dp.domain,
                    dp.url,
                    dp.normalized_url,
                    dp.discovery_run_id,
                    dp.page_type_guess,
                    dp.priority,
                    dp.url_depth,
                    dp.discovery_method,
                    dp.retained_at_discovery,
                    ROW_NUMBER() OVER (
                        PARTITION BY dp.domain, dp.normalized_url
                        ORDER BY
                            dp.retained_at_discovery DESC,
                            CASE dp.discovery_method WHEN 'homepage' THEN 0 ELSE 1 END,
                            dp.priority DESC,
                            dp.discovered_page_id
                    ) AS row_number
                FROM discovered_pages AS dp
                LEFT JOIN candidate_pages AS cp
                  ON cp.domain = dp.domain
                 AND cp.normalized_url = dp.normalized_url
                 AND COALESCE(cp.discovery_run_id, '') = COALESCE(dp.discovery_run_id, '')
                WHERE {" AND ".join(where_clauses)}
            )
            SELECT
                candidate_page_id,
                discovered_page_id,
                domain,
                url,
                normalized_url,
                discovery_run_id,
                page_type_guess,
                priority
            FROM ranked_discovered
            WHERE row_number = 1
            ORDER BY {order_by}
        """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = connection.execute(query, tuple(params)).fetchall()
    return [
        CandidatePage(
            candidate_page_id=row["candidate_page_id"],
            discovered_page_id=row["discovered_page_id"],
            domain=row["domain"],
            url=row["url"],
            normalized_url=row["normalized_url"],
            discovery_run_id=row["discovery_run_id"],
            page_type_guess=row["page_type_guess"],
            priority=row["priority"],
        )
        for row in rows
    ]


def existing_successful_scrape(
    connection: sqlite3.Connection,
    candidate_page_id: int | None,
    discovered_page_id: int | None = None,
) -> sqlite3.Row | None:
    if discovered_page_id is not None:
        existing = connection.execute(
            """
            SELECT scraped_page_id, html_path, scrape_status
            FROM scraped_pages
            WHERE discovered_page_id = ?
              AND scrape_status = 'success'
              AND html_path IS NOT NULL
            """,
            (discovered_page_id,),
        ).fetchone()
        if existing is not None:
            return existing
    if candidate_page_id is None:
        return None
    return connection.execute(
        """
        SELECT scraped_page_id, html_path, scrape_status
        FROM scraped_pages
        WHERE candidate_page_id = ?
          AND scrape_status = 'success'
          AND html_path IS NOT NULL
        """,
        (candidate_page_id,),
    ).fetchone()


def upsert_scraped_page(connection: sqlite3.Connection, result: FetchResult) -> None:
    existing: sqlite3.Row | tuple | None = None
    if result.discovered_page_id is not None:
        existing = connection.execute(
            "SELECT scraped_page_id FROM scraped_pages WHERE discovered_page_id = ?",
            (result.discovered_page_id,),
        ).fetchone()
        if existing is None and result.candidate_page_id is not None:
            existing = connection.execute(
                "SELECT scraped_page_id FROM scraped_pages WHERE candidate_page_id = ?",
                (result.candidate_page_id,),
            ).fetchone()
    elif result.candidate_page_id is not None:
        existing = connection.execute(
            "SELECT scraped_page_id FROM scraped_pages WHERE candidate_page_id = ?",
            (result.candidate_page_id,),
        ).fetchone()

    if existing:
        connection.execute(
            """
            UPDATE scraped_pages
            SET candidate_page_id = ?,
                discovered_page_id = ?,
                url = ?,
                final_url = ?,
                domain = ?,
                http_status = ?,
                content_type = ?,
                content_length = ?,
                fetched_at = ?,
                html_path = ?,
                scrape_status = ?,
                error_message = ?,
                title = ?,
                meta_description = ?,
                canonical_url = ?,
                language = ?,
                word_count_estimate = ?,
                redirect_count = ?,
                retained_at_discovery = ?,
                acquisition_run_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE scraped_page_id = ?
            """,
            (
                result.candidate_page_id,
                result.discovered_page_id,
                result.url,
                result.final_url,
                result.domain,
                result.http_status,
                result.content_type,
                result.content_length,
                result.fetched_at,
                result.html_path,
                result.scrape_status,
                result.error_message,
                result.title,
                result.meta_description,
                result.canonical_url,
                result.language,
                result.word_count_estimate,
                result.redirect_count,
                result.retained_at_discovery,
                result.acquisition_run_id,
                existing[0],
            ),
        )
        return

    connection.execute(
        """
        INSERT INTO scraped_pages (
            candidate_page_id,
            discovered_page_id,
            url,
            final_url,
            domain,
            http_status,
            content_type,
            content_length,
            fetched_at,
            html_path,
            scrape_status,
            error_message,
            title,
            meta_description,
            canonical_url,
            language,
            word_count_estimate,
            redirect_count,
            retained_at_discovery,
            acquisition_run_id,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            result.candidate_page_id,
            result.discovered_page_id,
            result.url,
            result.final_url,
            result.domain,
            result.http_status,
            result.content_type,
            result.content_length,
            result.fetched_at,
            result.html_path,
            result.scrape_status,
            result.error_message,
            result.title,
            result.meta_description,
            result.canonical_url,
            result.language,
            result.word_count_estimate,
            result.redirect_count,
            result.retained_at_discovery,
            result.acquisition_run_id,
        ),
    )


def fetch_candidate_page(
    candidate: CandidatePage,
    project_root: Path,
    acquisition_run_id: str,
    timeout_seconds: int,
    retry_count: int,
    user_agent: str,
    session: requests.Session | None = None,
    timings: dict[str, float] | None = None,
) -> FetchResult:
    active_session = session or requests.Session()
    fetched_at = utc_now_iso()
    try:
        with measure_stage(timings, "download"):
            response = request_with_retries(
                active_session,
                candidate.url,
                timeout_seconds,
                retry_count,
                user_agent,
            )
    except requests.RequestException as error:
        return FetchResult(
            candidate_page_id=candidate.candidate_page_id,
            discovered_page_id=candidate.discovered_page_id,
            url=candidate.url,
            final_url=None,
            domain=candidate.domain,
            http_status=None,
            content_type=None,
            content_length=None,
            fetched_at=fetched_at,
            html_path=None,
            scrape_status=classify_request_exception(error),
            error_message=str(error),
            title=None,
            meta_description=None,
            canonical_url=None,
            language=None,
            word_count_estimate=None,
            redirect_count=0,
            retained_at_discovery=1,
            acquisition_run_id=acquisition_run_id,
        )

    content_type = response.headers.get("Content-Type")
    content_length = response_content_length(response)
    redirect_count = len(response.history)
    final_url = response.url
    if response.status_code >= 400:
        return FetchResult(
            candidate_page_id=candidate.candidate_page_id,
            discovered_page_id=candidate.discovered_page_id,
            url=candidate.url,
            final_url=final_url,
            domain=candidate.domain,
            http_status=response.status_code,
            content_type=content_type,
            content_length=content_length,
            fetched_at=fetched_at,
            html_path=None,
            scrape_status="http_error",
            error_message=f"HTTP status {response.status_code}",
            title=None,
            meta_description=None,
            canonical_url=None,
            language=None,
            word_count_estimate=None,
            redirect_count=redirect_count,
            retained_at_discovery=1,
            acquisition_run_id=acquisition_run_id,
        )

    if not is_html_content_type(content_type):
        return FetchResult(
            candidate_page_id=candidate.candidate_page_id,
            discovered_page_id=candidate.discovered_page_id,
            url=candidate.url,
            final_url=final_url,
            domain=candidate.domain,
            http_status=response.status_code,
            content_type=content_type,
            content_length=content_length,
            fetched_at=fetched_at,
            html_path=None,
            scrape_status="non_html",
            error_message=None,
            title=None,
            meta_description=None,
            canonical_url=None,
            language=None,
            word_count_estimate=None,
            redirect_count=redirect_count,
            retained_at_discovery=1,
            acquisition_run_id=acquisition_run_id,
        )

    with measure_stage(timings, "html_processing"):
        metadata = extract_html_metadata(response.content, final_url)
    with measure_stage(timings, "html_storage"):
        html_path = html_storage_path(project_root, candidate.domain, candidate.normalized_url)
        save_compressed_html(html_path, response.content)
    return FetchResult(
        candidate_page_id=candidate.candidate_page_id,
        discovered_page_id=candidate.discovered_page_id,
        url=candidate.url,
        final_url=final_url,
        domain=candidate.domain,
        http_status=response.status_code,
        content_type=content_type,
        content_length=content_length,
        fetched_at=fetched_at,
        html_path=str(html_path.relative_to(project_root)),
        scrape_status="success",
        error_message=None,
        title=metadata["title"],
        meta_description=metadata["meta_description"],
        canonical_url=metadata["canonical_url"],
        language=metadata["language"],
        word_count_estimate=metadata["word_count_estimate"],
        redirect_count=redirect_count,
        retained_at_discovery=1,
        acquisition_run_id=acquisition_run_id,
    )


def acquire_one_candidate(
    db_path: Path,
    project_root: Path,
    candidate: CandidatePage,
    acquisition_run_id: str,
    timeout_seconds: int,
    retry_count: int,
    user_agent: str,
    skip_existing: bool,
    force_refresh: bool,
    logger: logging.Logger,
) -> FetchResult | None:
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        existing = existing_successful_scrape(
            connection,
            candidate.candidate_page_id,
            candidate.discovered_page_id,
        )
        if skip_existing and not force_refresh and existing:
            logger.info("url=%s status=skipped_existing html_path=%s", candidate.url, existing["html_path"])
            return None

    result = fetch_candidate_page(
        candidate,
        project_root,
        acquisition_run_id,
        timeout_seconds,
        retry_count,
        user_agent,
    )

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        upsert_scraped_page(connection, result)
        connection.commit()
    logger.info(
        "url=%s status=%s http_status=%s html_path=%s",
        result.url,
        result.scrape_status,
        result.http_status,
        result.html_path,
    )
    return result


def insert_acquisition_run(
    connection: sqlite3.Connection,
    acquisition_run_id: str,
    sample_id: str | None,
    fetch_scope: str,
    started_at: str,
    notes: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO acquisition_runs (
            acquisition_run_id,
            sample_id,
            fetch_scope,
            started_at,
            pages_attempted,
            pages_fetched,
            pages_failed,
            success_rate,
            notes
        )
        VALUES (?, ?, ?, ?, 0, 0, 0, 0.0, ?)
        """,
        (acquisition_run_id, sample_id, fetch_scope, started_at, notes),
    )


def update_acquisition_run(
    connection: sqlite3.Connection,
    acquisition_run_id: str,
    completed_at: str,
    pages_attempted: int,
    pages_fetched: int,
    pages_failed: int,
    success_rate: float | None = None,
) -> None:
    resolved_success_rate = (
        success_rate
        if success_rate is not None
        else ((pages_fetched / pages_attempted) * 100 if pages_attempted else 0.0)
    )
    connection.execute(
        """
        UPDATE acquisition_runs
        SET completed_at = ?,
            pages_attempted = ?,
            pages_fetched = ?,
            pages_failed = ?,
            success_rate = ?
        WHERE acquisition_run_id = ?
        """,
        (
            completed_at,
            pages_attempted,
            pages_fetched,
            pages_failed,
            resolved_success_rate,
            acquisition_run_id,
        ),
    )


def run_acquisition(
    sample_id: str | None = None,
    limit: int | None = None,
    fetch_scope: str | None = None,
    force_refresh: bool = False,
    max_workers: int | None = None,
) -> str:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    acquisition_config = config.get("acquisition", {})
    discovery_config = config.get("discovery", {})

    resolved_fetch_scope = fetch_scope or acquisition_config.get("fetch_scope", "discovered")
    timeout_seconds = int(acquisition_config.get("timeout_seconds", 15))
    retry_count = int(acquisition_config.get("retry_count", 2))
    worker_count = max(1, int(max_workers or acquisition_config.get("max_workers", 1)))
    skip_existing = bool(acquisition_config.get("skip_existing", True))
    resolved_force_refresh = force_refresh or bool(acquisition_config.get("force_refresh", False))
    user_agent = discovery_config.get("user_agent", "AIAustriaResearchBot/0.1")

    acquisition_run_id = generate_acquisition_run_id()
    logger = setup_acquisition_logger(project_root, acquisition_run_id)
    started_at = utc_now_iso()

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        candidates = select_candidate_pages(connection, sample_id, resolved_fetch_scope, limit)
        insert_acquisition_run(
            connection,
            acquisition_run_id,
            sample_id,
            resolved_fetch_scope,
            started_at,
            "raw HTML acquisition",
        )
        connection.commit()

    pages_fetched = 0
    pages_failed = 0
    results: list[FetchResult] = []
    print(f"Acquisition workers: {worker_count}")
    print(f"Pages selected: {len(candidates)}")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_candidate = {
            executor.submit(
                acquire_one_candidate,
                db_path,
                project_root,
                candidate,
                acquisition_run_id,
                timeout_seconds,
                retry_count,
                user_agent,
                skip_existing,
                resolved_force_refresh,
                logger,
            ): candidate
            for candidate in candidates
        }
        for index, future in enumerate(as_completed(future_to_candidate), start=1):
            candidate = future_to_candidate[future]
            try:
                result = future.result()
            except Exception as error:
                pages_failed += 1
                logger.exception("url=%s status=other_error error=%s", candidate.url, error)
                print(f"[{index}/{len(candidates)}] {candidate.url} -> other_error")
                continue
            if result is None:
                print(f"[{index}/{len(candidates)}] {candidate.url} -> skipped_existing")
                continue
            results.append(result)
            if result.scrape_status in HTML_STATUSES:
                pages_fetched += 1
            elif result.scrape_status in ERROR_STATUSES:
                pages_failed += 1
            print(f"[{index}/{len(candidates)}] {candidate.url} -> {result.scrape_status}")

    with sqlite3.connect(db_path, timeout=60) as connection:
        update_acquisition_run(
            connection,
            acquisition_run_id,
            utc_now_iso(),
            len(candidates),
            pages_fetched,
            pages_failed,
        )
        connection.commit()

    print("Acquisition summary")
    print(f"  Run ID: {acquisition_run_id}")
    print(f"  Pages attempted: {len(candidates)}")
    print(f"  Pages fetched: {pages_fetched}")
    print(f"  Pages failed: {pages_failed}")
    print(f"  Results recorded this run: {len(results)}")
    return acquisition_run_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acquire raw HTML for candidate pages.")
    parser.add_argument("--sample-id", help="Optional registered sample_id to acquire.")
    parser.add_argument("--limit", type=int, help="Optional page limit for small pilots.")
    parser.add_argument("--fetch-scope", choices=["discovered", "retained"], help="Candidate fetch scope.")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch pages even if already stored.")
    parser.add_argument("--max-workers", type=int, help="Override acquisition.max_workers.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_acquisition(
        sample_id=args.sample_id,
        limit=args.limit,
        fetch_scope=args.fetch_scope,
        force_refresh=args.force_refresh,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    main()
