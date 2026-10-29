import ast
from contextlib import nullcontext
from pathlib import Path
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.provisioning.models import ProvisioningError


class TemplateTargetingSavedAuthRouteTests(unittest.IsolatedAsyncioTestCase):
    def route(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
        node = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "private_template_reference")
        node.decorator_list = []
        node.body = [statement for statement in node.body
                     if not (isinstance(statement, ast.ImportFrom)
                             and statement.module == "app.private_template_reads")]

        class HTTPError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail

        asyncio_module = __import__("asyncio")
        timeouts = []

        class Asyncio:
            TimeoutError = asyncio_module.TimeoutError

            @staticmethod
            async def wait_for(awaitable, timeout):
                timeouts.append(timeout)
                return await asyncio_module.wait_for(awaitable, timeout=timeout)

        namespace = {
            "asyncio": Asyncio,
            "os": os,
            "HTTPException": HTTPError,
            "ProvisioningError": ProvisioningError,
            "ProfileContextError": type("ProfileContextError", (Exception,), {}),
            "AuthenticationError": type("AuthenticationError", (Exception,), {}),
            "RemoteRequestError": type("RemoteRequestError", (Exception,), {}),
            "ProxyError": type("ProxyError", (Exception,), {}),
            "private_auth_error": lambda error: error,
            "private_transport_error": lambda error: (502, str(error)),
            "measure_phase": lambda _name: nullcontext(),
            "log": SimpleNamespace(info=lambda *args, **kwargs: None),
            "REFERENCE_TYPES": {"interests"},
            "Body": lambda value: value,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     "template-targeting-route-fixture", "exec"), namespace)
        return namespace["private_template_reference"], HTTPError, namespace, timeouts

    def fixture(self, *, refresh_auth=True, successful_read=True):
        context = SimpleNamespace(profile_id="27",
            cookies={"c_user": "123456789", "xs": "saved-session"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        fresh = SimpleNamespace(profile_id="27",
            cookies={"c_user": "123456789",
                     "xs": "new-session" if refresh_auth else "saved-session"},
            proxy="http://fixture-proxy:80", user_agent="Fixture")
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        sessions = []

        class FakeSession:
            def __init__(self, session_context):
                self.context = session_context
                self.web = SimpleNamespace(private_only=False)
                sessions.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def facebook_web(self):
                return self.web

        gate = ProvisioningError("SESSION_EXPIRED", "Meta login response")
        success = {"account_id": "111111", "data": [{"id": "456", "name": "Travel"}]}
        read_template_reference = AsyncMock(
            side_effect=[gate, success] if successful_read else gate)
        pool = SimpleNamespace(
            resolver=resolver,
            private_launch=SimpleNamespace(
                ads_manager=SimpleNamespace(catalog=object()),
                resources=SimpleNamespace(TARGETING_OPTION_TYPES=()),
            ),
        )
        route, HTTPError, namespace, timeouts = self.route()
        namespace.update(
            pool=pool,
            ProfileSession=FakeSession,
            _resolve_read_profile=AsyncMock(return_value=context),
            read_template_reference=read_template_reference,
        )
        namespace["read_with_saved_auth_refresh"] = __import__(
            "app.session_auth_refresh", fromlist=["read_with_saved_auth_refresh"]
        ).read_with_saved_auth_refresh
        return route, HTTPError, context, resolver, sessions, read_template_reference, timeouts

    async def test_targeting_read_retries_once_with_new_auth_for_profile_27(self):
        route, _, context, resolver, sessions, read, timeouts = self.fixture()
        payload = {"q": "Travel", "limit": 50}
        result = await route("27", "interests", payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"][0]["id"], "456")
        self.assertEqual(timeouts, [75])
        self.assertEqual(resolver.resolve.await_count, 1)
        self.assertEqual(read.await_count, 2)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(session.web.private_only for session in sessions))
        self.assertEqual(context.cookies["xs"], "new-session")
        for call in read.await_args_list:
            self.assertIs(call.args[2], context)
            self.assertEqual(call.args[3], "interests")
            self.assertEqual(call.args[4], payload)

    async def test_targeting_read_does_not_retry_without_new_auth(self):
        route, HTTPError, _, resolver, sessions, read, timeouts = self.fixture(
            refresh_auth=False, successful_read=False)
        with self.assertRaises(HTTPError) as caught:
            await route("27", "interests", {"q": "Travel", "limit": 50})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "SESSION_EXPIRED")
        self.assertEqual(timeouts, [75])
        self.assertEqual(resolver.resolve.await_count, 1)
        self.assertEqual(read.await_count, 1)
        self.assertEqual(len(sessions), 1)

    async def test_targeting_read_never_retries_with_a_different_actor(self):
        route, HTTPError, _, resolver, sessions, read, timeouts = self.fixture()
        resolver.resolve.return_value.cookies["c_user"] = "987654321"
        with self.assertRaises(HTTPError) as caught:
            await route("27", "interests", {"q": "Travel", "limit": 50})
        self.assertEqual(caught.exception.detail["code"], "SESSION_EXPIRED")
        self.assertEqual(timeouts, [75])
        self.assertEqual(resolver.resolve.await_count, 1)
        self.assertEqual(read.await_count, 1)
        self.assertEqual(len(sessions), 1)


if __name__ == "__main__":
    unittest.main()
