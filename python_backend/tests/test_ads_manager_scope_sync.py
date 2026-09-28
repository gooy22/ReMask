import unittest

from app.facebook_business_browser import (
    _confirmed_ads_manager_scope_account_id,
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
