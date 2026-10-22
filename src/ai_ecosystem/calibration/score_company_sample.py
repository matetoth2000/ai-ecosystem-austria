"""Score registered sample companies with the two frozen company selectors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import time

import joblib
import pandas as pd

from ai_ecosystem.db.init_db import find_project_root, resolve_database_path
from ai_ecosystem.discovery.discovery_utils import sample_exists, get_sample_company_ids
from .build_company_evidence_packages import select_companies
from .company_modeling_common import canonical, digest, file_hash
from .company_modeling_features import build_features
from .company_modeling_groups import leakage_groups
from .company_modeling_sources import load_sources
from .company_tuning_contract import valid_checkpoint
from .company_tuning_features import expanded_lexical


MODELS = ('reference_logistic_size', 'cleaned_grounded_combined')
DEFAULT_FROZEN = 'data/interim/calibration/company_model_experiments/company_ai_relevance_cleanup_v3/frozen'


def sample_company_ids(database, sample_id):
    """Exact membership first; legacy domain samples retain their firm expansion."""
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if not sample_exists(connection, sample_id):
            raise ValueError(f'Unknown sample ID: {sample_id}')
        explicit = get_sample_company_ids(connection, sample_id)
        if explicit:
            return sorted(set(explicit))
        columns = {r['name'] for r in connection.execute('PRAGMA table_info(sample_sets)')}
        if 'sample_unit' in columns:
            unit = connection.execute('SELECT sample_unit FROM sample_sets WHERE sample_id=?', (sample_id,)).fetchone()[0]
            if unit == 'company':
                return []  # An empty explicit sample must not expand to sister firms.
        return sorted(c.company_id for c in select_companies(connection, sample_id=sample_id))


def read_context(directory):
    """Frozen raw overlap context only; never read targets or phrase supervision."""
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    config = json.loads((directory/'config_snapshot.json').read_text())
    if digest(canonical(config)) != manifest['config_hash']:
        raise ValueError('Dataset configuration snapshot hash mismatch')
    result = {}
    for name, key in [('companies', 'companies'), ('company_websites', 'relations'),
                      ('pages', 'pages'), ('contents', 'contents')]:
        path = directory/(name+'.parquet')
        if file_hash(path) != manifest['tables'][name]['file_hash']:
            raise ValueError(f'Frozen overlap context hash mismatch: {name}')
        result[key] = pd.read_parquet(path).to_dict('records')
    result['contents'] = {r['content_id']: r for r in result['contents']}
    return result, config, manifest


def overlap_context(source, context):
    """Replace refreshed sample websites in the fixed historical overlap inventory."""
    ids = {c['bvd_id'] for c in source['companies']}
    domains = {w['website_id'] for w in source['websites']}
    pages = [p for p in context['pages'] if p['website_id'] not in domains] + source['pages']
    contents = {**context['contents'], **source['contents']}
    used = {p['content_id'] for p in pages}
    return {
        'companies': [c for c in context['companies'] if c['bvd_id'] not in ids] + source['companies'],
        'relations': [r for r in context['relations'] if r['bvd_id'] not in ids] + source['relations'],
        'pages': pages, 'contents': {cid: contents[cid] for cid in used},
    }


def prepare_inputs(source, context, config, lexicon, scoring_config):
    """Reuse frozen feature definitions; retain context but output selected IDs only."""
    if source['errors']:
        raise ValueError(f'INPUT_ERROR: {source["errors"][:5]}')
    _, _, quarantine, _ = leakage_groups(overlap_context(source, context), config)
    cp, features, states = build_features(source, quarantine, config)
    companies = pd.DataFrame(source['companies'])
    numeric = pd.DataFrame(features).set_index('bvd_id').sort_index()
    states = pd.DataFrame(states).set_index('bvd_id').sort_index()
    pages = pd.DataFrame(source['pages'], columns=list(dict.fromkeys(
        ['scraped_page_id', 'title', 'headings_text'] + [k for p in source['pages'] for k in p])))
    relations = pd.DataFrame(cp, columns=list(dict.fromkeys(
        ['bvd_id', 'scraped_page_id', 'content_id', 'predictive_eligible'] + [k for r in cp for k in r])))
    relations['predictive_eligible'] = relations['predictive_eligible'].astype(bool)
    contents = pd.DataFrame(list(source['contents'].values()), columns=['content_id', 'body_text', 'word_count'])
    # The existing lexical loader consumes these tables. Its optional historical
    # diagnostic sees an absent label, never a fabricated relevance classification.
    with tempfile.TemporaryDirectory(prefix='company-score-input-') as tmp:
        for name, frame in [('pages', pages), ('contents', contents), ('company_page_features', relations)]:
            frame.to_parquet(Path(tmp)/(name+'.parquet'), index=False)
        lexical, _ = expanded_lexical(tmp, companies.assign(label=None), lexicon, scoring_config)
    documents = {channel: {c['bvd_id']: {} for c in source['companies']} for channel in ('body', 'titles')}
    lookup = {p['scraped_page_id']: p for p in source['pages']}
    for row in cp:
        if not row['predictive_eligible']:
            continue
        cid = row['bvd_id']; content_id = row['content_id']; page = lookup[row['scraped_page_id']]
        documents['body'][cid][content_id] = source['contents'][content_id]['body_text']
        text = str(page['title'] or '')+'\n'+str(page['headings_text'] or '')
        documents['titles'][cid][digest(text)] = text
    documents = {ch: {cid: sorted(d.items()) for cid, d in values.items()} for ch, values in documents.items()}
    return documents, numeric, lexical, states


def score_sample(sample_id, output, *, database=None, root=None, frozen_directory=None):
    started = time.monotonic()
    def progress(stage):
        print(f'[{time.monotonic() - started:.1f}s] {stage}', flush=True)

    root = Path(root or find_project_root())
    database = Path(database or resolve_database_path(root))
    frozen = Path(frozen_directory or root/DEFAULT_FROZEN)
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite scores: {output}')
    ids = sample_company_ids(database, sample_id)
    if not ids:
        raise ValueError(f'Sample has no company members: {sample_id}')
    progress(f'Sample resolved: {len(ids)} companies')
    bundles = {}
    for name in MODELS:
        if valid_checkpoint(frozen/name) is None:
            raise ValueError(f'Incomplete frozen model: {name}')
        bundle = joblib.load(frozen/name/'model.joblib')
        if bundle.candidate != name:
            raise ValueError(f'Frozen candidate mismatch: {name}')
        bundles[name] = bundle
    reference, challenger = (bundles[n] for n in MODELS)
    if reference.provenance['identity_hash'] != challenger.provenance['identity_hash']:
        raise ValueError('Models are from different freezes')
    dataset = root/reference.provenance['config']['dataset_directory']
    context, config, _ = read_context(dataset)
    if config != reference.fitted['body'].config or config != challenger.fitted['body'].config:
        raise ValueError('Frozen model / dataset configuration mismatch')
    progress('Frozen models and overlap context loaded')
    source = load_sources(database, root, config, company_ids=ids)
    progress(f'Source evidence loaded: {len(source["websites"])} websites, {len(source["pages"])} extracted pages')
    documents, numeric, lexical, states = prepare_inputs(
        source, context, config, challenger.provenance['lexicon'], challenger.provenance['config'])
    progress('Company features prepared')
    result = states.loc[ids, ['state', 'reason', 'predictive_content_available']].reset_index()
    for name, bundle in bundles.items():
        result[name] = bundle.score(ids, documents, numeric, lexical, states.state).score.to_numpy()
        progress(f'Frozen model scored: {name}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='') as handle:
        result.to_csv(handle, index=False)
    progress(f'CSV written: {output}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-id', required=True, help='Registered company or legacy domain sample')
    parser.add_argument('--output', required=True, help='New CSV path; existing files are never overwritten')
    parser.add_argument('--database', help='Optional read-only source SQLite database')
    parser.add_argument('--frozen-directory', help='Frozen model directory (defaults to cleanup_v3/frozen)')
    args = parser.parse_args()
    result = score_sample(args.sample_id, args.output, database=args.database, frozen_directory=args.frozen_directory)
    print(f'Wrote {len(result)} companies to {args.output}; states: {result.state.value_counts().to_dict()}')


if __name__ == '__main__':
    main()
