from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.build_company_evidence_packages import (
    build_company_evidence_packages,
    calibration_page_sort_key,
    estimate_tokens,
    render_blind_markdown,
    validate_blind_payload,
)
from ai_ecosystem.db.init_db import init_database


DIMENSIONS = ("firm_profile", "technology", "innovation", "ecosystem")


def write_project_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.yaml").write_text("database:\n  path: db/test.sqlite\n", encoding="utf-8")
    (root / "config" / "calibration.yaml").write_text(
        "\n".join(
            [
                "package_builder_id: test_calibration",
                "package_builder_version: v1",
                "default_sample_id: pilot_v0_2",
                "default_scoring_run_id: scoring_test",
                "default_max_package_tokens: 10000",
                "tokenizer_model: gpt-5",
            ]
        ),
        encoding="utf-8",
    )


def evidence(root: Path, scraped_page_id: int, content: str) -> tuple[str, str, str]:
    directory = root / "data" / "extracted" / "pages" / "000000" / str(scraped_page_id)
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / "page.md"
    text = directory / "page.txt"
    metadata = directory / "metadata.json"
    markdown.write_text(f"# Heading {scraped_page_id}\n\n{content}", encoding="utf-8")
    text.write_text(content, encoding="utf-8")
    metadata.write_text("{}", encoding="utf-8")
    return tuple(str(path.relative_to(root)) for path in (markdown, text, metadata))


def add_page(connection: sqlite3.Connection, root: Path, domain: str, url_suffix: str, content: str, *, score: float = 99.0) -> int:
    cursor = connection.execute("INSERT INTO scraped_pages (url, domain, scrape_status) VALUES (?, ?, 'success')", (f"https://{domain}/{url_suffix}", domain))
    scraped_id = int(cursor.lastrowid)
    markdown_path, text_path, metadata_path = evidence(root, scraped_id, content)
    cursor = connection.execute(
        """INSERT INTO extracted_pages (scraped_page_id, extraction_version, extraction_status, markdown_path, text_path, metadata_path, content_sha256)
           VALUES (?, 'v1', 'success', ?, ?, ?, ?)""",
        (scraped_id, markdown_path, text_path, metadata_path, f"hash-{scraped_id}"),
    )
    extracted_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO page_features (extracted_page_id, title, headings, word_count, page_type_guess, language) VALUES (?, ?, ?, ?, 'neutral_structure', 'en')",
        (extracted_id, f"Title {scraped_id}", json.dumps([{"level": 1, "text": f"Heading {scraped_id}"}]), len(content.split())),
    )
    for dimension in DIMENSIONS:
        connection.execute(
            """INSERT INTO page_scores (extracted_page_id, scraped_page_id, domain, url, scoring_run_id, dimension, extraction_version,
                      query_score, keyword_score, boost_score, negative_score, final_score,
                      top_queries_json, top_keywords_json, top_boost_terms_json, top_negative_terms_json,
                      knowledge_base_id, knowledge_base_version, scoring_profile_id, scoring_profile_version)
               VALUES (?, ?, ?, ?, 'scoring_test', ?, 'v1', 1, 2, 3, 0.5, ?, ?, ?, ?, ?, ?, 'v1', 'profile', 'v1')""",
            (extracted_id, scraped_id, domain, f"https://{domain}/{url_suffix}", dimension, score, json.dumps([{"value": "query evidence"}]), json.dumps([{"value": "keyword evidence"}]), json.dumps([{"value": "boost evidence"}]), json.dumps([{"value": "negative evidence"}]), dimension),
        )
    return scraped_id


class CompanyEvidencePackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project_config(self.root)
        self.db_path = init_database(self.root)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("INSERT INTO sample_sets (sample_id, sample_name, sample_size, created_at) VALUES ('pilot_v0_2', 'Pilot', 1, 'now')")
            connection.execute("INSERT INTO scoring_runs (scoring_run_id, status, extraction_version, scoring_profile_id, scoring_profile_version, pages_scored) VALUES ('scoring_test', 'completed', 'v1', 'profile', 'v1', 8)")
            connection.execute("INSERT INTO domains (domain, firm_count, domain_category, representation_level) VALUES ('shared.example', 2, 'company', 'shared_domain')")
            connection.execute("INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_v0_2', 'shared.example', 1)")
            for index in (1, 2):
                connection.execute("INSERT INTO firms (bvd_id, firm_name, normalized_domain, nace_code) VALUES (?, ?, 'shared.example', '6201')", (f"AT{index}", f"Firm {index}"))
            self.first_page = add_page(connection, self.root, "shared.example", "first", "first page neutral content " * 40, score=1000.0)
            self.second_page = add_page(connection, self.root, "shared.example", "second", "second page neutral content " * 40, score=-1000.0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_shared_domain_firms_get_separate_blind_packages_with_all_pages(self) -> None:
        output, manifest = build_company_evidence_packages(project_root=self.root, output_dir="data/output")
        self.assertEqual(manifest["company_count"], 2)
        self.assertEqual(manifest["shared_domain_company_count"], 2)
        for company_id in ("AT1", "AT2"):
            blind = json.loads((output / "blind_inputs" / f"{company_id}.json").read_text(encoding="utf-8"))
            self.assertTrue(blind["blind_to_scoring"])
            self.assertEqual([page["scraped_page_id"] for page in blind["pages"]], [self.first_page, self.second_page])
            self.assertTrue(blind["focal_company"]["shared_domain"])
            self.assertEqual(blind["focal_company"]["mapped_company_count"], 2)

    def test_structural_page_ordering_prioritizes_root_about_and_shallow_paths(self) -> None:
        pages = [
            {"scraped_page_id": 90, "url": "https://example.at/products/software/deep", "normalized_url": "https://example.at/products/software/deep"},
            {"scraped_page_id": 50, "url": "https://example.at/products", "normalized_url": "https://example.at/products"},
            {"scraped_page_id": 80, "url": "https://example.at/unternehmen/team", "normalized_url": "https://example.at/unternehmen/team"},
            {"scraped_page_id": 40, "url": "https://example.at/about", "normalized_url": "https://example.at/about"},
            {"scraped_page_id": 30, "url": "https://example.at/?source=nav#top", "normalized_url": "https://example.at/?source=nav#top"},
            {"scraped_page_id": 20, "url": "https://example.at/contact?x=1#details", "normalized_url": "https://example.at/contact?x=1#details"},
            {"scraped_page_id": 10, "url": "https://example.at/contact?x=2", "normalized_url": "https://example.at/contact?x=2"},
        ]

        ordered = sorted(pages, key=calibration_page_sort_key)

        self.assertEqual(
            [page["scraped_page_id"] for page in ordered],
            [30, 40, 80, 10, 20, 50, 90],
        )

    def test_structural_ordering_uses_exact_path_components(self) -> None:
        pages = [
            {"scraped_page_id": 1, "url": "https://example.at/aboutness", "normalized_url": "https://example.at/aboutness"},
            {"scraped_page_id": 2, "url": "https://example.at/%C3%BCber-uns", "normalized_url": "https://example.at/%C3%BCber-uns"},
        ]

        ordered = sorted(pages, key=calibration_page_sort_key)

        self.assertEqual([page["scraped_page_id"] for page in ordered], [2, 1])

    def test_blind_inputs_contain_no_score_data_and_analysis_keeps_it(self) -> None:
        output, _ = build_company_evidence_packages(project_root=self.root, output_dir="data/output")
        blind_text = (output / "blind_inputs" / "AT1.md").read_text(encoding="utf-8")
        blind_json = json.loads((output / "blind_inputs" / "AT1.json").read_text(encoding="utf-8"))
        analysis = json.loads((output / "analysis_metadata" / "AT1.json").read_text(encoding="utf-8"))
        self.assertNotIn("final_score", blind_text)
        self.assertNotIn("query evidence", blind_text)
        self.assertNotIn("final_score", json.dumps(blind_json))
        self.assertFalse(analysis["send_to_openai"])
        self.assertEqual(analysis["pages"][0]["scores"][0]["final_score"], 1000.0)
        self.assertEqual(analysis["pages"][0]["scores"][0]["top_queries"][0]["value"], "query evidence")

    def test_token_truncation_is_ordered_and_not_score_based(self) -> None:
        output, manifest = build_company_evidence_packages(project_root=self.root, output_dir="data/output", max_package_tokens=550)
        blind = json.loads((output / "blind_inputs" / "AT1.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["companies_truncated"])
        self.assertEqual([page["scraped_page_id"] for page in blind["pages"]], [self.first_page, self.second_page])
        self.assertIn(blind["pages"][0]["content_status"], {"complete", "partially_truncated"})
        self.assertIn(blind["pages"][1]["content_status"], {"partially_truncated", "metadata_only"})

    def test_metadata_only_pages_remain_in_json_but_not_model_markdown(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            for index in range(3, 43):
                add_page(connection, self.root, "shared.example", f"archive/{index}", "metadata pressure " * 100)
        output, _ = build_company_evidence_packages(project_root=self.root, output_dir="data/output", max_package_tokens=700)
        blind = json.loads((output / "blind_inputs" / "AT1.json").read_text(encoding="utf-8"))
        markdown = (output / "blind_inputs" / "AT1.md").read_text(encoding="utf-8")
        metadata_only = [page for page in blind["pages"] if page["content_status"] == "metadata_only"]
        included = [page for page in blind["pages"] if page["content_status"] in {"complete", "partially_truncated"}]

        self.assertGreater(len(metadata_only), 0)
        self.assertGreater(len(included), 0)
        self.assertEqual(len(blind["pages"]), 42)
        self.assertIn(f"- Additional metadata-only pages omitted from model input: {len(metadata_only)}", markdown)
        self.assertNotIn("Content availability: metadata_only", markdown)
        self.assertEqual(markdown.count("Content:\n```text"), len(included))
        for page in metadata_only:
            self.assertNotIn(f"- Scraped page ID: {page['scraped_page_id']}", markdown)
            self.assertEqual(page["content"], "")
        self.assertLessEqual(estimate_tokens(markdown), 700)
        validate_blind_payload(blind, markdown)

    def test_renderer_shows_complete_and_partial_pages_only(self) -> None:
        def page(scraped_page_id: int, status: str, content: str) -> dict[str, object]:
            return {
                "scraped_page_id": scraped_page_id,
                "extracted_page_id": scraped_page_id + 100,
                "url": f"https://example.test/{scraped_page_id}",
                "normalized_url": f"https://example.test/{scraped_page_id}",
                "title": f"Title {scraped_page_id}",
                "headings": [{"text": f"Heading {scraped_page_id}", "level": 1}],
                "page_type": "neutral",
                "language": "en",
                "word_count": 2,
                "extraction_version": "v1",
                "content_status": status,
                "content": content,
            }

        payload = {
            "blind_to_scoring": True,
            "focal_company": {"company_id": "AT1", "company_name": "Firm", "normalized_domain": "example.test", "pilot_sample_id": "sample", "nace_code": "6201", "activity_description": None},
            "evidence_availability": {"acquired_page_count": 3, "successful_extracted_page_count": 3, "usable_page_count": 3, "total_extracted_words": 6},
            "pages": [
                page(1, "complete", "complete evidence"),
                page(2, "partially_truncated", "partial evidence"),
                page(3, "metadata_only", ""),
            ],
        }

        markdown = render_blind_markdown(payload)

        self.assertIn("- Pages included with full text: 1", markdown)
        self.assertIn("- Pages included with partial text: 1", markdown)
        self.assertIn("- Additional metadata-only pages omitted from model input: 1", markdown)
        self.assertIn("- Scraped page ID: 1", markdown)
        self.assertIn("- Scraped page ID: 2", markdown)
        self.assertNotIn("- Scraped page ID: 3", markdown)
        self.assertIn("complete evidence", markdown)
        self.assertIn("partial evidence", markdown)
        self.assertNotIn("Content availability: metadata_only", markdown)

    def test_validator_rejects_scoring_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "scorer leakage"):
            validate_blind_payload({"blind_to_scoring": True, "pages": [{"final_score": 1.0}]})

    def test_existing_output_requires_force_refresh(self) -> None:
        build_company_evidence_packages(project_root=self.root, output_dir="data/output")
        with self.assertRaises(FileExistsError):
            build_company_evidence_packages(project_root=self.root, output_dir="data/output")
        build_company_evidence_packages(project_root=self.root, output_dir="data/output", force_refresh=True)


if __name__ == "__main__":
    unittest.main()
