"""Build reproducible retained-vs-discarded validation samples.

This module only constructs local samples. It does not call OpenAI and does not
inspect page content.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ai_ecosystem.db.init_db import (
    find_project_root,
    load_config,
    resolve_database_path,
    resolve_project_path,
)
from ai_ecosystem.validation.config import load_validation_config


CAP_PRESSURE_DOMAINS = (
    "sprechen.com",
    "museum-joanneum.at",
    "bertrandt.com",
    "consultatio.at",
    "uniqa.at",
)

SAMPLE_DEFINITIONS = {
    "smoke": {
        "sample_id": "smoke_v1",
        "sample_version": "v1",
        "sample_type": "smoke",
        "target_per_group_config_key": "smoke_sample_size_per_group",
        "default_target_per_group": 5,
    },
    "main": {
        "sample_id": "main_v1",
        "sample_version": "v1",
        "sample_type": "main",
        "target_per_group_config_key": "main_sample_size_per_group",
        "default_target_per_group": 300,
    },
}

SAMPLE_COLUMNS = [
    "sample_id",
    "sample_version",
    "sample_type",
    "scraped_page_id",
    "discovered_page_id",
    "candidate_page_id",
    "domain",
    "url",
    "retained_at_discovery",
    "page_type_guess",
    "priority",
    "discovery_method",
    "selection_reason",
    "created_at",
]

AUDIT_COLUMNS = [
    "sample_id",
    "sample_type",
    "group",
    "selected_count",
    "unique_domains",
    "unique_page_types",
    "average_priority",
    "top_domains",
    "top_page_types",
]


@dataclass(frozen=True)
class EligiblePage:
    scraped_page_id: int
    discovered_page_id: int
    candidate_page_id: int | None
    domain: str
    url: str
    retained_at_discovery: int
    page_type_guess: str
    priority: int
    discovery_method: str
    html_path: Path


@dataclass(frozen=True)
class SelectedPage:
    page: EligiblePage
    selection_reason: str


def stable_sort_key(seed: int, sample_id: str, group_label: str, page: EligiblePage) -> str:
    value = "|".join(
        [
            str(seed),
            sample_id,
            group_label,
            page.domain,
            page.page_type_guess,
            str(page.scraped_page_id),
        ]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_html_path(project_root: Path, html_path_value: str) -> Path:
    path = Path(html_path_value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def load_eligible_pages(
    connection: sqlite3.Connection,
    project_root: Path,
    retained_at_discovery: int,
) -> list[EligiblePage]:
    rows = connection.execute(
        """
        SELECT
            sp.scraped_page_id,
            sp.discovered_page_id,
            sp.candidate_page_id,
            sp.domain,
            sp.url,
            dp.retained_at_discovery,
            COALESCE(dp.page_type_guess, 'unknown') AS page_type_guess,
            COALESCE(dp.priority, 0) AS priority,
            COALESCE(dp.discovery_method, 'unknown') AS discovery_method,
            sp.html_path
        FROM scraped_pages AS sp
        INNER JOIN discovered_pages AS dp
            ON dp.discovered_page_id = sp.discovered_page_id
        WHERE sp.scrape_status = 'success'
          AND sp.html_path IS NOT NULL
          AND sp.html_path != ''
          AND sp.discovered_page_id IS NOT NULL
          AND dp.retained_at_discovery = ?
        ORDER BY sp.domain, sp.url, sp.scraped_page_id
        """,
        (retained_at_discovery,),
    ).fetchall()

    eligible: list[EligiblePage] = []
    for row in rows:
        html_path = resolve_html_path(project_root, row[9])
        if not html_path.is_file():
            continue
        eligible.append(
            EligiblePage(
                scraped_page_id=row[0],
                discovered_page_id=row[1],
                candidate_page_id=row[2],
                domain=row[3],
                url=row[4],
                retained_at_discovery=row[5],
                page_type_guess=row[6],
                priority=row[7],
                discovery_method=row[8],
                html_path=html_path,
            )
        )
    return eligible


def select_validation_group(
    pages: list[EligiblePage],
    target: int,
    *,
    sample_id: str,
    group_label: str,
    seed: int,
    initial_domain_cap: int,
) -> tuple[list[SelectedPage], bool]:
    selected: list[SelectedPage] = []
    selected_ids: set[int] = set()
    domain_counts: defaultdict[str, int] = defaultdict(int)
    relaxed = False

    def sorted_pages(candidates: Iterable[EligiblePage]) -> list[EligiblePage]:
        return sorted(candidates, key=lambda page: stable_sort_key(seed, sample_id, group_label, page))

    def add_page(page: EligiblePage, reason: str, domain_cap: int) -> bool:
        if len(selected) >= target:
            return False
        if page.scraped_page_id in selected_ids:
            return False
        if domain_counts[page.domain] >= domain_cap:
            return False
        selected_ids.add(page.scraped_page_id)
        domain_counts[page.domain] += 1
        reason_value = reason if domain_cap == initial_domain_cap else f"{reason}_relaxed_domain_cap"
        selected.append(SelectedPage(page=page, selection_reason=reason_value))
        return True

    cap_pressure_candidates = [
        page for page in pages if page.domain in CAP_PRESSURE_DOMAINS
    ]
    cap_pressure_order = {domain: index for index, domain in enumerate(CAP_PRESSURE_DOMAINS)}
    for page in sorted(
        cap_pressure_candidates,
        key=lambda page: (
            cap_pressure_order.get(page.domain, len(CAP_PRESSURE_DOMAINS)),
            stable_sort_key(seed, sample_id, group_label, page),
        ),
    ):
        add_page(page, "cap_pressure_domain", initial_domain_cap)

    current_cap = initial_domain_cap
    while len(selected) < target and len(selected) < len(pages):
        added_this_round = False
        page_types = sorted({page.page_type_guess for page in pages})
        for page_type in page_types:
            for page in sorted_pages(page for page in pages if page.page_type_guess == page_type):
                if add_page(page, "page_type_stratification", current_cap):
                    added_this_round = True
                    break
            if len(selected) >= target:
                break

        for page in sorted_pages(pages):
            if add_page(page, "random_fill", current_cap):
                added_this_round = True
            if len(selected) >= target:
                break

        if len(selected) >= target or len(selected) >= len(pages):
            break
        if not added_this_round:
            current_cap += 1
            relaxed = True

    return selected, relaxed


def write_sample_csv(connection: sqlite3.Connection, csv_path: Path, sample_id: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = connection.execute(
        f"""
        SELECT {", ".join(SAMPLE_COLUMNS)}
        FROM retention_validation_sample
        WHERE sample_id = ?
        ORDER BY retained_at_discovery DESC, domain, page_type_guess, scraped_page_id
        """,
        (sample_id,),
    ).fetchall()
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(SAMPLE_COLUMNS)
        writer.writerows(rows)


def top_counts(values: Iterable[str], limit: int = 10) -> str:
    counts = Counter(values)
    return "; ".join(f"{value}:{count}" for value, count in counts.most_common(limit))


def audit_row(sample_id: str, sample_type: str, group: str, selected: list[SelectedPage]) -> dict[str, object]:
    priorities = [item.page.priority for item in selected]
    average_priority = round(sum(priorities) / len(priorities), 2) if priorities else 0
    return {
        "sample_id": sample_id,
        "sample_type": sample_type,
        "group": group,
        "selected_count": len(selected),
        "unique_domains": len({item.page.domain for item in selected}),
        "unique_page_types": len({item.page.page_type_guess for item in selected}),
        "average_priority": average_priority,
        "top_domains": top_counts(item.page.domain for item in selected),
        "top_page_types": top_counts(item.page.page_type_guess for item in selected),
    }


def write_audit_csv(
    audit_path: Path,
    sample_id: str,
    sample_type: str,
    retained: list[SelectedPage],
    discarded: list[SelectedPage],
) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        audit_row(sample_id, sample_type, "retained", retained),
        audit_row(sample_id, sample_type, "discarded", discarded),
    ]
    with audit_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=AUDIT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def insert_sample_rows(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    sample_version: str,
    sample_type: str,
    retained: list[SelectedPage],
    discarded: list[SelectedPage],
) -> None:
    connection.execute("DELETE FROM retention_validation_sample WHERE sample_id = ?", (sample_id,))
    rows = []
    for selected in [*retained, *discarded]:
        page = selected.page
        rows.append(
            (
                sample_id,
                sample_version,
                sample_type,
                page.scraped_page_id,
                page.discovered_page_id,
                page.candidate_page_id,
                page.domain,
                page.url,
                page.retained_at_discovery,
                page.page_type_guess,
                page.priority,
                page.discovery_method,
                selected.selection_reason,
            )
        )
    connection.executemany(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def build_validation_sample(
    sample: str,
    *,
    project_root: Path | None = None,
) -> dict[str, object]:
    if sample not in SAMPLE_DEFINITIONS:
        valid = ", ".join(sorted(SAMPLE_DEFINITIONS))
        raise ValueError(f"Unknown sample '{sample}'. Expected one of: {valid}")

    root = project_root or find_project_root()
    config = load_config(root)
    validation = load_validation_config(root)
    sample_definition = SAMPLE_DEFINITIONS[sample]
    sample_id = sample_definition["sample_id"]
    sample_type = sample_definition["sample_type"]
    sample_version = sample_definition["sample_version"]
    target = int(
        validation.get(
            sample_definition["target_per_group_config_key"],
            sample_definition["default_target_per_group"],
        )
    )
    seed = int(validation.get("random_seed", 20260608))
    max_pages_per_domain = int(validation.get("max_pages_per_domain", 3))
    interim_dir = resolve_project_path(root, config.get("paths", {}).get("interim_dir", "data/interim"))
    sample_csv_path = interim_dir / f"validation_sample_{sample_id}.csv"
    audit_csv_path = interim_dir / f"validation_sample_audit_{sample_id}.csv"
    db_path = resolve_database_path(root, config)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        from ai_ecosystem.db.init_db import ensure_validation_schema

        ensure_validation_schema(connection)
        retained_pages = load_eligible_pages(connection, root, 1)
        discarded_pages = load_eligible_pages(connection, root, 0)
        retained, retained_relaxed = select_validation_group(
            retained_pages,
            target,
            sample_id=sample_id,
            group_label="retained",
            seed=seed,
            initial_domain_cap=max_pages_per_domain,
        )
        discarded, discarded_relaxed = select_validation_group(
            discarded_pages,
            target,
            sample_id=sample_id,
            group_label="discarded",
            seed=seed,
            initial_domain_cap=max_pages_per_domain,
        )
        insert_sample_rows(
            connection,
            sample_id=sample_id,
            sample_version=sample_version,
            sample_type=sample_type,
            retained=retained,
            discarded=discarded,
        )
        connection.commit()
        write_sample_csv(connection, sample_csv_path, sample_id)
        write_audit_csv(audit_csv_path, sample_id, sample_type, retained, discarded)

    summary = {
        "sample_id": sample_id,
        "sample_version": sample_version,
        "sample_type": sample_type,
        "target_per_group": target,
        "retained_selected": len(retained),
        "discarded_selected": len(discarded),
        "unique_domains": len({item.page.domain for item in [*retained, *discarded]}),
        "unique_page_types": len({item.page.page_type_guess for item in [*retained, *discarded]}),
        "top_domains": top_counts(item.page.domain for item in [*retained, *discarded]),
        "top_page_types": top_counts(item.page.page_type_guess for item in [*retained, *discarded]),
        "domain_cap_relaxed": retained_relaxed or discarded_relaxed,
        "sample_csv_path": str(sample_csv_path),
        "audit_csv_path": str(audit_csv_path),
        "target_achieved": len(retained) == target and len(discarded) == target,
    }
    print_summary(summary)
    return summary


def print_summary(summary: dict[str, object]) -> None:
    print(f"Validation sample: {summary['sample_id']}")
    print(f"Retained pages selected: {summary['retained_selected']}")
    print(f"Discarded pages selected: {summary['discarded_selected']}")
    print(f"Unique domains: {summary['unique_domains']}")
    print(f"Unique page types: {summary['unique_page_types']}")
    print(f"Top domains: {summary['top_domains']}")
    print(f"Top page types: {summary['top_page_types']}")
    print(f"Domain cap relaxed: {summary['domain_cap_relaxed']}")
    print(f"Sample CSV: {summary['sample_csv_path']}")
    print(f"Audit CSV: {summary['audit_csv_path']}")
    if not summary["target_achieved"]:
        print()
        print("WARNING:")
        print("Target sample size not achieved.")
        print()
        print(f"Retained: {summary['retained_selected']} / {summary['target_per_group']}")
        print(f"Discarded: {summary['discarded_selected']} / {summary['target_per_group']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build retained-vs-discarded validation samples.")
    parser.add_argument("--sample", choices=sorted(SAMPLE_DEFINITIONS), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_validation_sample(args.sample)


if __name__ == "__main__":
    main()
