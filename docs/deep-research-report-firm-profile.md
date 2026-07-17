# Multilingual Firm Profile Scoring for Austrian Company Websites

## Research basis and design implications

A scalable firm-profile scorer for Austrian company websites should be built as a **fielded retrieval and evidence-aggregation system**, not as a single page classifier. The literature and official-statistics practice all point in the same direction: websites contain rich signals about what firms actually do, but those signals are distributed across page types, navigation labels, URL patterns, headings, and internal linking structures. Official-statistics work uses websites to support the determination of a firm’s **main economic activity** under NACE, while innovation and business-profiling research shows that website text can capture a more current and fine-grained picture of firms’ activities, offerings, competences, and market orientation than static registry codes alone. citeturn8view9turn8view6turn30view0

For your purpose, the most important implication is that the target is **not just “industry classification”**. The scoring layer should instead surface pages that jointly answer four operational questions: **what the company is**, **what it offers**, **who it serves**, and **how it describes its business value**. This matches how official-statistics projects and prior web-mining studies approach enterprise websites: they use web text to predict economic activity, detect enterprise characteristics, and derive business-level indicators, but they repeatedly note that good results depend more on the **quality and relevance of the input pages** than on using ever more complex models. citeturn36view3turn30view0turn32view0

That is why the best design pattern here is a **focused-first, generic-second** approach. Eurostat WIN material explicitly notes that focused scrapers can prioritize pages expected to contain economic-activity information, especially **about-us**, **company**, and **services** pages, while generic scraping covers the rest at larger scale. Statistics Austria’s work within WIN also used selected links such as `unternehmen`, `über uns`, `about us`, `produkt`, `product`, `dienstleistung`, `angebot`, and `leistung` to define relevant subpages for NACE prediction. This is directly usable as a page-prior layer for your query-family system. citeturn36view3turn36view1

The evidence also supports a **multi-field** design. In classical IR, field weighting is standard for structured documents, and web retrieval work routinely separates body text, title, URL, and anchor text. Microsoft’s BM25F-style studies and related work explicitly treat **body, title, URL, and anchor text** as distinct fields, while anchor text research shows that anchor text can act as a useful surrogate description of the destination page. At the same time, title extraction work shows that HTML title fields can be noisy or duplicated, so titles are valuable but should not be trusted blindly. citeturn20view0turn20view4turn18view2turn19view3

Finally, German and Austrian web conventions matter operationally. DACH business websites commonly include legally required or quasi-standard pages such as **Impressum**, privacy, and cookie pages; Austrian guidance confirms the importance of the **Impressumspflicht**, and Austria’s Data Protection Authority highlights cookie-consent requirements. Those pages are useful for URL validation and legal-unit matching, but they are usually weak evidence for business activity. German-market localization guidance also shows that German-language sites often require more detailed trust and company information, and that multilingual sites commonly use domain or subdirectory structures like `/de` and `/en`. This all argues for strong negative rules on legal/navigation pages, but not for discarding them entirely, because they remain useful for linkage and confirmation. citeturn9view11turn8view12turn31view1turn9view9

## Recommended ontology

I recommend structuring the dimension as a hierarchical ontology whose top layer separates **identity**, **activity**, **offerings**, **market orientation**, **proof**, and **value framing**:

```text
FIRM_PROFILE
├── company_description
├── business_activity_statements
│   ├── make_build_develop
│   ├── consult_integrate_implement
│   ├── operate_provide_distribute
│   └── support_train_maintain
├── offerings
│   ├── products
│   ├── services
│   └── solutions_platforms
├── market_orientation
│   ├── industries_sectors
│   ├── customers_segments
│   ├── geographies_markets
│   └── applications_use_cases
├── proof_signals
│   ├── customers_references
│   ├── case_studies_use_cases
│   └── partners_certifications
└── value_proposition
```

This structure is preferable to a flat “company / products / services” scheme because NACE-style economic-activity inference depends heavily on **activity language**, while firm profiling requires additional evidence about **offerings**, **served markets**, and **commercial proof**. Official-statistics literature consistently frames the task as identifying **main economic activity** and often supports manual editors rather than fully replacing them; business-profiling work likewise emphasizes that website-derived tags and descriptions help explain what firms do in more detail than standard industrial codes. citeturn10view1turn10view4turn30view0turn8view6

