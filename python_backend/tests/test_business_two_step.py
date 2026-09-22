import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.business_create_service import create_business_resilient


class _WebController:
    def __init__(self):
        self.session = SimpleNamespace()

    async def create_business_manager_detailed(self, **kwargs):
        return SimpleNamespace(
            business_id="555666777888999",
            candidate=SimpleNamespace(
                variables_mode="scope_selector_business_creation_v1",
                doc_id="10024830640911292",
                friendly_name="useBusinessCreationMutationMutation",
                source="runtime_test",
            ),
            response_path="data.bizkit_create_business.id",
        )


class _Session:
    def __init__(self):
        self.context = SimpleNamespace(access_token="")
        self.controller = _WebController()

    async def facebook_controller(self):
        return self.controller


class TwoStepBusinessCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_selector_create_then_primary_page_attach(self):
        session = _Session()
        attach_candidate = SimpleNamespace(
            doc_id="7893672220672612",
            friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
            source="runtime_test",
        )

        with patch(
            "app.facebook_business_create.set_business_primary_page",
            new=AsyncMock(return_value=attach_candidate),
        ) as attach:
            result = await create_business_resilient(
                session,
                business_name="Test Business",
                page_id="123456789",
                user_email="owner@example.com",
                require_page_backed=True,
            )

        self.assertEqual(result.business_id, "555666777888999")
        self.assertEqual(result.primary_page_id, "123456789")
        self.assertEqual(
            result.transport,
            "facebook_web_graphql_scope_selector_plus_primary_page",
        )
        attach.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
