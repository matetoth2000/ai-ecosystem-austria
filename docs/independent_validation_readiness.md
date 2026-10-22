# Milestone 5.4a - Independent Validation Readiness and Acquisition Plan

Audit date: 2026-10-05. This is a planning checkpoint, not permission to start
acquisition. Database inspection used read-only SQLite connections; filesystem
measurements used file sizes and allocated blocks, without copying the corpus.
No sample was registered, website requested, model run, or research artifact changed.
All storage figures below are decimal GB/MB unless stated otherwise.

## Decision

Plan a **500-company acquisition sample, starting with a nested 100-company
measurement batch**. Complete each batch through extraction and frozen scoring
before admitting the next. About 500 companies appears practical within one day
and the disk allowance; 1,000 is conditional, 2,000 requires a substantially lighter
corpus than the enriched pilot, and 5,000 is not a defensible initial commitment.
These are acquisition-pool sizes, not proposed LLM-label allocations.

The critical missing component is an **unlabeled company feature-preparation and
frozen-inference adapter**. Acquisition infrastructure already supports new domain
samples. Preserve both frozen selectors: `reference_logistic_size` (C=10) and
`cleaned_grounded_combined` (C=1). Neither is independently validated and neither
has a production cutoff. The approximately EUR 20 labeling budget is separate;
label definitions, allocation, model choice and cutoff calibration remain later work.

## Current Implementation, Not Historical Assumptions

| Stage | What works now | Constraint for this study |
|---|---|---|
| Membership | `sample_companies` stores exact legal-company membership; `sample_domains` is its reusable website projection. `discovery_utils.py:32-64` reads both. | A new broad, probability-recorded sample builder is needed; do not reuse the AI-enriched taxonomy as the validation sampling design. |
| Discovery | `discovery/homepage_discovery.py:1519` accepts `--sample-id`. | Keep 20 workers, 15s request timeout, homepage discovery limit 200, sitemap limit 20 files/1,000 URLs and 5s sitemap timeout. The 200 limit is not a total-site cap: sitemap pages are additional. |
| Acquisition | `acquisition/run_acquisition.py` accepts generic samples; `html_fetcher.py:197` selects their discovered URLs, deduplicated by domain/normalized URL. | Keep `fetch_scope: discovered`, 20 workers, timeout 15s, two retries, existing-success skipping. Selection spans discovery runs, not just the latest run. The retained-candidate limit of 20 does **not** cap this acquisition. |
| Extraction | `extraction/run_extraction.py:245` selects successful acquired HTML in `sample_domains`; CLI `--sample-id` is distinct from legacy page-validation `--sample`. | Keep v1/Trafilatura/features/links unchanged. It processes pages serially and commits per page. The skip check requires successful v1 extraction, paths/hash and matching page features; it does not verify file existence. Audit files before scoring. |
| Deterministic features | `calibration/company_modeling_features.py:17` builds reusable structural, relational, availability, duplicate and original lexical features without labels. | `company_modeling_sources.py:13` loads companies from two completed calibration runs and reads historical packages. The dataset-building CLI therefore cannot prepare arbitrary new IDs as-is. |
| Expanded lexical/text inputs | `company_tuning_features.py:59,127` constructs the challenger lexical block and document maps. | Expanded lexical construction also accesses labels for diagnostic output; separate that diagnostic dependency. Document loading expects dataset tables/splits, although its text-loading path does not read targets or supervision. New inference needs neither historical splits nor calibration packages. |
| Frozen scoring | `company_cleanup_features.py:217` exposes `FrozenCompanyScorer.score(ids, documents, numeric, lexical, states)`, with no label argument or fitting. | It needs already prepared inputs, not raw company IDs. Add orchestration and provenance checks, not new models. Scores are logistic decision-function values, **not calibrated probabilities**. NO_EVIDENCE receives no score; INPUT_ERROR must be resolved/deferred. |

