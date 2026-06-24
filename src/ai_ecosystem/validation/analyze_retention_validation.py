"""Analyze stored OpenAI retention-validation results."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


CAP_PRESSURE_DOMAINS = {
    "sprechen.com",
    "museum-joanneum.at",
    "bertrandt.com",
    "consultatio.at",
    "uniqa.at",
}
EVIDENCE_RANK = {"strong": 4, "moderate": 3, "weak": 2, "none": 1, None: 0, "": 0}


def pct(part: int | float, total: int | float) -> float:
    return round(part / total * 100, 2) if total else 0.0


def avg(values: Iterable[int | float | None]) -> float:
    numeric = [float(value) for value in values if value is not None]
    return round(statistics.mean(numeric), 3) if numeric else 0.0


def median(values: Iterable[int | float | None]) -> float:
    numeric = [float(value) for value in values if value is not None]
    return round(statistics.median(numeric), 3) if numeric else 0.0


def stddev(values: Iterable[int | float | None]) -> float:
    numeric = [float(value) for value in values if value is not None]
    if len(numeric) < 2:
        return 0.0
    return statistics.stdev(numeric)


def standardized_mean_difference(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    left_sd = stddev(left)
    right_sd = stddev(right)
    pooled = (((len(left) - 1) * left_sd**2 + (len(right) - 1) * right_sd**2) / max(len(left) + len(right) - 2, 1)) ** 0.5
    if pooled == 0:
        return 0.0
    return round((statistics.mean(left) - statistics.mean(right)) / pooled, 3)


def row_value(row: sqlite3.Row | dict, key: str):
    return row[key] if isinstance(row, sqlite3.Row) else row.get(key)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM retention_validation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No retention validation run found for {run_id}")
    return row


def load_results(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    prompt_version: str,
    model_name: str,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT
            r.*,
            rvs.sample_type AS sample_sample_type,
            rvs.sample_version,
            rvs.selection_reason,
            rvs.priority AS sample_priority,
            rvs.discovery_method AS sample_discovery_method,
            dp.discovery_method AS discovered_discovery_method,
            dp.priority AS discovered_priority,
            cp.discovery_method AS candidate_discovery_method,
            cp.priority AS candidate_priority
        FROM retention_validation_results AS r
        LEFT JOIN retention_validation_sample AS rvs
          ON rvs.sample_id = r.sample_id
         AND rvs.scraped_page_id = r.scraped_page_id
        LEFT JOIN scraped_pages AS sp
          ON sp.scraped_page_id = r.scraped_page_id
        LEFT JOIN discovered_pages AS dp
          ON dp.discovered_page_id = sp.discovered_page_id
        LEFT JOIN candidate_pages AS cp
          ON cp.id = r.candidate_page_id
        WHERE r.validation_run_id = ?
          AND r.prompt_version = ?
          AND r.model_name = ?
          AND r.run_status = 'success'
        ORDER BY r.retained_at_discovery DESC, r.domain, r.url
        """,
        (run_id, prompt_version, model_name),
    ).fetchall()
    return [dict(row) for row in rows]


def values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def group_rows(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) if row.get(key) not in {None, ""} else "unknown")].append(row)
    return grouped


def retained_group_name(retained: int | None) -> str:
    return "retained" if retained == 1 else "discarded"


def build_group_metrics(name: str, rows: list[dict]) -> dict:
    count = len(rows)
    firm_scores = values(rows, "firm_relevance_score")
    ecosystem_scores = values(rows, "ecosystem_relevance_score")
    return {
        "group": name,
        "page_count": count,
        "average_firm_relevance_score": avg(firm_scores),
        "median_firm_relevance_score": median(firm_scores),
        "share_firm_score_ge_4": pct(sum(1 for score in firm_scores if score >= 4), count),
        "share_keep_for_firm_profile_yes": pct(
            sum(1 for row in rows if row.get("keep_for_firm_profile") == "yes"),
            count,
        ),
        "average_ecosystem_relevance_score": avg(ecosystem_scores),
        "median_ecosystem_relevance_score": median(ecosystem_scores),
        "share_ecosystem_score_ge_3": pct(sum(1 for score in ecosystem_scores if score >= 3), count),
        "share_ecosystem_score_ge_4": pct(sum(1 for score in ecosystem_scores if score >= 4), count),
    }


