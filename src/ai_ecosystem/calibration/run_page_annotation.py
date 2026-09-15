"""Build, submit, inspect, ingest, or synchronously run page annotations."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ai_ecosystem.calibration.page_annotation_contract import (
    AnnotationItem,
    PageAnnotationConfig,
    build_request_records,
    canonical_json,
    estimate_cost,
    load_annotation_items,
    load_page_annotation_config,
    load_prompt,
    parse_annotation,
    request_body,
    response_text_config,
    sha256_text,
    usage_from_response,
    validate_batch_lines,
)
from ai_ecosystem.db.init_db import find_project_root
from ai_ecosystem.validation.config import load_openai_api_key
from ai_ecosystem.validation.run_retention_validation import create_openai_client, get_output_text


TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    records = list(values)
    text = "".join(canonical_json(value) + "\n" for value in records)
    _atomic_text(path, text)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        values.append(value)
    return values


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    fields = (
        "id",
        "object",
        "endpoint",
        "input_file_id",
        "completion_window",
        "status",
        "output_file_id",
        "error_file_id",
        "created_at",
        "in_progress_at",
        "finalizing_at",
        "completed_at",
        "failed_at",
        "expired_at",
        "cancelled_at",
        "request_counts",
        "metadata",
    )
    return {name: getattr(value, name, None) for name in fields}


def _raw_response_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return response
    return {"id": getattr(response, "id", None), "output_text": get_output_text(response)}


def _run_directory(project_root: Path, config: PageAnnotationConfig, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must be 1-128 path-safe letters, numbers, dots, underscores, or hyphens")
    root = Path(config.output_root)
    if not root.is_absolute():
        root = project_root / root
    return root / run_id


def _write_snapshots(
    run_dir: Path,
    *,
    config: PageAnnotationConfig,
    prompt_id: str,
    prompt_version: str,
    prompt: str,
    prompt_hash: str,
    sample_manifest: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    schema = response_text_config()["format"]["schema"]
    model_configuration = {
        "model": config.model,
        "reasoning": {"effort": config.reasoning_effort},
        "response_format": response_text_config(),
        "store": False,
    }
    _atomic_text(run_dir / "prompt_snapshot.txt", prompt + "\n")
    _atomic_json(run_dir / "schema_snapshot.json", schema)
    metadata = {
        "run_id": run_dir.name,
        "annotation_id": config.annotation_id,
        "annotation_version": config.annotation_version,
        "sample_id": config.sample_id,
        "sample_manifest_sha256": sha256_text(canonical_json(sample_manifest)),
        "mode": mode,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "max_markdown_chars": config.max_markdown_chars,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_hash,
        "schema_sha256": sha256_text(canonical_json(schema)),
        "model_configuration_sha256": sha256_text(canonical_json(model_configuration)),
        "config_sha256": config.config_hash,
        "pricing_version": config.pricing_version,
        "created_at": utc_now(),
        "status": "prepared",
    }
    _atomic_json(run_dir / "run_metadata.json", metadata)
    return metadata


def prepare_run(
    *,
    project_root: Path,
    run_id: str,
    mode: str,
    include_hidden_repeats: bool,
    max_items: int | None = None,
    config_path: Path | None = None,
) -> tuple[Path, PageAnnotationConfig, list[AnnotationItem], list[dict[str, Any]], list[dict[str, Any]]]:
    config = load_page_annotation_config(project_root, config_path)
    sample_manifest, items = load_annotation_items(project_root, config)
    if max_items is not None:
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        items = items[:max_items]
    prompt_id, prompt_version, prompt, prompt_hash = load_prompt(project_root)
    if prompt_id != config.annotation_id or prompt_version != config.annotation_version:
        raise ValueError("Prompt identity does not match page annotation configuration")
    run_dir = _run_directory(project_root, config, run_id)
    if run_dir.exists():
        raise FileExistsError(f"Annotation run already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    batch_lines, index = build_request_records(
        items,
        prompt,
        config,
        include_hidden_repeats=include_hidden_repeats,
    )
    validate_batch_lines(batch_lines, index)
    metadata = _write_snapshots(
        run_dir,
        config=config,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt=prompt,
        prompt_hash=prompt_hash,
        sample_manifest=sample_manifest,
        mode=mode,
    )
    source_config = config_path or project_root / "config" / "page_annotation.yaml"
    shutil.copyfile(source_config, run_dir / "config_snapshot.yaml")
    model_inputs = []
    by_id = {item.annotation_item_id: item for item in items}
    for row in index:
        item = by_id[row["annotation_item_id"]]
        model_inputs.append({"custom_id": row["custom_id"], **item.model_input})
    _atomic_jsonl(run_dir / "model_inputs.jsonl", model_inputs)
    _atomic_jsonl(run_dir / "request_index.jsonl", index)
    if mode == "batch":
        _atomic_jsonl(run_dir / "batch_input.jsonl", batch_lines)
    metadata.update(
        {
            "annotation_item_count": len(items),
            "request_count": len(index),
            "hidden_repeat_count": sum(bool(row["is_hidden_repeat"]) for row in index),
            "batch_input_sha256": sha256_text("".join(canonical_json(line) + "\n" for line in batch_lines)),
        }
    )
    _atomic_json(run_dir / "run_metadata.json", metadata)
    return run_dir, config, items, batch_lines, index


def build_batch(project_root: Path, run_id: str, config_path: Path | None = None) -> dict[str, Any]:
    run_dir, _, _, lines, index = prepare_run(
        project_root=project_root,
        run_id=run_id,
        mode="batch",
        include_hidden_repeats=True,
        config_path=config_path,
    )
    byte_count = (run_dir / "batch_input.jsonl").stat().st_size
    if len(lines) > 50_000 or byte_count > 200 * 1024 * 1024:
        raise ValueError("Batch input exceeds OpenAI Batch limits")
    summary = {
        "run_id": run_id,
        "annotation_items": len(lines) - sum(bool(row["is_hidden_repeat"]) for row in index),
        "requests": len(lines),
        "hidden_repeats": sum(bool(row["is_hidden_repeat"]) for row in index),
        "batch_input_bytes": byte_count,
        "batch_input": str(run_dir / "batch_input.jsonl"),
        "status": "validated",
    }
    _atomic_json(run_dir / "build_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def _load_prepared_batch(
    project_root: Path,
    run_id: str,
    config_path: Path | None = None,
) -> tuple[Path, PageAnnotationConfig, dict[str, Any]]:
    config = load_page_annotation_config(project_root, config_path)
    run_dir = _run_directory(project_root, config, run_id)
    metadata = _read_json(run_dir / "run_metadata.json")
    if metadata.get("mode") != "batch":
        raise ValueError(f"Run is not a Batch run: {run_id}")
    lines = _read_jsonl(run_dir / "batch_input.jsonl")
    index = _read_jsonl(run_dir / "request_index.jsonl")
    validate_batch_lines(lines, index)
    actual_hash = sha256_text("".join(canonical_json(line) + "\n" for line in lines))
    if actual_hash != metadata.get("batch_input_sha256"):
        raise ValueError("Batch input changed after validation")
    return run_dir, config, metadata


def submit_batch(
    project_root: Path,
    run_id: str,
    client: Any | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    run_dir, config, metadata = _load_prepared_batch(project_root, run_id, config_path)
    if (run_dir / "openai_batch.json").exists():
        raise FileExistsError("Batch was already submitted; use status instead")
    client = client or create_openai_client(load_openai_api_key(project_root), config.request_timeout_seconds)
    with (run_dir / "batch_input.jsonl").open("rb") as handle:
        uploaded = client.files.create(file=handle, purpose="batch")
    uploaded_dict = _as_dict(uploaded)
    _atomic_json(run_dir / "openai_input_file.json", uploaded_dict)
    batch = client.batches.create(
        input_file_id=str(uploaded_dict["id"]),
        endpoint="/v1/responses",
        completion_window=config.completion_window,
        metadata={
            "run_id": run_id,
            "annotation_version": config.annotation_version,
            "prompt_sha256": str(metadata["prompt_sha256"]),
        },
    )
    batch_dict = _as_dict(batch)
    _atomic_json(run_dir / "openai_batch.json", batch_dict)
    metadata.update({"status": "submitted", "submitted_at": utc_now(), "batch_id": batch_dict.get("id")})
    _atomic_json(run_dir / "run_metadata.json", metadata)
    print(json.dumps({"run_id": run_id, "batch_id": batch_dict.get("id"), "status": batch_dict.get("status")}, indent=2))
    return batch_dict


def check_batch_status(
    project_root: Path,
    run_id: str,
    client: Any | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    run_dir, config, metadata = _load_prepared_batch(project_root, run_id, config_path)
    submitted = _read_json(run_dir / "openai_batch.json")
    client = client or create_openai_client(load_openai_api_key(project_root), config.request_timeout_seconds)
    batch = _as_dict(client.batches.retrieve(str(submitted["id"])))
    _atomic_json(run_dir / "openai_batch.json", batch)
    _atomic_json(run_dir / f"batch_status_{int(time.time())}.json", batch)
    metadata.update({"status": batch.get("status"), "last_status_check_at": utc_now(), "batch_id": batch.get("id")})
    _atomic_json(run_dir / "run_metadata.json", metadata)
    print(json.dumps({"run_id": run_id, "batch_id": batch.get("id"), "status": batch.get("status"), "request_counts": batch.get("request_counts")}, indent=2))
    return batch


def _content_text(content: Any) -> str:
    if hasattr(content, "text"):
        return str(content.text)
    if hasattr(content, "read"):
        value = content.read()
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    if isinstance(content, bytes):
        return content.decode("utf-8")
    return str(content)


def _result_record(
    *,
    index_row: dict[str, Any],
    raw_body: dict[str, Any],
    config: PageAnnotationConfig,
    batch: bool,
    model_input: dict[str, Any],
) -> dict[str, Any]:
    raw_text = get_output_text(raw_body)
    parsed = parse_annotation(raw_text, int(index_row["scraped_page_id"]))
    usage = usage_from_response(raw_body)
    markdown = str(model_input["extracted_markdown"])
    phrase_grounding = []
    for facet_name, facet in parsed["evidence_facets"].items():
        for phrase in facet["supporting_phrases"]:
            phrase_grounding.append(
                {
                    "facet": facet_name,
                    "phrase": phrase,
                    "exact_match": phrase in markdown,
                    "casefold_match": phrase.casefold() in markdown.casefold(),
                }
            )
    return {
        **index_row,
        "status": "success",
        "annotation": parsed,
        "phrase_grounding": phrase_grounding,
        "response_id": raw_body.get("id"),
        "usage": usage,
        "estimated_cost_usd": estimate_cost(config, usage, batch=batch),
        "completed_at": utc_now(),
        "error": None,
    }


def _usage_summary(records: Iterable[dict[str, Any]], config: PageAnnotationConfig, *, batch: bool) -> dict[str, Any]:
    records = list(records)
    totals = {name: sum(int(row.get("usage", {}).get(name, 0) or 0) for row in records) for name in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")}
    return {
        "successful_requests": sum(row.get("status") == "success" for row in records),
        "failed_requests": sum(row.get("status") != "success" for row in records),
        **totals,
        "estimated_cost_usd": estimate_cost(config, totals, batch=batch),
        "pricing_version": config.pricing_version,
        "batch_pricing": batch,
    }


def ingest_batch(
    project_root: Path,
    run_id: str,
    client: Any | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    run_dir, config, metadata = _load_prepared_batch(project_root, run_id, config_path)
    submitted = _read_json(run_dir / "openai_batch.json")
    client = client or create_openai_client(load_openai_api_key(project_root), config.request_timeout_seconds)
    batch = _as_dict(client.batches.retrieve(str(submitted["id"])))
    _atomic_json(run_dir / "openai_batch.json", batch)
    if batch.get("status") not in TERMINAL_BATCH_STATUSES:
        raise RuntimeError(f"Batch is not ready for ingestion: {batch.get('status')}")
    if batch.get("output_file_id"):
        _atomic_text(run_dir / "batch_output.jsonl", _content_text(client.files.content(str(batch["output_file_id"]))))
    if batch.get("error_file_id"):
        _atomic_text(run_dir / "batch_errors.jsonl", _content_text(client.files.content(str(batch["error_file_id"]))))

    index_rows = _read_jsonl(run_dir / "request_index.jsonl")
    index = {row["custom_id"]: row for row in index_rows}
    model_inputs = {
        row["custom_id"]: {key: value for key, value in row.items() if key != "custom_id"}
        for row in _read_jsonl(run_dir / "model_inputs.jsonl")
    }
    records_by_id = {row["custom_id"]: row for row in _read_jsonl(run_dir / "annotations.jsonl")}
    raw_rows = _read_jsonl(run_dir / "batch_output.jsonl") + _read_jsonl(run_dir / "batch_errors.jsonl")
    _atomic_jsonl(run_dir / "raw_batch_results.jsonl", raw_rows)
    for raw in raw_rows:
        custom_id = raw.get("custom_id")
        if custom_id not in index or custom_id in records_by_id:
            continue
        response = raw.get("response") or {}
        try:
            if raw.get("error") or int(response.get("status_code", 0)) != 200:
                raise ValueError(canonical_json(raw.get("error") or response))
            records_by_id[custom_id] = _result_record(
                index_row=index[custom_id],
                raw_body=response["body"],
                config=config,
                batch=True,
                model_input=model_inputs[custom_id],
            )
        except Exception as error:
            records_by_id[custom_id] = {
                **index[custom_id],
                "status": "failed",
                "annotation": None,
                "response_id": response.get("request_id"),
                "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "estimated_cost_usd": 0.0,
                "completed_at": utc_now(),
                "error": str(error),
            }
    if batch.get("status") in TERMINAL_BATCH_STATUSES:
        for custom_id, row in index.items():
            if custom_id not in records_by_id:
                records_by_id[custom_id] = {
                    **row,
                    "status": "failed",
                    "annotation": None,
                    "response_id": None,
                    "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "estimated_cost_usd": 0.0,
                    "completed_at": utc_now(),
                    "error": "No Batch output or error record returned",
                }
    records = [records_by_id[row["custom_id"]] for row in index_rows if row["custom_id"] in records_by_id]
    _atomic_jsonl(run_dir / "annotations.jsonl", records)
    failures = [row for row in records if row["status"] != "success"]
    _atomic_jsonl(run_dir / "failures.jsonl", failures)
    summary = _usage_summary(records, config, batch=True)
    summary.update({"run_id": run_id, "batch_id": batch.get("id"), "batch_status": batch.get("status"), "expected_requests": len(index_rows), "ingested_requests": len(records)})
    _atomic_json(run_dir / "usage_summary.json", summary)
    metadata.update({"status": "ingested", "ingested_at": utc_now(), "successful_requests": summary["successful_requests"], "failed_requests": summary["failed_requests"]})
    _atomic_json(run_dir / "run_metadata.json", metadata)
    print(json.dumps(summary, indent=2))
    return summary


def retry_failed_requests(
    project_root: Path,
    run_id: str,
    client: Any | None = None,
    sleep: Any = time.sleep,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Synchronously retry only failed requests from an ingested Batch run."""
    run_dir, config, metadata = _load_prepared_batch(project_root, run_id, config_path)
    if metadata.get("status") != "ingested":
        raise ValueError("Batch results must be ingested before failed requests can be retried")
    index_rows = _read_jsonl(run_dir / "request_index.jsonl")
    records_by_id = {row["custom_id"]: row for row in _read_jsonl(run_dir / "annotations.jsonl")}
    failed_index = [row for row in index_rows if records_by_id.get(row["custom_id"], {}).get("status") != "success"]
    if not failed_index:
        return {"run_id": run_id, "retried_requests": 0, "successful_retries": 0, "failed_retries": 0}
    model_inputs = {
        row["custom_id"]: {key: value for key, value in row.items() if key != "custom_id"}
        for row in _read_jsonl(run_dir / "model_inputs.jsonl")
    }
    prompt = (run_dir / "prompt_snapshot.txt").read_text(encoding="utf-8").strip()
    client = client or create_openai_client(load_openai_api_key(project_root), config.request_timeout_seconds)
    raw_dir = run_dir / "retry_raw_responses"
    raw_dir.mkdir(exist_ok=True)
    successful_retries = 0
    for index_row in failed_index:
        custom_id = str(index_row["custom_id"])
        model_input = model_inputs[custom_id]
        item = AnnotationItem(
            annotation_item_id=str(index_row["annotation_item_id"]),
            annotation_rank=int(index_row["annotation_rank"]),
            company_id=str(index_row["company_id"]),
            scraped_page_id=int(index_row["scraped_page_id"]),
            model_input=model_input,
        )
        previous = records_by_id[custom_id]
        last_error: Exception | None = None
        for attempt in range(config.retry_count + 1):
            try:
                response = client.responses.create(**request_body(item, prompt, config))
                raw = _raw_response_dict(response)
                _atomic_json(raw_dir / f"{custom_id}_attempt_{attempt + 1}.json", raw)
                record = _result_record(
                    index_row=index_row,
                    raw_body=raw,
                    config=config,
                    batch=False,
                    model_input=model_input,
                )
                record.update(
                    {
                        "execution_mode": "synchronous_failed_request_retry",
                        "retry_of_error": previous.get("error"),
                        "retry_attempt": attempt + 1,
                    }
                )
                records_by_id[custom_id] = record
                successful_retries += 1
                break
            except Exception as error:
                last_error = error
                if attempt < config.retry_count:
                    sleep(2**attempt)
        else:
            records_by_id[custom_id] = {
                **previous,
                "completed_at": utc_now(),
                "error": f"Retry failed: {last_error}",
                "retry_attempt": config.retry_count + 1,
            }
        ordered = [records_by_id[row["custom_id"]] for row in index_rows]
        _atomic_jsonl(run_dir / "annotations.jsonl", ordered)

    records = [records_by_id[row["custom_id"]] for row in index_rows]
    failures = [row for row in records if row.get("status") != "success"]
    _atomic_jsonl(run_dir / "failures.jsonl", failures)
    previous_summary = _read_json(run_dir / "usage_summary.json")
    batch_usage = {
        name: int(previous_summary.get(name, 0) or 0)
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    }
    retry_records = [row for row in records if row.get("execution_mode") == "synchronous_failed_request_retry"]
    retry_usage = {
        name: sum(int(row.get("usage", {}).get(name, 0) or 0) for row in retry_records)
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    }
    summary = {
        "run_id": run_id,
        "retried_requests": len(failed_index),
        "successful_retries": successful_retries,
        "failed_retries": len(failures),
        "successful_requests": len(records) - len(failures),
        "failed_requests": len(failures),
        "batch_usage": batch_usage,
        "synchronous_retry_usage": retry_usage,
        "estimated_batch_cost_usd": estimate_cost(config, batch_usage, batch=True),
        "estimated_retry_cost_usd": estimate_cost(config, retry_usage, batch=False),
    }
    summary["estimated_total_cost_usd"] = round(
        summary["estimated_batch_cost_usd"] + summary["estimated_retry_cost_usd"], 8
    )
    _atomic_json(run_dir / "usage_summary.json", summary)
    metadata.update(
        {
            "successful_requests": summary["successful_requests"],
            "failed_requests": summary["failed_requests"],
            "failed_request_retry_completed_at": utc_now(),
        }
    )
    _atomic_json(run_dir / "run_metadata.json", metadata)
    print(json.dumps(summary, indent=2))
    return summary


