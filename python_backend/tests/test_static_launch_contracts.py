import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.private_launch import PrivateLaunchContractStore, PrivateLaunchStep, _effective_launch_payload
from app.static_launch_contracts import StaticLaunchContractStore
from app.provisioning.models import ProvisioningError
from tests.test_private_launch_service import contracts


class StaticLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'manifest.json'
        self.patch = patch('app.facebook_docids.STORE_PATH', self.root / 'docids.json')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_manifest(self, evidence=True):
        rows = {}
        for step, row in contracts().items():
            rows[step] = {'contract': row, 'evidence': {
                'artifact_sha256': 'a' * 64, 'sender_sha256': 'b' * 64,
                'read_sha256': 'c' * 64, 'observed_at': '2026-10-11'} if evidence else {}}
        self.manifest.write_text(json.dumps({'version': 1, 'revision': 'fixture', 'steps': rows}))

    def test_runtime_candidate_and_environment_cannot_replace_deployed_contracts(self):
        candidate = self.root / 'candidates.json'
        store = PrivateLaunchContractStore('{}', path=candidate)
        store.register(PrivateLaunchStep.CAMPAIGN, contracts()['CAMPAIGN'])
        with patch.dict(os.environ, {'REMASK_PRIVATE_LAUNCH_CONTRACTS_PATH': str(candidate),
                                    'REMASK_PRIVATE_LAUNCH_CONTRACTS_JSON': json.dumps(contracts())}):
            pinned = StaticLaunchContractStore()
        self.assertFalse(any(row['configured'] for row in pinned.status().values()))
        with self.assertRaises(ProvisioningError) as caught:
            pinned.register(PrivateLaunchStep.CAMPAIGN, contracts()['CAMPAIGN'])
        self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_STATIC_CONTRACT_REQUIRED')
        self.assertFalse(any(row['configured'] for row in pinned.status().values()))

    def test_verified_manifest_loads_exact_ids_and_independent_reads(self):
        self.write_manifest()
        pinned = StaticLaunchContractStore(self.manifest)
        self.assertTrue(all(row['active'] for row in pinned.status().values()))
        self.assertEqual(pinned.get(PrivateLaunchStep.AD).doc_id, '444444')
        self.assertEqual(pinned.status()['AD']['revision'], 'fixture')

    def test_missing_source_evidence_is_rejected(self):
        self.write_manifest(evidence=False)
        with self.assertRaises(ProvisioningError) as caught:
            StaticLaunchContractStore(self.manifest)
        self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_STATIC_MANIFEST_INVALID')

    def test_local_media_cannot_be_mistaken_for_published_meta_media(self):
        for media in ({'staged_media': [{'id': 'm-local', 'sha256': 'a'*64}]},
                      {'media_library_id': 'm-local'}, {'carousel_media_library_ids': ['m-local']}):
            launch = {section: {'name': 'fixture'} for section in ('campaign', 'adset', 'creative', 'ad')}
            launch['creative'].update({'format': 'SINGLE', **media})
            with self.assertRaises(ProvisioningError) as caught:
                _effective_launch_payload(launch)
            self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_MEDIA_CONTRACT_REQUIRED')

    def test_omitting_format_cannot_bypass_local_media_or_carousel_guard(self):
        for fields, code in (({'staged_media': [{'id': 'm-local'}]}, 'PRIVATE_LAUNCH_MEDIA_CONTRACT_REQUIRED'),
                             ({'media_library_id': 'm-local'}, 'PRIVATE_LAUNCH_MEDIA_CONTRACT_REQUIRED'),
                             ({'carousel_media_library_ids': ['m-local']}, 'PRIVATE_LAUNCH_MEDIA_CONTRACT_REQUIRED'),
                             ({'carousel_cards': [{'link': 'https://example.test/'}]}, 'PRIVATE_LAUNCH_CAROUSEL_CONTRACT_REQUIRED')):
            with self.subTest(fields=fields):
                launch = {section: {'name': 'fixture'} for section in ('campaign', 'adset', 'creative', 'ad')}
                launch['creative'].update(fields)
                with self.assertRaises(ProvisioningError) as caught:
                    _effective_launch_payload(launch)
                self.assertEqual(caught.exception.code, code)

    def test_non_scalar_creative_format_is_a_validation_error(self):
        for value in ({}, [], True):
            launch = {section: {'name': 'fixture'} for section in ('campaign', 'adset', 'creative', 'ad')}
            launch['creative']['format'] = value
            with self.assertRaises(ProvisioningError) as caught:
                _effective_launch_payload(launch)
            self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_CREATIVE_FORMAT_INVALID')