Source settings: [config/config.yaml](../config/config.yaml). Source modules are
under `src/ai_ecosystem/`. Frozen artifacts are under
`data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen/`,
including `freeze_manifest.json` and the two `<candidate>/model.joblib` bundles.
Old deterministic KEEP/EXCLUDE rules, scorer-v1 and learned page rankers are not
required steps in this frozen-model inference path.

The adapter must preserve source/title/heading fallbacks, v1 compatibility, text
normalization/content hashes, duplicate/quarantine policy, focal-company relations,
feature order and both lexical definitions. Reference uses mean-pooled body/title
text plus original numeric/lexical/size blocks; challenger uses mean-pooled text,
original numeric, expanded lexical and frozen grounded aliases. Never refit
vocabularies, IDF, imputers, scalers or aliases on validation companies. ÖNACE is
sampling metadata only. No new scorer-v1 pass, page-model inference, company LLM
package or training cache is needed to produce these scores.

## Observed Runtime and Storage

### Runtime

| Recorded source | Observed work | Wall time / throughput |
|---|---|---|
| `logs/discovery_homepage_20260825T161741Z_d14dedd6_summary.json` | 498 domains, 443 homepage successes; 69,306 discovery records, including overlapping discovery methods | 221s; 2.25 domains/s |
| DB `acquisition_runs`, `acquisition_20260607T184817Z_3b5c11b2` | Pilot: 9,872 attempts, 8,743 fetched, 1,129 failed | 20m12s; 8.15 results/s |
| DB `acquisition_runs`, `acquisition_20260828T140346Z_a64ba4f1` | Enriched: 63,519 selected, 54,511 fetched, 7,726 failed, 1,282 already-existing skips | 4h07m15s; 4.20 newly recorded results/s |
| DB `extraction_runs`, `extraction_20260830T150455Z_92b8d223` | Last restart: 7,966 processed, 47,599 skipped; 7,352 new successes, 614 failures | 11m15s; 11.80 processed pages/s, **not** 82 newly extracted pages/s |
| DB `extraction_runs`, `extraction_20260708T111745Z_ff03df23` | 600 processed, 599 successful | 63s; 9.52 processed pages/s |
| `company_modeling_datasets/company_ai_relevance_v1/manifest.json` | Historical 649-company feature dataset, 55,568 pages | 158.4s total: source loading 77.9s, overlap 24.9s, features/validation 47.7s; peak memory 1.43 GB |

Earlier August extraction invocations lack completion timestamps/counters. There
is no reliable cumulative active-time total across those restarts. Its per-page
extraction timer also ends **before** page-feature/link insertion and commit, so
it understates complete processing cost. Generic domain-sample extraction is not
recorded in the legacy `extraction_runs.sample_id` field; future batch manifests
must explicitly link sample membership to run IDs. Acquisition logs live under
`logs/acquisition_<run_id>.log`; older runs do not prove the current implementation
has identical throughput. Frozen transform/inference wall time has **not** been
benchmarked separately. Training/CV runtimes are not inference estimates.

### Current Footprint

| Component | Logical size | Allocated disk / interpretation |
|---|---:|---|
| All raw gzip HTML, 64,223 files | 3.274 GB | 3.403 GB; 64,117 paths are referenced by current scraped rows |
| Extracted plain text, 55,568 files | 142.32 MB | 291.09 MB |
| Extracted Markdown, 55,568 files | 150.39 MB | 297.66 MB |
| Extraction metadata, 55,568 files | 54.13 MB | 227.85 MB; small-file allocation is significant |
| SQLite main database | 3.448 GB | WAL was empty, SHM about 32 KB; these can grow during writes |
| `page_links` plus its two indexes | 1.986 GB | Included in SQLite, **not additional**; 6,153,806 links |
| `page_scores` table alone | 1.119 GB | Included in SQLite; not needed for new-company preparation |
| Discovery/acquisition/extraction/features/links tables and indexes together | 2.151 GB | Included in SQLite; footprint attribution, not a measured before/after growth rate |
| Historical company dataset | 74.64 MB | 74.69 MB allocated; includes research-only tables |
| Two frozen bundles plus manifests | 9.18 MB | Reuse once; do not duplicate per batch |
| Cleanup preprocessing / final-training directories | 726.75 / 30.50 MB | Historical fitting artifacts, not required inference growth |

