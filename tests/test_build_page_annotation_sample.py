from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ai_ecosystem.calibration.annotation_sample_config import (
    AnnotationSampleConfig,
    CalibrationSource,
    PageAllocation,
)
from ai_ecosystem.calibration.build_page_annotation_sample import (
    CompanyCandidate,
    PageCandidate,
    assign_domain_grouped_splits,
    build_audit,
    deduplicate_domain_pages,
    extend_to_target,
    select_companies,
    select_pages_for_company,
    write_artifacts,
)


def make_config() -> AnnotationSampleConfig:
    return AnnotationSampleConfig(
        sample_id="test_annotation_v1",
        sample_version="v1",
        sampling_seed=42,
        output_directory="unused",
        calibration_sources=(
            CalibrationSource("source_a", "cal_a", "score_a"),
            CalibrationSource("source_b", "cal_b", "score_b"),
        ),
        company_targets={"KEEP": 1, "EXCLUDE": 2, "UNKNOWN": 2},
        source_targets={
            "EXCLUDE": {"source_a": 1, "source_b": 1},
            "UNKNOWN": {"source_a": 1, "source_b": 1},
        },
        split_targets={
            "development": {"KEEP": 1, "EXCLUDE": 1, "UNKNOWN": 1},
            "model_selection": {"KEEP": 0, "EXCLUDE": 1, "UNKNOWN": 0},
            "locked_retrieval_test": {"KEEP": 0, "EXCLUDE": 0, "UNKNOWN": 1},
        },
        target_pages=20,
        minimum_pages=1,
        maximum_pages=100,
        absolute_company_cap=24,
        allocations=(
            PageAllocation(5, 5),
            PageAllocation(20, 12),
            PageAllocation(100, 16),
            PageAllocation(None, 20),
        ),
        pool_shares={
            "structural": 0.25,
            "coverage_stratified": 0.25,
            "deterministic_random": 0.20,
            "previous_calibration_evidence": 0.15,
            "scorer_v1_diagnostic": 0.15,
        },
        config_hash="test-hash",
    )


def company(company_id: str, label: str, source: str, domain: str | None = None) -> CompanyCandidate:
    return CompanyCandidate(
        company_id=company_id,
        company_name=f"Company {company_id}",
        domain=domain or f"{company_id.casefold()}.example",
        label=label,
        calibration_sample_id=source,
        calibration_run_id=f"cal_{source}",
        scoring_run_id=f"score_{source}",
        nace_code="J620",
        enrichment_stratum="HIGH_POTENTIAL" if source == "source_a" else None,
        language_group="de" if source == "source_a" else "en",
        site_size_band="21-100",
        raw_usable_pages=30,
        unique_content_pages=28,
        shared_domain=False,
        mapped_company_count=1,
        attribution_uncertain=False,
        third_party_issue=False,
        cross_host_issue=False,
        calibration_result_id=int("".join(character for character in company_id if character.isdigit()) or 1),
    )


def page(company_id: str, page_id: int, *, url: str | None = None, previous: bool = False) -> PageCandidate:
    scores = {
        "firm_profile": float(page_id % 7),
        "technology": float(page_id % 11),
        "innovation": float(page_id % 5),
        "ecosystem": float(page_id % 3),
    }
    url = url or f"https://{company_id.casefold()}.example/section/{page_id}"
    depth = len([part for part in url.split("?", 1)[0].split("/")[3:] if part])
    return PageCandidate(
        company_id=company_id,
        extracted_page_id=page_id + 1000,
        scraped_page_id=page_id,
        domain=f"{company_id.casefold()}.example",
        url=url,
        normalized_url=url,
        title=f"Page {page_id}",
        language="de",
        language_group="de",
        word_count=50 + page_id,
        page_type="content",
        url_depth=depth,
        path_family="section" if depth else "root",
        structural_kind="root" if depth == 0 else ("about_company" if "/about" in url else None),
        extraction_version="v1",
        markdown_path=f"page-{page_id}.md",
        text_path=f"page-{page_id}.txt",
        content_sha256=f"hash-{page_id}",
        duplicate_group_size=1,
        duplicate_scraped_page_ids=(),
        previous_evidence_references=("supporting_evidence",) if previous else (),
        scorer_v1_scores=scores,
    )


