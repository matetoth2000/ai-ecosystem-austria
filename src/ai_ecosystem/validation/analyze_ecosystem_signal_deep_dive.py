"""Deep-dive analysis of AI-ecosystem signal in validation results."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path
from ai_ecosystem.validation.analyze_retention_validation import (
    CAP_PRESSURE_DOMAINS,
    EVIDENCE_RANK,
    avg,
    pct,
    write_csv,
    write_json,
)


AI_RELATED_ROLES = {
    "ai_developer_vendor",
    "ai_service_provider",
    "ai_consultancy",
    "ai_research_education",
    "ai_adopter_user",
    "ai_investor_support",
    "government_regulator",
}
INTERESTING_TERMS = [
    "ai",
    "ki",
    "künstliche",
    "kuenstliche",
    "intelligenz",
    "machine",
    "learning",
    "robotik",
    "robotics",
    "autonom",
    "automation",
    "automatisierung",
    "digitalisierung",
    "analytics",
    "software",
    "algorithm",
    "algorithmen",
    "data",
    "daten",
    "sensor",
    "hyperspectral",
    "iot",
]


def load_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM retention_validation_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"No retention validation run found for {run_id}")
    return row


def load_rows(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    prompt_version: str,
    model_name: str,
) -> list[dict]:
    rows = connection.execute(
        """
        SELECT
            r.id AS result_id,
            r.sample_id,
            r.validation_run_id,
            r.scraped_page_id,
            r.candidate_page_id,
            r.domain,
            r.url,
            r.retained_at_discovery,
            r.page_type_guess,
            r.firm_relevance_score,
            r.page_category_llm,
            r.keep_for_firm_profile,
            r.reason_short,
            r.evidence_snippet,
            r.ecosystem_relevance_score,
            r.ai_ecosystem_role,
            r.ai_evidence_strength,
            r.ai_relevance_reason,
            rvs.discovered_page_id,
            rvs.discovery_method AS sample_discovery_method,
            rvs.priority AS sample_priority,
            rvs.selection_reason,
            rvi.title,
            rvi.meta_description,
            rvi.visible_text_length,
            rvi.preview_char_count,
            rvi.visible_text_preview,
            dp.discovery_method AS discovered_discovery_method,
            dp.priority AS discovered_priority,
            dp.discard_reason AS discovered_discard_reason,
            dp.discovery_run_id,
            cp.discovery_method AS candidate_discovery_method,
            cp.priority AS candidate_priority
        FROM retention_validation_results AS r
        LEFT JOIN retention_validation_sample AS rvs
          ON rvs.sample_id = r.sample_id
         AND rvs.scraped_page_id = r.scraped_page_id
        LEFT JOIN retention_validation_inputs AS rvi
          ON rvi.sample_id = r.sample_id
         AND rvi.scraped_page_id = r.scraped_page_id
        LEFT JOIN discovered_pages AS dp
          ON dp.discovered_page_id = rvs.discovered_page_id
        LEFT JOIN candidate_pages AS cp
          ON cp.id = r.candidate_page_id
        WHERE r.validation_run_id = ?
          AND r.prompt_version = ?
          AND r.model_name = ?
          AND r.run_status = 'success'
        ORDER BY r.domain, r.url
        """,
        (run_id, prompt_version, model_name),
    ).fetchall()
    return [enrich_row(dict(row)) for row in rows]


def discovery_method(row: dict) -> str:
    return (
        row.get("sample_discovery_method")
        or row.get("discovered_discovery_method")
        or row.get("candidate_discovery_method")
        or "unknown"
    )


def priority(row: dict) -> int | None:
    value = row.get("sample_priority")
    if value is None:
        value = row.get("discovered_priority")
    if value is None:
        value = row.get("candidate_priority")
    return value


def is_signal(row: dict) -> bool:
    return (
        float(row.get("ecosystem_relevance_score") or 0) >= 3
        or row.get("ai_evidence_strength") in {"moderate", "strong"}
        or row.get("ai_ecosystem_role") in AI_RELATED_ROLES
    )


def text_blob(row: dict) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in [
            "url",
            "title",
            "meta_description",
            "page_type_guess",
            "page_category_llm",
            "reason_short",
            "ai_relevance_reason",
            "visible_text_preview",
        ]
    ).lower()


def matching_terms(row: dict) -> list[str]:
    blob = text_blob(row)
    found = []
    for term in INTERESTING_TERMS:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", blob, flags=re.IGNORECASE):
            found.append(term)
    return found


def url_tokens(url: str | None) -> list[str]:
    if not url:
        return []
    tokens = re.split(r"[^A-Za-zÄÖÜäöüß0-9]+", url.lower())
    return [token for token in tokens if len(token) >= 3 and not token.isdigit()]


def inferred_pattern(row: dict) -> str:
    terms = matching_terms(row)
    if row.get("ai_ecosystem_role") in AI_RELATED_ROLES:
        return f"explicit_role:{row['ai_ecosystem_role']}"
    if row.get("ai_evidence_strength") in {"moderate", "strong"}:
        return f"{row['ai_evidence_strength']}_evidence_without_confirmed_role"
    if terms:
        return "technology_terms:" + ",".join(terms[:5])
    if row.get("page_type_guess") == "ai_technology":
        return "classified_ai_technology"
    return "weak_or_contextual_signal"


def false_negative_cause(row: dict) -> str:
    discard_reason = row.get("discovered_discard_reason")
    method = discovery_method(row)
    if discard_reason == "retention_cap" and row.get("domain") in CAP_PRESSURE_DOMAINS:
        return "retention_cap_on_known_cap_pressure_domain"
    if discard_reason == "retention_cap":
        return f"retention_cap_{method}"
    if row.get("page_type_guess") == "unknown":
        return "unknown_page_type_with_ecosystem_signal"
    if row.get("page_type_guess") in {"careers", "news", "about"}:
        return f"business_page_type_discarded:{row['page_type_guess']}"
    return discard_reason or "discarded_without_recorded_reason"


def enrich_row(row: dict) -> dict:
    row["discovery_method"] = discovery_method(row)
    row["resolved_priority"] = priority(row)
    row["retention_group"] = "retained" if row.get("retained_at_discovery") == 1 else "discarded"
    row["is_ecosystem_signal"] = int(is_signal(row))
    row["interesting_terms"] = ";".join(matching_terms(row))
    row["url_tokens"] = ";".join(url_tokens(row.get("url"))[:12])
    row["signal_pattern"] = inferred_pattern(row)
    return row


def ecosystem_signal_pages(rows: list[dict]) -> list[dict]:
    signal_rows = [row for row in rows if row["is_ecosystem_signal"]]
    output = []
    for rank, row in enumerate(
        sorted(
            signal_rows,
            key=lambda item: (
                -float(item.get("ecosystem_relevance_score") or 0),
                -EVIDENCE_RANK.get(item.get("ai_evidence_strength"), 0),
                item.get("domain") or "",
                item.get("url") or "",
            ),
        ),
        start=1,
    ):
        output.append(
            {
                "rank": rank,
                "domain": row.get("domain"),
                "url": row.get("url"),
                "retention_group": row.get("retention_group"),
                "page_type_guess": row.get("page_type_guess"),
                "discovery_method": row.get("discovery_method"),
                "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
                "ai_ecosystem_role": row.get("ai_ecosystem_role"),
                "ai_evidence_strength": row.get("ai_evidence_strength"),
                "signal_pattern": row.get("signal_pattern"),
                "interesting_terms": row.get("interesting_terms"),
                "title": row.get("title") or "",
                "ai_relevance_reason": row.get("ai_relevance_reason") or "",
                "visible_text_preview_excerpt": (row.get("visible_text_preview") or "")[:300],
            }
        )
    return output


def false_negative_root_causes(rows: list[dict]) -> list[dict]:
    output = []
    false_negatives = [
        row
        for row in rows
        if row.get("retained_at_discovery") == 0
        and (
            float(row.get("ecosystem_relevance_score") or 0) >= 3
            or (
                row.get("ai_evidence_strength") in {"moderate", "strong"}
                and row.get("ai_ecosystem_role") != "not_ai_related"
            )
        )
    ]
    for row in sorted(
        false_negatives,
        key=lambda item: (
            -float(item.get("ecosystem_relevance_score") or 0),
            -EVIDENCE_RANK.get(item.get("ai_evidence_strength"), 0),
            item.get("domain") or "",
            item.get("url") or "",
        ),
    ):
        output.append(
            {
                "domain": row.get("domain"),
                "url": row.get("url"),
                "page_type_guess": row.get("page_type_guess"),
                "discovery_method": row.get("discovery_method"),
                "retained_at_discovery": row.get("retained_at_discovery"),
                "discard_reason": row.get("discovered_discard_reason") or "",
                "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
                "ai_ecosystem_role": row.get("ai_ecosystem_role"),
                "ai_evidence_strength": row.get("ai_evidence_strength"),
                "inferred_root_cause": false_negative_cause(row),
                "signal_pattern": row.get("signal_pattern"),
                "interesting_terms": row.get("interesting_terms"),
                "ai_relevance_reason": row.get("ai_relevance_reason") or "",
            }
        )
    return output


def cap_pressure_analysis(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    signal_domains = {row["domain"] for row in rows if row["is_ecosystem_signal"]}
    for row in rows:
        domain = row.get("domain") or "unknown"
        if domain in CAP_PRESSURE_DOMAINS or domain in signal_domains:
            grouped[domain].append(row)
    output = []
    for domain, domain_rows in sorted(grouped.items()):
        retained = [row for row in domain_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in domain_rows if row.get("retained_at_discovery") == 0]
        retained_signal = [row for row in retained if row["is_ecosystem_signal"]]
        discarded_signal = [row for row in discarded if row["is_ecosystem_signal"]]
        output.append(
            {
                "domain": domain,
                "cap_pressure_domain": int(domain in CAP_PRESSURE_DOMAINS),
                "page_count": len(domain_rows),
                "retained_count": len(retained),
                "discarded_count": len(discarded),
                "retained_signal_count": len(retained_signal),
                "discarded_signal_count": len(discarded_signal),
                "retained_average_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in retained),
                "discarded_average_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in discarded),
                "max_ecosystem_score": max([float(row.get("ecosystem_relevance_score") or 0) for row in domain_rows] or [0]),
                "top_signal_patterns": "; ".join(
                    f"{pattern}:{count}"
                    for pattern, count in Counter(row["signal_pattern"] for row in domain_rows if row["is_ecosystem_signal"]).most_common(5)
                ),
            }
        )
    return sorted(output, key=lambda row: (-row["discarded_signal_count"], -row["max_ecosystem_score"], row["domain"]))


def discovery_method_analysis(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["discovery_method"]].append(row)
    output = []
    for method, method_rows in sorted(grouped.items()):
        signal = [row for row in method_rows if row["is_ecosystem_signal"]]
        false_neg = [row for row in signal if row.get("retained_at_discovery") == 0]
        retained = [row for row in method_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in method_rows if row.get("retained_at_discovery") == 0]
        output.append(
            {
                "discovery_method": method,
                "page_count": len(method_rows),
                "retained_count": len(retained),
                "discarded_count": len(discarded),
                "average_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in method_rows),
                "ecosystem_signal_count": len(signal),
                "ecosystem_signal_rate": pct(len(signal), len(method_rows)),
                "false_negative_count": len(false_neg),
                "false_negative_rate": pct(len(false_neg), len(method_rows)),
                "average_firm_score": avg(row.get("firm_relevance_score") for row in method_rows),
                "top_signal_patterns": "; ".join(
                    f"{pattern}:{count}"
                    for pattern, count in Counter(row["signal_pattern"] for row in signal).most_common(5)
                ),
            }
        )
    return output


def ai_role_examples(rows: list[dict]) -> list[dict]:
    selected = [
        row
        for row in rows
        if row.get("ai_ecosystem_role") in AI_RELATED_ROLES
        or (row.get("ai_ecosystem_role") == "unclear" and row.get("ai_evidence_strength") in {"moderate", "strong"})
    ]
    output = []
    for row in sorted(
        selected,
        key=lambda item: (
            item.get("ai_ecosystem_role") or "",
            -float(item.get("ecosystem_relevance_score") or 0),
            -EVIDENCE_RANK.get(item.get("ai_evidence_strength"), 0),
            item.get("domain") or "",
        ),
    ):
        output.append(
            {
                "domain": row.get("domain"),
                "url": row.get("url"),
                "retention_group": row.get("retention_group"),
                "page_type_guess": row.get("page_type_guess"),
                "discovery_method": row.get("discovery_method"),
                "ecosystem_relevance_score": row.get("ecosystem_relevance_score"),
                "ai_ecosystem_role": row.get("ai_ecosystem_role"),
                "ai_evidence_strength": row.get("ai_evidence_strength"),
                "signal_pattern": row.get("signal_pattern"),
                "title": row.get("title") or "",
                "ai_relevance_reason": row.get("ai_relevance_reason") or "",
                "visible_text_preview_excerpt": (row.get("visible_text_preview") or "")[:300],
            }
        )
    return output


def token_patterns(rows: list[dict]) -> list[dict]:
    signal_counts: Counter[str] = Counter()
    non_signal_counts: Counter[str] = Counter()
    for row in rows:
        target = signal_counts if row["is_ecosystem_signal"] else non_signal_counts
        target.update(set(url_tokens(row.get("url"))))
    output = []
    for token, signal_count in signal_counts.most_common(50):
        non_signal_count = non_signal_counts[token]
        output.append(
            {
                "token": token,
                "signal_count": signal_count,
                "non_signal_count": non_signal_count,
                "signal_share_among_token": pct(signal_count, signal_count + non_signal_count),
            }
        )
    return output


def json_summary(
    *,
    run_id: str,
    rows: list[dict],
    signal_pages: list[dict],
    false_negative_rows: list[dict],
    cap_rows: list[dict],
    method_rows: list[dict],
    role_rows: list[dict],
    token_rows: list[dict],
    paths: dict[str, Path],
) -> dict:
    signal_domains = Counter(row["domain"] for row in signal_pages)
    signal_page_types = Counter(row["page_type_guess"] for row in signal_pages)
    signal_methods = Counter(row["discovery_method"] for row in signal_pages)
    signal_retention = Counter(row["retention_group"] for row in signal_pages)
    false_negative_causes = Counter(row["inferred_root_cause"] for row in false_negative_rows)
    return {
        "run_id": run_id,
        "pages_analyzed": len(rows),
        "ecosystem_signal_pages": len(signal_pages),
        "ecosystem_signal_rate": pct(len(signal_pages), len(rows)),
        "false_negative_count": len(false_negative_rows),
        "ai_related_or_moderate_examples": len(role_rows),
        "top_signal_domains": dict(signal_domains.most_common(15)),
        "top_signal_page_types": dict(signal_page_types.most_common(15)),
        "signal_by_discovery_method": dict(signal_methods.most_common()),
        "signal_by_retention_group": dict(signal_retention.most_common()),
        "false_negative_root_causes": dict(false_negative_causes.most_common()),
        "top_url_tokens_in_signal_pages": token_rows[:20],
        "cap_pressure_findings": cap_rows[:20],
        "discovery_method_findings": method_rows,
        "main_findings": [
            f"{len(signal_pages)} of {len(rows)} pages met the ecosystem-signal criterion.",
            f"{len(false_negative_rows)} ecosystem-signal pages were discarded.",
            "Signal is concentrated in a small number of domains and mostly appears as weak or moderate evidence.",
            "Homepage and sitemap rows should be compared through the exported discovery-method report before interpreting coverage.",
        ],
        "outputs": {name: str(path) for name, path in paths.items()},
    }


def output_paths(project_root: Path) -> dict[str, Path]:
    interim = project_root / "data" / "interim"
    logs = project_root / "logs"
    return {
        "false_negative_root_causes": interim / "retention_validation_false_negative_root_causes.csv",
        "ecosystem_signal_pages": interim / "retention_validation_ecosystem_signal_pages.csv",
        "cap_pressure_analysis": interim / "retention_validation_cap_pressure_analysis.csv",
        "discovery_method_analysis": interim / "retention_validation_discovery_method_analysis.csv",
        "ai_role_examples": interim / "retention_validation_ai_role_examples.csv",
        "json": logs / "retention_validation_ecosystem_deep_dive.json",
    }


def append_project_status(project_root: Path, run_id: str, summary: dict) -> None:
    path = project_root / "docs" / "project_status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").rstrip() if path.exists() else "# Project Status\n"
    entry = f"""

