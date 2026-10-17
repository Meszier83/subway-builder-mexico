"""Exercise the actual Wizard HTTP routes, jobs, raw imports and YAML delivery."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml
from tools import wizard
from sb_mexico.toponymy_official import MAX_BYTES


class TestOfficialToponymyAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root/'test.yaml'
        self.data = dict(city=dict(code='TST',name='Test',bbox=[-101,25,-100,26]),
                         data_dir=str(self.root/'data'),places=[],
                         toponymy=dict(engine='official_v1',osm_fallback=False))
        self.config.write_text(yaml.safe_dump(self.data),encoding='utf-8')
        for name, value in (('ROOT_DIR',str(self.root)),('CITIES_DIR',str(self.root)),('DATA_DIR',str(self.root/'data'))):
            p = patch.object(wizard,name,value);p.start();self.addCleanup(p.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),wizard.WizardRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.client = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)

    def stop(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,method,path,body=None,headers=None):
        self.client.request(method,path,body=body,headers=headers or {})
        response=self.client.getresponse()
        return response.status,json.loads(response.read())

    def job(self,path):
        status,data=self.request('GET',path)
        self.assertEqual(status,200)
        for _ in range(100):
            if data['status']!='running': break
            time.sleep(.01)
            _,data=self.request('GET',path+'&job='+data['job_id'])
        self.assertEqual(data['status'],'complete',data)
        return data

    def test_import_scan_and_save_roundtrip_actual_routes(self):
        content=json.dumps(dict(datos=[dict(cvegeo='190390001',nomgeo='Peña',longitud='-100.5',latitud='25.5',
                                           pob_total='5000',ambito='URBANO',estatus=1)])).encode()
        status,data=self.request('POST','/api/toponymy/import?file=test.yaml&kind=localities&name=localities.json',content)
        self.assertEqual(status,200);self.assertEqual(data['labels'],1)
        result=self.job('/api/toponymy/scan?file=test.yaml')['catalog']
        self.assertEqual(result['engine'],'official_v1')
        self.assertEqual(result['places'][0]['name'],'Peña')
        self.assertEqual(result['places'][0]['type'],'town')
        self.data['places']=result['places']
        wizard.save_full_city_data(str(self.config),self.data)
        loaded=yaml.safe_load(self.config.read_text(encoding='utf-8'))
        self.assertEqual(loaded['toponymy'],self.data['toponymy'])
        self.assertEqual(loaded['places'],result['places'])

    def test_acquisition_route_uses_requested_source_and_job(self):
        with patch('sb_mexico.toponymy_official.acquire_dcah',return_value=dict(files=['dcah'],errors=[],partial=False)) as acquire:
            result=self.job('/api/toponymy/acquire?file=test.yaml&source=dcah')
            self.assertEqual(result['files'],['dcah'])
            self.assertEqual(acquire.call_args.args[0],str(self.config))

    def test_import_rejects_invalid_kind_and_oversized_request(self):
        status,_=self.request('POST','/api/toponymy/import?file=test.yaml&kind=businesses&name=bad.json',b'{}')
        self.assertEqual(status,400)
        status,_=self.request('POST','/api/toponymy/import?file=test.yaml&kind=dcah&name=bad.zip',
                              headers={'Content-Length':str(MAX_BYTES+1)})
        self.assertEqual(status,400)

    def test_changed_project_cannot_consume_old_scan_job(self):
        result=self.job('/api/toponymy/scan?file=test.yaml')
        self.data['city']['bbox']=[-102,25,-100,26]
        self.config.write_text(yaml.safe_dump(self.data),encoding='utf-8')
        _,data=self.request('GET','/api/toponymy/scan?file=test.yaml&job='+result['job_id'])
        self.assertEqual(data['status'],'error')
        self.assertIn('cambió',data['message'])


if __name__=='__main__': unittest.main()
