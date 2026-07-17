PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS firms (
    bvd_id TEXT PRIMARY KEY,
    mark TEXT,
    firm_name TEXT,
    postal_code TEXT,
    city TEXT,
    nace_code TEXT,
    raw_url TEXT,
    normalized_url TEXT,
    normalized_domain TEXT,
    email TEXT,
    raw_source_file TEXT,
    imported_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS firm_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvd_id TEXT NOT NULL,
    raw_url TEXT,
    normalized_url TEXT,
    domain TEXT,
    is_primary INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bvd_id) REFERENCES firms(bvd_id),
    UNIQUE(bvd_id, normalized_url)
);

CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    notes TEXT,
    firm_count INTEGER,
    representation_level TEXT,
    domain_category TEXT,
    is_excluded INTEGER DEFAULT 0,
    exclusion_reason TEXT,
    primary_bvd_id TEXT,
    primary_firm_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE,
    started_at TEXT,
    completed_at TEXT,
    domains_attempted INTEGER,
    domains_succeeded INTEGER,
    domains_failed INTEGER,
    candidate_urls_discovered INTEGER,
    candidate_urls_retained INTEGER,
    max_workers INTEGER,
    duration_seconds REAL,
    domains_per_second REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS domain_discovery_status (
    domain TEXT PRIMARY KEY,
    homepage_status TEXT,
    robots_status TEXT,
    sitemap_status TEXT,
    last_discovery_attempt TEXT,
    last_successful_discovery TEXT,
    discovery_run_id TEXT,
    discovery_error TEXT,
    FOREIGN KEY (domain) REFERENCES domains(domain)
);

CREATE TABLE IF NOT EXISTS sample_sets (
    sample_id TEXT PRIMARY KEY,
    sample_name TEXT NOT NULL,
    sample_description TEXT,
    source_csv TEXT,
    sampling_strategy TEXT,
    sampling_seed INTEGER,
    sample_size INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sample_domains (
    sample_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    sample_rank INTEGER,
    FOREIGN KEY (sample_id) REFERENCES sample_sets(sample_id),
    UNIQUE(sample_id, domain)
);

CREATE TABLE IF NOT EXISTS candidate_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    discovery_method TEXT,
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    page_type_guess TEXT,
    page_title TEXT,
    normalized_url TEXT,
    source_domain TEXT,
    url_depth INTEGER,
    discovered_from_url TEXT,
    last_discovered_at TEXT,
    discovery_run_id TEXT,
    FOREIGN KEY (domain) REFERENCES domains(domain),
    UNIQUE(domain, normalized_url)
);

