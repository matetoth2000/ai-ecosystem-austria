"""Immutable parent contracts and explicit cleanup cache identities."""
from __future__ import annotations

from importlib.metadata import version
import json
from pathlib import Path
import sys

import pandas as pd
import yaml

from .company_cleanup_features import CANDIDATES
from .company_modeling_common import canonical, digest, file_hash, tokens
from .company_tuning_contract import load_config, load_full_cohort, make_schedule, valid_checkpoint


def configuration(path):
    request = yaml.safe_load(Path(path).read_text())
    required = ('experiment_id', 'parent_config', 'parent_directory', 'output_directory', 'cleanup')
    if not isinstance(request, dict) or any(k not in request for k in required):
        raise ValueError('Missing cleanup configuration fields')
    config, lexicon = load_config(request['parent_config']); config.update(request)
    p = config['cleanup']
    if not p.get('version') or not p.get('provenance'): raise ValueError('Missing cleanup policy provenance')
    for key in ('technical_unigrams', 'additional_low_information_tokens'):
        values = p[key]
        if len(set(values)) != len(values) or any(tokens(v) != [v] for v in values):
            raise ValueError(f'Invalid normalized cleanup tokens: {key}')
    if set(p['technical_unigrams']) & {'software', 'smart', 'intelligent', 'optimierung'}:
        raise ValueError('Generic term in technical unigram allowlist')
    for key in ('maximum_generic_fraction', 'maximum_group_fraction'):
        if not 0 < p[key] <= 1: raise ValueError(f'Invalid {key}')
    for key in ('minimum_groups', 'maximum_tokens'):
        if type(p[key]) is not int or p[key] < 1: raise ValueError(f'Invalid {key}')
    if p['deduplication'] != 'normalized_phrase_then_training_binary_vector_alias_union':
        raise ValueError('Unsupported grounded deduplication')
    return config, lexicon


def validate_dataset(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    snapshot = json.loads((directory/'config_snapshot.json').read_text())
    if digest(canonical(snapshot)) != manifest.get('config_hash'):
        raise ValueError('Dataset configuration snapshot hash mismatch')
    result = load_full_cohort(directory)
    return result


def implementation_files():
    base = Path(__file__).parent
    names = ('run_company_model_cleanup', 'company_cleanup_contract', 'company_cleanup_features',
             'company_cleanup_reporting', 'run_company_model_tuning', 'company_tuning_contract',
             'company_tuning_features', 'company_tuning_metrics', 'company_experiment_features',
             'company_experiment_metrics', 'company_modeling_preprocessing', 'company_modeling_common',
             'company_modeling_groups', 'attribution_features')
    return {n+'.py': base/(n+'.py') for n in names}


def validate_parent(config, lexicon, frame, manifest):
    root = Path(config['parent_directory'])
    if valid_checkpoint(root) is None: raise ValueError('Parent experiment is incomplete')
    parent = json.loads((root/'identity.json').read_text())
    if parent['dataset_hash'] != manifest['canonical_build_hash']:
        raise ValueError('Parent dataset identity mismatch')
    if parent['dataset_files'] != {n: v['file_hash'] for n, v in manifest['tables'].items()}:
        raise ValueError('Parent dataset table mismatch')
    if parent['lexicon'] != lexicon: raise ValueError('Expanded fixed lexicon must remain unchanged')
    for key in ('seeds', 'outer_folds', 'inner_folds', 'logistic_c', 'maximum_iterations',
                'support_cap', 'ap_tolerance', 'bootstrap_replicates', 'bootstrap_seed', 'blas_threads'):
        if parent['config'][key] != config[key]: raise ValueError(f'Parent setting mismatch: {key}')
    # Only these unchanged implementations produced the blocks being imported.
    for name in ('company_experiment_features.py', 'company_modeling_preprocessing.py',
                 'company_modeling_common.py', 'company_tuning_features.py', 'attribution_features.py'):
        if file_hash(Path(__file__).parent/name) != parent['source_files'][name]:
            raise ValueError(f'Parent feature implementation mismatch: {name}')
    pd.testing.assert_frame_equal(pd.read_parquet(root/'cohort.parquet'), frame)
    folds, parts, _ = make_schedule(frame, config)
    if parts != json.loads((root/'schedule.json').read_text())['partitions']:
        raise ValueError('Frozen nested schedule mismatch')
    pd.testing.assert_frame_equal(folds, pd.read_parquet(root/'fold_assignments.parquet'))
    index = {}
    for path in (root/'preprocessing').glob('*/complete.json'):
        marker = json.loads(path.read_text())
        key = canonical([marker['training_ids'], marker['transform_ids']])
        if key in index: raise ValueError('Duplicate parent preprocessing membership')
        index[key] = path.parent
    expected = sum(1+2*len(config['logistic_c']) if p['inner_fold'] is not None else 3 for p in parts)
    return folds, parts, expected, index, parent


def run_identity(config, lexicon, manifest, parent):
    data = Path(config['dataset_directory']); root = Path(config['parent_directory'])
    environment = {'python': sys.version, **{n: version(n) for n in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'pyarrow', 'joblib')}}
    if environment != parent['environment']: raise ValueError('Parent dependency environment mismatch')
    return json.loads(canonical({'config': config, 'lexicon': lexicon,
        'dataset_hash': manifest['canonical_build_hash'], 'dataset_config_hash': manifest['config_hash'],
        'dataset_manifest_file_hash': file_hash(data/'manifest.json'),
        'dataset_config_file_hash': file_hash(data/'config_snapshot.json'),
        'dataset_files': {n: v['file_hash'] for n, v in manifest['tables'].items()},
        'parent_identity_hash': file_hash(root/'identity.json'),
        'parent_completion_hash': file_hash(root/'complete.json'),
        'parent_schedule_hash': file_hash(root/'schedule.json'),
        'sources': {n: file_hash(p) for n, p in implementation_files().items()},
        'environment': environment, 'candidates': CANDIDATES}))
