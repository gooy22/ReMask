import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture


class SavedDraftInspectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LaunchTests.asyncSetUp
    launch = fixture.LaunchTests.launch

    async def saved(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        await self.launch()
        self.payload['reconcile_only'] = True

    async def test_native_account_parent_is_root_only_and_read_only(self):
        await self.saved()
        self.client.draft_root_parent_id = '666666'
        self.client.fragments['111111']['parent_ad_object_id'] = '666666'
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
            result = await self.launch()
        self.assertTrue(result['draft_verified'])
        post.assert_not_awaited()
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)
        self.client.fragments['222222']['parent_ad_object_id'] = '666666'
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')

    async def test_fresh_reads_of_saved_hierarchy_never_upload_publish_or_activate(self):
        await self.saved()
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post, \
                patch('app.static_payment_read.inspect_methods', new=AsyncMock(side_effect=AssertionError('No billing'))) as payment:
            result = await self.launch()
        post.assert_not_called(); payment.assert_not_called()
        self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(result['execution_mode'], 'DRAFT_INSPECT')
        self.assertEqual(result['draft_objects'], {'campaign':'777771', 'ad_set':'777772', 'ad':'777773'})
        self.assertTrue(result['fragment_storage_verified'])
        self.assertTrue(result['meta_validation_complete'])
        self.assertTrue(result['draft_verified'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(result['fragment_reads'][0]['submitted_values']['objective'], 'LINK_CLICKS')
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_partial_saved_campaign_is_inspected_through_exact_draft_edge_without_post(self):
        from app.private_ads_draft import fail
        from app.private_ads_http import AdsManagerResponseError
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get

        async def interrupted_campaign(path, params):
            if path == '/addraftfragment_111111':
                raise fail('READ_PENDING', 'synthetic interrupted Campaign read', True)
            return await original(path, params)

        self.client.get = interrupted_campaign
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_PENDING')
        self.assertEqual(len(self.client.posts), 2)

        async def edge_read(path, params):
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                fields = set(params.get('fields') or [])
                rows = [
                    {key: copy.deepcopy(value) for key, value in row.items() if key in fields}
                    for row in self.client.fragments.values()
                ]
                return {'data': rows}
            return await original(path, params)

        self.client.get = edge_read
        self.payload['reconcile_only'] = True
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(result['execution_mode'], 'DRAFT_INSPECT')
        self.assertEqual(result['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(result['pending_stage'],
                         {'stage': 'FRAGMENT_AD_SET', 'reason': 'NOT_SUBMITTED'})
        self.assertEqual(result['draft_objects'], {'campaign': '777771'})
        self.assertEqual([row['read_source'] for row in result['fragment_reads']], ['draft_edge'])
        self.assertEqual(result['mutation_posts'], 0)
        self.assertFalse(result['draft_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def _save_partial_campaign_for_account_readback(self, *, campaign_object_id=None):
        from app.private_ads_draft import fail
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original_get, original_post = self.client.get, self.client.post

        async def interrupted_campaign(path, params):
            if path == '/addraftfragment_111111':
                raise fail('READ_PENDING', 'synthetic interrupted Campaign read', True)
            return await original_get(path, params)

        async def campaign_receipt_post(path, params, **options):
            response = await original_post(path, params, **options)
            if (campaign_object_id is not None
                    and path.endswith('/addraft_fragments')
                    and params.get('ad_object_type') == 'campaign'):
                return {**response, 'ad_object_id': campaign_object_id}
            return response

        self.client.get = interrupted_campaign
        if campaign_object_id is not None:
            self.client.post = campaign_receipt_post
        try:
            with self.assertRaises(ProvisioningError):
                await self.launch()
        finally:
            self.client.post = original_post
        self.assertEqual(len(self.client.posts), 2)
        self.payload['reconcile_only'] = True
        return original_get

    async def _save_partial_campaign_for_distinct_fragment_readback(self):
        from app.private_ads_draft import fail
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original_get, original_post = self.client.get, self.client.post

        async def distinct_fragment_post(path, params, **options):
            response = await original_post(path, params, **options)
            if path.endswith('/addraft_fragments') and params.get('ad_object_type') == 'campaign':
                fragment = self.client.fragments.pop('111111')
                fragment['id'] = '444444'
                self.client.fragments['444444'] = fragment
                return {**response, 'id': '444444'}
            return response

        async def interrupted_campaign(path, params):
            if path == '/addraftfragment_444444':
                raise fail('READ_PENDING', 'synthetic interrupted Campaign read', True)
            return await original_get(path, params)

        self.client.post = distinct_fragment_post
        self.client.get = interrupted_campaign
        try:
            with self.assertRaises(ProvisioningError):
                await self.launch()
        finally:
            self.client.post, self.client.get = original_post, original_get
        self.assertEqual(len(self.client.posts), 2)
        self.payload['reconcile_only'] = True
        return original_get

    async def test_partial_campaign_never_guesses_fragment_id_as_object_without_receipt(self):
        from app.private_ads_http import AdsManagerResponseError
        original = await self._save_partial_campaign_for_distinct_fragment_readback()
        calls = []

        async def no_fragment_id_guess(path, params):
            calls.append(path)
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                return {'data': []}
            if path == '/444444':
                self.fail('A fragment receipt must never be used as an object ID')
            if path == '/act_111111/campaigns':
                fragment = self.client.fragments['444444']
                values = {row['field']: copy.deepcopy(row['new_value'])
                          for row in fragment['values']}
                return {'data': [{'id': '777771', 'account_id': '111111', **values}]}
            return await original(path, params)

        self.client.get = no_fragment_id_guess
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertNotIn('/444444', calls)
        self.assertEqual(calls.count('/act_111111/campaigns'), 1)
        self.assertEqual(result['execution_mode'], 'DRAFT_INSPECT')
        self.assertEqual(result['draft_objects'], {'campaign': '777771'})
        self.assertEqual(result['fragment_reads'][0]['read_source'], 'account_campaigns_read')
        self.assertEqual(result['mutation_posts'], 0)
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_partial_campaign_uses_unique_exact_paused_account_fallback_without_post(self):
        from app.private_ads_http import AdsManagerResponseError
        original = await self._save_partial_campaign_for_account_readback()
        calls = []

        async def account_read(path, params):
            calls.append(path)
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                return {'data': []}
            if path == '/111111':
                return {'id': '111111', 'account_id': '111111', 'status': 'ACTIVE'}
            if path == '/act_111111/campaigns':
                campaign_fragment = self.client.fragments['111111']
                values = {row['field']: copy.deepcopy(row['new_value'])
                          for row in campaign_fragment['values']}
                return {'data': [{'id': '777771', 'account_id': '111111', **values}]}
            return await original(path, params)

        self.client.get = account_read
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(calls.count('/act_111111/campaigns'), 1)
        self.assertEqual(result['execution_mode'], 'DRAFT_INSPECT')
        self.assertEqual(result['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(result['draft_objects'], {'campaign': '777771'})
        self.assertEqual(result['fragment_reads'][0]['read_source'], 'account_campaigns_read')
        self.assertTrue(result['fragment_reads'][0]['account_fallback_verified'])
        self.assertFalse(result['fragment_reads'][0]['fragment_storage_verified'])
        self.assertFalse(result['fragment_reads'][0]['fragment_relation_verified'])
        self.assertTrue(result['object_reads'][0]['available'])
        self.assertTrue(result['object_reads'][0]['settings_match'])
        self.assertFalse(result['fragment_storage_verified'])
        self.assertFalse(result['relationships_confirmed'])
        self.assertEqual(result['mutation_posts'], 0)
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_partial_campaign_uses_exact_saved_id_before_account_list_without_post(self):
        from app.private_ads_http import AdsManagerResponseError
        original = await self._save_partial_campaign_for_account_readback(
            campaign_object_id='777771')
        calls = []

        async def direct_read(path, params):
            calls.append(path)
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                return {'data': []}
            if path == '/777771':
                fragment = self.client.fragments['111111']
                values = {row['field']: copy.deepcopy(row['new_value'])
                          for row in fragment['values']}
                return {'id': '777771', 'account_id': '111111', **values}
            if path == '/act_111111/campaigns':
                self.fail('Exact saved object ID read should precede account-list fallback')
            return await original(path, params)

        self.client.get = direct_read
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(calls.count('/777771'), 1)
        self.assertNotIn('/111111', calls)
        self.assertNotIn('/act_111111/campaigns', calls)
        self.assertEqual(result['draft_objects'], {'campaign': '777771'})
        self.assertEqual(result['fragment_reads'][0]['read_source'],
                         'direct_saved_campaign_object_read')
        self.assertTrue(result['fragment_reads'][0]['account_fallback_verified'])
        self.assertTrue(result['object_reads'][0]['available'])
        self.assertTrue(result['object_reads'][0]['settings_match'])
        self.assertEqual(result['mutation_posts'], 0)
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_partial_campaign_saved_id_read_rejects_spec_mismatch_without_list_fallback(self):
        from app.private_ads_http import AdsManagerResponseError
        original = await self._save_partial_campaign_for_account_readback(
            campaign_object_id='777771')
        calls = []

        async def mismatched_direct_read(path, params):
            calls.append(path)
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                return {'data': []}
            if path == '/777771':
                fragment = self.client.fragments['111111']
                values = {row['field']: copy.deepcopy(row['new_value'])
                          for row in fragment['values']}
                return {'id': '777771', 'account_id': '111111', **values, 'name': 'different'}
            if path == '/act_111111/campaigns':
                self.fail('A mismatched saved object ID must not fall through to a name search')
            return await original(path, params)

        self.client.get = mismatched_direct_read
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        post.assert_not_awaited()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH')
        self.assertIn('/777771', calls)
        self.assertNotIn('/111111', calls)
        self.assertNotIn('/act_111111/campaigns', calls)
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_partial_campaign_account_fallback_rejects_multiple_exact_paused_matches(self):
        from app.private_ads_http import AdsManagerResponseError
        original = await self._save_partial_campaign_for_account_readback()

        async def ambiguous_read(path, params):
            if path.startswith('/addraftfragment_'):
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/addraft_555555/addraft_fragments':
                return {'data': []}
            if path == '/111111':
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path == '/act_111111/campaigns':
                campaign_fragment = self.client.fragments['111111']
                values = {row['field']: copy.deepcopy(row['new_value'])
                          for row in campaign_fragment['values']}
                row = {'id': '777771', 'account_id': '111111', **values}
                return {'data': [row, copy.deepcopy(row)]}
            return await original(path, params)

        self.client.get = ambiguous_read
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        post.assert_not_awaited()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_exact_native_objects_and_creative_are_read_separately_from_fragments(self):
        await self.saved()
        result = await self.launch()
        self.assertEqual([row['level'] for row in result['object_reads']], ['campaign','ad_set','ad','creative'])
        self.assertTrue(all(row['available'] and row['settings_match'] for row in result['object_reads']))
        self.assertEqual(result['unverified_object_levels'], [])
        self.assertTrue(result['launch_fingerprint_verified'])
        self.assertTrue(result['actor_verified'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), 4)

    async def test_campaign_with_foreign_parent_cannot_confirm_saved_hierarchy(self):
        await self.saved()
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        original, reads = self.client.get, []

        async def foreign_parent(path, params):
            reads.append(path)
            row = await original(path, params)
            if row.get('ad_object_type') == 'campaign':
                row['parent_ad_object_id'] = '999999'
            return row

        self.client.get = foreign_parent
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        post.assert_not_awaited()
        self.assertNotIn('/addraftfragment_222222', reads)
        self.assertNotIn('/777771', reads)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_root_campaign_accepts_missing_null_or_empty_parent(self):
        await self.saved()
        original = self.client.get
        for parent in ('missing', None, ''):
            async def root_parent(path, params):
                row = await original(path, params)
                if row.get('ad_object_type') == 'campaign':
                    if parent == 'missing':
                        row.pop('parent_ad_object_id', None)
                    else:
                        row['parent_ad_object_id'] = parent
                return row
            self.client.get = root_parent
            with self.subTest(parent=parent):
                result = await self.launch()
                self.assertTrue(result['relationships_confirmed'])
                self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(len(self.client.posts), 4)

    async def test_rejected_final_fragment_can_be_inspected_without_success_result_or_post_replay(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get
        async def rejected(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad':
                row.update(validation_status='HAS_ERRORS', active_errors=[{'error_code':1815857,'field':'bid_amount'}])
            return row
        self.client.get = rejected
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        self.payload['reconcile_only'] = True
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No replay'))) as post, \
                patch('app.static_payment_read.inspect_methods', new=AsyncMock(side_effect=AssertionError('No billing'))) as payment:
            result = await self.launch()
        post.assert_not_called(); payment.assert_not_called()
        self.assertEqual([row['validation_status'] for row in result['fragment_reads']],
                         ['VALIDATED', 'VALIDATED', 'HAS_ERRORS'])
        self.assertFalse(result['meta_validation_complete'])
        self.assertTrue(result['meta_validation_errors_present'])
        self.assertFalse(result['draft_verified'])
        self.assertEqual(result['mutation_posts'], 0)
        self.assertFalse(result['editor_verified'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_unsettled_validation_cannot_be_reported_as_verified_draft(self):
        await self.saved()
        original = self.client.get

        async def pending_validation(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad_set':
                row.pop('active_errors', None)
            return row

        self.client.get = pending_validation
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
            report = await self.launch()
        post.assert_not_awaited()
        self.assertTrue(report['fragment_storage_verified'])
        self.assertFalse(report['meta_validation_complete'])
        self.assertFalse(report['meta_validation_errors_present'])
        self.assertFalse(report['draft_verified'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_lost_draft_reply_returns_local_journal_diagnostic_without_meta_reads(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original_post = self.client.post
        async def unknown(path, params, *, before_submit, files):
            if path.endswith('/addrafts'):
                await before_submit()
                raise TimeoutError('synthetic lost reply')
            return await original_post(path, params, before_submit=before_submit, files=files)
        self.client.post = unknown
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.post = original_post
        old = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        self.payload['reconcile_only'] = True
        with patch.object(self.session, 'facebook_web',
                          new=AsyncMock(side_effect=AssertionError('No Meta session allowed'))) as session, \
                patch.object(self.client, 'post',
                             new=AsyncMock(side_effect=AssertionError('No POST allowed'))) as post:
            result = await self.launch()
        session.assert_not_awaited()
        post.assert_not_awaited()
        self.assertEqual(result['status'], 'RECONCILE_REQUIRED')
        self.assertIsNone(result['draft_id'])
        self.assertEqual(result['pending_stage'], {'stage': 'DRAFT', 'reason': 'UNKNOWN_REMOTE_RESULT'})
        self.assertEqual(result['fragment_reads'], [])
        self.assertEqual(result['uninspected_levels'], ['campaign', 'ad_set', 'ad'])
        self.assertFalse(result['fragment_storage_verified'])
        self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), old)

    async def _assert_lost_fragment_is_inspectable(self, stage_index):
        operation = ('FRAGMENT_CAMPAIGN', 'FRAGMENT_AD_SET', 'FRAGMENT_AD')[stage_index]
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original_post = self.client.post
        async def unknown(path, params, *, before_submit, files):
            if path.endswith('/addraft_fragments') and len(self.client.fragments) == stage_index:
                await before_submit()
                raise TimeoutError('synthetic lost fragment reply')
            return await original_post(path, params, before_submit=before_submit, files=files)
        self.client.post = unknown
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.post = original_post
        self.payload['reconcile_only'] = True
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        before_posts = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('No POST allowed'))) as post:
            result = await self.launch()
        post.assert_not_called()
        self.assertEqual(result['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(result['pending_stage'],
                         {'stage': operation, 'reason': 'UNKNOWN_REMOTE_RESULT'})
        self.assertEqual(len(result['fragment_reads']), stage_index)
        self.assertEqual(result['uninspected_levels'], list(('campaign', 'ad_set', 'ad')[stage_index:]))
        self.assertEqual(result['mutation_posts'], 0)
        self.assertFalse(result['fragment_storage_verified'])
        self.assertEqual(len(self.client.posts), before_posts)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_lost_campaign_fragment_is_inspectable(self):
        await self._assert_lost_fragment_is_inspectable(0)

    async def test_lost_adset_fragment_is_inspectable(self):
        await self._assert_lost_fragment_is_inspectable(1)

    async def test_lost_ad_fragment_is_inspectable(self):
        await self._assert_lost_fragment_is_inspectable(2)

    async def test_partial_draft_after_read_error_marks_next_stage_not_submitted(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original_get = self.client.get
        async def interrupted(path, params):
            if path == '/addraft_555555':
                from app.private_ads_draft import fail
                raise fail('READ_PENDING', 'synthetic independent read interrupted', True)
            return await original_get(path, params)
        self.client.get = interrupted
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.get = original_get
        self.payload['reconcile_only'] = True
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(result['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(result['draft_id'], '555555')
        self.assertEqual(result['pending_stage'], {'stage': 'FRAGMENT_CAMPAIGN', 'reason': 'NOT_SUBMITTED'})
        self.assertEqual(result['fragment_reads'], [])
        self.assertFalse(result['fragment_storage_verified'])
        self.assertTrue(result['relationships_confirmed'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_interrupted_draft_can_be_inspected_then_resumed_to_three_paused_levels(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        targeting = {'geo_locations': {'countries': ['UA']}, 'age_min': 23, 'age_max': 33,
                     'locales': [52],
                     'interests': [{'id': '6003139266461', 'name': 'Physical fitness'}],
                     'behaviors': [{'id': '6015548561283', 'name': 'Chrome'}]}
        self.payload['launch']['adset']['targeting'] = targeting
        original_get = self.client.get
        async def interrupted(path, params):
            if path == '/addraftfragment_222222':
                from app.private_ads_draft import fail
                raise fail('READ_PENDING', 'synthetic interrupted Ad Set verification', True)
            return await original_get(path, params)
        self.client.get = interrupted
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.client.get = original_get
        self.assertEqual(len(self.client.posts), 3)  # draft, campaign, ad set
        self.payload['reconcile_only'] = True
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            report = await self.launch()
        post.assert_not_called()
        self.assertEqual(report['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(report['pending_stage'], {'stage': 'FRAGMENT_AD', 'reason': 'NOT_SUBMITTED'})
        self.assertEqual([row['level'] for row in report['fragment_reads']], ['campaign', 'ad_set'])
        self.assertEqual(report['fragment_reads'][1]['submitted_values']['targeting'], targeting)
        self.assertEqual(report['fragment_reads'][1]['fresh_values']['targeting'], targeting)
        self.payload.pop('reconcile_only')
        final = await self.launch()
        repeated = await self.launch()
        self.assertTrue(final['draft_verified'])
        self.assertEqual(final['draft_id'], '555555')
        self.assertEqual(len(final['fragment_ids']), 3)
        self.assertEqual(final['fragment_ids'], repeated['fragment_ids'])
        self.assertEqual(len(self.client.posts), 4)  # exactly one create per level
        self.assertFalse(final['published'])
        self.assertFalse(final['editor_verified'])

    async def test_unavailable_object_read_records_rejection_and_marks_level_unverified(self):
        from app.private_ads_http import AdsManagerResponseError
        await self.saved()
        original = self.client.get
        async def unavailable(path, params):
            if path == '/777772':
                raise AdsManagerResponseError({'code':100,'error_subcode':33,'http_status':200})
            return await original(path,params)
        self.client.get = unavailable
        result = await self.launch()
        row = next(row for row in result['object_reads'] if row['level'] == 'ad_set')
        self.assertFalse(row['available'])
        self.assertEqual(row['diagnostic']['error_subcode'],33)
        self.assertEqual(result['unverified_object_levels'], ['ad_set'])
        self.assertNotIn('missing_direct_objects', result)
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts),4)

    async def test_exact_account_list_rows_confirm_saved_ids_after_direct_100_33_without_post(self):
        from app.private_ads_http import AdsManagerResponseError
        await self.saved()
        original = self.client.get
        ids = {'campaign': '777771', 'ad_set': '777772', 'ad': '777773'}
        edges = {'campaign': 'campaigns', 'ad_set': 'adsets', 'ad': 'ads'}

        def values_for(level):
            fragment = next(row for row in self.client.fragments.values()
                            if row['ad_object_type'] == level)
            return {row['field']: copy.deepcopy(row['new_value'])
                    for row in fragment['values']}

        async def exact_list_read(path, params):
            if path in {'/777771', '/777772', '/777773'}:
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            for level, edge in edges.items():
                if path == '/act_111111/' + edge:
                    values = values_for(level)
                    if level == 'ad':
                        self.expected_creative = values.pop('creative')
                        values['creative'] = {'id': '777774'}
                    return {'data': [{'id': ids[level], 'account_id': '111111', **values}]}
            if path == '/777774':
                return {'id': '777774', 'account_id': '111111',
                    **copy.deepcopy(self.expected_creative)}
            return await original(path, params)

        self.client.get = exact_list_read
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(result['unverified_object_levels'], [])
        self.assertEqual([row['level'] for row in result['object_reads']],
                         ['campaign', 'ad_set', 'ad', 'creative'])
        self.assertTrue(all(row['available'] and row['settings_match']
                            for row in result['object_reads']))
        self.assertTrue(all(row.get('identity_fallback_verified')
                            for row in result['object_reads']))
        self.assertEqual(result['object_reads'][2]['read_source'], 'account_ads_read')
        self.assertEqual(result['object_reads'][2]['direct_read_diagnostic']['error_subcode'], 33)
        self.assertEqual(result['object_reads'][3]['values'], values_for('ad')['creative'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), post_count)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_account_list_candidate_with_another_id_cannot_confirm_saved_object(self):
        from app.private_ads_http import AdsManagerResponseError
        await self.saved()
        original = self.client.get
        edges = {'/act_111111/campaigns': 'campaign',
                 '/act_111111/adsets': 'ad_set',
                 '/act_111111/ads': 'ad'}

        def values_for(level):
            fragment = next(row for row in self.client.fragments.values()
                            if row['ad_object_type'] == level)
            return {row['field']: copy.deepcopy(row['new_value'])
                    for row in fragment['values']}

        async def wrong_identity(path, params):
            if path in {'/777771', '/777772', '/777773'}:
                raise AdsManagerResponseError({
                    'http_status': 200, 'code': 100, 'error_subcode': 33,
                    'type': 'GraphMethodException',
                })
            if path in edges:
                values = values_for(edges[path])
                if edges[path] == 'ad':
                    values.pop('creative')
                    values['creative'] = {'id': '777774'}
                return {'data': [{'id': '777779', 'account_id': '111111', **values}]}
            if path == '/777774':
                self.fail('A different Ad ID must not trigger a Creative read')
            return await original(path, params)

        self.client.get = wrong_identity
        post_count = len(self.client.posts)
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('Inspection cannot POST'))) as post:
            result = await self.launch()
        post.assert_not_awaited()
        self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(result['unverified_object_levels'],
                         ['campaign', 'ad_set', 'ad', 'creative'])
        self.assertTrue(all(not row['available'] for row in result['object_reads']))
        self.assertTrue(all(row.get('identity_fallback_attempted')
                            for row in result['object_reads']))
        self.assertTrue(all(row['identity_fallback_diagnostic']['code']
                            == 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH'
                            for row in result['object_reads']))
        self.assertEqual(len(self.client.posts), post_count)
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])

    async def test_direct_object_foreign_account_fails_closed(self):
        await self.saved()
        original = self.client.get
        async def foreign(path,params):
            row = await original(path,params)
            if path == '/777771': row['account_id'] = '888888'
            return row
        self.client.get = foreign
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(len(self.client.posts),4)

    async def test_direct_read_session_rejection_stops_later_reads(self):
        from app.private_ads_http import AdsManagerResponseError
        await self.saved()
        original, calls = self.client.get, []
        async def expired(path,params):
            calls.append(path)
            if path == '/777771':
                raise AdsManagerResponseError({'code':190,'http_status':200})
            return await original(path,params)
        self.client.get = expired
        with self.assertRaises(AdsManagerResponseError):
            await self.launch()
        self.assertNotIn('/777772',calls)
        self.assertEqual(len(self.client.posts),4)

    async def test_invalid_scope_actor_configuration_or_missing_key_fails_before_open(self):
        await self.saved()
        original = copy.deepcopy(self.payload)
        for change in ({'launch_key':'unknown'}, {'page_id':'555556'}, {'business_id':'222223'},
                       {'ad_account_id':'111112'}, {'reconcile_only':'true'}, {'reconcile_only':False}):
            self.payload = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), patch('app.private_ads_inspect.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
                with self.assertRaises(ProvisioningError):
                    await self.launch()
                opened.assert_not_called()
        self.payload = original
        self.context.cookies['c_user'] = '123457'
        with patch('app.private_ads_inspect.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
            with self.assertRaises(ProvisioningError):
                await self.launch()
            opened.assert_not_called()

    async def test_historical_draft_inspection_is_read_only_and_cannot_enable_old_mutations(self):
        from app.private_ads_inspect import READ_ONLY_DRAFT_PREDECESSORS
        current = self.owner.ads_manager.source_digest
        self.owner.ads_manager.source_digest = next(iter(READ_ONLY_DRAFT_PREDECESSORS))
        await self.saved()
        self.owner.ads_manager.source_digest = current
        result = await self.launch()
        self.assertEqual(result['recovered_source_digest'], next(iter(READ_ONLY_DRAFT_PREDECESSORS)))
        self.payload.pop('reconcile_only')
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 4)

    async def test_controlled_1359188_predecessor_keeps_original_objective_and_never_replays(self):
        predecessor = '9b2b414e40aa4d6a1d69dfdfefee07341a45fa18b4328a72f185e63c28d00c44'
        current = self.owner.ads_manager.source_digest
        self.owner.ads_manager.source_digest = predecessor
        with patch.object(self.owner.ads_manager.objective_wire, 'campaign', side_effect=copy.deepcopy):
            await self.saved()
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        self.owner.ads_manager.source_digest = current
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No mutation'))) as post:
            result = await self.launch()
            self.assertEqual(result['recovered_source_digest'], predecessor)
            campaign = result['fragment_reads'][0]['submitted_values']
            self.assertEqual(campaign['objective'], 'OUTCOME_TRAFFIC')
            self.assertNotIn('is_odax_campaign_group', campaign)
            self.assertTrue(campaign['is_autobid'])
            self.assertFalse(result['editor_verified'])
            self.assertEqual(result['mutation_posts'], 0)
            self.assertEqual(await self.owner.state.all_steps('image-launch'), before)
            self.payload.pop('reconcile_only')
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        post.assert_not_called()

    async def test_changed_or_foreign_fresh_fragment_cannot_pass(self):
        await self.saved()
        self.client.foreign = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SCOPE_MISMATCH')
        self.client.foreign = False
        self.client.fragments['222222']['values'][0]['new_value'] = 'wrong-parent'
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(len(self.client.posts), 4)

    async def test_unreviewed_historical_source_is_inspectable_but_cannot_resume_mutations(self):
        current = self.owner.ads_manager.source_digest
        historical = 'b' * 64
        self.owner.ads_manager.source_digest = historical
        await self.saved()
        self.owner.ads_manager.source_digest = current
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post',
                          new=AsyncMock(side_effect=AssertionError('No writes'))) as post:
            report = await self.launch()
            self.assertEqual(report['status'], 'SUCCESS')
            self.assertEqual(report['recovered_source_digest'], historical)
            self.assertFalse(report['source_reviewed_for_mutation'])
            self.payload.pop('reconcile_only')
            with self.assertRaises(ProvisioningError) as rejected:
                await self.launch()
            self.assertEqual(rejected.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        post.assert_not_called()
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_inspection_rejects_empty_error_envelopes_and_secondary_account_mismatch(self):
        await self.saved()
        original = self.client.get
        scenarios = [
            ('/act_111111', {'error': {}}),
            ('/act_111111', {'errors': []}),
            ('/act_111111', {'account_id': '999999'}),
            ('/addraft_555555', {'error': None}),
            ('/addraftfragment_111111', {'errors': []}),
        ]
        for path, fields in scenarios:
            async def tampered(route, params):
                row = await original(route, params)
                return {**row, **fields} if route == path else row
            self.client.get = tampered
            with self.subTest(path=path, fields=fields), \
                    patch.object(self.client, 'post',
                                 new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
                with self.assertRaises(ProvisioningError):
                    await self.launch()
                post.assert_not_called()
        self.client.get = original

    async def test_meta_validation_error_is_recorded_without_claiming_editability(self):
        await self.saved()
        self.client.fragments['222222']['active_errors'] = [{'code':100}]
        self.client.fragments['222222']['validation_status'] = 'unrecognized-status'
        result = await self.launch()
        self.assertTrue(result['fragment_reads'][1]['active_errors_present'])
        self.assertEqual(result['fragment_reads'][1]['active_error_diagnostic']['codes'], [{'field':'code','value':100}])
        self.assertTrue(result['fragment_reads'][1]['active_errors_observed'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), 4)

    async def test_changed_ad_identity_cannot_replace_previously_confirmed_object(self):
        await self.saved()
        self.client.fragments['333333']['ad_object_id'] = '777779'
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 4)


class ValidationDiagnosticTests(unittest.TestCase):
    def test_explicit_field_path_in_error_text_is_extracted_without_raw_text(self):
        from app.private_ads_inspect import validation_diagnostic
        result = validation_diagnostic({
            'error_code': 1359188,
            'error_user_msg': 'Invalid parameter \'creative.object_story_spec.link_data.image_hash\'. See example.com.',
        }, observed=True)
        self.assertEqual(result['reported_field_paths'], [
            'creative.object_story_spec.link_data.image_hash',
        ])
        self.assertNotIn('Invalid parameter', repr(result))
        self.assertNotIn('example.com', repr(result))

    def test_meta_reported_unknown_field_paths_survive_catalog_filter_without_raw_text(self):
        from app.private_ads_inspect import validation_diagnostic
        result = validation_diagnostic({
            'error_code': 1359188,
            'message': 'No payment method. raw response text must not be retained.',
            'blame_field_specs': [['legacy_ad_setting']],
            'param': 'creative.object_story_spec.link_data.legacy_mode',
            'field_name': 'unknown_server_field',
            'credentials': {'field': 'private_token'},
            'cookie': {'field': 'secret_cookie'},
        }, observed=True)
        self.assertEqual(result['reported_field_paths'], [
            'creative.object_story_spec.link_data.legacy_mode',
            'legacy_ad_setting',
            'unknown_server_field',
        ])
        self.assertEqual(result['field_names'], [])
        self.assertNotIn('raw response text', repr(result))
        self.assertNotIn('private_token', repr(result))
        self.assertNotIn('secret_cookie', repr(result))

    def test_explicit_paths_are_extracted_from_any_error_text_value_without_raw_text(self):
        from app.private_ads_inspect import validation_diagnostic
        source = [{
            'error_code': 1359188,
            'summary': 'Invalid parameter `creative.object_story_spec.link_data.call_to_action`',
            'details': 'Private text that must not be retained: secret_cookie=DO_NOT_EXPOSE',
        }]
        result = validation_diagnostic(source, observed=True)
        self.assertEqual(result['codes'], [{'field': 'error_code', 'value': 1359188}])
        self.assertEqual(result['reported_field_paths'], [
            'creative.object_story_spec.link_data.call_to_action',
        ])
        self.assertNotIn('Invalid parameter', repr(result))
        self.assertNotIn('DO_NOT_EXPOSE', repr(result))

    def test_nested_and_encoded_codes_keep_reviewed_fields_without_raw_messages(self):
        from app.private_ads_inspect import validation_diagnostic
        source = {'data': [{'error': {'error_code': '1815706', 'error_subcode': 100,
            'message': 'Unsupported campaign objective for optimization_goal. access_token=DO_NOT_EXPOSE; https://private.invalid/?secret=DO_NOT_EXPOSE',
            'blame_field_specs': [['optimization_goal']]}}]}
        import json
        result = validation_diagnostic(json.dumps(source), observed=True)
        self.assertEqual(result['codes'], [{'field':'error_code','value':1815706}, {'field':'error_subcode','value':100}])
        self.assertIn('objective', result['terms'])
        self.assertIn('optimization_goal', result['field_names'])
        self.assertIn('optimization_goal', result['reported_field_paths'])
        self.assertNotIn('DO_NOT_EXPOSE', repr(result))
        self.assertNotIn('private.invalid', repr(result))

    def test_payment_terms_are_evidence_only_without_exporting_card_or_credentials(self):
        from app.private_ads_inspect import validation_diagnostic
        result = validation_diagnostic({'error_code':987654321,
            'message':'Billing: payment method card funding confirmation required. Card 4111111111111111; private-person@example.invalid; access_token=DO_NOT_EXPOSE',
            'credentials': {'message':'secret payment method', 'code':1234},
            'cookie': 'session=DO_NOT_EXPOSE'}, observed=True)
        self.assertFalse(result['exact_cause_confirmed'])
        self.assertTrue({'billing','payment','method','card','funding','confirmation','required'} <= set(result['terms']))
        self.assertEqual(result['codes'], [{'field':'error_code','value':987654321}])
        self.assertEqual(result['field_names'], [])
        for private in ('4111111111111111', 'private-person', 'example.invalid', 'DO_NOT_EXPOSE', '1234'):
            self.assertNotIn(private, repr(result))

    def test_multiple_errors_keep_codes_and_ordered_safe_terms_separate(self):
        from app.private_ads_inspect import validation_diagnostic
        value = [
            {'error_code':987654321,
             'message':'Payment method is not required. access_token=DO_NOT_EXPOSE'},
            {'error_subcode':17,
             'message':'Unsupported objective for optimization_goal: discontinued.'},
        ]
        result = validation_diagnostic(value, observed=True)
        self.assertFalse(result['exact_cause_confirmed'])
        self.assertEqual(result['records'], [
            {'index':0,'codes':[{'field':'error_code','value':987654321}],
             'field_names':[],'reported_field_paths':[],'matched_terms_in_order':['payment','method','not','required'],
             'truncated':False},
            {'index':1,'codes':[{'field':'error_subcode','value':17}],
             'field_names':['objective','optimization_goal'],'reported_field_paths':[],
             'matched_terms_in_order':['unsupported','objective','discontinued'],
             'truncated':False},
        ])
        for private in ('access_token', 'DO_NOT_EXPOSE', 'Payment method is not required'):
            self.assertNotIn(private, repr(result))
        flat = validation_diagnostic(['Payment method isn\'t required', 'Unsupported objective'], observed=True)
        self.assertEqual([row['matched_terms_in_order'] for row in flat['records']], [
            ['payment','method',"isn't",'required'], ['unsupported','objective']])

    def test_encoded_error_wrappers_are_split_into_error_records(self):
        from app.private_ads_inspect import validation_diagnostic
        import json
        value = json.dumps({'data':[{'error':{'error_code':987654321,
            'message':'Payment method not required'}},
            {'error':{'error_code':1815706,'message':'Unsupported objective'}}]})
        result = validation_diagnostic(value, observed=True)
        self.assertFalse(result['exact_cause_confirmed'])
        self.assertEqual([row['codes'] for row in result['records']], [
            [{'field':'error_code','value':987654321}],
            [{'field':'error_code','value':1815706}],
        ])
        self.assertEqual([row['matched_terms_in_order'] for row in result['records']], [
            ['payment','method','not','required'], ['unsupported','objective']])

    def test_credentials_and_boolean_or_unbounded_codes_are_never_exported(self):
        from app.private_ads_inspect import validation_diagnostic
        value = {'credentials': {'code': 77}, 'access_token': {'code': 88},
            'errors':[{'code':True}, {'code':999999999999999999}, {'code':'not-a-code'}]}
        self.assertEqual(validation_diagnostic(value, observed=True)['codes'], [])

    def test_absent_and_explicit_empty_validation_are_distinct(self):
        from app.private_ads_inspect import validation_diagnostic
        missing = validation_diagnostic(None, observed=False)
        empty = validation_diagnostic([], observed=True)
        self.assertFalse(missing['observed'])
        self.assertTrue(empty['observed'])
        self.assertFalse(empty['has_errors'])

    def test_oversized_or_deep_diagnostic_is_bounded(self):
        from app.private_ads_inspect import validation_diagnostic
        result = validation_diagnostic([{'message':'objective ' * 3000}] * 1000, observed=True)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['records']), 8)
        self.assertTrue(all(len(row['matched_terms_in_order']) <= 24 for row in result['records']))
        self.assertLess(len(repr(result)), 5000)
