# Deprecated Company Relevance Classifier

This document is retained only as a deprecation marker for the experimental deterministic company relevance classifier implemented during the earlier Milestone 5.1-5.3 work.

That classifier has been superseded. It was not empirically calibrated, its page-score thresholds assumed a score scale that is not valid for the current unbounded lexical scoring outputs, and its keyword-based conventional-business rules are not suitable for production exclusion decisions.

The following old runtime paths were removed from active code:

- `CompanyEvidenceAggregator`
- `CompanyRelevanceClassifier`
- `CompanyRelevanceEvidence`
- `CompanyRelevanceDecision`
- company classification runner
- latest-KEEP export
- `config/company_relevance.yaml`

The historical SQLite tables `company_relevance_runs` and `company_relevance_classifications` remain in schema and migration code for reproducibility and old database readability only. They must not be used as the current production classification system or as a source of downstream latest-KEEP company IDs.

Current Milestone 5 direction:

1. Build a company-level OpenAI calibration dataset first.
2. Review calibrated company-level evidence empirically.
3. Design any deterministic classifier only after that calibration evidence exists.

Recommended next milestone: pilot company evidence-package generation for OpenAI calibration.
