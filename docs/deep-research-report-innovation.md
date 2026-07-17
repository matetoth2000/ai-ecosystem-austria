# Innovation and R&D Ontology for Multilingual Website Scoring

## Conceptual frame for the dimension

A useful website-based **INNOVATION / R&D** dimension should be anchored in the statistical concepts used by the OECD, Eurostat, and the European Commission, not in generic corporate branding. In the **Oslo Manual**, innovation is the implementation of a **new or improved product or process** that differs significantly from prior offerings or processes and has either been introduced on the market or brought into use. The same manual defines **innovation activities** broadly, as developmental, financial, and commercial activities intended to result in innovation, and it highlights eight relevant activity types, including R&D, engineering/design, IP-related activities, software/database work, employee training, acquisition of tangible assets, and innovation management. In the **Frascati Manual**, R&D is narrower: it is creative and systematic work to increase the stock of knowledge and devise new applications, subdivided into **basic research, applied research, and experimental development**. Experimental development explicitly includes prototypes, demonstrators, pilot projects, and testing in representative environments, but excludes routine or periodic changes. citeturn6view0turn6view1turn24view3turn6view3turn7view0turn7view1turn7view2turn19view2

That distinction matters for scalable website scoring. A company page saying “we are innovative” is not equivalent to evidence of R&D, funded research, public-private research collaboration, prototype building, or patenting. The **Community Innovation Survey** reinforces this by separately tracking innovation activities, product innovations, business process innovations, innovation cooperation, IPR use, public funding, innovation expenditure, and turnover from innovative products. Eurostat’s newer **innovation profiles** also distinguish firms with **in-house developed new-to-market product innovation**, firms with **in-house process innovation**, firms with **ongoing or abandoned innovation activities**, and firms that merely **introduced innovations acquired from elsewhere**. That is exactly the distinction a web scorer should emulate: strong evidence should favour **in-house, technically substantive, project-based, or externally validated innovation**, while weak evidence should downweight **generic adoption, reselling, or marketing rhetoric**. citeturn31view0turn31view1turn31view2turn32view0

The **European Innovation Scoreboard** adds another useful lens. Within business innovation, it separates **innovators**, **linkages**, and **intellectual assets**. That is highly compatible with website scoring: one cluster of signals should capture actual product/process innovation, another should capture collaboration and ecosystem embeddedness, and a third should capture patents, trademarks, designs, licensing, and exploitation. The EIS also treats **public-private co-publications** and SMEs collaborating with others as core innovation indicators, which supports a dedicated collaboration subdimension rather than burying it inside generic R&D. citeturn10view0turn10view1turn10view3

The web-mining literature strongly supports this evidence-first design. Studies using firm websites show that website content can predict firm-level innovation activity, but that **textual content** is more informative than simple metadata, and that websites are better at surfacing **product innovation** and **innovation expenditure** than **process innovation**, partly because process innovation is less visible to website visitors. Keyword-based approaches can work well when the target concept is adequately described by keywords, but the literature also warns that websites are self-reported, multilingual, structurally heterogeneous, and biased toward larger and more patent-active firms. This means the scoring system should prefer **many narrow query families** with explainable boosts and negatives over a single giant “innovation” query. citeturn38view0turn38view1turn38view2turn38view3turn13view0turn37view0turn37view1turn37view2turn37view3turn14view0

## Recommended ontology

The highest-utility ontology for large-scale company website mining is a **hierarchical evidence ontology** rather than a flat list of terms.

```text
INNOVATION_R&D
├── research_and_development
│   ├── basic_applied_experimental_research
│   ├── r_and_d_projects_programmes
│   └── r_and_d_capabilities_and_teams
├── funded_innovation_projects
│   ├── eu_programmes
│   ├── austrian_programmes
│   └── consortium_and_project_administration
├── product_and_service_innovation
├── process_and_operational_innovation
├── prototypes_pilots_demonstrators_testbeds
├── patents_ip_and_exploitation
│   ├── patents_trademarks_designs
│   ├── licensing_and_ip_portfolio
│   └── standards_and_regulatory_validation
├── research_collaboration_and_ecosystem_linkages
│   ├── university_and_research_links
│   ├── competence_centres_and_labs
│   └── clusters_networks_hubs
├── innovation_structures_and_technology_transfer
│   ├── technology_transfer
│   ├── spinouts_and_commercialisation
│   └── incubators_accelerators_innovation_labs
└── research_outputs_and_scientific_visibility
    ├── publications
    ├── datasets_software_open_source
    └── conference_and_benchmark_visibility
```

This structure maps well to the manuals and indicators above. It keeps **formal R&D**, **innovation outputs**, **external validation**, **collaboration**, and **commercialisation / transfer** analytically separate, which improves both explainability and robustness. For an Austrian AI-ecosystem mapping project, that separation is especially useful because AI-relevant firms often show up through different website patterns: some expose **R&D teams and publications**, others **funded projects and pilots**, others **COMET/CDL/BRIDGE ties**, and others **product launches plus IP or technology-transfer evidence**. citeturn6view3turn31view0turn32view0turn10view0turn34view1turn34view2turn22search2turn22search8turn40view0turn40view1

