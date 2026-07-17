# Project Status

This file records completed implementation milestones. Future Codex milestones should update this file as part of the definition of done.

## 2026-06-10 — Milestone 3A.5g.1: Ecosystem-Relevance Prompt Upgrade

### Objective

Upgrade the OpenAI retention-validation experiment so it evaluates both firm representativeness and AI-ecosystem usefulness before running the full validation sample.

### Implementation Summary

- Added prompt version `v2` as the default validation prompt.
- Preserved `v1` scoring so existing results remain reproducible and comparable.
- Extended validation structured output with AI-ecosystem relevance fields.
- Added migration-safe SQLite columns for ecosystem relevance scores, AI role, evidence strength, and AI relevance reasoning.
- Updated the validation runner to support `--prompt-version v1` and `--prompt-version v2`.
- Updated tests to cover v2 output parsing and v1 compatibility.
- Ran only the requested `smoke_v1` v2 validation; `main_v1` was not run.

### Files Added

- `docs/project_status.md`

### Files Modified

- `config/config.yaml`
- `README.md`
- `src/ai_ecosystem/db/schema.sql`
- `src/ai_ecosystem/db/init_db.py`
- `src/ai_ecosystem/validation/run_retention_validation.py`
- `tests/test_acquisition_schema.py`
- `tests/test_run_retention_validation.py`

### Commands Executed

```bash
.venv/bin/python -m unittest tests.test_run_retention_validation tests.test_acquisition_schema
.venv/bin/python -m ai_ecosystem.db.init_db
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall src tests
.venv/bin/python -m ai_ecosystem.validation.run_retention_validation --sample smoke_v1 --prompt-version v2 --force-refresh
```

### Key Findings

- V2 smoke validation completed successfully.
- Run ID: `validation_20260610T123208Z_005db046`
- Pages selected: 10
- Pages completed: 10
- Pages failed: 0
- Input tokens: 11,350
- Output tokens: 9,729
- Total tokens: 21,079
- Firm relevance distribution: `3=1`, `4=7`, `5=2`
- Ecosystem relevance distribution: `1=10`
- AI role distribution: `not_ai_related=10`
- AI evidence strength distribution: `none=10`

### Current Milestone

Milestone 3A.5g.1 is complete. The validation experiment now records both organizational usefulness and AI-ecosystem usefulness.

### Next Recommended Step

Manually review the `smoke_v1` v2 outputs before running `main_v1`. If the prompt behavior is acceptable, run the full validation sample with `--prompt-version v2`.

### Known Caveats

- The smoke sample is small and not sufficient to infer full-sample behavior.
- Token usage is reported, but dollar cost is intentionally not calculated.
- The v2 smoke sample found no AI-related pages; this may reflect the sample composition rather than the broader corpus.
## 2026-06-11 — Milestone 3A.5h: Retention Validation Analysis & Reporting

### Date

2026-06-11

### Ticket / Milestone

Milestone 3A.5h — Retention Validation Analysis & Reporting

### Objective

Analyze the completed v2 retention-validation run and determine whether current retention preserves pages useful for organization profiling and AI-ecosystem determination.

### Implementation Summary

Added a read-only analysis utility that exports CSV and JSON reports from stored validation results without rerunning OpenAI or modifying discovery/retention logic.

### Key Findings

- Validation run analyzed: `validation_20260610T123941Z_06492a45`
- Pages analyzed: 600
- Yes; retained average firm score is 4.26 vs discarded 3.99.
- No clear improvement; retained average ecosystem score is 1.12 vs discarded 1.193.
- False negatives: 23
- False positives: 14

### Current Status

Retention validation evidence reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the validation findings manually before deciding whether to tune retention priorities or proceed to Milestone 3B.
## 2026-06-11 — Milestone 3A.5i: Ecosystem Signal Deep Dive

### Date

2026-06-11

### Ticket / Milestone

Milestone 3A.5i — Ecosystem Signal Deep Dive

### Objective

Investigate where AI-ecosystem signal appears in the completed validation results and why useful ecosystem pages were retained or discarded.

### Implementation Summary

Added a read-only deep-dive utility that analyzes stored validation, discovery, sample, and text-preview metadata without rerunning OpenAI or modifying pipeline behavior.

### Key Findings

- Validation run analyzed: `validation_20260610T123941Z_06492a45`
- Pages analyzed: 600
- Ecosystem-signal pages: 43
- False negatives: 23
- Top signal domains: evk.biz, ic-steiermark.at, bertrandt.com, consultatio.at, schachinger.com

