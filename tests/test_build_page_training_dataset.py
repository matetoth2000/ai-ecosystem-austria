from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_ecosystem.calibration.build_page_training_dataset import (
    FORBIDDEN_PREDICTIVE_COLUMNS,
    PageTrainingDatasetConfig,
    _restricted_targets,
    build_dataset,
    canonical_json,
)
from ai_ecosystem.calibration.page_annotation_contract import FACETS, NOISE_FLAGS


def annotation(page_id: int) -> dict:
    return {
        "scraped_page_id": page_id,
        "utility_score": 2,
        "attribution": "focal_company_direct",
        "page_category": "technology_capability",
        "evidence_facets": {
            facet: {
                "strength": 2 if facet == "technical_capability" else 0,
                "supporting_phrases": ["machine learning"] if facet == "technical_capability" else [],
            }
            for facet in FACETS
        },
        "noise_flags": {flag: False for flag in NOISE_FLAGS},
        "page_priority_tier": "P1_CORE",
        "rationale": "Technical evidence.",
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


class PageTrainingDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sample_dir = self.root / "sample"
        self.run_dir = self.root / "run"
        self.qa_dir = self.run_dir / "qa_v2"
        self.sample_dir.mkdir()
        self.qa_dir.mkdir(parents=True)
        self.config_path = self.root / "dataset.yaml"
        self.config_path.write_text("dataset_id: test\n", encoding="utf-8")
        self.config = PageTrainingDatasetConfig(
            dataset_id="test_dataset",
            dataset_version="v1",
            annotation_sample_id="sample_v1",
            annotation_sample_directory="sample",
            annotation_run_id="run_v1",
            annotation_run_directory="run",
            qa_version="qa_v2",
            output_directory="output",
            required_extraction_version="v1",
            exclude_cross_split_exact_duplicates_from_modeling=True,
            config_hash="config-hash",
        )
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self._create_database()
        self._create_artifacts()

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def _create_database(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE scraped_pages (
                scraped_page_id INTEGER PRIMARY KEY, url TEXT, final_url TEXT,
                domain TEXT, content_length INTEGER, http_status INTEGER,
                title TEXT, language TEXT
            );
            CREATE TABLE extracted_pages (
                extracted_page_id INTEGER PRIMARY KEY, scraped_page_id INTEGER,
                extraction_version TEXT, extraction_status TEXT, content_sha256 TEXT,
                markdown_path TEXT, text_path TEXT, language TEXT
            );
            CREATE TABLE page_features (
                extracted_page_id INTEGER PRIMARY KEY, title TEXT, language TEXT,
                word_count INTEGER, heading_count INTEGER, headings TEXT,
                heading_hierarchy TEXT, paragraph_count INTEGER,
                page_type_guess TEXT, feature_version TEXT
            );
            CREATE TABLE page_links (
                id INTEGER PRIMARY KEY, extracted_page_id INTEGER, source_url TEXT,
                target_url TEXT, normalized_target_url TEXT, target_domain TEXT,
                normalized_target_domain TEXT, anchor_text TEXT, anchor_word_count INTEGER,
                rel_attribute TEXT, internal_external TEXT, is_internal INTEGER,
                link_order INTEGER
            );
            CREATE TABLE page_scores (
                page_score_id INTEGER PRIMARY KEY, extracted_page_id INTEGER,
                scraped_page_id INTEGER, scoring_run_id TEXT, dimension TEXT,
                query_score REAL, keyword_score REAL, boost_score REAL,
                negative_score REAL, final_score REAL
            );
            """
        )
        for page_id, domain, content_hash in ((1, "a.test", "same"), (2, "b.test", "unique"), (3, "c.test", "same")):
            self.connection.execute(
                "INSERT INTO scraped_pages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (page_id, f"https://{domain}/about?p=1", f"https://{domain}/about?p=1", domain, 1000, 200, f"Title {page_id}", "en"),
            )
            self.connection.execute(
                "INSERT INTO extracted_pages VALUES (?, ?, 'v1', 'success', ?, ?, ?, 'en')",
                (page_id + 10, page_id, content_hash, f"page-{page_id}.md", f"page-{page_id}.txt"),
            )
            self.connection.execute(
                "INSERT INTO page_features VALUES (?, ?, 'en', 20, 1, ?, ?, 2, 'technology', 'v1')",
                (page_id + 10, f"Title {page_id}", '["Heading"]', '[{"level":1,"text":"Heading"}]'),
            )
            self.connection.execute(
                "INSERT INTO page_links VALUES (?, ?, '', '', '', 'outside.test', 'outside.test', 'Partner', 1, 'nofollow', 'external', 0, 1)",
                (page_id, page_id + 10),
            )
            for dimension in ("firm_profile", "technology", "innovation", "ecosystem"):
                self.connection.execute(
                    "INSERT INTO page_scores (extracted_page_id, scraped_page_id, scoring_run_id, dimension, query_score, keyword_score, boost_score, negative_score, final_score) VALUES (?, ?, 'score1', ?, 1, 2, 3, 4, 5)",
                    (page_id + 10, page_id, dimension),
                )
        self.connection.commit()

    def _create_artifacts(self) -> None:
        companies = []
        pages = []
        annotations = []
        model_inputs = []
        qa_rows = []
        phrases = []
        splits = ("development", "model_selection", "locked_retrieval_test")
        trusts = ("trusted", "partially_trusted", "untrusted")
        for page_id, (split, trust) in enumerate(zip(splits, trusts), 1):
            company_id = f"C{page_id}"
            item_id = f"{company_id}:{page_id}"
            custom_id = f"request-{page_id}"
            companies.append(
                {
                    "company_id": company_id,
                    "label": ("KEEP", "EXCLUDE", "UNKNOWN")[page_id - 1],
                    "domain": f"{chr(96 + page_id)}.test",
                    "split": split,
                    "shared_domain": False,
                    "mapped_company_count": 1,
                    "attribution_uncertain": False,
                    "calibration_sample_id": "calibration",
                    "raw_usable_pages": 3,
                    "unique_content_pages": 3,
                    "scoring_run_id": "score1",
                }
            )
            pages.append(
                {
                    "annotation_item_id": item_id,
                    "annotation_rank": page_id,
                    "company_id": company_id,
                    "scraped_page_id": page_id,
                    "extracted_page_id": page_id + 10,
                    "domain": f"{chr(96 + page_id)}.test",
                    "normalized_url": f"https://{chr(96 + page_id)}.test/about?p=1",
                    "title": f"Title {page_id}",
                    "language": "en",
                    "page_type": "technology",
                    "content_sha256": "same" if page_id in {1, 3} else "unique",
                    "extraction_version": "v1",
                    "split": split,
                    "sampling_pool": "structural",
                    "selection_reason": "test",
                    "scorer_v1_diagnostic": page_id == 2,
                    "duplicate_group_size": 1,
                }
            )
            annotations.append(
                {
                    "annotation_item_id": item_id,
                    "company_id": company_id,
                    "scraped_page_id": page_id,
                    "custom_id": custom_id,
                    "is_hidden_repeat": False,
                    "status": "success",
                    "annotation": annotation(page_id),
                }
            )
            model_inputs.append(
                {
                    "custom_id": custom_id,
                    "scraped_page_id": page_id,
                    "company_id": company_id,
                    "title": f"Title {page_id}",
                    "headings": ["Heading"],
                    "extracted_markdown": "We provide machine learning systems.\n\n- Service",
                    "markdown_truncated": False,
                }
            )
            reasons = []
            ungrounded = []
            if trust == "partially_trusted":
                reasons = ["positive_facet_without_grounded_phrase"]
                ungrounded = ["technical_capability"]
            elif trust == "untrusted":
                reasons = ["logical_contradiction:high_utility_with_noise_priority"]
            qa_rows.append(
                {
                    "custom_id": custom_id,
                    "annotation_item_id": item_id,
                    "company_id": company_id,
                    "scraped_page_id": page_id,
                    "is_hidden_repeat": False,
                    "trust_flag": trust,
                    "trust_reason_codes": canonical_json(reasons),
                    "lexical_restriction_reason_codes": "[]",
                    "ungrounded_positive_facets": canonical_json(ungrounded),
                    "soft_inconsistencies": "[]",
                }
            )
            phrases.append(
                {
                    "custom_id": custom_id,
                    "annotation_item_id": item_id,
                    "company_id": company_id,
                    "scraped_page_id": page_id,
                    "facet": "technical_capability",
                    "facet_strength": 2,
                    "phrase_index": 1,
                    "phrase": "machine learning",
                    "exact_grounded": True,
                    "casefold_grounded": True,
                    "normalized_grounded": True,
                    "lexical_supervision_trusted": True,
                    "reconstructed_context": "provide machine learning systems",
                }
            )
        write_jsonl(self.sample_dir / "companies.jsonl", companies)
        write_jsonl(self.sample_dir / "pages.jsonl", pages)
        (self.sample_dir / "manifest.json").write_text(json.dumps({"sample_id": "sample_v1"}), encoding="utf-8")
        (self.sample_dir / "config_snapshot.yaml").write_text("sample_id: sample_v1\n", encoding="utf-8")
        (self.run_dir / "run_metadata.json").write_text(
            json.dumps({"run_id": "run_v1", "sample_id": "sample_v1", "status": "ingested"}),
            encoding="utf-8",
        )
        (self.run_dir / "prompt_snapshot.txt").write_text("prompt", encoding="utf-8")
        (self.run_dir / "schema_snapshot.json").write_text("{}\n", encoding="utf-8")
        write_jsonl(self.run_dir / "annotations.jsonl", annotations)
        write_jsonl(self.run_dir / "model_inputs.jsonl", model_inputs)
        for path, rows in ((self.qa_dir / "annotation_qa.csv", qa_rows), (self.qa_dir / "grounded_phrases.csv", phrases)):
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        (self.qa_dir / "qa_summary.json").write_text("{}\n", encoding="utf-8")

    def test_field_level_target_restrictions_do_not_become_zero(self) -> None:
        partial = _restricted_targets(
            annotation(1),
            {
                "annotation_item_id": "C1:1",
                "company_id": "C1",
                "scraped_page_id": "1",
                "trust_flag": "partially_trusted",
                "trust_reason_codes": '["positive_facet_without_grounded_phrase"]',
                "lexical_restriction_reason_codes": "[]",
                "ungrounded_positive_facets": '["technical_capability"]',
                "soft_inconsistencies": "[]",
            },
        )
        self.assertIsNone(partial["facet__technical_capability__strength"])
        self.assertFalse(partial["facet__technical_capability__available"])
        self.assertEqual(partial["utility_score"], 2)
        self.assertEqual(partial["facet__company_activity__strength"], 0)

        category_partial = _restricted_targets(
            annotation(1),
            {
                "annotation_item_id": "C1:1",
                "company_id": "C1",
                "scraped_page_id": "1",
                "trust_flag": "partially_trusted",
                "trust_reason_codes": '["soft_logical_inconsistency"]',
                "lexical_restriction_reason_codes": "[]",
                "ungrounded_positive_facets": "[]",
                "soft_inconsistencies": '["category_without_matching_noise_flag:news_press_or_blog"]',
            },
        )
        self.assertIsNone(category_partial["page_category"])
        self.assertIsNone(category_partial["noise__news_or_press"])
        self.assertEqual(category_partial["utility_score"], 2)

    def test_build_is_deterministic_and_keeps_feature_blocks_isolated(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        summary1, _ = build_dataset(self.connection, self.root, self.config, self.config_path, first)
        summary2, _ = build_dataset(self.connection, self.root, self.config, self.config_path, second)
        self.assertEqual(summary1, summary2)
        for name in ("canonical_pages", "targets", "engineered_features", "scorer_v1_features", "split_manifest"):
            left = pd.read_parquet(first / f"{name}.parquet")
            right = pd.read_parquet(second / f"{name}.parquet")
            pd.testing.assert_frame_equal(left, right)

        canonical = pd.read_parquet(first / "canonical_pages.parquet")
        targets = pd.read_parquet(first / "targets.parquet")
        features = pd.read_parquet(first / "engineered_features.parquet")
        scorer = pd.read_parquet(first / "scorer_v1_features.parquet")
        splits = pd.read_parquet(first / "split_manifest.parquet")
        lexical = pd.read_parquet(first / "trusted_lexical_supervision.parquet")

        self.assertEqual(list(canonical.annotation_item_id), ["C1:1", "C2:2", "C3:3"])
        self.assertEqual(targets.target_row_available.tolist(), [True, True, False])
        self.assertTrue(pd.isna(targets.loc[1, "facet__technical_capability__strength"]))
        self.assertTrue(pd.isna(targets.loc[2, "utility_score"]))
        self.assertFalse(FORBIDDEN_PREDICTIVE_COLUMNS.intersection(features.columns))
        self.assertFalse(any(column.startswith("scorer_v1__") for column in features.columns))
        self.assertIn("scorer_v1__technology__final_score", scorer.columns)
        self.assertEqual(splits.cross_split_exact_duplicate.tolist(), [True, False, True])
        self.assertEqual(splits.modeling_eligible.tolist(), [False, True, False])
        self.assertEqual(set(lexical.annotation_item_id), {"C1:1", "C2:2"})
        self.assertEqual(summary1["dropped_or_unresolved_page_joins"], 0)

    def test_domain_crossing_splits_fails(self) -> None:
        pages = [json.loads(line) for line in (self.sample_dir / "pages.jsonl").read_text(encoding="utf-8").splitlines()]
        pages[2]["domain"] = pages[0]["domain"]
        write_jsonl(self.sample_dir / "pages.jsonl", pages)
        with self.assertRaisesRegex(ValueError, "domain split isolation"):
            build_dataset(self.connection, self.root, self.config, self.config_path, self.root / "leaky")

    def test_missing_extraction_join_fails(self) -> None:
        self.connection.execute("DELETE FROM extracted_pages WHERE extracted_page_id = 12")
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "Extraction join failed"):
            build_dataset(self.connection, self.root, self.config, self.config_path, self.root / "missing")


if __name__ == "__main__":
    unittest.main()