All company-experiment directories together occupy approximately 3.16 GB; leave
them intact. New inference should persist compact inputs/scores/provenance, not
repeat the experiment cache hierarchy. Measurements use `stat`/`du`, read-only
`dbstat`, source tables and manifests; there is no historical DB/WAL growth series.

### Typical Versus Large Websites

Count each sample domain and file path once, not once per associated company.
Enriched 500 companies map to 498 domains; the earlier domain pilot has 100 domains
and only limited page extraction, so its extraction footprint is not a full-site
forecast.

| Per-domain observation | Broad pilot | Enriched sample |
|---|---:|---:|
| Current successful HTML pages: median / p95 / maximum | 13.5 / 540 / 1,019 | 14.5 / 857 / 1,126 |
| Successful extracted pages | 617 total | 54,951 total |
| HTML + extraction files, allocated total | 0.352 GB | 3.866 GB |
| Allocated files: median / p95 / maximum | 0.338 / 18.27 / 81.67 MB | 0.434 / 32.93 / 650.70 MB |

Enriched mean file storage is 7.76 MB/domain, far above its median. Large examples:
`colt.net` 650.70 MB/1,018 extracted pages; `suenodesign.at` 248.91 MB/1,006;
`punkt3.at` 219.90 MB/1,003; `tridonic.com` 153.53 MB/1,112. These exclude their DB
shares. The largest individual gzip file observed was 3.03 MB; uncompressed
responses and parser memory can be much larger. Existing `content_length` values
are not a substitute for allocated-file measurements.

### Planning Ranges, Not Measured Forecasts

Assume **30-150 attempted pages per new unique domain**, with domains no more
numerous than sampled companies; use one domain/company conservatively. Historic
attempt averages were about 99 (pilot) and 128 (enriched), but neither establishes
the distribution of this new broad pool. Allow 120 KB persistent growth per
attempted page: roughly 55 KB compressed HTML, 15 KB extraction files, 40 KB
ingestion DB/index allowance and 10 KB feature/report margin. Counting every attempt
at this cost is deliberately conservative for failures. Large HTML/link-heavy
sites can exceed it substantially; these are scenarios, not confidence bounds.

Use 3-8 acquisition results/s and 8-12 extraction pages/s, sequential stages, plus
0.5-2 hours provisionally for discovery, integrity/overlap/feature work and frozen
transforms. The latter is an **unmeasured allowance**, not an inference benchmark.
Implementation time, LLM labeling and machine sleep are outside these ranges.

| New companies | Scenario attempted pages | Persistent additional storage | Preparation time estimate |
|---:|---:|---:|---:|
| 500 | 15,000-75,000 | 1.8-9.0 GB | 1.4-11.5 hours |
| 1,000 | 30,000-150,000 | 3.6-18.0 GB | 2.2-21.1 hours |
| 2,000 | 60,000-300,000 | 7.2-36.0 GB | 4.0-40.2 hours |
| 5,000 | 150,000-750,000 | 18.0-90.0 GB | 9.2-97.5 hours |

Add a separate **4 GB working/headroom reserve** for WAL, temporary outputs,
in-flight downloads and forecasting error. At the historic enriched page rate,
the same rule yields about 7.7 GB/500 companies before that reserve. Several
thousand firms are feasible only if the first batches demonstrate a light corpus;
5,000 is not supported under current space constraints. Do not multiply the tiny
median website by company count and ignore the tail.

## Acquisition Sampling Proposal

### Eligible Frame Observed Today

Read-only joins of `firms`, `domains`, `scraped_pages` and the frozen dataset's
`companies`, `websites`, `pages` give:

