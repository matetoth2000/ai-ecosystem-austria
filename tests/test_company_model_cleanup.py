from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
import yaml

from test_company_model_tuning import fixture, ROOT
from ai_ecosystem.calibration.company_cleanup_contract import configuration, validate_dataset
from ai_ecosystem.calibration.company_cleanup_features import CleanGrounded, phrase_presence
from ai_ecosystem.calibration.company_cleanup_reporting import freeze_decision
from ai_ecosystem.calibration.company_modeling_common import canonical, digest, file_hash
from ai_ecosystem.calibration.company_modeling_preprocessing import FoldPreprocessor
from ai_ecosystem.calibration.company_tuning_metrics import selection_count, operational_curve
from ai_ecosystem.calibration.company_tuning_contract import write_json
from ai_ecosystem.calibration.run_company_model_cleanup import run
from ai_ecosystem.calibration.run_company_model_tuning import run as run_parent


def grounded_fixture():
    config, lexicon = configuration(ROOT/'config/company_model_cleanup.yaml')
    docs = {'a': [('a', 'machine learning computer vision')],
            'b': [('b', 'machine learning computer vision')],
            'c': [('c', 'transport und logistik')], 'd': [('d', 'transport und logistik')],
            'test': [('test', 'computer vision future-only phrase')]}
    base = FoldPreprocessor(docs, {k: k for k in docs}, {}, {k: k+'name' for k in docs})
    labels = {'a': 'KEEP', 'b': 'KEEP', 'c': 'EXCLUDE', 'd': 'UNKNOWN', 'test': 'UNKNOWN'}
    records = []
    for cid in ('a', 'b', 'c', 'd'):
        for phrase in (['machine learning', 'computer vision'] if cid in ('a', 'b') else ['transport und logistik']):
            for channel in ('positive', 'context'):
                records.append({'bvd_id': cid, 'eligible': True, 'phrase': phrase,
                                'signal_type': channel, 'normalized_domain': cid+'name.at'})
    return config, lexicon, base, labels, records


