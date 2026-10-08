import copy
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.static_meta_contracts import (MANIFEST, StaticAssetContracts, command,
    contract_metadata, execute, rk_create_contract, create_business_static)
from app.static_meta_inventory import _bm_scopes, read_business_inventory, read_ad_account_inventory
from app.contract_maintenance.update_settings import compile_candidate
from app.private_page_ownership import full_control_tasks
from app.provisioning import private_create_handlers
from app.provisioning.models import ProvisioningError
from tests.test_private_inventory_queries import connected_response, BM, RK, NAME
from tests.test_private_page_ownership import config, P, PARTIAL


def scopes(nodes=(), *, next_page=False, errors=False):
    value = {'data': {'viewer': {'meta_business_scoping': {'business_scopes': {
        'nodes':[{'scope_id':key,'scope_name':name,'scope_type':'BUSINESS'} for key,name in nodes],
        'page_info':{'has_next_page':next_page}}}}}}
    if errors: value['errors']=[{'message':'partial'}]
    return value


class StaticContractTests(unittest.TestCase):
    def test_offline_compiler_reproduces_pinned_ids_and_variable_schemas(self):
        candidate=compile_candidate(Path(__file__).with_name('fixtures'))
        manifest=json.loads(MANIFEST.read_text())
        for op,row in manifest['operations'].items():
            self.assertEqual(candidate['operations'][op]['doc_id'],row['doc_id'])
            self.assertEqual(candidate['operations'][op]['variables'],row['variables'])
        self.assertIn('response_unverified',candidate['operations']['READ_BM']['evidence'])

    def test_operation_variables_and_mutation_id_types_are_exact(self):
        rk=rk_create_contract(business_id=BM, account_name=NAME,currency='USD',timezone_id=137,actor_id='123456789')
        self.assertNotIn('input',rk['variables'])
        self.assertEqual(rk['variables']['timezoneID'],'137')
        self.assertEqual(set(rk['variables']), {'businessID','adAccountName','currency','timezoneID','endAdvertiserID','qplJoinID'})
        other=rk_create_contract(business_id='555555555555',account_name='Other',currency='EUR',timezone_id=57,actor_id='987654321')
        self.assertNotIn(BM,repr(other));self.assertNotEqual(rk['variables']['qplJoinID'],other['variables']['qplJoinID'])

    def test_asset_defaults_cannot_be_overridden_or_extended(self):
        adapter=StaticAssetContracts()
        friendly=contract_metadata('PAGE')['friendly_name']
        self.assertIsNotNone(adapter.query(friendly,{'businessID':BM,'pageID':RK}))
        self.assertIsNone(adapter.query(friendly,{'businessID':BM,'pageID':RK,'isMMAPageTransfer':True}))
        self.assertIsNone(adapter.query(friendly,{'businessID':BM,'pageID':RK,'input':{}}))
        with self.assertRaises(ProvisioningError):command('ASSIGN',business=BM,user=RK,asset=RK,tasks=['bad'],types=['PAGE'])

    def test_scope_connection_requires_explicit_complete_proof(self):
        self.assertEqual(_bm_scopes(scopes())[1],True)
        value=scopes(); del value['data']['viewer']['meta_business_scoping']['business_scopes']['page_info']
        self.assertFalse(_bm_scopes(value)[1])
        self.assertFalse(_bm_scopes(scopes(errors=True))[1])
        self.assertFalse(_bm_scopes(scopes([(BM,'BM')],next_page=True))[1])
        self.assertFalse(_bm_scopes(scopes([(BM,'BM'),(BM,'BM')]))[1])
        value=scopes([(BM,'BM')]);value['data']['viewer']['meta_business_scoping']['business_scopes']['nodes'][0]['scope_type']='PAGE'
        self.assertEqual(_bm_scopes(value)[0],{})

    def test_implied_tasks_survive_variant_control_filter_like_meta_sdk(self):
        value=config();row=value['assetConfigs'][0]
        row['permissionTasksConfig']=row['permissionTasksConfig'][:1]
        row.update(hasAssetVariants=True,assetVariantConfig={'assetVariantPermissionConfig':[
            {'assetVariantName':'PROFILE_PLUS_DELEGATE_PAGE','availablePermissionTaskIDsForVariant':[P]}]})
        self.assertEqual(full_control_tasks(value,'PAGE',variant='PROFILE_PLUS_DELEGATE_PAGE'),sorted([P,PARTIAL]))
        row['permissionTasksConfig'][0]['impliedTaskIDs']=['malformed']
        with self.assertRaises(ProvisioningError):full_control_tasks(value,'PAGE',variant='PROFILE_PLUS_DELEGATE_PAGE')

    def test_handlers_have_no_runtime_discovery_or_cache_template_dependency(self):
        source=inspect.getsource(private_create_handlers)
        for text in ('discover_private_ad_account_contract','AdAccountContractStore','private_inventory_snapshot('):
            self.assertNotIn(text,source)
        source=inspect.getsource(create_business_static)
        self.assertNotIn('discover_',source);self.assertNotIn('list_candidates',source)