- 222,911 imported firms; 167,321 with nonempty normalized domains (146,300 domains).
- 166,779 after excluding currently categorized excluded domains: 542 removed.
- Remove all 649 development IDs, then 408 other companies matching the
  conservative development-host screen: **165,722 companies / 145,424 domains**.
- The screen uses 598 development website identities plus observed final hosts,
  748 distinct hosts in total. Match normalized hosts, their subdomains, and parent
  hosts of development subdomains. This is a conservative exclusion screen, not
  proof that all such firms are legally identical. Review shared-hosting/public-
  suffix edge cases before freezing the frame; record every exclusion reason.
- None of the remaining companies maps to an already acquired domain in the
  current `scraped_pages` table. `firm_urls` has zero rows. Nonempty normalized
  domains are existing metadata eligibility, not a fresh reachability guarantee.
- 33,048 eligible firms share their normalized domain with another imported firm.
  The frame spans 306 stored activity codes and 19 populated section letters;
  one firm has no usable section code. Firm size is not available for allocation.

These are **provisional metadata-eligible counts**. Unknown aliases, duplicate
legal records and cross-domain content overlap can only reduce the independent
validation frame after further checks. No company names, model scores or new LLM
labels were used to calculate this allocation.

### Allocation and Probabilities

Use stored ÖNACE section letters, not a new semantic taxonomy. Give each populated
section a minimum of five places in 500 (two in the first 100), capped by its
population, then distribute remaining places proportional to remaining sector
capacity using largest remainders, section-letter tie-breaking. This modestly
oversamples rare sectors; otherwise it is broad, near-proportional sampling.

| Stored section | Eligible companies | First 100 | Planned 500 |
|---|---:|---:|---:|
| A | 1,308 | 2 | 8 |
| B | 219 | 2 | 6 |
| C | 15,560 | 8 | 43 |
| D | 585 | 2 | 6 |
| E | 485 | 2 | 6 |
| F | 20,581 | 10 | 55 |
| G | 37,856 | 16 | 97 |
| H | 4,309 | 4 | 16 |
| I | 14,266 | 7 | 40 |
| J | 8,751 | 5 | 26 |
| K | 7,662 | 5 | 24 |
| L | 6,065 | 4 | 20 |
| M | 23,079 | 10 | 61 |
| N | 10,085 | 6 | 30 |
| O | 105 | 2 | 5 |
| P | 1,402 | 3 | 8 |
| Q | 2,861 | 3 | 12 |
| R | 2,923 | 3 | 12 |
| S | 7,619 | 5 | 24 |
| Missing | 1 | 1 | 1 |
| **Total** | **165,722** | **100** | **500** |

Propose versioned seed `20261005`: sort legal IDs within each section, use a
specified seeded uniform shuffle, take the 500 allocation and its nested first-
100 allocation. Freeze algorithm/version, frame hash, sector denominators,
inclusion probabilities and reserve order before acquisition. For section h,
company inclusion probability is `n_h / N_h` (separately for 100 and 500). Within-
section simple random selection keeps probabilities explicit; finer activity-code
coverage is audited, not achieved by undocumented substitutions.

Proportional sampling better represents workload and population precision but may
produce few positives. AI-sector enrichment would improve positive-case yield
while distorting unweighted precision; do not repeat the prior 300/125/75 design.
If a later, explicitly separate enrichment supplement is justified, retain its
known design weights. Final label sampling can combine score strata, disagreements
and low-score controls later, with recorded conditional inclusion probabilities.
The EUR 20 budget does not imply all acquisition-pool firms will be labeled.

Acquire a shared domain once and retain every selected `bvd_id`/relationship.
Do not automatically enroll its unselected sister companies. Company features,
scores and subsequent focal-company attribution remain distinct. Group all shared
sites for uncertainty/evaluation splitting; identical websites do not justify
copying one company's LLM judgment to all legal entities.

### Independence Checks Before Validation Labels

