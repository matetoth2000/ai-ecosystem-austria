"""Build visible-text previews for validation samples.

This is a lightweight validation-only extraction layer. It is not the final
HTML processing pipeline and does not produce Markdown.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from bs4 import BeautifulSoup

from ai_ecosystem.db.init_db import (
    ensure_validation_schema,
    find_project_root,
    load_config,
    resolve_database_path,
    resolve_project_path,
)
from ai_ecosystem.validation.build_validation_sample import resolve_html_path
from ai_ecosystem.validation.config import load_validation_config


NON_VISIBLE_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "template",
    "canvas",
    "iframe",
    "object",
    "embed",
)

CSV_COLUMNS = [
    "sample_id",
    "domain",
    "url",
    "retained_at_discovery",
    "page_type_guess",
    "visible_text_length",
    "preview_char_count",
]


@dataclass(frozen=True)
class SamplePage:
    sample_id: str
    scraped_page_id: int
    discovered_page_id: int
    domain: str
    url: str
    title: str | None
    meta_description: str | None
    page_type_guess: str
    retained_at_discovery: int
    html_path: Path


@dataclass(frozen=True)
class InputResult:
    page: SamplePage
    visible_text_length: int
    preview_char_count: int
    visible_text_preview: str


def collapse_visible_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n\n".join(lines)


def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in NON_VISIBLE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    return collapse_visible_text(soup.get_text(separator="\n"))


def read_html_gzip(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as file:
        return file.read()


def percentile(values: list[int], percent: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = index - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def load_sample_pages(
    connection: sqlite3.Connection,
    project_root: Path,
    sample_id: str,
    *,
    force_refresh: bool,
) -> list[SamplePage]:
    existing_filter = ""
    if not force_refresh:
        existing_filter = """
          AND NOT EXISTS (
              SELECT 1
              FROM retention_validation_inputs AS existing
              WHERE existing.sample_id = rvs.sample_id
                AND existing.scraped_page_id = rvs.scraped_page_id
          )
        """
    rows = connection.execute(
        f"""
        SELECT
            rvs.sample_id,
            rvs.scraped_page_id,
            rvs.discovered_page_id,
            rvs.domain,
            rvs.url,
            sp.title,
            sp.meta_description,
            COALESCE(rvs.page_type_guess, 'unknown'),
            rvs.retained_at_discovery,
            sp.html_path
        FROM retention_validation_sample AS rvs
        INNER JOIN scraped_pages AS sp
            ON sp.scraped_page_id = rvs.scraped_page_id
        WHERE rvs.sample_id = ?
          AND sp.html_path IS NOT NULL
          AND sp.html_path != ''
        {existing_filter}
        ORDER BY rvs.retained_at_discovery DESC, rvs.domain, rvs.scraped_page_id
        """,
        (sample_id,),
    ).fetchall()
    pages: list[SamplePage] = []
    for row in rows:
        html_path = resolve_html_path(project_root, row[9])
        pages.append(
            SamplePage(
                sample_id=row[0],
                scraped_page_id=row[1],
                discovered_page_id=row[2],
                domain=row[3],
                url=row[4],
                title=row[5],
                meta_description=row[6],
                page_type_guess=row[7],
                retained_at_discovery=row[8],
                html_path=html_path,
            )
        )
    return pages


def upsert_input_result(connection: sqlite3.Connection, result: InputResult, *, force_refresh: bool) -> None:
    if force_refresh:
        connection.execute(
            """
            DELETE FROM retention_validation_inputs
            WHERE sample_id = ? AND scraped_page_id = ?
            """,
            (result.page.sample_id, result.page.scraped_page_id),
        )
    connection.execute(
        """
        INSERT OR IGNORE INTO retention_validation_inputs (
            sample_id,
            scraped_page_id,
            discovered_page_id,
            domain,
            url,
            title,
            meta_description,
            page_type_guess,
            retained_at_discovery,
            visible_text_length,
            preview_char_count,
            visible_text_preview
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.page.sample_id,
            result.page.scraped_page_id,
            result.page.discovered_page_id,
            result.page.domain,
            result.page.url,
            result.page.title,
            result.page.meta_description,
            result.page.page_type_guess,
            result.page.retained_at_discovery,
            result.visible_text_length,
            result.preview_char_count,
            result.visible_text_preview,
        ),
    )


def write_inputs_csv(connection: sqlite3.Connection, csv_path: Path, sample_id: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = connection.execute(
        f"""
        SELECT {", ".join(CSV_COLUMNS)}
        FROM retention_validation_inputs
        WHERE sample_id = ?
        ORDER BY retained_at_discovery DESC, domain, scraped_page_id
        """,
        (sample_id,),
    ).fetchall()
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)


