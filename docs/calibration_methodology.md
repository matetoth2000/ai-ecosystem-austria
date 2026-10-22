# Company Calibration Methodology

Milestone 5 is calibration-first. Company-level LLM calibration and page-model
experiments are complete through 5.8g. The current direction, agreed on
2026-09-12, is company AI-promise modeling before further page-ranker development.
The sections below the pivot record describe the earlier implementation stages;
their forward-looking statements are historical, not the current work queue.

## 2026-09-16: First Company AI-Relevance Model Comparison

Implemented the eight-candidate comparison in
`ai_ecosystem.calibration.run_company_model_experiments`, configured by
`config/company_model_experiments.yaml`. The frozen dataset is unchanged.
Artifacts and the detailed comparison report are under
`data/interim/calibration/company_model_experiments/company_ai_relevance_comparison_v1/`.

```bash
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_experiments compare \
  --config config/company_model_experiments.yaml
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_experiments evaluate-holdout \
  --config config/company_model_experiments.yaml
```

These commands have already completed for v1. New experiments require a new
configuration/output version. `compare --resume` reuses only exact-identity
caches; code/config/data changes are rejected. Holdout evaluation requires a
valid selection freeze and refuses repeat evaluation. An interrupted evaluation
after opening targets is stopped for explicit inspection, not automatically
refit or retuned. The completed run retains its original execution sources;
a later reporting-only candidate-name correction is separately recorded.

### Fixed Experimental Protocol

- Binary KEEP versus EXCLUDE supervision includes only processable firms.
  UNKNOWN text and grounded phrases are permitted only inside training-side
  preprocessing; UNKNOWN never becomes a negative target. NO_EVIDENCE has no
  relevance score. Inverse supervised-company group-size weights reduce repeated
  website dominance.
- Five frozen development folds fit vocabulary, nonempty-group IDF, grounded
  phrase vocabulary, imputation and scaling independently. Text requires support
  in two training groups, with a 50,000-term cap per channel. Mean/max page-aware
  sparse pooling is compared; titles/headings remain separate from body text.
- Numeric blocks separate N=27 structural/relational/language features, L=27
  lexical features, and Z=22 availability/size features. The three misleadingly
  named lexical `*_support` columns are total content counts and belong to Z,
  not lexical evidence. The frozen source tables are not rewritten.
- Logistic lexical, structural, text-mean, text-max, combined, grounded-phrase,
  and size-context candidates plus one small numeric tree challenger were tested.
  Development CV chose mean pooling by the predefined 0.01 AP tie tolerance.
- Hyperparameters and final candidate selection prioritize decidable AP; models
  within 0.01 are resolved by effort for 90% KEEP capture, then simplicity.
  All eight candidates were evaluated on model-selection once. The winner was
  frozen before final refitting and opening internal-holdout targets.
- Operating tables include UNKNOWN in ranked selections, separate decidable
  precision from selected known-KEEP fraction, and show operational/processable
  denominators. Group bootstrap intervals use 1,000 paired resamples; random
  comparisons cover 100 deterministic seeds. No probabilities are claimed to be
  calibrated, and no production threshold or automatic exclusion was defined.

### Findings and Handoff

The experimental winner was mean-pooled combined logistic regression with size
context, C=10. Model-selection AP was 0.818; lexical-only and grounded/combined
alternatives had very similar AP. Size context won on lower selection effort
for 90% KEEP capture. Grounded phrases did not show a clear model-selection AP
benefit, and text alone was weaker than the lexical-only model.

One-time internal-holdout AP was 0.860. Selecting 27 of 96 processable companies
captured 19 of 21 KEEP, with five UNKNOWN and three EXCLUDE. Thirty-four
NO_EVIDENCE companies remained unscored. Capture effort varied considerably
between model-selection and holdout, and positive counts are small; do not turn
these results into population thresholds. The comparison took about five
minutes and 1.41 GB peak memory; preprocessing, rather than estimator fitting,
was the main cost. No post-holdout model tuning was performed.

Next: assess transfer/generalization and independent validation requirements
before production selection. Keep the lexical-only model as a cheap reference,
preserve UNKNOWN, and retain the frozen experiment as evidence rather than a
production classifier. No new labels, OpenAI calls, population inference, or
changes to the old page scorers were made.

## 2026-09-16: Company Modeling Dataset v1

The company-level development dataset is implemented by
`ai_ecosystem.calibration.build_company_modeling_dataset`, configured in
`config/company_modeling_dataset.yaml`. It opens SQLite read-only and never
calls acquisition, extraction, scoring, annotation, or model-training runners.

```bash
.venv/bin/python -m ai_ecosystem.calibration.build_company_modeling_dataset \
  --config config/company_modeling_dataset.yaml
```

The frozen output is
`data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1/`.
Existing output directories are refused. For a reproducibility check use a new
`--output-directory`; compare `canonical_build_hash` and per-table canonical
hashes, not timestamps. Source-file hashes and implementation/config hashes are
recorded. No global fitted vocabulary or trained model is produced.

### Sources and Units

Only completed company calibration runs
`calibration_20260825T141041Z_ee153c3e` and
`calibration_20260830T181238Z_3cf58aed` supply labels and phrase supervision.
The 649 legal-company IDs retain their original 104 KEEP, 193 EXCLUDE, and
352 UNKNOWN labels. Smoke results are excluded. Company IDs join to
`firms.bvd_id`; primary normalized domains and validated primary `firm_urls`
relationships determine page membership. `domains` and the full firm registry
provide mapped-company counts. There are currently no `firm_urls` rows.

Successful v1 `extracted_pages` join `scraped_pages` and optional `page_features`.
Body files are verified and normalized with NFKC, line-wise whitespace cleanup,
and SHA-256 hashing. Original extraction hashes and file hashes remain distinct.
Titles/headings have recorded structured-metadata/acquisition fallbacks;
language uses page features, extraction, then acquisition. Acquisition and
extraction record counts are availability diagnostics, not relevance labels.

