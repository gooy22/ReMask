import asyncio
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import (
    BrowserBusinessError,
    FacebookBusinessBrowser,
    _ad_account_required_attribution_post_data,
    _extract_created_ad_account_id,
)
from app.provisioning.business_handler import business_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.provisioning.service import ProvisioningService


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

    def test_ad_account_gate_matches_live_create_mutation(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=AdAccountCreateMutation"
            "&doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22name%22%3A"
            "%22ReMask%20Ads%22%2C%22currency%22%3A%22USD%22%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_accepts_renamed_mutation_with_strong_create_shape(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BizKitSettingsAssetMutation"
            "&doc_id=8877665544332211"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22name%22%3A"
            "%22ReMask%20Ads%22%2C%22currency%22%3A%22USD%22%2C"
            "%22timezone_id%22%3A1%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_accepts_known_create_without_name_in_envelope(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=AdAccountCreateMutation"
            "&doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22currency%22%3A%22USD%22%2C"
            "%22timezone_id%22%3A1%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_accepts_strong_renamed_mutation_without_name(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BizKitSettingsAssetMutation"
            "&doc_id=8877665544332211"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22currency%22%3A%22USD%22%2C"
            "%22timezone_id%22%3A1%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_rejects_update_shape_without_name(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BizKitSettingsAssetMutation"
            "&doc_id=8877665544332211"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22ad_account_id%22%3A"
            "%221234567890%22%2C%22currency%22%3A%22USD%22%2C"
            "%22timezone_id%22%3A1%7D%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_rejects_same_name_without_create_shape(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BusinessSettingsValidationMutation"
            "&doc_id=8877665544332211"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22name%22%3A"
            "%22ReMask%20Ads%22%7D%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_gate_ignores_unrelated_graphql(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=BusinessAdAccountSearchQuery"
            "&doc_id=9988776655443322"
            "&variables=%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22query%22%3A"
            "%22ReMask%20Ads%22%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="555666777888999",
                account_name="ReMask Ads",
            )
        )

    def test_ad_account_required_attribution_defaults_are_added(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=AdAccountCreateMutation"
            "&doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22name%22%3A"
            "%22ReMask%20Ads%22%2C%22currency%22%3A%22USD%22%2C"
            "%22timezone_id%22%3A1%7D%7D"
        )
        post_data, applied = _ad_account_required_attribution_post_data(
            request
        )
        self.assertEqual(
            applied,
            {
                "end_advertiser": "NONE",
                "media_agency": "NONE",
                "partner": "NONE",
            },
        )
        self.assertIn("end_advertiser", post_data)
        self.assertIn("media_agency", post_data)
        self.assertIn("partner", post_data)

    def test_ad_account_live_request_applies_requested_currency_timezone(self):
        request = _FakeRequest(
            "fb_api_req_friendly_name=AdAccountCreateMutation"
            "&doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B%22business_id%22%3A"
            "%22555666777888999%22%2C%22name%22%3A"
            "%22ReMask%20Ads%22%2C%22currency%22%3A%22EUR%22%2C"
            "%22timezone_id%22%3A10%7D%7D"
        )
        post_data, applied = _ad_account_required_attribution_post_data(
            request,
            currency="USD",
            timezone_id=1,
        )
        self.assertEqual(applied["currency"], "USD")
        self.assertEqual(applied["timezone_id"], 1)
        self.assertIn("%22currency%22%3A%22USD%22", post_data)
        self.assertIn("%22timezone_id%22%3A1", post_data)

    def test_ad_account_live_request_does_not_invent_currency_timezone_keys(self):
        request = _FakeRequest(
            "doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B"
            "%22business_id%22%3A%22555666777888999%22%7D%7D"
        )
        post_data, applied = _ad_account_required_attribution_post_data(
            request,
            currency="USD",
            timezone_id=1,
        )
        self.assertNotIn("currency", applied)
        self.assertNotIn("timezone_id", applied)
        self.assertNotIn("%22currency%22", post_data)
        self.assertNotIn("%22timezone_id%22", post_data)

    def test_ad_account_required_attribution_preserves_meta_values(self):
        request = _FakeRequest(
            "doc_id=9988776655443322"
            "&variables=%7B%22input%22%3A%7B"
            "%22end_advertiser%22%3A%22123456789%22%2C"
            "%22media_agency%22%3A%22NONE%22%2C"
            "%22partner%22%3A%22NONE%22%7D%7D"
        )
        post_data, applied = _ad_account_required_attribution_post_data(
            request
        )
        self.assertEqual(applied, {})
        self.assertEqual(post_data, request.post_data)

    def test_ad_account_response_id_accepts_unique_renamed_account_node(self):
        account_id, path = _extract_created_ad_account_id(
            {
                "data": {
                    "settingsCreateAdvertisingAccount": {
                        "adAccount": {"id": "act_123456789012345"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_123456789012345")
        self.assertIn("adAccount.id", path)

    def test_ad_account_response_id_rejects_ambiguous_generic_ids(self):
        account_id, path = _extract_created_ad_account_id(
            {
                "data": {
                    "settingsCreateAdvertisingAccount": {
                        "adAccount": {"id": "act_111111111111111"},
                        "secondary": {
                            "ad_account_id": "act_222222222222222"
                        },
                    }
                }
            }
        )
        self.assertEqual(account_id, "")
        self.assertEqual(path, "")

    def test_ad_account_response_id_known_shape(self):
        account_id, path = _extract_created_ad_account_id(
            {
                "data": {
                    "business_ad_account_create": {
                        "ad_account": {"id": "123456789012345"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_123456789012345")
        self.assertEqual(
            path,
            "data.business_ad_account_create.ad_account.id",
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


class BrowserAdAccountAdditionalLocaleTests(unittest.TestCase):
    def test_bangla_vietnamese_hindi_ad_account_labels_are_supported(self):
        section_names = FacebookBusinessBrowser.AD_ACCOUNT_SECTION_NAMES
        create_names = FacebookBusinessBrowser.AD_ACCOUNT_CREATE_ENTRY_NAMES
        add_names = FacebookBusinessBrowser.ADD_NAMES

        self.assertIn("বিজ্ঞাপন অ্যাকাউন্ট", section_names)
        self.assertIn("Tài khoản quảng cáo", section_names)
        self.assertIn("विज्ञापन खाते", section_names)

        self.assertIn(
            "বিজ্ঞাপন অ্যাকাউন্ট তৈরি করুন",
            create_names,
        )
        self.assertIn(
            "Tạo tài khoản quảng cáo",
            create_names,
        )
        self.assertIn(
            "विज्ञापन खाता बनाएँ",
            create_names,
        )

        self.assertIn("যোগ করুন", add_names)
        self.assertIn("Thêm", add_names)
        self.assertIn("जोड़ें", add_names)


class BrowserAdAccountFillBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_first_can_use_short_add_rk_timeout(self):
        class _Field:
            async def is_visible(self):
                return True

            async def fill(self, value, **kwargs):
                self.value = value
                self.kwargs = kwargs

        class _Locator:
            def __init__(self):
                self.first = _Field()

            async def count(self):
                return 1

        class _EmptyLocator:
            async def count(self):
                return 0

        class _Page:
            def __init__(self):
                self.field = _Locator()

            def get_by_label(self, pattern):
                return self.field

            def get_by_placeholder(self, pattern):
                return _EmptyLocator()

            def get_by_text(self, pattern):
                return _EmptyLocator()

            def locator(self, selector):
                return _EmptyLocator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-fill-budget")
        )
        browser.page = _Page()

        filled = await browser._fill_first(
            labels=("Nom du compte publicitaire",),
            value="ReMask RK",
            fill_timeout_ms=2500,
        )

        self.assertTrue(filled)
        self.assertEqual(
            browser.page.field.first.kwargs.get("timeout"),
            2500,
        )


class BrowserAdAccountSingleAttemptClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_attempt_stops_after_click_exception(self):
        class _Item:
            async def is_visible(self):
                return True
            async def is_enabled(self):
                return True
            async def click(self, **kwargs):
                raise RuntimeError("context destroyed after click")

        class _Locator:
            def nth(self, index):
                return _Item()
            async def count(self):
                return 2

        class _Page:
            def get_by_role(self, role, name=None):
                return _Locator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-single-attempt")
        )
        browser.page = _Page()
        intent = AsyncMock(return_value=None)

        result = await browser._click_named_single_attempt(
            ("Créer",),
            roles=("button",),
            before_click=intent,
            click_timeout_ms=2500,
        )

        self.assertTrue(result["found"])
        self.assertTrue(result["attempted"])
        self.assertFalse(result["clicked"])
        self.assertIn("context destroyed", result["error"])
        intent.assert_awaited_once()

    async def test_single_attempt_reports_no_candidate_without_arming_intent(self):
        class _Locator:
            async def count(self):
                return 0

        class _Page:
            def get_by_role(self, role, name=None):
                return _Locator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-no-final")
        )
        browser.page = _Page()
        intent = AsyncMock(return_value=None)

        result = await browser._click_named_single_attempt(
            ("Créer",),
            roles=("button",),
            before_click=intent,
        )

        self.assertFalse(result["found"])
        self.assertFalse(result["attempted"])
        self.assertFalse(result["clicked"])
        intent.assert_not_awaited()


class BrowserAdAccountClickBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_click_can_use_short_add_rk_timeout(self):
        class _Item:
            async def is_visible(self):
                return True

            async def is_enabled(self):
                return True

            async def click(self, **kwargs):
                self.kwargs = kwargs

        class _Locator:
            def __init__(self):
                self.item = _Item()

            async def count(self):
                return 1

            def nth(self, index):
                return self.item

        class _Page:
            def __init__(self):
                self.locator = _Locator()

            def get_by_role(self, role, name=None):
                return self.locator

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-click-budget")
        )
        browser.page = _Page()

        clicked = await browser._click_named(
            ("Ajouter",),
            roles=("button",),
            click_timeout_ms=2500,
        )

        self.assertTrue(clicked)
        self.assertEqual(
            browser.page.locator.item.kwargs.get("timeout"),
            2500,
        )

    async def test_runtime_timeout_diagnostic_reports_phase_and_submit_state(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-timeout-diag")
        )
        browser.page = SimpleNamespace()
        browser._diagnostic = AsyncMock(
            return_value={"stage": "ad_account_internal_timeout"}
        )
        browser._ad_account_action_candidates = AsyncMock(
            return_value=["Ajouter"]
        )
        browser._mark_ad_account_phase("ADD_PROBE")
        browser._ad_account_create_sent = False

        diag = await browser.ad_account_runtime_timeout_diagnostic()

        self.assertEqual(diag["runtime_phase"], "ADD_PROBE")
        self.assertFalse(diag["create_may_have_been_sent"])
        self.assertEqual(diag["action_candidates"], ["Ajouter"])


class BrowserAdAccountSectionNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_french_ad_accounts_sidebar_is_opened_before_create(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-nav")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
            evaluate=AsyncMock(return_value=False),
        )
        browser._click_named = AsyncMock(return_value=True)

        clicked = await browser._activate_ad_account_settings_section()

        self.assertTrue(clicked)
        browser._click_named.assert_awaited_once_with(
            browser.AD_ACCOUNT_SECTION_NAMES,
            roles=("link", "menuitem", "button"),
        )
        self.assertIn(
            "Comptes publicitaires",
            browser.AD_ACCOUNT_SECTION_NAMES,
        )


class BrowserAdAccountDomFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_section_dom_fallback_is_used_when_exact_role_name_misses(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-dom")
        )
        browser.page = SimpleNamespace(
            url="https://business.facebook.com/latest/settings/ad_accounts",
            wait_for_timeout=AsyncMock(return_value=None),
            evaluate=AsyncMock(
                return_value={
                    "mode": "click",
                    "href": "",
                    "text": "comptes publicitaires",
                    "x": 120,
                    "y": 280,
                    "tag": "DIV",
                    "role": "button",
                    "candidates": [],
                }
            ),
        )
        browser._click_named = AsyncMock(return_value=False)

        clicked = await browser._activate_ad_account_settings_section()

        self.assertTrue(clicked)
        browser.page.evaluate.assert_awaited_once()
        browser.page.wait_for_timeout.assert_awaited_once_with(900)

    async def test_section_prefers_real_meta_href_over_text_click(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-href")
        )
        href = (
            "https://business.facebook.com/latest/settings/ad_accounts/"
            "?nav_ref=bm_settings_redirect_migration"
            "&business_id=1056638030476027"
        )
        browser.page = SimpleNamespace(
            url="https://business.facebook.com/latest/settings/",
            wait_for_timeout=AsyncMock(return_value=None),
            evaluate=AsyncMock(
                return_value={
                    "mode": "href",
                    "href": href,
                    "text": "comptes publicitaires",
                    "x": 112,
                    "y": 302,
                    "tag": "A",
                    "role": "link",
                    "candidates": [],
                }
            ),
        )
        browser._goto = AsyncMock(return_value=href)
        browser._assert_authenticated = AsyncMock(return_value=None)
        browser._click_named = AsyncMock(return_value=False)

        clicked = await browser._activate_ad_account_settings_section(
            business_id="1056638030476027",
        )

        self.assertTrue(clicked)
        browser._goto.assert_awaited_once_with(href)
        browser._click_named.assert_not_awaited()
        self.assertEqual(
            browser._last_ad_account_section_diagnostic["mode"],
            "href",
        )

    async def test_dom_action_accepts_anchor_based_french_add(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-anchor-add")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(return_value="add"),
        )

        action = await browser._click_ad_account_action_dom(
            allow_generic_add=True
        )

        self.assertEqual(action, "add")
        script = browser.page.evaluate.await_args.args[0]
        self.assertIn(
            'button,a,[role="button"],[role="link"]',
            script,
        )
        self.assertIn("r.x < 300", script)

    async def test_dom_action_can_open_generic_add_button(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-add")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(return_value="add"),
        )

        action = await browser._click_ad_account_action_dom(
            allow_generic_add=True
        )

        self.assertEqual(action, "add")
        args = browser.page.evaluate.await_args.args
        self.assertTrue(args[1])

    async def test_dom_action_can_click_create_entry_without_generic_add(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-create")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(return_value="create"),
        )

        action = await browser._click_ad_account_action_dom(
            allow_generic_add=False
        )

        self.assertEqual(action, "create")
        args = browser.page.evaluate.await_args.args
        self.assertFalse(args[1])


class BrowserAdAccountFormActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_div_continue_fallback_is_supported(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-submit-div")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "clicked": True,
                    "action": "next",
                    "text": "continuer",
                    "x": 1040,
                    "y": 690,
                    "tag": "DIV",
                    "role": "",
                }
            )
        )

        result = await browser._click_ad_account_form_action_by_visible_text(
            "next"
        )

        self.assertTrue(result["clicked"])
        self.assertEqual(result["action"], "next")
        script = browser.page.evaluate.await_args.args[0]
        self.assertIn("continuer", script)
        self.assertIn("aria-disabled", script)

    async def test_plain_div_final_create_fallback_is_rejected(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-submit-final-div")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "clicked": False,
                    "action": "final",
                }
            )
        )

        result = await browser._click_ad_account_form_action_by_visible_text(
            "final"
        )

        self.assertFalse(result["clicked"])
        self.assertEqual(result["action"], "final")
        script = browser.page.evaluate.await_args.args[0]
        self.assertIn("mode === 'final' && !interactive", script)


class BrowserAdAccountOwnBusinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_french_own_business_choice_is_supported(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-own-business")
        )
        browser._click_named = AsyncMock(return_value=True)

        selected = await browser._select_own_business_if_present()

        self.assertTrue(selected)
        names = browser._click_named.await_args.args[0]
        self.assertIn("My business", names)
        self.assertIn("Mon entreprise", names)
        self.assertIn("Pour mon entreprise", names)
        self.assertIn("Мой бизнес", names)

    async def test_optional_own_business_uses_visible_text_fallback(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-own-business-div")
        )
        browser._click_named = AsyncMock(return_value=False)
        browser._click_ad_account_form_action_by_visible_text = AsyncMock(
            return_value={"clicked": True, "action": "own_business"}
        )

        selected = await browser._select_own_business_if_present()

        self.assertTrue(selected)
        browser._click_ad_account_form_action_by_visible_text.assert_awaited_once_with(
            "own_business"
        )



class BrowserAdAccountAddProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_probe_rescans_after_first_popup_misses(self):
        class _Item:
            def __init__(self, name, clicks):
                self.name = name
                self.clicks = clicks

            async def is_visible(self):
                return True

            async def is_enabled(self):
                return True

            async def scroll_into_view_if_needed(self, **kwargs):
                self.scroll_kwargs = kwargs
                return None

            async def click(self, **kwargs):
                self.click_kwargs = kwargs
                self.clicks.append(self.name)

        class _Locator:
            def __init__(self, item):
                self.first = item

            async def count(self):
                return 1

        class _Keyboard:
            def __init__(self):
                self.keys = []

            async def press(self, key):
                self.keys.append(key)

        class _Page:
            def __init__(self):
                self.clicks = []
                self.keyboard = _Keyboard()

            def locator(self, selector):
                probe_id = selector.split('"')[1]
                return _Locator(_Item(probe_id, self.clicks))

            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-multi-add-rescan")
        )
        browser.page = _Page()
        browser._ad_account_add_button_candidates = AsyncMock(
            side_effect=[
                [
                    {"probe_id":"0","text":"Ajouter","x":745,"y":631},
                    {"probe_id":"1","text":"Ajouter","x":1137,"y":97},
                ],
                [
                    # React rerender: probe ids are reassigned on fresh scan.
                    {"probe_id":"0","text":"Ajouter","x":745,"y":631},
                    {"probe_id":"1","text":"Ajouter","x":1137,"y":97},
                ],
            ]
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state":"ADD_SURFACE",
                "signature":"surface",
                "url":"",
                "errors":[],
                "dialogs":[],
                "controls":[],
            }
        )
        browser._ad_account_right_pane_snapshot = AsyncMock(
            side_effect=[
                ["Ajouter"],
                ["Unrelated popup"],
                ["Ajouter"],
                ["Créer un compte publicitaire"],
            ]
        )
        browser._wait_for_fresh_ad_account_create_candidate = AsyncMock(
            side_effect=[
                (
                    [],
                    {
                        "state":"DIALOG",
                        "signature":"popup-1",
                        "url":"",
                        "errors":[],
                        "dialogs":["Unrelated popup"],
                        "controls":[],
                    },
                ),
                (
                    [],
                    {
                        "state":"CREATE_ENTRY",
                        "signature":"popup-2",
                        "url":"",
                        "errors":[],
                        "dialogs":["Créer un compte publicitaire"],
                        "controls":["Créer un compte publicitaire"],
                    },
                ),
            ]
        )
        browser._wait_for_ad_account_ui_transition = AsyncMock(
            return_value={
                "state":"FORM",
                "signature":"wizard",
                "url":"",
                "name_input":True,
                "form_evidence":True,
                "editable_form_control":True,
                "errors":[],
                "dialogs":[],
                "controls":[
                    "Nom du compte publicitaire",
                    "Devise",
                    "Fuseau horaire",
                ],
            }
        )
        browser._ad_account_popup_candidates = AsyncMock(
            side_effect=[
                ["Unrelated popup"],
                ["Créer un compte publicitaire"],
            ]
        )
        browser._click_ad_account_create_entry_in_popup = AsyncMock(
            side_effect=[
                {"clicked":False,"popup_count":1},
                {
                    "clicked":True,
                    "popup_count":1,
                    "text":"Créer un compte publicitaire",
                },
            ]
        )
        browser._wait_for_ad_account_create_entry = AsyncMock(
            side_effect=AssertionError(
                "global create-entry search must not run after Add"
            )
        )

        found, attempts = await browser._probe_ad_account_add_buttons()

        self.assertTrue(found)
        self.assertEqual(browser.page.clicks, ["0", "1"])
        self.assertEqual(
            browser._ad_account_add_button_candidates.await_count,
            2,
        )
        self.assertEqual(browser.page.keyboard.keys, ["Escape"])
        self.assertFalse(attempts[0]["create_entry_found"])
        self.assertTrue(attempts[1]["create_entry_found"])
        browser._wait_for_ad_account_create_entry.assert_not_awaited()

    def test_add_attempt_summary_is_compact_and_ordered(self):
        rows = FacebookBusinessBrowser._summarize_ad_account_add_attempts(
            [
                {
                    "x": 745,
                    "y": 631,
                    "clicked": True,
                    "create_entry_found": False,
                    "post_click_candidates": ["Wrong popup [tag=DIV]"],
                },
                {
                    "x": 1137,
                    "y": 97,
                    "clicked": True,
                    "create_entry_found": True,
                },
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("x=745", rows[0])
        self.assertIn("popup=Wrong popup", rows[0])
        self.assertIn("x=1137", rows[1])
        self.assertIn("create=True", rows[1])

    async def test_add_candidate_scanner_prefers_content_button_over_toolbar(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-add-scan")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value=[
                    {
                        "probe_id": "0",
                        "text": "Ajouter",
                        "x": 745,
                        "y": 631,
                        "w": 100,
                        "h": 32,
                        "tag": "DIV",
                        "role": "button",
                        "haspopup": "",
                        "toolbar_penalty": 0,
                    },
                    {
                        "probe_id": "1",
                        "text": "Ajouter",
                        "x": 1137,
                        "y": 97,
                        "w": 100,
                        "h": 32,
                        "tag": "DIV",
                        "role": "button",
                        "haspopup": "",
                        "toolbar_penalty": 1,
                    },
                ]
            )
        )

        rows = await browser._ad_account_add_button_candidates()

        self.assertEqual(rows[0]["x"], 745)
        self.assertEqual(rows[0]["y"], 631)
        self.assertEqual(rows[1]["x"], 1137)
        self.assertEqual(rows[1]["y"], 97)


class BrowserAdAccountLiveAddSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_to_fresh_create_to_form_sequence(self):
        class _Item:
            async def is_visible(self):
                return True
            async def is_enabled(self):
                return True
            async def scroll_into_view_if_needed(self, **kwargs):
                return None
            async def click(self, **kwargs):
                return None

        class _Locator:
            first = _Item()
            async def count(self):
                return 1

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            keyboard = _Keyboard()
            def locator(self, selector):
                return _Locator()
            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-live-add-sequence")
        )
        browser.page = _Page()
        browser._ad_account_add_button_candidates = AsyncMock(
            return_value=[
                {
                    "probe_id":"0",
                    "text":"Ajouter",
                    "x":745,
                    "y":631,
                    "w":103,
                    "h":36,
                    "tag":"DIV",
                    "role":"button",
                }
            ]
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state":"ADD_SURFACE",
                "signature":"add-surface",
                "url":"",
                "errors":[],
                "dialogs":[],
                "controls":["Ajouter"],
                "name_input":False,
                "editable_form_control":False,
            }
        )
        browser._ad_account_right_pane_snapshot = AsyncMock(
            side_effect=[
                ["Ajouter"],
                ["Ajouter", "Créer un compte publicitaire"],
            ]
        )
        browser._ad_account_visible_create_candidates = AsyncMock(
            return_value=[]
        )
        browser._wait_for_fresh_ad_account_create_candidate = AsyncMock(
            return_value=(
                [
                    {
                        "probe_id":"0",
                        "text":"créer un compte publicitaire",
                        "x":760,
                        "y":520,
                        "tag":"DIV",
                        "role":"",
                    }
                ],
                {
                    "state":"CREATE_ENTRY",
                    "signature":"create-entry",
                    "url":"",
                    "errors":[],
                    "dialogs":[],
                    "controls":["Créer un compte publicitaire"],
                    "name_input":False,
                    "editable_form_control":False,
                },
            )
        )
        browser._click_fresh_ad_account_create_candidate = AsyncMock(
            return_value={
                "clicked":True,
                "text":"créer un compte publicitaire",
                "x":760,
                "y":520,
                "tag":"DIV",
                "role":"",
            }
        )
        browser._wait_for_ad_account_ui_transition = AsyncMock(
            return_value={
                "state":"FORM",
                "signature":"wizard-form",
                "url":"",
                "name_input":True,
                "form_evidence":True,
                "editable_form_control":True,
                "errors":[],
                "dialogs":[],
                "controls":[
                    "Nom du compte publicitaire",
                    "Devise",
                    "Fuseau horaire",
                ],
            }
        )
        browser._ad_account_popup_candidates = AsyncMock(return_value=[])
        browser._click_ad_account_create_entry_in_popup = AsyncMock(
            return_value={"clicked":False}
        )

        found, attempts = await browser._probe_ad_account_add_buttons()

        self.assertTrue(found)
        self.assertTrue(attempts[0]["clicked"])
        self.assertTrue(attempts[0]["fresh_create"]["clicked"])
        self.assertTrue(attempts[0]["create_entry_found"])
        browser._wait_for_fresh_ad_account_create_candidate.assert_awaited_once()
        browser._click_fresh_ad_account_create_candidate.assert_awaited_once()
        browser._wait_for_ad_account_ui_transition.assert_awaited_once()


