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
        'objective':['OUTCOME_TRAFFIC','OUTCOME_SALES'], 'special_ad_categories':['NONE'],
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

    def creative(self, enhanced=True, context=False):
        html = re.sub(r'<\?php.*?\?>', '', (ROOT / 'railway-v100-creatives.php').read_text(), flags=re.S)
        html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
        html = re.sub(r'<link\b[^>]*>', '', html)
        # Real runtime sidebar has z-index 1000 and overlaps wide editors if
        # the modal keeps the old 900 layer. Reproduce that shell in fixtures.
        html = html.replace('<body>', '<body><aside id="fixtureSidebar" style="position:fixed;left:0;top:0;width:201px;height:100vh;z-index:1000;background:#1a212c">Sidebar</aside>')
        if 'fixtureSidebar' not in html:
            html = re.sub(r'(<body\b[^>]*>)', r'\1<aside id="fixtureSidebar" style="position:fixed;left:0;top:0;width:201px;height:100vh;z-index:1000;background:#1a212c">Sidebar</aside>', html, count=1)
        self.page.set_content(html)
        if context:
            metadata = {'schema':SCHEMA,'items':[],'profiles':[{'name':'19'},{'name':'20'}]}
            self.page.add_script_tag(content='''window.fixtureRequests=[];window.fetch=async (url,options={})=>{
                let data='''+json.dumps(metadata)+''';
                if(String(url).includes('metaPreflight.php')) data={ad_accounts:{data:[{account_id:'123456',name:'Fixture RK'}]}};
                if(String(url).includes('metaTargetingSearch.php')){
                    const params=new URLSearchParams(options.body);window.fixtureRequests.push(Object.fromEntries(params));
                    const type=params.get('type');data={account_id:'123456',checked_live:true,account_scope_verified:true,data:[
                        type==='locations'?{key:'US',name:'United States',type:'country',country_code:'US'}:
                        {id:'34567',key:'34567',name:'Fixture '+type} ]};
                }
                return {ok:true,status:200,text:async()=>JSON.stringify({ok:true,data})};
            };''')
        else:
            self.page.add_script_tag(content='window.fetch=async url=>({ok:true,status:200,text:async()=>JSON.stringify('+json.dumps({'ok':True,'data':{'schema':SCHEMA,'items':[]}})+')});')
        source = (ROOT / 'railway-v100-creatives.js').read_text()
        self.page.add_script_tag(content=source+'\nwindow.testCreative={build:buildMetaBuilder,populate:populateMetaBuilder,open:openEditor};')
        self.page.wait_for_function("document.getElementById('mbObjective').options.length===2", timeout=5000)
        if enhanced:
            self.page.add_script_tag(content=ASSET)
        self.page.locator('#newCreative').click()

    def test_targeting_without_context_prompts_without_invalid_meta_request(self):
        self.creative(context=True)
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        self.page.locator('#mbLanguageSearch').fill('russia')
        self.page.locator('#mbLanguageSearch').press('Enter')
        self.assertIn('Выберите профиль Facebook', self.page.locator('#mbLanguageResults').inner_text())
        self.assertEqual(self.page.evaluate('fixtureRequests'), [])
        self.assertEqual(self.page.locator('#creativeMetaProfile option').count(), 3)

    def test_all_targeting_searches_send_selected_profile_and_account(self):
        self.creative(context=True)
        self.page.get_by_role('button',name='Аудитория',exact=True).click()
        self.page.locator('#creativeMetaProfile').select_option('19')
        self.page.wait_for_function("document.querySelector('#creativeMetaAccount').value==='123456'")
        for input_id,result_id,kind in [('mbGeoSearch','mbGeoResults','locations'),
                ('mbInterestSearch','mbInterestResults','interests'),
                ('mbBehaviorSearch','mbBehaviorResults','behaviors'),
                ('mbLanguageSearch','mbLanguageResults','languages')]:
            self.page.locator('#'+input_id).fill('fixture')
            self.page.locator('#'+input_id).press('Enter')
            self.page.locator('#'+result_id+' .cr-target-result').wait_for()
            self.page.locator('#'+result_id+' .cr-target-result').click()
            request=self.page.evaluate('fixtureRequests.at(-1)')
            self.assertEqual(request['profile'],'19')
            self.assertEqual(request['account_id'],'123456')
            self.assertEqual(request['type'],kind)
        targeting=self.page.evaluate('testCreative.build().targeting')
        self.assertEqual(targeting['geo_locations']['countries'],['US'])
        self.assertEqual(targeting['locales'],[34567])
        self.assertEqual(targeting['interests'][0]['id'],'34567')
        self.assertEqual(targeting['behaviors'][0]['id'],'34567')

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
