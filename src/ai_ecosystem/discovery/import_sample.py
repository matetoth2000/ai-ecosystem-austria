"""Import reproducible domain samples into the SQLite sample registry."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path, resolve_project_path
from ai_ecosystem.discovery.discovery_utils import utc_now_iso


def ensure_sample_registry_schema(connection: sqlite3.Connection) -> None:
    """Create sample registry tables for existing development databases."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sample_sets (
            sample_id TEXT PRIMARY KEY,
            sample_name TEXT NOT NULL,
            sample_description TEXT,
            source_csv TEXT,
            sampling_strategy TEXT,
            sampling_seed INTEGER,
            sample_size INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sample_domains (
            sample_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            sample_rank INTEGER,
            FOREIGN KEY (sample_id) REFERENCES sample_sets(sample_id),
            UNIQUE(sample_id, domain)
        )
        """
    )


def read_sample_csv(path: Path) -> list[tuple[str, int | None]]:
    """Read unique domains and optional ranks from a sample CSV."""
    rows: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if "domain" not in (reader.fieldnames or []):
            raise ValueError(f"CSV input must contain a 'domain' column: {path}")
        for index, row in enumerate(reader, start=1):
            domain = (row.get("domain") or "").strip().lower()
            if not domain or domain in seen:
                continue
            seen.add(domain)
            raw_rank = (row.get("sample_rank") or "").strip()
            try:
                sample_rank = int(raw_rank) if raw_rank else index
            except ValueError:
                sample_rank = index
            rows.append((domain, sample_rank))
    return rows


def import_sample(
    db_path: Path,
    sample_id: str,
    sample_name: str,
    csv_path: Path,
    sample_description: str | None = None,
    sampling_strategy: str | None = None,
    sampling_seed: int | None = None,
) -> dict:
    """Upsert one sample set and replace its domain membership from CSV."""
    domain_rows = read_sample_csv(csv_path)
    created_at = utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_sample_registry_schema(connection)
        existing = connection.execute(
            "SELECT created_at FROM sample_sets WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO sample_sets (
                sample_id,
                sample_name,
                sample_description,
                source_csv,
                sampling_strategy,
                sampling_seed,
                sample_size,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                sample_name = excluded.sample_name,
                sample_description = excluded.sample_description,
                source_csv = excluded.source_csv,
                sampling_strategy = excluded.sampling_strategy,
                sampling_seed = excluded.sampling_seed,
                sample_size = excluded.sample_size,
                created_at = sample_sets.created_at
            """,
            (
                sample_id,
                sample_name,
                sample_description,
                str(csv_path),
                sampling_strategy,
                sampling_seed,
                len(domain_rows),
                existing[0] if existing else created_at,
            ),
        )
        connection.execute("DELETE FROM sample_domains WHERE sample_id = ?", (sample_id,))
        connection.executemany(
            """
            INSERT INTO sample_domains (
                sample_id,
                domain,
                sample_rank
            )
            VALUES (?, ?, ?)
            ON CONFLICT(sample_id, domain) DO UPDATE SET
                sample_rank = excluded.sample_rank
            """,
            [(sample_id, domain, sample_rank) for domain, sample_rank in domain_rows],
        )
        connection.commit()
    return {
        "sample_id": sample_id,
        "sample_name": sample_name,
        "sample_size": len(domain_rows),
        "source_csv": str(csv_path),
    }


def list_samples(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        ensure_sample_registry_schema(connection)
        return connection.execute(
            """
            SELECT sample_id, sample_name, sample_size, created_at
            FROM sample_sets
            ORDER BY created_at, sample_id
            """
        ).fetchall()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import or list registered domain samples.")
    parser.add_argument("--list", action="store_true", help="List sample sets in the registry.")
    parser.add_argument("--sample-id", help="Stable sample identifier.")
    parser.add_argument("--sample-name", help="Human-readable sample name.")
    parser.add_argument("--sample-description", help="Optional sample description.")
    parser.add_argument("--sampling-strategy", help="Sampling strategy label.")
    parser.add_argument("--sampling-seed", type=int, help="Sampling seed, if applicable.")
    parser.add_argument("--csv", help="Sample CSV with a domain column.")
    return parser.parse_args()


def main() -> None:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    args = parse_args()

    if args.list:
        rows = list_samples(db_path)
        print("sample_id\tsample_name\tsample_size\tcreated_at")
        for row in rows:
            print(f"{row['sample_id']}\t{row['sample_name']}\t{row['sample_size']}\t{row['created_at']}")
        return

    missing = [
        name
        for name, value in {
            "--sample-id": args.sample_id,
            "--sample-name": args.sample_name,
            "--csv": args.csv,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required arguments for import: {', '.join(missing)}")

    csv_path = resolve_project_path(project_root, args.csv)
    summary = import_sample(
        db_path=db_path,
        sample_id=args.sample_id,
        sample_name=args.sample_name,
        csv_path=csv_path,
        sample_description=args.sample_description,
        sampling_strategy=args.sampling_strategy,
        sampling_seed=args.sampling_seed,
    )
    print(
        "Imported sample "
        f"{summary['sample_id']} ({summary['sample_name']}): "
        f"{summary['sample_size']} domains"
    )


if __name__ == "__main__":
    main()