class BrowserAdAccountPopupTransitionRegressionTests(unittest.TestCase):
    def test_popup_transition_uses_post_add_poll_state_signature(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._probe_ad_account_add_buttons
        )
        popup_pos = source.index('label="after_popup_create_entry"')
        before = source[max(0, popup_pos - 260):popup_pos]
        self.assertIn(
            'post_add_poll_state.get("signature")',
            before,
        )
        self.assertNotIn(
            'transition.get("signature")',
            before,
        )


class BrowserAdAccountImmediatePostAddOrderTests(unittest.TestCase):
    def test_add_flow_polls_fresh_create_before_coarse_transition_wait(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._probe_ad_account_add_buttons
        )
        click_pos = source.index("await item.click(timeout=2500)")
        poll_pos = source.index(
            "_wait_for_fresh_ad_account_create_candidate",
            click_pos,
        )
        transition_pos = source.find(
            "_wait_for_ad_account_ui_transition",
            click_pos,
        )
        self.assertGreater(poll_pos, click_pos)
        if transition_pos != -1:
            self.assertLess(poll_pos, transition_pos)


class BrowserAdAccountFreshCreatePollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_create_entry_that_appears_after_add(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-delayed-create")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._ad_account_ui_state = AsyncMock(
            side_effect=[
                {
                    "state":"UNKNOWN",
                    "signature":"s1",
                    "name_input":False,
                    "editable_form_control":False,
                    "controls":[],
                    "dialogs":[],
                },
                {
                    "state":"UNKNOWN",
                    "signature":"s2",
                    "name_input":False,
                    "editable_form_control":False,
                    "controls":[],
                    "dialogs":[],
                },
                {
                    "state":"UNKNOWN",
                    "signature":"s3",
                    "name_input":False,
                    "editable_form_control":False,
                    "controls":[],
                    "dialogs":[],
                },
            ]
        )
        browser._ad_account_visible_create_candidates = AsyncMock(
            side_effect=[
                [],
                [],
                [
                    {
                        "probe_id":"0",
                        "text":"créer un compte publicitaire",
                        "x":760,
                        "y":520,
                    }
                ],
            ]
        )

        fresh, state = (
            await browser._wait_for_fresh_ad_account_create_candidate(
                before=[],
                timeout_seconds=3.5,
            )
        )

        self.assertEqual(len(fresh), 1)
        self.assertEqual(
            fresh[0]["text"],
            "créer un compte publicitaire",
        )
        self.assertEqual(
            browser._ad_account_visible_create_candidates.await_count,
            3,
        )


