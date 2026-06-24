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
from ai_ecosystem.validation.build_validation_sample import build_validation_sample


def write_temp_config(project_root: Path, smoke_target: int = 5, main_target: int = 300) -> None:
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
                "  smoke_sample_size_per_group: " + str(smoke_target),
                "  main_sample_size_per_group: " + str(main_target),
                "  max_pages_per_domain: 3",
                "  random_seed: 123",
            ]
        ),
        encoding="utf-8",
    )


def create_html(project_root: Path, domain: str, index: int) -> str:
    relative_path = Path("data") / "raw" / "html" / domain / f"{index}.html.gz"
    full_path = project_root / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(full_path, "wt", encoding="utf-8") as file:
        file.write("<html><title>Test</title><body>hello world</body></html>")
    return str(relative_path)


def insert_page(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    domain: str,
    index: int,
    retained: int,
    page_type: str,
    priority: int,
    scrape_status: str = "success",
    create_file: bool = True,
) -> int:
    connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
    url = f"https://{domain}/page-{index}"
    candidate_page_id = None
    if retained:
        candidate_page_id = connection.execute(
            """
            INSERT INTO candidate_pages (
                domain, url, normalized_url, discovery_method, page_type_guess, priority
            )
            VALUES (?, ?, ?, 'homepage', ?, ?)
            """,
            (domain, url, url, page_type, priority),
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
        VALUES (?, ?, ?, 'sitemap', ?, ?, ?, 'run_test')
        """,
        (domain, url, url, page_type, priority, retained),
    ).lastrowid
    html_path = create_html(project_root, domain, index) if create_file else "data/raw/html/missing.html.gz"
    return connection.execute(
        """
        INSERT INTO scraped_pages (
            candidate_page_id,
            discovered_page_id,
            url,
            domain,
            html_path,
            scrape_status,
            retained_at_discovery
        )
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (candidate_page_id, discovered_page_id, url, domain, html_path, scrape_status),
    ).lastrowid


def populate_pages(project_root: Path, db_path: Path, retained_count: int = 8, discarded_count: int = 8) -> None:
    domains = [
        "sprechen.com",
        "museum-joanneum.at",
        "bertrandt.com",
        "consultatio.at",
        "uniqa.at",
        "regular-one.at",
        "regular-two.at",
        "regular-three.at",
    ]
    page_types = ["homepage", "about", "products", "services", "news", "careers", "negative", "unknown"]
    with sqlite3.connect(db_path) as connection:
        for index in range(retained_count):
            insert_page(
                connection,
                project_root,
                domain=domains[index % len(domains)],
                index=index,
                retained=1,
                page_type=page_types[index % len(page_types)],
                priority=10 - (index % 5),
            )
        for index in range(discarded_count):
            insert_page(
                connection,
                project_root,
                domain=domains[index % len(domains)],
                index=100 + index,
                retained=0,
                page_type=page_types[index % len(page_types)],
                priority=5 + (index % 5),
            )
        insert_page(
            connection,
            project_root,
            domain="missing-html.at",
            index=900,
            retained=0,
            page_type="unknown",
            priority=0,
            create_file=False,
        )
        insert_page(
            connection,
            project_root,
            domain="failed-fetch.at",
            index=901,
            retained=1,
            page_type="unknown",
            priority=0,
            scrape_status="timeout",
        )
        connection.commit()


def run_builder_quietly(sample: str, project_root: Path) -> tuple[dict[str, object], str]:
    output = StringIO()
    with redirect_stdout(output):
        summary = build_validation_sample(sample, project_root=project_root)
    return summary, output.getvalue()


