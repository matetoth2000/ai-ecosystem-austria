"""Evaluate raw HTML acquisition runs and export diagnostics."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


FETCHED_STATUSES = {"success", "non_html"}


def safe_duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return round(max((completed - started).total_seconds(), 0), 2)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile_value
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def summarize_numeric(values: Iterable[int | float | None]) -> dict[str, float]:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return {"min": 0, "average": 0, "median": 0, "p95": 0, "max": 0}
    return {
        "min": round(min(numeric_values), 2),
        "average": round(statistics.mean(numeric_values), 2),
        "median": round(statistics.median(numeric_values), 2),
        "p95": percentile(numeric_values, 0.95),
        "max": round(max(numeric_values), 2),
    }


def failure_category(row: sqlite3.Row) -> str:
    status = row["scrape_status"] or "other"
    http_status = row["http_status"]
    if status in {"dns_error", "ssl_error", "timeout", "connection_error", "redirect_error"}:
        return status
    if status == "http_error":
        if http_status is not None and 400 <= int(http_status) < 500:
            return "http_4xx"
        if http_status is not None and int(http_status) >= 500:
            return "http_5xx"
        return "http_error"
    if status == "non_html":
        return "non_html"
    if status in {"storage_error", "sqlite_error", "unexpected_exception"}:
        return status
    return "other"


def classify_failure(row: sqlite3.Row) -> str:
    category = failure_category(row)
    if category in {
        "dns_error",
        "ssl_error",
        "timeout",
        "connection_error",
        "redirect_error",
        "http_4xx",
        "http_5xx",
        "http_error",
    }:
        return "infrastructure_failure"
    if category in {"storage_error", "sqlite_error", "unexpected_exception"}:
        return "pipeline_failure"
    return "unknown"


def load_run(connection: sqlite3.Connection, acquisition_run_id: str | None, latest: bool) -> sqlite3.Row:
    if latest:
        row = connection.execute(
            """
            SELECT *
            FROM acquisition_runs
            ORDER BY COALESCE(completed_at, started_at) DESC, acquisition_run_id DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM acquisition_runs WHERE acquisition_run_id = ?",
            (acquisition_run_id,),
        ).fetchone()
    if row is None:
        raise ValueError("No acquisition run found.")
    return row


