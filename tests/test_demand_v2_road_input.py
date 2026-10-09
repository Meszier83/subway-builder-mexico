"""Preview/build use the same complete demand roads, separate from map delivery."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sb_mexico.demand_v2.integration import demand_roads,execute_candidate,preview_candidate

class RoadInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.out=self.root/'output';self.out.mkdir()
        def road(path,count):
            path.write_text(json.dumps({'type':'FeatureCollection','features':[
                {'type':'Feature','properties':{},'geometry':{'type':'LineString','coordinates':[[i,0],[i,1]]}}
                for i in range(count)]}))
        road(self.out/'roads.geojson',1);road(self.root/'full.geojson',2)
        self.cfg=dict(city={'code':'TEST','bbox':[0,0,2,2]},demand={'engine':'v2'},
                      routing={'use_osrm':False,'demand_roads_path':'full.geojson'})
    def tearDown(self):self.temp.cleanup()

    def test_preview_and_build_use_identical_complete_network_without_editing_map(self):
        original=(self.out/'roads.geojson').read_bytes();received=[]
        def build(request,*args):
            received.append([g.wkb for g in request.roads.geometry])
            return dict(identity='fixture',report={'export':{'commuters':0}},points=[],
                        game={'points':[],'pops':[]})
        with patch('sb_mexico.demand_v2.engine.build_demand',build),\
             patch('sb_mexico.demand_v2.engine.write_result',return_value=str(self.out/'demand_data.json')),\
             patch('sb_mexico.pipeline.package_demand_outputs',return_value='package'):
            preview_candidate(self.cfg,self.root,roads_path=self.out/'roads.geojson')
            execute_candidate(self.cfg,self.root,self.root/'data',self.out)
        self.assertEqual(received[0],received[1]);self.assertEqual(len(received[0]),2)
        self.assertEqual((self.out/'roads.geojson').read_bytes(),original)

    def test_missing_explicit_network_cannot_silently_fall_back_to_reduced_map(self):
        self.cfg['routing']['demand_roads_path']='missing.geojson'
        with self.assertRaisesRegex(ValueError,'Missing explicit'):demand_roads(self.cfg,self.root,self.out/'roads.geojson')

    def test_default_keeps_existing_map_road_selection(self):
        self.cfg['routing'].pop('demand_roads_path')
        self.assertEqual(len(demand_roads(self.cfg,self.root,self.out/'roads.geojson')),1)

if __name__=='__main__':unittest.main()
