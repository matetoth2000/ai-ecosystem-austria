"""Evaluate frozen page rankers on the fully annotated locked company corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import joblib
import numpy as np
import pandas as pd
import scipy.sparse as sp
import yaml

from ai_ecosystem.calibration.attribution_features import build_relational_features, normalize_lexical
from ai_ecosystem.calibration.build_company_evidence_packages import estimate_tokens
from ai_ecosystem.calibration.build_page_training_dataset import (
    _content_features,
    _language_features,
    _link_features,
    _page_type_features,
    _url_features,
    load_database_rows,
    parse_json,
)
from ai_ecosystem.calibration.page_annotation_contract import ATTRIBUTIONS, FACETS, NOISE_FLAGS
from ai_ecosystem.calibration.run_page_model_experiments import (
    KEY_COLUMNS,
    load_experiment_config,
    predict_model,
    transform_text,
)
from ai_ecosystem.calibration.run_page_model_improvements import (
    _aligned_probabilities,
    apply_probability_multipliers,
    focal_confidence,
)
from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


FOCAL_ATTRIBUTIONS = frozenset({"focal_company_direct", "focal_company_probable"})


@dataclass(frozen=True)
class EvaluationConfig:
    evaluation_id: str
    evaluation_version: str
    annotation_sample_directory: str
    annotation_run_id: str
    annotation_run_directory: str
    annotation_qa_version: str
    model_directory: str
    model_manifest: str
    training_feature_dictionary: str
    baseline_model_config: str
    output_directory: str
    candidate_ids: tuple[str, ...]
    dedicated_attribution_model: str
    relevance_utility_threshold: int
    ranking_k: tuple[int, ...]
    token_budgets: tuple[int, ...]
    random_seed: int
    set_selection: dict[str, Any]
    config_hash: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_evaluation_config(path: Path) -> EvaluationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = {
        "evaluation_id", "evaluation_version", "annotation_sample_directory",
        "annotation_run_id", "annotation_run_directory", "annotation_qa_version",
        "model_directory", "model_manifest", "training_feature_dictionary",
        "baseline_model_config", "output_directory", "candidate_ids",
        "dedicated_attribution_model", "relevance_utility_threshold", "ranking_k",
        "token_budgets", "random_seed", "set_selection",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"Missing locked retrieval evaluation keys: {', '.join(missing)}")
    candidates = tuple(str(value) for value in raw["candidate_ids"])
    expected = {
        "tfidf_relational_ridge_attribution_adjusted", "tfidf_relational_ridge",
        "hybrid_relational_ridge", "engineered_relational_hgb",
    }
    if set(candidates) != expected:
        raise ValueError("Locked evaluation must contain the four provisional candidates")
    ranking_k = tuple(int(value) for value in raw["ranking_k"])
    budgets = tuple(int(value) for value in raw["token_budgets"])
    if ranking_k != (5, 10, 20) or any(value <= 0 for value in budgets):
        raise ValueError("ranking_k must be [5, 10, 20] and token budgets must be positive")
    selection = dict(raw["set_selection"])
    if tuple(selection.get("strategies", ())) != ("top_ranked", "diversity_aware"):
        raise ValueError("Expected frozen top_ranked and diversity_aware strategies")
    required_selection = {
        "utility_weight", "focal_attribution_weight", "facet_novelty_weight",
        "path_novelty_weight", "redundancy_penalty", "predicted_noise_penalty",
        "lexical_token_cap", "material_utility_gain", "material_facet_gain",
        "maximum_utility_tradeoff",
    }
    if required_selection - set(selection):
        raise ValueError("Locked set-selection configuration is incomplete")
    return EvaluationConfig(
        evaluation_id=str(raw["evaluation_id"]), evaluation_version=str(raw["evaluation_version"]),
        annotation_sample_directory=str(raw["annotation_sample_directory"]),
        annotation_run_id=str(raw["annotation_run_id"]),
        annotation_run_directory=str(raw["annotation_run_directory"]),
        annotation_qa_version=str(raw["annotation_qa_version"]),
        model_directory=str(raw["model_directory"]), model_manifest=str(raw["model_manifest"]),
        training_feature_dictionary=str(raw["training_feature_dictionary"]),
        baseline_model_config=str(raw["baseline_model_config"]), output_directory=str(raw["output_directory"]),
        candidate_ids=candidates, dedicated_attribution_model=str(raw["dedicated_attribution_model"]),
        relevance_utility_threshold=int(raw["relevance_utility_threshold"]), ranking_k=ranking_k,
        token_budgets=budgets, random_seed=int(raw["random_seed"]), set_selection=selection,
        config_hash=hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest(),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def as_bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _stable_random(seed: int, company_id: str, scraped_page_id: int) -> float:
    digest = hashlib.sha256(f"{seed}:{company_id}:{scraped_page_id}".encode()).hexdigest()
    return int(digest[:16], 16) / float(16**16 - 1)


def _path_family(url: str) -> str:
    try:
        parts = [unquote(value).casefold() for value in urlsplit(url).path.split("/") if value]
    except ValueError:
        parts = []
    return parts[0] if parts else "root"


def _lexical_tokens(text: str, cap: int) -> frozenset[str]:
    counts = Counter(normalize_lexical(text).split())
    return frozenset(value for value, _ in counts.most_common(cap))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _model_input_tokens(row: dict[str, Any]) -> int:
    payload = {
        "scraped_page_id": int(row["scraped_page_id"]),
        "company_id": row["company_id"],
        "company_name": row["company_name"],
        "normalized_company_domain": row["normalized_domain"],
        "page_url": row["normalized_url"],
        "title": row["title"],
        "headings": parse_json(row["headings_json"], []),
        "extracted_markdown": row["model_visible_markdown"],
        "markdown_truncated": bool(row["markdown_truncated"]),
    }
    return max(1, estimate_tokens(canonical_json(payload)))


def build_locked_frame(
    connection: sqlite3.Connection,
    project_root: Path,
    config: EvaluationConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    sample_dir = _resolve(project_root, config.annotation_sample_directory)
    run_dir = _resolve(project_root, config.annotation_run_directory)
    qa_dir = run_dir / config.annotation_qa_version
    manifest = json.loads((sample_dir / "manifest.json").read_text(encoding="utf-8"))
    run_metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    if run_metadata.get("status") not in {"ingested", "completed"}:
        raise ValueError(f"Annotation run is not ingested: {run_metadata.get('status')}")
    if run_metadata.get("sample_id") != manifest.get("sample_id"):
        raise ValueError("Annotation run and locked sample identity differ")
    pages = sorted(read_jsonl(sample_dir / "pages.jsonl"), key=lambda row: (str(row["company_id"]), int(row["scraped_page_id"])))
    companies_list = read_jsonl(sample_dir / "companies.jsonl")
    companies = {str(row["company_id"]): row for row in companies_list}
    annotations = {
        str(row["annotation_item_id"]): row
        for row in read_jsonl(run_dir / "annotations.jsonl")
        if not row.get("is_hidden_repeat") and row.get("status") == "success"
    }
    model_inputs = {str(row["custom_id"]): row for row in read_jsonl(run_dir / "model_inputs.jsonl")}
    qa = {
        str(row["annotation_item_id"]): row
        for row in read_csv(qa_dir / "annotation_qa.csv")
        if not as_bool(row["is_hidden_repeat"])
    }
    expected = {str(row["annotation_item_id"]) for row in pages}
    missing = sorted(expected - annotations.keys() | expected - qa.keys())
    if missing:
        raise ValueError(f"Missing locked annotation or QA rows: {len(missing)}")

    metadata_by_page, links_by_page, scores = load_database_rows(connection, pages, companies)
    hash_counts = Counter(str(row.get("content_sha256") or "") for row in pages if row.get("content_sha256"))
    company_counts = Counter(str(row["company_id"]) for row in pages)
    domain_counts = Counter(str(row["domain"]) for row in pages)
    rows: list[dict[str, Any]] = []
    untrusted = partially_trusted = 0
    for page in pages:
        item_id = str(page["annotation_item_id"])
        company_id = str(page["company_id"])
        extracted_id = int(page["extracted_page_id"])
        db = metadata_by_page.get(extracted_id)
        if not db or db["extraction_status"] != "success" or db["extraction_version"] != "v1":
            raise ValueError(f"Missing compatible extraction for {item_id}")
        result = annotations[item_id]
        qa_row = qa[item_id]
        trust = str(qa_row["trust_flag"])
        untrusted += int(trust == "untrusted")
        partially_trusted += int(trust == "partially_trusted")
        model_input = model_inputs[str(result["custom_id"])]
        annotation = dict(result["annotation"])
        soft_inconsistencies = parse_json(qa_row.get("soft_inconsistencies"), [])
        category_noise = {
            "news_press_or_blog": "news_or_press",
            "navigation_or_low_content": "navigation_or_low_content",
            "legal_or_compliance": "legal_or_compliance",
            "careers": "careers",
        }
        restricted_noise: set[str] = set()
        category_restricted = False
        for value in soft_inconsistencies:
            prefix = "category_without_matching_noise_flag:"
            if str(value).startswith(prefix):
                category_restricted = True
                flag = category_noise.get(str(value)[len(prefix):])
                if flag:
                    restricted_noise.add(flag)
        headings_raw = model_input.get("headings") if isinstance(model_input.get("headings"), list) else parse_json(db.get("headings"), [])
        headings = [str(value) for value in headings_raw]
        markdown = str(model_input.get("extracted_markdown") or "")
        title = str(model_input.get("title") or db.get("title") or "")
        content_hash = str(page.get("content_sha256") or db.get("content_sha256") or "")
        identifiers = {"annotation_item_id": item_id, "company_id": company_id, "scraped_page_id": int(page["scraped_page_id"])}
        features: dict[str, Any] = dict(identifiers)
        features.update(_url_features(str(page.get("normalized_url") or db.get("url") or "")))
        features.update(_content_features(markdown, title, headings, db))
        features.update(_link_features(links_by_page.get(extracted_id, [])))
        features.update(_language_features(db.get("language") or page.get("language")))
        features.update(_page_type_features(db.get("page_type_guess") or page.get("page_type")))
        company = companies[company_id]
        features.update({
            "duplicate__content_hash_group_size": hash_counts.get(content_hash, 1),
            "duplicate__within_domain_group_size": int(page.get("duplicate_group_size") or 1),
            "duplicate__has_exact_duplicate": int(hash_counts.get(content_hash, 1) > 1 or int(page.get("duplicate_group_size") or 1) > 1),
            "duplicate__cross_split": 0,
            "site__company_usable_page_count": int(company.get("raw_usable_pages") or 0),
            "site__company_unique_content_page_count": int(company.get("unique_content_pages") or 0),
            "site__company_selected_page_count": company_counts[company_id],
            "site__domain_selected_page_count": domain_counts[str(page["domain"])],
            "site__shared_domain": int(bool(company.get("shared_domain"))),
            "site__mapped_company_count": int(company.get("mapped_company_count") or 1),
            "site__attribution_uncertain": int(bool(company.get("attribution_uncertain"))),
        })
        row = {
            **features,
            "extracted_page_id": extracted_id,
            "normalized_domain": str(page["domain"]),
            "normalized_url": str(page.get("normalized_url") or db.get("final_url") or db.get("url") or ""),
            "company_name": str(company.get("company_name") or ""),
            "title": title,
            "headings_json": canonical_json(headings_raw),
            "model_visible_markdown": markdown,
            "markdown_truncated": bool(model_input.get("markdown_truncated")),
            "content_sha256": content_hash,
            "path_family": _path_family(str(page.get("normalized_url") or "")),
            "trust_status": trust,
            "company_label_audit_only": str(company.get("label") or "unknown"),
            "utility_score": None if trust == "untrusted" else int(annotation["utility_score"]),
            "attribution": None if trust == "untrusted" else str(annotation["attribution"]),
            "page_category": None if trust == "untrusted" or category_restricted else str(annotation["page_category"]),
            "page_priority_tier": None if trust == "untrusted" else str(annotation["page_priority_tier"]),
        }
        restricted_facets = set(parse_json(qa_row.get("ungrounded_positive_facets"), []))
        for facet in FACETS:
            row[f"truth_facet__{facet}"] = None if trust == "untrusted" or facet in restricted_facets else int(annotation["evidence_facets"][facet]["strength"])
        for flag in NOISE_FLAGS:
            row[f"truth_noise__{flag}"] = (
                None
                if trust == "untrusted" or flag in restricted_noise
                else int(bool(annotation["noise_flags"][flag]))
            )
        for dimension in ("firm_profile", "technology", "innovation", "ecosystem"):
            score = scores.get((str(company["scoring_run_id"]), extracted_id, dimension))
            row[f"scorer_v1__{dimension}__final_score"] = float(score["final_score"]) if score else math.nan
        row["document"] = f"TITLE {title}\nHEADINGS {row['headings_json']}\nCONTENT {markdown}"
        row["improved_document"] = (
            f"FOCAL_COMPANY {row['company_name']}\nFOCAL_DOMAIN {row['normalized_domain']}\n"
            f"PAGE_URL {row['normalized_url']}\n{row['document']}"
        )
        row["model_input_tokens"] = _model_input_tokens(row)
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(KEY_COLUMNS).reset_index(drop=True)
    return frame, {
        "company_count": len(companies), "page_count": len(frame), "untrusted_annotations": untrusted,
        "partially_trusted_annotations": partially_trusted, "annotation_run_id": config.annotation_run_id,
        "sample_manifest_sha256": sha256_file(sample_dir / "manifest.json"),
        "qa_summary_sha256": sha256_file(qa_dir / "qa_summary.json"),
    }


def _matrix_for_artifact(
    frame: pd.DataFrame,
    artifact: dict[str, Any],
    matrix_key: str,
    engineered_columns: list[str],
) -> np.ndarray | sp.spmatrix:
    representations = artifact["representations"]
    relational = build_relational_features(frame)[artifact["relational_feature_names"]]
    if matrix_key == "relational":
        return representations["relational_preprocessor"].transform(relational)
    engineered_relational = frame[engineered_columns].reset_index(drop=True).join(relational.reset_index(drop=True))
    if matrix_key == "engineered_relational":
        return representations["engineered_relational_preprocessor"].transform(engineered_relational)
    text = transform_text(representations, frame["improved_document"])
    if matrix_key == "text_relational":
        dense = representations["relational_preprocessor"].transform(relational)
        return sp.hstack([text, sp.csr_matrix(dense)], format="csr")
    if matrix_key == "hybrid_relational":
        dense = representations["engineered_relational_preprocessor"].transform(engineered_relational)
        return sp.hstack([text, sp.csr_matrix(dense)], format="csr")
    raise ValueError(f"Unknown frozen matrix key: {matrix_key}")


def frozen_predictions(project_root: Path, config: EvaluationConfig, frame: pd.DataFrame) -> pd.DataFrame:
    feature_dictionary = json.loads(_resolve(project_root, config.training_feature_dictionary).read_text(encoding="utf-8"))
    baseline_config = load_experiment_config(_resolve(project_root, config.baseline_model_config))
    engineered_columns = [
        value for value in feature_dictionary["predictive_feature_columns"]
        if value not in baseline_config.excluded_engineered_features
    ]
    model_dir = _resolve(project_root, config.model_directory)
    model_manifest = json.loads(_resolve(project_root, config.model_manifest).read_text(encoding="utf-8"))
    required_model_files = {
        f"models/{candidate_id.removesuffix('_attribution_adjusted')}.joblib"
        for candidate_id in config.candidate_ids
    }
    required_model_files.add(f"models/attribution_{config.dedicated_attribution_model}.joblib")
    for relative in sorted(required_model_files):
        path = _resolve(project_root, config.model_manifest).parent / relative
        expected_hash = model_manifest.get("artifacts", {}).get(relative, {}).get("sha256")
        if not expected_hash or sha256_file(path) != expected_hash:
            raise ValueError(f"Frozen model artifact hash mismatch: {relative}")
    dedicated = joblib.load(model_dir / f"attribution_{config.dedicated_attribution_model}.joblib")
    dedicated_matrix = _matrix_for_artifact(frame, dedicated, dedicated["matrix_key"], engineered_columns)
    dedicated_probabilities = apply_probability_multipliers(
        _aligned_probabilities(dedicated["model"], dedicated_matrix), dedicated["probability_multipliers"]
    )
    dedicated_focal = focal_confidence(dedicated_probabilities, 0.65)
    output: list[pd.DataFrame] = []
    raw_artifacts: dict[str, dict[str, Any]] = {}
    for candidate_id in config.candidate_ids:
        base_id = candidate_id.removesuffix("_attribution_adjusted")
        artifact = raw_artifacts.setdefault(base_id, joblib.load(model_dir / f"{base_id}.joblib"))
        matrix = _matrix_for_artifact(frame, artifact, artifact["matrix_key"], engineered_columns)
        shared = predict_model({"utility_model": artifact["utility_model"], "auxiliary_models": artifact["auxiliary_models"]}, matrix)
        raw_score = np.asarray(shared["utility"], dtype=float)
        score = raw_score
        if candidate_id.endswith("_attribution_adjusted"):
            attribution_matrix = _matrix_for_artifact(frame, artifact, artifact["attribution_matrix_key"], engineered_columns)
            probabilities = apply_probability_multipliers(
                _aligned_probabilities(artifact["attribution_model"], attribution_matrix),
                artifact["attribution_probability_multipliers"],
            )
            score = raw_score - float(artifact["attribution_adjustment_penalty"]) * (
                1.0 - focal_confidence(probabilities, float(artifact["probable_credit"]))
            )
        candidate = frame[KEY_COLUMNS].copy()
        candidate["candidate_id"] = candidate_id
        candidate["predicted_utility"] = score
        candidate["predicted_utility_raw"] = raw_score
        candidate["dedicated_focal_confidence"] = dedicated_focal
        candidate["dedicated_attribution_prediction"] = np.asarray(ATTRIBUTIONS)[np.argmax(dedicated_probabilities, axis=1)]
        for index, value in enumerate(ATTRIBUTIONS):
            candidate[f"dedicated_attribution_probability__{value}"] = dedicated_probabilities[:, index]
        for facet in FACETS:
            candidate[f"predicted_facet__{facet}"] = np.asarray(shared["facets"][facet], dtype=int)
        for flag in NOISE_FLAGS:
            candidate[f"predicted_noise__{flag}"] = np.asarray(shared["noise"][flag], dtype=int)
        output.append(candidate)
    predictions = pd.concat(output, ignore_index=True)
    expected = len(frame) * len(config.candidate_ids)
    if len(predictions) != expected:
        raise AssertionError("Frozen inference did not produce every candidate/page prediction")
    return predictions


def dcg(values: Iterable[int]) -> float:
    return sum((2 ** int(value) - 1) / math.log2(index + 2) for index, value in enumerate(values))


def ranking_metrics(truth: pd.DataFrame, predictions: pd.DataFrame, config: EvaluationConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged = predictions.merge(truth, on=KEY_COLUMNS, validate="many_to_one")
    merged = merged[merged["utility_score"].notna()].copy()
    baseline_frames = []
    structural = (
        0.25 + 0.55 * merged["url__is_root"] + 0.50 * merged["url__is_about_company"]
        + 0.20 * merged["url__is_shallow"] + 0.80 * np.clip(np.log1p(merged["text__markdown_word_count"]) / math.log(1001), 0, 1)
        + 0.20 * np.clip(merged["structure__heading_count"] / 5, 0, 1)
        + 0.35 * merged[["page_type__is_products", "page_type__is_services", "page_type__is_technology", "page_type__is_innovation"]].max(axis=1)
        - 0.65 * merged[["page_type__is_legal", "page_type__is_careers"]].max(axis=1)
        - 0.25 * merged["page_type__is_news"] - 0.45 * (merged["text__markdown_word_count"] < 40)
    )
    base = merged.drop_duplicates(KEY_COLUMNS).copy()
    base["predicted_utility"] = structural.loc[base.index]
    base["candidate_id"] = "baseline_structural"
    baseline_frames.append(base)
    scorer_columns = [f"scorer_v1__{value}__final_score" for value in ("firm_profile", "technology", "innovation", "ecosystem")]
    scorer = merged.drop_duplicates(KEY_COLUMNS).copy()
    scorer["predicted_utility"] = scorer[scorer_columns].max(axis=1, skipna=True).fillna(0.0)
    scorer["candidate_id"] = "baseline_scorer_v1_max_dimension"
    baseline_frames.append(scorer)
    random = merged.drop_duplicates(KEY_COLUMNS).copy()
    random["predicted_utility"] = [
        _stable_random(config.random_seed, str(row.company_id), int(row.scraped_page_id))
        for row in random.itertuples()
    ]
    random["candidate_id"] = "baseline_random_deterministic"
    baseline_frames.append(random)
    all_rows = pd.concat([merged, *baseline_frames], ignore_index=True, sort=False)
    company_rows: list[dict[str, Any]] = []
    for (candidate_id, company_id), group in all_rows.groupby(["candidate_id", "company_id"], sort=True):
        group = group.sort_values(["predicted_utility", "scraped_page_id"], ascending=[False, True])
        ideal = sorted((int(value) for value in group["utility_score"]), reverse=True)
        for k in config.ranking_k:
            chosen = group.head(k)
            denominator = min(k, len(group))
            gains = [int(value) for value in chosen["utility_score"]]
            focal = chosen["attribution"].isin(FOCAL_ATTRIBUTIONS)
            noise_columns = [f"truth_noise__{flag}" for flag in NOISE_FLAGS]
            any_noise = chosen[noise_columns].fillna(0).astype(bool).any(axis=1)
            company_rows.append({
                "candidate_id": candidate_id, "company_id": company_id, "k": k,
                "page_count": len(group), "precision": float((chosen["utility_score"] >= config.relevance_utility_threshold).sum() / denominator),
                "ndcg": float(dcg(gains) / dcg(ideal[:denominator])) if dcg(ideal[:denominator]) else 0.0,
                "captured_utility": int(sum(gains)), "mean_utility": float(mean(gains)),
                "focal_attribution_rate": float(focal.mean()), "noise_rate": float(any_noise.mean()),
            })
    summary_rows = []
    frame = pd.DataFrame(company_rows)
    for (candidate_id, k), group in frame.groupby(["candidate_id", "k"], sort=True):
        summary_rows.append({
            "candidate_id": candidate_id, "k": int(k), "companies": len(group),
            **{column: float(group[column].mean()) for column in ("precision", "ndcg", "captured_utility", "mean_utility", "focal_attribution_rate", "noise_rate")},
        })
    lookup = {(row["candidate_id"], row["k"]): row for row in summary_rows}
    baseline_names = {
        "structural": "baseline_structural",
        "scorer_v1": "baseline_scorer_v1_max_dimension",
        "random": "baseline_random_deterministic",
    }
    for row in summary_rows:
        for label, baseline_id in baseline_names.items():
            baseline = lookup.get((baseline_id, row["k"]))
            row[f"utility_gain_vs_{label}"] = (
                float(row["captured_utility"] - baseline["captured_utility"])
                if baseline and not row["candidate_id"].startswith("baseline_")
                else None
            )
    return company_rows, summary_rows


def _normalize_scores(values: pd.Series) -> pd.Series:
    low, high = float(values.min()), float(values.max())
    return (values - low) / (high - low) if high > low else pd.Series(np.ones(len(values)), index=values.index)


def select_budget_pages(
    group: pd.DataFrame,
    budget: int,
    strategy: str,
    config: EvaluationConfig,
    *,
    max_pages: int | None = None,
) -> list[int]:
    work = group.copy()
    work["normalized_prediction"] = _normalize_scores(work["predicted_utility"])
    tokens = dict(zip(work.index, work["model_input_tokens"].astype(int)))
    selected: list[int] = []
    selected_set: set[int] = set()
    remaining = int(budget)
    covered_facets: set[str] = set()
    covered_paths: set[str] = set()
    token_sets = {index: work.at[index, "lexical_token_set"] for index in work.index}
    maximum_redundancy = {index: 0.0 for index in work.index}
    while max_pages is None or len(selected) < max_pages:
        eligible = [index for index in work.index if index not in selected_set and tokens[index] <= remaining]
        if not eligible:
            break
        if strategy == "top_ranked":
            chosen = max(eligible, key=lambda index: (float(work.at[index, "predicted_utility"]), -int(work.at[index, "scraped_page_id"])))
        else:
            def objective(index: int) -> tuple[float, int]:
                facets = {facet for facet in FACETS if int(work.at[index, f"predicted_facet__{facet}"]) > 0}
                novelty = len(facets - covered_facets) / len(FACETS)
                path_novelty = float(str(work.at[index, "path_family"]) not in covered_paths)
                redundancy = maximum_redundancy[index]
                noise = mean(int(work.at[index, f"predicted_noise__{flag}"]) for flag in NOISE_FLAGS)
                score = (
                    float(config.set_selection["utility_weight"]) * float(work.at[index, "normalized_prediction"])
                    + float(config.set_selection["focal_attribution_weight"]) * float(work.at[index, "dedicated_focal_confidence"])
                    + float(config.set_selection["facet_novelty_weight"]) * novelty
                    + float(config.set_selection["path_novelty_weight"]) * path_novelty
                    - float(config.set_selection["redundancy_penalty"]) * redundancy
                    - float(config.set_selection["predicted_noise_penalty"]) * noise
                )
                return score, -int(work.at[index, "scraped_page_id"])
            chosen = max(eligible, key=objective)
        selected.append(chosen)
        selected_set.add(chosen)
        remaining -= tokens[chosen]
        covered_facets.update(facet for facet in FACETS if int(work.at[chosen, f"predicted_facet__{facet}"]) > 0)
        covered_paths.add(str(work.at[chosen, "path_family"]))
        if strategy != "top_ranked":
            for index in work.index:
                if index not in selected_set:
                    maximum_redundancy[index] = max(
                        maximum_redundancy[index],
                        _jaccard(token_sets[index], token_sets[chosen]),
                    )
    return selected


def budget_metrics(truth: pd.DataFrame, predictions: pd.DataFrame, config: EvaluationConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    token_sets_by_item = {
        str(row.annotation_item_id): _lexical_tokens(
            str(row.model_visible_markdown),
            int(config.set_selection["lexical_token_cap"]),
        )
        for row in truth[["annotation_item_id", "model_visible_markdown"]].itertuples(index=False)
    }
    merged = predictions.merge(truth, on=KEY_COLUMNS, validate="many_to_one")
    merged = merged[merged["utility_score"].notna()].copy()
    merged["lexical_token_set"] = merged["annotation_item_id"].map(token_sets_by_item)
    company_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    for (candidate_id, company_id), group in merged.groupby(["candidate_id", "company_id"], sort=True):
        ground_facets = {
            facet for facet in FACETS
            if group[f"truth_facet__{facet}"].fillna(0).max() >= 1
        }
        important_facets = {
            facet for facet in FACETS
            if group[f"truth_facet__{facet}"].fillna(0).max() >= 2
        }
        budget_specs = [
            *(("pages", value, 10**18, value) for value in config.ranking_k),
            *(("tokens", value, value, None) for value in config.token_budgets),
        ]
        for budget_type, budget_value, token_budget, max_pages in budget_specs:
            for strategy in config.set_selection["strategies"]:
                selected_indices = select_budget_pages(
                    group,
                    token_budget,
                    str(strategy),
                    config,
                    max_pages=max_pages,
                )
                chosen = group.loc[selected_indices]
                selected_facets = {
                    facet for facet in FACETS
                    if not chosen.empty and chosen[f"truth_facet__{facet}"].fillna(0).max() >= 1
                }
                useful_categories = set(chosen.loc[chosen["utility_score"] >= config.relevance_utility_threshold, "page_category"].dropna())
                all_useful_categories = set(group.loc[group["utility_score"] >= config.relevance_utility_threshold, "page_category"].dropna())
                token_sets = list(chosen["lexical_token_set"])
                pairwise = [_jaccard(token_sets[left], token_sets[right]) for left in range(len(token_sets)) for right in range(left + 1, len(token_sets))]
                tokens_used = int(chosen["model_input_tokens"].sum())
                row = {
                    "candidate_id": candidate_id, "company_id": company_id, "strategy": strategy,
                    "budget_type": budget_type, "budget_value": budget_value,
                    "token_budget": token_budget if budget_type == "tokens" else None,
                    "page_budget": max_pages,
                    "selected_pages": len(chosen), "tokens_used": tokens_used,
                    "captured_utility": int(chosen["utility_score"].sum()),
                    "available_utility": int(group["utility_score"].sum()),
                    "utility_capture_rate": float(chosen["utility_score"].sum() / group["utility_score"].sum()) if group["utility_score"].sum() else 1.0,
                    "facet_coverage_rate": float(len(selected_facets) / len(ground_facets)) if ground_facets else 1.0,
                    "covered_facets": canonical_json(sorted(selected_facets)),
                    "missed_important_facets": canonical_json(sorted(important_facets - selected_facets)),
                    "missed_important_facet_count": len(important_facets - selected_facets),
                    "useful_category_diversity_rate": float(len(useful_categories) / len(all_useful_categories)) if all_useful_categories else 1.0,
                    "mean_pairwise_lexical_jaccard": float(mean(pairwise)) if pairwise else 0.0,
                    "utility_per_1k_tokens": float(1000 * chosen["utility_score"].sum() / tokens_used) if tokens_used else 0.0,
                    "focal_attribution_rate": float(chosen["attribution"].isin(FOCAL_ATTRIBUTIONS).mean()) if len(chosen) else 0.0,
                    "noise_rate": float(chosen[[f"truth_noise__{flag}" for flag in NOISE_FLAGS]].fillna(0).astype(bool).any(axis=1).mean()) if len(chosen) else 0.0,
                }
                company_rows.append(row)
                for rank, source in enumerate(chosen.itertuples(), start=1):
                    selection_rows.append({
                        "candidate_id": candidate_id, "company_id": company_id, "strategy": strategy,
                        "budget_type": budget_type, "budget_value": budget_value,
                        "token_budget": token_budget if budget_type == "tokens" else None,
                        "page_budget": max_pages, "selection_rank": rank,
                        "scraped_page_id": int(source.scraped_page_id), "predicted_utility": float(source.predicted_utility),
                        "annotated_utility": int(source.utility_score), "model_input_tokens": int(source.model_input_tokens),
                    })
    summary_rows = []
    frame = pd.DataFrame(company_rows)
    metrics = ("selected_pages", "tokens_used", "captured_utility", "utility_capture_rate", "facet_coverage_rate", "missed_important_facet_count", "useful_category_diversity_rate", "mean_pairwise_lexical_jaccard", "utility_per_1k_tokens", "focal_attribution_rate", "noise_rate")
    for (candidate_id, strategy, budget_type, budget_value), group in frame.groupby(
        ["candidate_id", "strategy", "budget_type", "budget_value"], sort=True
    ):
        summary_rows.append({
            "candidate_id": candidate_id,
            "strategy": strategy,
            "budget_type": budget_type,
            "budget_value": int(budget_value),
            "companies": len(group),
            **{value: float(group[value].mean()) for value in metrics},
        })
    return company_rows, summary_rows, selection_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pareto_rankers(
    candidate_rows: pd.DataFrame,
    *,
    include_attribution: bool = True,
) -> list[str]:
    aggregate = candidate_rows.groupby("candidate_id", sort=True).agg(
        ndcg=("ndcg", "mean"),
        precision=("precision", "mean"),
        focal=("focal_attribution_rate", "mean"),
        noise=("noise_rate", "mean"),
    )
    keep: list[str] = []
    for candidate_id, row in aggregate.iterrows():
        dominated = False
        for other_id, other in aggregate.iterrows():
            if other_id == candidate_id:
                continue
            attribution_no_worse = other["focal"] >= row["focal"] if include_attribution else True
            attribution_better = other["focal"] > row["focal"] if include_attribution else False
            no_worse = (
                other["ndcg"] >= row["ndcg"]
                and other["precision"] >= row["precision"]
                and attribution_no_worse
                and other["noise"] <= row["noise"]
            )
            strictly_better = (
                other["ndcg"] > row["ndcg"]
                or other["precision"] > row["precision"]
                or attribution_better
                or other["noise"] < row["noise"]
            )
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            keep.append(str(candidate_id))
    return keep


def run_evaluation(
    project_root: Path,
    config: EvaluationConfig,
    config_path: Path,
    output_directory: Path,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if output_directory.exists():
        if not force_refresh:
            raise FileExistsError(f"Locked evaluation output already exists: {output_directory}")
        shutil.rmtree(output_directory)
    qa_summary = json.loads((_resolve(project_root, config.annotation_run_directory) / config.annotation_qa_version / "qa_summary.json").read_text(encoding="utf-8"))
    failed_qa_gates = set(qa_summary.get("failed_gates", []))
    blocking_qa_gates = failed_qa_gates - {"hidden_repeat_attribution_agreement"}
    if blocking_qa_gates:
        raise ValueError(
            "Locked annotation QA has blocking failed gates: "
            + ", ".join(sorted(blocking_qa_gates))
        )
    attribution_evaluation_restricted = "hidden_repeat_attribution_agreement" in failed_qa_gates
    database = resolve_database_path(project_root, load_config(project_root))
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        truth, input_audit = build_locked_frame(connection, project_root, config)
    if len(truth) != 7_318 or truth["company_id"].nunique() != 40:
        raise ValueError("Locked evaluation corpus differs from the frozen 40-company/7,318-page scope")
    predictions = frozen_predictions(project_root, config, truth)
    company_ranking, candidate_ranking = ranking_metrics(truth, predictions, config)
    company_budget, candidate_budget, selection_rows = budget_metrics(truth, predictions, config)

    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    predictions.to_parquet(temporary / "locked_predictions.parquet", index=False, compression="zstd")
    _write_csv(temporary / "company_ranking_metrics.csv", company_ranking)
    _write_csv(temporary / "candidate_ranking_metrics.csv", candidate_ranking)
    _write_csv(temporary / "company_budget_metrics.csv", company_budget)
    _write_csv(temporary / "candidate_budget_metrics.csv", candidate_budget)
    _write_csv(temporary / "selected_pages.csv", selection_rows)
    shutil.copyfile(config_path, temporary / "config_snapshot.yaml")

    rank_frame = pd.DataFrame(candidate_ranking)
    candidates = rank_frame[~rank_frame["candidate_id"].str.startswith("baseline_")]
    rank_score = (
        candidates.groupby("candidate_id", sort=True)["ndcg"].sum().sort_values(ascending=False)
    )
    best_ranker = str(rank_score.index[0])
    recommended_rankers = pareto_rankers(
        candidates,
        include_attribution=not attribution_evaluation_restricted,
    )
    budget_frame = pd.DataFrame(candidate_budget)
    utility_pivot = budget_frame.pivot(
        index=["candidate_id", "budget_type", "budget_value"],
        columns="strategy",
        values="utility_capture_rate",
    )
    facet_pivot = budget_frame.pivot(
        index=["candidate_id", "budget_type", "budget_value"],
        columns="strategy",
        values="facet_coverage_rate",
    )
    diversity_gain = utility_pivot["diversity_aware"] - utility_pivot["top_ranked"]
    facet_gain = facet_pivot["diversity_aware"] - facet_pivot["top_ranked"]
    selector_warranted = bool(
        (diversity_gain > float(config.set_selection["material_utility_gain"])).any()
        or (
            (facet_gain >= float(config.set_selection["material_facet_gain"]))
            & (diversity_gain >= -float(config.set_selection["maximum_utility_tradeoff"]))
        ).any()
    )
    summary = {
        "evaluation_id": config.evaluation_id, "evaluation_version": config.evaluation_version,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "config_hash": config.config_hash, "locked_company_count": 40, "eligible_page_count": 7_318,
        "annotation_run_id": config.annotation_run_id, "annotation_qa_version": config.annotation_qa_version,
        "annotation_qa": {
            "failed_gates": sorted(failed_qa_gates),
            "blocking_failed_gates": sorted(blocking_qa_gates),
            "attribution_evaluation_restricted": attribution_evaluation_restricted,
            "hidden_repeat_attribution_agreement": qa_summary["hidden_repeat_consistency"]["attribution_agreement"],
            "hidden_repeat_utility_weighted_kappa": qa_summary["hidden_repeat_consistency"]["utility_weighted_kappa"],
            "trusted_primary_annotation_rate": qa_summary["rates"]["trusted_primary_annotations"],
        },
        "input_audit": input_audit, "candidate_ids": list(config.candidate_ids),
        "dedicated_attribution_model": config.dedicated_attribution_model,
        "baselines": {
            "structural": "Frozen structural-rule formula from Milestone 5.8f.",
            "scorer_v1": "Maximum final score across scorer-v1's four immutable baseline dimensions.",
            "random": f"Deterministic SHA-256 ordering with seed {config.random_seed}.",
        },
        "evaluation_policy": {
            "relevant_page": f"utility_score >= {config.relevance_utility_threshold}",
            "ndcg_gain": "2^utility_score - 1", "macro_averaging": "unweighted mean across locked companies",
            "no_locked_training_or_tuning": True, "set_selection_weights_frozen_in_config": True,
            "attribution_metrics": (
                "Descriptive only and excluded from Pareto selection because repeat agreement missed its QA gate."
                if attribution_evaluation_restricted
                else "Eligible for model-selection comparison."
            ),
        },
        "best_ranker_by_sum_ndcg": best_ranker,
        "recommended_pareto_rankers": recommended_rankers,
        "diversity_aware_utility_capture_gain": {
            "|".join(map(str, key)): float(value) for key, value in diversity_gain.items()
        },
        "diversity_aware_facet_coverage_gain": {
            "|".join(map(str, key)): float(value) for key, value in facet_gain.items()
        },
        "separate_set_selector_warranted": selector_warranted,
        "artifacts": {},
    }
    report = [
        "# Full Locked Retrieval and Evidence-Package Evaluation", "",
        "## Evaluation Boundary", "",
        "All 7,318 eligible exact-content-deduplicated pages from the 40 locked companies were scored by previously fitted artifacts. Locked annotations were loaded only for evaluation; no model or threshold was fitted or tuned here.", "",
    ]
    if attribution_evaluation_restricted:
        report.extend([
            "Attribution metrics are descriptive only: hidden-repeat agreement was 83.33%, below the 85% QA gate. Attribution is excluded from the Pareto recommendation.",
            "",
        ])
    report.extend([
        "## Ranking", "",
        "| Candidate | k | P@k | NDCG@k | Utility | Gain vs structural | Gain vs scorer-v1 | Gain vs random | Focal attribution | Noise |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in candidate_ranking:
        gains = [row.get(f"utility_gain_vs_{value}") for value in ("structural", "scorer_v1", "random")]
        rendered_gains = ["-" if value is None else f"{value:+.2f}" for value in gains]
        report.append(f"| `{row['candidate_id']}` | {row['k']} | {row['precision']:.3f} | {row['ndcg']:.3f} | {row['captured_utility']:.2f} | {rendered_gains[0]} | {rendered_gains[1]} | {rendered_gains[2]} | {row['focal_attribution_rate']:.3f} | {row['noise_rate']:.3f} |")
    report.extend(["", "## Evidence Packages", "", "| Candidate | Strategy | Budget | Utility capture | Facet coverage | Missed facets | Diversity | Redundancy | Utility/1k |", "|---|---|---|---:|---:|---:|---:|---:|---:|"])
    for row in candidate_budget:
        report.append(f"| `{row['candidate_id']}` | `{row['strategy']}` | {row['budget_value']} {row['budget_type']} | {row['utility_capture_rate']:.3f} | {row['facet_coverage_rate']:.3f} | {row['missed_important_facet_count']:.2f} | {row['useful_category_diversity_rate']:.3f} | {row['mean_pairwise_lexical_jaccard']:.3f} | {row['utility_per_1k_tokens']:.3f} |")
    report.extend(["", "## Recommendation", "", f"Best provisional ranker by summed locked NDCG: `{best_ranker}`.", "Rankers on the locked precision/NDCG/attribution/noise Pareto frontier: " + ", ".join(f"`{value}`" for value in recommended_rankers) + ".", ("A separate lightweight diversity/redundancy-aware selector is warranted under the frozen decision rule." if selector_warranted else "Pure top-ranked selection is sufficient under the frozen decision rule; the diversity-aware challenger did not provide a material utility/facet tradeoff."), "", "This recommendation is provisional; no scorer or selector is frozen automatically.", ""])
    (temporary / "comparison_report.md").write_text("\n".join(report), encoding="utf-8")
    artifact_names = ["locked_predictions.parquet", "company_ranking_metrics.csv", "candidate_ranking_metrics.csv", "company_budget_metrics.csv", "candidate_budget_metrics.csv", "selected_pages.csv", "comparison_report.md", "config_snapshot.yaml"]
    summary["artifacts"] = {name: {"path": name, "sha256": sha256_file(temporary / name)} for name in artifact_names}
    (temporary / "evaluation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.rename(output_directory)
    print(f"Locked retrieval evaluation: {output_directory}")
    print(f"  Companies: 40; pages: 7,318")
    print(f"  Best ranker: {best_ranker}")
    print(f"  Separate selector warranted: {selector_warranted}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate frozen page rankers on full locked retrieval annotations")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    root = find_project_root()
    config_path = args.config or root / "config" / "locked_retrieval_evaluation.yaml"
    if not config_path.is_absolute():
        config_path = root / config_path
    config = load_evaluation_config(config_path)
    output = args.output_directory or _resolve(root, config.output_directory)
    if not output.is_absolute():
        output = root / output
    run_evaluation(root, config, config_path, output, force_refresh=args.force_refresh)


if __name__ == "__main__":
    main()
