# Technology ontology for multilingual company-website scoring

## Research basis and design implications

For your use case, website text is a strong basis for scalable technology scoring, but it has to be handled as a biased, heterogeneous, multilingual signal rather than as a ground-truth record. Web-based firm indicators are attractive because they are timely, relatively cheap to collect, can be updated frequently, can often be published at firm level, and can capture emerging technologies more quickly than survey instruments. At the same time, firm websites are self-presentational, uneven in depth across firms, sensitive to language differences, and highly dependent on search strategy and keyword choice. In the firm-website literature, text content consistently carries most of the predictive power, while website structure and metadata are useful but secondary. citeturn15view3turn16view0turn28view0

That has two direct consequences for a BM25-style scorer. First, the ontology should be **homogeneous**, because heterogenous classes are harder to classify robustly and create unstable lexical neighborhoods. Second, the lexicon needs **maintenance for concept drift**, especially in IT- and AI-related categories where terminology changes quickly. The Netherlands website-classification work on predicting NACE codes explicitly recommends maintaining knowledge-based features over time and notes that concept drift is especially relevant for IT-related codes. citeturn15view4turn12view4

The policy and taxonomy literature points to a practical split between **AI**, **data**, **software**, **automation**, **robotics**, and adjacent enabling technologies. The OECD AI definition and the EU AI Act definition both center AI on a system’s ability to **infer** how to generate outputs such as predictions, content, recommendations, or decisions, rather than on generic software, dashboards, or fixed rule execution. The OECD AI classification framework also separates data/input, AI model, task/output, and economic context, which is a useful template for a website-scoring ontology. At European ecosystem level, Adra institutionalizes the **AI, Data, and Robotics** triad, while the European Commission’s ATI work treats AI, Big Data, Cloud, IoT, Advanced Manufacturing, Robotics, and related technologies as distinct but connected advanced-technology families. citeturn12view0turn23view0turn24view5turn15view1turn27view0turn15view5turn17view0

The Austrian context reinforces that split. Austria’s national AI strategy describes AI activity as concentrated in software development, data processing, consultancies using their own software, and research strengths in machine learning, symbolic processes, robotics, autonomous systems, production, Industry 4.0, predictive maintenance, and embedded systems. That makes Austrian terminology especially likely to mix English AI vocabulary with German industrial and engineering vocabulary such as *Industrie 4.0*, *eingebettete Systeme*, *Automatisierungstechnik*, and *vorausschauende Wartung*. citeturn15view2turn12view7

## Recommended technology ontology

The most useful top-level design for a company-website technology scorer is not “AI versus non-AI,” but a **multi-label technology stack** that preserves adjacent capabilities. That makes the system more explainable, gives you better separation between producers and adopters, and reduces false positives from generic digital language. It also aligns well with OECD/EU policy language, ATI terminology, Austrian AI-strategy language, software-engineering capability frameworks, and firm-website classification evidence. citeturn15view1turn15view5turn15view2turn25view0turn15view4

```text
TECHNOLOGY
├── artificial_intelligence
│   ├── ai_methods
│   ├── ai_applications
│   └── ai_engineering
├── data_and_analytics
│   ├── data_engineering
│   └── analytics_and_decision_support
├── software_and_cloud
│   ├── software_products
│   └── cloud_api_devops
├── automation_and_control
│   ├── industrial_control
│   ├── iiot_edge_cps
│   └── optimization_and_maintenance
├── robotics_and_autonomous_systems
│   ├── industrial_robotics
│   └── autonomous_mobile_systems
├── digital_platforms
│   ├── multi_sided_platforms
│   └── ai_data_service_platforms
└── engineering_and_embedded_technology
    ├── embedded_electronics_sensors
    └── simulation_digital_twin
```

In practice, this ontology should be treated as **overlapping**, not mutually exclusive. Many Austrian and European firms will legitimately score on several nodes at once: for example, an industrial firm may be **automation + robotics + AI adopter**; a SaaS company may be **software + data + AI producer**; and a mechatronics company may be **engineering + embedded + robotics**, with only weak or emerging AI evidence. This multi-label treatment reflects both the European AI/Data/Robotics framing and the observed heterogeneity of real firm websites. citeturn27view0turn15view2turn10search0turn10search1

