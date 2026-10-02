import base64
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.facebook_graph_api import FacebookGraphApi, GraphApiError
from app.provisioning.transport import ProvisioningTransport, TransportError
from app.session import ProfileContext, ProfileContextError, ProfileResolver, ProfileSession

ROOT = Path(__file__).resolve().parents[2]


class CookieOnlyTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_does_not_copy_historical_access_token(self):
        resolver=ProfileResolver('http://127.0.0.1/fixture', 'fixture-key')
        resolver._fetch=AsyncMock(return_value={'cookies':{'c_user':'123456789','xs':'fixture'},
            'proxy':'http://127.0.0.1:1','user_agent':'fixture','access_token':'historical-fixture'})
        context=await resolver.resolve('Fixture')
        self.assertEqual(context.access_token,'')
        self.assertEqual(context.cookies['xs'],'fixture')
        self.assertEqual(context.proxy,'http://127.0.0.1:1')

    async def test_profile_session_refuses_graph_even_with_historical_token(self):
        context = ProfileContext(profile_id='Fixture',cookies={'c_user':'123456789','xs':'fixture'},
            proxy='http://127.0.0.1:1',user_agent='fixture',access_token='historical-fixture')
        with self.assertRaisesRegex(ProfileContextError,'COOKIE_ONLY_GRAPH_DISABLED'):
            await ProfileSession(context).graph_api()

    async def test_graph_get_and_mutation_never_open_http_client(self):
        graph = FacebookGraphApi(access_token='fixture-unused-token', proxy='http://127.0.0.1:1', user_agent='fixture')
        graph._client = AsyncMock(side_effect=AssertionError('network must stay closed'))
        for method in ['GET', 'POST', 'DELETE']:
            with self.assertRaisesRegex(GraphApiError, 'COOKIE_ONLY_GRAPH_DISABLED'):
                await graph._request(method, 'me', mutation=method != 'GET')
        graph._client.assert_not_called()

    def test_route_config_cannot_restore_graph_funding(self):
        for step in ['BUSINESS', 'AD_ACCOUNT', 'FUNDING']:
            for host in ['graph.facebook.com', 'business.facebook.com', 'facebook.com']:
                with self.assertRaises(TransportError):
                    ProvisioningTransport(json.dumps({step: {'url': 'https://' + host + '/fixture'}}))
        self.assertIn('FUNDING', ProvisioningTransport(json.dumps({'FUNDING': {'url': 'https://example.test/funding'}})).routes)


