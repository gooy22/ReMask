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

    def test_hidden_input_payload(self) -> None:
        source = '<input type="hidden" name="fb_dtsg" value="NA_input_token">'
        self.assertEqual(self.extract(source), "NA_input_token")


if __name__ == "__main__":
    unittest.main()
