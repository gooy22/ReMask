from __future__ import annotations

import inspect
import json
import os
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode

from app.facebook_ad_account_create import (
    CREATE_AD_ACCOUNT_FRIENDLY_NAMES,
    _extract_ad_account_id,
    _normalize_ad_account_id,
    _replace_capture_values,
    _validate_rewritten_capture_variables,
    create_ad_account_with_docids,
    discover_current_ad_account_create_candidate,
)
from app.facebook_business_browser import FacebookBusinessBrowser
from app.provisioning.ad_account_handler import (
    AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES,
    _inventory_repeatedly_confirms_empty,
    _known_final_click_unmatched_empty_inventory,
    _known_pre_submit_navigation_failure,
    _known_pre_submit_usage_step_failure,
    ad_account_handler,
)
from app.provisioning.state import ProvisioningStateStore


class AdAccountRuntimeDiagnosticTests(unittest.TestCase):
    def test_handler_logs_full_browser_diagnostic(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("AD_ACCOUNT live-capture browser failure", source)
        self.assertIn("json.dumps(", source)
        self.assertIn("exc.diagnostic", source)


class AdAccountCreateEntryTargetTests(unittest.TestCase):
    def test_add_candidates_keep_top_add_for_meta_create_surface(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_add_button_candidates
        )
        self.assertNotIn(
            "if (r.y < 180 && !localAccountContext)",
            source,
        )

    def test_add_candidate_probe_defines_local_context_helper(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_add_button_candidates
        )
        self.assertIn("const hasLocalAccountContext = el =>", source)
        self.assertIn(
            "const localAccountContext = hasLocalAccountContext(el);",
            source,
        )

    def test_create_entry_state_clicks_exact_tagged_target(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._wait_for_ad_account_create_entry
        )
        self.assertIn(
            "_click_state_detected_ad_account_create_entry()",
            source,
        )
        clicker = inspect.getsource(
            FacebookBusinessBrowser._click_state_detected_ad_account_create_entry
        )
        self.assertIn(
            '[data-remask-rk-create-state="1"]',
            clicker,
        )

    def test_ui_state_exposes_concrete_create_target(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._ad_account_ui_state
        )
        self.assertIn("data-remask-rk-create-state", source)
        self.assertIn("create_target", source)

    def test_exact_migrated_ad_accounts_route_is_preferred(self) -> None:
        first = FacebookBusinessBrowser.SETTINGS_AD_ACCOUNTS_URLS[0]
        self.assertIn("nav_ref=bm_settings_redirect_migration", first)
        self.assertIn("bm_redirect_migration=true", first)
        self.assertIn("business_id={business_id}", first)


class AdAccountDetailsExpansionTests(unittest.TestCase):
    def test_browser_has_read_only_details_expander(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._expand_ad_account_details_if_present
        )
        self.assertIn("Afficher les détails", source)
        self.assertIn("Show details", source)
        self.assertIn("_ad_account_ui_state()", source)

    def test_add_probe_expands_details_after_direct_create_click(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._probe_ad_account_add_buttons
        )
        self.assertIn("details_markers", source)
        self.assertIn(
            "_expand_ad_account_details_if_present()",
            source,
        )
        self.assertIn("details_state", source)
        self.assertIn("details_errors", source)

    def test_final_failure_rechecks_expanded_meta_state(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser._open_ad_account_create_form
        )
        self.assertIn("after_create_entry_details", source)
        self.assertIn(
            "_expand_ad_account_details_if_present()",
            source,
        )
        self.assertIn("META_AD_ACCOUNT_CREATE_UNAVAILABLE", source)


class AdAccountCreateTransportTests(unittest.TestCase):
    def test_handler_requires_live_capture_then_private_replay(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("capture_ad_account_create_request(", source)
        self.assertIn("create_ad_account_with_docids(", source)
        self.assertIn("facebook_private_graphql_live_capture", source)
        self.assertNotIn("create_ad_account_for_business(", source)

    def test_browser_capture_aborts_real_create_before_meta(self) -> None:
        source = inspect.getsource(
            FacebookBusinessBrowser.capture_ad_account_create_request
        )
        self.assertIn("_open_ad_account_create_form(", source)
        self.assertIn("_prepare_ad_account_form_fields(", source)
        self.assertIn("_request_matches_ad_account_create(", source)
        self.assertIn("await route.abort()", source)
        self.assertIn("_click_ad_account_final_interactive()", source)

    def test_private_create_has_no_docid_fallback(self) -> None:
        source = inspect.getsource(create_ad_account_with_docids)
        self.assertIn("CREATE_AD_ACCOUNT_LIVE_CAPTURE_REQUIRED", source)
        self.assertIn('source="live_ui_capture"', source)
        self.assertNotIn("discover_current_ad_account_create_candidate(", source)
        self.assertNotIn("list_candidates(", source)
        self.assertNotIn("create_ad_account_for_business(", source)


class AdAccountDuplicateSafetyTests(unittest.TestCase):
    def test_click_intent_is_uncertain_in_handler(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertGreaterEqual(
            source.count('"CREATE_CLICK_INTENT"'),
            3,
        )
        self.assertIn('"RECONCILE_CREATE"', source)

    def test_click_intent_is_cross_job_uncertain_in_state_store(self) -> None:
        source = inspect.getsource(
            ProvisioningStateStore._latest_ad_account_resume_for_business_sync
        )
        self.assertIn('"CREATE_CLICK_INTENT"', source)
        self.assertIn('"CREATE_RESULT_UNKNOWN"', source)

    def test_proven_empty_browser_inventory_unlocks_stale_cross_job_guard(self) -> None:
        source = inspect.getsource(ad_account_handler)
        empty_check = 'browser_inventory.get("confirmed_empty")'
        guard_message = "previous Job may already have submitted CREATE"
        self.assertIn(empty_check, source)
        self.assertIn(guard_message, source)
        self.assertLess(source.index(empty_check), source.index(guard_message))




class AdAccountUiStateRegressionTests(unittest.TestCase):
    def test_normal_ad_accounts_surface_is_not_create_form(self) -> None:
        state = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [],
            "editable_form_control": True,
            "controls": [
                "Comptes publicitaires [tag=H2 role= x=410 y=120]",
                "Rechercher [tag=INPUT role= x=520 y=190]",
            ],
        }
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(state)
        )

    def test_real_localized_wizard_is_confirmed(self) -> None:
        state = {
            "state": "FORM",
            "name_input": True,
            "dialogs": [],
            "editable_form_control": True,
            "controls": [
                "Nom du compte publicitaire [tag=INPUT role= x=620 y=330]",
                "Devise [tag=DIV role=combobox x=620 y=410]",
                "Fuseau horaire [tag=DIV role=combobox x=620 y=480]",
            ],
        }
        self.assertTrue(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(state)
        )

    def test_unwrapped_wizard_requires_multiple_form_markers(self) -> None:
        good = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [],
            "editable_form_control": True,
            "controls": [
                "Devise [tag=DIV role=combobox x=620 y=410]",
                "Fuseau horaire [tag=DIV role=combobox x=620 y=480]",
            ],
        }
        bad = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [],
            "editable_form_control": True,
            "controls": ["Devise [tag=DIV role=combobox x=620 y=410]"],
        }
        self.assertTrue(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(good)
        )
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(bad)
        )

    def test_ownership_step_counts_as_wizard(self) -> None:
        state = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [],
            "editable_form_control": False,
            "controls": [
                "Pour mon entreprise [tag=DIV role=radio x=640 y=420]"
            ],
        }
        self.assertTrue(
            FacebookBusinessBrowser._ad_account_ownership_step_present(state)
        )
        self.assertTrue(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(state)
        )

    def test_plain_ad_accounts_page_is_not_ownership_step(self) -> None:
        state = {
            "state": "ADD_SURFACE",
            "name_input": False,
            "dialogs": [],
            "editable_form_control": False,
            "controls": [
                "Comptes publicitaires [tag=H2 role= x=430 y=120]",
                "Ajouter [tag=DIV role=button x=790 y=630]",
            ],
        }
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_ownership_step_present(state)
        )

    def test_meta_ai_dialog_is_not_create_form(self) -> None:
        state = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [
                (
                    "Assistant business Meta AI "
                    "Posez des questions sur votre compte publicitaire"
                )
            ],
            "editable_form_control": True,
            "controls": [
                "Assistant business Meta AI [tag=DIV role=dialog x=900 y=80]",
                "Message [tag=INPUT role=textbox x=940 y=710]",
            ],
        }
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(state)
        )

    def test_meta_ai_generic_ad_account_text_does_not_confirm_wizard(self) -> None:
        state = {
            "state": "FORM",
            "name_input": False,
            "dialogs": [
                (
                    "Meta AI Business Assistant "
                    "Ask about your ad account"
                )
            ],
            "editable_form_control": True,
            "controls": [
                "Ask Meta AI [tag=INPUT role=textbox x=930 y=700]",
            ],
        }
        self.assertFalse(
            FacebookBusinessBrowser._ad_account_create_form_confirmed(state)
        )



