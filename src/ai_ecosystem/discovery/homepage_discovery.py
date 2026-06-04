"""Homepage fetching and internal-link extraction for pilot URL discovery."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.discovery.classification import classify_page_type, is_hard_negative_url
from ai_ecosystem.discovery.discovery_utils import generate_discovery_run_id, get_sample_domains, utc_now_iso
from ai_ecosystem.discovery.normalization import (
    extract_domain,
    is_asset_or_static_url,
    is_internal_url,
    normalize_url,
    remove_tracking_parameters,
)
from ai_ecosystem.discovery.robots_discovery import extract_sitemap_urls_from_robots
from ai_ecosystem.discovery.scoring import score_candidate_page
from ai_ecosystem.discovery.sitemap_discovery import parse_sitemap_xml


DISALLOWED_HREF_PREFIXES = ("mailto:", "tel:", "javascript:")
DISCOVERY_METHODS_TO_REFRESH = ("homepage", "robots_txt", "sitemap")


@dataclass(frozen=True)
class FetchedHomepage:
    requested_url: str
    final_url: str
    status_code: int
    html: str
    page_title: str | None


@dataclass(frozen=True)
class FetchedText:
    requested_url: str
    final_url: str
    status_code: int
    text: str


@dataclass(frozen=True)
class CandidateUrl:
    url: str
    normalized_url: str
    source_domain: str
    discovered_from_url: str
    discovery_method: str
    page_type_guess: str
    url_depth: int
    priority: int


@dataclass(frozen=True)
class DomainDiscoveryResult:
    domain: str
    homepage_status: str
    attempted_url: str | None
    final_url: str | None
    page_title: str | None
    candidate_urls_discovered: int
    candidate_urls_retained: int
    robots_status: str | None = None
    sitemap_status: str | None = None
    asset_static_urls_filtered: int = 0
    duplicate_urls_collapsed: int = 0
    homepage_variants_collapsed: int = 0
    query_urls_normalized: int = 0
    hard_negative_urls_filtered: int = 0
    soft_negative_urls_retained: int = 0
    robots_txt_succeeded: int = 0
    robots_txt_failed: int = 0
    sitemap_succeeded: int = 0
    sitemap_failed: int = 0
    sitemap_files_parsed: int = 0
    invalid_sitemap_xml_count: int = 0
    sitemap_urls_discovered: int = 0
    sitemap_urls_retained: int = 0
    redirected_domain: int = 0
    domains_using_alias_host: int = 0
    candidate_urls_retained_via_alias_host: int = 0
    error: str | None = None


def setup_logging(project_root: Path, run_id: str) -> logging.Logger:
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"discovery_{run_id}.log"

    logger = logging.getLogger(f"ai_ecosystem.discovery.{run_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.info("Started discovery run run_id=%s log_path=%s", run_id, log_path)
    return logger


def ensure_domain_discovery_status_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(domain_discovery_status);").fetchall()
    }
    if "discovery_run_id" not in columns:
        connection.execute("ALTER TABLE domain_discovery_status ADD COLUMN discovery_run_id TEXT")


def ensure_discovery_runs_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(discovery_runs);").fetchall()
    }
    migrations = {
        "max_workers": "ALTER TABLE discovery_runs ADD COLUMN max_workers INTEGER",
        "duration_seconds": "ALTER TABLE discovery_runs ADD COLUMN duration_seconds REAL",
        "domains_per_second": "ALTER TABLE discovery_runs ADD COLUMN domains_per_second REAL",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def format_duration_seconds(started_at: str, completed_at: str) -> str:
    started = datetime.fromisoformat(started_at)
    completed = datetime.fromisoformat(completed_at)
    duration_seconds = max((completed - started).total_seconds(), 0)
    return f"{duration_seconds:.2f}s"


def duration_seconds(started_at: str, completed_at: str) -> float:
    started = datetime.fromisoformat(started_at)
    completed = datetime.fromisoformat(completed_at)
    return round(max((completed - started).total_seconds(), 0), 2)


def domains_per_second(domains_attempted: int, duration: float) -> float:
    if domains_attempted <= 0 or duration <= 0:
        return 0.0
    return round(domains_attempted / duration, 4)


def classify_failure_category(error: str | None) -> str:
    if not error:
        return "unknown"
    lowered = error.lower()
    if "nameresolutionerror" in lowered or "failed to resolve" in lowered:
        return "dns_error"
    if "ssl" in lowered or "certificate" in lowered:
        return "ssl_error"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "too many redirects" in lowered or "redirect" in lowered:
        return "redirect_error"
    if "status=" in lowered or "404" in lowered or "403" in lowered or "500" in lowered:
        return "http_error"
    return "unknown"


def retained_count_stats(results: list[DomainDiscoveryResult]) -> dict[str, float | int]:
    counts = [result.candidate_urls_retained for result in results]
    if not counts:
        return {"average": 0, "median": 0, "min": 0, "max": 0}
    return {
        "average": round(statistics.mean(counts), 2),
        "median": statistics.median(counts),
        "min": min(counts),
        "max": max(counts),
    }


def write_run_summary_json(
    project_root: Path,
    run_id: str,
    started_at: str,
    completed_at: str,
    sample_input_csv: str | None,
    sample_id: str | None,
    max_workers: int,
    results: list[DomainDiscoveryResult],
    config: dict,
    log_path: Path,
) -> Path:
    discovery_config = config.get("discovery", {})
    retained_cap = int(discovery_config.get("max_retained_candidate_urls_per_domain", 20))
    domains_attempted = len(results)
    domains_succeeded = sum(1 for result in results if result.homepage_status == "success")
    domains_failed = sum(1 for result in results if result.homepage_status != "success")
    candidate_urls_discovered = sum(result.candidate_urls_discovered for result in results)
    candidate_urls_retained = sum(result.candidate_urls_retained for result in results)
    run_duration_seconds = duration_seconds(started_at, completed_at)
    asset_static_urls_filtered = sum(result.asset_static_urls_filtered for result in results)
    duplicate_urls_collapsed = sum(result.duplicate_urls_collapsed for result in results)
    homepage_variants_collapsed = sum(result.homepage_variants_collapsed for result in results)
    query_urls_normalized = sum(result.query_urls_normalized for result in results)
    hard_negative_urls_filtered = sum(result.hard_negative_urls_filtered for result in results)
    soft_negative_urls_retained = sum(result.soft_negative_urls_retained for result in results)
    robots_txt_successes = sum(result.robots_txt_succeeded for result in results)
    robots_txt_failures = sum(result.robots_txt_failed for result in results)
    sitemap_successes = sum(result.sitemap_succeeded for result in results)
    sitemap_failures = sum(result.sitemap_failed for result in results)
    sitemap_files_parsed = sum(result.sitemap_files_parsed for result in results)
    invalid_sitemap_xml_count = sum(result.invalid_sitemap_xml_count for result in results)
    sitemap_urls_discovered = sum(result.sitemap_urls_discovered for result in results)
    sitemap_urls_retained = sum(result.sitemap_urls_retained for result in results)
    redirected_domains_count = sum(result.redirected_domain for result in results)
    domains_using_alias_host = sum(result.domains_using_alias_host for result in results)
    candidate_urls_retained_via_alias_host = sum(
        result.candidate_urls_retained_via_alias_host for result in results
    )
    stats = retained_count_stats(results)
    failure_categories = Counter(
        classify_failure_category(result.error)
        for result in results
        if result.homepage_status != "success"
    )
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": run_duration_seconds,
        "max_workers": max_workers,
        "domains_per_second": domains_per_second(domains_attempted, run_duration_seconds),
        "sample_input_csv": sample_input_csv,
        "sample_id": sample_id,
        "domains_attempted": domains_attempted,
        "domains_succeeded": domains_succeeded,
        "domains_failed": domains_failed,
        "homepage_success_rate": round(domains_succeeded / domains_attempted * 100, 2)
        if domains_attempted
        else 0,
        "candidate_urls_discovered": candidate_urls_discovered,
        "candidate_urls_retained": candidate_urls_retained,
        "asset_static_urls_filtered": asset_static_urls_filtered,
        "duplicate_urls_collapsed": duplicate_urls_collapsed,
        "homepage_variants_collapsed": homepage_variants_collapsed,
        "query_urls_normalized": query_urls_normalized,
        "hard_negative_urls_filtered": hard_negative_urls_filtered,
        "soft_negative_urls_retained": soft_negative_urls_retained,
        "robots_txt_successes": robots_txt_successes,
        "robots_txt_failures": robots_txt_failures,
        "sitemap_successes": sitemap_successes,
        "sitemap_failures": sitemap_failures,
        "sitemap_files_parsed": sitemap_files_parsed,
        "invalid_sitemap_xml_count": invalid_sitemap_xml_count,
        "sitemap_urls_discovered": sitemap_urls_discovered,
        "sitemap_urls_retained": sitemap_urls_retained,
        "redirected_domains_count": redirected_domains_count,
        "domains_using_alias_host": domains_using_alias_host,
        "candidate_urls_retained_via_alias_host": candidate_urls_retained_via_alias_host,
        "average_retained_per_domain": stats["average"],
        "median_retained_per_domain": stats["median"],
        "min_retained_per_domain": stats["min"],
        "max_retained_per_domain": stats["max"],
        "retained_cap": retained_cap,
        "top_failure_categories": [
            {"category": category, "count": count}
            for category, count in failure_categories.most_common()
        ],
        "log_path": str(log_path),
        "config": {
            "timeout": int(discovery_config.get("request_timeout_seconds", 15)),
            "max_retained_urls": retained_cap,
            "max_workers": max_workers,
            "user_agent": discovery_config.get("user_agent", "AIAustriaResearchBot/0.1"),
        },
    }
    summary_path = project_root / "logs" / f"discovery_{run_id}_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    return summary_path


def extract_page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
        return title or None
    return None


def fetch_homepage(
    domain: str,
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
    retries: int = 1,
) -> FetchedHomepage:
    headers = {"User-Agent": user_agent}
    errors: list[str] = []
    for scheme in ("https", "http"):
        requested_url = f"{scheme}://{domain}"
        for attempt in range(retries + 1):
            try:
                response = requests.get(
                    requested_url,
                    headers=headers,
                    timeout=timeout_seconds,
                    allow_redirects=True,
                )
                logger.info(
                    "domain=%s homepage_fetch requested_url=%s final_url=%s status_code=%s attempt=%s",
                    domain,
                    requested_url,
                    response.url,
                    response.status_code,
                    attempt + 1,
                )
                if response.status_code < 400:
                    return FetchedHomepage(
                        requested_url=requested_url,
                        final_url=response.url,
                        status_code=response.status_code,
                        html=response.text,
                        page_title=extract_page_title(response.text),
                    )
                errors.append(f"{requested_url} status={response.status_code}")
            except requests.RequestException as exc:
                logger.warning(
                    "domain=%s homepage_fetch_error requested_url=%s attempt=%s error=%s",
                    domain,
                    requested_url,
                    attempt + 1,
                    exc,
                )
                errors.append(f"{requested_url} error={exc}")
    raise RuntimeError("; ".join(errors) if errors else "homepage fetch failed")


def fetch_text_url(
    url: str,
    domain: str,
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
    label: str,
) -> FetchedText:
    """Fetch a lightweight text/XML URL for discovery only."""
    headers = {"User-Agent": user_agent}
    response = requests.get(
        url,
        headers=headers,
        timeout=timeout_seconds,
        allow_redirects=True,
    )
    logger.info(
        "domain=%s %s_fetch requested_url=%s final_url=%s status_code=%s",
        domain,
        label,
        url,
        response.url,
        response.status_code,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{url} status={response.status_code}")
    return FetchedText(
        requested_url=url,
        final_url=response.url,
        status_code=response.status_code,
        text=response.text,
    )


def fetch_first_available_text(
    domain: str,
    paths: tuple[str, ...],
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
    label: str,
) -> FetchedText:
    """Try https then http for path-based discovery resources."""
    errors: list[str] = []
    for scheme in ("https", "http"):
        for path in paths:
            url = f"{scheme}://{domain}{path}"
            try:
                return fetch_text_url(url, domain, timeout_seconds, user_agent, logger, label)
            except requests.RequestException as exc:
                logger.warning(
                    "domain=%s %s_fetch_error requested_url=%s error=%s",
                    domain,
                    label,
                    url,
                    exc,
                )
                errors.append(f"{url} error={exc}")
            except RuntimeError as exc:
                logger.warning(
                    "domain=%s %s_fetch_error requested_url=%s error=%s",
                    domain,
                    label,
                    url,
                    exc,
                )
                errors.append(str(exc))
    raise RuntimeError("; ".join(errors) if errors else f"{label} fetch failed")


def should_discard_href(href: str) -> bool:
    stripped = href.strip()
    return (
        not stripped
        or stripped.startswith("#")
        or stripped.lower().startswith(DISALLOWED_HREF_PREFIXES)
    )


def compute_url_depth(normalized_url: str) -> int:
    path = urlsplit(normalized_url).path.strip("/")
    if not path:
        return 0
    return len([part for part in path.split("/") if part])


def normalized_host(value: str) -> str | None:
    """Return a normalized host for a domain or URL-like value."""
    if not value:
        return None
    if "://" in value:
        return extract_domain(value)
    return extract_domain(f"https://{value}")


def allowed_discovery_hosts(original_domain: str, final_homepage_url: str | None = None) -> set[str]:
    """Build equivalent hosts accepted for one domain discovery run."""
    hosts: set[str] = set()
    original_host = normalized_host(original_domain)
    if original_host:
        hosts.add(original_host)
    if final_homepage_url:
        final_host = normalized_host(final_homepage_url)
        if final_host:
            hosts.add(final_host)
    return hosts


def is_allowed_discovery_url(url: str, allowed_hosts: set[str]) -> bool:
    return any(is_internal_url(url, host) for host in allowed_hosts)


def is_alias_candidate_url(url: str, original_domain: str) -> bool:
    return not is_internal_url(url, original_domain)


def candidate_from_url(
    raw_url: str,
    domain: str,
    source_url: str,
    discovery_method: str,
    stats: dict[str, int] | None = None,
    allowed_hosts: set[str] | None = None,
) -> CandidateUrl | None:
    raw_query = urlsplit(raw_url).query
    cleaned_url = remove_tracking_parameters(raw_url)
    normalized_url = normalize_url(cleaned_url)
    active_allowed_hosts = allowed_hosts or allowed_discovery_hosts(domain)
    if not normalized_url or not is_allowed_discovery_url(normalized_url, active_allowed_hosts):
        return None
    if stats is not None and is_alias_candidate_url(normalized_url, domain):
        stats["alias_candidate_urls_accepted"] = stats.get("alias_candidate_urls_accepted", 0) + 1
    if stats is not None and raw_query and raw_query != urlsplit(normalized_url).query:
        stats["query_urls_normalized"] = stats.get("query_urls_normalized", 0) + 1
    if is_asset_or_static_url(normalized_url):
        if stats is not None:
            stats["asset_static_urls_filtered"] = stats.get("asset_static_urls_filtered", 0) + 1
        return None
    if is_hard_negative_url(normalized_url):
        if stats is not None:
            stats["hard_negative_urls_filtered"] = stats.get("hard_negative_urls_filtered", 0) + 1
        return None

    page_type_guess = classify_page_type(normalized_url)
    url_depth = compute_url_depth(normalized_url)
    return CandidateUrl(
        url=normalized_url,
        normalized_url=normalized_url,
        source_domain=extract_domain(source_url) or domain,
        discovered_from_url=normalize_url(source_url) or source_url,
        discovery_method=discovery_method,
        page_type_guess=page_type_guess,
        url_depth=url_depth,
        priority=score_candidate_page(page_type_guess, url_depth),
    )


def extract_internal_candidate_urls(
    html: str,
    base_url: str,
    domain: str,
    limit: int | None = None,
    stats: dict[str, int] | None = None,
    allowed_hosts: set[str] | None = None,
) -> list[CandidateUrl]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: dict[str, CandidateUrl] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if should_discard_href(href):
            continue

        absolute_url = urljoin(base_url, href.strip())
        candidate = candidate_from_url(
            absolute_url,
            domain,
            base_url,
            "homepage",
            stats,
            allowed_hosts=allowed_hosts,
        )
        if candidate is None:
            continue
        normalized_url = candidate.normalized_url
        if normalized_url in candidates and stats is not None:
            stats["duplicate_urls_collapsed"] = stats.get("duplicate_urls_collapsed", 0) + 1
            if compute_url_depth(normalized_url) == 0:
                stats["homepage_variants_collapsed"] = stats.get("homepage_variants_collapsed", 0) + 1

        candidates[normalized_url] = candidate
        if limit is not None and len(candidates) >= limit:
            break

    return list(candidates.values())


def select_domains(connection: sqlite3.Connection, limit: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT domain
        FROM domains
        WHERE COALESCE(is_excluded, 0) = 0
          AND domain IS NOT NULL
          AND domain GLOB '[A-Za-z0-9]*'
        ORDER BY
          firm_count DESC,
          CASE representation_level
            WHEN 'single_firm' THEN 0
            WHEN 'shared_domain' THEN 1
            WHEN 'shared_group_or_brand' THEN 2
            ELSE 3
          END,
          domain
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row[0] for row in rows]


def read_domains_from_csv(path: Path, limit: int | None = None) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if "domain" not in (reader.fieldnames or []):
            raise ValueError(f"CSV input must contain a 'domain' column: {path}")
        domains: list[str] = []
        seen: set[str] = set()
        for row in reader:
            domain = (row.get("domain") or "").strip().lower()
            if not domain or domain in seen:
                continue
            seen.add(domain)
            domains.append(domain)
            if limit is not None and len(domains) >= limit:
                break
        return domains


def delete_discovery_candidate_pages_for_domains(
    connection: sqlite3.Connection,
    domains: list[str],
) -> int:
    """Delete refreshable discovery candidates for a fixed pilot domain set."""
    if not domains:
        return 0

    deleted = 0
    chunk_size = 900
    for start in range(0, len(domains), chunk_size):
        chunk = domains[start : start + chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = connection.execute(
            f"""
            DELETE FROM candidate_pages
            WHERE discovery_method IN ({", ".join("?" for _ in DISCOVERY_METHODS_TO_REFRESH)})
              AND domain IN ({placeholders})
            """,
            (*DISCOVERY_METHODS_TO_REFRESH, *chunk),
        )
        deleted += cursor.rowcount if cursor.rowcount is not None else 0
    return deleted


delete_homepage_candidate_pages_for_domains = delete_discovery_candidate_pages_for_domains


def upsert_candidate_pages(
    connection: sqlite3.Connection,
    domain: str,
    page_title: str | None,
    run_id: str,
    discovered_at: str,
    candidates: Iterable[CandidateUrl],
) -> int:
    retained = 0
    for candidate in candidates:
        connection.execute(
            """
            INSERT INTO candidate_pages (
                domain,
                url,
                normalized_url,
                source_domain,
                discovered_from_url,
                discovery_method,
                page_title,
                page_type_guess,
                url_depth,
                last_discovered_at,
                discovery_run_id,
                priority,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(domain, normalized_url) DO UPDATE SET
                url = excluded.url,
                source_domain = excluded.source_domain,
                discovered_from_url = excluded.discovered_from_url,
                discovery_method = CASE
                    WHEN candidate_pages.discovery_method = 'homepage'
                         AND excluded.discovery_method IN ('robots_txt', 'sitemap')
                    THEN candidate_pages.discovery_method
                    ELSE excluded.discovery_method
                END,
                page_title = excluded.page_title,
                page_type_guess = excluded.page_type_guess,
                url_depth = excluded.url_depth,
                last_discovered_at = excluded.last_discovered_at,
                discovery_run_id = excluded.discovery_run_id,
                priority = excluded.priority,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                domain,
                candidate.url,
                candidate.normalized_url,
                candidate.source_domain,
                candidate.discovered_from_url,
                candidate.discovery_method,
                page_title,
                candidate.page_type_guess,
                candidate.url_depth,
                discovered_at,
                run_id,
                candidate.priority,
            ),
        )
        retained += 1
    return retained


