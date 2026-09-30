import unittest

from app.facebook_business_browser import (
    _ad_account_compare_digits,
    _ads_manager_scope_account_from_request,
    _confirmed_ads_manager_scope_account_id,
)


class AdsManagerExpectedAccountNormalizationTests(unittest.TestCase):
    def test_expected_rk_act_prefix_matches_live_numeric_act(self):
        self.assertEqual(
            _ad_account_compare_digits("act_2172569806673120"),
            "2172569806673120",
        )
        self.assertEqual(
            _ad_account_compare_digits("2172569806673120"),
            "2172569806673120",
        )


class AdsManagerRequestScopeTests(unittest.TestCase):
    def test_request_pair_extracts_target_rk(self):
        meta = {
            "friendly_name": (
                "NorthStarBusinessUnifiedScopingSelector"
                "FirstAndZeroLevelScopesSectionAllZeroLevelScopesQuery"
            ),
            "variables": {
                "firstLevelScopeId": "61594753560938",
                "zeroLevelScopeId": "2172569806673120",
                "businessIdForAddAA": "61594753560938",
                "scopeIDs": ["61594753560938"],
            },
        }
        self.assertEqual(
            _ads_manager_scope_account_from_request(
                meta,
                business_id="61594753560938",
            ),
            "2172569806673120",
        )

    def test_request_pair_rejects_page_or_wrong_business(self):
        meta = {
            "friendly_name": (
                "NorthStarBusinessUnifiedScopingSelector"
                "FirstAndZeroLevelScopesSectionAllZeroLevelScopesQuery"
            ),
            "variables": {
                "firstLevelScopeId": "99999999999999",
                "zeroLevelScopeId": "1289628847574478",
                "businessIdForAddAA": "99999999999999",
            },
        }
        self.assertEqual(
            _ads_manager_scope_account_from_request(
                meta,
                business_id="61594753560938",
            ),
            "",
        )

    def test_request_pair_rejects_generic_query(self):
        meta = {
            "friendly_name": "SomeGenericAccountQuery",
            "variables": {
                "firstLevelScopeId": "61594753560938",
                "zeroLevelScopeId": "2172569806673120",
                "businessIdForAddAA": "61594753560938",
            },
        }
        self.assertEqual(
            _ads_manager_scope_account_from_request(
                meta,
                business_id="61594753560938",
            ),
            "",
        )


class AdsManagerScopeConfirmationTests(unittest.TestCase):
    def test_confirms_same_query_business_and_account_scope(self):
        diagnostics = [
            {
                "variable_numeric_ids": [
                    {
                        "path": "variables.firstLevelScopeId",
                        "value": "61594753560938",
                    },
                    {
                        "path": "variables.zeroLevelScopeId",
                        "value": "2172569806673120",
                    },
                    {
                        "path": "variables.businessIdForAddAA",
                        "value": "61594753560938",
                    },
                    {
                        "path": "variables.scopeIDs[0]",
                        "value": "61594753560938",
                    },
                ]
            }
        ]
        self.assertEqual(
            _confirmed_ads_manager_scope_account_id(
                business_id="61594753560938",
                final_act_ids=["2172569806673120"],
                diagnostics=diagnostics,
            ),
            "2172569806673120",
        )

    def test_rejects_unrelated_generic_parser_row(self):
        diagnostics = [
            {
                "variable_numeric_ids": [
                    {
                        "path": "variables.firstLevelScopeId",
                        "value": "61594753560938",
                    },
                    {
                        "path": "variables.zeroLevelScopeId",
                        "value": "2172569806673120",
                    },
                    {
                        "path": "variables.businessIdForAddAA",
                        "value": "61594753560938",
                    },
                ],
                "rows": [
                    {"id": "act_120249206211480625"}
                ],
            }
        ]
        self.assertEqual(
            _confirmed_ads_manager_scope_account_id(
                business_id="61594753560938",
                final_act_ids=["120249206211480625"],
                diagnostics=diagnostics,
            ),
            "",
        )

    def test_confirms_global_local_scope_pair(self):
        diagnostics = [
            {
                "variable_numeric_ids": [
                    {
                        "path": "variables.globalScopeID",
                        "value": "61594753560938",
                    },
                    {
                        "path": "variables.localScopeID",
                        "value": "2172569806673120",
                    },
                ]
            }
        ]
        self.assertEqual(
            _confirmed_ads_manager_scope_account_id(
                business_id="61594753560938",
                final_act_ids=["2172569806673120"],
                diagnostics=diagnostics,
            ),
            "2172569806673120",
        )


if __name__ == "__main__":
    unittest.main()
