import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

from app.private_inventory import private_inventory_snapshot, inventory_diagnostic_summary
from app.private_inventory_queries import QueryArtifacts, read_private_inventory_queries
from app.provisioning.models import ProvisioningStep
from fb_worker import RemoteRequestError
from tests import test_private_create_handlers as actions
from tests.test_ad_account_private_contract import BM, RK, UID, NAME


def observed_settings_modules():
    return (Path(__file__).parent / "fixtures/meta_settings_rk_observed_20261008.js").read_text()


def connected_response(*, business=BM, accounts=(), partial=False):
    edges = [{"node": {"__typename": "AdAccount", "assetType": "AD_ACCOUNT", "assetID": value,
        "business_object_id": value, "id": "relay-ui-id", "business_object_relationship_to_business": "OWNED"},
        "nameColumn": {"bizkit_settings_render_strategy_no_business_id": {"business_object": {
            "__typename": "AdAccount", "business_object_id": value, "business_object_name": "ReMask RK 1"}}}}
        for value in accounts]
    return {"data": {"business": {"__typename": "AdBusiness", "id": business,
        "connected_objects": {"edges": edges, "page_info": {"has_next_page": partial, "end_cursor": "cursor"}},
        "business_ad_accounts": {"edges": [{"__typename": "BusinessToAdAccountsEdge"}] if accounts else []}}}}


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

    def test_query_after_six_thousand_ui_modules_is_not_lost(self):
        unrelated = ''.join('__d("UI%d",[],function(a,b,c,d,e,f,g){g.exports=null;});' % i for i in range(6005))
        observed = QueryArtifacts()
        observed.observe(unrelated + artifact(imported=True))
        self.assertEqual(len(observed.contracts(BM)), 1)
        self.assertEqual(len(observed.modules), 2)
        self.assertEqual(observed.modules_scanned, 6007)

    def test_minified_relay_boolean_flags_are_literal_data(self):
        source = artifact().replace('"name": "ad_accounts",', '"name": "ad_accounts", "plural": !1, "flag": !0,')
        self.assertEqual(len(self.compile(source)), 1)
        for changed in (source.replace('!1', '!someRuntime'), source.replace('!1', 'executeSomething()')):
            self.assertEqual(self.compile(changed), [])

    def test_actual_meta_route_query_and_preload_bind_exact_inventory_contract(self):
        rows = self.compile(observed_settings_modules())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["friendly_name"], "BusinessCometBizSuiteSettingsAdAccountsRootQuery")
        self.assertEqual(rows[0]["response_collection"], "connected_objects")
        self.assertEqual(rows[0]["variables"], {"businessID": BM, "assetTypes": ["AD_ACCOUNT"], "searchTerm": None,
            "orderBy": None, "assetFilters": None, "globalFilters": None, "count": 100, "includeDiscoveryAssets": False})
        self.assertEqual(self.compile(observed_settings_modules().replace('26033539376343649', '6666666666666'))[0]["doc_id"], '6666666666666')

    def test_observed_route_asset_type_is_required_and_other_types_or_mutable_binding_fail(self):
        source = observed_settings_modules()
        for changed in (source.replace('assetType:"AD_ACCOUNT"', 'assetType:"PAGE"'),
                source.replace('assetType:"AD_ACCOUNT"', 'assetType:unknownRuntime'),
                source.replace('assetTypes:[e]', 'assetTypes:[differentType]'),
                source.replace('return o!=null', 'e=otherType;return o!=null'),
                source.replace('BusinessCometBizSuiteSettingsAdAccountsRootQuery$Parameters', 'UnrelatedQuery$Parameters')):
            self.assertEqual(self.compile(changed), [])

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

    async def test_observed_connected_objects_confirm_empty_and_exact_rk_without_ui_ids(self):
        for accounts in ((), (RK,)):
            web = self.web(connected_response(accounts=accounts), observed_settings_modules())
            result = await self.snapshot(web, list(accounts))
            self.assertTrue(result["ready"])
            self.assertEqual(result["businesses"][0]["confirmed_empty"], not bool(accounts))
            if accounts:
                self.assertEqual(result["businesses"][0]["ad_accounts"][0]["id"], RK)
                self.assertEqual(result["businesses"][0]["ad_accounts"][0]["name"], "ReMask RK 1")
            self.assertEqual(web.posts[0][1]["assetFilters"], None)
            self.assertEqual(web.posts[0][1]["includeDiscoveryAssets"], False)

    async def test_connected_objects_malformed_foreign_partial_or_hidden_account_blocks_absence(self):
        hidden = connected_response()
        hidden["data"]["business"]["business_ad_accounts"]["edges"] = [{"__typename": "BusinessToAdAccountsEdge"}]
        unknown = connected_response()
        unknown["data"]["business"]["connected_objects"]["edges"] = [{"node": {"id": RK}}]
        for payload in (hidden, unknown, connected_response(partial=True), connected_response(business="888888888888")):
            result = await self.snapshot(self.web(payload, observed_settings_modules()))
            self.assertFalse(result["ready"])
            self.assertFalse(result["businesses"][0]["confirmed_empty"])

    async def test_connected_objects_conflicting_canonical_ids_never_confirm_target(self):
        for key, value in (("assetID", "555555555555"), ("business_object_id", "malformed"), ("assetType", "PAGE"), ("__typename", "Page")):
            payload = connected_response(accounts=[RK])
            payload["data"]["business"]["connected_objects"]["edges"][0]["node"][key] = value
            result = await self.snapshot(self.web(payload, observed_settings_modules()), [RK])
            self.assertFalse(result["ready"])
            self.assertEqual(result["businesses"][0]["confirmed_expected_account_ids"], [])

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
        await self.query_create_flow(observed_settings_modules(), "BusinessCometBizSuiteSettingsAdAccountsRootQuery", connected_response)

    async def test_actual_meta_schema_query_create_verify_commit_without_chromium(self):
        await self.query_create_flow(observed_settings_modules(), "BusinessCometBizSuiteSettingsAdAccountsRootQuery", connected_response)

    async def test_cold_current_meta_contract_create_and_independent_query_commit(self):
        await self.cold_current_meta_flow()

    async def test_cold_current_meta_lost_create_response_is_reconciled_without_second_post(self):
        await self.cold_current_meta_flow(lost_response=True)

    async def cold_current_meta_flow(self, lost_response=False):
        import asyncio
        import uuid
        self.cache.path.unlink()
        source = observed_settings_modules() + actions.observed_create_modules()
        query = "BusinessCometBizSuiteSettingsAdAccountsRootQuery"
        operations = []
        owner = self.meta
        def post(url, **kwargs):
            friendly = kwargs["data"]["fb_api_req_friendly_name"]
            operations.append(friendly)
            creating = friendly == "BizKitSettingsCreateAdAccountMutation"
            if creating:
                owner.posts.append((url, kwargs))
                owner.created = True
            else:
                self.assertEqual(friendly, query)
            class Response:
                status = 200
                headers = {}
                async def text(self):
                    if creating:
                        if lost_response:
                            raise asyncio.TimeoutError()
                        return json.dumps({"data": {"business_settings_create_ad_account": {
                            "business_object_id": RK, "business_object_ui_id": "123123123123",
                            "id": "AdAccount:opaque-relay-id"}}})
                    return json.dumps(connected_response(accounts=[RK] if owner.created else []))
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
            return Response()
        self.meta.post = post
        async def fetch(url, **kwargs):
            return 200, '<script>' + source + '</script> ["DTSGInitialData",[],{"token":"CURRENT"}]', url
        self.web.fetch_text = fetch
        result = await actions.RKActionTests.run_action(self)
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(operations, [query, "BizKitSettingsCreateAdAccountMutation", query])
        self.assertEqual(len(owner.posts), 1)
        request = owner.posts[0][1]
        variables = json.loads(request["data"]["variables"])
        self.assertEqual(variables["businessID"], BM)
        self.assertEqual(variables["adAccountName"], NAME)
        self.assertEqual(variables["timezoneID"], "137")
        self.assertEqual(variables["currency"], "USD")
        self.assertEqual(variables["endAdvertiserID"], BM)
        self.assertEqual(uuid.UUID(variables["qplJoinID"]).version, 4)
        self.assertEqual(request["data"]["av"], UID)
        self.assertEqual(request["proxy"], "http://profile-proxy:8080")
        self.assertNotIn("input", variables)
        self.assertEqual(self.cache.diagnostic()["state"], "missing")
        saved = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["phase"], "CREATE_CONFIRMED")
        self.assertEqual(saved["result"]["verification"]["id"], RK)
        if not lost_response:
            self.assertEqual(saved["result"]["create_response_ad_account_id"], RK)
        self.session.facebook_business_browser.assert_not_awaited()
        self.web.graphql_browser_native.assert_not_awaited()

    async def query_create_flow(self, source, query_name, build_response):
        original_post = self.meta.post
        operations = []
        def post(url, **kwargs):
            friendly = kwargs["data"].get("fb_api_req_friendly_name")
            operations.append(friendly)
            if friendly != query_name:
                return original_post(url, **kwargs)
            owner = self.meta
            class Response:
                status = 200
                headers = {}
                async def text(self): return json.dumps(build_response(accounts=[RK] if owner.created else []))
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
            return Response()
        self.meta.post = post
        async def fetch(url, **kwargs):
            body = '<script>' + source + '</script> ["DTSGInitialData",[],{"token":"CURRENT"}]'
            return 200, body, url
        self.web.fetch_text = fetch
        result = await actions.RKActionTests.run_action(self)
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(operations, [query_name, "BizKitSettingsCreateAdAccountMutation", query_name])
        self.assertEqual(len(self.meta.posts), 1)
        self.session.facebook_business_browser.assert_not_awaited()
        self.web.graphql_browser_native.assert_not_awaited()

    async def test_query_context_conflict_stops_before_any_post(self):
        with self.assertRaises(RemoteRequestError):
            await self.web.graphql("5555555555555", {"businessID": "888888888888"},
                friendly_name="BusinessAdAccountsQuery", endpoint_url="https://business.facebook.com/api/graphql/", business_context_id=BM)
        self.assertEqual(self.meta.posts, [])
