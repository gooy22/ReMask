"""Local DOM reproduction of the 2026-10-07 stacked Add-RK dialogs. No Meta traffic."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from playwright.async_api import async_playwright
from app.facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError
from app.graphql_mutation_capture import GraphqlMutationCapture

BUSINESS = "1428816905866955"
NAME = "ReMask RK 1"

def fixture(*, confirmation=False, chooser_first=True, disabled_next=False, padding=0):
    chooser = '<div id="chooser" role="dialog" style="left:356px;top:126px;z-index:1"><h2>Add an ad account</h2><div role="gridcell" tabindex="0" id="entry" onclick="window.entryClicks++">Create a new ad account<br>Manage ads for your business, brand or organization.<br>Best for: When you do not have access to any existing ad accounts</div></div>'
    details = '<h2>Create a new ad account for this portfolio</h2><label for="name">Ad account name</label><input id="name"><label for="timezone">Time zone</label><select id="timezone"><option value="137">Europe/Kyiv</option></select><label for="currency">Currency</label><select id="currency"><option>USD</option></select>'
    action = '<div role="button" aria-label="Next" id="next" %s onclick="window.nextClicks++; showConfirm()">Next</div>' % ('aria-disabled="true"' if disabled_next else '')
    confirm = '<h2>Confirm ad account</h2><p>Create an ad account for your business</p><div role="button" id="final" onclick="sendCreate()">Create ad account</div>'
    wizard = '<div id="wizard" role="dialog" style="left:456px;top:50px;z-index:2">%s</div>' % (confirm if confirmation else details + action)
    return """<!doctype html><style>
    [role=dialog] { position:absolute;width:568px;height:620px;background:white;border:1px solid; padding:20px; box-sizing:border-box}
    [role=gridcell] {margin-top:40px;height:110px}
    label,input,select {display:block;margin:8px;height:28px}
    #next,#final {position:absolute;right:20px;bottom:20px;padding:10px;background:blue;color:white;cursor:pointer}
    </style>""" + ("<button>unrelated</button>" * padding) + (chooser + wizard if chooser_first else wizard + chooser) + """
    <script>
    window.entryClicks=0; window.nextClicks=0; window.finalClicks=0;
    window.savedName='';
    window.showConfirm=()=>{window.savedName=document.querySelector('#name').value;document.querySelector('#wizard').innerHTML=%s;};
    window.sendCreate=()=>{window.finalClicks++;fetch('/api/graphql/', {method:'POST',body:new URLSearchParams({
    doc_id:'123456789000', fb_api_req_friendly_name:'BizKitSettingsCreateAdAccountMutation',
    variables:JSON.stringify({input:{business_id:'%s',name:window.savedName || '%s',currency:'USD',timezone_id:137}})
    })}).catch(()=>{});};
    </script>""" % (__import__('json').dumps(confirm), BUSINESS, NAME)

class StackedDialogsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.chrome = await self.pw.chromium.launch(headless=True)
        self.page = await self.chrome.new_page(viewport={"width":1280,"height":800})
        self.driver = FacebookBusinessBrowser(SimpleNamespace(profile_id="fixture"))
        self.driver.page = self.page
        await self.page.route("**/*", lambda route: route.abort())

    async def asyncTearDown(self):
        await self.chrome.close()
        await self.pw.stop()

    async def load(self, **kwargs):
        await self.page.set_content(fixture(**kwargs))
        self.driver._ad_account_wizard_rect = await self.driver._capture_ad_account_wizard_rect()

    async def test_next_uses_foreground_details_dialog(self):
        await self.load()
        result = await self.driver._click_ad_account_form_action_by_visible_text("next")
        self.assertTrue(result["clicked"])
        self.assertEqual(await self.page.evaluate("window.nextClicks"), 1)
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)

    async def test_next_does_not_depend_on_dialog_dom_order(self):
        await self.load(chooser_first=False)
        result = await self.driver._click_ad_account_form_action_by_visible_text("next")
        self.assertTrue(result["clicked"])
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)

    async def test_chooser_is_never_a_final_create(self):
        await self.load()
        checkpoint = AsyncMock()
        result = await self.driver._click_ad_account_final_interactive(before_click=checkpoint)
        self.assertFalse(result["attempted"])
        checkpoint.assert_not_awaited()
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)

    async def test_final_matches_only_the_confirmation_action(self):
        await self.load(confirmation=True)
        checkpoint = AsyncMock()
        result = await self.driver._click_ad_account_final_interactive(before_click=checkpoint)
        self.assertTrue(result["clicked"])
        checkpoint.assert_awaited_once()
        self.assertEqual(await self.page.evaluate("window.finalClicks"), 1)
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)

    async def test_final_over_global_locator_limit_uses_dialog_recovery(self):
        await self.load(confirmation=True, padding=130)
        result = await self.driver._click_ad_account_final_interactive()
        self.assertTrue(result["clicked"])
        self.assertEqual(await self.page.evaluate("window.finalClicks"), 1)

    async def test_final_semantic_fallback_rejects_chooser(self):
        await self.load()
        result = await self.driver._click_ad_account_form_action_by_visible_text("final")
        self.assertFalse(result["clicked"])
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)

    async def test_final_over_locator_limit_handles_duplicate_aria_and_text(self):
        await self.load(confirmation=True, padding=130)
        await self.page.locator("#final").evaluate("el => el.setAttribute('aria-label','Create ad account')")
        result = await self.driver._click_ad_account_final_interactive()
        self.assertTrue(result["clicked"])
        self.assertEqual(await self.page.evaluate("window.finalClicks"), 1)

    async def test_localized_chooser_gridcell_is_never_final(self):
        await self.load()
        await self.page.locator("#wizard").evaluate("el=>el.remove()")
        await self.page.locator("#entry").evaluate("el=>el.textContent='Créer un compte publicitaire'")
        result = await self.driver._click_ad_account_final_interactive()
        self.assertFalse(result["attempted"])
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)
        semantic = await self.driver._click_ad_account_form_action_by_visible_text("final")
        self.assertFalse(semantic["clicked"])

    async def test_capture_route_matches_slash_and_query_endpoints(self):
        for url in ("https://wizard.test/api/graphql/", "https://wizard.test/api/graphql/?method=post", "https://wizard.test/api/graphql"):
            with self.subTest(url=url):
                async with GraphqlMutationCapture(self.page, matcher=lambda req, meta: meta["doc_id"] == "123456789000") as capture:
                    await self.page.evaluate("""url => {
                        fetch(url,{method:'POST',body:new URLSearchParams({doc_id:'123456789000',variables:'{"input":{"name":"fixture"}}'})}).catch(()=>{});
                    }""",url)
                    row = await capture.wait(1.0)
                    self.assertEqual(row["doc_id"], "123456789000")

    async def test_capture_advances_next_then_aborts_exactly_one_create(self):
        await self.load()
        async def open_form(**kwargs):
            await self.page.locator("#name").fill(NAME)
        self.driver._open_ad_account_create_form = open_form
        # Navigate a synthetic origin. Every request is fulfilled locally or aborted.
        await self.page.unroute("**/*")
        await self.page.route("https://wizard.test/", lambda route: route.fulfill(body=fixture(),content_type="text/html"))
        await self.page.route("**/api/graphql/**", lambda route: route.abort())
        await self.page.goto("https://wizard.test/")
        checkpoints = []
        async def save(patch):
            checkpoints.append(patch)
        try:
            row = await self.driver.capture_ad_account_create_request(
                business_id=BUSINESS, account_name=NAME, currency="USD",timezone_id=137,checkpoint=save)
        except BrowserBusinessError as error:
            print("FIXTURE_DIAGNOSTIC", __import__("json").dumps(error.diagnostic, default=str))
            print("FIXTURE_COUNTS", await self.page.evaluate("({entry:window.entryClicks,next:window.nextClicks,final:window.finalClicks})"))
            raise
        self.assertEqual(row["doc_id"], "123456789000")
        self.assertEqual(row["variables"]["input"]["name"], NAME)
        self.assertEqual(await self.page.evaluate("window.entryClicks"), 0)
        self.assertEqual(await self.page.evaluate("window.nextClicks"), 1)
        self.assertEqual(await self.page.evaluate("window.finalClicks"), 1)
        self.assertEqual(len(checkpoints), 1)

    async def test_disabled_next_does_not_arm_final_or_checkpoint_create(self):
        await self.load(disabled_next=True)
        async def open_form(**kwargs):
            await self.page.locator("#name").fill(NAME)
        self.driver._open_ad_account_create_form = open_form
        self.driver._diagnostic = AsyncMock(return_value={"stage":"fixture"})
        checkpoint = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.driver.capture_ad_account_create_request(
                business_id=BUSINESS,account_name=NAME,currency="USD",timezone_id=137,checkpoint=checkpoint)
        self.assertEqual(caught.exception.code, "AD_ACCOUNT_CREATE_UI_CHANGED")
        self.assertFalse(self.driver.ad_account_final_capture_armed)
        self.assertFalse(self.driver.ad_account_create_may_have_been_sent)
        checkpoint.assert_not_awaited()

if __name__ == "__main__":
    unittest.main(verbosity=2)
