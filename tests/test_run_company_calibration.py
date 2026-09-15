from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.run_company_calibration import (
    load_prompt,
    run_company_calibration,
    validate_model_output,
)
from ai_ecosystem.db.init_db import init_database


def output(label: str = "KEEP") -> dict:
    return {
        "label": label, "confidence": 0.9, "company_activity_summary": "Develops analytics software.",
        "ai_relevance_summary": "The focal company develops machine learning models.", "primary_reason": "Direct capability statement.",
        "attribution_assessment": {"focal_company_identified": True, "domain_attribution_reliable": True, "shared_domain_issue": False, "third_party_content_issue": False, "notes": "Direct service page."},
        "strongest_supporting_evidence": [{"page_id": "1", "url": "https://example.test/ai", "evidence_type": "service", "subject": "focal_company", "quote": "We develop machine learning models.", "interpretation": "Direct focal-company capability."}],
        "ai_evidence": {"explicit_ai": True, "ai_adjacent": False, "company_attributed": True, "strength": "strong", "capabilities": ["machine learning"]},
        "exclusion_evidence": {"activity_clear": True, "conventional_activity": False, "nontechnical_activity": False, "sufficient_evidence_for_exclusion": False, "description": "Technical activity."},
        "uncertainty": {"present": False, "reasons": []},
        "lexical_signals": [{"phrase": "machine learning", "normalized_phrase": "machine learning", "signal_type": "positive_ai", "page_id": "1", "url": "https://example.test/ai", "subject": "focal_company", "short_context": "We develop machine learning models.", "strength": "strong", "scorer_recommendation": "consider_positive_keyword", "explanation": "Direct capability."}],
    }


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.id = "resp_test"; self.model = "gpt-5.6-luna"; self.output_text = json.dumps(payload)
        self.usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "output_tokens_details": {"reasoning_tokens": 5}}

    def model_dump(self) -> dict:
        return {"id": self.id, "model": self.model, "usage": self.usage, "output_text": self.output_text}


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload; self.calls: list[dict] = []; self.responses = self

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self.payload)


class CompanyCalibrationRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root / "config").mkdir(parents=True); (self.root / "config" / "config.yaml").write_text("database:\n  path: db/test.sqlite\n", encoding="utf-8")
        (self.root / "config" / "calibration.yaml").write_text("\n".join(["package_builder_id: builder", "package_builder_version: v1", "default_sample_id: pilot_v0_2", "default_scoring_run_id: scoring", "default_max_package_tokens: 50000", "openai:", "  default_model: gpt-5.6-luna", "  default_reasoning_effort: low", "  pricing:", "    gpt-5.6-luna:", "      input_per_million: 0.2", "      cached_input_per_million: 0.02", "      output_per_million: 1.2"]), encoding="utf-8")
        prompt_dir = self.root / "src" / "ai_ecosystem" / "calibration" / "prompts"; prompt_dir.mkdir(parents=True)
        source_prompt = Path(__file__).parents[1] / "src" / "ai_ecosystem" / "calibration" / "prompts" / "company_ai_calibration_v1.txt"
        (prompt_dir / source_prompt.name).write_text(source_prompt.read_text(encoding="utf-8"), encoding="utf-8")
        self.db_path = init_database(self.root)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("INSERT INTO firms (bvd_id, firm_name, normalized_domain) VALUES ('AT1', 'Example', 'example.test')")
        package = self.root / "data" / "interim" / "calibration" / "company_evidence" / "package_test"; (package / "blind_inputs").mkdir(parents=True); (package / "analysis_metadata").mkdir()
        self.package = package
        payload = {"blind_to_scoring": True, "focal_company": {"company_id": "AT1", "company_name": "Example", "normalized_domain": "example.test", "shared_domain": False}, "evidence_availability": {"usable_page_count": 1}, "pages": [{"scraped_page_id": 1, "extracted_page_id": 1, "url": "https://example.test/ai", "normalized_url": "https://example.test/ai", "title": "AI", "headings": [], "page_type": "neutral", "language": "en", "word_count": 5, "extraction_version": "v1", "content_status": "complete", "content": "We develop machine learning models."}]}
        (package / "blind_inputs" / "AT1.json").write_text(json.dumps(payload), encoding="utf-8")
        (package / "blind_inputs" / "AT1.md").write_text("# Company Calibration Evidence\n\nWe develop machine learning models.\n", encoding="utf-8")
        (package / "analysis_metadata" / "AT1.json").write_text('{"final_score": 99999, "secret": "DO NOT SEND"}', encoding="utf-8")
        (package / "manifest.json").write_text(json.dumps({"sample_id": "pilot_v0_2", "blind_inputs_contain_no_scoring_information": True}), encoding="utf-8")

    def tearDown(self) -> None: self.tmp.cleanup()

    def test_prompt_contains_core_calibration_rules(self) -> None:
        _, _, prompt, _ = load_prompt(self.root)
        for phrase in ("Return KEEP", "Return EXCLUDE", "Return UNKNOWN", "Shared-domain", "customer", "lexical signal"):
            self.assertIn(phrase, prompt)

    def test_validates_and_persists_blind_result_with_lexical_export(self) -> None:
        client = FakeClient(output())
        summary = run_company_calibration(project_root=self.root, package_run_id="package_test", client=client, output_dir=Path("data/out"))
        self.assertEqual(summary["keep_count"], 1); self.assertEqual(summary["input_tokens"], 100)
        self.assertEqual(len(client.calls), 1)
        request_text = client.calls[0]["input"][1]["content"]
        self.assertNotIn("DO NOT SEND", request_text); self.assertNotIn("final_score", request_text)
        output_dir = self.root / "data" / "out"
        self.assertTrue((output_dir / "lexical_signals.csv").is_file())
        result = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))[0]
        self.assertTrue(result["grounding_verification"]["supporting_evidence"][0]["verified"])
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute("SELECT label, lexical_signals_json, raw_response_json FROM calibration_results").fetchone()
        self.assertEqual(row[0], "KEEP"); self.assertIn("machine learning", row[1]); self.assertIn("resp_test", row[2])

    def test_resume_skips_successful_company(self) -> None:
        client = FakeClient(output())
        summary = run_company_calibration(project_root=self.root, package_run_id="package_test", client=client, output_dir=Path("data/out"))
        resumed = run_company_calibration(project_root=self.root, package_run_id="package_test", client=client, output_dir=Path("data/out"), calibration_run_id=summary["calibration_run_id"], resume=True)
        self.assertEqual(resumed["completed_count"], 1); self.assertEqual(len(client.calls), 1)

    def test_invalid_label_is_rejected(self) -> None:
        bad = output("MAYBE")
        with self.assertRaises(ValueError): validate_model_output(json.dumps(bad))


if __name__ == "__main__": unittest.main()
