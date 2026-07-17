"""Build qualitative review packages for persisted scoring results."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.scoring.config import REQUIRED_DIMENSIONS, load_scoring_profile


PAGE_CATEGORIES = (
    "company profile",
    "product/service",
    "technology",
    "innovation",
    "partnership",
    "news",
    "press release",
    "legal/privacy",
    "careers",
    "blog",
    "history",
    "biography",
    "customer story",
    "third-party content",
    "other",
)


@dataclass(frozen=True)
class EvaluationPage:
    page_score_id: int
    rank: int
    dimension: str
    extracted_page_id: int
    scraped_page_id: int
    domain: str | None
    url: str
    scoring_run_id: str
    extraction_version: str | None
    scoring_profile_id: str | None
    scoring_profile_version: str | None
    knowledge_base_id: str | None
    knowledge_base_version: str | None
    query_score: float
    keyword_score: float
    boost_score: float
    negative_score: float
    final_score: float
    top_queries_json: str | None
    top_keywords_json: str | None
    top_boost_terms_json: str | None
    top_negative_terms_json: str | None
    markdown_path: str | None
    text_path: str | None
    metadata_path: str | None
    feature_title: str | None
    headings_json: str | None
    heading_hierarchy_json: str | None
    page_type_guess: str | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def resolve_optional_path(project_root: Path, path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = project_root / path
    return path


def read_text_excerpt(project_root: Path, path_value: str | None, max_chars: int, warnings: list[str], label: str) -> tuple[str, int, bool]:
    path = resolve_optional_path(project_root, path_value)
    if path is None:
        warnings.append(f"{label}_path_missing")
        return "", 0, False
    if not path.is_file():
        warnings.append(f"{label}_file_missing:{path_value}")
        return "", 0, False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        warnings.append(f"{label}_file_unreadable:{path_value}:{error}")
        return "", 0, False
    truncated = len(content) > max_chars
    return content[:max_chars], len(content), truncated


def read_metadata(project_root: Path, path_value: str | None, warnings: list[str]) -> dict[str, Any]:
    path = resolve_optional_path(project_root, path_value)
    if path is None:
        return {}
    if not path.is_file():
        warnings.append(f"metadata_file_missing:{path_value}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        warnings.append(f"metadata_unreadable:{path_value}:{error}")
        return {}
    return data if isinstance(data, dict) else {}


def parse_json_list(value: str | None, warnings: list[str], label: str) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        warnings.append(f"{label}_json_unreadable:{error}")
        return []
    if not isinstance(parsed, list):
        warnings.append(f"{label}_json_not_list")
        return []
    return parsed


def parse_headings(value: str | None, hierarchy_value: str | None, warnings: list[str]) -> list[str]:
    headings = parse_json_list(value, warnings, "headings")
    result: list[str] = []
    for item in headings:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and item.get("text"):
            level = item.get("level")
            prefix = f"H{level}: " if level else ""
            result.append(prefix + str(item["text"]))
    if result:
        return result
    hierarchy = parse_json_list(hierarchy_value, warnings, "heading_hierarchy")
    for item in hierarchy:
        if isinstance(item, dict) and item.get("text"):
            level = item.get("level")
            prefix = f"H{level}: " if level else ""
            result.append(prefix + str(item["text"]))
        elif isinstance(item, str):
            result.append(item)
    return result


def parse_matches(value: str | None, warnings: list[str], label: str) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        warnings.append(f"{label}_json_unreadable:{error}")
        return []
    if not isinstance(parsed, list):
        warnings.append(f"{label}_json_not_list")
        return []
    return [item for item in parsed if isinstance(item, dict)]


def truncate_for_table(value: object, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def render_match_section(title: str, matches: list[dict[str, Any]]) -> str:
    lines = [f"### {title}", ""]
    if not matches:
        lines.extend(["No matches recorded.", ""])
        return "\n".join(lines)
    lines.extend(["| Value | Score | Fields | Matched terms | Source | Language |", "|---|---:|---|---|---|---|"])
    for match in matches:
        fields = ", ".join(str(item) for item in match.get("matched_fields", []) or [])
        terms = ", ".join(str(item) for item in match.get("matched_terms", []) or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    truncate_for_table(match.get("value")),
                    str(match.get("score", "")),
                    truncate_for_table(fields),
                    truncate_for_table(terms),
                    truncate_for_table(match.get("source_node")),
                    truncate_for_table(match.get("language")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def markdown_fence(content: str) -> str:
    return "```text\n" + content.replace("```", "'''") + "\n```"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def select_top_pages(connection: sqlite3.Connection, scoring_run_id: str, dimensions: tuple[str, ...], top_n: int) -> list[EvaluationPage]:
    selected: list[EvaluationPage] = []
    for dimension in dimensions:
        rows = connection.execute(
            """
            SELECT
                ps.page_score_id,
                ps.dimension,
                ps.extracted_page_id,
                ps.scraped_page_id,
                COALESCE(ps.domain, s.domain) AS domain,
                COALESCE(ps.url, s.url) AS url,
                ps.scoring_run_id,
                ps.extraction_version,
                ps.scoring_profile_id,
                ps.scoring_profile_version,
                ps.knowledge_base_id,
                ps.knowledge_base_version,
                ps.query_score,
                ps.keyword_score,
                ps.boost_score,
                ps.negative_score,
                ps.final_score,
                ps.top_queries_json,
                ps.top_keywords_json,
                ps.top_boost_terms_json,
                ps.top_negative_terms_json,
                e.markdown_path,
                e.text_path,
                e.metadata_path,
                pf.title AS feature_title,
                pf.headings AS headings_json,
                pf.heading_hierarchy AS heading_hierarchy_json,
                pf.page_type_guess
            FROM page_scores AS ps
            JOIN extracted_pages AS e ON e.extracted_page_id = ps.extracted_page_id
            LEFT JOIN scraped_pages AS s ON s.scraped_page_id = ps.scraped_page_id
            LEFT JOIN page_features AS pf ON pf.extracted_page_id = ps.extracted_page_id
            WHERE ps.scoring_run_id = ?
              AND ps.dimension = ?
            ORDER BY ps.final_score DESC, ps.scraped_page_id ASC
            LIMIT ?
            """,
            (scoring_run_id, dimension, top_n),
        ).fetchall()
        for rank, row in enumerate(rows, start=1):
            selected.append(
                EvaluationPage(
                    page_score_id=int(row["page_score_id"]),
                    rank=rank,
                    dimension=row["dimension"],
                    extracted_page_id=int(row["extracted_page_id"]),
                    scraped_page_id=int(row["scraped_page_id"]),
                    domain=row["domain"],
                    url=row["url"],
                    scoring_run_id=row["scoring_run_id"],
                    extraction_version=row["extraction_version"],
                    scoring_profile_id=row["scoring_profile_id"],
                    scoring_profile_version=row["scoring_profile_version"],
                    knowledge_base_id=row["knowledge_base_id"],
                    knowledge_base_version=row["knowledge_base_version"],
                    query_score=float(row["query_score"] or 0),
                    keyword_score=float(row["keyword_score"] or 0),
                    boost_score=float(row["boost_score"] or 0),
                    negative_score=float(row["negative_score"] or 0),
                    final_score=float(row["final_score"] or 0),
                    top_queries_json=row["top_queries_json"],
                    top_keywords_json=row["top_keywords_json"],
                    top_boost_terms_json=row["top_boost_terms_json"],
                    top_negative_terms_json=row["top_negative_terms_json"],
                    markdown_path=row["markdown_path"],
                    text_path=row["text_path"],
                    metadata_path=row["metadata_path"],
                    feature_title=row["feature_title"],
                    headings_json=row["headings_json"],
                    heading_hierarchy_json=row["heading_hierarchy_json"],
                    page_type_guess=row["page_type_guess"],
                )
            )
    return selected


def get_scoring_run(connection: sqlite3.Connection, scoring_run_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM scoring_runs WHERE scoring_run_id = ?",
        (scoring_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"scoring_run_id not found: {scoring_run_id}")
    return dict(row)


def default_max_content_chars(config: dict[str, Any]) -> int:
    validation = config.get("validation", {})
    if isinstance(validation, dict):
        try:
            value = int(validation.get("max_input_chars", 4000))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 4000


def evaluation_filename(page: EvaluationPage) -> str:
    return f"{page.dimension}_rank_{page.rank:03d}_scraped_{page.scraped_page_id}.md"


def render_page_markdown(
    *,
    project_root: Path,
    page: EvaluationPage,
    max_content_chars: int,
) -> tuple[str, dict[str, Any]]:
    warnings: list[str] = []
    metadata = read_metadata(project_root, page.metadata_path, warnings)
    headings = parse_headings(page.headings_json, page.heading_hierarchy_json, warnings)
    title = page.feature_title or str(metadata.get("title") or "")
    markdown_excerpt, markdown_length, markdown_truncated = read_text_excerpt(
        project_root,
        page.markdown_path,
        max_content_chars,
        warnings,
        "markdown",
    )
    text_excerpt, text_length, text_truncated = read_text_excerpt(
        project_root,
        page.text_path,
        max_content_chars,
        warnings,
        "plain_text",
    )
    query_matches = parse_matches(page.top_queries_json, warnings, "top_queries")
    keyword_matches = parse_matches(page.top_keywords_json, warnings, "top_keywords")
    boost_matches = parse_matches(page.top_boost_terms_json, warnings, "top_boost_terms")
    negative_matches = parse_matches(page.top_negative_terms_json, warnings, "top_negative_terms")

    lines = [
        f"# {page.dimension} rank {page.rank}: scraped page {page.scraped_page_id}",
        "",
        "## Review Metadata",
        "",
        f"- Dimension: `{page.dimension}`",
        f"- Rank: `{page.rank}`",
        f"- Final score: `{page.final_score:.6f}`",
        f"- URL: {page.url}",
        f"- Domain: `{page.domain or ''}`",
        f"- Page title: {title or '(missing)'}",
        f"- Page type guess: `{page.page_type_guess or ''}`",
        f"- Scoring run ID: `{page.scoring_run_id}`",
        f"- Page score ID: `{page.page_score_id}`",
        f"- Scraped page ID: `{page.scraped_page_id}`",
        f"- Extracted page ID: `{page.extracted_page_id}`",
        f"- Extraction version: `{page.extraction_version or ''}`",
        f"- Knowledge base: `{page.knowledge_base_id or ''}` / `{page.knowledge_base_version or ''}`",
        f"- Scoring profile: `{page.scoring_profile_id or ''}` / `{page.scoring_profile_version or ''}`",
        "",
        "## Score Components",
        "",
        "| Component | Score |",
        "|---|---:|",
        f"| Query-family | {page.query_score:.6f} |",
        f"| Keyword | {page.keyword_score:.6f} |",
        f"| Boost | {page.boost_score:.6f} |",
        f"| Negative | {page.negative_score:.6f} |",
        f"| Final | {page.final_score:.6f} |",
        "",
        "## Headings",
        "",
    ]
    if headings:
        lines.extend(f"- {heading}" for heading in headings)
    else:
        lines.append("(No headings recorded.)")
    lines.extend(
        [
            "",
            "## Explainability",
            "",
            render_match_section("Top Query Matches", query_matches),
            render_match_section("Top Keyword Matches", keyword_matches),
            render_match_section("Top Boost Terms", boost_matches),
            render_match_section("Top Negative Terms", negative_matches),
        ]
    )
    if warnings:
        lines.extend(["## Package Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in warnings)
        lines.append("")
    lines.extend(
        [
            "## Markdown Excerpt",
            "",
            f"- Source path: `{page.markdown_path or ''}`",
            f"- Original characters: `{markdown_length}`",
            f"- Truncated: `{str(markdown_truncated).lower()}`",
            "",
            markdown_fence(markdown_excerpt),
            "",
            "## Plain Text Excerpt",
            "",
            f"- Source path: `{page.text_path or ''}`",
            f"- Original characters: `{text_length}`",
            f"- Truncated: `{str(text_truncated).lower()}`",
            "",
            markdown_fence(text_excerpt),
            "",
        ]
    )
    manifest_item = {
        "file": evaluation_filename(page),
        "dimension": page.dimension,
        "rank": page.rank,
        "page_score_id": page.page_score_id,
        "scraped_page_id": page.scraped_page_id,
        "extracted_page_id": page.extracted_page_id,
        "domain": page.domain,
        "url": page.url,
        "final_score": page.final_score,
        "query_score": page.query_score,
        "keyword_score": page.keyword_score,
        "boost_score": page.boost_score,
        "negative_score": page.negative_score,
        "title": title,
        "warnings": warnings,
    }
    return "\n".join(lines), manifest_item


def build_prompt(scoring_run_id: str, top_n: int, page_files: list[str]) -> str:
    categories = "\n".join(f"- {category}" for category in PAGE_CATEGORIES)
    files = "\n".join(f"- `{path}`" for path in page_files)
    return f"""# GPT Scoring Evaluation Prompt

