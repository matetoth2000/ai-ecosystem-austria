from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from ai_ecosystem.calibration.build_company_modeling_dataset import build_dataset
from ai_ecosystem.calibration.company_modeling_common import load_config, summarize, file_hash, tokens, has_phrase
from ai_ecosystem.calibration.company_modeling_preprocessing import FoldPreprocessor, ngrams
from ai_ecosystem.calibration.company_modeling_sources import load_sources
from ai_ecosystem.calibration.company_modeling_groups import leakage_groups

ROOT = Path(__file__).resolve().parents[1]


class CompanyDatasetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = self.root/'db.sqlite'
        self.config = load_config(ROOT/'config/company_modeling_dataset.yaml')
        self.config.update(calibration_runs=['run'], package_directory='packages')
        self.path = self.root/'config.yaml'; self.path.write_text(yaml.safe_dump(self.config))
        c = sqlite3.connect(self.db)
        c.executescript((ROOT/'src/ai_ecosystem/db/schema.sql').read_text())
        c.execute("INSERT INTO calibration_runs(calibration_run_id,package_run_id,sample_id,model,reasoning_effort,prompt_id,prompt_version,prompt_hash,status) VALUES ('run','package','sample','unused','unused','p','v1','hash','completed')")
        packages = self.root/'packages/package/blind_inputs'; packages.mkdir(parents=True)
        for i in range(12):
            cid = f'C{i:02}'; domain = 'shared.at' if i < 2 else f'company{i}.at'
            c.execute('INSERT INTO firms(bvd_id,firm_name,normalized_domain) VALUES (?,?,?)', (cid, f'Company {i}', domain))
            c.execute('INSERT OR IGNORE INTO domains(domain,firm_count) VALUES (?,?)', (domain, 2 if i < 2 else 1))
            page_id = 1 if i < 2 else i+1
            text = 'Company research machine learning\nSecond paragraph robotics' if i < 2 else f'Unique{i} activities and services for customers'
            if i != 1 and i != 11:
                (self.root/f'{page_id}.txt').write_text(text)
                c.execute('INSERT INTO scraped_pages(scraped_page_id,url,domain,scrape_status,language) VALUES (?,?,?,?,?)', (page_id, f'https://{domain}/about', domain, 'success', 'de-AT'))
                c.execute("INSERT INTO extracted_pages(extracted_page_id,scraped_page_id,extraction_version,extraction_status,text_path) VALUES (?,?,'v1','success',?)", (page_id, page_id, f'{page_id}.txt'))
                c.execute('INSERT INTO page_features(extracted_page_id,title,headings) VALUES (?,?,?)', (page_id, 'About Company', '["Research"]'))
            signal = {'page_id': page_id, 'url': f'https://{domain}/about', 'phrase': 'machine learning', 'signal_type': 'explicit_ai'}
            c.execute("INSERT INTO calibration_results(calibration_run_id,company_id,label,status,lexical_signals_json) VALUES ('run',?,?,'success',?)", (cid, ['KEEP','EXCLUDE','UNKNOWN'][i%3], json.dumps({'signals':[signal]})))
            package = {'pages': [] if i == 11 else [{'scraped_page_id': page_id, 'extracted_page_id': page_id, 'url': signal['url'], 'content_status': 'complete', 'content': text}]}
            (packages/f'{cid}.json').write_text(json.dumps(package))
        c.commit(); c.close()

    def test_build_reproducible_readonly_and_allowlisted(self):
        before = file_hash(self.db)
        a, audit = build_dataset(self.path, self.root/'one', self.db, self.root)
        b, _ = build_dataset(self.path, self.root/'two', self.db, self.root)
        self.assertEqual(a['canonical_build_hash'], b['canonical_build_hash'])
        self.assertEqual(before, file_hash(self.db))
        self.assertEqual(audit['companies'], 12)
        self.assertEqual(audit['states'], {'PROCESSABLE': 11, 'NO_EVIDENCE': 1})
        self.assertEqual(audit['labels'], {'KEEP':4, 'EXCLUDE':4, 'UNKNOWN':4})
        with self.assertRaises(FileExistsError):
            build_dataset(self.path, self.root/'one', self.db, self.root)
        features = pd.read_parquet(self.root/'one/company_features.parquet')
        self.assertFalse({'label','split','nace_code','scorer_v1','confidence'} & set(features))
        splits = pd.read_parquet(self.root/'one/splits.parquet').set_index('bvd_id')
        self.assertEqual(splits.loc['C00','leakage_group_id'], splits.loc['C01','leakage_group_id'])
        self.assertEqual(splits.loc['C00','split'], splits.loc['C01','split'])
        self.assertEqual(audit['eligible_phrases'], 2)
        pages = pd.read_parquet(self.root/'one/pages.parquet')
        self.assertEqual(set(pages.language), {'de'})
        self.assertEqual(len(pd.read_parquet(self.root/'one/contents.parquet')), 10)
        targets = pd.concat([pd.read_parquet(self.root/'one/targets.parquet'), pd.read_parquet(self.root/'one/evaluation_only/internal_holdout_targets.parquet')])
        self.assertFalse(targets.set_index('bvd_id').loc['C11','website_target_eligible'])
        preprocessor = FoldPreprocessor.from_dataset(self.root/'one')
        training = splits.loc[splits.split == 'development'].index.tolist()
        preprocessor.fit(training, preprocessor.training_supervision(training))
        self.assertEqual(preprocessor.transform(['C11'])['text_mean'].nnz, 0)

    def test_missing_file_is_input_error_not_negative(self):
        (self.root/'1.txt').unlink()
        source = load_sources(self.db, self.root, self.config)
        self.assertEqual(len(source['errors']), 1)
        with self.assertRaisesRegex(ValueError, 'INPUT_ERROR'):
            build_dataset(self.path, self.root/'broken', self.db, self.root)
        self.assertFalse((self.root/'broken').exists())

    def test_exact_single_page_and_near_overlap_grouping(self):
        text = ' '.join('token'+str(i) for i in range(120))
        for page in (3,4):
            (self.root/f'{page}.txt').write_text(text)
        source = load_sources(self.db, self.root, self.config)
        groups, _, _, _ = leakage_groups(source, self.config)
        self.assertEqual(groups['C02'], groups['C03'])

    def test_generic_duplicate_quarantine(self):
        for page in (3,4):
            (self.root/f'{page}.txt').write_text('cookie notice '*110)
        source = load_sources(self.db, self.root, self.config)
        groups, _, quarantine, _ = leakage_groups(source, self.config)
        self.assertNotEqual(groups['C02'], groups['C03'])
        self.assertEqual(len(quarantine), 1)

    def test_verified_near_overlap_quarantined_below_material_threshold(self):
        text = ' '.join('token'+str(i) for i in range(160))
        (self.root/'3.txt').write_text(text)
        (self.root/'4.txt').write_text(text.replace('token80', 'different80'))
        source = load_sources(self.db, self.root, self.config)
        groups, _, quarantine, audit = leakage_groups(source, self.config)
        self.assertGreaterEqual(audit['verified_near_pairs'], 1)
        self.assertNotEqual(groups['C02'], groups['C03'])
        self.assertEqual(len(quarantine), 2)

    def test_no_optional_metadata_does_not_remove_text(self):
        c = sqlite3.connect(self.db); c.execute('DELETE FROM page_features'); c.commit(); c.close()
        source = load_sources(self.db, self.root, self.config)
        self.assertEqual(len(source['pages']),10)
        self.assertTrue(all(p['usable'] for p in source['pages']))

    def test_unknown_source_fails_before_output(self):
        config = copy.deepcopy(self.config); config['calibration_runs'] = ['missing']
        self.path.write_text(yaml.safe_dump(config))
        with self.assertRaisesRegex(ValueError, 'Missing or incomplete'):
            build_dataset(self.path, self.root/'missing', self.db, self.root)

    def test_missing_config_and_invalid_thresholds(self):
        self.path.write_text('dataset_id: x')
        with self.assertRaises(ValueError): load_config(self.path)
        c = copy.deepcopy(self.config); c['split_fractions'] = [.5,.5,.5]
        self.path.write_text(yaml.safe_dump(c))
        with self.assertRaises(ValueError): load_config(self.path)

    def test_aggregation_keeps_missing_and_small_support(self):
        self.assertIsNone(summarize([], self.config['aggregation'])['maximum'])
        self.assertEqual(summarize([2,4], self.config['aggregation'])['top3_mean'], 3)
        self.assertEqual(summarize([1]*1000, self.config['aggregation'])['capped_support'], 5)
        self.assertFalse(has_phrase(tokens('email available'), 'ai'))


class FoldPreprocessingTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT/'config/company_modeling_dataset.yaml')
        self.docs = {'a':[('1','machine learning research')], 'b':[('2','machine learning tools')], 'v':[('3','validationonly machine learning')]}
        self.groups = {'a':'a','b':'b','v':'v'}
        self.supervision = [{'bvd_id': c, 'phrase':'machine learning', 'signal_type':'ai','eligible':True} for c in ('a','b')]

    def test_training_only_vocabulary_and_transform(self):
        p = FoldPreprocessor(self.docs,self.groups,self.config,prohibited_ids=['v']).fit(['a','b'], self.supervision)
        self.assertNotIn('validationonly', p.vocabulary)
        self.assertIn(('ai','machine learning'), p.phrase_vocabulary)
        self.assertEqual(p.transform(['v'])['phrase_proportion'][0,p.phrase_vocabulary[('ai','machine learning')]], 1)
        with self.assertRaises(ValueError): p.fit(['a','v'], [])
        with self.assertRaises(ValueError): p.fit(['a','b'], self.supervision+[{'bvd_id':'v'}])
        q = FoldPreprocessor(self.docs,self.groups,self.config).fit(['a'],self.supervision[:1])
        self.assertFalse(q.phrase_vocabulary)
        self.assertNotEqual(p.provenance['cache_key'],q.provenance['cache_key'])

    def test_group_boundaries_and_page_boundaries(self):
        with self.assertRaises(ValueError):
            FoldPreprocessor(self.docs,{'a':'same','b':'same','v':'v'},self.config).fit(['a'],[])
        docs = {'a':[('1','machine'),('2','learning')]}
        p = FoldPreprocessor(docs,{'a':'a'},self.config).fit(['a'],[])
        self.assertNotIn('machine learning', p.vocabulary)
        self.assertEqual(ngrams('A B',2),['a','b','a b'])

    def test_duplicate_invariance_and_missing_documents(self):
        docs = {'a':[('1','robot research'),('1','robot research')], 'v':[]}
        p = FoldPreprocessor(docs,{'a':'a','v':'v'},self.config).fit(['a'],[])
        self.assertEqual(p.transform(['v'])['text_mean'].nnz,0)
        self.assertEqual(p.transform(['a'])['text_mean'].shape[0],1)

    def test_group_document_frequency_not_page_frequency(self):
        self.docs['a'].extend((f'p{i}', 'machine learning research') for i in range(100))
        p = FoldPreprocessor(self.docs,self.groups,self.config).fit(['a','b'],self.supervision)
        self.assertEqual(p.idf[p.vocabulary['machine']],1)
        self.assertGreater(p.idf[p.vocabulary['research']],1)


if __name__ == '__main__':
    unittest.main()
