import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.private_inventory import private_inventory_snapshot, inventory_diagnostic_summary
from app.private_inventory_queries import QueryArtifacts, read_private_inventory_queries
from fb_worker import RemoteRequestError
from tests import test_private_create_handlers as actions
from tests.test_ad_account_private_contract import BM, RK


def artifact(*, kind="query", collection="ad_accounts", imported=False, extra_args=None, defaults=None):
    definitions = [{"kind": "LocalArgument", "name": "businessID", "defaultValue": None},
                   {"kind": "LocalArgument", "name": "count", "defaultValue": 20}]
    definitions.extend(defaults or [])
    operation = {"argumentDefinitions": definitions, "selections": [{"kind": "LinkedField", "name": "business",
        "args": [{"kind": "Variable", "name": "id", "variableName": "businessID"}],
        "selections": [{"kind": "LinkedField", "name": collection,
            "args": [{"kind": "Variable", "name": "first", "variableName": "count"}] + (extra_args or []),
            "selections": []}]}]}
    params = {"id": "5555555555555", "name": "BusinessAdAccountsQuery", "operationKind": kind}
    node = json.dumps({"params": params, "operation": operation})
    if imported:
        node = node.replace('"5555555555555"', 'd("BusinessAdAccountsQuery_facebookRelayOperation")')
    source = '__d("BusinessAdAccountsQuery.graphql",[],function(a,b,c,d,e,f,g){var node=' + node + ';g.exports=node;});'
    if imported:
        source += '__d("BusinessAdAccountsQuery_facebookRelayOperation",[],function(a,b,c,d,e,f,g){g.exports="5555555555555";});'
    return source


def response(*, business=BM, accounts=None, partial=False):
    rows = [] if accounts is None else [{"node": {"__typename": "AdAccount", "id": key}} for key in accounts]
    return {"data": {"business": {"__typename": "Business", "id": business,
        "ad_accounts": {"edges": rows, "page_info": {"has_next_page": partial}}}}}


class QueryCompilerTests(unittest.TestCase):
    def compile(self, source):
        observed = QueryArtifacts()
        observed.observe(source)
        return observed.contracts(BM)

    def test_literal_and_separate_persisted_id_modules_bind_exact_business(self):
        for imported in (False, True):
            row = self.compile(artifact(imported=imported))[0]
            self.assertEqual(row["variables"], {"businessID": BM, "count": 100})
            self.assertEqual(row["operation_kind"], "query")
            self.assertTrue(row["module_sha256"])

    def test_immutable_relay_argument_aliases_resolve_without_evaluation(self):
        source = artifact().replace('var node=', 'var args=[{"kind":"Variable","name":"id","variableName":"businessID"}];var node=')
        source = source.replace('[{"kind": "Variable", "name": "id", "variableName": "businessID"}]', 'args')
        self.assertEqual(len(self.compile(source)), 1)
        self.assertEqual(self.compile(source.replace('var node=', 'args[0]=other;var node=')), [])

    def test_mutation_unknown_binding_and_conflicting_artifacts_are_rejected(self):
        for source in (artifact(kind="mutation"), artifact().replace('"id", "variableName"', '"other", "variableName"'),
                artifact().replace('"defaultValue": null', '"defaultValue": unknownRuntime'),
                artifact() + artifact().replace('5555555555555', '6666666666666')):
            self.assertEqual(self.compile(source), [])

    def test_owned_only_collection_cannot_prove_absence_of_client_accounts(self):
        self.assertEqual(self.compile(artifact(collection="owned_ad_accounts")), [])

    def test_literal_and_variable_restrictive_filters_or_cursor_are_rejected(self):
        for argument in ("search", "status", "after"):
            self.assertEqual(self.compile(artifact(extra_args=[{"kind": "Literal", "name": argument, "value": "restricted"}])), [])
            self.assertEqual(self.compile(artifact(extra_args=[{"kind": "Variable", "name": argument, "variableName": "filter"}],
                defaults=[{"kind": "LocalArgument", "name": "filter", "defaultValue": "restricted"}])), [])

    def test_generic_assets_require_observed_ad_account_type(self):
        self.assertEqual(self.compile(artifact(collection="assets")), [])
        defaults = [{"kind": "LocalArgument", "name": "assetTypes", "defaultValue": ["AD_ACCOUNT"]}]
        self.assertEqual(self.compile(artifact(collection="assets", defaults=defaults)), [])
        row = self.compile(artifact(collection="assets", defaults=defaults,
            extra_args=[{"kind": "Variable", "name": "asset_types", "variableName": "assetTypes"}]))[0]
        self.assertTrue(row["asset_scope"])


