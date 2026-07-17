"""Run automated OpenAI evaluation over scoring review packages."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from ai_ecosystem.db.init_db import find_project_root
from ai_ecosystem.validation.config import load_openai_api_key, load_validation_config
from ai_ecosystem.validation.run_retention_validation import TokenUsage, create_openai_client, get_output_text, get_usage


PAGE_CATEGORY_VALUES = (
    "company_profile",
    "product_service",
    "technology",
    "innovation",
    "partnership",
    "news",
    "press_release",
    "legal_privacy",
    "careers",
    "blog",
    "history",
    "biography",
    "customer_story",
    "third_party_content",
    "other",
)
ASSESSMENT_VALUES = ("yes", "no", "uncertain")
QUALITY_VALUES = (1, 2, 3, 4, 5)
ISSUE_TYPES = (
    "third_party_content",
    "news_or_press",
    "legal_page",
    "career_page",
    "history_page",
    "biography",
    "customer_story",
    "generic_marketing",
    "duplicated_template",
    "partner_ambiguity",
    "other",
)
ISSUE_SEVERITY_VALUES = ("minor", "major", "critical")

MODEL_PRICING_PER_MILLION_TOKENS = {
    "gpt-5": {"input": 1.25, "output": 10.00},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "gpt-5-chat-latest": {"input": 1.25, "output": 10.00},
}


@dataclass(frozen=True)
class EvaluationInput:
    index: int
    file: str
    dimension: str
    page_score_id: int
    scraped_page_id: int
    extracted_page_id: int
    domain: str | None
    url: str
    final_score: float
    content: str


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def sanitize_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dimension": {"type": "string", "enum": ["firm_profile", "technology", "innovation", "ecosystem"]},
            "page_category": {"type": "string", "enum": list(PAGE_CATEGORY_VALUES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "score_justified": {"type": "string", "enum": list(ASSESSMENT_VALUES)},
            "retain_for_llm": {"type": "string", "enum": list(ASSESSMENT_VALUES)},
            "evidence_quality": {"type": "integer", "enum": list(QUALITY_VALUES)},
            "company_relevance": {"type": "integer", "enum": list(QUALITY_VALUES)},
            "page_type_quality": {"type": "integer", "enum": list(QUALITY_VALUES)},
            "detected_issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
                        "severity": {"type": "string", "enum": list(ISSUE_SEVERITY_VALUES)},
                        "explanation": {"type": "string"},
                    },
                    "required": ["issue_type", "severity", "explanation"],
                },
            },
            "concise_summary": {"type": "string"},
            "suggested_scoring_improvements": {"type": "array", "items": {"type": "string"}},
            "suggested_retention_improvements": {"type": "array", "items": {"type": "string"}},
            "suggested_knowledge_base_improvements": {"type": "array", "items": {"type": "string"}},
            "suggested_page_type_heuristics": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "dimension",
            "page_category",
            "confidence",
            "score_justified",
            "retain_for_llm",
            "evidence_quality",
            "company_relevance",
            "page_type_quality",
            "detected_issues",
            "concise_summary",
            "suggested_scoring_improvements",
            "suggested_retention_improvements",
            "suggested_knowledge_base_improvements",
            "suggested_page_type_heuristics",
        ],
    }


def response_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "scoring_page_evaluation",
            "strict": True,
            "schema": response_schema(),
        }
    }


def validate_model_output(raw_text: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output was not valid JSON: {exc}") from exc
    required = set(response_schema()["required"])
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"Model output missing required fields: {missing}")
    if data["dimension"] not in {"firm_profile", "technology", "innovation", "ecosystem"}:
        raise ValueError("dimension has invalid value")
    if data["page_category"] not in PAGE_CATEGORY_VALUES:
        raise ValueError("page_category has invalid value")
    if not isinstance(data["confidence"], int | float) or not 0 <= float(data["confidence"]) <= 1:
        raise ValueError("confidence must be a number from 0 to 1")
    for field in ("score_justified", "retain_for_llm"):
        if data[field] not in ASSESSMENT_VALUES:
            raise ValueError(f"{field} has invalid value")
    for field in ("evidence_quality", "company_relevance", "page_type_quality"):
        if not isinstance(data[field], int) or data[field] not in QUALITY_VALUES:
            raise ValueError(f"{field} must be an integer from 1 to 5")
    if not isinstance(data["detected_issues"], list):
        raise ValueError("detected_issues must be a list")
    for issue in data["detected_issues"]:
        if not isinstance(issue, dict):
            raise ValueError("detected_issues entries must be objects")
        if issue.get("issue_type") not in ISSUE_TYPES:
            raise ValueError("detected issue_type has invalid value")
        if issue.get("severity") not in ISSUE_SEVERITY_VALUES:
            raise ValueError("detected issue severity has invalid value")
        if not isinstance(issue.get("explanation"), str):
            raise ValueError("detected issue explanation must be a string")
    for field in (
        "concise_summary",
        "suggested_scoring_improvements",
        "suggested_retention_improvements",
        "suggested_knowledge_base_improvements",
        "suggested_page_type_heuristics",
    ):
        if field == "concise_summary":
            if not isinstance(data[field], str):
                raise ValueError("concise_summary must be a string")
        elif not isinstance(data[field], list) or not all(isinstance(item, str) for item in data[field]):
            raise ValueError(f"{field} must be a list of strings")
    return data


def build_system_prompt(evaluation_prompt: str) -> str:
    return "\n".join(
        [
            "You are evaluating scoring outputs for an Austrian AI ecosystem web-mining project.",
            "Return strict JSON only. Do not include Markdown.",
            "Use only the page review file provided by the user.",
            "",
            "Original review task:",
            evaluation_prompt,
        ]
    )


def build_user_prompt(page: EvaluationInput) -> str:
    return "\n".join(
        [
            f"Evaluate this scored page file: {page.file}",
            f"Expected dimension from manifest: {page.dimension}",
            f"URL: {page.url}",
            "",
            page.content,
        ]
    )


def estimate_cost(model: str, usage: dict[str, int | None]) -> dict[str, Any]:
    pricing = MODEL_PRICING_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        return {
            "model": model,
            "input_cost_usd": None,
            "output_cost_usd": None,
            "total_cost_usd": None,
            "pricing_note": "No local pricing estimate configured for this model.",
        }
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    input_cost = input_tokens * pricing["input"] / 1_000_000
    output_cost = output_tokens * pricing["output"] / 1_000_000
    return {
        "model": model,
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd": round(input_cost + output_cost, 6),
        "pricing_note": "Estimated from local per-1M-token constants; verify against current OpenAI billing.",
    }


def load_evaluation_inputs(evaluation_dir: Path, max_pages: int | None = None) -> tuple[dict[str, Any], str, list[EvaluationInput]]:
    manifest = read_json(evaluation_dir / "manifest.json")
    prompt = (evaluation_dir / str(manifest.get("evaluation_prompt", "evaluation_prompt.md"))).read_text(encoding="utf-8")
    items = manifest.get("page_files", [])
    if not isinstance(items, list):
        raise ValueError("manifest.json page_files must be a list")
    pages: list[EvaluationInput] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        filename = str(item["file"])
        content = (evaluation_dir / filename).read_text(encoding="utf-8")
        pages.append(
            EvaluationInput(
                index=index,
                file=filename,
                dimension=str(item.get("dimension", "")),
                page_score_id=int(item.get("page_score_id", 0)),
                scraped_page_id=int(item.get("scraped_page_id", 0)),
                extracted_page_id=int(item.get("extracted_page_id", 0)),
                domain=item.get("domain"),
                url=str(item.get("url", "")),
                final_score=float(item.get("final_score", 0)),
                content=content,
            )
        )
    if max_pages is not None:
        pages = pages[:max_pages]
    return manifest, prompt, pages


def existing_successes(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    return {
        str(item.get("file")): item
        for item in data
        if isinstance(item, dict) and item.get("status") == "success" and item.get("file")
    }


def call_openai(
    client: Any,
    page: EvaluationInput,
    *,
    system_prompt: str,
    model: str,
    temperature: float,
) -> tuple[dict[str, Any], str, TokenUsage]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_prompt(page)},
        ],
        "text": response_format(),
    }
    if not model.startswith("gpt-5"):
        request_kwargs["temperature"] = temperature
    response = client.responses.create(**request_kwargs)
    raw_text = get_output_text(response)
    parsed = validate_model_output(raw_text)
    return parsed, json.dumps(parsed, ensure_ascii=False, sort_keys=True), get_usage(response)


def evaluate_one_page(
    client: Any,
    page: EvaluationInput,
    *,
    system_prompt: str,
    model: str,
    temperature: float,
    retry_count: int,
) -> dict[str, Any]:
    started_at = utc_now()
    error_message = None
    usage = TokenUsage()
    for attempt in range(retry_count + 1):
        try:
            parsed, output_json, usage = call_openai(
                client,
                page,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
            )
            token_payload = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            }
            return {
                "status": "success",
                "file": page.file,
                "index": page.index,
                "dimension": page.dimension,
                "page_score_id": page.page_score_id,
                "scraped_page_id": page.scraped_page_id,
                "extracted_page_id": page.extracted_page_id,
                "domain": page.domain,
                "url": page.url,
                "final_score": page.final_score,
                "model": model,
                "started_at": started_at,
                "completed_at": utc_now(),
                "input_chars": len(system_prompt) + len(build_user_prompt(page)),
                "output_json": output_json,
                "parsed": parsed,
                "usage": token_payload,
                "estimated_cost": estimate_cost(model, token_payload),
                "error_message": None,
            }
        except Exception as exc:  # noqa: BLE001 - page failures must not abort the run.
            error_message = str(exc)
            if attempt < retry_count:
                time.sleep(min(2**attempt, 5))
    token_payload = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }
    return {
        "status": "failed",
        "file": page.file,
        "index": page.index,
        "dimension": page.dimension,
        "page_score_id": page.page_score_id,
        "scraped_page_id": page.scraped_page_id,
        "extracted_page_id": page.extracted_page_id,
        "domain": page.domain,
        "url": page.url,
        "final_score": page.final_score,
        "model": model,
        "started_at": started_at,
        "completed_at": utc_now(),
        "input_chars": len(system_prompt) + len(build_user_prompt(page)),
        "output_json": None,
        "parsed": None,
        "usage": token_payload,
        "estimated_cost": estimate_cost(model, token_payload),
        "error_message": error_message,
    }


def merge_results(pages: list[EvaluationInput], existing: dict[str, dict[str, Any]], new_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_file = dict(existing)
    for item in new_results:
        by_file[str(item.get("file"))] = item
    ordered = []
    for page in pages:
        item = by_file.get(page.file)
        if item:
            ordered.append(item)
    return ordered


def bool_rate(rows: list[dict[str, Any]], field: str, yes_value: str = "yes") -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get("parsed", {}).get(field) == yes_value) / len(rows), 4)


def average(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [
        float(row["parsed"][field])
        for row in rows
        if row.get("status") == "success" and row.get("parsed") and row["parsed"].get(field) is not None
    ]
    return round(mean(values), 4) if values else None


def normalize_recommendation(value: str) -> str:
    text = re.sub(r"\s+", " ", value.casefold()).strip()
    text = re.sub(r"[^a-z0-9äöüß ]+", "", text)
    return text[:180]


def recommendation_counts(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for row in rows:
        parsed = row.get("parsed") or {}
        for item in parsed.get(field, []) or []:
            if not isinstance(item, str) or not item.strip():
                continue
            key = normalize_recommendation(item)
            if not key:
                continue
            counts[key] += 1
            examples.setdefault(key, item.strip())
    return [
        {"recommendation": examples[key], "count": count}
        for key, count in counts.most_common(20)
    ]


def summarize_results(
    *,
    evaluation_dir: Path,
    output_dir: Path,
    model: str,
    results: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    success_rows = [row for row in results if row.get("status") == "success" and row.get("parsed")]
    failed_rows = [row for row in results if row.get("status") != "success"]
    issue_counter: Counter[str] = Counter()
    issue_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_counter: Counter[str] = Counter()
    by_dimension: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in success_rows:
        parsed = row["parsed"]
        category_counter[str(parsed.get("page_category"))] += 1
        by_dimension[str(row.get("dimension"))].append(row)
        for issue in parsed.get("detected_issues", []) or []:
            issue_type = str(issue.get("issue_type"))
            issue_counter[issue_type] += 1
            if len(issue_examples[issue_type]) < 3:
                issue_examples[issue_type].append(
                    {
                        "file": row.get("file"),
                        "url": row.get("url"),
                        "dimension": row.get("dimension"),
                        "severity": issue.get("severity"),
                        "explanation": issue.get("explanation"),
                    }
                )

    usage = {
        "input_tokens": sum((row.get("usage") or {}).get("input_tokens") or 0 for row in results),
        "output_tokens": sum((row.get("usage") or {}).get("output_tokens") or 0 for row in results),
        "total_tokens": sum((row.get("usage") or {}).get("total_tokens") or 0 for row in results),
    }
    per_dimension = {}
    for dimension, rows in sorted(by_dimension.items()):
        per_dimension[dimension] = {
            "pages": len(rows),
            "average_evidence_quality": average(rows, "evidence_quality"),
            "average_company_relevance": average(rows, "company_relevance"),
            "average_page_type_quality": average(rows, "page_type_quality"),
            "retain_rate": bool_rate(rows, "retain_for_llm"),
            "justified_score_rate": bool_rate(rows, "score_justified"),
        }
    summary = {
        "evaluation_dir": str(evaluation_dir),
        "output_dir": str(output_dir),
        "scoring_run_id": manifest.get("scoring_run_id"),
        "model": model,
        "generated_at": utc_now(),
        "pages_attempted": len(results),
        "pages_evaluated": len(success_rows),
        "pages_failed": len(failed_rows),
        "retain_rate": bool_rate(success_rows, "retain_for_llm"),
        "justified_score_rate": bool_rate(success_rows, "score_justified"),
        "category_distribution": dict(category_counter.most_common()),
        "failure_mode_counts": dict(issue_counter.most_common()),
        "representative_examples_by_failure_mode": issue_examples,
        "per_dimension": per_dimension,
        "usage": usage,
        "estimated_cost": estimate_cost(model, usage),
        "top_scoring_improvements": recommendation_counts(success_rows, "suggested_scoring_improvements"),
        "top_retention_improvements": recommendation_counts(success_rows, "suggested_retention_improvements"),
        "top_knowledge_base_improvements": recommendation_counts(success_rows, "suggested_knowledge_base_improvements"),
        "top_page_type_heuristics": recommendation_counts(success_rows, "suggested_page_type_heuristics"),
    }
    atomic_write_json(output_dir / "evaluation_summary.json", summary)
    write_summary_markdown(output_dir / "evaluation_summary.md", summary)
    write_page_evaluations_markdown(output_dir / "page_evaluations.md", results)
    return summary


def write_page_evaluations_markdown(path: Path, results: list[dict[str, Any]]) -> None:
    lines = ["# Page Evaluations", ""]
    for row in results:
        lines.extend(
            [
                f"## {row.get('file')}",
                "",
                f"- Status: `{row.get('status')}`",
                f"- Dimension: `{row.get('dimension')}`",
                f"- URL: {row.get('url')}",
                f"- Final score: `{row.get('final_score')}`",
            ]
        )
        parsed = row.get("parsed")
        if parsed:
            lines.extend(
                [
                    f"- Retain for LLM: `{parsed.get('retain_for_llm')}`",
                    f"- Score justified: `{parsed.get('score_justified')}`",
                    f"- Page category: `{parsed.get('page_category')}`",
                    f"- Evidence quality: `{parsed.get('evidence_quality')}`",
                    f"- Company relevance: `{parsed.get('company_relevance')}`",
                    "",
                    parsed.get("concise_summary", ""),
                    "",
                ]
            )
            issues = parsed.get("detected_issues", []) or []
            if issues:
                lines.append("Issues:")
                for issue in issues:
                    lines.append(f"- `{issue.get('issue_type')}` / `{issue.get('severity')}`: {issue.get('explanation')}")
                lines.append("")
        elif row.get("error_message"):
            lines.extend(["", f"Error: `{row.get('error_message')}`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# LLM Scoring Evaluation Summary",
        "",
        f"- Scoring run ID: `{summary.get('scoring_run_id')}`",
        f"- Model: `{summary.get('model')}`",
        f"- Pages evaluated: `{summary.get('pages_evaluated')}`",
        f"- Pages failed: `{summary.get('pages_failed')}`",
        f"- Retain rate: `{summary.get('retain_rate')}`",
        f"- Justified score rate: `{summary.get('justified_score_rate')}`",
        f"- Total tokens: `{summary.get('usage', {}).get('total_tokens')}`",
        f"- Estimated cost USD: `{summary.get('estimated_cost', {}).get('total_cost_usd')}`",
        "",
        "## Failure Modes",
        "",
    ]
    for issue, count in summary.get("failure_mode_counts", {}).items():
        lines.append(f"- `{issue}`: {count}")
    lines.extend(["", "## Per Dimension", ""])
    for dimension, values in summary.get("per_dimension", {}).items():
        lines.append(
            f"- `{dimension}`: pages={values.get('pages')}, "
            f"retain_rate={values.get('retain_rate')}, "
            f"evidence_quality={values.get('average_evidence_quality')}, "
            f"company_relevance={values.get('average_company_relevance')}"
        )
    for title, key in (
        ("Top Scoring Improvements", "top_scoring_improvements"),
        ("Top Retention Improvements", "top_retention_improvements"),
        ("Top Knowledge-Base Improvements", "top_knowledge_base_improvements"),
        ("Top Page-Type Heuristics", "top_page_type_heuristics"),
    ):
        lines.extend(["", f"## {title}", ""])
        for item in summary.get(key, [])[:10]:
            lines.append(f"- ({item.get('count')}) {item.get('recommendation')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_project_status(project_root: Path, summary: dict[str, Any]) -> None:
    path = project_root / "docs" / "project_status.md"
    if not path.is_file():
        return
    entry = f"""

