import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore
from app.private_launch_media import LocalMedia
from app.provisioning.models import ProvisioningError
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_ads_draft import DraftClient

class ImageClient(DraftClient):
    def __init__(self):
        super().__init__()
        self.uploads = 0
    async def post(self, path, params, *, before_submit, files):
        if path.endswith('/adimages'):
            await before_submit()
            self.uploads += 1
            self.uploaded = next(iter(files.values()))[1].read()
            return {'images': {'image.png': {'hash': 'fixture-image-hash'}}}
        return await super().post(path, params, before_submit=before_submit, files=files)
    async def get(self, path, params):
        if path == '/act_111111':
            return {'id':'act_111111','account_status':1,'business':{'id':'222222'},'promotable_page_ids':['555555']}
        if path.endswith('/adimages'):
            return {'data':[{'hash':'fixture-image-hash','account_id':'111111'}]}
        if path in {'/777771','/777772','/777773'}:
            level = {'/777771':'campaign','/777772':'ad_set','/777773':'ad'}[path]
            fragment = next(row for row in self.fragments.values() if row['ad_object_type'] == level)
            values = {row['field']:json.loads(row['new_value']) for row in fragment['values']}
            if level == 'ad':
                self.expected_creative = values['creative']
                values['creative'] = {'id':'777774'}
            return {'id':path[1:],'account_id':'111111',**values}
        if path == '/777774':
            return {'id':'777774','account_id':'111111',**copy.deepcopy(self.expected_creative)}
        return await super().get(path, params)

class LaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = ProvisioningStateStore(str(root/'state.sqlite'))
        await self.state.init()
        contracts = PrivateLaunchContractStore('{}')
        contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        self.context = SimpleNamespace(cookies={'c_user':'123456'}, ad_accounts=[
            {'business_id':'222222','account_id':'111111'}])
        self.client = ImageClient()
        self.session = SimpleNamespace(facebook_web=AsyncMock(return_value=SimpleNamespace()))
        data = b'synthetic image bytes'
        sha = hashlib.sha256(data).hexdigest()
        path = root/('m-'+sha[:24]+'.png')
        path.write_bytes(data)
        self.media = LocalMedia('m-'+sha[:24],sha,len(data),'image','image/png',path)
        self.payload = {'business_id':'222222','ad_account_id':'111111','page_id':'555555','launch_key':'image-launch',
            'launch':{'campaign':{'name':'campaign','daily_budget':2000},'adset':{'name':'adset'},
                      'ad':{'name':'ad'},'creative':{'name':'creative','message':'text',
                          'link':'https://example.com','headline':'headline','format':'SINGLE',
                          'media_library_id':self.media.id}}}
        self.funding = {'account_id':'111111', 'business_id':'222222',
            'payment_account_id':'333333',
            'account_scope_verified':True,'business_scope_verified':True,
            'payment_account_relation_verified':True,'methods_query_verified':True,
            'checked_live':True,'card_linked':True, 'inventory_complete':True,
            'payment_methods':[{'type':'Visa','last4':'1234',
                'needs_verification':False, 'verification_tasks_observed':True,
                'verification_tasks':[], 'card_confirmation_status':'CLEAR',
                'is_expired':False}]}
        contexts = [
            patch('app.private_ads_launch.AdsManagerPrivateHttp.open',new=AsyncMock(return_value=self.client)),
            patch('app.private_launch_media.LaunchMediaResolver.resolve',new=AsyncMock(return_value=[self.media])),
            patch('app.static_payment_read.inspect_methods',new=AsyncMock(return_value=self.funding))]
        for context in contexts:
            context.start()
            self.addCleanup(context.stop)
    async def launch(self):
        return await self.owner.run(item_id='fixture',profile_id='19',context=self.context,
                                    session=self.session,payload=self.payload)
    async def test_existing_launch_entrypoint_uploads_image_publishes_and_verifies(self):
        result = await self.launch()
        self.assertEqual(result['status'],'SUCCESS')
        self.assertEqual(result['entities']['creative_id'],'777774')
        self.assertEqual(self.client.uploads,1)
        self.assertEqual(len(self.client.posts),5)
        await self.launch()
        self.assertEqual(self.client.uploads,1)
        self.assertEqual(len(self.client.posts),5)
    async def test_existing_review_entrypoint_performs_reads_without_post(self):
        result = await self.owner.review(profile_id='19',context=self.context,payload=self.payload,
                                        transport=SimpleNamespace(session=self.session))
        self.assertTrue(result['ready'])
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
    async def test_offline_plan_never_resolves_profile_or_marks_live_ready(self):
        result = self.owner.plan(profile_id='19',payload=self.payload)
        self.assertFalse(result['ready'])
        self.assertFalse(result['checked_live'])
        self.assertEqual(result['mutation_posts'],0)
        self.session.facebook_web.assert_not_awaited()
    async def test_active_video_and_unsupported_creative_reject_before_any_post(self):
        for change in ('active','video','advanced'):
            payload = copy.deepcopy(self.payload)
            if change == 'active':
                payload['launch']['delivery']={'campaign':'ACTIVE'}
            elif change == 'video':
                payload['launch']['creative']['existing_video_id']='777775'
            else:
                payload['launch']['creative']['unsupported_option']=True
            with self.subTest(change=change),self.assertRaises(ProvisioningError):
                await self.owner.run(item_id='fixture',profile_id='19',context=self.context,
                                     session=self.session,payload=payload)
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
    async def test_filtered_methods_expand_using_exact_business_before_review(self):
        self.funding['inventory_complete'] = False
        expanded = {**self.funding, 'inventory_complete':True}
        with patch('app.static_payment_read.complete_methods',
                   new=AsyncMock(return_value=expanded)) as complete:
            result = await self.owner.review(profile_id='19', context=self.context,
                                             payload=self.payload,
                                             transport=SimpleNamespace(session=self.session))
        self.assertTrue(result['ready'])
        complete.assert_awaited_once()
        self.assertEqual(complete.await_args.kwargs['business_id'], '222222')
        self.assertEqual(self.client.posts, [])

    async def test_filtered_empty_methods_can_recover_scoped_card_from_full_read(self):
        full = {**self.funding, 'inventory_complete':True}
        self.funding.update(inventory_complete=False, card_linked=False, payment_methods=[])
        with patch('app.static_payment_read.complete_methods',
                   new=AsyncMock(return_value=full)) as complete:
            review = await self.owner.review(profile_id='19', context=self.context,
                                             payload=self.payload,
                                             transport=SimpleNamespace(session=self.session))
        self.assertTrue(review['ready'])
        complete.assert_awaited_once()
        self.assertEqual(self.client.posts, [])

    async def test_multiple_live_cards_stay_in_review_selection_required(self):
        self.funding['payment_methods'].append({
            'type':'Mastercard', 'last4':'5678', 'needs_verification':False,
            'verification_tasks_observed':True, 'verification_tasks':[],
            'card_confirmation_status':'CLEAR', 'is_expired':False})
        review = await self.owner.review(profile_id='19', context=self.context,
                                        payload=self.payload,
                                        transport=SimpleNamespace(session=self.session))
        self.assertFalse(review['ready'])
        self.assertTrue(review['preflight']['payment_selection_required'])
        self.assertEqual(self.client.posts, [])
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_SELECTION_REQUIRED')
        self.assertEqual(self.client.posts, [])

    async def test_incomplete_methods_refuse_publication_without_post(self):
        self.funding['inventory_complete'] = False
        with patch('app.static_payment_read.complete_methods',
                   new=AsyncMock(return_value=self.funding)) as complete:
            with self.assertRaises(ProvisioningError) as error:
                await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_UNVERIFIED')
        complete.assert_awaited_once()
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_foreign_account_in_complete_payment_read_is_blocked(self):
        self.funding['account_id'] = '999999'
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_SCOPE_UNVERIFIED')
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_unknown_card_confirmation_does_not_pass_payment_readiness(self):
        self.funding['payment_methods'][0]['card_confirmation_status'] = 'UNKNOWN'
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_CONFIRMATION_UNVERIFIED')
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_unverified_payment_prevents_upload_and_draft(self):
        self.funding['card_linked']=False
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
    async def test_changed_file_is_rejected_before_upload_or_draft(self):
        self.media.path.write_bytes(b'changed bytes')
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
    async def test_actor_change_cannot_resume_an_existing_launch(self):
        await self.launch()
        self.context.cookies['c_user']='999999'
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(self.client.uploads,1)
        self.assertEqual(len(self.client.posts),5)