Within this ontology, the pages you most want to retain are the ones where several layers coincide. A page is especially valuable when it combines a company or offering label with a concrete activity verb, an object, and a market or customer context, such as “We develop industrial computer-vision software for automotive quality inspection” or “Unsere Leistungen für Energieversorger”. That combination is more diagnostic than a page that only says “innovative AI solutions” or “future-ready technology”. The literature on website-based innovation indicators also supports this emphasis on **textual content**, while showing that generic “innovation” wording is much weaker than richer textual and structural signals. citeturn23view0turn23view3

## Subdimension library with recommended seeds

Below is a practical seed dictionary for the top-level nodes. The terms are **recommended seed terms**, not exhaustive lexicons. They are synthesized from the focused-scraping and German business-website literature, NACE-from-web-text work, DACH localization guidance, and official-statistics web-classification practice. They are intended for query-family lexical retrieval, boosted field matches, and transparent rule-based scoring. citeturn36view1turn15view0turn10view5turn31view1

**`company_description`**  
**Rationale:** This is the backbone node. It captures the page where the firm presents itself at organization level: who it is, what kind of company it is, and how it summarizes its business. Focused scrapers and official-statistics approaches repeatedly prioritize the homepage and especially the about/company section for economic-activity inference. citeturn36view3turn36view0  
**Why it helps identify what the company does:** It usually contains the most compact high-level statement of the firm’s activities, often including sector nouns, activity verbs, and target markets in one place.  
**Typical evidence:** “About us / Über uns”; “Company profile / Unternehmensprofil”; “We are a software company…”; “Wir sind ein Anbieter von…”.  
**English seeds:** keywords `company`, `business`, `provider`, `manufacturer`, `developer`, `supplier`, `specialist`, `firm`; phrases `about us`, `company profile`, `what we do`, `who we are`, `our business`, `we develop`, `we manufacture`, `we provide`; boost terms `provider`, `manufacturer`, `developer`, `specialist`, `operator`, `supplier`.  
**German seeds:** keywords `unternehmen`, `anbieter`, `hersteller`, `entwickler`, `lieferant`, `spezialist`, `betrieb`, `firma`; phrases `über uns`, `unternehmensprofil`, `wer wir sind`, `was wir tun`, `unser unternehmen`, `wir entwickeln`, `wir produzieren`, `wir bieten`; boost terms `anbieter`, `hersteller`, `entwickler`, `spezialist`, `betreiber`, `lieferant`.  
**Negatives/cautions:** `impressum`, `datenschutz`, `cookies`, `agb`, `karriere`, `presse`, `blog`, `news`, `events`, `team` alone. `team` is contextual: useful only when it co-occurs with business-description language. citeturn9view11turn8view12turn15view0

**`business_activity_statements`**  
**Rationale:** This node captures the **verb layer** of what a company actually does. It is essential because many firms do not describe themselves primarily through industry labels; they describe themselves through actions: build, manufacture, integrate, advise, distribute, operate, maintain. NACE prediction work is driven by concepts and keywords tied to activity definitions, and website-tagging studies find that extracted web text can reveal activities and competences more directly than SIC/NACE labels alone. citeturn10view5turn8view6  
**Why it helps identify what the company does:** Activity verbs reduce the ambiguity of broad nouns like `technology`, `platform`, or `solution`. “We integrate ERP systems” is far more diagnostic than “digital transformation”.  
**Typical evidence:** “We manufacture…”, “We design and build…”, “Wir beraten…”, “Wir implementieren…”, “Distribuimos…”, “We operate…”.  
**English seeds:** keywords `develop`, `build`, `design`, `manufacture`, `produce`, `engineer`, `integrate`, `implement`, `consult`, `advise`, `operate`, `provide`, `distribute`, `resell`, `maintain`, `support`, `train`; phrases `we develop`, `we build`, `we manufacture`, `we provide services`, `we offer consulting`, `we implement systems`, `we operate a platform`, `we distribute`, `we support customers`; boost terms `manufacture`, `integrate`, `implement`, `consulting`, `operate`, `maintain`, `development`, `engineering`.  
**German seeds:** keywords `entwickeln`, `bauen`, `konstruieren`, `herstellen`, `produzieren`, `fertigen`, `integrieren`, `implementieren`, `beraten`, `betreiben`, `anbieten`, `vertreiben`, `warten`, `unterstützen`, `schulen`; phrases `wir entwickeln`, `wir bauen`, `wir fertigen`, `wir produzieren`, `wir beraten`, `wir implementieren`, `wir integrieren`, `wir betreiben`, `wir vertreiben`, `wir unterstützen unsere kunden`; boost terms `herstellung`, `fertigung`, `entwicklung`, `integration`, `implementierung`, `beratung`, `betrieb`, `wartung`.  
**Negatives/cautions:** verbs such as `support`, `optimize`, `enable`, `empower`, `transform`, `innovate` are weak without an object. Downweight them unless they co-occur with concrete nouns like `software`, `machine`, `sensor`, `audit`, `consulting`, or named industries/customers. citeturn23view0turn23view2

