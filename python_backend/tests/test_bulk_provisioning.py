import asyncio
import ast
import inspect
import subprocess
import textwrap
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.models import CreateJobRequest
from app.store import JobStore
from app.session import MetaSession
from app.facebook_page_discovery import _extract_known_page_lists, _extract_pages_from_browser_document, business_page_relation_proven, browser_business_page_relation_proven
from app.facebook_business_create import _attach_response_confirms_page
from app.facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError, _exact_business_request_context
from app.provisioning.auto_plan import expand_auto_profiles
from app.provisioning.business_handler import business_handler
from app.provisioning.models import ProvisioningStep, ProvisioningError
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore


def request(count=2, **payload):
    return CreateJobRequest(profiles=[{'profile_id':'7','tasks':[{'action':'provisioning','payload':{
        'auto_generate':True,'batch_count':count,'steps':['PROXY_CHECK','FAN_PAGES','BUSINESS','AD_ACCOUNT'],
        'parameters':{'AD_ACCOUNT':{'currency':'usd','timezone_id':1}},**payload}}]}], idempotency_key='bulk-test')


class PageIdentityTests(unittest.TestCase):
    def test_document_route_cannot_override_explicit_other_business_scope(self):
        self.assertFalse(_exact_business_request_context('111111111', {'999999999'}, True))
        self.assertFalse(_exact_business_request_context('111111111', {'111111111','999999999'}, True))
        self.assertTrue(_exact_business_request_context('111111111', {'111111111'}, False))
        self.assertTrue(_exact_business_request_context('111111111', set(), True))
        self.assertFalse(_exact_business_request_context('111111111', set(), False))

    def test_page_submit_selector_ignores_wizard_header_and_global_add(self):
        source=ast.parse(textwrap.dedent(inspect.getsource(FacebookBusinessBrowser._click_page_add_surface_action)))
        script=next(node.args[0].value for node in ast.walk(source) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='evaluate' and node.args
            and isinstance(node.args[0],ast.Constant) and '(args)' in str(node.args[0].value))
        harness=r"""
const fs=require('fs'); const fn=eval('('+JSON.parse(fs.readFileSync(0,'utf8'))+')');
function run(hasFooter) {
 const all=[];
 function el(text,y,h=20){const attrs={}; const e={innerText:text,textContent:text,disabled:false,
   getAttribute:k=>attrs[k]||null,setAttribute:(k,v)=>attrs[k]=v,removeAttribute:k=>delete attrs[k],
   getBoundingClientRect:()=>({x:100,y,width:300,height:h,bottom:y+h})};all.push(e);return e;}
 const header=el('Request approval',100), cancel=el('Cancel',500), footer=el('Request approval',500), globalAdd=el('Add',750);
 const field=el('',300);field.setAttribute('placeholder','Facebook Page name or URL');
 const dialog=el('Add an existing Page Facebook Page name or URL',50,550);
 dialog.querySelectorAll=q=>q.startsWith('input')?[field]:(hasFooter?[header,cancel,footer]:[header,cancel]);
 global.document={querySelectorAll:q=>q.includes('[role="dialog"]')?[dialog]:q.startsWith('input')?[field]:all.filter(e=>e.getAttribute('data-remask-page-add-action'))};
 global.getComputedStyle=()=>({display:'block',visibility:'visible',pointerEvents:'auto'});
 const clicked=fn(['data-remask-page-add-action',['Request approval','Add']]);
 return {clicked,header:!!header.getAttribute('data-remask-page-add-action'),footer:!!footer.getAttribute('data-remask-page-add-action'),global:!!globalAdd.getAttribute('data-remask-page-add-action')};
}
process.stdout.write(JSON.stringify([run(true),run(false)]));
"""
        result=subprocess.run(['node','-e',harness],input=json.dumps(script),text=True,capture_output=True,check=True,timeout=5)
        self.assertEqual(json.loads(result.stdout),[
            {'clicked':True,'header':False,'footer':True,'global':False},
            {'clicked':False,'header':False,'footer':False,'global':False}])

    def test_attach_verification_rejects_global_page_and_other_business(self):
        page={'__typename':'Page','id':'222222222','name':'Mine'}
        global_payload={'viewer':{'pages_you_manage':{'nodes':[page]}}, 'business':{'id':'999999999','owned_pages':{'nodes':[page]}}}
        self.assertFalse(business_page_relation_proven(global_payload,'111111111','222222222'))
        self.assertFalse(business_page_relation_proven(global_payload,'111111111','222222222',request_scoped=True))
        self.assertFalse(browser_business_page_relation_proven('<script>'+json.dumps(global_payload)+'</script>','111111111','222222222'))
        self.assertFalse(browser_business_page_relation_proven('<div>222222222</div>','111111111','222222222'))
        self.assertTrue(business_page_relation_proven({'business':{'id':'111111111','owned_pages':{'nodes':[page]}}},'111111111','222222222'))
        self.assertTrue(business_page_relation_proven({'business_assets':{'nodes':[{'asset':page}]}},'111111111','222222222',request_scoped=True))

    def test_page_result_rejects_name_prefix_and_embedded_numeric_id(self):
        source=ast.parse(textwrap.dedent(inspect.getsource(FacebookBusinessBrowser._click_unique_page_add_result)))
        script=next(n.args[0].value for n in ast.walk(source) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and n.func.attr=='evaluate' and n.args and isinstance(n.args[0],ast.Constant))
        harness=r"""
const fs=require('fs');const fn=eval('('+JSON.parse(fs.readFileSync(0,'utf8'))+')');
function run(text){
 const attrs={role:'option'};const leaf={children:[],innerText:text};
 const card={tagName:'DIV',disabled:false,children:[leaf],innerText:text,textContent:text,
  getAttribute:k=>attrs[k]||null,setAttribute:(k,v)=>attrs[k]=v,removeAttribute:k=>delete attrs[k],closest:()=>null,
  querySelectorAll:()=>[leaf],getBoundingClientRect:()=>({x:100,y:300,width:500,height:50})};
 const root={innerText:'Add an existing Page',getAttribute:()=>null,querySelectorAll:()=>[card],
  getBoundingClientRect:()=>({x:50,y:50,width:700,height:600})};
 global.document={querySelectorAll:q=>q.includes('[role="dialog"]')?[root]:[]};
 global.getComputedStyle=()=>({display:'block',visibility:'visible'});
 return !!fn(['data-remask-page-result','1289628847574478','Media Shopsw','61594993341059']).selected;
}
process.stdout.write(JSON.stringify([run('Media Shopsw suffix'),run('9'+'1289628847574478'+'9'),run('Media Shopsw'),run('1289628847574478')]));
"""
        result=subprocess.run(['node','-e',harness],input=json.dumps(script),text=True,capture_output=True,check=True,timeout=5)
        self.assertEqual(json.loads(result.stdout),[False,False,True,True])

    def test_profile_plus_uses_delegate_page_identity(self):
        payload={'viewer':{'actor':{'additional_profiles_with_biz_tools':{'edges':[
            {'node':{'__typename':'User','id':'61594993341059','name':'Media Shopsw',
                     'delegate_page_id':'1289628847574478','delegate_page':{'id':'1289628847574478','__typename':'Page'}}},
            {'node':{'__typename':'User','id':'61595071734540','name':'ReMask Page',
                     'delegate_page_id':'1372205759306015'}}]}}}}
        pages=_extract_pages_from_browser_document('<script>'+json.dumps(payload)+'</script>')
        self.assertEqual([p['id'] for p in pages],['1289628847574478','1372205759306015'])
        self.assertEqual(pages[0]['profile_id'],'61594993341059')
        self.assertTrue(all(p['ownership_verified'] for p in pages))

    def test_profile_switcher_and_missing_delegate_do_not_prove_page(self):
        self.assertEqual(_extract_known_page_lists({'profile_switcher_eligible_profiles':{'nodes':[
            {'profile':{'id':'61594993341059','name':'Profile'}}]},
            'additional_profiles_with_biz_tools':{'edges':[{'node':{'id':'61594993341059','name':'Profile'}}]}}),[])

    def test_neighbor_entities_and_recommendations_do_not_inflate_count(self):
        payload={'viewer':{'id':'111111111','name':'Profile','pages_can_administer':{'nodes':[
            {'__typename':'Page','id':'222222222','name':'My Page','picture':{'id':'333333333','name':'Picture'}}]},
            'recommended_pages':[{'__typename':'Page','id':'444444444','name':'Recommendation'}],
            'page_business':{'__typename':'Business','id':'555555555','name':'BM'}}}
        pages=_extract_pages_from_browser_document('<script type="application/json">'+json.dumps(payload)+'</script>')
        self.assertEqual([p['id'] for p in pages],['222222222'])
        self.assertTrue(pages[0]['ownership_verified']); self.assertEqual(pages[0]['ownership_source'],'pages_can_administer')

    def test_unmanaged_objects_are_not_owned_inventory(self):
        self.assertEqual(_extract_known_page_lists({'pages':{'nodes':[{'__typename':'Page','id':'222222222','name':'Public Page'}]}}),[])

    def test_escaped_relay_exact_connection(self):
        payload={'pages_you_manage':{'edges':[{'node':{'id':'222222222','name':'Mine','__typename':'Page'}}]}}
        self.assertEqual([p['id'] for p in _extract_pages_from_browser_document(json.dumps(json.dumps(payload)))],['222222222'])

    def test_attach_requires_exact_relation(self):
        self.assertFalse(_attach_response_confirms_page({'mutation':{'success':True}},'111111111','222222222'))
        self.assertFalse(_attach_response_confirms_page({'business':{'id':'999999999','primary_page':{'id':'222222222'}}},'111111111','222222222'))
        self.assertTrue(_attach_response_confirms_page({'business':{'id':'111111111','primary_page':{'id':'222222222'}}},'111111111','222222222'))


