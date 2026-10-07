"""Reproduce a completed RK behind a stale wizard, and full rights, in isolated Chrome."""
from __future__ import annotations

import asyncio
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from app.facebook_ad_account_identity import ad_account_route, details_candidate, read_ad_account_identity
from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.provisioning.ad_account_handler import ad_account_handler
from app.provisioning.ad_account_full_control import ensure_ad_account_full_control
from app.provisioning.models import ProvisioningError

BM = "1109354271000001"
RK = "1383676627000001"
ASSET = "23860603850000001"
UID = "61594848550001"
NAME = "Amber Studio fixture Ads"
URL = "https://business.facebook.com/latest/settings/ad_accounts?business_id=" + BM
MESSAGE = "You've reached the maximum number of ad accounts allowed for a new business portfolio."


def diagnostic(*, business=BM, name=NAME, account=RK):
    return {"stage": "ad_account_create_request_missing", "ui_state": {
        "url": URL.replace(BM, business), "state": "FORM",
        "dialogs": ["Confirm the ad account that you want to create"],
        "controls": [
            name + " 0 people 0 people 0 partners Details [tag=TR role=row x=336 y=185]",
            name + " [tag=DIV role=heading x=693 y=173]",
            account + " [tag=A role=link x=714 y=194]",
            "Assign people [tag=DIV role=button x=637 y=250]",
        ],
    }}


class DetailsCandidateTests(unittest.TestCase):
    def test_stale_confirm_dialog_identifies_candidate_without_inventing_success(self):
        self.assertEqual(details_candidate({"browser_diagnostic": diagnostic()},
            business_id=BM, account_name=NAME), "act_" + RK)

    def test_wrong_portfolio_or_name_or_multiple_account_links_are_not_candidates(self):
        for diag in (diagnostic(business="999999999"), diagnostic(name="Other Ads"),
                     {**diagnostic(), "ui_state": {**diagnostic()["ui_state"], "controls":
                         diagnostic()["ui_state"]["controls"] + [
                             "8888888888888888 [tag=A role=link x=714 y=294]"]}}):
            self.assertEqual(details_candidate({"browser_diagnostic": diag},
                business_id=BM, account_name=NAME), "")

    def test_only_exact_authenticated_portfolio_route_is_accepted(self):
        self.assertTrue(ad_account_route(URL, BM))
        for url in (URL.replace("business.facebook.com", "example.invalid"),
                    URL + "&business_id=999999999", URL.replace("ad_accounts", "pages")):
            self.assertFalse(ad_account_route(url, BM))


class AdAccountDetailsChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        executable = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                           if (path := shutil.which(name))), None)
        if not executable:
            self.skipTest("No Chromium installed")
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.chrome = await self.playwright.chromium.launch(
            executable_path=executable, headless=True, args=["--no-sandbox"])
        self.page = await self.chrome.new_page(viewport={"width": 1400, "height": 900})
        self.requests = []
        async def isolated(route):
            self.requests.append(route.request.url)
            if route.request.is_navigation_request():
                await route.fulfill(status=200, content_type="text/html", body="<html></html>")
            else:
                await route.abort()
        await self.page.route("**/*", isolated)
        await self.page.goto(URL + "&selected_asset_id=" + ASSET + "&selected_asset_type=ad-account")
        self.browser = FacebookBusinessBrowser(SimpleNamespace(
            profile_id="fixture-new", cookies={"c_user": UID}))
        self.browser.page = self.page
        self.browser._goto = AsyncMock()
        self.browser._assert_authenticated = AsyncMock()

    async def asyncTearDown(self):
        if hasattr(self, "chrome"):
            await self.chrome.close()
            await self.playwright.stop()

    async def fixture(self, *, wizard=True, ready=True, duplicate=False, full=False,
                      delayed=False, leave_dialog=False, wrong_id=False):
        pane_link = ('https://adsmanager.facebook.com/adsmanager/manage/accounts?act='
                     + (ASSET if wrong_id else RK))
        pane = ('<section id="details"><header><h2>' + NAME + '</h2><a href="' + pane_link + '">' + RK +
                '</a></header><div role="listitem" id="operator">You ' +
                ("Full control" if full else "Partial access") + '</div>' +
                '<button id="assign" onclick="window.opens++;document.getElementById(\'permissions\').hidden=false">Assign people</button></section>')
        if duplicate:
            pane += pane.replace('id="details"', 'id="sibling"').replace(RK, "9999999999999999")
        after_click = (
            "window.clicks++;window.intentAtClick=window.intentSaved;"
            "document.getElementById('assets').hidden=false;"
            "document.getElementById('details').hidden=false;"
        )
        if delayed:
            after_click = "window.clicks++;window.intentAtClick=window.intentSaved;setTimeout(()=>{" + after_click.replace("window.clicks++;", "") + "},600);"
        await self.page.set_content("""
            <style>
                #assets {position:absolute;left:320px;top:150px;width:560px}
                #details,#sibling {position:absolute;left:900px;top:150px;width:400px}
                #wizard {position:absolute;left:400px;top:100px;width:600px;height:480px;background:#fff}
                #permissions {position:absolute;left:400px;top:100px;width:600px;height:400px;background:#fff}
            </style>
            <table id="assets"><thead><tr><th>Name</th><th>Full access</th></tr></thead>
                <tbody><tr><td><h3>""" + NAME + """</h3></td><td>0 people</td></tr></tbody></table>
            """ + pane + ("""
            <div role="dialog" id="wizard"><h2>Confirm the ad account that you want to create</h2>
                <p>Currency USD. Time zone Kyiv. Ad account details.</p>
                <button id="create">Create ad account</button>
            </div>""" if wizard else "") + """
            <div role="dialog" id="permissions" hidden>
                <label><input type="checkbox" id="person">You</label>
                <label><input type="checkbox" id="full">Full control (Everything)</label>
                <label><input type="checkbox" id="partial">Partial access</label>
                <button id="save">Assign</button>
            </div>
            <script>window.clicks=0;window.opens=0;window.saves=0;window.intentSaved=false;
            document.getElementById('save').onclick=()=>{
                window.saves++;
                if(document.getElementById('person').checked && document.getElementById('full').checked) {
                    document.getElementById('operator').textContent='You Full control';
                }
                """ + ("" if leave_dialog else "document.getElementById('permissions').hidden=true;") + """
            };
            </script>""")
        if wizard:
            await self.page.locator("#create").evaluate("(el, code)=>el.onclick=()=>{eval(code)}", after_click)
        if not ready:
            await self.page.locator("#assets,#details").evaluate_all("els=>els.forEach(el=>el.hidden=true)")

    async def test_exact_row_and_details_prove_canonical_id_behind_stale_confirm_dialog(self):
        await self.fixture()
        result = await read_ad_account_identity(self.page, business_id=BM, account_name=NAME)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertNotEqual(result["ad_account_id"], "act_" + ASSET)
        self.assertEqual(await self.page.locator("#create").count(), 1)

    async def test_wrong_name_wrong_business_and_two_details_identities_are_not_proof(self):
        await self.fixture()
        self.assertFalse((await read_ad_account_identity(self.page, business_id=BM, account_name="Other"))["confirmed"])
        self.assertFalse((await read_ad_account_identity(self.page, business_id="999999999", account_name=NAME))["confirmed"])
        await self.fixture(duplicate=True)
        self.assertFalse((await read_ad_account_identity(self.page, business_id=BM, account_name=NAME))["confirmed"])
        await self.fixture(wrong_id=True)
        self.assertFalse((await read_ad_account_identity(self.page, business_id=BM, account_name=NAME))["confirmed"])

    async def test_lazy_empty_graphql_cannot_override_visible_canonical_details(self):
        await self.fixture(wizard=False)
        self.browser.SETTINGS_AD_ACCOUNTS_URLS = (URL,)
        listeners = {}
        original_on, original_remove = self.page.on, self.page.remove_listener
        self.page.on = lambda event, fn: listeners.__setitem__(event, fn)
        self.page.remove_listener = lambda event, fn: listeners.pop(event, None)
        request = SimpleNamespace(method="POST", url="https://business.facebook.com/api/graphql/", headers={},
            post_data=urlencode({"fb_api_req_friendly_name":"BizKitBusinessAssetsQuery",
                "variables":json.dumps({"business_id":BM})}))
        response = SimpleNamespace(url=request.url, request=request,
            text=AsyncMock(return_value=json.dumps({"data":{"business":{"id":BM,"ad_accounts":{"nodes":[]}}}})))
        async def load(url):
            listeners["response"](response)
            await asyncio.sleep(0)
        self.browser._goto = AsyncMock(side_effect=load)
        try:
            result = await self.browser.find_ad_account_in_inventory(
                business_id=BM, account_name=NAME, timeout_seconds=2)
        finally:
            self.page.on, self.page.remove_listener = original_on, original_remove
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["confirmed_empty"])
        self.assertEqual(result["ad_account_id"], "act_" + RK)

    async def test_nonempty_table_without_canonical_identity_never_confirms_absence(self):
        await self.fixture(wizard=False)
        await self.page.locator("#details").evaluate("el=>el.hidden=true")
        self.browser.SETTINGS_AD_ACCOUNTS_URLS = (URL,)
        result = await self.browser.find_ad_account_in_inventory(
            business_id=BM, account_name=NAME, timeout_seconds=2)
        self.assertFalse(result["confirmed_empty"])
        self.assertTrue(result["ui_identity"]["nonempty"])

    async def run_capture(self, *, delayed=False, timeout_after_click=False):
        await self.fixture(ready=False, delayed=delayed)
        checkpoints = []
        async def save(data):
            checkpoints.append(data)
            if data.get("phase") == "CREATE_CLICK_INTENT":
                await self.page.evaluate("window.intentSaved=true")
        self.browser._open_ad_account_create_form = AsyncMock()
        self.browser._prepare_ad_account_form_fields = AsyncMock(return_value={})
        self.browser._capture_ad_account_wizard_rect = AsyncMock(return_value={})
        self.browser._select_own_business_if_present = AsyncMock(return_value=False)
        self.browser._click_ad_account_form_action_by_visible_text = AsyncMock(return_value={"clicked":False})
        self.browser._accept_ad_account_terms_if_present = AsyncMock(return_value={"clicked":False})
        if timeout_after_click:
            async def timed_out(*, before_click=None):
                if before_click is not None:
                    await before_click()
                await self.page.locator("#create").click()
                return {"attempted":True,"clicked":False}
            self.browser._click_ad_account_final_interactive = timed_out
        result = await self.browser.capture_ad_account_create_request(
            business_id=BM, account_name=NAME, currency="USD", timezone_id=137, checkpoint=save)
        self.assertTrue(result["created_during_capture"])
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(await self.page.evaluate("window.clicks"), 1)
        self.assertTrue(await self.page.evaluate("window.intentAtClick"))
        self.browser._click_ad_account_form_action_by_visible_text.assert_awaited_once_with("next")
        self.assertTrue(any(row.get("phase") == "CREATE_CLICK_INTENT" for row in checkpoints))

    async def test_native_final_click_recovers_created_rk_without_success_dialog(self):
        await self.run_capture()

    async def test_delayed_details_hydration_does_not_repeat_create(self):
        await self.run_capture(delayed=True)

    async def test_dispatched_click_timeout_is_observed_without_second_final_click(self):
        await self.run_capture(timeout_after_click=True)

    async def test_current_profile_receives_full_control_on_the_exact_rk(self):
        await self.fixture(wizard=False)
        checkpoints=[]
        result = await ensure_ad_account_full_control(
            self.browser, BM, RK, NAME, AsyncMock(side_effect=lambda row: checkpoints.append(row)), {})
        self.assertTrue(result["rk_operator_full_control_verified"])
        self.assertEqual(await self.page.evaluate("window.opens"), 1)
        self.assertEqual(await self.page.evaluate("window.saves"), 1)
        self.assertFalse(await self.page.locator("#partial").is_checked())
        self.assertEqual([row["rk_operator_assignment_phase"] for row in checkpoints],
                         ["CLICK_INTENT","SUBMITTED","CONFIRMED"])

    async def test_already_full_control_is_read_only(self):
        await self.fixture(wizard=False, full=True)
        result=await ensure_ad_account_full_control(self.browser,BM,RK,NAME,AsyncMock(),{})
        self.assertTrue(result["rk_operator_full_control_verified"])
        self.assertEqual(await self.page.evaluate("window.opens"),0)
        self.assertEqual(await self.page.evaluate("window.saves"),0)

    async def test_pending_assignment_never_replays_and_wrong_rk_never_opens_assign(self):
        await self.fixture(wizard=False)
        for account, prior in ((RK,{"rk_operator_assignment_phase":"SUBMITTED",
                                   "rk_operator_account_id":RK,"rk_operator_business_id":BM}),
                               ("9999999999999999",{})):
            with self.assertRaises(BrowserBusinessError):
                await ensure_ad_account_full_control(self.browser,BM,account,NAME,AsyncMock(),prior)
        self.assertEqual(await self.page.evaluate("window.opens"),0)
        self.assertEqual(await self.page.evaluate("window.saves"),0)


    async def test_native_business_discovery_filters_personal_scope_from_requests_responses_and_dom(self):
        payload={"data":{"viewer":{"business_id":UID},"businesses":{"nodes":[
            {"__typename":"Business","id":BM,"name":"Fixture business"}]}}}
        html='<html><body><a href="/latest/home?business_id='+UID+'">Personal profile</a>'+ \
            '<a href="/latest/home?business_id='+BM+'">Fixture business</a>'+ \
            '<script>fetch("/api/graphql/",{method:"POST",body:new URLSearchParams('+json.dumps({
                "fb_api_req_friendly_name":"NorthStarBusinessUnifiedScopingSelectorQuery",
                "variables":json.dumps({"firstLevelScopeId":UID,"businessId":UID})})+ \
            ')}).then(r=>r.json())</script></body></html>'
        await self.page.unroute("**/*")
        async def fixture_route(route):
            if "graphql" in route.request.url:
                await route.fulfill(status=200,content_type="application/json",body=json.dumps(payload))
            elif route.request.is_navigation_request():
                await route.fulfill(status=200,content_type="text/html",body=html)
            else:
                await route.abort()
        await self.page.route("**/*",fixture_route)
        businesses=await self.browser.snapshot_businesses()
        self.assertEqual(businesses,{BM:"Fixture business"})
        self.assertEqual(self.browser._last_business_inventory_diagnostic["rejected_personal_scope_ids"],[UID])

    async def test_native_snapshot_recovers_cold_details_without_a_graphql_inventory(self):
        await self.fixture(wizard=False)
        html=await self.page.content()
        await self.page.unroute("**/*")
        async def fixture_route(route):
            if route.request.is_navigation_request():
                await route.fulfill(status=200,content_type="text/html",body=html)
            else:
                await route.abort()
        await self.page.route("**/*",fixture_route)
        self.browser._activate_ad_account_settings_section=AsyncMock(return_value=False)
        result=await self.browser.snapshot_ad_accounts_for_business(
            business_id=BM,timeout_seconds=5.0,expected_account_name=NAME)
        self.assertTrue(result["ready"])
        self.assertFalse(result["confirmed_empty"])
        self.assertTrue(result["accounts_partial"])
        self.assertEqual(result["accounts"][0]["id"],"act_"+RK)
        self.assertEqual(result["accounts"][0]["business_id"],BM)
        self.assertEqual(result["source"],"business_settings_details_live_inventory")

