import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app.business_login_gate_http import (
    continue_business_login_gate,
    extract_business_login_continuation,
    is_business_login_gate_url,
)


GATE_URL = "https://business.facebook.com/business/loginpage?next=%2Flatest%2Fhome"
RETURN_URL = "https://business.facebook.com/latest/home"
ADS_MANAGER_RETURN_URL = (
    "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
    "?act=111111&business_id=222222"
)


def _native_gate_body(**overrides):
    from app.private_business_login import ROOT, SOURCE

    props = {
        "loginOptions": ["FB", "SSO"],
        "saveSession": False,
        "referrerURI": ADS_MANAGER_RETURN_URL,
        "requestId": "native-request-id",
    }
    props.update(overrides)
    row = [
        "react-xhp",
        "constructAndRenderComponentIntoComment_DO_NOT_USE",
        [ROOT],
        [{"constructor": {"__m": ROOT}, "props": props}],
    ]
    return (
        f'<script src="{SOURCE}"></script><script>server.handle('
        + json.dumps({"require": [row]})
        + ");</script>"
    )


class GateContinuationParserTests(unittest.TestCase):
    def test_accepts_one_facebook_continuation_form(self):
        body = """
        <form id="handoff" action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf-token">
          <button type="submit" name="continue" value="1">Continue with Facebook</button>
        </form>
        """
        result = extract_business_login_continuation(body, GATE_URL)
        self.assertTrue(is_business_login_gate_url(GATE_URL))
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["url"], "https://business.facebook.com/business/loginpage/continue")
        self.assertEqual(result["fields"], [("fb_dtsg", "csrf-token"), ("continue", "1")])
        self.assertEqual(result["label_kind"], "facebook_continue")

    def test_accepts_russian_meta_business_suite_continuation(self):
        body = """
        <form id="handoff" action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf-token">
          <button type="submit" name="continue" value="1">Продолжить с аккаунтом Facebook</button>
        </form>
        """
        result = extract_business_login_continuation(body, GATE_URL)
        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["url"], "https://business.facebook.com/business/loginpage/continue")
        self.assertEqual(result["fields"], [("fb_dtsg", "csrf-token"), ("continue", "1")])
        self.assertEqual(result["label_kind"], "facebook_continue")

    def test_accepts_russian_form_backed_aria_button(self):
        body = """
        <form id="handoff" action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf-token">
          <div role="button" aria-label="Продолжить с аккаунтом Facebook"
               name="continue" value="1">Продолжить с аккаунтом Facebook</div>
        </form>
        """
        result = extract_business_login_continuation(body, GATE_URL)
        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["url"], "https://business.facebook.com/business/loginpage/continue")
        self.assertEqual(result["fields"], [("fb_dtsg", "csrf-token"), ("continue", "1")])
        self.assertEqual(result["label_kind"], "facebook_continue")

    def test_ignores_checkpoint_markers_inside_scripts(self):
        body = """
        <script>const route = "/checkpoint/";</script>
        <form action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf">
          <button>Continue with Facebook</button>
        </form>
        """
        result = extract_business_login_continuation(body, GATE_URL)
        self.assertIsNotNone(result)
        self.assertEqual(result["label_kind"], "facebook_continue")

    def test_refuses_login_credentials_and_checkpoints(self):
        credential_form = """
        <form action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf">
          <input type="password" name="pass">
          <button>Continue with Facebook</button>
        </form>
        """
        checkpoint = "<p>Enter security code</p><button>Continue with Facebook</button>"
        self.assertIsNone(extract_business_login_continuation(credential_form, GATE_URL))
        self.assertIsNone(extract_business_login_continuation(checkpoint, GATE_URL))

    def test_extracts_reviewed_js_only_facebook_continuation(self):
        from app.private_business_login import (
            SOURCE, SOURCE_GB, native_business_login_continuation,
        )

        body = _native_gate_body()
        self.assertIsNone(extract_business_login_continuation(body, GATE_URL))
        continuation = native_business_login_continuation(body, ADS_MANAGER_RETURN_URL)
        self.assertTrue(is_business_login_gate_url(GATE_URL))
        self.assertEqual(
            urlsplit(continuation).path,
            "/business/loginrequest/fb/",
        )
        query = parse_qs(urlsplit(continuation).query)
        next_url = query["next"][0]
        next_query = parse_qs(urlsplit(next_url).query)
        self.assertEqual(query["f3_request_id"], ["native-request-id"])
        self.assertEqual(next_query["act"], ["111111"])
        self.assertEqual(next_query["business_id"], ["222222"])
        self.assertEqual(
            next_query["biz_login_source"],
            ["biz_unified_f3_fb_login_button"],
        )
        self.assertIsNotNone(
            native_business_login_continuation(
                body.replace(SOURCE, SOURCE_GB), ADS_MANAGER_RETURN_URL,
            )
        )
        # Meta rotates the immutable CDN bundle hash. Preserve the exact host,
        # path family, server-rendered props, and destination checks.
        rotated_source = SOURCE.replace("Ok9Q1EXGe9g.js", "NewHash20261030.js")
        self.assertIsNotNone(
            native_business_login_continuation(
                body.replace(SOURCE, rotated_source), ADS_MANAGER_RETURN_URL,
            )
        )
        self.assertIsNone(
            native_business_login_continuation(
                body.replace(SOURCE, SOURCE + "?changed=1"),
                ADS_MANAGER_RETURN_URL,
            )
        )
        self.assertIsNone(
            native_business_login_continuation(
                body.replace(SOURCE, "https://evil.example/rsrc.php/v4/yI/l/en_US-j/NewHash.js"),
                ADS_MANAGER_RETURN_URL,
            )
        )
        self.assertIsNone(
            native_business_login_continuation(
                body.replace(SOURCE, "https://static.xx.fbcdn.net/assets/login.js"),
                ADS_MANAGER_RETURN_URL,
            )
        )
        self.assertIsNone(
            native_business_login_continuation(
                body, ADS_MANAGER_RETURN_URL.replace("111111", "999999"),
            )
        )

    def test_refuses_new_consent_and_human_verification(self):
        for notice in ("By continuing, you agree to new terms.",
                       "By clicking continue, you agree to the terms.",
                       "By continuing you agree to new terms.",
                       "By logging in, you agree to the new terms.",
                       "Accept the terms to continue.", "Verify you are human.",
                       "Complete the security check.", "CAPTCHA"):
            body = notice + '<a href="/business/loginrequest/fb/">Continue with Facebook</a>'
            self.assertIsNone(extract_business_login_continuation(body, GATE_URL))

    def test_refuses_ambiguous_or_cross_origin_continuations(self):
        ambiguous = """
        <button>Continue with Facebook</button>
        <button>Continue with Facebook</button>
        """
        external = """
        <a href="https://example.invalid/continue">Continue with Facebook</a>
        """
        self.assertIsNone(extract_business_login_continuation(ambiguous, GATE_URL))
        self.assertIsNone(extract_business_login_continuation(external, GATE_URL))

    def test_refuses_unexpected_gate_hosts_and_hidden_controls(self):
        body = """
        <form action="/business/loginpage/continue">
          <button>Continue with Facebook</button>
        </form>
        """
        self.assertIsNone(extract_business_login_continuation(
            body, "https://evil.example/business/loginpage"))
        hidden = """
        <form action="/business/loginpage/continue">
          <button style="display:none">Continue with Facebook</button>
        </form>
        """
        self.assertIsNone(extract_business_login_continuation(hidden, GATE_URL))