class ReadInventoryTests(unittest.IsolatedAsyncioTestCase):
    def web(self, result=None, source=None):
        owner = SimpleNamespace(profile=SimpleNamespace(name="15"), posts=[], gets=[])
        source = artifact() if source is None else source
        async def fetch(url, **kwargs):
            owner.gets.append(url)
            if ".js" in url:
                return 200, source, url
            return 200, '<script type="application/json">{"data":{"business":{"id":"' + BM + '"}}}</script><script src="https://static.xx.fbcdn.net/runtime.js"></script>', url
        async def graphql(doc, variables, **kwargs):
            await kwargs["before_submit"]()
            owner.posts.append((doc, variables, kwargs))
            return response() if result is None else result
        owner.fetch_text, owner.graphql = fetch, graphql
        return owner

    async def snapshot(self, web, expected=None):
        return await private_inventory_snapshot(web, known_business_ids={BM},
            known_accounts_by_business={BM: set(expected or [])}, execute_read_queries=True)

    async def test_html_shell_is_hydrated_by_query_and_proves_complete_empty(self):
        web = self.web()
        result = await self.snapshot(web)
        self.assertTrue(result["businesses"][0]["confirmed_empty"])
        self.assertEqual(len(web.posts), 1)
        self.assertEqual(web.posts[0][1]["businessID"], BM)
        self.assertEqual(web.posts[0][2]["business_context_id"], BM)
        self.assertNotIn("_document", repr(result))
        self.assertEqual(inventory_diagnostic_summary(result)[-1]["query_posts"], 1)

    async def test_query_cache_is_profile_local_and_reused_for_fresh_verification(self):
        web = self.web(response(accounts=[RK]))
        self.assertTrue((await self.snapshot(web, [RK]))["ready"])
        self.assertTrue((await self.snapshot(web, [RK]))["ready"])
        self.assertEqual(len(web.posts), 2)
        self.assertEqual(len([url for url in web.gets if ".js" in url]), 1)
        self.assertFalse(hasattr(self.web(), "_private_inventory_query_cache"))

    async def test_partial_foreign_or_errored_empty_response_does_not_allow_create(self):
        error = response()
        error["errors"] = [{"message": "partial"}]
        for payload in (response(partial=True), response(business="888888888888"), error):
            result = await self.snapshot(self.web(payload))
            self.assertFalse(result["businesses"][0]["confirmed_empty"])
            self.assertFalse(result["ready"])

    async def test_no_contract_or_mutation_artifact_sends_no_query(self):
        for source in ("", artifact(kind="mutation")):
            web = self.web(source=source)
            result = await self.snapshot(web)
            self.assertFalse(result["ready"])
            self.assertEqual(web.posts, [])

    async def test_auth_precheck_failure_is_not_logged_as_sent_query(self):
        web = self.web()
        web.graphql = AsyncMock(side_effect=RemoteRequestError("auth precheck", request_may_have_been_sent=False))
        _, diag = await read_private_inventory_queries(web, business_id=BM,
            document='<script>' + artifact() + '</script>', entry_url="https://business.facebook.com/latest/settings/ad_accounts/")
        self.assertEqual(diag["query_posts"], 0)
        self.assertEqual(diag["query_attempts"], 1)
        self.assertEqual(web._private_inventory_query_cache, {})


class QueryCreateFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await actions.RKActionTests.asyncSetUp(self)

    async def test_query_precheck_one_create_query_verify_commit_without_chromium(self):
        original_post = self.meta.post
        operations = []
        def post(url, **kwargs):
            friendly = kwargs["data"].get("fb_api_req_friendly_name")
            operations.append(friendly)
            if friendly != "BusinessAdAccountsQuery":
                return original_post(url, **kwargs)
            owner = self.meta
            class Response:
                status = 200
                headers = {}
                async def text(self): return json.dumps(response(accounts=[RK] if owner.created else []))
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
            return Response()
        self.meta.post = post
        async def fetch(url, **kwargs):
            body = '<script>' + artifact() + '</script> ["DTSGInitialData",[],{"token":"CURRENT"}]'
            return 200, body, url
        self.web.fetch_text = fetch
        result = await actions.RKActionTests.run_action(self)
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(operations, ["BusinessAdAccountsQuery", "BizKitSettingsCreateAdAccountMutation", "BusinessAdAccountsQuery"])
        self.assertEqual(len(self.meta.posts), 1)
        self.session.facebook_business_browser.assert_not_awaited()
        self.web.graphql_browser_native.assert_not_awaited()

    async def test_query_context_conflict_stops_before_any_post(self):
        with self.assertRaises(RemoteRequestError):
            await self.web.graphql("5555555555555", {"businessID": "888888888888"},
                friendly_name="BusinessAdAccountsQuery", endpoint_url="https://business.facebook.com/api/graphql/", business_context_id=BM)
        self.assertEqual(self.meta.posts, [])