class BuildValidationSampleTest(unittest.TestCase):
    def test_smoke_sample_selects_retained_and_discarded_and_exports_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            populate_pages(project_root, db_path)

            summary, _output = run_builder_quietly("smoke", project_root)

            self.assertEqual(summary["sample_id"], "smoke_v1")
            self.assertEqual(summary["retained_selected"], 5)
            self.assertEqual(summary["discarded_selected"], 5)
            self.assertTrue(Path(summary["sample_csv_path"]).is_file())
            self.assertTrue(Path(summary["audit_csv_path"]).is_file())

            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    """
                    SELECT retained_at_discovery, COUNT(*), COUNT(discovered_page_id),
                           SUM(CASE WHEN candidate_page_id IS NULL THEN 1 ELSE 0 END)
                    FROM retention_validation_sample
                    WHERE sample_id = 'smoke_v1'
                    GROUP BY retained_at_discovery
                    ORDER BY retained_at_discovery
                    """
                ).fetchall()

            self.assertEqual(rows[0][0:3], (0, 5, 5))
            self.assertEqual(rows[0][3], 5)
            self.assertEqual(rows[1][0:3], (1, 5, 5))

    def test_rerun_is_deterministic_and_does_not_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            populate_pages(project_root, db_path)

            run_builder_quietly("smoke", project_root)
            with sqlite3.connect(db_path) as connection:
                first = connection.execute(
                    """
                    SELECT scraped_page_id, selection_reason
                    FROM retention_validation_sample
                    WHERE sample_id = 'smoke_v1'
                    ORDER BY retained_at_discovery, domain, scraped_page_id
                    """
                ).fetchall()

            run_builder_quietly("smoke", project_root)
            with sqlite3.connect(db_path) as connection:
                second = connection.execute(
                    """
                    SELECT scraped_page_id, selection_reason
                    FROM retention_validation_sample
                    WHERE sample_id = 'smoke_v1'
                    ORDER BY retained_at_discovery, domain, scraped_page_id
                    """
                ).fetchall()
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM retention_validation_sample WHERE sample_id = 'smoke_v1'"
                ).fetchone()[0]

            self.assertEqual(first, second)
            self.assertEqual(row_count, 10)

    def test_underfilled_sample_warns_and_gets_as_close_as_possible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root, smoke_target=5)
            db_path = init_database(project_root)
            populate_pages(project_root, db_path, retained_count=2, discarded_count=1)

            summary, output = run_builder_quietly("smoke", project_root)

            self.assertEqual(summary["retained_selected"], 2)
            self.assertEqual(summary["discarded_selected"], 1)
            self.assertFalse(summary["target_achieved"])
            self.assertIn("WARNING:", output)
            self.assertIn("Target sample size not achieved.", output)

    def test_domain_cap_relaxes_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root, smoke_target=5)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                for index in range(6):
                    insert_page(
                        connection,
                        project_root,
                        domain="single-domain.at",
                        index=index,
                        retained=1,
                        page_type="unknown",
                        priority=1,
                    )
                    insert_page(
                        connection,
                        project_root,
                        domain="single-domain.at",
                        index=100 + index,
                        retained=0,
                        page_type="unknown",
                        priority=1,
                    )
                connection.commit()

            summary, _output = run_builder_quietly("smoke", project_root)

            self.assertEqual(summary["retained_selected"], 5)
            self.assertEqual(summary["discarded_selected"], 5)
            self.assertTrue(summary["domain_cap_relaxed"])
            with sqlite3.connect(db_path) as connection:
                reasons = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT selection_reason
                        FROM retention_validation_sample
                        WHERE sample_id = 'smoke_v1'
                        """
                    ).fetchall()
                }
            self.assertTrue(any(reason.endswith("_relaxed_domain_cap") for reason in reasons))

    def test_audit_csv_contains_group_balance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            populate_pages(project_root, db_path)

            summary, _output = run_builder_quietly("smoke", project_root)

            with Path(summary["audit_csv_path"]).open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertEqual({row["group"] for row in rows}, {"retained", "discarded"})
            self.assertIn("average_priority", rows[0])
            self.assertIn("top_domains", rows[0])
            self.assertIn("top_page_types", rows[0])


if __name__ == "__main__":
    unittest.main()
