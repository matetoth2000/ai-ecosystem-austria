from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ai_ecosystem.calibration.run_page_model_experiments import ATTRIBUTION_VALUES
from ai_ecosystem.calibration.run_page_model_improvements import (
    ImprovementConfig,
    apply_probability_multipliers,
    attribution_metrics,
    focal_confidence,
    tune_attribution_candidate,
    tune_attribution_penalty,
    tune_probability_multipliers,
)


def config() -> ImprovementConfig:
    return ImprovementConfig(
        experiment_id="test",
        experiment_version="v1",
        dataset_directory="dataset",
        annotation_sample_directory="sample",
        baseline_directory="baseline",
        baseline_config="baseline.yaml",
        output_directory="output",
        random_seed=11,
        development_cv_folds=2,
        ridge_alphas=(1.0,),
        tree_candidates=(
            {
                "learning_rate": 0.1,
                "max_leaf_nodes": 5,
                "min_samples_leaf": 2,
                "l2_regularization": 1.0,
            },
        ),
        attribution_models={
            "sgd_alphas": [0.001],
            "class_weight_powers": [0.5],
            "max_iterations": 200,
            "hgb_candidates": [],
        },
        adjustment_penalties=(0.0, 0.5),
        probable_credit=0.65,
        runtime_targets={
            "minimum_pages_per_second": 30.0,
            "corpus_page_count": 100.0,
            "maximum_runtime_seconds": 10.0,
            "maximum_memory_mb": 100.0,
        },
        config_hash="hash",
    )


class PageModelImprovementTest(unittest.TestCase):
    def test_probability_calibration_is_deterministic(self) -> None:
        target = np.asarray(list(ATTRIBUTION_VALUES) * 4)
        probabilities = np.full((len(target), len(ATTRIBUTION_VALUES)), 0.1)
        probabilities[:, 0] = 0.6
        for index, label in enumerate(target):
            probabilities[index, ATTRIBUTION_VALUES.index(label)] += 0.2
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        first = tune_probability_multipliers(probabilities, target)
        second = tune_probability_multipliers(probabilities, target)
        self.assertEqual(first, second)
        calibrated = apply_probability_multipliers(probabilities, first[0])
        self.assertTrue(np.allclose(calibrated.sum(axis=1), 1.0))

    def test_attribution_candidate_preserves_full_row_alignment(self) -> None:
        labels = list(ATTRIBUTION_VALUES)
        rows = []
        matrix = []
        for group in range(6):
            for position, label in enumerate(labels):
                rows.append(
                    {
                        "normalized_domain": f"g{group}.test",
                        "attribution": label,
                        "attribution_available": not (group == 5 and position == 4),
                    }
                )
                values = np.zeros(len(labels))
                values[position] = 1.0
                matrix.append(values)
        frame = pd.DataFrame(rows)
        values = np.asarray(matrix)
        candidate, _ = tune_attribution_candidate(
            "test_sgd",
            "test",
            "sgd",
            values,
            values,
            frame,
            frame,
            config(),
        )
        self.assertEqual(candidate.development_oof_probabilities.shape, values.shape)
        self.assertTrue(np.isnan(candidate.development_oof_probabilities[-1]).all())
        self.assertEqual(len(candidate.model_selection_predictions), len(frame))

    def test_attribution_metrics_and_focal_confidence(self) -> None:
        truth = np.asarray(ATTRIBUTION_VALUES)
        metrics, rows = attribution_metrics(truth, truth.copy())
        self.assertEqual(metrics["attribution_macro_f1"], 1.0)
        self.assertEqual(metrics["focal_vs_nonfocal_f1"], 1.0)
        self.assertEqual(len(rows), len(ATTRIBUTION_VALUES))
        probabilities = np.eye(len(ATTRIBUTION_VALUES))
        confidence = focal_confidence(probabilities, 0.65)
        np.testing.assert_allclose(confidence[:2], [1.0, 0.65])
        np.testing.assert_allclose(confidence[2:], 0.0)

    def test_attribution_penalty_tuning_is_deterministic(self) -> None:
        raw = np.asarray([0.2, 0.8, 1.5, 2.2, 2.8, 1.1])
        target = np.asarray([0, 1, 1, 2, 3, 1], dtype=float)
        groups = np.asarray(["A", "A", "B", "B", "C", "C"])
        probabilities = np.full((6, len(ATTRIBUTION_VALUES)), 0.05)
        probabilities[:, 0] = np.asarray([0.9, 0.9, 0.2, 0.9, 0.9, 0.2])
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        first = tune_attribution_penalty(raw, probabilities, target, groups, (0.0, 0.5), 0.65)
        second = tune_attribution_penalty(raw, probabilities, target, groups, (0.0, 0.5), 0.65)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
