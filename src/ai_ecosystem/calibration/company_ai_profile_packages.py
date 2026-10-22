"""Deterministic, blinded ten-company profile pilot selection and packaging."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import yaml

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from ai_ecosystem.discovery.normalization import normalize_url
from .build_company_evidence_packages import calibration_page_sort_key
from .company_ai_profile_contract import canonical, digest, prompt_and_schema, request_body
from .company_ai_profile_tokens import (count_input_tokens, local_tokens, non_evidence_breakdown,
                                         reservation_usd, token_breakdown)
from .company_modeling_common import host
from .company_modeling_sources import load_sources


LEGACY_CONFIG = "config/company_ai_profile_pilot.yaml"
LEGACY_OUTPUT = "data/interim/calibration/ai_profile_pilot_v1"
PREVIOUS_OUTPUT = "data/interim/calibration/ai_profile_pilot_v2_verified"
DEFAULT_CONFIG = "config/company_ai_profile_pilot_v2.yaml"
DEFAULT_OUTPUT = "data/interim/calibration/ai_profile_pilot_v3_product_priority"
SCORE_TABLE = "data/interim/calibration/company_score_analysis/independent_validation_v1/company_score_ranks.csv"
SOURCE_SCORES = "data/interim/calibration/company_scores/independent_validation_v1.csv"
DATASET = "data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1"
GROUP_QUOTAS = {"both_top_100": 4, "reference_only_top_100": 2,
                "challenger_only_top_100": 2, "outside_both_top_100": 2}
PRODUCT_SERVICE_SOLUTION_COMPONENTS = frozenset({
    "product", "products", "produkt", "produkte",
    "service", "services", "dienstleistung", "dienstleistungen", "leistung", "leistungen",
    "solution", "solutions", "loesung", "loesungen", "lösung", "lösungen",
})
PROFILE_PAGE_ORDERING = "root_about_product_service_solution_then_depth_page_id_v1"


def load_config(root: Path, path: Path | None = None) -> dict:
    raw = yaml.safe_load((path or root / DEFAULT_CONFIG).read_text(encoding="utf-8"))
    if (raw["contract_id"], raw["mapping_version"]) not in {
            ("company_ai_profile_v1", "company_ai_profile_mapping_v1"),
            ("company_ai_profile_v2", "company_ai_profile_mapping_v2"),
            ("company_ai_profile_v3", "company_ai_profile_mapping_v3"),
            ("company_ai_profile_v4", "company_ai_profile_mapping_v4")}:
        raise ValueError("Unsupported profile contract or label mapping")
    return raw


def tokens_upper_bound(text: str) -> int:
    """Legacy v1 metric, retained for reading old pilot artifacts only."""
    return len(text.encode("utf-8"))


def normalized_url_or_raw(url: str) -> str:
    try:
        return normalize_url(url) or url
    except ValueError:
        return url


def utility_reason(url: str, config: dict) -> str | None:
    components = [unquote(x).casefold() for x in urlsplit(url).path.split("/") if x]
    if any(x in config["utility_exceptions"] for x in components):
        return None
    # A full path component is required; an article titled "privacy in AI" survives.
    return next((x for x in components if x in config["utility_path_components"]), None)


def is_product_service_solution_url(url: str) -> bool:
    components = (unquote(part).casefold() for part in urlsplit(url).path.split("/") if part)
    return any(part in PRODUCT_SERVICE_SOLUTION_COMPONENTS for part in components)


def ai_profile_page_sort_key(page: dict) -> tuple[int, int, int]:
    """Apply the pilot-only product tier after the existing root/about tiers."""
    url = str(page.get("final_url") or page.get("normalized_url") or page.get("url") or "")
    priority, depth, page_id = calibration_page_sort_key({**page, "normalized_url": url})
    if priority == 2 and not is_product_service_solution_url(url):
        priority = 3
    return priority, depth, page_id


def score_groups(path: Path, seed: int, development_domains: set[str],
                 source_scores: Path | None = None) -> tuple[dict[str, list[dict]], list[dict]]:
    records = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    if source_scores is not None:
        raw = {r["bvd_id"]: r for r in csv.DictReader(source_scores.open(encoding="utf-8", newline=""))}
        if len(raw) != len(records) or any(
            row["bvd_id"] not in raw or row["state"] != raw[row["bvd_id"]]["state"] or any(
                not math.isclose(float(row[key]), float(raw[row["bvd_id"]][key]), rel_tol=1e-12, abs_tol=1e-12)
                for key in ("reference_logistic_size", "cleaned_grounded_combined") if row["state"] == "PROCESSABLE"
            ) for row in records
        ):
            raise ValueError("Score analysis table differs from frozen independent-pool scores")
    scored = [r for r in records if r["state"] == "PROCESSABLE"]
    if len(records) != 1500 or len(scored) != 1084 or len({r["bvd_id"] for r in records}) != 1500:
        raise ValueError("Unexpected independent-pool score membership")
    ref = {r["bvd_id"] for r in sorted(scored, key=lambda r: (-float(r["reference_logistic_size"]), r["bvd_id"]))[:100]}
    challenger = {r["bvd_id"] for r in sorted(scored, key=lambda r: (-float(r["cleaned_grounded_combined"]), r["bvd_id"]))[:100]}
    buckets = {name: [] for name in GROUP_QUOTAS}
    excluded = []
    for row in scored:
        cid, domain = row["bvd_id"], row["normalized_domain"]
        if domain in development_domains:
            excluded.append({"company_id": cid, "reason": "known_development_domain"})
            continue
        group = ("both_top_100" if cid in ref and cid in challenger else
                 "reference_only_top_100" if cid in ref else
                 "challenger_only_top_100" if cid in challenger else "outside_both_top_100")
        buckets[group].append(row)
    rng = random.Random(seed)
    for rows in buckets.values():
        rows.sort(key=lambda r: r["bvd_id"])
        rng.shuffle(rows)
    return buckets, excluded


def choose_companies(buckets: dict[str, list[dict]], quotas: dict[str, int] = GROUP_QUOTAS) -> list[dict]:
    chosen, used_domains = [], set()
    for group, quota in quotas.items():
        candidates = [r for r in buckets[group] if r["normalized_domain"] not in used_domains]
        for row in candidates[:quota]:
            chosen.append({"company_id": row["bvd_id"], "company_name": row["company_name"],
                           "normalized_domain": row["normalized_domain"], "selection_group": group})
            used_domains.add(row["normalized_domain"])
        if len(candidates) < quota:
            raise ValueError(f"Shortage in {group}: {len(candidates)} < {quota}; allocations unchanged")
    return chosen


def _visible(package: dict) -> dict:
    return {"company_id": package["company_id"], "company_name": package["company_name"],
            "normalized_domain": package["normalized_domain"], "shared_website": package["shared_website"],
            "coverage": package["coverage"],
            "pages": [{k: p[k] for k in ("page_id", "url", "title", "text", "truncated")}
                      for p in package["pages"]]}


def prepare_profile_candidates(records: list[dict], root: Path, config: dict) -> tuple[list[dict], list[dict]]:
    """Read and deduplicate one website once before comparing multiple budgets."""
    records.sort(key=ai_profile_page_sort_key)
    groups: dict[str, dict] = {}
    excluded = []
    for record in records:
        url = record["final_url"] or record["url"]
        reason = utility_reason(url, config)
        if reason:
            excluded.append({"page_id": str(record["scraped_page_id"]), "url": url, "reason": f"standalone_utility:{reason}"})
            continue
        text = (root / record["text_path"]).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            continue
        key = hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
        provenance = {"page_id": str(record["scraped_page_id"]), "url": url}
        if key in groups:
            groups[key]["provenance"].append(provenance)
        else:
            groups[key] = {"page_id": provenance["page_id"], "url": url, "title": record["title"] or "",
                           "text": text, "truncated": False, "provenance": [provenance],
                           "body_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                           "content_id": record["content_id"], "word_count": len(text.split())}
    return list(groups.values()), excluded


def allocate_profile_candidates(company: dict, website: dict, records: list[dict],
                                candidates: list[dict], excluded: list[dict], config: dict) -> dict:
    domain = company["normalized_domain"]
    available = len(candidates)
    base = {"company_id": company["company_id"], "company_name": company["company_name"],
            "normalized_domain": domain, "shared_website": bool(website["mapped_company_count"] > 1),
            "coverage": {}, "pages": []}
    cap = int(config["website_evidence_token_cap"])
    count = (lambda text: local_tokens(text, config)) if "token_counting" in config else tokens_upper_bound
    # Reserve summary digits and omitted counts before filling the page section.
    base["coverage"] = {"available_unique_pages": available, "included_pages": available,
                        "excluded_utility_pages": len(excluded), "duplicate_page_records": len(records)-len(excluded)-available,
                        "omitted_pages": available, "text_truncated_pages": 1}
    selected, omitted = [], []
    oversized_first = None
    for page in candidates:
        public = {k: page[k] for k in ("page_id", "url", "title", "text", "truncated")}
        probe = {**base, "pages": selected + [public]}
        if count(canonical(_visible(probe))) <= cap:
            selected.append(public)
            continue
        if not selected and oversized_first is None:
            oversized_first = public
        omitted.append({"page_id": page["page_id"], "url": page["url"], "reason": "token_budget"})
    # Let later, smaller whole pages fit before using a partial first page.
    if not selected and oversized_first is not None:
        if count(canonical(_visible({**base, "pages": [{**oversized_first, "text": ""}]}))) >= cap:
            raise ValueError("Page metadata alone exceeds evidence budget")
        low, high = 0, len(oversized_first["text"])
        while low < high:
            mid = (low + high + 1) // 2
            prefix = oversized_first["text"][:mid] + "\n[PAGE TEXT TRUNCATED BY TOKEN BUDGET]"
            probe = {**base, "pages": [{**oversized_first, "text": prefix, "truncated": True}]}
            if count(canonical(_visible(probe))) <= cap:
                low = mid
            else:
                high = mid - 1
        selected.append({**oversized_first, "text": oversized_first["text"][:low] +
                         "\n[PAGE TEXT TRUNCATED BY TOKEN BUDGET]", "truncated": True})
        omitted = [item for item in omitted if item["page_id"] != oversized_first["page_id"]]
    base["pages"] = selected
    base["coverage"] = {"available_unique_pages": available, "included_pages": len(selected),
                        "excluded_utility_pages": len(excluded), "duplicate_page_records": len(records)-len(excluded)-available,
                        "omitted_pages": len(omitted), "text_truncated_pages": sum(p["truncated"] for p in selected)}
    if count(canonical(_visible(base))) > cap:
        raise ValueError("Website evidence exceeded local allocation cap after coverage update")
    estimator = ("o200k_base_local_allocation_proxy_not_luna_verified" if "token_counting" in config
                 else "utf8_byte_upper_bound_not_model_tokenizer")
    base["audit"] = {"excluded": excluded, "omitted": omitted,
                     "page_provenance": {p["page_id"]: p["provenance"] for p in candidates},
                     "content_hashes": {p["page_id"]: p["body_hash"] for p in candidates},
                     "content_ids": {p["page_id"]: p["content_id"] for p in candidates},
                     "word_counts": {p["page_id"]: p["word_count"] for p in candidates},
                     "token_estimator": estimator,
                     "website_evidence_allocation_tokens": count(canonical(_visible(base))),
                     "included_text_sha256": hashlib.sha256(canonical(selected).encode("utf-8")).hexdigest()}
    if "token_counting" not in config:
        base["audit"]["website_evidence_token_upper_bound"] = base["audit"]["website_evidence_allocation_tokens"]
    return base


def package_company(company: dict, source: dict, root: Path, config: dict) -> dict:
    domain = company["normalized_domain"]
    website = next(w for w in source["websites"] if w["website_id"] == domain)
    records = [p for p in source["pages"] if p["website_id"] == domain and p["usable"]]
    candidates, excluded = prepare_profile_candidates(records, root, config)
    return allocate_profile_candidates(company, website, records, candidates, excluded, config)


def enforce_exact_evidence_budget(package: dict, config: dict, prompt: str, schema: dict,
                                  client, non_evidence_tokens: int) -> dict:
    """Verify the frozen request; remove lowest-priority included pages if needed."""
    cap = int(config["website_evidence_token_cap"])
    while True:
        body = request_body(package, config, prompt, schema)
        counts = token_breakdown(client, body, non_evidence_tokens)
        if counts["website_evidence_tokens_exact"] <= cap:
            break
        if not package["pages"]:
            raise ValueError(f"Company metadata alone exceeds evidence budget: {package['company_id']}")
        removed = package["pages"].pop()
        package["audit"]["omitted"].append({"page_id": removed["page_id"], "url": removed["url"],
                                            "reason": "exact_model_token_budget"})
        package["coverage"]["included_pages"] = len(package["pages"])
        package["coverage"]["omitted_pages"] = len(package["audit"]["omitted"])
        package["coverage"]["text_truncated_pages"] = sum(p["truncated"] for p in package["pages"])
    package["audit"]["website_evidence_allocation_tokens"] = local_tokens(canonical(_visible(package)), config)
    package["audit"]["included_text_sha256"] = hashlib.sha256(canonical(package["pages"]).encode("utf-8")).hexdigest()
    package["audit"]["website_evidence_tokens_exact"] = counts["website_evidence_tokens_exact"]
    return counts


def prepare(root: Path | None = None, output: Path | None = None, *, token_client=None,
            source_directory: Path | None = None) -> dict:
    root = root or find_project_root()
    output = output or root / DEFAULT_OUTPUT
    if output.exists():
        raise FileExistsError(f"Pilot preparation already exists: {output}")
    config = load_config(root)
    if config.get("token_counting", {}).get("method") != "responses_input_tokens_count_v1":
        raise ValueError("New pilot requires model-specific Responses token counting")
    prompt, schema = prompt_and_schema()
    dataset = root / DATASET
    historical = pd.read_parquet(dataset / "companies.parquet")
    development_domains = set(historical.normalized_domain.dropna())
    buckets, excluded = score_groups(root / SCORE_TABLE, int(config["seed"]), development_domains,
                                     root / SOURCE_SCORES)
    manifest = choose_companies(buckets)
    frozen_config = yaml.safe_load((dataset / "config_snapshot.json").read_text(encoding="utf-8"))
    source = load_sources(resolve_database_path(root), root, frozen_config, company_ids=[m["company_id"] for m in manifest])
    if source["errors"]:
        raise ValueError(f"Selected source text failed to load: {source['errors'][:3]}")
    packages = [package_company(m, source, root, config) for m in manifest]
    dev_pages = pd.read_parquet(dataset / "pages.parquet")
    dev_urls = {normalized_url_or_raw(str(x)) for col in ("url", "final_url") if col in dev_pages for x in dev_pages[col].dropna()}
    dev_contents = pd.read_parquet(dataset / "contents.parquet", columns=["content_id", "word_count"])
    substantive = set(dev_contents.loc[dev_contents.word_count >= 100, "content_id"])
    overlap = []
    replacements = []
    rejected_ids = set()
    for index, company in enumerate(manifest):
        current_source = source
        while True:
            pages = [p for p in current_source["pages"] if p["website_id"] == company["normalized_domain"]]
            redirects = sorted({p["final_host"] for p in pages if p["final_host"] in development_domains})
            urls = sorted({p["url"] for p in pages if normalized_url_or_raw(p["url"]) in dev_urls})
            if not redirects and not urls:
                break
            rejected_ids.add(company["company_id"])
            taken = {m["company_id"] for m in manifest}
            domains_taken = {m["normalized_domain"] for m in manifest}
            replacement = next((r for r in buckets[company["selection_group"]]
                                if r["bvd_id"] not in taken and r["bvd_id"] not in rejected_ids
                                and r["normalized_domain"] not in domains_taken), None)
            if replacement is None:
                raise ValueError(f"No uncontaminated replacement in {company['selection_group']}")
            replacements.append({"removed_company_id": company["company_id"], "replacement_company_id": replacement["bvd_id"],
                                 "reason": "confirmed_development_redirect_or_exact_url", "redirect_hosts": redirects,
                                 "exact_urls": urls})
            company = {"company_id": replacement["bvd_id"], "company_name": replacement["company_name"],
                       "normalized_domain": replacement["normalized_domain"], "selection_group": company["selection_group"]}
            manifest[index] = company
            current_source = load_sources(resolve_database_path(root), root, frozen_config, company_ids=[company["company_id"]])
            if current_source["errors"]:
                raise ValueError(f"Replacement source failed to load: {company['company_id']}")
            packages[index] = package_company(company, current_source, root, config)
        package = packages[index]
        hashes = sorted({p["content_id"] for p in pages if p["content_id"] in substantive})
        overlap.append({"company_id": company["company_id"], "known_development_domain": False,
                        "known_development_redirect_hosts": redirects, "known_exact_urls": urls,
                        "shared_substantive_content_hashes": hashes,
                        "status": "uncertain_content_overlap" if hashes else "no_known_overlap"})
    source_directory = source_directory or root / PREVIOUS_OUTPUT
    prior = json.loads((source_directory / "selection_manifest.json").read_text(encoding="utf-8"))
    if [(m["company_id"], m["normalized_domain"], m["selection_group"]) for m in manifest] != [
            (m["company_id"], m["normalized_domain"], m["selection_group"]) for m in prior["companies"]]:
        raise ValueError("Pilot selection changed relative to the frozen ten-company comparison manifest")
    if prompt != (source_directory / "prompt_snapshot.txt").read_text(encoding="utf-8") or canonical(schema) != canonical(
            json.loads((source_directory / "response.schema.json").read_text(encoding="utf-8"))):
        raise ValueError("Pilot prompt/schema changed relative to the comparison package")
    if token_client is None:
        from openai import OpenAI
        from ai_ecosystem.validation.config import load_openai_api_key
        token_client = OpenAI(api_key=load_openai_api_key(root), timeout=config["request_timeout_seconds"], max_retries=1)
    baseline_body = request_body(packages[0], config, prompt, schema)
    non_evidence = non_evidence_breakdown(token_client, baseline_body)
    non_evidence_tokens = non_evidence["non_evidence_input_tokens_exact"]
    counts_by_company = {}
    comparison = []
    old_config = json.loads((source_directory / "config_snapshot.json").read_text(encoding="utf-8"))
    old_requests = {row["company_id"]: row for row in json.loads(
        (source_directory / "requests.json").read_text(encoding="utf-8"))}
    for package in packages:
        old_package = json.loads((source_directory / "packages" / f"{package['company_id']}.json").read_text(encoding="utf-8"))
        old_body = request_body(old_package, old_config, prompt, schema)
        old_row = old_requests[package["company_id"]]
        if digest(old_body) != old_row["request_hash"]:
            raise ValueError(f"Comparison request changed: {package['company_id']}")
        old_input_tokens = old_row.get("request_input_tokens_exact")
        if old_input_tokens is None:
            old_input_tokens = count_input_tokens(token_client, old_body)
        counts = enforce_exact_evidence_budget(package, config, prompt, schema, token_client, non_evidence_tokens)
        if counts["request_input_tokens_exact"] + int(config["max_output_tokens"]) > int(config["max_context_tokens"]):
            raise ValueError(f"Request may exceed context: {package['company_id']}")
        counts_by_company[package["company_id"]] = counts
        before_ids = [page["page_id"] for page in old_package["pages"]]
        after_ids = [page["page_id"] for page in package["pages"]]
        before_set, after_set = set(before_ids), set(after_ids)
        comparison.append({"company_id": package["company_id"],
                           "normalized_domain": package["normalized_domain"],
                           "before_included_pages": len(old_package["pages"]),
                           "before_omitted_pages": old_package["coverage"]["omitted_pages"],
                           "before_request_input_tokens_exact": old_input_tokens,
                           "before_reservation_usd": reservation_usd(config, old_input_tokens),
                           "before_product_service_solution_pages": sum(
                               is_product_service_solution_url(page["url"]) for page in old_package["pages"]),
                           "after_included_pages": len(package["pages"]),
                           "after_omitted_pages": package["coverage"]["omitted_pages"],
                           "after_request_input_tokens_exact": counts["request_input_tokens_exact"],
                           "after_website_evidence_tokens_exact": counts["website_evidence_tokens_exact"],
                           "after_reservation_usd": reservation_usd(config, counts["request_input_tokens_exact"]),
                           "after_product_service_solution_pages": sum(
                               is_product_service_solution_url(page["url"]) for page in package["pages"]),
                           "added_selected_pages": len(after_set - before_set),
                           "removed_selected_pages": len(before_set - after_set),
                           "selection_membership_changed": before_set != after_set,
                           "selected_page_order_changed": before_ids != after_ids})
        print(f"Token-counted {package['company_id']}: evidence={counts['website_evidence_tokens_exact']:,} "
              f"input={counts['request_input_tokens_exact']:,}; pages={len(package['pages'])}", flush=True)
    output.mkdir(parents=True)
    (output / "packages").mkdir()
    (output / "prompt_snapshot.txt").write_text(prompt, encoding="utf-8")
    (output / "response.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "config_snapshot.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "selection_manifest.json").write_text(json.dumps({"seed": config["seed"], "sample_id": config["sample_id"],
        "quotas": GROUP_QUOTAS, "page_ordering": PROFILE_PAGE_ORDERING,
        "comparison_directory": str(source_directory.relative_to(root)) if source_directory.is_relative_to(root) else str(source_directory),
        "companies": manifest, "selection_exclusions": excluded,
        "selection_replacements": replacements, "overlap_checks": overlap,
        "limitations": ["Redirect/content overlap checked against available development records only; full-pool audit pending.",
                        "A shared content hash alone may be boilerplate and is not proof of company identity."]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for package in packages:
        (output / "packages" / f"{package['company_id']}.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    requests = []
    for package in packages:
        body = request_body(package, config, prompt, schema)
        counts = counts_by_company[package["company_id"]]
        requests.append({"company_id": package["company_id"], "package_hash": digest(_visible(package)),
                         "request_hash": digest(body), "custom_id": f"profile-{package['company_id']}-{digest(body)[:16]}",
                         **counts, "allocation_local_tokens": package["audit"]["website_evidence_allocation_tokens"]})
    (output / "requests.json").write_text(json.dumps(requests, indent=2) + "\n", encoding="utf-8")
    (output / "before_after.json").write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    with (output / "before_after.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    (output / "build_summary.json").write_text(json.dumps({"companies": len(packages), "groups": dict(Counter(m["selection_group"] for m in manifest)),
        "page_ordering": PROFILE_PAGE_ORDERING,
        "total_request_input_tokens_exact": sum(r["request_input_tokens_exact"] for r in requests),
        "before_request_input_tokens_exact": sum(row["before_request_input_tokens_exact"] for row in comparison),
        "total_reservation_usd": sum(row["after_reservation_usd"] for row in comparison),
        "before_product_service_solution_pages": sum(row["before_product_service_solution_pages"] for row in comparison),
        "after_product_service_solution_pages": sum(row["after_product_service_solution_pages"] for row in comparison),
        "membership_changed_companies": [row["company_id"] for row in comparison if row["selection_membership_changed"]],
        "order_changed_companies": [row["company_id"] for row in comparison if row["selected_page_order_changed"]],
        **{f"{key}_per_request": value for key, value in non_evidence.items()},
        "prompt_local_tokens": local_tokens(prompt, config),
        "schema_local_tokens": local_tokens(canonical(schema), config),
        "output_tokens_reserved_per_request": int(config["max_output_tokens"]),
        "total_included_pages": sum(len(p["pages"]) for p in packages),
        "total_omitted_pages": sum(len(p["audit"]["omitted"]) for p in packages),
        "total_excluded_utility_pages": sum(len(p["audit"]["excluded"]) for p in packages),
        "uncertain_overlap_companies": [x["company_id"] for x in overlap if x["status"] != "no_known_overlap"],
        "token_count_method": "OpenAI Responses input_tokens.count on the exact model/request input/schema; "
                              "o200k_base used only to propose page allocation, not asserted as Luna tokenizer",
        "limitation": "The count endpoint excludes output tokens. The 8,000-token output allowance is reserved separately; "
                      "actual billing can differ with cache use, retries and model-side changes."}, indent=2) + "\n", encoding="utf-8")
    return {"manifest": manifest, "requests": requests, "packages": packages, "output": output}
