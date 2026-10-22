"""Freeze offline AI-profile Batch requests from the independent validation pool."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from .company_ai_profile_contract import canonical, digest, prompt_and_schema, request_body
from .company_ai_profile_contract import QUOTE_VALIDATION_VERSION
from .company_ai_profile_packages import _visible, load_config, normalized_url_or_raw
from .company_ai_profile_tokens import local_tokens
from .company_modeling_common import file_hash, host
from .company_modeling_groups import balanced_assignment, leakage_groups
from .company_modeling_sources import load_sources
from .compare_ai_profile_budgets import (CAPS, CONFIG, DEFAULT_OUTPUT as BUDGET_OUTPUT,
                                          batch_cost, pool_members, prior_spend)
from .score_company_sample import read_context


DEFAULT_OUTPUT = "data/interim/calibration/ai_profile_validation_batch_v1"
POOL = "data/interim/calibration/acquisition_pools/independent_validation_v1/revisions/v000001/pool.csv"
DATASET = "data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1"
SEED = 20261020
MAX_REQUESTS = 50_000
MAX_FILE_BYTES = 200_000_000
TARGET_FILE_BYTES = 100_000_000
TARGET_FILE_TOKENS = 10_000_000
QUEUE_HEADROOM = 1.2


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(canonical(value) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def host_family_overlap(value: str, known: set[str]) -> bool:
    value = host(value)
    return bool(value and (value in known or any(
        ".".join(value.split(".")[i:]) in known
        for i in range(1, len(value.split(".")) - 1)) or any(
        k.endswith("." + value) for k in known)))


def load_saved_selection(root: Path) -> tuple[dict, dict, dict, dict]:
    directory = root / BUDGET_OUTPUT
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if (summary["pool_companies"], summary["processable"], summary["no_evidence"]) != (1500, 1084, 416):
        raise ValueError("5.4v pool membership mismatch")
    rows = {r["company_id"]: r for r in csv.DictReader(
        (directory / "company_budget_comparison.csv").open(encoding="utf-8", newline=""))
            if int(r["cap"]) == CAPS[-1]}
    selection = {}
    with (directory / "page_selection.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["cap"] == CAPS[-1]:
                if row["company_id"] in selection:
                    raise ValueError("Duplicate saved company selection")
                selection[row["company_id"]] = row
    if len(rows) != 1084 or set(rows) != set(selection):
        raise ValueError("Incomplete 5.4v page selection")
    hashes = {name: file_hash(directory / name) for name in
              ("summary.json", "company_budget_comparison.csv", "page_selection.jsonl")}
    return summary, rows, selection, hashes


def overlap_audit(source: dict, context: dict, config: dict) -> tuple[dict, dict, dict]:
    """Use frozen development evidence and existing grouped-content rules."""
    development = {r["bvd_id"] for r in context["companies"]}
    validation = {r["bvd_id"] for r in source["companies"]}
    combined = {"companies": context["companies"] + source["companies"],
                "relations": context["relations"] + source["relations"],
                "pages": context["pages"] + source["pages"],
                "contents": {**context["contents"], **source["contents"]}}
    groups, edges, quarantine, diagnostics = leakage_groups(combined, config)
    dev_groups = {groups[cid] for cid in development}
    dev_hosts = {host(r["website_id"]) for r in context["relations"]}
    dev_hosts.update(host(p["final_host"]) for p in context["pages"])
    dev_hosts.discard("")
    dev_urls = {normalized_url_or_raw(url) for p in context["pages"]
                for url in (p["url"], p["final_url"]) if url}
    dev_content_domains = defaultdict(set)
    for page in context["pages"]:
        if page["usable"]:
            dev_content_domains[page["content_id"]].add(page["website_id"])
    pages_by_domain = defaultdict(list)
    for page in source["pages"]:
        pages_by_domain[page["website_id"]].append(page)
    audit = {}
    for company in source["companies"]:
        cid, domain = company["bvd_id"], company["normalized_domain"]
        pages = pages_by_domain[domain]
        clear, review = [], []
        if cid in development:
            clear.append("development_company_id")
        if host_family_overlap(domain, dev_hosts):
            clear.append("development_website_family")
        if any(host_family_overlap(p["final_host"], dev_hosts) for p in pages):
            clear.append("development_redirect_family")
        if any(normalized_url_or_raw(url) in dev_urls for p in pages
               for url in (p["url"], p["final_url"]) if url):
            clear.append("development_exact_url")
        if groups[cid] in dev_groups:
            clear.append("development_material_content_group")
        shared = sorted({p["content_id"] for p in pages if p["content_id"] in dev_content_domains})
        if shared and not clear:
            review.append("cross_development_exact_content_subthreshold_or_generic")
        audit[cid] = {"company_id": cid, "website_id": domain, "group_id": groups[cid],
                      "clear_reasons": sorted(set(clear)), "review_reasons": review,
                      "cross_development_content_ids": shared[:20],
                      "cross_development_content_count": len(shared)}
    return audit, groups, {"group_diagnostics": diagnostics, "edge_count": len(edges),
                           "quarantined_content_count": len(quarantine),
                           "cross_development_group_count": len({groups[c] for c in validation} & dev_groups)}


def assign_splits(ids: list[str], groups: dict, sectors: dict, seed: int = SEED) -> dict:
    membership = {cid: groups[cid] for cid in ids}
    categories = {cid: ["sector:" + sectors[cid]] for cid in ids}
    result = balanced_assignment(membership, categories, ["selection", "holdout"], [.8, .2], seed)
    if set(result) != set(ids) or any(len({result[c] for c in ids if groups[c] == gid}) != 1
                                    for gid in {groups[c] for c in ids}):
        raise ValueError("Split group isolation failed")
    return result


def extend_splits(ids: list[str], groups: dict, sectors: dict, previous: dict[str, str],
                  seed: int = SEED) -> dict[str, str]:
    """Keep prior assignments fixed; assign only newly restored website groups."""
    if not set(previous) <= set(ids) or set(previous.values()) - {"selection", "holdout"}:
        raise ValueError("Invalid previous split membership")
    members = defaultdict(list)
    for cid in ids:
        members[groups[cid]].append(cid)
    splits = dict(previous)
    for group, companies in members.items():
        anchored = {previous[cid] for cid in companies if cid in previous}
        if len(anchored) > 1:
            raise ValueError(f"Restored company connects opposite frozen splits: {group}: {companies}")
        if anchored:
            splits.update({cid: next(iter(anchored)) for cid in companies})
    target = round(len(ids) * .8)
    sector_totals = Counter(sectors[cid] for cid in ids)
    pending = [(group, companies) for group, companies in members.items() if companies[0] not in splits]
    pending.sort(key=lambda item: hashlib.sha256(f"{seed}:{item[0]}".encode()).hexdigest())
    for _, companies in pending:
        def penalty(label: str) -> tuple[float, str]:
            selected = sum(value == "selection" for value in splits.values())
            selected += len(companies) if label == "selection" else 0
            sector_selected = Counter(sectors[cid] for cid, value in splits.items() if value == "selection")
            if label == "selection":
                sector_selected.update(sectors[cid] for cid in companies)
            return (abs(selected - target) + .25 * sum(abs(sector_selected[s] - .8 * n)
                                                      for s, n in sector_totals.items()), label)
        chosen = min(("selection", "holdout"), key=penalty)
        splits.update({cid: chosen for cid in companies})
    if set(splits) != set(ids) or any(len({splits[c] for c in companies}) != 1 for companies in members.values()):
        raise ValueError("Final split isolation failed")
    return splits


def reconstruct_package(cid: str, company: dict, website: dict, pages: dict,
                        saved: dict, measure: dict, root: Path, config: dict) -> dict:
    selected = []
    for entry in saved["selected"]:
        page_id = int(entry["page_id"])
        page = pages.get(page_id)
        if page is None or page["website_id"] != company["normalized_domain"] or not page["usable"]:
            raise ValueError(f"Saved page outside focal website: {cid}/{page_id}")
        if entry["truncated"]:
            raise ValueError("5.4v partial page cannot be reconstructed from compact manifest")
        text = (root / page["text_path"]).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise ValueError(f"Source page changed: {page_id}")
        selected.append({"page_id": str(page_id), "url": page["final_url"] or page["url"],
                         "title": page["title"] or "", "text": text, "truncated": False})
    package = {"company_id": cid, "company_name": company["company_name"],
               "normalized_domain": company["normalized_domain"],
               "shared_website": bool(website["mapped_company_count"] > 1),
               "coverage": {"available_unique_pages": int(measure["eligible_pages"]),
                            "included_pages": int(measure["included_pages"]),
                            "excluded_utility_pages": int(measure["excluded_utility_pages"]),
                            "duplicate_page_records": int(measure["duplicate_page_records"]),
                            "omitted_pages": int(measure["omitted_pages"]),
                            "text_truncated_pages": int(measure["truncated_pages"])},
               "pages": selected}
    if len(selected) != package["coverage"]["included_pages"] or len(saved["omitted_page_ids"]) != package["coverage"]["omitted_pages"]:
        raise ValueError(f"Saved coverage mismatch: {cid}")
    estimate = local_tokens(canonical(_visible(package)), config)
    if estimate != int(measure["estimated_evidence_tokens"]) or estimate > config["website_evidence_token_cap"]:
        raise ValueError(f"Saved token allocation mismatch: {cid}")
    return package


def pack_files(records: list[dict], output: Path) -> list[dict]:
    files, lines, size, tokens = [], [], 0, 0
    def flush():
        nonlocal lines, size, tokens
        if not lines:
            return
        path = output / f"batch_{len(files)+1:04d}.jsonl"
        if path.exists():
            if path.read_bytes() != "".join(lines).encode("utf-8"):
                raise ValueError(f"Incompatible existing Batch file: {path}")
        else:
            tmp = path.with_suffix(".jsonl.tmp")
            if tmp.exists():
                if tmp.read_bytes() != "".join(lines).encode("utf-8"):
                    raise ValueError(f"Incompatible interrupted Batch file: {tmp}")
            else:
                with tmp.open("x", encoding="utf-8") as handle:
                    handle.writelines(lines)
            os.replace(tmp, path)
        if path.stat().st_size != size or size > MAX_FILE_BYTES or len(lines) > MAX_REQUESTS:
            raise ValueError("Batch file size or count mismatch")
        files.append({"path": str(path), "requests": len(lines), "bytes": size,
                      "estimated_input_tokens": tokens, "sha256": file_hash(path)})
        lines, size, tokens = [], 0, 0
    for record in records:
        line = canonical(record["batch_line"]) + "\n"
        encoded = len(line.encode("utf-8"))
        if encoded > TARGET_FILE_BYTES:
            raise ValueError("Single request exceeds conservative file target")
        if lines and (size + encoded > TARGET_FILE_BYTES or len(lines) >= MAX_REQUESTS or
                      tokens + record["estimated_input_tokens"] > TARGET_FILE_TOKENS):
            flush()
        lines.append(line); size += encoded; tokens += record["estimated_input_tokens"]
    flush()
    return files


def prepare(root: Path, output: Path, *, resume: bool = False,
            review: Path | None = None, previous: Path | None = None) -> dict:
    if output.exists() and not resume:
        raise FileExistsError(output)
    config = load_config(root, root / CONFIG)
    if (config["model"], config["reasoning_effort"], config["website_evidence_token_cap"],
            config["max_output_tokens"]) != ("gpt-6-luna", "low", 250000, 8000):
        raise ValueError("Approved model/settings changed")
    prompt, schema = prompt_and_schema(config["contract_id"])
    ids, no_evidence = pool_members(root)
    baseline, measures, selections, baseline_hashes = load_saved_selection(root)
    if set(ids) != set(measures):
        raise ValueError("Saved selection is not the scored pool")
    source = load_sources(resolve_database_path(root), root,
                          {"extraction_version": "v1", "calibration_runs": []}, company_ids=ids)
    if source["errors"] or source["source_hashes"]["selected_source_rows"] != baseline["source_rows_sha256"]:
        raise ValueError("5.4v source evidence changed; do not reuse its page selection")
    print(f"Validated 5.4v source snapshot: {len(source['pages'])} extracted pages", flush=True)
    context, group_config, dataset_manifest = read_context(root / DATASET)
    print("Frozen development overlap context loaded", flush=True)
    audit, groups, overlap_meta = overlap_audit(source, context, group_config)
    print("Development overlap and validation grouping audited", flush=True)
    pool_rows = list(csv.DictReader((root / POOL).open(encoding="utf-8", newline="")))
    sectors = {r["company_id"]: r["sector"] for r in pool_rows}
    if not set(ids) <= set(sectors):
        raise ValueError("Missing pool sector metadata")
    review_decisions = {}
    previous_records = {}
    previous_hashes = {}
    if (review is None) != (previous is None):
        raise ValueError("Finalization requires both review and previous preparation")
    if review is not None:
        review_rows = list(csv.DictReader(review.open(encoding="utf-8", newline="")))
        review_decisions = {r["company_id"]: r["decision"] for r in review_rows}
        if (len(review_rows) != 64 or len(review_decisions) != 64
                or set(review_decisions.values()) != {"INCLUDE"}):
            raise ValueError("Expected 64 unique approved inclusions")
        previous_manifest = [json.loads(line) for line in (previous / "company_manifest.jsonl").open(encoding="utf-8")]
        if {r["company_id"] for r in previous_manifest if r["status"] == "unresolved"} != set(review_decisions):
            raise ValueError("Review membership differs from the frozen withheld set")
        if {r["company_id"] for r in previous_manifest if r["status"] == "quarantined"} != {
                "AT9030118034", "AT9150093010"}:
            raise ValueError("Clear development exclusions changed")
        prior_identity = json.loads((previous / "preparation_identity.json").read_text(encoding="utf-8"))
        for key in ("config_sha256", "prompt_sha256", "schema_sha256", "source_rows_sha256",
                    "development_dataset_config_hash", "development_dataset_table_hashes",
                    "baseline_artifact_hashes", "pool_sha256"):
            current = {"config_sha256": file_hash(root / CONFIG),
                       "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                       "schema_sha256": digest(schema), "source_rows_sha256": baseline["source_rows_sha256"],
                       "development_dataset_config_hash": dataset_manifest["config_hash"],
                       "development_dataset_table_hashes": {k: v["file_hash"] for k, v in dataset_manifest["tables"].items()},
                       "baseline_artifact_hashes": baseline_hashes, "pool_sha256": file_hash(root / POOL)}[key]
            if prior_identity[key] != current:
                raise ValueError(f"Prior preparation source changed: {key}")
        previous_summary = json.loads((previous / "summary.json").read_text(encoding="utf-8"))
        if previous_summary["included"] != 1017 or any(
                file_hash(Path(f["path"])) != f["sha256"] for f in previous_summary["files"]):
            raise ValueError("Prior Batch files failed identity checks")
        previous_splits = {r["company_id"]: r["split"] for r in previous_manifest if r["status"] == "included"}
        previous_records = {cid: json.loads((previous / "work" / f"{cid}.json").read_text(encoding="utf-8"))
                            for cid in previous_splits}
        if len(previous_records) != 1017 or any(
                r["split"] != previous_splits[cid] or r["request_hash"] != digest(r["batch_line"]["body"])
                for cid, r in previous_records.items()):
            raise ValueError("Prior company checkpoints failed identity checks")
        previous_hashes = {"review_sha256": file_hash(review),
                           "previous_manifest_sha256": file_hash(previous / "company_manifest.jsonl"),
                           "previous_summary_sha256": file_hash(previous / "summary.json")}
    accepted = [cid for cid in ids if not audit[cid]["clear_reasons"]
                and (not audit[cid]["review_reasons"] or cid in review_decisions)
                and int(measures[cid]["included_pages"]) > 0]
    if review is not None and (len(accepted) != 1081 or set(accepted) != set(previous_records) | set(review_decisions)):
        raise ValueError("Final membership is not the expected 1,081 companies")
    splits = extend_splits(accepted, groups, sectors, previous_splits) if review is not None else assign_splits(accepted, groups, sectors)
    identity = {"config_sha256": file_hash(root / CONFIG), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "schema_sha256": digest(schema), "source_rows_sha256": baseline["source_rows_sha256"],
                "development_dataset_config_hash": dataset_manifest["config_hash"],
                "development_dataset_table_hashes": {k: v["file_hash"] for k, v in dataset_manifest["tables"].items()},
                "baseline_artifact_hashes": baseline_hashes, "pool_sha256": file_hash(root / POOL),
                "seed": SEED, "split_policy": "frozen_5_4w_plus_reviewed_groups_v1" if review else "domain_and_material_content_group_80_20_v1",
                **previous_hashes}
    identity_hash = digest(identity)
    if output.exists():
        if not resume or not (output / "preparation_identity.json").exists():
            raise FileExistsError(output)
        if json.loads((output / "preparation_identity.json").read_text(encoding="utf-8")) != identity:
            raise ValueError("Resume source/configuration identity mismatch")
    else:
        output.mkdir(parents=True)
        (output / "work").mkdir()
        atomic_json(output / "preparation_identity.json", identity)
        atomic_json(output / "config_snapshot.json", config)
        atomic_json(output / "schema_snapshot.json", schema)
        (output / "prompt_snapshot.txt").write_text(prompt, encoding="utf-8")
    companies = {r["bvd_id"]: r for r in source["companies"]}
    websites = {r["website_id"]: r for r in source["websites"]}
    pages = {r["scraped_page_id"]: r for r in source["pages"]}
    overhead = baseline["estimated_overhead_tokens_per_request"]
    prepared = []
    for number, cid in enumerate(sorted(accepted), 1):
        path = output / "work" / f"{cid}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["identity_hash"] != identity_hash or record["company_id"] != cid or record["split"] != splits[cid]:
                raise ValueError(f"Incompatible company checkpoint: {cid}")
            if digest(record["batch_line"]["body"]) != record["request_hash"]:
                raise ValueError(f"Corrupt company checkpoint: {cid}")
        else:
            if cid in previous_records:
                record = {**previous_records[cid], "identity_hash": identity_hash, "group_id": groups[cid]}
            else:
                company = companies[cid]
                package = reconstruct_package(cid, company, websites[company["normalized_domain"]], pages,
                                              selections[cid], measures[cid], root, config)
                body = request_body(package, config, prompt, schema)
                request_hash = digest(body)
                estimate = overhead + int(measures[cid]["estimated_evidence_tokens"])
                if estimate != int(measures[cid]["estimated_input_tokens"]) or estimate * QUEUE_HEADROOM + 8000 > 1_050_000:
                    raise ValueError(f"Estimated request size invalid: {cid}")
                custom_id = f"independent-v1-{cid}-{request_hash[:16]}"
                record = {"identity_hash": identity_hash, "company_id": cid, "split": splits[cid],
                          "group_id": groups[cid], "sector": sectors[cid],
                          "request_hash": request_hash, "estimated_input_tokens": estimate,
                          "included_pages": len(package["pages"]),
                          "omitted_pages": package["coverage"]["omitted_pages"],
                          "page_ids": [p["page_id"] for p in package["pages"]],
                          "batch_line": {"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body}}
            atomic_json(path, record)
        prepared.append(record)
        if number % 25 == 0:
            print(f"Prepared {number}/{len(accepted)} companies", flush=True)
    if len({r["batch_line"]["custom_id"] for r in prepared}) != len(prepared):
        raise ValueError("Duplicate Batch custom IDs")
    # A resumed completed preparation is verified, not overwritten.
    if (output / "report.md").exists():
        completed = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        if completed["included"] != len(prepared) or any(
                file_hash(Path(item["path"])) != item["sha256"]
                or Path(item["path"]).stat().st_size != item["bytes"]
                for item in completed["files"]):
            raise ValueError("Completed preparation artifact changed")
        return completed
    files = pack_files(prepared, output)
    total_tokens = sum(r["estimated_input_tokens"] for r in prepared)
    estimated_cost = [batch_cost(r["estimated_input_tokens"], 8000, config) for r in prepared]
    input_cost = sum(c[0] for c in estimated_cost); output_cost = sum(c[1] for c in estimated_cost)
    sensitivity = [batch_cost(int(r["estimated_input_tokens"] * QUEUE_HEADROOM), 8000, config)
                   for r in prepared]
    sensitivity_cost = sum(sum(cost) for cost in sensitivity)
    long_context_sensitivity_count = sum(r["estimated_input_tokens"] * QUEUE_HEADROOM > 272000
                                         for r in prepared)
    skipped_empty = sorted(cid for cid in ids if int(measures[cid]["included_pages"]) == 0)
    quarantined = sorted(cid for cid in ids if audit[cid]["clear_reasons"])
    unresolved = sorted(cid for cid in ids if audit[cid]["review_reasons"] and cid not in quarantined
                        and cid not in review_decisions)
    summary = {"pool_companies": 1500, "initial_processable": len(ids), "no_evidence": no_evidence,
               "included": len(prepared), "skipped_no_usable_package": len(skipped_empty),
               "quarantined": len(quarantined), "unresolved": len(unresolved),
               "splits": dict(Counter(splits.values())), "shared_groups": len(set(groups[c] for c in accepted)),
               "estimated_input_tokens": total_tokens, "queue_headroom_tokens": int(total_tokens * QUEUE_HEADROOM),
               "batch_input_usd": input_cost, "batch_output_reserved_usd": output_cost,
               "batch_total_reserved_usd": input_cost + output_cost,
               "twenty_percent_more_input_cost_sensitivity_usd": sensitivity_cost,
               "potential_long_context_requests_at_twenty_percent_more_input": long_context_sensitivity_count,
               "contract_id": config["contract_id"], "mapping_version": config["mapping_version"],
               "quote_validation_version": QUOTE_VALIDATION_VERSION,
               "prior_spend": prior_spend(root), "files": files, "overlap": overlap_meta,
               "submission_blocked": review is None,
               "blockers": (["Account/project Luna Batch queue limit and current queued tokens unverified",
                             "Independence review required for unresolved content overlaps"] +
                            (["Manual review of quarantined overlap evidence"] if quarantined else [])) if review is None else [],
               "reviewed_inclusions": len(review_decisions),
               "account_queue_allowance": ("User confirmed 1 billion Luna Batch queued input tokens per day and empty queue on 2026-10-20; not API verified; recheck active jobs before submission" if review else None)}
    atomic_json(output / "summary.json", summary)
    manifest_text = "".join(canonical({**audit[cid], "status": (
        "quarantined" if cid in quarantined else "unresolved" if cid in unresolved
        else "skipped_no_usable_package" if cid in skipped_empty else "included"),
        "split": splits.get(cid), "sector": sectors[cid], "overlap_review_decision": review_decisions.get(cid)}) + "\n" for cid in ids)
    manifest_path = output / "company_manifest.jsonl"
    if manifest_path.exists():
        if manifest_path.read_text(encoding="utf-8") != manifest_text:
            raise ValueError("Incompatible existing company manifest")
    else:
        manifest_path.write_text(manifest_text, encoding="utf-8")
    report = ["# Independent Validation Batch Preparation", "",
              ("**Finalized offline; explicit user-run submission required.**" if review else "**Prepared offline; submission blocked.**") + " No files were uploaded and no labeling API calls were made.", "",
              f"Starting pool: 1,500 companies, {len(ids)} PROCESSABLE, {no_evidence} NO_EVIDENCE.",
              f"Included {len(prepared)} requests; skipped {len(skipped_empty)} with no usable packaged page; quarantined {len(quarantined)} clear development overlaps; unresolved {len(unresolved)} possible overlaps.",
              f"Split seed {SEED}: selection {summary['splits'].get('selection',0)}, holdout {summary['splits'].get('holdout',0)} across {summary['shared_groups']} website/content groups. Split assignment is frozen in `company_manifest.jsonl`; holdout labels must not enter selection reports.", "",
              "## Batch Files", "", "| File | Requests | UTF-8 bytes | Estimated input tokens |", "| --- | ---: | ---: | ---: |"]
    report += [f"| `{Path(f['path']).name}` | {f['requests']:,} | {f['bytes']:,} | {f['estimated_input_tokens']:,} |" for f in files]
    report += ["", f"Total estimated input: {total_tokens:,} tokens. Queue-planning allowance (+20%): {summary['queue_headroom_tokens']:,} tokens.",
               f"Estimated uncached Batch input ${input_cost:.2f}; full 8,000-output-token reservation ${output_cost:.2f}; combined ${input_cost+output_cost:.2f}. Local `o200k_base` counts are **not** model-verified. Pricing uses short-context Luna Batch rates of $0.05/M input and $0.25/M output; any request above 272k actual input tokens would incur long-context multipliers (2x input, 1.5x output).", "",
               f"At 20% more actual input tokens per request, {long_context_sensitivity_count} requests would cross 272k and the estimated combined cost would be ${sensitivity_cost:.2f}, before other billing modifiers. This is a sensitivity scenario, not a prediction.", "",
               "## Limits and Submission Checks" if review else "## Limits and Submission Blockers", "",
               "OpenAI documentation checked 2026-10-20: [Batch guide](https://developers.openai.com/api/docs/guides/batch), [Batch create reference](https://developers.openai.com/api/reference/resources/batches/methods/create), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [pricing](https://developers.openai.com/api/docs/pricing), [rate limits](https://developers.openai.com/api/docs/guides/rate-limits).",
               "A Batch accepts at most 50,000 requests and a 200 MB JSONL file. `/v1/responses` JSONL lines require unique `custom_id`, `method: POST` and `body`; only the `24h` completion window is supported. Luna supports structured output, a 1,050,000-token context and 128,000-token maximum output. These files target 100 MB and 10M estimated input tokens apiece, below the per-file ceilings.",
               ("The user confirmed a 1B-token/day Luna Batch allowance and an empty queue on 2026-10-20. This is user-reported, not API verified or necessarily the currently free *queued-token* allowance. Queue occupancy, spend limits and residency settings must be rechecked before submission. The adapter refuses unrelated active jobs and reserves 20% token-estimate headroom; splitting files does not increase the shared allowance." if review else "Luna Batch *queue* limits are tier-specific: Build 20M, Launch 1B, Grow 15B queued input tokens. This is a shared active-model queue, not a per-file allowance. The account/project tier, queue occupancy, monthly/spend limits and residency/processing settings were not queried. Check Platform Settings > Organization/Project > Limits before submission; submit in waves if the **available** Luna queue is below a file's actual prompt-token volume. Splitting files alone does not increase the queue allowance."),
               ("The identity-checking adapter exposes explicit dry-run, paid submit, status, reconciliation, ingest and selection-only report commands. It does not submit automatically; ambiguous outcomes fail closed." if review else "The existing AI-profile runner exports Batch JSONL but has no upload/create/status/ingest commands. A narrowly scoped, identity-checking Batch submit/status/ingest adapter is needed before user-run submission commands can be offered. No generic orchestration was added here."), "",
               "## Provenance and Coverage", "",
               f"5.4v source-row hash and all three saved-artifact hashes were checked. Prompt/schema/config and frozen development dataset hashes are in `preparation_identity.json`. Company checkpoints in `work/` are atomic and reusable with `--resume`; a mismatch fails closed. {sum(r['included_pages'] for r in prepared):,} pages are included and {sum(r['omitted_pages'] for r in prepared):,} omitted across prepared requests. Raw website text exists only inside the frozen request lines/checkpoints.",
               f"Contract `{config['contract_id']}`, Python mapping `{config['mapping_version']}`, and offline quotation validator `{QUOTE_VALIDATION_VERSION}` remain the approved pilot versions. These are request preparations only; no derived labels exist yet.",
               f"Known prior saved pilot usage is ${summary['prior_spend']['known_usage_cost_usd']:.4f}; one ambiguous prior attempt has a ${summary['prior_spend']['ambiguous_attempt_reserved_usd']:.4f} reservation. Other account charges remain unknown; this is not a confirmed $20 balance.",
               ("The 64 generic-only overlaps were approved in the separate 5.4x review and restored without removing matched pages. The 1,017 prior splits and request bodies are unchanged. The user reported a 1B Luna Batch queued-token allowance and empty queue on 2026-10-20; this is not API verified and active jobs must be rechecked immediately before submission." if review else "Clear development overlaps were quarantined. Generic/subthreshold content matches are flagged separately for review."),
               "Existing near-duplicate candidate search is not exhaustive; unobserved redirects or content families remain possible. No final production cutoff or validation label was created."]
    if review:
        report.extend(["", "## Explicit User Commands", "",
                       "```bash",
                       ".venv/bin/python -m ai_ecosystem.calibration.run_ai_profile_validation_batch dry-run",
                       ".venv/bin/python -m ai_ecosystem.calibration.run_ai_profile_validation_batch submit --confirm-paid",
                       ".venv/bin/python -m ai_ecosystem.calibration.run_ai_profile_validation_batch status",
                       ".venv/bin/python -m ai_ecosystem.calibration.run_ai_profile_validation_batch ingest",
                       ".venv/bin/python -m ai_ecosystem.calibration.run_ai_profile_validation_batch report",
                       "```", "",
                       "`submit` performs uploads and paid Batch creation only when explicitly invoked. Check the Platform's current allowance, active jobs, billing and processing settings first. `ingest` saves holdout results separately; `report` exposes selection labels only. Ambiguous creates require `reconcile --file NAME` before retry; no failed request is automatically resubmitted."])
        report.append("If a multi-file submission stops after some files succeed, use `status`, then `submit --file batch_XXXX.jsonl --confirm-paid` for each file still unsubmitted. Repeating the default submit command refuses a duplicate rather than resending it.")
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = find_project_root()
    output = args.output if args.output.is_absolute() else root / args.output
    result = prepare(root, output, resume=args.resume)
    print(f"Prepared {result['included']} requests in {len(result['files'])} Batch files: {output}")


if __name__ == "__main__":
    main()
