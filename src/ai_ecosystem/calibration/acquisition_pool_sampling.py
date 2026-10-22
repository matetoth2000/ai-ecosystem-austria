"""Deterministic, house-monotone sampling for append-only acquisition pools."""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
import random
import re

import pandas as pd

from .company_modeling_common import canonical, digest, file_hash, host

VERSION = 'broad_sector_pool_v1'
BATCH_SIZE = 100
DEFAULT_SEED = 20261006
DEFAULT_DEVELOPMENT = 'data/interim/calibration/company_modeling_datasets/company_ai_relevance_v1'


def shuffled(values, seed, channel):
    result = list(values)
    random.Random(int(digest(f'{VERSION}:{seed}:{channel}'), 16)).shuffle(result)
    return result


def allocations(populations, target):
    """Capped Webster/Sainte-Lague seats for weights .8*N_h/N + .2/H.

    Fixed weights and sequential seats are house-monotone. Removing a full
    sector from subsequent seat competitions redistributes its remaining weight.
    Integer weights and rational priorities make rounding/ties platform-stable.
    """
    if type(target) is not int or target < 1:
        raise ValueError('Target size must be a positive integer')
    if not populations or any(type(n) is not int or n < 1 for n in populations.values()):
        raise ValueError('Sector populations must be positive integers')
    total, sectors = sum(populations.values()), sorted(populations)
    if target > total:
        raise ValueError(f'Target {target} exceeds frozen eligible capacity {total}')
    weights = {s: 4 * len(sectors) * populations[s] + total for s in sectors}
    counts = dict.fromkeys(sectors, 0)
    for _ in range(target):
        available = [s for s in sectors if counts[s] < populations[s]]
        winner = min(available, key=lambda s: (-Fraction(weights[s], 2 * counts[s] + 1), s))
        counts[winner] += 1
    return counts