The canonical tables separate companies, websites, pages, contents, and
company-to-website/page relationships. Text is stored once per normalized
content hash; each URL retains its own title and headings. Shared-domain
relationships remain explicitly unresolved: name overlap is a soft lexical
indicator, not proof of ownership or evidence attribution. No company is
excluded simply because its name does not appear on the shared website.

### Feature and Target Boundaries

- `company_features.parquet` contains 76 numeric features with an explicit
  dictionary/allowlist: availability, structure, relationships, language,
  duplicates, and objective-defined bilingual lexical indicators.
- `company_page_features.parquet` exposes the seven named relational features
  in `RELATION_FEATURES`; IDs/content references and eligibility flags are
  routing fields, not unrestricted predictive columns.
- Raw text, titles and parsed heading text are reusable inputs. Meta descriptions
  are retained for optional future experiments but excluded from the initial
  sparse-text interface.
- Industry, links/anchors/external-domain features, scorer-v1, learned page
  predictions, geography/email, model rationales/confidence, sample/source/split
  identifiers, and annotation QA fields are not predictive inputs.
- `targets.parquet` contains development/model-selection labels. Internal
  holdout targets are separately stored in
  `evaluation_only/internal_holdout_targets.parquet`. Audit tables and
  supervision are not feature tables.

Lexical body evidence counts each unique content item once. Features expose
presence, maximum phrase-type count, mean over the strongest available 3/5
content items, fraction of contents with a match, and support capped at five.
Empty evidence has null relevance summaries, not a fabricated zero. Title and
heading presence are separate. Same-paragraph company-name matching is only a
context indicator; it does not solve negation or third-party attribution.
Structural/relational page averages describe the available page URLs; body
lengths and lexical support use unique content. Raw/log page and word counts
remain an availability block to ablate for site-size shortcuts, not a score.

### Leakage Groups and Operational States

Shared normalized domains are grouped. Material content overlap uses substantive
pages (at least 100 words), exact hashes, and bottom-hash five-token-shingle
candidate fingerprints followed by full Jaccard verification (at least 0.90).
Material grouping requires at least three matching pages covering at least
half of the smaller substantive site, or exact duplicate substantive single-page
sites. Cross-host aliases require corroborating material content. Similar names
alone and generic notices do not establish entity identity.

Near-duplicate candidate search is approximate, not an exhaustive proof of no
overlap: the audit records skipped common fingerprint buckets. All detected
subthreshold substantive overlaps and shared generic/short duplicates are
quarantined from predictive inputs, while canonical text remains intact.
This build quarantines 95 content items and places `palfinger.com` and
`palfingerepsilon.com` in the same group. There are no remaining predictive exact
duplicates spanning groups. Future verified aliases/duplicate records must be
incorporated through a new dataset version, not an in-place split change.

Whole groups are balanced approximately by label, source, and evidence state
using seed 20260915: 389 development, 130 model-selection, 130 internal holdout,
with five group-isolated development folds. This is an **internal holdout**:
historical exploration prevents claiming untouched independent validation.

Operational state is independent of the relevance label. The 476 PROCESSABLE
companies comprise 104 KEEP, 193 EXCLUDE, and 179 UNKNOWN. The remaining 173
are NO_EVIDENCE and retain UNKNOWN, not EXCLUDE. Sparse nonempty text stays
processable. Unreadable required files cause INPUT_ERROR and block publication;
they are never relabeled. Historical package visibility/prefix hashes expose
the mismatch between labeling evidence and current full local text.

### Fold-Specific Text and Phrase Interfaces

`FoldPreprocessor.from_dataset(directory, channel='body')` loads predictive
content references without reading targets or supervision. A separate
`titles_headings` channel pools page metadata. Each fit requires complete
training leakage groups; the dataset-loading interface prohibits internal
holdout fitting. Call `training_supervision(training_ids)` explicitly, then
`fit(training_ids, training_supervision)` separately in every training fold.
`transform(company_ids)` has no annotation argument and reads raw text only.
Fit is tested on synthetic fixtures; no real-data fit occurs in this milestone.

Ordinary word uni/bigrams use distinct training-group document frequency for
vocabulary selection and smoothed IDF, with a configurable 50,000-feature cap.
Page vectors are normalized and mean/max pooled into separate normalized company
blocks. No n-gram crosses a page boundary, and duplicate content identities are
pooled once. Later experiments choose pooling; this dataset does not choose a
winner. Sparse operations avoid dense page-by-vocabulary matrices.

Company-calibration phrases are re-grounded to resolved historical visible
pages with URL/ID conflict handling and token-boundary verification. Of 2,733
original signals, 2,205 are eligible; all others retain rejection reasons.
Model-generated surrounding context is not required or used as a feature.
Training-only contiguous 1-5-token candidates use NFKC/casefolding, stopword,
numeric, short-token and focal-identity filters. The initial minimum support is
two training leakage groups. Signal type defines vocabulary channels only;
validation companies receive features from raw-text matches, not their own
annotations. Vocabulary exports include training IDs/groups, input hashes and
normalization/config provenance. Page-annotation phrases are not included.

### Build Handoff

The dataset contains 55,568 successful extracted pages and 41,971 normalized
content items, with 41,876 predictive content items. Builds take roughly three
minutes and about 1.4 GB peak memory on this development environment; exact
runtime/storage are in `manifest.json`. No pages are arbitrarily truncated.
Tables are compressed Parquet; content/page references avoid per-company text
copies. Current size does not warrant partition complexity; population-scale
incremental partitions/caches are future integration work.

The next milestone is grouped company-model experimentation using development
and model-selection data. Keep UNKNOWN separate from negative labels; exclude
NO_EVIDENCE from website-model fitting while reporting it operationally. Compare
mean/max/top-k and presence/prevalence representations, and ablate size/context
and supervised phrase blocks. Refit vocabularies inside every fold, keep holdout
targets closed until evaluation, and do not infer population prevalence from
these enriched calibration samples.

