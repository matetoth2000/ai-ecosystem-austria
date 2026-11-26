"""Register one immutable fresh-acquisition trial; never execute web/model stages."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from ai_ecosystem.discovery.discovery_utils import get_sample_company_ids, get_sample_domains
from .acquisition_pool_sampling import DEFAULT_DEVELOPMENT, make_frame
from .build_acquisition_pool import write_csv, write_json
from .company_modeling_common import canonical, digest, file_hash, host

VERSION = 'fresh_acquisition_trial_v1'


def history_hosts(connection):
    """Include failed attempts, legacy rows and redirect/discovered alias hosts."""
    columns = {
        'domain_discovery_status': ('domain',),
        'discovered_pages': ('domain', 'source_domain', 'url'),
        'candidate_pages': ('domain', 'source_domain', 'url'),
        'scraped_pages': ('domain', 'url', 'final_url'),
        'url_retention_audit': ('domain',),
    }
    counts, seen = {}, set()
    for table, fields in columns.items():
        current = set()
        for row in connection.execute(f'SELECT DISTINCT {",".join(fields)} FROM {table}'):
            current.update(host(value) for value in row if value)
        current.discard(''); counts[table] = len(current); seen.update(current)
    return seen, counts


def trial_selection(connection, root, development_directory, seed):
    # Reuse normal domain eligibility and frozen development/company-family exclusions.
    # No allocation by sector, AI terms or model scores is performed for this trial.
    frame = make_frame(connection, development_directory, seed)
    used_ids = {r[0] for r in connection.execute('SELECT DISTINCT company_id FROM sample_companies')}
    used_ids.update(r[0] for r in connection.execute('SELECT DISTINCT company_id FROM calibration_results'))
    used_ids.update(r[0] for r in connection.execute('''SELECT DISTINCT f.bvd_id FROM firms f
        JOIN sample_domains s ON s.domain=f.normalized_domain'''))
    reserved_domains = {host(r[0]) for r in connection.execute('SELECT DISTINCT domain FROM sample_domains')}
    previous, history_counts = history_hosts(connection)
    html = root / 'data/raw/html'
    existing_html = {host(p.name) for p in html.iterdir() if p.is_dir()} if html.is_dir() else set()
    aliases = defaultdict(set)
    for cid, domain, url in connection.execute('SELECT bvd_id,domain,normalized_url FROM firm_urls WHERE is_primary=1'):
        if host(domain) and host(domain) == host(url):
            aliases[cid].add(host(domain))
    named = {r[0] for r in connection.execute("SELECT bvd_id FROM firms WHERE firm_name IS NOT NULL AND TRIM(firm_name)<>''")}
    domain_registry = {r[0] for r in connection.execute('SELECT domain FROM domains WHERE COALESCE(is_excluded,0)=0')}
    eligible, rejected = [], Counter()
    for row in (r for rows in frame['sector_lists'].values() for r in rows):
        cid, domain = row['company_id'], row['normalized_domain']
        reasons = []
        if cid in used_ids: reasons.append('previous_sample_or_calibration_company')
        if cid not in named: reasons.append('missing_company_name')
        if domain != host(domain) or '.' not in domain or domain not in domain_registry:
            reasons.append('not_a_registered_normalized_eligible_domain')
        if domain in reserved_domains: reasons.append('previously_registered_sample_domain')
        if domain in previous: reasons.append('prior_discovery_or_acquisition')
        if domain in existing_html: reasons.append('existing_raw_html_directory')
        if aliases[cid] - {domain}: reasons.append('additional_primary_website')
        if reasons:
            rejected.update(reasons)
        else:
            eligible.append(row)
    domains = defaultdict(list)
    for row in eligible:
        domains[row['normalized_domain']].append(row)
    def rank(channel, value):
        return digest(canonical([VERSION, seed, channel, value]))
    ordered = sorted(domains, key=lambda d: (rank('domain', d), d))
    if len(ordered) < 100:
        raise ValueError(f'Need 100 fresh distinct domains; found {len(ordered)}')
    selected = []
    for number, domain in enumerate(ordered[:100], 1):
        row = min(domains[domain], key=lambda r: (rank('company', [domain, r['company_id']]), r['company_id']))
        selected.append({**row, 'sample_rank': number,
                         'selection_reason': 'fresh eligible domain; seeded SHA-256 domain rank then company rank'})
    audit = {'version': VERSION, 'seed': seed, 'method': 'seeded_sha256_domain_then_company',
             'development_source_hashes': frame['development_source_hashes'],
             'base_exclusion_reason_counts': frame['exclusion_reason_counts'],
             'freshness_exclusion_reason_counts': dict(rejected),
             'history_unique_host_counts': history_counts,
             'excluded_registered_or_calibrated_company_ids_sha256': digest(canonical(sorted(used_ids))),
             'history_hosts_sha256': digest(canonical(sorted(previous))),
             'eligible_companies': len(eligible), 'eligible_domains': len(domains),
             'eligible_frame_sha256': digest(canonical(sorted(eligible, key=lambda r: r['company_id']))),
             'selection_sha256': digest(canonical(selected)),
             'note': 'One company per fresh domain; sector retained as metadata only. No scores/AI keyword selection. '
                     'Known database/alias history only; unseen live redirects cannot be ruled out before discovery.'}
    return selected, audit


def register_fresh_trial(sample_id, seed, *, database=None, root=None, output_directory=None,
                         development_directory=None, target_size=100, dry_run=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', sample_id):
        raise ValueError('Invalid distinct trial sample ID')
    if type(seed) is not int or seed < 0 or target_size != 100:
        raise ValueError('Trial requires an explicit nonnegative integer seed and exactly 100 companies')
    root = Path(root or find_project_root())
    database = Path(database or resolve_database_path(root)).resolve()
    development = Path(development_directory or root / DEFAULT_DEVELOPMENT)
    directory = Path(output_directory or root / 'data/interim/calibration/acquisition_trials' / sample_id).resolve()
    with sqlite3.connect(database.as_uri() + ('?mode=ro' if dry_run else '?mode=rw'), uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN' if dry_run else 'BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM sample_sets WHERE sample_id=?', (sample_id,)).fetchone():
            raise FileExistsError(f'Sample already exists; trial registration never overwrites: {sample_id}')
        if directory.exists():
            raise FileExistsError(f'Trial artifact directory already exists: {directory}')
        selected, audit = trial_selection(connection, root, development, seed)
        expected_companies = [r['company_id'] for r in selected]
        expected_domains = [r['normalized_domain'] for r in selected]
        assert len(set(expected_companies)) == len(set(expected_domains)) == 100
        summary = {'batch_id': sample_id, 'seed': seed, 'companies': 100, 'domains': 100,
                   'registered': not dry_run, 'artifacts': str(directory), **audit}
        if dry_run:
            return summary
        # New artifacts only. A rolled-back registration can leave an orphan directory;
        # neither a retry nor a distinct invocation silently overwrites it.
        directory.mkdir(parents=True, exist_ok=False)
        csv_path = directory / 'companies.csv'
        write_csv(csv_path, selected)
        write_json(directory / 'audit.json', {**audit, 'batch_id': sample_id,
                   'companies': 100, 'domains': 100,
                   'registration_note': 'Check sample registry; artifact presence alone does not prove commit.'})
        config_hash = digest(canonical({'version': VERSION, 'seed': seed,
                                        'selection_sha256': audit['selection_sha256']}))
        connection.execute('''INSERT INTO sample_sets(sample_id,sample_name,sample_description,source_csv,
            sampling_strategy,sampling_seed,sample_size,sample_unit,config_id,config_version,config_hash,domain_count,created_at)
            VALUES (?,?,?,?,?,?,100,'company','fresh_acquisition_trial','v1',?,100,?)''',
            (sample_id, sample_id, canonical({'kind': VERSION, 'audit': str(directory/'audit.json'),
             'audit_sha256': file_hash(directory/'audit.json'), 'selection_sha256': audit['selection_sha256']}),
             str(csv_path), audit['method'], seed, config_hash, datetime.now(timezone.utc).isoformat()))
        connection.executemany('''INSERT INTO sample_companies(sample_id,company_id,sample_rank,nace_code,
            enrichment_stratum,normalized_domain,sampling_seed,selection_reason) VALUES (?,?,?,?,?,?,?,?)''',
            [(sample_id, r['company_id'], r['sample_rank'], r['nace_code'], r['sector'],
              r['normalized_domain'], seed, r['selection_reason']) for r in selected])
        connection.executemany('INSERT INTO sample_domains(sample_id,domain,sample_rank) VALUES (?,?,?)',
                              [(sample_id, r['normalized_domain'], r['sample_rank']) for r in selected])
        if (get_sample_company_ids(connection, sample_id) != expected_companies
                or get_sample_domains(connection, sample_id) != expected_domains):
            raise ValueError('Downstream company/domain registry mismatch; registration rolled back')
        connection.commit()
        return summary
