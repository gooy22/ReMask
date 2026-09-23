import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import (
    BrowserBusinessError,
    FacebookBusinessBrowser,
)
from app.provisioning.business_handler import business_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore


class _FakeRequest:
    def __init__(self, post_data: str):
        self.method = "POST"
        self.url = "https://business.facebook.com/api/graphql/"
        self.post_data = post_data


class BrowserNetworkGateTests(unittest.TestCase):
    def test_create_gate_matches_real_creation_mutation_shape(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=useBusinessCreationMutationMutation"
            "&variables=%7B%22input%22%3A%7B%22business_name%22%3A"
            "%22Test%20Business%22%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_create(
                request,
                "Test Business",
            )
        )

    def test_page_gate_matches_mutation_with_business_and_page(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BizKitSettingsAddPageMutation"
            "&variables=%7B%22business_id%22%3A%22555666777888999%22%2C"
            "%22page_id%22%3A%22123456789%22%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_page_add(
                request,
                business_id="555666777888999",
                page_id="123456789",
            )
        )

    def test_page_gate_ignores_non_mutation_search_query(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BusinessPageSearchQuery"
            "&variables=%7B%22business_id%22%3A%22555666777888999%22%2C"
            "%22page_id%22%3A%22123456789%22%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_page_add(
                request,
                business_id="555666777888999",
                page_id="123456789",
            )
        )


class BrowserPageDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_pages_from_rendered_browser_surface(self):
        class _RenderedPage:
            async def wait_for_timeout(self, ms):
                return None

            async def content(self):
                return (
                    '<script type="application/json">'
                    '{"__typename":"Page","id":"123456789",'
                    '"name":"Demo Fan Page","category":"Local business"}'
                    '</script>'
                )

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-pages")
        )
        browser.page = _RenderedPage()
        browser._goto = AsyncMock(return_value="https://www.facebook.com/pages/")

        pages = await browser.discover_managed_pages()

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "123456789")
        self.assertEqual(pages[0]["name"], "Demo Fan Page")
        browser._goto.assert_awaited()


class _FakeBrowser:
    def __init__(
        self,
        *,
        snapshot=None,
        create_id="555666777888999",
        reconcile_id="",
        verify_sequence=None,
    ):
        self.snapshot = snapshot or {"111111111111111": "Existing"}
        self.create_id = create_id
        self.reconcile_id = reconcile_id
        self.verify_sequence = list(verify_sequence or [False, True])
        self.create_calls = 0
        self.reconcile_calls = 0
        self.add_calls = 0
        self.verify_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def snapshot_businesses(self):
        return dict(self.snapshot)

    async def create_business(self, **kwargs):
        self.create_calls += 1
        callback = kwargs.get("before_submit")
        if callback:
            await callback(
                {
                    "phase": "CREATE_SUBMITTED",
                    "business_name": kwargs["business_name"],
                    "business_ids_before": sorted(self.snapshot),
                    "submitted_at": 1,
                }
            )
        return SimpleNamespace(
            business_id=self.create_id,
            before_ids=sorted(self.snapshot),
            after_ids=sorted([*self.snapshot, self.create_id]),
            response_business_id=self.create_id,
            response_friendly_name="MetaBusinessCreate",
            recovered=False,
        )

    async def reconcile_created_business(self, **kwargs):
        self.reconcile_calls += 1
        if not self.reconcile_id:
            raise BrowserBusinessError(
                "CREATE_RESULT_UNKNOWN",
                "No unique Business found",
                retryable=True,
            )
        return SimpleNamespace(
            business_id=self.reconcile_id,
            before_ids=list(kwargs.get("before_ids") or []),
            after_ids=sorted(
                [*(kwargs.get("before_ids") or []), self.reconcile_id]
            ),
            response_business_id="",
            response_friendly_name="",
            recovered=True,
        )

    async def verify_page_attached(self, **kwargs):
        self.verify_calls += 1
        if self.verify_sequence:
            return bool(self.verify_sequence.pop(0))
        return True

    async def add_existing_page(self, **kwargs):
        self.add_calls += 1
        callback = kwargs.get("before_submit")
        if callback:
            await callback(
                {
                    "phase": "PAGE_ADD_SUBMITTED",
                    "business_id": kwargs["business_id"],
                    "primary_page_id": kwargs["page_id"],
                    "page_submitted_at": 2,
                }
            )
        return SimpleNamespace(
            business_id=kwargs["business_id"],
            page_id=kwargs["page_id"],
            already_attached=False,
        )


