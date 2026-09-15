"""Build blind, company-level evidence packages for calibration review.

The Markdown and JSON files in ``blind_inputs`` contain only source company and
extraction evidence. Scoring data is written exclusively to ``analysis_metadata``
so that later LLM labels can be compared with, rather than influenced by, scores.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from ai_ecosystem.calibration.config import CalibrationConfig, load_calibration_config
from ai_ecosystem.db.init_db import find_project_root, init_database, load_config, resolve_database_path, resolve_project_path


FORBIDDEN_SCORER_KEYS = frozenset(
    {
        "firm_profile_score",
        "technology_score",
        "innovation_score",
        "ecosystem_score",
        "query_score",
        "keyword_score",
        "boost_score",
        "negative_score",
        "final_score",
        "top_queries",
        "top_keywords",
        "top_boost_terms",
        "top_negative_terms",
    }
)
FORBIDDEN_MARKDOWN_LABELS = (
    "technology score",
    "innovation score",
    "firm profile score",
    "ecosystem score",
    "high-scoring page",
    "strong technology page",
    "top query",
    "top keyword",
    "boost term",
    "negative term",
)
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
MODEL_VISIBLE_CONTENT_STATUSES = frozenset({"complete", "partially_truncated"})
ABOUT_COMPANY_PATH_COMPONENTS = frozenset(
    {
        "about",
        "about-us",
        "company",
        "unternehmen",
        "ueber-uns",
        "über-uns",
        "ueber",
    }
)


@dataclass(frozen=True)
class CompanyRecord:
    company_id: str
    company_name: str | None
    domain: str | None
    nace_code: str | None
    city: str | None
    postal_code: str | None
    mark: str | None
    raw_url: str | None
    normalized_url: str | None
    domain_category: str | None
    representation_level: str | None
    sample_domains: tuple[str, ...]


@dataclass(frozen=True)
class PackageResult:
    company_id: str
    company_name: str | None
    domain: str | None
    shared_domain: bool
    mapped_company_count: int
    page_count: int
    usable_page_count: int
    total_words: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    truncated: bool
    full_page_count: int
    partial_page_count: int
    metadata_only_page_count: int
    score_coverage: str
    blind_input_path: str
    analysis_metadata_path: str
    warnings: tuple[str, ...]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def estimate_tokens(text: str) -> int:
    """Estimate tokens without adding a tokenizer dependency.

    The estimator is deterministic and intentionally conservative enough for
    package budgeting. The manifest records this approximation and model label.
    """
    return len(TOKEN_PATTERN.findall(text))


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe or "unknown"


def resolve_path(project_root: Path, path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else project_root / path


def read_text(project_root: Path, path_value: str | None, warnings: list[str], label: str) -> str:
    path = resolve_path(project_root, path_value)
    if path is None or not path.is_file():
        warnings.append(f"{label}_missing:{path_value or ''}")
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        warnings.append(f"{label}_unreadable:{path_value}:{error}")
        return ""


def parse_json_list(value: str | None, warnings: list[str], label: str) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        warnings.append(f"{label}_unreadable:{error}")
        return []
    if not isinstance(parsed, list):
        warnings.append(f"{label}_not_list")
        return []
    return parsed


def normalize_headings(value: str | None, hierarchy: str | None, warnings: list[str]) -> list[dict[str, Any]]:
    source = parse_json_list(value, warnings, "headings") or parse_json_list(hierarchy, warnings, "heading_hierarchy")
    headings: list[dict[str, Any]] = []
    for item in source:
        if isinstance(item, str):
            headings.append({"text": item, "level": None})
        elif isinstance(item, dict) and item.get("text"):
            headings.append({"text": str(item["text"]), "level": item.get("level")})
    return headings


def calibration_page_sort_key(page: dict[str, Any]) -> tuple[int, int, int]:
    """Order blind evidence structurally without using page content or scores."""
    url = str(page.get("normalized_url") or page.get("url") or "")
    path = urlsplit(url).path
    components = [unquote(component).casefold() for component in path.split("/") if component]
    depth = len(components)
    if depth == 0:
        priority = 0
    elif any(component in ABOUT_COMPANY_PATH_COMPONENTS for component in components):
        priority = 1
    else:
        priority = 2
    return priority, depth, int(page["scraped_page_id"])


def select_companies(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    company_ids: Iterable[str] = (),
    domain: str | None = None,
    company_limit: int | None = None,
) -> list[CompanyRecord]:
    """Select focal companies, preferring exact company membership when present."""
    requested_ids = tuple(dict.fromkeys(company_ids))
    has_explicit_companies = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'sample_companies'
          AND EXISTS (SELECT 1 FROM sample_companies WHERE sample_id = ?)
        """,
        (sample_id,),
    ).fetchone()
    if has_explicit_companies:
        where = ["EXISTS (SELECT 1 FROM sample_companies sc WHERE sc.sample_id = ? AND sc.company_id = f.bvd_id)"]
    else:
        where = ["EXISTS (SELECT 1 FROM sample_domains sd WHERE sd.sample_id = ? AND (sd.domain = f.normalized_domain OR sd.domain IN (SELECT fu.domain FROM firm_urls fu WHERE fu.bvd_id = f.bvd_id)))"]
    params: list[Any] = [sample_id]
    if requested_ids:
        where.append("f.bvd_id IN ({})".format(", ".join("?" for _ in requested_ids)))
        params.extend(requested_ids)
    if domain:
        where.append("(f.normalized_domain = ? OR EXISTS (SELECT 1 FROM firm_urls fu WHERE fu.bvd_id = f.bvd_id AND fu.domain = ?))")
        params.extend([domain, domain])
    limit_clause = " LIMIT ?" if company_limit is not None else ""
    if company_limit is not None:
        params.append(company_limit)
    rows = connection.execute(
        f"""
        SELECT
            f.bvd_id, f.firm_name, f.normalized_domain, f.nace_code, f.city,
            f.postal_code, f.mark, f.raw_url, f.normalized_url,
            d.domain_category, d.representation_level
        FROM firms f
        LEFT JOIN domains d ON d.domain = f.normalized_domain
        WHERE {' AND '.join(where)}
        ORDER BY f.bvd_id ASC{limit_clause}
        """,
        params,
    ).fetchall()
    companies: list[CompanyRecord] = []
    for row in rows:
        if has_explicit_companies:
            related_domains = connection.execute(
                """
                SELECT DISTINCT normalized_domain AS domain
                FROM sample_companies
                WHERE sample_id = ? AND company_id = ? AND normalized_domain IS NOT NULL
                ORDER BY normalized_domain
                """,
                (sample_id, row["bvd_id"]),
            ).fetchall()
        else:
            related_domains = connection.execute(
                """
                SELECT DISTINCT sd.domain
                FROM sample_domains sd
                LEFT JOIN firm_urls fu ON fu.domain = sd.domain AND fu.bvd_id = ?
                WHERE sd.sample_id = ? AND (sd.domain = ? OR fu.bvd_id IS NOT NULL)
                ORDER BY sd.domain
                """,
                (row["bvd_id"], sample_id, row["normalized_domain"]),
            ).fetchall()
        sample_domains = tuple(str(item["domain"]) for item in related_domains)
        primary_domain = row["normalized_domain"] or (sample_domains[0] if sample_domains else None)
        companies.append(
            CompanyRecord(
                company_id=str(row["bvd_id"]), company_name=row["firm_name"], domain=primary_domain,
                nace_code=row["nace_code"], city=row["city"], postal_code=row["postal_code"],
                mark=row["mark"], raw_url=row["raw_url"], normalized_url=row["normalized_url"],
                domain_category=row["domain_category"], representation_level=row["representation_level"],
                sample_domains=sample_domains,
            )
        )
    return companies


