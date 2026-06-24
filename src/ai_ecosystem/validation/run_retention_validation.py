"""Run OpenAI retention scoring for validation inputs."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_ecosystem.db.init_db import (
    ensure_validation_schema,
    find_project_root,
    load_config,
    resolve_database_path,
)
from ai_ecosystem.validation.config import load_openai_api_key, load_validation_config


FIRM_RELEVANCE_LABELS = ("very_low", "low", "medium", "high", "very_high")
PAGE_CATEGORY_VALUES = (
    "company_description",
    "products_services",
    "technology_capabilities",
    "careers_jobs",
    "news_press_blog",
    "contact_location",
    "legal_compliance",
    "investor_corporate_admin",
    "low_content_or_navigation",
    "other",
)
KEEP_VALUES = ("yes", "no", "uncertain")
AI_ECOSYSTEM_ROLE_VALUES = (
    "ai_developer_vendor",
    "ai_service_provider",
    "ai_consultancy",
    "ai_research_education",
    "ai_adopter_user",
    "ai_investor_support",
    "government_regulator",
    "not_ai_related",
    "unclear",
)
AI_EVIDENCE_STRENGTH_VALUES = ("none", "weak", "moderate", "strong")


@dataclass(frozen=True)
class ValidationInput:
    sample_id: str
    scraped_page_id: int
    candidate_page_id: int | None
    domain: str
    url: str
    title: str | None
    meta_description: str | None
    page_type_guess: str
    retained_at_discovery: int
    visible_text_preview: str
    input_chars: int


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def make_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"validation_{timestamp}_{uuid.uuid4().hex[:8]}"


def base_output_properties() -> dict[str, Any]:
    return {
        "firm_relevance_score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "firm_relevance_label": {"type": "string", "enum": list(FIRM_RELEVANCE_LABELS)},
        "page_category_llm": {"type": "string", "enum": list(PAGE_CATEGORY_VALUES)},
        "keep_for_firm_profile": {"type": "string", "enum": list(KEEP_VALUES)},
        "reason_short": {"type": "string"},
        "evidence_snippet": {"type": "string"},
        "language": {"type": "string"},
    }


def output_schema(prompt_version: str = "v2") -> dict[str, Any]:
    properties = base_output_properties()
    if prompt_version == "v2":
        properties.update(
            {
                "ecosystem_relevance_score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                "ecosystem_relevance_label": {"type": "string", "enum": list(FIRM_RELEVANCE_LABELS)},
                "ai_ecosystem_role": {"type": "string", "enum": list(AI_ECOSYSTEM_ROLE_VALUES)},
                "ai_evidence_strength": {"type": "string", "enum": list(AI_EVIDENCE_STRENGTH_VALUES)},
                "ai_relevance_reason": {"type": "string"},
            }
        )
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def system_prompt(prompt_version: str = "v2") -> str:
    if prompt_version == "v1":
        return (
            "You evaluate web pages for firm profiling research. "
            "Return JSON only. The target is business usefulness for understanding what the company "
            "actually does: activities, products, services, technologies, capabilities, positioning, "
            "company description, or strategic context. AI relevance is not the target."
        )
    return (
        "You evaluate web pages for Austrian AI ecosystem research. Return JSON only. "
        "Evaluate two separate dimensions: firm representativeness and AI-ecosystem usefulness. "
        "Evidence that an organization is not meaningfully connected to AI is valuable. "
        "Use only information visible on the page. Do not infer AI activity without evidence."
    )


def user_prompt(page: ValidationInput, prompt_version: str = "v2") -> str:
    retained_label = "retained" if page.retained_at_discovery else "discarded"
    common = [
        f"URL: {page.url}",
        f"Title: {page.title or ''}",
        f"Meta description: {page.meta_description or ''}",
        f"Discovery page type guess: {page.page_type_guess}",
        f"Discovery retention group: {retained_label}",
        "",
        "Visible text preview:",
        page.visible_text_preview,
    ]
    if prompt_version == "v1":
        return "\n".join(
            [
                "Question: How useful is this page for understanding what the company actually does?",
                "",
                "Scoring:",
                "1 = Not useful for understanding the firm.",
                "2 = Weakly useful.",
                "3 = Somewhat useful.",
                "4 = Useful.",
                "5 = Highly useful for understanding products, services, activities, technologies, "
                "capabilities, or positioning.",
                "",
                *common,
            ]
        )
    return "\n".join(
        [
            "Question 1: How useful is this page for understanding what the organization does?",
            "Question 2: How useful is this page for determining whether the organization participates "
            "in the AI ecosystem and what role it plays?",
            "",
            "Important instructions:",
            "- Evidence that the organization is NOT meaningfully connected to AI is valuable.",
            "- Do not assume AI involvement.",
            "- Use only information visible on the page.",
            "- Do not infer AI activity without evidence.",
            "",
            "Firm relevance scoring:",
            "1 = Not useful for understanding the firm.",
            "2 = Weakly useful.",
            "3 = Somewhat useful.",
            "4 = Useful.",
            "5 = Highly useful for understanding products, services, activities, technologies, "
            "capabilities, or positioning.",
            "",
            "AI ecosystem relevance scoring:",
            "1 = Little or no information relevant to determining AI involvement; examples include "
            "opening hours, directions, contact pages, or generic legal pages.",
            "3 = Partial clues about technologies, products, services, or digital capabilities, but "
            "AI connection is uncertain.",
            "5 = Strong evidence about AI products, AI services, AI research, AI deployment, AI "
            "partnerships, AI capabilities, AI strategy, or AI ecosystem participation.",
            "",
            *common,
        ]
    )


def response_format(prompt_version: str = "v2") -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "retention_validation_result",
            "strict": True,
            "schema": output_schema(prompt_version),
        }
    }


def create_openai_client(api_key: str, timeout_seconds: int):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The OpenAI Python SDK is not installed. Run `python -m pip install -e .` "
            "after adding the project dependency."
        ) from exc

    return OpenAI(api_key=api_key, timeout=timeout_seconds)


def get_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    output = getattr(response, "output", None)
    if output:
        parts: list[str] = []
        for item in output:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    parts.append(text)
        if parts:
            return "".join(parts)

    if isinstance(response, dict):
        if response.get("output_text"):
            return str(response["output_text"])
        parts = []
        for item in response.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("text"):
                    parts.append(content["text"])
        if parts:
            return "".join(parts)

    raise ValueError("OpenAI response did not contain output text")


def get_usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return TokenUsage()

    def value(name: str) -> int | None:
        if isinstance(usage, dict):
            raw = usage.get(name)
        else:
            raw = getattr(usage, name, None)
        return int(raw) if raw is not None else None

    return TokenUsage(
        input_tokens=value("input_tokens"),
        output_tokens=value("output_tokens"),
        total_tokens=value("total_tokens"),
    )


def validate_model_output(raw_text: str, prompt_version: str = "v2") -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output was not valid JSON: {exc}") from exc

    required = set(output_schema(prompt_version)["required"])
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"Model output missing required fields: {missing}")

    score = data["firm_relevance_score"]
    if not isinstance(score, int) or score not in {1, 2, 3, 4, 5}:
        raise ValueError("firm_relevance_score must be an integer from 1 to 5")
    if data["firm_relevance_label"] not in FIRM_RELEVANCE_LABELS:
        raise ValueError("firm_relevance_label has an invalid value")
    if data["page_category_llm"] not in PAGE_CATEGORY_VALUES:
        raise ValueError("page_category_llm has an invalid value")
    if data["keep_for_firm_profile"] not in KEEP_VALUES:
        raise ValueError("keep_for_firm_profile has an invalid value")
    for field in ("reason_short", "evidence_snippet", "language"):
        if not isinstance(data[field], str):
            raise ValueError(f"{field} must be a string")
    if prompt_version == "v2":
        ecosystem_score = data["ecosystem_relevance_score"]
        if not isinstance(ecosystem_score, int) or ecosystem_score not in {1, 2, 3, 4, 5}:
            raise ValueError("ecosystem_relevance_score must be an integer from 1 to 5")
        if data["ecosystem_relevance_label"] not in FIRM_RELEVANCE_LABELS:
            raise ValueError("ecosystem_relevance_label has an invalid value")
        if data["ai_ecosystem_role"] not in AI_ECOSYSTEM_ROLE_VALUES:
            raise ValueError("ai_ecosystem_role has an invalid value")
        if data["ai_evidence_strength"] not in AI_EVIDENCE_STRENGTH_VALUES:
            raise ValueError("ai_evidence_strength has an invalid value")
        if not isinstance(data["ai_relevance_reason"], str):
            raise ValueError("ai_relevance_reason must be a string")
    return data


def call_openai(
    client: Any,
    page: ValidationInput,
    *,
    model: str,
    temperature: float,
    prompt_version: str,
) -> tuple[dict[str, Any], str, TokenUsage]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt(prompt_version)},
            {"role": "user", "content": user_prompt(page, prompt_version)},
        ],
        "text": response_format(prompt_version),
    }
    if not model.startswith("gpt-5"):
        request_kwargs["temperature"] = temperature

    response = client.responses.create(**request_kwargs)
    raw_text = get_output_text(response)
    parsed = validate_model_output(raw_text, prompt_version)
    return parsed, json.dumps(parsed, ensure_ascii=False, sort_keys=True), get_usage(response)


def load_pages(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    model_name: str,
    prompt_version: str,
    page_limit: int | None,
    force_refresh: bool = False,
) -> tuple[list[ValidationInput], int]:
    skipped = connection.execute(
        """
        SELECT COUNT(*)
        FROM retention_validation_inputs AS rvi
        WHERE rvi.sample_id = ?
          AND EXISTS (
              SELECT 1
              FROM retention_validation_results AS rvr
              WHERE rvr.sample_id = rvi.sample_id
                AND rvr.scraped_page_id = rvi.scraped_page_id
                AND rvr.prompt_version = ?
                AND rvr.model_name = ?
                AND rvr.run_status = 'success'
          )
        """,
        (sample_id, prompt_version, model_name),
    ).fetchone()[0]
    skip_filter = ""
    if not force_refresh:
        skip_filter = """
          AND NOT EXISTS (
              SELECT 1
              FROM retention_validation_results AS rvr
              WHERE rvr.sample_id = rvi.sample_id
                AND rvr.scraped_page_id = rvi.scraped_page_id
                AND rvr.prompt_version = ?
                AND rvr.model_name = ?
                AND rvr.run_status = 'success'
          )
        """
        query_params: tuple[Any, ...] = (sample_id, prompt_version, model_name)
    else:
        query_params = (sample_id,)

    rows = connection.execute(
        """
        SELECT
            rvi.sample_id,
            rvi.scraped_page_id,
            rvs.candidate_page_id,
            rvi.domain,
            rvi.url,
            rvi.title,
            rvi.meta_description,
            COALESCE(rvi.page_type_guess, 'unknown'),
            rvi.retained_at_discovery,
            COALESCE(rvi.visible_text_preview, ''),
            COALESCE(rvi.preview_char_count, 0)
        FROM retention_validation_inputs AS rvi
        LEFT JOIN retention_validation_sample AS rvs
            ON rvs.sample_id = rvi.sample_id
           AND rvs.scraped_page_id = rvi.scraped_page_id
        WHERE rvi.sample_id = ?
        """ + skip_filter + """
        ORDER BY rvi.retained_at_discovery DESC, rvi.domain, rvi.scraped_page_id
        """,
        query_params,
    ).fetchall()
    if page_limit is not None:
        rows = rows[:page_limit]

    pages = [
        ValidationInput(
            sample_id=row[0],
            scraped_page_id=row[1],
            candidate_page_id=row[2],
            domain=row[3],
            url=row[4],
            title=row[5],
            meta_description=row[6],
            page_type_guess=row[7],
            retained_at_discovery=row[8],
            visible_text_preview=row[9],
            input_chars=row[10],
        )
        for row in rows
    ]
    return pages, skipped


def insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    sample_id: str,
    sample_type: str,
    model_name: str,
    prompt_version: str,
    pages_selected: int,
    estimated_input_chars: int,
) -> None:
    connection.execute(
        """
        INSERT INTO retention_validation_runs (
            run_id,
            sample_id,
            sample_type,
            model_name,
            prompt_version,
            pages_selected,
            pages_completed,
            pages_failed,
            estimated_input_chars,
            input_tokens,
            output_tokens,
            total_tokens,
            started_at,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 0, ?, 'running')
        """,
        (
            run_id,
            sample_id,
            sample_type,
            model_name,
            prompt_version,
            pages_selected,
            estimated_input_chars,
            utc_now(),
        ),
    )


def delete_prior_result(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    scraped_page_id: int,
    prompt_version: str,
    model_name: str,
) -> None:
    connection.execute(
        """
        DELETE FROM retention_validation_results
        WHERE sample_id = ?
          AND scraped_page_id = ?
          AND prompt_version = ?
          AND model_name = ?
        """,
        (sample_id, scraped_page_id, prompt_version, model_name),
    )


def insert_result(
    connection: sqlite3.Connection,
    *,
    page: ValidationInput,
    run_id: str,
    prompt_version: str,
    model_name: str,
    input_chars: int,
    output_json: str | None,
    parsed: dict[str, Any] | None,
    run_status: str,
    error_message: str | None,
    usage: TokenUsage,
) -> None:
    connection.execute(
        """
        INSERT INTO retention_validation_results (
            sample_id,
            validation_run_id,
            scraped_page_id,
            candidate_page_id,
            domain,
            url,
            retained_at_discovery,
            page_type_guess,
            prompt_version,
            model_name,
            input_chars,
            output_json,
            firm_relevance_score,
            firm_relevance_label,
            page_category_llm,
            keep_for_firm_profile,
            reason_short,
            evidence_snippet,
            language,
            run_status,
            error_message,
            input_tokens,
            output_tokens,
            total_tokens,
            ecosystem_relevance_score,
            ecosystem_relevance_label,
            ai_ecosystem_role,
            ai_evidence_strength,
            ai_relevance_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            page.sample_id,
            run_id,
            page.scraped_page_id,
            page.candidate_page_id,
            page.domain,
            page.url,
            page.retained_at_discovery,
            page.page_type_guess,
            prompt_version,
            model_name,
            input_chars,
            output_json,
            parsed.get("firm_relevance_score") if parsed else None,
            parsed.get("firm_relevance_label") if parsed else None,
            parsed.get("page_category_llm") if parsed else None,
            parsed.get("keep_for_firm_profile") if parsed else None,
            parsed.get("reason_short") if parsed else None,
            parsed.get("evidence_snippet") if parsed else None,
            parsed.get("language") if parsed else None,
            run_status,
            error_message,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            parsed.get("ecosystem_relevance_score") if parsed else None,
            parsed.get("ecosystem_relevance_label") if parsed else None,
            parsed.get("ai_ecosystem_role") if parsed else None,
            parsed.get("ai_evidence_strength") if parsed else None,
            parsed.get("ai_relevance_reason") if parsed else None,
        ),
    )


