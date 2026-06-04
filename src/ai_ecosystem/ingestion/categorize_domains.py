"""Categorize imported domains by firm representation level."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ai_ecosystem.db.init_db import (
    find_project_root,
    init_database,
    load_config,
    resolve_database_path,
)


SOCIAL_MEDIA_DOMAINS = {
    "facebook.com",
    "de-de.facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
}

GENERIC_PLATFORM_DOMAINS = {
    "business.site",
    "sites.google.com",
    "google.com",
    "linktr.ee",
    "wixsite.com",
    "wordpress.com",
    "blogspot.com",
}

DOMAIN_METADATA_COLUMNS = {
    "firm_count": "INTEGER",
    "representation_level": "TEXT",
    "domain_category": "TEXT",
    "is_excluded": "INTEGER DEFAULT 0",
    "exclusion_reason": "TEXT",
    "primary_bvd_id": "TEXT",
    "primary_firm_name": "TEXT",
}


@dataclass(frozen=True)
class CategorizationSummary:
    total_domains: int
    single_firm_domains: int
    shared_domain_domains: int
    shared_group_or_brand_domains: int
    social_or_low_value_domains: int
    excluded_domains: int


def is_known_domain(domain: str, known_domains: set[str]) -> bool:
    return domain in known_domains or any(domain.endswith(f".{known}") for known in known_domains)


def categorize_domain(domain: str, firm_count: int) -> tuple[str, str, int, str | None]:
    if is_known_domain(domain, SOCIAL_MEDIA_DOMAINS):
        return "social_or_low_value", "social_media", 1, "social_media_profile_domain"
    if is_known_domain(domain, GENERIC_PLATFORM_DOMAINS):
        return "social_or_low_value", "generic_platform", 1, "generic_platform_domain"
    if firm_count == 1:
        return "single_firm", "company_or_group", 0, None
    if 2 <= firm_count <= 10:
        return "shared_domain", "company_or_group", 0, None
    if firm_count > 10:
        return "shared_group_or_brand", "company_or_group", 0, None
    return "unknown", "unknown", 0, None


def ensure_domain_metadata_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(domains);").fetchall()
    }
    for column_name, column_type in DOMAIN_METADATA_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE domains ADD COLUMN {column_name} {column_type}")


def categorize_domains(project_root: Path | None = None) -> CategorizationSummary:
    project_root = project_root or find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    if not db_path.exists():
        init_database(project_root)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_domain_metadata_columns(connection)

        firm_counts = dict(
            connection.execute(
                """
                SELECT normalized_domain, COUNT(*) AS firm_count
                FROM firms
                WHERE normalized_domain IS NOT NULL
                GROUP BY normalized_domain
                """
            ).fetchall()
        )

        domains = [
            row[0]
            for row in connection.execute("SELECT domain FROM domains ORDER BY domain").fetchall()
        ]

        for domain in domains:
            firm_count = int(firm_counts.get(domain, 0))
            representation_level, domain_category, is_excluded, exclusion_reason = categorize_domain(
                domain,
                firm_count,
            )
            connection.execute(
                """
                UPDATE domains
                SET
                    firm_count = ?,
                    representation_level = ?,
                    domain_category = ?,
                    is_excluded = ?,
                    exclusion_reason = ?,
                    primary_bvd_id = NULL,
                    primary_firm_name = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE domain = ?
                """,
                (
                    firm_count,
                    representation_level,
                    domain_category,
                    is_excluded,
                    exclusion_reason,
                    domain,
                ),
            )

        summary = CategorizationSummary(
            total_domains=connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0],
            single_firm_domains=connection.execute(
                "SELECT COUNT(*) FROM domains WHERE representation_level = 'single_firm'"
            ).fetchone()[0],
            shared_domain_domains=connection.execute(
                "SELECT COUNT(*) FROM domains WHERE representation_level = 'shared_domain'"
            ).fetchone()[0],
            shared_group_or_brand_domains=connection.execute(
                "SELECT COUNT(*) FROM domains WHERE representation_level = 'shared_group_or_brand'"
            ).fetchone()[0],
            social_or_low_value_domains=connection.execute(
                "SELECT COUNT(*) FROM domains WHERE representation_level = 'social_or_low_value'"
            ).fetchone()[0],
            excluded_domains=connection.execute(
                "SELECT COUNT(*) FROM domains WHERE is_excluded = 1"
            ).fetchone()[0],
        )

    return summary


def print_top_shared_domains(connection: sqlite3.Connection) -> None:
    print()
    print("Top 30 shared domains by firm_count")
    rows = connection.execute(
        """
        SELECT domain, firm_count, representation_level, domain_category, is_excluded
        FROM domains
        WHERE firm_count > 1
        ORDER BY firm_count DESC, domain
        LIMIT 30
        """
    ).fetchall()
    if not rows:
        print("  none")
        return
    for domain, firm_count, representation_level, domain_category, is_excluded in rows:
        print(f"  {domain} | {firm_count} | {representation_level} | {domain_category} | excluded={is_excluded}")


def print_summary(summary: CategorizationSummary, db_path: Path) -> None:
    print("Domain categorization complete")
    print(f"Database: {db_path}")
    print(f"Total domains: {summary.total_domains}")
    print(f"Single-firm domains: {summary.single_firm_domains}")
    print(f"Shared-domain domains: {summary.shared_domain_domains}")
    print(f"Shared group/brand domains: {summary.shared_group_or_brand_domains}")
    print(f"Social/low-value domains: {summary.social_or_low_value_domains}")
    print(f"Excluded domains: {summary.excluded_domains}")


def main() -> None:
    project_root = find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    summary = categorize_domains(project_root)
    print_summary(summary, db_path)
    with sqlite3.connect(db_path) as connection:
        print_top_shared_domains(connection)


if __name__ == "__main__":
    main()
