"""Analyze URL retention quality from retained/discarded audit records."""

from __future__ import annotations

import argparse
import csv
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path
from ai_ecosystem.discovery.audit_retention import latest_audit_run_id, load_audit_rows
from ai_ecosystem.discovery.discovery_utils import get_sample_domains


SAMPLE_SIZE = 200
SAMPLE_SEED = 20260604
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9äöüß]+", re.IGNORECASE)

CLEARLY_USEFUL_TOKENS = {
    "about",
    "aboutus",
    "company",
    "corporate",
    "careers",
    "career",
    "jobs",
    "karriere",
    "leistungen",
    "loesungen",
    "lösungen",
    "news",
    "presse",
    "products",
    "product",
    "produkte",
    "services",
    "service",
    "solutions",
    "technology",
    "technologie",
    "unternehmen",
    "ueber",
    "über",
}
LIKELY_LOW_VALUE_TOKENS = {
    "author",
    "category",
    "feed",
    "page",
    "search",
    "tag",
    "tags",
}
TOKEN_STOPWORDS = {
    "",
    "at",
    "com",
    "de",
    "en",
    "eu",
    "html",
    "htm",
    "php",
    "www",
}


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def path_tokens(url: str | None) -> list[str]:
    if not url:
        return []
    path = unquote(urlsplit(url).path or "").lower()
    tokens = [token for token in TOKEN_SPLIT_RE.split(path) if token and token not in TOKEN_STOPWORDS]
    return tokens


def token_counter(rows: list[dict]) -> Counter:
    counter: Counter = Counter()
    for row in rows:
        counter.update(path_tokens(row.get("url")))
    return counter


def counter_rows(
    section: str,
    counter: Counter,
    denominator: int,
    limit: int | None = None,
) -> list[dict]:
    items = counter.most_common(limit)
    return [
        {
            "section": section,
            "key": key if key is not None else "",
            "count": count,
            "share": round(count / denominator * 100, 2) if denominator else 0,
        }
        for key, count in items
    ]


