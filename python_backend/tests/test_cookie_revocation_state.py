import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from app.private_http import PrivateHttpClient, deleted_auth_cookie_names
from app.session import MetaSession


class DeletedAuthCookieNamesTests(unittest.TestCase):
    def test_root_scope_meta_deletion_is_detected_without_returning_values(self):
        headers = httpx.Headers({
            "Set-Cookie": "c_user=; Domain=.facebook.com; Path=/; Max-Age=0; Secure"
        })
        self.assertEqual(
            deleted_auth_cookie_names(headers, httpx.URL("https://business.facebook.com/")),
            {"c_user"},
        )

    def test_expired_root_auth_cookie_is_detected(self):
        expired = format_datetime(datetime(2000, 1, 1, tzinfo=timezone.utc), usegmt=True)
        headers = httpx.Headers({
            "Set-Cookie": f"xs=; Domain=facebook.com; Path=/; Expires={expired}; Secure"
        })
        self.assertEqual(
            deleted_auth_cookie_names(headers, httpx.URL("https://www.facebook.com/")),
            {"xs"},
        )

    def test_non_auth_or_unrepresentable_cookies_are_ignored(self):
        headers = httpx.Headers([
            ("Set-Cookie", "datr=; Domain=.facebook.com; Path=/; Max-Age=0"),
            ("Set-Cookie", "xs=; Domain=.facebook.com; Path=/login; Max-Age=0"),
            ("Set-Cookie", "c_user=; Path=/; Max-Age=0"),
        ])
        self.assertEqual(
            deleted_auth_cookie_names(headers, httpx.URL("https://business.facebook.com/")),
            set(),
        )

    def test_non_facebook_response_cannot_revoke_profile_auth(self):
        headers = httpx.Headers({
            "Set-Cookie": "xs=; Domain=.facebook.com; Path=/; Max-Age=0"
        })
        self.assertEqual(
            deleted_auth_cookie_names(headers, httpx.URL("https://example.com/")),
            set(),
        )


class RevocationEventHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_http_client_records_meta_root_cookie_deletion(self):
        client = PrivateHttpClient(
            profile_name="fixture-profile",
            cookies={"c_user": "123456789", "xs": "fixture-session"},
            proxy="http://127.0.0.1:1",
            user_agent="fixture-UA",
            timeout_seconds=5,
            pool_size=1,
        )
        self.addAsyncCleanup(client.close)
        response = httpx.Response(
            302,
            headers={"Set-Cookie": "xs=; Domain=.facebook.com; Path=/; Max-Age=0"},
            request=httpx.Request("GET", "https://business.facebook.com/latest/home"),
        )
        hook = client._client.event_hooks["response"][0]
        await hook(response)
        self.assertTrue(client.facebook_auth_cookie_revoked)


class MetaSessionRevocationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_meta_deletion_is_persisted_for_same_profile_session(self):
        cookies = {"c_user": "123456789", "xs": "fixture-session"}
        context = SimpleNamespace(
            profile_id="fixture-profile",
            cookies=dict(cookies),
            _loaded_cookies=dict(cookies),
            _cookie_revoker=AsyncMock(return_value=True),
            proxy="http://127.0.0.1:1",
            user_agent="fixture-UA",
        )

        class FakeFacebookSession:
            def __init__(self):
                self.profile = SimpleNamespace(cookies=dict(cookies))
                self.auth_cookie_revoked = False

            async def close(self):
                self.auth_cookie_revoked = True

        session = MetaSession(context)
        session._facebook_session = FakeFacebookSession()
        session._facebook_cookie_baseline = dict(cookies)
        session._facebook_profile_id = "fixture-profile"
        await session.close()

        context._cookie_revoker.assert_awaited_once_with(
            "fixture-profile", "123456789", "fixture-session"
        )

if __name__ == "__main__":
    unittest.main()
