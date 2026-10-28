import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.private_draft_read_diagnostic import read_diagnostic, scalar_shape, values_diagnostic
from app.provisioning.models import ProvisioningError


class DraftReadDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_saved_handles_are_read_without_post_or_journal_changes(self):
        target = {'ad_account_id':'111111', 'business_id':'222222'}
        values = {'name':'fixture campaign', 'status':'PAUSED'}
        intent = (target, 'saved-key', 'fingerprint', '333333', 'source', '444444',
                  [('campaign', '555555', values, None)], {'stage':'FRAGMENT_AD_SET'})
        client = SimpleNamespace(read_only=False, context=SimpleNamespace(account_id='111111'),
            post=AsyncMock(side_effect=AssertionError('No POST')),
            get=AsyncMock(side_effect=[
                {'id':'444444', 'account_id':'111111', 'author_id':'333333', 'ownership_type':'USER'},
                {'id':'555555', 'account_id':'111111', 'ad_draft_id':'444444',
                 'ad_object_id':'666666', 'ad_object_type':'campaign', 'parent_ad_object_id':'0',
                 'values':[{'field':key,'new_value':value} for key,value in values.items()],
                 'validation_status':'VALIDATED', 'active_errors':[]}]))
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=object()))
        open_client = AsyncMock(return_value=client)
        with patch('app.private_draft_read_diagnostic.saved_draft_intent', new=AsyncMock(return_value=intent)), \
                patch('app.private_draft_read_diagnostic.AdsManagerPrivateHttp.open', new=open_client):
            result = await read_diagnostic(object(), profile_id='4', context=object(), session=session, payload={})
        self.assertEqual([call.args[0] for call in client.get.await_args_list],
                         ['/addraft_444444','/addraftfragment_555555'])
        client.post.assert_not_awaited()
        self.assertTrue(client.read_only)
        self.assertTrue(open_client.await_args.kwargs['reference_read'])
        self.assertFalse(result['draft_verified'])
        self.assertFalse(result['persisted'])
        self.assertEqual(result['advertising_mutation_posts'], 0)
        read = result['fragment_reads'][0]
        self.assertEqual(read['checks'], {'id':True,'ad_draft_id':True,'ad_object_type':True,'parent_ad_object_id':False})
        self.assertEqual(read['observed']['parent_ad_object_id']['value'], '0')
        self.assertTrue(read['settings_match'])

    async def test_invalid_saved_binding_stops_before_opening_a_session(self):
        session = SimpleNamespace(facebook_web=AsyncMock())
        with patch('app.private_draft_read_diagnostic.saved_draft_intent',
                new=AsyncMock(side_effect=ProvisioningError('SAVED_OPERATION_MISMATCH','fixed'))), \
                patch('app.private_draft_read_diagnostic.AdsManagerPrivateHttp.open', new=AsyncMock()) as open_client:
            with self.assertRaises(ProvisioningError):
                await read_diagnostic(object(), profile_id='4', context=object(), session=session, payload={})
        session.facebook_web.assert_not_awaited()
        open_client.assert_not_awaited()

    def test_arbitrary_strings_and_nested_data_are_not_exposed(self):
        for value in ('credential fixture', {'token':'fixture'}, ['fixture'], 'https://example.com/?token=fixture'):
            self.assertNotIn('value', scalar_shape(value))
        self.assertEqual(scalar_shape('addraft_444444')['value'], 'addraft_444444')

    def test_field_diagnostic_distinguishes_changed_missing_and_encoded_values(self):
        rows = [{'field':'objective', 'new_value':'"LINK_CLICKS"'},
                {'field':'status', 'new_value':'ACTIVE'},
                {'field':'is_odax_campaign_group', 'new_value':False}]
        result = values_diagnostic(rows, {'objective':'LINK_CLICKS', 'status':'PAUSED',
            'buying_type':'AUCTION'}, '222222')
        fields = {row['field']: row for row in result['fields']}
        self.assertTrue(fields['objective']['matches'])
        self.assertEqual(fields['objective']['json_decoded']['value'], 'LINK_CLICKS')
        self.assertFalse(fields['status']['matches'])
        self.assertEqual(fields['status']['observed']['value'], 'ACTIVE')
        self.assertEqual(fields['buying_type']['count'], 0)
        self.assertEqual(result['unexpected_row_count'], 1)

    def test_field_diagnostic_never_exposes_text_nested_credentials_or_unknown_names(self):
        import json
        secret = 'private-ad-text-and-credential-fixture'
        rows = [{'field':'name', 'new_value':secret},
                {'field':'creative', 'new_value':json.dumps({'password':secret})},
                {'field':secret, 'new_value':secret}, {'field':[]}]
        result = values_diagnostic(rows, {'name':secret, 'creative':{'password':secret}}, '222222')
        encoded = json.dumps(result)
        self.assertNotIn(secret, encoded)
        self.assertNotIn('password', encoded)
        self.assertEqual(result['unexpected_row_count'], 2)
        self.assertEqual(result['unexpected_reviewed_fields'], [])


class DraftReadApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_uses_exact_original_task_payload_and_saved_profile(self):
        import httpx
        import main as api
        original = {'launch_key':'original-key','business_id':'222222'}
        other = {'launch_key':'different-rk'}
        view = {'status':'FAILED','items':[{'profile_id':'4','tasks':[
            {'id':'b'*32,'action':'private_launch','payload':original},
            {'id':'c'*32,'action':'private_launch','payload':other}]}]}
        context, session = object(), object()
        session_scope = SimpleNamespace(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        class Scope:
            async def __aenter__(self):
                return await session_scope.__aenter__()
            async def __aexit__(self, *args):
                return await session_scope.__aexit__(*args)
        with patch.object(api,'API_KEY','fixture-key'), \
                patch.object(api.store,'job_view',new=AsyncMock(return_value=view)), \
                patch.object(api,'_resolve_read_profile',new=AsyncMock(return_value=context)) as resolve, \
                patch('app.session.ProfileSession',return_value=Scope()) as profile_session, \
                patch('app.private_draft_read_diagnostic.read_diagnostic',new=AsyncMock(return_value={'persisted':False})) as read, \
                patch.object(api.store,'create_job',new=AsyncMock()) as create:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-key'}) as client:
                response=await client.post('/api/v1/profiles/4/private-draft-read',
                    json={'job_id':'a'*32,'task_id':'b'*32})
        self.assertEqual(response.status_code,200)
        resolve.assert_awaited_once_with('4')
        profile_session.assert_called_once_with(context)
        self.assertIs(read.await_args.kwargs['payload'], original)
        self.assertIs(read.await_args.kwargs['session'], session)
        create.assert_not_awaited()

    async def test_multiple_profile_launch_tasks_require_an_exact_task_id(self):
        import httpx
        import main as api
        view = {'status':'FAILED','items':[{'profile_id':'4','tasks':[
            {'id':'b'*32,'action':'private_launch','payload':{}},
            {'id':'c'*32,'action':'private_launch','payload':{}}]}]}
        with patch.object(api,'API_KEY','fixture-key'), \
                patch.object(api.store,'job_view',new=AsyncMock(return_value=view)), \
                patch.object(api,'_resolve_read_profile',new=AsyncMock()) as resolve, \
                patch.object(api.store,'create_job',new=AsyncMock()) as create:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-key'}) as client:
                response=await client.post('/api/v1/profiles/4/private-draft-read',json={'job_id':'a'*32})
        self.assertEqual(response.status_code,409)
        resolve.assert_not_awaited()
        create.assert_not_awaited()

    async def test_api_key_required_before_job_or_context_lookup(self):
        import httpx
        import main as api
        with patch.object(api, 'API_KEY', 'fixture-key'), patch.object(api.store,'job_view',new=AsyncMock()) as view:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://fixture') as client:
                response = await client.post('/api/v1/profiles/4/private-draft-read',json={'job_id':'a'*32})
        self.assertEqual(response.status_code,401)
        view.assert_not_awaited()

    async def test_running_job_cannot_be_read_concurrently_or_changed(self):
        import httpx
        import main as api
        with patch.object(api,'API_KEY','fixture-key'), \
                patch.object(api.store,'job_view',new=AsyncMock(return_value={'status':'RUNNING'})), \
                patch.object(api,'_resolve_read_profile',new=AsyncMock()) as resolve, \
                patch.object(api.store,'create_job',new=AsyncMock()) as create:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://fixture',
                    headers={'X-Remask-Worker-Key':'fixture-key'}) as client:
                response=await client.post('/api/v1/profiles/4/private-draft-read',json={'job_id':'a'*32})
        self.assertEqual(response.status_code,409)
        resolve.assert_not_awaited()
        create.assert_not_awaited()
