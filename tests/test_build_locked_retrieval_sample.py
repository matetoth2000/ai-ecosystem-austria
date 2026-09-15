from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.calibration.build_locked_retrieval_sample import build_locked_sample
from ai_ecosystem.calibration.build_page_annotation_sample import PageCandidate


class FakeConnection:
    row_factory = None

    def execute(self, query, params):
        class Result:
            @staticmethod
            def fetchone():
                return {"id": 7, "company_id": "C1"}

        return Result()


def company() -> dict:
    return {
        "attribution_uncertain": False,
        "calibration_result_id": 7,
        "calibration_run_id": "calibration_1",
        "calibration_sample_id": "sample_1",
        "company_id": "C1",
        "company_name": "Example GmbH",
        "company_rank": 1,
        "cross_host_issue": False,
        "domain": "example.test",
        "enrichment_stratum": "HIGH_POTENTIAL",
        "label": "KEEP",
        "language_group": "en",
        "mapped_company_count": 1,
        "nace_code": "J620",
        "raw_usable_pages": 3,
        "scoring_run_id": "scoring_1",
        "shared_domain": False,
        "site_size_band": "1-5",
        "split": "locked_retrieval_test",
        "third_party_issue": False,
        "unique_content_pages": 2,
    }


def page(scraped_page_id: int, content_hash: str) -> PageCandidate:
    return PageCandidate(
        company_id="C1",
        extracted_page_id=scraped_page_id + 100,
        scraped_page_id=scraped_page_id,
        domain="example.test",
        url=f"https://example.test/{scraped_page_id}",
        normalized_url=f"https://example.test/{scraped_page_id}",
        title=f"Page {scraped_page_id}",
        language="en",
        language_group="en",
        word_count=10,
        page_type=None,
        url_depth=1,
        path_family=str(scraped_page_id),
        structural_kind="shallow",
        extraction_version="v1",
        markdown_path=f"data/{scraped_page_id}.md",
        text_path=f"data/{scraped_page_id}.txt",
        content_sha256=content_hash,
        duplicate_group_size=2 if scraped_page_id == 1 else 1,
        duplicate_scraped_page_ids=(3,) if scraped_page_id == 1 else (),
        previous_evidence_references=(),
        scorer_v1_scores={"technology": float(scraped_page_id)},
    )


class BuildLockedRetrievalSampleTest(unittest.TestCase):
    def test_builds_all_exact_deduplicated_pages_for_frozen_locked_companies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "data/interim/calibration/page_annotation_samples/scorer_v2_page_annotation_v1"
            source.mkdir(parents=True)
            (source / "companies.jsonl").write_text(json.dumps(company()) + "\n", encoding="utf-8")
            (source / "manifest.json").write_text(
                json.dumps({"sample_id": "source", "files": {"companies": {"path": "companies.jsonl"}}}),
                encoding="utf-8",
            )
            output = root / "locked"
            rows = [{"scraped_page_id": 1}, {"scraped_page_id": 2}, {"scraped_page_id": 3}]
            with (
                patch(
                    "ai_ecosystem.calibration.build_locked_retrieval_sample.load_usable_pages_by_domain",
                    return_value={"example.test": rows},
                ),
                patch(
                    "ai_ecosystem.calibration.build_locked_retrieval_sample.build_page_candidates",
                    return_value=[page(1, "a"), page(2, "b")],
                ),
            ):
                audit = build_locked_sample(
                    FakeConnection(),
                    root,
                    output,
                    expected_company_count=1,
                    expected_page_count=2,
                )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            pages = [json.loads(line) for line in (output / "pages.jsonl").read_text().splitlines()]
            self.assertEqual(audit["duplicate_pages_removed"], 1)
            self.assertEqual(manifest["page_annotation_item_count"], 2)
            self.assertEqual([row["scraped_page_id"] for row in pages], [1, 2])
            self.assertEqual([row["annotation_rank"] for row in pages], [1, 2])
            self.assertTrue(all(row["sampling_pool"] == "locked_full_corpus" for row in pages))
            self.assertTrue(all(row["split"] == "locked_retrieval_test" for row in pages))

            with self.assertRaises(FileExistsError):
                build_locked_sample(
                    FakeConnection(),
                    root,
                    output,
                    expected_company_count=1,
                    expected_page_count=2,
                )


if __name__ == "__main__":
    unittest.main()
