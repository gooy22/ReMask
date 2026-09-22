import unittest
from types import SimpleNamespace

from app.business_create_service import create_business_resilient


class _WebController:
    async def create_business_manager_detailed(self, **kwargs):
        return SimpleNamespace(
            business_id="123456789012345",
            candidate=SimpleNamespace(
                variables_mode="legacy_primary_page_v1",
                doc_id="987654321012345",
                friendly_name="BusinessManagerCreateMutation",
                source="test_runtime",
            ),
            response_path="data.business_manager_create.business.id",
        )


class _PrivateFirstSession:
    def __init__(self):
        self.context = SimpleNamespace(access_token="token-present")
        self.graph_touched = False

    async def facebook_controller(self):
        return _WebController()

    async def graph_api(self):
        self.graph_touched = True
        raise AssertionError("official Graph API must not run before private Add BM")


class PrivateBusinessRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_backed_add_bm_is_private_first(self):
        session = _PrivateFirstSession()
        result = await create_business_resilient(
            session,
            business_name="Test Business",
            page_id="123456789",
            require_page_backed=True,
        )
        self.assertEqual(result.business_id, "123456789012345")
        self.assertEqual(result.transport, "facebook_web_graphql_page_backed")
        self.assertFalse(session.graph_touched)


if __name__ == "__main__":
    unittest.main()
