"""Read-only source loading and grounded, non-predictive supervision."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .company_modeling_common import canonical, digest, file_hash, host, normalized_body, tokens, has_phrase
from .grounding import ground_lexical_signal


def _domain_batches(domains, size=900):
    for start in range(0, len(domains), size):
        yield domains[start:start + size]


def _domain_evidence(connection, domains, extraction_version):
    """Read selected website statistics and successful rows in bounded SQL batches."""
    acquisition = {domain: {'acquired_records': 0, 'acquired_success': None,
                            'extraction_records': 0, 'extraction_success': 0,
                            'extraction_not_success': 0} for domain in domains}
    for batch in _domain_batches(domains):
        slots = ','.join('?' for _ in batch)
        for row in connection.execute(f'''SELECT domain,count(*) acquired_records,
            sum(CASE WHEN scrape_status='success' THEN 1 ELSE 0 END) acquired_success
            FROM scraped_pages WHERE domain IN ({slots}) GROUP BY domain''', batch):
            acquisition[row['domain']].update({k: v for k, v in dict(row).items() if k != 'domain'})
        for row in connection.execute(f'''SELECT s.domain,count(*) extraction_records,
            sum(CASE WHEN e.extraction_status='success' THEN 1 ELSE 0 END) extraction_success,
            sum(CASE WHEN e.extraction_status!='success' THEN 1 ELSE 0 END) extraction_not_success
            FROM extracted_pages e JOIN scraped_pages s ON e.scraped_page_id=s.scraped_page_id
            WHERE s.domain IN ({slots}) AND e.extraction_version=? GROUP BY s.domain''',
            (*batch, extraction_version)):
            acquisition[row['domain']].update({k: v or 0 for k, v in dict(row).items() if k != 'domain'})
    query = '''SELECT s.scraped_page_id,s.url,s.final_url,s.domain,s.title acquisition_title,s.language acquisition_language,
        s.meta_description,e.extracted_page_id,e.extraction_version,e.extraction_run_id,e.content_sha256 source_content_sha256,
        e.text_path,e.metadata_path,e.language extraction_language,p.title,p.headings,p.language feature_language,
        p.feature_version FROM scraped_pages s JOIN extracted_pages e ON e.scraped_page_id=s.scraped_page_id
        LEFT JOIN page_features p ON p.extracted_page_id=e.extracted_page_id
        WHERE s.domain IN ({slots}) AND e.extraction_status='success' AND e.extraction_version=?
        ORDER BY s.domain,s.scraped_page_id,e.extracted_page_id DESC'''

    def rows():
        for batch in _domain_batches(domains):
            slots = ','.join('?' for _ in batch)
            yield from connection.execute(query.format(slots=slots), (*batch, extraction_version))
    return acquisition, rows()


def load_sources(database, root, config, *, company_ids=None):
    """Load historical calibration sources, or unlabeled explicit company IDs."""
    companies, relations, pages, contents, labels, visibility, phrases = [], [], [], {}, [], [], []
    hashes, errors = {}, []
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        seen = set()
        if company_ids is not None:
            for cid in sorted(set(company_ids)):
                row = connection.execute(
                    'SELECT firm_name,normalized_domain FROM firms WHERE bvd_id=?', (cid,)
                ).fetchone()
                if row is None or not row['firm_name']:
                    raise ValueError(f'Unreconciled company {cid}')
                domain = host(row['normalized_domain'])
                companies.append({'bvd_id': cid, 'company_name': row['firm_name'], 'normalized_domain': domain})
                if domain:
                    relations.append({'bvd_id': cid, 'website_id': domain, 'relationship_source': 'firms.normalized_domain'})
                for extra in connection.execute('SELECT domain,normalized_url FROM firm_urls WHERE bvd_id=? AND is_primary=1', (cid,)):
                    d = host(extra['domain'])
                    if d and d == host(extra['normalized_url']) and d != domain:
                        relations.append({'bvd_id': cid, 'website_id': d, 'relationship_source': 'firm_urls.validated_primary'})
        for run_id in config['calibration_runs'] if company_ids is None else ():
            run = connection.execute('SELECT * FROM calibration_runs WHERE calibration_run_id=?', (run_id,)).fetchone()
            if run is None or run['status'] != 'completed':
                raise ValueError(f'Missing or incomplete frozen calibration run: {run_id}')
            hashes['run:' + run_id] = digest(canonical(dict(run)))
            rows = connection.execute('''SELECT r.company_id,r.label,r.status,r.lexical_signals_json,
                f.firm_name,f.normalized_domain FROM calibration_results r
                LEFT JOIN firms f ON f.bvd_id=r.company_id WHERE r.calibration_run_id=? ORDER BY r.company_id''', (run_id,)).fetchall()
            if not rows:
                raise ValueError(f'No calibration results: {run_id}')
            for row in rows:
                r = dict(row); cid = r['company_id']
                if cid in seen or not r['firm_name'] or r['status'] != 'success' or r['label'] not in ('KEEP', 'UNKNOWN', 'EXCLUDE'):
                    raise ValueError(f'Unreconciled calibration company {cid}')
                seen.add(cid)
                domain = host(r['normalized_domain'])
                companies.append({'bvd_id': cid, 'company_name': r['firm_name'], 'normalized_domain': domain})
                labels.append({'bvd_id': cid, 'label': r['label'], 'calibration_run_id': run_id, 'calibration_source': run['sample_id']})
                if domain:
                    relations.append({'bvd_id': cid, 'website_id': domain, 'relationship_source': 'firms.normalized_domain'})
                for extra in connection.execute('SELECT domain,normalized_url FROM firm_urls WHERE bvd_id=? AND is_primary=1', (cid,)):
                    d = host(extra['domain'])
                    if d and d == host(extra['normalized_url']) and d != domain:
                        relations.append({'bvd_id': cid, 'website_id': d, 'relationship_source': 'firm_urls.validated_primary'})
                package_path = root / config['package_directory'] / run['package_run_id'] / 'blind_inputs' / f'{cid}.json'
                package = json.loads(package_path.read_text(encoding='utf-8'))
                hashes[str(package_path.relative_to(root))] = file_hash(package_path)
                historical = package['pages']
                for p in historical:
                    visibility.append({'bvd_id': cid, 'scraped_page_id': p['scraped_page_id'],
                        'historical_content_status': p.get('content_status'),
                        'visible_text_hash': digest(normalized_body(p.get('content') or '')),
                        'visible_normalized_characters': len(normalized_body(p.get('content') or '')),
                        'visible_characters': len(p.get('content') or '')})
                lexical = json.loads(r['lexical_signals_json'] or '{}')
                for number, signal in enumerate(lexical.get('signals', [])):
                    grounded = ground_lexical_signal(signal, historical)
                    resolved = next((p for p in historical if p['scraped_page_id'] == grounded['resolved_scraped_page_id']), None)
                    eligible = bool(grounded['page_visible_to_model'] and grounded['phrase_grounded_normalized'] and resolved
                                    and has_phrase(tokens(resolved.get('content')), signal.get('phrase')))
                    phrases.append({'bvd_id': cid, 'signal_index': number, 'phrase': signal.get('phrase') or '',
                        'signal_type': signal.get('signal_type') or 'unspecified', 'calibration_run_id': run_id,
                        'eligible': eligible, 'rejection_reason': None if eligible else 'unresolved_not_visible_or_not_token_grounded',
                        'grounding_json': canonical(grounded), 'scraped_page_id': grounded['resolved_scraped_page_id']})
        companies.sort(key=lambda r: r['bvd_id'])
        domains = sorted({r['website_id'] for r in relations})
        selected_domains = set(domains)
        domain_rows = {host(r['domain']): dict(r) for r in connection.execute('SELECT domain,firm_count,representation_level FROM domains') if host(r['domain']) in selected_domains}
        registry_counts = {host(r[0]): r[1] for r in connection.execute('SELECT normalized_domain,count(*) FROM firms GROUP BY normalized_domain')}
        websites = []
        acquisition, evidence_rows = _domain_evidence(connection, domains, config['extraction_version'])
        for domain in domains:
            # Domain membership is explicit; redirect hosts are relationship features, not extra acquisition scope.
            stats = {k: v for k, v in acquisition[domain].items()}
            websites.append({'website_id': domain, 'mapped_company_count': max(registry_counts.get(domain, 0), domain_rows.get(domain, {}).get('firm_count') or 0),
                'representation_level': domain_rows.get(domain, {}).get('representation_level'), **stats})
        seen_pages = set()
        for record in evidence_rows:
            p = dict(record)
            domain = p['domain']
            if p['scraped_page_id'] in seen_pages:
                raise ValueError(f'Duplicate successful versioned extraction: {p["scraped_page_id"]}')
            seen_pages.add(p['scraped_page_id'])
            try:
                path = root / p['text_path']
                body = normalized_body(path.read_text(encoding='utf-8'))
                source_hash = file_hash(path)
                hashes[str(path)] = source_hash
            except (OSError, UnicodeError, TypeError) as exc:
                errors.append({'website_id': domain, 'scraped_page_id': p['scraped_page_id'], 'error': str(exc)})
                continue
            metadata = {}
            if (not p['title'] or not p['headings']) and p['metadata_path']:
                try:
                    metadata = json.loads((root / p['metadata_path']).read_text(encoding='utf-8'))
                    hashes[p['metadata_path']] = file_hash(root / p['metadata_path'])
                except (OSError, ValueError):
                    metadata = {}
            p['title_source'] = 'page_features' if p['title'] else 'metadata_or_acquisition'
            p['title'] = p['title'] or metadata.get('title') or p['acquisition_title'] or ''
            try:
                headings = json.loads(p['headings']) if p['headings'] else metadata.get('headings', [])
            except (ValueError, TypeError):
                headings = []
            p['headings'] = canonical(headings)
            p['language'] = str(p['feature_language'] or p['extraction_language'] or p['acquisition_language'] or 'missing').lower().split('-')[0].split('_')[0]
            p['language_source'] = next((k for k in ('feature_language', 'extraction_language', 'acquisition_language') if p[k]), 'missing')
            p['content_id'] = digest(body)
            p['usable'] = bool(body)
            p['source_file_hash'] = source_hash
            p['website_id'] = domain
            p['word_count'] = len(body.split())
            p['final_host'] = host(p['final_url'] or p['url'])
            contents.setdefault(p['content_id'], {'content_id': p['content_id'], 'body_text': body, 'word_count': len(body.split())})
            pages.append(p)
        for r in relations:
            w = next(w for w in websites if w['website_id'] == r['website_id'])
            r['shared_domain'] = w['mapped_company_count'] > 1
            r['attribution_unresolved'] = r['shared_domain']
        hashes['selected_source_rows'] = digest(canonical({'companies': companies, 'labels': labels, 'relations': relations, 'pages': pages, 'websites': websites, 'phrases': phrases}))
        return dict(companies=companies, relations=relations, pages=sorted(pages, key=lambda p: p['scraped_page_id']),
                    contents=contents, labels=labels, websites=websites, visibility=visibility, phrases=phrases,
                    source_hashes=hashes, errors=errors)
    finally:
        connection.close()
