import csv
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from ai_ecosystem.calibration.evaluate_validation_holdout import (
    OUTPUT, RUN, group_intervals, metrics, selection_cost_rows, verify_freeze,
)
from ai_ecosystem.calibration.company_ai_profile_contract import derive_label, validate_profile


def row(cid, score, label, group=None):
    return {"company_id": cid, "group_id": group or cid, "score": score, "label": label,
            "input_tokens": 100, "output_tokens": 20, "reasoning_output_tokens_included": 5,
            "input_cost_usd": .01, "output_cost_usd": .02, "total_cost_usd": .03}


class HoldoutEvaluationTests(unittest.TestCase):
    def test_ties_unknown_and_unresolved_bounds(self):
        rows = [row("a", 2, "KEEP"), row("b", 2, "UNKNOWN"),
                row("c", 2, "UNRESOLVED"), row("d", 1, "KEEP"), row("e", 0, "EXCLUDE")]
        result = metrics(rows, 2)
        self.assertEqual((result["selected_total"], result["selected_labelled"]), (3, 2))
        self.assertEqual(result["confirmed_keep_precision"], .5)
        self.assertEqual(result["observed_keep_recall"], .5)
        self.assertEqual((result["precision_missing_low"], result["precision_missing_high"]), (1 / 3, 2 / 3))
        self.assertEqual((result["recall_missing_low"], result["recall_missing_high"]), (.5, 2 / 3))
        self.assertAlmostEqual(result["selected_total_usd"], .09)
        self.assertEqual(result["selected_output_tokens_including_reasoning"], 60)
        self.assertIsNone(metrics(rows, 3)["confirmed_keep_precision"])

    def test_group_bootstrap_is_deterministic(self):
        rows = [row("a", 2, "KEEP", "shared"), row("b", 1, "EXCLUDE", "shared"),
                row("c", .5, "KEEP"), row("d", -.5, "UNKNOWN")]
        policies = [{"budget_usd": 100, "cutoff": 1}]
        self.assertEqual(group_intervals(rows, policies, 17, 50), group_intervals(rows, policies, 17, 50))

    def test_selection_cost_join_preserves_label_and_score(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.csv"
            fields = ["company_id", "analysis_label", "label_source", "cleaned_grounded_combined",
                      "input_tokens", "output_tokens", "reasoning_output_tokens_included",
                      "input_cost_usd", "output_cost_usd", "total_cost_usd"]
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(dict(zip(fields, ["a", "KEEP", "original_valid", "2", "100", "20", "5", ".01", ".02", ".03"])))
            selection = [{"company_id": "a", "group_id": "g", "analysis_label": "KEEP",
                          "label_source": "original_valid", "cleaned_grounded_combined": "2"}]
            self.assertEqual(selection_cost_rows(selection, path)[0]["total_cost_usd"], .03)
            selection[0]["analysis_label"] = "EXCLUDE"
            with self.assertRaises(ValueError):
                selection_cost_rows(selection, path)

    def test_saved_freeze_recovery_and_metrics(self):
        if not (OUTPUT / "report_v2.md").exists():
            self.skipTest("Saved frozen evaluation unavailable")
        freeze, freeze_hash = verify_freeze(OUTPUT)
        summary = json.loads((OUTPUT / "evaluation_manifest.json").read_text())
        self.assertEqual(summary["freeze_sha256"], freeze_hash)
        self.assertEqual((summary["holdout_companies"], summary["original_valid"], summary["recovered"], summary["unresolved"]),
                         (216, 208, 2, 6))
        self.assertEqual(sum(summary["holdout_labels"].values()), 216)
        with (OUTPUT / "policy_results_v2.csv").open() as handle:
            policies = list(csv.DictReader(handle))
        self.assertEqual([float(r["holdout_cutoff"]) for r in policies], [p["cutoff"] for p in freeze["policies"]])
        self.assertEqual([int(r["holdout_selected_keep"]) for r in policies], [13, 15, 16])
        self.assertTrue(all(float(r["selection_selected_total_usd"]) > 0 for r in policies))
        recovered = json.loads((OUTPUT / "recovered_results.json").read_text())
        self.assertEqual(len(recovered), 2)
        schema = json.loads((RUN / "schema_snapshot.json").read_text())
        for record in recovered:
            cid = record["company_id"]
            original = RUN / "holdout_results" / f"{cid}.json"
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), record["source_result_sha256"])
            work = json.loads((RUN / "work" / f"{cid}.json").read_text())
            package = json.loads(work["batch_line"]["body"]["input"][-1]["content"])
            profile = validate_profile(record["profile"], package, schema, contract_id=record["validation_contract"])
            self.assertEqual(derive_label(profile, mapping_version=record["mapping_version"]), record["derived"])
        with (OUTPUT / "holdout_company_metrics.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(Counter(r["label"] for r in rows), summary["holdout_labels"])


if __name__ == "__main__":
    unittest.main()
