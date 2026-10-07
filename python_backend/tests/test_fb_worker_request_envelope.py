import inspect
import unittest
from unittest.mock import AsyncMock

from fb_worker import FacebookBootstrap, FacebookWebSession, WebProfile


class FacebookRequestEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    def _session(self):
        return FacebookWebSession(
            WebProfile(
                name="test",
                cookies={"c_user": "123456789"},
                proxy="http://127.0.0.1:8080",
                user_agent="Mozilla/5.0",
            )
        )

    def test_extracts_only_current_profile_request_metadata(self):
        html = r'''
        <script>
        window.__TEST__ = {
          "__rev": 1048238984,
          "__hsi": "7688647915964986531",
          "__comet_req": 11,
          "__spin_r": 1048238984,
          "__spin_b": "trunk",
          "__spin_t": 1790152843,
          "__jssesw": "1",
          "__crn": "comet.bizweb.BusinessCometBizSuiteBusinessHomeRoute",
          "__dyn": "dyn-current-profile",
          "__csr": "csr-current-profile"
        };
        </script>
        '''
        context = FacebookWebSession._extract_request_context(html)

        self.assertEqual(context["__rev"], "1048238984")
        self.assertEqual(context["__hsi"], "7688647915964986531")
        self.assertEqual(context["__comet_req"], "11")
        self.assertEqual(context["__spin_b"], "trunk")
        self.assertEqual(context["__dyn"], "dyn-current-profile")
        self.assertEqual(context["__csr"], "csr-current-profile")

    def test_request_counter_is_local_and_base36(self):
        session = self._session()
        self.assertEqual(session._next_graphql_req(), "1")
        self.assertEqual(session._next_graphql_req(), "2")

        session._graphql_request_counter = 35
        self.assertEqual(session._next_graphql_req(), "10")


    def test_decodes_streamed_relay_create_response(self):
        payload = FacebookWebSession._decode_graphql_body(
            '{"extensions":{"is_final":false}}\n'
            '{"data":{"business_create":{"business":{"id":"555666777888999"}}}}'
        )
        self.assertEqual(
            payload["data"]["business_create"]["business"]["id"],
            "555666777888999",
        )

    def test_merges_streamed_relay_errors(self):
        payload = FacebookWebSession._decode_graphql_body(
            '{"errors":[{"message":"first"}]}\n'
            '{"errors":[{"message":"second"}]}'
        )
        self.assertEqual(
            [row["message"] for row in payload["errors"]],
            ["first", "second"],
        )

    async def test_direct_graphql_uses_asset_scoped_bootstrap_referer(self):
        class FakeResponse:
            status = 200
            headers = {}

            async def text(self):
                return '{"data":{"ok":true}}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return FakeResponse()

        session = self._session()
        fake = FakeSession()
        session._ensure_session = AsyncMock(return_value=fake)
        session.bootstrap = AsyncMock(
            return_value=FacebookBootstrap(
                fb_dtsg="dtsg",
                actor_id="123456789",
                lsd="lsd-token",
                request_context={"__aaid": "42"},
                source_url=(
                    "https://business.facebook.com/latest/home"
                    "?asset_id=1348798761652037&ir_qe_exposed=1"
                ),
            )
        )

        payload = await session.graphql(
            "28057338880523368",
            {"input": {"actor_id": "123456789"}},
            friendly_name="useBusinessCreationMutationMutation",
        )

        self.assertTrue(payload["data"]["ok"])
        self.assertEqual(len(fake.calls), 1)
        _, kwargs = fake.calls[0]
        headers = kwargs["headers"]
        self.assertEqual(
            headers["Referer"],
            (
                "https://business.facebook.com/latest/home"
                "?asset_id=1348798761652037&ir_qe_exposed=1"
            ),
        )
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")


if __name__ == "__main__":
    unittest.main()


class FacebookBrowserGraphqlExactlyOnceTests(unittest.TestCase):
    def test_browser_graphql_preserves_submit_state_on_timeout_and_http_error(self):
        source = inspect.getsource(FacebookWebSession.graphql_browser_native)
        self.assertIn(
            'request_may_have_been_sent=request_may_have_been_sent',
            source,
        )
        self.assertIn('transport_stage=transport_stage', source)
        self.assertIn(
            'request_may_have_been_sent=True',
            source,
        )
        self.assertIn(
            'transport_stage="graphql_response"',
            source,
        )

    def test_legacy_remote_errors_are_enriched_instead_of_losing_send_state(self):
        source = inspect.getsource(FacebookWebSession.graphql_browser_native)
        self.assertIn(
            "if exc.request_may_have_been_sent is None:",
            source,
        )
        self.assertIn(
            "exc.request_may_have_been_sent = request_may_have_been_sent",
            source,
        )