**`products`**  
**Rationale:** Product pages often encode the clearest evidence of what a firm sells or manufactures. Product-extraction literature specifically treats the identification of product pages within heterogeneous company websites as a distinct, valuable task, and shows that filtering for product pages before extraction greatly improves quality and scalability. citeturn9view6turn9view7  
**Why it helps identify what the company does:** Products reveal concrete outputs: software packages, devices, machines, materials, tools, systems, modules, apps, platforms, or physical goods.  
**Typical evidence:** product catalogues, lists of SKUs or model names, product families, feature and specification blocks, “our products / unsere Produkte”.  
**English seeds:** keywords `product`, `products`, `portfolio`, `catalog`, `model`, `device`, `machine`, `tool`, `module`, `equipment`, `software`, `platform`, `app`; phrases `our products`, `product portfolio`, `product overview`, `product line`, `technical specifications`, `features`, `data sheet`, `product family`; boost terms `products`, `portfolio`, `catalog`, `specifications`, `datasheet`, model-like alphanumeric strings, `SKU`.  
**German seeds:** keywords `produkt`, `produkte`, `produktportfolio`, `sortiment`, `anlage`, `maschine`, `gerät`, `werkzeug`, `modul`, `software`, `plattform`, `app`; phrases `unsere produkte`, `produktübersicht`, `produktlinie`, `technische daten`, `datenblatt`, `produktfamilie`, `produktportfolio`; boost terms `produkte`, `datenblatt`, `technische daten`, `produktübersicht`, `modell`, article-like item codes.  
**Negatives/cautions:** `productivity`, `production`, `portfolio` used only for investment or customer showcases, and `platform` without any concrete deliverable. URL path features for product pages are often high precision but not high recall, so URL should boost rather than dominate. citeturn9view7

**`services`**  
**Rationale:** Many firms, especially consultancies, integrators, agencies, engineering companies, and B2B software firms, explain themselves primarily through services rather than products. WIN explicitly includes `services` among target link labels for pages likely to contain economic-activity information. citeturn36view0  
**Why it helps identify what the company does:** Service language often names the firm’s actual business model: consulting, implementation, maintenance, audit, managed services, outsourcing, development, training.  
**Typical evidence:** “Services / Leistungen / Services”; capability menus; project phases; support contracts; managed-service descriptions.  
**English seeds:** keywords `service`, `services`, `consulting`, `implementation`, `integration`, `maintenance`, `support`, `training`, `outsourcing`, `managed service`, `design`, `engineering`, `audit`; phrases `our services`, `service portfolio`, `consulting services`, `implementation services`, `managed services`, `support and maintenance`, `training services`; boost terms `services`, `consulting`, `integration`, `implementation`, `managed`, `maintenance`, `support`.  
**German seeds:** keywords `dienstleistung`, `dienstleistungen`, `leistungen`, `beratung`, `implementierung`, `integration`, `wartung`, `support`, `schulung`, `outsourcing`, `engineering`, `prüfung`, `audit`; phrases `unsere leistungen`, `unsere dienstleistungen`, `leistungsportfolio`, `beratungsleistungen`, `implementierungsleistungen`, `support und wartung`; boost terms `leistungen`, `dienstleistungen`, `beratung`, `integration`, `implementierung`, `wartung`, `support`.  
**Negatives/cautions:** `customer service`, `self-service`, `service area`, `service desk`, `service center` can be support-only or navigational. Downweight unless paired with a commercial service offer. citeturn36view1

