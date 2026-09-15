"""Versioned configuration for ÖNACE-based calibration enrichment samples."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ai_ecosystem.db.init_db import find_project_root


REQUIRED_STRATA = ("HIGH_POTENTIAL", "AI_ADJACENT", "CONVENTIONAL_CONTROL")
ONACE_CODE_PATTERN = re.compile(r"^[A-U][0-9]{3}$")


@dataclass(frozen=True)
class EnrichmentCode:
    code: str
    description: str
    rationale: str


@dataclass(frozen=True)
class EnrichmentStratum:
    name: str
    target_size: int
    per_code_cap: int
    rationale: str
    codes: tuple[EnrichmentCode, ...]


@dataclass(frozen=True)
class EnrichmentConfig:
    sampling_id: str
    sampling_version: str
    taxonomy_id: str
    taxonomy_version: str
    taxonomy_source: dict[str, str]
    default_sample_id: str
    default_sample_name: str
    default_target_size: int
    default_seed: int
    default_exclude_samples: tuple[str, ...]
    default_output_csv: str
    require_normalized_domain: bool
    exclude_marked_domains: bool
    strata: tuple[EnrichmentStratum, ...]
    config_hash: str

    @property
    def codes_by_value(self) -> dict[str, tuple[EnrichmentStratum, EnrichmentCode]]:
        return {
            code.code: (stratum, code)
            for stratum in self.strata
            for code in stratum.codes
        }


def _nonempty(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ValueError(f"config/calibration_enrichment.yaml is missing required key: {key}")
    return value


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def load_enrichment_config(project_root: Path | None = None) -> EnrichmentConfig:
    """Load and validate the reproducible ÖNACE enrichment taxonomy."""
    root = project_root or find_project_root()
    path = root / "config" / "calibration_enrichment.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing calibration enrichment configuration: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config/calibration_enrichment.yaml must contain a mapping")

    taxonomy_source = raw.get("taxonomy_source")
    if not isinstance(taxonomy_source, dict):
        raise ValueError("taxonomy_source must be a mapping")
    normalized_source = {
        key: _nonempty(taxonomy_source, key)
        for key in ("authority", "title", "url", "source_field")
    }

    default_exclude_samples = raw.get("default_exclude_samples", [])
    if not isinstance(default_exclude_samples, list) or not all(
        str(sample).strip() for sample in default_exclude_samples
    ):
        raise ValueError("default_exclude_samples must be a list of non-empty sample IDs")
    if len(set(default_exclude_samples)) != len(default_exclude_samples):
        raise ValueError("default_exclude_samples cannot contain duplicates")

    eligibility = raw.get("eligibility")
    if not isinstance(eligibility, dict):
        raise ValueError("eligibility must be a mapping")
    if eligibility.get("require_normalized_domain") is not True:
        raise ValueError("eligibility.require_normalized_domain must be true for this web-pipeline sample")

    raw_strata = raw.get("strata")
    if not isinstance(raw_strata, dict):
        raise ValueError("strata must be a mapping")
    missing_strata = [name for name in REQUIRED_STRATA if name not in raw_strata]
    unexpected_strata = sorted(set(raw_strata) - set(REQUIRED_STRATA))
    if missing_strata or unexpected_strata:
        details = []
        if missing_strata:
            details.append(f"missing: {', '.join(missing_strata)}")
        if unexpected_strata:
            details.append(f"unsupported: {', '.join(unexpected_strata)}")
        raise ValueError(f"strata must contain exactly {', '.join(REQUIRED_STRATA)} ({'; '.join(details)})")

    seen_codes: set[str] = set()
    strata: list[EnrichmentStratum] = []
    for name in REQUIRED_STRATA:
        section = raw_strata[name]
        if not isinstance(section, dict):
            raise ValueError(f"strata.{name} must be a mapping")
        codes_raw = section.get("codes")
        if not isinstance(codes_raw, list) or not codes_raw:
            raise ValueError(f"strata.{name}.codes must be a non-empty list")
        codes: list[EnrichmentCode] = []
        for item in codes_raw:
            if not isinstance(item, dict):
                raise ValueError(f"strata.{name}.codes entries must be mappings")
            code = _nonempty(item, "code")
            if not ONACE_CODE_PATTERN.fullmatch(code):
                raise ValueError(f"Invalid stored ÖNACE code: {code}")
            if code in seen_codes:
                raise ValueError(f"ÖNACE code configured more than once: {code}")
            seen_codes.add(code)
            codes.append(
                EnrichmentCode(
                    code=code,
                    description=_nonempty(item, "description"),
                    rationale=_nonempty(item, "rationale"),
                )
            )
        strata.append(
            EnrichmentStratum(
                name=name,
                target_size=_positive_int(section.get("target_size"), f"strata.{name}.target_size"),
                per_code_cap=_positive_int(section.get("per_code_cap"), f"strata.{name}.per_code_cap"),
                rationale=_nonempty(section, "rationale"),
                codes=tuple(codes),
            )
        )

    default_target_size = _positive_int(raw.get("default_target_size"), "default_target_size")
    configured_total = sum(stratum.target_size for stratum in strata)
    if configured_total != default_target_size:
        raise ValueError(
            "default_target_size must equal the sum of configured stratum targets "
            f"({configured_total})"
        )
    try:
        default_seed = int(raw.get("default_seed"))
    except (TypeError, ValueError) as error:
        raise ValueError("default_seed must be an integer") from error
    if default_seed < 0:
        raise ValueError("default_seed must be non-negative")

    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return EnrichmentConfig(
        sampling_id=_nonempty(raw, "sampling_id"),
        sampling_version=_nonempty(raw, "sampling_version"),
        taxonomy_id=_nonempty(raw, "taxonomy_id"),
        taxonomy_version=_nonempty(raw, "taxonomy_version"),
        taxonomy_source=normalized_source,
        default_sample_id=_nonempty(raw, "default_sample_id"),
        default_sample_name=_nonempty(raw, "default_sample_name"),
        default_target_size=default_target_size,
        default_seed=default_seed,
        default_exclude_samples=tuple(str(sample) for sample in default_exclude_samples),
        default_output_csv=_nonempty(raw, "default_output_csv"),
        require_normalized_domain=True,
        exclude_marked_domains=bool(eligibility.get("exclude_marked_domains", True)),
        strata=tuple(strata),
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
