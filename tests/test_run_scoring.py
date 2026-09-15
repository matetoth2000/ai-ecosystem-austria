from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.scoring.run_scoring import run_scoring, select_scoring_candidates


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir(parents=True, exist_ok=True)
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
                "  dimension_weights:",
                "    firm_profile: 1.0",
                "    technology: 1.0",
                "    innovation: 1.0",
                "    ecosystem: 1.0",
                "  field_weights:",
                "    title: 2.0",
                "    headings: 1.5",
                "    markdown: 1.0",
                "    plain_text: 1.0",
                "    url: 0.5",
                "    internal_anchor_text: 0.5",
                "    external_anchor_text: 0.25",
                "  fusion_weights:",
                "    query: 1.0",
                "    keyword: 1.0",
                "    boost: 0.5",
                "    negative: -1.0",
            ]
        ),
        encoding="utf-8",
    )


def write_kb(project_root: Path, dimension: str, keyword: str) -> None:
    directory = project_root / "config" / "knowledge_bases"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{dimension}_v1.yaml").write_text(
        "\n".join(
            [
                "version: v1",
                "",
                f"{dimension}:",
                "  node:",
                "    query_families:",
                f"      - \"We provide {keyword} capability evidence.\"",
                "    keywords:",
                "      en:",
                f"        - {keyword}",
                "      de: []",
                "    boost_terms:",
                "      en:",
                f"        - {keyword} implementation",
                "      de: []",
                "    negative_terms:",
                "      en:",
                "        - low value",
                "      de: []",
            ]
        ),
        encoding="utf-8",
    )


def write_all_kbs(project_root: Path) -> None:
    write_kb(project_root, "firm_profile", "products")
    write_kb(project_root, "technology", "software")
    write_kb(project_root, "innovation", "prototype")
    write_kb(project_root, "ecosystem", "partner")


