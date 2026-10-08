import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.business_fan_page_contracts import MANIFEST, command, execute
from app.contract_maintenance.update_business_pages import compile_candidate
from app.private_business_fan_page_create import create_business_page, read_pages
from app.provisioning.business_pages import BusinessPageStore, ensure_business_page
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.prepare import PrepareService
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore
from tests.test_business_pages import TwoBundleMeta, UID, BM1, BM2, FP1, FP2, RK1

OPS = {op: command(op, business=BM2, name='PrgssTeam', category='Digital creator',
    categories=['242822000000000'], bio='', join='fixture', cursor=None)['friendly_name']
    for op in ('CREATE_FP', 'READ_FP', 'FP_PRECHECK', 'FP_CATEGORY')}


class BusinessPageContractTests(unittest.TestCase):
    def test_public_artifacts_and_sender_compile_pinned_ids_and_schema(self):
        candidate = compile_candidate(Path(__file__).with_name('fixtures') / 'meta_business_page_observed_20261008.js')
        pinned = json.loads(MANIFEST.read_text())
        for op, row in pinned['operations'].items():
            self.assertEqual(candidate['operations'][op]['doc_id'], row['doc_id'])
            self.assertEqual(candidate['operations'][op]['variables'], row['variables'])

    def test_offline_update_rejects_nested_input_drift_and_ambiguous_version(self):
        source = (Path(__file__).with_name('fixtures') / 'meta_business_page_observed_20261008.js').read_text()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'changed.js'
            for changed in (source.replace('business_id:E,categories:s', 'renamed_business:E,categories:s'),
                    source.replace('params:{search_string:e}', 'params:{changed_search:e}'),
                    source + '\n__d("BizKitSettingsCreateAdditionalProfilePlusMutation_facebookRelayOperation",[],function(a,b,c,d,e){e.exports="9999999999999"});'):
                target.write_text(changed)
                with self.assertRaises(ValueError):
                    compile_candidate(target)


class BusinessPageHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / 'state.sqlite'))
        await self.state.init()
        self.meta = TwoBundleMeta()
        self.context = SimpleNamespace(profile_id='15', cookies={'c_user': UID}, pages=[], businesses=[], ad_accounts=[])
        self.session = SimpleNamespace(context=self.context, private_only=True,
            facebook_web=AsyncMock(return_value=self.meta), close_business_browser=AsyncMock(),
            facebook_business_browser=AsyncMock(side_effect=AssertionError('Chromium forbidden')))
        self.params = {'names': ['PrgssTeam'], 'business_id': BM2, 'category': 'Digital creator',
            'policies_accepted': True, 'reserved_page_ids': [FP1], 'foreign_page_ids': [FP1]}
        await self.state.set_running('create', '15', 'page', ProvisioningStep.FAN_PAGES)
        self.sleep = patch('app.private_business_fan_page_create.asyncio.sleep', AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def run_page(self):
        checkpoint = (await self.state.step('create', ProvisioningStep.FAN_PAGES)).get('result') or {}
        return await create_business_page(self.session, self.params, state=self.state,
            item_id='create', profile_id='15', scope_key='page', checkpoint=checkpoint)

    def intercept(self, operation, response):
        original = self.meta.graphql
        async def wrapped(doc, variables, **kwargs):
            if kwargs['friendly_name'] == OPS[operation]:
                return copy.deepcopy(response)
            return await original(doc, variables, **kwargs)
        self.meta.graphql = wrapped

    async def test_uncached_create_binds_exact_business_and_commits_canonical_delegate(self):
        result = await self.run_page()
        self.assertEqual(result['page_ids'], [FP2])
        body = self.meta.posts[0][1]
        self.assertEqual(set(body), {'input'})
        self.assertEqual(set(body['input']), {'business_id', 'categories', 'creation_source', 'bio', 'name',
            'casd_bl_disclosure_acceptance_data', 'qpl_join_id'})
        self.assertEqual(body['input']['categories'], ['242822000000000'])
        self.assertEqual(body['input']['business_id'], BM2)
        self.assertNotIn('actor_id', body['input'])
        self.assertNotEqual(result['page_ids'], ['61500012345678'])
        self.assertEqual(self.meta.owners[FP1], BM1)
        self.meta.fetch_text.assert_not_awaited()
        self.session.facebook_business_browser.assert_not_awaited()
        await self.run_page()
        self.assertEqual(len(self.meta.posts), 1)

    async def test_lost_response_reconciles_exact_business_without_second_create(self):
        self.meta.lose_page = True
        self.assertEqual((await self.run_page())['page_ids'], [FP2])
        await self.run_page()
        self.assertEqual(len(self.meta.posts), 1)

    async def test_unavailable_verify_retains_submit_and_retry_ignores_new_inventory_as_baseline(self):
        self.meta.page_read_available = False
        with self.assertRaises(ProvisioningError) as exc:
            await self.run_page()
        self.assertEqual(exc.exception.code, 'FAN_PAGE_CREATE_RESULT_UNKNOWN')
        retained = (await self.state.step('create', ProvisioningStep.FAN_PAGES))['result']
        self.assertEqual(retained['response_page_id'], FP2)
        self.assertEqual(retained['active_before_ids'], [FP1])
        self.meta.page_read_available = True
        self.params['reserved_page_ids'].append(FP2)  # resolver now sees the submitted Page
        self.assertEqual((await self.run_page())['page_ids'], [FP2])
        self.assertEqual(len(self.meta.posts), 1)

    async def test_lost_response_and_unavailable_verify_retains_original_baseline(self):
        self.meta.lose_page = True
        self.meta.page_read_available = False
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.meta.page_read_available = True
        self.params['reserved_page_ids'].append(FP2)
        self.assertEqual((await self.run_page())['page_ids'], [FP2])
        self.assertEqual(len(self.meta.posts), 1)

    async def test_response_id_without_independent_owner_and_name_never_commits(self):
        from app.private_page_ownership import PAGE
        original = self.meta.graphql
        async def wrong(doc, variables, **kwargs):
            response = await original(doc, variables, **kwargs)
            if kwargs['friendly_name'] == PAGE:
                response['data']['page']['ownerBusiness']['id'] = BM1
            return response
        self.meta.graphql = wrong
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.assertEqual((await self.state.step('create', ProvisioningStep.FAN_PAGES))['result']['phase'], 'PAGE_CREATE_RESULT_UNKNOWN')
        self.assertEqual(len(self.meta.posts), 1)

    async def test_partial_mutation_response_can_complete_only_with_exact_fresh_owner_proof(self):
        original = self.meta.graphql
        async def partial(doc, variables, **kwargs):
            response = await original(doc, variables, **kwargs)
            if kwargs['friendly_name'] == OPS['CREATE_FP']:
                response['errors'] = [{'message': 'partial response'}]
            return response
        self.meta.graphql = partial
        self.assertEqual((await self.run_page())['page_ids'], [FP2])

    async def test_multiple_new_same_name_pages_cannot_reconcile_lost_response(self):
        self.meta.lose_page = True
        original = self.meta.graphql
        async def ambiguous(doc, variables, **kwargs):
            if kwargs['friendly_name'] == OPS['CREATE_FP']:
                self.meta.owners['3348798761652037'] = BM2
            return await original(doc, variables, **kwargs)
        self.meta.graphql = ambiguous
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.assertEqual(len(self.meta.posts), 1)

    async def test_category_requires_exact_name_unique_numeric_id(self):
        for nodes in ([], [{'category_id': 'bad', 'category_name': 'Digital creator'}],
                [{'category_id': '242822000000000', 'category_name': 'Other category'}],
                [{'category_id': value, 'category_name': 'Digital creator'} for value in ('242822000000000', '242822111111111')]):
            with self.subTest(nodes=nodes):
                self.intercept('FP_CATEGORY', {'data': {'page_creation_category_typeahead_search': {'results': {'nodes': nodes}}}})
                with self.assertRaises(ProvisioningError) as exc:
                    await self.run_page()
                self.assertEqual(exc.exception.code, 'PRIVATE_FAN_PAGE_CATEGORY_UNCONFIRMED')
                self.assertEqual(self.meta.posts, [])

    async def test_disclosure_required_or_missing_gate_does_not_submit(self):
        for gate in ({'passes_gk': True}, {}, None):
            self.intercept('FP_PRECHECK', {'data': {'business': {'id': BM2, 'showPageClaimBlockingDisclosures': gate}}})
            with self.assertRaises(ProvisioningError):
                await self.run_page()
            self.assertEqual(self.meta.posts, [])

    async def test_wrong_actor_or_business_inventory_never_submits(self):
        self.meta.bootstrap.return_value.actor_id = '61500000000000'
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.assertEqual(self.meta.calls, [])
        self.meta.bootstrap.return_value.actor_id = UID
        self.intercept('READ_FP', {'data': {'node': {'id': BM1, 'connected_objects': {'edges': [], 'page_info': {'has_next_page': False}}}}})
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.assertEqual(self.meta.posts, [])

    async def test_partial_or_malformed_inventory_cannot_authorize_create(self):
        for connection in ({'edges': [], 'page_info': {}}, {'edges': [{'node': {'assetType': 'AD_ACCOUNT', 'assetID': RK1}}], 'page_info': {'has_next_page': False}}):
            self.intercept('READ_FP', {'data': {'node': {'id': BM2, 'connected_objects': connection}}})
            with self.assertRaises(ProvisioningError):
                await self.run_page()
            self.assertEqual(self.meta.posts, [])

    async def test_exact_existing_owned_page_is_reused_without_category_or_create(self):
        self.meta.owners[FP2] = BM2
        self.params['reserved_page_ids'].append(FP2)
        self.assertTrue((await self.run_page())['pages'][0]['reused'])
        self.assertEqual(self.meta.posts, [])
        self.assertNotIn(OPS['FP_CATEGORY'], [name for name, _ in self.meta.calls])

    async def test_failed_durable_intent_prevents_post(self):
        original = self.state.checkpoint
        async def failed(*args, **kwargs):
            if args[-1].get('phase') == 'PAGE_CREATE_CLICK_INTENT':
                raise OSError('intent storage unavailable')
            return await original(*args, **kwargs)
        with patch.object(self.state, 'checkpoint', side_effect=failed):
            with self.assertRaises(ProvisioningError):
                await self.run_page()
        self.assertEqual(self.meta.posts, [])

    async def test_target_change_of_retained_submit_is_rejected_before_new_post(self):
        self.meta.page_read_available = False
        with self.assertRaises(ProvisioningError):
            await self.run_page()
        self.params['business_id'] = BM1
        with self.assertRaises(ProvisioningError) as exc:
            await self.run_page()
        self.assertEqual(exc.exception.code, 'FAN_PAGES_CHECKPOINT_MISMATCH')
        self.assertEqual(len(self.meta.posts), 1)

    async def test_paginated_inventory_keeps_exact_cursor_and_does_not_treat_first_page_as_complete(self):
        calls = []
        async def pages(doc, variables, **kwargs):
            calls.append(copy.deepcopy(variables))
            cursor = variables['cursor']
            return {'data': {'node': {'id': BM2, 'connected_objects': {'edges': [],
                'page_info': {'has_next_page': cursor is None, 'end_cursor': 'next' if cursor is None else None}}}}}
        self.meta.graphql = pages
        self.assertEqual(await read_pages(self.meta, BM2), ([], True))
        self.assertEqual([row['cursor'] for row in calls], [None, 'next'])
        self.assertTrue(all(row['id'] == row['businessID'] == BM2 and row['assetTypes'] == ['PAGE'] for row in calls))

    async def test_mutation_requires_durable_intent(self):
        with self.assertRaises(ProvisioningError):
            await execute(self.meta, 'CREATE_FP', business=BM2, name='PrgssTeam', bio='',
                categories=['242822000000000'], join='fixture')
        self.assertEqual(self.meta.posts, [])

    async def test_new_private_profile_defers_first_page_until_business_and_commits_ready(self):
        self.meta.owners = {}
        self.meta.tasks[FP2] = []
        for step, result in ((ProvisioningStep.BUSINESS, {'business_id': BM1}),
                (ProvisioningStep.AD_ACCOUNT, {'business_id': BM1, 'ad_account_id': RK1, 'account_name': 'RK'})):
            await self.state.complete('seed-' + step.value, '15', BM1, step, result)
        await self.state.set_payment_link_state('15', RK1, True, source='fixture')
        service = PrepareService(self.state, ProvisioningService(self.state))
        with patch('app.provisioning.private_create_handlers._rk_inventory', AsyncMock(return_value={'id': RK1, 'asset_ui_id': RK1})), \
                patch('app.provisioning.service._await_profile_mutation_cooldown', AsyncMock()), \
                patch('app.provisioning.fan_pages_handler._fresh_page_inventory', side_effect=AssertionError('WWW forbidden')):
            result = await service.run(item_id='first', profile_id='15', context=self.context, session=self.session,
                payload={'desired': {'ad_accounts': 1}, 'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}})
        self.assertTrue(result['ready_to_launch'])
        self.assertEqual(result['actual']['bundles'][0]['page_id'], FP2)
        self.assertEqual(self.meta.owners, {FP2: BM1})
        self.meta.fetch_text.assert_not_awaited()
