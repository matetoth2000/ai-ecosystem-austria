"""Run raw HTML acquisition for retained or discovered URLs."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_ecosystem.acquisition.html_fetcher import (
    ERROR_STATUSES,
    HTML_STATUSES,
    CandidatePage,
    FetchResult,
    existing_successful_scrape,
    fetch_candidate_page,
    generate_acquisition_run_id,
    insert_acquisition_run,
    select_candidate_pages,
    setup_acquisition_logger,
    upsert_scraped_page,
    update_acquisition_run,
    utc_now_iso,
)
from ai_ecosystem.db.init_db import (
    ensure_acquisition_schema,
    find_project_root,
    load_config,
    resolve_database_path,
)


@dataclass(frozen=True)
class AcquisitionConfig:
    timeout_seconds: int
    retry_count: int
    max_workers: int
    skip_existing: bool
    force_refresh: bool
    user_agent: str
    progress_log_every_n_pages: int


@dataclass(frozen=True)
class AcquisitionWorkerResult:
    candidate: CandidatePage
    fetch_result: FetchResult | None
    skipped_existing: bool = False
    unexpected_error: str | None = None


def format_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_acquisition_config(config: dict, force_refresh: bool, max_workers: int | None = None) -> AcquisitionConfig:
    acquisition_config = config.get("acquisition", {})
    discovery_config = config.get("discovery", {})
    return AcquisitionConfig(
        timeout_seconds=int(acquisition_config.get("timeout_seconds", 15)),
        retry_count=int(acquisition_config.get("retry_count", 2)),
        max_workers=max(1, int(max_workers or acquisition_config.get("max_workers", 1))),
        skip_existing=bool(acquisition_config.get("skip_existing", True)),
        force_refresh=force_refresh or bool(acquisition_config.get("force_refresh", False)),
        user_agent=discovery_config.get("user_agent", "AIAustriaResearchBot/0.1"),
        progress_log_every_n_pages=max(1, int(acquisition_config.get("progress_log_every_n_pages", 25))),
    )


def acquire_candidate(
    db_path: Path,
    project_root: Path,
    candidate: CandidatePage,
    acquisition_run_id: str,
    acquisition_config: AcquisitionConfig,
) -> AcquisitionWorkerResult:
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        existing = existing_successful_scrape(
            connection,
            candidate.candidate_page_id,
            candidate.discovered_page_id,
        )
        if acquisition_config.skip_existing and not acquisition_config.force_refresh and existing:
            return AcquisitionWorkerResult(candidate=candidate, fetch_result=None, skipped_existing=True)

    result = fetch_candidate_page(
        candidate,
        project_root,
        acquisition_run_id,
        acquisition_config.timeout_seconds,
        acquisition_config.retry_count,
        acquisition_config.user_agent,
    )

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        upsert_scraped_page(connection, result)
        connection.commit()

    return AcquisitionWorkerResult(candidate=candidate, fetch_result=result)


def log_run_start(
    logger: logging.Logger,
    acquisition_run_id: str,
    sample_id: str | None,
    fetch_scope: str,
    max_pages: int | None,
    acquisition_config: AcquisitionConfig,
    selected_pages: int,
) -> None:
    logger.info("run_start acquisition_run_id=%s", acquisition_run_id)
    logger.info(
        "config sample_id=%s fetch_scope=%s max_pages=%s max_workers=%s timeout_seconds=%s retry_count=%s "
        "skip_existing=%s force_refresh=%s user_agent=%s progress_log_every_n_pages=%s selected_pages=%s",
        sample_id,
        fetch_scope,
        max_pages,
        acquisition_config.max_workers,
        acquisition_config.timeout_seconds,
        acquisition_config.retry_count,
        acquisition_config.skip_existing,
        acquisition_config.force_refresh,
        acquisition_config.user_agent,
        acquisition_config.progress_log_every_n_pages,
        selected_pages,
    )


def print_progress(
    completed: int,
    total: int,
    pages_fetched: int,
    pages_failed: int,
    pages_skipped: int,
    started_monotonic: float,
) -> None:
    elapsed = max(0.001, time.monotonic() - started_monotonic)
    rate = completed / elapsed
    print(
        "Processed: "
        f"{completed}/{total} "
        f"Success: {pages_fetched} "
        f"Failed: {pages_failed} "
        f"Skipped: {pages_skipped} "
        f"Rate: {rate:.2f} pages/sec "
        f"Elapsed: {format_duration(elapsed)}"
    )


def finalize_acquisition_run(
    db_path: Path,
    logger: logging.Logger,
    acquisition_run_id: str,
    pages_attempted: int,
    pages_fetched: int,
    pages_failed: int,
    pages_skipped: int,
    elapsed_seconds: float,
    final_status: str,
) -> float:
    success_rate = (pages_fetched / pages_attempted * 100) if pages_attempted else 0.0
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        update_acquisition_run(
            connection,
            acquisition_run_id,
            utc_now_iso(),
            pages_attempted,
            pages_fetched,
            pages_failed,
            success_rate,
        )
        connection.commit()

    logger.info(
        "run_complete acquisition_run_id=%s final_status=%s pages_attempted=%s pages_fetched=%s "
        "pages_failed=%s pages_skipped=%s success_rate=%.2f elapsed_seconds=%.2f",
        acquisition_run_id,
        final_status,
        pages_attempted,
        pages_fetched,
        pages_failed,
        pages_skipped,
        success_rate,
        elapsed_seconds,
    )
    return success_rate


def run_acquisition(
    sample_id: str | None,
    fetch_scope: str,
    max_pages: int | None = None,
    force_refresh: bool = False,
    max_workers: int | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    project_root = project_root or find_project_root()
    config = load_config(project_root)
    db_path = resolve_database_path(project_root, config)
    acquisition_config = load_acquisition_config(config, force_refresh=force_refresh, max_workers=max_workers)
    acquisition_run_id = generate_acquisition_run_id()
    logger = setup_acquisition_logger(project_root, acquisition_run_id)
    started_at = utc_now_iso()

    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        ensure_acquisition_schema(connection)
        candidates = select_candidate_pages(
            connection,
            sample_id,
            fetch_scope,
            limit=max_pages,
            deterministic_limit_order=max_pages is not None,
        )
        insert_acquisition_run(
            connection,
            acquisition_run_id,
            sample_id,
            fetch_scope,
            started_at,
            "raw HTML acquisition",
        )
        connection.commit()

    log_run_start(
        logger,
        acquisition_run_id,
        sample_id,
        fetch_scope,
        max_pages,
        acquisition_config,
        len(candidates),
    )

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Acquisition started")
    print(f"Run ID: {acquisition_run_id}")
    print(f"Fetch scope: {fetch_scope}")
    print(f"Pages selected: {len(candidates)}")
    print(f"Workers: {acquisition_config.max_workers}")

    pages_fetched = 0
    pages_failed = 0
    pages_skipped = 0
    completed = 0
    started_monotonic = time.monotonic()
    success_rate = 0.0
    final_status = "completed"
    pending_exception: BaseException | None = None
    executor = ThreadPoolExecutor(max_workers=acquisition_config.max_workers)
    future_to_candidate = {}
    try:
        future_to_candidate = {
            executor.submit(
                acquire_candidate,
                db_path,
                project_root,
                candidate,
                acquisition_run_id,
                acquisition_config,
            ): candidate
            for candidate in candidates
        }

        for future in as_completed(future_to_candidate):
            candidate = future_to_candidate[future]
            completed += 1
            try:
                worker_result = future.result()
            except Exception as error:
                pages_failed += 1
                logger.exception(
                    "failure url=%s domain=%s status=other_error error=%s",
                    candidate.url,
                    candidate.domain,
                    error,
                )
            else:
                if worker_result.skipped_existing:
                    pages_skipped += 1
                elif worker_result.fetch_result is None:
                    pages_failed += 1
                    logger.error(
                        "failure url=%s domain=%s status=other_error error=missing_fetch_result",
                        candidate.url,
                        candidate.domain,
                    )
                elif worker_result.fetch_result.scrape_status in HTML_STATUSES:
                    pages_fetched += 1
                elif worker_result.fetch_result.scrape_status in ERROR_STATUSES:
                    pages_failed += 1
                    logger.warning(
                        "failure url=%s domain=%s status=%s http_status=%s error=%s",
                        worker_result.fetch_result.url,
                        worker_result.fetch_result.domain,
                        worker_result.fetch_result.scrape_status,
                        worker_result.fetch_result.http_status,
                        worker_result.fetch_result.error_message,
                    )
                else:
                    pages_failed += 1
                    logger.warning(
                        "failure url=%s domain=%s status=%s http_status=%s error=%s",
                        worker_result.fetch_result.url,
                        worker_result.fetch_result.domain,
                        worker_result.fetch_result.scrape_status,
                        worker_result.fetch_result.http_status,
                        worker_result.fetch_result.error_message,
                    )

            if (
                completed % acquisition_config.progress_log_every_n_pages == 0
                or completed == len(candidates)
            ):
                print_progress(
                    completed,
                    len(candidates),
                    pages_fetched,
                    pages_failed,
                    pages_skipped,
                    started_monotonic,
                )
    except KeyboardInterrupt as error:
        final_status = "interrupted"
        pending_exception = error
        logger.warning("run_interrupted acquisition_run_id=%s completed=%s", acquisition_run_id, completed)
        for future in future_to_candidate:
            future.cancel()
    except BaseException as error:
        final_status = "exception"
        pending_exception = error
        logger.exception("run_exception acquisition_run_id=%s completed=%s error=%s", acquisition_run_id, completed, error)
        for future in future_to_candidate:
            future.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        elapsed_seconds = time.monotonic() - started_monotonic
        success_rate = finalize_acquisition_run(
            db_path,
            logger,
            acquisition_run_id,
            len(candidates),
            pages_fetched,
            pages_failed,
            pages_skipped,
            elapsed_seconds,
            final_status,
        )

    if pending_exception is not None:
        print("Acquisition summary")
        print(f"  Run ID: {acquisition_run_id}")
        print(f"  Final status: {final_status}")
        print(f"  Pages attempted: {len(candidates)}")
        print(f"  Pages completed: {completed}")
        print(f"  Pages fetched: {pages_fetched}")
        print(f"  Pages failed: {pages_failed}")
        print(f"  Pages skipped: {pages_skipped}")
        print(f"  Success rate: {success_rate:.1f}%")
        print(f"  Elapsed: {format_duration(time.monotonic() - started_monotonic)}")
        print(f"  Log path: {project_root / 'logs' / f'acquisition_{acquisition_run_id}.log'}")
        raise pending_exception

    print("Acquisition summary")
    print(f"  Run ID: {acquisition_run_id}")
    print(f"  Pages attempted: {len(candidates)}")
    print(f"  Pages fetched: {pages_fetched}")
    print(f"  Pages failed: {pages_failed}")
    print(f"  Pages skipped: {pages_skipped}")
    print(f"  Success rate: {success_rate:.1f}%")
    print(f"  Elapsed: {format_duration(time.monotonic() - started_monotonic)}")
    print(f"  Log path: {project_root / 'logs' / f'acquisition_{acquisition_run_id}.log'}")

    return {
        "acquisition_run_id": acquisition_run_id,
        "sample_id": sample_id,
        "fetch_scope": fetch_scope,
        "pages_attempted": len(candidates),
        "pages_fetched": pages_fetched,
        "pages_failed": pages_failed,
        "pages_skipped": pages_skipped,
        "success_rate": success_rate,
        "elapsed_seconds": time.monotonic() - started_monotonic,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run raw HTML acquisition for discovered or retained URLs.")
    parser.add_argument("--sample-id", help="Registered sample_id to acquire.")
    parser.add_argument("--fetch-scope", choices=["discovered", "retained"], required=True)
    parser.add_argument("--max-pages", type=int, help="Deterministic page limit for smoke tests.")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch pages even if stored HTML exists.")
    parser.add_argument("--max-workers", type=int, help="Override acquisition.max_workers.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_acquisition(
        sample_id=args.sample_id,
        fetch_scope=args.fetch_scope,
        max_pages=args.max_pages,
        force_refresh=args.force_refresh,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    main()
