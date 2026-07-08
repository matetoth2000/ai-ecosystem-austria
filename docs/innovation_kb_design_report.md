# Innovation Knowledge Base v1 Design Report

## Purpose

Innovation Knowledge Base v1 is designed for page-level content scoring in the Austrian AI ecosystem pipeline. Its role is not to detect AI directly. Its role is to retain pages that contain strong evidence about organized innovation activity, research and development, prototypes, funded projects, intellectual property, research collaboration, commercialization, and scientific visibility.

The knowledge base follows the same YAML structure and BM25 query philosophy as Technology Knowledge Base v1. BM25 queries are written as short prototypes of company-website language rather than search-engine syntax. Each query represents a distinct retrieval intent that may appear independently on pages such as Innovation, Research, Development, Projects, Products, Technology, Engineering, Capabilities, Publications, and Partnerships.

## Selected Ontology

The selected ontology uses nine top-level nodes:

1. `research_and_development`
2. `funded_innovation_projects`
3. `product_and_service_innovation`
4. `process_and_operational_innovation`
5. `prototypes_pilots_and_testbeds`
6. `patents_ip_and_exploitation`
7. `research_collaboration_and_ecosystem_linkages`
8. `technology_transfer_and_commercialisation`
9. `research_outputs_and_scientific_visibility`

This ontology is faithful to the deep research report but intentionally avoids deeper hierarchy. The report presents many subdimensions, but for a scoring knowledge base the top-level nodes need to remain interpretable, explainable, and easy to inspect manually. Each node corresponds to a distinct evidence channel rather than a broad marketing concept.

## Node Rationale

`research_and_development` captures formal internal R&D capability. It is grounded in the Frascati distinction between research, applied research, and experimental development. This node is important because internal R&D teams, labs, research agendas, and technical validation are stronger evidence than generic innovation claims.

`funded_innovation_projects` captures externally validated innovation activity. Named programmes such as Horizon Europe, FFG, COMET, Christian Doppler, Josef Ressel, BRIDGE, aws Proof of Concept, and Spin-off Fellowship are highly discriminative for Austrian ecosystem mapping. This node is separated from general R&D because funded projects often expose partners, work packages, milestones, deliverables, and consortium roles.

`product_and_service_innovation` captures innovation that has reached or is approaching the market. It focuses on new or improved products, services, platforms, modules, functionality, releases, and market launches. It is separate from R&D because a company can show innovation through productization even when it does not publish formal research activity.

`process_and_operational_innovation` captures internal process improvements, production systems, digital twins, predictive maintenance, process automation, and data-driven optimization. It is retained as a separate node even though websites under-report process innovation, because industrial firms may expose ecosystem-relevant innovation through operations rather than product pages.

`prototypes_pilots_and_testbeds` captures the bridge between research and application. Prototype, pilot, demonstrator, testbed, proof-of-concept, TRL, validation, field trial, and living-lab language is among the strongest evidence that a firm is engaged in substantive technology development.

`patents_ip_and_exploitation` captures formal intellectual-property and exploitation evidence. This node is high precision but not high recall. Patents, licensing, IP portfolios, PCT applications, granted patents, claims, inventors, and exploitation plans are strong signals when they refer to specific technologies rather than boilerplate legal text.

`research_collaboration_and_ecosystem_linkages` captures structured relationships with universities, research institutes, competence centres, clusters, hubs, and public-private research organizations. It is essential for ecosystem mapping because ecosystem participation is often visible through consortia, research partnerships, joint projects, and co-authored publications.

`technology_transfer_and_commercialisation` captures the institutional pathway from research to market. It covers technology transfer, knowledge transfer, spin-offs, incubators, accelerators, science parks, proof-of-concept funding, commercialization, and startup creation.

`research_outputs_and_scientific_visibility` captures observable knowledge production: publications, technical reports, conference papers, datasets, open-source repositories, benchmarks, and scientific posters. This node is particularly useful for identifying firms and research-adjacent organizations that disclose technical substance through outputs rather than sales pages.

## Design Decisions

The main design decision was to keep product innovation and process innovation separate. The deep research report emphasizes that websites surface product innovation more readily than process innovation. Keeping them separate allows future scoring to treat process signals with different thresholds without losing them.

Funded projects, prototypes, and collaborations are also separated rather than merged into a generic R&D node. They represent different forms of evidence: external selection, application-readiness, and ecosystem linkage. Keeping them separate makes later score explanations more useful.

The technology-transfer node combines the report's innovation structures, spinouts, commercialization, incubators, accelerators, and technology-transfer content. These are conceptually related as market-translation infrastructure and do not need separate top-level scoring nodes in v1.

The BM25 query families are intentionally bilingual and concept-focused. Each major English retrieval intent has a German counterpart where the report provides German terminology. The queries avoid implementation wording and instead approximate how firms describe themselves: "We participate in Horizon Europe projects", "Wir betreiben Forschung und Entwicklung", or "Our pilot deployment tests the system with customers".

## Departures From the Research Report

The deep research report proposes a hierarchical ontology. Innovation KB v1 flattens that hierarchy into top-level nodes. This is a deliberate implementation simplification for reviewability and page-level scoring. The subordinate concepts are preserved through BM25 query families, keywords, boosts, and negatives.

The report includes innovation management and acquisition of tangible assets in the conceptual discussion. These were not made standalone nodes because they are less directly useful for evidence retention in the AI ecosystem pipeline. Related terms are represented only where they support stronger evidence such as process innovation, funded projects, or technology transfer.

The knowledge base uses the spelling `commercialisation` in the node name to stay readable in a European context. The lexical entries include both English terms from the report and German equivalents where provided.

## Difficult Judgement Calls

The word family around "innovation" is intentionally treated cautiously. It appears in keywords only where tied to concrete nodes, and generic claims such as "innovative", "future-ready", and "excellence" are placed in negative terms for R&D. This reflects the report's warning that generic innovation branding is common and low precision.

Patent and IP terms are also handled cautiously. They are strong evidence when tied to applications, portfolios, licensing, claims, inventors, or exploitation plans, but legal boilerplate and privacy/trademark text can create false positives. For that reason, IP has a focused node and explicit negatives.

Process innovation is included despite expected lower recall. The report states that process innovation is underreported on websites, but for industrial AI ecosystem mapping it can be highly relevant when visible through production systems, predictive maintenance, automation, robotics, or data-driven optimization.

## Assumptions

The knowledge base is intended for first-pass page retention, not final classification. It should increase the chance that pages with concrete innovation evidence survive into later processing stages.

The YAML does not implement scoring weights. Relative importance is represented through separate nodes, boost terms, and query-family granularity. Weighting should be implemented later in the scoring layer.

The lexicon is v1 and should be manually reviewed against Austrian website examples. The report explicitly warns that website content is self-reported, uneven, multilingual, and biased toward larger and more visible firms. Future versions should use false positives and false negatives from validation runs to refine negatives, boosts, and query coverage.