class AdAccountFalseUncertaintyRecoveryTests(unittest.TestCase):
    def test_final_click_unmatched_empty_inventory_is_recoverable_evidence(self) -> None:
        result = {
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
                "state_after": {
                    "state": "FORM",
                    "signature": (
                        "FORM::/latest/settings/ad_accounts::"
                        "Aucun compte publicitaire ajouté"
                    ),
                },
                "graphql_candidates": [
                    {
                        "friendly_name": (
                            "BizKitSettingsCreateAdAccountUsageStepQuery"
                        ),
                        "matched_create": False,
                    }
                ],
            },
        }
        self.assertTrue(
            _known_final_click_unmatched_empty_inventory(result)
        )

    def test_real_create_candidate_keeps_duplicate_guard(self) -> None:
        result = {
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
                "state_after": {
                    "signature": "Aucun compte publicitaire ajouté",
                },
                "graphql_candidates": [
                    {
                        "friendly_name": (
                            "BizKitSettingsCreateAdAccountMutation"
                        ),
                        "matched_create": True,
                    }
                ],
            },
        }
        self.assertFalse(
            _known_final_click_unmatched_empty_inventory(result)
        )


class AdAccountUsageStepRegressionTests(unittest.TestCase):
    def test_old_usage_step_query_is_safe_pre_submit_recovery(self) -> None:
        result = {
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
                "graphql_candidates": [
                    {
                        "friendly_name": (
                            "BizKitSettingsCreateAdAccountUsageStepQuery"
                        ),
                        "matched_create": False,
                    }
                ],
            },
        }
        self.assertTrue(_known_pre_submit_usage_step_failure(result))

    def test_real_create_candidate_is_never_downgraded_to_pre_submit(self) -> None:
        result = {
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
                "graphql_candidates": [
                    {
                        "friendly_name": (
                            "BizKitSettingsCreateAdAccountUsageStepQuery"
                        ),
                        "matched_create": False,
                    },
                    {
                        "friendly_name": "BizKitSettingsCreateAdAccountMutation",
                        "matched_create": True,
                    },
                ],
            },
        }
        self.assertFalse(_known_pre_submit_usage_step_failure(result))



