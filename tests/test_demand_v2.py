import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, LineString
from sb_mexico.demand_v2 import build_demand, prepare_request
from sb_mexico.demand_v2.population import allocate_population, integer_budgets
from sb_mexico.demand_v2.allocation import allocate, support
from sb_mexico.demand_v2.engine import write_result, route_commutes


def point(id, lon, commuters=0, attraction=10, zone=0, municipality='23005', **kwargs):
    return dict(id=id, location=[lon,21.], commuters=commuters, attraction=attraction,
                zone=zone, municipality=municipality, **kwargs)


class AllocationTests(unittest.TestCase):
    def test_exact_origins_external_and_deterministic_rounding(self):
        points = [point('a',-86.9,31),point('b',-86.8,17),point('c',-86.7)]
        mobility = dict(flows=[dict(origin='23005',destination='99999',weight=100.,
                                   training_weight=80.,validation_weight=20.,records=20,effective_sample_size=20.)])
        cells, report = allocate(points, {}, mobility)
        self.assertEqual(sum(c['size'] for c in cells),48)
        self.assertEqual(sum(c['size'] for c in cells if c['origin']=='a'),31)
        self.assertEqual(report['redistributed_external']['23005']['retained_estimate'],48)
        self.assertEqual(cells,allocate(points,{},mobility)[0])
        self.assertTrue(all(c['origin']!=c['destination'] for c in cells))
        self.assertEqual(report['integer_row_max_residual'],0)
        self.assertGreaterEqual(report['rounding_l1_persons'],0)
        self.assertIn('conditional_kl',report['validation_integer'])

    def test_comparison_uses_identical_held_out_coverage(self):
        from tools.compare_demand_v2 import common_validation
        points=[dict(id='a',location=[0,0],municipality='A'),dict(id='b',location=[1,0],municipality='B'),
                dict(id='c',location=[2,0],municipality='C')]
        old=dict(points=points,pops=[dict(residenceId='a',jobId='b',size=10),dict(residenceId='a',jobId='c',size=90)])
        new=dict(points=points,pops=[dict(residenceId='a',jobId='b',size=10)])
        mobility=dict(flows=[dict(origin='A',destination='B',validation_weight=4),
                             dict(origin='A',destination='C',validation_weight=6)])
        result=common_validation(old,new,points,mobility)
        self.assertEqual(result['destinations'],['B'])
        self.assertEqual(result['scores']['legacy']['evaluated_weight'],4)
        self.assertEqual(result['scores']['candidate']['evaluated_weight'],4)
        self.assertEqual(result['scores']['legacy']['conditional_kl'],0)
        empty=dict(points=[],pops=[])
        self.assertEqual(common_validation(empty,empty,[],mobility)['scores']['candidate']['evaluated_weight'],0)

    def test_isolated_no_jobs_and_distance_fail(self):
        with self.assertRaisesRegex(ValueError,'without support'):
            allocate([point('a',-86.9,30,zone=1),point('b',-86.8,zone=2)],{}, {})
        with self.assertRaisesRegex(ValueError,'without support'):
            allocate([point('a',-86.9,30),point('b',-86.8)],{'max_distance_km':.01},{})

    def test_expand_sparse_support_to_permitted_destination(self):
        points=[point('a',-86.9,30,attraction=0,zone=1)]+[point(str(i),-86.9+i*.00001,zone=2) for i in range(150)]
        points.append(point('permitted',-86.,zone=1))
        self.assertEqual(allocate(points,{}, {})[0][0]['destination'],'permitted')

    def test_coincident_distinct_nodes_and_disconnected_roads(self):
        with self.assertRaisesRegex(ValueError,'without support'):
            allocate([point('a',-86.9,7),point('b',-86.9)],{}, {})
        cells,_=allocate([point('a',-86.9,7),point('b',-86.9),point('c',-86.8)],{}, {})
        self.assertEqual(cells[0]['destination'],'c')
        with self.assertRaisesRegex(ValueError,'without support'):
            support([point('a',-86.9,7,road_component=1),point('b',-86.8,road_component=2)],{})

    def test_zero_pool(self):
        self.assertEqual(allocate([point('a',-86.9)],{}, {})[0],[])


