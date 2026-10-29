"""Distinct simulated Meta accounts through one real HTTP/SQLite/WorkerPool engine."""
import asyncio
import copy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.runner import WorkerPool
from app.session import ProfileContextError
from tests import test_private_launch_worker_pipeline as fixture


class AccountPeer:
    """External Meta only. Every identity/asset is scoped to one account."""
    def __init__(self,index):
        self.account=str(100000+index);self.business=str(200000+index);self.actor=str(300000+index)
        self.page=str(400000+index);self.draft=str(500000+index);self.creative=str(800000+index)
        self.operation=str(900000+index);self.index=index
        self.context=SimpleNamespace(account_id=self.account,business_id=self.business,actor_id=self.actor,application_id='654321')
        self.posts=[];self.fragments={};self.images={};self.objects={};self.creative_value={}
        self.rejected=False;self.interrupt_status=False

    async def post(self,path,params,*,before_submit,files=None,multipart=False):
        await before_submit();self.posts.append((path,copy.deepcopy(params)))
        if path.endswith('/adimages'):
            assert path=='/act_'+self.account+'/adimages'
            name,(filename,stream,mime)=next(iter(files.items()))
            raw=stream.read();identity='image-'+self.account+'-'+hashlib.sha256(raw).hexdigest()
            self.images[identity]=raw
            return {'images':{name:{'hash':identity}}}
        if path.endswith('/addrafts'):
            assert path=='/act_'+self.account+'/addrafts'
            return {'id':self.draft}
        if path.endswith('/addraft_fragments'):
            assert path=='/'+self.draft+'/addraft_fragments'
            index=len(self.fragments);fragment=str(600000+self.index*10+index);entity=str(700000+self.index*10+index)
            row={'id':fragment,'account_id':self.account,'ad_draft_id':self.draft,'ad_object_id':entity,
                 'ad_object_type':params['ad_object_type'],'values':copy.deepcopy(params['values']),
                 'parent_ad_object_id':params.get('parent_ad_object_id'),
                 'validation_status':'VALIDATED','active_errors':[]}
            self.fragments[fragment]=row;self.objects[entity]=row
            return {'id':fragment}
        assert path=='/'+self.draft+'/publish'
        return {'success':True,'async_request_set_id':self.operation}

    async def get(self,path,params):
        if path=='/act_'+self.account:
            return {'id':'act_'+self.account,'account_status':2 if self.rejected else 1,
                'business':{'id':self.business},'ad_account_promotable_objects':{'promotable_page_ids':[self.page]}}
        if path=='/act_'+self.account+'/adimages':
            return {'data':[{'hash':value,'account_id':self.account} for value in params['hashes'] if value in self.images]}
        if path=='/addraft_'+self.draft:
            return {'id':self.draft,'account_id':self.account,'author_id':self.actor,'ownership_type':'USER'}
        if path.startswith('/addraftfragment_'):
            return copy.deepcopy(self.fragments[path.removeprefix('/addraftfragment_')])
        if path.endswith('/addraft_publish_statuses'):
            assert path=='/addraft_'+self.draft+'/addraft_publish_statuses'
            if self.interrupt_status:
                self.interrupt_status=False
                raise ConnectionError('offline interrupted status READ')
            return {'data':[{'async_request_set_id':self.operation,'status':'SUCCEEDED','total_count':3,
                'success_count':3,'error_count':0,'canceled_count':0,'initial_count':0,'in_progress_count':0,
                'fragment_statuses':{'data':[{'fragment_id':key,'status':'SUCCESS'} for key in self.fragments]}}]}
        if path=='/'+self.creative:
            return {'id':self.creative,'account_id':self.account,**copy.deepcopy(self.creative_value)}
        row=self.objects[path.removeprefix('/')]
        values={value['field']:copy.deepcopy(value['new_value']) for value in row['values']}
        if row['ad_object_type']=='ad':
            self.creative_value=values['creative'];values['creative']={'id':self.creative}
        return {'id':row['ad_object_id'],'account_id':self.account,**values}


class NativeBatchWorkerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.NativeWorkerPipelineTests.asyncSetUp
    install=fixture.NativeWorkerPipelineTests.install
    start_pool=fixture.NativeWorkerPipelineTests.start_pool
    stop_pools=fixture.NativeWorkerPipelineTests.stop_pools
    submit=fixture.NativeWorkerPipelineTests.submit

    async def terminal(self,job):
        async def wait():
            while True:
                view=await self.store.job_view(job)
                if view['status'] not in {'QUEUED','RUNNING'}:return view
                await asyncio.sleep(.02)
        return await asyncio.wait_for(wait(),30)

    async def batch_pool(self,count,contexts):
        pool=WorkerPool(self.store,concurrency=count);pool._restore_workspace_bindings=AsyncMock()
        async def resolve(profile):
            if profile.endswith('-expired'):
                raise ProfileContextError('offline expired session',retryable=False,category='session_expired')
            return contexts[profile]
        pool.resolver.resolve=AsyncMock(side_effect=resolve)
        self.install(patch.object(fixture.api,'pool',pool))
        await pool.start();self.pools.append(pool);self.pool=pool

    async def test_1_3_10_30_native_profiles_keep_media_scope_and_isolate_failures_and_publish_recovery(self):
        await self.pool.stop()
        for count in (1,3,10,30):
            peers={};contexts={};profiles=[];expected={}
            for index in range(count):
                profile=f'b{count}-{index}';peer=AccountPeer(count*100+index)
                if index==1:profile+='-expired'
                peers[profile]=peer
                contexts[profile]=SimpleNamespace(profile_id=profile,proxy='fixture-proxy-'+profile,
                    cookies={'c_user':peer.actor},ad_accounts=[{'business_id':peer.business,'account_id':peer.account}])
                payload=copy.deepcopy(self.payload)
                payload.update(business_id=peer.business,ad_account_id=peer.account,page_id=peer.page,
                               launch_key='native-intent-'+profile)
                expected[profile]='SUCCESS'
                if index==1:expected[profile]='FAILED'
                if index==2:
                    raw=b'invalid image '+profile.encode();sha=hashlib.sha256(raw).hexdigest();identity='m-'+sha[:24]
                    path=self.root/'files'/(identity+'.png');path.write_bytes(raw)
                    self.extra_records[identity]={'id':identity,'sha256':sha,'path':str(path),'size_bytes':len(raw),
                                                 'media_type':'image','mime_type':'image/png'}
                    payload['launch']['creative']['media_library_id']=identity;expected[profile]='FAILED'
                if index==3:peer.rejected=True;expected[profile]='FAILED'
                profiles.append({'profile_id':profile,'tasks':[{'action':'private_launch',
                    'idempotency_key':payload['launch_key'],'payload':payload}]})
            async def opened(web,**target):
                profile=web.profile_name;peer=peers[profile]
                assert target=={'account_id':peer.account,'business_id':peer.business,'actor_id':peer.actor,'allow_draft_gate_recovery':True}
                assert contexts[profile].proxy=='fixture-proxy-'+profile
                assert web.context is contexts[profile]
                assert web.context.cookies['c_user']==peer.actor
                assert len(self.pool.queue.active)<=count
                return peer
            self.meta_open.side_effect=opened
            async def page(web,*,target,client):
                assert target['page_id']==client.page
                return {'page_id':client.page,'checked_live':True,'publication_access_verified':False}
            async def funding(web,*,account,business_id):
                peer=peers[web.profile_name]
                assert (account,business_id)==(peer.account,peer.business)
                return {'account_id':account,'business_id':business_id,'payment_account_id':'333333',
                    'account_scope_verified':True,'business_scope_verified':True,'payment_account_relation_verified':True,
                    'methods_query_verified':True,'checked_live':True,'card_linked':True,'inventory_complete':True,
                    'payment_methods':[{'type':'Visa','last4':'1234','needs_verification':False,'verification_tasks_observed':True,
                        'verification_tasks':[],'card_confirmation_status':'CLEAR','is_expired':False}]}
            self.install(patch('app.private_draft_pages.verify_draft_page',side_effect=page))
            self.install(patch('app.static_payment_read.inspect_methods',side_effect=funding))
            await self.batch_pool(count,contexts)
            request={'idempotency_key':f'native-batch-{count}','profiles':profiles}
            job=await self.submit(request);view=await self.terminal(job)
            self.assertEqual({row['profile_id']:row['status'] for row in view['items']},expected)
            self.assertEqual(await self.submit(request),job)
            good=[row for row in profiles if expected[row['profile_id']]=='SUCCESS']
            for profile,peer in peers.items():
                if expected[profile]=='FAILED':self.assertEqual(peer.posts,[])
                else:
                    self.assertEqual(len(peer.images),1);self.assertEqual(len(peer.fragments),3)
                    self.assertTrue(all(row['account_id']==peer.account for row in peer.fragments.values()))
                    for fragment in peer.fragments.values():
                        values={row['field']:row['new_value'] for row in fragment['values']}
                        self.assertEqual(values['status'],'PAUSED')
                        if fragment['ad_object_type']=='ad':
                            self.assertEqual(values['creative']['object_story_spec']['page_id'],peer.page)
                            self.assertIn(values['creative']['object_story_spec']['link_data']['image_hash'],peer.images)
            publication=copy.deepcopy(good)
            first=publication[0]['profile_id'];peers[first].interrupt_status=True
            for row in publication:row['tasks'][0]['payload']['publish_saved_draft']=True
            pub_job=await self.submit({'idempotency_key':f'native-publish-{count}','profiles':publication})
            pub_view=await self.terminal(pub_job)
            self.assertEqual({row['profile_id']:row['status'] for row in pub_view['items']},
                {row['profile_id']:('FAILED' if row['profile_id']==first else 'SUCCESS') for row in publication})
            before={profile:len(peer.posts) for profile,peer in peers.items()}
            await self.pool.stop();await self.batch_pool(count,contexts)
            resumed=await self.submit({'idempotency_key':f'native-resume-{count}','profiles':[publication[0]]})
            self.assertEqual((await self.terminal(resumed))['status'],'SUCCESS')
            self.assertEqual({profile:len(peer.posts) for profile,peer in peers.items()},before)
            await self.pool.stop()
