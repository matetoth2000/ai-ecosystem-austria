# Decisions

## Overview

## Decisions

## Open Questions

## Notes

## 2026-05-06 — Use BvD ID as firm primary key and normalized domain as scraping unit

### Context

The ORBIS/Aurelia file contains firm-level records with BvD IDs and website addresses. Multiple firms may share the same domain.

### Decision

Use BvD ID Nummer as the primary firm identifier and use normalized domains as the initial domain-level unit for URL discovery and scraping.

### Reasoning

- BvD ID uniquely identifies firm records.
- Domain-level deduplication avoids scraping the same website repeatedly.
- Firm-level processing can still be performed later using mappings from firms to pages/domains.

### Implications

- The pipeline separates firm-level records from domain-level scraping.
- Multiple firms can point to the same domain.
- Later LLM inputs may need firm-specific context when firms share domains.
