import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.session import MetaSession, ProfileContextError, ProfileResolver


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


class ProfileResolverRevocationGuardTests(unittest.IsolatedAsyncioTestCase):
    def resolver(self, cookies, revocation):
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver._fetch = AsyncMock(return_value={
            "cookies": cookies,
            "user_agent": "fixture-UA",
            "proxy": "http://127.0.0.1:1",
        })
        store = SimpleNamespace(
            has_profile_auth_cookie_revocation=AsyncMock(return_value=True),
            profile_auth_cookie_revocation_for_context=AsyncMock(return_value=revocation),
        )
        resolver.auth_cookie_revocation_store = store
        return resolver, store

    async def test_same_revoked_snapshot_stops_before_any_meta_session(self):
        cookies = {"c_user": "123456789", "xs": "revoked-session"}
        resolver, store = self.resolver(cookies, {
            "status": "META_AUTH_COOKIE_REVOKED",
            "deleted_names": ["c_user", "xs"],
        })

        with self.assertRaises(ProfileContextError) as caught:
            await resolver.resolve("fixture-profile")

        self.assertEqual(caught.exception.code, "META_AUTH_COOKIE_REVOKED")
        self.assertIn("c_user, xs", str(caught.exception))
        self.assertEqual(caught.exception.auth_cookie_deletions, ["c_user", "xs"])
        store.profile_auth_cookie_revocation_for_context.assert_awaited_once_with(
            "fixture-profile", cookies,
        )

    async def test_new_session_for_same_actor_clears_marker_before_returning_context(self):
        cookies = {"c_user": "123456789", "xs": "new-session"}
        resolver, store = self.resolver(cookies, None)

        context = await resolver.resolve("fixture-profile")

        self.assertEqual(context.cookies, cookies)
        store.profile_auth_cookie_revocation_for_context.assert_awaited_once_with(
            "fixture-profile", cookies,
        )

    async def test_changed_actor_is_blocked_before_meta_session(self):
        cookies = {"c_user": "987654321", "xs": "other-actor-session"}
        resolver, store = self.resolver(cookies, {
            "status": "PROFILE_IDENTITY_CHANGED",
            "deleted_names": [],
        })

        with self.assertRaises(ProfileContextError) as caught:
            await resolver.resolve("fixture-profile")

        self.assertEqual(caught.exception.code, "PROFILE_IDENTITY_CHANGED")
        self.assertIn("ID Facebook-профиля изменился", str(caught.exception))

    async def test_guard_store_failure_fails_closed_before_meta_session(self):
        cookies = {"c_user": "123456789", "xs": "revoked-session"}
        resolver, store = self.resolver(cookies, None)
        store.has_profile_auth_cookie_revocation.side_effect = RuntimeError("store unavailable")

        with self.assertRaises(ProfileContextError) as caught:
            await resolver.resolve("fixture-profile")

        self.assertEqual(caught.exception.code, "PROFILE_AUTH_GUARD_UNAVAILABLE")
        self.assertIn("Запрос к Meta не отправлен", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