**`solutions_platforms`**  
**Rationale:** On European B2B websites, especially tech and industrial sites, firms often package offerings as “solutions” rather than pure products or services. This node is useful because “solutions” pages often bundle a target problem, an offering configuration, and a specific vertical. Localization guidance for the German market also emphasizes the need to articulate value proposition, products/services, and target audience in the site’s content and messaging. citeturn31view1  
**Why it helps identify what the company does:** A solution page can connect an abstract capability to a concrete commercial problem, even when the page is not a PDP or service detail page.  
**Typical evidence:** “solutions for manufacturing”, “Lösungen für Banken”, “platform for industrial analytics”, “end-to-end solution”.  
**English seeds:** keywords `solution`, `solutions`, `platform`, `suite`, `end-to-end`, `workflow`, `system`, `application`; phrases `solutions for`, `industry solutions`, `business solutions`, `end-to-end solution`, `platform for`, `solution portfolio`; boost terms `solutions for [industry]`, `platform for [task]`, `end-to-end`, `suite`, `workflow`.  
**German seeds:** keywords `lösung`, `lösungen`, `plattform`, `suite`, `system`, `anwendung`, `workflow`; phrases `lösungen für`, `branchenlösungen`, `systemlösungen`, `end-to-end lösung`, `plattform für`, `lösungsportfolio`; boost terms `lösungen für [branche]`, `plattform für [aufgabe]`, `systemlösung`, `branchenlösung`.  
**Negatives/cautions:** `solution` and `platform` are among the most ambiguous business terms on the web. Require co-occurrence with an object, industry, use case, or customer need. Otherwise they are classic marketing fluff. citeturn31view1turn23view0

**`industries_markets`**  
**Rationale:** Industry and market pages directly answer who the firm serves. They are especially useful when the company is a horizontal technology provider whose sector cannot be inferred from the product name alone. German-market guidance explicitly emphasizes target audience, routes to market, competitors, products/services, and value proposition as key content decisions for localized business websites. citeturn31view1  
**Why it helps identify what the company does:** If a firm says it serves hospitals, utilities, automotive suppliers, and logistics operators, that sharply narrows its real activity.  
**Typical evidence:** “Industries / Branchen / Sectors / Markets”; vertical pages; “solutions for healthcare”; “Märkte”; export or regional market pages.  
**English seeds:** keywords `industry`, `industries`, `sector`, `market`, `markets`, `vertical`, `application`, `segment`; phrases `industries we serve`, `solutions for`, `serving the`, `target markets`, `market segments`, `applications in`; boost terms named industries such as `automotive`, `healthcare`, `manufacturing`, `energy`, `finance`, `construction`, `retail`, `logistics`, `public sector`.  
**German seeds:** keywords `branche`, `branchen`, `sektor`, `markt`, `märkte`, `anwendung`, `segment`; phrases `branchen`, `branchenlösungen`, `wir bedienen`, `zielmärkte`, `marktsegmente`, `anwendungen in`, `lösungen für`; boost terms named industries such as `automobil`, `gesundheitswesen`, `fertigung`, `energie`, `finanzwesen`, `bau`, `handel`, `logistik`, `öffentlicher sektor`.  
**Negatives/cautions:** `global market leader`, `market presence`, `capital markets`, `market news` are weak unless they clearly indicate served sectors or geographies. citeturn31view1

**`customers_references`**  
**Rationale:** Reference pages are powerful evidence because they ground claims in actual customers, buyers, or deployments. German-market guidance treats testimonials and trust content as important for trust building, and company-website studies treat links, network structure, and external references as informative web signals. citeturn31view1turn18view2  
**Why it helps identify what the company does:** Real client names, sectors, and project contexts reveal the company’s authentic commercial scope better than slogans do.  
**Typical evidence:** “Customers / Clients / Referenzen / Kunden”; customer logos; testimonial blocks; “selected clients”.  
**English seeds:** keywords `customer`, `customers`, `client`, `clients`, `reference`, `references`, `testimonial`, `success story`; phrases `our customers`, `selected clients`, `customer references`, `trusted by`, `client testimonials`; boost terms customer names, sector nouns near customer names, `trusted by`, `selected customers`, logo grids with captions.  
**German seeds:** keywords `kunde`, `kunden`, `referenz`, `referenzen`, `testimonial`, `erfolgsgeschichte`; phrases `unsere kunden`, `ausgewählte kunden`, `kundenreferenzen`, `vertrauen auf uns`, `referenzkunden`; boost terms `referenzen`, `kunden`, customer names, logo grids with textual context.  
**Negatives/cautions:** partner lists and press mentions can look similar to customer evidence. Treat `partner`, `netzwerk`, `alliance`, `media` as separate or lower-confidence signals unless a commercial relationship is explicit. citeturn31view1

