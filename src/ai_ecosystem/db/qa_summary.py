"""Print basic QA summaries for the local SQLite database."""

from __future__ import annotations

import sqlite3

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


def print_scalar(connection: sqlite3.Connection, label: str, query: str) -> None:
    value = connection.execute(query).fetchone()[0]
    print(f"{label}: {value}")


def print_rows(connection: sqlite3.Connection, title: str, query: str) -> None:
    print()
    print(title)
    rows = connection.execute(query).fetchall()
    if not rows:
        print("  none")
        return
    for row in rows:
        print("  " + " | ".join("" if value is None else str(value) for value in row))


def main() -> None:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)

    with sqlite3.connect(db_path) as connection:
        print(f"Database: {db_path}")
        print_scalar(connection, "Total firms", "SELECT COUNT(*) FROM firms")
        print_scalar(connection, "Total domains", "SELECT COUNT(*) FROM domains")
        print_scalar(
            connection,
            "Firms with URLs",
            "SELECT COUNT(*) FROM firms WHERE normalized_domain IS NOT NULL",
        )
        print_scalar(
            connection,
            "Firms without URLs",
            "SELECT COUNT(*) FROM firms WHERE normalized_domain IS NULL",
        )

        print_rows(
            connection,
            "Duplicate BvD IDs",
            """
            SELECT bvd_id, COUNT(*)
            FROM firms
            GROUP BY bvd_id
            HAVING COUNT(*) > 1
            """,
        )
        print_rows(
            connection,
            "Domains shared by many firms",
            """
            SELECT normalized_domain, COUNT(*) AS n
            FROM firms
            WHERE normalized_domain IS NOT NULL
            GROUP BY normalized_domain
            ORDER BY n DESC
            LIMIT 30
            """,
        )
        print_rows(
            connection,
            "Sample invalid/missing URLs",
            """
            SELECT firm_name, raw_url
            FROM firms
            WHERE normalized_domain IS NULL
            LIMIT 50
            """,
        )


if __name__ == "__main__":
    main()
