import json
import base64
import io
import shutil
import subprocess
import tarfile
import unittest

from tests import test_cookie_only_profiles as fixture


@unittest.skipUnless(shutil.which('php'), 'PHP required in deployment CI')
class CookieTxtImportTests(unittest.TestCase):
    setUp = fixture.CookieOnlyProfileTests.setUp
    create = fixture.CookieOnlyProfileTests.create
    saved = fixture.CookieOnlyProfileTests.saved

    def endpoint(self, name, payload):
        # Read large TXT fixtures from stdin, not OS argument strings.
        script = '$_POST=json_decode(stream_get_contents(STDIN),true);require $argv[1];'
        result = subprocess.run(['php','-r',script,str(self.root/'ajax'/name)], input=json.dumps(payload),
            env=self.env,capture_output=True,text=True,check=True,timeout=10)
        return json.loads(result.stdout)

    def row(self, uid='234567890', xs='txt-session-fixture', login=None, suffix=''):
        cookies = [{'domain':'.facebook.com','name':'c_user','value':uid},
                   {'domain':'.facebook.com','name':'xs','value':xs}]
        return f'{login or uid}\tshop-password-fixture\t{json.dumps(cookies)}{suffix}'

    def run_import(self, text, preview=False, **extra):
        return self.endpoint('metaProfileManager.php', {'action':'import_preview' if preview else 'import_txt',
            'text':text, 'proxy':self.proxy, **extra})

    def test_both_shop_formats_and_bom_crlf(self):
        text='\ufeff'+self.row(suffix='\tAAAA BBBB CCCC DDDD')+'\r\n\r\n'+self.row('345678901')+'\r\n'
        result=self.run_import(text, preview=True)
        self.assertEqual(result['ready'],2)
        self.assertEqual([r['line'] for r in result['records']],[1,3])
        self.assertEqual([r['user_id'] for r in result['records']],['234567890','345678901'])
        self.assertFalse(self.data.exists() and self.saved())
        for secret in ['shop-password-fixture','txt-session-fixture','AAAA BBBB','cookies',self.proxy]:
            self.assertNotIn(secret,json.dumps(result))

    def test_selected_account_numbered_persisted_and_retry_is_idempotent(self):
        self.create(name='7')
        text=self.row()+'\n'+self.row('345678901')
        result=self.run_import(text,selected_lines=[2])
        self.assertEqual(result['imported'],1)
        self.assertEqual(result['records'][0]['profile_name'],'8')
        self.assertEqual(self.saved()[-1]['cookies'][0]['value'],'345678901')
        before=self.saved()
        repeated=self.run_import(text,selected_lines=[2])
        self.assertEqual(repeated['imported'],0)
        self.assertEqual(repeated['records'][0]['status'],'already_exists')
        self.assertEqual(self.saved(),before)
        self.assertEqual(self.endpoint('metaProfileManager.php',{'action':'next_number'})['next_number'],9)
        self.assertFalse(result['session_verified'])
        self.assertEqual(self.saved()[-1]['token'],'')

    def test_wrong_login_missing_session_and_broken_json_are_isolated(self):
        broken='999999999\tbroken-password-fixture\t[{"name":"c_user"'
        text=self.row(login='987654321')+'\n'+broken+'\n'+self.row('345678901')
        result=self.run_import(text)
        self.assertEqual(result['errors'],2)
        self.assertEqual(result['imported'],1)
        self.assertEqual(result['records'][0]['error'],'LOGIN_COOKIE_MISMATCH')
        self.assertEqual(result['records'][1]['error'],'COOKIE_JSON_INVALID')
        self.assertEqual(len(self.saved()),1)
        self.assertNotIn('broken-password-fixture',json.dumps(result))

    def test_existing_cookie_session_never_overwritten(self):
        self.create(name='7')
        before=self.saved()
        result=self.run_import(self.row('123456789',xs='new-session-fixture'))
        self.assertEqual(result['records'][0]['status'],'already_exists')
        self.assertEqual(result['records'][0]['profile_name'],'7')
        self.assertEqual(self.saved(),before)

    def test_duplicate_and_conflicting_sessions(self):
        result=self.run_import(self.row()+'\n'+self.row())
        self.assertEqual((result['imported'],result['skipped']),(1,1))
        other=self.row('456789012')+'\n'+self.row('456789012',xs='conflicting-fixture')
        result=self.run_import(other)
        self.assertEqual(result['imported'],0)
        self.assertEqual(result['errors'],2)
        self.assertTrue(all(r['error']=='CONFLICTING_COOKIE_SESSIONS' for r in result['records']))

    def test_multiline_escaped_brackets_email_login_and_cookie_map(self):
        cookies={'c_user':'234567890','xs':'fixture"[\\]'}
        text='fixture@example.test\tpassword[fixture]\t'+json.dumps(cookies,indent=2)+'\t2FA FIXTURE'
        result=self.run_import(text)
        self.assertEqual(result['imported'],1)
        self.assertEqual(self.saved()[0]['cookies'][1]['value'],cookies['xs'])

    def test_empty_oversized_nonarray_selection_and_missing_proxy_do_not_write(self):
        for text in ['', 'x' * (2097152+1)]:
            self.assertFalse(self.run_import(text)['ok'])
        for selection in [[],['1'],{},[0]]:
            self.assertFalse(self.run_import(self.row(),selected_lines=selection)['ok'])
        result=self.run_import(self.row(),proxy='')
        self.assertFalse(result['ok'])
        self.assertFalse(self.data.exists() and self.saved())

    def test_foreign_cookie_domain_and_two_sessions_in_one_record(self):
        text=self.row().replace('.facebook.com','.example.test')+'\n'+self.row()+'\t'+self.row().split('\t')[2]
        result=self.run_import(text)
        self.assertEqual(result['imported'],0)
        self.assertEqual(result['errors'],2)

    def test_parallel_imports_of_same_uid_do_not_create_duplicates(self):
        self.create(name='7')
        payload={'action':'import_txt','text':self.row(),'proxy':self.proxy}
        script='$_POST=json_decode($argv[1],true);require $argv[2];'
        jobs=[subprocess.Popen(['php','-r',script,json.dumps(payload),str(self.root/'ajax/metaProfileManager.php')],
            env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        results=[]
        for job in jobs:
            stdout,stderr=job.communicate(timeout=15)
            self.assertEqual(job.returncode,0,stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(r['imported'] for r in results),1)
        self.assertEqual(len(self.saved()),2)

    def test_installed_runtime_contains_shared_import_ui_and_parser(self):
        for page in ['workspace.php','accounts.php']:
            self.assertIn('scripts/cookie-txt-import.js?v=',(self.root/page).read_text())
        self.assertIn('REMASK_COOKIE_TXT_IMPORT_V1',(self.root/'scripts/cookie-txt-import.js').read_text())
        self.assertTrue((self.root/'classes/RemaskCookieTxt.php').exists())

    def test_number_write_failure_is_not_counted_as_confirmed_import_and_retry_deduplicates(self):
        blocked = self.root / 'profile-sequence.json.tmp'
        blocked.mkdir()
        result = self.run_import(self.row() + '\n' + self.row('345678901'))
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['records'][0]['error'], 'TXT_SAVE_FAILED')
        self.assertEqual(len(self.saved()), 1)
        blocked.rmdir()
        repeated = self.run_import(self.row() + '\n' + self.row('345678901'))
        self.assertEqual(repeated['imported'], 1)
        self.assertEqual(repeated['skipped'], 1)
        self.assertEqual(repeated['records'][0]['status'], 'already_exists')
        self.assertEqual([row['name'] for row in self.saved()], ['1', '2'])

    def test_broken_cookie_json_cannot_consume_next_cookie_map_account(self):
        broken = '987654321\tfixture-password\t[{"name":"c_user"'
        valid = 'fixture@example.test\tfixture-password\t' + json.dumps({'c_user':'234567890','xs':'map-fixture'})
        result = self.run_import(broken + '\n' + valid)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['records'][1]['user_id'], '234567890')

    def test_real_auth_guard_rejects_missing_csrf_and_accepts_valid_header(self):
        parts=sorted((fixture.ROOT/'.deploy/clean-preview-valid').glob('runtime.b64.*'))
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(''.join(p.read_text() for p in parts)))) as archive:
            member=next(m for m in archive.getmembers() if m.name.lstrip('./')=='checkpassword.php')
            (self.root/'checkpassword.php').write_bytes(archive.extractfile(member).read())
        script = '''ini_set('session.save_path',dirname($argv[1]));session_start();
$_SESSION['remask_authenticated']=true;$_SESSION['remask_csrf']='csrf-fixture';
$_SERVER['REQUEST_METHOD']='POST';$_SERVER['REQUEST_URI']='/ajax/metaProfileManager.php';
if($argv[2]==='1')$_SERVER['HTTP_X_REMASK_CSRF']='csrf-fixture';
$_POST=json_decode(stream_get_contents(STDIN),true);require $argv[1];'''
        payload={'action':'import_txt','text':self.row(),'proxy':self.proxy}
        def run(header):
            r=subprocess.run(['php','-r',script,str(self.root/'ajax/metaProfileManager.php'),str(header)],
                input=json.dumps(payload),env=self.env,capture_output=True,text=True,check=True,timeout=10)
            return json.loads(r.stdout)
        denied=run(0)
        self.assertFalse(denied['ok'])
        self.assertIn('CSRF validation failed',denied['error']['message'])
        self.assertFalse(self.data.exists() and self.saved())
        accepted=run(1)
        self.assertEqual(accepted['imported'],1)
