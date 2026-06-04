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