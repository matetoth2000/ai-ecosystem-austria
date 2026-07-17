from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.scoring.evaluate_scoring import evaluate_scoring, select_top_pages


def write_config(project_root: Path) -> None:
    (project_root / "config" / "knowledge_bases").mkdir(parents=True, exist_ok=True)
    (project_root / "db").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "scoring:",
                "  scoring_profile_id: test_profile",
                "  scoring_profile_version: v_test",
                "  knowledge_base_directory: config/knowledge_bases",
                "  required_dimensions:",
                "    - firm_profile",
                "    - technology",
                "    - innovation",
                "    - ecosystem",
                "validation:",
                "  max_input_chars: 80",
            ]
        ),
        encoding="utf-8",
    )
    for dimension in ("firm_profile", "technology", "innovation", "ecosystem"):
        (project_root / "config" / "knowledge_bases" / f"{dimension}_v1.yaml").write_text(
            "\n".join(
                [
                    "version: v1",
                    f"{dimension}:",
                    "  node:",
                    "    query_families: []",
                    "    keywords:",
                    "      en: []",
                    "      de: []",
                    "    boost_terms:",
                    "      en: []",
                    "      de: []",
                    "    negative_terms:",
                    "      en: []",
                    "      de: []",
                ]
            ),
            encoding="utf-8",
        )


