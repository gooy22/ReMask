import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.business_create_service import create_business_resilient


class _WebController:
    def __init__(self) -> None:
        self.session = SimpleNamespace()

    async def create_business_manager_detailed(self, **kwargs):
        assert kwargs["allow_scope_selector_fallback"] is True
        return SimpleNamespace(
            business_id="555666777888999",
            candidate=SimpleNamespace(
                variables_mode="scope_selector_business_creation_v1",
                doc_id="9988776655443322",
                friendly_name="useBusinessCreationMutationMutation",
                source="runtime_test",
            ),
            response_path="data.bizkit_create_business.id",
        )


class _Session:
    def __init__(self) -> None:
        self.context = SimpleNamespace(access_token="")
        self.controller = _WebController()
        self.graph_touched = False

    async def facebook_controller(self):
        return self.controller

    async def graph_api(self):
        self.graph_touched = True
        raise AssertionError("official Graph API must not run for Add BM")


class TwoStepBusinessCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_selector_create_then_primary_page_attach(self) -> None:
        session = _Session()
        attach_candidate = SimpleNamespace(
            doc_id="8877665544332211",
            friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
            source="runtime_test",
        )

        with patch(
            "app.business_create_service.set_business_primary_page",
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
        self.assertFalse(session.graph_touched)
        attach.assert_awaited_once()

    async def test_created_business_is_not_recreated_when_page_attach_fails(self) -> None:
        from app.business_create_service import BusinessCreateError
        from app.facebook_business_create import DocIdMutationError

        session = _Session()

        with patch(
            "app.business_create_service.set_business_primary_page",
            new=AsyncMock(
                side_effect=DocIdMutationError("current Page mutation failed")
            ),
        ):
            with self.assertRaises(BusinessCreateError) as raised:
                await create_business_resilient(
                    session,
                    business_name="Test Business",
                    page_id="123456789",
                    user_email="owner@example.com",
                    require_page_backed=True,
                )

        self.assertEqual(
            raised.exception.code,
            "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
        )
        self.assertIn("555666777888999", str(raised.exception))
        self.assertFalse(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
