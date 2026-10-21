"""Retained objective repair: no object creation, replay or journal rewriting."""
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_objective_repair import REVIEWED_SOURCE
from app.private_launch import PrivateLaunchContractStore, PrivateLaunchService
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture


class ObjectiveRepairTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LaunchTests.asyncSetUp
    launch = fixture.LaunchTests.launch

    async def retained(self, *, source=REVIEWED_SOURCE, rejected=True):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        self.owner.ads_manager.source_digest = source
        read = self.client.get
        async def original_read(path, params):
            row = await read(path, params)
            if row.get('ad_object_type'):
                row.update(status='EDITING', publish_status=None, active_errors=[])
                if row['ad_object_type'] == 'ad' and rejected:
                    row.update(validation_status='HAS_ERRORS', active_errors=[{'error_code':1359188,'message':'billing'}])
            return row
        self.client.get = original_read
        with patch.object(self.owner.ads_manager.objective_wire, 'campaign', side_effect=copy.deepcopy):
            if rejected:
                with self.assertRaises(ProvisioningError):
                    await self.launch()
            else:
                await self.launch()
        for row in self.client.fragments.values():
            row.update(status='EDITING', publish_status=None, active_errors=[])
            if row['ad_object_type'] == 'ad' and rejected:
                row.update(validation_status='HAS_ERRORS', active_errors=[{'error_code':1359188,'message':'billing'}])
        self.client.get = read
        contracts = PrivateLaunchContractStore('{}');contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        self.payload['repair_objective'] = True
        self.before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        self.original_fragments = copy.deepcopy(self.client.fragments)
        self.before_posts = len(self.client.posts)
        self.modify_count = 0
        self.original_post = self.client.post
        self.modify_outcome = 'ok'
        async def modified(path, params, *, before_submit, files):
            self.assertFalse(self.client.read_only)
            self.assertEqual(path, '/555555/addraft_fragments')
            self.assertEqual(params['action'], 'modify')
            self.assertEqual(params['ad_object_type'], 'campaign')
            self.assertEqual(params['ad_object_id'], '777771')
            self.assertEqual({x['field'] for x in params['values']}, {'objective','is_odax_campaign_group'})
            self.assertEqual({x['field']:x['old_value'] for x in params['values']},
                             {'objective':'OUTCOME_TRAFFIC','is_odax_campaign_group':None})
            await before_submit();self.modify_count += 1
            self.client.posts.append((path, copy.deepcopy(params)))
            row = self.client.fragments['111111']
            values = {x['field']:copy.deepcopy(x) for x in row['values']}
            values.update({x['field']:copy.deepcopy(x) for x in params['values']})
            row['values'] = list(values.values())
            row.update(validation_status='VALIDATED', active_errors=[])
            if self.modify_outcome == 'lost':
                raise TimeoutError('lost receipt')
            if self.modify_outcome == 'malformed':
                return {'unrelated':'value'}
            if self.modify_outcome == 'pending':
                row['validation_status'] = 'IN_PROGRESS'
            return {'id':'111111'}
        self.client.post = modified

    async def test_single_modify_keeps_children_and_reports_ad_error_without_success_claim(self):
        await self.retained()
        ad_step = next(x for x in self.before if x['step']=='ADS_MANAGER:FRAGMENT_AD')
        self.assertNotIn('verification', ad_step['result']['evidence'])
        result = await self.launch()
        self.assertTrue(result['campaign_correction_verified'])
        self.assertEqual(result['draft_objects'], {'campaign':'777771','ad_set':'777772','ad':'777773'})
        self.assertEqual(result['mutation_posts'], 1)
        self.assertEqual(result['creation_posts'], 0)
        self.assertFalse(result['all_fragments_validated'])
        self.assertFalse(result['editor_verified'])
        self.assertFalse(result['publication_ready'])
        self.assertFalse(result['published'])
        self.assertFalse(result['payment_checked_live'])
        self.assertEqual(result['fragment_reads'][2]['validation_status'], 'HAS_ERRORS')
        self.assertEqual(result['fragment_reads'][2]['active_error_diagnostic']['codes'],
                         [{'field':'error_code','value':1359188}])
        self.assertEqual(self.client.fragments['222222'],self.original_fragments['222222'])
        self.assertEqual(self.client.fragments['333333'],self.original_fragments['333333'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), self.before)
        self.assertEqual(self.modify_count, 1)
        self.assertEqual(len(self.client.fragments), 3)
        self.assertEqual(self.client.uploads, 1)

    async def test_rerun_and_restart_resume_only_reads_and_preserve_repair_key(self):
        await self.retained()
        first = await self.launch()
        contracts = PrivateLaunchContractStore('{}');contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No replay'))) as post:
            second = await self.launch()
        self.assertEqual(first['repair_key'], second['repair_key'])
        self.assertEqual(second['mutation_posts'], 0)
        self.assertEqual(self.modify_count, 1)
        self.assertEqual(await self.owner.state.all_steps('image-launch'), self.before)
        post.assert_not_called()

    async def test_lost_receipt_cannot_replay_even_if_change_is_visible(self):
        await self.retained();self.modify_outcome = 'lost'
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(self.modify_count,1)
        self.assertEqual(await self.owner.state.all_steps('image-launch'),self.before)

    async def test_malformed_receipt_cannot_replay(self):
        await self.retained();self.modify_outcome = 'malformed'
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(self.modify_count,1)

    async def test_pending_validation_receipt_resumes_get_without_second_modify(self):
        await self.retained();self.modify_outcome = 'pending'
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_VALIDATION_PENDING')
        self.client.fragments['111111']['validation_status'] = 'VALIDATED'
        result = await self.launch()
        self.assertTrue(result['campaign_correction_verified'])
        self.assertEqual(result['mutation_posts'],0)
        self.assertEqual(self.modify_count,1)

    async def test_wrong_actor_scope_or_changed_settings_stop_before_modify(self):
        await self.retained()
        original = copy.deepcopy(self.client.fragments)
        for fragment, changes in [
            ('111111',{'account_id':'999999'}),
            ('111111',{'ad_object_id':'777779'}),
            ('222222',{'parent_ad_object_id':'777779'}),
            ('333333',{'parent_ad_object_id':'777779'}),
            ('333333',{'ad_object_id':'777772'}),
            ('111111',{'status':'PUBLISHED'}),
            ('111111',{'publish_status':'IN_PROGRESS'}),
        ]:
            self.client.fragments = copy.deepcopy(original)
            self.client.fragments[fragment].update(changes)
            with self.subTest(fragment=fragment, changes=changes), self.assertRaises(ProvisioningError):
                await self.launch()
            self.assertEqual(self.modify_count,0)
        self.client.fragments = copy.deepcopy(original)
        row = self.client.fragments['222222'];row['values'].append({'field':'status','new_value':'ACTIVE','old_value':None})
        with self.assertRaises(ProvisioningError): await self.launch()
        self.assertEqual(self.modify_count,0)
        self.client.fragments = copy.deepcopy(original)
        self.context.cookies['c_user'] = '999999'
        with self.assertRaises(ProvisioningError): await self.launch()
        self.assertEqual(self.modify_count,0)

    async def test_wrong_fresh_account_or_draft_ownership_stop_before_modify(self):
        await self.retained()
        read = self.client.get
        for path, patch_values in [('/act_111111', {'business':{'id':'999999'}}),
                                   ('/addraft_555555', {'author_id':'999999'}),
                                   ('/addraft_555555', {'ownership_type':'EMPLOYEE'})]:
            async def changed(actual, params):
                row = await read(actual,params)
                if actual == path:row.update(patch_values)
                return row
            self.client.get = changed
            with self.subTest(path=path, patch=patch_values), self.assertRaises(ProvisioningError):
                await self.launch()
            self.assertEqual(self.modify_count,0)
        self.client.get = read

    async def test_explicit_mode_and_original_launch_identity_are_required(self):
        await self.retained()
        original = copy.deepcopy(self.payload)
        for change in ({'repair_objective':False}, {'reconcile_only':True}, {'page_id':'999999'}, {'ad_account_id':'999999'}):
            self.payload = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ProvisioningError): await self.launch()
            self.assertEqual(self.modify_count,0)
        self.payload = copy.deepcopy(original)
        self.payload['launch']['adset']['targeting']['age_min'] = 26
        with self.assertRaises(ProvisioningError): await self.launch()
        self.assertEqual(self.modify_count,0)

    async def test_malformed_or_mixed_original_journal_cannot_open_meta_or_modify(self):
        await self.retained()
        with self.owner.state._connect() as con:
            original = con.execute("SELECT result_json FROM private_launch_steps WHERE launch_key=? AND step=?",
                ('image-launch','ADS_MANAGER:FRAGMENT_AD')).fetchone()['result_json']
        for field, value in [('source_digest','f' * 64), ('actor_id','999999'),
                             ('launch_fingerprint','f' * 64), ('receipt',{})]:
            document = json.loads(original);document['evidence'][field] = value
            with self.owner.state._connect() as con:
                con.execute("UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?",
                    (json.dumps(document),'image-launch','ADS_MANAGER:FRAGMENT_AD'))
            with self.subTest(field=field), patch('app.private_ads_objective_repair.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
                with self.assertRaises(ProvisioningError):await self.launch()
                opened.assert_not_called()
            self.assertEqual(self.modify_count,0)
        with self.owner.state._connect() as con:
            con.execute("UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?",
                (original,'image-launch','ADS_MANAGER:FRAGMENT_AD'))

    async def test_code_revision_drift_cannot_reissue_an_accepted_repair(self):
        await self.retained();await self.launch()
        self.owner.ads_manager.source_digest = 'a' * 64
        with patch('app.private_ads_objective_repair.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
            with self.assertRaises(ProvisioningError) as caught:await self.launch()
            self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
            opened.assert_not_called()
        self.assertEqual(self.modify_count,1)

    async def test_unreviewed_older_sender_cannot_enter_repair(self):
        await self.retained(source='f9f8e21841da2cf174bc516941a5581fe1c5b65642783ab84ed2b03ede68c218')
        with self.assertRaises(ProvisioningError) as caught:await self.launch()
        self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_REPAIR_SOURCE_UNREVIEWED')
        self.assertEqual(self.modify_count,0)
