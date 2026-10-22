"""Build company AI-relevance artifacts without fitting models or changing SQLite."""
from __future__ import annotations

import argparse
import json
import resource
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from .company_modeling_common import canonical, digest, load_config, file_hash
from .company_modeling_sources import load_sources
from .company_modeling_groups import leakage_groups, make_splits
from .company_modeling_features import build_features, feature_dictionary


def validate_dataset(source, splits, company_features):
    ids = [r['bvd_id'] for r in source['companies']]
    if len(ids) != len(set(ids)) or set(ids) != {r['bvd_id'] for r in source['labels']}:
        raise ValueError('Company/target membership is not reconciled')
    if source['errors']:
        raise ValueError(f'Required evidence input errors: {source["errors"][:5]}')
    group_splits = defaultdict(set); group_folds = defaultdict(set)
    by_id = {r['bvd_id']: r for r in splits}
    for r in splits:
        group_splits[r['leakage_group_id']].add(r['split'])
        group_folds[r['leakage_group_id']].add(r['development_fold'])
    if any(len(v) != 1 for v in list(group_splits.values())+list(group_folds.values())):
        raise ValueError('Leakage group crosses split/fold boundary')
    domain_splits = defaultdict(set)
    for r in source['relations']:
        domain_splits[r['website_id']].add(by_id[r['bvd_id']]['split'])
    if any(len(v) > 1 for v in domain_splits.values()):
        raise ValueError('Shared domain crosses splits')
    content_groups = defaultdict(set)
    domains = defaultdict(set)
    for r in source['relations']:
        domains[r['website_id']].add(by_id[r['bvd_id']]['leakage_group_id'])
    for p in source['pages']:
        if p['predictive_eligible']:
            content_groups[p['content_id']].update(domains[p['website_id']])
    if any(len(v) > 1 for v in content_groups.values()):
        raise ValueError('Predictive duplicate content crosses leakage groups')
    allowed = {'bvd_id'} | set(feature_dictionary(company_features))
    if any(set(r) != allowed for r in company_features):
        raise ValueError('Inconsistent predictive schema')
    forbidden = ('label', 'scorer', 'sampling', 'confidence', 'calibration', 'split', 'run_id', 'nace', 'rationale')
    if any(any(term in key for term in forbidden) for key in allowed):
        raise ValueError('Forbidden predictive field')


