"""Offline AI-profile evidence-budget comparison for the frozen validation pool."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from .company_ai_profile_contract import canonical, prompt_and_schema, quote_match_status
from .company_ai_profile_packages import (SOURCE_SCORES, _visible, allocate_profile_candidates,
                                          load_config, prepare_profile_candidates)
from .company_ai_profile_tokens import local_tokens
from .company_modeling_sources import load_sources


CAPS = (25000, 50000, 100000, 250000)
DEFAULT_OUTPUT = "data/interim/calibration/ai_profile_budget_analysis/independent_validation_v1"
CONFIG = "config/company_ai_profile_pilot_v5.yaml"
REVALIDATED = ("data/interim/calibration/ai_profile_pilot_v6_four_case_fix/"
               "revalidation/quote_edge_whitespace_v2/results")
PILOT = "data/interim/calibration/ai_profile_pilot_v3_product_priority"


def pool_members(root: Path) -> tuple[list[str], int]:
    rows = list(csv.DictReader((root / SOURCE_SCORES).open(encoding="utf-8", newline="")))
    states = {row["bvd_id"]: row["state"] for row in rows}
    if len(rows) != 1500 or len(states) != 1500 or set(states.values()) != {"PROCESSABLE", "NO_EVIDENCE"}:
        raise ValueError("Unexpected independent-pool score membership")
    ids = sorted(cid for cid, state in states.items() if state == "PROCESSABLE")
    no_evidence = sum(state == "NO_EVIDENCE" for state in states.values())
    if len(ids) != 1084 or no_evidence != 416:
        raise ValueError("Unexpected independent-pool evidence states")
    return ids, no_evidence


def prior_spend(root: Path) -> dict:
    from .run_company_ai_profile_pilot import actual_cost
    base = root / "data/interim/calibration"
    known, calls, missing_usage, ambiguous_reserve = 0.0, 0, 0, 0.0
    for directory in sorted(base.glob("ai_profile_pilot_v*")):
        config_path = directory / "config_snapshot.json"
        if not config_path.exists():
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for path in directory.glob("raw_responses/*/*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            usage = raw.get("usage") or {}
            if not usage or "input_tokens" not in usage or "output_tokens" not in usage:
                missing_usage += 1
                continue
            known += actual_cost(config, usage)
            calls += 1
        for path in directory.glob("attempts/*/*.json"):
            attempt = json.loads(path.read_text(encoding="utf-8"))
            if attempt.get("status") == "ambiguous_network_outcome":
                ambiguous_reserve += float(attempt.get("reserved_usd") or 0)
    return {"known_usage_cost_usd": known, "known_response_calls": calls,
            "responses_without_usage": missing_usage,
            "ambiguous_attempt_reserved_usd": ambiguous_reserve,
            "other_account_charges": "unknown; not included in headroom"}


def supporting_citations(root: Path) -> dict[str, list[dict]]:
    directory = root / REVALIDATED
    result = {}
    for path in sorted(directory.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["status"] != "valid" or saved["derived"]["label"] != "KEEP":
            continue
        profile = saved["profile"]
        ids = set(profile["attribution"]["evidence_ids"])
        for key in ("ai_active", "ai_adjacent"):
            if profile[key]["value"] is True:
                ids.update(profile[key]["evidence_ids"])
        result[saved["company_id"]] = [
            {"evidence_id": item["id"], "page_id": str(item["page_id"]), "quote": item["quote"]}
            for item in profile["evidence"] if item["id"] in ids]
    if set(result) != {"AT9110786558", "AT9130087499", "AT9130109951"}:
        raise ValueError("Reviewed positive pilot membership changed")
    return result


def quote_coverage(package: dict, citations: list[dict]) -> list[dict]:
    pages = {str(page["page_id"]): page for page in package["pages"]}
    checks = []
    for citation in citations:
        page = pages.get(citation["page_id"])
        status = "PAGE_OMITTED" if page is None else (
            quote_match_status(citation["quote"], page["text"]) or "QUOTE_LOST_IN_TRUNCATION")
        checks.append({**citation, "status": status})
    return checks


def batch_cost(input_tokens: int, output_tokens: int, config: dict) -> tuple[float, float]:
    price = config["pricing"]
    long = input_tokens > price["long_context_input_threshold"]
    input_rate = price["input_per_million"] * 0.5 * (price["long_context_input_multiplier"] if long else 1)
    output_rate = price["output_per_million"] * 0.5 * (price["long_context_output_multiplier"] if long else 1)
    return input_tokens * input_rate / 1_000_000, output_tokens * output_rate / 1_000_000


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def charts(directory: Path, summary: list[dict]) -> None:
    caps = [row["cap"] // 1000 for row in summary]
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(caps, [row["batch_input_usd"] for row in summary], "o-", color="#167d87", label="Input")
    ax.plot(caps, [row["batch_total_max_usd"] for row in summary], "o-", color="#c54d32",
            label="Input + 8k output allowance")
    ax.set(xlabel="Website evidence limit (thousand estimated tokens)", ylabel="Estimated Batch cost (USD)",
           title="Estimated cost for 1,084 website-based requests")
    ax.legend(frameon=False)
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(directory / "estimated_batch_cost.png", dpi=170)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(caps, [row["page_coverage_pct"] for row in summary], "o-", color="#167d87",
            label="Eligible pages included")
    ax.plot(caps, [row["complete_company_pct"] for row in summary], "o-", color="#c54d32",
            label="Companies with complete eligible evidence")
    ax.set(xlabel="Website evidence limit (thousand estimated tokens)", ylabel="Coverage (%)",
           ylim=(0, 101), title="Website evidence coverage by token limit")
    ax.legend(frameon=False)
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(directory / "evidence_coverage.png", dpi=170)
    plt.close(fig)


def compare_company(cid: str, company: dict, website: dict, site_pages: list[dict], root: Path,
                    config: dict, overhead: int, citations: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    domain = company["normalized_domain"]
    records = [p for p in site_pages if p["usable"]]
    candidates, excluded = prepare_profile_candidates(records, root, config)
    measurement, selection, citation_rows = [], [], []
    complete_package = None
    for cap in CAPS:
        scoped = {**config, "website_evidence_token_cap": cap}
        package = complete_package or allocate_profile_candidates(
            {"company_id": cid, **company}, website, records, candidates, excluded, scoped)
        coverage = package["coverage"]
        evidence_tokens = local_tokens(canonical(_visible(package)), scoped)
        if evidence_tokens > cap or coverage["included_pages"] + coverage["omitted_pages"] != coverage["available_unique_pages"]:
            raise ValueError(f"Package budget/count mismatch: {cid}/{cap}")
        if coverage["omitted_pages"] == 0 and coverage["text_truncated_pages"] == 0:
            complete_package = package
        selected = [{"page_id": page["page_id"], "truncated": page["truncated"]}
                    for page in package["pages"]]
        input_tokens = overhead + evidence_tokens
        input_cost, output_cost = batch_cost(input_tokens, config["max_output_tokens"], config)
        measurement.append({"company_id": cid, "company_name": company["company_name"],
                            "normalized_domain": domain, "cap": cap,
                            "eligible_pages": coverage["available_unique_pages"],
                            "included_pages": coverage["included_pages"],
                            "omitted_pages": coverage["omitted_pages"],
                            "truncated_pages": coverage["text_truncated_pages"],
                            "excluded_utility_pages": coverage["excluded_utility_pages"],
                            "duplicate_page_records": coverage["duplicate_page_records"],
                            "complete_evidence": int(coverage["omitted_pages"] == 0 and coverage["text_truncated_pages"] == 0),
                            "estimated_evidence_tokens": evidence_tokens,
                            "estimated_input_tokens": input_tokens,
                            "estimated_batch_input_usd": round(input_cost, 9),
                            "estimated_batch_output_max_usd": round(output_cost, 9)})
        selection.append({"company_id": cid, "cap": cap, "selected": selected,
                          "omitted_page_ids": [row["page_id"] for row in package["audit"]["omitted"]]})
        for item in quote_coverage(package, citations):
            citation_rows.append({"company_id": cid, "cap": cap, **item})
    return measurement, selection, citation_rows


def run(root: Path, output: Path, *, workers: int = 4) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if workers < 1:
        raise ValueError("workers must be positive")
    started = time.monotonic()
    config = load_config(root, root / CONFIG)
    prompt, schema = prompt_and_schema(config["contract_id"])
    ids, no_evidence = pool_members(root)
    citations = supporting_citations(root)
    source = load_sources(resolve_database_path(root), root,
                          {"extraction_version": "v1", "calibration_runs": []}, company_ids=ids)
    if source["errors"]:
        raise ValueError(f"Unreadable extracted pages: {len(source['errors'])}")
    companies = {row["bvd_id"]: row for row in source["companies"]}
    if set(companies) != set(ids):
        raise ValueError("Source company membership drift")
    websites = {row["website_id"]: row for row in source["websites"]}
    pages_by_site = defaultdict(list)
    for page in source["pages"]:
        pages_by_site[page["website_id"]].append(page)
    overhead = local_tokens(prompt, config) + local_tokens(canonical(schema), config) + 10
    measurement, selection, citation_rows = [], [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for cid in ids:
            company = companies[cid]
            domain = company["normalized_domain"]
            if not domain or domain not in websites:
                raise ValueError(f"PROCESSABLE company has no website: {cid}")
            future = executor.submit(compare_company, cid, company, websites[domain],
                                     pages_by_site[domain], root, config, overhead, citations.get(cid, []))
            futures[future] = cid
        for position, future in enumerate(as_completed(futures), 1):
            rows, selected, checks = future.result()
            measurement.extend(rows)
            selection.extend(selected)
            citation_rows.extend(checks)
            if position % 25 == 0:
                print(f"Compared {position}/{len(ids)} companies in {time.monotonic()-started:.1f}s", flush=True)
    measurement.sort(key=lambda row: (row["company_id"], row["cap"]))
    selection.sort(key=lambda row: (row["company_id"], row["cap"]))
    citation_rows.sort(key=lambda row: (row["company_id"], row["cap"], row["evidence_id"]))
    summary = []
    for cap in CAPS:
        rows = [row for row in measurement if row["cap"] == cap]
        inputs = sum(row["estimated_input_tokens"] for row in rows)
        input_cost = sum(row["estimated_batch_input_usd"] for row in rows)
        output_cost = sum(row["estimated_batch_output_max_usd"] for row in rows)
        eligible = sum(row["eligible_pages"] for row in rows)
        included = sum(row["included_pages"] for row in rows)
        complete = sum(row["complete_evidence"] for row in rows)
        summary.append({"cap": cap, "estimated_input_tokens": inputs,
                        "batch_input_usd": input_cost, "batch_output_max_usd": output_cost,
                        "batch_total_max_usd": input_cost + output_cost,
                        "headroom_before_prior_spend_usd": 20 - input_cost - output_cost,
                        "included_pages": included, "omitted_pages": eligible - included,
                        "eligible_pages": eligible, "page_coverage_pct": 100 * included / eligible,
                        "complete_companies": complete, "complete_company_pct": 100 * complete / len(ids)})
    counts_by_company = defaultdict(dict)
    for row in measurement:
        counts_by_company[row["company_id"]][row["cap"]] = row["included_pages"]
    nonmonotone = sorted(cid for cid, counts in counts_by_company.items()
                         if any(counts[lower] > counts[upper]
                                for lower, upper in zip(CAPS, CAPS[1:])))
    spend = prior_spend(root)
    output.mkdir(parents=True)
    write_csv(output / "company_budget_comparison.csv", measurement)
    write_csv(output / "positive_evidence_coverage.csv", citation_rows)
    with (output / "page_selection.jsonl").open("w", encoding="utf-8") as handle:
        for row in selection:
            handle.write(canonical(row) + "\n")
    charts(output, summary)
    audit = {"pool_companies": 1500, "processable": len(ids), "no_evidence": no_evidence,
             "local_token_encoding": config["token_counting"]["allocation_encoding"],
             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
             "schema_sha256": hashlib.sha256(canonical(schema).encode()).hexdigest(),
             "source_rows_sha256": source["source_hashes"].get("selected_source_rows"),
             "estimated_overhead_tokens_per_request": overhead,
             "companies_with_nonmonotone_page_counts": nonmonotone,
             "prior_pilot_spend": spend, "runtime_seconds": time.monotonic()-started,
             "summary": summary}
    (output / "summary.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    report_lines = ["# Independent-Pool AI-Profile Evidence Budgets", "",
                    "All 1,084 PROCESSABLE companies are modeled as requests; 416 NO_EVIDENCE companies are excluded, not scored as negatives. Website page ordering, utility filtering and exact-content deduplication use the existing AI-profile builder. Page-selection IDs only are saved; extracted text is not copied.", "",
                    "## Cost And Coverage", "",
                    "| Evidence cap | Estimated input tokens | Batch input | Output, 8k max each | Total | Headroom before prior charges | Included / omitted pages | Complete companies |",
                    "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in summary:
        report_lines.append(f"| {row['cap']:,} | {row['estimated_input_tokens']:,} | ${row['batch_input_usd']:.2f} | ${row['batch_output_max_usd']:.2f} | ${row['batch_total_max_usd']:.2f} | ${row['headroom_before_prior_spend_usd']:.2f} | {row['included_pages']:,} / {row['omitted_pages']:,} ({row['page_coverage_pct']:.1f}% included) | {row['complete_companies']:,} ({row['complete_company_pct']:.1f}%) |")
    report_lines += ["", "![Estimated Batch cost](estimated_batch_cost.png)", "",
                     "![Evidence coverage](evidence_coverage.png)", "",
                     f"For {len(nonmonotone)} companies, a larger cap includes fewer page records at one adjacent comparison: the existing URL-ordered allocator can admit an earlier long page and leave room for fewer later short pages. This is not a change to page filtering or ordering; IDs are recorded in the page-selection manifest.", "",
                     "Estimates use local `o200k_base` allocation counts, not Luna-verified token counts. Prompt and JSON schema overhead is estimated separately with the same local encoding; 10 framing tokens are carried from the last verified pilot request shape. No token-count endpoint was called. Costs assume uncached GPT-6 Luna Batch input at $0.05/M and output at $0.25/M, with the documented long-context multiplier above 272k input tokens. No request here is expected to cross that threshold. Batch rates are 50% of Standard; no EU data-residency premium, retries, or cache discounts are assumed. Pricing source: https://developers.openai.com/api/docs/models/gpt-6-luna (checked 2026-10-19).", "",
                     f"The output column reserves the full 8,000-token allowance for each of 1,084 requests, not a prediction of actual outputs. Known prior pilot API usage across {spend['known_response_calls']} saved responses totals ${spend['known_usage_cost_usd']:.4f} at their saved Standard rates. One ambiguous prior attempt has a ${spend['ambiguous_attempt_reserved_usd']:.4f} reservation but its actual charge is unresolved. Other account charges are unknown. Consequently, displayed headroom is *before* these amounts and is not confirmed remaining account budget.", "",
                     "## Reviewed Positive Evidence", "",
                     "The table checks all saved AI-activity and attribution evidence IDs in the latest offline-revalidated KEEP results. A retained page counts only if the cited quotation survives in its included text (exact or edge-trimmed match).", "",
                     "| Company ID | Cap | Supporting quotes retained | Missing supporting evidence |", "| --- | ---: | ---: | --- |"]
    for cid in sorted(citations):
        for cap in CAPS:
            checks = [row for row in citation_rows if row["company_id"] == cid and row["cap"] == cap]
            retained = sum(row["status"].startswith("PASS_") for row in checks)
            missing = ", ".join(f"{row['evidence_id']} (page {row['page_id']}, {row['status']})"
                                for row in checks if not row["status"].startswith("PASS_")) or "None"
            report_lines.append(f"| {cid} | {cap:,} | {retained}/{len(checks)} | {missing} |")
    report_lines += ["", "The full page IDs and quotation text for missing citations are in `positive_evidence_coverage.csv`. Retaining known quotations is a coverage check, **not** proof that smaller packages produce equally accurate labels. No final cap is selected here.", "",
                     "## Artifacts And Limits", "",
                     "- `company_budget_comparison.csv`: one company/cap measurement row.",
                     "- `page_selection.jsonl`: compact selected/omitted page IDs and truncation flags; no page text.",
                     "- `positive_evidence_coverage.csv`: cited quotation survival.",
                     "- `summary.json`: provenance, counts and cost assumptions.",
                     "- The 416 NO_EVIDENCE firms are not modeled as requests. A PROCESSABLE website can still have all pages excluded by the fixed utility filter; such cases are visible in the company CSV.",
                     "- Local allocation counts may differ from actual model billing, and prior unsettled/account-wide charges are not known. No API calls or labels were generated."]
    (output / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    root = find_project_root()
    output = args.output if args.output.is_absolute() else root / args.output
    result = run(root, output, workers=args.workers)
    print(f"Wrote {result['processable']} companies x {len(CAPS)} caps to {output}")


if __name__ == "__main__":
    main()