class GroundedCleanupTests(unittest.TestCase):
    def test_explicit_allowlist_and_fragment_policy(self):
        c, lex, *_ = grounded_fixture(); cleaner = CleanGrounded(c['cleanup'], lex)
        for term in ('software', 'smart', 'intelligent', 'optimierung', 'auf', 'bei', 'für'):
            self.assertIsNotNone(cleaner.rejection(term, set()))
        for term in ('ai', 'mlops', 'robotik', 'transformer', 'edge ai', 'machine learning', 'transport und logistik'):
            self.assertIsNone(cleaner.rejection(term, set()))
        for phrase in ('bis hin', 'state of the art', 'art technology', 'innovative solutions', 'und transport', 'transport und', 'model 123'):
            self.assertIsNotNone(cleaner.rejection(phrase, set()))
        self.assertEqual(cleaner.rejection('machine learning', {'machine'}), 'source_identifier')

    def test_channel_canonicalization_alias_union_and_unknown_diagnostic(self):
        c, lex, base, labels, records = grounded_fixture()
        cleaner = CleanGrounded(c['cleanup'], lex).fit(base, ['a', 'b', 'c', 'd'], records, labels, ['a', 'b', 'c', 'd'])
        self.assertEqual(cleaner.diagnostic['all_size'], 3)
        self.assertEqual(cleaner.diagnostic['keep_exclude_size'], 2)
        self.assertEqual(cleaner.diagnostic['identical_vector_reduction'], 1)
        self.assertEqual(cleaner.aliases, [['computer vision', 'machine learning'], ['transport und logistik']])
        np.testing.assert_array_equal(cleaner.transform(base.documents, ['test']).toarray(), [[1., 0.]])
        self.assertEqual(cleaner.diagnostic['unknown_added'], ['transport und logistik'])

    def test_evaluation_text_and_annotations_cannot_fit_vocabulary(self):
        c, lex, base, labels, records = grounded_fixture(); ids = ['a', 'b', 'c', 'd']
        a = CleanGrounded(c['cleanup'], lex).fit(base, ids, records, labels, ids)
        base.documents['test'] = [('changed', 'unrelated quantum classifier')]
        b = CleanGrounded(c['cleanup'], lex).fit(base, ids, records, labels, ids)
        self.assertEqual(a.aliases, b.aliases); self.assertEqual(a.provenance, b.provenance)
        with self.assertRaisesRegex(ValueError, 'Non-training annotation'):
            b.fit(base, ids, records+[dict(records[0], bvd_id='test')], labels, ids)
        base.groups['test'] = 'a'
        with self.assertRaisesRegex(ValueError, 'splits a leakage group'):
            b.fit(base, ids, records, labels, ids)

    def test_empty_constant_and_ineligible_sources(self):
        c, lex, base, labels, records = grounded_fixture(); ids = ['a', 'b']
        rows = [r for r in records if r['bvd_id'] in ids]
        a = CleanGrounded(c['cleanup'], lex).fit(base, ids, rows, labels, ids)
        self.assertEqual(a.transform(base.documents, ['test']).shape, (1, 0))
        self.assertEqual(len(a.diagnostic['constant_phrases']), 2)
        b = CleanGrounded(c['cleanup'], lex).fit(base, ids, [dict(r, eligible=False) for r in rows], labels, ids)
        self.assertEqual(b.diagnostic['all_size'], 0)
        self.assertGreater(b.diagnostic['lexical_rejection_counts']['ineligible_source'], 0)

    def test_phrase_boundaries_and_duplicate_content(self):
        docs = {'a': [('one', 'AI chair'), ('one', 'AI chair')], 'b': [('two', 'mail chair')]}
        np.testing.assert_array_equal(phrase_presence(docs, ['a', 'b'], ['ai']).toarray(), [[1.], [0.]])

    def test_frequency_ceiling_is_training_only_and_technical_exception(self):
        c, lex, base, labels, _ = grounded_fixture(); ids = ['a', 'b', 'c', 'd']
        for cid in ids[:3]: base.documents[cid] = [(cid, 'machine learning metal fabrication')]
        records = [{'bvd_id': cid, 'phrase': phrase, 'eligible': True, 'signal_type': 'context'}
                   for cid in ids[:2] for phrase in ('machine learning', 'metal fabrication')]
        cleaner = CleanGrounded(c['cleanup'], lex).fit(base, ids, records, labels, ids)
        reasons = {r['phrase']: r['reason'] for r in cleaner.records if r['origin'] == 'all'}
        self.assertEqual(reasons['metal fabrication'], 'common_corpus_term')
        self.assertEqual(reasons['machine learning'], 'accepted')

    def test_support_is_unique_groups_not_annotation_channels(self):
        c, lex, base, labels, records = grounded_fixture(); ids = ['a', 'b', 'c', 'd']
        rows = [r for r in records if r['bvd_id'] == 'a']
        cleaner = CleanGrounded(c['cleanup'], lex).fit(base, ids, rows, labels, ids)
        self.assertEqual(cleaner.diagnostic['all_size'], 0)

    def test_freeze_requires_clear_stable_advantage(self):
        config, _, *_ = grounded_fixture()
        names = ['reference_logistic_size', 'cleaned_grounded_lexical', 'cleaned_grounded_combined']
        summary = pd.DataFrame({'candidate': names, 'weighted': [False]*3,
            'average_precision': [.90, .925, .905], 'operational_f1': [.7, .70, .705],
            'selected_share': [.28, .28, .28], 'keep_recall': [.8, .8, .8]})
        reps = pd.concat([summary.assign(repetition=r) for r in (0, 1)])
        ci = pd.DataFrame([{'candidate': c, 'metric': m, 'paired_delta_lower': .001}
                           for c in names[1:] for m in ('average_precision', 'operational_f1')])
        self.assertEqual(freeze_decision(summary, reps, ci, config)['primary'], names[1])
        ci['paired_delta_lower'] = -.01
        self.assertEqual(freeze_decision(summary, reps, ci, config)['primary'], names[0])
        summary.loc[1, ['average_precision', 'operational_f1', 'selected_share', 'keep_recall']] = [.901, .73, .22, .79]
        reps = pd.concat([summary.assign(repetition=r) for r in (0, 1)])
        self.assertEqual(freeze_decision(summary, reps, ci, config)['primary'], names[0])
        ci['paired_delta_lower'] = .001
        self.assertEqual(freeze_decision(summary, reps, ci, config)['primary'], names[1])

    def test_selection_count_boundaries(self):
        for n in range(501):
            for p in range(101):
                self.assertEqual(selection_count(n, p), (n*p+99)//100)
        self.assertEqual(selection_count(100, 28), 28)
        with self.assertRaises(ValueError): selection_count(10, 1.5)
        f = pd.DataFrame({'bvd_id': [str(i) for i in range(100)], 'state': ['PROCESSABLE']*100,
                          'score': list(range(100)), 'leakage_group_id': list(range(100)),
                          'label': ['KEEP']*30+['EXCLUDE']*30+['UNKNOWN']*40})
        self.assertEqual(operational_curve(f).iloc[27].selected_count, 28)


class CleanupIntegrationTests(unittest.TestCase):
    def prepare(self, temp):
        path, config, _ = fixture(temp); data = Path(config['dataset_directory'])
        manifest = json.loads((data/'manifest.json').read_text())
        manifest['config_hash'] = digest(canonical(json.loads((data/'config_snapshot.json').read_text())))
        write_json(data/'manifest.json', manifest)
        run_parent(path)
        request = yaml.safe_load((ROOT/'config/company_model_cleanup.yaml').read_text())
        request.update(parent_config=str(path), parent_directory=config['output_directory'],
                       output_directory=str(temp/'cleanup'))
        cleanup = temp/'cleanup.yaml'; cleanup.write_text(yaml.safe_dump(request))
        return cleanup, data, Path(config['output_directory']), temp/'cleanup'

    def test_end_to_end_pause_resume_freeze_reproduce_and_immutability(self):
        with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(io.StringIO()), threadpool_limits(limits=1):
            path, data, parent, output = self.prepare(Path(t))
            original = {str(p): file_hash(p) for d in (data, parent) for p in d.rglob('*') if p.is_file()}
            paused = run(path, stop_after_outer=1); self.assertTrue(paused['paused'])
            result = run(path, resume=True)
            self.assertEqual(result['comparison_fits'], 18); self.assertEqual(result['final_fits'], 2)
            predictions = pd.read_parquet(output/'outer_predictions.parquet')
            self.assertTrue(predictions.loc[predictions.state.eq('NO_EVIDENCE'), 'score'].isna().all())
            self.assertEqual(len(predictions), 72*3)
            freeze = json.loads((output/'frozen/freeze_manifest.json').read_text())
            self.assertEqual(len(freeze['bundles']), 2); self.assertIsNone(freeze['production_threshold'])
            for p in freeze['bundles']:
                bundle = joblib.load(p)
                self.assertEqual(bundle.provenance['training_label_counts'], {'KEEP': 18, 'EXCLUDE': 18})
                self.assertNotIn('selected_fraction', bundle.provenance)
            with patch('ai_ecosystem.calibration.run_company_model_cleanup.fit_candidate', side_effect=AssertionError('must not refit')):
                self.assertEqual(run(path, resume=True), result)
            with self.assertRaises(FileExistsError): run(path)
            run(path, verification_output=Path(t)/'verification')
            self.assertTrue(json.loads((Path(t)/'verification/reproducibility.json').read_text())['passed'])
            for p, h in original.items(): self.assertEqual(file_hash(p), h)

    def test_dataset_configuration_tampering(self):
        with tempfile.TemporaryDirectory() as t:
            _, config, _ = fixture(Path(t)); data = Path(config['dataset_directory'])
            manifest = json.loads((data/'manifest.json').read_text())
            source = json.loads((data/'config_snapshot.json').read_text())
            manifest['config_hash'] = digest(canonical(source)); write_json(data/'manifest.json', manifest)
            validate_dataset(data)
            source['text']['max_features'] += 1; write_json(data/'config_snapshot.json', source)
            with self.assertRaisesRegex(ValueError, 'configuration snapshot hash mismatch'): validate_dataset(data)

    def test_failure_checkpoint_and_resume_identity(self):
        with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(io.StringIO()), threadpool_limits(limits=1):
            path, _, _, output = self.prepare(Path(t))
            from ai_ecosystem.calibration.run_company_model_cleanup import fit_candidate as actual
            calls = 0
            def fail(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2: raise RuntimeError('synthetic interruption')
                return actual(*args, **kwargs)
            with patch('ai_ecosystem.calibration.run_company_model_cleanup.fit_candidate', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'): run(path)
            self.assertEqual(len(list((output/'fits').glob('*/complete.json'))), 1)
            run(path, resume=True)
            progress = json.loads((output/'progress.json').read_text())
            self.assertEqual(progress['failure_count'], 1); self.assertEqual(progress['retry_count'], 1)
            request = yaml.safe_load(path.read_text()); request['cleanup']['minimum_groups'] = 3
            path.write_text(yaml.safe_dump(request))
            with self.assertRaisesRegex(ValueError, 'resume identity mismatch'): run(path, resume=True)


if __name__ == '__main__':
    unittest.main()
