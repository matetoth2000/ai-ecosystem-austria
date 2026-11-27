# Run one company batch through LLM profiling

This guide follows the implemented commands as of 2026-11-19. Run them from the
repository root. The consistent example is the registered fresh-acquisition trial
`fresh_acquisition_trial_20261119_v1` (100 companies, 100 distinct normalized
domains; seed `20261119`). It is a standalone company sample, separate from the
historical `independent_validation_v1` pool. Substitute another **registered**
batch ID everywhere when processing it.

A company acquisition batch is a local sample of up to 100 legal companies.
It is **not an OpenAI Batch job**: the steps below discover websites, acquire HTML,
extract text and compute local company scores. Steps 8–12 then prepare and process
OpenAI Batch jobs for only the selected companies.
Different companies/batches can share a website; discovery, acquisition and
extraction operate on the batch's deduplicated domain projection, whereas scoring
returns the selected legal companies.

For an existing batch, follow steps 1–2, **skip step 3**, then follow steps 4–12.
Commands in this guide describe future operations; pool setup and pipeline
commands change local state, and discovery/acquisition make website requests.

## 1. Environment and local prerequisites

Activate an installed source-checkout environment and inspect the required packages.

```bash
source venv/repro/bin/activate
export TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken"
python --version
python -m pip check
python -m pip show ai-ecosystem-austria numpy pandas scipy scikit-learn pyarrow joblib threadpoolctl jsonschema
```

