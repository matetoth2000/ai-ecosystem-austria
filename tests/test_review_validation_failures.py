import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.company_ai_profile_contract import derive_label, validate_profile
from ai_ecosystem.calibration.review_validation_failures import (
    DEFAULT_OUTPUT, DEFAULT_RUN, quote_diagnosis, selected_raw_responses,
)


class FailureReviewTests(unittest.TestCase):
    def test_quote_diagnosis_distinguishes_mechanical_and_substantive_cases(self):
        pages = {"1": "First page with ordinary text.", "2": "Exact AI sentence for this company."}
        self.assertEqual(quote_diagnosis("Exact AI sentence", "1", pages),
                         ("exact_quote_on_other_page", ["2"]))
        self.assertEqual(quote_diagnosis("Exact AI\nsentence", "2", pages)[0],
                         "internal_whitespace_difference")
        self.assertEqual(quote_diagnosis("This quotation was invented", "1", pages)[0],
                         "quotation_absent_from_supplied_evidence")

    def test_raw_reader_skips_nonselection_body_without_decoding(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / "raw_results"
            directory.mkdir()
            selected = {"custom_id": "selection-id", "response": {"body": {"id": "r1"}}}
            (directory / "one.output.jsonl").write_text(
                '{"custom_id":"holdout-id","response":THIS IS NOT JSON}\n'
                + json.dumps(selected) + "\n")
            found = selected_raw_responses(Path(name), {"selection-id"})
            self.assertEqual(found["selection-id"][0]["id"], "r1")
            self.assertEqual(len(found), 1)

    def test_saved_review_is_complete_and_non_destructive(self):
        if not (DEFAULT_OUTPUT / "summary.json").exists():
            self.skipTest("Saved selection review not available")
        summary = json.loads((DEFAULT_OUTPUT / "summary.json").read_text())
        diagnostics = [json.loads(line) for line in (DEFAULT_OUTPUT / "diagnostics.jsonl").read_text().splitlines()]
        recovered = [json.loads(line) for line in (DEFAULT_OUTPUT / "recovered_results.jsonl").read_text().splitlines()]
        self.assertEqual((summary["selection_responses"], summary["invalid_reviewed"]), (865, 22))
        self.assertEqual((len(diagnostics), len(recovered)), (22, 3))
        self.assertEqual(len({row["company_id"] for row in diagnostics}), 22)
        self.assertTrue(all(row["raw_status"] == "completed" and row["output_tokens"] < row["max_output_tokens"]
                            and row["incomplete_details"] is None for row in diagnostics))
        self.assertTrue(all(row["source_raw_response_sha256"] for row in diagnostics))
        schema = json.loads((DEFAULT_RUN / "schema_snapshot.json").read_text())
        for row in diagnostics:
            path = DEFAULT_RUN / "selection_results" / (row["company_id"] + ".json")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["source_result_sha256"])
            self.assertEqual(json.loads(path.read_text())["status"], row["original_status"])
        for row in recovered:
            work = json.loads((DEFAULT_RUN / "work" / (row["company_id"] + ".json")).read_text())
            package = json.loads(work["batch_line"]["body"]["input"][-1]["content"])
            self.assertEqual(row["split"], "selection")
            profile = validate_profile(row["profile"], package, schema, contract_id=row["validation_contract"])
            self.assertEqual(derive_label(profile, mapping_version=row["mapping_version"]), row["derived"])


if __name__ == "__main__":
    unittest.main()
