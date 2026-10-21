"""Hermetic browser checks using the complete installed ReMask Launch module."""
import json
import re
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
from tests import test_cookie_only_profiles as runtime_fixture

ROOT = Path(__file__).resolve().parents[1]
MEDIA_ID = 'm-' + 'a' * 24
MEDIA = {'id': MEDIA_ID, 'sha256': 'b' * 64, 'media_type': 'image', 'original_name': 'fixture.png'}

class InstalledLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = runtime_fixture.CookieOnlyProfileTests('test_complete_runtime_overlay_order_and_installed_php_build_contracts')
        cls.build.setUp()
        cls.build.test_complete_runtime_overlay_order_and_installed_php_build_contracts()
        cls.root = cls.build.root
        cls.config = json.loads(subprocess.check_output(['php', '-r',
            'require $argv[1]; echo json_encode(MetaLaunchValidator::schema());',
            str(cls.root/'classes/MetaLaunchValidator.php')], text=True))
        from app.editor_modules import form_schema
        from app.private_ads_catalog import AdsManagerRouteCatalog
        cls.schema = form_schema(AdsManagerRouteCatalog())
        from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore
        from app.provisioning.state import ProvisioningStateStore
        contracts=PrivateLaunchContractStore('{}')
        contracts.protocol='private_ads_manager_versioned'
        cls.planner=PrivateLaunchService(ProvisioningStateStore(str(cls.root/'browser-plan.sqlite')),contracts=contracts)
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.runtime.stop(); cls.build.tearDown()

    def setUp(self):
        self.bundles = []
        self.missing = False
        self.foreign_bundle_media = False
        self.errors = []
        self.preset_objective = 'LEGACY_TEST_OBJECTIVE' if self._testMethodName == 'test_initial_config_keeps_legacy_goal' else 'OUTCOME_TRAFFIC'
        self.preset_rules={}
        if self._testMethodName=='test_initial_config_restores_dependent_preset_rules':
            self.preset_rules={'optimization_goal':'LINK_CLICKS','billing_event':'IMPRESSIONS',
                'bid_strategy':'LOWEST_COST_WITH_BID_CAP','bid_amount':250}
        if self._testMethodName=='test_preset_changes_dependent_goal_after_config_is_loaded':
            self.preset_objective='OUTCOME_SALES'
            self.preset_rules={'optimization_goal':'OFFSITE_CONVERSIONS','billing_event':'IMPRESSIONS'}
        if self._testMethodName=='test_unsupported_optimization_is_preserved_until_user_replaces_it':
            self.preset_rules={'optimization_goal':'LEGACY_TEST_OPTIMIZATION','billing_event':'IMPRESSIONS'}
        self.delay_config = self._testMethodName.startswith('test_initial_config')
        self.waiting_config = []
        self.lookup_requests = []
        self.lookup_failure = False
        self.instagram_requests = []
        self.instagram_wrong_scope = False
        self.estimate_requests=[]
        self.estimate_failure=False
        self.asset_requests=[]
        self.waiting_pages=[]
        self.worker_requests=[]
        self.review_ready=False
        self.page = self.browser.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.route('**/*', self.route)
        self.page.goto('https://remask.fixture/launch.php')
        self.page.wait_for_function('window.fixtureLaunch && document.querySelector("#bundleSelect option[value=fixture]") === null')
        self.page.wait_for_function('document.querySelector("#creativeLibrarySelect option[value=preset]") !== null')

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.page.close()

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if url.hostname != 'remask.fixture':
            route.abort(); return
        path = url.path.lstrip('/')
        if path == 'launch.php':
            html = re.sub(r'<\?(?:php|=).*?\?>', '', (self.root/path).read_text(), flags=re.S)
            # Check the deployed shell itself; never mask a broken dependency
            # in the browser fixture before asserting pageerror is empty.
            self.assertNotIn('styles/bootstrap.min.js', html)
            html=html.replace('<meta name="remask-csrf" content=""/>','<meta name="remask-csrf" content="fixture-csrf"/>',1)
            self.assertIn('content="fixture-csrf"',html)
            route.fulfill(body=html, content_type='text/html'); return
        file = self.root/path
        if file.is_file() and not path.endswith('.php'):
            body = file.read_bytes()
            if path == 'scripts/launch.js':
                body += b'\nwindow.fixtureSingleTarget=(account,business="654321")=>{state.profile="20";state.targets=[];state.accounts=[{id:account,business_id:business}];$("adAccount").innerHTML=`<option value="${account}" selected>${account}</option>`;$("adAccount").disabled=false;$("adAccount").dispatchEvent(new Event("change",{bubbles:true}));};'
                body += b'\nwindow.fixtureBulkTargets=()=>{state.targets=[{profile:"20",account_id:"act_123456",business_id:"654321"},{profile:"20",account_id:"act_234567",business_id:"765432"}];state.profile="20";state.targetBindingAssets.pages={};$("adAccount").innerHTML=state.targets.map(t=>`<option value="${t.account_id}" selected>${t.account_id}</option>`).join("");renderTargetBindings();};'
                body += b'\nwindow.fixtureLaunch={payload:()=>buildPayload(),effective:()=>remaskApplySavedMetaBuilderToPayload(buildPayload()),capture:()=>bundleFormState(),restore:applyBundleData,config:loadLaunchConfig,ready:()=>Boolean(state.launchConfig),geo:()=>{state.geo=[{type:"country",key:"US",country_code:"US",name:"United States"}];$("geoQuery").dispatchEvent(new Event("change",{bubbles:true}));},instagramTargets:()=>{state.targets=[{profile:"20",account_id:"act_123456",business_id:"654321"},{profile:"21",account_id:"act_234567",business_id:"765432"}];state.profile="20";state.targetBindingAssets.pages={"20|123456|DRAFT":[{id:"345678",instagram_business_account:{id:"456789"}}],"21|234567|DRAFT":[{id:"567890",instagram_business_account:{id:"678901"}}]};$("adAccount").innerHTML=state.targets.map(t=>`<option value="${t.account_id}" selected>${t.account_id}</option>`).join("");$("creativeFormat").value="INSTAGRAM_POST";renderTargetBindings();},reloadPages:force=>loadPages(force),binding:account=>({...bindingFor(account)}),rows:()=>remaskPrivateTargetRows(currentLaunchConfigForRequest())};'
            route.fulfill(body=body, content_type='application/javascript' if path.endswith('.js') else 'text/css'); return
        data = {}
        status = 200
        if path == 'ajax/metaLaunchConfig.php':
            if self.delay_config:
                self.waiting_config.append(route)
                return
            data = self.config
        elif path == 'ajax/metaPrivateSchema.php': data = self.schema
        elif path in ('ajax/metaTemplateAdvertisingHelper.php','ajax/metaAudienceEstimate.php','ajax/metaPlacementCapabilities.php','ajax/metaCreativePreview.php'):
            if request.headers.get('x-remask-csrf')!='fixture-csrf':
                route.fulfill(status=403,json={'ok':False,'error':{'message':'CSRF validation failed.'}});return
            fields=parse_qs(request.post_data or '');self.estimate_requests.append((path,fields))
            if self.estimate_failure:
                route.fulfill(status=409,json={'ok':False,'error':{'message':'Сессия недоступна. Настройки можно сохранить.'}});return
            scope={'profile':fields.get('profile',[''])[0],'account_id':fields.get('account_id',[''])[0]}
            data={'checked_live':True,'account_scope_verified':True,'transport':'private_http_h2',
                'template_account_bound':True,'estimate_ready':True,'lower_bound':1000,'upper_bound':5000,'currency':'USD',
                'forecast':{'available':True,'reach':1200,'actions':15,'budget_minor_units':500},
                'options':{'instagram_positions':['story']},'body':'<div>Fixture Meta preview</div>'}
            if path=='ajax/metaTemplateAdvertisingHelper.php':data['template_account_bound']=False
            else:data.update(scope)
        elif path == 'ajax/metaAssets.php':
            fields=parse_qs(request.post_data or '');self.asset_requests.append(fields)
            resource=fields['resource'][0];account=fields.get('account_id',[''])[0]
            if resource=='pages':
                if self._testMethodName=='test_page_access_failure_keeps_profile_pages_for_local_drafts' and account:
                    route.fulfill(status=409,json={'ok':False,'error':{'message':'Page access not confirmed'}});return
                if self._testMethodName=='test_old_page_read_cannot_overwrite_new_selected_account' and account=='act_123456':
                    self.waiting_pages.append(route);return
                data={'data':[{'id':'345678' if account.removeprefix('act_')=='123456' else ('555555' if account.removeprefix('act_')=='111111' else '567890'),'name':'Scoped Page'}]}
            else:data={'data':[]}
        elif path == 'ajax/metaAssetManager.php':
            fields=parse_qs(request.post_data or '');self.instagram_requests.append(fields)
            account=fields['account_id'][0];actor='456789' if account=='123456' else '678901'
            data={'profile':fields['profile'][0],'account_id':'999999' if self.instagram_wrong_scope else account,
                'resource':'instagram_media','checked_live':True,'account_scope_verified':True,'data':[
                    {'id':'789012','caption':'Fixture owned post','instagram_actor_id':actor},
                    {'id':'890123','caption':'Foreign actor post','instagram_actor_id':'999999'}]}
        elif path == 'ajax/pythonWorkerJobs.php':
            self.assertEqual(request.headers.get('x-remask-csrf'),'fixture-csrf')
            fields=json.loads(request.post_data or '{}');self.worker_requests.append(fields)
            action=fields.get('action')
            if action=='private_launch_plan':
                # Exercise the same associative JSON boundary as the installed
                # PHP bridge, then the real Python planner (no Meta requests).
                forwarded=json.loads(subprocess.check_output(['php','-r',
                    'echo json_encode(json_decode(stream_get_contents(STDIN),true),JSON_THROW_ON_ERROR);'],
                    input=json.dumps(fields),text=True))
                try:
                    plan=self.planner.plan(profile_id=forwarded['profile_id'],payload=forwarded)
                except Exception as error:
                    route.fulfill(status=422,json={'ok':False,'error':str(error)});return
                data={'plan':plan}
            elif action=='private_launch_contracts':
                data={'contracts':{step:{'configured':True,'active':True,'verification_configured':True}
                                   for step in ('CAMPAIGN','ADSET','CREATIVE','AD')}}
            elif action=='private_launch_review':
                # A deliberate independent server refusal proves the fixed UI
                # reaches review with its selected Page, without creating ads.
                data={'review':{'ready':self.review_ready}}
            else:
                self.fail('Unexpected worker operation in browser fixture: '+str(action))
            route.fulfill(json={'ok':True,**data});return
        elif path == 'ajax/metaJobsList.php': data = {'jobs': []}
        elif path == 'ajax/creativeLibrary.php':
            data = {'items': [{'id':'preset','name':'Fixture','format':'SINGLE','media_library_id':MEDIA_ID,
                'media':MEDIA,'message':'Fixture message','headline':'Fixture headline','destination_url':'https://example.com/',
                'meta_builder':{'campaign':{'daily_budget':500,'objective':self.preset_objective},'adset':self.preset_rules}}]}
            if self._testMethodName=='test_saved_preset_module_values_stay_removed_after_bundle_reload':
                data['items'][0]['meta_builder'].update(adset={'is_dynamic_creative':True},targeting={'user_device':['iphone 17'],'education_statuses':[1]},
                    creative={'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_IN'}}}})
        elif path in ('ajax/metaTemplateTargetingSearch.php','ajax/metaTargetingSearch.php'):
            if request.headers.get('x-remask-csrf')!='fixture-csrf':
                route.fulfill(status=403,json={'ok':False,'error':{'message':'CSRF validation failed.'}});return
            fields = parse_qs(request.post_data or '', keep_blank_values=True)
            self.lookup_requests.append(fields)
            if self.lookup_failure:
                route.fulfill(status=409,json={'ok':False,'error':{'code':'PRIVATE_HTTP_PROXY_UNAVAILABLE',
                    'message':'Прокси недоступен. Уже введённые настройки можно сохранить.'}})
                return
            kind = fields['type'][0]
            item = {'key':'US','name':'Fixture locations','type':'country','country_code':'US'} if kind == 'locations' else {
                'id':{'interests':'123456','behaviors':'654321','languages':'1000'}.get(kind,'345678'), 'name':'Fixture '+kind}
            data = {'data':[item], 'checked_live':True,'source':'private_meta_template_lookup','template_account_bound':False}
            if path=='ajax/metaTargetingSearch.php':
                data.update(profile=fields['profile'][0],account_id=fields['account_id'][0],resource=kind,
                    account_scope_verified=True,template_account_bound=True,source='ads_manager_private_http')
                if self._testMethodName=='test_selected_rk_searches_fresh_multiple_interests_and_preserves_ids_in_worker_plan':
                    data['data'].append({'id':'234567','name':'Fixture second interest'})
        elif path == 'ajax/metaBundles.php':
            fields = parse_qs(request.post_data or '')
            if fields.get('action') == ['save']:
                bundle = {'id':'fixture','name':fields['name'][0], 'data':json.loads(fields['data'][0]), 'updated_at':'2026-10-13T00:00:00Z'}
                self.bundles = [bundle]
                data['bundle'] = bundle
            data['bundles'] = self.bundles
        elif path == 'ajax/metaBundleMedia.php':
            if self.missing:
                status = 409
            else:
                ids = json.loads(parse_qs(url.query)['ids'][0])
                data = {'media':[{**MEDIA,'id':id, 'media_type':'video' if i else 'image'} for i,id in enumerate(ids)]}
                if self.foreign_bundle_media:
                    data['media'][0]['id'] = 'm-' + 'f' * 24
        route.fulfill(status=status, json={'ok':status==200,'data':data,'error':{'message':'MEDIA_LIBRARY_ITEM_UNAVAILABLE'}})

    def test_installed_ui_php_python_native_saved_draft_publish_restart(self):
        """Complete installed UI/real PHP bridge/real Python Launch against fake Meta HTTP."""
        import asyncio
        from dataclasses import replace
        from unittest.mock import AsyncMock, patch
        from tests import test_private_ads_launch as ads_fixtures
        from app.private_launch import PrivateLaunchStateStore

        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        # Match the exact RK/BM/Page IDs observed by the simulated native
        # sender; this is never substituted after collecting UI state.
        self.page.evaluate('fixtureSingleTarget("act_111111","222222")')
        self.page.locator('#page option[value="555555"]').wait_for(state='attached')
        self.page.locator('#page').select_option('555555')
        self.page.evaluate('fixtureLaunch.geo()')
        self.page.locator('#interestQuery').fill('Fixture')
        self.page.locator('#interestQuery').press('Enter')
        self.page.locator('#interestResults .search-item').first.click()
        self.page.locator('#remaskPrivateExecutionMode').select_option('DRAFT')
        rows=self.page.evaluate('fixtureLaunch.rows()')
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual((row['profile'],row['business_id'],row['ad_account_id'],row['page_id']),
                         ('20','222222','111111','555555'))
        self.assertEqual(row['launch']['base']['execution_mode'],'DRAFT')
        self.assertEqual(row['launch']['base']['creative']['media_library_id'],MEDIA_ID)
        self.assertEqual(row['launch']['base']['campaign']['objective'],'OUTCOME_TRAFFIC')
        self.assertIn('flexible_spec',row['launch']['base']['adset']['targeting'])
        builder=self.page.evaluate('fixtureLaunch.capture().meta_builder')

        # This is the installed MetaOfficialFields class used by the PHP job
        # endpoint. It must not change campaign, ad set, creative or targeting.
        php_source=(
            'require $argv[1];'
            '$row=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);'
            '$effective=MetaOfficialFields::applyBuilderToPayload($row["launch"]["base"],$row["builder"]);'
            '$effective=MetaOfficialFields::validateAndNormalizePayload($effective);'
            'echo json_encode($effective,JSON_THROW_ON_ERROR|JSON_UNESCAPED_UNICODE);'
        )
        prepared={**row,'builder':builder}
        result=subprocess.run(['php','-r',php_source,str(self.root/'classes/MetaOfficialFields.php')],
            input=json.dumps(prepared),text=True,capture_output=True,check=True)
        normalized=json.loads(result.stdout)
        self.assertEqual(normalized['adset']['targeting'],row['launch']['base']['adset']['targeting'])
        self.assertEqual(normalized['campaign']['objective'],'OUTCOME_TRAFFIC')
        self.assertEqual(normalized['creative']['media_library_id'],MEDIA_ID)
        payload={key:row[key] for key in ('business_id','ad_account_id','page_id')}
        payload.update(launch_key='installed-ui-traffic',
            launch={'base':normalized,'override':row['launch']['override']})

        async def exercise_real_engine():
            fixture=ads_fixtures.LaunchTests('test_existing_launch_entrypoint_uploads_image_publishes_and_verifies')
            await fixture.asyncSetUp()
            try:
                fixture.media=replace(fixture.media,id=MEDIA_ID)
                fixture.payload=payload
                async def execute():
                    return await fixture.owner.run(item_id='browser-ui',profile_id=row['profile'],
                        context=fixture.context,session=fixture.session,payload=fixture.payload)
                draft=await execute()
                self.assertTrue(draft['draft_verified'])
                self.assertFalse(draft['published'])
                self.assertEqual(len(fixture.client.fragments),3)
                self.assertEqual(len(fixture.client.posts),4)
                self.assertEqual(fixture.client.uploads,1)
                adset=next(r for r in fixture.client.fragments.values() if r['ad_object_type']=='ad_set')
                values={r['field']:r['new_value'] for r in adset['values']}
                self.assertEqual(values['targeting'],normalized['adset']['targeting'])
                self.assertEqual(values['campaign_id'],'777771')
                self.assertEqual(values['status'],'PAUSED')
                ad=next(r for r in fixture.client.fragments.values() if r['ad_object_type']=='ad')
                ad_values={r['field']:r['new_value'] for r in ad['values']}
                self.assertEqual(ad_values['adset_id'],'777772')
                self.assertEqual(ad_values['creative']['object_story_spec']['page_id'],'555555')
                self.assertEqual(ad_values['creative']['object_story_spec']['link_data']['message'],
                                 normalized['creative']['message'])
                fixture.payload['publish_saved_draft']=True
                published=await execute()
                self.assertTrue(published['published'])
                self.assertTrue(published['verified'])
                self.assertFalse(published['delivery_running_verified'])
                self.assertEqual(published['draft_id'],draft['draft_id'])
                self.assertEqual(len(fixture.client.posts),5)
                self.assertEqual(published['entities']['ad_id'],draft['draft_objects']['ad'])
                fixture.owner.state=PrivateLaunchStateStore(fixture.owner.state.path)
                recovered=await execute()
                self.assertTrue(recovered['verified'])
                self.assertEqual(recovered['entities'],published['entities'])
                self.assertEqual(len(fixture.client.posts),5)
                self.assertEqual(fixture.client.uploads,1)
            finally:
                fixture.doCleanups()
        asyncio.run(exercise_real_engine())

    def test_bundle_restore_rejects_foreign_media_without_checksums(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        data = self.page.evaluate('fixtureLaunch.capture()')
        self.assertNotIn('checksums',data['media'])
        self.foreign_bundle_media = True
        error = self.page.evaluate('(data)=>fixtureLaunch.restore(data).then(()=>"",error=>error.message)',data)
        self.assertIn('удалено или недоступно',error)
        self.assertIsNone(self.page.evaluate('fixtureLaunch.capture().media'))
        self.foreign_bundle_media = False
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',data)
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().media.media_library_id'),MEDIA_ID)

    def save_and_reload(self):
        self.page.locator('#bundleName').fill('Fixture bundle')
        self.page.locator('#bundleSave').click()
        self.page.wait_for_function('document.querySelector("#bundleSelect").value === "fixture"')
        self.page.reload()
        self.page.wait_for_function('document.querySelector("#bundleSelect option[value=fixture]") !== null')
        self.page.locator('#bundleSelect').select_option('fixture')
        self.page.locator('#bundleLoad').click()
        self.page.wait_for_function('document.querySelector("#launchResult").classList.contains("ready")')

    def test_launch_without_selected_account_does_not_probe_template_profiles(self):
        self.page.evaluate('fixtureLaunch.geo()')
        self.page.wait_for_function('document.querySelector("[data-rm-estimate-status]").textContent.includes("Выберите рекламный кабинет")')
        self.assertEqual(self.estimate_requests, [])
        self.save_and_reload()
        self.assertNotIn('forecast',self.bundles[0]['data'])

    def select_estimate_account(self):
        self.page.evaluate('fixtureSingleTarget("act_123456")')

    def test_launch_audience_sidebar_tracks_budget_and_survives_saved_bundle_reload(self):
        self.page.set_viewport_size({'width':1600,'height':1000})
        self.select_estimate_account()
        self.page.evaluate('fixtureLaunch.geo()')
        self.page.locator('#dailyBudget').fill('875')
        self.page.wait_for_function('document.querySelector("[data-rm-estimates]").textContent.includes("1 000")')
        request=self.estimate_requests[-1][1]
        self.assertEqual(request['daily_budget'],['875'])
        self.assertEqual(json.loads(request['targeting_spec'][0])['geo_locations']['countries'],['US'])
        self.assertEqual(self.page.locator('.launch-wrap>.rm-estimate-rail>#remaskAdvertisingEstimate').count(),1)
        rail=self.page.locator('.rm-estimate-rail').bounding_box()
        card=self.page.locator('#dailyBudget').locator('xpath=ancestor::*[contains(@class,"launch-card")]').bounding_box()
        self.assertGreaterEqual(rail['x'],card['x']+card['width'])
        self.save_and_reload()
        self.assertEqual(self.page.locator('#dailyBudget').input_value(),'875')
        self.assertEqual(self.page.locator('#remaskAdvertisingEstimate').count(),1)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.locator('#remaskAdvertisingEstimate').is_visible())
        # The installed theme rounds a fractional border to one CSS pixel.
        # Check the panel itself as well, so this allowance cannot hide overflow.
        panel=self.page.locator('#remaskAdvertisingEstimate').bounding_box()
        self.assertGreaterEqual(panel['x'],0)
        self.assertLessEqual(panel['x']+panel['width'],391)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),391)

    def test_launch_estimates_and_previews_each_selected_account_in_its_own_scope(self):
        self.page.evaluate('fixtureLaunch.geo();fixtureLaunch.instagramTargets()')
        for button in self.page.get_by_role('button',name='Загрузить публикации Instagram',exact=True).all():
            button.click()
        for select in self.page.get_by_role('combobox',name='Публикация Instagram для РК',exact=False).all():
            select.locator('option[value="789012"]').wait_for(state='attached')
            select.select_option('789012')
        self.page.wait_for_function('document.querySelectorAll("[data-rm-estimates] article").length===2')
        requests=[fields for path,fields in self.estimate_requests if path=='ajax/metaAudienceEstimate.php']
        self.assertEqual([(r['profile'][0],r['account_id'][0]) for r in requests],[('20','123456'),('21','234567')])
        self.page.locator('[data-rm-preview]').click()
        self.page.wait_for_function('document.querySelectorAll("[data-rm-preview-body] iframe").length===2')
        for frame in self.page.locator('[data-rm-preview-body] iframe').all():
            self.assertEqual(frame.get_attribute('sandbox'),'')
            self.assertIn('Fixture Meta preview',frame.get_attribute('srcdoc'))
        self.page.locator('#message').fill('Updated copy')
        self.assertEqual(self.page.locator('[data-rm-preview-body] iframe').count(),0)

    def test_launch_estimate_error_preserves_budget_and_targeting(self):
        self.select_estimate_account()
        self.estimate_failure=True;self.page.evaluate('fixtureLaunch.geo()')
        self.page.wait_for_function('document.querySelector("[data-rm-estimates]").textContent.includes("Размер аудитории временно недоступен")')
        self.assertEqual(self.page.evaluate('fixtureLaunch.payload().adset.targeting.geo_locations.countries'),['US'])

    def test_template_configuration_has_no_manual_audience_or_placement_read_buttons(self):
        self.assertEqual(self.page.locator('#remaskAdvertisingEstimate button').count(),0)
        self.page.locator('#placementMode').select_option('MANUAL')
        self.assertTrue(self.page.locator('#manualPlacements').is_visible())
        self.page.locator('#placementMode').select_option('AUTO')
        self.page.locator('[data-launch-meta-group="targeting"] > summary').click()
        self.page.locator('#launchMeta_targeting_instagram_positions_choices').fill('story')
        self.page.locator('#launchMeta_targeting_instagram_positions_choices').press('Enter')
        self.page.locator('#launchMeta_targeting_instagram_positions_choices_results').get_by_role('button',name='Story',exact=True).click()
        self.assertEqual(self.page.locator('#placementMode').input_value(),'MANUAL')
        self.assertTrue(self.page.locator('.publisher-platform[value="instagram"]').is_checked())
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.targeting.instagram_positions'),['story'])
        self.save_and_reload()
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.targeting.instagram_positions'),['story'])
        self.assertFalse(any(fields.get('helper')==['placements'] for _,fields in self.estimate_requests))

    def test_all_module_choices_enter_launch_payload_and_bundle_round_trip(self):
        self.page.locator('[data-launch-meta-group="targeting"] > summary').click()
        self.page.locator('#launchMeta_targeting_user_device_choices').fill('iphone 17')
        self.page.locator('#launchMeta_targeting_user_device_choices').press('Enter')
        self.page.get_by_role('button',name='iphone 17',exact=True).click()
        self.page.locator('#launchMeta_targeting_education_statuses_choices').press('Enter')
        self.page.get_by_role('button',name='High School',exact=True).click()
        self.page.locator('#launchMeta_targeting_work_positions_choices').fill('Fixture')
        self.page.locator('#launchMeta_targeting_work_positions_choices').press('Enter')
        self.page.get_by_role('button',name='Fixture work_positions',exact=True).click()
        for field in ('user_device','education_statuses','work_positions'):
            self.assertFalse(self.page.locator('#launchMeta_targeting_'+field).is_visible())
        data=self.page.evaluate('fixtureLaunch.capture()')
        target=data['meta_builder']['targeting']
        self.assertEqual(target['user_device'],['iphone 17'])
        self.assertEqual(target['education_statuses'],[1])
        self.assertEqual(target['work_positions'],[{'id':'345678','name':'Fixture work_positions'}])
        self.assertEqual(self.lookup_requests[-1]['type'],['work_positions'])
        self.assertNotIn('profile',self.lookup_requests[-1])
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',data)
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().meta_builder.targeting'),target)
        self.assertEqual(self.page.get_by_role('button',name='Удалить iphone 17').count(),1)

    def test_generic_instagram_template_bundle_saves_without_destination_or_post(self):
        self.page.locator('#creativeFormat').select_option('INSTAGRAM_POST')
        self.page.locator('#dailyBudget').fill('500')
        self.save_and_reload()
        self.assertEqual(self.page.locator('#creativeFormat').input_value(),'INSTAGRAM_POST')
        self.assertEqual(self.page.locator('#instagramMediaId').input_value(),'')
        self.assertEqual(self.instagram_requests,[])
        self.assertNotIn('profile',self.bundles[0]['data'])

    def test_saved_preset_module_values_stay_removed_after_bundle_reload(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.locator('[data-launch-meta-group="targeting"] > summary').click()
        self.page.get_by_role('button',name='Удалить iphone 17',exact=True).click()
        self.page.get_by_role('button',name='Удалить High School',exact=True).click()
        self.page.locator('[data-launch-meta-group="adset"] > summary').click()
        self.page.locator('#launchMeta_adset_is_dynamic_creative').select_option('')
        self.page.locator('[data-launch-meta-group="creative"] > summary').click()
        self.page.locator('#launchMeta_creative_degrees_of_freedom_spec').locator('..').locator('.rm-module-structure').get_by_role('button',name='Очистить',exact=True).click()
        self.save_and_reload()
        data=self.page.evaluate('fixtureLaunch.capture()')
        self.assertNotIn('user_device',data['meta_builder']['targeting'])
        self.assertNotIn('education_statuses',data['meta_builder']['targeting'])
        self.assertNotIn('degrees_of_freedom_spec',data['meta_builder']['creative'])
        self.assertNotIn('is_dynamic_creative',data['meta_builder']['adset'])
        self.assertEqual(data['meta_builder']['campaign']['daily_budget'],500)
        self.assertEqual(data['media']['media_library_id'],MEDIA_ID)

    def test_module_parameters_without_native_controls_are_rendered(self):
        for group,fields in {'campaign':['buying_type','spend_cap','bid_strategy'],
                'adset':['attribution_spec','is_dynamic_creative'],
                'targeting':['excluded_geo_locations','flexible_spec','exclusions','genders'],
                'creative':['degrees_of_freedom_spec','asset_feed_spec','platform_customizations'],
                'ad':['conversion_domain','priority','tracking_specs']}.items():
            for field in fields:
                self.assertEqual(self.page.locator('#launchMeta_'+group+'_'+field).count(),1,(group,field))

    def test_instagram_posts_selected_per_rk_and_foreign_actor_filtered(self):
        self.page.evaluate('fixtureLaunch.instagramTargets()')
        self.page.get_by_role('button',name='Загрузить публикации Instagram').first.click()
        select=self.page.get_by_role('combobox',name='Публикация Instagram для РК 123456')
        select.locator('option[value="789012"]').wait_for(state='attached')
        self.assertEqual(select.locator('option[value="890123"]').count(),0)
        select.select_option('789012')
        self.assertEqual(self.page.evaluate('fixtureLaunch.binding("123456").source_instagram_media_id'),'789012')
        self.assertEqual(self.page.evaluate('fixtureLaunch.binding("234567").source_instagram_media_id'),'')
        self.assertEqual(self.instagram_requests[0]['profile'],['20'])
        self.assertEqual(self.instagram_requests[0]['account_id'],['123456'])
        self.assertEqual(self.instagram_requests[0]['force'],['1'])

    def test_instagram_wrong_rk_response_never_supplies_choices(self):
        self.instagram_wrong_scope=True
        self.page.evaluate('fixtureLaunch.instagramTargets()')
        self.page.get_by_role('button',name='Загрузить публикации Instagram').first.click()
        self.page.get_by_text('Не удалось загрузить публикации этого РК.',exact=False).wait_for()
        self.assertEqual(self.page.get_by_role('combobox',name='Публикация Instagram для РК 123456').locator('option').count(),1)
        self.assertEqual(self.instagram_requests[0]['account_id'],['123456'])
        self.assertEqual(self.page.evaluate('fixtureLaunch.binding("123456").source_instagram_media_id'),'')

    def test_preset_media_and_edited_budget_survive_save_reload(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.locator('#dailyBudget').fill('700')
        self.page.locator('#campaignName').fill('Edited campaign')
        self.save_and_reload()
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '700')
        self.assertEqual(self.page.locator('#campaignName').input_value(), 'Edited campaign')
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().media.media_library_id'), MEDIA_ID)
        self.page.evaluate('fixtureLaunch.geo()')
        payload = self.page.evaluate('fixtureLaunch.payload()')
        self.assertEqual(payload['campaign'].get('daily_budget', payload['adset'].get('daily_budget')), 700)
        self.page.locator('#clearCreativeLibrary').click()
        self.assertIsNone(self.page.evaluate('fixtureLaunch.capture().media'))

    def test_lifetime_budget_keeps_period_and_end_time(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.locator('#budgetPeriod').select_option('LIFETIME')
        self.page.locator('#dailyBudget').fill('2000')
        self.page.locator('#endTime').fill('2026-11-01T18:00')
        self.save_and_reload()
        self.assertEqual(self.page.locator('#budgetPeriod').input_value(), 'LIFETIME')
        self.assertEqual(self.page.locator('label[for="dailyBudget"]').inner_text(), 'Бюджет на весь срок')
        self.page.evaluate('fixtureLaunch.geo()')
        payload = self.page.evaluate('fixtureLaunch.payload()')
        section = payload['campaign'] if 'lifetime_budget' in payload['campaign'] else payload['adset']
        self.assertEqual(section['lifetime_budget'], 2000)
        self.assertNotIn('daily_budget', section)
        self.assertTrue(payload['adset']['end_time'])

    def test_missing_media_does_not_reuse_previous_preset(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.missing = True
        with self.assertRaises(Exception):
            self.page.evaluate('(data)=>fixtureLaunch.restore(data)', {'fields':{},'media':{'format':'SINGLE','media_library_id':MEDIA_ID}})
        self.assertIsNone(self.page.evaluate('fixtureLaunch.capture().media'))

    def test_mixed_carousel_restores_order_and_card_fields(self):
        second = 'm-' + 'c' * 24
        snapshot = {'fields':{'creativeFormat':'CAROUSEL'},'media':{'format':'CAROUSEL','carousel':[
            {'media_library_id':MEDIA_ID,'headline':'First','link':'https://example.com/one'},
            {'media_library_id':second,'headline':'Second','link':'https://example.com/two'}]}}
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)', snapshot)
        media = self.page.evaluate('fixtureLaunch.capture().media')
        self.assertEqual([x['media_library_id'] for x in media['carousel']], [MEDIA_ID,second])
        self.assertEqual([x['headline'] for x in media['carousel']], ['First','Second'])

    def test_launch_controls_expose_missing_account_on_click(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.wait_for_selector('#remaskPrivateDisabledReason:not([hidden])')
        reason = self.page.locator('#remaskPrivateDisabledReason')
        self.assertTrue(reason.is_visible())
        self.assertIn('Select at least one ad account.', reason.inner_text())
        for button_id in ('launchButton', 'reviewLaunch', 'dryRunPlan'):
            button = self.page.locator('#' + button_id)
            self.assertTrue(button.is_enabled())
            self.assertIn('Select at least one ad account.', button.get_attribute('title'))
        self.page.locator('#reviewLaunch').click()
        self.page.wait_for_function(
            'document.querySelector("#launchResult").textContent.includes("Select at least one ad account.")')
        self.assertTrue(self.page.locator('#launchButton').is_enabled())

    def test_launch_controls_expose_unavailable_goal_on_click(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.evaluate("fixtureSingleTarget('123456')")
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',
            {'fields': {'objective': 'LEGACY_TEST_OBJECTIVE'}})
        self.page.wait_for_selector('#remaskPrivateDisabledReason:not([hidden])')
        reason = self.page.locator('#remaskPrivateDisabledReason')
        self.assertTrue(reason.is_visible())
        self.assertIn('Сохранённая цель недоступна', reason.inner_text())
        self.assertTrue(self.page.locator('#launchButton').is_enabled())
        self.assertTrue(self.page.locator('#reviewLaunch').is_enabled())
        self.page.locator('#reviewLaunch').click()
        self.page.wait_for_function(
            'document.querySelector("#launchResult").textContent.includes("Сохранённая цель недоступна")')
        self.assertTrue(self.page.locator('#launchButton').is_enabled())

    def test_legacy_preset_goal_survives_bundle_reload_until_user_selects_supported_goal(self):
        self.preset_objective = 'LEGACY_TEST_OBJECTIVE'
        self.page.reload()
        self.page.wait_for_function('document.querySelector("#creativeLibrarySelect option[value=preset]") !== null')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.assertTrue(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.assertTrue(self.page.locator('#launchButton').is_enabled())
        self.assertTrue(self.page.locator('#reviewLaunch').is_enabled())
        self.page.evaluate('fixtureLaunch.geo()')
        with self.assertRaisesRegex(Exception, 'Сохранённая цель недоступна'):
            self.page.evaluate('fixtureLaunch.payload()')
        self.page.locator('#dailyBudget').fill('700')
        self.save_and_reload()
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '700')
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().fields.objective'), self.preset_objective)
        self.page.evaluate('fixtureLaunch.config()')
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.page.locator('#objective').select_option('OUTCOME_TRAFFIC')
        self.assertFalse(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.payload().campaign.objective'), 'OUTCOME_TRAFFIC')
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '700')

    def test_disabled_configured_goal_is_retained_in_restored_bundle(self):
        disabled = next((key for key,spec in self.config['objectives'].items() if not spec.get('enabled')), None)
        self.assertIsNotNone(disabled)
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',
            {'fields':{'objective':disabled,'dailyBudget':'800','campaignName':'Keep name'}})
        self.assertEqual(self.page.locator('#objective').input_value(), disabled)
        self.assertTrue(self.page.locator('#dryRunPlan').is_enabled())
        self.assertTrue(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.assertEqual(self.page.locator('#campaignName').input_value(), 'Keep name')
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '800')

    def test_all_common_audience_searches_work_without_publishing_profile_or_account(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        for field, results in (('geoQuery','geoResults'), ('interestQuery','interestResults'),
                               ('behaviorQuery','behaviorResults'), ('languageQuery','languageResults')):
            self.page.locator('#'+field).fill('Fixture')
            self.page.locator('#'+field).press('Enter')
            self.page.locator('#'+results+' .search-item').first.click()
        self.assertEqual({row['type'][0] for row in self.lookup_requests},
                         {'locations','interests','behaviors','languages'})
        for row in self.lookup_requests:
            self.assertEqual(set(row), {'type','q','limit'})
        self.assertEqual(self.page.locator('#profile option:checked').count(), 0)
        self.save_and_reload()
        saved = self.page.evaluate('fixtureLaunch.capture()')
        self.assertEqual(saved['geo'][0]['country_code'], 'US')
        self.assertEqual(saved['interests'][0]['id'], '123456')
        self.assertEqual(saved['behaviors'][0]['id'], '654321')
        self.assertEqual(int(saved['languages'][0]['id']), 1000)
        payload = self.page.evaluate('fixtureLaunch.payload()')
        self.assertEqual(payload['adset']['targeting']['geo_locations']['countries'], ['US'])
        self.assertEqual(payload['adset']['targeting']['locales'], [1000])
        self.assertNotIn('account_id', payload['adset']['targeting'])
        self.assertEqual(self.page.locator('#profile option:checked').count(), 0)

    def test_blank_focus_choices_survive_legacy_search_cleanup_and_keyboard_selection(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        for field,result in [('geoQuery','geoResults'),('languageQuery','languageResults')]:
            self.page.locator('#'+field).focus()
            option=self.page.locator('#'+result+' button[data-remask-target-choice]').first
            option.wait_for(state='visible')
            option.press('Enter')
        self.assertTrue(all(row.get('q')==[''] for row in self.lookup_requests))
        payload=self.page.evaluate('fixtureLaunch.payload()')
        self.assertEqual(payload['adset']['targeting']['geo_locations']['countries'],['US'])
        self.assertEqual(payload['adset']['targeting']['locales'],[1000])

    def test_audience_connection_error_keeps_media_budget_and_saved_selections(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.locator('#dailyBudget').fill('700')
        self.page.locator('#geoQuery').fill('Fixture')
        self.page.locator('#geoQuery').press('Enter')
        self.page.locator('#geoResults .search-item').first.click()
        self.lookup_failure = True
        self.page.locator('#interestQuery').fill('Fixture')
        self.page.locator('#interestQuery').press('Enter')
        self.page.wait_for_function('document.querySelector("#interestResults").textContent.includes("Прокси недоступен")')
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '700')
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().media.media_library_id'), MEDIA_ID)
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().geo[0].country_code'), 'US')
        self.save_and_reload()
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '700')
        self.assertEqual(self.page.evaluate('fixtureLaunch.capture().geo[0].country_code'), 'US')

    def release_config(self):
        self.assertTrue(self.waiting_config)
        self.delay_config = False
        for route in self.waiting_config:
            route.fulfill(json={'ok':True,'data':self.config})
        self.waiting_config = []
        self.page.wait_for_function('fixtureLaunch.ready()')

    def test_initial_config_arriving_after_preset_has_no_false_warning(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertFalse(self.page.evaluate('fixtureLaunch.ready()'))
        self.assertEqual(self.page.locator('#objective').input_value(), 'OUTCOME_TRAFFIC')
        self.assertFalse(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.release_config()
        self.assertEqual(self.page.locator('#objective').input_value(), 'OUTCOME_TRAFFIC')
        self.assertFalse(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '500')
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.payload().campaign.objective'), 'OUTCOME_TRAFFIC')

    def test_initial_config_keeps_legacy_goal(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.assertFalse(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.release_config()
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.assertTrue(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '500')
        with self.assertRaisesRegex(Exception, 'Сохранённая цель недоступна'):
            self.page.evaluate('fixtureLaunch.payload()')

    def test_initial_config_restores_dependent_preset_rules(self):
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.release_config()
        self.assertEqual(self.page.locator('#optimizationGoal').input_value(),'LINK_CLICKS')
        self.assertEqual(self.page.locator('#billingEvent').input_value(),'IMPRESSIONS')
        self.assertEqual(self.page.locator('#bidStrategy').input_value(),'LOWEST_COST_WITH_BID_CAP')
        self.assertEqual(self.page.locator('#bidAmount').input_value(),'250')
        self.assertTrue(self.page.locator('#bidAmount').is_enabled())
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.optimization_goal'),'LINK_CLICKS')
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.bid_amount'),250)
        self.page.locator('#optimizationGoal').select_option('LANDING_PAGE_VIEWS')
        self.page.locator('#bidStrategy').select_option('LOWEST_COST_WITHOUT_CAP')
        self.page.evaluate('fixtureLaunch.config()')
        self.assertEqual(self.page.locator('#optimizationGoal').input_value(),'LANDING_PAGE_VIEWS')
        self.assertEqual(self.page.locator('#bidStrategy').input_value(),'LOWEST_COST_WITHOUT_CAP')

    def test_preset_changes_dependent_goal_after_config_is_loaded(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertEqual(self.page.locator('#objective').input_value(),'OUTCOME_SALES')
        self.assertEqual(self.page.locator('#optimizationGoal').input_value(),'OFFSITE_CONVERSIONS')
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.optimization_goal'),'OFFSITE_CONVERSIONS')

    def test_unsupported_optimization_is_preserved_until_user_replaces_it(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertEqual(self.page.locator('#optimizationGoal').input_value(),'LEGACY_TEST_OPTIMIZATION')
        with self.assertRaisesRegex(Exception,'Сохранённая оптимизация'):
            self.page.evaluate('fixtureLaunch.payload()')
        self.page.locator('#optimizationGoal').select_option('LINK_CLICKS')
        self.page.evaluate('fixtureLaunch.geo()')
        self.assertEqual(self.page.evaluate('fixtureLaunch.effective().adset.optimization_goal'),'LINK_CLICKS')

    def test_initial_config_restores_bundle_rules_without_a_preset(self):
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',{'fields':{'objective':'OUTCOME_TRAFFIC',
            'optimizationGoal':'LINK_CLICKS','billingEvent':'IMPRESSIONS','bidStrategy':'COST_CAP','bidAmount':'300','dailyBudget':'700'}})
        self.release_config()
        self.assertEqual(self.page.locator('#optimizationGoal').input_value(),'LINK_CLICKS')
        self.assertEqual(self.page.locator('#bidStrategy').input_value(),'COST_CAP')
        self.assertEqual(self.page.locator('#bidAmount').input_value(),'300')
        self.assertEqual(self.page.locator('#dailyBudget').input_value(),'700')

    def test_selected_account_loads_its_pages_automatically(self):
        self.page.evaluate('fixtureSingleTarget("act_123456")')
        self.page.locator('#page option[value="345678"]').wait_for(state='attached')
        self.assertTrue(self.page.locator('#page').is_enabled())
        pages=[fields for fields in self.asset_requests if fields['resource']==['pages']]
        self.assertEqual(pages[-1]['profile'],['20'])
        self.assertEqual(pages[-1]['account_id'],['act_123456'])
        self.assertEqual(pages[-1].get('execution_mode'),['DRAFT'])
        self.page.locator('#remaskPrivateExecutionMode').select_option('PUBLISH')
        self.page.evaluate('fixtureLaunch.reloadPages(true)')
        pages=[fields for fields in self.asset_requests if fields['resource']==['pages']]
        self.assertEqual(pages[-1].get('execution_mode'),['PUBLISH'])
        self.assertEqual(pages[-1]['account_id'],['act_123456'])

    def test_workspace_bulk_pages_use_exact_rk_and_mode_on_the_same_profile(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.evaluate('fixtureBulkTargets()')
        self.page.locator('#loadTargetBindings').click()
        self.page.locator('#targetBindingRows tr').nth(0).locator('select').first.locator('option[value="345678"]').wait_for(state='attached')
        self.page.locator('#targetBindingRows tr').nth(1).locator('select').first.locator('option[value="567890"]').wait_for(state='attached')
        pages = [fields for fields in self.asset_requests if fields['resource'] == ['pages']]
        self.assertEqual({row['account_id'][0] for row in pages}, {'123456','234567'})
        self.assertTrue(all(row['profile'] == ['20'] and row['execution_mode'] == ['DRAFT'] for row in pages))
        self.assertEqual(self.page.locator('#targetBindingRows tr').nth(0).locator('option[value="567890"]').count(), 0)
        self.assertEqual(self.page.locator('#targetBindingRows tr').nth(1).locator('option[value="345678"]').count(), 0)
        self.page.locator('#remaskPrivateExecutionMode').select_option('PUBLISH')
        self.page.locator('#loadTargetBindings').click()
        self.page.wait_for_function('!document.querySelector("#loadTargetBindings").disabled')
        pages = [fields for fields in self.asset_requests if fields['resource'] == ['pages']]
        self.assertEqual([row['execution_mode'] for row in pages[-2:]], [['PUBLISH'], ['PUBLISH']])
        self.assertEqual({row['account_id'][0] for row in pages[-2:]}, {'123456','234567'})

    def test_page_access_failure_keeps_profile_pages_for_local_drafts(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.evaluate('fixtureSingleTarget("act_123456")')
        self.page.locator('#page option[value="567890"]').wait_for(state='attached')
        self.assertIn('доступ не проверен',self.page.locator('#page option[value="567890"]').inner_text())
        self.page.locator('#page').select_option('567890')
        self.page.evaluate('fixtureLaunch.geo()')
        self.page.locator('#dryRunPlan').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("План подготовлен для 1 РК")')
        self.page.locator('#reviewLaunch').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("Проверка Meta: 0/1")')
        self.assertEqual(next(row for row in self.worker_requests if row['action']=='private_launch_review')['page_id'],'567890')
        self.assertFalse(any(row['action']=='create' for row in self.worker_requests))

        summary=self.page.locator('#remaskPrivateAccountReviews').inner_text()
        self.assertIn('123456',summary)
        self.assertIn('ACCOUNT_NOT_READY',summary)
        self.save_and_reload()
        self.assertEqual(self.bundles[0]['data']['assets']['page'],'567890')

    def prepare_single_page_launch(self):
        self.page.wait_for_function('fixtureLaunch.ready()')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.page.evaluate('fixtureSingleTarget("act_123456")')
        self.page.locator('#page option[value="345678"]').wait_for(state='attached')
        self.page.locator('#page').select_option('345678')
        self.page.evaluate('fixtureLaunch.geo()')
        self.page.locator('#launchDeliverySelect').select_option('PAUSED')

    def test_selected_rk_searches_fresh_multiple_interests_and_preserves_ids_in_worker_plan(self):
        self.prepare_single_page_launch()
        for name in ('Fixture interests','Fixture second interest'):
            self.page.locator('#interestQuery').fill('Fixture')
            self.page.locator('#interestQuery').press('Enter')
            self.page.locator('#interestResults').get_by_role('button',name=name,exact=False).click()
        lookup=self.lookup_requests[-1]
        self.assertEqual(lookup['profile'],['20'])
        self.assertEqual(lookup['account_id'],['123456'])
        self.assertEqual(lookup['execution_mode'],['DRAFT'])
        self.page.locator('#dryRunPlan').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("План подготовлен для 1 РК")')
        plan=next(row for row in self.worker_requests if row['action']=='private_launch_plan')
        interests=plan['launch']['base']['adset']['targeting']['flexible_spec'][0]['interests']
        self.assertEqual([item['id'] for item in interests],['123456','234567'])
        self.assertFalse(any(row['action']=='create' for row in self.worker_requests))

    def test_nested_targeting_placements_schedule_and_copy_survive_bundle_and_worker_plan(self):
        self.prepare_single_page_launch()
        snapshot=self.page.evaluate('fixtureLaunch.capture()')
        nested=[{'interests':[{'id':'123456','name':'First'},{'id':'234567','name':'Second'}]},
            {'behaviors':[{'id':'654321','name':'Behavior'}],
             'education_statuses':[1,2]}]
        extra={'genders':[1],'flexible_spec':nested,'exclusions':{'interests':[{'id':'345678','name':'Excluded'}]},
            'excluded_geo_locations':{'countries':['RU']},'device_platforms':['mobile'],
            'facebook_positions':['feed'],'instagram_positions':['story'],'user_os':['Android']}
        snapshot['meta_builder']['targeting']=extra
        snapshot['meta_builder']['adset']={'attribution_spec':[{'event_type':'CLICK_THROUGH','window_days':1}]}
        snapshot['fields'].update(ageMin='25',ageMax='55',placementMode='MANUAL',
            startTime='2026-11-01T09:00',endTime='2026-11-07T18:30',
            message='Primary text ✓',headline='Headline ✓',description='Description ✓',
            destinationUrl='https://example.com/selected?a=1',urlTags='utm_source=selected',cta='LEARN_MORE')
        snapshot['publisherPlatforms']=['facebook','instagram']
        snapshot['languages']=[{'id':'52','name':'Ukrainian'},{'id':'1000','name':'Second language'}]
        snapshot['geo']=[{'type':'country','key':'UA','country_code':'UA'},
            {'type':'region','key':'123456'},{'type':'city','key':'234567'}]
        self.page.evaluate('(data)=>fixtureLaunch.restore(data)',snapshot)
        self.save_and_reload()
        self.page.evaluate('fixtureSingleTarget("act_123456")')
        self.page.locator('#page option[value="345678"]').wait_for(state='attached')
        self.page.locator('#page').select_option('345678')
        self.page.locator('#dryRunPlan').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("План подготовлен для 1 РК")')
        base=next(row for row in self.worker_requests if row['action']=='private_launch_plan')['launch']['base']
        target=base['adset']['targeting']
        for field,value in extra.items():self.assertEqual(target[field],value,field)
        self.assertEqual(target['locales'],[52,1000])
        self.assertEqual((target['age_min'],target['age_max']),(25,55))
        self.assertEqual(target['publisher_platforms'],['facebook','instagram'])
        self.assertEqual(target['geo_locations'],{'countries':['UA'],'regions':[{'key':'123456'}],'cities':[{'key':'234567'}]})
        self.assertEqual(base['campaign']['objective'],'OUTCOME_TRAFFIC')
        self.assertEqual(base['campaign']['daily_budget'],500)
        self.assertEqual(base['adset']['attribution_spec'],snapshot['meta_builder']['adset']['attribution_spec'])
        self.assertTrue(base['adset']['start_time'].startswith('2026-11-01T09:00:'))
        self.assertTrue(base['adset']['end_time'].startswith('2026-11-07T18:30:'))
        for field,value in {'message':'Primary text ✓','headline':'Headline ✓','description':'Description ✓',
                'link':'https://example.com/selected?a=1','url_tags':'utm_source=selected','call_to_action':'LEARN_MORE',
                'page_id':'345678'}.items():self.assertEqual(base['creative'][field],value,field)
        self.assertFalse(any(row['action']=='create' for row in self.worker_requests))

    def test_single_rk_selected_page_reaches_plan_and_independent_review(self):
        self.prepare_single_page_launch()
        self.page.locator('#dryRunPlan').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("План подготовлен для 1 РК")')
        plan=next(row for row in self.worker_requests if row['action']=='private_launch_plan')
        self.assertEqual((plan['profile_id'],plan['ad_account_id'],plan['page_id']),('20','123456','345678'))
        self.assertEqual(plan['launch']['base']['creative']['page_id'],'345678')
        self.assertNotIn('official_params',plan['launch']['base']['creative'])
        self.page.locator('#reviewLaunch').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("Проверка Meta: 0/1")')
        review=next(row for row in self.worker_requests if row['action']=='private_launch_review')
        self.assertEqual(review['page_id'],'345678')
        self.assertNotIn('При потере ответа',self.page.locator('#launchResult').inner_text())
        self.assertFalse(any(row['action']=='create' for row in self.worker_requests))

    def test_explicit_meta_readiness_can_succeed_in_both_modes_without_creating_a_job(self):
        self.prepare_single_page_launch()
        self.review_ready=True
        self.assertEqual(self.page.locator('#reviewLaunch').inner_text(),'Проверить готовность в Meta')
        for mode in ('DRAFT','PUBLISH'):
            with self.subTest(mode=mode):
                self.page.locator('#remaskPrivateExecutionMode').select_option(mode)
                before=self.page.evaluate('fixtureLaunch.capture()')
                self.worker_requests.clear()
                self.page.locator('#reviewLaunch').click()
                self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("Проверка Meta: 1/1")')
                review=next(row for row in self.worker_requests if row['action']=='private_launch_review')
                self.assertEqual((review['profile_id'],review['ad_account_id'],review['page_id']),('20','123456','345678'))
                self.assertEqual(review['launch']['base'].get('execution_mode'),'DRAFT' if mode=='DRAFT' else None)
                self.assertTrue(all(row['action'] in ('private_launch_contracts','private_launch_review') for row in self.worker_requests))
                self.assertEqual(self.page.evaluate('localStorage.getItem("remask_private_launch_pending_v1")'),None)
                self.assertEqual(self.page.evaluate('fixtureLaunch.capture()'),before)
                self.assertIn('публикация и редактор Meta не подтверждены',self.page.locator('#launchResult').inner_text())

    def test_meta_draft_and_publication_are_explicit_separate_operations(self):
        self.prepare_single_page_launch()
        self.assertEqual(self.page.locator('#remaskPrivateExecutionMode').input_value(), 'DRAFT')
        self.assertIn('Без публикации и проверки карты', self.page.locator('#remaskPrivateExecutionNote').inner_text())
        with self.page.expect_request(lambda request:request.url.endswith('/ajax/pythonWorkerJobs.php')
                and request.post_data_json.get('action')=='private_launch_review') as draft_request:
            self.page.locator('#launchButton').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("Проверка не подтвердила готовность")')
        draft = draft_request.value.post_data_json
        self.assertEqual(draft['launch']['base']['execution_mode'], 'DRAFT')
        self.page.locator('#remaskPrivateExecutionMode').select_option('PUBLISH')
        self.assertIn('Публикация требует', self.page.locator('#remaskPrivateExecutionNote').inner_text())
        with self.page.expect_request(lambda request:request.url.endswith('/ajax/pythonWorkerJobs.php')
                and request.post_data_json.get('action')=='private_launch_review') as publication_request:
            self.page.locator('#launchButton').click()
        published = publication_request.value.post_data_json
        self.assertNotIn('execution_mode', published['launch']['base'])
        self.assertFalse(any(row['action']=='create' for row in self.worker_requests))

    def test_single_rk_cleared_page_cannot_reuse_payload_or_create_job(self):
        self.prepare_single_page_launch()
        self.page.locator('#page').select_option('')
        self.worker_requests.clear()
        self.page.locator('#launchButton').click()
        self.page.wait_for_function('document.querySelector("#launchResult").textContent.includes("Выберите страницу для РК 123456")')
        message=self.page.locator('#launchResult').inner_text()
        self.assertNotIn('..',message)
        self.assertNotIn('При потере ответа',message)
        self.assertEqual(self.worker_requests,[])

    def test_old_page_read_cannot_overwrite_new_selected_account(self):
        self.page.evaluate('fixtureSingleTarget("act_123456")')
        self.page.wait_for_timeout(50)
        self.assertEqual(len(self.waiting_pages),1)
        self.page.evaluate('fixtureSingleTarget("act_234567")')
        self.page.locator('#page option[value="567890"]').wait_for(state='attached')
        self.waiting_pages[0].fulfill(json={'ok':True,'data':{'data':[{'id':'345678','name':'Old Page'}]}})
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.locator('#page option[value="345678"]').count(),0)
        self.assertEqual(self.page.locator('#page option[value="567890"]').count(),1)

if __name__ == '__main__': unittest.main()
