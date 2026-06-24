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

---

### Milestone 2A.3c — Sample Registry
Status: Completed

#### Completed
- Sample registry tables
- Sample import utility
- Sample metadata tracking
- Re-runnable sample evaluation
- Sample-based discovery evaluation

#### Added Tables
- sample_sets
- sample_domains

#### Pilot Registered
- sample_id: pilot_v0_2
- strategy: stratified
- seed: 20260526
- domains: 100

---

### Milestone 2A.3d — Discovery Benchmarking and Concurrency
Status: Completed

#### Completed
- Configurable worker count
- Parallel domain discovery
- Benchmark runner
- Runtime tracking
- Quality comparison across worker counts

#### Benchmark Results

| Workers | Runtime | Retained URLs | Coverage |
|----------|---------:|---------:|---------:|
| 1 | 351s | 1551 | 73 |
| 5 | 66s | 1551 | 73 |
| 10 | 33s | 1551 | 73 |
| 20 | 23s | 1551 | 73 |
| 50 | 19s | 1551 | 73 |

#### Key Finding
- Discovery quality remained unchanged across all tested worker counts.
- 50 workers achieved approximately 18.5× speedup relative to a single worker.

---

### Milestone 2A.3e — Discovery Miss Investigation
Status: Completed

#### Completed
- Zero-retained-domain investigation
- Failure categorization
- Discovery gap analysis
- Extraction-gap identification

#### Results

##### Zero-Retained Domains
- Total: 27

##### Categories
- Infrastructure failures: 14
- No URLs exposed: 5
- Filtered out: 1
- Potential extraction gaps: 6
- Unknown: 1

#### Key Finding
Most missing domains were caused by infrastructure failures rather than discovery defects.

---

### Milestone 2A.3f — Canonical Redirect / Alias Host Support
Status: Completed

#### Completed
- Redirect host tracking
- Alias-host discovery support
- Canonical-domain candidate retention
- Redirect-aware sitemap handling

#### Results

##### Coverage Improvement
- Covered domains: 73 → 80
- Retained candidate URLs: 1,551 → 1,770
- Zero-retained domains: 27 → 20
- Potential extraction gaps: 6 → 1

##### Alias Metrics
- Redirected domains: 9
- Domains using alias hosts: 8
- Candidates retained via alias hosts: 219

##### Newly Recovered Domains
- bauakademie.co.at
- biomassehofat.srv272.adino.at
- consultatio.at
- evk.biz
- gemeindeinformatik.at
- np-d.com
- peratravel.at

#### Key Finding
Canonical-domain aliasing was a major remaining discovery gap. Browser rendering was not justified after correction.

---

## Final Discovery Pilot Result

### Pilot Configuration
- 100 stratified domains
- Seed: 20260526

### Final Coverage
- Domains covered: 80
- Domains uncovered: 20

### Discovery Inventory
- Homepage candidates: 895
- Sitemap candidates: 875
- Total retained candidates: 1,770

### Remaining Gaps
- Infrastructure failures: 12
- No URLs exposed: 6
- Filtered out: 1
- Potential extraction gaps: 1

### Conclusion
Discovery coverage limitations are now primarily caused by external infrastructure issues (DNS, SSL, timeout, unavailable sites) rather than discovery pipeline defects.

Milestone 2 is considered complete.

---

### Current Stage

Discovery pipeline complete, benchmarked, evaluated, and validated.

### Next Planned Milestone

Targeted page acquisition and content extraction from retained candidate URLs.