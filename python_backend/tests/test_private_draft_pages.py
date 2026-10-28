"""Draft Page reads must never turn Business metadata into publication proof."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_draft_pages import read_draft_pages, verify_draft_page
from app.private_ads_reads import AdsManagerReadCatalog
from app.private_ads_catalog import AdsManagerRouteCatalog
from app.static_meta_contracts import contract_metadata
from app.provisioning.models import ProvisioningError


class DraftPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.target = {'business_id':'222222','page_id':'555555','ad_account_id':'111111'}
        self.web = SimpleNamespace(profile=SimpleNamespace(cookies={'c_user':'123456'}),
            bootstrap=AsyncMock(return_value=SimpleNamespace(actor_id='123456')),
            graphql=AsyncMock(side_effect=self.query))
        self.client = SimpleNamespace(_native_account_id='999999')
        self.config = {'data':{'business':{'id':'222222','scheduledForDeletion':False,
            'businessUser':{'id':'666666'},'bizKitSettingsConfig':{'assetConfigs':[
                {'assetType':kind,'hasUserPermissions':True,'permissionTasksConfig':[
                    {'taskID':task,'taskPermissionType':'FULL_CONTROL_TASK','impliedTaskIDs':None}]}
                for kind,task in [('PAGE','100001'),('AD_ACCOUNT','100002')]]}}}}
        self.page = {'data':{'page':{'id':'555555','name':'Page',
            'ownerBusiness':{'id':'222222'}}}}
        self.rights = {}
        for asset,task in [('555555','100001'),('999999','100002')]:
            self.rights[asset] = {'data':{'business_object_rendered_in_ui':{'user_assigned_permissions':{
                'asset':{'business_object_id':'111111' if asset == '999999' else asset},
                'user':{'id':'666666'},'current_business':{'id':'222222'},
                'assigned_permission_task_ids':[task]}}}}
        self.calls = []

    async def query(self, doc_id, variables, **kwargs):
        friendly = kwargs['friendly_name']
        self.calls.append((friendly, copy.deepcopy(variables)))
        self.assertEqual(kwargs['endpoint_url'], 'https://business.facebook.com/api/graphql/')
        self.assertEqual(kwargs['business_context_id'], '222222')
        if friendly == contract_metadata('CONFIG')['friendly_name']:
            return copy.deepcopy(self.config)
        if friendly == contract_metadata('PAGE')['friendly_name']:
            return copy.deepcopy(self.page)
        if friendly == contract_metadata('RIGHTS')['friendly_name']:
            return copy.deepcopy(self.rights[variables['assetID']])
        raise AssertionError('No mutation or other query allowed: ' + friendly)

    async def verify(self):
        return await verify_draft_page(self.web, target=self.target, client=self.client)

    async def test_fresh_exact_owner_and_two_operator_grants_are_draft_only(self):
        result = await self.verify()
        self.assertTrue(result['checked_live'])
        self.assertTrue(result['operator_full_control_verified'])
        self.assertTrue(result['account_operator_full_control_verified'])
        self.assertFalse(result['publication_access_verified'])
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.web.private_only)
        self.assertEqual([v['assetID'] for f,v in self.calls if f == contract_metadata('RIGHTS')['friendly_name']],
            ['555555','999999'])

    async def test_wrong_actor_stops_before_graphql(self):
        self.web.bootstrap.return_value.actor_id = '888888'
        with self.assertRaises(ProvisioningError):
            await self.verify()
        self.web.graphql.assert_not_awaited()

    async def test_wrong_deleted_or_unscoped_business_is_rejected(self):
        original = copy.deepcopy(self.config)
        for case in ('foreign','deleted','operator','config','errors'):
            self.config = copy.deepcopy(original)
            node = self.config['data']['business']
            if case == 'foreign': node['id'] = '333333'
            elif case == 'deleted': node['scheduledForDeletion'] = True
            elif case == 'operator': node['businessUser'] = None
            elif case == 'config': node['bizKitSettingsConfig'] = None
            else: self.config['errors'] = [{'message':'denied'}]
            with self.subTest(case=case), self.assertRaises(ProvisioningError):
                await self.verify()

    async def test_foreign_missing_or_unowned_page_is_rejected(self):
        original = copy.deepcopy(self.page)
        for case in ('foreign','missing','unowned','wrong_page','errors'):
            self.page = copy.deepcopy(original)
            node = self.page['data']['page']
            if case == 'foreign': node['ownerBusiness']['id'] = '333333'
            elif case == 'missing': node.pop('ownerBusiness')
            elif case == 'unowned': node['ownerBusiness'] = None
            elif case == 'wrong_page': node['id'] = '777777'
            else: self.page['errors'] = [{'message':'denied'}]
            with self.subTest(case=case), self.assertRaises(ProvisioningError):
                await self.verify()

    async def test_available_rights_are_not_assignments_and_exact_relations_required(self):
        original = copy.deepcopy(self.rights)
        for asset in original:
            for case in ('tasks','asset','user','business','errors'):
                self.rights = copy.deepcopy(original)
                payload = self.rights[asset]
                record = payload['data']['business_object_rendered_in_ui']['user_assigned_permissions']
                if case == 'tasks': record['assigned_permission_task_ids'] = []
                elif case == 'asset': record['asset']['business_object_id'] = '777777'
                elif case == 'user': record['user']['id'] = '777777'
                elif case == 'business': record['current_business']['id'] = '777777'
                else: payload['errors'] = [{'message':'denied'}]
                with self.subTest(asset=asset,case=case), self.assertRaises(ProvisioningError):
                    await self.verify()

    async def test_ui_asset_id_does_not_substitute_for_canonical_settings_account(self):
        record = self.rights['999999']['data']['business_object_rendered_in_ui']['user_assigned_permissions']
        record['asset']['business_object_id'] = '999999'
        with self.assertRaises(ProvisioningError):
            await self.verify()

    async def test_draft_picker_uses_fresh_exact_business_list_without_publication_claim(self):
        read = AsyncMock(return_value=([{'id':'555555','name':'Page','business_id':'222222'}], True))
        with patch('app.private_business_fan_page_create.read_pages', new=read):
            result = await read_draft_pages(self.web, account='111111',business='222222')
        read.assert_awaited_once_with(self.web, '222222')
        self.assertFalse(result['ad_account_page_access_verified'])
        self.assertFalse(result['data'][0]['publication_access_verified'])
        self.assertEqual(result['execution_mode'], 'DRAFT')
        self.web.graphql.assert_not_awaited()

    async def test_picker_skips_cdn_module_refresh_and_restores_flag(self):
        observed = []

        async def read_pages(web, business):
            observed.append(web.defer_module_refresh)
            return ([{'id':'555555','name':'Page','business_id':'222222'}], True)

        with patch('app.private_business_fan_page_create.read_pages',
                new=AsyncMock(side_effect=read_pages)):
            await read_draft_pages(self.web,account='111111',business='222222')
        self.assertEqual(observed, [True])
        self.assertFalse(hasattr(self.web, 'defer_module_refresh'))

        self.web.defer_module_refresh = False

        async def fail_read(web, business):
            self.assertTrue(web.defer_module_refresh)
            raise RuntimeError('read failed')

        with patch('app.private_business_fan_page_create.read_pages',
                new=AsyncMock(side_effect=fail_read)):
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                await read_draft_pages(self.web,account='111111',business='222222')
        self.assertFalse(self.web.defer_module_refresh)

    async def test_incomplete_picker_never_uses_partial_or_cached_business_pages(self):
        with patch('app.private_business_fan_page_create.read_pages',
                new=AsyncMock(return_value=([{'id':'555555'}],False))):
            with self.assertRaises(ProvisioningError):
                await read_draft_pages(self.web,account='111111',business='222222')

    async def test_catalog_draft_read_omits_publication_projection_but_publish_keeps_it(self):
        catalog = AdsManagerReadCatalog(AdsManagerRouteCatalog())
        http = SimpleNamespace(get=AsyncMock(return_value={'id':'act_111111',
            'business':{'id':'222222'},'account_status':1,
            'ad_account_promotable_objects':{'promotable_page_ids':['555555']}}))
        values = {'ad_account_id':'111111','business_id':'222222','execution_mode':'DRAFT'}
        rows = ([{'id':'555555','name':'Page','business_id':'222222'}],True)
        open_ads = AsyncMock(return_value=http)
        with patch('app.private_ads_reads.AdsManagerPrivateHttp.open',new=open_ads), \
                patch('app.private_business_fan_page_create.read_pages',new=AsyncMock(return_value=rows)):
            result = await catalog.pages(self.web,values)
            self.assertFalse(result['ad_account_page_access_verified'])
            open_ads.assert_not_awaited()
            result = await catalog.pages(self.web,{**values,'execution_mode':'PUBLISH'})
            self.assertTrue(result['ad_account_page_access_verified'])
            open_ads.assert_awaited_once()
            self.assertIn('ad_account_promotable_objects',http.get.call_args.args[1]['fields'])


if __name__ == '__main__':
    unittest.main()
