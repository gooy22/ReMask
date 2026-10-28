import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.business_fan_page_contracts import command, execute
from fb_worker import AuthenticationError, FacebookBootstrap, FacebookWebSession, RemoteRequestError, WebProfile

BUSINESS = "1451903470239662"
ACTOR = "61594946647826"
CATEGORY_ID = "242822000000000"


def bindings_for(operation):
    if operation == "READ_FP":
        return {"business": BUSINESS, "cursor": None}
    if operation == "FP_PRECHECK":
        return {"business": BUSINESS}
    if operation == "FP_CATEGORY":
        return {"category": "Digital creator"}
    return {
        "business": BUSINESS,
        "name": "PrgssTeam",
        "bio": "",
        "categories": [CATEGORY_ID],
        "join": "fixture-join",
    }


class BusinessPageLoginpageProbeTests(unittest.TestCase):
    def setUp(self):
        self.web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "fixture-xs"}, None, "fixture-UA",
        ))

    def validate(self, operation, *, business=BUSINESS, before_submit=None,
                 read_timeout_seconds=None):
        row = command(operation, **bindings_for(operation))
        return self.web._validate_business_loginpage_probe(
            operation, row["doc_id"], row["variables"],
            friendly_name=row["friendly_name"],
            endpoint_url=row["endpoint_url"],
            request_envelope=None,
            before_submit=before_submit,
            business_context_id=business,
            multipart_files=None,
            read_timeout_seconds=read_timeout_seconds,
        )

    def test_exact_page_contracts_are_scoped_to_verified_business(self):
        for operation in ("READ_FP", "FP_PRECHECK", "FP_CATEGORY"):
            with self.subTest(operation=operation):
                self.assertEqual(
                    self.validate(operation, read_timeout_seconds=30),
                    operation,
                )
        self.assertEqual(
            self.validate("CREATE_FP", before_submit=AsyncMock()),
            "CREATE_FP",
        )

    def test_page_reads_reject_wrong_scope_or_unreviewed_budget(self):
        with self.assertRaises(RemoteRequestError):
            self.validate("READ_FP", business="1428816905866955",
                          read_timeout_seconds=30)
        with self.assertRaises(RemoteRequestError):
            self.validate("READ_FP", read_timeout_seconds=15)

    def test_page_create_requires_exact_business_and_durable_intent(self):
        with self.assertRaises(RemoteRequestError):
            self.validate("CREATE_FP")
        with self.assertRaises(RemoteRequestError):
            self.validate("CREATE_FP", business="1428816905866955",
                          before_submit=AsyncMock())

    def test_unknown_page_operation_cannot_use_the_probe(self):
        row = command("READ_FP", **bindings_for("READ_FP"))
        with self.assertRaises(RemoteRequestError):
            self.web._validate_business_loginpage_probe(
                "UNREVIEWED_PAGE_MUTATION", row["doc_id"], row["variables"],
                friendly_name=row["friendly_name"],
                endpoint_url=row["endpoint_url"],
                request_envelope=None, before_submit=None,
                business_context_id=BUSINESS, multipart_files=None,
                read_timeout_seconds=30,
            )


class BusinessPageBootstrapProofTests(unittest.IsolatedAsyncioTestCase):
    def make_web(self):
        web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "fixture-xs"}, None, "fixture-UA",
        ))
        bootstrap = FacebookBootstrap(
            fb_dtsg="fixture-token", actor_id=ACTOR,
            source_url="https://www.facebook.com/me", profile_actor_verified=False,
        )
        web._bootstrap = bootstrap

        class CookieSession:
            def facebook_cookie_snapshot(self):
                return {"c_user": ACTOR, "xs": "fixture-xs"}
            def restore_profile_cookies(self):
                return None

        web._ensure_session = AsyncMock(return_value=CookieSession())
        web.fetch_text = AsyncMock(return_value=(
            200, '<form id="login_form"></form>',
            "https://business.facebook.com/business/loginpage/",
        ))
        return web, bootstrap

    async def test_exact_page_read_keeps_the_authenticated_business_token(self):
        web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "fixture-xs"}, None, "fixture-UA",
        ))
        business_token = FacebookBootstrap(
            fb_dtsg="business-token", actor_id=ACTOR,
            source_url="https://business.facebook.com/latest/settings/ad_accounts/?business_id=" + BUSINESS,
        )
        web._bootstrap = business_token
        posts = []

        class Client:
            @asynccontextmanager
            async def post(self, endpoint, **kwargs):
                posts.append((endpoint, kwargs))
                yield SimpleNamespace(status=200, headers={},
                    text=AsyncMock(return_value='{"data":{"node":{"id":"' + BUSINESS + '"}}}'))

        web._ensure_session = AsyncMock(return_value=Client())
        web._verified_profile_bootstrap = AsyncMock(
            side_effect=AssertionError("An exact Page read must not replace an authenticated Business token with /me."))

        with patch("app.contract_maintenance.module_refresh.refresh_due", return_value=False):
            result = await execute(web, "READ_FP", business=BUSINESS, cursor=None)

        self.assertEqual(result["data"]["node"]["id"], BUSINESS)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][0], "https://business.facebook.com/api/graphql/")
        self.assertEqual(posts[0][1]["data"]["fb_dtsg"], "business-token")
        web._verified_profile_bootstrap.assert_not_awaited()
        self.assertIs(web._bootstrap, business_token)

    async def test_exact_page_read_can_continue_after_business_loginpage(self):
        web, bootstrap = self.make_web()
        result = await web._business_bootstrap(
            bootstrap, business_id=BUSINESS, business_loginpage_probe="READ_FP",
        )
        self.assertIs(result, bootstrap)
        self.assertFalse(bootstrap.profile_actor_verified)

    async def test_page_create_requires_fresh_exact_business_prechecks(self):
        web, bootstrap = self.make_web()
        with self.assertRaises(AuthenticationError):
            await web._business_bootstrap(
                bootstrap, business_id=BUSINESS, business_loginpage_probe="CREATE_FP",
            )

        web, bootstrap = self.make_web()
        self.assertTrue(web.mark_business_page_create_precheck_verified(
            BUSINESS, ACTOR, "PrgssTeam", "", [CATEGORY_ID]))
        self.assertTrue(web.has_fresh_business_page_create_precheck(
            BUSINESS, ACTOR, "PrgssTeam", "", [CATEGORY_ID]))
        self.assertFalse(web.has_fresh_business_page_create_precheck(
            "1428816905866955", ACTOR, "PrgssTeam", "", [CATEGORY_ID]))
        self.assertFalse(web.has_fresh_business_page_create_precheck(
            BUSINESS, ACTOR, "Different page", "", [CATEGORY_ID]))
        result = await web._business_bootstrap(
            bootstrap, business_id=BUSINESS, business_loginpage_probe="CREATE_FP",
            business_page_create_prechecked=True,
        )
        self.assertIs(result, bootstrap)
        web.invalidate_bootstrap()
        self.assertFalse(web.has_fresh_business_page_create_precheck(
            BUSINESS, ACTOR, "PrgssTeam", "", [CATEGORY_ID]))


class BusinessPageSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_previous_snapshot_bootstrap_requires_and_preserves_same_actor(self):
        web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "current-xs"}, None, "fixture-UA",
            recovery_cookies={"c_user": ACTOR, "xs": "current-xs"},
        ))
        self.assertIsNone(await web.bootstrap_from_previous_session())
        self.assertFalse(web._previous_recovery_attempted)
        self.assertIsNotNone(web.profile.recovery_cookies)
        web.profile.recovery_cookies = {"c_user": ACTOR, "xs": "previous-xs"}
        refreshed = {"c_user": ACTOR, "xs": "refreshed-xs"}

        class SnapshotClient:
            closed = False
            def facebook_cookie_snapshot(self):
                return dict(refreshed)

        client = SnapshotClient()

        async def verified_bootstrap(previous, **kwargs):
            previous._bootstrap = FacebookBootstrap(
                fb_dtsg="fixture-token", actor_id=ACTOR,
                profile_actor_verified=True,
            )
            previous._profile_session_identity_verified = True
            previous.session = client
            return previous._bootstrap

        with patch.object(FacebookWebSession, "bootstrap", new=verified_bootstrap):
            result = await web.bootstrap_from_previous_session()

        self.assertIsNotNone(result)
        self.assertEqual(result.actor_id, ACTOR)
        self.assertTrue(web._profile_session_identity_verified)
        self.assertIs(web.session, client)
        self.assertEqual(web.profile.cookies, refreshed)
        self.assertIsNone(web.profile.recovery_cookies)

    async def test_unusable_scoped_read_snapshot_does_not_consume_bootstrap_recovery(self):
        web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "current-xs"}, None, "fixture-UA",
            recovery_cookies={"c_user": ACTOR, "xs": "current-xs"},
        ))
        result = await web._retry_scoped_read_with_previous_session(
            "fixture-doc", {}, friendly_name="READ_RK",
            endpoint_url=None, request_envelope=None,
            business_context_id=BUSINESS, business_loginpage_probe="READ_RK",
            read_timeout_seconds=30,
        )
        self.assertIsNone(result)
        self.assertFalse(web._previous_recovery_attempted)
        self.assertIsNotNone(web.profile.recovery_cookies)

    async def test_previous_snapshot_with_different_actor_is_rejected_without_bootstrap(self):
        web = FacebookWebSession(WebProfile(
            "15", {"c_user": ACTOR, "xs": "current-xs"}, None, "fixture-UA",
            recovery_cookies={"c_user": "61500000000000", "xs": "previous-xs"},
        ))
        with patch.object(FacebookWebSession, "bootstrap", new_callable=AsyncMock) as bootstrap:
            result = await web.bootstrap_from_previous_session()
        self.assertIsNone(result)
        bootstrap.assert_not_awaited()
        self.assertIsNone(web.session)
        self.assertEqual(web.profile.cookies["c_user"], ACTOR)


class BusinessPageContractExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_marks_reads_and_durable_create(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={"data": {}}))
        await execute(web, "READ_FP", business=BUSINESS, cursor=None)
        read_kwargs = web.graphql.await_args.kwargs
        self.assertEqual(read_kwargs["business_loginpage_probe"], "READ_FP")
        self.assertEqual(read_kwargs["business_context_id"], BUSINESS)
        self.assertEqual(read_kwargs["read_timeout_seconds"], 30)

        web.graphql.reset_mock()
        intent = AsyncMock()
        await execute(
            web, "CREATE_FP", business=BUSINESS, name="PrgssTeam",
            bio="", categories=[CATEGORY_ID], join="fixture-join",
            before_submit=intent,
        )
        create_kwargs = web.graphql.await_args.kwargs
        self.assertEqual(create_kwargs["business_loginpage_probe"], "CREATE_FP")
        self.assertEqual(create_kwargs["business_context_id"], BUSINESS)
        self.assertIs(create_kwargs["before_submit"], intent)
        self.assertNotIn("read_timeout_seconds", create_kwargs)