def write_evidence(project_root: Path, scraped_page_id: int, markdown: str, text: str, metadata: str | dict | None = None) -> tuple[str, str, str | None]:
    directory = project_root / "data" / "extracted" / "pages" / "000000" / str(scraped_page_id)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / "page.md"
    text_path = directory / "page.txt"
    markdown_path.write_text(markdown, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    metadata_path: Path | None = None
    if metadata is not None:
        metadata_path = directory / "metadata.json"
        if isinstance(metadata, dict):
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        else:
            metadata_path.write_text(metadata, encoding="utf-8")
    return (
        str(markdown_path.relative_to(project_root)),
        str(text_path.relative_to(project_root)),
        str(metadata_path.relative_to(project_root)) if metadata_path else None,
    )


def insert_extracted_page(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    domain: str,
    url: str,
    markdown: str,
    text: str,
    metadata: str | dict | None = None,
    sample_id: str | None = None,
    with_features: bool = False,
    links: list[tuple[str, str]] | None = None,
) -> tuple[int, int]:
    cursor = connection.execute(
        "INSERT INTO scraped_pages (url, domain, scrape_status) VALUES (?, ?, 'success')",
        (url, domain),
    )
    scraped_page_id = int(cursor.lastrowid)
    markdown_path, text_path, metadata_path = write_evidence(project_root, scraped_page_id, markdown, text, metadata)
    cursor = connection.execute(
        """
        INSERT INTO extracted_pages (
            scraped_page_id,
            extraction_version,
            extraction_status,
            markdown_path,
            text_path,
            metadata_path,
            content_sha256
        )
        VALUES (?, 'v_test_extract', 'success', ?, ?, ?, 'hash')
        """,
        (scraped_page_id, markdown_path, text_path, metadata_path),
    )
    extracted_page_id = int(cursor.lastrowid)
    if with_features:
        connection.execute(
            """
            INSERT INTO page_features (
                extracted_page_id,
                title,
                headings,
                heading_count,
                markdown_path,
                text_path,
                feature_version
            )
            VALUES (?, 'Feature Title software', ?, 1, ?, ?, 'v_test_extract')
            """,
            (
                extracted_page_id,
                json.dumps(["Software implementation heading"]),
                markdown_path,
                text_path,
            ),
        )
    for index, (anchor_text, internal_external) in enumerate(links or [], start=1):
        connection.execute(
            """
            INSERT INTO page_links (
                extracted_page_id,
                source_url,
                target_url,
                anchor_text,
                internal_external,
                is_internal,
                link_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extracted_page_id,
                url,
                f"https://target.test/{index}",
                anchor_text,
                internal_external,
                1 if internal_external == "internal" else 0,
                index,
            ),
        )
    if sample_id:
        connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
        cursor = connection.execute(
            """
            INSERT INTO discovered_pages (
                domain,
                url,
                normalized_url,
                retained_at_discovery,
                discovery_run_id
            )
            VALUES (?, ?, ?, 1, 'test_discovery')
            """,
            (domain, url, url),
        )
        discovered_page_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE scraped_pages SET discovered_page_id = ? WHERE scraped_page_id = ?",
            (discovered_page_id, scraped_page_id),
        )
        connection.execute(
            """
            INSERT INTO retention_validation_sample (
                sample_id,
                sample_version,
                sample_type,
                scraped_page_id,
                discovered_page_id,
                domain,
                url,
                retained_at_discovery,
                selection_reason
            )
            VALUES (?, 'v1', 'smoke', ?, ?, ?, ?, 1, 'test')
            """,
            (sample_id, scraped_page_id, discovered_page_id, domain, url),
        )
    return scraped_page_id, extracted_page_id


class RunScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tmp.name)
        write_temp_config(self.project_root)
        write_all_kbs(self.project_root)
        self.db_path = init_database(self.project_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scores_validation_sample_and_persists_results(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            page1, _ = insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/about",
                markdown="# Products\n\nSoftware implementation with partner prototype.",
                text="Products software implementation with partner prototype.",
                metadata={"title": "Metadata Title products"},
                sample_id="smoke_v1",
                links=[("software partner", "internal"), ("prototype", "external")],
            )
            insert_extracted_page(
                connection,
                self.project_root,
                domain="b.test",
                url="https://b.test/technology",
                markdown="# Software\n\nPrototype partner products.",
                text="Software prototype partner products.",
                sample_id="smoke_v1",
                with_features=True,
            )
            insert_extracted_page(
                connection,
                self.project_root,
                domain="c.test",
                url="https://c.test/skip",
                markdown="# Software\n\nNot in sample.",
                text="Software not in sample.",
            )
            connection.commit()

        output = io.StringIO()
        with redirect_stdout(output):
            report = run_scoring(sample_id="smoke_v1", project_root=self.project_root, progress_every=1)

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            scores = connection.execute("SELECT * FROM page_scores ORDER BY scraped_page_id, dimension").fetchall()
            run = connection.execute("SELECT * FROM scoring_runs").fetchone()

        self.assertEqual(report["pages_selected"], 2)
        self.assertEqual(report["pages_scored"], 2)
        self.assertEqual(report["score_rows_written"], 8)
        self.assertEqual(len(scores), 8)
        self.assertEqual(run["pages_selected"], 2)
        self.assertEqual(run["pages_scored"], 2)
        self.assertEqual(run["pages_skipped"], 0)
        self.assertGreater(run["pages_per_second"], 0)
        self.assertTrue(all(row["extraction_version"] == "v_test_extract" for row in scores))
        self.assertTrue(all(row["scoring_profile_version"] == "v_test" for row in scores))
        self.assertIn("query_score", scores[0].keys())
        first_page_technology = [
            row for row in scores if row["scraped_page_id"] == page1 and row["dimension"] == "technology"
        ][0]
        top_keywords = json.loads(first_page_technology["top_keywords_json"])
        self.assertTrue(any(item["value"] == "software" for item in top_keywords))
        self.assertTrue((self.project_root / report["report_paths"]["json"]).is_file())
        self.assertTrue((self.project_root / report["report_paths"]["summary_csv"]).is_file())
        self.assertIn("Pages/sec", output.getvalue())
        self.assertEqual(report["sample_id"], "smoke_v1")
        self.assertEqual(report["scoring_profile_version"], "v_test")
        self.assertEqual(report["knowledge_base_versions"]["technology"], "v1")

    def test_selection_filters_domain_page_limit_and_order(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            insert_extracted_page(
                connection,
                self.project_root,
                domain="z.test",
                url="https://z.test/one",
                markdown="# Products",
                text="products",
            )
            insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/two",
                markdown="# Software",
                text="software",
            )
            insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/three",
                markdown="# Partner",
                text="partner",
            )
            connection.row_factory = sqlite3.Row
            candidates = select_scoring_candidates(
                connection,
                sample_id=None,
                domain_sample_id=None,
                domain="a.test",
                page_limit=1,
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].domain, "a.test")
        self.assertEqual(candidates[0].scraped_page_id, 2)

    def test_scores_registered_generic_domain_sample_without_changing_legacy_sample_behavior(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO sample_sets (sample_id, sample_name, created_at) VALUES ('ai_enriched_v1', 'AI enriched', 'now')"
            )
            connection.execute(
                "INSERT INTO sample_sets (sample_id, sample_name, created_at) VALUES ('other_sample', 'Other', 'now')"
            )
            connection.executemany(
                "INSERT INTO domains (domain) VALUES (?)",
                [("included.test",), ("also-included.test",), ("excluded.test",)],
            )
            connection.executemany(
                "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES (?, ?, ?)",
                [
                    ("ai_enriched_v1", "included.test", 1),
                    ("ai_enriched_v1", "also-included.test", 2),
                    ("other_sample", "excluded.test", 1),
                ],
            )
            included_id, _ = insert_extracted_page(
                connection,
                self.project_root,
                domain="included.test",
                url="https://included.test/about",
                markdown="# Products",
                text="products software prototype partner",
                sample_id="smoke_v1",
            )
            also_included_id, _ = insert_extracted_page(
                connection,
                self.project_root,
                domain="also-included.test",
                url="https://also-included.test/about",
                markdown="# Software",
                text="products software prototype partner",
            )
            excluded_id, _ = insert_extracted_page(
                connection,
                self.project_root,
                domain="excluded.test",
                url="https://excluded.test/about",
                markdown="# Partner",
                text="products software prototype partner",
            )
            connection.row_factory = sqlite3.Row
            selected = select_scoring_candidates(
                connection,
                sample_id=None,
                domain_sample_id="ai_enriched_v1",
                domain=None,
                page_limit=None,
            )
            intersected = select_scoring_candidates(
                connection,
                sample_id="smoke_v1",
                domain_sample_id="ai_enriched_v1",
                domain=None,
                page_limit=None,
            )
            legacy_selected = select_scoring_candidates(
                connection,
                sample_id="smoke_v1",
                domain_sample_id=None,
                domain=None,
                page_limit=None,
            )
            with self.assertRaisesRegex(ValueError, "Unknown domain sample_id"):
                select_scoring_candidates(
                    connection,
                    sample_id=None,
                    domain_sample_id="missing_sample",
                    domain=None,
                    page_limit=None,
                )
            connection.commit()

        self.assertEqual([candidate.scraped_page_id for candidate in selected], [included_id, also_included_id])
        self.assertEqual([candidate.scraped_page_id for candidate in intersected], [included_id])
        self.assertEqual([candidate.scraped_page_id for candidate in legacy_selected], [included_id])
        self.assertNotIn(excluded_id, [candidate.scraped_page_id for candidate in selected])

        report = run_scoring(domain_sample_id="ai_enriched_v1", project_root=self.project_root)

        self.assertEqual(report["sample_id"], "ai_enriched_v1")
        self.assertEqual(report["domain_sample_id"], "ai_enriched_v1")
        self.assertEqual(report["pages_selected"], 2)
        self.assertEqual(report["pages_scored"], 2)

    def test_rerun_skips_existing_scores_and_force_refresh_replaces(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/about",
                markdown="# Products",
                text="products software prototype partner",
                sample_id="smoke_v1",
            )
            connection.commit()

        first = run_scoring(sample_id="smoke_v1", project_root=self.project_root)
        second = run_scoring(sample_id="smoke_v1", project_root=self.project_root)
        with sqlite3.connect(self.db_path) as connection:
            run_rows = connection.execute(
                "SELECT scoring_run_id, pages_scored, pages_skipped FROM scoring_runs ORDER BY started_at"
            ).fetchall()
            score_count_after_skip = connection.execute("SELECT COUNT(*) FROM page_scores").fetchone()[0]

        self.assertEqual(first["score_rows_written"], 4)
        self.assertEqual(second["score_rows_written"], 0)
        self.assertEqual(run_rows[-1][1], 0)
        self.assertEqual(run_rows[-1][2], 1)
        self.assertEqual(score_count_after_skip, 4)

        refreshed = run_scoring(sample_id="smoke_v1", project_root=self.project_root, force_refresh=True)
        with sqlite3.connect(self.db_path) as connection:
            score_rows = connection.execute("SELECT scoring_run_id FROM page_scores").fetchall()
        self.assertEqual(refreshed["score_rows_written"], 4)
        self.assertEqual(len(score_rows), 4)
        self.assertTrue(all(row[0] == refreshed["scoring_run_id"] for row in score_rows))

    def test_missing_required_evidence_fails_page_without_aborting(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            scraped_page_id, _ = insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/missing",
                markdown="# Products",
                text="products",
                sample_id="smoke_v1",
            )
            missing_path = "data/extracted/pages/000000/missing/page.md"
            connection.execute(
                "UPDATE extracted_pages SET markdown_path = ? WHERE scraped_page_id = ?",
                (missing_path, scraped_page_id),
            )
            connection.commit()

        report = run_scoring(sample_id="smoke_v1", project_root=self.project_root)

        with sqlite3.connect(self.db_path) as connection:
            score_count = connection.execute("SELECT COUNT(*) FROM page_scores").fetchone()[0]
            run = connection.execute("SELECT pages_failed, status FROM scoring_runs").fetchone()

        self.assertEqual(report["pages_failed"], 1)
        self.assertEqual(score_count, 0)
        self.assertEqual(run[0], 1)
        self.assertEqual(run[1], "completed_with_errors")

    def test_corrupt_optional_metadata_warns_and_continues(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            insert_extracted_page(
                connection,
                self.project_root,
                domain="a.test",
                url="https://a.test/about",
                markdown="# Products",
                text="products software",
                metadata="{not-json",
                sample_id="smoke_v1",
            )
            connection.commit()

        report = run_scoring(sample_id="smoke_v1", project_root=self.project_root)

        self.assertEqual(report["pages_scored"], 1)
        self.assertTrue(any("metadata_unreadable" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
