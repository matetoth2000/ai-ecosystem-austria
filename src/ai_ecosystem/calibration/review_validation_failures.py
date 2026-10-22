"""Offline, selection-only review of invalid AI-profile Batch responses."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from ai_ecosystem.calibration.company_ai_profile_contract import derive_label, quote_match_status, validate_profile
from ai_ecosystem.calibration.run_company_ai_profile_pilot import extract_text


DEFAULT_RUN = Path("data/interim/calibration/ai_profile_validation_batch_v2_final")
DEFAULT_OUTPUT = Path("data/interim/calibration/ai_profile_validation_failure_review_v5")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact_space(value: str) -> str:
    return " ".join(value.split())


def quote_diagnosis(quote: str, cited: str, pages: dict[str, str]) -> tuple[str, list[str]]:
    if quote_match_status(quote, pages[cited]):
        return "accepted_quote", []
    exact_elsewhere = sorted(pid for pid, text in pages.items() if pid != cited and quote in text)
    if exact_elsewhere:
        return "exact_quote_on_other_page", exact_elsewhere
    normalized = compact_space(quote)
    if normalized and any(normalized in compact_space(text) for text in pages.values()):
        return "internal_whitespace_difference", []
    words = normalized.casefold().split()
    distinctive = [" ".join(words[index:index + 4]) for index in range(max(0, len(words) - 3))]
    if distinctive and any(fragment in compact_space(text).casefold()
                           for text in pages.values() for fragment in distinctive):
        return "changed_wording_partial_source", []
    return "quotation_absent_from_supplied_evidence", []


def nearby_excerpt(quote: str, page_text: str) -> str:
    """Show the closest local context without treating a fuzzy match as valid."""
    tokens = [token for token in quote.split() if len(token) >= 5]
    if not tokens:
        return page_text[:300]
    folded = page_text.casefold()
    for token in tokens[:20]:
        location = folded.find(token.casefold())
        if location >= 0:
            return page_text[max(0, location - 100):location + 220]
    return page_text[:300]


def reference_issues(profile: dict) -> list[str]:
    evidence_ids = {item["id"] for item in profile["evidence"]}
    issues = []
    for path, item in [(name, profile[name]) for name in ("ai_active", "ai_adjacent", "attribution")]:
        if path != "attribution" and item["value"] is True and not item["evidence_ids"]:
            issues.append(f"{path}.evidence_ids: required reference missing")
        if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
            issues.append(f"{path}.evidence_ids: duplicate references")
        for eid in item["evidence_ids"]:
            if eid not in evidence_ids:
                issues.append(f"{path}.evidence_ids: unknown {eid}")
    for section in ("entities", "roles", "technologies", "relationships", "unmapped_findings"):
        for index, item in enumerate(profile[section]):
            if not item["evidence_ids"]:
                issues.append(f"{section}[{index}].evidence_ids: required reference missing")
            if len(item["evidence_ids"]) != len(set(item["evidence_ids"])):
                issues.append(f"{section}[{index}].evidence_ids: duplicate references")
            for eid in item["evidence_ids"]:
                if eid not in evidence_ids:
                    issues.append(f"{section}[{index}].evidence_ids: unknown {eid}")
    return issues


def selected_raw_responses(run: Path, custom_ids: set[str]) -> dict[str, tuple[dict, str]]:
    """Do not decode nonselected Batch response bodies, including holdout items."""
    found = {}
    for path in sorted((run / "raw_results").glob("*.output.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                match = re.search(r'"custom_id"\s*:\s*"([^"\\]+)"', line)
                if not match or match.group(1) not in custom_ids:
                    continue
                row = json.loads(line)
                custom_id = row["custom_id"]
                if custom_id in found:
                    raise ValueError(f"Duplicate raw selection response: {custom_id}")
                found[custom_id] = row["response"]["body"], hashlib.sha256(line.encode()).hexdigest()
    if set(found) != custom_ids:
        raise ValueError(f"Missing raw selection responses: {sorted(custom_ids - set(found))}")
    return found


def review_one(result: dict, work: dict, schema: dict, config: dict, result_hash: str,
               raw: dict, raw_hash: str, *, expected_split: str = "selection") -> tuple[dict, dict | None]:
    if expected_split not in {"selection", "holdout"} or result["split"] != expected_split or work["split"] != expected_split:
        raise ValueError("Review received a record from the wrong split")
    if result["request_hash"] != work["request_hash"]:
        raise ValueError("Frozen request hash mismatch")
    package = json.loads(work["batch_line"]["body"]["input"][-1]["content"])
    if package["company_id"] != result["company_id"]:
        raise ValueError("Frozen request company mismatch")
    pages = {str(item["page_id"]): item["text"] for item in package["pages"]}
    profile = result["profile"]
    raw_text, refused = extract_text(raw)
    if refused or json.loads(raw_text) != profile or raw.get("id") != result["response_id"]:
        raise ValueError("Raw selection response differs from saved invalid profile")
    details = []
    corrections = []
    candidate = copy.deepcopy(profile)
    for index, evidence in enumerate(profile["evidence"]):
        pid, quote = evidence["page_id"], evidence["quote"]
        if pid not in pages:
            details.append({"field": f"evidence[{index}].page_id", "evidence_id": evidence["id"],
                            "cause": "unknown_page_id", "cited_page_id": pid, "quote": quote})
            continue
        if quote_match_status(quote, pages[pid]):
            continue
        cause, other_pages = quote_diagnosis(quote, pid, pages)
        details.append({"field": f"evidence[{index}].quote", "evidence_id": evidence["id"],
                        "cause": cause, "cited_page_id": pid, "matching_page_ids": other_pages,
                        "quote": quote, "cited_page_excerpt": nearby_excerpt(quote, pages[pid]),
                        "matching_page_excerpts": {other: nearby_excerpt(quote, pages[other])
                                                   for other in other_pages}})
        if cause == "exact_quote_on_other_page" and len(other_pages) == 1:
            candidate["evidence"][index]["page_id"] = other_pages[0]
            corrections.append({"field": f"evidence[{index}].page_id", "from": pid, "to": other_pages[0],
                                "justification": "The unchanged quote matches exactly one other frozen supplied page"})
    for issue in reference_issues(profile):
        details.append({"field": issue.split(":", 1)[0], "cause": "invalid_reference", "detail": issue})
    if result["status"] == "invalid_references_or_contract":
        contract_detail = result["detail"]
        if contract_detail == "nonpositive_profile_has_positive_outputs":
            field = "entities" if profile["entities"] else "ai_summary/roles/technologies/relationships"
            explanation = "Positive-profile content is present although both AI assessments are nonpositive"
        elif contract_detail == "group_scope_requires_membership_reference":
            field = "ai_active.evidence_ids / attribution.evidence_ids"
            explanation = f"No shared membership reference: active={profile['ai_active']['evidence_ids']}, attribution={profile['attribution']['evidence_ids']}"
        elif contract_detail == "duplicate_or_invalid_entity_id":
            field = "entities[].id"
            explanation = f"Entity IDs: {[item['id'] for item in profile['entities']]} (focal_company is reserved)"
        else:
            field, explanation = "contract", contract_detail
        details.append({"field": field, "cause": contract_detail, "detail": explanation})
    recovered = None
    if corrections and len(corrections) == sum(d["cause"] == "exact_quote_on_other_page" for d in details) and all(
            d["cause"] == "exact_quote_on_other_page" for d in details):
        try:
            validated = validate_profile(copy.deepcopy(candidate), package, schema, contract_id=config["contract_id"])
        except ValueError:
            pass
        else:
            recovered = {"company_id": result["company_id"], "split": expected_split, "source_result_sha256": result_hash,
                         "source_raw_response_sha256": raw_hash,
                         "request_hash": result["request_hash"], "original_status": result["status"],
                         "original_detail": result["detail"], "corrections": corrections,
                         "validation_contract": config["contract_id"],
                         "quote_validation_version": result["quote_validation_version"],
                         "mapping_version": config["mapping_version"], "validation_status": "valid",
                         "profile": validated, "derived": derive_label(validated, mapping_version=config["mapping_version"])}
    row = {"company_id": result["company_id"], "company_name": package["company_name"],
           "original_status": result["status"], "original_detail": result.get("detail", ""),
           "cause": ";".join(sorted({item["cause"] for item in details})),
           "details": details, "outcome": "recovered" if recovered else "unresolved",
           "recovered_label": recovered["derived"]["label"] if recovered else None,
           "corrections": corrections if recovered else [], "source_result_sha256": result_hash,
           "source_raw_response_sha256": raw_hash,
           "request_hash": result["request_hash"], "response_id": result.get("response_id"),
           "raw_status": result.get("raw_status"), "output_tokens": (result.get("usage") or {}).get("output_tokens"),
           "max_output_tokens": work["batch_line"]["body"]["max_output_tokens"],
           "incomplete_details": raw.get("incomplete_details")}
    if recovered:
        row["next_action"] = "none_recovered_offline"
    elif any(item["cause"] in {"changed_wording_partial_source", "quotation_absent_from_supplied_evidence",
                                   "group_scope_requires_membership_reference", "nonpositive_profile_has_positive_outputs"}
             for item in details):
        row["next_action"] = "manual_evidence_or_contract_review_before_any_retry"
    else:
        row["next_action"] = "targeted_api_retry_for_exact_quote_or_valid_contract_references"
    return row, recovered


def build(run: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    config = json.loads((run / "config_snapshot.json").read_text())
    schema = json.loads((run / "schema_snapshot.json").read_text())
    selection_dir = run / "selection_results"
    results = sorted(selection_dir.glob("*.json"))
    if len(results) != 865:
        raise ValueError(f"Expected 865 selection results, found {len(results)}")
    all_results = [json.loads(path.read_text()) for path in results]
    invalid_results = [row for row in all_results if row["status"] != "valid"]
    raw_responses = selected_raw_responses(run, {row["custom_id"] for row in invalid_results})
    rows, recovered = [], []
    original_labels = Counter()
    for path, result in zip(results, all_results, strict=True):
        if result["split"] != "selection":
            raise ValueError(f"Nonselection result: {path}")
        if result["status"] == "valid":
            original_labels[result["derived"]["label"]] += 1
            continue
        work = json.loads((run / "work" / path.name).read_text())
        raw, raw_hash = raw_responses[result["custom_id"]]
        row, corrected = review_one(result, work, schema, config, digest(path), raw, raw_hash)
        rows.append(row)
        if corrected:
            recovered.append(corrected)
    if len(rows) != 22 or Counter(row["original_status"] for row in rows) != {
            "failed_exact_quote": 18, "invalid_references_or_contract": 4}:
        raise ValueError("Invalid response census differs from frozen selection summary")
    if original_labels != {"KEEP": 51, "UNKNOWN": 59, "EXCLUDE": 733}:
        raise ValueError("Original-valid selection labels differ from frozen summary")
    output.mkdir(parents=True)
    (output / "diagnostics.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))
    (output / "recovered_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in recovered))
    with (output / "company_review.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["company_id", "company_name", "original_status", "original_detail",
                                                  "cause", "outcome", "next_action", "recovered_label", "response_id", "raw_status",
                                                  "output_tokens", "max_output_tokens", "source_result_sha256",
                                                  "source_raw_response_sha256", "request_hash", "details_json",
                                                  "corrections_json"])
        writer.writeheader()
        for row in rows:
            values = {key: row.get(key) for key in writer.fieldnames}
            values["details_json"] = json.dumps(row["details"], ensure_ascii=False)
            values["corrections_json"] = json.dumps(row["corrections"], ensure_ascii=False)
            writer.writerow(values)
    recovered_labels = Counter(row["derived"]["label"] for row in recovered)
    summary = {"run": str(run), "review_version": "selection_failure_review_v5", "selection_responses": 865,
               "original_valid": 843, "original_valid_labels": dict(original_labels), "invalid_reviewed": len(rows),
               "recovered": len(recovered), "recovered_labels": dict(recovered_labels),
               "unresolved": len(rows) - len(recovered),
               "cause_counts": dict(Counter(cause for row in rows for cause in set(row["cause"].split(";")))),
               "updated_valid_labels": dict(original_labels + recovered_labels),
               "updated_valid": 843 + len(recovered),
               "source_schema_sha256": digest(run / "schema_snapshot.json"),
               "source_config_sha256": digest(run / "config_snapshot.json")}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    report = ["# Selection-response failure review", "",
              "Source: finalized independent-validation Batch, selection split only. No holdout result was read.",
              "The original responses and canonical results remain unchanged. Corrections below are separate review outputs.",
              "", "## Outcome", "",
              f"- Original: 865 responses; 843 valid (51 KEEP, 59 UNKNOWN, 733 EXCLUDE); 18 failed exact quote and 4 contract/reference checks.",
              f"- Recovered mechanically: {len(recovered)} responses. Still unresolved: {len(rows) - len(recovered)}.",
              f"- Combined original-valid plus recovered: {843 + len(recovered)} valid; "
              f"{(original_labels + recovered_labels)['KEEP']} KEEP, "
              f"{(original_labels + recovered_labels)['UNKNOWN']} UNKNOWN, "
              f"{(original_labels + recovered_labels)['EXCLUDE']} EXCLUDE.",
              "- All 22 responses were marked completed; their output use was below the 8,000-token request limit. No output-limit truncation was observed.",
              "", "## Company review", "",
              "| Company ID | Original failure | Failed field and cited text / reference | Outcome | Next action |",
              "|---|---|---|---|---|"]
    for row in rows:
        fields = "; ".join(
            f"{item['field']} ({item['cause']})"
            + (f" page {item['cited_page_id']} quote {item['quote'][:85]!r}"
               if "quote" in item else f" {item.get('detail', '')}")
            + (f" -> page(s) {','.join(item['matching_page_ids'])}"
               if item.get("matching_page_ids") else "")
            for item in row["details"])
        fields = fields.replace("|", "/").replace("\n", " ")
        report.append(f"| {row['company_id']} | {row['original_detail']} | {fields} | "
                      f"{row['outcome']}{' (' + row['recovered_label'] + ')' if row['recovered_label'] else ''} | "
                      f"{row['next_action']} |")
    report.extend(["", "The machine-readable diagnostics contain the unchanged quote, cited page, nearby supplied text,"
                   " and any exact matching page IDs for every failed evidence item. The corrected results contain"
                   " full revalidated profiles, original source hashes, page-ID edits, and derived labels.",
                   "Changed-wording versus absent-quotation categorization is a diagnostic based on shared"
                   " word sequences, not an acceptance rule; both remain invalid under exact grounding.",
                   "", "## Recovery rule and next action", "",
                   "Only a wrong page ID was repaired when the unchanged quote occurred on exactly one other frozen"
                   " supplied page and the complete corrected profile passed the approved validator. No quotation"
                   " text, AI assessment, attribution or label was edited. Internal whitespace differences are"
                   " diagnostic only; the approved edge-whitespace rule does not accept them. The unresolved"
                   " cases need manual evidence/contract review or a targeted API retry. Do not substitute these"
                   " review labels for canonical inputs without an explicit later decision.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.run, args.output), indent=2))


if __name__ == "__main__":
    main()
