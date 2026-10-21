"""Real HTTP admission, SQLite queue, WorkerPool and native Launch execution.

Only profile storage/session and external Meta peers are simulated. These cases
never invoke PrivateLaunchService.run directly or register an alternate engine.
"""
import asyncio
import copy
import hashlib
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

import main as api
from app import runner
from app.private_launch_media import LaunchMediaResolver
from app.runner import WorkerPool
from app.store import JobStore
from tests.test_private_ads_launch import ImageClient
from tests.test_private_ads_carousel import CarouselClient


class VideoClient(ImageClient):
    async def post(self,path,params,*,before_submit,files=None,multipart=False):
        if not path.endswith('/advideos'):
            return await super().post(path,params,before_submit=before_submit,files=files)
        await before_submit(); self.posts.append((path,copy.deepcopy(params)))
        if params['upload_phase']=='start':
            return {'upload_session_id':'999991','video_id':'999992','start_offset':'0','end_offset':str(params['file_size'])}
        if params['upload_phase']=='transfer':
            end=int(params['start_offset'])+len(files['video_file_chunk'][1])
            return {'start_offset':str(end),'end_offset':str(end)}
        return {'video_id':'999992'}
    async def get(self,path,params):
        if path=='/act_111111/advideos':return {'data':[{'id':'999992'}]}
        if path=='/999992':return {'id':'999992','status':{'video_status':'ready'},
            'thumbnails':{'data':[{'uri':'https://example.invalid/thumbnail.jpg','is_preferred':True}]}}
        return await super().get(path,params)


class OfflineProfileSession:
    def __init__(self, context): self.context = context
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def facebook_web(self): return SimpleNamespace(profile_name=self.context.profile_id,context=self.context)


class NativeWorkerPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'files').mkdir()
        original = self.root/'original.png'; Image.new('RGB',(64,48),(10,20,30)).save(original,'PNG')
        raw = original.read_bytes(); sha = hashlib.sha256(raw).hexdigest(); identity = 'm-'+sha[:24]
        self.path = self.root/'files'/(identity+'.png'); self.path.write_bytes(raw)
        self.record = {'id':identity,'path':str(self.path),'sha256':sha,'size_bytes':len(raw),
                       'media_type':'image','mime_type':'image/png'}
        self.extra_records = {}
        self.resolver = LaunchMediaResolver(); self.resolver.root = self.root
        self.meta = ImageClient()
        self.payload = {'business_id':'222222','ad_account_id':'111111','page_id':'555555','launch_key':'immutable-intent',
            'launch':{'campaign':{'name':'HTTP queued campaign','objective':'OUTCOME_TRAFFIC','daily_budget':500},
                'adset':{'name':'HTTP queued adset','optimization_goal':'LINK_CLICKS','billing_event':'IMPRESSIONS',
                    'destination_type':'WEBSITE','targeting':{'geo_locations':{'countries':['UA']}}},
                'creative':{'name':'HTTP queued creative','message':'Actual file','headline':'Headline',
                    'link':'https://example.com/','format':'SINGLE','media_library_id':identity,'call_to_action':'LEARN_MORE'},
                'ad':{'name':'HTTP queued ad'},'execution_mode':'DRAFT',
                'delivery':{'campaign':'PAUSED','adset':'PAUSED','ad':'PAUSED'}}}
        self.store = JobStore(self.root/'jobs.sqlite'); await self.store.init()
        self.pools = []
        self.addAsyncCleanup(self.stop_pools)
        self.install(patch.object(api,'store',self.store))
        self.install(patch.object(api,'mirror',SimpleNamespace(enabled=False)))
        self.install(patch.object(api,'API_KEY','worker-fixture-only'))
        self.install(patch.object(runner,'ProfileSession',OfflineProfileSession))
        self.meta_open = self.install(patch('app.private_ads_launch.AdsManagerPrivateHttp.open',new=AsyncMock(return_value=self.meta)))
        self.install(patch('app.private_draft_pages.verify_draft_page',new=AsyncMock(return_value={
            'page_id':'555555','checked_live':True,'publication_access_verified':False})))
        # Simulate only the PHP file-record read; run real managed path/hash and
        # content inspection, not a synthetic media acceptance response.
        async def resolve(refs):
            records={self.record['id']:self.record,**self.extra_records}
            self.assertTrue(all(r['id'] in records for r in refs))
            return [await asyncio.to_thread(self.resolver._validate,records[r['id']]) for r in refs]
        self.install(patch('app.private_launch_media.LaunchMediaResolver.resolve',side_effect=resolve))
        self.install(patch('app.static_payment_read.inspect_methods',new=AsyncMock(return_value={
            'account_id':'111111','business_id':'222222','payment_account_id':'333333',
            'account_scope_verified':True,'business_scope_verified':True,'payment_account_relation_verified':True,
            'methods_query_verified':True,'checked_live':True,'card_linked':True,'inventory_complete':True,
            'payment_methods':[{'type':'Visa','last4':'1234','needs_verification':False,
                'verification_tasks_observed':True,'verification_tasks':[],'card_confirmation_status':'CLEAR','is_expired':False}]})))
        await self.start_pool()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://worker.fixture',
            headers={'X-Remask-Worker-Key':'worker-fixture-only'})
        self.addAsyncCleanup(self.http.aclose)

    def install(self, patcher):
        result = patcher.start(); self.addCleanup(patcher.stop); return result

    async def stop_pools(self):
        for pool in self.pools: await pool.stop()

    async def start_pool(self):
        pool = WorkerPool(self.store,concurrency=1)
        pool._restore_workspace_bindings = AsyncMock()
        pool.resolver.resolve = AsyncMock(return_value=SimpleNamespace(profile_id='38',proxy='fixture-profile38-proxy',
            cookies={'c_user':'123456'},ad_accounts=[{'business_id':'222222','account_id':'111111'}]))
        self.install(patch.object(api,'pool',pool))
        await pool.start(); self.pools.append(pool); self.pool = pool

    def request(self, key='create-draft', publish=False):
        payload = copy.deepcopy(self.payload)
        if publish: payload['publish_saved_draft']=True
        return {'idempotency_key':key,'profiles':[{'profile_id':'38','tasks':[
            {'action':'private_launch','idempotency_key':'immutable-intent','payload':payload}]}]}

    async def submit(self, request):
        response = await self.http.post('/api/v1/jobs',json=request)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['job_id']

    async def terminal(self, job):
        async def wait():
            while True:
                response = await self.http.get('/api/v1/jobs/'+job)
                self.assertEqual(response.status_code,200,response.text)
                view = response.json()
                if view['status'] not in {'QUEUED','RUNNING'}:return view
                await asyncio.sleep(.005)
        return await asyncio.wait_for(wait(),10)

    async def test_http_job_executes_same_immutable_task_then_publishes_saved_draft_after_restart(self):
        request = self.request(); job = await self.submit(request); view = await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        result = view['items'][0]['tasks'][0]['result']
        self.assertTrue(result['draft_verified']); self.assertFalse(result['published'])
        tasks = await self.store.tasks(view['items'][0]['id'])
        self.assertEqual(tasks[0]['payload'],{**request['profiles'][0]['tasks'][0]['payload'],'variables':{}})
        self.assertEqual(self.meta.uploads,1); self.assertEqual(len(self.meta.posts),4)
        self.assertEqual(await self.submit(request),job)
        await self.pool.stop(); await self.start_pool()
        self.assertEqual(await self.submit(request),job)
        published_job = await self.submit(self.request('publish-retained',publish=True))
        published = await self.terminal(published_job)
        self.assertEqual(published['status'],'SUCCESS',published)
        receipt = published['items'][0]['tasks'][0]['result']
        self.assertTrue(receipt['verified']); self.assertFalse(receipt['delivery_running_verified'])
        self.assertEqual(self.meta.uploads,1); self.assertEqual(len(self.meta.fragments),3)
        self.assertEqual(len(self.meta.posts),5)

    async def test_invalid_media_reports_actual_worker_error_without_remote_post(self):
        raw = b'corrupt content'; sha = hashlib.sha256(raw).hexdigest(); identity='m-'+sha[:24]
        path = self.root/'files'/(identity+'.png'); path.write_bytes(raw)
        self.record.update(id=identity,path=str(path),sha256=sha,size_bytes=len(raw))
        self.payload['launch']['creative']['media_library_id']=identity
        request = self.request(); job = await self.submit(request); view = await self.terminal(job)
        self.assertEqual(view['status'],'FAILED')
        self.assertEqual(view['items'][0]['tasks'][0]['error_code'],'MEDIA_CONTENT_INVALID')
        self.assertEqual(self.meta.posts,[])
        self.assertEqual(await self.submit(request),job)
        self.assertEqual(self.meta.posts,[])

    async def test_rejected_ad_stays_one_retained_hierarchy_and_job_error_not_success(self):
        get = self.meta.get
        async def reject(path, params):
            row = await get(path,params)
            if row.get('ad_object_type')=='ad':
                row.update(validation_status='HAS_ERRORS',active_errors=[{'error_code':1359188,'field':'billing'}])
            return row
        self.meta.get = reject
        request = self.request(); job = await self.submit(request); view = await self.terminal(job)
        self.assertEqual(view['status'],'FAILED')
        self.assertEqual(view['items'][0]['tasks'][0]['error_code'],'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        self.assertEqual(len(self.meta.fragments),3); self.assertEqual(len(self.meta.posts),4)
        self.assertEqual(await self.submit(request),job)
        self.assertEqual(len(self.meta.fragments),3); self.assertEqual(len(self.meta.posts),4)

    async def test_actual_video_file_reaches_worker_phased_upload_and_saved_publication(self):
        path=self.root/'source.mp4'
        await asyncio.to_thread(subprocess.run,['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
            'color=c=blue:s=64x48:r=10:d=1','-c:v','libx264','-pix_fmt','yuv420p','-threads','1','-y',str(path)],
            check=True,timeout=20)
        raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest(); identity='m-'+sha[:24]
        managed=self.root/'files'/(identity+'.mp4');managed.write_bytes(raw)
        self.record.update(id=identity,path=str(managed),sha256=sha,size_bytes=len(raw),media_type='video',mime_type='video/mp4')
        self.payload['launch']['creative']['media_library_id']=identity
        self.meta=VideoClient();self.meta_open.return_value=self.meta
        job=await self.submit(self.request('video-draft'));view=await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        phases=[params['upload_phase'] for path,params in self.meta.posts if path.endswith('/advideos')]
        self.assertEqual(phases,['start','transfer','finish'])
        job=await self.submit(self.request('video-publish',publish=True));view=await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        self.assertEqual(self.meta.expected_creative['object_story_spec']['video_data']['video_id'],'999992')
        self.assertEqual(len(self.meta.posts),8)

    async def test_ordered_carousel_runs_through_actual_worker_with_distinct_valid_images(self):
        path=self.root/'second.png';Image.new('RGB',(80,80),(90,30,20)).save(path,'PNG')
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest();identity='m-'+sha[:24]
        managed=self.root/'files'/(identity+'.png');managed.write_bytes(raw)
        self.extra_records[identity]={'id':identity,'path':str(managed),'sha256':sha,'size_bytes':len(raw),
            'media_type':'image','mime_type':'image/png'}
        creative=self.payload['launch']['creative'];creative.pop('media_library_id')
        creative.update(format='CAROUSEL',carousel_media_library_ids=[self.record['id'],identity],
            carousel_cards=[{'headline':'First','link':'https://example.com/one','call_to_action':'LEARN_MORE'},
                {'headline':'Second','description':'Second copy','link':'https://example.com/two','call_to_action':'SHOP_NOW'}])
        self.meta=CarouselClient();self.meta_open.return_value=self.meta
        job=await self.submit(self.request('carousel-draft'));view=await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        job=await self.submit(self.request('carousel-publish',publish=True));view=await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        cards=self.meta.expected_creative['object_story_spec']['link_data']['child_attachments']
        self.assertEqual([row['image_hash'] for row in cards],[self.record['sha256'][:32],sha[:32]])
        self.assertEqual([row['call_to_action']['type'] for row in cards],['LEARN_MORE','SHOP_NOW'])
        self.assertEqual([row['link'] for row in cards],['https://example.com/one','https://example.com/two'])
        self.assertEqual(self.meta.uploads,2);self.assertEqual(len(self.meta.posts),5)

    async def test_saved_publish_receipt_resumes_only_reads_after_worker_restart(self):
        job=await self.submit(self.request());self.assertEqual((await self.terminal(job))['status'],'SUCCESS')
        get=self.meta.get
        async def interrupted(path,params):
            if path.endswith('/addraft_publish_statuses'):
                raise ConnectionError('offline lost status read after accepted publication')
            return await get(path,params)
        self.meta.get=interrupted
        job=await self.submit(self.request('interrupted-publication',publish=True))
        self.assertEqual((await self.terminal(job))['status'],'FAILED')
        self.assertEqual(len(self.meta.posts),5)
        self.meta.get=get
        await self.pool.stop();await self.start_pool()
        job=await self.submit(self.request('resume-publication',publish=True));view=await self.terminal(job)
        self.assertEqual(view['status'],'SUCCESS',view)
        self.assertTrue(view['items'][0]['tasks'][0]['result']['verified'])
        self.assertEqual(len(self.meta.posts),5);self.assertEqual(self.meta.uploads,1)

    async def test_inconsistent_configuration_is_rejected_by_worker_before_any_post(self):
        original=copy.deepcopy(self.payload)
        for key in ('media','targeting'):
            self.payload=copy.deepcopy(original)
            if key=='media':self.payload['launch']['creative']['existing_image_hash']='conflicting-image-hash'
            else:self.payload['launch']['adset']['targeting']['publisher_platforms']=['not_a_platform']
            job=await self.submit(self.request('invalid-'+key));view=await self.terminal(job)
            self.assertEqual(view['status'],'FAILED',view)
            self.assertEqual(self.meta.posts,[])