## Lexical knowledge base

The lexical recommendations below are **designed outputs** for BM25-style scoring, synthesized from OECD and EU AI definitions, the OECD AI classification framework, the European AI-taxonomy and ATI terminology, Austrian AI-strategy language, industrial AI literature, software-engineering capability frameworks, robotics standards, and firm-website mining studies. They are meant to be operational rather than exhaustive. citeturn23view0turn15view1turn15view0turn17view0turn15view2turn25view0turn25view6turn25view7turn16view0

**artificial_intelligence**  
This node is the strongest direct indicator of AI-ecosystem participation because OECD and EU definitions distinguish AI from generic software through inference, autonomy, and output generation, while the European AI-taxonomy literature further breaks AI into capability families such as computer vision, computer audition, computer linguistics, forecasting, discovery, planning, creation, and robotics. On company websites, this node should capture both **AI producers/developers** and firms with explicit, technically detailed AI offerings. citeturn23view0turn24view5turn15view0

**Why useful:** It identifies the firms most likely to be building, productizing, integrating, or seriously deploying AI rather than merely using generic digital tools.  
**AI-ecosystem relation:** Primary signal for direct AI participation.  
**Typical trigger activities:** developing ML models; offering computer vision or NLP systems; building recommendation engines; deploying anomaly detection; creating generative AI products; training models for prediction, decision support, or autonomous operation.

**English keywords:** ai, artificial intelligence, machine learning, ml, deep learning, neural network, computer vision, machine vision, natural language processing, nlp, speech recognition, llm, foundation model, generative ai, reinforcement learning, expert system, symbolic ai, knowledge graph, model inference, model training, mlops.  
**English multi-word phrases:** artificial intelligence solutions, machine learning models, computer vision system, natural language processing, predictive model, anomaly detection, recommendation engine, decision support system, generative AI platform, foundation model deployment, model training pipeline, explainable AI.  
**English boost terms:** model, training, inference, deployment, mlops, fine-tuning, multimodal, autonomous, reasoning, classifier, detector, segmentation, optimisation, prediction.  
**German keywords:** ki, künstliche intelligenz, maschinelles lernen, machine learning, deep learning, neuronales netz, computer vision, bildverarbeitung, nlp, verarbeitung natürlicher sprache, spracherkennung, llm, foundation model, generative ki, symbolische ki, wissensbasierte systeme, wissensgraph, inferenz, modelltraining, mlops.  
**German multi-word phrases:** künstliche intelligenz lösungen, maschinelles lernen modelle, computer vision system, verarbeitung natürlicher sprache, prädiktives modell, anomalieerkennung, empfehlungssystem, entscheidungsunterstützungssystem, generative ki plattform, erklärbare ki, modellbereitstellung, modelltraining pipeline.  
**German boost terms:** modell, training, inferenz, deployment, feintuning, multimodal, autonom, klassifikator, detektion, segmentierung, prognose, optimierung.  
**Node-specific negatives:** smart, intelligent, innovative, future-ready, AI-ready, digital assistant, virtual assistant, optimizer, dashboard, statistics, rules engine, heuristic.

**data_and_analytics**  
This node matters because data capabilities are a core adjacent capability for AI adoption and because European ecosystem language explicitly couples AI with data. The ATI framework treats Big Data and Cloud as distinct advanced technologies, and the analytics-capabilities literature treats big data analytics capability as a source of competitive performance. For websites, this node is particularly useful for identifying **data-intensive firms**, AI enablers, and AI adopters with strong data foundations. citeturn27view0turn17view0turn25view3

**Why useful:** It captures firms whose technology base is data-centric even when they do not claim AI explicitly.  
**AI-ecosystem relation:** Strong adjacent signal; becomes especially important when combined with predictive, recommendation, optimization, or MLOps language.  
**Typical trigger activities:** building data platforms; offering BI and advanced analytics; operating data pipelines; real-time analytics; forecasting; sensor-data analysis; data products.

