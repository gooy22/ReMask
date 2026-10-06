"""Exercise the actual fill, selection and request wizard in isolated Chromium."""
from __future__ import annotations

import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.facebook_page_search import (
    click_exact_page_search_result, owned_page_actor, page_lookup_url, page_query_kind,
)
from app.provisioning.page_access_handler import _request_target_page_access

PAGE = "1323505007517351"
ACTOR = "61595183909581"
NAME = "PrgssTeam"
BUSINESS = "1463351668989938"
ROWS = [{"id": PAGE, "profile_id": ACTOR, "name": NAME, "ownership_verified": True}]
ASSET_URL = f"https://www.facebook.com/{PAGE}"
ACTOR_URL = f"https://www.facebook.com/profile.php?id={ACTOR}"


class PageLookupIdentityTests(unittest.TestCase):
    def test_only_unique_verified_actor_changes_lookup_url(self):
        self.assertEqual(owned_page_actor(ROWS, PAGE), ACTOR)
        self.assertEqual(page_lookup_url(ROWS, PAGE), ACTOR_URL)
        for rows in ([], [{**ROWS[0], "ownership_verified": False}],
                     ROWS + [{**ROWS[0], "profile_id": "9999999999"}]):
            with self.subTest(rows=rows):
                self.assertEqual(page_lookup_url(rows, PAGE), ASSET_URL)

    def test_queries_accept_exact_asset_and_verified_actor_and_reject_wrong_identity(self):
        for query in (ASSET_URL, ACTOR_URL, NAME):
            with self.subTest(query=query):
                self.assertTrue(page_query_kind(ROWS, PAGE, NAME, query))
        for query in (
            "https://www.facebook.com/9999999999",
            "https://www.facebook.com/profile.php?id=9999999999",
            f"https://facebook.example/{PAGE}",
            f"https://www.facebook.com.evil.example/{PAGE}",
            f"https://user@www.facebook.com/{PAGE}",
            f"https://www.facebook.com/profile.php?id={ACTOR}&id={PAGE}",
        ):
            with self.subTest(query=query):
                self.assertFalse(page_query_kind(ROWS, PAGE, NAME, query))
        self.assertFalse(page_query_kind([], PAGE, NAME, ACTOR_URL))
        self.assertFalse(page_query_kind([], PAGE, NAME, NAME))


class PageSharedSearchChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        executable = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                           if (path := shutil.which(name))), None)
        if not executable:
            self.skipTest("No local Chromium installed")
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.chromium = await self.playwright.chromium.launch(
            executable_path=executable, headless=True, args=["--no-sandbox"])
        self.page = await self.chromium.new_page()
        # Fixtures must never contact Meta or any other external host.
        await self.page.route("**/*", lambda route: route.abort())
        self.browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="fixture", pages=ROWS))
        self.browser.page = self.page

    async def asyncTearDown(self):
        if hasattr(self, "chromium"):
            await self.chromium.close()
            await self.playwright.stop()

    async def fixture(self, result: str, *, noise: str = "", query: str = ACTOR_URL):
        await self.page.set_content(
            noise + '<input placeholder="Facebook Page name or URL" aria-controls="results">'
            '<div id="results" role="listbox">' + result + '</div>'
            '<script>window.selected=[];</script>')
        await self.page.get_by_placeholder("Facebook Page name or URL", exact=True).fill(query)

    def option(self, identity: str = PAGE, *, key: str = "exact"):
        return (f'<div role="option" data-page-id="{identity}" '
                f'onclick="window.selected.push(\'{key}\')"><span>{NAME}</span></div>')

    async def select(self, *, rows=ROWS, wait=0.2):
        return await click_exact_page_search_result(
            self.page, rows, PAGE, NAME, wait_seconds=wait)

    async def test_fill_default_verified_name_selects_exact_identity_among_same_names(self):
        await self.fixture(self.option() + self.option("9999999999", key="wrong"), query="")
        self.assertTrue(await self.browser._fill_page_add_identifier(
            labels=("Facebook Page name or URL",), value=PAGE))
        self.assertEqual(await self.page.locator("input").input_value(), NAME)
        self.assertTrue(await self.browser._click_exact_page_search_name(PAGE, NAME))
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])

    async def test_shared_lookup_override_preserves_canonical_actor_url_and_ignores_sidebar(self):
        noise = f'<h2>{NAME}</h2><button>{NAME}</button>'
        await self.fixture(self.option(), noise=noise, query="")
        self.assertTrue(await self.browser._fill_page_add_identifier(
            labels=("Facebook Page name or URL",), value=PAGE,
            lookup_override=page_lookup_url(ROWS, PAGE)))
        self.assertEqual(await self.page.locator("input").input_value(), ACTOR_URL)
        self.assertTrue(await self.browser._click_exact_page_search_name(PAGE, NAME))
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])
        self.assertEqual(await self.page.locator("[data-remask-exact-page-search]").count(), 0)

    async def test_actor_link_in_option_proves_identity_for_name_query(self):
        await self.fixture(
            f'<a role="option" href="{ACTOR_URL}" onclick="event.preventDefault();window.selected.push(\'exact\')">'
            f'<span>{NAME}</span></a>', query=NAME)
        selected, diagnostic = await self.select()
        self.assertTrue(selected, diagnostic)
        self.assertTrue(diagnostic["selected"]["id_match"])
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])

    async def test_unverified_name_never_selects_unknown_same_name(self):
        await self.fixture(f'<button onclick="window.selected.push(\'unknown\')">{NAME}</button>', query=NAME)
        selected, diagnostic = await self.select()
        self.assertFalse(selected, diagnostic)
        self.assertEqual(await self.page.evaluate("window.selected"), [])

    async def test_wrong_identity_is_rejected_even_when_only_result(self):
        await self.fixture(self.option("9999999999", key="wrong"))
        selected, diagnostic = await self.select()
        self.assertFalse(selected, diagnostic)
        self.assertEqual(diagnostic["reason"], "result_missing")
        self.assertEqual(await self.page.evaluate("window.selected"), [])

    async def test_two_real_unknown_options_remain_ambiguous(self):
        await self.fixture(
            f'<button onclick="window.selected.push(\'one\')">{NAME}</button>'
            f'<button onclick="window.selected.push(\'two\')">{NAME}</button>', query=ASSET_URL)
        selected, diagnostic = await self.select(rows=[])
        self.assertFalse(selected, diagnostic)
        self.assertEqual(diagnostic["reason"], "result_not_unique")
        self.assertEqual(await self.page.evaluate("window.selected"), [])

    async def test_unique_asset_url_result_does_not_require_private_meta_attributes(self):
        await self.fixture(f'<button onclick="window.selected.push(\'exact\')">{NAME}</button>', query=ASSET_URL)
        selected, diagnostic = await self.select(rows=[])
        self.assertTrue(selected, diagnostic)
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])

    async def test_nested_text_and_button_deduplicate_same_option(self):
        await self.fixture(
            f'<div role="option" data-page-id="{PAGE}" onclick="window.selected.push(\'exact\')">'
            f'<span>{NAME}</span><button>{NAME}</button></div>')
        selected, diagnostic = await self.select()
        self.assertTrue(selected, diagnostic)
        self.assertEqual(len(diagnostic["candidates"]), 1)
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])

    async def test_delayed_autocomplete_is_waited_for_without_resubmitting(self):
        await self.fixture("")
        await self.page.evaluate(
            "(html) => setTimeout(() => document.getElementById('results').innerHTML=html, 1100)",
            self.option())
        selected, diagnostic = await self.select(wait=2.5)
        self.assertTrue(selected, diagnostic)
        self.assertGreater(diagnostic["polls"], 1)
        self.assertEqual(await self.page.evaluate("window.selected"), ["exact"])

    async def test_wrong_query_never_clicks_even_if_correct_page_text_exists(self):
        await self.fixture(self.option(), query="https://www.facebook.com/9999999999")
        selected, diagnostic = await self.select()
        self.assertFalse(selected, diagnostic)
        self.assertEqual(diagnostic["reason"], "query_identity_mismatch")
        self.assertEqual(await self.page.evaluate("window.selected"), [])

    async def test_actual_request_wizard_selects_page_ads_only_and_checkpoints_once(self):
        await self.page.set_content(f"""
            <h2>{NAME}</h2><button onclick="window.ownershipClaims++">Add an existing Page</button>
            <div role="dialog" id="find">
              <input placeholder="Facebook Page name or URL" aria-controls="results">
              <div role="listbox" id="results">
                <div role="option" data-page-id="{PAGE}"
                  onclick="window.selected.push('{PAGE}');document.getElementById('next').disabled=false">
                  <span>{NAME}</span>
                </div>
                {self.option("9999999999", key="wrong")}
              </div>
              <button id="next" disabled onclick="window.query=document.querySelector('input').value;
                document.getElementById('find').hidden=true;document.getElementById('review').hidden=false">Next</button>
            </div>
            <div role="dialog" id="review" hidden>
              <label><input type="checkbox" id="ads" onchange="document.getElementById('confirm').disabled=!this.checked">Ads</label>
              <label><input type="checkbox" id="full">Full control</label>
              <button id="confirm" disabled onclick="window.requests++">Confirm</button>
            </div>
            <script>window.selected=[];window.requests=0;window.ownershipClaims=0;window.query='';</script>
        """)
        self.browser._open_pages_add_action = AsyncMock(return_value=True)
        self.browser._click_named = AsyncMock(return_value=True)
        self.browser.verify_page_attached = AsyncMock(side_effect=[False, True])
        self.browser._diagnostic = AsyncMock(return_value={"stage": "fixture"})
        events = []

        async def checkpoint(data):
            events.append(data["phase"])
            if data["phase"] == "TARGET_PAGE_ACCESS_CLICK_INTENT":
                self.assertEqual(await self.page.evaluate("window.requests"), 0)
                self.assertEqual(data["requested_tasks"], ["ADVERTISE"])
            else:
                self.assertEqual(await self.page.evaluate("window.requests"), 1)

        result = await _request_target_page_access(
            self.browser, {"page_id": PAGE, "name": NAME}, BUSINESS, checkpoint, {})
        self.assertTrue(result)
        self.assertEqual(events, ["TARGET_PAGE_ACCESS_CLICK_INTENT", "TARGET_PAGE_ACCESS_SUBMITTED"])
        self.assertEqual(await self.page.evaluate("window.query"), ACTOR_URL)
        self.assertEqual(await self.page.evaluate("window.selected"), [PAGE])
        self.assertEqual(await self.page.evaluate("window.requests"), 1)
        self.assertEqual(await self.page.evaluate("window.ownershipClaims"), 0)
        self.assertTrue(await self.page.locator("#ads").is_checked())
        self.assertFalse(await self.page.locator("#full").is_checked())
        # Resuming a submitted request must not open or send another request.
        self.browser._open_pages_add_action.reset_mock()
        self.assertFalse(await _request_target_page_access(
            self.browser, {"page_id": PAGE, "name": NAME}, BUSINESS, checkpoint,
            {"phase": "TARGET_PAGE_ACCESS_SUBMITTED"}))
        self.browser._open_pages_add_action.assert_not_awaited()
        self.assertEqual(await self.page.evaluate("window.requests"), 1)

    async def test_wrong_page_stops_request_before_checkpoint_and_confirm(self):
        await self.fixture(self.option("9999999999", key="wrong"))
        self.browser._open_pages_add_action = AsyncMock(return_value=True)
        self.browser._click_named = AsyncMock(return_value=True)
        self.browser.verify_page_attached = AsyncMock(return_value=False)
        checkpoint = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await _request_target_page_access(
                self.browser, {"page_id": PAGE, "name": NAME}, BUSINESS, checkpoint, {})
        self.assertEqual(caught.exception.code, "PAGE_SHARE_PAGE_UNVERIFIED")
        self.assertEqual(caught.exception.diagnostic["page_search"]["reason"], "result_missing")
        self.assertEqual(await self.page.evaluate("window.selected"), [])
        checkpoint.assert_not_awaited()