class AdAccountCreateRequestMatcherTests(unittest.TestCase):
    def test_generic_operation_with_businessID_and_immutable_fields_matches(self) -> None:
        request = SimpleNamespace(
            method="POST",
            url="https://business.facebook.com/api/graphql/",
            headers={},
            post_data=urlencode(
                {
                    "fb_api_req_friendly_name": "BizKitSettingsSubmitStep",
                    "doc_id": "30132031866444376",
                    "variables": json.dumps(
                        {
                            "input": {
                                "businessID": "1056638030476027",
                                "name": "ReMask RK",
                                "currency": "USD",
                                "timezone_id": 137,
                            }
                        }
                    ),
                }
            ),
            post_data_buffer=None,
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="1056638030476027",
                account_name="ReMask RK",
            )
        )

    def test_usage_query_with_only_businessID_does_not_match(self) -> None:
        request = SimpleNamespace(
            method="POST",
            url="https://business.facebook.com/api/graphql/",
            headers={},
            post_data=urlencode(
                {
                    "fb_api_req_friendly_name": (
                        "BizKitSettingsCreateAdAccountUsageStepQuery"
                    ),
                    "doc_id": "30132031866444376",
                    "variables": json.dumps(
                        {"businessID": "1056638030476027"}
                    ),
                }
            ),
            post_data_buffer=None,
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="1056638030476027",
                account_name="ReMask RK",
            )
        )

    def test_graphql_get_transport_with_method_post_matches(self) -> None:
        variables = {
            "input": {
                "businessID": "1056638030476027",
                "name": "ReMask RK",
                "currency": "USD",
                "timezone_id": 137,
            }
        }
        request = SimpleNamespace(
            method="GET",
            url=(
                "https://graph.facebook.com/graphql?"
                + urlencode(
                    {
                        "method": "post",
                        "fb_api_req_friendly_name": (
                            "BizKitSettingsCreateAdAccountMutation"
                        ),
                        "doc_id": "30132031866444376",
                        "variables": json.dumps(variables),
                    }
                )
            ),
            headers={},
            post_data="",
            post_data_buffer=None,
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_ad_account_create(
                request,
                business_id="1056638030476027",
                account_name="ReMask RK",
            )
        )

    def test_safe_network_summary_redacts_values(self) -> None:
        request = SimpleNamespace(
            method="GET",
            url=(
                "https://graph.facebook.com/graphql?"
                + urlencode(
                    {
                        "method": "post",
                        "fb_api_req_friendly_name": "BizKitSettingsSubmitStep",
                        "doc_id": "123456789",
                        "variables": '{"secret":"SHOULD_NOT_APPEAR"}',
                        "access_token": "SECRET_TOKEN",
                    }
                )
            ),
            post_data="",
        )
        summary = FacebookBusinessBrowser._safe_meta_network_request_summary(
            request
        )
        self.assertEqual(summary["host"], "graph.facebook.com")
        self.assertEqual(summary["path"], "/graphql")
        self.assertEqual(summary["browser_method"], "GET")
        self.assertEqual(summary["effective_method"], "POST")
        self.assertEqual(
            summary["friendly_name"],
            "BizKitSettingsSubmitStep",
        )
        self.assertEqual(summary["doc_id"], "123456789")
        self.assertIn("variables", summary["param_keys"])
        self.assertIn("access_token", summary["param_keys"])
        self.assertNotIn("SECRET_TOKEN", repr(summary))
        self.assertNotIn("SHOULD_NOT_APPEAR", repr(summary))


