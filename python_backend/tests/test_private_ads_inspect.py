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
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(result['fragment_reads'][0]['submitted_values']['objective'], 'OUTCOME_TRAFFIC')
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_exact_native_objects_and_creative_are_read_separately_from_fragments(self):
        await self.saved()
        result = await self.launch()
        self.assertEqual([row['level'] for row in result['object_reads']], ['campaign','ad_set','ad','creative'])
        self.assertTrue(all(row['available'] and row['settings_match'] for row in result['object_reads']))
        self.assertEqual(result['missing_direct_objects'], [])
        self.assertTrue(result['launch_fingerprint_verified'])
        self.assertTrue(result['actor_verified'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), 4)

    async def test_unavailable_object_read_records_exact_rejection_without_claiming_absence(self):
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
        self.assertEqual(result['missing_direct_objects'], ['ad_set'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts),4)

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
        self.assertNotIn('DO_NOT_EXPOSE', repr(result))
        self.assertNotIn('private.invalid', repr(result))

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
        self.assertLess(len(repr(result)), 1000)
