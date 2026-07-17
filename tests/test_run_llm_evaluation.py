from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.scoring.run_llm_evaluation import (
    response_schema,
    run_llm_evaluation,
    validate_model_output,
)


VALID_OUTPUT = {
    "dimension": "firm_profile",
    "page_category": "company_profile",
    "confidence": 0.92,
    "score_justified": "yes",
    "retain_for_llm": "yes",
    "evidence_quality": 4,
    "company_relevance": 5,
    "page_type_quality": 4,
    "detected_issues": [
        {
            "issue_type": "generic_marketing",
            "severity": "minor",
            "explanation": "Some phrases are broad but supported by services evidence.",
        }
    ],
    "concise_summary": "Useful company page with concrete services.",
    "suggested_scoring_improvements": ["Reduce generic marketing boosts."],
    "suggested_retention_improvements": ["Retain pages with concrete services sections."],
    "suggested_knowledge_base_improvements": ["Add more service-specific negative context."],
    "suggested_page_type_heuristics": ["Prefer service pages over legal pages."],
}


class FakeResponse:
    def __init__(self, payload: dict, *, input_tokens: int = 100, output_tokens: int = 50) -> None:
        self.output_text = json.dumps(payload)
        self.usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class FakeResponses:
    def __init__(self, parent: "FakeClient") -> None:
        self.parent = parent

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        action = self.parent.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return FakeResponse(action)


class FakeClient:
    def __init__(self, actions: list[dict | Exception]) -> None:
        self.actions = list(actions)
        self.calls = []
        self.responses = FakeResponses(self)


def write_package(root: Path, *, page_count: int = 2) -> Path:
    evaluation_dir = root / "data" / "interim" / "scoring_evaluation" / "scoring_test"
    evaluation_dir.mkdir(parents=True)
    page_files = []
    for index in range(1, page_count + 1):
        dimension = "firm_profile" if index == 1 else "technology"
        filename = f"{dimension}_rank_{index:03d}_scraped_{index}.md"
        (evaluation_dir / filename).write_text(
            f"# {dimension} rank {index}\n\nURL: https://example.test/{index}\n\nUseful services evidence.",
            encoding="utf-8",
        )
        page_files.append(
            {
                "file": filename,
                "dimension": dimension,
                "page_score_id": index,
                "scraped_page_id": index,
                "extracted_page_id": index + 100,
                "domain": "example.test",
                "url": f"https://example.test/{index}",
                "final_score": 10.0 + index,
            }
        )
    (evaluation_dir / "evaluation_prompt.md").write_text("Review each page for scoring quality.", encoding="utf-8")
    (evaluation_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scoring_run_id": "scoring_test",
                "evaluation_prompt": "evaluation_prompt.md",
                "page_files": page_files,
            }
        ),
        encoding="utf-8",
    )
    return evaluation_dir


def write_config(root: Path) -> None:
    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "validation:",
                "  retry_count: 1",
                "  request_timeout_seconds: 60",
            ]
        ),
        encoding="utf-8",
    )


class RunLLMEvaluationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_config(self.root)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "project_status.md").write_text("# Status\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_and_output_validation(self) -> None:
        schema = response_schema()
        self.assertIn("detected_issues", schema["properties"])
        self.assertIn("suggested_retention_improvements", schema["required"])
        parsed = validate_model_output(json.dumps(VALID_OUTPUT))
        self.assertEqual(parsed["retain_for_llm"], "yes")
        invalid = dict(VALID_OUTPUT)
        invalid["page_category"] = "bad"
        with self.assertRaises(ValueError):
            validate_model_output(json.dumps(invalid))

    def test_successful_run_persists_pages_and_aggregate_reports(self) -> None:
        evaluation_dir = write_package(self.root, page_count=2)
        client = FakeClient([VALID_OUTPUT, {**VALID_OUTPUT, "dimension": "technology", "retain_for_llm": "no"}])

        summary = run_llm_evaluation(
            evaluation_dir=evaluation_dir,
            model="gpt-5",
            client=client,
            project_root=self.root,
            update_project_status=True,
        )

        output_dir = Path(summary["output_dir"])
        page_results = json.loads((output_dir / "page_evaluations.json").read_text(encoding="utf-8"))
        self.assertEqual(len(page_results), 2)
        self.assertEqual(summary["pages_evaluated"], 2)
        self.assertEqual(summary["failure_mode_counts"]["generic_marketing"], 2)
        self.assertEqual(summary["usage"]["total_tokens"], 300)
        self.assertGreater(summary["estimated_cost"]["total_cost_usd"], 0)
        self.assertTrue((output_dir / "page_evaluations.md").is_file())
        self.assertTrue((output_dir / "evaluation_summary.md").is_file())
        self.assertIn("Review each page", client.calls[0]["input"][0]["content"])
        self.assertIn("Useful services evidence", client.calls[0]["input"][1]["content"])
        self.assertIn("Milestone 4.5", (self.root / "docs" / "project_status.md").read_text(encoding="utf-8"))

    def test_resume_skips_successful_pages_and_retries_missing(self) -> None:
        evaluation_dir = write_package(self.root, page_count=2)
        output_dir = evaluation_dir / "llm_evaluation_gpt-5"
        output_dir.mkdir()
        existing = {
            "status": "success",
            "file": "firm_profile_rank_001_scraped_1.md",
            "index": 1,
            "dimension": "firm_profile",
            "page_score_id": 1,
            "scraped_page_id": 1,
            "extracted_page_id": 101,
            "domain": "example.test",
            "url": "https://example.test/1",
            "final_score": 11,
            "model": "gpt-5",
            "parsed": VALID_OUTPUT,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "estimated_cost": {"total_cost_usd": 0},
        }
        (output_dir / "page_evaluations.json").write_text(json.dumps([existing]), encoding="utf-8")
        client = FakeClient([{**VALID_OUTPUT, "dimension": "technology"}])

        summary = run_llm_evaluation(
            evaluation_dir=evaluation_dir,
            model="gpt-5",
            client=client,
            project_root=self.root,
            resume=True,
            update_project_status=False,
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(summary["pages_evaluated"], 2)
        page_results = json.loads((output_dir / "page_evaluations.json").read_text(encoding="utf-8"))
        self.assertEqual([item["file"] for item in page_results], ["firm_profile_rank_001_scraped_1.md", "technology_rank_002_scraped_2.md"])

    def test_failure_recovery_records_failed_page_without_aborting(self) -> None:
        evaluation_dir = write_package(self.root, page_count=2)
        client = FakeClient([RuntimeError("rate limit"), RuntimeError("still down"), VALID_OUTPUT])

        summary = run_llm_evaluation(
            evaluation_dir=evaluation_dir,
            model="gpt-5",
            client=client,
            project_root=self.root,
            update_project_status=False,
        )

        self.assertEqual(summary["pages_evaluated"], 1)
        self.assertEqual(summary["pages_failed"], 1)
        page_results = json.loads((Path(summary["output_dir"]) / "page_evaluations.json").read_text(encoding="utf-8"))
        self.assertEqual(page_results[0]["status"], "failed")
        self.assertEqual(page_results[1]["status"], "success")

    def test_max_pages_and_no_overwrite(self) -> None:
        evaluation_dir = write_package(self.root, page_count=2)
        client = FakeClient([VALID_OUTPUT])
        summary = run_llm_evaluation(
            evaluation_dir=evaluation_dir,
            model="gpt-5",
            client=client,
            project_root=self.root,
            max_pages=1,
            update_project_status=False,
        )
        self.assertEqual(summary["pages_attempted"], 1)
        with self.assertRaises(FileExistsError):
            run_llm_evaluation(
                evaluation_dir=evaluation_dir,
                model="gpt-5",
                client=FakeClient([]),
                project_root=self.root,
                update_project_status=False,
            )


if __name__ == "__main__":
    unittest.main()
