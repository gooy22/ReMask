import inspect
import unittest
from unittest.mock import AsyncMock

from fb_worker import AuthenticationError, RemoteRequestError, FacebookBootstrap, FacebookWebSession, WebProfile


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
                request_context={"__aaid": "42", "__hsi": "fresh-session", "__rev": "200", "__spin_t": "999"},
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
            request_envelope={"__aaid": "old-asset", "__hsi": "old-session", "__rev": "100", "__spin_t": "111", "__dyn": "other-profile-dyn", "__req": "cached-counter", "fb_dtsg": "other-profile-token", "dpr": "2"},
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
        form = kwargs["data"]
        self.assertEqual(form["__aaid"], "42")
        self.assertEqual(form["__hsi"], "fresh-session")
        self.assertEqual(form["__rev"], "200")
        self.assertEqual(form["__spin_t"], "999")
        self.assertEqual(form["__req"], "1")
        self.assertEqual(form["fb_dtsg"], "dtsg")
        self.assertEqual(form["dpr"], "2")
        self.assertNotIn("__dyn", form)
        await session.graphql("28057338880523368", {}, request_envelope={"__req": "cached"})
        self.assertEqual(fake.calls[1][1]["data"]["__req"], "2")


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


class BusinessAuthenticationPrecheckTests(unittest.IsolatedAsyncioTestCase):
    def session(self, payload='{"data":{"ok":true}}', source="https://www.facebook.com/marketplace/"):
        class Response:
            status = 200
            headers = {}
            async def text(self):
                return payload
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
        class HTTP:
            def __init__(self):
                self.posts = []
            def post(self, endpoint, **kwargs):
                self.posts.append(kwargs)
                return Response()
        session = FacebookWebSession(WebProfile(name="fixture",cookies={"c_user":"123456789"},proxy=None,user_agent="fixture"))
        http = HTTP()
        session._ensure_session = AsyncMock(return_value=http)
        session.bootstrap = AsyncMock(return_value=FacebookBootstrap(
            actor_id="123456789", fb_dtsg="marketplace-token",source_url=source,
            request_context={"__hsi":"marketplace-context"}))
        return session, http

    async def test_business_login_redirect_blocks_submit_before_checkpoint(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(return_value=(200,'<form id="login_form">','https://www.facebook.com/login.php?next=business'))
        checkpoint = AsyncMock()
        with self.assertRaises(AuthenticationError) as caught:
            await session.graphql("123456789",{},before_submit=checkpoint)
        self.assertFalse(caught.exception.request_may_have_been_sent)
        self.assertEqual(http.posts, [])
        checkpoint.assert_not_awaited()

    async def test_fresh_business_precheck_supplies_business_token_and_context(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(return_value=(200,
            '["DTSGInitialData",[],{"token":"business-token"}] ["LSD",[],{"token":"business-lsd"}] {"__hsi":"business-context"}',
            'https://business.facebook.com/latest/home?asset_id=1234567890'))
        result = await session.graphql("123456789",{})
        self.assertTrue(result["data"]["ok"])
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(http.posts[0]["data"]["fb_dtsg"], "business-token")
        self.assertEqual(http.posts[0]["data"]["lsd"], "business-lsd")
        self.assertEqual(http.posts[0]["data"]["__hsi"], "business-context")
        self.assertIn("asset_id=1234567890", http.posts[0]["headers"]["Referer"])

    async def test_missing_business_token_is_retryable_transport_without_submit(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(return_value=(200,"not hydrated","https://business.facebook.com/latest/home"))
        with self.assertRaises(RemoteRequestError) as caught:
            await session.graphql("123456789",{})
        self.assertFalse(caught.exception.request_may_have_been_sent)
        self.assertEqual(http.posts, [])

    async def test_http_200_login_error_invalidates_session_without_retry(self):
        session, http = self.session(payload='{"error":1357001,"errorSummary":"Log in to continue","payload":null}',
                                    source="https://business.facebook.com/latest/home")
        session._bootstrap = await session.bootstrap()
        with self.assertRaises(AuthenticationError) as caught:
            await session.graphql("123456789",{})
        self.assertTrue(caught.exception.request_rejected)
        self.assertEqual(caught.exception.meta_payload["error"], 1357001)
        self.assertIsNone(session._bootstrap)
        self.assertEqual(len(http.posts), 1)

    async def test_generic_processing_error_is_not_mislabeled_session_expired(self):
        session, http = self.session(payload='{"error":1357054,"payload":null}',
                                    source="https://business.facebook.com/latest/home")
        self.assertEqual((await session.graphql("123456789",{}))["error"], 1357054)
        self.assertEqual(len(http.posts), 1)

    async def test_create_business_recovers_home_400_with_authenticated_creation_page(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(side_effect=[
            (400, "Bad Request", session.ADS_MANAGER_URL),
            (200, '["DTSGInitialData",[],{"token":"creation-token"}] {"__hsi":"creation-context"}',
             "https://business.facebook.com/create"),
        ])
        checkpoint = AsyncMock()
        result = await session.graphql("123456789", {},
            friendly_name="useBusinessCreationMutationMutation", before_submit=checkpoint)
        self.assertTrue(result["data"]["ok"])
        self.assertEqual([call.args[0] for call in session.fetch_text.await_args_list],
                         [session.ADS_MANAGER_URL, "https://business.facebook.com/create"])
        checkpoint.assert_awaited_once()
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(http.posts[0]["data"]["fb_dtsg"], "creation-token")
        self.assertEqual(http.posts[0]["data"]["__hsi"], "creation-context")
        self.assertEqual(http.posts[0]["headers"]["Referer"], "https://business.facebook.com/create")

    async def test_create_business_unconfirmed_surfaces_keep_diagnostics_without_post(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(side_effect=[
            (400, "Bad Request", session.ADS_MANAGER_URL),
            (200, "no auth context", "https://business.facebook.com/create?secret=not-for-logs"),
        ])
        checkpoint = AsyncMock()
        with self.assertRaises(RemoteRequestError) as caught:
            await session.graphql("123456789", {},
                friendly_name="useBusinessCreationMutationMutation", before_submit=checkpoint)
        error = caught.exception
        self.assertFalse(error.request_may_have_been_sent)
        self.assertEqual(len(error.meta_payload["business_precheck"]), 2)
        self.assertNotIn("not-for-logs", str(error.meta_payload))
        self.assertEqual(error.meta_payload["business_precheck"][0]["http_status"], 400)
        checkpoint.assert_not_awaited()
        self.assertEqual(http.posts, [])

    async def test_business_auth_challenge_forbids_creation_surface_fallback(self):
        for status, body, final_url in (
            (403, "denied", "https://business.facebook.com/latest/home"),
            (200, "login", "https://www.facebook.com/login.php"),
            (200, "checkpoint", "https://business.facebook.com/checkpoint/"),
        ):
            with self.subTest(status=status, final_url=final_url):
                session, http = self.session()
                session.fetch_text = AsyncMock(return_value=(status, body, final_url))
                checkpoint = AsyncMock()
                with self.assertRaises(AuthenticationError) as caught:
                    await session.graphql("123456789", {},
                        friendly_name="useBusinessCreationMutationMutation", before_submit=checkpoint)
                self.assertFalse(caught.exception.request_may_have_been_sent)
                session.fetch_text.assert_awaited_once()
                checkpoint.assert_not_awaited()
                self.assertEqual(http.posts, [])

    async def test_rk_exact_business_precheck_never_uses_generic_creation_surface(self):
        session, http = self.session()
        target = "https://business.facebook.com/latest/settings/ad_accounts/?business_id=444444444444"
        session.fetch_text = AsyncMock(return_value=(400, "Bad Request", target))
        checkpoint = AsyncMock()
        with self.assertRaises(RemoteRequestError):
            await session.graphql("123456789", {"input": {"business_id": "444444444444"}},
                friendly_name="BizKitSettingsCreateAdAccountMutation", before_submit=checkpoint)
        session.fetch_text.assert_awaited_once_with(target)
        checkpoint.assert_not_awaited()
        self.assertEqual(http.posts, [])

    async def test_business_rate_limit_does_not_probe_another_surface(self):
        session, http = self.session()
        session.fetch_text = AsyncMock(return_value=(429, "rate limited", session.ADS_MANAGER_URL))
        with self.assertRaises(RemoteRequestError) as caught:
            await session.graphql("123456789", {}, friendly_name="useBusinessCreationMutationMutation")
        self.assertEqual(caught.exception.http_status, 429)
        session.fetch_text.assert_awaited_once()
        self.assertEqual(http.posts, [])

