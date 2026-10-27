import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from app.private_http import PrivateHttpClient, deleted_auth_cookie_names
from app.session import MetaSession, ProfileContextError, ProfileResolver


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
        client._client.cookies.extract_cookies(response)
        await hook(response)
        self.assertTrue(client.facebook_auth_cookie_revoked)
        self.assertNotIn("xs", client.facebook_cookie_snapshot())

        restored = httpx.Response(
            200,
            headers={"Set-Cookie": "xs=fixture-session-renewed; Domain=.facebook.com; Path=/; Secure"},
            request=httpx.Request("GET", "https://business.facebook.com/latest/home"),
        )
        client._client.cookies.extract_cookies(restored)
        await hook(restored)
        self.assertEqual(client.facebook_cookie_snapshot().get("c_user"), "123456789")
        self.assertEqual(client.facebook_cookie_snapshot().get("xs"), "fixture-session-renewed")
        self.assertFalse(client.facebook_auth_cookie_revoked)


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
                self.profile = SimpleNamespace(cookies={"c_user": cookies["c_user"]})
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



    async def test_restored_same_actor_cookie_session_is_refreshed_without_revocation(self):
        old = {"c_user": "123456789", "xs": "fixture-session"}
        refreshed = {"c_user": "123456789", "xs": "fixture-session-renewed"}
        persistor = AsyncMock(return_value=True)
        revoker = AsyncMock(return_value=True)
        context = SimpleNamespace(
            profile_id="fixture-profile",
            cookies=dict(old),
            _loaded_cookies=dict(old),
            _cookie_persistor=persistor,
            _cookie_revoker=revoker,
            proxy="http://127.0.0.1:1",
            user_agent="fixture-UA",
        )

        class FakeFacebookSession:
            def __init__(self):
                self.profile = SimpleNamespace(cookies=dict(refreshed))
                self.auth_cookie_revoked = False

            async def close(self):
                return None

        session = MetaSession(context)
        session._facebook_session = FakeFacebookSession()
        session._facebook_cookie_baseline = dict(old)
        session._facebook_profile_id = "fixture-profile"
        await session.close()

        revoker.assert_not_awaited()
        persistor.assert_awaited_once_with(
            "fixture-profile", "123456789", "fixture-session", refreshed
        )
        self.assertEqual(context.cookies, refreshed)