### Current Status

Ecosystem signal evidence reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the evidence manually before deciding whether to tune retention priorities or move to Milestone 3B.
## 2026-06-11 — Milestone 3A.5i.1: Ecosystem Signal Pattern Discovery

### Date

2026-06-11

### Ticket / Milestone

Milestone 3A.5i.1 — Ecosystem Signal Pattern Discovery

### Objective

Identify what differentiates ecosystem-signal pages from non-signal pages, and what ecosystem signal the current retention process is most likely to lose.

### Implementation Summary

Added a read-only pattern-discovery utility using transparent token, n-gram, lift, and ecosystem-score-weighted analyses over stored validation outputs and text previews.

### Key Findings

- Validation run analyzed: `validation_20260610T123941Z_06492a45`
- Signal pages: 39
- Non-signal pages: 547
- Discarded signal pages: 23
- Top weighted theme: research
- Top lost pattern: sitemap

### Current Status

Pattern-discovery reports are available under `data/interim/` and `logs/`.

### Next Recommended Milestone

Review the pattern evidence manually before deciding whether retention-priority experiments are warranted.
## 2026-06-27 — Milestone 3B.1: Extraction Schema & Configuration

### Date

2026-06-27

### Ticket / Milestone

Milestone 3B.1 — Extraction Schema & Configuration

### Objective

Prepare schema, configuration, directories, and helper utilities for the HTML extraction stage without running extraction.

### Implementation Summary

Added extraction metadata tables, idempotent migration support, extraction config defaults, deterministic path helpers, and a no-op preparation CLI for schema and directory initialization.

### Key Findings

- `extracted_pages`, `page_features`, and `page_links` are prepared for later extraction milestones.
- Extraction outputs are configured under `data/extracted/markdown` and `data/extracted/text`.
- SQLite stores metadata and file paths only; extracted text is not stored in the database.
- No HTML extraction, Trafilatura integration, Markdown generation, feature extraction, link extraction, OpenAI calls, or QA reporting was added.

### Current Status

Milestone 3B.1 infrastructure is ready.

### Next Recommended Milestone

Implement the extraction runner in Milestone 3B.2 with a controlled smoke test.
## 2026-06-27 — Milestone 3B.2: Trafilatura Extraction Pipeline

### Date

2026-06-27

### Ticket / Milestone

Milestone 3B.2 — Trafilatura Extraction Pipeline

### Objective

Implement in-memory main-content extraction for acquired HTML pages using Trafilatura and store extraction metadata only.

### Implementation Summary

Added a runnable extraction pipeline that selects successful acquired HTML pages, reads gzip-compressed HTML, runs Trafilatura extraction in memory, and upserts `extracted_pages` metadata with idempotent skip and force-refresh behavior.

### Key Findings

- Trafilatura is now a declared project dependency.
- Successful extractions are recorded with `scraped_page_id`, `extraction_version`, `extraction_status`, `language`, and `extracted_at`.
- Extraction failures are recorded without aborting the run.
- `markdown_path` and `text_path` remain NULL in this milestone.
- No Markdown files, plain-text files, page features, links, QA reports, or OpenAI calls are produced.

### Current Status

The extraction runner is ready for smoke testing on acquired HTML pages.

### Next Recommended Milestone

Add controlled persistence of extracted Markdown/text after reviewing smoke-test extraction quality.
## 2026-06-28 — Milestone 3B.3: Canonical Extracted Evidence Package

### Date

2026-06-28

### Ticket / Milestone

Milestone 3B.3 — Canonical Extracted Evidence Package

### Objective

Persist each successful extraction as a reusable canonical evidence package containing Markdown, plain text, and metadata.

### Implementation Summary

Extended the extraction runner to write deterministic evidence packages under `data/extracted/pages/<shard>/<scraped_page_id>/` with `page.md`, `page.txt`, and `metadata.json`, while updating SQLite with file paths and content hashes.

### Key Findings

- Plain text is derived from Trafilatura Markdown with light Markdown-syntax cleanup for query-family and keyword search.
- `content_sha256` is computed from normalized plain text for future change detection and reproducibility.
- `extracted_pages` now stores `markdown_path`, `text_path`, `metadata_path`, and `content_sha256`; extracted content itself remains outside SQLite.
- Existing metadata-only successful extractions are reprocessed until canonical package paths exist.
- Heading parsing, link extraction, feature extraction, QA reporting, scoring, embeddings, and OpenAI validation remain out of scope.