A practical scoring rule is to treat the ontology as a **stack of evidence tiers**. The strongest pages are those that hit **multiple nodes simultaneously** on the same page or closely linked pages—for example, a company page that mentions a **Horizon Europe project**, a **prototype demonstrator**, a **university partner**, and a **patent filing**. Single-node hits still matter, but isolated language such as “innovative solutions” should be weak. This follows both the manuals’ distinction between activities and outcomes and the web-mining literature’s caution that websites contain both signal and self-promotion. citeturn24view3turn31view2turn14view0turn29view0

## Node design for query-family and keyword-family scoring

The lexical packets below are **design recommendations synthesized from the cited manuals, CIS/EIS terminology, Horizon Europe, FFG, COMET, CDG, WIPO, and web-mining studies**. They are intended for **query-family and keyword-family scoring**, not for legal definitions or LLM extraction. citeturn31view0turn33view0turn34view0turn34view1turn34view2turn22search0turn22search2turn40view0turn40view1

### Research and development

This node should capture **formal, internally organized R&D capability**, because it is the clearest direct evidence that a company is doing more than market-facing innovation storytelling. It is also one of the strongest bridges to AI ecosystem participation: firms with explicit R&D units, applied research agendas, experimental development work, or research roadmaps are more likely to collaborate with universities, join competence centres, participate in funded consortia, and contribute to the local AI knowledge base. Useful trigger activities include maintaining an R&D department, running applied research projects, hiring research scientists, operating a machine-learning lab, or describing experimental development of new digital systems. citeturn6view3turn7view0turn7view1turn7view2turn19view2turn38view0

| Element | English | German |
|---|---|---|
| keywords | research, development, R&D, applied research, industrial research, experimental development, research team, research scientist, engineering, feasibility, proof of concept | Forschung, Entwicklung, F&E, Forschung und Entwicklung, angewandte Forschung, industrielle Forschung, experimentelle Entwicklung, Forschungsteam, Forschungsgruppe, Machbarkeit, Machbarkeitsstudie |
| multi-word phrases | “research and development”, “applied research”, “experimental development”, “technology development”, “research programme”, “research agenda”, “R&D department”, “R&D team”, “in-house development”, “proof of concept” | „Forschung und Entwicklung“, „angewandte Forschung“, „experimentelle Entwicklung“, „Technologieentwicklung“, „Forschungsprogramm“, „Forschungsagenda“, „F&E-Abteilung“, „F&E-Team“, „interne Entwicklung“, „Machbarkeitsstudie“ |
| boost terms | lab, laboratory, ML team, AI team, scientists, engineers, roadmap, state of the art, novel method, technical validation, in-house, proprietary | Labor, Laboratorium, KI-Team, Forschungslabor, Wissenschaftler:innen, Ingenieur:innen, Roadmap, Stand der Technik, neuartige Methode, technische Validierung, intern, proprietär |
| negative / downweight | innovative, innovation, solution, future-ready, excellence, consulting, digital transformation, business development, property development, website development | innovativ, Innovation, zukunftssicher, Exzellenz, Beratung, digitale Transformation, Geschäftsentwicklung, Projektentwicklung, Webentwicklung |

### Funded innovation projects

This node should capture pages showing that innovation activity is **externally selected, financed, and administratively organized**. That is one of the strongest signals on the public web because funded projects usually require explicit deliverables, partners, milestones, work packages, exploitation planning, and some formal description of novelty. For Austrian ecosystem mapping, this node is especially important because programme names such as **Horizon Europe, FFG, COMET, BRIDGE, Christian Doppler Lab, Josef Ressel Zentrum, aws Proof of Concept, aws Preseed, and Spin-off Fellowship** are highly discriminative and explainable. Trigger activities include EU project participation, being a consortium partner, public grant announcements, posting project pages with grant IDs, and describing work packages or milestones. citeturn33view0turn26view1turn28view0turn34view0turn34view1turn34view2turn23search3turn23search4turn19view3turn19view4turn22search2turn22search11

