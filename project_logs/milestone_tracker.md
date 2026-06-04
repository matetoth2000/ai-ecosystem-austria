# Austrian AI Ecosystem Mapping Project

## Milestone Tracker

### Milestone 1A — Repository Skeleton + SQLite Schema
Status: Completed

#### Completed
- Repository structure created
- SQLite schema created
- DB initialization script implemented
- Config structure implemented
- Prompt directory created
- Project log structure created

#### Key Outputs
- schema.sql
- init_db.py
- config.yaml

---

### Milestone 1B — ORBIS/Aurelia Import + URL Normalization
Status: Completed

#### Completed
- ORBIS/Aurelia importer
- URL normalization
- Domain extraction
- Domain deduplication
- Import statistics reporting
- Re-runnable imports

#### Key Outputs
- firms table populated
- domains table populated

---

### Domain QA and Categorization
Status: Completed

#### Completed
- Domain categorization
- Shared-domain detection
- Excluded-domain handling
- Pilot sampling support

---

### Milestone 2A.1 — Discovery Foundations
Status: Completed

#### Completed
- Discovery schema extensions
- Discovery configuration
- Discovery tracking tables
- Discovery run logging framework

---

### Milestone 2A.2 — Homepage Discovery
Status: Completed

#### Completed
- Homepage fetching
- Internal link extraction
- URL normalization
- Candidate storage
- Progress tracking
- Logging
- Evaluation tooling

#### Final Pilot Result
- Homepage successes: 89
- Homepage failures: 11
- Retained homepage candidates: 895

---

### Milestone 2A.2b — Homepage Discovery Evaluation
Status: Completed

#### Completed
- Re-runnable evaluation script
- Pilot discovery reports
- Exportable CSV summaries
- Coverage statistics

---

### Milestone 2A.2c — Reproducible Pilot Sampling
Status: Completed

#### Completed
- Fixed pilot sample generation
- Stratified sampling support
- Reproducible random seed
- Domain metadata export

#### Pilot Configuration
- 100 domains
- 90 single_firm
- 8 shared_domain
- 2 shared_group_or_brand

#### Sampling Seed
20260526

---

### Milestone 2A.2e — Classification Hardening
Status: Completed

#### Completed
- German and English URL classification
- Token-aware AI detection
- False-positive reduction
- Business-page detection improvements

#### Examples Added
- ai
- ki
- künstliche-intelligenz
- machine-learning
- products
- services
- leistungen
- produkte
- karriere

---

### Milestone 2A.2g — Canonical URL Normalization
Status: Completed

#### Completed
- Canonical URL handling
- Homepage deduplication
- Query cleanup
- Duplicate reduction
- Tracking parameter removal

#### Results
- 1,335 duplicate URLs collapsed
- 113 homepage variants collapsed

---

### Milestone 2A.2h — Hard-Negative Filtering
Status: Completed

#### Completed
- Hard-negative filtering
- Soft-negative retention
- Candidate quality improvements

#### Hard-Negative Examples
- cookie
- privacy
- datenschutz
- login
- checkout
- cart
- agb
- terms

#### Soft-Negative Examples
- impressum
- kontakt
- contact
- legal

#### Results
- 117 hard-negative URLs filtered

---

### Milestone 2A.3 — Robots.txt + Sitemap Discovery
Status: Completed

#### Completed
- robots.txt parsing
- Sitemap discovery
- Sitemap index recursion
- Sitemap candidate extraction
- Sitemap provenance tracking
- Discovery metrics and logging

#### Results
- robots.txt successes: 71
- sitemap files parsed: 262
- sitemap URLs discovered: 8,739
- sitemap candidates retained: 656
- invalid sitemap XML files: 16

#### Key Finding
Sitemap discovery increased retained candidate inventory by approximately 73% relative to homepage discovery.

---

### Milestone 2A.3b — Multi-Method Discovery Evaluation
Status: Completed

#### Completed
- Homepage vs sitemap comparison
- Discovery coverage analysis
- Category-level summaries
- Domain-level summaries
- Exportable evaluation reports

#### Results

##### Retained Candidates
- Homepage: 895
- Sitemap: 656
- Total: 1,551

##### Domain Coverage
- Homepage only: 25
- Sitemap only: 1
- Both: 47
- Neither: 27

##### Candidate Distribution

###### Homepage
- unknown: 572
- negative: 86
- homepage: 72
- products: 66
- about: 55
- news: 29
- careers: 15

###### Sitemap
- unknown: 509
- products: 59
- about: 35
- news: 34
- negative: 14
- homepage: 3
- careers: 2

#### Key Findings
- Sitemap discovery contributed substantial additional coverage.
- Sitemap discovery produced nearly as many product pages as homepage discovery.
- Sitemap discovery produced more news pages than homepage discovery.
- Careers pages were primarily discovered through homepage navigation.
- URL-only classification leaves many URLs in the unknown category, suggesting content-based classification will become important later.

---

## Current Project Status

### Completed
- Repository setup
- Database schema
- Firm import pipeline
- URL normalization
- Domain inventory
- Domain categorization
- Homepage discovery
- Robots discovery
- Sitemap discovery
- Discovery evaluation
- Logging and provenance tracking

### Not Yet Implemented
- Page scraping
- HTML storage
- Markdown conversion
- External link extraction
- Chunking
- Retrieval
- Embeddings
- LLM extraction
- Organization extraction
- Network construction
- Validation pipeline

### Current Discovery Pipeline

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
12. Candidate page storage with provenance

### Current Stage

Discovery pipeline complete and validated.

### Next Planned Milestone

Targeted page scraping of retained candidate URLs.