import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
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
        self.pixels = [{'id':'888881','name':'Account pixel','is_unavailable':False}]
    async def post(self, path, params, *, before_submit, files):
        if path.endswith('/adimages'):
            await before_submit()
            self.uploads += 1
            self.uploaded = next(iter(files.values()))[1].read()
            return {'images': {next(iter(files)): {'hash': 'fixture-image-hash'}}}
        return await super().post(path, params, before_submit=before_submit, files=files)
    async def get(self, path, params):
        if path.endswith('/adspixels'):
            return {'data':copy.deepcopy(self.pixels)}
        if path == '/act_111111':
            return {'id':'act_111111','account_status':1,'business':{'id':'222222'},'ad_account_promotable_objects':{'promotable_page_ids':['555555']}}
        if path.endswith('/adimages'):
            return {'data':[{'hash':'fixture-image-hash','account_id':'111111'}]}
        if path in {'/777771','/777772','/777773'}:
            level = {'/777771':'campaign','/777772':'ad_set','/777773':'ad'}[path]
            fragment = next(row for row in self.fragments.values() if row['ad_object_type'] == level)
            values = {row['field']:copy.deepcopy(row['new_value']) for row in fragment['values']}
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
            'launch':{'execution_mode':'PUBLISH','campaign':{'name':'campaign','daily_budget':2000,'objective':'OUTCOME_TRAFFIC'},
                      'adset':{'name':'adset','optimization_goal':'LINK_CLICKS','billing_event':'IMPRESSIONS',
                               'destination_type':'WEBSITE','targeting':{'geo_locations':{'countries':['UA']}}},
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
            # These existing cases test transport/journal behavior with synthetic
            # bytes. Real byte acceptance is exercised in test_media_preflight.
            patch('app.media_preflight.inspect_managed', new=AsyncMock(return_value={
                'media_type':'image', 'mime_type':'image/png', 'width':1080, 'height':1080,
                'aspect_ratio':1.0, 'requires_conversion':False,
                'integrity_verified':False, 'meta_acceptance_verified':False})),
            patch('app.private_draft_pages.verify_draft_page', new=AsyncMock(return_value={
                'page_id':'555555', 'checked_live':True, 'publication_access_verified':False})),
            patch('app.private_ads_launch.AdsManagerPrivateHttp.open',new=AsyncMock(return_value=self.client)),
            patch('app.private_launch_media.LaunchMediaResolver.resolve',new=AsyncMock(return_value=[self.media])),
            patch('app.static_payment_read.inspect_methods',new=AsyncMock(return_value=self.funding))]
        for context in contexts:
            context.start()
            self.addCleanup(context.stop)
    async def launch(self):
        return await self.owner.run(item_id='fixture',profile_id='19',context=self.context,
                                    session=self.session,payload=self.payload)
    async def test_missing_execution_mode_fails_before_meta_or_media_operations(self):
        self.payload['launch'].pop('execution_mode')
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta reads allowed'))
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_INVALID_INPUT')
        self.client.get.assert_not_awaited()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])
        with self.assertRaises(ProvisioningError):
            self.owner.ads_manager.plan(profile_id='19', payload=self.payload)

    async def test_account_override_cannot_change_draft_intent_to_publish(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        self.payload['launch'] = {
            'base': copy.deepcopy(self.payload['launch']),
            'override': {'execution_mode': 'PUBLISH'}
        }
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta reads allowed'))
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, 'PRIVATE_ADS_DRAFT_INVALID_INPUT')
        self.client.get.assert_not_awaited()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

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

    async def test_launch_account_read_rejects_conflicting_secondary_id_before_mutation(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get
        async def changed(path, params):
            record = await original(path, params)
            if path == '/act_111111':
                record['account_id'] = '999999'
            return record
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertIn('ACCOUNT_SCOPE_UNVERIFIED', caught.exception.code)
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

    async def test_real_draft_mode_verifies_fragments_without_payment_publish_or_delivery(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        self.payload['launch']['adset']['targeting'] = {'geo_locations': {'countries': ['UA']},
            'age_min': 23, 'age_max': 33, 'locales': [52],
            'interests': [{'id': '6003139266461', 'name': 'Physical fitness'}],
            'behaviors': [{'id': '6015548561283', 'name': 'Chrome'}]}
        with patch('app.static_payment_read.inspect_methods',new=AsyncMock(side_effect=AssertionError('No billing read'))) as payment, \
                patch.object(self.owner.ads_manager, '_finish_delivery',new=AsyncMock(side_effect=AssertionError('No delivery'))) as delivery:
            review = await self.owner.review(profile_id='19',context=self.context,payload=self.payload,
                transport=SimpleNamespace(session=self.session))
            self.assertTrue(review['ready'])
            self.assertEqual(review['ready_for'], 'DRAFT')
            self.assertFalse(review['preflight']['payment_checked_live'])
            self.assertFalse(review['preflight']['publication_ready'])
            result = await self.launch()
            repeated = await self.launch()
        self.assertTrue(result['draft_verified'])
        self.assertEqual(result['editor_state'], 'EDITOR_UNVERIFIED')
        self.assertFalse(result['editor_verified'])
        self.assertEqual(result['draft_id'], repeated['draft_id'])
        self.assertFalse(result['published'])
        self.assertFalse(result['verified'])
        self.assertNotIn('entities', result)
        self.assertNotIn('publication_id', result)
        self.assertEqual(len(result['fragment_ids']), 3)
        self.assertEqual(len(self.client.posts), 4)
        self.assertEqual(self.client.uploads, 1)
        self.assertFalse(any(row['step'] == 'ADS_MANAGER:PUBLISH' for row in await self.owner.state.all_steps('image-launch')))
        payment.assert_not_awaited()
        delivery.assert_not_awaited()

    async def test_draft_never_requests_promotable_projection_and_denied_owned_page_stops_posts(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get
        account_reads = []
        async def projected(path, params):
            if path == '/act_111111':
                account_reads.append(params['fields'])
                self.assertNotIn('ad_account_promotable_objects', params['fields'])
            return await original(path, params)
        self.client.get = projected
        with patch('app.private_draft_pages.verify_draft_page', new=AsyncMock(
                side_effect=ProvisioningError('DRAFT_PAGE_DENIED', 'Denied'))):
            with self.assertRaises(ProvisioningError):
                await self.launch()
        self.assertTrue(account_reads)
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_draft_mode_cannot_change_to_publication_under_the_same_key(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        await self.launch()
        self.payload['launch']['execution_mode'] = 'PUBLISH'
        with patch('app.static_payment_read.inspect_methods',new=AsyncMock(side_effect=AssertionError('No billing read'))) as payment:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 4)
        payment.assert_not_awaited()

    async def test_draft_mode_rejects_activation_or_malformed_mode_before_live_reads(self):
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read'))
        for mode in ([], {}, True, 'UNKNOWN', 'DRAFT'):
            self.payload['launch']['execution_mode'] = mode
            if mode == 'DRAFT':
                self.payload['launch']['delivery'] = {'campaign':'ACTIVE'}
            with self.subTest(mode=mode), self.assertRaises(ProvisioningError):
                await self.launch()
        self.client.get.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_draft_mode_needs_independent_matching_fragment_values(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get
        async def tampered(path, params):
            row = await original(path, params)
            if path.startswith('/addraftfragment_') and row.get('ad_object_type') == 'ad_set':
                row['values'] = []
            return row
        self.client.get = tampered
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH')
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))

    async def test_published_object_read_recovers_after_restart_without_draft_rows(self):
        original = self.client.get
        async def interrupted(path, params):
            if path == '/777771':
                raise ConnectionError('object read interrupted after publication')
            return await original(path, params)
        self.client.get = interrupted
        with self.assertRaises(ConnectionError):
            await self.launch()
        self.assertEqual(len(self.client.posts), 5)
        reads = []
        async def consumed(path, params):
            reads.append(path)
            if path == '/addraft_555555' or path.startswith('/addraftfragment_'):
                raise AssertionError('consumed draft row cannot be required for recovery')
            return await original(path, params)
        self.client.get = consumed
        contracts = PrivateLaunchContractStore('{}')
        contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        with patch('app.static_payment_read.inspect_methods', new=AsyncMock()) as payment:
            result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(result['entities'], {'campaign_id':'777771', 'adset_id':'777772',
            'ad_id':'777773', 'creative_id':'777774'})
        self.assertTrue({'/777771','/777772','/777773','/777774'} <= set(reads))
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)
        self.assertTrue(self.client.read_only)
        payment.assert_not_awaited()

    async def test_page_permission_is_required_before_payment_upload_or_draft(self):
        original = self.client.get
        async def denied(path, params):
            row = await original(path, params)
            if path == '/act_111111':
                row['ad_account_promotable_objects']['promotable_page_ids'] = []
            return row
        self.client.get = denied
        with patch('app.static_payment_read.inspect_methods', new=AsyncMock()) as payment:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_PAGE_UNVERIFIED')
        payment.assert_not_awaited()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

    async def accepted_predecessor_publication(self, source=None):
        from app.private_ads_launch import READ_ONLY_PUBLICATION_PREDECESSORS
        self.owner.ads_manager.source_digest = source or next(iter(READ_ONLY_PUBLICATION_PREDECESSORS))
        original = self.client.get
        async def interrupted(path, params):
            if path == '/777771':
                raise ConnectionError('interrupted after accepted publication')
            return await original(path, params)
        self.client.get = interrupted
        # Seed the predecessor's original serializer, not today's bidding
        # fields under an old source digest. Recovery must retain old bindings.
        with patch('app.private_ads_launch.native_bidding',
                   side_effect=lambda campaign, adset: (copy.deepcopy(campaign), copy.deepcopy(adset), {})), \
                patch.object(self.owner.ads_manager.objective_wire, 'campaign', side_effect=copy.deepcopy), \
                self.assertRaises(ConnectionError):
            await self.launch()
        self.client.get = original
        contracts = PrivateLaunchContractStore('{}')
        contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        return original

    async def test_reviewed_predecessor_resumes_only_fresh_publication_reads(self):
        original = await self.accepted_predecessor_publication()
        reads = []
        async def observed(path, params):
            reads.append(path)
            if path == '/addraft_555555' or path.startswith('/addraftfragment_'):
                raise AssertionError('Consumed draft rows cannot be read')
            return await original(path, params)
        self.client.get = observed
        with patch.object(self.owner.ads_manager, '_finish_delivery', new=AsyncMock()) as delivery:
            result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertTrue(result['recovery_read_only'])
        self.assertTrue(self.client.read_only)
        self.assertEqual(result['delivery'], {'campaign':'PAUSED','adset':'PAUSED','ad':'PAUSED'})
        self.assertTrue({'/777771','/777772','/777773','/777774'} <= set(reads))
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)
        delivery.assert_not_awaited()

    async def test_unknown_predecessor_is_rejected_before_live_reads(self):
        await self.accepted_predecessor_publication('f' * 64)
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read allowed'))
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.client.get.assert_not_awaited()
        self.assertEqual(len(self.client.posts), 5)

    async def test_predecessor_cannot_resume_activation_or_another_actor(self):
        await self.accepted_predecessor_publication()
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read allowed'))
        self.context.cookies['c_user'] = '999999'
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.context.cookies['c_user'] = '123456'
        self.payload['launch']['delivery'] = {'campaign':'ACTIVE','adset':'ACTIVE','ad':'ACTIVE'}
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.get.assert_not_awaited()
        self.assertEqual(len(self.client.posts), 5)

    async def test_predecessor_without_publish_receipt_cannot_create_a_new_intent(self):
        await self.accepted_predecessor_publication()
        with self.owner.state._connect() as con:
            row = con.execute("SELECT result_json FROM private_launch_steps WHERE launch_key=? AND step=?",
                ('image-launch','ADS_MANAGER:PUBLISH')).fetchone()
            saved = json.loads(row['result_json'])
            saved['evidence']['receipt'] = {}
            con.execute("UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?",
                (json.dumps(saved),'image-launch','ADS_MANAGER:PUBLISH'))
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read allowed'))
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.get.assert_not_awaited()
        self.assertEqual(len(self.client.posts), 5)

    async def test_predecessor_recovery_rejects_fresh_active_object_without_mutating(self):
        original = await self.accepted_predecessor_publication()
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777771':
                row['status'] = 'ACTIVE'
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_OBJECT_SPEC_MISMATCH')
        self.assertTrue(self.client.read_only)
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)

    async def test_predecessor_recovery_rejects_mixed_source_journal_before_reads(self):
        await self.accepted_predecessor_publication()
        with self.owner.state._connect() as con:
            row = con.execute("SELECT result_json FROM private_launch_steps WHERE launch_key=? AND step=?",
                ('image-launch','ADS_MANAGER:PUBLISH')).fetchone()
            saved = json.loads(row['result_json'])
            saved['evidence']['source_digest'] = self.owner.ads_manager.source_digest
            con.execute("UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?",
                (json.dumps(saved),'image-launch','ADS_MANAGER:PUBLISH'))
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read allowed'))
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.get.assert_not_awaited()
        self.assertEqual(len(self.client.posts), 5)

    async def test_offline_plan_never_resolves_profile_or_marks_live_ready(self):
        result = self.owner.plan(profile_id='19',payload=self.payload)
        self.assertFalse(result['ready'])
        self.assertFalse(result['checked_live'])
        self.assertTrue(result['configuration_valid'])
        self.assertEqual(result['editor_state'], 'EDITOR_UNVERIFIED')
        self.assertEqual(result['missing_fields'], [])
        self.assertEqual(result['missing_contracts'], [])
        self.assertEqual(result['mutation_posts'],0)
        self.session.facebook_web.assert_not_awaited()

    async def test_review_bidding_evidence_keeps_selected_and_wire_levels_distinct(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        plan = self.owner.ads_manager.plan(profile_id='19', payload=self.payload)
        bidding = plan['bidding_representation']
        self.assertEqual(bidding['budget_owner'], 'campaign')
        self.assertEqual(bidding['selected'], {
            'campaign_strategy': None, 'adset_strategy': None, 'adset_bid_amount': None})
        self.assertEqual(bidding['private_campaign']['bid_strategy'], 'LOWEST_COST_WITHOUT_CAP')
        self.assertTrue(bidding['private_campaign']['is_autobid'])
        self.assertNotIn('bid_strategy', bidding['private_adset'])
        self.assertFalse(bidding['checked_live'])
        self.assertEqual(bidding['editor_state'], 'EDITOR_UNVERIFIED')
        self.payload['launch']['campaign'].pop('daily_budget')
        self.payload['launch']['adset'].update(daily_budget=2000,
            bid_strategy='COST_CAP', bid_amount=75)
        plan2 = self.owner.ads_manager.plan(profile_id='19', payload=self.payload)
        bidding = plan2['bidding_representation']
        self.assertEqual(bidding['budget_owner'], 'adset')
        self.assertEqual(bidding['selected']['adset_strategy'], 'COST_CAP')
        self.assertEqual(bidding['selected']['adset_bid_amount'], 75)
        self.assertEqual(bidding['private_adset']['bid_strategy'], 'COST_CAP')
        self.assertEqual(bidding['private_adset']['bid_amount'], 75)
        self.assertNotIn('bid_strategy', bidding['private_campaign'])
        self.assertFalse(plan2['checked_live'])
        self.assertEqual(plan2['launch_fingerprint'],
            self.owner.ads_manager.plan(profile_id='19', payload=self.payload)['launch_fingerprint'])
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_draft_allows_only_inert_worker_metadata_and_explicit_control_flags(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        accepted = copy.deepcopy(self.payload)
        accepted.update(variables={}, profile_id='19')
        plan = self.owner.ads_manager.plan(profile_id='19', payload=accepted)
        self.assertEqual(plan['target']['profile_id'], '19')
        self.assertEqual(plan['launch_fingerprint'],
            self.owner.ads_manager.plan(profile_id='19', payload=self.payload)['launch_fingerprint'])
        for change in ({'variables': {'unreviewed': True}}, {'profile_id': '20'},
                       {'repair_objective': False}, {'reconcile_only': 'true'},
                       {'publish_saved_draft': True, 'repair_objective': True},
                       {'selected_payment_method': {'last4': '1234'}}):
            with self.subTest(change=change):
                candidate = copy.deepcopy(accepted)
                candidate.update(change)
                with self.assertRaises(ProvisioningError):
                    self.owner.ads_manager.plan(profile_id='19', payload=candidate)
        repaired = copy.deepcopy(self.payload)
        repaired['repair_objective'] = True
        self.owner.ads_manager.prepare('19', repaired)
        inspected = copy.deepcopy(self.payload)
        inspected['reconcile_only'] = True
        self.owner.ads_manager.prepare('19', inspected)
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_draft_strips_only_legacy_meta_marker_before_launch_validation(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        direct = copy.deepcopy(self.payload)
        direct['launch']['_meta_official_v1'] = True
        direct_plan = self.owner.ads_manager.plan(profile_id='19', payload=direct)
        self.assertTrue(direct_plan['configuration_valid'])
        self.assertNotIn('_meta_official_v1', direct_plan['launch'])

        wrapped = copy.deepcopy(self.payload)
        wrapped['launch'] = {'base': copy.deepcopy(wrapped['launch']), 'override': {}}
        wrapped['launch']['base']['_meta_official_v1'] = True
        wrapped_plan = self.owner.ads_manager.plan(profile_id='19', payload=wrapped)
        self.assertTrue(wrapped_plan['configuration_valid'])
        self.assertNotIn('_meta_official_v1', wrapped_plan['launch'])

        malformed = copy.deepcopy(self.payload)
        malformed['launch']['_meta_official_v1'] = 'true'
        with self.assertRaises(ProvisioningError):
            self.owner.ads_manager.plan(profile_id='19', payload=malformed)

        unsupported = copy.deepcopy(self.payload)
        unsupported['launch']['unknown_ui_section'] = {}
        with self.assertRaises(ProvisioningError) as error:
            self.owner.ads_manager.plan(profile_id='19', payload=unsupported)
        self.assertIn('unsupported sections', str(error.exception))
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_staged_media_rejects_client_paths_urls_and_foreign_checksum_before_network(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        creative = self.payload['launch']['creative']
        del creative['media_library_id']
        managed = {'id': self.media.id, 'sha256': self.media.sha256,
                   'size_bytes': self.media.size_bytes,
                   'media_type': 'image', 'mime_type': 'image/png'}
        creative['staged_media'] = [managed]
        valid_plan = self.owner.ads_manager.plan(profile_id='19', payload=self.payload)
        self.assertEqual(valid_plan['local_media'], [
            {'id':self.media.id, 'sha256':self.media.sha256}])
        for change in ({'path':'/tmp/unreviewed.png'},
                       {'url':'https://example.org/foreign-media'},
                       {'id':'m-'+'f'*24},
                       {'sha256':'b'*64},
                       {'access_token':'fake-user-parameter'}):
            with self.subTest(change=change):
                creative['staged_media'] = [{**managed, **change}]
                with self.assertRaises(ProvisioningError):
                    self.owner.ads_manager.plan(profile_id='19', payload=self.payload)
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_versioned_launch_acceptance_fixture_matches_request_projection(self):
        fixture_path = Path(__file__).parent / 'fixtures/launch/single-image-draft.json'
        accepted = json.loads(fixture_path.read_text())
        self.payload = copy.deepcopy(accepted['payload'])
        plan = self.owner.ads_manager.plan(profile_id='19', payload=self.payload)
        campaign, adset, _ = __import__('app.private_ads_bidding', fromlist=['native_bidding']).native_bidding(
            plan['launch']['campaign'], plan['launch']['adset'])
        campaign = self.owner.ads_manager.objective_wire.campaign(campaign)
        from app.private_ads_creative import resolved_creative
        creative = resolved_creative(plan['launch']['creative'], plan['target']['page_id'],
            [{'image_hash': plan['launch']['creative']['existing_image_hash']}])
        link_data = creative['object_story_spec']['link_data']
        actual = {
            'campaign': {key: campaign[key] for key in accepted['expected_request_projection']['campaign']},
            'ad_set': {key: adset[key] for key in accepted['expected_request_projection']['ad_set']},
            'ad': {'name': plan['launch']['ad']['name'], 'status': plan['launch']['ad']['status'],
                   'creative_page_id': creative['object_story_spec']['page_id'],
                   'image_hash': link_data['image_hash']},
        }
        self.assertEqual(actual, accepted['expected_request_projection'])
        self.assertEqual(plan['launch_fingerprint'], self.owner.ads_manager.plan(
            profile_id='19', payload=self.payload)['launch_fingerprint'])
        self.assertEqual(accepted['source_sha'], 'd55851e37c7ae6ec3bcd6cf8e746ce1ce940e81a')
        self.assertFalse(accepted['checked_live'])
        self.assertEqual(accepted['editor_state'], 'EDITOR_UNVERIFIED')
        self.assertEqual(plan['editor_state'], 'EDITOR_UNVERIFIED')
        self.assertFalse(plan['checked_live'])

    async def test_draft_unknown_sections_and_fields_fail_before_session_or_post(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        invalid = [
            ('launch', 'unreviewed_section', {}),
            ('payload', 'unreviewed_field', 'ignored'),
        ]
        for location, key, value in invalid:
            with self.subTest(location=location, key=key):
                candidate = copy.deepcopy(self.payload)
                (candidate['launch'] if location == 'launch' else candidate)[key] = value
                with self.assertRaises(ProvisioningError):
                    self.owner.ads_manager.plan(profile_id='19', payload=candidate)
        candidate = copy.deepcopy(self.payload)
        candidate['launch'] = {'base':candidate['launch'], 'override':{}, 'unreviewed_section':{}}
        with self.assertRaises(ProvisioningError):
            self.owner.ads_manager.plan(profile_id='19', payload=candidate)
        self.session.facebook_web.assert_not_awaited()
        self.assertEqual(self.client.posts, [])

    async def test_selected_asset_requires_fresh_scope_and_keeps_label_source_time_separate(self):
        observed = {'data':[{'id':'123456','name':'Selected audience'}],
            'account_id':'111111','account_scope_verified':True,'checked_live':True,
            'checked_at':int(time.time()),'source':'ads_manager_private_http'}
        with patch.object(self.owner.reads, 'read', new=AsyncMock(return_value=observed)):
            checks = await self.owner._verify_assets(None, {'custom_audiences':['123456']},
                {'ad_account_id':'111111'})
        self.assertEqual(checks['custom_audiences']['selected'], ['123456'])
        self.assertEqual(checks['custom_audiences']['selected_details'],
            [{'id':'123456','label':'Selected audience'}])
        self.assertEqual(checks['custom_audiences']['source'], 'ads_manager_private_http')
        self.assertEqual(checks['custom_audiences']['checked_at'], observed['checked_at'])
        for changed in ({'checked_live':False}, {'account_id':'999999'}, {'checked_at':0},
                        {'data':[]}, {'query_required':True},
                        {'truncated':True}, {'inventory_complete':False}):
            with self.subTest(changed=changed), patch.object(self.owner.reads, 'read',
                    new=AsyncMock(return_value={**observed, **changed})):
                with self.assertRaises(ProvisioningError):
                    await self.owner._verify_assets(None, {'custom_audiences':['123456']},
                        {'ad_account_id':'111111'})

    async def test_live_api_review_keeps_editor_acceptance_unverified(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        result = await self.owner.review(profile_id='19', context=self.context, payload=self.payload,
            transport=SimpleNamespace(session=self.session))
        self.assertTrue(result['configuration_valid'])
        self.assertTrue(result['checked_live'])
        self.assertTrue(result['api_preflight_ready'])
        self.assertEqual(result['editor_state'], 'EDITOR_UNVERIFIED')
        self.assertEqual(result['missing_fields'], [])
        self.assertEqual(result['missing_contracts'], [])
    async def test_competing_video_and_unsupported_creative_reject_before_any_post(self):
        for change in ('competing_video','advanced'):
            payload = copy.deepcopy(self.payload)
            if change == 'competing_video':
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

    async def test_launch_retry_keeps_explicit_rejection_without_upload_or_post_replay(self):
        from app.private_ads_http import AdsManagerResponseError
        original_post = self.client.post
        async def reject_draft(path, params, *, before_submit, files):
            if path.endswith('/addrafts'):
                await before_submit()
                self.client.posts.append((path, params))
                raise AdsManagerResponseError({'http_status':400, 'code':100})
            return await original_post(path, params, before_submit=before_submit, files=files)
        self.client.post = reject_draft
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_REQUEST_REJECTED')
            self.assertIn('code=100', str(caught.exception))
        self.assertEqual(self.client.uploads,1)
        self.assertEqual(len(self.client.posts),1)

    async def test_selected_account_pixel_publishes_and_verifies_without_duplicate_post(self):
        self.payload['launch']['campaign']['objective'] = 'OUTCOME_LEADS'
        self.payload['launch']['adset']['optimization_goal'] = 'OFFSITE_CONVERSIONS'
        self.payload['launch']['adset']['promoted_object'] = {'pixel_id':'888881','custom_event_type':'LEAD'}
        plan = self.owner.plan(profile_id='19', payload=self.payload)
        self.assertTrue(plan['configuration_valid'])
        self.assertEqual(plan['mutation_posts'],0)
        result = await self.launch()
        self.assertTrue(result['preflight']['asset_checks']['pixels']['verified'])
        self.assertTrue(result['verified'])
        await self.launch()
        self.assertEqual(self.client.uploads,1)
        self.assertEqual(len(self.client.posts),5)

    async def test_absent_or_unavailable_pixel_stops_before_upload_and_draft(self):
        self.payload['launch']['campaign']['objective'] = 'OUTCOME_LEADS'
        self.payload['launch']['adset']['optimization_goal'] = 'OFFSITE_CONVERSIONS'
        self.payload['launch']['adset']['promoted_object'] = {'pixel_id':'888881','custom_event_type':'LEAD'}
        for rows in ([], [{'id':'888881','name':'Pixel','is_unavailable':True}]):
            self.client.pixels = rows
            with self.subTest(rows=rows), self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_ASSET_ACCESS_UNCONFIRMED')
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
    async def test_saved_publication_resumes_without_billing_or_local_bytes(self):
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.media.path.unlink()
        with patch('app.static_payment_read.inspect_methods', new=AsyncMock(side_effect=RuntimeError('synthetic billing outage'))), \
             patch('app.private_launch_media.LaunchMediaResolver.resolve', new=AsyncMock(side_effect=FileNotFoundError())):
            recovered = await self.launch()
        self.assertTrue(recovered['verified'])
        self.assertTrue(recovered['preflight']['resume_read_only'])
        self.assertFalse(recovered['preflight']['payment_checked_live'])
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(len(self.client.posts), 5)
