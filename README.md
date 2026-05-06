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