class StaticReadTests(unittest.IsolatedAsyncioTestCase):
    def web(self, payload):
        return SimpleNamespace(graphql=AsyncMock(return_value=payload),profile=SimpleNamespace(name='fixture'),
            fetch_text=AsyncMock(side_effect=AssertionError('No HTML or CDN inventory')))

    async def test_bm_inventory_posts_one_fixed_read_and_no_asset_navigation(self):
        web=self.web(scopes([(BM,'BM')]))
        proof=await read_business_inventory(web)
        self.assertTrue(proof['complete']);self.assertEqual(proof['rows'],{BM:'BM'})
        web.fetch_text.assert_not_awaited()
        self.assertEqual(web.graphql.call_args.args[0],command('READ_BM')['doc_id'])
        self.assertNotIn('asset_id',repr(web.graphql.call_args))

    async def test_prefix_pagination_does_not_treat_first_page_as_complete(self):
        web=self.web(None);web.graphql.side_effect=[scopes([(BM,'BM')],next_page=True),scopes([(BM,'BM'),(RK,'Other')])]
        proof=await read_business_inventory(web)
        self.assertTrue(proof['complete']);self.assertEqual(len(proof['rows']),2)
        self.assertEqual([call.args[1]['fetchNumberForBusinessScopes'] for call in web.graphql.call_args_list],[200,400])

    async def test_expected_bm_is_independently_verified_without_requiring_complete_list(self):
        web=self.web({'data':{'business':{'id':BM,'name':'BM','scheduledForDeletion':False}}})
        proof=await read_business_inventory(web,BM)
        self.assertEqual(proof['rows'],{BM:'BM'});self.assertFalse(proof['complete'])
        self.assertEqual(web.graphql.call_args.kwargs['business_context_id'],BM)

    async def test_wrong_bm_errors_and_partial_inventory_cannot_prove_absence(self):
        for value in (connected_response(business='888888888888'),connected_response(partial=True),{'errors':[{'message':'partial'}]}):
            proof=await read_ad_account_inventory(self.web(value),BM,NAME)
            self.assertFalse(proof['complete']);self.assertEqual(proof['id'],'')

    async def test_rk_unique_name_requires_complete_scope_exact_id_can_reconcile_partial(self):
        value=connected_response(accounts=[RK],partial=True)
        self.assertEqual((await read_ad_account_inventory(self.web(value),BM,NAME))['id'],'')
        self.assertEqual((await read_ad_account_inventory(self.web(value),BM,NAME,RK))['id'],RK)

    async def test_mutation_requires_durable_intent_and_unknown_shape_never_executes(self):
        web=self.web({})
        with self.assertRaises(ProvisioningError):await execute(web,'CLAIM',business=BM,page=RK,join='fixture')
        web.graphql.assert_not_awaited()
        with self.assertRaises(ProvisioningError):await execute(web,'READ_BM',variables={'input':{}})
        web.graphql.assert_not_awaited()

    async def test_diagnostics_do_not_log_auth_or_response_scalar_values(self):
        payload=scopes();payload['data']['viewer']['access_token']='SECRET'
        payload['data']['viewer']['unknown']={'token':'OTHER_SECRET','name':'DO_NOT_LOG'}
        proof=await read_business_inventory(self.web(payload))
        self.assertNotIn('SECRET',repr(proof));self.assertNotIn('DO_NOT_LOG',repr(proof))


class StaticChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests import test_private_page_ownership as assets
        from app.provisioning.state import ProvisioningStateStore
        self.assets=assets
        class Meta(assets.MetaFixture):
            def __init__(inner):
                super().__init__();inner.business_created=False;inner.rk_created=False;inner.reads=[]
                inner.fetch_text=AsyncMock(side_effect=AssertionError('Runtime module/HTML inventory forbidden'))
            async def graphql(inner,doc,variables,*,friendly_name,before_submit=None,**kwargs):
                op=next(op for op in ('READ_BM','CREATE_BM','READ_RK','CREATE_RK','CONFIG','PAGE','RIGHTS','CLAIM','ASSIGN')
                    if contract_metadata(op)['friendly_name']==friendly_name)
                if op in {'READ_BM','READ_RK'}:
                    inner.reads.append(op)
                    if op=='READ_BM':return scopes([(assets.BM,'Test Business')] if inner.business_created else [])
                    return connected_response(business=assets.BM,accounts=[assets.RK] if inner.rk_created else [])
                if op in {'CREATE_BM','CREATE_RK'}:
                    await before_submit();inner.posts.append((friendly_name,copy.deepcopy(variables)))
                    if op=='CREATE_BM':
                        inner.business_created=True
                        response={'data':{'bizkit_create_business':{'id':assets.BM}}}
                    else:
                        inner.rk_created=True
                        response={'data':{'business_settings_create_ad_account':{'business_object_id':assets.RK,'business_object_ui_id':assets.RK}}}
                    if inner.lost==friendly_name:raise TimeoutError('lost submit response')
                    return response
                response=await super().graphql(doc,variables,friendly_name=friendly_name,before_submit=before_submit,**kwargs)
                if op=='CONFIG':response['data']['business']['name']='Test Business'
                return response
        self.meta=Meta()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ProvisioningStateStore(str(Path(self.temp.name)/'state.db'));await self.store.init()
        self.context=SimpleNamespace(profile_id='15',cookies={'c_user':assets.UID},email='owner@example.com')
        self.session=SimpleNamespace(context=self.context,facebook_web=AsyncMock(return_value=self.meta),
            facebook_business_browser=AsyncMock(side_effect=AssertionError('Chromium forbidden')))
        self.saved={}

    async def run_chain(self, suffix='1'):
        from app.provisioning.registry import get_handler
        from app.provisioning.models import ProvisioningStep
        from app.private_page_ownership import ensure_private_page_full_control
        await self.store.set_running('bm-'+suffix,'15','bundle',ProvisioningStep.BUSINESS)
        bm=await get_handler('BUSINESS')(self.session,{'name':'Test Business'},
            {'business_id':self.assets.BM} if self.meta.business_created else {},
            profile_id='15',scope_key='bundle',item_id='bm-'+suffix,provisioning_state=self.store)
        await self.store.set_running('rk-'+suffix,'15','bundle',ProvisioningStep.AD_ACCOUNT)
        rk=await get_handler('AD_ACCOUNT')(self.session,{'business_id':bm['business_id'],'name':NAME,'currency':'USD','timezone_id':137},
            {'ad_account_id':self.assets.RK} if self.meta.rk_created else {},
            profile_id='15',scope_key='bundle',item_id='rk-'+suffix,provisioning_state=self.store)
        async def checkpoint(patch):self.saved.update(copy.deepcopy(patch))
        result=await ensure_private_page_full_control(self.meta,page_id=self.assets.FP,business_id=bm['business_id'],
            ad_account_id=rk['ad_account_id'],profile_id='15',checkpoint=checkpoint,prior=copy.deepcopy(self.saved))
        return result

    async def test_entire_static_chain_and_second_job_never_recreates_confirmed_assets(self):
        result=await self.run_chain()
        self.assertTrue(result['page_owned_by_business']);self.assertTrue(result['rk_operator_full_control_verified'])
        self.assertEqual(len(self.meta.posts),5)
        self.assertTrue((await self.run_chain('2'))['operator_full_control_verified'])
        self.assertEqual(len(self.meta.posts),5)
        self.meta.fetch_text.assert_not_awaited();self.session.facebook_business_browser.assert_not_awaited()

    async def test_lost_bm_create_response_reconciles_then_finishes_chain(self):
        self.meta.lost=contract_metadata('CREATE_BM')['friendly_name']
        result=await self.run_chain()
        self.assertTrue(result['page_owned_by_business'])
        self.assertEqual(sum(name==self.meta.lost for name,_ in self.meta.posts),1)
        self.assertEqual(len(self.meta.posts),5)