| Element | English | German |
|---|---|---|
| keywords | project, grant, funded, consortium, partner, call, proposal, work package, deliverable, milestone, exploitation, dissemination | Projekt, Förderung, gefördert, Konsortium, Partner, Ausschreibung, Antrag, Arbeitspaket, Deliverable, Meilenstein, Verwertung, Dissemination |
| multi-word phrases | “Horizon Europe”, “Horizon 2020”, “Research and Innovation Action”, “Innovation Action”, “Coordination and Support Action”, “grant agreement”, “work package”, “public deliverable”, “project partner”, “consortium member”, “funded by the European Union”, “co-funded”, “FFG project”, “cooperative R&D project”, “COMET centre”, “Christian Doppler Laboratory”, “Josef Ressel Centre”, “aws Proof of Concept”, “Spin-off Fellowship”, “BRIDGE project” | „Horizon Europe“, „Forschungs- und Innovationsmaßnahme“, „Innovationsmaßnahme“, „Koordinierungs- und Unterstützungsmaßnahme“, „Grant Agreement“, „Arbeitspaket“, „öffentliches Deliverable“, „Projektpartner“, „Konsortialpartner“, „von der Europäischen Union gefördert“, „kofinanziert“, „FFG-Projekt“, „kooperatives F&E-Projekt“, „COMET-Zentrum“, „Christian-Doppler-Labor“, „Josef-Ressel-Zentrum“, „aws Proof of Concept“, „Spin-off Fellowship“, „BRIDGE-Projekt“ |
| boost terms | CORDIS, project ID, grant agreement number, consortium, deliverable, milestone, TRL, demonstrator, pilot, partner universities, funded by FFG, funded by aws | CORDIS, Projekt-ID, Grant Agreement, Konsortium, Deliverable, Meilenstein, TRL, Demonstrator, Pilot, Partneruniversitäten, gefördert durch FFG, gefördert durch aws |
| negative / downweight | client project, customer project, reference project, CSR project, internal project only, project management services | Kundenprojekt, Referenzprojekt, CSR-Projekt, internes Projekt, Projektmanagement-Dienstleistung |

### Product and service innovation

This node should capture **implemented innovation on the market**, especially when the company describes technical novelty, new functionality, or market introduction. It is strongly related to AI ecosystem participation because AI firms often present themselves through their most concrete outcomes: new software products, ML-enabled services, new platform capabilities, intelligent devices, or domain-specific AI applications. The strongest triggers are pages describing a **new or improved** product or service with technical detail, especially where the product is linked to development, validation, pilots, patents, or funded projects. Eurostat’s innovation profiles make **in-house developed new-to-market product innovation** the most “profound” innovator profile, so this node deserves high weight. citeturn6view0turn31view2turn32view0turn38view1

| Element | English | German |
|---|---|---|
| keywords | new product, new service, product innovation, launch, release, developed, platform, software, system, module, feature | neues Produkt, neue Dienstleistung, Produktinnovation, Markteinführung, Release, entwickelt, Plattform, Software, System, Modul, Funktion |
| multi-word phrases | “new product”, “new service”, “product innovation”, “new-to-market”, “developed in-house”, “market launch”, “introduced on the market”, “new generation platform”, “AI-powered product”, “new functionality” | „neues Produkt“, „neue Dienstleistung“, „Produktinnovation“, „neu am Markt“, „intern entwickelt“, „Markteinführung“, „am Markt eingeführt“, „neue Plattformgeneration“, „KI-gestütztes Produkt“, „neue Funktionalität“ |
| boost terms | patented, prototype, pilot customer, validated, benchmarked, deployment, state of the art, first-of-its-kind, scalable | patentiert, Prototyp, Pilotkunde, validiert, benchmarked, Einsatz, Stand der Technik, neuartig, skalierbar |
| negative / downweight | portfolio expansion, product range, webshop, reseller, distribution, catalogue, cosmetic redesign, rebranding | Sortimentserweiterung, Produktpalette, Webshop, Händler, Vertrieb, Katalog, kosmetische Überarbeitung, Rebranding |

### Process and operational innovation

This node is important, but it should usually score **below product/service innovation** on websites, because the literature shows that process innovation is less often explicitly announced online. Still, it is a valuable signal for firms active in AI, automation, analytics, and digital operations, especially when the website mentions internal production methods, workflow automation, MLOps, quality systems, digital twins, predictive maintenance, robotics, or new business-process architectures. Trigger activities include new production methods, autonomous operations, data-driven optimization, digital process redesign, or internal AI-supported operational improvement. citeturn6view1turn31view2turn38view1

| Element | English | German |
|---|---|---|
| keywords | process innovation, manufacturing process, workflow, automation, optimisation, digital twin, predictive maintenance, MLOps, production system, quality control | Prozessinnovation, Fertigungsprozess, Arbeitsablauf, Automatisierung, Optimierung, digitaler Zwilling, Predictive Maintenance, MLOps, Produktionssystem, Qualitätskontrolle |
| multi-word phrases | “business process innovation”, “new production process”, “new manufacturing process”, “process automation”, “data-driven optimisation”, “AI-supported process”, “digital process redesign”, “smart factory”, “production efficiency” | „Geschäftsprozessinnovation“, „neuer Produktionsprozess“, „neues Fertigungsverfahren“, „Prozessautomatisierung“, „datengetriebene Optimierung“, „KI-gestützter Prozess“, „digitale Prozessneugestaltung“, „Smart Factory“, „Produktionseffizienz“ |
| boost terms | validated in operation, implemented internally, production line, process control, industrial AI, robotics, sensor fusion, inspection system | im Betrieb validiert, intern implementiert, Produktionslinie, Prozessregelung, industrielle KI, Robotik, Sensorfusion, Inspektionssystem |
| negative / downweight | process consulting, business process outsourcing, implementation services, workflow software reseller, ISO page without novelty | Prozessberatung, BPO, Implementierungsservices, Workflow-Software-Händler, ISO-Seite ohne Neuheitsbezug |

