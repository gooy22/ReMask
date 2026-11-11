from datetime import datetime, timezone
import unittest

import httpx

from app.private_cookie_directives import auth_cookie_deletes_profile_root, auth_cookie_directive
from app.private_http import PrivateHttpClient


class AuthCookieDirectiveTests(unittest.TestCase):
    NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def test_nonempty_auth_cookies_without_expiry_are_not_deletions(self):
        self.assertEqual(auth_cookie_directive("xs=fixture-value; Path=/"), ("xs", False))

    def test_empty_or_deleted_values_without_expiry_are_not_deletions(self):
        self.assertEqual(auth_cookie_directive("c_user=; Domain=.facebook.com; Path=/"), ("c_user", False))
        self.assertEqual(auth_cookie_directive("xs=deleted; Domain=.facebook.com; Path=/"), ("xs", False))

    def test_empty_value_with_positive_max_age_is_not_a_deletion(self):
        self.assertEqual(auth_cookie_directive(
            "xs=; Domain=.facebook.com; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=60",
            now=self.NOW,
        ), ("xs", False))

    def test_auth_cookie_names_are_case_sensitive(self):
        self.assertIsNone(auth_cookie_directive("C_USER=; Domain=.facebook.com; Path=/; Max-Age=0"))

    def test_nonempty_cookie_with_past_expires_is_a_deletion(self):
        result = auth_cookie_directive(
            "c_user=fixture-actor; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/",
            now=self.NOW,
        )
        self.assertEqual(result, ("c_user", True))

    def test_negative_max_age_is_a_deletion(self):
        result = auth_cookie_directive("xs=fixture-value; Max-Age=-1; Path=/", now=self.NOW)
        self.assertEqual(result, ("xs", True))

    def test_future_expires_is_not_a_deletion(self):
        result = auth_cookie_directive(
            "xs=fixture-value; Expires=Thu, 01 Jan 2099 00:00:00 GMT; Path=/",
            now=self.NOW,
        )
        self.assertEqual(result, ("xs", False))

    def test_positive_max_age_overrides_past_expires(self):
        result = auth_cookie_directive(
            "xs=fixture-value; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=60; Path=/",
            now=self.NOW,
        )
        self.assertEqual(result, ("xs", False))

    def test_malformed_max_age_falls_back_to_expires(self):
        result = auth_cookie_directive(
            "xs=fixture-value; Max-Age=soon; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/",
            now=self.NOW,
        )
        self.assertEqual(result, ("xs", True))

    def test_parser_returns_only_cookie_name_and_deletion_state(self):
        result = auth_cookie_directive("xs=never-return-this; Max-Age=-1; Path=/", now=self.NOW)
        self.assertEqual(result, ("xs", True))
        self.assertNotIn("never-return-this", str(result))

    def test_unrelated_cookie_is_ignored(self):
        self.assertIsNone(auth_cookie_directive("datr=fixture-value; Max-Age=-1"))

    def test_only_expired_shared_root_directive_targets_imported_profile_cookie(self):
        self.assertTrue(auth_cookie_deletes_profile_root(
            "xs=; Domain=.facebook.com; Path=/; Max-Age=0",
            response_host="business.facebook.com", response_path="/latest/home",
            now=self.NOW,
        ))
        self.assertFalse(auth_cookie_deletes_profile_root(
            "xs=; Path=/; Max-Age=0",
            response_host="business.facebook.com", response_path="/latest/home",
            now=self.NOW,
        ))
        self.assertFalse(auth_cookie_deletes_profile_root(
            "xs=; Domain=business.facebook.com; Path=/; Max-Age=0",
            response_host="business.facebook.com", response_path="/latest/home",
            now=self.NOW,
        ))
        self.assertFalse(auth_cookie_deletes_profile_root(
            "xs=; Domain=.facebook.com; Path=/business; Max-Age=0",
            response_host="business.facebook.com", response_path="/latest/home",
            now=self.NOW,
        ))
        self.assertFalse(auth_cookie_deletes_profile_root(
            "xs=; Domain=.facebook.com; Path=/; Max-Age=0",
            response_host="evil.example", response_path="/",
            now=self.NOW,
        ))

    def test_missing_path_uses_the_response_default_path(self):
        root = "xs=; Domain=facebook.com; Max-Age=0"
        self.assertTrue(auth_cookie_deletes_profile_root(
            root, response_host="facebook.com", response_path="/", now=self.NOW,
        ))
        self.assertFalse(auth_cookie_deletes_profile_root(
            root, response_host="business.facebook.com", response_path="/latest/home", now=self.NOW,
        ))

    def test_ambiguous_duplicate_scope_attributes_do_not_mark_profile_deletion(self):
        self.assertFalse(auth_cookie_deletes_profile_root(
            "xs=; Domain=.facebook.com; Domain=business.facebook.com; Path=/; Max-Age=0",
            response_host="business.facebook.com", response_path="/", now=self.NOW,
        ))


class AuthCookieDeletionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_auth_directives_are_recorded_without_cookie_values(self):
        client = object.__new__(PrivateHttpClient)
        client.profile_name = 'fixture'
        client._trusted_meta_xs_fingerprints = set()
        client._meta_redirect_auth_cookie_directive_pending = False
        client.meta_auth_cookie_deletions = set()

        request = httpx.Request('GET', 'https://business.facebook.com/latest/home')
        response = httpx.Response(302, request=request, headers=[
            ('Location', 'https://business.facebook.com/business/loginpage/'),
            ('Set-Cookie', 'c_user=synthetic-actor; Domain=.facebook.com; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure'),
            ('Set-Cookie', 'xs=synthetic-expired-xs; Domain=.facebook.com; Path=/; Max-Age=-1; Secure'),
        ])
        await client._track_meta_xs_cookie_updates(response)

        self.assertEqual(client.meta_auth_cookie_deletions, {'c_user', 'xs'})
        self.assertEqual(response.extensions['remask_meta_auth_cookie_directive']['deletions'],
            ['c_user', 'xs'])
        self.assertNotIn('synthetic-actor', str(response.extensions))
        self.assertNotIn('synthetic-expired-xs', str(response.extensions))
        # A recorded server deletion prevents the existing cookie restoration path.
        client.restore_profile_cookies()

    async def test_scoped_cookie_deletions_do_not_revoke_imported_profile_cookies(self):
        client = object.__new__(PrivateHttpClient)
        client.profile_name = 'fixture'
        client._trusted_meta_xs_fingerprints = set()
        client._meta_redirect_auth_cookie_directive_pending = False
        client.meta_auth_cookie_deletions = set()

        request = httpx.Request('GET', 'https://business.facebook.com/latest/home')
        response = httpx.Response(200, request=request, headers=[
            ('Set-Cookie', 'c_user=; Path=/; Max-Age=0; Secure'),
            ('Set-Cookie', 'xs=; Domain=.facebook.com; Path=/business; Max-Age=0; Secure'),
        ])
        await client._track_meta_xs_cookie_updates(response)

        directive = response.extensions['remask_meta_auth_cookie_directive']
        self.assertEqual(directive['deletions'], [])
        self.assertEqual(directive['observed_deletions'], ['c_user', 'xs'])
        self.assertEqual(client.meta_auth_cookie_deletions, set())
        self.assertFalse(client._meta_redirect_auth_cookie_directive_pending)

    async def test_empty_auth_value_is_recorded_as_a_set_not_a_deletion(self):
        client = object.__new__(PrivateHttpClient)
        client.profile_name = 'fixture'
        client._trusted_meta_xs_fingerprints = set()
        client._meta_redirect_auth_cookie_directive_pending = False
        client.meta_auth_cookie_deletions = set()

        request = httpx.Request('GET', 'https://business.facebook.com/latest/home')
        response = httpx.Response(302, request=request, headers=[
            ('Location', 'https://business.facebook.com/latest/home'),
            ('Set-Cookie', 'c_user=; Domain=.facebook.com; Path=/; Secure'),
        ])
        await client._track_meta_xs_cookie_updates(response)

        directive = response.extensions['remask_meta_auth_cookie_directive']
        self.assertEqual(directive['sets'], ['c_user'])
        self.assertEqual(directive['deletions'], [])
        self.assertEqual(directive['observed_deletions'], [])
        self.assertEqual(client.meta_auth_cookie_deletions, set())


if __name__ == "__main__":
    unittest.main()