class _ResponseBody:
    def __init__(self, value):
        self.value = value

    async def read(self, size=-1):
        value, self.value = self.value, b""
        return value if size < 0 else value[:size]


class _Response:
    def __init__(self, status, url, body=b"", headers=None):
        self.status = status
        self.url = url
        self.headers = headers or {}
        self.charset = "utf-8"
        self.content = _ResponseBody(body)


class _ResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_):
        return False


class _FakeSession:
    def __init__(self, responses, snapshots=None):
        self.responses = list(responses)
        self.snapshots = list(snapshots or [
            {"c_user": "123", "xs": "same-session"},
        ])
        self.request_calls = []

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return _ResponseContext(self.responses.pop(0))

    def facebook_cookie_snapshot(self):
        if len(self.snapshots) > 1:
            return dict(self.snapshots.pop(0))
        return dict(self.snapshots[0])


class _FakeWeb:
    def __init__(self, session):
        self.profile = SimpleNamespace(
            cookies={"c_user": "123", "xs": "same-session"},
            proxy="http://profile-proxy:8080",
            user_agent="Fixture UA",
        )
        self.session = session
        self.closed = False

    async def _ensure_session(self):
        return self.session

    async def close(self):
        self.closed = True


class GateContinuationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_once_then_reuses_the_verified_profile_http_session(self):
        body = """
        <form action="/business/loginpage/continue" method="post">
          <input type="hidden" name="fb_dtsg" value="csrf-token">
          <button name="continue" value="1">Continue with Facebook</button>
        </form>
        """
        session = _FakeSession([
            _Response(302, "https://business.facebook.com/business/loginpage/continue",
                      headers={"Location": "/latest/home"}),
            _Response(200, "https://business.facebook.com/latest/home", b"redirect target"),
            _Response(200, RETURN_URL, b"verified profile response"),
        ])
        web = _FakeWeb(session)
        result = await continue_business_login_gate(
            web, gate_url=GATE_URL, body=body, return_url=RETURN_URL,
        )
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["url"], RETURN_URL)
        self.assertEqual(result["body"], "verified profile response")
        self.assertEqual(web._business_login_gate_outcome, "recovered")
        self.assertEqual(len(session.request_calls), 3)
        method, url, kwargs = session.request_calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://business.facebook.com/business/loginpage/continue")
        self.assertEqual(kwargs["content"], "fb_dtsg=csrf-token&continue=1")
        self.assertEqual(kwargs["proxy"], "http://profile-proxy:8080")

    async def test_records_safe_reason_when_gate_has_no_supported_control(self):
        session = _FakeSession([])
        web = _FakeWeb(session)
        result = await continue_business_login_gate(
            web,
            gate_url=GATE_URL,
            body="<p>Review your business</p>",
            return_url=RETURN_URL,
        )
        self.assertIsNone(result)
        self.assertEqual(
            web._business_login_gate_outcome,
            "native_request_scope",
        )
        self.assertEqual(session.request_calls, [])

    async def test_native_js_only_gate_uses_same_profile_http_get_and_exact_scope(self):
        from app.private_business_login import native_business_login_continuation

        body = _native_gate_body()
        native_url = native_business_login_continuation(
            body, ADS_MANAGER_RETURN_URL,
        )
        session = _FakeSession([
            _Response(200, native_url, b"native callback"),
            _Response(200, ADS_MANAGER_RETURN_URL, b"fresh scoped document"),
        ])
        web = _FakeWeb(session)
        result = await continue_business_login_gate(
            web,
            gate_url="https://business.facebook.com/business/loginpage/",
            body=body,
            return_url=ADS_MANAGER_RETURN_URL,
        )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["url"], ADS_MANAGER_RETURN_URL)
        self.assertEqual(result["body"], "fresh scoped document")
        self.assertEqual(web._business_login_gate_outcome, "recovered")
        self.assertEqual(len(session.request_calls), 2)
        for method, _, _ in session.request_calls:
            self.assertEqual(method, "GET")
        actual_url = session.request_calls[0][1]
        self.assertEqual(urlsplit(actual_url).path, "/business/loginrequest/fb/")
        params = parse_qs(urlsplit(actual_url).query)
        self.assertEqual(params["f3_request_id"], ["native-request-id"])
        next_url = params["next"][0]
        next_query = parse_qs(urlsplit(next_url).query)
        self.assertEqual(next_query["act"], ["111111"])
        self.assertEqual(next_query["business_id"], ["222222"])
        self.assertEqual(
            next_query["biz_login_source"],
            ["biz_unified_f3_fb_login_button"],
        )
        self.assertNotIn("content", session.request_calls[0][2])

    async def test_native_js_only_gate_refuses_terms_and_credentials(self):
        body = _native_gate_body()
        for rejected in (
            "By continuing, you agree to new terms." + body,
            '<input type="password" name="pass">' + body,
        ):
            session = _FakeSession([])
            web = _FakeWeb(session)
            result = await continue_business_login_gate(
                web,
                gate_url="https://business.facebook.com/business/loginpage/",
                body=rejected,
                return_url=ADS_MANAGER_RETURN_URL,
            )
            self.assertIsNone(result)
            self.assertEqual(session.request_calls, [])

    async def test_stops_if_facebook_actor_changes_after_continuation(self):
        body = """
        <a href="/business/loginpage/continue">Continue with Facebook</a>
        """
        session = _FakeSession(
            [_Response(200, "https://business.facebook.com/business/loginpage/continue")],
            snapshots=[
                {"c_user": "123", "xs": "same-session"},
                {"c_user": "999", "xs": "other-actor"},
            ],
        )
        web = _FakeWeb(session)
        result = await continue_business_login_gate(
            web, gate_url=GATE_URL, body=body, return_url=RETURN_URL,
        )
        self.assertIsNone(result)
        self.assertTrue(web.closed)
        self.assertEqual(len(session.request_calls), 1)


if __name__ == "__main__":
    unittest.main()
