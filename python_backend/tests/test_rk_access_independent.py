import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fb_worker import AuthenticationError
from app.private_auth import private_auth_error
from app.private_page_ownership import ensure_private_ad_account_full_control, ASSIGN
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.rk_access import ensure_existing_rk_full_control
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_page_ownership import MetaFixture, UID, BM, FP, RK, USER, R, PARTIAL


class IndependentRKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / 'state.sqlite'))
        await self.state.init()
        self.meta = MetaFixture()
        self.session = SimpleNamespace(context=SimpleNamespace(profile_id='15', cookies={'c_user': UID}),
            facebook_web=AsyncMock(return_value=self.meta))
        self.inventory = patch('app.provisioning.private_create_handlers._rk_inventory',
            AsyncMock(return_value={'id': RK, 'asset_ui_id': RK})).start()
        patch('app.private_page_ownership.asyncio.sleep', AsyncMock()).start()
        self.addCleanup(patch.stopall)
        self.stable = f'workspace-rk-access-facebook:{UID}-{BM}-{RK}'

    async def run_action(self, item='first', profile='15'):
        await self.state.set_running(item, profile, item, ProvisioningStep.PAGE_ACCESS)
        return await ensure_existing_rk_full_control(self.session, state=self.state, profile=profile,
            item=item, scope=item, business=BM, account=RK)

    async def test_no_page_and_no_page_permission_configuration_are_needed(self):
        self.meta.permissions['assetConfigs'] = self.meta.permissions['assetConfigs'][1:]
        result = await self.run_action()
        self.assertTrue(result['rk_operator_full_control_verified'])
        self.assertNotIn('page_id', result)
        self.assertIsNone(self.meta.owner)
        self.assertEqual(self.meta.posts[0][0], ASSIGN)
        self.assertEqual(self.meta.posts[0][1]['assetID'], RK)
        self.assertEqual(self.meta.posts[0][1]['userID'], USER)
        self.assertFalse(await self.state.page_access_confirmed('15', BM, RK, full_control=True))

    async def test_lost_response_keeps_intent_across_jobs_then_reconciles_without_duplicate(self):
        self.meta.lost, self.meta.defer = ASSIGN, True
        for item in ('first', 'second'):
            with self.assertRaises(ProvisioningError) as caught:
                await self.run_action(item)
            self.assertEqual(caught.exception.code, 'PRIVATE_ASSET_RESULT_UNKNOWN')
        self.assertEqual(len(self.meta.posts), 1)
        retained = (await self.state.step(self.stable, ProvisioningStep.PAGE_ACCESS))['result']
        self.assertEqual(retained['private_rk_operations']['assign_rk']['status'], 'RESULT_UNKNOWN')
        self.assertNotIn('private_target', retained)
        self.meta.tasks[RK], self.meta.defer = [R, PARTIAL], False
        self.assertTrue((await self.run_action('third'))['rk_operator_full_control_verified'])
        self.assertEqual(len(self.meta.posts), 1)

    async def test_alias_uses_original_checkpoint_creator_and_same_actor_assignment(self):
        await self.run_action()
        self.session.context.profile_id = '18'
        await self.run_action('alias', '18')
        self.assertEqual(len(self.meta.posts), 1)
        self.assertEqual((await self.state.step(self.stable, ProvisioningStep.PAGE_ACCESS))['profile_id'], '15')

    async def test_rk_checkpoint_cannot_overwrite_a_retained_page_claim_phase(self):
        await self.state.set_running('first', '15', 'first', ProvisioningStep.PAGE_ACCESS)
        await self.state.checkpoint('first', '15', 'first', ProvisioningStep.PAGE_ACCESS,
            {'phase': 'TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED', 'page_id': FP})
        await self.run_action()
        retained = (await self.state.step('first', ProvisioningStep.PAGE_ACCESS))['result']
        self.assertEqual(retained['phase'], 'TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED')
        self.assertEqual(retained['rk_access_phase'], 'PRIVATE_ASSET_CONFIRMED')
        self.assertEqual(retained['page_id'], FP)

    async def test_distinct_ui_id_keeps_canonical_proof_and_full_business_user(self):
        self.meta.standalone, self.meta.rk_ui_id = True, '123123123123123'
        self.inventory.return_value = {'id': RK, 'asset_ui_id': self.meta.rk_ui_id}
        result = await self.run_action()
        self.assertEqual(self.meta.posts[0][1]['assetID'], self.meta.rk_ui_id)
        self.assertEqual(result['rk_operator_full_control_proof']['asset_id'], RK)

    async def test_wrong_relation_and_authentication_gate_send_no_assignment(self):
        self.inventory.return_value = {'id': FP}
        with self.assertRaises(ProvisioningError) as caught:
            await self.run_action()
        self.assertEqual(caught.exception.code, 'BUSINESS_RK_RELATION_INCONCLUSIVE')
        error = AuthenticationError('token present on login wall')
        error.request_may_have_been_sent = False
        error.transport_stage = 'business_auth_precheck'
        error.meta_payload = {'business_precheck': [{'auth_reason': 'login_redirect',
            'final_url': 'https://business.facebook.com/business/loginpage/'}]}
        self.inventory.side_effect = error
        with self.assertRaises(ProvisioningError) as caught:
            await self.run_action('auth')
        self.assertEqual(caught.exception.code, 'BUSINESS_LOGIN_GATE')
        self.assertEqual(self.meta.posts, [])

    async def test_pending_legacy_rk_submit_is_not_replayed_by_independent_action(self):
        prior = {'private_target': {'business_id': BM, 'page_id': FP, 'ad_account_id': RK,
            'rk_asset_id': RK, 'operator_uid': UID, 'business_user_id': USER},
            'private_operations': {'assign_rk': {'status': 'RESULT_UNKNOWN'}}}
        saved = {}
        async def checkpoint(value): saved.update(copy.deepcopy(value))
        with self.assertRaises(ProvisioningError) as caught:
            await ensure_private_ad_account_full_control(self.meta, business_id=BM,
                ad_account_id=RK, profile_id='15', checkpoint=checkpoint, prior=prior)
        self.assertEqual(caught.exception.code, 'PRIVATE_ASSET_RESULT_UNKNOWN')
        self.assertEqual(self.meta.posts, [])
        prior['private_target']['business_user_id'] = UID
        with self.assertRaises(ProvisioningError) as caught:
            await ensure_private_ad_account_full_control(self.meta, business_id=BM,
                ad_account_id=RK, profile_id='15', checkpoint=checkpoint, prior=prior)
        self.assertEqual(caught.exception.code, 'PRIVATE_PAGE_ACCESS_CHECKPOINT_MISMATCH')
        self.assertEqual(self.meta.posts, [])