**English keywords:** data, data analytics, analytics, data science, business intelligence, bi, advanced analytics, predictive analytics, data engineering, data platform, data pipeline, etl, elt, data warehouse, data lake, lakehouse, dashboard, reporting, visualization, decision support, data governance, master data, streaming.  
**English multi-word phrases:** data platform, data engineering, business intelligence, advanced analytics, predictive analytics, real-time analytics, decision support, data warehouse, data lakehouse, data integration, streaming analytics, analytics platform.  
**English boost terms:** predictive, forecasting, segmentation, recommendation, optimization, feature store, streaming, real-time, semantic layer, lakehouse, governance, observability.  
**German keywords:** daten, datenanalyse, datenanalytik, datenwissenschaft, business intelligence, analytik, advanced analytics, prädiktive analytik, data engineering, datenplattform, datenpipeline, etl, elt, data warehouse, data lake, lakehouse, dashboard, berichtswesen, visualisierung, entscheidungsunterstützung, datenmanagement, datenintegration.  
**German multi-word phrases:** datenplattform, data engineering, business intelligence, prädiktive analytik, echtzeitanalytik, datenwarehouse, data lakehouse, datenintegration, entscheidungsunterstützung, analytics plattform, datenpipeline, streaming analytik.  
**German boost terms:** prädiktiv, prognose, empfehlung, optimierung, feature store, streaming, echtzeit, semantic layer, governance, observability.  
**Node-specific negatives:** reporting only, dashboard only, descriptive analytics only, survey statistics, media analytics, web analytics without technical evidence.

**software_and_cloud**  
This node captures a major share of Austrian and European technology firms because Austrian AI activity is strongly tied to software development and data processing, and because software-engineering capability frameworks emphasize architecture, software design, programming, systems integration, embedded development, release, deployment, and quality assurance. DevOps literature also treats development-operation integration, automation, and delivery capability as core software-production capacity. citeturn15view2turn25view0turn25view2turn15view6

**Why useful:** It separates genuine software firms from broader digital-service firms and identifies the deployment substrate for AI.  
**AI-ecosystem relation:** High-value enabling node; becomes very strong when paired with AI, analytics, platform, or embedded terms.  
**Typical trigger activities:** SaaS products; APIs; application development; cloud-native architecture; system integration; CI/CD; custom software; enterprise software.

**English keywords:** software, software development, software engineering, saas, application, app, platform, api, sdk, integration, system integration, cloud, cloud-native, microservices, devops, ci/cd, kubernetes, container, deployment, release management, embedded software, enterprise software.  
**English multi-word phrases:** software development, custom software, software products, enterprise software, software as a service, cloud platform, cloud-native application, system integration, rest api, platform engineering, continuous integration, continuous deployment.  
**English boost terms:** saas, api, sdk, microservices, kubernetes, ci/cd, cloud-native, release, deploy, integration, architecture, platform engineering.  
**German keywords:** software, softwareentwicklung, softwaretechnik, software engineering, saas, anwendung, app, plattform, api, sdk, systemintegration, cloud, cloud-native, microservices, devops, ci/cd, container, deployment, embedded software, unternehmenssoftware.  
**German multi-word phrases:** softwareentwicklung, individuelle software, softwareprodukte, unternehmenssoftware, software as a service, cloud plattform, cloud-native anwendung, systemintegration, rest api, plattform engineering, continuous integration, continuous deployment.  
**German boost terms:** saas, api, sdk, microservices, kubernetes, ci/cd, cloud-native, release, deployment, integration, architektur, plattform engineering.  
**Node-specific negatives:** website platform, learning platform, communication platform, collaboration platform, digital solution, software-supported, IT support only.

**automation_and_control**  
This node is crucial for Austrian industrial mapping because industrial AI in Europe is repeatedly described through applications such as predictive maintenance, quality assurance, and production/process optimization, while ATI defines advanced manufacturing in terms of robotics, automation technology, computer-integrated manufacturing, and measuring/control/testing devices. This node is the main bridge between manufacturing technology and AI adoption. citeturn26view0turn17view0

**Why useful:** It identifies industrial technology firms and AI-adopting manufacturers that may never foreground “AI” on top pages.  
**AI-ecosystem relation:** Strong secondary signal for industrial AI participation, especially when paired with computer vision, optimization, edge, sensor, or robotics language.  
**Typical trigger activities:** PLC/SCADA integration; line automation; process control; MES; condition monitoring; predictive maintenance; machine monitoring; quality inspection; edge control.

