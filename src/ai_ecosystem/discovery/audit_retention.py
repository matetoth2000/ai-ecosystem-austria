"""Export and summarize URL retention audit records."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


def latest_audit_run_id(connection: sqlite3.Connection, sample_id: str) -> str:
    row = connection.execute(
        """
        SELECT run_id
        FROM url_retention_audit
        WHERE sample_id = ?
        GROUP BY run_id
        ORDER BY MAX(audit_id) DESC
        LIMIT 1
        """,
        (sample_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No retention audit records found for sample_id: {sample_id}")
    return row["run_id"]


def load_audit_rows(
    connection: sqlite3.Connection,
    sample_id: str,
    run_id: str | None = None,
) -> tuple[str, list[sqlite3.Row]]:
    selected_run_id = run_id or latest_audit_run_id(connection, sample_id)
    rows = connection.execute(
        """
        SELECT
            audit_id,
            run_id,
            sample_id,
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            decision,
            discard_reason,
            created_at
        FROM url_retention_audit
        WHERE sample_id = ?
          AND run_id = ?
        ORDER BY audit_id
        """,
        (sample_id, selected_run_id),
    ).fetchall()
    return selected_run_id, rows


def write_audit_csv(path: Path, rows: list[sqlite3.Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "audit_id",
        "run_id",
        "sample_id",
        "domain",
        "url",
        "normalized_url",
        "discovery_method",
        "page_type_guess",
        "priority",
        "decision",
        "discard_reason",
        "created_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fieldnames})


def summarize_audit(rows: list[sqlite3.Row]) -> dict:
    retained = sum(1 for row in rows if row["decision"] == "retained")
    discarded = sum(1 for row in rows if row["decision"] == "discarded")
    total = retained + discarded
    reason_counts = Counter(
        row["discard_reason"] or "none"
        for row in rows
        if row["decision"] == "discarded"
    )
    return {
        "retained_urls": retained,
        "discarded_urls": discarded,
        "retention_rate": round(retained / total * 100, 2) if total else 0,
        "discard_reason_distribution": dict(reason_counts.most_common()),
    }


def export_retention_audit(sample_id: str, run_id: str | None = None) -> tuple[Path, dict, str]:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        selected_run_id, rows = load_audit_rows(connection, sample_id, run_id)

    export_path = project_root / "data" / "interim" / f"discovery_retention_audit_{sample_id}.csv"
    write_audit_csv(export_path, rows)
    return export_path, summarize_audit(rows), selected_run_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export URL retention audit records.")
    parser.add_argument("--sample-id", required=True, help="Registered sample_id to export.")
    parser.add_argument("--run-id", help="Optional audit run_id. Defaults to latest audit run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_path, summary, run_id = export_retention_audit(args.sample_id, args.run_id)
    print("Retention audit summary")
    print(f"  Sample ID: {args.sample_id}")
    print(f"  Run ID: {run_id}")
    print(f"  Retained URLs: {summary['retained_urls']}")
    print(f"  Discarded URLs: {summary['discarded_urls']}")
    print(f"  Retention rate: {summary['retention_rate']}%")
    print("  Discard reasons:")
    for reason, count in summary["discard_reason_distribution"].items():
        print(f"    {reason}: {count}")
    print(f"  CSV: {export_path}")


if __name__ == "__main__":
    main()
