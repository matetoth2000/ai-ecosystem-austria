# Project Status

This file records completed implementation milestones. Future Codex milestones should update this file as part of the definition of done.
Keep milestone/status entries in chronological order, oldest to newest. Append new
updates below existing entries; preserve implementation order within the same date.

Current direction (2026-09-16): Milestone 5 prioritizes company AI-promise
modeling. Further page-ranker optimization is paused. The first grouped company
model comparison and one-time internal-holdout evaluation are complete. Next:
**Generalization and Independent Validation Planning**, not production exclusion.
See the [pivot and artifact-status record](calibration_methodology.md#2026-09-12-company-level-pivot).
Earlier next-step recommendations below describe their dated checkpoints.

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

## 2026-07-28 — Milestone 5.1: Company Relevance Schema, Configuration, and Persistence

### Date

2026-07-28

### Status

Deprecated on 2026-08-25 by Milestone 5.0. This entry is retained as historical implementation context only.

### Ticket / Milestone

Milestone 5.1 — Company Relevance Schema, Configuration, and Persistence

### Objective

Prepare versioned storage and configuration foundations for conservative company-level AI relevance triage without implementing classification logic.

### Implementation Summary

Added `company_relevance_runs` and `company_relevance_classifications` to the fresh SQLite schema and idempotent initialization path. Added `config/company_relevance.yaml` with versioned classifier/config IDs, supported labels, supported reason codes, evidence availability settings, substantive-content thresholds, confidence settings, safeguards, and reporting settings. Added typed company-relevance models, config validation, and transactional repository helpers for creating/finalizing runs and storing/retrieving classification history.

### Key Findings

- Historical classifications are preserved: each company can have one classification per run, and latest retrieval is indexed separately from run history.
- Supported labels are fixed to `KEEP`, `EXCLUDE`, and `UNKNOWN`.
- Supported reason codes are centralized and validated before persistence.
- No KEEP / EXCLUDE / UNKNOWN decision rules, company aggregation, pilot classification, OpenAI calls, or downstream latest-KEEP selection were implemented.

### Current Status

Milestone 5.1 was completed experimentally but is no longer the active Milestone 5 direction. The historical SQLite tables remain for reproducibility; the Python classifier/config runtime has been removed.

### Next Recommended Milestone

Superseded. Current direction is company-level OpenAI calibration before deterministic classifier design.

## 2026-07-28 — Milestone 5.2: Deterministic Company AI Relevance Classifier

### Date

2026-07-28

### Status

Deprecated on 2026-08-25 by Milestone 5.0. This classifier was experimental and not empirically calibrated.

### Ticket / Milestone

Milestone 5.2 — Deterministic Company AI Relevance Classifier

### Objective

Implement a conservative database-independent classifier that assigns `KEEP`, `EXCLUDE`, or `UNKNOWN` for one pre-assembled company evidence object.

### Implementation Summary

Added `CompanyRelevanceClassifier` with the ordered cascade: evidence availability, explicit AI evidence, AI-adjacent evidence, strong/repeated technology or innovation evidence, confident exclusion, and conservative fallback. Added typed classifier evidence, supporting-page evidence, rule-evaluation, and decision models with auditable JSON payloads and version propagation. Extended `config/company_relevance.yaml` with cascade thresholds and rule-specific confidence defaults.

### Key Findings

- `UNKNOWN` is reserved for critical evidence reliability failures; missing optional metadata is audited but does not block classification.
- `EXCLUDE` requires explicit conventional-business evidence, reliable attribution, low positive evidence, and no technical conflicts.
- Explicit AI, AI-adjacent, and strong technology/innovation `KEEP` decisions require traceable supporting page evidence.
- Ambiguous but assessable companies conservatively fall back to `KEEP` with `ambiguous_keep`.
- No corpus runner, evidence aggregation from SQLite, persistence of decisions, OpenAI calls, page scoring changes, or retention logic were implemented.

### Current Status

Milestone 5.2 was completed experimentally but is no longer active. The deterministic decision cascade, thresholds, and conventional-business keyword rules are superseded and must not be used for production exclusion decisions.

### Next Recommended Milestone

Superseded. Current direction is company-level OpenAI calibration before deterministic classifier design.

## 2026-07-28 — Milestone 5.3: Company Evidence Aggregation, Classification Runner, and Latest-KEEP Selection

### Date

2026-07-28

### Status

Deprecated on 2026-08-25 by Milestone 5.0. Runtime paths that could classify real companies or export latest KEEP labels have been removed.

### Ticket / Milestone

Milestone 5.3 — Company Evidence Aggregation, Classification Runner, and Latest-KEEP Selection

### Objective

Operationalize company-level relevance triage by assembling company evidence from project tables, running the existing deterministic classifier, persisting versioned decisions, and adding safe latest-label export.

### Implementation Summary

Added a database-backed `CompanyEvidenceAggregator` that builds `CompanyRelevanceEvidence` from firm, domain, acquisition/extraction, feature, and page-score tables while keeping the classifier database-independent. Added `python -m ai_ecosystem.company_relevance.run_classification` with explicit scoring-run support, company/domain/sample filters, deterministic company ordering, force refresh, reused-decision persistence, progress output, and lightweight reports under `data/interim/company_relevance/<run_id>/`. Added latest-label repository methods and `python -m ai_ecosystem.company_relevance.export_latest` for provenance-preserving latest KEEP/EXCLUDE/UNKNOWN exports.

### Key Decisions

- The runner records one new relevance run per invocation; every selected company gets either a classification row or an operational failure record.
- Equivalent prior decisions are copied into the new run with `reused_from_classification_id`; history is preserved and old rows are never deleted.
- Latest-label selection ignores incomplete or failed runs and only considers `completed` and `completed_with_failures` runs.
- Scoring context is recorded with extraction version, scoring profile version, knowledge-base versions, configuration hash, scoring run metadata, and coverage diagnostics.
- Shared-domain attribution is conservative: unresolved shared domains do not receive page evidence as company evidence.
- Operational failures are reported separately and are not converted into evidence-based `UNKNOWN`.

### Current Status

Milestone 5.3 was implemented experimentally but is no longer active. It was not run as a production decision system, and its latest-KEEP selection must not be used downstream.

### Next Recommended Milestone

Superseded. Current direction is company-level OpenAI calibration before deterministic classifier design.

## 2026-08-25 — Milestone 5.0: Audit and Deprecate Superseded Company Relevance Classifier

### Date

2026-08-25

### Ticket / Milestone

Milestone 5.0 — Audit and Deprecate Superseded Company Relevance Classifier

### Objective

Deprecate the earlier deterministic company relevance classifier direction and make Milestone 5 calibration-first.

### Implementation Summary

Audited and removed the active Python runtime for the old deterministic company relevance classifier, including the aggregator, classifier, config loader, models, repository helpers, classification runner, latest-KEEP export, obsolete threshold config, and tests that asserted the old behavior as current. Retained the historical `company_relevance_runs` and `company_relevance_classifications` SQLite tables in schema and idempotent initialization code for reproducibility and existing database readability.

### Key Findings

- The old classifier was not empirically calibrated.
- Its score thresholds assumed a bounded score scale that does not match the current unbounded lexical page scores.
- The keyword-based conventional-business rules were unsafe for production exclusion decisions.
- The old runner could accidentally classify real companies.
- The old latest-KEEP export could feed experimental or stale labels into downstream processing.

### Current Status

The repository is now calibration-first for Milestone 5. No active default workflow can run deterministic company classification or export latest KEEP labels from the deprecated classifier.

### Next Recommended Milestone

Milestone 5.1 — pilot company evidence-package generation for OpenAI calibration.

## 2026-08-25 — Milestone 5.1: Blind Pilot Company Calibration Evidence Packages

### Objective

Build one company-level, scorer-blind calibration evidence package for every company mapped to `pilot_v0_2`.

### Implementation Summary

Added `ai_ecosystem.calibration.build_company_evidence_packages` and versioned `config/calibration.yaml`. The builder writes separate `blind_inputs/` Markdown and JSON evidence and `analysis_metadata/` JSON under `data/interim/calibration/company_evidence/`. Blind packages contain neutral company/domain metadata, acquisition/extraction availability, and all usable extracted pages in deterministic `scraped_page_id` order. They never include scorer fields, score-derived ordering, or scorer explainability; the builder validates that separation before output. Analysis metadata preserves one coherent scoring run for post-hoc comparison only.

### Methodology

Shared domains produce a separate package for every mapped company and preserve attribution uncertainty. The default 50,000-token cap uses deterministic page-order truncation only: complete pages, then a partial page if needed, then metadata-only records. No page is selected, ranked, or excluded by score, heuristic, or retention state. See `docs/calibration_methodology.md`.

### Current Status

Real-pilot verification produced 149 company packages across 100 domains, including 59 shared-domain companies. The blind inputs contain an estimated 536,109 tokens in total; none exceeded the 50,000-token per-company cap. Twenty-six companies have no usable extracted content and 29 have incomplete coverage in the preserved scoring run; these conditions are explicitly recorded rather than hidden or repaired from another run.

No OpenAI call, AI-relevance classification, threshold tuning, or full-corpus workflow has been added. The next step is a versioned company-level calibration prompt and API runner using only `blind_inputs/`.

## 2026-08-25 — Milestone 5.2: OpenAI Company-Level Calibration Runner and Lexical Signal Discovery

### Objective

Run a scorer-blind, company-level OpenAI calibration experiment over the existing `pilot_v0_2` evidence packages and preserve grounded lexical signal candidates for later analysis.

### Implementation Summary

Added the immutable versioned prompt `company_ai_calibration_v1`, calibration-specific `calibration_runs` and `calibration_results` storage, and `python -m ai_ecosystem.calibration.run_company_calibration`. The runner sends only validated `blind_inputs` Markdown to the OpenAI Responses API with strict JSON Schema and no tools. It validates scoring blindness before every request, records model/prompt/version/cost/usage metadata, retries transient errors, persists raw structured results, verifies literal quotes and lexical contexts where practical, writes resumable artifacts, and exports `lexical_signals.csv`. Batch API support remains deliberately deferred.

### Smoke Verification

Ran a mixed 10-company scorer-blind smoke plus a non-shared machine-learning positive-control package. No-content packages were `UNKNOWN`; shared/group-domain evidence remained `UNKNOWN` when legal-entity attribution was unresolved; independently documented conventional businesses could become `EXCLUDE`; and explicit AI/group evidence did not produce `KEEP` without focal-company attribution. The prompt was unchanged after smoke review.

### Full Pilot Result

Completed calibration run `calibration_20260825T141041Z_ee153c3e` using `gpt-5.6-luna`, low reasoning effort, and prompt `company_ai_calibration` v1. All 149 pilot companies completed with 1 `KEEP`, 99 `UNKNOWN`, 49 `EXCLUDE`, and 0 failures. Usage was 924,817 input tokens, 176,930 output tokens, 11,999 reasoning tokens, and 1,101,747 total tokens; estimated cost was USD 0.397279. The completed run was resumed successfully with no additional API requests.

### Lexical Observations

The run collected 619 observational lexical candidates: 3 positive-AI, 107 AI-adjacent, 110 false-positive, 59 third-party-AI, 90 generic-marketing, 179 conventional-activity, and 71 useful-context-pattern candidates. Literal phrase grounding succeeded for 539 candidates; only 189 contexts matched literally, so unverified contexts must be treated as model suggestions rather than direct extraction facts. No candidate has changed a scoring dictionary or knowledge base.

### Current Status

Milestone 5.2 is complete. The next milestone is post-hoc calibration analysis comparing independent company labels with the separately preserved page-scoring metadata. It must not yet tune thresholds or modify scoring dictionaries.

## 2026-08-25 — Milestone 5.3: ÖNACE-Based AI-Enriched Calibration Sample

### Objective

Construct a reproducible, company-level calibration sample enriched for
technology and AI-adjacent activity while retaining conventional controls. The
sample supports later web-pipeline and scorer-blind OpenAI calibration work; it
does not estimate Austrian AI prevalence.

### Implementation Summary

Added `config/calibration_enrichment.yaml` with a versioned, exact-code ÖNACE
2008 taxonomy sourced from Statistik Austria, fixed seed `20260825`, and target
allocation of 300 `HIGH_POTENTIAL`, 125 `AI_ADJACENT`, and 75
`CONVENTIONAL_CONTROL` companies. The new
`python -m ai_ecosystem.calibration.build_enrichment_sample` command performs
only deterministic source-data sampling. It does not invoke discovery,
acquisition, extraction, scoring, calibration, or OpenAI.

Generic sample storage now has `sample_companies` for exact legal-company
membership. `sample_domains` remains the deduplicated domain projection required
by existing discovery commands. Company-level consumers prefer explicit company
membership, which prevents shared domains from expanding a selected firm sample
to unrelated firms.

### Real Sample

Built `ai_enriched_v1` with 500 companies and 498 unique domains: 300 high
potential, 125 AI-adjacent, and 75 conventional controls. The sample covers 49
ÖNACE codes, has zero overlap with `pilot_v0_2`, and includes 120 companies on
shared/group domains. No firm-size field is available in the imported source, so
firm size was not used for stratification. The ignored audit artifacts are:

- `data/interim/ai_enriched_calibration_v1.csv`
- `data/interim/ai_enriched_calibration_v1_audit.json`

This remains an explicitly enriched calibration sample. Its later label mix is
not an Austrian AI-prevalence estimate.

### Current Status

Milestone 5.3 is complete. Subsequent operational runs discovered pages for
`ai_enriched_v1` and acquired 54,511 successful HTML pages; extraction was
still intentionally pending when this entry was superseded by Milestone 5.4a.
The discovery command used was:

```bash
.venv/bin/python -m ai_ecosystem.discovery.homepage_discovery --sample-id ai_enriched_v1
```

## 2026-08-30 — Milestone 5.4a: Domain-Sample-Scoped Extraction

### Objective

Prevent the generic enrichment sample from accidentally selecting every
successfully acquired page in the database during extraction.

### Implementation Summary

`ai_ecosystem.extraction.run_extraction` now accepts `--sample-id` for a
registered generic domain sample such as `ai_enriched_v1`. It selects only
successful acquired pages whose source domain occurs in that sample's
`sample_domains` projection. The pre-existing `--sample` option remains a
separate legacy page-level validation-sample filter. When combined with
`--sample`, `--domain`, or `--page-limit`, all filters intersect; unknown
generic sample IDs fail clearly before an extraction run is created.

No extraction behavior, Trafilatura settings, evidence-package format,
features, scoring, acquisition, or discovery behavior changed. No extraction
run was started as part of this implementation.

### Next Step

Run the acquired `ai_enriched_v1` pages only:

```bash
.venv/bin/python -m ai_ecosystem.extraction.run_extraction --sample-id ai_enriched_v1
```

## 2026-08-30 — Extraction Robustness: Malformed Link URLs

Malformed individual HTML link targets are now discarded during link
normalization rather than aborting page or corpus extraction. In particular,
telephone-like values parsed as invalid URL ports are skipped. Valid HTTP(S)
links continue to be normalized and persisted unchanged; Trafilatura, evidence
packages, page features, scoring, acquisition, and sample filtering are
unaffected. No production extraction run was started for this fix.

## 2026-08-30 — Milestone 5.5a: Structural Blind-Package Ordering

Blind calibration package content now uses deterministic structural ordering:
root URL, then a deliberately small set of exact About/Company path components,
then shallowest remaining normalized URL paths, with `scraped_page_id` as the
tie-breaker. The existing 50,000-token estimated budget, full/partial/metadata
page treatment, package schema, shared-domain handling, scoring separation, and
scorer blindness are unchanged. No scoring or OpenAI call was run.

Rebuilt `ai_enriched_v1`: 500 company packages across 498 domains, with 353
companies containing usable extracted text and 147 without. The blind inputs
total 9,430,650 estimated tokens; 10,865 pages are complete, 45 are partially
truncated, and 44,999 are metadata-only. Forty-two packages exceed the nominal
50,000-token estimate because their metadata-only page lists alone exceed that
budget. This is documented as an existing package-format limitation, not altered
by the structural-ordering change.

## 2026-08-30 — Milestone 5.5b: Omit Metadata-Only Blocks From Model Markdown

Blind JSON packages continue to retain every usable extracted page, including
metadata-only records, for reproducibility and audit. The model-facing Markdown
now renders only complete and partially truncated pages, with a compact page
inclusion summary reporting all usable, full, partial, and omitted metadata-only
counts. The allocator measures that same Markdown while assigning the existing
50,000-token budget, so omitted metadata cannot consume text capacity. No
scoring, extraction, page selection, prompt, or OpenAI behavior changed.

Rebuilt `ai_enriched_v1` without API calls: 500 company packages across 498
domains, including 353 with usable extracted content and 147 without. Model
inputs total 7,753,662 estimated tokens; median input is 5,557.5 tokens and the
maximum is exactly 50,000, with zero packages above the budget. The JSON retains
17,511 complete, 69 partial, and 38,329 metadata-only page records. Previously
blocked large domains now receive structural evidence: `evgroup.com` has 158
complete plus one partial page, `tridonic.com` 106 plus one, and `humer.com` 144
plus one; each model Markdown is exactly 50,000 estimated tokens. No OpenAI
calibration run has been started.

## 2026-08-30 — Generic Domain-Sample-Scoped Scoring

`ai_ecosystem.scoring.run_scoring` now accepts `--sample-id` for registered
generic domain samples such as `ai_enriched_v1`. It selects successful extracted
pages only when their source domain is present in that sample's `sample_domains`
projection. The pre-existing `--sample` option remains limited to legacy
page-level validation samples. When both sample forms, `--domain`, or
`--page-limit` are supplied, they intersect; an unknown generic sample ID fails
before a scoring run is created. No real scoring run was started for this change.

## 2026-08-31 — Milestone 5.6: Company Calibration Analysis Notebook

### Objective

Create the main reproducible empirical artifact for comparing deterministic
page scores with independent blind OpenAI company labels before designing a new
company-level relevance method.

### Implementation Summary

Added the output-free source notebook
`notebooks/06_company_calibration_analysis.ipynb` and an optional `analysis`
dependency group in `pyproject.toml`. The notebook pins and validates:

- enriched calibration run `calibration_20260830T181238Z_3cf58aed`;
- enriched scoring run `scoring_20260830T185247Z_839a147c`;
- broad-pilot calibration run `calibration_20260825T141041Z_ee153c3e`;
- broad-pilot scoring run `scoring_20260715T131248Z_f305ef18`.

It assembles read-only company, blind-package, lexical-signal, page-score,
extraction, and sample data. Analyses cover raw/content-unique and all-page/model-
visible score aggregations, label distributions, dimension relationships,
website-size bias, literal lexical grounding, high-scoring non-KEEP and
low-scoring KEEP casebooks, precision-recall diagnostics, fixed downstream
budgets, enrichment strata, shared domains, cross-sample behavior, and
robustness checks with domain-cluster bootstrap intervals.

Executed outputs are ignored under
`data/interim/calibration_analysis/company_calibration_analysis_v1_3cf58aed_839a147c/`.
The run produced an executed notebook, 12 figures, 26 analytical CSV tables,
Parquet caches with source-manifest validation, and
`decision_support_summary.json`.

### Current Findings

The enriched labels remain 103 KEEP, 253 UNKNOWN, and 144 EXCLUDE. Deterministic
scores are available for 353 of 500 enriched companies; 147 have no compatible
page-score coverage. The broad pilot has compatible scores for 123 of 149
companies. Only 358 of 2,733 OpenAI lexical suggestions were found literally in
the exact model-visible page text, so ungrounded suggestions are not treated as
observed vocabulary.

The strongest apparent enriched-sample feature is the content-unique mean of
technology and innovation top-three page scores (average precision 0.667; ROC
AUC 0.847 as a secondary diagnostic). At fixed downstream budgets it selected:

- 25 companies: 20 KEEP, 5 UNKNOWN, 0 EXCLUDE;
- 50 companies: 36 KEEP, 11 UNKNOWN, 3 EXCLUDE;
- 100 companies: 62 KEEP, 29 UNKNOWN, 9 EXCLUDE;
- 125 companies: 77 KEEP, 35 UNKNOWN, 13 EXCLUDE.

These are in-sample calibration results, not approved thresholds. Page count is
strongly associated with top-three score summaries (Spearman rho approximately
0.54 to 0.62 across dimensions), confirming that website-size sensitivity must
be addressed in any later company-level method. The enriched KEEP share must
not be interpreted as Austrian AI prevalence.

### Verification And Next Step

The notebook executed end to end with no cell errors while leaving the source
notebook output-free. `python -m unittest discover -s tests` passes 154 tests and
`python -m compileall src tests` succeeds. No scorer, knowledge base, OpenAI
label, retention rule, or production data was modified. Next, review the
casebooks and calibration diagnostics, freeze candidate scorer-v2/company-level
features, and validate them on a future independent sample before operational
use.

## 2026-09-01 — Milestone 5.6a: Correct Lexical-Signal Grounding

The calibration notebook's historical lexical resolver now handles the
ambiguous `page_id` field against both `scraped_page_id` and
`extracted_page_id`, using an unambiguous supplied URL as the strongest
conflict-resolution signal. Resolution provenance, ID namespace, ID/URL
conflicts, model visibility, and strict versus normalized phrase/context
grounding remain explicit in the analytical data. Normalization is limited to
Unicode, case, punctuation/hyphen, and whitespace representation differences;
no semantic similarity is used.

The notebook was re-executed without rerunning OpenAI or modifying labels,
scores, knowledge bases, evidence packages, or production data. Of 2,733
lexical suggestions, 2,731 resolve to a model-visible page and two remain
unresolved. There are 422 explicit ID/URL conflicts. Phrase grounding is 79.4%
strict and 81.5% normalized; context grounding is 25.0% strict and 42.2%
normalized. URL-selected pages contain the strict phrase in 327 conflict cases,
compared with 21 under the historical scraped-ID-first interpretation.

Corrected normalized phrase-grounded signals now feed only the candidate
vocabulary/downweighting review tables. They are sufficiently traceable for
candidate scorer-v2 vocabulary analysis, but remain model-suggested review
items rather than automatic knowledge-base changes. Model-generated context is
substantially more paraphrastic and must not be treated as a verbatim quote
without its own grounding flag. Company-level score features, the strongest
apparent feature, website-size conclusions, and selection-budget operating
points are unchanged. Future calibration schemas should expose one explicit
page identifier and request a clearly named verbatim context field.

Verification: the notebook completed without cell errors, the source notebook
remains output-free, all 160 unit tests pass, and `python -m compileall src
tests` succeeds.

## 2026-09-08 — Milestone 5.8a: Scorer-v2 Annotation Sample Builder

Implemented a deterministic, artifact-only builder for the scorer-v2 page
annotation sample. The versioned configuration pins the completed broad-pilot
and AI-enriched calibration/scoring contexts and seed `20260908`. Selection
uses only existing successful `v1` extractions; it does not invoke discovery,
acquisition, extraction, scoring, model training, or OpenAI.

The frozen sample contains 264 companies (`104 KEEP`, `80 EXCLUDE`, `80
UNKNOWN`) grouped into development, model-selection, and locked-retrieval
splits by domain. Exact content hashes are deduplicated per focal company, with
duplicate group metadata retained. Page allocation combines structural,
coverage-stratified, deterministic-random, prior-calibration, and isolated
scorer-v1 diagnostic pools. The generated audit verifies that scorer-v1-aware
selection remains below 15%, at least 85% remains scorer-v1-independent, and
shared domains never cross splits.

The artifact contains 3,531 focal-company/page annotation items representing
exactly 3,500 distinct scraped pages. It is 88.98% scorer-v1-independent and
78.53% independent of both scorer v1 and prior OpenAI citations. All configured
split-label counts are met, with no page-allocation shortfalls. A byte-for-byte
rebuild check passed, all 167 unit tests pass, and `python -m compileall src
tests` succeeds.

Artifacts are frozen under
`data/interim/calibration/page_annotation_samples/scorer_v2_page_annotation_v1/`.
The next ticket is Milestone 5.8b: construct scorer-blind page inputs and add
the strict Luna Medium annotation schema, prompt, and resumable runner. No
OpenAI annotation has been performed yet.

## 2026-09-08 — Milestone 5.8b: Annotation Contract And Batch Runner

Frozen the versioned `scorer_v2_page_annotation` v1 prompt, strict structured
output schema, and model configuration for `gpt-5.6-luna` with medium reasoning.
Each model input contains only the focal company/page identifiers and names,
domain, URL, title, headings, up to 24,000 Markdown characters, and a truncation
flag. Automated contract validation prevents sample labels, splits, selection
reasons, scorer-v1 diagnostics, and prior calibration decisions from entering
the model-facing payload.

Added an artifact-only page-annotation runner with separate commands to build,
submit, inspect, and ingest OpenAI Batch jobs, plus a small synchronous mode for
debugging and failed-item recovery. Both paths preserve prompt/schema/model
hashes, raw requests and responses, parsed annotations, failures, and token/cost
accounting. Synchronous runs resume successful items; Batch ingestion is
idempotent and maps every response through a stable unique request ID. The Batch
builder adds 5% deterministic hidden repeats with private repeat provenance and
identical model-facing requests.

The complete frozen sample was rendered and validated locally without upload or
submission: 3,531 primary annotation items plus 177 hidden repeats produce 3,708
requests and a 35,160,581-byte Batch JSONL file. Two independent builds produced
the same SHA-256 (`0d1a1c4b983f8104ad94bc28e0737c7540f77ba00222ffbafe30b27b6f951fdc`).
A one-page synchronous live smoke returned the exact `scraped_page_id`, passed
schema parsing, persisted its raw response and usage, and produced eight of eight
supporting phrases that matched the model-visible Markdown exactly. The smoke
used 1,068 input and 479 output tokens (1,547 total); estimated cost was
USD 0.0007884.

Verification: all 173 unit tests pass, `python -m compileall src tests` succeeds,
and `git diff --check` is clean. No bulk Batch job was submitted. Milestone 5.8c
can now build the named production Batch run, submit it, monitor it, and ingest
the completed page annotations.

## 2026-09-08 — Milestone 5.8d: Automated Annotation QA

Implemented and ran deterministic, read-only QA over
`scorer_v2_page_annotation_v1_bulk_20260908`. The analysis verifies artifact
integrity, schema and identifier validity, terminal failures, exact and
normalized phrase grounding, logical consistency, hidden-repeat stability,
output collapse, diagnostic-pool dominance, and distributions across company
label, sampling pool, language, website size, shared-domain status, calibration
source, attribution, and page category.

All 3,708 responses are present, schema-valid, and identifier-valid; there are
no duplicate request/result IDs, missing responses, model-input hash mismatches,
or terminal failures. Among 3,531 primary annotations, 8 (0.23%) contain a hard
utility/priority contradiction. Hidden-repeat results across 177 pairs show
utility weighted kappa 0.922, attribution agreement 88.14%, evidence-facet
strength agreement 86.52%, category agreement 92.09%, and priority agreement
79.66%. No output field collapsed and the 11.02% scorer-v1 diagnostic pool does
not dominate the annotation distribution.

Of 24,383 returned positive phrases including repeats, 95.23% ground exactly
and 97.80% after limited Unicode, case, punctuation, hyphen, and whitespace
normalization. Trust assignment for primary annotations is 3,450 trusted
(97.71%), 73 partially trusted, and 8 untrusted. Every sufficiently populated
key subgroup is at least 94.81% trusted. All agreed QA gates pass.

The annotations are approved to proceed to Milestone 5.8e with explicit field
restrictions: untrusted rows must not provide training targets; partial rows
retain their machine-readable affected facets/checks; and only normalized-
grounded phrases may become lexical supervision. The page-priority tier should
be treated as a secondary target because its hidden-repeat exact agreement
(79.66%) is lower than utility, attribution, category, and facet stability.
Original model annotations were not changed and no scorer was trained.

Final QA artifacts are under
`data/interim/calibration/page_annotation_runs/scorer_v2_page_annotation_v1_bulk_20260908/qa_v2/`.
The run did not contain a standalone YAML config snapshot, but its persisted
config hash matches the current versioned configuration and its prompt, schema,
model settings, model-input hashes, and complete Batch input all verify against
the frozen request artifacts. This provenance gap does not affect annotation
quality but should be corrected before a future annotation campaign.

Verification: all 178 unit tests pass, `python -m compileall src tests`
succeeds, and `git diff --check` is clean.

## 2026-09-08 — Milestone 5.8e: Scorer-v2 Page Training Dataset

Built the deterministic, leakage-safe `scorer_v2_page_training` v1 dataset
from the frozen page sample, completed Luna annotations, authoritative `qa_v2`
trust assignments, model-visible inputs, extraction features, links, duplicate
hashes, and pinned scorer-v1 runs. The builder performs no model fitting,
threshold tuning, OpenAI calls, or scorer changes.

The canonical dataset contains 3,531 unique focal `(company_id,
scraped_page_id)` rows. It exposes 3,523 rows with at least one usable target,
keeps all 73 partially trusted annotations with explicit field-level
availability flags, and nulls every target for the 8 untrusted annotations.
Restricted values are never converted to zero. Lexical supervision contains
22,759 normalized-grounded phrases from non-untrusted primary annotations.

Predictive data is separated into 68 scorer-v1-independent engineered
features, model-visible title/headings/Markdown documents, and an isolated
22-column optional scorer-v1 block. Company labels, sampling pools, selection
reasons, diagnostic flags, and split metadata do not enter the engineered
feature table. The split manifest preserves 2,386 development, 512
model-selection, and 633 locked-retrieval rows and records that text
transformers must be fit on development data only.

No normalized domain or shared-domain cluster crosses a split. Three exact
content hashes occur across frozen splits, affecting six rows; those rows are
preserved with their targets but marked ineligible for modeling so identical
content cannot leak across comparisons. Three broad-pilot pages have no
compatible scorer-v1 rows; only their optional scorer-v1 fields are null, and
their independent features and targets remain available. All extraction and
annotation/QA joins resolved successfully.

Versioned Parquet tables, feature/target definitions, source hashes, verified
annotation config snapshot, data-quality audit, and provenance manifest are
under
`data/interim/calibration/page_training_datasets/scorer_v2_page_training_v1/`.
Verification passes all 182 unit tests and `python -m compileall src tests`.
The dataset is ready for Milestone 5.8f page-model baseline and challenger
experiments; locked-retrieval outcomes remain reserved from model selection.

## 2026-09-09 — Milestone 5.8f: Page Baseline And Challenger Experiments

Completed a controlled comparison of seven page-ranking candidates using only
the frozen 5.8e development and model-selection rows. Domain-grouped
cross-validation on development data selected model hyperparameters; the
model-selection split was used for architecture comparison. Locked-retrieval
targets were filtered at Parquet read time and were never loaded into the
experiment frame.

The scorer-v1-independent text/engineered ridge hybrid produced the best
overall calibration and correlation (`MAE=0.621`, `Spearman=0.691`), while the
word/character TF-IDF ridge produced the strongest within-company ranking
(`pairwise=0.859`, `NDCG@5=0.948`) and the best aggregate facet and noise
predictions. A compact engineered-feature histogram gradient booster remains a
speed-oriented challenger. Scorer v1 alone was substantially weaker, and
adding its isolated feature block to the independent hybrid slightly worsened
the comparison result; scorer-v1 features are therefore not included in the
provisional shortlist.

All candidates emit continuous utility scores plus attribution, evidence-facet,
and noise predictions. Full-output inference benchmarks, including feature
transformation and all auxiliary predictions, exceed the target of 30 pages per
second and project well below one hour for 55,000 pages. The text-aware models
process roughly 278–300 pages per second; measured process peak memory remained
below 0.8 GB. A multilingual embedding rescue was not justified at this stage.

The model-selection pool is enriched, with 69.1% utility-2/3 pages, so these
results do not estimate website-wide retrieval quality. Attribution remains the
weakest task (`best macro-F1=0.302`), and explicit-AI, third-party,
template/duplicate, and non-substantive-listing detection require caution. Each
major scorer-independent sampling pool still contains at least 20 low-utility
pages, so this comparison alone does not justify another hard-negative
annotation pass.

The provisional 5.8g shortlist is `hybrid_independent_ridge`, `tfidf_ridge`,
and `engineered_hist_gradient_boosting`. This is not a frozen production
candidate set. True retrieval evaluation remains deferred to Milestone 5.8g.
Versioned models, metrics, diagnostics, subgroup tables, ablations, runtime
profiles, coefficients, report, and shortlist are under
`data/interim/calibration/page_model_experiments/scorer_v2_page_model_comparison_v1/`.
Verification passes all 186 unit tests and `python -m compileall src tests`.

## 2026-09-09 — Milestone 5.8f+: Page-Model Attribution Improvement

Completed a focused improvement study for the three provisional page-model
families using only 2,383 development rows and 511 modeling-eligible
model-selection rows. Locked-retrieval targets were filtered before loading;
the generated predictions exactly match the eligible model-selection IDs and
have no overlap with locked-retrieval IDs.

The input audit confirmed that Luna saw focal company name/domain and page URL,
while the original local models did not model focal-company-to-page
relationships. Added 30 deterministic, scorer-v1-independent relational
features covering company-name and domain overlap across title, headings, body,
path, and host, plus exact-domain, subdomain, and cross-host relationships.
Opaque company/page identifiers remain non-predictive keys.

A dedicated engineered-and-relational attribution model improved attribution
macro-F1 from 0.302 to 0.397 (`+0.094`) and exceeded the best attribution model
sharing a utility representation by `+0.054`. F1 improved for direct focal
company (`+0.046`), probable focal company (`+0.176`), third-party (`+0.204`),
and unrelated (`+0.063`) pages; ambiguous/group attribution declined slightly
(`-0.017`) and remains weak. Gains were substantial for shared-domain
(`+0.144` macro-F1) and cross-host (`+0.094`) pages. Binary focal-versus-
nonfocal F1 is 0.942. Attribution is therefore suitable as an auxiliary soft
signal for locked retrieval evaluation, but not as a hard five-class filter.

Relational utility variants preserved or improved ranking. Relative to their
fixed baselines, the TF-IDF ridge improved MAE by 0.043, Spearman by 0.034,
pairwise accuracy by 0.011, and NDCG@5 by 0.003; the hybrid improved MAE by
0.029 and NDCG@5 by 0.013 with effectively unchanged Spearman; the compact tree
improved MAE by 0.032, Spearman by 0.032, and NDCG@5 by 0.006. Attribution-based
score adjustment produced only a small best selection-score gain (`+0.002`),
so raw utility ranking remains the primary design and adjustment remains an
optional locked-test variant. Facet/noise trade-offs are retained in the
comparison tables rather than hidden by the aggregate recommendation.

All full-output candidates exceed the 30 pages/second target and project below
one hour for 55,000 pages; the dedicated attribution model processes roughly
2,400 pages/second with an estimated 1.7 MB model/matrix working set. The
provisional utility candidates for 5.8g are
`tfidf_relational_ridge_attribution_adjusted`, `tfidf_relational_ridge`,
`hybrid_relational_ridge`, and `engineered_relational_hgb`. The provisional
attribution candidates are `dedicated_engineered_relational_hgb` and
`dedicated_hybrid_relational_sgd`. No candidate has been frozen.

Versioned models, exact input/feature audits, utility and attribution metrics,
per-class and subgroup deltas, runtime/memory benchmarks, predictions, report,
and provisional shortlist are under
`data/interim/calibration/page_model_improvements/scorer_v2_page_model_improvement_v1/`.
All 194 unit tests pass, `python -m compileall src tests` succeeds, artifact
hashes verify, and `git diff --check` is clean. No scorer-v1 features,
embeddings, OpenAI calls, annotation changes, company-model work, or locked
retrieval evaluation were performed.

## 2026-09-11 — Milestone 5.8g: Full Locked Retrieval Evaluation

Annotated all 7,318 eligible exact-content-deduplicated pages from the 40
locked companies with the frozen GPT-5.6 Luna Medium contract, plus 366 hidden
repeats. All 7,684 responses are schema-valid with exact identifiers after six
targeted retries. Estimated total annotation cost was USD 4.448. Automated QA
found 94.33% trusted primary annotations, 97.02% normalized phrase grounding,
utility weighted kappa 0.892, and facet agreement 84.31%. Hidden-repeat
attribution agreement was 83.33%, narrowly below its 85% gate, so attribution
metrics are descriptive and excluded from the ranker recommendation.

Frozen-model evaluation used every eligible page and performed no fitting or
tuning on locked outcomes. `tfidf_relational_ridge` and its attribution-adjusted
variant were effectively tied and led true retrieval. The adjusted variant
reached P@5/P@10/P@20 of 0.780/0.743/0.700 and NDCG@5/@10/@20 of
0.784/0.790/0.796. At k=20 it captured 1.58 more utility points per company
than scorer v1 and 3.58 more than the structural baseline. The attribution
adjustment itself yielded only a negligible advantage over raw TF-IDF, so both
proceed as candidates; `engineered_relational_hgb` remains a lower-ranking,
lower-noise challenger.

The frozen diversity-aware post-ranker materially improved limited-token
packages. For raw TF-IDF at 16k tokens, utility capture rose from 55.75% to
58.04%, category diversity improved, and mean lexical redundancy fell from
0.138 to 0.119. Gains persisted at 8k, 32k, and 50k budgets, while facet
coverage remained high. A lightweight diversity/redundancy-aware set-selection
layer is therefore warranted; pure top-k alone is not sufficient for the final
evidence-package objective. No scorer or selector was frozen automatically,
and company AI-promise modeling has not begun.

Versioned annotation, QA, predictions, company/candidate ranking metrics,
budget metrics, selected-page traces, and the comparison report are under
`data/interim/calibration/page_annotation_runs/scorer_v2_locked_retrieval_v1_bulk_20260910/`
and
`data/interim/calibration/locked_retrieval_evaluation/scorer_v2_locked_retrieval_evaluation_v1/`.
All 202 unit tests pass, `python -m compileall src tests` succeeds, and
`git diff --check` is clean.

## 2026-09-12: Milestone 5 Pivot to Company AI-Promise Modeling

Recorded the methodological pivot following company calibration, scorer-v1
analysis, page annotation/QA, training dataset construction, baseline/challenger
experiments, relational attribution improvements, and locked 5.8g evaluation.
The detailed findings, artifact statuses, and new sequence are in
`docs/calibration_methodology.md`.

Broad utility ranking worked reasonably well, and TF-IDF relational candidates
led the tested page rankers. Token-budget diagnostics nevertheless showed that
useful pages were common, random selection packed more short pages, and broad
utility capture did not establish AI-specific evidence retrieval. Diversity
selection helped packages but did not resolve the company-screening objective.

The primary objective is now to identify companies with promising, attributed AI
or AI-adjacent evidence for final LLM analysis. Existing calibration provides
649 companies (104 KEEP, 193 EXCLUDE, 352 UNKNOWN) for initial development;
UNKNOWN must not be treated as negative. Final AI-active labels, roles,
techniques, and supporting evidence remain the final LLM mapping task.

Scorer v1 remains an uncalibrated historical baseline. Page models and selectors
remain provisional development artifacts, not production decisions. All corpus,
calibration, QA, datasets, models, and reports are retained with their original
provenance and limitations. Earlier deprecated company rules are not reactivated.

Next: build a leakage-safe company dataset and evaluation protocol, then compare
company models and independently validate false exclusions before revisiting
AI-task-specific package selection. Population scaling and network work remain
later. This checkpoint changes documentation only; no new calibration, model
training, or pipeline execution was performed.

## 2026-09-16: Company AI Relevance Modeling Dataset

- Implemented the read-only, versioned builder and separate source, grouping,
  feature, and fold-preprocessing helpers. Configuration:
  `config/company_modeling_dataset.yaml`.
- Output: `data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1/`.
  Canonical Parquet tables separate shared text, company/page relationships,
  deterministic features, original targets, operational states, supervision,
  leakage groups and provenance. Existing output cannot be overwritten.
- Reconciled 649 companies: 104 KEEP, 193 EXCLUDE, 352 UNKNOWN. There are 476
  PROCESSABLE and 173 NO_EVIDENCE companies. No missing evidence was labeled
  negative; no required-input errors or unresolved company joins remain.
- Loaded 55,568 successful v1 pages, deduplicated to 41,971 normalized content
  items. Quarantined 95 overlapping/generic items from predictive inputs only;
  canonical content is preserved. Palfinger domains share one leakage group.
- Created 76 deterministic numeric features and retained full text references.
  Re-grounded 2,733 company-calibration signals; 2,205 are eligible for later
  training-fold phrase vocabulary construction. No real vocabularies were fit.
- Split 597 leakage groups into 389 development, 130 model-selection and 130
  internal-holdout companies, with five grouped development folds. Exact
  predictive content and shared domains do not cross groups/splits. Near-duplicate
  candidate discovery is approximate and explicitly audited.
- Verification: 215 tests pass, including 13 new dataset/preprocessing tests;
  `compileall` and whitespace checks pass. An independent rebuild matched all
  16 canonical table hashes, the source hash, and implementation hashes. The
  frozen build took 158 seconds, about 1.43 GB peak memory and 75 MB storage.
  Exact timings and hashes are in the manifest.
- Source SQLite, scorer-v1, annotations, and production behavior were unchanged.
  No OpenAI calls, real model training, or population processing was performed.
- Handoff and limitations: see
  [Company Modeling Dataset v1](calibration_methodology.md#2026-09-16-company-modeling-dataset-v1).
  Next: company-model experiments with fold-local preprocessing; UNKNOWN remains
  distinct from EXCLUDE. This historically explored internal holdout is not an
  independent population validation sample.

## 2026-09-16: Milestone 5.3 - First Company AI-Relevance Model Comparison

- Added a separate versioned experiment runner, fold-local feature adapters,
  metric/budget reporting and tests. The frozen 5.2 dataset remains unchanged.
- Completed 125 development CV fits and eight model-selection fits. UNKNOWN
  and NO_EVIDENCE were never negative supervised examples. All five original
  grouped folds and the held-out split were preserved.
- The predeclared selection rule chose `logistic_size` (mean-pooled text plus
  structural/relational, lexical and size context; C=10). Model-selection AP:
  0.818. Similar AP among the strongest alternatives prevents a strong claim
  that one architecture is generally superior.
- Froze the winner, refit on 83 KEEP / 155 EXCLUDE development+selection rows,
  then performed one internal-holdout evaluation: AP 0.860; 27/96 processable
  firms selected captures 19/21 KEEP, alongside five UNKNOWN and three EXCLUDE.
  Thirty-four NO_EVIDENCE firms remained unscored. No post-holdout tuning.
- Reports, predictions, bootstrap intervals, cost-ready operating points,
  subgroup/error analysis, vocabularies, timings and the fitted winner are under
  `data/interim/calibration/company_model_experiments/company_ai_relevance_comparison_v1/`.
  Comparison runtime: 299 seconds, peak memory 1.41 GB; final preprocessing
  66.5 seconds, peak memory 1.62 GB. Text preprocessing dominates fitting cost.
- A final audit corrected inherited candidate names in baseline error-example
  rows using saved predictions only. Original source snapshots, correction hashes,
  metrics and the unchanged model-selection freeze are preserved.
- Verification: 228 tests pass, including 13 new experiment tests covering the
  synthetic end-to-end comparison/resume/holdout gate and reporting correction;
  compilation and whitespace checks pass; source-dataset hashes verified. No OpenAI,
  new labels, scorer-v1/page-model changes, population inference, production
  threshold, or automatic exclusion was introduced.
- See the methodology's first-company-model-comparison section and the generated
  `comparison_report.md` for limitations and operating-point interpretation.

## 2026-09-16: Milestone 5.3a - Focused Company AI-Relevance Tuning

- Added an isolated, resumable tuning runner and versioned bilingual lexicon.
  All 649 existing companies are now development evidence, including the opened
  former internal holdout. The frozen dataset, old experiment, original model,
  source database, scorer-v1 and page models remain unchanged.
- Reconciled 104 KEEP / 193 EXCLUDE / 352 UNKNOWN; 476 PROCESSABLE and 173
  NO_EVIDENCE. Preserved domain/content leakage groups across two repetitions
  of five outer folds and three inner folds. UNKNOWN never supplied negative
  supervision; NO_EVIDENCE remained unscored.
- Completed all eight candidates: 600 inner fits, 80 outer refits and 40
  fold-local preprocessing/vocabulary comparisons. References reproduced the
  original logistic-size and lexical specifications under the same new folds.
  Top-three pooling retains zero-aware per-coordinate averaging as planned.
- AP remains primary. Inner folds chose regularization and the operational-F1
  operating fraction; outer folds evaluated those choices unchanged. Selected
  KEEP is desired, UNKNOWN unresolved but costly, and EXCLUDE negative under
  current calibration. Every operational curve retains this composition.
- `lexical_grounded_v2` had the highest mean AP (0.9242 versus reference 0.9117),
  but lower operational F1 (0.7037 versus 0.7311) and KEEP capture (0.7740 versus
  0.8219). Mean selected shares were 26.15% versus 27.21%. Mean per-fold selected
  KEEP/UNKNOWN/EXCLUDE counts were 16.1/7.0/1.8 versus 17.1/6.4/2.4.
  No challenger passed the predeclared improvement gates; retain the frozen
  reference and keep the grounded variant as a challenger, not a replacement.
- UNKNOWN supervision changed at least 20% of grounded vocabulary in 39/40
  training partitions. All-annotation vocabularies contained 84-188 terms;
  KEEP/EXCLUDE-only versions contained 55-122. This is diagnostic evidence of
  different vocabulary composition, not proof of noise and not a ninth model.
- Reports, operating curves, paired uncertainty, coefficients, source/size
  diagnostics, visibility conflicts and progress records are under
  `data/interim/calibration/company_model_experiments/company_ai_relevance_tuning_v2/`.
  Runtime: 2,465.5 seconds (41.1 minutes), peak memory 2.42 GB, artifacts 1.91 GB.
  No recorded runtime failures. A real benchmark pause/resume reused completed
  checkpoints; completed-run resume verified artifacts without refitting.
- Verification: 244 tests pass, including 16 tuning tests covering reference
  prediction parity, nested isolation, vocabulary provenance, operational
  semantics and interruption recovery. Compilation and whitespace checks pass.
  Source and artifact hashes, 10,384 outer prediction rows, full company
  membership, unscored NO_EVIDENCE and all 680 fit checkpoints were audited.
- Next: independent company validation planning with new companies and broader
  website evidence. These enriched-cohort CV results are development evidence,
  not population performance or production operating points. No OpenAI calls,
  relabeling, deployment-model fitting, production exclusions or population
  processing were performed.

## 2026-09-17: Milestone 5.3b - Grounded Cleanup and Final Company-Scorer Freeze

- Completed the single bounded `company_ai_relevance_cleanup_v3` comparison:
  explicit technical unigrams, generic-fragment filtering, cross-channel phrase
  canonicalization, training-only constant/vector deduplication, consistent
  bootstrap selection counts and stricter source/config/cache validation.
  The expanded fixed lexical block and historical artifacts remain unchanged.
- Preserved all 649 companies, 597 leakage groups and the two-repetition,
  five-outer/three-inner schedule. Executed 210 inner fits, 30 outer fits and two
  final fits. Imported 40 validated preprocessing partitions, not old model fits.
  UNKNOWN never supplied negative supervision; 173 NO_EVIDENCE firms remain
  unscored. Final supervised fits used 104 KEEP and 193 EXCLUDE companies.
- Mean AP / operational F1: reference 0.9117 / 0.7311; cleaned lexical
  0.9175 / 0.6726; cleaned combined 0.9214 / 0.6969. Combined's AP improvement
  interval includes zero, and neither challenger passed the frozen improvement
  gates. Mean selected KEEP/UNKNOWN/EXCLUDE counts per fold were 17.1/6.4/2.4
  for reference and 15.5/6.3/1.9 for combined.
- Froze two fitted development bundles: primary `reference_logistic_size`
  (C=10) and challenger `cleaned_grounded_combined` (C=1). Final regularization
  came from saved inner CV, not outer outcomes. No production threshold was
  exported. These are awaiting independent validation, not validated winners.
- Grounded features fell from 84-188 historical channel columns to 37-98
  filtered phrases and 35-85 final columns. UNKNOWN still materially changes
  vocabulary in 32/40 partitions. Retained technical/conventional phrases,
  rejected fragments, aliases, coefficients and visibility-gap diagnostics are
  preserved; no post-result filter tuning or label changes were made.
- Artifacts: `data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/`.
  See `comparison_report.md`, `reports/`, `historical_uncertainty_correction/`
  and `frozen/freeze_manifest.json`. Historical point estimates are unchanged.
  Runtime was approximately nine minutes; peak memory 1.45 GB.
- All ten reference partitions matched prior predictions. Separate fresh
  preprocessing reproduced the first outer partition (390 comparison rows);
  both bundles reloaded with exact prediction parity. Completed-run resume
  verified artifacts without refitting; source dataset and parent hashes passed.
- Verification: all 256 tests passed, including 12 cleanup tests. Compilation
  and whitespace checks passed. Synthetic tests cover fold-local cleaning,
  alias equivalence, cache tampering, interruption/resume and bundle parity.
- Architecture is now frozen. Next: independent validation with new companies
  and broader website evidence. No API calls, new labels, population inference,
  production threshold or further tuning were performed.

## 2026-10-05: Milestone 5.4a - Independent Validation Readiness and Acquisition Plan

- Completed a read-only audit of current discovery, acquisition, extraction,
  deterministic feature preparation and both frozen company-model interfaces.
  Findings and proposed allocations: [Independent Validation Readiness](independent_validation_readiness.md).
- Generic domain-sample acquisition/extraction already works. The missing bridge
  is an unlabeled company loader/feature adapter and frozen-scoring orchestration:
  the research dataset loader still starts from historical calibration runs and
  packages. Preserve reference C=10 and cleaned combined C=1; no refitting needed.
- Provisional frame: 165,722 companies / 145,424 domains after current domain
  exclusions, all 649 development IDs and 408 other development-host-family
  matches. Unknown redirects/content overlap still require post-extraction checks.
  Shared websites are acquired once; company attribution and predictions stay distinct.
- Measured enriched acquisition took 4h07m; its current HTML/extraction files use
  3.87 GB. Links/indexes occupy 1.99 GB within the 3.45 GB database. Frozen inference
  has no separate measured runtime; training time is not used as its estimate.
- Proposed broad-sector 500-company sample begins with a nested, reproducible
  100-company measurement batch. No sample was drawn or registered. Existing
  coverage/settings remain unchanged; scale is controlled by scheduling, not new
  page/site caps. Sampling probabilities and all exclusions must be retained.
- Scenario estimates for 500: 1.8-9.0 GB persistent growth and 1.4-11.5 hours,
  plus working-space reserve. Larger pools are conditional, not promised. With
  23.67 GB currently free, an 8 GB filesystem floor and 4 GB transient reserve
  constrain planned persistent additions to about 11.7 GB despite the 20 GB budget.
- Next ticket: implement the unlabeled frozen-inference adapter, parity tests and
  resumable batch/resource checks; then authorize the first measured acquisition
  batch. Later define labeling allocations (approximately EUR 20), independent
  model/cutoff validation and confirmation. UNKNOWN is not EXCLUDE; NO_EVIDENCE
  stays unscored. No acquisition, API calls, inference, training, database changes
  or production optimizations were performed in this documentation-only ticket.

## 2026-10-06: Milestone 5.4b - Score Companies by Sample ID

- Added `ai_ecosystem.calibration.score_company_sample`: resolves explicit company
  membership first and preserves legacy domain-sample firm expansion. Only selected
  companies appear in the output; empty company samples never expand to sister firms.
- Extended the existing read-only source loader with an explicit unlabeled-company
  path. New firms require no calibration labels or historical LLM packages. The
  original historical-loading path and frozen scoring implementations are unchanged.
- Reused deterministic features, expanded lexical preparation and both frozen
  transforms/models. Frozen raw dataset tables supply the existing overlap context;
  selected websites are refreshed without changing duplicate/quarantine rules.
  Model/context/configuration hashes are checked, and no vocabularies are refitted.
- CSV output includes company ID, evidence state/reason, predictive-content flag and
  both signed model scores. NO_EVIDENCE remains unscored. Required text-file errors
  fail explicitly; existing outputs are not overwritten. No thresholds or labels
  are generated, and source database access is read-only.
- Command (replace sample ID/output path for another registered sample):
  `.venv/bin/python -m ai_ecosystem.calibration.score_company_sample --sample-id ai_enriched_v1 --output data/interim/calibration/company_scores/ai_enriched_v1.csv`
  See [usage and limitations](independent_validation_readiness.md#2026-10-06-sample-based-scoring-milestone-54b).
- Verification: five new sample-scoring tests and all 261 tests pass; compilation
  and whitespace checks pass. Coverage includes unlabeled/unseen companies,
  out-of-sample exclusion, legacy samples, no evidence, source immutability and
  historical feature/frozen-transform parity. No production model was retrained.
- Real-data verification of `ai_enriched_v1`: all 500 companies matched historical
  prepared feature values and both frozen scores exactly (maximum difference 0).
  There were 353 PROCESSABLE and 147 unscored NO_EVIDENCE rows. Verification outputs
  were temporary; existing model/experiment artifacts were not overwritten.
- The legacy `pilot_v0_2` path likewise reproduced all 149 companies' features and
  both model scores exactly: 123 PROCESSABLE and 26 NO_EVIDENCE. Source database
  size/modification time remained unchanged across both real-data checks.
- Limitations: the frozen context dataset remains required; this is not independent-
  validation sample registration or eligibility screening. Resource monitoring,
  acquisition, new labels, cutoff selection and production optimization remain
  out of scope. Next: register and prepare the independent validation sample in
  a separately authorized ticket.

## 2026-10-06: Milestone 5.4c - Create an Expandable Acquisition Pool

- Added `ai_ecosystem.calibration.build_acquisition_pool` with separate `create`
  and `extend` commands. Default creation is 1,500 companies, seed 20261006,
  with shuffled acquisition batches of 100 and no individual-batch sector quotas.
- Uses existing normalized-domain eligibility and excludes all 649 frozen
  development IDs plus known development website families. Missing ÖNACE sections
  remain an explicit stratum; shared-domain firms remain separate legal companies.
- Whole-pool weights are 80% proportional to eligible section populations and
  20% equal across populated sections. Capped, deterministic highest-averages
  allocation redistributes unavailable capacity and never decreases a section's
  allocation as the target grows. Sector permutations and unused IDs are saved.
- Reuses the generic registry: full pool and batches are company samples with
  exact membership and unique-domain projections. Extension only appends members
  and new batches, retaining old ranks/batch rows and historical artifact versions.
  No failed websites are replaced. New-member eligibility drift fails explicitly.
- Versioned frame/state, membership CSVs, exclusions, shared-domain audit and
  probability reports live under `data/interim/calibration/acquisition_pools/<pool_id>/`.
  Full-pool probabilities and early-batch probabilities are distinct, including
  the wave-specific calculation after expansion. No scores or labels drive sampling.
- Commands and limitations: [expandable pool usage](independent_validation_readiness.md#2026-10-06-expandable-acquisition-pool-milestone-54c).
  Create: `.venv/bin/python -m ai_ecosystem.calibration.build_acquisition_pool create --pool-id independent_validation_v1 --target-size 1500 --seed 20261006`.
  First batch: `independent_validation_v1_batch_0001`.
  Extend: `.venv/bin/python -m ai_ecosystem.calibration.build_acquisition_pool extend --pool-id independent_validation_v1 --target-size 3000`.
- Synthetic tests cover deterministic membership, 1,500/100 sizing, exclusions,
  saturation, monotone allocations, registry integration, shared domains,
  unchanged previous batches, extension-schedule-independent final membership,
  probabilities, tampering and transaction rollback. No real pool was registered.
- Verification: all 272 tests passed, including 11 new pool tests; compilation,
  CLI help and whitespace checks passed. All registration tests used temporary
  synthetic databases, with no production registry or corpus writes.
- Limitations: newly discovered aliases/content overlap remain a later validation
  check; interrupted filesystem writes may leave unregistered artifacts that must
  be inspected rather than overwritten. No acquisition, extraction, scoring, API
  calls, model changes or automatic resource scheduling were added or run.

## 2026-10-07: Milestone 5.4d - Improve Acquisition Speed Without Reducing Coverage

- Inspected `run_acquisition.py`, `html_fetcher.py`, acquisition migrations and
  `logs/acquisition_acquisition_20261006T210644Z_811e9833.log`. Observed first-batch
  runtime: 2,468.10 seconds (41m08s), 9,045 selected, 8,171 fetched, 874 failed,
  zero skipped, 20 workers, 15-second timeout and two retries: 3.66 pages/second.
  Failures: 807 HTTP errors, 26 timeouts, 24 SSL, 13 DNS and four other errors.
  The old log contains no stage timings, so it cannot attribute the elapsed time.
- Concrete wasted work: `acquire_candidate` called `ensure_acquisition_schema`
  before both the resume lookup and result write. This migration drops/rebuilds
  two full-table indexes on every call, potentially 36,180 index rebuilds for
  9,045 non-skipped pages, plus setup/finalization. The current table has 82,436
  rows. Initialization now runs once in the coordinator before workers start;
  no worker or finalization DDL is needed. The migration itself is unchanged.
- Kept thread-owned, short-lived SQLite connections and per-page transactions,
  with explicit connection closure and rollback on write failure. No connections
  are shared across threads; no write batching or delayed result persistence was
  introduced. URL selection/order, limits, timeout/retry policy, HTML parsing,
  compressed content, successful-page skipping and force-refresh semantics remain
  unchanged. Worker count and request concurrency policy are unchanged.
- Other hypotheses inspected but deliberately not optimized: a new HTTP session
  is created per page, and submissions are ordered by domain (78 domain blocks in
  batch 0001, largest 1,093 pages). Neither session/connection-setup overhead nor
  same-site congestion was separately measured in the old run. Session reuse
  would also require cookie-state isolation; scheduling changes need separate
  politeness checks. Both are deferred, not asserted to explain the runtime.
- Added lightweight `page_timing` log records, including on skips/errors:
  database read, download (all attempts/redirects/body), HTML metadata processing,
  gzip/file storage, database write and total worker seconds. Database durations
  include connection/lock/commit/close time. `setup_seconds` records initialization
  and selection separately. Concurrent elapsed stage times are not CPU timings
  and must not be summed as a partition of run wall time. Existing final elapsed
  time and counters remain available in `logs/acquisition_<run_id>.log`.
- Repeatable offline comparison: 50,000 synthetic existing rows and 40 new pages,
  identical mocked fetches, separate temporary databases. Across three runs,
  old per-page database work took 1.57-1.84 seconds versus 0.054-0.060 seconds after
  the fix, with identical stored rows. This establishes reduced local database
  overhead, not a real-internet speedup or a predicted next-batch runtime.
  Reproduce with `.venv/bin/python -m unittest discover -s tests -p 'test_acquisition_performance.py'`.
- Five focused tests cover offline data parity, unchanged metadata/gzip payload
  and retry counts, 20-worker writes with one schema check, skip/refresh behavior,
  rollback/retry and persistence across interrupted submission. Full suite:
  277 tests pass; compilation and whitespace checks pass. All fetching in tests
  is mocked; no live acquisition, extraction, scoring or paid calls were run.
- Batch 0002 is registered (100 companies / 100 domains), but at audit time has
  zero discovered candidate URLs. First run discovery if not already completed:
  `.venv/bin/python -m ai_ecosystem.discovery.homepage_discovery --sample-id independent_validation_v1_batch_0002`.
  Then measure acquisition using unchanged settings:
  `.venv/bin/python -m ai_ecosystem.acquisition.run_acquisition --sample-id independent_validation_v1_batch_0002 --fetch-scope discovered --max-workers 20`.
  Compare stage distributions, failures and wall throughput; different websites
  make this an operational measurement, not a controlled before/after benchmark.

## 2026-10-07: Milestone 5.4e - Investigate Extraction Speed

- Audit/profiling only; no extraction code, configuration or production data was
  changed. Inspected extraction, schema initialization and installed Trafilatura
  implementation. No persistent extraction log was found under `logs/`; the runner
  prints progress/summary to stdout rather than saving stage timings. The database
  confirms `extraction_20261006T215314Z_aa06b016`: 8,156 selected/processed,
  8,136 successes, 20 failures, zero skips, 21:53:14-22:04:22 UTC (668 seconds at
  timestamp resolution). The supplied precise baseline is 667.840 seconds.
- `process_candidate` times gzip reading/decoding, Trafilatura main extraction
  and metadata, Markdown-to-text conversion, hashing and three atomic evidence
  file writes. The timer stops BEFORE extraction-result persistence, page-feature
  construction and HTML link processing. Resume lookup and per-page commit are
  also excluded. The average includes failed processed pages, excludes skips.
  Thus 0.064 x 8,156 is approximately 522 seconds inside that timer and 146 seconds
  outside it (about 22%); rounding permits roughly 142-150 seconds outside. Total
  runtime includes selection/schema checks/progress/finalization, but excludes
  the initial `init_database()` call. This is not a pure Trafilatura benchmark.
- Database work: one processing connection, no per-page schema initialization,
  and one transaction commit per page. Extraction schema checks run during startup
  initialization and again before selection; initialization also invokes broader
  migrations, including acquisition index rebuilds, outside the reported runtime.
  This is not the repeated per-page acquisition problem. Read-only measurements
  on the current production database: selecting these 8,156 pages took 0.282s;
  all resume lookups took 0.293s. The run has 826,697 persisted link rows.
- Disposable profile: 35 existing downloaded pages across 20 domains, selected
  at 32 HTML-size quantiles plus three additional largest pages, two serial passes.
  Used a fresh temporary schema/database and temporary evidence directories,
  with network requests blocked. Both passes reproduced 34 successful content
  hashes plus the original failed/empty page; each stored 3,169 links. Production
  database size/mtime remained unchanged. This is a deliberately size-spanning
  sample, not an unbiased whole-batch estimate; the temporary database is smaller
  than production and caches/warm-up affect results.
- Measured profile (inclusive nested times must not be added twice): total
  7.84s/6.15s; Trafilatura main extraction 1.23s/1.16s; metadata 5.63s/3.93s,
  including date detection 4.71s/2.96s and metadata HTML loading 0.122s/0.122s.
  Link parsing/normalization cost 0.79s/0.87s, approximately 95% of the time outside
  the existing per-page timer. Link SQL added about 0.012s/0.013s, commits 0.015s,
  features including three immediate file rereads 0.009s/0.010s; package generation
  and atomic writes took 0.111s/0.114s, gzip reads 0.037s/0.031s.
- Ranked opportunities, preserving quality:
  1. CPU extraction/metadata is the largest measured cost. Bounded process workers
     could overlap independent pages while a single coordinator keeps per-page
     transactions and resume semantics. Potentially meaningful, NOT benchmarked;
     higher complexity/memory and ordering/interruption risks. Do not disable
     extensive date detection, metadata or recall settings to gain speed.
  2. Auxiliary links reparse the full HTML with BeautifulSoup. Investigate parsing
     only anchor subtrees with the SAME parser, keeping normalization/order/text
     unchanged. Moderate potential, not measured as an optimization; malformed
     HTML can behave differently. Require exact full link-row parity before use.
  3. Feature generation rereads the three files just written. Passing the same
     in-memory values avoids these reads with low consistency risk, but the
     measured feature step is tiny. Link `executemany` likewise saves Python calls
     but cannot remove index work; no evidence supports batching page commits.
  4. Sharing parsed trees may save an extra metadata parse, but only 0.122s was
     measured there and extractors may mutate trees. Low priority; do not change
     parsers or omit metadata. Startup migration cleanup is separate and does not
     explain the reported per-page gap.
- Proposed next ticket: add stage timings without changing extraction outputs,
  then a narrowly scoped, parity-gated anchor-only link-parsing benchmark. Do not
  adopt it if link rows differ; keep process-based parallelism as a separate later
  experiment. Compare Markdown/text hashes, complete metadata excluding timestamps,
  feature values and ordered link rows, plus restart behavior. No production
  speedup is claimed. Disposable reproduction: `/tmp/profile_extraction_54e.py`
  and `/tmp/profile_extraction_54e_results.txt`; no full-batch extraction,
  acquisition, scoring or API calls were performed.

## 2026-10-07: Milestone 5.4f - Investigate Repeated Website Timeouts

- Read-only audit of acquisition `acquisition_20261007T164147Z_5331b1a3`,
  discovery `homepage_20261007T164111Z_699bd078`, current acquisition/discovery
  code, run logs and database records. Acquisition reports 10,041 attempted,
  7,365 fetched, 2,676 failed, 20 workers and 3,828.60 seconds (63m49s).
  `hocheck.at` contributed 1,007 attempts: one successful homepage, 47 HTTP
  errors and 959 timeouts. Of its URLs, 1,000 came from its sitemap as root-path
  `post_type=flamingo_inbound` (639) or `flamingo_contact` (361) query URLs;
  those produced 953 timeouts and 47 HTTP errors. The remaining seven homepage-
  source URLs (the root and six links) produced one success and six timeouts.
  The sitemap was valid; its
  URLs were not manufactured by acquisition.
- Discovery preserves `post_type` and `p` because they are not tracking/empty
  query parameters. The host is allowed, the root path has depth zero, and
  `classify_page_type` calls these query URLs `homepage` (priority eight).
  The hard-negative and static-asset checks do not identify either exact query
  type. Discovery's 20-per-source retained limit kept 20 sitemap URLs and
  marked 980 as not retained, but acquisition used `--fetch-scope discovered`,
  which selects all 1,007 unique discovered URLs for that domain. This is why
  the retention cap did not bound network requests.
- Domain-ordered selection placed all 1,007 `hocheck.at` jobs consecutively at
  positions 4,065-5,071 of 10,041. Another 4,970 jobs from 46 domains were
  queued behind them. Each timeout's median worker duration was 45.18 seconds:
  three immediate attempts under the configured 15-second timeout/two-retry
  policy. With all 20 workers eventually occupied on this domain, no other
  domain completed a page from 18:54:21 to 19:43:59 local log time, apart from
  work that overlapped at the boundaries. The next domain resumed while the last
  `hocheck.at` requests were finishing. This directly supports queue starvation.
- Timing caveat: the domain's first/last completions span about 50m38s of log
  timestamps, but an 808.7-second gap occurs between any two completions and
  the whole run's wall timestamps exceed the reported monotonic duration by
  about 798 seconds. A computer pause or clock change is possible but unproved.
  Subtracting that discrepancy puts the domain's active queue span at about
  37-38 minutes, roughly 59% of reported run time. Its 44,286 summed worker
  seconds overlap across 20 workers and are NOT elapsed time. The estimate is
  approximate because the run has no explicit domain-phase monotonic timer.
- Recommended next implementation: deterministically interleave the existing
  per-domain URL queues before submitting to the same 20-worker executor,
  retaining each domain's current internal priority, every discovered URL,
  existing retries, timeouts, per-page records and resume checks. Verify the
  selected URL multiset is identical and check that mixed-domain fixtures let
  later domains make progress; keep per-site in-flight pressure at or below the
  present limit. This prevents one slow site's contiguous queue from starving
  other sites. It does not by itself eliminate the 1,000 failing URLs or promise
  shorter total runtime; after other sites finish, slow requests can still form
  the tail. Measure both time-to-other-domains and full run time separately.
- A general `p=` filter would discard potentially useful WordPress pages, while
  excluding the entire domain would lose its successful homepage and possible
  later recovery of other pages. A narrowly defined Flamingo-query filter may
  warrant a separate content/coverage audit, but is not recommended as the first
  fix. Failure-triggered deferral also needs explicit queue/status semantics:
  deferred URLs should remain in `discovered_pages`, be listed in a run report
  rather than counted as fetched or failed, and remain eligible for a later retry
  run. The proposed interleaving does not defer any URL. No live requests or
  production pipeline changes were made in this investigation.

## 2026-10-07: Milestone 5.4g - Defer Domains After Repeated Connection Failures

- Added a run-local, thread-safe domain failure guard to the existing acquisition
  worker path. The default threshold is five consecutive exhausted page-level
  connection failures, configured as
  `acquisition.consecutive_connection_failure_threshold` in `config/config.yaml`.
  Counted statuses are timeout, connection error, DNS error and SSL error after
  their existing retries. Any received HTTP response, including 404/500, resets
  the consecutive count. Other errors neither increment nor reset it. Once the
  threshold is reached, deferral stays latched until this invocation finishes.
- Workers check the guard after the existing-success lookup and immediately
  before starting a fetch. Already admitted requests finish and persist normally;
  queued requests for the deferred domain return without a network call or a
  `scraped_pages` row. The run continues through other domains using the existing
  domain ordering, 20-worker default, 15-second timeout and two retries. No
  cross-domain interleaving, URL filter, page cap, extraction/scoring change or
  permanent domain exclusion was introduced.
- Progress, final console summary and return value distinguish selected,
  attempted, fetched, failed, skipped-success and deferred counts. New run rows
  store actual attempted work in `pages_attempted`; selected and deferred totals
  are available in the log and return summary. Log entries include the trigger
  domain/status/reason, each deferred URL/domain/reason, and a final count per
  deferred domain. Deferred URLs remain in `discovered_pages`; a normal later
  invocation builds a fresh guard, retries them and previously failed pages,
  and skips saved successes. `--force-refresh` keeps its existing meaning.
- Mocked-network tests cover the default/config validation, reset on HTTP
  responses, retry attempts counted once per failed page, exhaustion across
  connection-failure types, run-local reset,
  persisted counts, deferred URL retention, next-run retry/skip behavior,
  concurrent in-flight completion and continued progress on another domain.
  All 282 tests, compilation and whitespace checks pass. No live acquisition or
  production database writes were performed.
- Limitations: up to the configured number of workers may already have started
  requests for a domain before the fifth failure is recorded; those requests
  are allowed to finish and may also fail. A successful in-flight response after
  the trigger does not undo this run's deferral. Deferral prevents further
  attempts on that domain in the current run, but does not fix the website,
  remove previously recorded failures or guarantee a short batch runtime.
- For batch 0004, run discovery first, then acquisition with the same scope:
  `.venv/bin/python -m ai_ecosystem.discovery.homepage_discovery --sample-id independent_validation_v1_batch_0004`
  and
  `.venv/bin/python -m ai_ecosystem.acquisition.run_acquisition --sample-id independent_validation_v1_batch_0004 --fetch-scope discovered --max-workers 20`.

## 2026-10-07: Milestone 5.4h - Diagnose Batch 0004 Discovery Failure and Page Provenance

- Read-only inspection of the discovery/acquisition code, run logs, sample registry,
  SQLite foreign keys and the frozen 649-company development dataset. Batch 0004
  contains 100 distinct legal companies and 100 normalized domains. Discovery
  `homepage_20261007T205549Z_66ec785b` failed in its pre-run refresh cleanup:
  `delete_discovered_pages_for_domains` deletes before the candidate cleanup or
  new `discovery_runs` insertion. Its `DELETE` includes 64 `discovered_pages` rows
  for `gross-glockner.at` (IDs 4716594-4716657), each referenced by a successful
  `scraped_pages.discovered_page_id` foreign key. Example: discovered page 4716594,
  `https://skisport.com/grossglockner/de`, is referenced by scraped page 74806;
  candidate 38291 is also referenced by that scraped row. Twenty of the 64
  scraped pages reference `candidate_pages` rows as well, but deletion of those
  candidates was never reached. No new discovery run, discovered page or candidate
  from the failed invocation exists. The prior run/status and all 64 pages remain,
  consistent with complete rollback of this discovery invocation.
- Those 64 unique URLs were discovered in batch 0001 by
  `homepage_20261006T210234Z_4f3c101d`, acquired successfully by
  `acquisition_20261006T210644Z_811e9833` and extracted successfully as v1 by
  `extraction_20261006T215314Z_aa06b016` (60 distinct content hashes).
  `gross-glockner.at` redirects to `https://skisport.com/grossglockner/de`;
  56 discovered URLs are under that path and eight are other `skisport.com` paths.
  The generic sample registry maps batch 0001 company `AT9090148410` (GBT
  Ski-Holding GmbH) and batch 0004 company `AT9090067383` (Grossglockner
  Bergbahnen Beteiligungsverwaltung GmbH) to the same domain. The domain record
  reports two firms and `shared_domain`. Acquisition
  `acquisition_20261007T210118Z_2e2933b4` selected these 64 surviving pages by
  sample domain, skipped all as existing successes, attempted/fetched none and
  did not acquire the other 99 batch-0004 domains. Its skip count is not evidence
  of a successful batch-0004 discovery.
- Against the frozen development artifacts, all 100 batch-0004 company IDs and
  normalized domain families are new; `firm_urls` supplies no additional aliases.
  The only previously acquired batch-0004 domain is `gross-glockner.at`. Neither
  it nor its known `skisport.com` redirect occurs among development website/final
  hosts. None of its 64 normalized URLs, scraped-page IDs or v1 content hashes
  occurs in the frozen development pages, and neither associated company has a
  development calibration result. Thus the observed 64 are **within-validation-
  pool website reuse**, not evidence used to fit the frozen models. The other
  99 sites have not been newly discovered/acquired, so their redirects and content
  overlap cannot yet be ruled out.
- The 1,500-company pool has 1,495 unique normalized domains; five domains each
  occur in two batches because sampling preserved distinct legal companies and
  randomized company order without clustering shared sites into one batch.
  Besides `gross-glockner.at` (0001/0004), the repeated domains are
  `easydrivers.at` (0002/0014), `bm-jaeger.com` (0009/0014),
  `maschinenring.at` (0006/0015) and `spar.at` (0009/0012). This is a future
  refresh/provenance and evaluation-grouping risk, not a duplicate company ID.
  Across previously acquired pages in the full pool, no final host or exact URL
  matches a frozen development host/URL, but 86 pages in 12 pool domains share
  35 exact extraction hashes with development pages. Inspected examples are
  placeholder lorem ipsum, cookie notices or parked-domain text; hash equality
  alone does not establish company-level leakage. Materiality of every match and
  unseen redirects/content need a post-acquisition validation audit.
- Smallest next code ticket: make discovery refresh foreign-key-safe, retaining
  acquired discovered/candidate rows and their provenance while allowing the
  remaining sample domains to be discovered; test repeat discovery on a shared
  domain with acquired pages and verify no historical references are rewritten.
  Keep shared legal companies distinct but group them by website in independent
  validation. Do not redraw this pool, disable foreign keys, or treat old database
  pages alone as development leakage. This ticket changed only this status entry;
  no database, pool, pipeline or artifact changes or live requests were made.

## 2026-10-07: Milestone 5.4i - Foreign-Key-Safe Discovery Refresh

- Refresh now deletes only unreferenced `discovered_pages` and `candidate_pages`
  for the selected sample domains. Existing scraped-page and retention-validation
  foreign keys are respected; acquired and extracted rows are not changed.
  Candidate upserts do not rewrite referenced candidates' original discovery
  metadata. Rediscovered URLs with a protected discovery record reuse that
  record instead of creating another row for a later run. Newly discovered URLs
  still receive current-run records and remain eligible for acquisition.
- Acquisition keeps its existing URL selection and skip policy. A narrow lookup
  fallback also recognizes a saved success linked only through a historical
  candidate when a discovered-page ID is present; this avoids refetching those
  legacy pages. Repeated discovery does not create duplicate acquisition work
  for already recorded URLs. Previously acquired URLs absent from a new discovery
  result remain in the inventory with original provenance. Domain-scoped
  acquisition still sees them: saved successes are skipped, while recorded
  failures remain retryable. Unreferenced URLs absent from the refreshed result
  are removed as before. The discovery summary counts observed/retained URLs,
  including protected URLs reused rather than inserted under the new run ID.
- Temporary SQLite regression tests exercise two company samples sharing a
  website, intact scraped/extracted references, preserved original run IDs,
  repeat refresh, unreferenced stale-row cleanup, a new selectable URL,
  candidate-only legacy success, real acquisition skip behavior without a
  network call, and rollback of cleanup after an injected failure. The focused
  discovery tests, full unittest suite, compilation and diff whitespace checks
  pass. No live discovery/acquisition/extraction/scoring/API calls or production
  database changes were made.
- Limitation: this resolves the refresh/FK failure, not validation-site
  attribution or overlap review. Batch 0004 discovery has not been rerun here;
  the user can run:
  `.venv/bin/python -m ai_ecosystem.discovery.homepage_discovery --sample-id independent_validation_v1_batch_0004`.

## 2026-10-09: Milestone 5.4j - Faster Sample-Scoring Source Reads

- Confirmed 5.4j is the next entry after 5.4i. The interrupted 1,500-company
  scorer loaded each domain with three separate SQLite queries. Read-only
  `EXPLAIN QUERY PLAN` on the production database showed a full
  `scraped_pages` scan for each domain count, extraction-index scans followed
  by primary-key domain checks for extraction counts and successful pages,
  and a temporary sort. No `scraped_pages.domain` index exists. The 7.2 GB
  database was not changed.
- Source loading now batches selected domains into bounded SQL `IN` queries
  for acquisition counts, versioned extraction counts and successful page
  records. Existing extraction-version filters, row ordering, title/heading
  fallbacks, file hashing, duplicate checks, shared-domain relationships,
  quarantine and scoring logic are unchanged. The source connection remains
  SQLite read-only. A selected-domain set also avoids repeated linear
  membership checks while loading domain metadata. No index migration or
  production index setup is required.
- On the same 25 sampled pool domains, read-only SQL retrieval took 93.106 s
  with the old per-domain pattern and 0.213 s batched, returning identical
  domain counts and 253 identical page rows. This measures the affected SQL
  step only under local cache conditions, not a full 1,500-company runtime.
  In a separate read-only 649-company check, 55,568 historical pages loaded
  in 25-31 s; all selected page IDs, aligned numeric features, lexical
  features, evidence states and text documents matched the frozen dataset.
  Both frozen models reproduced all 649 historical scores exactly, including
  NO_EVIDENCE values. Synthetic tests also cover unlabeled companies,
  extraction failures/versions, empty domains and multiple SQL batches.
- Sample scoring now prints elapsed-time messages after sample resolution,
  frozen-context loading, source loading, feature preparation, each model and
  CSV writing. The full 1,500-company run was not launched. Reading many text
  files, feature construction and frozen text transforms may still take
  substantial time; there is no measured full-run ETA. Existing-output
  refusal and read-only database behavior remain in place.
- Verification: 287 unittest tests passed; full 649-company feature/score
  parity passed. No acquisition, extraction, API call or production database
  update was performed. To score the full pool after checking that the output
  path does not already exist, run
  `.venv/bin/python -m ai_ecosystem.calibration.score_company_sample --sample-id independent_validation_v1 --output data/interim/calibration/company_scores/independent_validation_v1.csv`.

## 2026-10-09: Milestone 5.4k - Independent Pool Preparation and Scoring Record

- Confirmed 5.4k follows 5.4j. The saved `independent_validation_v1` revision
  and sample registry contain 1,500 distinct legal companies on 1,495 distinct
  normalized domains, randomized with seed `20261006` into 15 batches of 100.
  Selection used broad OeNACE sections (including an explicit missing-code
  stratum), with 80% proportional-to-eligible-sector allocation and 20%
  equal allocation across populated sectors; individual batches have no sector
  quota. The pool audit records 649 `development_company` and 1,057
  `development_website_family` exclusion reasons. These are audit categories,
  not additional sampled companies or necessarily additive counts.
- Saved discovery summaries show a completed discovery invocation for each
  batch: 1,500 batch-domain attempts, 1,260 successes and 240 failures. These
  are historical attempts, not unique-domain counts, because five websites
  occur in two batches. Acquisition run records exist for every batch (17
  invocations: batch 0002 was retried and an initial batch-0004 invocation
  selected only 64 prior successes). The table below uses *current* page rows
  joined by each batch's domain membership for acquisition/extraction status.
  `HTML` means acquired `success`; `other` means fetched non-HTML. Acquisition
  failures and failed v1 extractions appear in separate `fail` columns. Deferrals
  are historical unstarted-URL events from each batch's acquisition log, with
  the number of domains having at least one deferred URL. A dash means the
  guard had not yet been introduced, not confirmed zero pending URLs.

  | Batch | Discovery OK/fail | Acquisition HTML/other/fail | v1 extraction OK/fail | Deferred URLs/domains (run log) |
  |---|---:|---:|---:|---:|
  | 0001 | 85/15 | 8,156/15/874 | 8,136/20 | - |
  | 0002 | 67/33 | 5,349/17/439 | 5,341/8 | - |
  | 0003 | 81/19 | 7,360/5/2,676 | 7,345/15 | - |
  | 0004 | 86/14 | 6,767/12/627 | 6,764/3 | 110/6 |
  | 0005 | 84/16 | 8,460/37/2,041 | 8,412/48 | 101/6 |
  | 0006 | 82/18 | 7,101/14/422 | 7,091/10 | 420/2 |
  | 0007 | 92/8 | 8,860/10/871 | 8,855/5 | 102/3 |
  | 0008 | 86/14 | 6,233/18/524 | 6,226/7 | 97/4 |
  | 0009 | 84/16 | 8,263/674/667 | 8,219/44 | 130/3 |
  | 0010 | 88/12 | 7,377/84/796 | 7,361/16 | 1,313/7 |
  | 0011 | 78/22 | 5,975/49/1,229 | 5,972/3 | 53/3 |
  | 0012 | 82/18 | 6,058/22/2,750 | 5,774/284 | 1,333/4 |
  | 0013 | 82/18 | 5,376/291/410 | 5,303/73 | 685/2 |
  | 0014 | 89/11 | 8,825/83/3,036 | 8,817/8 | 523/4 |
  | 0015 | 94/6 | 8,877/9/352 | 8,809/68 | 1,136/5 |

- Across the *unique full-pool domains*, current database rows contain
  125,900 distinct acquired URL records in 1,169 domains: 106,869 successful
  HTML pages, 1,340 non-HTML responses and 17,691 recorded acquisition
  failures. Every successful HTML row has a v1 extraction record: 106,258
  successes and 611 failures. Thus 326 pool domains have no acquired row.
  Per-batch table values must not be summed into pool totals because shared
  domains appear in two batches; run attempts, skips and retries must not be
  treated as additional unique pages. Guard-era logs record 6,003 deferred-URL
  events across 49 domains with nonzero deferrals (76 domains triggered the
  guard, including zero-unstarted-URL cases). These are not a deduplicated
  current pending count; later shared-domain runs can retry earlier deferrals.
  The exact current number of discovered-but-never-attempted URLs was not
  established from saved summaries. Extraction run `sample_id` fields are NULL,
  so the per-batch extraction snapshot is verified by domain/page joins rather
  than by run-to-batch metadata.
- Five website domains are shared between different pool batches (listed in
  5.4h); their legal companies remain distinct but reuse website evidence.
  The earlier 5.4h development-overlap check covered data available then,
  including 35 exact content hashes across 86 pool pages in 12 domains, mostly
  inspected as generic/placeholder material. It was not a final review of all
  later redirects and acquired content. Full post-acquisition host/URL and
  substantive content-overlap checks, plus shared-site attribution review,
  remain required before claiming independent validation.
- Full-pool scoring completed in a user-reported 387.3 seconds. The scores CSV
  at `data/interim/calibration/company_scores/independent_validation_v1.csv`
  has one row per 1,500 pool companies: 1,084 `PROCESSABLE` companies with both
  frozen scores and 416 unscored `NO_EVIDENCE` companies. The source database
  confirms 106,258 successful v1 extracted pages across 1,080 domains, matching
  the reported load count. The two unchanged frozen models are
  `reference_logistic_size` and `cleaned_grounded_combined`. This step did not
  retrain either model, generate LLM labels or set a final selection cutoff.
  The acquisition overhead, temporary domain guard, safe discovery refresh and
  batched scoring reads are documented in 5.4d/5.4g/5.4i/5.4j respectively.
- Next: inspect both score distributions and their overlap, then design the
  independent labeling sample and cutoff-validation protocol. Resolve the
  remaining coverage and development-overlap uncertainties before treating
  these scores as an independently validated operating point.

## 2026-10-09: Milestone 5.4l - Independent-Pool Score and Model-Overlap Analysis

- Confirmed 5.4l follows 5.4k. Analyzed the existing
  `data/interim/calibration/company_scores/independent_validation_v1.csv`
  with the frozen pool revision and read-only firm names. Verified 1,500
  unique companies on 1,495 domains: 1,084 `PROCESSABLE` rows have both finite
  scores, while 416 `NO_EVIDENCE` rows have neither and are not scored as zero
  or treated as EXCLUDE. Scores are uncalibrated decision values, not
  probabilities; all cutoffs below are exploratory.
- Among the 1,084 processable companies, reference cutoffs >=2/4/6 select
  98/43/7 and challenger cutoffs >=1/2/3 select 110/68/25. The corresponding
  pairs select 125, 74 and 28 unique companies, with 83, 37 and 4 selected
  by both. Deterministic top 25/50/100 lists share 12/32/80 companies and
  contain 38/68/120 unique companies in their unions. Top-K ties are resolved
  by score descending, then `bvd_id` ascending. These overlaps show material
  disagreement, especially at the highest bands, but do not show which model
  is correct.
- Broad OeNACE M (professional/technical) and J (information/communication)
  are more prominent in the mid-cutoff and top-100 lists than in the scored
  pool; this is a composition diagnostic, not measured AI yield. Four of the
  five within-pool shared domains have two processable companies. The
  challenger includes both `maschinenring.at` companies in several shortlists;
  the reference lists shown do not duplicate a within-pool website. Shared
  website evidence still requires focal-company attribution, and the pending
  full post-acquisition development-overlap review is not replaced here.
- Report, four inspected figures and supporting company/sector/overlap tables:
  `data/interim/calibration/company_score_analysis/independent_validation_v1/report.md`.
  The report identifies large within-model rank disagreements without
  subtracting differently scaled scores. Input hashes, exact coverage checks,
  inclusive-cutoff counts and overlap arithmetic were verified; the source
  scores and pool registry were not changed.
- The processable pool has enough candidates to plan a first independent
  labeling sample spanning common high-rank cases, model-only disagreements
  and lower-score checks. Expansion is not required by this score analysis
  alone, though rare sectors or scarce very-high-score cases may later warrant
  it. Actual KEEP yield and missed KEEPs require independent labels, including
  lower-score cases. No labeling sample or production cutoff was chosen, and
  no scoring, acquisition, retraining or LLM call was run.

## 2026-10-09: Milestone 5.4m - Historical Calibration Scores by Label

- Analyzed saved grouped outer-CV predictions for `reference_logistic_size`
  and `cleaned_grounded_combined`, keeping their two repetitions separate.
  Each contains the same 649 historical companies, of which 476 are scorable:
  104 KEEP, 179 UNKNOWN, and 193 EXCLUDE. The other 173 are unscored
  NO_EVIDENCE (all historically UNKNOWN), never assigned zero scores.
  Frozen-model scores on their own training companies are shown only as an
  optimistic score-scale diagnostic, not an estimate of generalization.
- In outer CV, reference cutoffs >=2/4/6 select 93-97/43-44/16-17 companies,
  including 65-69/32-34/13-15 KEEPs across the two repetitions. Challenger
  cutoffs >=1/2/3 select 76-84/34-42/21-22, including 54-57/27-28/15-17
  KEEPs. Top 100 captures 69-70 reference or 68 challenger KEEPs out of 104;
  reaching at least 90% historical KEEP capture requires ranking 155-156
  reference or 169-211 challenger companies. These figures do not establish
  a small, high-recall production shortlist.
- The report provides count and within-label score distributions, cumulative
  label coverage, selected KEEP/UNKNOWN/EXCLUDE composition, cutoff and
  equal-budget top-K tables, separate broad-pilot/enriched-sample tables,
  final-fit diagnostics, and traceable company scores. See
  `data/interim/calibration/company_score_analysis/historical_label_distributions_v1/report.md`.
  The source predictions and labels were not changed. Scores are uncalibrated;
  CV cutoffs may not transfer to frozen deployment scores. Neither calibration
  sample is population representative, and historical labels may miss evidence
  outside the original packages. These bands guide independent labeling only;
  no cutoff, inference run, new labels, or API calls were made.

## 2026-10-12: Milestone 5.4n - Ten-Company AI-Profile Pilot Prepared

- Added the versioned `company_ai_profile_v1` prompt and strict schema under
  `src/ai_ecosystem/calibration/prompts/`, copied byte-for-byte from the
  finalized ticket files. The local contract validates exact page-specific
  quotations, references, positive-assessment basis/status, entity/edge types,
  focal-company graph involvement and nonpositive output consistency. It
  normalizes symmetric edges and exact duplicate facts without inventing
  relationships. The separate `company_ai_profile_mapping_v1` maps valid,
  grounded, attributable operational or pilot/research positives to KEEP;
  sufficiently evidenced attributed double-negatives to EXCLUDE; and other
  valid profiles to UNKNOWN. NO_EVIDENCE and technical/review failures have
  no substantive derived label. Model output and later manual corrections
  remain separate; confidence is not a calibrated probability.
- Added `config/company_ai_profile_pilot.yaml`, package selection/building,
  ordinary Responses execution, resumable per-company raw/result persistence,
  local spend guard, strict validation, review-report output and an offline
  Batch JSONL export. The same request body serves ordinary and Batch modes;
  this milestone does not submit a Batch job. `gpt-6-luna` with low reasoning,
  8,000 maximum output tokens, Standard processing and a $2 local pilot
  allowance are the defaults. Official model pricing checked 2026-10-12:
  $0.10/M input, $0.01/M cached input, $0.50/M output, with long-context
  multipliers above 272k input tokens (see
  https://developers.openai.com/api/docs/models/gpt-6-luna). The allowance
  does not control other account usage or resolve ambiguous network billing.
- Using seed 20261012 and the saved independent-pool scores, prepared ten
  distinct-domain PROCESSABLE companies: four in both top-100 lists, two
  reference-only, two challenger-only and two outside both. The local checks
  found no known development-domain, redirect-host, normalized-URL or
  substantive-content-hash overlap among the ten. This does not certify
  full-pool independence or rule out undetected website-family overlap.
  The blinded frozen packages contain 734 included text pages and 2,044
  budget-omitted unique pages; no utility-path exclusions occurred in this
  particular selection. Duplicate provenance and all omissions are retained
  locally. Shared websites still receive focal-company-specific assessments.
- The 250k website-evidence limit uses UTF-8 byte counts as a conservative
  upper-bound proxy because no verified GPT-6 Luna tokenizer is installed;
  it is **not** an exact model token count and can underfill the nominal
  budget. Prompt/schema/output context is checked separately. The offline
  dry-run reports <=2,095,029 request-token-proxy units across ten requests
  and a conservative $0.2495 reservation, subject to actual API tokenization
  and pricing. Prepared artifacts are under
  `data/interim/calibration/ai_profile_pilot_v1/`, including manifest,
  packages, snapshots, request identities and an unsubmitted Batch preview.
- Verified 15 focused fixture/mock tests, the full unittest suite,
  `compileall`, byte-identical supplied prompt/schema, JSON Schema validity,
  and deterministic fresh preparation with identical request identities.
  No paid API call, new label, crawler run, extraction, scoring, model change,
  or production-database write occurred. The pilot is a prompt/runner
  diagnostic, not a performance or prevalence estimate. Review its outputs
  manually before designing the remaining independent-validation labeling.
- Commands: `python -m ai_ecosystem.calibration.run_company_ai_profile_pilot
  dry-run`, then explicitly `... run` for ordinary paid requests. If
  interrupted, use `... resume`; failed records require explicit
  `--retry-status` (or `--retry-ambiguous`). Afterward use `... report`.
  `... export-batch --output <new.jsonl>` is offline only. Run with
  `.venv/bin/python` in this repository.

## 2026-10-13: Milestone 5.4o - Model-Compatible Pilot Evidence Budget

- Rebuilt the same ten focal companies in
  `data/interim/calibration/ai_profile_pilot_v2_verified/`, leaving the v1
  packages and requests untouched. The prompt, strict schema, label mapping,
  page ordering, duplicate handling and URL exclusions are unchanged. The
  new directory includes frozen packages, request identities, snapshots,
  `before_after.csv`/`.json`, and an offline Batch JSONL preview.
- The 250,000-token website-evidence allowance now uses the GPT-6 Luna
  Responses `input_tokens.count` endpoint on the finalized request body.
  Evidence tokens are the difference between counts for that request and the
  same request with empty user content. OpenAI's `o200k_base` encoding is used
  only as a fast provisional page-allocation proxy: its public model mapping
  does not identify `gpt-6-luna`, so it is **not** claimed to be exact. If
  the model-specific count exceeds the cap, the lowest-priority included
  page is omitted and the request is recounted. The frozen request hash binds
  the count to the body used by ordinary and Batch modes. Counting is
  model-specific at preparation time; later model-side changes or usage
  billing may still differ.
- Exact non-evidence input overhead is 2,573 tokens/request: 1,605 prompt,
  958 schema and 10 request framing. The 8,000-token maximum output allowance
  is separate. Across ten companies, included text pages increased from 734
  to 1,733; budget-omitted pages decreased from 2,044 to 1,045. The old
  packages contain 476,224 actual input tokens when counted with the same
  endpoint, versus 1,257,168 in v2. Four large sites still omit pages. The
  Standard-tier reservation at full allowed output is $0.1657, compared with
  $0.0876 for the old bodies when measured in actual tokens; the prior
  $0.2495 byte-based reservation was a conservative proxy, not an actual
  token-cost estimate. See `before_after.csv` for every company's page and
  token counts. These are reservations, not observed labeling costs.
- The new `tiktoken` dependency, budget tests and frozen ordinary/Batch
  request checks are in place. All 19 focused tests, the full unittest suite,
  `compileall`, and `git diff --check` passed; all ten Batch-body hashes match
  the frozen ordinary request hashes. Preparation used only token-count requests;
  no paid AI-profile generation, Batch submission, new labels, acquisition,
  extraction or scoring occurred. The pilot still requires an explicit
  ordinary `run` command. From the repository root use
  `.venv/bin/python -m ai_ecosystem.calibration.run_company_ai_profile_pilot dry-run`
  to inspect it, then `... run` only when ready to make paid calls. Both
  commands default to the v2 verified directory; `--directory` can select it
  explicitly. The v1 directory remains available for historical audit. An
  earlier `ai_profile_pilot_v2_token_budget` comparison build also remains
  preserved, but `ai_profile_pilot_v2_verified` is the final runnable v2 pilot.

## 2026-10-13: Milestone 5.4p - Product/Service Evidence Ordering

- Added a pilot-only URL ordering: homepage, obvious about/company paths,
  product/service/solution paths, then remaining pages by path depth and
  scraped-page ID. The new tier matches complete German or English path
  components (including `produkte`, `dienstleistungen`, `leistungen`,
  `loesungen`, and percent-encoded `lösungen`), not substrings, queries or
  fragments. The historical blind-calibration sort key was not changed.
- Rebuilt the same ten companies at
  `data/interim/calibration/ai_profile_pilot_v3_product_priority/`, preserving
  v1/v2 artifacts. The prompt, strict schema, label mapping, configuration,
  deduplication, utility-path filters and 250k evidence-token allowance match
  v2. The frozen ordinary and offline Batch bodies were checked against
  model-specific Responses input-token counts; no labeling or Batch submission
  occurred. `before_after.csv` records per-company selected pages, ordering,
  product/service/solution counts, input tokens and cost reservations.
- Product/service/solution pages included increased from **107 to 130**.
  Selected-page membership changed for `maschinenring.at` (20 to 21 such
  pages; one page added and one removed) and `eisbacherhof.at` (11 to 33;
  23 added, 12 removed). The same pages were reordered for `aba.gv.at`,
  `mf.ag`, `enzian.net` and `interapp.net`; the other four companies did not
  change. Total included pages rose from 1,733 to 1,744 and budget omissions
  fell from 1,045 to 1,034. Exact total request input changed from 1,257,168
  to 1,257,158 tokens; the full-output Standard-tier reservation is about
  **$0.1657**, effectively unchanged. Four large sites still omit pages.
- Added focused path/ordering tests, including encoded German segments,
  substring rejection, deterministic ties, unchanged historical sorting,
  and actual product-page inclusion under a tight package budget.
  All 23 pilot tests, the full unittest suite, `compileall` and request-hash
  checks passed. No acquisition, extraction, scoring, model change or
  production-database write occurred. Use
  `.venv/bin/python -m ai_ecosystem.calibration.run_company_ai_profile_pilot
  dry-run --directory data/interim/calibration/ai_profile_pilot_v3_product_priority`
  to inspect; the corresponding `run` command makes the ordinary paid pilot
  calls only when explicitly invoked.

## 2026-10-14: Milestone 5.4q - AI-Profile Contract Clarification

- Versioned the pilot as `company_ai_profile_v2` / `company_ai_profile_mapping_v2`
  in new prompt, strict schema and configuration files. Historical v1 requests,
  results and mapping remain unchanged. Attribution now asks whether supplied
  evidence identifies the focal company sufficiently to assess it, independently
  of whether AI is present. The prompt allows a consistent trading-name/domain
  identity without exact legal-name text, treats redirects neutrally, and flags
  unexplained different entities or unconnected shared-group activity as
  uncertain. Positive focal AI claims require linked evidence; first-person
  claims and explicit focal contributions in news/customer stories can qualify,
  while logos and third-party mentions alone cannot. False/null focal
  assessments retain `basis=none` even when their reasons cite another firm's
  AI activity.
- Clarified that using a third-party AI system in operations/customer service
  supports `adopter`, not `application_provider` unless the company supplies
  its own AI product. Added `unspecified_ai` for explicit AI systems lacking a
  supported technique and instructed the model not to infer machine learning
  from generic AI/KI or duplicate bilingual descriptions. The KEEP/UNKNOWN/
  EXCLUDE criteria, confidence treatment and adjacent-activity rules did not
  change. ABA's possible ecosystem-support role remains an unresolved taxonomy
  question; this ticket does not force it into an existing category.
- Prepared ten revised ordinary/Batch-compatible requests at
  `data/interim/calibration/ai_profile_pilot_v4_contract_clarification/`.
  `source_packages.json` references the exact frozen v3 package files with
  SHA-256 checks; no website evidence was reacquired, reselected or rewritten.
  The request hashes/custom IDs differ from v3, and the new directory contains
  no prior results. Local validation enforces exact quotations, strict page/
  evidence references, nonpositive basis and positive focal-attribution
  references; semantic correctness still requires pilot review.
- Model-specific token-count requests measured 1,260,518 input tokens versus
  1,257,158 for v3, exactly 336 additional input tokens for each of ten
  requests. The 1,744 included and 1,034 omitted pages are unchanged. The
  full 8,000-output-token-per-request Standard-tier reservation is about
  $0.1661 versus $0.1657 for v3, below the $2 local pilot allowance.
  `build_summary.json` records per-company before/after counts, prompt/schema/
  framing allowance and source provenance. No labeling requests or Batch
  submission occurred.
- Focused fictional regression cases cover trading names, redirects, different
  legal entities, shared-group attribution, informative non-AI evidence,
  third-party AI mentions, and hotel-mirror adopter/unspecified-AI output.
  All focused tests and the full unittest suite passed. Next step is an
  explicitly invoked ordinary ten-company rerun, followed by review; no
  production cutoff or taxonomy expansion is implied.

## 2026-10-14: Milestone 5.4r - Attribution Scope and AI-Profile Prompt Streamlining

- Reviewed the v4 ten-company pilot and confirmed `company_ai_profile_v2` /
  `company_ai_profile_mapping_v2` in its frozen configuration. The next unused
  milestone is 5.4r. Added versioned `company_ai_profile_v3` prompt/schema and
  `company_ai_profile_mapping_v3` with a separate future configuration;
  historical prompts, requests and results remain unchanged. No new requests
  or evidence packages were prepared in this ticket.
- The prompt now treats the homepage/about business on the ORBIS-linked domain
  as the default subject without claiming legal-name equivalence. A separately
  identified smaller business needs its own activity, while a supported
  regional group member may qualify from general group-wide AI evidence,
  explicitly marked as group attribution rather than proven regional use.
  Activity limited to another region does not qualify. New per-assessment
  `attribution_scope` values are `website_business`, `focal_unit`, `group`,
  or null for false/uncertain assessments.
- A positive scope requires existing grounded evidence references; group
  scope must share a membership reference with the attribution assessment.
  Group-level graph edges may now use an explicitly identified
  `group_organization` entity, linked to the focal membership evidence, while
  the edge has its own relationship evidence. Unrelated third-party edges are
  still rejected. The v3 derived result preserves both assessment scopes and
  a `group_attributed` flag, allowing separate reporting of direct and group
  cases. Existing KEEP/UNKNOWN/EXCLUDE rules, adjacency boundaries, roles,
  technologies (including `unspecified_ai`), exact quotes, confidence and
  model/settings are unchanged.
- The v2 prompt was 10,754 characters / 1,405 whitespace words; v3 is 9,699
  characters / 1,240 words. Local `o200k_base` counts fell from 1,937 to
  1,774 tokens (8.4%). These are local proxy counts, **not** verified Luna
  input-token counts; no token-count or labeling API calls were made.
  Tests cover website-business aliases, a distinct hospital parking business,
  regional group scope, other-region-only evidence, group graph endpoints,
  legacy schema isolation and exported scope provenance. Semantic identity,
  membership and relationship support still require review of cited quotes;
  reference checks cannot prove those meanings automatically. Next step is
  preparing new pilot requests against frozen evidence under the v3 contract.

## 2026-10-15: Milestone 5.4s - Scoped AI-Profile Pilot Request Preparation

- Extended the existing `prepare-rerun` command with an explicit
  `--contract-id company_ai_profile_v3` option. It accepts only the frozen
  `ai_profile_pilot_v3_product_priority` package source for this contract,
  retains the legacy v2 rerun path, and refuses to overwrite an existing
  destination. The new request directory is
  `data/interim/calibration/ai_profile_pilot_v5_attribution_scope/` with
  snapshots of the v3 prompt, matching strict schema, configuration and
  request hashes. The frozen source package and selection-manifest SHA-256
  hashes are recorded and checked at loading; no evidence was repackaged.
- All ten company IDs, names/domains and model-facing website evidence
  (page IDs, URLs, order and text) match the v3 product-priority requests.
  The same **1,744** pages are included and **1,034** omitted. Every request
  hash differs from v3, custom IDs are unique, and the new directory has no
  prior results; historical requests and responses remain untouched. Model,
  low reasoning effort and 8,000-token output allowance are unchanged.
- The model-specific Responses input-token count endpoint verified
  **1,259,178** total input tokens, 202 more per request than the earlier v3
  product-priority contract. Non-evidence overhead is 2,775 tokens/request:
  1,774 prompt, 991 schema, 10 framing. The full-output Standard-tier cost
  reservation is **$0.1659**, under the $2 local pilot allowance; actual
  billing may differ with retries/cache. `build_summary.json` has per-company
  counts and reservations. Only token-count calls were made, with no AI-profile
  labeling calls or Batch submission.
- Added offline regression coverage for frozen-source enforcement, identical
  model-facing evidence, new request identity, no old-result reuse and v2
  preparation compatibility. The next step is the user's explicit ordinary
  ten-company run followed by review; this preparation does not change labels
  or the underlying website evidence.

## 2026-10-15: Milestone 5.4t - Four-Case AI-Profile Pilot Fix

- Reviewed the v5 attribution-scope results before changing the contract. SOJUKA
  added the reserved `focal_company` endpoint to `entities`; Enzian cited an
  exact quotation from page 126576 as page 126591; InterApp's ev3 replaced
  source line breaks with spaces; and Maschinenring Tennengau was excluded
  despite evidence of regional group membership and general group AI activity.
  These are pilot diagnostics, not revised labels.
- Added versioned `company_ai_profile_v4` prompt/schema and
  `company_ai_profile_mapping_v4` configuration. The prompt now explicitly
  treats `focal_company` as a pre-existing relationship endpoint, requires
  verbatim quotes from the exact cited page ID, and states that supported
  regional membership plus general current group AI qualifies with
  `attribution_scope=group` even without demonstrated regional use. Activity
  limited to another region does not transfer, and group edges must not be
  assigned to the regional branch. The schema, AI definitions, roles,
  relationship types, model, low reasoning effort, output allowance and Python
  KEEP/UNKNOWN/EXCLUDE derivation remain unchanged. The LLM still does not
  output those labels.
- Kept strict exact-quotation validation. The review report now distinguishes
  exact matches, wrong-page quotations, whitespace-only mismatches, and
  missing quotations without accepting the latter three as valid. The real v5
  Enzian/InterApp responses reproduce the wrong-page and whitespace-only
  diagnoses. Semantic attribution still requires review; prompt clarification
  cannot guarantee the model will apply the group rule correctly.
- Prepared only SOJUKA, Enzian, InterApp and Maschinenring Tennengau under
  `data/interim/calibration/ai_profile_pilot_v6_four_case_fix/`. The directory
  references the exact SHA-256-checked v3 product-priority packages; company
  identity, 786 included pages, 765 omitted pages, page order and text were
  not rebuilt. It contains new prompt/schema/config snapshots and four new
  request identities, with no copied results. The model-specific token-count
  endpoint verified 637,667 total input tokens; reserving 8,000 output tokens
  per call yields $0.0798 at the configured Standard rates, below the $2 pilot
  allowance. No labeling request or Batch submission occurred.
- Focused tests cover exact-quote diagnostics without relaxed acceptance,
  four-company source-package integrity and versioning. The full unittest
  suite, compilation and dry-run validation passed. The next step is an
  explicitly invoked four-company ordinary pilot run and report review; no
  broader rerun or production label change is implied.

## 2026-10-15: Milestone 5.4u - Quotation Edge Whitespace and Offline Revalidation

- Changed quotation validation to try an exact substring on the cited page
  first, then an exact substring after trimming only leading/trailing Unicode
  whitespace (including non-breaking spaces). Empty trimmed quotations,
  internal whitespace changes, altered wording and wrong-page citations remain
  invalid. The original model quotation is retained unchanged. The review
  report distinguishes `PASS_EXACT` from `PASS_EDGE_WHITESPACE_TRIMMED` and
  retains separate wrong-page, internal-whitespace and missing-quote diagnoses.
  Prompt, schema, evidence packages, model settings and Python label rules did
  not change.
- Added offline `revalidate` for saved raw responses. It writes separate
  results under `ai_profile_pilot_v6_four_case_fix/revalidation/
  quote_edge_whitespace_v2/`, recording the validation version and SHA-256
  hashes/paths of each source response and prior result. Original raw and
  result files were not overwritten. Report generation now chooses the next
  free numbered filename; the original `review_report.md` remains intact and
  the revalidated report is `review_report_2.md`.
- All four saved v6 responses revalidated as valid: SOJUKA KEEP, Enzian KEEP,
  InterApp EXCLUDE and Maschinenring Tennengau KEEP. SOJUKA's ev3 on page
  134399 passed only after trimming its trailing non-breaking space; all its
  other cited quotations passed exactly. No remaining mechanical validation
  failures were found in these four responses. This does not verify the
  semantic correctness of activity, legal identity or group attribution;
  manual interpretation of the cited evidence remains necessary.
- Focused tests cover edge whitespace, empty quotations, internal mismatch,
  wrong-page rejection, preserved response text, separate revalidation output
  and numbered report preservation. The full unittest suite passed. No API
  calls or new labeling were made.

## 2026-10-20: Milestone 5.4v - Independent-Pool Evidence-Budget Comparison

- Completed an offline comparison of the existing AI-profile package rules at
  25k, 50k, 100k and 250k evidence-token caps for all 1,084 PROCESSABLE
  companies in `independent_validation_v1`. The 416 NO_EVIDENCE companies are
  excluded from modeled requests, not assigned negative labels. The report,
  company-level CSV, compact page-ID manifest, quotation-coverage CSV, charts
  and provenance summary are under
  `data/interim/calibration/ai_profile_budget_analysis/independent_validation_v1/`.
- Removed repeated candidate preparation across caps in the offline analysis
  and exposed the existing package preparation/allocation steps for reuse;
  package ordering, utility filtering, exact-content deduplication and token
  allocation behavior remain unchanged. Independent companies are measured
  with four worker threads. At the 550-company checkpoint, the earlier run
  took 1,978 seconds versus 991 seconds for the optimized run; cache warmth
  prevents attributing that difference solely to code changes. The completed
  run took 2,582 seconds including source loading and artifact generation.
- Estimated total Batch costs, reserving the full 8k output allowance, were
  $2.97, $3.27, $3.64 and $4.32 respectively. Eligible-page coverage was
  29.2%, 41.0%, 53.8% and 75.3%; complete eligible evidence was available
  for 783, 895, 958 and 1,020 companies. These are local `o200k_base`
  estimates, not model-verified request counts or actual bills. The known
  prior pilot usage is $0.4437; one prior attempt and any other account charges
  remain unresolved, so $20 headroom is not confirmed. Three companies have
  nonmonotone included-page counts across adjacent caps because the unchanged
  URL-ordered allocation can include a longer early page at a higher cap.
- At 25k, none of SOJUKA's or Enzian's saved supporting quotations survive;
  Maschinenring Tennengau retains one of three. Enzian has both at 50k,
  Maschinenring has all three at 100k, and SOJUKA has all three only at 250k.
  Citation retention checks coverage of known evidence, not label accuracy.
  No final evidence limit was selected. Focused parity tests and the full
  unittest suite passed; both charts were inspected. No API calls, Batch
  submission, model fitting or source-data changes were made. The next step
  is to use these tradeoffs when defining the independent labeling budget and
  validation sample, with live token counts before any paid campaign.

## 2026-10-20: Milestone 5.4w - Prepared Full-Pool Luna Validation Batch

- Prepared frozen GPT-6 Luna/low-reasoning, 250k-evidence-token,
  8k-output-token Responses Batch requests offline using the approved
  `company_ai_profile_v4` prompt/schema, v4 Python label mapping and
  edge-whitespace-v2 quotation validator. The 5.4v source-row hash, page
  selection and token allocations were checked before reuse. No API requests,
  uploads, labels or Batch submissions occurred. Output is under
  `data/interim/calibration/ai_profile_validation_batch_v1/`.
- Of 1,500 pool firms, 1,084 were PROCESSABLE and 416 NO_EVIDENCE. Prepared
  1,017 requests; one PROCESSABLE company (`AT9030116642`, `tombauer.aero`)
  had no usable page after the fixed package filters. Two clear development
  overlaps were quarantined: `AT9030118034` (`kcu.at`, material content) and
  `AT9150093010` (`bagspot.at`, redirect/exact URL/material content). Another
  64 firms have subthreshold or potentially generic exact-content overlap
  and are withheld for review, not silently declared independent.
- Grouped shared websites/content with the existing leakage rules and froze
  a deterministic seed-20261020 selection/holdout split of 814/203 firms in
  1,008 groups. Eight groups contain multiple firms (17 firms total); no group
  crosses the split. Broad-sector balance is recorded in the company manifest.
  Holdout labels must remain out of model/cutoff selection reports.
- Five JSONL files hold 1,017 unique requests and 40,535,218 **locally
  estimated** input tokens. Files contain 14-284 requests and 3.1-42.4 MB
  each, below the documented 50,000-request/200 MB per-Batch limits. The
  uncached short-context Batch estimate is $2.03 input plus a full $2.03
  8k-output reservation, $4.06 total. At 20% more actual input tokens, 67
  requests might cross Luna's 272k long-context price threshold; the
  corresponding sensitivity cost is about $5.54. These are not verified Luna
  counts or actual bills. Known prior pilot usage is $0.4437; one ambiguous
  attempt and other account charges remain unresolved.
- Current official OpenAI Batch/model/rate-limit documentation was checked on
  2026-10-20 and linked in the preparation report. Luna's published Batch
  queue allowance depends on account tier (Build 20M, Launch 1B, Grow 15B
  enqueued input tokens). The account/project tier, current queue occupancy,
  spend limits and residency settings were not established offline. Splitting
  files does not increase the shared queue allowance; submission remains
  blocked pending those checks and overlap review. The current pilot runner
  only exports Batch JSONL, so a small identity-checked submit/status/ingest
  adapter is still needed before a user-run submission command is available.
- Preparation uses atomic per-company checkpoints and source/config hashes.
  A completed `--resume` revalidated the source, split and Batch file hashes
  without rewriting them. Local checks verified unique IDs, request settings,
  file bytes, token totals and split isolation; focused tests, the full
  unittest suite, compilation and whitespace checks passed. Existing
  near-duplicate candidate discovery is not exhaustive, so further
  independence review remains necessary.

## 2026-10-20: Milestone 5.4x - Reviewed Uncertain Development Overlaps

- Reviewed all 64 companies withheld for possible development-content overlap
  in the 5.4w manifest. The 41 distinct exact shared texts were generic cookie
  and embedded-content notices, CMS/theme demo pages, uncustomized policy
  templates, or access/redirect placeholders. Original domains, saved final
  hosts and matched original/final URL pairs were checked against the frozen
  development pages and the 5.4w website-family/exact-URL/material-group audit.
  No shared substantive company, product or service text was identified in
  these recorded matches.
- Decisions for the 64: **64 INCLUDE, 0 EXCLUDE, 0 UNRESOLVED**. The two clear
  5.4w exclusions, `AT9030118034` (`kcu.at`) and `AT9150093010`
  (`bagspot.at`), remain excluded. The larger `hotel-in-graz.com` match is 16
  WordPress/theme demo items shared with `t4a-automation.com`; the 578-word
  `brantner.com`/`hmw-mobility.com` match is an uncustomized sample returns
  policy. Neither indicates a shared business. Matched pages were not removed.
- For 9 of the 64 companies, at least one corresponding development page was
  eligible for supervised frozen-model input; for 25, one was eligible for
  frozen preprocessing; for 63, one was visible in a historical calibration
  package. These are input/visibility checks, not proof of a nonzero model
  contribution. The generic matched text does not imply company identity.
- The separate review under
  `data/interim/calibration/ai_profile_overlap_review_v1/` contains a 64-row
  decision table, compact match provenance, summary and readable report.
  Exact membership/uniqueness and unchanged clear exclusions were checked.
  The 5.4w Batch files, membership and split assignments were not changed;
  approved inclusions require a later preparation step. The earlier
  near-duplicate search is not exhaustive, so this review does not establish
  absolute independence from every undiscovered content overlap.

## 2026-10-20: Milestone 5.4y - Finalized Validation Requests and Batch Adapter

- Finalized a new, separate run at
  `data/interim/calibration/ai_profile_validation_batch_v2_final/` with 1,081
  PROCESSABLE requests. The 64 reviewed generic-overlap companies were
  restored; `kcu.at` and `bagspot.at` remain quarantined, one company has no
  usable packaged page, and 416 NO_EVIDENCE companies remain outside requests.
  The 1,017 previous request bodies and split assignments were preserved.
  Restored website/content groups were assigned without a cross-split conflict:
  51 selection and 13 holdout additions, yielding 865 selection and 216
  holdout companies across 1,072 groups.
- Five Batch JSONL files contain 1,081 unique requests (104-282 per file,
  13.7-41.9 MB each). Estimated input is 42,999,019 local `o200k_base` tokens;
  a 20% queue-planning allowance is 51,598,822. Uncached short-context Luna
  Batch cost is estimated at $2.15 input plus $2.16 for the full 8k output
  allowance per request, $4.31 total. A 20%-higher-input sensitivity is about
  $5.88, including 71 requests potentially crossing 272k input tokens.
  These are not Luna-verified counts or bills. Known prior pilot usage is
  $0.4437; an ambiguous attempt and other account charges remain unresolved.
- The user reported a 1B-token/day Luna Batch allowance and empty queue on
  2026-10-20. This is not API-verified current queue availability. The explicit
  submit command validates local file/request identities, checks remote active
  jobs, records upload and Batch IDs immediately, refuses duplicate jobs, and
  requires reconciliation after an ambiguous create. Status shows remote
  request counts. Ingestion preserves raw output/errors, maps exact custom IDs,
  applies the approved v4 profile validator/mapping, leaves failed/missing/
  invalid requests unlabelled, and keeps holdout labels out of selection-only
  summaries. No upload or paid call was made in this ticket.
- The [Batch guide](https://developers.openai.com/api/docs/guides/batch) and
  [GPT-6 Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
  were rechecked on 2026-10-20. Focused mocked tests cover split conflicts,
  duplicate submission, ambiguous recovery, unknown result IDs, error/missing
  outcomes, and holdout separation. Offline request validation and completed
  preparation resume were checked. Before user submission, recheck the live
  Platform queue, account/spend settings and any unrelated active jobs.
  Near-duplicate independence screening remains non-exhaustive; no scorer
  cutoff or validation label has been chosen.

## 2026-10-21: Milestone 5.5a - Selection-Response Failure Diagnosis and Offline Recovery

- Milestone 5.5 is now the validation-analysis and selection-policy phase. The
  finalized Batch ingestion contains 865 selection responses: 843 originally
  valid (51 KEEP, 59 UNKNOWN, 733 EXCLUDE), 18 failed exact quotation checks,
  and 4 failed reference/contract checks. This ticket inspected only selection
  responses and their frozen requests; holdout labels were not opened.
- A separate, versioned review at
  `data/interim/calibration/ai_profile_validation_failure_review_v5/` accounts
  for all 22 failures. Three responses had exact quotations assigned to the
  wrong page; each unchanged quotation matched exactly one other supplied page.
  Correcting only those page IDs and revalidating the full responses recovered
  1 KEEP, 1 UNKNOWN and 1 EXCLUDE. Thus the review-only combined total is 846
  valid: 52 KEEP, 60 UNKNOWN, 734 EXCLUDE. Canonical results and labels were
  not replaced.
- Of the 19 unresolved responses, 3 have internal-whitespace quotation
  differences, 7 have partial wording overlap, 5 have quotations absent from
  supplied evidence under the diagnostic check, and 4 have substantive
  reference/contract issues. The approved exact/edge-whitespace validator was
  unchanged. All 22 responses were marked completed, used fewer than the
  8,000 permitted output tokens and had no output-limit truncation metadata.
  Unresolved cases require manual evidence/contract review or targeted retry;
  the triage identifies 14 for review before any retry and 5 for targeted
  retry. No API calls or retry requests were made.
- The review contains a 22-company table, unchanged quotation and cited-page
  diagnostics, original result/raw-response hashes, separate corrected
  profiles and labels, and a concise report. Focused tests check selection-only
  raw reading, failure classification, source preservation, all 22 cases and
  full-validator acceptance of every recovered result. Scorer comparison and
  cutoff choice remain for a later ticket.

## 2026-10-21: Milestone 5.5b - Selection-Set Scorer Comparison and Cutoff Candidates

- Analyzed only the 865 frozen selection companies, joining both saved frozen
  scorer outputs to the selection manifest and finalized per-company request
  measurements. Membership, score uniqueness, request hashes and 5.5a
  recovered-result source hashes were checked. The versioned label view has
  843 originally valid plus 3 separately recovered responses: 52 KEEP,
  60 UNKNOWN and 734 EXCLUDE; 19 remain unresolved. Canonical results were
  not changed, and holdout labels were not opened.
- The report and charts at
  `data/interim/calibration/company_score_analysis/independent_validation_selection_v3/`
  show label-specific score distributions, tie-inclusive cutoff curves,
  unresolved-label bounds, 95% descriptive Wilson intervals, equal-size
  shortlist comparisons and estimated same-setup Luna Batch costs. For the
  reference scorer, cutoffs 0/2/4 select 122/72/29 companies and capture
  40/31/16 observed KEEPs; labelled KEEP precision is 33.6%/44.3%/57.1%.
  For the cleaned combined scorer, cutoffs 0/1/2 select 130/81/49 and capture
  48/37/24 KEEPs; precision is 37.8%/47.4%/51.1%. The strict options are
  small; no candidate yet establishes convincingly high precision at broad
  KEEP coverage.
- At equal top-100 size the combined scorer captures 42 observed KEEPs
  versus 37 for the reference; at top 25 the reference has 15 versus 13.
  Only three unresolved cases enter the displayed cutoff shortlists and are
  identified for review. Costs use saved local token estimates, the frozen
  250k-evidence/8k-output request settings and the recorded Batch price
  schedule; they are not bills or population-spend projections. All results
  are unweighted, descriptive selection-set comparisons. No scorer or
  production cutoff was frozen; user review precedes any holdout evaluation.

## 2026-10-21: Milestone 5.5c - Usage-Based Costs and Conditional Budget Scenarios

- Used saved usage metadata for only the 865 selection responses, joined to
  the 5.5b versioned label/score view (52 KEEP, 60 UNKNOWN, 734 EXCLUDE,
  19 unresolved). All 865 had usage and returned `gpt-6-luna`. Selection
  input totalled 32,546,364 tokens, including 3,474,063 cached reads and
  29,069,706 cache writes; output totalled 437,843 billed tokens, including
  96,507 reasoning tokens already inside that output total. No request
  exceeded the 272k long-context pricing threshold.
- Using the official Luna Batch rates checked on 2026-10-21 and the default
  30-minute cache-write rate, calculated **selection-only** usage cost is
  $1.944. A flat ordinary-input-rate cache-write sensitivity is $1.580;
  neither is a confirmed charge. The user reported **$1.91 for all five
  original Batches across both splits**. This aggregate was not allocated
  to companies or selection results. The documented-rate selection
  calculation exceeds that reported full-run amount; billing/credits,
  processing terms and timing remain unreconciled.
- KEEP cases used more input and output tokens on average than EXCLUDEs;
  mean calculated KEEP cost was approximately 3.3 times EXCLUDE cost, with
  input dominating overall. The report, company-level cost table, all
  tie-inclusive score thresholds (including negative scores), cost curves,
  label summaries and $50/$100 tables are saved under
  `data/interim/calibration/company_score_analysis/independent_validation_usage_costs_v2/`.
- The acquisition frame has 165,722 eligible-domain companies; the 1,500
  firm pool has sector probabilities, but only 1,084 were PROCESSABLE and
  the later 865/216 split was group/sector balanced rather than a recorded
  independent probability draw. No population weights or AI recall are
  claimed. The budget tables assume a processable population with the same
  joint score/cost distribution as these selection companies, vary evidence
  availability and per-company cost, and hold back 20% ($10/$20) of the
  new $50/$100 budgets. They are conditional scenarios, not Austrian totals
  or a production policy. Historical results and holdout labels remain
  unchanged and unopened; no API calls, retraining or cutoff freeze occurred.

## 2026-10-22: Milestone 5.5d - Frozen Challenger Policies and Holdout Evaluation

- Before reading holdout labels or responses, froze `cleaned_grounded_combined`
  and three full-precision, tie-inclusive score cutoffs: 1.2235276873548995
  ($100), 0.2706790875316717 ($150), and -0.10212121864474141 ($200).
  Saved a dated policy manifest with model, score, split, contract and cost
  source hashes, metric/recovery rules, 20% reserve, and no minimum performance
  target. The freeze was preserved unchanged during evaluation.
- Opened the 216-company holdout only after the freeze. All had saved scores and
  usage. Approved validation found 208 originally valid responses; uniform
  offline review of eight exact-quote failures recovered two unique wrong-page
  citations without changing quotations or assessments. Six remain unresolved.
  The resulting review-only labels are 18 KEEP, 10 UNKNOWN, 182 EXCLUDE and
  six unresolved. Canonical responses and labels were not overwritten; no
  output-limit truncation was found among the eight failures.
- The $100/$150/$200 policies selected 26/37/44 holdout companies, respectively.
  Among valid-labelled selections they captured 13/15/16 of 18 holdout KEEPs
  (observed recall 72.2%/83.3%/88.9%) with confirmed-KEEP precision
  54.2%/44.1%/39.0%. Selected KEEP/UNKNOWN/EXCLUDE/unresolved counts were
  13/2/9/2, 15/2/17/3, and 16/2/23/3. The report gives extreme unresolved-label
  bounds and descriptive 95% website-group bootstrap intervals; with only 18
  labelled KEEPs, these are not population recall estimates.
- Calculated usage for the selected holdout requests was $0.2356/$0.2886/$0.3619,
  including input and billed output (reasoning not double-counted). These are
  sample costs, separate from the pre-evaluation conditional wider-frame
  projections of $77.66/$119.96/$130.65 before reserve. The $100/$150/$200
  names are scenario labels, not guaranteed spending limits. The earlier
  user-reported $1.91 full-Batch charge and calculated $1.944 selection-only
  usage remain unreconciled.
- Saved the frozen policy, separate recovery audit, company/policy tables,
  charts and cost-reconciled report under
  `data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/`.
  The initial result table's selection-cost placeholders were retained for
  provenance; `policy_results_v2.csv` and `report_v2.md` supersede them without
  altering holdout metrics or the freeze. Visual checks and focused regression
  tests passed. No new cutoff, model comparison, retraining, API call, or
  population inference was performed. The AI-profile labels remain LLM-derived,
  not independently verified ground truth.

## 2026-10-22: Milestone 5.5e - Closed Scorer Validation Analysis

- Consolidated the development, independent-pool, labeling-contract, frozen
  policy, selection and holdout results in
  [calibration_methodology.md](calibration_methodology.md). The existing
  [holdout report](../data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/report_v2.md)
  remains the detailed evaluation source; its `policy_freeze.json`,
  `policy_results_v2.csv`, recovery audit, charts and source hashes provide
  reproducibility. No analysis was regenerated and historical entries remain.
- The unchanged `cleaned_grounded_combined` model and all three full-precision
  $100/$150/$200 evaluation policies were frozen before holdout access. The
  865-company selection set has 52 labelled KEEPs; the 216-company holdout
  has 18. At the frozen cutoffs the holdout selected 26/37/44 companies,
  with observed KEEP recall 72.2%/83.3%/88.9% and confirmed-KEEP precision
  54.2%/44.1%/39.0%. The methodology keeps the selection results separate and
  records uncertainty and unresolved-label bounds for each policy.
- Validation analysis is closed without a post-holdout threshold choice or
  another training experiment. Genuinely unfinished checks include 19
  selection and six holdout unresolved labels, residual development-overlap
  and shared-website uncertainty, limited website evidence for some firms,
  independent assessment of LLM-label error, and reconciliation of calculated
  usage costs with the user-reported $1.91 balance deduction. These limitations
  preclude established population recall or spending estimates.
- The next decisions are the final deployment budget/policy and acquisition
  scale. Wider acquisition, final LLM ecosystem mapping and optional network
  construction have not yet been completed. This ticket changed documentation
  only; no model, source data, policy artifact or API request was changed.

## 2026-10-22: Milestone 5.5f - Validation Milestone Publication Checkpoint

- Reviewed the pending acquisition/discovery improvements, company-dataset and
  modeling tools, AI-profile validation tooling, versioned configurations and
  prompts, tests, and the completed milestone documentation for publication on
  the existing `main` branch. The remote `main` matched local HEAD before the
  checkpoint. This entry records publication scope; it does not change the
  frozen model, thresholds, labels or analysis results.
- Committed research code, configs, prompt/schema contracts, tests and
  methodological/status documentation are the public reproducibility layer.
  Licensed ORBIS/Aurelia data, the production database, acquired HTML,
  extracted text, model bundles, Batch requests/responses, company-level
  analyses and logs remain local under ignored `data/`, `db/` and `logs/` paths.
  The existing local analysis links in the methodology/status documents are
  explicitly described as non-public; exact policy thresholds and aggregate
  results are recorded in [calibration_methodology.md](calibration_methodology.md).
  The unrelated local dashboard workbook is not part of this checkpoint.
- Checked ignore/tracking rules and candidate file contents for credential
  patterns; no credentials were found in the reviewed publication paths.
  The complete unittest suite passed (354 tests). Changes were checked for
  whitespace errors and staged by explicit reviewed paths. No pipeline runs,
  model inference, API calls or research-data migration were performed.
