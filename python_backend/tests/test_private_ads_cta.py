import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_creative import resolved_creative
from app.private_ads_cta import MANIFEST, capabilities, validate_cta
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture


class CtaPolicyTests(unittest.TestCase):
    def test_policy_exposes_an_implemented_subset_without_claiming_live_meta_eligibility(self):
        policy=json.loads(MANIFEST.read_text());result=capabilities()
        self.assertEqual(result['cta_types'],policy['web_actions'])
        self.assertFalse(result['cta_configuration']['checked_live'])
        self.assertFalse(result['cta_configuration']['account_eligibility_verified'])
        from app.contract_maintenance.module_registry import MANIFEST as catalog
        inventory=json.loads(catalog.read_text())['modules']
        for field in ('enum_evidence','value_evidence'):
            row=policy[field]
            self.assertEqual(inventory[row['module']]['sha256'],row['module_sha256'])
            self.assertEqual(inventory[row['module']]['script_sha256'],row['script_sha256'])

    def test_all_supported_actions_preserve_image_video_and_carousel_wire_values(self):
        for action in capabilities()['cta_types']:
            creative={'format':'SINGLE','name':'Creative','message':'Message','link':'https://example.com/',
                'call_to_action':action}
            validate_cta({'adset':{'destination_type':'WEBSITE'},'creative':creative})
            image=resolved_creative(creative,'555555',[{'image_hash':'fixture-image-hash'}],None)
            video=resolved_creative(creative,'555555',[{'video_id':'999992','thumbnail_uri':'https://example.invalid/thumbnail.jpg'}],None)
            self.assertEqual(image['object_story_spec']['link_data']['call_to_action']['type'],action)
            self.assertEqual(video['object_story_spec']['video_data']['call_to_action'],
                {'type':action,'value':{'link':'https://example.com/'}})
            carousel={**creative,'format':'CAROUSEL','carousel_cards':[
                {'headline':'First','link':'https://example.com/one','call_to_action':action},
                {'headline':'Second','link':'https://example.com/two','call_to_action':'SHOP_NOW'}]}
            validate_cta({'adset':{'destination_type':'WEBSITE'},'creative':carousel})
            result=resolved_creative(carousel,'555555',[{'image_hash':'first-image-hash'},{'image_hash':'second-image-hash'}],None)
            cards=result['object_story_spec']['link_data']['child_attachments']
            self.assertEqual([c['call_to_action']['type'] for c in cards],[action,'SHOP_NOW'])
            self.assertEqual([c['link'] for c in cards],['https://example.com/one','https://example.com/two'])


class CtaNativeLaunchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.LaunchTests.asyncSetUp
    launch=fixture.LaunchTests.launch

    async def test_arbitrary_uppercase_or_phone_cta_cannot_reach_media_or_meta(self):
        base=copy.deepcopy(self.payload)
        with patch('app.private_launch_media.LaunchMediaResolver.resolve',new=AsyncMock()) as media:
            for action in ('SOME_UPPERCASE_WORD','CALL_NOW','INSTALL_APP',''):
                self.payload=copy.deepcopy(base);self.payload['launch']['creative']['call_to_action']=action
                with self.subTest(action=action),self.assertRaises(ProvisioningError) as caught:
                    await self.launch()
                self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_CREATIVE_CTA_UNAVAILABLE')
            media.assert_not_awaited()
        self.session.facebook_web.assert_not_awaited();self.assertEqual(self.client.posts,[])

    async def test_supported_action_survives_native_draft_publication_and_independent_read(self):
        self.payload['launch']['creative']['call_to_action']='GET_OFFER'
        result=await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.expected_creative['object_story_spec']['link_data']['call_to_action']['type'],'GET_OFFER')
        await self.launch();self.assertEqual(len(self.client.posts),5)

    async def test_carousel_override_or_existing_post_cta_is_not_silently_ignored(self):
        launch=copy.deepcopy(self.payload['launch'])
        launch['creative'].update(format='CAROUSEL',carousel_cards=[{'call_to_action':'LEARN_MORE'},{'call_to_action':'CALL_NOW'}])
        with self.assertRaises(ProvisioningError):validate_cta(launch)
        launch['creative']={'format':'INSTAGRAM_POST','call_to_action':'SHOP_NOW'}
        with self.assertRaises(ProvisioningError):validate_cta(launch)
        launch['creative'].pop('call_to_action');validate_cta(launch)
