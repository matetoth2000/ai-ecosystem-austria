"""Create a reproducible homepage discovery pilot domain sample."""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path


DEFAULT_OUTPUT_PATH = "data/interim/discovery_pilot_domains_v0_1.csv"
DEFAULT_STRATIFIED_OUTPUT_PATH = "data/interim/discovery_pilot_domains_v0_2_stratified.csv"
DEFAULT_SAMPLING_SEED = 20260526
PILOT_REPRESENTATION_LEVELS = (
    "single_firm",
    "shared_domain",
    "shared_group_or_brand",
)


def get_pilot_mix(config: dict) -> dict[str, int]:
    mix = config.get("discovery", {}).get("pilot_mix", {})
    return {
        "single_firm": int(mix.get("single_firm", 90)),
        "shared_domain": int(mix.get("shared_domain", 8)),
        "shared_group_or_brand": int(mix.get("shared_group_or_brand", 2)),
    }


def select_domains_for_level(
    connection: sqlite3.Connection,
    representation_level: str,
    limit: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            d.domain,
            d.representation_level,
            d.domain_category,
            d.firm_count,
            d.is_excluded,
            d.exclusion_reason,
            f.bvd_id,
            f.firm_name,
            f.nace_code,
            f.city
        FROM domains d
        LEFT JOIN firms f
            ON f.normalized_domain = d.domain
        WHERE COALESCE(d.is_excluded, 0) = 0
          AND d.representation_level = ?
          AND d.domain IS NOT NULL
          AND d.domain GLOB '[A-Za-z0-9]*'
          AND (
              d.representation_level != 'single_firm'
              OR f.bvd_id = (
                  SELECT MIN(f2.bvd_id)
                  FROM firms f2
                  WHERE f2.normalized_domain = d.domain
              )
          )
        ORDER BY
            d.domain,
            f.bvd_id
        LIMIT ?
        """,
        (representation_level, limit),
    ).fetchall()


def create_pilot_sample(output_path: Path | None = None) -> Path:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    output_path = output_path or resolve_project_path(project_root, DEFAULT_OUTPUT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        selected_rows: list[sqlite3.Row] = []
        for representation_level, limit in get_pilot_mix(config).items():
            selected_rows.extend(select_domains_for_level(connection, representation_level, limit))

    fieldnames = [
        "sample_rank",
        "domain",
        "representation_level",
        "domain_category",
        "firm_count",
        "is_excluded",
        "exclusion_reason",
        "bvd_id",
        "firm_name",
        "nace_code",
        "city",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for sample_rank, row in enumerate(selected_rows, start=1):
            writer.writerow(
                {
                    "sample_rank": sample_rank,
                    "domain": row["domain"],
                    "representation_level": row["representation_level"],
                    "domain_category": row["domain_category"],
                    "firm_count": row["firm_count"],
                    "is_excluded": row["is_excluded"],
                    "exclusion_reason": row["exclusion_reason"],
                    "bvd_id": row["bvd_id"],
                    "firm_name": row["firm_name"],
                    "nace_code": row["nace_code"],
                    "city": row["city"],
                }
            )

    print(f"Wrote pilot sample to {output_path}")
    print(f"Domains selected: {len(selected_rows)}")
    for representation_level in PILOT_REPRESENTATION_LEVELS:
        count = sum(1 for row in selected_rows if row["representation_level"] == representation_level)
        print(f"{representation_level}: {count}")
    return output_path


def select_domain_candidates_for_level(
    connection: sqlite3.Connection,
    representation_level: str,
) -> list[dict]:
    if representation_level == "single_firm":
        rows = connection.execute(
            """
            SELECT
                d.domain,
                d.representation_level,
                d.domain_category,
                d.firm_count,
                d.is_excluded,
                d.exclusion_reason,
                f.bvd_id AS example_bvd_id,
                f.firm_name AS example_firm_name,
                f.nace_code AS nace_codes,
                f.city AS example_city
            FROM domains d
            JOIN firms f
                ON f.normalized_domain = d.domain
            WHERE COALESCE(d.is_excluded, 0) = 0
              AND d.representation_level = ?
              AND d.domain IS NOT NULL
              AND d.domain GLOB '[A-Za-z0-9]*'
            ORDER BY d.domain
            """,
            (representation_level,),
        ).fetchall()
        return [dict(row) for row in rows]

    rows = connection.execute(
        """
        SELECT
            d.domain,
            d.representation_level,
            d.domain_category,
            d.firm_count,
            d.is_excluded,
            d.exclusion_reason,
            MIN(f.bvd_id) AS example_bvd_id,
            MIN(f.firm_name) AS example_firm_name,
            GROUP_CONCAT(DISTINCT f.nace_code) AS nace_codes,
            MIN(f.city) AS example_city
        FROM domains d
        LEFT JOIN firms f
            ON f.normalized_domain = d.domain
        WHERE COALESCE(d.is_excluded, 0) = 0
          AND d.representation_level = ?
          AND d.domain IS NOT NULL
          AND d.domain GLOB '[A-Za-z0-9]*'
        GROUP BY
            d.domain,
            d.representation_level,
            d.domain_category,
            d.firm_count,
            d.is_excluded,
            d.exclusion_reason
        """,
        (representation_level,),
    ).fetchall()
    return [dict(row) for row in rows]


def sector_section_from_nace_codes(nace_codes: str | None) -> str:
    if not nace_codes:
        return "unknown"
    for nace_code in sorted(code.strip() for code in nace_codes.split(",") if code.strip()):
        if nace_code:
            return nace_code[0].upper()
    return "unknown"


def stratified_sample_by_sector(
    candidates: list[dict],
    target_count: int,
    rng: random.Random,
) -> list[dict]:
    candidates = [dict(candidate) for candidate in candidates]
    rng.shuffle(candidates)

    by_sector: dict[str, list[dict]] = {}
    for candidate in candidates:
        sector_section = sector_section_from_nace_codes(candidate.get("nace_codes"))
        candidate["sector_section"] = sector_section
        by_sector.setdefault(sector_section, []).append(candidate)

    sectors = list(by_sector)
    rng.shuffle(sectors)

    selected: list[dict] = []
    while len(selected) < target_count and sectors:
        next_sectors: list[str] = []
        for sector in sectors:
            sector_candidates = by_sector[sector]
            if sector_candidates and len(selected) < target_count:
                selected.append(sector_candidates.pop())
            if sector_candidates:
                next_sectors.append(sector)
        sectors = next_sectors

    return selected


def create_stratified_pilot_sample(
    output_path: Path | None = None,
    seed: int = DEFAULT_SAMPLING_SEED,
) -> Path:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    output_path = output_path or resolve_project_path(project_root, DEFAULT_STRATIFIED_OUTPUT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        selected_rows: list[dict] = []
        for representation_level, limit in get_pilot_mix(config).items():
            candidates = select_domain_candidates_for_level(connection, representation_level)
            selected_rows.extend(stratified_sample_by_sector(candidates, limit, rng))

    fieldnames = [
        "sample_rank",
        "sampling_seed",
        "domain",
        "representation_level",
        "domain_category",
        "firm_count",
        "is_excluded",
        "exclusion_reason",
        "sector_section",
        "nace_codes",
        "example_bvd_id",
        "example_firm_name",
        "example_city",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for sample_rank, row in enumerate(selected_rows, start=1):
            writer.writerow(
                {
                    "sample_rank": sample_rank,
                    "sampling_seed": seed,
                    "domain": row["domain"],
                    "representation_level": row["representation_level"],
                    "domain_category": row["domain_category"],
                    "firm_count": row["firm_count"],
                    "is_excluded": row["is_excluded"],
                    "exclusion_reason": row["exclusion_reason"],
                    "sector_section": row.get("sector_section") or sector_section_from_nace_codes(row.get("nace_codes")),
                    "nace_codes": row["nace_codes"],
                    "example_bvd_id": row["example_bvd_id"],
                    "example_firm_name": row["example_firm_name"],
                    "example_city": row["example_city"],
                }
            )

    print(f"Wrote stratified pilot sample to {output_path}")
    print(f"Sampling seed: {seed}")
    print(f"Domains selected: {len(selected_rows)}")
    for representation_level in PILOT_REPRESENTATION_LEVELS:
        count = sum(1 for row in selected_rows if row["representation_level"] == representation_level)
        print(f"{representation_level}: {count}")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create fixed homepage discovery pilot sample CSV.")
    parser.add_argument(
        "--strategy",
        choices=("alphabetical", "stratified"),
        default="alphabetical",
        help="Sampling strategy. The default preserves the v0.1 alphabetical/debug pilot.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SAMPLING_SEED,
        help="Fixed seed for the stratified random strategy.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path, relative to project root unless absolute.",
    )
    return parser.parse_args()


def main() -> None:
    project_root = find_project_root()
    args = parse_args()
    default_output = DEFAULT_STRATIFIED_OUTPUT_PATH if args.strategy == "stratified" else DEFAULT_OUTPUT_PATH
    output_path = resolve_project_path(project_root, args.output or default_output)
    if args.strategy == "stratified":
        create_stratified_pilot_sample(output_path, seed=args.seed)
    else:
        create_pilot_sample(output_path)


if __name__ == "__main__":
    main()
