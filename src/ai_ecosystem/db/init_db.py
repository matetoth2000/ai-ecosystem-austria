"""Initialize the project SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml


def find_project_root() -> Path:
    """Return the repository root containing the project config file."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "config" / "config.yaml").is_file():
            return parent
    raise FileNotFoundError("Could not find project root containing config/config.yaml")


def load_config(project_root: Path) -> dict:
    config_path = project_root / "config" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def resolve_project_path(project_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_database_path(project_root: Path, config: dict | None = None) -> Path:
    config = config if config is not None else load_config(project_root)
    db_path_value = config.get("database", {}).get("path")
    if not db_path_value:
        raise ValueError("Missing database.path in config/config.yaml")
    return resolve_project_path(project_root, db_path_value)


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name});").fetchall()}


def ensure_acquisition_schema(connection: sqlite3.Connection) -> None:
    """Apply small acquisition schema upgrades for existing development DBs."""
    scraped_columns = table_columns(connection, "scraped_pages")
    if "id" in scraped_columns and "scraped_page_id" not in scraped_columns:
        connection.execute("ALTER TABLE scraped_pages RENAME COLUMN id TO scraped_page_id;")
        scraped_columns = table_columns(connection, "scraped_pages")

    scraped_page_migrations = {
        "final_url": "ALTER TABLE scraped_pages ADD COLUMN final_url TEXT",
        "content_length": "ALTER TABLE scraped_pages ADD COLUMN content_length INTEGER",
        "meta_description": "ALTER TABLE scraped_pages ADD COLUMN meta_description TEXT",
        "canonical_url": "ALTER TABLE scraped_pages ADD COLUMN canonical_url TEXT",
        "word_count_estimate": "ALTER TABLE scraped_pages ADD COLUMN word_count_estimate INTEGER",
        "redirect_count": "ALTER TABLE scraped_pages ADD COLUMN redirect_count INTEGER",
        "retained_at_discovery": "ALTER TABLE scraped_pages ADD COLUMN retained_at_discovery INTEGER",
        "acquisition_run_id": "ALTER TABLE scraped_pages ADD COLUMN acquisition_run_id TEXT",
        "discovered_page_id": "ALTER TABLE scraped_pages ADD COLUMN discovered_page_id INTEGER",
    }
    for column, statement in scraped_page_migrations.items():
        if column not in scraped_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS acquisition_runs (
            acquisition_run_id TEXT PRIMARY KEY,
            sample_id TEXT,
            fetch_scope TEXT,
            started_at TEXT,
            completed_at TEXT,
            pages_attempted INTEGER,
            pages_fetched INTEGER,
            pages_failed INTEGER,
            success_rate REAL,
            notes TEXT
        );
        """
    )
    acquisition_run_columns = table_columns(connection, "acquisition_runs")
    if "success_rate" not in acquisition_run_columns:
        connection.execute("ALTER TABLE acquisition_runs ADD COLUMN success_rate REAL")

    connection.execute("DROP INDEX IF EXISTS idx_scraped_pages_candidate_page_id;")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_pages_candidate_page_id
        ON scraped_pages(candidate_page_id);
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_scraped_pages_discovered_page_id;")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_pages_discovered_page_id
        ON scraped_pages(discovered_page_id);
        """
    )


def ensure_discovered_pages_schema(connection: sqlite3.Connection) -> None:
    """Create and backfill the first-class discovery inventory."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discovered_pages (
            discovered_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            discovery_method TEXT,
            page_type_guess TEXT,
            priority INTEGER,
            url_depth INTEGER,
            source_domain TEXT,
            discovered_from_url TEXT,
            retained_at_discovery INTEGER DEFAULT 0,
            discard_reason TEXT,
            discovery_run_id TEXT NOT NULL DEFAULT 'historical_backfill',
            discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (domain) REFERENCES domains(domain)
        );
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_discovered_pages_run_domain_url_method
        ON discovered_pages(discovery_run_id, domain, normalized_url, discovery_method);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovered_pages_domain_url
        ON discovered_pages(domain, normalized_url);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovered_pages_run_retained
        ON discovered_pages(discovery_run_id, retained_at_discovery);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovered_pages_domain_run
        ON discovered_pages(domain, discovery_run_id);
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO discovered_pages (
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            url_depth,
            source_domain,
            discovered_from_url,
            retained_at_discovery,
            discard_reason,
            discovery_run_id,
            discovered_at,
            updated_at
        )
        SELECT
            domain,
            url,
            normalized_url,
            discovery_method,
            page_type_guess,
            priority,
            url_depth,
            source_domain,
            discovered_from_url,
            1,
            NULL,
            COALESCE(discovery_run_id, 'historical_backfill'),
            COALESCE(last_discovered_at, created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        FROM candidate_pages
        WHERE normalized_url IS NOT NULL
        """
    )


