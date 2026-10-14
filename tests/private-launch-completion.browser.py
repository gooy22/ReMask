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
        cls.schema = json.loads((ROOT/'python_backend/app/contracts/ads_manager_http_20261012.json').read_text())['form_schema']
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.runtime.stop(); cls.build.tearDown()

    def setUp(self):
        self.bundles = []
        self.missing = False
        self.errors = []
        self.preset_objective = 'LEGACY_TEST_OBJECTIVE' if self._testMethodName == 'test_initial_config_keeps_legacy_goal' else 'OUTCOME_TRAFFIC'
        self.delay_config = self._testMethodName.startswith('test_initial_config')
        self.waiting_config = []
        self.lookup_requests = []
        self.lookup_failure = False
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
            # The PHP shell supplies jQuery on the real page; this fixture tests
            # Launch itself and retains CSS without executing its shell plugin.
            html = re.sub(r'<script src="styles/bootstrap.min.js"></script>', '', html)
            route.fulfill(body=html, content_type='text/html'); return
        file = self.root/path
        if file.is_file() and not path.endswith('.php'):
            body = file.read_bytes()
            if path == 'scripts/launch.js':
                body += b'\nwindow.fixtureLaunch={payload:()=>buildPayload(),capture:()=>bundleFormState(),restore:applyBundleData,config:loadLaunchConfig,ready:()=>Boolean(state.launchConfig),geo:()=>{state.geo=[{type:"country",key:"US",country_code:"US",name:"United States"}];}};'
            route.fulfill(body=body, content_type='application/javascript' if path.endswith('.js') else 'text/css'); return
        data = {}
        status = 200
        if path == 'ajax/metaLaunchConfig.php':
            if self.delay_config:
                self.waiting_config.append(route)
                return
            data = self.config
        elif path == 'ajax/metaPrivateSchema.php': data = self.schema
        elif path == 'ajax/metaJobsList.php': data = {'jobs': []}
        elif path == 'ajax/creativeLibrary.php':
            data = {'items': [{'id':'preset','name':'Fixture','format':'SINGLE','media_library_id':MEDIA_ID,
                'media':MEDIA,'message':'Fixture message','headline':'Fixture headline','destination_url':'https://example.com/',
                'meta_builder':{'campaign':{'daily_budget':500,'objective':self.preset_objective}}}]}
        elif path == 'ajax/metaTemplateTargetingSearch.php':
            fields = parse_qs(request.post_data or '')
            self.lookup_requests.append(fields)
            if self.lookup_failure:
                route.fulfill(status=409,json={'ok':False,'error':{'code':'PRIVATE_HTTP_PROXY_UNAVAILABLE',
                    'message':'Прокси недоступен. Уже введённые настройки можно сохранить.'}})
                return
            kind = fields['type'][0]
            item = {'key':'US','name':'Fixture locations','type':'country','country_code':'US'} if kind == 'locations' else {
                'id':{'interests':'123456','behaviors':'654321','languages':'1000'}[kind], 'name':'Fixture '+kind}
            data = {'data':[item], 'checked_live':True,'source':'private_meta_template_lookup','template_account_bound':False}
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
        route.fulfill(status=status, json={'ok':status==200,'data':data,'error':{'message':'MEDIA_LIBRARY_ITEM_UNAVAILABLE'}})

    def save_and_reload(self):
        self.page.locator('#bundleName').fill('Fixture bundle')
        self.page.locator('#bundleSave').click()
        self.page.wait_for_function('document.querySelector("#bundleSelect").value === "fixture"')
        self.page.reload()
        self.page.wait_for_function('document.querySelector("#bundleSelect option[value=fixture]") !== null')
        self.page.locator('#bundleSelect').select_option('fixture')
        self.page.locator('#bundleLoad').click()
        self.page.wait_for_function('document.querySelector("#launchResult").classList.contains("ready")')

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

    def test_legacy_preset_goal_survives_bundle_reload_until_user_selects_supported_goal(self):
        self.preset_objective = 'LEGACY_TEST_OBJECTIVE'
        self.page.reload()
        self.page.wait_for_function('document.querySelector("#creativeLibrarySelect option[value=preset]") !== null')
        self.page.locator('#creativeLibrarySelect').select_option('preset')
        self.assertEqual(self.page.locator('#objective').input_value(), self.preset_objective)
        self.assertTrue(self.page.locator('#remaskUnsupportedObjective').is_visible())
        self.assertFalse(self.page.locator('#launchButton').is_enabled())
        self.assertFalse(self.page.locator('#reviewLaunch').is_enabled())
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
        self.assertFalse(self.page.locator('#dryRunPlan').is_enabled())
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

if __name__ == '__main__': unittest.main()
