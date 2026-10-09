"""Long candidate previews return promptly and cannot publish stale results."""
from pathlib import Path
import http.client,json,tempfile,threading,time,unittest
from urllib.parse import urlencode
from unittest.mock import patch
import yaml
from tools import wizard


class CandidatePreviewJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        workspace=patch.multiple(wizard,ROOT_DIR=self.temp.name,CITIES_DIR=self.temp.name,
                                 DIST_DIR=str(Path(self.temp.name)/'dist'))
        workspace.start();self.addCleanup(workspace.stop)
        self.config=Path(self.temp.name)/'preview.yaml'
        self.config.write_text(yaml.safe_dump(dict(city=dict(code='TST',name='Test',bbox=[0,0,1,1]),
                                                 demand=dict(engine='legacy'))),encoding='utf-8')
        self.release=threading.Event();self.entered=threading.Event()
        self.addCleanup(self.release.set)

    def test_http_pending_duplicate_click_poll_and_stale_config(self):
        calls=[]
        def calculate(*args,**kwargs):
            calls.append(args);self.entered.set();self.release.wait(10)
            return dict(engine='v2',identity='fixture',points=[],report={})
        with patch('sb_mexico.demand_v2.integration.preview_candidate',side_effect=calculate):
            server=wizard.WizardHTTPServer(('127.0.0.1',0),wizard.WizardRequestHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            url='/api/demand-v2-preview?'+urlencode(dict(file=str(self.config),stage='allocation',async_='1')).replace('async_=','async=')
            def get(path):
                client.request('GET',path);r=client.getresponse();return r.status,json.loads(r.read())
            try:
                code,pending=get(url);self.assertEqual(code,200);self.assertEqual(pending['status'],'running')
                self.assertTrue(self.entered.wait(2))
                code,again=get(url);self.assertEqual(again['job_id'],pending['job_id']);self.assertEqual(len(calls),1)
                self.release.set()
                for _ in range(100):
                    code,done=get(url+'&job='+pending['job_id'])
                    if done['status']!='running':break
                    time.sleep(.01)
                self.assertEqual(done['status'],'complete');self.assertEqual(done['identity'],'fixture')
                self.assertEqual(yaml.safe_load(self.config.read_text())['demand']['engine'],'legacy')
                self.config.write_text('city: {name: Changed, code: TST}\n',encoding='utf-8')
                code,stale=get(url+'&job='+pending['job_id'])
                self.assertEqual(code,400);self.assertIn('cambió',stale['error'])
            finally:
                self.release.set();client.close();server.shutdown();server.server_close();thread.join()

    def test_worker_errors_are_reported(self):
        def calculate():raise ValueError('Incompatible workplace support')
        pending=wizard.candidate_preview_job(str(self.config),'allocation',calculate)
        for _ in range(100):
            data=wizard.candidate_preview_job(str(self.config),'allocation',calculate,pending['job_id'])
            if data['status']!='running':break
            time.sleep(.01)
        self.assertEqual(data['status'],'error');self.assertIn('Incompatible',data['error'])

    def test_change_during_calculation_cannot_be_published(self):
        def calculate():
            self.entered.set();self.release.wait(10);return {'identity':'old'}
        job=wizard.candidate_preview_job(str(self.config),'allocation',calculate)
        self.assertTrue(self.entered.wait(2))
        self.config.write_text('city: {name: Changed}\n',encoding='utf-8');self.release.set()
        for _ in range(100):
            with wizard.candidate_preview_lock:status=wizard.candidate_preview_jobs[job['job_id']]['status']
            if status!='running':break
            time.sleep(.01)
        self.assertEqual(status,'error')
        with self.assertRaisesRegex(ValueError,'cambió'):
            wizard.candidate_preview_job(str(self.config),'allocation',calculate,job['job_id'])