def example_rows(
    connection: sqlite3.Connection,
    sample_id: str,
    *,
    limit: int = 3,
) -> list[tuple[str, str, str]]:
    return connection.execute(
        """
        SELECT domain, url, visible_text_preview
        FROM retention_validation_inputs
        WHERE sample_id = ?
        ORDER BY retained_at_discovery DESC, domain, scraped_page_id
        LIMIT ?
        """,
        (sample_id, limit),
    ).fetchall()


def build_validation_inputs(
    sample_id: str,
    *,
    project_root: Path | None = None,
    force_refresh: bool = False,
) -> dict[str, object]:
    root = project_root or find_project_root()
    config = load_config(root)
    validation = load_validation_config(root)
    max_input_chars = int(validation.get("max_input_chars", 2000))
    interim_dir = resolve_project_path(root, config.get("paths", {}).get("interim_dir", "data/interim"))
    csv_path = interim_dir / f"validation_inputs_{sample_id}.csv"
    db_path = resolve_database_path(root, config)

    processed = 0
    skipped = 0
    extraction_failures: list[tuple[SamplePage, str]] = []
    empty_pages: list[SamplePage] = []
    results: list[InputResult] = []

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_validation_schema(connection)
        total_sample_pages = connection.execute(
            "SELECT COUNT(*) FROM retention_validation_sample WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()[0]
        if total_sample_pages == 0:
            raise ValueError(f"No validation sample rows found for sample_id='{sample_id}'")
        if force_refresh:
            connection.execute(
                "DELETE FROM retention_validation_inputs WHERE sample_id = ?",
                (sample_id,),
            )
        pages = load_sample_pages(connection, root, sample_id, force_refresh=force_refresh)
        existing_count = connection.execute(
            "SELECT COUNT(*) FROM retention_validation_inputs WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()[0]
        skipped = existing_count if not force_refresh else 0

        for page in pages:
            try:
                if not page.html_path.is_file():
                    raise FileNotFoundError(f"Missing HTML file: {page.html_path}")
                visible_text = extract_visible_text(read_html_gzip(page.html_path))
                preview = visible_text[:max_input_chars]
                result = InputResult(
                    page=page,
                    visible_text_length=len(visible_text),
                    preview_char_count=len(preview),
                    visible_text_preview=preview,
                )
                upsert_input_result(connection, result, force_refresh=force_refresh)
                results.append(result)
                processed += 1
                if len(visible_text) == 0:
                    empty_pages.append(page)
            except Exception as exc:  # noqa: BLE001 - diagnostics should not abort the whole sample.
                extraction_failures.append((page, str(exc)))

        connection.commit()
        write_inputs_csv(connection, csv_path, sample_id)
        examples = example_rows(connection, sample_id)

    lengths = [result.visible_text_length for result in results]
    preview_lengths = [result.preview_char_count for result in results]
    summary = {
        "sample_id": sample_id,
        "pages_processed": processed,
        "pages_skipped": skipped,
        "empty_extraction_count": len(empty_pages),
        "extraction_failure_count": len(extraction_failures),
        "average_visible_text_length": round(mean(lengths), 2) if lengths else 0,
        "median_visible_text_length": median(lengths) if lengths else 0,
        "p95_visible_text_length": round(percentile(lengths, 0.95), 2),
        "average_preview_length": round(mean(preview_lengths), 2) if preview_lengths else 0,
        "shortest_pages": sorted(
            [(result.visible_text_length, result.page.url) for result in results],
            key=lambda item: item[0],
        )[:5],
        "longest_pages": sorted(
            [(result.visible_text_length, result.page.url) for result in results],
            key=lambda item: item[0],
            reverse=True,
        )[:5],
        "empty_examples": [page.url for page in empty_pages[:5]],
        "failure_examples": [(page.url, message) for page, message in extraction_failures[:5]],
        "csv_path": str(csv_path),
        "examples": [(domain, url, preview[:200]) for domain, url, preview in examples],
    }
    print_summary(summary)
    return summary


def print_summary(summary: dict[str, object]) -> None:
    print(f"Validation inputs: {summary['sample_id']}")
    print(f"Pages processed: {summary['pages_processed']}")
    print(f"Pages skipped: {summary['pages_skipped']}")
    print(f"Average visible text length: {summary['average_visible_text_length']}")
    print(f"Median visible text length: {summary['median_visible_text_length']}")
    print(f"P95 visible text length: {summary['p95_visible_text_length']}")
    print(f"Average preview length: {summary['average_preview_length']}")
    print(f"Empty extraction count: {summary['empty_extraction_count']}")
    print(f"Extraction failure count: {summary['extraction_failure_count']}")
    print(f"Shortest pages: {summary['shortest_pages']}")
    print(f"Longest pages: {summary['longest_pages']}")
    print(f"Output CSV: {summary['csv_path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build visible-text validation inputs.")
    parser.add_argument("--sample", required=True, help="Validation sample_id, e.g. smoke_v1 or main_v1")
    parser.add_argument("--force-refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_validation_inputs(args.sample, force_refresh=args.force_refresh)


if __name__ == "__main__":
    main()
