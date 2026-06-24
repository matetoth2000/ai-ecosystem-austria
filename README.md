# Mapping the Austrian AI Ecosystem Using Web Data and NLP

This repository supports a reproducible workflow for mapping organizations in the Austrian AI ecosystem using web data and NLP methods.

Current milestone: **Milestone 3A.5g.1** - ecosystem-relevance prompt upgrade for validation scoring.

Full analysis, chunking, embeddings, and final LLM extraction are not implemented yet.

## Data Availability

ORBIS/Aurelia firm-level data is not included in this repository. The repository only contains code and reproducible pipeline infrastructure. Data access is restricted.

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the project dependencies:

```bash
python -m pip install -e .
```

Initialize the SQLite database:

```bash
python -m ai_ecosystem.db.init_db
```

The database path is configured in `config/config.yaml` and defaults to `db/ai_ecosystem.sqlite`.

## Milestone 1B: ORBIS/Aurelia Import

Place the local ORBIS/Aurelia semicolon-separated `.txt` export under `data/raw/orbis_aurelia/`. This directory is ignored by Git because the firm-level data is NDA-restricted.

Update `config/config.yaml` so `ingestion.orbis_aurelia_input_path` points to the local input file:

```yaml
ingestion:
  orbis_aurelia_input_path: data/raw/orbis_aurelia/your_local_file.txt
```

Initialize the database and run the importer:

```bash
python -m ai_ecosystem.db.init_db
python -m ai_ecosystem.ingestion.import_orbis_aurelia
```

You can also override the configured input file:

```bash
python -m ai_ecosystem.ingestion.import_orbis_aurelia --input data/raw/orbis_aurelia/your_local_file.txt
```

After importing ORBIS/Aurelia records, categorize domains by firm representation level:

```bash
python -m ai_ecosystem.ingestion.categorize_domains
```

This computes `domains.firm_count`, flags social/profile/generic platforms for exclusion, and distinguishes single-firm domains from shared group or brand domains. Existing local development databases are migrated by this script if the domain metadata columns are missing. For a clean development reset, delete the ignored local SQLite file and re-run initialization plus imports.

Run the fake-data smoke test with:

```bash
python -m unittest tests.test_import_orbis_aurelia
python -m unittest tests.test_categorize_domains
```

Do not commit ORBIS/Aurelia files, processed data extracts, SQLite databases, or logs containing firm-level data.

## URL Discovery Pilot (Milestone 2A)

Milestone 2A prepares schema, configuration, and helper utilities for URL discovery. It does not implement crawling, HTTP fetching, sitemap parsing, or robots handling yet.

During the current development phase, schema changes are applied by recreating the ignored local SQLite database and rebuilding it from reproducible local inputs:

```bash
rm db/ai_ecosystem.sqlite
python -m ai_ecosystem.db.init_db
python -m ai_ecosystem.ingestion.import_orbis_aurelia --input data/raw/orbis_aurelia/Aurelia_Export_1.txt
python -m ai_ecosystem.ingestion.import_orbis_aurelia --input data/raw/orbis_aurelia/Aurelia_Export_2.txt
python -m ai_ecosystem.ingestion.categorize_domains
```

Reimporting ORBIS/Aurelia data and rerunning domain categorization after DB recreation is expected and acceptable at this stage.

Run a small homepage discovery pilot with live progress and structured logs:

```bash
python -m ai_ecosystem.discovery.pilot_sample
python -m ai_ecosystem.discovery.pilot_sample --strategy stratified
python -m ai_ecosystem.discovery.homepage_discovery --limit 3
python -m ai_ecosystem.discovery.homepage_discovery --input-csv data/interim/discovery_pilot_domains_v0_1.csv --limit 3
python -m ai_ecosystem.discovery.homepage_discovery --input-csv data/interim/discovery_pilot_domains_v0_2_stratified.csv --limit 3
python -m ai_ecosystem.discovery.import_sample --sample-id pilot_v0_2 --sample-name "Pilot v0.2 Stratified" --sampling-strategy stratified --sampling-seed 20260526 --csv data/interim/discovery_pilot_domains_v0_2_stratified.csv
python -m ai_ecosystem.discovery.import_sample --list
python -m ai_ecosystem.discovery.evaluate_homepage_discovery --input-csv data/interim/discovery_pilot_domains_v0_2_stratified.csv --export-csv
python -m ai_ecosystem.discovery.evaluate_discovery --input-csv data/interim/discovery_pilot_domains_v0_2_stratified.csv --export-csv
python -m ai_ecosystem.discovery.evaluate_discovery --sample-id pilot_v0_2 --export-csv
```

