"""Fold-local experiment adapters; frozen dataset artifacts remain untouched."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, normalize

from .company_modeling_common import canonical, digest, file_hash
from .company_modeling_preprocessing import FoldPreprocessor


def feature_blocks(columns):
    columns = set(columns)-{'bvd_id'}
    z = {c for c in columns if c.startswith('availability_') or c.startswith('structure_words_') or c == 'duplicates_quarantined_contents'}
    z.update(f'lexical_{kind}_support' for kind in ('explicit_ai', 'adjacent', 'ambiguous'))
    lexical = {c for c in columns if c.startswith('lexical_')} - z
    blocks = {'N': sorted(columns-z-lexical), 'L': sorted(lexical), 'Z': sorted(z)}
    if set.union(*(set(v) for v in blocks.values())) != columns:
        raise ValueError('Feature-block partition mismatch')
    return blocks


def load_dataset(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    for name, info in manifest['tables'].items():
        if name.startswith('evaluation_only/'):
            continue
        if file_hash(directory/(name+'.parquet')) != info['file_hash']:
            raise ValueError(f'Frozen dataset hash mismatch: {name}')
    def read(name):
        return pd.read_parquet(directory/(name+'.parquet'))
    frame = read('companies').merge(read('splits'), validate='one_to_one').merge(read('operational_states'), validate='one_to_one')
    frame = frame.merge(read('targets'), on='bvd_id', how='left', validate='one_to_one')
    frame = frame.merge(read('audit/calibration_sources'), on='bvd_id', validate='one_to_one')
    frame = frame.merge(read('audit/visibility_gaps'), on='bvd_id', validate='one_to_one')
    numeric = read('company_features').set_index('bvd_id').sort_index()
    if set(numeric.columns) != set(manifest['predictive_allowlist']):
        raise ValueError('Predictive allowlist mismatch')
    if frame.groupby('leakage_group_id').split.nunique().max() != 1:
        raise ValueError('Leakage group crosses splits')
    development = frame[frame.split.eq('development')]
    if development.groupby('leakage_group_id').development_fold.nunique().max() != 1:
        raise ValueError('Leakage group crosses folds')
    if frame[~frame.split.eq('internal_holdout')].label.isna().any():
        raise ValueError('Missing non-holdout targets')
    if set(frame.bvd_id) != set(numeric.index):
        raise ValueError('Incomplete feature joins')
    return manifest, frame.sort_values('bvd_id').reset_index(drop=True), numeric


class ExperimentText(FoldPreprocessor):
    def fit(self, training_company_ids, training_supervision):
        super().fit(training_company_ids, training_supervision)
        old_groups = len({self.groups[c] for c in training_company_ids})
        evidence_groups = len({self.groups[c] for c in training_company_ids if any(text.strip() for _, text in self.documents[c])})
        df = np.rint((1+old_groups)/np.exp(self.idf-1)-1).astype(int)
        kept = [(term, df[index]) for term, index in self.vocabulary.items() if df[index] >= 2]
        self.vocabulary = {term: i for i, (term, _) in enumerate(kept)}
        self.idf = np.array([np.log((1+evidence_groups)/(1+count))+1 for _, count in kept])
        self.provenance.update(experiment_adapter='nonempty_group_df_v1', evidence_group_count=evidence_groups, minimum_text_groups=2)
        self.provenance['cache_key'] = digest(canonical(self.provenance))
        return self


class NumericBlock:
    def fit(self, values, columns):
        self.columns = columns
        self.imputer = SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)
        transformed = self.imputer.fit_transform(values[columns])
        self.keep = np.ptp(transformed, axis=0) > 0
        self.scaler = StandardScaler().fit(transformed[:, self.keep]) if self.keep.any() else None
        return self

    def transform(self, values, tree=False):
        transformed = self.imputer.transform(values[self.columns])[:, self.keep]
        if tree:
            return transformed
        return sparse.csr_matrix(normalize(self.scaler.transform(transformed))) if self.scaler else sparse.csr_matrix((len(values), 0))


def implementation_hashes():
    directory = Path(__file__).parent
    paths = list(directory.glob('company_experiment_*.py')) + [directory/'run_company_model_experiments.py', directory/'company_modeling_preprocessing.py', directory/'company_modeling_common.py']
    return {p.name: file_hash(p) for p in paths if p.exists()}


def prepare_fold(directory, train_ids, transform_ids, numeric, frame, config, cache_directory):
    manifest = json.loads((Path(directory)/'manifest.json').read_text())
    identity = {'dataset': manifest['canonical_build_hash'], 'training_ids': sorted(train_ids),
                'transform_ids': sorted(transform_ids), 'config': config, 'implementation': implementation_hashes()}
    key = digest(canonical(identity)); cache = Path(cache_directory)/key
    start = time.monotonic()
    if (cache/'complete.json').exists():
        saved = json.loads((cache/'complete.json').read_text())
        if saved['identity'] != identity or file_hash(cache/'features.joblib') != saved['features_hash']:
            raise ValueError('Invalid cached preprocessing')
        result = joblib.load(cache/'features.joblib')
        result['cache_hit'] = True
        result['warm_cache_seconds'] = time.monotonic()-start
        return result
    cache.mkdir(parents=True, exist_ok=True)
    ids = sorted(transform_ids)
    blocks = {}; fitted = {}; timings = {}; vocabulary = {}
    for channel in ('body', 'titles_headings'):
        begin = time.monotonic()
        base = FoldPreprocessor.from_dataset(directory, channel)
        pre = ExperimentText(base.documents, base.groups, base.config, base.names, base.prohibited)
        supervision = base.training_supervision(train_ids) if channel == 'body' else []
        pre.fit(train_ids, supervision)
        timings[channel+'_fit_seconds'] = time.monotonic()-begin
        begin = time.monotonic()
        result = pre.transform(ids)
        timings[channel+'_transform_seconds'] = time.monotonic()-begin
        for pooling in ('mean', 'max'):
            blocks[channel+'_'+pooling] = result['text_'+pooling]
        if channel == 'body':
            phrase = result['phrase_proportion'].copy()
            phrase.data[:] = 1
            blocks['G'] = normalize(phrase) if phrase.shape[1] else phrase
        vocabulary[channel] = pre.export_vocabulary()
        # Fitted artifacts preserve transforms, not another copy of website text.
        compact = copy.copy(pre); compact.documents = {}; compact.names = {}
        fitted[channel] = compact
        del base, pre, result
    training_frame = frame.set_index('bvd_id').loc[train_ids]
    usable_ids = training_frame.index[training_frame.state.eq('PROCESSABLE')].tolist()
    for name, columns in feature_blocks(numeric.columns).items():
        processor = NumericBlock().fit(numeric.loc[usable_ids], columns)
        fitted[name] = processor
        blocks[name] = processor.transform(numeric.loc[ids])
        blocks[name+'_tree'] = processor.transform(numeric.loc[ids], tree=True)
    result = {'ids': ids, 'blocks': blocks, 'fitted': fitted, 'timings': timings, 'vocabulary': vocabulary,
              'preprocessing_seconds': time.monotonic()-start, 'cache_hit': False, 'cache_key': key}
    joblib.dump(result, cache/'features.joblib', compress=3)
    (cache/'vocabulary.json').write_text(json.dumps(vocabulary, ensure_ascii=False, sort_keys=True))
    (cache/'complete.json').write_text(json.dumps({'identity': identity, 'features_hash': file_hash(cache/'features.joblib')}, sort_keys=True))
    return result


CANDIDATES = {
    'logistic_lexical': ('L',), 'logistic_structural': ('N',),
    'logistic_text_mean': ('T_mean',), 'logistic_text_max': ('T_max',),
    'logistic_combined': ('T', 'N', 'L'), 'logistic_grounded': ('T', 'N', 'L', 'G'),
    'logistic_size': ('T', 'N', 'L', 'Z'), 'tree_numeric': ('N', 'L'),
}


def matrix(cache, candidate, pooling):
    parts = []
    tree = candidate == 'tree_numeric'
    for block in CANDIDATES[candidate]:
        if block.startswith('T'):
            mode = block.split('_')[1] if '_' in block else pooling
            parts.extend(cache['blocks'][channel+'_'+mode] for channel in ('body', 'titles_headings'))
        else:
            parts.append(cache['blocks'][block+('_tree' if tree else '')])
    return np.hstack(parts) if tree else sparse.hstack(parts, format='csr')
