"""Training-only grounded cleaning and annotation-free frozen transforms."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.preprocessing import normalize

from .company_experiment_features import ExperimentText, NumericBlock, feature_blocks
from .company_modeling_common import canonical, digest, file_hash, tokens
from .company_modeling_preprocessing import ngrams
from .company_tuning_contract import valid_checkpoint, write_json
from .company_tuning_features import PhraseMatcher, pooled_text


CANDIDATES = {
    'reference_logistic_size': ('body_mean', 'titles_mean', 'N', 'L', 'Z'),
    'cleaned_grounded_lexical': ('L2', 'G3'),
    'cleaned_grounded_combined': ('body_mean', 'titles_mean', 'N', 'L2', 'G3'),
}
BASE_BLOCKS = ('body_mean', 'titles_mean', 'N', 'L', 'Z', 'L2')


def phrase_presence(documents, ids, phrases):
    """One binary column per normalized phrase, independent of annotations."""
    matcher = PhraseMatcher(phrases)
    columns = {p: i for i, p in enumerate(phrases)}
    matched = {}; rows = []; cols = []
    for i, cid in enumerate(ids):
        found = set()
        for identity, text in documents[cid]:
            if identity not in matched:
                matched[identity] = matcher.match(text)
            found.update(matched[identity])
        for phrase in sorted(found):
            rows.append(i); cols.append(columns[phrase])
    return sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(ids), len(phrases)))


class CleanGrounded:
    def __init__(self, policy, lexicon):
        self.policy = policy; self.lexicon = lexicon
        self.stop = {t for word in lexicon['stopwords'] for t in tokens(word)}
        self.low = self.stop | {t for word in lexicon['generic_tokens']+policy['additional_low_information_tokens'] for t in tokens(word)}
        self.allow = set(policy['technical_unigrams'])
        self.protected = {' '.join(tokens(p)) for entry in lexicon['categories'].values()
                          if entry['band'] in ('explicit_ai', 'adjacent') for p in entry['aliases']
                          if len(tokens(p)) > 1}

    def rejection(self, phrase, identifiers):
        ts = phrase.split()
        stop, low, allow, protected = self.stop, self.low, self.allow, self.protected
        if any(any(c.isdigit() for c in t) for t in ts): return 'numeric_token'
        if set(ts) & identifiers: return 'source_identifier'
        if len(ts) == 1:
            return None if phrase in allow else 'nontechnical_unigram'
        if phrase in protected: return None
        if ts[0] in stop or ts[-1] in stop: return 'stopword_boundary'
        if all(t in low for t in ts): return 'generic_only'
        if sum(t in low for t in ts)/len(ts) >= self.policy['maximum_generic_fraction']:
            return 'generic_fragment'
        if any(len(t) < 3 and t not in stop and t not in allow for t in ts):
            return 'uninformative_short_token'
        return None

    def fit(self, base, train_ids, supervision, labels, processable_ids):
        train_ids = sorted(train_ids); train = set(train_ids)
        if any(r['bvd_id'] not in train for r in supervision):
            raise ValueError('Non-training annotation supplied to grounded fit')
        if not set(processable_ids) <= train: raise ValueError('Non-training vector-fit rows')
        groups = {base.groups[c] for c in train}
        if any(g in groups and cid not in train for cid, g in base.groups.items()):
            raise ValueError('Training membership splits a leakage group')
        supports = {'all': defaultdict(set), 'keep_exclude': defaultdict(set)}
        channels = defaultdict(set); rejected = Counter(); before = set()
        rejected_terms = Counter()
        for row in supervision:
            if not row['eligible']:
                rejected['ineligible_source'] += 1; continue
            cid = row['bvd_id']
            identifiers = set(tokens(base.names.get(cid, '')) + tokens(str(row.get('normalized_domain') or '').replace('.', ' ')))
            for phrase in set(ngrams(row['phrase'], self.policy['maximum_tokens'])):
                before.add((row['signal_type'], phrase))
                reason = self.rejection(phrase, identifiers)
                if reason:
                    rejected[reason] += 1
                    rejected_terms[(phrase, reason)] += 1
                    continue
                supports['all'][phrase].add(base.groups[cid])
                channels[phrase].add(row['signal_type'])
                if labels[cid] in ('KEEP', 'EXCLUDE'):
                    supports['keep_exclude'][phrase].add(base.groups[cid])
        phrases = sorted(supports['all'])
        matrix = phrase_presence(base.documents, train_ids, phrases).tocsc()
        evidence_groups = {base.groups[c] for c in train_ids if any(t.strip() for _, t in base.documents[c])}
        technical = set(self.policy['technical_unigrams']) | {
            ' '.join(tokens(p)) for entry in self.lexicon['categories'].values()
            if entry['band'] in ('explicit_ai', 'adjacent') for p in entry['aliases'] if len(tokens(p)) > 1}
        frequencies = {}
        for i, phrase in enumerate(phrases):
            present = matrix.indices[matrix.indptr[i]:matrix.indptr[i+1]]
            frequencies[phrase] = len({base.groups[train_ids[j]] for j in present})/max(len(evidence_groups), 1)
        accepted = {}; records = []
        for origin, vocabulary in supports.items():
            accepted[origin] = set()
            for phrase, source_groups in sorted(vocabulary.items()):
                reason = 'accepted'
                if len(source_groups) < self.policy['minimum_groups']: reason = 'insufficient_training_groups'
                elif frequencies[phrase] > self.policy['maximum_group_fraction'] and phrase not in technical:
                    reason = 'common_corpus_term'
                if reason == 'accepted': accepted[origin].add(phrase)
                records.append({'origin': origin, 'phrase': phrase, 'reason': reason,
                    'channels': sorted(channels[phrase]), 'source_group_ids': sorted(source_groups),
                    'source_group_count': len(source_groups), 'corpus_group_frequency': frequencies[phrase]})
        a, b = accepted['all'], accepted['keep_exclude']
        if not b <= a: raise ValueError('Grounded diagnostic subset invariant violated')
        rows = [train_ids.index(c) for c in sorted(processable_ids)]
        columns = {p: i for i, p in enumerate(phrases)}
        equivalence = defaultdict(list); constants = []
        for phrase in sorted(a):
            vector = matrix[rows, columns[phrase]].tocsc()
            present = tuple(vector.indices)
            if len(present) in (0, len(rows)):
                constants.append(phrase)
            else:
                equivalence[present].append(phrase)
        self.aliases = sorted([sorted(v) for v in equivalence.values()])
        self.feature_names = ['grounded:'+digest(canonical(v))[:20] for v in self.aliases]
        self.provenance = {'training_company_ids': train_ids, 'training_group_ids': sorted(groups),
            'vector_training_ids': sorted(processable_ids), 'policy_hash': digest(canonical(self.policy)),
            'supervision_hash': digest(canonical(supervision)),
            'training_text_hash': digest(canonical({c: base.documents[c] for c in train_ids}))}
        self.diagnostic = {**self.provenance, 'before_channel_phrase_count': len(before),
            'before_distinct_phrase_count': len({p for _, p in before}),
            'all_size': len(a), 'keep_exclude_size': len(b), 'intersection': len(a & b),
            'jaccard': len(b)/len(a) if a else 1., 'unknown_added': sorted(a-b),
            'meaningful_difference_flag': len(a-b)/max(len(a), 1) >= .2,
            'after_lexical_filter_count': len(phrases), 'after_vector_dedup_count': len(self.aliases),
            'constant_phrases': constants,
            'identical_vector_reduction': len(a)-len(constants)-len(self.aliases),
            'ngram_counts': dict(Counter(len(p.split()) for p in a)),
            'channel_counts': dict(Counter(ch for p in a for ch in channels[p])),
            'lexical_rejection_counts': dict(rejected), 'retained_examples': sorted(a)[:50],
            'aliases': self.aliases}
        self.records = records + [{'origin': 'lexical_filter', 'phrase': p, 'reason': r, 'occurrences': n}
                                  for (p, r), n in sorted(rejected_terms.items())]
        return self

    def transform(self, documents, ids):
        phrases = sorted({p for aliases in self.aliases for p in aliases})
        raw = phrase_presence(documents, ids, phrases)
        indices = {p: i for i, p in enumerate(phrases)}
        parts = [sparse.csr_matrix(raw[:, [indices[p] for p in aliases]].max(axis=1)) for aliases in self.aliases]
        result = sparse.hstack(parts, format='csr') if parts else sparse.csr_matrix((len(ids), 0))
        return normalize(result) if result.shape[1] else result


def write_joblib(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    joblib.dump(value, temporary, compress=3); temporary.replace(path)


def prepare_cleanup(root, identity, part, documents, frame, numeric, lexical, lexicon, config, parent=None):
    train = part['train_ids']; ids = sorted(set(train+part['evaluation_ids']))
    directory = Path(root)/'preprocessing'/digest(canonical({'identity': identity, 'training': train, 'ids': ids}))
    if valid_checkpoint(directory):
        return joblib.load(directory/'features.joblib'), directory, True
    directory.mkdir(parents=True, exist_ok=True)
    available = frame[frame.bvd_id.isin(train) & frame.state.eq('PROCESSABLE')].bvd_id.tolist()
    blocks = {}; fitted = {}; names = {}; provenance = None
    if parent is not None:
        marker = valid_checkpoint(parent)
        if marker is None or marker['training_ids'] != train or marker['transform_ids'] != ids:
            raise ValueError('Parent preprocessing membership mismatch')
        old = joblib.load(Path(parent)/'features.joblib')
        if old['ids'] != ids or old['training_ids'] != train: raise ValueError('Parent cache membership mismatch')
        blocks = {b: old['blocks'][b] for b in BASE_BLOCKS}
        names = {b: old['names'][b] for b in BASE_BLOCKS}
        fitted = {b: old['fitted'][b] for b in ('body', 'titles', 'N', 'L', 'Z', 'L2')}
        provenance = {'directory': str(parent), 'marker_hash': file_hash(Path(parent)/'complete.json')}
    else:
        for channel, base in documents.items():
            pre = ExperimentText(base.documents, base.groups, base.config, base.names).fit(train, [])
            blocks[channel+'_mean'] = pooled_text(pre, ids)['mean']
            names[channel+'_mean'] = [channel+':'+t for t in pre.vocabulary]
            compact = copy.copy(pre); compact.documents = {}; compact.names = {}; fitted[channel] = compact
        sources = {k: (numeric, cols) for k, cols in feature_blocks(numeric.columns).items()}
        sources['L2'] = lexical, sorted(lexical.columns)
        for name, (values, cols) in sources.items():
            processor = NumericBlock().fit(values.loc[available], cols)
            blocks[name] = processor.transform(values.loc[ids]); fitted[name] = processor
            names[name] = list(processor.imputer.get_feature_names_out(cols)[processor.keep])
    base = documents['body']
    grounded = CleanGrounded(config['cleanup'], lexicon).fit(base, train, base.training_supervision(train),
        frame.set_index('bvd_id').label.to_dict(), available)
    blocks['G3'] = grounded.transform(base.documents, ids); names['G3'] = grounded.feature_names
    write_json(directory/'grounded_diagnostic.json', grounded.diagnostic)
    write_json(directory/'grounded_terms.json', grounded.records)
    grounded.records = []
    fitted['G3'] = grounded
    result = {'ids': ids, 'training_ids': train, 'blocks': blocks, 'fitted': fitted, 'names': names}
    write_joblib(directory/'features.joblib', result)
    write_json(directory/'complete.json', {'training_ids': train, 'transform_ids': ids, 'parent': provenance,
        'files': {n: file_hash(directory/n) for n in ('features.joblib', 'grounded_diagnostic.json', 'grounded_terms.json')}})
    return result, directory, False


class FrozenCompanyScorer:
    """Offline scoring of deterministic inputs; no annotation or label argument."""
    def __init__(self, model, candidate, fitted, names, provenance):
        self.model = model; self.candidate = candidate; self.fitted = fitted
        self.names = names; self.provenance = provenance

    def score(self, ids, documents, numeric, lexical, states):
        if len(set(ids)) != len(ids): raise ValueError('Duplicate company IDs')
        if not set(states.loc[ids]) <= {'PROCESSABLE', 'NO_EVIDENCE'}:
            raise ValueError('Unresolved evidence state')
        parts = []
        for block in CANDIDATES[self.candidate]:
            if block.endswith('_mean'):
                channel = block.removesuffix('_mean'); pre = copy.copy(self.fitted[channel])
                pre.documents = documents[channel]
                matrix = pooled_text(pre, ids)['mean']
            elif block == 'G3': matrix = self.fitted[block].transform(documents['body'], ids)
            else: matrix = self.fitted[block].transform((lexical if block == 'L2' else numeric).loc[ids])
            if matrix.shape[1] != len(self.names[block]): raise ValueError('Frozen feature width mismatch')
            parts.append(matrix)
        x = sparse.hstack(parts, format='csr')
        mask = states.loc[ids].eq('PROCESSABLE').to_numpy(); scores = np.full(len(ids), np.nan)
        if mask.any(): scores[mask] = self.model.decision_function(x[mask])
        return pd.DataFrame({'bvd_id': ids, 'state': states.loc[ids].to_numpy(), 'score': scores})
