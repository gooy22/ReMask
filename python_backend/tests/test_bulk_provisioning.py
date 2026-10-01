import asyncio
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
from app.facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError
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
    def test_attach_verification_rejects_global_page_and_other_business(self):
        page={'__typename':'Page','id':'222222222','name':'Mine'}
        global_payload={'viewer':{'pages_you_manage':{'nodes':[page]}}, 'business':{'id':'999999999','owned_pages':{'nodes':[page]}}}
        self.assertFalse(business_page_relation_proven(global_payload,'111111111','222222222'))
        self.assertFalse(business_page_relation_proven(global_payload,'111111111','222222222',request_scoped=True))
        self.assertFalse(browser_business_page_relation_proven('<script>'+json.dumps(global_payload)+'</script>','111111111','222222222'))
        self.assertFalse(browser_business_page_relation_proven('<div>222222222</div>','111111111','222222222'))
        self.assertTrue(business_page_relation_proven({'business':{'id':'111111111','owned_pages':{'nodes':[page]}}},'111111111','222222222'))
        self.assertTrue(business_page_relation_proven({'business_assets':{'nodes':[{'asset':page}]}},'111111111','222222222',request_scoped=True))

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
            'BUSINESS':{'primary_page_id':'123456789'},
            'AD_ACCOUNT':{'business_id':'987654321','bm_id':'987654321','ad_account_id':'555555555','currency':'USD','timezone_id':1}}).profiles,'job')[0].tasks[0].payload['parameters']
        self.assertEqual(params['FAN_PAGES']['mode'],'create')
        self.assertNotIn('existing_page_id',params['FAN_PAGES'])
        self.assertNotIn('business_id',params['FAN_PAGES'])
        self.assertNotIn('primary_page_id',params['BUSINESS'])
        self.assertNotIn('business_id',params['AD_ACCOUNT']); self.assertNotIn('bm_id',params['AD_ACCOUNT'])

    def test_units_have_independent_scopes_and_single_random_page(self):
        rows=expand_auto_profiles(request(3).profiles,'job'); self.assertEqual(len(rows),3)
        scopes=set(); names=set(); emails=set()
        for row in rows:
            task=row.tasks[0]; payload=task.payload; params=payload['parameters']
            scopes.add(payload['scope_key']); names.add(params['FAN_PAGES']['names'][0]); emails.add(params['BUSINESS']['user_email'])
            self.assertEqual(task.idempotency_key,payload['scope_key']); self.assertEqual(params['FAN_PAGES']['count'],1)
            self.assertTrue(params['BUSINESS']['use_created_page']); self.assertNotIn('page_id',params['BUSINESS'])
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

    async def test_existing_business_private_attach_never_creates_or_ui_submits(self):
        session,browser,kwargs=await self.prepare_resume()
        async def attach(*args,**kw):
            await kw['before_submit'](); self.assertEqual((await self.state.step('item',ProvisioningStep.BUSINESS))['result']['phase'],'PAGE_ADD_SUBMITTED')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock(side_effect=attach)) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertEqual(result['business_id'],'111111111'); self.assertEqual(result['phase'],'PAGE_CONFIRMED')
        create.assert_not_awaited(); browser.add_existing_page.assert_not_awaited(); submit.assert_awaited_once()

    async def test_existing_business_legacy_profile_id_resolves_without_recreate(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'primary_page_id':'61594993341059'})
        session.context.pages=[{'id':'1289628847574478','profile_id':'61594993341059',
            'name':'Media Shopsw','ownership_verified':True,'ownership_source':'additional_profiles_with_biz_tools.delegate_page'}]
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            result=await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'61594993341059'},{},**kwargs)
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
            result=await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'61594993341059'},{},**kwargs)
        self.assertEqual(result['business_id'],'111111111'); self.assertEqual(result['primary_page_id'],'1289628847574478')
        create.assert_not_awaited()

    async def test_lost_attach_response_blocks_duplicate_on_retry(self):
        session,browser,kwargs=await self.prepare_resume()
        async def lost(*args,**kw): await kw['before_submit'](); raise TimeoutError('lost response')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock(side_effect=lost)) as submit:
            for _ in range(2):
                with self.assertRaises(ProvisioningError) as error: await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
                self.assertEqual(error.exception.code,'PAGE_ATTACH_RESULT_UNKNOWN')
        self.assertEqual(submit.await_count,1); browser.add_existing_page.assert_not_awaited()

    async def test_new_job_preserves_another_items_uncertain_page_submit(self):
        session,browser,kwargs=await self.prepare_resume()
        await self.state.checkpoint('item','7','scope',ProvisioningStep.BUSINESS,{'phase':'PAGE_ADD_SUBMITTED'})
        await self.state.set_running('next-item','7','next-scope',ProvisioningStep.BUSINESS)
        kwargs.update(item_id='next-item',scope_key='next-scope')
        with patch('app.provisioning.business_handler.set_business_primary_page',new=AsyncMock()) as submit,patch('app.provisioning.business_handler.create_business_resilient',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as error:
                await business_handler(session,{'name':'Existing','user_email':'owner@example.com','page_id':'222222222'},{},**kwargs)
        self.assertEqual(error.exception.code,'PAGE_ATTACH_RESULT_UNKNOWN')
        submit.assert_not_awaited(); create.assert_not_awaited(); browser.add_existing_page.assert_not_awaited()
        self.assertEqual((await self.state.step('next-item',ProvisioningStep.BUSINESS))['result']['phase'],'PAGE_ADD_SUBMITTED')


class PageHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_business_pages_document_is_reused(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        browser.page=SimpleNamespace(url='https://business.facebook.com/latest/settings/pages/?business_id=111111111',wait_for_timeout=AsyncMock())
        browser._goto=AsyncMock(); browser._click_named=AsyncMock(return_value=True)
        self.assertTrue(await browser._open_pages_add_action('111111111'))
        browser._goto.assert_not_awaited()

    async def test_current_autocomplete_receives_keyboard_and_blur_events(self):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='7'))
        field=SimpleNamespace(is_visible=AsyncMock(return_value=True),is_editable=AsyncMock(return_value=True),
            fill=AsyncMock(),press_sequentially=AsyncMock(),press=AsyncMock())
        browser.page=SimpleNamespace(get_by_placeholder=lambda pattern:SimpleNamespace(first=field))
        self.assertTrue(await browser._fill_page_add_identifier(labels=('Facebook Page name or URL',),value='222222222'))
        field.fill.assert_awaited_once_with('',timeout=2000)
        field.press_sequentially.assert_awaited_once_with('https://www.facebook.com/222222222',delay=15,timeout=4000)
        field.press.assert_awaited_once_with('Tab',timeout=1000)

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