### Current Status

Canonical extracted evidence packages are now available as the stable downstream input format.

### Next Recommended Milestone

Implement structural feature extraction over the canonical evidence package in Milestone 3B.4.

## 2026-06-28 — Milestone 3B.4: Page Feature Store + Pipeline Run Infrastructure

### Date

2026-06-28

### Ticket / Milestone

Milestone 3B.4 — Page Feature Store + Pipeline Run Infrastructure

### Objective

Populate reusable page metadata, structural features, and link metadata from canonical evidence packages while standardizing extraction runs by sample and improving progress reporting.

### Implementation Summary

Extended the extraction runner with `--sample`, feature-store population, original-HTML link extraction, `extraction_runs` tracking, per-page `extraction_run_id` assignment, and periodic progress output with elapsed time, ETA, skips, successes, and failures.

### Key Findings

- `page_features` now stores title, language, word count, paragraph count, heading count, headings, heading hierarchy, page type, and evidence package paths.
- `page_links` now stores source URL, target URL, normalized target URL/domain, internal/external flag, anchor text, anchor word count, rel attribute, and link order.
- Extraction runs are recorded in `extraction_runs` with selected, processed, successful, skipped, and failed counts plus a configuration hash.
- `--sample smoke_v1 --force-refresh` processed 10 pages successfully; repeating `--sample smoke_v1` skipped the same 10 pages.
- Evidence packages remain the canonical downstream input; no scoring, retention, embeddings, OpenAI calls, or QA reports were added.

### Current Status

Milestone 3B.4 is complete. The project now has a reusable page feature/link store and observable extraction run infrastructure.

### Next Recommended Milestone

Implement extraction QA and corpus diagnostics before introducing content scoring.

## 2026-07-08 — Milestone 3B.5: Automatic Extraction QA Report

### Date

2026-07-08

### Ticket / Milestone

Milestone 3B.5 — Automatic Extraction QA Report

### Objective

Add a read-only QA utility that analyzes extracted evidence packages, page features, and links before content scoring.

### Implementation Summary

Implemented `ai_ecosystem.extraction.analyze_extraction_quality` with sample-first CLI execution, optional extraction-version/run/domain filters, deterministic non-overwriting report directories, CSV exports, JSON summary output, and warning heuristics for extraction quality issues.

### Key Findings

- `smoke_v1` QA analyzed 10 pages from extraction run `extraction_20260627T233442Z_3bda4262`.
- `smoke_v1` average word count was 176.3 and median word count was 143.5.
- `smoke_v1` warnings included 7 pages without headings, 6 HTML-to-text size-gap warnings, and 2 duplicate-content groups.
- `main_v1` QA analyzed 599 of 600 pages from extraction run `extraction_20260708T111745Z_ff03df23`.
- `main_v1` extraction success rate was 99.83%, with 599 successful pages and 1 failed page.
- `main_v1` average word count was 231.85 and median word count was 119.0; the shortest extracted page had 12 words.
- `main_v1` warnings included 272 pages without headings, 397 HTML-to-text size-gap warnings, 27 duplicate-content groups, and 1 extremely high-link-count page.
- No critical extraction engineering problems were identified. The main caveat is a high share of short pages, which should be handled during semantic validation and content scoring rather than blocking progress.
- Reports were generated under `data/interim/extraction_qa/`.
- The analyzer is read-only with respect to SQLite extraction tables and evidence packages; no extraction reruns, OpenAI calls, scoring, or retention changes were added.

### Current Status

Milestone 3B.5 is complete. Extraction QA reports indicate that extraction is robust enough to proceed to semantic extraction validation and initial content-scoring experiments, with expected real-world website noise around short pages, missing headings, duplicate content, and HTML/template bloat.

### Next Recommended Milestone

Proceed to semantic extraction validation, while monitoring short-page and duplicate-content effects before finalizing content-scoring thresholds.

## 2026-07-08 — Milestone 4.1: Scoring Schema & Configuration Foundations

### Date

2026-07-08

### Ticket / Milestone

Milestone 4.1 — Scoring Schema & Configuration Foundations

### Objective

Prepare the database and configuration layer for the content scoring engine without scoring pages.

### Implementation Summary

