"""Read-only QA reports for extracted evidence packages."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


REPORT_FILENAMES = {
    "summary": "extraction_summary.csv",
    "word_statistics": "extraction_word_statistics.csv",
    "heading_statistics": "extraction_heading_statistics.csv",
    "link_statistics": "extraction_link_statistics.csv",
    "language_statistics": "extraction_language_statistics.csv",
    "html_vs_text": "extraction_html_vs_text.csv",
    "short_pages": "extraction_short_pages.csv",
    "large_pages": "extraction_large_pages.csv",
    "duplicate_content": "extraction_duplicate_content.csv",
    "json": "extraction_quality_report.json",
}


@dataclass(frozen=True)
class ExtractionPage:
    extracted_page_id: int
    scraped_page_id: int
    domain: str | None
    url: str
    html_path: str | None
    title: str | None
    extraction_version: str
    extraction_run_id: str | None
    markdown_path: str | None
    text_path: str | None
    metadata_path: str | None
    content_sha256: str | None
    language: str | None
    word_count: int
    heading_count: int
    page_type_guess: str | None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_label(value: str | None) -> str:
    if not value:
        return "unknown"
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def resolve_path(project_root: Path, path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = project_root / path
    return path


def file_text_length(project_root: Path, path_value: str | None) -> int | None:
    path = resolve_path(project_root, path_value)
    if path is None or not path.is_file():
        return None
    return len(path.read_text(encoding="utf-8", errors="replace"))


def html_text_length(project_root: Path, path_value: str | None) -> int | None:
    path = resolve_path(project_root, path_value)
    if path is None or not path.is_file():
        return None
    try:
        with gzip.open(path, "rb") as file:
            return len(file.read().decode("utf-8", errors="replace"))
    except (OSError, gzip.BadGzipFile, UnicodeDecodeError):
        return None


def pct(part: int | float, total: int | float) -> float:
    return round((float(part) / float(total) * 100), 2) if total else 0.0


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * p
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[int(index)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def numeric_statistics(values: list[int | float]) -> dict[str, float | int | None]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
            "p5": None,
            "p25": None,
            "p75": None,
            "p95": None,
        }
    return {
        "count": len(numeric),
        "mean": round(mean(numeric), 2),
        "median": round(median(numeric), 2),
        "std": round(pstdev(numeric), 2) if len(numeric) > 1 else 0.0,
        "min": round(min(numeric), 2),
        "max": round(max(numeric), 2),
        "p5": round(float(percentile(numeric, 0.05)), 2),
        "p25": round(float(percentile(numeric, 0.25)), 2),
        "p75": round(float(percentile(numeric, 0.75)), 2),
        "p95": round(float(percentile(numeric, 0.95)), 2),
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def latest_successful_extraction_context(
    connection: sqlite3.Connection,
    sample_id: str,
) -> tuple[str | None, str | None]:
    run = connection.execute(
        """
        SELECT extraction_run_id, extraction_version
        FROM extraction_runs
        WHERE sample_id = ?
          AND COALESCE(successful_pages, 0) > 0
          AND finished_at IS NOT NULL
        ORDER BY finished_at DESC, started_at DESC
        LIMIT 1
        """,
        (sample_id,),
    ).fetchone()
    if run:
        return run["extraction_run_id"], run["extraction_version"]

    version = connection.execute(
        """
        SELECT e.extraction_version
        FROM retention_validation_sample AS rvs
        JOIN extracted_pages AS e ON e.scraped_page_id = rvs.scraped_page_id
        WHERE rvs.sample_id = ?
          AND e.extraction_status = 'success'
        ORDER BY e.extracted_at DESC
        LIMIT 1
        """,
        (sample_id,),
    ).fetchone()
    return None, version["extraction_version"] if version else None


def load_run_summary(
    connection: sqlite3.Connection,
    sample_id: str,
    extraction_version: str | None,
    run_id: str | None,
) -> dict[str, object]:
    if run_id:
        row = connection.execute(
            """
            SELECT *
            FROM extraction_runs
            WHERE extraction_run_id = ?
            """,
            (run_id,),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT *
            FROM extraction_runs
            WHERE sample_id = ?
              AND (? IS NULL OR extraction_version = ?)
            ORDER BY finished_at DESC, started_at DESC
            LIMIT 1
            """,
            (sample_id, extraction_version, extraction_version),
        ).fetchone()
    if not row:
        return {
            "sample": sample_id,
            "extraction_version": extraction_version,
            "run_id": run_id,
            "pages_selected": 0,
            "pages_processed": 0,
            "successful": 0,
            "skipped": 0,
            "failed": 0,
            "success_rate": 0.0,
        }
    processed = int(row["processed_pages"] or 0)
    successful = int(row["successful_pages"] or 0)
    return {
        "sample": sample_id,
        "extraction_version": row["extraction_version"],
        "run_id": row["extraction_run_id"],
        "pages_selected": int(row["selected_pages"] or 0),
        "pages_processed": processed,
        "successful": successful,
        "skipped": int(row["skipped_pages"] or 0),
        "failed": int(row["failed_pages"] or 0),
        "success_rate": pct(successful, processed),
    }


