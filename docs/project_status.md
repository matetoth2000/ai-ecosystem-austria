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