**`case_studies_use_cases`**  
**Rationale:** Case studies are one of the best high-value page types because they often contain the full triad of **problem -> solution -> outcome**. Localization material also notes that firms frequently maintain and translate new case studies for German-language sites, which makes them common on export-facing DACH business sites. citeturn31view0turn31view1  
**Why it helps identify what the company does:** These pages usually contain the most concrete description of deliverables, customer segment, implementation approach, and results.  
**Typical evidence:** “Case study / Erfolgsgeschichte / Anwendungsfall / Use case”; customer project narratives; “challenge”, “solution”, “result”.  
**English seeds:** keywords `case study`, `use case`, `project`, `implementation`, `deployment`, `customer story`, `reference project`; phrases `challenge`, `solution`, `results`, `how we helped`, `example application`, `success story`; boost terms the sequence `challenge`, `solution`, `result`, plus client/industry names.  
**German seeds:** keywords `fallstudie`, `anwendungsfall`, `projekt`, `implementierung`, `einsatz`, `kundenprojekt`, `erfolgsgeschichte`, `referenzprojekt`; phrases `ausgangssituation`, `herausforderung`, `lösung`, `ergebnis`, `so haben wir`, `beispielanwendung`; boost terms the sequence `herausforderung`, `lösung`, `ergebnis`, plus client/industry names.  
**Negatives/cautions:** blogs or news posts about projects are weaker than dedicated case-study templates. Require either structured challenge-solution-result language or strong customer context. citeturn31view0

**`value_proposition`**  
**Rationale:** This node captures how the firm frames the benefit of its offering: cost reduction, automation, efficiency, compliance, quality, risk reduction, speed, reliability, performance, sustainability. German-market guidance puts explicit emphasis on value proposition, USP, target audience needs, and trust content, all of which matter for identifying commercially meaningful pages. citeturn31view1  
**Why it helps identify what the company does:** On its own, value language is weak; but when combined with offering and customer language it helps distinguish a page that explains the business from one that is only descriptive.  
**Typical evidence:** “Why us”; “Benefits”; “Mehrwert”; “we help [customer] reduce…”; “USP”; “competitive advantage”; “why customers choose us”.  
**English seeds:** keywords `value`, `benefit`, `benefits`, `advantage`, `USP`, `efficiency`, `quality`, `compliance`, `automation`, `optimization`, `reliability`, `scalability`; phrases `why choose us`, `value proposition`, `what sets us apart`, `reduce costs`, `increase efficiency`, `improve quality`, `ensure compliance`, `shorter time-to-market`; boost terms `reduce`, `increase`, `improve`, `ensure`, `faster`, `safer`, `more efficient` when paired with concrete objects.  
**German seeds:** keywords `mehrwert`, `nutzen`, `vorteil`, `usp`, `effizienz`, `qualität`, `compliance`, `automatisierung`, `optimierung`, `zuverlässigkeit`, `skalierbarkeit`; phrases `warum wir`, `ihr mehrwert`, `was uns auszeichnet`, `kosten senken`, `effizienz steigern`, `qualität verbessern`, `compliance sicherstellen`, `kürzere time-to-market`; boost terms `senken`, `steigern`, `verbessern`, `sicherstellen`, `schneller`, `effizienter` when paired with concrete objects.  
**Negatives/cautions:** this is the node most vulnerable to generic marketing language. Downweight `innovative`, `cutting-edge`, `future-proof`, `digital`, `smart`, `sustainable`, `leading`, `best-in-class` unless anchored to concrete offering, activity, and market evidence. citeturn23view0turn31view1

## Query families and page-type discrimination

For query-family lexical retrieval, I would use **query families** rather than one large “firm profile” query. Query families are easier to debug, easier to multilingualize, and easier to analyze per page type. They also align with official-statistics practice, where different studies compare homepage-only, selected-link, and broader-page strategies rather than betting on one monolithic representation. Statistics Netherlands found that homepage-only predictions were often close to using the homepage plus one underlying layer, but not uniformly so across sectors; that is exactly why separate query families are useful. citeturn9view3turn9view5

