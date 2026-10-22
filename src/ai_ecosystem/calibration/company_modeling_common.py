"""Versioned contracts and deterministic normalization for company datasets."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import yaml

SPLITS = ('development', 'model_selection', 'internal_holdout')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def tokens(text):
    return re.findall(r'\w+', unicodedata.normalize('NFKC', str(text or '')).casefold())


def normalized_body(text):
    # Keep paragraphs and case for later attribution/context reconstruction.
    return '\n'.join(' '.join(line.split()) for line in unicodedata.normalize('NFKC', text).splitlines()).strip()


def has_phrase(text_tokens, phrase):
    needle = tokens(phrase)
    return bool(needle) and (' ' + ' '.join(needle) + ' ') in (' ' + ' '.join(text_tokens) + ' ')


def host(url):
    try:
        return (urlsplit(url if '://' in (url or '') else 'https://' + (url or '')).hostname or '').lower().removeprefix('www.').rstrip('.')
    except ValueError:
        return ''


def load_config(path):
    c = yaml.safe_load(Path(path).read_text())
    required = ('dataset_id', 'dataset_version', 'output_directory', 'extraction_version',
                'calibration_runs', 'package_directory', 'seed', 'split_fractions',
                'development_folds', 'aggregation', 'overlap', 'text', 'phrases', 'lexicon', 'lexicon_version')
    if not isinstance(c, dict) or any(k not in c or c[k] is None for k in required):
        raise ValueError('Missing required company dataset configuration')
    for key in ('dataset_id', 'dataset_version', 'extraction_version', 'lexicon_version'):
        if not isinstance(c[key], str) or not c[key].strip():
            raise ValueError(f'Invalid {key}')
    if not c['calibration_runs'] or len(set(c['calibration_runs'])) != len(c['calibration_runs']):
        raise ValueError('Calibration runs must be nonempty and unique')
    fractions = c['split_fractions']
    if len(fractions) != 3 or any(not math.isfinite(x) or x <= 0 for x in fractions) or abs(sum(fractions)-1) > 1e-8:
        raise ValueError('Split fractions must be positive and sum to one')
    for block, fields in {'aggregation': ['support_cap'], 'overlap': ['substantive_words', 'shingle_size', 'material_pages', 'fingerprint_size', 'maximum_fingerprint_bucket'], 'text': ['max_features', 'ngram_max'], 'phrases': ['ngram_max', 'minimum_training_groups']}.items():
        for key in fields:
            v = c[block][key]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f'Invalid {block}.{key}')
    if c['development_folds'] < 2 or any(not isinstance(k, int) or k < 1 for k in c['aggregation']['top_k']):
        raise ValueError('Invalid folds or top-k')
    for key in ('jaccard', 'smaller_site_fraction'):
        if not 0 < c['overlap'][key] <= 1:
            raise ValueError(f'Invalid overlap.{key}')
    if not c['lexicon'] or any(not values or len(set(values)) != len(values) for values in c['lexicon'].values()):
        raise ValueError('Lexical channels must contain unique phrases')
    return c


def summarize(values, config):
    ordered = sorted(values, reverse=True)
    result = {'maximum': max(ordered) if ordered else None,
              'presence': int(any(v > 0 for v in ordered)) if ordered else None,
              'proportion': sum(v > 0 for v in ordered)/len(ordered) if ordered else None,
              'capped_support': min(sum(v > 0 for v in ordered), config['support_cap']) if ordered else None,
              'support': len(ordered)}
    for k in config['top_k']:
        result[f'top{k}_mean'] = sum(ordered[:k])/len(ordered[:k]) if ordered else None
    return result
