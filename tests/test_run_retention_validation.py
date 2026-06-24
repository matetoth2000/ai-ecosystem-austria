from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.validation.run_retention_validation import (
    output_schema,
    response_format,
    run_retention_validation,
    user_prompt,
)


SUCCESS_JSON = {
    "firm_relevance_score": 4,
    "firm_relevance_label": "high",
    "page_category_llm": "products_services",
    "keep_for_firm_profile": "yes",
    "reason_short": "The page describes concrete services.",
    "evidence_snippet": "Services for industrial clients",
    "language": "en",
}

SUCCESS_JSON_V2 = {
    **SUCCESS_JSON,
    "ecosystem_relevance_score": 3,
    "ecosystem_relevance_label": "medium",
    "ai_ecosystem_role": "unclear",
    "ai_evidence_strength": "weak",
    "ai_relevance_reason": "The page describes digital services but no explicit AI activity.",
}


class FakeUsage:
    input_tokens = 100
    output_tokens = 30
    total_tokens = 130


class FakeResponse:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.usage = FakeUsage()


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return FakeResponse(output)


class FakeClient:
    def __init__(self, outputs):
        self.responses = FakeResponses(outputs)


def write_temp_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "db").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "validation:",
                "  model: gpt-5-mini",
                "  prompt_version: v2",
                "  temperature: 0",
                "  retry_count: 0",
                "  request_timeout_seconds: 60",
            ]
        ),
        encoding="utf-8",
    )


