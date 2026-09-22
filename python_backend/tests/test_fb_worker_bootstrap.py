import unittest

from fb_worker import FacebookWebSession


class FacebookDtsgBootstrapParserTests(unittest.TestCase):
    def extract(self, source: str) -> str:
        return FacebookWebSession._first_match(
            source,
            list(FacebookWebSession.FB_DTSG_PATTERNS),
        )

    def test_plain_bootloader_payload(self) -> None:
        source = '["DTSGInitialData",[],{"token":"NA_plain_token"}]'
        self.assertEqual(self.extract(source), "NA_plain_token")

    def test_html_entity_escaped_payload(self) -> None:
        source = (
            '[&quot;DTSGInitialData&quot;,[],'
            '{&quot;token&quot;:&quot;NA_html_token&quot;}]'
        )
        self.assertEqual(self.extract(source), "NA_html_token")

    def test_javascript_escaped_payload(self) -> None:
        source = r'[\"DTSGInitialData\",[],{\"token\":\"NA_js_token\"}]'
        self.assertEqual(self.extract(source), "NA_js_token")

    def test_dtsg_init_data_payload(self) -> None:
        source = (
            '["DTSGInitData",[],'
            '{"token":"NA_init_token","async_get_token":"NA_async"}]'
        )
        self.assertEqual(self.extract(source), "NA_init_token")

    def test_dtsg_object_payload(self) -> None:
        source = '{"dtsg":{"token":"NA_object_token"}}'
        self.assertEqual(self.extract(source), "NA_object_token")

    def test_named_json_input_payload(self) -> None:
        source = '{"name":"fb_dtsg","value":"NA_named_token"}'
        self.assertEqual(self.extract(source), "NA_named_token")

    def test_unicode_escaped_init_payload(self) -> None:
        source = (
            r'[\u0022DTSGInitData\u0022,[],'
            r'{\u0022token\u0022:\u0022NA_unicode_token\u0022}]'
        )
        self.assertEqual(self.extract(source), "NA_unicode_token")

    def test_ajax_dtsg_refresh_payload(self) -> None:
        source = 'for (;;);{"payload":{"token":"NA_refresh_token","valid_for":3600}}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_refresh_token",
        )

    def test_ajax_dtsg_nested_string_payload(self) -> None:
        source = 'for (;;);{"payload":"{\\\"token\\\":\\\"NA_nested_refresh\\\"}"}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_nested_refresh",
        )

    def test_ajax_dtsg_invalid_payload(self) -> None:
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(
                'for (;;);{"payload":{"error":"no token"}}'
            ),
            "",
        )

    def test_hidden_input_payload(self) -> None:
        source = '<input type="hidden" name="fb_dtsg" value="NA_input_token">'
        self.assertEqual(self.extract(source), "NA_input_token")


if __name__ == "__main__":
    unittest.main()
