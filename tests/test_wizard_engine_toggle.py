"""Engine toggle source reuse and persistence on isolated projects."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from tools import wizard


class EngineToggleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / 'data' / 'map'
        self.project.mkdir(parents=True)
        self.city = self.root / 'cities' / 'map.yaml'
        self.patches = [patch.object(wizard, 'ROOT_DIR', str(self.root)),
                        patch.object(wizard, 'DATA_DIR', str(self.root / 'data')),
                        patch.object(wizard, 'CITIES_DIR', str(self.root / 'cities'))]
        for item in self.patches:
            item.start()
        self.cpv('31')
        self.data = dict(city=dict(code='TST', name='Map', bbox=[-90, 20, -89, 22], residential_placement='official_blocks'),
                         data_dir='data/map', demand=dict(engine='v2', target_year=2025,
                             wizard_legacy_settings=dict(residential_placement='legacy', macro=dict(projection_year=2026))),
                         macroeconomics=dict(projection_year=2025, residential_employment='census_employed',
                             workplace_employment='auto', demographic_reference=dict(mode='eic2025', indicators='', persons=[],
                                                                                   pending_download=True, previous_projection_year=2026)))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def cpv(self, state):
        (self.project / f'RESAGEBURB_{state}CSV20.csv').write_text(
            'ENTIDAD,MUN,LOC,AGEB,MZA,POBTOT,P_15YMAS,POCUPADA\n'+state+',050,0,0,0,100,80,60\n', encoding='utf-8')

    def eic(self, folder=None, states=('31',)):
        folder = folder or self.project
        (folder / 'conjunto_datos_eic2025_105.csv').write_text(
            'CVE_ENT,CVE_MUN,CVE_LOC,ESTIMADOR,POBTOT,POCUPADA\n31,050,0000,Valor,100,60\n', encoding='utf-8')
        for state in states:
            (folder / f'personas{state}.csv').write_text(
                'CVEGEO,ID_PERSONA,EDAD,CONACT,TIE_TRASLADO_TRAB,FACTOR,MUN_TRAB,ENT_PAIS_TRAB\n'
                + state+'050,x,30,10,2,1,050,'+state+'\n', encoding='utf-8')

    def test_reuses_project_eic_and_roundtrips_engine_and_previous_settings(self):
        self.eic()
        wizard.save_full_city_data(str(self.city), self.data)
        loaded = wizard.load_city_data(str(self.city))
        ref = loaded['macroeconomics']['demographic_reference']
        self.assertNotIn('pending_download', ref)
        self.assertEqual(ref['indicators'], 'data/map/conjunto_datos_eic2025_105.csv')
        self.assertEqual(ref['persons'], ['data/map/personas31.csv'])
        self.assertEqual(loaded['demand']['wizard_legacy_settings'], self.data['demand']['wizard_legacy_settings'])
        self.assertEqual(loaded['demand']['engine'], 'v2')
        from sb_mexico.demand_v2.request import prepare_request
        request = prepare_request(loaded, self.root)
        self.assertEqual(request.sources['eic_persons'], [str(self.project / 'personas31.csv')])

    def test_shared_national_eic_can_be_reused_but_other_projects_cannot(self):
        other = self.root / 'data' / 'other'
        other.mkdir(); self.eic(other)
        wizard.bind_available_engine_sources(self.data)
        self.assertTrue(self.data['macroeconomics']['demographic_reference']['pending_download'])
        self.eic(self.root / 'data')
        wizard.bind_available_engine_sources(self.data)
        self.assertEqual(self.data['macroeconomics']['demographic_reference']['persons'], ['data/personas31.csv'])

    def test_multistate_partial_or_invalid_files_remain_pending(self):
        self.cpv('23'); self.eic()
        wizard.bind_available_engine_sources(self.data)
        self.assertTrue(self.data['macroeconomics']['demographic_reference']['pending_download'])
        self.eic(states=('23','31'))
        (self.project / 'personas23.csv').write_text('wrong,columns\n1,2\n')
        wizard.bind_available_engine_sources(self.data)
        self.assertTrue(self.data['macroeconomics']['demographic_reference']['pending_download'])
        self.eic(states=('23','31'))
        wizard.bind_available_engine_sources(self.data)
        self.assertEqual(set(self.data['macroeconomics']['demographic_reference']['persons']),
                         {'data/map/personas23.csv','data/map/personas31.csv'})

    def test_legacy_or_complete_manual_references_are_preserved(self):
        self.eic()
        self.data['demand']['engine'] = 'legacy'
        original = copy.deepcopy(self.data)
        wizard.bind_available_engine_sources(self.data)
        self.assertEqual(self.data, original)
        self.data['demand']['engine'] = 'v2'
        self.data['macroeconomics']['demographic_reference'] = dict(mode='eic2025',indicators='manual.csv',persons=['custom.csv'])
        original = copy.deepcopy(self.data)
        wizard.bind_available_engine_sources(self.data)
        self.assertEqual(self.data, original)

    def test_http_save_returns_bound_reference_for_form_and_preview(self):
        self.eic()
        server = wizard.WizardHTTPServer(('127.0.0.1',0),wizard.WizardRequestHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        try:
            client.request('POST','/api/city/save',json.dumps(dict(file=str(self.city),**self.data)),
                           {'Content-Type':'application/json'})
            response = client.getresponse(); payload = json.loads(response.read())
            self.assertEqual(response.status,200)
            self.assertEqual(payload['demographic_reference']['persons'],['data/map/personas31.csv'])
            self.assertNotIn('pending_download',payload['demographic_reference'])
        finally:
            client.close();server.shutdown();server.server_close();thread.join()

    def test_source_status_uses_explicit_candidate_inputs_and_reports_missing_files(self):
        self.eic()
        external = self.root / 'external'
        external.mkdir()
        cpv = external / 'RESAGEBURB_31CSV20.csv'
        cpv.write_bytes((self.project / cpv.name).read_bytes())
        denue = external / 'denue_inegi_31.csv'
        denue.write_text('cve_ent,cve_mun,codigo_act,per_ocu,latitud,longitud\n31,050,722511,1,21,-89\n')
        marco = external / '31m.geojson'
        marco.write_text('{}')
        self.data['demand']['sources'] = dict(cpv=[str(cpv)],denue=[str(denue)],marco=[str(marco)])
        wizard.save_full_city_data(str(self.city), self.data)
        status = wizard.inspect_data_files(city_file=str(self.city))
        self.assertTrue(status['all_ready'])
        self.assertEqual(status['cpv']['files'][0]['abs_path'],str(cpv))
        self.assertEqual(status['marco']['status'],'ok')
        denue.unlink()
        status = wizard.inspect_data_files(city_file=str(self.city))
        self.assertFalse(status['all_ready'])
        self.assertEqual(status['denue']['missing_paths'],[str(denue)])


if __name__ == '__main__':
    unittest.main()
