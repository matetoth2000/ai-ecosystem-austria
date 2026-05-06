"""Import local ORBIS/Aurelia firm records into SQLite."""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ai_ecosystem.db.init_db import (
    find_project_root,
    init_database,
    load_config,
    resolve_database_path,
    resolve_project_path,
)


REQUIRED_COLUMNS = {
    "Mark": "mark",
    "Name des Unternehmens": "firm_name",
    "BvD ID Nummer": "bvd_id",
    "Postleitzahl": "postal_code",
    "Ort": "city",
    "ONACE 2008 Haupttätigkeit 4-stellig - Code": "nace_code",
    "Web Adresse": "raw_url",
    "E-Mail Adresse": "email",
}


DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


@dataclass(frozen=True)
class NormalizedUrl:
    normalized_url: str | None
    normalized_domain: str | None
    is_missing: bool = False
    is_invalid: bool = False


@dataclass(frozen=True)
class ImportSummary:
    rows_read: int
    firms_inserted: int
    firms_updated: int
    unique_valid_domains_found: int
    new_domains_inserted: int
    missing_urls: int
    invalid_urls: int
    rows_missing_bvd_id: int


def normalize_url(raw_url: str | None) -> NormalizedUrl:
    if raw_url is None or not raw_url.strip():
        return NormalizedUrl(None, None, is_missing=True)

    url = raw_url.strip()
    if re.search(r"\s", url):
        return NormalizedUrl(None, None, is_invalid=True)

    if "://" not in url:
        url = f"https://{url}"

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return NormalizedUrl(None, None, is_invalid=True)

    domain = parsed.hostname.lower() if parsed.hostname else ""
    normalized_domain = domain.removeprefix("www.")
    if not DOMAIN_RE.fullmatch(normalized_domain):
        return NormalizedUrl(None, None, is_invalid=True)

    netloc = domain
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    normalized_url = urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    if normalized_url.endswith("/") and parsed.path == "/" and not parsed.query and not parsed.fragment:
        normalized_url = normalized_url[:-1]

    return NormalizedUrl(normalized_url, normalized_domain)


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, "Could not decode input file")


def read_orbis_rows(path: Path) -> list[dict[str, str]]:
    text, _encoding = read_text_with_fallback(path)
    reader = csv.DictReader(io.StringIO(text), delimiter=";", quotechar='"')

    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(reader.fieldnames or []))
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"Missing required ORBIS/Aurelia columns: {missing}")

    return list(reader)


def validate_firms_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(firms);").fetchall()}
    required = {
        "bvd_id",
        "mark",
        "firm_name",
        "postal_code",
        "city",
        "nace_code",
        "raw_url",
        "normalized_url",
        "normalized_domain",
        "email",
        "raw_source_file",
        "imported_at",
    }
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(
            "The existing SQLite database does not match the Milestone 1B schema. "
            "Delete the local ignored SQLite file and rerun database initialization. "
            f"Missing columns: {', '.join(missing)}"
        )


def import_orbis_aurelia(
    input_override: str | Path | None = None,
    project_root: Path | None = None,
) -> ImportSummary:
    project_root = project_root or find_project_root()
    config = load_config(project_root)

    input_path_value = input_override or config.get("ingestion", {}).get("orbis_aurelia_input_path")
    if not input_path_value:
        raise ValueError("Missing ingestion.orbis_aurelia_input_path in config/config.yaml")

    input_path = resolve_project_path(project_root, str(input_path_value))
    if not input_path.is_file():
        raise FileNotFoundError(f"ORBIS/Aurelia input file not found: {input_path}")

    db_path = resolve_database_path(project_root, config)
    if not db_path.exists():
        init_database(project_root)

    rows = read_orbis_rows(input_path)
    imported_at = datetime.now(UTC).isoformat(timespec="seconds")
    if input_path.is_relative_to(project_root):
        raw_source_file = str(input_path.relative_to(project_root))
    else:
        raw_source_file = str(input_path)

    firms_inserted = 0
    firms_updated = 0
    missing_urls = 0
    invalid_urls = 0
    rows_missing_bvd_id = 0
    valid_domains: set[str] = set()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        validate_firms_schema(connection)

        for row in rows:
            mapped = {target: (row.get(source) or "").strip() for source, target in REQUIRED_COLUMNS.items()}
            bvd_id = mapped["bvd_id"]
            if not bvd_id:
                rows_missing_bvd_id += 1
                continue

            normalized = normalize_url(mapped["raw_url"])
            if normalized.is_missing:
                missing_urls += 1
            elif normalized.is_invalid:
                invalid_urls += 1
            elif normalized.normalized_domain:
                valid_domains.add(normalized.normalized_domain)

            exists = connection.execute(
                "SELECT 1 FROM firms WHERE bvd_id = ?",
                (bvd_id,),
            ).fetchone()

            connection.execute(
                """
                INSERT INTO firms (
                    bvd_id,
                    mark,
                    firm_name,
                    postal_code,
                    city,
                    nace_code,
                    raw_url,
                    normalized_url,
                    normalized_domain,
                    email,
                    raw_source_file,
                    imported_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bvd_id) DO UPDATE SET
                    mark = excluded.mark,
                    firm_name = excluded.firm_name,
                    postal_code = excluded.postal_code,
                    city = excluded.city,
                    nace_code = excluded.nace_code,
                    raw_url = excluded.raw_url,
                    normalized_url = excluded.normalized_url,
                    normalized_domain = excluded.normalized_domain,
                    email = excluded.email,
                    raw_source_file = excluded.raw_source_file,
                    imported_at = excluded.imported_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    bvd_id,
                    mapped["mark"],
                    mapped["firm_name"],
                    mapped["postal_code"],
                    mapped["city"],
                    mapped["nace_code"],
                    mapped["raw_url"] or None,
                    normalized.normalized_url,
                    normalized.normalized_domain,
                    mapped["email"] or None,
                    raw_source_file,
                    imported_at,
                ),
            )
            if exists:
                firms_updated += 1
            else:
                firms_inserted += 1

        new_domains_inserted = 0
        for domain in sorted(valid_domains):
            cursor = connection.execute(
                "INSERT OR IGNORE INTO domains (domain) VALUES (?)",
                (domain,),
            )
            new_domains_inserted += cursor.rowcount

    return ImportSummary(
        rows_read=len(rows),
        firms_inserted=firms_inserted,
        firms_updated=firms_updated,
        unique_valid_domains_found=len(valid_domains),
        new_domains_inserted=new_domains_inserted,
        missing_urls=missing_urls,
        invalid_urls=invalid_urls,
        rows_missing_bvd_id=rows_missing_bvd_id,
    )


def print_summary(summary: ImportSummary) -> None:
    print("ORBIS/Aurelia import complete")
    print(f"Rows read: {summary.rows_read}")
    print(f"Firms inserted: {summary.firms_inserted}")
    print(f"Firms updated or skipped: {summary.firms_updated + summary.rows_missing_bvd_id}")
    print(f"Unique valid domains found: {summary.unique_valid_domains_found}")
    print(f"New domains inserted: {summary.new_domains_inserted}")
    print(f"Missing URLs: {summary.missing_urls}")
    print(f"Invalid URLs: {summary.invalid_urls}")
    if summary.rows_missing_bvd_id:
        print(f"Rows missing BvD ID skipped: {summary.rows_missing_bvd_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local ORBIS/Aurelia records into SQLite.")
    parser.add_argument(
        "--input",
        help="Optional path to the ORBIS/Aurelia semicolon-separated .txt file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = import_orbis_aurelia(input_override=args.input)
    print_summary(summary)


if __name__ == "__main__":
    main()
