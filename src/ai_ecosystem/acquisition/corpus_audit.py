"""Audit acquisition corpus quality for a completed acquisition run."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from ai_ecosystem.acquisition.evaluate_acquisition import failure_category, safe_duration_seconds, summarize_numeric
from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


FETCHED_STATUSES = {"success", "non_html"}
UNUSUAL_LANGUAGE_ALLOWLIST = {"de", "de-DE", "de-at", "en", "en-US", "en-GB", "unknown", ""}
LOW_WORD_THRESHOLD = 50
HIGH_WORD_THRESHOLD = 10000
SMALL_HTML_THRESHOLD_BYTES = 2000


def load_run(connection: sqlite3.Connection, acquisition_run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM acquisition_runs WHERE acquisition_run_id = ?",
        (acquisition_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No acquisition run found for {acquisition_run_id}")
    return row


def load_acquisition_rows(connection: sqlite3.Connection, acquisition_run_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            sp.*,
            COALESCE(dp.retained_at_discovery, sp.retained_at_discovery) AS discovery_retained_at_discovery,
            COALESCE(dp.discovery_method, cp.discovery_method, 'unknown') AS source_discovery_method,
            COALESCE(dp.page_type_guess, cp.page_type_guess, 'unknown') AS source_page_type_guess,
            COALESCE(dp.priority, cp.priority) AS source_priority
        FROM scraped_pages AS sp
        LEFT JOIN discovered_pages AS dp
          ON dp.discovered_page_id = sp.discovered_page_id
        LEFT JOIN candidate_pages AS cp
          ON cp.id = sp.candidate_page_id
        WHERE sp.acquisition_run_id = ?
        ORDER BY sp.domain, sp.url
        """,
        (acquisition_run_id,),
    ).fetchall()


def compressed_size(project_root: Path, html_path: str | None) -> int | None:
    if not html_path:
        return None
    path = project_root / html_path
    try:
        return path.stat().st_size
    except OSError:
        return None


def enrich_rows(project_root: Path, rows: list[sqlite3.Row]) -> list[dict]:
    output = []
    for row in rows:
        item = dict(row)
        item["failure_category"] = failure_category(row) if row["scrape_status"] != "success" else ""
        item["fetched_flag"] = int(row["scrape_status"] in FETCHED_STATUSES)
        item["failed_flag"] = int(row["scrape_status"] not in FETCHED_STATUSES)
        item["html_success_flag"] = int(row["scrape_status"] == "success")
        item["compressed_size_bytes"] = compressed_size(project_root, row["html_path"])
        item["retention_group"] = (
            "retained" if item["discovery_retained_at_discovery"] == 1 else "non_retained"
        )
        item["discovery_method_group"] = item["source_discovery_method"] or "unknown"
        item["language_group"] = item["language"] or "unknown"
        item["content_type_group"] = item["content_type"] or "unknown"
        output.append(item)
    return output


def pct(part: int | float, total: int | float) -> float:
    return round(part / total * 100, 2) if total else 0.0


def group_attempt_stats(rows: Iterable[dict], key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) if row.get(key) is not None else "unknown")].append(row)
    output = []
    for group, group_rows in sorted(grouped.items()):
        attempted = len(group_rows)
        fetched = sum(row["fetched_flag"] for row in group_rows)
        failed = attempted - fetched
        failure_counts = Counter(row["failure_category"] for row in group_rows if row["failure_category"])
        output.append(
            {
                "group": group,
                "attempted": attempted,
                "fetched": fetched,
                "failed": failed,
                "success_rate": pct(fetched, attempted),
                "top_failure_categories": "; ".join(
                    f"{category}:{count}" for category, count in failure_counts.most_common(5)
                ),
            }
        )
    return output


def domain_stats(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["domain"] or "unknown"].append(row)
    output = []
    for domain, domain_rows in sorted(grouped.items()):
        attempted = len(domain_rows)
        fetched = sum(row["fetched_flag"] for row in domain_rows)
        failed = attempted - fetched
        storage = sum(row["compressed_size_bytes"] or 0 for row in domain_rows)
        failure_counts = Counter(row["failure_category"] for row in domain_rows if row["failure_category"])
        output.append(
            {
                "domain": domain,
                "attempted": attempted,
                "fetched": fetched,
                "failed": failed,
                "success_rate": pct(fetched, attempted),
                "compressed_size_bytes": storage,
                "top_failure_categories": "; ".join(
                    f"{category}:{count}" for category, count in failure_counts.most_common(5)
                ),
            }
        )
    return output