**English keywords:** automation, industrial automation, process automation, control systems, industrial control, plc, scada, hmi, mes, dcs, process control, condition monitoring, predictive maintenance, industrial iot, iiot, cyber-physical systems, edge computing, quality control, process optimisation, machine control, production monitoring.  
**English multi-word phrases:** industrial automation, process control, programmable logic controller, industrial control system, manufacturing execution system, condition monitoring, predictive maintenance, industrial internet of things, cyber-physical system, process optimisation, quality inspection, machine monitoring.  
**English boost terms:** plc, scada, mes, hmi, dcs, opc ua, industrial ethernet, edge ai, condition monitoring, predictive maintenance, process optimisation.  
**German keywords:** automatisierung, industrieautomation, automatisierungstechnik, prozessautomatisierung, steuerungstechnik, leittechnik, sps, scada, hmi, mes, dcs, prozessleitsystem, zustandsüberwachung, vorausschauende wartung, iiot, industrielle iot, cyber-physische systeme, edge computing, qualitätskontrolle, produktionsüberwachung.  
**German multi-word phrases:** industrielle automatisierung, prozesssteuerung, speicherprogrammierbare steuerung, industrielles steuerungssystem, manufacturing execution system, zustandsüberwachung, vorausschauende wartung, industrielles internet der dinge, cyber-physisches system, prozessoptimierung, qualitätsinspektion, maschinenüberwachung.  
**German boost terms:** sps, scada, mes, hmi, opc ua, edge ai, zustandsüberwachung, prognosewartung, prozessoptimierung, industrielle ethernet.  
**Node-specific negatives:** workflow automation, office automation, marketing automation, hr automation, automated email, automated billing, control in finance/compliance.

**robotics_and_autonomous_systems**  
This node should be separate from general automation because professional practice and standards distinguish industrial robots, service robots, AGVs/mobile robots, and broader autonomous systems. Austria’s AI strategy explicitly mentions robotics and autonomous systems as core strength areas, and IFR definitions provide a clean operational distinction for industrial and service robotics. citeturn15view2turn25view6turn25view7

**Why useful:** It identifies firms that build or integrate physical intelligent systems, which are especially relevant for industrial AI ecosystems.  
**AI-ecosystem relation:** Very strong signal when paired with vision, planning, navigation, control, sensing, or learning language.  
**Typical trigger activities:** cobots; robot cells; AMRs/AGVs; warehouse robotics; navigation; machine-vision-guided manipulation; autonomous drone operation; robot programming.

**English keywords:** robotics, robot, robotic, cobot, collaborative robot, manipulator, gripper, amr, autonomous mobile robot, agv, navigation, autonomy, autonomous system, drone, unmanned, motion planning, robot control, pick and place, machine vision, robot cell.  
**English multi-word phrases:** industrial robot, service robot, autonomous mobile robot, automated guided vehicle, collaborative robot, robot control, motion planning, machine vision guided, warehouse robotics, autonomous drone, robotic inspection, robotic manipulation.  
**English boost terms:** cobot, amr, agv, navigation, autonomy, motion planning, perception, robot control, machine vision, manipulator, gripper.  
**German keywords:** robotik, roboter, robotisch, cobot, kollaborativer roboter, manipulator, greifer, amr, autonomer mobiler roboter, fahrerloses transportsystem, ftf, navigation, autonomie, autonomes system, drohne, bewegungsplanung, robotiksteuerung, maschinelles sehen.  
**German multi-word phrases:** industrieroboter, serviceroboter, autonomer mobiler roboter, fahrerloses transportsystem, kollaborativer roboter, robotiksteuerung, bewegungsplanung, bildverarbeitungsgeführt, lagerrobotik, autonome drohne, robotische inspektion, robotische manipulation.  
**German boost terms:** cobot, amr, ftf, navigation, autonomie, bewegungsplanung, wahrnehmung, robotiksteuerung, bildverarbeitung, manipulator, greifer.  
**Node-specific negatives:** robotic process automation, software bot, chat bot, toy robot, educational robot kits, “robotic” as metaphor.

