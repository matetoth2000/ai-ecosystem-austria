# Mapping the Austrian AI Ecosystem Using Web Data and NLP

This research repository implements company website acquisition → extraction →
frozen company scoring → evidence-grounded LLM profiling. Discovery prepares the
URL inventory; SQLite records membership, page metadata and run provenance, while
HTML, extracted evidence and research artifacts live in local files.

**Start here: [Run one company batch, step by step](docs/run_one_batch.md)** —
prerequisites, registered batches, optional pool setup, discovery, acquisition,
extraction, frozen scoring, exact-cutoff evidence preparation and paid Batch
submission/status/ingestion/reporting, with retry behavior for each stage.

**Current status (2026-11-12):** company-scorer validation and its analysis are
complete. The user chose the frozen **$200-scenario selection rule** for new scored
samples: `cleaned_grounded_combined >= -0.10212121864474141`, including ties.
This is a selection rule, not a $200 spending guarantee. Each profiling run needs
an explicit spending allowance and current queue allowance. Wider acquisition, deployment scale, final ecosystem mapping and optional network
construction remain future work.
LLM-derived labels and unresolved cases do not establish population recall. See
[validation findings and limitations](docs/calibration_methodology.md#2026-10-22-independent-company-scorer-validation-milestone-55e)
and the [chronological implementation record](docs/project_status.md).

## Installation and execution assumptions

Run commands from the repository root. The declared minimum is Python 3.11;
the reference installation uses **CPython 3.11.14 on macOS 15 arm64**. The code
locates `config/config.yaml` relative to its source checkout; some research tools
also resolve paths against the current working directory. Create a separate
environment rather than modifying an existing research `.venv`:

```bash
python3.11 -m venv venv/repro
source venv/repro/bin/activate
python -m pip install --no-cache-dir --disable-pip-version-check --constraint constraints/macos-arm64-py311.txt pip==25.3 setuptools==80.9.0
python -m pip install --no-cache-dir --disable-pip-version-check --no-build-isolation --constraint constraints/macos-arm64-py311.txt -e '.[analysis]'
python -m pip check
```

Base dependencies cover HTTP acquisition, HTML parsing, Trafilatura extraction,
OpenAI access, token allocation and JSON Schema validation. The `pipeline` extra
explicitly includes pandas, NumPy, SciPy, scikit-learn, PyArrow, joblib,
threadpoolctl and plotting required by scoring/profile research tooling. The
`analysis` extra includes `pipeline` plus notebook/report tools. Use `.[pipeline]`
instead of `.[analysis]` for the smaller operational environment; the documented
verification installation includes the full `analysis` extra. No separate
`jsonschema` workaround is needed. The optional `dotenv` import retains its
existing built-in `.env` fallback; python-dotenv is not required.

[constraints/macos-arm64-py311.txt](constraints/macos-arm64-py311.txt) pins the
working environment's dependency closure for these extras, plus pip/setuptools;
it does not freeze unrelated installed packages. Constraints do not install
unused packages. Numerical pins match the saved frozen-model metadata. Pinned
build tooling and `--no-build-isolation` avoid a separate unconstrained editable
build environment. This requires package-index access; it is not an offline
wheelhouse or a hash-locked supply-chain artifact. Other Python versions,
architectures and Linux/cloud environments are unverified. Experiment-resume
contracts also compare the original Python build string, not just package pins.

Token-budget preparation/tests also need tiktoken's `o200k_base` encoding data.
Populate a persistent ignored cache once during setup, with network access:

```bash
export TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken"
python -c 'import tiktoken; print(tiktoken.get_encoding("o200k_base").name)'
```

This fetches only the public `o200k_base.tiktoken` asset if absent; tiktoken checks
SHA-256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
Expected output is `o200k_base`. In each later shell, set the same
`TIKTOKEN_CACHE_DIR` after activating the environment; the same command and tests
then reuse the cached asset offline. Do not point it at an empty temporary cache.
This performs no model or token-count API call and needs no API key. On another
offline machine, supply the preserved cache directory rather than a new download.

For LLM operations, set `OPENAI_API_KEY` in the environment or an ignored local
`.env` file; [.env.example](.env.example) shows the format. Discovery/acquisition
make website requests. Profile preparation can make token-count API calls;
generation/Batch submission can incur charges. Frozen scoring is offline.

Storage assumes a writable local or mounted filesystem, SQLite, atomic renames
and temporary working space. The Batch adapter uses POSIX file locking. Object-store
URIs are not supported directly. Some historical pilot source references contain
absolute developer-machine paths, so relocating a checkout alone is insufficient.

## Reusable acquisition, extraction and scoring

[config/config.yaml](config/config.yaml) sets the database path (default
`db/ai_ecosystem.sqlite`), discovery/acquisition settings and extraction version.
For a new research database, initialize and import an authorized ORBIS/Aurelia
semicolon-separated export, then categorize its domains:

```bash
python -m ai_ecosystem.db.init_db
python -m ai_ecosystem.ingestion.import_orbis_aurelia --input data/raw/orbis_aurelia/your_local_file.txt
python -m ai_ecosystem.ingestion.categorize_domains
```

The configured input filename is a placeholder unless overridden with `--input`.
Preserve an existing research database: initialization/import is not a substitute
for its saved samples, page IDs, calibration results and provenance.

The following examples use an **existing registered** `your_sample_id`; replace
it and choose a new output filename. `discovery.import_sample` registers domain
CSVs using `--sample-id`, `--csv` and `--sample-name`;
`calibration.build_acquisition_pool` creates/extends company pools using frozen
development exclusions. Sample design and acquisition scale are separate decisions.

```bash
python -m ai_ecosystem.discovery.import_sample --list
python -m ai_ecosystem.discovery.homepage_discovery --sample-id your_sample_id
python -m ai_ecosystem.acquisition.run_acquisition --sample-id your_sample_id --fetch-scope discovered
python -m ai_ecosystem.extraction.run_extraction --sample-id your_sample_id
python -m ai_ecosystem.calibration.score_company_sample --sample-id your_sample_id --output data/interim/calibration/company_scores/your_sample_id.csv
```

Acquisition uses `discovered_pages` as its URL inventory and imports essential
fetch/storage helpers from `acquisition/html_fetcher.py`. The retained-candidate
limit does not cap acquisition with `--fetch-scope discovered`. Existing successes
are skipped by default. Extraction writes canonical `page.md`, `page.txt` and
`metadata.json` packages under `data/extracted/pages/` plus database features/links.
Its `--sample-id` selects a generic domain sample; `--sample` instead selects a
legacy page-validation sample.

The scorer loads both frozen models without fitting and writes signed decision
scores, evidence states and reasons. Scores are not probabilities or final
KEEP/EXCLUDE decisions; NO_EVIDENCE remains unscored. Explicit company membership
is preferred; legacy domain samples expand to associated companies. Missing
required text fails explicitly, and existing score outputs are never overwritten.
New-company inference needs no historical LLM labels/packages, scorer-v1 pass or
learned page-ranker pass, but still requires the frozen overlap-context dataset.

## LLM profiling and validation-specific workflows

Profiling uses versioned evidence packages, prompt/schema contracts, quotation and
reference checks, and derived label mappings in `src/ai_ecosystem/calibration/`.
The current validation contract is `company_ai_profile_v4` /
`company_ai_profile_mapping_v4`, configured in
[config/company_ai_profile_pilot_v5.yaml](config/company_ai_profile_pilot_v5.yaml).
Earlier versions remain necessary to read historical requests and results.

For a registered scored sample, use **`run_company_profile_batch`** and the
[complete batch walkthrough](docs/run_one_batch.md#8-cutoff-selection-and-evidence-preparation-offline).
It reads the exact policy from the preserved `policy_freeze.json`, validates score
membership, prepares frozen evidence/requests offline, and reuses the existing
Batch engine. `prepare`, `dry-run` and `report` are offline with the tokenizer cache
already populated; `submit`, `status`, `ingest` and `reconcile` contact OpenAI.
Submission additionally needs `--confirm-paid`. Local token/cost estimates and
the spending guard are not provider-enforced billing caps. The 250k evidence
allocation uses the same unverified local proxy as the saved bulk study; output
is limited to 8k tokens. No selection/holdout split or overlap-review decision
table is required. Missing scores/evidence and failed responses stay unlabelled.

The following runners retain their historical pilot/validation scope:

| Entry point (`python -m ai_ecosystem.calibration.…`) | Scope |
|---|---|
| `run_company_ai_profile_pilot` | Fixed pilot preparation/reruns, ordinary `run`/`resume`, offline revalidation and reports. Default artifact directory is the historical v3 product-priority pilot, not the latest contract; select prepared artifacts explicitly with `--directory`. |
| `compare_ai_profile_budgets` | Saved independent-pool evidence-budget study. |
| `prepare_ai_profile_validation_batch` | Frozen pool, saved budget/page selection, development-overlap audit and study splits. |
| `finalize_ai_profile_validation_batch` | Approved overlap review plus previous preparation; produces the final validation run. |
| `run_ai_profile_validation_batch` | Prepared Batch validation, submission/status/ingestion and selection-only reporting. |
| `analyze_validation_selection`, `analyze_validation_usage_costs`, `evaluate_validation_holdout` | Saved selection labels, usage and pre-frozen holdout policies; fixed study paths and source hashes. |

These offline inspections require preserved local artifacts:

```bash
python -m ai_ecosystem.calibration.run_ai_profile_validation_batch --run-dir data/interim/calibration/ai_profile_validation_batch_v2_final dry-run
python -m ai_ecosystem.calibration.run_ai_profile_validation_batch --run-dir data/interim/calibration/ai_profile_validation_batch_v2_final report
```

The adapter's `submit --confirm-paid`, `status`, `ingest` and `reconcile` commands
access the API. Pilot `prepare`/`prepare-rerun` can access the token-count endpoint;
`run`/`resume` generate profiles. The completed validation does not need
resubmission. See the [methodology](docs/calibration_methodology.md#2026-10-22-independent-company-scorer-validation-milestone-55e)
for policies, uncertainty, recovery rules and results. Study defaults are not a
final deployment policy.

## Repository map and preservation boundaries

Module paths in this table are under `src/ai_ecosystem/`; shared experiment
filenames are all within `calibration/`. Tests, notebooks and documentation paths
are relative to the repository root.

| Area | Contents and role |
|---|---|
| Active pipeline | `db/`, `ingestion/`, `discovery/`, `acquisition/`, `extraction/`; `calibration/score_company_sample.py`, `calibration/run_company_profile_batch.py` and reused packaging/Batch helpers. |
| Historical experiment runners | `scoring/` lexical baseline/evaluation; `validation/` retention studies; `calibration/run_company_calibration.py`, `run_page_annotation.py`, `run_page_model_experiments.py`, `run_page_model_improvements.py`, `run_locked_retrieval_evaluation.py`, and `run_company_model_{experiments,tuning,cleanup}.py`. Their configs/results retain research provenance. |
| Shared experiment code required by frozen inference | `calibration/company_modeling_{common,sources,features,groups,preprocessing}.py`, `company_experiment_features.py`, `company_tuning_{features,contract}.py`, `company_cleanup_features.py`, `attribution_features.py`, `grounding.py`, and `build_company_evidence_packages.py`. Imports and serialized project classes make moving/renaming these risky. |
| Committed contracts/configuration | Repository `config/` and `calibration/prompts/`, including every versioned prompt/schema. Identical schema bytes do not make contract-specific filenames interchangeable. |
| Tests and notebooks | `tests/`; `notebooks/00_orbis_aurelia_import_inspection.ipynb` and `06_company_calibration_analysis.ipynb`. |
| Detailed documentation | [Calibration methodology](docs/calibration_methodology.md), [historical readiness checkpoint and scoring handoff](docs/independent_validation_readiness.md), [project status](docs/project_status.md), `project_logs/` and knowledge-base design reports in `docs/`. Dated recommendations describe their historical checkpoints. |

The following **local inputs/artifacts are absent from Git**. A public checkout
alone cannot score companies or reproduce saved results. Obtain inputs through
authorized research storage; ORBIS/Aurelia access is restricted.

| Local path | Preserve because |
|---|---|
| `data/raw/orbis_aurelia/`, `data/raw/html/` | Licensed source records and acquired gzip HTML. |
| `db/ai_ecosystem.sqlite` and any transaction sidecars | Membership, company/page IDs, source paths, extraction metadata and run/results history. |
| `data/extracted/pages/` | Canonical evidence files referenced by SQLite. |
| `data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen/` | Both `reference_logistic_size` and `cleaned_grounded_combined` bundles, manifests, completion hashes and reload checks. |
| `data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1/` | Inference reads `companies.parquet`, `company_websites.parquet`, `pages.parquet`, `contents.parquet`, `manifest.json` and `config_snapshot.json`. Other tables preserve development/reproduction evidence. The dataset path is embedded in model provenance. |
| Remaining `data/interim/` and `logs/` | Samples, experiment caches/parent contracts, snapshots, scores, packages, Batch requests/responses, overlap/failure reviews, cost reports and provenance. Later pilots reference v3 packages; final validation preparation depends on its predecessor. |

`reports/` holds presentation artifacts; the existing untracked dashboard workbook
is separate from the pipeline and is not part of the committed research artifacts.
Ignoring an artifact does not make it disposable. Moving evidence breaks stored
paths, deleting caches can break reproduction, and changing contract/model bytes
can break frozen hash checks. This organizational cleanup does not reclaim the
large corpus/model-storage footprint.

## Verification

```bash
python -m unittest discover -s tests
```

The suite includes synthetic/mocked acquisition, extraction, frozen-transform and
profile-contract tests. Saved holdout regression checks additionally require local
artifacts and skip when their report is absent. Historical parity records and
frozen evaluation sources are described in the methodology; passing tests without
those files is not a reproduction of all published scores.

Reference verification on 2026-11-11 used a separate macOS arm64 / CPython 3.11.14
environment installed with the commands above: `pip check` passed, all 354 tests
passed with **zero skips**, and both frozen models reproduced 649 saved development
scores exactly (maximum difference 0). Network access and repository writes were
blocked during tests/parity; outputs were temporary. Five saved-artifact regression
checks ran because the local company dataset and validation selection/recovery/
usage/holdout artifacts were available; they are not self-contained public-data
tests. See the [verification record](docs/project_status.md#2026-11-11-reproducible-installation-and-dependency-verification).
No live batch or Linux/cloud execution was tested. Installation verification does
not establish acquisition runtime or disk capacity for an overnight trial.
