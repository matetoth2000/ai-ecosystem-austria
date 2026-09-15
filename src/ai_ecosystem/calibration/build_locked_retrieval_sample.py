"""Build the all-page manifest for the frozen locked retrieval companies."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ai_ecosystem.calibration.build_page_annotation_sample import (
    build_page_candidates,
    load_usable_pages_by_domain,
)
from ai_ecosystem.db.init_db import find_project_root, load_config, resolve_database_path


SOURCE_SAMPLE = "data/interim/calibration/page_annotation_samples/scorer_v2_page_annotation_v1"
OUTPUT_SAMPLE = "data/interim/calibration/page_annotation_samples/scorer_v2_locked_retrieval_v1"
LOCKED_SPLIT = "locked_retrieval_test"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def build_locked_sample(
    connection: sqlite3.Connection,
    project_root: Path,
    output_directory: Path,
    *,
    force_refresh: bool = False,
    expected_company_count: int = 40,
    expected_page_count: int = 7_318,
) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    if output_directory.exists() and not force_refresh:
        raise FileExistsError(f"Locked retrieval sample already exists: {output_directory}")
    source_directory = project_root / SOURCE_SAMPLE
    source_manifest = json.loads((source_directory / "manifest.json").read_text(encoding="utf-8"))
    source_companies = read_jsonl(source_directory / source_manifest["files"]["companies"]["path"])
    companies = sorted(
        (row for row in source_companies if row["split"] == LOCKED_SPLIT),
        key=lambda row: str(row["company_id"]),
    )
    if len(companies) != expected_company_count:
        raise ValueError(
            f"Expected {expected_company_count} frozen locked companies, found {len(companies)}"
        )
    if len({str(row["domain"]) for row in companies}) != len(companies):
        raise ValueError("Locked company domains are not unique; focal-company page ownership is ambiguous")

    pages_by_domain = load_usable_pages_by_domain(
        connection,
        project_root,
        (str(row["domain"]) for row in companies),
    )
    pages: list[dict[str, Any]] = []
    duplicate_pages_removed = 0
    company_page_counts: Counter[str] = Counter()
    for company in companies:
        result = connection.execute(
            "SELECT * FROM calibration_results WHERE id = ? AND company_id = ?",
            (int(company["calibration_result_id"]), str(company["company_id"])),
        ).fetchone()
        if result is None:
            raise ValueError(f"Missing frozen calibration result for {company['company_id']}")
        domain_rows = pages_by_domain.get(str(company["domain"]), [])
        candidates = build_page_candidates(connection, _company_candidate(company), domain_rows, result)
        duplicate_pages_removed += len(domain_rows) - len(candidates)
        for candidate in sorted(candidates, key=lambda value: value.scraped_page_id):
            company_page_counts[candidate.company_id] += 1
            row = asdict(candidate)
            row.update(
                {
                    "annotation_item_id": f"{candidate.company_id}:{candidate.scraped_page_id}",
                    "annotation_rank": len(pages) + 1,
                    "company_page_rank": company_page_counts[candidate.company_id],
                    "split": LOCKED_SPLIT,
                    "sampling_pool": "locked_full_corpus",
                    "selection_reason": "all_eligible_exact_content_deduplicated",
                    "scorer_v1_diagnostic": False,
                    "scorer_v1_independent": True,
                    "independent_of_scorer_v1_and_previous_llm": True,
                    "duplicate_scraped_page_ids": list(candidate.duplicate_scraped_page_ids),
                    "previous_evidence_references": list(candidate.previous_evidence_references),
                }
            )
            pages.append(row)

    if len(pages) != len({(row["company_id"], row["scraped_page_id"]) for row in pages}):
        raise AssertionError("Locked annotation item keys are not unique")
    if len(pages) != expected_page_count:
        raise ValueError(
            f"Expected {expected_page_count:,} eligible exact-content-deduplicated pages, "
            f"found {len(pages):,}"
        )

    if output_directory.exists():
        shutil.rmtree(output_directory)
    temporary = output_directory.with_name(output_directory.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    write_jsonl(temporary / "companies.jsonl", companies)
    write_jsonl(temporary / "pages.jsonl", pages)
    audit = {
        "sample_id": "scorer_v2_locked_retrieval_v1",
        "source_sample_id": source_manifest["sample_id"],
        "company_count": len(companies),
        "page_annotation_item_count": len(pages),
        "unique_scraped_page_count": len({row["scraped_page_id"] for row in pages}),
        "duplicate_pages_removed": duplicate_pages_removed,
        "pages_by_company": dict(Counter(row["company_id"] for row in pages)),
        "labels_audit_only": dict(Counter(row["label"] for row in companies)),
        "policy": "All usable v1 pages, exact-content deduplicated within each frozen locked company domain.",
    }
    (temporary / "audit_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "sample_id": "scorer_v2_locked_retrieval_v1",
        "sample_version": "v1",
        "source_sample_id": source_manifest["sample_id"],
        "company_count": len(companies),
        "page_annotation_item_count": len(pages),
        "unique_scraped_page_count": len({row["scraped_page_id"] for row in pages}),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "files": {
            name: {"path": path.name, "sha256": sha256_file(path)}
            for name, path in {
                "companies": temporary / "companies.jsonl",
                "pages": temporary / "pages.jsonl",
                "audit": temporary / "audit_summary.json",
            }.items()
        },
    }
    (temporary / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.rename(output_directory)
    return audit


def _company_candidate(row: dict[str, Any]):
    from ai_ecosystem.calibration.build_page_annotation_sample import CompanyCandidate

    fields = CompanyCandidate.__dataclass_fields__
    return CompanyCandidate(**{name: row[name] for name in fields})


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the frozen all-page locked retrieval annotation sample")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root()
    output = args.output_directory or project_root / OUTPUT_SAMPLE
    if not output.is_absolute():
        output = project_root / output
    database_path = resolve_database_path(project_root, load_config(project_root))
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
        audit = build_locked_sample(connection, project_root, output, force_refresh=args.force_refresh)
    print("Locked retrieval annotation sample built")
    print(f"  Companies: {audit['company_count']:,}")
    print(f"  Pages: {audit['page_annotation_item_count']:,}")
    print(f"  Exact duplicates removed: {audit['duplicate_pages_removed']:,}")
    print(f"  Output: {output / 'manifest.json'}")


if __name__ == "__main__":
    main()
