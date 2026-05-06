# Mapping the Austrian AI Ecosystem Using Web Data and NLP

This repository supports a reproducible workflow for mapping organizations in the Austrian AI ecosystem using web data and NLP methods.

Current milestone: **Milestone 1A** - repository skeleton and SQLite schema initialization.

Scraping, ingestion, retrieval, chunking, LLM calls, and analysis are not implemented yet.

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

Run the fake-data smoke test with:

```bash
python -m unittest tests.test_import_orbis_aurelia
```

Do not commit ORBIS/Aurelia files, processed data extracts, SQLite databases, or logs containing firm-level data.