def ensure_validation_schema(connection: sqlite3.Connection) -> None:
    """Create validation metadata tables for existing development DBs."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS retention_validation_runs (
            run_id TEXT PRIMARY KEY,
            sample_id TEXT,
            sample_type TEXT,
            model_name TEXT,
            prompt_version TEXT,
            pages_selected INTEGER,
            pages_completed INTEGER,
            pages_failed INTEGER,
            estimated_input_chars INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            error_message TEXT
        );
        """
    )
    run_migrations = {
        "sample_id": "ALTER TABLE retention_validation_runs ADD COLUMN sample_id TEXT",
        "input_tokens": "ALTER TABLE retention_validation_runs ADD COLUMN input_tokens INTEGER",
        "output_tokens": "ALTER TABLE retention_validation_runs ADD COLUMN output_tokens INTEGER",
        "total_tokens": "ALTER TABLE retention_validation_runs ADD COLUMN total_tokens INTEGER",
    }
    run_columns = table_columns(connection, "retention_validation_runs")
    for column, statement in run_migrations.items():
        if column not in run_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_runs_status_started
        ON retention_validation_runs(status, started_at);
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS retention_validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id TEXT,
            validation_run_id TEXT,
            scraped_page_id INTEGER,
            candidate_page_id INTEGER,
            domain TEXT,
            url TEXT,
            retained_at_discovery INTEGER,
            page_type_guess TEXT,
            prompt_version TEXT,
            model_name TEXT,
            input_chars INTEGER,
            output_json TEXT,
            firm_relevance_score REAL,
            firm_relevance_label TEXT,
            page_category_llm TEXT,
            keep_for_firm_profile TEXT,
            reason_short TEXT,
            evidence_snippet TEXT,
            language TEXT,
            ecosystem_relevance_score REAL,
            ecosystem_relevance_label TEXT,
            ai_ecosystem_role TEXT,
            ai_evidence_strength TEXT,
            ai_relevance_reason TEXT,
            run_status TEXT DEFAULT 'pending',
            error_message TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
            FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id)
        );
        """
    )
    result_migrations = {
        "sample_id": "ALTER TABLE retention_validation_results ADD COLUMN sample_id TEXT",
        "validation_run_id": "ALTER TABLE retention_validation_results ADD COLUMN validation_run_id TEXT",
        "input_tokens": "ALTER TABLE retention_validation_results ADD COLUMN input_tokens INTEGER",
        "output_tokens": "ALTER TABLE retention_validation_results ADD COLUMN output_tokens INTEGER",
        "total_tokens": "ALTER TABLE retention_validation_results ADD COLUMN total_tokens INTEGER",
        "ecosystem_relevance_score": (
            "ALTER TABLE retention_validation_results ADD COLUMN ecosystem_relevance_score REAL"
        ),
        "ecosystem_relevance_label": (
            "ALTER TABLE retention_validation_results ADD COLUMN ecosystem_relevance_label TEXT"
        ),
        "ai_ecosystem_role": "ALTER TABLE retention_validation_results ADD COLUMN ai_ecosystem_role TEXT",
        "ai_evidence_strength": "ALTER TABLE retention_validation_results ADD COLUMN ai_evidence_strength TEXT",
        "ai_relevance_reason": "ALTER TABLE retention_validation_results ADD COLUMN ai_relevance_reason TEXT",
    }
    result_columns = table_columns(connection, "retention_validation_results")
    for column, statement in result_migrations.items():
        if column not in result_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_results_scraped_page_id
        ON retention_validation_results(scraped_page_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_results_candidate_page_id
        ON retention_validation_results(candidate_page_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_results_domain
        ON retention_validation_results(domain);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_results_status_created
        ON retention_validation_results(run_status, created_at);
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_retention_validation_results_unique_success_key
        ON retention_validation_results(sample_id, scraped_page_id, prompt_version, model_name);
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS retention_validation_sample (
            sample_id TEXT NOT NULL,
            sample_version TEXT,
            sample_type TEXT NOT NULL,
            scraped_page_id INTEGER NOT NULL,
            discovered_page_id INTEGER NOT NULL,
            candidate_page_id INTEGER,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            retained_at_discovery INTEGER NOT NULL,
            page_type_guess TEXT,
            priority INTEGER,
            discovery_method TEXT,
            selection_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
            FOREIGN KEY (discovered_page_id) REFERENCES discovered_pages(discovered_page_id),
            FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id),
            UNIQUE(sample_id, scraped_page_id)
        );
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_retained
        ON retention_validation_sample(sample_id, retained_at_discovery);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_domain
        ON retention_validation_sample(sample_id, domain);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_page_type
        ON retention_validation_sample(sample_id, page_type_guess);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_discovered_page_id
        ON retention_validation_sample(discovered_page_id);
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS retention_validation_inputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id TEXT NOT NULL,
            scraped_page_id INTEGER NOT NULL,
            discovered_page_id INTEGER NOT NULL,
            domain TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            meta_description TEXT,
            page_type_guess TEXT,
            retained_at_discovery INTEGER NOT NULL,
            visible_text_length INTEGER,
            preview_char_count INTEGER,
            visible_text_preview TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
            FOREIGN KEY (discovered_page_id) REFERENCES discovered_pages(discovered_page_id),
            UNIQUE(sample_id, scraped_page_id)
        );
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_inputs_sample_id
        ON retention_validation_inputs(sample_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retention_validation_inputs_discovered_page_id
        ON retention_validation_inputs(discovered_page_id);
        """
    )


