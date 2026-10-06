"""Chromium coverage of Add existing Page and full rights, never Ads-only success."""
from __future__ import annotations
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError
from app.facebook_page_discovery import business_page_owned_proven, browser_business_page_owned_proven
from app.provisioning.page_full_control import ensure_existing_page_full_control, _operator_full_proof, MODE

PAGE="1323505007517351"
ACTOR="61595183909581"
BM="991479610630943"
UID="61594596674774"
URL="https://business.facebook.com/latest/settings/pages/?business_id="+BM
CONFIG={"page_id":PAGE,"name":"PrgssTeam"}

class OwnedPageEvidenceTests(unittest.TestCase):
    def test_only_exact_owned_connection_is_ownership(self):
        for key,expected in (("owned_pages",True),("client_pages",False),("page_assets",False)):
            with self.subTest(key=key):
                payload={"id":BM,"__typename":"Business",""+key:{"nodes":[{"id":PAGE,"__typename":"Page"}]}}
                self.assertEqual(business_page_owned_proven(payload,BM,PAGE),expected)
                self.assertFalse(business_page_owned_proven(payload,"111111111",PAGE))
                self.assertFalse(business_page_owned_proven(payload,BM,"111111111"))
    def test_explicit_asset_owner_requires_exact_business_and_page(self):
        for owner,flag,expected in ((BM,True,True),("111111111",True,False),(BM,False,False)):
            with self.subTest(owner=owner,flag=flag):
                payload={"data":{"business_assets":{"nodes":[{"id":PAGE,"__typename":"Page",
                    "owning_business":{"id":owner},"is_owned":flag}]}}}
                self.assertEqual(business_page_owned_proven(payload,BM,PAGE,request_scoped=True),expected)
                self.assertFalse(business_page_owned_proven(payload,BM,PAGE))
    def test_client_access_primary_page_and_unrelated_script_never_prove_ownership(self):
        for value in ({"id":BM,"primary_page":{"id":PAGE}},
                      {"id":PAGE,"is_owned":True},
                      {"id":"111111111","__typename":"Business","owned_pages":{"nodes":[{"id":PAGE}]}}):
            self.assertFalse(browser_business_page_owned_proven("<script>"+json.dumps(value)+"</script>",BM,PAGE))
    def test_embedded_owned_page_script_proves_exact_identity(self):
        payload={"data":{"business":{"id":BM,"owned_pages":{"edges":[{"node":{"id":PAGE}}]}}}}
        self.assertTrue(browser_business_page_owned_proven("<script>"+json.dumps(payload)+"</script>",BM,PAGE))

