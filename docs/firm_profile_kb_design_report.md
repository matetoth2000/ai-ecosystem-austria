# Firm Profile Knowledge Base v1 Design Report

## Objective

Firm Profile Knowledge Base v1 is designed to identify pages that explain what an organization actually does. It is the foundation layer for content-based page retention: before the pipeline can assess technology, innovation, or ecosystem participation, it needs reliable evidence about the firm’s activities, offerings, customers, markets, and value proposition.

This knowledge base is not intended to classify firms directly. It is a page-scoring resource for retaining the most informative evidence pages from acquired company websites.

## Selected Ontology

The ontology uses ten concise top-level nodes:

- `company_description`
- `business_activity_statements`
- `products`
- `services`
- `solutions_platforms`
- `industries_markets`
- `customers_references`
- `case_studies_use_cases`
- `competencies_capabilities`
- `value_proposition`

This structure follows the deep research report’s recommended separation between identity, activity, offerings, market orientation, proof signals, and value framing. The final YAML flattens the report’s hierarchical structure into directly scorable nodes, consistent with the previous Technology, Innovation, and Ecosystem knowledge bases.

## Node Rationale

`company_description` captures the organization-level page where a firm explains who it is and summarizes its business. These pages are usually the strongest compact source for identity, sector, and high-level activity evidence.

`business_activity_statements` captures concrete action language such as developing, manufacturing, integrating, consulting, operating, distributing, maintaining, and supporting. This node is critical because activity verbs often reveal the firm’s actual business more clearly than generic nouns like “solution” or “technology”.

`products` captures concrete outputs: devices, machines, modules, software, apps, product portfolios, specifications, model families, and data sheets. Product pages are high-value evidence for what a firm sells or manufactures.

`services` captures commercial delivery activities such as consulting, implementation, integration, maintenance, support, training, managed services, outsourcing, audits, and engineering services. This node is essential for service-heavy firms that may not expose their business through product pages.

`solutions_platforms` captures bundled B2B offering pages that connect products and services to customer problems, workflows, industries, or tasks. Because “solution” and “platform” are ambiguous, this node includes strong negative/contextual terms to avoid overvaluing generic marketing language.

`industries_markets` captures who the firm serves: industries, sectors, verticals, market segments, applications, geographies, and target markets. These pages help contextualize horizontal providers whose business cannot be understood from offerings alone.

`customers_references` captures customer, client, reference, and testimonial pages. These pages provide commercial proof and often reveal the real deployment contexts of products and services.

`case_studies_use_cases` captures high-value proof pages that describe customer challenges, implementations, solutions, results, and reference projects. These pages are often among the best evidence for what the firm actually delivers in practice.

`competencies_capabilities` captures expertise, specializations, competences, capabilities, know-how, and engineering depth. This node was made explicit because the project needs to retain pages that explain what a firm can do, even when the page is not a product, service, or case-study page.

`value_proposition` captures concrete benefit framing such as reducing costs, improving quality, increasing efficiency, ensuring compliance, improving reliability, or shortening time-to-market. It is included as an auxiliary node because value language is useful only when anchored to concrete offerings, activities, or markets.

## Major Design Decisions

The ontology keeps business concepts separate from marketing concepts. The YAML prioritizes pages that contain concrete verbs, objects, customers, industries, applications, or commercial outcomes. Generic claims such as “leading”, “innovative”, “premium”, “trusted”, and “future-ready” are treated as negative or context-required signals rather than core evidence.

BM25 queries are written as prototypes of company website language, not search-engine queries. Each query represents a distinct retrieval intent likely to appear on pages such as About, Products, Services, Solutions, Industries, Customers, References, Capabilities, and Case Studies.

English and German coverage is balanced throughout. The knowledge base includes DACH-relevant forms such as `über uns`, `unternehmen`, `leistungen`, `lösungen`, `branchen`, `referenzen`, `fallstudie`, `anwendungsfall`, and `mehrwert`, while preserving common English terminology used on Austrian B2B websites.

Negative terms are node-specific where possible. Legal, career, news, navigation, and support-only terminology is downweighted for company-description purposes, while ambiguous terms such as `solution`, `platform`, `service`, `support`, and value adjectives are controlled within the nodes where they create the most false positives.

## Departures From The Deep Research Report

The report recommends a hierarchical ontology with nested nodes under activity, offerings, market orientation, proof signals, and value proposition. The YAML uses a flatter structure to match the established knowledge-base format and make independent scoring easier.

The report’s `offerings` branch was split into `products`, `services`, and `solutions_platforms`. This keeps retrieval intents distinct and avoids letting broad solution language dominate concrete product or service evidence.

The report’s `proof_signals` branch was split into `customers_references` and `case_studies_use_cases`. These represent different page types and should be explainable separately during later retention analysis.

`competencies_capabilities` is explicit in v1 even though the report treats competences and capabilities mostly as cross-cutting evidence. This reflects the project requirement to retain pages that describe competencies, capabilities, and specialization as first-class firm-profile evidence.

## Difficult Judgement Calls

The hardest boundary is between firm-profile evidence and technology evidence. Terms such as software, platform, system, engineering, automation, and analytics can describe either a firm’s business or a technical domain. In this knowledge base they are used only when they help describe offerings, capabilities, products, or business activity, not as technology-detection signals.

Another judgement call concerns value proposition language. Benefit claims are useful for understanding commercial positioning, but they are also highly prone to marketing noise. The v1 design keeps `value_proposition` as a separate node with strong negatives, so it can support retention without overpowering concrete activity and offering evidence.

Customer and reference pages can be extremely valuable, but image-only logo walls are weak for text-based evidence. The negative terms explicitly mark references without textual customer context as lower quality.

## Primary Retention Role

Firm Profile KB should be the primary driver of page retention because every later evidence package needs to answer the baseline question: what does this organization do? A page that clearly describes products, services, customers, industries, and use cases is useful even if it does not mention AI, R&D, or partnerships.

Technology KB complements Firm Profile KB by identifying technical capabilities and technical domains. Innovation KB complements it by identifying R&D, prototypes, patents, pilots, funded projects, and technology transfer. Ecosystem KB complements it by identifying relationships that may become graph edges, such as research collaborations, customers, suppliers, clusters, and consortium participation.

The four knowledge bases therefore serve different roles. Firm Profile KB identifies the commercially and organizationally descriptive pages; Technology KB identifies technical substance; Innovation KB identifies development and novelty evidence; Ecosystem KB identifies relationship evidence. Firm Profile KB should anchor retention because it prevents the pipeline from retaining pages that are technically interesting but do not explain the firm.

## Assumptions

The knowledge base assumes that useful firm-profile evidence is usually distributed across several pages per domain rather than concentrated on one homepage. It also assumes that German and English terminology often co-exist on Austrian company websites, so both languages must be represented in each node.

The v1 lists are seed dictionaries, not exhaustive lexicons. They are intended for transparent scoring and manual review before implementation in the content-retention stage.
