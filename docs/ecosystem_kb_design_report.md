# Ecosystem Knowledge Base v1 Design Report

## Purpose

Ecosystem Knowledge Base v1 is designed for page-level evidence retention in the Austrian AI ecosystem pipeline. It does not score technologies or innovation activity directly. Its purpose is to identify relationship evidence: who is connected to whom, in what role, through what structure, and for what purpose.

The knowledge base follows the same YAML structure and query-family style as the Technology and Innovation knowledge bases. query families are written as short prototypes of real company-website language rather than search syntax. Each query corresponds to a specific relationship intent that may appear on pages such as Partners, Customers, References, Projects, Consortium, Members, Networks, Research, About, Innovation, and Group.

## Selected Ontology

The selected ontology uses thirteen top-level relationship nodes:

1. `strategic_partnerships`
2. `research_collaboration`
3. `university_research_institute_links`
4. `joint_labs_shared_infrastructure`
5. `funded_project_participation`
6. `associated_partner_roles`
7. `cluster_association_memberships`
8. `innovation_hubs_testbeds_infrastructures`
9. `advisory_boards_working_groups`
10. `technology_provider_relationships`
11. `customer_use_case_relationships`
12. `supplier_oem_integrator_relationships`
13. `corporate_group_relationships`

This is a flat relationship ontology rather than the full hierarchy proposed in the research report. The report's hierarchy is useful conceptually, but the scoring layer needs relation types that are directly interpretable and graph-ready.

## Node Rationale

`strategic_partnerships` captures durable, high-commitment external ties such as strategic partnerships, long-term partners, alliances, framework agreements, memoranda of understanding, and joint go-to-market activity. These relationships often imply central ecosystem positioning.

`research_collaboration` captures externally oriented collaborative knowledge production. It includes joint research, co-development, R&D partnerships, development partners, prototypes, datasets, validation, and publications when tied to another actor.

`university_research_institute_links` separates science-system relationships from generic collaboration. Named universities, research institutes, faculties, labs, and scientific partners are especially important for Austrian AI ecosystem mapping because they indicate access to talent, methods, frontier knowledge, and research infrastructure.

`joint_labs_shared_infrastructure` captures stronger, more durable forms of collaboration such as joint labs, competence centres, shared facilities, living labs, testbeds, pilot sites, and research platforms.

`funded_project_participation` captures formal project and consortium roles. Horizon Europe, FFG, COMET, Christian Doppler, Josef Ressel, CORDIS, coordinator, beneficiary, company partner, and scientific partner language is highly discriminative and naturally graph-ready.

`associated_partner_roles` separates active but unfunded or advisory project roles from funded beneficiary roles. This matters because associated partners, observers, advisory partners, stakeholder-board members, and third parties may be ecosystem-relevant without being funded recipients.

`cluster_association_memberships` captures weaker but useful ecosystem-positioning evidence such as membership in clusters, associations, innovation networks, competence networks, boards, steering groups, and official member directories.

`innovation_hubs_testbeds_infrastructures` captures intermediary and adoption infrastructure relationships, including EDIHs, TEFs, AI Factories, Innovation Hubs, KICs, sandboxes, test-before-invest services, and experimentation facilities.

`advisory_boards_working_groups` captures governance and agenda-setting ties. These relationships are often less commercial than partnerships but can reveal who shapes standards, roadmaps, working groups, task forces, and expert panels.

`technology_provider_relationships` captures upstream stack and complementor ties: certified partners, cloud partners, platform partners, integration partners, solution partners, powered-by pages, APIs, marketplaces, and named provider brands.

`customer_use_case_relationships` captures downstream adoption evidence through customer stories, case studies, references, success stories, pilot deployments, named customers, rollouts, and production use.

`supplier_oem_integrator_relationships` captures operational and value-chain dependencies, including suppliers, OEMs, manufacturing partners, systems integrators, distributors, resellers, and implementation partners.

`corporate_group_relationships` captures internal ownership and group structure. These links are important for entity resolution and graph enrichment, but they should remain separate from external ecosystem participation.

## Design Decisions

The main design decision was to separate relationship types by graph semantics. A research collaboration, a customer deployment, a supplier relationship, a certified provider relationship, a cluster membership, and a subsidiary relationship should become different edge types later. They should not be collapsed into a generic partnership score.

The ontology separates university and research-institute links from broader research collaboration. The research report emphasizes that science-system ties are unusually informative for AI ecosystem mapping, and they often carry stronger implications than generic co-development language.

