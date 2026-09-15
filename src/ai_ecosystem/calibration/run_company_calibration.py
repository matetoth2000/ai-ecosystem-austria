"""Run blinded, company-level OpenAI calibration and lexical signal discovery."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ai_ecosystem.calibration.build_company_evidence_packages import render_blind_markdown, validate_blind_payload
from ai_ecosystem.calibration.config import CalibrationConfig, load_calibration_config
from ai_ecosystem.db.init_db import find_project_root, init_database, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.validation.config import load_openai_api_key
from ai_ecosystem.validation.run_retention_validation import create_openai_client, get_output_text


LABELS = ("KEEP", "UNKNOWN", "EXCLUDE")
REASONING_EFFORTS = ("none", "low", "medium")
SIGNAL_TYPES = ("positive_ai", "ai_adjacent", "false_positive", "third_party_ai", "generic_marketing", "conventional_activity", "useful_context_pattern")
SUBJECTS = ("focal_company", "customer", "partner", "supplier", "employee", "collaborator", "third_party", "unclear")
STRENGTHS = ("weak", "moderate", "strong")
RECOMMENDATIONS = ("consider_positive_keyword", "consider_boost", "consider_query_pattern", "context_only", "consider_negative_or_downweight", "do_not_use")


@dataclass(frozen=True)
class CalibrationInput:
    company_id: str
    company_name: str | None
    domain: str | None
    shared_domain: bool
    json_path: Path
    markdown_path: Path
    payload: dict[str, Any]
    content: str


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def response_schema() -> dict[str, Any]:
    signal = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "phrase": {"type": "string"}, "normalized_phrase": {"type": "string"},
            "signal_type": {"type": "string", "enum": list(SIGNAL_TYPES)},
            "page_id": {"type": "string"}, "url": {"type": "string"},
            "subject": {"type": "string", "enum": list(SUBJECTS)},
            "short_context": {"type": "string"}, "strength": {"type": "string", "enum": list(STRENGTHS)},
            "scorer_recommendation": {"type": "string", "enum": list(RECOMMENDATIONS)},
            "explanation": {"type": "string"},
        },
        "required": ["phrase", "normalized_phrase", "signal_type", "page_id", "url", "subject", "short_context", "strength", "scorer_recommendation", "explanation"],
    }
    evidence = {
        "type": "object", "additionalProperties": False,
        "properties": {"page_id": {"type": "string"}, "url": {"type": "string"}, "evidence_type": {"type": "string"}, "subject": {"type": "string", "enum": list(SUBJECTS)}, "quote": {"type": "string"}, "interpretation": {"type": "string"}},
        "required": ["page_id", "url", "evidence_type", "subject", "quote", "interpretation"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "label": {"type": "string", "enum": list(LABELS)}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "company_activity_summary": {"type": "string"}, "ai_relevance_summary": {"type": "string"}, "primary_reason": {"type": "string"},
            "attribution_assessment": {"type": "object", "additionalProperties": False, "properties": {"focal_company_identified": {"type": "boolean"}, "domain_attribution_reliable": {"type": "boolean"}, "shared_domain_issue": {"type": "boolean"}, "third_party_content_issue": {"type": "boolean"}, "notes": {"type": "string"}}, "required": ["focal_company_identified", "domain_attribution_reliable", "shared_domain_issue", "third_party_content_issue", "notes"]},
            "strongest_supporting_evidence": {"type": "array", "items": evidence},
            "ai_evidence": {"type": "object", "additionalProperties": False, "properties": {"explicit_ai": {"type": "boolean"}, "ai_adjacent": {"type": "boolean"}, "company_attributed": {"type": "boolean"}, "strength": {"type": "string", "enum": ["none", "weak", "moderate", "strong"]}, "capabilities": {"type": "array", "items": {"type": "string"}}}, "required": ["explicit_ai", "ai_adjacent", "company_attributed", "strength", "capabilities"]},
            "exclusion_evidence": {"type": "object", "additionalProperties": False, "properties": {"activity_clear": {"type": "boolean"}, "conventional_activity": {"type": "boolean"}, "nontechnical_activity": {"type": "boolean"}, "sufficient_evidence_for_exclusion": {"type": "boolean"}, "description": {"type": "string"}}, "required": ["activity_clear", "conventional_activity", "nontechnical_activity", "sufficient_evidence_for_exclusion", "description"]},
            "uncertainty": {"type": "object", "additionalProperties": False, "properties": {"present": {"type": "boolean"}, "reasons": {"type": "array", "items": {"type": "string"}}}, "required": ["present", "reasons"]},
            "lexical_signals": {"type": "array", "items": signal},
        },
        "required": ["label", "confidence", "company_activity_summary", "ai_relevance_summary", "primary_reason", "attribution_assessment", "strongest_supporting_evidence", "ai_evidence", "exclusion_evidence", "uncertainty", "lexical_signals"],
    }


def response_format() -> dict[str, Any]:
    return {"format": {"type": "json_schema", "name": "company_ai_calibration", "strict": True, "schema": response_schema()}}


def load_prompt(project_root: Path) -> tuple[str, str, str, str]:
    path = project_root / "src" / "ai_ecosystem" / "calibration" / "prompts" / "company_ai_calibration_v1.txt"
    text = path.read_text(encoding="utf-8")
    head, _, body = text.partition("\n\n")
    metadata = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    prompt_id, version = metadata.get("prompt_id"), metadata.get("prompt_version")
    if not prompt_id or not version:
        raise ValueError("Calibration prompt must declare prompt_id and prompt_version")
    return prompt_id, version, body, hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_model_output(raw_text: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Model output was not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("Model output must be an object")
    missing = set(response_schema()["required"]) - set(data)
    if missing:
        raise ValueError(f"Model output missing required fields: {sorted(missing)}")
    if data["label"] not in LABELS or not isinstance(data["confidence"], int | float) or not 0 <= data["confidence"] <= 1:
        raise ValueError("Model label or confidence is invalid")
    for signal in data["lexical_signals"]:
        if not isinstance(signal, dict) or signal.get("signal_type") not in SIGNAL_TYPES or signal.get("subject") not in SUBJECTS or signal.get("strength") not in STRENGTHS or signal.get("scorer_recommendation") not in RECOMMENDATIONS:
            raise ValueError("Lexical signal has invalid enum values")
    return data


def verify_grounding(parsed: dict[str, Any], content: str) -> dict[str, Any]:
    """Mark quoted and lexical strings by literal occurrence in blind input."""
    lowered = content.casefold()
    verification: dict[str, Any] = {"supporting_evidence": [], "lexical_signals": []}
    for item in parsed.get("strongest_supporting_evidence", []):
        quote = str(item.get("quote", "")).strip()
        verification["supporting_evidence"].append({"page_id": item.get("page_id"), "quote": quote, "verified": bool(quote and quote.casefold() in lowered)})
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for signal in parsed.get("lexical_signals", []):
        phrase, context = str(signal.get("phrase", "")).strip(), str(signal.get("short_context", "")).strip()
        key = (str(signal.get("signal_type")), phrase.casefold(), str(signal.get("page_id")))
        if not phrase or key in seen:
            continue
        seen.add(key)
        verification["lexical_signals"].append({"phrase": phrase, "page_id": signal.get("page_id"), "phrase_verified": phrase.casefold() in lowered, "context_verified": bool(context and context.casefold() in lowered)})
        deduplicated.append(signal)
    parsed["lexical_signals"] = deduplicated
    return verification


def load_inputs(package_dir: Path, company_ids: Iterable[str] = (), company_limit: int | None = None, smoke: bool = False) -> tuple[dict[str, Any], list[CalibrationInput]]:
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("blind_inputs_contain_no_scoring_information"):
        raise ValueError("Package manifest does not assert blind scoring separation")
    requested = set(company_ids)
    inputs: list[CalibrationInput] = []
    for json_path in sorted((package_dir / "blind_inputs").glob("*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if payload.get("blind_to_scoring") is not True:
            raise ValueError(f"Blind scoring assertion missing: {json_path}")
        markdown_path = json_path.with_suffix(".md")
        content = markdown_path.read_text(encoding="utf-8")
        validate_blind_payload(payload, content)
        focal = payload.get("focal_company", {})
        company_id = str(focal.get("company_id", ""))
        if requested and company_id not in requested:
            continue
        inputs.append(CalibrationInput(company_id, focal.get("company_name"), focal.get("normalized_domain"), bool(focal.get("shared_domain")), json_path, markdown_path, payload, content))
    if smoke:
        # Neutral strata only: content/page counts and shared-domain metadata, never scores.
        groups = [
            lambda item: int(item.payload.get("evidence_availability", {}).get("usable_page_count", 0)) == 0,
            lambda item: item.shared_domain,
            lambda item: 0 < int(item.payload.get("evidence_availability", {}).get("usable_page_count", 0)) <= 2,
            lambda item: int(item.payload.get("evidence_availability", {}).get("usable_page_count", 0)) >= 10,
            # These use only the blind source material, never scorer output.
            lambda item: any(term in item.content.casefold() for term in ("machine learning", "artificial intelligence", "künstliche intelligenz", "generative ai")),
            lambda item: any(marker in item.content.casefold() for marker in ("news", "presse", "press release", "/news", "/blog")),
        ]
        selected: list[CalibrationInput] = []
        candidates = [[item for item in inputs if predicate(item)] for predicate in groups]
        while len(selected) < min(10, len(inputs)) and any(candidates):
            for group in candidates:
                while group and group[0] in selected:
                    group.pop(0)
                if group and len(selected) < min(10, len(inputs)):
                    selected.append(group.pop(0))
        selected.extend(item for item in inputs if item not in selected)
        inputs = selected[: min(10, len(selected))]
    if company_limit is not None:
        inputs = inputs[:company_limit]
    return manifest, inputs


def estimate_cost(config: CalibrationConfig, model: str, usage: dict[str, int | None]) -> float | None:
    pricing = config.openai.get("pricing", {}).get(model)
    if not isinstance(pricing, dict):
        return None
    return round(((usage.get("input_tokens") or 0) * float(pricing["input_per_million"]) + (usage.get("output_tokens") or 0) * float(pricing["output_per_million"])) / 1_000_000, 6)


def usage_from_response(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None) or (response.get("usage") if isinstance(response, dict) else None) or {}
    def value(name: str) -> int | None:
        raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(raw) if raw is not None else None
    details = usage.get("output_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "output_tokens_details", None)
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else getattr(details, "reasoning_tokens", None)
    return {"input_tokens": value("input_tokens"), "output_tokens": value("output_tokens"), "reasoning_tokens": int(reasoning) if reasoning is not None else None, "total_tokens": value("total_tokens")}


def response_as_json(response: Any, raw_text: str) -> str:
    if hasattr(response, "model_dump"):
        return json_dump(response.model_dump())
    if isinstance(response, dict):
        return json_dump(response)
    return json_dump({"response_id": getattr(response, "id", None), "model": getattr(response, "model", None), "raw_output_text": raw_text})


def call_one(client: Any, item: CalibrationInput, *, prompt: str, model: str, effort: str, config: CalibrationConfig, retries: int) -> dict[str, Any]:
    started = utc_now()
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            response = client.responses.create(model=model, reasoning={"effort": effort}, input=[{"role": "system", "content": prompt}, {"role": "user", "content": item.content}], text=response_format())
            raw_text = get_output_text(response)
            parsed = validate_model_output(raw_text)
            verification = verify_grounding(parsed, item.content)
            usage = usage_from_response(response)
            return {"status": "success", "company_id": item.company_id, "company_name": item.company_name, "domain": item.domain, "shared_domain": item.shared_domain, "parsed": parsed, "grounding_verification": verification, "raw_response_json": response_as_json(response, raw_text), "response_id": getattr(response, "id", None), "usage": usage, "estimated_cost_usd": estimate_cost(config, model, usage), "started_at": started, "completed_at": utc_now(), "error_message": None}
        except Exception as error:  # Page failures should not abort the experiment.
            last_error = str(error)
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    return {"status": "failed", "company_id": item.company_id, "company_name": item.company_name, "domain": item.domain, "shared_domain": item.shared_domain, "parsed": None, "grounding_verification": {}, "raw_response_json": None, "response_id": None, "usage": {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None}, "estimated_cost_usd": None, "started_at": started, "completed_at": utc_now(), "error_message": last_error}


def insert_result(connection: sqlite3.Connection, run_id: str, result: dict[str, Any]) -> None:
    parsed = result.get("parsed") or {}
    usage = result["usage"]
    connection.execute(
        """INSERT INTO calibration_results (calibration_run_id, company_id, domain, label, confidence, company_activity_summary, ai_relevance_summary, primary_reason, attribution_json, supporting_evidence_json, ai_evidence_json, exclusion_evidence_json, uncertainty_json, lexical_signals_json, raw_response_json, parsed_response_json, input_tokens, output_tokens, reasoning_tokens, total_tokens, estimated_cost_usd, response_id, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(calibration_run_id, company_id) DO UPDATE SET label=excluded.label, confidence=excluded.confidence, company_activity_summary=excluded.company_activity_summary, ai_relevance_summary=excluded.ai_relevance_summary, primary_reason=excluded.primary_reason, attribution_json=excluded.attribution_json, supporting_evidence_json=excluded.supporting_evidence_json, ai_evidence_json=excluded.ai_evidence_json, exclusion_evidence_json=excluded.exclusion_evidence_json, uncertainty_json=excluded.uncertainty_json, lexical_signals_json=excluded.lexical_signals_json, raw_response_json=excluded.raw_response_json, parsed_response_json=excluded.parsed_response_json, input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, reasoning_tokens=excluded.reasoning_tokens, total_tokens=excluded.total_tokens, estimated_cost_usd=excluded.estimated_cost_usd, response_id=excluded.response_id, status=excluded.status, error_message=excluded.error_message""",
        (run_id, result["company_id"], result["domain"], parsed.get("label"), parsed.get("confidence"), parsed.get("company_activity_summary"), parsed.get("ai_relevance_summary"), parsed.get("primary_reason"), json_dump(parsed.get("attribution_assessment")) if parsed else None, json_dump({"evidence": parsed.get("strongest_supporting_evidence", []), "grounding_verification": result.get("grounding_verification", {})}) if parsed else None, json_dump(parsed.get("ai_evidence")) if parsed else None, json_dump(parsed.get("exclusion_evidence")) if parsed else None, json_dump(parsed.get("uncertainty")) if parsed else None, json_dump({"signals": parsed.get("lexical_signals", []), "grounding_verification": result.get("grounding_verification", {}).get("lexical_signals", [])}) if parsed else None, result.get("raw_response_json"), json_dump(parsed) if parsed else None, usage.get("input_tokens"), usage.get("output_tokens"), usage.get("reasoning_tokens"), usage.get("total_tokens"), result.get("estimated_cost_usd"), result.get("response_id"), result["status"], result.get("error_message")),
    )


def load_existing(connection: sqlite3.Connection, run_id: str) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT company_id FROM calibration_results WHERE calibration_run_id=? AND status='success'", (run_id,))}


def generate_run_id() -> str:
    return f"calibration_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def write_artifacts(output: Path, run: dict[str, Any], results: list[dict[str, Any]], prompt_text: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "prompt_snapshot.txt").write_text(prompt_text, encoding="utf-8")
    atomic_write_json(output / "results.json", results)
    signal_rows: list[dict[str, Any]] = []
    result_fields = ["company_id", "company_name", "domain", "shared_domain", "label", "confidence", "company_activity_summary", "ai_relevance_summary", "primary_reason", "explicit_ai", "ai_adjacent", "company_attributed_ai", "ai_evidence_strength", "sufficient_evidence_for_exclusion", "attribution_reliable", "third_party_content_issue", "uncertainty_present", "lexical_signal_count", *[f"{kind}_signal_count" for kind in SIGNAL_TYPES], "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd", "status"]
    rows: list[dict[str, Any]] = []
    for result in results:
        parsed = result.get("parsed") or {}
        ai = parsed.get("ai_evidence", {})
        attribution = parsed.get("attribution_assessment", {})
        exclusion = parsed.get("exclusion_evidence", {})
        uncertainty = parsed.get("uncertainty", {})
        signals = parsed.get("lexical_signals", [])
        counts = Counter(signal.get("signal_type") for signal in signals)
        rows.append({"company_id": result["company_id"], "company_name": result.get("company_name"), "domain": result.get("domain"), "shared_domain": result.get("shared_domain"), "label": parsed.get("label"), "confidence": parsed.get("confidence"), "company_activity_summary": parsed.get("company_activity_summary"), "ai_relevance_summary": parsed.get("ai_relevance_summary"), "primary_reason": parsed.get("primary_reason"), "explicit_ai": ai.get("explicit_ai"), "ai_adjacent": ai.get("ai_adjacent"), "company_attributed_ai": ai.get("company_attributed"), "ai_evidence_strength": ai.get("strength"), "sufficient_evidence_for_exclusion": exclusion.get("sufficient_evidence_for_exclusion"), "attribution_reliable": attribution.get("domain_attribution_reliable"), "third_party_content_issue": attribution.get("third_party_content_issue"), "uncertainty_present": uncertainty.get("present"), "lexical_signal_count": len(signals), **{f"{kind}_signal_count": counts.get(kind, 0) for kind in SIGNAL_TYPES}, **result.get("usage", {}), "estimated_cost_usd": result.get("estimated_cost_usd"), "status": result.get("status")})
        verification_by_phrase = {entry.get("phrase"): entry for entry in result.get("grounding_verification", {}).get("lexical_signals", [])}
        for signal in signals:
            verification = verification_by_phrase.get(signal.get("phrase"), {})
            signal_rows.append({"calibration_run_id": run["calibration_run_id"], "company_id": result["company_id"], "company_label": parsed.get("label"), "company_confidence": parsed.get("confidence"), **signal, "phrase_verified": verification.get("phrase_verified"), "context_verified": verification.get("context_verified")})
    for filename, fields, data in (("results.csv", result_fields, rows), ("lexical_signals.csv", ["calibration_run_id", "company_id", "company_label", "company_confidence", "phrase", "normalized_phrase", "signal_type", "page_id", "url", "subject", "short_context", "strength", "scorer_recommendation", "explanation", "phrase_verified", "context_verified"], signal_rows)):
        with (output / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(data)
    failed = [row for row in results if row.get("status") != "success"]
    if failed:
        with (output / "failures.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["company_id", "company_name", "domain", "error_message", "status"])
            writer.writeheader(); writer.writerows(failed)
    run["lexical_signal_counts"] = dict(Counter(signal["signal_type"] for signal in signal_rows))
    run["output_paths"] = {"results_json": "results.json", "results_csv": "results.csv", "lexical_signals_csv": "lexical_signals.csv", "prompt_snapshot": "prompt_snapshot.txt"}
    atomic_write_json(output / "run_summary.json", run)


def run_company_calibration(*, package_run_id: str = "company_calibration_evidence_v1_pilot_v0_2", model: str | None = None, reasoning_effort: str | None = None, company_ids: Iterable[str] = (), company_limit: int | None = None, resume: bool = False, force_refresh: bool = False, parallelism: int = 1, progress_every: int = 10, output_dir: Path | None = None, calibration_run_id: str | None = None, smoke: bool = False, project_root: Path | None = None, client: Any | None = None) -> dict[str, Any]:
    root = project_root or find_project_root(); config = load_calibration_config(root)
    chosen_model = model or str(config.openai.get("default_model", "gpt-5.6-luna")); effort = reasoning_effort or str(config.openai.get("default_reasoning_effort", "low"))
    if effort not in REASONING_EFFORTS: raise ValueError(f"Unsupported reasoning effort: {effort}")
    package_dir = resolve_project_path(root, f"data/interim/calibration/company_evidence/{package_run_id}")
    manifest, items = load_inputs(package_dir, company_ids, company_limit, smoke)
    _, all_package_inputs = load_inputs(package_dir)
    package_input_by_company = {item.company_id: item for item in all_package_inputs}
    prompt_id, prompt_version, prompt, prompt_hash = load_prompt(root)
    init_database(root); db_path = resolve_database_path(root, load_config(root))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if resume and calibration_run_id is None:
            row = connection.execute("SELECT calibration_run_id FROM calibration_runs WHERE package_run_id=? AND model=? AND reasoning_effort=? AND prompt_hash=? ORDER BY created_at DESC LIMIT 1", (package_run_id, chosen_model, effort, prompt_hash)).fetchone()
            calibration_run_id = row[0] if row else None
        run_id = calibration_run_id or generate_run_id()
        existing_run = connection.execute("SELECT calibration_run_id, selected_count FROM calibration_runs WHERE calibration_run_id=?", (run_id,)).fetchone()
        out = Path(output_dir) if output_dir is not None else (root / "data" / "interim" / "calibration" / "openai" / run_id)
        if not out.is_absolute(): out = root / out
        if existing_run is None:
            connection.execute("INSERT INTO calibration_runs (calibration_run_id, package_run_id, sample_id, model, reasoning_effort, prompt_id, prompt_version, prompt_hash, status, started_at, selected_count, run_config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)", (run_id, package_run_id, manifest.get("sample_id"), chosen_model, effort, prompt_id, prompt_version, prompt_hash, utc_now(), len(items), json_dump({"smoke": smoke, "parallelism": parallelism, "company_limit": company_limit})))
        elif not resume and not force_refresh:
            raise FileExistsError(f"Calibration run already exists: {run_id}. Use --resume or --force-refresh.")
        expected_selected_count = len(items) if existing_run is None else int(existing_run["selected_count"] or len(items))
        existing = set() if force_refresh else (load_existing(connection, run_id) if resume else set())
        connection.commit()
    pending = [item for item in items if item.company_id not in existing]
    if client is None:
        client = create_openai_client(load_openai_api_key(root), int(config.openai.get("request_timeout_seconds", 120)))
    retries = int(config.openai.get("retry_count", 2)); started = time.monotonic(); results: list[dict[str, Any]] = []
    def work(item: CalibrationInput) -> dict[str, Any]: return call_one(client, item, prompt=prompt, model=chosen_model, effort=effort, config=config, retries=retries)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if parallelism <= 1:
            iterator = ((item, work(item)) for item in pending)
            for index, (item, result) in enumerate(iterator, start=1):
                insert_result(connection, run_id, result); connection.commit(); results.append(result)
                if index % progress_every == 0 or index == len(pending): print(f"Calibration {index}/{len(pending)}: {item.company_id} {result['status']}", flush=True)
        else:
            with ThreadPoolExecutor(max_workers=parallelism) as executor:
                futures = {executor.submit(work, item): item for item in pending}
                for index, future in enumerate(as_completed(futures), start=1):
                    item, result = futures[future], future.result(); insert_result(connection, run_id, result); connection.commit(); results.append(result)
                    if index % progress_every == 0 or index == len(pending): print(f"Calibration {index}/{len(pending)}: {item.company_id} {result['status']}", flush=True)
        stored = connection.execute("SELECT * FROM calibration_results WHERE calibration_run_id=? ORDER BY company_id", (run_id,)).fetchall()
        final_results = []
        for row in stored:
            parsed = json.loads(row["parsed_response_json"]) if row["parsed_response_json"] else None
            package_input = package_input_by_company.get(str(row["company_id"]))
            final_results.append({"status": row["status"], "company_id": row["company_id"], "company_name": package_input.company_name if package_input else None, "domain": row["domain"], "shared_domain": package_input.shared_domain if package_input else False, "parsed": parsed, "usage": {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"], "total_tokens": row["total_tokens"]}, "estimated_cost_usd": row["estimated_cost_usd"], "error_message": row["error_message"], "grounding_verification": json.loads(row["supporting_evidence_json"] or "{}").get("grounding_verification", {})})
        counts = Counter((item.get("parsed") or {}).get("label") for item in final_results if item["status"] == "success")
        usage = {key: sum((item["usage"].get(key) or 0) for item in final_results) for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")}
        result_count = len(final_results)
        if result_count < expected_selected_count:
            final_status = "running"
        else:
            final_status = "completed_with_failures" if any(item["status"] != "success" for item in final_results) else "completed"
        summary = {"calibration_run_id": run_id, "package_run_id": package_run_id, "sample_id": manifest.get("sample_id"), "model": chosen_model, "reasoning_effort": effort, "prompt_id": prompt_id, "prompt_version": prompt_version, "prompt_hash": prompt_hash, "selected_count": expected_selected_count, "completed_count": sum(item["status"] == "success" for item in final_results), "failed_count": sum(item["status"] != "success" for item in final_results), "keep_count": counts["KEEP"], "unknown_count": counts["UNKNOWN"], "exclude_count": counts["EXCLUDE"], **usage, "estimated_cost_usd": estimate_cost(config, chosen_model, usage), "runtime_seconds": round(time.monotonic() - started, 3), "status": final_status}
        connection.execute("UPDATE calibration_runs SET status=?, completed_at=?, completed_count=?, failed_count=?, keep_count=?, unknown_count=?, exclude_count=?, input_tokens=?, output_tokens=?, reasoning_tokens=?, total_tokens=?, estimated_cost_usd=?, runtime_seconds=? WHERE calibration_run_id=?", (summary["status"], utc_now(), summary["completed_count"], summary["failed_count"], summary["keep_count"], summary["unknown_count"], summary["exclude_count"], summary["input_tokens"], summary["output_tokens"], summary["reasoning_tokens"], summary["total_tokens"], summary["estimated_cost_usd"], summary["runtime_seconds"], run_id)); connection.commit()
    write_artifacts(out, summary, final_results, prompt)
    return summary


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0: raise argparse.ArgumentTypeError("must be positive")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scorer-blind company-level OpenAI calibration.")
    parser.add_argument("--package-run-id", default="company_calibration_evidence_v1_pilot_v0_2"); parser.add_argument("--model"); parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS); parser.add_argument("--company-id", action="append", default=[]); parser.add_argument("--company-limit", type=positive_int); parser.add_argument("--resume", action="store_true"); parser.add_argument("--force-refresh", action="store_true"); parser.add_argument("--parallelism", type=positive_int, default=1); parser.add_argument("--progress-every", type=positive_int, default=10); parser.add_argument("--output-dir", type=Path); parser.add_argument("--calibration-run-id"); parser.add_argument("--smoke", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    summary = run_company_calibration(package_run_id=args.package_run_id, model=args.model, reasoning_effort=args.reasoning_effort, company_ids=args.company_id, company_limit=args.company_limit, resume=args.resume, force_refresh=args.force_refresh, parallelism=args.parallelism, progress_every=args.progress_every, output_dir=args.output_dir, calibration_run_id=args.calibration_run_id, smoke=args.smoke)
    print(json_dump(summary))


if __name__ == "__main__": main()
