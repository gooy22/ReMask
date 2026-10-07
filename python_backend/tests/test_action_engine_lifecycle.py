import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore


class ActionEngineLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(
            str(Path(self.tmp.name) / "action-engine.sqlite")
        )
        await self.state.init()
        self.context = SimpleNamespace(
            profile_id="7",
            proxy=None,
            user_agent="test",
        )
        self.session = SimpleNamespace()

    async def test_business_runs_precheck_execute_verify_commit(self):
        phases = []
        original_checkpoint = self.state.checkpoint

        async def checkpoint(*args, **kwargs):
            patch_value = args[4] if len(args) > 4 else kwargs.get("patch", {})
            if isinstance(patch_value, dict) and patch_value.get("action_phase"):
                phases.append(patch_value["action_phase"])
            return await original_checkpoint(*args, **kwargs)

        self.state.checkpoint = checkpoint

        async def handler(session, params, state, **kwargs):
            return {
                "business_id": "111111111111111",
                "phase": "CREATE_CONFIRMED",
            }

        with patch(
            "app.provisioning.service.get_handler",
            return_value=handler,
        ), patch(
            "app.provisioning.service._await_profile_mutation_cooldown",
            new=AsyncMock(),
        ):
            result = await ProvisioningService(self.state).run(
                item_id="item-1",
                profile_id="7",
                context=self.context,
                session=self.session,
                payload={
                    "steps": ["BUSINESS"],
                    "scope_key": "scope-1",
                    "parameters": {"BUSINESS": {"name": "BM One"}},
                },
            )

        self.assertEqual(
            phases,
            ["PRECHECK", "EXECUTE", "VERIFY"],
        )
        step = await self.state.step(
            "item-1",
            ProvisioningStep.BUSINESS,
        )
        self.assertEqual(step["status"], "SUCCESS")
        self.assertEqual(step["result"]["action_phase"], "COMMIT")
        self.assertEqual(step["result"]["action_contract_version"], 1)
        self.assertEqual(
            result["steps"][0]["result"]["action_phase"],
            "COMMIT",
        )

    async def test_action_receives_transport_router_and_records_policy(self):
        observed = {}

        async def handler(session, params, state, **kwargs):
            router = kwargs.get("meta_transport")
            observed["router"] = router
            observed["policy"] = router.policy("BUSINESS")
            return {
                "business_id": "111111111111111",
                "phase": "CREATE_CONFIRMED",
            }

        with patch(
            "app.provisioning.service.get_handler",
            return_value=handler,
        ), patch(
            "app.provisioning.service._await_profile_mutation_cooldown",
            new=AsyncMock(),
        ):
            await ProvisioningService(self.state).run(
                item_id="item-router",
                profile_id="7",
                context=self.context,
                session=self.session,
                payload={
                    "steps": ["BUSINESS"],
                    "scope_key": "scope-router",
                    "parameters": {"BUSINESS": {"name": "BM Router"}},
                },
            )

        self.assertEqual(
            observed["policy"].primary,
            "facebook_web_graphql",
        )
        self.assertEqual(
            observed["policy"].fallback,
            "chromium_business_suite",
        )
        step = await self.state.step(
            "item-router",
            ProvisioningStep.BUSINESS,
        )
        self.assertEqual(
            step["result"]["action_phase"],
            "COMMIT",
        )

    async def test_verify_blocks_wrong_business_relation_before_commit(self):
        async def handler(session, params, state, **kwargs):
            return {
                "ad_account_id": "222222222222222",
                "business_id": "999999999999999",
            }

        with patch(
            "app.provisioning.service.get_handler",
            return_value=handler,
        ), patch(
            "app.provisioning.service._await_profile_mutation_cooldown",
            new=AsyncMock(),
        ):
            with self.assertRaises(ProvisioningError) as caught:
                await ProvisioningService(self.state).run(
                    item_id="item-2",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={
                        "steps": ["AD_ACCOUNT"],
                        "scope_key": "scope-2",
                        "parameters": {
                            "AD_ACCOUNT": {
                                "business_id": "111111111111111",
                            }
                        },
                    },
                )

        self.assertEqual(
            caught.exception.code,
            "VERIFY_AD_ACCOUNT_BUSINESS_MISMATCH",
        )
        step = await self.state.step(
            "item-2",
            ProvisioningStep.AD_ACCOUNT,
        )
        self.assertEqual(step["status"], "FAILED")
        self.assertEqual(step["result"]["action_phase"], "VERIFY")


if __name__ == "__main__":
    unittest.main()
