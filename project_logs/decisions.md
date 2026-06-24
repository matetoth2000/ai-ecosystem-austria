# Project Decision Log

This file tracks major design and methodological decisions taken during the project.

The purpose is:
- reproducibility
- easier report writing
- documenting tradeoffs and reasoning
- tracking pipeline evolution

---

## 2026-05-06 — Initial project direction

### Context
The interdisciplinary data science project focuses on mapping the Austrian AI ecosystem using website data and NLP/LLMs.

### Decision
Focus primarily on AI-related firm classification and ecosystem mapping.

### Reasoning
- AI classification has strong methodological relevance.
- Existing literature and taxonomies are available.
- Better fit for scalable NLP/LLM extraction than broader strategic concepts.
- Easier validation and future thesis extension potential.

### Implications
- Project scope narrowed mainly to AI.
- Innovation may remain secondary or exploratory.

---

## 2026-05-06 — Use multi-label AI ecosystem roles instead of single classification

### Context
A single-label taxonomy would not capture firms operating in multiple AI-related roles.

### Decision
Use multi-label role scoring with Likert-scale confidence values.

### Roles
- ai_core_dev
- ai_app_vendor
- ai_integrator
- ai_infra_tool
- ai_data
- ai_adopter

### Reasoning
- Firms can occupy multiple positions in the AI ecosystem.
- Reduces ambiguity.
- Better reflects real-world firm behavior.

### Implications
- More flexible downstream analysis.
- Slightly more complex prompt design and evaluation.

---

## 2026-05-06 — Use SQLite instead of PostgreSQL initially

### Context
The project requires storing firms, pages, chunks, links, and LLM outputs.

### Decision
Use SQLite for initial implementation.

### Reasoning
- Simpler setup and debugging.
- Sufficient for pilot and likely medium-scale processing.
- Easier local development and reproducibility.

### Implications
- Potential migration to PostgreSQL later if concurrency or scale becomes problematic.

---

## 2026-05-06 — Use page-level storage and firm-level processing

### Context
Multiple firms may share domains or websites.

### Decision
Store scraped data at page level, but construct firm-level LLM inputs.

### Reasoning
- Avoids duplicate scraping.
- Preserves flexibility.
- Allows firm-specific retrieval and prompt construction.

### Implications
- Requires `firm_pages` mapping logic.
- Enables better scaling and later analysis.

---

## 2026-05-06 — Use keyword retrieval baseline before embeddings

### Context
Embedding-based retrieval was considered early in the pipeline design.

### Decision
Start with bilingual keyword-based retrieval and add embeddings later if useful.

### Reasoning
- Simpler baseline.
- Easier debugging and interpretability.
- Allows establishing retrieval benchmark before increasing complexity.

### Implications
- Embeddings may later be added for robustness and semantic retrieval comparison.

---

## 2026-05-06 — Use one-shot LLM extraction

### Context
LLM costs are a major concern at scale.

### Decision
Perform AI classification, role scoring, AI technique extraction, and organization extraction in a single LLM call per firm.

### Reasoning
- Reduces token usage and API costs.
- Keeps extraction pipeline simpler.

### Implications
- Prompt design becomes critically important.
- Output schema must remain stable and parseable.

---

## 2026-05-08 — Interpret shared domains at domain/group web-presence level

### Context
After importing two ORBIS/Aurelia files, the database contained 222,911 firms and 146,303 unique domains. QA showed that many domains are shared by multiple legal entities. Examples include `spar.at`, `raiffeisen.at`, `voestalpine.com`, and `omv.at`. Some shared domains are also social media or profile domains such as `facebook.com` and `instagram.com`.

### Decision
Use the domain as the default web-presence unit for scraping and initial AI classification. If a domain is associated with exactly one firm, its classification can be interpreted as firm-level evidence. If a domain is associated with multiple firms, its classification should be interpreted as domain/group/brand-level evidence rather than automatically assigned to each legal entity.

### Reasoning
A shared website often represents a corporate group, brand, franchise system, platform, or social media profile rather than a specific legal entity. Classifying every legal entity linked to the same shared domain would create false precision and could overcount AI-active firms. Domain-level interpretation is more methodologically honest and better aligned with website-based evidence.

### Implications
- The `domains` table should store `firm_count`, `representation_level`, `domain_category`, and exclusion flags.
- Obvious social media/profile domains should be excluded from scraping.
- Shared domains should be flagged and interpreted cautiously.
- Later analysis can distinguish between firm-level and group/domain-level evidence.
- If needed, a likely primary firm for a shared domain can be identified later using explicit heuristics, but the pipeline should not guess this automatically at this stage.

---

## 2026-05-26 — Use homepage + sitemap discovery as the primary URL discovery strategy

### Context

The URL discovery pilot was implemented on a reproducible stratified sample of 100 Austrian company domains. Discovery sources included homepage link extraction, robots.txt parsing, and sitemap discovery.

### Decision

Use homepage discovery and sitemap discovery as the standard URL discovery mechanisms before page scraping.

### Reasoning

- Homepage discovery reliably identifies navigation-linked pages such as products, services, company information, careers, and contact pages.
- Sitemap discovery substantially increases candidate page coverage at relatively low computational cost.
- Sitemap discovery does not require recursive crawling and therefore remains scalable.
- Homepage and sitemap discovery together provide broad coverage while maintaining manageable complexity.

### Evidence

Pilot results:

