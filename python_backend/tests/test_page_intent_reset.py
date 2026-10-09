import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.provisioning.page_intent_reset import MANIFEST, PHASE, apply_page_intent_resets
from app.provisioning.models import ProvisioningStep
from app.provisioning.state import ProvisioningStateStore


class PageIntentResetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / 'state.sqlite'))
        await self.state.init()
        self.repair = json.loads(MANIFEST.read_text())['repairs'][0]
        self.item = self.repair['item_id']
        self.value = {'phase': 'PAGE_CREATE_RESULT_UNKNOWN', 'resume_from': 'RECONCILE_CREATE',
            'business_id': self.repair['business_id'], 'create_actor_id': self.repair['actor_id'],
            'target_names': [self.repair['page_name']], 'active_page_name': self.repair['page_name'],
            'active_before_ids': ['1348798761652037'], 'transport': 'business_suite_page_static_http2',
            'response_page_id': '', 'last_error_code': 'FAN_PAGE_CREATE_RESULT_UNKNOWN'}

    async def seed(self, *, item=None, value=None, profile='15', status='FAILED'):
        item = item or self.item
        await self.state.set_running(item, profile, 'workspace-business-page:' + self.repair['business_id'], ProvisioningStep.FAN_PAGES)
        with self.state._connect() as con:
            con.execute('UPDATE provisioning_steps SET result_json=?,status=?,error_code=? WHERE item_id=? AND step=?',
                (json.dumps(value or self.value), status, 'FAN_PAGE_CREATE_RESULT_UNKNOWN', item, 'FAN_PAGES'))

    async def result(self, item=None):
        return (await self.state.step(item or self.item, ProvisioningStep.FAN_PAGES))['result']

    async def test_reset_archives_original_and_does_not_resurrect_matching_history(self):
        await self.seed()
        await self.seed(item='older-copy')
        await self.seed(item='other-bm', value={**self.value, 'business_id': '1428816905866955'})
        await self.seed(item='other-profile', profile='16')
        before = await self.state.step(self.item, ProvisioningStep.FAN_PAGES)
        outcomes = await apply_page_intent_resets(self.state)
        self.assertEqual(outcomes[0]['outcome'], 'APPLIED')
        self.assertEqual(outcomes[0]['reset_count'], 2)
        self.assertEqual((await self.result())['phase'], PHASE)
        self.assertEqual((await self.result())['reset_authorization']['original_result'], 'UNKNOWN')
        self.assertEqual(await self.state.latest_uncertain_fan_page('15', 'PrgssTeam', exclude_item_id=self.item,
            business_id=self.repair['business_id']), {})
        self.assertEqual((await self.result('other-bm'))['phase'], 'PAGE_CREATE_RESULT_UNKNOWN')
        self.assertEqual((await self.result('other-profile'))['phase'], 'PAGE_CREATE_RESULT_UNKNOWN')
        with self.state._connect() as con:
            archive = json.loads(con.execute('SELECT archived_steps_json FROM provisioning_page_intent_resets').fetchone()[0])
        old = next(row for row in archive if row['item_id'] == self.item)
        self.assertEqual(json.loads(old['result_json']), before['result'])
        self.assertEqual(old['error_code'], 'FAN_PAGE_CREATE_RESULT_UNKNOWN')

    async def test_restart_normalized_nested_running_step_can_be_retired(self):
        await self.seed(status='RUNNING')
        await self.state.init()
        self.assertEqual((await self.state.step(self.item, ProvisioningStep.FAN_PAGES))['status'], 'QUEUED')
        self.assertEqual((await apply_page_intent_resets(self.state))[0]['outcome'], 'APPLIED')

    async def test_new_attempt_is_never_reset_on_subsequent_startup(self):
        await self.seed()
        await apply_page_intent_resets(self.state)
        new = {**self.value, 'create_attempt_id': 'new-journalled-submit'}
        await self.seed(value=new, status='RUNNING')
        await self.state.init()
        self.assertEqual((await apply_page_intent_resets(self.state))[0]['outcome'], 'ALREADY_APPLIED')
        self.assertEqual(await self.result(), new)

    async def test_known_ids_success_new_journal_actor_and_active_run_are_protected(self):
        for changes, status in (({'response_page_id': '2348798761652037'}, 'FAILED'),
                ({'additional_profile_id': '61500012345678'}, 'FAILED'),
                ({'created_pages': [{'id': '2348798761652037', 'name': 'PrgssTeam'}]}, 'FAILED'),
                ({'create_response': {'page_id': '2348798761652037'}}, 'FAILED'),
                ({'create_attempt_id': 'fresh-submit'}, 'FAILED'),
                ({'create_actor_id': '61500000000000'}, 'FAILED'),
                ({'page_business_attached': True}, 'FAILED'), ({}, 'SUCCESS'), ({}, 'RUNNING')):
            with self.subTest(changes=changes, status=status):
                value = {**self.value, **changes}
                await self.seed(value=value, status=status)
                self.assertEqual((await apply_page_intent_resets(self.state))[0]['outcome'], 'TARGET_CHANGED')
                self.assertEqual(await self.result(), value)

    async def test_bound_page_prevents_reset_and_confirmed_bm_rk_payment_are_preserved(self):
        from types import SimpleNamespace
        from app.provisioning.business_pages import BusinessPageStore
        await self.seed()
        context = SimpleNamespace(profile_id='15', cookies={'c_user': self.repair['actor_id']})
        store = BusinessPageStore(self.state, context, self.repair['business_id'])
        await store.patch(page_id='2348798761652037')
        for step, data in ((ProvisioningStep.BUSINESS, {'business_id': self.repair['business_id']}),
                (ProvisioningStep.AD_ACCOUNT, {'business_id': self.repair['business_id'], 'ad_account_id': '120251650486340295'})):
            await self.state.complete('confirmed-' + step.value, '15', 'bundle', step, data)
        await self.state.set_payment_link_state('15', '120251650486340295', True, source='fixture')
        self.assertEqual((await apply_page_intent_resets(self.state))[0]['outcome'], 'PAGE_ALREADY_BOUND')
        self.assertEqual((await self.state.snapshot('15', 'bundle')).ad_account_id, '120251650486340295')
        self.assertEqual((await store.get())['page_id'], '2348798761652037')
        self.assertEqual((await self.result())['phase'], 'PAGE_CREATE_RESULT_UNKNOWN')

    async def test_archive_and_checkpoint_are_one_transaction(self):
        await self.seed()
        with self.state._connect() as con:
            con.execute("CREATE TRIGGER fail_reset BEFORE UPDATE ON provisioning_steps BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            await apply_page_intent_resets(self.state)
        self.assertEqual(await self.result(), self.value)
        with self.state._connect() as con:
            table = con.execute("SELECT 1 FROM sqlite_master WHERE name='provisioning_page_intent_resets'").fetchone()
            if table:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM provisioning_page_intent_resets').fetchone()[0], 0)
