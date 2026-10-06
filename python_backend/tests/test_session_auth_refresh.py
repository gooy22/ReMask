import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.session_auth_refresh import refresh_saved_auth_context


class SavedAuthRefreshTests(unittest.IsolatedAsyncioTestCase):
    def context(self, *, uid="123456789", xs="fixture-old", profile="14"):
        return SimpleNamespace(profile_id=profile, cookies={"c_user": uid, "xs": xs},
                               proxy="http://fixture-proxy:80", user_agent="Fixture")

    async def test_reloads_a_new_saved_session_for_the_same_facebook_identity(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context(xs="fixture-fresh")))
        self.assertTrue(await refresh_saved_auth_context(resolver,context))
        self.assertEqual(context.cookies["xs"],"fixture-fresh")
        resolver.resolve.assert_awaited_once_with("14")

    async def test_same_expired_session_is_not_reported_restored(self):
        context=self.context()
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=self.context()))
        self.assertFalse(await refresh_saved_auth_context(resolver,context))

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
