import os, sys, unittest, yaml
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path: sys.path.insert(0, ROOT_DIR)
from tools.wizard import save_full_city_data

class TestCameraViewport(unittest.TestCase):
    def test_write_city_yaml_with_initial_center(self):
        city_data = {
            'city': {
                'code': 'TEST',
                'name': 'Ciudad Test',
                'bbox': [-99.3, 19.2, -98.9, 19.6],
                'initial_zoom': 12.0,
                'initial_center': [-99.1332, 19.4326]
            }
        }
        temp_path = os.path.join(ROOT_DIR, 'cities', 'temp_test_camera.yaml')
        try:
            save_full_city_data(temp_path, city_data)
            with open(temp_path, 'r', encoding='utf-8') as f:
                loaded = yaml.safe_load(f)
            self.assertIn('city', loaded)
            self.assertEqual(loaded['city']['initial_center'], [-99.1332, 19.4326])
            self.assertEqual(loaded['city']['initial_zoom'], 12.0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_pipeline_initial_view_state_with_manual_center(self):
        manual_center = [-86.85123, 21.16456]
        city_info = {'name': 'Cancun Test', 'initial_zoom': 12.5, 'initial_center': manual_center}
        man_center = city_info.get('initial_center')
        center_lon = float(man_center[0])
        center_lat = float(man_center[1])
        view_state = {'zoom': city_info.get('initial_zoom', 12.0), 'latitude': round(center_lat, 5), 'longitude': round(center_lon, 5)}
        self.assertEqual(view_state['longitude'], -86.85123)
        self.assertEqual(view_state['latitude'], 21.16456)
        self.assertEqual(view_state['zoom'], 12.5)

    def test_label_spatial_suppression_logic(self):
        from shapely.geometry import Polygon, Point
        poly = Polygon([(-86.95, 21.10), (-86.80, 21.10), (-86.80, 21.22), (-86.95, 21.22), (-86.95, 21.10)])
        labels = [
            {'name': 'Cancun Centro', 'coords': [-86.85, 21.16]},
            {'name': 'Puerto Morelos', 'coords': [-86.87, 20.85]},
            {'name': 'Isla Contoy', 'coords': [-86.79, 21.50]},
            {'name': 'Supermanzana 20', 'coords': [-86.83, 21.15]}
        ]
        kept = [lbl for lbl in labels if poly.intersects(Point(lbl['coords'][0], lbl['coords'][1]))]
        self.assertEqual(len(kept), 2)
        names = [k['name'] for k in kept]
        self.assertIn('Cancun Centro', names)
        self.assertIn('Supermanzana 20', names)
        self.assertNotIn('Puerto Morelos', names)
        self.assertNotIn('Isla Contoy', names)

if __name__ == '__main__':
    unittest.main()