You are reviewing pages selected from content scoring run `{scoring_run_id}`.

Each Markdown file contains one scored page for one scoring dimension. Review each page using only the evidence in the file.

## Review Questions

For each page, answer:

1. Is this page genuinely useful for understanding the company?
2. Is the score justified by the evidence shown?
3. Should the page be retained for downstream evidence extraction?
4. What evidence supports or contradicts the score?
5. Is this page a likely false positive?
6. What recurring scoring or retention failure mode, if any, does it illustrate?

Classify the page primarily as one of:

{categories}

After reviewing all files, summarize:

- recurring false positives;
- recurring high-quality evidence patterns;
- recurring failure modes in query, keyword, boost, or negative scoring;
- suggested scoring or retention improvements for later milestones.

Do not assume that high score means the page should be retained. Do not modify the current implementation.

## Included Files

Top N per dimension: `{top_n}`

{files}
"""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evaluate_scoring(
    *,
    scoring_run_id: str,
    top_n: int = 20,
    output_dir: Path | None = None,
    max_content_chars: int | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = project_root or find_project_root()
    config = load_config(root)
    db_path = resolve_database_path(root, config)
    profile = load_scoring_profile(root)
    dimensions = profile.required_dimensions or REQUIRED_DIMENSIONS
    content_chars = max_content_chars or default_max_content_chars(config)
    output_root = output_dir or resolve_project_path(root, "data/interim/scoring_evaluation")
    if not output_root.is_absolute():
        output_root = root / output_root
    package_dir = output_root / scoring_run_id
    if package_dir.exists():
        raise FileExistsError(f"Evaluation package already exists: {package_dir}")
    package_dir.mkdir(parents=True)

    with connect_readonly(db_path) as connection:
        run = get_scoring_run(connection, scoring_run_id)
        pages = select_top_pages(connection, scoring_run_id, dimensions, top_n)

    manifest_items: list[dict[str, Any]] = []
    page_files: list[str] = []
    all_warnings: list[str] = []
    selected_counts: dict[str, int] = {dimension: 0 for dimension in dimensions}
    for page in pages:
        selected_counts[page.dimension] = selected_counts.get(page.dimension, 0) + 1
        filename = evaluation_filename(page)
        content, manifest_item = render_page_markdown(
            project_root=root,
            page=page,
            max_content_chars=content_chars,
        )
        (package_dir / filename).write_text(content, encoding="utf-8")
        manifest_items.append(manifest_item)
        page_files.append(filename)
        all_warnings.extend(
            f"{filename}:{warning}"
            for warning in manifest_item.get("warnings", [])
        )

    prompt = build_prompt(scoring_run_id, top_n, page_files)
    prompt_path = package_dir / "evaluation_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    manifest_path = package_dir / "manifest.json"
    source_report_dir = root / "data" / "interim" / "scoring" / scoring_run_id
    manifest = {
        "scoring_run_id": scoring_run_id,
        "generated_at": utc_now_iso(),
        "top_n": top_n,
        "max_content_chars": content_chars,
        "dimensions": list(dimensions),
        "selected_counts_by_dimension": selected_counts,
        "total_review_files": len(page_files),
        "scoring_run": run,
        "output_directory": str(package_dir.relative_to(root)) if package_dir.is_relative_to(root) else str(package_dir),
        "evaluation_prompt": prompt_path.name,
        "page_files": manifest_items,
        "warnings": all_warnings,
        "source_report_paths": {
            "scoring_summary_json": str((source_report_dir / "scoring_summary.json").relative_to(root)),
            "scoring_summary_csv": str((source_report_dir / "scoring_summary.csv").relative_to(root)),
            "scoring_top_pages_csv": str((source_report_dir / "scoring_top_pages.csv").relative_to(root)),
        },
    }
    write_json(manifest_path, manifest)
    return manifest


def print_summary(manifest: dict[str, Any]) -> None:
    print("Scoring evaluation package")
    print(f"  Scoring run ID: {manifest['scoring_run_id']}")
    print(f"  Top N: {manifest['top_n']}")
    print(f"  Review files: {manifest['total_review_files']}")
    print(f"  Output directory: {manifest['output_directory']}")
    print(f"  Warnings: {len(manifest['warnings'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a GPT review package for scoring outputs.")
    parser.add_argument("--scoring-run-id", required=True)
    parser.add_argument("--top-n", type=positive_int, default=20)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-content-chars", type=positive_int)
    args = parser.parse_args()
    manifest = evaluate_scoring(
        scoring_run_id=args.scoring_run_id,
        top_n=args.top_n,
        output_dir=args.output_dir,
        max_content_chars=args.max_content_chars,
    )
    print_summary(manifest)


if __name__ == "__main__":
    main()
