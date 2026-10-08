import asyncio
import tempfile
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fb_worker import RemoteRequestError
from app.business_create_service import BusinessCreateError, create_business_resilient
from app.facebook_business_browser import FacebookBusinessBrowser
from app.facebook_business_create import create_business_with_docids
from app.provisioning.business_handler import business_handler
from app.provisioning.ad_account_handler import ad_account_handler, _reconcile_existing, _prove_empty_after_uncertainty
from app.provisioning.fan_pages_handler import fan_pages_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.runner import WorkerPool
from app.session import ProfileContextError
from app.store import JobStore


PARAMS = {"name": "Audit Business", "page_id": "123456789", "user_email": "owner@example.com"}


class _Task:
    action = "provisioning"
    payload = {"steps": ["BUSINESS"]}
    idempotency_key = "audit-task"

    def model_dump(self, **kwargs):
        return {}


REQUEST = SimpleNamespace(idempotency_key="audit-job", profiles=[SimpleNamespace(profile_id="audit-profile", tasks=[_Task()])])


class SystemAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "jobs.sqlite")
        self.jobs = JobStore(self.path)
        self.state = ProvisioningStateStore(self.path)
        await self.jobs.init()
        await self.state.init()
        self.browser = AsyncMock()
        self.browser.verify_page_attached.return_value = True
        self.controller = SimpleNamespace(create_business_manager_detailed=AsyncMock())
        self.session = SimpleNamespace(
            context=SimpleNamespace(profile_id="audit-profile", access_token="legacy-token", pages=[]),
            facebook_controller=AsyncMock(return_value=self.controller),
            facebook_business_browser=AsyncMock(return_value=self.browser),
            graph_api=AsyncMock(side_effect=AssertionError("official API forbidden")),
        )
        self.controller.session = self.session

    async def run_business(self, item="audit-item", scope="audit-scope"):
        await self.state.set_running(item, "audit-profile", scope, ProvisioningStep.BUSINESS)
        return await business_handler(self.session, {**PARAMS, "attach_page": True}, {}, provisioning_state=self.state,
                                      item_id=item, profile_id="audit-profile", scope_key=scope)

    async def test_database_connections_close_and_rollback_on_error(self):
        for store in [self.jobs, self.state]:
            with store._connect() as connection:
                connection.execute("SELECT 1")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        with self.assertRaises(RuntimeError):
            with self.jobs._connect() as connection:
                connection.execute("INSERT INTO jobs(id,status,created_at,updated_at) VALUES('rolled-back','QUEUED',1,1)")
                raise RuntimeError("abort transaction")
        with self.jobs._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs WHERE id='rolled-back'").fetchone()[0], 0)

    async def test_cancel_after_private_send_blocks_create_in_new_job(self):
        async def lost_response(**kwargs):
            await kwargs["before_submit"]()
            raise asyncio.CancelledError()
        self.controller.create_business_manager_detailed.side_effect = lost_response
        with self.assertRaises(asyncio.CancelledError):
            await self.run_business()
        saved = await self.state.step("audit-item", ProvisioningStep.BUSINESS)
        self.assertEqual(saved["result"]["phase"], "CREATE_SUBMITTED")
        with self.assertRaises(ProvisioningError) as error:
            await self.run_business("second-item", "second-scope")
        self.assertEqual(error.exception.code, "CREATE_RESULT_UNKNOWN")
        self.assertEqual(self.controller.create_business_manager_detailed.await_count, 1)
        self.session.graph_api.assert_not_awaited()

    async def test_cancel_during_attach_preserves_created_id_and_retry_skips_create(self):
        async def created(**kwargs):
            await kwargs["before_submit"]()
            return SimpleNamespace(business_id="555666777888999", response_path="data.bizkit_create_business.id",
                candidate=SimpleNamespace(variables_mode="scope_selector_business_creation_v1"))
        self.controller.create_business_manager_detailed.side_effect = created
        with patch("app.business_create_service.set_business_primary_page", new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_business()
        saved = await self.state.step("audit-item", ProvisioningStep.BUSINESS)
        self.assertEqual(saved["result"]["business_id"], "555666777888999")
        result = await self.run_business()
        self.assertEqual(result["business_id"], "555666777888999")
        self.assertTrue(result["resumed"])
        self.assertEqual(self.controller.create_business_manager_detailed.await_count, 1)

    async def test_empty_before_snapshot_can_reconcile_across_jobs(self):
        await self.state.set_running("previous", "audit-profile", "old-scope", ProvisioningStep.BUSINESS)
        await self.state.checkpoint("previous", "audit-profile", "old-scope", ProvisioningStep.BUSINESS,
            {"phase": "CREATE_SUBMITTED", "primary_page_id": "123456789", "business_name": "Audit Business", "business_ids_before": []})
        self.browser.reconcile_created_business.return_value = SimpleNamespace(business_id="555666777888999")
        result = await self.run_business()
        self.assertEqual(result["business_id"], "555666777888999")
        self.browser.reconcile_created_business.assert_awaited_once_with(before_ids=[], business_name="Audit Business")
        self.controller.create_business_manager_detailed.assert_not_awaited()

    async def test_private_transport_send_state_controls_retry_without_official_api(self):
        for sent, expected in [(False, "CREATE_BM_PRE_SUBMIT_TRANSPORT"), (True, "CREATE_RESULT_UNKNOWN")]:
            self.controller.create_business_manager_detailed.side_effect = RemoteRequestError("transport failed", request_may_have_been_sent=sent)
            with self.assertRaises(BusinessCreateError) as error:
                await create_business_resilient(self.session, business_name="Audit", page_id="123456789", user_email="owner@example.com", require_page_backed=True)
            self.assertEqual(error.exception.code, expected)
            self.assertEqual(error.exception.retryable, not sent)
        self.session.graph_api.assert_not_awaited()

    async def test_ad_account_reconcile_does_not_read_official_token(self):
        found, evidence = await _reconcile_existing(self.session, business_id="123456789", account_name="Audit")
        self.assertEqual(found, "")
        self.assertEqual(evidence[0]["result"], "unavailable")
        self.session.graph_api.assert_not_awaited()

    async def test_private_mutation_forwards_gate_to_http_transport(self):
        gate = AsyncMock()
        native = AsyncMock(return_value={"data": {"bizkit_create_business": {"id": "555666777888999"}}})
        session = SimpleNamespace(profile=SimpleNamespace(name="audit-profile"),
            bootstrap=AsyncMock(return_value=SimpleNamespace(actor_id="123456789", request_context={})),
            graphql=native)
        with patch("app.facebook_business_create.discover_current_scope_selector_create_candidate", new=AsyncMock(return_value=None)), patch("app.facebook_business_create.list_candidates", return_value=[]), patch("app.facebook_business_create.upsert_candidate"):
            await create_business_with_docids(session, business_name="Audit", user_email="owner@example.com",
                manual_doc_id="28057338880523368", before_submit=gate)
        self.assertIs(native.call_args.kwargs["before_submit"], gate)

    async def check_captured_ad_account(self, verified):
        capture_browser = AsyncMock()
        capture_browser.__aenter__.return_value = capture_browser
        capture_browser.__aexit__.return_value = False
        capture_browser.capture_ad_account_create_request.return_value = {
            "created_during_capture": True, "ad_account_id": "987654321"}
        await self.state.set_running("rk-item", "audit-profile", "rk-scope", ProvisioningStep.AD_ACCOUNT)
        with patch("app.provisioning.ad_account_handler.FacebookBusinessBrowser", return_value=capture_browser), patch("app.provisioning.ad_account_handler._reconcile_existing_browser_inventory", new=AsyncMock(return_value=("", {"confirmed_empty": True}))), patch("app.provisioning.ad_account_handler._verify_expected_ad_account_in_business", new=AsyncMock(return_value=(verified, []))) as verify:
            result = await ad_account_handler(self.session,
                {"business_id": "123456789", "name": "Audit RK", "currency": "USD", "timezone_id": 1}, {},
                provisioning_state=self.state, item_id="rk-item", profile_id="audit-profile", scope_key="rk-scope")
        self.assertEqual(verify.call_args.kwargs["expected_ad_account_id"], "act_987654321")
        self.assertEqual(verify.call_args.kwargs["business_id"], "123456789")
        return result

    async def test_capture_id_not_in_selected_business_cannot_succeed(self):
        with self.assertRaises(ProvisioningError) as error:
            await self.check_captured_ad_account(False)
        self.assertEqual(error.exception.code, "CREATE_RESULT_UNKNOWN")
        saved = await self.state.step("rk-item", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["phase"], "CREATE_RESULT_UNKNOWN")
        snapshot = await self.state.snapshot("audit-profile", "rk-scope")
        self.assertFalse(snapshot.ad_account_id)

    async def test_capture_id_verified_in_selected_business_can_succeed(self):
        result = await self.check_captured_ad_account(True)
        self.assertEqual(result["ad_account_id"], "act_987654321")
        snapshot = await self.state.snapshot("audit-profile", "rk-scope")
        self.assertEqual(snapshot.ad_account_id, "act_987654321")

    async def test_old_graph_empty_evidence_cannot_shorten_browser_proof(self):
        old = [{"stage": "inventory", "result": "ok", "count": 0}] * 3
        with patch("app.provisioning.ad_account_handler._reconcile_existing_browser_inventory", new=AsyncMock(return_value=("", {"confirmed_empty": True}))) as inventory:
            _, proven, proof = await _prove_empty_after_uncertainty(self.session, business_id="123456789", account_name="Audit", initial_graph_diagnostics=old, delay_seconds=0)
        self.assertTrue(proven)
        self.assertEqual(inventory.await_count, 3)
        self.assertEqual(proof["proof_path"], "browser_consensus")

    async def test_retryable_flag_survives_snapshot_restore(self):
        job, _ = await self.jobs.create_job(REQUEST)
        item = (await self.jobs.queued_item_ids(job))[0]
        task = (await self.jobs.tasks(item))[0]
        await self.jobs.set_task_failed(task["id"], "TRANSIENT_AUDIT_ERROR", "temporary", retryable=True)
        await self.jobs.finalize_item(item)
        snapshot = await self.jobs.job_view(job)
        restored = JobStore(str(Path(self.tmp.name) / "restored.sqlite"))
        await restored.init()
        await restored.import_snapshots([snapshot])
        self.assertTrue((await restored.item(item))["retryable"])
        self.assertTrue((await restored.tasks(item))[0]["retryable"])
        self.assertEqual(await restored.retry_failed(job), 1)
        self.assertEqual((await restored.tasks(item))[0]["status"], "QUEUED")

    async def test_duplicate_queue_consumers_only_resolve_once(self):
        job, _ = await self.jobs.create_job(REQUEST)
        item = (await self.jobs.queued_item_ids(job))[0]
        pool = WorkerPool(self.jobs)
        async def fail_context(profile):
            await asyncio.sleep(0.02)
            raise ProfileContextError("resolver unavailable", retryable=True)
        pool.resolver.resolve = AsyncMock(side_effect=fail_context)
        await asyncio.gather(pool._run_item(item), pool._run_item(item))
        self.assertEqual(pool.resolver.resolve.await_count, 1)

    async def test_concurrent_idempotent_job_creation_returns_one_job(self):
        results = await asyncio.gather(*(self.jobs.create_job(REQUEST) for _ in range(8)))
        self.assertEqual(len({job for job, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)

    async def test_partial_fan_page_batch_is_available_to_later_jobs(self):
        await self.state.set_running("partial", "audit-profile", "pages-scope", ProvisioningStep.FAN_PAGES)
        await self.state.checkpoint("partial", "audit-profile", "pages-scope", ProvisioningStep.FAN_PAGES,
            {"created_pages": [{"id": "123456789", "name": "Already Created", "attached": True, "business_id": "999999999"}],
             "phase": "PAGE_CREATE_RESULT_UNKNOWN", "active_page_name": "Unconfirmed"})
        pages = await self.state.latest_profile_fan_pages("audit-profile")
        self.assertEqual(pages[0]["id"], "123456789")
        self.assertEqual(pages[0]["business_id"], "999999999")
        uncertain = await self.state.latest_uncertain_fan_page("audit-profile", "unconfirmed", exclude_item_id="new")
        self.assertEqual(uncertain["item_id"], "partial")

    async def test_new_fan_page_job_cannot_repeat_uncertain_old_create(self):
        await self.state.set_running("old-page", "audit-profile", "old", ProvisioningStep.FAN_PAGES)
        await self.state.checkpoint("old-page", "audit-profile", "old", ProvisioningStep.FAN_PAGES,
            {"phase": "PAGE_CREATE_RESULT_UNKNOWN", "active_page_name": "Audit Page", "active_before_ids": []})
        await self.state.set_running("new-page", "audit-profile", "new", ProvisioningStep.FAN_PAGES)
        with patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(return_value=[])), patch("app.provisioning.fan_pages_handler._reconcile_uncertain_page", new=AsyncMock(return_value=(None, False, []))), patch("app.provisioning.fan_pages_handler.FacebookBusinessBrowser") as browser:
            with self.assertRaises(ProvisioningError) as error:
                await fan_pages_handler(self.session, {"names": ["Audit Page"]}, {}, provisioning_state=self.state,
                    item_id="new-page", profile_id="audit-profile", scope_key="new")
        self.assertEqual(error.exception.code, "PAGE_CREATE_RESULT_UNKNOWN")
        browser.assert_not_called()

    async def test_delayed_page_hydration_uses_one_navigation(self):
        page = SimpleNamespace(content=AsyncMock(side_effect=["<html>shell</html>", "<html>shell</html>",
            '<script type="application/json">{"__typename":"Page","id":"123456789","name":"Delayed Page","is_owned":true}</script>']),
            wait_for_timeout=AsyncMock(), locator=lambda selector: SimpleNamespace(evaluate_all=AsyncMock(return_value=[])))
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="audit-profile"))
        browser.page = page
        browser._goto = AsyncMock()
        browser._assert_authenticated = AsyncMock()
        result = await browser.discover_managed_pages(fast=True)
        self.assertEqual(result[0]["id"], "123456789")
        self.assertEqual(browser._goto.await_count, 1)
        self.assertEqual(browser._goto.call_args.kwargs["wait_until"], "commit")


if __name__ == "__main__":
    unittest.main()
