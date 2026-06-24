"""Discover transparent patterns that distinguish ecosystem-signal pages."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path
from ai_ecosystem.validation.analyze_ecosystem_signal_deep_dive import (
    EVIDENCE_RANK,
    false_negative_cause,
    load_rows,
    load_run,
)
from ai_ecosystem.validation.analyze_retention_validation import CAP_PRESSURE_DOMAINS, avg, pct, write_csv, write_json


STOPWORDS = {
    "aber",
    "about",
    "alle",
    "also",
    "and",
    "are",
    "activity",
    "auf",
    "aus",
    "bei",
    "but",
    "cannot",
    "contains",
    "could",
    "com",
    "das",
    "der",
    "direct",
    "directly",
    "die",
    "ein",
    "evidence",
    "eine",
    "emphasizes",
    "explicit",
    "explicitly",
    "for",
    "from",
    "für",
    "however",
    "https",
    "im",
    "information",
    "in",
    "involvement",
    "lists",
    "mit",
    "mention",
    "mentioned",
    "mentions",
    "not",
    "organization",
    "oder",
    "page",
    "pages",
    "possible",
    "provides",
    "specific",
    "suggestive",
    "suggests",
    "that",
    "the",
    "there",
    "therefore",
    "this",
    "und",
    "visible",
    "von",
    "website",
    "which",
    "without",
    "with",
    "www",
}
MIN_PATTERN_COUNT = 2
MIN_DISTINGUISHING_LIFT = 1.2


def token_list(text: str | None) -> list[str]:
    if not text:
        return []
    tokens = re.split(r"[^A-Za-zÄÖÜäöüß0-9]+", text.lower())
    return [
        token
        for token in tokens
        if len(token) >= 3 and not token.isdigit() and token not in STOPWORDS
    ]


def ngrams(tokens: list[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]


def url_tokens(url: str | None) -> list[str]:
    return token_list(url)


def evidence_tokens(row: dict) -> list[str]:
    return token_list(" ".join([row.get("ai_relevance_reason") or "", row.get("evidence_snippet") or ""]))


def theme_tokens(row: dict) -> list[str]:
    text = " ".join(
        [
            row.get("title") or "",
            row.get("meta_description") or "",
            row.get("ai_relevance_reason") or "",
            row.get("visible_text_preview") or "",
        ]
    )
    tokens = token_list(text)
    return tokens + ngrams(tokens, 2)


def purpose_key(row: dict) -> str:
    category = row.get("page_category_llm") or "unknown_category"
    page_type = row.get("page_type_guess") or "unknown_type"
    return f"{category}|{page_type}"


def evidence_type_key(row: dict) -> str:
    role = row.get("ai_ecosystem_role") or "unknown_role"
    strength = row.get("ai_evidence_strength") or "unknown_strength"
    tokens = evidence_tokens(row)
    token_hint = tokens[0] if tokens else "no_evidence_token"
    return f"{role}|{strength}|{token_hint}"


def retention_group(row: dict) -> str:
    return "retained" if row.get("retained_at_discovery") == 1 else "discarded"


def is_signal(row: dict) -> bool:
    return float(row.get("ecosystem_relevance_score") or 0) >= 3


def is_non_signal(row: dict) -> bool:
    return float(row.get("ecosystem_relevance_score") or 0) == 1


def contrast_lift(signal_count: int, signal_total: int, baseline_count: int, baseline_total: int) -> float:
    if not signal_total or not baseline_total:
        return 0.0
    signal_rate = signal_count / signal_total
    baseline_rate = baseline_count / baseline_total
    if baseline_rate == 0:
        return round(signal_rate / (0.5 / baseline_total), 3)
    return round(signal_rate / baseline_rate, 3)


def row_weight(row: dict) -> float:
    score = float(row.get("ecosystem_relevance_score") or 0)
    evidence_bonus = {"strong": 1.0, "moderate": 0.5, "weak": 0.1, "none": 0.0}.get(
        row.get("ai_evidence_strength"),
        0.0,
    )
    return score + evidence_bonus


def example_fields(rows: list[dict], limit: int = 5) -> tuple[str, str, str]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get("ecosystem_relevance_score") or 0),
            -EVIDENCE_RANK.get(row.get("ai_evidence_strength"), 0),
            row.get("domain") or "",
            row.get("url") or "",
        ),
    )[:limit]
    terms = Counter()
    for row in ordered:
        terms.update(theme_tokens(row)[:20])
    domains = []
    urls = []
    for row in ordered:
        domain = row.get("domain") or ""
        url = row.get("url") or ""
        if domain and domain not in domains:
            domains.append(domain)
        if url and url not in urls:
            urls.append(url)
    return (
        ";".join(term for term, _count in terms.most_common(8)),
        ";".join(domains),
        ";".join(urls),
    )


def collect_patterns(
    rows: list[dict],
    extractor,
    pattern_type: str,
    *,
    min_lift: float = MIN_DISTINGUISHING_LIFT,
) -> list[dict]:
    signal_rows = [row for row in rows if is_signal(row)]
    non_signal_rows = [row for row in rows if is_non_signal(row)]
    signal_map: dict[str, list[dict]] = defaultdict(list)
    non_signal_map: dict[str, list[dict]] = defaultdict(list)
    for row in signal_rows:
        for pattern in set(extractor(row)):
            signal_map[pattern].append(row)
    for row in non_signal_rows:
        for pattern in set(extractor(row)):
            non_signal_map[pattern].append(row)

    output = []
    for pattern, pattern_signal_rows in signal_map.items():
        signal_count = len(pattern_signal_rows)
        if signal_count < MIN_PATTERN_COUNT:
            continue
        baseline_count = len(non_signal_map.get(pattern, []))
        lift = contrast_lift(signal_count, len(signal_rows), baseline_count, len(non_signal_rows))
        if lift < min_lift:
            continue
        representative_terms, representative_domains, representative_urls = example_fields(pattern_signal_rows)
        weighted = round(sum(row_weight(row) for row in pattern_signal_rows), 3)
        output.append(
            {
                "pattern_type": pattern_type,
                "pattern": pattern,
                "signal_count": signal_count,
                "non_signal_count": baseline_count,
                "signal_rate": pct(signal_count, len(signal_rows)),
                "non_signal_rate": pct(baseline_count, len(non_signal_rows)),
                "lift": lift,
                "ecosystem_score_weighted_frequency": weighted,
                "average_signal_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in pattern_signal_rows),
                "representative_terms": representative_terms,
                "representative_domains": representative_domains,
                "representative_urls": representative_urls,
            }
        )
    return sorted(
        output,
        key=lambda row: (
            -float(row["ecosystem_score_weighted_frequency"]),
            -float(row["lift"]),
            -int(row["signal_count"]),
            row["pattern"],
        ),
    )


def theme_analysis(rows: list[dict]) -> list[dict]:
    return collect_patterns(rows, theme_tokens, "theme")


def url_pattern_analysis(rows: list[dict]) -> list[dict]:
    return collect_patterns(rows, lambda row: url_tokens(row.get("url")), "url_token")


def page_purpose_analysis(rows: list[dict]) -> list[dict]:
    return collect_patterns(rows, lambda row: [purpose_key(row)], "page_purpose", min_lift=0.0)


def evidence_type_analysis(rows: list[dict]) -> list[dict]:
    return collect_patterns(
        rows,
        lambda row: [evidence_type_key(row), row.get("ai_evidence_strength") or "unknown_strength"]
        + evidence_tokens(row)
        + ngrams(evidence_tokens(row), 2),
        "evidence_type",
    )


def false_negative_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if is_signal(row) and row.get("retained_at_discovery") == 0]


def retained_signal_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if is_signal(row) and row.get("retained_at_discovery") == 1]


def comparison_against_retained_signal(
    false_rows: list[dict],
    retained_rows: list[dict],
    extractor,
    comparison_type: str,
) -> list[dict]:
    false_map: dict[str, list[dict]] = defaultdict(list)
    retained_map: dict[str, list[dict]] = defaultdict(list)
    for row in false_rows:
        for pattern in set(extractor(row)):
            false_map[pattern].append(row)
    for row in retained_rows:
        for pattern in set(extractor(row)):
            retained_map[pattern].append(row)
    output = []
    for pattern, pattern_false_rows in false_map.items():
        if len(pattern_false_rows) < 1:
            continue
        retained_count = len(retained_map.get(pattern, []))
        representative_terms, representative_domains, representative_urls = example_fields(pattern_false_rows)
        output.append(
            {
                "comparison_type": comparison_type,
                "pattern": pattern,
                "false_negative_count": len(pattern_false_rows),
                "retained_signal_count": retained_count,
                "lift_vs_retained_signal": contrast_lift(
                    len(pattern_false_rows),
                    len(false_rows),
                    retained_count,
                    len(retained_rows),
                ),
                "ecosystem_score_weighted_frequency": round(sum(row_weight(row) for row in pattern_false_rows), 3),
                "common_discard_reasons": ";".join(
                    f"{reason}:{count}"
                    for reason, count in Counter(row.get("discovered_discard_reason") or "unknown" for row in pattern_false_rows).most_common(5)
                ),
                "inferred_loss_explanation": ";".join(
                    f"{cause}:{count}"
                    for cause, count in Counter(false_negative_cause(row) for row in pattern_false_rows).most_common(5)
                ),
                "representative_terms": representative_terms,
                "representative_domains": representative_domains,
                "representative_urls": representative_urls,
            }
        )
    return sorted(
        output,
        key=lambda row: (
            -float(row["ecosystem_score_weighted_frequency"]),
            -float(row["lift_vs_retained_signal"]),
            -int(row["false_negative_count"]),
            row["pattern"],
        ),
    )


def false_negative_comparison(rows: list[dict]) -> list[dict]:
    false_rows = false_negative_rows(rows)
    retained_rows = retained_signal_rows(rows)
    output: list[dict] = []
    for extractor, comparison_type in [
        (theme_tokens, "theme"),
        (lambda row: url_tokens(row.get("url")), "url_pattern"),
        (lambda row: [purpose_key(row)], "page_purpose"),
        (
            lambda row: [evidence_type_key(row), row.get("ai_evidence_strength") or "unknown_strength"]
            + evidence_tokens(row)
            + ngrams(evidence_tokens(row), 2),
            "evidence_type",
        ),
        (lambda row: [row.get("discovery_method") or "unknown"], "discovery_method"),
    ]:
        output.extend(comparison_against_retained_signal(false_rows, retained_rows, extractor, comparison_type))
    return output


def sortable_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def retention_cap_comparison(rows: list[dict]) -> list[dict]:
    signal_rows = [row for row in rows if is_signal(row)]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in signal_rows:
        if row.get("domain") in CAP_PRESSURE_DOMAINS or row.get("discovered_discard_reason") == "retention_cap":
            grouped[row.get("domain") or "unknown"].append(row)
    output = []
    for domain, domain_rows in sorted(grouped.items()):
        retained = [row for row in domain_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in domain_rows if row.get("retained_at_discovery") == 0]
        discarded_terms, discarded_domains, discarded_urls = example_fields(discarded)
        retained_terms, _retained_domains, retained_urls = example_fields(retained)
        output.append(
            {
                "domain": domain,
                "cap_pressure_domain": int(domain in CAP_PRESSURE_DOMAINS),
                "retained_signal_count": len(retained),
                "discarded_signal_count": len(discarded),
                "retained_weighted_frequency": round(sum(row_weight(row) for row in retained), 3),
                "discarded_weighted_frequency": round(sum(row_weight(row) for row in discarded), 3),
                "retained_average_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in retained),
                "discarded_average_ecosystem_score": avg(row.get("ecosystem_relevance_score") for row in discarded),
                "discarded_representative_terms": discarded_terms,
                "retained_representative_terms": retained_terms,
                "representative_domains": discarded_domains or domain,
                "discarded_representative_urls": discarded_urls,
                "retained_representative_urls": retained_urls,
                "common_discard_reasons": ";".join(
                    f"{reason}:{count}"
                    for reason, count in Counter(row.get("discovered_discard_reason") or "unknown" for row in discarded).most_common(5)
                ),
            }
        )
    return sorted(
        output,
        key=lambda row: (
            -float(row["discarded_weighted_frequency"]),
            -int(row["discarded_signal_count"]),
            row["domain"],
        ),
    )


def discovery_method_context(rows: list[dict]) -> list[dict]:
    signal_rows = [row for row in rows if is_signal(row)]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in signal_rows:
        grouped[row.get("discovery_method") or "unknown"].append(row)
    output = []
    for method, method_rows in sorted(grouped.items()):
        retained = [row for row in method_rows if row.get("retained_at_discovery") == 1]
        discarded = [row for row in method_rows if row.get("retained_at_discovery") == 0]
        terms, domains, urls = example_fields(method_rows)
        output.append(
            {
                "comparison_type": "discovery_method_signal_context",
                "pattern": method,
                "false_negative_count": len(discarded),
                "retained_signal_count": len(retained),
                "lift_vs_retained_signal": "",
                "ecosystem_score_weighted_frequency": round(sum(row_weight(row) for row in method_rows), 3),
                "common_discard_reasons": ";".join(
                    f"{reason}:{count}"
                    for reason, count in Counter(row.get("discovered_discard_reason") or "unknown" for row in discarded).most_common(5)
                ),
                "inferred_loss_explanation": ";".join(
                    f"{cause}:{count}"
                    for cause, count in Counter(false_negative_cause(row) for row in discarded).most_common(5)
                ),
                "representative_terms": terms,
                "representative_domains": domains,
                "representative_urls": urls,
            }
        )
    return output


def output_paths(project_root: Path) -> dict[str, Path]:
    interim = project_root / "data" / "interim"
    logs = project_root / "logs"
    return {
        "themes": interim / "ecosystem_signal_theme_analysis.csv",
        "url_patterns": interim / "ecosystem_signal_url_patterns.csv",
        "page_purposes": interim / "ecosystem_signal_page_purposes.csv",
        "evidence_types": interim / "ecosystem_signal_evidence_types.csv",
        "false_negative_comparison": interim / "ecosystem_signal_false_negative_comparison.csv",
        "retention_cap_comparison": interim / "ecosystem_signal_retention_cap_comparison.csv",
        "json": logs / "ecosystem_signal_pattern_discovery.json",
    }


def pattern_fieldnames() -> list[str]:
    return [
        "pattern_type",
        "pattern",
        "signal_count",
        "non_signal_count",
        "signal_rate",
        "non_signal_rate",
        "lift",
        "ecosystem_score_weighted_frequency",
        "average_signal_ecosystem_score",
        "representative_terms",
        "representative_domains",
        "representative_urls",
    ]


def false_negative_fieldnames() -> list[str]:
    return [
        "comparison_type",
        "pattern",
        "false_negative_count",
        "retained_signal_count",
        "lift_vs_retained_signal",
        "ecosystem_score_weighted_frequency",
        "common_discard_reasons",
        "inferred_loss_explanation",
        "representative_terms",
        "representative_domains",
        "representative_urls",
    ]


def json_summary(
    *,
    run_id: str,
    rows: list[dict],
    themes: list[dict],
    urls: list[dict],
    purposes: list[dict],
    evidence: list[dict],
    false_comparison: list[dict],
    cap_comparison: list[dict],
    paths: dict[str, Path],
) -> dict:
    signal_rows = [row for row in rows if is_signal(row)]
    non_signal_rows = [row for row in rows if is_non_signal(row)]
    retained_signal = retained_signal_rows(rows)
    false_negatives = false_negative_rows(rows)
    return {
        "run_id": run_id,
        "pages_analyzed": len(rows),
        "signal_pages": len(signal_rows),
        "non_signal_pages": len(non_signal_rows),
        "score_2_context_pages": len(rows) - len(signal_rows) - len(non_signal_rows),
        "retained_signal_pages": len(retained_signal),
        "discarded_signal_pages": len(false_negatives),
        "executive_summary": {
            "what_creates_ecosystem_signal": [
                f"{row['pattern']} ({row['representative_terms']})" for row in themes[:8]
            ],
            "recurring_page_purposes": [
                f"{row['pattern']} ({row['representative_domains']})" for row in purposes[:8]
            ],
            "recurring_url_patterns": [
                f"{row['pattern']} ({row['representative_domains']})" for row in urls[:8]
            ],
            "recurring_evidence_types": [
                f"{row['pattern']} ({row['representative_terms']})" for row in evidence[:8]
            ],
            "signal_vs_non_signal_differentiators": [
                f"{row['pattern']} lift={row['lift']} weighted={row['ecosystem_score_weighted_frequency']}"
                for row in (themes + urls)[:10]
            ],
            "false_negative_differentiators": [
                f"{row['comparison_type']}:{row['pattern']} false_negative_count={row['false_negative_count']} retained_signal_count={row['retained_signal_count']}"
                for row in false_comparison[:10]
            ],
            "ecosystem_signal_most_likely_to_be_lost": [
                f"{row['pattern']} via {row['inferred_loss_explanation']}" for row in false_comparison[:8]
            ],
            "future_retention_design_evidence": "Evidence only: high-weight lost patterns, URL structures, and page purposes are exported for manual review; no rule changes are recommended here.",
        },
        "top_weighted_themes": themes[:20],
        "top_weighted_url_patterns": urls[:20],
        "top_weighted_page_purposes": purposes[:20],
        "top_weighted_evidence_types": evidence[:20],
        "what_did_we_miss": false_comparison[:30],
        "retention_cap_comparison": cap_comparison[:30],
        "outputs": {name: str(path) for name, path in paths.items()},
    }


def append_project_status(project_root: Path, run_id: str, summary: dict) -> None:
    path = project_root / "docs" / "project_status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").rstrip() if path.exists() else "# Project Status\n"
    if "Milestone 3A.5i.1 — Ecosystem Signal Pattern Discovery" in existing and run_id in existing:
        return
    top_theme = summary["top_weighted_themes"][0]["pattern"] if summary["top_weighted_themes"] else "none"
    top_lost = summary["what_did_we_miss"][0]["pattern"] if summary["what_did_we_miss"] else "none"
    entry = f"""