### Prototypes, pilots, demonstrators, and testbeds

This should be one of the strongest website nodes because it captures the **bridge between research and application**. In Horizon Europe terminology, **Innovation Actions** explicitly include prototyping, testing, demonstrating, piloting, and market replication. FFG experimental-development guidance likewise treats prototypes, demonstrators, pilot projects, and testing in real-life or representative environments as central characteristics, and FFG’s TRL material places prototype demonstration around **TRL 7**. For AI ecosystem mapping, this node is particularly discriminative because serious AI actors often describe **testbeds, pilot deployments, proof-of-concept systems, field trials, validation sites, sandboxes, or demonstrators** before commercialization. citeturn33view0turn19view2turn26view0turn28view0turn28view1

| Element | English | German |
|---|---|---|
| keywords | prototype, pilot, demonstrator, testbed, proof of concept, validation, field trial, sandbox, pilot line, trial deployment | Prototyp, Pilot, Demonstrator, Testbed, Proof of Concept, Validierung, Feldversuch, Sandbox, Pilotlinie, Testeinsatz |
| multi-word phrases | “system prototype”, “pilot project”, “pilot deployment”, “proof of concept”, “technology validated”, “technology demonstrated”, “test environment”, “operational environment”, “field validation”, “large-scale demonstrator” | „Systemprototyp“, „Pilotprojekt“, „Piloteinsatz“, „Proof of Concept“, „Technologie validiert“, „Technologie demonstriert“, „Testumgebung“, „Einsatzumgebung“, „Feldvalidierung“, „Großdemonstrator“ |
| boost terms | TRL, prototype demonstrated, relevant environment, operational environment, beta deployment, test site, validation partner, living lab | TRL, Prototyp demonstriert, relevante Umgebung, Einsatzumgebung, Beta-Einsatz, Teststandort, Validierungspartner, Living Lab |
| negative / downweight | pilot customer logos without technical context, UI mockup, concept only, demo video only, prototype as marketing metaphor | Pilotkundenlogos ohne Technikbezug, UI-Mockup, nur Konzept, nur Demo-Video, Prototyp als Werbemetapher |

### Patents, IP, and exploitation

This node should capture **formal appropriation and exploitation mechanisms**. The manuals and WIPO guidance make clear that IP-related activity is part of the innovation landscape, but also that patents capture only a subset of innovation and miss many firms that innovate without patentable inventions. For websites, the right design approach is therefore: treat patents and licensing as **high-precision but not high-recall** signals. Useful activities include patent applications, portfolio pages, licensing offers, IP commercialization, design rights, trademarks tied to new technology, and statements about exploitation of project results. AI ecosystem relevance is high because firms with patents or licensing strategies are often deeply embedded in applied research, transfer, venture building, or university-industry collaboration. citeturn24view0turn24view1turn10view1turn40view0turn40view2

| Element | English | German |
|---|---|---|
| keywords | patent, patented, patent application, IP, intellectual property, licensing, licence, trademark, design right, trade secret, exploitation | Patent, patentiert, Patentanmeldung, IP, geistiges Eigentum, Lizenzierung, Lizenz, Marke, Geschmacksmuster, Betriebsgeheimnis, Verwertung |
| multi-word phrases | “patent application”, “patent pending”, “IP portfolio”, “licensing agreement”, “technology licensing”, “commercial exploitation”, “freedom to operate”, “intellectual property rights”, “PCT application” | „Patentanmeldung“, „zum Patent angemeldet“, „IP-Portfolio“, „Lizenzvereinbarung“, „Technologielizenzierung“, „wirtschaftliche Verwertung“, „Freedom to Operate“, „Schutzrechte“, „PCT-Anmeldung“ |
| boost terms | patent family, granted patent, claims, inventor, assignee, exclusive licence, technology transfer office, exploitation plan | Patentfamilie, erteiltes Patent, Ansprüche, Erfinder:in, Anmelder:in, exklusive Lizenz, Technologietransferstelle, Verwertungsplan |
| negative / downweight | privacy and trademark boilerplate, law firm content, patent leather, brand-protection page only | Datenschutz- und Marken-Standardtexte, Kanzleiinhalt, Lackleder, reine Markenschutzseite |

### Research collaboration and ecosystem linkages