## 2026-09-12: Company-Level Pivot

### Completed Exploration and Findings

The original Milestone 5 calibration/scorer-v2 work investigated how to screen
companies conservatively and select useful website evidence before expensive
LLM processing. It progressed from company calibration into page-supervision,
ranking, and evidence-package experiments. This was useful exploratory research;
the pivot follows from its findings rather than invalidating the work.

- Company calibration covered 149 companies in the sector/domain-stratified
  `pilot_v0_2` and 500 in the ONACE-enriched `ai_enriched_v1`. Combined labels
  are 104 KEEP, 193 EXCLUDE, and 352 UNKNOWN. The company prompt requires
  company-attributed AI or AI-adjacent evidence for KEEP, affirmative conventional
  activity for EXCLUDE, and preserves uncertainty as UNKNOWN. Labels depend on
  the token-limited evidence shown and are not independently verified truth.
- Scorer-v1 evaluation and corrected lexical grounding exposed website-size
  bias, generic marketing, third-party evidence, attribution ambiguity, and
  missing positive terminology. Its four lexical dimensions are uncalibrated
  historical baselines, not probabilities or required scorer-v2 targets.
- The page annotation campaign produced 3,708 responses including hidden
  repeats. Automated QA supported a versioned, domain-grouped page training
  dataset with target restrictions and trusted grounded phrases. Eight
  untrusted primary annotations were excluded from training targets; 73
  partially trusted rows retained field-level restrictions.
- Page baseline/challenger experiments showed that broad company-information
  utility can be predicted reasonably well. Relational features improved
  attribution; fine-grained attribution remained too weak for a hard filter.
  TF-IDF relational ranking and its attribution-adjusted variant led the tested
  page rankers, with negligible differences between those two variants.
- Full locked retrieval evaluation annotated 7,318 eligible deduplicated pages
  across 40 companies, plus hidden repeats. Fixed-page-count ranking was strong
  (about 78% utility-2/3 pages at top five), but token-budget evaluation exposed
  a mismatch between ranking broad utility and assembling useful packages.
- The subsequent read-only diagnostic found utility-2/3 pages constituted about
  71% of evaluable pages and 72.5% on sites with more than 100 pages. Small-site
  saturation also raised average capture. Random selection fit more short pages,
  making it competitive under token budgets despite lower utility per selected
  page. Raw TF-IDF had no demonstrated token-budget capture advantage over random
  on large sites. A 1,000-seed check did not attribute this to a lucky random run.
- Diversity-aware selection improved raw TF-IDF's 16k-token utility capture
  from 55.75% to 58.04% and reduced lexical redundancy. This supports studying
  package selection separately, but does not establish AI-specific evidence
  capture or final mapping accuracy.

The page prompt rewards specific information about company activities,
capabilities, products, relationships, or technology. It does not require AI
activity for high utility. Evidence-package selection and company AI-promise
ranking therefore have distinct targets.

### Primary Objective and Next Sequence

The primary local screening objective is:

> Identify companies with sufficiently promising, company-attributed AI or
> AI-adjacent evidence to justify final LLM analysis.

The final LLM remains responsible for substantive AI-active versus non-AI
mapping, literature-defined ecosystem roles, AI techniques/categories, and
supporting evidence. A calibration KEEP is promising AI or AI-adjacent evidence,
not an automatic final AI-active label. Page modeling supports evidence selection.

1. Build a leakage-safe company dataset and evaluation protocol from the existing
   649 company labels. Use all available local company evidence, audit missing
   coverage and package visibility, and isolate shared-domain/duplicate groups.
   Keep label rationales and sampling metadata out of predictive features. If
   learned page outputs are used, prevent training on held-out companies through
   group-safe out-of-fold predictions or appropriate model refitting.
2. Develop and compare practical company relevance/AI-promise models. Evaluate
   KEEP capture, ranked precision/recall, false exclusions, subgroups, and runtime;
   do not assume a direct three-class model or a ranking/abstention design wins.
3. Preserve KEEP, UNKNOWN, and EXCLUDE distinctions. UNKNOWN is not negative;
   missing evidence cannot justify exclusion. Assess calibration before interpreting
   model outputs as probabilities.
4. Freeze promising candidates for targeted independent validation of false
   exclusions, low-scoring possible positives, disagreements, and attribution
   uncertainty. No replacement generic calibration campaign is required before
   initial experiments. Enriched-sample selection rates are not population estimates.
5. Revisit page/package selection against capture of AI activity, roles,
   techniques, and attribution under budgets. Previously inspected locked results
   remain historical evaluation evidence; further tuning requires development data
   and a fresh independent evaluation for new performance claims.
6. Define and validate the final LLM mapping contract and end-to-end outputs.
7. Address population-scale acquisition/scoring optimization and the optional
   ecosystem network later. No production exclusion policy is approved by this pivot.

### Artifact Status

All existing artifacts are retained; none should be deleted or silently relabeled.

| Artifact group | Status and permitted use |
| --- | --- |
| Extracted corpus, sample membership, hashes, provenance, blind company packages | Valid and reusable, subject to recorded availability, visibility, and attribution limitations. |
| Company calibration labels and corrected grounded lexical signals | Reusable development supervision with trust restrictions; not population prevalence or final role/AI-active ground truth. |
| Page annotations, QA, training dataset and frozen splits | Reusable for their broad-utility/facet targets; preserve field restrictions and split lineage. |
| Scorer v1, its four dimensions and KBs | Immutable historical baseline and isolated optional features; no calibrated exclusion interpretation. |
| Page rankers, attribution models and diversity selectors | Provisional, development-only candidates/features; not frozen production policies. Broad-utility optimization is no longer the primary workstream. |
| Experiment reports and locked 5.8g evaluation | Valid records of tested objectives and limitations; not proof of AI-activity detection or a fresh test set after subsequent tuning. |
| Earlier deterministic company-relevance classifier | Previously deprecated historical work; this pivot does not reactivate its rules or storage as a new classifier. |