def run_sync(
    *,
    project_root: Path,
    run_id: str,
    max_items: int,
    resume: bool = False,
    client: Any | None = None,
    sleep: Any = time.sleep,
    config_path: Path | None = None,
) -> dict[str, Any]:
    config = load_page_annotation_config(project_root, config_path)
    run_dir = _run_directory(project_root, config, run_id)
    if not run_dir.exists():
        run_dir, config, items, _, index_rows = prepare_run(
            project_root=project_root,
            run_id=run_id,
            mode="sync",
            include_hidden_repeats=False,
            max_items=max_items,
            config_path=config_path,
        )
    elif not resume:
        raise FileExistsError(f"Annotation run already exists: {run_dir}; use --resume")
    else:
        metadata = _read_json(run_dir / "run_metadata.json")
        if metadata.get("mode") != "sync":
            raise ValueError("Cannot resume a non-synchronous run")
        _, all_items = load_annotation_items(project_root, config)
        item_by_id = {item.annotation_item_id: item for item in all_items}
        index_rows = _read_jsonl(run_dir / "request_index.jsonl")
        items = [item_by_id[row["annotation_item_id"]] for row in index_rows]
    prompt = (run_dir / "prompt_snapshot.txt").read_text(encoding="utf-8").strip()
    item_by_id = {item.annotation_item_id: item for item in items}
    records_by_id = {row["custom_id"]: row for row in _read_jsonl(run_dir / "annotations.jsonl")}
    client = client or create_openai_client(load_openai_api_key(project_root), config.request_timeout_seconds)
    raw_dir = run_dir / "raw_responses"
    raw_dir.mkdir(exist_ok=True)
    for index_row in index_rows:
        custom_id = index_row["custom_id"]
        if records_by_id.get(custom_id, {}).get("status") == "success":
            continue
        item = item_by_id[index_row["annotation_item_id"]]
        last_error: Exception | None = None
        for attempt in range(config.retry_count + 1):
            try:
                body = request_body(item, prompt, config)
                response = client.responses.create(**body)
                raw = _raw_response_dict(response)
                _atomic_json(raw_dir / f"{custom_id}.json", raw)
                record = _result_record(
                    index_row=index_row,
                    raw_body=raw,
                    config=config,
                    batch=False,
                    model_input=item.model_input,
                )
                records_by_id[custom_id] = record
                break
            except Exception as error:
                last_error = error
                if attempt < config.retry_count:
                    sleep(min(2**attempt, 8))
        else:
            records_by_id[custom_id] = {
                **index_row,
                "status": "failed",
                "annotation": None,
                "response_id": None,
                "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "estimated_cost_usd": 0.0,
                "completed_at": utc_now(),
                "error": str(last_error),
            }
        records = [records_by_id[row["custom_id"]] for row in index_rows if row["custom_id"] in records_by_id]
        _atomic_jsonl(run_dir / "annotations.jsonl", records)
    records = [records_by_id[row["custom_id"]] for row in index_rows if row["custom_id"] in records_by_id]
    _atomic_jsonl(run_dir / "failures.jsonl", [row for row in records if row["status"] != "success"])
    summary = _usage_summary(records, config, batch=False)
    summary.update({"run_id": run_id, "expected_requests": len(index_rows), "completed_at": utc_now()})
    _atomic_json(run_dir / "usage_summary.json", summary)
    metadata = _read_json(run_dir / "run_metadata.json")
    metadata.update({"status": "completed" if not summary["failed_requests"] else "completed_with_failures", "completed_at": utc_now(), "successful_requests": summary["successful_requests"], "failed_requests": summary["failed_requests"]})
    _atomic_json(run_dir / "run_metadata.json", metadata)
    print(json.dumps(summary, indent=2))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run scorer-v2 page annotation infrastructure")
    parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help="Annotation YAML; defaults to config/page_annotation.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-batch", help="Build and validate Batch JSONL without submitting it")
    build.add_argument("--run-id", required=True)
    submit = subparsers.add_parser("submit-batch", help="Upload a prepared JSONL file and submit the Batch")
    submit.add_argument("--run-id", required=True)
    status = subparsers.add_parser("status", help="Retrieve and persist current Batch status")
    status.add_argument("--run-id", required=True)
    ingest = subparsers.add_parser("ingest-batch", help="Download and idempotently ingest terminal Batch results")
    ingest.add_argument("--run-id", required=True)
    retry = subparsers.add_parser("retry-failures", help="Synchronously retry only failed ingested Batch requests")
    retry.add_argument("--run-id", required=True)
    sync = subparsers.add_parser("run-sync", help="Run a small resumable synchronous annotation job")
    sync.add_argument("--run-id", required=True)
    sync.add_argument("--max-items", type=int, default=3)
    sync.add_argument("--resume", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = (args.project_root or find_project_root()).resolve()
    config_path = args.config
    if config_path and not config_path.is_absolute():
        config_path = root / config_path
    if args.command == "build-batch":
        build_batch(root, args.run_id, config_path)
    elif args.command == "submit-batch":
        submit_batch(root, args.run_id, config_path=config_path)
    elif args.command == "status":
        check_batch_status(root, args.run_id, config_path=config_path)
    elif args.command == "ingest-batch":
        ingest_batch(root, args.run_id, config_path=config_path)
    elif args.command == "retry-failures":
        retry_failed_requests(root, args.run_id, config_path=config_path)
    elif args.command == "run-sync":
        run_sync(
            project_root=root,
            run_id=args.run_id,
            max_items=args.max_items,
            resume=args.resume,
            config_path=config_path,
        )


if __name__ == "__main__":
    main()
