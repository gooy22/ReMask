import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.facebook_business_create import create_business_with_docids


class _Session:
    def __init__(self):
        self.profile = SimpleNamespace(name="profile-envelope")
        self.calls = []

    async def bootstrap(self):
        return SimpleNamespace(
            actor_id="123456789",
            request_context={"__rev": "111"},
        )

    async def graphql_browser_native(self, doc_id, variables, **kwargs):
        self.calls.append((doc_id, variables, kwargs))
        return {
            "data": {
                "bizkit_create_business": {
                    "id": "555666777888999"
                }
            }
        }


class ExactEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_captured_safe_envelope_is_forwarded_to_browser_graphql(self):
        session = _Session()
        captured = {
            "__rev": "222",
            "__hsi": "333",
            "__dyn": "captured-dyn",
            "__csr": "captured-csr",
            "__req": "a",
            "__comet_req": "11",
        }

        with patch(
            "app.facebook_business_create.discover_current_scope_selector_create_candidate",
            return_value=None,
        ), patch(
            "app.facebook_business_create.list_candidates",
            return_value=[],
        ), patch(
            "app.facebook_business_create.upsert_candidate",
        ):
            result = await create_business_with_docids(
                session,
                business_name="Test Business",
                user_email="owner@example.com",
                manual_doc_id="28057338880523368",
                qpl_join_id="11111111-2222-4333-8444-555555555555",
                request_envelope=captured,
                profile_id="profile-envelope",
            )

        self.assertEqual(result.business_id, "555666777888999")
        self.assertEqual(len(session.calls), 1)
        _, variables, kwargs = session.calls[0]
        self.assertEqual(kwargs["request_envelope"], captured)
        self.assertEqual(
            variables["input"]["qpl_join_id"],
            "11111111-2222-4333-8444-555555555555",
        )


if __name__ == "__main__":
    unittest.main()