CREATE TABLE IF NOT EXISTS discovered_pages (
    discovered_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    discovery_method TEXT,
    page_type_guess TEXT,
    priority INTEGER,
    url_depth INTEGER,
    source_domain TEXT,
    discovered_from_url TEXT,
    retained_at_discovery INTEGER DEFAULT 0,
    discard_reason TEXT,
    discovery_run_id TEXT NOT NULL DEFAULT 'historical_backfill',
    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (domain) REFERENCES domains(domain)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_discovered_pages_run_domain_url_method
ON discovered_pages(discovery_run_id, domain, normalized_url, discovery_method);

CREATE INDEX IF NOT EXISTS idx_discovered_pages_domain_url
ON discovered_pages(domain, normalized_url);

CREATE INDEX IF NOT EXISTS idx_discovered_pages_run_retained
ON discovered_pages(discovery_run_id, retained_at_discovery);

CREATE INDEX IF NOT EXISTS idx_discovered_pages_domain_run
ON discovered_pages(domain, discovery_run_id);

CREATE TABLE IF NOT EXISTS url_retention_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    sample_id TEXT,
    domain TEXT NOT NULL,
    url TEXT,
    normalized_url TEXT,
    discovery_method TEXT,
    page_type_guess TEXT,
    priority INTEGER,
    decision TEXT NOT NULL,
    discard_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scraped_pages (
    scraped_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_page_id INTEGER,
    discovered_page_id INTEGER,
    url TEXT NOT NULL,
    final_url TEXT,
    domain TEXT,
    http_status INTEGER,
    content_type TEXT,
    content_length INTEGER,
    fetched_at TEXT,
    html_path TEXT,
    scrape_status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    title TEXT,
    meta_description TEXT,
    canonical_url TEXT,
    language TEXT,
    word_count_estimate INTEGER,
    redirect_count INTEGER,
    retained_at_discovery INTEGER,
    acquisition_run_id TEXT,
    FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id),
    FOREIGN KEY (discovered_page_id) REFERENCES discovered_pages(discovered_page_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scraped_pages_candidate_page_id
ON scraped_pages(candidate_page_id);

CREATE TABLE IF NOT EXISTS acquisition_runs (
    acquisition_run_id TEXT PRIMARY KEY,
    sample_id TEXT,
    fetch_scope TEXT,
    started_at TEXT,
    completed_at TEXT,
    pages_attempted INTEGER,
    pages_fetched INTEGER,
    pages_failed INTEGER,
    success_rate REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS retention_validation_runs (
    run_id TEXT PRIMARY KEY,
    sample_id TEXT,
    sample_type TEXT,
    model_name TEXT,
    prompt_version TEXT,
    pages_selected INTEGER,
    pages_completed INTEGER,
    pages_failed INTEGER,
    estimated_input_chars INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    started_at TEXT,
    completed_at TEXT,
    status TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_retention_validation_runs_status_started
ON retention_validation_runs(status, started_at);

CREATE TABLE IF NOT EXISTS retention_validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id TEXT,
    validation_run_id TEXT,
    scraped_page_id INTEGER,
    candidate_page_id INTEGER,
    domain TEXT,
    url TEXT,
    retained_at_discovery INTEGER,
    page_type_guess TEXT,
    prompt_version TEXT,
    model_name TEXT,
    input_chars INTEGER,
    output_json TEXT,
    firm_relevance_score REAL,
    firm_relevance_label TEXT,
    page_category_llm TEXT,
    keep_for_firm_profile TEXT,
    reason_short TEXT,
    evidence_snippet TEXT,
    language TEXT,
    ecosystem_relevance_score REAL,
    ecosystem_relevance_label TEXT,
    ai_ecosystem_role TEXT,
    ai_evidence_strength TEXT,
    ai_relevance_reason TEXT,
    run_status TEXT DEFAULT 'pending',
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id)
);

CREATE INDEX IF NOT EXISTS idx_retention_validation_results_scraped_page_id
ON retention_validation_results(scraped_page_id);

CREATE INDEX IF NOT EXISTS idx_retention_validation_results_candidate_page_id
ON retention_validation_results(candidate_page_id);

CREATE INDEX IF NOT EXISTS idx_retention_validation_results_domain
ON retention_validation_results(domain);

CREATE INDEX IF NOT EXISTS idx_retention_validation_results_status_created
ON retention_validation_results(run_status, created_at);

CREATE TABLE IF NOT EXISTS retention_validation_sample (
    sample_id TEXT NOT NULL,
    sample_version TEXT,
    sample_type TEXT NOT NULL,
    scraped_page_id INTEGER NOT NULL,
    discovered_page_id INTEGER NOT NULL,
    candidate_page_id INTEGER,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    retained_at_discovery INTEGER NOT NULL,
    page_type_guess TEXT,
    priority INTEGER,
    discovery_method TEXT,
    selection_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    FOREIGN KEY (discovered_page_id) REFERENCES discovered_pages(discovered_page_id),
    FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id),
    UNIQUE(sample_id, scraped_page_id)
);

CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_retained
ON retention_validation_sample(sample_id, retained_at_discovery);

CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_domain
ON retention_validation_sample(sample_id, domain);

CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_page_type
ON retention_validation_sample(sample_id, page_type_guess);

CREATE INDEX IF NOT EXISTS idx_retention_validation_sample_discovered_page_id
ON retention_validation_sample(discovered_page_id);

CREATE TABLE IF NOT EXISTS retention_validation_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id TEXT NOT NULL,
    scraped_page_id INTEGER NOT NULL,
    discovered_page_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    meta_description TEXT,
    page_type_guess TEXT,
    retained_at_discovery INTEGER NOT NULL,
    visible_text_length INTEGER,
    preview_char_count INTEGER,
    visible_text_preview TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    FOREIGN KEY (discovered_page_id) REFERENCES discovered_pages(discovered_page_id),
    UNIQUE(sample_id, scraped_page_id)
);

CREATE INDEX IF NOT EXISTS idx_retention_validation_inputs_sample_id
ON retention_validation_inputs(sample_id);

CREATE INDEX IF NOT EXISTS idx_retention_validation_inputs_discovered_page_id
ON retention_validation_inputs(discovered_page_id);

CREATE TABLE IF NOT EXISTS extracted_pages (
    extracted_page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_page_id INTEGER NOT NULL,
    extraction_version TEXT NOT NULL,
    extraction_status TEXT DEFAULT 'pending',
    markdown_path TEXT,
    text_path TEXT,
    metadata_path TEXT,
    language TEXT,
    extracted_at TEXT,
    content_sha256 TEXT,
    extraction_run_id TEXT,
    extraction_started_at TEXT,
    extraction_completed_at TEXT,
    extraction_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(extraction_run_id),
    UNIQUE(scraped_page_id, extraction_version)
);

