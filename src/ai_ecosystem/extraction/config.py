"""Configuration and path helpers for extraction workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_project_path


@dataclass(frozen=True)
class ExtractionConfig:
    extraction_version: str
    output_root: Path
    pages_directory: Path
    markdown_directory: Path
    text_directory: Path
    overwrite_existing: bool


def load_extraction_config(project_root: Path | None = None) -> ExtractionConfig:
    """Load extraction settings from config/config.yaml."""
    root = project_root or find_project_root()
    config = load_config(root)
    extraction_config = config.get("extraction", {})

    version = extraction_config.get("extraction_version", extraction_config.get("version", "v1"))
    output_root_value = extraction_config.get(
        "output_root",
        extraction_config.get("output_directory", "data/extracted"),
    )
    output_root = resolve_project_path(root, str(output_root_value))

    return ExtractionConfig(
        extraction_version=str(version),
        output_root=output_root,
        pages_directory=output_root / "pages",
        markdown_directory=output_root / str(extraction_config.get("markdown_directory", "markdown")),
        text_directory=output_root / str(extraction_config.get("text_directory", "text")),
        overwrite_existing=bool(extraction_config.get("overwrite_existing", False)),
    )


def ensure_extraction_directories(extraction_config: ExtractionConfig) -> None:
    """Create extraction output directories without creating extracted files."""
    extraction_config.pages_directory.mkdir(parents=True, exist_ok=True)
    extraction_config.markdown_directory.mkdir(parents=True, exist_ok=True)
    extraction_config.text_directory.mkdir(parents=True, exist_ok=True)


def safe_extraction_version(extraction_version: str) -> str:
    """Return a filesystem-safe extraction version label."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", extraction_version).strip("._") or "version"


def output_stem(scraped_page_id: int, extraction_version: str) -> str:
    """Return the deterministic basename for extracted artifacts."""
    if scraped_page_id < 1:
        raise ValueError("scraped_page_id must be a positive integer")
    return f"{safe_extraction_version(extraction_version)}_scraped_page_{scraped_page_id:012d}"


def markdown_output_path(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    """Return the deterministic Markdown output path for a scraped page."""
    return extraction_config.markdown_directory / f"{output_stem(scraped_page_id, extraction_config.extraction_version)}.md"


def text_output_path(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    """Return the deterministic plain-text output path for a scraped page."""
    return extraction_config.text_directory / f"{output_stem(scraped_page_id, extraction_config.extraction_version)}.txt"


def evidence_shard(scraped_page_id: int) -> str:
    """Return the deterministic evidence-package shard for a scraped page."""
    if scraped_page_id < 1:
        raise ValueError("scraped_page_id must be a positive integer")
    return f"{((scraped_page_id - 1) // 1000):06d}"


def evidence_package_dir(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    """Return the canonical evidence package directory for a scraped page."""
    if scraped_page_id < 1:
        raise ValueError("scraped_page_id must be a positive integer")
    return extraction_config.pages_directory / evidence_shard(scraped_page_id) / str(scraped_page_id)


def evidence_markdown_path(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    return evidence_package_dir(extraction_config, scraped_page_id) / "page.md"


def evidence_text_path(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    return evidence_package_dir(extraction_config, scraped_page_id) / "page.txt"


def evidence_metadata_path(extraction_config: ExtractionConfig, scraped_page_id: int) -> Path:
    return evidence_package_dir(extraction_config, scraped_page_id) / "metadata.json"
