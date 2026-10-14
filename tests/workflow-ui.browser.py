"""Offline browser regression for the real Creative serializer and UI adapter."""
import json
from pathlib import Path
import re
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSET = (ROOT / 'railway-workflow-ui.js').read_text()
SCHEMA = {
    'campaign': {'fields': {'adlabels':'list<Object>'}, 'enums': {
        'objective':['OUTCOME_TRAFFIC','OUTCOME_SALES','DEPRECATED_CLICKS','WILDCARD_INTERNAL_ONLY','OUTCOME_ACQUISITION','OUTCOME_APP_PROMOTION'], 'special_ad_categories':['NONE'],
        'bid_strategy':['LOWEST_COST_WITHOUT_CAP'], 'status':['PAUSED','ACTIVE']}},
    'adset': {'fields': {}, 'enums': {'optimization_goal':['LINK_CLICKS'],
        'billing_event':['IMPRESSIONS'], 'bid_strategy':['LOWEST_COST_WITHOUT_CAP'], 'status':['PAUSED']}},
    'targeting': {'fields': {}, 'enums': {}},
    'creative': {'fields': {}, 'enums': {}}, 'ad': {'fields': {}, 'enums': {'status':['PAUSED']}}
}


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width':1440,'height':1000})
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error:self.errors.append(str(error)))
        # Every test is hermetic. No Facebook or production request is allowed.
        self.page.route('**/*', lambda route:route.abort())

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.page.close()

    def creative(self, enhanced=True, context=False, preferred="", modules=False):
        html = re.sub(r'<\?php.*?\?>', '', (ROOT / 'railway-v100-creatives.php').read_text(), flags=re.S)
        html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
        html = re.sub(r'<link\b[^>]*>', '', html)
        # Real runtime sidebar has z-index 1000 and overlaps wide editors if
        # the modal keeps the old 900 layer. Reproduce that shell in fixtures.
        html = html.replace('<body>', '<body><aside id="fixtureSidebar" style="position:fixed;left:0;top:0;width:201px;height:100vh;z-index:1000;background:#1a212c">Sidebar</aside>')
        if 'fixtureSidebar' not in html:
            html = re.sub(r'(<body\b[^>]*>)', r'\1<aside id="fixtureSidebar" style="position:fixed;left:0;top:0;width:201px;height:100vh;z-index:1000;background:#1a212c">Sidebar</aside>', html, count=1)
        self.page.set_content(html)
        module_metadata={}
        if modules:
            import sys,copy
            sys.path.insert(0,str(ROOT/'python_backend'))
            from app.editor_modules import form_schema
            from app.private_ads_catalog import AdsManagerRouteCatalog
            module_metadata=form_schema(AdsManagerRouteCatalog())
            module_metadata['schema']['campaign']['enums']['objective']=SCHEMA['campaign']['enums']['objective']
        schema=module_metadata.get('schema',SCHEMA)
        if context:
            metadata = {**module_metadata,'schema':schema,'items':[],'profiles':[{'name':'19'},{'name':'20'}],'preferred_profile':preferred}
            self.page.add_script_tag(content='''window.fixtureRequests=[];window.fixtureUrls=[];window.fixtureSaves=[];window.fetch=async (url,options={})=>{
                window.fixtureUrls.push(String(url));
                if(options.body instanceof FormData && options.body.get('action')==='save') {
                    window.fixtureSaves.push(Object.fromEntries(options.body));
                    return {ok:true,status:200,text:async()=>JSON.stringify({ok:true,data:{items:[]}})};
                }
                let data='''+json.dumps(metadata)+''';
                if(String(url).includes('metaPreflight.php')) data={ad_accounts:{data:[{account_id:'123456',name:'Fixture RK'}]}};
                if(String(url).includes('metaTemplateTargetingSearch.php')){
                    const params=new URLSearchParams(options.body);window.fixtureRequests.push(Object.fromEntries(params));
                    if(window.fixtureTargetingError) return {ok:false,status:409,text:async()=>JSON.stringify({ok:false,error:window.fixtureTargetingError})};
                    const type=params.get('type');data={account_id:'123456',checked_live:true,account_scope_verified:true,data:[
                        type==='locations'?{key:'US',name:'United States',type:'country',country_code:'US'}:
                        {id:'34567',key:'34567',name:'Fixture '+type} ]};
                }
                return {ok:true,status:200,text:async()=>JSON.stringify({ok:true,data})};
            };''')
        else:
            self.page.add_script_tag(content='window.fetch=async url=>({ok:true,status:200,text:async()=>JSON.stringify('+json.dumps({'ok':True,'data':{'schema':SCHEMA,'items':[]}})+')});')
        if modules:self.page.add_script_tag(content=(ROOT/'railway-meta-module-controls.js').read_text())
        source = (ROOT / 'railway-v100-creatives.js').read_text()
        self.page.add_script_tag(content=source+'\nwindow.testCreative={build:buildMetaBuilder,populate:populateMetaBuilder,open:openEditor,primary:populatePrimaryMetaControls};')
        self.page.wait_for_function("document.getElementById('mbObjective').options.length===2", timeout=5000)
        if enhanced:
            self.page.add_script_tag(content=ASSET)
        self.page.locator('#newCreative').click()

    def test_module_choices_save_and_reopen_without_manual_ids(self):
        self.creative(context=True,modules=True)
        self.page.get_by_role('button',name='Места показа',exact=True).click()
        for details in self.page.locator('#wf-panel-placements details:not([open])').all():details.locator(':scope > summary').click()
        self.page.locator('#mbUserDevice_choices').fill('iphone 17')
        self.page.locator('#mbUserDevice_choices').press('Enter')
        self.page.get_by_role('button',name='iphone 17',exact=True).click()
        self.page.locator('#mbUserOs_choices').fill('iOS 26.0')
        self.page.locator('#mbUserOs_choices').press('Enter')
        self.page.get_by_role('button',name='iOS 26.0 и новее',exact=True).click()
        self.page.get_by_role('button',name='Дополнительно',exact=True).click()
        self.page.locator('.cr-sdk-group[data-sdk-group="targeting"] > summary').click()
        self.page.locator('#sdk_targeting_education_statuses_choices').press('Enter')
        self.page.get_by_role('button',name='High School',exact=True).click()
        self.page.locator('#sdk_targeting_work_employers_choices').fill('Fixture')
        self.page.locator('#sdk_targeting_work_employers_choices').press('Enter')
        self.page.get_by_role('button',name='Fixture work_employers',exact=True).click()
        builder=self.page.evaluate('testCreative.build()')
        self.assertEqual(builder['targeting']['user_device'],['iphone 17'])
        self.assertEqual(builder['targeting']['user_os'],['iOS_ver_26.0_and_above'])
        self.assertEqual(builder['targeting']['education_statuses'],[1])
        self.assertEqual(builder['targeting']['work_employers'],[{'id':'34567','name':'Fixture work_employers'}])
        self.assertEqual(self.page.evaluate('fixtureRequests.at(-1).type'),'work_employers')
        self.assertNotIn('profile',self.page.evaluate('fixtureRequests.at(-1)'))
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Choices",meta_builder:builder})',builder)
        self.assertEqual(self.page.evaluate('testCreative.build()'),builder)
        self.page.get_by_role('button',name='Места показа',exact=True).click()
        self.assertEqual(self.page.get_by_role('button',name='Удалить iphone 17').count(),1)

    def test_nested_module_fields_edit_without_json_and_keep_value_types(self):
        self.creative(context=True,modules=True)
        self.page.get_by_role('button',name='Отслеживание',exact=True).click()
        self.page.locator('#mbDegreesOfFreedom').evaluate('(source)=>{source.value=JSON.stringify({creative_features_spec:{standard_enhancements:{enroll_status:"OPT_OUT"}}});}')
        self.page.locator('#mbDegreesOfFreedom').locator('..').locator('.rm-module-structure').get_by_role('button',name='Настроить',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='Улучшения креатива',exact=False)
        # The actual associated label may include the old technical hint.
        if dialog.count()==0:dialog=self.page.get_by_role('dialog').last
        dialog.get_by_role('textbox',name='Значение enroll_status',exact=True).fill('OPT_IN')
        dialog.get_by_role('button',name='Сохранить',exact=True).click()
        self.assertEqual(self.page.evaluate('testCreative.build().creative.degrees_of_freedom_spec'),{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_IN'}}})
        self.assertFalse(self.page.locator('#mbDegreesOfFreedom').is_visible())
        self.assertEqual(self.page.locator('.rm-module-structure').count()>1,True)
        self.page.locator('#mbTrackingSpecs').evaluate('(source)=>{source.value=JSON.stringify([{action_type:"offsite_conversion",amount:1,enabled:true,extra:null}]);}')
        self.page.locator('#mbTrackingSpecs').locator('..').locator('.rm-module-structure').get_by_role('button',name='Настроить',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='Правила отслеживания',exact=True)
        dialog.get_by_role('spinbutton',name='Значение amount',exact=True).fill('2.5')
        dialog.get_by_role('combobox',name='Значение enabled',exact=True).select_option('false')
        dialog.get_by_role('button',name='Сохранить',exact=True).click()
        builder=self.page.evaluate('testCreative.build()')
        self.assertEqual(builder['ad']['tracking_specs'],[{'action_type':'offsite_conversion','amount':2.5,'enabled':False,'extra':None}])
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Structured",meta_builder:builder})',builder)
        self.assertEqual(self.page.evaluate('testCreative.build()'),builder)

    def test_saved_module_choices_and_structures_can_be_removed(self):
        self.creative(context=True,modules=True)
        builder={'campaign':{'daily_budget':500,'objective':'OUTCOME_TRAFFIC'},
            'targeting':{'user_device':['iphone 17'],'education_statuses':[1]},
            'creative':{'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_IN'}}}}}
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Saved",meta_builder:builder})',builder)
        self.page.get_by_role('button',name='Места показа',exact=True).click()
        for details in self.page.locator('#wf-panel-placements details:not([open])').all():details.locator(':scope > summary').click()
        self.page.get_by_role('button',name='Удалить iphone 17',exact=True).click()
        self.page.get_by_role('button',name='Дополнительно',exact=True).click()
        self.page.locator('.cr-sdk-group[data-sdk-group="targeting"] > summary').click()
        self.page.get_by_role('button',name='Удалить High School',exact=True).click()
        self.page.get_by_role('button',name='Отслеживание',exact=True).click()
        self.page.locator('#mbDegreesOfFreedom').locator('..').locator('.rm-module-structure').get_by_role('button',name='Очистить',exact=True).click()
        saved=self.page.evaluate('testCreative.build()')
        self.assertNotIn('user_device',saved['targeting'])
        self.assertNotIn('education_statuses',saved['targeting'])
        self.assertNotIn('degrees_of_freedom_spec',saved['creative'])
        self.assertEqual(saved['campaign']['daily_budget'],500)
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Cleared",meta_builder:builder})',saved)
        self.assertEqual(self.page.evaluate('testCreative.build()'),saved)

    def test_instagram_template_saves_without_profile_account_or_owned_post(self):
        self.creative(context=True)
        self.page.locator('#presetName').fill('Universal Instagram')
        self.page.get_by_role('button',name='Объявление',exact=True).click()
        self.page.locator('#presetFormat').select_option('INSTAGRAM_POST')
        self.assertEqual(self.page.locator('#presetInstagramProfile,#presetInstagramAccount,#presetInstagramPost').count(),0)
        self.assertIn('для каждого РК в автозаливе',self.page.locator('#instagramSection').inner_text())
        self.page.locator('#saveCreative').click()
        self.page.wait_for_function('fixtureSaves.length===1')
        saved=self.page.evaluate('fixtureSaves[0]')
        self.assertEqual(saved['format'],'INSTAGRAM_POST')
        self.assertNotIn('instagram_media_id',saved)
        builder=json.loads(saved['meta_builder'])
        self.assertEqual(builder['identity'],{})
        self.assertEqual(builder['existing_media'],{})
        self.assertFalse(any('metaPreflight' in url or 'metaAssetManager' in url for url in self.page.evaluate('fixtureUrls')))

    def test_legacy_template_owned_assets_do_not_bind_editor(self):
        self.creative(context=True)
        builder=self.page.evaluate('testCreative.build()')
        builder.update(identity={'page_id':'123456','instagram_actor_id':'654321'},existing_media={'source_instagram_media_id':'999999'})
        builder['adset']['promoted_object']={'pixel_id':'555555','custom_event_type':'PURCHASE'}
        builder['targeting']['custom_audiences']=[{'id':'777777'}]
        builder['targeting']['interests']=[{'id':'888888','name':'Fixture interest'}]
        self.page.evaluate('(builder)=>testCreative.open({id:"legacy",name:"Universal",format:"INSTAGRAM_POST",instagram_media_id:"999999",meta_builder:builder})',builder)
        saved=self.page.evaluate('testCreative.build()')
        self.assertEqual(saved['identity'],{})
        self.assertEqual(saved['existing_media'],{})
        self.assertNotIn('custom_audiences',saved['targeting'])
        self.assertEqual(saved['targeting']['interests'],builder['targeting']['interests'])
        self.assertEqual(saved['adset']['promoted_object'],{'custom_event_type':'PURCHASE'})

    def test_template_search_needs_no_profile_or_publishing_account(self):
        self.creative(context=True)
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        self.assertEqual(self.page.locator('#creativeMetaProfile,#creativeMetaAccount').count(), 0)
        self.page.locator('#mbLanguageSearch').fill('russia')
        self.page.locator('#mbLanguageSearch').press('Enter')
        self.page.locator('#mbLanguageResults .cr-target-result').click()
        self.assertEqual(self.page.evaluate('testCreative.build().targeting.locales'), [34567])
        request=self.page.evaluate('fixtureRequests.at(-1)')
        self.assertNotIn('profile',request)
        self.assertNotIn('account_id',request)

    def test_reopening_unbound_template_retains_targeting(self):
        self.creative(context=True, preferred='19')
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        self.page.locator('#mbLanguageSearch').fill('fixture')
        self.page.locator('#mbLanguageSearch').press('Enter')
        self.page.locator('#mbLanguageResults .cr-target-result').click()
        builder=self.page.evaluate('testCreative.build()')
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Universal",meta_builder:builder})',builder)
        self.assertEqual(self.page.evaluate('testCreative.build()'),builder)
        self.assertNotIn('account_id',builder)
        self.assertNotIn('profile',builder)
        self.assertEqual(self.page.locator('#creativeMetaProfile,#creativeMetaAccount').count(),0)

    def test_all_template_targeting_searches_work_without_destination_selection(self):
        self.creative(context=True)
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        for input_id,result_id,kind in [('mbGeoSearch','mbGeoResults','locations'),
                ('mbExcludedGeoSearch','mbExcludedGeoResults','locations'),
                ('mbInterestSearch','mbInterestResults','interests'),
                ('mbBehaviorSearch','mbBehaviorResults','behaviors'),
                ('mbLanguageSearch','mbLanguageResults','languages')]:
            self.page.locator('#'+input_id).fill('fixture')
            self.page.locator('#'+input_id).press('Enter')
            self.page.locator('#'+result_id+' .cr-target-result').wait_for()
            self.page.locator('#'+result_id+' .cr-target-result').click()
            request=self.page.evaluate('fixtureRequests.at(-1)')
            self.assertNotIn('profile',request)
            self.assertNotIn('account_id',request)
            self.assertEqual(request['type'],kind)
        targeting=self.page.evaluate('testCreative.build().targeting')
        self.assertEqual(targeting['geo_locations']['countries'],['US'])
        self.assertEqual(targeting['excluded_geo_locations']['countries'],['US'])
        self.assertEqual(targeting['locales'],[34567])
        self.assertEqual(targeting['interests'][0]['id'],'34567')
        self.assertEqual(targeting['behaviors'][0]['id'],'34567')

    def test_empty_focus_browses_choices_without_destination_and_preserves_ids(self):
        self.creative(context=True)
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        for input_id,result_id,kind in [('mbInterestSearch','mbInterestResults','interests'),
                                     ('mbLanguageSearch','mbLanguageResults','languages')]:
            self.page.locator('#'+input_id).focus()
            self.page.locator('#'+result_id+' .cr-target-result').wait_for()
            request=self.page.evaluate('fixtureRequests.at(-1)')
            self.assertEqual(request['q'],'')
            self.assertNotIn('profile',request)
            self.assertNotIn('account_id',request)
            self.assertEqual(request['type'],kind)
            self.page.locator('#'+result_id+' .cr-target-result').press('Enter')
        targeting=self.page.evaluate('testCreative.build().targeting')
        self.assertEqual(targeting['locales'],[34567])
        self.assertEqual(targeting['interests'][0]['id'],'34567')

    def test_connection_error_explains_repair_without_losing_settings_or_retry(self):
        self.creative(context=True)
        self.page.locator('#mbCampaignDailyBudget').fill('500')
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        self.page.locator('#mbInterestSearch').fill('fixture')
        self.page.locator('#mbInterestSearch').press('Enter')
        self.page.locator('#mbInterestResults .cr-target-result').click()
        before=self.page.evaluate('testCreative.build()')
        self.page.evaluate("window.fixtureTargetingError={code:'PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED',message:'Общий справочник Meta временно недоступен. Шаблон можно редактировать и сохранить без выбора профиля.',settings_preserved:true}")
        count=self.page.evaluate('fixtureRequests.length')
        self.page.locator('#mbLanguageSearch').fill('russia')
        self.page.locator('#mbLanguageSearch').press('Enter')
        self.page.locator('#mbLanguageResults [role=status]').wait_for()
        self.assertIn('без выбора профиля',self.page.locator('#mbLanguageResults').inner_text())
        self.assertEqual(self.page.locator('#mbLanguageResults a').count(),0)
        self.assertEqual(self.page.evaluate('testCreative.build()'),before)
        self.assertEqual(self.page.evaluate('fixtureRequests.length'),count+1)
        self.assertEqual(self.page.locator('#mbLanguageResults .cr-target-result').count(),0)

    def test_objectives_match_launch_and_preserve_saved_legacy_value(self):
        self.creative()
        self.assertEqual(self.page.locator('#mbObjective').evaluate('(el)=>Array.from(el.options).map(o=>o.value)'),['OUTCOME_TRAFFIC','OUTCOME_SALES'])
        builder=self.page.evaluate('testCreative.build()')
        builder['campaign']['objective']='LINK_CLICKS'
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Legacy",meta_builder:builder})',builder)
        self.assertEqual(self.page.evaluate('testCreative.build().campaign.objective'),'LINK_CLICKS')
        self.assertTrue(self.page.locator('#mbObjective [data-saved-objective]').is_disabled())
        self.assertTrue(self.page.locator('#creativeObjectiveHint').is_visible())
        self.page.evaluate('testCreative.primary()')
        self.assertEqual(self.page.evaluate('testCreative.build().campaign.objective'),'LINK_CLICKS')
        self.page.locator('#mbObjective').select_option('OUTCOME_SALES')
        self.assertFalse(self.page.locator('#creativeObjectiveHint').is_visible())
        self.assertEqual(self.page.evaluate('testCreative.build().campaign.objective'),'OUTCOME_SALES')
        self.page.locator('#cancelCreative').click()
        self.page.locator('#newCreative').click()
        self.assertEqual(self.page.locator('#mbObjective [data-saved-objective]').count(),0)

    def test_grouping_and_translation_preserve_original_serialization(self):
        self.creative(False)
        self.page.locator('#mbCampaignName').fill('Fixture campaign')
        self.page.locator('#mbCampaignDailyBudget').fill('500')
        self.page.get_by_role('button', name='Creative', exact=True).click()
        self.page.locator('#presetMessage').fill('Текст для теста')
        self.page.get_by_role('button', name='Advanced', exact=True).click()
        self.page.locator('.cr-sdk-group[data-sdk-group="campaign"] > summary').click()
        self.page.locator('#sdk_campaign_adlabels').fill('[{"name":"reviewed"}]')
        before = self.page.evaluate('testCreative.build()')
        self.page.add_script_tag(content=ASSET)
        self.assertEqual(self.page.evaluate('testCreative.build()'), before)
        self.assertEqual(self.page.locator('#mbObjective').input_value(), 'OUTCOME_TRAFFIC')
        self.assertEqual(self.page.locator('#mbCampaignDailyBudget').input_value(), '500')
        self.assertEqual(self.page.get_by_label('Название кампании', exact=True).count(), 1)
        self.assertEqual(self.page.locator('#wf-panel-campaign .wf-block').count(), 2)
        self.assertIn('500', self.page.locator('[data-wf-summary="budget"]').inner_text())
        self.assertEqual(self.page.locator('#creativeForm .cr-tab').count(), 7)

    def test_reopen_retains_budget_raw_json_and_enum_values(self):
        self.creative()
        self.page.locator('#mbCampaignDailyBudget').fill('1500')
        self.page.get_by_role('button', name='Дополнительно', exact=True).click()
        self.page.locator('.cr-sdk-group[data-sdk-group="campaign"] > summary').click()
        self.page.locator('#sdk_campaign_adlabels').fill('[{"name":"fixture"}]')
        builder = self.page.evaluate('testCreative.build()')
        self.page.evaluate('(builder)=>testCreative.open({id:"fixture",name:"Fixture",meta_builder:builder})', builder)
        self.assertEqual(self.page.evaluate('testCreative.build()'), builder)
        self.assertEqual(self.page.locator('#sdk_campaign_adlabels').input_value(), '[{"name":"fixture"}]')
        self.assertEqual(self.page.locator('#mbCampaignDailyBudget').input_value(), '1500')

    def test_mobile_editor_keeps_save_action_and_has_no_page_overflow(self):
        self.page.set_viewport_size({'width':390,'height':844})
        self.creative()
        self.assertTrue(self.page.locator('#saveCreative').is_visible())
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.get_by_role('button', name='Объявление', exact=True).click()
        self.assertTrue(self.page.locator('#presetMessage').is_visible())

    def test_editor_navigation_is_above_real_runtime_sidebar(self):
        self.creative()
        self.page.locator('.cr-tab[data-tab="creative"]').click()
        self.assertTrue(self.page.locator('#presetMessage').is_visible())
        self.page.wait_for_function("document.querySelector('.cr-tab[data-tab=creative]').getAttribute('aria-current')==='step'")
        self.assertEqual(self.page.locator('.cr-tab[data-tab="creative"]').get_attribute('aria-current'),'step')
        self.assertTrue(self.page.locator('.cr-tab[data-tab="creative"]').evaluate('(el)=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.left+8,r.top+r.height/2).closest(".cr-tab")===el;}'))

    def launch(self):
        self.page.set_content('''<style>label{display:inline}.form-control{display:inline-block!important;width:auto!important}</style><main><div class="launch-wrap">
        <div class="launch-card"><h5>Source</h5><div class="row"><div class="col-md-4">
        <label for="profile">Facebook profiles</label><select id="profile" multiple><option value="19">19</option><option value="21">21</option></select></div></div></div>
        <div class="launch-card"><h5>Campaign</h5><label>Campaign name</label><input id="campaignName"><select id="objective"><option value="OUTCOME_TRAFFIC">Traffic</option></select></div>
        <div class="launch-card"><h5>Ad Set</h5><div class="row"><input id="adsetName"><div><label>Daily budget</label><input id="dailyBudget" value="500"><span class="muted">Minor currency units</span></div><div class="col-md-6"><label>GEO search</label><div class="input-group"><input id="geoQuery"></div></div><select id="placementMode"><option value="AUTO">Advantage+ / automatic</option><option value="MANUAL">Manual platforms</option></select></div></div>
        <div class="launch-card"><h5>Creative</h5><input id="creativeName"></div>
        <div class="launch-card"><h5>Review</h5><button id="reviewLaunch" disabled>Review</button><button id="launchButton" disabled><i></i> LAUNCH SELECTED</button></div>
        </div></main><section id="launchMetaAllFields"><h3>API settings</h3><div class="rm-meta-note"></div>
        <details data-launch-meta-group="targeting"><summary><span>TARGETING</span></summary><div class="rm-meta-field" data-search="targeting custom_audiences Object"><label><span>custom_audiences</span><span class="rm-meta-type">Object</span></label><textarea data-launch-meta-field="custom_audiences">[{"id":"12345678"}]</textarea></div></details></section>''')
        self.page.add_script_tag(content='document.querySelectorAll("input,select,textarea").forEach(el=>el.classList.add("form-control"));window.changes=0;document.getElementById("profile").addEventListener("change",()=>window.changes++);')
        self.page.add_script_tag(content=ASSET)

    def test_profile_checkboxes_use_original_selection_and_do_not_launch(self):
        self.launch()
        self.page.get_by_role('checkbox', name='19', exact=True).check()
        self.page.get_by_role('checkbox', name='21', exact=True).check()
        self.assertEqual(self.page.locator('#profile').evaluate('(el)=>Array.from(el.selectedOptions).map(o=>o.value)'), ['19','21'])
        self.assertEqual(self.page.evaluate('window.changes'), 2)
        self.assertFalse(self.page.locator('#launchButton').is_enabled())
        # Preset/catalog hydration updates the hidden source, then emits its
        # normal change event. User interaction happens through checkboxes.
        self.page.locator('#profile').evaluate('(el)=>{for(const option of el.options)option.selected=option.value==="21";el.dispatchEvent(new Event("change",{bubbles:true}));}')
        self.assertFalse(self.page.get_by_role('checkbox', name='19', exact=True).is_checked())

    def test_dynamic_field_search_and_values_survive_repeated_updates(self):
        self.launch()
        before = self.page.locator('[data-launch-meta-field]').input_value()
        for n in range(5):
            self.page.evaluate('(n)=>document.querySelector(".rm-meta-note").textContent="changed"+n', n)
            self.page.wait_for_timeout(25)
        field = self.page.locator('.rm-meta-field')
        self.assertIn('включить свои аудитории', field.get_attribute('data-search'))
        self.assertLess(len(field.get_attribute('data-search')), 200)
        self.assertEqual(self.page.locator('[data-launch-meta-field]').input_value(), before)
        self.assertEqual(self.page.locator('#dailyBudget').input_value(), '500')
        self.assertEqual(self.page.get_by_label('География', exact=True).count(),1)
        self.assertEqual(self.page.locator('#placementMode').input_value(),'AUTO')
        self.assertEqual(self.page.locator('#placementMode option:checked').inner_text(),'Автоматически (Advantage+)')
        self.assertEqual(self.page.locator('main #launchMetaAllFields').count(), 1)

    def test_launch_fields_override_inline_theme_without_revealing_native_picker(self):
        self.launch()
        label=self.page.get_by_text('География',exact=True).bounding_box()
        field=self.page.locator('#geoQuery').bounding_box()
        wrapper=self.page.locator('#geoQuery').evaluate('(el)=>el.parentElement.getBoundingClientRect().width')
        self.assertGreaterEqual(field['y'],label['y']+label['height'])
        self.assertAlmostEqual(field['width'],wrapper,delta=1)
        self.assertFalse(self.page.locator('#profile').is_visible())


if __name__ == '__main__':
    unittest.main()
