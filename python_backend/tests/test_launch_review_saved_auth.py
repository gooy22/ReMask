import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.provisioning.models import ProvisioningError


class LaunchReviewSavedAuthRouteTests(unittest.IsolatedAsyncioTestCase):
    def route(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "private_launch_review")
        node.decorator_list = []
        class HTTPError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        namespace = {
            "Body": lambda value: value,
            "HTTPException": HTTPError,
            "ProvisioningError": ProvisioningError,
            "ProfileContextError": type("ProfileContextError", (Exception,), {}),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "launch-review-route-fixture", "exec"), namespace)
        return namespace["private_launch_review"], HTTPError

    def fixture(self, *, fresh_session=True):
        context = SimpleNamespace(profile_id="27",
            cookies={"c_user":"123456789", "xs":"expired-cookie"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        fresh = SimpleNamespace(profile_id="27",
            cookies={"c_user":"123456789", "xs":"fresh-cookie"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=[context, fresh if fresh_session else context]))
        gate = ProvisioningError("SESSION_EXPIRED", "Meta session expired")
        review = AsyncMock(side_effect=[gate, {"ready":True, "api_preflight_ready":True}])
        pool = SimpleNamespace(resolver=resolver,
            private_launch=SimpleNamespace(review=review))
        store = SimpleNamespace(
            record_terminal_checkpoint=AsyncMock(),
            record_terminal_profile_status=AsyncMock())
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


if __name__ == "__main__":
    unittest.main()