class BrowserAdAccountFreshCreateEntryTests(unittest.TestCase):
    def test_fresh_create_candidate_appearing_after_add_is_detected(self):
        before = []
        after = [
            {
                "probe_id":"0",
                "text":"créer un compte publicitaire",
                "x":760,
                "y":520,
                "w":240,
                "h":36,
                "tag":"DIV",
                "role":"",
            }
        ]

        fresh = FacebookBusinessBrowser._fresh_ad_account_create_candidates(
            before,
            after,
        )

        self.assertEqual(len(fresh), 1)
        self.assertEqual(
            fresh[0]["text"],
            "créer un compte publicitaire",
        )

    def test_existing_create_candidate_is_not_treated_as_fresh(self):
        before = [
            {
                "text":"créer un compte publicitaire",
                "x":760,
                "y":520,
            }
        ]
        after = [
            {
                "probe_id":"0",
                "text":"créer un compte publicitaire",
                "x":764,
                "y":524,
            }
        ]

        fresh = FacebookBusinessBrowser._fresh_ad_account_create_candidates(
            before,
            after,
        )

        self.assertEqual(fresh, [])


class BrowserAdAccountSubmitTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_transition_waits_for_signature_change(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-submit-transition")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._ad_account_ui_state = AsyncMock(
            side_effect=[
                {
                    "state": "FORM",
                    "signature": "same-form",
                    "url": "",
                    "errors": [],
                    "dialogs": [],
                    "controls": [],
                },
                {
                    "state": "FORM",
                    "signature": "next-form",
                    "url": "",
                    "errors": [],
                    "dialogs": [],
                    "controls": [],
                },
            ]
        )

        state = await browser._wait_for_ad_account_ui_transition(
            previous_signature="same-form",
            timeout_seconds=2.0,
            label="after_next",
            require_signature_change=True,
        )

        self.assertEqual(state["signature"], "next-form")
        self.assertEqual(browser._ad_account_ui_state.await_count, 2)


