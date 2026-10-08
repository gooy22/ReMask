import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.private_asset_contracts import AssetContracts, CONFIG, PAGE, RIGHTS, CLAIM, ASSIGN
from app.private_page_ownership import ensure_private_page_full_control, assignment_proof, full_control_tasks, permission_config_shape
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.provisioning.prepare import PrepareService
from app.provisioning.service import ProvisioningService
from app.provisioning.advertising_page import AdvertisingPageStore
from app.provisioning.private_create_handlers import _business_collection_complete
from app.facebook_business_browser import _extract_business_inventory_rows

BM, FP, RK, USER, UID = '1428816905866955', '1348798761652037', '222222222222222', '333333333333333', '61594946647826'
P, R, PARTIAL = '444444444444444', '555555555555555', '666666666666666'
SOURCE = Path(__file__).with_name('fixtures').joinpath('meta_page_access_observed_20261008.js').read_text()

def config():
    return {'assetConfigs': [{'assetType': kind, 'hasUserPermissions': True, 'permissionTasksConfig': [
        {'taskID': task, 'taskPermissionType': 'FULL_CONTROL_TASK', 'impliedTaskIDs': [PARTIAL]},
        {'taskID': PARTIAL, 'taskPermissionType': 'PARTIAL_ACCESS_TASK', 'impliedTaskIDs': []}]}
        for kind, task in [('PAGE', P), ('AD_ACCOUNT', R)]], 'apiAccessToken': 'SECRET_NOT_USED'}

def rights(asset, user, tasks):
    return {'data': {'asset': {'__typename': 'Page' if asset == FP else 'AdAccount', 'id': asset,
        'assigned_users': {'edges': [{'node': {'__typename': 'BusinessUser', 'id': user}, 'task_ids': list(tasks)}]}},
        'user': {'__typename': 'BusinessUser', 'id': user}}}

class MetaFixture:
    def __init__(self):
        self.profile = SimpleNamespace(cookies={'c_user': UID}, name='15')
        self.bootstrap = AsyncMock(return_value=SimpleNamespace(actor_id=UID))
        self.owner, self.tasks, self.posts = None, {FP: [], RK: []}, []
        self.lost, self.defer, self.incorrect_read = '', False, False
        self.permissions = config()
    async def graphql(self, doc, variables, *, friendly_name, before_submit=None, **kwargs):
        if friendly_name == CONFIG:
            return {'data': {'business': {'__typename': 'AdBusiness', 'id': BM,
                'businessUser': {'__typename': 'BusinessUser', 'id': USER}, 'bizKitSettingsConfig': self.permissions}}}
        if friendly_name == PAGE:
            return {'data': {'page': {'__typename': 'Page', 'id': FP,
                'ownerBusiness': {'id': self.owner} if self.owner else None, 'permission_to_claim_to_business': 'ALLOWED'}}}
        if friendly_name == RIGHTS:
            asset = variables['assetID']
            return rights('999999999999999' if self.incorrect_read else asset, USER, self.tasks[asset])
        assert before_submit is not None
        await before_submit()
        self.posts.append((friendly_name, copy.deepcopy(variables)))
        if not self.defer:
            if friendly_name == CLAIM: self.owner = variables['businessID']
            else: self.tasks[variables['assetID']] = list(variables['taskIDs'])
        if self.lost == friendly_name: raise TimeoutError('lost response')
        return {'data': {}}

async def discover(web, *, observed, **kwargs):
    observed.observe(SOURCE)
    return observed.result()