class PrivateAuthTests(unittest.TestCase):
    def test_generic_login_checkpoint_message_is_not_checkpoint_evidence(self):
        self.assertEqual(private_auth_error(AuthenticationError(
            'Facebook GraphQL redirected to login/checkpoint')).code, 'SESSION_EXPIRED')

    def test_business_checkpoint_and_profile_auth_errors_preserve_actionable_codes(self):
        for final_url, reason, code in (
            ('https://business.facebook.com/business/loginpage/', 'login_redirect', 'BUSINESS_LOGIN_GATE'),
            ('https://www.facebook.com/checkpoint/', 'checkpoint_redirect', 'CHECKPOINT_REQUIRED'),
            ('https://www.facebook.com/login/', 'login_redirect', 'SESSION_EXPIRED')):
            with self.subTest(code=code):
                error = AuthenticationError('auth precheck')
                error.request_may_have_been_sent = False
                error.meta_payload = {'business_precheck': [{'final_url': final_url, 'auth_reason': reason}]}
                classified = ProvisioningService._classify(error)
                self.assertEqual(classified.code, code)
                self.assertIn('No GraphQL POST was sent', str(classified))
                self.assertTrue(classified.retryable)

    def test_post_uncertainty_is_not_misrepresented_as_unsent(self):
        error = AuthenticationError('login response after POST')
        classified = private_auth_error(error)
        self.assertNotIn('No GraphQL POST was sent', str(classified))
        self.assertIn('verified before retry', str(classified))
        self.assertIsNone(private_auth_error(RuntimeError('login string is not proof')))
