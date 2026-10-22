"""Allowlisted deterministic evidence features, with no annotation inputs."""
from __future__ import annotations

import math
from collections import defaultdict
from urllib.parse import urlsplit, unquote

import numpy as np

from .attribution_features import distinctive_company_tokens, parse_headings
from .company_modeling_common import tokens, has_phrase, summarize

PAGE_FEATURES = ('path_depth', 'is_root', 'url_characters', 'heading_words', 'title_words', 'paragraph_count')
RELATION_FEATURES = ('name_title_overlap', 'name_heading_overlap', 'name_body_overlap', 'same_host', 'subdomain_host', 'cross_host', 'shared_domain')


def build_features(source, quarantine, config):
    by_domain = defaultdict(list)
    content_tokens = {k: tokens(v['body_text']) for k, v in source['contents'].items()}
    lexical = {}
    for cid, ts in content_tokens.items():
        lexical[cid] = {channel: sum(has_phrase(ts, phrase) for phrase in phrases) for channel, phrases in config['lexicon'].items()}
    for p in source['pages']:
        try:
            path = urlsplit(p['url']).path
        except ValueError:
            path = ''
        depth = len([part for part in unquote(path).split('/') if part])
        p['headings_text'] = parse_headings(p['headings'])
        p.update(path_depth=depth, is_root=int(depth == 0), url_characters=len(p['url'] or ''),
                 heading_words=len(tokens(p['headings_text'])), title_words=len(tokens(p['title'])),
                 paragraph_count=sum(bool(s.strip()) for s in source['contents'][p['content_id']]['body_text'].splitlines()),
                 predictive_eligible=p['usable'] and p['content_id'] not in quarantine)
        by_domain[p['website_id']].append(p)
    relations = defaultdict(list)
    for r in source['relations']:
        relations[r['bvd_id']].append(r)
    cp, features, states = [], [], []
    error_domains = {e['website_id'] for e in source['errors']}
    websites = {w['website_id']: w for w in source['websites']}
    for company in source['companies']:
        cid = company['bvd_id']; rs = relations[cid]
        all_pages = {p['scraped_page_id']: p for r in rs for p in by_domain[r['website_id']]}
        usable = [p for p in all_pages.values() if p['usable']]
        predictive = [p for p in usable if p['predictive_eligible']]
        unique = sorted({p['content_id'] for p in predictive})
        all_unique = {p['content_id'] for p in usable}
        failed = bool(error_domains & {r['website_id'] for r in rs})
        state = 'INPUT_ERROR' if failed else 'PROCESSABLE' if usable else 'NO_EVIDENCE'
        states.append({'bvd_id': cid, 'state': state, 'reason': 'required_file_error' if failed else 'usable_text' if usable else 'no_usable_extracted_text',
                       'predictive_content_available': bool(unique)})
        name = set(distinctive_company_tokens(company['company_name']))
        rel_values = defaultdict(list)
        for p in sorted(usable, key=lambda p: p['scraped_page_id']):
            d = company['normalized_domain']; h = p['final_host']
            row = {'bvd_id': cid, 'scraped_page_id': p['scraped_page_id'], 'content_id': p['content_id'],
                'predictive_eligible': p['predictive_eligible'],
                'name_title_overlap': len(name & set(tokens(p['title'])))/len(name) if name else None,
                'name_heading_overlap': len(name & set(tokens(p['headings_text'])))/len(name) if name else None,
                'name_body_overlap': len(name & set(content_tokens[p['content_id']]))/len(name) if name else None,
                'same_host': int(h == d), 'subdomain_host': int(bool(d) and h.endswith('.'+d)),
                'cross_host': int(h != d and not (d and h.endswith('.'+d))),
                'shared_domain': int(any(r['shared_domain'] for r in rs))}
            cp.append(row)
            if p['predictive_eligible']:
                for key in RELATION_FEATURES:
                    if row[key] is not None:
                        rel_values[key].append(row[key])
        lengths = [source['contents'][key]['word_count'] for key in unique]
        f = {'bvd_id': cid, 'availability_usable': int(bool(usable)), 'availability_predictive': int(bool(unique)),
             'availability_acquired_records': sum(websites[r['website_id']]['acquired_records'] for r in rs),
             'availability_acquired_success': sum(websites[r['website_id']]['acquired_success'] or 0 for r in rs),
             'availability_extraction_records': sum(websites[r['website_id']]['extraction_records'] for r in rs),
             'availability_extraction_not_success': sum(websites[r['website_id']]['extraction_not_success'] for r in rs),
             'availability_successful_pages': len(usable), 'availability_unique_contents': len(all_unique),
             'availability_predictive_contents': len(unique), 'availability_words': sum(lengths),
             'availability_log_pages': math.log1p(len(usable)), 'availability_log_words': math.log1p(sum(lengths)),
             'duplicates_fraction': 1-len(all_unique)/len(usable) if usable else None,
             'duplicates_quarantined_contents': len(all_unique)-len(unique),
             'relationship_shared': int(any(r['shared_domain'] for r in rs)),
             'relationship_attribution_unresolved': int(any(r['attribution_unresolved'] for r in rs))}
        for q in (0, 25, 50, 75, 95, 100):
            f[f'structure_words_p{q}'] = float(np.percentile(lengths, q)) if lengths else None
        for key in PAGE_FEATURES:
            vals = [p[key] for p in predictive]
            f['structure_'+key+'_mean'] = sum(vals)/len(vals) if vals else None
        for key in RELATION_FEATURES:
            vals = rel_values[key]
            f['relationship_'+key+'_maximum'] = max(vals) if vals else None
            f['relationship_'+key+'_mean'] = sum(vals)/len(vals) if vals else None
        # A fixed set of language bins avoids data-fitted categorical vocabularies.
        for language in ('de', 'en', 'missing', 'other'):
            matched = sum((p['language'] if p['language'] in ('de', 'en', 'missing') else 'other') == language for p in predictive)
            f['language_'+language+'_proportion'] = matched/len(predictive) if predictive else None
        for channel, terms in config['lexicon'].items():
            for key, value in summarize([lexical[content][channel] for content in unique], config['aggregation']).items():
                f[f'lexical_{channel}_{key}'] = value
            for field in ('title', 'headings_text'):
                f[f'lexical_{channel}_{field}_presence'] = int(any(has_phrase(tokens(p[field]), term) for p in predictive for term in terms)) if predictive else None
            f[f'lexical_{channel}_name_paragraph_support'] = min(config['aggregation']['support_cap'], sum(
                any(name & set(tokens(paragraph)) and any(has_phrase(tokens(paragraph), term) for term in terms)
                    for paragraph in source['contents'][content]['body_text'].splitlines()) for content in unique)) if unique else None
        features.append(f)
    return cp, features, states


def feature_dictionary(features):
    # Only this explicitly constructed table is predictive. IDs are join keys.
    return {key: {'block': key.split('_')[0], 'dtype': 'numeric', 'missing': 'null; never impute from targets',
                  'definition': key, 'predictive': True,
                  'unit': 'company', 'source': 'deduplicated text and company-specific page relationships',
                  'aggregation': ('top-k mean over available unique contents' if '_mean' in key and '_top' in key else
                                  'fraction of eligible unique contents with support' if key.startswith('lexical_') and key.endswith('_proportion') else
                                  'binary presence' if key.endswith('_presence') else
                                  'count/context; not a relevance score' if key.startswith('availability_') else
                                  'deterministic statistic named by suffix; see methodology'),
                  'size_bias_note': 'Raw counts/context require ablation; never an exclusion rule'}
            for key in features[0] if key != 'bvd_id'} if features else {}