def load_pages(
    connection: sqlite3.Connection,
    sample_id: str,
    extraction_version: str | None,
    run_id: str | None,
    domain: str | None,
) -> list[ExtractionPage]:
    rows = connection.execute(
        """
        SELECT
            e.extracted_page_id,
            e.scraped_page_id,
            s.domain,
            s.url,
            s.html_path,
            COALESCE(pf.title, s.title) AS title,
            e.extraction_version,
            e.extraction_run_id,
            e.markdown_path,
            e.text_path,
            e.metadata_path,
            e.content_sha256,
            COALESCE(pf.language, e.language, s.language) AS language,
            COALESCE(pf.word_count, 0) AS word_count,
            COALESCE(pf.heading_count, 0) AS heading_count,
            pf.page_type_guess
        FROM retention_validation_sample AS rvs
        JOIN scraped_pages AS s ON s.scraped_page_id = rvs.scraped_page_id
        JOIN extracted_pages AS e ON e.scraped_page_id = s.scraped_page_id
        LEFT JOIN page_features AS pf ON pf.extracted_page_id = e.extracted_page_id
        WHERE rvs.sample_id = ?
          AND e.extraction_status = 'success'
          AND (? IS NULL OR e.extraction_version = ?)
          AND (? IS NULL OR e.extraction_run_id = ?)
          AND (? IS NULL OR s.domain = ?)
        ORDER BY s.domain, s.url, e.scraped_page_id
        """,
        (sample_id, extraction_version, extraction_version, run_id, run_id, domain, domain),
    ).fetchall()
    return [
        ExtractionPage(
            extracted_page_id=int(row["extracted_page_id"]),
            scraped_page_id=int(row["scraped_page_id"]),
            domain=row["domain"],
            url=row["url"],
            html_path=row["html_path"],
            title=row["title"],
            extraction_version=row["extraction_version"],
            extraction_run_id=row["extraction_run_id"],
            markdown_path=row["markdown_path"],
            text_path=row["text_path"],
            metadata_path=row["metadata_path"],
            content_sha256=row["content_sha256"],
            language=row["language"],
            word_count=int(row["word_count"] or 0),
            heading_count=int(row["heading_count"] or 0),
            page_type_guess=row["page_type_guess"],
        )
        for row in rows
    ]


def link_counts_by_page(connection: sqlite3.Connection, page_ids: list[int]) -> dict[int, dict[str, int | float]]:
    if not page_ids:
        return {}
    placeholders = ",".join("?" for _ in page_ids)
    rows = connection.execute(
        f"""
        SELECT
            extracted_page_id,
            COUNT(*) AS total_links,
            SUM(CASE WHEN is_internal = 1 THEN 1 ELSE 0 END) AS internal_links,
            SUM(CASE WHEN is_internal = 0 THEN 1 ELSE 0 END) AS external_links,
            AVG(COALESCE(anchor_word_count, 0)) AS average_anchor_words,
            MAX(COALESCE(anchor_word_count, 0)) AS max_anchor_words
        FROM page_links
        WHERE extracted_page_id IN ({placeholders})
        GROUP BY extracted_page_id
        """,
        page_ids,
    ).fetchall()
    return {
        int(row["extracted_page_id"]): {
            "total_links": int(row["total_links"] or 0),
            "internal_links": int(row["internal_links"] or 0),
            "external_links": int(row["external_links"] or 0),
            "average_anchor_words": round(float(row["average_anchor_words"] or 0), 2),
            "max_anchor_words": int(row["max_anchor_words"] or 0),
        }
        for row in rows
    }


