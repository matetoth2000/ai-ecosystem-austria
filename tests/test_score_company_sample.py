from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
import yaml

import test_company_modeling_dataset as dataset_tests
from ai_ecosystem.calibration.build_company_modeling_dataset import build_dataset
from ai_ecosystem.calibration.company_cleanup_features import FrozenCompanyScorer, CleanGrounded, CANDIDATES
from ai_ecosystem.calibration.company_experiment_features import NumericBlock, ExperimentText, feature_blocks
from ai_ecosystem.calibration.company_modeling_common import file_hash
from ai_ecosystem.calibration.company_modeling_sources import _domain_evidence, load_sources
from ai_ecosystem.calibration.company_tuning_features import document_maps, expanded_lexical
from ai_ecosystem.calibration.score_company_sample import (
    MODELS, prepare_inputs, read_context, sample_company_ids, score_sample,
)

ROOT = dataset_tests.ROOT


class LinearProbe:
    """Fixed coefficients exercise the frozen transforms without training a model."""
    def decision_function(self, matrix):
        return np.asarray(matrix @ np.linspace(-1, 1, matrix.shape[1])).ravel()


class SampleScoringTests(unittest.TestCase):
    def setUp(self):
        fixture = dataset_tests.CompanyDatasetTest('test_build_reproducible_readonly_and_allowlisted')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.root, self.db, self.config, self.path = fixture.root, fixture.db, fixture.config, fixture.path
        self.data = self.root/'dataset'
        # Cross-site boilerplate proves subset loading must retain overlap context.
        for p in (3, 4):
            (self.root/f'{p}.txt').write_text('cookie notice '*110)
        with contextlib.redirect_stdout(io.StringIO()):
            build_dataset(self.path, self.data, self.db, self.root)
        self.lexicon = yaml.safe_load((ROOT/'config/company_tuning_lexicon.yaml').read_text())
        self.scoring_config = {'support_cap': 5, 'dataset_directory': str(self.data)}
        with sqlite3.connect(self.db) as c:
            for sample, unit in [('selected', 'company'), ('legacy', 'domain'), ('empty', 'company')]:
                c.execute('INSERT INTO sample_sets(sample_id,sample_name,sample_unit,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)', (sample, sample, unit))
                c.execute('INSERT INTO sample_domains(sample_id,domain) VALUES (?,?)', (sample, 'shared.at'))
            for cid in ('C00', 'C02', 'C11'):
                c.execute("INSERT INTO sample_companies(sample_id,company_id,sample_rank,normalized_domain,selection_reason) VALUES (?,?,?,?,'test')",
                          ('selected', cid, int(cid[1:]), 'shared.at'))

    def test_membership_explicit_legacy_empty_and_unknown(self):
        self.assertEqual(sample_company_ids(self.db, 'selected'), ['C00', 'C02', 'C11'])
        self.assertEqual(sample_company_ids(self.db, 'legacy'), ['C00', 'C01'])
        self.assertEqual(sample_company_ids(self.db, 'empty'), [])
        with self.assertRaisesRegex(ValueError, 'Unknown sample'):
            sample_company_ids(self.db, 'absent')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO firm_urls(bvd_id,domain,normalized_url) VALUES ('C04','shared.at','https://shared.at')")
        self.assertEqual(sample_company_ids(self.db, 'legacy'), ['C00', 'C01', 'C04'])

    def test_unlabeled_sources_need_no_calibration_tables_or_packages(self):
        expected = load_sources(self.db, self.root, self.config)
        with sqlite3.connect(self.db) as c:
            c.execute('DROP TABLE calibration_results'); c.execute('DROP TABLE calibration_runs')
        config = dict(self.config, package_directory='does-not-exist', calibration_runs=['missing'])
        actual = load_sources(self.db, self.root, config, company_ids=['C00', 'C11'])
        self.assertEqual(actual['companies'], [r for r in expected['companies'] if r['bvd_id'] in ('C00', 'C11')])
        self.assertEqual(actual['pages'], [r for r in expected['pages'] if r['website_id'] == 'shared.at'])
        self.assertEqual(actual['labels'], []); self.assertEqual(actual['phrases'], [])
        with self.assertRaisesRegex(ValueError, 'Unreconciled company'):
            load_sources(self.db, self.root, config, company_ids=['missing'])

    def test_batched_domain_reads_match_original_queries(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO scraped_pages(scraped_page_id,url,domain,scrape_status) VALUES (90,'https://shared.at/fail','shared.at','timeout')")
            c.execute("INSERT INTO extracted_pages(scraped_page_id,extraction_version,extraction_status) VALUES (90,'v1','failed')")
            c.execute("INSERT INTO extracted_pages(scraped_page_id,extraction_version,extraction_status) VALUES (90,'v2','success')")
            c.row_factory = sqlite3.Row
            domains = ['company11.at', 'shared.at']
            actual_stats, actual_rows = _domain_evidence(c, domains, 'v1')
            actual_rows = [dict(row) for row in actual_rows]
            original_rows = []
            for domain in domains:
                counts = dict(c.execute('''SELECT count(*) acquired_records,
                    sum(CASE WHEN scrape_status='success' THEN 1 ELSE 0 END) acquired_success
                    FROM scraped_pages WHERE domain=?''', (domain,)).fetchone())
                extraction = dict(c.execute('''SELECT count(*) extraction_records,
                    sum(CASE WHEN e.extraction_status='success' THEN 1 ELSE 0 END) extraction_success,
                    sum(CASE WHEN e.extraction_status!='success' THEN 1 ELSE 0 END) extraction_not_success
                    FROM extracted_pages e JOIN scraped_pages s ON e.scraped_page_id=s.scraped_page_id
                    WHERE s.domain=? AND e.extraction_version=?''', (domain, 'v1')).fetchone())
                counts.update({key: value or 0 for key, value in extraction.items()})
                self.assertEqual(actual_stats[domain], counts)
                original_rows.extend(dict(row) for row in c.execute('''SELECT s.scraped_page_id,s.url,s.final_url,s.domain,s.title acquisition_title,s.language acquisition_language,
                    s.meta_description,e.extracted_page_id,e.extraction_version,e.extraction_run_id,e.content_sha256 source_content_sha256,
                    e.text_path,e.metadata_path,e.language extraction_language,p.title,p.headings,p.language feature_language,
                    p.feature_version FROM scraped_pages s JOIN extracted_pages e ON e.scraped_page_id=s.scraped_page_id
                    LEFT JOIN page_features p ON p.extracted_page_id=e.extracted_page_id
                    WHERE s.domain=? AND e.extraction_status='success' AND e.extraction_version=?
                    ORDER BY s.scraped_page_id,e.extracted_page_id DESC''', (domain, 'v1')))
            self.assertEqual(actual_rows, original_rows)
            self.assertEqual(actual_stats['company11.at']['acquired_records'], 0)
            self.assertEqual(actual_stats['shared.at']['extraction_not_success'], 1)

    def test_batched_domain_reads_include_later_batches_only(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            domains = [f'absent{i:04}.at' for i in range(900)] + ['shared.at']
            stats, rows = _domain_evidence(c, domains, 'v1')
            self.assertEqual(len(stats), 901)
            self.assertEqual(stats['absent0000.at']['acquired_records'], 0)
            self.assertEqual({row['domain'] for row in rows}, {'shared.at'})

    def old_inputs(self):
        companies = pd.read_parquet(self.data/'companies.parquet').assign(label=None)
        numeric = pd.read_parquet(self.data/'company_features.parquet').set_index('bvd_id')
        states = pd.read_parquet(self.data/'operational_states.parquet').set_index('bvd_id')
        lexical, _ = expanded_lexical(self.data, companies, self.lexicon, self.scoring_config)
        documents = {k: v.documents for k, v in document_maps(self.data).items()}
        return documents, numeric, lexical, states

    def bundles(self):
        _, numeric, lexical, states = self.old_inputs()
        frame = numeric.copy()
        fitted, names = {}, {}
        for channel, base in document_maps(self.data).items():
            pre = ExperimentText(base.documents, base.groups, base.config, base.names).fit(sorted(numeric.index), [])
            fitted[channel] = pre; names[channel+'_mean'] = list(pre.vocabulary)
        sources = {k: (frame, cols) for k, cols in feature_blocks(frame.columns).items()}
        sources['L2'] = lexical, sorted(lexical.columns)
        for key, (values, columns) in sources.items():
            processor = NumericBlock().fit(values.loc[states.state.eq('PROCESSABLE')], columns)
            fitted[key] = processor
            names[key] = list(processor.imputer.get_feature_names_out(columns)[processor.keep])
        policy = yaml.safe_load((ROOT/'config/company_model_cleanup.yaml').read_text())['cleanup']
        grounded = CleanGrounded(policy, self.lexicon)
        grounded.aliases = [['computer vision', 'machine learning']]
        fitted['G3'] = grounded; names['G3'] = ['technical']
        result = {}
        for candidate in MODELS:
            used = CANDIDATES[candidate]
            result[candidate] = FrozenCompanyScorer(LinearProbe(), candidate,
                {b.removesuffix('_mean'): fitted[b.removesuffix('_mean')] for b in used},
                {b: names[b] for b in used}, {'identity_hash': 'synthetic', 'config': self.scoring_config, 'lexicon': self.lexicon})
        return result

    def test_historical_features_documents_and_scores_match_subset(self):
        context, config, _ = read_context(self.data)
        ids = sample_company_ids(self.db, 'selected')
        source = load_sources(self.db, self.root, config, company_ids=ids)
        new = prepare_inputs(source, context, config, self.lexicon, self.scoring_config)
        old = self.old_inputs()
        for index in (1, 2, 3):
            pd.testing.assert_frame_equal(new[index], old[index].loc[ids], check_dtype=False)
        for ch in old[0]:
            self.assertEqual(new[0][ch], {cid: old[0][ch][cid] for cid in ids})
        for bundle in self.bundles().values():
            a = bundle.score(ids, old[0], old[1], old[2], old[3].state)
            b = bundle.score(ids, new[0], new[1], new[2], new[3].state)
            pd.testing.assert_frame_equal(a, b)
            self.assertTrue(b.loc[b.bvd_id.eq('C11'), 'score'].isna().all())
        self.assertFalse(new[3].loc['C02', 'predictive_content_available'])

    def test_cli_path_scores_new_ids_readonly_and_preserves_output(self):
        frozen = self.root/'frozen'
        for name, bundle in self.bundles().items():
            folder = frozen/name; folder.mkdir(parents=True)
            joblib.dump(bundle, folder/'model.joblib')
            (folder/'complete.json').write_text(json.dumps({'files': {'model.joblib': file_hash(folder/'model.joblib')}}))
        with sqlite3.connect(self.db) as c:
            c.execute('DROP TABLE calibration_results'); c.execute('DROP TABLE calibration_runs')
            c.execute("INSERT INTO firms(bvd_id,firm_name,normalized_domain) VALUES ('C99','New Research','new.at')")
            c.execute("INSERT INTO sample_companies(sample_id,company_id,sample_rank,selection_reason) VALUES ('selected','C99',99,'test')")
            c.execute("INSERT INTO scraped_pages(scraped_page_id,url,domain,scrape_status) VALUES (99,'https://new.at/','new.at','success')")
            c.execute("INSERT INTO extracted_pages(scraped_page_id,extraction_version,extraction_status,text_path) VALUES (99,'v1','success','new.txt')")
        (self.root/'new.txt').write_text('New Research develops computer vision and machine learning products.')
        before = file_hash(self.db); output = self.root/'scores.csv'
        log = io.StringIO()
        with patch.object(ExperimentText, 'fit', side_effect=AssertionError('must not fit')), contextlib.redirect_stdout(log):
            result = score_sample('selected', output, database=self.db, root=self.root, frozen_directory=frozen)
        for stage in ('Sample resolved', 'Frozen models and overlap context loaded',
                      'Source evidence loaded', 'Company features prepared',
                      'Frozen model scored: reference_logistic_size',
                      'Frozen model scored: cleaned_grounded_combined', 'CSV written'):
            self.assertIn(stage, log.getvalue())
        self.assertEqual(result.bvd_id.tolist(), ['C00', 'C02', 'C11', 'C99'])
        self.assertTrue(np.isfinite(result.loc[result.bvd_id.eq('C99'), list(MODELS)].to_numpy()).all())
        self.assertEqual(before, file_hash(self.db))
        self.assertTrue(result.loc[result.state.eq('NO_EVIDENCE'), list(MODELS)].isna().all().all())
        self.assertEqual(list(pd.read_csv(output).columns), list(result.columns))
        with self.assertRaises(FileExistsError):
            score_sample('selected', output, database=self.db, root=self.root, frozen_directory=frozen)

    def test_missing_file_fails_and_all_no_evidence_is_supported(self):
        context, config, _ = read_context(self.data)
        source = load_sources(self.db, self.root, config, company_ids=['C11'])
        _, _, _, states = prepare_inputs(source, context, config, self.lexicon, self.scoring_config)
        self.assertEqual(states.loc['C11', 'state'], 'NO_EVIDENCE')
        (self.root/'1.txt').unlink()
        source = load_sources(self.db, self.root, config, company_ids=['C00'])
        with self.assertRaisesRegex(ValueError, 'INPUT_ERROR'):
            prepare_inputs(source, context, config, self.lexicon, self.scoring_config)


if __name__ == '__main__':
    unittest.main()
