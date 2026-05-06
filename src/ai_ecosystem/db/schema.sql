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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    FOREIGN KEY (domain) REFERENCES domains(domain),
    UNIQUE(domain, url)
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
    FOREIGN KEY (candidate_page_id) REFERENCES candidate_pages(id)
);

CREATE TABLE IF NOT EXISTS external_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_page_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    target_domain TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
