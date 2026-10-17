import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np,yaml
from tools import wizard
from sb_mexico.gravity import assign_zones

class IsolatedPolygonTests(unittest.TestCase):
    polygon=[[0,0],[2,0],[2,1],[1,1],[1,2],[0,2],[0,0]]
    def test_actual_contour_and_boundary_not_envelope(self):
        zone=dict(id='island',polygon=self.polygon,bbox=[0,0,2,2])
        np.testing.assert_array_equal(assign_zones(np.array([[.5,.5],[1.5,1.5],[1,1.5],[0,0]]),[zone]),[1,0,1,1])
        np.testing.assert_array_equal(assign_zones(np.array([[.5,.5]]),[dict(zone,enabled=False)]),[0])
    def test_legacy_box(self):
        np.testing.assert_array_equal(assign_zones(np.array([[0,0],[1,1],[2,2]]),[dict(bbox=[0,0,1,1])]),[1,1,0])
    def test_save_reload_and_preflight(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder, patch.multiple(wizard,ROOT_DIR=folder,CITIES_DIR=folder):
            cfg=Path(folder)/'test.yaml'
            data=dict(city=dict(name='Test',code='TST',bbox=[0,0,3,3]),isolated_zones=[dict(id='island',name='Island',polygon=self.polygon,enabled=False),dict(id='old',bbox=[0,0,1,1])])
            wizard.save_full_city_data(str(cfg),data)
            saved=yaml.safe_load(cfg.read_text(encoding='utf-8'))
            self.assertEqual(saved['isolated_zones'][0]['polygon'],self.polygon)
            self.assertNotIn('bbox',saved['isolated_zones'][0]);self.assertFalse(saved['isolated_zones'][0]['enabled'])
            self.assertEqual(wizard.load_city_data(str(cfg))['isolated_zones'][0]['polygon'],self.polygon)
            self.assertFalse([e for e in wizard.validate_city_configuration(str(cfg))['errors'] if 'aislada' in e])
            data['isolated_zones'][0]['polygon']=[[0,0],[1,1],[0,1],[1,0]]
            wizard.save_full_city_data(str(cfg),data)
            self.assertTrue([e for e in wizard.validate_city_configuration(str(cfg))['errors'] if 'polígono inválido' in e])

if __name__=='__main__':unittest.main()