This node should capture **structured external innovation relationships**: universities, research institutes, competence centres, public-private co-publications, consortium partnerships, clusters, digital innovation hubs, and other research-facing network ties. This is one of the best signals for participation in an AI ecosystem because ecosystems are fundamentally about **linkages**, not only internal capability. The EIS treats collaboration and public-private research links as distinct innovation indicators; OECD work on university-industry collaboration highlights channels such as collaborative research, co-patenting, and spin-offs; and recent web-mining work shows websites can reveal many more science-industry linkages than patent-based methods alone. Trigger activities include named university collaborations, research consortia, joint labs, cluster memberships with substantive R&D language, and public-private publications or projects. citeturn10view0turn10view3turn39view0turn16view0turn16view1turn16view2turn34view1turn36search8

| Element | English | German |
|---|---|---|
| keywords | collaboration, partner, consortium, university, institute, research institute, cluster, hub, ecosystem, co-publication, joint project | Kooperation, Partner, Konsortium, Universität, Institut, Forschungseinrichtung, Cluster, Hub, Ökosystem, Ko-Publikation, Gemeinschaftsprojekt |
| multi-word phrases | “research collaboration”, “joint research”, “public-private collaboration”, “university partnership”, “research partner”, “consortium partner”, “competence centre”, “innovation ecosystem”, “digital innovation hub”, “cluster initiative” | „Forschungskooperation“, „gemeinsame Forschung“, „öffentlich-private Zusammenarbeit“, „Universitätspartnerschaft“, „Forschungspartner“, „Konsortialpartner“, „Kompetenzzentrum“, „Innovationsökosystem“, „Digital Innovation Hub“, „Clusterinitiative“ |
| boost terms | COMET, Christian Doppler Laboratory, Josef Ressel Centre, university names, AIT, TU Wien, FH, research centre, co-authored publication | COMET, Christian-Doppler-Labor, Josef-Ressel-Zentrum, Universitätsnamen, AIT, TU Wien, FH, Forschungszentrum, gemeinsame Publikation |
| negative / downweight | sales partner, channel partner, implementation partner, alliance, network, ecosystem used only in marketing sense | Vertriebspartner, Channel-Partner, Implementierungspartner, Allianz, Netzwerk, Ökosystem nur im Marketing-Sinn |

### Innovation structures and technology transfer

This node should cover the **institutionalized infrastructure around innovation**: innovation labs, maker spaces, TTOs, incubators, accelerators, spin-out programmes, proof-of-concept offices, industry liaison offices, science parks, and commercialization pathways. WIPO defines knowledge and technology transfer as the collaborative flow of knowledge and IP from creators to public and private users, and identifies TTOs, science parks, incubators, and related organizations as core transfer structures. Austrian programmes such as **aws Proof of Concept**, **aws Preseed**, **FFG Spin-off Fellowship**, and **BRIDGE** add highly relevant local terms for early-stage transfer and commercialization. This node is highly valuable for AI ecosystem mapping because many AI actors sit at the boundary between research and market deployment. citeturn40view0turn40view1turn40view2turn19view4turn19view3turn23search4turn34view2turn35search3turn35search9

| Element | English | German |
|---|---|---|
| keywords | technology transfer, knowledge transfer, commercialization, spin-off, incubator, accelerator, innovation lab, maker space, science park, TTO | Technologietransfer, Wissenstransfer, Kommerzialisierung, Spin-off, Inkubator, Accelerator, Innovationslabor, Makerspace, Wissenschaftspark, TTO |
| multi-word phrases | “technology transfer office”, “knowledge and technology transfer”, “from lab to market”, “spin-off programme”, “proof of concept funding”, “innovation lab”, “science and technology park”, “incubation programme”, “startup accelerator” | „Technologietransferstelle“, „Wissens- und Technologietransfer“, „vom Labor zum Markt“, „Spin-off-Programm“, „Proof-of-Concept-Förderung“, „Innovationslabor“, „Wissenschafts- und Technologiepark“, „Inkubationsprogramm“, „Startup-Accelerator“ |
| boost terms | aws Proof of Concept, aws Preseed, Spin-off Fellowship, licensing, TTO, science park, incubator, startup creation, exploitation | aws Proof of Concept, aws Preseed, Spin-off Fellowship, Lizenzierung, TTO, Science Park, Inkubator, Unternehmensgründung, Verwertung |
| negative / downweight | innovation workshop, ideation event, creative lab, accelerator only as customer programme, incubator literal non-business meaning | Innovationsworkshop, Ideation-Event, Kreativlabor, Accelerator nur als Kundenprogramm, Inkubator im nichtunternehmerischen Sinn |

### Research outputs and scientific visibility

This node should capture outputs that indicate **knowledge production**, not just commercial promotion. It includes publications, conference papers, white papers with strong technical substance, datasets, open-source repositories, benchmarks, challenge participation, and co-authored work with research partners. It is especially useful for AI because serious AI actors often disclose methodological or applied-research capability through publications, model evaluations, open-source releases, technical reports, or conference participation. The EIS uses public-private co-publications as an innovation linkage indicator, and the OECD stresses that websites can reveal these knowledge-producing activities. Trigger activities include publication lists, “our research” pages, arXiv or conference links, benchmark results, and named datasets or software releases. citeturn10view3turn39view0turn29view0