def build_output_directory(
    project_root: Path,
    output_directory: Path | None,
    sample_id: str,
    extraction_version: str | None,
    run_id: str | None,
) -> Path:
    root = output_directory or project_root / "data" / "interim" / "extraction_qa"
    if not root.is_absolute():
        root = project_root / root
    label = f"extraction_{safe_label(extraction_version)}_{safe_label(sample_id)}"
    if run_id:
        label = f"{label}_{safe_label(run_id)}"
    candidate = root / label
    if not candidate.exists():
        return candidate
    return root / f"{label}_{utc_timestamp()}"


def build_html_text_rows(
    project_root: Path,
    pages: list[ExtractionPage],
    links: dict[int, dict[str, int | float]],
) -> list[dict]:
    rows: list[dict] = []
    for page in pages:
        html_chars = html_text_length(project_root, page.html_path)
        markdown_chars = file_text_length(project_root, page.markdown_path)
        text_chars = file_text_length(project_root, page.text_path)
        text_ratio = round(text_chars / html_chars, 4) if html_chars and text_chars is not None else None
        markdown_ratio = round(markdown_chars / html_chars, 4) if html_chars and markdown_chars is not None else None
        warnings: list[str] = []
        if markdown_chars in {0, None}:
            warnings.append("empty_markdown")
        if text_chars in {0, None}:
            warnings.append("empty_plain_text")
        if page.word_count == 0:
            warnings.append("zero_word_extraction")
        if html_chars and text_chars is not None and html_chars > 5000 and text_ratio is not None and text_ratio < 0.02:
            warnings.append("html_much_larger_than_text")
        if text_chars is not None and text_chars > 250000:
            warnings.append("suspiciously_large_extraction")
        link_count = int(links.get(page.extracted_page_id, {}).get("total_links", 0))
        if link_count > 500:
            warnings.append("extremely_high_link_count")
        rows.append(
            {
                "domain": page.domain,
                "url": page.url,
                "title": page.title,
                "html_chars": html_chars,
                "markdown_chars": markdown_chars,
                "plain_text_chars": text_chars,
                "markdown_to_html_ratio": markdown_ratio,
                "text_to_html_ratio": text_ratio,
                "word_count": page.word_count,
                "total_links": link_count,
                "warnings": ";".join(warnings),
            }
        )
    return rows


def duplicate_content_rows(pages: list[ExtractionPage]) -> list[dict]:
    grouped: dict[str, list[ExtractionPage]] = defaultdict(list)
    for page in pages:
        if page.content_sha256:
            grouped[page.content_sha256].append(page)
    rows: list[dict] = []
    for sha256, duplicate_pages in grouped.items():
        if len(duplicate_pages) < 2:
            continue
        rows.append(
            {
                "content_sha256": sha256,
                "number_of_pages": len(duplicate_pages),
                "domains": "; ".join(sorted({page.domain or "" for page in duplicate_pages if page.domain})),
                "urls": "; ".join(page.url for page in duplicate_pages[:25]),
            }
        )
    return sorted(rows, key=lambda row: int(row["number_of_pages"]), reverse=True)


def build_warning_summary(
    pages: list[ExtractionPage],
    html_text_rows: list[dict],
    duplicates: list[dict],
) -> dict[str, int]:
    warnings = Counter()
    warnings["pages_without_headings"] = sum(1 for page in pages if page.heading_count == 0)
    for row in html_text_rows:
        for warning in str(row.get("warnings") or "").split(";"):
            if warning:
                warnings[warning] += 1
    warnings["duplicate_content_groups"] = len(duplicates)
    return dict(warnings)


