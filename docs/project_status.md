# Project Status

This file records completed implementation milestones. Future Codex milestones should update this file as part of the definition of done.

Current direction (2026-09-12): Milestone 5 now prioritizes company AI-promise
modeling. Further page-ranker optimization is paused. The immediate next milestone
is **Company Training Dataset and Evaluation Protocol** using existing calibration.
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