def discover_robots_txt(
    domain: str,
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
) -> tuple[str | None, list[str], str | None]:
    try:
        fetched = fetch_first_available_text(
            domain,
            ("/robots.txt",),
            timeout_seconds,
            user_agent,
            logger,
            "robots_txt",
        )
    except Exception as exc:
        logger.info("domain=%s robots_txt_failed error=%s", domain, exc)
        return None, [], str(exc)

    sitemap_urls = extract_sitemap_urls_from_robots(fetched.text, fetched.final_url)
    logger.info(
        "domain=%s robots_txt_success final_url=%s sitemap_refs=%s",
        domain,
        fetched.final_url,
        len(sitemap_urls),
    )
    return fetched.final_url, sitemap_urls, None


def discover_robots_txt_for_hosts(
    original_domain: str,
    hosts: set[str],
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
) -> tuple[str | None, list[str], str | None, int, int]:
    """Try robots.txt for the original domain and any canonical alias hosts."""
    ordered_hosts = [
        host
        for host in [normalized_host(original_domain), *sorted(hosts)]
        if host
    ]
    seen: set[str] = set()
    sitemap_urls: list[str] = []
    final_robots_url: str | None = None
    errors: list[str] = []
    succeeded = 0
    failed = 0
    for host in ordered_hosts:
        if host in seen:
            continue
        seen.add(host)
        robots_url, host_sitemap_urls, error = discover_robots_txt(
            host,
            timeout_seconds,
            user_agent,
            logger,
        )
        if error:
            failed += 1
            errors.append(f"{host}={error}")
            continue
        succeeded += 1
        final_robots_url = final_robots_url or robots_url
        sitemap_urls.extend(host_sitemap_urls)
    if succeeded:
        return final_robots_url, sitemap_urls, None, succeeded, failed
    return None, [], "; ".join(errors) if errors else "robots_txt fetch failed", succeeded, failed


