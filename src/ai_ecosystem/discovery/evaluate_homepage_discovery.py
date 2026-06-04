"""Evaluate homepage discovery results for a fixed pilot sample."""

from __future__ import annotations

import argparse
import csv
import statistics
import sqlite3
from collections import Counter
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.discovery.homepage_discovery import read_domains_from_csv


RETAINED_CAP_DEFAULT = 20


def quote_placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def count_rows(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0])


def get_candidate_counts(connection: sqlite3.Connection, domains: list[str]) -> dict[str, int]:
    if not domains:
        return {}
    rows = connection.execute(
        f"""
        SELECT domain, COUNT(*) AS n
        FROM candidate_pages
        WHERE discovery_method = 'homepage'
          AND domain IN ({quote_placeholders(domains)})
        GROUP BY domain
        """,
        tuple(domains),
    ).fetchall()
    counts = {domain: 0 for domain in domains}
    counts.update({row[0]: int(row[1]) for row in rows})
    return counts


def summarize_counts(counts: list[int]) -> dict[str, float | int]:
    if not counts:
        return {"average": 0, "median": 0, "min": 0, "max": 0}
    return {
        "average": round(statistics.mean(counts), 2),
        "median": statistics.median(counts),
        "min": min(counts),
        "max": max(counts),
    }


def get_failure_reasons(connection: sqlite3.Connection, domains: list[str]) -> Counter:
    if not domains:
        return Counter()
    rows = connection.execute(
        f"""
        SELECT discovery_error
        FROM domain_discovery_status
        WHERE domain IN ({quote_placeholders(domains)})
          AND homepage_status != 'success'
          AND discovery_error IS NOT NULL
          AND discovery_error != ''
        """,
        tuple(domains),
    ).fetchall()
    return Counter(row[0].split(";")[0][:160] for row in rows)


def get_top_candidate_urls(connection: sqlite3.Connection, domains: list[str], limit: int = 20) -> list[sqlite3.Row]:
    if not domains:
        return []
    return connection.execute(
        f"""
        SELECT domain, normalized_url, priority, page_type_guess
        FROM candidate_pages
        WHERE discovery_method = 'homepage'
          AND domain IN ({quote_placeholders(domains)})
        ORDER BY priority DESC, domain, normalized_url
        LIMIT ?
        """,
        tuple(domains) + (limit,),
    ).fetchall()


def evaluate_homepage_discovery(
    input_csv: Path,
    export_csv: Path | None = None,
    retained_cap: int = RETAINED_CAP_DEFAULT,
) -> dict:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    domains = read_domains_from_csv(input_csv)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        placeholders = quote_placeholders(domains)
        status_rows = []
        if domains:
            status_rows = connection.execute(
                f"""
                SELECT domain, homepage_status, discovery_error
                FROM domain_discovery_status
                WHERE domain IN ({placeholders})
                """,
                tuple(domains),
            ).fetchall()

        status_by_domain = {row["domain"]: row for row in status_rows}
        attempted_domains = len(status_by_domain)
        homepage_success_count = sum(
            1 for row in status_rows if row["homepage_status"] == "success"
        )
        homepage_failure_count = sum(
            1 for row in status_rows if row["homepage_status"] and row["homepage_status"] != "success"
        )
        candidate_counts = get_candidate_counts(connection, domains)
        count_values = list(candidate_counts.values())
        domains_with_zero = [domain for domain, count in candidate_counts.items() if count == 0]
        domains_hitting_cap = [
            domain for domain, count in candidate_counts.items() if count >= retained_cap
        ]
        failure_reasons = get_failure_reasons(connection, domains)
        top_candidates = get_top_candidate_urls(connection, domains)

    success_rate = round(homepage_success_count / attempted_domains * 100, 2) if attempted_domains else 0
    summary = {
        "input_csv": str(input_csv),
        "sample_size": len(domains),
        "attempted_domains": attempted_domains,
        "homepage_success_count": homepage_success_count,
        "homepage_failure_count": homepage_failure_count,
        "success_rate": success_rate,
        "total_candidate_pages_from_homepage": sum(count_values),
        "candidate_stats": summarize_counts(count_values),
        "domains_with_zero_candidate_urls": domains_with_zero,
        "domains_hitting_retained_cap": domains_hitting_cap,
        "failure_reasons": failure_reasons,
        "top_candidates": top_candidates,
        "candidate_counts": candidate_counts,
    }

    if export_csv:
        export_report(export_csv, domains, candidate_counts, status_by_domain)

    return summary