Added scoring-stage SQLite tables, scoring configuration, knowledge-base/profile loading helpers, required-dimension validation, and tests for fresh schema creation, migration-safe initialization, and configuration loading.

### Key Findings

- `knowledge_bases`, `scoring_profiles`, `scoring_runs`, and `page_scores` are now available through fresh schema creation and idempotent migrations.
- `page_scores` can store query, keyword, boost, negative, and final score components plus top-match JSON and knowledge-base/profile version provenance.
- The scoring profile config defines required dimensions, dimension weights, field weights, and query/keyword/boost/negative fusion weights.
- Existing knowledge-base YAML files for `firm_profile`, `technology`, `innovation`, and `ecosystem` are readable and validated as required dimensions.
- Verification passed with 104 unit tests, compileall, and `ai_ecosystem.db.init_db`.
- No scoring engine, retention logic, or OpenAI calls were added.

### Current Status

Milestone 4.1 is complete. The project is ready to implement the first content scoring engine against extracted evidence packages.

### Next Recommended Milestone

Implement the read-only content scoring engine that computes page scores from extracted evidence packages and the four configured knowledge bases.

## 2026-07-08 — Milestone 4.2: Core Content Scoring Engine

### Date

2026-07-08

### Ticket / Milestone

Milestone 4.2 — Core Content Scoring Engine

### Objective

Implement a reusable, database-free scoring library that scores a single extracted page in memory.

### Implementation Summary

Added `ContentScorer`, structured page input/result dataclasses, deterministic weighted query-family lexical scoring, exact normalized phrase matching for keywords/boost/negative terms, configurable field weighting, configurable fusion, and explainability output for the four scoring dimensions.

### Key Findings

- The scorer operates only in memory and has no SQLite dependency.
- The query-family component uses deterministic query-term saturation over weighted fields and intentionally does not approximate corpus-wide IDF.
- Keyword, boost, and negative matching supports exact normalized multi-word phrases and single-word token matches across English and German terms.
- Field weights now explicitly cover title, headings, markdown, plain text, URL, internal anchor text, and external anchor text, with legacy `text` and `anchor_text` fallback support.
- Results expose query, keyword, boost, negative, and final scores plus top query/term matches for later serialization.
- Verification passed with 113 unit tests and compileall.
- No corpus scoring loop, scoring runs, SQLite writes, reports, retention logic, embeddings, or OpenAI calls were added.

### Current Status

Milestone 4.2 is complete. The project now has the core single-page scoring library needed by the future scoring pipeline.

### Next Recommended Milestone

Implement the scoring runner that applies the core scorer to selected extracted pages and persists `page_scores` through tracked `scoring_runs`.

## 2026-07-15 — Milestone 4.2a: Query-Family Scoring Terminology Migration

### Date

2026-07-15

### Ticket / Milestone

Milestone 4.2a — Project-wide Terminology Migration to Query-Family Scoring

### Objective

Rename the implemented single-page lexical query component so project terminology matches the actual method.

### Implementation Summary

Renamed the legacy implementation-facing query terminology to query-family terminology across the scorer API, configuration, knowledge-base YAML keys, SQLite schema/migrations, tests, and project documentation while preserving scoring behavior.

### Key Findings

- The score field for query-family matches is now `query_score`.
- Knowledge-base query lists now use `query_families`.
- The fusion weight key is now `query`.
- The implemented method is documented as a deterministic weighted query-family lexical scorer.
- The method uses weighted lexical overlap, query normalization, stopword removal, frequency saturation, and configurable field weights.
- The method intentionally does not use corpus IDF, document frequency, document-length normalization, or corpus indexing.
- Numerical scoring behavior was intentionally preserved.

### Current Status

The scoring terminology is now methodologically aligned with the implementation and ready for thesis-facing documentation.

### Next Recommended Milestone

Implement the scoring runner that applies the core scorer to selected extracted pages and persists `page_scores` through tracked `scoring_runs`.

## 2026-07-15 — Milestone 4.3: Corpus Scoring Runner & Score Persistence

### Date

2026-07-15

### Ticket / Milestone

Milestone 4.3 — Corpus Scoring Runner & Score Persistence

### Objective

Implement the corpus-level scoring runner that applies the existing `ContentScorer` to extracted evidence packages and persists per-dimension scores.

### Implementation Summary

