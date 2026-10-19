"""Release regressions use disposable local storage and mock HTTP; no browser or Meta writes."""
import asyncio
import base64
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import CreateJobRequest
from app.runner import WorkerPool
from app.store import JobStore
from app.work_queue import WorkerInstanceLease

ROOT = Path(__file__).resolve().parents[2]

class HardeningQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(str(Path(self.tmp.name)/'jobs.sqlite3')); await self.store.init()

    async def job(self, profile, count=1, key=None):
        return await self.store.create_job(CreateJobRequest(idempotency_key=key, profiles=[
            {'profile_id':profile,'tasks':[{'action':'fixture'}]} for _ in range(count)]))

    async def test_busy_A_does_not_occupy_worker_needed_by_B_and_duplicates_do_not_run(self):
        from app import runner
        pool = WorkerPool(self.store, concurrency=2); await pool.provisioning_state.init()
        a,b,release = asyncio.Event(),asyncio.Event(),asyncio.Event(); calls=[]
        async def handler(session,payload):
            name=session.context.profile_id;calls.append(name)
            if name=='A':a.set();await release.wait()
            else:b.set()
            return {'ok':True}
        pool.registry.register('fixture',handler)
        pool.resolver.resolve=AsyncMock(side_effect=lambda name:SimpleNamespace(profile_id=name))
        class Session:
            def __init__(self,context):self.context=context
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        ja,_=await self.job('A',3);jb,_=await self.job('B')
        with patch.object(runner,'ProfileSession',Session):
            await pool.enqueue_job(ja);await pool.enqueue_job(ja);await pool.enqueue_job(jb)
            pool._workers=[asyncio.create_task(pool._worker(i)) for i in range(2)]
            try:
                await asyncio.wait_for(a.wait(),2);await asyncio.wait_for(b.wait(),2)
                self.assertEqual(calls.count('A'),1);self.assertEqual(calls.count('B'),1)
                release.set()
                for _ in range(100):
                    if (await self.store.job_view(ja))['status']=='SUCCESS':break
                    await asyncio.sleep(.02)
                self.assertEqual(calls.count('A'),3);self.assertEqual(calls.count('B'),1)
            finally:release.set();await pool.stop()

    async def test_claim_is_atomic_and_idempotent(self):
        job,_=await self.job('A');item=(await self.store.queued_item_ids(job))[0]
        results=await asyncio.gather(*(self.store.set_item_running(item) for _ in range(8)))
        self.assertEqual(results.count(True),1)
        self.assertEqual((await self.store.item(item))['attempt'],1)

    async def test_queue_admission_keeps_idempotent_existing_job(self):
        with patch.dict(os.environ,{'REMASK_MAX_PENDING_ITEMS':'1'}):
            job,created=await self.job('A',key='fixture-key');self.assertTrue(created)
            self.assertEqual(await self.job('A',key='fixture-key'),(job,False))
            with self.assertRaisesRegex(ValueError,'JOB_QUEUE_FULL'):await self.job('B')

    async def test_low_space_refuses_new_job_before_any_item_is_written(self):
        with patch('app.store.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError,'STORAGE_LOW_SPACE'):await self.job('A')
        self.assertEqual(await self.store.queue_count(),0)

    async def test_second_worker_refused_until_owner_releases(self):
        first=WorkerInstanceLease(self.store.path);second=WorkerInstanceLease(self.store.path)
        first.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError,'WORKER_STORE_ALREADY_OWNED'):second.acquire()
        finally:first.release()
        second.acquire();second.release()

    async def test_worker_auth_fails_closed_and_compares_configured_key(self):
        import main
        from fastapi import HTTPException
        with patch.object(main,'API_KEY',None):
            with self.assertRaises(HTTPException) as error:await main.require_key(None)
            self.assertEqual(error.exception.status_code,503)
        with patch.object(main,'API_KEY','fixture-secret'):
            with self.assertRaises(HTTPException) as error:await main.require_key('wrong')
            self.assertEqual(error.exception.status_code,401)
            await main.require_key('fixture-secret')

    async def test_generic_session_does_not_forward_cookies_or_bypass_Meta_HTTP2(self):
        from app.session import ProfileContext,ProfileSession,ProfileContextError
        session=ProfileSession(ProfileContext('fixture',{'c_user':'123','xs':'secret'},'http://127.0.0.1:1','fixture'))
        try:
            client=await session._get_session()
            self.assertEqual(list(client.cookie_jar),[])
            with self.assertRaisesRegex(ProfileContextError,'PRIVATE_HTTP_REQUIRED'):
                await session.request('GET','https://www.facebook.com/')
        finally:await session.close()

    async def test_browser_launch_is_disabled_before_import_or_network(self):
        from app.facebook_business_browser import FacebookBusinessBrowser,BrowserBusinessError
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture',proxy=None))
        with self.assertRaises(BrowserBusinessError) as error:await browser.open()
        self.assertEqual(error.exception.code,'BROWSER_TRANSPORT_DISABLED')
        from fb_worker import FacebookWebSession,WebProfile,RemoteRequestError
        web=FacebookWebSession(WebProfile('fixture',{'c_user':'123'},'http://127.0.0.1:1','fixture'))
        try:
            with self.assertRaises(RemoteRequestError) as error:await web.graphql_browser_native('123',{})
            self.assertFalse(error.exception.request_may_have_been_sent)
        finally:await web.close()