Use the entire 649-company development inventory (including previous internal
holdout), known aliases and content groups as the exclusion reference. After
discovery, check new redirect hosts; after extraction, compare normalized content
hashes and substantive shingle overlap against development and other new sites.
Reuse the existing overlap definitions in `company_modeling_groups.py:30` and the
frozen dataset config: at least 100 words, five-token shingles, verified Jaccard
at least 0.90, material overlap of at least three pages and half the smaller site,
plus the exact single-substantive-page rule. Generic notices are not entity proof.
Candidate fingerprint search is approximate and skips frequent buckets; report
that limitation and unresolved aliases, rather than claiming perfect independence.

Exclude any new company connected to a development group from independent
validation; preserve the acquired evidence and exclusion audit. Quarantine or
defer unresolved/subthreshold substantive overlap before validation, using a
documented policy consistent with frozen feature construction. Do not silently
change frozen scores by changing quarantine membership between batches: freeze
the comparison inventory and rescore only versioned evidence snapshots if it
changes. Companies with all predictive content quarantined need an explicit
deferred state, not a normal score on empty evidence.

Record post-acquisition exclusions and acquisition nonresponse separately; do not
silently replace failures with convenient websites. A predefined same-sector
reserve can supply later draws, with draw order/probability accounting retained.
If resource stopping prevents all 500 completing, report the predeclared 100 cohort
and completion pattern, not an allegedly representative arbitrary partial run.
Before labels, reserve separate grouped data for choosing model/cutoff versus
confirming the chosen policy; new data used for cutoff calibration is not also an
untouched final test. Definitions and label allocations are intentionally not fixed here.

## Bottlenecks and Score-Preserving Opportunities

| Priority | Evidence and diagnosis | Future change and required equivalence check |
|---|---|---|
| 1. Acquisition/network tails | Measured four-hour enriched acquisition; current enriched rows include 2,290 timeouts and 5,065 HTTP errors. Their precise share of runtime is not recorded. `html_fetcher.py:173` retries up to three requests; `run_acquisition.py:264` queues every future. | Measure per-host latency/retries first. Consider bounded future queues, connection/session reuse or scheduling that avoids host bursts; keep URL coverage, timeout/retry policy and quality. Test deterministic HTTP fixtures for request targets, status/retry handling, response bytes and DB rows. Live responses cannot guarantee byte parity over time. |
| 2. Extraction and links | Serial processing and per-page commit; 1.986 GB link/index footprint is measured. Full HTML is parsed for links and each row inserted separately (`run_extraction.py:458,498,659,860`). The source-page index already exists: this is not a missing-index claim. | Profile parser, feature generation, inserts and commits separately. Batch identical inserts or reuse parsing only after fixtures show identical text/Markdown/hashes, titles/headings/features, normalized links/anchors/order and crash/resume behavior. Do not omit links or change extraction quality to save space. |
| 3. Feature/inference preparation | Historical loading cost 77.9s exceeds overlap's 24.9s. `pooled_text` at `company_tuning_features.py:136` also computes top-three pooling even when both frozen models use only means. Repeated tokenization across models/shared companies is a code-observed opportunity, not a measured dominant bottleneck. | Separate an unlabeled adapter, batch DB reads, memoize immutable content transforms, and later avoid unused pooling. Compare all deterministic feature values, feature order, missingness, sparse vectors and both decision scores against saved development inputs; require exact equality where possible and documented tight numeric tolerance otherwise. Test batch-order/shared-domain invariance. Never refit. |

First implement correctness/readiness, not parallel extraction or a broad
optimization rewrite. Pin dataset/lexicon/config/source hashes and dependency
versions from the frozen manifests. Historical research inputs remain untouched.

## Batch Resource Controls and Next Ticket

Observed free filesystem space was **23.67 GB**. Spending 20 GB would leave only
3.67 GB for the operating system and other work. Preserve an **8 GB free-space
floor** as well as the 20 GB project-increment ceiling. On this disk that permits
at most roughly **15.7 GB actual additional usage**, not 20 GB. Keep 4 GB of this
available for transient growth/uncertainty: persistent planned batches should fit
within about **11.7 GB**, less if other applications consume space.

