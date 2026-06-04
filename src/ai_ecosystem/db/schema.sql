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

CREATE TABLE IF NOT EXISTS scraped_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_page_id INTEGER,
    url TEXT NOT NULL,
    domain TEXT,
    http_status INTEGER,
    content_type TEXT,
    fetched_at TEXT,
    html_path TEXT,
    markdown_path TEXT,
    scrape_status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    title TEXT,
    language TEXT,
    word_count INTEGER,
    FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id)
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
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(id)
);

CREATE TABLE IF NOT EXISTS markdown_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_page_id INTEGER NOT NULL,
    chunking_version TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    token_count INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scraped_page_id) REFERENCES scraped_pages(id),
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