A practical family set is:

**Company overview family.**  
Target: about/company pages.  
Seed queries: `about us`, `company profile`, `what we do`, `our company`, `über uns`, `unternehmen`, `unternehmensprofil`, `wer wir sind`.  
Best fields: title, H1, H2, URL, anchor text, first 400 body tokens.  
Use this family to retrieve the page that most directly summarizes the firm. citeturn36view3turn15view0

**Activity family.**  
Target: verb-rich descriptions of actual business activity.  
Seed queries: `develop manufacture integrate consulting provide services`, `wir entwickeln herstellen integrieren beraten dienstleistungen`.  
Best fields: main text, H1/H2, nearby noun phrases.  
This family is the best hedge against vague brand pages because it requires action language. citeturn10view5turn8view6

**Products family.**  
Target: product overview and PDP-like pages.  
Seed queries: `products product portfolio catalog solutions software platform data sheet`, `produkte produktportfolio datenblatt produktübersicht software plattform`.  
Best fields: URL, title, H1, structured lists, model codes.  
Product-page research suggests URL path segments are strong precision features, but body and HTML evidence are still needed for recall. citeturn9view7

**Services family.**  
Target: service-offering pages.  
Seed queries: `services consulting implementation integration maintenance support`, `leistungen dienstleistungen beratung implementierung integration wartung support`.  
Best fields: title, URL, H1/H2, main text.  
Use this to capture firms that are primarily service businesses, especially consulting, engineering, and implementation companies. citeturn36view0

**Solutions family.**  
Target: bundled offering pages.  
Seed queries: `solutions for industry platform for workflow end-to-end`, `lösungen für branchenlösung plattform für systemlösung`.  
Best fields: title/H1, H2, internal anchor text, co-occurring industry/customer terms.  
Because `solutions` and `platform` are ambiguous, this family should score highest when paired with an industry or task phrase. citeturn31view1

**Industry and market family.**  
Target: vertical pages and market pages.  
Seed queries: `industries we serve sectors markets applications`, `branchen märkte anwendungen zielmärkte`.  
Best fields: title, H1/H2, internal navigation links, anchor text.  
These pages are especially helpful for horizontally positioned firms. citeturn31view1

**Customer/reference family.**  
Target: reference pages and trust pages with real commercial evidence.  
Seed queries: `customers clients references testimonials trusted by`, `kunden referenzen kundenreferenzen vertrauen auf uns`.  
Best fields: title, H1, logo captions, internal anchors, nearby sector names.  
This family should have medium-to-high value, but only when customer evidence is textual and not just image-only. citeturn31view1

**Case-study/use-case family.**  
Target: high-value proof pages.  
Seed queries: `case study use case challenge solution result project`, `fallstudie anwendungsfall herausforderung lösung ergebnis projekt`.  
Best fields: title/H1/H2, section headings, main text.  
These are among the best pages for explaining what the company really does in market context. citeturn31view0

**Value family.**  
Target: benefit and differentiation pages.  
Seed queries: `why us value proposition benefits improve reduce increase`, `warum wir mehrwert nutzen vorteile verbessern senken steigern`.  
Best fields: title/H1, supporting paragraphs, co-occurring offer and customer terminology.  
Use as an auxiliary family, not as the primary family, because the risk of generic marketing fluff is high. citeturn31view1turn23view0

For page-type discrimination, use a separate transparent ruleset:

**Company/about pages** usually have `about`, `company`, `unternehmen`, `über uns`, and contain a compact organization-level description.  
**Product pages** usually have `product`, `produkte`, model names, specs, data sheets, and stronger URL precision.  
**Service pages** usually have `services`, `leistungen`, consulting/integration verbs, process descriptions, and commercial delivery wording.  
**Solution pages** usually combine outcome language plus an industry/task target.  
**Industry/sector pages** usually have vertical nouns and repeated sector headers.  
**Customer/reference pages** usually contain client names, “trusted by”, “Referenzen”, testimonials, or logo walls with captions.  
**Case-study/use-case pages** usually follow challenge-solution-result structure.  
**Market/value pages** usually contain benefits and differentiation claims, but need co-evidence from offerings or customers.  
**Generic contact/legal/navigation pages** usually contain address, phone, VAT, privacy, cookie, terms, or imprint language.  
**Generic marketing pages with low activity value** often have many adjectives and slogans but few concrete nouns, few target-market mentions, and little action/object structure. citeturn15view0turn36view1turn9view7turn9view11turn8view12