def export_report(
    export_csv: Path,
    domains: list[str],
    candidate_counts: dict[str, int],
    status_by_domain: dict[str, sqlite3.Row],
) -> None:
    export_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "domain",
        "homepage_status",
        "candidate_url_count",
        "discovery_error",
    ]
    with export_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for domain in domains:
            status = status_by_domain.get(domain)
            writer.writerow(
                {
                    "domain": domain,
                    "homepage_status": status["homepage_status"] if status else "",
                    "candidate_url_count": candidate_counts.get(domain, 0),
                    "discovery_error": status["discovery_error"] if status else "",
                }
            )


def default_export_path(project_root: Path, input_csv: Path) -> Path:
    sample_name = input_csv.stem
    return project_root / "data" / "interim" / f"homepage_discovery_report_{sample_name}.csv"


def print_summary(summary: dict) -> None:
    stats = summary["candidate_stats"]
    print("Homepage discovery evaluation")
    print(f"Sample CSV: {summary['input_csv']}")
    print(f"Sample size: {summary['sample_size']}")
    print(f"Attempted domains: {summary['attempted_domains']}")
    print(f"Homepage success count: {summary['homepage_success_count']}")
    print(f"Homepage failure count: {summary['homepage_failure_count']}")
    print(f"Success rate: {summary['success_rate']}%")
    print(f"Total candidate_pages from homepage: {summary['total_candidate_pages_from_homepage']}")
    print(
        "Retained URLs per domain: "
        f"avg={stats['average']} median={stats['median']} min={stats['min']} max={stats['max']}"
    )

    print()
    print("Domains with 0 candidate URLs")
    for domain in summary["domains_with_zero_candidate_urls"][:30]:
        print(f"  {domain}")
    if not summary["domains_with_zero_candidate_urls"]:
        print("  none")

    print()
    print("Domains hitting retained cap")
    for domain in summary["domains_hitting_retained_cap"][:30]:
        print(f"  {domain}")
    if not summary["domains_hitting_retained_cap"]:
        print("  none")

    print()
    print("Top failure reasons")
    for reason, count in summary["failure_reasons"].most_common(10):
        print(f"  {count} | {reason}")
    if not summary["failure_reasons"]:
        print("  none")

    print()
    print("Top candidate URLs by priority")
    for row in summary["top_candidates"]:
        print(f"  {row['priority']} | {row['domain']} | {row['page_type_guess']} | {row['normalized_url']}")
    if not summary["top_candidates"]:
        print("  none")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate homepage discovery results for a pilot CSV.")
    parser.add_argument("--input-csv", required=True, help="Pilot sample CSV with a domain column.")
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export per-domain report to data/interim/homepage_discovery_report_<sample_name>.csv.",
    )
    parser.add_argument(
        "--retained-cap",
        type=int,
        default=RETAINED_CAP_DEFAULT,
        help="Retained URL cap used to flag domains hitting the cap.",
    )
    return parser.parse_args()


def main() -> None:
    project_root = find_project_root()
    args = parse_args()
    input_csv = resolve_project_path(project_root, args.input_csv)
    export_csv = default_export_path(project_root, input_csv) if args.export_csv else None
    summary = evaluate_homepage_discovery(
        input_csv=input_csv,
        export_csv=export_csv,
        retained_cap=args.retained_cap,
    )
    print_summary(summary)
    if export_csv:
        print()
        print(f"Exported report: {export_csv}")


if __name__ == "__main__":
    main()