## {date.today().isoformat()} — Milestone 3A.5i.1: Ecosystem Signal Pattern Discovery

### Date

{date.today().isoformat()}

### Ticket / Milestone

Milestone 3A.5i.1 — Ecosystem Signal Pattern Discovery

### Objective

Identify what differentiates ecosystem-signal pages from non-signal pages, and what ecosystem signal the current retention process is most likely to lose.

### Implementation Summary

Added a read-only pattern-discovery utility using transparent token, n-gram, lift, and ecosystem-score-weighted analyses over stored validation outputs and text previews.

### Key Findings

- Validation run analyzed: `{run_id}`
- Signal pages: {summary['signal_pages']}
- Non-signal pages: {summary['non_signal_pages']}
- Discarded signal pages: {summary['discarded_signal_pages']}
- Top weighted theme: {top_theme}
- Top lost pattern: {top_lost}

### Current Status

Pattern-discovery reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the pattern evidence manually before deciding whether retention-priority experiments are warranted.
"""
    path.write_text(existing + "\n" + entry.strip() + "\n", encoding="utf-8")


def analyze_ecosystem_signal_patterns(
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

    themes = theme_analysis(rows)
    urls = url_pattern_analysis(rows)
    purposes = page_purpose_analysis(rows)
    evidence = evidence_type_analysis(rows)
    false_comparison = false_negative_comparison(rows)
    false_comparison.extend(discovery_method_context(rows))
    false_comparison = sorted(
        false_comparison,
        key=lambda row: (
            -sortable_float(row["ecosystem_score_weighted_frequency"]),
            -sortable_float(row["lift_vs_retained_signal"]),
            row["comparison_type"],
            row["pattern"],
        ),
    )
    cap_comparison = retention_cap_comparison(rows)
    paths = output_paths(project_root)
    summary = json_summary(
        run_id=run_id,
        rows=rows,
        themes=themes,
        urls=urls,
        purposes=purposes,
        evidence=evidence,
        false_comparison=false_comparison,
        cap_comparison=cap_comparison,
        paths=paths,
    )

    write_csv(paths["themes"], themes, pattern_fieldnames())
    write_csv(paths["url_patterns"], urls, pattern_fieldnames())
    write_csv(paths["page_purposes"], purposes, pattern_fieldnames())
    write_csv(paths["evidence_types"], evidence, pattern_fieldnames())
    write_csv(paths["false_negative_comparison"], false_comparison, false_negative_fieldnames())
    write_csv(
        paths["retention_cap_comparison"],
        cap_comparison,
        [
            "domain",
            "cap_pressure_domain",
            "retained_signal_count",
            "discarded_signal_count",
            "retained_weighted_frequency",
            "discarded_weighted_frequency",
            "retained_average_ecosystem_score",
            "discarded_average_ecosystem_score",
            "discarded_representative_terms",
            "retained_representative_terms",
            "representative_domains",
            "discarded_representative_urls",
            "retained_representative_urls",
            "common_discard_reasons",
        ],
    )
    write_json(paths["json"], summary)
    if update_project_status:
        append_project_status(project_root, run_id, summary)

    return {
        "rows": rows,
        "themes": themes,
        "url_patterns": urls,
        "page_purposes": purposes,
        "evidence_types": evidence,
        "false_negative_comparison": false_comparison,
        "retention_cap_comparison": cap_comparison,
        "summary": summary,
        "paths": paths,
    }


def print_summary(result: dict) -> None:
    summary = result["summary"]
    print("Ecosystem signal pattern discovery")
    print(f"Run ID: {summary['run_id']}")
    print(f"Signal pages: {summary['signal_pages']}")
    print(f"Non-signal pages: {summary['non_signal_pages']}")
    print(f"Discarded signal pages: {summary['discarded_signal_pages']}")
    print()
    print("Top weighted themes")
    for row in result["themes"][:8]:
        print(
            f"  {row['ecosystem_score_weighted_frequency']} | {row['pattern']} "
            f"lift={row['lift']} examples={row['representative_domains']}"
        )
    print()
    print("What did we miss?")
    for row in result["false_negative_comparison"][:8]:
        print(
            f"  {row['ecosystem_score_weighted_frequency']} | {row['comparison_type']}:{row['pattern']} "
            f"lost={row['false_negative_count']} retained={row['retained_signal_count']}"
        )
    print()
    print("Generated files")
    for path in result["paths"].values():
        print(f"  {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover patterns that distinguish ecosystem-signal pages.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-version", default="v2")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--skip-project-status", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = analyze_ecosystem_signal_patterns(
        run_id=args.run_id,
        prompt_version=args.prompt_version,
        model_name=args.model,
        update_project_status=not args.skip_project_status,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
