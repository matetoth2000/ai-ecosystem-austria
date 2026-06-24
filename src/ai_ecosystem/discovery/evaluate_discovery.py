"""Evaluate multi-method URL discovery results for a fixed pilot sample."""

from __future__ import annotations

import argparse
import csv
import statistics
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.discovery.discovery_utils import get_sample_domains
from ai_ecosystem.discovery.homepage_discovery import read_domains_from_csv


def quote_placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def summarize_counts(counts: Iterable[int]) -> dict[str, float | int]:
    values = list(counts)
    if not values:
        return {"average": 0, "median": 0, "min": 0, "max": 0}
    return {
        "average": round(statistics.mean(values), 2),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def safe_duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return round(max((completed - started).total_seconds(), 0), 2)


def get_candidate_rows(connection: sqlite3.Connection, domains: list[str]) -> list[sqlite3.Row]:
    if not domains:
        return []
    discovered_rows = connection.execute(
        f"""
        SELECT domain, discovery_method, page_type_guess, normalized_url, priority, discovery_run_id
        FROM discovered_pages
        WHERE retained_at_discovery = 1
          AND domain IN ({quote_placeholders(domains)})
        """,
        tuple(domains),
    ).fetchall()
    if discovered_rows:
        return discovered_rows
    return connection.execute(
        f"""
        SELECT domain, discovery_method, page_type_guess, normalized_url, priority, discovery_run_id
        FROM candidate_pages
        WHERE domain IN ({quote_placeholders(domains)})
        """,
        tuple(domains),
    ).fetchall()


def get_status_rows(connection: sqlite3.Connection, domains: list[str]) -> dict[str, sqlite3.Row]:
    if not domains:
        return {}
    rows = connection.execute(
        f"""
        SELECT
            domain,
            homepage_status,
            robots_status,
            sitemap_status,
            discovery_run_id,
            discovery_error
        FROM domain_discovery_status
        WHERE domain IN ({quote_placeholders(domains)})
        """,
        tuple(domains),
    ).fetchall()
    return {row["domain"]: row for row in rows}


def get_latest_run_metadata(
    connection: sqlite3.Connection,
    candidate_rows: list[sqlite3.Row],
) -> sqlite3.Row | None:
    run_ids = [row["discovery_run_id"] for row in candidate_rows if row["discovery_run_id"]]
    if not run_ids:
        return None
    counts = Counter(run_ids)
    latest_run_id = counts.most_common(1)[0][0]
    return connection.execute(
        """
        SELECT
            run_id,
            started_at,
            completed_at,
            domains_attempted,
            domains_succeeded,
            domains_failed,
            candidate_urls_discovered,
            candidate_urls_retained,
            notes
        FROM discovery_runs
        WHERE run_id = ?
        """,
        (latest_run_id,),
    ).fetchone()


def load_domains_from_sample_id(connection: sqlite3.Connection, sample_id: str) -> list[str]:
    domains = get_sample_domains(connection, sample_id)
    if not domains:
        raise ValueError(f"No domains found for sample_id: {sample_id}")
    return domains


def build_domain_summary(
    domains: list[str],
    candidate_rows: list[sqlite3.Row],
    status_by_domain: dict[str, sqlite3.Row],
) -> list[dict]:
    by_domain_method: dict[str, Counter] = {domain: Counter() for domain in domains}
    by_domain_category: dict[str, Counter] = {domain: Counter() for domain in domains}
    for row in candidate_rows:
        by_domain_method[row["domain"]][row["discovery_method"] or "unknown_method"] += 1
        by_domain_category[row["domain"]][row["page_type_guess"] or "unknown"] += 1

    rows: list[dict] = []
    category_names = sorted({category for counts in by_domain_category.values() for category in counts})
    for domain in domains:
        method_counts = by_domain_method[domain]
        category_counts = by_domain_category[domain]
        status = status_by_domain.get(domain)
        total = sum(method_counts.values())
        row = {
            "domain": domain,
            "total_candidates": total,
            "homepage_candidates": method_counts.get("homepage", 0),
            "sitemap_candidates": method_counts.get("sitemap", 0),
            "has_homepage": int(method_counts.get("homepage", 0) > 0),
            "has_sitemap": int(method_counts.get("sitemap", 0) > 0),
            "homepage_status": status["homepage_status"] if status else "",
            "robots_status": status["robots_status"] if status else "",
            "sitemap_status": status["sitemap_status"] if status else "",
            "discovery_error": status["discovery_error"] if status else "",
        }
        for category in category_names:
            row[f"page_type_{category}"] = category_counts.get(category, 0)
        rows.append(row)
    return rows


def build_method_summary(domains: list[str], candidate_rows: list[sqlite3.Row]) -> list[dict]:
    by_method_domain: dict[str, Counter] = defaultdict(Counter)
    for row in candidate_rows:
        by_method_domain[row["discovery_method"] or "unknown_method"][row["domain"]] += 1

    rows: list[dict] = []
    for method, counts_by_domain in sorted(by_method_domain.items()):
        per_domain_counts = [counts_by_domain.get(domain, 0) for domain in domains]
        covered_counts = [count for count in per_domain_counts if count > 0]
        rows.append(
            {
                "discovery_method": method,
                "retained_candidate_count": sum(per_domain_counts),
                "domains_covered": len(covered_counts),
                "average_retained_urls_per_domain": round(statistics.mean(covered_counts), 2)
                if covered_counts
                else 0,
                "median_retained_urls_per_domain": statistics.median(covered_counts)
                if covered_counts
                else 0,
            }
        )
    return rows


def build_category_summary(candidate_rows: list[sqlite3.Row]) -> list[dict]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in candidate_rows:
        counts[(row["discovery_method"] or "unknown_method", row["page_type_guess"] or "unknown")] += 1

    rows = [
        {
            "discovery_method": method,
            "page_type_guess": page_type,
            "retained_candidate_count": count,
        }
        for (method, page_type), count in sorted(counts.items())
    ]
    return rows


def evaluate_discovery(
    input_csv: Path | None = None,
    export_dir: Path | None = None,
    sample_id: str | None = None,
) -> dict:
    if bool(input_csv) == bool(sample_id):
        raise ValueError("Provide exactly one of input_csv or sample_id.")

    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if sample_id:
            domains = load_domains_from_sample_id(connection, sample_id)
            input_label = f"sample_id:{sample_id}"
            export_name = sample_id
        else:
            assert input_csv is not None
            domains = read_domains_from_csv(input_csv)
            input_label = str(input_csv)
            export_name = input_csv.stem
        candidate_rows = get_candidate_rows(connection, domains)
        status_by_domain = get_status_rows(connection, domains)
        latest_run = get_latest_run_metadata(connection, candidate_rows)

    domain_summary = build_domain_summary(domains, candidate_rows, status_by_domain)
    method_summary = build_method_summary(domains, candidate_rows)
    category_summary = build_category_summary(candidate_rows)

    total_by_domain = {row["domain"]: row["total_candidates"] for row in domain_summary}
    homepage_by_domain = {row["domain"]: row["homepage_candidates"] for row in domain_summary}
    sitemap_by_domain = {row["domain"]: row["sitemap_candidates"] for row in domain_summary}
    domains_with_any = [domain for domain, count in total_by_domain.items() if count > 0]
    domains_with_zero = [domain for domain, count in total_by_domain.items() if count == 0]
    homepage_only = [
        domain for domain in domains if homepage_by_domain[domain] > 0 and sitemap_by_domain[domain] == 0
    ]
    sitemap_only = [
        domain for domain in domains if sitemap_by_domain[domain] > 0 and homepage_by_domain[domain] == 0
    ]
    both = [
        domain for domain in domains if sitemap_by_domain[domain] > 0 and homepage_by_domain[domain] > 0
    ]
    neither = [
        domain for domain in domains if sitemap_by_domain[domain] == 0 and homepage_by_domain[domain] == 0
    ]

    page_type_counts = Counter(row["page_type_guess"] or "unknown" for row in candidate_rows)
    page_type_by_method = Counter(
        (row["discovery_method"] or "unknown_method", row["page_type_guess"] or "unknown")
        for row in candidate_rows
    )
    latest_run_summary = None
    if latest_run:
        latest_run_summary = {
            "run_id": latest_run["run_id"],
            "started_at": latest_run["started_at"],
            "completed_at": latest_run["completed_at"],
            "duration_seconds": safe_duration_seconds(latest_run["started_at"], latest_run["completed_at"]),
            "candidate_urls_discovered": latest_run["candidate_urls_discovered"],
            "candidate_urls_retained": latest_run["candidate_urls_retained"],
            "domains_attempted": latest_run["domains_attempted"],
            "domains_succeeded": latest_run["domains_succeeded"],
            "domains_failed": latest_run["domains_failed"],
            "notes": latest_run["notes"],
        }

    summary = {
        "input_csv": str(input_csv) if input_csv else None,
        "sample_id": sample_id,
        "input_label": input_label,
        "sample_size": len(domains),
        "attempted_domains": len(status_by_domain),
        "domains_with_any_retained_candidate_url": len(domains_with_any),
        "domains_with_zero_retained_candidate_urls": domains_with_zero,
        "total_retained_candidate_urls": len(candidate_rows),
        "retained_stats": summarize_counts(total_by_domain.values()),
        "method_summary": method_summary,
        "homepage_only_domains": homepage_only,
        "sitemap_only_domains": sitemap_only,
        "both_homepage_and_sitemap_domains": both,
        "neither_homepage_nor_sitemap_domains": neither,
        "total_homepage_candidates": sum(homepage_by_domain.values()),
        "total_sitemap_candidates": sum(sitemap_by_domain.values()),
        "sitemap_only_retained_candidates": sum(sitemap_by_domain[domain] for domain in sitemap_only),
        "domains_with_largest_sitemap_gain": sorted(
            sitemap_by_domain.items(),
            key=lambda item: (-item[1], item[0]),
        )[:20],
        "page_type_counts": page_type_counts,
        "page_type_by_method": page_type_by_method,
        "top_domains_by_total": sorted(total_by_domain.items(), key=lambda item: (-item[1], item[0]))[:20],
        "top_domains_by_sitemap": sorted(sitemap_by_domain.items(), key=lambda item: (-item[1], item[0]))[:20],
        "latest_run": latest_run_summary,
        "domain_summary": domain_summary,
        "category_summary": category_summary,
    }

    if export_dir:
        export_reports(export_dir, export_name, domain_summary, method_summary, category_summary)

    return summary


def export_reports(
    export_dir: Path,
    sample_name: str,
    domain_summary: list[dict],
    method_summary: list[dict],
    category_summary: list[dict],
) -> list[Path]:
    export_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        (export_dir / f"discovery_eval_{sample_name}_domain_summary.csv", domain_summary),
        (export_dir / f"discovery_eval_{sample_name}_method_summary.csv", method_summary),
        (export_dir / f"discovery_eval_{sample_name}_category_summary.csv", category_summary),
    ]
    for path, rows in outputs:
        fieldnames = sorted({key for row in rows for key in row}) if rows else []
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(rows)
            else:
                file.write("")
    return [path for path, _ in outputs]


