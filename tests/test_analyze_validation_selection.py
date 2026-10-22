import unittest
from collections import Counter

from ai_ecosystem.calibration.analyze_validation_selection import (
    CANDIDATES, MODELS, RUN, build_rows, candidate, summarize, wilson,
)


class SelectionAnalysisTests(unittest.TestCase):
    def test_missing_label_bounds_and_unknown_denominator(self):
        rows = ([{"analysis_label": "KEEP"}] * 2 + [{"analysis_label": "UNKNOWN"},
                {"analysis_label": "EXCLUDE"}, {"analysis_label": "UNRESOLVED"}])
        for row in rows:
            row["estimated_batch_input_usd"] = 0.01
            row["estimated_batch_output_reserved_usd"] = 0.02
            row["estimated_batch_total_reserved_usd"] = 0.03
        metric = summarize(rows)
        self.assertEqual(metric["keep_precision_labelled"], 2 / 4)
        self.assertEqual(metric["observed_keep_recall"], 2 / 52)
        self.assertEqual(metric["precision_missing_low"], 2 / 5)
        self.assertEqual(metric["precision_missing_high"], 3 / 5)
        self.assertEqual(metric["recall_missing_low"], 2 / (52 + 18))
        self.assertEqual(metric["recall_missing_high"], 3 / 53)
        self.assertAlmostEqual(metric["estimated_batch_total_reserved_usd"], 0.15)

    def test_zero_selection_and_cutoff_ties(self):
        rows = [{"analysis_label": label, "reference_logistic_size": score,
                 "estimated_batch_input_usd": 0, "estimated_batch_output_reserved_usd": 0,
                 "estimated_batch_total_reserved_usd": 0} for label, score in
                (("KEEP", 2), ("EXCLUDE", 2), ("UNRESOLVED", 1))]
        empty = candidate(rows, MODELS[0], 3)
        self.assertEqual(empty["selected_total"], 0)
        self.assertIsNone(empty["keep_precision_labelled"])
        self.assertIsNone(empty["precision_wilson95_low"])
        tied = candidate(rows, MODELS[0], 2)
        self.assertEqual((tied["selected_total"], tied["selected_labelled"]), (2, 2))
        self.assertEqual(tied["selected_keep"], 1)
        self.assertEqual(wilson(0, 0), (None, None))

    def test_saved_selection_join_and_candidate_consistency(self):
        if not (RUN / "selection_results").exists():
            self.skipTest("Frozen selection artifacts unavailable")
        rows, _ = build_rows()
        self.assertEqual(len(rows), 865)
        self.assertEqual(len({row["company_id"] for row in rows}), 865)
        self.assertEqual(Counter(row["analysis_label"] for row in rows),
                         {"KEEP": 52, "UNKNOWN": 60, "EXCLUDE": 734, "UNRESOLVED": 19})
        self.assertEqual(Counter(row["label_source"] for row in rows),
                         {"original_valid": 843, "recovered_review_v5": 3, "invalid_unresolved": 19})
        for model in MODELS:
            for cutoff in CANDIDATES[model]:
                result = candidate(rows, model, cutoff)
                self.assertEqual(result["selected_total"], sum(row[model] >= cutoff for row in rows))
                self.assertEqual(result["selected_total"], result["selected_labelled"] + result["selected_unresolved"])


if __name__ == "__main__":
    unittest.main()