def retained_vs_discarded(rows: list[dict]) -> tuple[list[dict], dict]:
    retained = [row for row in rows if row.get("retained_at_discovery") == 1]
    discarded = [row for row in rows if row.get("retained_at_discovery") == 0]
    retained_metrics = build_group_metrics("retained", retained)
    discarded_metrics = build_group_metrics("discarded", discarded)
    firm_diff = round(
        retained_metrics["average_firm_relevance_score"] - discarded_metrics["average_firm_relevance_score"],
        3,
    )
    ecosystem_diff = round(
        retained_metrics["average_ecosystem_relevance_score"]
        - discarded_metrics["average_ecosystem_relevance_score"],
        3,
    )
    effects = {
        "firm_average_difference": firm_diff,
        "ecosystem_average_difference": ecosystem_diff,
        "firm_standardized_mean_difference": standardized_mean_difference(
            values(retained, "firm_relevance_score"),
            values(discarded, "firm_relevance_score"),
        ),
        "ecosystem_standardized_mean_difference": standardized_mean_difference(
            values(retained, "ecosystem_relevance_score"),
            values(discarded, "ecosystem_relevance_score"),
        ),
    }
    return [retained_metrics, discarded_metrics], effects


def page_type_analysis(rows: list[dict]) -> list[dict]:
    output = []
    for page_type, page_rows in sorted(group_rows(rows, "page_type_guess").items()):
        retained = [row for row in page_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in page_rows if row.get("retained_at_discovery") == 0]
        firm_scores = values(page_rows, "firm_relevance_score")
        ecosystem_scores = values(page_rows, "ecosystem_relevance_score")
        output.append(
            {
                "page_type_guess": page_type,
                "count": len(page_rows),
                "average_firm_relevance_score": avg(firm_scores),
                "average_ecosystem_relevance_score": avg(ecosystem_scores),
                "share_firm_score_ge_4": pct(sum(1 for score in firm_scores if score >= 4), len(page_rows)),
                "share_ecosystem_score_ge_3": pct(
                    sum(1 for score in ecosystem_scores if score >= 3),
                    len(page_rows),
                ),
                "share_keep_yes": pct(
                    sum(1 for row in page_rows if row.get("keep_for_firm_profile") == "yes"),
                    len(page_rows),
                ),
                "retained_count": len(retained),
                "discarded_count": len(discarded),
                "retained_average_firm_relevance_score": avg(values(retained, "firm_relevance_score")),
                "discarded_average_firm_relevance_score": avg(values(discarded, "firm_relevance_score")),
                "retained_average_ecosystem_relevance_score": avg(values(retained, "ecosystem_relevance_score")),
                "discarded_average_ecosystem_relevance_score": avg(values(discarded, "ecosystem_relevance_score")),
            }
        )
    return sorted(output, key=lambda row: (-row["average_ecosystem_relevance_score"], -row["average_firm_relevance_score"], row["page_type_guess"]))


def category_analysis(rows: list[dict]) -> list[dict]:
    output = []
    for category, category_rows in sorted(group_rows(rows, "page_category_llm").items()):
        output.append(
            {
                "page_category_llm": category,
                "count": len(category_rows),
                "average_firm_relevance_score": avg(values(category_rows, "firm_relevance_score")),
                "average_ecosystem_relevance_score": avg(values(category_rows, "ecosystem_relevance_score")),
            }
        )
    return sorted(output, key=lambda row: (-row["average_ecosystem_relevance_score"], -row["average_firm_relevance_score"], row["page_category_llm"]))


