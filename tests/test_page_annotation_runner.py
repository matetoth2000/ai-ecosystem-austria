from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai_ecosystem.calibration.page_annotation_contract import (
    FACETS,
    NOISE_FLAGS,
    annotation_schema,
    build_request_records,
    hidden_repeat_item_ids,
    load_annotation_items,
    load_page_annotation_config,
    load_prompt,
    parse_annotation,
    validate_batch_lines,
    validate_model_input,
)
from ai_ecosystem.calibration.run_page_annotation import (
    build_batch,
    ingest_batch,
    retry_failed_requests,
    run_sync,
)


def annotation(scraped_page_id: int) -> dict:
    facets = {
        name: {
            "strength": 2 if name == "technical_capability" else 0,
            "supporting_phrases": ["machine learning systems"] if name == "technical_capability" else [],
        }
        for name in FACETS
    }
    return {
        "scraped_page_id": scraped_page_id,
        "utility_score": 2,
        "attribution": "focal_company_direct",
        "page_category": "technology_capability",
        "evidence_facets": facets,
        "noise_flags": {name: False for name in NOISE_FLAGS},
        "page_priority_tier": "P2_SUPPORTING",
        "rationale": "The page describes machine learning systems.",
    }


def raw_response(scraped_page_id: int, response_id: str = "resp_test") -> dict:
    output_text = json.dumps(annotation(scraped_page_id))
    return {
        "id": response_id,
        "output": [{"content": [{"type": "output_text", "text": output_text}]}],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 25,
            "total_tokens": 125,
        },
    }


class FakeResponse:
    def __init__(self, scraped_page_id: int) -> None:
        self.payload = raw_response(scraped_page_id)
        self.id = self.payload["id"]
        self.output_text = self.payload["output"][0]["content"][0]["text"]
        self.usage = self.payload["usage"]

    def model_dump(self, mode: str = "json") -> dict:
        return self.payload


class FakeSyncClient:
    def __init__(self, fail_first: bool = False) -> None:
        self.responses = self
        self.fail_first = fail_first
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(dict(kwargs))
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("transient")
        payload = json.loads(kwargs["input"][1]["content"])
        return FakeResponse(payload["scraped_page_id"])


class FakeBatchClient:
    def __init__(self, output_text: str, batch: dict) -> None:
        self.output_text = output_text
        self.batch = batch
        self.batches = SimpleNamespace(retrieve=lambda batch_id: self.batch)
        self.files = SimpleNamespace(content=self._content)

    def _content(self, file_id: str) -> object:
        value = self.output_text if file_id == "file_output" else ""
        return SimpleNamespace(text=value)


class PageAnnotationRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir(parents=True)
        (self.root / "src" / "ai_ecosystem" / "calibration" / "prompts").mkdir(parents=True)
        (self.root / "data" / "sample").mkdir(parents=True)
        (self.root / "data" / "pages").mkdir(parents=True)
        (self.root / "db").mkdir()
        (self.root / "config" / "config.yaml").write_text("database:\n  path: db/test.sqlite\n", encoding="utf-8")
        (self.root / "config" / "page_annotation.yaml").write_text(
            "\n".join(
                [
                    "annotation_id: scorer_v2_page_annotation",
                    "annotation_version: v1",
                    "sample_id: scorer_v2_page_annotation_v1",
                    "sample_directory: data/sample",
                    "output_root: data/runs",
                    "model: gpt-5.6-luna",
                    "reasoning_effort: medium",
                    "max_markdown_chars: 24000",
                    "hidden_repeat_fraction: 0.5",
                    "hidden_repeat_seed: 7",
                    "completion_window: 24h",
                    "retry_count: 2",
                    "request_timeout_seconds: 30",
                    "pricing_version: test",
                    "pricing:",
                    "  input_per_million: 0.2",
                    "  cached_input_per_million: 0.02",
                    "  output_per_million: 1.2",
                    "  batch_discount: 0.5",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        source_prompt = Path(__file__).parents[1] / "src" / "ai_ecosystem" / "calibration" / "prompts" / "page_annotation_v1.txt"
        (self.root / "src" / "ai_ecosystem" / "calibration" / "prompts" / source_prompt.name).write_text(
            source_prompt.read_text(encoding="utf-8"), encoding="utf-8"
        )
        markdowns = ["machine learning systems", "x" * 25_000]
        for index, text in enumerate(markdowns, start=1):
            (self.root / "data" / "pages" / f"{index}.md").write_text(text, encoding="utf-8")
        companies = [
            {
                "company_id": "C1",
                "company_name": "Example AI GmbH",
                "domain": "example.test",
                "label": "KEEP",
                "split": "development",
            }
        ]
        pages = [
            {
                "annotation_item_id": f"C1:{index}",
                "annotation_rank": index,
                "company_id": "C1",
                "extracted_page_id": index,
                "scraped_page_id": index,
                "markdown_path": f"data/pages/{index}.md",
                "normalized_url": f"https://example.test/{index}",
                "url": f"https://example.test/{index}",
                "title": f"Page {index}",
                "sampling_pool": "scorer_v1_diagnostic",
                "selection_reason": "secret",
                "scorer_v1_scores": {"technology": 999},
                "scorer_v1_diagnostic": True,
            }
            for index in (1, 2)
        ]
        (self.root / "data" / "sample" / "companies.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in companies), encoding="utf-8"
        )
        (self.root / "data" / "sample" / "pages.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in pages), encoding="utf-8"
        )
        manifest = {
            "sample_id": "scorer_v2_page_annotation_v1",
            "page_annotation_item_count": 2,
            "files": {"companies": {"path": "companies.jsonl"}, "pages": {"path": "pages.jsonl"}},
        }
        (self.root / "data" / "sample" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with sqlite3.connect(self.root / "db" / "test.sqlite") as connection:
            connection.execute("CREATE TABLE page_features (extracted_page_id INTEGER PRIMARY KEY, headings TEXT, heading_hierarchy TEXT)")
            connection.executemany(
                "INSERT INTO page_features VALUES (?, ?, ?)",
                [(1, json.dumps(["Overview"]), None), (2, None, json.dumps([{"level": 2, "text": "Details"}]))],
            )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_and_exact_identifier_validation(self) -> None:
        schema = annotation_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]["evidence_facets"]["required"]), set(FACETS))
        self.assertEqual(parse_annotation(json.dumps(annotation(1)), 1)["scraped_page_id"], 1)
        with self.assertRaisesRegex(ValueError, "expected 2"):
            parse_annotation(json.dumps(annotation(1)), 2)

    def test_inputs_are_leakage_free_and_deterministically_truncated(self) -> None:
        config = load_page_annotation_config(self.root)
        _, items = load_annotation_items(self.root, config)
        first, second = items
        self.assertEqual(set(first.model_input), {
            "scraped_page_id", "company_id", "company_name", "normalized_company_domain",
            "page_url", "title", "headings", "extracted_markdown", "markdown_truncated",
        })
        serialized = json.dumps(first.model_input)
        for forbidden in ("KEEP", "development", "scorer_v1", "sampling_pool", "secret", "999"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(first.model_input["headings"], [{"level": None, "text": "Overview"}])
        self.assertEqual(len(second.model_input["extracted_markdown"]), 24_000)
        self.assertTrue(second.model_input["markdown_truncated"])
        with self.assertRaises(ValueError):
            validate_model_input({**first.model_input, "label": "KEEP"})

    def test_batch_jsonl_and_hidden_repeats_are_deterministic(self) -> None:
        config = load_page_annotation_config(self.root)
        _, items = load_annotation_items(self.root, config)
        _, _, prompt, _ = load_prompt(self.root)
        first_lines, first_index = build_request_records(items, prompt, config)
        second_lines, second_index = build_request_records(items, prompt, config)
        self.assertEqual(first_lines, second_lines)
        self.assertEqual(first_index, second_index)
        self.assertEqual(len(hidden_repeat_item_ids(items, config)), 1)
        self.assertEqual(len(first_lines), 3)
        self.assertEqual(len({row["custom_id"] for row in first_lines}), 3)
        validate_batch_lines(first_lines, first_index)
        repeated = [row for row in first_index if row["is_hidden_repeat"]][0]
        primary_id = repeated["repeat_of_custom_id"]
        bodies = {row["custom_id"]: row["body"] for row in first_lines}
        self.assertEqual(bodies[repeated["custom_id"]], bodies[primary_id])

    def test_build_batch_writes_validated_snapshots_without_submission(self) -> None:
        summary = build_batch(self.root, "batch_test")
        run_dir = self.root / "data" / "runs" / "batch_test"
        self.assertEqual(summary["requests"], 3)
        self.assertTrue((run_dir / "batch_input.jsonl").is_file())
        self.assertTrue((run_dir / "prompt_snapshot.txt").is_file())
        self.assertTrue((run_dir / "schema_snapshot.json").is_file())
        self.assertTrue((run_dir / "config_snapshot.yaml").is_file())
        self.assertFalse((run_dir / "openai_batch.json").exists())
        metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(len(metadata["model_configuration_sha256"]), 64)
        with self.assertRaises(FileExistsError):
            build_batch(self.root, "batch_test")
        with self.assertRaises(ValueError):
            build_batch(self.root, "../unsafe")

    def test_build_batch_accepts_an_explicit_annotation_config(self) -> None:
        alternate = self.root / "config" / "alternate.yaml"
        alternate.write_text(
            (self.root / "config" / "page_annotation.yaml")
            .read_text(encoding="utf-8")
            .replace("sample_id: scorer_v2_page_annotation_v1", "sample_id: alternate_sample")
            .replace("sample_directory: data/sample", "sample_directory: data/alternate"),
            encoding="utf-8",
        )
        alternate_sample = self.root / "data" / "alternate"
        alternate_sample.mkdir()
        for name in ("companies.jsonl", "pages.jsonl"):
            (alternate_sample / name).write_text(
                (self.root / "data" / "sample" / name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        manifest = json.loads((self.root / "data" / "sample" / "manifest.json").read_text())
        manifest["sample_id"] = "alternate_sample"
        (alternate_sample / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        summary = build_batch(self.root, "alternate_batch", alternate)

        self.assertEqual(summary["annotation_items"], 2)
        snapshot = self.root / "data" / "runs" / "alternate_batch" / "config_snapshot.yaml"
        self.assertEqual(snapshot.read_text(encoding="utf-8"), alternate.read_text(encoding="utf-8"))

    def test_sync_retries_persists_raw_and_resume_skips_successes(self) -> None:
        client = FakeSyncClient(fail_first=True)
        summary = run_sync(
            project_root=self.root,
            run_id="sync_test",
            max_items=1,
            client=client,
            sleep=lambda _: None,
        )
        self.assertEqual(summary["successful_requests"], 1)
        self.assertEqual(len(client.calls), 2)
        run_dir = self.root / "data" / "runs" / "sync_test"
        self.assertTrue(next((run_dir / "raw_responses").glob("*.json")).is_file())
        resumed = run_sync(
            project_root=self.root,
            run_id="sync_test",
            max_items=1,
            resume=True,
            client=client,
            sleep=lambda _: None,
        )
        self.assertEqual(resumed["successful_requests"], 1)
        self.assertEqual(len(client.calls), 2)
        usage = json.loads((run_dir / "usage_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(usage["cached_input_tokens"], 20)
        self.assertGreater(usage["estimated_cost_usd"], 0)
        result = json.loads((run_dir / "annotations.jsonl").read_text(encoding="utf-8"))
        self.assertTrue(result["phrase_grounding"][0]["exact_match"])

    def test_batch_ingestion_maps_results_and_is_idempotent(self) -> None:
        build_batch(self.root, "batch_ingest")
        run_dir = self.root / "data" / "runs" / "batch_ingest"
        index = [json.loads(line) for line in (run_dir / "request_index.jsonl").read_text(encoding="utf-8").splitlines()]
        outputs = []
        for row in index:
            outputs.append(
                {
                    "id": "batch_req",
                    "custom_id": row["custom_id"],
                    "response": {"status_code": 200, "request_id": "req", "body": raw_response(row["scraped_page_id"])},
                    "error": None,
                }
            )
        output_text = "".join(json.dumps(row) + "\n" for row in reversed(outputs))
        batch = {
            "id": "batch_test",
            "status": "completed",
            "output_file_id": "file_output",
            "error_file_id": None,
            "request_counts": {"total": 3, "completed": 3, "failed": 0},
        }
        (run_dir / "openai_batch.json").write_text(json.dumps(batch), encoding="utf-8")
        client = FakeBatchClient(output_text, batch)
        first = ingest_batch(self.root, "batch_ingest", client=client)
        before = (run_dir / "annotations.jsonl").read_bytes()
        second = ingest_batch(self.root, "batch_ingest", client=client)
        after = (run_dir / "annotations.jsonl").read_bytes()
        self.assertEqual(first["successful_requests"], 3)
        self.assertEqual(second["successful_requests"], 3)
        self.assertEqual(before, after)
        records = [json.loads(line) for line in after.decode().splitlines()]
        self.assertEqual([row["custom_id"] for row in records], [row["custom_id"] for row in index])
        self.assertTrue((run_dir / "raw_batch_results.jsonl").is_file())

    def test_failed_batch_request_can_be_retried_without_losing_batch_usage(self) -> None:
        build_batch(self.root, "batch_retry")
        run_dir = self.root / "data" / "runs" / "batch_retry"
        index = [json.loads(line) for line in (run_dir / "request_index.jsonl").read_text().splitlines()]
        outputs = []
        for position, row in enumerate(index):
            response = raw_response(row["scraped_page_id"])
            if position == 0:
                invalid = annotation(row["scraped_page_id"])
                invalid["scraped_page_id"] += 1000
                response["output"][0]["content"][0]["text"] = json.dumps(invalid)
            outputs.append(
                {
                    "id": "batch_req",
                    "custom_id": row["custom_id"],
                    "response": {"status_code": 200, "request_id": "req", "body": response},
                    "error": None,
                }
            )
        batch = {
            "id": "batch_test",
            "status": "completed",
            "output_file_id": "file_output",
            "error_file_id": None,
            "request_counts": {"total": 3, "completed": 3, "failed": 0},
        }
        (run_dir / "openai_batch.json").write_text(json.dumps(batch), encoding="utf-8")
        ingest_batch(
            self.root,
            "batch_retry",
            client=FakeBatchClient("".join(json.dumps(row) + "\n" for row in outputs), batch),
        )

        summary = retry_failed_requests(
            self.root,
            "batch_retry",
            client=FakeSyncClient(),
            sleep=lambda _: None,
        )

        self.assertEqual(summary["retried_requests"], 1)
        self.assertEqual(summary["failed_requests"], 0)
        self.assertEqual(summary["batch_usage"]["input_tokens"], 200)
        self.assertEqual(summary["synchronous_retry_usage"]["input_tokens"], 100)
        self.assertEqual((run_dir / "failures.jsonl").read_text(), "")
        records = [json.loads(line) for line in (run_dir / "annotations.jsonl").read_text().splitlines()]
        self.assertEqual(
            sum(row.get("execution_mode") == "synchronous_failed_request_retry" for row in records),
            1,
        )
        self.assertTrue(next((run_dir / "retry_raw_responses").glob("*.json")).is_file())


if __name__ == "__main__":
    unittest.main()