| Element | English | German |
|---|---|---|
| keywords | publication, paper, journal, conference, benchmark, dataset, repository, open source, technical report, white paper | Publikation, Paper, Fachartikel, Konferenz, Benchmark, Datensatz, Repository, Open Source, technischer Bericht, Whitepaper |
| multi-word phrases | “peer-reviewed publication”, “conference paper”, “research publication”, “technical report”, “open-source software”, “benchmark result”, “public dataset”, “co-authored paper”, “scientific poster” | „peer-reviewte Publikation“, „Konferenzbeitrag“, „Forschungspublikation“, „technischer Bericht“, „Open-Source-Software“, „Benchmark-Ergebnis“, „öffentlicher Datensatz“, „gemeinsame Publikation“, „wissenschaftliches Poster“ |
| boost terms | arXiv, IEEE, ACM, NeurIPS, ICML, CVPR, GitHub, DOI, benchmark, challenge winner, co-authored | arXiv, IEEE, ACM, NeurIPS, ICML, CVPR, GitHub, DOI, Benchmark, Challenge-Sieger, koautoriert |
| negative / downweight | news article, blog post, PR white paper, generic insight article, webinar recap | Newsartikel, Blogbeitrag, PR-Whitepaper, allgemeiner Insights-Artikel, Webinar-Rückblick |

## False positives, ambiguity, and caution handling

The most important design mistake would be to let the scorer collapse everything into the word family **innovat***. The website literature shows that even the explicit word **“innovative”** is less informative than broader textual patterns, website size, and multilingual behavior, while the manuals require evidence of novelty, implementation, or organized innovation activity. So “innovative”, “innovation leader”, “future-proof”, “cutting-edge”, and “state-of-the-art” should be treated as **weak support terms only**, unless they co-occur with a stronger evidence node such as funding, patents, prototypes, collaboration, publications, or formal R&D. citeturn38view1turn38view2turn6view0turn6view1

A second major source of noise is **ambiguous professional vocabulary**. Terms such as **development**, **research**, **lab**, **pilot**, **project**, **platform**, **test**, and **validation** are common on company websites but often refer to ordinary delivery work, consulting, marketing, or customer implementations rather than innovation activity. Frascati and FFG are explicit that routine changes, pre-production work without novelty, and ordinary operational modifications should not be treated as R&D evidence. citeturn7view2turn19view2

I recommend the following handling rules:

| Category | Terms / patterns | Recommended treatment | Why |
|---|---|---|---|
| generic branding | innovative, innovation, excellence, leading-edge, future-ready, next generation | downweight heavily unless near strong evidence | branding is common and low precision citeturn38view1turn14view0 |
| ambiguous development | development, developed, developer, development partner | require nearby technical or research context | “business development”, “property development”, “web development” are frequent false positives citeturn7view2turn19view2 |
| ambiguous research | research, studies, analysis, investigation | require nearby R&D, technical, scientific, product, process, lab, or partner terms | “market research” and “customer research” are common non-R&D uses citeturn6view3turn14view0 |
| ambiguous lab | lab, laboratory | boost only if near engineering, AI, materials, testing, prototype, university, COMET, CD terms | “innovation lab” can be workshop branding rather than research structure citeturn35search3turn22search2 |
| ambiguous pilot | pilot, pilot phase, pilot customer | require nearby demonstrator, validation, prototype, deployment, trial, TRL | “pilot customer” alone is sales language, not necessarily R&D citeturn33view0turn19view2 |
| process noise | implementation, rollout, migration, customization, integration, maintenance, support | downweight unless paired with novelty / new method / validation signals | websites often expose service delivery more than innovation activity citeturn38view1turn14view0 |
| IP boilerplate | trademark, copyright, patent notice, legal notice | ignore unless paired with patent application, licensing, inventors, portfolio, or claims | legal footer text is common and non-diagnostic citeturn40view2 |
| ecosystem fluff | ecosystem, network, hub, accelerator | require institution names, programme names, or collaboration verbs | these terms are often used metaphorically in marketing citeturn36search8turn40view1 |

A very effective additional rule is to use **proximity constraints**. For example, score “prototype” much higher when it appears within a small window of terms like **validated, tested, TRL, pilot, operational environment, field trial, demonstrator**, and score “patent” much higher when it appears near **application, granted, inventor, licensing, PCT, portfolio**, not in privacy or footer boilerplate. This keeps the system explainable while materially improving precision. citeturn26view0turn28view0turn40view2

## query families and weighting strategy

The literature suggests that **simple keyword search can be sufficient** when the topic is adequately described by terms and phrases, but also that more precise filtering is needed to separate different actor types and reduce self-promotional noise. For this use case, the best design is not one monolithic innovation query. It is a **family of narrow query families**, each corresponding to one ontology node, with later score fusion. citeturn37view0turn13view0

