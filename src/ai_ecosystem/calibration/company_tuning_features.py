"""Objective-defined lexical features and fold-local tuning representations."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.preprocessing import normalize

from .attribution_features import distinctive_company_tokens
from .company_experiment_features import ExperimentText, NumericBlock, feature_blocks
from .company_modeling_common import canonical, digest, file_hash, summarize, tokens
from .company_modeling_preprocessing import FoldPreprocessor, ngrams
from .company_tuning_contract import CANDIDATES, valid_checkpoint, write_json


class PhraseMatcher:
    def __init__(self, phrases):
        self.starts = defaultdict(list)
        for phrase in sorted(set(phrases)):
            ts = tuple(tokens(phrase))
            if ts:
                self.starts[ts[0]].append((ts, phrase))

    def match(self, text):
        ts = tokens(text); found = set()
        for i, token in enumerate(ts):
            for seq, phrase in self.starts.get(token, ()):
                if tuple(ts[i:i+len(seq)]) == seq:
                    found.add(phrase)
        return found


def saturation_features(numeric, caps):
    result = pd.DataFrame(index=numeric.index)
    for col in ('availability_usable', 'availability_predictive'):
        result['context_'+col] = numeric[col]
    result['context_contents'] = np.log1p(numeric.availability_predictive_contents.clip(upper=caps['contents']))
    result['context_words'] = np.log1p(numeric.availability_words.clip(upper=caps['words']))
    result['context_median_words'] = numeric.structure_words_p50.clip(upper=caps['median_words'])
    anomalies = []
    for name, num, den in [('acquisition', 'availability_acquired_success', 'availability_acquired_records'),
                           ('extraction', 'availability_successful_pages', 'availability_acquired_success')]:
        valid = numeric[den].gt(0) & numeric[num].ge(0) & numeric[num].le(numeric[den])
        result['context_'+name+'_ratio'] = (numeric[num]/numeric[den]).where(valid)
        bad = numeric[num].gt(numeric[den]) | numeric[num].lt(0) | numeric[den].lt(0)
        for cid in numeric.index[bad]:
            anomalies.append({'bvd_id': cid, 'ratio': name, 'numerator': float(numeric.at[cid, num]),
                              'denominator': float(numeric.at[cid, den]), 'reason': 'inconsistent_source_counts'})
    return result, anomalies


def expanded_lexical(directory, cohort, lexicon, config):
    directory = Path(directory)
    contents = pd.read_parquet(directory/'contents.parquet').set_index('content_id').body_text.to_dict()
    pages = pd.read_parquet(directory/'pages.parquet').set_index('scraped_page_id')
    cp = pd.read_parquet(directory/'company_page_features.parquet')
    cp = cp[cp.predictive_eligible]
    category = {name: {' '.join(tokens(p)) for p in entry['aliases']} for name, entry in lexicon['categories'].items()}
    channels = {'category_'+k: v for k, v in category.items()}
    for band in ('explicit_ai', 'adjacent', 'ambiguous'):
        channels['band_'+band] = set().union(*(category[k] for k, v in lexicon['categories'].items() if v['band'] == band))
    acronyms = set(lexicon['acronyms']); context = set(lexicon['context_terms'])
    for acronym in sorted(acronyms):
        channels['acronym_'+acronym] = {acronym}
    matcher = PhraseMatcher(set().union(*channels.values()))
    hits = {cid: matcher.match(text) for cid, text in contents.items()}
    title_hits = {pid: matcher.match(str(row.title or '')) for pid, row in pages.iterrows()}
    heading_hits = {pid: matcher.match(str(row.headings_text or '')) for pid, row in pages.iterrows()}
    paragraph_cache = {}
    for cid, text in contents.items():
        if hits[cid]:
            paragraph_cache[cid] = [(set(tokens(p)), matcher.match(p)) for p in text.splitlines() if p.strip()]
    visibility_path = directory/'audit/historical_visibility.parquet'
    visibility = pd.read_parquet(visibility_path) if visibility_path.exists() else pd.DataFrame()
    visible = {(r.bvd_id, r.scraped_page_id): r._asdict() for r in visibility.itertuples(index=False)}
    rows, evidence = [], []
    grouped = {cid: group for cid, group in cp.groupby('bvd_id')}
    for company in cohort.itertuples():
        rel = grouped.get(company.bvd_id, cp.iloc[:0])
        unique = sorted(set(rel.content_id)); pids = sorted(set(rel.scraped_page_id))
        name = set(distinctive_company_tokens(company.company_name))
        f = {'bvd_id': company.bvd_id}
        for channel, phrases in channels.items():
            # Acronyms remain separate indicators, not explicit/adjacent phrase support.
            effective = phrases if channel.startswith('acronym_') else phrases-acronyms
            values = [len(hits[cid] & effective) for cid in unique]
            stats = summarize(values, {'support_cap': config['support_cap'], 'top_k': [3, 5]})
            stats.pop('support')
            for key, value in stats.items():
                f[f'lex2_{channel}_{key}'] = value
            for field, mapping in [('title', title_hits), ('heading', heading_hits)]:
                f[f'lex2_{channel}_{field}_presence'] = int(any(mapping[pid] & effective for pid in pids)) if unique else None
            f[f'lex2_{channel}_name_paragraph_support'] = min(config['support_cap'], sum(
                any(ts & name and matches & effective for ts, matches in paragraph_cache.get(cid, [])) for cid in unique)) if unique else None
            if channel.startswith('acronym_'):
                f[f'lex2_{channel}_context_support'] = min(config['support_cap'], sum(
                    any(ts & context and matches & phrases for ts, matches in paragraph_cache.get(cid, [])) for cid in unique)) if unique else None
        rows.append(f)
        explicit = channels['band_explicit_ai']-acronyms
        if company.label in ('UNKNOWN', 'EXCLUDE'):
            for relrow in rel.sort_values('scraped_page_id').itertuples():
                matched = hits[relrow.content_id] & explicit
                if not matched:
                    continue
                page = pages.loc[relrow.scraped_page_id]
                old = visible.get((company.bvd_id, relrow.scraped_page_id), {})
                status = old.get('historical_content_status', 'not_in_historical_package')
                text = contents[relrow.content_id]
                prefix = text[:int(old.get('visible_normalized_characters') or 0)]
                seen = matcher.match(prefix)
                for phrase in sorted(matched):
                    evidence.append({'bvd_id': company.bvd_id, 'label': company.label,
                        'scraped_page_id': int(relrow.scraped_page_id), 'phrase': phrase,
                        'url': page.get('url', ''), 'historical_content_status': status,
                        'phrase_in_visible_prefix': phrase in seen,
                        'interpretation': 'possible_label_evidence_mismatch_not_label_correction'})
    return pd.DataFrame(rows).set_index('bvd_id').sort_index(), pd.DataFrame(evidence)


def document_maps(directory):
    body = FoldPreprocessor.from_dataset(directory, 'body')
    titles = FoldPreprocessor.from_dataset(directory, 'titles_headings')
    # Old protections stay intact. Only this new full-development adapter clears
    # historical split prohibitions; explicit nested memberships replace them.
    body.prohibited = set(); titles.prohibited = set()
    return {'body': body, 'titles': titles}


def pooled_text(pre, ids):
    means, strongest = [], []
    width = len(pre.vocabulary)
    for cid in ids:
        docs = sorted(dict(pre.documents[cid]).items())
        rows, cols, values = [], [], []
        for row, (_, text) in enumerate(docs):
            for term, count in Counter(ngrams(text, pre.config['text']['ngram_max'])).items():
                if term in pre.vocabulary:
                    col = pre.vocabulary[term]
                    rows.append(row); cols.append(col); values.append((1+np.log(count))*pre.idf[col])
        matrix = normalize(sparse.csr_matrix((values, (rows, cols)), shape=(len(docs), width))) if docs and width else sparse.csr_matrix((len(docs), width))
        mean = sparse.csr_matrix(np.ones((1, len(docs)))/len(docs)) @ matrix if docs else sparse.csr_matrix((1, width))
        csc = matrix.tocsc(); selected_cols, selected_values = [], []
        for col in np.flatnonzero(np.diff(csc.indptr)):
            vals = csc.data[csc.indptr[col]:csc.indptr[col+1]]
            k = min(3, len(docs))
            selected_cols.append(col)
            selected_values.append(float(np.partition(vals, -min(k, len(vals)))[-k:].sum()/k))
        strongest.append(sparse.csr_matrix((selected_values, ([0]*len(selected_cols), selected_cols)), shape=(1, width)))
        means.append(mean)
    return {name: normalize(sparse.vstack(items, format='csr')) if width else sparse.csr_matrix((len(ids), 0))
            for name, items in [('mean', means), ('top3', strongest)]}


def grounded_vocab(base, train_ids, supervision, labels, lexicon, config):
    allowed_ids = set(train_ids)
    if any(r['bvd_id'] not in allowed_ids for r in supervision):
        raise ValueError('Non-training annotation in grounded vocabulary')
    stop = {t for word in lexicon['stopwords']+lexicon['generic_tokens'] for t in tokens(word)}
    allow = set(lexicon['acronyms']+lexicon['technical_unigrams'])
    allow.update(' '.join(tokens(p)) for e in lexicon['categories'].values() for p in e['aliases'])
    supports = {'all': defaultdict(set), 'keep_exclude': defaultdict(set)}
    rejected = Counter(); rejected_terms = Counter()
    for r in supervision:
        if not r['eligible']:
            rejected['ineligible_source'] += 1; continue
        cid = r['bvd_id']
        identifiers = set(tokens(base.names.get(cid, ''))+tokens(str(r.get('normalized_domain') or '').replace('.', ' ')))
        for phrase in set(ngrams(r['phrase'], config['phrase_maximum_tokens'])):
            ts = phrase.split(); reason = None
            if any(any(c.isdigit() for c in t) for t in ts): reason = 'numeric_token'
            elif set(ts) & identifiers: reason = 'source_identifier'
            elif all(t in stop for t in ts): reason = 'generic_only'
            elif len(ts) == 1 and phrase not in allow: reason = 'nontechnical_unigram'
            elif any(len(t) < 3 and t not in stop and t not in allow for t in ts): reason = 'uninformative_short_token'
            if reason:
                rejected[reason] += 1
                rejected_terms[(r['signal_type'], phrase, reason)] += 1
                continue
            key = (r['signal_type'], phrase)
            supports['all'][key].add(base.groups[cid])
            if labels[cid] in ('KEEP', 'EXCLUDE'):
                supports['keep_exclude'][key].add(base.groups[cid])
    matcher = PhraseMatcher(k[1] for k in supports['all'])
    df = defaultdict(set); matched_content = {}
    evidence_groups = set()
    for cid in train_ids:
        for identity, text in base.documents[cid]:
            if text.strip(): evidence_groups.add(base.groups[cid])
            if identity not in matched_content: matched_content[identity] = matcher.match(text)
            for term in matched_content[identity]: df[term].add(base.groups[cid])
    accepted = {}; records = []
    for origin, candidates in supports.items():
        accepted[origin] = set()
        for key, groups in sorted(candidates.items()):
            frequency = len(df[key[1]])/max(len(evidence_groups), 1)
            reason = 'accepted'
            if len(groups) < config['phrase_minimum_groups']: reason = 'insufficient_training_groups'
            elif frequency > config['phrase_maximum_group_fraction'] and key[1] not in allow: reason = 'common_corpus_term'
            if reason == 'accepted': accepted[origin].add(key)
            records.append({'origin': origin, 'channel': key[0], 'phrase': key[1], 'reason': reason,
                'source_group_ids': sorted(groups), 'source_group_count': len(groups),
                'corpus_group_frequency': frequency,
                'generic_token_fraction': sum(t in stop for t in key[1].split())/len(key[1].split())})
    a, b = accepted['all'], accepted['keep_exclude']
    if not b <= a: raise ValueError('Grounded subset invariant violated')
    def composition(origin):
        kept = [r for r in records if r['origin'] == origin and r['reason'] == 'accepted']
        return {'channels': dict(Counter(r['channel'] for r in kept)),
                'mean_corpus_frequency': float(np.mean([r['corpus_group_frequency'] for r in kept])) if kept else None,
                'mean_generic_fraction': float(np.mean([r['generic_token_fraction'] for r in kept])) if kept else None}
    diagnostic = {'all_size': len(a), 'keep_exclude_size': len(b), 'intersection': len(a & b),
        'union': len(a | b), 'jaccard': len(a & b)/len(a | b) if a | b else 1.,
        'all_unique_examples': [list(k) for k in sorted(a-b)[:30]], 'keep_exclude_unique_examples': [],
        'composition': {origin: composition(origin) for origin in accepted},
        'lexical_rejection_counts': dict(rejected),
        'meaningful_difference_flag': len(a-b)/max(len(a), 1) >= config['vocabulary_change_fraction'],
        'training_company_ids': sorted(train_ids), 'training_group_ids': sorted({base.groups[c] for c in train_ids}),
        'annotation_source_hash': digest(canonical(supervision)),
        'common_frequency_reference_groups': sorted(evidence_groups)}
    records.extend({'origin': 'all_lexical_filter', 'channel': key[0], 'phrase': key[1],
                    'reason': key[2], 'source_annotation_occurrences': count}
                   for key, count in sorted(rejected_terms.items()))
    return {key: i for i, key in enumerate(sorted(a))}, records, diagnostic


def phrase_matrix(base, ids, vocabulary):
    matcher = PhraseMatcher(key[1] for key in vocabulary)
    phrase_columns = defaultdict(list)
    for key, index in vocabulary.items(): phrase_columns[key[1]].append(index)
    cache = {}; rows, cols = [], []
    for row, cid in enumerate(ids):
        found = set()
        for identity, text in base.documents[cid]:
            if identity not in cache: cache[identity] = matcher.match(text)
            found.update(cache[identity])
        for term in sorted(found):
            for col in phrase_columns[term]: rows.append(row); cols.append(col)
    matrix = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(ids), len(vocabulary)))
    return normalize(matrix) if len(vocabulary) else matrix


def prepare_features(root, identity, partition, documents, frame, numeric, lexical, saturated, lexicon, config):
    train_ids = partition['train_ids']; ids = sorted(set(train_ids+partition['evaluation_ids']))
    key = digest(canonical({'experiment': identity, 'training_ids': train_ids, 'transform_ids': ids}))
    directory = Path(root)/'preprocessing'/key
    marker = valid_checkpoint(directory)
    if marker:
        return joblib.load(directory/'features.joblib'), directory, True
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    blocks, fitted, names = {}, {}, {}
    for channel, base in documents.items():
        pre = ExperimentText(base.documents, base.groups, base.config, base.names)
        pre.fit(train_ids, [])
        for pooling, matrix in pooled_text(pre, ids).items():
            blocks[channel+'_'+pooling] = matrix
            names[channel+'_'+pooling] = [channel+':'+term for term in pre.vocabulary]
        compact = copy.copy(pre); compact.documents = {}; compact.names = {}
        fitted[channel] = compact
    labels = frame.set_index('bvd_id').label.to_dict()
    supervision = documents['body'].training_supervision(train_ids)
    vocabulary, records, diagnostic = grounded_vocab(documents['body'], train_ids, supervision, labels, lexicon, config)
    blocks['G2'] = phrase_matrix(documents['body'], ids, vocabulary)
    names['G2'] = [channel+':'+term for channel, term in vocabulary]
    available = frame[frame.bvd_id.isin(train_ids) & frame.state.eq('PROCESSABLE')].bvd_id.tolist()
    sources = {k: (numeric, columns) for k, columns in feature_blocks(numeric.columns).items()}
    sources.update(L2=(lexical, sorted(lexical.columns)), S=(saturated, sorted(saturated.columns)))
    for name, (values, columns) in sources.items():
        processor = NumericBlock().fit(values.loc[available], columns)
        blocks[name] = processor.transform(values.loc[ids]); fitted[name] = processor
        names[name] = list(processor.imputer.get_feature_names_out(columns)[processor.keep])
    result = {'ids': ids, 'blocks': blocks, 'names': names, 'fitted': fitted,
              'grounded_vocabulary': vocabulary, 'training_ids': train_ids}
    temporary = directory/'features.joblib.tmp'; joblib.dump(result, temporary, compress=3)
    temporary.replace(directory/'features.joblib')
    write_json(directory/'grounded_diagnostic.json', diagnostic)
    write_json(directory/'grounded_terms.json', records)
    write_json(directory/'vocabularies.json', {channel: fitted[channel].export_vocabulary() for channel in documents})
    files = ['features.joblib', 'grounded_diagnostic.json', 'grounded_terms.json', 'vocabularies.json']
    write_json(directory/'complete.json', {'training_ids': train_ids, 'transform_ids': ids,
        'seconds': time.monotonic()-started, 'files': {name: file_hash(directory/name) for name in files}})
    return result, directory, False


def candidate_matrix(cache, candidate):
    return sparse.hstack([cache['blocks'][b] for b in CANDIDATES[candidate]], format='csr')
