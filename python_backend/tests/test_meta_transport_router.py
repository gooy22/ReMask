import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.provisioning.meta_transport import MetaTransportRouter
from app.provisioning.service import normalize_add_bm_payload


class AddBmPayloadNormalizationTests(unittest.TestCase):
    def test_stale_add_bm_payload_is_forced_to_business_only(self):
        payload = {
            "steps": ["PROXY_CHECK", "BUSINESS", "AD_ACCOUNT", "PAGE_ACCESS"],
            "scope_key": "add-bm-page-1318717134669505",
            "parameters": {
                "BUSINESS": {
                    "name": "Fresh Business",
                    "attach_page": False,
                    "page_id": "1318717134669505",
                },
                "AD_ACCOUNT": {"name": "stale-rk"},
                "PAGE_ACCESS": {"page_id": "1318717134669505"},
            },
        }

        normalized = normalize_add_bm_payload(
            payload,
            task_idempotency_key="add-bm-1760000000-0",
        )

        self.assertEqual(normalized["steps"], ["PROXY_CHECK", "BUSINESS"])
        self.assertEqual(normalized["scope_key"], "add-bm-1760000000-0")
        self.assertNotIn("page_id", normalized["parameters"]["BUSINESS"])
        self.assertNotIn("primary_page_id", normalized["parameters"]["BUSINESS"])
        self.assertNotIn("AD_ACCOUNT", normalized["parameters"])
        self.assertNotIn("PAGE_ACCESS", normalized["parameters"])

    def test_explicit_page_attach_is_not_rewritten(self):
        payload = {
            "steps": ["PROXY_CHECK", "BUSINESS", "PAGE_ACCESS"],
            "scope_key": "add-bm-page-1318717134669505",
            "parameters": {
                "BUSINESS": {
                    "name": "Page-backed Business",
                    "attach_page": True,
                    "page_id": "1318717134669505",
                },
                "PAGE_ACCESS": {"page_id": "1318717134669505"},
            },
        }

        normalized = normalize_add_bm_payload(
            payload,
            task_idempotency_key="add-bm-1760000000-0",
        )

        self.assertEqual(normalized, payload)


class MetaTransportRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_only_bundle_job_uses_private_transport_without_infrastructure_create(self):
        import tempfile
        from pathlib import Path
        from app.provisioning.service import ProvisioningService
        from app.provisioning.state import ProvisioningStateStore
        from app.provisioning.models import ProvisioningStep
        with tempfile.TemporaryDirectory() as directory:
            state = ProvisioningStateStore(str(Path(directory)/'state.sqlite'))
            await state.init()
            context = SimpleNamespace(profile_id='fixture', cookies={'c_user':'123456789'})
            session = SimpleNamespace(context=context, close_business_browser=AsyncMock(),
                facebook_business_browser=AsyncMock(side_effect=AssertionError('Chromium must not start')))
            async def verify_transport(session, params, snapshot, **kwargs):
                router = kwargs['meta_transport']
                self.assertTrue(router.private_only)
                self.assertFalse(router.browser_available())
                self.assertEqual(router.policy('PAGE_ACCESS').fallback,'none_private_only')
                return {'business_id':params['business_id'],'ad_account_id':params['ad_account_id'],
                    'page_id':'77777777','page_owned_by_business':True,
                    'operator_full_control_verified':True,'rk_operator_full_control_verified':True,
                    'ad_account_page_access_verified':True}
            handler = AsyncMock(side_effect=verify_transport)
            for index, explicit_mode in enumerate((False, True)):
                payload = {'steps':['PAGE_ACCESS'],'scope_key':'bundle-page-v2-fixture-'+str(index),
                    'parameters':{'PAGE_ACCESS':{'business_id':'88888888','ad_account_id':'99999999',
                        'existing_target':True,'page_topology':'ONE_PAGE_PER_BUSINESS',
                        'access_mode':'existing_page_full_control'}}}
                if explicit_mode:
                    payload['transport_mode'] = 'private_http_only'
                with patch('app.provisioning.service.get_handler',return_value=handler), \
                        patch('app.provisioning.service._await_profile_mutation_cooldown',AsyncMock()):
                    result = await ProvisioningService(state).run(item_id='bundle-'+str(index),
                        profile_id='fixture',context=context,session=session,payload=payload)
                self.assertEqual(result['steps'][0]['status'],'SUCCESS')
                self.assertEqual((await state.step('bundle-'+str(index),ProvisioningStep.PAGE_ACCESS))['status'],'SUCCESS')
            self.assertEqual(handler.await_count,2)
            session.facebook_business_browser.assert_not_awaited()

    def test_page_access_browser_fallback_is_explicit_and_one_shot(self):
        lease = object()
        session = SimpleNamespace(
            context=SimpleNamespace(profile_id="27"),
            browser_lease=Mock(return_value=lease),
        )
        router = MetaTransportRouter(session, private_only=True)

        self.assertFalse(router.browser_available())
        self.assertEqual(router.policy("PAGE_ACCESS").fallback, "none_private_only")
        with self.assertRaises(Exception) as blocked:
            router.browser_lease(timeout_seconds=45)
        self.assertEqual(getattr(blocked.exception, "code", ""), "PRIVATE_CAPABILITY_UNAVAILABLE")
        session.browser_lease.assert_not_called()

        router.authorize_page_access_browser_fallback()
        self.assertTrue(router.browser_available())
        self.assertIs(router.browser_lease(timeout_seconds=45), lease)
        self.assertFalse(router.browser_available())
        with self.assertRaises(Exception) as blocked_again:
            router.browser_lease(timeout_seconds=45)
        self.assertEqual(getattr(blocked_again.exception, "code", ""), "PRIVATE_CAPABILITY_UNAVAILABLE")
        session.browser_lease.assert_called_once_with(timeout_seconds=45)

    async def test_authorized_page_access_fallback_reaches_browser_but_default_stays_blocked(self):
        from app.facebook_business_browser import FacebookBusinessBrowser

        context = SimpleNamespace(profile_id="27", cookies={})
        disabled = FacebookBusinessBrowser(context)
        disabled.page = object()
        with self.assertRaises(Exception) as blocked:
            await disabled.open()
        self.assertEqual(
            getattr(blocked.exception, "code", ""),
            "BROWSER_TRANSPORT_DISABLED",
        )

        router = MetaTransportRouter(
            SimpleNamespace(context=context),
            private_only=True,
        )
        with self.assertRaises(Exception) as blocked_by_router:
            router.browser_lease(timeout_seconds=15)
        self.assertEqual(
            getattr(blocked_by_router.exception, "code", ""),
            "PRIVATE_CAPABILITY_UNAVAILABLE",
        )

        router.authorize_page_access_browser_fallback()
        browser = router.browser_lease(timeout_seconds=15)
        self.assertIsInstance(browser, FacebookBusinessBrowser)
        self.assertTrue(browser.allow_private_http_fallback)
        browser.page = object()
        await browser.open()

        with self.assertRaises(Exception) as blocked_again:
            router.browser_lease(timeout_seconds=15)
        self.assertEqual(
            getattr(blocked_again.exception, "code", ""),
            "PRIVATE_CAPABILITY_UNAVAILABLE",
        )

    async def test_business_policy_is_private_first(self):
        session = SimpleNamespace(context=SimpleNamespace(profile_id="7"))
        router = MetaTransportRouter(session)
        policy = router.policy("BUSINESS")
        self.assertEqual(policy.primary, "facebook_web_graphql")
        self.assertEqual(policy.fallback, "none_private_only")

    def test_mutation_fallback_allows_only_proven_pre_submit_errors(self):
        router = MetaTransportRouter(
            SimpleNamespace(context=SimpleNamespace(profile_id="7"))
        )
        self.assertFalse(
            router.mutation_fallback_allowed(
                "BUSINESS",
                "CREATE_BM_MUTATION_NOT_DISCOVERED",
            )
        )
        self.assertFalse(
            router.mutation_fallback_allowed(
                "BUSINESS",
                "CREATE_RESULT_UNKNOWN",
            )
        )
        self.assertFalse(
            router.mutation_fallback_allowed(
                "BUSINESS",
                "CREATE_BM_MUTATION_NOT_DISCOVERED",
                submit_started=True,
            )
        )

    async def test_browser_lease_uses_session_factory_when_available(self):
        lease = object()
        session = SimpleNamespace(
            context=SimpleNamespace(profile_id="7"),
            browser_lease=lambda **kwargs: (
                lease if kwargs.get("timeout_seconds") == 45 else None
            ),
        )
        router = MetaTransportRouter(session)
        self.assertIs(
            router.browser_lease(timeout_seconds=45),
            lease,
        )

    async def test_close_browser_falls_back_to_last_shared_browser(self):
        browser = SimpleNamespace(close=AsyncMock())
        session = SimpleNamespace(
            context=SimpleNamespace(profile_id="7"),
            facebook_business_browser=AsyncMock(return_value=browser),
            _business_browser=browser,
        )
        router = MetaTransportRouter(session)
        self.assertIs(await router.facebook_business_browser(), browser)
        await router.close_business_browser()
        browser.close.assert_awaited_once()
        self.assertIsNone(session._business_browser)

    async def test_router_delegates_profile_bound_resources(self):
        web = object()
        controller = object()
        browser = object()
        session = SimpleNamespace(
            context=SimpleNamespace(profile_id="7"),
            facebook_web=AsyncMock(return_value=web),
            facebook_controller=AsyncMock(return_value=controller),
            facebook_business_browser=AsyncMock(return_value=browser),
            close_business_browser=AsyncMock(),
        )
        router = MetaTransportRouter(session)

        self.assertIs(await router.facebook_web(), web)
        self.assertIs(await router.facebook_controller(), controller)
        self.assertIs(await router.facebook_business_browser(), browser)
        await router.close_business_browser()

        session.facebook_web.assert_awaited_once()
        session.facebook_controller.assert_awaited_once()
        session.facebook_business_browser.assert_awaited_once()
        session.close_business_browser.assert_awaited_once()

    async def test_isolated_browser_lease_is_routed(self):
        lease = object()
        session = SimpleNamespace(
            context=SimpleNamespace(profile_id="7"),
            browser_lease=Mock(return_value=lease),
        )
        router = MetaTransportRouter(session)
        self.assertIs(
            router.browser_lease(timeout_seconds=45),
            lease,
        )
        session.browser_lease.assert_called_once_with(
            timeout_seconds=45,
        )


if __name__ == "__main__":
    unittest.main()