**digital_platforms**  
Digital platforms should be modeled as a separate firm type because the digital-platform literature explicitly distinguishes them from generic software firms, and AI service platforms are a recognizable subtype in the AI-platform literature. This matters for web mining because “platform” is common language, but only a subset of firms are actual multi-sided or ecosystem-orchestrating platform firms. citeturn25view4turn25view5turn11search0

**Why useful:** It distinguishes software vendors from firms whose core logic is matchmaking, orchestration, or ecosystem enablement.  
**AI-ecosystem relation:** Medium-to-strong signal if the platform is explicitly AI-, data-, model-, or developer-platform oriented.  
**Typical trigger activities:** marketplace operation; booking/matching; API ecosystems; developer platforms; model hubs; data-sharing platforms; AI service platforms.

**English keywords:** platform, marketplace, ecosystem, network effects, matching, exchange, on-demand, multi-sided platform, digital platform, developer platform, api platform, ai platform, data platform, model hub, partner ecosystem.  
**English multi-word phrases:** digital platform, online marketplace, multi-sided platform, developer platform, api platform, ai service platform, data exchange platform, model marketplace, partner ecosystem, platform business model.  
**English boost terms:** marketplace, matching, seller, buyer, provider, partner ecosystem, developer, api, model hub, platform business.  
**German keywords:** plattform, marktplatz, ökösystem, ecosystem, matching, vermittlung, on-demand, mehrseitige plattform, digitale plattform, entwicklerplattform, api-plattform, ki-plattform, datenplattform, modellhub, partnerökosystem.  
**German multi-word phrases:** digitale plattform, online-marktplatz, mehrseitige plattform, entwicklerplattform, api-plattform, ki-service-plattform, daten-austauschplattform, modellmarktplatz, partnerökosystem, plattformgeschäftsmodell.  
**German boost terms:** marktplatz, vermittlung, anbieter, nachfrager, partnerökosystem, entwickler, api, modellhub, plattformgeschäft.  
**Node-specific negatives:** platform as generic product label, communication platform, learning platform, business platform, technology platform without ecosystem or multi-sided evidence.

**engineering_and_embedded_technology**  
This node captures the Austrian and European deep-tech layer that often sits adjacent to AI rather than under explicit AI branding. Austria’s strategy highlights embedded systems and strengths in automotive and mechanical engineering, while ATI includes connectivity, IoT, micro- and nanoelectronics, photonics, mobility, and related hardware-technical families. SFIA also treats real-time and embedded systems as core software-engineering competencies. This is the most useful node for identifying firms that are likely to become AI-relevant through sensing, control, edge, or simulation capability. citeturn15view2turn17view0turn25view0

**Why useful:** It captures firms with strong technical depth that may not yet use explicit AI labels but sit close to robotics, industrial AI, or edge AI.  
**AI-ecosystem relation:** Important adjacency signal, especially when combined with automation, robotics, data, or AI-engineering language.  
**Typical trigger activities:** embedded-system design; sensor systems; signal processing; electronics; edge devices; simulation; digital twins; test benches; mechatronics; control hardware.

**English keywords:** engineering, mechatronics, embedded systems, embedded software, real-time systems, sensors, sensing, sensor fusion, electronics, microelectronics, semiconductors, photonics, signal processing, edge device, connectivity, iot, simulation, digital twin, cad, cae, fem, hpc, test bench.  
**English multi-word phrases:** embedded systems, real-time systems, sensor fusion, signal processing, electronic design, edge device, digital twin, simulation software, hardware-software co-design, mechatronic systems, industrial connectivity, high-performance computing.  
**English boost terms:** embedded, real-time, sensor fusion, digital twin, simulation, edge, electronics, signal processing, hpc, mechatronics.  
**German keywords:** ingenieurwesen, mechatronik, eingebettete systeme, embedded software, echtzeitsysteme, sensorik, sensoren, sensorfusion, elektronik, mikroelektronik, halbleiter, photonik, signalverarbeitung, edge gerät, konnektivität, iot, simulation, digitaler zwilling, cad, cae, fem, hpc, prüfstand.  
**German multi-word phrases:** eingebettete systeme, echtzeitsysteme, sensorfusion, signalverarbeitung, elektronische entwicklung, edge gerät, digitaler zwilling, simulationssoftware, hardware-software co-design, mechatronische systeme, industrielle konnektivität, high-performance computing.  
**German boost terms:** eingebettet, echtzeit, sensorfusion, digitaler zwilling, simulation, edge, elektronik, signalverarbeitung, hpc, mechatronik.  
**Node-specific negatives:** engineering services generic, consulting engineering generic, technology solutions, design only, construction engineering, civil engineering.

