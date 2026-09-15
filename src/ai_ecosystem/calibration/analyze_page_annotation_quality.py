"""Read-only automated QA for scorer-v2 page annotations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from ai_ecosystem.calibration.grounding import normalize_grounding_text
from ai_ecosystem.calibration.page_annotation_contract import (
    FACETS,
    NOISE_FLAGS,
    canonical_json,
    load_page_annotation_config,
    sha256_text,
    validate_annotation,
)
from ai_ecosystem.db.init_db import find_project_root


QA_VERSION = "v2"
QUALIFYING_RUN_STATUSES = {"ingested"}
GATES = {
    "identifier_validity": 1.0,
    "schema_valid_rate": 0.99,
    "maximum_terminal_failure_rate": 0.01,
    "maximum_logical_inconsistency_rate": 0.01,
    "normalized_phrase_grounding_rate": 0.95,
    "hidden_repeat_utility_weighted_kappa": 0.70,
    "hidden_repeat_attribution_agreement": 0.85,
    "hidden_repeat_facet_agreement": 0.80,
    "trusted_annotation_rate": 0.90,
    "key_subgroup_trusted_rate": 0.80,
}
MIN_KEY_SUBGROUP_SIZE = 30


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        records.append(value)
    return records


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _duplicates(values: Iterable[Any]) -> list[str]:
    counts = Counter(str(value) for value in values)
    return sorted(value for value, count in counts.items() if count > 1)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / denominator, 6) if denominator else None


def _distribution(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _normalize_context_match(
    phrase: str,
    markdown: str,
    normalized_markdown: str | None = None,
) -> tuple[bool, str]:
    normalized_phrase = normalize_grounding_text(phrase)
    if normalized_markdown is None:
        normalized_markdown = normalize_grounding_text(markdown)
    if not normalized_phrase or normalized_phrase not in normalized_markdown:
        return False, ""
    literal_index = markdown.find(phrase)
    if literal_index >= 0:
        start, end = literal_index, literal_index + len(phrase)
    else:
        tokens = normalized_phrase.split()
        separator = r"[\W_]+"
        pattern = r"(?<!\w)" + separator.join(re.escape(token) for token in tokens) + r"(?!\w)"
        match = re.search(pattern, markdown, flags=re.IGNORECASE | re.UNICODE)
        if not match:
            return True, ""
        start, end = match.span()
    left = max(0, start - 120)
    right = min(len(markdown), end + 120)
    return True, " ".join(markdown[left:right].split())


def ground_annotation_phrases(
    annotation: dict[str, Any],
    model_input: dict[str, Any],
    custom_id: str,
    annotation_item_id: str,
) -> list[dict[str, Any]]:
    markdown = str(model_input.get("extracted_markdown") or "")
    markdown_casefold = markdown.casefold()
    normalized_markdown = normalize_grounding_text(markdown)
    rows: list[dict[str, Any]] = []
    for facet_name in FACETS:
        facet = annotation.get("evidence_facets", {}).get(facet_name, {})
        strength = facet.get("strength")
        for phrase_index, phrase_value in enumerate(facet.get("supporting_phrases", []), 1):
            phrase = str(phrase_value)
            exact = bool(phrase and phrase in markdown)
            casefold = bool(phrase and phrase.casefold() in markdown_casefold)
            normalized, context = _normalize_context_match(phrase, markdown, normalized_markdown)
            rows.append(
                {
                    "custom_id": custom_id,
                    "annotation_item_id": annotation_item_id,
                    "company_id": model_input.get("company_id"),
                    "scraped_page_id": model_input.get("scraped_page_id"),
                    "facet": facet_name,
                    "facet_strength": strength,
                    "phrase_index": phrase_index,
                    "phrase": phrase,
                    "exact_grounded": exact,
                    "casefold_grounded": casefold,
                    "normalized_grounded": normalized,
                    "lexical_supervision_trusted": normalized,
                    "reconstructed_context": context,
                }
            )
    return rows


def logical_consistency(annotation: dict[str, Any]) -> tuple[list[str], list[str]]:
    utility = int(annotation["utility_score"])
    priority = annotation["page_priority_tier"]
    attribution = annotation["attribution"]
    category = annotation["page_category"]
    facets = annotation["evidence_facets"]
    noise = annotation["noise_flags"]
    strengths = [int(facets[name]["strength"]) for name in FACETS]
    hard: list[str] = []
    soft: list[str] = []
    if utility == 0 and priority in {"P1_CORE", "P2_SUPPORTING"}:
        hard.append("zero_utility_with_core_priority")
    if utility == 0 and max(strengths) >= 2 and attribution in {
        "focal_company_direct",
        "focal_company_probable",
    }:
        hard.append("zero_utility_with_strong_evidence")
    if utility >= 2 and priority == "P4_NOISE":
        hard.append("high_utility_with_noise_priority")
    if priority == "P1_CORE" and utility < 2:
        hard.append("core_priority_without_useful_utility")
    if utility == 3 and max(strengths) == 0:
        hard.append("high_utility_without_positive_facets")
    if attribution == "unrelated" and (utility == 3 or priority == "P1_CORE"):
        hard.append("unrelated_high_priority")
    if category == "navigation_or_low_content" and utility == 3:
        hard.append("low_content_high_utility")
    expected_noise = {
        "news_press_or_blog": "news_or_press",
        "careers": "careers",
        "legal_or_compliance": "legal_or_compliance",
        "navigation_or_low_content": "navigation_or_low_content",
    }
    if category in expected_noise and not noise[expected_noise[category]]:
        soft.append(f"category_without_matching_noise_flag:{category}")
    if attribution == "third_party" and not noise["third_party_focus"]:
        soft.append("third_party_attribution_without_noise_flag")
    return sorted(set(hard)), sorted(set(soft))


def quadratic_weighted_kappa(pairs: Iterable[tuple[int, int]], categories: int = 4) -> float | None:
    values = list(pairs)
    if not values:
        return None
    left = Counter(first for first, _ in values)
    right = Counter(second for _, second in values)
    observed = sum(((first - second) ** 2) / ((categories - 1) ** 2) for first, second in values) / len(values)
    expected = 0.0
    for first in range(categories):
        for second in range(categories):
            expected += (
                left[first]
                / len(values)
                * right[second]
                / len(values)
                * ((first - second) ** 2)
                / ((categories - 1) ** 2)
            )
    if math.isclose(expected, 0.0):
        return 1.0 if math.isclose(observed, 0.0) else None
    return round(1.0 - observed / expected, 6)


def hidden_repeat_metrics(
    annotation_by_custom_id: dict[str, dict[str, Any]],
    request_index: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparisons: list[dict[str, Any]] = []
    utility_pairs: list[tuple[int, int]] = []
    attribution_matches = 0
    facet_matches = 0
    facet_total = 0
    for index_row in request_index:
        if not index_row.get("is_hidden_repeat"):
            continue
        repeat = annotation_by_custom_id.get(str(index_row["custom_id"]))
        primary = annotation_by_custom_id.get(str(index_row["repeat_of_custom_id"]))
        row: dict[str, Any] = {
            "repeat_custom_id": index_row["custom_id"],
            "primary_custom_id": index_row["repeat_of_custom_id"],
            "annotation_item_id": index_row["annotation_item_id"],
            "scraped_page_id": index_row["scraped_page_id"],
            "pair_available": bool(primary and repeat),
        }
        if (
            not primary
            or not repeat
            or primary.get("status") != "success"
            or repeat.get("status") != "success"
            or not isinstance(primary.get("annotation"), dict)
            or not isinstance(repeat.get("annotation"), dict)
        ):
            comparisons.append(row)
            continue
        first, second = primary["annotation"], repeat["annotation"]
        pair = (int(first["utility_score"]), int(second["utility_score"]))
        utility_pairs.append(pair)
        attribution_match = first["attribution"] == second["attribution"]
        attribution_matches += attribution_match
        per_facet = {
            facet: first["evidence_facets"][facet]["strength"] == second["evidence_facets"][facet]["strength"]
            for facet in FACETS
        }
        facet_matches += sum(per_facet.values())
        facet_total += len(FACETS)
        row.update(
            {
                "primary_utility": pair[0],
                "repeat_utility": pair[1],
                "utility_absolute_difference": abs(pair[0] - pair[1]),
                "attribution_match": attribution_match,
                "primary_attribution": first["attribution"],
                "repeat_attribution": second["attribution"],
                "facet_strength_matches": sum(per_facet.values()),
                "facet_strength_total": len(FACETS),
                "facet_agreement": _rate(sum(per_facet.values()), len(FACETS)),
                "facet_match_json": canonical_json(per_facet),
                "category_match": first["page_category"] == second["page_category"],
                "priority_match": first["page_priority_tier"] == second["page_priority_tier"],
            }
        )
        comparisons.append(row)
    available = len(utility_pairs)
    metrics = {
        "expected_repeat_pairs": sum(bool(row.get("is_hidden_repeat")) for row in request_index),
        "available_repeat_pairs": available,
        "utility_weighted_kappa": quadratic_weighted_kappa(utility_pairs),
        "utility_exact_agreement": _rate(sum(a == b for a, b in utility_pairs), available),
        "utility_mean_absolute_difference": round(mean(abs(a - b) for a, b in utility_pairs), 6) if available else None,
        "attribution_agreement": _rate(attribution_matches, available),
        "facet_strength_agreement": _rate(facet_matches, facet_total),
        "category_agreement": _rate(sum(bool(row.get("category_match")) for row in comparisons), available),
        "priority_agreement": _rate(sum(bool(row.get("priority_match")) for row in comparisons), available),
    }
    return metrics, comparisons


def _subgroup_row(dimension: str, value: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    trusted = sum(record["trust_flag"] == "trusted" for record in records)
    phrases = [phrase for record in records for phrase in record["phrase_rows"]]
    row: dict[str, Any] = {
        "dimension": dimension,
        "value": value,
        "count": count,
        "trusted_count": trusted,
        "trusted_rate": _rate(trusted, count),
        "partially_trusted_count": sum(record["trust_flag"] == "partially_trusted" for record in records),
        "untrusted_count": sum(record["trust_flag"] == "untrusted" for record in records),
        "mean_utility": round(mean(record["annotation"]["utility_score"] for record in records), 6),
        "logical_inconsistency_rate": _rate(sum(bool(record["hard_contradictions"]) for record in records), count),
        "positive_phrase_count": len(phrases),
        "normalized_phrase_grounding_rate": _rate(sum(bool(phrase["normalized_grounded"]) for phrase in phrases), len(phrases)),
        "utility_distribution_json": canonical_json(_distribution(record["annotation"]["utility_score"] for record in records)),
        "priority_distribution_json": canonical_json(_distribution(record["annotation"]["page_priority_tier"] for record in records)),
        "attribution_distribution_json": canonical_json(_distribution(record["annotation"]["attribution"] for record in records)),
        "category_distribution_json": canonical_json(_distribution(record["annotation"]["page_category"] for record in records)),
    }
    for facet in FACETS:
        row[f"mean_{facet}_strength"] = round(mean(record["annotation"]["evidence_facets"][facet]["strength"] for record in records), 6)
    return row


def build_subgroups(records: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    dimensions = (
        "company_label",
        "sampling_pool",
        "language",
        "website_size",
        "shared_domain",
        "calibration_source",
        "attribution",
        "page_category",
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    combined: list[dict[str, Any]] = []
    for dimension in dimensions:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            groups[str(record[dimension])].append(record)
        rows = [_subgroup_row(dimension, value, groups[value]) for value in sorted(groups)]
        tables[dimension] = rows
        combined.extend(rows)
    return tables, combined


def _collapsed_outputs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: dict[str, list[Any]] = {
        "utility_score": [record["annotation"]["utility_score"] for record in records],
        "attribution": [record["annotation"]["attribution"] for record in records],
        "page_category": [record["annotation"]["page_category"] for record in records],
        "page_priority_tier": [record["annotation"]["page_priority_tier"] for record in records],
    }
    for facet in FACETS:
        checks[f"facet:{facet}"] = [record["annotation"]["evidence_facets"][facet]["strength"] for record in records]
    findings = []
    for field, values in checks.items():
        counts = Counter(str(value) for value in values)
        modal, modal_count = counts.most_common(1)[0]
        share = modal_count / len(values)
        threshold = 0.98 if field.startswith("facet:") else 0.90
        if share >= threshold:
            findings.append({"field": field, "modal_value": modal, "modal_share": round(share, 6), "distribution": dict(sorted(counts.items()))})
    return findings


def _subgroup_divergences(rows: list[dict[str, Any]], overall_utility: float, overall_trust: float) -> list[dict[str, Any]]:
    findings = []
    for row in rows:
        if row["count"] < MIN_KEY_SUBGROUP_SIZE:
            continue
        utility_delta = float(row["mean_utility"]) - overall_utility
        trust_delta = float(row["trusted_rate"] or 0) - overall_trust
        if abs(utility_delta) >= 1.0 or abs(trust_delta) >= 0.20:
            findings.append({**row, "utility_delta": round(utility_delta, 6), "trusted_rate_delta": round(trust_delta, 6)})
    return findings


def analyze_annotation_quality(
    *,
    project_root: Path,
    run_id: str,
    qa_version: str = QA_VERSION,
    output_directory: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    config = load_page_annotation_config(project_root, config_path)
    run_root = Path(config.output_root)
    if not run_root.is_absolute():
        run_root = project_root / run_root
    run_dir = run_root / run_id
    metadata = _read_json(run_dir / "run_metadata.json")
    if metadata.get("status") not in QUALIFYING_RUN_STATUSES:
        raise ValueError(f"Annotation run is not ingested: {metadata.get('status')}")
    output_dir = output_directory or run_dir / f"qa_{qa_version}"
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    if output_dir.exists():
        raise FileExistsError(f"QA output already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    request_index = _read_jsonl(run_dir / "request_index.jsonl")
    model_inputs = _read_jsonl(run_dir / "model_inputs.jsonl")
    annotations = _read_jsonl(run_dir / "annotations.jsonl")
    batch_lines = _read_jsonl(run_dir / "batch_input.jsonl")
    sample_dir = Path(config.sample_directory)
    if not sample_dir.is_absolute():
        sample_dir = project_root / sample_dir
    sample_manifest = _read_json(sample_dir / "manifest.json")
    sample_companies = {row["company_id"]: row for row in _read_jsonl(sample_dir / sample_manifest["files"]["companies"]["path"])}
    sample_pages = {row["annotation_item_id"]: row for row in _read_jsonl(sample_dir / sample_manifest["files"]["pages"]["path"])}

    index_by_custom = {row["custom_id"]: row for row in request_index}
    input_by_custom = {row["custom_id"]: {key: value for key, value in row.items() if key != "custom_id"} for row in model_inputs}
    annotation_by_custom = {row["custom_id"]: row for row in annotations}
    expected_ids, input_ids, annotation_ids = set(index_by_custom), set(input_by_custom), set(annotation_by_custom)
    prompt_snapshot = (run_dir / "prompt_snapshot.txt").read_text(encoding="utf-8").strip()
    schema_snapshot = _read_json(run_dir / "schema_snapshot.json")
    batch_prompt_hashes = {
        sha256_text(str(line.get("body", {}).get("input", [{}, {}])[0].get("content", "")).strip())
        for line in batch_lines
    }
    batch_schema_hashes = {
        sha256_text(canonical_json(line.get("body", {}).get("text", {}).get("format", {}).get("schema", {})))
        for line in batch_lines
    }
    integrity = {
        "request_index_count": len(request_index),
        "model_input_count": len(model_inputs),
        "annotation_record_count": len(annotations),
        "duplicate_request_ids": _duplicates(row.get("custom_id") for row in request_index),
        "duplicate_model_input_ids": _duplicates(row.get("custom_id") for row in model_inputs),
        "duplicate_annotation_ids": _duplicates(row.get("custom_id") for row in annotations),
        "missing_model_inputs": sorted(expected_ids - input_ids),
        "missing_annotations": sorted(expected_ids - annotation_ids),
        "unexpected_annotations": sorted(annotation_ids - expected_ids),
        "batch_input_sha256_valid": _file_sha256(run_dir / "batch_input.jsonl") == metadata.get("batch_input_sha256"),
        "sample_manifest_sha256_valid": sha256_text(canonical_json(sample_manifest)) == metadata.get("sample_manifest_sha256"),
        "schema_snapshot_valid": sha256_text(canonical_json(schema_snapshot)) == metadata.get("schema_sha256"),
        "schema_snapshot_matches_batch": batch_schema_hashes == {sha256_text(canonical_json(schema_snapshot))},
        "prompt_snapshot_matches_batch": batch_prompt_hashes == {sha256_text(prompt_snapshot)},
        "current_config_hash_matches_run": config.config_hash == metadata.get("config_sha256"),
        "config_snapshot_present": (run_dir / "config_snapshot.yaml").is_file(),
        "model_input_hash_mismatches": sorted(
            custom_id
            for custom_id in expected_ids & input_ids
            if sha256_text(canonical_json(input_by_custom[custom_id])) != index_by_custom[custom_id]["model_input_sha256"]
        ),
    }

    all_qa_rows: list[dict[str, Any]] = []
    primary_records: list[dict[str, Any]] = []
    all_phrase_rows: list[dict[str, Any]] = []
    successful = schema_valid = identifier_valid = terminal_failures = 0
    for index_row in request_index:
        custom_id = str(index_row["custom_id"])
        result = annotation_by_custom.get(custom_id)
        model_input = input_by_custom.get(custom_id)
        status = str(result.get("status")) if result else "missing"
        schema_error: str | None = None
        identifier_ok = False
        hard: list[str] = []
        soft: list[str] = []
        phrase_rows: list[dict[str, Any]] = []
        parsed: dict[str, Any] | None = None
        if status == "success" and result and model_input:
            successful += 1
            try:
                parsed = validate_annotation(dict(result["annotation"]), int(index_row["scraped_page_id"]))
                schema_valid += 1
                identifier_ok = parsed["scraped_page_id"] == index_row["scraped_page_id"] == model_input["scraped_page_id"]
                identifier_valid += identifier_ok
                hard, soft = logical_consistency(parsed)
                phrase_rows = ground_annotation_phrases(parsed, model_input, custom_id, str(index_row["annotation_item_id"]))
                all_phrase_rows.extend(phrase_rows)
            except Exception as error:
                schema_error = str(error)
        else:
            terminal_failures += 1
        reason_codes: list[str] = []
        if status != "success":
            reason_codes.append("terminal_failure")
        if schema_error:
            reason_codes.append("invalid_schema")
        if status == "success" and not identifier_ok:
            reason_codes.append("identifier_mismatch")
        reason_codes.extend(f"logical_contradiction:{code}" for code in hard)
        if soft:
            reason_codes.append("soft_logical_inconsistency")
        ungrounded = sum(not row["normalized_grounded"] for row in phrase_rows)
        lexical_restrictions = ["normalized_phrase_ungrounded"] if ungrounded else []
        ungrounded_positive_facets = []
        if parsed:
            for facet_name in FACETS:
                if parsed["evidence_facets"][facet_name]["strength"] <= 0:
                    continue
                facet_phrases = [row for row in phrase_rows if row["facet"] == facet_name]
                if not any(row["normalized_grounded"] for row in facet_phrases):
                    ungrounded_positive_facets.append(facet_name)
        if ungrounded_positive_facets:
            reason_codes.append("positive_facet_without_grounded_phrase")
        if any(code in {"terminal_failure", "invalid_schema", "identifier_mismatch"} or code.startswith("logical_contradiction:") for code in reason_codes):
            trust = "untrusted"
        elif reason_codes:
            trust = "partially_trusted"
        else:
            trust = "trusted"
        page = sample_pages.get(str(index_row["annotation_item_id"]), {})
        company = sample_companies.get(str(index_row["company_id"]), {})
        qa_row = {
            "custom_id": custom_id,
            "annotation_item_id": index_row["annotation_item_id"],
            "annotation_rank": index_row["annotation_rank"],
            "company_id": index_row["company_id"],
            "scraped_page_id": index_row["scraped_page_id"],
            "is_hidden_repeat": bool(index_row["is_hidden_repeat"]),
            "repeat_of_custom_id": index_row.get("repeat_of_custom_id"),
            "status": status,
            "schema_valid": parsed is not None,
            "identifier_valid": identifier_ok,
            "hard_logical_contradiction": bool(hard),
            "hard_contradictions": hard,
            "soft_inconsistencies": soft,
            "positive_phrase_count": len(phrase_rows),
            "normalized_grounded_phrase_count": sum(bool(row["normalized_grounded"]) for row in phrase_rows),
            "trust_flag": trust,
            "trust_reason_codes": reason_codes,
            "lexical_restriction_reason_codes": lexical_restrictions,
            "ungrounded_positive_facets": ungrounded_positive_facets,
            "schema_error": schema_error,
            "annotation": parsed,
            "phrase_rows": phrase_rows,
            "company_label": company.get("label", "unknown"),
            "sampling_pool": page.get("sampling_pool", "unknown"),
            "language": page.get("language_group") or page.get("language") or "unknown",
            "website_size": company.get("site_size_band", "unknown"),
            "shared_domain": str(bool(company.get("shared_domain"))).lower(),
            "calibration_source": company.get("calibration_sample_id", "unknown"),
            "attribution": parsed.get("attribution") if parsed else "invalid",
            "page_category": parsed.get("page_category") if parsed else "invalid",
        }
        all_qa_rows.append(qa_row)
        if not index_row["is_hidden_repeat"]:
            primary_records.append(qa_row)

    repeat_metrics, repeat_rows = hidden_repeat_metrics(annotation_by_custom, request_index)
    primary_phrases = [phrase for record in primary_records for phrase in record["phrase_rows"]]
    all_positive_phrases = all_phrase_rows
    hard_inconsistent = sum(bool(record["hard_contradictions"]) for record in primary_records)
    trusted = sum(record["trust_flag"] == "trusted" for record in primary_records)
    subgroup_tables, subgroup_rows = build_subgroups([record for record in primary_records if record["annotation"]])
    populated_subgroups = [row for row in subgroup_rows if row["count"] >= MIN_KEY_SUBGROUP_SIZE]
    min_subgroup_trust = min((float(row["trusted_rate"] or 0) for row in populated_subgroups), default=1.0)
    schema_rate = _rate(schema_valid, len(request_index)) or 0.0
    identifier_rate = _rate(identifier_valid, successful) or 0.0
    terminal_rate = _rate(terminal_failures, len(request_index)) or 0.0
    logical_rate = _rate(hard_inconsistent, len(primary_records)) or 0.0
    grounding_rate = _rate(sum(bool(row["normalized_grounded"]) for row in all_positive_phrases), len(all_positive_phrases)) or 0.0
    trusted_rate = _rate(trusted, len(primary_records)) or 0.0
    integrity_passed = not any(
        integrity[key]
        for key in (
            "duplicate_request_ids",
            "duplicate_model_input_ids",
            "duplicate_annotation_ids",
            "missing_model_inputs",
            "missing_annotations",
            "unexpected_annotations",
            "model_input_hash_mismatches",
        )
    ) and all(
        integrity[key]
        for key in (
            "batch_input_sha256_valid",
            "sample_manifest_sha256_valid",
            "schema_snapshot_valid",
            "schema_snapshot_matches_batch",
            "prompt_snapshot_matches_batch",
            "current_config_hash_matches_run",
        )
    )
    gate_results = {
        "artifact_integrity": {"value": integrity_passed, "threshold": True, "passed": integrity_passed},
        "identifier_validity": {"value": identifier_rate, "threshold": GATES["identifier_validity"], "passed": identifier_rate == 1.0},
        "schema_valid_rate": {"value": schema_rate, "threshold": GATES["schema_valid_rate"], "passed": schema_rate >= GATES["schema_valid_rate"]},
        "terminal_failure_rate": {"value": terminal_rate, "threshold": GATES["maximum_terminal_failure_rate"], "passed": terminal_rate <= GATES["maximum_terminal_failure_rate"]},
        "logical_inconsistency_rate": {"value": logical_rate, "threshold": GATES["maximum_logical_inconsistency_rate"], "passed": logical_rate <= GATES["maximum_logical_inconsistency_rate"]},
        "normalized_phrase_grounding_rate": {"value": grounding_rate, "threshold": GATES["normalized_phrase_grounding_rate"], "passed": grounding_rate >= GATES["normalized_phrase_grounding_rate"]},
        "hidden_repeat_utility_weighted_kappa": {"value": repeat_metrics["utility_weighted_kappa"], "threshold": GATES["hidden_repeat_utility_weighted_kappa"], "passed": repeat_metrics["utility_weighted_kappa"] is not None and repeat_metrics["utility_weighted_kappa"] >= GATES["hidden_repeat_utility_weighted_kappa"]},
        "hidden_repeat_attribution_agreement": {"value": repeat_metrics["attribution_agreement"], "threshold": GATES["hidden_repeat_attribution_agreement"], "passed": repeat_metrics["attribution_agreement"] is not None and repeat_metrics["attribution_agreement"] >= GATES["hidden_repeat_attribution_agreement"]},
        "hidden_repeat_facet_agreement": {"value": repeat_metrics["facet_strength_agreement"], "threshold": GATES["hidden_repeat_facet_agreement"], "passed": repeat_metrics["facet_strength_agreement"] is not None and repeat_metrics["facet_strength_agreement"] >= GATES["hidden_repeat_facet_agreement"]},
        "trusted_annotation_rate": {"value": trusted_rate, "threshold": GATES["trusted_annotation_rate"], "passed": trusted_rate >= GATES["trusted_annotation_rate"]},
        "key_subgroup_trusted_rate": {"value": round(min_subgroup_trust, 6), "threshold": GATES["key_subgroup_trusted_rate"], "passed": min_subgroup_trust >= GATES["key_subgroup_trusted_rate"], "minimum_subgroup_size": MIN_KEY_SUBGROUP_SIZE},
    }
    failed_gates = [name for name, result in gate_results.items() if not result["passed"]]
    overall_utility = mean(record["annotation"]["utility_score"] for record in primary_records if record["annotation"])
    diagnostic = next((row for row in subgroup_tables["sampling_pool"] if row["value"] == "scorer_v1_diagnostic"), None)
    diagnostic_dominance = {
        "diagnostic_page_share": _rate(sum(record["sampling_pool"] == "scorer_v1_diagnostic" for record in primary_records), len(primary_records)),
        "diagnostic_mean_utility": diagnostic["mean_utility"] if diagnostic else None,
        "overall_mean_utility": round(overall_utility, 6),
    }
    diagnostic_dominance["dominance_detected"] = bool(
        diagnostic_dominance["diagnostic_page_share"] is not None
        and diagnostic_dominance["diagnostic_page_share"] > 0.15
    )
    collapsed = _collapsed_outputs([record for record in primary_records if record["annotation"]])
    divergences = _subgroup_divergences(subgroup_rows, overall_utility, trusted_rate)

    if not failed_gates:
        recommendation = "PROCEED_TO_5_8E"
        recommendation_text = "All agreed QA gates passed. Use trusted annotations for all targets and grounded phrases for lexical supervision."
    elif set(failed_gates) <= {"normalized_phrase_grounding_rate", "trusted_annotation_rate", "key_subgroup_trusted_rate"}:
        recommendation = "PROCEED_TO_5_8E_WITH_RESTRICTED_LEXICAL_FIELDS"
        recommendation_text = "Core labels are usable, but lexical supervision must be restricted to normalized-grounded phrases and trust flags must be retained."
    else:
        recommendation = "DO_NOT_PROCEED_TO_5_8E"
        recommendation_text = "One or more core annotation-quality or repeat-stability gates failed; diagnose the listed failures before building the training dataset."

    anomaly_examples = []
    for record in all_qa_rows:
        if record["trust_flag"] == "trusted":
            continue
        ungrounded_examples = [
            {"facet": phrase["facet"], "phrase": phrase["phrase"]}
            for phrase in record["phrase_rows"]
            if not phrase["normalized_grounded"]
        ][:5]
        anomaly_examples.append(
            {
                "custom_id": record["custom_id"],
                "annotation_item_id": record["annotation_item_id"],
                "company_id": record["company_id"],
                "scraped_page_id": record["scraped_page_id"],
                "is_hidden_repeat": record["is_hidden_repeat"],
                "trust_flag": record["trust_flag"],
                "reason_codes": record["trust_reason_codes"],
                "hard_contradictions": record["hard_contradictions"],
                "soft_inconsistencies": record["soft_inconsistencies"],
                "ungrounded_phrase_examples": ungrounded_examples,
            }
        )

    qa_csv_rows = []
    for record in all_qa_rows:
        annotation = record["annotation"] or {}
        qa_csv_rows.append(
            {
                "custom_id": record["custom_id"],
                "annotation_item_id": record["annotation_item_id"],
                "annotation_rank": record["annotation_rank"],
                "company_id": record["company_id"],
                "scraped_page_id": record["scraped_page_id"],
                "is_hidden_repeat": record["is_hidden_repeat"],
                "status": record["status"],
                "schema_valid": record["schema_valid"],
                "identifier_valid": record["identifier_valid"],
                "utility_score": annotation.get("utility_score"),
                "attribution": annotation.get("attribution"),
                "page_category": annotation.get("page_category"),
                "page_priority_tier": annotation.get("page_priority_tier"),
                "hard_logical_contradiction": record["hard_logical_contradiction"],
                "hard_contradictions": canonical_json(record["hard_contradictions"]),
                "soft_inconsistencies": canonical_json(record["soft_inconsistencies"]),
                "positive_phrase_count": record["positive_phrase_count"],
                "normalized_grounded_phrase_count": record["normalized_grounded_phrase_count"],
                "trust_flag": record["trust_flag"],
                "trust_reason_codes": canonical_json(record["trust_reason_codes"]),
                "lexical_restriction_reason_codes": canonical_json(record["lexical_restriction_reason_codes"]),
                "ungrounded_positive_facets": canonical_json(record["ungrounded_positive_facets"]),
                "company_label": record["company_label"],
                "sampling_pool": record["sampling_pool"],
                "language": record["language"],
                "website_size": record["website_size"],
                "shared_domain": record["shared_domain"],
                "calibration_source": record["calibration_source"],
            }
        )

    # Avoid maintaining duplicate CSV schemas in multiple call sites.
    qa_fields = list(qa_csv_rows[0]) if qa_csv_rows else []
    phrase_fields = list(all_phrase_rows[0]) if all_phrase_rows else ["custom_id", "annotation_item_id", "company_id", "scraped_page_id", "facet", "facet_strength", "phrase_index", "phrase", "exact_grounded", "casefold_grounded", "normalized_grounded", "lexical_supervision_trusted", "reconstructed_context"]
    repeat_fields = sorted({key for row in repeat_rows for key in row})
    subgroup_fields = list(subgroup_rows[0]) if subgroup_rows else []
    _write_csv(output_dir / "annotation_qa.csv", qa_csv_rows, qa_fields)
    _write_csv(output_dir / "grounded_phrases.csv", all_phrase_rows, phrase_fields)
    _write_csv(output_dir / "hidden_repeat_pairs.csv", repeat_rows, repeat_fields)
    _write_csv(output_dir / "subgroup_distributions.csv", subgroup_rows, subgroup_fields)
    for dimension, rows in subgroup_tables.items():
        _write_csv(output_dir / f"subgroups_{dimension}.csv", rows, subgroup_fields)
    _atomic_json(output_dir / "hidden_repeat_consistency.json", repeat_metrics)
    _atomic_json(output_dir / "anomaly_examples.json", anomaly_examples)

    summary = {
        "qa_id": "scorer_v2_page_annotation_qa",
        "qa_version": qa_version,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "annotation_run_id": run_id,
        "annotation_metadata": metadata,
        "frozen_sample_manifest": sample_manifest,
        "integrity": integrity,
        "counts": {
            "requests": len(request_index),
            "primary_annotations": len(primary_records),
            "hidden_repeats": sum(bool(row["is_hidden_repeat"]) for row in request_index),
            "successful_responses": successful,
            "terminal_failures": terminal_failures,
            "schema_valid_responses": schema_valid,
            "identifier_valid_responses": identifier_valid,
            "positive_phrases": len(all_positive_phrases),
            "primary_positive_phrases": len(primary_phrases),
            "trusted_primary_annotations": trusted,
            "partially_trusted_primary_annotations": sum(record["trust_flag"] == "partially_trusted" for record in primary_records),
            "untrusted_primary_annotations": sum(record["trust_flag"] == "untrusted" for record in primary_records),
            "hard_logical_inconsistencies": hard_inconsistent,
        },
        "rates": {
            "schema_valid": schema_rate,
            "identifier_valid_among_successful": identifier_rate,
            "terminal_failure": terminal_rate,
            "logical_inconsistency_primary": logical_rate,
            "exact_phrase_grounding": _rate(sum(bool(row["exact_grounded"]) for row in all_positive_phrases), len(all_positive_phrases)),
            "normalized_phrase_grounding": grounding_rate,
            "trusted_primary_annotations": trusted_rate,
        },
        "distributions": {
            "utility_score": _distribution(record["annotation"]["utility_score"] for record in primary_records if record["annotation"]),
            "attribution": _distribution(record["annotation"]["attribution"] for record in primary_records if record["annotation"]),
            "page_category": _distribution(record["annotation"]["page_category"] for record in primary_records if record["annotation"]),
            "page_priority_tier": _distribution(record["annotation"]["page_priority_tier"] for record in primary_records if record["annotation"]),
            "trust_flag": _distribution(record["trust_flag"] for record in primary_records),
        },
        "hidden_repeat_consistency": repeat_metrics,
        "gate_results": gate_results,
        "failed_gates": failed_gates,
        "collapsed_or_near_constant_outputs": collapsed,
        "subgroup_divergences": divergences,
        "diagnostic_pool_dominance": diagnostic_dominance,
        "sufficiently_populated_subgroup_count": len(populated_subgroups),
        "trust_rules": {
            "untrusted": ["terminal failure", "invalid schema", "identifier mismatch", "hard logical contradiction"],
            "partially_trusted": ["a positive facet has no normalized-grounded phrase", "soft logical inconsistency"],
            "trusted": ["valid schema and identifier", "no hard or soft logical inconsistency", "every positive facet has at least one normalized-grounded phrase"],
            "lexical_supervision": "Every ungrounded phrase is restricted even when another grounded phrase keeps its page facet and annotation trusted.",
        },
        "recommendation": {"decision": recommendation, "text": recommendation_text},
        "output_paths": {
            "qa_summary": str(output_dir / "qa_summary.json"),
            "annotation_qa": str(output_dir / "annotation_qa.csv"),
            "grounded_phrases": str(output_dir / "grounded_phrases.csv"),
            "hidden_repeat_consistency": str(output_dir / "hidden_repeat_consistency.json"),
            "hidden_repeat_pairs": str(output_dir / "hidden_repeat_pairs.csv"),
            "subgroup_distributions": str(output_dir / "subgroup_distributions.csv"),
            "anomaly_examples": str(output_dir / "anomaly_examples.json"),
        },
    }
    _atomic_json(output_dir / "qa_summary.json", summary)
    recommendation_md = "\n".join(
        [
            "# Page Annotation QA Recommendation",
            "",
            f"- Run: `{run_id}`",
            f"- Decision: `{recommendation}`",
            f"- Failed gates: `{', '.join(failed_gates) if failed_gates else 'none'}`",
            "",
            recommendation_text,
            "",
            "Machine-readable trust flags are in `annotation_qa.csv`. Only rows in",
            "`grounded_phrases.csv` with `lexical_supervision_trusted=true` may be used",
            "as lexical supervision. Original annotations were not altered.",
            "",
        ]
    )
    (output_dir / "recommendation.md").write_text(recommendation_md, encoding="utf-8")
    print("Page annotation QA")
    print(f"  Run: {run_id}")
    print(f"  Primary annotations: {len(primary_records)}")
    print(f"  Hidden repeats: {repeat_metrics['available_repeat_pairs']}")
    print(f"  Trusted: {trusted_rate:.2%}")
    print(f"  Normalized phrase grounding: {grounding_rate:.2%}")
    print(f"  Failed gates: {', '.join(failed_gates) if failed_gates else 'none'}")
    print(f"  Recommendation: {recommendation}")
    print(f"  Reports: {output_dir}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run automated QA over a completed scorer-v2 annotation run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--qa-version", default=QA_VERSION)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--config", type=Path, help="Annotation YAML; defaults to config/page_annotation.yaml")
    parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = (args.project_root or find_project_root()).resolve()
    config_path = args.config
    if config_path and not config_path.is_absolute():
        config_path = project_root / config_path
    analyze_annotation_quality(
        project_root=project_root,
        run_id=args.run_id,
        qa_version=args.qa_version,
        output_directory=args.output_directory,
        config_path=config_path,
    )


if __name__ == "__main__":
    main()