Added `python -m ai_ecosystem.scoring.run_scoring` with sample, page-limit, domain, force-refresh, and progress controls. The runner selects successful extracted pages, builds `PageScoringInput` from evidence packages plus optional features and link anchors, persists `scoring_runs` and `page_scores`, enforces score identity independent of run ID, and writes scoring reports under `data/interim/scoring/<scoring_run_id>/`.

### Key Findings

- Pilot run `scoring_20260715T131248Z_f305ef18` scored `main_v1`.
- Pages selected: 599; processed: 599; scored: 599; skipped: 0; failed: 0.
- Score rows written: 2,396, covering firm profile, technology, innovation, and ecosystem for every scored page.
- Runtime was 76.6 seconds at 7.82 pages/sec.
- Reports were generated in `data/interim/scoring/scoring_20260715T131248Z_f305ef18/`.
- Verification passed with 118 unit tests, compileall, and idempotent DB initialization.
- No scoring math, knowledge bases, retention logic, embeddings, or OpenAI calls were changed.

### Current Status

Milestone 4.3 is complete. Corpus scoring is operational for extracted evidence packages, with persisted scores and reproducible run-level reporting.

### Next Recommended Milestone

Review scoring distributions and representative examples before designing retention thresholds or score-based retention policy.

## 2026-07-17 — Milestone 4.4: LLM-Assisted Scoring Evaluation

### Date

2026-07-17

### Ticket / Milestone

Milestone 4.4 — LLM-Assisted Scoring Evaluation

### Objective

Prepare high-scoring pages for qualitative GPT review without changing scoring, knowledge bases, or retention behavior.

### Implementation Summary

Added `python -m ai_ecosystem.scoring.evaluate_scoring`, a read-only package generator that selects the top scored pages per dimension, writes one Markdown review file per selected dimension/page pair, creates a reusable GPT evaluation prompt, and records package metadata in `manifest.json`.

### Key Findings

- Evaluation package generated for scoring run `scoring_20260715T131248Z_f305ef18`.
- Output directory: `data/interim/scoring_evaluation/scoring_20260715T131248Z_f305ef18/`.
- Generated 80 review files: top 20 pages for each of firm profile, technology, innovation, and ecosystem.
- Generated `evaluation_prompt.md` and `manifest.json`.
- Package warnings: 0.
- Verification passed with 122 unit tests and compileall.
- No OpenAI calls, retention decisions, scoring changes, or knowledge-base changes were made.

### Current Status

Milestone 4.4 is complete. The project now has a reusable manual/GPT review package for inspecting whether high scoring pages are genuinely useful and whether score components are justified.

### Next Recommended Milestone

Manually review the scoring evaluation package, document recurring false positives and useful evidence patterns, then decide whether score calibration or retention-policy design should come next.

## 2026-07-17 — Milestone 4.5: Automated LLM Scoring Evaluation

### Date

2026-07-17

### Ticket / Milestone

Milestone 4.5 — Automated LLM Scoring Evaluation

### Objective

Automatically evaluate high-scoring page review files with the OpenAI Responses API to identify scoring strengths, false positives, and retention-design evidence.

### Implementation Summary

Added `python -m ai_ecosystem.scoring.run_llm_evaluation`, which reads a scoring evaluation package, evaluates each page Markdown file with strict JSON output, supports resumable execution, and writes page-level plus aggregate Markdown/JSON reports.

### Key Findings

- Evaluation directory: `/Users/matetoth/TU_Data_Science_MSc/ai-ecosystem-austria/data/interim/scoring_evaluation/scoring_20260715T131248Z_f305ef18`.
- Output directory: `/Users/matetoth/TU_Data_Science_MSc/ai-ecosystem-austria/data/interim/scoring_evaluation/scoring_20260715T131248Z_f305ef18/llm_evaluation_gpt-5`.
- Model: `gpt-5`.
- Pages evaluated: 80 / 80.
- Pages failed: 0.
- Total tokens: 551229.
- Estimated cost USD: 2.229027.
- Generated `page_evaluations.json`, `page_evaluations.md`, `evaluation_summary.json`, and `evaluation_summary.md`.
- No scores, knowledge bases, retention logic, SQLite scoring tables, or evidence packages were modified.

### Current Status

Milestone 4.5 is complete. The project now has automated qualitative evaluation outputs for the high-scoring page review package.

### Next Recommended Milestone

Review the LLM evaluation findings and decide whether to calibrate scoring, adjust knowledge-base terms, or design the first score-based retention policy.
