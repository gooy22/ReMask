from __future__ import annotations

import inspect
import os
import unittest

from app.facebook_ad_account_create import (
    _extract_ad_account_id,
    _normalize_ad_account_id,
)
from app.provisioning.ad_account_handler import (
    _known_pre_submit_navigation_failure,
    ad_account_handler,
)
from app.provisioning.state import ProvisioningStateStore


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