def false_negatives(rows: list[dict]) -> list[dict]:
    output = []
    for row in rows:
        if row.get("retained_at_discovery") != 0:
            continue
        role = row.get("ai_ecosystem_role")
        evidence = row.get("ai_evidence_strength")
        if float(row.get("ecosystem_relevance_score") or 0) >= 3 or (
            evidence in {"moderate", "strong"} and role != "not_ai_related"
        ):
            output.append(example_row(row, "false_negative"))
    return sorted(
        output,
        key=lambda row: (-float(row["ecosystem_relevance_score"] or 0), -EVIDENCE_RANK.get(row["ai_evidence_strength"], 0), row["domain"], row["url"]),
    )


def false_positives(rows: list[dict]) -> list[dict]:
    output = []
    for row in rows:
        if row.get("retained_at_discovery") != 1:
            continue
        if float(row.get("firm_relevance_score") or 0) <= 2 or (
            float(row.get("ecosystem_relevance_score") or 0) == 1
            and row.get("keep_for_firm_profile") == "no"
        ):
            output.append(example_row(row, "false_positive"))
    return sorted(
        output,
        key=lambda row: (float(row["firm_relevance_score"] or 0), float(row["ecosystem_relevance_score"] or 0), row["domain"], row["url"]),
    )


def example_row(row: dict, finding_type: str) -> dict:
    return {
        "finding_type": finding_type,
        "domain": row.get("domain"),
        "url": row.get("url"),
        "retained_at_discovery": row.get("retained_at_discovery"),
        "page_type_guess": row.get("page_type_guess"),
        "firm_relevance_score": row.get("firm_relevance_score"),
        "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
        "ai_ecosystem_role": row.get("ai_ecosystem_role"),
        "ai_evidence_strength": row.get("ai_evidence_strength"),
        "reason_short": row.get("reason_short"),
        "ai_relevance_reason": row.get("ai_relevance_reason"),
    }


def domain_analysis(rows: list[dict]) -> list[dict]:
    output = []
    for domain, domain_rows in sorted(group_rows(rows, "domain").items()):
        retained = [row for row in domain_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in domain_rows if row.get("retained_at_discovery") == 0]
        output.append(
            {
                "domain": domain,
                "retained_count": len(retained),
                "discarded_count": len(discarded),
                "average_firm_relevance_score": avg(values(domain_rows, "firm_relevance_score")),
                "average_ecosystem_relevance_score": avg(values(domain_rows, "ecosystem_relevance_score")),
                "max_ecosystem_relevance_score": max(values(domain_rows, "ecosystem_relevance_score") or [0]),
                "has_ai_signal": int(
                    any(
                        row.get("ai_ecosystem_role") not in {"not_ai_related", "unclear", None, ""}
                        or row.get("ai_evidence_strength") in {"moderate", "strong"}
                        for row in domain_rows
                    )
                ),
                "cap_pressure_domain": int(domain in CAP_PRESSURE_DOMAINS),
            }
        )
    return sorted(output, key=lambda row: (-row["max_ecosystem_relevance_score"], -row["average_firm_relevance_score"], row["domain"]))


