"""Fold-local sparse text and supervised phrase vocabularies.

The dataset builder never fits these on real data. Callers must provide the
explicit training membership for each fold; transform has no annotation input.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

from .company_modeling_common import canonical, digest, tokens


def ngrams(text, maximum):
    ts = tokens(text)
    return [' '.join(ts[i:i+n]) for n in range(1, maximum+1) for i in range(len(ts)-n+1)]


class FoldPreprocessor:
    """Documents: company -> list of (content identity, text) for one channel.

    Instantiate separate objects for body and title/headings. Duplicate
    identities are pooled once per company. No text crosses page boundaries.
    Groups must be complete in fit; heldout IDs can be prohibited explicitly.
    """
    def __init__(self, documents, groups, config, company_names=None, prohibited_ids=()):
        self.documents = documents
        self.groups = groups
        self.config = config
        self.names = company_names or {}
        self.prohibited = set(prohibited_ids)

    @classmethod
    def from_dataset(cls, directory, channel='body'):
        """Load raw predictive text without opening targets or supervision."""
        import pandas as pd
        directory = Path(directory)
        if channel not in ('body', 'titles_headings'):
            raise ValueError('Unknown text channel')
        config = json.loads((directory/'config_snapshot.json').read_text())
        companies = pd.read_parquet(directory/'companies.parquet')
        splits = pd.read_parquet(directory/'splits.parquet')
        pages = pd.read_parquet(directory/'pages.parquet').set_index('scraped_page_id')
        content = pd.read_parquet(directory/'contents.parquet').set_index('content_id').body_text.to_dict()
        relationships = pd.read_parquet(directory/'company_page_features.parquet')
        documents = {cid: {} for cid in companies.bvd_id}
        for row in relationships.itertuples(index=False):
            if not row.predictive_eligible:
                continue
            if channel == 'body':
                identity, text = row.content_id, content[row.content_id]
            else:
                page = pages.loc[row.scraped_page_id]
                text = str(page.title or '')+'\n'+str(page.headings_text or '')
                identity = digest(text)
            documents[row.bvd_id][identity] = text
        result = cls({cid: sorted(docs.items()) for cid, docs in documents.items()},
                     splits.set_index('bvd_id').leakage_group_id.to_dict(), config,
                     companies.set_index('bvd_id').company_name.to_dict(),
                     splits.loc[splits.split == 'internal_holdout', 'bvd_id'].tolist())
        result.dataset_directory = directory
        return result

    def training_supervision(self, training_company_ids):
        """Explicitly filter supervision to training IDs before reading rows."""
        import pandas as pd
        ids = sorted(set(training_company_ids))
        if set(ids) & self.prohibited or set(ids) - self.documents.keys():
            raise ValueError('Invalid training supervision membership')
        if not ids:
            return []
        frame = pd.read_parquet(self.dataset_directory/'supervision/phrase_candidates.parquet',
                                filters=[('bvd_id', 'in', ids)])
        return frame.astype(object).where(pd.notna(frame), None).to_dict('records')

    def fit(self, training_company_ids, training_supervision):
        ids = sorted(set(training_company_ids))
        if not ids or set(ids) - self.documents.keys() or set(ids) & self.prohibited:
            raise ValueError('Invalid or prohibited training membership')
        gs = {self.groups[c] for c in ids}
        if any(g in gs and c not in ids for c, g in self.groups.items()):
            raise ValueError('Training membership splits a leakage group')
        supervision = list(training_supervision)
        if any(r['bvd_id'] not in ids for r in supervision):
            raise ValueError('Non-training supervision supplied to fit')
        df = defaultdict(set)
        for cid in ids:
            for _, text in self.documents[cid]:
                for term in set(ngrams(text, self.config['text']['ngram_max'])):
                    df[term].add(self.groups[cid])
        ordered = sorted(df, key=lambda term: (-len(df[term]), term))[:self.config['text']['max_features']]
        self.vocabulary = {term: i for i, term in enumerate(sorted(ordered))}
        self.idf = np.array([np.log((1+len(gs))/(1+len(df[term])))+1 for term in self.vocabulary])
        phrase_groups = defaultdict(set); settings = self.config['phrases']
        for r in supervision:
            if not r['eligible']:
                continue
            cid = r['bvd_id']; identifiers = set(tokens(self.names.get(cid, '')))
            identifiers.update(tokens(r.get('normalized_domain', '').replace('.', ' ')))
            for term in ngrams(r['phrase'], settings['ngram_max']):
                ts = term.split()
                if all(t.isnumeric() or t in settings['stopwords'] for t in ts) or any(t in identifiers for t in ts):
                    continue
                if any(len(t) < 3 and t not in settings['acronym_allowlist'] and t not in settings['stopwords'] for t in ts):
                    continue
                phrase_groups[(r['signal_type'], term)].add(self.groups[cid])
        self.phrase_vocabulary = {key: i for i, key in enumerate(sorted(k for k, support in phrase_groups.items() if len(support) >= settings['minimum_training_groups']))}
        self.provenance = {'training_company_ids': ids, 'training_group_ids': sorted(gs),
            'config_hash': digest(canonical(self.config)), 'normalization': 'nfkc_casefold_word_tokens_v1',
            'source_hash': digest(canonical({cid: self.documents[cid] for cid in ids})),
            'supervision_hash': digest(canonical(supervision)),
            'vocabulary_origin': 'training_group_document_frequency_and_training_company_grounded_phrases'}
        self.provenance['cache_key'] = digest(canonical(self.provenance))
        return self

    def transform(self, company_ids):
        if not hasattr(self, 'vocabulary'):
            raise ValueError('fit must precede transform')
        means, maxima, phrases = [], [], []
        for cid in company_ids:
            docs = sorted(dict(self.documents[cid]).items())
            rows, cols, vals = [], [], []
            phrase_support = Counter()
            for row, (_, text) in enumerate(docs):
                counts = Counter(ngrams(text, self.config['text']['ngram_max']))
                for term, count in counts.items():
                    if term in self.vocabulary:
                        col = self.vocabulary[term]; rows.append(row); cols.append(col); vals.append((1+np.log(count))*self.idf[col])
                candidates = set(ngrams(text, self.config['phrases']['ngram_max']))
                for key, column in self.phrase_vocabulary.items():
                    if key[1] in candidates:
                        phrase_support[column] += 1
            matrix = normalize(sparse.csr_matrix((vals, (rows, cols)), shape=(len(docs), len(self.vocabulary)))) if docs and self.vocabulary else sparse.csr_matrix((len(docs), len(self.vocabulary)))
            # Sparse multiplication, not dense page-by-vocabulary intermediates.
            mean = sparse.csr_matrix(np.ones((1, len(docs)))/len(docs)) @ matrix if docs else sparse.csr_matrix((1, len(self.vocabulary)))
            maximum = matrix.max(axis=0).tocsr() if docs and self.vocabulary else sparse.csr_matrix((1, len(self.vocabulary)))
            means.append(mean); maxima.append(maximum)
            phrases.append(sparse.csr_matrix(([v/max(len(docs), 1) for v in phrase_support.values()], ([0]*len(phrase_support), list(phrase_support))), shape=(1, len(self.phrase_vocabulary))))
        def stack(items, width):
            result = sparse.vstack(items, format='csr') if items else sparse.csr_matrix((0, width))
            return normalize(result) if width and result.shape[0] else result
        return {'text_mean': stack(means, len(self.vocabulary)), 'text_max': stack(maxima, len(self.vocabulary)),
                'phrase_proportion': sparse.vstack(phrases, format='csr') if phrases else sparse.csr_matrix((0, len(self.phrase_vocabulary)))}

    def export_vocabulary(self):
        return {**self.provenance, 'vocabulary': self.vocabulary,
                'phrase_vocabulary': [{'channel': k[0], 'phrase': k[1], 'column': v} for k, v in self.phrase_vocabulary.items()],
                'idf': self.idf.tolist()}