@unittest.skipUnless(shutil.which('php'),'PHP required by release CI')
class HardeningPhpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_cookie_only_profiles import CookieOnlyProfileTests
        cls.fixture=CookieOnlyProfileTests('test_complete_runtime_overlay_order_and_installed_php_build_contracts')
        cls.fixture.setUp();cls.addClassCleanup(cls.fixture.doCleanups)
        cls.fixture.test_complete_runtime_overlay_order_and_installed_php_build_contracts()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name);self.runtime=self.fixture.root
        self.env={**os.environ,'REMASK_DATA_DIR':str(self.data),'REMASK_PASSWORD':'fixture-secret',
            'REMASK_PHP_SESSION_DIR':str(self.data/'sessions'),'REMASK_DRAFTS_FILE':str(self.data/'drafts.json')}
        self.env.pop('REMASK_CREDENTIALS_KEY',None);self.env.pop('REMASK_ENCRYPT_PROFILE_JSON',None)
        (self.data/'sessions').mkdir()

    def php(self,code,*args,env=None,**kwargs):
        p=subprocess.run(['php','-r',code,*map(str,args)],env=env or self.env,capture_output=True,text=True,timeout=15,**kwargs)
        return p

    def endpoint(self,name,method,payload,csrf='fixture-csrf'):
        code='session_save_path($argv[1]);session_start();$_SESSION["remask_authenticated"]=true;$_SESSION["remask_csrf"]="fixture-csrf";'
        code+='$_SERVER["REQUEST_METHOD"]=$argv[2];$_SERVER["REQUEST_URI"]="/ajax/".$argv[3];$_SERVER["HTTP_X_REMASK_CSRF"]=$argv[5];'
        code+='if($argv[2]==="POST")$_POST=json_decode($argv[4],true);else $_GET=json_decode($argv[4],true);'
        code+='register_shutdown_function(function(){fwrite(STDERR,"STATUS=".http_response_code());});require $argv[6];'
        return self.php(code,self.data/'sessions',method,name,json.dumps(payload),csrf,self.runtime/'ajax'/name)

    def test_GET_cannot_save_delete_or_retry_even_with_session_and_internal_key(self):
        for endpoint,action in [('metaDrafts.php','save'),('metaDrafts.php','delete'),('metaMediaLibrary.php','delete'),
            ('pythonWorkerJobs.php','retry_failed'),('pythonWorkerJobs.php','register_docid'),('metaJobRetry.php','')]:
            result=self.endpoint(endpoint,'GET',{'action':action,'name':'fixture','job_id':'fixture'})
            self.assertIn('STATUS=405',result.stderr,(endpoint,result.stdout,result.stderr))
        self.assertFalse((self.data/'drafts.json').exists())

    def test_HEAD_and_OPTIONS_cannot_fall_through_to_default_save(self):
        for method in ['HEAD','OPTIONS']:
            p=self.endpoint('metaDrafts.php',method,{'name':'fixture','data':{'x':1}})
            self.assertIn('STATUS=405',p.stderr,(method,p.stdout,p.stderr))
        self.assertFalse((self.data/'drafts.json').exists())

    def test_POST_requires_csrf_and_valid_POST_can_save_and_read_draft(self):
        result=self.endpoint('metaDrafts.php','POST',{'action':'save','name':'fixture','data':{'x':1}},csrf='wrong')
        self.assertIn('STATUS=403',result.stderr,result.stdout)
        result=self.endpoint('metaDrafts.php','POST',{'action':'save','name':'fixture','data':{'x':1}})
        self.assertTrue(json.loads(result.stdout)['ok'],(result.stdout,result.stderr))
        result=self.endpoint('metaDrafts.php','GET',{'action':'list'})
        self.assertTrue(json.loads(result.stdout)['ok'],(result.stdout,result.stderr))

    def profile_script(self):
        return 'require $argv[1]."/classes/RemaskProxy.php";require $argv[1]."/classes/FbAccount.php";require $argv[1]."/classes/FbAccountSerializer.php";$s=new FbAccountSerializer($argv[2]);'

    def test_interrupted_profile_write_preserves_original_and_recovery_snapshot(self):
        file=self.data/'accounts.json';file.write_text(json.dumps([{'name':'fixture','cookies':[]}]))
        before=file.read_bytes()
        def limit():signal.signal(signal.SIGXFSZ,signal.SIG_DFL);resource.setrlimit(resource.RLIMIT_FSIZE,(32,32))
        result=self.php(self.profile_script()+'$s->serialize($s->deserialize());',self.runtime,file,preexec_fn=limit)
        self.assertNotEqual(result.returncode,0);self.assertEqual(file.read_bytes(),before)
        self.assertIsInstance(json.loads(file.read_text()),list)

    def test_profile_encryption_migrates_snapshot_and_wrong_key_cannot_overwrite(self):
        file=self.data/'accounts.json';file.write_text(json.dumps([{'name':'fixture','cookies':[{'name':'c_user','value':'123'},{'name':'xs','value':'fixture-cookie-secret'}]}]))
        env={**self.env,'REMASK_ENCRYPT_PROFILE_JSON':'1','REMASK_CREDENTIALS_KEY':'base64:'+base64.b64encode(b'f'*32).decode()}
        code=self.profile_script()+'$s->serialize($s->deserialize());echo json_encode($s->deserialize()[0]->cookies);'
        result=self.php(code,self.runtime,file,env=env);self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('fixture-cookie-secret',result.stdout)
        for path in [file,Path(str(file)+'.previous')]:self.assertNotIn('fixture-cookie-secret',path.read_text())
        before=file.read_bytes();env['REMASK_CREDENTIALS_KEY']='base64:'+base64.b64encode(b'g'*32).decode()
        result=self.php(code,self.runtime,file,env=env);self.assertNotEqual(result.returncode,0)
        self.assertEqual(file.read_bytes(),before)

    def test_legacy_symlink_remains_volume_backed_after_atomic_write(self):
        target=self.data/'persistent-accounts.json';target.write_text('[]')
        link=self.data/'legacy-accounts.json';link.symlink_to(target)
        p=self.php(self.profile_script()+'$s->addOrUpdateAccount(new FbAccount("fixture","","[]"));',self.runtime,link)
        self.assertEqual(p.returncode,0,p.stderr);self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text())[0]['name'],'fixture')

    def test_hex_migration_can_read_base64_v1_and_never_replaces_key_or_card_storage(self):
        file=self.data/'accounts.json'
        file.write_text(json.dumps([{'name':'fixture','cookies':[{'name':'xs','value':'fixture-cookie-secret'}]}]))
        key=b'\xfb\xef'*16
        env={**self.env,'REMASK_ENCRYPT_PROFILE_JSON':'1','REMASK_CREDENTIALS_KEY':key.hex()}
        cards=self.data/'payment-cards';cards.mkdir()
        (cards/'key').write_bytes(b'c'*32);(cards/'cards.json').write_text('{"fixture":"untouched"}')
        originals={p:p.read_bytes() for p in cards.iterdir()}
        code=self.profile_script()+'$s->serialize($s->deserialize());echo json_encode($s->deserialize()[0]->cookies);'
        p=self.php(code,self.runtime,file,env=env);self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('fixture-cookie-secret',p.stdout)
        before=file.read_bytes()
        for value in ['base64:'+base64.b64encode(key).decode(),base64.urlsafe_b64encode(key).decode().rstrip('=')]:
            env['REMASK_CREDENTIALS_KEY']=value
            p=self.php(self.profile_script()+'echo json_encode($s->deserialize()[0]->cookies);',self.runtime,file,env=env)
            self.assertEqual(p.returncode,0,p.stderr);self.assertIn('fixture-cookie-secret',p.stdout)
            self.assertEqual(file.read_bytes(),before)
        self.assertEqual({p:p.read_bytes() for p in cards.iterdir()},originals)
        for p in [file,Path(str(file)+'.previous')]:self.assertNotIn('fixture-cookie-secret',p.read_text())

    def test_parallel_profile_updates_keep_every_profile(self):
        file=self.data/'accounts.json'
        code=self.profile_script()+'$s->addOrUpdateAccount(new FbAccount($argv[3],"","[]"));'
        children=[subprocess.Popen(['php','-r',code,str(self.runtime),str(file),str(i)],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(10)]
        for child in children:
            out,err=child.communicate(timeout=15);self.assertEqual(child.returncode,0,err)
        self.assertEqual(len(json.loads(file.read_text())),10)

    def test_corrupt_draft_store_is_not_silently_overwritten_and_backup_can_restore(self):
        file=self.data/'drafts.json'
        code='require $argv[1]."/classes/MetaDraftStore.php";$s=new MetaDraftStore($argv[2]);$s->save(null,"first","",["x"=>1]);$s->save(null,"second","",["x"=>2]);'
        p=self.php(code,self.runtime,file);self.assertEqual(p.returncode,0,p.stderr)
        file.write_text('{broken')
        p=self.php('require $argv[1]."/classes/MetaDraftStore.php";$s=new MetaDraftStore($argv[2]);$s->save(null,"lost","",["x"=>3]);',self.runtime,file)
        self.assertNotEqual(p.returncode,0);self.assertEqual(file.read_text(),'{broken')
        p=self.php('require $argv[1]."/classes/AtomicJsonFile.php";AtomicJsonFile::restorePrevious($argv[2]);',self.runtime,file)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(len(json.loads(file.read_text())),1)

    def test_default_password_is_refused_and_query_password_cannot_login(self):
        code='$_SERVER["REQUEST_URI"]="/workspace.php";$_GET["password"]="fixture-secret";register_shutdown_function(function(){fwrite(STDERR,"STATUS=".http_response_code());});require $argv[1]."/checkpassword.php";'
        p=self.php(code,self.runtime);self.assertIn('STATUS=401',p.stderr)
        p=self.php(code,self.runtime,env={**self.env,'REMASK_PASSWORD':''});self.assertIn('STATUS=503',p.stderr)

    def test_activity_log_rotates_without_unbounded_file_growth(self):
        file=self.data/'activity.jsonl';file.write_text('x'*1048576)
        code='require $argv[1]."/classes/FileActivityLogStore.php";$s=new FileActivityLogStore($argv[2]);$s->append(["action"=>"fixture"]);'
        p=self.php(code,self.runtime,file,env={**self.env,'REMASK_ACTIVITY_LOG_MAX_BYTES':'1048576'})
        self.assertEqual(p.returncode,0,p.stderr);self.assertLess(file.stat().st_size,1048576)
        self.assertEqual(Path(str(file)+'.previous').stat().st_size,1048576)
        self.assertEqual(json.loads(file.read_text())['action'],'fixture')

    def test_media_quota_preserves_index_and_does_not_copy_rejected_file(self):
        image=self.data/'fixture.png'
        image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII='))
        media=self.data/'media';(media/'files').mkdir(parents=True);(media/'files'/'existing').write_bytes(b'x'*1048576)
        code='require $argv[1]."/classes/MediaLibraryStore.php";$s=new MediaLibraryStore($argv[2]);$s->add($argv[3],"fixture.png");'
        p=self.php(code,self.runtime,media,image,env={**self.env,'REMASK_MEDIA_LIBRARY_TOTAL_BYTES':'1048576'})
        self.assertNotEqual(p.returncode,0);self.assertIn('MEDIA_LIBRARY_QUOTA_EXCEEDED',p.stderr)
        self.assertEqual(len(list((media/'files').iterdir())),1);self.assertEqual(json.loads((media/'index.json').read_text()),[])