1. **Next ticket: unlabeled frozen-inference adapter and batch readiness checks.**
   Accept explicit new IDs/company-sample membership; prepare the same reusable
   website/content and focal-company features without targets or historical
   packages; validate hashes/schema and load both bundles transform-only. Add
   complete cohort/run/resource manifests and resume checkpoints. Prove historical
   feature/score parity on fixtures/existing inputs without fitting. INPUT_ERROR
   and missing files fail/defer clearly; NO_EVIDENCE remains unscored, never EXCLUDE.
2. In a subsequent authorized execution ticket, freeze/register the proposed
   500-company frame/draw and nested **100-company first batch**. Run the existing
   sample-scoped discovery, discovered-URL acquisition and v1 extraction settings
   unchanged; no additional page/site cap, no force-refresh, no scorer-v1 or API.
   Keep website acquisition once even when membership spans batches.
3. Before each stage and periodically during execution, record wall time,
   attempted/successful/failed/skipped pages, latency/retries, distinct domains,
   logical/allocated HTML/text/metadata bytes, DB/WAL/temp growth, free space and
   peak memory. Measure feature loading, overlap, transformation and each frozen
   scorer separately. Log initial/peak/final disk totals, not just directory sizes
   after temporary files disappear. Keep the machine awake for the one-day window.
4. Stop admission of further work if the next batch's measured-rate projection
   plus tail reserve would breach either resource limit or the remaining day.
   During acquisition, request graceful interruption early if headroom is being
   consumed; the current runner cancels queued tasks but waits for active workers.
   Preserve all files/results, resume through existing-success checks, and audit
   extraction files/features after any interruption. Extraction lacks acquisition's
   equivalent finalized interruption summary; an external batch ledger is needed.
5. After the first 100, report coverage/NO_EVIDENCE, failures, sector/site-size
   distribution, new overlap exclusions, pages/domain, bytes/page and bytes/domain
   (mean/p50/p95/max), stage throughput, transient peak and score parity. Use actual
   discovered queues as well as first-batch costs to admit the next cohort. Add the
   remaining 400 in predetermined waves of at most 100 companies, without limiting
   any selected website's existing coverage. Schedule shared-domain acquisition only
   once; retain predeclared company inclusion and batch assignments for evaluation.

No unconditional disk guarantee is possible with the current fetcher: responses
are loaded in memory, there is no response-byte ceiling, and request timeout is
not a total wall-clock deadline. Twenty in-flight responses, decompression, WAL,
temporary extraction files and a stalled transaction can exceed a simple mean-cost
forecast. The 4 GB reserve and early stop are safeguards, not a mathematical bound.
Do not delete research artifacts, copy the SQLite corpus, compact/rewrite storage,
or quietly add quality-changing limits. If measured headroom is insufficient,
stop and obtain more storage/time before expansion.

This audit establishes readiness gaps and a first-batch design only. No new
independent labels, population performance claim, winning-model change or selection
cutoff was produced. Milestone 5.4 continues with readiness implementation,
measured acquisition batches, then the separately specified independent labeling
and cutoff-calibration study.

## 2026-10-06: Sample-Based Scoring (Milestone 5.4b)

The unlabeled input adapter is now available. From the repository root:

```bash
.venv/bin/python -m ai_ecosystem.calibration.score_company_sample \
  --sample-id ai_enriched_v1 \
  --output data/interim/calibration/company_scores/ai_enriched_v1.csv
```

Replace the sample ID and output filename for another registered sample. Explicit
`sample_companies` membership is authoritative; legacy domain samples retain the
existing expansion through firms/firm URLs. Empty company samples do not expand
to sister companies. Unknown or empty samples fail clearly. Output files are never
overwritten; choose a new filename for a rerun. Optional `--database` and
`--frozen-directory` arguments select a read-only source DB and frozen bundles.

