import unittest

from fb_worker import FacebookWebSession, WebProfile


class FacebookRequestEnvelopeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
