"""Scored-sample profiling with real local tokenization and mocked Batch APIs."""
from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_ecosystem.calibration import run_company_profile_batch as workflow
from ai_ecosystem.calibration import run_ai_profile_validation_batch as engine
from ai_ecosystem.calibration.company_ai_profile_contract import canonical, prompt_and_schema, request_body
from ai_ecosystem.calibration.company_ai_profile_packages import load_config, package_company
from ai_ecosystem.calibration.company_modeling_sources import load_sources
from test_ai_profile_validation_batch import FakeBatches, FakeFiles
from test_company_ai_profile_pilot import profile, response

ROOT = Path(__file__).resolve().parents[1]


class ScoredProfileBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'config').mkdir()
        for name in ('config.yaml', 'company_ai_profile_pilot_v5.yaml'):
            shutil.copyfile(ROOT / 'config' / name, self.root / 'config' / name)
        self.database = self.root / 'source.sqlite'
        self.scores = self.root / 'scores.csv'; self.output = self.root / 'run'
        self.policy = self.root / 'policy.json'
        self.policy.write_text('{"model":"cleaned_grounded_combined","score_rule":"score >= cutoff; include all ties",'
                               '"policies":[{"budget_usd":200,"cutoff":-0.10212121864474141}]}')
        with sqlite3.connect(self.database) as c:
            c.executescript((ROOT / 'src/ai_ecosystem/db/schema.sql').read_text())
            c.execute("INSERT INTO sample_sets(sample_id,sample_name,sample_unit,created_at) VALUES ('batch','Fixture batch','company','2026-11-12')")
            for i in range(1, 7):
                cid = f'C{i}'; domain = f'c{i}.at'
                c.execute('INSERT INTO firms(bvd_id,firm_name,normalized_domain) VALUES (?,?,?)',
                          (cid, f'Company {i}', domain))
                c.execute('INSERT INTO domains(domain,firm_count) VALUES (?,1)', (domain,))
                c.execute('INSERT INTO sample_companies(sample_id,company_id,sample_rank,selection_reason) VALUES (?,?,?,?)',
                          ('batch', cid, i, 'fixture'))
                c.execute('INSERT INTO sample_domains(sample_id,domain,sample_rank) VALUES (?,?,?)', ('batch', domain, i))
                if i < 3:
                    for offset, path, text in [(0, '/', f'Company {i} supplies conventional chairs.'),
                                                (1, '/products', f'Company {i} supplies conventional chairs.'),
                                                (2, '/privacy', 'Privacy notices.')]:
                        pid = i * 10 + offset; filename = f'{pid}.txt'
                        (self.root / filename).write_text(text)
                        c.execute("INSERT INTO scraped_pages(scraped_page_id,url,domain,scrape_status) VALUES (?,?,?,'success')",
                                  (pid, f'https://{domain}{path}', domain))
                        c.execute("INSERT INTO extracted_pages(scraped_page_id,extraction_version,extraction_status,text_path) VALUES (?,'v1','success',?)",
                                  (pid, filename))
        self.rows = [
            {'bvd_id': 'C1', 'state': 'PROCESSABLE', workflow.MODEL: '-0.10212121864474140'},
            {'bvd_id': 'C2', 'state': 'PROCESSABLE', workflow.MODEL: '-0.10212121864474141'},
            {'bvd_id': 'C3', 'state': 'PROCESSABLE', workflow.MODEL: '-0.10212121864474142'},
            {'bvd_id': 'C4', 'state': 'NO_EVIDENCE', workflow.MODEL: ''},
            {'bvd_id': 'C5', 'state': 'PROCESSABLE', workflow.MODEL: ''},
        ]
        self.write_scores()
        self.client = SimpleNamespace(batches=FakeBatches(), files=FakeFiles())

    def write_scores(self):
        with self.scores.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['bvd_id', 'state', workflow.MODEL])
            writer.writeheader(); writer.writerows(self.rows)

    def prepare(self, **kwargs):
        options = dict(root=self.root, database=self.database, policy=self.policy,
                       spend_allowance_usd=1.0, queue_allowance_tokens=1_000_000)
        options.update(kwargs)
        return workflow.prepare('batch', self.scores, self.output, **options)

    def terminal(self, rows, status='completed'):
        engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        batch = self.client.batches.remote[0]
        batch.update(status=status, output_file_id='out', error_file_id=None)
        self.client.files.contents['out'] = ''.join(json.dumps(r)+'\n' for r in rows)
        return batch

    def test_exact_cutoff_ties_membership_and_unscored(self):
        before = workflow.file_hash(self.database)
        result = self.prepare()
        self.assertEqual((result['selected'], result['included'], result['not_selected']), (2, 2, 4))
        self.assertEqual(result['policy']['cutoff'], '-0.10212121864474141')
        self.assertEqual(result['policy']['source_sha256'], workflow.file_hash(self.policy))
        report = workflow.report(self.output)
        rows = {r['company_id']: r for r in report['company_results']}
        self.assertEqual(rows['C3']['selection_reason'], 'below_policy_cutoff')
        self.assertEqual(rows['C4']['selection_reason'], 'NO_EVIDENCE')
        self.assertEqual(rows['C5']['selection_reason'], 'missing_score')
        self.assertEqual(rows['C6']['selection_reason'], 'missing_score_row')
        self.assertTrue(all(r['label'] is None for r in rows.values()))
        self.assertEqual(before, workflow.file_hash(self.database))
        self.assertNotIn('splits', result)
        self.assertEqual(result['coverage']['duplicate_page_records'], 2)
        self.assertEqual(result['coverage']['excluded_utility_pages'], 2)

    def test_duplicate_foreign_bad_state_and_inconsistent_scores_rejected(self):
        members = [f'C{i}' for i in range(1, 7)]
        for bad in [self.rows + [self.rows[0]], self.rows + [{'bvd_id': 'FOREIGN', 'state': 'PROCESSABLE', workflow.MODEL: '1'}],
                    [{**self.rows[0], 'state': 'INPUT_ERROR'}],
                    [{'bvd_id': 'C4', 'state': 'NO_EVIDENCE', workflow.MODEL: '1'}],
                    [{**self.rows[0], workflow.MODEL: 'Infinity'}], [{**self.rows[0], workflow.MODEL: 'bad'}]]:
            with self.subTest(bad=bad):
                self.rows = bad; self.write_scores()
                with self.assertRaises(ValueError):
                    workflow.select_scores(self.scores, members, workflow.EXACT_CUTOFF)

    def test_wrong_policy_and_unknown_sample_rejected(self):
        self.policy.write_text(self.policy.read_text().replace('-0.10212121864474141', '-0.102'))
        with self.assertRaisesRegex(ValueError, 'exact frozen'):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'Unknown sample'):
            workflow.prepare('unknown', self.scores, self.output, root=self.root, database=self.database,
                             policy=self.policy, spend_allowance_usd=1., queue_allowance_tokens=10000)

    def test_offline_preparation_refuses_missing_tokenizer_cache(self):
        with patch.dict(os.environ, {'TIKTOKEN_CACHE_DIR': str(self.root / 'absent-cache')}), \
                patch('requests.get', side_effect=AssertionError('must not download')):
            with self.assertRaisesRegex(ValueError, 'persistent o200k_base cache'):
                self.prepare()

    def test_request_and_package_parity_with_existing_components(self):
        self.prepare()
        config = load_config(self.root, self.root / workflow.CONFIG)
        source = load_sources(self.database, self.root, {'extraction_version': 'v1'}, company_ids=['C1', 'C2'])
        company = {'company_id': 'C1', **next(c for c in source['companies'] if c['bvd_id'] == 'C1')}
        expected = package_company(company, source, self.root, config)
        self.assertEqual(expected, workflow.read_json(self.output / 'packages/C1.json'))
        prompt, schema = prompt_and_schema(config['contract_id'])
        record = workflow.read_json(self.output / 'work/C1.json')
        self.assertEqual(request_body(expected, config, prompt, schema), record['batch_line']['body'])
        self.assertNotIn('split', record)
        self.assertNotIn('"score":', record['batch_line']['body']['input'][1]['content'])
        self.assertEqual(record['batch_line']['body']['max_output_tokens'], 8000)

    def test_zero_selection_never_uses_client_or_source(self):
        for row in self.rows:
            if row['state'] == 'PROCESSABLE':row[workflow.MODEL] = '-1'
        self.write_scores()
        with patch.object(workflow, 'load_sources', side_effect=AssertionError('no sources')), \
                patch.object(engine, 'client_for', side_effect=AssertionError('no API client')):
            summary = self.prepare()
            self.assertEqual(summary['selected'], 0)
            self.assertEqual(engine.submit(self.root, self.output, confirm_paid=True), [])
            self.assertEqual(engine.status(self.root, self.output), [])
            self.assertEqual(engine.ingest(self.root, self.output)['unresolved_selected'], 0)

    def test_selected_without_usable_package_stays_unresolved(self):
        (self.root / '10.txt').write_text('   '); (self.root / '11.txt').write_text('   ')
        summary = self.prepare()
        self.assertEqual((summary['selected'], summary['included'], summary['selected_without_request']), (2, 1, 1))
        report = workflow.report(self.output)
        row = next(r for r in report['company_results'] if r['company_id'] == 'C1')
        self.assertEqual(row['status'], 'no_usable_evidence'); self.assertIsNone(row['label'])

    def test_resume_partial_complete_tamper_and_no_overwrite(self):
        with patch.object(workflow, 'pack_files', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):self.prepare()
        request_hash = workflow.read_json(self.output / 'work/C1.json')['request_hash']
        self.prepare(resume=True)
        self.assertEqual(request_hash, workflow.read_json(self.output / 'work/C1.json')['request_hash'])
        self.assertEqual(self.prepare(resume=True), workflow.validate_prepared(self.output)[0])
        with self.assertRaises(FileExistsError):self.prepare()
        path = self.output / 'work/C1.json'; path.write_text(path.read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):workflow.validate_prepared(self.output)

    def test_source_changes_refuse_resume(self):
        self.prepare(); (self.root / '10.txt').write_text('Changed evidence.')
        with self.assertRaisesRegex(ValueError, 'Resume identity'):self.prepare(resume=True)

    def test_paid_flag_spend_and_queue_guards_before_upload(self):
        self.prepare(spend_allowance_usd=0.000001)
        with self.assertRaises(ValueError):engine.submit(self.root, self.output, client=self.client)
        with self.assertRaisesRegex(ValueError, 'spending allowance'):
            engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        self.assertEqual(self.client.files.uploads, 0)
        other = self.root / 'queue-run'; self.output = other
        self.prepare(queue_allowance_tokens=1)
        with self.assertRaisesRegex(ValueError, 'queue allowance'):
            engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        self.assertEqual(self.client.files.uploads, 0)

    def test_duplicate_submission_and_multifile_resume(self):
        # Force small files using the existing packer, without changing requests.
        with patch('ai_ecosystem.calibration.prepare_ai_profile_validation_batch.TARGET_FILE_TOKENS', 1):
            self.prepare()
        summary = workflow.validate_prepared(self.output)[0]
        self.assertEqual(len(summary['files']), 2)
        engine.submit(self.root, self.output, confirm_paid=True, file_name='batch_0001.jsonl', client=self.client)
        with self.assertRaises(FileExistsError):
            engine.submit(self.root, self.output, confirm_paid=True, file_name='batch_0001.jsonl', client=self.client)
        engine.submit(self.root, self.output, confirm_paid=True, file_name='batch_0002.jsonl', client=self.client)
        self.assertEqual(self.client.files.uploads, 2)

    def test_ambiguous_submit_reconcile_reuses_upload(self):
        self.prepare(); self.client.batches.fail_create = True
        with self.assertRaises(ConnectionError):engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        with self.assertRaisesRegex(RuntimeError, 'reconcile'):
            engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        with self.assertRaisesRegex(RuntimeError, 'confirm absence'):
            engine.reconcile(self.root, self.output, 'batch_0001.jsonl', client=self.client)
        engine.reconcile(self.root, self.output, 'batch_0001.jsonl', confirm_remote_absent=True, client=self.client)
        self.client.batches.fail_create = False
        engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        self.assertEqual(self.client.files.uploads, 1)

    def test_ingest_missing_invalid_and_failed_responses_are_unresolved(self):
        self.prepare(); _, records, _ = workflow.validate_prepared(self.output)
        first = records['C1']['batch_line']
        self.terminal([{'custom_id': first['custom_id'], 'response': {'status_code': 200,
                       'body': {'id': 'bad', 'status': 'completed', 'model': 'gpt-6-luna',
                                'output': [{'content': [{'type': 'output_text', 'text': '{}'}]}]}}}])
        result = engine.ingest(self.root, self.output, client=self.client)
        self.assertEqual(result['status_counts']['invalid_schema'], 1)
        self.assertEqual(result['status_counts']['missing'], 1)
        self.assertEqual(result['valid_labels'], {})
        self.assertEqual(result, engine.ingest(self.root, self.output, client=self.client))
        self.assertTrue(all(r['label'] is None for r in result['company_results']))

    def test_ingest_success_usage_and_api_failure_costs(self):
        self.prepare(); _, records, _ = workflow.validate_prepared(self.output)
        value = profile(); value['ai_active']['attribution_scope'] = None; value['ai_adjacent']['attribution_scope'] = None
        rows = [{'custom_id': records['C1']['batch_line']['custom_id'],
                 'response': {'status_code': 200, 'body': response(value)}},
                {'custom_id': records['C2']['batch_line']['custom_id'], 'error': {'code': 'batch_expired'}}]
        self.terminal(rows, status='expired')
        report = engine.ingest(self.root, self.output, client=self.client)
        self.assertEqual(report['valid_labels'], {'EXCLUDE': 1})
        self.assertEqual(report['status_counts']['api_failure'], 1)
        self.assertEqual(report['calculated_usage_totals']['input_tokens'], 100)
        self.assertEqual(report['calculated_usage_totals']['reasoning_output_tokens_included'], 20)
        self.assertGreater(report['calculated_usage_totals']['total_cost_usd'], 0)
        self.assertEqual(report['cost_unknown_company_ids'], ['C2'])

    def test_ingest_unknown_duplicate_and_changed_download_rejected(self):
        self.prepare(); _, records, _ = workflow.validate_prepared(self.output)
        self.terminal([{'custom_id': 'unknown', 'error': {'code': 'bad'}}])
        with self.assertRaisesRegex(ValueError, 'Unknown or duplicate'):
            engine.ingest(self.root, self.output, client=self.client)
        self.client.files.contents['out'] = ''
        with self.assertRaisesRegex(ValueError, 'content changed'):
            engine.ingest(self.root, self.output, client=self.client)

    def test_ingest_duplicate_returned_id_rejected(self):
        self.prepare(); _, records, _ = workflow.validate_prepared(self.output)
        row = {'custom_id': records['C1']['batch_line']['custom_id'], 'error': {'code': 'expired'}}
        self.terminal([row, row])
        with self.assertRaisesRegex(ValueError, 'Unknown or duplicate'):
            engine.ingest(self.root, self.output, client=self.client)

    def test_utility_only_packages_and_empty_sample_never_create_requests(self):
        for i in (1, 2):
            for offset in (0, 1):
                (self.root / f'{i * 10 + offset}.txt').write_text('')
        self.assertEqual(self.prepare()['included'], 0)
        with patch.object(engine, 'client_for', side_effect=AssertionError('no API')):
            self.assertEqual(engine.ingest(self.root, self.output)['unresolved_selected'], 2)
        with sqlite3.connect(self.database) as c:
            c.execute('DELETE FROM sample_companies'); c.execute('DELETE FROM sample_domains')
        self.rows = []; self.write_scores(); self.output = self.root / 'empty-run'
        with patch.object(workflow, 'local_tokens', side_effect=AssertionError('no tokenizer needed')):
            self.assertEqual(self.prepare()['selected'], 0)

    def test_nonterminal_batch_remains_pending(self):
        self.prepare(); engine.submit(self.root, self.output, confirm_paid=True, client=self.client)
        report = engine.ingest(self.root, self.output, client=self.client)
        self.assertEqual(report['status_counts']['pending'], 2)
        self.assertEqual(report['unresolved_selected'], 2)
        self.assertEqual(engine.status(self.root, self.output, client=self.client)[0]['status'], 'validating')


if __name__ == '__main__':
    unittest.main()
