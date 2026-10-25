import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.private_session_probe import probe_document
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class SessionProbeTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(profile_id="4", cookies={"c_user":"123456", "xs":"saved-fixture"},
            proxy="http://user:proxy-fixture-secret@proxy.invalid:80", user_agent="fixture-UA",
            ad_accounts=[{"id":"act_111111", "business_id":"222222"}])

    def payload(self):
        return {"cookies":{"c_user":"123456", "xs":"import-fixture-secret", "datr":"fixture-device"},
            "ad_account_id":"111111", "business_id":"222222"}

    def factory(self, final=None, body=None, error=None):
        self.web = SimpleNamespace(close=AsyncMock(), fetch_text=AsyncMock(
            side_effect=error, return_value=(200, document() if body is None else body,
                final or "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222")))
        return Mock(return_value=self.web)

    async def test_one_get_uses_exact_saved_proxy_ua_and_does_not_change_input_or_profile(self):
        context, payload, factory = self.context(), self.payload(), self.factory()
        before_context, before_payload = copy.deepcopy(vars(context)), copy.deepcopy(payload)
        result = await probe_document(context, payload, web_factory=factory)
        profile = factory.call_args.args[0]
        self.assertEqual(profile.name, "4")
        self.assertEqual(profile.proxy, context.proxy)
        self.assertEqual(profile.user_agent, context.user_agent)
        self.assertEqual(profile.cookies, payload["cookies"])
        self.assertIsNot(profile.cookies, payload["cookies"])
        self.assertEqual(vars(context), before_context)
        self.assertEqual(payload, before_payload)
        self.web.fetch_text.assert_awaited_once_with(
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222",
            max_bytes=5_000_001, document_navigation=True)
        self.web.close.assert_awaited_once()
        self.assertTrue(self.web.private_only)
        self.assertTrue(result["bootstrap_context_confirmed"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["objects_created"])
        self.assertNotEqual(result["saved_auth_cookie_fingerprint"], result["evidence"]["auth_cookie_fingerprint"])
        for secret in ("saved-fixture", "import-fixture-secret", "proxy-fixture-secret", "synthetic-session-value"):
            self.assertNotIn(secret, str(result))

    async def test_real_login_reply_is_a_result_without_renewal_or_post(self):
        result = await probe_document(self.context(), self.payload(), web_factory=self.factory(
            final="https://business.facebook.com/business/loginpage/?next=secret", body="<h1>Login</h1>"))
        self.assertTrue(result["response_received"])
        self.assertEqual(result["evidence"]["final_path"], "/business/loginpage/")
        self.assertEqual(result["evidence"]["path_gate"], "BUSINESS_LOGIN_GATE")
        self.assertFalse(result["bootstrap_context_confirmed"])
        self.web.fetch_text.assert_awaited_once()

    async def test_another_actor_is_rejected_before_opening_transport(self):
        payload = self.payload(); payload["cookies"]["c_user"] = "999999"
        factory = self.factory()
        with self.assertRaises(ProvisioningError) as caught:
            await probe_document(self.context(), payload, web_factory=factory)
        self.assertEqual(caught.exception.code, "PRIVATE_SESSION_PROBE_ACTOR_MISMATCH")
        factory.assert_not_called()

    async def test_unrelated_account_or_business_is_rejected_before_network(self):
        for key in ("business_id", "ad_account_id"):
            payload = self.payload(); payload[key] = "999999"
            factory = self.factory()
            with self.assertRaises(ProvisioningError):
                await probe_document(self.context(), payload, web_factory=factory)
            factory.assert_not_called()

    async def test_proxy_is_mandatory_even_if_a_direct_test_context_exists(self):
        context = self.context(); context.proxy = None; context.direct_test_until = 9999999999
        factory = self.factory()
        with self.assertRaises(ProvisioningError):
            await probe_document(context, self.payload(), web_factory=factory)
        factory.assert_not_called()

    async def test_unreviewed_fields_invalid_cookies_and_headers_are_rejected(self):
        cases = []
        for extra in ("endpoint_url", "access_token", "password", "execution_mode"):
            value = self.payload(); value[extra] = "fixture"; cases.append(value)
        for cookies in ({}, [], {"c_user":"123456", "xs":""},
                {"c_user":"123456", "xs":"injected\r\nHeader: value"},
                {"c_user":"123456", "xs":"value; c_user=999999"},
                {"c_user":"123456", "xs":123}, {"c_user":"123456", "xs":"x"*8193}):
            value = self.payload(); value["cookies"] = cookies; cases.append(value)
        for payload in cases:
            factory = self.factory()
            with self.subTest(payload_keys=list(payload)), self.assertRaises(ProvisioningError):
                await probe_document(self.context(), payload, web_factory=factory)
            factory.assert_not_called()

    async def test_read_failure_is_not_retried_and_does_not_echo_exception_secrets(self):
        result = await probe_document(self.context(), self.payload(), web_factory=self.factory(
            error=TimeoutError("cookie=import-fixture-secret")))
        self.assertFalse(result["response_received"])
        self.assertEqual(result["error_type"], "TimeoutError")
        self.assertNotIn("import-fixture-secret", str(result))
        self.web.fetch_text.assert_awaited_once()
        self.web.close.assert_awaited_once()

    async def test_route_or_actor_mismatch_never_confirms_bootstrap(self):
        for final, body in (("https://business.facebook.com/latest/home", document()),
                (None, document("999999")),
                ("https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999999&business_id=222222", document())):
            result = await probe_document(self.context(), self.payload(), web_factory=self.factory(final, body))
            self.assertFalse(result["bootstrap_context_confirmed"])
