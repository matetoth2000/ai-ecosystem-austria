"""Small utilities for future discovery runs and provenance metadata.

No HTTP requests or crawling logic belongs here yet.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4


def utc_now_iso() -> str:
    """Return a UTC timestamp suitable for discovery metadata."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_discovery_run_id(prefix: str = "discovery") -> str:
    """Return a unique run identifier for future discovery metadata."""
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"


def sample_exists(connection: sqlite3.Connection, sample_id: str) -> bool:
    """Return whether a sample set exists in the registry."""
    row = connection.execute(
        "SELECT 1 FROM sample_sets WHERE sample_id = ?",
        (sample_id,),
    ).fetchone()
    return row is not None


def get_sample_domains(connection: sqlite3.Connection, sample_id: str) -> list[str]:
    """Return registered domains for sample_id in sample-rank order."""
    rows = connection.execute(
        """
        SELECT domain
        FROM sample_domains
        WHERE sample_id = ?
        ORDER BY sample_rank IS NULL, sample_rank, domain
        """,
        (sample_id,),
    ).fetchall()
    return [row[0] for row in rows]


def get_sample_company_ids(connection: sqlite3.Connection, sample_id: str) -> list[str]:
    """Return explicit company membership for company-level samples, if present."""
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sample_companies'"
    ).fetchone()
    if not table_exists:
        return []
    rows = connection.execute(
        """
        SELECT company_id
        FROM sample_companies
        WHERE sample_id = ?
        ORDER BY sample_rank, company_id
        """,
        (sample_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]