def print_summary(summary: dict) -> None:
    stats = summary["retained_stats"]
    print("Multi-method discovery evaluation")
    print(f"Sample: {summary['input_label']}")
    print(f"Sample size: {summary['sample_size']}")
    print(f"Attempted domains: {summary['attempted_domains']}")
    print(f"Domains with any retained candidate URL: {summary['domains_with_any_retained_candidate_url']}")
    print(f"Domains with zero retained candidate URLs: {len(summary['domains_with_zero_retained_candidate_urls'])}")
    print(f"Total retained candidate URLs: {summary['total_retained_candidate_urls']}")
    print(
        "Retained URLs per domain: "
        f"avg={stats['average']} median={stats['median']} min={stats['min']} max={stats['max']}"
    )

    print()
    print("By discovery method")
    for row in summary["method_summary"]:
        print(
            "  "
            f"{row['discovery_method']} | candidates={row['retained_candidate_count']} "
            f"domains={row['domains_covered']} avg/domain={row['average_retained_urls_per_domain']} "
            f"median/domain={row['median_retained_urls_per_domain']}"
        )
    if not summary["method_summary"]:
        print("  none")

    print()
    print("Homepage vs sitemap")
    print(f"  homepage only domains: {len(summary['homepage_only_domains'])}")
    print(f"  sitemap only domains: {len(summary['sitemap_only_domains'])}")
    print(f"  both homepage and sitemap domains: {len(summary['both_homepage_and_sitemap_domains'])}")
    print(f"  neither domains: {len(summary['neither_homepage_nor_sitemap_domains'])}")
    print(f"  total homepage candidates: {summary['total_homepage_candidates']}")
    print(f"  total sitemap candidates: {summary['total_sitemap_candidates']}")
    print(f"  sitemap-only retained candidates: {summary['sitemap_only_retained_candidates']}")

    print()
    print("Page type distribution")
    for page_type, count in summary["page_type_counts"].most_common():
        print(f"  {page_type}: {count}")
    if not summary["page_type_counts"]:
        print("  none")

    print()
    print("Page type by discovery method")
    for (method, page_type), count in sorted(summary["page_type_by_method"].items()):
        print(f"  {method} | {page_type}: {count}")
    if not summary["page_type_by_method"]:
        print("  none")

    print()
    print("Top domains by total retained candidates")
    for domain, count in summary["top_domains_by_total"]:
        print(f"  {count} | {domain}")

    print()
    print("Top domains by sitemap retained candidates")
    for domain, count in summary["top_domains_by_sitemap"]:
        if count > 0:
            print(f"  {count} | {domain}")

    print()
    print("Domains with zero candidates")
    for domain in summary["domains_with_zero_retained_candidate_urls"][:30]:
        print(f"  {domain}")
    if not summary["domains_with_zero_retained_candidate_urls"]:
        print("  none")

    print()
    print("Latest run metadata")
    latest_run = summary["latest_run"]
    if latest_run:
        print(f"  run_id: {latest_run['run_id']}")
        print(f"  started_at: {latest_run['started_at']}")
        print(f"  completed_at: {latest_run['completed_at']}")
        print(f"  duration_seconds: {latest_run['duration_seconds']}")
        print(f"  candidate_urls_discovered: {latest_run['candidate_urls_discovered']}")
        print(f"  candidate_urls_retained: {latest_run['candidate_urls_retained']}")
        print(f"  domains_succeeded: {latest_run['domains_succeeded']}")
        print(f"  domains_failed: {latest_run['domains_failed']}")
    else:
        print("  none")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate multi-method URL discovery results for a pilot CSV.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-csv", help="Pilot sample CSV with a domain column.")
    source.add_argument("--sample-id", help="Registered sample_id from sample_sets/sample_domains.")
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export domain, method, and category reports to data/interim/.",
    )
    return parser.parse_args()


def main() -> None:
    project_root = find_project_root()
    args = parse_args()
    export_dir = project_root / "data" / "interim" if args.export_csv else None
    input_csv = resolve_project_path(project_root, args.input_csv) if args.input_csv else None
    summary = evaluate_discovery(input_csv=input_csv, sample_id=args.sample_id, export_dir=export_dir)
    print_summary(summary)
    if export_dir:
        print()
        print(f"Exported reports to: {export_dir}")


if __name__ == "__main__":
    main()
