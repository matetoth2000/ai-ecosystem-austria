"""Create/extend a frozen-frame pool or register one fresh trial; never run acquisition stages."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from .company_modeling_common import canonical, digest
from .acquisition_pool_sampling import (
    VERSION, DEFAULT_DEVELOPMENT, DEFAULT_SEED, make_frame, advance,
    member_rows, prefix_probability,
)


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')


def write_csv(path, rows):
    with path.open('x', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def selection_reason(row):
    return canonical({'method': VERSION, 'wave': row['wave'], 'batch_id': row['batch_id'],
                      'selection_order': row['selection_order']})


def expected_members(rows, seed):
    return [(r['company_id'], i, r['nace_code'], r['sector'], r['normalized_domain'], seed, selection_reason(r))
            for i, r in enumerate(rows, 1)]


def domain_projection(rows):
    domains = {}
    for i, row in enumerate(rows, 1):
        domains.setdefault(row['normalized_domain'], i)
    return sorted(domains.items())


def memberships(state):
    batches = defaultdict(list)
    for row in state['selected']:
        batches[row['batch_id']].append(row)
    return {state['pool_id']: state['selected'], **dict(sorted(batches.items()))}


def validate_registry(connection, state, config_hash):
    for sample_id, rows in memberships(state).items():
        sample = connection.execute('SELECT * FROM sample_sets WHERE sample_id=?', (sample_id,)).fetchone()
        if (not sample or sample['sample_unit'] != 'company' or sample['config_hash'] != config_hash
                or sample['config_id'] != 'company_acquisition_pool' or sample['config_version'] != VERSION):
            raise ValueError(f'Registry identity mismatch: {sample_id}')
        actual = [tuple(r) for r in connection.execute('''SELECT company_id,sample_rank,nace_code,
            enrichment_stratum,normalized_domain,sampling_seed,selection_reason
            FROM sample_companies WHERE sample_id=? ORDER BY sample_rank''', (sample_id,))]
        domains = [tuple(r) for r in connection.execute(
            'SELECT domain,sample_rank FROM sample_domains WHERE sample_id=? ORDER BY domain', (sample_id,))]
        if (actual != expected_members(rows, state['seed']) or domains != domain_projection(rows)
                or sample['sample_size'] != len(rows) or sample['domain_count'] != len(domains)
                or sample['sampling_seed'] != state['seed']):
            raise ValueError(f'Registry membership mismatch: {sample_id}')


def load_saved(connection, pool_id):
    row = connection.execute('SELECT * FROM sample_sets WHERE sample_id=?', (pool_id,)).fetchone()
    if not row:
        raise ValueError(f'Unknown pool: {pool_id}')
    try:
        metadata = json.loads(row['sample_description'])
        if metadata['kind'] != 'acquisition_pool' or metadata['version'] != VERSION:
            raise ValueError('Not a supported acquisition pool')
        directory = Path(metadata['artifact_directory'])
        frame = json.loads((directory/'frame.json').read_text())
        revision = directory/'revisions'/f'v{metadata["revision"]:06d}'
        state = json.loads((revision/'state.json').read_text())
        if row['source_csv'] != str(revision/'pool.csv') or metadata['revision'] != state['revision']:
            raise ValueError('Pool registry artifact pointer mismatch')
        if digest(canonical(frame)) != state['frame_hash'] or digest(canonical(state)) != metadata['state_hash']:
            raise ValueError('Pool artifact hash mismatch')
        if state['pool_id'] != pool_id or state['version'] != VERSION or state['seed'] != frame['seed']:
            raise ValueError('Pool identity mismatch')
        expected = None
        for wave in state['waves']:
            expected = advance(frame, pool_id, wave['start_order']+wave['size']-1, expected)
        if expected != state:
            raise ValueError('Pool selection history does not reproduce')
        config_hash = digest(canonical({'frame_hash': state['frame_hash'], 'policy': frame['policy']}))
        validate_registry(connection, state, config_hash)
        return directory, frame, state, config_hash
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Incomplete or invalid saved pool: {pool_id}') from exc


def write_revision(directory, frame, state):
    revision = directory/'revisions'/f'v{state["revision"]:06d}'
    revision.mkdir(parents=True, exist_ok=False)
    rows = member_rows(frame, state)
    write_json(revision/'state.json', state)
    write_csv(revision/'pool.csv', rows)
    batches = defaultdict(list)
    for row in rows:
        if row['wave'] == state['revision']:
            batches[row['batch_id']].append(row)
    for batch_id, values in batches.items():
        write_csv(revision/(batch_id+'.csv'), values)
    write_json(revision/'unused_by_sector.json', {
        s: [r['company_id'] for r in values[state['allocations'][s]:]] for s, values in frame['sector_lists'].items()})
    batch_counts = Counter(r['batch_id'] for r in rows)
    cumulative = 0; prefixes = []
    for batch_id, count in sorted(batch_counts.items()):
        cumulative += count
        prefixes.append({'through_batch': batch_id, 'companies': cumulative,
                         'sector_inclusion_probabilities': {
                             s: prefix_probability(state, frame['sector_populations'], s, cumulative)
                             for s in sorted(frame['sector_populations'])}})
    write_json(revision/'batch_prefix_probabilities.json', prefixes)
    shared = defaultdict(list)
    for row in rows:
        shared[row['normalized_domain']].append(row['company_id'])
    summary = {'pool_id': state['pool_id'], 'revision': state['revision'], 'companies': len(rows),
               'unique_domains': len(shared), 'batches': dict(sorted(batch_counts.items())),
               'sector_populations': frame['sector_populations'], 'sector_allocations': state['allocations'],
               'saturated_sectors': [s for s, n in state['allocations'].items() if n == frame['sector_populations'][s]],
               'sector_weights': {s: .8*n/frame['eligible_count'] + .2/len(frame['sector_populations'])
                                  for s, n in frame['sector_populations'].items()},
               'shared_domain_groups': {d: ids for d, ids in sorted(shared.items()) if len(ids) > 1},
               'exclusion_reason_counts': frame['exclusion_reason_counts'], 'seed': state['seed'],
               'frame_hash': state['frame_hash'],
               'probability_note': 'Legal-company design probabilities, not domain or label probabilities; conditional on predeclared extension sizes.'}
    write_json(revision/'audit.json', summary)
    return revision, summary


def register(connection, directory, revision, frame, state, previous, config_hash):
    previous_members = memberships(previous) if previous else {}
    now = datetime.now(timezone.utc).isoformat()
    for sample_id, rows in memberships(state).items():
        if sample_id != state['pool_id'] and sample_id in previous_members:
            continue  # Existing batch rows and their provenance are immutable.
        is_pool = sample_id == state['pool_id']
        csv_path = revision/('pool.csv' if is_pool else sample_id+'.csv')
        metadata = canonical({'kind': 'acquisition_pool' if is_pool else 'acquisition_batch',
                              'version': VERSION, 'pool_id': state['pool_id'], 'revision': state['revision'],
                              'artifact_directory': str(directory), 'state_hash': digest(canonical(state))})
        domains = domain_projection(rows)
        old_count = len(previous_members.get(sample_id, []))
        if previous and is_pool:
            connection.execute('''UPDATE sample_sets SET sample_size=?,domain_count=?,source_csv=?,sample_description=?
                WHERE sample_id=?''', (len(rows), len(domains), str(csv_path), metadata, sample_id))
        else:
            connection.execute('''INSERT INTO sample_sets(sample_id,sample_name,sample_description,source_csv,
                sampling_strategy,sampling_seed,sample_size,sample_unit,config_id,config_version,config_hash,domain_count,created_at)
                VALUES (?,?,?,?,?,?,?,'company',?,?,?,?,?)''',
                (sample_id, sample_id, metadata, str(csv_path), 'capped_webster_80_20_sector_pool', state['seed'],
                 len(rows), 'company_acquisition_pool', VERSION, config_hash, len(domains), now))
        connection.executemany('''INSERT INTO sample_companies(sample_id,company_id,sample_rank,nace_code,
            enrichment_stratum,normalized_domain,sampling_seed,selection_reason) VALUES (?,?,?,?,?,?,?,?)''',
            [(sample_id, *r) for r in expected_members(rows, state['seed'])[old_count:]])
        known = {d for d, _ in domain_projection(previous_members.get(sample_id, []))}
        connection.executemany('INSERT INTO sample_domains(sample_id,domain,sample_rank) VALUES (?,?,?)',
                               [(sample_id, d, rank) for d, rank in domains if d not in known])


def build_pool(mode, pool_id, target_size, *, seed=None, database=None, root=None,
               output_directory=None, development_directory=None):
    if mode not in ('create', 'extend'):
        raise ValueError('Mode must be create or extend')
    if seed is not None and type(seed) is not int:
        raise ValueError('Seed must be an integer')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', pool_id):
        raise ValueError('Pool ID must contain only letters, digits, underscores and hyphens')
    if mode == 'extend' and any(v is not None for v in (seed, output_directory, development_directory)):
        raise ValueError('Extension uses the saved seed, frame and artifact directory; they cannot be overridden')
    root = Path(root or find_project_root())
    database = Path(database or resolve_database_path(root)).resolve()
    # mode=rw refuses to silently create a new/empty source database.
    connection = sqlite3.connect(database.as_uri()+'?mode=rw', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        if mode == 'create':
            if connection.execute('SELECT 1 FROM sample_sets WHERE sample_id=?', (pool_id,)).fetchone():
                raise FileExistsError(f'Sample already exists: {pool_id}; use extend, never redraw')
            directory = Path(output_directory or root/'data/interim/calibration/acquisition_pools'/pool_id).resolve()
            if directory.exists():
                raise FileExistsError(f'Artifact directory already exists: {directory}')
            frame = make_frame(connection, development_directory or root/DEFAULT_DEVELOPMENT,
                               DEFAULT_SEED if seed is None else seed)
            previous = None
        else:
            directory, frame, previous, _ = load_saved(connection, pool_id)
        state = advance(frame, pool_id, target_size, previous)
        new_samples = set(memberships(state)) - (set(memberships(previous)) if previous else set())
        for sample_id in sorted(new_samples):
            if connection.execute('SELECT 1 FROM sample_sets WHERE sample_id=?', (sample_id,)).fetchone():
                raise FileExistsError(f'Batch/sample ID collision: {sample_id}')
        # No substitution if a frozen member disappears or its domain changes.
        for row in state['selected'][previous['target_size'] if previous else 0:]:
            current = connection.execute('''SELECT f.normalized_domain,COALESCE(d.is_excluded,0) excluded
                FROM firms f LEFT JOIN domains d ON d.domain=f.normalized_domain WHERE f.bvd_id=?''',
                (row['company_id'],)).fetchone()
            if current is None or (current[0] or '').strip().lower() != row['normalized_domain']:
                raise ValueError(f'Frozen member missing or domain changed: {row["company_id"]}')
            if current['excluded']:
                raise ValueError(f'Frozen member domain is now excluded; no replacement drawn: {row["company_id"]}')
        config_hash = digest(canonical({'frame_hash': state['frame_hash'], 'policy': frame['policy']}))
        if previous is None:
            directory.mkdir(parents=True, exist_ok=False)
            write_json(directory/'frame.json', frame)
        revision, summary = write_revision(directory, frame, state)
        register(connection, directory, revision, frame, state, previous, config_hash)
        validate_registry(connection, state, config_hash)
        connection.commit()
        return {**summary, 'artifacts': str(revision), 'first_batch': state['selected'][0]['batch_id']}
    except BaseException:
        connection.rollback()
        # Files created before an interrupted transaction are unregistered artifacts.
        # Never overwrite them or silently pretend the registry commit succeeded.
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('create', 'extend', 'trial'))
    parser.add_argument('--sample-id', help='Distinct ID for one fresh trial (trial mode only)')
    parser.add_argument('--dry-run', action='store_true', help='Read-only trial selection preview (trial mode only)')
    parser.add_argument('--pool-id', default='independent_validation_v1')
    parser.add_argument('--target-size', type=int, help='Default 1500 for create, 100 for trial; required for extend')
    parser.add_argument('--seed', type=int, help=f'Creation seed (default {DEFAULT_SEED}); required for trial; fixed on extension')
    parser.add_argument('--database', help='Existing initialized SQLite database')
    parser.add_argument('--output-directory', help='New artifact directory (create or trial only)')
    parser.add_argument('--development-directory', help='Frozen 649-company dataset (create or trial only)')
    args = parser.parse_args()
    if args.mode == 'trial':
        if args.sample_id is None or args.seed is None:
            parser.error('trial requires --sample-id and --seed')
        from .fresh_trial_registration import register_fresh_trial
        result = register_fresh_trial(args.sample_id, args.seed, database=args.database,
            output_directory=args.output_directory, development_directory=args.development_directory,
            target_size=args.target_size if args.target_size is not None else 100, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if args.sample_id is not None or args.dry_run:
        parser.error('--sample-id and --dry-run apply only to trial mode')
    if args.mode == 'extend' and args.target_size is None:
        parser.error('--target-size is required for extension')
    result = build_pool(args.mode, args.pool_id, 1500 if args.target_size is None else args.target_size,
                        seed=args.seed, database=args.database, output_directory=args.output_directory,
                        development_directory=args.development_directory)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