class _FakeSession:
    def __init__(self, browser):
        self.context = SimpleNamespace(
            profile_id="profile-1",
            email="owner@example.com",
            first_name="Owner",
            last_name="Test",
            display_name="Owner Test",
        )
        self.browser = browser

    async def facebook_business_browser(self):
        return self.browser


class BusinessBrowserFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProvisioningStateStore(
            str(Path(self.tmp.name) / "state.sqlite3")
        )
        await self.store.init()
        self.item_id = "item-1"
        self.profile_id = "profile-1"
        self.scope_key = "scope-1"
        await self.store.set_running(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _run(self, browser, step_state=None):
        return await business_handler(
            _FakeSession(browser),
            {
                "name": "Test Business",
                "page_id": "123456789",
                "user_email": "owner@example.com",
            },
            {
                "profile_id": self.profile_id,
                "scope_key": self.scope_key,
            },
            provisioning_state=self.store,
            item_id=self.item_id,
            profile_id=self.profile_id,
            scope_key=self.scope_key,
            step_state=step_state,
        )

    async def test_create_and_page_add_use_one_browser_flow(self):
        browser = _FakeBrowser(verify_sequence=[False, True])
        result = await self._run(browser)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(result["primary_page_id"], "123456789")
        self.assertEqual(result["transport"], "facebook_business_suite_ui")
        self.assertEqual(browser.create_calls, 1)
        self.assertEqual(browser.add_calls, 1)

        stored = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )
        self.assertEqual(stored["result"]["phase"], "PAGE_CONFIRMED")
        self.assertEqual(
            stored["result"]["business_id"],
            "555666777888999",
        )

    async def test_create_submitted_retry_reconciles_without_second_create(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_SUBMITTED",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
                "resume_from": "VERIFY_CREATE",
            },
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(
            reconcile_id="555666777888999",
            verify_sequence=[True],
        )
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.reconcile_calls, 1)
        self.assertEqual(browser.add_calls, 0)
        self.assertTrue(result["resumed"])

    async def test_page_submitted_retry_verifies_without_second_add(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "PAGE_ADD_SUBMITTED",
                "business_id": "555666777888999",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "resume_from": "VERIFY_PAGE",
            },
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(verify_sequence=[True])
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.add_calls, 0)
        self.assertTrue(result["page"]["recovered"])

    async def test_create_click_intent_retry_reconciles_without_second_create(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_CLICK_INTENT",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
            },
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(
            reconcile_id="555666777888999",
            verify_sequence=[True],
        )
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.reconcile_calls, 1)
        self.assertEqual(browser.add_calls, 0)

    async def test_page_click_intent_retry_verifies_without_second_add(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "PAGE_ADD_CLICK_INTENT",
                "business_id": "555666777888999",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
            },
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(verify_sequence=[True])
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.add_calls, 0)
        self.assertTrue(result["page"]["recovered"])

    async def test_unknown_create_is_terminal_and_never_recreated(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_SUBMITTED",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
            },
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(reconcile_id="")

        with self.assertRaises(ProvisioningError) as raised:
            await self._run(browser, step_state=step_state)

        self.assertEqual(raised.exception.code, "CREATE_RESULT_UNKNOWN")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.reconcile_calls, 1)

    async def test_aborted_create_gate_allows_safe_submit_on_retry(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_CLICK_INTENT",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
            },
        )
        await self.store.fail(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
            "request was blocked before Meta send",
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(verify_sequence=[False, True])
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.reconcile_calls, 0)
        self.assertEqual(browser.create_calls, 1)
        self.assertEqual(browser.add_calls, 1)

    async def test_aborted_page_gate_allows_safe_page_submit_on_retry(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "PAGE_ADD_CLICK_INTENT",
                "business_id": "555666777888999",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
            },
        )
        await self.store.fail(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            "PAGE_CHECKPOINT_FAILED_BEFORE_SEND",
            "page request was blocked before Meta send",
        )
        step_state = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(verify_sequence=[False, True])
        result = await self._run(browser, step_state=step_state)

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.add_calls, 1)


if __name__ == "__main__":
    unittest.main()
