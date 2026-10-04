import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / 'railway-launch-private-catalog-overlay.php'


@unittest.skipUnless(shutil.which('php'), 'PHP unavailable locally; required in deployment CI')
class PrivateLaunchCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.helper = Path(self.tmp.name)/'catalog.php'
        source = OVERLAY.read_text()
        self.helper.write_text(re.search(r"<<<'CATALOG'\n(.*?)\nCATALOG", source, re.S).group(1))
        self.snapshot = {'7': {'updated_at':1000, 'ad_accounts':[
            {'id':'act_333333333','name':'Saved RK','currency':'USD','business_id':'111111111'},
            {'id':'999999999','profile':'8','name':'Other profile'}], 'pages':[
            {'id':'222222222','name':'Existing Page'},
            {'id':'555555555','page_id':'222222222','name':'Existing Page'},
            {'id':'888888888','profile':'8','name':'Other profile'}]}}

    def run_php(self, code):
        result = subprocess.run(['php','-r', 'require '+json.dumps(str(self.helper))+';'+code],
            capture_output=True,text=True,check=True,timeout=10)
        return json.loads(result.stdout)

    def catalog(self, bindings=None, now=1100, identities=None):
        args = [self.snapshot, bindings or {}, identities or {}]
        return self.run_php('$args=json_decode('+json.dumps(json.dumps(args))+',true);'
            + f'echo json_encode(RemaskPrivateLaunchCatalog::fromState("7",$args[0],$args[1],{now},$args[2]));')

    def test_profile_and_alias_dedup_without_false_permissions(self):
        catalog = self.catalog()
        self.assertEqual([r['account_id'] for r in catalog['ad_accounts']['data']], ['333333333'])
        self.assertEqual([r['id'] for r in catalog['pages']['data']], ['222222222'])
        self.assertIsNone(catalog['session_ready'])
        self.assertIsNone(catalog['ads_management_granted'])
        self.assertIsNone(catalog['ad_accounts']['data'][0]['account_status'])
        self.assertFalse(catalog['pages']['data'][0]['ad_account_page_access_verified'])
        self.assertFalse(catalog['_cache']['stale'])

    def test_exact_worker_binding_merge_and_no_invented_active_or_currency(self):
        catalog = self.catalog({'7':{'ad_accounts':[
            {'ad_account_id':'act_666666666','business_id':'777777777','account_name':'Confirmed RK'},
            {'ad_account_id':'333333333','business_id':'111111111','account_name':'Duplicate'},
            {'ad_account_id':'123456789','business_id':''}]}})
        self.assertEqual(len(catalog['ad_accounts']['data']), 2)
        created = catalog['ad_accounts']['data'][1]
        self.assertEqual(created['business_id'], '777777777')
        self.assertIsNone(created['account_status'])
        self.assertEqual(created['currency'], '')
        self.assertFalse(created['advertising_access_verified'])
        legacy = self.catalog({'7':{'ad_account_id':'666666666','business_id':'777777777'}})
        self.assertEqual(len(legacy['ad_accounts']['data']), 2)

    def test_create_alias_cannot_overwrite_observed_disabled_status_in_either_order(self):
        observed={'id':'act_333333333','business_id':'111111111','account_status':2}
        created={'id':'333333333','business_id':'111111111','_provisioned_only':True}
        for rows in [[observed,created],[created,observed]]:
            self.snapshot['7']['ad_accounts']=rows
            accounts=self.catalog()['ad_accounts']['data']
            self.assertEqual(len(accounts),1)
            self.assertEqual(accounts[0]['account_status'],2)

    def test_stale_or_missing_snapshot_cannot_be_fresh(self):
        self.assertTrue(self.catalog(now=3000)['_cache']['stale'])
        self.snapshot = {}
        catalog = self.catalog()
        self.assertTrue(catalog['_cache']['stale'])
        self.assertEqual(catalog['ad_accounts']['data'], [])

    def test_recorded_profile_plus_pairs_repair_four_rows_into_two_pages(self):
        self.snapshot['7']['pages'] = [
            {'id':'61594993341059','name':'Profile picture for Media Shopsw'},
            {'id':'61595071734540','name':'Profile picture for ReMask Page'},
            {'id':'1289628847574478','name':'Promote'},
            {'id':'1372205759306015','name':'Promote'}]
        recorded = self.run_php('echo json_encode(require '+json.dumps(str(ROOT/'railway-confirmed-page-identities.php'))+');')
        pages = self.catalog(identities=recorded)['pages']['data']
        self.assertEqual([(r['id'],r['name']) for r in pages], [
            ('1289628847574478','Media Shopsw'),('1372205759306015','ReMask Page')])
        self.assertTrue(all(r['ad_account_page_access_verified'] is False for r in pages))
        self.assertEqual(len(self.catalog(identities={'8':recorded['7']})['pages']['data']),4)

    def test_confirmed_mapping_cannot_inject_page_or_use_unverified_alias(self):
        self.snapshot['7']['pages'] = [{'id':'222222222','name':'Page'}, {'id':'555555555','name':'Alias'}]
        proof = {'7':[{'id':'888888888','profile_id':'555555555','identity_verified':True}]}
        self.assertEqual(len(self.catalog(identities=proof)['pages']['data']),2)
        proof = {'7':[{'id':'222222222','profile_id':'555555555','identity_verified':False}]}
        self.assertEqual(len(self.catalog(identities=proof)['pages']['data']),2)

    def test_snapshot_delegate_evidence_deduplicates_without_manual_mapping(self):
        self.snapshot['7']['pages'] = [
            {'id':'222222222','name':'Page','profile_id':'555555555','ownership_verified':True},
            {'id':'555555555','name':'Profile picture'}]
        self.assertEqual([r['id'] for r in self.catalog()['pages']['data']], ['222222222'])

    def test_ambiguous_alias_is_not_silently_merged(self):
        self.snapshot['7']['pages'] = [{'id':'222222222'},{'id':'555555555'},{'id':'888888888'}]
        proof = {'7':[{'id':id,'profile_id':'555555555','identity_verified':True} for id in ['222222222','888888888']]}
        self.assertEqual(len(self.catalog(identities=proof)['pages']['data']),3)

    def test_funding_is_unverified_and_rejects_rk_outside_profile(self):
        raw = json.dumps(json.dumps(self.catalog()))
        data = self.run_php('$c=json_decode('+raw+',true);echo json_encode(RemaskPrivateLaunchCatalog::asset($c,"funding","act_333333333"));')
        self.assertFalse(data['funding_verified'])
        self.assertEqual(data['verification_status'], 'NOT_CHECKED')
        error = self.run_php('$c=json_decode('+raw+',true);try {RemaskPrivateLaunchCatalog::asset($c,"funding","999999999");} catch(InvalidArgumentException $e) {echo json_encode(["rejected"=>true]);}')
        self.assertTrue(error['rejected'])

    def test_unsupported_resource_has_no_legacy_fallback(self):
        raw = json.dumps(json.dumps(self.catalog()))
        error = self.run_php('$c=json_decode('+raw+',true);try {RemaskPrivateLaunchCatalog::asset($c,"pixels","333333333");} catch(DomainException $e) {echo json_encode(["rejected"=>true]);}')
        self.assertTrue(error['rejected'])

    def test_readiness_exposes_pages_without_claiming_advertising_or_payment_access(self):
        raw = json.dumps(json.dumps(self.catalog()))
        data = self.run_php('$c=json_decode('+raw+',true);echo json_encode(RemaskPrivateLaunchCatalog::readiness($c,"act_333333333"));')
        self.assertEqual(data['status'], 'NOT_VERIFIED')
        self.assertEqual(data['pages']['count'], 1)
        self.assertEqual(data['pages']['data'][0]['id'], '222222222')
        self.assertFalse(data['pages']['ad_account_page_access_verified'])
        self.assertFalse(data['funding']['funding_verified'])
        error = self.run_php('$c=json_decode('+raw+',true);try {RemaskPrivateLaunchCatalog::readiness($c,"999999999");} catch(InvalidArgumentException $e) {echo json_encode(["rejected"=>true]);}')
        self.assertTrue(error['rejected'])

    def test_installer_and_generated_php_lint_and_js_cache_bust(self):
        root = Path(self.tmp.name)/'web'
        for folder in ['classes','ajax','scripts']:
            (root/folder).mkdir(parents=True,exist_ok=True)
        (root/'scripts/launch.js').write_text('function validateReady(){}\n')
        (root/'scripts/workspace.js').write_text('async function checkAssetsSelection(){}\nasync function showFunding(){}\n'.replace('checkAssetsSelection(){}','checkAssetsSelection(){ }'))
        (root/'launch.php').write_text('<script src="scripts/launch.js?v=old"></script>')
        installer = Path(self.tmp.name)/'overlay.php'
        source = OVERLAY.read_text().replace("$root='/var/www/html';", '$root='+json.dumps(str(root))+';')
        source = source.replace('/tmp/railway-launch-private-catalog.js', str(ROOT/'railway-launch-private-catalog.js'))
        source = source.replace('/tmp/railway-confirmed-page-identities.php', str(ROOT/'railway-confirmed-page-identities.php'))
        installer.write_text(source)
        subprocess.run(['php','-l',str(installer)],capture_output=True,check=True,timeout=10)
        for _ in range(2):
            subprocess.run(['php',str(installer)],capture_output=True,check=True,timeout=10)
        for file in [*root.glob('classes/*.php'),*root.glob('ajax/*.php')]:
            subprocess.run(['php','-l',str(file)],capture_output=True,check=True,timeout=10)
        self.assertIn('20261002-private-catalog-v1', (root/'launch.php').read_text())
        self.assertEqual((root/'scripts/launch.js').read_text().count('/* REMASK_PRIVATE_LAUNCH_CATALOG_V1 */'), 1)
        for name in ['metaLaunchReview.php','metaDryRun.php','metaJobCreate.php','metaLaunch.php']:
            self.assertIn('PRIVATE_LAUNCH_VERIFICATION_REQUIRED',(root/'ajax'/name).read_text())
            self.assertNotIn('cachedAsset',(root/'ajax'/name).read_text())

    def test_live_page_proof_cannot_cross_profile_account_or_expiry(self):
        import time
        proof={'profile_id':'7','account_id':'333333333','checked_live':True,
            'account_scope_verified':True,'status':'VERIFIED','checked_at':int(time.time()),
            'data':[{'id':'222222222','name':'Page','account_id':'333333333',
                'ad_account_page_access_verified':True,'source':'scoped_private_promotable_pages'}]}
        def readiness(p):
            args=json.dumps(json.dumps([self.catalog(),p]))
            return self.run_php('$a=json_decode('+args+',true);echo json_encode(RemaskPrivateLaunchCatalog::readiness($a[0],"333333333",$a[1]));')
        result=readiness(proof)
        self.assertTrue(result['pages']['data'][0]['ad_account_page_access_verified'])
        self.assertFalse(result['funding']['funding_verified'])
        self.assertEqual(result['status'],'NOT_VERIFIED')
        for patch in [{'profile_id':'8'},{'account_id':'999999999'},{'checked_at':1},
                      {'account_scope_verified':False},{'checked_live':False},
                      {'data':[{**proof['data'][0],'account_id':'999999999'}]}]:
            result=readiness({**proof,**patch})
            self.assertFalse(result['pages']['ad_account_page_access_verified'])
        confirmed={**proof,'status':'UNVERIFIED','data':[], 'page_confirmations':[
            {'id':'222222222','main_business_confirmed':True,'main_business_id':'111111111','source':'saved_worker_confirmation'}]}
        result=readiness(confirmed)
        self.assertTrue(result['pages']['data'][0]['main_business_confirmed'])
        self.assertFalse(result['pages']['ad_account_page_access_verified'])


if __name__ == '__main__':
    unittest.main()
