"""Frozen AI-profile request, validation, and label-mapping contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_PATH = PROMPT_DIR / "company_ai_profile_v1.txt"
SCHEMA_PATH = PROMPT_DIR / "company_ai_profile_v1.schema.json"
CONTRACT_VERSION = "company_ai_profile_v1"
MAPPING_VERSION = "company_ai_profile_mapping_v1"
REVISED_CONTRACT_VERSION = "company_ai_profile_v2"
REVISED_MAPPING_VERSION = "company_ai_profile_mapping_v2"
SCOPED_CONTRACT_VERSION = "company_ai_profile_v3"
SCOPED_MAPPING_VERSION = "company_ai_profile_mapping_v3"
PILOT_FIX_CONTRACT_VERSION = "company_ai_profile_v4"
PILOT_FIX_MAPPING_VERSION = "company_ai_profile_mapping_v4"
QUOTE_VALIDATION_VERSION = "quote_edge_whitespace_v2"
SCOPED_CONTRACTS = {SCOPED_CONTRACT_VERSION, PILOT_FIX_CONTRACT_VERSION}
SCOPED_MAPPINGS = {SCOPED_MAPPING_VERSION, PILOT_FIX_MAPPING_VERSION}
POSITIVE_SCOPES = {"website_business", "focal_unit", "group"}
SYMMETRIC = {"collaborates_on_ai_with", "partners_with"}
AI_RELATIONS = {"supplies_ai_to", "integrates_ai_from", "supplies_ai_infrastructure_to",
                "supplies_ai_data_to", "collaborates_on_ai_with"}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def prompt_and_schema(contract_id: str = CONTRACT_VERSION) -> tuple[str, dict]:
    if contract_id not in {CONTRACT_VERSION, REVISED_CONTRACT_VERSION, *SCOPED_CONTRACTS}:
        raise ValueError(f"Unsupported profile contract: {contract_id}")
    stem = PROMPT_DIR / contract_id
    return stem.with_suffix(".txt").read_text(encoding="utf-8"), json.loads(
        stem.with_suffix(".schema.json").read_text(encoding="utf-8"))


def request_body(package: dict, config: dict, prompt: str, schema: dict) -> dict:
    """Exactly the body used by ordinary requests and future Batch JSONL."""
    visible = {
        "company_id": package["company_id"],
        "company_name": package["company_name"],
        "normalized_domain": package["normalized_domain"],
        "shared_website": package["shared_website"],
        "coverage": package["coverage"],
        "pages": [{k: page[k] for k in ("page_id", "url", "title", "text", "truncated")}
                  for page in package["pages"]],
    }
    forbidden = ("score", "rank", "label", "sector", "selection_group", "historical")
    assert not any(part in canonical(visible).lower() for part in ('"'+f+'":' for f in forbidden))
    return {
        "model": config["model"],
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": int(config["max_output_tokens"]),
        "input": [{"role": "system", "content": prompt},
                  {"role": "user", "content": canonical(visible)}],
        "text": {"format": {"type": "json_schema", "name": config.get("contract_id", CONTRACT_VERSION),
                            "strict": True, "schema": schema}},
    }


def normalize_relationships(profile: dict) -> dict:
    """Normalize symmetric orientation and exact duplicate facts only."""
    profile = json.loads(canonical(profile))
    merged: dict[tuple, dict] = {}
    for item in profile["relationships"]:
        if item["type"] in SYMMETRIC and item["target"] == "focal_company":
            item["source"], item["target"] = item["target"], item["source"]
        key = (item["source"], item["type"], item["target"], item["ai_context"],
               item["status"], item["date_text"], item["context"])
        if key in merged:
            merged[key]["evidence_ids"] = sorted(set(merged[key]["evidence_ids"] + item["evidence_ids"]))
            merged[key]["confidence"] = max(merged[key]["confidence"], item["confidence"])
        else:
            merged[key] = item
    profile["relationships"] = list(merged.values())
    return profile


def quote_match_status(quote: str, page_text: str) -> str | None:
    """Accept exact quotes or only edge-whitespace-trimmed quotes, without rewriting them."""
    if quote and quote.strip() and quote in page_text:
        return "PASS_EXACT"
    trimmed = quote.strip()
    if trimmed and trimmed != quote and trimmed in page_text:
        return "PASS_EDGE_WHITESPACE_TRIMMED"
    return None


def validate_profile(profile: dict, package: dict, schema: dict, *, contract_id: str = CONTRACT_VERSION) -> dict:
    """Raise ValueError for local grounding, reference, and semantic-contract failures."""
    try:
        jsonschema.Draft202012Validator(schema).validate(profile)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"invalid_schema:{exc.message}") from exc
    pages = {str(p["page_id"]): p["text"] for p in package["pages"]}
    evidence = {}
    for item in profile["evidence"]:
        eid, pid, quote = item["id"], item["page_id"], item["quote"]
        if eid in evidence or not eid.strip():
            raise ValueError("duplicate_or_empty_evidence_id")
        if pid not in pages:
            raise ValueError(f"unknown_page_id:{pid}")
        if quote_match_status(quote, pages[pid]) is None:
            raise ValueError(f"ungrounded_quote:{eid}:{pid}")
        evidence[eid] = item
    def refs(item: dict, *, required: bool = False) -> None:
        ids = item["evidence_ids"]
        if (required and not ids) or len(ids) != len(set(ids)) or any(e not in evidence for e in ids):
            raise ValueError("missing_duplicate_or_unknown_evidence_reference")
    for name in ("ai_active", "ai_adjacent"):
        item = profile[name]
        refs(item, required=item["value"] is True)
        if item["value"] is True:
            allowed = {"explicit"} if name == "ai_active" else {"explicit", "inferred_compute", "inferred_data"}
            if item["basis"] not in allowed or item["status"] not in {"operational", "pilot_research"}:
                raise ValueError(f"invalid_positive_{name}")
            if contract_id in SCOPED_CONTRACTS and item["attribution_scope"] not in POSITIVE_SCOPES:
                raise ValueError(f"missing_positive_attribution_scope_{name}")
        elif item["basis"] != "none":
            raise ValueError(f"invalid_nonpositive_basis_{name}")
        elif contract_id in SCOPED_CONTRACTS and item["attribution_scope"] is not None:
            raise ValueError(f"invalid_nonpositive_attribution_scope_{name}")
    refs(profile["attribution"])
    if contract_id in {REVISED_CONTRACT_VERSION, *SCOPED_CONTRACTS} and any(
            profile[name]["value"] is True for name in ("ai_active", "ai_adjacent")):
        if profile["attribution"]["value"] is not True or not profile["attribution"]["evidence_ids"]:
            raise ValueError("positive_assessment_requires_focal_attribution_evidence")
    group_membership_refs = set()
    if contract_id in SCOPED_CONTRACTS:
        group_assessments = [profile[name] for name in ("ai_active", "ai_adjacent")
                             if profile[name]["value"] is True and profile[name]["attribution_scope"] == "group"]
        for item in group_assessments:
            shared_refs = set(item["evidence_ids"]) & set(profile["attribution"]["evidence_ids"])
            if not shared_refs:
                raise ValueError("group_scope_requires_membership_reference")
            group_membership_refs.update(shared_refs)
    entities = {"focal_company": "organization"}
    group_entity_refs = {}
    for entity in profile["entities"]:
        if entity["id"] in entities or not entity["id"].startswith("e") or not entity["id"][1:].isdigit():
            raise ValueError("duplicate_or_invalid_entity_id")
        entities[entity["id"]] = entity["type"]
        refs(entity, required=True)
        if contract_id in SCOPED_CONTRACTS and entity["type"] == "group_organization":
            if not (set(entity["evidence_ids"]) & group_membership_refs):
                raise ValueError("group_entity_requires_focal_membership_evidence")
            group_entity_refs[entity["id"]] = set(entity["evidence_ids"])
    for key in ("roles", "technologies", "relationships", "unmapped_findings"):
        for item in profile[key]:
            refs(item, required=True)
    for edge in profile["relationships"]:
        source, target, kind = edge["source"], edge["target"], edge["type"]
        if source not in entities or target not in entities or source == target:
            raise ValueError("invalid_relationship_endpoint")
        if "focal_company" not in (source, target):
            if (contract_id not in SCOPED_CONTRACTS or
                    len({source, target} & set(group_entity_refs)) != 1):
                raise ValueError("invalid_relationship_endpoint")
        if kind == "participates_in_project":
            if entities[source] not in {"organization", "group_organization"} or entities[target] != "project":
                raise ValueError("invalid_project_endpoint")
        elif entities[source] not in {"organization", "group_organization"} or entities[target] not in {"organization", "group_organization"}:
            raise ValueError("invalid_organization_endpoint")
        if kind in AI_RELATIONS and edge["ai_context"] != "explicit_ai":
            raise ValueError("missing_ai_connection")
        if kind in SYMMETRIC and edge["source"] == edge["target"]:
            raise ValueError("self_relationship")
    if not (profile["ai_active"]["value"] is True or profile["ai_adjacent"]["value"] is True):
        if profile["ai_summary"] or any(profile[k] for k in ("roles", "technologies", "entities", "relationships")):
            raise ValueError("nonpositive_profile_has_positive_outputs")
    return normalize_relationships(profile)


def derive_label(profile: dict | None, *, status: str = "valid",
                 mapping_version: str = MAPPING_VERSION) -> dict:
    if mapping_version not in {MAPPING_VERSION, REVISED_MAPPING_VERSION, *SCOPED_MAPPINGS}:
        raise ValueError(f"Unsupported profile mapping: {mapping_version}")
    def with_scopes(result: dict) -> dict:
        if mapping_version not in SCOPED_MAPPINGS:
            return result
        scopes = ({name: profile[name]["attribution_scope"] for name in ("ai_active", "ai_adjacent")}
                  if profile is not None and status == "valid" else {"ai_active": None, "ai_adjacent": None})
        return {**result, "attribution_scopes": scopes,
                "group_attributed": result["label"] == "KEEP" and "group" in scopes.values()}
    if status != "valid" or profile is None:
        return with_scopes({"label": None, "reason_code": status, "mapping_version": mapping_version,
                            "inferred_adjacency_only": False})
    active, adjacent = profile["ai_active"], profile["ai_adjacent"]
    attributable = profile["attribution"]["value"] is True
    positive = any(item["value"] is True and item["evidence_ids"] and
                   item["status"] in {"operational", "pilot_research"} and
                   item["basis"] in ({"explicit"} if item is active else {"explicit", "inferred_compute", "inferred_data"}) and
                   (mapping_version not in SCOPED_MAPPINGS or item["attribution_scope"] in POSITIVE_SCOPES)
                   for item in (active, adjacent))
    if attributable and positive:
        inferred_only = active["value"] is not True and adjacent["basis"] in {"inferred_compute", "inferred_data"}
        return with_scopes({"label": "KEEP", "reason_code": "supported_attributed_positive", "mapping_version": mapping_version,
                            "inferred_adjacency_only": inferred_only})
    if (active["value"] is False and adjacent["value"] is False and attributable
            and profile["evidence_adequacy"]["value"] == "sufficient"
            and not any(x["affects_eligibility"] for x in profile["unmapped_findings"])):
        return with_scopes({"label": "EXCLUDE", "reason_code": "sufficient_attributed_nonpositive", "mapping_version": mapping_version,
                            "inferred_adjacency_only": False})
    return with_scopes({"label": "UNKNOWN", "reason_code": "unresolved_valid_profile", "mapping_version": mapping_version,
                        "inferred_adjacency_only": False})
