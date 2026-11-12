"""Run ownership and labels-only reuse must follow geometry identity."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sb_mexico.building_runs import select_run, record_run, exclusive_city_run


class BuildingRunTests(unittest.TestCase):
    def test_labels_reuse_only_matching_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            settings=dict(bbox=[-100,18,-98,20],lod='none')
            first,key=select_run(base,False,settings)
            Path(first).mkdir(parents=True)
            record_run(base,first,key)
            self.assertEqual(select_run(base,True,settings),(first,key))
            changed=dict(settings,lod='all')
            self.assertNotEqual(select_run(base,True,changed)[0],first)
            self.assertNotEqual(select_run(base,False,settings)[0],first)
            with self.assertRaisesRegex(ValueError,'outside'):
                record_run(base,base.parent,key)

    def test_core_content_changes_invalidate_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);core=base/'core.json';core.write_text('first')
            settings=dict(urban_core_geojson=str(core))
            run,key=select_run(base,False,settings)
            record_run(base,run,key)
            core.write_text('changed')
            self.assertNotEqual(select_run(base,True,settings)[0],run)

    @unittest.skipIf(os.name=='nt','native Linux/WSL lock')
    def test_run_lock_blocks_concurrency_and_releases_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pathlib.Path.home',return_value=Path(tmp)), patch.dict(os.environ,SB_BUILDINGS_BACKEND='bounded'):
            @exclusive_city_run
            def run(city_code):
                self.assertTrue((Path(tmp)/'build_fix/.cartography.lock').exists())
                with self.assertRaisesRegex(RuntimeError,'writer lock'): run(city_code)
                raise ValueError('controlled failure')
            with self.assertRaisesRegex(ValueError,'controlled failure'): run('FIX')
            self.assertFalse((Path(tmp)/'build_fix/.cartography.lock').exists())


if __name__=='__main__': unittest.main()
