"""Offline full-flow tests. Every request contract here is synthetic."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore
from app.private_launch_extensions import LaunchExtensionCatalog, extension_contract
from app.private_advertising_reads import AdvertisingReadCatalog, AdvertisingResourceStore
from app.provisioning.models import ProvisioningError
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_launch_publication import family_contract, PublicationWeb, IDS, RELATIONS


def delivery_contract(step, index):
    read = copy.deepcopy(family_contract().row['verification'][step])
    return extension_contract('ACTIVATE_' + step, {'evidence': {
        'artifact_sha256':'a'*64, 'sender_sha256':'b'*64, 'read_sha256':'c'*64,
        'observed_at':'2026-10-12'}, 'contract': {
        'doc_id':str(50010+index), 'friendly_name':'FixtureStatusMutation',
        'variables':{'account':'{{ad_account_id}}','id':'{{entity_id}}','status':'{{target_status}}'},
        'output_paths':{}, 'verification':read}})


class LifecycleWeb(PublicationWeb):
    def __init__(self):
        super().__init__()
        self.status='SUCCEEDED'
        self.delivery={step:'PAUSED' for step in RELATIONS}
        self.changes=[]
        self.published_payload=None
        self.foreign_read=False

    async def graphql(self, doc_id, variables, **options):
        if doc_id == '50001':
            self.published_payload=copy.deepcopy(variables['input']['settings'])
        if doc_id in {'50010','50011','50012'}:
            await options['before_submit']()
            step=('CAMPAIGN','ADSET','AD')[int(doc_id)-50010]
            self.changes.append((step, variables['status'], variables['id']))
            self.delivery[step]=variables['status']
            return {'data':{'change':True}}
        response=await super().graphql(doc_id,variables,**options)
        if doc_id in {'99001','99002','99003','99004'}:
            step=list(RELATIONS)[int(doc_id)-99001]
            response['data']['object']['status']=self.delivery[step]
            if self.foreign_read:
                response['data']['object']['account_id']='999999999'
        return response


class AdvertisingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.state=ProvisioningStateStore(str(root/'state.sqlite'))
        await self.state.init()
        self.docids=patch('app.facebook_docids.STORE_PATH',root/'docids.json')
        self.docids.start();self.addCleanup(self.docids.stop)
        contracts=PrivateLaunchContractStore('{}',path=root/'contracts.json')
        contracts.extensions=LaunchExtensionCatalog()
        contracts.extensions.operations['PUBLISH_FAMILY']=family_contract()
        for index,step in enumerate(('CAMPAIGN','ADSET','AD')):
            contracts.extensions.operations['ACTIVATE_'+step]=delivery_contract(step,index)
        self.service=PrivateLaunchService(self.state,contracts=contracts)
        self.service._review_preflight=AsyncMock(return_value={'payment_selection_required':False})
        self.web=LifecycleWeb()
        self.session=SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.context=SimpleNamespace(cookies={'c_user':'888888888'})
        self.payload={'business_id':'987654321','ad_account_id':'123456789','page_id':'555555555',
            'launch_key':'full-flow-fixture','launch':{step.lower():{'name':'fixture'} for step in RELATIONS}}

    async def launch(self):
        return await self.service.run(item_id='fixture',profile_id='fixture',context=self.context,
            session=self.session,payload=self.payload)

    async def delivery(self,status,key):
        return await self.service.run_delivery(item_id='fixture',profile_id='fixture',context=self.context,
            session=self.session,payload={key:self.payload[key] for key in ('business_id','ad_account_id','page_id')}|
                {'source_launch_key':self.payload['launch_key'],'delivery_key':key,'status':status})

    async def test_full_active_launch_creates_paused_then_activates_children_first(self):
        self.payload['launch']['delivery']={name:'ACTIVE' for name in ('campaign','adset','ad')}
        result=await self.launch()
        self.assertEqual(result['status'],'SUCCESS')
        self.assertEqual(result['entities'],IDS)
        self.assertTrue(all(self.web.published_payload[name]['status']=='PAUSED' for name in ('campaign','adset','ad')))
        self.assertNotIn('delivery',self.web.published_payload)
        self.assertEqual([row[0] for row in self.web.changes],['AD','ADSET','CAMPAIGN'])
        await self.launch()
        self.assertEqual(self.web.posts,1)
        self.assertEqual(len(self.web.changes),3)

    async def test_delivery_uses_saved_objects_and_pause_orders_campaign_first(self):
        await self.launch()
        active=await self.delivery('ACTIVE','activate-fixture')
        paused=await self.delivery('PAUSED','pause-fixture')
        self.assertEqual(active['entities'],IDS)
        self.assertEqual(paused['entities'],IDS)
        self.assertEqual([row[0] for row in self.web.changes],['AD','ADSET','CAMPAIGN','CAMPAIGN','ADSET','AD'])
        self.assertEqual(self.web.posts,1)
        await self.delivery('PAUSED','pause-fixture')
        self.assertEqual(len(self.web.changes),6)

    async def test_foreign_source_read_stops_before_status_mutation(self):
        await self.launch()
        self.web.foreign_read=True
        with self.assertRaises(ProvisioningError) as caught:
            await self.delivery('ACTIVE','foreign-read-fixture')
        self.assertEqual(caught.exception.code,'PRIVATE_LAUNCH_DELIVERY_SCOPE_UNCONFIRMED')
        self.assertEqual(self.web.changes,[])

    async def test_plan_stays_offline_and_unconfigured_requests_are_reported(self):
        service=PrivateLaunchService(self.state)
        plan=service.plan(profile_id='fixture',payload=self.payload)
        self.assertFalse(plan['ready'])
        self.assertFalse(plan['checked_live'])
        self.assertEqual(plan['mutation_posts'],0)
        self.assertEqual(plan['missing_contracts'],['CAMPAIGN','ADSET','CREATIVE','AD'])
        self.assertEqual(self.web.posts,0)

    async def test_selected_pixel_with_paginated_read_is_validated_before_publication(self):
        from app.private_advertising_reads import AdvertisingReadCatalog
        row=copy.deepcopy(page_catalog().rows['pages'])
        entry={'evidence':{'artifact_sha256':'a'*64,'sender_sha256':'b'*64,
            'read_sha256':'c'*64,'observed_at':'2026-10-12'},'contract':row}
        self.service.reads=AdvertisingReadCatalog({'reads':{'pixels':entry}})
        self.payload['launch']['adset']['promoted_object']={'pixel_id':'777777777'}
        original=self.web.graphql
        async def read(doc_id,variables,**options):
            if doc_id=='99009':
                self.assertEqual(variables['cursor'],'')
                self.assertEqual(self.web.posts,0)
                return {'data':{'account':{'id':'123456789','pages':[{'id':'777777777','name':'Pixel'}],
                    'has_next':False,'cursor':''}}}
            return await original(doc_id,variables,**options)
        self.web.graphql=read
        result=await self.launch()
        self.assertEqual(result['status'],'SUCCESS')
        self.assertTrue(result['preflight']['asset_checks']['pixels']['verified'])

    async def test_non_scalar_delivery_status_is_validation_error(self):
        with self.assertRaises(ProvisioningError) as caught:
            await self.delivery({},'malformed-fixture')
        self.assertEqual(caught.exception.code,'PRIVATE_LAUNCH_STATUS_INVALID')


def page_catalog():
    return AdvertisingReadCatalog({'reads':{'pages':{'evidence':{
        'artifact_sha256':'a'*64,'sender_sha256':'b'*64,'read_sha256':'c'*64,'observed_at':'2026-10-12'},
        'contract':{'operation_kind':'query','doc_id':'99009','friendly_name':'FixturePagesQuery',
            'variables':{'account':'{{ad_account_id}}','cursor':'{{cursor}}'},
            'account_path':'data.account.id','rows_path':'data.account.pages',
            'fields':{'id':'id','name':'name'},'permission_path':'can_advertise',
            'pagination':{'has_next_path':'data.account.has_next','cursor_path':'data.account.cursor'}}}}})


class AdvertisingResourceTests(unittest.IsolatedAsyncioTestCase):
    def values(self):
        return {'profile_id':'fixture','business_id':'987654321','ad_account_id':'123456789','query':'','limit':100}

    def response(self,account='123456789',has_next=False,cursor=''):
        return {'data':{'account':{'id':account,'pages':[
            {'id':'555555555','name':'Allowed','can_advertise':True},
            {'id':'666666666','name':'Owned only','can_advertise':False}],
            'has_next':has_next,'cursor':cursor}}}

    async def test_pages_need_exact_account_and_explicit_permission(self):
        web=SimpleNamespace(graphql=AsyncMock(return_value=self.response()))
        result=await page_catalog().pages(web,self.values())
        self.assertEqual([row['id'] for row in result['data']],['555555555'])
        self.assertTrue(result['account_scope_verified'])
        web.graphql.return_value=self.response(account='999999999')
        with self.assertRaises(ProvisioningError):
            await page_catalog().pages(web,self.values())

    async def test_repeated_pagination_cursor_never_produces_complete_catalog(self):
        web=SimpleNamespace(graphql=AsyncMock(return_value=self.response(has_next=True,cursor='same')))
        with self.assertRaises(ProvisioningError):
            await page_catalog().pages(web,self.values())
        self.assertEqual(web.graphql.await_count,2)

    async def test_cached_permission_is_not_live_and_actor_or_query_changes_do_not_reuse_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=AdvertisingResourceStore(page_catalog());store.root=Path(tmp)
            web=SimpleNamespace(graphql=AsyncMock(return_value=self.response()))
            await store.get(name='pages',values=self.values(),actor_id='111111111',force=True,web=web)
            cached=await store.get(name='pages',values=self.values(),actor_id='111111111')
            self.assertFalse(cached['checked_live'])
            self.assertFalse(cached['account_scope_verified'])
            self.assertNotIn('ad_account_page_access_verified',cached['data'][0])
            for actor,values in [('222222222',self.values()),('111111111',{**self.values(),'limit':500})]:
                result=await store.get(name='pages',values=values,actor_id=actor)
                self.assertEqual(result['data'],[])
            self.assertEqual(web.graphql.await_count,1)
