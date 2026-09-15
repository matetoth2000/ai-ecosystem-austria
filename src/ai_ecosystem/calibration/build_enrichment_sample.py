"""Build a reproducible, ÖNACE-based company calibration enrichment sample.

The selection is intentionally restricted to imported firm activity codes and
existing domain eligibility metadata.  It never reads website content, scores,
or calibration labels and never starts another pipeline stage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from ai_ecosystem.calibration.enrichment_config import (
    EnrichmentCode,
    EnrichmentConfig,
    EnrichmentStratum,
    load_enrichment_config,
)
from ai_ecosystem.db.init_db import (
    ensure_sample_registry_schema,
    find_project_root,
    init_database,
    load_config,
    resolve_database_path,
    resolve_project_path,
    table_columns,
)
from ai_ecosystem.discovery.discovery_utils import sample_exists


STRATUM_ORDER = ("HIGH_POTENTIAL", "AI_ADJACENT", "CONVENTIONAL_CONTROL")


@dataclass(frozen=True)
class CandidateCompany:
    company_id: str
    company_name: str | None
    nace_code: str
    normalized_domain: str | None
    domain_category: str | None
    representation_level: str | None
    domain_firm_count: int | None
    domain_excluded: bool


@dataclass(frozen=True)
class SelectedCompany:
    candidate: CandidateCompany
    stratum: EnrichmentStratum
    code: EnrichmentCode
    sample_rank: int
    selection_reason: str

    @property
    def shared_domain(self) -> bool:
        return (
            (self.candidate.domain_firm_count or 0) > 1
            or self.candidate.representation_level in {"shared_domain", "shared_group_or_brand"}
        )


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _seed_for(seed: int, *parts: str) -> int:
    payload = ":".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _placeholder_list(values: Iterable[str]) -> str:
    values = tuple(values)
    if not values:
        raise ValueError("At least one value is required")
    return ", ".join("?" for _ in values)


def _configured_target_sizes(config: EnrichmentConfig, target_size: int) -> dict[str, int]:
    """Scale configured stratum targets by largest remainder if overridden."""
    configured_total = sum(stratum.target_size for stratum in config.strata)
    if target_size == configured_total:
        return {stratum.name: stratum.target_size for stratum in config.strata}

    scaled = {
        stratum.name: target_size * stratum.target_size / configured_total
        for stratum in config.strata
    }
    targets = {name: int(value) for name, value in scaled.items()}
    remaining = target_size - sum(targets.values())
    remainders = sorted(
        scaled,
        key=lambda name: (scaled[name] - targets[name], -STRATUM_ORDER.index(name)),
        reverse=True,
    )
    for name in remainders[:remaining]:
        targets[name] += 1
    return targets


def _sample_company_table_exists(connection: sqlite3.Connection) -> bool:
    return "sample_companies" in {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sample_companies'"
        )
    }


def existing_sample_company_ids(
    connection: sqlite3.Connection,
    sample_ids: Iterable[str],
) -> set[str]:
    """Resolve company membership, falling back to legacy domain samples."""
    ids = tuple(dict.fromkeys(str(sample_id) for sample_id in sample_ids))
    if not ids:
        return set()
    missing = [sample_id for sample_id in ids if not sample_exists(connection, sample_id)]
    if missing:
        raise ValueError(f"Unknown excluded sample ID(s): {', '.join(missing)}")

    placeholders = _placeholder_list(ids)
    company_ids: set[str] = set()
    if _sample_company_table_exists(connection):
        company_ids.update(
            str(row[0])
            for row in connection.execute(
                f"SELECT company_id FROM sample_companies WHERE sample_id IN ({placeholders})",
                ids,
            )
        )
    company_ids.update(
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT f.bvd_id
            FROM firms AS f
            JOIN sample_domains AS sd ON sd.domain = f.normalized_domain
            WHERE sd.sample_id IN ({placeholders})
            """,
            ids,
        )
    )
    return company_ids