## False positives and ambiguity controls

The most important negative-control rule is this: **do not let “digital,” “smart,” “automated,” or “analytics” count as AI by default**. Under the Commission’s AI-system-definition guidance, systems based on human-defined rules alone, basic data processing, descriptive analysis and visualization, classical heuristics, or simple prediction systems can fall **outside** the AI-system definition even if they sound computationally sophisticated. That distinction is extremely useful for your scorer because company websites regularly blur the line between advanced analytics, software automation, optimization, and AI. citeturn24view1turn24view2turn24view4turn23view0

The website-mining literature gives the second critical warning: companies selectively publish positive information, website depth is uneven, process innovation is underreported relative to product-facing innovation, and language differences matter. That means ambiguity control should happen at both **term level** and **aggregation level**: use negatives/downweights locally, and require repeated evidence across pages before assigning a strong technology label globally. citeturn28view0turn16view0

A practical control set is the following.

| Term or pattern | Recommended treatment | Why |
|---|---|---|
| smart, intelligent, innovative, future-ready, next-gen, AI-ready | **Downweight** | High marketing prevalence; weak technical specificity. |
| dashboard, reporting, data visualization, KPI, survey results, charts, statistics | **Negative for AI**, **allowed for data/analytics** | Commission guidance treats descriptive analysis/visualization as outside AI on its own. citeturn24view2turn24view4 |
| rule-based, rules engine, predefined rules, fixed rules, deterministic workflow | **Negative for AI** | Explicitly closer to traditional software than AI. citeturn24view1 |
| heuristic, classical heuristic, minimax, solver, optimization only, linear/logistic regression benchmark | **Negative or strong downweight for AI** | Commission guidance says simple optimization and classical heuristics can fall outside the AI definition. citeturn24view1turn24view4turn24view0 |
| automation, automated, workflow automation, process automation | **Context required** | Can indicate industrial automation, office workflow, or marketing ops. |
| robotic process automation, RPA, software bot | **Negative for robotics**, **neutral-to-positive for automation** | Not evidence of physical robotics. |
| platform, ecosystem | **Context required** | Very common generic label on company sites. |
| vision | **Context required** | Could mean company vision instead of computer vision. |
| learning, Lernen | **Context required** | Could mean education/training rather than machine learning. |
| control, Steuerung | **Context required** | Could mean management/governance rather than industrial control. |
| model, Modell | **Context required** | Could refer to business model, product model, or AI model. |
| cloud | **Downweight unless paired with software/API/deployment terms** | Generic modernization term; useful mainly as enabling stack evidence. |
| data-driven, datengetrieben | **Downweight** | Good soft evidence, weak specific evidence. |
| predictive | **Context required** | Stronger when paired with maintenance, analytics, diagnosis, demand, risk, or model. |
| autonomous, autonom | **Context required** | Useful in robotics/mobility; often vague elsewhere. |
| consulting-only language | **Downweight for producer labels** | “AI strategy,” “digital transformation,” or “innovation consulting” alone can signal advisory services rather than technical capability. |

For German-language scraping, a few ambiguity pairs deserve explicit handling. *Bildverarbeitung* is usually strong for computer vision, but *Vision* is not. *Lernen* should only score highly when paired with *maschinell*, *Modell*, *Algorithmus*, *KI*, or known ML terms. *Steuerung* should score strongly only near *SPS, Regelung, Leittechnik, Anlage, Maschine,* or *Prozess*. *Plattform* should only score strongly for digital-platform firms when paired with ecosystem or marketplace evidence such as *Marktplatz, Anbieter, Partner, API, Entwickler,* or two-sided role language. These compound-sensitive rules are particularly important in Austrian industrial websites. citeturn15view2turn17view0turn28view0

