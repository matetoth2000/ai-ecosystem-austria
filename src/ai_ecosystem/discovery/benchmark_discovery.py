"""Benchmark discovery concurrency while checking result stability."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path
from ai_ecosystem.discovery.discovery_utils import get_sample_domains
from ai_ecosystem.discovery.homepage_discovery import run_homepage_discovery


DEFAULT_WORKER_COUNTS = (1, 5, 10, 20, 50)
PAGE_TYPES_TO_TRACK = ("homepage", "products", "about", "careers", "news", "negative", "unknown")


def benchmark_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_worker_counts(value: str | None) -> list[int]:
    if not value:
        return list(DEFAULT_WORKER_COUNTS)
    worker_counts = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not worker_counts:
        raise ValueError("At least one worker count is required.")
    if any(count < 1 for count in worker_counts):
        raise ValueError("Worker counts must be positive integers.")
    return worker_counts


def load_summary_json(project_root: Path, run_id: str) -> dict:
    summary_path = project_root / "logs" / f"discovery_{run_id}_summary.json"
    with summary_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def quote_placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def collect_candidate_metrics(
    connection: sqlite3.Connection,
    run_id: str,
    sample_domains: list[str],
) -> dict:
    if not sample_domains:
        page_type_counts = Counter()
        return {
            "retained_domains_count": 0,
            "classification_counts": {page_type: 0 for page_type in PAGE_TYPES_TO_TRACK},
            "ai_technology_count": 0,
        }

    rows = connection.execute(
        f"""
        SELECT domain, page_type_guess
        FROM candidate_pages
        WHERE discovery_run_id = ?
          AND domain IN ({quote_placeholders(sample_domains)})
        """,
        (run_id, *sample_domains),
    ).fetchall()
    page_type_counts = Counter((row["page_type_guess"] or "unknown") for row in rows)
    classification_counts = {
        page_type: page_type_counts.get(page_type, 0)
        for page_type in PAGE_TYPES_TO_TRACK
    }
    return {
        "retained_domains_count": len({row["domain"] for row in rows}),
        "classification_counts": classification_counts,
        "ai_technology_count": page_type_counts.get("ai_technology", 0),
    }


def build_run_record(summary: dict, candidate_metrics: dict, workers: int) -> dict:
    classification_counts = candidate_metrics["classification_counts"]
    record = {
        "workers": workers,
        "run_id": summary.get("run_id"),
        "duration_seconds": summary.get("duration_seconds", 0),
        "domains_per_second": summary.get("domains_per_second", 0),
        "domains_attempted": summary.get("domains_attempted", 0),
        "domains_succeeded": summary.get("domains_succeeded", 0),
        "domains_failed": summary.get("domains_failed", 0),
        "homepage_successes": summary.get("domains_succeeded", 0),
        "homepage_failures": summary.get("domains_failed", 0),
        "robots_txt_successes": summary.get("robots_txt_successes", 0),
        "robots_txt_failures": summary.get("robots_txt_failures", 0),
        "sitemap_files_parsed": summary.get("sitemap_files_parsed", 0),
        "sitemap_urls_discovered": summary.get("sitemap_urls_discovered", 0),
        "sitemap_successes": summary.get("sitemap_successes", 0),
        "sitemap_failures": summary.get("sitemap_failures", 0),
        "candidate_urls_discovered": summary.get("candidate_urls_discovered", 0),
        "candidate_urls_retained": summary.get("candidate_urls_retained", 0),
        "retained_domains_count": candidate_metrics["retained_domains_count"],
        "redirected_domains_count": summary.get("redirected_domains_count", 0),
        "domains_using_alias_host": summary.get("domains_using_alias_host", 0),
        "candidate_urls_retained_via_alias_host": summary.get(
            "candidate_urls_retained_via_alias_host",
            0,
        ),
        "hard_negative_urls_filtered": summary.get("hard_negative_urls_filtered", 0),
        "asset_static_urls_filtered": summary.get("asset_static_urls_filtered", 0),
        "ai_technology_count": candidate_metrics.get("ai_technology_count", 0),
    }
    for page_type in PAGE_TYPES_TO_TRACK:
        record[f"{page_type}_count"] = classification_counts.get(page_type, 0)
    return record


def add_quality_differences(records: list[dict]) -> None:
    if not records:
        return
    baseline = records[0]
    for record in records:
        record["retained_diff_vs_1_worker"] = (
            record["candidate_urls_retained"] - baseline["candidate_urls_retained"]
        )
        record["discovered_diff_vs_1_worker"] = (
            record["candidate_urls_discovered"] - baseline["candidate_urls_discovered"]
        )
        record["coverage_diff_vs_1_worker"] = (
            record["retained_domains_count"] - baseline["retained_domains_count"]
        )


def write_benchmark_json(project_root: Path, sample_id: str, records: list[dict]) -> Path:
    path = project_root / "logs" / f"discovery_concurrency_benchmark_{benchmark_timestamp()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sample_id": sample_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": records,
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
    return path


def write_benchmark_csv(json_path: Path, records: list[dict]) -> Path:
    csv_path = json_path.with_suffix(".csv")
    if not records:
        csv_path.write_text("", encoding="utf-8")
        return csv_path
    fieldnames = list(records[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return csv_path


def format_runtime(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{float(seconds):.0f}s"


def print_comparison_table(records: list[dict]) -> None:
    headers = [
        "Workers",
        "Runtime",
        "Domains/s",
        "Retained URLs",
        "Domains Covered",
        "Sitemap URLs",
        "Retained Δ",
        "Coverage Δ",
    ]
    rows = [
        [
            str(record["workers"]),
            format_runtime(record["duration_seconds"]),
            f"{float(record['domains_per_second']):.2f}",
            str(record["candidate_urls_retained"]),
            str(record["retained_domains_count"]),
            str(record["sitemap_urls_discovered"]),
            str(record.get("retained_diff_vs_1_worker", 0)),
            str(record.get("coverage_diff_vs_1_worker", 0)),
        ]
        for record in records
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row, record in zip(rows, records, strict=False):
        marker = " *" if (
            record.get("retained_diff_vs_1_worker", 0)
            or record.get("discovered_diff_vs_1_worker", 0)
            or record.get("coverage_diff_vs_1_worker", 0)
        ) else ""
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) + marker)


def run_benchmark(sample_id: str, worker_counts: Iterable[int]) -> tuple[Path, Path, list[dict]]:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    workers = list(worker_counts)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        sample_domains = get_sample_domains(connection, sample_id)
    if not sample_domains:
        raise ValueError(f"No domains found for sample_id: {sample_id}")

    records: list[dict] = []
    for worker_count in workers:
        print(f"\nBenchmark run: sample_id={sample_id} workers={worker_count}")
        run_id = run_homepage_discovery(sample_id=sample_id, max_workers=worker_count)
        summary = load_summary_json(project_root, run_id)
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            candidate_metrics = collect_candidate_metrics(connection, run_id, sample_domains)
        records.append(build_run_record(summary, candidate_metrics, worker_count))

    add_quality_differences(records)
    json_path = write_benchmark_json(project_root, sample_id, records)
    csv_path = write_benchmark_csv(json_path, records)

    print("\nDiscovery concurrency benchmark")
    print_comparison_table(records)
    changed = [
        record
        for record in records
        if record.get("retained_diff_vs_1_worker", 0)
        or record.get("discovered_diff_vs_1_worker", 0)
        or record.get("coverage_diff_vs_1_worker", 0)
    ]
    if changed:
        print("\nQuality differences vs 1-worker baseline:")
        for record in changed:
            print(
                "  workers={workers} retained_diff={retained} discovered_diff={discovered} coverage_diff={coverage}".format(
                    workers=record["workers"],
                    retained=record["retained_diff_vs_1_worker"],
                    discovered=record["discovered_diff_vs_1_worker"],
                    coverage=record["coverage_diff_vs_1_worker"],
                )
            )
    else:
        print("\nQuality differences vs 1-worker baseline: none")
    print(f"\nBenchmark JSON: {json_path}")
    print(f"Benchmark CSV: {csv_path}")
    return json_path, csv_path, records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark URL discovery concurrency.")
    parser.add_argument("--sample-id", required=True, help="Registered sample_id to benchmark.")
    parser.add_argument(
        "--workers",
        help="Comma-separated worker counts. Defaults to 1,5,10,20,50.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_benchmark(args.sample_id, parse_worker_counts(args.workers))


if __name__ == "__main__":
    main()
