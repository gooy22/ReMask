import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.provisioning.meta_transport import MetaTransportRouter


class MetaTransportRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_policy_is_private_first(self):
        session = SimpleNamespace(context=SimpleNamespace(profile_id="7"))
        router = MetaTransportRouter(session)
        policy = router.policy("BUSINESS")
        self.assertEqual(policy.primary, "facebook_web_graphql")
        self.assertEqual(policy.fallback, "chromium_business_suite")

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
