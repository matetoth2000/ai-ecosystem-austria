# Project Experiment Log

This file tracks experiments, pilot runs, parameter tuning, and observations.

The goal is to document:
- what was tested
- what worked
- what failed
- what changed afterwards

---

## Template

### YYYY-MM-DD — Experiment title

#### Goal
...

#### Setup
...

#### Data/sample
...

#### Results
...

#### Problems observed
...

#### Conclusion
...

#### Next steps
...

---

## 2026-05-06 — Pipeline architecture ideation

### Goal
Design scalable architecture for large-scale AI ecosystem extraction.

### Setup
Discussion and design phase only.

### Results
Defined:
- SQLite storage
- page-level scraping
- chunk-based retrieval
- one-shot LLM extraction
- multi-label role schema

### Conclusion
Architecture considered stable enough for implementation planning.

### Next steps
Create repository structure and database schema.

---

## 2026-05-08 — ORBIS/Aurelia import completed

### Goal
Import firm records from two Aurelia export files into SQLite.

### Results
- Export 1 rows read: 110,000
- Export 1 firms inserted: 110,000
- Export 1 unique valid domains found: 81,515
- Export 1 missing URLs: 20,654
- Export 1 invalid URLs: 192

- Export 2 rows read: 112,911
- Export 2 firms inserted: 112,911
- Export 2 unique valid domains found: 71,577
- Export 2 new domains inserted: 64,788
- Export 2 missing URLs: 34,495
- Export 2 invalid URLs: 249

### Conclusion
The import pipeline works and produces a large domain inventory for URL discovery.

### Next step
Run database sanity checks and inspect shared domains before URL discovery.

---

## 2026-05-08 — Domain categorization completed

### Goal
Categorize imported domains by representation level before URL discovery.

### Results
- Total domains: 146,303
- Single-firm domains: 133,157
- Shared-domain domains: 12,766
- Shared group/brand domains: 121
- Social/low-value domains: 259
- Excluded domains: 259

### Conclusion
Most domains are single-firm domains, so domain-level scraping remains feasible. Shared domains require cautious interpretation, and social/profile domains should be excluded from scraping.

### Next step
Run URL discovery pilot on a small sample of non-excluded domains.

---

## 2026-05-26 - Discovery Pilot v0.2 — Homepage + Sitemap Evaluation

### Sample

Reproducible stratified sample:

- 100 domains
- 90 single_firm
- 8 shared_domain
- 2 shared_group_or_brand

Sampling seed:

20260526

### Discovery Results

Homepage discovery:

- Successes: 89
- Failures: 11
- Retained candidate URLs: 895

Robots and sitemap discovery:

- robots.txt successes: 71
- Sitemap files parsed: 262
- Sitemap URLs discovered: 8,739
- Sitemap candidates retained: 656
- Invalid sitemap XML files: 16

Combined:

- Total retained candidates: 1,551

### Coverage

Domains with homepage candidates only:

25

Domains with sitemap candidates only:

1

Domains with both homepage and sitemap candidates:

47

Domains with neither:

27

### Candidate Distribution by Discovery Method

Homepage:

- unknown: 572
- negative: 86
- homepage: 72
- products: 66
- about: 55
- news: 29
- careers: 15

Sitemap:

- unknown: 509
- products: 59
- about: 35
- news: 34
- negative: 14
- homepage: 3
- careers: 2

### Observations

- Sitemap discovery increased retained candidate inventory by approximately 73% relative to homepage discovery.
- Sitemap discovery produced nearly as many product-related URLs as homepage discovery.
- Sitemap discovery produced more news URLs than homepage discovery.
- Careers pages were primarily discovered via homepage navigation.
- URL-only classification leaves many URLs in the unknown category, suggesting that content-based classification will be important later in the pipeline.
- Homepage + sitemap discovery appears sufficient for the next project stage without introducing recursive crawling.

---

## Persistent Sample Registry Validation

Date: 2026-05-27

### Objective

Validate database-backed sample management and integration with evaluation tooling.

### Sample Imported

Sample ID:

pilot_v0_2

Metadata:

- Sample name: Pilot v0.2 Stratified
- Sampling strategy: stratified
- Sampling seed: 20260526
- Sample size: 100 domains

### Results

Sample import completed successfully.

Verification:

- sample_sets contains pilot_v0_2
- sample_domains contains 100 domains
- duplicate sample/domain rows were prevented on rerun

Evaluation tooling successfully executed using:

python -m ai_ecosystem.discovery.evaluate_discovery --sample-id pilot_v0_2

### Outputs

Generated:

- discovery_eval_pilot_v0_2_domain_summary.csv
- discovery_eval_pilot_v0_2_method_summary.csv
- discovery_eval_pilot_v0_2_category_summary.csv

### Conclusion

Database-backed sample management is functioning correctly and will be used for future pilot, validation, and evaluation workflows.

---

## Discovery Concurrency Benchmark

Sample:
pilot_v0_2

Domains:
100

Results:

| Workers | Runtime (s) | Domains/s | Retained URLs | Domains Covered |
|----------|------------:|-----------:|--------------:|----------------:|
| 1 | 351 | 0.28 | 1551 | 73 |
| 5 | 66 | 1.52 | 1551 | 73 |
| 10 | 33 | 3.03 | 1551 | 73 |
| 20 | 23 | 4.35 | 1551 | 73 |
| 50 | 19 | 5.26 | 1551 | 73 |

Conclusion:

Concurrency provides approximately 18.5× acceleration with no observed loss in discovery quality.

