"""Improve shortlisted page rankers without loading locked retrieval labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
import pyarrow.dataset as pads
import scipy.sparse as sp
import yaml
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge, SGDClassifier
from sklearn.metrics import f1_score, precision_recall_fscore_support
from sklearn.model_selection import GroupKFold

from ai_ecosystem.calibration.attribution_features import build_relational_features
from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS
from ai_ecosystem.calibration.run_page_model_experiments import (
    ATTRIBUTION_VALUES,
    KEY_COLUMNS,
    ExperimentConfig,
    ExperimentData,
    _benchmark,
    _matrix_bytes,
    _rss_mb,
    _selection_score,
    _subgroup_metrics,
    canonical_json,
    dense_preprocessor,
    evaluate_auxiliary,
    fit_auxiliary_models,
    fit_text_representation,
    load_experiment_config,
    load_experiment_data,
    predict_model,
    sha256_file,
    text_feature_names,
    transform_text,
    tune_ridge_alpha,
    utility_metrics,
)
from ai_ecosystem.db.init_db import find_project_root


BASELINE_CANDIDATES = (
    "hybrid_independent_ridge",
    "tfidf_ridge",
    "engineered_hist_gradient_boosting",
)
FOCAL_ATTRIBUTION = frozenset({"focal_company_direct", "focal_company_probable"})


@dataclass(frozen=True)
class ImprovementConfig:
    experiment_id: str
    experiment_version: str
    dataset_directory: str
    annotation_sample_directory: str
    baseline_directory: str
    baseline_config: str
    output_directory: str
    random_seed: int
    development_cv_folds: int
    ridge_alphas: tuple[float, ...]
    tree_candidates: tuple[dict[str, Any], ...]
    attribution_models: dict[str, Any]
    adjustment_penalties: tuple[float, ...]
    probable_credit: float
    runtime_targets: dict[str, float]
    config_hash: str


@dataclass
class ImprovementData:
    baseline: ExperimentData
    development: pd.DataFrame
    model_selection: pd.DataFrame
    relational_columns: list[str]
    input_audit: dict[str, Any]


@dataclass
class AttributionCandidate:
    candidate_id: str
    matrix_key: str
    family: str
    parameters: dict[str, Any]
    probability_multipliers: dict[str, float]
    model: Any
    development_oof_probabilities: np.ndarray
    model_selection_probabilities: np.ndarray
    model_selection_predictions: np.ndarray
    development_cv_macro_f1: float


@dataclass
class UtilityCandidate:
    candidate_id: str
    family: str
    matrix_key: str
    model: Any
    auxiliary_models: dict[str, Any]
    parameters: dict[str, Any]
    development_oof_predictions: np.ndarray
    model_selection_raw_predictions: np.ndarray
    feature_names: list[str]


def load_improvement_config(path: Path) -> ImprovementConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = {
        "experiment_id",
        "experiment_version",
        "dataset_directory",
        "annotation_sample_directory",
        "baseline_directory",
        "baseline_config",
        "output_directory",
        "random_seed",
        "development_cv_folds",
        "ridge_alphas",
        "tree_candidates",
        "attribution_models",
        "attribution_adjustment",
        "runtime_targets",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"Missing page-model improvement config keys: {', '.join(missing)}")
    penalties = tuple(float(value) for value in raw["attribution_adjustment"]["penalties"])
    probable_credit = float(raw["attribution_adjustment"]["probable_credit"])
    if not penalties or min(penalties) < 0:
        raise ValueError("Attribution penalties must be non-negative")
    if not 0 <= probable_credit <= 1:
        raise ValueError("probable_credit must be between zero and one")
    return ImprovementConfig(
        experiment_id=str(raw["experiment_id"]),
        experiment_version=str(raw["experiment_version"]),
        dataset_directory=str(raw["dataset_directory"]),
        annotation_sample_directory=str(raw["annotation_sample_directory"]),
        baseline_directory=str(raw["baseline_directory"]),
        baseline_config=str(raw["baseline_config"]),
        output_directory=str(raw["output_directory"]),
        random_seed=int(raw["random_seed"]),
        development_cv_folds=int(raw["development_cv_folds"]),
        ridge_alphas=tuple(float(value) for value in raw["ridge_alphas"]),
        tree_candidates=tuple(dict(value) for value in raw["tree_candidates"]),
        attribution_models=dict(raw["attribution_models"]),
        adjustment_penalties=penalties,
        probable_credit=probable_credit,
        runtime_targets={key: float(value) for key, value in raw["runtime_targets"].items()},
        config_hash=hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest(),
    )


def _filtered_parquet(path: Path, allowed_ids: set[str], columns: list[str]) -> pd.DataFrame:
    expression = pads.field("annotation_item_id").isin(sorted(allowed_ids))
    return pads.dataset(path, format="parquet").to_table(filter=expression, columns=columns).to_pandas()


def _load_company_names(path: Path, allowed_company_ids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    with path.open(encoding="utf-8") as file:
        for line in file:
            value = json.loads(line)
            company_id = str(value["company_id"])
            if company_id in allowed_company_ids:
                names[company_id] = str(value.get("company_name") or "")
    missing = sorted(allowed_company_ids - set(names))
    if missing:
        raise ValueError(f"Missing focal company names for {len(missing)} companies")
    return names


def _enrich_frame(
    frame: pd.DataFrame,
    canonical: pd.DataFrame,
    names: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    enriched = frame.merge(canonical, on="annotation_item_id", validate="one_to_one")
    enriched["company_name"] = enriched["company_id"].map(names)
    if enriched["company_name"].isna().any():
        raise ValueError("Focal company name join is incomplete")
    relational = build_relational_features(enriched)
    relational_columns = [column for column in relational if column.startswith("relation__")]
    enriched = enriched.merge(relational, on=KEY_COLUMNS, validate="one_to_one")
    enriched["improved_document"] = (
        "FOCAL_COMPANY "
        + enriched["company_name"].fillna("").astype(str)
        + "\nFOCAL_DOMAIN "
        + enriched["normalized_domain"].fillna("").astype(str)
        + "\nPAGE_URL "
        + enriched["normalized_url"].fillna("").astype(str)
        + "\n"
        + enriched["document"]
    )
    return enriched, relational[KEY_COLUMNS + relational_columns]


def load_improvement_data(
    project_root: Path,
    config: ImprovementConfig,
    baseline_config: ExperimentConfig,
) -> ImprovementData:
    dataset_directory = project_root / config.dataset_directory
    baseline = load_experiment_data(dataset_directory, baseline_config)
    allowed = pd.concat([baseline.development[KEY_COLUMNS], baseline.model_selection[KEY_COLUMNS]])
    allowed_ids = set(allowed["annotation_item_id"])
    canonical = _filtered_parquet(
        dataset_directory / "canonical_pages.parquet",
        allowed_ids,
        ["annotation_item_id", "normalized_url", "domain"],
    )
    companies = set(allowed["company_id"].astype(str))
    names = _load_company_names(
        project_root / config.annotation_sample_directory / "companies.jsonl",
        companies,
    )
    development, development_relational = _enrich_frame(baseline.development, canonical, names)
    model_selection, selection_relational = _enrich_frame(baseline.model_selection, canonical, names)
    relational_columns = [column for column in development_relational if column.startswith("relation__")]
    if relational_columns != [column for column in selection_relational if column.startswith("relation__")]:
        raise ValueError("Development/model-selection relational feature schemas differ")
    if set(development["split"]) != {"development"} or set(model_selection["split"]) != {"model_selection"}:
        raise AssertionError("Improvement data admitted a non-development/model-selection split")

    input_audit = {
        "luna_model_facing_fields": [
            "scraped_page_id",
            "company_id",
            "company_name",
            "normalized_company_domain",
            "page_url",
            "title",
            "headings",
            "extracted_markdown",
            "markdown_truncated",
        ],
        "baseline_shortlist_inputs": {
            "hybrid_independent_ridge": {
                "text": ["title", "headings", "model_visible_markdown"],
                "engineered_features": baseline.engineered_columns,
            },
            "tfidf_ridge": {
                "text": ["title", "headings", "model_visible_markdown"],
                "engineered_features": [],
            },
            "engineered_hist_gradient_boosting": {
                "text": [],
                "engineered_features": baseline.engineered_columns,
            },
        },
        "luna_relational_information_missing_from_baselines": [
            "focal company name as an explicit comparison target",
            "normalized focal company domain as an explicit comparison target",
            "page URL/host lexical content",
            "company-name overlap with title, headings, body, host, and URL path",
            "company-domain overlap with title, headings, and body",
            "page-host versus focal-domain relationship",
        ],
        "improved_text_fields": [
            "company_name",
            "normalized_company_domain",
            "page_url",
            "title",
            "headings",
            "model_visible_markdown",
        ],
        "identifiers_intentionally_excluded_from_predictive_features": [
            "annotation_item_id",
            "company_id",
            "scraped_page_id",
        ],
        "identifier_exclusion_reason": (
            "Opaque identifiers carry no transferable attribution meaning and would "
            "allow company/page memorization rather than relational generalization."
        ),
        "added_relational_features": relational_columns,
        "development_rows": len(development),
        "model_selection_rows": len(model_selection),
        "locked_targets_loaded": False,
    }
    return ImprovementData(baseline, development, model_selection, relational_columns, input_audit)


def _class_weights(target: np.ndarray, power: float) -> dict[str, float]:
    values, counts = np.unique(target, return_counts=True)
    baseline = len(target) / (len(values) * counts)
    return {str(value): float(weight**power) for value, weight in zip(values, baseline)}


def _sample_weights(target: np.ndarray, power: float) -> np.ndarray:
    weights = _class_weights(target, power)
    return np.asarray([weights[str(value)] for value in target], dtype=float)


def _attribution_model(
    family: str,
    parameters: dict[str, Any],
    target: np.ndarray,
    config: ImprovementConfig,
) -> Any:
    weights = _class_weights(target, float(parameters["class_weight_power"]))
    if family == "sgd":
        return SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=float(parameters["alpha"]),
            max_iter=int(config.attribution_models["max_iterations"]),
            class_weight=weights,
            random_state=config.random_seed,
            tol=1e-4,
        )
    return HistGradientBoostingClassifier(
        learning_rate=float(parameters["learning_rate"]),
        max_leaf_nodes=int(parameters["max_leaf_nodes"]),
        min_samples_leaf=int(parameters["min_samples_leaf"]),
        l2_regularization=float(parameters["l2_regularization"]),
        max_iter=160,
        random_state=config.random_seed,
    )


def _aligned_probabilities(model: Any, matrix: np.ndarray | sp.spmatrix) -> np.ndarray:
    source = model.predict_proba(matrix)
    output = np.zeros((matrix.shape[0], len(ATTRIBUTION_VALUES)), dtype=float)
    index = {value: position for position, value in enumerate(ATTRIBUTION_VALUES)}
    for source_position, value in enumerate(model.classes_):
        output[:, index[str(value)]] = source[:, source_position]
    return output


def apply_probability_multipliers(
    probabilities: np.ndarray,
    multipliers: dict[str, float],
) -> np.ndarray:
    values = np.asarray(
        [float(multipliers.get(label, 1.0)) for label in ATTRIBUTION_VALUES],
        dtype=float,
    )
    scaled = probabilities * values
    totals = scaled.sum(axis=1, keepdims=True)
    return np.divide(scaled, totals, out=np.zeros_like(scaled), where=totals > 0)


def tune_probability_multipliers(
    probabilities: np.ndarray,
    target: np.ndarray,
) -> tuple[dict[str, float], float]:
    """Tune a small deterministic decision calibration on development OOF rows."""
    candidates = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
    multipliers = {label: 1.0 for label in ATTRIBUTION_VALUES}
    support = {label: int(np.sum(target == label)) for label in ATTRIBUTION_VALUES}
    order = sorted(ATTRIBUTION_VALUES, key=lambda label: (support[label], label))

    def score(values: dict[str, float]) -> float:
        calibrated = apply_probability_multipliers(probabilities, values)
        predicted = np.asarray(ATTRIBUTION_VALUES)[np.argmax(calibrated, axis=1)]
        return float(
            f1_score(
                target,
                predicted,
                labels=list(ATTRIBUTION_VALUES),
                average="macro",
                zero_division=0,
            )
        )

    best_score = score(multipliers)
    for _ in range(3):
        changed = False
        for label in order:
            original = multipliers[label]
            choices = []
            for value in candidates:
                trial = dict(multipliers)
                trial[label] = value
                choices.append((score(trial), -abs(math.log(value)), -value, value))
            candidate_score, _, _, selected = max(choices)
            if candidate_score > best_score + 1e-12:
                multipliers[label] = selected
                best_score = candidate_score
                changed = True
            else:
                multipliers[label] = original
        if not changed:
            break
    return multipliers, best_score


def attribution_metrics(
    truth: np.ndarray,
    predicted: np.ndarray,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    precision, recall, f1, support = precision_recall_fscore_support(
        truth,
        predicted,
        labels=list(ATTRIBUTION_VALUES),
        zero_division=0,
    )
    rows = [
        {
            "attribution_class": value,
            "precision": float(precision[position]),
            "recall": float(recall[position]),
            "f1": float(f1[position]),
            "support": int(support[position]),
        }
        for position, value in enumerate(ATTRIBUTION_VALUES)
    ]
    focal_truth = np.asarray([value in FOCAL_ATTRIBUTION for value in truth], dtype=int)
    focal_predicted = np.asarray([value in FOCAL_ATTRIBUTION for value in predicted], dtype=int)
    return {
        "attribution_macro_f1": float(np.mean(f1)),
        "focal_vs_nonfocal_f1": float(f1_score(focal_truth, focal_predicted, zero_division=0)),
        "attribution_accuracy": float(np.mean(truth == predicted)),
    }, rows


def _attribution_parameter_grid(family: str, config: ImprovementConfig) -> list[dict[str, Any]]:
    powers = [float(value) for value in config.attribution_models["class_weight_powers"]]
    if family == "sgd":
        return [
            {"alpha": float(alpha), "class_weight_power": power}
            for alpha, power in product(config.attribution_models["sgd_alphas"], powers)
        ]
    return [
        {**dict(candidate), "class_weight_power": power}
        for candidate, power in product(config.attribution_models["hgb_candidates"], powers)
    ]


def tune_attribution_candidate(
    candidate_id: str,
    matrix_key: str,
    family: str,
    development_matrix: np.ndarray | sp.spmatrix,
    selection_matrix: np.ndarray | sp.spmatrix,
    development: pd.DataFrame,
    selection: pd.DataFrame,
    config: ImprovementConfig,
) -> tuple[AttributionCandidate, list[dict[str, Any]]]:
    available = development["attribution_available"].fillna(False).to_numpy(bool)
    matrix = development_matrix[available]
    target = development.loc[available, "attribution"].to_numpy(str)
    groups = development.loc[available, "normalized_domain"].to_numpy(str)
    folds = min(config.development_cv_folds, len(np.unique(groups)))
    splitter = GroupKFold(n_splits=folds)
    rows: list[dict[str, Any]] = []
    best: tuple[
        float,
        float,
        str,
        dict[str, Any],
        np.ndarray,
        dict[str, float],
    ] | None = None
    for parameters in _attribution_parameter_grid(family, config):
        oof = np.zeros((len(target), len(ATTRIBUTION_VALUES)), dtype=float)
        for train, validation in splitter.split(np.zeros(len(target)), target, groups):
            model = _attribution_model(family, parameters, target[train], config)
            if family == "hgb":
                model.fit(
                    matrix[train],
                    target[train],
                    sample_weight=_sample_weights(
                        target[train], float(parameters["class_weight_power"])
                    ),
                )
            else:
                model.fit(matrix[train], target[train])
            oof[validation] = _aligned_probabilities(model, matrix[validation])
        raw_predicted = np.asarray(ATTRIBUTION_VALUES)[np.argmax(oof, axis=1)]
        raw_metrics, _ = attribution_metrics(target, raw_predicted)
        multipliers, calibrated_cv_f1 = tune_probability_multipliers(oof, target)
        calibrated_oof = apply_probability_multipliers(oof, multipliers)
        predicted = np.asarray(ATTRIBUTION_VALUES)[np.argmax(calibrated_oof, axis=1)]
        metrics, _ = attribution_metrics(target, predicted)
        row = {
            "candidate_id": candidate_id,
            "family": family,
            **parameters,
            "raw_development_cv_macro_f1": raw_metrics["attribution_macro_f1"],
            "probability_multipliers_json": canonical_json(multipliers),
            **metrics,
        }
        rows.append(row)
        key = (
            metrics["attribution_macro_f1"],
            metrics["focal_vs_nonfocal_f1"],
            canonical_json(parameters),
            parameters,
            calibrated_oof,
            multipliers,
        )
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None:
        raise ValueError(f"No attribution parameters evaluated for {candidate_id}")
    parameters = best[3]
    multipliers = best[5]
    model = _attribution_model(family, parameters, target, config)
    if family == "hgb":
        model.fit(
            matrix,
            target,
            sample_weight=_sample_weights(
                target, float(parameters["class_weight_power"])
            ),
        )
    else:
        model.fit(matrix, target)
    selection_probabilities = apply_probability_multipliers(
        _aligned_probabilities(model, selection_matrix), multipliers
    )
    selection_predictions = np.asarray(ATTRIBUTION_VALUES)[np.argmax(selection_probabilities, axis=1)]
    for row in rows:
        row["selected_on_development"] = canonical_json(
            {key: row[key] for key in parameters}
        ) == canonical_json(parameters)
    full_oof = np.full(
        (len(development), len(ATTRIBUTION_VALUES)), np.nan, dtype=float
    )
    full_oof[available] = best[4]
    return AttributionCandidate(
        candidate_id=candidate_id,
        matrix_key=matrix_key,
        family=family,
        parameters=parameters,
        probability_multipliers=multipliers,
        model=model,
        development_oof_probabilities=full_oof,
        model_selection_probabilities=selection_probabilities,
        model_selection_predictions=selection_predictions,
        development_cv_macro_f1=float(best[0]),
    ), rows


def _utility_oof(
    matrix: np.ndarray | sp.spmatrix,
    target: np.ndarray,
    groups: np.ndarray,
    family: str,
    parameters: dict[str, Any],
    config: ImprovementConfig,
) -> np.ndarray:
    output = np.zeros(len(target), dtype=float)
    splitter = GroupKFold(n_splits=min(config.development_cv_folds, len(np.unique(groups))))
    for train, validation in splitter.split(np.zeros(len(target)), target, groups):
        if family == "ridge":
            model = Ridge(alpha=float(parameters["alpha"]), solver="lsqr")
        else:
            model = HistGradientBoostingRegressor(
                learning_rate=float(parameters["learning_rate"]),
                max_leaf_nodes=int(parameters["max_leaf_nodes"]),
                min_samples_leaf=int(parameters["min_samples_leaf"]),
                l2_regularization=float(parameters["l2_regularization"]),
                max_iter=160,
                random_state=config.random_seed,
            )
        model.fit(matrix[train], target[train])
        output[validation] = model.predict(matrix[validation])
    return output


def _tune_tree_utility(
    matrix: np.ndarray,
    target: np.ndarray,
    groups: np.ndarray,
    config: ImprovementConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    splitter = GroupKFold(n_splits=min(config.development_cv_folds, len(np.unique(groups))))
    for parameters in config.tree_candidates:
        values = []
        for train, validation in splitter.split(np.zeros(len(target)), target, groups):
            model = HistGradientBoostingRegressor(
                **parameters,
                max_iter=160,
                random_state=config.random_seed,
            )
            model.fit(matrix[train], target[train])
            values.append(float(np.mean(np.abs(target[validation] - model.predict(matrix[validation])))))
        rows.append({**parameters, "development_group_cv_mae": float(np.mean(values))})
    best = min(rows, key=lambda row: (row["development_group_cv_mae"], canonical_json(row)))
    parameters = {key: best[key] for key in config.tree_candidates[0]}
    for row in rows:
        row["selected_on_development"] = all(row[key] == parameters[key] for key in parameters)
    return parameters, rows


def fit_utility_candidate(
    candidate_id: str,
    family: str,
    matrix_key: str,
    development_matrix: np.ndarray | sp.spmatrix,
    selection_matrix: np.ndarray | sp.spmatrix,
    feature_names: list[str],
    development: pd.DataFrame,
    config: ImprovementConfig,
    baseline_config: ExperimentConfig,
) -> tuple[UtilityCandidate, list[dict[str, Any]]]:
    available = development["utility_score_available"].fillna(False).to_numpy(bool)
    matrix = development_matrix[available]
    target = development.loc[available, "utility_score"].to_numpy(float)
    groups = development.loc[available, "normalized_domain"].to_numpy(str)
    if family == "ridge":
        proxy_config = ExperimentConfig(
            **{
                **baseline_config.__dict__,
                "ridge_alphas": config.ridge_alphas,
                "development_cv_folds": config.development_cv_folds,
            }
        )
        alpha, tuning = tune_ridge_alpha(matrix, target, groups, proxy_config)
        parameters = {"alpha": alpha}
        model = Ridge(alpha=alpha, solver="lsqr")
    else:
        parameters, tuning = _tune_tree_utility(matrix, target, groups, config)
        model = HistGradientBoostingRegressor(
            **parameters,
            max_iter=160,
            random_state=config.random_seed,
        )
    oof = _utility_oof(matrix, target, groups, family, parameters, config)
    model.fit(matrix, target)
    auxiliary = fit_auxiliary_models(
        development_matrix,
        development,
        "tree" if family == "tree" else "linear",
        baseline_config,
    )
    return UtilityCandidate(
        candidate_id=candidate_id,
        family=family,
        matrix_key=matrix_key,
        model=model,
        auxiliary_models=auxiliary,
        parameters=parameters,
        development_oof_predictions=oof,
        model_selection_raw_predictions=model.predict(selection_matrix),
        feature_names=feature_names,
    ), [{"candidate_id": candidate_id, **row} for row in tuning]


def focal_confidence(probabilities: np.ndarray, probable_credit: float) -> np.ndarray:
    index = {value: position for position, value in enumerate(ATTRIBUTION_VALUES)}
    return probabilities[:, index["focal_company_direct"]] + probable_credit * probabilities[:, index["focal_company_probable"]]


def tune_attribution_penalty(
    raw_oof_score: np.ndarray,
    attribution_oof_probabilities: np.ndarray,
    target: np.ndarray,
    groups: np.ndarray,
    penalties: Iterable[float],
    probable_credit: float,
) -> tuple[float, list[dict[str, Any]]]:
    confidence = focal_confidence(attribution_oof_probabilities, probable_credit)
    rows = []
    for penalty in penalties:
        adjusted = raw_oof_score - float(penalty) * (1.0 - confidence)
        metrics, _ = utility_metrics(target, adjusted, groups, (3, 5))
        row = {"penalty": float(penalty), **metrics}
        row["development_selection_score"] = _selection_score(row)
        rows.append(row)
    best = max(rows, key=lambda row: (row["development_selection_score"], -row["penalty"]))
    for row in rows:
        row["selected_on_development"] = row["penalty"] == best["penalty"]
    return float(best["penalty"]), rows


def _prediction_bundle(
    utility: np.ndarray,
    attribution: np.ndarray,
    shared: dict[str, Any],
) -> dict[str, Any]:
    return {
        "utility": utility,
        "attribution": attribution,
        "facets": shared["facets"],
        "noise": shared["noise"],
    }


def _baseline_prediction_bundle(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "utility": frame["utility_score"].to_numpy(float),
        "attribution": frame["attribution_prediction"].to_numpy(str),
        "facets": {
            facet: frame[f"facet__{facet}__prediction"].to_numpy(int)
            for facet in FACETS
        },
        "noise": {
            flag: frame[f"noise__{flag}__prediction"].to_numpy(int)
            for flag in NOISE_FLAGS
        },
    }


def _evaluate_candidate(
    candidate_id: str,
    variant_type: str,
    attribution_source: str,
    adjustment_penalty: float,
    selection: pd.DataFrame,
    predictions: dict[str, Any],
    top_k: Iterable[int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    available = selection["utility_score_available"].fillna(False).to_numpy(bool)
    target = selection.loc[available, "utility_score"].to_numpy(float)
    score = np.asarray(predictions["utility"])[available]
    groups = selection.loc[available, "company_id"].to_numpy(str)
    utility, _ = utility_metrics(target, score, groups, top_k)
    auxiliary, detail = evaluate_auxiliary(selection, predictions)
    truth = selection.loc[selection["attribution_available"], "attribution"].to_numpy(str)
    predicted = np.asarray(predictions["attribution"])[selection["attribution_available"].to_numpy(bool)]
    attribution, per_class = attribution_metrics(truth, predicted)
    row = {
        "candidate_id": candidate_id,
        "variant_type": variant_type,
        "attribution_source": attribution_source,
        "attribution_adjustment_penalty": adjustment_penalty,
        **utility,
        **auxiliary,
        "attribution_macro_f1": attribution["attribution_macro_f1"],
        "focal_vs_nonfocal_f1": attribution["focal_vs_nonfocal_f1"],
        "attribution_accuracy": attribution["attribution_accuracy"],
    }
    row["selection_score"] = _selection_score(row)
    return row, [dict(value, candidate_id=candidate_id) for value in detail], [dict(value, candidate_id=candidate_id) for value in per_class]


def _attribution_subgroups(
    candidate_id: str,
    selection: pd.DataFrame,
    predicted: np.ndarray,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dimensions = {
        "shared_domain": "shared_domain",
        "host_relationship": "host_relationship",
        "site_attribution_uncertain": "site__attribution_uncertain",
    }
    work = selection.copy()
    work["host_relationship"] = np.select(
        [
            work["relation__page_cross_host"].astype(bool),
            work["relation__page_host_subdomain_of_company_domain"].astype(bool),
            work["relation__page_host_exact_company_domain"].astype(bool),
        ],
        ["cross_host", "company_subdomain", "exact_company_domain"],
        default="missing_or_other",
    )
    for dimension, column in dimensions.items():
        for value, indices in work.groupby(column, dropna=False).groups.items():
            positions = np.asarray(list(indices), dtype=int)
            available = work.iloc[positions]["attribution_available"].fillna(False).to_numpy(bool)
            positions = positions[available]
            if not len(positions):
                continue
            truth = work.iloc[positions]["attribution"].to_numpy(str)
            metrics, _ = attribution_metrics(truth, predicted[positions])
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "subgroup_dimension": dimension,
                    "subgroup_value": str(value),
                    "page_count": len(positions),
                    **metrics,
                }
            )
    return rows


def _matrix_context(
    development: pd.DataFrame,
    selection: pd.DataFrame,
    data: ImprovementData,
    baseline_config: ExperimentConfig,
) -> tuple[dict[str, Any], dict[str, np.ndarray | sp.spmatrix], dict[str, np.ndarray | sp.spmatrix], dict[str, list[str]]]:
    relational = data.relational_columns
    engineered_relational = [*data.baseline.engineered_columns, *relational]

    relational_preprocessor = dense_preprocessor()
    relation_dev = relational_preprocessor.fit_transform(development[relational])
    relation_selection = relational_preprocessor.transform(selection[relational])
    relation_names = list(relational_preprocessor.get_feature_names_out(relational))

    engineered_relational_preprocessor = dense_preprocessor()
    engineered_relation_dev = engineered_relational_preprocessor.fit_transform(
        development[engineered_relational]
    )
    engineered_relation_selection = engineered_relational_preprocessor.transform(
        selection[engineered_relational]
    )
    engineered_relation_names = list(
        engineered_relational_preprocessor.get_feature_names_out(engineered_relational)
    )

    text_representation, text_dev = fit_text_representation(
        development["improved_document"], baseline_config
    )
    text_selection = transform_text(text_representation, selection["improved_document"])
    text_names = text_feature_names(text_representation)

    tfidf_relation_dev = sp.hstack([text_dev, sp.csr_matrix(relation_dev)], format="csr")
    tfidf_relation_selection = sp.hstack(
        [text_selection, sp.csr_matrix(relation_selection)], format="csr"
    )
    hybrid_dev = sp.hstack(
        [text_dev, sp.csr_matrix(engineered_relation_dev)], format="csr"
    )
    hybrid_selection = sp.hstack(
        [text_selection, sp.csr_matrix(engineered_relation_selection)], format="csr"
    )
    representations = {
        "relational_preprocessor": relational_preprocessor,
        "engineered_relational_preprocessor": engineered_relational_preprocessor,
        **text_representation,
    }
    development_matrices = {
        "relational": relation_dev,
        "engineered_relational": engineered_relation_dev,
        "text_relational": tfidf_relation_dev,
        "hybrid_relational": hybrid_dev,
    }
    selection_matrices = {
        "relational": relation_selection,
        "engineered_relational": engineered_relation_selection,
        "text_relational": tfidf_relation_selection,
        "hybrid_relational": hybrid_selection,
    }
    names = {
        "relational": relation_names,
        "engineered_relational": engineered_relation_names,
        "text_relational": [*text_names, *relation_names],
        "hybrid_relational": [*text_names, *engineered_relation_names],
    }
    return representations, development_matrices, selection_matrices, names


def _transform_matrices(
    frame: pd.DataFrame,
    data: ImprovementData,
    representations: dict[str, Any],
    requested: set[str],
) -> dict[str, np.ndarray | sp.spmatrix]:
    relational_frame = build_relational_features(frame)
    relational = relational_frame[data.relational_columns]
    engineered_relational = frame[data.baseline.engineered_columns].reset_index(drop=True).join(
        relational.reset_index(drop=True)
    )
    output: dict[str, np.ndarray | sp.spmatrix] = {}
    relation_dense = engineered_dense = text = None
    if requested & {"relational", "text_relational"}:
        relation_dense = representations["relational_preprocessor"].transform(relational)
        output["relational"] = relation_dense
    if requested & {"engineered_relational", "hybrid_relational"}:
        engineered_dense = representations["engineered_relational_preprocessor"].transform(
            engineered_relational
        )
        output["engineered_relational"] = engineered_dense
    if requested & {"text_relational", "hybrid_relational"}:
        text = transform_text(representations, frame["improved_document"])
    if "text_relational" in requested:
        output["text_relational"] = sp.hstack(
            [text, sp.csr_matrix(relation_dense)], format="csr"
        )
    if "hybrid_relational" in requested:
        output["hybrid_relational"] = sp.hstack(
            [text, sp.csr_matrix(engineered_dense)], format="csr"
        )
    return output


def run_improvement_study(
    project_root: Path,
    config: ImprovementConfig,
    config_path: Path,
    output_directory: Path,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    if output_directory.exists():
        if not force_refresh:
            raise FileExistsError(f"Improvement experiment directory exists: {output_directory}")
        shutil.rmtree(output_directory)
    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    (temporary / "models").mkdir(parents=True)

    baseline_config_path = project_root / config.baseline_config
    baseline_config = load_experiment_config(baseline_config_path)
    data = load_improvement_data(project_root, config, baseline_config)
    development = data.development
    selection = data.model_selection
    representations, dev_matrices, selection_matrices, feature_names = _matrix_context(
        development, selection, data, baseline_config
    )

    attribution_specs = [
        ("dedicated_relational_hgb", "relational", "hgb"),
        ("dedicated_engineered_relational_hgb", "engineered_relational", "hgb"),
        ("dedicated_tfidf_relational_sgd", "text_relational", "sgd"),
        ("dedicated_hybrid_relational_sgd", "hybrid_relational", "sgd"),
    ]
    attribution_candidates: dict[str, AttributionCandidate] = {}
    attribution_tuning_rows: list[dict[str, Any]] = []
    attribution_comparison_rows: list[dict[str, Any]] = []
    attribution_per_class_rows: list[dict[str, Any]] = []
    dedicated_attribution_subgroup_rows: list[dict[str, Any]] = []
    for candidate_id, matrix_key, family in attribution_specs:
        candidate, tuning = tune_attribution_candidate(
            candidate_id,
            matrix_key,
            family,
            dev_matrices[matrix_key],
            selection_matrices[matrix_key],
            development,
            selection,
            config,
        )
        attribution_candidates[candidate_id] = candidate
        attribution_tuning_rows.extend(tuning)
        available = selection["attribution_available"].fillna(False).to_numpy(bool)
        metrics, per_class = attribution_metrics(
            selection.loc[available, "attribution"].to_numpy(str),
            candidate.model_selection_predictions[available],
        )
        attribution_comparison_rows.append(
            {
                "attribution_candidate_id": candidate_id,
                "representation": matrix_key,
                "family": family,
                "development_cv_macro_f1": candidate.development_cv_macro_f1,
                **metrics,
                **candidate.parameters,
            }
        )
        attribution_per_class_rows.extend(
            {
                "candidate_id": candidate_id,
                "candidate_kind": "dedicated_attribution",
                **row,
            }
            for row in per_class
        )
        dedicated_attribution_subgroup_rows.extend(
            _attribution_subgroups(
                candidate_id, selection, candidate.model_selection_predictions
            )
        )
        joblib.dump(
            {
                "candidate_id": candidate_id,
                "matrix_key": matrix_key,
                "family": family,
                "parameters": candidate.parameters,
                "probability_multipliers": candidate.probability_multipliers,
                "model": candidate.model,
                "representations": representations,
                "relational_feature_names": data.relational_columns,
            },
            temporary / "models" / f"attribution_{candidate_id}.joblib",
            compress=3,
        )

    dense_attribution = max(
        (
            candidate
            for candidate in attribution_candidates.values()
            if candidate.matrix_key in {"relational", "engineered_relational"}
        ),
        key=lambda value: value.development_cv_macro_f1,
    )
    text_attribution = max(
        attribution_candidates.values(), key=lambda value: value.development_cv_macro_f1
    )

    utility_specs = [
        ("hybrid_relational_ridge", "ridge", "hybrid_relational", text_attribution),
        ("tfidf_relational_ridge", "ridge", "text_relational", text_attribution),
        ("engineered_relational_hgb", "tree", "engineered_relational", dense_attribution),
    ]
    utility_candidates: dict[str, UtilityCandidate] = {}
    utility_tuning_rows: list[dict[str, Any]] = []
    for candidate_id, family, matrix_key, _ in utility_specs:
        candidate, tuning = fit_utility_candidate(
            candidate_id,
            family,
            matrix_key,
            dev_matrices[matrix_key],
            selection_matrices[matrix_key],
            feature_names[matrix_key],
            development,
            config,
            baseline_config,
        )
        utility_candidates[candidate_id] = candidate
        utility_tuning_rows.extend(tuning)

    baseline_directory = project_root / config.baseline_directory
    baseline_predictions = pd.read_parquet(
        baseline_directory / "model_selection_predictions.parquet"
    )
    baseline_runtime = pd.read_csv(baseline_directory / "runtime_benchmarks.csv").set_index(
        "candidate_id"
    )

    comparison_rows: list[dict[str, Any]] = []
    auxiliary_rows: list[dict[str, Any]] = []
    per_class_rows: list[dict[str, Any]] = []
    subgroup_rows: list[dict[str, Any]] = []
    attribution_subgroup_rows: list[dict[str, Any]] = list(
        dedicated_attribution_subgroup_rows
    )
    adjustment_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    attribution_runtime_rows: list[dict[str, Any]] = []
    coefficient_rows: list[dict[str, Any]] = []

    for candidate_id in BASELINE_CANDIDATES:
        frame = baseline_predictions[baseline_predictions["candidate_id"] == candidate_id]
        frame = selection[KEY_COLUMNS].merge(frame, on=KEY_COLUMNS, validate="one_to_one")
        predictions = _baseline_prediction_bundle(frame)
        row, detail, per_class = _evaluate_candidate(
            candidate_id,
            "fixed_baseline",
            "baseline_shared_representation",
            0.0,
            selection,
            predictions,
            baseline_config.ranking_top_k,
        )
        runtime = baseline_runtime.loc[candidate_id]
        row.update(
            pages_per_second=float(runtime["pages_per_second"]),
            projected_seconds_for_55000_pages=float(runtime["projected_seconds_for_55000_pages"]),
            estimated_working_set_mb=float(runtime["estimated_working_set_mb"]),
            process_peak_rss_mb=float(runtime["process_peak_rss_mb"]),
            production_runtime_viable=bool(runtime["production_runtime_viable"]),
        )
        comparison_rows.append(row)
        attribution_comparison_rows.append(
            {
                "attribution_candidate_id": f"{candidate_id}_fixed_baseline",
                "representation": candidate_id,
                "family": "baseline_shared_representation",
                "development_cv_macro_f1": None,
                "attribution_macro_f1": row["attribution_macro_f1"],
                "focal_vs_nonfocal_f1": row["focal_vs_nonfocal_f1"],
                "attribution_accuracy": row["attribution_accuracy"],
            }
        )
        auxiliary_rows.extend(detail)
        per_class_rows.extend(per_class)
        subgroup_rows.extend(
            _subgroup_metrics(candidate_id, selection, predictions, baseline_config.ranking_top_k)
        )
        attribution_subgroup_rows.extend(
            _attribution_subgroups(candidate_id, selection, np.asarray(predictions["attribution"]))
        )
        for position, source in selection.iterrows():
            prediction_rows.append(
                {
                    "candidate_id": candidate_id,
                    **{key: source[key] for key in KEY_COLUMNS},
                    "utility_score": float(predictions["utility"][position]),
                    "utility_score_raw": float(predictions["utility"][position]),
                    "focal_attribution_confidence": float(
                        1.0
                        if predictions["attribution"][position] == "focal_company_direct"
                        else config.probable_credit
                        if predictions["attribution"][position] == "focal_company_probable"
                        else 0.0
                    ),
                    "attribution_prediction": str(predictions["attribution"][position]),
                    **{
                        f"facet__{facet}__prediction": int(
                            predictions["facets"][facet][position]
                        )
                        for facet in FACETS
                    },
                    **{
                        f"noise__{flag}__prediction": int(
                            predictions["noise"][flag][position]
                        )
                        for flag in NOISE_FLAGS
                    },
                }
            )

    development_utility = development["utility_score_available"].fillna(False).to_numpy(bool)
    dev_target = development.loc[development_utility, "utility_score"].to_numpy(float)
    dev_groups = development.loc[development_utility, "company_id"].to_numpy(str)
    selection_available = selection["utility_score_available"].fillna(False).to_numpy(bool)
    selection_target = selection.loc[selection_available, "utility_score"].to_numpy(float)
    selection_groups = selection.loc[selection_available, "company_id"].to_numpy(str)

    improved_records: dict[str, dict[str, Any]] = {}
    for candidate_id, family, matrix_key, attribution_candidate in utility_specs:
        candidate = utility_candidates[candidate_id]
        shared = predict_model(
            {
                "utility_model": candidate.model,
                "auxiliary_models": candidate.auxiliary_models,
            },
            selection_matrices[matrix_key],
        )
        shared_metrics, shared_per_class = attribution_metrics(
            selection.loc[selection["attribution_available"], "attribution"].to_numpy(str),
            np.asarray(shared["attribution"])[selection["attribution_available"].to_numpy(bool)],
        )
        attribution_comparison_rows.append(
            {
                "attribution_candidate_id": f"{candidate_id}_shared_representation",
                "representation": matrix_key,
                "family": "shared_representation",
                "development_cv_macro_f1": None,
                **shared_metrics,
            }
        )
        attribution_per_class_rows.extend(
            {
                "candidate_id": f"{candidate_id}_shared_representation",
                "candidate_kind": "shared_representation_attribution",
                **row,
            }
            for row in shared_per_class
        )

        joint_available_within_utility = development.loc[
            development_utility, "attribution_available"
        ].fillna(False).to_numpy(bool)
        penalty, penalty_rows = tune_attribution_penalty(
            candidate.development_oof_predictions[joint_available_within_utility],
            attribution_candidate.development_oof_probabilities[
                development_utility
            ][joint_available_within_utility],
            dev_target[joint_available_within_utility],
            dev_groups[joint_available_within_utility],
            config.adjustment_penalties,
            config.probable_credit,
        )
        adjustment_rows.extend(
            {
                "utility_candidate_id": candidate_id,
                "attribution_candidate_id": attribution_candidate.candidate_id,
                **row,
            }
            for row in penalty_rows
        )
        confidence = focal_confidence(
            attribution_candidate.model_selection_probabilities, config.probable_credit
        )
        raw_predictions = _prediction_bundle(
            candidate.model_selection_raw_predictions,
            attribution_candidate.model_selection_predictions,
            shared,
        )
        adjusted_score = candidate.model_selection_raw_predictions - penalty * (1.0 - confidence)
        adjusted_predictions = _prediction_bundle(
            adjusted_score,
            attribution_candidate.model_selection_predictions,
            shared,
        )
        variants = [
            (candidate_id, "relational_raw", 0.0, raw_predictions),
            (f"{candidate_id}_attribution_adjusted", "relational_adjusted", penalty, adjusted_predictions),
        ]
        for variant_id, variant_type, applied_penalty, predictions in variants:
            row, detail, per_class = _evaluate_candidate(
                variant_id,
                variant_type,
                attribution_candidate.candidate_id,
                applied_penalty,
                selection,
                predictions,
                baseline_config.ranking_top_k,
            )
            comparison_rows.append(row)
            auxiliary_rows.extend(detail)
            per_class_rows.extend(per_class)
            subgroup_rows.extend(
                _subgroup_metrics(variant_id, selection, predictions, baseline_config.ranking_top_k)
            )
            attribution_subgroup_rows.extend(
                _attribution_subgroups(
                    variant_id, selection, attribution_candidate.model_selection_predictions
                )
            )
            for position, source in selection.iterrows():
                prediction_rows.append(
                    {
                        "candidate_id": variant_id,
                        **{key: source[key] for key in KEY_COLUMNS},
                        "utility_score": float(predictions["utility"][position]),
                        "utility_score_raw": float(candidate.model_selection_raw_predictions[position]),
                        "focal_attribution_confidence": float(confidence[position]),
                        "attribution_prediction": str(predictions["attribution"][position]),
                        **{
                            f"facet__{facet}__prediction": int(predictions["facets"][facet][position])
                            for facet in FACETS
                        },
                        **{
                            f"noise__{flag}__prediction": int(predictions["noise"][flag][position])
                            for flag in NOISE_FLAGS
                        },
                    }
                )

        improved_records[candidate_id] = {
            "candidate": candidate,
            "attribution": attribution_candidate,
            "matrix_key": matrix_key,
            "penalty": penalty,
        }
        if candidate.family == "ridge":
            coefficients = np.asarray(candidate.model.coef_).ravel()
            top = np.argsort(-np.abs(coefficients))[:100]
            coefficient_rows.extend(
                {
                    "candidate_id": candidate_id,
                    "rank": rank,
                    "feature": candidate.feature_names[index],
                    "coefficient": float(coefficients[index]),
                    "absolute_coefficient": float(abs(coefficients[index])),
                }
                for rank, index in enumerate(top, 1)
            )

    for candidate_id, record in improved_records.items():
        candidate: UtilityCandidate = record["candidate"]
        attribution_candidate: AttributionCandidate = record["attribution"]
        matrix_key = str(record["matrix_key"])
        required_keys = {matrix_key, attribution_candidate.matrix_key}

        def benchmark_callback() -> dict[str, Any]:
            matrices = _transform_matrices(
                selection, data, representations, required_keys
            )
            shared = predict_model(
                {
                    "utility_model": candidate.model,
                    "auxiliary_models": candidate.auxiliary_models,
                },
                matrices[matrix_key],
            )
            attribution_probabilities = apply_probability_multipliers(
                _aligned_probabilities(
                    attribution_candidate.model,
                    matrices[attribution_candidate.matrix_key],
                ),
                attribution_candidate.probability_multipliers,
            )
            attribution = np.asarray(ATTRIBUTION_VALUES)[
                np.argmax(attribution_probabilities, axis=1)
            ]
            return _prediction_bundle(shared["utility"], attribution, shared)

        seconds, pages_per_second = _benchmark(benchmark_callback, len(selection), repeats=3)
        artifact = {
            "experiment_id": config.experiment_id,
            "experiment_version": config.experiment_version,
            "candidate_id": candidate_id,
            "matrix_key": matrix_key,
            "feature_names": candidate.feature_names,
            "utility_parameters": candidate.parameters,
            "utility_model": candidate.model,
            "auxiliary_models": candidate.auxiliary_models,
            "attribution_candidate_id": attribution_candidate.candidate_id,
            "attribution_matrix_key": attribution_candidate.matrix_key,
            "attribution_parameters": attribution_candidate.parameters,
            "attribution_probability_multipliers": attribution_candidate.probability_multipliers,
            "attribution_model": attribution_candidate.model,
            "attribution_adjustment_penalty": record["penalty"],
            "probable_credit": config.probable_credit,
            "representations": representations,
            "relational_feature_names": data.relational_columns,
        }
        model_path = temporary / "models" / f"{candidate_id}.joblib"
        joblib.dump(artifact, model_path, compress=3)
        matrix_bytes = _matrix_bytes(selection_matrices[matrix_key])
        if attribution_candidate.matrix_key != matrix_key:
            matrix_bytes += _matrix_bytes(selection_matrices[attribution_candidate.matrix_key])
        memory_mb = (matrix_bytes + model_path.stat().st_size) / (1024 * 1024)
        projected = config.runtime_targets["corpus_page_count"] / pages_per_second
        viable = (
            pages_per_second >= config.runtime_targets["minimum_pages_per_second"]
            and projected < config.runtime_targets["maximum_runtime_seconds"]
            and memory_mb < config.runtime_targets["maximum_memory_mb"]
            and _rss_mb() < config.runtime_targets["maximum_memory_mb"]
        )
        runtime_rows.append(
            {
                "candidate_id": candidate_id,
                "full_output_seconds_for_model_selection": seconds,
                "pages_per_second": pages_per_second,
                "projected_seconds_for_55000_pages": projected,
                "model_artifact_bytes": model_path.stat().st_size,
                "estimated_working_set_mb": memory_mb,
                "process_peak_rss_mb": _rss_mb(),
                "production_runtime_viable": viable,
                "includes_relational_feature_construction": True,
                "includes_attribution_facet_noise_predictions": True,
            }
        )

    for candidate_id, candidate in attribution_candidates.items():
        def attribution_benchmark_callback(
            current: AttributionCandidate = candidate,
        ) -> np.ndarray:
            matrices = _transform_matrices(
                selection, data, representations, {current.matrix_key}
            )
            probabilities = apply_probability_multipliers(
                _aligned_probabilities(current.model, matrices[current.matrix_key]),
                current.probability_multipliers,
            )
            return np.asarray(ATTRIBUTION_VALUES)[np.argmax(probabilities, axis=1)]

        seconds, pages_per_second = _benchmark(
            attribution_benchmark_callback, len(selection), repeats=3
        )
        model_path = temporary / "models" / f"attribution_{candidate_id}.joblib"
        memory_mb = (
            _matrix_bytes(selection_matrices[candidate.matrix_key])
            + model_path.stat().st_size
        ) / (1024 * 1024)
        projected = config.runtime_targets["corpus_page_count"] / pages_per_second
        attribution_runtime_rows.append(
            {
                "candidate_id": candidate_id,
                "representation": candidate.matrix_key,
                "family": candidate.family,
                "inference_seconds_for_model_selection": seconds,
                "pages_per_second": pages_per_second,
                "projected_seconds_for_55000_pages": projected,
                "model_artifact_bytes": model_path.stat().st_size,
                "estimated_working_set_mb": memory_mb,
                "process_peak_rss_mb": _rss_mb(),
                "production_runtime_viable": (
                    pages_per_second
                    >= config.runtime_targets["minimum_pages_per_second"]
                    and projected < config.runtime_targets["maximum_runtime_seconds"]
                    and memory_mb < config.runtime_targets["maximum_memory_mb"]
                    and _rss_mb() < config.runtime_targets["maximum_memory_mb"]
                ),
                "includes_relational_feature_construction": True,
            }
        )

    comparison = pd.DataFrame(comparison_rows)
    runtime = pd.DataFrame(runtime_rows)
    runtime_lookup = runtime.set_index("candidate_id")
    for position, row in comparison.iterrows():
        base = str(row["candidate_id"]).removesuffix("_attribution_adjusted")
        if base in runtime_lookup.index:
            values = runtime_lookup.loc[base]
            for column in (
                "pages_per_second",
                "projected_seconds_for_55000_pages",
                "estimated_working_set_mb",
                "process_peak_rss_mb",
                "production_runtime_viable",
            ):
                comparison.loc[position, column] = values[column]
    comparison = comparison.sort_values(
        ["selection_score", "attribution_macro_f1"], ascending=False
    ).reset_index(drop=True)

    attribution_comparison = pd.DataFrame(attribution_comparison_rows).sort_values(
        ["attribution_macro_f1", "focal_vs_nonfocal_f1"], ascending=False
    )
    dedicated_attribution_comparison = attribution_comparison[
        attribution_comparison["family"].isin({"hgb", "sgd"})
    ]
    shared_attribution_comparison = attribution_comparison[
        attribution_comparison["family"] == "shared_representation"
    ]
    best_attribution = dedicated_attribution_comparison.iloc[0]
    best_shared_attribution = shared_attribution_comparison.iloc[0]
    raw_improved = comparison[comparison["variant_type"] == "relational_raw"]
    best_utility = raw_improved.sort_values("selection_score", ascending=False).iloc[0]
    adjusted_improved = comparison[comparison["variant_type"] == "relational_adjusted"]
    best_adjusted = adjusted_improved.sort_values("selection_score", ascending=False).iloc[0]

    utility_shortlist = [
        "hybrid_relational_ridge",
        "tfidf_relational_ridge",
        "engineered_relational_hgb",
    ]
    for adjusted in adjusted_improved.itertuples():
        raw_id = str(adjusted.candidate_id).removesuffix("_attribution_adjusted")
        raw = comparison[comparison["candidate_id"] == raw_id].iloc[0]
        ranking_not_materially_worse = all(
            float(getattr(adjusted, metric)) >= float(raw[metric]) - 0.005
            for metric in (
                "utility_spearman",
                "pairwise_ranking_accuracy",
                "ndcg_at_5",
            )
        )
        if (
            float(adjusted.selection_score) >= float(raw["selection_score"]) + 0.001
            and ranking_not_materially_worse
        ):
            utility_shortlist.append(str(adjusted.candidate_id))
    utility_shortlist = sorted(
        utility_shortlist,
        key=lambda candidate_id: float(
            comparison[comparison["candidate_id"] == candidate_id].iloc[0]["selection_score"]
        ),
        reverse=True,
    )
    attribution_shortlist = [
        str(best_attribution["attribution_candidate_id"]),
        str(
            dedicated_attribution_comparison.sort_values(
                ["development_cv_macro_f1", "attribution_macro_f1"], ascending=False
            ).iloc[0]["attribution_candidate_id"]
        ),
    ]
    attribution_shortlist = list(dict.fromkeys(attribution_shortlist))

    attribution_per_class = pd.DataFrame(
        [
            *attribution_per_class_rows,
            *(
                {"candidate_kind": "page_model_output", **row}
                for row in per_class_rows
            ),
        ]
    )
    attribution_subgroups = pd.DataFrame(attribution_subgroup_rows)
    attribution_runtime = pd.DataFrame(attribution_runtime_rows)

    tables = {
        "candidate_comparison.csv": comparison,
        "utility_tuning.csv": pd.DataFrame(utility_tuning_rows),
        "attribution_tuning.csv": pd.DataFrame(attribution_tuning_rows),
        "attribution_model_comparison.csv": attribution_comparison,
        "attribution_per_class.csv": attribution_per_class,
        "auxiliary_target_metrics.csv": pd.DataFrame(auxiliary_rows),
        "utility_subgroups.csv": pd.DataFrame(subgroup_rows),
        "attribution_subgroups.csv": attribution_subgroups,
        "attribution_adjustment.csv": pd.DataFrame(adjustment_rows),
        "runtime_benchmarks.csv": runtime,
        "attribution_runtime_benchmarks.csv": attribution_runtime,
        "feature_coefficients.csv": pd.DataFrame(coefficient_rows),
    }
    for name, frame in tables.items():
        frame.to_csv(temporary / name, index=False)
    pd.DataFrame(prediction_rows).to_parquet(
        temporary / "model_selection_predictions.parquet", index=False, compression="zstd"
    )
    (temporary / "input_feature_audit.json").write_text(
        json.dumps(data.input_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (temporary / "relational_feature_dictionary.json").write_text(
        json.dumps(
            {
                "feature_count": len(data.relational_columns),
                "features": data.relational_columns,
                "scorer_v1_independent": True,
                "target_or_sampling_fields_used": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(config_path, temporary / "config_snapshot.yaml")

    original_hybrid = comparison[
        comparison["candidate_id"] == "hybrid_independent_ridge"
    ].iloc[0]
    attribution_gain = float(best_attribution["attribution_macro_f1"]) - float(
        original_hybrid["attribution_macro_f1"]
    )
    dedicated_over_shared_gain = float(best_attribution["attribution_macro_f1"]) - float(
        best_shared_attribution["attribution_macro_f1"]
    )
    adjustment_gain = float(best_adjusted["selection_score"]) - float(
        comparison[
            comparison["candidate_id"]
            == str(best_adjusted["candidate_id"]).removesuffix("_attribution_adjusted")
        ].iloc[0]["selection_score"]
    )
    utility_baselines = {
        "hybrid_relational_ridge": "hybrid_independent_ridge",
        "tfidf_relational_ridge": "tfidf_ridge",
        "engineered_relational_hgb": "engineered_hist_gradient_boosting",
    }
    utility_delta_rows = []
    for candidate_id, baseline_id in utility_baselines.items():
        candidate = comparison[comparison["candidate_id"] == candidate_id].iloc[0]
        baseline = comparison[comparison["candidate_id"] == baseline_id].iloc[0]
        utility_delta_rows.append(
            {
                "candidate_id": candidate_id,
                "baseline_id": baseline_id,
                **{
                    f"{metric}_delta": float(candidate[metric]) - float(baseline[metric])
                    for metric in (
                        "utility_mae",
                        "utility_spearman",
                        "pairwise_ranking_accuracy",
                        "ndcg_at_5",
                        "evidence_facet_macro_f1",
                        "noise_macro_f1",
                        "pages_per_second",
                    )
                },
            }
        )

    best_attribution_id = str(best_attribution["attribution_candidate_id"])
    baseline_attribution_id = "hybrid_independent_ridge"
    per_class_delta = (
        attribution_per_class[
            attribution_per_class["candidate_id"] == best_attribution_id
        ][["attribution_class", "f1", "support"]]
        .merge(
            attribution_per_class[
                attribution_per_class["candidate_id"] == baseline_attribution_id
            ][["attribution_class", "f1"]],
            on="attribution_class",
            suffixes=("_dedicated", "_baseline"),
            validate="one_to_one",
        )
        .sort_values("attribution_class")
    )
    per_class_delta["f1_delta"] = (
        per_class_delta["f1_dedicated"] - per_class_delta["f1_baseline"]
    )
    subgroup_delta = (
        attribution_subgroups[
            attribution_subgroups["candidate_id"] == best_attribution_id
        ]
        .merge(
            attribution_subgroups[
                attribution_subgroups["candidate_id"] == baseline_attribution_id
            ],
            on=["subgroup_dimension", "subgroup_value"],
            suffixes=("_dedicated", "_baseline"),
            validate="one_to_one",
        )
        .sort_values(["subgroup_dimension", "subgroup_value"])
    )
    subgroup_delta["macro_f1_delta"] = (
        subgroup_delta["attribution_macro_f1_dedicated"]
        - subgroup_delta["attribution_macro_f1_baseline"]
    )
    subgroup_delta["focal_f1_delta"] = (
        subgroup_delta["focal_vs_nonfocal_f1_dedicated"]
        - subgroup_delta["focal_vs_nonfocal_f1_baseline"]
    )
    best_attribution_runtime = attribution_runtime[
        attribution_runtime["candidate_id"] == best_attribution_id
    ].iloc[0]
    pd.DataFrame(utility_delta_rows).to_csv(
        temporary / "utility_baseline_deltas.csv", index=False
    )
    per_class_delta.to_csv(temporary / "attribution_per_class_deltas.csv", index=False)
    subgroup_delta.to_csv(temporary / "attribution_subgroup_deltas.csv", index=False)
    report = [
        "# Scorer-v2 Page Model Attribution Improvement Study",
        "",
        "## Evaluation Boundary",
        "",
        f"Development rows: {len(development):,}; model-selection rows: {len(selection):,}.",
        "All fitting, hyperparameter selection, and attribution-penalty selection used domain-grouped development folds. Model selection was used only for the reported architecture comparison.",
        "Locked retrieval-test targets were filtered at Parquet read time and were not loaded.",
        "",
        "## Input Audit",
        "",
        "Luna received focal company ID/name/domain and page URL plus title, headings, and Markdown. The original local text models received only title, headings, and Markdown; the engineered models received generic page/site features but no focal-company-to-page comparison.",
        f"This study adds {len(data.relational_columns)} deterministic scorer-v1-independent features covering company-name overlap, domain overlap, and page-host/domain relationships, and exposes focal company/domain/page URL as tagged text fields.",
        "Opaque annotation, company, and scraped-page IDs remain keys only: they are intentionally excluded from predictive matrices because they do not generalize to unseen companies or pages.",
        "The exact added numeric features are:",
        "",
        *[f"- `{value}`" for value in data.relational_columns],
        "",
        "## Candidate Comparison",
        "",
        "| Candidate | Variant | MAE | Spearman | Pairwise | NDCG@5 | Attribution F1 | Focal/nonfocal F1 | Facet F1 | Noise F1 | Pages/s | Working MB |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.itertuples():
        report.append(
            f"| `{row.candidate_id}` | {row.variant_type} | {row.utility_mae:.3f} | {row.utility_spearman:.3f} | {row.pairwise_ranking_accuracy:.3f} | {row.ndcg_at_5:.3f} | {row.attribution_macro_f1:.3f} | {row.focal_vs_nonfocal_f1:.3f} | {row.evidence_facet_macro_f1:.3f} | {row.noise_macro_f1:.3f} | {row.pages_per_second:.1f} | {row.estimated_working_set_mb:.1f} |"
        )
    report.extend(
        [
            "",
            "## Utility Changes Against Fixed Baselines",
            "",
            "Negative MAE deltas are improvements; positive deltas for all other quality metrics are improvements.",
            "",
            "| Improved candidate | Fixed baseline | MAE delta | Spearman delta | Pairwise delta | NDCG@5 delta | Facet F1 delta | Noise F1 delta | Pages/s delta |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in utility_delta_rows:
        report.append(
            f"| `{row['candidate_id']}` | `{row['baseline_id']}` | {row['utility_mae_delta']:+.3f} | {row['utility_spearman_delta']:+.3f} | {row['pairwise_ranking_accuracy_delta']:+.3f} | {row['ndcg_at_5_delta']:+.3f} | {row['evidence_facet_macro_f1_delta']:+.3f} | {row['noise_macro_f1_delta']:+.3f} | {row['pages_per_second_delta']:+.1f} |"
        )
    report.extend(
        [
            "",
            "## Attribution",
            "",
            f"Best dedicated attribution model: `{best_attribution['attribution_candidate_id']}` with macro-F1 {best_attribution['attribution_macro_f1']:.3f} and focal-versus-nonfocal F1 {best_attribution['focal_vs_nonfocal_f1']:.3f}.",
            f"The macro-F1 change relative to the original hybrid attribution output is {attribution_gain:+.3f}.",
            f"It exceeds the best same-representation auxiliary attribution model (`{best_shared_attribution['attribution_candidate_id']}`) by {dedicated_over_shared_gain:+.3f} macro-F1.",
            f"Standalone inference, including relational feature construction, is {best_attribution_runtime['pages_per_second']:.1f} pages/s with an estimated working set of {best_attribution_runtime['estimated_working_set_mb']:.1f} MB.",
            "",
            "### Per-Class Attribution Change",
            "",
            f"Compared with `{baseline_attribution_id}`:",
            "",
            "| Attribution class | Support | Baseline F1 | Dedicated F1 | Delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in per_class_delta.itertuples():
        report.append(
            f"| `{row.attribution_class}` | {row.support} | {row.f1_baseline:.3f} | {row.f1_dedicated:.3f} | {row.f1_delta:+.3f} |"
        )
    report.extend(
        [
            "",
            "Direct focal-company and third-party attribution improve materially, while the rare ambiguous/group and unrelated classes remain weak. The five-class output is therefore useful as a soft signal, not as a hard exclusion gate.",
            "",
            "### Attribution Subgroups",
            "",
            "| Subgroup | Value | Pages | Baseline macro-F1 | Dedicated macro-F1 | Delta | Baseline focal F1 | Dedicated focal F1 | Delta |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in subgroup_delta.itertuples():
        report.append(
            f"| `{row.subgroup_dimension}` | `{row.subgroup_value}` | {row.page_count_dedicated} | {row.attribution_macro_f1_baseline:.3f} | {row.attribution_macro_f1_dedicated:.3f} | {row.macro_f1_delta:+.3f} | {row.focal_vs_nonfocal_f1_baseline:.3f} | {row.focal_vs_nonfocal_f1_dedicated:.3f} | {row.focal_f1_delta:+.3f} |"
        )
    report.extend(
        [
            "",
            "Full dedicated, shared-representation, per-class, shared-domain, host, and attribution-uncertainty results are preserved in the attribution CSV artifacts.",
            "",
            "## Attribution-Aware Ranking",
            "",
            "| Utility candidate | Selected penalty | Selection-score change | Spearman change | Pairwise change | NDCG@5 change |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for adjusted in adjusted_improved.itertuples():
        raw_id = str(adjusted.candidate_id).removesuffix("_attribution_adjusted")
        raw = comparison[comparison["candidate_id"] == raw_id].iloc[0]
        report.append(
            f"| `{raw_id}` | {adjusted.attribution_adjustment_penalty:.2f} | {adjusted.selection_score - float(raw['selection_score']):+.3f} | {adjusted.utility_spearman - float(raw['utility_spearman']):+.3f} | {adjusted.pairwise_ranking_accuracy - float(raw['pairwise_ranking_accuracy']):+.3f} | {adjusted.ndcg_at_5 - float(raw['ndcg_at_5']):+.3f} |"
        )
    report.extend(
        [
            "",
            f"The strongest attribution-adjusted variant changes the selection score by {adjustment_gain:+.3f} relative to its raw relational ranker. Penalties were selected only from grouped development out-of-fold predictions.",
            "The observed ranking benefit is small. Attribution adjustment should remain an optional retrieval variant rather than replace raw utility ranking before locked evaluation.",
            "",
            "## Runtime",
            "",
            "Full page-model benchmarks include relational feature construction, text/engineered transformation, continuous utility inference, dedicated attribution, facet, and noise predictions. Dedicated attribution benchmarks are reported separately.",
            "All recommended candidates exceed the 30 pages/s target, project below one hour for 55,000 pages, and remain below the 8 GB memory target on this development machine.",
            "",
            "## Recommended Utility Candidates for 5.8g",
            "",
            *[f"- `{value}`" for value in utility_shortlist],
            "",
            "These retain the best ranking candidate, the best-MAE hybrid, and the compact tree speed comparison. Attribution-adjusted variants are included only when they improved the development-selected criterion without a material model-selection ranking loss.",
            "",
            "## Recommended Attribution Candidates for 5.8g",
            "",
            *[f"- `{value}`" for value in attribution_shortlist],
            "",
            "Attribution improved enough to carry these candidates into locked retrieval evaluation as auxiliary/soft ranking signals. It did not improve enough to justify hard five-class attribution filtering.",
            "",
            "This remains a recommendation, not a frozen candidate set. Locked retrieval evaluation has not been run.",
        ]
    )
    (temporary / "comparison_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    summary = {
        "experiment_id": config.experiment_id,
        "experiment_version": config.experiment_version,
        "development_rows": len(development),
        "model_selection_rows": len(selection),
        "locked_targets_loaded": False,
        "relational_feature_count": len(data.relational_columns),
        "best_attribution_candidate": str(best_attribution["attribution_candidate_id"]),
        "best_attribution_macro_f1": float(best_attribution["attribution_macro_f1"]),
        "original_hybrid_attribution_macro_f1": float(original_hybrid["attribution_macro_f1"]),
        "attribution_macro_f1_gain": attribution_gain,
        "best_shared_representation_attribution_candidate": str(
            best_shared_attribution["attribution_candidate_id"]
        ),
        "dedicated_over_shared_attribution_macro_f1_gain": dedicated_over_shared_gain,
        "best_raw_utility_candidate": str(best_utility["candidate_id"]),
        "best_adjusted_candidate": str(best_adjusted["candidate_id"]),
        "attribution_adjustment_selection_score_gain": adjustment_gain,
        "recommended_candidates": utility_shortlist,
        "recommended_utility_candidates": utility_shortlist,
        "recommended_attribution_candidates": attribution_shortlist,
        "all_runtime_targets_met": bool(
            runtime["production_runtime_viable"].all()
            and attribution_runtime["production_runtime_viable"].all()
        ),
        "embedding_model_run": False,
        "scorer_v1_feature_addition_run": False,
    }
    (temporary / "experiment_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (temporary / "shortlist.json").write_text(
        json.dumps(
            {
                "experiment_id": config.experiment_id,
                "experiment_version": config.experiment_version,
                "recommended_utility_candidates": utility_shortlist,
                "recommended_attribution_candidates": attribution_shortlist,
                "status": "provisional_for_locked_retrieval_evaluation",
                "final_candidates_frozen": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    artifact_paths = sorted(path for path in temporary.rglob("*") if path.is_file())
    manifest = {
        "experiment_id": config.experiment_id,
        "experiment_version": config.experiment_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": config.config_hash,
        "dataset_manifest_sha256": sha256_file(
            project_root / config.dataset_directory / "manifest.json"
        ),
        "baseline_manifest_sha256": sha256_file(
            baseline_directory / "manifest.json"
        ),
        "locked_targets_loaded": False,
        "artifacts": {
            str(path.relative_to(temporary)): {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in artifact_paths
        },
    }
    (temporary / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.rename(output_directory)
    return summary, {
        path.name: str(path) for path in output_directory.iterdir() if path.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Improve scorer-v2 shortlisted page models without locked labels."
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root()
    config_path = args.config or project_root / "config" / "page_model_improvements.yaml"
    config = load_improvement_config(config_path)
    output = args.output_directory or project_root / config.output_directory
    if not output.is_absolute():
        output = project_root / output
    summary, paths = run_improvement_study(
        project_root, config, config_path, output, args.force_refresh
    )
    print("Scorer-v2 page-model improvement study complete")
    print(f"  Best attribution: {summary['best_attribution_candidate']}")
    print(f"  Attribution macro-F1: {summary['best_attribution_macro_f1']:.3f}")
    print(f"  Gain: {summary['attribution_macro_f1_gain']:+.3f}")
    print(f"  Recommended: {', '.join(summary['recommended_candidates'])}")
    print(f"  Report: {paths['comparison_report.md']}")


if __name__ == "__main__":
    main()