class CreatedDetailsResumeTests(unittest.IsolatedAsyncioTestCase):
    async def run_saved(self, *, verified):
        saved={"phase":"CREATE_NOT_SUBMITTED","business_id":BM,"account_name":NAME,
               "last_error_code":"META_AD_ACCOUNT_CREATE_UNAVAILABLE",
               "browser_diagnostic":diagnostic()}
        changes=[]
        async def save(*args):
            changes.append(dict(args[-1])); saved.update(args[-1]); return dict(saved)
        state=SimpleNamespace(remember_entity=AsyncMock(),forget_entity=AsyncMock(),
            checkpoint=AsyncMock(side_effect=save),latest_ad_account_resume_for_business=AsyncMock())
        session=SimpleNamespace(context=SimpleNamespace(profile_id="fixture-new",cookies={"c_user":UID}))
        with patch("app.provisioning.ad_account_handler._verify_expected_ad_account_in_business",
                   AsyncMock(return_value=(verified,[{"source":"fresh_exact_details"}]))) as proof, \
             patch("app.provisioning.ad_account_handler._reconcile_existing",
                   AsyncMock(side_effect=AssertionError("must not restart CREATE"))) as create:
            call=ad_account_handler(session,{"business_id":BM,"name":NAME,"currency":"USD","timezone_id":137},{},
                profile_id="fixture-new",item_id="saved-created",scope_key="fixture",
                provisioning_state=state,step_state={"result":dict(saved)})
            if verified:
                result=await call
                self.assertEqual(result["ad_account_id"],"act_"+RK)
                self.assertTrue(result["recovered_after_capture_ui_details"])
            else:
                with self.assertRaises(ProvisioningError) as caught:
                    await call
                self.assertEqual(caught.exception.code,"AD_ACCOUNT_CREATE_RESULT_UNKNOWN")
            create.assert_not_awaited()
            self.assertEqual(proof.await_args.kwargs["expected_ad_account_id"],"act_"+RK)
        return changes

    async def test_legacy_quota_failure_resumes_exact_created_rk_after_live_verification(self):
        changes=await self.run_saved(verified=True)
        self.assertEqual(changes[-1]["phase"],"CREATE_CONFIRMED")

    async def test_saved_candidate_without_live_proof_keeps_duplicate_guard(self):
        changes=await self.run_saved(verified=False)
        self.assertEqual(changes[-1]["phase"],"CREATE_RESULT_UNKNOWN")
        self.assertEqual(changes[-1]["create_response_ad_account_id"],"act_"+RK)


class CurrentCaptureDetailsRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_error(self, code, *, verified):
        changes=[]
        async def save(*args):
            changes.append(dict(args[-1])); return dict(args[-1])
        state=SimpleNamespace(remember_entity=AsyncMock(),forget_entity=AsyncMock(),
            checkpoint=AsyncMock(side_effect=save),latest_ad_account_resume_for_business=AsyncMock(return_value={}))
        diag=diagnostic()
        if code=="META_AD_ACCOUNT_CREATE_UNAVAILABLE":
            diag["ui_state"]["state"]="BLOCKED"
            diag["ui_state"]["errors"]=[MESSAGE]
        capture=AsyncMock(side_effect=BrowserBusinessError(code,MESSAGE,retryable=code!="META_AD_ACCOUNT_CREATE_UNAVAILABLE",diagnostic=diag))
        browser=SimpleNamespace(capture_ad_account_create_request=capture)
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False
        session=SimpleNamespace(context=SimpleNamespace(profile_id="fixture-new",cookies={"c_user":UID}))
        with patch("app.provisioning.ad_account_handler.FacebookBusinessBrowser",return_value=Lease()), \
             patch("app.provisioning.ad_account_handler._reconcile_existing",AsyncMock(return_value=("",[]))), \
             patch("app.provisioning.ad_account_handler._reconcile_existing_browser_inventory",
                   AsyncMock(return_value=("",{"confirmed_empty":True}))), \
             patch("app.provisioning.ad_account_handler._verify_expected_ad_account_in_business",
                   AsyncMock(return_value=(verified,[{"source":"fresh_exact_details"}]))), \
             patch("app.provisioning.ad_account_handler._prove_empty_after_uncertainty",
                   AsyncMock(side_effect=AssertionError("positive details cannot be cleared by an empty connection"))) as empty:
            call=ad_account_handler(session,{"business_id":BM,"name":NAME,"currency":"USD","timezone_id":137},{},
                profile_id="fixture-new",item_id="current-created",scope_key="fixture",
                provisioning_state=state,step_state={"result":{}})
            if verified:
                result=await call
                self.assertEqual(result["ad_account_id"],"act_"+RK)
            else:
                with self.assertRaises(ProvisioningError) as caught:
                    await call
                self.assertEqual(caught.exception.code,"AD_ACCOUNT_CREATE_RESULT_UNKNOWN")
            capture.assert_awaited_once()
            empty.assert_not_awaited()
        return changes

    async def test_first_unmatched_capture_recovers_created_rk_without_second_create(self):
        self.assertEqual((await self.run_error("AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED",verified=True))[-1]["phase"],"CREATE_CONFIRMED")

    async def test_quota_for_second_create_recovers_exact_existing_result(self):
        self.assertEqual((await self.run_error("META_AD_ACCOUNT_CREATE_UNAVAILABLE",verified=True))[-1]["phase"],"CREATE_CONFIRMED")

    async def test_unverified_positive_details_never_unlock_a_second_create(self):
        self.assertEqual((await self.run_error("AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED",verified=False))[-1]["phase"],"CREATE_RESULT_UNKNOWN")