Recommended operational default:
20 workers.

---

## 2026-06-04 — Discovery pilot refinement: redirect/alias host support

### Goal
Improve discovery coverage for domains using redirects, alias hosts, and www/non-www variants.

### Changes
- Added canonical redirect support.
- Added alias host handling.
- Discovery now accepts candidate URLs from normalized redirect hosts.

### Results
- Covered domains increased from 73 → 80.
- Retained candidate URLs increased from 1,551 → 1,770.
- Potential extraction gaps reduced from 6 → 1.

### Investigation
Most remaining misses are caused by infrastructure issues:
- DNS failures
- SSL failures
- timeouts

### Conclusion
Discovery quality improved substantially.
Remaining misses are largely outside discovery logic.

### Decision
Browser rendering is not justified at this stage.

---

## 2026-06-04 — Discovery Retention Audit

### Goal
Evaluate whether the URL retention strategy is discarding potentially valuable pages before starting large-scale page acquisition.

### Dataset
Latest audited pilot run.

### Results

Discovered URLs:
- 11,053

Retained URLs:
- 1,770

Retention rate:
- 11.9%

Discard reasons:

- retention_cap: 8,642
- duplicate: 2,725
- invalid_url: 627
- external_domain: 576
- hard_negative: 282
- asset_static: 144
- homepage_variant: 126

### Key Findings

#### Filtering is not the primary bottleneck

Only 426 URLs were removed through content-related filtering:

- hard_negative: 282
- asset_static: 144

Most discarded URLs were removed because of retention caps or deduplication.

#### Retention-cap pressure dominates

Page types discarded due to retention caps:

- unknown: 5,477
- news: 1,232
- products: 1,154
- about: 451
- careers: 135
- ai_technology: 43

#### Cap pressure is concentrated

Largest cap-pressure domains:

- sprechen.com: 985
- museum-joanneum.at: 923
- bertrandt.com: 794
- itf-tkd.org: 782
- consultatio.at: 650

A small number of sitemap-heavy domains account for most cap-related discards.

#### Retained coverage appears healthy

Retained URLs:

- unknown: 1,229
- products: 151
- negative: 106
- about: 101
- homepage: 88

AI-specific pages discarded due to retention caps:

- ai_technology: 43

#### Classification remains the main weakness

Approximately 69% of retained URLs are classified as unknown.

### Conclusion

The audit does not support the concern that important pages are being randomly filtered out.

Discovery coverage appears strong and filtering behaves conservatively.

The primary limitation is classification coverage and ranking quality, not filtering logic.

### Next Step

Proceed to Page Acquisition and evaluate actual page content quality before investing further effort into URL-level heuristics.

---

## 2026-06-07 — Milestone 3A: Page Acquisition Pilot

### Goal
Fetch and persist raw HTML pages discovered during URL discovery and evaluate acquisition quality before content processing.

### Dataset
- Acquisition-eligible URLs: 9,872
- Fetch scope: discovered pages
- Pilot sample: pilot_v0_2

### Results

Acquired pages:
- 8,552

Acquisition success rate:
- 88.6%

Compressed corpus size:
- 327 MB

Metadata extraction:
- title extraction: >99%
- canonical URL extraction: >99%
- language detection: >99%
- word count estimation: >99%

### Failure Analysis

Failures were primarily caused by external web infrastructure issues:

- HTTP errors
- timeouts
- SSL failures
- connection failures

No major pipeline defects were identified.

### Retention Analysis

Acquisition success by discovery status:

- retained pages: 87.5%
- non-retained pages: 88.8%

Retention status did not materially affect acquisition success.

### Discovery Source Analysis

Sitemap-discovered pages acquired at least as successfully as homepage-discovered pages.

### Conclusion

The acquisition pipeline successfully produced a large and reproducible HTML corpus.

The resulting corpus appears suitable for downstream HTML processing, content extraction, and retrieval experiments.

### Next Step

Proceed to Milestone 3B: HTML processing and content preparation.

---

## 2026-06-04 — Post-acquisition methodological review

### Context

Milestone 3A acquisition succeeded:

- 9,872 acquisition-eligible URLs
- 8,552 acquired pages
- 88.6% success rate
- 327 MB compressed HTML corpus

### Main Finding

Discovery and acquisition are no longer the main bottlenecks.

### New Concern

URL-retained pages are useful for firm profiling, but may not always preserve pages useful for technology, innovation, partnership, or ecosystem evidence.

### Conclusion

Further implementation should be paused until a content-based retention strategy is designed.

---

## 2026-07-08 — Milestone 3B Extraction QA Review

### Objective
Evaluate whether the HTML-to-Markdown extraction stage is sufficiently robust to support downstream content scoring.

### Results
- Successfully extracted 599 of 600 sampled pages (99.83% success rate).
- Trafilatura produced usable markdown and plain-text representations for nearly all pages.
- Page-level metadata, links, and evidence packages were generated successfully.
- Extraction QA indicates realistic distributions of page length, headings, languages, and links.

### Key observations
- Many extracted pages are short (<100 words), reflecting a mix of genuine low-content pages and template-heavy websites.
- Many pages contain no explicit headings, which is common for modern CMS-generated sites.
- Duplicate content groups exist and should be considered during later evidence scoring.
- No systemic extraction failures or widespread empty outputs were observed.

### Conclusion
The extraction pipeline is considered sufficiently robust for downstream semantic validation and content-scoring development. Further extraction optimization is deferred unless later scoring experiments reveal systematic evidence loss.
