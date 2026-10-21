"""Retained source is not a fresh observation; no network or Meta mutations."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.contract_maintenance import module_refresh as refresh
from app.contract_maintenance.module_registry import refresh_registry
from app.contract_maintenance.source_observation import epoch_observation, proof_until
from app.editor_modules import refresh_editor
from app.private_ads_catalog import AdsManagerRouteCatalog
from app.targeting_reference import MODULES, refresh_reference
from tests.test_editor_modules import rows as editor_rows
from tests import test_module_refresh as fixtures


class SourceFreshnessTests(unittest.TestCase):
    def selected(self):
        fixture = fixtures.SelectionTests()
        fixture.setUp()
        return fixture, fixture.updated()

    def test_later_sender_does_not_renew_older_artifact_proof(self):
        fixture, rows = self.selected()
        spec = fixture.refs[fixture.name]
        for row in rows:
            row['_source_observed_at'] = 1000 if row['name'] in spec['artifact_modules'] else 1061
        result = refresh.select_updates(rows, fixture.refs, now=1061)
        self.assertEqual(result['accepted'][fixture.name]['until'], 1000 + refresh.TTL)
        # Re-selecting exactly the same retained evidence later cannot slide
        # its deadline forwards. Expired updated IDs remain fail-closed history.
        later = refresh.select_updates(rows, fixture.refs, now=3000)
        self.assertEqual(later['accepted'][fixture.name]['until'], 1000 + refresh.TTL)
        self.assertLess(later['accepted'][fixture.name]['until'], 3000)

    def test_only_a_new_independent_source_observation_can_renew_same_code(self):
        fixture, rows = self.selected()
        for row in rows:
            row['_source_observed_at'] = 1000
        newer = copy.deepcopy(rows)
        for row in newer:
            row['_source_observed_at'] = 1100
        for evidence in (rows + newer, newer + rows):
            result = refresh.select_updates(evidence, fixture.refs, now=1100)
            self.assertEqual(result['accepted'][fixture.name]['until'],1100 + refresh.TTL)

    def test_future_epoch_or_invalid_clock_cannot_authorize_a_monotonic_proof(self):
        for observed in (1_800_000_000, float('nan'), float('inf'), True, '1000', -1):
            with self.subTest(observed=observed):
                self.assertEqual(proof_until({'_source_observed_at':observed},1000,refresh.TTL),1000)
        self.assertEqual(proof_until({'_source_observed_at':900},1000,refresh.TTL),900+refresh.TTL)

    def test_metadata_epoch_is_not_replaced_by_current_refresh_time(self):
        old, now = 1_700_000_000, 1_800_000_000
        rows = editor_rows()
        table={'language_code_to_language_name':{'aa_AA':'Fixture language'},
            'language_codes_to_locales':{'aa_AA':list(range(1000,1040))},
            'locales_to_language_codes':{str(i):'aa_AA' for i in range(1000,1040)}}
        language={'name':MODULES[0],'source':'function(){return '+json.dumps(table)+';}',
            '_targeting_evidence':{'module':MODULES[0],'module_sha256':'a'*64,
                'script_sha256':'b'*64,'observed_public_url':'https://static.xx.fbcdn.net/fixture.js'}}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'REMASK_DATA_DIR':folder}), \
                patch('time.time',return_value=now):
            for _ in range(2):
                refresh_editor(rows,AdsManagerRouteCatalog().data['object_fields'],observed_at=old)
                refresh_registry(rows,observed_at=old)
                refresh_reference([language],observed_at=old)
            for name in ('private-editor-modules.json','private-module-catalog.json','private-targeting-reference.json'):
                self.assertEqual(json.loads((Path(folder)/name).read_text())['refreshed_at'],old)

    def test_invalid_or_future_metadata_epoch_is_rejected_before_persistence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'REMASK_DATA_DIR':folder}), \
                patch('time.time',return_value=1_800_000_000):
            for observed in (1_800_000_001,True,'1000',float('nan'),-1):
                with self.subTest(observed=observed), self.assertRaises(ValueError):
                    epoch_observation(observed)
            with self.assertRaises(ValueError):refresh_editor(editor_rows(),{},observed_at=1_800_000_001)
            with self.assertRaises(ValueError):refresh_registry(editor_rows(),observed_at=1_800_000_001)
            with self.assertRaises(ValueError):refresh_reference([],observed_at=1_800_000_001)
            self.assertEqual(list(Path(folder).iterdir()),[])


if __name__ == '__main__':
    unittest.main()
