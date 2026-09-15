"""Build the deterministic, scorer-v2 page annotation sample.

The builder is artifact-only. It reads completed calibration labels, existing
v1 extractions, and pinned scorer-v1 runs, but does not invoke or mutate any of
those pipeline stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit

from ai_ecosystem.calibration.annotation_sample_config import (
    LABELS,
    AnnotationSampleConfig,
    CalibrationSource,
    load_annotation_sample_config,
)
from ai_ecosystem.db.init_db import (
    find_project_root,
    load_config,
    resolve_database_path,
)


ABOUT_COMPONENTS = frozenset(
    {"about", "about-us", "company", "unternehmen", "ueber-uns", "über-uns", "ueber", "who-we-are"}
)


@dataclass(frozen=True)
class CompanyCandidate:
    company_id: str
    company_name: str | None
    domain: str
    label: str
    calibration_sample_id: str
    calibration_run_id: str
    scoring_run_id: str
    nace_code: str | None
    enrichment_stratum: str | None
    language_group: str
    site_size_band: str
    raw_usable_pages: int
    unique_content_pages: int
    shared_domain: bool
    mapped_company_count: int
    attribution_uncertain: bool
    third_party_issue: bool
    cross_host_issue: bool
    calibration_result_id: int


@dataclass(frozen=True)
class PageCandidate:
    company_id: str
    extracted_page_id: int
    scraped_page_id: int
    domain: str
    url: str
    normalized_url: str
    title: str | None
    language: str | None
    language_group: str
    word_count: int
    page_type: str | None
    url_depth: int
    path_family: str
    structural_kind: str | None
    extraction_version: str
    markdown_path: str | None
    text_path: str | None
    content_sha256: str | None
    duplicate_group_size: int
    duplicate_scraped_page_ids: tuple[int, ...]
    previous_evidence_references: tuple[str, ...]
    scorer_v1_scores: dict[str, float]


@dataclass(frozen=True)
class SelectedPage:
    page: PageCandidate
    split: str
    sampling_pool: str
    selection_reason: str
    scorer_v1_diagnostic: bool
    scorer_v1_independent: bool
    independent_of_scorer_v1_and_previous_llm: bool
    company_page_rank: int


def _stable_digest(seed: int, *parts: Any) -> str:
    value = ":".join([str(seed), *(str(part) for part in parts)])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunks(values: Iterable[Any], size: int = 400) -> Iterator[tuple[Any, ...]]:
    chunk: list[Any] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield tuple(chunk)
            chunk = []
    if chunk:
        yield tuple(chunk)


def _placeholders(values: Iterable[Any]) -> str:
    values = tuple(values)
    if not values:
        raise ValueError("At least one SQL value is required")
    return ",".join("?" for _ in values)


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _base_language(value: str | None) -> str:
    normalized = str(value or "").strip().casefold().replace("_", "-")
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("en"):
        return "en"
    if not normalized or normalized in {"unknown", "zxx", "und", "none"}:
        return "unknown"
    return "other"


def _site_size_band(page_count: int) -> str:
    if page_count <= 5:
        return "1-5"
    if page_count <= 20:
        return "6-20"
    if page_count <= 100:
        return "21-100"
    return ">100"


def _url_key(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return str(value).strip().casefold().rstrip("/")
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{path}"


def _url_features(value: str) -> tuple[int, str, str | None]:
    try:
        path = urlsplit(value).path
    except ValueError:
        path = ""
    components = [unquote(part).casefold() for part in path.split("/") if part]
    depth = len(components)
    path_family = components[0] if components else "root"
    if depth == 0:
        structural = "root"
    elif any(component in ABOUT_COMPONENTS for component in components):
        structural = "about_company"
    elif depth == 1:
        structural = "shallow"
    else:
        structural = None
    return depth, path_family, structural


def _path_exists(project_root: Path, value: str | None) -> bool:
    if not value:
        return False
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.is_file()


def _validate_source_runs(connection: sqlite3.Connection, source: CalibrationSource) -> None:
    calibration = connection.execute(
        "SELECT sample_id, status FROM calibration_runs WHERE calibration_run_id = ?",
        (source.calibration_run_id,),
    ).fetchone()
    if calibration is None:
        raise ValueError(f"Calibration run does not exist: {source.calibration_run_id}")
    if calibration["status"] != "completed" or calibration["sample_id"] != source.sample_id:
        raise ValueError(f"Calibration run is not a completed run for {source.sample_id}: {source.calibration_run_id}")
    scoring = connection.execute(
        "SELECT status FROM scoring_runs WHERE scoring_run_id = ?",
        (source.scoring_run_id,),
    ).fetchone()
    if scoring is None or scoring["status"] != "completed":
        raise ValueError(f"Scoring run is not completed: {source.scoring_run_id}")


def load_usable_pages_by_domain(
    connection: sqlite3.Connection,
    project_root: Path,
    domains: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    """Load existing usable v1 pages without reading their page content."""
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for domain_chunk in _chunks(sorted(set(domains))):
        rows = connection.execute(
            f"""
            SELECT e.extracted_page_id, e.scraped_page_id, e.extraction_version,
                   e.content_sha256, e.markdown_path, e.text_path,
                   s.domain, s.url, COALESCE(s.final_url, s.url) AS normalized_url,
                   COALESCE(pf.title, s.title) AS title,
                   COALESCE(pf.language, e.language, s.language) AS language,
                   COALESCE(pf.word_count, s.word_count, s.word_count_estimate, 0) AS word_count,
                   pf.page_type_guess
            FROM extracted_pages e
            JOIN scraped_pages s ON s.scraped_page_id = e.scraped_page_id
            LEFT JOIN page_features pf ON pf.extracted_page_id = e.extracted_page_id
            WHERE s.domain IN ({_placeholders(domain_chunk)})
              AND e.extraction_status = 'success'
              AND e.extraction_version = 'v1'
            ORDER BY s.domain, e.scraped_page_id
            """,
            domain_chunk,
        ).fetchall()
        for row in rows:
            if not (_path_exists(project_root, row["text_path"]) or _path_exists(project_root, row["markdown_path"])):
                continue
            result[str(row["domain"])].append(dict(row))
    return result


def deduplicate_domain_pages(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose one structural-first representative per exact content hash."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("content_sha256") or f"scraped:{row['scraped_page_id']}")
        groups[key].append(row)
    representatives: list[dict[str, Any]] = []
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda row: (
                {"root": 0, "about_company": 1, "shallow": 2, None: 3}[
                    _url_features(str(row.get("normalized_url") or row.get("url") or ""))[2]
                ],
                _url_features(str(row.get("normalized_url") or row.get("url") or ""))[0],
                int(row["scraped_page_id"]),
            ),
        )
        representative = dict(ordered[0])
        representative["duplicate_group_size"] = len(ordered)
        representative["duplicate_scraped_page_ids"] = tuple(
            int(item["scraped_page_id"]) for item in ordered[1:]
        )
        representatives.append(representative)
    return sorted(representatives, key=lambda row: int(row["scraped_page_id"]))


def _extract_reference_items(result_row: sqlite3.Row) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    supporting = _json_object(result_row["supporting_evidence_json"])
    for item in supporting.get("evidence", []):
        if isinstance(item, dict):
            items.append({**item, "reference_source": "supporting_evidence"})
    lexical = _json_object(result_row["lexical_signals_json"])
    for item in lexical.get("signals", []):
        if isinstance(item, dict):
            items.append({**item, "reference_source": "lexical_signal"})
    return items


def resolve_previous_references(
    result_row: sqlite3.Row,
    pages: Iterable[dict[str, Any]],
) -> dict[int, tuple[str, ...]]:
    by_scraped = {str(page["scraped_page_id"]): page for page in pages}
    by_extracted = {str(page["extracted_page_id"]): page for page in pages}
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        by_url[_url_key(str(page.get("normalized_url") or page.get("url") or ""))].append(page)
    references: dict[int, set[str]] = defaultdict(set)
    for item in _extract_reference_items(result_row):
        page_id = str(item.get("scraped_page_id") or item.get("page_id") or "").strip()
        url_matches = by_url.get(_url_key(item.get("url")), []) if item.get("url") else []
        id_matches = [match for match in (by_scraped.get(page_id), by_extracted.get(page_id)) if match]
        selected: dict[str, Any] | None = None
        if url_matches:
            overlap = [page for page in id_matches if page in url_matches]
            selected = (overlap or url_matches)[0]
        elif id_matches:
            selected = id_matches[0]
        if selected is None:
            continue
        descriptor = str(
            item.get("evidence_type")
            or item.get("signal_type")
            or item.get("reference_source")
        )
        references[int(selected["scraped_page_id"])].add(descriptor)
    return {page_id: tuple(sorted(values)) for page_id, values in references.items()}


def load_company_candidates(
    connection: sqlite3.Connection,
    project_root: Path,
    config: AnnotationSampleConfig,
) -> tuple[list[CompanyCandidate], dict[str, list[dict[str, Any]]], dict[str, sqlite3.Row]]:
    label_rows: list[tuple[CalibrationSource, sqlite3.Row]] = []
    seen_company_ids: set[str] = set()
    for source in config.calibration_sources:
        _validate_source_runs(connection, source)
        rows = connection.execute(
            """
            SELECT cr.*, f.firm_name, f.nace_code, f.normalized_domain AS firm_domain,
                   sc.enrichment_stratum, d.firm_count, d.representation_level
            FROM calibration_results cr
            JOIN firms f ON f.bvd_id = cr.company_id
            LEFT JOIN sample_companies sc
              ON sc.sample_id = ? AND sc.company_id = cr.company_id
            LEFT JOIN domains d ON d.domain = COALESCE(cr.domain, f.normalized_domain)
            WHERE cr.calibration_run_id = ? AND cr.status = 'success'
            ORDER BY cr.company_id
            """,
            (source.sample_id, source.calibration_run_id),
        ).fetchall()
        for row in rows:
            company_id = str(row["company_id"])
            if company_id in seen_company_ids:
                raise ValueError(f"Company appears in multiple authoritative calibration runs: {company_id}")
            seen_company_ids.add(company_id)
            label_rows.append((source, row))

    domains = [str(row["domain"] or row["firm_domain"]) for _, row in label_rows if row["domain"] or row["firm_domain"]]
    pages_by_domain = load_usable_pages_by_domain(connection, project_root, domains)
    result_rows: dict[str, sqlite3.Row] = {}
    candidates: list[CompanyCandidate] = []
    for source, row in label_rows:
        domain = str(row["domain"] or row["firm_domain"] or "").strip().casefold()
        raw_pages = pages_by_domain.get(domain, [])
        unique_pages = deduplicate_domain_pages(raw_pages)
        if not domain or not unique_pages:
            continue
        language_counts = Counter(_base_language(page.get("language")) for page in unique_pages)
        language_group = sorted(language_counts, key=lambda value: (-language_counts[value], value))[0]
        attribution = _json_object(row["attribution_json"])
        reference_items = _extract_reference_items(row)
        cross_host = False
        for item in reference_items:
            try:
                host = urlsplit(str(item.get("url") or "")).hostname
            except ValueError:
                host = None
            if host and host.casefold().removeprefix("www.") != domain.removeprefix("www."):
                cross_host = True
                break
        mapped_count = int(row["firm_count"] or 1)
        shared = mapped_count > 1 or row["representation_level"] in {"shared_domain", "shared_group_or_brand"}
        candidates.append(
            CompanyCandidate(
                company_id=str(row["company_id"]),
                company_name=row["firm_name"],
                domain=domain,
                label=str(row["label"]),
                calibration_sample_id=source.sample_id,
                calibration_run_id=source.calibration_run_id,
                scoring_run_id=source.scoring_run_id,
                nace_code=row["nace_code"],
                enrichment_stratum=row["enrichment_stratum"],
                language_group=language_group,
                site_size_band=_site_size_band(len(unique_pages)),
                raw_usable_pages=len(raw_pages),
                unique_content_pages=len(unique_pages),
                shared_domain=shared,
                mapped_company_count=mapped_count,
                attribution_uncertain=(
                    attribution.get("domain_attribution_reliable") is False
                    or bool(attribution.get("shared_domain_issue"))
                ),
                third_party_issue=bool(attribution.get("third_party_content_issue")),
                cross_host_issue=cross_host,
                calibration_result_id=int(row["id"]),
            )
        )
        result_rows[str(row["company_id"])] = row
    return candidates, pages_by_domain, result_rows


def _diversity_values(company: CompanyCandidate) -> tuple[tuple[str, str], ...]:
    return (
        ("site_size", company.site_size_band),
        ("language", company.language_group),
        ("shared", str(company.shared_domain)),
        ("attribution", str(company.attribution_uncertain)),
        ("third_party", str(company.third_party_issue)),
        ("cross_host", str(company.cross_host_issue)),
        ("onace", company.enrichment_stratum or company.nace_code or "missing"),
        ("domain", company.domain),
    )


def diversity_select(candidates: Iterable[CompanyCandidate], count: int, seed: int, label: str, source: str) -> list[CompanyCandidate]:
    pool = list(candidates)
    if len(pool) < count:
        raise ValueError(f"Insufficient usable {label} companies in {source}: need {count}, found {len(pool)}")
    selected: list[CompanyCandidate] = []
    category_counts: Counter[tuple[str, str]] = Counter()
    while len(selected) < count:
        best = max(
            pool,
            key=lambda company: (
                sum(1.0 / (1 + category_counts[value]) for value in _diversity_values(company)),
                _stable_digest(seed, label, source, company.company_id),
            ),
        )
        selected.append(best)
        pool.remove(best)
        category_counts.update(_diversity_values(best))
    return selected


def select_companies(candidates: Iterable[CompanyCandidate], config: AnnotationSampleConfig) -> list[CompanyCandidate]:
    candidates = list(candidates)
    selected: list[CompanyCandidate] = []
    keep = sorted((company for company in candidates if company.label == "KEEP"), key=lambda item: item.company_id)
    if len(keep) != config.company_targets["KEEP"]:
        raise ValueError(
            f"Expected all {config.company_targets['KEEP']} usable KEEP companies, found {len(keep)}"
        )
    selected.extend(keep)
    for label in ("EXCLUDE", "UNKNOWN"):
        source_targets = config.source_targets.get(label)
        if source_targets:
            for source_id, target in source_targets.items():
                pool = [
                    company for company in candidates
                    if company.label == label and company.calibration_sample_id == source_id
                ]
                selected.extend(diversity_select(pool, target, config.sampling_seed, label, source_id))
        else:
            pool = [company for company in candidates if company.label == label]
            selected.extend(diversity_select(pool, config.company_targets[label], config.sampling_seed, label, "all"))
    if len(selected) != config.target_companies or len({company.company_id for company in selected}) != len(selected):
        raise AssertionError("Company selection did not produce the configured unique-company target")
    return sorted(selected, key=lambda item: item.company_id)


def assign_domain_grouped_splits(
    companies: Iterable[CompanyCandidate],
    config: AnnotationSampleConfig,
) -> dict[str, str]:
    groups: dict[str, list[CompanyCandidate]] = defaultdict(list)
    for company in companies:
        groups[company.domain or f"company:{company.company_id}"].append(company)
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (-len(item[1]), _stable_digest(config.sampling_seed, "split", item[0])),
    )
    split_names = tuple(config.split_targets)
    assigned_label: dict[str, Counter[str]] = {name: Counter() for name in split_names}
    assigned_total: Counter[str] = Counter()
    assignments: dict[str, str] = {}
    target_totals = {name: sum(config.split_targets[name].values()) for name in split_names}
    for domain, group in ordered_groups:
        group_labels = Counter(company.label for company in group)

        def cost(split: str) -> tuple[float, str]:
            overflow = max(0, assigned_total[split] + len(group) - target_totals[split])
            residual = ((assigned_total[split] + len(group) - target_totals[split]) ** 2) / target_totals[split]
            for label in LABELS:
                target = config.split_targets[split][label]
                new_value = assigned_label[split][label] + group_labels[label]
                overflow += max(0, new_value - target)
                residual += ((new_value - target) ** 2) / target
            return overflow * 10_000 + residual, _stable_digest(config.sampling_seed, domain, split)

        chosen = min(split_names, key=cost)
        assigned_total[chosen] += len(group)
        assigned_label[chosen].update(group_labels)
        for company in group:
            assignments[company.company_id] = chosen
    return assignments


def _load_scores(
    connection: sqlite3.Connection,
    scoring_run_id: str,
    extracted_page_ids: Iterable[int],
) -> dict[int, dict[str, float]]:
    scores: dict[int, dict[str, float]] = defaultdict(dict)
    for id_chunk in _chunks(sorted(set(extracted_page_ids))):
        rows = connection.execute(
            f"""
            SELECT extracted_page_id, dimension, final_score
            FROM page_scores
            WHERE scoring_run_id = ? AND extracted_page_id IN ({_placeholders(id_chunk)})
            ORDER BY extracted_page_id, dimension
            """,
            (scoring_run_id, *id_chunk),
        ).fetchall()
        for row in rows:
            scores[int(row["extracted_page_id"])][str(row["dimension"])] = float(row["final_score"] or 0.0)
    return scores


def build_page_candidates(
    connection: sqlite3.Connection,
    company: CompanyCandidate,
    domain_rows: Iterable[dict[str, Any]],
    result_row: sqlite3.Row,
) -> list[PageCandidate]:
    domain_rows = list(domain_rows)
    unique_rows = deduplicate_domain_pages(domain_rows)
    raw_references = resolve_previous_references(result_row, domain_rows)
    scores = _load_scores(
        connection,
        company.scoring_run_id,
        (int(row["extracted_page_id"]) for row in unique_rows),
    )
    pages: list[PageCandidate] = []
    for row in unique_rows:
        normalized_url = str(row.get("normalized_url") or row.get("url") or "")
        depth, path_family, structural = _url_features(normalized_url)
        reference_ids = (int(row["scraped_page_id"]), *row.get("duplicate_scraped_page_ids", ()))
        references = tuple(
            sorted(
                {
                    reference
                    for page_id in reference_ids
                    for reference in raw_references.get(page_id, ())
                }
            )
        )
        pages.append(
            PageCandidate(
                company_id=company.company_id,
                extracted_page_id=int(row["extracted_page_id"]),
                scraped_page_id=int(row["scraped_page_id"]),
                domain=company.domain,
                url=str(row.get("url") or normalized_url),
                normalized_url=normalized_url,
                title=row.get("title"),
                language=row.get("language"),
                language_group=_base_language(row.get("language")),
                word_count=int(row.get("word_count") or 0),
                page_type=row.get("page_type_guess"),
                url_depth=depth,
                path_family=path_family,
                structural_kind=structural,
                extraction_version=str(row["extraction_version"]),
                markdown_path=row.get("markdown_path"),
                text_path=row.get("text_path"),
                content_sha256=row.get("content_sha256"),
                duplicate_group_size=int(row.get("duplicate_group_size") or 1),
                duplicate_scraped_page_ids=tuple(row.get("duplicate_scraped_page_ids") or ()),
                previous_evidence_references=references,
                scorer_v1_scores=scores.get(int(row["extracted_page_id"]), {}),
            )
        )
    return pages


def _structural_order(page: PageCandidate) -> tuple[int, int, int]:
    priorities = {"root": 0, "about_company": 1, "shallow": 2, None: 3}
    return priorities[page.structural_kind], page.url_depth, page.scraped_page_id


def _coverage_order(pages: Iterable[PageCandidate], seed: int, company_id: str) -> list[PageCandidate]:
    groups: dict[tuple[str, str, str, str], list[PageCandidate]] = defaultdict(list)
    for page in pages:
        length = "short" if page.word_count < 100 else "medium" if page.word_count < 500 else "long"
        key = (page.language_group, length, page.page_type or "unknown", page.path_family)
        groups[key].append(page)
    for key in groups:
        groups[key].sort(key=lambda page: _stable_digest(seed, company_id, "coverage_page", page.scraped_page_id))
    keys = sorted(groups, key=lambda key: _stable_digest(seed, company_id, "coverage_group", *key))
    result: list[PageCandidate] = []
    while keys:
        next_keys: list[tuple[str, str, str, str]] = []
        for key in keys:
            result.append(groups[key].pop())
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    return result


def _diagnostic_order(company: CompanyCandidate, pages: Iterable[PageCandidate]) -> list[tuple[PageCandidate, str]]:
    pages = [page for page in pages if page.scorer_v1_scores]
    result: list[tuple[PageCandidate, str]] = []
    seen: set[int] = set()
    dimensions = ("firm_profile", "technology", "innovation", "ecosystem")
    rankings: list[tuple[str, list[PageCandidate]]] = []
    for dimension in dimensions:
        rankings.append(
            (
                f"scorer_v1_high_{dimension}",
                sorted(
                    pages,
                    key=lambda page: (
                        -page.scorer_v1_scores.get(dimension, float("-inf")),
                        page.scraped_page_id,
                    ),
                ),
            )
        )
    rankings.append(
        (
            "scorer_v1_dimension_disagreement",
            sorted(
                pages,
                key=lambda page: (
                    -(max(page.scorer_v1_scores.values()) - min(page.scorer_v1_scores.values())),
                    page.scraped_page_id,
                ),
            ),
        )
    )
    if company.label == "KEEP":
        rankings.append(
            (
                "keep_low_scorer_v1_evidence",
                sorted(pages, key=lambda page: (max(page.scorer_v1_scores.values()), page.scraped_page_id)),
            )
        )
    else:
        rankings.append(
            (
                "nonkeep_high_scorer_v1_evidence",
                sorted(pages, key=lambda page: (-max(page.scorer_v1_scores.values()), page.scraped_page_id)),
            )
        )
    for rank in range(max((len(ordered) for _, ordered in rankings), default=0)):
        for reason, ordered in rankings:
            if rank >= len(ordered):
                continue
            page = ordered[rank]
            if page.scraped_page_id in seen:
                continue
            seen.add(page.scraped_page_id)
            result.append((page, reason))
    return result


def select_pages_for_company(
    company: CompanyCandidate,
    pages: Iterable[PageCandidate],
    split: str,
    config: AnnotationSampleConfig,
) -> list[SelectedPage]:
    pages = list(pages)
    quota = config.page_quota(len(pages))
    if len(pages) <= quota:
        ordered = sorted(pages, key=_structural_order)
        return [
            SelectedPage(page, split, "all_available", "all_unique_pages_fit_company_quota", False, True, True, index)
            for index, page in enumerate(ordered, 1)
        ]

    pool_caps = {pool: math.floor(config.pool_shares[pool] * quota) for pool in config.pool_shares}
    selected: list[tuple[PageCandidate, str, str]] = []
    used: set[int] = set()

    def take(candidates: Iterable[tuple[PageCandidate, str]], pool: str, limit: int) -> None:
        for page, reason in candidates:
            if len([item for item in selected if item[1] == pool]) >= limit:
                break
            if page.scraped_page_id in used:
                continue
            selected.append((page, pool, reason))
            used.add(page.scraped_page_id)

    structural = [
        (page, f"structural_{page.structural_kind}")
        for page in sorted(pages, key=_structural_order)
        if page.structural_kind is not None
    ]
    previous = [
        (page, "previous_calibration_page_reference:" + ",".join(page.previous_evidence_references))
        for page in sorted(
            pages,
            key=lambda page: (-len(page.previous_evidence_references), *_structural_order(page)),
        )
        if page.previous_evidence_references
    ]
    diagnostic = _diagnostic_order(company, pages)
    coverage = [
        (page, "coverage_language_length_type_path")
        for page in _coverage_order(pages, config.sampling_seed, company.company_id)
    ]
    random_order = [
        (page, "seeded_uniform_page_sample")
        for page in sorted(
            pages,
            key=lambda page: _stable_digest(config.sampling_seed, company.company_id, "random", page.scraped_page_id),
        )
    ]

    take(structural, "structural", pool_caps["structural"])
    take(previous, "previous_calibration_evidence", pool_caps["previous_calibration_evidence"])
    take(diagnostic, "scorer_v1_diagnostic", pool_caps["scorer_v1_diagnostic"])
    take(coverage, "coverage_stratified", pool_caps["coverage_stratified"])
    take(random_order, "deterministic_random", pool_caps["deterministic_random"])

    # Rounding and sparse special-purpose pools are filled only by independent selection.
    if len(selected) < quota:
        take(coverage, "coverage_stratified", quota - len(selected) + len([item for item in selected if item[1] == "coverage_stratified"]))
    if len(selected) < quota:
        take(random_order, "deterministic_random", quota - len(selected) + len([item for item in selected if item[1] == "deterministic_random"]))
    if len(selected) != quota:
        raise AssertionError(f"Unable to allocate {quota} unique pages for {company.company_id}")

    result: list[SelectedPage] = []
    for rank, (page, pool, reason) in enumerate(selected, 1):
        diagnostic_flag = pool == "scorer_v1_diagnostic"
        pure_independent = pool not in {"scorer_v1_diagnostic", "previous_calibration_evidence"}
        result.append(
            SelectedPage(
                page=page,
                split=split,
                sampling_pool=pool,
                selection_reason=reason,
                scorer_v1_diagnostic=diagnostic_flag,
                scorer_v1_independent=not diagnostic_flag,
                independent_of_scorer_v1_and_previous_llm=pure_independent,
                company_page_rank=rank,
            )
        )
    return result


def extend_to_target(
    selected_by_company: dict[str, list[SelectedPage]],
    all_pages_by_company: dict[str, list[PageCandidate]],
    companies_by_id: dict[str, CompanyCandidate],
    splits: dict[str, str],
    config: AnnotationSampleConfig,
) -> None:
    selected_scraped_ids = {
        selected.page.scraped_page_id
        for pages in selected_by_company.values()
        for selected in pages
    }
    if len(selected_scraped_ids) >= config.target_pages:
        return
    company_order = sorted(
        companies_by_id,
        key=lambda company_id: (
            len(selected_by_company[company_id]),
            _stable_digest(config.sampling_seed, "extension", company_id),
        ),
    )
    progress = True
    while len(selected_scraped_ids) < config.target_pages and progress:
        progress = False
        for company_id in company_order:
            if len(selected_scraped_ids) >= config.target_pages:
                break
            selected = selected_by_company[company_id]
            if len(selected) >= config.absolute_company_cap:
                continue
            used = {item.page.scraped_page_id for item in selected}
            remaining = [page for page in all_pages_by_company[company_id] if page.scraped_page_id not in used]
            if not remaining:
                continue
            ordered_remaining = _coverage_order(remaining, config.sampling_seed, company_id)
            page = next(
                (candidate for candidate in ordered_remaining if candidate.scraped_page_id not in selected_scraped_ids),
                ordered_remaining[0],
            )
            selected.append(
                SelectedPage(
                    page=page,
                    split=splits[company_id],
                    sampling_pool="coverage_stratified",
                    selection_reason="independent_target_extension",
                    scorer_v1_diagnostic=False,
                    scorer_v1_independent=True,
                    independent_of_scorer_v1_and_previous_llm=True,
                    company_page_rank=len(selected) + 1,
                )
            )
            selected_scraped_ids.add(page.scraped_page_id)
            progress = True


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted((str(key), int(value)) for key, value in Counter(values).items()))


def build_audit(
    companies: list[CompanyCandidate],
    splits: dict[str, str],
    selected_pages: list[SelectedPage],
    config: AnnotationSampleConfig,
) -> dict[str, Any]:
    page_count = len(selected_pages)
    unique_scraped_page_count = len({page.page.scraped_page_id for page in selected_pages})
    diagnostics = sum(page.scorer_v1_diagnostic for page in selected_pages)
    scorer_independent = sum(page.scorer_v1_independent for page in selected_pages)
    prior_independent = sum(page.independent_of_scorer_v1_and_previous_llm for page in selected_pages)
    raw_pages = sum(company.raw_usable_pages for company in companies)
    unique_pages = sum(company.unique_content_pages for company in companies)
    split_label = Counter((splits[company.company_id], company.label) for company in companies)
    source_label = Counter((company.calibration_sample_id, company.label) for company in companies)
    selected_company_ids = {company.company_id for company in companies}
    selected_counts = Counter(page.page.company_id for page in selected_pages)
    allocation_shortfalls = [
        {
            "company_id": company.company_id,
            "expected_base_quota": config.page_quota(company.unique_content_pages),
            "selected_pages": selected_counts[company.company_id],
        }
        for company in companies
        if selected_counts[company.company_id] < config.page_quota(company.unique_content_pages)
    ]
    allocation_extensions = [
        {
            "company_id": company.company_id,
            "base_quota": config.page_quota(company.unique_content_pages),
            "selected_pages": selected_counts[company.company_id],
        }
        for company in companies
        if selected_counts[company.company_id] > config.page_quota(company.unique_content_pages)
    ]
    configured_split_label = {
        f"{split_name}:{label}": target
        for split_name, targets in config.split_targets.items()
        for label, target in targets.items()
    }
    actual_split_label = {
        f"{split_name}:{label}": split_label[(split_name, label)]
        for split_name in config.split_targets
        for label in LABELS
    }
    return {
        "sample_id": config.sample_id,
        "sample_version": config.sample_version,
        "sampling_seed": config.sampling_seed,
        "config_hash": config.config_hash,
        "companies": {
            "total": len(companies),
            "by_label": _counter_dict(company.label for company in companies),
            "by_source": _counter_dict(company.calibration_sample_id for company in companies),
            "source_label_counts": {
                f"{source}:{label}": source_label[(source, label)]
                for source in sorted({company.calibration_sample_id for company in companies})
                for label in LABELS
            },
            "by_split": _counter_dict(splits[company.company_id] for company in companies),
            "split_label_counts": actual_split_label,
            "split_label_targets": configured_split_label,
            "split_label_imbalances": {
                key: actual_split_label[key] - configured_split_label[key]
                for key in configured_split_label
                if actual_split_label[key] != configured_split_label[key]
            },
            "by_language": _counter_dict(company.language_group for company in companies),
            "by_site_size": _counter_dict(company.site_size_band for company in companies),
            "by_enrichment_stratum": _counter_dict(company.enrichment_stratum or "not_available" for company in companies),
            "shared_domain": sum(company.shared_domain for company in companies),
            "attribution_uncertain": sum(company.attribution_uncertain for company in companies),
            "third_party_issue": sum(company.third_party_issue for company in companies),
            "cross_host_issue": sum(company.cross_host_issue for company in companies),
            "distinct_domains": len({company.domain for company in companies}),
        },
        "pages": {
            "annotation_items": page_count,
            "unique_scraped_pages": unique_scraped_page_count,
            "by_pool": _counter_dict(page.sampling_pool for page in selected_pages),
            "by_split": _counter_dict(page.split for page in selected_pages),
            "by_language": _counter_dict(page.page.language_group for page in selected_pages),
            "scorer_v1_diagnostic_count": diagnostics,
            "scorer_v1_diagnostic_percentage": round(100 * diagnostics / page_count, 4),
            "scorer_v1_independent_count": scorer_independent,
            "scorer_v1_independent_percentage": round(100 * scorer_independent / page_count, 4),
            "independent_of_scorer_v1_and_previous_llm_count": prior_independent,
            "independent_of_scorer_v1_and_previous_llm_percentage": round(100 * prior_independent / page_count, 4),
            "raw_usable_company_page_records": raw_pages,
            "content_unique_company_page_records": unique_pages,
            "duplicate_records_removed": raw_pages - unique_pages,
            "selected_duplicate_representatives": sum(page.page.duplicate_group_size > 1 for page in selected_pages),
            "selected_pages_per_company": {
                "minimum": min(selected_counts.values()),
                "median": median(selected_counts.values()),
                "maximum": max(selected_counts.values()),
            },
            "companies_below_base_quota": len(allocation_shortfalls),
            "companies_extended_above_base_quota": len(allocation_extensions),
            "companies_at_absolute_cap": sum(
                count == config.absolute_company_cap for count in selected_counts.values()
            ),
        },
        "validation": {
            "target_company_count_met": len(companies) == config.target_companies,
            "company_label_targets_met": all(
                sum(company.label == label for company in companies)
                == config.company_targets[label]
                for label in LABELS
            ),
            "configured_source_targets_met": all(
                source_label[(source, label)] == target
                for label, targets in config.source_targets.items()
                for source, target in targets.items()
            ),
            "page_range_met": config.minimum_pages <= unique_scraped_page_count <= config.maximum_pages,
            "scorer_v1_diagnostic_at_most_15_percent": diagnostics / page_count <= 0.15,
            "scorer_v1_independent_at_least_85_percent": scorer_independent / page_count >= 0.85,
            "scorer_and_previous_llm_independent_at_least_70_percent": prior_independent / page_count >= 0.70,
            "domain_split_isolation": all(
                len({splits[company.company_id] for company in companies if company.domain == domain}) == 1
                for domain in {company.domain for company in companies}
            ),
            "annotation_items_unique": len(selected_pages)
            == len({(page.page.company_id, page.page.scraped_page_id) for page in selected_pages}),
            "only_selected_companies": all(
                page.page.company_id in selected_company_ids for page in selected_pages
            ),
            "only_v1_extractions": all(
                page.page.extraction_version == "v1" for page in selected_pages
            ),
            "absolute_company_cap_respected": max(selected_counts.values())
            <= config.absolute_company_cap,
            "base_allocation_quotas_met": not allocation_shortfalls,
            "allocation_shortfalls": allocation_shortfalls,
            "allocation_extensions": allocation_extensions,
        },
    }


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_artifacts(
    output_directory: Path,
    config_path: Path,
    config: AnnotationSampleConfig,
    companies: list[CompanyCandidate],
    splits: dict[str, str],
    selected_pages: list[SelectedPage],
    audit: dict[str, Any],
    force_refresh: bool,
) -> dict[str, str]:
    if output_directory.exists():
        if not force_refresh:
            raise FileExistsError(f"Annotation sample output already exists: {output_directory}")
        shutil.rmtree(output_directory)
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)

    company_path = temporary / "companies.jsonl"
    page_path = temporary / "pages.jsonl"
    audit_path = temporary / "audit_summary.json"
    config_snapshot = temporary / "config_snapshot.yaml"

    company_records = []
    for rank, company in enumerate(sorted(companies, key=lambda item: item.company_id), 1):
        record = asdict(company)
        record.update({"company_rank": rank, "split": splits[company.company_id]})
        company_records.append(record)
    _write_jsonl(company_path, company_records)

    page_records = []
    for annotation_rank, selected in enumerate(
        sorted(selected_pages, key=lambda item: (item.split, item.page.company_id, item.company_page_rank)),
        1,
    ):
        record = asdict(selected.page)
        record.update(
            {
                "annotation_rank": annotation_rank,
                "annotation_item_id": f"{selected.page.company_id}:{selected.page.scraped_page_id}",
                "split": selected.split,
                "sampling_pool": selected.sampling_pool,
                "selection_reason": selected.selection_reason,
                "scorer_v1_diagnostic": selected.scorer_v1_diagnostic,
                "scorer_v1_independent": selected.scorer_v1_independent,
                "independent_of_scorer_v1_and_previous_llm": selected.independent_of_scorer_v1_and_previous_llm,
                "company_page_rank": selected.company_page_rank,
            }
        )
        page_records.append(record)
    _write_jsonl(page_path, page_records)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config_snapshot.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    files = {
        "companies": company_path.name,
        "pages": page_path.name,
        "audit": audit_path.name,
        "config_snapshot": config_snapshot.name,
    }
    manifest = {
        "sample_id": config.sample_id,
        "sample_version": config.sample_version,
        "sampling_seed": config.sampling_seed,
        "config_hash": config.config_hash,
        "company_count": len(company_records),
        "page_annotation_item_count": len(page_records),
        "unique_scraped_page_count": audit["pages"]["unique_scraped_pages"],
        "files": {
            key: {"path": value, "sha256": _sha256(temporary / value)}
            for key, value in files.items()
        },
    }
    manifest_path = temporary / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.rename(output_directory)
    return {key: str(output_directory / value) for key, value in {**files, "manifest": manifest_path.name}.items()}


def build_annotation_sample(
    connection: sqlite3.Connection,
    project_root: Path,
    config: AnnotationSampleConfig,
    config_path: Path,
    output_directory: Path,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    connection.row_factory = sqlite3.Row
    candidates, pages_by_domain, result_rows = load_company_candidates(connection, project_root, config)
    companies = select_companies(candidates, config)
    splits = assign_domain_grouped_splits(companies, config)
    companies_by_id = {company.company_id: company for company in companies}
    all_pages_by_company: dict[str, list[PageCandidate]] = {}
    selected_by_company: dict[str, list[SelectedPage]] = {}
    for company in companies:
        pages = build_page_candidates(
            connection,
            company,
            pages_by_domain[company.domain],
            result_rows[company.company_id],
        )
        all_pages_by_company[company.company_id] = pages
        selected_by_company[company.company_id] = select_pages_for_company(
            company, pages, splits[company.company_id], config
        )
    extend_to_target(selected_by_company, all_pages_by_company, companies_by_id, splits, config)
    selected_pages = [page for company_id in sorted(selected_by_company) for page in selected_by_company[company_id]]
    audit = build_audit(companies, splits, selected_pages, config)
    failed_checks = [name for name, passed in audit["validation"].items() if isinstance(passed, bool) and not passed]
    if failed_checks:
        raise ValueError(f"Annotation sample validation failed: {', '.join(failed_checks)}")
    paths = write_artifacts(
        output_directory,
        config_path,
        config,
        companies,
        splits,
        selected_pages,
        audit,
        force_refresh,
    )
    return audit, paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the deterministic scorer-v2 page annotation sample")
    parser.add_argument("--config", type=Path, help="Page annotation sample YAML path")
    parser.add_argument("--output-directory", type=Path, help="Override the configured artifact directory")
    parser.add_argument("--force-refresh", action="store_true", help="Replace this builder's existing artifact directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = find_project_root()
    config_path = args.config or project_root / "config" / "page_annotation_sample.yaml"
    config = load_annotation_sample_config(project_root, config_path)
    output_directory = args.output_directory or project_root / config.output_directory
    if not output_directory.is_absolute():
        output_directory = project_root / output_directory
    database_path = resolve_database_path(project_root, load_config(project_root))
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
        audit, paths = build_annotation_sample(
            connection,
            project_root,
            config,
            config_path,
            output_directory,
            force_refresh=args.force_refresh,
        )
    print("Scorer-v2 page annotation sample")
    print(f"Companies: {audit['companies']['total']}")
    print(f"Page annotation items: {audit['pages']['annotation_items']}")
    print(f"Scorer-v1 independent: {audit['pages']['scorer_v1_independent_percentage']:.2f}%")
    print(
        "Scorer-v1 and previous-LLM independent: "
        f"{audit['pages']['independent_of_scorer_v1_and_previous_llm_percentage']:.2f}%"
    )
    print(f"Artifacts: {paths['manifest']}")


if __name__ == "__main__":
    main()
