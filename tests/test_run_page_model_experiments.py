from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS
from ai_ecosystem.calibration.run_page_model_experiments import (
    ExperimentConfig,
    grouped_ranking_metrics,
    load_experiment_data,
    pairwise_ranking_accuracy,
    structural_predictions,
    tune_ridge_alpha,
    utility_metrics,
)


def config() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="test",
        experiment_version="v1",
        dataset_directory="dataset",
        output_directory="output",
        random_seed=7,
        development_cv_folds=2,
        ridge_alphas=(0.1, 1.0),
        tree_candidates=({"learning_rate": 0.1, "max_leaf_nodes": 5, "l2_regularization": 1.0},),
        tfidf={
            "word_ngram_range": [1, 2],
            "character_ngram_range": [3, 4],
            "word_max_features": 100,
            "character_max_features": 100,
            "min_document_frequency": 1,
            "max_document_frequency": 1.0,
            "sublinear_term_frequency": True,
        },
        auxiliary_models={"linear_alpha": 0.001, "max_iterations": 100, "positive_facet_threshold": 1},
        ranking_top_k=(2,),
        runtime_targets={
            "minimum_pages_per_second": 30.0,
            "corpus_page_count": 100.0,
            "maximum_runtime_seconds": 10.0,
            "maximum_memory_mb": 8192.0,
        },
        excluded_engineered_features=("site__company_selected_page_count",),
        config_hash="hash",
    )


