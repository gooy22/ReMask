"""Installed reference and literal-only refresh regressions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.targeting_reference import literal, module_table, compile_tables, refresh_reference, MODULES
from app.private_contract_discovery import _LANGUAGE
from tree_sitter import Parser

ROOT=Path(__file__).resolve().parents[1]

class TargetingReferenceTests(unittest.TestCase):
    def test_literal_parser_rejects_code_spreads_and_decodes_vendor_hex_strings(self):
        def parse(text):
            node=Parser(_LANGUAGE).parse(('('+text+')').encode()).root_node.named_children[0].named_children[0].named_children[0]
            return literal(node)
        self.assertEqual(parse('{name:"Franche-Comt\\xE9",ids:[17,52]}'),{'name':'Franche-Comté','ids':[17,52]})
        for text in ('{name:fetch("https://invalid.test")}','{...source}','{name:"one",name:"two"}'):
            with self.assertRaises(ValueError):parse(text)

    def test_known_reference_ids_and_provenance(self):
        data=json.loads((ROOT/'app/contracts/meta_targeting_reference_20261014.json').read_text())
        self.assertEqual(len(data['locales']),72)
        self.assertEqual(len(data['locations']),406)
        self.assertEqual(next(r for r in data['locales'] if r['id']=='17')['language_code'],'ru_RU')
        self.assertEqual(next(r for r in data['locales'] if r['id']=='52')['language_code'],'uk_UA')
        self.assertTrue(any(r['key']=='UA' and r['type']=='country' for r in data['locations']))
        self.assertFalse(any(r['country_code']=='AU' and r['type']=='region' for r in data['locations']))
        self.assertEqual({r['module'] for r in data['source_evidence']},set(MODULES))
        self.assertFalse(data['checked_live'])

    def test_partial_or_conflicting_tables_never_replace_installed_reference(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            refresh_reference([])
            self.assertFalse((Path(folder)/'private-targeting-reference.json').exists())
            with self.assertRaises(ValueError):
                refresh_reference([{'name':MODULES[0],'source':'function(){return {language_codes_to_locales:{}}}',
                                    '_targeting_evidence':{'module':MODULES[0]}}])
            self.assertFalse((Path(folder)/'private-targeting-reference.json').exists())

    def test_language_only_refresh_retains_geography_and_preserves_real_source_names(self):
        table={'language_code_to_language_name':{'aa_AA':'Fixture language'},
               'language_codes_to_locales':{'aa_AA':list(range(1000,1040))},
               'locales_to_language_codes':{str(i):'aa_AA' for i in range(1000,1040)}}
        source='function(){return '+json.dumps(table)+';}'
        row={'name':MODULES[0],'source':source,'_targeting_evidence':{'module':MODULES[0],
             'module_sha256':'a'*64,'script_sha256':'b'*64,'observed_public_url':'https://static.xx.fbcdn.net/fixture.js'}}
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            refresh_reference([row])
            result=json.loads((Path(folder)/'private-targeting-reference.json').read_text())
            self.assertEqual(len(result['locales']),40)
            self.assertEqual(result['locales'][0]['id'],'1000')
            self.assertEqual(len(result['locations']),406)
            self.assertFalse(result['checked_live'])
            broken={**table,'locales_to_language_codes':{'1000':'different'}}
            with self.assertRaises(ValueError):
                refresh_reference([{**row,'source':'function(){return '+json.dumps(broken)+';}'}])
            self.assertEqual(json.loads((Path(folder)/'private-targeting-reference.json').read_text()),result)