def ai_findings(rows: list[dict], limit: int = 25) -> list[dict]:
    output = []
    role_counts = Counter(row.get("ai_ecosystem_role") or "unknown" for row in rows)
    evidence_counts = Counter(row.get("ai_evidence_strength") or "unknown" for row in rows)
    for role, count in role_counts.most_common():
        output.append(
            {
                "section": "role_distribution",
                "rank": "",
                "key": role,
                "count": count,
                "domain": "",
                "url": "",
                "page_type_guess": "",
                "ecosystem_relevance_score": "",
                "ai_ecosystem_role": role,
                "ai_evidence_strength": "",
                "ai_relevance_reason": "",
            }
        )
    for evidence, count in evidence_counts.most_common():
        output.append(
            {
                "section": "evidence_strength_distribution",
                "rank": "",
                "key": evidence,
                "count": count,
                "domain": "",
                "url": "",
                "page_type_guess": "",
                "ecosystem_relevance_score": "",
                "ai_ecosystem_role": "",
                "ai_evidence_strength": evidence,
                "ai_relevance_reason": "",
            }
        )
    ranked = sorted(
        rows,
        key=lambda row: (
            -float(row.get("ecosystem_relevance_score") or 0),
            -EVIDENCE_RANK.get(row.get("ai_evidence_strength"), 0),
            row.get("domain") or "",
            row.get("url") or "",
        ),
    )
    for rank, row in enumerate(ranked[:limit], start=1):
        output.append(
            {
                "section": "top_ecosystem_signal_pages",
                "rank": rank,
                "key": "",
                "count": "",
                "domain": row.get("domain"),
                "url": row.get("url"),
                "page_type_guess": row.get("page_type_guess"),
                "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
                "ai_ecosystem_role": row.get("ai_ecosystem_role"),
                "ai_evidence_strength": row.get("ai_evidence_strength"),
                "ai_relevance_reason": row.get("ai_relevance_reason"),
            }
        )
    non_ai = [
        row
        for row in rows
        if row.get("ai_ecosystem_role") == "not_ai_related"
        and row.get("ai_evidence_strength") == "none"
    ]
    for rank, row in enumerate(
        sorted(non_ai, key=lambda item: (-(item.get("firm_relevance_score") or 0), item.get("domain") or ""))[:10],
        start=1,
    ):
        output.append(
            {
                "section": "strong_non_ai_evidence",
                "rank": rank,
                "key": "",
                "count": "",
                "domain": row.get("domain"),
                "url": row.get("url"),
                "page_type_guess": row.get("page_type_guess"),
                "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
                "ai_ecosystem_role": row.get("ai_ecosystem_role"),
                "ai_evidence_strength": row.get("ai_evidence_strength"),
                "ai_relevance_reason": row.get("ai_relevance_reason"),
            }
        )
    return output


def cost_report(run: sqlite3.Row, rows: list[dict]) -> list[dict]:
    input_tokens = int(run["input_tokens"] or sum(row.get("input_tokens") or 0 for row in rows))
    output_tokens = int(run["output_tokens"] or sum(row.get("output_tokens") or 0 for row in rows))
    total_tokens = int(run["total_tokens"] or sum(row.get("total_tokens") or 0 for row in rows))
    return [
        {"metric": "run_id", "value": run["run_id"]},
        {"metric": "sample_id", "value": run["sample_id"]},
        {"metric": "pages_evaluated", "value": len(rows)},
        {"metric": "input_tokens", "value": input_tokens},
        {"metric": "output_tokens", "value": output_tokens},
        {"metric": "total_tokens", "value": total_tokens},
        {"metric": "model", "value": run["model_name"]},
        {"metric": "prompt_version", "value": run["prompt_version"]},
        {"metric": "dollar_estimate", "value": "not calculated"},
    ]


def build_summary_rows(run: sqlite3.Row, rows: list[dict], group_metrics: list[dict], effects: dict, assessment: dict) -> list[dict]:
    output = [
        {"section": "run", "metric": "run_id", "group": "", "value": run["run_id"], "detail": ""},
        {"section": "run", "metric": "sample_id", "group": "", "value": run["sample_id"], "detail": ""},
        {"section": "run", "metric": "prompt_version", "group": "", "value": run["prompt_version"], "detail": ""},
        {"section": "run", "metric": "model_name", "group": "", "value": run["model_name"], "detail": ""},
        {"section": "run", "metric": "pages_evaluated", "group": "", "value": len(rows), "detail": ""},
    ]
    for item in group_metrics:
        for metric, value in item.items():
            if metric == "group":
                continue
            output.append({"section": "retained_vs_discarded", "metric": metric, "group": item["group"], "value": value, "detail": ""})
    for metric, value in effects.items():
        output.append({"section": "effect_size", "metric": metric, "group": "retained_minus_discarded", "value": value, "detail": ""})
    for key, value in assessment.items():
        output.append({"section": "final_assessment", "metric": key, "group": "", "value": value, "detail": ""})
    return output


