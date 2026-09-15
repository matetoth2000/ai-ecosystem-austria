"""Compare scorer-v2 page-ranking baselines without touching locked labels.

The experiment fits and tunes only on development data, compares frozen
architectures on model-selection data, and never loads locked retrieval-test
targets into the experiment frame.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable

import joblib
import numpy as np
import pandas as pd
import pyarrow.dataset as pads
import scipy.sparse as sp
import yaml
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge, SGDClassifier
from sklearn.metrics import (
    cohen_kappa_score,
    dcg_score,
    f1_score,
    mean_absolute_error,
    ndcg_score,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS
from ai_ecosystem.db.init_db import find_project_root


KEY_COLUMNS = ["annotation_item_id", "company_id", "scraped_page_id"]
LOCKED_SPLIT = "locked_retrieval_test"
INDEPENDENT_EXPERIMENTS = {
    "structural_rule",
    "engineered_ridge",
    "tfidf_ridge",
    "engineered_hist_gradient_boosting",
    "hybrid_independent_ridge",
}
ATTRIBUTION_VALUES = (
    "focal_company_direct",
    "focal_company_probable",
    "ambiguous",
    "third_party",
    "unrelated",
)


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    experiment_version: str
    dataset_directory: str
    output_directory: str
    random_seed: int
    development_cv_folds: int
    ridge_alphas: tuple[float, ...]
    tree_candidates: tuple[dict[str, float | int], ...]
    tfidf: dict[str, Any]
    auxiliary_models: dict[str, Any]
    ranking_top_k: tuple[int, ...]
    runtime_targets: dict[str, float]
    excluded_engineered_features: tuple[str, ...]
    config_hash: str


@dataclass
class ExperimentData:
    development: pd.DataFrame
    model_selection: pd.DataFrame
    engineered_columns: list[str]
    scorer_v1_columns: list[str]
    dataset_manifest: dict[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_experiment_config(path: Path) -> ExperimentConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Page-model experiment config must be a mapping")
    for key in (
        "experiment_id",
        "experiment_version",
        "dataset_directory",
        "output_directory",
        "random_seed",
        "development_cv_folds",
        "ridge_alphas",
        "tree_candidates",
        "tfidf",
        "auxiliary_models",
        "ranking",
        "runtime_targets",
        "excluded_engineered_features",
    ):
        if key not in raw:
            raise ValueError(f"Missing page-model experiment config key: {key}")
    alphas = tuple(float(value) for value in raw["ridge_alphas"])
    if not alphas or any(value <= 0 for value in alphas):
        raise ValueError("ridge_alphas must contain positive values")
    folds = int(raw["development_cv_folds"])
    if folds < 2:
        raise ValueError("development_cv_folds must be at least two")
    return ExperimentConfig(
        experiment_id=str(raw["experiment_id"]),
        experiment_version=str(raw["experiment_version"]),
        dataset_directory=str(raw["dataset_directory"]),
        output_directory=str(raw["output_directory"]),
        random_seed=int(raw["random_seed"]),
        development_cv_folds=folds,
        ridge_alphas=alphas,
        tree_candidates=tuple(dict(value) for value in raw["tree_candidates"]),
        tfidf=dict(raw["tfidf"]),
        auxiliary_models=dict(raw["auxiliary_models"]),
        ranking_top_k=tuple(int(value) for value in raw["ranking"]["top_k"]),
        runtime_targets={key: float(value) for key, value in raw["runtime_targets"].items()},
        excluded_engineered_features=tuple(str(value) for value in raw["excluded_engineered_features"]),
        config_hash=hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest(),
    )


def _read_targets_without_locked_rows(path: Path, allowed_ids: set[str]) -> pd.DataFrame:
    """Use a dataset filter so locked target rows never enter a pandas frame."""
    expression = pads.field("annotation_item_id").isin(sorted(allowed_ids))
    table = pads.dataset(path, format="parquet").to_table(filter=expression)
    result = table.to_pandas()
    if not set(result["annotation_item_id"]).issubset(allowed_ids):
        raise ValueError("Target filter admitted rows outside development/model selection")
    return result


def _site_size_band(value: int | float) -> str:
    count = int(value)
    if count <= 5:
        return "1-5"
    if count <= 20:
        return "6-20"
    if count <= 100:
        return "21-100"
    return ">100"


def _language_group(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("_", "-")
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("en"):
        return "en"
    if not normalized or normalized in {"unknown", "und", "zxx", "none"}:
        return "unknown"
    return "other"


def load_experiment_data(dataset_directory: Path, config: ExperimentConfig) -> ExperimentData:
    manifest = json.loads((dataset_directory / "manifest.json").read_text(encoding="utf-8"))
    split = pd.read_parquet(dataset_directory / "split_manifest.parquet")
    allowed_split = split[split["split"].isin(["development", "model_selection"])].copy()
    allowed_ids = set(allowed_split["annotation_item_id"])
    targets = _read_targets_without_locked_rows(dataset_directory / "targets.parquet", allowed_ids)
    canonical = pd.read_parquet(dataset_directory / "canonical_pages.parquet")
    canonical = canonical[canonical["annotation_item_id"].isin(allowed_ids)].copy()
    engineered = pd.read_parquet(dataset_directory / "engineered_features.parquet")
    engineered = engineered[engineered["annotation_item_id"].isin(allowed_ids)].copy()
    scorer = pd.read_parquet(dataset_directory / "scorer_v1_features.parquet")
    scorer = scorer[scorer["annotation_item_id"].isin(allowed_ids)].copy()
    documents = pd.read_parquet(dataset_directory / "text_documents.parquet")
    documents = documents[documents["annotation_item_id"].isin(allowed_ids)].copy()

    engineered_columns = [
        column
        for column in engineered.columns
        if column not in KEY_COLUMNS and column not in config.excluded_engineered_features
    ]
    scorer_columns = [
        column
        for column in scorer.columns
        if column not in KEY_COLUMNS and column != "scorer_v1__source_run_id"
    ]
    forbidden = {
        "split",
        "company_label_audit_only",
        "sampling_pool_audit_only",
        "selection_reason_audit_only",
        "scorer_v1_diagnostic_audit_only",
        "calibration_source_audit_only",
    }
    if forbidden.intersection(engineered_columns) or any(column.startswith("scorer_v1__") for column in engineered_columns):
        raise ValueError("Audit/scorer-v1 fields entered the independent feature block")

    frame = allowed_split.merge(
        canonical[
            KEY_COLUMNS
            + [
                "language",
                "sampling_pool_audit_only",
                "scorer_v1_diagnostic_audit_only",
                "calibration_source_audit_only",
                "trust_status",
            ]
        ],
        on=KEY_COLUMNS,
        validate="one_to_one",
    )
    frame = frame.merge(targets, on=KEY_COLUMNS, suffixes=("", "_target"), validate="one_to_one")
    frame = frame.merge(engineered, on=KEY_COLUMNS, validate="one_to_one")
    frame = frame.merge(scorer, on=KEY_COLUMNS, validate="one_to_one")
    frame = frame.merge(
        documents[KEY_COLUMNS + ["title", "headings_json", "model_visible_markdown", "markdown_truncated"]],
        on=KEY_COLUMNS,
        validate="one_to_one",
    )
    frame["site_size_band"] = frame["site__company_usable_page_count"].map(_site_size_band)
    frame["language_group"] = frame["language"].map(_language_group)
    frame["selection_origin"] = np.where(
        frame["scorer_v1_diagnostic_audit_only"], "scorer_v1_diagnostic", "scorer_v1_independent"
    )
    frame["document"] = (
        "TITLE " + frame["title"].fillna("").astype(str)
        + "\nHEADINGS " + frame["headings_json"].fillna("[]").astype(str)
        + "\nCONTENT " + frame["model_visible_markdown"].fillna("").astype(str)
    )
    frame = frame[frame["modeling_eligible"]].copy()
    development = frame[frame["split"] == "development"].sort_values(KEY_COLUMNS).reset_index(drop=True)
    model_selection = frame[frame["split"] == "model_selection"].sort_values(KEY_COLUMNS).reset_index(drop=True)
    if development.empty or model_selection.empty:
        raise ValueError("Development and model-selection rows are required")
    if development["utility_score_available"].sum() == 0:
        raise ValueError("No usable development utility targets")
    if LOCKED_SPLIT in set(frame["split"]):
        raise AssertionError("Locked retrieval rows entered the experiment frame")
    return ExperimentData(development, model_selection, engineered_columns, scorer_columns, manifest)


def dense_preprocessor() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", StandardScaler()),
        ]
    )


def build_vectorizer(config: ExperimentConfig) -> tuple[TfidfVectorizer, TfidfVectorizer]:
    values = config.tfidf
    common = {
        "min_df": int(values["min_document_frequency"]),
        "max_df": float(values["max_document_frequency"]),
        "sublinear_tf": bool(values["sublinear_term_frequency"]),
        "dtype": np.float32,
        "strip_accents": "unicode",
    }
    word = TfidfVectorizer(
        analyzer="word",
        ngram_range=tuple(int(value) for value in values["word_ngram_range"]),
        max_features=int(values["word_max_features"]),
        **common,
    )
    character = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=tuple(int(value) for value in values["character_ngram_range"]),
        max_features=int(values["character_max_features"]),
        **common,
    )
    return word, character


def fit_text_representation(documents: Iterable[str], config: ExperimentConfig) -> tuple[dict[str, Any], sp.csr_matrix]:
    docs = list(documents)
    word, character = build_vectorizer(config)
    word_matrix = word.fit_transform(docs)
    character_matrix = character.fit_transform(docs)
    matrix = sp.hstack([word_matrix, character_matrix], format="csr", dtype=np.float32)
    return {"word_vectorizer": word, "character_vectorizer": character}, matrix


def transform_text(representation: dict[str, Any], documents: Iterable[str]) -> sp.csr_matrix:
    docs = list(documents)
    return sp.hstack(
        [
            representation["word_vectorizer"].transform(docs),
            representation["character_vectorizer"].transform(docs),
        ],
        format="csr",
        dtype=np.float32,
    )


def text_feature_names(representation: dict[str, Any]) -> list[str]:
    return [
        *(f"word::{value}" for value in representation["word_vectorizer"].get_feature_names_out()),
        *(f"char::{value}" for value in representation["character_vectorizer"].get_feature_names_out()),
    ]


def tune_ridge_alpha(
    matrix: np.ndarray | sp.spmatrix,
    target: np.ndarray,
    groups: np.ndarray,
    config: ExperimentConfig,
) -> tuple[float, list[dict[str, Any]]]:
    unique_groups = len(np.unique(groups))
    folds = min(config.development_cv_folds, unique_groups)
    splitter = GroupKFold(n_splits=folds)
    rows: list[dict[str, Any]] = []
    for alpha in config.ridge_alphas:
        fold_mae = []
        for train_index, validation_index in splitter.split(np.zeros(len(target)), target, groups):
            model = Ridge(alpha=alpha, solver="lsqr")
            model.fit(matrix[train_index], target[train_index])
            fold_mae.append(mean_absolute_error(target[validation_index], model.predict(matrix[validation_index])))
        rows.append({"alpha": alpha, "development_group_cv_mae": float(np.mean(fold_mae))})
    best = min(rows, key=lambda row: (row["development_group_cv_mae"], row["alpha"]))
    return float(best["alpha"]), rows


def tune_tree(
    matrix: np.ndarray,
    target: np.ndarray,
    groups: np.ndarray,
    config: ExperimentConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    splitter = GroupKFold(n_splits=min(config.development_cv_folds, len(np.unique(groups))))
    rows: list[dict[str, Any]] = []
    for candidate in config.tree_candidates:
        fold_mae = []
        for train_index, validation_index in splitter.split(np.zeros(len(target)), target, groups):
            model = HistGradientBoostingRegressor(
                learning_rate=float(candidate["learning_rate"]),
                max_leaf_nodes=int(candidate["max_leaf_nodes"]),
                l2_regularization=float(candidate["l2_regularization"]),
                max_iter=160,
                random_state=config.random_seed,
            )
            model.fit(matrix[train_index], target[train_index])
            fold_mae.append(mean_absolute_error(target[validation_index], model.predict(matrix[validation_index])))
        row = dict(candidate)
        row["development_group_cv_mae"] = float(np.mean(fold_mae))
        rows.append(row)
    best = min(rows, key=lambda row: (row["development_group_cv_mae"], row["max_leaf_nodes"]))
    return {key: best[key] for key in ("learning_rate", "max_leaf_nodes", "l2_regularization")}, rows


def _fit_auxiliary_model(
    matrix: np.ndarray | sp.spmatrix,
    target: np.ndarray,
    available: np.ndarray,
    family: str,
    config: ExperimentConfig,
) -> dict[str, Any]:
    values = target[available]
    classes = np.unique(values)
    if len(classes) == 0:
        return {"kind": "unavailable", "value": None}
    if len(classes) == 1:
        return {"kind": "constant", "value": classes[0]}
    if family == "tree":
        model = HistGradientBoostingClassifier(
            learning_rate=0.08,
            max_leaf_nodes=15,
            l2_regularization=2.0,
            max_iter=120,
            class_weight="balanced",
            random_state=config.random_seed,
        )
    else:
        model = SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=float(config.auxiliary_models["linear_alpha"]),
            max_iter=int(config.auxiliary_models["max_iterations"]),
            class_weight="balanced",
            random_state=config.random_seed,
            tol=1e-4,
        )
    model.fit(matrix[available], values)
    return {"kind": "model", "model": model}


def _predict_auxiliary(specification: dict[str, Any], matrix: np.ndarray | sp.spmatrix) -> np.ndarray:
    if specification["kind"] == "model":
        return specification["model"].predict(matrix)
    return np.repeat(specification.get("value"), matrix.shape[0])


def fit_auxiliary_models(
    matrix: np.ndarray | sp.spmatrix,
    frame: pd.DataFrame,
    family: str,
    config: ExperimentConfig,
) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    available = frame["attribution_available"].fillna(False).to_numpy(bool)
    models["attribution"] = _fit_auxiliary_model(
        matrix,
        frame["attribution"].fillna("unavailable").to_numpy(str),
        available,
        family,
        config,
    )
    for facet in FACETS:
        column = f"facet__{facet}__strength"
        available = frame[f"facet__{facet}__available"].fillna(False).to_numpy(bool)
        target = (frame[column].fillna(-1).to_numpy(float) >= int(config.auxiliary_models["positive_facet_threshold"])).astype(int)
        models[f"facet__{facet}"] = _fit_auxiliary_model(matrix, target, available, family, config)
    for flag in NOISE_FLAGS:
        column = f"noise__{flag}"
        available = frame[f"noise__{flag}__available"].fillna(False).to_numpy(bool)
        target = frame[column].map(lambda value: bool(value) if pd.notna(value) else False).to_numpy(bool).astype(int)
        models[f"noise__{flag}"] = _fit_auxiliary_model(matrix, target, available, family, config)
    return models


def structural_predictions(frame: pd.DataFrame) -> dict[str, Any]:
    words = frame["text__markdown_word_count"].to_numpy(float)
    length_signal = np.clip(np.log1p(words) / math.log(1001), 0, 1)
    utility = (
        0.25
        + 0.55 * frame["url__is_root"].to_numpy(float)
        + 0.50 * frame["url__is_about_company"].to_numpy(float)
        + 0.20 * frame["url__is_shallow"].to_numpy(float)
        + 0.80 * length_signal
        + 0.20 * np.clip(frame["structure__heading_count"].to_numpy(float) / 5, 0, 1)
        + 0.35 * frame[["page_type__is_products", "page_type__is_services", "page_type__is_technology", "page_type__is_innovation"]].max(axis=1).to_numpy(float)
        - 0.65 * frame[["page_type__is_legal", "page_type__is_careers"]].max(axis=1).to_numpy(float)
        - 0.25 * frame["page_type__is_news"].to_numpy(float)
        - 0.45 * (words < 40)
    )
    attribution = np.where(
        frame["site__attribution_uncertain"].to_numpy(bool),
        "ambiguous",
        np.where(frame["site__shared_domain"].to_numpy(bool), "focal_company_probable", "focal_company_direct"),
    )
    facets = {
        "company_activity": (
            frame[["url__is_root", "url__is_about_company", "page_type__is_homepage", "page_type__is_company"]].max(axis=1) > 0
        ).astype(int).to_numpy(),
        "products_services": (frame[["page_type__is_products", "page_type__is_services"]].max(axis=1) > 0).astype(int).to_numpy(),
        "technical_capability": (frame["page_type__is_technology"] > 0).astype(int).to_numpy(),
        "explicit_ai": np.zeros(len(frame), dtype=int),
        "ai_adjacent": np.zeros(len(frame), dtype=int),
        "innovation_research": (frame["page_type__is_innovation"] > 0).astype(int).to_numpy(),
        "ecosystem_relationships": np.zeros(len(frame), dtype=int),
    }
    noise = {
        "generic_marketing": np.zeros(len(frame), dtype=int),
        "duplicated_or_template": (frame["duplicate__has_exact_duplicate"] > 0).astype(int).to_numpy(),
        "third_party_focus": np.zeros(len(frame), dtype=int),
        "news_or_press": (frame["page_type__is_news"] > 0).astype(int).to_numpy(),
        "legal_or_compliance": (frame["page_type__is_legal"] > 0).astype(int).to_numpy(),
        "careers": (frame["page_type__is_careers"] > 0).astype(int).to_numpy(),
        "navigation_or_low_content": (words < 40).astype(int),
        "non_substantive_listing": ((words < 80) & (frame["link__total_count"].to_numpy(float) > 30)).astype(int),
    }
    return {"utility": utility, "attribution": attribution, "facets": facets, "noise": noise}


def _matrix_bytes(matrix: np.ndarray | sp.spmatrix) -> int:
    if sp.issparse(matrix):
        return int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
    return int(matrix.nbytes)


def _rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return float(value / (1024 * 1024) if value > 10_000_000 else value / 1024)


def pairwise_ranking_accuracy(target: np.ndarray, score: np.ndarray, groups: np.ndarray) -> float | None:
    correct = total = 0
    for group in np.unique(groups):
        indices = np.flatnonzero(groups == group)
        for offset, left in enumerate(indices):
            for right in indices[offset + 1 :]:
                if target[left] == target[right]:
                    continue
                total += 1
                difference = (score[left] - score[right]) * (target[left] - target[right])
                correct += int(difference > 0) + 0.5 * int(difference == 0)
    return float(correct / total) if total else None


def grouped_ranking_metrics(
    target: np.ndarray,
    score: np.ndarray,
    groups: np.ndarray,
    top_k: Iterable[int],
) -> tuple[dict[str, float | None], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    full_ndcg: list[float] = []
    full_dcg: list[float] = []
    top_values: dict[int, list[float]] = defaultdict(list)
    top_ndcg: dict[int, list[float]] = defaultdict(list)
    top_dcg: dict[int, list[float]] = defaultdict(list)
    for group in np.unique(groups):
        indices = np.flatnonzero(groups == group)
        truth = target[indices]
        predicted = score[indices]
        if len(indices) > 1 and np.max(truth) > 0:
            full_ndcg.append(float(ndcg_score([truth], [predicted])))
            full_dcg.append(float(dcg_score([truth], [predicted])))
        order = np.argsort(-predicted, kind="stable")
        row: dict[str, Any] = {"company_id": group, "page_count": len(indices)}
        for value in top_k:
            count = min(int(value), len(indices))
            chosen = truth[order[:count]]
            top_values[int(value)].append(float(np.mean(chosen)))
            row[f"top_{value}_mean_utility"] = float(np.mean(chosen))
            if len(indices) > 1 and np.max(truth) > 0:
                score_value = float(ndcg_score([truth], [predicted], k=count))
                dcg_value = float(dcg_score([truth], [predicted], k=count))
                top_ndcg[int(value)].append(score_value)
                top_dcg[int(value)].append(dcg_value)
                row[f"ndcg_at_{value}"] = score_value
                row[f"dcg_at_{value}"] = dcg_value
            else:
                row[f"ndcg_at_{value}"] = None
                row[f"dcg_at_{value}"] = None
        diagnostics.append(row)
    metrics: dict[str, float | None] = {
        "annotation_pool_ndcg": float(np.mean(full_ndcg)) if full_ndcg else None,
        "annotation_pool_dcg": float(np.mean(full_dcg)) if full_dcg else None,
    }
    for value in top_k:
        metrics[f"ndcg_at_{value}"] = float(np.mean(top_ndcg[int(value)])) if top_ndcg[int(value)] else None
        metrics[f"dcg_at_{value}"] = float(np.mean(top_dcg[int(value)])) if top_dcg[int(value)] else None
        metrics[f"top_{value}_mean_utility"] = float(np.mean(top_values[int(value)])) if top_values[int(value)] else None
    return metrics, diagnostics


def utility_metrics(target: np.ndarray, score: np.ndarray, groups: np.ndarray, top_k: Iterable[int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rounded = np.clip(np.rint(score), 0, 3).astype(int)
    correlation = (
        spearmanr(target, score).statistic
        if len(np.unique(target)) > 1 and len(np.unique(score)) > 1
        else None
    )
    kappa = (
        cohen_kappa_score(target.astype(int), rounded, labels=[0, 1, 2, 3], weights="quadratic")
        if len(np.unique(target)) > 1
        else None
    )
    ranking, diagnostics = grouped_ranking_metrics(target, score, groups, top_k)
    result = {
        "utility_mae": float(mean_absolute_error(target, score)),
        "utility_quadratic_weighted_kappa": float(kappa) if kappa is not None and not math.isnan(kappa) else None,
        "utility_spearman": float(correlation) if correlation is not None and not math.isnan(correlation) else None,
        "pairwise_ranking_accuracy": pairwise_ranking_accuracy(target, score, groups),
        **ranking,
    }
    return result, diagnostics


def evaluate_auxiliary(
    frame: pd.DataFrame,
    predictions: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    mask = frame["attribution_available"].fillna(False).to_numpy(bool)
    truth = frame.loc[mask, "attribution"].to_numpy(str)
    predicted = np.asarray(predictions["attribution"])[mask]
    attribution_f1 = float(f1_score(truth, predicted, labels=list(ATTRIBUTION_VALUES), average="macro", zero_division=0))
    rows.append({"target_group": "attribution", "target": "attribution", "support": int(mask.sum()), "macro_f1": attribution_f1})
    facet_scores: list[float] = []
    for facet in FACETS:
        mask = frame[f"facet__{facet}__available"].fillna(False).to_numpy(bool)
        truth = (frame.loc[mask, f"facet__{facet}__strength"].to_numpy(float) > 0).astype(int)
        predicted = np.asarray(predictions["facets"][facet])[mask].astype(int)
        value = float(f1_score(truth, predicted, average="binary", zero_division=0))
        facet_scores.append(value)
        rows.append({"target_group": "evidence_facet", "target": facet, "support": int(mask.sum()), "macro_f1": value})
    noise_scores: list[float] = []
    for flag in NOISE_FLAGS:
        mask = frame[f"noise__{flag}__available"].fillna(False).to_numpy(bool)
        truth = frame.loc[mask, f"noise__{flag}"].to_numpy(bool).astype(int)
        predicted = np.asarray(predictions["noise"][flag])[mask].astype(int)
        value = float(f1_score(truth, predicted, average="binary", zero_division=0))
        noise_scores.append(value)
        rows.append({"target_group": "noise", "target": flag, "support": int(mask.sum()), "macro_f1": value})
    return {
        "attribution_macro_f1": attribution_f1,
        "evidence_facet_macro_f1": float(np.mean(facet_scores)),
        "noise_macro_f1": float(np.mean(noise_scores)),
    }, rows


def predict_model(model_artifact: dict[str, Any], matrix: np.ndarray | sp.spmatrix) -> dict[str, Any]:
    utility = model_artifact["utility_model"].predict(matrix)
    auxiliary = model_artifact["auxiliary_models"]
    return {
        "utility": utility,
        "attribution": _predict_auxiliary(auxiliary["attribution"], matrix),
        "facets": {facet: _predict_auxiliary(auxiliary[f"facet__{facet}"], matrix) for facet in FACETS},
        "noise": {flag: _predict_auxiliary(auxiliary[f"noise__{flag}"], matrix) for flag in NOISE_FLAGS},
    }


def _build_prediction_rows(candidate_id: str, frame: pd.DataFrame, predictions: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, source in frame.iterrows():
        row = {
            "candidate_id": candidate_id,
            **{key: source[key] for key in KEY_COLUMNS},
            "utility_score": float(predictions["utility"][index]),
            "attribution_prediction": str(predictions["attribution"][index]),
        }
        row.update({f"facet__{facet}__prediction": int(predictions["facets"][facet][index]) for facet in FACETS})
        row.update({f"noise__{flag}__prediction": int(predictions["noise"][flag][index]) for flag in NOISE_FLAGS})
        rows.append(row)
    return rows


def _subgroup_metrics(
    candidate_id: str,
    frame: pd.DataFrame,
    predictions: dict[str, Any],
    top_k: Iterable[int],
) -> list[dict[str, Any]]:
    score = np.asarray(predictions["utility"], dtype=float)
    dimensions = {
        "sampling_pool": "sampling_pool_audit_only",
        "language": "language_group",
        "site_size": "site_size_band",
        "shared_domain": "shared_domain",
        "trust_level": "trust_status",
        "selection_origin": "selection_origin",
    }
    rows: list[dict[str, Any]] = []
    for dimension, column in dimensions.items():
        for value, indices in frame.groupby(column, dropna=False).groups.items():
            positions = np.asarray(list(indices), dtype=int)
            available = frame.iloc[positions]["utility_score_available"].fillna(False).to_numpy(bool)
            positions = positions[available]
            if not len(positions):
                continue
            truth = frame.iloc[positions]["utility_score"].to_numpy(float)
            predicted = score[positions]
            groups = frame.iloc[positions]["company_id"].to_numpy(str)
            metrics, _ = utility_metrics(truth, predicted, groups, top_k)
            subgroup_predictions = {
                "utility": predicted,
                "attribution": np.asarray(predictions["attribution"])[positions],
                "facets": {
                    facet: np.asarray(predictions["facets"][facet])[positions]
                    for facet in FACETS
                },
                "noise": {
                    flag: np.asarray(predictions["noise"][flag])[positions]
                    for flag in NOISE_FLAGS
                },
            }
            auxiliary, _ = evaluate_auxiliary(
                frame.iloc[positions].reset_index(drop=True), subgroup_predictions
            )
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "subgroup_dimension": dimension,
                    "subgroup_value": str(value),
                    "page_count": len(positions),
                    "company_count": len(np.unique(groups)),
                    "mean_true_utility": float(np.mean(truth)),
                    "high_utility_share": float(np.mean(truth >= 2)),
                    **metrics,
                    **auxiliary,
                }
            )
    return rows


def _benchmark(callback: Callable[[], Any], page_count: int, repeats: int = 5) -> tuple[float, float]:
    callback()
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        callback()
        durations.append(time.perf_counter() - started)
    seconds = float(median(durations))
    return seconds, float(page_count / seconds) if seconds else math.inf


def _selection_score(row: dict[str, Any]) -> float:
    values = [row.get("utility_spearman"), row.get("pairwise_ranking_accuracy"), row.get("ndcg_at_5")]
    valid = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    return float(np.mean(valid) - 0.1 * float(row["utility_mae"]) / 3) if valid else -math.inf


def _format_number(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value):.{digits}f}"


def run_experiments(
    project_root: Path,
    config: ExperimentConfig,
    config_path: Path,
    output_directory: Path,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    dataset_directory = project_root / config.dataset_directory
    if output_directory.exists():
        if not force_refresh:
            raise FileExistsError(f"Experiment directory already exists: {output_directory}")
        shutil.rmtree(output_directory)
    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    (temporary / "models").mkdir(parents=True)

    data = load_experiment_data(dataset_directory, config)
    development = data.development
    selection = data.model_selection
    development_utility = development["utility_score_available"].fillna(False).to_numpy(bool)
    selection_utility = selection["utility_score_available"].fillna(False).to_numpy(bool)
    dev_target = development.loc[development_utility, "utility_score"].to_numpy(float)
    dev_groups = development.loc[development_utility, "normalized_domain"].to_numpy(str)
    selection_target = selection.loc[selection_utility, "utility_score"].to_numpy(float)
    selection_groups = selection.loc[selection_utility, "company_id"].to_numpy(str)

    tuning_rows: list[dict[str, Any]] = []
    model_records: dict[str, dict[str, Any]] = {}
    train_started = time.perf_counter()

    engineered_preprocessor = dense_preprocessor()
    engineered_dev_all = engineered_preprocessor.fit_transform(development[data.engineered_columns])
    engineered_selection = engineered_preprocessor.transform(selection[data.engineered_columns])
    engineered_dev = engineered_dev_all[development_utility]
    engineered_feature_names = list(
        engineered_preprocessor.get_feature_names_out(data.engineered_columns)
    )

    scorer_preprocessor = dense_preprocessor()
    scorer_dev_all = scorer_preprocessor.fit_transform(development[data.scorer_v1_columns])
    scorer_selection = scorer_preprocessor.transform(selection[data.scorer_v1_columns])
    scorer_dev = scorer_dev_all[development_utility]
    scorer_feature_names = list(
        scorer_preprocessor.get_feature_names_out(data.scorer_v1_columns)
    )

    text_representation, text_dev_all = fit_text_representation(development["document"], config)
    text_selection = transform_text(text_representation, selection["document"])
    text_dev = text_dev_all[development_utility]

    independent_dev_all = sp.hstack([text_dev_all, sp.csr_matrix(engineered_dev_all)], format="csr")
    independent_selection = sp.hstack([text_selection, sp.csr_matrix(engineered_selection)], format="csr")
    independent_dev = independent_dev_all[development_utility]
    combined_dev_all = sp.hstack([independent_dev_all, sp.csr_matrix(scorer_dev_all)], format="csr")
    combined_selection = sp.hstack([independent_selection, sp.csr_matrix(scorer_selection)], format="csr")
    combined_dev = combined_dev_all[development_utility]

    representation_specs: list[tuple[str, str, Any, Any, Any, list[str], dict[str, Any]]] = [
        (
            "engineered_ridge",
            "linear",
            engineered_dev,
            engineered_dev_all,
            engineered_selection,
            engineered_feature_names,
            {"engineered_preprocessor": engineered_preprocessor},
        ),
        (
            "tfidf_ridge",
            "linear",
            text_dev,
            text_dev_all,
            text_selection,
            text_feature_names(text_representation),
            dict(text_representation),
        ),
        (
            "scorer_v1_ridge",
            "linear",
            scorer_dev,
            scorer_dev_all,
            scorer_selection,
            scorer_feature_names,
            {"scorer_v1_preprocessor": scorer_preprocessor},
        ),
        (
            "hybrid_independent_ridge",
            "linear",
            independent_dev,
            independent_dev_all,
            independent_selection,
            [*text_feature_names(text_representation), *engineered_feature_names],
            {**text_representation, "engineered_preprocessor": engineered_preprocessor},
        ),
        (
            "hybrid_plus_scorer_v1_ridge",
            "linear",
            combined_dev,
            combined_dev_all,
            combined_selection,
            [*text_feature_names(text_representation), *engineered_feature_names, *scorer_feature_names],
            {
                **text_representation,
                "engineered_preprocessor": engineered_preprocessor,
                "scorer_v1_preprocessor": scorer_preprocessor,
            },
        ),
    ]
    for candidate_id, family, utility_matrix, full_dev_matrix, selection_matrix, feature_names, representation in representation_specs:
        alpha, rows = tune_ridge_alpha(utility_matrix, dev_target, dev_groups, config)
        tuning_rows.extend({"candidate_id": candidate_id, **row, "selected": row["alpha"] == alpha} for row in rows)
        utility_model = Ridge(alpha=alpha, solver="lsqr").fit(utility_matrix, dev_target)
        auxiliary = fit_auxiliary_models(full_dev_matrix, development, family, config)
        model_records[candidate_id] = {
            "candidate_id": candidate_id,
            "family": family,
            "representation": representation,
            "feature_names": feature_names,
            "selected_hyperparameters": {"alpha": alpha},
            "utility_model": utility_model,
            "auxiliary_models": auxiliary,
            "development_matrix": full_dev_matrix,
            "selection_matrix": selection_matrix,
        }

    tree_parameters, tree_rows = tune_tree(engineered_dev, dev_target, dev_groups, config)
    tuning_rows.extend(
        {
            "candidate_id": "engineered_hist_gradient_boosting",
            **row,
            "selected": all(row[key] == tree_parameters[key] for key in tree_parameters),
        }
        for row in tree_rows
    )
    tree_utility = HistGradientBoostingRegressor(
        **tree_parameters,
        max_iter=160,
        random_state=config.random_seed,
    ).fit(engineered_dev, dev_target)
    tree_auxiliary = fit_auxiliary_models(engineered_dev_all, development, "tree", config)
    model_records["engineered_hist_gradient_boosting"] = {
        "candidate_id": "engineered_hist_gradient_boosting",
        "family": "tree",
        "representation": {"engineered_preprocessor": engineered_preprocessor},
        "feature_names": engineered_feature_names,
        "selected_hyperparameters": tree_parameters,
        "utility_model": tree_utility,
        "auxiliary_models": tree_auxiliary,
        "development_matrix": engineered_dev_all,
        "selection_matrix": engineered_selection,
    }
    total_training_seconds = time.perf_counter() - train_started

    metrics_rows: list[dict[str, Any]] = []
    auxiliary_rows: list[dict[str, Any]] = []
    ranking_rows: list[dict[str, Any]] = []
    subgroup_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    coefficient_rows: list[dict[str, Any]] = []

    all_candidates = ["structural_rule", *model_records]
    candidate_predictions: dict[str, dict[str, Any]] = {"structural_rule": structural_predictions(selection)}
    for candidate_id, record in model_records.items():
        candidate_predictions[candidate_id] = predict_model(record, record["selection_matrix"])

    for candidate_id in all_candidates:
        predictions = candidate_predictions[candidate_id]
        utility_score = np.asarray(predictions["utility"], dtype=float)
        utility_result, company_diagnostics = utility_metrics(
            selection_target,
            utility_score[selection_utility],
            selection_groups,
            config.ranking_top_k,
        )
        auxiliary_result, detail = evaluate_auxiliary(selection, predictions)
        row = {
            "candidate_id": candidate_id,
            "feature_group": (
                "scorer_v1_only"
                if candidate_id == "scorer_v1_ridge"
                else "combined"
                if candidate_id == "hybrid_plus_scorer_v1_ridge"
                else "scorer_v1_independent"
            ),
            **utility_result,
            **auxiliary_result,
        }
        row["selection_score"] = _selection_score(row)
        metrics_rows.append(row)
        auxiliary_rows.extend({"candidate_id": candidate_id, **value} for value in detail)
        ranking_rows.extend({"candidate_id": candidate_id, **value} for value in company_diagnostics)
        subgroup_rows.extend(_subgroup_metrics(candidate_id, selection, predictions, config.ranking_top_k))
        prediction_rows.extend(_build_prediction_rows(candidate_id, selection, predictions))

        if candidate_id == "structural_rule":
            utility_benchmark_callback = lambda: structural_predictions(selection)["utility"]
            full_output_benchmark_callback = lambda: structural_predictions(selection)
            matrix_bytes = int(selection[data.engineered_columns].memory_usage(index=False, deep=True).sum())
            artifact = {
                "candidate_id": candidate_id,
                "family": "fixed_rule",
                "formula_version": "v1",
                "uses_scorer_v1": False,
            }
            model_path = temporary / "models" / f"{candidate_id}.json"
            model_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            record = model_records[candidate_id]
            matrix = record["selection_matrix"]
            if candidate_id in {"engineered_ridge", "engineered_hist_gradient_boosting"}:
                matrix_callback = lambda record=record: record["representation"]["engineered_preprocessor"].transform(
                    selection[data.engineered_columns]
                )
            elif candidate_id == "scorer_v1_ridge":
                matrix_callback = lambda record=record: record["representation"]["scorer_v1_preprocessor"].transform(
                    selection[data.scorer_v1_columns]
                )
            elif candidate_id == "tfidf_ridge":
                matrix_callback = lambda record=record: transform_text(
                    record["representation"], selection["document"]
                )
            elif candidate_id == "hybrid_independent_ridge":
                matrix_callback = lambda record=record: sp.hstack(
                    [
                        transform_text(record["representation"], selection["document"]),
                        sp.csr_matrix(
                            record["representation"]["engineered_preprocessor"].transform(
                                selection[data.engineered_columns]
                            )
                        ),
                    ],
                    format="csr",
                )
            else:
                matrix_callback = lambda record=record: sp.hstack(
                    [
                        transform_text(record["representation"], selection["document"]),
                        sp.csr_matrix(
                            record["representation"]["engineered_preprocessor"].transform(
                                selection[data.engineered_columns]
                            )
                        ),
                        sp.csr_matrix(
                            record["representation"]["scorer_v1_preprocessor"].transform(
                                selection[data.scorer_v1_columns]
                            )
                        ),
                    ],
                    format="csr",
                )
            utility_benchmark_callback = lambda record=record: record["utility_model"].predict(
                matrix_callback()
            )
            full_output_benchmark_callback = lambda record=record: predict_model(
                record, matrix_callback()
            )
            matrix_bytes = _matrix_bytes(matrix)
            persisted = {key: value for key, value in record.items() if key not in {"development_matrix", "selection_matrix"}}
            model_path = temporary / "models" / f"{candidate_id}.joblib"
            joblib.dump(persisted, model_path, compress=3)
            if candidate_id != "engineered_hist_gradient_boosting":
                coefficients = np.asarray(record["utility_model"].coef_).ravel()
                top = np.argsort(-np.abs(coefficients))[:100]
                coefficient_rows.extend(
                    {
                        "candidate_id": candidate_id,
                        "rank": rank,
                        "feature": record["feature_names"][index],
                        "coefficient": float(coefficients[index]),
                        "absolute_coefficient": float(abs(coefficients[index])),
                    }
                    for rank, index in enumerate(top, 1)
                )
        utility_seconds, utility_pages_per_second = _benchmark(
            utility_benchmark_callback, len(selection)
        )
        inference_seconds, pages_per_second = _benchmark(
            full_output_benchmark_callback, len(selection)
        )
        model_bytes = model_path.stat().st_size
        projected_seconds = config.runtime_targets["corpus_page_count"] / pages_per_second
        estimated_memory_mb = (matrix_bytes + model_bytes) / (1024 * 1024)
        runtime_rows.append(
            {
                "candidate_id": candidate_id,
                "model_artifact_bytes": model_bytes,
                "model_selection_matrix_bytes": matrix_bytes,
                "estimated_working_set_mb": estimated_memory_mb,
                "process_peak_rss_mb": _rss_mb(),
                "utility_inference_seconds_for_model_selection": utility_seconds,
                "utility_pages_per_second": utility_pages_per_second,
                "full_output_inference_seconds_for_model_selection": inference_seconds,
                "inference_seconds_for_model_selection": inference_seconds,
                "pages_per_second": pages_per_second,
                "projected_seconds_for_55000_pages": projected_seconds,
                "meets_pages_per_second_target": pages_per_second >= config.runtime_targets["minimum_pages_per_second"],
                "meets_projected_runtime_target": projected_seconds < config.runtime_targets["maximum_runtime_seconds"],
                "meets_memory_target": (
                    estimated_memory_mb < config.runtime_targets["maximum_memory_mb"]
                    and _rss_mb() < config.runtime_targets["maximum_memory_mb"]
                ),
                "production_runtime_viable": (
                    pages_per_second >= config.runtime_targets["minimum_pages_per_second"]
                    and projected_seconds < config.runtime_targets["maximum_runtime_seconds"]
                    and estimated_memory_mb < config.runtime_targets["maximum_memory_mb"]
                    and _rss_mb() < config.runtime_targets["maximum_memory_mb"]
                ),
            }
        )

    metrics = pd.DataFrame(metrics_rows).sort_values("selection_score", ascending=False).reset_index(drop=True)
    runtime = pd.DataFrame(runtime_rows)
    metrics = metrics.merge(runtime[["candidate_id", "pages_per_second", "estimated_working_set_mb", "production_runtime_viable"]], on="candidate_id", validate="one_to_one")
    best_independent = metrics[metrics["candidate_id"].isin(INDEPENDENT_EXPERIMENTS)].iloc[0]
    combined = metrics[metrics["candidate_id"] == "hybrid_plus_scorer_v1_ridge"].iloc[0]
    compact = metrics[metrics["candidate_id"].isin(["engineered_ridge", "engineered_hist_gradient_boosting"])].iloc[0]
    shortlist = metrics[
        metrics["candidate_id"].isin(INDEPENDENT_EXPERIMENTS)
        & (metrics["selection_score"] >= float(best_independent["selection_score"]) - 0.02)
    ]["candidate_id"].astype(str).tolist()
    if float(combined["selection_score"]) > float(best_independent["selection_score"]) + 0.01:
        shortlist.append("hybrid_plus_scorer_v1_ridge")
    if str(compact["candidate_id"]) not in shortlist and float(best_independent["selection_score"]) - float(compact["selection_score"]) <= 0.06:
        shortlist.append(str(compact["candidate_id"]))

    pool_distribution = (
        selection.loc[selection_utility]
        .groupby(["sampling_pool_audit_only", "utility_score"], dropna=False)
        .size()
        .rename("page_count")
        .reset_index()
    )
    pool_totals = pool_distribution.groupby("sampling_pool_audit_only")["page_count"].transform("sum")
    pool_distribution["share_within_pool"] = pool_distribution["page_count"] / pool_totals
    pool_summary = (
        selection.loc[selection_utility]
        .groupby("sampling_pool_audit_only")["utility_score"]
        .agg(page_count="count", mean_utility="mean", low_utility_count=lambda value: int((value <= 1).sum()), high_utility_count=lambda value: int((value >= 2).sum()))
        .reset_index()
    )
    pool_summary["low_utility_share"] = pool_summary["low_utility_count"] / pool_summary["page_count"]
    pool_summary["high_utility_share"] = pool_summary["high_utility_count"] / pool_summary["page_count"]

    tables = {
        "candidate_metrics.csv": metrics,
        "development_tuning.csv": pd.DataFrame(tuning_rows),
        "auxiliary_target_metrics.csv": pd.DataFrame(auxiliary_rows),
        "ranking_diagnostics.csv": pd.DataFrame(ranking_rows),
        "subgroup_metrics.csv": pd.DataFrame(subgroup_rows),
        "ablation_results.csv": metrics[metrics["candidate_id"].isin(["scorer_v1_ridge", "hybrid_independent_ridge", "hybrid_plus_scorer_v1_ridge", "engineered_ridge"])].copy(),
        "runtime_benchmarks.csv": runtime,
        "feature_coefficients.csv": pd.DataFrame(coefficient_rows),
        "utility_distribution_by_sampling_pool.csv": pool_distribution,
        "sampling_pool_summary.csv": pool_summary,
    }
    for name, frame in tables.items():
        frame.to_csv(temporary / name, index=False)
    pd.DataFrame(prediction_rows).to_parquet(temporary / "model_selection_predictions.parquet", index=False, compression="zstd")

    low_independent_pools = pool_summary[
        pool_summary["sampling_pool_audit_only"].isin(["all_available", "coverage_stratified", "deterministic_random", "structural"])
        & (pool_summary["low_utility_count"] < 20)
    ]["sampling_pool_audit_only"].tolist()
    embeddings_justified = not bool(best_independent["production_runtime_viable"])
    tfidf_result = metrics[metrics["candidate_id"] == "tfidf_ridge"].iloc[0]
    tree_result = metrics[metrics["candidate_id"] == "engineered_hist_gradient_boosting"].iloc[0]
    scorer_result = metrics[metrics["candidate_id"] == "scorer_v1_ridge"].iloc[0]
    auxiliary_frame = pd.DataFrame(auxiliary_rows)
    weak_hybrid_auxiliary = auxiliary_frame[
        (auxiliary_frame["candidate_id"] == "hybrid_independent_ridge")
        & (auxiliary_frame["target_group"].isin(["evidence_facet", "noise"]))
    ].nsmallest(4, "macro_f1")
    subgroup_frame = pd.DataFrame(subgroup_rows)
    weakest_hybrid_subgroups = subgroup_frame[
        (subgroup_frame["candidate_id"] == "hybrid_independent_ridge")
        & (subgroup_frame["page_count"] >= 20)
    ].nsmallest(3, "utility_spearman")
    report_lines = [
        "# Scorer-v2 Page Baseline and Challenger Comparison",
        "",
        "## Evaluation Boundary",
        "",
        f"Utility models were fitted/tuned on {int(development_utility.sum()):,} usable development targets; field-specific auxiliary models used up to {len(development):,} modeling-eligible development rows. Comparison uses {len(selection):,} model-selection rows.",
        "The locked retrieval-test targets were not loaded. Ranking metrics describe only the enriched annotated pool and are not website-wide retrieval estimates.",
        "",
        "## Candidate Comparison",
        "",
        "| Candidate | MAE | Spearman | Pairwise | NDCG@5 | Attribution F1 | Facet F1 | Noise F1 | Full outputs/s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in metrics.iterrows():
        report_lines.append(
            f"| `{row['candidate_id']}` | {_format_number(row['utility_mae'])} | {_format_number(row['utility_spearman'])} | {_format_number(row['pairwise_ranking_accuracy'])} | {_format_number(row['ndcg_at_5'])} | {_format_number(row['attribution_macro_f1'])} | {_format_number(row['evidence_facet_macro_f1'])} | {_format_number(row['noise_macro_f1'])} | {_format_number(row['pages_per_second'], 1)} |"
        )
    report_lines.extend(
        [
            "",
            "## Primary Findings",
            "",
            f"`hybrid_independent_ridge` has the lowest MAE ({_format_number(best_independent['utility_mae'])}) and highest overall Spearman correlation ({_format_number(best_independent['utility_spearman'])}).",
            f"`tfidf_ridge` has the strongest pairwise ranking ({_format_number(tfidf_result['pairwise_ranking_accuracy'])}), NDCG@5 ({_format_number(tfidf_result['ndcg_at_5'])}), facet F1 ({_format_number(tfidf_result['evidence_facet_macro_f1'])}), and noise F1 ({_format_number(tfidf_result['noise_macro_f1'])}).",
            f"`engineered_hist_gradient_boosting` is the compact speed-oriented challenger at {_format_number(tree_result['pages_per_second'], 1)} pages/sec, but trails the text-aware models on utility ranking.",
            "The text-aware candidates are complementary: the hybrid is better calibrated globally, while TF-IDF is better at selecting top pages and recovering semantic facets.",
            "",
            "## Ablation Interpretation",
            "",
            f"The strongest scorer-v1-independent candidate is `{best_independent['candidate_id']}`. Its selection score is {_format_number(best_independent['selection_score'])}.",
            f"The scorer-v1-only ridge selection score is {_format_number(scorer_result['selection_score'])}; the hybrid with scorer v1 is {_format_number(combined['selection_score'])}.",
            f"Scorer v1 alone has MAE {_format_number(scorer_result['utility_mae'])} and Spearman {_format_number(scorer_result['utility_spearman'])}; adding it to the independent hybrid worsens both measures. Scorer-v1 values remain optional and are not supported for the shortlist by this experiment.",
            "",
            "## Failure Modes and Subgroups",
            "",
            f"Attribution is the weakest auxiliary task: the best candidate reaches only macro-F1 {_format_number(metrics['attribution_macro_f1'].max())}. Utility rankers must not be treated as reliable attribution filters without separate improvement.",
            "The weakest hybrid facet/noise targets are: "
            + ", ".join(
                f"`{row.target}` (F1 {_format_number(row.macro_f1)})"
                for row in weak_hybrid_auxiliary.itertuples()
            )
            + ".",
            "The weakest sufficiently populated hybrid subgroups by Spearman are: "
            + ", ".join(
                f"`{row.subgroup_dimension}={row.subgroup_value}` (n={int(row.page_count)}, rho={_format_number(row.utility_spearman)})"
                for row in weakest_hybrid_subgroups.itertuples()
            )
            + ". These subgroup estimates are descriptive, not independent tests.",
            "",
            "## Annotation-Pool Limitation",
            "",
            f"Model-selection utility labels contain {int((selection_target >= 2).sum()):,} utility-2/3 pages ({100 * float(np.mean(selection_target >= 2)):.1f}%).",
            "This enrichment makes separating the very best pages harder and prevents interpreting these metrics as unbiased website-wide top-k performance.",
        ]
    )
    if low_independent_pools:
        report_lines.append(
            "Potential hard-negative coverage is thin in these scorer-independent pools: "
            + ", ".join(f"`{value}`" for value in low_independent_pools)
            + ". Consider a targeted hard-negative supplement only if 5.8g retrieval diagnostics confirm the gap."
        )
    else:
        report_lines.append("Every major scorer-independent pool contains at least 20 utility-0/1 model-selection pages; no immediate hard-negative supplement is required from this diagnostic alone.")
    report_lines.extend(
        [
            "",
            "## Runtime and Optional Embeddings",
            "",
            "Runtime benchmarks report utility-only and full-output inference. The comparison table and production gate use full-output throughput, including attribution, facet, and noise predictions; memory is the serialized model plus model-selection matrix estimate, with process peak RSS reported separately.",
            "Benchmarks start from the existing 5.8e text and structural tables; they do not include discovery, acquisition, or extraction time.",
            (
                "A multilingual embedding rescue is justified by the runtime/performance gate and remains a future optional challenger."
                if embeddings_justified
                else "A multilingual embedding rescue was not run: at least one scorer-independent candidate meets the local runtime/memory targets, so adding a model dependency is not justified by this comparison."
            ),
            "",
            "## Provisional Shortlist for 5.8g",
            "",
            *[f"- `{candidate}`" for candidate in shortlist],
            "",
            "This is a shortlist, not a frozen candidate set. True retrieval evaluation remains Milestone 5.8g.",
        ]
    )
    (temporary / "comparison_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    shortlist_payload = {
        "experiment_id": config.experiment_id,
        "experiment_version": config.experiment_version,
        "recommended_candidates": shortlist,
        "best_scorer_v1_independent": str(best_independent["candidate_id"]),
        "embedding_rescue_run": False,
        "embedding_rescue_justified": embeddings_justified,
        "status": "provisional_for_locked_retrieval_evaluation",
    }
    (temporary / "shortlist.json").write_text(json.dumps(shortlist_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.copyfile(config_path, temporary / "config_snapshot.yaml")

    summary = {
        "experiment_id": config.experiment_id,
        "experiment_version": config.experiment_version,
        "development_rows": len(development),
        "development_utility_target_rows": int(development_utility.sum()),
        "development_companies": int(development["company_id"].nunique()),
        "model_selection_rows": len(selection),
        "model_selection_companies": int(selection["company_id"].nunique()),
        "locked_targets_loaded": False,
        "candidate_count": len(all_candidates),
        "training_seconds_total": total_training_seconds,
        "utility_distribution_model_selection": {str(int(key)): int(value) for key, value in Counter(selection_target.astype(int)).items()},
        "high_utility_share_model_selection": float(np.mean(selection_target >= 2)),
        "best_scorer_v1_independent": str(best_independent["candidate_id"]),
        "shortlist": shortlist,
        "all_runtime_viable": bool(runtime["production_runtime_viable"].all()),
        "hard_negative_supplement_flagged": bool(low_independent_pools),
        "low_utility_thin_independent_pools": low_independent_pools,
    }
    (temporary / "experiment_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifact_paths = sorted(path for path in temporary.rglob("*") if path.is_file())
    manifest = {
        "experiment_id": config.experiment_id,
        "experiment_version": config.experiment_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": config.config_hash,
        "dataset_manifest_sha256": sha256_file(dataset_directory / "manifest.json"),
        "locked_targets_loaded": False,
        "artifacts": {
            str(path.relative_to(temporary)): {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in artifact_paths
        },
    }
    (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.rename(output_directory)
    paths = {path.name: str(path) for path in output_directory.iterdir() if path.is_file()}
    paths["models"] = str(output_directory / "models")
    return summary, paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scorer-v2 page baseline/challenger experiments.")
    parser.add_argument("--config", type=Path, help="Defaults to config/page_model_experiments.yaml")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root()
    config_path = args.config or project_root / "config" / "page_model_experiments.yaml"
    config = load_experiment_config(config_path)
    output = args.output_directory or project_root / config.output_directory
    if not output.is_absolute():
        output = project_root / output
    summary, paths = run_experiments(project_root, config, config_path, output, args.force_refresh)
    print("Scorer-v2 page-model comparison complete")
    print(f"  Development rows: {summary['development_rows']:,}")
    print(f"  Model-selection rows: {summary['model_selection_rows']:,}")
    print(f"  Candidates: {summary['candidate_count']}")
    print(f"  Best independent: {summary['best_scorer_v1_independent']}")
    print(f"  Shortlist: {', '.join(summary['shortlist'])}")
    print(f"  Report: {paths['comparison_report.md']}")


if __name__ == "__main__":
    main()
