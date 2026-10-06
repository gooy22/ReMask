"""Real Chromium coverage of Meta city search and complete selected-RK binding."""
from __future__ import annotations
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.payment_timezone_picker import choose_payment_timezone, kyiv_label, payment_timezone_selected
from app.payment_card_binding import configure_payment_account, payment_card_flow

SETUP = {"country": "UA", "currency": "USD", "timezone": "Europe/Kyiv"}
CARD = {"number": "4111111111111111", "month": 12, "year": 2099, "holder": "Fixture"}
BUSINESS = "987654321"


class TimezoneIdentityTests(unittest.TestCase):
    def test_city_and_iana_aliases_accept_both_dst_offsets_and_prefix_formats(self):
        for label in ("Kyiv, Europe (GMT+03:00)", "(GMT+02:00) Kiev, Europe",
                      "(GMT+03:00) Europe/Kyiv", "Europe/Kiev", "Київ, Європа (UTC+03:00)",
                      "Time zone: Kyiv", "Kyiv, Ukraine"):
            with self.subTest(label=label):
                self.assertTrue(kyiv_label(label))
        for label in ("GMT+03:00", "Moscow (GMT+03:00)", "Kyiv, America (GMT+03:00)",
                      "Kyiv (GMT-07:00)", "Kyiv District", "Kyiv\nLondon", "Choose Kyiv"):
            with self.subTest(label=label):
                self.assertFalse(kyiv_label(label))