## 2026-07-17 — Milestone 4.5: Automated LLM Scoring Evaluation

### Date

2026-07-17

### Ticket / Milestone

Milestone 4.5 — Automated LLM Scoring Evaluation

### Objective

Automatically evaluate high-scoring page review files with the OpenAI Responses API to identify scoring strengths, false positives, and retention-design evidence.

### Implementation Summary

Added `python -m ai_ecosystem.scoring.run_llm_evaluation`, which reads a scoring evaluation package, evaluates each page Markdown file with strict JSON output, supports resumable execution, and writes page-level plus aggregate Markdown/JSON reports.

### Key Findings

- Evaluation directory: `{summary.get('evaluation_dir')}`.
- Output directory: `{summary.get('output_dir')}`.
- Model: `{summary.get('model')}`.
- Pages evaluated: {summary.get('pages_evaluated')} / {summary.get('pages_attempted')}.
- Pages failed: {summary.get('pages_failed')}.
- Total tokens: {summary.get('usage', {}).get('total_tokens')}.
- Estimated cost USD: {summary.get('estimated_cost', {}).get('total_cost_usd')}.
- Generated `page_evaluations.json`, `page_evaluations.md`, `evaluation_summary.json`, and `evaluation_summary.md`.
- No scores, knowledge bases, retention logic, SQLite scoring tables, or evidence packages were modified.

