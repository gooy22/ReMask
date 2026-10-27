import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.session import MetaSession, ProfileResolver


class CookieDeletionDoesNotCreateProfileGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_marker_fingerprint_is_ignored_by_profile_resolver(self):
        cookies = {"c_user": "123456789", "xs": "fixture-session"}
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver._fetch = AsyncMock(return_value={
            "cookies": cookies,
            "user_agent": "fixture-UA",
            "proxy": "http://127.0.0.1:1",
            "cookie_auth_revocation_xs_sha256": "a" * 64,
        })

        context = await resolver.resolve("fixture-profile")

        self.assertEqual(context.cookies, cookies)
        self.assertFalse(hasattr(context, "_cookie_revoker"))

    async def test_missing_xs_during_private_request_does_not_revoke_saved_profile(self):
        saved = {"c_user": "123456789", "xs": "fixture-session"}
        dropped = {"c_user": "123456789"}
        persistor = AsyncMock(return_value=True)
        revoker = AsyncMock(return_value=True)
        context = SimpleNamespace(
            profile_id="fixture-profile",
            cookies=dict(saved),
            _loaded_cookies=dict(saved),
            _cookie_persistor=persistor,
            _cookie_revoker=revoker,
            proxy="http://127.0.0.1:1",
            user_agent="fixture-UA",
        )

        class FakeFacebookSession:
            def __init__(self):
                self.profile = SimpleNamespace(cookies=dict(dropped))

            async def close(self):
                return None

        session = MetaSession(context)
        session._facebook_session = FakeFacebookSession()
        session._facebook_cookie_baseline = dict(saved)
        session._facebook_profile_id = "fixture-profile"

        await session.close()

        self.assertEqual(context.cookies, saved)
        persistor.assert_not_awaited()
        revoker.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