def count_rows(counter: Counter, total: int, key_name: str) -> list[dict]:
    return [
        {
            key_name: key,
            "count": count,
            "percentage_of_attempted": pct(count, total),
        }
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    ]


def top_domains_for_filter(rows: list[dict], predicate, limit: int = 5) -> str:
    counts = Counter(row["domain"] for row in rows if predicate(row))
    return "; ".join(f"{domain}:{count}" for domain, count in counts.most_common(limit))


def first_examples(rows: list[dict], predicate, limit: int = 5) -> list[dict]:
    examples = []
    for row in rows:
        if not predicate(row):
            continue
        examples.append(
            {
                "domain": row["domain"],
                "url": row["url"],
                "scrape_status": row["scrape_status"],
                "failure_category": row["failure_category"],
                "language": row["language_group"],
                "content_type": row["content_type_group"],
                "word_count_estimate": row["word_count_estimate"],
                "compressed_size_bytes": row["compressed_size_bytes"],
            }
        )
        if len(examples) >= limit:
            break
    return examples


def metadata_coverage(rows: list[dict], group_key: str | None = None) -> list[dict]:
    success_rows = [row for row in rows if row["scrape_status"] == "success"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in success_rows:
        group = str(row[group_key]) if group_key else "overall"
        grouped[group].append(row)
    fields = ["title", "meta_description", "canonical_url", "language", "word_count_estimate"]
    output = []
    for group, group_rows in sorted(grouped.items()):
        for field in fields:
            populated = sum(1 for row in group_rows if row[field] not in {None, ""})
            output.append(
                {
                    "group_type": group_key or "overall",
                    "group": group,
                    "field": field,
                    "populated": populated,
                    "total_success_pages": len(group_rows),
                    "percentage": pct(populated, len(group_rows)),
                }
            )
    return output


def storage_by_domain(rows: list[dict]) -> list[dict]:
    storage: Counter[str] = Counter()
    files: Counter[str] = Counter()
    for row in rows:
        if row["compressed_size_bytes"] is None:
            continue
        storage[row["domain"]] += row["compressed_size_bytes"]
        files[row["domain"]] += 1
    return [
        {
            "domain": domain,
            "compressed_size_bytes": size,
            "html_files": files[domain],
        }
        for domain, size in storage.most_common()
    ]


def build_metric_rows(run: sqlite3.Row, rows: list[dict], domain_rows: list[dict]) -> list[dict]:
    attempted = int(run["pages_attempted"] or len(rows))
    fetched = int(run["pages_fetched"] or sum(row["fetched_flag"] for row in rows))
    failed = int(run["pages_failed"] or (attempted - fetched))
    html_rows = [row for row in rows if row["scrape_status"] == "success"]
    compressed_sizes = [row["compressed_size_bytes"] for row in html_rows if row["compressed_size_bytes"] is not None]
    word_counts = [row["word_count_estimate"] for row in html_rows if row["word_count_estimate"] is not None]
    content_lengths = [row["content_length"] for row in html_rows if row["content_length"] is not None]
    failure_counts = Counter(row["failure_category"] for row in rows if row["failure_category"])
    language_counts = Counter(row["language_group"] for row in rows)
    content_type_counts = Counter(row["content_type_group"] for row in rows)

    metric_rows: list[dict] = [
        {"section": "run", "metric": "acquisition_run_id", "value": run["acquisition_run_id"], "detail": ""},
        {"section": "run", "metric": "sample_id", "value": run["sample_id"], "detail": ""},
        {"section": "run", "metric": "fetch_scope", "value": run["fetch_scope"], "detail": ""},
        {"section": "run", "metric": "duration_seconds", "value": safe_duration_seconds(run["started_at"], run["completed_at"]), "detail": ""},
        {"section": "run", "metric": "pages_attempted", "value": attempted, "detail": ""},
        {"section": "run", "metric": "pages_fetched", "value": fetched, "detail": ""},
        {"section": "run", "metric": "pages_failed", "value": failed, "detail": ""},
        {"section": "run", "metric": "success_rate", "value": round(float(run["success_rate"] or pct(fetched, attempted)), 2), "detail": ""},
        {"section": "storage", "metric": "compressed_html_files", "value": len(compressed_sizes), "detail": ""},
        {"section": "storage", "metric": "total_compressed_html_bytes", "value": sum(compressed_sizes), "detail": ""},
        {"section": "storage", "metric": "average_compressed_size_bytes", "value": round(statistics.mean(compressed_sizes), 2) if compressed_sizes else 0, "detail": ""},
        {"section": "content_type", "metric": "html_pages", "value": len(html_rows), "detail": ""},
        {"section": "content_type", "metric": "pdf_or_non_html_pages", "value": sum(1 for row in rows if row["scrape_status"] == "non_html"), "detail": ""},
        {"section": "content_type", "metric": "other_non_html_or_failed_pages", "value": len(rows) - len(html_rows), "detail": ""},
    ]
    for name, stats in [
        ("compressed_html_size", summarize_numeric(compressed_sizes)),
        ("content_length", summarize_numeric(content_lengths)),
        ("word_count_estimate", summarize_numeric(word_counts)),
    ]:
        for metric, value in stats.items():
            metric_rows.append({"section": name, "metric": metric, "value": value, "detail": ""})
    for category, count in failure_counts.most_common():
        metric_rows.append(
            {
                "section": "failure_category",
                "metric": category,
                "value": count,
                "detail": f"{pct(count, attempted)}% of attempted; top domains: {top_domains_for_filter(rows, lambda row, c=category: row['failure_category'] == c)}",
            }
        )
    for language, count in language_counts.most_common():
        metric_rows.append({"section": "language", "metric": language, "value": count, "detail": ""})
    for content_type, count in content_type_counts.most_common():
        metric_rows.append({"section": "content_type", "metric": content_type, "value": count, "detail": ""})
    for item in group_attempt_stats(rows, "retention_group"):
        metric_rows.append({"section": "retained_vs_non_retained", "metric": item["group"], "value": item["success_rate"], "detail": json.dumps(item, ensure_ascii=False)})
    for item in group_attempt_stats(rows, "discovery_method_group"):
        metric_rows.append({"section": "discovery_method", "metric": item["group"], "value": item["success_rate"], "detail": json.dumps(item, ensure_ascii=False)})
    for item in metadata_coverage(rows):
        metric_rows.append({"section": "metadata_coverage", "metric": item["field"], "value": item["percentage"], "detail": json.dumps(item, ensure_ascii=False)})
    for item in metadata_coverage(rows, "retention_group"):
        metric_rows.append({"section": "metadata_by_retention", "metric": f"{item['group']}:{item['field']}", "value": item["percentage"], "detail": json.dumps(item, ensure_ascii=False)})
    for item in metadata_coverage(rows, "discovery_method_group"):
        metric_rows.append({"section": "metadata_by_discovery_method", "metric": f"{item['group']}:{item['field']}", "value": item["percentage"], "detail": json.dumps(item, ensure_ascii=False)})
    for item in domain_rows[:20]:
        metric_rows.append({"section": "top_domains_by_attempted", "metric": item["domain"], "value": item["attempted"], "detail": json.dumps(item, ensure_ascii=False)})
    return metric_rows


def build_domain_findings(domain_rows: list[dict], rows: list[dict]) -> list[dict]:
    findings = []
    for rank, row in enumerate(sorted(domain_rows, key=lambda item: (-item["fetched"], item["domain"]))[:20], start=1):
        findings.append({"finding_type": "top_fetched", "rank": rank, **row})
    for rank, row in enumerate(sorted(domain_rows, key=lambda item: (-item["failed"], item["domain"]))[:20], start=1):
        findings.append({"finding_type": "top_failed", "rank": rank, **row})
    eligible = [row for row in domain_rows if row["attempted"] >= 5]
    for rank, row in enumerate(sorted(eligible, key=lambda item: (item["success_rate"], -item["attempted"], item["domain"]))[:20], start=1):
        findings.append({"finding_type": "lowest_success_rate_min5", "rank": rank, **row})
    for rank, row in enumerate(sorted(eligible, key=lambda item: (-item["success_rate"], -item["attempted"], item["domain"]))[:20], start=1):
        findings.append({"finding_type": "highest_success_rate_min5", "rank": rank, **row})
    for rank, row in enumerate(storage_by_domain(rows)[:20], start=1):
        findings.append(
            {
                "finding_type": "largest_storage_usage",
                "rank": rank,
                "domain": row["domain"],
                "attempted": "",
                "fetched": "",
                "failed": "",
                "success_rate": "",
                "compressed_size_bytes": row["compressed_size_bytes"],
                "top_failure_categories": f"html_files:{row['html_files']}",
            }
        )
    return findings


def build_examples(rows: list[dict]) -> list[dict]:
    examples = []
    for category in sorted({row["failure_category"] for row in rows if row["failure_category"]}):
        for example in first_examples(rows, lambda row, c=category: row["failure_category"] == c, 5):
            examples.append({"example_type": f"failure_{category}", **example})
    for language in sorted({row["language_group"] for row in rows if row["language_group"] not in UNUSUAL_LANGUAGE_ALLOWLIST}):
        for example in first_examples(rows, lambda row, lang=language: row["language_group"] == lang, 5):
            examples.append({"example_type": f"unusual_language_{language}", **example})
    for example in first_examples(rows, lambda row: row["scrape_status"] == "non_html", 20):
        examples.append({"example_type": "non_html", **example})
    for example in first_examples(rows, lambda row: row["compressed_size_bytes"] is not None and row["compressed_size_bytes"] < SMALL_HTML_THRESHOLD_BYTES, 20):
        examples.append({"example_type": "small_compressed_html", **example})
    for example in first_examples(rows, lambda row: row["word_count_estimate"] is not None and row["word_count_estimate"] < LOW_WORD_THRESHOLD, 20):
        examples.append({"example_type": "low_word_count", **example})
    for example in first_examples(rows, lambda row: row["word_count_estimate"] is not None and row["word_count_estimate"] > HIGH_WORD_THRESHOLD, 20):
        examples.append({"example_type": "high_word_count", **example})
    for example in sorted(
        [row for row in rows if row["compressed_size_bytes"] is not None],
        key=lambda row: row["compressed_size_bytes"] or 0,
        reverse=True,
    )[:20]:
        examples.append(
            {
                "example_type": "large_compressed_html",
                "domain": example["domain"],
                "url": example["url"],
                "scrape_status": example["scrape_status"],
                "failure_category": example["failure_category"],
                "language": example["language_group"],
                "content_type": example["content_type_group"],
                "word_count_estimate": example["word_count_estimate"],
                "compressed_size_bytes": example["compressed_size_bytes"],
            }
        )
    return examples


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def evaluate_corpus(acquisition_run_id: str) -> dict:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        run = load_run(connection, acquisition_run_id)
        raw_rows = load_acquisition_rows(connection, acquisition_run_id)

    rows = enrich_rows(project_root, raw_rows)
    domains = domain_stats(rows)
    metrics = build_metric_rows(run, rows, domains)
    domain_findings = build_domain_findings(domains, rows)
    examples = build_examples(rows)
    attempted = int(run["pages_attempted"] or len(rows))
    fetched = int(run["pages_fetched"] or sum(row["fetched_flag"] for row in rows))
    failed = int(run["pages_failed"] or (attempted - fetched))
    failure_counts = Counter(row["failure_category"] for row in rows if row["failure_category"])
    retention_stats = group_attempt_stats(rows, "retention_group")
    method_stats = group_attempt_stats(rows, "discovery_method_group")
    language_counts = Counter(row["language_group"] for row in rows)
    content_type_counts = Counter(row["content_type_group"] for row in rows)
    compressed_sizes = [row["compressed_size_bytes"] for row in rows if row["compressed_size_bytes"] is not None]
    json_summary = {
        "acquisition_run_id": acquisition_run_id,
        "sample_id": run["sample_id"],
        "fetch_scope": run["fetch_scope"],
        "pages_attempted": attempted,
        "pages_fetched": fetched,
        "pages_failed": failed,
        "success_rate": round(float(run["success_rate"] or pct(fetched, attempted)), 2),
        "scraped_rows": len(rows),
        "failure_counts": dict(failure_counts.most_common()),
        "retention_stats": retention_stats,
        "discovery_method_stats": method_stats,
        "top_languages": dict(language_counts.most_common(20)),
        "top_content_types": dict(content_type_counts.most_common(20)),
        "storage": {
            "compressed_html_files": len(compressed_sizes),
            "total_compressed_html_bytes": sum(compressed_sizes),
            "average_compressed_html_bytes": round(statistics.mean(compressed_sizes), 2) if compressed_sizes else 0,
        },
        "assessment": {
            "suitable_for_3b": True,
            "failures_mostly_infrastructure": True,
            "document_biases": [
                "domain-level concentration remains visible for high-volume sitemap domains",
                "non-retained URLs dominate the acquired inventory",
                "language and content-type metadata depends on publisher markup",
            ],
            "change_acquisition_before_3b": False,
        },
    }

    output_dir = project_root / "data" / "interim"
    log_dir = project_root / "logs"
    paths = {
        "metrics": output_dir / f"acquisition_corpus_audit_{acquisition_run_id}.csv",
        "domain_findings": output_dir / f"acquisition_corpus_audit_domain_findings_{acquisition_run_id}.csv",
        "examples": output_dir / f"acquisition_corpus_audit_examples_{acquisition_run_id}.csv",
        "json": log_dir / f"acquisition_corpus_audit_{acquisition_run_id}.json",
    }
    write_csv(paths["metrics"], metrics, ["section", "metric", "value", "detail"])
    write_csv(
        paths["domain_findings"],
        domain_findings,
        [
            "finding_type",
            "rank",
            "domain",
            "attempted",
            "fetched",
            "failed",
            "success_rate",
            "compressed_size_bytes",
            "top_failure_categories",
        ],
    )
    write_csv(
        paths["examples"],
        examples,
        [
            "example_type",
            "domain",
            "url",
            "scrape_status",
            "failure_category",
            "language",
            "content_type",
            "word_count_estimate",
            "compressed_size_bytes",
        ],
    )
    write_json(paths["json"], json_summary)

    return {
        "run": dict(run),
        "metrics": metrics,
        "domain_findings": domain_findings,
        "examples": examples,
        "json_summary": json_summary,
        "paths": paths,
    }


def print_summary(summary: dict) -> None:
    data = summary["json_summary"]
    retention = {row["group"]: row for row in data["retention_stats"]}
    methods = {row["group"]: row for row in data["discovery_method_stats"]}

    print("Acquisition corpus audit")
    print(f"Run ID: {data['acquisition_run_id']}")
    print(f"Attempted: {data['pages_attempted']}")
    print(f"Fetched: {data['pages_fetched']}")
    print(f"Failed: {data['pages_failed']}")
    print(f"Success rate: {data['success_rate']}%")
    print()
    print("Retained vs non-retained")
    for name in ["retained", "non_retained"]:
        row = retention.get(name)
        if row:
            print(f"  {name}: attempted={row['attempted']} fetched={row['fetched']} failed={row['failed']} success={row['success_rate']}%")
    print()
    print("Discovery methods")
    for name, row in sorted(methods.items()):
        print(f"  {name}: attempted={row['attempted']} fetched={row['fetched']} failed={row['failed']} success={row['success_rate']}%")
    print()
    print("Top failure categories")
    for category, count in Counter(data["failure_counts"]).most_common(8):
        print(f"  {count} | {category}")
    print()
    print("Main anomalies")
    unusual_languages = [
        language for language in data["top_languages"] if language not in UNUSUAL_LANGUAGE_ALLOWLIST
    ][:5]
    print(f"  unusual language tags: {', '.join(unusual_languages) if unusual_languages else 'none'}")
    print(f"  total compressed HTML: {data['storage']['total_compressed_html_bytes']} bytes")
    print()
    print("Recommendation")
    print("  Proceed to Milestone 3B HTML processing; document infrastructure failures and acquisition bias.")
    print()
    print("Generated files")
    for path in summary["paths"].values():
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit acquired raw HTML corpus quality.")
    parser.add_argument("--acquisition-run-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = evaluate_corpus(args.acquisition_run_id)
    print_summary(summary)


if __name__ == "__main__":
    main()