A strong production setup would use the following families:

### Core R&D family

Use this family to find **formal research capability**.

```text
(
  "research and development"^10 OR "R&D"^10 OR
  "applied research"^8 OR "industrial research"^8 OR
  "experimental development"^9 OR
  "Forschung und Entwicklung"^10 OR "F&E"^10 OR
  "angewandte Forschung"^8 OR "industrielle Forschung"^8 OR
  "experimentelle Entwicklung"^9
)
AND
(
  lab^3 OR laboratory^3 OR scientists^3 OR engineers^2 OR
  Labor^3 OR Wissenschaftler^3 OR Ingenieur^2
)
NOT
(
  "business development" OR "property development" OR
  "website development" OR "project development" OR
  "Geschäftsentwicklung" OR "Projektentwicklung" OR "Webentwicklung"
)
```

### Funded project family

Use this family to identify **externally validated innovation projects**.

```text
(
  "Horizon Europe"^10 OR "Horizon 2020"^9 OR "Research and Innovation Action"^9 OR
  "Innovation Action"^9 OR "grant agreement"^8 OR consortium^6 OR deliverable^6 OR milestone^6 OR
  "FFG"^10 OR "kooperatives F&E-Projekt"^9 OR "COMET"^10 OR "Christian Doppler"^10 OR
  "Josef Ressel"^9 OR "BRIDGE"^9 OR "Spin-off Fellowship"^8 OR
  "aws Proof of Concept"^9 OR "aws Preseed"^8
)
NOT
(
  "customer project" OR "reference project" OR "Kundenprojekt" OR "Referenzprojekt"
)
```

### Innovation output family

Use this family for **new-to-market or in-house-developed offerings**.

```text
(
  "product innovation"^8 OR "new product"^7 OR "new service"^7 OR
  "new-to-market"^8 OR "introduced on the market"^8 OR
  "Produktinnovation"^8 OR "neues Produkt"^7 OR "neue Dienstleistung"^7 OR
  "am Markt eingeführt"^8 OR "neu am Markt"^8
)
AND
(
  developed^3 OR "in-house"^4 OR platform^2 OR software^2 OR system^2 OR
  entwickelt^3 OR intern^4 OR Plattform^2 OR Software^2 OR System^2
)
```

### Process innovation family

Use this family, but weight it slightly lower because websites under-report it.

```text
(
  "business process innovation"^8 OR "process innovation"^7 OR
  "process automation"^6 OR "predictive maintenance"^6 OR "digital twin"^6 OR
  "Geschäftsprozessinnovation"^8 OR "Prozessinnovation"^7 OR
  "Prozessautomatisierung"^6 OR "vorausschauende Wartung"^6 OR "digitaler Zwilling"^6
)
AND
(
  production^2 OR workflow^2 OR manufacturing^2 OR operations^2 OR
  Produktion^2 OR Arbeitsablauf^2 OR Fertigung^2 OR Betrieb^2
)
```

### Prototype and validation family

This should be one of the most heavily weighted evidence families.

```text
(
  prototype^8 OR demonstrator^8 OR pilot^7 OR testbed^7 OR
  "proof of concept"^8 OR validation^6 OR "field trial"^6 OR
  Prototyp^8 OR Demonstrator^8 OR Pilot^7 OR Testbed^7 OR
  "Proof of Concept"^8 OR Validierung^6 OR Feldversuch^6
)
AND
(
  TRL^6 OR validated^4 OR demonstrated^4 OR deployment^3 OR
  "operational environment"^5 OR "relevant environment"^5 OR
  validiert^4 OR demonstriert^4 OR Einsatz^3 OR Einsatzumgebung^5
)
```

### IP and exploitation family

```text
(
  patent^8 OR patented^7 OR "patent application"^8 OR licensing^7 OR
  "intellectual property"^7 OR "IP portfolio"^7 OR "PCT"^7 OR
  Patent^8 OR patentiert^7 OR Patentanmeldung^8 OR Lizenzierung^7 OR
  "geistiges Eigentum"^7 OR Schutzrechte^7 OR PCT^7
)
NOT
(
  copyright OR privacy OR "patent leather" OR Urheberrecht OR Datenschutz
)
```

### Collaboration and ecosystem family

```text
(
  "research collaboration"^8 OR "joint research"^8 OR university^5 OR
  institute^4 OR "competence centre"^7 OR cluster^5 OR hub^4 OR
  "public-private"^7 OR "co-publication"^7 OR
  "Forschungskooperation"^8 OR Universität^5 OR Institut^4 OR
  Kompetenzzentrum^7 OR Cluster^5 OR Hub^4 OR "öffentlich-privat"^7
)
AND
(
  partner^2 OR consortium^3 OR publication^3 OR project^2 OR
  Partner^2 OR Konsortium^3 OR Publikation^3 OR Projekt^2
)
```

### Technology transfer and commercialization family

