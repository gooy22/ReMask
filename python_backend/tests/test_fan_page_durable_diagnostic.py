from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.runner import WorkerPool, _fan_page_error_summary
from app.provisioning.models import ProvisioningStep
from app.provisioning.state import ProvisioningStateStore


class FanPageDurableDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def test_click_reason_survives_without_credentials(self):
        error = ("TimeoutError: overlay intercepts pointer events "
                 "https://user:password@www.facebook.com/pages/?token=secret-token "
                 "xs=secret-cookie fb_dtsg=secret-dtsg\nCookie: xs=secret-header")
        clean = _fan_page_error_summary(error)
        self.assertIn("overlay intercepts pointer events", clean)
        for secret in ("password", "secret-token", "secret-cookie", "secret-dtsg", "secret-header"):
            self.assertNotIn(secret, clean)

    async def test_audit_reads_pending_common_item_without_jobs_or_facebook(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(tmp + "/state.sqlite")
            await state.init()
            await state.set_running("common-uid-item", "13", "common-page", ProvisioningStep.FAN_PAGES)
            await state.checkpoint("common-uid-item", "13", "common-page", ProvisioningStep.FAN_PAGES, {
                "phase": "PAGE_CREATE_RESULT_UNKNOWN",
                "browser_diagnostic": {"stage": "fan_page_final_click_unknown", "click_meta": {
                    "attempted": True, "clicked": False,
                    "error": "TimeoutError: overlay intercepts pointer events xs=secret-cookie",
                }},
                "reconciliation": [{"source": "facebook_web_graphql", "code": "PRIVATE_LIST_PAGES_UNAVAILABLE",
                                    "message": "No current query token=secret-token"}],
            })
            before = await state.step("common-uid-item", ProvisioningStep.FAN_PAGES)
            worker = WorkerPool.__new__(WorkerPool)
            worker.provisioning_state = state
            with patch("app.runner.log") as logger:
                await worker._log_recent_fan_page_state()
            logger.info.assert_called_once()
            printed = str(logger.info.call_args)
            self.assertIn("overlay intercepts pointer events", printed)
            self.assertNotIn("secret-cookie", printed)
            self.assertNotIn("secret-token", printed)
            self.assertEqual(await state.step("common-uid-item", ProvisioningStep.FAN_PAGES), before)
