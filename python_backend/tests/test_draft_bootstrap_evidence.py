import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_document import _auth_gate
from app.private_ads_http import AdsManagerPrivateHttp, _bootstrap_evidence
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class BootstrapEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def profile(self):
        return SimpleNamespace(name="4", proxy="http://user:proxy-secret@proxy.invalid:80",
            cookies={"c_user": "123456", "xs": "fixture-xs-secret", "fr": "fixture-fr-secret"})

    def evidence(self, url, body=""):
        return _bootstrap_evidence(url, body, self.profile(), "123456")

    def test_actual_business_login_route_is_distinguished_from_query_only_match(self):
        actual = "https://business.facebook.com/business/loginpage/?next=%2Fadsmanager"
        false_match = "https://adsmanager.facebook.com/adsmanager/manage/campaigns?next=https://business.facebook.com/business/loginpage/"
        self.assertEqual(_auth_gate(actual, ""), "BUSINESS_LOGIN_GATE")
        self.assertEqual(_auth_gate(false_match, ""), "")
        self.assertEqual(self.evidence(actual)["final_path"], "/business/loginpage/")
        self.assertEqual(self.evidence(actual)["path_gate"], "BUSINESS_LOGIN_GATE")
        self.assertEqual(self.evidence(false_match)["path_gate"], "none")

    def test_login_form_is_independent_of_route_and_body_actor(self):
        body = '<form id="login_form" action="/login/"><input name="pass"></form>'
        evidence = self.evidence("https://business.facebook.com/latest/home", body)
        self.assertTrue(evidence["login_form"])
        self.assertEqual(evidence["path_gate"], "none")
        self.assertEqual(evidence["document_actor"], "absent")

    def test_body_actor_matches_mismatches_and_logged_out_are_distinct(self):
        for actor, state in (("123456", "matches"), ("999999", "mismatch"), ("0", "logged_out")):
            with self.subTest(actor=actor):
                self.assertEqual(self.evidence("https://adsmanager.facebook.com/", document(actor))["document_actor"], state)

    def test_script_text_and_return_query_do_not_establish_an_actual_login_form(self):
        for body in ('<script>const example = `<form id="login_form" action="/login/">`;</script>',
                     '<form action="/settings?next=/login/"></form>'):
            self.assertFalse(self.evidence("https://business.facebook.com/latest/home", body)["login_form"])

    def test_queries_fragments_and_host_lookalikes_do_not_establish_auth_routes(self):
        from app.private_inventory import _auth_gate as inventory_gate
        urls = (
            'https://adsmanager.facebook.com/adsmanager/manage/campaigns?next=/login/',
            'https://business.facebook.com/latest/home?next=/checkpoint/',
            'https://business.facebook.com/latest/home#https://business.facebook.com/business/loginpage/',
            'https://business.facebook.com/latest/home?next=https%3A%2F%2Fwww.facebook.com%2Flogin%2F',
            'https://business.facebook.com.unreviewed.invalid/business/loginpage/',
            'https://business.facebook.com@unreviewed.invalid/business/loginpage/',
            'https://business.facebook.com/settings/login_preferences',
            'https://business.facebook.com/business/loginpage-preview/',
            'https://[invalid/login/',
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(_auth_gate(url, ''), '')
                self.assertEqual(inventory_gate(url, ''), '')

    def test_real_routes_and_forms_remain_blocked_in_both_callers(self):
        from app.private_inventory import _auth_gate as inventory_gate
        cases = (
            ('https://business.facebook.com/business/loginpage/', '', 'BUSINESS_LOGIN_GATE'),
            ('https://BUSINESS.FACEBOOK.COM/business/loginpage?next=/checkpoint/', '', 'BUSINESS_LOGIN_GATE'),
            ('https://www.facebook.com/login.php?next=/checkpoint/', '', 'META_LOGIN_REQUIRED'),
            ('https://www.facebook.com/business/loginpage/', '', 'META_LOGIN_REQUIRED'),
            ('https://m.facebook.com/login/device-based/regular/login/', '', 'META_LOGIN_REQUIRED'),
            ('https://www.facebook.com/checkpoint/?next=/login/', '', 'CHECKPOINT_REQUIRED'),
            ('https://business.facebook.com/latest/home', '<FORM ID=login_form>', 'META_LOGIN_REQUIRED'),
            ('https://business.facebook.com/latest/home', '<form action="https://www.facebook.com/login/">', 'META_LOGIN_REQUIRED'),
        )
        for url, body, expected in cases:
            with self.subTest(url=url, body=body):
                self.assertEqual(_auth_gate(url, body), expected)
                self.assertEqual(inventory_gate(url, body), expected)

    def test_inert_markup_and_form_return_parameters_are_not_login_forms(self):
        for body in (
            '<script>const login = `<form id="login_form">`;</script>',
            '<!-- <form action="/login.php"> -->',
            '<template><form id="login_form"></form></template>',
            '<textarea><form action="/login/"></form></textarea>',
            '<form action="/settings?next=/login/">',
            '<form action="/settings#https://www.facebook.com/login/">',
            '<form action="/login_preferences">',
            '<form action="https://unreviewed.invalid/login/">',
            '<form action="https://[invalid/login/">',
        ):
            with self.subTest(body=body):
                self.assertEqual(_auth_gate('https://business.facebook.com/latest/home', body), '')
                self.assertFalse(self.evidence('https://business.facebook.com/latest/home', body)['login_form'])
        body = '<template><form id="login_form"></form></template><form action="/login/">'
        self.assertEqual(_auth_gate('https://business.facebook.com/latest/home', body), 'META_LOGIN_REQUIRED')

    def test_request_echo_and_duplicate_json_cannot_confirm_actor(self):
        for body in (json.dumps({"request": json.loads(document())}),
                     '{"define":[],"define":[["CurrentUserInitialData",[],{"USER_ID":"123456"}]]}'):
            self.assertEqual(self.evidence("https://business.facebook.com/", body)["document_actor"], "absent")

    def test_unknown_hosts_paths_queries_and_credentials_never_leave_boundary(self):
        for url in ("https://unreviewed.invalid/private-route-secret?xs=query-secret",
                    "https://business.facebook.com/private-route-secret?xs=query-secret#fragment-secret"):
            evidence = self.evidence(url, document(token="fixture-token-secret"))
            serialized = json.dumps(evidence)
            self.assertEqual(evidence["final_path"], "unreviewed")
            for secret in ("unreviewed.invalid", "private-route-secret", "query-secret", "fragment-secret",
                           "fixture-xs-secret", "fixture-fr-secret", "proxy-secret", "fixture-token-secret", "123456"):
                self.assertNotIn(secret, serialized)

    def test_auth_fingerprint_compares_auth_cookies_only_and_never_changes_them(self):
        profile = self.profile()
        before = dict(profile.cookies)
        original = _bootstrap_evidence("https://business.facebook.com/", "", profile, "123456")
        self.assertEqual(profile.cookies, before)
        self.assertRegex(original["auth_cookie_fingerprint"], r"^[a-f0-9]{64}$")
        profile.cookies["fr"] = "another-fixture"
        self.assertEqual(_bootstrap_evidence("https://business.facebook.com/", "", profile, "123456")["auth_cookie_fingerprint"], original["auth_cookie_fingerprint"])
        profile.cookies["xs"] = "new-fixture-session"
        self.assertNotEqual(_bootstrap_evidence("https://business.facebook.com/", "", profile, "123456")["auth_cookie_fingerprint"], original["auth_cookie_fingerprint"])

    async def test_rejected_review_logs_evidence_without_renewal_post_or_changed_error(self):
        web = SimpleNamespace(profile=self.profile(), private_only=False,
            fetch_text=AsyncMock(return_value=(200, document("0"),
                "https://business.facebook.com/business/loginpage/?next=query-secret")),
            _ensure_session=AsyncMock(side_effect=AssertionError("no further network")),
            refresh_reviewed_modules=AsyncMock())
        before = dict(web.profile.cookies)
        with self.assertLogs("remask.private_ads_http", "INFO") as logs:
            with self.assertRaises(ProvisioningError) as caught:
                await AdsManagerPrivateHttp.open(web, account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE")
        web.fetch_text.assert_awaited_once_with("https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222", max_bytes=5_000_001, document_navigation=True, retry_timeout=True)
        web._ensure_session.assert_not_called()
        web.refresh_reviewed_modules.assert_not_called()
        self.assertEqual(web.profile.cookies, before)
        lines = "\n".join(logs.output)
        self.assertIn('"final_path":"/business/loginpage/"', lines)
        self.assertIn('"document_actor":"logged_out"', lines)
        for secret in ("query-secret", "fixture-xs-secret", "proxy-secret"):
            self.assertNotIn(secret, lines)

    async def test_accepted_bootstrap_preserves_original_verified_context(self):
        profile = self.profile()
        profile.user_agent = "fixture-UA"
        url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222"
        web = SimpleNamespace(profile=profile, private_only=False,
            fetch_text=AsyncMock(return_value=(200, document(), url)))
        with self.assertLogs("remask.private_ads_http", "INFO"):
            client = await AdsManagerPrivateHttp.open(web, account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual(client.context.actor_id, "123456")
        self.assertEqual(client.context.account_id, "111111")
        self.assertEqual(client.context.business_id, "222222")
        self.assertFalse(client.read_only)

    async def test_return_url_cannot_reject_a_scoped_authenticated_bootstrap(self):
        url = ('https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222'
               '&next=https://business.facebook.com/business/loginpage/&return=/checkpoint/')
        web = SimpleNamespace(profile=self.profile(), private_only=False,
            fetch_text=AsyncMock(return_value=(200, document(), url)),
            _ensure_session=AsyncMock(side_effect=AssertionError('No renewal or POST')))
        with self.assertLogs('remask.private_ads_http', 'INFO'):
            client = await AdsManagerPrivateHttp.open(web, account_id='111111', business_id='222222', actor_id='123456')
        self.assertEqual(client.context.actor_id, '123456')
        self.assertEqual(client.context.account_id, '111111')
        self.assertEqual(client.context.business_id, '222222')
        web.fetch_text.assert_awaited_once()
        web._ensure_session.assert_not_awaited()

    async def test_query_fix_never_relaxes_account_business_actor_or_origin_scope(self):
        base = 'https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222&next=/login/'
        for url, body in (
            (base.replace('business_id=222222', 'business_id=999999'), document()),
            (base.replace('adsmanager.facebook.com', 'unreviewed.invalid'), document()),
            (base, document('999999')),
        ):
            with self.subTest(url=url):
                web = SimpleNamespace(profile=self.profile(), fetch_text=AsyncMock(return_value=(200, body, url)),
                    _ensure_session=AsyncMock(side_effect=AssertionError('No POST or renewal')))
                with self.assertRaises(ProvisioningError):
                    await AdsManagerPrivateHttp.open(web, account_id='111111', business_id='222222', actor_id='123456')
                web._ensure_session.assert_not_awaited()

    async def test_diagnostic_parser_failure_cannot_change_gate_or_leak_exception_text(self):
        for final, body, rejected in (("https://business.facebook.com/business/loginpage/", "", True),
                ("https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222", document(), False)):
            web = SimpleNamespace(profile=self.profile(), fetch_text=AsyncMock(return_value=(200, body, final)))
            with patch("app.private_ads_http._bootstrap_evidence", side_effect=AssertionError("body-secret")):
                with self.assertLogs("remask.private_ads_http", "INFO") as logs:
                    if rejected:
                        with self.assertRaises(ProvisioningError) as caught:
                            await AdsManagerPrivateHttp.open(web, account_id="111111", business_id="222222", actor_id="123456")
                        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE")
                    else:
                        client = await AdsManagerPrivateHttp.open(web, account_id="111111", business_id="222222", actor_id="123456")
                        self.assertEqual(client.context.actor_id, "123456")
            self.assertIn('"evidence_error":"unavailable"', "\n".join(logs.output))
            self.assertNotIn("body-secret", "\n".join(logs.output))
