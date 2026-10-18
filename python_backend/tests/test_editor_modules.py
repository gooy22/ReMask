"""Source-backed field adapters and full inventory; no Facebook requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.editor_modules import compile_editor,form_schema,installed_editor,refresh_editor,editor_snapshot
from app.contract_maintenance.ads_sources import module_records
from app.contract_maintenance.module_registry import build_registry,refresh_registry
from app.private_ads_catalog import AdsManagerRouteCatalog

ROOT=Path(__file__).resolve().parents[1]


def rows():
    source=(ROOT/'tests/fixtures/meta_editor_modules_observed_20261014.js').read_text()
    out=list(module_records(source,all_modules=True))
    for row in out:
        row['_module_evidence']={'module':row['name'],'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
            'script_sha256':'a'*64,'observed_public_url':'https://static.xx.fbcdn.net/fixture.js'}
    return out


class EditorModulesTests(unittest.TestCase):
    def setUp(self):
        self.rows=rows();self.fields=AdsManagerRouteCatalog().data['object_fields']

    def test_all_editor_bindings_reproduce_observed_literals_without_mock_ids(self):
        result=compile_editor(self.rows,self.fields);installed=installed_editor()
        for key in ('schema','field_controls','references','targeting_types','object_definitions'):
            self.assertEqual(result[key],installed[key])
        self.assertEqual(len(installed['targeting_types']),28)
        self.assertEqual(len(installed['references']['user_device']),1939)
        self.assertIn('iphone 17', {r['id'] for r in installed['references']['user_device']})
        text=json.dumps(result)
        self.assertNotIn('6003167425934',text) # MOCK_DATA is not a source catalog.
        self.assertNotIn('6006371343132',text)
        self.assertFalse(result['checked_live'])

    def test_unknown_modules_are_inventoried_without_installing_fields_or_routes(self):
        source='__d("UnknownMetaFeature",["UnknownDependency"],function(){return {NEW_FIELD:"new_field"};});'
        row=next(module_records(source,all_modules=True));row['_module_evidence']={'script_sha256':'b'*64,'observed_public_url':'https://static.xx.fbcdn.net/fixture.js'}
        registry=build_registry([row]);self.assertIn('UnknownMetaFeature',registry['modules'])
        self.assertEqual(registry['modules']['UnknownMetaFeature']['dependencies'],['UnknownDependency'])
        self.assertEqual(compile_editor([row],self.fields)['schema'],{})
        self.assertNotIn('source',registry['modules']['UnknownMetaFeature'])

    def test_partial_enum_refresh_reaches_shared_schema_and_retains_other_groups(self):
        row=copy.deepcopy(next(r for r in self.rows if r['name']=='AdsAPIOptimizationSubEvent'))
        row['source']=row['source'].replace('NONE:"NONE"','NONE:"NONE",NEW_SUB_EVENT:"NEW_SUB_EVENT"')
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            refresh_editor([row],self.fields)
            form=form_schema(AdsManagerRouteCatalog())
            self.assertIn('NEW_SUB_EVENT',form['schema']['adset']['enums']['optimization_sub_event'])
            self.assertEqual(len(form['field_controls']['targeting']['user_device']['options']),1939)
            self.assertEqual(len(form['object_definitions']),195)
            self.assertFalse(form['checked_live']);self.assertFalse(form['browser_started'])
            self.assertLess(len(json.dumps(form).encode()),1048576)
            raw=(Path(folder)/'private-editor-modules.json').read_text()
            self.assertNotIn('function(',raw);self.assertNotIn('cookies',raw)

    def test_conflicting_or_executable_tables_preserve_installed_values(self):
        row=copy.deepcopy(next(r for r in self.rows if r['name']=='AdsTargetingEducation'))
        row['source']=row['source'].replace('HIGH_SCHOOL:1','HIGH_SCHOOL:fetch("https://invalid.test")')
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            with self.assertRaises(ValueError):refresh_editor([row],self.fields)
            self.assertFalse((Path(folder)/'private-editor-modules.json').exists())
            self.assertEqual(editor_snapshot()['schema'],installed_editor()['schema'])
        good=next(r for r in self.rows if r['name']=='AdsTargetingEducation')
        self.assertNotIn('education_statuses',compile_editor([good,row],self.fields).get('schema',{}).get('targeting',{}).get('fields',{}))

    def test_inventory_refresh_retains_all_installed_modules_and_strips_code(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            refresh_registry(self.rows[:1])
            result=json.loads((Path(folder)/'private-module-catalog.json').read_text())
            installed_catalog=json.loads((ROOT/'app/contracts/meta_module_catalog_20261014.json').read_text())
            self.assertEqual(len(result['modules']),len(installed_catalog['modules']))
            self.assertFalse(result['checked_live'])
            self.assertNotIn('source',next(iter(result['modules'].values())))
            self.assertNotIn('fb_dtsg',result)


if __name__=='__main__':unittest.main()
