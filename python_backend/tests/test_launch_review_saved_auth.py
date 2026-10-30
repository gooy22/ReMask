import ast
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.provisioning.models import ProvisioningError
from app.store import JobStore


class LaunchReviewSavedAuthRouteTests(unittest.IsolatedAsyncioTestCase):
    def route(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        module = ast.parse(source)
        helper = next(node for node in module.body
                      if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == "_clear_business_login_gate_after_profile_review")
        node = next(node for node in module.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "private_launch_review")
        helper.decorator_list = []
        node.decorator_list = []
        class HTTPError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        namespace = {
            "Body": lambda value: value,
            "HTTPException": HTTPError,
            "ProvisioningError": ProvisioningError,
            "ProfileContextError": type("ProfileContextError", (Exception,), {}),
            "Any": object,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper, node], type_ignores=[])),
                     "launch-review-route-fixture", "exec"), namespace)
        return namespace["private_launch_review"], HTTPError

    def fixture(self, *, fresh_session=True, review_result=None):
        context = SimpleNamespace(profile_id="27",
            cookies={"c_user":"123456789", "xs":"expired-cookie"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        fresh = SimpleNamespace(profile_id="27",
            cookies={"c_user":"123456789", "xs":"fresh-cookie"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=[context, fresh if fresh_session else context]))
        gate = ProvisioningError("SESSION_EXPIRED", "Meta session expired")
        accepted_review = review_result or {
            "ready":True, "api_preflight_ready":True, "checked_live":True,
            "profile_id":"27", "business_id":"1422264096661455",
            "ad_account_id":"120250306658010101", "page_id":"1458249004037430",
        }
        review = AsyncMock(side_effect=[gate, accepted_review])
        pool = SimpleNamespace(resolver=resolver,
            private_launch=SimpleNamespace(review=review))
        store = SimpleNamespace(
            record_terminal_checkpoint=AsyncMock(),
            record_terminal_profile_status=AsyncMock(),
            clear_terminal_business_login_gate=AsyncMock())
        route, HTTPError = self.route()
        namespace = route.__globals__
        namespace.update(pool=pool, store=store)
        namespace["read_with_saved_auth_refresh"] = __import__(
            "app.session_auth_refresh", fromlist=["read_with_saved_auth_refresh"]
        ).read_with_saved_auth_refresh
        return route, HTTPError, context, resolver, review, store

    async def test_review_retries_once_with_new_auth_for_the_same_profile(self):
        route, _, context, resolver, review, store = self.fixture()
        result = await route("27", {"launch_key":"saved-launch"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        self.assertEqual(resolver.resolve.await_count, 2)
        self.assertEqual(review.await_count, 2)
        for call in review.await_args_list:
            self.assertEqual(call.kwargs["profile_id"], "27")
            self.assertIs(call.kwargs["context"], context)
            self.assertEqual(call.kwargs["payload"], {"launch_key":"saved-launch"})
        self.assertEqual(context.cookies["xs"], "fresh-cookie")
        store.record_terminal_checkpoint.assert_not_awaited()
        store.record_terminal_profile_status.assert_not_awaited()
        store.clear_terminal_business_login_gate.assert_awaited_once_with("27")

    async def test_review_does_not_retry_when_saved_auth_is_unchanged(self):
        route, HTTPError, _, resolver, review, store = self.fixture(fresh_session=False)
        with self.assertRaises(HTTPError) as caught:
            await route("27", {"launch_key":"saved-launch"})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "SESSION_EXPIRED")
        self.assertEqual(resolver.resolve.await_count, 2)
        self.assertEqual(review.await_count, 1)
        store.record_terminal_checkpoint.assert_not_awaited()
        store.record_terminal_profile_status.assert_not_awaited()


    async def test_review_does_not_clear_gate_without_scoped_live_proof(self):
        route, _, _, _, _, store = self.fixture(review_result={
            "ready":True, "api_preflight_ready":True,
            "profile_id":"27", "business_id":"1422264096661455",
            "ad_account_id":"120250306658010101", "page_id":"1458249004037430",
        })
        await route("27", {"launch_key":"saved-launch"})
        store.clear_terminal_business_login_gate.assert_not_awaited()

    def revalidation_guard(self, status):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "_require_profile_not_checkpoint_for_revalidation")
        node.decorator_list = []
        class HTTPError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        async def terminal_statuses(profile_ids):
            return {profile_ids[0]: {"status":status,"message":"fixture"}}
        namespace = {"HTTPException":HTTPError,"_terminal_profile_statuses":terminal_statuses}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "launch-review-revalidation-guard-fixture", "exec"), namespace)
        return namespace[node.name], HTTPError

    async def test_read_only_revalidation_allows_business_login_gate(self):
        guard, _ = self.revalidation_guard("BUSINESS_LOGIN_GATE")
        self.assertIsNone(await guard("27"))

    async def test_read_only_revalidation_keeps_checkpoint_terminal(self):
        guard, HTTPError = self.revalidation_guard("CHECKPOINT")
        with self.assertRaises(HTTPError) as caught:
            await guard("27")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "CHECKPOINT_REQUIRED")

    def test_read_only_launch_routes_use_revalidation_guard(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        module = ast.parse(source)
        for route_name in ("private_launch_review", "private_advertising_resources", "profile_live_inventory"):
            route = next(node for node in module.body
                         if isinstance(node, ast.AsyncFunctionDef) and node.name == route_name)
            dependencies = [node.id for decorator in route.decorator_list
                            for node in ast.walk(decorator) if isinstance(node, ast.Name)]
            self.assertIn("_require_profile_not_checkpoint_for_revalidation", dependencies)

    async def test_store_clears_business_gate_but_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(str(Path(tmp).joinpath("jobs.sqlite3")))
            await store.init()
            await store.record_terminal_profile_status("27", "BUSINESS_LOGIN_GATE")
            await store.record_terminal_profile_status("28", "CHECKPOINT")
            self.assertTrue(await store.clear_terminal_business_login_gate("27"))
            self.assertFalse(await store.clear_terminal_business_login_gate("28"))
            statuses = await store.terminal_profile_statuses(["27", "28"])
            self.assertNotIn("27", statuses)
            self.assertEqual(statuses["28"]["status"], "CHECKPOINT")


if __name__ == "__main__":
    unittest.main()