@unittest.skipUnless(shutil.which('php'), 'PHP unavailable locally; required in deployment CI')
class CookieOnlyProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runtime'
        self.root.mkdir()
        parts = sorted((ROOT / '.deploy/clean-preview-valid').glob('runtime.b64.*'))
        self.assertTrue(parts, 'tests must use the actual deployed base models/store')
        archive = base64.b64decode(''.join(p.read_text() for p in parts))
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(self.root, filter='data')
        self.data = self.root / 'fixture-accounts.json'
        (self.root/'settings.php').write_text('<?php define("ACCOUNTSFILENAME",' + json.dumps(str(self.data)) + '); define("REMASK_PROFILE_STORE","json");')
        (self.root/'checkpassword.php').write_text('<?php // isolated fixture authentication')
        self.env = {**os.environ, 'REMASK_PROFILE_STORE':'json'}
        overlay = (ROOT/'railway-cookie-only-overlay.php').read_text().replace("'/var/www/html'", json.dumps(str(self.root)))
        for file in ['railway-cookie-profile-ui.js','railway-cookie-accounts.js']:
            overlay = overlay.replace("'/tmp/" + file + "'", json.dumps(str(ROOT/file)))
        installer = self.root/'installer.php'
        installer.write_text(overlay)
        subprocess.run(['php',str(installer)],env=self.env,capture_output=True,text=True,check=True,timeout=15)
        profile = re.search(r"<<<'PHP_CODE'\n(.*?)\nPHP_CODE;", (ROOT/'railway-profile-session-guard-overlay.php').read_text(), re.S).group(1)
        (self.root/'ajax/metaProfileManager.php').write_text(profile)
        self.cookies = [{'name':'c_user','value':'123456789'},{'name':'xs','value':'test-fixture-only'}]
        self.proxy = 'http:127.0.0.1:8080:user:fixture'

    def endpoint(self, name, payload):
        script = '$_POST=json_decode($argv[1],true);require $argv[2];'
        result = subprocess.run(['php','-r',script,json.dumps(payload),str(self.root/'ajax'/name)],env=self.env,capture_output=True,text=True,check=True,timeout=10)
        return json.loads(result.stdout)

    def create(self, **extra):
        return self.endpoint('metaProfileManager.php', {'action':'create','name':'Fixture','cookies':self.cookies,'proxy':self.proxy,**extra})

    def saved(self):
        return json.loads(self.data.read_text())

    def test_create_without_token_and_no_false_live_verification(self):
        result = self.create()
        self.assertTrue(result['ok'])
        self.assertEqual(result['profile']['auth_mode'],'cookies')
        self.assertFalse(result['profile']['token_required'])
        self.assertFalse(result['profile']['session_verified'])
        self.assertNotIn('test-fixture-only',json.dumps(result))
        self.assertEqual(self.saved()[0]['token'],'')
        self.assertEqual(self.saved()[0]['userId'] if 'userId' in self.saved()[0] else self.saved()[0]['cookies'][0]['value'],'123456789')

    def test_new_imported_token_is_ignored(self):
        self.assertTrue(self.create(token='fixture-token-must-not-be-imported')['ok'])
        self.assertEqual(self.saved()[0]['token'],'')

    def test_invalid_cookies_or_proxy_do_not_write_profile(self):
        for cookies in [[],{},[{'name':'c_user','value':'123'}],[{'name':'c_user','value':'123'},{'name':'xs','value':''}],self.cookies + [{'name':'xs','value':'conflicting-fixture'}]]:
            self.assertFalse(self.create(cookies=cookies)['ok'])
        self.assertFalse(self.create(proxy='')['ok'])
        self.assertFalse(self.data.exists() and self.saved())

    def test_cookie_map_is_normalized(self):
        self.assertTrue(self.create(cookies={'c_user':'123456789','xs':'test-fixture-only'})['ok'])
        self.assertEqual(self.saved()[0]['cookies'],self.cookies)

    def test_blank_edit_preserves_cookies_and_proxy(self):
        self.create()
        before = self.saved()
        for cookies in ['',[],{}]:
            result = self.endpoint('metaProfileManager.php',{'action':'save','name':'Fixture','cookies':cookies,'proxy':''})
            self.assertTrue(result['ok'])
            self.assertEqual(self.saved(),before)

    def test_failed_edit_does_not_replace_working_session(self):
        self.create()
        before = self.saved()
        result = self.endpoint('metaProfileManager.php',{'action':'save','name':'Fixture','cookies':[{'name':'xs','value':'new-fixture'}]})
        self.assertFalse(result['ok'])
        self.assertEqual(self.saved(),before)

    def test_historical_token_is_preserved_but_cannot_be_updated(self):
        self.create()
        saved = self.saved()
        saved[0]['token']='historical-fixture'
        self.data.write_text(json.dumps(saved))
        result=self.endpoint('metaProfileManager.php',{'action':'save','name':'Fixture','token':'new-unused-fixture'})
        self.assertTrue(result['ok'])
        self.assertEqual(self.saved()[0]['token'],'historical-fixture')
        self.assertNotIn('historical-fixture',json.dumps(result))

    def test_legacy_add_and_check_endpoints_accept_no_token(self):
        payload = {'name':'Fixture','cookies':json.dumps(self.cookies),'proxy':self.proxy}
        checked = self.endpoint('checkAccount.php',payload)
        self.assertTrue(checked['ok'])
        self.assertFalse(json.loads(checked['res'])['session_verified'])
        self.assertTrue(self.endpoint('addAccount.php',payload)['ok'])
        self.assertEqual(self.saved()[0]['token'],'')

    def test_actual_php_graph_and_refresh_boundaries_are_closed(self):
        code = 'require $argv[1]."/classes/MetaApiClient.php";require $argv[1]."/classes/FbRequests.php";$out=[];try{new MetaApiClient("fixture");}catch(Throwable $e){$out[]=$e->getMessage();}$a=new FbAccount("Fixture","fixture");$f=new FbRequests;foreach(["ApiGet","ApiPost"] as $method){try{$f->$method($a,"me","");}catch(Throwable $e){$out[]=$e->getMessage();}}$r=new ReflectionMethod(FbRequests::class,"GetNewToken");$out[]=$r->invoke($f,$a);echo json_encode($out);'
        result = subprocess.run(['php','-r',code,str(self.root)],env=self.env,capture_output=True,text=True,check=True,timeout=10)
        data = json.loads(result.stdout)
        self.assertTrue(all('COOKIE_ONLY_GRAPH_DISABLED' in value for value in data[:3]))
        self.assertIsNone(data[3])

    def test_actual_installed_ui_contains_no_token_inputs(self):
        workspace=(self.root/'scripts/workspace.js').read_text()
        accounts=(self.root/'accounts.php').read_text()
        self.assertNotIn('newProfileToken',workspace)
        self.assertNotIn('id="editToken"',workspace)
        self.assertNotIn('name="token"',accounts)
        self.assertIn('Facebook cookies',accounts)