**Expected output/completion:** the reference Python 3.11.14, `No broken requirements
found.` and package metadata for each name, without missing-package warnings.
This checks installation only, not model
compatibility or evidence completeness. For first-time environment creation, use
the [README installation instructions](../README.md#installation-and-execution-assumptions),
using the committed constraints and the `analysis` extra (which includes the
`pipeline` extra). The smaller `pipeline` extra also covers these stages. This
procedure creates `venv/repro` and preserves the working `.venv`; do not replace
an existing environment merely to follow this example. `jsonschema`, joblib and
threadpoolctl are now explicit dependencies, with no manual workaround. No OpenAI key is needed for steps 1–9 or offline reporting. Populate the persistent
tokenizer cache during setup for offline tests and evidence preparation. Submission,
status, ingestion and reconciliation need `OPENAI_API_KEY` in the environment or
ignored `.env`, as documented in the README.

Before continuing, obtain the following preserved local inputs from authorized
research storage; Git alone does not include them:

| Input | Required location/condition |
|---|---|
| Research database | `database.path` in [config/config.yaml](../config/config.yaml), normally `db/ai_ecosystem.sqlite`; initialized with imported firms, categorized domains and sample-registry tables. Preserve the existing database and transaction sidecars. |
| Frozen models | `data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen/`, with both `reference_logistic_size/` and `cleaned_grounded_combined/`, their `model.joblib`, `complete.json` and files identified by each completion marker. Keep manifests and reload checks. |
| Frozen scoring context | `data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1/`: `manifest.json`, `config_snapshot.json`, `companies.parquet`, `company_websites.parquet`, `pages.parquet`, `contents.parquet`. The dataset location is embedded in model provenance and its hashes/configuration are checked. |
| Additional pool-creation inputs | The same development dataset also needs `websites.parquet`; the builder checks the frozen inventory of exactly 649 development companies. Extension needs its saved pool frame and all referenced revision artifacts. |
| Existing corpus | Preserve `data/raw/html/` and `data/extracted/pages/` files referenced by the database. Later steps add to these directories. New-company scoring does not require historical calibration labels/packages or a new scorer-v1/page-ranker pass. |
| Runtime storage/source | Writable database, `logs/`, raw/extracted/output directories and temporary-file space; retained Python modules under `src/ai_ecosystem/` for imports and serialized model classes. |

**Retry/overwrite:** activation and package inspection can be repeated. Do not
recreate the research database to fix missing artifacts. There is no combined
preflight command that certifies all prerequisites. The reference constraints
preserve saved numerical-library versions, but do not establish Linux or other
Python-build compatibility; keep original versions and paths when reproducing
results. See the [preservation map](../README.md#repository-map-and-preservation-boundaries).

The constrained full installation and all 354 tests were verified in a separate
temporary macOS arm64 / CPython 3.11.14 environment; both frozen bundles matched
saved development scores exactly. This verifies software setup, not the runtime,
network success or available disk headroom for a new overnight batch. Check storage
before acquisition. Preserve the documented **8 GB free-space floor** and a
separate **4 GB working/headroom reserve** for transient growth; see the
[storage planning assumptions](independent_validation_readiness.md#batch-resource-controls-and-next-ticket).
The latest 2026-11-19 registration check found **10.23 GB free (9.52 GiB)**:
above the 8 GB floor, but only 2.23 GB remains before reaching it, less than
the 4 GB reserve. Registration does not establish sufficient acquisition
headroom; restore and recheck space before starting the trial. See the [installation verification record](project_status.md#2026-11-11-reproducible-installation-and-dependency-verification).

## 2. Identify an existing registered batch

List registered samples and locate `fresh_acquisition_trial_20261119_v1`.

```bash
python -m ai_ecosystem.discovery.import_sample --list
```

**Expected output/completion:** a table headed
`sample_id`, `sample_name`, `sample_size`, `created_at`, including the exact batch
ID. This command lists all samples; it has no batch-filter option. Its listing
path can initialize/migrate the registry schema, so it is not a strict read-only
database inspector.

With the optional SQLite CLI installed, inspect that batch's unit and membership
counts without registering anything (change the database filename if configured
differently):

```bash
sqlite3 -readonly -header -column db/ai_ecosystem.sqlite "
SELECT sample_id, sample_unit, sample_size, domain_count,
       (SELECT COUNT(*) FROM sample_companies c WHERE c.sample_id=s.sample_id) AS company_rows,
       (SELECT COUNT(*) FROM sample_domains d WHERE d.sample_id=s.sample_id) AS domain_rows
FROM sample_sets s
WHERE sample_id='fresh_acquisition_trial_20261119_v1';"
```

**Expected output/completion:** one row with `sample_unit=company`, company count
matching `sample_size` (100 for an original full batch), and domain count matching
`domain_count`. Shared domains can make the domain count smaller. An absent row
means the ID is not registered; stop rather than running a stage without
`--sample-id`. These counts establish registration, not acquisition progress.

**Retry/overwrite:** repeat the listing/inspection as needed; neither imports a
CSV. Do not re-import a pool batch with `discovery.import_sample`: that tool
registers domain samples, replaces their domain membership and can overwrite
existing metadata; it does not create the builder's explicit company membership.

## 3. One-time trial registration or historical pool setup (skip for an existing batch)

### Register a fresh 100-company trial

The example `fresh_acquisition_trial_20261119_v1` is already registered: **do not
rerun its registration**. For a separately authorized future trial, choose a new
ID and first preview selection without writing files or database rows:

```bash
python -m ai_ecosystem.calibration.build_acquisition_pool trial --sample-id fresh_acquisition_trial_NEXT_v1 --seed 20261119 --target-size 100 --dry-run
```

Then register that exact selection against the current database:

```bash
python -m ai_ecosystem.calibration.build_acquisition_pool trial --sample-id fresh_acquisition_trial_NEXT_v1 --seed 20261119 --target-size 100
```

This selects exactly 100 distinct normalized eligible domains by seeded SHA-256
rank, then one company per domain by a separate seeded rank. It uses no model
scores, AI keywords or sector quotas. It reuses frozen development-company and
website-family exclusions, and additionally excludes existing sample/calibration
company IDs, all registered sample domains, any recorded discovery/acquisition
host (including failed attempts and known redirects), existing raw-HTML domain
directories and companies with additional validated primary website aliases.
Names and registered eligible domain metadata must exist. This establishes known
local freshness; unseen live redirects cannot be ruled out before discovery.

**Expected output/completion:** preview JSON has `registered=false`; successful
registration has `registered=true`, `companies=100`, `domains=100`, seed and
selection hash. It writes only the new sample registry membership and
`data/interim/calibration/acquisition_trials/<sample-id>/companies.csv` plus
`audit.json`; it neither creates nor extends a validation pool. No separate CSV
import is necessary. Verify the registered company/domain counts using step 2.

**Retry/overwrite:** requires an explicit nonnegative integer seed and size 100;
refuses an existing sample ID or artifact directory. There is no overwrite/resume
flag. A failed transaction rolls back registry changes but can leave new orphan
artifacts; files alone do not establish registration. A dry-run does not reserve
the selection: changes to the eligible source database can change a later draw.

### Historical validation pool creation/extension


The current builder is a **validation-study sampler**, not an approved
full-population deployment design. It excludes missing/excluded domains, all 649
development companies and overlapping development website families. It allocates
across broad sectors using an 80% proportional / 20% equal-sector mixture.
These rules are applied even if a different pool name is supplied.

### Create a pool and register its batches

This is the historical example for a time when neither this pool ID nor its
artifact directory exists; do not rerun it to process an existing batch.

```bash
python -m ai_ecosystem.calibration.build_acquisition_pool create --pool-id independent_validation_v1 --target-size 1500 --seed 20261006
```

Its purpose is to freeze the eligible frame, select companies and register the
pool plus its company batches in the existing initialized database.

**Expected output/completion:** final JSON includes `pool_id`, `revision`,
`companies`, `batches`, `artifacts` and `first_batch`. For this successful example,
there are 1,500 companies and 15 batches, starting with
`independent_validation_v1_batch_0001`. Artifacts are under
`data/interim/calibration/acquisition_pools/independent_validation_v1/`: `frame.json`
and `revisions/v000001/` with `state.json`, `pool.csv`, per-batch CSVs, `audit.json`,
unused-sector inventory and prefix probabilities. Registry tables are
`sample_sets`, `sample_companies` and `sample_domains`; **no separate registration
command is needed**. Confirm the batch with step 2 after successful return.

**Retry/overwrite:** `create` refuses an existing pool ID, artifact directory or
colliding sample ID. There is no `--resume` or `--force` option. An exception rolls
back database registration, but files written before interruption can remain as
unregistered artifacts. Do not assume files alone mean registration completed;
there is no automatic orphan-artifact recovery command.

### Extend a saved pool without redrawing old batches

If the saved pool currently has 1,500 companies and an extension has separately
been chosen, this requests an absolute total of 1,600, not 1,600 additional firms:

```bash
python -m ai_ecosystem.calibration.build_acquisition_pool extend --pool-id independent_validation_v1 --target-size 1600
```

Its purpose is to append companies and new batches using the original frame,
seed and ordering while preserving previously registered batch rows.

**Expected output/completion:** final JSON reports the new total and revision,
and the saved registry is validated before commit. In this exact 1,500→1,600
example, revision `v000002` adds `independent_validation_v1_batch_0016`; batch
`0001` remains unchanged. Smaller creation/extension waves can end in a partial
batch; extension starts new batch numbers rather than filling old partial ones.

**Retry/overwrite:** the total must exceed the saved size and fit the frozen
eligible capacity. Extension loads the original artifact directory and cannot
override `--seed`, `--output-directory` or `--development-directory`. Missing,
changed-domain or newly excluded selected firms cause an error rather than a
replacement draw. Repeating the same successful target is rejected. Interrupted
revision directories can block retry; automatic recovery is not implemented.

The commands below process only the registered fresh trial; registration, pool
creation and extension themselves never run discovery, acquisition, extraction
or scoring.

## 4. Homepage discovery

```bash
python -m ai_ecosystem.discovery.homepage_discovery --sample-id fresh_acquisition_trial_20261119_v1 --max-workers 20
```

This discovers homepage, robots/sitemap and linked URL inventory for the batch's
registered domains.

**Expected output/completion:** wait for `Homepage discovery summary`, the run ID,
domain success/failure counts and URL counts. SQLite receives a `discovery_runs`
record and page inventory. Logs are `logs/discovery_<run_id>.log`; a successful
summary write also prints `Summary JSON:` pointing to
`logs/discovery_<run_id>_summary.json`. Summary-file writing can fail separately
and is logged; the terminal summary still appears. Review failures before moving
on: a completed invocation is not proof that every company has discoverable URLs.

**Retry/overwrite:** rerun the same command to refresh all batch domains; there
is no checkpoint-resume or success-skipping switch. Homepage fetching tries HTTPS
then HTTP with its built-in retry loop. Refresh deletes only eligible unreferenced
discovery/candidate rows and preserves acquired/validation-referenced rows and
their provenance. It can change the unprotected URL inventory. Shared websites
can affect another batch's inventory too. Acquisition selects inventory across
runs, not just the discovery run most recently printed.

## 5. HTML acquisition

```bash
python -m ai_ecosystem.acquisition.run_acquisition --sample-id fresh_acquisition_trial_20261119_v1 --fetch-scope discovered --max-workers 20
```

This fetches the batch's discovered URLs and stores HTML plus acquisition metadata.

**Expected output/completion:** wait for `Acquisition summary`, run ID, selected,
attempted, fetched, failed, skipped and deferred counts, and `Log path:`. HTML is
stored at `data/raw/html/<domain>/<url_hash>.html.gz`; `scraped_pages` and
`acquisition_runs` are updated. Read failed/deferred details in
`logs/acquisition_<run_id>.log`. Fetched counts can include non-HTML responses;
only successful HTML is eligible for extraction. `--fetch-scope discovered` is
not capped by the retained-candidate limit of 20.

**Retry/overwrite:** with current configuration, rerunning skips recorded
successful HTML, retries failed/unattempted URLs, and creates a new run ID.
Request exceptions have two retries after the first attempt under the configured
15-second timeout; HTTP error responses are recorded rather than retried by that
exception loop. Five consecutive exhausted connection failures latch a run-local
domain deferral; in-flight requests finish, and later invocations start a fresh
guard. Deferred URLs remain discovered and receive no acquisition row for that
deferral. There is no `--resume` flag. `--force-refresh` bypasses success skipping
and can replace stored HTML/metadata; it is not needed for ordinary retries.
Success skipping checks database status/path metadata, not actual file existence.
An interrupted/exception summary is not a normal completed run; completed page
writes remain available for the next invocation.

## 6. Text extraction

```bash
python -m ai_ecosystem.extraction.run_extraction --sample-id fresh_acquisition_trial_20261119_v1
```

This applies Trafilatura to successful acquired HTML and persists canonical text,
Markdown, metadata, page features and links for the batch's domains.

**Expected output/completion:** progress ends in `Extraction summary` with run ID,
version, selected/processed/skipped/successful/failed counts and runtime. Evidence
is stored at `data/extracted/pages/<shard>/<scraped_page_id>/` as `page.md`,
`page.txt` and `metadata.json`. SQLite records `extracted_pages`, `page_features`,
`page_links` and `extraction_runs`. A batch with failed/deferred acquisition has
fewer inputs; successful extraction does not repair that earlier coverage gap.

**Retry/overwrite:** results commit per page. Rerun this command after interruption
or failures; compatible successful extraction records with paths/hash and matching
features are skipped, while failed/incomplete records are retried. There is no
`--resume` flag or within-page extraction retry loop. The skip check does not
verify evidence files exist on disk. `--force-refresh` reprocesses successes;
processed pages replace deterministic evidence files and upsert metadata/features/
links. The configured `overwrite_existing: false` is not a separate file-level
protection in this writer. Keep the current extraction version/settings for frozen
scoring. Use `--sample-id` here, not the legacy page-validation `--sample`.

Record the batch ID alongside the printed extraction run ID: the database's
legacy `extraction_runs.sample_id` receives `--sample`, not this generic
`--sample-id`; it therefore does not by itself identify the company batch.

## 7. Frozen company scoring

```bash
python -m ai_ecosystem.calibration.score_company_sample --sample-id fresh_acquisition_trial_20261119_v1 --output data/interim/calibration/company_scores/fresh_acquisition_trial_20261119_v1_v1.csv
```

This prepares the registered companies' current evidence and computes both frozen
model scores without training or applying an exclusion cutoff.

**Expected output/completion:** elapsed-time messages report sample resolution,
model/context loading, source loading, feature preparation and each model. Normal
completion prints `CSV written:` followed by `Wrote <N> companies ...; states:`.
The CSV contains `bvd_id`, `state`, `reason`, `predictive_content_available`,
`reference_logistic_size` and `cleaned_grounded_combined`. For a full registered
100-company batch, expect 100 rows, with NO_EVIDENCE companies retained but their
scores blank. Scores are decision values, not probabilities or KEEP/EXCLUDE
labels. A missing required text file or invalid model/context hash causes an
error rather than silent scoring. Source SQLite is read-only for this stage.

**Retry/overwrite:** there is no incremental resume, `--force` or overwrite flag.
Rerunning recomputes the batch. Any existing output is refused, including a
possibly partial CSV left by interruption during writing; file existence alone
does not establish completion. Preserve/review an existing result and choose a
new filename such as `fresh_acquisition_trial_20261119_v1_v2.csv` for a repeat.
The program supports `--database` and `--frozen-directory` overrides, but relocating
models does not independently relocate the dataset embedded in their provenance.

## 8. Cutoff selection and evidence preparation (offline)

The user chose the frozen **$200-scenario policy** for this workflow. It selects
`PROCESSABLE` companies whose `cleaned_grounded_combined` score is at least
**`-0.10212121864474141`**, including ties. The exact value and SHA-256 are read
from `data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/policy_freeze.json`
and saved with the run. Obtain that preserved policy file if it is absent; its
referenced historical company-score tables are not required here. Do not substitute
the rounded `-0.102`, reclassify unscored companies as negative, or interpret the
scenario name as a guaranteed $200 bill.

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1 prepare --sample-id fresh_acquisition_trial_20261119_v1 --scores data/interim/calibration/company_scores/fresh_acquisition_trial_20261119_v1_v1.csv --spend-allowance-usd 2.00 --queue-allowance-tokens 1000000000
```

This verifies registered membership and score consistency, selects companies,
and freezes evidence packages and Batch requests in a **new** run directory.
The $2.00 spending allowance and 1-billion queued-token allowance are explicit
example inputs: choose a deliberate run allowance and verify your current account
queue allowance before using them. They are not automatically inherited from the
old validation study or guaranteed by the $200-scenario policy.

**Expected output/completion:** a summary JSON printed after preparation and
validation. Review `sample_companies`, `selected`, `included` (actual requests),
`screened_out_below_cutoff`, `unscored`, `selection_reasons`,
`selected_without_request`, evidence `coverage`, estimated costs, queue headroom,
file constraints and `submission_blocked`/`blockers`. The run directory contains
`input_scores.csv`, the policy snapshot, `company_manifest.jsonl`, config/prompt/
schema snapshots, `packages/`, `work/`, `batch_XXXX.jsonl`, `summary.json` and the
immutable-file hashes in `complete.json`. It also writes initial `report.md`,
`report.json` and `company_results.csv` with pending/unselected outcomes.

Rows outside the sample, duplicate IDs, invalid states, nonfinite scores and
NO_EVIDENCE rows with scores are rejected. Missing score rows or blank scores are
recorded as unscored, not EXCLUDE. Selected companies with no usable packaged pages
remain unresolved and do not create requests. Zero selections create an empty,
reportable run with no API client or request submission.

Packaging reuses the current v4 prompt/schema and Python attribution/label checks,
Luna configuration, homepage/about/product/service ordering, content deduplication,
utility-page exclusions, **250,000 local evidence-token allocation** and **8,000
output-token allowance**. Model-facing inputs contain no selector scores, cutoff
labels, sectors or train/holdout splits. The source database and extracted text
are read only; no historical validation cohort or overlap-review table is needed.

Preparation makes **no token-count or model API call** and refuses a missing or
invalid tokenizer cache rather than downloading it. `o200k_base` counts are the
same provisional local proxy used by the existing bulk-validation preparation,
not Luna-verified counts; actual evidence/input tokens may differ. The report
shows the recorded pricing schedule, uncached Batch input estimate, full output
allowance and a 20%-higher-input sensitivity. Cache writes, long-context effects,
provider changes, retries and other account spending can change actual charges.
The local spending check uses the sensitivity estimate; it is not a provider
billing cap or a population spending guarantee.

**Retry/overwrite:** an existing run directory is refused unless the same `prepare`
command is repeated with `--resume`. This verifies the same sample, scores, policy,
allowances, configuration and source evidence, reuses matching request checkpoints
and completes compatible interrupted preparation. Source/identity changes or
incompatible partial files fail closed; preserve them and investigate. Completed
runs are hash-verified rather than regenerated. There is no force/overwrite flag.
Do not replace a partially submitted run with a newly prepared directory to retry
remote work; use its existing state and per-file commands below.

## 9. Review the prepared run (offline dry-run)

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1 dry-run
```

This validates frozen file/request identities and prints the submission summary
without source-database access or network calls.

**Expected output/completion:** the validated summary prints selected/request
counts, omissions/truncations, Luna settings, exact policy/hash, estimated cost,
explicit spending/queue allowances, queue headroom and file limits. The existing
adapter uses at most 50,000 requests/200,000,000 bytes per file, with conservative
packing targets of 100,000,000 bytes and 10,000,000 estimated input tokens per file.
These are local adapter constraints; recheck current Platform/account limits.

**Retry/overwrite:** repeat freely; no frozen file is overwritten. Hash errors
must be investigated. A valid dry-run can still show `submission_blocked=true`
when a spending or queue estimate exceeds the declared allowance; it does not
silently raise either allowance. Before any upload, a deliberately changed
allowance requires a new preparation directory, with care to avoid duplicate paid
runs. A dry-run is not approval or a guarantee of provider capacity/billing.

## 10. Submit the prepared OpenAI Batch jobs (external, paid)

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1 submit --confirm-paid
```

This first prints the preflight summary, then checks remote jobs, uploads the
frozen JSONL files and creates Batch jobs only when the local guards pass.

**Expected output/completion:** per-file Batch IDs and remote statuses are printed;
`submission/` records upload IDs, job IDs and phases immediately. This means jobs
were accepted for processing, **not** that profiles are complete. The adapter
requests a `24h` completion window; do not promise overnight completion or assume
that every request will succeed.

**Retry/overwrite:** `--confirm-paid` is mandatory, separate from the frozen
per-run spending allowance. A local file lock and remote/local identity checks
prevent duplicate submission. Other active jobs block submission until queue
availability is reviewed. Existing submitted jobs are never automatically resent.
For a multi-file run interrupted after some files were submitted, check status,
then submit only the still-unsubmitted file (use its actual filename):

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1 submit --file batch_0002.jsonl --confirm-paid
```

A timeout during Batch creation can have an ambiguous outcome. Do not resubmit or
edit submission JSON manually; use reconciliation after checking the provider:

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1 reconcile --file batch_0001.jsonl
```

This contacts OpenAI and binds a matching remote job. Only after independently
confirming that no job exists, repeat with `--confirm-remote-absent` to reset the
ambiguous phase and permit a retry using the recorded upload. Reconciliation does
not itself generate profiles. Zero-request runs return without API calls.

## 11. Return later for status and ingestion (external)

From a fresh terminal, change to your checkout and activate the environment
you used for preparation. This example uses the Mac trial's checkout and
`.venv`. If you installed the environment from step 1, activate
`venv/repro/bin/activate` instead. Set `RUN_DIR` to your prepared run directory
if it differs from the example. Status and ingestion use `OPENAI_API_KEY`
from the environment or the checkout's ignored `.env`.

```bash
cd "$HOME/TU_Data_Science_MSc/ai-ecosystem-austria"
source .venv/bin/activate
export TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken"
export RUN_DIR="$PWD/data/interim/calibration/company_profiles/fresh_acquisition_trial_20261119_v1_200scenario_v1"

python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir "$RUN_DIR" status
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir "$RUN_DIR" ingest
```

Status inspects the saved remote jobs; ingestion downloads terminal outputs/errors,
checks their identities, validates profiles and derives KEEP/UNKNOWN/EXCLUDE in
Python using the existing contract.

**Expected output/completion:** status prints remote state and request counts.
Ingestion preserves downloads under `raw_results/`, per-request outcomes under
`results/`, and updates the local report. Nonterminal requests stay pending: return
later and repeat these commands. Terminal jobs can contain missing, failed,
refused, incomplete or invalid responses; those remain visibly unresolved with
no substantive label. A selected request is not automatically KEEP, and an
unselected/unscored company is not automatically EXCLUDE.

**Retry/overwrite:** both commands can be repeated; ingestion reuses matching
results and refuses changed downloads, unknown/duplicate custom IDs or conflicting
saved identities. Original invalid outputs are preserved. Failed or missing
requests are not automatically retried or relabelled. Any targeted retry is a
separate deliberate paid run; inspect failures and existing job state first.

## 12. Inspect outcomes, costs and coverage (offline)

Use the terminal setup from step 11 if starting in a fresh session.

```bash
python -m ai_ecosystem.calibration.run_company_profile_batch --run-dir "$RUN_DIR" report
```

This joins frozen selection/evidence metadata with saved results and recorded
usage, without contacting OpenAI or reading the source database.

**Expected output/completion:** `report.md`, `report.json` and
`company_results.csv` show selection reasons, per-company status/validation
errors, derived labels, evidence coverage/omissions, usage, calculated costs and
unknown-cost cases. Output tokens already include reasoning; reasoning is not
billed a second time in these calculations. Missing usage is reported as unknown,
not zero. Usage-based costs use the existing recorded Luna Batch schedule and are
not invoice-confirmed charges. The report includes nonselected and unscored sample
members with blank labels. Inspect pending and unresolved counts before claiming
a finished profiling pass.

**Retry/overwrite:** repeat to refresh only these derived local reports; immutable
requests, original results and raw responses are preserved. A completed command
or Batch job can still contain failed domains, failed/deferred pages, missing
profiles or unscored companies. No single success flag certifies full coverage or
LLM-label accuracy.

The older [pilot/validation workflows](../README.md#llm-profiling-and-validation-specific-workflows)
and [completed validation methodology](calibration_methodology.md#2026-10-22-independent-company-scorer-validation-milestone-55e)
retain their fixed study membership, splits and interpretation. Use the new
sample-based entry point above for this trial; do not resubmit the old validation
jobs. No trial is started merely by adding this guide. Population acquisition
scale, current account capacity, spend settings and the scientific interpretation
of the outputs still require deliberate review.