## False positives and multilingual caution rules

For large-scale web mining, false positives will come less from obvious errors and more from **plausible but weak pages**. The highest-risk classes are legal pages, careers pages, news/blog pages, thin campaign pages, and generic brand language. Austrian and DACH sites make legal pages especially common because of the **Impressum** and data-protection conventions; these should be tagged explicitly and strongly downweighted for firm-profile purposes. At the same time, they remain useful for legal-unit verification because official-statistics projects note that identifying information such as chamber-of-commerce or tax identifiers can support page-to-unit matching. citeturn9view11turn36view3

The most important **negative keywords** are:

`impressum`, `datenschutz`, `cookies`, `cookie-einstellungen`, `agb`, `widerruf`, `legal`, `privacy`, `terms`, `accessibility`, `barrierefreiheit`, `sitemap`, `karriere`, `jobs`, `stellenangebote`, `bewerbung`, `news`, `blog`, `presse`, `media`, `events`, `messen`, `newsletter`, `download center`, `faq`, `support portal`, `login`, `shop cart`, `checkout`. These do not all imply exclusion. They imply either low relevance or relevance only for another dimension. citeturn9view11turn8view12turn11view4turn32view2

Important **downweight terms** are generic business words that occur everywhere: `solution`, `platform`, `technology`, `digital`, `innovation`, `innovative`, `system`, `service`, `support`, `quality`, `partner`, `network`, `future`, `smart`, `AI`, `automation`. These should not be treated as strong positives unless they are joined to a concrete object or served market. The innovation-indicator literature is particularly useful here: it found that broader structural and text-rich features were more predictive than simply spotting words like “innovative”, and that textual context matters more than single buzzwords. citeturn23view0turn23view2

You should also define a **context-required class**. Terms in this class only fire positively when they satisfy a co-occurrence rule, proximity rule, or cross-field corroboration rule. I would place the following here:  
`platform`, `solution`, `application`, `system`, `consulting`, `engineering`, `support`, `management`, `service`, `digital transformation`, `optimization`, `automation`, `compliance`, `sustainability`, `AI`, `data`, `analytics`.  
For example, `platform` is positive only if within a short window of a task (`for monitoring`, `für Qualitätsprüfung`) or a product noun (`software platform`, `IoT platform`). `Consulting` is positive only if near a domain object (`SAP consulting`, `regulatory consulting`, `IT-Beratung`). `AI` is positive only if near an offering, activity, or market term, not merely in a slogan. citeturn31view1turn23view0

For multilingual robustness, build explicit normalization for **umlaut and transliteration variants** such as `über` / `ueber`, `lösungen` / `loesungen`, and recognize the hybrid German-English style common on DACH B2B websites, where pages may mix `Über uns`, `Solutions`, `Use Cases`, `Referenzen`, and `/en` or `/de` URL structure. The German-business-website extraction work explicitly notes that its methods can transfer to non-German websites with only limited language-specific modifications, while German-market localization guidance recommends industry-aware translation, local terminology, and language-specific site structures and content. citeturn15view4turn31view3turn31view1

## Signal weighting and implementation guidance

The strongest pages for “what the company does” are the ones where **high-value page type + concrete activity language + market or customer context** occur together. In practical scoring terms, the highest-confidence positive is not a standalone keyword hit. It is a **compound evidence pattern** such as:

- title/H1 says `About us`, `Services`, `Products`, `Solutions`, `Branchen`, `Referenzen`
- URL supports the same interpretation
- main text contains concrete activity verbs plus object nouns
- internal anchor text or surrounding navigation corroborates the page type
- a customer, industry, or application context is present

This layered approach matches both IR practice and official-statistics evidence: weighted fields matter, focused page selection matters, and input-page quality often matters more than model complexity. citeturn20view0turn36view3turn30view0

My recommended **starting signal hierarchy** is:

**Main text** should be the strongest total contributor. Innovation-indicator work shows that textual content is the most important feature block, and firm-tagging work shows that web descriptions reveal activities, specializations, and competences with more granularity than static codes. Strong main-text evidence should therefore dominate final score once page-type priors have admitted a page into consideration. citeturn23view3turn8view6

