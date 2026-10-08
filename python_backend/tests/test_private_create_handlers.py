import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.private_contract_discovery import WebModuleContracts, discover_private_ad_account_contract
from app.provisioning import private_create_handlers as actions
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.registry import get_handler
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore
from tests import test_ad_account_private_contract as fixtures
from tests.test_ad_account_private_contract import BM, UID, RK, NAME


def modules(doc="1234567890123"):
    return '''__d("BizKitSettingsCreateAdAccountMutation.graphql",[],function(a,b,c,d,e,f,g){
        var node={params:{id:"%s",name:"BizKitSettingsCreateAdAccountMutation",operationKind:"mutation"}};g.exports=node;
    });__d("CreateAdAccountSender",[],function(a,b,c,d,e,f,g){
        return commitMutation(env,{mutation:d("BizKitSettingsCreateAdAccountMutation.graphql"),
        variables:{input:{business_id:111111111111,actor_id:currentActor,name:formName,
        currency:selectedCurrency,timezone_id:selectedTimezone,client_mutation_id:mutationID}}});
    });''' % doc


class ModuleDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_relay_hook_sender_binds_exact_artifact_and_typed_variables(self):
        source = modules().replace('return commitMutation(env,{mutation:d("BizKitSettingsCreateAdAccountMutation.graphql"),',
            'var [submit,pending]=d("RelayHooks").useMutation(d("BizKitSettingsCreateAdAccountMutation.graphql"));return submit({')
        observer = WebModuleContracts(friendly_names=("BizKitSettingsCreateAdAccountMutation",),
            bindings={"businessid": BM, "actorid": UID, "name": NAME, "currency": "USD",
                "timezoneid": 137, "clientmutationid": "current"})
        observer.observe(source)
        self.assertEqual(observer.result()["variables"]["input"]["business_id"], int(BM))
        for changed in (source.replace('return submit', 'submit=otherSubmit;return submit'),
                source.replace('return submit', '[submit]=otherHook;return submit'),
                source.replace('return submit', 'var submit=otherSubmit;return submit'),
                source.replace('return submit', 'function nested(submit){};return submit'),
                source.replace('return submit({', 'return submit=>submit({'),
                source.replace('d("RelayHooks")', 'd("UnrelatedHooks")'),
                source.replace('var [submit,pending]', 'var [,submit]'),
                source.replace('.useMutation(d("BizKitSettingsCreateAdAccountMutation.graphql"))', '.useMutation(d("OtherMutation.graphql"))'),
                source.replace('return submit', 'return unrelatedSubmit'),
                source.replace('client_mutation_id:mutationID', 'unobserved_value:computedState')):
            with self.subTest(source=changed):
                rejected = WebModuleContracts(friendly_names=observer.names, bindings=observer.bindings)
                rejected.observe(changed)
                self.assertIsNone(rejected.result())

    def test_unique_unmodified_import_and_input_aliases_resolve_without_js_execution(self):
        source = modules().replace('return commitMutation(env,{mutation:d("BizKitSettingsCreateAdAccountMutation.graphql"),',
            'var artifact=d("BizKitSettingsCreateAdAccountMutation.graphql");return commitMutation(env,{mutation:artifact,')
        observer = WebModuleContracts(friendly_names=("BizKitSettingsCreateAdAccountMutation",),
            bindings={"businessid": BM, "actorid": UID, "name": NAME, "currency": "USD",
                "timezoneid": 137, "clientmutationid": "current"})
        observer.observe(source)
        self.assertIsNotNone(observer.result())
        for changed in (source.replace('return commitMutation', 'artifact=otherMutation;return commitMutation'),
                source.replace('return commitMutation', 'var artifact=otherMutation;return commitMutation')):
            observer = WebModuleContracts(friendly_names=("BizKitSettingsCreateAdAccountMutation",), bindings=observer.bindings)
            observer.observe(changed)
            self.assertIsNone(observer.result())

    async def test_http_document_artifact_and_sender_form_a_typed_contract(self):
        web = SimpleNamespace(fetch_text=AsyncMock(return_value=(200, '<script>' + modules() + '</script>',
            'https://business.facebook.com/latest/settings/ad_accounts/?business_id=' + BM)))
        result = await discover_private_ad_account_contract(web, business_id=BM, account_name=NAME,
            currency="USD", timezone_id=137, actor_id=UID)
        self.assertEqual(result["variables"]["input"]["business_id"], int(BM))
        self.assertEqual(result["variables"]["input"]["actor_id"], UID)
        self.assertEqual(result["variables"]["input"]["timezone_id"], 137)
        self.assertEqual(result["schema_source"], "web_module")
        self.assertTrue(result["module_sha256"])
        self.assertEqual(web.fetch_text.await_count, 1)

    def test_artifact_only_or_unknown_expression_or_conflicting_schema_is_not_a_contract(self):
        for source in (modules().split('__d("CreateAdAccountSender"')[0],
                modules().replace('client_mutation_id:mutationID', 'unknown_identity:someOtherProfile'),
                modules() + modules("8888888888888"),
                modules().replace('business_id:111111111111', '...otherInput,business_id:111111111111'),
                modules().replace('business_id:111111111111', 'cookie:{value:"secret"},business_id:111111111111')):
            observer = WebModuleContracts(friendly_names=("BizKitSettingsCreateAdAccountMutation",),
                bindings={"businessid": BM, "actorid": UID, "name": NAME,
                    "currency": "USD", "timezoneid": 137, "clientmutationid": "current"})
            observer.observe(source)
            self.assertIsNone(observer.result())


class RKActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.PrivateTransportTests.asyncSetUp(self)
        self.session.facebook_business_browser = AsyncMock(side_effect=AssertionError("Chromium forbidden"))
        self.cache_patch = patch("app.provisioning.private_create_handlers.AdAccountContractStore", return_value=self.cache)
        self.cache_patch.start()

    async def run_action(self, item="job-1", **overrides):
        return await get_handler("AD_ACCOUNT")(self.session,
            {"business_id": BM, "name": NAME, "currency": "USD", "timezone_id": 137, **overrides}, {},
            profile_id="15", scope_key="bundle-1", item_id=item, provisioning_state=self.state)

    async def test_production_registry_uses_http_intent_verify_commit(self):
        result = await self.run_action()
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(result["transport"], "private_http")
        self.assertFalse(result["browser_started"])
        self.assertEqual(len(self.meta.posts), 1)
        saved = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["phase"], "CREATE_CONFIRMED")
        self.assertEqual(saved["result"]["verification"]["id"], RK)
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_full_service_keeps_bm_rk_binding_without_browser(self):
        with patch("app.provisioning.service._await_profile_mutation_cooldown", new=AsyncMock()):
            result = await ProvisioningService(self.state).run(item_id="job-1", profile_id="15",
                context=self.context, session=self.session, payload={"steps": ["AD_ACCOUNT"],
                    "scope_key": "bundle-1", "parameters": {"AD_ACCOUNT": {
                        "business_id": BM, "name": NAME, "currency": "USD", "timezone_id": 137}}})
        self.assertEqual(result["state"]["business_id"], BM)
        self.assertEqual(result["state"]["ad_account_id"], "act_" + RK)
        self.assertEqual(len(self.meta.posts), 1)
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_lost_post_response_is_reconciled_without_second_post(self):
        self.meta.timeout = True
        result = await self.run_action()
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(len(self.meta.posts), 1)

    async def test_unknown_result_survives_new_job_and_name_change(self):
        original = self.meta.fetch_text
        async def empty(url, **kwargs):
            created = self.meta.created
            self.meta.created = False
            try:
                return await original(url, **kwargs)
            finally:
                self.meta.created = created
        self.web.fetch_text = empty
        self.meta.timeout = True
        with self.assertRaises(ProvisioningError):
            await self.run_action()
        saved = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["phase"], "CREATE_RESULT_UNKNOWN")
        await self.state.set_running("job-2", "15", "bundle-1", ProvisioningStep.AD_ACCOUNT)
        await self.state.checkpoint("job-2", "15", "bundle-1", ProvisioningStep.AD_ACCOUNT,
            {"action_phase": "EXECUTE", "transport_primary": "facebook_private_http_contract"})
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action("job-2")
        self.assertEqual(error.exception.code, "CREATE_AD_ACCOUNT_RESULT_UNKNOWN")
        await self.state.set_running("job-3", "15", "bundle-1", ProvisioningStep.AD_ACCOUNT)
        with self.assertRaises(ProvisioningError):
            await self.run_action("job-3", name="Other RK")
        self.assertEqual(len(self.meta.posts), 1)
        self.web.fetch_text = original
        await self.state.set_running("job-4", "15", "bundle-1", ProvisioningStep.AD_ACCOUNT)
        result = await self.run_action("job-4")
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(len(self.meta.posts), 1)

    async def test_response_id_without_inventory_never_commits(self):
        original = self.meta.fetch_text
        async def wrong_bm(url, **kwargs):
            response = await original(url, **kwargs)
            if self.meta.created:
                return response[0], response[1].replace(BM, "888888888888"), response[2]
            return response
        self.web.fetch_text = wrong_bm
        with self.assertRaises(ProvisioningError):
            await self.run_action()
        snapshot = await self.state.snapshot("15", "bundle-1")
        self.assertIsNone(snapshot.ad_account_id)
        saved = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["create_response_ad_account_id"], RK)

    async def test_cold_contract_resolves_via_http_and_posts_once(self):
        self.cache.path.unlink()
        original = self.meta.fetch_text
        async def document(url, **kwargs):
            response = await original(url, **kwargs)
            return response[0], response[1] + '<script>' + modules() + '</script>', response[2]
        self.web.fetch_text = document
        result = await self.run_action()
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        sent = json.loads(self.meta.posts[0][1]["data"]["variables"])["input"]
        self.assertNotIn("media_agency", sent)
        self.assertEqual(sent["business_id"], int(BM))
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_missing_schema_never_starts_browser_or_posts(self):
        self.cache.path.unlink()
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action()
        self.assertEqual(error.exception.code, "PRIVATE_AD_ACCOUNT_CONTRACT_UNAVAILABLE")
        self.assertEqual(self.meta.posts, [])
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_packed_relay_inventory_runs_exact_verify_commit_without_browser(self):
        original = self.meta.fetch_text
        async def packed(url, **kwargs):
            status, body, final = await original(url, **kwargs)
            payloads = actions._json_payloads(body)
            if payloads:
                body = '<script>' + json.dumps({"RelayPrefetchedStreamCache": {
                    "__bbox": {"result": json.dumps(payloads[0])}}}) + '</script>'
            return status, body, final
        self.web.fetch_text = packed
        result = await self.run_action()
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertEqual(len(self.meta.posts), 1)
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_scoped_auth_gate_is_preserved_instead_of_inventory_inconclusive(self):
        self.web.fetch_text = AsyncMock(return_value=(200, '<form id="login_form"></form>',
            'https://business.facebook.com/business/loginpage/?session=SECRET'))
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action()
        self.assertEqual(error.exception.code, "BUSINESS_LOGIN_GATE")
        self.assertEqual(self.meta.posts, [])
        saved = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(saved["result"]["inventory_auth_error"], "BUSINESS_LOGIN_GATE")
        self.assertNotIn("SECRET", repr(saved["result"]["inventory_verification"]))

    async def test_database_intent_failure_prevents_post(self):
        original = self.state.checkpoint
        async def checkpoint(*args):
            if args[-1].get("phase") == "CREATE_SUBMIT_INTENT":
                raise OSError("disk full")
            return await original(*args)
        with patch.object(self.state, "checkpoint", new=checkpoint):
            with self.assertRaises(ProvisioningError):
                await self.run_action()
        self.assertEqual(self.meta.posts, [])


class BMActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProvisioningStateStore(str(Path(self.temp.name) / "state.db"))
        await self.store.init()
        await self.store.set_running("bm-1", "14", "bm-scope", ProvisioningStep.BUSINESS)
        self.created = False
        self.posts = []
        self.web = SimpleNamespace(fetch_text=self.fetch, bootstrap=AsyncMock(return_value=SimpleNamespace(actor_id=UID)),
            graphql=self.graphql, profile=SimpleNamespace(name="14"))
        self.context = SimpleNamespace(profile_id="14", cookies={"c_user": UID}, email="owner@example.com")
        self.session = SimpleNamespace(context=self.context, facebook_web=AsyncMock(return_value=self.web),
            facebook_business_browser=AsyncMock(side_effect=AssertionError("Chromium forbidden")))
        self.addCleanup(patch.stopall)
        patch("app.facebook_business_create.discover_current_scope_selector_create_candidate", new=AsyncMock(return_value=None)).start()
        patch("app.facebook_business_create.list_candidates", return_value=[]).start()
        patch("app.facebook_business_create.record_result").start()
        patch("app.facebook_business_create.upsert_candidate", return_value=None).start()

    async def fetch(self, url, **kwargs):
        rows = [{"__typename": "Business", "id": BM, "name": "Test Business"}] if self.created else []
        return 200, '<script type="application/json">' + json.dumps({"data": {"businesses": rows}}) + '</script>', url

    async def graphql(self, doc, variables, **kwargs):
        await kwargs["before_submit"]()
        saved = await self.store.step("bm-1", ProvisioningStep.BUSINESS)
        self.assertEqual(saved["result"]["phase"], "CREATE_SUBMIT_INTENT")
        self.posts.append(doc)
        self.created = True
        return {"data": {"bizkit_create_business": {"id": BM}}}

    async def run_action(self, item="bm-1"):
        return await get_handler("BUSINESS")(self.session, {"name": "Test Business"}, {},
            profile_id="14", scope_key="bm-scope", item_id=item, provisioning_state=self.store)

    async def test_bm_http_post_is_independently_verified_before_commit(self):
        result = await self.run_action()
        self.assertEqual(result["business_id"], BM)
        self.assertEqual(result["verification"]["exact_business_id"], BM)
        self.assertEqual(len(self.posts), 1)
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_restored_submitted_phase_is_not_logged_as_a_new_submit(self):
        await self.store.set_running("bm-old", "14", "old-scope", ProvisioningStep.BUSINESS)
        await self.store.checkpoint("bm-old", "14", "old-scope", ProvisioningStep.BUSINESS,
            {"phase": "CREATE_SUBMITTED", "business_name": "Test Business",
             "baseline_complete": True, "baseline_business_ids": []})
        with self.assertLogs("remask_worker", level="INFO") as logs:
            with self.assertRaises(ProvisioningError):
                await self.run_action()
        self.assertTrue(any("RESTORE_PREVIOUS_CHECKPOINT" in line for line in logs.output))
        self.assertFalse(any("stage=CREATE_SUBMITTED" in line for line in logs.output))
        self.assertEqual(self.posts, [])

    async def test_full_service_retains_proof_for_prepare_and_workspace(self):
        with patch("app.provisioning.service._await_profile_mutation_cooldown", new=AsyncMock()):
            result = await ProvisioningService(self.store).run(item_id="bm-1", profile_id="14",
                context=self.context, session=self.session, payload={"steps": ["BUSINESS"],
                    "scope_key": "bm-scope", "parameters": {"BUSINESS": {"name": "Test Business"}}})
        groups = await self.store.confirmed_business_binding_groups()
        self.assertEqual(result["state"]["business_id"], BM)
        self.assertIn(BM, groups["14"]["businesses"])
        self.session.facebook_business_browser.assert_not_awaited()

    async def test_bm_lost_response_recovers_by_new_name_and_complete_baseline(self):
        original = self.graphql
        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise TimeoutError("response lost")
        self.web.graphql = lost
        result = await self.run_action()
        self.assertEqual(result["business_id"], BM)
        self.assertEqual(len(self.posts), 1)

    async def test_bm_unknown_across_jobs_never_reposts(self):
        original = self.graphql
        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            self.created = False
            raise TimeoutError("response lost")
        self.web.graphql = lost
        with self.assertRaises(ProvisioningError):
            await self.run_action()
        await self.store.set_running("bm-2", "14", "bm-scope", ProvisioningStep.BUSINESS)
        await self.store.checkpoint("bm-2", "14", "bm-scope", ProvisioningStep.BUSINESS,
            {"action_phase": "EXECUTE", "transport_primary": "facebook_web_graphql"})
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action("bm-2")
        self.assertEqual(error.exception.code, "CREATE_BM_RESULT_UNKNOWN")
        self.assertEqual(len(self.posts), 1)
        self.created = True
        result = await self.run_action("bm-2")
        self.assertEqual(result["business_id"], BM)
        self.assertEqual(len(self.posts), 1)

    async def test_js_login_form_reference_is_not_an_auth_wall(self):
        original = self.fetch
        async def fetch(*args, **kwargs):
            status, body, final = await original(*args, **kwargs)
            return status, body + '<script>var component="login_form";</script>', final
        self.web.fetch_text = fetch
        self.assertEqual((await self.run_action())["business_id"], BM)

    async def test_real_login_wall_prevents_bm_post(self):
        self.web.fetch_text = AsyncMock(return_value=(200, '<form id="login_form"></form>',
            "https://business.facebook.com/latest/home"))
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action()
        self.assertEqual(error.exception.code, "SESSION_EXPIRED")
        self.assertEqual(self.posts, [])

    async def test_missing_http_submit_does_not_fall_back_to_native_browser(self):
        self.web.graphql = None
        self.web.graphql_browser_native = AsyncMock(side_effect=AssertionError("Chromium forbidden"))
        with self.assertRaises(ProvisioningError) as error:
            await self.run_action()
        self.assertEqual(error.exception.code, "CREATE_BM_PRIVATE_TRANSPORT_UNAVAILABLE")
        self.web.graphql_browser_native.assert_not_awaited()
        self.assertEqual(self.posts, [])

    def test_production_entrypoints_have_no_browser_operations(self):
        source = inspect.getsource(actions)
        for name in ("facebook_business_browser", "browser_lease", "graphql_browser_native", "playwright", "capture_ad_account_create_request"):
            self.assertNotIn(name, source)
        self.assertIs(get_handler("BUSINESS"), actions.business_handler)
        self.assertIs(get_handler("AD_ACCOUNT"), actions.ad_account_handler)