def load_taxonomy_population(
    connection: sqlite3.Connection,
    config: EnrichmentConfig,
) -> list[CandidateCompany]:
    """Read the source-side population without any website-derived signal."""
    codes = tuple(config.codes_by_value)
    rows = connection.execute(
        f"""
        SELECT
            f.bvd_id,
            f.firm_name,
            f.nace_code,
            f.normalized_domain,
            d.domain_category,
            d.representation_level,
            d.firm_count,
            COALESCE(d.is_excluded, 0) AS domain_excluded
        FROM firms AS f
        LEFT JOIN domains AS d ON d.domain = f.normalized_domain
        WHERE f.bvd_id IS NOT NULL
          AND TRIM(f.bvd_id) <> ''
          AND f.nace_code IN ({_placeholder_list(codes)})
        ORDER BY f.bvd_id ASC
        """,
        codes,
    ).fetchall()
    return [
        CandidateCompany(
            company_id=str(row["bvd_id"]),
            company_name=row["firm_name"],
            nace_code=str(row["nace_code"]),
            normalized_domain=(str(row["normalized_domain"]).strip().lower() if row["normalized_domain"] else None),
            domain_category=row["domain_category"],
            representation_level=row["representation_level"],
            domain_firm_count=(int(row["firm_count"]) if row["firm_count"] is not None else None),
            domain_excluded=bool(row["domain_excluded"]),
        )
        for row in rows
    ]


def eligible_candidates(
    population: Iterable[CandidateCompany],
    *,
    config: EnrichmentConfig,
    excluded_company_ids: set[str],
) -> tuple[list[CandidateCompany], dict[str, int]]:
    """Apply only configured source/domain eligibility checks."""
    counts: Counter[str] = Counter()
    eligible: list[CandidateCompany] = []
    for candidate in population:
        if config.require_normalized_domain and not candidate.normalized_domain:
            counts["missing_normalized_domain"] += 1
            continue
        if config.exclude_marked_domains and candidate.domain_excluded:
            counts["marked_excluded_domain"] += 1
            continue
        if candidate.company_id in excluded_company_ids:
            counts["existing_sample_overlap"] += 1
            continue
        eligible.append(candidate)
    return eligible, dict(sorted(counts.items()))


def select_stratum(
    *,
    candidates: Iterable[CandidateCompany],
    stratum: EnrichmentStratum,
    target_size: int,
    seed: int,
) -> tuple[list[CandidateCompany], dict[str, int]]:
    """Select a breadth-first, deterministic sample within one configured stratum."""
    by_code: dict[str, list[CandidateCompany]] = defaultdict(list)
    configured_codes = {code.code for code in stratum.codes}
    for candidate in candidates:
        if candidate.nace_code in configured_codes:
            by_code[candidate.nace_code].append(candidate)

    queues: dict[str, list[CandidateCompany]] = {}
    for code in stratum.codes:
        queue = sorted(by_code.get(code.code, []), key=lambda candidate: candidate.company_id)
        random.Random(_seed_for(seed, stratum.name, code.code)).shuffle(queue)
        queues[code.code] = queue

    selected: list[CandidateCompany] = []
    selected_by_code: Counter[str] = Counter()
    code_order = [code.code for code in stratum.codes]

    while len(selected) < target_size:
        progressed = False
        for code in code_order:
            if len(selected) >= target_size:
                break
            if selected_by_code[code] >= stratum.per_code_cap or not queues[code]:
                continue
            selected.append(queues[code].pop())
            selected_by_code[code] += 1
            progressed = True
        if not progressed:
            break

    cap_relaxation: Counter[str] = Counter()
    while len(selected) < target_size:
        progressed = False
        for code in code_order:
            if len(selected) >= target_size:
                break
            if not queues[code]:
                continue
            selected.append(queues[code].pop())
            selected_by_code[code] += 1
            cap_relaxation[code] += 1
            progressed = True
        if not progressed:
            break

    return selected, dict(sorted(cap_relaxation.items()))


def select_companies(
    candidates: Iterable[CandidateCompany],
    *,
    config: EnrichmentConfig,
    target_size: int,
    seed: int,
) -> tuple[list[SelectedCompany], dict[str, int], dict[str, dict[str, int]]]:
    """Apply configured target allocation without mixing enrichment strata."""
    target_sizes = _configured_target_sizes(config, target_size)
    candidates_by_code = {candidate.company_id: candidate for candidate in candidates}
    candidates_by_stratum: dict[str, list[CandidateCompany]] = defaultdict(list)
    for candidate in candidates_by_code.values():
        stratum, _code = config.codes_by_value[candidate.nace_code]
        candidates_by_stratum[stratum.name].append(candidate)

    selected: list[SelectedCompany] = []
    cap_relaxations: dict[str, dict[str, int]] = {}
    next_rank = 1
    for stratum in config.strata:
        selected_candidates, cap_relaxation = select_stratum(
            candidates=candidates_by_stratum[stratum.name],
            stratum=stratum,
            target_size=target_sizes[stratum.name],
            seed=seed,
        )
        cap_relaxations[stratum.name] = cap_relaxation
        for candidate in selected_candidates:
            _configured_stratum, code = config.codes_by_value[candidate.nace_code]
            selected.append(
                SelectedCompany(
                    candidate=candidate,
                    stratum=stratum,
                    code=code,
                    sample_rank=next_rank,
                    selection_reason=(
                        f"{stratum.name}; ÖNACE {candidate.nace_code}; "
                        "seeded breadth-first round-robin selection"
                    ),
                )
            )
            next_rank += 1
    return selected, target_sizes, cap_relaxations


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _audit_path_for(output_csv: Path) -> Path:
    return output_csv.with_name(f"{output_csv.stem}_audit.json")


