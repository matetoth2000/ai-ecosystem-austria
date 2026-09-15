"""Configuration for the scorer-v2 page annotation sample."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ai_ecosystem.db.init_db import find_project_root


LABELS = ("KEEP", "EXCLUDE", "UNKNOWN")
POOLS = (
    "structural",
    "coverage_stratified",
    "deterministic_random",
    "previous_calibration_evidence",
    "scorer_v1_diagnostic",
)


@dataclass(frozen=True)
class CalibrationSource:
    sample_id: str
    calibration_run_id: str
    scoring_run_id: str


@dataclass(frozen=True)
class PageAllocation:
    maximum_available_pages: int | None
    selected_pages: int


@dataclass(frozen=True)
class AnnotationSampleConfig:
    sample_id: str
    sample_version: str
    sampling_seed: int
    output_directory: str
    calibration_sources: tuple[CalibrationSource, ...]
    company_targets: dict[str, int]
    source_targets: dict[str, dict[str, int]]
    split_targets: dict[str, dict[str, int]]
    target_pages: int
    minimum_pages: int
    maximum_pages: int
    absolute_company_cap: int
    allocations: tuple[PageAllocation, ...]
    pool_shares: dict[str, float]
    config_hash: str

    @property
    def target_companies(self) -> int:
        return sum(self.company_targets.values())

    def page_quota(self, available_pages: int) -> int:
        if available_pages <= 0:
            return 0
        for allocation in self.allocations:
            if allocation.maximum_available_pages is None or available_pages <= allocation.maximum_available_pages:
                return min(available_pages, allocation.selected_pages)
        raise AssertionError("Page allocation must end with an unbounded rule")


def _required_text(mapping: dict[str, Any], key: str, context: str) -> str:
    value = str(mapping.get(key, "")).strip()
    if not value:
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{context} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return parsed


def load_annotation_sample_config(
    project_root: Path | None = None,
    config_path: Path | None = None,
) -> AnnotationSampleConfig:
    root = project_root or find_project_root()
    path = config_path or root / "config" / "page_annotation_sample.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing page annotation sample configuration: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Page annotation sample configuration must be a mapping")

    sources_raw = raw.get("calibration_sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ValueError("calibration_sources must be a non-empty list")
    sources: list[CalibrationSource] = []
    for index, item in enumerate(sources_raw):
        if not isinstance(item, dict):
            raise ValueError(f"calibration_sources[{index}] must be a mapping")
        sources.append(
            CalibrationSource(
                sample_id=_required_text(item, "sample_id", f"calibration_sources[{index}]"),
                calibration_run_id=_required_text(item, "calibration_run_id", f"calibration_sources[{index}]"),
                scoring_run_id=_required_text(item, "scoring_run_id", f"calibration_sources[{index}]"),
            )
        )
    sample_ids = [source.sample_id for source in sources]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("calibration_sources sample IDs must be unique")

    company_raw = raw.get("company_selection")
    if not isinstance(company_raw, dict):
        raise ValueError("company_selection must be a mapping")
    targets_raw = company_raw.get("targets")
    if not isinstance(targets_raw, dict) or set(targets_raw) != set(LABELS):
        raise ValueError(f"company_selection.targets must contain exactly {', '.join(LABELS)}")
    company_targets = {
        label: _positive_int(targets_raw[label], f"company_selection.targets.{label}")
        for label in LABELS
    }
    source_targets_raw = company_raw.get("source_targets", {})
    if not isinstance(source_targets_raw, dict):
        raise ValueError("company_selection.source_targets must be a mapping")
    source_targets: dict[str, dict[str, int]] = {}
    for label, target_by_source in source_targets_raw.items():
        if label not in LABELS or not isinstance(target_by_source, dict):
            raise ValueError(f"Invalid source target label: {label}")
        unknown_sources = set(target_by_source) - set(sample_ids)
        if unknown_sources:
            raise ValueError(f"Unknown source target sample IDs: {', '.join(sorted(unknown_sources))}")
        parsed_targets = {
            source_id: _positive_int(value, f"company_selection.source_targets.{label}.{source_id}")
            for source_id, value in target_by_source.items()
        }
        if sum(parsed_targets.values()) != company_targets[label]:
            raise ValueError(f"Source targets for {label} must sum to {company_targets[label]}")
        source_targets[label] = parsed_targets

    splits_raw = raw.get("splits")
    if not isinstance(splits_raw, dict) or not splits_raw:
        raise ValueError("splits must be a non-empty mapping")
    split_targets: dict[str, dict[str, int]] = {}
    for split_name, target_by_label in splits_raw.items():
        if not isinstance(target_by_label, dict) or set(target_by_label) != set(LABELS):
            raise ValueError(f"splits.{split_name} must contain exactly {', '.join(LABELS)}")
        split_targets[str(split_name)] = {
            label: _positive_int(target_by_label[label], f"splits.{split_name}.{label}")
            for label in LABELS
        }
    for label in LABELS:
        split_total = sum(targets[label] for targets in split_targets.values())
        if split_total != company_targets[label]:
            raise ValueError(f"Split targets for {label} must sum to {company_targets[label]}")

    page_raw = raw.get("page_selection")
    if not isinstance(page_raw, dict):
        raise ValueError("page_selection must be a mapping")
    minimum_pages = _positive_int(page_raw.get("minimum_pages"), "page_selection.minimum_pages")
    target_pages = _positive_int(page_raw.get("target_pages"), "page_selection.target_pages")
    maximum_pages = _positive_int(page_raw.get("maximum_pages"), "page_selection.maximum_pages")
    if not minimum_pages <= target_pages <= maximum_pages:
        raise ValueError("Page targets must satisfy minimum_pages <= target_pages <= maximum_pages")
    absolute_company_cap = _positive_int(
        page_raw.get("absolute_company_cap"), "page_selection.absolute_company_cap"
    )

    allocation_raw = page_raw.get("allocation")
    if not isinstance(allocation_raw, list) or not allocation_raw:
        raise ValueError("page_selection.allocation must be a non-empty list")
    allocations: list[PageAllocation] = []
    previous_maximum = 0
    for index, item in enumerate(allocation_raw):
        if not isinstance(item, dict):
            raise ValueError(f"page_selection.allocation[{index}] must be a mapping")
        maximum = item.get("maximum_available_pages")
        if maximum is not None:
            maximum = _positive_int(maximum, f"page_selection.allocation[{index}].maximum_available_pages")
            if maximum <= previous_maximum:
                raise ValueError("Page allocation maximums must be strictly increasing")
            previous_maximum = maximum
        elif index != len(allocation_raw) - 1:
            raise ValueError("Only the last page allocation may be unbounded")
        selected = _positive_int(item.get("selected_pages"), f"page_selection.allocation[{index}].selected_pages")
        if selected > absolute_company_cap:
            raise ValueError("Page allocation exceeds absolute_company_cap")
        allocations.append(PageAllocation(maximum, selected))
    if allocations[-1].maximum_available_pages is not None:
        raise ValueError("Page allocation must end with an unbounded rule")

    shares_raw = page_raw.get("pool_shares")
    if not isinstance(shares_raw, dict) or set(shares_raw) != set(POOLS):
        raise ValueError(f"page_selection.pool_shares must contain exactly {', '.join(POOLS)}")
    pool_shares: dict[str, float] = {}
    for pool in POOLS:
        try:
            share = float(shares_raw[pool])
        except (TypeError, ValueError) as error:
            raise ValueError(f"page_selection.pool_shares.{pool} must be numeric") from error
        if not 0.0 <= share <= 1.0:
            raise ValueError(f"page_selection.pool_shares.{pool} must be between 0 and 1")
        pool_shares[pool] = share
    if abs(sum(pool_shares.values()) - 1.0) > 1e-9:
        raise ValueError("Page selection pool shares must sum to 1.0")
    if pool_shares["scorer_v1_diagnostic"] > 0.15:
        raise ValueError("scorer_v1_diagnostic share must not exceed 0.15")
    if pool_shares["previous_calibration_evidence"] + pool_shares["scorer_v1_diagnostic"] > 0.30:
        raise ValueError("At least 70% of pages must be independent of scorer v1 and prior LLM citations")

    try:
        seed = int(raw.get("sampling_seed"))
    except (TypeError, ValueError) as error:
        raise ValueError("sampling_seed must be an integer") from error
    if seed < 0:
        raise ValueError("sampling_seed must be non-negative")

    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return AnnotationSampleConfig(
        sample_id=_required_text(raw, "sample_id", "root"),
        sample_version=_required_text(raw, "sample_version", "root"),
        sampling_seed=seed,
        output_directory=_required_text(raw, "output_directory", "root"),
        calibration_sources=tuple(sources),
        company_targets=company_targets,
        source_targets=source_targets,
        split_targets=split_targets,
        target_pages=target_pages,
        minimum_pages=minimum_pages,
        maximum_pages=maximum_pages,
        absolute_company_cap=absolute_company_cap,
        allocations=tuple(allocations),
        pool_shares=pool_shares,
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