class ContractTests(unittest.TestCase):
    def observed(self, source=SOURCE):
        value = AssetContracts(); value.observe(source); return value
    def test_actual_artifacts_and_flat_senders_compile(self):
        value = self.observed(); self.assertIsNotNone(value.result())
        self.assertEqual(value.query(CONFIG, {'businessID': BM})['variables'], {'businessID': BM, 'shouldDefer': False})
    def test_doc_id_without_sender_does_not_enable_writes(self):
        value = self.observed(SOURCE.replace('businessID:K,pageID:s,igAuthCode:n', 'businessID:K,pageID:s,unexpected:n,igAuthCode:n'))
        self.assertIsNone(value.result())
    def test_changed_doc_id_is_read_from_current_module(self):
        old = self.observed().query(CONFIG, {'businessID': BM})['doc_id']
        self.assertEqual(self.observed(SOURCE.replace(old, '888888888888888')).query(CONFIG, {'businessID': BM})['doc_id'], '888888888888888')
    def test_current_adbusiness_is_not_dropped_and_invalid_absence_is_rejected(self):
        payload = {'data': {'businesses': [{'__typename': 'AdBusiness', 'id': BM, 'name': 'BM'}]}}
        self.assertEqual(_extract_business_inventory_rows(payload), [{'id': BM, 'name': 'BM'}])
        self.assertTrue(_business_collection_complete(payload))
        self.assertFalse(_business_collection_complete({'data': {'businesses': [{'__typename': 'Page', 'id': FP}]}}))
        self.assertFalse(_business_collection_complete({'errors': [{'message': 'partial'}], **payload}))
    def test_partial_and_wrong_person_do_not_prove_full_rights(self):
        self.assertIsNone(assignment_proof(rights(FP, USER, [PARTIAL]), asset_id=FP, user_id=USER, required_tasks=[P]))
        self.assertIsNone(assignment_proof(rights(FP, UID, [P]), asset_id=FP, user_id=USER, required_tasks=[P]))
        self.assertEqual(full_control_tasks(config(), 'PAGE'), sorted([P, PARTIAL]))
    def test_variant_tasks_require_exact_variant_and_complete_implications(self):
        value = config()
        row = value['assetConfigs'][0]
        row.update(hasAssetVariants=True, assetVariantConfig={'assetVariantPermissionConfig': [
            {'assetVariantName': 'PAGE_PLUS', 'availablePermissionTaskIDsForVariant': [P, PARTIAL]}]})
        with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE')
        self.assertEqual(full_control_tasks(value, 'PAGE', variant='PAGE_PLUS'), sorted([P, PARTIAL]))
        row['assetVariantConfig']['assetVariantPermissionConfig'][0]['availablePermissionTaskIDsForVariant'] = [P]
        self.assertEqual(full_control_tasks(value, 'PAGE', variant='PAGE_PLUS'), sorted([P, PARTIAL]))
    def test_conflicting_or_malformed_task_config_is_rejected(self):
        for change in ('duplicate', 'invalid_implied'):
            value = config(); rows = value['assetConfigs'][0]['permissionTasksConfig']
            if change == 'duplicate': rows.append(copy.deepcopy(rows[0]))
            else: rows[0]['impliedTaskIDs'] = ['unresolved']
            with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE')
    def test_dead_enum_object_does_not_certify_claim_entrypoint(self):
        value = self.observed(SOURCE.replace('i.default=e', 'i.default=foreignEnum'))
        self.assertIsNone(value.claim_entrypoint()); self.assertIsNone(value.result())
    def test_configured_tasks_are_not_assigned_task_proof(self):
        payload = rights(FP, USER, [])
        payload['data']['asset']['permissionTasksConfig'] = {'task_ids': [P], '__typename': 'BusinessUser', 'id': USER}
        self.assertIsNone(assignment_proof(payload, asset_id=FP, user_id=USER, required_tasks=[P]))
    def test_explicit_null_variant_restriction_is_unrestricted_but_omission_is_not(self):
        value = config(); row = value['assetConfigs'][0]
        variant = {'assetVariantName': 'PROFILE_PLUS_DELEGATE_PAGE', 'availablePermissionTaskIDsForVariant': None}
        row.update(hasAssetVariants=True, assetVariantConfig={'assetVariantPermissionConfig': [variant]})
        self.assertEqual(full_control_tasks(value, 'PAGE', variant='PROFILE_PLUS_DELEGATE_PAGE'), sorted([P, PARTIAL]))
        variant.pop('availablePermissionTaskIDsForVariant')
        with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE', variant='PROFILE_PLUS_DELEGATE_PAGE')
    def test_explicit_null_variant_config_is_unrestricted_but_missing_config_is_not(self):
        value = config(); row = value['assetConfigs'][0]
        row.update(hasAssetVariants=True, assetVariantConfig=None)
        self.assertEqual(full_control_tasks(value, 'PAGE'), sorted([P, PARTIAL]))
        row.pop('assetVariantConfig')
        with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE')
    def test_nullable_implied_tasks_follow_observed_meta_config_normalization(self):
        value = config(); row = value['assetConfigs'][0]['permissionTasksConfig'][0]
        row['impliedTaskIDs'] = None
        self.assertEqual(full_control_tasks(value, 'PAGE'), [P])
        row['impliedTaskIDs'] = 'unresolved'
        with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE')
    def test_empty_variant_restriction_does_not_grant_full_control(self):
        value = config(); row = value['assetConfigs'][0]
        row.update(hasAssetVariants=True, assetVariantConfig={'assetVariantPermissionConfig': [
            {'assetVariantName': 'PROFILE_PLUS_DELEGATE_PAGE', 'availablePermissionTaskIDsForVariant': []}]})
        with self.assertRaises(ProvisioningError): full_control_tasks(value, 'PAGE', variant='PROFILE_PLUS_DELEGATE_PAGE')
    def test_permission_diagnostics_exclude_config_secrets_and_handle_bad_shapes(self):
        value = config()
        value['assetConfigs'][0]['assetVariantConfig'] = {'assetVariantPermissionConfig': 'invalid'}
        snapshot = permission_config_shape(value, 'PAGE', None)
        self.assertNotIn('SECRET', repr(snapshot)); self.assertEqual(snapshot['variant_shapes'], [])

class PageActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.meta, self.saved = MetaFixture(), {}
        self.addCleanup(patch.stopall)
        patch('app.private_page_ownership.asyncio.sleep', new=AsyncMock()).start()
    async def checkpoint(self, patch): self.saved.update(copy.deepcopy(patch))
    async def run_action(self, prior=None):
        return await ensure_private_page_full_control(self.meta, page_id=FP, business_id=BM, ad_account_id=RK,
            profile_id='15', checkpoint=self.checkpoint, prior=prior or {})
    async def test_claim_and_full_assignments_are_independently_verified(self):
        result = await self.run_action()
        self.assertTrue(result['page_owned_by_business']); self.assertTrue(result['operator_full_control_verified'])
        self.assertTrue(result['rk_operator_full_control_verified']); self.assertFalse(result['browser_started'])
        self.assertEqual([x[0] for x in self.meta.posts], [CLAIM, ASSIGN, ASSIGN])
        self.assertEqual(self.meta.posts[1][1]['userID'], USER)
        self.assertNotEqual(self.meta.posts[1][1]['userID'], UID)
        self.assertEqual(self.meta.posts[0][1]['shouldRemoveDirectUsersBeforeClaiming'], 'KEEP')
        self.assertNotIn('SECRET', repr(self.saved))
    async def test_lost_claim_response_reconciles_without_resubmit(self):
        self.meta.lost = CLAIM
        self.assertTrue((await self.run_action())['page_owned_by_business'])
        self.assertEqual(sum(n == CLAIM for n, _ in self.meta.posts), 1)
    async def test_unknown_claim_cross_job_never_duplicates(self):
        self.meta.defer = True
        with self.assertRaises(ProvisioningError): await self.run_action()
        with self.assertRaises(ProvisioningError): await self.run_action(copy.deepcopy(self.saved))
        self.assertEqual(len(self.meta.posts), 1)
    async def test_unknown_assignment_is_verified_before_next_post(self):
        self.meta.owner, self.meta.lost, self.meta.defer = BM, ASSIGN, True
        with self.assertRaises(ProvisioningError): await self.run_action()
        old = copy.deepcopy(self.saved)
        self.meta.tasks, self.meta.defer = {FP: [P, PARTIAL], RK: [R, PARTIAL]}, False
        self.assertTrue((await self.run_action(old))['rk_operator_full_control_verified'])
        self.assertEqual(len(self.meta.posts), 1)
    async def test_storage_failure_prevents_submit(self):
        async def fail(value):
            if value.get('phase') == 'PRIVATE_ASSET_SUBMIT_INTENT': raise OSError('failed storage')
        self.checkpoint = fail
        with self.assertRaises(ProvisioningError): await self.run_action()
        self.assertEqual(self.meta.posts, [])
    async def test_foreign_owner_does_not_transfer_page(self):
        self.meta.owner = '999999999999999'
        with self.assertRaises(ProvisioningError) as caught: await self.run_action()
        self.assertEqual(caught.exception.code, 'PAGE_OWNED_BY_ANOTHER_BUSINESS'); self.assertEqual(self.meta.posts, [])
    async def test_unrelated_read_does_not_trigger_assignment(self):
        self.meta.owner, self.meta.incorrect_read = BM, True
        with self.assertRaises(ProvisioningError) as caught: await self.run_action()
        self.assertEqual(caught.exception.code, 'PRIVATE_ASSIGNMENT_TARGET_UNCONFIRMED'); self.assertEqual(self.meta.posts, [])
        self.assertEqual(self.saved['diagnostic']['code'], 'PRIVATE_ASSIGNMENT_TARGET_UNCONFIRMED')
        self.assertTrue(self.saved['diagnostic']['response_shape'])
    async def test_existing_full_rights_do_not_mutate(self):
        self.meta.owner, self.meta.tasks = BM, {FP: [P, PARTIAL], RK: [R, PARTIAL]}
        self.assertTrue((await self.run_action())['operator_full_control_verified']); self.assertEqual(self.meta.posts, [])
    async def test_nullable_live_config_reaches_independent_assignment_verification(self):
        row = self.meta.permissions['assetConfigs'][0]
        row.update(hasAssetVariants=True, assetVariantConfig=None)
        row['permissionTasksConfig'][0]['impliedTaskIDs'] = None
        self.assertTrue((await self.run_action())['operator_full_control_verified'])
        self.assertEqual(self.meta.posts[1][1]['taskIDs'], [P])
    async def test_permission_error_retains_specific_schema_diagnostic_without_mutating(self):
        self.meta.permissions['assetConfigs'][0].update(hasAssetVariants=True)
        with self.assertRaises(ProvisioningError): await self.run_action()
        self.assertEqual(self.saved['diagnostic']['asset_type'], 'PAGE')
        self.assertIn('omitted', self.saved['diagnostic']['reason'])
        self.assertNotIn('SECRET', repr(self.saved)); self.assertEqual(self.meta.posts, [])
    async def test_discovery_session_gate_is_not_contract_unavailable(self):
        from app.private_contract_discovery import _discover_web_modules
        self.meta.fetch_text = AsyncMock(return_value=(200, '<form id="login_form"></form>', 'https://www.facebook.com/login/'))
        with self.assertRaises(ProvisioningError) as caught:
            await _discover_web_modules(self.meta, entry='https://business.facebook.com/latest/settings/pages/',
                observed=AssetContracts(), label='PAGE_ACCESS')
        self.assertEqual(caught.exception.code, 'SESSION_EXPIRED'); self.assertEqual(self.meta.posts, [])

class PrepareHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_prepare_reuses_bm_rk_without_chromium(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp) / 'state.sqlite')); await state.init()
            context = SimpleNamespace(profile_id='15', cookies={'c_user': UID}, pages=[], businesses=[], ad_accounts=[], inventory_updated_at=0)
            meta = MetaFixture()
            session = SimpleNamespace(context=context, facebook_web=AsyncMock(return_value=meta),
                facebook_business_browser=AsyncMock(side_effect=AssertionError('Chromium forbidden')))
            await AdvertisingPageStore.for_context(state, context, '15').patch(page_id=FP, name='PrgssTeam')
            await state.complete('bm', '15', 'bm', ProvisioningStep.BUSINESS,
                {'business_id': BM, 'phase': 'CREATE_CONFIRMED', 'private_inventory_verified': True,
                 'verification': {'source': 'private_http_business_response', 'exact_business_id': BM}})
            await state.complete('rk', '15', 'rk', ProvisioningStep.AD_ACCOUNT,
                {'business_id': BM, 'ad_account_id': RK, 'account_name': 'Existing RK'})
            await state.complete('funding', '15', 'funding', ProvisioningStep.FUNDING,
                {'business_id': BM, 'ad_account_id': RK, 'funding_source_id': 'existing-funding', 'funding_verified': True})
            prepare = PrepareService(state, ProvisioningService(state))
            with patch('app.provisioning.private_create_handlers._rk_inventory', new=AsyncMock(return_value={'id': RK, 'diagnostics': []})), \
                 patch('app.provisioning.service._await_profile_mutation_cooldown', new=AsyncMock()):
                for job in ['first', 'repeat']:
                    result = await prepare.run(item_id=job, profile_id='15', context=context, session=session,
                        payload={'desired': {'payment': True}, 'parameters': {'AD_ACCOUNT': {'currency': 'USD', 'timezone_id': 137}}})
                    self.assertEqual(result['status'], 'READY_TO_LAUNCH')
            self.assertEqual([name for name, _ in meta.posts], [CLAIM, ASSIGN, ASSIGN])
            self.assertTrue(await state.page_access_confirmed('15', BM, RK, full_control=True))
            session.facebook_business_browser.assert_not_awaited()
    async def test_legacy_ads_only_success_is_not_full_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(str(Path(tmp) / 'state.sqlite')); await state.init()
            await state.complete('old', '15', 'access', ProvisioningStep.PAGE_ACCESS,
                {'business_id': BM, 'ad_account_id': RK, 'page_shared_to_business': True, 'operator_ads_access_assigned': True})
            self.assertTrue(await state.page_access_confirmed('15', BM, RK))
            self.assertFalse(await state.page_access_confirmed('15', BM, RK, full_control=True))
    async def test_full_control_commit_rejects_legacy_ads_only_result(self):
        with self.assertRaises(ProvisioningError) as caught:
            ProvisioningService._verify_result_contract(ProvisioningStep.PAGE_ACCESS,
                {'page_id': FP, 'business_id': BM, 'ad_account_id': RK,
                 'page_shared_to_business': True, 'operator_ads_access_assigned': True},
                {'access_mode': 'existing_page_full_control'}, {})
        self.assertEqual(caught.exception.code, 'VERIFY_FULL_CONTROL_UNCONFIRMED')
