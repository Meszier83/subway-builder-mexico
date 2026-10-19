"""Comprehensive verification of Demand v2, Step reordering, grid_size, and legacy UI removal."""
import copy
import http.client
import json
from pathlib import Path
import threading
import unittest
import urllib.parse

from sb_mexico.config_defaults import apply_demand_defaults
from tools import wizard


class TestWizardR2AdversarialReview(unittest.TestCase):
    def test_r1_apply_demand_defaults(self):
        # Empty config gets v2
        c1 = apply_demand_defaults({})
        self.assertEqual(c1['demand']['engine'], 'v2')
        self.assertEqual(c1['city']['residential_placement'], 'official_blocks')
        self.assertEqual(c1['macroeconomics']['residential_employment'], 'census_employed')
        self.assertEqual(c1['macroeconomics']['workplace_employment'], 'auto')

        # Existing v2 preserved
        c2 = apply_demand_defaults({'demand': {'engine': 'v2'}})
        self.assertEqual(c2['demand']['engine'], 'v2')

        # Historical engine: legacy explicitly preserved
        c3 = apply_demand_defaults({'demand': {'engine': 'legacy'}})
        self.assertEqual(c3['demand']['engine'], 'legacy')

        # Legacy macroeconomics triggers legacy default
        c4 = apply_demand_defaults({'macroeconomics': {'tasa_pea': 0.62}})
        self.assertEqual(c4['demand']['engine'], 'legacy')

        c5 = apply_demand_defaults({'macroeconomics': {'til_1_state': 0.45}})
        self.assertEqual(c5['demand']['engine'], 'legacy')

        c6 = apply_demand_defaults({'macroeconomics': {'default_growth_factor': 1.05}})
        self.assertEqual(c6['demand']['engine'], 'legacy')

        c7 = apply_demand_defaults({'macroeconomics': {'workplace_employment': 'legacy'}})
        self.assertEqual(c7['demand']['engine'], 'legacy')

        # Explicit v2 overrides legacy macroeconomics
        c8 = apply_demand_defaults({'demand': {'engine': 'v2'}, 'macroeconomics': {'tasa_pea': 0.62}})
        self.assertEqual(c8['demand']['engine'], 'v2')

    def test_r1_points_default_grid_size(self):
        from sb_mexico.demand_v2.request import DemandRequest
        from sb_mexico.demand_v2 import points
        # Verify function uses 0.0025 default
        import inspect
        src = inspect.getsource(points.build_points)
        self.assertIn("grid_size=city.get('grid_size', .0025)", src)

    def test_r2_html_structure(self):
        root = Path(wizard.ROOT_DIR)
        html = (root / 'tools/templates/wizard.html').read_text(encoding='utf-8')

        # 1. Stepper order: Step 3 is POI Studio, Step 4 is Demanda & Cohortes
        step3_pos = html.find('id="stepTab-3"')
        step4_pos = html.find('id="stepTab-4"')
        self.assertGreater(step3_pos, 0)
        self.assertGreater(step4_pos, step3_pos)

        step3_snippet = html[step3_pos:step4_pos]
        self.assertIn('POI Studio', step3_snippet)

        step5_pos = html.find('id="stepTab-5"')
        step4_snippet = html[step4_pos:step5_pos]
        self.assertIn('Demanda & Cohortes', step4_snippet)

        # 2. Step 1 has NO cfg_grid_size and NO cfg_demand_engine
        step1_pos = html.find('id="stepView-1"')
        step2_pos = html.find('id="stepView-2"')
        step1_snippet = html[step1_pos:step2_pos]
        self.assertNotIn('cfg_grid_size', step1_snippet)
        self.assertNotIn('cfg_demand_engine', step1_snippet)

        # 3. Step 3 (POI Studio) view comes BEFORE Step 4 (Demanda & Cohortes) view
        view3_pos = html.find('id="stepView-3"')
        view4_pos = html.find('id="stepView-4"')
        view5_pos = html.find('id="stepView-5"')
        self.assertGreater(view3_pos, 0)
        self.assertGreater(view4_pos, view3_pos)
        self.assertGreater(view5_pos, view4_pos)

        # 4. View 3 is POI Studio
        view3_snippet = html[view3_pos:view4_pos]
        self.assertIn('POI STUDIO', view3_snippet)
        self.assertIn('id="mapPoi"', view3_snippet)

        # 5. View 4 is Demanda & Cohortes and contains cfg_grid_size in advanced settings
        view4_snippet = html[view4_pos:view5_pos]
        self.assertIn('id="cfg_grid_size"', view4_snippet)
        self.assertIn('value="0.0025" selected', view4_snippet)
        self.assertIn('value="0.0035"', view4_snippet)
        self.assertIn('value="0.0018"', view4_snippet)
        self.assertIn('id="v2DemandPanel"', view4_snippet)

        # 6. Legacy panels are completely absent from the DOM
        for forbidden in ['legacyMacroHeader', 'legacyMacroRates', 'legacyModalPanel', 'legacyConapoPanel', 'cohort_fps_badge']:
            self.assertNotIn(f'id="{forbidden}"', html, f'Found {forbidden} in wizard.html')

    def test_r2_r3_http_server(self):
        server = wizard.WizardHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        port = server.server_port
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            client = http.client.HTTPConnection('127.0.0.1', port, timeout=10)

            # Test GET /
            client.request('GET', '/')
            resp = client.getresponse()
            self.assertEqual(resp.status, 200)
            html = resp.read().decode('utf-8')
            self.assertIn('<!DOCTYPE html>', html)
            self.assertNotIn('cfg_demand_engine', html)

            # Test GET /api/cities
            client.request('GET', '/api/cities')
            resp = client.getresponse()
            self.assertEqual(resp.status, 200)
            cities_payload = json.loads(resp.read().decode('utf-8'))
            self.assertIn('cities', cities_payload)
            self.assertGreater(len(cities_payload['cities']), 0)

            # Test GET /api/instance
            client.request('GET', '/api/instance')
            resp = client.getresponse()
            self.assertEqual(resp.status, 200)
            inst = json.loads(resp.read().decode('utf-8'))
            self.assertIn('instance_id', inst)

            # Test manifest hash parity
            manifest = json.loads((Path(wizard.ROOT_DIR) / 'tools/static/wizard/manifest.json').read_text(encoding='utf-8'))
            import hashlib
            for filename, expected_sha in manifest['files'].items():
                client.request('GET', f'/static/wizard/{filename}')
                asset_resp = client.getresponse()
                self.assertEqual(asset_resp.status, 200)
                body = asset_resp.read()
                actual_sha = hashlib.sha256(body).hexdigest()
                self.assertEqual(actual_sha, expected_sha, f'Hash mismatch on {filename}')

            client.close()
        finally:
            server.shutdown()
            server.server_close()
            t.join()

    def test_r2_merida_and_null_growth_factors_guard(self):
        root = Path(wizard.ROOT_DIR)
        app_js = (root / 'tools/static/wizard/app.js').read_text(encoding='utf-8')

        # Verify renderGrowthFactorsTable guards growthFactorsTableBody against null
        import re
        match = re.search(r'function renderGrowthFactorsTable\(\) \{([\s\S]*?)\n    \}', app_js)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertIn("const tbody = document.getElementById('growthFactorsTableBody');", body)
        self.assertIn("if (!tbody) return;", body)

        # Verify all projects in cities/ can be loaded by wizard backend
        import yaml
        cities_dir = root / 'cities'
        for yaml_path in cities_dir.glob('*.yaml'):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            self.assertIn('city', cfg, f"Missing 'city' in {yaml_path}")
            self.assertIn('macroeconomics', cfg, f"Missing 'macroeconomics' in {yaml_path}")


if __name__ == '__main__':
    unittest.main()

