import unittest
from urllib.parse import parse_qs, urlsplit

from app.private_business_login import SOURCE, native_business_login_continuation


class NativeBusinessLoginContinuationTests(unittest.TestCase):
    def gate(self, referrer="/latest/home"):
        payload = (
            '[["react-xhp",'
            '"constructAndRenderComponentIntoComment_DO_NOT_USE",null,'
            '[{"constructor":{"__m":"BusinessToolsF3UnifiedLoginPageView.react"},'
            '"props":{"loginOptions":["FB"],"saveSession":false,'
            '"referrerURI":"' + referrer + '","requestId":"fixture-request"}}]]]'
        )
        return (
            '<html><head><script src="' + SOURCE + '"></script></head>'
            '<body><script type="application/json">' + payload + '</script></body></html>'
        )

    def test_emits_reviewed_facebook_loginrequest_for_business_home(self):
        report = {}
        value = native_business_login_continuation(
            self.gate(),
            "https://business.facebook.com/latest/home",
            diagnostic=report,
        )
        self.assertIsInstance(value, str)
        parts = urlsplit(value)
        self.assertEqual(parts.netloc, "business.facebook.com")
        self.assertEqual(parts.path, "/business/loginrequest/fb/")
        query = parse_qs(parts.query)
        self.assertEqual(set(query), {"next", "f3_request_id"})
        self.assertEqual(query["f3_request_id"], ["fixture-request"])
        next_parts = urlsplit(query["next"][0])
        self.assertEqual(next_parts.netloc, "business.facebook.com")
        self.assertEqual(next_parts.path, "/latest/home")
        next_query = parse_qs(next_parts.query)
        self.assertEqual(
            next_query["biz_login_source"],
            ["biz_unified_f3_fb_login_button"],
        )
        self.assertTrue(next_query["join_id"][0])
        self.assertEqual(report["reason"], "native_fb_continuation")

    def test_refuses_unscoped_or_credential_flow(self):
        report = {}
        result = native_business_login_continuation(
            self.gate(),
            "https://evil.example/latest/home",
            diagnostic=report,
        )
        self.assertIsNone(result)
        self.assertEqual(report["reason"], "request_scope")

        report = {}
        credential_gate = self.gate().replace(
            "<body>",
            '<body><input type="password" name="pass">',
        )
        result = native_business_login_continuation(
            credential_gate,
            "https://business.facebook.com/latest/home",
            diagnostic=report,
        )
        self.assertIsNone(result)
        self.assertEqual(report["reason"], "credential_controls")


if __name__ == "__main__":
    unittest.main()