Immediate next milestone: **Company Training Dataset and Evaluation Protocol**.
It prepares data and evaluation boundaries without training models, changing
annotations, making new API calls, or executing production exclusions.

## Blind Evidence Packages

`python -m ai_ecosystem.calibration.build_company_evidence_packages` builds one
package per company in `pilot_v0_2`. The calibration review unit is the company,
not the domain. A shared domain produces one package for every mapped company;
each package identifies the focal company and the other mapped companies.
Domain-level pages are potentially ambiguous evidence, not an attribution claim.

Only `blind_inputs/` is for the calibration model. It contains neutral company
and domain metadata, acquisition/extraction availability, and page identifiers,
URLs, titles, headings, languages, word counts, and extracted content. It does
not contain scores, score components, query-family matches, keyword matches,
boost terms, negative terms, rankings, or prior retention decisions. The builder
validates generated fields and Markdown labels for scorer leakage before output.

All usable extracted pages are included with deterministic, scorer-blind
structural ordering: the root URL first, then obvious About/Company paths, then
remaining pages by normalized URL-path depth and `scraped_page_id`. About/Company
recognition uses only a small fixed set of exact path components. Inclusion never
uses scores, page content, page-type classifiers, or retention. With the default
50,000-token budget, content is retained in that order until the budget is
exhausted. Packages record complete, partial, and metadata-only pages.
The blind JSON retains metadata-only records for every usable page, preserving
the complete page inventory for auditability and reproducibility. The
model-facing Markdown renders only complete and partially truncated pages,
followed by a compact count of omitted metadata-only pages. Omitted page blocks
therefore do not consume the model-input budget. The current token estimate is
deterministic and recorded in the manifest.

## Separate Analysis Metadata

`analysis_metadata/` is explicitly marked `send_to_openai: false`. It retains
the configured coherent scoring run plus page-level scores, components, and
explainability for post-hoc comparison with independently obtained calibration
labels. It must never be included in a calibration request. Missing score
coverage is recorded rather than filled from another scoring run.

This separation prevents circular validation: score data is available for later
analysis but cannot influence the LLM label.

## Scope

This is a pilot-only builder. It makes no OpenAI requests, classification or
threshold decisions, page-retention changes, or full-corpus samples. The next
milestone is a versioned company-level calibration prompt and API runner that
consumes only the blind package files.

## OpenAI Calibration Run

Milestone 5.2 adds `python -m ai_ecosystem.calibration.run_company_calibration`.
It uses the OpenAI Responses API with a strict JSON schema, no web search or
other tools, and the versioned prompt
`src/ai_ecosystem/calibration/prompts/company_ai_calibration_v1.txt`. The model,
reasoning effort, prompt ID/version/hash, response identifiers, raw response
metadata, token usage, and a configuration-based estimated cost are persisted in
`calibration_runs` and `calibration_results`. These tables are independent of
the deprecated deterministic company-relevance tables.

The label definitions are deliberately conservative. `KEEP` needs credible,
company-attributed AI or AI-adjacent capability evidence. `EXCLUDE` needs
substantive, affirmative, reliably attributed conventional non-AI activity and
no credible counter-evidence. `UNKNOWN` is used for insufficient content,
uncertain attribution, shared/group-domain ambiguity, or unresolved evidence;
it is never a low-confidence exclusion. ONACE is contextual only and cannot
determine a label by itself.

Every AI-relevant statement is assessed for subject attribution. Customer,
partner, supplier, employee, collaborator, third-party, and unclear statements
do not normally support a `KEEP`. Shared domains require focal-company or
clearly inclusive entity attribution; otherwise the model should return
`UNKNOWN`.

The structured response includes lexical candidates grouped as positive AI,
AI-adjacent, false-positive, third-party AI, generic marketing, conventional
activity, and useful context patterns. Each candidate includes its subject,
page reference, phrase, context, strength, and advisory scorer recommendation.
The runner records literal phrase/context grounding checks in the output. These
signals are observational only: no term is automatically promoted into a
knowledge base or scoring configuration.

Run artifacts live in `data/interim/calibration/openai/<calibration_run_id>/`:
`run_summary.json`, `results.json`, `results.csv`, `lexical_signals.csv`, and a
prompt snapshot. `--resume` skips successful company rows in the same run;
failed rows remain retryable. Batch API support was deliberately deferred: the
standard Responses API runner is the implemented, tested path.

The protocol runs a mixed score-blind smoke sample before the full pilot. The
smoke uses only neutral package metadata and blind raw-content strata, never
page scores, to include no-content, sparse, shared-domain, conventional,
news-heavy, rich-content, and AI-looking cases. The prompt version must not be
changed after beginning the full run.

## ÖNACE-Enriched Calibration Sample

The original `pilot_v0_2` calibration run produced 1 `KEEP`, 99 `UNKNOWN`, and
49 `EXCLUDE` labels across 149 companies. It was useful for validating the
scorer-blind package and attribution protocol, but it does not provide enough
credible positive or borderline technical cases to calibrate later company-level
decisions.

Milestone 5.3 therefore adds `ai_enriched_v1`, a separate 500-company
calibration sample selected before any new web pipeline work. Selection uses
only the imported source field `ONACE 2008 Haupttätigkeit 4-stellig - Code` and
existing domain eligibility metadata. It does not use firm names, website text,
page scores, previous calibration labels, query families, or keywords.

