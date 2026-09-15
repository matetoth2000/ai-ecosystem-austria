"""Run Trafilatura extraction and persist canonical evidence packages."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ai_ecosystem.discovery.discovery_utils import sample_exists
from ai_ecosystem.discovery.normalization import extract_domain, is_internal_url, normalize_url
from ai_ecosystem.db.init_db import (
    ensure_extraction_schema,
    find_project_root,
    init_database,
    load_config,
    resolve_database_path,
)
from ai_ecosystem.extraction.config import (
    ExtractionConfig,
    evidence_markdown_path,
    evidence_metadata_path,
    evidence_text_path,
    load_extraction_config,
)


@dataclass(frozen=True)
class ExtractionCandidate:
    scraped_page_id: int
    domain: str | None
    url: str
    html_path: str
    title: str | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str
    language: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ExtractionResult:
    candidate: ExtractionCandidate
    status: str
    language: str | None
    extracted_at: str | None
    markdown_path: str | None
    text_path: str | None
    metadata_path: str | None
    content_sha256: str | None
    word_count: int | None
    extraction_error: str | None
    extraction_time_seconds: float
    skipped: bool = False
    extracted_page_id: int | None = None
    extraction_run_id: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def generate_extraction_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"extraction_{timestamp}_{uuid.uuid4().hex[:8]}"


def read_gzip_html(project_root: Path, html_path: str) -> str:
    path = Path(html_path)
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        raise FileNotFoundError(f"missing HTML file: {path}")
    with gzip.open(path, "rb") as file:
        return file.read().decode("utf-8", errors="replace")


def _metadata_to_dict(metadata: object | None) -> dict[str, object]:
    if metadata is None:
        return {}
    result: dict[str, object] = {}
    for key in (
        "title",
        "author",
        "url",
        "hostname",
        "description",
        "sitename",
        "date",
        "categories",
        "tags",
        "license",
        "language",
        "id",
        "fingerprint",
    ):
        value = getattr(metadata, key, None)
        if value:
            result[key] = value
    return result


def extract_html_content(html: str) -> ExtractedDocument:
    """Extract main content with Trafilatura."""
    try:
        import trafilatura
    except ImportError as error:  # pragma: no cover - exercised in environments without dependency
        raise RuntimeError(
            "Missing dependency 'trafilatura'. Install project dependencies before running extraction."
        ) from error

    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=False,
        favor_recall=True,
    )
    if not markdown or not markdown.strip():
        raise ValueError("empty extraction")

    language = None
    metadata_dict: dict[str, object] = {}
    try:
        metadata = trafilatura.extract_metadata(html)
    except Exception:
        metadata = None
    if metadata is not None:
        language = getattr(metadata, "language", None) or getattr(metadata, "lang", None)
        metadata_dict = _metadata_to_dict(metadata)
    return ExtractedDocument(markdown=markdown, language=language, metadata=metadata_dict)


def markdown_to_plain_text(markdown: str) -> str:
    """Convert extracted Markdown to search-friendly plain text."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]{1,3}", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))


def content_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def configuration_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def relative_path(project_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root.resolve()))


def write_evidence_package(
    project_root: Path,
    extraction_config: ExtractionConfig,
    candidate: ExtractionCandidate,
    document: ExtractedDocument,
    extracted_at: str,
) -> tuple[str, str, str, str, int]:
    markdown = document.markdown.strip() + "\n"
    plain_text = markdown_to_plain_text(markdown)
    text_for_hash = plain_text.strip()
    sha256 = content_hash(text_for_hash)
    words = word_count(text_for_hash)

    markdown_path = evidence_markdown_path(extraction_config, candidate.scraped_page_id)
    text_path = evidence_text_path(extraction_config, candidate.scraped_page_id)
    metadata_path = evidence_metadata_path(extraction_config, candidate.scraped_page_id)
    metadata = {
        "scraped_page_id": candidate.scraped_page_id,
        "url": candidate.url,
        "domain": candidate.domain,
        "title": candidate.title,
        "extraction_version": extraction_config.extraction_version,
        "language": document.language,
        "extraction_timestamp": extracted_at,
        "markdown_filename": markdown_path.name,
        "text_filename": text_path.name,
        "markdown_char_count": len(markdown),
        "text_char_count": len(text_for_hash),
        "word_count": words,
        "content_sha256": sha256,
        "trafilatura_metadata": document.metadata or {},
    }

    atomic_write_text(markdown_path, markdown)
    atomic_write_text(text_path, text_for_hash + "\n")
    atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return (
        relative_path(project_root, markdown_path),
        relative_path(project_root, text_path),
        relative_path(project_root, metadata_path),
        sha256,
        words,
    )