def analyze_extraction_quality(
    *,
    sample_id: str,
    extraction_version: str | None = None,
    run_id: str | None = None,
    domain: str | None = None,
    output_directory: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    root = project_root or find_project_root()
    config = load_config(root)
    db_path = resolve_database_path(root, config)

    with connect_read_only(db_path) as connection:
        if extraction_version is None and run_id is None:
            latest_run_id, latest_version = latest_successful_extraction_context(connection, sample_id)
            run_id = latest_run_id
            extraction_version = latest_version

        run_summary = load_run_summary(connection, sample_id, extraction_version, run_id)
        pages = load_pages(connection, sample_id, extraction_version, run_id, domain)
        links = link_counts_by_page(connection, [page.extracted_page_id for page in pages])

    output_dir = build_output_directory(root, output_directory, sample_id, extraction_version, run_id)
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {key: output_dir / filename for key, filename in REPORT_FILENAMES.items()}

    words = [page.word_count for page in pages]
    headings = [page.heading_count for page in pages]
    total_links = [int(links.get(page.extracted_page_id, {}).get("total_links", 0)) for page in pages]
    internal_links = [int(links.get(page.extracted_page_id, {}).get("internal_links", 0)) for page in pages]
    external_links = [int(links.get(page.extracted_page_id, {}).get("external_links", 0)) for page in pages]
    anchor_lengths = []
    for page in pages:
        link_data = links.get(page.extracted_page_id, {})
        if link_data:
            anchor_lengths.append(float(link_data.get("average_anchor_words", 0)))

    html_text_rows = build_html_text_rows(root, pages, links)
    duplicates = duplicate_content_rows(pages)
    warnings = build_warning_summary(pages, html_text_rows, duplicates)

    run_summary = {
        **run_summary,
        "domain_filter": domain,
        "pages_analyzed": len(pages),
        "successful": len(pages) if len(pages) > int(run_summary.get("successful") or 0) else run_summary["successful"],
    }

    summary_rows = [run_summary]
    word_stats = [{"metric": metric, "value": value} for metric, value in numeric_statistics(words).items()]

    heading_distribution = Counter(headings)
    heading_rows: list[dict] = [
        {"section": "summary", "metric": "average_heading_count", "value": numeric_statistics(headings)["mean"], "domain": "", "url": "", "heading_count": ""},
        {"section": "summary", "metric": "pages_with_zero_headings", "value": heading_distribution.get(0, 0), "domain": "", "url": "", "heading_count": ""},
    ]
    heading_rows.extend(
        {
            "section": "distribution",
            "metric": "heading_count",
            "value": count,
            "domain": "",
            "url": "",
            "heading_count": heading_count,
        }
        for heading_count, count in sorted(heading_distribution.items())
    )
    heading_rows.extend(
        {
            "section": "largest_heading_counts",
            "metric": "page",
            "value": "",
            "domain": page.domain,
            "url": page.url,
            "heading_count": page.heading_count,
        }
        for page in sorted(pages, key=lambda item: item.heading_count, reverse=True)[:25]
    )

    domain_link_totals: dict[str, int] = defaultdict(int)
    for page in pages:
        domain_link_totals[page.domain or "unknown"] += int(links.get(page.extracted_page_id, {}).get("total_links", 0))
    link_rows = [
        {"section": "summary", "metric": "total_links", "value": sum(total_links), "domain": "", "url": ""},
        {"section": "summary", "metric": "internal_links", "value": sum(internal_links), "domain": "", "url": ""},
        {"section": "summary", "metric": "external_links", "value": sum(external_links), "domain": "", "url": ""},
        {"section": "summary", "metric": "average_internal_links", "value": numeric_statistics(internal_links)["mean"], "domain": "", "url": ""},
        {"section": "summary", "metric": "average_external_links", "value": numeric_statistics(external_links)["mean"], "domain": "", "url": ""},
        {"section": "summary", "metric": "average_anchor_words", "value": numeric_statistics(anchor_lengths)["mean"], "domain": "", "url": ""},
    ]
    link_rows.extend(
        {"section": "domain_link_totals", "metric": "total_links", "value": count, "domain": domain_name, "url": ""}
        for domain_name, count in sorted(domain_link_totals.items(), key=lambda item: item[1], reverse=True)[:25]
    )

    language_counts = Counter(page.language or "unknown" for page in pages)
    language_rows = [
        {"language": language, "count": count, "percentage": pct(count, len(pages))}
        for language, count in sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    short_pages = [
        {
            "domain": page.domain,
            "url": page.url,
            "title": page.title,
            "word_count": page.word_count,
            "heading_count": page.heading_count,
            "markdown_path": page.markdown_path,
            "text_path": page.text_path,
            "category": "empty" if page.word_count == 0 else "under_50_words" if page.word_count < 50 else "under_100_words",
        }
        for page in pages
        if page.word_count < 100
    ]
    large_pages = [
        {
            "domain": page.domain,
            "url": page.url,
            "word_count": page.word_count,
            "heading_count": page.heading_count,
            "internal_links": int(links.get(page.extracted_page_id, {}).get("internal_links", 0)),
            "external_links": int(links.get(page.extracted_page_id, {}).get("external_links", 0)),
        }
        for page in sorted(pages, key=lambda item: item.word_count, reverse=True)[:50]
    ]

    write_csv(paths["summary"], summary_rows, list(summary_rows[0].keys()))
    write_csv(paths["word_statistics"], word_stats, ["metric", "value"])
    write_csv(paths["heading_statistics"], heading_rows, ["section", "metric", "value", "domain", "url", "heading_count"])
    write_csv(paths["link_statistics"], link_rows, ["section", "metric", "value", "domain", "url"])
    write_csv(paths["language_statistics"], language_rows, ["language", "count", "percentage"])
    write_csv(
        paths["html_vs_text"],
        html_text_rows,
        [
            "domain",
            "url",
            "title",
            "html_chars",
            "markdown_chars",
            "plain_text_chars",
            "markdown_to_html_ratio",
            "text_to_html_ratio",
            "word_count",
            "total_links",
            "warnings",
        ],
    )
    write_csv(paths["short_pages"], short_pages, ["domain", "url", "title", "word_count", "heading_count", "markdown_path", "text_path", "category"])
    write_csv(paths["large_pages"], large_pages, ["domain", "url", "word_count", "heading_count", "internal_links", "external_links"])
    write_csv(paths["duplicate_content"], duplicates, ["content_sha256", "number_of_pages", "domains", "urls"])

    report = {
        "sample": sample_id,
        "extraction_version": extraction_version,
        "run_id": run_id,
        "domain_filter": domain,
        "summary": run_summary,
        "word_statistics": numeric_statistics(words),
        "heading_statistics": {
            "average_heading_count": numeric_statistics(headings)["mean"],
            "pages_with_zero_headings": heading_distribution.get(0, 0),
            "distribution": dict(sorted(heading_distribution.items())),
        },
        "link_statistics": {
            "total_links": sum(total_links),
            "internal_links": sum(internal_links),
            "external_links": sum(external_links),
            "average_internal_links": numeric_statistics(internal_links)["mean"],
            "average_external_links": numeric_statistics(external_links)["mean"],
            "average_anchor_words": numeric_statistics(anchor_lengths)["mean"],
        },
        "languages": language_rows,
        "warnings": warnings,
        "representative_examples": {
            "short_pages": short_pages[:10],
            "large_pages": large_pages[:10],
            "duplicate_content": duplicates[:10],
        },
        "generated_report_paths": {key: str(path) for key, path in paths.items()},
    }
    write_json(paths["json"], report)
    print_console_summary(report, output_dir)
    return report


def print_console_summary(report: dict[str, object], output_dir: Path) -> None:
    summary = report["summary"]
    words = report["word_statistics"]
    headings = report["heading_statistics"]
    links = report["link_statistics"]
    warnings = report["warnings"]
    print("Extraction QA Summary")
    print(f"Sample: {report['sample']}")
    print(f"Extraction version: {report['extraction_version']}")
    print(f"Run: {report['run_id']}")
    print(f"Pages analyzed: {summary['pages_analyzed']}")
    print(f"Successful: {summary['successful']}")
    print(f"Skipped: {summary['skipped']}")
    print(f"Failed: {summary['failed']}")
    print(f"Average words: {words['mean']}")
    print(f"Median words: {words['median']}")
    print(f"Average headings: {headings['average_heading_count']}")
    print(f"Average internal links: {links['average_internal_links']}")
    print(f"Average external links: {links['average_external_links']}")
    print(f"Duplicate content groups: {warnings.get('duplicate_content_groups', 0)}")
    print(f"Warnings: {warnings}")
    print(f"Reports written to: {output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze extraction quality without modifying extraction outputs.")
    parser.add_argument("--sample", required=True, help="Validation sample ID, such as smoke_v1 or main_v1.")
    parser.add_argument("--extraction-version", help="Extraction version to analyze.")
    parser.add_argument("--run-id", help="Specific extraction_run_id to analyze.")
    parser.add_argument("--domain", help="Restrict analysis to one domain.")
    parser.add_argument("--output-directory", type=Path, help="Directory root for QA reports.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    analyze_extraction_quality(
        sample_id=args.sample,
        extraction_version=args.extraction_version,
        run_id=args.run_id,
        domain=args.domain,
        output_directory=args.output_directory,
    )


if __name__ == "__main__":
    main()