class AdAccountCreateResponseTests(unittest.TestCase):
    def test_numeric_id_is_canonicalized(self) -> None:
        self.assertEqual(
            _normalize_ad_account_id("123456789"),
            "act_123456789",
        )

    def test_act_id_is_preserved(self) -> None:
        self.assertEqual(
            _normalize_ad_account_id("act_123456789"),
            "act_123456789",
        )

    def test_current_nested_shape(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "ad_account_create": {
                        "ad_account": {"id": "act_123456789"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_123456789")
        self.assertEqual(path, "data.ad_account_create.ad_account.id")

    def test_business_nested_shape(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "business_ad_account_create": {
                        "ad_account": {"id": "987654321"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_987654321")
        self.assertEqual(path, "data.business_ad_account_create.ad_account.id")

    def test_current_bizkit_renamed_node_shape(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "bizkit_settings_create_ad_account": {
                        "created_ad_account": {
                            "account_id": "765432109"
                        }
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_765432109")
        self.assertIn("account_id", path)

    def test_recursive_ad_account_parent_id_is_accepted(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "some_new_relay_payload": {
                        "advertising_account": {
                            "id": "765432110"
                        }
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_765432110")
        self.assertEqual(
            path,
            "data.some_new_relay_payload.advertising_account.id",
        )

    def test_unrelated_numeric_ids_are_not_mistaken_for_rk(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "bizkit_settings_create_ad_account": {
                        "business": {"id": "1056638030476027"},
                        "actor": {"id": "123456789"},
                    }
                }
            }
        )
        self.assertEqual(account_id, "")
        self.assertEqual(path, "")

    def test_ambiguous_ids_are_rejected(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "ad_account_create": {
                        "id": "111111",
                        "ad_account": {"id": "222222"},
                    }
                }
            }
        )
        self.assertEqual(account_id, "")
        self.assertEqual(path, "")

    def test_page_goto_timeout_is_known_pre_submit_failure(self) -> None:
        self.assertTrue(
            _known_pre_submit_navigation_failure(
                {
                    "last_error": (
                        "Facebook browser GraphQL transport failure: "
                        "TimeoutError: Page.goto: Timeout 15000ms exceeded. "
                        "Call log: navigating to "
                        "https://business.facebook.com/latest/home"
                    )
                }
            )
        )

    def test_post_submit_timeout_is_not_migrated(self) -> None:
        self.assertFalse(
            _known_pre_submit_navigation_failure(
                {
                    "last_error": (
                        "Facebook browser GraphQL transport failure: "
                        "TimeoutError during page.evaluate"
                    )
                }
            )
        )


if __name__ == "__main__":
    unittest.main()


class BrowserQueueAwareTimeoutTests(unittest.TestCase):
    def test_default_rk_timeout_covers_browser_queue_waves(self) -> None:
        from unittest.mock import patch
        from app.provisioning.models import ProvisioningStep
        from app.provisioning.timeouts import (
            browser_provisioning_hard_timeout,
            browser_queue_waves,
            browser_step_timeout,
        )

        env = {
            "REMASK_WORKER_CONCURRENCY": "30",
            "REMASK_BM_BROWSER_CONCURRENCY": "2",
        }
        with patch.dict(os.environ, env, clear=False):
            for key in (
                "REMASK_AD_ACCOUNT_STEP_TIMEOUT",
                "REMASK_BUSINESS_STEP_TIMEOUT",
                "REMASK_ADD_RK_HARD_TIMEOUT_SECONDS",
                "REMASK_ADD_BM_HARD_TIMEOUT_SECONDS",
                "REMASK_BROWSER_PROVISIONING_HARD_TIMEOUT_SECONDS",
            ):
                os.environ.pop(key, None)
            self.assertEqual(browser_queue_waves(), 15)
            rk_timeout = browser_step_timeout(
                ProvisioningStep.AD_ACCOUNT
            )
            self.assertGreater(rk_timeout, 150.0)
            self.assertGreater(
                browser_provisioning_hard_timeout(
                    [ProvisioningStep.AD_ACCOUNT]
                ),
                rk_timeout,
            )

    def test_combined_browser_watchdog_covers_both_steps(self) -> None:
        from unittest.mock import patch
        from app.provisioning.models import ProvisioningStep
        from app.provisioning.timeouts import (
            browser_provisioning_hard_timeout,
            browser_step_timeout,
        )

        env = {
            "REMASK_WORKER_CONCURRENCY": "30",
            "REMASK_BM_BROWSER_CONCURRENCY": "2",
        }
        with patch.dict(os.environ, env, clear=False):
            for key in (
                "REMASK_AD_ACCOUNT_STEP_TIMEOUT",
                "REMASK_BUSINESS_STEP_TIMEOUT",
                "REMASK_ADD_RK_HARD_TIMEOUT_SECONDS",
                "REMASK_ADD_BM_HARD_TIMEOUT_SECONDS",
                "REMASK_BROWSER_PROVISIONING_HARD_TIMEOUT_SECONDS",
            ):
                os.environ.pop(key, None)
            total = browser_provisioning_hard_timeout(
                [
                    ProvisioningStep.BUSINESS,
                    ProvisioningStep.AD_ACCOUNT,
                ]
            )
            self.assertGreater(
                total,
                browser_step_timeout(ProvisioningStep.BUSINESS),
            )
            self.assertGreater(
                total,
                browser_step_timeout(ProvisioningStep.AD_ACCOUNT),
            )


class AdAccountRepeatedInventoryRecoveryTests(unittest.TestCase):
    def test_three_empty_inventory_checks_allow_recovery(self) -> None:
        diagnostics = [
            {"stage":"inventory","result":"ok","count":0},
            {"stage":"inventory","result":"ok","count":0},
            {"stage":"inventory","result":"ok","count":0},
        ]
        self.assertTrue(
            _inventory_repeatedly_confirms_empty(
                diagnostics,
                required_checks=3,
            )
        )

    def test_two_empty_and_one_unavailable_do_not_allow_recovery(self) -> None:
        diagnostics = [
            {"stage":"inventory","result":"ok","count":0},
            {"stage":"inventory","result":"unavailable"},
            {"stage":"inventory","result":"ok","count":0},
        ]
        self.assertFalse(
            _inventory_repeatedly_confirms_empty(
                diagnostics,
                required_checks=3,
            )
        )

    def test_nonempty_inventory_does_not_count_as_empty(self) -> None:
        diagnostics = [
            {"stage":"inventory","result":"ok","count":0},
            {"stage":"inventory","result":"ok","count":1},
            {"stage":"inventory","result":"ok","count":0},
        ]
        self.assertFalse(
            _inventory_repeatedly_confirms_empty(
                diagnostics,
                required_checks=3,
            )
        )



class AdAccountSelfHealingPipelineRegressionTests(unittest.TestCase):
    def test_capture_phase_retries_safe_pre_submit_ui_failures(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("capture_attempt_limit = 3", source)
        self.assertIn("AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES", source)
        self.assertIn("capture_failures", source)
        self.assertIn("await asyncio.sleep(0.75 * capture_attempt)", source)

    def test_replay_retries_only_proven_pre_submit_transport_failure(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("replay_attempt_limit = 2", source)
        self.assertIn(
            'exc.code == "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT"',
            source,
        )
        self.assertIn("safe_same_capture_retry", source)
        self.assertIn("return await reconcile_after_uncertain", source)

    def test_safe_capture_codes_exclude_uncertain_and_rejected_results(self) -> None:
        self.assertIn(
            "AD_ACCOUNT_CREATE_UI_CHANGED",
            AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES,
        )
        self.assertNotIn(
            "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
            AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES,
        )
        self.assertNotIn(
            "AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED",
            AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES,
        )
        self.assertNotIn(
            "META_AD_ACCOUNT_CREATE_REJECTED",
            AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES,
        )


class AdAccountCapturedVariableSafetyTests(unittest.TestCase):
    def test_rewrite_preserves_integer_timezone_and_business_id_types(self) -> None:
        rewritten = _replace_capture_values(
            {
                "input": {
                    "businessID": 111222333444555,
                    "name": "capture-canary",
                    "currency": "EUR",
                    "time_zone_id": 57,
                }
            },
            canary_name="capture-canary",
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        payload = rewritten["input"]
        self.assertEqual(payload["businessID"], 1056638030476027)
        self.assertIsInstance(payload["businessID"], int)
        self.assertEqual(payload["time_zone_id"], 137)
        self.assertIsInstance(payload["time_zone_id"], int)
        self.assertEqual(payload["name"], "ReMask RK")
        self.assertEqual(payload["currency"], "USD")

    def test_rewrite_keeps_string_scalar_types_when_meta_captured_strings(self) -> None:
        rewritten = _replace_capture_values(
            {
                "input": {
                    "business_id": "111222333444555",
                    "ad_account_name": "capture-canary",
                    "currency_code": "EUR",
                    "timezoneId": "57",
                }
            },
            canary_name="capture-canary",
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        payload = rewritten["input"]
        self.assertEqual(payload["business_id"], "1056638030476027")
        self.assertEqual(payload["timezoneId"], "137")

    def test_rewritten_payload_validation_requires_all_immutable_values(self) -> None:
        valid = _validate_rewritten_capture_variables(
            {
                "input": {
                    "businessID": "1056638030476027",
                    "name": "ReMask RK",
                    "currency": "USD",
                    "timezone_id": 137,
                }
            },
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        self.assertTrue(valid["ok"])

        invalid = _validate_rewritten_capture_variables(
            {
                "input": {
                    "businessID": "1056638030476027",
                    "name": "ReMask RK",
                    "currency": "USD",
                }
            },
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("timezone", invalid["missing"])


class AdAccountExactlyOnceSystemRegressionTests(unittest.TestCase):
    def test_unmatched_capture_reconciles_before_any_retry(self) -> None:
        source = inspect.getsource(ad_account_handler)
        unmatched_pos = source.index(
            'exc.code == "AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED"'
        )
        reconcile_pos = source.index(
            "_inventory_repeatedly_confirms_empty(",
            unmatched_pos,
        )
        safe_retry_pos = source.index(
            "safe_retry = True",
            unmatched_pos,
        )
        self.assertLess(reconcile_pos, safe_retry_pos)
        self.assertIn(
            "capture_escape_inventory_reconciliation",
            source,
        )
        self.assertIn(
            "capture_escape_business_settings_inventory",
            source,
        )

    def test_cross_job_guard_requires_graph_and_secondary_empty_proof(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("graph_empty_confirmed =", source)
        self.assertIn("secondary_empty_confirmed =", source)
        self.assertIn(
            "if graph_empty_confirmed and secondary_empty_confirmed:",
            source,
        )
        self.assertIn(
            "Independent inventory checks",
            source,
        )

    def test_uncertain_current_job_polls_inventory_before_failure(self) -> None:
        source = inspect.getsource(ad_account_handler)
        self.assertIn("for reconcile_attempt in range(3):", source)
        self.assertIn("await asyncio.sleep(2.0)", source)
        self.assertIn("AD_ACCOUNT_RECONCILE_EXHAUSTED", source)


class AdAccountUnknownCaptureExceptionRecoveryTests(unittest.TestCase):
    def test_unknown_capture_exception_reconciles_before_retry(self) -> None:
        source = inspect.getsource(ad_account_handler)
        marker = source.index(
            'code": "AD_ACCOUNT_CAPTURE_BROWSER_EXCEPTION"'
        )
        tail = source[marker:marker + 12000]
        self.assertIn(
            "capture_exception_inventory_reconciliation",
            tail,
        )
        self.assertIn(
            "capture_exception_business_settings_inventory",
            tail,
        )
        self.assertIn(
            "graph_empty",
            tail,
        )
        self.assertIn(
            "secondary_empty",
            tail,
        )
        self.assertIn(
            "continue",
            tail,
        )
        self.assertIn(
            "Duplicate CREATE remains blocked",
            tail,
        )


class AdAccountNestedCapturedPayloadTests(unittest.TestCase):
    def test_nested_ad_account_data_receives_required_attribution_defaults(self) -> None:
        rewritten = _replace_capture_values(
            {
                "businessID": 111222333444555,
                "adAccountData": {
                    "name": "capture-canary",
                    "currency": "EUR",
                    "timezoneId": 57,
                },
            },
            canary_name="capture-canary",
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        payload = rewritten["adAccountData"]
        self.assertEqual(payload["name"], "ReMask RK")
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["timezoneId"], 137)
        self.assertEqual(payload["end_advertiser"], "NONE")
        self.assertEqual(payload["media_agency"], "NONE")
        self.assertEqual(payload["partner"], "NONE")

    def test_ambiguous_nested_payloads_are_not_blindly_patched(self) -> None:
        rewritten = _replace_capture_values(
            {
                "left": {"currency": "EUR", "timezone_id": 57},
                "right": {"currency": "EUR", "timezone_id": 57},
            },
            canary_name="",
            business_id="1056638030476027",
            account_name="ReMask RK",
            currency="USD",
            timezone_id=137,
        )
        self.assertNotIn("end_advertiser", rewritten["left"])
        self.assertNotIn("end_advertiser", rewritten["right"])
