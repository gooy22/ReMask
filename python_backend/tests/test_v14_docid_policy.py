import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import facebook_docids
from app.facebook_business_create import create_business_with_docids
from app.facebook_docids import (
    DocIdCandidate,
    classify_cache_failure,
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
                '{"requireLazy":["RelayPrefetchedStreamCache"],'
                '"__bbox":{"result":{"useBusinessCreationMutationMutation_'
                'facebookRelayOperation":{"id":"9988776655443322"}}}}'
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

    async def test_plain_unscoped_object_is_not_accepted_as_html_discovery(self):
        session = _HtmlOnlySession(
            body=(
                '{"useBusinessCreationMutationMutation_facebookRelayOperation":'
                '{"id":"1111222233334444"}}'
            )
        )
        result = await discover_persisted_query(
            session,
            friendly_name="useBusinessCreationMutationMutation",
            entry_urls=["https://www.facebook.com/"],
            cache_ttl_seconds=0,
        )
        self.assertIsNone(result)

    async def test_discovers_set_primary_page_from_same_relay_block(self):
        session = _HtmlOnlySession(
            body=(
                '{"RelayPrefetchedStreamCache":{"__bbox":{"result":{'
                '"BizKitSettingsUpdateBusinessBasicInfoMutation_'
                'facebookRelayOperation":{"id":"7788990011223344"}}}}}'
            )
        )
        result = await discover_persisted_query(
            session,
            friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
            entry_urls=["https://business.facebook.com/latest/home"],
            cache_ttl_seconds=0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.doc_id, "7788990011223344")
        self.assertEqual(result.source_kind, "html")

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
                reason="1357054 PersistedQueryNotFound",
                profile_id="profile-1",
                failure_kind="stale_schema",
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
                reason="1357054 PersistedQueryNotFound",
                profile_id=profile_id,
                failure_kind="stale_schema",
            )
        self.assertIn(
            candidate.doc_id,
            [item.doc_id for item in list_candidates("CREATE_BM")],
        )

        record_result(
            "CREATE_BM",
            candidate,
            success=False,
            reason="1357054 PersistedQueryNotFound",
            profile_id="profile-3",
            failure_kind="stale_schema",
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
                reason="1357054 PersistedQueryNotFound",
                profile_id=profile_id,
                failure_kind="stale_schema",
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


class CacheFailureClassifierTests(unittest.TestCase):
    def test_top_level_1357054_with_schema_marker_is_stale(self):
        kind = classify_cache_failure(
            payload={
                "error": 1357054,
                "isNotCritical": 1,
                "errorDescription": "PersistedQueryNotFound: unknown field",
            }
        )
        self.assertEqual(kind, "stale_schema")

    def test_1357054_without_schema_marker_is_not_stale(self):
        kind = classify_cache_failure(
            payload={
                "error": 1357054,
                "isNotCritical": 1,
                "errorDescription": "generic request failure",
            }
        )
        self.assertEqual(kind, "other")

    def test_profile_checkpoint_does_not_poison_candidate(self):
        kind = classify_cache_failure(
            payload={
                "error": 1357054,
                "errorDescription": "checkpoint required unknown field",
            }
        )
        self.assertEqual(kind, "account")

    def test_rate_limit_does_not_poison_candidate(self):
        kind = classify_cache_failure(
            payload={
                "error": 1357054,
                "errorDescription": "PersistedQueryNotFound unknown argument",
            },
            http_status=429,
        )
        self.assertEqual(kind, "network")


class _ManualSession:
    def __init__(self):
        self.profile = SimpleNamespace(name="profile-manual")
        self.used_doc_ids = []

    async def bootstrap(self):
        return SimpleNamespace(actor_id="123456789")

    async def graphql_browser_native(self, doc_id, variables, **kwargs):
        self.used_doc_ids.append(doc_id)
        return {
            "data": {
                "bizkit_create_business": {
                    "id": "555666777888999"
                }
            }
        }


class ManualFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_manual_docid_is_not_promoted_or_recorded(self):
        session = _ManualSession()

        async def failed_graphql(doc_id, variables, **kwargs):
            session.used_doc_ids.append(doc_id)
            return {
                "error": 1357054,
                "isNotCritical": 1,
                "errorDescription": "PersistedQueryNotFound: unknown argument",
            }

        session.graphql_browser_native = failed_graphql

        with patch(
            "app.facebook_business_create.discover_current_scope_selector_create_candidate",
            return_value=None,
        ), patch(
            "app.facebook_business_create.list_candidates",
            return_value=[],
        ), patch(
            "app.facebook_business_create.upsert_candidate",
        ) as promote, patch(
            "app.facebook_business_create.record_result",
        ) as record:
            with self.assertRaises(Exception):
                await create_business_with_docids(
                    session,
                    business_name="Test Business",
                    user_email="owner@example.com",
                    explicit_doc_id="6655443322110099",
                    allow_scope_selector_fallback=True,
                )

        promote.assert_not_called()
        record.assert_not_called()
        self.assertEqual(session.used_doc_ids, ["6655443322110099"])

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
