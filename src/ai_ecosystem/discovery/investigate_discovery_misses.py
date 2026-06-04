"""Investigate sample domains with zero retained discovery candidates.

This module is diagnostic only. It does not write to SQLite and does not change
discovery, filtering, classification, or retention behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path
from ai_ecosystem.discovery.discovery_utils import get_sample_domains, utc_now_iso
from ai_ecosystem.discovery.homepage_discovery import (
    allowed_discovery_hosts,
    candidate_from_url,
    classify_failure_category,
    discover_robots_txt_for_hosts,
    discover_sitemap_candidates,
    fetch_homepage,
    seed_sitemap_urls,
    should_discard_href,
)
from ai_ecosystem.discovery.normalization import (
    is_internal_url,
    normalize_url,
    remove_tracking_parameters,
)


@dataclass
class MissInvestigationRow:
    domain: str
    homepage_status: str | None
    robots_status: str | None
    sitemap_status: str | None
    homepage_candidates_discovered: int
    homepage_candidates_retained: int
    sitemap_urls_discovered: int
    sitemap_urls_retained: int
    robots_sitemap_references_count: int
    homepage_internal_link_count: int
    links_before_filtering: int
    asset_static_filtered: int
    hard_negative_filtered: int
    duplicate_collapsed: int
    homepage_canonicalization_collapsed: int
    sitemap_url_count_before_filtering: int
    failure_category: str
    diagnosis_category: str
    evidence: str


def quiet_logger() -> logging.Logger:
    logger = logging.getLogger("ai_ecosystem.discovery.miss_investigation")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def quote_placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def zero_retained_domains(connection: sqlite3.Connection, sample_id: str) -> list[str]:
    domains = get_sample_domains(connection, sample_id)
    if not domains:
        raise ValueError(f"No domains found for sample_id: {sample_id}")
    rows = connection.execute(
        f"""
        SELECT sd.domain
        FROM sample_domains AS sd
        LEFT JOIN candidate_pages AS cp
          ON cp.domain = sd.domain
        WHERE sd.sample_id = ?
          AND sd.domain IN ({quote_placeholders(domains)})
        GROUP BY sd.domain
        HAVING COUNT(cp.id) = 0
        ORDER BY sd.sample_rank IS NULL, sd.sample_rank, sd.domain
        """,
        (sample_id, *domains),
    ).fetchall()
    return [row["domain"] for row in rows]


def load_status_rows(connection: sqlite3.Connection, domains: list[str]) -> dict[str, sqlite3.Row]:
    if not domains:
        return {}
    rows = connection.execute(
        f"""
        SELECT domain, homepage_status, robots_status, sitemap_status, discovery_error
        FROM domain_discovery_status
        WHERE domain IN ({quote_placeholders(domains)})
        """,
        tuple(domains),
    ).fetchall()
    return {row["domain"]: row for row in rows}


def analyze_homepage_links(
    html: str,
    base_url: str,
    domain: str,
    max_retained: int,
    allowed_hosts: set[str],
) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    stats: dict[str, int] = {}
    candidates = {}
    links_before_filtering = 0
    homepage_internal_link_count = 0

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if should_discard_href(href):
            continue
        links_before_filtering += 1
        absolute_url = urljoin(base_url, href.strip())
        cleaned_url = remove_tracking_parameters(absolute_url)
        normalized_url = normalize_url(cleaned_url)
        if normalized_url and any(is_internal_url(normalized_url, host) for host in allowed_hosts):
            homepage_internal_link_count += 1

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
        if candidate.normalized_url in candidates:
            stats["duplicate_urls_collapsed"] = stats.get("duplicate_urls_collapsed", 0) + 1
            if not urlsplit(candidate.normalized_url).path.strip("/"):
                stats["homepage_variants_collapsed"] = stats.get("homepage_variants_collapsed", 0) + 1
        candidates[candidate.normalized_url] = candidate

    return {
        "links_before_filtering": links_before_filtering,
        "homepage_internal_link_count": homepage_internal_link_count,
        "homepage_candidates_discovered": len(candidates),
        "homepage_candidates_retained": min(len(candidates), max_retained),
        "asset_static_filtered": stats.get("asset_static_urls_filtered", 0),
        "hard_negative_filtered": stats.get("hard_negative_urls_filtered", 0),
        "duplicate_collapsed": stats.get("duplicate_urls_collapsed", 0),
        "homepage_canonicalization_collapsed": stats.get("homepage_variants_collapsed", 0),
    }


def map_failure_category(error: str | None) -> str:
    category = classify_failure_category(error)
    return {
        "dns_error": "dns_failure",
        "ssl_error": "ssl_failure",
        "timeout": "timeout",
    }.get(category, "unknown")


def classify_diagnosis(
    failure_category: str,
    homepage_status: str | None,
    links_before_filtering: int,
    homepage_internal_link_count: int,
    homepage_candidates_discovered: int,
    sitemap_url_count_before_filtering: int,
    sitemap_urls_retained: int,
    filtered_total: int,
) -> str:
    if failure_category in {"dns_failure", "ssl_failure", "timeout"}:
        return "infrastructure_failure"
    if (
        homepage_status == "success"
        and links_before_filtering == 0
        and sitemap_url_count_before_filtering == 0
    ):
        return "no_urls_exposed"
    if (
        homepage_status == "success"
        and (homepage_internal_link_count > 0 or sitemap_url_count_before_filtering > 0)
        and homepage_candidates_discovered == 0
        and sitemap_urls_retained == 0
        and filtered_total > 0
    ):
        return "filtered_out"
    if (
        homepage_status == "success"
        and homepage_internal_link_count <= 1
        and sitemap_url_count_before_filtering == 0
        and sitemap_urls_retained == 0
    ):
        return "potential_extraction_gap"
    if homepage_status == "success" and homepage_candidates_discovered == 0 and sitemap_urls_retained == 0:
        return "unknown"
    return "unknown"


def investigate_domain(domain: str, status: sqlite3.Row | None, config: dict) -> MissInvestigationRow:
    discovery_config = config.get("discovery", {})
    timeout_seconds = int(discovery_config.get("request_timeout_seconds", 15))
    user_agent = discovery_config.get("user_agent", "AIAustriaResearchBot/0.1")
    max_retained = int(discovery_config.get("max_retained_candidate_urls_per_domain", 20))
    logger = quiet_logger()

    homepage_status = status["homepage_status"] if status else None
    robots_status = status["robots_status"] if status else None
    sitemap_status = status["sitemap_status"] if status else None
    db_error = status["discovery_error"] if status else None
    failure_category = map_failure_category(db_error)
    evidence: list[str] = []

    homepage_metrics = {
        "links_before_filtering": 0,
        "homepage_internal_link_count": 0,
        "homepage_candidates_discovered": 0,
        "homepage_candidates_retained": 0,
        "asset_static_filtered": 0,
        "hard_negative_filtered": 0,
        "duplicate_collapsed": 0,
        "homepage_canonicalization_collapsed": 0,
    }
    robots_sitemap_references_count = 0
    sitemap_stats: dict[str, int] = {}
    sitemap_urls_retained = 0
    active_allowed_hosts = allowed_discovery_hosts(domain)

    if failure_category not in {"dns_failure", "ssl_failure", "timeout"}:
        try:
            fetched = fetch_homepage(domain, timeout_seconds, user_agent, logger)
            homepage_status = "success"
            active_allowed_hosts = allowed_discovery_hosts(domain, fetched.final_url)
            homepage_metrics = analyze_homepage_links(
                fetched.html,
                fetched.final_url,
                domain,
                max_retained,
                active_allowed_hosts,
            )
        except Exception as exc:
            failure_category = map_failure_category(str(exc))
            evidence.append(f"homepage_refetch_error={exc}")

        try:
            robots_url, robot_sitemaps, robots_error, _, _ = discover_robots_txt_for_hosts(
                domain,
                active_allowed_hosts,
                timeout_seconds,
                user_agent,
                logger,
            )
            robots_sitemap_references_count = len(robot_sitemaps)
            robots_status = "failed" if robots_error else "success"
            sitemap_candidates = discover_sitemap_candidates(
                domain,
                seed_sitemap_urls(domain, robot_sitemaps),
                timeout_seconds,
                user_agent,
                logger,
                config,
                sitemap_stats,
                allowed_hosts=active_allowed_hosts,
            )
            sitemap_urls_retained = min(len(sitemap_candidates), max_retained)
            sitemap_status = "success" if sitemap_stats.get("sitemap_successes", 0) else sitemap_status
            if robots_url:
                evidence.append(f"robots_url={robots_url}")
        except Exception as exc:
            evidence.append(f"sitemap_refetch_error={exc}")

    filtered_total = (
        homepage_metrics["asset_static_filtered"]
        + homepage_metrics["hard_negative_filtered"]
        + homepage_metrics["duplicate_collapsed"]
        + homepage_metrics["homepage_canonicalization_collapsed"]
        + sitemap_stats.get("asset_static_urls_filtered", 0)
        + sitemap_stats.get("hard_negative_urls_filtered", 0)
        + sitemap_stats.get("duplicate_urls_collapsed", 0)
        + sitemap_stats.get("homepage_variants_collapsed", 0)
    )
    diagnosis_category = classify_diagnosis(
        failure_category=failure_category,
        homepage_status=homepage_status,
        links_before_filtering=homepage_metrics["links_before_filtering"],
        homepage_internal_link_count=homepage_metrics["homepage_internal_link_count"],
        homepage_candidates_discovered=homepage_metrics["homepage_candidates_discovered"],
        sitemap_url_count_before_filtering=sitemap_stats.get("sitemap_urls_discovered", 0),
        sitemap_urls_retained=sitemap_urls_retained,
        filtered_total=filtered_total,
    )

    return MissInvestigationRow(
        domain=domain,
        homepage_status=homepage_status,
        robots_status=robots_status,
        sitemap_status=sitemap_status,
        homepage_candidates_discovered=homepage_metrics["homepage_candidates_discovered"],
        homepage_candidates_retained=homepage_metrics["homepage_candidates_retained"],
        sitemap_urls_discovered=sitemap_stats.get("sitemap_urls_discovered", 0),
        sitemap_urls_retained=sitemap_urls_retained,
        robots_sitemap_references_count=robots_sitemap_references_count,
        homepage_internal_link_count=homepage_metrics["homepage_internal_link_count"],
        links_before_filtering=homepage_metrics["links_before_filtering"],
        asset_static_filtered=homepage_metrics["asset_static_filtered"]
        + sitemap_stats.get("asset_static_urls_filtered", 0),
        hard_negative_filtered=homepage_metrics["hard_negative_filtered"]
        + sitemap_stats.get("hard_negative_urls_filtered", 0),
        duplicate_collapsed=homepage_metrics["duplicate_collapsed"]
        + sitemap_stats.get("duplicate_urls_collapsed", 0),
        homepage_canonicalization_collapsed=homepage_metrics["homepage_canonicalization_collapsed"]
        + sitemap_stats.get("homepage_variants_collapsed", 0),
        sitemap_url_count_before_filtering=sitemap_stats.get("sitemap_urls_discovered", 0),
        failure_category=failure_category,
        diagnosis_category=diagnosis_category,
        evidence="; ".join(evidence),
    )


def summarize(rows: list[MissInvestigationRow], sample_id: str) -> dict:
    counts = Counter(row.diagnosis_category for row in rows)
    total = len(rows)
    percentages = {
        category: round(count / total * 100, 2) if total else 0
        for category, count in sorted(counts.items())
    }
    return {
        "sample_id": sample_id,
        "created_at": utc_now_iso(),
        "total_zero_retained_domains": total,
        "count_infrastructure_failures": counts.get("infrastructure_failure", 0),
        "count_no_urls_exposed": counts.get("no_urls_exposed", 0),
        "count_filtered_out": counts.get("filtered_out", 0),
        "count_potential_extraction_gap": counts.get("potential_extraction_gap", 0),
        "count_unknown": counts.get("unknown", 0),
        "percentage_of_zero_retained_domains_by_category": percentages,
        "failure_category_counts": dict(Counter(row.failure_category for row in rows)),
        "potential_extraction_gap_domains": [
            row.domain for row in rows if row.diagnosis_category == "potential_extraction_gap"
        ],
    }


def write_csv(path: Path, rows: list[MissInvestigationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else [field.name for field in MissInvestigationRow.__dataclass_fields__.values()]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def investigate_discovery_misses(sample_id: str) -> tuple[Path, Path, dict]:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        domains = zero_retained_domains(connection, sample_id)
        statuses = load_status_rows(connection, domains)

    rows = [investigate_domain(domain, statuses.get(domain), config) for domain in domains]
    summary = summarize(rows, sample_id)

    csv_path = project_root / "data" / "interim" / f"discovery_miss_investigation_{sample_id}.csv"
    json_path = project_root / "logs" / f"discovery_miss_investigation_{sample_id}.json"
    write_csv(csv_path, rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    return csv_path, json_path, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Investigate zero-retained discovery domains.")
    parser.add_argument("--sample-id", required=True, help="Registered sample_id to investigate.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path, json_path, summary = investigate_discovery_misses(args.sample_id)
    print("Discovery miss investigation")
    print(f"  Sample ID: {args.sample_id}")
    print(f"  Zero-retained domains: {summary['total_zero_retained_domains']}")
    print(f"  Infrastructure failures: {summary['count_infrastructure_failures']}")
    print(f"  No URLs exposed: {summary['count_no_urls_exposed']}")
    print(f"  Filtered out: {summary['count_filtered_out']}")
    print(f"  Potential extraction gaps: {summary['count_potential_extraction_gap']}")
    print(f"  Unknown: {summary['count_unknown']}")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")


if __name__ == "__main__":
    main()