class MetaTimezoneChromiumTests(unittest.IsolatedAsyncioTestCase):
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
        await self.page.route("**/*", lambda route: route.abort())

    async def asyncTearDown(self):
        if hasattr(self, "chromium"):
            await self.chromium.close()
            await self.playwright.stop()

    async def picker_fixture(self, *, popup_role="dialog", current="Los Angeles, America (GMT-07:00)",
                             legacy=False, stuck=False, duplicate=False):
        role = f'role="{popup_role}"' if popup_role else ""
        required = "kiev" if legacy else "kyiv"
        option = ('<button role="option" data-value="Europe/Kyiv" '
                  'onclick="window.picks++;' +
                  ("" if stuck else "document.getElementById('zone').textContent='(GMT+03:00) Kyiv, Europe';") +
                  "document.getElementById('popup').hidden=true\">(GMT+03:00) Kyiv, Europe</button>")
        if duplicate:
            option += '<button role="option">Kyiv, Europe (GMT+03:00)</button>'
        await self.page.set_content(f"""
            <input id="background" placeholder="Search accounts" value="unchanged">
            <div role="dialog" id="setup"><h2>Select location and currency</h2>
              <label>Country/region<select><option value="UA">Ukraine</option></select></label>
              <label>Currency<select><option value="USD">US Dollars</option></select></label>
              <button id="zone" onclick="window.opens++;document.getElementById('popup').hidden=false">{current}</button>
              <button id="next" onclick="window.nexts++">Next</button>
            </div>
            <div id="popup" {role} hidden>
              <input placeholder="Search for a city" onkeyup="document.getElementById('choices').hidden=this.value.toLowerCase()!=='{required}'">
              <div id="choices" hidden>{option}</div>
            </div>
            <script>window.opens=0;window.picks=0;window.nexts=0;</script>
        """)
        return self.page.locator("#setup")

    async def test_search_in_separate_dialog_selects_prefix_label_and_keeps_background_search(self):
        scope = await self.picker_fixture()
        result = await configure_payment_account(
            SimpleNamespace(page=self.page, profile_id="fixture", _assert_authenticated=AsyncMock()), SETUP)
        self.assertEqual(result["status"], "SETUP_ADVANCED", result)
        self.assertEqual(await self.page.locator("#background").input_value(), "unchanged")
        self.assertEqual(await self.page.evaluate("[window.opens,window.picks,window.nexts]"), [1, 1, 1])
        self.assertTrue(await payment_timezone_selected(scope))
        self.assertEqual(await self.page.locator("[data-remask-timezone-before]").count(), 0)

    async def test_plain_virtualized_city_popup_without_listbox_role_is_supported(self):
        scope = await self.picker_fixture(popup_role="")
        selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertEqual(diagnostic["searches"], ["Kyiv"])
        self.assertEqual(await self.page.evaluate("[window.opens,window.picks]"), [1, 1])

    async def test_legacy_kiev_search_alias_uses_keyboard_events_without_reopening(self):
        scope = await self.picker_fixture(legacy=True)
        selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertEqual(diagnostic["searches"], ["Kyiv", "Kiev"])
        self.assertEqual(await self.page.evaluate("[window.opens,window.picks]"), [1, 1])

    async def test_current_city_and_dst_offset_are_read_from_control_instead_of_fixed_default(self):
        scope = await self.picker_fixture(current="London, Europe (GMT+00:00)")
        selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertTrue(await payment_timezone_selected(scope))

    async def test_multiline_city_and_region_are_one_timezone_identity(self):
        await self.page.set_content("""
            <div role="dialog" id="setup">
              <button id="zone" onclick="document.getElementById('popup').style.visibility='visible'">Los Angeles, America (GMT-07:00)</button>
            </div>
            <div id="popup" role="listbox" style="visibility:hidden"><button role="option"
              onclick="document.getElementById('zone').innerHTML=this.innerHTML;document.getElementById('popup').style.visibility='hidden'">
              Kyiv<br>Europe (GMT+03:00)</button></div>""")
        scope = self.page.locator("#setup")
        selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertTrue(await payment_timezone_selected(scope))

    async def test_native_iana_value_is_selected_when_option_label_has_offset_prefix(self):
        await self.page.set_content("""
            <label>Time zone<select id="zone">
              <option value="America/Los_Angeles">Los Angeles, America (GMT-08:00)</option>
              <option value="Europe/Kiev">(GMT+02:00) Kiev, Europe</option>
            </select></label>""")
        selected, diagnostic = await choose_payment_timezone(self.page, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertEqual(await self.page.locator("#zone").input_value(), "Europe/Kiev")

    async def test_already_selected_kyiv_is_observed_without_opening_picker(self):
        scope = await self.picker_fixture(current="(GMT+02:00) Europe/Kiev")
        selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertEqual(diagnostic["reason"], "already_selected")
        self.assertEqual(await self.page.evaluate("window.opens"), 0)

    async def test_two_city_choices_block_before_any_option_or_next_click(self):
        scope = await self.picker_fixture(duplicate=True)
        selected, diagnostic = await choose_payment_timezone(scope, self.page, wait_seconds=1.0)
        self.assertFalse(selected, diagnostic)
        self.assertEqual(diagnostic["reason"], "timezone_option_not_unique")
        self.assertEqual(await self.page.evaluate("[window.picks,window.nexts]"), [0, 0])

    async def test_transition_timeout_after_city_click_observes_selected_value_without_replay(self):
        scope = await self.picker_fixture()
        from playwright.async_api import Locator, TimeoutError as PlaywrightTimeoutError
        original_click = Locator.click
        async def click_then_timeout(locator, **kwargs):
            option = await locator.get_attribute("data-remask-timezone-option")
            await original_click(locator, **kwargs)
            if option is not None:
                raise PlaywrightTimeoutError("Fixture post-click transition timeout")
        with patch.object(Locator, "click", new=click_then_timeout):
            selected, diagnostic = await choose_payment_timezone(scope, self.page)
        self.assertTrue(selected, diagnostic)
        self.assertEqual(await self.page.evaluate("[window.opens,window.picks]"), [1, 1])

    async def test_option_click_without_changed_value_stops_before_next(self):
        await self.picker_fixture(stuck=True)
        result = await configure_payment_account(
            SimpleNamespace(page=self.page, profile_id="fixture", _assert_authenticated=AsyncMock()), SETUP)
        self.assertEqual(result["missing_fields"], ["timezone"])
        self.assertEqual(result["setup_diagnostic"]["reason"], "timezone_value_unchanged")
        self.assertEqual(await self.page.evaluate("[window.opens,window.picks,window.nexts]"), [1, 1, 0])

    async def test_background_account_search_is_never_used_when_city_picker_has_no_search(self):
        await self.page.set_content("""
            <input id="background" placeholder="Search accounts" value="unchanged">
            <button onclick="window.opens++">Los Angeles, America (GMT-07:00)</button>
            <script>window.opens=0;</script>""")
        selected, diagnostic = await choose_payment_timezone(self.page, self.page, wait_seconds=0.5)
        self.assertFalse(selected, diagnostic)
        self.assertEqual(diagnostic["searches"], [])
        self.assertEqual(await self.page.locator("#background").input_value(), "unchanged")
        self.assertEqual(await self.page.evaluate("window.opens"), 1)

    async def full_binding_fixture(self, target, *, observed=None):
        url = f"https://business.facebook.com/latest/settings/ad_accounts/?business_id={BUSINESS}"
        html = f"""
            <div role="row"><button>Fixture RK</button><a>Details</a></div>
            <button id="add" onclick="document.getElementById('setup').hidden=false;window.adds++">Add payment method</button>
            <input id="background" placeholder="Search accounts" value="unchanged">
            <div role="dialog" id="setup" hidden><h2>Select location and currency</h2>
              <label>Country/region<select id="country"><option value="BD">Bangladesh</option><option value="UA">Ukraine</option></select></label>
              <label>Currency<select id="currency"><option value="BDT">BDT</option><option value="USD">US Dollars</option></select></label>
              <button id="zone" onclick="window.opens++;document.getElementById('popup').hidden=false">Los Angeles, America (GMT-07:00)</button>
              <button onclick="window.settings=[document.getElementById('country').value,document.getElementById('currency').value,document.getElementById('zone').textContent];
                this.closest('[role=dialog]').hidden=true;document.getElementById('card').hidden=false;
                history.replaceState(null,'','/billing_hub/payment_settings?asset_id={target}&business_id={BUSINESS}')">Next</button>
            </div>
            <div id="popup" role="dialog" hidden>
              <input placeholder="Search city" onkeyup="document.getElementById('choice').hidden=this.value.toLowerCase()!=='kyiv'">
              <button id="choice" role="option" hidden onclick="document.getElementById('zone').textContent='(GMT+03:00) Kyiv, Europe';document.getElementById('popup').hidden=true">(GMT+03:00) Kyiv, Europe</button>
            </div>
            <form id="card" hidden onsubmit="return false">
              <label>Name on card<input autocomplete="cc-name"></label>
              <label>Card number<input id="pan" autocomplete="cc-number"></label>
              <label>Expiry<input autocomplete="cc-exp"></label>
              <label>CVV<input autocomplete="cc-csc"></label>
              <label><input type="radio" name="availability" id="only">Only this account</label>
              <label><input type="radio" name="availability" id="all" checked>All accounts in this business portfolio</label>
              <button type="button" onclick="window.saves++;window.boundAccount='{target}';window.onlySelected=document.getElementById('only').checked;
                const last4=document.getElementById('pan').value.slice(-4);
                document.getElementById('card').remove();document.getElementById('methods').textContent='Payment methods Visa •••• '+last4">Save</button>
            </form>
            <div id="methods"></div><script>window.adds=0;window.opens=0;window.saves=0;window.settings=[];</script>
        """
        await self.page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        async def goto(address, **kwargs):
            await self.page.goto(address, wait_until="domcontentloaded")
        return SimpleNamespace(page=self.page, profile_id="fixture", _goto=goto,
            _assert_authenticated=AsyncMock(), SETTINGS_AD_ACCOUNTS_URLS=[url],
            _read_selected_ad_account_identity=AsyncMock(
                return_value={"confirmed": True, "ad_account_id": observed or target}))

    async def test_complete_bind_setup_fields_single_save_and_exact_masked_rk_proof_for_selected_targets(self):
        targets = ["123456789", "123456790"]
        saved = []
        for target in targets:
            browser = await self.full_binding_fixture(target)
            with patch("app.payment_card_binding.select_settings_payment_tab", AsyncMock(return_value=True)):
                result = await payment_card_flow(browser, "act_" + target,
                    {"business_id": BUSINESS, "name": "Fixture RK"},
                    operation="bind", card=CARD, cvv="123", billing_setup=SETUP)
            self.assertEqual(result["status"], "LINKED", result)
            self.assertTrue(result["submitted"])
            self.assertEqual(result["account_id"], target)
            self.assertEqual(await self.page.evaluate("[window.adds,window.opens,window.saves]"), [1, 1, 1])
            self.assertEqual(await self.page.evaluate("window.settings"), ["UA", "USD", "(GMT+03:00) Kyiv, Europe"])
            self.assertTrue(await self.page.evaluate("window.onlySelected"))
            self.assertEqual(await self.page.locator("#background").input_value(), "unchanged")
            self.assertNotIn(CARD["number"], json.dumps(result))
            self.assertNotIn('"cvv"', json.dumps(result))
            saved.append(await self.page.evaluate("window.boundAccount"))
            await self.page.unroute(f"https://business.facebook.com/latest/settings/ad_accounts/?business_id={BUSINESS}")
        self.assertEqual(saved, targets)
        self.assertNotIn("123456791", saved)

    async def test_wrong_canonical_rk_stops_before_setup_card_fields_and_save(self):
        browser = await self.full_binding_fixture("123456789", observed="123456790")
        with patch("app.payment_card_binding.select_settings_payment_tab", AsyncMock(return_value=True)):
            result = await payment_card_flow(browser, "123456789",
                {"business_id": BUSINESS, "name": "Fixture RK"},
                operation="bind", card=CARD, cvv="123", billing_setup=SETUP)
        self.assertEqual(result["code"], "PAYMENT_ACCOUNT_SCOPE_UNVERIFIED")
        self.assertFalse(result["submitted"])
        self.assertEqual(await self.page.evaluate("[window.adds,window.opens,window.saves]"), [0, 0, 0])
        self.assertEqual(await self.page.locator("#pan").input_value(), "")
