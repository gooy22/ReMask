import copy
import json
import unittest
from pathlib import Path

from app.private_ads_catalog import AdsManagerRouteCatalog
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as launch_fixture


class PrivateSchemaTests(unittest.TestCase):
    def test_form_fields_and_enums_have_private_source_provenance(self):
        catalog = AdsManagerRouteCatalog()
        form = catalog.data['form_schema']
        self.assertEqual(form['source'], 'reviewed_private_ads_manager_modules')
        self.assertFalse(form['checked_live'])
        self.assertFalse(form['browser_started'])
        self.assertEqual(set(form['schema']), {'campaign','adset','targeting','creative','ad'})
        for group, row in form['schema'].items():
            self.assertLessEqual(set(row['fields']), set(catalog.data['object_fields'][group]))
            self.assertLessEqual(set(row['enums']), set(row['fields']))
        self.assertIn('OUTCOME_TRAFFIC', form['schema']['campaign']['enums']['objective'])
        self.assertIn('facebook_reels', form['placement_options']['facebook_positions'])
        self.assertTrue(all(len(source['module_sha256']) == 64 for source in form['source_evidence']))
        self.assertNotIn('facebook-python-business-sdk', json.dumps(form))
        self.assertNotIn('object_story_spec', form['schema']['creative']['fields'])

    def test_final_ui_installer_routes_every_metadata_endpoint_to_private_sources(self):
        root = Path(__file__).resolve().parents[2]
        overlay = (root/'railway-private-completion-overlay.php').read_text()
        self.assertIn('ajax/metaPrivateSchema.php', overlay)
        self.assertIn('RemaskPrivateFormSchema::sanitizeBuilder', overlay)
        self.assertIn('metaAudienceEstimate.php', overlay)
        self.assertIn('metaPlacementCapabilities.php', overlay)
        self.assertIn('metaCreativePreview.php', overlay)
        self.assertIn('await remaskPrivateCaptureBundle()', overlay)


# Use the established full private publisher fixture for the added behavior;
# inherited cases remain owned by their existing module.
class CreativeOptionsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = launch_fixture.LaunchTests.asyncSetUp
    launch = launch_fixture.LaunchTests.launch

    async def test_advanced_creative_values_survive_publication_and_exact_readback(self):
        options = {'degrees_of_freedom_spec': {'creative_features_spec': {'standard_enhancements': {'enroll_status':'OPT_OUT'}}}}
        self.payload['launch']['creative']['official_params'] = copy.deepcopy(options)
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.expected_creative['degrees_of_freedom_spec'], options['degrees_of_freedom_spec'])
        await self.launch()
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(len(self.client.posts), 5)

    async def test_managed_identity_and_media_cannot_be_replaced_by_advanced_values(self):
        for field in ('id','account_id','object_story_spec','source_instagram_media_id','image_hash','name'):
            self.payload['launch']['creative']['official_params'] = {field:'foreign'}
            with self.subTest(field=field), self.assertRaises(ProvisioningError):
                await self.launch()
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_advanced_wrong_type_or_nested_auth_rejects_before_upload(self):
        for value in ([], ['untyped'], {'asset_feed_spec': {'access_token':'synthetic-forbidden'}}):
            self.payload['launch']['creative']['official_params'] = value
            with self.subTest(value=value), self.assertRaises(ProvisioningError):
                await self.launch()
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_image_instagram_actor_requires_account_membership_and_is_read_back(self):
        from unittest.mock import AsyncMock, patch
        self.payload['launch']['creative']['instagram_actor_id'] = '999991'
        proof = {'data':[{'id':'999991'}], 'account_scope_verified':True,'checked_live':True}
        with patch.object(self.owner.reads, 'read', new=AsyncMock(return_value=proof)) as read:
            result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(read.await_args.args[1], 'instagram_accounts')
        self.assertEqual(self.client.expected_creative['object_story_spec']['instagram_actor_id'], '999991')
