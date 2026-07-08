"""Prepare extraction schema and output directories.

This scaffold intentionally performs no HTML extraction, Markdown generation,
plain-text generation, feature extraction, or link extraction.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root, init_database
from ai_ecosystem.extraction.config import (
    ensure_extraction_directories,
    load_extraction_config,
    markdown_output_path,
    text_output_path,
)


def prepare_extraction(project_root: Path | None = None) -> tuple[Path, Path, Path]:
    """Initialize extraction schema and configured output directories."""
    root = project_root or find_project_root()
    db_path = init_database(root)
    extraction_config = load_extraction_config(root)
    ensure_extraction_directories(extraction_config)
    return db_path, extraction_config.markdown_directory, extraction_config.text_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare extraction schema and directories without extracting HTML."
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print resolved extraction configuration and example deterministic paths.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    project_root = find_project_root()
    extraction_config = load_extraction_config(project_root)
    db_path, markdown_directory, text_directory = prepare_extraction(project_root)

    print("Extraction infrastructure ready.")
    print(f"Database: {db_path}")
    print(f"Extraction version: {extraction_config.extraction_version}")
    print(f"Markdown directory: {markdown_directory}")
    print(f"Text directory: {text_directory}")
    print("No HTML extraction was performed.")

    if args.show_config:
        print(f"Output root: {extraction_config.output_root}")
        print(f"Overwrite existing: {extraction_config.overwrite_existing}")
        print(f"Example Markdown path: {markdown_output_path(extraction_config, 1)}")
        print(f"Example text path: {text_output_path(extraction_config, 1)}")


if __name__ == "__main__":
    main()
