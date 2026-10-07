import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
