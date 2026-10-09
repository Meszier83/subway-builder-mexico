"""Acquisition failures, geography and publication boundaries with isolated inputs."""
import base64
import io
import json
import struct
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sb_mexico import source_downloads as d


def archive(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, content in files.items():
            z.writestr(name, content)


def feature(code, bbox, state=False):
    x0, y0, x1, y1 = bbox
    return dict(properties=dict(cve_ent=code[:2], cvegeo=code, nomgeo=code),
                geometry=dict(type='Polygon', coordinates=[[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]))


class FixtureClient:
    def __init__(self, docs=None, files=None):
        self.docs, self.files, self.calls = docs or {}, files or {}, []

    def json(self, url, payload=None, ttl=None):
        self.calls.append((url, payload))
        value = self.docs[url]
        if callable(value):
            value = value(payload)
        return value, dict(url=url, query=payload)

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.files[url], dict(url=url)


class DownloadTests(unittest.TestCase):
    def test_fine_catalog_walk_and_hierarchy_rejection(self):
        from sb_mexico.historical_transfer import SUPPORTED
        docs={d.SAIC+f'acteco/seg/{code}/3/6/':dict(success=True,list=[]) for code in SUPPORTED}
        docs[d.SAIC+'acteco/seg/72/3/6/']=dict(success=True,list=[dict(key='721111')])
        self.assertIn('721111',d.fine_ce_catalog(FixtureClient(docs)))
        docs[d.SAIC+'acteco/seg/72/3/6/']=dict(success=True,list=[dict(key='311110')])
        with self.assertRaisesRegex(ValueError,'jerarquía'):d.fine_ce_catalog(FixtureClient(docs))

    def test_candidate_download_adds_fine_files_without_changing_sector_requests(self):
        def exported(q):
            activity='Clase 721111 Hoteles' if '721111' in q['actecos'] else 'Sector 72 Servicios'
            stratum='0 a 10' if q['stratums']!=[0] else 'Total'
            raw=('"Año Censal","Entidad","Municipio","Estrato","Actividad económica","UE Unidades económicas","H001A Personal ocupado total"\n'
                 f'2023,"23 Quintana Roo","005 Benito Juárez","{stratum}","{activity}",2,8\n').encode()
            return base64.b64encode(raw).decode()
        docs={d.SAIC+'anios/seg/0/0/6/':dict(success=True,list=[dict(key='2023')]),
              d.SAIC+'acteco/seg/0/2/6/':dict(success=True,list=[dict(key='72')]),
              d.SAIC+'ageos/seg/23/1/6/':dict(success=True,list=[dict(key='23005')]),
              d.SAIC+'consulta/total/6/':dict(success=True,info=[dict(total=1)]),
              d.SAIC+'exporta/files/2/6/':exported}
        client=FixtureClient(docs)
        with patch.object(d,'fine_ce_catalog',return_value=['72','721111']):
            files=d.acquire_source(client,self.geo(),self.folder,'ce',ce_detail=True)
        self.assertEqual(len(files),4)
        queries=[q for url,q in client.calls if url.endswith('exporta/files/2/6/')]
        self.assertEqual([q['actecos'] for q in queries[:2]],[['72'],['72']])
        self.assertEqual([q['actecos'] for q in queries[2:]],[['72','721111'],['72','721111']])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'project'
        self.folder.mkdir()
        self.zip = self.root / 'source.zip'

    def tearDown(self):
        self.temp.cleanup()

    def geo(self):
        return dict(states=[dict(code='23', name='Quintana Roo')],
                    municipalities=[dict(code='23005', state='23', name='Benito Juárez')])

    def test_geography_uses_intersections_across_states_not_center(self):
        client = FixtureClient({d.GEO+'mgee/':dict(features=[feature('23',[0,0,1,2]), feature('31',[1,0,2,2]), feature('09',[5,5,6,6])]),
                               d.GEO+'mgem/23':dict(features=[feature('23005',[0,0,1,2])]),
                               d.GEO+'mgem/31':dict(features=[feature('31050',[1,0,2,2])])})
        geo = d.geography(client, [.5,.5,1.5,1.5])
        self.assertEqual([s['code'] for s in geo['states']], ['23','31'])
        self.assertEqual([m['code'] for m in geo['municipalities']], ['23005','31050'])

    def test_geography_rejects_invalid_and_foreign_bbox(self):
        for bbox in ([1,2,0,3], [float('nan'),0,1,2], [False,0,1,2], [0,0,1]):
            with self.assertRaises(ValueError): d.geography(FixtureClient(), bbox)
        with self.assertRaisesRegex(ValueError, 'México'):
            d.geography(FixtureClient({d.GEO+'mgee/':dict(features=[])}), [0,0,1,1])

    def test_marco_resolves_all_32_catalog_links(self):
        nodes=[dict(url=f'/catalog/{i:02}_official.zip') for i in range(1,33)]
        client=FixtureClient({d.MARCO_CATALOG:dict(success=True, info=dict(multiarchivos=[dict(hijos=nodes)]))})
        self.assertEqual(len(d.marco_links(client)),32)
        nodes.pop()
        with self.assertRaisesRegex(ValueError,'32'):d.marco_links(client)

    def test_zip_slip_and_symlinks_rejected_before_extraction(self):
        for name in ('../escape.csv','/absolute.csv','C:/absolute.csv'):
            archive(self.zip,{name:'bad'})
            with self.assertRaisesRegex(ValueError,'insegura'):d.extract(self.zip,self.folder,'cpv','23')
        with zipfile.ZipFile(self.zip,'w') as z:
            info=zipfile.ZipInfo('link');info.external_attr=0o120777 << 16;z.writestr(info,'target')
        with self.assertRaisesRegex(ValueError,'insegura'):d.extract(self.zip,self.folder,'cpv','23')
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_cpv_extracts_dataset_not_dictionary(self):
        header='ENTIDAD,MUN,LOC,AGEB,MZA,POBTOT,P_15YMAS,POCUPADA\n23,005,0001,0001,001,20,15,10\n'
        archive(self.zip,{'root/conjunto_de_datos/conjunto_de_datos_ageb_urbana_23_cpv2020.csv':header,
                          'root/diccionario_de_datos/diccionario.csv':'wrong'})
        files=d.extract(self.zip,self.folder,'cpv','23')
        self.assertEqual(len(files),1)
        self.assertIn('cpv2020',files[0].name)
        with self.assertRaises(ValueError):d.extract(self.zip,self.folder,'cpv','31')

    def test_denue_rejects_csv_with_missing_columns(self):
        archive(self.zip,{'conjunto_de_datos/denue_inegi_23_.csv':'latitud,longitud\n1,2\n'})
        with self.assertRaisesRegex(ValueError,'columnas'):d.extract(self.zip,self.folder,'denue','23')

    def test_enoe_chooses_only_reference_state_workbook(self):
        xls=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fixture'
        archive(self.zip,{'Ciudades/2026_trim_2_Entidad_Quintana Roo.xls':b'wrong',
                          'Entidades/2026_trim_2_Entidad_Quintana Roo.xls':xls,
                          'Entidades/2026_trim_2_Entidad_Yucatán.xls':xls})
        files=d.extract(self.zip,self.folder,'enoe',reference='Quintana Roo')
        self.assertEqual(len(files),1)
        self.assertEqual(files[0].read_bytes(),xls)
        with self.assertRaises(ValueError):d.extract(self.zip,self.folder,'enoe',reference='Jalisco')
        archive(self.zip,{'Entidades/2026_trim_2_Entidad_Ciudad_de_Mexico.xls':xls})
        self.assertEqual(len(d.extract(self.zip,self.folder,'enoe',reference='Ciudad de México')),1)

    def test_marco_preserves_components_and_rejects_missing_sidecars(self):
        header=bytearray(100);struct.pack_into('>i',header,0,9994);struct.pack_into('>i',header,24,50)
        files={f'conjunto_de_datos/23{layer}.{ext}':bytes(header) if ext=='shp' else b'fixture'
               for layer in ('m','a') for ext in ('shp','dbf','shx','prj','cpg')}
        archive(self.zip,files)
        outputs=d.extract(self.zip,self.folder,'marco','23')
        self.assertEqual(len(outputs),10)
        self.assertTrue(all(p.parent.name=='marco_2020_23' for p in outputs))
        del files['conjunto_de_datos/23a.prj'];archive(self.zip,files)
        with self.assertRaisesRegex(ValueError,'incompletos'):d.extract(self.zip,self.folder,'marco','23')

    def test_pbf_rejects_html_and_truncated_blocks(self):
        header=b'\x0a\x09OSMHeader\x18\x02'
        path=self.root/'test.pbf';path.write_bytes(struct.pack('>I',len(header))+header+b'xx')
        d.validate_pbf(path)
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):d.validate_pbf(path)
        path.write_bytes(b'<html>not found</html>')
        with self.assertRaises(ValueError):d.validate_pbf(path)

    def test_publish_does_not_overwrite_manual_or_modified_files(self):
        stage=self.root/'stage';stage.mkdir();(stage/'denue.csv').write_text('new')
        (self.folder/'denue.csv').write_text('manual')
        with self.assertRaisesRegex(ValueError,'manual'):d.publish(stage,self.folder,'denue',[])
        self.assertEqual((self.folder/'denue.csv').read_text(),'manual')
        (self.folder/'denue.csv').unlink();d.publish(stage,self.folder,'denue',[])
        (stage/'denue.csv').write_text('newer');(self.folder/'denue.csv').write_text('edited')
        with self.assertRaises(ValueError):d.publish(stage,self.folder,'denue',[])

    def test_publish_replaces_only_managed_files_and_retires_stale_coverage(self):
        stage=self.root/'stage';stage.mkdir();(stage/'SAIC_old.csv').write_text('old')
        d.publish(stage,self.folder,'ce',[])
        (self.folder/'unrelated.txt').write_text('keep')
        (stage/'SAIC_new.csv').write_text('new');d.publish(stage,self.folder,'ce',[dict(url=d.SAIC)])
        self.assertFalse((self.folder/'SAIC_old.csv').exists())
        self.assertTrue((self.folder/'unrelated.txt').exists())
        self.assertEqual(list(d.read_manifest(self.folder)['files']),['SAIC_new.csv'])

    def test_saic_preserves_suppression_and_checks_row_count(self):
        raw=('Instituto\n"Año Censal","Entidad","Municipio","Estrato","Actividad económica","UE Unidades económicas","H001A Personal ocupado total"\n'
             '2023,"23 Quintana Roo","005 Benito Juárez","0 a 10","Sector 72 Servicios",2,\n').encode('utf8')+b'\0\0'
        docs={d.SAIC+'anios/seg/0/0/6/':dict(success=True,list=[dict(key='2023')]),
              d.SAIC+'acteco/seg/0/2/6/':dict(success=True,list=[dict(key='72')]),
              d.SAIC+'ageos/seg/23/1/6/':dict(success=True,list=[dict(key='23005')]),
              d.SAIC+'consulta/total/6/':dict(success=True,info=[dict(total=1)]),
              d.SAIC+'exporta/files/2/6/':base64.b64encode(raw).decode()}
        client=FixtureClient(docs)
        outputs=d.acquire_source(client,self.geo(),self.folder,'ce')
        from sb_mexico.ce_controls import read_saic_controls
        self.assertEqual(len(outputs),2)
        self.assertIsNone(next(iter(read_saic_controls(outputs,2023).values()))['employment'])
        docs[d.SAIC+'consulta/total/6/']['info'][0]['total']=2
        with self.assertRaisesRegex(ValueError,'filas'):d.acquire_source(client,self.geo(),self.folder,'ce')
        self.assertEqual(len(d.read_manifest(self.folder)['files']),2)

    def test_saic_rejects_absent_municipality_before_export(self):
        docs={d.SAIC+'anios/seg/0/0/6/':dict(success=True,list=[dict(key='2023')]),
              d.SAIC+'acteco/seg/0/2/6/':dict(success=True,list=[dict(key='72')]),
              d.SAIC+'ageos/seg/23/1/6/':dict(success=True,list=[])}
        client=FixtureClient(docs)
        with self.assertRaisesRegex(ValueError,'municipios'):d.acquire_source(client,self.geo(),self.folder,'ce')
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_publication_guard_blocks_config_change_without_installing_files(self):
        path=self.root/'conapo.csv';path.write_text('CLAVE,ANO,POB_TOTAL\n23005,2026,100\n')
        def guard(stage):raise ValueError('changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            d.acquire_source(FixtureClient(files={d.CONAPO:path}),self.geo(),self.folder,'conapo',before_publish=guard)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_cache_detects_modified_bytes_and_http_200_html(self):
        class Response(io.BytesIO):
            url=d.CONAPO
            headers={}
        client=d.Client(self.root/'cache')
        calls=[]
        def opening(req, timeout):calls.append(req);return Response(b'CLAVE,ANO,POB_TOTAL\n')
        with patch.object(client.opener,'open',opening):
            path,record=client.fetch(d.CONAPO)
            self.assertEqual(record['sha256'],d.sha256(path))
            client.fetch(d.CONAPO);self.assertEqual(len(calls),1)
            path.write_bytes(b'corrupt');client.fetch(d.CONAPO);self.assertEqual(len(calls),2)
        with patch.object(client.opener,'open',return_value=Response(b'<!DOCTYPE html><html>404</html>')):
            client.refresh=True
            with self.assertRaisesRegex(ValueError,'HTML'):client.fetch(d.CONAPO)

    def test_official_urls_and_redirects_are_restricted(self):
        for url in ('http://www.inegi.org.mx/file','https://evil.example/file','https://user@www.inegi.org.mx/file'):
            with self.assertRaises(ValueError):d.official_url(url)
        with self.assertRaises(ValueError):d.OfficialRedirect().redirect_request(None,None,302,'',{},'https://evil.example/')

    def test_manager_requires_explicit_multi_state_enoe_and_saved_identity(self):
        config=self.root/'test.yaml';config.write_text('initial')
        manager=d.DownloadManager(self.root,self.root/'data',lambda _: {},str)
        geo=self.geo();geo['states'].append(dict(code='31',name='Yucatán'))
        plan=dict(id='plan',file=str(config),config_hash=d.sha256(config),folder=str(self.folder),
                  geography=geo,exclusions=[],created_at=time.time())
        manager.plans['plan']=plan
        with self.assertRaisesRegex(ValueError,'referencia'):manager.start('plan',['enoe'],None,2026,2)
        with self.assertRaisesRegex(ValueError,'Periodo'):manager.start('plan',['enoe'],'23',2026,5)
        config.write_text('changed')
        with self.assertRaisesRegex(ValueError,'cambió'):manager.start('plan',['cpv'],None,2026,2)

    def test_manager_reports_partial_failure_and_continues_other_sources(self):
        config=self.root/'test.yaml';config.write_text('initial')
        manager=d.DownloadManager(self.root,self.root/'data',lambda _: {},str)
        manager.plans['plan']=dict(id='plan',file=str(config),config_hash=d.sha256(config),folder=str(self.folder),
                                   geography=self.geo(),exclusions=[],created_at=time.time())
        def acquire(client,geo,folder,kind,*args):
            if kind=='cpv':raise ValueError('provider offline')
            return ['conapo.csv']
        with patch.object(d,'acquire_source',acquire):
            job=manager.start('plan',['cpv','conapo'],None,2026,2)
            for _ in range(100):
                job=manager.status(job['id'])
                if job['status']!='running':break
                time.sleep(.01)
        self.assertEqual(job['status'],'partial')
        self.assertEqual([r['status'] for r in job['results']],['error','ok'])

    def test_preparation_runs_in_background_and_can_be_polled(self):
        import threading
        release = threading.Event()
        entered = threading.Event()
        manager=d.DownloadManager(self.root,self.root/'data',lambda _: {},str)
        def prepare(file):
            entered.set()
            release.wait(2)
            return {'id':'plan','file':file}
        with patch.object(manager,'prepare',prepare):
            task=manager.prepare_async('saved.yaml')
            self.assertEqual(task['status'],'running')
            self.assertTrue(entered.wait(1))
            self.assertEqual(manager.prepare_async('saved.yaml')['id'],task['id'])
            with self.assertRaises(ValueError):manager.prepare_async('other.yaml')
            release.set()
            for _ in range(100):
                task=manager.preparation_status(task['id'])
                if task['status']!='running':break
                time.sleep(.01)
        self.assertEqual(task['status'],'complete')
        self.assertEqual(task['plan']['id'],'plan')


if __name__=='__main__':unittest.main()