- Homepage candidates retained: 895
- Sitemap candidates retained: 656
- Total retained candidates: 1,551
- Homepage-covered domains: 72
- Sitemap-covered domains: 48
- Homepage-only domains: 25
- Sitemap-only domains: 1
- Domains covered by both methods: 47

### Implications

- Sitemap discovery becomes a standard pipeline component.
- Recursive crawling is not currently required.
- Future work should focus on content acquisition and evaluation before introducing deeper crawling.

---

## 2026-05-27 — Introduce persistent sample registry

### Context

The project now uses multiple reproducible samples for discovery evaluation and future validation activities. Initially, samples existed only as CSV files.

Examples:

- discovery_pilot_domains_v0_1.csv
- discovery_pilot_domains_v0_2_stratified.csv

Future project stages are expected to introduce additional samples for validation, annotation, scraping, and LLM evaluation.

### Decision

Store sample definitions in SQLite using dedicated sample registry tables.

Tables:

- sample_sets
- sample_domains

Each sample receives a stable sample_id and can be referenced independently of the original CSV file.

### Reasoning

- Improves reproducibility.
- Enables SQL-based evaluation and analysis.
- Allows multiple samples to coexist in a structured manner.
- Reduces dependence on local CSV files.
- Simplifies future validation and annotation workflows.

### Implications

- Samples become first-class project entities.
- Evaluation scripts can operate using sample_id instead of file paths.
- CSV files are treated as import/export artifacts rather than the authoritative source of sample membership.

---

## 2026-05-31 — Use concurrent domain-level discovery

### Context

Sequential URL discovery produced long runtimes during pilot evaluation.

### Decision

Adopt configurable domain-level concurrency for homepage, robots.txt, and sitemap discovery.

### Experiment

Benchmark on pilot_v0_2:

1 worker  -> 351s
5 workers -> 66s
10 workers -> 33s
20 workers -> 23s
50 workers -> 19s

Discovery outputs remained identical across all worker counts.

### Implications

Discovery is primarily network-bound.

A worker count around 20 provides most of the attainable speedup while reducing network pressure relative to 50 workers.

---

## 2026-06-04 — Support canonical redirects and alias hosts during discovery

### Context
Many domains redirect to alternate hosts (e.g. www/non-www variants or corporate alias domains).

### Decision
Treat canonical redirect hosts and normalized alias hosts as valid discovery hosts for URL inventory generation.

### Reasoning
Ignoring redirected hosts caused avoidable coverage losses and missed candidate pages.

### Implications
- Discovery operates on logical website ownership rather than exact hostname matches.
- Candidate URLs discovered through redirect targets are retained.
- Coverage improves without requiring browser rendering.

---

## 2026-06-04 — Proceed to content-level analysis after retention audit

### Context

A retention audit was performed on the URL discovery pilot to determine whether the retention strategy was discarding potentially valuable pages.

### Decision

Proceed to Page Acquisition and content-level analysis rather than further refining URL retention heuristics.

### Reasoning

The audit showed:

- Discovery coverage is strong.
- Filtering removes relatively few URLs.
- Most discarded URLs result from retention caps and deduplication.
- AI-related pages are rarely suppressed.
- The largest remaining uncertainty concerns page content quality rather than URL discovery quality.

### Implications

- Future prioritization should increasingly rely on content signals rather than URL structure.
- The next milestone will focus on HTML acquisition, metadata extraction, and content inspection.
- Additional URL heuristic refinement is deferred until content-level evidence suggests it is necessary.

---

## 2026-06-07 — Store compressed raw HTML as the canonical page snapshot

### Context

The project requires reproducible website acquisition while avoiding unnecessary re-fetching of pages that may change over time.

### Decision

Store compressed raw HTML files on disk as the canonical representation of acquired pages and store acquisition metadata in SQLite.

### Reasoning

- Preserves full page content for future processing.
- Enables reproducibility.
- Avoids repeated network requests.
- Supports future improvements to extraction and parsing without reacquiring data.

### Implications

- HTML acquisition and content processing are decoupled.
- Future processing stages operate on stored HTML snapshots.
- Raw HTML remains available for debugging and validation.

---

## 2026-06-07 — Proceed to content processing after acquisition validation

### Context

Page acquisition successfully fetched 8,552 pages with an acquisition success rate of 88.6%. Metadata extraction coverage exceeded 99%.

### Decision

Proceed to HTML processing and content preparation without further changes to the acquisition pipeline.

### Reasoning

- Acquisition success is sufficiently high.
- Failures are dominated by external website issues rather than pipeline defects.
- Sitemap-discovered pages perform at least as well as homepage-discovered pages.
- The acquired corpus is large enough for downstream experimentation.

### Implications

- Future effort should focus on content quality rather than acquisition quality.
- HTML processing becomes the primary bottleneck and area of investigation.
- Acquisition logic is considered stable for the current project stage.

---

## 2026-06-04 — Pause implementation to redesign evidence retention around content-based scoring

### Context

Discovery and acquisition are working. Page acquisition produced a usable HTML corpus. However, validation and investigation suggest URL-based retention is insufficient for reliably preserving pages useful for AI ecosystem mapping.

### Decision

Pause further implementation before HTML-to-Markdown/chunking and redesign the next stage around content-based page retention.

### Reasoning

The core methodological bottleneck is now evidence selection, not discovery or acquisition. Retention should prioritize pages that describe firm activities, technologies, innovation/R&D, partnerships, capabilities, and ecosystem participation, not only pages that look useful from URLs.

### Implications

The next design milestone will define content extraction, page scoring dimensions, retention policy, and whether embeddings/chunking are needed.
