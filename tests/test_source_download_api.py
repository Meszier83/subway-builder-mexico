"""Wizard HTTP acquisition contract without any public downloads."""
import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch
from tools import wizard


class DownloadAPITests(unittest.TestCase):
    def setUp(self):
        self.manager=Mock()
        self.manager.prepare_async.return_value={'id':'preparation','status':'running'}
        self.manager.preparation_status.return_value={'id':'preparation','status':'complete','plan':{'id':'plan','file':'city.yaml'}}
        self.manager.start.return_value={'id':'job','status':'running'}
        self.manager.status.return_value={'id':'job','status':'complete','results':[]}
        self.patcher=patch.object(wizard,'get_source_download_manager',return_value=self.manager)
        self.patcher.start()
        self.server=wizard.WizardHTTPServer(('127.0.0.1',0),wizard.WizardRequestHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)

    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join();self.patcher.stop()

    def request(self,method,url,body=None):
        self.client.request(method,url,body=json.dumps(body) if body is not None else None,
                            headers={'Content-Type':'application/json'})
        response=self.client.getresponse();return response.status,json.loads(response.read())

    def test_plan_start_and_status_use_the_saved_plan(self):
        status,preparation=self.request('POST','/api/sources/plan',{'file':'city.yaml'})
        self.assertEqual(status,200);self.manager.prepare_async.assert_called_once_with('city.yaml')
        status,result=self.request('GET','/api/sources/plan-job?id='+preparation['id'])
        self.assertEqual(status,200);plan=result['plan']
        status,job=self.request('POST','/api/sources/start',dict(plan_id=plan['id'],kinds=['cpv','enoe'],enoe_state='05',enoe_year=2026,enoe_quarter=2))
        self.assertEqual(status,200)
        self.manager.start.assert_called_once_with('plan',['cpv','enoe'],'05',2026,2,False)
        status,result=self.request('GET','/api/sources/job?id='+job['id'])
        self.assertEqual(status,200);self.assertEqual(result['status'],'complete')

    def test_invalid_and_stale_requests_report_errors(self):
        self.manager.start.side_effect=ValueError('El proyecto cambió')
        status,result=self.request('POST','/api/sources/start',dict(plan_id='old',kinds=['cpv']))
        self.assertEqual(status,400)
        self.assertIn('cambió',result.get('error',result.get('message','')))
        status,result=self.request('POST','/api/sources/plan',{})
        self.assertEqual(status,400)


if __name__=='__main__':unittest.main()
