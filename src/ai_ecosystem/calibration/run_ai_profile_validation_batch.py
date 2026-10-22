"""Explicit submit/status/ingest workflow for the finalized AI-profile Batch."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from ai_ecosystem.db.init_db import find_project_root
from ai_ecosystem.validation.config import load_openai_api_key
from .company_ai_profile_contract import derive_label, digest, request_body
from .finalize_ai_profile_validation_batch import FINAL_OUTPUT
from .prepare_ai_profile_validation_batch import (MAX_FILE_BYTES, MAX_REQUESTS, QUEUE_HEADROOM,
                                                   atomic_json)
from .run_company_ai_profile_pilot import parse_response
from .company_modeling_common import file_hash


ACTIVE = {"validating", "in_progress", "finalizing", "cancelling"}
TERMINAL = {"completed", "failed", "expired", "cancelled"}
QUEUE_ALLOWANCE = 1_000_000_000  # User-confirmed on 2026-10-20, not API-verified.


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_run(directory: Path) -> tuple[dict, dict[str, dict], dict[str, dict]]:
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    identity = json.loads((directory / "preparation_identity.json").read_text(encoding="utf-8"))
    if (summary["included"], summary["quarantined"], summary["skipped_no_usable_package"],
            summary["no_evidence"], summary["unresolved"], summary["reviewed_inclusions"]) != (1081, 2, 1, 416, 0, 64):
        raise ValueError("Not the finalized 1,081-request run")
    if summary["submission_blocked"]:
        raise ValueError("Preparation is blocked from submission")
    if (summary["contract_id"], summary["mapping_version"], summary["quote_validation_version"]) != (
            "company_ai_profile_v4", "company_ai_profile_mapping_v4", "quote_edge_whitespace_v2"):
        raise ValueError("Approved contract changed")
    config = json.loads((directory / "config_snapshot.json").read_text(encoding="utf-8"))
    schema = json.loads((directory / "schema_snapshot.json").read_text(encoding="utf-8"))
    prompt = (directory / "prompt_snapshot.txt").read_text(encoding="utf-8")
    if (config["model"], config["reasoning_effort"], config["website_evidence_token_cap"],
            config["max_output_tokens"]) != ("gpt-6-luna", "low", 250000, 8000):
        raise ValueError("Approved request settings changed")
    if digest(schema) != identity["schema_sha256"]:
        raise ValueError("Schema snapshot changed")
    import hashlib
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != identity["prompt_sha256"]:
        raise ValueError("Prompt snapshot changed")
    manifest = {row["company_id"]: row for row in read_jsonl(directory / "company_manifest.jsonl")}
    if len(manifest) != 1084 or Counter(row["status"] for row in manifest.values()) != {
            "included": 1081, "quarantined": 2, "skipped_no_usable_package": 1}:
        raise ValueError("Final company manifest changed")
    records = {}
    for path in sorted((directory / "work").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        cid = record["company_id"]
        line = record["batch_line"]
        body = line["body"]
        if len(body.get("input", [])) != 2:
            raise ValueError(f"Invalid model input messages: {cid}")
        visible = json.loads(body["input"][1]["content"])
        if (cid in records or record["identity_hash"] != digest(identity)
                or digest(line["body"]) != record["request_hash"]
                or visible.get("company_id") != cid or not visible.get("pages")
                or body != request_body(visible, config, prompt, schema)
                or manifest[cid]["status"] != "included"
                or manifest[cid]["split"] != record["split"]
                or line["custom_id"] != f"independent-v1-{cid}-{record['request_hash'][:16]}"
                or line["method"] != "POST" or line["url"] != "/v1/responses"
                or line["body"]["model"] != config["model"]
                or line["body"]["reasoning"] != {"effort": "low"}
                or line["body"]["max_output_tokens"] != 8000):
            raise ValueError(f"Invalid request checkpoint: {path}")
        records[cid] = record
    if len(records) != 1081 or set(records) != {cid for cid, row in manifest.items() if row["status"] == "included"}:
        raise ValueError("Incomplete request checkpoints")
    files = {Path(item["path"]).name: item for item in summary["files"]}
    if len(files) != len(summary["files"]):
        raise ValueError("Duplicate Batch file names")
    seen = set()
    total_tokens = 0
    for name, item in files.items():
        path = directory / name
        if (file_hash(path) != item["sha256"] or path.stat().st_size != item["bytes"]
                or item["bytes"] > MAX_FILE_BYTES or item["requests"] > MAX_REQUESTS):
            raise ValueError(f"Batch file changed or exceeds limits: {name}")
        lines = read_jsonl(path)
        if len(lines) != item["requests"]:
            raise ValueError(f"Batch line count changed: {name}")
        file_tokens = 0
        for line in lines:
            cid = line["custom_id"].split("-")[2]
            if cid not in records or line != records[cid]["batch_line"] or cid in seen:
                raise ValueError(f"Unrecognized or duplicate Batch request: {line['custom_id']}")
            seen.add(cid)
            file_tokens += records[cid]["estimated_input_tokens"]
        if file_tokens != item["estimated_input_tokens"]:
            raise ValueError(f"Batch token estimate changed: {name}")
        total_tokens += file_tokens
    if seen != set(records) or total_tokens != summary["estimated_input_tokens"]:
        raise ValueError("Batch file coverage or token total changed")
    return summary, records, files


def client_for(root: Path):
    from openai import OpenAI
    return OpenAI(api_key=load_openai_api_key(root), timeout=180, max_retries=0)


def remote_batches(client: Any) -> list[dict]:
    page = client.batches.list(limit=100)
    if hasattr(page, "auto_paging_iter"):
        return [as_dict(item) for item in page.auto_paging_iter()]
    results = [as_dict(item) for item in page.data]
    while page.has_more:
        page = client.batches.list(limit=100, after=results[-1]["id"])
        results.extend(as_dict(item) for item in page.data)
    return results


def state_path(directory: Path, name: str) -> Path:
    return directory / "submission" / f"{name}.json"


def expected_metadata(identity: dict, item: dict, name: str) -> dict[str, str]:
    return {"run_hash": digest(identity)[:32], "file_sha": item["sha256"], "file_name": name}


def matching_remote(remote: list[dict], metadata: dict) -> list[dict]:
    return [batch for batch in remote if batch.get("metadata") == metadata]


def bind_remote(path: Path, state: dict, matches: list[dict]) -> bool:
    if len(matches) > 1:
        raise ValueError("Multiple remote Batches match the same file identity")
    if not matches:
        return False
    batch = matches[0]
    if state.get("input_file_id") and batch["input_file_id"] != state["input_file_id"]:
        raise ValueError("Remote Batch input file conflicts with saved upload")
    state.update({"phase": "submitted", "input_file_id": batch["input_file_id"],
                  "batch_id": batch["id"], "remote_status": batch["status"]})
    atomic_json(path, state)
    return True


def submit(root: Path, directory: Path, *, file_name: str | None = None,
           confirm_paid: bool = False, client: Any | None = None) -> list[dict]:
    if not confirm_paid:
        raise ValueError("Paid submission requires --confirm-paid")
    (directory / "submission").mkdir(exist_ok=True)
    with (directory / "submission" / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another local submission is in progress") from exc
        return _submit_locked(root, directory, file_name=file_name,
                              confirm_paid=confirm_paid, client=client)


def _submit_locked(root: Path, directory: Path, *, file_name: str | None,
                   confirm_paid: bool, client: Any | None) -> list[dict]:
    if not confirm_paid:
        raise ValueError("Paid submission requires --confirm-paid")
    summary, _, files = validate_run(directory)
    if summary["queue_headroom_tokens"] >= QUEUE_ALLOWANCE:
        raise ValueError("Estimated queue volume exceeds user-confirmed allowance")
    if file_name is not None and file_name not in files:
        raise ValueError("Unknown finalized Batch file")
    identity = json.loads((directory / "preparation_identity.json").read_text(encoding="utf-8"))
    client = client or client_for(root)
    results = []
    for name in ([file_name] if file_name else files):
        item = files[name]
        path = state_path(directory, name)
        metadata = expected_metadata(identity, item, name)
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "phase": "planned", "metadata": metadata, "input_file_id": None, "batch_id": None}
        if state["metadata"] != metadata:
            raise ValueError("Submission state identity mismatch")
        remote = remote_batches(client)
        matches = matching_remote(remote, metadata)
        if state["phase"] == "submitted" or matches:
            if matches and not state.get("batch_id"):
                bind_remote(path, state, matches)
            raise FileExistsError(f"Already submitted or present remotely: {name}")
        if state["phase"] == "submitting":
            raise RuntimeError(f"Ambiguous prior submission for {name}; use reconcile first")
        if any(batch.get("status") in ACTIVE and (batch.get("metadata") or {}).get("run_hash") != metadata["run_hash"]
               for batch in remote):
            raise RuntimeError("Other active Batch jobs detected; verify available Luna queue before submission")
        if any(batch.get("status") in ACTIVE and (batch.get("metadata") or {}).get("run_hash") == metadata["run_hash"]
               and (batch.get("metadata") or {}).get("file_name") not in files for batch in remote):
            raise RuntimeError("Active Batch with unknown file identity; reconcile before submission")
        queued_ours = sum(int(files[b["metadata"]["file_name"]]["estimated_input_tokens"] * QUEUE_HEADROOM)
                          for b in remote if b.get("status") in ACTIVE and (b.get("metadata") or {}).get("run_hash") == metadata["run_hash"]
                          and b["metadata"].get("file_name") in files)
        if queued_ours + int(item["estimated_input_tokens"] * QUEUE_HEADROOM) >= QUEUE_ALLOWANCE:
            raise ValueError("Insufficient user-confirmed queue headroom")
        if state["phase"] == "planned":
            atomic_json(path, state)
            with (directory / name).open("rb") as handle:
                uploaded = as_dict(client.files.create(file=handle, purpose="batch"))
            state.update({"phase": "uploaded", "input_file_id": uploaded["id"]})
            atomic_json(path, state)
        state["phase"] = "submitting"
        atomic_json(path, state)
        batch = as_dict(client.batches.create(input_file_id=state["input_file_id"], endpoint="/v1/responses",
                                              completion_window="24h", metadata=metadata))
        if batch.get("metadata") != metadata or batch.get("input_file_id") != state["input_file_id"]:
            raise ValueError("Remote Batch identity mismatch; reconcile before retry")
        state.update({"phase": "submitted", "batch_id": batch["id"], "remote_status": batch["status"]})
        atomic_json(path, state)
        results.append({"file": name, "batch_id": batch["id"], "status": batch["status"]})
    return results


def reconcile(root: Path, directory: Path, name: str, *, confirm_remote_absent: bool = False,
              client: Any | None = None) -> dict:
    _, _, files = validate_run(directory)
    if name not in files:
        raise ValueError("Unknown Batch file")
    path = state_path(directory, name)
    state = json.loads(path.read_text(encoding="utf-8"))
    client = client or client_for(root)
    matches = matching_remote(remote_batches(client), state["metadata"])
    if bind_remote(path, state, matches):
        return state
    if not confirm_remote_absent:
        raise RuntimeError("No matching remote job found; confirm absence in Platform before allowing retry")
    if state["phase"] != "submitting":
        raise ValueError("No ambiguous submission to reconcile")
    state["phase"] = "uploaded" if state.get("input_file_id") else "planned"
    atomic_json(path, state)
    return state


def status(root: Path, directory: Path, *, client: Any | None = None) -> list[dict]:
    _, _, files = validate_run(directory)
    client = client or client_for(root)
    output = []
    for name in files:
        path = state_path(directory, name)
        if not path.exists():
            output.append({"file": name, "status": "not_submitted"})
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        if not state.get("batch_id"):
            output.append({"file": name, "status": state["phase"]})
            continue
        batch = as_dict(client.batches.retrieve(state["batch_id"]))
        if batch.get("metadata") != state["metadata"] or batch.get("input_file_id") != state["input_file_id"]:
            raise ValueError("Remote Batch identity changed")
        state["remote_status"] = batch["status"]
        atomic_json(path, state)
        output.append({"file": name, "batch_id": batch["id"], "status": batch["status"],
                       "request_counts": batch.get("request_counts")})
    return output


def content_text(value: Any) -> str:
    if hasattr(value, "text"):
        return str(value.text)
    if hasattr(value, "read"):
        data = value.read()
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def preserve_download(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Remote result content changed: {path}")
    else:
        atomic_text(path, content)


def ingest(root: Path, directory: Path, *, client: Any | None = None) -> dict:
    summary, records, files = validate_run(directory)
    config = json.loads((directory / "config_snapshot.json").read_text(encoding="utf-8"))
    schema = json.loads((directory / "schema_snapshot.json").read_text(encoding="utf-8"))
    client = client or client_for(root)
    raw_dir = directory / "raw_results"
    raw_dir.mkdir(exist_ok=True)
    for name in files:
        path = state_path(directory, name)
        if not path.exists():
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        if not state.get("batch_id"):
            continue
        batch = as_dict(client.batches.retrieve(state["batch_id"]))
        if batch.get("metadata") != state["metadata"] or batch.get("input_file_id") != state["input_file_id"]:
            raise ValueError("Remote Batch identity changed")
        if batch["status"] not in TERMINAL:
            continue
        for kind, key in (("output", "output_file_id"), ("errors", "error_file_id")):
            if batch.get(key):
                preserve_download(raw_dir / f"{name}.{kind}.jsonl",
                                  content_text(client.files.content(batch[key])))
        expected = {row["custom_id"]: row for row in read_jsonl(directory / name)}
        returned = {}
        for kind in ("output", "errors"):
            for row in read_jsonl(raw_dir / f"{name}.{kind}.jsonl"):
                custom_id = row.get("custom_id")
                if custom_id not in expected or custom_id in returned:
                    raise ValueError(f"Unknown or duplicate returned custom_id: {custom_id}")
                returned[custom_id] = row
        for custom_id, line in expected.items():
            cid = json.loads(line["body"]["input"][-1]["content"])["company_id"]
            record = records[cid]
            if line != record["batch_line"]:
                raise ValueError("Result does not match frozen request")
            destination = directory / ("selection_results" if record["split"] == "selection" else "holdout_results")
            destination.mkdir(exist_ok=True)
            result_path = destination / f"{cid}.json"
            if result_path.exists():
                existing = json.loads(result_path.read_text(encoding="utf-8"))
                if existing["request_hash"] != record["request_hash"] or existing["batch_id"] != batch["id"]:
                    raise ValueError("Existing result identity mismatch")
                continue
            raw = returned.get(custom_id)
            response = raw.get("response") if raw else None
            if raw is None:
                parsed = {"status": "missing", "profile": None,
                          "derived": derive_label(None, status="missing", mapping_version=config["mapping_version"])}
            elif raw.get("error") or not response or response.get("status_code") != 200:
                parsed = {"status": "api_failure", "profile": None,
                          "detail": raw.get("error") or response,
                          "derived": derive_label(None, status="api_failure", mapping_version=config["mapping_version"])}
            else:
                parsed = parse_response(response["body"], json.loads(line["body"]["input"][-1]["content"]),
                                        schema, contract_id=config["contract_id"],
                                        mapping_version=config["mapping_version"])
            atomic_json(result_path, {"company_id": cid, "split": record["split"], "custom_id": custom_id,
                                      "request_hash": record["request_hash"], "batch_id": batch["id"],
                                      "quote_validation_version": summary["quote_validation_version"], **parsed})
    selection = [json.loads(path.read_text(encoding="utf-8")) for path in
                 sorted((directory / "selection_results").glob("*.json"))] if (directory / "selection_results").exists() else []
    holdout_count = len(list((directory / "holdout_results").glob("*.json"))) if (directory / "holdout_results").exists() else 0
    status_counts = Counter(row["status"] for row in selection)
    labels = Counter(row["derived"]["label"] for row in selection if row["status"] == "valid")
    operational = {"expected_requests": summary["included"], "selection_ingested": len(selection),
                   "holdout_ingested": holdout_count, "selection_statuses": dict(status_counts),
                   "selection_valid_labels": dict(labels)}
    atomic_json(directory / "selection_only_summary.json", operational)
    return operational


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path(FINAL_OUTPUT))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dry-run")
    submit_parser = sub.add_parser("submit")
    submit_parser.add_argument("--confirm-paid", action="store_true")
    submit_parser.add_argument("--file")
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--file", required=True)
    reconcile_parser.add_argument("--confirm-remote-absent", action="store_true")
    sub.add_parser("status")
    sub.add_parser("ingest")
    sub.add_parser("report")
    args = parser.parse_args()
    root = find_project_root()
    directory = args.run_dir if args.run_dir.is_absolute() else root / args.run_dir
    if args.command == "dry-run":
        summary, _, _ = validate_run(directory)
        result = {"requests": summary["included"], "splits": summary["splits"],
                  "files": summary["files"], "queue_headroom_tokens": summary["queue_headroom_tokens"],
                  "user_confirmed_queue_allowance": QUEUE_ALLOWANCE}
    elif args.command == "submit":
        result = submit(root, directory, file_name=args.file, confirm_paid=args.confirm_paid)
    elif args.command == "reconcile":
        result = reconcile(root, directory, args.file, confirm_remote_absent=args.confirm_remote_absent)
    elif args.command == "status":
        result = status(root, directory)
    elif args.command == "ingest":
        result = ingest(root, directory)
    else:
        path = directory / "selection_only_summary.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"message": "No selection results ingested"}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
