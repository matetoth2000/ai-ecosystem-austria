from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.analyze_page_annotation_quality import (
    analyze_annotation_quality,
    ground_annotation_phrases,
    hidden_repeat_metrics,
    logical_consistency,
    quadratic_weighted_kappa,
)
from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS, canonical_json


def make_annotation(
    scraped_page_id: int,
    *,
    utility: int = 2,
    priority: str = "P2_SUPPORTING",
    phrase: str = "machine-learning",
) -> dict:
    return {
        "scraped_page_id": scraped_page_id,
        "utility_score": utility,
        "attribution": "focal_company_direct",
        "page_category": "technology_capability",
        "evidence_facets": {
            facet: {
                "strength": 2 if facet == "technical_capability" else 0,
                "supporting_phrases": [phrase] if facet == "technical_capability" else [],
            }
            for facet in FACETS
        },
        "noise_flags": {flag: False for flag in NOISE_FLAGS},
        "page_priority_tier": priority,
        "rationale": "Visible technical evidence.",
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


class PageAnnotationQualityTest(unittest.TestCase):
    def test_normalized_grounding_reconstructs_context(self) -> None:
        annotation = make_annotation(1)
        rows = ground_annotation_phrases(
            annotation,
            {"company_id": "C1", "scraped_page_id": 1, "extracted_markdown": "We build machine learning systems."},
            "request",
            "C1:1",
        )
        self.assertFalse(rows[0]["exact_grounded"])
        self.assertTrue(rows[0]["normalized_grounded"])
        self.assertIn("machine learning", rows[0]["reconstructed_context"])

    def test_logical_contradictions_are_distinct_from_soft_checks(self) -> None:
        annotation = make_annotation(1, utility=0, priority="P1_CORE")
        hard, soft = logical_consistency(annotation)
        self.assertIn("zero_utility_with_core_priority", hard)
        self.assertIn("zero_utility_with_strong_evidence", hard)
        self.assertEqual(soft, [])

    def test_quadratic_weighted_kappa(self) -> None:
        self.assertEqual(quadratic_weighted_kappa([(0, 0), (1, 1), (2, 2), (3, 3)]), 1.0)
        self.assertLess(quadratic_weighted_kappa([(0, 3), (3, 0), (1, 2), (2, 1)]), 0)

    def test_hidden_repeat_metrics_tolerate_missing_or_failed_pairs(self) -> None:
        metrics, rows = hidden_repeat_metrics(
            {"primary": {"status": "success", "annotation": make_annotation(1)}},
            [
                {
                    "custom_id": "repeat",
                    "repeat_of_custom_id": "primary",
                    "annotation_item_id": "C1:1",
                    "scraped_page_id": 1,
                    "is_hidden_repeat": True,
                }
            ],
        )
        self.assertEqual(metrics["expected_repeat_pairs"], 1)
        self.assertEqual(metrics["available_repeat_pairs"], 0)
        self.assertFalse(rows[0]["pair_available"])

    def test_end_to_end_assigns_trust_and_preserves_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "data" / "runs" / "run1"
            sample_dir = root / "data" / "sample"
            prompt_dir = root / "src" / "ai_ecosystem" / "calibration" / "prompts"
            for path in (run_dir, sample_dir, prompt_dir, root / "config"):
                path.mkdir(parents=True, exist_ok=True)
            config_text = "\n".join(
                [
                    "annotation_id: scorer_v2_page_annotation",
                    "annotation_version: v1",
                    "sample_id: scorer_v2_page_annotation_v1",
                    "sample_directory: data/sample",
                    "output_root: data/runs",
                    "model: gpt-5.6-luna",
                    "reasoning_effort: medium",
                    "max_markdown_chars: 24000",
                    "hidden_repeat_fraction: 0.05",
                    "hidden_repeat_seed: 1",
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
            ) + "\n"
            (root / "config" / "page_annotation.yaml").write_text(config_text, encoding="utf-8")
            companies = [
                {
                    "company_id": "C1",
                    "label": "KEEP",
                    "site_size_band": "6-20",
                    "shared_domain": False,
                    "calibration_sample_id": "sample",
                }
            ]
            pages = [
                {"annotation_item_id": "C1:1", "company_id": "C1", "sampling_pool": "structural", "language_group": "en"},
                {"annotation_item_id": "C1:2", "company_id": "C1", "sampling_pool": "deterministic_random", "language_group": "en"},
            ]
            write_jsonl(sample_dir / "companies.jsonl", companies)
            write_jsonl(sample_dir / "pages.jsonl", pages)
            sample_manifest = {
                "sample_id": "scorer_v2_page_annotation_v1",
                "files": {"companies": {"path": "companies.jsonl"}, "pages": {"path": "pages.jsonl"}},
            }
            (sample_dir / "manifest.json").write_text(json.dumps(sample_manifest), encoding="utf-8")

            prompt = "Frozen prompt"
            schema = {"type": "object"}
            inputs = [
                {"custom_id": "p1", "company_id": "C1", "scraped_page_id": 1, "extracted_markdown": "machine learning"},
                {"custom_id": "p2", "company_id": "C1", "scraped_page_id": 2, "extracted_markdown": "technical"},
                {"custom_id": "r1", "company_id": "C1", "scraped_page_id": 1, "extracted_markdown": "machine learning"},
            ]
            index = [
                {"custom_id": "p1", "annotation_item_id": "C1:1", "annotation_rank": 1, "company_id": "C1", "scraped_page_id": 1, "is_hidden_repeat": False, "repeat_of_custom_id": None},
                {"custom_id": "p2", "annotation_item_id": "C1:2", "annotation_rank": 2, "company_id": "C1", "scraped_page_id": 2, "is_hidden_repeat": False, "repeat_of_custom_id": None},
                {"custom_id": "r1", "annotation_item_id": "C1:1", "annotation_rank": 1, "company_id": "C1", "scraped_page_id": 1, "is_hidden_repeat": True, "repeat_of_custom_id": "p1"},
            ]
            for row, model_input in zip(index, inputs):
                clean = {key: value for key, value in model_input.items() if key != "custom_id"}
                row["model_input_sha256"] = hashlib.sha256(canonical_json(clean).encode()).hexdigest()
            annotations = [
                {"custom_id": "p1", "status": "success", "annotation": make_annotation(1)},
                {"custom_id": "p2", "status": "success", "annotation": make_annotation(2, utility=0, priority="P1_CORE", phrase="technical")},
                {"custom_id": "r1", "status": "success", "annotation": make_annotation(1)},
            ]
            batch_lines = [
                {
                    "custom_id": row["custom_id"],
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": {"input": [{"role": "system", "content": prompt}], "text": {"format": {"schema": schema}}},
                }
                for row in index
            ]
            write_jsonl(run_dir / "request_index.jsonl", index)
            write_jsonl(run_dir / "model_inputs.jsonl", inputs)
            write_jsonl(run_dir / "annotations.jsonl", annotations)
            write_jsonl(run_dir / "batch_input.jsonl", batch_lines)
            (run_dir / "prompt_snapshot.txt").write_text(prompt + "\n", encoding="utf-8")
            (run_dir / "schema_snapshot.json").write_text(json.dumps(schema), encoding="utf-8")
            batch_hash = hashlib.sha256((run_dir / "batch_input.jsonl").read_bytes()).hexdigest()
            config_raw = __import__("yaml").safe_load(config_text)
            config_hash = hashlib.sha256(canonical_json(config_raw).encode()).hexdigest()
            metadata = {
                "status": "ingested",
                "batch_input_sha256": batch_hash,
                "sample_manifest_sha256": hashlib.sha256(canonical_json(sample_manifest).encode()).hexdigest(),
                "schema_sha256": hashlib.sha256(canonical_json(schema).encode()).hexdigest(),
                "config_sha256": config_hash,
            }
            (run_dir / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

            original = (run_dir / "annotations.jsonl").read_bytes()
            summary = analyze_annotation_quality(project_root=root, run_id="run1")
            self.assertEqual((run_dir / "annotations.jsonl").read_bytes(), original)
            self.assertEqual(summary["counts"]["trusted_primary_annotations"], 1)
            self.assertEqual(summary["counts"]["untrusted_primary_annotations"], 1)
            self.assertEqual(summary["hidden_repeat_consistency"]["utility_weighted_kappa"], 1.0)
            self.assertTrue((run_dir / "qa_v2" / "grounded_phrases.csv").is_file())
            with self.assertRaises(FileExistsError):
                analyze_annotation_quality(project_root=root, run_id="run1")


if __name__ == "__main__":
    unittest.main()
