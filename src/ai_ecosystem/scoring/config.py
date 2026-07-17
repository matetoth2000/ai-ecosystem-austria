"""Configuration and knowledge-base loaders for future content scoring.

This module intentionally does not score pages. It only validates and loads
versioned knowledge bases and scoring-profile settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_project_path


REQUIRED_DIMENSIONS = ("firm_profile", "technology", "innovation", "ecosystem")


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    knowledge_base_id: str
    version: str
    dimension: str
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class ScoringProfileConfig:
    scoring_profile_id: str
    scoring_profile_version: str
    required_dimensions: tuple[str, ...]
    knowledge_base_directory: Path
    dimension_weights: dict[str, float]
    field_weights: dict[str, float]
    fusion_weights: dict[str, float]
    raw_config: dict[str, Any]


def _as_float_map(value: object, *, field_name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"scoring.{field_name} must be a mapping")
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            result[str(key)] = float(item)
        except (TypeError, ValueError) as error:
            raise ValueError(f"scoring.{field_name}.{key} must be numeric") from error
    return result


def load_scoring_profile(project_root: Path | None = None) -> ScoringProfileConfig:
    """Load and validate the scoring profile section from config/config.yaml."""
    root = project_root or find_project_root()
    config = load_config(root)
    scoring_config = dict(config.get("scoring", {}))

    profile_id = str(scoring_config.get("scoring_profile_id", "content_scoring_default"))
    profile_version = str(scoring_config.get("scoring_profile_version", "v1"))
    kb_directory_value = str(scoring_config.get("knowledge_base_directory", "config/knowledge_bases"))
    kb_directory = resolve_project_path(root, kb_directory_value)
    required_dimensions = tuple(
        str(dimension)
        for dimension in scoring_config.get("required_dimensions", REQUIRED_DIMENSIONS)
    )
    if not profile_id.strip():
        raise ValueError("scoring.scoring_profile_id must not be empty")
    if not profile_version.strip():
        raise ValueError("scoring.scoring_profile_version must not be empty")
    if not required_dimensions:
        raise ValueError("scoring.required_dimensions must not be empty")

    dimension_weights = _as_float_map(
        scoring_config.get("dimension_weights", {dimension: 1.0 for dimension in required_dimensions}),
        field_name="dimension_weights",
    )
    field_weights = _as_float_map(
        scoring_config.get(
            "field_weights",
            {
                "title": 2.0,
                "headings": 1.5,
                "markdown": 1.0,
                "plain_text": 1.0,
                "url": 0.5,
                "internal_anchor_text": 0.5,
                "external_anchor_text": 0.25,
            },
        ),
        field_name="field_weights",
    )
    fusion_weights = _as_float_map(
        scoring_config.get(
            "fusion_weights",
            {"query": 1.0, "keyword": 1.0, "boost": 0.5, "negative": -1.0},
        ),
        field_name="fusion_weights",
    )
    return ScoringProfileConfig(
        scoring_profile_id=profile_id,
        scoring_profile_version=profile_version,
        required_dimensions=required_dimensions,
        knowledge_base_directory=kb_directory,
        dimension_weights=dimension_weights,
        field_weights=field_weights,
        fusion_weights=fusion_weights,
        raw_config=scoring_config,
    )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Knowledge base YAML must be a mapping: {path}")
    return data


def load_knowledge_base(path: Path) -> KnowledgeBaseConfig:
    """Load a single knowledge-base YAML file."""
    data = load_yaml(path)
    version = str(data.get("version", "")).strip()
    dimensions = [key for key in data.keys() if key != "version"]
    if not version:
        raise ValueError(f"Knowledge base is missing version: {path}")
    if len(dimensions) != 1:
        raise ValueError(f"Knowledge base must contain exactly one dimension root: {path}")
    dimension = dimensions[0]
    dimension_data = data.get(dimension)
    if not isinstance(dimension_data, dict) or not dimension_data:
        raise ValueError(f"Knowledge base dimension must contain ontology nodes: {path}")
    return KnowledgeBaseConfig(
        knowledge_base_id=dimension,
        version=version,
        dimension=dimension,
        path=path,
        data=data,
    )


def load_knowledge_bases(
    project_root: Path | None = None,
    knowledge_base_directory: Path | None = None,
) -> dict[str, KnowledgeBaseConfig]:
    """Load all versioned knowledge-base YAML files from config/knowledge_bases."""
    root = project_root or find_project_root()
    directory = knowledge_base_directory or load_scoring_profile(root).knowledge_base_directory
    if not directory.is_absolute():
        directory = root / directory
    if not directory.is_dir():
        raise FileNotFoundError(f"Knowledge base directory not found: {directory}")

    loaded: dict[str, KnowledgeBaseConfig] = {}
    for path in sorted(directory.glob("*_v*.yaml")):
        knowledge_base = load_knowledge_base(path)
        if knowledge_base.dimension in loaded:
            raise ValueError(f"Duplicate knowledge-base dimension: {knowledge_base.dimension}")
        loaded[knowledge_base.dimension] = knowledge_base
    return loaded


def validate_required_dimensions(
    knowledge_bases: dict[str, KnowledgeBaseConfig],
    required_dimensions: tuple[str, ...] = REQUIRED_DIMENSIONS,
) -> None:
    """Raise if any required scoring dimension is unavailable."""
    missing = [dimension for dimension in required_dimensions if dimension not in knowledge_bases]
    if missing:
        raise ValueError(f"Missing required knowledge-base dimensions: {', '.join(missing)}")


def validate_scoring_foundation(project_root: Path | None = None) -> tuple[ScoringProfileConfig, dict[str, KnowledgeBaseConfig]]:
    """Load the scoring profile and all required knowledge bases."""
    root = project_root or find_project_root()
    profile = load_scoring_profile(root)
    knowledge_bases = load_knowledge_bases(root, profile.knowledge_base_directory)
    validate_required_dimensions(knowledge_bases, profile.required_dimensions)
    return profile, knowledge_bases
