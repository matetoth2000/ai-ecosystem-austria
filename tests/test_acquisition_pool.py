from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from ai_ecosystem.calibration.acquisition_pool_sampling import allocations, prefix_probability, advance
from ai_ecosystem.calibration.build_acquisition_pool import build_pool, load_saved
from ai_ecosystem.calibration.company_modeling_common import file_hash
from ai_ecosystem.calibration.score_company_sample import sample_company_ids
from ai_ecosystem.discovery.discovery_utils import get_sample_domains

ROOT = Path(__file__).resolve().parents[1]


class AllocationTests(unittest.TestCase):
    def test_monotone_capped_allocation_and_capacity(self):
        population = {'A': 700, 'C': 200, 'J': 99, 'MISSING': 1}
        old = dict.fromkeys(population, 0)
        for n in list(range(1, 101)) + [200, 500, 999, 1000]:
            result = allocations(population, n)
            self.assertEqual(sum(result.values()), n)
            self.assertTrue(all(old[s] <= result[s] <= population[s] for s in population))
            old = result
        self.assertEqual(old, population)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            allocations(population, 1001)
        for value in (0, -1, 1.5, True):
            with self.assertRaises(ValueError): allocations(population, value)

    def test_eighty_twenty_mix_without_saturation(self):
        population = {'A': 8000, 'J': 2000}
        result = allocations(population, 1500)
        self.assertEqual(result, {'A': 1110, 'J': 390})
        self.assertEqual(allocations({'B': 10, 'A': 10}, 1), {'A': 1, 'B': 0})


class AcquisitionPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = self.root/'source.sqlite'
        self.dev = self.root/'development'; self.dev.mkdir()
        self.connection = sqlite3.connect(self.db); self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        c = self.connection
        c.executescript((ROOT/'src/ai_ecosystem/db/schema.sql').read_text())
        dev = [{'bvd_id': f'D{i:03d}', 'normalized_domain': 'development.at'} for i in range(649)]
        tables = {'companies': pd.DataFrame(dev), 'websites': pd.DataFrame({'website_id': ['development.at']}),
                  'pages': pd.DataFrame({'final_host': ['redirected-dev.at']})}
        hashes = {}
        for name, table in tables.items():
            p = self.dev/(name+'.parquet'); table.to_parquet(p, index=False)
            hashes[name] = {'file_hash': file_hash(p)}
        (self.dev/'manifest.json').write_text(json.dumps({'tables': hashes}))
        c.executemany('INSERT INTO firms(bvd_id,firm_name,normalized_domain,nace_code) VALUES (?,?,?,?)',
                      [(r['bvd_id'], 'Development', 'development.at', 'J620') for r in dev])
        for i in range(1800):
            code = 'C261' if i < 1200 else 'J620' if i < 1799 else None
            # Many shared websites exercise legal-company vs acquisition units.
            c.execute('INSERT INTO firms(bvd_id,firm_name,normalized_domain,nace_code) VALUES (?,?,?,?)',
                      (f'N{i:04d}', f'Firm {i}', f'site{i//3}.at', code))
        for cid, domain in [('no-domain', None), ('blocked', 'blocked.at'), ('sister', 'development.at'),
                            ('alias', 'redirected-dev.at'), ('child', 'shop.development.at'), ('url-alias', 'other.at')]:
            c.execute('INSERT INTO firms(bvd_id,firm_name,normalized_domain,nace_code) VALUES (?,?,?,?)',
                      (cid, cid, domain, 'M711'))
        c.execute("INSERT INTO firm_urls(bvd_id,domain,normalized_url,is_primary) VALUES ('url-alias','redirected-dev.at','https://redirected-dev.at',1)")
        c.execute("INSERT INTO domains(domain,is_excluded) VALUES ('blocked.at',1)")
        c.commit()

    def create(self, size=1500, pool='test', seed=42):
        return build_pool('create', pool, size, seed=seed, database=self.db, root=self.root,
                          development_directory=self.dev)

    def saved(self, pool='test'):
        return load_saved(self.connection, pool)

    def test_reproducible_membership_sizes_exclusions_and_integration(self):
        result = self.create()
        directory, frame, state, _ = self.saved()
        self.assertEqual(result['companies'], 1500)
        self.assertEqual(list(result['batches'].values()), [100]*15)
        self.assertEqual(frame['eligible_count'], 1800)
        self.assertEqual(len(set(r['company_id'] for r in state['selected'])), 1500)
        reasons = {r['company_id']: r['reasons'] for r in frame['exclusions']}
        self.assertIn('development_company', reasons['D000'])
        self.assertIn('missing_normalized_domain', reasons['no-domain'])
        self.assertIn('marked_excluded_domain', reasons['blocked'])
        for cid in ('sister', 'alias', 'child', 'url-alias'):
            self.assertIn('development_website_family', reasons[cid])
        self.assertEqual(frame['sector_populations']['MISSING'], 1)
        self.assertEqual(state['allocations']['MISSING'], 1)
        self.assertEqual(len(sample_company_ids(self.db, 'test')), 1500)
        self.assertEqual(len(sample_company_ids(self.db, result['first_batch'])), 100)
        batch_sector_counts = pd.DataFrame(state['selected']).groupby(['batch_id', 'sector']).size().unstack(fill_value=0)
        self.assertGreater(len(batch_sector_counts.drop_duplicates()), 1)
        self.assertLess(len(get_sample_domains(self.connection, 'test')), 1500)
        domain_members = {r['normalized_domain'] for r in state['selected']}
        self.assertEqual(set(get_sample_domains(self.connection, 'test')), domain_members)
        self.assertTrue(result['shared_domain_groups'])
        self.create(pool='same_draw')
        other = self.saved('same_draw')[2]
        self.assertEqual([r['company_id'] for r in state['selected']], [r['company_id'] for r in other['selected']])
        self.assertEqual(frame, self.saved('same_draw')[1])
        csv = pd.read_csv(Path(result['artifacts'])/'pool.csv')
        for sector, group in csv.groupby('sector'):
            self.assertTrue((group.pool_inclusion_probability == state['allocations'][sector]/frame['sector_populations'][sector]).all())
            self.assertAlmostEqual(prefix_probability(state, frame['sector_populations'], sector, 100),
                                   state['allocations'][sector]/frame['sector_populations'][sector]/15)
        unused = json.loads((Path(result['artifacts'])/'unused_by_sector.json').read_text())
        self.assertEqual(sum(map(len, unused.values())), 300)
        self.assertTrue((directory/'frame.json').exists())

    def test_extension_preserves_all_old_rows_batches_and_artifacts(self):
        result = self.create(235)
        directory, frame, old, _ = self.saved()
        before = {str(p): file_hash(p) for p in directory.rglob('*') if p.is_file()}
        batch = 'test_batch_0001'
        registry = [tuple(r) for r in self.connection.execute('SELECT * FROM sample_companies WHERE sample_id=? ORDER BY sample_rank', (batch,))]
        # A new eligible firm and failures do not alter the saved frame or trigger replacements.
        self.connection.execute("INSERT INTO firms(bvd_id,firm_name,normalized_domain,nace_code) VALUES ('new-later','Later','later.at','J620')")
        self.connection.execute("INSERT INTO scraped_pages(url,domain,scrape_status) VALUES ('https://site0.at','site0.at','timeout')")
        self.connection.commit()
        expanded = build_pool('extend', 'test', 500, database=self.db, root=self.root)
        new = self.saved()[2]
        self.assertEqual(new['selected'][:235], old['selected'])
        self.assertEqual(registry, [tuple(r) for r in self.connection.execute('SELECT * FROM sample_companies WHERE sample_id=? ORDER BY sample_rank', (batch,))])
        self.assertEqual(expanded['batches'], {'test_batch_0001':100, 'test_batch_0002':100, 'test_batch_0003':35,
                                              'test_batch_0004':100, 'test_batch_0005':100, 'test_batch_0006':65})
        self.assertNotIn('new-later', {r['company_id'] for r in new['selected']})
        for path, value in before.items(): self.assertEqual(file_hash(path), value)
        for s in frame['sector_populations']:
            self.assertGreaterEqual(new['allocations'][s], old['allocations'][s])
            self.assertAlmostEqual(prefix_probability(new, frame['sector_populations'], s, 235),
                                   old['allocations'][s]/frame['sector_populations'][s])
            self.assertAlmostEqual(prefix_probability(new, frame['sector_populations'], s, 500),
                                   new['allocations'][s]/frame['sector_populations'][s])

    def test_refuses_redraw_shrink_collisions_and_over_capacity(self):
        self.create(100)
        with self.assertRaises(FileExistsError): self.create(100)
        for n in (50, 100, 1801):
            with self.assertRaises(ValueError): build_pool('extend', 'test', n, database=self.db)
        with self.assertRaises(ValueError): build_pool('extend', 'test', 200, seed=4, database=self.db)
        self.connection.execute("INSERT INTO sample_sets(sample_id,sample_name,created_at) VALUES ('test_batch_0002','other','now')")
        self.connection.commit()
        with self.assertRaisesRegex(FileExistsError, 'collision'):
            build_pool('extend', 'test', 200, database=self.db)
        self.assertEqual(self.saved()[2]['target_size'], 100)

    def test_final_membership_is_independent_of_expansion_schedule(self):
        self.create(100, pool='direct')
        self.create(100, pool='staged')
        build_pool('extend', 'direct', 400, database=self.db)
        build_pool('extend', 'staged', 200, database=self.db)
        before = self.saved('staged')[2]['selected']
        build_pool('extend', 'staged', 400, database=self.db)
        direct = self.saved('direct')[2]; staged = self.saved('staged')[2]
        self.assertEqual(direct['allocations'], staged['allocations'])
        self.assertEqual({r['company_id'] for r in direct['selected']}, {r['company_id'] for r in staged['selected']})
        self.assertEqual(staged['selected'][:200], before)

    def test_registry_and_frame_tampering_fail_clearly(self):
        self.create(100)
        self.connection.execute("UPDATE sample_companies SET normalized_domain='changed.at' WHERE sample_id='test_batch_0001'")
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, 'membership mismatch'):
            build_pool('extend', 'test', 200, database=self.db)

    def test_frame_tampering_and_missing_development_context(self):
        self.create(100)
        directory, frame, _, _ = self.saved()
        frame['seed'] += 1
        (directory/'frame.json').write_text(json.dumps(frame))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            build_pool('extend', 'test', 200, database=self.db)
        pd.DataFrame({'bvd_id': ['D000'], 'normalized_domain': ['development.at']}).to_parquet(self.dev/'companies.parquet')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.create(100, pool='bad_context')

    def test_extension_failure_preserves_previous_registry(self):
        from ai_ecosystem.calibration import build_acquisition_pool as builder
        self.create(100)
        old = [tuple(r) for r in self.connection.execute('SELECT * FROM sample_companies ORDER BY sample_id,sample_rank')]
        real = builder.register
        def fail(*args):
            real(*args)
            raise RuntimeError('extension interrupted')
        with patch.object(builder, 'register', side_effect=fail):
            with self.assertRaises(RuntimeError): build_pool('extend', 'test', 200, database=self.db)
        self.assertEqual(old, [tuple(r) for r in self.connection.execute('SELECT * FROM sample_companies ORDER BY sample_id,sample_rank')])
        self.assertEqual(self.saved()[2]['target_size'], 100)

    def test_newly_ineligible_saved_member_is_not_replaced(self):
        self.create(100)
        _, frame, state, _ = self.saved()
        next_row = advance(frame, 'test', 200, state)['selected'][100]
        self.connection.execute('INSERT INTO domains(domain,is_excluded) VALUES (?,1)', (next_row['normalized_domain'],))
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, 'no replacement'):
            build_pool('extend', 'test', 200, database=self.db)
        self.assertEqual(self.saved()[2]['target_size'], 100)

    def test_transaction_rolls_back_all_samples_on_failure(self):
        from ai_ecosystem.calibration import build_acquisition_pool as builder
        real = builder.register
        def fail(*args):
            real(*args)
            raise RuntimeError('synthetic interruption')
        with patch.object(builder, 'register', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'interruption'): self.create(100)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM sample_sets').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM sample_companies').fetchone()[0], 0)
        # Interrupted files are retained, not overwritten or treated as a registered pool.
        with self.assertRaises(FileExistsError): self.create(100)


if __name__ == '__main__':
    unittest.main()