The CSV contains `bvd_id`, `state`, `reason`, `predictive_content_available`,
`reference_logistic_size` and `cleaned_grounded_combined`. Scores remain signed
decision-function values, not probabilities or KEEP/EXCLUDE decisions. NO_EVIDENCE
rows retain blank scores. Missing/corrupt required text fails as INPUT_ERROR rather
than silently becoming missing evidence. Existing PROCESSABLE/quarantine semantics
remain unchanged; the predictive-content flag exposes fully quarantined evidence.

Only existing successful v1 extracted data is loaded; there are no requests for
websites or LLM labels. New companies need no calibration records or packages.
Historical source loading remains available unchanged by default. Feature
definitions, numeric transforms, lexical blocks, mean pooling and models are reused
without refitting. Short-lived input tables for the existing lexical loader are
removed after preparation; no experiment cache/progress system is added.

The frozen dataset's raw company/website/page/content tables remain necessary as
overlap context, with current sample websites substituted before applying the
existing grouping/quarantine rules. This prevents a subset of historical companies
from losing the original cross-site duplicate context. Configuration and context
table hashes and model checkpoint hashes are verified. No historical targets or
phrase-supervision tables are read for scoring. Context companies outside the
sample are not scored or exported. As before, quarantine can change when evidence
or the comparison inventory changes; this is not a new duplicate-handling policy.

This command does not register samples, reject development-overlap companies from
an independent study, choose cutoffs or add acquisition resource controls. Those
remain separate work. Synthetic tests cover membership, unlabeled/unseen firms,
legacy expansion, missing evidence, read-only operation and historical feature/
score parity. The full 261-test suite, compilation and whitespace checks pass.
Real-data verification on all 500 `ai_enriched_v1` companies reproduced historical
feature values and both saved frozen scores exactly: 353 PROCESSABLE and 147
NO_EVIDENCE. The comparison aligns tabular features by name; the unchanged frozen
transformers enforce their saved model-column order. Verification CSVs were temporary.
The legacy `pilot_v0_2` sample also matched exactly for all 149 companies (123
PROCESSABLE, 26 NO_EVIDENCE). Database size/modification time was unchanged across
both checks; historical model and experiment files were only read.

## 2026-10-06: Expandable Acquisition Pool (Milestone 5.4c)

Create and register a 1,500-company pool, without running any web/data-processing
stage:

```bash
.venv/bin/python -m ai_ecosystem.calibration.build_acquisition_pool create \
  --pool-id independent_validation_v1 --target-size 1500 --seed 20261006
```

The full sample is `independent_validation_v1`. Its first batch is
`independent_validation_v1_batch_0001`; the initial pool has 15 batches of 100,
ending in `_batch_0015`. Use a batch ID with existing `--sample-id` discovery,
acquisition, extraction or company-scoring commands. The builder itself invokes
none of them. Registration of 1,500 companies is not a commitment to acquire them
all at once; retain the first-batch resource assessment.

Extend the same pool to 3,000 total companies later:

```bash
.venv/bin/python -m ai_ecosystem.calibration.build_acquisition_pool extend \
  --pool-id independent_validation_v1 --target-size 3000
```

Extension reloads the saved frame and sector lists, never the current population
as a new draw. It preserves all old company ranks, batch memberships and batch
registry rows, adding batches `_0016` through `_0030` in this example. The seed
cannot change. Existing pool IDs/artifact directories are never overwritten by
creation, and shrinking or repeating the current target is rejected. A partial
batch from a non-multiple-of-100 target remains partial; additions start a new
batch rather than filling it. Final membership at a given target is independent
of the extension schedule; append order/batch IDs reflect the committed waves.

### Sampling Design and Probability Records