def final_assessment(group_metrics: list[dict], effects: dict, page_types: list[dict], false_negative_rows: list[dict], false_positive_rows: list[dict]) -> dict:
    groups = {row["group"]: row for row in group_metrics}
    retained = groups.get("retained", {})
    discarded = groups.get("discarded", {})
    higher_firm = effects["firm_average_difference"] > 0
    higher_ecosystem = effects["ecosystem_average_difference"] > 0
    top_firm_types = ", ".join(
        row["page_type_guess"]
        for row in sorted(page_types, key=lambda item: (-item["average_firm_relevance_score"], -item["count"]))[:5]
    )
    top_ecosystem_types = ", ".join(
        row["page_type_guess"]
        for row in sorted(page_types, key=lambda item: (-item["average_ecosystem_relevance_score"], -item["count"]))[:5]
    )
    low_types = ", ".join(
        row["page_type_guess"]
        for row in sorted(page_types, key=lambda item: (item["average_firm_relevance_score"], item["average_ecosystem_relevance_score"], -item["count"]))[:5]
    )
    return {
        "A_retention_improves_firm_representativeness": (
            f"Yes; retained average firm score is {retained.get('average_firm_relevance_score', 0)} "
            f"vs discarded {discarded.get('average_firm_relevance_score', 0)}."
            if higher_firm
            else f"No clear improvement; retained average firm score is {retained.get('average_firm_relevance_score', 0)} "
            f"vs discarded {discarded.get('average_firm_relevance_score', 0)}."
        ),
        "B_retention_improves_ai_ecosystem_usefulness": (
            f"Yes; retained average ecosystem score is {retained.get('average_ecosystem_relevance_score', 0)} "
            f"vs discarded {discarded.get('average_ecosystem_relevance_score', 0)}."
            if higher_ecosystem
            else f"No clear improvement; retained average ecosystem score is {retained.get('average_ecosystem_relevance_score', 0)} "
            f"vs discarded {discarded.get('average_ecosystem_relevance_score', 0)}."
        ),
        "C_page_types_with_evidence_for_higher_priority": top_ecosystem_types,
        "D_page_types_with_evidence_for_lower_priority": low_types,
        "E_retention_cap_losses_likely_removing_useful_ecosystem_pages": (
            f"Evidence exists: {len(false_negative_rows)} discarded pages meet ecosystem false-negative criteria."
            if false_negative_rows
            else "No strong evidence in this validation run."
        ),
        "F_current_strategy_validated": (
            "Partially: firm representativeness improves, but ecosystem usefulness should be interpreted from false-negative and page-type evidence."
            if higher_firm
            else "Not validated for firm representativeness by this run."
        ),
        "strongest_firm_page_types": top_firm_types,
        "false_positive_count": len(false_positive_rows),
        "false_negative_count": len(false_negative_rows),
    }


def output_paths(project_root: Path, prompt_version: str) -> dict[str, Path]:
    interim = project_root / "data" / "interim"
    logs = project_root / "logs"
    suffix = prompt_version
    return {
        "summary": interim / f"retention_validation_summary_{suffix}.csv",
        "page_type": interim / f"retention_validation_page_type_analysis_{suffix}.csv",
        "category": interim / f"retention_validation_category_analysis_{suffix}.csv",
        "false_negatives": interim / f"retention_validation_false_negatives_{suffix}.csv",
        "false_positives": interim / f"retention_validation_false_positives_{suffix}.csv",
        "domain": interim / f"retention_validation_domain_analysis_{suffix}.csv",
        "ai_findings": interim / f"retention_validation_ai_findings_{suffix}.csv",
        "cost": interim / f"retention_validation_cost_report_{suffix}.csv",
        "json": logs / f"retention_validation_analysis_{suffix}.json",
    }