class BuildPageAnnotationSampleTests(unittest.TestCase):
    def test_company_selection_is_deterministic_and_source_balanced(self) -> None:
        config = make_config()
        candidates = [company("K1", "KEEP", "source_a")]
        for label in ("EXCLUDE", "UNKNOWN"):
            for source in ("source_a", "source_b"):
                candidates.extend(company(f"{label[0]}{source[-1]}{index}", label, source) for index in range(4))
        first = select_companies(candidates, config)
        second = select_companies(reversed(candidates), config)
        self.assertEqual([item.company_id for item in first], [item.company_id for item in second])
        self.assertEqual(len(first), 5)
        self.assertEqual(
            {(item.label, item.calibration_sample_id) for item in first if item.label != "KEEP"},
            {
                ("EXCLUDE", "source_a"), ("EXCLUDE", "source_b"),
                ("UNKNOWN", "source_a"), ("UNKNOWN", "source_b"),
            },
        )

    def test_domain_groups_never_cross_splits(self) -> None:
        config = replace(
            make_config(),
            company_targets={"KEEP": 3, "EXCLUDE": 3, "UNKNOWN": 3},
            split_targets={
                "development": {"KEEP": 1, "EXCLUDE": 1, "UNKNOWN": 1},
                "model_selection": {"KEEP": 1, "EXCLUDE": 1, "UNKNOWN": 1},
                "locked_retrieval_test": {"KEEP": 1, "EXCLUDE": 1, "UNKNOWN": 1},
            },
        )
        companies = [
            company(f"{label[0]}{index}", label, "source_a", "shared.example" if index == 0 else None)
            for label in ("KEEP", "EXCLUDE", "UNKNOWN")
            for index in range(3)
        ]
        splits = assign_domain_grouped_splits(companies, config)
        self.assertEqual(len({splits[item.company_id] for item in companies if item.domain == "shared.example"}), 1)
        self.assertEqual(splits, assign_domain_grouped_splits(reversed(companies), config))

    def test_page_allocation_caps_and_independence(self) -> None:
        config = make_config()
        focal = company("K1", "KEEP", "source_a")
        pages = [
            page("K1", 1, url="https://k1.example/"),
            page("K1", 2, url="https://k1.example/about", previous=True),
            *[page("K1", index, previous=index in {3, 4, 5}) for index in range(3, 43)],
        ]
        selected = select_pages_for_company(focal, pages, "development", config)
        self.assertEqual(len(selected), 16)
        self.assertLessEqual(len(selected), config.absolute_company_cap)
        self.assertLessEqual(sum(item.scorer_v1_diagnostic for item in selected) / len(selected), 0.15)
        self.assertGreaterEqual(sum(item.scorer_v1_independent for item in selected) / len(selected), 0.85)
        self.assertGreaterEqual(
            sum(item.independent_of_scorer_v1_and_previous_llm for item in selected) / len(selected),
            0.70,
        )

    def test_all_available_pages_do_not_become_scorer_selected(self) -> None:
        config = make_config()
        selected = select_pages_for_company(
            company("K1", "KEEP", "source_a"),
            [page("K1", index, previous=True) for index in range(1, 6)],
            "development",
            config,
        )
        self.assertEqual(len(selected), 5)
        self.assertTrue(all(item.sampling_pool == "all_available" for item in selected))
        self.assertTrue(all(item.scorer_v1_independent for item in selected))

    def test_target_extension_is_independent_and_respects_absolute_cap(self) -> None:
        config = replace(make_config(), target_pages=25)
        focal = company("K1", "KEEP", "source_a")
        pages = [page("K1", index) for index in range(1, 31)]
        selected = select_pages_for_company(focal, pages, "development", config)
        selected_by_company = {focal.company_id: selected}
        extend_to_target(
            selected_by_company,
            {focal.company_id: pages},
            {focal.company_id: focal},
            {focal.company_id: "development"},
            config,
        )
        self.assertEqual(len(selected_by_company[focal.company_id]), config.absolute_company_cap)
        extensions = [
            item for item in selected_by_company[focal.company_id]
            if item.selection_reason == "independent_target_extension"
        ]
        self.assertTrue(extensions)
        self.assertTrue(all(item.scorer_v1_independent for item in extensions))
        self.assertTrue(all(item.independent_of_scorer_v1_and_previous_llm for item in extensions))

    def test_exact_content_duplicates_use_structural_representative(self) -> None:
        rows = [
            {"scraped_page_id": 2, "url": "https://example.com/deep/copy", "normalized_url": "https://example.com/deep/copy", "content_sha256": "same"},
            {"scraped_page_id": 1, "url": "https://example.com/", "normalized_url": "https://example.com/", "content_sha256": "same"},
            {"scraped_page_id": 3, "url": "https://example.com/other", "normalized_url": "https://example.com/other", "content_sha256": "different"},
        ]
        result = deduplicate_domain_pages(rows)
        self.assertEqual([item["scraped_page_id"] for item in result], [1, 3])
        self.assertEqual(result[0]["duplicate_group_size"], 2)
        self.assertEqual(result[0]["duplicate_scraped_page_ids"], (2,))

    def test_artifact_rebuild_is_byte_deterministic_and_contains_only_selected_pages(self) -> None:
        config = make_config()
        companies = [company("K1", "KEEP", "source_a")]
        splits = {"K1": "development"}
        selected = select_pages_for_company(companies[0], [page("K1", index) for index in range(1, 6)], "development", config)
        audit = build_audit(companies, splits, selected, replace(config, company_targets={"KEEP": 1, "EXCLUDE": 0, "UNKNOWN": 0}))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.yaml"
            config_path.write_text("sample_id: test\n", encoding="utf-8")
            output = root / "artifacts"
            write_artifacts(output, config_path, config, companies, splits, selected, audit, False)
            first = {path.name: path.read_bytes() for path in output.iterdir()}
            write_artifacts(output, config_path, config, companies, splits, selected, audit, True)
            second = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual(first, second)
            records = [json.loads(line) for line in (output / "pages.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({record["company_id"] for record in records}, {"K1"})
            self.assertEqual({record["scraped_page_id"] for record in records}, {1, 2, 3, 4, 5})
            self.assertTrue(audit["validation"]["only_selected_companies"])
            self.assertTrue(audit["validation"]["only_v1_extractions"])


if __name__ == "__main__":
    unittest.main()