- Frame: imported legal-company IDs with a nonempty normalized domain, excluding
  domains already marked excluded. Exclude all 649 frozen development IDs and
  known development host families/redirect hosts, including firm-URL relationships.
  Save every exclusion reason. Shared non-development domains stay eligible;
  company IDs remain distinct and `sample_domains` stores each domain once per
  sample. Cross-batch reuse relies on the existing pipeline's successful-page
  skipping; this ticket does not change discovery or fetch behavior.
- Stratify by stored ÖNACE section A-U; absent/unrecognized codes form an explicit
  `MISSING` stratum. Only populated strata receive weight. For section h use
  `w_h = 0.8 * N_h / N + 0.2 / H`, where H is the number of populated strata.
- Allocate sequential capped Webster/Sainte-Lague seats: highest
  `w_h / (2*a_h + 1)` wins, with section-name tie-breaking and exact rational
  comparisons. Exhausted sectors leave the competition, redistributing seats
  among remaining sectors. This approximates the 80/20 mixture while guaranteeing
  nondecreasing counts as the target grows. It does not impose individual-batch quotas.
- Save a seeded permutation of every sector's eligible IDs and take its allocated
  prefix. Shuffle the initial selected pool globally. Each extension takes only
  the next sector-list entries and shuffles only those additions. Algorithm version
  `broad_sector_pool_v1`, seed and frame/source hashes are recorded. No names, model
  scores, LLM labels or website content influence the draw; development page hosts
  are read solely for exclusion.
- Full-pool inclusion probability for a company in h is `a_h / N_h`. Initially,
  inclusion in the first m positions is `(a_h / N_h) * m / pool_size`. After
  extensions, sum over waves: `(added_h / N_h) * (covered_positions / wave_size)`.
  Thus old early batches are **not** treated as a fresh uniform subset of the
  enlarged pool. These are legal-company design probabilities conditional on the
  planned target sizes, not website, acquisition-success or labeling probabilities.

Artifacts are under
`data/interim/calibration/acquisition_pools/independent_validation_v1/`:

- `frame.json`: frozen eligible records, ordered sector lists, populations,
  eligibility/source identity, seed, exclusions and allocation policy.
- `revisions/v000001/` (then `v000002/`, etc.): `state.json`, ordered `pool.csv`,
  new-batch CSVs, `unused_by_sector.json`, `batch_prefix_probabilities.json` and
  `audit.json` with allocations, sector saturation and shared-domain groups.
- The latest full-pool `pool.csv` supplies current full-pool and batch inclusion
  probabilities and all batch/order assignments. Earlier revision and batch CSVs
  retain their original probability snapshots; they are not rewritten on extension.
  `sample_sets.source_csv` points to the appropriate versioned membership export.

Registry updates for the pool and new batches share one SQLite transaction; no
schema changes or deletion/replacement of membership rows are needed. Extension
validates saved frame/state hashes, reproduces the selection history and checks
existing registry membership before appending. Missing newly selected firms,
changed domain snapshots or newly marked excluded domains cause a clear failure,
not substitution. Acquisition failure statuses do not cause redraws.

Limitations: the known-host screen is conservative, not proof of legal identity;
unknown aliases/material content overlap still need checking after extraction.
The original 649-company frozen inventory and initialized generic registry are
required for creation. Extension requires the saved artifacts and registry. It
does not silently refresh eligibility, introduce later firms or change stored
industry snapshots. Outcome-dependent stopping/label sampling needs its own
evaluation treatment. Files are written before the registry transaction commits:
an interrupted write can leave an **unregistered** revision/directory. The database
pointer remains authoritative; subsequent attempts refuse to overwrite those
files. Inspect and explicitly relocate an orphan before retrying the same operation;
do not redraw an already committed pool or delete existing research artifacts.

Only synthetic temporary databases were used to create/extend pools in this
ticket. No real pool has been registered or acquired.
Verification: 11 focused pool tests and all 272 tests pass, along with compilation
and whitespace checks. Tests include monotone capped allocation, reproducible
draws, exact membership/domain projection, immutable previous batches, probability
formulas, eligibility drift, tampering and transaction rollback.