def build_audit(
    *,
    config: EnrichmentConfig,
    sample_id: str,
    seed: int,
    target_size: int,
    population: list[CandidateCompany],
    eligible: list[CandidateCompany],
    selected: list[SelectedCompany],
    target_sizes: dict[str, int],
    eligibility_exclusions: dict[str, int],
    cap_relaxations: dict[str, dict[str, int]],
    excluded_samples: tuple[str, ...],
    output_csv: Path,
) -> dict[str, object]:
    selected_by_stratum = Counter(item.stratum.name for item in selected)
    selected_by_code = Counter(item.candidate.nace_code for item in selected)
    selected_domains = [item.candidate.normalized_domain for item in selected if item.candidate.normalized_domain]
    shared_items = [item for item in selected if item.shared_domain]
    shared_groups_by_domain = {
        str(item.candidate.normalized_domain): {
            "domain": item.candidate.normalized_domain,
            "firm_count": item.candidate.domain_firm_count,
            "representation_level": item.candidate.representation_level,
        }
        for item in shared_items
        if item.candidate.normalized_domain
    }
    shared_groups = sorted(
        shared_groups_by_domain.values(),
        key=lambda item: (-(item["firm_count"] or 0), str(item["domain"])),
    )[:10]
    representatives = {
        stratum: [
            {
                "company_id": item.candidate.company_id,
                "company_name": item.candidate.company_name,
                "nace_code": item.candidate.nace_code,
                "domain": item.candidate.normalized_domain,
            }
            for item in selected
            if item.stratum.name == stratum
        ][:3]
        for stratum in STRATUM_ORDER
    }
    selected_count = len(selected)
    return {
        "sample_id": sample_id,
        "sample_unit": "company",
        "sampling_id": config.sampling_id,
        "sampling_version": config.sampling_version,
        "taxonomy_id": config.taxonomy_id,
        "taxonomy_version": config.taxonomy_version,
        "taxonomy_source": config.taxonomy_source,
        "config_hash": config.config_hash,
        "sampling_seed": seed,
        "target_size": target_size,
        "selected_company_count": selected_count,
        "target_shortfall": max(target_size - selected_count, 0),
        "excluded_samples": list(excluded_samples),
        "population_company_count": len(population),
        "eligible_company_count": len(eligible),
        "eligibility_exclusions": eligibility_exclusions,
        "firm_size": {"available": False, "reason": "No firm-size field exists in the imported firm source/schema."},
        "strata": {
            name: {
                "target_size": target_sizes[name],
                "selected_count": selected_by_stratum[name],
                "selected_share": (selected_by_stratum[name] / selected_count if selected_count else 0.0),
                "cap_relaxations": cap_relaxations.get(name, {}),
            }
            for name in STRATUM_ORDER
        },
        "onace": {
            "unique_codes_selected": len(selected_by_code),
            "selected_counts_by_code": dict(sorted(selected_by_code.items())),
        },
        "domains": {
            "unique_known_domains": len(set(selected_domains)),
            "missing_domain_count": selected_count - len(selected_domains),
            "shared_domain_company_count": len(shared_items),
            "largest_selected_shared_domain_groups": shared_groups,
        },
        "existing_pilot_overlap_selected": 0,
        "representative_companies": representatives,
        "output_csv": str(output_csv),
        "sampling_caveat": (
            "This is an ÖNACE-enriched calibration sample. Its later labels must not be "
            "interpreted as an estimate of Austrian AI prevalence."
        ),
    }


