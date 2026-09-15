# Company Calibration Methodology

Milestone 5 is calibration-first. Company-level LLM calibration and page-model
experiments are complete through 5.8g. The current direction, agreed on
2026-09-12, is company AI-promise modeling before further page-ranker development.
The sections below the pivot record describe the earlier implementation stages;
their forward-looking statements are historical, not the current work queue.

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
