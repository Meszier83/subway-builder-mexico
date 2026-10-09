"""EIC acquisition, source selection and project binding with isolated states."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import pandas as pd
import yaml
from sb_mexico import source_downloads as d
from sb_mexico.demographic_reference import validate_reference
from tools import wizard
from tests import test_source_downloads as download_fixtures
from tests import test_demographic_reference as reference_fixtures


class EicSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'data' / 'test'
        self.project.mkdir(parents=True)
        self.raw = self.root / 'raw'
        self.raw.mkdir()
        self.macro = reference_fixtures.sources(self.raw, ['01001', '02002'])
        self.geo = dict(states=[dict(code='01', name='Aguascalientes'), dict(code='02', name='Baja California')],
                        municipalities=[dict(code='01001', state='01'), dict(code='02002', state='02')])
        self.config = self.root / 'test.yaml'
        self.cfg = dict(city=dict(code='TST', bbox=[0, 0, 1, 1]), data_dir=str(self.project), macroeconomics=self.macro)
        self.config.write_text(yaml.safe_dump(self.cfg), encoding='utf-8')

    def client(self, incomplete=False):
        files = {}
        path = self.root / 'indicators.zip'
        download_fixtures.archive(path, {'data/'+d.EIC_INDICATOR_NAME: (self.raw/'indicators.csv').read_bytes()})
        files[d.EIC_INDICATORS] = path
        people = pd.read_csv(self.raw/'persons.csv', dtype=str)
        for state in ('01', '02'):
            rows = people[people.CVEGEO.str.startswith(state)]
            if incomplete and state == '02':
                rows = rows.iloc[1:]
            path = self.root / ('micro'+state+'.zip')
            download_fixtures.archive(path, {'conjunto_de_datos/personas'+state+'.csv': rows.to_csv(index=False),
                                            'diccionario.csv': 'ignored'})
            files[d.eic_persons_url(state)] = path
        return download_fixtures.FixtureClient(files=files)

    def test_all_intersecting_states_are_reconciled_and_published_with_provenance(self):
        client = self.client()
        paths = d.acquire_source(client, self.geo, self.project, 'eic')
        self.assertEqual({Path(p).name for p in paths}, {d.EIC_INDICATOR_NAME, 'personas01.csv', 'personas02.csv'})
        self.assertEqual(len(client.calls), 3)
        manifest = d.read_manifest(self.project)
        for name, entry in manifest['files'].items():
            self.assertEqual(entry['kind'], 'eic')
            self.assertEqual(entry['sha256'], d.sha256(self.project/name))
            self.assertEqual([r['year'] for r in entry['sources']], [2025]*3)
        self.assertEqual(paths, d.acquire_source(client, self.geo, self.project, 'eic'))

    def test_incomplete_microdata_does_not_publish_partial_states(self):
        (self.project/'manual.txt').write_text('preserve')
        with self.assertRaisesRegex(ValueError, '02002'):
            d.acquire_source(self.client(incomplete=True), self.geo, self.project, 'eic')
        self.assertEqual([p.name for p in self.project.iterdir()], ['manual.txt'])

    def test_wrong_state_duplicate_members_and_manual_files_are_not_accepted(self):
        client = self.client()
        download_fixtures.archive(client.files[d.eic_persons_url('02')], {'personas01.csv': 'wrong state'})
        with self.assertRaisesRegex(ValueError, 'personas02'):
            d.acquire_source(client, self.geo, self.project, 'eic')
        client = self.client()
        download_fixtures.archive(client.files[d.EIC_INDICATORS], {'a/'+d.EIC_INDICATOR_NAME: 'bad', 'b/'+d.EIC_INDICATOR_NAME: 'bad'})
        with self.assertRaisesRegex(ValueError, 'único'):
            d.acquire_source(client, self.geo, self.project, 'eic')
        (self.project/'personas01.csv').write_text('manual')
        with self.assertRaisesRegex(ValueError, 'manual'):
            d.acquire_source(self.client(), self.geo, self.project, 'eic')
        self.assertEqual((self.project/'personas01.csv').read_text(), 'manual')
        self.assertFalse((self.project/d.EIC_INDICATOR_NAME).exists())

    def manager(self):
        return d.DownloadManager(self.root, self.root/'data', lambda _: yaml.safe_load(self.config.read_text()), str)

    def test_plan_recommendations_pending_config_and_completed_binding_paths(self):
        self.cfg['macroeconomics']['demographic_reference'] = dict(mode='eic2025', indicators='', persons=[], pending_download=True)
        with patch.object(wizard, 'ROOT_DIR', str(self.root)), patch.object(wizard, 'DATA_DIR', str(self.root/'data')):
            wizard.save_full_city_data(str(self.config), self.cfg)
        with self.assertRaisesRegex(ValueError, 'pending'):
            validate_reference(self.cfg['macroeconomics'])
        manager = self.manager()
        with patch.object(d, 'Client', return_value=self.client()), patch.object(d, 'geography', return_value=self.geo):
            plan = manager.prepare(str(self.config))
            recommendations = {s['kind']: s['recommended'] for s in plan['sources']}
            self.assertTrue(recommendations['eic'])
            self.assertFalse(recommendations['conapo'])
            job = manager.start(plan['id'], ['eic'], None, 2026, 2)
            for _ in range(300):
                job = manager.status(job['id'])
                if job['status'] != 'running':
                    break
                time.sleep(.01)
        self.assertEqual(job['status'], 'complete', job)
        ref = job['results'][0]['demographic_reference']
        self.assertEqual(ref['persons'], ['data/test/personas01.csv', 'data/test/personas02.csv'])
        self.assertEqual(ref['indicators'], 'data/test/'+d.EIC_INDICATOR_NAME)
        self.assertTrue(yaml.safe_load(self.config.read_text())['macroeconomics']['demographic_reference']['pending_download'])

    def test_manual_shared_reference_blocks_automatic_replacement(self):
        manager = self.manager()
        plan = dict(file=str(self.config), folder=str(self.project), exclusions=['persons.csv'])
        self.assertEqual(set(manager.conflicts(plan, 'eic')), {str(self.raw/'indicators.csv'), str(self.raw/'persons.csv')})

    def test_project_without_eic_keeps_projection_download_recommendations(self):
        self.cfg['macroeconomics'].pop('demographic_reference')
        self.config.write_text(yaml.safe_dump(self.cfg))
        with patch.object(d, 'Client', return_value=self.client()), patch.object(d, 'geography', return_value=self.geo):
            plan = self.manager().prepare(str(self.config))
        sources = {s['kind']: s for s in plan['sources']}
        self.assertFalse(sources['eic']['recommended'])
        self.assertTrue(sources['conapo']['recommended'])

    def test_panel_uses_only_explicit_sources_and_marks_conapo_inactive(self):
        for name in ('denue_01.csv', 'cpv.csv', 'conapo.csv'):
            (self.project/name).write_text('header\n')
        with patch.object(wizard, 'ROOT_DIR', str(self.root)), patch.object(wizard, 'DATA_DIR', str(self.root/'data')):
            status = wizard.inspect_data_files(city_file=str(self.config))
            self.assertEqual(status['eic']['status'], 'ok')
            self.assertTrue(status['eic']['required'])
            self.assertTrue(all(f['shared'] for f in status['eic']['files']))
            self.assertFalse(status['conapo']['active'])
            self.assertTrue(all(not f['selected'] for f in status['conapo']['files']))
            self.assertTrue(status['all_ready'])
            (self.raw/'persons.csv').unlink()
            status = wizard.inspect_data_files(city_file=str(self.config))
            self.assertEqual(status['eic']['status'], 'missing')
            self.assertEqual(status['eic']['missing_paths'], [str(self.raw/'persons.csv')])
            self.assertFalse(status['all_ready'])
            (self.raw/'persons.csv').write_text('wrong_header\n')
            self.assertEqual(wizard.inspect_data_files(city_file=str(self.config))['eic']['status'], 'invalid')
            self.cfg['macroeconomics'].pop('demographic_reference')
            self.config.write_text(yaml.safe_dump(self.cfg))
            status = wizard.inspect_data_files(city_file=str(self.config))
            self.assertEqual(status['eic']['status'], 'inactive')
            self.assertFalse(status['eic']['required'])
            self.assertTrue(status['conapo']['active'])


if __name__ == '__main__':
    unittest.main()
