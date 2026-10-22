"""Read-only review of independent-pool/development exact-content matches."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from .company_modeling_common import file_hash
from .company_modeling_sources import load_sources
from .prepare_ai_profile_validation_batch import DATASET, DEFAULT_OUTPUT


def excerpt(text: str, limit: int = 280) -> str:
    return " ".join(str(text).split())[:limit]


OUTPUT = Path("data/interim/calibration/ai_profile_overlap_review_v1")


def material_kind(text: str) -> str:
    """Categorize the reviewed exact texts; fail closed on an unfamiliar match."""
    value = " ".join(text.casefold().split())
    prefixes = (
        ("um dir ein optimales erlebnis", "cookie_consent"),
        ("um ihnen ein optimales erlebnis", "cookie_consent"),
        ("cookie-zustimmung verwalten", "cookie_consent"),
        ("diese website verwendet cookies", "cookie_consent"),
        ("informationen zu cookies und datenschutz", "cookie_consent"),
        ("weitere infos datenschutzerklärung", "cookie_consent"),
        ("die an dieser stelle vorgesehenen inhalte", "embedded_content_placeholder"),
        ("sie sehen gerade einen platzhalterinhalt", "embedded_content_placeholder"),
        ("you are currently viewing a placeholder", "embedded_content_placeholder"),
        ("für die verwendung von youtube videos", "embedded_content_placeholder"),
        ("sie müssen den inhalt von recaptcha", "embedded_content_placeholder"),
        ("online geschlossene verträge", "standard_withdrawal_notice"),
        ("this is an example page", "cms_sample_page"),
        ("this is a sample page. overview our refund", "storefront_sample_policy"),
        ("welcome to wordpress", "cms_sample_page"),
        ("if your post contains", "theme_demo"),
        ("this format can be used", "theme_demo"),
        ("this format perfectly fits", "theme_demo"),
        ("aliquam dapibus", "theme_demo"),
        ("lorem ipsum", "theme_demo"),
        ("you need to enable javascript", "access_placeholder"),
        ("your browser is currently set to block cookies", "access_placeholder"),
        ("anmelden warnung: dieser webmail-service", "access_placeholder"),
        ("loader please wait", "access_placeholder"),
        ("redirecting... if you are not redirected", "redirect_placeholder"),
    )
    for prefix, kind in prefixes:
        if value.startswith(prefix):
            return kind
    raise ValueError(f"Unreviewed shared text: {value[:100]}")


def collect(root: Path) -> list[dict]:
    directory = root / DEFAULT_OUTPUT
    manifest = [json.loads(line) for line in (directory / "company_manifest.jsonl").open(encoding="utf-8")]
    withheld = [row for row in manifest if row["status"] == "unresolved"]
    if len(withheld) != 64 or len({row["company_id"] for row in withheld}) != 64:
        raise ValueError("Expected exactly 64 unresolved companies from 5.4w")
    dataset = root / DATASET
    dev_companies = pd.read_parquet(dataset / "companies.parquet").to_dict("records")
    dev_pages = pd.read_parquet(dataset / "pages.parquet").to_dict("records")
    dev_contents = pd.read_parquet(dataset / "contents.parquet").to_dict("records")
    dev_relations = pd.read_parquet(dataset / "company_page_features.parquet").to_dict("records")
    visibility = pd.read_parquet(dataset / "audit/historical_visibility.parquet").to_dict("records")
    dev_name = {r["bvd_id"]: r["company_name"] for r in dev_companies}
    dev_domain = {r["bvd_id"]: r["normalized_domain"] for r in dev_companies}
    dev_pages_by_content = defaultdict(list)
    for page in dev_pages:
        dev_pages_by_content[page["content_id"]].append(page)
    dev_company_page = defaultdict(list)
    for relation in dev_relations:
        dev_company_page[relation["scraped_page_id"]].append(relation)
    dev_visible = {(r["bvd_id"], r["scraped_page_id"]): r for r in visibility}
    dev_text = {r["content_id"]: r for r in dev_contents}
    source = load_sources(resolve_database_path(root), root,
                          {"extraction_version": "v1", "calibration_runs": []},
                          company_ids=[r["company_id"] for r in withheld])
    if source["errors"]:
        raise ValueError(f"Unreadable validation evidence: {source['errors'][:3]}")
    val_companies = {r["bvd_id"]: r for r in source["companies"]}
    val_pages = defaultdict(list)
    for page in source["pages"]:
        val_pages[page["website_id"]].append(page)
    training_ids = set()
    preprocessing_ids = set()
    import joblib
    frozen = root / "data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen"
    for name in ("reference_logistic_size", "cleaned_grounded_combined"):
        provenance = joblib.load(frozen / name / "model.joblib").provenance
        training_ids.update(provenance["training_company_ids"])
        preprocessing_ids.update(provenance["preprocessing_company_ids"])
    result = []
    for row in withheld:
        cid = row["company_id"]
        company = val_companies[cid]
        domain = company["normalized_domain"]
        matches = []
        for page in val_pages[domain]:
            if page["content_id"] not in dev_pages_by_content:
                continue
            content = dev_text[page["content_id"]]
            for counterpart in dev_pages_by_content[page["content_id"]]:
                for relation in dev_company_page[counterpart["scraped_page_id"]]:
                    dev_id = relation["bvd_id"]
                    seen = dev_visible.get((dev_id, counterpart["scraped_page_id"]))
                    matches.append({"content_id": page["content_id"],
                                    "validation_page_id": page["scraped_page_id"],
                                    "validation_url": page["url"], "validation_final_url": page["final_url"],
                                    "validation_final_host": page["final_host"],
                                    "development_company_id": dev_id,
                                    "development_company_name": dev_name[dev_id],
                                    "development_original_domain": dev_domain[dev_id],
                                    "development_domain": counterpart["website_id"],
                                    "development_page_id": counterpart["scraped_page_id"],
                                    "development_url": counterpart["url"],
                                    "development_final_url": counterpart["final_url"],
                                    "development_final_host": counterpart["final_host"],
                                    "word_count": content["word_count"],
                                    "material_kind": material_kind(content["body_text"]),
                                    "excerpt": excerpt(content["body_text"]),
                                    "development_predictive_eligible": bool(relation["predictive_eligible"]),
                                    "development_used_in_frozen_training": bool(relation["predictive_eligible"] and dev_id in training_ids),
                                    "development_used_in_frozen_preprocessing": bool(relation["predictive_eligible"] and dev_id in preprocessing_ids),
                                    "historical_calibration_visible": bool(seen and seen["historical_content_status"] in ("complete", "partially_truncated")),
                                    "historical_content_status": seen["historical_content_status"] if seen else "not_in_package"})
        if not matches:
            raise ValueError(f"No development counterpart for withheld company {cid}")
        result.append({"company_id": cid, "company_name": company["company_name"],
                       "original_domain": domain, "matches": matches,
                       "known_final_hosts": sorted({p["final_host"] for p in val_pages[domain] if p["final_host"]}),
                       "audit_clear_reasons": row["clear_reasons"],
                       "distinct_shared_content": len({m["content_id"] for m in matches}),
                       "validation_pages": len(val_pages[domain]),
                       "development_counterparts": sorted({m["development_company_id"] for m in matches})})
    return result


def write_review(root: Path, cases: list[dict]) -> None:
    directory = root / OUTPUT
    if directory.exists():
        previous = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        if previous["source_manifest_sha256"] != file_hash(root / DEFAULT_OUTPUT / "company_manifest.jsonl"):
            raise ValueError("Existing review has a different source manifest")
    if len(cases) != 64 or len({case["company_id"] for case in cases}) != 64:
        raise ValueError("Exactly one case per withheld company is required")
    rows = []
    details = []
    for case in sorted(cases, key=lambda item: item["company_id"]):
        if case["audit_clear_reasons"]:
            raise ValueError(f"Clear exclusion encountered: {case['company_id']}")
        matches = sorted(case["matches"], key=lambda m: (m["content_id"], m["validation_page_id"],
                                                         m["development_company_id"], m["development_page_id"]))
        kinds = sorted({m["material_kind"] for m in matches})
        # The complete matched texts were reviewed as generic. Unknown material fails in material_kind.
        decision = "INCLUDE"
        reason = ("Exact overlap is limited to " + ", ".join(kinds).replace("_", " ") +
                  "; original/redirect hosts and the prior exact-URL/material-group audit show "
                  "no development website-family or substantive evidence overlap. "
                  "Matched pages are retained, not silently removed.")
        representative = max(matches, key=lambda m: (m["word_count"], -m["validation_page_id"]))
        representative_pairs = {}
        for match in matches:
            key = (match["content_id"], match["development_company_id"])
            if key not in representative_pairs:
                representative_pairs[key] = dict(match)
            else:
                for flag in ("development_used_in_frozen_training", "development_used_in_frozen_preprocessing",
                             "historical_calibration_visible"):
                    representative_pairs[key][flag] |= match[flag]
        counterparts = sorted({(m["development_company_id"], m["development_company_name"],
                                m["development_original_domain"]) for m in matches})
        rows.append({"company_id": case["company_id"], "company_name": case["company_name"],
                     "original_domain": case["original_domain"],
                     "known_final_hosts": json.dumps(case["known_final_hosts"], ensure_ascii=False),
                     "validation_page_count": case["validation_pages"],
                     "shared_content_items": case["distinct_shared_content"],
                     "shared_material_kinds": "; ".join(kinds),
                     "development_counterparts": json.dumps(counterparts, ensure_ascii=False),
                     "representative_matched_url_pairs": json.dumps(sorted({(m["validation_url"], m["validation_final_url"],
                         m["development_url"], m["development_final_url"]) for m in representative_pairs.values()}), ensure_ascii=False),
                     "representative_validation_url": representative["validation_final_url"],
                     "representative_development_url": representative["development_final_url"],
                     "representative_excerpt": representative["excerpt"],
                     "any_matched_page_frozen_training_eligible": any(m["development_used_in_frozen_training"] for m in matches),
                     "any_matched_page_frozen_preprocessing_eligible": any(m["development_used_in_frozen_preprocessing"] for m in matches),
                     "any_matched_page_historical_calibration_visible": any(m["historical_calibration_visible"] for m in matches),
                     "decision": decision, "justification": reason})
        details.extend({"validation_company_id": case["company_id"], **m} for m in representative_pairs.values())
    counts = Counter(row["decision"] for row in rows)
    if set(counts) - {"INCLUDE", "EXCLUDE", "UNRESOLVED"} or sum(counts.values()) != 64:
        raise ValueError("Invalid review decision inventory")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "decision_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (directory / "match_details.jsonl").open("w", encoding="utf-8") as handle:
        for match in details:
            handle.write(json.dumps(match, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {"source_manifest_sha256": file_hash(root / DEFAULT_OUTPUT / "company_manifest.jsonl"),
               "development_pages_sha256": file_hash(root / DATASET / "pages.parquet"),
               "reviewed_companies": 64, "decisions": {key: counts.get(key, 0) for key in
                                                ("INCLUDE", "EXCLUDE", "UNRESOLVED")},
               "distinct_shared_content_items": len({m["content_id"] for m in details}),
               "representative_matched_page_pairs": len(details),
               "material_kinds": dict(sorted(Counter(m["material_kind"] for m in details).items()))}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# 5.4x: Review of possible development-content overlaps", "",
             "All 64 companies withheld as `unresolved` in 5.4w were reviewed against frozen development content and saved validation extraction. This review does not alter requests or split assignments.", "",
             f"**Decisions:** {counts.get('INCLUDE', 0)} INCLUDE, {counts.get('EXCLUDE', 0)} EXCLUDE, {counts.get('UNRESOLVED', 0)} UNRESOLVED.", "",
             "The two previously clear exclusions (`AT9030118034`, `AT9150093010`) remain excluded and are not part of these 64. The 5.4w exact-URL/host-family/material-group audit found no clear overlap for these cases. All 41 distinct exact shared texts are generic notices, CMS/theme examples, policy templates or access placeholders. This is an independence review, not an AI label or a guarantee against undiscovered near-duplicates.", "",
             "A matched page marked training-eligible belonged to a supervised development company and was predictive-eligible in the frozen dataset; that establishes input eligibility, not a nonzero fitted coefficient. Preprocessing eligibility is likewise separate from supervised fitting. Historical visibility records whether the matched text appeared in a calibration package.", "",
             f"Matched material was frozen-training-input eligible for {sum(r['any_matched_page_frozen_training_eligible'] for r in rows)} companies, preprocessing eligible for {sum(r['any_matched_page_frozen_preprocessing_eligible'] for r in rows)}, and visible in historical calibration packages for {sum(r['any_matched_page_historical_calibration_visible'] for r in rows)}. Its generic nature does not turn these companies into the same business.", "",
             "| Validation company | Original domain | Shared items / reviewed kind | Development counterpart(s) | Representative validation URL | Representative development URL | Text excerpt | Frozen training input eligible? | Decision / reason |",
             "|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        counterpart_domains = ", ".join(sorted({item[2] for item in json.loads(row["development_counterparts"])}))
        excerpt_md = row["representative_excerpt"].replace("|", "\\|").replace("\n", " ")[:125]
        lines.append(f"| {row['company_id']} | {row['original_domain']} | {row['shared_content_items']}; {row['shared_material_kinds']} | {counterpart_domains} | {row['representative_validation_url']} | {row['representative_development_url']} | {excerpt_md} | {'yes' if row['any_matched_page_frozen_training_eligible'] else 'no'} | {row['decision']}: generic shared material only |")
    lines.extend(["", "Full original/final URL pairs, counterpart IDs/names, excerpts, historical visibility, and per-page frozen-input flags are in `decision_table.csv` and `match_details.jsonl`.", "",
                  "## Cases needing user review", "", "None from the 64 exact-content flags. Existing near-duplicate candidate detection was not exhaustive, so independent validation still needs the broader pre-labeling checks noted in 5.4w.", "",
                  "## Important larger matches", "",
                  "`hotel-in-graz.com` shares 16 extracted items with `t4a-automation.com`, but all are CMS/theme demonstration posts or lorem ipsum, not company/product evidence; the validation website has 158 extracted pages. `brantner.com` shares a 578-word stock refund/returns sample page with `hmw-mobility.com`; its other 116 extracted pages were not exact matches. Neither match was stripped from evidence.", ""])
    (directory / "review_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(directory), **summary}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Print all 64 case summaries without saving a review")
    parser.add_argument("--compact", action="store_true", help="Print a brief line per case")
    parser.add_argument("--company-id", help="Inspect one withheld company in detail")
    parser.add_argument("--write-review", action="store_true", help="Write the reviewed decisions in a new directory")
    args = parser.parse_args()
    root = find_project_root()
    cases = collect(root)
    if args.write_review:
        write_review(root, cases)
    if args.inspect:
        for case in cases:
            if args.company_id and case["company_id"] != args.company_id:
                continue
            unique = list({m["content_id"]: m for m in case["matches"]}.values())
            if args.compact:
                print(f"{case['company_id']} {case['original_domain']} shared={case['distinct_shared_content']} "
                      f"pages={case['validation_pages']} "
                      f"dev={','.join(sorted({m['development_domain'] for m in case['matches']}))} "
                      f"text={excerpt(unique[0]['excerpt'], 95)}")
                continue
            if args.company_id:
                for match in unique:
                    print(json.dumps({"id": case["company_id"], "url": match["validation_final_url"],
                                      "development": match["development_final_url"],
                                      "words": match["word_count"], "text": match["excerpt"]},
                                     ensure_ascii=False))
                continue
            print(json.dumps({"id": case["company_id"], "domain": case["original_domain"],
                              "counterparts": sorted({m["development_domain"] for m in case["matches"]}),
                              "shared": case["distinct_shared_content"],
                              "examples": [{"words": m["word_count"], "text": excerpt(m["excerpt"], 155),
                                            "url": m["validation_final_url"],
                                            "dev_url": m["development_final_url"],
                                            "training": m["development_used_in_frozen_training"]}
                                           for m in unique[:3]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