class ProfileResolverCookieRevalidationTests(unittest.IsolatedAsyncioTestCase):
    def _context(self):
        cookies = {"c_user": "123456789", "xs": "fixture-session"}
        return SimpleNamespace(
            profile_id="fixture-profile",
            cookies=dict(cookies),
            _loaded_cookies=dict(cookies),
            proxy="http://127.0.0.1:1",
            user_agent="fixture-UA",
            _cookie_persistor=AsyncMock(return_value=True),
            _cookie_revoker=AsyncMock(return_value=True),
        )

    async def test_resolve_revalidates_a_matching_marker_before_returning_context(self):
        context_cookies = {"c_user": "123456789", "xs": "fixture-session"}
        marker_hash = hashlib.sha256(
            context_cookies["xs"].encode("utf-8")
        ).hexdigest()
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver._fetch = AsyncMock(return_value={
            "cookies": context_cookies,
            "user_agent": "fixture-UA",
            "proxy": "http://127.0.0.1:1",
            "cookie_auth_revocation_xs_sha256": marker_hash,
        })
        resolver._revalidate_marked_cookie_auth = AsyncMock(return_value=None)

        resolved = await resolver.resolve("fixture-profile")

        resolver._revalidate_marked_cookie_auth.assert_awaited_once_with(
            resolved, marker_hash
        )

    async def test_positive_same_actor_read_only_probe_clears_exact_marker(self):
        context = self._context()
        marker_hash = hashlib.sha256(
            context.cookies["xs"].encode("utf-8")
        ).hexdigest()
        web = SimpleNamespace(
            profile=SimpleNamespace(name="fixture-profile"),
            fetch_text=AsyncMock(return_value=(
                200, "<fixture-document>", "https://business.facebook.com/latest/home/"
            )),
        )
        meta_session = SimpleNamespace(
            facebook_web=AsyncMock(return_value=web),
            close=AsyncMock(),
        )
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver.persist_cookie_auth_verification = AsyncMock(return_value=True)

        with patch("app.session.MetaSession", return_value=meta_session), patch(
            "app.private_ads_http._bootstrap_evidence",
            return_value={"final_host": "business.facebook.com", "document_actor": "matches"},
        ):
            await resolver._revalidate_marked_cookie_auth(context, marker_hash)

        web.fetch_text.assert_awaited_once_with(
            "https://business.facebook.com/latest/home/",
            max_bytes=5_000_000,
            document_navigation=True,
        )
        resolver.persist_cookie_auth_verification.assert_awaited_once_with(
            "fixture-profile", "123456789", marker_hash
        )



    async def test_positive_document_does_not_clear_marker_if_meta_removed_final_auth_cookie(self):
        context = self._context()
        marker_hash = hashlib.sha256(
            context.cookies["xs"].encode("utf-8")
        ).hexdigest()
        web = SimpleNamespace(
            profile=SimpleNamespace(name="fixture-profile"),
            auth_cookie_revoked=False,
            fetch_text=AsyncMock(return_value=(
                200, "<fixture-document>", "https://business.facebook.com/latest/home/"
            )),
        )

        class FakeMetaSession:
            async def facebook_web(self):
                return web

            async def close(self):
                web.auth_cookie_revoked = True

        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver.persist_cookie_auth_verification = AsyncMock(return_value=True)

        with patch("app.session.MetaSession", return_value=FakeMetaSession()), patch(
            "app.private_ads_http._bootstrap_evidence",
            return_value={"final_host": "business.facebook.com", "document_actor": "matches"},
        ):
            with self.assertRaises(ProfileContextError) as raised:
                await resolver._revalidate_marked_cookie_auth(context, marker_hash)

        self.assertEqual(raised.exception.code, "SESSION_EXPIRED")
        resolver.persist_cookie_auth_verification.assert_not_awaited()

    async def test_verified_previous_same_actor_session_is_restored(self):
        context = self._context()
        current_hash = hashlib.sha256(context.cookies["xs"].encode("utf-8")).hexdigest()
        previous_cookies = {"c_user": "123456789", "xs": "previous-session"}
        previous_hash = hashlib.sha256(previous_cookies["xs"].encode("utf-8")).hexdigest()
        resolver = ProfileResolver(None, None)
        resolver.load_previous_profile_cookies = AsyncMock(
            return_value=(previous_cookies, previous_hash)
        )
        resolver.persist_verified_previous_profile_cookies = AsyncMock(return_value=True)
        probe_webs = []

        class FakeMetaSession:
            def __init__(self, candidate):
                self.candidate = candidate

            async def facebook_web(self):
                web = SimpleNamespace(
                    profile=SimpleNamespace(
                        name=self.candidate.profile_id,
                        cookies=dict(self.candidate.cookies),
                    ),
                    auth_cookie_revoked=False,
                    fetch_text=AsyncMock(return_value=(
                        200, "<verified-business-home>",
                        "https://business.facebook.com/latest/home/",
                    )),
                )
                probe_webs.append(web)
                return web

            async def close(self):
                return None

        with patch("app.session.MetaSession", FakeMetaSession), patch(
            "app.private_ads_http._bootstrap_evidence",
            return_value={"final_host": "business.facebook.com", "document_actor": "matches"},
        ):
            restored = await resolver._restore_verified_previous_profile_cookies(
                context, current_hash
            )

        self.assertTrue(restored)
        resolver.load_previous_profile_cookies.assert_awaited_once_with(
            "fixture-profile", "123456789", current_hash
        )
        resolver.persist_verified_previous_profile_cookies.assert_awaited_once_with(
            "fixture-profile", "123456789", current_hash, previous_hash, previous_cookies
        )
        probe_webs[0].fetch_text.assert_awaited_once_with(
            "https://business.facebook.com/latest/home/",
            max_bytes=5_000_000,
            document_navigation=True,
        )
        self.assertEqual(context.cookies, previous_cookies)
        self.assertEqual(context._loaded_cookies, previous_cookies)

    async def test_previous_session_requiring_login_is_never_persisted(self):
        context = self._context()
        current_hash = hashlib.sha256(context.cookies["xs"].encode("utf-8")).hexdigest()
        previous_cookies = {"c_user": "123456789", "xs": "previous-session"}
        previous_hash = hashlib.sha256(previous_cookies["xs"].encode("utf-8")).hexdigest()
        resolver = ProfileResolver(None, None)
        resolver.load_previous_profile_cookies = AsyncMock(
            return_value=(previous_cookies, previous_hash)
        )
        resolver.persist_verified_previous_profile_cookies = AsyncMock(return_value=True)

        class FakeMetaSession:
            def __init__(self, candidate):
                self.candidate = candidate

            async def facebook_web(self):
                return SimpleNamespace(
                    profile=SimpleNamespace(
                        name=self.candidate.profile_id,
                        cookies=dict(self.candidate.cookies),
                    ),
                    auth_cookie_revoked=False,
                    fetch_text=AsyncMock(return_value=(
                        200, "<login-page>",
                        "https://business.facebook.com/business/loginpage/",
                    )),
                )

            async def close(self):
                return None

        with patch("app.session.MetaSession", FakeMetaSession):
            restored = await resolver._restore_verified_previous_profile_cookies(
                context, current_hash
            )

        self.assertFalse(restored)
        resolver.persist_verified_previous_profile_cookies.assert_not_awaited()
        self.assertEqual(context.cookies["xs"], "fixture-session")

    async def test_previous_session_with_different_actor_is_never_probed_or_persisted(self):
        context = self._context()
        current_hash = hashlib.sha256(context.cookies["xs"].encode("utf-8")).hexdigest()
        resolver = ProfileResolver(None, None)
        resolver.load_previous_profile_cookies = AsyncMock(return_value=(
            {"c_user": "999999999", "xs": "other-actor-session"},
            hashlib.sha256(b"other-actor-session").hexdigest(),
        ))
        resolver.persist_verified_previous_profile_cookies = AsyncMock(return_value=True)
        meta_session_factory = Mock(return_value=SimpleNamespace())

        with patch("app.session.MetaSession", meta_session_factory):
            restored = await resolver._restore_verified_previous_profile_cookies(
                context, current_hash
            )

        self.assertFalse(restored)
        meta_session_factory.assert_not_called()
        resolver.persist_verified_previous_profile_cookies.assert_not_awaited()

    async def test_business_home_gate_does_not_globally_block_profile(self):
        cookies = {"c_user": "123456789", "xs": "fixture-session"}
        marker_hash = hashlib.sha256(cookies["xs"].encode("utf-8")).hexdigest()
        web = SimpleNamespace(
            profile=SimpleNamespace(name="fixture-profile"),
            fetch_text=AsyncMock(return_value=(
                200, "<fixture-login-page>",
                "https://business.facebook.com/business/loginpage/"
            )),
        )
        meta_session = SimpleNamespace(
            facebook_web=AsyncMock(return_value=web),
            close=AsyncMock(),
        )
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver._fetch = AsyncMock(return_value={
            "cookies": cookies,
            "user_agent": "fixture-UA",
            "proxy": "http://127.0.0.1:1",
            "cookie_auth_revocation_xs_sha256": marker_hash,
        })
        resolver.persist_cookie_auth_verification = AsyncMock(return_value=True)
        resolver._restore_verified_previous_profile_cookies = AsyncMock(return_value=False)

        with patch("app.session.MetaSession", return_value=meta_session):
            resolved = await resolver.resolve("fixture-profile")

        self.assertEqual(resolved.cookies, cookies)
        web.fetch_text.assert_awaited_once()
        resolver._restore_verified_previous_profile_cookies.assert_awaited_once_with(
            resolved, marker_hash
        )
        resolver.persist_cookie_auth_verification.assert_not_awaited()

    async def test_facebook_login_page_still_blocks_marked_profile(self):
        cookies = {"c_user": "123456789", "xs": "fixture-session"}
        marker_hash = hashlib.sha256(cookies["xs"].encode("utf-8")).hexdigest()
        web = SimpleNamespace(
            profile=SimpleNamespace(name="fixture-profile"),
            fetch_text=AsyncMock(return_value=(
                200, "<fixture-login-page>", "https://www.facebook.com/login/"
            )),
        )
        meta_session = SimpleNamespace(
            facebook_web=AsyncMock(return_value=web),
            close=AsyncMock(),
        )
        resolver = ProfileResolver("https://resolver.invalid/profile", "fixture-key")
        resolver._fetch = AsyncMock(return_value={
            "cookies": cookies,
            "user_agent": "fixture-UA",
            "proxy": "http://127.0.0.1:1",
            "cookie_auth_revocation_xs_sha256": marker_hash,
        })
        resolver.persist_cookie_auth_verification = AsyncMock(return_value=True)

        with patch("app.session.MetaSession", return_value=meta_session):
            with self.assertRaises(ProfileContextError) as raised:
                await resolver.resolve("fixture-profile")

        self.assertEqual(raised.exception.code, "SESSION_EXPIRED")
        self.assertFalse(raised.exception.retryable)
        resolver.persist_cookie_auth_verification.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
