import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import facebook_docids
from app.facebook_business_create import create_business_with_docids
from app.facebook_docids import (
    DocIdCandidate,
    list_candidates,
    record_result,
    upsert_candidate,
)
from app.facebook_query_discovery import discover_persisted_query


class _HtmlOnlySession:
    def __init__(self, body="", headers=None):
        self.body = body
        self.headers = headers or {}
        self.calls = []

    async def fetch_text_with_headers(self, url, **kwargs):
        self.calls.append(url)
        return 200, self.body, url, dict(self.headers)


class HtmlOnlyDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_from_initial_html_without_bundle_fetch(self):
        session = _HtmlOnlySession(
            body=(
                '{"useBusinessCreationMutationMutation_facebookRelayOperation":'
                '{"id":"9988776655443322"}}'
            )
        )
        result = await discover_persisted_query(
            session,
            friendly_name="useBusinessCreationMutationMutation",
            entry_urls=["https://www.facebook.com/"],
            cache_ttl_seconds=0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.doc_id, "9988776655443322")
        self.assertEqual(result.source_kind, "html")
        self.assertEqual(session.calls, ["https://www.facebook.com/"])

    async def test_discovers_from_response_headers(self):
        session = _HtmlOnlySession(
            body="<html></html>",
            headers={
                "X-ReMask-Test": (
                    'useBusinessCreationMutationMutation '
                    'doc_id="8877665544332211"'
                )
            },
        )
        result = await discover_persisted_query(
            session,
            friendly_name="useBusinessCreationMutationMutation",
            entry_urls=["https://business.facebook.com/latest/home"],
            cache_ttl_seconds=0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.doc_id, "8877665544332211")
        self.assertEqual(result.source_kind, "response_headers")


class CrossProfileInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_patch = patch.object(
            facebook_docids,
            "STORE_PATH",
            Path(self.tmp.name) / "docids.json",
        )
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.tmp.cleanup()

    def _candidate(self):
        return upsert_candidate(
            "CREATE_BM",
            doc_id="7766554433221100",
            friendly_name="useBusinessCreationMutationMutation",
            variables_mode="scope_selector_business_creation_v1",
            source="runtime_html",
        )

    def test_same_profile_cannot_invalidate_shared_docid(self):
        candidate = self._candidate()
        for _ in range(5):
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason="1357054",
                profile_id="profile-1",
                stale_failure=True,
            )
        ids = [item.doc_id for item in list_candidates("CREATE_BM")]
        self.assertIn(candidate.doc_id, ids)

    def test_three_distinct_profiles_invalidate_cached_docid(self):
        candidate = self._candidate()
        for profile_id in ("profile-1", "profile-2"):
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason="1357054",
                profile_id=profile_id,
                stale_failure=True,
            )
        self.assertIn(
            candidate.doc_id,
            [item.doc_id for item in list_candidates("CREATE_BM")],
        )

        record_result(
            "CREATE_BM",
            candidate,
            success=False,
            reason="1357054",
            profile_id="profile-3",
            stale_failure=True,
        )
        self.assertNotIn(
            candidate.doc_id,
            [item.doc_id for item in list_candidates("CREATE_BM")],
        )

    def test_success_resets_cross_profile_stale_sequence(self):
        candidate = self._candidate()
        for profile_id in ("profile-1", "profile-2"):
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason="1357054",
                profile_id=profile_id,
                stale_failure=True,
            )

        record_result(
            "CREATE_BM",
            candidate,
            success=True,
            response_path="data.bizkit_create_business.id",
            profile_id="profile-2",
        )

        record_result(
            "CREATE_BM",
            candidate,
            success=False,
            reason="1357054",
            profile_id="profile-3",
            stale_failure=True,
        )
        self.assertIn(
            candidate.doc_id,
            [item.doc_id for item in list_candidates("CREATE_BM")],
        )


class _ManualSession:
    def __init__(self):
        self.profile = SimpleNamespace(name="profile-manual")
        self.used_doc_ids = []

    async def bootstrap(self):
        return SimpleNamespace(actor_id="123456789")

    async def graphql(self, doc_id, variables, **kwargs):
        self.used_doc_ids.append(doc_id)
        return {
            "data": {
                "bizkit_create_business": {
                    "id": "555666777888999"
                }
            }
        }


class ManualFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_docid_used_when_dynamic_discovery_empty(self):
        session = _ManualSession()
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
                explicit_doc_id="6655443322110099",
                allow_scope_selector_fallback=True,
            )

        self.assertEqual(result.business_id, "555666777888999")
        self.assertEqual(session.used_doc_ids, ["6655443322110099"])
        self.assertEqual(result.candidate.source, "job_manual")


if __name__ == "__main__":
    unittest.main()