def load_scraped_rows(connection: sqlite3.Connection, acquisition_run_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM scraped_pages
        WHERE acquisition_run_id = ?
        ORDER BY domain, url
        """,
        (acquisition_run_id,),
    ).fetchall()


def counter_rows(counter: Counter, key_name: str) -> list[dict]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    ]


def build_run_summary(run: sqlite3.Row, rows: list[sqlite3.Row]) -> list[dict]:
    success_rows = [row for row in rows if row["scrape_status"] == "success"]
    html_rows = [row for row in success_rows if row["html_path"]]
    failure_rows = [row for row in rows if row["scrape_status"] != "success"]
    duration = safe_duration_seconds(run["started_at"], run["completed_at"])
    content_stats = summarize_numeric(row["content_length"] for row in html_rows)
    word_stats = summarize_numeric(row["word_count_estimate"] for row in html_rows)
    metadata_coverage = build_metadata_coverage(rows)

    base = {
        "acquisition_run_id": run["acquisition_run_id"],
        "sample_id": run["sample_id"],
        "fetch_scope": run["fetch_scope"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "duration_seconds": duration,
        "pages_attempted": run["pages_attempted"],
        "pages_fetched": run["pages_fetched"],
        "pages_failed": run["pages_failed"],
        "success_rate": run["success_rate"],
        "recorded_rows": len(rows),
        "success_rows": len(success_rows),
        "failure_rows": len(failure_rows),
        "html_path_rows": len(html_rows),
    }
    return [
        base | {"metric_group": "run", "metric": "run_summary", "value": ""},
        *[
            base
            | {
                "metric_group": "http_status",
                "metric": str(row["http_status"] if row["http_status"] is not None else "unknown"),
                "value": row["count"],
            }
            for row in counter_rows(
                Counter(str(row["http_status"] if row["http_status"] is not None else "unknown") for row in rows),
                "http_status",
            )
        ],
        *[
            base | {"metric_group": "html_size", "metric": metric, "value": value}
            for metric, value in content_stats.items()
        ],
        *[
            base | {"metric_group": "word_count", "metric": metric, "value": value}
            for metric, value in word_stats.items()
        ],
        *[
            base
            | {
                "metric_group": "metadata_coverage",
                "metric": row["field"],
                "value": row["percentage"],
            }
            for row in metadata_coverage
        ],
    ]


def build_domain_stats(rows: list[sqlite3.Row]) -> list[dict]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row["domain"] or "unknown"].append(row)

    output = []
    for domain, domain_rows in sorted(grouped.items()):
        fetched = sum(1 for row in domain_rows if row["scrape_status"] in FETCHED_STATUSES)
        failed = len(domain_rows) - fetched
        output.append(
            {
                "domain": domain,
                "pages_attempted": len(domain_rows),
                "pages_fetched": fetched,
                "pages_failed": failed,
                "success_rate": round(fetched / len(domain_rows) * 100, 2) if domain_rows else 0,
            }
        )
    return output


def build_failures(rows: list[sqlite3.Row]) -> list[dict]:
    grouped: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["scrape_status"] == "success":
            continue
        key = (failure_category(row), classify_failure(row))
        grouped[key] += 1
        if key not in examples and row["error_message"]:
            examples[key] = row["error_message"]
    return [
        {
            "failure_category": category,
            "failure_class": failure_class,
            "example_error_message": examples.get((category, failure_class), ""),
            "count": count,
        }
        for (category, failure_class), count in sorted(
            grouped.items(), key=lambda item: (-item[1], item[0][0])
        )
    ]


def build_metadata_coverage(rows: list[sqlite3.Row]) -> list[dict]:
    success_rows = [row for row in rows if row["scrape_status"] == "success"]
    total = len(success_rows)
    fields = ["title", "meta_description", "canonical_url", "language"]
    output = []
    for field in fields:
        populated = sum(1 for row in success_rows if row[field] not in {None, ""})
        output.append(
            {
                "field": field,
                "populated": populated,
                "total": total,
                "percentage": round(populated / total * 100, 2) if total else 0,
            }
        )
    return output


def build_content_types(rows: list[sqlite3.Row]) -> list[dict]:
    counts = Counter(row["content_type"] or "unknown" for row in rows)
    return counter_rows(counts, "content_type")


def build_languages(rows: list[sqlite3.Row]) -> list[dict]:
    counts = Counter(row["language"] or "unknown" for row in rows)
    return counter_rows(counts, "language")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_reports(project_root: Path, acquisition_run_id: str, summary: dict) -> dict[str, Path]:
    output_dir = project_root / "data" / "interim"
    paths = {
        "summary": output_dir / f"acquisition_summary_{acquisition_run_id}.csv",
        "domain_stats": output_dir / f"acquisition_domain_stats_{acquisition_run_id}.csv",
        "failures": output_dir / f"acquisition_failures_{acquisition_run_id}.csv",
        "content_types": output_dir / f"acquisition_content_types_{acquisition_run_id}.csv",
        "languages": output_dir / f"acquisition_languages_{acquisition_run_id}.csv",
    }
    write_csv(paths["summary"], summary["run_summary"])
    write_csv(
        paths["domain_stats"],
        summary["domain_stats"],
        ["domain", "pages_attempted", "pages_fetched", "pages_failed", "success_rate"],
    )
    write_csv(
        paths["failures"],
        summary["failures"],
        ["failure_category", "failure_class", "example_error_message", "count"],
    )
    write_csv(paths["content_types"], summary["content_types"], ["content_type", "count"])
    write_csv(paths["languages"], summary["languages"], ["language", "count"])
    return paths


def evaluate_acquisition(acquisition_run_id: str | None = None, latest: bool = False) -> dict:
    if bool(acquisition_run_id) == bool(latest):
        raise ValueError("Provide exactly one of acquisition_run_id or latest=True.")

    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        run = load_run(connection, acquisition_run_id, latest)
        rows = load_scraped_rows(connection, run["acquisition_run_id"])

    summary = {
        "project_root": project_root,
        "run": dict(run),
        "rows": rows,
        "run_summary": build_run_summary(run, rows),
        "domain_stats": build_domain_stats(rows),
        "failures": build_failures(rows),
        "content_types": build_content_types(rows),
        "languages": build_languages(rows),
        "metadata_coverage": build_metadata_coverage(rows),
    }
    summary["export_paths"] = export_reports(project_root, run["acquisition_run_id"], summary)
    return summary


def print_summary(summary: dict) -> None:
    run = summary["run"]
    failures = summary["failures"]
    languages = summary["languages"]
    content_types = summary["content_types"]

    print("Acquisition evaluation")
    print(f"Acquisition Run: {run['acquisition_run_id']}")
    print(f"Sample ID: {run['sample_id']}")
    print(f"Fetch scope: {run['fetch_scope']}")
    print(f"Pages Attempted: {run['pages_attempted']}")
    print(f"Pages Fetched: {run['pages_fetched']}")
    print(f"Pages Failed: {run['pages_failed']}")
    print(f"Success Rate: {run['success_rate']}%")
    print()
    print("Top Failure Categories")
    for row in failures[:10]:
        print(f"  {row['count']} | {row['failure_category']} | {row['failure_class']}")
    if not failures:
        print("  none")
    print()
    print("Top Languages")
    for row in languages[:10]:
        print(f"  {row['count']} | {row['language']}")
    print()
    print("Top Content Types")
    for row in content_types[:10]:
        print(f"  {row['count']} | {row['content_type']}")
    print()
    print("Generated files")
    for path in summary["export_paths"].values():
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate an acquisition run and export diagnostics.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--acquisition-run-id", help="Specific acquisition run ID to evaluate.")
    source.add_argument("--latest", action="store_true", help="Evaluate the latest acquisition run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = evaluate_acquisition(acquisition_run_id=args.acquisition_run_id, latest=args.latest)
    print_summary(summary)


if __name__ == "__main__":
    main()
