import unittest
from unittest.mock import AsyncMock, patch

import httpx
import main as api


def prepare_request(*, count=1, action="prepare"):
    profile_id = "31"
    scope = f"prepare:{profile_id}"
    return {
        "action": "create",
        "idempotency_key": "workspace-prepare-test-nonce",
        "profiles": [{
            "profile_id": profile_id,
            "tasks": [{
                "action": action,
                "idempotency_key": scope,
                "payload": {
                    "scope_key": scope,
                    "desired": {
                        "fan_page": True,
                        "business": True,
                        "ad_accounts": count,
                        "page_access": True,
                        "payment": True,
                    },
                    "parameters": {},
                },
            }],
        }],
    }


class BusinessGatePrepareAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def post_job(self, request, terminal_status):
        create = AsyncMock(return_value=("prepare-job", True))
        enqueue = AsyncMock(return_value=1)
        with patch.object(api, "API_KEY", "fixture-worker-key"), \
                patch.object(api, "_terminal_profile_statuses",
                             new=AsyncMock(return_value={"31": terminal_status})), \
                patch.object(api, "_request_fan_page_profile_ids", return_value=[]), \
                patch.object(api.store, "create_job", new=create), \
                patch.object(api.store, "job_view",
                             new=AsyncMock(return_value={"status": "QUEUED", "items_total": 1})), \
                patch.object(api.pool, "enqueue_job", new=enqueue):
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=api.app),
                    base_url="http://fixture",
                    headers={"X-Remask-Worker-Key": "fixture-worker-key"}) as client:
                response = await client.post("/api/v1/jobs", json=request)
        return response, create, enqueue

    async def test_explicit_single_bundle_prepare_reaches_worker_under_business_gate(self):
        response, create, enqueue = await self.post_job(
            prepare_request(),
            {"status": "BUSINESS_LOGIN_GATE", "message": "saved gate"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["blocked_profiles"], [])
        create.assert_awaited_once()
        enqueue.assert_awaited_once_with("prepare-job")

    async def test_checkpoint_still_blocks_prepare_before_job_creation(self):
        response, create, enqueue = await self.post_job(
            prepare_request(),
            {"status": "CHECKPOINT", "message": "checkpoint"},
        )
        self.assertEqual(response.status_code, 409)
        create.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_other_job_shapes_do_not_bypass_business_gate(self):
        response, create, enqueue = await self.post_job(
            prepare_request(action="provisioning"),
            {"status": "BUSINESS_LOGIN_GATE", "message": "saved gate"},
        )
        self.assertEqual(response.status_code, 409)
        create.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_two_bundle_request_does_not_bypass_business_gate(self):
        response, create, enqueue = await self.post_job(
            prepare_request(count=2),
            {"status": "BUSINESS_LOGIN_GATE", "message": "saved gate"},
        )
        self.assertEqual(response.status_code, 409)
        create.assert_not_awaited()
        enqueue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
