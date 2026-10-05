import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import aiohttp
from pathlib import Path

from app.store import JobStore
from app.provisioning.state import ProvisioningStateStore
from app.runner import _await_with_hard_watchdog
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.service import ProvisioningService
from app.session import ProfileContextError, ProfileResolver


class JobStoreRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_common_page_picker_failure_can_resume_without_submit(self):
        job,item=self._seed(task_status='FAILED'); task=(await self.store.tasks(item))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',
                (json.dumps({'parameters':{'FAN_PAGES':{'common_page':True}}}),task['id']))
        await self.provisioning_state.set_running(item,'4','default',ProvisioningStep.PAGE_ACCESS)
        await self.provisioning_state.checkpoint(item,'4','default',ProvisioningStep.PAGE_ACCESS,
            {'phase':'PAGE_ADD_NOT_SUBMITTED','diagnostic':{'stage':'page_add_submit_missing','result_selected':False}})
        await self.provisioning_state.fail(item,'4','default',ProvisioningStep.PAGE_ACCESS,'PAGE_ADD_UI_CHANGED','picker')
        await self.store.set_task_failed(task['id'],'PAGE_ADD_UI_CHANGED','Meta Page-add review/submit action was not found.')
        await self.store.finalize_item(item)
        old=await self.store.job_view(job)
        await self.store.init()
        await self.store.import_snapshots([old])
        self.assertEqual(await self.store.queued_item_ids(job),[])
        self.assertEqual(await self.store.retry_failed(job),1)

    async def test_completed_act_prefix_rk_restores_access_retry_without_recreation(self):
        job,item=self._seed(task_status='FAILED'); task=(await self.store.tasks(item))[0]
        await self.provisioning_state.complete(item,'4','default',ProvisioningStep.BUSINESS,{'business_id':'934505709362142'})
        await self.provisioning_state.complete(item,'4','default',ProvisioningStep.AD_ACCOUNT,
            {'business_id':'934505709362142','ad_account_id':'act_1152836437079070'})
        await self.store.set_task_failed(task['id'],'CREATED_BUSINESS_RK_REQUIRED','prefix rejected')
        await self.store.finalize_item(item)
        await self.store.init()
        self.assertEqual(await self.store.queued_item_ids(job),[])
        self.assertEqual(await self.store.retry_failed(job),1)
        rk=await self.provisioning_state.step(item,ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(rk['status'],'SUCCESS')
        self.assertEqual(rk['result']['ad_account_id'],'act_1152836437079070')

    async def test_common_page_missing_checkpoint_repair_preserves_job_and_does_not_enqueue(self):
        job_id,item_id=self._seed(task_status='FAILED')
        task=(await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',
                (json.dumps({'steps':['FAN_PAGES'],'parameters':{'FAN_PAGES':{'common_page':True}}}),task['id']))
        await self.store.set_task_failed(task['id'],'TASK_FAILED',
            'cannot checkpoint FAN_PAGES: step row does not exist for item workspace-common-page-9')
        await self.store.finalize_item(item_id)
        await self.store.init()
        view=await self.store.job_view(job_id)
        self.assertEqual(view['id'],job_id)
        self.assertEqual(view['status'],'FAILED')
        self.assertTrue(view['items'][0]['retryable'])
        self.assertEqual(await self.store.queued_item_ids(job_id),[])
        self.assertEqual(await self.store.retry_failed(job_id),1)

    async def test_legacy_fp_crash_restores_same_job_retry_and_preserves_submit_intent(self):
        job_id, item_id = self._seed(task_status='FAILED')
        task = (await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',
                (json.dumps({'steps':['FAN_PAGES']}), task['id']))
        await self.store.set_task_failed(task['id'], 'TASK_FAILED', 'Page.wait_for_timeout: Page crashed')
        await self.store.finalize_item(item_id)
        await self.provisioning_state.set_running(item_id, '4', 'default', ProvisioningStep.FAN_PAGES)
        await self.provisioning_state.checkpoint(item_id, '4', 'default', ProvisioningStep.FAN_PAGES,
            {'phase':'PAGE_CREATE_CLICK_INTENT', 'active_page_name':'ReMask Page', 'active_before_ids':['123456789']})
        old_view = await self.store.job_view(job_id)
        await self.store.init()
        # A restored mirror must not overwrite the repaired retryability.
        await self.store.import_snapshots([old_view])
        view = await self.store.job_view(job_id)
        self.assertEqual(view['id'], job_id)
        self.assertEqual(view['status'], 'FAILED')
        self.assertTrue(view['items'][0]['retryable'])
        self.assertEqual(view['items'][0]['tasks'][0]['error_code'], 'BROWSER_PAGE_CRASHED')
        self.assertEqual(await self.store.retry_failed(job_id), 1)
        checkpoint = await self.provisioning_state.step(item_id, ProvisioningStep.FAN_PAGES)
        self.assertEqual(checkpoint['result']['phase'], 'PAGE_CREATE_CLICK_INTENT')
        self.assertEqual(checkpoint['result']['active_before_ids'], ['123456789'])

    async def test_legacy_crash_repair_does_not_make_other_task_failures_retryable(self):
        job_id, item_id = self._seed(task_status='FAILED')
        task = (await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',
                (json.dumps({'steps':['BUSINESS']}), task['id']))
        await self.store.set_task_failed(task['id'], 'TASK_FAILED', 'Page.wait_for_timeout: Page crashed')
        await self.store.finalize_item(item_id)
        await self.store.init()
        self.assertEqual(await self.store.retry_failed(job_id), 0)

    def test_renderer_crash_is_resumable_while_unrelated_failure_is_not(self):
        crash = ProvisioningService._classify(RuntimeError('Page.wait_for_timeout: Page crashed'))
        self.assertEqual(crash.code, 'BROWSER_PAGE_CRASHED')
        self.assertTrue(crash.retryable)
        target = ProvisioningService._classify(RuntimeError('Locator.count: Target crashed'))
        self.assertEqual(target.code, 'BROWSER_PAGE_CRASHED')
        self.assertTrue(target.retryable)
        self.assertFalse(ProvisioningService._classify(RuntimeError('Invalid task')).retryable)
        closed=ProvisioningService._classify(RuntimeError('unable to perform operation on <WriteUnixTransport closed=True>; the handler is closed'))
        self.assertEqual(closed.code,'BROWSER_CONNECTION_CLOSED'); self.assertTrue(closed.retryable)

    async def test_closed_fp_transport_restores_retry_without_changing_confirm_intent(self):
        job_id,item_id=self._seed(task_status='FAILED')
        task=(await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',(json.dumps({'steps':['FAN_PAGES']}),task['id']))
        await self.store.set_task_failed(task['id'],'TASK_FAILED','unable to perform operation on <WriteUnixTransport closed=True>; the handler is closed')
        await self.store.finalize_item(item_id)
        await self.provisioning_state.set_running(item_id,'4','default',ProvisioningStep.FAN_PAGES)
        await self.provisioning_state.checkpoint(item_id,'4','default',ProvisioningStep.FAN_PAGES,
            {'phase':'PAGE_CONFIRM_CLICK_INTENT','confirm_page_id':'222222222','created_pages':[{'id':'222222222','name':'Page'}]})
        old_view=await self.store.job_view(job_id); await self.store.init(); await self.store.import_snapshots([old_view])
        view=await self.store.job_view(job_id)
        self.assertEqual(view['items'][0]['error_code'],'BROWSER_CONNECTION_CLOSED')
        self.assertEqual(await self.store.retry_failed(job_id),1)
        saved=await self.provisioning_state.step(item_id,ProvisioningStep.FAN_PAGES)
        self.assertEqual(saved['result']['phase'],'PAGE_CONFIRM_CLICK_INTENT')
        self.assertEqual(saved['result']['confirm_page_id'],'222222222')

    async def test_diagnostic_hydration_failure_restores_same_confirm_job(self):
        job_id,item_id=self._seed(task_status='FAILED')
        task=(await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',(json.dumps({'steps':['FAN_PAGES']}),task['id']))
        await self.store.set_task_failed(task['id'],'TASK_FAILED',"Page.evaluate: TypeError: Failed to execute 'createTreeWalker' on 'Document': parameter 1 is not of type 'Node'.")
        await self.store.finalize_item(item_id)
        await self.store.init()
        self.assertEqual((await self.store.job_view(job_id))['items'][0]['error_code'],'BROWSER_DIAGNOSTIC_FAILED')
        self.assertEqual(await self.store.retry_failed(job_id),1)

    async def test_target_crash_restores_same_confirm_job(self):
        job_id,item_id=self._seed(task_status='FAILED')
        task=(await self.store.tasks(item_id))[0]
        with self.store._connect() as con:
            con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',(json.dumps({'steps':['FAN_PAGES']}),task['id']))
        await self.store.set_task_failed(task['id'],'TASK_FAILED','Locator.count: Target crashed')
        await self.store.finalize_item(item_id)
        await self.store.init()
        self.assertEqual((await self.store.job_view(job_id))['items'][0]['error_code'],'BROWSER_PAGE_CRASHED')
        self.assertEqual(await self.store.retry_failed(job_id),1)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "jobs.sqlite3")
        self.store = JobStore(db_path)
        self.provisioning_state = ProvisioningStateStore(db_path)
        await self.store.init()
        await self.provisioning_state.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _seed(self, *, task_status: str) -> tuple[str, str]:
        now = int(time.time())
        job_id = "job-test-12345678"
        item_id = "item-test-12345678"
        task_id = "task-test-12345678"
        with self.store._connect() as con:
            con.execute(
                "INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)",
                (job_id, "RUNNING", "idem-test", now, now),
            )
            con.execute(
                "INSERT INTO job_items(id,job_id,profile_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (item_id, job_id, "4", "RUNNING", now, now),
            )
            con.execute(
                """INSERT INTO job_tasks(
                    id,item_id,position,action,payload_json,idempotency_key,status,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    item_id,
                    0,
                    "provisioning",
                    "{}",
                    "task-idem",
                    task_status,
                    now,
                    now,
                ),
            )
        return job_id, item_id

    async def test_finalize_does_not_promote_running_task_to_success(self):
        job_id, item_id = self._seed(task_status="RUNNING")

        await self.store.finalize_item(item_id)

        item = await self.store.item(item_id)
        job = await self.store.job_view(job_id)
        self.assertEqual(item["status"], "RUNNING")
        self.assertEqual(job["status"], "RUNNING")

    async def test_recover_requeues_interrupted_running_task(self):
        job_id, item_id = self._seed(task_status="RUNNING")

        await self.store.finalize_item(item_id)
        recovered = await self.store.recover()

        item = await self.store.item(item_id)
        tasks = await self.store.tasks(item_id)
        self.assertIn(item_id, recovered)
        self.assertEqual(item["status"], "QUEUED")
        self.assertEqual(tasks[0]["status"], "QUEUED")

    async def test_hard_watchdog_does_not_wait_for_slow_cancellation(self):
        async def cancellation_resistant():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # Simulate a Playwright coroutine that takes time to unwind
                # after cancellation. The watchdog must return before this
                # cleanup completes.
                await asyncio.sleep(0.20)
                return {"late": True}

        started = time.monotonic()
        with self.assertRaises(ProvisioningError) as ctx:
            await _await_with_hard_watchdog(
                cancellation_resistant(),
                timeout_seconds=0.02,
                code="TEST_HARD_TIMEOUT",
                message="test watchdog",
            )
        elapsed = time.monotonic() - started

        self.assertEqual(ctx.exception.code, "TEST_HARD_TIMEOUT")
        self.assertTrue(ctx.exception.retryable)
        self.assertLess(elapsed, 0.12)

        # Let the detached cancellation cleanup finish so the test loop exits
        # without leaving a pending task.
        await asyncio.sleep(0.25)


    async def test_latest_ad_account_resume_skips_guard_only_derivative(self):
        now = int(time.time())
        business_id = "1056638030476027"
        profile_id = "4"

        original_result = {
            "business_id": business_id,
            "phase": "CREATE_RESULT_UNKNOWN",
            "resume_from": "RECONCILE_CREATE",
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
                "state_after": {
                    "signature": (
                        "FORM::/latest/settings/ad_accounts::"
                        "Aucun compte publicitaire ajouté"
                    )
                },
                "graphql_candidates": [],
            },
        }
        guard_result = {
            "business_id": business_id,
            "phase": "CREATE_RESULT_UNKNOWN",
            "resume_from": "RECONCILE_CREATE",
        }

        with self.provisioning_state._connect() as con:
            con.execute(
                """INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,result_json,
                    error_code,error_message,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "item-original",
                    profile_id,
                    "rk-original",
                    "AD_ACCOUNT",
                    "FAILED",
                    1,
                    json.dumps(original_result),
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    "Meta final Create was clicked but unmatched",
                    now - 10,
                    now - 10,
                ),
            )
            con.execute(
                """INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,result_json,
                    error_code,error_message,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "item-guard",
                    profile_id,
                    "rk-guard",
                    "AD_ACCOUNT",
                    "FAILED",
                    1,
                    json.dumps(guard_result),
                    "",
                    (
                        "AD_ACCOUNT_CREATE_RESULT_UNKNOWN · A previous Job may already "
                        "have submitted CREATE for Business "
                        + business_id
                        + ". Inventory does not prove the RK yet, so ReMask will not "
                        "submit a duplicate CREATE."
                    ),
                    now,
                    now,
                ),
            )

        row = await self.provisioning_state.latest_ad_account_resume_for_business(
            profile_id,
            business_id,
        )

        self.assertEqual(row["item_id"], "item-original")
        self.assertEqual(
            row["result"]["browser_diagnostic"]["stage"],
            "ad_account_final_click_unmatched",
        )

    async def test_newer_create_not_submitted_supersedes_older_uncertainty(self):
        now = int(time.time())
        business_id = "1056638030476027"
        profile_id = "4"

        older_uncertain = {
            "business_id": business_id,
            "phase": "CREATE_RESULT_UNKNOWN",
            "resume_from": "RECONCILE_CREATE",
            "activity": "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
            "browser_diagnostic": {
                "stage": "ad_account_final_click_unmatched",
            },
        }
        newer_safe = {
            "business_id": business_id,
            "phase": "CREATE_NOT_SUBMITTED",
            "resume_from": "CREATE",
            "last_error_code": "AD_ACCOUNT_CREATE_UI_CHANGED",
            "browser_diagnostic": {
                "stage": "ad_account_create_submit_missing",
                "final_click_attempted": False,
            },
        }

        with self.provisioning_state._connect() as con:
            for item_id, result, updated_at in (
                ("item-old", older_uncertain, now - 10),
                ("item-new", newer_safe, now),
            ):
                con.execute(
                    """INSERT INTO provisioning_steps(
                        item_id,profile_id,scope_key,step,status,attempt,result_json,
                        error_code,error_message,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item_id,
                        profile_id,
                        "rk-scope",
                        "AD_ACCOUNT",
                        "FAILED",
                        1,
                        json.dumps(result),
                        result.get("last_error_code", ""),
                        "",
                        updated_at,
                        updated_at,
                    ),
                )

        row = await self.provisioning_state.latest_ad_account_resume_for_business(
            profile_id,
            business_id,
        )
        self.assertEqual(row, {})

    async def test_finalize_marks_all_success_tasks_success(self):
        job_id, item_id = self._seed(task_status="SUCCESS")

        await self.store.finalize_item(item_id)

        item = await self.store.item(item_id)
        job = await self.store.job_view(job_id)
        self.assertEqual(item["status"], "SUCCESS")
        self.assertEqual(job["status"], "SUCCESS")


    async def test_retry_failed_recovers_legacy_checkpoint_even_if_old_row_was_nonretryable(self):
        now = int(time.time())
        job_id = "job-checkpoint-legacy"
        item_id = "item-checkpoint-legacy"
        task_id = "task-checkpoint-legacy"
        with self.store._connect() as con:
            con.execute(
                "INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)",
                (job_id, "FAILED", "idem-checkpoint-legacy", now, now),
            )
            con.execute(
                """INSERT INTO job_items(
                    id,job_id,profile_id,status,error_code,error_message,retryable,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    job_id,
                    "6",
                    "FAILED",
                    "CHECKPOINT_REQUIRED",
                    "Facebook requires a checkpoint",
                    0,
                    now,
                    now,
                ),
            )
            con.execute(
                """INSERT INTO job_tasks(
                    id,item_id,position,action,payload_json,idempotency_key,status,
                    error_code,error_message,retryable,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    item_id,
                    0,
                    "provisioning",
                    "{}",
                    "task-checkpoint-legacy",
                    "FAILED",
                    "CHECKPOINT_REQUIRED",
                    "Facebook requires a checkpoint",
                    0,
                    now,
                    now,
                ),
            )

        requeued = await self.store.retry_failed(job_id)
        item = await self.store.item(item_id)
        tasks = await self.store.tasks(item_id)

        self.assertEqual(requeued, 1)
        self.assertEqual(item["status"], "QUEUED")
        self.assertEqual(tasks[0]["status"], "QUEUED")
        self.assertIsNone(tasks[0]["error_code"])

    async def test_manual_retry_requeues_page_add_ui_changed(self):
        now = int(time.time())
        job_id = "job-page-ui-changed"
        item_id = "item-page-ui-changed"
        task_id = "task-page-ui-changed"
        with self.store._connect() as con:
            con.execute(
                "INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)",
                (job_id, "FAILED", "idem-page-ui-changed", now, now),
            )
            con.execute(
                """INSERT INTO job_items(
                    id,job_id,profile_id,status,error_code,error_message,retryable,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    job_id,
                    "7",
                    "FAILED",
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Page-add review/submit action was not found.",
                    0,
                    now,
                    now,
                ),
            )
            con.execute(
                """INSERT INTO job_tasks(
                    id,item_id,position,action,payload_json,idempotency_key,status,
                    error_code,error_message,retryable,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    item_id,
                    0,
                    "provisioning",
                    "{}",
                    "task-page-ui-changed",
                    "FAILED",
                    "PAGE_ADD_UI_CHANGED",
                    "Meta Page-add review/submit action was not found.",
                    0,
                    now,
                    now,
                ),
            )

        requeued = await self.store.retry_failed(job_id)
        item = await self.store.item(item_id)
        tasks = await self.store.tasks(item_id)

        self.assertEqual(requeued, 1)
        self.assertEqual(item["status"], "QUEUED")
        self.assertEqual(tasks[0]["status"], "QUEUED")
        self.assertIsNone(tasks[0]["error_code"])


    async def test_confirmed_business_page_binding_is_restored_for_live_revalidation(self):
        now = int(time.time())
        with self.provisioning_state._connect() as con:
            con.execute(
                """INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,result_json,
                    error_code,error_message,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "item-page-binding",
                    "7",
                    "bm-page-scope",
                    "BUSINESS",
                    "SUCCESS",
                    1,
                    json.dumps(
                        {
                            "phase": "PAGE_CONFIRMED",
                            "business_id": "61594753560938",
                            "business_name": "Test BM",
                            "primary_page_id": "61594993341059",
                        }
                    ),
                    "",
                    "",
                    now,
                    now,
                ),
            )

        rows = await self.provisioning_state.confirmed_business_page_bindings_for_profile(
            "7"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_id"], "61594753560938")
        self.assertEqual(rows[0]["page_id"], "61594993341059")
        self.assertEqual(
            rows[0]["source"],
            "python_worker_business_page_history",
        )




class ProfileResolverRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _payload() -> dict:
        return {
            "cookies": {"c_user": "123", "xs": "session"},
            "user_agent": "Mozilla/5.0",
            "proxy": "http://127.0.0.1:8888",
            "display_name": "Profile Five",
            "pages": [],
        }

    async def test_loopback_transport_recovers_before_job_failure(self):
        calls = {"count": 0}
        payload = self._payload()

        class Response:
            status = 200

            async def json(self, content_type=None):
                return payload

        class RequestContext:
            async def __aenter__(self):
                calls["count"] += 1
                if calls["count"] < 3:
                    raise aiohttp.ClientConnectionError("resolver not listening")
                return Response()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def get(self, *args, **kwargs):
                return RequestContext()

        with patch.dict(
            os.environ,
            {
                "REMASK_PROFILE_RESOLVER_ATTEMPTS": "3",
                "REMASK_PROFILE_RESOLVER_BACKOFF_SECONDS": "0",
            },
            clear=False,
        ), patch("app.session.aiohttp.ClientSession", Client):
            resolver = ProfileResolver(
                "http://127.0.0.1/ajax/pythonProfileContext.php",
                "internal-key",
            )
            context = await resolver.resolve("5")

        self.assertEqual(calls["count"], 3)
        self.assertEqual(context.profile_id, "5")
        self.assertEqual(context.cookies["c_user"], "123")

    async def test_exhausted_transport_error_stays_retryable(self):
        calls = {"count": 0}

        class RequestContext:
            async def __aenter__(self):
                calls["count"] += 1
                raise aiohttp.ClientConnectionError("resolver unavailable")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def get(self, *args, **kwargs):
                return RequestContext()

        with patch.dict(
            os.environ,
            {
                "REMASK_PROFILE_RESOLVER_ATTEMPTS": "2",
                "REMASK_PROFILE_RESOLVER_BACKOFF_SECONDS": "0",
            },
            clear=False,
        ), patch("app.session.aiohttp.ClientSession", Client):
            resolver = ProfileResolver(
                "http://127.0.0.1/ajax/pythonProfileContext.php",
                "internal-key",
            )
            with self.assertRaises(ProfileContextError) as raised:
                await resolver.resolve("5")

        self.assertEqual(calls["count"], 2)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.category, "resolver_transport")
        self.assertIn("ClientConnectionError", str(raised.exception))




class ProfileMutationCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_mutation_has_no_delay(self):
        from app.provisioning import service as service_module

        original = service_module._PROFILE_MUTATION_COOLDOWN_SECONDS
        service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = 8.0
        service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
        try:
            waited = await service_module._await_profile_mutation_cooldown("6")
            self.assertEqual(waited, 0.0)
        finally:
            service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
            service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = original

    async def test_recent_mutation_waits_before_same_profile(self):
        from app.provisioning import service as service_module

        original = service_module._PROFILE_MUTATION_COOLDOWN_SECONDS
        original_sleep = service_module.asyncio.sleep
        waits = []

        async def fake_sleep(seconds):
            waits.append(seconds)

        service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = 8.0
        service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
        service_module._PROFILE_MUTATION_LAST_FINISHED["6"] = service_module.time.monotonic()
        service_module.asyncio.sleep = fake_sleep
        try:
            waited = await service_module._await_profile_mutation_cooldown("6")
            self.assertGreater(waited, 7.0)
            self.assertLessEqual(waited, 8.0)
            self.assertEqual(len(waits), 1)
            self.assertGreater(waits[0], 7.0)
        finally:
            service_module.asyncio.sleep = original_sleep
            service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
            service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = original

    async def test_other_profile_is_not_delayed(self):
        from app.provisioning import service as service_module

        original = service_module._PROFILE_MUTATION_COOLDOWN_SECONDS
        service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = 8.0
        service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
        service_module._PROFILE_MUTATION_LAST_FINISHED["6"] = service_module.time.monotonic()
        try:
            waited = await service_module._await_profile_mutation_cooldown("7")
            self.assertEqual(waited, 0.0)
        finally:
            service_module._PROFILE_MUTATION_LAST_FINISHED.clear()
            service_module._PROFILE_MUTATION_COOLDOWN_SECONDS = original


if __name__ == "__main__":
    unittest.main()