def select_extraction_candidates(
    connection: sqlite3.Connection,
    limit: int | None = None,
    domain: str | None = None,
    sample_id: str | None = None,
    domain_sample_id: str | None = None,
) -> list[ExtractionCandidate]:
    if domain_sample_id and not sample_exists(connection, domain_sample_id):
        raise ValueError(f"Unknown domain sample_id: {domain_sample_id}")

    domain_filter = ""
    if domain:
        domain_filter = "AND s.domain = ?"
    sample_join = ""
    sample_filter = ""
    if sample_id:
        sample_join = "JOIN retention_validation_sample AS rvs ON rvs.scraped_page_id = s.scraped_page_id"
        sample_filter = "AND rvs.sample_id = ?"
    domain_sample_join = ""
    domain_sample_filter = ""
    if domain_sample_id:
        domain_sample_join = "JOIN sample_domains AS sd ON sd.domain = s.domain"
        domain_sample_filter = "AND sd.sample_id = ?"
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"

    # Keep bind values in the same order as the dynamically added predicates.
    parameters: list[object] = []
    if sample_id:
        parameters.append(sample_id)
    if domain_sample_id:
        parameters.append(domain_sample_id)
    if domain:
        parameters.append(domain)
    if limit is not None:
        parameters.append(limit)

    rows = connection.execute(
        f"""
        SELECT DISTINCT s.scraped_page_id, s.domain, s.url, s.html_path, s.title
        FROM scraped_pages AS s
        {sample_join}
        {domain_sample_join}
        WHERE s.scrape_status = 'success'
          AND s.html_path IS NOT NULL
          {sample_filter}
          {domain_sample_filter}
          {domain_filter}
        ORDER BY s.domain, s.url, s.scraped_page_id
        {limit_clause}
        """,
        parameters,
    ).fetchall()
    return [
        ExtractionCandidate(
            scraped_page_id=int(row["scraped_page_id"]),
            domain=row["domain"],
            url=row["url"],
            html_path=row["html_path"],
            title=row["title"],
        )
        for row in rows
    ]