def finalize_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    pages_completed: int,
    pages_failed: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    status: str,
    error_message: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE retention_validation_runs
        SET completed_at = ?,
            pages_completed = ?,
            pages_failed = ?,
            input_tokens = ?,
            output_tokens = ?,
            total_tokens = ?,
            status = ?,
            error_message = ?
        WHERE run_id = ?
        """,
        (
            utc_now(),
            pages_completed,
            pages_failed,
            input_tokens,
            output_tokens,
            total_tokens,
            status,
            error_message,
            run_id,
        ),
    )


def sample_type_for(connection: sqlite3.Connection, sample_id: str) -> str:
    row = connection.execute(
        "SELECT sample_type FROM retention_validation_sample WHERE sample_id = ? LIMIT 1",
        (sample_id,),
    ).fetchone()
    if row:
        return row[0]
    return sample_id


def run_retention_validation(
    sample_id: str,
    *,
    project_root: Path | None = None,
    page_limit: int | None = None,
    force_refresh: bool = False,
    model_override: str | None = None,
    prompt_version_override: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    root = project_root or find_project_root()
    config = load_config(root)
    validation = load_validation_config(root)
    model_name = model_override or validation.get("model")
    if not model_name:
        raise ValueError("Missing validation.model in config/config.yaml")
    prompt_version = prompt_version_override or str(validation.get("prompt_version", "v2"))
    if prompt_version not in {"v1", "v2"}:
        raise ValueError("prompt_version must be v1 or v2")
    temperature = float(validation.get("temperature", 0))
    retry_count = int(validation.get("retry_count", 2))
    timeout_seconds = int(validation.get("request_timeout_seconds", 60))

    api_key = load_openai_api_key(root)
    if client is None:
        client = create_openai_client(api_key, timeout_seconds)

    db_path = resolve_database_path(root, config)
    run_id = make_run_id()
    pages_completed = 0
    pages_failed = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    examples: list[dict[str, Any]] = []

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_validation_schema(connection)
        pages, skipped = load_pages(
            connection,
            sample_id=sample_id,
            model_name=model_name,
            prompt_version=prompt_version,
            page_limit=page_limit,
            force_refresh=force_refresh,
        )
        if not pages:
            raise ValueError(f"No validation input rows to score for sample_id='{sample_id}'")
        if force_refresh:
            for page in pages:
                delete_prior_result(
                    connection,
                    sample_id=sample_id,
                    scraped_page_id=page.scraped_page_id,
                    prompt_version=prompt_version,
                    model_name=model_name,
                )
        estimated_input_chars = sum(
            len(system_prompt(prompt_version)) + len(user_prompt(page, prompt_version))
            for page in pages
        )
        insert_run(
            connection,
            run_id=run_id,
            sample_id=sample_id,
            sample_type=sample_type_for(connection, sample_id),
            model_name=model_name,
            prompt_version=prompt_version,
            pages_selected=len(pages),
            estimated_input_chars=estimated_input_chars,
        )
        connection.commit()

        try:
            for index, page in enumerate(pages, start=1):
                delete_prior_result(
                    connection,
                    sample_id=sample_id,
                    scraped_page_id=page.scraped_page_id,
                    prompt_version=prompt_version,
                    model_name=model_name,
                )
                input_chars = len(user_prompt(page, prompt_version)) + len(system_prompt(prompt_version))
                parsed = None
                output_json = None
                usage = TokenUsage()
                error_message = None
                for attempt in range(retry_count + 1):
                    try:
                        parsed, output_json, usage = call_openai(
                            client,
                            page,
                            model=model_name,
                            temperature=temperature,
                            prompt_version=prompt_version,
                        )
                        break
                    except Exception as exc:  # noqa: BLE001 - each page should fail independently.
                        error_message = str(exc)
                        if attempt < retry_count:
                            time.sleep(min(2**attempt, 5))
                        else:
                            parsed = None
                            output_json = None
                if parsed is None:
                    pages_failed += 1
                    insert_result(
                        connection,
                        page=page,
                        run_id=run_id,
                        prompt_version=prompt_version,
                        model_name=model_name,
                        input_chars=input_chars,
                        output_json=output_json,
                        parsed=None,
                        run_status="failed",
                        error_message=error_message,
                        usage=usage,
                    )
                else:
                    pages_completed += 1
                    input_tokens += usage.input_tokens or 0
                    output_tokens += usage.output_tokens or 0
                    total_tokens += usage.total_tokens or 0
                    insert_result(
                        connection,
                        page=page,
                        run_id=run_id,
                        prompt_version=prompt_version,
                        model_name=model_name,
                        input_chars=input_chars,
                        output_json=output_json,
                        parsed=parsed,
                        run_status="success",
                        error_message=None,
                        usage=usage,
                    )
                    if len(examples) < 3:
                        examples.append(parsed)
                connection.commit()
                print(
                    f"Processed {index}/{len(pages)} "
                    f"completed={pages_completed} failed={pages_failed}",
                    flush=True,
                )
            status = "completed" if pages_failed == 0 else "completed_with_errors"
            finalize_run(
                connection,
                run_id=run_id,
                pages_completed=pages_completed,
                pages_failed=pages_failed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                status=status,
            )
            connection.commit()
        except KeyboardInterrupt:
            finalize_run(
                connection,
                run_id=run_id,
                pages_completed=pages_completed,
                pages_failed=pages_failed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                status="interrupted",
                error_message="KeyboardInterrupt",
            )
            connection.commit()
            raise

        score_distribution = dict(
            connection.execute(
                """
                SELECT firm_relevance_score, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY firm_relevance_score
                ORDER BY firm_relevance_score
                """,
                (run_id,),
            ).fetchall()
        )
        keep_distribution = dict(
            connection.execute(
                """
                SELECT keep_for_firm_profile, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY keep_for_firm_profile
                ORDER BY keep_for_firm_profile
                """,
                (run_id,),
            ).fetchall()
        )
        category_distribution = dict(
            connection.execute(
                """
                SELECT page_category_llm, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY page_category_llm
                ORDER BY COUNT(*) DESC
                """,
                (run_id,),
            ).fetchall()
        )
        ecosystem_score_distribution = dict(
            connection.execute(
                """
                SELECT ecosystem_relevance_score, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY ecosystem_relevance_score
                ORDER BY ecosystem_relevance_score
                """,
                (run_id,),
            ).fetchall()
        )
        ai_role_distribution = dict(
            connection.execute(
                """
                SELECT ai_ecosystem_role, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY ai_ecosystem_role
                ORDER BY COUNT(*) DESC
                """,
                (run_id,),
            ).fetchall()
        )
        ai_evidence_distribution = dict(
            connection.execute(
                """
                SELECT ai_evidence_strength, COUNT(*)
                FROM retention_validation_results
                WHERE validation_run_id = ? AND run_status = 'success'
                GROUP BY ai_evidence_strength
                ORDER BY COUNT(*) DESC
                """,
                (run_id,),
            ).fetchall()
        )

    summary = {
        "run_id": run_id,
        "sample_id": sample_id,
        "model_name": model_name,
        "pages_selected": len(pages),
        "pages_completed": pages_completed,
        "pages_failed": pages_failed,
        "pages_skipped": skipped,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": "not calculated",
        "score_distribution": score_distribution,
        "ecosystem_score_distribution": ecosystem_score_distribution,
        "keep_distribution": keep_distribution,
        "category_distribution": category_distribution,
        "ai_role_distribution": ai_role_distribution,
        "ai_evidence_distribution": ai_evidence_distribution,
        "examples": examples,
    }
    print_summary(summary)
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("Retention validation summary")
    print(f"  Run ID: {summary['run_id']}")
    print(f"  Sample: {summary['sample_id']}")
    print(f"  Model: {summary['model_name']}")
    print(f"  Pages selected: {summary['pages_selected']}")
    print(f"  Pages completed: {summary['pages_completed']}")
    print(f"  Pages failed: {summary['pages_failed']}")
    print(f"  Pages skipped: {summary['pages_skipped']}")
    print(f"  Input tokens: {summary['input_tokens']}")
    print(f"  Output tokens: {summary['output_tokens']}")
    print(f"  Total tokens: {summary['total_tokens']}")
    print("  Dollar cost estimate: not calculated")
    print(f"  Score distribution: {summary['score_distribution']}")
    print(f"  Ecosystem score distribution: {summary['ecosystem_score_distribution']}")
    print(f"  Keep distribution: {summary['keep_distribution']}")
    print(f"  Category distribution: {summary['category_distribution']}")
    print(f"  AI role distribution: {summary['ai_role_distribution']}")
    print(f"  AI evidence distribution: {summary['ai_evidence_distribution']}")
    print("  Example outputs:")
    for example in summary["examples"]:
        print(f"    {json.dumps(example, ensure_ascii=False, sort_keys=True)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenAI retention validation scoring.")
    parser.add_argument("--sample", required=True, help="Validation sample_id, e.g. smoke_v1")
    parser.add_argument("--page-limit", type=int)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--prompt-version", choices=["v1", "v2"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_retention_validation(
        args.sample,
        page_limit=args.page_limit,
        force_refresh=args.force_refresh,
        model_override=args.model,
        prompt_version_override=args.prompt_version,
    )


if __name__ == "__main__":
    main()