class PageModelExperimentTest(unittest.TestCase):
    def test_loader_never_admits_locked_targets_or_forbidden_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = [
                ("D1:1", "D1", 1, "development"),
                ("D2:2", "D2", 2, "development"),
                ("M1:3", "M1", 3, "model_selection"),
                ("L1:4", "L1", 4, "locked_retrieval_test"),
            ]
            split = pd.DataFrame(
                [
                    {
                        "annotation_item_id": item,
                        "company_id": company,
                        "scraped_page_id": page,
                        "normalized_domain": f"{company.lower()}.test",
                        "split": split_name,
                        "shared_domain": False,
                        "content_sha256": f"h{page}",
                        "cross_split_exact_duplicate": False,
                        "modeling_eligible": True,
                        "modeling_exclusion_reason": None,
                    }
                    for item, company, page, split_name in keys
                ]
            )
            targets = []
            for item, company, page, split_name in keys:
                row = {
                    "annotation_item_id": item,
                    "company_id": company,
                    "scraped_page_id": page,
                    "utility_score": 99 if split_name == "locked_retrieval_test" else page % 4,
                    "utility_score_available": True,
                    "attribution": "focal_company_direct",
                    "attribution_available": True,
                    "trust_status": "trusted",
                }
                for facet in FACETS:
                    row[f"facet__{facet}__strength"] = 0
                    row[f"facet__{facet}__available"] = True
                for flag in NOISE_FLAGS:
                    row[f"noise__{flag}"] = False
                    row[f"noise__{flag}__available"] = True
                targets.append(row)
            canonical = pd.DataFrame(
                [
                    {
                        "annotation_item_id": item,
                        "company_id": company,
                        "scraped_page_id": page,
                        "language": "en",
                        "sampling_pool_audit_only": "structural",
                        "scorer_v1_diagnostic_audit_only": False,
                        "calibration_source_audit_only": "test",
                        "trust_status": "trusted",
                    }
                    for item, company, page, _ in keys
                ]
            )
            engineered = pd.DataFrame(
                [
                    {
                        "annotation_item_id": item,
                        "company_id": company,
                        "scraped_page_id": page,
                        "url__depth": page,
                        "site__company_usable_page_count": 5,
                        "site__company_selected_page_count": 1,
                    }
                    for item, company, page, _ in keys
                ]
            )
            scorer = pd.DataFrame(
                [
                    {
                        "annotation_item_id": item,
                        "company_id": company,
                        "scraped_page_id": page,
                        "scorer_v1__source_run_id": "run",
                        "scorer_v1__technology__final_score": float(page),
                    }
                    for item, company, page, _ in keys
                ]
            )
            documents = pd.DataFrame(
                [
                    {
                        "annotation_item_id": item,
                        "company_id": company,
                        "scraped_page_id": page,
                        "title": "Title",
                        "headings_json": "[]",
                        "model_visible_markdown": "text",
                        "markdown_truncated": False,
                    }
                    for item, company, page, _ in keys
                ]
            )
            for name, frame in (
                ("split_manifest", split),
                ("targets", pd.DataFrame(targets)),
                ("canonical_pages", canonical),
                ("engineered_features", engineered),
                ("scorer_v1_features", scorer),
                ("text_documents", documents),
            ):
                frame.to_parquet(root / f"{name}.parquet", index=False)
            (root / "manifest.json").write_text(json.dumps({"dataset_id": "test"}), encoding="utf-8")

            data = load_experiment_data(root, config())
            self.assertEqual(set(data.development.annotation_item_id), {"D1:1", "D2:2"})
            self.assertEqual(set(data.model_selection.annotation_item_id), {"M1:3"})
            self.assertNotIn("L1:4", set(data.development.annotation_item_id) | set(data.model_selection.annotation_item_id))
            self.assertNotIn("site__company_selected_page_count", data.engineered_columns)
            self.assertFalse(any(column.startswith("scorer_v1__") for column in data.engineered_columns))
            self.assertEqual(data.scorer_v1_columns, ["scorer_v1__technology__final_score"])
            self.assertEqual(set(data.development["language_group"]), {"en"})

    def test_perfect_within_company_ranking_metrics(self) -> None:
        target = np.asarray([0, 1, 2, 3], dtype=float)
        score = np.asarray([0.1, 1.1, 2.1, 3.1], dtype=float)
        groups = np.asarray(["A", "A", "B", "B"])
        self.assertEqual(pairwise_ranking_accuracy(target, score, groups), 1.0)
        metrics, diagnostics = grouped_ranking_metrics(target, score, groups, (2,))
        self.assertAlmostEqual(metrics["annotation_pool_ndcg"], 1.0)
        self.assertIsNotNone(metrics["annotation_pool_dcg"])
        self.assertAlmostEqual(metrics["ndcg_at_2"], 1.0)
        self.assertEqual(len(diagnostics), 2)
        utility, _ = utility_metrics(target, score, groups, (2,))
        self.assertAlmostEqual(utility["utility_spearman"], 1.0)

    def test_ridge_tuning_is_deterministic_and_grouped(self) -> None:
        matrix = np.arange(24, dtype=float).reshape(8, 3)
        target = np.asarray([0, 1, 1, 2, 2, 3, 1, 0], dtype=float)
        groups = np.asarray(["A", "A", "B", "B", "C", "C", "D", "D"])
        first = tune_ridge_alpha(matrix, target, groups, config())
        second = tune_ridge_alpha(matrix, target, groups, config())
        self.assertEqual(first, second)

    def test_structural_rule_returns_continuous_deterministic_outputs(self) -> None:
        frame = pd.DataFrame(
            {
                "text__markdown_word_count": [20, 500],
                "url__is_root": [0, 1],
                "url__is_about_company": [0, 0],
                "url__is_shallow": [0, 1],
                "structure__heading_count": [0, 4],
                "page_type__is_products": [0, 1],
                "page_type__is_services": [0, 0],
                "page_type__is_technology": [0, 0],
                "page_type__is_innovation": [0, 0],
                "page_type__is_homepage": [0, 1],
                "page_type__is_company": [0, 0],
                "page_type__is_legal": [1, 0],
                "page_type__is_careers": [0, 0],
                "page_type__is_news": [0, 0],
                "duplicate__has_exact_duplicate": [0, 0],
                "link__total_count": [2, 5],
                "site__attribution_uncertain": [False, False],
                "site__shared_domain": [False, False],
            }
        )
        first = structural_predictions(frame)
        second = structural_predictions(frame)
        np.testing.assert_allclose(first["utility"], second["utility"])
        self.assertGreater(first["utility"][1], first["utility"][0])
        self.assertEqual(first["attribution"].tolist(), ["focal_company_direct", "focal_company_direct"])


if __name__ == "__main__":
    unittest.main()
