import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.private_launch import PrivateLaunchContractStore, PrivateLaunchService, PrivateLaunchStep, _auto_entity_id, _extract_entity_id
from app.private_launch_read import inspect_pages
from app.provisioning.meta_transport import MetaTransportRouter
from app.provisioning.models import ProvisioningError
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_launch_service import contracts
from tests.test_payment_static_methods import ACCOUNT, BM, account_response, methods_response

PAGE = "111222333"
URL = f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={ACCOUNT}&business_id={BM}"


def page_response():
    return {"data": {"node": {"__typename": "AdAccount", "id": ACCOUNT,
        "promotable_pages": {"edges": [{"node": {"__typename": "Page", "id": PAGE, "name": "Fixture Page"}}]}}}}


class LaunchHttpReadTests(unittest.IsolatedAsyncioTestCase):
    def web(self, response=None, final=URL):
        body = "<script type='application/json'>" + json.dumps(response or page_response()) + "</script>"
        return SimpleNamespace(fetch_text=AsyncMock(return_value=(200, body, final)),
            bootstrap=AsyncMock(), graphql=AsyncMock())

    async def test_exact_positive_scope_never_starts_browser_or_scans_javascript(self):
        web = self.web()
        result = await inspect_pages(web, account=ACCOUNT, business_id=BM)
        self.assertEqual([row["id"] for row in result["data"]], [PAGE])
        self.assertTrue(result["ad_account_page_access_verified"])
        self.assertFalse(result["browser_started"])
        self.assertTrue(web.private_only)
        self.assertEqual(web.fetch_text.await_count, 1)
        self.assertEqual(web.fetch_text.await_args.args, (URL,))
        web.graphql.assert_not_awaited()

    async def test_foreign_scope_actor_list_request_echo_or_partial_never_proves_access(self):
        invalid = []
        value = page_response(); value["data"]["node"]["id"] = "999888777"; invalid.append(value)
        invalid.append({"data": {"viewer": {"pages": {"nodes": [{"id": PAGE, "name": "Owned"}]}}}})
        invalid.append({"variables": {"node": page_response()["data"]["node"]}})
        for marker in ({"errors": [{"message": "fixture"}]}, {"hasNext": True}, {"extensions": {"is_final": False}}):
            invalid.append({**page_response(), **marker})
        for value in invalid:
            with self.subTest(value=value):
                result = await inspect_pages(self.web(value), account=ACCOUNT, business_id=BM)
                self.assertFalse(result["ad_account_page_access_verified"])
        for final in (URL.replace(ACCOUNT, "999888777"), URL.replace(BM, "999888777"), URL.split("&")[0]):
            result = await inspect_pages(self.web(final=final), account=ACCOUNT, business_id=BM)
            self.assertFalse(result["account_scope_verified"])

    async def test_only_actual_login_or_checkpoint_is_auth_gate(self):
        for final, code in (("https://www.facebook.com/login/", "SESSION_EXPIRED"),
                            ("https://www.facebook.com/checkpoint/", "CHECKPOINT_REQUIRED")):
            with self.assertRaises(ProvisioningError) as caught:
                await inspect_pages(self.web(final=final), account=ACCOUNT, business_id=BM)
            self.assertEqual(caught.exception.code, code)
        result = await inspect_pages(self.web(), account=ACCOUNT, business_id=BM)
        self.assertTrue(result["checked_live"])

    async def test_full_preflight_uses_static_billing_and_same_http_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = ProvisioningStateStore(str(Path(temporary) / "state.sqlite"))
            service = PrivateLaunchService(state, contracts=PrivateLaunchContractStore(json.dumps(contracts())))
            context = SimpleNamespace(profile_id="fixture", inventory_updated_at=int(time.time()),
                ad_accounts=[{"ad_account_id": ACCOUNT, "business_id": BM, "account_status": 1}])
            response = methods_response()
            for key in ("billing_payment_methods_allowlist_customized", "primary_funding_source_customized"):
                response["data"]["billable_account_by_asset_id"]["billing_payment_account"][key][0]["credential"]["needs_verification"] = False
            web = self.web(); web.graphql.side_effect = [account_response(), response]
            session = SimpleNamespace(facebook_web=AsyncMock(return_value=web),
                browser_lease=lambda **kw: self.fail("Chromium must not be used"))
            result = await service._live_preflight(context=context, profile_id="fixture", business_id=BM,
                ad_account_id=ACCOUNT, page_id=PAGE, transport=MetaTransportRouter(session, private_only=True))
            self.assertFalse(result["browser_started"])
            self.assertEqual(result["selected_payment_method"]["last4"], "1234")
            self.assertEqual([call.args[0] for call in web.graphql.await_args_list],
                ["28797973873175785", "28814526004898205"])
            self.assertTrue(all(call.kwargs["business_context_id"] == BM for call in web.graphql.await_args_list))
            self.assertNotIn("PRIVATE ADDRESS", json.dumps(result))
            # A linked instrument requiring Meta confirmation cannot be marked ready.
            web.graphql.side_effect = [account_response(), methods_response()]
            with self.assertRaises(ProvisioningError) as caught:
                await service._live_preflight(context=context, profile_id="fixture", business_id=BM,
                    ad_account_id=ACCOUNT, page_id=PAGE, transport=MetaTransportRouter(session, private_only=True))
            self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_PAYMENT_CONFIRMATION_REQUIRED")

    async def test_rotated_credentials_invalidate_review_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = PrivateLaunchService(ProvisioningStateStore(str(Path(temporary)/"state.sqlite")),
                contracts=PrivateLaunchContractStore(json.dumps(contracts())))
            context = SimpleNamespace(inventory_updated_at=123, cookies={"xs": "first"}, proxy="proxy-a", user_agent="ua")
            probe = AsyncMock(return_value={"page_access_checked_live": True})
            kwargs = dict(context=context, profile_id="fixture", business_id=BM, ad_account_id=ACCOUNT, page_id=PAGE)
            with patch.object(service, "_live_preflight", probe):
                await service._review_preflight(**kwargs)
                await service._review_preflight(**kwargs)
                self.assertEqual(probe.await_count, 1)
                context.cookies["xs"] = "second"
                await service._review_preflight(**kwargs)
                context.proxy = "proxy-b"
                await service._review_preflight(**kwargs)
                self.assertEqual(probe.await_count, 3)

    def test_error_partial_or_metadata_id_cannot_be_success(self):
        for marker in ({"errors": [{}]}, {"error": 123}, {"hasNext": True}, {"extensions": {"is_final": False}}):
            value = {"data": {"create": {"id": "123456789"}}, **marker}
            self.assertEqual(_extract_entity_id(value, ("data.create.id",)), ("", ""))
            self.assertEqual(_auto_entity_id(value, PrivateLaunchStep.AD, excluded_ids=set()), ("", ""))
        value = {"data": {"create": {}}, "extensions": {"trace": {"id": "123456789"}}}
        self.assertEqual(_auto_entity_id(value, PrivateLaunchStep.AD, excluded_ids=set()), ("", ""))
