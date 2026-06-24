from __future__ import annotations

import csv
import gzip
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.validation.build_validation_inputs import (
    build_validation_inputs,
    extract_visible_text,
)


def write_temp_config(project_root: Path, max_input_chars: int = 30) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "data" / "interim").mkdir(parents=True)
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "paths:",
                "  interim_dir: data/interim",
                "validation:",
                f"  max_input_chars: {max_input_chars}",
            ]
        ),
        encoding="utf-8",
    )


def create_html(project_root: Path, domain: str, filename: str, html: str) -> str:
    relative_path = Path("data") / "raw" / "html" / domain / filename
    full_path = project_root / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(full_path, "wt", encoding="utf-8") as file:
        file.write(html)
    return str(relative_path)


def insert_sampled_page(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    sample_id: str,
    domain: str,
    index: int,
    html: str,
    retained: int = 1,
    page_type: str = "about",
    html_path: str | None = None,
) -> tuple[int, int]:
    connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
    url = f"https://{domain}/page-{index}"
    candidate_page_id = None
    if retained:
        candidate_page_id = connection.execute(
            """
            INSERT INTO candidate_pages (
                domain, url, normalized_url, discovery_method, page_type_guess, priority
            )
            VALUES (?, ?, ?, 'homepage', ?, 4)
            """,
            (domain, url, url, page_type),
        ).lastrowid
    discovered_page_id = connection.execute(
        """
        INSERT INTO discovered_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            retained_at_discovery,
            discovery_run_id
        )
        VALUES (?, ?, ?, 'homepage', ?, 4, ?, 'run_test')
        """,
        (domain, url, url, page_type, retained),
    ).lastrowid
    html_path_value = html_path
    if html_path_value is None:
        html_path_value = create_html(project_root, domain, f"{index}.html.gz", html)
    scraped_page_id = connection.execute(
        """
        INSERT INTO scraped_pages (
            candidate_page_id,
            discovered_page_id,
            url,
            domain,
            html_path,
            scrape_status,
            title,
            meta_description
        )
        VALUES (?, ?, ?, ?, ?, 'success', 'Title', 'Description')
        """,
        (candidate_page_id, discovered_page_id, url, domain, html_path_value),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO retention_validation_sample (
            sample_id,
            sample_version,
            sample_type,
            scraped_page_id,
            discovered_page_id,
            candidate_page_id,
            domain,
            url,
            retained_at_discovery,
            page_type_guess,
            priority,
            discovery_method,
            selection_reason
        )
        VALUES (?, 'v1', 'smoke', ?, ?, ?, ?, ?, ?, ?, 4, 'homepage', 'test')
        """,
        (
            sample_id,
            scraped_page_id,
            discovered_page_id,
            candidate_page_id,
            domain,
            url,
            retained,
            page_type,
        ),
    )
    return scraped_page_id, discovered_page_id


def run_quietly(sample_id: str, project_root: Path, *, force_refresh: bool = False) -> tuple[dict[str, object], str]:
    output = StringIO()
    with redirect_stdout(output):
        summary = build_validation_inputs(sample_id, project_root=project_root, force_refresh=force_refresh)
    return summary, output.getvalue()


class BuildValidationInputsTest(unittest.TestCase):
    def test_extract_visible_text_removes_non_visible_and_preserves_readability(self) -> None:
        html = """
        <html>
          <head><style>.hidden{display:none}</style><script>bad()</script></head>
          <body>
            <h1>Company</h1>
            <p>AI services</p>
            <noscript>noscript text</noscript>
            <svg><text>icon text</text></svg>
            <p>Products   and\tcapabilities</p>
          </body>
        </html>
        """
        text = extract_visible_text(html)

        self.assertIn("Company", text)
        self.assertIn("AI services", text)
        self.assertIn("Products and capabilities", text)
        self.assertNotIn("bad()", text)
        self.assertNotIn("noscript text", text)
        self.assertNotIn("icon text", text)
        self.assertNotIn("servicesProducts", text)

    def test_build_validation_inputs_stores_preview_and_exports_summary_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root, max_input_chars=20)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sampled_page(
                    connection,
                    project_root,
                    sample_id="smoke_v1",
                    domain="example.at",
                    index=1,
                    html="<html><body><p>One two three four five six.</p></body></html>",
                )
                connection.commit()

            summary, _output = run_quietly("smoke_v1", project_root)

            self.assertEqual(summary["pages_processed"], 1)
            self.assertEqual(summary["pages_skipped"], 0)
            self.assertTrue(Path(summary["csv_path"]).is_file())
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT visible_text_length, preview_char_count, visible_text_preview
                    FROM retention_validation_inputs
                    WHERE sample_id = 'smoke_v1'
                    """
                ).fetchone()
            self.assertGreater(row[0], 20)
            self.assertEqual(row[1], 20)
            self.assertEqual(len(row[2]), 20)

            with Path(summary["csv_path"]).open("r", encoding="utf-8", newline="") as file:
                csv_rows = list(csv.DictReader(file))
            self.assertEqual(csv_rows[0]["sample_id"], "smoke_v1")
            self.assertNotIn("visible_text_preview", csv_rows[0])

    def test_skip_existing_and_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sampled_page(
                    connection,
                    project_root,
                    sample_id="smoke_v1",
                    domain="example.at",
                    index=1,
                    html="<html><body><p>First version.</p></body></html>",
                )
                connection.commit()

            run_quietly("smoke_v1", project_root)
            second_summary, _second_output = run_quietly("smoke_v1", project_root)
            refreshed_summary, _refreshed_output = run_quietly(
                "smoke_v1",
                project_root,
                force_refresh=True,
            )

            self.assertEqual(second_summary["pages_processed"], 0)
            self.assertEqual(second_summary["pages_skipped"], 1)
            self.assertEqual(refreshed_summary["pages_processed"], 1)
            with sqlite3.connect(db_path) as connection:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM retention_validation_inputs WHERE sample_id = 'smoke_v1'"
                ).fetchone()[0]
            self.assertEqual(row_count, 1)

    def test_empty_and_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_sampled_page(
                    connection,
                    project_root,
                    sample_id="smoke_v1",
                    domain="empty.at",
                    index=1,
                    html="<html><script>onlyScript()</script></html>",
                )
                insert_sampled_page(
                    connection,
                    project_root,
                    sample_id="smoke_v1",
                    domain="missing.at",
                    index=2,
                    html="",
                    html_path="data/raw/html/missing/missing.html.gz",
                )
                connection.commit()

            summary, _output = run_quietly("smoke_v1", project_root)

            self.assertEqual(summary["pages_processed"], 1)
            self.assertEqual(summary["empty_extraction_count"], 1)
            self.assertEqual(summary["extraction_failure_count"], 1)
            self.assertIn("missing", summary["failure_examples"][0][0])

    def test_missing_sample_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            init_database(project_root)

            with self.assertRaisesRegex(ValueError, "No validation sample rows found"):
                build_validation_inputs("missing_sample", project_root=project_root)


if __name__ == "__main__":
    unittest.main()