The versioned taxonomy is stored in `config/calibration_enrichment.yaml`. It
uses the [Statistik Austria ÖNACE 2008 classification](https://www.statistik.at/kdb/downloads/pdf/prod/OENACE2008_DE_CTE.pdf)
for every exact stored code, description, and rationale. The fixed seed is
`20260825`; default targets are 300 `HIGH_POTENTIAL`, 125 `AI_ADJACENT`, and
75 `CONVENTIONAL_CONTROL` companies. Within each stratum, code-specific seeded
shuffles and breadth-first round-robin sampling prevent common IT and
engineering codes from dominating the sample.

Only firms with a normalized, non-excluded domain are eligible because the next
stage is website discovery. Firms sharing a domain remain distinct legal-company
sample members. `sample_companies` is the authoritative company-level
membership registry; `sample_domains` contains only its unique domain projection
for discovery. Existing `pilot_v0_2` companies, including firms sharing a pilot
domain, are excluded by default.

This is an AI-enriched calibration sample, not a probability sample. Its later
`KEEP`/`UNKNOWN`/`EXCLUDE` distribution must never be interpreted as an estimate
of Austrian AI prevalence. The next operational stage is discovery only:

```bash
.venv/bin/python -m ai_ecosystem.discovery.homepage_discovery --sample-id ai_enriched_v1
```

## 2026-09-16: Milestone 5.3a - Focused Company Model Tuning

All 649 existing calibration companies are now development evidence, including
the previously opened internal holdout. This change does not alter the frozen
dataset, previous split artifacts, previous experiment, or the old runner's
holdout protections. The new tuning loader explicitly reconciles the separately
stored former-holdout targets. A future new-company sample with broader website
evidence is required for independent evaluation.

The prediction unit remains `bvd_id`. Existing domain/content leakage groups
are indivisible in two repetitions of five outer folds, each containing three
inner folds. Seeds are `20260916` and `20260917`. Group assignments balance
labels, calibration source and operational state. Only PROCESSABLE KEEP/EXCLUDE
rows supervise logistic regression. PROCESSABLE UNKNOWN rows receive scores;
NO_EVIDENCE rows remain operationally separate and unscored.

Average precision on KEEP versus EXCLUDE is the primary comparison metric.
Within 0.01 AP, selection prefers operational F1, then lower selection effort,
then stronger regularization and fewer features. Inner folds choose C and an
operating fraction from 1-100%; outer folds evaluate that fixed choice. This is
not a production threshold and is not selected for a fixed KEEP-capture target.

Operational precision is selected KEEP divided by all selected companies;
recall is selected KEEP divided by eligible KEEP. Every operational precision/F1
result includes KEEP/UNKNOWN/EXCLUDE composition: KEEP is desired; UNKNOWN is
unresolved but costly; EXCLUDE is negative under current calibration. UNKNOWN
reduces operational yield without becoming a negative training label. Standard
KEEP-versus-EXCLUDE precision/F1 is separately reported, as are effort at
80/90/95% capture, full operating curves, group-weighted sensitivity results,
and paired group-bootstrap uncertainty conditional on the fitted CV models.

Eight candidates are frozen in `config/company_model_tuning.yaml`: exact old
`logistic_size` and lexical specifications, expanded lexical-only, lexical plus
cleaned grounded phrases, combined text/relational/lexical without size, with
original size, with saturated size, and saturated size with top-three text
pooling. References keep C=10 and C=0.1 respectively; challengers choose from
0.1/1/10 inside inner folds. The original scorer-v1 and page models are not used.

`config/company_tuning_lexicon.yaml` defines bilingual objective-based explicit,
adjacent and ambiguous categories with provenance/rationales. Short acronyms
have separate standalone/context-supported indicators. Lexical summaries count
unique content, use support cap five, and retain title/heading and focal-name
paragraph signals; none proves attribution. Conservative context saturates
unique-content counts at 50, total words at 50,000, and median words at 2,000,
with validated availability ratios instead of redundant raw volume fields.

Mean text pooling remains the reference. Top-three pooling averages each
vocabulary coordinate's three largest page values, including zeros, or all
available pages if fewer than three exist, then normalizes the company vector.
It does not select three whole pages. Body and title/headings channels remain
separate; identical body content is not counted repeatedly.

Vocabularies, group-frequency IDF, imputation/scaling, and grounded phrase
filtering are training-fold-local. Grounded candidates use eligible company
annotations, not page annotations. Cleaning rejects numeric, generic and source
identifier terms, favors contiguous multiword phrases, and requires two source
groups. Frequent corpus terms above 60% are rejected unless allowlisted.
Each partition compares all-annotation versus KEEP/EXCLUDE-only vocabularies
using identical cleaning and training-text frequency references. Size, overlap,
channels, frequency and unique terms are diagnostic only; this comparison does
not add a ninth model. Transform never accesses target-company annotations.

Read-only preflight, benchmark, and full resume commands are:

```bash
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_tuning --validate-only
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_tuning --stop-after-outer 1
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_tuning --resume
```

Outputs are under
`data/interim/calibration/company_model_experiments/company_ai_relevance_tuning_v2/`.
`progress.json` and `run.log` show current partition/candidate, completed versus
expected fits, cache status, elapsed time, checkpoint and failure/retry details.
The schedule contains 600 inner fits and 80 outer refits. Checkpoint hashes,
not progress counters, determine completed work on resume. Incompatible source,
configuration, dataset or environment identities are rejected; prior runs are
never silently overwritten. Completed-run resume verifies artifacts without
refitting. No ETA model or automatic parameter changes are used.

`outer_predictions.parquet`, `outer_metrics.parquet`, `operating_points.parquet`,
`repetition_metrics.parquet`, `paired_intervals.parquet` and
`selection_procedure_metrics.parquet` distinguish candidate development results
from the inner-selected procedure. Raw logits from separate fits are not pooled
for headline ranking metrics. Full coefficients remain with outer fits; compact
coefficient/block/stability diagnostics, vocabulary comparisons, subgroup and
size diagnostics, disagreement tables, and historical visibility mismatches
support interpretation. Possible label/evidence conflicts never change labels.

Numerical improvement requires either AP +0.02 with a positive paired interval
and no more than 0.01 operational-F1 loss, or AP within 0.01 of the reference,
operational F1 +0.02, selection share -0.05, and at most 0.02 recall loss.
Directions must agree across repetitions and subgroup limitations must be
inspected. These are development criteria, not proof of population validity.
No deployment model, production exclusion rule or automatic new labels are
created by this milestone.

### Verified Tuning Outcome

The completed run contains 680 fits, 40 preprocessing/vocabulary partitions and
10,384 outer prediction rows: every one of the 649 companies appears once per
candidate per repetition. All 173 NO_EVIDENCE companies remain unscored; the
179 processable UNKNOWN companies receive scores without negative supervision.
The complete schedule took 2,465.5 seconds, peaked at 2.42 GB memory, and wrote
about 1.91 GB of versioned caches/models/reports. The benchmark pause/resume and
completed-run verification both succeeded without replacing completed fits.

`lexical_grounded_v2` led mean AP at 0.9242, compared with 0.9117 for the old
size reference. Its paired AP-difference interval was approximately
[-0.0195, 0.0476], so the observed gain is uncertain. At inner-selected operating
fractions, its operational F1 was 0.7037, recall 0.7740 and selected share
0.2615, versus reference 0.7311 / 0.8219 / 0.2721. Mean selected compositions
per outer fold were 16.1 KEEP / 7.0 UNKNOWN / 1.8 EXCLUDE for grounded and
17.1 KEEP / 6.4 UNKNOWN / 2.4 EXCLUDE for the reference. Neither this challenger
nor the other six alternatives passed the predefined improvement gates.

Keep the frozen reference and preserve the grounded variant for independent
comparison. Top-three versus mean pooling and conservative versus original
size context did not establish a meaningful overall improvement. Source and
site-size diagnostics remain important: the pilot contributes only one KEEP
overall, and subgroup results with few positives are not reliable ranking
estimates. `subgroups.parquet` reports within-subgroup ranking at the chosen
fraction, not the composition of a single globally selected production set.

The all-annotation grounded vocabulary averaged 122.3 terms versus 82.4 from
KEEP/EXCLUDE annotations alone. Jaccard overlap ranged from 0.534 to 0.809;
39 of 40 partitions exceeded the predeclared 20% difference flag. All
KEEP/EXCLUDE-only vocabularies were subsets of their all-annotation counterparts
under the common training-text frequency reference. This identifies a
substantial UNKNOWN contribution, not proof that those terms are harmful.
No extra model was added after seeing this diagnostic.

All 244 tests passed, including 16 tuning tests. Compilation, whitespace,
source preservation, fold/identifier completeness, operational composition,
checkpoint hashes and completed-run no-refit resume were verified. Final
recommendations remain development findings; no old holdout score is presented
as a comparable independent benchmark for this round.

## Milestone 5.3b: Grounded Cleanup and Model Freeze

This single bounded follow-up uses `company_ai_relevance_cleanup_v3`. It preserves
the dataset, historical artifacts, all 649 company labels, and the exact 40
training partitions from tuning v2. Its three candidates are the original
`reference_logistic_size` (fixed C=10), `cleaned_grounded_lexical` (expanded fixed
lexical plus cleaned grounded features), and `cleaned_grounded_combined`
(mean-pooled body/title text, original structural/relational N, expanded fixed
lexical and cleaned grounded features, without explicit size Z). Challengers
use only C=0.1/1/10. The expanded fixed lexical block itself is unchanged.

`config/company_model_cleanup.yaml` freezes an explicit technical unigram list
and fragment policy before fitting. Fixed-lexicon aliases are not implicitly
allowed grounded unigrams. Number-bearing/source-identifier candidates remain
invalid. Multiword candidates with stopword boundaries, entirely generic tokens,
or at least 50% generic/stopword tokens are rejected. Exact explicit-AI/adjacent
multiword aliases are protected from fragment checks, not grounding or support
checks. Specific conventional evidence remains eligible. Support requires two
training leakage groups; nontechnical phrases over 60% training-group frequency
are rejected. These are feature-cleaning rules, not company classifications.

Grounded support is pooled by normalized phrase across annotation channels;
channels are provenance only. Training-constant columns are removed. Phrases
with identical binary presence vectors across PROCESSABLE training companies
share a single alias-union feature. At transformation time any alias activates
that feature; neither labels nor annotations are consulted. Normalization follows
deduplication. All/KEEP+EXCLUDE vocabulary diagnostics are compared before vector
collapse, so UNKNOWN's contribution is not conflated with feature equivalence.

The runner explicitly validates the dataset configuration snapshot, source data,
parent artifact hashes, unchanged feature implementations, memberships and
dependency versions before importing old text/numeric preprocessing blocks.
New cache identities also include cleanup policy and implementation hashes.
Models are refitted; old models and reports are never overwritten. Integer
percentage selection counts are shared by metrics and bootstrap. Historical
uncertainty is regenerated only under the new run's correction directory, with
an assertion that historical point estimates remain unchanged.

```bash
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_cleanup --validate-only
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_cleanup
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_cleanup --resume
.venv/bin/python -m ai_ecosystem.calibration.run_company_model_cleanup --verification-output data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3_verification
```

The comparison has 210 inner fits and 30 outer fits. AP tolerance and operational
selection semantics remain unchanged: selected KEEP is desired, selected UNKNOWN
is unresolved but costly, and selected EXCLUDE is negative under calibration.
NO_EVIDENCE remains unscored and never becomes EXCLUDE. Standard and operational
metrics, composition, capture effort, group-weighted sensitivity and paired
uncertainty are reported. The operational improvement route additionally requires
a positive paired F1 interval; both improvement routes require directional
consistency across repetitions. A weak result retains the reference.

After comparison, two models are frozen: the reference and strongest cleaned
comparator. Challenger C is selected from saved inner-CV curves only, averaged
equally across the balanced nested partitions; outer outcomes choose the
architecture/comparator, not its final regularization. Final preprocessing may
use all development-group text and eligible supervision; supervised fits use only
PROCESSABLE KEEP/EXCLUDE. Serialized bundles contain fitted transforms, feature
ordering, phrase aliases, policy/version provenance and reload checks, but no
production decision threshold. Reload predictions are serialization checks, not
performance estimates. `FrozenCompanyScorer.score` consumes deterministic raw-text
document maps, numeric/lexical tables and evidence states without annotation input.

Reports are under `reports/`, per-partition vocabulary records under
`preprocessing/`, corrected historical intervals under
`historical_uncertainty_correction/`, and model bundles under `frozen/` in the new
experiment directory. Evidence on pages omitted during historical labeling is
retained as a possible evidence-label mismatch, not automatically relabeled or
removed from metrics. The next stage is independent validation with new companies
and broader website evidence, not another tuning iteration.

### 2026-09-17: Completed Cleanup and Frozen Models

The bounded comparison completed 210 inner fits, 30 outer fits and two final
full-development fits. Forty validated parent preprocessing partitions were
imported; no model fits were reused. The original 649 companies and 597 leakage
groups were preserved: 476 PROCESSABLE (104 KEEP, 193 EXCLUDE, 179 UNKNOWN) and
173 NO_EVIDENCE. Final supervised training used only the 297 KEEP/EXCLUDE rows.

| Candidate | Mean outer AP | Operational F1 | KEEP recall | Selected share |
|---|---:|---:|---:|---:|
| reference_logistic_size | 0.9117 | 0.7311 | 0.8219 | 0.2721 |
| cleaned_grounded_lexical | 0.9175 | 0.6726 | 0.7650 | 0.2762 |
| cleaned_grounded_combined | 0.9214 | 0.6969 | 0.7460 | 0.2490 |

Mean selected KEEP/UNKNOWN/EXCLUDE counts per outer fold were 17.1/6.4/2.4 for
the reference, 15.9/7.9/2.5 for lexical and 15.5/6.3/1.9 for combined. These are
enriched-development-cohort results, not population selection estimates.
Neither challenger passed either predeclared improvement route. Combined's AP
gain was 0.0096, with paired 95% interval [-0.0154, 0.0372]; its operational F1
change was -0.0342, with interval [-0.0791, 0.0190]. Higher point AP therefore
does not establish superiority.

The frozen primary is `reference_logistic_size`, C=10. The frozen challenger is
`cleaned_grounded_combined`, C=1, selected using saved inner-CV results. Both
fitted bundles and their provenance are under
`data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen/`.
`freeze_manifest.json` records the roles; each candidate directory contains
`model.joblib`, its manifest and reload verification. There is no production
operating threshold. The architecture is now frozen for independent validation.

Across 40 training partitions, 84-188 historical channel columns became 37-98
filtered canonical phrases and 35-85 final grounded columns. Vector-equivalence
collapse removed 0-13 additional columns per partition. Technical phrases such
as `machine learning` and `computer vision`, and informative conventional
phrases such as `transport und logistik`, remain eligible. Generic unigrams
`software`, `smart`, `intelligent` and `optimierung` are disallowed. UNKNOWN
supervision still changes at least 20% of vocabulary in 32/40 partitions;
this is a composition diagnostic, not evidence that UNKNOWN supervision helps
or harms prediction. Cleaning remains rule-based: broad phrases such as
`real time` can survive. No post-result rule revisions were made.

Saved coefficient/contribution diagnostics show that the expanded fixed lexical
block supplies the largest mean absolute net block contribution in both cleaned
models; the grounded block is nonzero but smaller. Coefficients are associations,
not validated attribution or causal effects. Historical evidence-package gaps
remain traceable diagnostics without changing labels or headline metrics.

Integer-rounding corrections left historical point estimates unchanged. The
largest absolute change among reported historical uncertainty endpoints was
approximately 0.000813. Corrections live only in the new experiment directory.
The comparison took approximately 538 seconds with peak process memory 1.45 GB.
All ten reference outer partitions reproduced prior predictions. A separate
fresh-preprocessing reproduction of the first outer partition passed for 390
company/candidate rows; both frozen bundles reloaded with zero prediction
difference. Completed-run resume validated hashes without refitting. Dataset
and parent experiment hashes were verified before reuse and after completion.
All 256 tests passed, including 12 cleanup tests; compilation and whitespace
checks passed.
No API calls, new labels, population inference or further tuning were performed.

## 2026-10-22: Independent Company-Scorer Validation (Milestone 5.5e)

The selection objective is to capture as many genuinely AI-relevant companies
(KEEP) as possible within a constrained final LLM budget. The prediction unit is
the legal company; shared website evidence does not by itself prove focal-company
activity. The original development set comprised 649 historically calibrated
companies (104 KEEP, 193 EXCLUDE, 352 UNKNOWN), of which 476 were PROCESSABLE.
The cleaned model was trained only on processable KEEP/EXCLUDE labels, with
UNKNOWN kept distinct and NO_EVIDENCE unscored. Grouped development evaluation
and the two fitted frozen bundles are described above. After the development
comparison did not establish a clear challenger advantage, the user chose the
unchanged `cleaned_grounded_combined` (C=1) for independent validation.

The new broad-sector pool comprised 1,500 companies (1,495 domains), sampled
with a recorded seed and an 80% population-proportional/20% equal-sector
allocation. Development companies and known shared development websites were
excluded at sampling. Of the pool, 416 had NO_EVIDENCE; of 1,084 initially
PROCESSABLE companies, two clear development overlaps and one empty packaged
evidence case were excluded. Review of 64 possible generic-content overlaps
approved their inclusion, yielding 1,081 requests. A domain/content-grouped,
sector-balanced split assigned 865 to selection and 216 to holdout, with no
known website group crossing splits. Overlap screening was not exhaustive.

The labeling contract was `company_ai_profile_v4` with v4 Python label mapping:
GPT-6 Luna (low reasoning) assessed focal-company AI activity/adjacency and
profile fields, while Python derived KEEP/UNKNOWN/EXCLUDE. Requests allowed up
to 250,000 evidence tokens and 8,000 output tokens; some large websites could
have evidence omitted. Quotations had to match the cited page exactly, except
for the approved leading/trailing-whitespace rule. Originals remain preserved.
Three selection and two holdout responses were recovered separately, only by
correcting a wrong page ID when the unchanged exact quote uniquely identified
another supplied page and the whole response then validated. Unresolved
responses were never treated as EXCLUDE. These LLM-derived labels have not been
independently human-verified.

Before holdout labels were opened, the [policy freeze](../data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/policy_freeze.json)
fixed the model, exact `score >= cutoff` rule (all ties included), three budget
scenarios, metric definitions, recovery rule and 20% budget reserve. No minimum
performance target was specified and no policy was optimized after holdout
access. Confirmed-KEEP precision is selected KEEP / selected *labelled*
companies (including UNKNOWN); observed KEEP recall is selected KEEP / all
labelled KEEPs in that split. The two splits are reported separately:

| Scenario | Exact cutoff | Selection selected; KEEP/UNKNOWN/EXCLUDE/unresolved | Selection precision; recall (of 52) | Holdout selected; KEEP/UNKNOWN/EXCLUDE/unresolved | Holdout precision; recall (of 18) |
|---|---:|---|---|---|---|
| $100 | 1.2235276873548995 | 72; 34/4/32/2 | 48.6%; 65.4% | 26; 13/2/9/2 | 54.2%; 72.2% |
| $150 | 0.2706790875316717 | 117; 45/9/60/3 | 39.5%; 86.5% | 37; 15/2/17/3 | 44.1%; 83.3% |
| $200 | -0.10212121864474141 | 143; 50/11/79/3 | 35.7%; 96.2% | 44; 16/2/23/3 | 39.0%; 88.9% |

The selection set had 843 originally valid, three recovered and 19 unresolved
responses: 846 labelled (52 KEEP, 60 UNKNOWN, 734 EXCLUDE). The holdout had
208 originally valid, two recovered and six unresolved: 210 labelled (18 KEEP,
10 UNKNOWN, 182 EXCLUDE). In the selection set, the $100/$150/$200 policies'
extreme missing-label precision bounds were 47.2-50.0% / 38.5-41.0% /
35.0-37.1%, and recall bounds were 49.3-66.7% / 66.2-87.3% / 73.5-96.4%.
Their descriptive 95% website-group bootstrap intervals were 36.4-60.6% /
30.0-49.2% / 27.1-44.3% for precision and 51.1-78.3% / 76.6-95.4% /
90.2-100.0% for recall. The holdout's 95% website-group bootstrap intervals
were, for $100/$150/$200 respectively, 33.3-73.9% / 27.3-62.1% / 24.1-53.8%
for precision and 52.3-92.3% / 65.0-100.0% / 73.3-100.0% for recall.
Extreme missing-label precision bounds were 50.0-57.7% / 40.5-48.6% /
36.4-43.2%; corresponding recall bounds were 59.1-75.0% / 71.4-85.7% /
76.2-90.5%. These bounds assign unresolved cases extreme labels and are *not*
confidence intervals; bootstrap intervals are conditional on available labels
and groups, not population uncertainty. The [holdout report and charts](../data/interim/calibration/company_score_analysis/independent_validation_holdout_v1/report_v2.md)
and [selection report](../data/interim/calibration/company_score_analysis/independent_validation_selection_v3/report.md)
retain full denominators, recovery provenance and supporting company tables.

The scenario names denote prospective $100/$150/$200 final-pass budgets, each
with 20% reserved ($80/$120/$160 planned request spend). Conditional
wider-frame request-cost projections made *before* holdout access were
$77.66/$119.96/$130.65; they assume the wider processable population resembles
the selection set in joint scores and per-company costs, and are not established
Austria-wide spending estimates. Observed selected-request costs on the
holdout were only $0.2356/$0.2886/$0.3619. Costs were calculated from saved
Batch input/output usage at documented rates, with reasoning already included
in output tokens. The user-reported $1.91 total balance deduction for five
Batches remains unreconciled with $1.944 calculated for selection usage alone;
neither is an invoice-confirmed per-company price. The [cost analysis](../data/interim/calibration/company_score_analysis/independent_validation_usage_costs_v2/report.md)
documents rates and conditional scenarios.

The model and three *evaluation* policies are frozen; no further training
experiment is planned. A final deployment budget/policy and acquisition scale
are still undecided. The holdout contains only 18 labelled KEEPs; six holdout
and 19 selection responses remain unresolved, some websites share evidence,
416 pool companies lacked usable website evidence, and residual overlap and
LLM-label error limit generalization. Neither Austria-wide recall nor spend is
established. Wider acquisition, final ecosystem mapping and optional graph
construction remain future work, not completed parts of this validation.

### Reproducibility and Publication Boundary

The analysis links above point to local `data/interim/` artifacts, which are
intentionally excluded from Git because they contain licensed/source-derived
company evidence, model responses, scores or bulk generated data. They are not
downloadable from the public repository. The committed source modules,
versioned prompt/schema files, configuration and tests specify the preparation,
validation and analysis procedures. Reproducing numeric results requires the
preserved local source database/corpus, calibration and Batch artifacts, and
frozen model bundles; these must be obtained through the project's authorized
research storage, not reconstructed from public Git files alone. The exact
policy thresholds and headline counts above remain in this public methodology
summary; original request/response bodies, company-level tables and raw source
data remain local.