def append_project_status(project_root: Path, run_id: str, assessment: dict, rows: list[dict]) -> None:
    path = project_root / "docs" / "project_status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip()
    else:
        existing = "# Project Status\n"
    text = f"""

## {date.today().isoformat()} — Milestone 3A.5h: Retention Validation Analysis & Reporting

### Date

{date.today().isoformat()}

### Ticket / Milestone

Milestone 3A.5h — Retention Validation Analysis & Reporting

### Objective

Analyze the completed v2 retention-validation run and determine whether current retention preserves pages useful for organization profiling and AI-ecosystem determination.

### Implementation Summary

Added a read-only analysis utility that exports CSV and JSON reports from stored validation results without rerunning OpenAI or modifying discovery/retention logic.

### Key Findings

- Validation run analyzed: `{run_id}`
- Pages analyzed: {len(rows)}
- {assessment['A_retention_improves_firm_representativeness']}
- {assessment['B_retention_improves_ai_ecosystem_usefulness']}
- False negatives: {assessment['false_negative_count']}
- False positives: {assessment['false_positive_count']}

### Current Status

Retention validation evidence reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the validation findings manually before deciding whether to tune retention priorities or proceed to Milestone 3B.
"""
    path.write_text(existing + "\n" + text.strip() + "\n", encoding="utf-8")


def analyze_retention_validation(
    *,
    run_id: str,
    prompt_version: str = "v2",
    model_name: str = "gpt-5-mini",
    update_project_status: bool = True,
) -> dict:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        run = load_run(connection, run_id)
        rows = load_results(
            connection,
            run_id=run_id,
            prompt_version=prompt_version,
            model_name=model_name,
        )
    if not rows:
        raise ValueError(f"No successful validation results found for run_id={run_id}, prompt_version={prompt_version}, model={model_name}")

    group_metrics, effects = retained_vs_discarded(rows)
    page_types = page_type_analysis(rows)
    categories = category_analysis(rows)
    false_negative_rows = false_negatives(rows)
    false_positive_rows = false_positives(rows)
    domains = domain_analysis(rows)
    ai_rows = ai_findings(rows)
    cost_rows = cost_report(run, rows)
    assessment = final_assessment(group_metrics, effects, page_types, false_negative_rows, false_positive_rows)
    summary_rows = build_summary_rows(run, rows, group_metrics, effects, assessment)
    paths = output_paths(project_root, prompt_version)

    write_csv(paths["summary"], summary_rows, ["section", "metric", "group", "value", "detail"])
    write_csv(
        paths["page_type"],
        page_types,
        [
            "page_type_guess",
            "count",
            "average_firm_relevance_score",
            "average_ecosystem_relevance_score",
            "share_firm_score_ge_4",
            "share_ecosystem_score_ge_3",
            "share_keep_yes",
            "retained_count",
            "discarded_count",
            "retained_average_firm_relevance_score",
            "discarded_average_firm_relevance_score",
            "retained_average_ecosystem_relevance_score",
            "discarded_average_ecosystem_relevance_score",
        ],
    )
    write_csv(paths["category"], categories, ["page_category_llm", "count", "average_firm_relevance_score", "average_ecosystem_relevance_score"])
    example_fields = [
        "finding_type",
        "domain",
        "url",
        "retained_at_discovery",
        "page_type_guess",
        "firm_relevance_score",
        "ecosystem_relevance_score",
        "ai_ecosystem_role",
        "ai_evidence_strength",
        "reason_short",
        "ai_relevance_reason",
    ]
    write_csv(paths["false_negatives"], false_negative_rows, example_fields)
    write_csv(paths["false_positives"], false_positive_rows, example_fields)
    write_csv(
        paths["domain"],
        domains,
        [
            "domain",
            "retained_count",
            "discarded_count",
            "average_firm_relevance_score",
            "average_ecosystem_relevance_score",
            "max_ecosystem_relevance_score",
            "has_ai_signal",
            "cap_pressure_domain",
        ],
    )
    write_csv(
        paths["ai_findings"],
        ai_rows,
        [
            "section",
            "rank",
            "key",
            "count",
            "domain",
            "url",
            "page_type_guess",
            "ecosystem_relevance_score",
            "ai_ecosystem_role",
            "ai_evidence_strength",
            "ai_relevance_reason",
        ],
    )
    write_csv(paths["cost"], cost_rows, ["metric", "value"])
    payload = {
        "run_id": run_id,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "pages_analyzed": len(rows),
        "retained_vs_discarded": group_metrics,
        "effect_sizes": effects,
        "top_page_types": page_types[:10],
        "category_analysis": categories,
        "false_negative_count": len(false_negative_rows),
        "false_positive_count": len(false_positive_rows),
        "ai_role_distribution": dict(Counter(row.get("ai_ecosystem_role") or "unknown" for row in rows).most_common()),
        "ai_evidence_distribution": dict(Counter(row.get("ai_evidence_strength") or "unknown" for row in rows).most_common()),
        "assessment": assessment,
        "outputs": {name: str(path) for name, path in paths.items()},
    }
    write_json(paths["json"], payload)
    if update_project_status:
        append_project_status(project_root, run_id, assessment, rows)

    return {
        "run": dict(run),
        "rows": rows,
        "group_metrics": group_metrics,
        "effects": effects,
        "page_types": page_types,
        "categories": categories,
        "false_negatives": false_negative_rows,
        "false_positives": false_positive_rows,
        "domains": domains,
        "ai_findings": ai_rows,
        "cost": cost_rows,
        "assessment": assessment,
        "paths": paths,
        "json": payload,
    }


