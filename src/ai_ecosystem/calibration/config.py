"""Configuration for blind company calibration evidence packages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ai_ecosystem.db.init_db import find_project_root


@dataclass(frozen=True)
class CalibrationConfig:
    package_builder_id: str
    package_builder_version: str
    default_sample_id: str
    default_scoring_run_id: str | None
    default_max_package_tokens: int
    tokenizer_model: str
    blind_input_format: str
    analysis_metadata_format: str
    openai: dict[str, Any]
    config_hash: str


def load_calibration_config(project_root: Path | None = None) -> CalibrationConfig:
    """Load and validate the calibration package builder configuration."""
    root = project_root or find_project_root()
    path = root / "config" / "calibration.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing calibration configuration: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config/calibration.yaml must contain a mapping")

    required = ("package_builder_id", "package_builder_version", "default_sample_id")
    missing = [key for key in required if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValueError(f"config/calibration.yaml is missing required keys: {', '.join(missing)}")
    try:
        token_limit = int(raw.get("default_max_package_tokens", 50000))
    except (TypeError, ValueError) as error:
        raise ValueError("default_max_package_tokens must be a positive integer") from error
    if token_limit <= 0:
        raise ValueError("default_max_package_tokens must be a positive integer")
    openai_config = raw.get("openai", {})
    if not isinstance(openai_config, dict):
        raise ValueError("openai must be a mapping when configured")

    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return CalibrationConfig(
        package_builder_id=str(raw["package_builder_id"]),
        package_builder_version=str(raw["package_builder_version"]),
        default_sample_id=str(raw["default_sample_id"]),
        default_scoring_run_id=(
            str(raw["default_scoring_run_id"]) if raw.get("default_scoring_run_id") else None
        ),
        default_max_package_tokens=token_limit,
        tokenizer_model=str(raw.get("tokenizer_model", "gpt-5")),
        blind_input_format=str(raw.get("blind_input_format", "markdown_and_json")),
        analysis_metadata_format=str(raw.get("analysis_metadata_format", "json")),
        openai=dict(openai_config),
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def calibration_config_as_dict(config: CalibrationConfig) -> dict[str, Any]:
    return {
        "package_builder_id": config.package_builder_id,
        "package_builder_version": config.package_builder_version,
        "default_sample_id": config.default_sample_id,
        "default_scoring_run_id": config.default_scoring_run_id,
        "default_max_package_tokens": config.default_max_package_tokens,
        "tokenizer_model": config.tokenizer_model,
        "blind_input_format": config.blind_input_format,
        "analysis_metadata_format": config.analysis_metadata_format,
        "openai": config.openai,
        "config_hash": config.config_hash,
    }
