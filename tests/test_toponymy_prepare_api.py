import copy
import json
from pathlib import Path
from unittest.mock import patch
import yaml

try:
    from . import test_toponymy_official_api as fixture
except ImportError:
    import test_toponymy_official_api as fixture
from sb_mexico.toponymy_layout import plan_labels
from tools import wizard


class PreparedToponymyAPI(fixture.TestOfficialToponymyAPI):
    def prepared(self):
        places=[dict(id='official',name='Ciudad',loc=[-100.5,25.5],type='city',feature_kind='locality',
                     population=200000,is_urban=True,official_code='190390001')]
        pipeline=dict(engine='official_v1',osm_fallback=False,
                      layout=dict(engine='label_v1',profile='balanced',periphery='core_only'))
        return dict(places=places,toponymy=pipeline,toponymy_mode='replace',deleted_places=[],
                    layout=plan_labels(places,self.data['city']['bbox'],pipeline=pipeline),
                    summary=dict(total=1,eligible=1,hidden=0,deferred=0,added=1,preserved=0,exceptions=0),
                    exceptions=[],warnings=[])

    def prepare(self):
        with patch('sb_mexico.toponymy_prepare.prepare_labels',return_value=self.prepared()):
            return self.job('/api/toponymy/prepare?file=test.yaml')

    def apply(self,job_id):
        return self.request('POST','/api/toponymy/apply-prepared',json.dumps(dict(file='test.yaml',job_id=job_id)))

    def test_prepare_compute_does_not_edit_and_commit_changes_only_toponymy(self):
        self.data['custom_field']=dict(keep='exactly')
        self.data['demand']=dict(engine='legacy',extra=52)
        self.config.write_text(yaml.safe_dump(self.data),encoding='utf-8')
        before=self.config.read_bytes()
        job=self.prepare()
        self.assertEqual(before,self.config.read_bytes())
        status,result=self.apply(job['job_id'])
        self.assertEqual(status,200,result)
        saved=yaml.safe_load(self.config.read_text(encoding='utf-8'))
        self.assertEqual(saved['custom_field'],self.data['custom_field'])
        self.assertEqual(saved['demand'],self.data['demand'])
        self.assertEqual(saved['places'][0]['id'],'official')
        backup=json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(backup['fields']['places'],[])
        self.assertNotIn('demand',backup['fields'])
        # A later unrelated edit must survive restoring the label backup.
        saved['demand']['extra']=99
        self.config.write_text(yaml.safe_dump(saved),encoding='utf-8')
        status,result=self.request('POST','/api/toponymy/restore',json.dumps(dict(file='test.yaml')))
        self.assertEqual(status,200,result)
        restored=yaml.safe_load(self.config.read_text(encoding='utf-8'))
        self.assertEqual(restored['places'],[])
        self.assertEqual(restored['toponymy'],self.data['toponymy'])
        self.assertEqual(restored['demand']['extra'],99)

    def test_edited_yaml_cannot_apply_old_job(self):
        job=self.prepare()
        self.data['places']=[dict(id='manual',name='Nuevo',loc=[-100.5,25.5],type='suburb')]
        self.config.write_text(yaml.safe_dump(self.data),encoding='utf-8')
        before=self.config.read_bytes()
        status,_=self.apply(job['job_id'])
        self.assertEqual(status,409)
        self.assertEqual(before,self.config.read_bytes())

    def test_wrong_city_job_and_unknown_job_rejected(self):
        job=self.prepare()
        (self.root/'other.yaml').write_text(yaml.safe_dump(self.data),encoding='utf-8')
        status,_=self.request('POST','/api/toponymy/apply-prepared',json.dumps(dict(file='other.yaml',job_id=job['job_id'])))
        self.assertEqual(status,409)
        self.assertEqual(self.apply('missing')[0],409)

    def test_failed_atomic_replace_preserves_yaml_and_backup_is_recoverable(self):
        job=self.prepare();before=self.config.read_bytes()
        original_replace=wizard.os.replace
        def replace(source,target):
            if Path(target)==self.config: raise OSError('disk unavailable')
            return original_replace(source,target)
        with patch('tools.wizard.os.replace',side_effect=replace):
            # Direct call avoids patching the source acquisition cache during HTTP.
            with self.assertRaises(OSError): wizard.apply_prepared_toponymy(str(self.config),job['job_id'])
        self.assertEqual(before,self.config.read_bytes())
        self.assertTrue(list((self.root/'data/.toponymy/backups').glob('*.json')))
        self.assertEqual(self.apply(job['job_id'])[0],200)

    def test_layout_http_matches_shared_python_and_honors_overrides(self):
        prepared=self.prepared()
        config=dict(city=self.data['city'],places=prepared['places'],toponymy=prepared['toponymy'])
        config['places'][0]['label_overrides']=dict(scale='small')
        status,result=self.request('POST','/api/toponymy/layout',json.dumps(dict(config=config)))
        self.assertEqual(status,200,result)
        self.assertEqual(result['layout'],plan_labels(config['places'],config['city']['bbox'],pipeline=config['toponymy']))
        self.assertEqual(config['places'][0]['type'],'city')

    def test_get_layout_after_persisted_preparation(self):
        job=self.prepare();self.assertEqual(self.apply(job['job_id'])[0],200)
        status,result=self.request('GET','/api/toponymy/layout?file=test.yaml')
        self.assertEqual(status,200)
        self.assertEqual(result['layout']['decisions']['official']['min_zoom'],6)
