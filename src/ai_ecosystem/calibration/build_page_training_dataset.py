"""Build the versioned, leakage-safe scorer-v2 page training dataset.

This is an artifact builder only. It preserves the frozen annotation splits,
applies QA target restrictions, and keeps scorer-v1 data in a separate optional
feature table. It does not fit vectorizers or models.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml

from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS
from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


SPLITS = ("development", "model_selection", "locked_retrieval_test")
ID_COLUMNS = ("annotation_item_id", "company_id", "scraped_page_id")
FORBIDDEN_PREDICTIVE_COLUMNS = {
    "company_label",
    "split",
    "sampling_pool",
    "selection_reason",
    "scorer_v1_diagnostic",
    "calibration_source",
}
ABOUT_COMPONENTS = frozenset(
    {"about", "about-us", "company", "unternehmen", "ueber-uns", "über-uns", "ueber", "who-we-are"}
)
PAGE_TYPE_VALUES = (
    "homepage",
    "about",
    "company",
    "products",
    "services",
    "technology",
    "innovation",
    "news",
    "careers",
    "contact",
    "legal",
    "other",
)


@dataclass(frozen=True)
class PageTrainingDatasetConfig:
    dataset_id: str
    dataset_version: str
    annotation_sample_id: str
    annotation_sample_directory: str
    annotation_run_id: str
    annotation_run_directory: str
    qa_version: str
    output_directory: str
    required_extraction_version: str
    exclude_cross_split_exact_duplicates_from_modeling: bool
    config_hash: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_dataset_config(path: Path) -> PageTrainingDatasetConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Page training dataset configuration must be a mapping")
    required = (
        "dataset_id",
        "dataset_version",
        "annotation_sample_id",
        "annotation_sample_directory",
        "annotation_run_id",
        "annotation_run_directory",
        "qa_version",
        "output_directory",
        "required_extraction_version",
    )
    missing = [key for key in required if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Missing page training dataset configuration: {', '.join(missing)}")
    return PageTrainingDatasetConfig(
        **{key: str(raw[key]).strip() for key in required},
        exclude_cross_split_exact_duplicates_from_modeling=bool(
            raw.get("exclude_cross_split_exact_duplicates_from_modeling", True)
        ),
        config_hash=sha256_text(canonical_json(raw)),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            rows.append(value)
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def parse_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes"}


def chunks(values: Iterable[int], size: int = 400) -> Iterator[tuple[int, ...]]:
    chunk: list[int] = []
    for value in values:
        chunk.append(int(value))
        if len(chunk) == size:
            yield tuple(chunk)
            chunk = []
    if chunk:
        yield tuple(chunk)


def placeholders(values: Iterable[Any]) -> str:
    values = tuple(values)
    if not values:
        raise ValueError("At least one SQL value is required")
    return ",".join("?" for _ in values)


def _word_count(value: str | None) -> int:
    return len(str(value or "").split())


def _url_features(url: str) -> dict[str, Any]:
    try:
        parsed = urlsplit(url)
        components = [unquote(part).casefold() for part in parsed.path.split("/") if part]
        query_count = len(parse_qsl(parsed.query, keep_blank_values=True))
    except ValueError:
        components, query_count = [], 0
    depth = len(components)
    return {
        "url__depth": depth,
        "url__path_char_count": sum(len(value) for value in components),
        "url__query_parameter_count": query_count,
        "url__has_query": int(query_count > 0),
        "url__is_root": int(depth == 0),
        "url__is_about_company": int(any(value in ABOUT_COMPONENTS for value in components)),
        "url__is_shallow": int(depth <= 1),
    }


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _content_features(markdown: str, title: str, headings: list[str], db_row: dict[str, Any]) -> dict[str, Any]:
    lines = markdown.splitlines()
    nonempty = [line for line in lines if line.strip()]
    paragraphs = [part for part in markdown.split("\n\n") if part.strip()]
    heading_text = " ".join(headings)
    words = markdown.split()
    result: dict[str, Any] = {
        "text__markdown_char_count": len(markdown),
        "text__markdown_word_count": len(words),
        "text__line_count": len(lines),
        "text__nonempty_line_count": len(nonempty),
        "text__paragraph_count_computed": len(paragraphs),
        "text__average_word_length": _safe_ratio(sum(len(word) for word in words), len(words)),
        "text__digit_character_ratio": _safe_ratio(sum(char.isdigit() for char in markdown), len(markdown)),
        "text__uppercase_character_ratio": _safe_ratio(sum(char.isupper() for char in markdown), len(markdown)),
        "structure__title_char_count": len(title),
        "structure__title_word_count": _word_count(title),
        "structure__heading_count": len(headings),
        "structure__heading_char_count": len(heading_text),
        "structure__heading_word_count": _word_count(heading_text),
        "structure__markdown_heading_line_count": sum(line.lstrip().startswith("#") for line in lines),
        "structure__list_line_count": sum(line.lstrip().startswith(("- ", "* ", "+ ")) for line in lines),
        "structure__feature_word_count": int(db_row.get("word_count") or 0),
        "structure__feature_heading_count": int(db_row.get("heading_count") or 0),
        "structure__feature_paragraph_count": int(db_row.get("paragraph_count") or 0),
    }
    hierarchy = parse_json(db_row.get("heading_hierarchy"), [])
    for level in range(1, 7):
        result[f"structure__h{level}_count"] = sum(
            isinstance(item, dict) and item.get("level") == level for item in hierarchy
        )
    return result


def _link_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    internal = [row for row in rows if str(row.get("internal_external") or "").casefold() == "internal" or row.get("is_internal") == 1]
    external = [row for row in rows if row not in internal]
    anchors = [int(row.get("anchor_word_count") or _word_count(row.get("anchor_text"))) for row in rows]
    return {
        "link__total_count": len(rows),
        "link__internal_count": len(internal),
        "link__external_count": len(external),
        "link__internal_ratio": _safe_ratio(len(internal), len(rows)),
        "link__anchor_word_count_sum": sum(anchors),
        "link__anchor_word_count_mean": _safe_ratio(sum(anchors), len(anchors)),
        "link__anchor_word_count_max": max(anchors, default=0),
        "link__empty_anchor_count": sum(value == 0 for value in anchors),
        "link__unique_target_domain_count": len({row.get("normalized_target_domain") or row.get("target_domain") for row in rows if row.get("normalized_target_domain") or row.get("target_domain")}),
        "link__nofollow_count": sum("nofollow" in str(row.get("rel_attribute") or "").casefold().split() for row in rows),
    }


def _language_features(language: str | None) -> dict[str, int]:
    normalized = str(language or "").casefold().replace("_", "-")
    group = "de" if normalized.startswith("de") else "en" if normalized.startswith("en") else "unknown" if not normalized or normalized in {"und", "zxx", "unknown"} else "other"
    return {f"language__is_{value}": int(group == value) for value in ("de", "en", "other", "unknown")}


def _page_type_features(value: str | None) -> dict[str, int]:
    raw = str(value or "").casefold().replace("-", "_")
    selected = "other"
    for candidate in PAGE_TYPE_VALUES[:-1]:
        if candidate in raw:
            selected = candidate
            break
    return {f"page_type__is_{candidate}": int(selected == candidate) for candidate in PAGE_TYPE_VALUES}


def load_database_rows(
    connection: sqlite3.Connection,
    page_rows: list[dict[str, Any]],
    company_rows: dict[str, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], dict[tuple[str, int, str], dict[str, Any]]]:
    extracted_ids = sorted({int(row["extracted_page_id"]) for row in page_rows})
    page_metadata: dict[int, dict[str, Any]] = {}
    links: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for group in chunks(extracted_ids):
        params = tuple(group)
        query = f"""
            SELECT e.extracted_page_id, e.scraped_page_id, e.extraction_version,
                   e.extraction_status, e.content_sha256, e.markdown_path, e.text_path,
                   s.url, s.final_url, s.domain, s.content_length, s.http_status,
                   COALESCE(pf.title, s.title) AS title,
                   COALESCE(pf.language, e.language, s.language) AS language,
                   pf.word_count, pf.heading_count, pf.headings, pf.heading_hierarchy,
                   pf.paragraph_count, pf.page_type_guess, pf.feature_version
            FROM extracted_pages e
            JOIN scraped_pages s ON s.scraped_page_id = e.scraped_page_id
            LEFT JOIN page_features pf ON pf.extracted_page_id = e.extracted_page_id
            WHERE e.extracted_page_id IN ({placeholders(params)})
        """
        for row in connection.execute(query, params).fetchall():
            page_metadata[int(row["extracted_page_id"])] = dict(row)
        link_query = f"SELECT * FROM page_links WHERE extracted_page_id IN ({placeholders(params)}) ORDER BY extracted_page_id, link_order, id"
        for row in connection.execute(link_query, params).fetchall():
            links[int(row["extracted_page_id"])].append(dict(row))

    scores: dict[tuple[str, int, str], dict[str, Any]] = {}
    by_run: dict[str, list[int]] = defaultdict(list)
    for page in page_rows:
        run_id = str(company_rows[str(page["company_id"])]["scoring_run_id"])
        by_run[run_id].append(int(page["extracted_page_id"]))
    for run_id, ids in sorted(by_run.items()):
        for group in chunks(sorted(set(ids))):
            query = f"SELECT * FROM page_scores WHERE scoring_run_id = ? AND extracted_page_id IN ({placeholders(group)}) ORDER BY extracted_page_id, dimension"
            for row in connection.execute(query, (run_id, *group)).fetchall():
                item = dict(row)
                scores[(run_id, int(item["extracted_page_id"]), str(item["dimension"]))] = item
    return page_metadata, links, scores


def _restricted_targets(annotation: dict[str, Any], qa: dict[str, Any]) -> dict[str, Any]:
    trust = str(qa["trust_flag"])
    all_allowed = trust != "untrusted"
    ungrounded_facets = set(parse_json(qa.get("ungrounded_positive_facets"), []))
    soft = parse_json(qa.get("soft_inconsistencies"), [])
    restrict_category = any(str(value).startswith("category_without_matching_noise_flag:") for value in soft)
    restricted_noise: set[str] = set()
    category_noise = {
        "news_press_or_blog": "news_or_press",
        "navigation_or_low_content": "navigation_or_low_content",
        "legal_or_compliance": "legal_or_compliance",
        "careers": "careers",
    }
    for value in soft:
        prefix = "category_without_matching_noise_flag:"
        if str(value).startswith(prefix):
            noise = category_noise.get(str(value)[len(prefix):])
            if noise:
                restricted_noise.add(noise)

    row: dict[str, Any] = {
        "annotation_item_id": qa["annotation_item_id"],
        "company_id": qa["company_id"],
        "scraped_page_id": int(qa["scraped_page_id"]),
        "trust_status": trust,
        "trust_reason_codes_json": canonical_json(parse_json(qa.get("trust_reason_codes"), [])),
        "lexical_restriction_reason_codes_json": canonical_json(parse_json(qa.get("lexical_restriction_reason_codes"), [])),
        "target_row_available": all_allowed,
        "utility_score": annotation.get("utility_score") if all_allowed else None,
        "utility_score_available": all_allowed,
        "attribution": annotation.get("attribution") if all_allowed else None,
        "attribution_available": all_allowed,
        "page_category": annotation.get("page_category") if all_allowed and not restrict_category else None,
        "page_category_available": all_allowed and not restrict_category,
        "page_priority_tier": annotation.get("page_priority_tier") if all_allowed else None,
        "page_priority_tier_available": all_allowed,
        "page_priority_is_secondary_target": True,
    }
    for facet in FACETS:
        available = all_allowed and facet not in ungrounded_facets
        row[f"facet__{facet}__strength"] = annotation["evidence_facets"][facet]["strength"] if available else None
        row[f"facet__{facet}__available"] = available
    for flag in NOISE_FLAGS:
        available = all_allowed and flag not in restricted_noise
        row[f"noise__{flag}"] = annotation["noise_flags"][flag] if available else None
        row[f"noise__{flag}__available"] = available
    return row


def _parquet_write(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - analysis extras are expected for this artifact build
        raise RuntimeError("Building Parquet artifacts requires the project analysis dependencies") from error
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False, compression="zstd")


def _table_schema(path: Path) -> list[dict[str, str]]:
    import pyarrow.parquet as parquet

    schema = parquet.read_schema(path)
    return [{"name": field.name, "type": str(field.type)} for field in schema]


def _summarize_missing_targets(targets: list[dict[str, Any]]) -> dict[str, int]:
    columns = [key for key in targets[0] if key not in ID_COLUMNS and not key.endswith("_available") and key not in {"trust_status", "trust_reason_codes_json", "lexical_restriction_reason_codes_json", "page_priority_is_secondary_target", "target_row_available"}]
    return {column: sum(row.get(column) is None for row in targets) for column in columns}


def build_dataset(
    connection: sqlite3.Connection,
    project_root: Path,
    config: PageTrainingDatasetConfig,
    config_path: Path,
    output_directory: Path,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    connection.row_factory = sqlite3.Row
    sample_dir = project_root / config.annotation_sample_directory
    run_dir = project_root / config.annotation_run_directory
    qa_dir = run_dir / config.qa_version
    sample_manifest = json.loads((sample_dir / "manifest.json").read_text(encoding="utf-8"))
    if sample_manifest.get("sample_id") != config.annotation_sample_id:
        raise ValueError("Annotation sample identity does not match dataset config")
    run_metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    if run_metadata.get("run_id") != config.annotation_run_id:
        raise ValueError("Annotation run identity does not match dataset config")
    if run_metadata.get("status") not in {"completed", "ingested"}:
        raise ValueError(f"Annotation run is not completed: {run_metadata.get('status')}")
    if run_metadata.get("sample_id") != config.annotation_sample_id:
        raise ValueError("Annotation run sample does not match dataset config")
    annotation_config_path = project_root / "config" / "page_annotation.yaml"
    if annotation_config_path.is_file():
        annotation_config_raw = yaml.safe_load(annotation_config_path.read_text(encoding="utf-8")) or {}
        annotation_config_hash = sha256_text(canonical_json(annotation_config_raw))
        if annotation_config_hash != run_metadata.get("config_sha256"):
            raise ValueError("Current versioned annotation config does not match the completed run hash")
    page_rows = read_jsonl(sample_dir / "pages.jsonl")
    company_list = read_jsonl(sample_dir / "companies.jsonl")
    company_rows = {str(row["company_id"]): row for row in company_list}
    annotation_rows = [row for row in read_jsonl(run_dir / "annotations.jsonl") if not row.get("is_hidden_repeat")]
    annotation_by_item = {str(row["annotation_item_id"]): row for row in annotation_rows}
    input_rows = read_jsonl(run_dir / "model_inputs.jsonl")
    input_by_custom = {str(row["custom_id"]): row for row in input_rows}
    qa_rows = [row for row in read_csv(qa_dir / "annotation_qa.csv") if not parse_bool(row["is_hidden_repeat"])]
    qa_by_item = {str(row["annotation_item_id"]): row for row in qa_rows}
    phrase_rows = read_csv(qa_dir / "grounded_phrases.csv")

    page_rows.sort(key=lambda row: (SPLITS.index(str(row["split"])), str(row["company_id"]), int(row["scraped_page_id"])))
    split_mismatches = [
        row["annotation_item_id"]
        for row in page_rows
        if str(row["split"]) != str(company_rows[str(row["company_id"])]["split"])
    ]
    if split_mismatches:
        raise ValueError(f"Page/company split assignment mismatch for {len(split_mismatches)} items")
    expected_items = {str(row["annotation_item_id"]) for row in page_rows}
    unresolved = sorted(expected_items - annotation_by_item.keys() | expected_items - qa_by_item.keys())
    if unresolved:
        raise ValueError(f"Missing annotation/QA joins for {len(unresolved)} items")
    if len(page_rows) != len({(row["company_id"], row["scraped_page_id"]) for row in page_rows}):
        raise ValueError("Canonical company/page units are not unique")

    page_metadata, links_by_page, scores = load_database_rows(connection, page_rows, company_rows)
    missing_db = [row["annotation_item_id"] for row in page_rows if int(row["extracted_page_id"]) not in page_metadata]
    invalid_extraction = [
        row["annotation_item_id"]
        for row in page_rows
        if int(row["extracted_page_id"]) in page_metadata
        and (
            page_metadata[int(row["extracted_page_id"])]["extraction_status"] != "success"
            or page_metadata[int(row["extracted_page_id"])]["extraction_version"] != config.required_extraction_version
        )
    ]
    if missing_db or invalid_extraction:
        raise ValueError(f"Extraction join failed: missing={len(missing_db)}, incompatible={len(invalid_extraction)}")

    hash_splits: dict[str, set[str]] = defaultdict(set)
    hash_rows: Counter[str] = Counter()
    for row in page_rows:
        content_hash = str(row.get("content_sha256") or "")
        if content_hash:
            hash_splits[content_hash].add(str(row["split"]))
            hash_rows[content_hash] += 1
    cross_split_hashes = {value for value, splits in hash_splits.items() if len(splits) > 1}

    company_selected_counts = Counter(str(row["company_id"]) for row in page_rows)
    domain_selected_counts = Counter(str(row["domain"]) for row in page_rows)
    canonical_rows: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    scorer_rows: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    missing_score_rows = 0

    for page in page_rows:
        item_id = str(page["annotation_item_id"])
        company_id = str(page["company_id"])
        extracted_id = int(page["extracted_page_id"])
        metadata = page_metadata[extracted_id]
        company = company_rows[company_id]
        annotation_record = annotation_by_item[item_id]
        qa = qa_by_item[item_id]
        annotation = dict(annotation_record["annotation"])
        model_input = input_by_custom[str(annotation_record["custom_id"])]
        headings = model_input.get("headings") if isinstance(model_input.get("headings"), list) else parse_json(metadata.get("headings"), [])
        headings = [str(value) for value in headings]
        markdown = str(model_input.get("extracted_markdown") or "")
        title = str(model_input.get("title") or metadata.get("title") or "")
        content_hash = str(page.get("content_sha256") or metadata.get("content_sha256") or "")
        cross_split_duplicate = bool(content_hash and content_hash in cross_split_hashes)
        modeling_eligible = not (
            config.exclude_cross_split_exact_duplicates_from_modeling and cross_split_duplicate
        )
        target = _restricted_targets(annotation, qa)
        targets.append(target)

        canonical_rows.append(
            {
                "annotation_item_id": item_id,
                "annotation_rank": int(page["annotation_rank"]),
                "company_id": company_id,
                "scraped_page_id": int(page["scraped_page_id"]),
                "extracted_page_id": extracted_id,
                "domain": str(page["domain"]),
                "normalized_url": str(page.get("normalized_url") or metadata.get("final_url") or metadata.get("url") or ""),
                "title": title,
                "language": str(metadata.get("language") or page.get("language") or "unknown"),
                "page_type": str(metadata.get("page_type_guess") or page.get("page_type") or "unknown"),
                "content_sha256": content_hash or None,
                "extraction_version": str(metadata["extraction_version"]),
                "feature_version": metadata.get("feature_version"),
                "split": str(page["split"]),
                "company_label_audit_only": str(company["label"]),
                "sampling_pool_audit_only": str(page["sampling_pool"]),
                "selection_reason_audit_only": str(page["selection_reason"]),
                "scorer_v1_diagnostic_audit_only": bool(page["scorer_v1_diagnostic"]),
                "calibration_source_audit_only": str(company["calibration_sample_id"]),
                "shared_domain": bool(company["shared_domain"]),
                "trust_status": str(qa["trust_flag"]),
                "trust_reason_codes_json": canonical_json(parse_json(qa.get("trust_reason_codes"), [])),
                "cross_split_exact_duplicate": cross_split_duplicate,
                "modeling_eligible": modeling_eligible,
            }
        )

        ids = {"annotation_item_id": item_id, "company_id": company_id, "scraped_page_id": int(page["scraped_page_id"])}
        features: dict[str, Any] = dict(ids)
        features.update(_url_features(str(page.get("normalized_url") or metadata.get("url") or "")))
        features.update(_content_features(markdown, title, headings, metadata))
        features.update(_link_features(links_by_page.get(extracted_id, [])))
        features.update(_language_features(metadata.get("language") or page.get("language")))
        features.update(_page_type_features(metadata.get("page_type_guess") or page.get("page_type")))
        features.update(
            {
                "duplicate__content_hash_group_size": hash_rows.get(content_hash, 1),
                "duplicate__within_domain_group_size": int(page.get("duplicate_group_size") or 1),
                "duplicate__has_exact_duplicate": int(hash_rows.get(content_hash, 1) > 1 or int(page.get("duplicate_group_size") or 1) > 1),
                "duplicate__cross_split": int(cross_split_duplicate),
                "site__company_usable_page_count": int(company.get("raw_usable_pages") or 0),
                "site__company_unique_content_page_count": int(company.get("unique_content_pages") or 0),
                "site__company_selected_page_count": company_selected_counts[company_id],
                "site__domain_selected_page_count": domain_selected_counts[str(page["domain"])],
                "site__shared_domain": int(bool(company.get("shared_domain"))),
                "site__mapped_company_count": int(company.get("mapped_company_count") or 1),
                "site__attribution_uncertain": int(bool(company.get("attribution_uncertain"))),
            }
        )
        forbidden = FORBIDDEN_PREDICTIVE_COLUMNS.intersection(features)
        if forbidden:
            raise ValueError(f"Forbidden metadata entered predictive features: {sorted(forbidden)}")
        feature_rows.append(features)

        score_features: dict[str, Any] = dict(ids)
        score_features["scorer_v1__source_run_id"] = str(company["scoring_run_id"])
        score_count = 0
        for dimension in ("firm_profile", "technology", "innovation", "ecosystem"):
            score = scores.get((str(company["scoring_run_id"]), extracted_id, dimension))
            for component in ("query_score", "keyword_score", "boost_score", "negative_score", "final_score"):
                score_features[f"scorer_v1__{dimension}__{component}"] = score.get(component) if score else None
            score_count += int(score is not None)
        score_features["scorer_v1__dimension_rows_available"] = score_count
        missing_score_rows += int(score_count != 4)
        scorer_rows.append(score_features)

        documents.append(
            {
                **ids,
                "split": str(page["split"]),
                "modeling_eligible": modeling_eligible,
                "model_input_custom_id": str(annotation_record["custom_id"]),
                "title": title,
                "headings_json": canonical_json(headings),
                "model_visible_markdown": markdown,
                "markdown_truncated": bool(model_input.get("markdown_truncated")),
                "model_visible_char_count": len(markdown),
                "model_visible_sha256": sha256_text(markdown),
                "source_markdown_path": str(metadata.get("markdown_path") or page.get("markdown_path") or ""),
                "source_text_path": str(metadata.get("text_path") or page.get("text_path") or ""),
            }
        )
        split_rows.append(
            {
                **ids,
                "normalized_domain": str(page["domain"]),
                "split": str(page["split"]),
                "shared_domain": bool(company["shared_domain"]),
                "content_sha256": content_hash or None,
                "cross_split_exact_duplicate": cross_split_duplicate,
                "modeling_eligible": modeling_eligible,
                "modeling_exclusion_reason": "cross_split_exact_duplicate" if not modeling_eligible else None,
            }
        )

    qa_custom_to_item = {str(row["custom_id"]): str(row["annotation_item_id"]) for row in qa_rows}
    trust_by_item = {str(row["annotation_item_id"]): str(row["trust_flag"]) for row in qa_rows}
    lexical_rows: list[dict[str, Any]] = []
    for phrase in phrase_rows:
        item_id = qa_custom_to_item.get(str(phrase["custom_id"]))
        if not item_id or trust_by_item[item_id] == "untrusted" or not parse_bool(phrase["lexical_supervision_trusted"]):
            continue
        lexical_rows.append(
            {
                "annotation_item_id": item_id,
                "company_id": str(phrase["company_id"]),
                "scraped_page_id": int(phrase["scraped_page_id"]),
                "facet": str(phrase["facet"]),
                "facet_strength": int(phrase["facet_strength"]),
                "phrase_index": int(phrase["phrase_index"]),
                "phrase": str(phrase["phrase"]),
                "exact_grounded": parse_bool(phrase["exact_grounded"]),
                "normalized_grounded": True,
                "lexical_supervision_trusted": True,
                "reconstructed_context": str(phrase["reconstructed_context"]),
            }
        )
    lexical_rows.sort(key=lambda row: (row["annotation_item_id"], row["facet"], row["phrase_index"]))

    domains_by_split: dict[str, set[str]] = defaultdict(set)
    for row in split_rows:
        domains_by_split[row["normalized_domain"]].add(row["split"])
    domain_leaks = {domain: values for domain, values in domains_by_split.items() if len(values) > 1}
    shared_domain_leaks = {
        domain: values
        for domain, values in domain_leaks.items()
        if any(row["normalized_domain"] == domain and row["shared_domain"] for row in split_rows)
    }
    if domain_leaks:
        raise ValueError(f"Frozen domain split isolation failed for {len(domain_leaks)} domains")

    if output_directory.exists():
        if not force_refresh:
            raise FileExistsError(f"Page training dataset already exists: {output_directory}")
        shutil.rmtree(output_directory)
    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)

    tables = {
        "canonical_pages": ("canonical_pages.parquet", canonical_rows),
        "targets": ("targets.parquet", targets),
        "engineered_features": ("engineered_features.parquet", feature_rows),
        "scorer_v1_features": ("scorer_v1_features.parquet", scorer_rows),
        "trusted_lexical_supervision": ("trusted_lexical_supervision.parquet", lexical_rows),
        "text_documents": ("text_documents.parquet", documents),
        "split_manifest": ("split_manifest.parquet", split_rows),
    }
    for _, (name, rows) in tables.items():
        _parquet_write(temporary / name, rows)

    feature_columns = [column for column in feature_rows[0] if column not in ID_COLUMNS]
    feature_dictionary = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "identifier_columns": list(ID_COLUMNS),
        "predictive_feature_columns": feature_columns,
        "feature_blocks": {
            prefix: [column for column in feature_columns if column.startswith(prefix + "__")]
            for prefix in ("url", "text", "structure", "link", "language", "page_type", "duplicate", "site")
        },
        "raw_text_feature_source": {
            "table": "text_documents.parquet",
            "columns": ["title", "headings_json", "model_visible_markdown"],
            "fit_policy": "Fit vectorizers on development rows only; never fit on model_selection or locked_retrieval_test text.",
        },
        "optional_scorer_v1_block": {
            "table": "scorer_v1_features.parquet",
            "columns": [column for column in scorer_rows[0] if column not in ID_COLUMNS],
            "required_experiment_arms": ["scorer_v1_independent", "scorer_v1_only", "combined"],
        },
        "forbidden_predictive_metadata": sorted(FORBIDDEN_PREDICTIVE_COLUMNS),
        "target_restriction_policy": {
            "untrusted": "All target values are null and unavailable.",
            "partially_trusted_positive_facet_without_grounded_phrase": "Only affected facet strengths are null and unavailable.",
            "partially_trusted_category_noise_mismatch": "Page category and implicated noise flag are null and unavailable.",
            "missing_or_restricted_values": "Remain null; never coerce to zero.",
        },
    }
    (temporary / "feature_dictionary.json").write_text(json.dumps(feature_dictionary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "annotation_rows": len(page_rows),
        "usable_target_rows": sum(row["target_row_available"] for row in targets),
        "excluded_untrusted_rows": sum(row["trust_status"] == "untrusted" for row in targets),
        "partially_trusted_rows": sum(row["trust_status"] == "partially_trusted" for row in targets),
        "counts_by_split": dict(Counter(row["split"] for row in canonical_rows)),
        "company_counts_by_split": dict(Counter(row["split"] for row in company_list)),
        "counts_by_company_label_audit_only": dict(Counter(row["company_label_audit_only"] for row in canonical_rows)),
        "company_counts_by_label_audit_only": dict(Counter(str(row["label"]) for row in company_list)),
        "counts_by_sampling_pool_audit_only": dict(Counter(row["sampling_pool_audit_only"] for row in canonical_rows)),
        "counts_by_language": dict(Counter(row["language"] for row in canonical_rows)),
        "shared_domain_rows": sum(row["shared_domain"] for row in canonical_rows),
        "shared_domain_companies": sum(bool(row["shared_domain"]) for row in company_list),
        "target_missingness": _summarize_missing_targets(targets),
        "feature_block_dimensions": {key: len(value) for key, value in feature_dictionary["feature_blocks"].items()},
        "engineered_feature_count": len(feature_columns),
        "scorer_v1_optional_feature_count": len(feature_dictionary["optional_scorer_v1_block"]["columns"]),
        "trusted_grounded_phrases": len(lexical_rows),
        "missing_scorer_v1_page_rows": missing_score_rows,
        "dropped_or_unresolved_page_joins": len(unresolved) + len(missing_db) + len(invalid_extraction),
        "leakage_checks": {
            "normalized_domain_cross_split_count": len(domain_leaks),
            "shared_domain_cross_split_count": len(shared_domain_leaks),
            "cross_split_exact_content_hashes": len(cross_split_hashes),
            "cross_split_exact_content_rows": sum(row["cross_split_exact_duplicate"] for row in split_rows),
            "cross_split_exact_content_rows_excluded_from_modeling": sum(row["cross_split_exact_duplicate"] and not row["modeling_eligible"] for row in split_rows),
            "predictive_feature_forbidden_column_intersection": sorted(FORBIDDEN_PREDICTIVE_COLUMNS.intersection(feature_columns)),
        },
        "modeling_eligible_rows": sum(row["modeling_eligible"] for row in split_rows),
        "canonical_key_unique": len(canonical_rows) == len({(row["company_id"], row["scraped_page_id"]) for row in canonical_rows}),
        "locked_test_policy": "Labels are preserved but were not summarized for feature/model decisions; locked rows must not be used for model selection.",
    }
    (temporary / "data_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.copyfile(config_path, temporary / "config_snapshot.yaml")
    if annotation_config_path.is_file():
        shutil.copyfile(annotation_config_path, temporary / "annotation_config_snapshot.yaml")

    artifact_names = {key: name for key, (name, _) in tables.items()}
    artifact_names.update(
        {
            "feature_dictionary": "feature_dictionary.json",
            "data_quality_summary": "data_quality_summary.json",
            "config_snapshot": "config_snapshot.yaml",
        }
    )
    if annotation_config_path.is_file():
        artifact_names["annotation_config_snapshot"] = "annotation_config_snapshot.yaml"
    source_files = {
        "annotation_sample_manifest": sample_dir / "manifest.json",
        "annotation_sample_pages": sample_dir / "pages.jsonl",
        "annotation_sample_companies": sample_dir / "companies.jsonl",
        "annotation_sample_config_snapshot": sample_dir / "config_snapshot.yaml",
        "annotation_results": run_dir / "annotations.jsonl",
        "model_inputs": run_dir / "model_inputs.jsonl",
        "annotation_run_metadata": run_dir / "run_metadata.json",
        "prompt_snapshot": run_dir / "prompt_snapshot.txt",
        "schema_snapshot": run_dir / "schema_snapshot.json",
        "qa_summary": qa_dir / "qa_summary.json",
        "annotation_qa": qa_dir / "annotation_qa.csv",
        "grounded_phrases": qa_dir / "grounded_phrases.csv",
    }
    if annotation_config_path.is_file():
        source_files["annotation_config"] = annotation_config_path
    manifest = {
        "dataset_id": config.dataset_id,
        "dataset_version": config.dataset_version,
        "annotation_sample_id": config.annotation_sample_id,
        "annotation_run_id": config.annotation_run_id,
        "qa_version": config.qa_version,
        "config_hash": config.config_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(canonical_rows),
        "canonical_key": ["company_id", "scraped_page_id"],
        "row_order": ["split_order", "company_id", "scraped_page_id"],
        "artifacts": {
            key: {
                "path": value,
                "sha256": sha256_file(temporary / value),
                **({"schema": _table_schema(temporary / value)} if value.endswith(".parquet") else {}),
            }
            for key, value in artifact_names.items()
        },
        "sources": {
            key: {"path": str(path.relative_to(project_root)), "sha256": sha256_file(path)}
            for key, path in source_files.items()
        },
        "policies": {
            "scorer_v1_isolation": "scorer-v1 fields exist only in scorer_v1_features.parquet",
            "locked_test": "Do not inspect locked target outcomes for feature or model selection.",
            "cross_split_duplicates": "Exact cross-split content is retained canonically and excluded from modeling.",
            "text_vectorization": "Fit text transformations on development rows only.",
        },
    }
    (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.rename(output_directory)
    paths = {key: str(output_directory / value) for key, value in artifact_names.items()}
    paths["manifest"] = str(output_directory / "manifest.json")
    return summary, paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the scorer-v2 page training dataset without fitting models.")
    parser.add_argument("--config", type=Path, help="Dataset YAML; defaults to config/page_training_dataset.yaml")
    parser.add_argument("--output-directory", type=Path, help="Override the configured output directory")
    parser.add_argument("--force-refresh", action="store_true", help="Replace an existing dataset artifact directory")
    args = parser.parse_args()
    project_root = find_project_root()
    config_path = args.config or project_root / "config" / "page_training_dataset.yaml"
    config = load_dataset_config(config_path)
    output = args.output_directory or project_root / config.output_directory
    if not output.is_absolute():
        output = project_root / output
    project_config = load_config(project_root)
    database_path = resolve_database_path(project_root, project_config)
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        summary, paths = build_dataset(connection, project_root, config, config_path, output, args.force_refresh)
    finally:
        connection.close()
    print("Page training dataset built")
    print(f"  Dataset: {config.dataset_id} {config.dataset_version}")
    print(f"  Rows: {summary['annotation_rows']:,}")
    print(f"  Usable target rows: {summary['usable_target_rows']:,}")
    print(f"  Untrusted excluded: {summary['excluded_untrusted_rows']:,}")
    print(f"  Partially trusted: {summary['partially_trusted_rows']:,}")
    print(f"  Trusted grounded phrases: {summary['trusted_grounded_phrases']:,}")
    print(f"  Output: {paths['manifest']}")


if __name__ == "__main__":
    main()