**Headings** should come next, especially H1/H2-like extracted headings. Title-extraction and structured-document retrieval research show that conspicuous page headings and structured zones carry unusually strong topical information. In practice, headings are often cleaner than body text and more reliable than raw HTML title alone for identifying page intent. citeturn19view3turn22view1

**Page title** should be highly weighted, but not absolute. Page titles are excellent when good, but web retrieval research shows many title fields are empty, repeated, or low quality. So title should strongly boost but never by itself determine inclusion or exclusion. citeturn19view3turn19view1

**URL path** should get a meaningful precision weight. Product-page work shows URL-segment features can have high precision, which is exactly what you want for page-type priors. But they are not sufficient for recall, so URL should function as a strong prior rather than a full judge. This is especially useful for `/about`, `/ueber-uns`, `/services`, `/leistungen`, `/products`, `/produkte`, `/industries`, `/branchen`, `/references`, `/referenzen`, `/case-study`, `/anwendungsfall`. citeturn9view7turn36view1

**Internal links and anchor text** should be given real weight. Web retrieval work treats referring anchor text as a distinct field, and enterprise-URL discovery work explicitly uses anchor-based queries such as `inanchor:contact`, while agreement features on title/snippet/URL prove that structural search-result fields are informative. For your application, internal anchor text is especially valuable for pages reached from menus like `Über uns`, `Leistungen`, `Produkte`, `Branchen`, or `Referenzen`. citeturn20view0turn18view2turn28view0turn28view3

A good **starting field-weight schema** for fielded lexical scoring would be something like: main text `1.0`, H1/H2 extracted headings `1.8–2.2`, page title `1.6–2.0`, URL path `1.3–1.7`, internal anchor text `1.3–1.6`, menu/navigation breadcrumbs `1.1–1.4`. I would then add **page-type priors** and **negative priors** outside the query-family component: for example, +strong prior for `about/company/services/products/branchen/referenzen` and −strong prior for `impressum/privacy/cookies/jobs/news/blog`. These exact numbers are design recommendations rather than literature constants; the literature supports the principle of field weighting, not one universal setting. citeturn20view0turn22view1turn20view4

For implementation, I would use the following pipeline:

1. **Normalize and tokenize** with German decompounding where possible, preserve English and German forms, normalize umlaut/transliteration pairs, and index title, headings, URL tokens, main text, and internal anchor text separately. The need for industry-aware, localized terminology and the coexistence of German and English terminology on DACH websites make this indispensable. citeturn9view9turn31view1

2. **Assign a page-type prior** from URL, title, H1, and incoming internal anchor text. This emulates the focused-scraper heuristics used in official-statistics practice. citeturn36view0turn36view1

3. **Run multiple query families** per page rather than one large family, and keep separate explainable subscores for company description, activity, products, services, market, references, case studies, and value. This gives you retrieval transparency and lets you keep diverse but relevant pages instead of over-optimizing for one label. citeturn30view0turn8view6

4. **Apply negative and contextual rules after retrieval**, not before indexing. That allows you to keep pages such as case studies or customer references even if they include boilerplate words, while still downweighting legal or HR pages hard. Official-statistics quality work repeatedly stresses caution and validation rather than assuming a single automated rule works everywhere. citeturn32view0turn11view0

5. **Promote pages with corroborated evidence across fields**. A page with `services` in the URL, `Leistungen` in anchor text, an H1 with `Beratung und Implementierung`, and body text naming sectors and deliverables should outrank a homepage that only says `innovative digital solutions`. citeturn36view1turn23view0

6. **Retain a small diverse set of top pages per domain**, not one winner-takes-all page. For the knowledge base, you usually want the best 3–8 pages that jointly cover company description, offerings, industries, references, and case studies. That is more robust than one-page selection, especially because websites may represent multiple legal units, multiple business lines, or multilingual variants. citeturn36view3turn11view0

In short, the strongest firm-profile pages are usually the ones that look like **about/company**, **services**, **products**, **solutions-by-industry**, **references**, and **case studies**—and the strongest evidence is usually a **concrete verb + concrete object + concrete market/customer** pattern, supported by headings, URL, and internal anchor text. That design is scalable, multilingual, explainable, and well aligned with how official-statistics and web-mining research already approach enterprise websites. citeturn36view0turn15view0turn9view7turn30view0