def existing_successful_extraction(
    connection: sqlite3.Connection,
    scraped_page_id: int,
    extraction_version: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT e.extracted_page_id
        FROM extracted_pages AS e
        JOIN page_features AS pf
          ON pf.extracted_page_id = e.extracted_page_id
         AND pf.feature_version = e.extraction_version
        WHERE e.scraped_page_id = ?
          AND e.extraction_version = ?
          AND e.extraction_status = 'success'
          AND e.markdown_path IS NOT NULL
          AND e.text_path IS NOT NULL
          AND e.metadata_path IS NOT NULL
          AND e.content_sha256 IS NOT NULL
        """,
        (scraped_page_id, extraction_version),
    ).fetchone()


def get_discovery_page_type(connection: sqlite3.Connection, scraped_page_id: int) -> str | None:
    row = connection.execute(
        """
        SELECT
            COALESCE(dp.page_type_guess, cp.page_type_guess, rvs.page_type_guess) AS page_type_guess
        FROM scraped_pages AS s
        LEFT JOIN discovered_pages AS dp ON dp.discovered_page_id = s.discovered_page_id
        LEFT JOIN candidate_pages AS cp ON cp.id = s.candidate_page_id
        LEFT JOIN retention_validation_sample AS rvs ON rvs.scraped_page_id = s.scraped_page_id
        WHERE s.scraped_page_id = ?
        LIMIT 1
        """,
        (scraped_page_id,),
    ).fetchone()
    return row["page_type_guess"] if row else None


def extract_heading_hierarchy(markdown: str) -> tuple[list[str], list[dict[str, object]]]:
    headings: list[str] = []
    hierarchy: list[dict[str, object]] = []
    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        text = markdown_to_plain_text(match.group(2)).strip()
        if not text:
            continue
        headings.append(text)
        hierarchy.append({"level": len(match.group(1)), "text": text})
    return headings, hierarchy


def paragraph_count(text: str) -> int:
    return len([block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()])


def load_json_file(project_root: Path, relative_or_absolute_path: str | None) -> dict[str, object]:
    if not relative_or_absolute_path:
        return {}
    path = Path(relative_or_absolute_path)
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def populate_page_features(
    connection: sqlite3.Connection,
    project_root: Path,
    result: ExtractionResult,
    extraction_version: str,
) -> None:
    if result.extracted_page_id is None or not result.markdown_path or not result.text_path:
        return
    markdown_file = project_root / result.markdown_path
    text_file = project_root / result.text_path
    markdown = markdown_file.read_text(encoding="utf-8") if markdown_file.is_file() else ""
    text = text_file.read_text(encoding="utf-8") if text_file.is_file() else ""
    metadata = load_json_file(project_root, result.metadata_path)
    headings, hierarchy = extract_heading_hierarchy(markdown)
    title = metadata.get("title") or result.candidate.title
    language = result.language or metadata.get("language")
    words = result.word_count if result.word_count is not None else word_count(text)
    connection.execute(
        """
        INSERT INTO page_features (
            extracted_page_id,
            title,
            word_count,
            heading_count,
            headings,
            heading_hierarchy,
            paragraph_count,
            page_type_guess,
            language,
            markdown_path,
            text_path,
            feature_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(extracted_page_id) DO UPDATE SET
            title = excluded.title,
            word_count = excluded.word_count,
            heading_count = excluded.heading_count,
            headings = excluded.headings,
            heading_hierarchy = excluded.heading_hierarchy,
            paragraph_count = excluded.paragraph_count,
            page_type_guess = excluded.page_type_guess,
            language = excluded.language,
            markdown_path = excluded.markdown_path,
            text_path = excluded.text_path,
            feature_version = excluded.feature_version
        """,
        (
            result.extracted_page_id,
            title,
            words,
            len(headings),
            json.dumps(headings, ensure_ascii=False),
            json.dumps(hierarchy, ensure_ascii=False),
            paragraph_count(text),
            get_discovery_page_type(connection, result.candidate.scraped_page_id),
            language,
            result.markdown_path,
            result.text_path,
            extraction_version,
        ),
    )


def normalize_anchor_text(anchor_text: str) -> str:
    return re.sub(r"\s+", " ", anchor_text).strip()


def rel_attribute_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if str(item).strip()) or None
    return str(value).strip() or None


def extract_links_from_html(html: str, candidate: ExtractionCandidate) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, object]] = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href or not str(href).strip():
            continue
        href_text = str(href).strip()
        href_lower = href_text.lower()
        if href_lower.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        try:
            absolute_url = urljoin(candidate.url, href_text)
            normalized_target_url = normalize_url(absolute_url)
            target_domain = extract_domain(absolute_url)
            normalized_target_domain = extract_domain(normalized_target_url) if normalized_target_url else None
            is_internal = is_internal_url(absolute_url, candidate.domain or "") if candidate.domain else False
        except ValueError:
            # HTML often contains malformed telephone-like values in href attributes.
            # They are invalid link records, not extraction failures.
            continue
        if not normalized_target_url and not target_domain:
            continue
        anchor_text = normalize_anchor_text(anchor.get_text(" ", strip=True))
        links.append(
            {
                "target_url": absolute_url,
                "normalized_target_url": normalized_target_url,
                "target_domain": target_domain,
                "normalized_target_domain": normalized_target_domain,
                "anchor_text": anchor_text,
                "anchor_word_count": word_count(anchor_text),
                "rel_attribute": rel_attribute_value(anchor.get("rel")),
                "internal_external": "internal" if is_internal else "external",
                "is_internal": 1 if is_internal else 0,
            }
        )
    return links


def populate_page_links(
    connection: sqlite3.Connection,
    extracted_page_id: int,
    candidate: ExtractionCandidate,
    html: str,
) -> None:
    links = extract_links_from_html(html, candidate)
    connection.execute("DELETE FROM page_links WHERE extracted_page_id = ?", (extracted_page_id,))
    for index, link in enumerate(links, start=1):
        connection.execute(
            """
            INSERT INTO page_links (
                extracted_page_id,
                source_url,
                target_url,
                normalized_target_url,
                target_domain,
                normalized_target_domain,
                anchor_text,
                anchor_word_count,
                rel_attribute,
                internal_external,
                is_internal,
                link_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extracted_page_id,
                candidate.url,
                link["target_url"],
                link["normalized_target_url"],
                link["target_domain"],
                link["normalized_target_domain"],
                link["anchor_text"],
                link["anchor_word_count"],
                link["rel_attribute"],
                link["internal_external"],
                link["is_internal"],
                index,
            ),
        )


def insert_extraction_run(
    connection: sqlite3.Connection,
    *,
    extraction_run_id: str,
    sample_id: str | None,
    extraction_version: str,
    started_at: str,
    selected_pages: int,
    config_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO extraction_runs (
            extraction_run_id,
            sample_id,
            extraction_version,
            started_at,
            selected_pages,
            processed_pages,
            successful_pages,
            skipped_pages,
            failed_pages,
            configuration_hash
        )
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
        """,
        (extraction_run_id, sample_id, extraction_version, started_at, selected_pages, config_hash),
    )


def update_extraction_run(
    connection: sqlite3.Connection,
    *,
    extraction_run_id: str,
    finished_at: str,
    processed: int,
    successful: int,
    skipped: int,
    failed: int,
) -> None:
    connection.execute(
        """
        UPDATE extraction_runs
        SET finished_at = ?,
            processed_pages = ?,
            successful_pages = ?,
            skipped_pages = ?,
            failed_pages = ?
        WHERE extraction_run_id = ?
        """,
        (finished_at, processed, successful, skipped, failed, extraction_run_id),
    )


def upsert_extraction_result(
    connection: sqlite3.Connection,
    result: ExtractionResult,
    extraction_version: str,
) -> int | None:
    connection.execute(
        """
        INSERT INTO extracted_pages (
            scraped_page_id,
            extraction_version,
            extraction_status,
            markdown_path,
            text_path,
            metadata_path,
            language,
            extracted_at,
            content_sha256,
            extraction_run_id,
            extraction_started_at,
            extraction_completed_at,
            extraction_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scraped_page_id, extraction_version) DO UPDATE SET
            extraction_status = excluded.extraction_status,
            markdown_path = excluded.markdown_path,
            text_path = excluded.text_path,
            metadata_path = excluded.metadata_path,
            language = excluded.language,
            extracted_at = excluded.extracted_at,
            content_sha256 = excluded.content_sha256,
            extraction_run_id = excluded.extraction_run_id,
            extraction_started_at = excluded.extraction_started_at,
            extraction_completed_at = excluded.extraction_completed_at,
            extraction_error = excluded.extraction_error
        """,
        (
            result.candidate.scraped_page_id,
            extraction_version,
            result.status,
            result.markdown_path,
            result.text_path,
            result.metadata_path,
            result.language,
            result.extracted_at,
            result.content_sha256,
            result.extraction_run_id,
            result.extracted_at,
            utc_now_iso(),
            result.extraction_error,
        ),
    )
    row = connection.execute(
        """
        SELECT extracted_page_id
        FROM extracted_pages
        WHERE scraped_page_id = ? AND extraction_version = ?
        """,
        (result.candidate.scraped_page_id, extraction_version),
    ).fetchone()
    return int(row["extracted_page_id"]) if row else None


def process_candidate(
    project_root: Path,
    connection: sqlite3.Connection,
    candidate: ExtractionCandidate,
    extraction_config: ExtractionConfig,
    force_refresh: bool,
    extraction_run_id: str,
) -> ExtractionResult:
    if not force_refresh and existing_successful_extraction(
        connection,
        candidate.scraped_page_id,
        extraction_config.extraction_version,
    ):
        return ExtractionResult(
            candidate=candidate,
            status="success",
            language=None,
            extracted_at=None,
            markdown_path=None,
            text_path=None,
            metadata_path=None,
            content_sha256=None,
            word_count=None,
            extraction_error=None,
            extraction_time_seconds=0.0,
            skipped=True,
            extraction_run_id=extraction_run_id,
        )

    started = time.perf_counter()
    extracted_at = utc_now_iso()
    html_for_links: str | None = None
    try:
        html = read_gzip_html(project_root, candidate.html_path)
        html_for_links = html
        document = extract_html_content(html)
        markdown_path, text_path, metadata_path, sha256, words = write_evidence_package(
            project_root,
            extraction_config,
            candidate,
            document,
            extracted_at,
        )
        result = ExtractionResult(
            candidate=candidate,
            status="success",
            language=document.language,
            extracted_at=extracted_at,
            markdown_path=markdown_path,
            text_path=text_path,
            metadata_path=metadata_path,
            content_sha256=sha256,
            word_count=words,
            extraction_error=None,
            extraction_time_seconds=time.perf_counter() - started,
            extraction_run_id=extraction_run_id,
        )
    except FileNotFoundError as error:
        result = ExtractionResult(
            candidate=candidate,
            status="failed",
            language=None,
            extracted_at=extracted_at,
            markdown_path=None,
            text_path=None,
            metadata_path=None,
            content_sha256=None,
            word_count=None,
            extraction_error=f"missing_html: {error}",
            extraction_time_seconds=time.perf_counter() - started,
            extraction_run_id=extraction_run_id,
        )
    except (OSError, gzip.BadGzipFile, UnicodeDecodeError) as error:
        result = ExtractionResult(
            candidate=candidate,
            status="failed",
            language=None,
            extracted_at=extracted_at,
            markdown_path=None,
            text_path=None,
            metadata_path=None,
            content_sha256=None,
            word_count=None,
            extraction_error=f"unreadable_gzip: {error}",
            extraction_time_seconds=time.perf_counter() - started,
            extraction_run_id=extraction_run_id,
        )
    except ValueError as error:
        result = ExtractionResult(
            candidate=candidate,
            status="failed",
            language=None,
            extracted_at=extracted_at,
            markdown_path=None,
            text_path=None,
            metadata_path=None,
            content_sha256=None,
            word_count=None,
            extraction_error=f"empty_extraction: {error}",
            extraction_time_seconds=time.perf_counter() - started,
            extraction_run_id=extraction_run_id,
        )
    except Exception as error:
        result = ExtractionResult(
            candidate=candidate,
            status="failed",
            language=None,
            extracted_at=extracted_at,
            markdown_path=None,
            text_path=None,
            metadata_path=None,
            content_sha256=None,
            word_count=None,
            extraction_error=f"extraction_error: {error}",
            extraction_time_seconds=time.perf_counter() - started,
            extraction_run_id=extraction_run_id,
        )

    extracted_page_id = upsert_extraction_result(connection, result, extraction_config.extraction_version)
    if result.status == "success" and extracted_page_id is not None:
        result = ExtractionResult(
            candidate=result.candidate,
            status=result.status,
            language=result.language,
            extracted_at=result.extracted_at,
            markdown_path=result.markdown_path,
            text_path=result.text_path,
            metadata_path=result.metadata_path,
            content_sha256=result.content_sha256,
            word_count=result.word_count,
            extraction_error=result.extraction_error,
            extraction_time_seconds=result.extraction_time_seconds,
            skipped=result.skipped,
            extracted_page_id=extracted_page_id,
            extraction_run_id=result.extraction_run_id,
        )
        populate_page_features(connection, project_root, result, extraction_config.extraction_version)
        if html_for_links is not None:
            populate_page_links(connection, extracted_page_id, candidate, html_for_links)
    return result


def run_extraction(
    page_limit: int | None = None,
    force_refresh: bool = False,
    domain: str | None = None,
    sample_id: str | None = None,
    domain_sample_id: str | None = None,
    progress_every: int = 25,
    project_root: Path | None = None,
) -> dict[str, object]:
    root = project_root or find_project_root()
    db_path = init_database(root)
    config = load_config(root)
    db_path = resolve_database_path(root, config)
    extraction_config = load_extraction_config(root)

    started = time.perf_counter()
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_extraction_schema(connection)
        candidates = select_extraction_candidates(
            connection,
            page_limit,
            domain,
            sample_id,
            domain_sample_id,
        )
        extraction_run_id = generate_extraction_run_id()
        started_at = utc_now_iso()
        config_hash = configuration_hash(
            {
                "extraction_version": extraction_config.extraction_version,
                "sample_id": sample_id,
                "domain_sample_id": domain_sample_id,
                "page_limit": page_limit,
                "domain": domain,
                "force_refresh": force_refresh,
                "progress_every": progress_every,
            }
        )
        insert_extraction_run(
            connection,
            extraction_run_id=extraction_run_id,
            sample_id=sample_id,
            extraction_version=extraction_config.extraction_version,
            started_at=started_at,
            selected_pages=len(candidates),
            config_hash=config_hash,
        )
        connection.commit()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Extraction started")
        print(f"  Run ID: {extraction_run_id}")
        print(f"  Pages selected: {len(candidates)}")
        if sample_id:
            print(f"  Validation sample: {sample_id}")
        if domain_sample_id:
            print(f"  Domain sample: {domain_sample_id}")

        processed = 0
        skipped = 0
        successful = 0
        failed = 0
        extraction_times: list[float] = []
        for candidate in candidates:
            result = process_candidate(
                root,
                connection,
                candidate,
                extraction_config,
                force_refresh,
                extraction_run_id,
            )
            if result.skipped:
                skipped += 1
            else:
                processed += 1
                extraction_times.append(result.extraction_time_seconds)
                if result.status == "success":
                    successful += 1
                else:
                    failed += 1
            connection.commit()
            completed = processed + skipped
            if len(candidates) and (
                completed == len(candidates)
                or completed == 1
                or completed % max(progress_every, 1) == 0
            ):
                elapsed = time.perf_counter() - started
                rate = completed / elapsed if elapsed else 0.0
                remaining = len(candidates) - completed
                eta = format_duration(remaining / rate) if rate else "unknown"
                print(
                    "Progress: "
                    f"{completed}/{len(candidates)} "
                    f"Processed: {processed} "
                    f"Success: {successful} "
                    f"Failed: {failed} "
                    f"Skipped: {skipped} "
                    f"Elapsed: {format_duration(elapsed)} "
                    f"ETA: {eta} "
                    f"Rate: {rate:.2f} pages/sec"
                )
        update_extraction_run(
            connection,
            extraction_run_id=extraction_run_id,
            finished_at=utc_now_iso(),
            processed=processed,
            successful=successful,
            skipped=skipped,
            failed=failed,
        )
        connection.commit()

    total_runtime = time.perf_counter() - started
    average_extraction_time = sum(extraction_times) / len(extraction_times) if extraction_times else 0.0
    summary = {
        "extraction_run_id": extraction_run_id,
        "sample_id": sample_id,
        "domain_sample_id": domain_sample_id,
        "pages_selected": len(candidates),
        "processed": processed,
        "skipped": skipped,
        "successful": successful,
        "failed": failed,
        "average_extraction_time": average_extraction_time,
        "total_runtime": total_runtime,
        "extraction_version": extraction_config.extraction_version,
    }
    print_summary(summary)
    return summary


def print_summary(summary: dict[str, object]) -> None:
    print("Extraction summary")
    print(f"  Run ID: {summary['extraction_run_id']}")
    print(f"  Extraction version: {summary['extraction_version']}")
    if summary.get("sample_id"):
        print(f"  Validation sample: {summary['sample_id']}")
    if summary.get("domain_sample_id"):
        print(f"  Domain sample: {summary['domain_sample_id']}")
    print(f"  Pages selected: {summary['pages_selected']}")
    print(f"  Processed: {summary['processed']}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Successful: {summary['successful']}")
    print(f"  Failed: {summary['failed']}")
    print(f"  Average extraction time: {summary['average_extraction_time']:.3f}s")
    print(f"  Total runtime: {summary['total_runtime']:.3f}s")
    print(f"  Evidence packages written: {summary['successful']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Trafilatura extraction over acquired HTML pages.")
    parser.add_argument("--page-limit", type=int, default=None, help="Maximum number of acquired pages to select.")
    parser.add_argument("--force-refresh", action="store_true", help="Reprocess already successful extractions.")
    parser.add_argument("--domain", help="Restrict extraction to one source domain.")
    parser.add_argument(
        "--sample",
        dest="sample_id",
        help="Restrict extraction to a legacy page-level validation sample ID.",
    )
    parser.add_argument(
        "--sample-id",
        dest="domain_sample_id",
        help="Restrict extraction to a registered generic domain sample; combines with --sample and --domain.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print progress every N selected pages.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.page_limit is not None and args.page_limit < 1:
        raise SystemExit("--page-limit must be a positive integer")
    if args.progress_every < 1:
        raise SystemExit("--progress-every must be a positive integer")
    run_extraction(
        page_limit=args.page_limit,
        force_refresh=args.force_refresh,
        domain=args.domain,
        sample_id=args.sample_id,
        domain_sample_id=args.domain_sample_id,
        progress_every=args.progress_every,
    )


if __name__ == "__main__":
    main()