## BM25 query families and firm types

For scalability and explainability, I would not use one giant “technology” query. I would use a **query family design** in which each family targets a distinct evidence pattern, and then aggregate family scores into node scores. This fits the web-mining literature well because website indicators depend strongly on keyword design, and transparent knowledge-based features remain easier to maintain than opaque end-to-end classifiers. citeturn28view0turn15view4

A good minimum set is six query families.

**Family for AI producer/developer evidence**  
Purpose: identify firms that build or provide AI systems, models, or AI-enabled products.

```text
(AI core terms OR AI method terms)
AND
(build/develop/provide/platform/software/API/model/deploy/train)
BOOST
(model, inference, training, MLOps, computer vision, NLP, LLM, anomaly detection, recommendation)
NEGATIVE
(dashboard, reporting, predefined rules, heuristic, visualization only)
```

**Family for AI adopter/user evidence**  
Purpose: identify firms that use AI inside business processes without necessarily producing AI products.

```text
(AI core terms OR method terms)
AND
(use/apply/integrate/deploy/optimise/support)
AND
(use-case terms such as predictive maintenance, quality inspection, forecasting, planning, decision support)
BOOST
(operational verbs + sector/use-case pairs)
```

**Family for data-intensive capability**  
Purpose: separate data-platform and analytics firms from generic digital firms.

```text
(data engineering OR BI/analytics OR data platform terms)
AND
(platform/pipeline/warehouse/lakehouse/integration/streaming/decision support)
BOOST
(predictive/recommendation/real-time/feature store/governance)
DOWNWEIGHT
(reporting only/visualization only)
```

**Family for software productization**  
Purpose: identify software firms and the software substrate around AI.

```text
(software/SaaS/API/cloud/microservices/devops/embedded software)
AND
(product/platform/build/integration/deployment/release)
BOOST
(API, SaaS, SDK, cloud-native, CI/CD, Kubernetes)
```

**Family for industrial automation and control**  
Purpose: identify automation firms and industrial-AI contexts.

```text
(automation/control/PLC/SCADA/HMI/MES/IIoT/CPS)
AND
(machine/process/production/plant/line/system)
BOOST
(predictive maintenance, condition monitoring, process optimisation, edge)
NEGATIVE
(workflow automation/marketing automation/RPA-only for robotics)
```

**Family for robotics and autonomous systems**  
Purpose: identify physical intelligent systems.

```text
(robotics/cobot/AMR/AGV/navigation/autonomous/drone/manipulator)
AND
(control/vision/perception/planning/inspection/logistics)
BOOST
(machine vision, motion planning, warehouse robotics, manipulation)
NEGATIVE
(RPA/chatbot/software bot)
```

For multilingual robustness, every family should be implemented in **parallel EN/DE variants**, with acronyms and mixed-language variants included because Austrian corporate sites often mix them. In practice, that means storing, for each node, at least four lexical layers: **core nouns**, **multi-word phrases**, **boost verbs**, and **negative/downweight terms**. Phrase and proximity matching should matter more than raw unigram presence when possible. citeturn15view2turn28view0

The same families can be used to distinguish firm types. The distinction should be **multi-label and hierarchical**, not exclusive.

| Firm type | Strongest evidence pattern | Secondary evidence | Main confusion risk |
|---|---|---|---|
| **AI producers/developers** | AI method terms + build/provide/deploy/platform/software/model language | MLOps, APIs, engineers, R&D, benchmark, model lifecycle | generic consultants using AI buzzwords |
| **AI adopters/users** | AI terms + use/apply/integrate verbs + concrete business process or sector use case | case studies, operational improvement claims, process/quality/planning language | software vendors marketing AI features |
| **Software firms** | software/SaaS/API/cloud/devops/integration + product language | architecture, deployment, release, embedded software | platform firms and consultancies |
| **Automation firms** | PLC/SCADA/MES/HMI/control/process/plant language | IIoT, condition monitoring, systems integration | office/process automation vendors |
| **Data-intensive firms** | data platform/pipeline/warehouse/BI/analytics/governance language | predictive/real-time/decision-support modifiers | descriptive BI shops mislabeled as AI |
| **Digital platform firms** | marketplace/platform/ecosystem/matching/developer/API role language | partner ecosystem, seller/buyer or provider/user roles | firms that call any software a “platform” |
| **Engineering/technology firms** | embedded/sensors/electronics/mechatronics/simulation/digital twin language | real-time, signal processing, device/system design | generic engineering consultancies |