class AutoPlanTests(unittest.TestCase):
    def test_template_asset_ids_cannot_redirect_an_automatic_unit(self):
        params=expand_auto_profiles(request(1,parameters={
            'FAN_PAGES':{'mode':'attach_existing','page_id':'123456789','existing_page_id':'123456789','business_id':'987654321','ad_account_id':'555555555'},
            'BUSINESS':{'primary_page_id':'123456789','attach_page':True},
            'AD_ACCOUNT':{'business_id':'987654321','bm_id':'987654321','ad_account_id':'555555555','currency':'USD','timezone_id':1}}).profiles,'job')[0].tasks[0].payload['parameters']
        self.assertEqual(params['FAN_PAGES']['mode'],'create')
        self.assertNotIn('existing_page_id',params['FAN_PAGES'])
        self.assertNotIn('business_id',params['FAN_PAGES'])
        self.assertNotIn('primary_page_id',params['BUSINESS'])
        self.assertIs(params['BUSINESS']['attach_page'],False)
        self.assertNotIn('business_id',params['AD_ACCOUNT']); self.assertNotIn('bm_id',params['AD_ACCOUNT'])

    def test_units_have_independent_scopes_and_single_random_page(self):
        rows=expand_auto_profiles(request(3).profiles,'job'); self.assertEqual(len(rows),3)
        scopes=set(); names=set(); emails=set()
        for row in rows:
            task=row.tasks[0]; payload=task.payload; params=payload['parameters']
            scopes.add(payload['scope_key']); names.add(params['FAN_PAGES']['names'][0]); emails.add(params['BUSINESS']['user_email'])
            self.assertEqual(task.idempotency_key,payload['scope_key']); self.assertEqual(params['FAN_PAGES']['count'],1)
            self.assertTrue(params['BUSINESS']['use_created_page']); self.assertNotIn('page_id',params['BUSINESS'])
            self.assertIs(params['BUSINESS']['attach_page'],False)
            self.assertEqual(params['AD_ACCOUNT']['currency'],'USD'); self.assertFalse(payload['generated']['contact_email_registered'])
            self.assertRegex(params['BUSINESS']['user_email'],r'^[a-f0-9]{24}@gmail\.com$')
        self.assertEqual(len(scopes),3); self.assertEqual(len(names),3); self.assertEqual(len(emails),3)

    def test_invalid_counts_and_order(self):
        for count in [0,21,True,1.5]:
            with self.subTest(count=count),self.assertRaises(ValueError): expand_auto_profiles(request(count).profiles,'job')
        with self.assertRaises(ValueError): expand_auto_profiles(request(steps=['BUSINESS','FAN_PAGES']).profiles,'job')

    def test_missing_rk_settings(self):
        with self.assertRaises(ValueError): expand_auto_profiles(request(parameters={}).profiles,'job')

    def test_multiple_provisioning_tasks_split(self):
        req=CreateJobRequest(profiles=[{'profile_id':'7','tasks':[{'action':'provisioning','payload':{'steps':['BUSINESS']}}]*2}])
        self.assertEqual(len(expand_auto_profiles(req.profiles,'job')),2)

    def test_expanded_batch_limit(self):
        req=request(20); req.profiles=req.profiles*26
        with self.assertRaises(ValueError): expand_auto_profiles(req.profiles,'job')


class BulkPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'jobs.sqlite'); self.jobs=JobStore(self.path); self.state=ProvisioningStateStore(self.path)
        await self.jobs.init(); await self.state.init()

    async def test_generation_survives_idempotency_retry_restore(self):
        job,created=await self.jobs.create_job(request()); self.assertTrue(created)
        items=await self.jobs.queued_item_ids(job); self.assertEqual(len(items),2)
        payloads=[(await self.jobs.tasks(item))[0]['payload'] for item in items]
        with patch('app.provisioning.auto_plan.secrets.token_hex',side_effect=AssertionError('regenerated')):
            same,created=await self.jobs.create_job(request()); self.assertEqual(same,job); self.assertFalse(created)
        for item in items:
            task=(await self.jobs.tasks(item))[0]; await self.jobs.set_task_failed(task['id'],'TEMPORARY','retry',retryable=True); await self.jobs.finalize_item(item)
        restored=JobStore(str(Path(self.tmp.name)/'restored.sqlite')); await restored.init(); await restored.import_snapshots([await self.jobs.job_view(job)])
        self.assertEqual(await restored.retry_failed(job),2)
        self.assertEqual([(await restored.tasks(item))[0]['payload'] for item in items],payloads)

    async def test_bulk_queue_and_recovery_interleave_profiles(self):
        req=request(3)
        other=req.profiles[0].model_copy(deep=True); other.profile_id='8'; req.profiles.append(other)
        job,_=await self.jobs.create_job(req)
        ids=await self.jobs.queued_item_ids(job)
        profiles=[(await self.jobs.item(item))['profile_id'] for item in ids]
        self.assertEqual(profiles,['7','8','7','8','7','8'])
        recovered=await self.jobs.recover()
        self.assertEqual([(await self.jobs.item(item))['profile_id'] for item in recovered],profiles)

    async def test_invalid_generation_rolls_back(self):
        with self.assertRaises(ValueError): await self.jobs.create_job(request(21))
        with self.jobs._connect() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    async def test_chain_binds_this_items_page_and_business_and_retry_skips_success(self):
        payload=expand_auto_profiles(request(1).profiles,'job')[0].tasks[0].payload; payload['steps']=payload['steps'][1:]; observed=[]
        async def handler(session,params,state,**kwargs):
            observed.append((dict(params),dict(state)))
            if len(observed)==1: return {'page_ids':['222222222']}
            if len(observed)==2: return {'business_id':'111111111','primary_page_id':params['page_id']}
            return {'ad_account_id':'act_333333333'}
        with patch('app.provisioning.service.get_handler',return_value=handler),patch('app.provisioning.service._await_profile_mutation_cooldown',new=AsyncMock()):
            service=ProvisioningService(self.state); kwargs=dict(item_id='unit',profile_id='7',context=SimpleNamespace(profile_id='7',proxy=None,user_agent='test'),session=SimpleNamespace(),payload=payload)
            await service.run(**kwargs); self.assertEqual(observed[1][0]['page_id'],'222222222'); self.assertEqual(observed[2][1]['business_id'],'111111111')
            await service.run(**kwargs); self.assertEqual(len(observed),3)

    async def test_business_browser_slot_released_before_ad_account_phase(self):
        slot=asyncio.BoundedSemaphore(1)
        context=SimpleNamespace(profile_id='7',proxy=None,user_agent='test')
        session=MetaSession(context)
        async def close(): slot.release()
        session._business_browser=SimpleNamespace(close=close)
        await slot.acquire()
        async def handler(session,params,state,**kwargs):
            if not state.get('business_id'): return {'business_id':'111111111'}
            await slot.acquire(); slot.release()
            return {'ad_account_id':'act_333333333'}
        with patch('app.provisioning.service.get_handler',return_value=handler),patch('app.provisioning.service._await_profile_mutation_cooldown',new=AsyncMock()):
            await asyncio.wait_for(ProvisioningService(self.state).run(item_id='slot-test',profile_id='7',context=context,session=session,
                payload={'steps':['BUSINESS','AD_ACCOUNT'],'scope_key':'slot-test'}),1)
        self.assertIsNone(session._business_browser)

    async def test_legacy_unique_profile_migration_preserves_tasks_and_checkpoints(self):
        path=str(Path(self.tmp.name)/'legacy.sqlite')
        con=sqlite3.connect(path)
        con.executescript("""
        CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,idempotency_key TEXT UNIQUE,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
        CREATE TABLE job_items(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,profile_id TEXT NOT NULL,status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,error_code TEXT,error_message TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(job_id, profile_id));
        CREATE TABLE job_tasks(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,position INTEGER NOT NULL,action TEXT NOT NULL,
            payload_json TEXT NOT NULL,idempotency_key TEXT,status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,result_json TEXT,error_code TEXT,error_message TEXT,
            created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(item_id,position));
        INSERT INTO jobs VALUES('old-job','FAILED','old-key',1,1);
        INSERT INTO job_items VALUES('old-item','old-job','7','FAILED',2,'PAGE_ADD_UI_CHANGED','missing',1,1);
        INSERT INTO job_tasks VALUES('old-task','old-item',0,'provisioning','{}','old-task-key','FAILED',2,'{"business_id":"111111111"}','PAGE_ADD_UI_CHANGED','missing',1,1);
        """)
        con.close()
        legacy_state=ProvisioningStateStore(path); await legacy_state.init()
        await legacy_state.set_running('old-item','7','old-scope',ProvisioningStep.BUSINESS)
        await legacy_state.checkpoint('old-item','7','old-scope',ProvisioningStep.BUSINESS,{'business_id':'111111111','phase':'CREATE_CONFIRMED'})
        legacy=JobStore(path); await legacy.init(); await legacy.init()
        self.assertEqual((await legacy.tasks('old-item'))[0]['result']['business_id'],'111111111')
        self.assertEqual((await legacy_state.step('old-item',ProvisioningStep.BUSINESS))['result']['phase'],'CREATE_CONFIRMED')
        self.assertEqual((await legacy.item('old-item'))['attempt'],2)
        job,_=await legacy.create_job(request()); self.assertEqual(len(await legacy.queued_item_ids(job)),2)
        with legacy._connect() as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0],1)

    async def prepare_resume(self):
        await self.state.set_running('item','7','scope',ProvisioningStep.BUSINESS)
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'business_id':'111111111','primary_page_id':'222222222','phase':'CREATE_CONFIRMED'})
        browser=AsyncMock(); browser.verify_page_attached.return_value=False
        session=SimpleNamespace(context=SimpleNamespace(profile_id='7',pages=[]),facebook_controller=AsyncMock(return_value=SimpleNamespace(session=SimpleNamespace(bootstrap=AsyncMock()))),facebook_business_browser=AsyncMock(return_value=browser))
        kwargs=dict(provisioning_state=self.state,item_id='item',profile_id='7',scope_key='scope')
        return session,browser,kwargs

    async def test_confirmed_bm_succeeds_without_page_attach_or_recreate(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,
            {'phase':'PAGE_ADD_NOT_SUBMITTED','create_response_business_id':'111111111',
             'create_response_path':'data.business_create.business.id'})
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as attach,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertEqual(result['business_id'],'111111111')
        self.assertEqual(result['phase'],'CREATE_CONFIRMED'); self.assertEqual(result['resume_from'],'DONE')
        self.assertIsNone(result['primary_page_id']); self.assertEqual(result['selected_page_id'],'222222222')
        self.assertFalse(result['page']['attach_requested']); self.assertFalse(result['page']['attachment_verified'])
        self.assertEqual(result['create_response_business_id'],'111111111')
        attach.assert_not_awaited(); create.assert_not_awaited(); session.facebook_business_browser.assert_not_awaited()

    async def test_no_page_is_required_for_independent_bm_create(self):
        session,browser,kwargs=await self.prepare_resume()
        # A fresh item has no saved CREATE.
        kwargs.update(item_id='fresh',scope_key='fresh')
        await self.state.set_running('fresh','7','fresh',ProvisioningStep.BUSINESS)
        async def created(*args,**kw):
            self.assertEqual(kw['page_id'],''); self.assertIs(kw['require_page_backed'],False)
            await kw['before_submit']()
            self.assertEqual((await self.state.step('fresh',ProvisioningStep.BUSINESS))['result']['phase'],'CREATE_SUBMITTED')
            await kw['after_created'](SimpleNamespace(business_id='444444444',response_path='data.business_create.business.id'))
            return SimpleNamespace(business_id='444444444',transport='facebook_web_graphql_scope_selector',primary_page_id='',diagnostics=[])
        with patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock(side_effect=created)) as create,patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as attach:
            result=await business_handler(session,{'name':'Fresh','user_email':'owner@example.com'},{},**kwargs)
        self.assertEqual(result['business_id'],'444444444'); self.assertEqual(result['phase'],'CREATE_CONFIRMED')
        self.assertIsNone(result['primary_page_id']); self.assertIsNone(result['selected_page_id'])
        create.assert_awaited_once(); attach.assert_not_awaited(); session.facebook_business_browser.assert_not_awaited()

    async def test_selected_page_is_reference_and_not_sent_to_native_create(self):
        session,browser,kwargs=await self.prepare_resume()
        kwargs.update(item_id='fresh',scope_key='fresh')
        await self.state.set_running('fresh','7','fresh',ProvisioningStep.BUSINESS)
        async def created(*args,**kw):
            self.assertEqual(kw['page_id'],''); self.assertIs(kw['require_page_backed'],False)
            await kw['after_created'](SimpleNamespace(business_id='444444444',response_path='data.business_create.business.id'))
            return SimpleNamespace(business_id='444444444',transport='facebook_web_graphql_scope_selector',primary_page_id='',diagnostics=[])
        with patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock(side_effect=created)),patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as attach:
            result=await business_handler(session,{'name':'Fresh','user_email':'owner@example.com','page_id':'555555555'},{},**kwargs)
        self.assertEqual(result['selected_page_id'],'555555555'); self.assertIsNone(result['primary_page_id'])
        attach.assert_not_awaited(); session.facebook_business_browser.assert_not_awaited()

    async def test_uncertain_create_still_cannot_be_resubmitted_without_page_attach(self):
        session,browser,kwargs=await self.prepare_resume()
        kwargs.update(item_id='unknown',scope_key='unknown')
        await self.state.set_running('unknown','7','unknown',ProvisioningStep.BUSINESS)
        await self.state.checkpoint('unknown','7','unknown',ProvisioningStep.BUSINESS,{'phase':'CREATE_SUBMITTED'})
        with patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as error:
                await business_handler(session,{'name':'Unknown','user_email':'owner@example.com'},{},**kwargs)
        self.assertEqual(error.exception.code,'CREATE_RESULT_UNKNOWN')
        create.assert_not_awaited(); session.facebook_business_browser.assert_not_awaited()

    async def test_unbound_bm_does_not_erase_uncertain_prior_attach_history(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'phase':'PAGE_ADD_SUBMITTED'})
        result=await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertTrue(result['page']['result_unknown']); self.assertEqual(result['page_attach_previous_phase'],'PAGE_ADD_SUBMITTED')
        self.assertIsNone(result['primary_page_id']); session.facebook_business_browser.assert_not_awaited()

    async def test_attach_flag_rejects_truthy_string(self):
        session,browser,kwargs=await self.prepare_resume()
        with self.assertRaises(ProvisioningError) as error:
            await business_handler(session,{'name':'Existing','user_email':'owner@example.com','attach_page':'false'},{},**kwargs)
        self.assertEqual(error.exception.code,'INVALID_INPUT')

    async def test_completed_unbound_bm_retains_cross_job_duplicate_protection(self):
        session,browser,kwargs=await self.prepare_resume()
        await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        resume=await self.state.latest_business_resume_for_page('7','222222222',exclude_item_id='next-item')
        self.assertEqual(resume['result']['business_id'],'111111111')
        self.assertIsNone(resume['result']['primary_page_id'])
        self.assertEqual(resume['result']['selected_page_id'],'222222222')

    async def test_existing_business_private_attach_never_creates_or_ui_submits(self):
        session,browser,kwargs=await self.prepare_resume()
        async def attach(*args,**kw):
            await kw['before_submit'](); self.assertEqual((await self.state.step('item',ProvisioningStep.BUSINESS))['result']['phase'],'PAGE_ADD_SUBMITTED')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock(side_effect=attach)) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','attach_page':True,'user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertEqual(result['business_id'],'111111111'); self.assertEqual(result['phase'],'PAGE_CONFIRMED')
        create.assert_not_awaited(); browser.add_existing_page.assert_not_awaited(); submit.assert_awaited_once()

    async def test_existing_business_legacy_profile_id_resolves_without_recreate(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'primary_page_id':'61594993341059'})
        session.context.pages=[{'id':'1289628847574478','profile_id':'61594993341059',
            'name':'Media Shopsw','ownership_verified':True,'ownership_source':'additional_profiles_with_biz_tools.delegate_page'}]
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','attach_page':True,'user_email':'owner@example.com','page_id':'61594993341059'},{},**kwargs)
        self.assertEqual(result['primary_page_id'],'1289628847574478')
        self.assertEqual(submit.call_args.kwargs['page_id'],'1289628847574478')
        saved=(await self.state.step('item',ProvisioningStep.BUSINESS))['result']
        self.assertEqual(saved['business_id'],'111111111'); self.assertEqual(saved['selected_page_profile_id'],'61594993341059')
        create.assert_not_awaited()

    async def test_new_job_recovers_legacy_profile_checkpoint_after_identity_resolution(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'primary_page_id':'61594993341059'})
        session.context.pages=[{'id':'1289628847574478','profile_id':'61594993341059','name':'Media Shopsw',
            'ownership_verified':True,'ownership_source':'additional_profiles_with_biz_tools.delegate_page'}]
        kwargs.update(item_id='next-item',scope_key='next-scope')
        await self.state.set_running('next-item','7','next-scope',ProvisioningStep.BUSINESS)
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()),patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','attach_page':True,'user_email':'owner@example.com','page_id':'61594993341059'},{},**kwargs)
        self.assertEqual(result['business_id'],'111111111'); self.assertEqual(result['primary_page_id'],'1289628847574478')
        create.assert_not_awaited()

    async def test_lost_attach_response_blocks_duplicate_on_retry(self):
        session,browser,kwargs=await self.prepare_resume()
        async def lost(*args,**kw): await kw['before_submit'](); raise TimeoutError('lost response')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock(side_effect=lost)) as submit:
            for _ in range(2):
                with self.assertRaises(ProvisioningError) as error: await business_handler(session,{'name':'Existing','attach_page':True,'user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
                self.assertEqual(error.exception.code,'PAGE_ATTACH_RESULT_UNKNOWN')
        self.assertEqual(submit.await_count,1); browser.add_existing_page.assert_not_awaited()

    async def test_new_job_preserves_another_items_uncertain_page_submit(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'phase':'PAGE_ADD_SUBMITTED'})
        await self.state.set_running('next-item','7','next-scope',ProvisioningStep.BUSINESS)
        kwargs.update(item_id='next-item',scope_key='next-scope')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as error:
                await business_handler(session,{'name':'Existing','attach_page':True,'user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertEqual(error.exception.code,'PAGE_ATTACH_RESULT_UNKNOWN')
        submit.assert_not_awaited(); create.assert_not_awaited(); browser.add_existing_page.assert_not_awaited()
        self.assertEqual((await self.state.step('next-item',ProvisioningStep.BUSINESS))['result']['phase'],'PAGE_ADD_SUBMITTED')


class PageHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_new_page_is_not_reported_as_the_requested_creation(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7',pages=[]))
        browser.page=SimpleNamespace(url='https://www.facebook.com/pages/create',wait_for_timeout=AsyncMock())
        browser._fan_page_snapshot=AsyncMock(return_value=[]); browser._goto=AsyncMock()
        browser._fill_first=AsyncMock(return_value=True); browser._fill_fan_page_category=AsyncMock(return_value=True)
        browser._click_named_single_attempt=AsyncMock(return_value={'found':True}); browser._body_text=AsyncMock(return_value='')
        browser.discover_managed_pages=AsyncMock(return_value=[{'id':'222222222','name':'Other Page','ownership_verified':True}])
        with patch('app.facebook_business_browser.asyncio.sleep',new=AsyncMock()):
            with self.assertRaises(BrowserBusinessError) as error:
                await browser.create_fan_page(page_name='Requested Page',category='Digital creator')
        self.assertEqual(error.exception.code,'FAN_PAGE_CREATE_RESULT_UNKNOWN')

    async def test_new_page_keeps_profile_link_for_the_next_business_step(self):
        context=SimpleNamespace(profile_id='7',pages=[]); browser=FacebookBusinessBrowser(context)
        browser.page=SimpleNamespace(url='https://www.facebook.com/pages/create',wait_for_timeout=AsyncMock())
        browser._fan_page_snapshot=AsyncMock(return_value=[]); browser._goto=AsyncMock()
        browser._fill_first=AsyncMock(return_value=True); browser._fill_fan_page_category=AsyncMock(return_value=True)
        browser._click_named_single_attempt=AsyncMock(return_value={'found':True}); browser._body_text=AsyncMock(return_value='')
        row={'id':'1289628847574478','name':'Requested Page','profile_id':'61594993341059','ownership_verified':True}
        browser.discover_managed_pages=AsyncMock(return_value=[row])
        result=await browser.create_fan_page(page_name='Requested Page',category='Digital creator')
        self.assertEqual(result['page_id'],row['id']); self.assertEqual(context.pages,[row])

    async def test_rk_lookup_rejects_response_for_other_business_on_current_route(self):
        from urllib.parse import urlencode
        for rows in ([{'node':{'id':'222222222','name':'Requested Ads'}}], []):
            browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
            listeners={}
            request=SimpleNamespace(method='POST',url='https://business.facebook.com/api/graphql/',
                post_data=urlencode({'fb_api_req_friendly_name':'BusinessAdAccountsQuery','doc_id':'123456789',
                                     'variables':json.dumps({'businessID':'999999999'})}))
            response=SimpleNamespace(url=request.url,request=request,
                text=AsyncMock(return_value=json.dumps({'data':{'business':{'id':'999999999','ad_accounts':{'edges':rows}}}})))
            browser.page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts?business_id=111111111',
                on=lambda event,callback:listeners.update({event:callback}),remove_listener=lambda event,callback:None)
            async def goto(target):
                browser.page.url=target
                listeners['response'](response)
                await asyncio.sleep(0)
            browser._goto=AsyncMock(side_effect=goto)
            result=await browser.find_ad_account_in_inventory(business_id='111111111',account_name='Requested Ads',timeout_seconds=2)
            self.assertFalse(result['confirmed'])
            self.assertFalse(result['confirmed_empty'])

    async def test_profile_connection_relay_survives_navigation_timeout_without_html(self):
        from urllib.parse import urlencode
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        listeners={}
        request=SimpleNamespace(method='POST',url='https://www.facebook.com/api/graphql/',post_data=urlencode({
            'fb_api_req_friendly_name':'CometProfileSwitcherQuery','doc_id':'123456789','variables':'{}'}))
        response=SimpleNamespace(url=request.url,request=request,text=AsyncMock(return_value=json.dumps({
            'data':{'viewer':{'actor':{'additional_profiles_with_biz_tools':{'edges':[{'node':{
                'id':'61594993341059','name':'Media Shopsw','delegate_page_id':'1289628847574478'}}]}}}}})))
        browser.page=SimpleNamespace(url='https://www.facebook.com/pages/?category=your_pages',
            on=lambda event,cb:listeners.update({event:cb}),remove_listener=lambda *args:None,
            content=AsyncMock(side_effect=RuntimeError('HTML is not ready')))
        async def goto(*args,**kwargs):
            listeners['response'](response)
            await asyncio.sleep(0)
            raise BrowserBusinessError('FACEBOOK_NAVIGATION_FAILED','DOMContentLoaded timed out')
        browser._goto=AsyncMock(side_effect=goto); browser._assert_authenticated=AsyncMock()
        rows=await browser.discover_managed_pages(fast=True)
        self.assertEqual(rows[0]['id'],'1289628847574478')
        browser.page.content.assert_not_awaited()

    async def test_page_navigation_timeout_keeps_exact_managed_document_proof(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        payload={'viewer':{'actor':{'additional_profiles_with_biz_tools':{'edges':[{'node':{
            'id':'61594993341059','name':'Media Shopsw','delegate_page_id':'1289628847574478'}}]}}}}
        browser.page=SimpleNamespace(url='https://www.facebook.com/pages/?category=your_pages',
            content=AsyncMock(return_value='<script>'+json.dumps(payload)+'</script>'))
        browser._goto=AsyncMock(side_effect=BrowserBusinessError('FACEBOOK_NAVIGATION_FAILED','DOMContentLoaded timed out'))
        browser._assert_authenticated=AsyncMock()
        rows=await browser.discover_managed_pages(fast=True)
        self.assertEqual([r['id'] for r in rows],['1289628847574478'])
        self.assertTrue(rows[0]['ownership_verified'])
        self.assertEqual(browser._goto.await_count,1)

    async def test_page_navigation_timeout_without_managed_proof_remains_failure(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        browser.page=SimpleNamespace(url='https://www.facebook.com/pages/?category=your_pages',
            content=AsyncMock(return_value='<script>'+json.dumps({'recommendations':{'id':'1289628847574478','name':'Media Shopsw'}})+'</script>'))
        browser._goto=AsyncMock(side_effect=BrowserBusinessError('FACEBOOK_NAVIGATION_FAILED','DOMContentLoaded timed out'))
        browser._assert_authenticated=AsyncMock()
        with self.assertRaises(BrowserBusinessError) as error:
            await browser.discover_managed_pages(fast=True)
        self.assertEqual(error.exception.code,'FACEBOOK_NAVIGATION_FAILED')

    async def test_current_add_existing_page_menu_opens_before_field_lookup(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        browser.page=SimpleNamespace(url='https://business.facebook.com/latest/settings/pages?business_id=111111111',
            wait_for_timeout=AsyncMock(),get_by_role=lambda *args,**kwargs:SimpleNamespace(count=AsyncMock(return_value=0)))
        opened=False
        async def click(names):
            nonlocal opened
            opened='Add an existing Page' in names
            return opened
        async def fill(**kwargs):
            if not opened:
                raise AssertionError('Cannot fill the Page field before opening the real menu action')
            return True
        browser.verify_page_attached=AsyncMock(return_value=False)
        browser._open_pages_add_action=AsyncMock(return_value=True)
        browser._click_named=AsyncMock(side_effect=click)
        browser._fill_page_add_identifier=AsyncMock(side_effect=fill)
        browser._click_unique_page_add_result=AsyncMock(return_value=True)
        browser._page_add_surface_state=AsyncMock(return_value={})
        browser._body_text=AsyncMock(return_value='')
        result=await browser.preflight_page_add_form(business_id='111111111',page_id='222222222')
        self.assertTrue(result['result_selected'])

    async def test_dom_marked_page_field_receives_keyboard_events(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7',pages=[{'id':'1289628847574478','profile_id':'61594993341059','name':'Media Shopsw','ownership_verified':True}]))
        field=SimpleNamespace(is_visible=AsyncMock(return_value=True),is_editable=AsyncMock(return_value=True),
            fill=AsyncMock(),press_sequentially=AsyncMock(),press=AsyncMock(),get_attribute=AsyncMock(return_value='Facebook\u00a0Page name or URL'))
        browser.page=SimpleNamespace(get_by_placeholder=lambda p:SimpleNamespace(count=AsyncMock(return_value=0)),evaluate=AsyncMock(return_value=True),locator=lambda p:SimpleNamespace(first=field))
        browser._fill_first=AsyncMock(return_value=True)
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='1289628847574478'))
        field.press_sequentially.assert_awaited_once_with('Media Shopsw',delay=15)
        browser._fill_first.assert_not_awaited()

    async def test_name_autocomplete_uses_the_verified_managed_page_name(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7',pages=[{'id':'1289628847574478',
            'profile_id':'61594993341059','name':'Media Shopsw','ownership_verified':True}]))
        field=SimpleNamespace(is_visible=AsyncMock(return_value=True),is_editable=AsyncMock(return_value=True),
            fill=AsyncMock(),press_sequentially=AsyncMock(),press=AsyncMock())
        browser.page=SimpleNamespace(get_by_placeholder=lambda pattern:SimpleNamespace(count=AsyncMock(return_value=1),nth=lambda i:field))
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='1289628847574478'))
        field.press_sequentially.assert_awaited_once_with('Media Shopsw',delay=15)

    async def test_delegate_page_search_uses_its_confirmed_profile_link(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7',pages=[{'id':'1289628847574478','profile_id':'61594993341059','ownership_verified':True}]))
        browser.page=SimpleNamespace(); browser._fill_first=AsyncMock(return_value=True)
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='1289628847574478'))
        self.assertEqual(browser._fill_first.call_args.kwargs['value'],'https://www.facebook.com/profile.php?id=61594993341059')

    async def test_repeated_close_cannot_reap_another_live_browser(self):
        semaphore=asyncio.Semaphore(1)
        first=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        second=FacebookBusinessBrowser(SimpleNamespace(profile_id='8'))
        with patch('app.facebook_business_browser._BROWSER_SEMAPHORE',semaphore),patch('app.facebook_business_browser._BROWSER_LIMIT',1),patch('app.facebook_business_browser._reap_stale_chromium_processes',new=AsyncMock(return_value={'found':0})) as reap:
            await semaphore.acquire(); first._semaphore_acquired=True
            await first.close(); self.assertEqual(reap.await_count,1)
            await semaphore.acquire(); second._semaphore_acquired=True
            await first.close(); self.assertEqual(reap.await_count,1)
            self.assertTrue(second._semaphore_acquired)
            await second.close(); self.assertEqual(reap.await_count,2)

    async def test_parallel_browser_limit_disables_global_process_reaping(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        semaphore=asyncio.Semaphore(2)
        await semaphore.acquire(); browser._semaphore_acquired=True
        with patch('app.facebook_business_browser._BROWSER_SEMAPHORE',semaphore),patch('app.facebook_business_browser._BROWSER_LIMIT',2),patch('app.facebook_business_browser._reap_stale_chromium_processes',new=AsyncMock()) as reap:
            await browser.close()
        reap.assert_not_awaited()

    async def test_ready_business_pages_document_is_reused(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        browser.page=SimpleNamespace(url='https://business.facebook.com/latest/settings/pages/?business_id=111111111',wait_for_timeout=AsyncMock())
        browser._goto=AsyncMock(); browser._click_named=AsyncMock(return_value=True)
        self.assertTrue(await browser._open_pages_add_action('111111111'))
        browser._goto.assert_not_awaited()

    async def test_current_autocomplete_keeps_focus_until_a_result_is_selected(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        field=SimpleNamespace(is_visible=AsyncMock(return_value=True),is_editable=AsyncMock(return_value=True),
            fill=AsyncMock(),press_sequentially=AsyncMock(),press=AsyncMock())
        browser.page=SimpleNamespace(get_by_placeholder=lambda pattern:SimpleNamespace(count=AsyncMock(return_value=1),nth=lambda i:field))
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='222222222'))
        field.fill.assert_awaited_once_with('',timeout=2000)
        field.press_sequentially.assert_awaited_once_with('https://www.facebook.com/222222222',delay=15)
        field.press.assert_not_awaited()

    async def test_current_name_url_picker_receives_page_url(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        browser.page=SimpleNamespace()
        browser._fill_first=AsyncMock(return_value=True)
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='222222222'))
        self.assertEqual(browser._fill_first.call_args.kwargs['value'],'https://www.facebook.com/222222222')

    async def test_late_add_action_uses_one_navigation(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7')); browser.page=SimpleNamespace(wait_for_timeout=AsyncMock()); browser._goto=AsyncMock(); browser._click_named=AsyncMock(side_effect=[False,False,True])
        self.assertTrue(await browser._open_pages_add_action('111111111')); self.assertEqual(browser._goto.await_count,1); self.assertEqual(browser._click_named.await_count,3)

    async def test_redirect_abort_probes_replacement_document(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7')); browser.page=SimpleNamespace(wait_for_timeout=AsyncMock()); browser._goto=AsyncMock(side_effect=BrowserBusinessError('FACEBOOK_NAVIGATION_FAILED','net::ERR_ABORTED',retryable=True)); browser._assert_authenticated=AsyncMock(); browser._click_named=AsyncMock(return_value=True)
        self.assertTrue(await browser._open_pages_add_action('111111111')); browser._assert_authenticated.assert_awaited_once()