class BrowserAdAccountComboboxSafetyTests(unittest.TestCase):
    def test_custom_field_fallback_requires_dropdown_semantics(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._select_ad_account_form_field
        )
        self.assertIn("@aria-haspopup or @aria-expanded", source)
        self.assertNotIn(
            "ancestor::*[.//button or .//*[@role=\"button\"]]",
            source,
        )


class BrowserAdAccountPlainDropdownSafetyTests(unittest.TestCase):
    def test_nearby_field_probe_allows_plain_role_button_only_with_geometry_guard(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_field_control_by_nearby_label
        )
        self.assertIn("'[role=\"button\"]'", source)
        self.assertIn("const sameRow =", source)
        self.assertIn("const toRight =", source)
        self.assertIn("const substantial =", source)
        self.assertIn("if (!sameRow || !toRight || !substantial)", source)


class BrowserAdAccountAncestorClimbRegressionTests(unittest.TestCase):
    def test_nearby_field_probe_does_not_stop_on_unrelated_button_ancestor(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_field_control_by_nearby_label
        )
        self.assertIn("acceptedAtThisDepth", source)
        self.assertIn("if (acceptedAtThisDepth > 0)", source)
        self.assertLess(
            source.index("acceptedAtThisDepth += 1"),
            source.index("if (acceptedAtThisDepth > 0)"),
        )


class BrowserAdAccountNearbyFieldControlTests(unittest.TestCase):
    def test_nearby_label_field_probe_covers_french_currency_and_timezone(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_field_control_by_nearby_label
        )
        self.assertIn("data-remask-rk-field-probe", source)
        self.assertIn('[role="combobox"]', source)
        self.assertIn("button[aria-haspopup]", source)

    def test_field_selector_uses_nearby_label_fallback_before_not_found(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._select_ad_account_form_field
        )
        self.assertIn("_ad_account_field_control_by_nearby_label", source)
        self.assertLess(
            source.index("_ad_account_field_control_by_nearby_label"),
            source.index('"field_not_found"'),
        )


class BrowserAdAccountFormFieldTests(unittest.IsolatedAsyncioTestCase):
    def test_timezone_fallbacks_cover_main_profile_geos(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-timezone-map")
        )
        self.assertEqual(browser._timezone_name_for_id(57), "Europe/Paris")
        self.assertEqual(browser._timezone_name_for_id(17), "Asia/Dhaka")
        self.assertEqual(browser._timezone_name_for_id(71), "Asia/Kolkata")
        self.assertEqual(browser._timezone_name_for_id(140), "Asia/Ho_Chi_Minh")
        self.assertEqual(browser._timezone_name_for_id(137), "Europe/Kyiv")

    def test_choice_matching_uses_exact_currency_and_timezone_ids(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-choice-match")
        )
        self.assertTrue(
            browser._ad_account_choice_matches(
                "USD US Dollar",
                tokens=("USD",),
            )
        )
        self.assertFalse(
            browser._ad_account_choice_matches(
                "AUD Australian Dollar",
                tokens=("USD",),
            )
        )
        self.assertTrue(
            browser._ad_account_choice_matches(
                "data-id=17 Asia/Dhaka",
                tokens=("Asia/Dhaka",),
                numeric_id="17",
            )
        )

    async def test_prepare_fields_targets_currency_and_timezone(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-form-fields")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._select_ad_account_form_field = AsyncMock(
            side_effect=[
                {"field":"currency","status":"selected","selected":"USD"},
                {
                    "field":"timezone",
                    "status":"selected",
                    "selected":"America/Los_Angeles",
                },
            ]
        )

        result = await browser._prepare_ad_account_form_fields(
            currency="USD",
            timezone_id=1,
        )

        self.assertEqual(result["currency"]["status"], "selected")
        self.assertEqual(result["timezone"]["status"], "selected")
        second = browser._select_ad_account_form_field.await_args_list[1]
        self.assertEqual(second.kwargs["numeric_id"], "1")
        self.assertIn(
            "America/Los_Angeles",
            second.kwargs["tokens"],
        )


class BrowserAdAccountTimezoneSafetyTests(unittest.TestCase):
    def test_numeric_timezone_id_matches_identity_not_human_offset(self):
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_option_matches(
                {
                    "identity": "",
                    "label": "UTC+1 Central European Time",
                    "text": "UTC+1 Central European Time",
                },
                numeric_id="1",
            )
        )
        self.assertTrue(
            FacebookBusinessBrowser._ad_account_option_matches(
                {
                    "identity": "1",
                    "label": "Pacific Time (US & Canada)",
                    "text": "1 Pacific Time (US & Canada)",
                },
                numeric_id="1",
            )
        )

    def test_kyiv_timezone_uses_current_name_and_keeps_legacy_aliases(self):
        self.assertEqual(
            FacebookBusinessBrowser._timezone_name_for_id(137),
            "Europe/Kyiv",
        )
        source = inspect.getsource(
            FacebookBusinessBrowser._prepare_ad_account_form_fields
        )
        self.assertIn("Europe/Kiev", source)
        self.assertIn("Kyiv", source)


class BrowserAdAccountFinalControlTests(unittest.TestCase):
    def test_final_control_words_exclude_next_and_continue(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._click_ad_account_final_interactive
        )
        self.assertIn("Create ad account", source)
        self.assertIn("Créer un compte publicitaire", source)
        self.assertNotIn('"Next"', source)
        self.assertNotIn('"Continue"', source)


class BrowserAdAccountFinalLabelMatchingTests(unittest.TestCase):
    def test_final_control_matches_each_label_source_separately(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._click_ad_account_final_interactive
        )
        self.assertIn("aria_label =", source)
        self.assertIn("inner_text =", source)
        self.assertIn("matched_label = next", source)
        self.assertNotIn('" ".join(', source)


class BrowserAdAccountExactlyOnceSubmitTests(unittest.TestCase):
    def test_unmatched_final_click_becomes_unknown_instead_of_second_click(self):
        source = inspect.getsource(
            FacebookBusinessBrowser.create_ad_account
        )
        self.assertIn("final_click_attempted = True", source)
        self.assertIn('"action": "final_blocked_duplicate"', source)
        self.assertIn("AD_ACCOUNT_FINAL_CLICK_UNMATCHED", source)
        self.assertIn('"phase": "CREATE_RESULT_UNKNOWN"', source)
        self.assertIn("_click_ad_account_final_interactive", source)
        self.assertIn("AD_ACCOUNT_FINAL_CLICK_EXCEPTION", source)


class BrowserAdAccountSubmitProgressionTests(unittest.TestCase):
    def test_ownership_selection_precedes_next_click(self):
        source = inspect.getsource(FacebookBusinessBrowser.create_ad_account)
        own_pos = source.index(
            "own_selected_now = ("
        )
        next_pos = source.index(
            "next_clicked = await self._click_named("
        )
        self.assertLess(own_pos, next_pos)

    def test_submit_flow_does_not_use_one_shot_own_business_attempt_flag(self):
        source = inspect.getsource(FacebookBusinessBrowser.create_ad_account)
        self.assertIn("own_business_selected = False", source)
        self.assertNotIn("own_business_attempted", source)
        self.assertGreaterEqual(
            source.count("_select_own_business_if_present()"),
            2,
        )

    def test_submit_flow_reprepares_immutable_fields_only_on_new_form_signature(self):
        source = inspect.getsource(FacebookBusinessBrowser.create_ad_account)
        self.assertIn("last_form_setup_signature", source)
        self.assertIn(
            "transition_signature != last_form_setup_signature",
            source,
        )


