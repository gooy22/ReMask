import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.private_page_ownership import CONFIG, PAGE, RIGHTS, CLAIM, ASSIGN
from app.business_fan_page_contracts import command as page_command
from app.provisioning.advertising_page import AdvertisingPageStore
from app.provisioning.business_pages import BusinessPageStore, ensure_business_page
from app.provisioning.fan_pages_handler import fan_pages_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.prepare import PrepareService
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_page_ownership import config, standalone_rights, P, R, PARTIAL

UID = '61594946647826'
BM1, BM2 = '1428816905866955', '1451903470239662'
FP1, FP2 = '1348798761652037', '2348798761652037'
RK1, RK2 = '120251352568830122', '120251650486340295'
USER = '333333333333333'


class TwoBundleMeta:
    """Recorded query shapes with synthetic Page2; no network or browser."""
    def __init__(self):
        self.profile = SimpleNamespace(cookies={'c_user': UID}, name='15')
        self.bootstrap = AsyncMock(return_value=SimpleNamespace(actor_id=UID))
        self.owners = {FP1: BM1}
        self.tasks = {FP1: [P, PARTIAL], RK1: [R, PARTIAL], RK2: []}
        self.posts = []
        self.lose_claim = False
        self.lose_page = False
        self.page_read_available = True
        self.calls = []
        self.fetch_text = AsyncMock(side_effect=AssertionError('WWW/HTML discovery must not run'))

    async def graphql(self, doc, variables, *, friendly_name, before_submit=None, **kwargs):
        self.calls.append((friendly_name, copy.deepcopy(variables)))
        page_ops = {page_command(op, business=BM2, cursor=None, category='Digital creator',
            categories=['242822000000000'], name='PrgssTeam', bio='', join='fixture')['friendly_name']: op
            for op in ('READ_FP', 'FP_CATEGORY', 'FP_PRECHECK', 'CREATE_FP')}
        op = page_ops.get(friendly_name)
        if op == 'READ_FP':
            bm = variables['businessID']
            edges = [{'node': {'__typename': 'Page', 'assetID': page, 'assetType': 'PAGE'},
                'nameColumn': {'bizkit_settings_render_strategy_no_business_id': {'business_object': {
                    'business_object_id': page, 'business_object_name': 'PrgssTeam'}}}}
                for page, owner in self.owners.items() if owner == bm]
            return {'data': {'node': {'id': bm, 'connected_objects': {'edges': edges,
                'page_info': {'has_next_page': False, 'end_cursor': None}}}}}
        if op == 'FP_CATEGORY':
            return {'data': {'page_creation_category_typeahead_search': {'results': {'nodes': [
                {'category_id': '242822000000000', 'category_name': 'Digital creator'}]}}}}
        if op == 'FP_PRECHECK':
            return {'data': {'business': {'id': variables['businessID'],
                'showPageClaimBlockingDisclosures': {'passes_gk': False}}}}
        if op == 'CREATE_FP':
            await before_submit()
            self.posts.append(('FP_CREATE', copy.deepcopy(variables)))
            self.owners[FP2] = variables['input']['business_id']
            self.tasks[FP2] = []
            if self.lose_page:
                self.lose_page = False
                raise TimeoutError('lost after Page commit')
            return {'data': {'additional_profile_plus_create': {'additional_profile': {
                'id': '61500012345678', 'delegate_page': {'id': FP2}}}}}
        bm = variables.get('businessID')
        if friendly_name == CONFIG:
            return {'data': {'business': {'id': bm, 'businessUser': {'id': USER}, 'bizKitSettingsConfig': config()}}}
        if friendly_name == PAGE:
            page = variables['pageID']
            if not self.page_read_available:
                return {'data': {'page': None}}
            return {'data': {'page': {'id': page, 'name': 'PrgssTeam', 'ownerBusiness': {'id': self.owners[page]} if self.owners[page] else None,
                'permission_to_claim_to_business': 'ALLOWED'}}}
        if friendly_name == RIGHTS:
            return standalone_rights(variables['assetID'], USER, self.tasks.get(variables['assetID'], []), bm)
        if friendly_name not in (CLAIM, ASSIGN):
            raise AssertionError('Unexpected contract / infrastructure CREATE: ' + friendly_name)
        await before_submit()
        self.posts.append((friendly_name, copy.deepcopy(variables)))
        if friendly_name == CLAIM:
            page = variables['pageID']
            assert page == FP2 and bm == BM2, 'Must never transfer the first Page'
            self.owners[page] = bm
            if self.lose_claim:
                self.lose_claim = False
                raise TimeoutError('lost after commit')
        else:
            self.tasks[variables['assetID']] = list(variables['taskIDs'])
        return {'data': {}}

    async def pages(self, session):
        return [{'id': page, 'name': 'PrgssTeam', 'business_id': owner or ''} for page, owner in self.owners.items()]

    async def create(self, session, *, before_pages, before_submit, **kwargs):
        await before_submit({'before_ids': [row['id'] for row in before_pages], 'transport': 'private_http'})
        self.posts.append(('FP_CREATE', {}))
        self.owners[FP2] = None
        self.tasks[FP2] = []
        return {'page_id': FP2, 'name': 'PrgssTeam', 'reused': False}


class BusinessPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / 'state.sqlite'))
        await self.state.init()
        self.context = SimpleNamespace(profile_id='15', cookies={'c_user': UID}, pages=[], businesses=[], ad_accounts=[])
        self.meta = TwoBundleMeta()
        self.session = SimpleNamespace(context=self.context, private_only=True,
            facebook_web=AsyncMock(return_value=self.meta), close_business_browser=AsyncMock(),
            facebook_business_browser=AsyncMock(side_effect=AssertionError('Chromium must not start')))
        self.common = AdvertisingPageStore.for_context(self.state, self.context)
        await self.common.patch(page_id=FP1, name='PrgssTeam', owner_business_id=BM1, owner_business_confirmed=True)

    async def test_migration_preserves_first_owner_grants_and_does_not_copy_to_second(self):
        pending = {'private_target': {'business_id': BM1, 'page_id': FP1},
            'private_operations': {'assign_rk': {'status': 'RESULT_UNKNOWN'}}}
        await self.common.patch(grants={BM1: pending})
        first = await BusinessPageStore(self.state, self.context, BM1).get()
        second = await BusinessPageStore(self.state, self.context, BM2).get()
        self.assertEqual(first['page_id'], FP1)
        self.assertEqual(first['grants'][BM1], pending)
        self.assertNotIn('page_id', second)
        self.assertEqual((await self.common.get())['page_id'], FP1)

    async def test_duplicate_page_reservation_is_atomic_and_does_not_delete_first_binding(self):
        first = BusinessPageStore(self.state, self.context, BM1)
        await first.get()
        with self.assertRaises(ProvisioningError) as exc:
            await BusinessPageStore(self.state, self.context, BM2).patch(page_id=FP1)
        self.assertEqual(exc.exception.code, 'BUNDLE_PAGE_ALREADY_RESERVED')
        self.assertEqual((await first.get())['page_id'], FP1)
        await first.patch(grants={BM1: {'phase': 'PRIVATE_ASSET_CONFIRMED'}})
        self.assertEqual((await first.get())['page_id'], FP1)

    async def test_aliases_share_exact_bm_page_and_different_actor_is_isolated(self):
        await BusinessPageStore(self.state, self.context, BM2).patch(page_id=FP2)
        alias = SimpleNamespace(profile_id='18', cookies={'c_user': UID})
        self.assertEqual((await BusinessPageStore(self.state, alias, BM2).get())['page_id'], FP2)
        other = SimpleNamespace(profile_id='19', cookies={'c_user': '61500000000000'})
        self.assertNotIn('page_id', await BusinessPageStore(self.state, other, BM2).get())

    async def test_unowned_legacy_page_keeps_retained_claim_when_bound(self):
        grant = {'private_target': {'business_id': BM2, 'page_id': FP1},
            'private_operations': {'claim_page': {'status': 'RESULT_UNKNOWN'}}}
        self.meta.owners[FP1] = None
        await self.common.patch(owner_business_confirmed=False, owner_business_id='', grants={BM2: grant})
        result = await ensure_business_page(self.session, {'business_id': BM2}, self.state)
        self.assertEqual(result['page_id'], FP1)
        self.assertEqual(result['grants'][BM2], grant)
        self.assertEqual(self.meta.posts, [])

    async def test_same_name_foreign_page_is_read_and_skipped_without_claim_or_transfer(self):
        await self.common.patch(owner_business_confirmed=False, owner_business_id='')
        create = AsyncMock(return_value={'pages': [{'id': FP2, 'name': 'PrgssTeam'}]})
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler', create):
            result = await ensure_business_page(self.session, {'business_id': BM2}, self.state)
        self.assertEqual(result['page_id'], FP2)
        self.assertEqual(create.call_args.args[1]['reserved_page_ids'], [FP1])
        self.assertTrue(create.call_args.args[1]['defer_business_attach'])
        self.assertEqual(self.meta.posts, [])
        self.assertEqual(self.meta.owners[FP1], BM1)

    async def test_inconclusive_owner_or_actor_mismatch_never_creates(self):
        await self.common.patch(owner_business_confirmed=False)
        for mode in ('owner', 'actor'):
            with self.subTest(mode=mode):
                create = AsyncMock()
                if mode == 'owner':
                    self.meta.graphql = AsyncMock(return_value={'data': {'page': {'id': FP1}}})
                else:
                    self.meta.bootstrap.return_value.actor_id = '999999999'
                with patch('app.provisioning.fan_pages_handler.fan_pages_handler', create):
                    with self.assertRaises(ProvisioningError):
                        await ensure_business_page(self.session, {'business_id': BM2}, self.state)
                create.assert_not_awaited()

    async def test_alias_retry_uses_original_intent_and_never_reuses_first_same_name_page(self):
        seen = []
        async def create(session, params, snapshot, **kwargs):
            seen.append((kwargs['item_id'], kwargs['profile_id']))
            if len(seen) == 1:
                await self.state.checkpoint(kwargs['item_id'], kwargs['profile_id'], kwargs['scope_key'],
                    ProvisioningStep.FAN_PAGES, {'phase': 'PAGE_CREATE_CLICK_INTENT', 'business_id': BM2,
                        'active_page_name': 'PrgssTeam', 'active_before_ids': [FP1]})
                raise ProvisioningError('FAN_PAGE_CREATE_RESULT_UNKNOWN', 'lost', retryable=True)
            saved = await self.state.step(kwargs['item_id'], ProvisioningStep.FAN_PAGES)
            self.assertEqual(saved['result']['active_before_ids'], [FP1])
            return {'pages': [{'id': FP2, 'name': 'PrgssTeam'}]}
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler', side_effect=create):
            with self.assertRaises(ProvisioningError):
                await ensure_business_page(self.session, {'business_id': BM2}, self.state)
            self.context.profile_id = '18'
            result = await ensure_business_page(self.session, {'business_id': BM2}, self.state)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(result['page_id'], FP2)
        self.assertEqual((await self.common.get())['page_id'], FP1)

    async def test_parallel_retries_allocate_one_page_for_exact_business(self):
        create = AsyncMock(return_value={'pages': [{'id': FP2, 'name': 'PrgssTeam'}]})
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler', create):
            result = await asyncio.gather(*[ensure_business_page(self.session, {'business_id': BM2}, self.state) for _ in range(4)])
        self.assertEqual(create.await_count, 1)
        self.assertEqual({row['page_id'] for row in result}, {FP2})

    async def test_uncertain_same_name_creates_are_scoped_and_do_not_tombstone_other_business(self):
        for item, bm, phase in [('pending', BM1, 'PAGE_CREATE_CLICK_INTENT'), ('done', BM2, 'PAGE_CREATED')]:
            await self.state.set_running(item, '15', item, ProvisioningStep.FAN_PAGES)
            await self.state.checkpoint(item, '15', item, ProvisioningStep.FAN_PAGES,
                {'phase': phase, 'business_id': bm, 'active_page_name': 'PrgssTeam'})
        result = await self.state.latest_uncertain_fan_page('15', 'PrgssTeam', business_id=BM1)
        self.assertEqual(result['item_id'], 'pending')
        self.assertEqual(await self.state.latest_uncertain_fan_page('15', 'PrgssTeam', business_id=BM2), {})

    async def test_second_page_create_baseline_includes_missing_first_and_defers_browser_attach(self):
        await self.state.set_running('fp2', '15', 'fp2', ProvisioningStep.FAN_PAGES)
        async def create(session, *, before_pages, before_submit, **kwargs):
            self.assertEqual({row['id'] for row in before_pages}, {FP1})
            await before_submit({'before_ids': [FP1], 'transport': 'private_http'})
            return {'page_id': FP2}
        with patch('app.provisioning.fan_pages_handler._fresh_page_inventory', AsyncMock(return_value=[])), \
                patch('app.provisioning.fan_pages_handler._create_page_via_private_contract', side_effect=create), \
                patch('app.provisioning.fan_pages_handler._attach_page_to_business', AsyncMock()) as attach:
            result = await fan_pages_handler(self.session, {'names': ['PrgssTeam'], 'business_id': BM2,
                'reserved_page_ids': [FP1], 'defer_business_attach': True}, {}, provisioning_state=self.state,
                item_id='fp2', profile_id='15', scope_key='fp2')
        attach.assert_not_awaited()
        self.assertEqual(result['page_ids'], [FP2])
        self.assertFalse(result['page_business_attached'])
        self.assertEqual((await self.state.step('fp2', ProvisioningStep.FAN_PAGES))['result']['business_id'], BM2)

    async def test_standalone_same_name_success_cannot_clear_a_business_create_intent(self):
        await self.state.set_running('pending', '15', 'pending', ProvisioningStep.FAN_PAGES)
        await self.state.checkpoint('pending', '15', 'pending', ProvisioningStep.FAN_PAGES,
            {'phase': 'PAGE_CREATE_CLICK_INTENT', 'business_id': BM2,
                'active_page_name': 'PrgssTeam', 'active_before_ids': [FP1]})
        await self.state.complete('standalone', '15', 'standalone', ProvisioningStep.FAN_PAGES,
            {'phase': 'DONE', 'pages': [{'id': FP1, 'name': 'PrgssTeam'}]})
        with self.state._connect() as db:
            db.execute('UPDATE provisioning_steps SET updated_at=100 WHERE item_id=?', ('pending',))
            db.execute('UPDATE provisioning_steps SET updated_at=200 WHERE item_id=?', ('standalone',))
        result = await self.state.latest_uncertain_fan_page('15', 'PrgssTeam', business_id=BM2)
        self.assertEqual(result['item_id'], 'pending')

    async def _seed_existing_bundles(self):
        for bm, rk in ((BM1, RK1), (BM2, RK2)):
            await self.state.complete('bm-'+bm, '15', bm, ProvisioningStep.BUSINESS, {'business_id': bm})
            await self.state.complete('rk-'+rk, '15', bm, ProvisioningStep.AD_ACCOUNT,
                {'business_id': bm, 'ad_account_id': rk, 'account_name': 'RK'})
            await self.state.set_payment_link_state('15', rk, True, source='fixture')
        await self.state.complete('access1', '15', 'first', ProvisioningStep.PAGE_ACCESS,
            {'business_id': BM1, 'ad_account_id': RK1, 'page_id': FP1, 'page_shared_to_business': True,
                'operator_ads_access_assigned': True, 'page_owned_by_business': True,
                'operator_full_control_verified': True, 'rk_operator_full_control_verified': True})

    async def test_prepare_repairs_two_live_bundles_with_uncached_business_page_http_and_full_rights(self):
        await self._seed_existing_bundles()
        prepare = PrepareService(self.state, ProvisioningService(self.state))
        async def inventory(web, business, name, account):
            return {'id': account, 'asset_ui_id': account}
        with patch('app.provisioning.private_create_handlers._rk_inventory', side_effect=inventory), \
                patch('app.provisioning.fan_pages_handler._fresh_page_inventory', side_effect=AssertionError('WWW Page inventory forbidden')), \
                patch('app.provisioning.fan_pages_handler._create_page_via_private_contract', side_effect=AssertionError('Legacy Page CREATE forbidden')), \
                patch('app.provisioning.service._await_profile_mutation_cooldown', AsyncMock()):
            payload = {'desired': {'ad_accounts': 2}, 'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}}
            self.meta.lose_page = True
            first = await prepare.run(item_id='repair', profile_id='15', context=self.context, session=self.session, payload=payload)
            posts = copy.deepcopy(self.meta.posts)
            # Explicitly reverse the selected order; Page identity follows BM.
            payload['desired']['business_id'] = BM2
            second = await prepare.run(item_id='repeat', profile_id='15', context=self.context, session=self.session, payload=payload)
        self.assertTrue(first['ready_to_launch'])
        self.assertTrue(second['ready_to_launch'])
        self.assertEqual({row['business_id']: row['page_id'] for row in first['actual']['bundles']}, {BM1: FP1, BM2: FP2})
        self.assertEqual({row['business_id']: row['page_id'] for row in second['actual']['bundles']}, {BM1: FP1, BM2: FP2})
        self.assertEqual(second['actual']['bundles'][0]['business_id'], BM2)
        self.assertEqual(self.meta.posts, posts)
        self.assertEqual([row[0] for row in posts], [ASSIGN, 'FP_CREATE', ASSIGN])
        self.assertEqual(posts[0][1]['assetID'], RK2)
        self.assertEqual(posts[1][1]['input']['business_id'], BM2)
        self.meta.fetch_text.assert_not_awaited()
        self.assertEqual(self.meta.owners, {FP1: BM1, FP2: BM2})
        self.assertEqual((await self.common.get())['page_id'], FP1)
        self.session.facebook_business_browser.assert_not_awaited()
        self.assertTrue(await self.state.page_access_confirmed('15', BM2, RK2, full_control=True, page_id=FP2))
        self.assertFalse(await self.state.page_access_confirmed('15', BM2, RK2, full_control=True, page_id=FP1))

    async def test_page_failure_keeps_rk_full_control_and_next_job_repairs_only_page(self):
        from fb_worker import AuthenticationError
        await self._seed_existing_bundles()
        prepare = PrepareService(self.state, ProvisioningService(self.state))
        graphql = self.meta.graphql
        page_read = page_command('READ_FP', business=BM2, cursor=None)['friendly_name']
        blocked = True
        async def response(*args, **kwargs):
            if blocked and kwargs['friendly_name'] == page_read:
                error = AuthenticationError('Business login required')
                error.request_may_have_been_sent = False
                error.transport_stage = 'business_auth_precheck'
                error.meta_payload = {'business_precheck': [{'auth_reason': 'login_redirect',
                    'final_url': 'https://business.facebook.com/business/loginpage/'}]}
                raise error
            return await graphql(*args, **kwargs)
        self.meta.graphql = response
        payload = {'desired': {'ad_accounts': 2}, 'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}}
        with patch('app.provisioning.private_create_handlers._rk_inventory',
                side_effect=lambda web, business, name, account: {'id': account, 'asset_ui_id': account}), \
                patch('app.provisioning.service._await_profile_mutation_cooldown', AsyncMock()):
            with self.assertRaises(ProvisioningError) as caught:
                await prepare.run(item_id='page-blocked', profile_id='15', context=self.context,
                    session=self.session, payload=payload)
            self.assertEqual(caught.exception.code, 'BUSINESS_LOGIN_GATE')
            self.assertEqual(self.meta.tasks[RK2], [R, PARTIAL])
            self.assertEqual([name for name, _ in self.meta.posts], [ASSIGN])
            self.assertFalse(await self.state.page_access_confirmed('15', BM2, RK2, full_control=True))
            self.assertNotIn(FP2, self.meta.owners)
            # Simulate process restart and a new Job after session restoration.
            blocked = False
            result = await PrepareService(self.state, ProvisioningService(self.state)).run(
                item_id='page-restored', profile_id='15', context=self.context, session=self.session, payload=payload)
        self.assertTrue(result['ready_to_launch'])
        self.assertEqual([name for name, _ in self.meta.posts], [ASSIGN, 'FP_CREATE', ASSIGN])
        self.assertEqual(self.meta.posts[-1][1]['assetID'], FP2)
        self.assertEqual(self.meta.owners[FP1], BM1)
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_create_without_business_owner_continues_add_existing_page_and_full_rights(self):
        await self._seed_existing_bundles()
        original = self.meta.graphql
        create = page_command('CREATE_FP', business=BM2, name='PrgssTeam',
            categories=['242822000000000'], bio='', join='fixture')['friendly_name']
        async def response(*args, **kwargs):
            payload = await original(*args, **kwargs)
            if kwargs['friendly_name'] == create:
                self.meta.owners[FP2] = None
            return payload
        self.meta.graphql = response
        prepare = PrepareService(self.state, ProvisioningService(self.state))
        with patch('app.provisioning.private_create_handlers._rk_inventory',
                side_effect=lambda web, business, name, account: {'id': account, 'asset_ui_id': account}), \
                patch('app.provisioning.service._await_profile_mutation_cooldown', AsyncMock()):
            result = await prepare.run(item_id='claim-created', profile_id='15', context=self.context,
                session=self.session, payload={'desired': {'ad_accounts': 2},
                    'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}})
            posts = copy.deepcopy(self.meta.posts)
            await prepare.run(item_id='repeat-created', profile_id='15', context=self.context,
                session=self.session, payload={'desired': {'ad_accounts': 2},
                    'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}})
        self.assertTrue(result['ready_to_launch'])
        self.assertEqual([name for name, _ in posts], [ASSIGN, 'FP_CREATE', CLAIM, ASSIGN])
        self.assertEqual(self.meta.posts, posts)
        self.assertEqual(self.meta.owners, {FP1: BM1, FP2: BM2})
        self.assertTrue(await self.state.page_access_confirmed('15', BM2, RK2, full_control=True, page_id=FP2))
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_legacy_unknown_submit_recovers_free_profile_page_and_claims_without_create(self):
        from fb_worker import FacebookWebSession
        await self._seed_existing_bundles()
        self.meta.owners[FP2] = None
        self.meta.tasks[FP2] = []
        self.meta._match_sources = FacebookWebSession._match_sources
        document = '<script>CurrentUserInitialData {"USER_ID":"' + UID + '"};</script><script>' + json.dumps({
            'additional_profiles_with_biz_tools': {'nodes': [{'id': '61500012345678',
                'delegate_page': {'__typename': 'Page', 'id': FP2, 'name': 'PrgssTeam'}}]}}) + '</script>'
        self.meta.fetch_text = AsyncMock(return_value=(200, document, 'https://www.facebook.com/pages/'))
        item = 'workspace-business-page-facebook:' + UID + '-' + BM2
        scope = 'workspace-business-page:' + BM2
        await BusinessPageStore(self.state, self.context, BM2).patch(creation_item_id=item, creation_profile_id='15')
        await self.state.set_running(item, '15', scope, ProvisioningStep.FAN_PAGES)
        await self.state.checkpoint(item, '15', scope, ProvisioningStep.FAN_PAGES,
            {'phase': 'PAGE_CREATE_RESULT_UNKNOWN', 'resume_from': 'RECONCILE_CREATE',
                'business_id': BM2, 'target_names': ['PrgssTeam'], 'create_actor_id': UID,
                'active_page_name': 'PrgssTeam', 'active_before_ids': [FP1], 'response_page_id': ''})
        with patch('app.provisioning.private_create_handlers._rk_inventory',
                side_effect=lambda web, business, name, account: {'id': account, 'asset_ui_id': account}), \
                patch('app.provisioning.service._await_profile_mutation_cooldown', AsyncMock()):
            result = await PrepareService(self.state, ProvisioningService(self.state)).run(
                item_id='recover-legacy', profile_id='15', context=self.context, session=self.session,
                payload={'desired': {'ad_accounts': 2}, 'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 1}}})
        self.assertTrue(result['ready_to_launch'])
        self.assertEqual([name for name, _ in self.meta.posts], [ASSIGN, CLAIM, ASSIGN])
        self.assertEqual(self.meta.owners, {FP1: BM1, FP2: BM2})
        self.assertEqual((await self.state.step(item, ProvisioningStep.FAN_PAGES))['result']['resolution_source'], 'managed_profile_http')
        self.session.facebook_business_browser.assert_not_awaited()