def related_companies(connection: sqlite3.Connection, domain: str | None) -> list[dict[str, Any]]:
    if not domain:
        return []
    rows = connection.execute(
        """
        SELECT DISTINCT f.bvd_id, f.firm_name
        FROM firms f LEFT JOIN firm_urls fu ON fu.bvd_id = f.bvd_id
        WHERE f.normalized_domain = ? OR fu.domain = ?
        ORDER BY f.bvd_id
        """,
        (domain, domain),
    ).fetchall()
    return [{"company_id": row["bvd_id"], "company_name": row["firm_name"]} for row in rows]


def load_pages(connection: sqlite3.Connection, company: CompanyRecord, project_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if not company.domain:
        return [], ["normalized_domain_missing"]
    rows = connection.execute(
        """
        SELECT e.extracted_page_id, e.scraped_page_id, e.extraction_version,
               e.language AS extraction_language, e.content_sha256,
               e.markdown_path, e.text_path, e.metadata_path,
               s.url, s.final_url, s.domain, s.scrape_status,
               pf.title, pf.headings, pf.heading_hierarchy, pf.page_type_guess,
               pf.language AS feature_language, pf.word_count
        FROM extracted_pages e
        JOIN scraped_pages s ON s.scraped_page_id = e.scraped_page_id
        LEFT JOIN page_features pf ON pf.extracted_page_id = e.extracted_page_id
        WHERE s.domain = ? AND e.extraction_status = 'success'
        ORDER BY e.scraped_page_id ASC
        """,
        (company.domain,),
    ).fetchall()
    pages: list[dict[str, Any]] = []
    for row in rows:
        page_warnings: list[str] = []
        plain_text = read_text(project_root, row["text_path"], page_warnings, "text")
        markdown = read_text(project_root, row["markdown_path"], page_warnings, "markdown")
        if not plain_text and not markdown:
            warnings.extend(f"page_{row['scraped_page_id']}:{warning}" for warning in page_warnings)
            continue
        content = plain_text or markdown
        headings = normalize_headings(row["headings"], row["heading_hierarchy"], page_warnings)
        word_count = row["word_count"] if row["word_count"] is not None else len(content.split())
        pages.append(
            {
                "scraped_page_id": int(row["scraped_page_id"]),
                "extracted_page_id": int(row["extracted_page_id"]),
                "url": row["url"],
                "normalized_url": row["final_url"] or row["url"],
                "title": row["title"],
                "headings": headings,
                "page_type": row["page_type_guess"],
                "language": row["feature_language"] or row["extraction_language"],
                "word_count": int(word_count or 0),
                "extraction_version": row["extraction_version"],
                "content": content,
                "content_sha256": row["content_sha256"],
                "page_warnings": page_warnings,
            }
        )
        warnings.extend(f"page_{row['scraped_page_id']}:{warning}" for warning in page_warnings)
    return sorted(pages, key=calibration_page_sort_key), warnings


def load_availability(connection: sqlite3.Connection, domain: str | None, usable_pages: int, total_words: int) -> dict[str, Any]:
    if not domain:
        return {"acquired_page_count": 0, "acquisition_statuses": [], "successful_extracted_page_count": 0, "usable_page_count": usable_pages, "total_extracted_words": total_words}
    acquisition = connection.execute(
        """SELECT scrape_status, COUNT(*) AS count FROM scraped_pages WHERE domain = ? GROUP BY scrape_status ORDER BY scrape_status""",
        (domain,),
    ).fetchall()
    extraction = connection.execute(
        """
        SELECT extraction_status, COUNT(*) AS count FROM extracted_pages e
        JOIN scraped_pages s ON s.scraped_page_id=e.scraped_page_id
        WHERE s.domain=? GROUP BY extraction_status ORDER BY extraction_status
        """,
        (domain,),
    ).fetchall()
    return {
        "acquired_page_count": sum(int(row["count"]) for row in acquisition),
        "acquisition_statuses": [{"status": row["scrape_status"], "count": int(row["count"])} for row in acquisition],
        "extraction_statuses": [{"status": row["extraction_status"], "count": int(row["count"])} for row in extraction],
        "successful_extracted_page_count": sum(int(row["count"]) for row in extraction if row["extraction_status"] == "success"),
        "usable_page_count": usable_pages,
        "total_extracted_words": total_words,
    }


def load_scoring_context(connection: sqlite3.Connection, scoring_run_id: str | None) -> dict[str, Any]:
    if not scoring_run_id:
        return {"scoring_run_id": None, "available": False, "warning": "scoring_run_not_configured"}
    row = connection.execute("SELECT * FROM scoring_runs WHERE scoring_run_id = ?", (scoring_run_id,)).fetchone()
    if row is None:
        return {"scoring_run_id": scoring_run_id, "available": False, "warning": "scoring_run_missing"}
    return {
        "scoring_run_id": scoring_run_id,
        "available": True,
        "status": row["status"],
        "sample_id": row["sample_id"],
        "extraction_version": row["extraction_version"],
        "scoring_profile_id": row["scoring_profile_id"],
        "scoring_profile_version": row["scoring_profile_version"],
        "configuration_hash": row["configuration_hash"],
    }


def load_analysis_pages(connection: sqlite3.Connection, pages: list[dict[str, Any]], scoring_run_id: str | None) -> tuple[list[dict[str, Any]], str]:
    if not scoring_run_id:
        return [{"scraped_page_id": page["scraped_page_id"], "extracted_page_id": page["extracted_page_id"], "scores": []} for page in pages], "missing"
    results: list[dict[str, Any]] = []
    score_count = 0
    for page in pages:
        rows = connection.execute(
            """SELECT dimension, query_score, keyword_score, boost_score, negative_score, final_score,
                      top_queries_json, top_keywords_json, top_boost_terms_json, top_negative_terms_json,
                      knowledge_base_id, knowledge_base_version, scoring_profile_id, scoring_profile_version,
                      extraction_version
               FROM page_scores WHERE scoring_run_id=? AND extracted_page_id=? ORDER BY dimension""",
            (scoring_run_id, page["extracted_page_id"]),
        ).fetchall()
        scores = []
        for row in rows:
            score_count += 1
            scores.append({
                "dimension": row["dimension"], "query_score": row["query_score"], "keyword_score": row["keyword_score"],
                "boost_score": row["boost_score"], "negative_score": row["negative_score"], "final_score": row["final_score"],
                "top_queries": parse_json_list(row["top_queries_json"], [], "top_queries"),
                "top_keywords": parse_json_list(row["top_keywords_json"], [], "top_keywords"),
                "top_boost_terms": parse_json_list(row["top_boost_terms_json"], [], "top_boost_terms"),
                "top_negative_terms": parse_json_list(row["top_negative_terms_json"], [], "top_negative_terms"),
                "knowledge_base_id": row["knowledge_base_id"], "knowledge_base_version": row["knowledge_base_version"],
                "scoring_profile_id": row["scoring_profile_id"], "scoring_profile_version": row["scoring_profile_version"],
                "extraction_version": row["extraction_version"],
            })
        results.append({"scraped_page_id": page["scraped_page_id"], "extracted_page_id": page["extracted_page_id"], "url": page["url"], "content_sha256": page["content_sha256"], "scores": scores})
    expected = len(pages) * 4
    return results, "complete" if expected and score_count == expected else ("missing" if score_count == 0 else "partial")


def neutral_page(page: dict[str, Any], content: str, status: str) -> dict[str, Any]:
    return {
        "scraped_page_id": page["scraped_page_id"], "extracted_page_id": page["extracted_page_id"],
        "url": page["url"], "normalized_url": page["normalized_url"], "title": page["title"],
        "headings": page["headings"], "page_type": page["page_type"], "language": page["language"],
        "word_count": page["word_count"], "extraction_version": page["extraction_version"],
        "content_status": status, "content": content,
    }


def render_blind_markdown(payload: dict[str, Any]) -> str:
    company = payload["focal_company"]
    availability = payload["evidence_availability"]
    lines = ["# Company Calibration Evidence", "", "## Focal Company", ""]
    for label, key in (("Company ID", "company_id"), ("Company name", "company_name"), ("Normalized domain", "normalized_domain"), ("Pilot sample", "pilot_sample_id"), ("ONACE 2008 activity code", "nace_code"), ("Activity description", "activity_description")):
        lines.append(f"- {label}: {company.get(key) if company.get(key) is not None else 'Missing'}")
    if company.get("shared_domain"):
        lines.extend(["", "### Shared-domain warning", "", "Website evidence belongs to a domain mapped to multiple companies. The focal company is identified above; domain-level content may not be attributable to it."])
    lines.extend(["", "## Evidence Availability", ""])
    for key in ("acquired_page_count", "successful_extracted_page_count", "usable_page_count", "total_extracted_words"):
        lines.append(f"- {key.replace('_', ' ').capitalize()}: {availability.get(key, 0)}")
    pages = payload["pages"]
    full_count = sum(1 for page in pages if page.get("content_status") == "complete")
    partial_count = sum(1 for page in pages if page.get("content_status") == "partially_truncated")
    metadata_only_count = sum(1 for page in pages if page.get("content_status") == "metadata_only")
    lines.extend(
        [
            "",
            "## Page Inclusion Summary",
            "",
            f"- Usable extracted pages: {len(pages)}",
            f"- Pages included with full text: {full_count}",
            f"- Pages included with partial text: {partial_count}",
            f"- Additional metadata-only pages omitted from model input: {metadata_only_count}",
            "",
            "## Pages",
            "",
        ]
    )
    visible_number = 0
    for page in pages:
        if page.get("content_status") not in MODEL_VISIBLE_CONTENT_STATUSES:
            continue
        visible_number += 1
        lines.extend([f"### Page {visible_number}", "", f"- Scraped page ID: {page['scraped_page_id']}", f"- Extracted page ID: {page['extracted_page_id']}", f"- URL: {page['url']}", f"- Title: {page['title'] or 'Missing'}", f"- Language: {page['language'] or 'Missing'}", f"- Word count: {page['word_count']}", f"- Extraction version: {page['extraction_version']}", f"- Content availability: {page['content_status']}"])
        headings = page.get("headings") or []
        lines.append("- Headings: " + ("; ".join(str(item.get("text")) for item in headings) if headings else "None extracted"))
        lines.extend(["", "Content:", "```text", page.get("content", ""), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def strip_fenced_content(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)


def validate_blind_payload(payload: dict[str, Any], markdown: str | None = None) -> None:
    """Reject builder-generated scoring leakage while allowing genuine page text."""
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).casefold()
                if lowered in FORBIDDEN_SCORER_KEYS:
                    raise ValueError(f"Blind package scorer leakage at {path}{key}")
                walk(child, f"{path}{key}.")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}{index}.")
    walk(payload)
    if markdown:
        template_text = strip_fenced_content(markdown).casefold()
        for label in FORBIDDEN_MARKDOWN_LABELS:
            if label in template_text:
                raise ValueError(f"Blind Markdown scorer leakage: {label}")


def truncate_pages(base_payload: dict[str, Any], pages: list[dict[str, Any]], max_tokens: int) -> tuple[list[dict[str, Any]], int, int, int, int]:
    full_payload = {**base_payload, "pages": [neutral_page(page, page["content"], "complete") for page in pages]}
    before = estimate_tokens(render_blind_markdown(full_payload))
    packaged = [neutral_page(page, "", "metadata_only") for page in pages]
    full_count = partial_count = 0
    for index, page in enumerate(pages):
        candidate = [*packaged]
        complete = neutral_page(page, page["content"], "complete")
        candidate[index] = complete
        if estimate_tokens(render_blind_markdown({**base_payload, "pages": candidate})) <= max_tokens:
            packaged[index] = complete
            full_count += 1
            continue

        candidate[index] = neutral_page(page, "", "partially_truncated")
        if estimate_tokens(render_blind_markdown({**base_payload, "pages": candidate})) <= max_tokens:
            low, high = 0, len(page["content"])
            while low < high:
                midpoint = (low + high + 1) // 2
                candidate[index] = neutral_page(page, page["content"][:midpoint].rstrip(), "partially_truncated")
                if estimate_tokens(render_blind_markdown({**base_payload, "pages": candidate})) <= max_tokens:
                    low = midpoint
                else:
                    high = midpoint - 1
            if low > 0:
                packaged[index] = neutral_page(page, page["content"][:low].rstrip(), "partially_truncated")
                partial_count += 1
        break
    after = estimate_tokens(render_blind_markdown({**base_payload, "pages": packaged}))
    return packaged, before, after, full_count, partial_count


def company_payload(connection: sqlite3.Connection, project_root: Path, company: CompanyRecord, sample_id: str, scoring_run_id: str | None, max_tokens: int) -> tuple[dict[str, Any], dict[str, Any], PackageResult]:
    pages, warnings = load_pages(connection, company, project_root)
    mapped = related_companies(connection, company.domain)
    shared = len(mapped) > 1
    total_words = sum(page["word_count"] for page in pages)
    availability = load_availability(connection, company.domain, len(pages), total_words)
    focal_company = {
        "company_id": company.company_id, "company_name": company.company_name,
        "normalized_domain": company.domain, "source_url": company.normalized_url or company.raw_url,
        "pilot_sample_id": sample_id, "pilot_sample_domains": list(company.sample_domains), "nace_code": company.nace_code,
        "activity_description": None, "industry_metadata": {"domain_category": company.domain_category, "representation_level": company.representation_level},
        "legal_entity_metadata": {"mark": company.mark, "city": company.city, "postal_code": company.postal_code},
        "shared_domain": shared, "mapped_company_count": len(mapped), "other_mapped_companies": [item for item in mapped if item["company_id"] != company.company_id],
        "missing_fields": [name for name, value in (("company_name", company.company_name), ("normalized_domain", company.domain), ("nace_code", company.nace_code), ("activity_description", None)) if value is None],
    }
    base_payload = {"blind_to_scoring": True, "package_type": "blind_company_calibration_evidence", "focal_company": focal_company, "evidence_availability": availability}
    packaged_pages, before, after, full_count, partial_count = truncate_pages(base_payload, pages, max_tokens)
    metadata_count = sum(1 for page in packaged_pages if page["content_status"] == "metadata_only")
    blind = {**base_payload, "token_budget": {"max_package_tokens": max_tokens, "estimated_tokens_before_truncation": before, "estimated_tokens_after_truncation": after, "token_estimator": "deterministic_regex_approximation", "pages_complete": full_count, "pages_partially_truncated": partial_count, "pages_metadata_only": metadata_count, "estimated_tokens_omitted": max(0, before - after)}, "pages": packaged_pages}
    markdown = render_blind_markdown(blind)
    validate_blind_payload(blind, markdown)
    scoring_context = load_scoring_context(connection, scoring_run_id)
    analysis_pages, coverage = load_analysis_pages(connection, pages, scoring_run_id)
    if coverage != "complete":
        warnings.append(f"score_coverage:{coverage}")
    knowledge_base_versions = sorted(
        {
            (score.get("knowledge_base_id"), score.get("knowledge_base_version"))
            for page in analysis_pages
            for score in page["scores"]
            if score.get("knowledge_base_id") or score.get("knowledge_base_version")
        }
    )
    analysis = {
        "send_to_openai": False,
        "purpose": "post-hoc comparison of independent calibration labels against page scoring",
        "company_id": company.company_id,
        "scoring_context": {**scoring_context, "knowledge_base_versions": [list(item) for item in knowledge_base_versions]},
        "score_coverage": coverage,
        "pages": analysis_pages,
    }
    result = PackageResult(company.company_id, company.company_name, company.domain, shared, len(mapped), len(pages), len(pages), total_words, before, after, bool(partial_count or metadata_count), full_count, partial_count, metadata_count, coverage, "", "", tuple(warnings))
    return blind, analysis, result


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_company_evidence_packages(*, project_root: Path | None = None, sample_id: str | None = None, company_ids: Iterable[str] = (), domain: str | None = None, company_limit: int | None = None, max_package_tokens: int | None = None, output_dir: str | Path | None = None, force_refresh: bool = False, progress_every: int = 10) -> tuple[Path, dict[str, Any]]:
    root = project_root or find_project_root()
    config = load_calibration_config(root)
    selected_sample = sample_id or config.default_sample_id
    token_limit = max_package_tokens or config.default_max_package_tokens
    if token_limit <= 0:
        raise ValueError("max_package_tokens must be positive")
    init_database(root)
    app_config = load_config(root)
    db_path = resolve_database_path(root, app_config)
    base_output = resolve_project_path(root, str(output_dir or "data/interim/calibration/company_evidence"))
    package_run_id = f"{safe_filename(config.package_builder_id)}_{safe_filename(config.package_builder_version)}_{safe_filename(selected_sample)}"
    run_dir = base_output / package_run_id
    if run_dir.exists():
        if not force_refresh:
            raise FileExistsError(f"Calibration package already exists: {run_dir}. Use --force-refresh to replace it.")
        shutil.rmtree(run_dir)
    blind_dir, analysis_dir = run_dir / "blind_inputs", run_dir / "analysis_metadata"
    blind_dir.mkdir(parents=True, exist_ok=False)
    analysis_dir.mkdir(parents=True, exist_ok=False)

    started = time.monotonic()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        selected_scoring_context = load_scoring_context(connection, config.default_scoring_run_id)
        companies = select_companies(connection, sample_id=selected_sample, company_ids=company_ids, domain=domain, company_limit=company_limit)
        if not companies:
            raise ValueError(f"No companies found for pilot sample '{selected_sample}' and supplied filters")
        results: list[PackageResult] = []
        for index, company in enumerate(companies, start=1):
            blind, analysis, result = company_payload(connection, root, company, selected_sample, config.default_scoring_run_id, token_limit)
            markdown = render_blind_markdown(blind)
            blind_markdown = blind_dir / f"{safe_filename(company.company_id)}.md"
            blind_json = blind_dir / f"{safe_filename(company.company_id)}.json"
            analysis_json = analysis_dir / f"{safe_filename(company.company_id)}.json"
            blind_markdown.write_text(markdown, encoding="utf-8")
            write_json(blind_json, blind)
            write_json(analysis_json, analysis)
            results.append(PackageResult(**{**result.__dict__, "blind_input_path": str(blind_markdown.relative_to(run_dir)), "analysis_metadata_path": str(analysis_json.relative_to(run_dir))}))
            if progress_every > 0 and (index == len(companies) or index % progress_every == 0):
                print(f"Packaged {index}/{len(companies)} companies")

    summary_path = run_dir / "package_summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as stream:
        fields = list(PackageResult.__dataclass_fields__)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = result.__dict__.copy()
            row["warnings"] = "; ".join(result.warnings)
            writer.writerow(row)
    warnings = [warning for result in results for warning in result.warnings]
    manifest = {
        "package_run_id": package_run_id, "generated_at": utc_now_iso(), "sample_id": selected_sample,
        "company_count": len(results), "domain_count": len({result.domain for result in results if result.domain}),
        "scoring_run_id_for_analysis_metadata": config.default_scoring_run_id,
        "extraction_version": selected_scoring_context.get("extraction_version"),
        "scoring_profile_id": selected_scoring_context.get("scoring_profile_id"),
        "scoring_profile_version": selected_scoring_context.get("scoring_profile_version"),
        "max_blind_package_tokens": token_limit, "tokenizer_model_assumption": config.tokenizer_model,
        "token_estimator": "deterministic_regex_approximation", "total_estimated_blind_input_tokens": sum(result.estimated_tokens_after for result in results),
        "companies_truncated": sum(result.truncated for result in results), "companies_missing_scores": sum(result.score_coverage != "complete" for result in results),
        "companies_missing_extracted_content": sum(result.usable_page_count == 0 for result in results),
        "shared_domain_company_count": sum(result.shared_domain for result in results),
        "warnings": sorted(set(warnings)), "blind_inputs_contain_no_scoring_information": True,
        "analysis_metadata_must_not_be_sent_to_openai": True, "config_hash": config.config_hash,
        "package_builder_id": config.package_builder_id, "package_builder_version": config.package_builder_version,
        "blind_input_paths": [result.blind_input_path for result in results], "analysis_metadata_paths": [result.analysis_metadata_path for result in results],
        "package_summary_path": str(summary_path.relative_to(run_dir)), "runtime_seconds": round(time.monotonic() - started, 3),
    }
    write_json(run_dir / "manifest.json", manifest)
    return run_dir, manifest


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build blind company calibration evidence packages.")
    parser.add_argument("--sample-id")
    parser.add_argument("--company-id", action="append", default=[])
    parser.add_argument("--domain")
    parser.add_argument("--company-limit", type=positive_int)
    parser.add_argument("--max-package-tokens", type=positive_int)
    parser.add_argument("--output-dir")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--progress-every", type=positive_int, default=10)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run_dir, manifest = build_company_evidence_packages(sample_id=args.sample_id, company_ids=args.company_id, domain=args.domain, company_limit=args.company_limit, max_package_tokens=args.max_package_tokens, output_dir=args.output_dir, force_refresh=args.force_refresh, progress_every=args.progress_every)
    print(f"Calibration evidence packages written to {run_dir}")
    print(f"Companies: {manifest['company_count']}; domains: {manifest['domain_count']}; shared-domain companies: {manifest['shared_domain_company_count']}")
    print(f"Estimated blind-input tokens: {manifest['total_estimated_blind_input_tokens']}; truncated companies: {manifest['companies_truncated']}")


if __name__ == "__main__":
    main()
