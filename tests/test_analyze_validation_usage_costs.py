import tempfile
import unittest
from collections import Counter
from pathlib import Path

from ai_ecosystem.calibration.analyze_validation_usage_costs import (
    CANDIDATES, MODELS, OUTPUT, budget_options, curves, load_rows, scenarios,
    selection_usage, usage_cost,
)


class UsageCostAnalysisTests(unittest.TestCase):
    def test_only_usage_metadata_is_decoded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            path.write_text('{"profile":THIS IS NOT JSON,"returned_model":"gpt-6-luna",'
                            '"usage":{"input_tokens":10,"output_tokens":2}}')
            usage, model = selection_usage(path)
            self.assertEqual((usage["input_tokens"], model), (10, "gpt-6-luna"))

    def test_cache_and_reasoning_arithmetic(self):
        usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200,
                  "cache_write_tokens": 300}, "output_tokens": 120,
                 "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 1120}
        cost = usage_cost(usage)
        self.assertEqual(cost["ordinary_input_tokens"], 500)
        self.assertEqual(cost["nonreasoning_output_tokens"], 80)
        self.assertAlmostEqual(cost["input_cost_usd"],
                               (500 * .05 + 200 * .005 + 300 * .0625) / 1_000_000)
        self.assertAlmostEqual(cost["output_cost_usd"], 120 * .25 / 1_000_000)
        self.assertLess(usage_cost(usage, write_multiplier=1)["total_cost_usd"], cost["total_cost_usd"])
        with self.assertRaises(ValueError):
            usage_cost({**usage, "total_tokens": 999})

    def test_long_context_boundary(self):
        base = {"input_tokens": 272000, "output_tokens": 100, "total_tokens": 272100}
        short = usage_cost(base)
        long = usage_cost({"input_tokens": 272001, "output_tokens": 100, "total_tokens": 272101})
        self.assertFalse(short["long_context_pricing"])
        self.assertTrue(long["long_context_pricing"])
        self.assertAlmostEqual(long["output_cost_usd"], short["output_cost_usd"] * 1.5)

    def test_saved_selection_and_cutoff_arithmetic(self):
        if not OUTPUT.exists():
            self.skipTest("Saved usage-cost analysis not available")
        rows, audit = load_rows()
        self.assertEqual((len(rows), audit["missing_usage_company_ids"]), (865, []))
        self.assertEqual(Counter(r["analysis_label"] for r in rows),
                         {"KEEP": 52, "UNKNOWN": 60, "EXCLUDE": 734, "UNRESOLVED": 19})
        points = curves(rows)
        self.assertTrue(any(r["cutoff"] < 0 for r in points))
        self.assertTrue(all(any(r["model"] == model and r["cutoff"] == cutoff for r in points)
                            for model in MODELS for cutoff in CANDIDATES[model]))
        for point in points:
            selected = [r for r in rows if r[point["model"]] >= point["cutoff"]]
            self.assertEqual(point["selected_total"], len(selected))
            self.assertAlmostEqual(point["usage_total_usd"], sum(r["total_cost_usd"] for r in selected))
            self.assertEqual(point["selected_unresolved"],
                             sum(r["analysis_label"] == "UNRESOLVED" for r in selected))
        choices = budget_options(scenarios(points))
        self.assertEqual(len(choices), 12)
        self.assertTrue(all(r["scenario_usage_cost_usd"] <= r["planned_requests_usd"] for r in choices))


if __name__ == "__main__":
    unittest.main()