class BrowserAdAccountFormClassificationTests(unittest.TestCase):
    def test_form_classifier_requires_editable_or_dialog_control(self):
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_ui_state
        )
        self.assertIn("const isEditable", source)
        self.assertIn("dialogHasFormControl", source)
        self.assertNotIn(
            "if (nameWords.some(word => low.includes(word))) {\n"
            "                            formEvidence = true;",
            source,
        )


class BrowserAdAccountStateMachineTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_state_can_recognize_direct_form_open(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-direct-form")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "state": "FORM",
                    "signature": "FORM::/settings/ad_accounts::name",
                    "url": "https://business.facebook.com/latest/settings/ad_accounts",
                    "name_input": True,
                    "form_evidence": True,
                    "create_entry": False,
                    "add_surface": False,
                    "errors": [],
                    "dialogs": ["Nom du compte publicitaire Devise Fuseau horaire"],
                    "controls": ["Nom du compte publicitaire [tag=INPUT]"],
                }
            )
        )

        state = await browser._ad_account_ui_state()

        self.assertEqual(state["state"], "FORM")
        self.assertTrue(state["name_input"])
        self.assertTrue(state["form_evidence"])

    async def test_transition_stops_on_blocked_state(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-blocked")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state": "BLOCKED",
                "signature": "BLOCKED::limit",
                "url": "https://business.facebook.com/latest/settings/ad_accounts",
                "errors": ["Vous avez atteint la limite."],
                "dialogs": [],
                "controls": [],
            }
        )

        state = await browser._wait_for_ad_account_ui_transition(
            previous_signature="ADD_SURFACE::old",
            timeout_seconds=2.0,
            label="after_add",
        )

        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(
            browser._ad_account_ui_trace[-1]["label"],
            "after_add",
        )

    async def test_add_probe_accepts_form_opened_directly(self):
        class _Item:
            async def is_visible(self):
                return True
            async def is_enabled(self):
                return True
            async def scroll_into_view_if_needed(self, **kwargs):
                return None
            async def click(self, **kwargs):
                return None

        class _Locator:
            first = _Item()
            async def count(self):
                return 1

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            keyboard = _Keyboard()
            def locator(self, selector):
                return _Locator()
            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-direct-form-probe")
        )
        browser.page = _Page()
        browser._ad_account_add_button_candidates = AsyncMock(
            side_effect=[
                [{"probe_id":"0","text":"Ajouter","x":745,"y":631}],
            ]
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state":"ADD_SURFACE",
                "signature":"before",
                "url":"",
                "errors":[],
                "dialogs":[],
                "controls":[],
            }
        )
        browser._ad_account_right_pane_snapshot = AsyncMock(
            side_effect=[["Ajouter"], ["Nom du compte publicitaire"]]
        )
        browser._wait_for_fresh_ad_account_create_candidate = AsyncMock(
            return_value=(
                [],
                {
                    "state":"FORM",
                    "signature":"form",
                    "url":"",
                    "name_input": True,
                    "form_evidence": True,
                    "editable_form_control": True,
                    "errors":[],
                    "dialogs":[],
                    "controls":[
                        "Nom du compte publicitaire",
                        "Devise",
                        "Fuseau horaire",
                    ],
                },
            )
        )
        browser._wait_for_ad_account_ui_transition = AsyncMock(
            side_effect=AssertionError(
                "direct FORM path must not wait for another transition"
            )
        )

        found, attempts = await browser._probe_ad_account_add_buttons()

        self.assertTrue(found)
        self.assertTrue(attempts[0]["form_opened_during_poll"])
        self.assertEqual(attempts[0]["ui_state_after"], "FORM")


    async def test_add_probe_uses_popup_scoped_create_only(self):
        class _Item:
            async def is_visible(self):
                return True
            async def is_enabled(self):
                return True
            async def scroll_into_view_if_needed(self, **kwargs):
                return None
            async def click(self, **kwargs):
                return None

        class _Locator:
            first = _Item()
            async def count(self):
                return 1

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            keyboard = _Keyboard()
            def locator(self, selector):
                return _Locator()
            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-popup-only")
        )
        browser.page = _Page()
        browser._ad_account_add_button_candidates = AsyncMock(
            side_effect=[
                [{"probe_id":"0","text":"Ajouter","x":745,"y":631}],
            ]
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state":"ADD_SURFACE",
                "signature":"before",
                "url":"",
                "errors":[],
                "dialogs":[],
                "controls":[],
            }
        )
        browser._ad_account_right_pane_snapshot = AsyncMock(
            side_effect=[["Ajouter"], ["Créer un compte publicitaire"]]
        )
        browser._wait_for_fresh_ad_account_create_candidate = AsyncMock(
            return_value=(
                [],
                {
                    "state":"CREATE_ENTRY",
                    "signature":"popup",
                    "url":"",
                    "errors":[],
                    "dialogs":[],
                    "controls":["Créer un compte publicitaire"],
                },
            )
        )
        browser._wait_for_ad_account_ui_transition = AsyncMock(
            return_value={
                "state":"FORM",
                "signature":"wizard",
                "url":"",
                "name_input":True,
                "form_evidence":True,
                "editable_form_control":True,
                "errors":[],
                "dialogs":[],
                "controls":[
                    "Nom du compte publicitaire",
                    "Devise",
                    "Fuseau horaire",
                ],
            }
        )
        browser._ad_account_popup_candidates = AsyncMock(
            return_value=[
                "Créer un compte publicitaire [tag=DIV role=menuitem]"
            ]
        )
        browser._click_ad_account_create_entry_in_popup = AsyncMock(
            return_value={
                "clicked":True,
                "popup_count":1,
                "text":"Créer un compte publicitaire",
            }
        )
        browser._wait_for_ad_account_create_entry = AsyncMock(
            side_effect=AssertionError(
                "global create-entry search must not be used after Add"
            )
        )

        found, attempts = await browser._probe_ad_account_add_buttons()

        self.assertTrue(found)
        self.assertTrue(attempts[0]["create_entry_found"])
        self.assertTrue(attempts[0]["popup_create"]["clicked"])
        browser._wait_for_ad_account_create_entry.assert_not_awaited()


    async def test_verify_ad_account_inventory_empty_from_business_settings(self):
        class _Page:
            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-empty-rk")
        )
        browser.page = _Page()
        browser._goto = AsyncMock(return_value=None)
        browser._body_text = AsyncMock(
            return_value="Aucun compte publicitaire ajouté"
        )
        browser._ad_account_ui_state = AsyncMock(
            return_value={
                "state":"ADD_SURFACE",
                "url":(
                    "https://business.facebook.com/latest/settings/"
                    "ad_accounts?business_id=1056638030476027"
                ),
                "signature":"ADD_SURFACE::Aucun compte publicitaire ajouté",
                "controls":["Ajouter"],
                "dialogs":[],
            }
        )

        result = await browser.verify_ad_account_inventory_empty(
            business_id="1056638030476027"
        )

        self.assertTrue(result["confirmed_empty"])
        self.assertEqual(result["source"], "business_settings_ui")
        self.assertEqual(
            result["marker"],
            "aucun compte publicitaire ajouté",
        )


    async def test_final_create_does_not_accept_plain_div(self):
        class _Page:
            async def evaluate(self, script, mode):
                self.assert_mode = mode
                return {
                    "clicked": False,
                    "action": "final",
                }

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-final-div")
        )
        browser.page = _Page()

        result = await browser._click_ad_account_form_action_by_visible_text(
            "final"
        )

        self.assertFalse(result["clicked"])
        self.assertEqual(result["action"], "final")

    def test_ui_trace_is_bounded(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-trace")
        )
        for index in range(40):
            browser._record_ad_account_ui_state(
                f"step-{index}",
                {
                    "state":"UNKNOWN",
                    "url":"",
                    "errors":[],
                    "dialogs":[],
                    "controls":[],
                },
            )
        self.assertLessEqual(len(browser._ad_account_ui_trace), 24)
        self.assertEqual(
            browser._ad_account_ui_trace[-1]["label"],
            "step-39",
        )


class BrowserAdAccountVisibleTextCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_text_create_fallback_can_click_plain_meta_node(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-visible-create")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "clicked": True,
                    "text": "créer un compte publicitaire",
                    "x": 810,
                    "y": 420,
                    "tag": "DIV",
                    "role": "",
                }
            )
        )

        clicked = await browser._click_ad_account_create_entry_by_visible_text()

        self.assertTrue(clicked)
        script = browser.page.evaluate.await_args.args[0]
        self.assertIn("compte publicitaire", script)
        self.assertIn("closest(", script)

    async def test_right_pane_snapshot_is_available_for_add_diff(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-pane-diff")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value=[
                    "Créer un compte publicitaire [tag=DIV role= x=820 y=440]"
                ]
            )
        )

        rows = await browser._ad_account_right_pane_snapshot()

        self.assertEqual(len(rows), 1)
        self.assertIn("Créer un compte publicitaire", rows[0])


class BrowserAdAccountCreateEntryWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_create_entry_never_reclicks_generic_add(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-create-entry-wait")
        )
        browser.page = SimpleNamespace(
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._ad_account_ui_state = AsyncMock(
            side_effect=[
                {
                    "state":"CREATE_ENTRY",
                    "signature":"entry-1",
                    "name_input":False,
                    "dialogs":[],
                    "editable_form_control":False,
                    "controls":[],
                },
                {
                    "state":"CREATE_ENTRY",
                    "signature":"entry-1",
                    "name_input":False,
                    "dialogs":[],
                    "editable_form_control":False,
                    "controls":[],
                },
                {
                    "state":"CREATE_ENTRY",
                    "signature":"entry-2",
                    "name_input":False,
                    "dialogs":[],
                    "editable_form_control":False,
                    "controls":[],
                },
            ]
        )
        browser._click_named = AsyncMock(side_effect=[False, True])
        browser._click_ad_account_create_entry_by_visible_text = AsyncMock(
            return_value=False
        )
        browser._click_ad_account_action_dom = AsyncMock(return_value="")
        browser._wait_for_ad_account_ui_transition = AsyncMock(
            return_value={
                "state":"FORM",
                "signature":"wizard",
                "name_input":True,
                "form_evidence":True,
                "editable_form_control":True,
                "dialogs":[],
                "controls":[
                    "Nom du compte publicitaire",
                    "Devise",
                    "Fuseau horaire",
                ],
            }
        )

        ready = await browser._wait_for_ad_account_create_entry(
            timeout_seconds=2.0,
        )

        self.assertTrue(ready)
        self.assertEqual(browser._click_named.await_count, 2)
        for call in browser._click_named.await_args_list:
            self.assertEqual(
                call.args[0],
                browser.AD_ACCOUNT_CREATE_ENTRY_NAMES,
            )
            self.assertNotEqual(call.args[0], browser.ADD_NAMES)

    async def test_post_add_popup_diagnostic_exists(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-popup-diag")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value=[
                    "Créer un compte publicitaire [tag=DIV role=menuitem x=902 y=420]"
                ]
            ),
        )

        rows = await browser._ad_account_popup_candidates()

        self.assertEqual(len(rows), 1)
        self.assertIn("Créer un compte publicitaire", rows[0])


class BrowserAdAccountCreateActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_french_sidebar_text_does_not_count_as_create_action(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-french-action")
        )
        browser.page = SimpleNamespace(
            evaluate=AsyncMock(side_effect=[False, False, True]),
            wait_for_timeout=AsyncMock(return_value=None),
        )

        ready = await browser._wait_for_ad_account_create_action(
            timeout_seconds=2.0,
        )

        self.assertTrue(ready)
        self.assertEqual(browser.page.evaluate.await_count, 3)

    def test_french_add_account_labels_are_supported(self):
        self.assertIn(
            "Ajouter un compte publicitaire",
            FacebookBusinessBrowser.ADD_NAMES,
        )
        self.assertIn(
            "Ajouter des comptes publicitaires",
            FacebookBusinessBrowser.ADD_NAMES,
        )


class BrowserAdAccountHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_through_empty_meta_shell_until_ad_account_surface_renders(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-rk-hydration")
        )
        browser.page = SimpleNamespace(
            url=(
                "https://business.facebook.com/latest/settings/ad_accounts/"
                "?business_id=1056638030476027"
            ),
            evaluate=AsyncMock(return_value=False),
            wait_for_timeout=AsyncMock(return_value=None),
        )
        browser._assert_authenticated = AsyncMock(return_value=None)
        browser._body_text = AsyncMock(
            side_effect=["", "", "Ad accounts Add"]
        )

        ready = await browser._wait_for_ad_account_settings_ready(
            business_id="1056638030476027",
            timeout_seconds=2.0,
        )

        self.assertTrue(ready)
        self.assertEqual(browser._body_text.await_count, 3)


class BrowserCreateSurfaceVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mounted_create_text_counts_as_meta_create_surface(self):
        class _EmptyLocator:
            async def count(self):
                return 0

        class _Page:
            def get_by_role(self, role, name=None):
                return _EmptyLocator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-hidden-create")
        )
        browser.page = _Page()
        browser._body_text = AsyncMock(
            return_value="Create a business portfolio"
        )

        self.assertTrue(await browser._has_create_surface())


class BrowserCreateFormIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_generic_home_inputs_are_not_create_form(self):
        class _Input:
            def __init__(self, attrs):
                self.attrs = attrs

            async def get_attribute(self, name):
                return self.attrs.get(name)

        class _Locator:
            def __init__(self, rows):
                self.rows = rows

            async def count(self):
                return len(self.rows)

            def nth(self, index):
                return _Input(self.rows[index])

        class _Page:
            def locator(self, selector):
                self.last_selector = selector
                return _Locator(
                    [
                        {"type": "text", "placeholder": "Rechercher"},
                        {"type": "text", "aria-label": "Rechercher"},
                        {"type": "email", "placeholder": "E-mail"},
                        {"type": "text", "name": "instagram"},
                    ]
                )

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-french-home")
        )
        browser.page = _Page()
        browser._body_text = AsyncMock(
            return_value=(
                "Lucky Joker Connectez-vous à Instagram Créer une publication "
                "Créer une publicité Se familiariser avec Meta Business Suite"
            )
        )

        self.assertFalse(await browser._form_ready())

    async def test_french_business_form_markers_are_recognized(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-french-form")
        )
        browser.page = SimpleNamespace()
        browser._body_text = AsyncMock(
            return_value=(
                "Nom du portefeuille business "
                "Adresse e-mail professionnelle"
            )
        )

        self.assertTrue(await browser._form_ready())

    def test_french_final_submit_action_is_supported(self):
        self.assertIn("Créer", FacebookBusinessBrowser.SUBMIT_NAMES)
        self.assertIn("Continuer", FacebookBusinessBrowser.SUBMIT_NAMES)


class BrowserAuthenticationStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_feature_block_is_not_retryable(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-temp-block")
        )
        browser.page = SimpleNamespace(
            url="https://business.facebook.com/reg/"
        )
        browser._body_text = AsyncMock(
            return_value=(
                "You're Temporarily Blocked. It looks like you were "
                "misusing this feature by going too fast."
            )
        )
        browser._diagnostic = AsyncMock(return_value={})

        with self.assertRaises(BrowserBusinessError) as caught:
            await browser._assert_authenticated()

        self.assertEqual(
            caught.exception.code,
            "FACEBOOK_TEMPORARILY_BLOCKED",
        )
        self.assertFalse(caught.exception.retryable)


class BrowserNavigationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_err_aborted_is_accepted_when_meta_surface_is_alive(self):
        class _Page:
            def __init__(self):
                self.url = "https://business.facebook.com/latest/home"
                self.goto_calls = 0

            async def goto(self, *args, **kwargs):
                self.goto_calls += 1
                raise Exception(
                    "Page.goto: net::ERR_ABORTED; maybe frame was detached?"
                )

            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-nav")
        )
        browser.page = _Page()
        browser._assert_authenticated = AsyncMock(return_value=None)
        browser._body_text = AsyncMock(return_value="Meta Business Suite")
        browser._form_ready = AsyncMock(return_value=False)
        browser._has_create_surface = AsyncMock(return_value=False)

        url = await browser._goto(
            "https://business.facebook.com/reg/"
        )

        self.assertEqual(
            url,
            "https://business.facebook.com/latest/home",
        )
        self.assertEqual(browser.page.goto_calls, 1)

    async def test_err_aborted_retries_once_when_no_facebook_surface_exists(self):
        class _Page:
            def __init__(self):
                self.url = "about:blank"
                self.goto_calls = 0

            async def goto(self, *args, **kwargs):
                self.goto_calls += 1
                if self.goto_calls == 1:
                    raise Exception(
                        "Page.goto: net::ERR_ABORTED; maybe frame was detached?"
                    )
                self.url = "https://business.facebook.com/reg/"
                return None

            async def wait_for_timeout(self, ms):
                return None

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-nav-retry")
        )
        browser.page = _Page()
        browser._assert_authenticated = AsyncMock(return_value=None)
        browser._body_text = AsyncMock(return_value="")
        browser._form_ready = AsyncMock(return_value=False)
        browser._has_create_surface = AsyncMock(return_value=False)

        url = await browser._goto(
            "https://business.facebook.com/reg/"
        )

        self.assertEqual(
            url,
            "https://business.facebook.com/reg/",
        )
        self.assertEqual(browser.page.goto_calls, 2)


class BrowserPortfolioSelectorProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_portfolio_probe_does_not_scan_entire_spa_dom(self):
        class _EmptyLocator:
            async def count(self):
                return 0

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            def __init__(self):
                self.script = ""
                self.keyboard = _Keyboard()

            async def evaluate(self, script):
                self.script = script
                return {"clicked": False, "candidates": []}

            def locator(self, selector):
                return _EmptyLocator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-light-probe")
        )
        browser.page = _Page()
        browser._has_create_surface = AsyncMock(return_value=False)

        opened = await browser._try_open_top_left_portfolio_menu()

        self.assertFalse(opened)
        self.assertIn("elementsFromPoint", browser.page.script)
        self.assertNotIn("querySelectorAll('*')", browser.page.script)
        self.assertNotIn('querySelectorAll("*")', browser.page.script)


class BrowserKnownAssetSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_asset_id_prefers_known_page_selector(self):
        class _Candidate:
            def __init__(self):
                self.clicked = False

            async def is_visible(self):
                return True

            async def get_attribute(self, name):
                if name == "role":
                    return "button"
                return ""

            async def evaluate(self, script):
                return "DIV"

            async def bounding_box(self):
                return {
                    "x": 20,
                    "y": 110,
                    "width": 220,
                    "height": 48,
                }

            async def click(self, timeout=None):
                self.clicked = True

        class _Locator:
            def __init__(self, items=None):
                self.items = list(items or [])

            async def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            def __init__(self, candidate):
                self.url = (
                    "https://business.facebook.com/latest/home"
                    "?nav_ref=bm_home_redirect&asset_id=1301710056363524"
                )
                self.candidate = candidate
                self.keyboard = _Keyboard()

            def get_by_role(self, role, name=None):
                if role == "button":
                    return _Locator([self.candidate])
                return _Locator()

            def get_by_text(self, *args, **kwargs):
                return _Locator()

            async def wait_for_timeout(self, ms):
                return None

        candidate = _Candidate()
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[
                    {
                        "id": "1301710056363524",
                        "name": "Lucky Joker",
                    }
                ],
            )
        )
        browser.page = _Page(candidate)
        browser._wait_for_create_surface = AsyncMock(return_value=True)

        opened = await browser._try_open_known_asset_selector()

        self.assertTrue(opened)
        self.assertTrue(candidate.clicked)
        self.assertEqual(
            browser._last_selector_diagnostic[
                "known_asset_selector"
            ]["current_asset_id"],
            "1301710056363524",
        )


class BrowserAssetContextAdsManagerFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_asset_context_uses_ads_manager_fallback(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[
                    {
                        "id": "1301710056363524",
                        "name": "Lucky Joker",
                    }
                ],
            )
        )
        browser.page = SimpleNamespace(
            url=(
                "https://business.facebook.com/latest/home"
                "?asset_id=1301710056363524"
            )
        )
        browser._form_ready = AsyncMock(return_value=False)
        browser._quick_surface_state = AsyncMock(return_value={"blank": False})
        browser._try_open_known_asset_selector = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)
        browser._try_open_direct_create_url = AsyncMock(return_value=False)
        browser._try_open_overview_create_entry = AsyncMock(return_value=False)
        browser._try_open_ads_manager_create_entry = AsyncMock(return_value=True)
        browser._goto = AsyncMock()

        ready = await browser._open_create_entry(
            open_form=True,
            already_on_home=True,
        )

        self.assertTrue(ready)
        browser._try_open_direct_create_url.assert_awaited_once()
        browser._try_open_overview_create_entry.assert_awaited_once()
        browser._try_open_ads_manager_create_entry.assert_awaited_once()
        browser._goto.assert_not_awaited()


class BrowserAssetContextDirectCreateFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_asset_context_prefers_direct_create_before_ads_manager(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[
                    {
                        "id": "1301710056363524",
                        "name": "Lucky Joker",
                    }
                ],
            )
        )
        browser.page = SimpleNamespace(
            url=(
                "https://business.facebook.com/latest/home"
                "?asset_id=1301710056363524"
            )
        )
        browser._form_ready = AsyncMock(return_value=False)
        browser._quick_surface_state = AsyncMock(return_value={"blank": False})
        browser._try_open_known_asset_selector = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)
        browser._try_open_direct_create_url = AsyncMock(return_value=True)
        browser._try_open_overview_create_entry = AsyncMock(return_value=False)
        browser._try_open_ads_manager_create_entry = AsyncMock(return_value=False)

        ready = await browser._open_create_entry(
            open_form=True,
            already_on_home=True,
        )

        self.assertTrue(ready)
        browser._try_open_direct_create_url.assert_awaited_once()
        browser._try_open_ads_manager_create_entry.assert_not_awaited()

    async def test_direct_create_route_accepts_form_ready(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-direct-create")
        )
        browser.page = SimpleNamespace(url=browser.HOME_URL)
        browser._goto = AsyncMock(return_value=browser.DIRECT_CREATE_URL)
        browser._assert_authenticated = AsyncMock(return_value=None)
        browser._wait_for_form_ready = AsyncMock(return_value=True)
        browser._wait_for_create_surface = AsyncMock(return_value=False)

        ready = await browser._try_open_direct_create_url()

        self.assertTrue(ready)
        browser._goto.assert_awaited_once_with(browser.DIRECT_CREATE_URL)
        self.assertTrue(
            browser._last_selector_diagnostic["direct_create_route"]["form_ready"]
        )

    def test_current_create_labels_include_plain_portfolio_variant(self):
        self.assertIn("Create portfolio", FacebookBusinessBrowser.CREATE_NAMES)

    def test_current_create_labels_include_french_portfolio_variant(self):
        self.assertIn(
            "Créer un portefeuille business",
            FacebookBusinessBrowser.CREATE_NAMES,
        )


class BrowserBlankAssetShellTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_page_shell_skips_home_selectors_and_uses_overview(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[{"id": "1301710056363524", "name": "Lucky Joker"}],
            )
        )
        browser.page = SimpleNamespace(
            url=(
                "https://business.facebook.com/latest/home"
                "?nav_ref=bm_home_redirect&asset_id=1301710056363524"
            )
        )
        browser._form_ready = AsyncMock(return_value=False)
        browser._quick_surface_state = AsyncMock(
            return_value={
                "blank": True,
                "body_text_length": 0,
                "interactive_count": 0,
                "body_children": 1,
            }
        )
        browser._try_open_known_asset_selector = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)
        browser._try_open_direct_create_url = AsyncMock(return_value=False)
        browser._try_open_overview_create_entry = AsyncMock(return_value=True)
        browser._try_open_ads_manager_create_entry = AsyncMock(return_value=False)

        ready = await browser._open_create_entry(
            open_form=True,
            already_on_home=True,
        )

        self.assertTrue(ready)
        browser._try_open_known_asset_selector.assert_not_awaited()
        browser._try_open_top_left_portfolio_menu.assert_not_awaited()
        browser._try_open_direct_create_url.assert_awaited_once()
        browser._try_open_overview_create_entry.assert_awaited_once()
        browser._try_open_ads_manager_create_entry.assert_not_awaited()


class BrowserAssetContextFastFailTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_asset_context_does_not_loop_root_and_reg(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[
                    {
                        "id": "1301710056363524",
                        "name": "Lucky Joker",
                    }
                ],
            )
        )
        browser.page = SimpleNamespace(
            url=(
                "https://business.facebook.com/latest/home"
                "?nav_ref=bm_home_redirect&asset_id=1301710056363524"
            )
        )
        browser._form_ready = AsyncMock(return_value=False)
        browser._quick_surface_state = AsyncMock(return_value={"blank": False})
        browser._try_open_known_asset_selector = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)
        browser._try_open_direct_create_url = AsyncMock(return_value=False)
        browser._try_open_overview_create_entry = AsyncMock(return_value=False)
        browser._try_open_ads_manager_create_entry = AsyncMock(return_value=False)
        browser._goto = AsyncMock()

        ready = await browser._open_create_entry(
            open_form=True,
            already_on_home=True,
        )

        self.assertFalse(ready)
        browser._goto.assert_not_awaited()
        browser._try_open_known_asset_selector.assert_awaited_once()
        browser._try_open_top_left_portfolio_menu.assert_awaited_once_with(
            skip_known_asset=True,
        )
        browser._try_open_direct_create_url.assert_awaited_once()
        browser._try_open_ads_manager_create_entry.assert_awaited_once()
        self.assertTrue(
            browser._last_selector_diagnostic.get(
                "asset_context_fast_fail"
            )
        )


class BrowserLateAssetReclassifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_asset_redirect_reenters_known_page_path(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(
                profile_id="4",
                pages=[{"id": "1301710056363524", "name": "Lucky Joker"}],
            )
        )

        class FakePage:
            def __init__(self):
                self.url = browser.HOME_URL

            async def wait_for_timeout(self, _ms):
                return None

        browser.page = FakePage()
        browser._form_ready = AsyncMock(return_value=False)
        browser._quick_surface_state = AsyncMock(return_value={"blank": False})
        browser._try_open_known_asset_selector = AsyncMock(return_value=False)
        browser._try_open_direct_create_url = AsyncMock(return_value=True)
        browser._try_open_overview_create_entry = AsyncMock(return_value=False)
        browser._try_open_ads_manager_create_entry = AsyncMock(return_value=False)

        async def generic_probe(*args, **kwargs):
            browser.page.url = (
                browser.HOME_URL
                + "?asset_id=1301710056363524&ir_qe_exposed=1"
            )
            return False

        browser._try_open_top_left_portfolio_menu = AsyncMock(
            side_effect=generic_probe
        )

        ready = await browser._open_create_entry(
            open_form=True,
            already_on_home=True,
        )

        self.assertTrue(ready)
        self.assertEqual(
            browser._last_selector_diagnostic[
                "late_asset_reclassify"
            ]["asset_id"],
            "1301710056363524",
        )
        browser._try_open_direct_create_url.assert_awaited_once()


class BrowserCreateEntryRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_flow_skips_crash_prone_overview_surface(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-create-routing")
        )
        calls = []

        async def fake_goto(url):
            calls.append(url)
            return url

        browser._goto = AsyncMock(side_effect=fake_goto)
        browser._form_ready = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)

        with patch.dict(
            os.environ,
            {"REMASK_BM_LEGACY_NAV_FALLBACK": ""},
            clear=False,
        ):
            ready = await browser._open_create_entry(open_form=False)

        self.assertFalse(ready)
        self.assertEqual(
            calls,
            [
                browser.HOME_URL,
                browser.CREATE_URL,
            ],
        )
        self.assertNotIn(browser.OVERVIEW_URL, calls)

    async def test_legacy_navigation_fallback_requires_explicit_flag(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-create-routing-legacy")
        )
        calls = []

        async def fake_goto(url):
            calls.append(url)
            return url

        browser._goto = AsyncMock(side_effect=fake_goto)
        browser._form_ready = AsyncMock(return_value=False)
        browser._try_open_top_left_portfolio_menu = AsyncMock(return_value=False)

        with patch.dict(
            os.environ,
            {"REMASK_BM_LEGACY_NAV_FALLBACK": "1"},
            clear=False,
        ):
            ready = await browser._open_create_entry(open_form=False)

        self.assertFalse(ready)
        self.assertEqual(
            calls,
            [
                browser.HOME_URL,
                browser.CREATE_URL,
                browser.OVERVIEW_URL,
            ],
        )


class BrowserCreateFormTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_create_form_has_own_timeout(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-create-form-timeout")
        )
        browser.page = SimpleNamespace(url=browser.HOME_URL)
        browser._open_create_entry = AsyncMock()
        browser._diagnostic = AsyncMock(
            return_value={
                "stage": "create_form_timeout",
                "url": browser.HOME_URL,
            }
        )

        with patch(
            "app.facebook_business_browser.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            with self.assertRaises(BrowserBusinessError) as ctx:
                await browser._prepare_create_form(
                    business_name="Test Business",
                    user_email="owner@example.com",
                    user_first_name="",
                    user_last_name="",
                    profile_display_name="",
                )

        self.assertEqual(
            ctx.exception.code,
            "BUSINESS_CREATE_FORM_TIMEOUT",
        )
        self.assertTrue(ctx.exception.retryable)


class BrowserCreateFormNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_create_form_reuses_existing_home_page(self):
        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-create-form-home")
        )
        browser.page = SimpleNamespace(url=browser.HOME_URL)
        browser._open_create_entry = AsyncMock(return_value=True)
        browser._form_ready = AsyncMock(return_value=True)
        browser._fill_first = AsyncMock(return_value=True)

        await browser._prepare_create_form(
            business_name="Test Business",
            user_email="owner@example.com",
            user_first_name="",
            user_last_name="",
            profile_display_name="",
        )

        browser._open_create_entry.assert_awaited_once_with(
            open_form=True,
            already_on_home=True,
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

    async def test_discovers_pages_from_visible_page_links(self):
        class _AnchorLocator:
            async def evaluate_all(self, script):
                return [
                    {
                        "href": "https://www.facebook.com/profile.php?id=123456789",
                        "text": "Demo Fan Page",
                    }
                ]

        class _RenderedPage:
            async def wait_for_timeout(self, ms):
                return None

            async def content(self):
                return "<html><body>No embedded Page JSON</body></html>"

            def locator(self, selector):
                return _AnchorLocator()

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-pages-links")
        )
        browser.page = _RenderedPage()
        browser._goto = AsyncMock(
            return_value="https://www.facebook.com/pages/?category=your_pages"
        )

        pages = await browser.discover_managed_pages()

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "123456789")
        self.assertEqual(pages[0]["name"], "Demo Fan Page")
        self.assertEqual(pages[0]["source"], "browser_dom_link")


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
            response_friendly_name="useBusinessCreationMutationMutation",
            response_path="data.business_create.business.id",
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
            response_path="",
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

    async def test_confirmed_business_survives_page_attach_failure_for_next_job(self):
        browser = _FakeBrowser(verify_sequence=[False])
        browser.add_existing_page = AsyncMock(
            side_effect=BrowserBusinessError(
                "PAGE_ATTACH_RESULT_UNKNOWN",
                "Page attach could not be confirmed",
                retryable=True,
            )
        )

        with self.assertRaises(ProvisioningError):
            await self._run(browser)

        snapshot = await self.store.snapshot(
            self.profile_id,
            self.scope_key,
        )
        self.assertEqual(
            snapshot.business_id,
            "555666777888999",
        )

        next_item_id = "item-2"
        await self.store.set_running(
            next_item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
        )

        resume_browser = _FakeBrowser(verify_sequence=[True])
        result = await business_handler(
            _FakeSession(resume_browser),
            {
                "name": "Test Business",
                "page_id": "123456789",
                "user_email": "owner@example.com",
            },
            snapshot.as_dict(),
            provisioning_state=self.store,
            item_id=next_item_id,
            profile_id=self.profile_id,
            scope_key=self.scope_key,
        )

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(resume_browser.create_calls, 0)
        self.assertEqual(resume_browser.reconcile_calls, 0)
        self.assertEqual(resume_browser.add_calls, 0)

    async def test_service_does_not_skip_business_when_entity_exists(self):
        await self.store.remember_entity(
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {"business_id": "555666777888999"},
        )

        service = ProvisioningService(self.store)
        handler = AsyncMock(
            return_value={
                "business_id": "555666777888999",
                "primary_page_id": "123456789",
            }
        )

        with patch(
            "app.provisioning.service.get_handler",
            return_value=handler,
        ):
            result = await service.run(
                item_id="item-service-2",
                profile_id=self.profile_id,
                context=SimpleNamespace(),
                session=SimpleNamespace(),
                payload={
                    "steps": ["BUSINESS"],
                    "scope_key": self.scope_key,
                    "parameters": {
                        "BUSINESS": {
                            "name": "Test Business",
                            "page_id": "123456789",
                        }
                    },
                },
                task_idempotency_key="stable-business-test",
            )

        handler.assert_awaited_once()
        self.assertEqual(
            result["state"]["business_id"],
            "555666777888999",
        )

    async def test_new_scope_recovers_confirmed_business_from_legacy_scope(self):
        legacy_item_id = "legacy-item-confirmed"
        legacy_scope = "add-bm-legacy-nonce"
        await self.store.set_running(
            legacy_item_id,
            self.profile_id,
            legacy_scope,
            ProvisioningStep.BUSINESS,
        )
        await self.store.checkpoint(
            legacy_item_id,
            self.profile_id,
            legacy_scope,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_CONFIRMED",
                "business_id": "555666777888999",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
            },
        )

        new_item_id = "item-cross-scope-confirmed"
        new_scope = "add-bm-page-123456789"
        await self.store.set_running(
            new_item_id,
            self.profile_id,
            new_scope,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(verify_sequence=[True])
        result = await business_handler(
            _FakeSession(browser),
            {
                "name": "Test Business",
                "page_id": "123456789",
                "user_email": "owner@example.com",
            },
            {
                "profile_id": self.profile_id,
                "scope_key": new_scope,
            },
            provisioning_state=self.store,
            item_id=new_item_id,
            profile_id=self.profile_id,
            scope_key=new_scope,
        )

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.reconcile_calls, 0)

        stored = await self.store.step(
            new_item_id,
            ProvisioningStep.BUSINESS,
        )
        self.assertTrue(stored["result"].get("recovered_cross_job"))
        self.assertEqual(
            stored["result"].get("recovered_from_item_id"),
            legacy_item_id,
        )

    async def test_new_scope_reconciles_uncertain_legacy_create_before_new_create(self):
        legacy_item_id = "legacy-item-uncertain"
        legacy_scope = "add-bm-old-nonce-2"
        await self.store.set_running(
            legacy_item_id,
            self.profile_id,
            legacy_scope,
            ProvisioningStep.BUSINESS,
        )
        await self.store.checkpoint(
            legacy_item_id,
            self.profile_id,
            legacy_scope,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_SUBMITTED",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
            },
        )

        new_item_id = "item-cross-scope-uncertain"
        new_scope = "add-bm-page-123456789"
        await self.store.set_running(
            new_item_id,
            self.profile_id,
            new_scope,
            ProvisioningStep.BUSINESS,
        )

        browser = _FakeBrowser(
            reconcile_id="555666777888999",
            verify_sequence=[True],
        )
        result = await business_handler(
            _FakeSession(browser),
            {
                "name": "Test Business",
                "page_id": "123456789",
                "user_email": "owner@example.com",
            },
            {
                "profile_id": self.profile_id,
                "scope_key": new_scope,
            },
            provisioning_state=self.store,
            item_id=new_item_id,
            profile_id=self.profile_id,
            scope_key=new_scope,
        )

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertEqual(browser.create_calls, 0)
        self.assertEqual(browser.reconcile_calls, 1)

        stored = await self.store.step(
            new_item_id,
            ProvisioningStep.BUSINESS,
        )
        self.assertTrue(stored["result"].get("recovered_cross_job"))

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

    async def test_exact_create_response_checkpoint_resumes_without_reconcile(self):
        await self.store.checkpoint(
            self.item_id,
            self.profile_id,
            self.scope_key,
            ProvisioningStep.BUSINESS,
            {
                "phase": "CREATE_SUBMITTED",
                "activity": "CREATE_RESPONSE_OBSERVED",
                "business_name": "Test Business",
                "primary_page_id": "123456789",
                "business_ids_before": ["111111111111111"],
                "create_response_business_id": "555666777888999",
                "create_response_friendly_name": (
                    "useBusinessCreationMutationMutation"
                ),
                "create_response_path": "data.business_create.business.id",
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
        self.assertEqual(browser.reconcile_calls, 0)
        self.assertEqual(browser.add_calls, 0)
        self.assertTrue(result["resumed"])

        stored = await self.store.step(
            self.item_id,
            ProvisioningStep.BUSINESS,
        )
        self.assertEqual(stored["result"]["phase"], "PAGE_CONFIRMED")
        self.assertTrue(
            stored["result"].get("recovered_from_exact_create_response")
        )

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