def insert_validation_input(
    connection: sqlite3.Connection,
    *,
    sample_id: str = "smoke_v1",
    domain: str = "example.at",
    index: int = 1,
    retained: int = 1,
) -> int:
    connection.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain,))
    url = f"https://{domain}/page-{index}"
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
        VALUES (?, ?, ?, 'homepage', 'products', 6, ?, 'run_test')
        """,
        (domain, url, url, retained),
    ).lastrowid
    candidate_page_id = None
    if retained:
        candidate_page_id = connection.execute(
            """
            INSERT INTO candidate_pages (
                domain, url, normalized_url, discovery_method, page_type_guess, priority
            )
            VALUES (?, ?, ?, 'homepage', 'products', 6)
            """,
            (domain, url, url),
        ).lastrowid
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
        VALUES (?, ?, ?, ?, 'data/raw/html/example.html.gz', 'success', ?, ?)
        """,
        (candidate_page_id, discovered_page_id, url, domain, "Title", "Meta"),
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
        VALUES (?, 'v1', 'smoke', ?, ?, ?, ?, ?, ?, 'products', 6, 'homepage', 'test')
        """,
        (sample_id, scraped_page_id, discovered_page_id, candidate_page_id, domain, url, retained),
    )
    connection.execute(
        """
        INSERT INTO retention_validation_inputs (
            sample_id,
            scraped_page_id,
            discovered_page_id,
            domain,
            url,
            title,
            meta_description,
            page_type_guess,
            retained_at_discovery,
            visible_text_length,
            preview_char_count,
            visible_text_preview
        )
        VALUES (?, ?, ?, ?, ?, 'Title', 'Meta', 'products', ?, 80, 80, ?)
        """,
        (
            sample_id,
            scraped_page_id,
            discovered_page_id,
            domain,
            url,
            retained,
            "This company provides products and services for industrial customers.",
        ),
    )
    return scraped_page_id


def run_quietly(*args, **kwargs):
    output = StringIO()
    with redirect_stdout(output):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            summary = run_retention_validation(*args, **kwargs)
    return summary, output.getvalue()


class RunRetentionValidationTest(unittest.TestCase):
    def test_schema_and_prompt_include_required_fields(self) -> None:
        schema = output_schema()
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["firm_relevance_score"]["enum"], [1, 2, 3, 4, 5])
        self.assertIn("products_services", schema["properties"]["page_category_llm"]["enum"])
        self.assertIn("ecosystem_relevance_score", schema["properties"])
        self.assertIn("ai_service_provider", schema["properties"]["ai_ecosystem_role"]["enum"])
        self.assertNotIn("ecosystem_relevance_score", output_schema("v1")["properties"])
        self.assertEqual(response_format()["format"]["type"], "json_schema")

    def test_successful_response_inserts_result_and_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection)
                connection.commit()

            fake_client = FakeClient([json.dumps(SUCCESS_JSON_V2)])
            summary, _output = run_quietly("smoke_v1", project_root=project_root, client=fake_client)

            self.assertEqual(summary["pages_completed"], 1)
            self.assertEqual(summary["pages_failed"], 0)
            self.assertEqual(summary["input_tokens"], 100)
            self.assertEqual(summary["output_tokens"], 30)
            prompt = fake_client.responses.calls[0]["input"][1]["content"]
            self.assertIn("URL: https://example.at/page-1", prompt)
            self.assertIn("Discovery retention group: retained", prompt)
            self.assertIn("Question 2", prompt)
            self.assertIn("Do not assume AI involvement", prompt)

            with sqlite3.connect(db_path) as connection:
                result = connection.execute(
                    """
                    SELECT sample_id, validation_run_id, firm_relevance_score,
                           firm_relevance_label, keep_for_firm_profile, run_status,
                           ecosystem_relevance_score, ecosystem_relevance_label,
                           ai_ecosystem_role, ai_evidence_strength,
                           input_tokens, output_tokens, total_tokens
                    FROM retention_validation_results
                    """
                ).fetchone()
                run = connection.execute(
                    """
                    SELECT sample_id, pages_completed, pages_failed, input_tokens,
                           output_tokens, total_tokens, status
                    FROM retention_validation_runs
                    """
                ).fetchone()

            self.assertEqual(result[0], "smoke_v1")
            self.assertEqual(result[1], summary["run_id"])
            self.assertEqual(result[2:6], (4.0, "high", "yes", "success"))
            self.assertEqual(result[6:10], (3.0, "medium", "unclear", "weak"))
            self.assertEqual(result[10:13], (100, 30, 130))
            self.assertEqual(run, ("smoke_v1", 1, 0, 100, 30, 130, "completed"))

    def test_api_failure_records_failed_result_and_finalizes_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection)
                connection.commit()

            fake_client = FakeClient([RuntimeError("api failed")])
            summary, _output = run_quietly("smoke_v1", project_root=project_root, client=fake_client)

            self.assertEqual(summary["pages_completed"], 0)
            self.assertEqual(summary["pages_failed"], 1)
            with sqlite3.connect(db_path) as connection:
                result = connection.execute(
                    "SELECT run_status, error_message FROM retention_validation_results"
                ).fetchone()
                run_status = connection.execute("SELECT status FROM retention_validation_runs").fetchone()[0]
            self.assertEqual(result[0], "failed")
            self.assertIn("api failed", result[1])
            self.assertEqual(run_status, "completed_with_errors")

    def test_invalid_json_records_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection)
                connection.commit()

            fake_client = FakeClient(["not json"])
            summary, _output = run_quietly("smoke_v1", project_root=project_root, client=fake_client)

            self.assertEqual(summary["pages_failed"], 1)
            with sqlite3.connect(db_path) as connection:
                message = connection.execute(
                    "SELECT error_message FROM retention_validation_results"
                ).fetchone()[0]
            self.assertIn("not valid JSON", message)

    def test_skip_existing_success_and_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                scraped_page_id = insert_validation_input(connection)
                connection.execute(
                    """
                    INSERT INTO retention_validation_results (
                        sample_id, scraped_page_id, prompt_version, model_name,
                        run_status, firm_relevance_score
                    )
                    VALUES ('smoke_v1', ?, 'v2', 'gpt-5-mini', 'success', 5)
                    """,
                    (scraped_page_id,),
                )
                connection.commit()

            fake_client = FakeClient([json.dumps(SUCCESS_JSON_V2)])
            with self.assertRaisesRegex(ValueError, "No validation input rows"):
                run_quietly("smoke_v1", project_root=project_root, client=fake_client)

            summary, _output = run_quietly(
                "smoke_v1",
                project_root=project_root,
                client=fake_client,
                force_refresh=True,
            )
            self.assertEqual(summary["pages_completed"], 1)
            with sqlite3.connect(db_path) as connection:
                row_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]
            self.assertEqual(row_count, 1)

    def test_page_limit_selects_deterministic_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection, domain="b.example", index=2)
                insert_validation_input(connection, domain="a.example", index=1)
                connection.commit()

            fake_client = FakeClient([json.dumps(SUCCESS_JSON_V2)])
            summary, _output = run_quietly(
                "smoke_v1",
                project_root=project_root,
                client=fake_client,
                page_limit=1,
            )

            self.assertEqual(summary["pages_selected"], 1)
            self.assertIn("https://a.example/page-1", fake_client.responses.calls[0]["input"][1]["content"])

    def test_missing_api_key_fails_before_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection)
                connection.commit()

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Missing OPENAI_API_KEY"):
                    run_retention_validation(
                        "smoke_v1",
                        project_root=project_root,
                        client=FakeClient([json.dumps(SUCCESS_JSON_V2)]),
                    )
            with sqlite3.connect(db_path) as connection:
                result_count = connection.execute("SELECT COUNT(*) FROM retention_validation_results").fetchone()[0]
                run_count = connection.execute("SELECT COUNT(*) FROM retention_validation_runs").fetchone()[0]
            self.assertEqual(result_count, 0)
            self.assertEqual(run_count, 0)

    def test_prompt_version_v1_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_temp_config(project_root)
            db_path = init_database(project_root)
            with sqlite3.connect(db_path) as connection:
                insert_validation_input(connection)
                connection.commit()

            fake_client = FakeClient([json.dumps(SUCCESS_JSON)])
            summary, _output = run_quietly(
                "smoke_v1",
                project_root=project_root,
                client=fake_client,
                prompt_version_override="v1",
            )

            self.assertEqual(summary["pages_completed"], 1)
            prompt = fake_client.responses.calls[0]["input"][1]["content"]
            self.assertNotIn("Question 2", prompt)
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    """
                    SELECT prompt_version, ecosystem_relevance_score, ai_ecosystem_role
                    FROM retention_validation_results
                    """
                ).fetchone()
            self.assertEqual(row, ("v1", None, None))


if __name__ == "__main__":
    unittest.main()
