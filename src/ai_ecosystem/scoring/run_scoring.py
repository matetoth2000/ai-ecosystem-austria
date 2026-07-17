"""Run corpus scoring over extracted evidence packages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from ai_ecosystem.db.init_db import find_project_root, init_database, load_config, resolve_database_path
from ai_ecosystem.scoring.config import (
    KnowledgeBaseConfig,
    ScoringProfileConfig,
    validate_scoring_foundation,
)
from ai_ecosystem.scoring.engine import ContentScorer, DimensionScoringResult, MatchContribution, PageScoringInput


@dataclass(frozen=True)
class ScoringCandidate:
    extracted_page_id: int
    scraped_page_id: int
    extraction_version: str
    domain: str | None
    url: str
    markdown_path: str
    text_path: str
    metadata_path: str | None
    title: str | None
    headings_json: str | None


@dataclass(frozen=True)
class ScoredPageSummary:
    extracted_page_id: int
    scraped_page_id: int
    domain: str | None
    url: str
    dimension: str
    query_score: float
    keyword_score: float
    boost_score: float
    negative_score: float
    final_score: float
    top_queries_json: str
    top_keywords_json: str
    top_boost_terms_json: str
    top_negative_terms_json: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def generate_scoring_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"scoring_{timestamp}_{uuid.uuid4().hex[:8]}"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def configuration_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_path(project_root: Path, path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = project_root / path
    return path


def read_required_text(project_root: Path, path_value: str, label: str) -> str:
    path = resolve_path(project_root, path_value)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"missing {label}: {path_value}")
    return path.read_text(encoding="utf-8", errors="replace")


def read_optional_metadata(project_root: Path, path_value: str | None, warnings: list[str]) -> dict[str, Any]:
    path = resolve_path(project_root, path_value)
    if path is None:
        return {}
    if not path.is_file():
        warnings.append(f"metadata_missing:{path_value}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        warnings.append(f"metadata_unreadable:{path_value}:{error}")
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_headings(value: str | None, warnings: list[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        warnings.append(f"headings_unreadable:{error}")
        return []
    if not isinstance(parsed, list):
        warnings.append("headings_not_list")
        return []
    headings: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            headings.append(item)
        elif isinstance(item, dict) and item.get("text"):
            headings.append(str(item["text"]))
    return headings


def match_to_dict(match: MatchContribution) -> dict[str, Any]:
    return {
        "value": match.value,
        "score": match.score,
        "matched_fields": match.matched_fields,
        "matched_terms": list(match.matched_terms),
        "source_node": match.source_node,
        "language": match.language,
    }


def matches_json(matches: tuple[MatchContribution, ...]) -> str:
    return json.dumps([match_to_dict(match) for match in matches], ensure_ascii=False, sort_keys=True)


def select_scoring_candidates(
    connection: sqlite3.Connection,
    *,
    sample_id: str | None,
    domain: str | None,
    page_limit: int | None,
) -> list[ScoringCandidate]:
    parameters: list[object] = []
    sample_join = ""
    sample_filter = ""
    if sample_id:
        sample_join = "JOIN retention_validation_sample AS rvs ON rvs.scraped_page_id = e.scraped_page_id"
        sample_filter = "AND rvs.sample_id = ?"
        parameters.append(sample_id)
    domain_filter = ""
    if domain:
        domain_filter = "AND s.domain = ?"
        parameters.append(domain)
    limit_clause = ""
    if page_limit is not None:
        limit_clause = "LIMIT ?"
        parameters.append(page_limit)

    rows = connection.execute(
        f"""
        SELECT
            e.extracted_page_id,
            e.scraped_page_id,
            e.extraction_version,
            e.markdown_path,
            e.text_path,
            e.metadata_path,
            s.domain,
            s.url,
            pf.title,
            pf.headings
        FROM extracted_pages AS e
        JOIN scraped_pages AS s ON s.scraped_page_id = e.scraped_page_id
        {sample_join}
        LEFT JOIN page_features AS pf ON pf.extracted_page_id = e.extracted_page_id
        WHERE e.extraction_status = 'success'
          AND e.markdown_path IS NOT NULL
          AND e.text_path IS NOT NULL
          {sample_filter}
          {domain_filter}
        ORDER BY e.scraped_page_id ASC
        {limit_clause}
        """,
        parameters,
    ).fetchall()
    return [
        ScoringCandidate(
            extracted_page_id=int(row["extracted_page_id"]),
            scraped_page_id=int(row["scraped_page_id"]),
            extraction_version=str(row["extraction_version"]),
            domain=row["domain"],
            url=row["url"],
            markdown_path=row["markdown_path"],
            text_path=row["text_path"],
            metadata_path=row["metadata_path"],
            title=row["title"],
            headings_json=row["headings"],
        )
        for row in rows
    ]


def load_anchor_texts(connection: sqlite3.Connection, extracted_page_id: int, warnings: list[str]) -> tuple[list[str], list[str]]:
    try:
        rows = connection.execute(
            """
            SELECT anchor_text, internal_external
            FROM page_links
            WHERE extracted_page_id = ?
              AND anchor_text IS NOT NULL
              AND TRIM(anchor_text) != ''
            ORDER BY link_order, id
            """,
            (extracted_page_id,),
        ).fetchall()
    except sqlite3.Error as error:
        warnings.append(f"links_unreadable:{extracted_page_id}:{error}")
        return [], []
    internal: list[str] = []
    external: list[str] = []
    for row in rows:
        if row["internal_external"] == "internal":
            internal.append(row["anchor_text"])
        elif row["internal_external"] == "external":
            external.append(row["anchor_text"])
    return internal, external


def build_page_input(
    project_root: Path,
    connection: sqlite3.Connection,
    candidate: ScoringCandidate,
    warnings: list[str],
) -> PageScoringInput:
    markdown = read_required_text(project_root, candidate.markdown_path, "markdown")
    plain_text = read_required_text(project_root, candidate.text_path, "plain_text")
    metadata = read_optional_metadata(project_root, candidate.metadata_path, warnings)
    headings = parse_headings(candidate.headings_json, warnings)
    title = candidate.title or str(metadata.get("title") or "")
    internal_anchors, external_anchors = load_anchor_texts(connection, candidate.extracted_page_id, warnings)
    return PageScoringInput(
        plain_text=plain_text,
        markdown=markdown,
        title=title,
        headings=headings,
        url=candidate.url,
        internal_anchor_text=internal_anchors,
        external_anchor_text=external_anchors,
    )


def insert_scoring_run(
    connection: sqlite3.Connection,
    *,
    scoring_run_id: str,
    profile: ScoringProfileConfig,
    sample_id: str | None,
    extraction_version: str | None,
    started_at: str,
    pages_selected: int,
    config_hash: str,
    notes: str,
) -> None:
    connection.execute(
        """
        INSERT INTO scoring_runs (
            scoring_run_id,
            scoring_profile_id,
            scoring_profile_version,
            sample_id,
            extraction_version,
            started_at,
            pages_selected,
            pages_processed,
            pages_scored,
            pages_skipped,
            pages_failed,
            status,
            configuration_hash,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 'running', ?, ?)
        """,
        (
            scoring_run_id,
            profile.scoring_profile_id,
            profile.scoring_profile_version,
            sample_id,
            extraction_version,
            started_at,
            pages_selected,
            config_hash,
            notes,
        ),
    )


def update_scoring_run(
    connection: sqlite3.Connection,
    *,
    scoring_run_id: str,
    completed_at: str,
    processed: int,
    scored: int,
    skipped: int,
    failed: int,
    runtime_seconds: float,
    pages_per_second: float,
    status: str,
    notes: str,
) -> None:
    connection.execute(
        """
        UPDATE scoring_runs
        SET completed_at = ?,
            pages_processed = ?,
            pages_scored = ?,
            pages_skipped = ?,
            pages_failed = ?,
            runtime_seconds = ?,
            pages_per_second = ?,
            status = ?,
            notes = ?
        WHERE scoring_run_id = ?
        """,
        (
            completed_at,
            processed,
            scored,
            skipped,
            failed,
            runtime_seconds,
            pages_per_second,
            status,
            notes,
            scoring_run_id,
        ),
    )


def existing_score_count(
    connection: sqlite3.Connection,
    candidate: ScoringCandidate,
    knowledge_bases: dict[str, KnowledgeBaseConfig],
    profile: ScoringProfileConfig,
) -> int:
    count = 0
    for dimension in profile.required_dimensions:
        knowledge_base = knowledge_bases.get(dimension)
        if knowledge_base is None:
            continue
        row = connection.execute(
            """
            SELECT 1
            FROM page_scores
            WHERE extracted_page_id = ?
              AND dimension = ?
              AND extraction_version = ?
              AND knowledge_base_version = ?
              AND scoring_profile_version = ?
            LIMIT 1
            """,
            (
                candidate.extracted_page_id,
                dimension,
                candidate.extraction_version,
                knowledge_base.version,
                profile.scoring_profile_version,
            ),
        ).fetchone()
        if row:
            count += 1
    return count


def delete_existing_scores(
    connection: sqlite3.Connection,
    candidate: ScoringCandidate,
    knowledge_bases: dict[str, KnowledgeBaseConfig],
    profile: ScoringProfileConfig,
) -> None:
    for dimension in profile.required_dimensions:
        knowledge_base = knowledge_bases.get(dimension)
        if knowledge_base is None:
            continue
        connection.execute(
            """
            DELETE FROM page_scores
            WHERE extracted_page_id = ?
              AND dimension = ?
              AND extraction_version = ?
              AND knowledge_base_version = ?
              AND scoring_profile_version = ?
            """,
            (
                candidate.extracted_page_id,
                dimension,
                candidate.extraction_version,
                knowledge_base.version,
                profile.scoring_profile_version,
            ),
        )


def persist_dimension_score(
    connection: sqlite3.Connection,
    *,
    scoring_run_id: str,
    candidate: ScoringCandidate,
    result: DimensionScoringResult,
    profile: ScoringProfileConfig,
) -> ScoredPageSummary:
    connection.execute(
        """
        INSERT INTO page_scores (
            extracted_page_id,
            scraped_page_id,
            domain,
            url,
            scoring_run_id,
            dimension,
            extraction_version,
            query_score,
            keyword_score,
            boost_score,
            negative_score,
            final_score,
            top_queries_json,
            top_keywords_json,
            top_boost_terms_json,
            top_negative_terms_json,
            knowledge_base_id,
            knowledge_base_version,
            scoring_profile_id,
            scoring_profile_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.extracted_page_id,
            candidate.scraped_page_id,
            candidate.domain,
            candidate.url,
            scoring_run_id,
            result.dimension,
            candidate.extraction_version,
            result.query_score,
            result.keyword_score,
            result.boost_score,
            result.negative_score,
            result.final_score,
            matches_json(result.top_queries),
            matches_json(result.top_keywords),
            matches_json(result.top_boost_terms),
            matches_json(result.top_negative_terms),
            result.knowledge_base_id,
            result.knowledge_base_version,
            profile.scoring_profile_id,
            profile.scoring_profile_version,
        ),
    )
    return ScoredPageSummary(
        extracted_page_id=candidate.extracted_page_id,
        scraped_page_id=candidate.scraped_page_id,
        domain=candidate.domain,
        url=candidate.url,
        dimension=result.dimension,
        query_score=result.query_score,
        keyword_score=result.keyword_score,
        boost_score=result.boost_score,
        negative_score=result.negative_score,
        final_score=result.final_score,
        top_queries_json=matches_json(result.top_queries),
        top_keywords_json=matches_json(result.top_keywords),
        top_boost_terms_json=matches_json(result.top_boost_terms),
        top_negative_terms_json=matches_json(result.top_negative_terms),
    )


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


