import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as api
from fb_worker import AuthenticationError
from app.private_business_preflight import private_business_preflight
from app.provisioning.models import ProvisioningStep
from app.provisioning.timeouts import private_create_step_timeout, provisioning_hard_timeout


class PrivateBusinessPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SimpleNamespace(cookies={"c_user": "123456789"}, pages=[], email="owner@example.com")
        bootstrap = SimpleNamespace(actor_id="123456789", fb_dtsg="SECRET TOKEN", lsd="SECRET LSD",
            source_url="https://business.facebook.com/latest/home?session=SECRET", jazoest="")
        self.web = SimpleNamespace(bootstrap=AsyncMock(return_value=bootstrap),
            _business_bootstrap=AsyncMock(return_value=bootstrap), graphql=AsyncMock(side_effect=AssertionError("Mutation forbidden")))
        self.session = SimpleNamespace(proxy_check=AsyncMock(return_value={"exit_ip": "127.0.0.1"}),
            facebook_web=AsyncMock(return_value=self.web), facebook_business_browser=AsyncMock(side_effect=AssertionError("Chromium forbidden")))
        self.addCleanup(patch.stopall)
        self.inventory = patch("app.private_business_preflight._business_inventory", new=AsyncMock(return_value={"rows": {}, "complete": True})).start()
        self.contract = patch('app.private_business_preflight.contract_metadata', return_value={
            'doc_id':'28057338880523368','friendly_name':'useBusinessCreationMutationMutation','evidence':'static_capture'}).start()

    async def test_readiness_needs_no_chromium_page_inventory_or_mutation(self):
        result = await private_business_preflight(self.session, self.context, "14")
        self.assertTrue(result["bm_route_ready"])
        self.assertEqual(result["facebook_session"], "private_http")
        self.assertFalse(result["browser_started"])
        self.assertFalse(result["bm_routes"]["browser_ui"])
        self.assertTrue(result["bm_routes"]["web_scope_selector_candidate"])
        self.web.graphql.assert_not_awaited()
        self.session.facebook_business_browser.assert_not_awaited()
        self.assertNotIn("SECRET", repr(result))

    async def test_authenticated_but_incomplete_inventory_is_not_create_ready(self):
        self.inventory.return_value = {"rows": {"999999999": "BM"}, "complete": False}
        result = await private_business_preflight(self.session, self.context, "14")
        self.assertTrue(result["facebook_session_ready"])
        self.assertFalse(result["bm_route_ready"])
        self.assertEqual(result["private_business"]["error_code"], "PRIVATE_BM_INVENTORY_INCONCLUSIVE")

    async def test_login_checkpoint_and_actor_mismatch_are_auth_failures(self):
        for failure, code in ((AuthenticationError("Facebook login required"), "SESSION_EXPIRED"),
                (AuthenticationError("Facebook checkpoint required"), "CHECKPOINT_REQUIRED")):
            self.web._business_bootstrap.side_effect = failure
            result = await private_business_preflight(self.session, self.context, "14")
            self.assertTrue(result["auth_blocked"])
            self.assertFalse(result["facebook_session_ready"])
            self.assertEqual(result["auth_error_code"], code)
        self.web._business_bootstrap.side_effect = None
        self.web._business_bootstrap.return_value.actor_id = "888888888"
        result = await private_business_preflight(self.session, self.context, "14")
        self.assertTrue(result["auth_blocked"])

    async def test_missing_contract_is_not_claimed_ready_from_bootstrap_alone(self):
        self.contract.side_effect = ValueError('missing contract')
        result = await private_business_preflight(self.session, self.context, "14")
        self.assertFalse(result["bm_route_ready"])
        self.assertTrue(result["facebook_session_ready"])
        self.assertEqual(result["private_business"]["error_code"], "PRIVATE_BM_PREFLIGHT_INCONCLUSIVE")

    async def test_auth_gate_during_inventory_cannot_be_hidden_by_static_contract(self):
        self.inventory.side_effect = AuthenticationError('Facebook checkpoint required')
        result = await private_business_preflight(self.session, self.context, '14')
        self.assertFalse(result['bm_route_ready'])
        self.assertFalse(result['facebook_session_ready'])
        self.assertEqual(result['auth_error_code'], 'CHECKPOINT_REQUIRED')

    async def test_api_business_purpose_never_enters_browser_path(self):
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=self.session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api, "ProfileSession", factory), patch.object(api.pool.resolver, "resolve", new=AsyncMock(return_value=self.context)):
            result = await api.profile_preflight("14", purpose="business")
        self.assertTrue(result["bm_route_ready"])
        self.session.facebook_business_browser.assert_not_awaited()


class PrivateActionWatchdogTests(unittest.TestCase):
    def test_http_budgets_are_independent_of_browser_queue_size(self):
        for workers in (1, 100, 500):
            with patch.dict(os.environ, {"REMASK_WORKER_CONCURRENCY": str(workers), "REMASK_BM_BROWSER_CONCURRENCY": "1"}, clear=True):
                self.assertEqual(private_create_step_timeout(ProvisioningStep.BUSINESS), 240)
                self.assertEqual(private_create_step_timeout(ProvisioningStep.AD_ACCOUNT), 240)
                self.assertEqual(provisioning_hard_timeout(["BUSINESS", "AD_ACCOUNT"]), 600)

    def test_explicit_private_budget_and_mixed_jobs_use_each_transport_budget(self):
        with patch.dict(os.environ, {"REMASK_PRIVATE_BUSINESS_STEP_TIMEOUT": "120", "REMASK_WORKER_CONCURRENCY": "2"}, clear=True):
            self.assertEqual(private_create_step_timeout(ProvisioningStep.BUSINESS), 120)
            self.assertGreater(provisioning_hard_timeout(["BUSINESS", "PAGE_ACCESS"]), 120)

    def test_standalone_operator_timeout_override_remains_supported(self):
        with patch.dict(os.environ, {"REMASK_ADD_BM_HARD_TIMEOUT_SECONDS": "700"}, clear=True):
            self.assertEqual(provisioning_hard_timeout([" business "]), 700)
            self.assertEqual(provisioning_hard_timeout(["BUSINESS", "AD_ACCOUNT"]), 600)
