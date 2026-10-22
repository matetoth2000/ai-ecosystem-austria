"""Prepare, inspect, and (only on explicit command) run the ten-company AI-profile pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_ecosystem.db.init_db import find_project_root
from ai_ecosystem.validation.config import load_openai_api_key
from .company_ai_profile_contract import (QUOTE_VALIDATION_VERSION, canonical, derive_label, digest,
                                          prompt_and_schema, quote_match_status, request_body, validate_profile)
from .company_ai_profile_packages import DEFAULT_OUTPUT, LEGACY_CONFIG, load_config, prepare, tokens_upper_bound
from .company_ai_profile_tokens import (count_payload, local_tokens, non_evidence_breakdown,
                                         reservation_usd, token_breakdown)


REVISED_CONFIG = "config/company_ai_profile_pilot_v3.yaml"
REVISED_OUTPUT = "data/interim/calibration/ai_profile_pilot_v4_contract_clarification"
SCOPED_CONFIG = "config/company_ai_profile_pilot_v4.yaml"
SCOPED_OUTPUT = "data/interim/calibration/ai_profile_pilot_v5_attribution_scope"
PILOT_FIX_CONFIG = "config/company_ai_profile_pilot_v5.yaml"
PILOT_FIX_OUTPUT = "data/interim/calibration/ai_profile_pilot_v6_four_case_fix"


def package_for(directory: Path, company_id: str) -> dict:
    reference = directory / "source_packages.json"
    if not reference.exists():
        return read_json(directory / "packages" / f"{company_id}.json")
    source = read_json(reference)
    if company_id not in source["package_sha256"]:
        raise ValueError(f"Missing frozen package reference: {company_id}")
    path = Path(source["directory"]) / "packages" / f"{company_id}.json"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["package_sha256"][company_id]:
        raise ValueError(f"Frozen source package changed: {company_id}")
    return json.loads(raw)


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_prepared(root: Path, directory: Path) -> tuple[dict, str, dict, list[dict], list[dict]]:
    config = read_json(directory / "config_snapshot.json")
    prompt = (directory / "prompt_snapshot.txt").read_text(encoding="utf-8")
    schema = read_json(directory / "response.schema.json")
    canonical_prompt, canonical_schema = prompt_and_schema(config.get("contract_id", "company_ai_profile_v1"))
    if prompt != canonical_prompt or canonical(schema) != canonical(canonical_schema):
        raise ValueError("Prepared prompt/schema no longer match versioned contract")
    config_path = (PILOT_FIX_CONFIG if config.get("contract_id") == "company_ai_profile_v4" else
                   SCOPED_CONFIG if config.get("contract_id") == "company_ai_profile_v3" else
                   REVISED_CONFIG if config.get("contract_id") == "company_ai_profile_v2" else
                   LEGACY_CONFIG if "token_counting" not in config else "config/company_ai_profile_pilot_v2.yaml")
    expected_config = load_config(root, root / config_path)
    if config != expected_config:
        raise ValueError("Prepared configuration no longer matches versioned configuration")
    manifest = read_json(directory / "selection_manifest.json")["companies"]
    index = read_json(directory / "requests.json")
    expected_count = 4 if config.get("contract_id") == "company_ai_profile_v4" else 10
    if len(manifest) != expected_count or len(index) != expected_count:
        raise ValueError(f"Pilot must contain exactly {expected_count} companies")
    source_reference = directory / "source_packages.json"
    if source_reference.exists():
        source = read_json(source_reference)
        if (source.get("contract_id") != config.get("contract_id") or
                set(source["package_sha256"]) != {row["company_id"] for row in index}):
            raise ValueError("Frozen source package registry mismatch")
        manifest_bytes = (Path(source["directory"]) / "selection_manifest.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != source["selection_manifest_sha256"]:
            raise ValueError("Frozen source selection manifest changed")
        source_manifest = json.loads(manifest_bytes)
        selected_ids = source.get("selected_company_ids")
        if selected_ids is not None:
            if selected_ids != [row["company_id"] for row in index]:
                raise ValueError("Frozen source selected-company order changed")
            source_manifest["companies"] = [row for row in source_manifest["companies"]
                                            if row["company_id"] in set(selected_ids)]
        if read_json(directory / "selection_manifest.json") != source_manifest:
            raise ValueError("Frozen source selection membership changed")
    for item, row in zip(manifest, index):
        if item["company_id"] != row["company_id"]:
            raise ValueError("Request index/manifest mismatch")
        package = package_for(directory, row["company_id"])
        body = request_body(package, config, prompt, schema)
        if digest(body) != row["request_hash"]:
            raise ValueError(f"Prepared request changed: {row['company_id']}")
        if "token_counting" in config:
            from hashlib import sha256
            counted_hash = sha256(canonical(count_payload(body)).encode("utf-8")).hexdigest()
            if counted_hash != row["model_count_payload_sha256"]:
                raise ValueError(f"Counted request changed: {row['company_id']}")
            if row["website_evidence_tokens_exact"] > config["website_evidence_token_cap"]:
                raise ValueError(f"Evidence budget exceeded: {row['company_id']}")
            if (row["request_input_tokens_exact"] + config["max_output_tokens"] >
                    config["max_context_tokens"]):
                raise ValueError(f"Context budget exceeded: {row['company_id']}")
    return config, prompt, schema, manifest, index


def body_for(directory: Path, config: dict, prompt: str, schema: dict, company_id: str) -> dict:
    return request_body(package_for(directory, company_id), config, prompt, schema)


def reservation(config: dict, input_tokens: int) -> float:
    return reservation_usd(config, input_tokens)


def actual_cost(config: dict, usage: dict) -> float:
    price = config["pricing"]
    input_tokens = int(usage.get("input_tokens") or 0)
    cached = min(input_tokens, int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0))
    output = int(usage.get("output_tokens") or 0)
    factor = price["long_context_input_multiplier"] if input_tokens > price["long_context_input_threshold"] else 1
    output_factor = price["long_context_output_multiplier"] if factor != 1 else 1
    return ((input_tokens - cached) * price["input_per_million"] * factor +
            cached * price["cached_input_per_million"] * factor +
            output * price["output_per_million"] * output_factor) / 1_000_000


def dry_run(root: Path, directory: Path) -> dict:
    config, prompt, schema, manifest, index = load_prepared(root, directory)
    totals = {"reservation_usd": 0.0, "input_tokens": 0}
    for company, row in zip(manifest, index):
        package = package_for(directory, company["company_id"])
        body = request_body(package, config, prompt, schema)
        if "token_counting" in config:
            input_tokens = int(row["request_input_tokens_exact"])
            if local_tokens(body["input"][1]["content"], config) != row["allocation_local_tokens"]:
                raise ValueError("Local allocation token count drift")
        else:
            input_tokens = tokens_upper_bound(canonical(body))
            if input_tokens != row["request_token_upper_bound"]:
                raise ValueError("Legacy token-estimate drift")
        cost = reservation(config, input_tokens) if package["pages"] else 0.0
        totals["reservation_usd"] += cost
        totals["input_tokens"] += input_tokens
        print(f"{company['company_id']} {company['selection_group']}: "
              f"pages={len(package['pages'])}/{package['coverage']['available_unique_pages']} "
              f"omitted={package['coverage']['omitted_pages']} "
              f"{'verified_input_tokens' if 'token_counting' in config else 'legacy_byte_bound'}={input_tokens:,} "
              f"reserved=${cost:.4f}"
              f"{' NO_EVIDENCE/no call' if not package['pages'] else ''}")
    print(f"Total {'verified input tokens' if 'token_counting' in config else 'legacy byte-bound units'}="
          f"{totals['input_tokens']:,}; reserve ${totals['reservation_usd']:.4f} / "
          f"${config['pilot_spend_allowance_usd']:.2f} allowance")
    if "token_counting" in config:
        print("Input counts came from the model-specific count endpoint at preparation. "
              "Output reserves 8,000 tokens per call; actual billing may vary with caching or retries.")
    else:
        print("Legacy UTF-8 byte counts are not model token counts.")
    if totals["reservation_usd"] > config["pilot_spend_allowance_usd"]:
        raise ValueError("Planned pilot exceeds spend allowance")
    return totals


def export_batch(root: Path, directory: Path, output: Path) -> None:
    config, prompt, schema, _, index = load_prepared(root, directory)
    if output.exists():
        raise FileExistsError(output)
    lines = [{"custom_id": row["custom_id"], "method": "POST", "url": "/v1/responses",
              "body": body_for(directory, config, prompt, schema, row["company_id"])} for row in index]
    if len({line["custom_id"] for line in lines}) != len(index):
        raise ValueError("Duplicate Batch custom_id")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(canonical(line) + "\n" for line in lines), encoding="utf-8")
    print(f"Exported {len(lines)} requests to {output}; no upload or submission occurred")


def extract_text(raw: dict) -> tuple[str, bool]:
    parts = []
    refused = False
    for output in raw.get("output", []) or []:
        for content in output.get("content", []) or []:
            if content.get("type") == "refusal" or content.get("refusal"):
                refused = True
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "".join(parts), refused


def parse_response(raw: dict, package: dict, schema: dict, *,
                   contract_id: str = "company_ai_profile_v1",
                   mapping_version: str = "company_ai_profile_mapping_v1") -> dict:
    usage = raw.get("usage") or {}
    base = {"response_id": raw.get("id"), "returned_model": raw.get("model"), "usage": usage,
            "completed_at": now(), "raw_status": raw.get("status")}
    if raw.get("status") != "completed":
        return {**base, "status": "incomplete_output", "detail": raw.get("incomplete_details"), "profile": None,
                "derived": derive_label(None, status="incomplete_output", mapping_version=mapping_version)}
    text, refused = extract_text(raw)
    if refused:
        return {**base, "status": "refusal", "profile": None, "derived": derive_label(None, status="refusal", mapping_version=mapping_version)}
    try:
        profile = json.loads(text)
    except (ValueError, TypeError) as exc:
        return {**base, "status": "invalid_schema", "detail": str(exc), "profile": None,
                "derived": derive_label(None, status="invalid_schema", mapping_version=mapping_version)}
    try:
        profile = validate_profile(profile, package, schema, contract_id=contract_id)
    except ValueError as exc:
        detail = str(exc)
        status = "invalid_schema" if detail.startswith("invalid_schema") else (
            "failed_exact_quote" if detail.startswith("ungrounded_quote") else "invalid_references_or_contract")
        return {**base, "status": status, "detail": detail, "profile": profile,
                "derived": derive_label(None, status=status, mapping_version=mapping_version)}
    return {**base, "status": "valid", "profile": profile,
            "derived": derive_label(profile, mapping_version=mapping_version)}


def prepare_rerun(root: Path, source_directory: Path, directory: Path, *,
                  contract_id: str = "company_ai_profile_v2", company_ids: list[str] | None = None,
                  token_client=None) -> dict:
    """Bind new requests to the exact frozen v3 page evidence, without repackaging."""
    if directory.exists():
        raise FileExistsError(directory)
    if contract_id not in {"company_ai_profile_v2", "company_ai_profile_v3", "company_ai_profile_v4"}:
        raise ValueError(f"Unsupported rerun contract: {contract_id}")
    if contract_id in {"company_ai_profile_v3", "company_ai_profile_v4"} and source_directory.resolve() != (root / DEFAULT_OUTPUT).resolve():
        raise ValueError("Scoped contract requires the frozen v3 product-priority packages")
    if contract_id == "company_ai_profile_v4":
        if company_ids is None or len(company_ids) != 4 or len(set(company_ids)) != 4:
            raise ValueError("v4 pilot fix requires four distinct --company-id values")
    elif company_ids:
        raise ValueError("Company filtering is supported only for the v4 pilot fix")
    old_config, old_prompt, old_schema, manifest, old_index = load_prepared(root, source_directory)
    if old_config.get("contract_id") != "company_ai_profile_v1":
        raise ValueError("Rerun source must use the v1 pilot contract")
    if company_ids is not None:
        requested = set(company_ids)
        available = {row["company_id"] for row in manifest}
        if requested - available:
            raise ValueError(f"Unknown frozen pilot companies: {sorted(requested - available)}")
        manifest = [row for row in manifest if row["company_id"] in requested]
        old_index = [row for row in old_index if row["company_id"] in requested]
    config_file = (PILOT_FIX_CONFIG if contract_id == "company_ai_profile_v4" else
                   SCOPED_CONFIG if contract_id == "company_ai_profile_v3" else REVISED_CONFIG)
    config = load_config(root, root / config_file)
    for key in ("model", "reasoning_effort", "max_output_tokens", "website_evidence_token_cap"):
        if config[key] != old_config[key]:
            raise ValueError(f"Rerun changed frozen API/evidence setting: {key}")
    prompt, schema = prompt_and_schema(config["contract_id"])
    packages = [package_for(source_directory, row["company_id"]) for row in old_index]
    for package, row in zip(packages, old_index):
        if digest(request_body(package, old_config, old_prompt, old_schema)) != row["request_hash"]:
            raise ValueError(f"Source request changed: {row['company_id']}")
    if token_client is None:
        from openai import OpenAI
        token_client = OpenAI(api_key=load_openai_api_key(root),
                              timeout=config["request_timeout_seconds"], max_retries=1)
    baseline = non_evidence_breakdown(token_client, request_body(packages[0], config, prompt, schema))
    requests = []
    for package in packages:
        body = request_body(package, config, prompt, schema)
        counts = token_breakdown(token_client, body, baseline["non_evidence_input_tokens_exact"])
        if counts["website_evidence_tokens_exact"] > config["website_evidence_token_cap"]:
            raise ValueError(f"Frozen package exceeds revised evidence budget: {package['company_id']}")
        if counts["request_input_tokens_exact"] + config["max_output_tokens"] > config["max_context_tokens"]:
            raise ValueError(f"Frozen package exceeds context: {package['company_id']}")
        if digest(body) == old_index[len(requests)]["request_hash"]:
            raise ValueError(f"Rerun did not change request identity: {package['company_id']}")
        requests.append({"company_id": package["company_id"], "request_hash": digest(body),
                         "custom_id": f"profile-{config['contract_id']}-{package['company_id']}-{digest(body)[:16]}",
                         "allocation_local_tokens": local_tokens(body["input"][1]["content"], config),
                         **counts})
    total_reservation = sum(reservation_usd(config, row["request_input_tokens_exact"])
                            for package, row in zip(packages, requests) if package["pages"])
    if total_reservation > config["pilot_spend_allowance_usd"]:
        raise ValueError("Revised pilot exceeds spend allowance")
    package_hashes = {row["company_id"]: hashlib.sha256(
        (source_directory / "packages" / f"{row['company_id']}.json").read_bytes()).hexdigest()
        for row in old_index}
    directory.mkdir(parents=True)
    save_json(directory / "source_packages.json", {
        "directory": str(source_directory.resolve()), "contract_id": config["contract_id"],
        "selection_manifest_sha256": hashlib.sha256(
            (source_directory / "selection_manifest.json").read_bytes()).hexdigest(),
        "package_sha256": package_hashes,
        **({"selected_company_ids": [row["company_id"] for row in old_index]}
           if contract_id == "company_ai_profile_v4" else {})})
    source_manifest = read_json(source_directory / "selection_manifest.json")
    if contract_id == "company_ai_profile_v4":
        source_manifest["companies"] = manifest
    save_json(directory / "selection_manifest.json", source_manifest)
    save_json(directory / "config_snapshot.json", config)
    (directory / "prompt_snapshot.txt").write_text(prompt, encoding="utf-8")
    save_json(directory / "response.schema.json", schema)
    save_json(directory / "requests.json", requests)
    save_json(directory / "build_summary.json", {
        "contract_id": config["contract_id"], "mapping_version": config["mapping_version"],
        "source_directory": str(source_directory.resolve()), "companies": len(packages),
        "page_selection_unchanged": True, "total_included_pages": sum(len(p["pages"]) for p in packages),
        "total_omitted_pages": sum(p["coverage"]["omitted_pages"] for p in packages),
        "input_tokens_exact": sum(row["request_input_tokens_exact"] for row in requests),
        "non_evidence_tokens_per_request": baseline,
        "output_tokens_reserved_per_request": config["max_output_tokens"],
        "total_reservation_usd": total_reservation,
        "per_company": [{"company_id": p["company_id"], "included_pages": len(p["pages"]),
                         "omitted_pages": p["coverage"]["omitted_pages"],
                         "before_input_tokens_exact": old["request_input_tokens_exact"],
                         "after_input_tokens_exact": new["request_input_tokens_exact"],
                         "reservation_usd": reservation_usd(config, new["request_input_tokens_exact"])
                         if p["pages"] else 0.0}
                        for p, old, new in zip(packages, old_index, requests)]})
    load_prepared(root, directory)
    return read_json(directory / "build_summary.json")


def _existing_spend(directory: Path, config: dict) -> float:
    total = 0.0
    for path in (directory / "attempts").glob("*/*.json"):
        item = read_json(path)
        if item.get("status") in {"ambiguous_network_outcome", "in_flight"}:
            total += float(item["reserved_usd"])
        elif item.get("raw_path"):
            raw = read_json(directory / item["raw_path"])
            total += actual_cost(config, raw.get("usage") or {})
    return total


def run(root: Path, directory: Path, *, resume: bool = False,
        retry_status: set[str] | None = None, retry_ambiguous: bool = False,
        client: Any | None = None) -> None:
    config, prompt, schema, manifest, index = load_prepared(root, directory)
    run_marker = directory / "pilot_started.json"
    if run_marker.exists() and not resume:
        raise ValueError("Pilot already started; use resume explicitly")
    if not run_marker.exists() and resume:
        raise ValueError("No pilot run to resume")
    if not run_marker.exists():
        dry_run(root, directory)
        save_json(run_marker, {"started_at": now(), "mode": "ordinary_responses", "model": config["model"]})
    if client is None:
        from openai import OpenAI
        client = OpenAI(api_key=load_openai_api_key(root), timeout=config["request_timeout_seconds"], max_retries=0)
    started = time.monotonic()
    selected_count = len(index)
    for position, (company, row) in enumerate(zip(manifest, index), start=1):
        cid = row["company_id"]
        result_path = directory / "results" / f"{cid}.json"
        package = package_for(directory, cid)
        prior = read_json(result_path) if result_path.exists() else None
        past_attempts = sorted((directory / "attempts" / cid).glob("*.json"))
        if prior is None and past_attempts:
            last = read_json(past_attempts[-1])
            if last["request_hash"] != row["request_hash"]:
                raise ValueError(f"Attempt identity differs: {cid}")
            raw_path = directory / "raw_responses" / cid / past_attempts[-1].name
            if raw_path.exists():
                raw = read_json(raw_path)
                recovered = parse_response(raw, package, schema,
                                           contract_id=config.get("contract_id", "company_ai_profile_v1"),
                                           mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1"))
                recovered.update({"company_id": cid, "request_hash": row["request_hash"],
                                  "attempts": len(past_attempts), "cost_usd": actual_cost(config, raw.get("usage") or {}),
                                  "raw_path": str(raw_path.relative_to(directory))})
                save_json(past_attempts[-1], {**last, "status": "response_received",
                                             "raw_path": str(raw_path.relative_to(directory))})
                save_json(result_path, recovered)
                prior = recovered
            elif last.get("status") == "in_flight":
                save_json(past_attempts[-1], {**last, "status": "ambiguous_network_outcome"})
                prior = {"company_id": cid, "request_hash": row["request_hash"],
                         "status": "ambiguous_network_outcome", "attempts": len(past_attempts),
                         "derived": derive_label(None, status="ambiguous_network_outcome",
                                                 mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1"))}
                save_json(result_path, prior)
            elif last.get("status") in {"api_failure", "ambiguous_network_outcome"}:
                status = last["status"]
                prior = {"company_id": cid, "request_hash": row["request_hash"],
                         "status": status, "attempts": len(past_attempts),
                         "derived": derive_label(None, status=status,
                                                 mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1")), "detail": last.get("error")}
                save_json(result_path, prior)
        if prior:
            if prior["request_hash"] != row["request_hash"]:
                raise ValueError(f"Saved result request identity differs: {cid}")
            if prior["status"] == "valid":
                print(f"[{position}/{selected_count}] {cid} saved valid; skipped", flush=True)
                continue
            if not resume or (prior["status"] == "ambiguous_network_outcome" and not retry_ambiguous) or (
                    prior["status"] != "ambiguous_network_outcome" and prior["status"] not in (retry_status or set())):
                print(f"[{position}/{selected_count}] {cid} saved {prior['status']}; skipped (explicit retry required)", flush=True)
                continue
        if not package["pages"]:
            save_json(result_path, {"company_id": cid, "request_hash": row["request_hash"],
                                    "status": "NO_EVIDENCE", "derived": derive_label(None, status="NO_EVIDENCE",
                                        mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1")),
                                    "attempts": len(past_attempts)})
            print(f"[{position}/{selected_count}] {cid} NO_EVIDENCE; no API call", flush=True)
            continue
        used = _existing_spend(directory, config)
        reserve = reservation(config, row.get("request_input_tokens_exact", row.get("request_token_upper_bound")))
        if used + reserve > config["pilot_spend_allowance_usd"]:
            raise RuntimeError(f"Local spend guard: ${used:.4f} recorded/reserved + ${reserve:.4f} next > "
                               f"${config['pilot_spend_allowance_usd']:.2f} allowance")
        body = request_body(package, config, prompt, schema)
        attempt = len(past_attempts) + 1
        attempt_path = directory / "attempts" / cid / f"{attempt:04d}.json"
        save_json(attempt_path, {"request_hash": row["request_hash"], "attempt": attempt, "at": now(),
                                 "status": "in_flight", "reserved_usd": reserve})
        print(f"[{position}/{selected_count}] {cid} requesting; elapsed={time.monotonic()-started:.1f}s "
              f"recorded/reserved=${used:.4f}", flush=True)
        try:
            response = client.responses.create(**body)
        except Exception as exc:
            # Transport failures may have been accepted by the server; reserve their cost.
            known_http_failure = getattr(exc, "status_code", None) is not None
            status = "api_failure" if known_http_failure else "ambiguous_network_outcome"
            error = {"request_hash": row["request_hash"], "attempt": attempt, "at": now(),
                     "status": status, "reserved_usd": reserve if not known_http_failure else 0,
                     "http_status": getattr(exc, "status_code", None),
                     "error_type": type(exc).__name__, "error": str(exc)}
            save_json(attempt_path, error)
            save_json(result_path, {"company_id": cid, "request_hash": row["request_hash"],
                                    "status": status, "derived": derive_label(None, status=status,
                                        mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1")),
                                    "attempts": attempt, "detail": str(exc)})
            print(f"[{position}/{selected_count}] {cid} {status}; manual retry only", flush=True)
            continue
        raw = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
        raw_path = Path("raw_responses") / cid / f"{attempt:04d}.json"
        save_json(directory / raw_path, raw)
        save_json(attempt_path, {"request_hash": row["request_hash"], "attempt": attempt, "at": now(),
                                 "status": "response_received", "raw_path": str(raw_path)})
        parsed = parse_response(raw, package, schema,
                                contract_id=config.get("contract_id", "company_ai_profile_v1"),
                                mapping_version=config.get("mapping_version", "company_ai_profile_mapping_v1"))
        parsed.update({"company_id": cid, "request_hash": row["request_hash"], "attempts": attempt,
                       "cost_usd": actual_cost(config, raw.get("usage") or {}), "raw_path": str(raw_path)})
        save_json(result_path, parsed)
        print(f"[{position}/{selected_count}] {cid}: {parsed['status']} {parsed['derived']['label']} "
              f"elapsed={time.monotonic()-started:.1f}s recorded/reserved=${_existing_spend(directory, config):.4f}", flush=True)


def quote_diagnostic(package: dict, item: dict) -> tuple[str, list[str]]:
    """Explain cited-page acceptance or a failed quotation without changing it."""
    pages = {str(page["page_id"]): page["text"] for page in package["pages"]}
    page_id, quote = str(item["page_id"]), item["quote"]
    if page_id not in pages:
        return "UNKNOWN_PAGE_ID", []
    match = quote_match_status(quote, pages[page_id])
    if match:
        return match, []
    if not quote.strip():
        return "EMPTY_QUOTE", []
    other_exact = sorted(pid for pid, text in pages.items() if pid != page_id and quote and quote in text)
    if other_exact:
        return "WRONG_PAGE_ID", other_exact
    collapsed = " ".join(quote.split())
    if collapsed and collapsed in " ".join(pages[page_id].split()):
        return "WHITESPACE_ONLY_MISMATCH", []
    return "MISSING_QUOTE", []


def revalidate_saved(root: Path, directory: Path, output: Path) -> dict:
    """Reparse saved raw responses under the current validator without changing originals."""
    config, _, schema, _, index = load_prepared(root, directory)
    if output.exists():
        raise FileExistsError(output)
    staged = []
    for row in index:
        cid = row["company_id"]
        original_path = directory / "results" / f"{cid}.json"
        original_bytes = original_path.read_bytes()
        original = json.loads(original_bytes)
        if original["request_hash"] != row["request_hash"]:
            raise ValueError(f"Saved result request mismatch: {cid}")
        raw_path = directory / original["raw_path"]
        raw_bytes = raw_path.read_bytes()
        raw = json.loads(raw_bytes)
        parsed = parse_response(raw, package_for(directory, cid), schema,
                                contract_id=config["contract_id"], mapping_version=config["mapping_version"])
        parsed.update({"company_id": cid, "request_hash": row["request_hash"],
                       "quote_validation_version": QUOTE_VALIDATION_VERSION,
                       "source_result_path": str(original_path.resolve()),
                       "source_result_sha256": hashlib.sha256(original_bytes).hexdigest(),
                       "source_response_path": str(raw_path.resolve()),
                       "source_response_sha256": hashlib.sha256(raw_bytes).hexdigest()})
        staged.append(parsed)
    output.mkdir(parents=True)
    for parsed in staged:
        save_json(output / "results" / f"{parsed['company_id']}.json", parsed)
    summary = {"quote_validation_version": QUOTE_VALIDATION_VERSION,
               "source_directory": str(directory.resolve()),
               "companies": len(staged),
               "results": [{"company_id": row["company_id"], "status": row["status"],
                            "label": row["derived"]["label"], "detail": row.get("detail")}
                           for row in staged]}
    save_json(output / "summary.json", summary)
    return summary


def report(root: Path, directory: Path, *, results_directory: Path | None = None) -> Path:
    _, _, _, manifest, _ = load_prepared(root, directory)
    if results_directory is not None:
        missing = [row["company_id"] for row in manifest
                   if not (results_directory / f"{row['company_id']}.json").exists()]
        if missing:
            raise ValueError(f"Revalidated results missing companies: {missing}")
    path = directory / "review_report.md"
    if path.exists():
        number = 2
        while (directory / f"review_report_{number}.md").exists():
            number += 1
        path = directory / f"review_report_{number}.md"
    source_results = results_directory or directory / "results"
    lines = ["# AI-Profile Pilot Review", "",
             "Source results: " + str(source_results),
             "Exact cited-page quotes and quotes trimmed only at their edges pass validation; internal whitespace changes and wrong-page citations do not. Original response quotations remain unchanged. These checks do not establish semantic correctness.", ""]
    for company in manifest:
        cid = company["company_id"]
        package = package_for(directory, cid)
        result_path = source_results / f"{cid}.json"
        result = read_json(result_path) if result_path.exists() else {"status": "not_run", "derived": {"label": None, "reason_code": "not_run"}}
        profile = result.get("profile") or {}
        urls = {p["page_id"]: p["url"] for p in package["pages"]}
        lines += [f"## {company['company_name']} ({cid})", "", f"Website: {company['normalized_domain']}; selection group: {company['selection_group']}", "",
                  f"Status: {result['status']}; derived label: {result['derived']['label']}; reason: {result['derived']['reason_code']}", "",
                  *([f"Validation detail: {result['detail']}", ""] if result.get("detail") else []),
                  f"Package coverage: {package['coverage']}; website evidence: "
                  f"{package['audit'].get('website_evidence_tokens_exact', package['audit'].get('website_evidence_token_upper_bound')):,} "
                  f"{'verified input tokens' if 'website_evidence_tokens_exact' in package['audit'] else 'legacy UTF-8 bytes'}", ""]
        if profile:
            lines += [f"AI-active: {profile['ai_active']}", "", f"AI-adjacent: {profile['ai_adjacent']}", "",
                      f"Attribution: {profile['attribution']}; adequacy: {profile['evidence_adequacy']}", "",
                      f"Summary: {profile['ai_summary']}", "", f"Roles: {profile['roles']}", "",
                      f"Techniques: {profile['technologies']}", "",
                      "Graph triplets:", ""]
            lines += [f"- {x['source']} -> {x['type']} -> {x['target']} ({x['status']}; {x['date_text']})"
                      for x in profile["relationships"]] or ["- None"]
            lines += ["", "Quotation checks (cited-page substring only, not semantic verification):", ""]
            for evidence in profile["evidence"]:
                page_id = evidence["page_id"]
                diagnosis, alternatives = quote_diagnostic(package, evidence)
                suffix = f" (exact on page_id {', '.join(alternatives)})" if alternatives else ""
                lines.append(f"- {evidence['id']}: {diagnosis}{suffix} "
                             f"[{page_id}]({urls.get(page_id, '')}): {evidence['quote']!r}")
            lines += ["", f"Unmapped findings: {profile['unmapped_findings']}", ""]
        lines += ["Manual review - correctness: ", "", "Manual review - attribution: ", "",
                  "Manual review - missed findings: ", "", "Manual review - taxonomy changes: ", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "prepare-rerun", "dry-run", "export-batch", "run", "resume", "revalidate", "report"):
        p = sub.add_parser(name)
        p.add_argument("--directory", type=Path, help="Pilot artifact directory")
        if name == "prepare-rerun":
            p.add_argument("--source-directory", type=Path,
                           default=Path(DEFAULT_OUTPUT))
            p.add_argument("--contract-id", choices=["company_ai_profile_v2", "company_ai_profile_v3",
                                                     "company_ai_profile_v4"],
                           default="company_ai_profile_v2")
            p.add_argument("--company-id", action="append", dest="company_ids")
        if name == "export-batch":
            p.add_argument("--output", type=Path, required=True)
        if name == "revalidate":
            p.add_argument("--output", type=Path, help="New directory for offline revalidated results")
        if name == "report":
            p.add_argument("--results-directory", type=Path,
                           help="Read separate revalidated results rather than original results")
        if name == "resume":
            p.add_argument("--retry-status", action="append", default=[],
                           choices=["api_failure", "refusal", "incomplete_output", "invalid_schema", "failed_exact_quote", "invalid_references_or_contract"])
            p.add_argument("--retry-ambiguous", action="store_true")
    args = parser.parse_args()
    root = find_project_root()
    directory = args.directory or root / DEFAULT_OUTPUT
    if args.command == "prepare":
        built = prepare(root, directory)
        print(f"Prepared {len(built['manifest'])} companies at {directory}; token-count API only, no labeling calls")
    elif args.command == "prepare-rerun":
        directory = args.directory or root / (PILOT_FIX_OUTPUT if args.contract_id == "company_ai_profile_v4" else
                                              SCOPED_OUTPUT if args.contract_id == "company_ai_profile_v3" else
                                              REVISED_OUTPUT)
        source = args.source_directory
        if not source.is_absolute():
            source = root / source
        summary = prepare_rerun(root, source, directory, contract_id=args.contract_id,
                                company_ids=args.company_ids)
        print(f"Prepared {summary['companies']} frozen-package requests at {directory}; "
              "token-count API only, no labeling calls")
    elif args.command == "dry-run":
        dry_run(root, directory)
    elif args.command == "export-batch":
        export_batch(root, directory, args.output)
    elif args.command == "run":
        run(root, directory)
    elif args.command == "resume":
        run(root, directory, resume=True, retry_status=set(args.retry_status), retry_ambiguous=args.retry_ambiguous)
    elif args.command == "revalidate":
        output = args.output or directory / "revalidation" / QUOTE_VALIDATION_VERSION
        summary = revalidate_saved(root, directory, output)
        print(f"Revalidated {summary['companies']} saved responses at {output}; no API calls")
    else:
        report(root, directory, results_directory=args.results_directory)


if __name__ == "__main__":
    main()
