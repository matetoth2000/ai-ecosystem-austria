from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS
from ai_ecosystem.calibration.run_locked_retrieval_evaluation import (
    EvaluationConfig,
    load_evaluation_config,
    ranking_metrics,
    pareto_rankers,
    select_budget_pages,
)


def config() -> EvaluationConfig:
    return EvaluationConfig(
        evaluation_id="test", evaluation_version="v1", annotation_sample_directory="sample",
        annotation_run_id="run", annotation_run_directory="run", annotation_qa_version="qa",
        model_directory="models", model_manifest="manifest", training_feature_dictionary="features",
        baseline_model_config="baseline", output_directory="output",
        candidate_ids=("tfidf_relational_ridge_attribution_adjusted", "tfidf_relational_ridge", "hybrid_relational_ridge", "engineered_relational_hgb"),
        dedicated_attribution_model="dedicated_engineered_relational_hgb",
        relevance_utility_threshold=2, ranking_k=(5, 10, 20), token_budgets=(20,), random_seed=7,
        set_selection={
            "strategies": ["top_ranked", "diversity_aware"], "utility_weight": 0.65,
            "focal_attribution_weight": 0.15, "facet_novelty_weight": 0.10,
            "path_novelty_weight": 0.05, "redundancy_penalty": 0.30,
            "predicted_noise_penalty": 0.10, "lexical_token_cap": 512,
            "material_utility_gain": 0.01, "material_facet_gain": 0.03,
            "maximum_utility_tradeoff": 0.01,
        }, config_hash="hash",
    )


class LockedRetrievalEvaluationTest(unittest.TestCase):
    def test_versioned_config_loads_required_candidates_and_budgets(self) -> None:
        source = Path(__file__).parents[1] / "config" / "locked_retrieval_evaluation.yaml"
        loaded = load_evaluation_config(source)
        self.assertEqual(loaded.ranking_k, (5, 10, 20))
        self.assertEqual(loaded.token_budgets, (8000, 16000, 32000, 50000))
        self.assertEqual(len(loaded.candidate_ids), 4)

    def test_ranking_metrics_use_true_company_wide_pages(self) -> None:
        rows = []
        for page_id, utility in enumerate((3, 2, 0, 1, 0, 0), start=1):
            row = {
                "annotation_item_id": f"C:{page_id}", "company_id": "C", "scraped_page_id": page_id,
                "utility_score": utility, "attribution": "focal_company_direct",
                "url__is_root": int(page_id == 1), "url__is_about_company": 0, "url__is_shallow": 1,
                "text__markdown_word_count": 100, "structure__heading_count": 2,
                "page_type__is_products": 0, "page_type__is_services": 0,
                "page_type__is_technology": 0, "page_type__is_innovation": 0,
                "page_type__is_legal": 0, "page_type__is_careers": 0, "page_type__is_news": 0,
                **{f"truth_noise__{flag}": 0 for flag in NOISE_FLAGS},
                **{f"scorer_v1__{dimension}__final_score": 0.0 for dimension in ("firm_profile", "technology", "innovation", "ecosystem")},
            }
            rows.append(row)
        truth = pd.DataFrame(rows)
        predictions = truth[["annotation_item_id", "company_id", "scraped_page_id"]].copy()
        predictions["candidate_id"] = "tfidf_relational_ridge"
        predictions["predicted_utility"] = [6, 5, 1, 4, 3, 2]
        _, summary = ranking_metrics(truth, predictions, config())
        result = next(row for row in summary if row["candidate_id"] == "tfidf_relational_ridge" and row["k"] == 5)
        self.assertEqual(result["precision"], 0.4)
        self.assertGreater(result["ndcg"], 0.9)
        self.assertEqual(result["focal_attribution_rate"], 1.0)

    def test_diversity_selection_can_avoid_near_duplicate_high_ranked_page(self) -> None:
        rows = []
        texts = ("alpha beta gamma", "alpha beta gamma", "robotics sensor fusion")
        for index, text in enumerate(texts):
            row = {
                "scraped_page_id": index + 1, "predicted_utility": 3.0 - index * 0.1,
                "model_input_tokens": 10, "model_visible_markdown": text,
                "lexical_token_set": frozenset(text.split()), "path_family": "about" if index < 2 else "technology",
                "dedicated_focal_confidence": 1.0,
            }
            row.update({f"predicted_facet__{facet}": int(facet == ("company_activity" if index < 2 else "technical_capability")) for facet in FACETS})
            row.update({f"predicted_noise__{flag}": 0 for flag in NOISE_FLAGS})
            rows.append(row)
        frame = pd.DataFrame(rows)
        top = select_budget_pages(frame, 20, "top_ranked", config())
        diverse = select_budget_pages(frame, 20, "diversity_aware", config())
        self.assertEqual(top, [0, 1])
        self.assertEqual(diverse, [0, 2])

    def test_pareto_rankers_remove_strictly_dominated_candidate(self) -> None:
        rows = pd.DataFrame([
            {"candidate_id": "a", "ndcg": 0.8, "precision": 0.7, "focal_attribution_rate": 0.9, "noise_rate": 0.1},
            {"candidate_id": "b", "ndcg": 0.7, "precision": 0.6, "focal_attribution_rate": 0.8, "noise_rate": 0.2},
            {"candidate_id": "c", "ndcg": 0.9, "precision": 0.6, "focal_attribution_rate": 0.8, "noise_rate": 0.2},
        ])
        self.assertEqual(pareto_rankers(rows), ["a", "c"])

    def test_pareto_rankers_can_exclude_restricted_attribution_metric(self) -> None:
        rows = pd.DataFrame([
            {"candidate_id": "a", "ndcg": 0.8, "precision": 0.8, "focal_attribution_rate": 0.7, "noise_rate": 0.1},
            {"candidate_id": "b", "ndcg": 0.7, "precision": 0.7, "focal_attribution_rate": 0.9, "noise_rate": 0.2},
        ])
        self.assertEqual(pareto_rankers(rows), ["a", "b"])
        self.assertEqual(pareto_rankers(rows, include_attribution=False), ["a"])


if __name__ == "__main__":
    unittest.main()