HTML=r"""
<meta charset="utf-8">
<script>
window.owned=__OWNED__;window.full=__FULL__;window.claims=0;window.assigns=0;window.partialRequests=0;
function renderPeople(){
 document.getElementById('people').innerHTML='<div role="row">You '+(window.full?'Full control':'Partial access: Ads')+'</div>';
}
function search(){document.getElementById('results').hidden=false;}
function selectPage(){document.getElementById('results').hidden=true;document.getElementById('next').hidden=false;}
function reviewPage(){document.getElementById('find').hidden=true;document.getElementById('review').hidden=false;}
async function claim(){
 window.claims++;
 await fetch('/api/graphql/',{method:'POST',body:new URLSearchParams({
   fb_api_req_friendly_name:'BizKitClaimPageToBusinessMutation',
   variables:JSON.stringify({input:{business_id:'__BM__',page_id:'__PAGE__'}})
 })});
 window.owned=true;document.getElementById('add-dialog').hidden=true;
 document.getElementById('page-row').hidden=false;
}
function save(){
 window.assigns++;
 if(!__STUCK__) window.full=document.getElementById('full-control').checked;
 document.getElementById('assign-dialog').hidden=true;renderPeople();
}
</script>
<h1>Pages</h1><button onclick="document.getElementById('menu').hidden=false">Add</button>
<div id="menu" hidden><button onclick="document.getElementById('menu').hidden=true;document.getElementById('add-dialog').hidden=false">
Add an existing Page</button><button onclick="window.partialRequests++">Request shared access to a Facebook Page</button></div>
<div role="dialog" aria-label="Add an existing Page" id="add-dialog" hidden>
 <div id="find"><h2>Find an existing Page</h2>
 <input role="combobox" aria-controls="results" placeholder="Facebook Page name or URL" oninput="search()">
 <div role="listbox" id="results" hidden>
 <button role="option" data-page-id="__PAGE__" onclick="selectPage()">
 <span>PrgssTeam</span><a href="https://www.facebook.com/profile.php?id=__ACTOR__">Page profile</a></button></div>
 <button id="next" hidden onclick="reviewPage()">Next</button></div>
 <div id="review" hidden><h2>Add Page to business portfolio</h2>
 <label><input type="checkbox">I agree to the terms</label><button onclick="claim()">Add Page</button></div>
</div>
<div role="row" id="page-row"><button onclick="document.getElementById('detail').hidden=false">PrgssTeam</button> __PAGE__</div>
<div id="detail" hidden><h2>PrgssTeam</h2><h3>People</h3><div id="people"></div>
 <button onclick="document.getElementById('assign-dialog').hidden=false">Assign people</button></div>
<div role="dialog" aria-label="Assign people" id="assign-dialog" hidden>
 <label><input type="checkbox">You</label>__DUPLICATE__
 <label><input id="full-control" type="checkbox" __DISABLED__>Full control (Everything)</label>
 <label><input type="checkbox">Ads</label><button onclick="save()">Assign</button>
</div>
<script>renderPeople();</script>
"""

class ExistingPageFullControlChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        executable=next((path for name in ("google-chrome","chromium","chromium-browser")
                         if (path:=shutil.which(name))),None)
        if not executable:
            self.skipTest("No local Chromium installed")
        from playwright.async_api import async_playwright
        self.pw=await async_playwright().start()
        self.chromium=await self.pw.chromium.launch(executable_path=executable,headless=True,args=["--no-sandbox"])
        self.page=await self.chromium.new_page()
        self.js_errors=[]
        self.page.on('pageerror',lambda error:self.js_errors.append(str(error)))
        self.context=SimpleNamespace(profile_id="13",cookies={"c_user":UID},pages=[
            {"id":PAGE,"profile_id":ACTOR,"name":"PrgssTeam","ownership_verified":True}])
        self.browser=FacebookBusinessBrowser(self.context)
        self.browser.page=self.page
        self.browser._diagnostic=AsyncMock(return_value={"stage":"fixture"})
        self.patches=[]
    async def asyncTearDown(self):
        if hasattr(self,"chromium"):
            await self.chromium.close()
            await self.pw.stop()
            self.assertEqual(self.js_errors,[], 'The isolated Meta fixture must not have JavaScript errors')
    async def fixture(self,owned=False,full=False,stuck=False,duplicate=False,disabled=False):
        html=HTML
        for key,value in {"OWNED":str(owned).lower(),"FULL":str(full).lower(),"STUCK":str(stuck).lower(),
                          "BM":BM,"PAGE":PAGE,"ACTOR":ACTOR,
                          "DUPLICATE":'<label><input type="checkbox">You</label>' if duplicate else "",
                          "DISABLED":"disabled" if disabled else ""}.items():
            html=html.replace("__"+key+"__",value)
        async def route_handler(route):
            if "/api/graphql/" in route.request.url:
                await route.fulfill(content_type="application/json",body='{"data":{"claim_page":{"success":true}}}')
            elif "/settings/pages" in route.request.url:
                await route.fulfill(content_type="text/html; charset=utf-8",body=html)
            else:
                await route.abort()
        await self.page.route("**/*",route_handler)
        async def verify(**kwargs):
            self.assertEqual(kwargs,{"business_id":BM,"page_id":PAGE,"require_owned":True})
            if self.page.url=="about:blank":
                await self.page.goto(URL)
            return await self.page.evaluate("window.owned")
        self.browser.verify_page_attached=AsyncMock(side_effect=verify)
    async def checkpoint(self,patch_value):
        self.patches.append(patch_value)
    async def run_flow(self,prior=None,config=None):
        return await ensure_existing_page_full_control(self.browser,config or CONFIG,BM,self.checkpoint,prior or {})
    async def counts(self):
        return await self.page.evaluate("({claims:window.claims,assigns:window.assigns,partial:window.partialRequests})")

    async def test_partial_partner_access_is_upgraded_via_existing_page_and_full_control(self):
        await self.fixture()
        result=await self.run_flow({"phase":"TARGET_PAGE_ACCESS_OWNER_CONFIRMED"})
        self.assertTrue(result["page_owned_by_business"])
        self.assertTrue(result["operator_full_control_verified"])
        self.assertEqual(await self.counts(),{"claims":1,"assigns":1,"partial":0})
        phases=[row.get("phase") for row in self.patches]
        self.assertIn("TARGET_PAGE_ACCESS_FULL_ADD_CLICK_INTENT",phases)
        self.assertIn("TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED",phases)
        self.assertIn("TARGET_PAGE_OPERATOR_FULL_ASSIGN_CLICK_INTENT",phases)
        self.assertEqual(phases[-1],"TARGET_PAGE_OPERATOR_FULL_ASSIGN_CONFIRMED")
        self.assertEqual(self.browser.verify_page_attached.await_count,3)
        self.assertFalse(await self.page.get_by_role("checkbox",name="Ads",exact=True).is_checked())
    async def test_already_owned_page_still_upgrades_partial_profile(self):
        await self.fixture(owned=True)
        await self.run_flow()
        self.assertEqual(await self.counts(),{"claims":0,"assigns":1,"partial":0})
    async def test_exact_profile_full_control_is_verified_without_another_assignment(self):
        await self.fixture(owned=True,full=True)
        await self.run_flow()
        self.assertEqual(await self.counts(),{"claims":0,"assigns":0,"partial":0})
    async def test_full_assignment_pending_is_read_only_and_keeps_its_phase(self):
        await self.fixture(owned=True)
        for phase in ("TARGET_PAGE_OPERATOR_FULL_ASSIGN_CLICK_INTENT","TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED"):
            self.patches=[]
            with self.assertRaises(BrowserBusinessError) as caught:
                await self.run_flow({"phase":phase,"access_mode":MODE})
            self.assertEqual(caught.exception.code,"PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN")
            self.assertFalse(any("phase" in patch_value for patch_value in self.patches))
        self.assertEqual(await self.counts(),{"claims":0,"assigns":0,"partial":0})
    async def test_pending_assignment_reconciles_exact_full_row_without_replay(self):
        await self.fixture(owned=True,full=True)
        await self.run_flow({"phase":"TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED","access_mode":MODE})
        self.assertEqual(await self.counts(),{"claims":0,"assigns":0,"partial":0})
    async def test_no_full_toggle_does_not_fall_back_to_ads(self):
        await self.fixture(owned=True,disabled=True)
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.run_flow()
        self.assertEqual(caught.exception.code,"PAGE_FULL_CONTROL_NOT_AVAILABLE")
        self.assertEqual((await self.counts())["assigns"],0)
    async def test_duplicate_operator_is_never_assigned(self):
        await self.fixture(owned=True,duplicate=True)
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.run_flow()
        self.assertEqual(caught.exception.code,"PAGE_OPERATOR_IDENTITY_UNAVAILABLE")
        self.assertEqual((await self.counts())["assigns"],0)
    async def test_save_without_full_row_stays_pending_and_does_not_replay(self):
        await self.fixture(owned=True,stuck=True)
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.run_flow()
        self.assertEqual(caught.exception.code,"PAGE_OPERATOR_FULL_CONTROL_UNVERIFIED")
        self.assertEqual((await self.counts())["assigns"],1)
        self.assertEqual(self.patches[-1]["phase"],"TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED")
    async def test_save_dispatched_then_timeout_observes_same_full_assignment(self):
        await self.fixture(owned=True)
        from playwright.async_api import Locator, TimeoutError
        original=Locator.click
        async def interrupted(locator,*args,**kwargs):
            await original(locator,*args,**kwargs)
            if 'Assign'==await locator.inner_text():
                raise TimeoutError("fixture transition")
        with patch.object(Locator,"click",interrupted):
            await self.run_flow()
        self.assertEqual((await self.counts())["assigns"],1)
    async def test_other_recorded_owner_cannot_be_silently_replaced_or_shared_ads(self):
        await self.fixture()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.run_flow(config={**CONFIG,"owner_business_id":"111111111","owner_business_confirmed":True})
        self.assertEqual(caught.exception.code,"PAGE_OWNED_BY_ANOTHER_BUSINESS")
        self.assertEqual(await self.counts(),{"claims":0,"assigns":0,"partial":0})
    async def test_full_control_text_inside_open_assignment_is_not_post_save_proof(self):
        await self.fixture(owned=True)
        await self.browser.verify_page_attached(business_id=BM,page_id=PAGE,require_owned=True)
        await self.page.locator("#detail").evaluate("el=>el.hidden=false")
        await self.page.locator("#assign-dialog").evaluate("el=>{el.hidden=false;el.innerHTML='<div role=\"row\">You Full control</div>'}")
        self.assertIsNone(await _operator_full_proof(self.page,UID))

    async def test_pending_ownership_request_never_submits_another_existing_page_add(self):
        await self.fixture()
        self.browser.add_existing_page=AsyncMock()
        approve=AsyncMock(return_value=False)
        with patch("app.provisioning.page_full_control._approve_existing_request",approve):
            with self.assertRaises(BrowserBusinessError) as caught:
                await self.run_flow({"phase":"TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED","access_mode":MODE})
        self.assertEqual(caught.exception.code,"PAGE_OWNERSHIP_RESULT_UNKNOWN")
        self.browser.add_existing_page.assert_not_awaited()
        approve.assert_awaited_once()
        self.assertEqual(await self.counts(),{"claims":0,"assigns":0,"partial":0})
    async def test_pending_owner_approval_is_verification_only(self):
        await self.fixture()
        self.browser.add_existing_page=AsyncMock()
        approve=AsyncMock()
        with patch("app.provisioning.page_full_control._approve_existing_request",approve):
            for phase in ("TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_CLICK_INTENT",
                          "TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_SUBMITTED"):
                with self.assertRaises(BrowserBusinessError):
                    await self.run_flow({"phase":phase,"access_mode":MODE})
        self.browser.add_existing_page.assert_not_awaited()
        approve.assert_not_awaited()
    async def test_real_owned_verifier_reads_exact_embedded_business_page_connection(self):
        payload={"data":{"business":{"id":BM,"owned_pages":{"nodes":[{"id":PAGE,"__typename":"Page"}]}}}}
        async def fixture_route(route):
            await route.fulfill(content_type="text/html; charset=utf-8",
                body="<h1>Pages</h1><script type='application/json'>"+json.dumps(payload)+"</script>")
        await self.page.route("**/*",fixture_route)
        self.browser._assert_authenticated=AsyncMock()
        async def goto(target,**kwargs):
            await self.page.goto(target)
        self.browser._goto=AsyncMock(side_effect=goto)
        result=await FacebookBusinessBrowser.verify_page_attached(self.browser,
            business_id=BM,page_id=PAGE,require_owned=True)
        self.assertTrue(result)
        self.assertIn("business_id="+BM,self.page.url)