class PopulationTests(unittest.TestCase):
    def test_capacity_falsifier_and_nonspatial_residual(self):
        frame=pd.DataFrame([dict(cve_mun_clean='23005',pobtot_num=20.,pob15_num=20.,employed_2020=19.)])
        controls={'23005':{'Valor':(120.,80.)}}
        people={'23005':dict(population=120.,occupied=80.,commuters=80.,no_travel=0.,unspecified=0.)}
        result, report=allocate_population(frame,{'23005':(100.,60.)},controls,people)
        self.assertAlmostEqual(result.pobtot_adj.iloc[0],24.)
        self.assertLessEqual(result.occupied_residents.iloc[0],24.)
        self.assertAlmostEqual(result.occupied_residents.sum()+report['23005']['residual_nonspatial']['occupied'],80.)
        self.assertGreater(report['23005']['residual_nonspatial']['population'],0)

    def test_zero_controls_and_invalid_weights(self):
        frame=pd.DataFrame([dict(cve_mun_clean='23005',pobtot_num=20.,pob15_num=20.,employed_2020=0.)])
        people={'23005':dict(population=0.,occupied=0.,commuters=0.,no_travel=0.,unspecified=0.)}
        self.assertEqual(allocate_population(frame,{'23005':(100.,60.)},{'23005':{'Valor':(0.,0.)}},people)[0].pea_real.sum(),0)
        with self.assertRaisesRegex(ValueError,'exceed'):
            allocate_population(frame,{'23005':(10.,0.)},{'23005':{'Valor':(0.,0.)}},people)

    def test_hierarchical_integer_bounds(self):
        ids=['b','a','c']; groups=['x','x','y']
        p=integer_budgets([1.4,2.2,1.1],groups,ids)
        o=integer_budgets([1.4,1.9,.8],groups,ids,p)
        c=integer_budgets([1.3,1.8,.7],groups,ids,o)
        self.assertTrue((c<=o).all() and (o<=p).all())
        self.assertEqual(c[:2].sum(),3)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        rows=[dict(ENTIDAD=23,MUN=5,LOC='0000',AGEB='0000',MZA=0,POBTOT=100,POCUPADA=60,P_12YMAS=100,P_15YMAS=100)]
        for i, (population, occupied) in enumerate([(20,19),(80,41)],1):
            rows.append(dict(ENTIDAD=23,MUN=5,LOC='0001',AGEB='001A',MZA=i,POBTOT=population,
                             POCUPADA=occupied,P_12YMAS=population,P_15YMAS=population))
        pd.DataFrame(rows).to_csv(self.root/'cpv.csv',index=False)
        geo=gpd.GeoDataFrame([dict(CVEGEO='230050001001A001'),dict(CVEGEO='230050001001A002')],
            geometry=[Polygon([(-86.92,20.99),(-86.91,20.99),(-86.91,21.01),(-86.92,21.01)]),
                      Polygon([(-86.82,20.99),(-86.81,20.99),(-86.81,21.01),(-86.82,21.01)])],crs=4326)
        geo.to_file(self.root/'mza.geojson',driver='GeoJSON')
        # A distinct ordinary job makes the fixture feasible after coincident
        # workplaces are excluded and POIs receive explicit, bounded quotas.
        pd.DataFrame([dict(id=1,latitud=21,longitud=-86.815,cve_ent=23,cve_mun=5,cve_loc=1,ageb='001A',manzana=2,
                           per_ocu='11 a 30 personas',codigo_act='461110',nom_estab='Test'),
                      dict(id=2,latitud=21,longitud=-86.75,cve_ent=23,cve_mun=5,cve_loc=1,ageb='001A',manzana=3,
                           per_ocu='0 a 5 personas',codigo_act='541110',nom_estab='Ordinary job')]).to_csv(self.root/'denue.csv',index=False)
        indicators=[]
        for label,values in [('Valor',(120,80)),('Error estándar',(1,1)),('Límite inferior de confianza',(110,70)),
                             ('Límite superior de confianza',(130,90)),('Coeficiente de variación',(1,1))]:
            indicators.append(dict(CVE_ENT=23,CVE_MUN=5,CVE_LOC='0000',ESTIMADOR=label,POBTOT=values[0],POCUPADA=values[1]))
        pd.DataFrame(indicators).to_csv(self.root/'indicators.csv',index=False,encoding='cp1252')
        people=[dict(CVEGEO='23005',ID_PERSONA=str(i),EDAD=30,CONACT=10 if i<4 else 30,
                     TIE_TRASLADO_TRAB=1 if i<4 else '',FACTOR=20,MUN_TRAB='001',ENT_PAIS_TRAB='031') for i in range(6)]
        pd.DataFrame(people).to_csv(self.root/'persons.csv',index=False)
        self.config=dict(city=dict(name='Test',code='TST',bbox=[-87,20.9,-86.7,21.1],restrict_demand_to_urban_core=False,
                                   grid_size=.01,min_residents=0,min_jobs=0),macroeconomics=dict(workplace_employment='ce_bounded'),
                         demand=dict(engine='v2',target_year=2025,beta=.12,
                             sources=dict(cpv=['cpv.csv'],denue=['denue.csv'],marco=['mza.geojson'],ce=[],
                                          eic_indicators=['indicators.csv'],eic_persons=['persons.csv'])),
                         pois=[dict(id='UNI_Keep',loc=[-86.815,21.],jobs=33,radius_m=30,mode='BOOST')])

    def request(self):
        return prepare_request(self.config,self.root)

    def test_full_pipeline_exact_cells_pois_and_identity(self):
        original=copy.deepcopy(self.config)
        result=build_demand(self.request())
        self.assertEqual(sum(p['size'] for p in result['game']['pops']),80)
        self.assertEqual(self.config,original)
        self.assertIn('UNI_Keep',[p['id'] for p in result['game']['points']])
        self.assertEqual(result['report']['territory']['poi_audit'][0]['manual'],33)
        self.assertEqual(result['game'],build_demand(self.request())['game'])
        self.assertEqual(result['points'],build_demand(self.request(),'points')['points'])
        output=self.root/'output'; write_result(result,self.request(),output)
        self.assertEqual(json.loads((output/'demand_manifest.json').read_text())['identity'],result['identity'])
        people=pd.read_csv(self.root/'persons.csv',dtype=str)
        people.loc[0,'MUN_TRAB']='005'; people.to_csv(self.root/'persons.csv',index=False)
        self.assertNotEqual(build_demand(self.request(),'sources')['identity'],result['identity'])

    def test_exact_count_preview_export_save_and_identity(self):
        from tools import wizard
        import yaml
        self.config['macroeconomics'].update(min_pop_size=1,target_pop_size=20,max_pop_size=40)
        self.config['demand']['cohort_count']=4
        preview=build_demand(self.request(),'allocation')
        result=build_demand(self.request())
        self.assertEqual(preview['cells'],result['cells'])
        self.assertEqual(preview['report']['allocation']['cohorts']['actual_count'],4)
        self.assertEqual(len(result['game']['pops']),4)
        self.assertEqual(sum(p['size'] for p in result['game']['pops']),80)
        self.assertEqual(result['report']['territory']['poi_audit'][0]['manual'],33)
        config=self.root/'save.yaml'; config.write_text(yaml.safe_dump(self.config),encoding='utf-8')
        with patch.object(wizard,'ROOT_DIR',str(self.root)):
            wizard.save_full_city_data(str(config),copy.deepcopy(self.config))
        self.assertEqual(yaml.safe_load(config.read_text())['demand']['cohort_count'],4)
        self.config['demand']['cohort_count']=5
        changed=build_demand(self.request())
        self.assertEqual(len(changed['game']['pops']),5)
        self.assertNotEqual(result['identity'],changed['identity'])

    def test_local_fixed_groups_preview_export_and_statistical_separation(self):
        original=copy.deepcopy(self.config)
        for size in [50,100,200]:
            self.config['demand']['fixed_cohort_size']=size
            preview=build_demand(self.request(),'points')
            result=build_demand(self.request())
            self.assertEqual(preview['points'],result['points'])
            sizes=sorted(p['size'] for p in result['game']['pops'])
            self.assertEqual(sizes,[6,24,50] if size==50 else [24,56])
            self.assertEqual(sum(p['commuters'] for p in result['statistical_points']),80)
            self.assertEqual(result['report']['territory']['poi_audit'][0]['manual'],33)
            self.assertEqual(sum(p['population'] for p in result['statistical_points']),sum(p['population'] for p in result['points']))
        self.assertEqual(self.config['pois'],original['pois'])

    def test_partial_map_does_not_absorb_outside_and_missing_controls(self):
        self.config['city']['bbox'][2]=-86.85
        self.config['pois']=[]
        result=build_demand(self.request(),'points')
        self.assertEqual(result['report']['territory']['integer_population'],24)
        self.assertLessEqual(result['report']['territory']['integer_commuters'],24)
        self.config['demand']['sources']['eic_persons']=[]
        with self.assertRaisesRegex(ValueError,'complete state'):
            build_demand(self.request())

    def test_overlap_dedup_and_conflicting_destination(self):
        people=pd.read_csv(self.root/'persons.csv',dtype=str)
        people.to_csv(self.root/'duplicate.csv',index=False)
        self.config['demand']['sources']['eic_persons'].append('duplicate.csv')
        self.assertEqual(build_demand(self.request(),'points')['report']['territory']['integer_commuters'],80)
        people.loc[0,'MUN_TRAB']='005'; people.to_csv(self.root/'duplicate.csv',index=False)
        with self.assertRaisesRegex(ValueError,'Conflicting EIC mobility'):
            build_demand(self.request(),'points')

    def test_route_cache_coordinates_profile_and_no_route(self):
        req=self.request(); req.route_cache=self.root/'cache.json'
        pts=[dict(id='a',location=[-86.9,21]),dict(id='b',location=[-86.8,21])]
        def provider(pops,points,include):
            for p in pops: p.update(drivingDistance=2000,drivingSeconds=300)
            return {('a','b'):('osrm','ok')}
        req.route_provider=provider
        p=[dict(id='x',residenceId='a',jobId='b',size=3)]
        route_commutes(req,p,pts)
        req.route_provider=lambda *a: self.fail('cache missed')
        self.assertIn('cached_osrm',route_commutes(req,copy.deepcopy(p),pts)['provenance'])
        req.route_identity='other:car'
        req.route_provider=lambda *a: {('a','b'):('canonical','no_route')}
        with self.assertRaisesRegex(ValueError,'disconnected'):
            route_commutes(req,copy.deepcopy(p),pts)

    def test_candidate_pipeline_and_wizard_preserve_contract(self):
        import yaml
        from sb_mexico.pipeline import execute_pipeline
        from tools import wizard
        self.config['data_dir']=str(self.root)
        config=self.root/'city.yaml'
        config.write_text(yaml.safe_dump(self.config),encoding='utf-8')
        with patch('sb_mexico.pipeline.ROOT_DIR',str(self.root)):
            path=execute_pipeline(str(config),skip_map=True,output_dir=str(self.root/'compiled'))
        game=json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(sum(p['size'] for p in game['pops']),80)
        with patch.object(wizard,'ROOT_DIR',str(self.root)):
            wizard.save_full_city_data(str(config),copy.deepcopy(self.config))
        saved=yaml.safe_load(config.read_text(encoding='utf-8'))
        self.assertEqual(saved['demand'],self.config['demand'])
        self.assertEqual(saved['pois'],self.config['pois'])
        from sb_mexico.build_delivery import execute_wizard_build
        delivery=self.root/'delivery'; delivery.mkdir()
        (delivery/'TST.pmtiles').write_bytes(b'static package fixture; not a playable map')
        (delivery/'roads.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[]}),encoding='utf-8')
        with patch('sb_mexico.pipeline.ROOT_DIR',str(self.root)):
            artifact=execute_wizard_build(str(config),str(delivery),str(self.root),skip_map=True)
        self.assertEqual(artifact['status'],'success')
        self.assertEqual(artifact['commuters'],80)

    def test_auto_fine_ce_preview_and_compilation(self):
        import yaml
        from sb_mexico.pipeline import execute_pipeline
        from sb_mexico.demand_v2.integration import preview_candidate
        self.config['macroeconomics']['workplace_employment']='auto'
        self.config['demand']['fixed_cohort_size']=50
        self.config['routing']={'use_osrm':False}
        self.config['data_dir']=str(self.root)
        self.config['demand']['sources']['ce']=['SAIC.csv']
        pd.DataFrame([{'Año Censal':2023,'Entidad':'23 Quintana Roo','Municipio':'005 Benito Juarez',
                       'Actividad económica':'Clase 461110 Comercio','Estrato':'11 a 50',
                       'H001A Personal ocupado total':25,'UE Unidades económicas':1}]).to_csv(self.root/'SAIC.csv',index=False)
        original=copy.deepcopy(self.config)
        core=build_demand(self.request())
        preview=preview_candidate(self.config,self.root,stage='points')
        self.assertEqual(preview['points'],core['points'])
        report=core['report']['workplaces']
        self.assertEqual(report['transferred_establishments'],1)
        self.assertAlmostEqual(report['coverage']['bbox']['by_level']['class']['attraction'],25,places=6)
        config=self.root/'city.yaml'
        config.write_text(yaml.safe_dump(self.config),encoding='utf-8')
        with patch('sb_mexico.pipeline.ROOT_DIR',str(self.root)):
            path=execute_pipeline(str(config),skip_map=True,output_dir=str(self.root/'compiled'))
        game=json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(game,core['game'])
        self.assertEqual(self.config,original)
        self.assertEqual(yaml.safe_load(config.read_text(encoding='utf-8')),original)

    def test_osrm_disconnection_rebuilds_support_before_export(self):
        calls=[]
        blocked=set()
        def provider(pops,points,include):
            if not calls:
                chosen=next(p for p in pops if p['jobId']=='UNI_Keep')
                blocked.add((chosen['residenceId'],chosen['jobId']))
            calls.append(len(pops))
            return {(p['residenceId'],p['jobId']):('canonical','NoRoute')
                    if (p['residenceId'],p['jobId']) in blocked else ('osrm','accepted') for p in pops}
        request=prepare_request(self.config,self.root,route_provider=provider,route_identity='fixture-network:car')
        result=build_demand(request)
        self.assertEqual(len(calls),2)
        self.assertEqual(sum(p['size'] for p in result['game']['pops']),80)
        self.assertFalse(any((p['residenceId'],p['jobId']) in blocked for p in result['game']['pops']))
        self.assertEqual(result['report']['routing']['support_refinements'],1)
        self.assertEqual(result['report']['routing']['excluded_no_route_pairs'],[list(p) for p in sorted(blocked)])

    def test_native_http_candidate_is_passive_and_same_as_core(self):
        import http.client
        import threading
        import yaml
        from tools import wizard
        self.config['demand']['engine']='legacy'
        config=self.root/'city.yaml'; config.write_text(yaml.safe_dump(self.config),encoding='utf-8')
        patches=[patch.object(wizard,'ROOT_DIR',str(self.root)),patch.object(wizard,'DIST_DIR',str(self.root/'dist'))]
        for p in patches: p.start(); self.addCleanup(p.stop)
        server=wizard.WizardHTTPServer(('127.0.0.1',0),wizard.WizardRequestHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=20)
        try:
            client.request('GET','/api/demand-v2-preview?file='+str(config).replace('\\','/')+'&stage=allocation')
            response=client.getresponse(); body=json.loads(response.read())
            self.assertEqual(response.status,200,body)
            self.assertEqual(body['report']['allocation']['commuters'],80)
            self.assertEqual(yaml.safe_load(config.read_text())['demand']['engine'],'legacy')
            self.config['demand']['target_year'] = 2020
            config.write_text(yaml.safe_dump(self.config), encoding='utf-8')
            url='/api/demand-v2-preview?file='+str(config).replace('\\','/')+'&stage=points&async=1'
            client.request('GET',url)
            response=client.getresponse(); body=json.loads(response.read())
            import time
            deadline=time.monotonic()+20
            while body.get('status')=='running' and time.monotonic()<deadline:
                time.sleep(.01)
                client.request('GET',url+'&job='+body['job_id'])
                response=client.getresponse();body=json.loads(response.read())
            self.assertEqual(body.get('status'),'complete',body)
            self.assertEqual(response.status,200,body)
            from sb_mexico.demand_v2.integration import preview_candidate
            expected_config=wizard.load_city_data(str(config))
            self.assertEqual(expected_config['demand']['target_year'],2020)
            expected_config['demand']['engine']='v2'
            expected=preview_candidate(expected_config,self.root,stage='points')
            self.assertEqual(body['identity'],expected['identity'])
            expected=json.loads(json.dumps(expected))
            self.assertEqual(body['points'],expected['points'])
            self.assertEqual(body['report']['territory'],expected['report']['territory'])
        finally:
            client.close(); server.shutdown(); server.server_close(); thread.join()

    def test_candidate_workplace_status_matches_build_sources(self):
        import yaml
        from tools import wizard
        self.config['demand']['engine']='v2'
        self.config['macroeconomics']['workplace_employment']='auto'
        config=self.root/'status.yaml'
        config.write_text(yaml.safe_dump(self.config),encoding='utf-8')
        with patch.object(wizard,'ROOT_DIR',str(self.root)):
            status=wizard.automatic_workplace_status(str(config))
            expected=build_demand(prepare_request(wizard.load_city_data(str(config)),self.root),'points')['report']['workplaces']
        for key in ('mode','coverage','fallback_reasons','source_sha256'):
            self.assertEqual(status[key],expected[key])

    def test_geographic_crosswalk_and_missing_new_municipality(self):
        self.config['demand']['municipality_crosswalk']={'23005':'23006'}
        with self.assertRaisesRegex(ValueError,'controls/precision missing'):
            build_demand(self.request(),'population')
        indicators=pd.read_csv(self.root/'indicators.csv',encoding='cp1252',dtype=str)
        indicators['CVE_MUN']='006'; indicators.to_csv(self.root/'indicators.csv',index=False,encoding='cp1252')
        people=pd.read_csv(self.root/'persons.csv',dtype=str); people['CVEGEO']='23006'
        people.to_csv(self.root/'persons.csv',index=False)
        result=build_demand(self.request(),'points')
        self.assertEqual(result['report']['municipalities'],['23006'])
        self.assertEqual(result['report']['territory']['integer_commuters'],80)

    def test_poi_adapter_equivalence_and_multistate_identities(self):
        from sb_mexico.demand_v2.points import build_points
        from sb_mexico.demand_v2.population import allocate_population
        from sb_mexico.gravity import build_demand_grid
        req=self.request(); evidence=build_demand(req,'sources')['evidence']
        population,_=allocate_population(evidence['census'],evidence['bases'],evidence['controls'],evidence['people'])
        points,retained,report=build_points(req,evidence,population)
        grid=retained.copy(); grid['pobtot_adj']=retained.population_integer; grid['pea_real']=retained.commuters_integer
        old,audit=build_demand_grid(evidence['denue'],grid,self.config['pois'],gpd.GeoDataFrame(geometry=[],crs=4326),
                                   grid_size=.01,min_residents=0,min_jobs=0,restrict_demand_to_urban_core=False)
        self.assertEqual(audit,report['poi_audit'])
        a=next(p for p in old if p['id']=='UNI_Keep'); b=next(p for p in points if p['id']=='UNI_Keep')
        self.assertEqual({k:a[k] for k in ('id','location','jobs','residents','pea_15ymas')},
                         {k:b[k] for k in ('id','location','jobs','residents','pea_15ymas')})
        # Same person ID in another state is distinct, not a global duplicate.
        from sb_mexico.demand_v2.sources import read_mobility
        people=pd.read_csv(self.root/'persons.csv',dtype=str)
        other=people.copy(); other['CVEGEO']='05017'; other.to_csv(self.root/'other.csv',index=False)
        mobility=read_mobility([self.root/'persons.csv',self.root/'other.csv'],{'23005','05017'})
        self.assertEqual(sum(r['weight'] for r in mobility['flows']),160)


if __name__=='__main__': unittest.main()
