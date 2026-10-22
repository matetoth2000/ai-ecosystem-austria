from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy import sparse
from threadpoolctl import threadpool_limits
import yaml

from ai_ecosystem.calibration.company_modeling_common import digest, file_hash
from ai_ecosystem.calibration.company_modeling_preprocessing import FoldPreprocessor
from ai_ecosystem.calibration.company_experiment_features import ExperimentText, NumericBlock
from ai_ecosystem.calibration.company_tuning_contract import (CANDIDATES, Progress, load_config,
    load_full_cohort, make_schedule, valid_checkpoint, write_json)
from ai_ecosystem.calibration.company_tuning_features import (PhraseMatcher, grounded_vocab,
    phrase_matrix, pooled_text, saturation_features, expanded_lexical, candidate_matrix,
    document_maps, prepare_features)
from ai_ecosystem.calibration.company_tuning_metrics import (operational_curve, select_inner,
    paired_uncertainty, aggregate_outer)
from ai_ecosystem.calibration.run_company_model_tuning import run, fit_candidate
from ai_ecosystem.calibration.run_company_model_experiments import fit_predict

ROOT = Path(__file__).resolve().parents[1]


def fixture(root):
    data = root/'dataset'; data.mkdir()
    tables = {name: [] for name in ('companies', 'splits', 'operational_states', 'targets',
        'evaluation_only/internal_holdout_targets', 'audit/calibration_sources', 'audit/visibility_gaps',
        'audit/historical_visibility', 'company_features', 'pages', 'contents', 'company_page_features',
        'supervision/phrase_candidates')}
    for i in range(72):
        cid = f'firm{i:03}'; label = ['KEEP', 'EXCLUDE', 'UNKNOWN', 'UNKNOWN'][i % 4]
        state = 'NO_EVIDENCE' if i % 4 == 3 else 'PROCESSABLE'
        split = ['development', 'model_selection', 'internal_holdout'][i//24]
        tables['companies'].append({'bvd_id': cid, 'company_name': cid, 'normalized_domain': cid+'.at'})
        tables['splits'].append({'bvd_id': cid, 'leakage_group_id': cid, 'split': split, 'development_fold': i % 5 if split == 'development' else None})
        tables['operational_states'].append({'bvd_id': cid, 'state': state})
        tables['evaluation_only/internal_holdout_targets' if split == 'internal_holdout' else 'targets'].append({'bvd_id': cid, 'label': label})
        tables['audit/calibration_sources'].append({'bvd_id': cid, 'calibration_source': 'pilot' if i % 3 == 0 else 'enriched'})
        tables['audit/visibility_gaps'].append({'bvd_id': cid, 'current_pages_not_model_visible': 0})
        f = {'bvd_id': cid, 'availability_usable': int(state == 'PROCESSABLE'), 'availability_predictive': int(state == 'PROCESSABLE'),
             'availability_predictive_contents': 1, 'availability_words': 20+i, 'availability_acquired_success': 1,
             'availability_acquired_records': 1, 'availability_successful_pages': 1, 'availability_unique_contents': 1,
             'structure_words_p50': 20+i, 'relationship_shared': 0, 'structure_path_depth_mean': i % 3,
             'language_de_proportion': i % 2, 'language_en_proportion': 1-i % 2}
        for band in ('explicit_ai', 'adjacent', 'ambiguous'):
            f['lexical_'+band+'_support'] = 1
            f['lexical_'+band+'_presence'] = int(label == 'KEEP')
        tables['company_features'].append(f)
        if state == 'NO_EVIDENCE': continue
        text = ('Machine learning robotics neural network' if label == 'KEEP' else 'Local trade services and hospitality')+' '+cid
        content = digest(text)
        tables['contents'].append({'content_id': content, 'body_text': text})
        tables['pages'].append({'scraped_page_id': i, 'title': cid+' Company', 'headings_text': 'About', 'url': f'https://{cid}.at/'})
        tables['company_page_features'].append({'bvd_id': cid, 'scraped_page_id': i, 'content_id': content, 'predictive_eligible': True})
        tables['audit/historical_visibility'].append({'bvd_id': cid, 'scraped_page_id': i, 'historical_content_status': 'complete', 'visible_normalized_characters': len(text)})
        tables['supervision/phrase_candidates'].append({'bvd_id': cid, 'eligible': True,
            'phrase': 'machine learning' if label == 'KEEP' else 'trade services', 'signal_type': 'ai' if label == 'KEEP' else 'conventional',
            'normalized_domain': cid+'.at'})
    manifest = {'canonical_build_hash': 'synthetic', 'holdout_targets': 'evaluation_only/internal_holdout_targets.parquet',
                'predictive_allowlist': list(k for k in tables['company_features'][0] if k != 'bvd_id'), 'tables': {}}
    for name, records in tables.items():
        path = data/(name+'.parquet'); path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(records).to_parquet(path, index=False)
        manifest['tables'][name] = {'file_hash': file_hash(path)}
    write_json(data/'manifest.json', manifest)
    source = yaml.safe_load((ROOT/'config/company_modeling_dataset.yaml').read_text())
    write_json(data/'config_snapshot.json', source)
    config, lexicon = load_config(ROOT/'config/company_model_tuning.yaml')
    config.update(dataset_directory=str(data), output_directory=str(root/'experiment'), seeds=[20260916],
                  outer_folds=2, inner_folds=2, logistic_c=[1.], bootstrap_replicates=3,
                  lexicon_path=str(ROOT/'config/company_tuning_lexicon.yaml'))
    path = root/'config.yaml'; path.write_text(yaml.safe_dump(config))
    return path, config, lexicon


def scored():
    return pd.DataFrame({'bvd_id': ['a', 'b', 'c', 'd', 'e'], 'label': ['KEEP', 'UNKNOWN', 'EXCLUDE', 'KEEP', 'UNKNOWN'],
        'state': ['PROCESSABLE']*4+['NO_EVIDENCE'], 'score': [4., 3., 2., 1., np.nan],
        'leakage_group_id': ['a', 'b', 'c', 'd', 'e']})


class TuningContractTests(unittest.TestCase):
    def test_full_cohort_preserves_source_and_new_group_folds(self):
        with tempfile.TemporaryDirectory() as temp:
            _, c, _ = fixture(Path(temp)); data = Path(c['dataset_directory'])
            before = file_hash(data/'evaluation_only/internal_holdout_targets.parquet')
            manifest, f, _ = load_full_cohort(data)
            self.assertEqual(f.label.isna().sum(), 0); self.assertEqual(len(f), 72)
            f.loc[f.bvd_id.eq('firm004'), 'leakage_group_id'] = 'firm000'
            folds, parts, total = make_schedule(f, c)
            self.assertEqual(total, 48)
            again, parts2, _ = make_schedule(f, c); pd.testing.assert_frame_equal(folds, again); self.assertEqual(parts, parts2)
            groups = f.set_index('bvd_id').leakage_group_id
            for part in parts:
                self.assertFalse(set(groups.loc[part['train_ids']]) & set(groups.loc[part['evaluation_ids']]))
            full, _ = load_config(ROOT/'config/company_model_tuning.yaml')
            _, _, total = make_schedule(f, full); self.assertEqual(total, 680)
            self.assertEqual(before, file_hash(data/'evaluation_only/internal_holdout_targets.parquet'))

    def test_progress_checkpoint_authority_and_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = Progress(root, 2); p.failure(RuntimeError('synthetic failure'))
            write_json(root/'progress.json', {'completed_fits': 999})
            fit = root/'fits'/'one'; fit.mkdir(parents=True); (fit/'result').write_text('ok')
            write_json(fit/'complete.json', {'files': {'result': file_hash(fit/'result')}})
            resumed = Progress(root, 2)
            self.assertEqual(resumed.state['completed_fits'], 1)
            self.assertEqual(resumed.state['failure_count'], 1); self.assertEqual(resumed.state['retry_count'], 1)
            self.assertGreaterEqual(resumed.state['elapsed_seconds'], p.state['elapsed_seconds'])
            (fit/'result').write_text('corrupt')
            with self.assertRaises(ValueError): valid_checkpoint(fit)

    def test_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            p, c, _ = fixture(Path(temp)); c['logistic_c'] = [-1]; p.write_text(yaml.safe_dump(c))
            with self.assertRaises(ValueError): load_config(p)


class TuningFeatureTests(unittest.TestCase):
    def setUp(self):
        self.c, self.lex = load_config(ROOT/'config/company_model_tuning.yaml')
        self.source = yaml.safe_load((ROOT/'config/company_modeling_dataset.yaml').read_text())

    def test_bilingual_boundaries(self):
        m = PhraseMatcher(['ki', 'künstliche intelligenz', 'machine learning'])
        self.assertEqual(m.match('Skifahren and machines learning'), set())
        self.assertEqual(m.match('KI: KÜNSTLICHE INTELLIGENZ; machine-learning'), {'ki', 'künstliche intelligenz', 'machine learning'})

    def test_reference_mean_parity_and_top3_zero_denominator(self):
        docs = {'a': [('1', 'robotics'), ('2', 'plain'), ('3', 'plain'), ('4', 'plain')],
                'b': [('5', 'robotics'), ('6', 'plain')], 'v': [('7', 'unseen validationword')]}
        pre = ExperimentText(docs, {c: c for c in docs}, self.source).fit(['a', 'b'], [])
        actual = pooled_text(pre, ['a', 'b', 'v']); original = pre.transform(['a', 'b', 'v'])
        np.testing.assert_allclose(actual['mean'].toarray(), original['text_mean'].toarray())
        robotic = pre.vocabulary['robotics']; plain = pre.vocabulary['plain']
        self.assertAlmostEqual(actual['top3'][0, robotic]/actual['top3'][0, plain], 1/3)
        self.assertAlmostEqual(actual['top3'][1, robotic]/actual['top3'][1, plain], 1.)
        self.assertEqual(actual['top3'][2].nnz, 0)

    def test_grounded_source_diagnostic_and_validation_exclusion(self):
        docs = {'a': [('1', 'machine learning robotics models')], 'b': [('2', 'machine learning robotics models')],
                'u': [('3', 'adaptive robotic perception')], 'v': [('4', 'adaptive robotic perception')], 'test': [('5', 'secret testing phrase')]}
        base = FoldPreprocessor(docs, {c: c for c in docs}, self.source)
        ids = ['a', 'b', 'u', 'v']; labels = {'a': 'KEEP', 'b': 'EXCLUDE', 'u': 'UNKNOWN', 'v': 'UNKNOWN'}
        supervision = [{'bvd_id': c, 'eligible': True, 'phrase': 'machine learning' if c in ('a', 'b') else 'adaptive robotic perception', 'signal_type': 'ai'} for c in ids]
        vocab, records, diagnostic = grounded_vocab(base, ids, supervision, labels, self.lex, self.c)
        self.assertGreater(diagnostic['all_size'], diagnostic['keep_exclude_size'])
        self.assertEqual(diagnostic['keep_exclude_unique_examples'], [])
        self.assertNotIn(('ai', 'secret testing phrase'), vocab)
        before = phrase_matrix(base, ['test'], vocab).toarray()
        self.assertEqual(before.sum(), 0)
        with self.assertRaises(ValueError):
            grounded_vocab(base, ids, supervision+[{'bvd_id': 'test'}], labels, self.lex, self.c)

    def test_generic_numeric_and_identifier_cleaning(self):
        docs = {c: [('text'+c, 'some local phrase robotics')] for c in ('a', 'b')}
        base = FoldPreprocessor(docs, {'a': 'a', 'b': 'b'}, self.source, {'a': 'Acme', 'b': 'Acme'})
        sup = [{'bvd_id': c, 'eligible': True, 'phrase': 'auf bei für 740 Acme robotics', 'signal_type': 'ai'} for c in docs]
        vocab, records, d = grounded_vocab(base, list(docs), sup, {'a': 'KEEP', 'b': 'EXCLUDE'}, self.lex, self.c)
        self.assertIn(('ai', 'robotics'), vocab)
        self.assertFalse(any('740' in phrase or 'acme' in phrase for _, phrase in vocab))
        self.assertNotIn(('ai', 'auf'), vocab)
        self.assertGreater(d['lexical_rejection_counts']['numeric_token'], 0)

    def test_corpus_filter_training_only_and_empty_vocabulary(self):
        docs = {c: [(c, 'special unusual phrase')] for c in ('a', 'b', 'held')}
        base = FoldPreprocessor(docs, {c: c for c in docs}, self.source)
        sup = [{'bvd_id': c, 'eligible': True, 'phrase': 'special unusual phrase', 'signal_type': 'ai'} for c in ('a', 'b')]
        vocab, records, d = grounded_vocab(base, ['a', 'b'], sup, {'a': 'KEEP', 'b': 'EXCLUDE'}, self.lex, self.c)
        self.assertEqual(vocab, {}); self.assertEqual(phrase_matrix(base, ['held'], vocab).shape, (1, 0))
        self.assertEqual(d['common_frequency_reference_groups'], ['a', 'b'])

    def test_saturation_missing_ratios_and_no_label_features(self):
        with tempfile.TemporaryDirectory() as temp:
            _, c, lex = fixture(Path(temp)); _, f, n = load_full_cohort(c['dataset_directory'])
            n.loc['firm000', 'availability_predictive_contents'] = 10000
            n.loc['firm000', 'availability_acquired_success'] = 20
            s, anomalies = saturation_features(n, c['size_caps'])
            self.assertAlmostEqual(s.loc['firm000', 'context_contents'], np.log1p(50))
            self.assertTrue(np.isnan(s.loc['firm000', 'context_acquisition_ratio'])); self.assertTrue(anomalies)
            features, evidence = expanded_lexical(c['dataset_directory'], f, lex, c)
            self.assertTrue(all(name.startswith('lex2_') for name in features.columns))
            f['label'] = 'UNKNOWN'
            again, _ = expanded_lexical(c['dataset_directory'], f, lex, c)
            pd.testing.assert_frame_equal(features, again)

    def test_reference_block_concatenation(self):
        blocks = {b: sparse.csr_matrix([[i, i+1]]) for i, b in enumerate(['body_mean', 'titles_mean', 'N', 'L', 'Z'])}
        actual = candidate_matrix({'blocks': blocks}, 'reference_logistic_size')
        np.testing.assert_array_equal(actual.toarray(), sparse.hstack(list(blocks.values())).toarray())

    def test_reference_fitted_predictions_match_old_estimator(self):
        with tempfile.TemporaryDirectory() as temp, threadpool_limits(limits=1):
            root = Path(temp); _, c, lex = fixture(root)
            _, f, n = load_full_cohort(c['dataset_directory']); _, parts, _ = make_schedule(f, c)
            l2, _ = expanded_lexical(c['dataset_directory'], f, lex, c)
            sat, _ = saturation_features(n, c['size_caps']); docs = document_maps(c['dataset_directory'])
            part = parts[0]
            cache, location, hit = prepare_features(root, 'test', part, docs, f, n, l2, sat, lex, c)
            old_blocks = dict(cache['blocks']); old_blocks['titles_headings_mean'] = old_blocks['titles_mean']
            old = {'ids': cache['ids'], 'blocks': old_blocks}
            train = f[f.bvd_id.isin(part['train_ids'])]; evaluation = f[f.bvd_id.isin(part['evaluation_ids'])]
            for new_name, old_name, strength in [('reference_logistic_size', 'logistic_size', 10.),
                                                ('reference_logistic_lexical', 'logistic_lexical', .1)]:
                _, expected, _ = fit_predict(old, old_name, {'C': strength}, 'mean', train, evaluation,
                                            {'maximum_iterations': 5000, 'seed': c['seeds'][0]})
                result, _ = fit_candidate(root, cache, location, part, new_name, strength, f, c, Progress(root, 2))
                np.testing.assert_allclose(result.score.to_numpy(), expected.score.to_numpy(), equal_nan=True)


class TuningMetricsTests(unittest.TestCase):
    def test_unknown_cost_not_standard_negative(self):
        row = operational_curve(scored()).iloc[49]
        self.assertEqual(row.selected_keep, 1); self.assertEqual(row.selected_unknown, 1); self.assertEqual(row.selected_exclude, 0)
        self.assertEqual(row.operational_precision, .5); self.assertEqual(row.standard_precision, 1.)
        self.assertEqual(row.keep_recall, .5); self.assertAlmostEqual(row.operational_f1, .5)
        self.assertAlmostEqual(row.standard_f1, 2/3); self.assertAlmostEqual(row.average_precision, 5/6)

    def test_inner_choice_and_outer_fraction(self):
        p = pd.concat([scored().assign(candidate='x', C=c, inner_fold=f) for f in (0, 1) for c in (.1, 1.)])
        table, choices, winner = select_inner(p, .01)
        self.assertEqual(winner['C'], .1)
        self.assertEqual(len(choices), 1)
        scored_outer = scored().assign(candidate='x', repetition=0, outer_fold=0, selected_fraction=winner['fraction'])
        rows, _, _, _ = aggregate_outer(scored_outer)
        self.assertTrue(rows.fraction.eq(winner['fraction']).all())

    def test_paired_bootstrap_identical_models(self):
        p = pd.concat([scored().assign(candidate=c, repetition=r, outer_fold=0, selected_fraction=.5)
                       for c in ('reference_logistic_size', 'other') for r in (0, 1)], ignore_index=True)
        c = {'bootstrap_seed': 7, 'bootstrap_replicates': 10}
        a = paired_uncertainty(p, c); b = paired_uncertainty(p, c)
        pd.testing.assert_frame_equal(a, b)
        self.assertTrue(a.paired_delta_lower.eq(0).all()); self.assertTrue(a.paired_delta_upper.eq(0).all())


class TuningWorkflowTests(unittest.TestCase):
    def test_failure_then_resume_preserves_completed_fits(self):
        with tempfile.TemporaryDirectory() as temp, threadpool_limits(limits=1):
            root = Path(temp); path, _, _ = fixture(root); calls = []
            def interrupted(*args, **kwargs):
                calls.append(1)
                if len(calls) == 3: raise RuntimeError('injected interruption before next fit')
                return fit_candidate(*args, **kwargs)
            with patch('ai_ecosystem.calibration.run_company_model_tuning.fit_candidate', side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, 'injected interruption'): run(path)
            output = root/'experiment'
            old = {str(p): file_hash(p) for p in (output/'fits').glob('*/predictions.parquet')}
            self.assertEqual(len(old), 2)
            run(path, resume=True)
            for name, expected in old.items(): self.assertEqual(file_hash(name), expected)
            progress = json.loads((output/'progress.json').read_text())
            self.assertEqual(progress['failure_count'], 1); self.assertEqual(progress['retry_count'], 1)
            self.assertEqual(progress['completed_fits'], 48)

    def test_synthetic_run_pause_resume_no_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp, threadpool_limits(limits=1):
            root = Path(temp); path, c, _ = fixture(root)
            hashes = {str(p): file_hash(p) for p in (root/'dataset').rglob('*') if p.is_file()}
            self.assertEqual(run(path, validate_only=True)['expected_fits'], 48)
            self.assertFalse((root/'experiment').exists())
            with patch('sqlite3.connect', side_effect=AssertionError('No database access')):
                self.assertTrue(run(path, stop_after_outer=1)['paused'])
                output = root/'experiment'
                fit = next((output/'fits').glob('*/predictions.parquet')); before = file_hash(fit)
                (output/'progress.json').unlink()
                result = run(path, resume=True)
                self.assertEqual(result['completed_fits'], 48); self.assertEqual(file_hash(fit), before)
                self.assertEqual(run(path, resume=True), result)
            for name, expected in hashes.items(): self.assertEqual(file_hash(name), expected)
            p = pd.read_parquet(output/'outer_predictions.parquet')
            self.assertEqual(set(p.candidate), set(CANDIDATES))
            self.assertTrue(p.loc[p.state.eq('NO_EVIDENCE'), 'score'].isna().all())
            self.assertTrue(p.loc[p.state.eq('PROCESSABLE') & p.label.eq('UNKNOWN'), 'score'].notna().all())
            for marker in (output/'fits').glob('*/complete.json'):
                self.assertEqual(json.loads(marker.read_text())['training_unknown'], 0)
            self.assertEqual(len(list((output/'preprocessing').glob('*/grounded_diagnostic.json'))), 6)
            with self.assertRaises(FileExistsError): run(path)
            c['support_cap'] += 1; path.write_text(yaml.safe_dump(c))
            with self.assertRaisesRegex(ValueError, 'identity mismatch'): run(path, resume=True)


if __name__ == '__main__': unittest.main()