CREATE INDEX IF NOT EXISTS idx_extracted_pages_scraped_page_id
ON extracted_pages(scraped_page_id);

CREATE INDEX IF NOT EXISTS idx_extracted_pages_extraction_status
ON extracted_pages(extraction_status);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_run_id TEXT PRIMARY KEY,
    sample_id TEXT,
    extraction_version TEXT,
    started_at TEXT,
    finished_at TEXT,
    selected_pages INTEGER,
    processed_pages INTEGER,
    successful_pages INTEGER,
    skipped_pages INTEGER,
    failed_pages INTEGER,
    configuration_hash TEXT
);

CREATE TABLE IF NOT EXISTS page_features (
    extracted_page_id INTEGER PRIMARY KEY,
    title TEXT,
    word_count INTEGER,
    heading_count INTEGER,
    headings TEXT,
    heading_hierarchy TEXT,
    paragraph_count INTEGER,
    page_type_guess TEXT,
    language TEXT,
    markdown_path TEXT,
    text_path TEXT,
    feature_version TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extracted_page_id) REFERENCES extracted_pages(extracted_page_id)
);

CREATE TABLE IF NOT EXISTS page_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extracted_page_id INTEGER NOT NULL,
    source_url TEXT,
    target_url TEXT,
    normalized_target_url TEXT,
    target_domain TEXT,
    normalized_target_domain TEXT,
    anchor_text TEXT,
    anchor_word_count INTEGER,
    rel_attribute TEXT,
    internal_external TEXT,
    is_internal INTEGER,
    link_order INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extracted_page_id) REFERENCES extracted_pages(extracted_page_id)
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_base_id TEXT NOT NULL,
    knowledge_base_version TEXT NOT NULL,
    dimension TEXT NOT NULL,
    source_path TEXT,
    config_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(knowledge_base_id, knowledge_base_version)
);

CREATE TABLE IF NOT EXISTS scoring_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scoring_profile_id TEXT NOT NULL,
    scoring_profile_version TEXT NOT NULL,
    dimension_weights_json TEXT,
    field_weights_json TEXT,
    fusion_weights_json TEXT,
    config_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scoring_profile_id, scoring_profile_version)
);

CREATE TABLE IF NOT EXISTS scoring_runs (
    scoring_run_id TEXT PRIMARY KEY,
    scoring_profile_id TEXT,
    scoring_profile_version TEXT,
    sample_id TEXT,
    extraction_version TEXT,
    started_at TEXT,
    completed_at TEXT,
    pages_selected INTEGER,
    pages_processed INTEGER,
    pages_scored INTEGER,
    pages_skipped INTEGER,
    pages_failed INTEGER,
    runtime_seconds REAL,
    pages_per_second REAL,
    status TEXT,
    configuration_hash TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS page_scores (
    page_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    extracted_page_id INTEGER NOT NULL,
    scraped_page_id INTEGER,
    domain TEXT,
    url TEXT,
    scoring_run_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    extraction_version TEXT,
    query_score REAL,
    keyword_score REAL,
    boost_score REAL,
    negative_score REAL,
    final_score REAL,
    top_queries_json TEXT,
    top_keywords_json TEXT,
    top_boost_terms_json TEXT,
    top_negative_terms_json TEXT,
    knowledge_base_id TEXT,
    knowledge_base_version TEXT,
    scoring_profile_id TEXT,
    scoring_profile_version TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extracted_page_id) REFERENCES extracted_pages(extracted_page_id),
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    FOREIGN KEY (scoring_run_id) REFERENCES scoring_runs(scoring_run_id),
    UNIQUE(scoring_run_id, extracted_page_id, dimension)
);

CREATE TABLE IF NOT EXISTS external_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_page_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    target_domain TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    target_bvd_id TEXT,
    edge_type TEXT DEFAULT 'hyperlink',
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id)
);

CREATE TABLE IF NOT EXISTS markdown_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_page_id INTEGER NOT NULL,
    chunking_version TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    token_count INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(scraped_page_id),
    UNIQUE(scraped_page_id, chunking_version, chunk_index)
);

CREATE TABLE IF NOT EXISTS retrieval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL,
    retrieval_version TEXT NOT NULL,
    score REAL,
    query_label TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chunk_id) REFERENCES markdown_chunks(id),
    UNIQUE(chunk_id, retrieval_version, query_label)
);

CREATE TABLE IF NOT EXISTS llm_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvd_id TEXT,
    prompt_version TEXT NOT NULL,
    model_name TEXT,
    model_run_version TEXT NOT NULL,
    input_ref TEXT,
    output_json TEXT,
    run_status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bvd_id) REFERENCES firms(bvd_id)
);