def numeric_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "p5": None,
            "p25": None,
            "p75": None,
            "p95": None,
            "zero_score_count": 0,
        }
    return {
        "count": len(values),
        "mean": round(mean(values), 6),
        "median": round(median(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "p5": round(float(percentile(values, 0.05)), 6),
        "p25": round(float(percentile(values, 0.25)), 6),
        "p75": round(float(percentile(values, 0.75)), 6),
        "p95": round(float(percentile(values, 0.95)), 6),
        "zero_score_count": sum(1 for value in values if value == 0),
    }


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def leading_match_values(payload: str) -> str:
    try:
        values = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if not isinstance(values, list):
        return ""
    return "; ".join(str(item.get("value")) for item in values[:3] if isinstance(item, dict) and item.get("value"))


def build_report(
    *,
    project_root: Path,
    scoring_run_id: str,
    rows: list[ScoredPageSummary],
    summary: dict[str, object],
    profile: ScoringProfileConfig,
    knowledge_bases: dict[str, KnowledgeBaseConfig],
    warnings: list[str],
) -> dict[str, object]:
    report_dir = project_root / "data" / "interim" / "scoring" / scoring_run_id
    report_dir.mkdir(parents=True, exist_ok=False)
    by_dimension: dict[str, list[ScoredPageSummary]] = defaultdict(list)
    for row in rows:
        by_dimension[row.dimension].append(row)

    summary_rows: list[dict[str, object]] = []
    top_rows: list[dict[str, object]] = []
    json_dimensions: dict[str, object] = {}
    for dimension in profile.required_dimensions:
        dimension_rows = by_dimension.get(dimension, [])
        stats = numeric_stats([row.final_score for row in dimension_rows])
        summary_rows.append({"dimension": dimension, **stats})
        top = sorted(dimension_rows, key=lambda item: (-item.final_score, item.scraped_page_id))[:10]
        low_non_zero = sorted(
            [item for item in dimension_rows if item.final_score > 0],
            key=lambda item: (item.final_score, item.scraped_page_id),
        )[:10]
        for section, selected in (("top_10", top), ("lowest_non_zero", low_non_zero)):
            for item in selected:
                top_rows.append(
                    {
                        "section": section,
                        "dimension": item.dimension,
                        "domain": item.domain,
                        "url": item.url,
                        "scraped_page_id": item.scraped_page_id,
                        "extracted_page_id": item.extracted_page_id,
                        "query_score": item.query_score,
                        "keyword_score": item.keyword_score,
                        "boost_score": item.boost_score,
                        "negative_score": item.negative_score,
                        "final_score": item.final_score,
                        "top_queries": leading_match_values(item.top_queries_json),
                        "top_keywords": leading_match_values(item.top_keywords_json),
                        "top_boost_terms": leading_match_values(item.top_boost_terms_json),
                        "top_negative_terms": leading_match_values(item.top_negative_terms_json),
                    }
                )
        json_dimensions[dimension] = {
            "statistics": stats,
            "top_10": [item.__dict__ for item in top],
            "lowest_non_zero": [item.__dict__ for item in low_non_zero],
        }

    summary_csv = report_dir / "scoring_summary.csv"
    top_csv = report_dir / "scoring_top_pages.csv"
    json_path = report_dir / "scoring_summary.json"
    write_csv(
        summary_csv,
        summary_rows,
        ["dimension", "count", "mean", "median", "min", "max", "p5", "p25", "p75", "p95", "zero_score_count"],
    )
    write_csv(
        top_csv,
        top_rows,
        [
            "section",
            "dimension",
            "domain",
            "url",
            "scraped_page_id",
            "extracted_page_id",
            "query_score",
            "keyword_score",
            "boost_score",
            "negative_score",
            "final_score",
            "top_queries",
            "top_keywords",
            "top_boost_terms",
            "top_negative_terms",
        ],
    )
    payload: dict[str, object] = {
        **summary,
        "knowledge_base_versions": {
            dimension: knowledge_base.version
            for dimension, knowledge_base in sorted(knowledge_bases.items())
        },
        "scoring_profile_id": profile.scoring_profile_id,
        "scoring_profile_version": profile.scoring_profile_version,
        "dimensions": json_dimensions,
        "warnings": warnings,
        "report_paths": {
            "summary_csv": str(summary_csv.relative_to(project_root)),
            "top_pages_csv": str(top_csv.relative_to(project_root)),
            "json": str(json_path.relative_to(project_root)),
        },
    }
    write_json(json_path, payload)
    return payload


def run_scoring(
    *,
    sample_id: str | None = None,
    page_limit: int | None = None,
    domain: str | None = None,
    force_refresh: bool = False,
    progress_every: int = 25,
    project_root: Path | None = None,
) -> dict[str, object]:
    root = project_root or find_project_root()
    db_path = init_database(root)
    config = load_config(root)
    db_path = resolve_database_path(root, config)
    profile, knowledge_bases = validate_scoring_foundation(root)
    scorer = ContentScorer(profile, knowledge_bases)
    scoring_run_id = generate_scoring_run_id()
    started_at = utc_now_iso()
    started = time.perf_counter()
    warnings: list[str] = []
    scored_rows: list[ScoredPageSummary] = []

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        candidates = select_scoring_candidates(
            connection,
            sample_id=sample_id,
            domain=domain,
            page_limit=page_limit,
        )
        extraction_versions = sorted({candidate.extraction_version for candidate in candidates})
        config_hash = configuration_hash(
            {
                "sample_id": sample_id,
                "page_limit": page_limit,
                "domain": domain,
                "force_refresh": force_refresh,
                "progress_every": progress_every,
                "scoring_profile_id": profile.scoring_profile_id,
                "scoring_profile_version": profile.scoring_profile_version,
                "knowledge_base_versions": {
                    dimension: knowledge_base.version
                    for dimension, knowledge_base in sorted(knowledge_bases.items())
                },
            }
        )
        insert_scoring_run(
            connection,
            scoring_run_id=scoring_run_id,
            profile=profile,
            sample_id=sample_id,
            extraction_version=",".join(extraction_versions) if extraction_versions else None,
            started_at=started_at,
            pages_selected=len(candidates),
            config_hash=config_hash,
            notes=json.dumps({"domain": domain, "page_limit": page_limit, "force_refresh": force_refresh}, sort_keys=True),
        )
        connection.commit()

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Scoring started")
        print(f"  Run ID: {scoring_run_id}")
        print(f"  Pages selected: {len(candidates)}")
        if sample_id:
            print(f"  Sample: {sample_id}")
        if domain:
            print(f"  Domain: {domain}")

        processed = 0
        scored_pages = 0
        skipped_pages = 0
        failed_pages = 0
        for candidate in candidates:
            processed += 1
            try:
                if force_refresh:
                    delete_existing_scores(connection, candidate, knowledge_bases, profile)
                existing = existing_score_count(connection, candidate, knowledge_bases, profile)
                expected = sum(1 for dimension in profile.required_dimensions if dimension in knowledge_bases)
                if not force_refresh and existing >= expected:
                    skipped_pages += 1
                else:
                    page_warnings: list[str] = []
                    page_input = build_page_input(root, connection, candidate, page_warnings)
                    warnings.extend(
                        f"scraped_page_id={candidate.scraped_page_id}:{warning}"
                        for warning in page_warnings
                    )
                    score_result = scorer.score_page(page_input)
                    for dimension in profile.required_dimensions:
                        dimension_result = score_result.dimensions.get(dimension)
                        if dimension_result is None:
                            continue
                        scored_rows.append(
                            persist_dimension_score(
                                connection,
                                scoring_run_id=scoring_run_id,
                                candidate=candidate,
                                result=dimension_result,
                                profile=profile,
                            )
                        )
                    scored_pages += 1
                connection.commit()
            except Exception as error:
                failed_pages += 1
                warnings.append(f"scraped_page_id={candidate.scraped_page_id}:scoring_failed:{error}")
                connection.commit()

            if candidates and (
                processed == len(candidates)
                or processed == 1
                or processed % max(progress_every, 1) == 0
            ):
                elapsed = time.perf_counter() - started
                rate = processed / elapsed if elapsed else 0.0
                print(
                    "Progress: "
                    f"{processed}/{len(candidates)} "
                    f"Scored: {scored_pages} "
                    f"Skipped: {skipped_pages} "
                    f"Failed: {failed_pages} "
                    f"Elapsed: {format_duration(elapsed)} "
                    f"Rate: {rate:.2f} pages/sec"
                )

        runtime = time.perf_counter() - started
        pages_per_second = processed / runtime if runtime else 0.0
        status = "completed" if failed_pages == 0 else "completed_with_errors"
        completed_at = utc_now_iso()
        notes = json.dumps(
            {
                "domain": domain,
                "page_limit": page_limit,
                "force_refresh": force_refresh,
                "skipped_pages": skipped_pages,
                "warnings_count": len(warnings),
            },
            sort_keys=True,
        )
        update_scoring_run(
            connection,
            scoring_run_id=scoring_run_id,
            completed_at=completed_at,
            processed=processed,
            scored=scored_pages,
            skipped=skipped_pages,
            failed=failed_pages,
            runtime_seconds=runtime,
            pages_per_second=pages_per_second,
            status=status,
            notes=notes,
        )
        connection.commit()

    summary: dict[str, object] = {
        "scoring_run_id": scoring_run_id,
        "sample_id": sample_id,
        "filters": {"domain": domain, "page_limit": page_limit, "force_refresh": force_refresh},
        "extraction_version": extraction_versions[0] if len(extraction_versions) == 1 else extraction_versions,
        "started_at": started_at,
        "completed_at": completed_at,
        "runtime_seconds": round(runtime, 6),
        "pages_per_second": round(pages_per_second, 6),
        "pages_selected": len(candidates),
        "pages_processed": processed,
        "pages_scored": scored_pages,
        "pages_skipped": skipped_pages,
        "pages_failed": failed_pages,
        "score_rows_written": len(scored_rows),
        "status": status,
    }
    report = build_report(
        project_root=root,
        scoring_run_id=scoring_run_id,
        rows=scored_rows,
        summary=summary,
        profile=profile,
        knowledge_bases=knowledge_bases,
        warnings=warnings,
    )
    print_summary(report)
    return report


def print_summary(summary: dict[str, object]) -> None:
    print("Scoring summary")
    print(f"  Run ID: {summary['scoring_run_id']}")
    print(f"  Pages selected: {summary['pages_selected']}")
    print(f"  Pages processed: {summary['pages_processed']}")
    print(f"  Pages scored: {summary['pages_scored']}")
    print(f"  Pages skipped: {summary['pages_skipped']}")
    print(f"  Pages failed: {summary['pages_failed']}")
    print(f"  Runtime: {summary['runtime_seconds']}s")
    print(f"  Pages/sec: {summary['pages_per_second']}")
    print(f"  Reports: {summary['report_paths']['json']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run content scoring over extracted pages.")
    parser.add_argument("--sample", dest="sample_id")
    parser.add_argument("--page-limit", type=int)
    parser.add_argument("--domain")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()
    run_scoring(
        sample_id=args.sample_id,
        page_limit=args.page_limit,
        domain=args.domain,
        force_refresh=args.force_refresh,
        progress_every=args.progress_every,
    )


if __name__ == "__main__":
    main()