def persist_sample(
    connection: sqlite3.Connection,
    *,
    sample_id: str,
    sample_name: str,
    selected: list[SelectedCompany],
    config: EnrichmentConfig,
    seed: int,
    source_csv: Path,
    force_refresh: bool,
) -> None:
    """Persist exact firm membership and its discovery-domain projection."""
    ensure_sample_registry_schema(connection)
    existing = connection.execute(
        "SELECT created_at FROM sample_sets WHERE sample_id = ?", (sample_id,)
    ).fetchone()
    if existing and not force_refresh:
        raise FileExistsError(f"Sample already exists: {sample_id}. Use --force-refresh to rebuild it.")

    domain_ranks: dict[str, int] = {}
    for item in selected:
        domain = item.candidate.normalized_domain
        if domain and domain not in domain_ranks:
            domain_ranks[domain] = item.sample_rank

    description = (
        "ÖNACE-based, AI-enriched company calibration sample. Selection uses only the "
        "imported ÖNACE activity code and existing domain eligibility metadata; it is not "
        "an Austrian AI-prevalence sample."
    )
    created_at = existing["created_at"] if existing else utc_now_iso()
    with connection:
        connection.execute(
            """
            INSERT INTO sample_sets (
                sample_id, sample_name, sample_description, source_csv,
                sampling_strategy, sampling_seed, sample_size, sample_unit,
                config_id, config_version, config_hash, domain_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'company', ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                sample_name = excluded.sample_name,
                sample_description = excluded.sample_description,
                source_csv = excluded.source_csv,
                sampling_strategy = excluded.sampling_strategy,
                sampling_seed = excluded.sampling_seed,
                sample_size = excluded.sample_size,
                sample_unit = excluded.sample_unit,
                config_id = excluded.config_id,
                config_version = excluded.config_version,
                config_hash = excluded.config_hash,
                domain_count = excluded.domain_count
            """,
            (
                sample_id,
                sample_name,
                description,
                str(source_csv),
                "seeded_onace_stratified_round_robin",
                seed,
                len(selected),
                config.sampling_id,
                config.sampling_version,
                config.config_hash,
                len(domain_ranks),
                created_at,
            ),
        )
        connection.execute("DELETE FROM sample_companies WHERE sample_id = ?", (sample_id,))
        connection.execute("DELETE FROM sample_domains WHERE sample_id = ?", (sample_id,))
        connection.executemany(
            """
            INSERT INTO sample_companies (
                sample_id, company_id, sample_rank, nace_code, nace_description,
                enrichment_stratum, normalized_domain, sampling_seed, selection_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sample_id,
                    item.candidate.company_id,
                    item.sample_rank,
                    item.candidate.nace_code,
                    item.code.description,
                    item.stratum.name,
                    item.candidate.normalized_domain,
                    seed,
                    item.selection_reason,
                )
                for item in selected
            ],
        )
        connection.executemany(
            "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES (?, ?, ?)",
            [
                (sample_id, domain, rank)
                for domain, rank in sorted(domain_ranks.items(), key=lambda item: (item[1], item[0]))
            ],
        )


def csv_rows(sample_id: str, seed: int, selected: Iterable[SelectedCompany]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in selected:
        rows.append(
            {
                "sample_id": sample_id,
                "sample_rank": item.sample_rank,
                "company_id": item.candidate.company_id,
                "company_name": item.candidate.company_name or "",
                "nace_code": item.candidate.nace_code,
                "nace_description": item.code.description,
                "enrichment_stratum": item.stratum.name,
                "normalized_domain": item.candidate.normalized_domain or "",
                "domain_category": item.candidate.domain_category or "",
                "representation_level": item.candidate.representation_level or "",
                "shared_domain": str(item.shared_domain).lower(),
                "shared_domain_firm_count": item.candidate.domain_firm_count or "",
                "sampling_seed": seed,
                "selection_reason": item.selection_reason,
            }
        )
    return rows


CSV_FIELDNAMES = [
    "sample_id",
    "sample_rank",
    "company_id",
    "company_name",
    "nace_code",
    "nace_description",
    "enrichment_stratum",
    "normalized_domain",
    "domain_category",
    "representation_level",
    "shared_domain",
    "shared_domain_firm_count",
    "sampling_seed",
    "selection_reason",
]


def build_enrichment_sample(
    *,
    sample_id: str | None = None,
    target_size: int | None = None,
    seed: int | None = None,
    exclude_samples: Iterable[str] | None = None,
    include_existing_pilot: bool = False,
    output_csv: str | Path | None = None,
    force_refresh: bool = False,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Build and persist a deterministic company sample without running the web pipeline."""
    root = project_root or find_project_root()
    config = load_enrichment_config(root)
    effective_sample_id = sample_id or config.default_sample_id
    effective_target_size = target_size if target_size is not None else config.default_target_size
    if effective_target_size <= 0:
        raise ValueError("target_size must be a positive integer")
    effective_seed = seed if seed is not None else config.default_seed
    if effective_seed < 0:
        raise ValueError("seed must be non-negative")
    effective_exclude_samples = tuple(
        dict.fromkeys(exclude_samples if exclude_samples is not None else config.default_exclude_samples)
    )
    if include_existing_pilot:
        effective_exclude_samples = tuple(
            sample for sample in effective_exclude_samples if sample != "pilot_v0_2"
        )

    output_path = resolve_project_path(root, str(output_csv or config.default_output_csv))
    audit_path = _audit_path_for(output_path)
    if not force_refresh and (output_path.exists() or audit_path.exists()):
        existing_paths = ", ".join(str(path) for path in (output_path, audit_path) if path.exists())
        raise FileExistsError(f"Output already exists: {existing_paths}. Use --force-refresh to replace it.")

    db_path = init_database(root)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        excluded_company_ids = existing_sample_company_ids(connection, effective_exclude_samples)
        population = load_taxonomy_population(connection, config)
        eligible, eligibility_exclusions = eligible_candidates(
            population,
            config=config,
            excluded_company_ids=excluded_company_ids,
        )
        selected, target_sizes, cap_relaxations = select_companies(
            eligible,
            config=config,
            target_size=effective_target_size,
            seed=effective_seed,
        )
        persist_sample(
            connection,
            sample_id=effective_sample_id,
            sample_name=(config.default_sample_name if effective_sample_id == config.default_sample_id else effective_sample_id),
            selected=selected,
            config=config,
            seed=effective_seed,
            source_csv=output_path,
            force_refresh=force_refresh,
        )

    audit = build_audit(
        config=config,
        sample_id=effective_sample_id,
        seed=effective_seed,
        target_size=effective_target_size,
        population=population,
        eligible=eligible,
        selected=selected,
        target_sizes=target_sizes,
        eligibility_exclusions=eligibility_exclusions,
        cap_relaxations=cap_relaxations,
        excluded_samples=effective_exclude_samples,
        output_csv=output_path,
    )
    _atomic_write_csv(output_path, CSV_FIELDNAMES, csv_rows(effective_sample_id, effective_seed, selected))
    _atomic_write_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**audit, "audit_json": str(audit_path), "database_path": str(db_path)}


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reproducible ÖNACE-based company calibration sample.")
    parser.add_argument("--sample-id", help="Stable company-sample identifier.")
    parser.add_argument("--target-size", type=positive_int, help="Target number of companies.")
    parser.add_argument("--seed", type=nonnegative_int, help="Deterministic sampling seed.")
    parser.add_argument(
        "--exclude-sample",
        action="append",
        default=None,
        help="Existing sample ID to exclude; repeatable. Defaults to configured pilot_v0_2.",
    )
    parser.add_argument(
        "--include-existing-pilot",
        action="store_true",
        help="Debug-only override: do not exclude pilot_v0_2 membership.",
    )
    parser.add_argument("--output-csv", help="CSV output path, relative to the project root unless absolute.")
    parser.add_argument("--force-refresh", action="store_true", help="Replace an existing sample and audit artifacts.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_enrichment_sample(
        sample_id=args.sample_id,
        target_size=args.target_size,
        seed=args.seed,
        exclude_samples=args.exclude_sample,
        include_existing_pilot=args.include_existing_pilot,
        output_csv=args.output_csv,
        force_refresh=args.force_refresh,
    )
    print("ÖNACE Enrichment Sample")
    print(f"Sample ID: {summary['sample_id']}")
    print(f"Seed: {summary['sampling_seed']}")
    print(f"Companies selected: {summary['selected_company_count']} / {summary['target_size']}")
    for stratum in STRATUM_ORDER:
        values = summary["strata"][stratum]
        print(f"{stratum}: {values['selected_count']} / {values['target_size']}")
    print(f"Unique ÖNACE codes: {summary['onace']['unique_codes_selected']}")
    print(f"Unique domains: {summary['domains']['unique_known_domains']}")
    print(f"Shared-domain companies: {summary['domains']['shared_domain_company_count']}")
    print(f"CSV: {summary['output_csv']}")
    print(f"Audit: {summary['audit_json']}")


if __name__ == "__main__":
    main()