Funded project participation and associated-partner roles are separate. EU and Austrian programmes distinguish beneficiaries, coordinators, company partners, scientific partners, associated partners, observers, and other roles. Keeping these separate preserves role information for later graph construction.

Corporate group relationships are included but kept separate. They are not external ecosystem ties, but they are valuable for ownership context, deduplication, and understanding corporate groups.

No standalone negative/noise node is included. Instead, social-media, footer, legal, generic ecosystem, and unsupported partner language are represented through negative terms inside the relevant relationship nodes. This keeps the ontology focused on positive relation evidence while preserving precision controls.

## Departures From the Deep Research Report

The report proposes a hierarchy with operational leaves and a `non_ecosystem_noise` branch. Ecosystem KB v1 flattens the hierarchy into directly scoreable relationship nodes and handles noise through node-specific negatives.

The report includes knowledge transfer and valorisation as an ecosystem relation type. In this project, that content is primarily represented in Innovation KB v1 under technology transfer and commercialisation. Ecosystem KB v1 only includes transfer-like language when it is tied to external research collaboration, shared infrastructure, hubs, or formal partnerships.

The report groups market and value-chain relationships under a broader market node. Ecosystem KB v1 splits this into technology provider, customer/use-case, and supplier/OEM/integrator relationships because those imply different graph edge directions and meanings.

## Difficult Judgement Calls

The broad words `partner`, `network`, `cluster`, `community`, `hub`, `ecosystem`, and `platform` are useful but dangerous. They are common on company websites and can mean marketing, technology stacks, social groups, computer networks, or real ecosystem participation. The KB therefore uses these terms as keywords only inside relation-specific nodes and adds explicit negative terms requiring named organizations, formal role labels, or contextual evidence.

Customer references were included despite not always being "ecosystem" relationships in a narrow research-policy sense. They are valuable for AI ecosystem mapping because they reveal real-world deployment, adoption, sector penetration, and downstream use cases.

Technology-provider relationships were also included because AI firms often depend on cloud, model, data, GPU, API, MLOps, and platform providers. These are not research collaborations, but they are important ecosystem edges in the AI value chain.

Supplier and OEM relationships were retained because industrial AI, robotics, sensing, embedded systems, and manufacturing ecosystems often expose relationships through supplier, OEM, integrator, reseller, and channel pages.

## Support for Later Graph Construction

This knowledge base is intended to support graph construction by preserving typed relationship evidence at page level. Each node suggests a likely graph edge type:

- `strategic_partnerships` -> `strategic_partner`
- `research_collaboration` -> `research_collaborator`
- `university_research_institute_links` -> `science_partner`
- `joint_labs_shared_infrastructure` -> `shared_infrastructure_partner`
- `funded_project_participation` -> `project_participant`
- `associated_partner_roles` -> `associated_project_partner`
- `cluster_association_memberships` -> `member_of`
- `innovation_hubs_testbeds_infrastructures` -> `hub_or_infrastructure_participant`
- `advisory_boards_working_groups` -> `governance_or_working_group_role`
- `technology_provider_relationships` -> `technology_provider_or_integration_partner`
- `customer_use_case_relationships` -> `customer_or_deployment_reference`
- `supplier_oem_integrator_relationships` -> `supplier_oem_or_integrator`
- `corporate_group_relationships` -> `ownership_or_group_affiliation`

The later graph layer should not emit edges from keyword hits alone. Strong graph candidates should require a typed relation phrase, a named external actor, and preferably a high-value page zone such as title, heading, URL slug, anchor text, or outbound link. Corporate-group edges should be emitted separately from external ecosystem edges.

The most graph-ready signals are funded project roles, named university or institute collaborations, joint labs and shared infrastructures, cluster and hub memberships, named customer deployments, certified technology-provider relationships, and explicit group ownership statements. Generic logo walls, footer links, privacy/vendor lists, and unsupported ecosystem language should remain weak or negative evidence.

## Assumptions

The KB is designed for first-pass page retention and evidence preservation, not final entity linking. It should increase the likelihood that relationship-rich pages survive into later Markdown processing and LLM extraction.

The YAML does not encode graph-edge confidence rules. It only preserves relation-specific query, keyword, boost, and negative evidence needed by a future scorer.

The ontology and lexicon are v1 and should be manually reviewed on Austrian company websites. Future versions should use validation false positives and false negatives to refine relationship terms, named programme markers, and noise controls.