def build_dataset(config_path, output_directory=None, database=None, root=None):
    root = Path(root or find_project_root()); config = load_config(config_path)
    output = Path(output_directory) if output_directory else root / config['output_directory']
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite dataset: {output}')
    start = time.monotonic(); stages = {}
    source = load_sources(database or resolve_database_path(root), root, config)
    stages['source_loading'] = time.monotonic()-start
    print(f'Loaded {len(source["companies"])} companies, {len(source["pages"])} pages', flush=True)
    if source['errors']:
        raise ValueError(f'INPUT_ERROR: {source["errors"][:5]}')
    t = time.monotonic()
    groups, edges, quarantine, overlap_audit = leakage_groups(source, config)
    stages['overlap_grouping'] = time.monotonic()-t
    print(f'Grouped into {len(set(groups.values()))} leakage groups; quarantined {len(quarantine)} content items', flush=True)
    t = time.monotonic()
    cp, features, states = build_features(source, quarantine, config)
    splits = make_splits(source, groups, states, config)
    validate_dataset(source, splits, features)
    stages['features_and_validation'] = time.monotonic()-t
    split_map = {r['bvd_id']: r for r in splits}; state_map = {r['bvd_id']: r for r in states}
    labels = []
    for r in source['labels']:
        cid = r['bvd_id']
        labels.append({'bvd_id': cid, 'label': r['label'],
            'website_target_eligible': state_map[cid]['state'] == 'PROCESSABLE' and state_map[cid]['predictive_content_available']})
    companies = {r['bvd_id']: r for r in source['companies']}
    for r in source['phrases']:
        r.update(leakage_group_id=groups[r['bvd_id']], normalized_domain=companies[r['bvd_id']]['normalized_domain'])
    for cid, content in source['contents'].items():
        content['predictive_eligible'] = bool(content['body_text']) and cid not in quarantine
        content['quarantine_reason'] = 'cross_group_or_generic_content_overlap' if cid in quarantine else None
    current = defaultdict(set)
    for r in cp:
        current[r['bvd_id']].add(r['scraped_page_id'])
    visible = defaultdict(set)
    page_lookup = {p['scraped_page_id']: p for p in source['pages']}
    for r in source['visibility']:
        page = page_lookup.get(r['scraped_page_id'])
        body = source['contents'][page['content_id']]['body_text'] if page else None
        r['visible_text_matches_current_prefix'] = (
            digest(body[:r['visible_normalized_characters']]) == r['visible_text_hash'] if body is not None else None)
        if r['historical_content_status'] in ('complete', 'partially_truncated'):
            visible[r['bvd_id']].add(r['scraped_page_id'])
    visibility_gaps = [{'bvd_id': cid, 'current_usable_pages': len(current[cid]), 'historically_visible_pages': len(visible[cid]),
        'current_pages_not_model_visible': len(current[cid]-visible[cid]),
        'historical_pages_not_current': len(visible[cid]-current[cid])} for cid in companies]
    for w in source['websites']:
        ps = [p for p in source['pages'] if p['website_id'] == w['website_id']]
        w.update(successful_pages=len(ps), usable_pages=sum(p['usable'] for p in ps),
                 known_final_hosts=sorted({p['final_host'] for p in ps}),
                 unique_content_items=len({p['content_id'] for p in ps if p['usable']}))
    tables = {
        'companies': source['companies'], 'websites': source['websites'], 'pages': source['pages'],
        'contents': sorted(source['contents'].values(), key=lambda r: r['content_id']),
        'company_websites': source['relations'], 'company_page_features': cp, 'company_features': features,
        'targets': [r for r in labels if split_map[r['bvd_id']]['split'] != 'internal_holdout'],
        'evaluation_only/internal_holdout_targets': [r for r in labels if split_map[r['bvd_id']]['split'] == 'internal_holdout'],
        'operational_states': states, 'supervision/phrase_candidates': source['phrases'],
        'leakage_edges': edges, 'splits': splits,
        'audit/calibration_sources': [{k: v for k, v in r.items() if k != 'label'} for r in source['labels']],
        'audit/historical_visibility': source['visibility'], 'audit/visibility_gaps': visibility_gaps,
    }
    audit = {'companies': len(companies), 'labels': dict(Counter(r['label'] for r in labels)),
        'states': dict(Counter(r['state'] for r in states)), 'websites': len(source['websites']),
        'pages': len(source['pages']), 'usable_pages': sum(p['usable'] for p in source['pages']),
        'unique_contents': len(source['contents']), 'quarantined_contents': len(quarantine),
        'predictive_contents': sum(c['predictive_eligible'] for c in source['contents'].values()),
        'target_eligible': sum(r['website_target_eligible'] for r in labels),
        'label_source_counts': dict(Counter(r['calibration_source']+':'+r['label'] for r in source['labels'])),
        'split_counts': dict(Counter(r['split'] for r in splits)),
        'development_fold_counts': dict(Counter(str(r['development_fold']) for r in splits if r['split'] == 'development')),
        'split_label_counts': dict(Counter(split_map[r['bvd_id']]['split']+':'+r['label'] for r in labels)),
        'group_count': len(set(groups.values())), 'maximum_group_size': max(Counter(groups.values()).values()),
        'phrase_candidates': len(source['phrases']), 'eligible_phrases': sum(r['eligible'] for r in source['phrases']),
        'phrase_rejections': dict(Counter(r['rejection_reason'] for r in source['phrases'] if not r['eligible'])),
        'feature_count': len(features[0])-1, 'input_errors': source['errors'], 'unresolved_company_joins': 0,
        'domain_split_isolation': True, 'predictive_content_group_isolation': True,
        'companies_with_visibility_gap': sum(r['current_pages_not_model_visible'] > 0 for r in visibility_gaps),
        'historical_visible_text_prefix_mismatches': sum(r['visible_text_matches_current_prefix'] is False for r in source['visibility'] if r['historical_content_status'] in ('complete', 'partially_truncated')),
        'evidence_state_by_source': dict(Counter(r['calibration_source']+':'+state_map[r['bvd_id']]['state'] for r in source['labels'])),
        'missing_page_fields': {k: sum(not p.get(k) or p.get(k) in ('missing', '[]') for p in source['pages']) for k in ('title', 'headings', 'language', 'meta_description', 'feature_version')},
        'feature_missingness': {k: sum(r[k] is None for r in features) for k in features[0] if k != 'bvd_id'},
        'overlap': overlap_audit, 'limitations': ['Internal holdout has historical analysis exposure.',
            'Near-duplicate candidate discovery is approximate, verified edges are exact.',
            'Shared evidence and lexical name overlap do not establish company ownership.',
            'Company labels apply to historical model-visible evidence, not all current pages.']}
    output.mkdir(parents=True, exist_ok=False)
    manifests = {}
    for name, rows in tables.items():
        path = output / (name + '.parquet'); path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False, compression='zstd')
        manifests[name] = {'rows': len(rows), 'canonical_hash': digest(canonical(rows)), 'file_hash': file_hash(path), 'bytes': path.stat().st_size}
    for name, value in [('feature_dictionary.json', feature_dictionary(features)), ('config_snapshot.json', config),
                        ('audit/data_quality_summary.json', audit), ('audit/source_hashes.json', source['source_hashes'])]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
    manifest = {'dataset_id': config['dataset_id'], 'dataset_version': config['dataset_version'],
        'created_at': datetime.now(timezone.utc).isoformat(), 'config_hash': digest(canonical(config)),
        'source_hash': digest(canonical(source['source_hashes'])), 'tables': manifests,
        'canonical_build_hash': digest(canonical({name: m['canonical_hash'] for name, m in manifests.items()})),
        'stage_seconds': stages, 'runtime_seconds': time.monotonic()-start,
        'peak_memory_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if __import__('sys').platform == 'darwin' else 1024),
        'storage_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
        'predictive_table': 'company_features.parquet', 'predictive_allowlist': list(feature_dictionary(features)),
        'no_models_or_vocabularies_fitted': True,
        'preprocessing_contract': 'FoldPreprocessor: fit complete training leakage groups only; transform reads raw text only',
        'holdout_targets': 'evaluation_only/internal_holdout_targets.parquet'}
    manifest['implementation_hashes'] = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('company_modeling_*.py'))}
    manifest['implementation_hashes'][Path(__file__).name] = file_hash(Path(__file__))
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'output': str(output), **{k: audit[k] for k in ('companies', 'labels', 'states', 'pages', 'target_eligible', 'split_counts', 'eligible_phrases')}}, indent=2))
    return manifest, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/company_modeling_dataset.yaml')
    parser.add_argument('--output-directory', help='New version/reproducibility directory; never overwrite')
    parser.add_argument('--database', help='Optional source database, always opened read-only')
    args = parser.parse_args()
    build_dataset(args.config, args.output_directory, args.database)


if __name__ == '__main__':
    main()
