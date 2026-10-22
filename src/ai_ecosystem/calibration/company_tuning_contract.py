"""Isolated full-development contract, nested groups and atomic checkpoints."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
import yaml

from .company_experiment_features import load_dataset
from .company_modeling_common import canonical, digest, file_hash
from .company_modeling_groups import balanced_assignment


CANDIDATES = {
    'reference_logistic_size': ('body_mean', 'titles_mean', 'N', 'L', 'Z'),
    'reference_logistic_lexical': ('L',),
    'lexical_v2': ('L2',),
    'lexical_grounded_v2': ('L2', 'G2'),
    'combined_no_size_v2': ('body_mean', 'titles_mean', 'N', 'L2'),
    'combined_original_size_v2': ('body_mean', 'titles_mean', 'N', 'L2', 'Z'),
    'combined_saturated_size_v2': ('body_mean', 'titles_mean', 'N', 'L2', 'S'),
    'combined_top3_size_v2': ('body_top3', 'titles_top3', 'N', 'L2', 'S'),
}


def settings(candidate, config):
    if candidate == 'reference_logistic_size':
        return [10.0]
    if candidate == 'reference_logistic_lexical':
        return [0.1]
    return config['logistic_c']


def load_config(path):
    config = yaml.safe_load(Path(path).read_text())
    required = ('experiment_id', 'dataset_directory', 'output_directory', 'lexicon_path',
                'seeds', 'outer_folds', 'inner_folds', 'logistic_c', 'maximum_iterations',
                'ap_tolerance', 'bootstrap_replicates', 'bootstrap_seed', 'blas_threads',
                'support_cap', 'size_caps', 'phrase_minimum_groups',
                'phrase_maximum_group_fraction', 'phrase_maximum_tokens',
                'vocabulary_change_fraction', 'improvement')
    if not isinstance(config, dict) or any(k not in config for k in required):
        raise ValueError('Missing tuning configuration fields')
    for key in ('outer_folds', 'inner_folds', 'maximum_iterations', 'bootstrap_replicates',
                'blas_threads', 'support_cap', 'phrase_minimum_groups', 'phrase_maximum_tokens'):
        if type(config[key]) is not int or config[key] < (2 if 'folds' in key else 1):
            raise ValueError(f'Invalid {key}')
    if not config['seeds'] or len(set(config['seeds'])) != len(config['seeds']):
        raise ValueError('Seeds must be nonempty and unique')
    if not config['logistic_c'] or any(not np.isfinite(c) or c <= 0 for c in config['logistic_c']):
        raise ValueError('Invalid regularization grid')
    if not 0 < config['phrase_maximum_group_fraction'] <= 1:
        raise ValueError('Invalid phrase frequency limit')
    lexicon = yaml.safe_load(Path(config['lexicon_path']).read_text())
    if not lexicon.get('version') or not lexicon.get('provenance') or not lexicon.get('categories'):
        raise ValueError('Missing lexical provenance/categories')
    for name, entry in lexicon['categories'].items():
        if entry['band'] not in ('explicit_ai', 'adjacent', 'ambiguous') or not entry['rationale'] or not entry['aliases']:
            raise ValueError(f'Invalid lexical category {name}')
    return config, lexicon


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def write_table(path, frame):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def load_full_cohort(directory):
    """Explicit new-development access; never relax the old holdout loader."""
    directory = Path(directory)
    manifest, frame, numeric = load_dataset(directory)
    name = manifest['holdout_targets']
    if file_hash(directory/name) != manifest['tables'][name.removesuffix('.parquet')]['file_hash']:
        raise ValueError('Former holdout hash mismatch')
    held = pd.read_parquet(directory/name).set_index('bvd_id')
    target = frame.set_index('bvd_id')
    if not set(held.index) <= set(target.index) or held.index.has_duplicates:
        raise ValueError('Invalid former holdout membership')
    for cid, row in held.iterrows():
        if pd.notna(target.at[cid, 'label']) and target.at[cid, 'label'] != row.label:
            raise ValueError('Conflicting original labels')
        target.at[cid, 'label'] = row.label
    frame = target.reset_index().sort_values('bvd_id').reset_index(drop=True)
    if frame.bvd_id.duplicated().any() or not frame.label.isin(['KEEP', 'EXCLUDE', 'UNKNOWN']).all():
        raise ValueError('Incomplete full-development label reconciliation')
    if not frame.state.isin(['PROCESSABLE', 'NO_EVIDENCE']).all():
        raise ValueError('Unresolved operational input error')
    frame['historical_split'] = frame['split']
    return manifest, frame, numeric


def assign_folds(frame, count, seed):
    members = frame.set_index('bvd_id').leakage_group_id.to_dict()
    categories = {r.bvd_id: [r.label, 'source:'+r.calibration_source, 'state:'+r.state]
                  for r in frame.itertuples()}
    assignment = balanced_assignment(members, categories, list(range(count)), [1/count]*count, seed)
    for fold in range(count):
        for selected in (True, False):
            subset = frame[frame.bvd_id.map(assignment).eq(fold).eq(selected)]
            known = subset[subset.state.eq('PROCESSABLE') & subset.label.isin(['KEEP', 'EXCLUDE'])]
            if set(known.label) != {'KEEP', 'EXCLUDE'}:
                raise ValueError(f'Fold {fold} lacks both supervised classes')
    return assignment


def make_schedule(frame, config):
    rows, partitions = [], []
    for repetition, seed in enumerate(config['seeds']):
        outer = assign_folds(frame, config['outer_folds'], seed)
        for fold in range(config['outer_folds']):
            training = frame[~frame.bvd_id.map(outer).eq(fold)]
            evaluation = frame[frame.bvd_id.map(outer).eq(fold)]
            inner = assign_folds(training, config['inner_folds'], seed + 1000 + fold)
            for r in frame.itertuples():
                rows.append({'bvd_id': r.bvd_id, 'leakage_group_id': r.leakage_group_id,
                             'repetition': repetition, 'outer_fold': fold,
                             'role': 'evaluation' if outer[r.bvd_id] == fold else 'training',
                             'inner_fold': inner.get(r.bvd_id)})
            for inner_fold in range(config['inner_folds']):
                partitions.append({'repetition': repetition, 'outer_fold': fold, 'inner_fold': inner_fold,
                    'train_ids': sorted(c for c in inner if inner[c] != inner_fold),
                    'evaluation_ids': sorted(c for c in inner if inner[c] == inner_fold)})
            partitions.append({'repetition': repetition, 'outer_fold': fold, 'inner_fold': None,
                               'train_ids': training.bvd_id.tolist(), 'evaluation_ids': evaluation.bvd_id.tolist()})
    total = sum(sum(len(settings(c, config)) if p['inner_fold'] is not None else 1 for c in CANDIDATES)
                for p in partitions)
    return pd.DataFrame(rows), partitions, total


def valid_checkpoint(directory):
    directory = Path(directory)
    marker = directory/'complete.json'
    if not marker.exists():
        return None
    result = json.loads(marker.read_text())
    for name, expected in result['files'].items():
        if not (directory/name).is_file() or file_hash(directory/name) != expected:
            raise ValueError(f'Corrupt checkpoint: {directory/name}')
    return result


class Progress:
    """Informational state; successful artifact checkpoints are authoritative."""
    def __init__(self, root, expected):
        self.root = Path(root); self.started = time.monotonic()
        self.events = self.root/'runtime_events.jsonl'
        prior = [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []
        self.offset = max((e.get('elapsed_seconds', 0) for e in prior), default=0)
        failures = [e for e in prior if e.get('event') == 'failure']
        completed = sum(valid_checkpoint(p.parent) is not None for p in (self.root/'fits').glob('*/complete.json'))
        self.state = {'status': 'running', 'current_stage': 'initializing', 'repetition': None,
                      'outer_fold': None, 'inner_fold': None, 'candidate': None, 'setting': None,
                      'completed_fits': completed, 'expected_total_fits': expected,
                      'completed_candidates': [], 'cache_status': None,
                      'last_successful_checkpoint': None, 'failure_count': len(failures),
                      'retry_count': sum(e.get('event') == 'retry' for e in prior),
                      'last_failure_reason': failures[-1].get('reason') if failures else None}
        self.update(event='session_start')
        if prior and prior[-1].get('status') in ('failed', 'running'):
            self.state['retry_count'] += 1
            self.update(event='retry', reason='resume_after_failure' if prior[-1].get('status') == 'failed' else 'resume_after_unclosed_session')

    def update(self, event='progress', **values):
        self.state.update(values)
        self.state['elapsed_seconds'] = self.offset + time.monotonic()-self.started
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()
        write_json(self.root/'progress.json', self.state)
        record = {**self.state, 'event': event}
        with self.events.open('a') as stream:
            stream.write(json.dumps(record, sort_keys=True)+'\n'); stream.flush()
        line = (f"[{self.state['completed_fits']}/{self.state['expected_total_fits']}] "
              f"{self.state['current_stage']} r={self.state['repetition']} outer={self.state['outer_fold']} "
              f"inner={self.state['inner_fold']} {self.state['candidate'] or ''} "
              f"{self.state['cache_status'] or ''} elapsed={self.state['elapsed_seconds']:.1f}s")
        with (self.root/'run.log').open('a') as stream:
            stream.write(line+'\n')
            if event in ('failure', 'retry'): stream.write(str(values.get('reason', ''))+'\n')
        print(line, flush=True)

    def failure(self, error):
        self.update(event='failure', status='failed', failure_count=self.state['failure_count']+1,
                    reason=f'{type(error).__name__}: {error}', last_failure_reason=str(error))