def development_inventory(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    tables, hashes = {}, {}
    for name, columns in [('companies', ['bvd_id', 'normalized_domain']),
                          ('websites', ['website_id']), ('pages', ['final_host'])]:
        path = directory/(name+'.parquet')
        hashes[name] = file_hash(path)
        if hashes[name] != manifest['tables'][name]['file_hash']:
            raise ValueError(f'Development inventory hash mismatch: {name}')
        tables[name] = pd.read_parquet(path, columns=columns)
    ids = set(tables['companies'].bvd_id)
    if len(ids) != 649 or len(tables['companies']) != 649:
        raise ValueError('Expected all 649 frozen development companies')
    hosts = set(tables['companies'].normalized_domain.dropna())
    hosts.update(tables['websites'].website_id.dropna())
    hosts.update(tables['pages'].final_host.dropna())
    return ids, {host(d) for d in hosts if host(d)}, hashes


def make_frame(connection, development_directory, seed):
    dev_ids, dev_hosts, hashes = development_inventory(development_directory)
    urls = defaultdict(set)
    for row in connection.execute('SELECT bvd_id,domain FROM firm_urls'):
        if row['domain']:
            urls[row['bvd_id']].add(host(row['domain']))
    rows = list(connection.execute('''SELECT f.bvd_id,f.normalized_domain,f.nace_code,
        COALESCE(d.is_excluded,0) excluded FROM firms f
        LEFT JOIN domains d ON d.domain=f.normalized_domain ORDER BY f.bvd_id'''))
    for row in rows:
        if row['bvd_id'] in dev_ids:
            dev_hosts.update(urls[row['bvd_id']])
            if host(row['normalized_domain']):
                dev_hosts.add(host(row['normalized_domain']))
    dev_hosts.discard('')
    # Same conservative host-family pre-screen as the readiness audit; no names,
    # industry meanings, scores, labels or acquisition outcomes enter eligibility.
    parents = {'.'.join(d.split('.')[i:]) for d in dev_hosts for i in range(1, len(d.split('.'))-1)}
    def overlaps(domain):
        return domain in dev_hosts or domain in parents or any(
            '.'.join(domain.split('.')[i:]) in dev_hosts for i in range(1, len(domain.split('.'))-1))
    eligible, exclusions = [], []
    for row in rows:
        cid = row['bvd_id']
        domain = (row['normalized_domain'] or '').strip().lower()
        reasons = []
        if not cid or not str(cid).strip(): reasons.append('missing_company_id')
        if not domain: reasons.append('missing_normalized_domain')
        if row['excluded']: reasons.append('marked_excluded_domain')
        if cid in dev_ids: reasons.append('development_company')
        if any(overlaps(d) for d in {host(domain)} | urls[cid] if d):
            reasons.append('development_website_family')
        if reasons:
            exclusions.append({'company_id': cid, 'normalized_domain': domain, 'reasons': reasons})
            continue
        code = row['nace_code'] or ''
        match = re.match(r'^([A-U])(?:[0-9]|$)', code.strip().upper())
        eligible.append({'company_id': cid, 'normalized_domain': domain, 'nace_code': code,
                         'sector': match.group(1) if match else 'MISSING'})
    grouped = defaultdict(list)
    for row in eligible:
        grouped[row['sector']].append(row)
    lists = {s: shuffled(values, seed, 'sector:'+s) for s, values in sorted(grouped.items())}
    return {'version': VERSION, 'seed': seed, 'eligible_frame_hash': digest(canonical(eligible)),
            'eligible_count': len(eligible), 'unique_domains': len({r['normalized_domain'] for r in eligible}),
            'sector_populations': {s: len(v) for s, v in lists.items()}, 'sector_lists': lists,
            'development_company_ids': sorted(dev_ids), 'development_hosts': sorted(dev_hosts),
            'development_source_hashes': hashes, 'exclusions': exclusions,
            'exclusion_reason_counts': dict(sorted(Counter(r for e in exclusions for r in e['reasons']).items())),
            'policy': {'proportional_weight': '4/5', 'equal_sector_weight': '1/5',
                       'allocator': 'capped_webster_highest_averages', 'tie_break': 'sector_ascending',
                       'batch_size': BATCH_SIZE, 'randomization': 'sha256_seeded_python_random_shuffle_v1'}}


def advance(frame, pool_id, target, previous=None):
    counts = allocations(frame['sector_populations'], target)
    if previous and target <= previous['target_size']:
        raise ValueError('Extension target must be larger than the current pool')
    old = previous['allocations'] if previous else dict.fromkeys(counts, 0)
    revision = previous['revision'] + 1 if previous else 1
    added = []
    for sector in sorted(counts):
        if counts[sector] < old[sector]:
            raise ValueError('Non-monotone allocation')
        added.extend(frame['sector_lists'][sector][old[sector]:counts[sector]])
    old_size = previous['target_size'] if previous else 0
    added = shuffled(added, frame['seed'], f'wave:{old_size}:{target}')
    selected = list(previous['selected']) if previous else []
    last_batch = max((r['batch_number'] for r in selected), default=0)
    for offset, row in enumerate(added):
        batch = last_batch + offset // BATCH_SIZE + 1
        selected.append({**row, 'selection_order': old_size + offset + 1,
                         'batch_number': batch, 'batch_id': f'{pool_id}_batch_{batch:04d}',
                         'batch_rank': offset % BATCH_SIZE + 1, 'wave': revision})
    wave = {'revision': revision, 'start_order': old_size+1, 'size': len(added),
            'sector_additions': {s: counts[s]-old[s] for s in counts}}
    return {'version': VERSION, 'pool_id': pool_id, 'seed': frame['seed'], 'revision': revision,
            'frame_hash': digest(canonical(frame)), 'target_size': target, 'allocations': counts,
            'selected': selected, 'waves': (previous['waves'] if previous else []) + [wave]}


def prefix_probability(state, populations, sector, prefix_size):
    """Unconditional company inclusion in the first m ordered pool positions."""
    probability = Fraction(0)
    for wave in state['waves']:
        covered = max(0, min(wave['size'], prefix_size - wave['start_order'] + 1))
        probability += Fraction(wave['sector_additions'][sector], populations[sector]) * Fraction(covered, wave['size'])
    return float(probability)


def member_rows(frame, state):
    populations = frame['sector_populations']
    waves = {w['revision']: w for w in state['waves']}
    sizes = Counter(r['batch_id'] for r in state['selected'])
    rows = []
    for row in state['selected']:
        s = row['sector']; wave = waves[row['wave']]
        rows.append({**row, 'pool_inclusion_probability': state['allocations'][s]/populations[s],
                     'batch_inclusion_probability': wave['sector_additions'][s]/populations[s] * sizes[row['batch_id']]/wave['size'],
                     'seed': frame['seed'], 'sector_population': populations[s]})
    return rows
