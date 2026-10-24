import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.session_auth_refresh import (
    persist_and_refresh_saved_auth_context,
    refresh_saved_auth_context,
)


class SavedAuthRefreshTests(unittest.IsolatedAsyncioTestCase):
    def context(self, *, uid="123456789", xs="fixture-old", profile="14"):
        cookies={"c_user": uid, "xs": xs}
        return SimpleNamespace(profile_id=profile, cookies=cookies,
                               _loaded_cookies=dict(cookies), _cookie_persistor=None,
                               proxy="http://fixture-proxy:80", user_agent="Fixture")

    async def test_reloads_a_new_saved_session_for_the_same_facebook_identity(self):
        context=self.context()
        old_persistor=AsyncMock()
        fresh_persistor=AsyncMock()
        context._cookie_persistor=old_persistor
        fresh=self.context(xs="fixture-fresh")
        fresh.proxy="http://new-fixture-proxy:80"
        fresh.user_agent="Fresh fixture"
        fresh._cookie_persistor=fresh_persistor
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        self.assertTrue(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["xs"],"fixture-fresh")
        self.assertEqual(context._loaded_cookies,context.cookies)
        self.assertIs(context._cookie_persistor,fresh_persistor)
        self.assertEqual(context.proxy,"http://new-fixture-proxy:80")
        self.assertEqual(context.user_agent,"Fresh fixture")
        resolver.resolve.assert_awaited_once_with("14")

    async def test_flushes_meta_rotation_before_comparing_saved_session(self):
        context=self.context()
        fresh=self.context(xs="fixture-fresh")

        async def close_profile_session():
            context.cookies["xs"]="fixture-fresh"

        session=SimpleNamespace(
            context=context,
            close=AsyncMock(side_effect=close_profile_session),
        )

        async def resolve_after_close(profile):
            session.close.assert_awaited_once()
            return fresh

        resolver=SimpleNamespace(resolve=AsyncMock(side_effect=resolve_after_close))
        self.assertTrue(await persist_and_refresh_saved_auth_context(
            session,resolver,context,
        ))
        self.assertEqual(context.cookies["xs"],"fixture-fresh")
        self.assertEqual(context._loaded_cookies,context.cookies)
        resolver.resolve.assert_awaited_once_with("14")

    async def test_same_expired_session_is_not_reported_restored(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context()))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context._loaded_cookies,{"c_user":"123456789","xs":"fixture-old"})

    async def test_unrelated_cookie_change_does_not_trigger_auth_retry(self):
        context=self.context()
        context.cookies["locale"]="en_US"
        context._loaded_cookies=dict(context.cookies)
        fresh=self.context()
        fresh.cookies["locale"]="fr_FR"
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["locale"],"en_US")

    async def test_another_profile_or_another_facebook_identity_cannot_replace_the_session(self):
        for fresh in (self.context(profile="15",xs="fixture-fresh"),
                      self.context(uid="999999999",xs="fixture-fresh")):
            context=self.context()
            resolver=SimpleNamespace(resolve=AsyncMock(return_value=fresh))
            self.assertFalse(await refresh_saved_auth_context(resolver,context))
            self.assertEqual(context.cookies["c_user"],"123456789")
            self.assertEqual(context.cookies["xs"],"fixture-old")

    async def test_incomplete_session_does_not_erase_current_authentication(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs="")))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["xs"],"fixture-old")