## Strongest signals for AI-ecosystem participation

If the operational goal is to identify **potential AI ecosystem participants**, the strongest signals should not be generic AI mentions. The best signals are **compound signals**: a specific AI method, paired with an engineering or productization verb, paired with a concrete output or use case. That matches OECD/EU definitional emphasis on inference and outputs, aligns with Austrian and European AI/Data/Robotics language, and is also the safest way to separate real technical capability from generic digital marketing. citeturn23view0turn24view5turn27view0turn15view2

In priority order, I would treat the following as the strongest evidence.

**Highest-confidence signals** are phrases like: *develop machine learning models; computer vision for quality inspection; NLP platform; predictive maintenance system; AI-powered defect detection; autonomous mobile robot; model deployment; MLOps pipeline; edge AI for industrial control; knowledge-based expert system; generative AI assistant for enterprise workflows.* These are strong because they combine method, action, and object. In industrial settings, predictive maintenance, quality assurance, and supply-chain/process optimization are especially strong because OECD’s manufacturing work identifies them as major AI-use domains. citeturn26view0turn15view0turn17view0

**Strong secondary signals** are AI-adjacent capabilities that repeatedly co-occur with AI adoption: data engineering, data platforms, software productization, APIs, cloud-native deployment, robotics, embedded systems, sensors, and industrial automation. OECD evidence shows AI use is concentrated in ICT and professional/scientific services, while the sectoral AI-intensity literature highlights IT services, telecommunications, media, scientific R&D, and computer/electronics manufacturing as comparatively AI-intensive. In the Austrian context, software development, data processing, embedded systems, robotics, autonomous systems, and Industry 4.0 language are especially informative. citeturn10search1turn10search0turn10search9turn15view2

**Useful but weaker signals** include generic cloud, BI, software integration, dashboards, digital transformation, and innovation language. These are often real technology signals, but by themselves they are much better at identifying general digitalization than AI participation. The Commission’s AI guidance is especially helpful here: descriptive dashboards, simple statistical systems, basic optimization, and heuristic systems should not be allowed to accumulate a high AI score unless stronger AI evidence appears elsewhere on the domain. citeturn24view0turn24view1turn24view2turn24view4

Operationally, I would assign **very high weight** to `artificial_intelligence`, **high weight** to `robotics_and_autonomous_systems` and `automation_and_control` when paired with AI/vision/planning terms, **medium-high weight** to `data_and_analytics` and `software_and_cloud`, and **medium weight** to `engineering_and_embedded_technology` and `digital_platforms`. I would also add a **cross-cutting boost layer** for productization and technical depth: terms such as *platform, API, SDK, deployment, integration, reference project, case study, R&D, research lab, competence center, engineers,* and their German equivalents often separate real technical capability from broad marketing pages. This weighting scheme is consistent with the website-mining evidence that text detail matters most, with Austrian AI-strategy language, and with OECD evidence on where AI adoption and capability concentrate. citeturn16view0turn15view2turn10search1turn26view1

Finally, because website content is biased, uneven, and subject to drift, the maintenance rule should be simple: refresh the lexicons quarterly, review false positives manually, and treat new term families semi-automatically before promoting them into the stable ontology. That recommendation follows directly from the innovation-indicator and website-classification literature, which stresses transparency, expert-readable features, weighting for coverage bias, and active maintenance of knowledge-based features over time. citeturn28view0turn15view4

The regulatory and ecosystem context around European AI is still evolving, so AI-Act-specific vocabulary and Austrian ecosystem labels should be reviewed periodically alongside the ontology. citeturn20view2turn20view0turn15view4

navlistRecent European AI policy and ecosystem reportingturn2news18,turn2news21,turn6news24