def print_summary(summary: dict) -> None:
    groups = {row["group"]: row for row in summary["group_metrics"]}
    retained = groups.get("retained", {})
    discarded = groups.get("discarded", {})
    print("Retention validation analysis")
    print(f"Run ID: {summary['run']['run_id']}")
    print(f"Pages analyzed: {len(summary['rows'])}")
    print()
    print("Retained vs discarded")
    print(
        "  firm avg: retained="
        f"{retained.get('average_firm_relevance_score', 0)} discarded={discarded.get('average_firm_relevance_score', 0)} "
        f"diff={summary['effects']['firm_average_difference']}"
    )
    print(
        "  ecosystem avg: retained="
        f"{retained.get('average_ecosystem_relevance_score', 0)} discarded={discarded.get('average_ecosystem_relevance_score', 0)} "
        f"diff={summary['effects']['ecosystem_average_difference']}"
    )
    print()
    print("Top page types by ecosystem score")
    for row in summary["page_types"][:5]:
        print(
            f"  {row['page_type_guess']}: count={row['count']} "
            f"firm_avg={row['average_firm_relevance_score']} ecosystem_avg={row['average_ecosystem_relevance_score']}"
        )
    print()
    print("Findings")
    print(f"  false negatives: {len(summary['false_negatives'])}")
    print(f"  false positives: {len(summary['false_positives'])}")
    print(f"  {summary['assessment']['A_retention_improves_firm_representativeness']}")
    print(f"  {summary['assessment']['B_retention_improves_ai_ecosystem_usefulness']}")
    print()
    print("Generated files")
    for path in summary["paths"].values():
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze stored retention-validation results.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-version", default="v2")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--skip-project-status", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = analyze_retention_validation(
        run_id=args.run_id,
        prompt_version=args.prompt_version,
        model_name=args.model,
        update_project_status=not args.skip_project_status,
    )
    print_summary(summary)


if __name__ == "__main__":
    main()