```text
(
  "technology transfer"^9 OR "knowledge transfer"^8 OR commercialization^7 OR
  "technology transfer office"^9 OR incubator^6 OR accelerator^6 OR spin-off^8 OR
  Technologietransfer^9 OR Wissenstransfer^8 OR Kommerzialisierung^7 OR
  Inkubator^6 OR Accelerator^6 OR Spin-off^8
)
AND
(
  licensing^3 OR startup^3 OR IP^2 OR market^2 OR licence^3 OR
  Lizenzierung^3 OR Startup^3 OR IP^2 OR Markt^2
)
```

For score fusion, a good default is:

- **Very high weight**: funded projects; prototypes/pilots/demonstrators; patents/IP when substantive; formal R&D.
- **High weight**: research collaboration; product/service innovation; technology transfer.
- **Medium weight**: research outputs/publications.
- **Medium-low weight**: process innovation.
- **Low weight alone**: generic innovation claims, labs without technical context, cluster memberships without research language. citeturn38view1turn38view2turn37view0turn37view3turn14view0

## Strongest signals and implementation priorities

If the goal is to identify **meaningful innovation/R&D evidence** at scale, the strongest signals are not individual words but **specific combinations of words, named entities, and contexts**.

The single strongest website signals are typically these:

1. **Named funded R&D or innovation projects** with programme identifiers, grant types, project IDs, partners, work packages, deliverables, or milestones. These are externally structured and highly explainable. Horizon Europe, COMET, BRIDGE, Christian Doppler, aws Proof of Concept, and Spin-off Fellowship references are especially valuable in the Austrian context. citeturn33view0turn28view0turn34view1turn34view2turn19view4turn23search4

2. **Prototype / pilot / demonstrator / testbed language** paired with validation or TRL language. This is one of the clearest signs that a firm is engaged in actual technology development rather than only promotion. citeturn19view2turn26view0turn28view0turn28view1

3. **Formal R&D capability** such as R&D teams, applied research units, experimental development, or research roadmaps. This is central to Frascati/OECD logic and highly relevant for AI ecosystem participation. citeturn6view3turn7view0turn7view1turn7view2

4. **Named research collaborations** with universities, research institutes, COMET centres, Christian Doppler Labs, public-private consortia, or co-publications. This aligns directly with EIS “linkages” and ecosystem participation. citeturn10view0turn10view3turn39view0turn34view1turn22search2

5. **Substantive IP and exploitation evidence**, especially patent applications, licensing, IP portfolios, and technology-transfer language connected to specific technologies or inventions. citeturn24view0turn40view0turn40view1turn40view2

6. **In-house product innovation with technical detail**, especially if the product is stated to be new to market, benchmarked, validated, or linked to research outputs or pilots. citeturn32view0turn31view2turn38view1

A practical scoring stack for your project would therefore be:

- **Tier A evidence**: funded-project pages, prototype/pilot pages, patent/licensing pages, formal R&D pages.
- **Tier B evidence**: collaboration pages, research-output pages, in-house technical product pages.
- **Tier C evidence**: process-innovation pages, innovation-lab / incubator pages, innovation management pages.
- **Tier D evidence**: generic “innovation” branding, unless corroborated by A/B/C evidence on the same page or domain. citeturn14view0turn38view1turn37view0

For multilingual robustness, two practical lessons from the literature matter. First, websites in multilingual settings often have different language versions, so score at **page level** and preserve language segmentation where possible. Second, because website size and the share of English text are themselves correlated with innovation-related outcomes, it is useful to keep **structural features** such as subpage count and English-language share as auxiliary features or priors—but not as substitutes for substantive lexical evidence. citeturn37view1turn37view2turn38view0turn38view2

## Open questions and limitations

This synthesis is high-confidence on the **conceptual structure** and on the **programme terminology**, but two parts remain context-sensitive.

First, the best **negative lexicon** will depend on your crawl mix. For example, Austrian manufacturing, software, consulting, and engineering firms use overlapping words such as *development*, *lab*, *pilot*, and *platform* in different ways. You should therefore validate negatives on a hand-labeled Austrian sample before freezing them. The literature is clear that website content is valuable but self-reported and uneven across firm sizes and sectors. citeturn14view0turn13view0turn37view3

Second, **process innovation** will almost certainly have lower recall than product or funded-project evidence on websites. That is not a failure of the ontology; it reflects what firms choose to publish online. If process innovation matters strategically for your mapping, you may want to use a lower threshold for process pages but require corroboration from other nodes before concluding that the domain is strongly innovation-active. citeturn38view1turn38view3

Overall, the most robust design choice is this: **reward concrete evidence of organized, technically substantive, externally linked, or externally validated innovation activity; downweight generic claims; and keep product, process, collaboration, funding, prototypes, IP, and technology transfer as separate explainable channels**. That is the best match to OECD/Eurostat/EU measurement logic and to what large-scale web mining can reliably recover from company websites. citeturn6view0turn6view3turn31view0turn10view0turn14view0turn29view0