def seed_sitemap_urls(domain: str, robots_sitemap_urls: list[str], hosts: set[str] | None = None) -> list[str]:
    seed_hosts = hosts or allowed_discovery_hosts(domain)
    seeds = []
    for host in sorted(seed_hosts):
        seeds.extend(
            [
                f"https://{host}/sitemap.xml",
                f"https://{host}/sitemap_index.xml",
            ]
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for url in [*robots_sitemap_urls, *seeds]:
        key = url.strip().rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(url.strip())
    return ordered


def discover_sitemap_candidates(
    domain: str,
    sitemap_urls: list[str],
    timeout_seconds: int,
    user_agent: str,
    logger: logging.Logger,
    config: dict,
    stats: dict[str, int],
    allowed_hosts: set[str] | None = None,
) -> list[CandidateUrl]:
    discovery_config = config.get("discovery", {})
    max_sitemap_files = int(discovery_config.get("sitemap_url_limit", 20))
    max_sitemap_urls = int(discovery_config.get("max_sitemap_urls_per_domain", 1000))
    sitemap_timeout_seconds = min(timeout_seconds, int(discovery_config.get("sitemap_timeout_seconds", 5)))
    queue: list[tuple[str, int]] = [(url, 0) for url in sitemap_urls]
    seen_sitemaps: set[str] = set()
    candidates: dict[str, CandidateUrl] = {}
    active_allowed_hosts = allowed_hosts or allowed_discovery_hosts(domain)
    max_depth = 3

    while queue and len(seen_sitemaps) < max_sitemap_files and stats.get("sitemap_urls_discovered", 0) < max_sitemap_urls:
        sitemap_url, depth = queue.pop(0)
        normalized_sitemap_url = normalize_url(sitemap_url)
        sitemap_key = sitemap_url.strip().rstrip("/").lower()
        if not normalized_sitemap_url or sitemap_key in seen_sitemaps:
            continue
        if not is_allowed_discovery_url(normalized_sitemap_url, active_allowed_hosts):
            continue
        seen_sitemaps.add(sitemap_key)

        try:
            fetched = fetch_text_url(
                sitemap_url,
                domain,
                sitemap_timeout_seconds,
                user_agent,
                logger,
                "sitemap",
            )
        except Exception as exc:
            stats["sitemap_failures"] = stats.get("sitemap_failures", 0) + 1
            logger.info("domain=%s sitemap_failed sitemap_url=%s error=%s", domain, normalized_sitemap_url, exc)
            continue

        parsed = parse_sitemap_xml(fetched.text)
        if not parsed.is_valid_xml:
            stats["invalid_sitemap_xml_count"] = stats.get("invalid_sitemap_xml_count", 0) + 1
            logger.info("domain=%s sitemap_invalid_xml sitemap_url=%s", domain, fetched.final_url)
            continue

        stats["sitemap_successes"] = stats.get("sitemap_successes", 0) + 1
        stats["sitemap_files_parsed"] = stats.get("sitemap_files_parsed", 0) + 1

        if depth < max_depth:
            for child_sitemap_url in parsed.sitemap_urls:
                child_key = child_sitemap_url.strip().rstrip("/").lower()
                if child_key and child_key not in seen_sitemaps:
                    queue.append((child_sitemap_url, depth + 1))

        for raw_url in parsed.urls:
            if stats.get("sitemap_urls_discovered", 0) >= max_sitemap_urls:
                break
            stats["sitemap_urls_discovered"] = stats.get("sitemap_urls_discovered", 0) + 1
            candidate = candidate_from_url(
                raw_url,
                domain,
                fetched.final_url,
                "sitemap",
                stats,
                allowed_hosts=active_allowed_hosts,
            )
            if candidate is None:
                continue
            if candidate.normalized_url in candidates:
                stats["duplicate_urls_collapsed"] = stats.get("duplicate_urls_collapsed", 0) + 1
                continue
            candidates[candidate.normalized_url] = candidate

    return list(candidates.values())


def update_domain_status(
    connection: sqlite3.Connection,
    domain: str,
    homepage_status: str,
    attempted_at: str,
    run_id: str,
    successful_at: str | None = None,
    error: str | None = None,
    robots_status: str | None = None,
    sitemap_status: str | None = None,
) -> None:
    ensure_domain_discovery_status_schema(connection)
    connection.execute(
        """
        INSERT INTO domain_discovery_status (
            domain,
            homepage_status,
            last_discovery_attempt,
            last_successful_discovery,
            discovery_run_id,
            robots_status,
            sitemap_status,
            discovery_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            homepage_status = excluded.homepage_status,
            robots_status = COALESCE(excluded.robots_status, domain_discovery_status.robots_status),
            sitemap_status = COALESCE(excluded.sitemap_status, domain_discovery_status.sitemap_status),
            last_discovery_attempt = excluded.last_discovery_attempt,
            last_successful_discovery = COALESCE(
                excluded.last_successful_discovery,
                domain_discovery_status.last_successful_discovery
            ),
            discovery_run_id = excluded.discovery_run_id,
            discovery_error = excluded.discovery_error
        """,
        (domain, homepage_status, attempted_at, successful_at, run_id, robots_status, sitemap_status, error),
    )


def discover_domain_homepage(
    connection: sqlite3.Connection,
    domain: str,
    run_id: str,
    config: dict,
    logger: logging.Logger,
) -> DomainDiscoveryResult:
    discovery_config = config.get("discovery", {})
    timeout_seconds = int(discovery_config.get("request_timeout_seconds", 15))
    user_agent = discovery_config.get("user_agent", "AIAustriaResearchBot/0.1")
    max_discovered = int(discovery_config.get("max_discovered_urls_per_domain", 200))
    max_retained = int(discovery_config.get("max_retained_candidate_urls_per_domain", 20))
    attempted_at = utc_now_iso()
    extraction_stats: dict[str, int] = {}
    homepage_status = "failed"
    robots_status = "failed"
    sitemap_status = "failed"
    attempted_url: str | None = None
    final_url: str | None = None
    page_title: str | None = None
    error_messages: list[str] = []
    homepage_candidates: list[CandidateUrl] = []
    active_allowed_hosts = allowed_discovery_hosts(domain)
    redirected_domain = 0

    try:
        fetched = fetch_homepage(domain, timeout_seconds, user_agent, logger)
        homepage_status = "success"
        attempted_url = fetched.requested_url
        final_url = fetched.final_url
        page_title = fetched.page_title
        active_allowed_hosts = allowed_discovery_hosts(domain, fetched.final_url)
        redirected_domain = int((normalized_host(domain) or "") != (normalized_host(fetched.final_url) or ""))
        homepage_candidates = extract_internal_candidate_urls(
            fetched.html,
            fetched.final_url,
            domain,
            limit=max_discovered,
            stats=extraction_stats,
            allowed_hosts=active_allowed_hosts,
        )
    except Exception as exc:
        error = str(exc)
        error_messages.append(f"homepage={error}")
        logger.exception("domain=%s homepage_failed error=%s", domain, error)

    robots_url, robots_sitemap_urls, robots_error, robots_success_count, robots_failure_count = discover_robots_txt_for_hosts(
        domain,
        active_allowed_hosts,
        timeout_seconds,
        user_agent,
        logger,
    )
    if robots_error:
        extraction_stats["robots_txt_failed"] = robots_failure_count or 1
        error_messages.append(f"robots_txt={robots_error}")
    else:
        robots_status = "success"
        extraction_stats["robots_txt_succeeded"] = robots_success_count or 1
        if robots_failure_count:
            extraction_stats["robots_txt_failed"] = robots_failure_count

    sitemap_seed_urls = seed_sitemap_urls(domain, robots_sitemap_urls, active_allowed_hosts)
    sitemap_candidates = discover_sitemap_candidates(
        domain,
        sitemap_seed_urls,
        timeout_seconds,
        user_agent,
        logger,
        config,
        extraction_stats,
        allowed_hosts=active_allowed_hosts,
    )
    if extraction_stats.get("sitemap_successes", 0) > 0:
        sitemap_status = "success"
    elif sitemap_seed_urls:
        sitemap_status = "failed"

    retained_homepage_candidates = homepage_candidates[:max_retained]
    retained_sitemap_candidates = [
        candidate
        for candidate in sitemap_candidates
        if candidate.normalized_url not in {item.normalized_url for item in retained_homepage_candidates}
    ][:max_retained]
    retained_candidates = [*retained_homepage_candidates, *retained_sitemap_candidates]
    retained = upsert_candidate_pages(
        connection,
        domain,
        page_title,
        run_id,
        attempted_at,
        retained_candidates,
    )
    successful_at = attempted_at if homepage_status == "success" else None
    update_domain_status(
        connection,
        domain,
        homepage_status,
        attempted_at,
        run_id,
        successful_at=successful_at,
        error="; ".join(error_messages) if error_messages else None,
        robots_status=robots_status,
        sitemap_status=sitemap_status,
    )

    candidate_urls_discovered = len(homepage_candidates) + len(sitemap_candidates)
    asset_static_urls_filtered = extraction_stats.get("asset_static_urls_filtered", 0)
    duplicate_urls_collapsed = extraction_stats.get("duplicate_urls_collapsed", 0)
    homepage_variants_collapsed = extraction_stats.get("homepage_variants_collapsed", 0)
    query_urls_normalized = extraction_stats.get("query_urls_normalized", 0)
    hard_negative_urls_filtered = extraction_stats.get("hard_negative_urls_filtered", 0)
    soft_negative_urls_retained = sum(
        1 for candidate in retained_candidates if candidate.page_type_guess == "negative"
    )
    sitemap_urls_retained = len(retained_sitemap_candidates)
    candidate_urls_retained_via_alias_host = sum(
        1 for candidate in retained_candidates if is_alias_candidate_url(candidate.normalized_url, domain)
    )
    domains_using_alias_host = int(candidate_urls_retained_via_alias_host > 0)
    logger.info(
        "domain=%s discovery_done homepage_status=%s robots_status=%s sitemap_status=%s final_url=%s allowed_hosts=%s redirected_domain=%s discovered=%s retained=%s retained_via_alias=%s sitemap_retained=%s asset_static_filtered=%s hard_negative_filtered=%s soft_negative_retained=%s duplicates_collapsed=%s homepage_variants_collapsed=%s query_urls_normalized=%s sitemap_files_parsed=%s invalid_sitemap_xml=%s",
        domain,
        homepage_status,
        robots_status,
        sitemap_status,
        final_url,
        ",".join(sorted(active_allowed_hosts)),
        redirected_domain,
        candidate_urls_discovered,
        retained,
        candidate_urls_retained_via_alias_host,
        sitemap_urls_retained,
        asset_static_urls_filtered,
        hard_negative_urls_filtered,
        soft_negative_urls_retained,
        duplicate_urls_collapsed,
        homepage_variants_collapsed,
        query_urls_normalized,
        extraction_stats.get("sitemap_files_parsed", 0),
        extraction_stats.get("invalid_sitemap_xml_count", 0),
    )
    return DomainDiscoveryResult(
        domain=domain,
        homepage_status=homepage_status,
        attempted_url=attempted_url,
        final_url=final_url,
        page_title=page_title,
        candidate_urls_discovered=candidate_urls_discovered,
        candidate_urls_retained=retained,
        robots_status=robots_status,
        sitemap_status=sitemap_status,
        asset_static_urls_filtered=asset_static_urls_filtered,
        duplicate_urls_collapsed=duplicate_urls_collapsed,
        homepage_variants_collapsed=homepage_variants_collapsed,
        query_urls_normalized=query_urls_normalized,
        hard_negative_urls_filtered=hard_negative_urls_filtered,
        soft_negative_urls_retained=soft_negative_urls_retained,
        robots_txt_succeeded=extraction_stats.get("robots_txt_succeeded", 0),
        robots_txt_failed=extraction_stats.get("robots_txt_failed", 0),
        sitemap_succeeded=extraction_stats.get("sitemap_successes", 0),
        sitemap_failed=extraction_stats.get("sitemap_failures", 0),
        sitemap_files_parsed=extraction_stats.get("sitemap_files_parsed", 0),
        invalid_sitemap_xml_count=extraction_stats.get("invalid_sitemap_xml_count", 0),
        sitemap_urls_discovered=extraction_stats.get("sitemap_urls_discovered", 0),
        sitemap_urls_retained=sitemap_urls_retained,
        redirected_domain=redirected_domain,
        domains_using_alias_host=domains_using_alias_host,
        candidate_urls_retained_via_alias_host=candidate_urls_retained_via_alias_host,
        error="; ".join(error_messages) if error_messages else None,
    )


def discover_domain_homepage_with_new_connection(
    db_path: Path,
    domain: str,
    run_id: str,
    config: dict,
    logger: logging.Logger,
) -> DomainDiscoveryResult:
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_domain_discovery_status_schema(connection)
        return discover_domain_homepage(connection, domain, run_id, config, logger)


def failed_domain_result(domain: str, error: Exception) -> DomainDiscoveryResult:
    return DomainDiscoveryResult(
        domain=domain,
        homepage_status="failed",
        attempted_url=None,
        final_url=None,
        page_title=None,
        candidate_urls_discovered=0,
        candidate_urls_retained=0,
        robots_status="failed",
        sitemap_status="failed",
        error=str(error),
    )


def run_homepage_discovery(
    limit: int | None = None,
    input_csv: str | Path | None = None,
    sample_id: str | None = None,
    max_workers: int | None = None,
) -> str:
    if input_csv and sample_id:
        raise ValueError("Use either input_csv or sample_id, not both.")

    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    discovery_config = config.get("discovery", {})
    domain_limit = limit or int(discovery_config.get("pilot_domain_limit", 100))
    worker_count = max(1, int(max_workers or discovery_config.get("max_workers", 1)))
    run_id = generate_discovery_run_id("homepage")
    logger = setup_logging(project_root, run_id)
    log_path = project_root / "logs" / f"discovery_{run_id}.log"
    started_at = utc_now_iso()
    sample_input_csv: str | None = None

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        ensure_domain_discovery_status_schema(connection)
        ensure_discovery_runs_schema(connection)
        if input_csv:
            csv_path = resolve_project_path(project_root, str(input_csv))
            sample_input_csv = str(csv_path)
            domains = read_domains_from_csv(csv_path, limit=limit)
            notes = f"homepage discovery pilot from csv={csv_path}"
        elif sample_id:
            domains = get_sample_domains(connection, sample_id)
            if limit is not None:
                domains = domains[:limit]
            notes = f"homepage discovery pilot from sample_id={sample_id}"
        else:
            domains = select_domains(connection, domain_limit)
            notes = "homepage discovery pilot"

        if input_csv or sample_id:
            deleted_candidates = delete_discovery_candidate_pages_for_domains(connection, domains)
            logger.info(
                "pilot_refresh_cleanup deleted_candidate_pages=%s domains=%s",
                deleted_candidates,
                ", ".join(domains),
            )

        connection.execute(
            """
            INSERT INTO discovery_runs (
                run_id,
                started_at,
                domains_attempted,
                domains_succeeded,
                domains_failed,
                candidate_urls_discovered,
                candidate_urls_retained,
                max_workers,
                notes
            )
            VALUES (?, ?, 0, 0, 0, 0, 0, ?, ?)
            """,
            (run_id, started_at, worker_count, notes),
        )
        connection.commit()

        results: list[DomainDiscoveryResult] = []
        print(f"Discovery workers: {worker_count}")
        for index, domain in enumerate(domains, start=1):
            if worker_count != 1:
                break
            print(f"[{index}/{len(domains)}] Processing domain: {domain}")
            logger.info("domain=%s progress index=%s total=%s", domain, index, len(domains))
            try:
                result = discover_domain_homepage(connection, domain, run_id, config, logger)
            except Exception as exc:
                logger.exception("domain=%s discovery_unhandled_error error=%s", domain, exc)
                result = failed_domain_result(domain, exc)
            results.append(result)
            print(f"  Homepage fetch: {result.homepage_status}")
            print(f"  Internal URLs discovered: {result.candidate_urls_discovered}")
            print(f"  Sitemap URLs retained: {result.sitemap_urls_retained}")

        if worker_count > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_domain = {
                    executor.submit(
                        discover_domain_homepage_with_new_connection,
                        db_path,
                        domain,
                        run_id,
                        config,
                        logger,
                    ): domain
                    for domain in domains
                }
                for completed_index, future in enumerate(as_completed(future_to_domain), start=1):
                    domain = future_to_domain[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.exception("domain=%s discovery_unhandled_error error=%s", domain, exc)
                        result = failed_domain_result(domain, exc)
                    results.append(result)
                    print(f"[{completed_index}/{len(domains)}] Completed domain: {domain}")
                    print(f"  Homepage fetch: {result.homepage_status}")
                    print(f"  Internal URLs discovered: {result.candidate_urls_discovered}")
                    print(f"  Sitemap URLs retained: {result.sitemap_urls_retained}")

        succeeded = sum(1 for result in results if result.homepage_status == "success")
        failed = sum(1 for result in results if result.homepage_status != "success")
        discovered_total = sum(result.candidate_urls_discovered for result in results)
        retained_total = sum(result.candidate_urls_retained for result in results)
        filtered_asset_static_total = sum(result.asset_static_urls_filtered for result in results)
        duplicate_urls_collapsed_total = sum(result.duplicate_urls_collapsed for result in results)
        homepage_variants_collapsed_total = sum(result.homepage_variants_collapsed for result in results)
        query_urls_normalized_total = sum(result.query_urls_normalized for result in results)
        hard_negative_urls_filtered_total = sum(result.hard_negative_urls_filtered for result in results)
        soft_negative_urls_retained_total = sum(result.soft_negative_urls_retained for result in results)
        robots_txt_succeeded_total = sum(result.robots_txt_succeeded for result in results)
        robots_txt_failed_total = sum(result.robots_txt_failed for result in results)
        sitemap_succeeded_total = sum(result.sitemap_succeeded for result in results)
        sitemap_failed_total = sum(result.sitemap_failed for result in results)
        sitemap_files_parsed_total = sum(result.sitemap_files_parsed for result in results)
        invalid_sitemap_xml_total = sum(result.invalid_sitemap_xml_count for result in results)
        sitemap_urls_discovered_total = sum(result.sitemap_urls_discovered for result in results)
        sitemap_urls_retained_total = sum(result.sitemap_urls_retained for result in results)
        redirected_domains_total = sum(result.redirected_domain for result in results)
        domains_using_alias_host_total = sum(result.domains_using_alias_host for result in results)
        candidate_urls_retained_via_alias_host_total = sum(
            result.candidate_urls_retained_via_alias_host for result in results
        )

        completed_at = utc_now_iso()
        run_duration_seconds = duration_seconds(started_at, completed_at)
        run_domains_per_second = domains_per_second(len(domains), run_duration_seconds)
        connection.execute(
            """
            UPDATE discovery_runs
            SET
                completed_at = ?,
                domains_attempted = ?,
                domains_succeeded = ?,
                domains_failed = ?,
                candidate_urls_discovered = ?,
                candidate_urls_retained = ?,
                max_workers = ?,
                duration_seconds = ?,
                domains_per_second = ?
            WHERE run_id = ?
            """,
            (
                completed_at,
                len(domains),
                succeeded,
                failed,
                discovered_total,
                retained_total,
                worker_count,
                run_duration_seconds,
                run_domains_per_second,
                run_id,
            ),
        )

    logger.info(
        "completed run_id=%s domains=%s succeeded=%s failed=%s discovered=%s retained=%s robots_success=%s robots_failed=%s sitemap_success=%s sitemap_failed=%s sitemap_files_parsed=%s sitemap_urls_discovered=%s sitemap_urls_retained=%s redirected_domains=%s domains_using_alias_host=%s retained_via_alias_host=%s invalid_sitemap_xml=%s asset_static_filtered=%s hard_negative_filtered=%s soft_negative_retained=%s duplicates_collapsed=%s homepage_variants_collapsed=%s query_urls_normalized=%s duration=%s",
        run_id,
        len(domains),
        succeeded,
        failed,
        discovered_total,
        retained_total,
        robots_txt_succeeded_total,
        robots_txt_failed_total,
        sitemap_succeeded_total,
        sitemap_failed_total,
        sitemap_files_parsed_total,
        sitemap_urls_discovered_total,
        sitemap_urls_retained_total,
        redirected_domains_total,
        domains_using_alias_host_total,
        candidate_urls_retained_via_alias_host_total,
        invalid_sitemap_xml_total,
        filtered_asset_static_total,
        hard_negative_urls_filtered_total,
        soft_negative_urls_retained_total,
        duplicate_urls_collapsed_total,
        homepage_variants_collapsed_total,
        query_urls_normalized_total,
        format_duration_seconds(started_at, completed_at),
    )
    summary_path: Path | None = None
    try:
        summary_path = write_run_summary_json(
            project_root=project_root,
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            sample_input_csv=sample_input_csv,
            sample_id=sample_id,
            max_workers=worker_count,
            results=results,
            config=config,
            log_path=log_path,
        )
        logger.info("summary_written run_id=%s summary_path=%s", run_id, summary_path)
    except Exception as exc:
        logger.exception("summary_write_failed run_id=%s error=%s", run_id, exc)
    print("Homepage discovery summary")
    print(f"  Run ID: {run_id}")
    print(f"  Workers: {worker_count}")
    print(f"  Domains attempted: {len(domains)}")
    print(f"  Domains succeeded: {succeeded}")
    print(f"  Domains failed: {failed}")
    print(f"  Candidate URLs discovered: {discovered_total}")
    print(f"  Candidate URLs retained: {retained_total}")
    print(f"  robots.txt successes: {robots_txt_succeeded_total}")
    print(f"  robots.txt failures: {robots_txt_failed_total}")
    print(f"  Sitemap fetch successes: {sitemap_succeeded_total}")
    print(f"  Sitemap fetch failures: {sitemap_failed_total}")
    print(f"  Sitemap files parsed: {sitemap_files_parsed_total}")
    print(f"  Sitemap URLs discovered: {sitemap_urls_discovered_total}")
    print(f"  Sitemap URLs retained: {sitemap_urls_retained_total}")
    print(f"  Redirected domains: {redirected_domains_total}")
    print(f"  Domains using alias host: {domains_using_alias_host_total}")
    print(f"  Candidate URLs retained via alias host: {candidate_urls_retained_via_alias_host_total}")
    print(f"  Invalid sitemap XML count: {invalid_sitemap_xml_total}")
    print(f"  Asset/static URLs filtered: {filtered_asset_static_total}")
    print(f"  Hard-negative URLs filtered: {hard_negative_urls_filtered_total}")
    print(f"  Soft-negative URLs retained: {soft_negative_urls_retained_total}")
    print(f"  Duplicate URLs collapsed: {duplicate_urls_collapsed_total}")
    print(f"  Homepage variants collapsed: {homepage_variants_collapsed_total}")
    print(f"  Empty/tracking query URLs normalized: {query_urls_normalized_total}")
    print(f"  Duration: {format_duration_seconds(started_at, completed_at)}")
    print(f"  Domains/s: {run_domains_per_second}")
    if summary_path:
        print(f"  Summary JSON: {summary_path}")
    print(f"  Success rate: {round(succeeded / len(domains) * 100, 2) if domains else 0}%")
    return run_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run homepage URL discovery for a small domain batch.")
    parser.add_argument("--limit", type=int, help="Maximum number of domains to process.")
    parser.add_argument(
        "--input-csv",
        help="Optional fixed pilot CSV with a domain column.",
    )
    parser.add_argument(
        "--sample-id",
        help="Optional registered sample_id from sample_domains.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        help="Number of domains to process concurrently.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = run_homepage_discovery(
        limit=args.limit,
        input_csv=args.input_csv,
        sample_id=args.sample_id,
        max_workers=args.max_workers,
    )
    print(f"Discovery run complete: {run_id}")


if __name__ == "__main__":
    main()