Logs are written to `logs/discovery_<run_id>.log`. The pilot fetches homepage HTML into memory only, extracts internal links, and stores normalized candidate URL metadata without recursively crawling discovered pages.

From Milestone 3A.0 onward, `discovered_pages` is the source-of-truth URL inventory for acquisition-eligible discovered URLs. `candidate_pages` remains a derived retained subset for backward-compatible downstream workflows. Historical `discovered_pages` rows created before rerunning discovery may contain only retained candidates backfilled from `candidate_pages`.

Use `evaluate_discovery` for multi-method discovery summaries across retained homepage and sitemap candidates. It reads SQLite without network access and writes ignored CSV reports to `data/interim/` when `--export-csv` is used.

Use `import_sample` to persist reproducible sample definitions in SQLite. Once imported, evaluation and future pipeline steps can reference a stable `sample_id` instead of reparsing the CSV.

The alphabetical/debug pilot sample is written to `data/interim/discovery_pilot_domains_v0_1.csv`. The fixed-seed stratified sample is written to `data/interim/discovery_pilot_domains_v0_2_stratified.csv`. Both files are ignored by Git.

## OpenAI Validation Infrastructure (Milestone 3A.5d)

Validation configuration lives in `config/config.yaml` under `validation`. The model name, prompt version, input length limit, and sample sizes are configurable there; no OpenAI model is hardcoded in validation code.

Provide credentials through the environment:

```bash
export OPENAI_API_KEY=...
```

You may also create a local `.env` file:

```bash
OPENAI_API_KEY=...
```

Do not commit `.env` or API keys. `.env.example` is included only as a safe template:

```bash
OPENAI_API_KEY=
```

Before running OpenAI validation, construct retained-vs-discarded validation samples from successfully acquired HTML pages:

```bash
python -m ai_ecosystem.validation.build_validation_sample --sample smoke
python -m ai_ecosystem.validation.build_validation_sample --sample main
```

Then build lightweight visible-text previews for those samples:

```bash
python -m ai_ecosystem.validation.build_validation_inputs --sample smoke_v1
python -m ai_ecosystem.validation.build_validation_inputs --sample main_v1
```

These commands do not call OpenAI or evaluate page content. They store sample rows and text previews in SQLite and write local ignored artifacts:

```text
data/interim/validation_sample_smoke_v1.csv
data/interim/validation_sample_audit_smoke_v1.csv
data/interim/validation_sample_main_v1.csv
data/interim/validation_sample_audit_main_v1.csv
data/interim/validation_inputs_smoke_v1.csv
data/interim/validation_inputs_main_v1.csv
```

Run the OpenAI retention scoring smoke sample after setting `OPENAI_API_KEY`. Prompt version `v2` is the default and scores both organization usefulness and AI-ecosystem usefulness:

```bash
python -m ai_ecosystem.validation.run_retention_validation --sample smoke_v1 --prompt-version v2
```

Use `--force-refresh` only when intentionally replacing prior results for the same sample, model, and prompt version:

```bash
python -m ai_ecosystem.validation.run_retention_validation --sample smoke_v1 --prompt-version v2 --force-refresh
```

For small debugging runs:

```bash
python -m ai_ecosystem.validation.run_retention_validation --sample smoke_v1 --prompt-version v2 --page-limit 3
```

Prompt version `v1` remains reproducible for side-by-side comparisons:

```bash
python -m ai_ecosystem.validation.run_retention_validation --sample smoke_v1 --prompt-version v1
```

Do not run `main_v1` until the smoke results have been manually reviewed. The runner reports token usage only; dollar cost is intentionally not calculated in this milestone.