### Current Status

Milestone 4.5 is complete. The project now has automated qualitative evaluation outputs for the high-scoring page review package.

### Next Recommended Milestone

Review the LLM evaluation findings and decide whether to calibrate scoring, adjust knowledge-base terms, or design the first score-based retention policy.
"""
    with path.open("a", encoding="utf-8") as file:
        file.write(entry)


def run_llm_evaluation(
    *,
    evaluation_dir: Path,
    model: str = "gpt-5",
    max_pages: int | None = None,
    parallelism: int = 1,
    resume: bool = False,
    output_dir: Path | None = None,
    temperature: float = 0,
    project_root: Path | None = None,
    client: Any | None = None,
    update_project_status: bool = True,
) -> dict[str, Any]:
    root = project_root or find_project_root()
    eval_dir = evaluation_dir if evaluation_dir.is_absolute() else root / evaluation_dir
    if not eval_dir.is_dir():
        raise FileNotFoundError(f"Evaluation directory not found: {evaluation_dir}")
    manifest, prompt, pages = load_evaluation_inputs(eval_dir, max_pages=max_pages)
    out_dir = output_dir if output_dir is not None else eval_dir / f"llm_evaluation_{sanitize_model_name(model)}"
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    existing_path = out_dir / "page_evaluations.json"
    if out_dir.exists() and not resume:
        raise FileExistsError(f"Output directory already exists. Use --resume to continue: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = existing_successes(existing_path) if resume else {}
    pending = [page for page in pages if page.file not in existing]
    validation_config = load_validation_config(root)
    retry_count = int(validation_config.get("retry_count", 2))
    if client is None:
        api_key = load_openai_api_key(root)
        timeout_seconds = int(validation_config.get("request_timeout_seconds", 60))
        client = create_openai_client(api_key, timeout_seconds)
    system = build_system_prompt(prompt)
    new_results: list[dict[str, Any]] = []

    print("LLM scoring evaluation")
    print(f"  Evaluation dir: {eval_dir}")
    print(f"  Output dir: {out_dir}")
    print(f"  Model: {model}")
    print(f"  Pages total: {len(pages)}")
    print(f"  Already complete: {len(existing)}")
    print(f"  Pending: {len(pending)}")

    def run_page(page: EvaluationInput) -> dict[str, Any]:
        return evaluate_one_page(
            client,
            page,
            system_prompt=system,
            model=model,
            temperature=temperature,
            retry_count=retry_count,
        )

    if parallelism <= 1:
        for page in pending:
            result = run_page(page)
            new_results.append(result)
            merged = merge_results(pages, existing, new_results)
            atomic_write_json(existing_path, merged)
            print(
                f"Processed {page.index}/{len(pages)} "
                f"status={result['status']} file={page.file}",
                flush=True,
            )
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            future_to_page = {executor.submit(run_page, page): page for page in pending}
            for future in as_completed(future_to_page):
                page = future_to_page[future]
                result = future.result()
                new_results.append(result)
                merged = merge_results(pages, existing, new_results)
                atomic_write_json(existing_path, merged)
                print(
                    f"Processed {page.index}/{len(pages)} "
                    f"status={result['status']} file={page.file}",
                    flush=True,
                )

    results = merge_results(pages, existing, new_results)
    atomic_write_json(existing_path, results)
    summary = summarize_results(
        evaluation_dir=eval_dir,
        output_dir=out_dir,
        model=model,
        results=results,
        manifest=manifest,
    )
    if update_project_status and new_results:
        append_project_status(root, summary)
    print_summary(summary)
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("LLM evaluation summary")
    print(f"  Pages evaluated: {summary['pages_evaluated']}")
    print(f"  Pages failed: {summary['pages_failed']}")
    print(f"  Retain rate: {summary['retain_rate']}")
    print(f"  Justified score rate: {summary['justified_score_rate']}")
    print(f"  Total tokens: {summary['usage']['total_tokens']}")
    print(f"  Estimated cost USD: {summary['estimated_cost']['total_cost_usd']}")
    print(f"  Reports: {Path(summary['output_dir']) / 'evaluation_summary.json'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenAI evaluation over scoring review packages.")
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--max-pages", type=positive_int)
    parser.add_argument("--parallelism", type=positive_int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--temperature", type=non_negative_float, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_llm_evaluation(
        evaluation_dir=args.evaluation_dir,
        model=args.model,
        max_pages=args.max_pages,
        parallelism=args.parallelism,
        resume=args.resume,
        output_dir=args.output_dir,
        temperature=args.temperature,
    )


if __name__ == "__main__":
    main()