def sample_rows(rows: list[dict], size: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> list[dict]:
    if len(rows) <= size:
        return list(rows)
    rng = random.Random(seed)
    return rng.sample(rows, size)


def get_candidate_pages_count(connection: sqlite3.Connection, run_id: str, domains: list[str]) -> int:
    if not domains:
        return 0
    placeholders = ",".join("?" for _ in domains)
    return connection.execute(
        f"""
        SELECT COUNT(*)
        FROM candidate_pages
        WHERE discovery_run_id = ?
          AND domain IN ({placeholders})
        """,
        (run_id, *domains),
    ).fetchone()[0]


def build_retained_summary(rows: list[dict]) -> list[dict]:
    total = len(rows)
    summary = [{"section": "total", "key": "retained_urls", "count": total, "share": 100 if total else 0}]
    summary.extend(counter_rows("page_type_guess", Counter(row.get("page_type_guess") or "unknown" for row in rows), total))
    summary.extend(counter_rows("discovery_method", Counter(row.get("discovery_method") or "unknown" for row in rows), total))
    summary.extend(counter_rows("top_domain", Counter(row.get("domain") or "" for row in rows), total, limit=30))
    summary.extend(counter_rows("top_path_token", token_counter(rows), total, limit=50))
    summary.extend(counter_rows("priority", Counter(str(row.get("priority")) for row in rows), total))
    return summary


def build_discarded_summary(rows: list[dict]) -> list[dict]:
    total = len(rows)
    summary = [{"section": "total", "key": "discarded_urls", "count": total, "share": 100 if total else 0}]
    summary.extend(counter_rows("discard_reason", Counter(row.get("discard_reason") or "none" for row in rows), total))
    summary.extend(counter_rows("page_type_guess", Counter(row.get("page_type_guess") or "unknown" for row in rows), total))
    summary.extend(counter_rows("top_domain", Counter(row.get("domain") or "" for row in rows), total, limit=30))
    summary.extend(counter_rows("top_path_token", token_counter(rows), total, limit=50))
    return summary


def build_domain_stats(domains: list[str], rows: list[dict]) -> list[dict]:
    counts_by_domain: dict[str, Counter] = {domain: Counter() for domain in domains}
    for row in rows:
        domain = row.get("domain")
        if domain not in counts_by_domain:
            continue
        decision = row.get("decision")
        method = row.get("discovery_method") or "unknown"
        discard_reason = row.get("discard_reason") or "none"
        counts_by_domain[domain]["discovered_url_count"] += 1
        counts_by_domain[domain][f"{decision}_url_count"] += 1
        counts_by_domain[domain][f"{method}_{decision}"] += 1
        if decision == "discarded":
            counts_by_domain[domain][f"discard_reason_{discard_reason}"] += 1

    output: list[dict] = []
    for domain in domains:
        domain_counts = counts_by_domain[domain]
        discovered = domain_counts["discovered_url_count"]
        retained = domain_counts["retained_url_count"]
        discarded = domain_counts["discarded_url_count"]
        output.append(
            {
                "domain": domain,
                "discovered_url_count": discovered,
                "retained_url_count": retained,
                "discarded_url_count": discarded,
                "retention_rate": round(retained / discovered * 100, 2) if discovered else 0,
                "homepage_retained": domain_counts["homepage_retained"],
                "sitemap_retained": domain_counts["sitemap_retained"],
                "retention_cap_discarded": domain_counts["discard_reason_retention_cap"],
            }
        )
    return output


def distribution_comparison(
    dimension: str,
    retained_counter: Counter,
    discarded_counter: Counter,
    retained_total: int,
    discarded_total: int,
) -> list[dict]:
    keys = sorted(set(retained_counter) | set(discarded_counter))
    rows: list[dict] = []
    for key in keys:
        retained_count = retained_counter.get(key, 0)
        discarded_count = discarded_counter.get(key, 0)
        retained_share = retained_count / retained_total * 100 if retained_total else 0
        discarded_share = discarded_count / discarded_total * 100 if discarded_total else 0
        rows.append(
            {
                "dimension": dimension,
                "key": key if key is not None else "",
                "retained_count": retained_count,
                "retained_share": round(retained_share, 2),
                "discarded_count": discarded_count,
                "discarded_share": round(discarded_share, 2),
                "retained_minus_discarded_share": round(retained_share - discarded_share, 2),
            }
        )
    return sorted(rows, key=lambda row: abs(row["retained_minus_discarded_share"]), reverse=True)


def build_comparison(retained_rows: list[dict], discarded_rows: list[dict]) -> list[dict]:
    retained_total = len(retained_rows)
    discarded_total = len(discarded_rows)
    comparison: list[dict] = []
    comparison.extend(
        distribution_comparison(
            "page_type_guess",
            Counter(row.get("page_type_guess") or "unknown" for row in retained_rows),
            Counter(row.get("page_type_guess") or "unknown" for row in discarded_rows),
            retained_total,
            discarded_total,
        )
    )
    comparison.extend(
        distribution_comparison(
            "discovery_method",
            Counter(row.get("discovery_method") or "unknown" for row in retained_rows),
            Counter(row.get("discovery_method") or "unknown" for row in discarded_rows),
            retained_total,
            discarded_total,
        )
    )
    comparison.extend(
        distribution_comparison(
            "domain",
            Counter(row.get("domain") or "" for row in retained_rows),
            Counter(row.get("domain") or "" for row in discarded_rows),
            retained_total,
            discarded_total,
        )[:30]
    )
    comparison.extend(
        distribution_comparison(
            "discard_reason",
            Counter(),
            Counter(row.get("discard_reason") or "none" for row in discarded_rows),
            retained_total,
            discarded_total,
        )
    )
    return comparison


def quality_label(url: str | None) -> str:
    tokens = set(path_tokens(url))
    if tokens & LIKELY_LOW_VALUE_TOKENS:
        return "likely_low_value"
    if tokens & CLEARLY_USEFUL_TOKENS:
        return "clearly_useful"
    return "possibly_useful"


def build_quality_estimate(rows: list[dict]) -> list[dict]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        counts[row.get("decision") or "unknown"][quality_label(row.get("url"))] += 1

    output: list[dict] = []
    for decision, counter in sorted(counts.items()):
        total = sum(counter.values())
        for label, count in counter.most_common():
            output.append(
                {
                    "decision": decision,
                    "quality_estimate": label,
                    "count": count,
                    "share": round(count / total * 100, 2) if total else 0,
                }
            )
    return output


def export_retention_quality_audit(sample_id: str) -> tuple[dict, dict[str, Path]]:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    output_dir = project_root / "data" / "interim"

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        run_id = latest_audit_run_id(connection, sample_id)
        domains = get_sample_domains(connection, sample_id)
        if not domains:
            raise ValueError(f"No domains found for sample_id: {sample_id}")
        _selected_run_id, audit_rows = load_audit_rows(connection, sample_id, run_id)
        candidate_pages_count = get_candidate_pages_count(connection, run_id, domains)

    rows = [row_to_dict(row) for row in audit_rows]
    retained_rows = [row for row in rows if row["decision"] == "retained"]
    discarded_rows = [row for row in rows if row["decision"] == "discarded"]

    retained_summary = build_retained_summary(retained_rows)
    discarded_summary = build_discarded_summary(discarded_rows)
    domain_stats = build_domain_stats(domains, rows)
    comparison = build_comparison(retained_rows, discarded_rows)
    quality_estimate = build_quality_estimate(rows)

    retained_sample_fields = ["domain", "url", "page_type_guess", "priority", "discovery_method"]
    discarded_sample_fields = [
        "domain",
        "url",
        "page_type_guess",
        "priority",
        "discovery_method",
        "discard_reason",
    ]
    retained_sample = [
        {field: row.get(field) for field in retained_sample_fields}
        for row in sample_rows(retained_rows)
    ]
    discarded_sample = [
        {field: row.get(field) for field in discarded_sample_fields}
        for row in sample_rows(discarded_rows)
    ]

    paths = {
        "retained_summary": output_dir / f"retention_audit_retained_summary_{sample_id}.csv",
        "retained_sample": output_dir / f"retained_sample_{sample_id}.csv",
        "discarded_summary": output_dir / f"retention_audit_discarded_summary_{sample_id}.csv",
        "discarded_sample": output_dir / f"discarded_sample_{sample_id}.csv",
        "domain_stats": output_dir / f"retention_audit_domain_stats_{sample_id}.csv",
        "comparison": output_dir / f"retention_audit_comparison_{sample_id}.csv",
        "quality_estimate": output_dir / f"retention_audit_quality_estimate_{sample_id}.csv",
    }

    write_csv(paths["retained_summary"], retained_summary, ["section", "key", "count", "share"])
    write_csv(paths["retained_sample"], retained_sample, retained_sample_fields)
    write_csv(paths["discarded_summary"], discarded_summary, ["section", "key", "count", "share"])
    write_csv(paths["discarded_sample"], discarded_sample, discarded_sample_fields)
    write_csv(
        paths["domain_stats"],
        domain_stats,
        [
            "domain",
            "discovered_url_count",
            "retained_url_count",
            "discarded_url_count",
            "retention_rate",
            "homepage_retained",
            "sitemap_retained",
            "retention_cap_discarded",
        ],
    )
    write_csv(
        paths["comparison"],
        comparison,
        [
            "dimension",
            "key",
            "retained_count",
            "retained_share",
            "discarded_count",
            "discarded_share",
            "retained_minus_discarded_share",
        ],
    )
    write_csv(paths["quality_estimate"], quality_estimate, ["decision", "quality_estimate", "count", "share"])

    summary = {
        "sample_id": sample_id,
        "run_id": run_id,
        "retained_urls": len(retained_rows),
        "discarded_urls": len(discarded_rows),
        "candidate_pages_count": candidate_pages_count,
        "top_retained_page_types": Counter(row.get("page_type_guess") or "unknown" for row in retained_rows).most_common(10),
        "top_discarded_page_types": Counter(row.get("page_type_guess") or "unknown" for row in discarded_rows).most_common(10),
        "top_discard_reasons": Counter(row.get("discard_reason") or "none" for row in discarded_rows).most_common(10),
        "top_cap_pressure_domains": [
            (row["domain"], row["retention_cap_discarded"])
            for row in sorted(domain_stats, key=lambda item: item["retention_cap_discarded"], reverse=True)[:10]
            if row["retention_cap_discarded"] > 0
        ],
        "quality_estimate": quality_estimate,
    }
    return summary, paths


def print_summary(summary: dict, paths: dict[str, Path]) -> None:
    print("Discovery retention quality audit")
    print(f"  Sample ID: {summary['sample_id']}")
    print(f"  Run ID: {summary['run_id']}")
    print(f"  Retained URLs: {summary['retained_urls']}")
    print(f"  Discarded URLs: {summary['discarded_urls']}")
    print(f"  Candidate pages for run: {summary['candidate_pages_count']}")
    print("  Top retained page types:")
    for page_type, count in summary["top_retained_page_types"][:5]:
        print(f"    {page_type}: {count}")
    print("  Top discarded page types:")
    for page_type, count in summary["top_discarded_page_types"][:5]:
        print(f"    {page_type}: {count}")
    print("  Top discard reasons:")
    for reason, count in summary["top_discard_reasons"][:5]:
        print(f"    {reason}: {count}")
    print("  Domains generating most cap pressure:")
    for domain, count in summary["top_cap_pressure_domains"][:5]:
        print(f"    {domain}: {count}")
    print("  Heuristic quality estimate:")
    for row in summary["quality_estimate"]:
        print(
            "    "
            f"{row['decision']} | {row['quality_estimate']}: "
            f"{row['count']} ({row['share']}%)"
        )
    print("  CSV outputs:")
    for path in paths.values():
        print(f"    {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze discovery URL retention quality.")
    parser.add_argument("--sample-id", required=True, help="Registered sample_id with retention audit records.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary, paths = export_retention_quality_audit(args.sample_id)
    print_summary(summary, paths)


if __name__ == "__main__":
    main()
