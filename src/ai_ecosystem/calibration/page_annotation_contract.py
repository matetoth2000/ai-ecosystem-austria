"""Frozen contract for scorer-v2 page annotations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


ATTRIBUTIONS = (
    "focal_company_direct",
    "focal_company_probable",
    "ambiguous",
    "third_party",
    "unrelated",
)
PAGE_CATEGORIES = (
    "company_profile",
    "product_or_service",
    "technology_capability",
    "innovation_or_research",
    "partnership_or_ecosystem",
    "customer_story",
    "news_press_or_blog",
    "careers",
    "legal_or_compliance",
    "contact_or_location",
    "navigation_or_low_content",
    "other",
)
FACETS = (
    "company_activity",
    "products_services",
    "technical_capability",
    "explicit_ai",
    "ai_adjacent",
    "innovation_research",
    "ecosystem_relationships",
)
NOISE_FLAGS = (
    "generic_marketing",
    "duplicated_or_template",
    "third_party_focus",
    "news_or_press",
    "legal_or_compliance",
    "careers",
    "navigation_or_low_content",
    "non_substantive_listing",
)
PRIORITY_TIERS = ("P1_CORE", "P2_SUPPORTING", "P3_CONTEXT", "P4_NOISE")
ALLOWED_MODEL_INPUT_KEYS = {
    "scraped_page_id",
    "company_id",
    "company_name",
    "normalized_company_domain",
    "page_url",
    "title",
    "headings",
    "extracted_markdown",
    "markdown_truncated",
}
FORBIDDEN_METADATA_KEYS = {
    "label",
    "split",
    "sampling_pool",
    "selection_reason",
    "scorer_v1_scores",
    "scorer_v1_diagnostic",
    "scorer_v1_independent",
    "previous_evidence_references",
    "calibration_result_id",
    "calibration_run_id",
}


@dataclass(frozen=True)
class PageAnnotationConfig:
    annotation_id: str
    annotation_version: str
    sample_id: str
    sample_directory: str
    output_root: str
    model: str
    reasoning_effort: str
    max_markdown_chars: int
    hidden_repeat_fraction: float
    hidden_repeat_seed: int
    completion_window: str
    retry_count: int
    request_timeout_seconds: int
    pricing_version: str
    pricing: dict[str, float]
    config_hash: str


@dataclass(frozen=True)
class AnnotationItem:
    annotation_item_id: str
    annotation_rank: int
    company_id: str
    scraped_page_id: int
    model_input: dict[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ValueError(f"page_annotation.{key} must be a non-empty string")
    return value


def load_page_annotation_config(
    project_root: Path | None = None,
    config_path: Path | None = None,
) -> PageAnnotationConfig:
    root = project_root or find_project_root()
    path = config_path or root / "config" / "page_annotation.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("page_annotation configuration must be a mapping")
    max_chars = int(raw.get("max_markdown_chars", 0))
    repeat_fraction = float(raw.get("hidden_repeat_fraction", -1))
    if max_chars <= 0:
        raise ValueError("max_markdown_chars must be positive")
    if not 0 <= repeat_fraction < 1:
        raise ValueError("hidden_repeat_fraction must be in [0, 1)")
    if raw.get("reasoning_effort") not in {"low", "medium", "high"}:
        raise ValueError("reasoning_effort must be low, medium, or high")
    if raw.get("completion_window") != "24h":
        raise ValueError("Batch completion_window must be 24h")
    pricing_raw = raw.get("pricing")
    if not isinstance(pricing_raw, dict):
        raise ValueError("pricing must be a mapping")
    pricing = {str(key): float(value) for key, value in pricing_raw.items()}
    for key in ("input_per_million", "cached_input_per_million", "output_per_million", "batch_discount"):
        if key not in pricing or pricing[key] < 0:
            raise ValueError(f"pricing.{key} must be non-negative")
    canonical = canonical_json(raw)
    return PageAnnotationConfig(
        annotation_id=_required_text(raw, "annotation_id"),
        annotation_version=_required_text(raw, "annotation_version"),
        sample_id=_required_text(raw, "sample_id"),
        sample_directory=_required_text(raw, "sample_directory"),
        output_root=_required_text(raw, "output_root"),
        model=_required_text(raw, "model"),
        reasoning_effort=_required_text(raw, "reasoning_effort"),
        max_markdown_chars=max_chars,
        hidden_repeat_fraction=repeat_fraction,
        hidden_repeat_seed=int(raw.get("hidden_repeat_seed")),
        completion_window=_required_text(raw, "completion_window"),
        retry_count=int(raw.get("retry_count", 0)),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 180)),
        pricing_version=_required_text(raw, "pricing_version"),
        pricing=pricing,
        config_hash=sha256_text(canonical),
    )


def load_prompt(project_root: Path | None = None) -> tuple[str, str, str, str]:
    root = project_root or find_project_root()
    path = root / "src" / "ai_ecosystem" / "calibration" / "prompts" / "page_annotation_v1.txt"
    source = path.read_text(encoding="utf-8")
    header, separator, body = source.partition("\n\n")
    if not separator:
        raise ValueError("Page annotation prompt must contain a metadata header")
    metadata = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    prompt_id = metadata.get("prompt_id", "").strip()
    prompt_version = metadata.get("prompt_version", "").strip()
    if not prompt_id or not prompt_version:
        raise ValueError("Page annotation prompt must declare prompt_id and prompt_version")
    return prompt_id, prompt_version, body.strip(), sha256_text(source)


def annotation_schema() -> dict[str, Any]:
    facet = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strength": {"type": "integer", "enum": [0, 1, 2, 3]},
            "supporting_phrases": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
            },
        },
        "required": ["strength", "supporting_phrases"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scraped_page_id": {"type": "integer"},
            "utility_score": {"type": "integer", "enum": [0, 1, 2, 3]},
            "attribution": {"type": "string", "enum": list(ATTRIBUTIONS)},
            "page_category": {"type": "string", "enum": list(PAGE_CATEGORIES)},
            "evidence_facets": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: facet for name in FACETS},
                "required": list(FACETS),
            },
            "noise_flags": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: {"type": "boolean"} for name in NOISE_FLAGS},
                "required": list(NOISE_FLAGS),
            },
            "page_priority_tier": {"type": "string", "enum": list(PRIORITY_TIERS)},
            "rationale": {"type": "string"},
        },
        "required": [
            "scraped_page_id",
            "utility_score",
            "attribution",
            "page_category",
            "evidence_facets",
            "noise_flags",
            "page_priority_tier",
            "rationale",
        ],
    }


def response_text_config() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "scorer_v2_page_annotation",
            "strict": True,
            "schema": annotation_schema(),
        }
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        records.append(value)
    return records


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _headings_by_extracted_page(project_root: Path, extracted_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    identifiers = sorted(set(extracted_ids))
    if not identifiers:
        return {}
    db_path = resolve_database_path(project_root, load_config(project_root))
    result: dict[int, list[dict[str, Any]]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for offset in range(0, len(identifiers), 500):
            chunk = identifiers[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT extracted_page_id, headings, heading_hierarchy FROM page_features WHERE extracted_page_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                parsed: Any = []
                for value in (row["heading_hierarchy"], row["headings"]):
                    if not value:
                        continue
                    try:
                        candidate = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, list):
                        parsed = candidate
                        break
                headings: list[dict[str, Any]] = []
                for item in parsed:
                    if isinstance(item, str) and item.strip():
                        headings.append({"level": None, "text": item.strip()})
                    elif isinstance(item, dict) and str(item.get("text", "")).strip():
                        level = item.get("level")
                        headings.append({"level": int(level) if isinstance(level, int) else None, "text": str(item["text"]).strip()})
                result[int(row["extracted_page_id"])] = headings
    return result


def validate_model_input(payload: dict[str, Any]) -> None:
    keys = set(payload)
    if keys != ALLOWED_MODEL_INPUT_KEYS:
        raise ValueError(
            f"Model input fields differ from frozen contract; missing={sorted(ALLOWED_MODEL_INPUT_KEYS - keys)}, "
            f"extra={sorted(keys - ALLOWED_MODEL_INPUT_KEYS)}"
        )
    leaked = keys & FORBIDDEN_METADATA_KEYS
    if leaked:
        raise ValueError(f"Model input leaks private annotation metadata: {sorted(leaked)}")
    if not isinstance(payload["scraped_page_id"], int):
        raise ValueError("scraped_page_id must be an integer")
    if not isinstance(payload["headings"], list):
        raise ValueError("headings must be a list")
    if not isinstance(payload["extracted_markdown"], str):
        raise ValueError("extracted_markdown must be a string")
    if not isinstance(payload["markdown_truncated"], bool):
        raise ValueError("markdown_truncated must be boolean")


def load_annotation_items(project_root: Path, config: PageAnnotationConfig) -> tuple[dict[str, Any], list[AnnotationItem]]:
    sample_dir = _resolve(project_root, config.sample_directory)
    manifest = json.loads((sample_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("sample_id") != config.sample_id:
        raise ValueError("Frozen sample ID does not match annotation configuration")
    companies = {record["company_id"]: record for record in _read_jsonl(sample_dir / manifest["files"]["companies"]["path"])}
    pages = _read_jsonl(sample_dir / manifest["files"]["pages"]["path"])
    headings = _headings_by_extracted_page(project_root, (int(page["extracted_page_id"]) for page in pages))
    items: list[AnnotationItem] = []
    seen_ids: set[str] = set()
    for page in pages:
        item_id = str(page["annotation_item_id"])
        if item_id in seen_ids:
            raise ValueError(f"Duplicate annotation_item_id: {item_id}")
        seen_ids.add(item_id)
        company = companies.get(page["company_id"])
        if company is None:
            raise ValueError(f"Missing company record for {page['company_id']}")
        markdown_path = _resolve(project_root, str(page["markdown_path"]))
        markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        truncated = len(markdown) > config.max_markdown_chars
        payload = {
            "scraped_page_id": int(page["scraped_page_id"]),
            "company_id": str(page["company_id"]),
            "company_name": company.get("company_name"),
            "normalized_company_domain": company.get("domain"),
            "page_url": page.get("normalized_url") or page.get("url"),
            "title": page.get("title"),
            "headings": headings.get(int(page["extracted_page_id"]), []),
            "extracted_markdown": markdown[: config.max_markdown_chars],
            "markdown_truncated": truncated,
        }
        validate_model_input(payload)
        items.append(
            AnnotationItem(
                annotation_item_id=item_id,
                annotation_rank=int(page["annotation_rank"]),
                company_id=str(page["company_id"]),
                scraped_page_id=int(page["scraped_page_id"]),
                model_input=payload,
            )
        )
    items.sort(key=lambda item: (item.annotation_rank, item.annotation_item_id))
    if len(items) != int(manifest["page_annotation_item_count"]):
        raise ValueError("Frozen manifest item count does not match pages.jsonl")
    return manifest, items


def stable_request_id(annotation_item_id: str, repeat_index: int = 0) -> str:
    digest = sha256_text(annotation_item_id)[:24]
    suffix = "primary" if repeat_index == 0 else f"repeat{repeat_index}"
    return f"pa_{digest}_{suffix}"


def hidden_repeat_item_ids(items: Iterable[AnnotationItem], config: PageAnnotationConfig) -> set[str]:
    ordered = list(items)
    count = round(len(ordered) * config.hidden_repeat_fraction)
    ranked = sorted(
        ordered,
        key=lambda item: (sha256_text(f"{config.hidden_repeat_seed}:{item.annotation_item_id}"), item.annotation_item_id),
    )
    return {item.annotation_item_id for item in ranked[:count]}


def request_body(item: AnnotationItem, prompt: str, config: PageAnnotationConfig) -> dict[str, Any]:
    validate_model_input(item.model_input)
    return {
        "model": config.model,
        "reasoning": {"effort": config.reasoning_effort},
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": canonical_json(item.model_input)},
        ],
        "text": response_text_config(),
        "store": False,
    }


def build_request_records(
    items: Iterable[AnnotationItem],
    prompt: str,
    config: PageAnnotationConfig,
    *,
    include_hidden_repeats: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = list(items)
    repeats = hidden_repeat_item_ids(ordered, config) if include_hidden_repeats else set()
    batch_lines: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    for item in ordered:
        copies = (0, 1) if item.annotation_item_id in repeats else (0,)
        for repeat_index in copies:
            custom_id = stable_request_id(item.annotation_item_id, repeat_index)
            body = request_body(item, prompt, config)
            batch_lines.append({"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body})
            index.append(
                {
                    "custom_id": custom_id,
                    "annotation_item_id": item.annotation_item_id,
                    "annotation_rank": item.annotation_rank,
                    "company_id": item.company_id,
                    "scraped_page_id": item.scraped_page_id,
                    "is_hidden_repeat": repeat_index > 0,
                    "repeat_of_custom_id": stable_request_id(item.annotation_item_id) if repeat_index else None,
                    "model_input_sha256": sha256_text(canonical_json(item.model_input)),
                }
            )
    custom_ids = [line["custom_id"] for line in batch_lines]
    if len(custom_ids) != len(set(custom_ids)):
        raise ValueError("Batch request custom IDs are not unique")
    return batch_lines, index


def validate_batch_lines(lines: Iterable[dict[str, Any]], index: Iterable[dict[str, Any]]) -> None:
    materialized = list(lines)
    index_rows = list(index)
    if len(materialized) != len(index_rows):
        raise ValueError("Batch input and request index counts differ")
    expected = {row["custom_id"] for row in index_rows}
    actual: set[str] = set()
    for line in materialized:
        if set(line) != {"custom_id", "method", "url", "body"}:
            raise ValueError("Batch request line has unexpected fields")
        if line["method"] != "POST" or line["url"] != "/v1/responses":
            raise ValueError("Batch request must target POST /v1/responses")
        custom_id = str(line["custom_id"])
        if custom_id in actual:
            raise ValueError(f"Duplicate custom_id: {custom_id}")
        actual.add(custom_id)
        body = line["body"]
        if body.get("store") is not False:
            raise ValueError("Page annotation responses must use store=false")
        user_payload = json.loads(body["input"][1]["content"])
        validate_model_input(user_payload)
    if actual != expected:
        raise ValueError("Batch custom IDs do not match request index")


def validate_annotation(value: dict[str, Any], expected_scraped_page_id: int) -> dict[str, Any]:
    schema = annotation_schema()
    if set(value) != set(schema["required"]):
        raise ValueError("Annotation fields do not match the frozen schema")
    if value.get("scraped_page_id") != expected_scraped_page_id:
        raise ValueError(
            f"Model returned scraped_page_id={value.get('scraped_page_id')}, expected {expected_scraped_page_id}"
        )
    if value.get("utility_score") not in {0, 1, 2, 3}:
        raise ValueError("Invalid utility_score")
    if value.get("attribution") not in ATTRIBUTIONS or value.get("page_category") not in PAGE_CATEGORIES:
        raise ValueError("Invalid attribution or page category")
    if value.get("page_priority_tier") not in PRIORITY_TIERS:
        raise ValueError("Invalid page priority tier")
    facets = value.get("evidence_facets")
    if not isinstance(facets, dict) or set(facets) != set(FACETS):
        raise ValueError("Evidence facets do not match the frozen contract")
    for name, facet in facets.items():
        if not isinstance(facet, dict) or set(facet) != {"strength", "supporting_phrases"}:
            raise ValueError(f"Invalid facet structure: {name}")
        strength = facet["strength"]
        phrases = facet["supporting_phrases"]
        if strength not in {0, 1, 2, 3} or not isinstance(phrases, list) or len(phrases) > 3:
            raise ValueError(f"Invalid facet value: {name}")
        if strength == 0 and phrases:
            raise ValueError(f"Zero-strength facet has supporting phrases: {name}")
        if strength > 0 and not phrases:
            raise ValueError(f"Positive facet lacks a supporting phrase: {name}")
        if any(not isinstance(phrase, str) or not phrase.strip() for phrase in phrases):
            raise ValueError(f"Invalid supporting phrase: {name}")
    noise = value.get("noise_flags")
    if not isinstance(noise, dict) or set(noise) != set(NOISE_FLAGS) or any(not isinstance(flag, bool) for flag in noise.values()):
        raise ValueError("Noise flags do not match the frozen contract")
    if not isinstance(value.get("rationale"), str):
        raise ValueError("Rationale must be a string")
    return value


def parse_annotation(raw_text: str, expected_scraped_page_id: int) -> dict[str, Any]:
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Model output is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Model output must be an object")
    return validate_annotation(value, expected_scraped_page_id)


def usage_from_response(value: Any) -> dict[str, int]:
    usage = getattr(value, "usage", None) or (value.get("usage") if isinstance(value, dict) else None) or {}
    def read(name: str) -> int:
        raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        return int(raw or 0)
    details = usage.get("input_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "input_tokens_details", {})
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else getattr(details, "cached_tokens", 0)
    return {
        "input_tokens": read("input_tokens"),
        "cached_input_tokens": int(cached or 0),
        "output_tokens": read("output_tokens"),
        "total_tokens": read("total_tokens"),
    }


def estimate_cost(config: PageAnnotationConfig, usage: dict[str, int], *, batch: bool) -> float:
    uncached = max(0, usage["input_tokens"] - usage["cached_input_tokens"])
    cost = (
        uncached * config.pricing["input_per_million"]
        + usage["cached_input_tokens"] * config.pricing["cached_input_per_million"]
        + usage["output_tokens"] * config.pricing["output_per_million"]
    ) / 1_000_000
    if batch:
        cost *= config.pricing["batch_discount"]
    return round(cost, 8)
