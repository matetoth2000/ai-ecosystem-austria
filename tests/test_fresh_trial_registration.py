import csv
import unittest
from unittest.mock import patch

from test_acquisition_pool import AcquisitionPoolTests
from ai_ecosystem.calibration.fresh_trial_registration import register_fresh_trial
from ai_ecosystem.calibration.score_company_sample import sample_company_ids
from ai_ecosystem.discovery.discovery_utils import get_sample_domains


class FreshTrialTests(unittest.TestCase):
    def setUp(self):
        fixture = AcquisitionPoolTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture
        fixture.connection.execute('''INSERT OR IGNORE INTO domains(domain,is_excluded)
            SELECT DISTINCT normalized_domain,0 FROM firms WHERE normalized_domain IS NOT NULL''')
        fixture.connection.commit()
        fixture.create(size=100, pool='validation')

    def register(self, name='fresh', **kwargs):
        return register_fresh_trial(name, 20261119, database=self.f.db, root=self.f.root,
                                    development_directory=self.f.dev, **kwargs)

    def registries(self):
        return {t: [tuple(r) for r in self.f.connection.execute(f'SELECT * FROM {t} ORDER BY 1,2')]
                for t in ('sample_sets', 'sample_companies', 'sample_domains')}

    def test_dry_run_exact_registration_and_no_overwrite(self):
        before = self.registries()
        preview = self.register(dry_run=True)
        self.assertEqual(preview, self.register(dry_run=True))
        self.assertEqual(before, self.registries())
        self.assertFalse((self.f.root/'data/interim/calibration/acquisition_trials/fresh').exists())
        result = self.register()
        self.assertEqual(result['selection_sha256'], preview['selection_sha256'])
        with open(result['artifacts']+'/companies.csv') as stream:
            rows = list(csv.DictReader(stream))
        ids = {r['company_id'] for r in rows}
        domains = {r['normalized_domain'] for r in rows}
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(domains), 100)
        self.assertEqual(ids, set(sample_company_ids(self.f.db, 'fresh')))
        self.assertEqual(domains, set(get_sample_domains(self.f.connection, 'fresh')))
        for table, old in before.items():
            self.assertEqual(old, [r for r in self.registries()[table] if r[0] != 'fresh'])
        with self.assertRaises(FileExistsError): self.register()

    def test_history_alias_and_raw_directory_exclusions(self):
        c = self.f.connection
        c.execute("INSERT INTO domain_discovery_status(domain,homepage_status) VALUES ('site0.at','failed')")
        c.execute("INSERT INTO discovered_pages(domain,url,normalized_url) VALUES ('site1.at','https://site2.at/','https://site2.at/')")
        c.execute("INSERT INTO candidate_pages(domain,url,source_domain) VALUES ('site3.at','https://site4.at/','site5.at')")
        c.execute("INSERT INTO scraped_pages(domain,url,final_url) VALUES ('site6.at','https://site6.at/','https://site7.at/')")
        c.execute("INSERT INTO firm_urls(bvd_id,domain,normalized_url,is_primary) VALUES ('N0024','alias.at','https://alias.at',1)")
        (self.f.root/'data/raw/html/site9.at').mkdir(parents=True)
        c.commit()
        result = self.register()
        domains = set(get_sample_domains(c, 'fresh'))
        self.assertFalse(domains & {f'site{i}.at' for i in range(8)})
        self.assertNotIn('site9.at', domains)
        self.assertNotIn('N0024', sample_company_ids(self.f.db, 'fresh'))
        self.assertGreater(result['history_unique_host_counts']['scraped_pages'], 0)

    def test_capacity_and_transaction_rollback(self):
        before = self.registries()
        with patch('ai_ecosystem.calibration.fresh_trial_registration.get_sample_domains', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'rolled back'): self.register('rollback')
        self.assertEqual(before, self.registries())
        with self.assertRaises(FileExistsError): self.register('rollback')
        self.f.connection.execute('UPDATE domains SET is_excluded=1')
        self.f.connection.commit()
        with self.assertRaisesRegex(ValueError, 'Need 100'): self.register()
        self.assertEqual(before, self.registries())

    def test_requires_exact_size_and_distinct_valid_id(self):
        for size in (99, 101):
            with self.assertRaises(ValueError): self.register(target_size=size)
        with self.assertRaises(ValueError): self.register('../unsafe')
        with self.assertRaises(FileExistsError): self.register('validation')