## {date.today().isoformat()} — Milestone 3A.5i: Ecosystem Signal Deep Dive

### Date

{date.today().isoformat()}

### Ticket / Milestone

Milestone 3A.5i — Ecosystem Signal Deep Dive

### Objective

Investigate where AI-ecosystem signal appears in the completed validation results and why useful ecosystem pages were retained or discarded.

### Implementation Summary

Added a read-only deep-dive utility that analyzes stored validation, discovery, sample, and text-preview metadata without rerunning OpenAI or modifying pipeline behavior.

### Key Findings

- Validation run analyzed: `{run_id}`
- Pages analyzed: {summary['pages_analyzed']}
- Ecosystem-signal pages: {summary['ecosystem_signal_pages']}
- False negatives: {summary['false_negative_count']}
- Top signal domains: {', '.join(list(summary['top_signal_domains'])[:5])}

### Current Status

Ecosystem signal evidence reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the evidence manually before deciding whether to tune retention priorities or move to Milestone 3B.
"""
    path.write_text(existing + "\n" + entry.strip() + "\n", encoding="utf-8")


def analyze_ecosystem_signal_deep_dive(
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
        load_run(connection, run_id)
        rows = load_rows(
            connection,
            run_id=run_id,
            prompt_version=prompt_version,
            model_name=model_name,
        )
    if not rows:
        raise ValueError(f"No successful validation rows found for {run_id}")

    signal_rows = ecosystem_signal_pages(rows)
    false_negative_rows = false_negative_root_causes(rows)
    cap_rows = cap_pressure_analysis(rows)
    method_rows = discovery_method_analysis(rows)
    role_rows = ai_role_examples(rows)
    token_rows = token_patterns(rows)
    paths = output_paths(project_root)
    summary = json_summary(
        run_id=run_id,
        rows=rows,
        signal_pages=signal_rows,
        false_negative_rows=false_negative_rows,
        cap_rows=cap_rows,
        method_rows=method_rows,
        role_rows=role_rows,
        token_rows=token_rows,
        paths=paths,
    )

    write_csv(
        paths["false_negative_root_causes"],
        false_negative_rows,
        [
            "domain",
            "url",
            "page_type_guess",
            "discovery_method",
            "retained_at_discovery",
            "discard_reason",
            "ecosystem_relevance_score",
            "ai_ecosystem_role",
            "ai_evidence_strength",
            "inferred_root_cause",
            "signal_pattern",
            "interesting_terms",
            "ai_relevance_reason",
        ],
    )
    write_csv(
        paths["ecosystem_signal_pages"],
        signal_rows,
        [
            "rank",
            "domain",
            "url",
            "retention_group",
            "page_type_guess",
            "discovery_method",
            "ecosystem_relevance_score",
            "ai_ecosystem_role",
            "ai_evidence_strength",
            "signal_pattern",
            "interesting_terms",
            "title",
            "ai_relevance_reason",
            "visible_text_preview_excerpt",
        ],
    )
    write_csv(
        paths["cap_pressure_analysis"],
        cap_rows,
        [
            "domain",
            "cap_pressure_domain",
            "page_count",
            "retained_count",
            "discarded_count",
            "retained_signal_count",
            "discarded_signal_count",
            "retained_average_ecosystem_score",
            "discarded_average_ecosystem_score",
            "max_ecosystem_score",
            "top_signal_patterns",
        ],
    )
    write_csv(
        paths["discovery_method_analysis"],
        method_rows,
        [
            "discovery_method",
            "page_count",
            "retained_count",
            "discarded_count",
            "average_ecosystem_score",
            "ecosystem_signal_count",
            "ecosystem_signal_rate",
            "false_negative_count",
            "false_negative_rate",
            "average_firm_score",
            "top_signal_patterns",
        ],
    )
    write_csv(
        paths["ai_role_examples"],
        role_rows,
        [
            "domain",
            "url",
            "retention_group",
            "page_type_guess",
            "discovery_method",
            "ecosystem_relevance_score",
            "ai_ecosystem_role",
            "ai_evidence_strength",
            "signal_pattern",
            "title",
            "ai_relevance_reason",
            "visible_text_preview_excerpt",
        ],
    )
    write_json(paths["json"], summary)
    if update_project_status:
        append_project_status(project_root, run_id, summary)

    return {
        "rows": rows,
        "ecosystem_signal_pages": signal_rows,
        "false_negative_root_causes": false_negative_rows,
        "cap_pressure_analysis": cap_rows,
        "discovery_method_analysis": method_rows,
        "ai_role_examples": role_rows,
        "token_patterns": token_rows,
        "summary": summary,
        "paths": paths,
    }


def print_summary(result: dict) -> None:
    summary = result["summary"]
    print("Ecosystem signal deep dive")
    print(f"Run ID: {summary['run_id']}")
    print(f"Pages analyzed: {summary['pages_analyzed']}")
    print(f"Ecosystem-signal pages: {summary['ecosystem_signal_pages']} ({summary['ecosystem_signal_rate']}%)")
    print(f"False negatives: {summary['false_negative_count']}")
    print()
    print("Top signal domains")
    for domain, count in list(summary["top_signal_domains"].items())[:8]:
        print(f"  {count} | {domain}")
    print()
    print("Signal by discovery method")
    for method, count in summary["signal_by_discovery_method"].items():
        print(f"  {count} | {method}")
    print()
    print("False-negative root causes")
    for cause, count in summary["false_negative_root_causes"].items():
        print(f"  {count} | {cause}")
    print()
    print("Generated files")
    for path in result["paths"].values():
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deep-dive analysis of validation ecosystem signal.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-version", default="v2")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--skip-project-status", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = analyze_ecosystem_signal_deep_dive(
        run_id=args.run_id,
        prompt_version=args.prompt_version,
        model_name=args.model,
        update_project_status=not args.skip_project_status,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