def ensure_extraction_schema(connection: sqlite3.Connection) -> None:
    """Create extraction-stage tables for existing development DBs."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS extracted_pages (
            extracted_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_page_id INTEGER NOT NULL,
            extraction_version TEXT NOT NULL,
            extraction_status TEXT DEFAULT 'pending',
            markdown_path TEXT,
            text_path TEXT,
            metadata_path TEXT,
            language TEXT,
            extracted_at TEXT,
            content_sha256 TEXT,
            extraction_run_id TEXT,
            extraction_started_at TEXT,
            extraction_completed_at TEXT,
            extraction_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
            FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(extraction_run_id),
            UNIQUE(scraped_page_id, extraction_version)
        );
        """
    )
    extracted_columns = table_columns(connection, "extracted_pages")
    extracted_migrations = {
        "metadata_path": "ALTER TABLE extracted_pages ADD COLUMN metadata_path TEXT",
        "language": "ALTER TABLE extracted_pages ADD COLUMN language TEXT",
        "extracted_at": "ALTER TABLE extracted_pages ADD COLUMN extracted_at TEXT",
        "content_sha256": "ALTER TABLE extracted_pages ADD COLUMN content_sha256 TEXT",
        "extraction_run_id": "ALTER TABLE extracted_pages ADD COLUMN extraction_run_id TEXT",
        "extraction_started_at": "ALTER TABLE extracted_pages ADD COLUMN extraction_started_at TEXT",
        "extraction_completed_at": "ALTER TABLE extracted_pages ADD COLUMN extraction_completed_at TEXT",
        "extraction_error": "ALTER TABLE extracted_pages ADD COLUMN extraction_error TEXT",
    }
    for column, statement in extracted_migrations.items():
        if column not in extracted_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_extracted_pages_unique_scraped_version
        ON extracted_pages(scraped_page_id, extraction_version);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_extracted_pages_scraped_page_id
        ON extracted_pages(scraped_page_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_extracted_pages_extraction_status
        ON extracted_pages(extraction_status);
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS extraction_runs (
            extraction_run_id TEXT PRIMARY KEY,
            sample_id TEXT,
            extraction_version TEXT,
            started_at TEXT,
            finished_at TEXT,
            selected_pages INTEGER,
            processed_pages INTEGER,
            successful_pages INTEGER,
            skipped_pages INTEGER,
            failed_pages INTEGER,
            configuration_hash TEXT
        );
        """
    )
    run_columns = table_columns(connection, "extraction_runs")
    run_migrations = {
        "sample_id": "ALTER TABLE extraction_runs ADD COLUMN sample_id TEXT",
        "extraction_version": "ALTER TABLE extraction_runs ADD COLUMN extraction_version TEXT",
        "started_at": "ALTER TABLE extraction_runs ADD COLUMN started_at TEXT",
        "finished_at": "ALTER TABLE extraction_runs ADD COLUMN finished_at TEXT",
        "selected_pages": "ALTER TABLE extraction_runs ADD COLUMN selected_pages INTEGER",
        "processed_pages": "ALTER TABLE extraction_runs ADD COLUMN processed_pages INTEGER",
        "successful_pages": "ALTER TABLE extraction_runs ADD COLUMN successful_pages INTEGER",
        "skipped_pages": "ALTER TABLE extraction_runs ADD COLUMN skipped_pages INTEGER",
        "failed_pages": "ALTER TABLE extraction_runs ADD COLUMN failed_pages INTEGER",
        "configuration_hash": "ALTER TABLE extraction_runs ADD COLUMN configuration_hash TEXT",
    }
    for column, statement in run_migrations.items():
        if column not in run_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS page_features (
            extracted_page_id INTEGER PRIMARY KEY,
            title TEXT,
            word_count INTEGER,
            heading_count INTEGER,
            headings TEXT,
            heading_hierarchy TEXT,
            paragraph_count INTEGER,
            page_type_guess TEXT,
            language TEXT,
            markdown_path TEXT,
            text_path TEXT,
            feature_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (extracted_page_id) REFERENCES extracted_pages(extracted_page_id)
        );
        """
    )
    feature_columns = table_columns(connection, "page_features")
    feature_migrations = {
        "title": "ALTER TABLE page_features ADD COLUMN title TEXT",
        "word_count": "ALTER TABLE page_features ADD COLUMN word_count INTEGER",
        "heading_count": "ALTER TABLE page_features ADD COLUMN heading_count INTEGER",
        "headings": "ALTER TABLE page_features ADD COLUMN headings TEXT",
        "heading_hierarchy": "ALTER TABLE page_features ADD COLUMN heading_hierarchy TEXT",
        "paragraph_count": "ALTER TABLE page_features ADD COLUMN paragraph_count INTEGER",
        "page_type_guess": "ALTER TABLE page_features ADD COLUMN page_type_guess TEXT",
        "language": "ALTER TABLE page_features ADD COLUMN language TEXT",
        "markdown_path": "ALTER TABLE page_features ADD COLUMN markdown_path TEXT",
        "text_path": "ALTER TABLE page_features ADD COLUMN text_path TEXT",
        "feature_version": "ALTER TABLE page_features ADD COLUMN feature_version TEXT",
    }
    for column, statement in feature_migrations.items():
        if column not in feature_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS page_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            extracted_page_id INTEGER NOT NULL,
            source_url TEXT,
            target_url TEXT,
            normalized_target_url TEXT,
            target_domain TEXT,
            normalized_target_domain TEXT,
            anchor_text TEXT,
            anchor_word_count INTEGER,
            rel_attribute TEXT,
            internal_external TEXT,
            is_internal INTEGER,
            link_order INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (extracted_page_id) REFERENCES extracted_pages(extracted_page_id)
        );
        """
    )
    link_columns = table_columns(connection, "page_links")
    link_migrations = {
        "source_url": "ALTER TABLE page_links ADD COLUMN source_url TEXT",
        "target_url": "ALTER TABLE page_links ADD COLUMN target_url TEXT",
        "normalized_target_url": "ALTER TABLE page_links ADD COLUMN normalized_target_url TEXT",
        "target_domain": "ALTER TABLE page_links ADD COLUMN target_domain TEXT",
        "normalized_target_domain": "ALTER TABLE page_links ADD COLUMN normalized_target_domain TEXT",
        "anchor_text": "ALTER TABLE page_links ADD COLUMN anchor_text TEXT",
        "anchor_word_count": "ALTER TABLE page_links ADD COLUMN anchor_word_count INTEGER",
        "rel_attribute": "ALTER TABLE page_links ADD COLUMN rel_attribute TEXT",
        "internal_external": "ALTER TABLE page_links ADD COLUMN internal_external TEXT",
        "is_internal": "ALTER TABLE page_links ADD COLUMN is_internal INTEGER",
        "link_order": "ALTER TABLE page_links ADD COLUMN link_order INTEGER",
    }
    for column, statement in link_migrations.items():
        if column not in link_columns:
            connection.execute(statement)

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_page_links_extracted_page_id
        ON page_links(extracted_page_id);
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_page_links_target_domain
        ON page_links(target_domain);
        """
    )


def init_database(project_root: Path | None = None) -> Path:
    project_root = project_root or find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(__file__).resolve().with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(schema_sql)
        ensure_discovered_pages_schema(connection)
        ensure_acquisition_schema(connection)
        ensure_validation_schema(connection)
        ensure_extraction_schema(connection)

    return db_path


def main() -> None:
    db_path = init_database()
    print(f"Initialized SQLite database at {db_path}")


if __name__ == "__main__":
    main()
