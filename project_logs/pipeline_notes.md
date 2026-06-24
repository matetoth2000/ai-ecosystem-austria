# Pipeline Notes

Freeform notes, TODOs, observations, ideas, and open questions.

This file is intentionally informal.

---

## Open Questions

- Should embeddings later replace or complement keyword retrieval?
- How should duplicate multilingual pages be handled?
- What threshold should define AI-active firms?
- Should network analysis later include centrality/community detection?

---

## Ideas

- Compare keyword retrieval vs embedding retrieval.
- Potential future master thesis extension:
  - evolution of AI ecosystems
  - greenwashing / AI-washing
  - network dynamics
  - sectoral specialization

---

## Important reminders

- Keep prompts versioned.
- Keep pipeline stages resumable.
- Store intermediate outputs.
- Avoid unnecessary re-scraping.
- Avoid re-running expensive LLM calls.

---

## Planned pipeline

firms
→ domains
→ discovered URLs
→ pages
→ chunks
→ retrieval
→ LLM extraction
→ network construction
→ validation
→ analysis

---

## Discovery Pipeline Status (Post-Milestone 2A.3)

Current discovery workflow:

1. Import ORBIS/Aurelia firms
2. Normalize URLs and domains
3. Generate reproducible pilot sample
4. Homepage discovery
5. robots.txt discovery
6. Sitemap discovery
7. URL normalization
8. Asset/static filtering
9. Hard-negative filtering
10. Candidate page classification
11. Candidate page scoring
12. Store candidate URLs with provenance

Stored provenance currently includes:

- discovery_method
- discovered_from_url
- source_domain
- discovery_run_id
- last_discovered_at

Current discovery methods:

- homepage
- sitemap

Current filtering:

Hard-negative:
- cookie
- privacy
- datenschutz
- login
- cart
- checkout
- agb
- terms

Soft-negative:
- impressum
- kontakt
- contact
- legal

Current status:

- Homepage discovery validated
- Sitemap discovery validated
- Reproducible evaluation implemented
- Runtime and summary logging implemented

Pilot runtime:

- Homepage only: ~212 seconds (100 domains)
- Homepage + sitemap: ~653 seconds (100 domains)

Important observation:

Sitemap discovery adds substantial coverage while remaining significantly cheaper than recursive crawling.

Planned next stage:

Evaluate whether to proceed directly to targeted page scraping of retained candidates or introduce limited recursive crawling. Current evidence suggests targeted scraping should be prioritized first.

---

## Sample Management

Sample definitions are now stored in SQLite.

Authoritative sample tables:

- sample_sets
- sample_domains

Recommended workflow:

1. Create sample CSV
2. Import sample into registry
3. Reference sample by sample_id in evaluation and analysis scripts

Example:

python -m ai_ecosystem.discovery.import_sample \
    --sample-id pilot_v0_2 \
    --csv data/interim/discovery_pilot_domains_v0_2_stratified.csv

Preferred evaluation:

python -m ai_ecosystem.discovery.evaluate_discovery \
    --sample-id pilot_v0_2

instead of:

python -m ai_ecosystem.discovery.evaluate_discovery \
    --input-csv data/interim/discovery_pilot_domains_v0_2_stratified.csv

Rationale:

- Better reproducibility
- Easier SQL-based analysis
- Consistent sample tracking across project stages
- Supports multiple concurrent evaluation samples

CSV files remain useful for import/export and sharing, but the database registry is the authoritative source of sample membership.

---

## Important methodological clarification

The purpose of URL retention is not to maximize AI signal capture.

The purpose of URL retention is to maximize retention of pages that describe the firm's actual activities, products, services, technologies, capabilities, and strategic positioning.

AI classification is a downstream task performed on the retained content.

Retention quality should therefore be evaluated primarily on firm-relevance and business descriptiveness rather than AI keyword prevalence.

---

## Milestone 3A Outcome

Acquisition corpus:

- 8,552 HTML pages
- 327 MB compressed storage
- 88.6% acquisition success rate

Acquisition quality is considered sufficient for downstream content processing.

Future evaluation should focus on:

- HTML cleaning quality
- Markdown conversion quality
- Boilerplate removal
- Language handling
- Chunking strategy
- Retrieval effectiveness

Acquisition is no longer considered a major project risk.

---

## 2026-06-04 — Current methodological direction

The pipeline direction is shifting from URL-based retention toward content-based retention.

URL discovery and acquisition remain useful as broad evidence gathering stages.

The next stage should likely be:

HTML -> main-content extraction -> page feature extraction -> multi-dimensional content scoring -> retained firm evidence package -> final LLM extraction.

Candidate content dimensions:

- firm activity descriptiveness
- technology/AI specificity
- innovation/R&D evidence
- partnership/ecosystem evidence
- page quality/informational density
- structural/site importance

No further implementation should proceed until this content-retention design is locked.
