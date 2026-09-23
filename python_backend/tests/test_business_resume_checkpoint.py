import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.facebook_business_create import BusinessMutationError
from app.provisioning.business_handler import business_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.store import JobStore


class _Controller:
    def __init__(self) -> None:
        self.create_calls = 0
        self.attach_calls = 0
        self.fail_first_attach = True

    async def create_business_manager_v2(self, *, params, profile_id=""):
        self.create_calls += 1
        return SimpleNamespace(
            business_id="555666777888999",
            candidate=SimpleNamespace(
                doc_id="9988776655443322",
                friendly_name="useBusinessCreationMutationMutation",
                variables_mode="scope_selector_business_creation_v1",
                source="dynamic_success",
            ),
        )

    async def attach_page_to_business(
        self,
        *,
        business_id,
        business_name,
        page_id,
        profile_id="",
    ):
        self.attach_calls += 1

        if self.fail_first_attach:
            self.fail_first_attach = False
            raise BusinessMutationError(
                "SET_PRIMARY_PAGE_META_ERROR",
                "temporary attach failure",
                retryable=True,
            )

        return SimpleNamespace(
            candidate=SimpleNamespace(
                doc_id="8877665544332211",
                friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
                variables_mode="bizkit_settings_update_business_basic_info_v1",
                source="dynamic_success",
            )
        )


class _Session:
    def __init__(self, controller: _Controller) -> None:
        self.controller = controller
        self.context = SimpleNamespace(
            profile_id="profile-1",
            email="owner@example.com",
            first_name="Test",
            last_name="Owner",
            display_name="Test Owner",
        )

    async def facebook_controller(self):
        return self.controller


class BusinessResumeCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "jobs.sqlite3")
        self.state_store = ProvisioningStateStore(self.db_path)
        await self.state_store.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_retry_resumes_attach_without_second_create(self):
        item_id = "item-1"
        profile_id = "profile-1"
        scope_key = "scope-1"

        await self.state_store.set_running(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.BUSINESS,
        )

        controller = _Controller()
        session = _Session(controller)
        snapshot = await self.state_store.snapshot(profile_id, scope_key)

        with self.assertRaises(ProvisioningError) as raised:
            await business_handler(
                session,
                {
                    "name": "Test Business",
                    "page_id": "123456789",
                    "user_email": "owner@example.com",
                },
                snapshot.as_dict(),
                provisioning_state=self.state_store,
                item_id=item_id,
                profile_id=profile_id,
                scope_key=scope_key,
                step_state=None,
            )

        self.assertEqual(
            raised.exception.code,
            "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
        )
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(controller.create_calls, 1)
        self.assertEqual(controller.attach_calls, 1)

        await self.state_store.fail(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.BUSINESS,
            raised.exception.code,
            str(raised.exception),
        )

        failed_step = await self.state_store.step(
            item_id,
            ProvisioningStep.BUSINESS,
        )
        self.assertIsNotNone(failed_step)
        self.assertEqual(
            failed_step["result"]["business_id"],
            "555666777888999",
        )
        self.assertEqual(
            failed_step["result"]["resume_from"],
            "ATTACH_PAGE",
        )

        await self.state_store.set_running(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.BUSINESS,
        )

        retry_snapshot = await self.state_store.snapshot(
            profile_id,
            scope_key,
        )

        result = await business_handler(
            session,
            {
                "name": "Test Business",
                "page_id": "123456789",
                "user_email": "owner@example.com",
            },
            retry_snapshot.as_dict(),
            provisioning_state=self.state_store,
            item_id=item_id,
            profile_id=profile_id,
            scope_key=scope_key,
            step_state=failed_step,
        )

        self.assertEqual(result["business_id"], "555666777888999")
        self.assertTrue(result["resumed"])
        self.assertEqual(result["resume_from"], "DONE")
        self.assertEqual(controller.create_calls, 1)
        self.assertEqual(controller.attach_calls, 2)

        await self.state_store.complete(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.BUSINESS,
            result,
        )

        final_snapshot = await self.state_store.snapshot(
            profile_id,
            scope_key,
        )
        self.assertEqual(
            final_snapshot.business_id,
            "555666777888999",
        )


class _JobTask:
    action = "provisioning"
    payload = {"steps": ["BUSINESS"]}
    idempotency_key = "business-task"

    def model_dump(self, **kwargs):
        return {}


class _JobProfile:
    profile_id = "profile-1"
    tasks = [_JobTask()]


class _JobRequest:
    idempotency_key = "resume-job"
    profiles = [_JobProfile()]


class JobRetryCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_retry_keeps_provisioning_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "jobs.sqlite3")

            jobs = JobStore(db_path)
            state = ProvisioningStateStore(db_path)

            await jobs.init()
            await state.init()

            job_id, created = await jobs.create_job(_JobRequest())
            self.assertTrue(created)

            item_ids = await jobs.queued_item_ids(job_id)
            self.assertEqual(len(item_ids), 1)
            item_id = item_ids[0]

            tasks = await jobs.tasks(item_id)
            self.assertEqual(len(tasks), 1)
            task_id = tasks[0]["id"]

            await jobs.set_item_running(item_id)
            await jobs.set_task_running(task_id)

            await state.set_running(
                item_id,
                "profile-1",
                "scope-1",
                ProvisioningStep.BUSINESS,
            )
            await state.checkpoint(
                item_id,
                "profile-1",
                "scope-1",
                ProvisioningStep.BUSINESS,
                {
                    "business_id": "555666777888999",
                    "resume_from": "ATTACH_PAGE",
                    "primary_page_id": "123456789",
                },
            )
            await state.fail(
                item_id,
                "profile-1",
                "scope-1",
                ProvisioningStep.BUSINESS,
                "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
                "attach failed",
            )

            await jobs.set_task_failed(
                task_id,
                "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
                "attach failed",
                retryable=True,
            )
            await jobs.finalize_item(item_id)

            retried = await jobs.retry_failed(job_id)
            self.assertEqual(retried, 1)

            preserved = await state.step(
                item_id,
                ProvisioningStep.BUSINESS,
            )
            self.assertIsNotNone(preserved)
            self.assertEqual(
                preserved["result"]["business_id"],
                "555666777888999",
            )
            self.assertEqual(
                preserved["result"]["resume_from"],
                "ATTACH_PAGE",
            )


if __name__ == "__main__":
    unittest.main()