def write_evidence(project_root: Path, scraped_page_id: int, *, markdown: str, text: str, metadata: dict | None = None) -> tuple[str, str, str | None]:
    directory = project_root / "data" / "extracted" / "pages" / "000000" / str(scraped_page_id)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / "page.md"
    text_path = directory / "page.txt"
    markdown_path.write_text(markdown, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    metadata_path = None
    if metadata is not None:
        metadata_path = directory / "metadata.json"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return (
        str(markdown_path.relative_to(project_root)),
        str(text_path.relative_to(project_root)),
        str(metadata_path.relative_to(project_root)) if metadata_path else None,
    )


def insert_scored_page(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    scoring_run_id: str,
    domain: str,
    url: str,
    dimension_scores: dict[str, float],
    markdown: str = "# Heading\n\nMarkdown content about products and software.",
    text: str = "Heading plain text content about products and software.",
    title: str | None = "Feature Title",
    missing_markdown: bool = False,
) -> tuple[int, int]:
    cursor = connection.execute(
        "INSERT INTO scraped_pages (url, domain, scrape_status) VALUES (?, ?, 'success')",
        (url, domain),
    )
    scraped_page_id = int(cursor.lastrowid)
    markdown_path, text_path, metadata_path = write_evidence(
        project_root,
        scraped_page_id,
        markdown=markdown,
        text=text,
        metadata={"title": "Metadata Title"},
    )
    if missing_markdown:
        markdown_path = "data/extracted/pages/missing/page.md"
    cursor = connection.execute(
        """
        INSERT INTO extracted_pages (
            scraped_page_id,
            extraction_version,
            extraction_status,
            markdown_path,
            text_path,
            metadata_path
        )
        VALUES (?, 'v_test_extract', 'success', ?, ?, ?)
        """,
        (scraped_page_id, markdown_path, text_path, metadata_path),
    )
    extracted_page_id = int(cursor.lastrowid)
    if title is not None:
        connection.execute(
            """
            INSERT INTO page_features (
                extracted_page_id,
                title,
                headings,
                heading_hierarchy,
                page_type_guess
            )
            VALUES (?, ?, ?, ?, 'products')
            """,
            (
                extracted_page_id,
                title,
                json.dumps(["Feature Heading"]),
                json.dumps([{"level": 1, "text": "Hierarchy Heading"}]),
            ),
        )
    match_json = json.dumps(
        [
            {
                "value": "sample match",
                "score": 1.5,
                "matched_fields": ["title", "plain_text"],
                "matched_terms": ["sample"],
                "source_node": "node",
                "language": "en",
            }
        ]
    )
    for dimension, final_score in dimension_scores.items():
        connection.execute(
            """
            INSERT INTO page_scores (
                extracted_page_id,
                scraped_page_id,
                domain,
                url,
                scoring_run_id,
                dimension,
                extraction_version,
                query_score,
                keyword_score,
                boost_score,
                negative_score,
                final_score,
                top_queries_json,
                top_keywords_json,
                top_boost_terms_json,
                top_negative_terms_json,
                knowledge_base_id,
                knowledge_base_version,
                scoring_profile_id,
                scoring_profile_version
            )
            VALUES (?, ?, ?, ?, ?, ?, 'v_test_extract', 1.0, 2.0, 3.0, 0.5, ?, ?, ?, ?, ?, ?, 'v1', 'test_profile', 'v_test')
            """,
            (
                extracted_page_id,
                scraped_page_id,
                domain,
                url,
                scoring_run_id,
                dimension,
                final_score,
                match_json,
                match_json,
                match_json,
                match_json,
                dimension,
            ),
        )
    return scraped_page_id, extracted_page_id


class EvaluateScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tmp.name)
        write_config(self.project_root)
        self.db_path = init_database(self.project_root)
        self.run_id = "scoring_test_run"
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO scoring_runs (
                    scoring_run_id,
                    scoring_profile_id,
                    scoring_profile_version,
                    sample_id,
                    extraction_version,
                    pages_selected,
                    pages_scored,
                    status
                )
                VALUES (?, 'test_profile', 'v_test', 'main_v1', 'v_test_extract', 3, 3, 'completed')
                """,
                (self.run_id,),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_selects_top_n_per_dimension_with_deterministic_tie_order(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            first, _ = insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="a.test",
                url="https://a.test/one",
                dimension_scores={"firm_profile": 10, "technology": 9},
            )
            second, _ = insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="b.test",
                url="https://b.test/two",
                dimension_scores={"firm_profile": 10, "technology": 20},
            )
            insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="c.test",
                url="https://c.test/three",
                dimension_scores={"firm_profile": 1, "technology": 1},
            )
            connection.commit()
            pages = select_top_pages(connection, self.run_id, ("firm_profile", "technology"), 1)

        self.assertEqual([(page.dimension, page.scraped_page_id) for page in pages], [("firm_profile", first), ("technology", second)])

    def test_generates_prompt_manifest_and_dimension_specific_page_files(self) -> None:
        long_text = " ".join(["software"] * 100)
        with sqlite3.connect(self.db_path) as connection:
            insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="a.test",
                url="https://a.test/one",
                dimension_scores={"firm_profile": 10, "technology": 20},
                markdown="# Feature Heading\n\n" + long_text,
                text=long_text,
            )
            connection.commit()

        manifest = evaluate_scoring(
            scoring_run_id=self.run_id,
            top_n=1,
            output_dir=self.project_root / "review",
            project_root=self.project_root,
        )

        package_dir = self.project_root / manifest["output_directory"]
        prompt = (package_dir / "evaluation_prompt.md").read_text(encoding="utf-8")
        manifest_json = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
        page_files = sorted(path.name for path in package_dir.glob("*_rank_*.md"))
        self.assertIn("Is this page genuinely useful for understanding the company?", prompt)
        self.assertIn("legal/privacy", prompt)
        self.assertEqual(manifest_json["total_review_files"], 2)
        self.assertEqual(page_files[0], "firm_profile_rank_001_scraped_1.md")
        self.assertEqual(page_files[1], "technology_rank_001_scraped_1.md")
        page_content = (package_dir / page_files[0]).read_text(encoding="utf-8")
        self.assertIn("## Score Components", page_content)
        self.assertIn("Feature Heading", page_content)
        self.assertIn("Top Query Matches", page_content)
        self.assertIn("Truncated: `true`", page_content)

    def test_missing_required_evidence_is_reported_without_aborting(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="a.test",
                url="https://a.test/missing",
                dimension_scores={"firm_profile": 10},
                missing_markdown=True,
            )
            connection.commit()

        manifest = evaluate_scoring(
            scoring_run_id=self.run_id,
            top_n=1,
            output_dir=self.project_root / "review",
            project_root=self.project_root,
        )

        self.assertEqual(manifest["total_review_files"], 1)
        self.assertTrue(any("markdown_file_missing" in warning for warning in manifest["warnings"]))
        package_dir = self.project_root / manifest["output_directory"]
        page_content = next(package_dir.glob("*_rank_*.md")).read_text(encoding="utf-8")
        self.assertIn("markdown_file_missing", page_content)

    def test_existing_output_directory_fails_without_overwrite(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            insert_scored_page(
                connection,
                self.project_root,
                scoring_run_id=self.run_id,
                domain="a.test",
                url="https://a.test/one",
                dimension_scores={"firm_profile": 10},
            )
            connection.commit()

        evaluate_scoring(
            scoring_run_id=self.run_id,
            top_n=1,
            output_dir=self.project_root / "review",
            project_root=self.project_root,
        )
        with self.assertRaises(FileExistsError):
            evaluate_scoring(
                scoring_run_id=self.run_id,
                top_n=1,
                output_dir=self.project_root / "review",
                project_root=self.project_root,
            )


if __name__ == "__main__":
    unittest.main()
