"""Offline service/SQLite integration; only the Meta boundary is simulated."""
import asyncio
import copy
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app.private_launch import PrivateLaunchStateStore
from app.private_launch_extensions import OperationKey
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as launch_fixtures


class DeliveryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = launch_fixtures.LaunchTests.asyncSetUp
    launch = launch_fixtures.LaunchTests.launch

    async def prepare_delivery(self, *, create_source=True):
        # Establish a publication through the actual Launch service, uploader,
        # fragment publisher, independent object verifier and SQLite journal.
        if create_source:
            await self.launch()
        self.changes = {}
        self.publications = {}
        self.statuses = {}
        self.delivery_posts = []
        self.validation = 'VALIDATED'
        self.errors = []
        self.consumed = False
        self.pending = False
        self.lose_publication_reply = False
        original_get, original_post = self.client.get, self.client.post

        async def post(path, params, *, before_submit, files):
            if params.get('action') == 'modify':
                await before_submit()
                self.delivery_posts.append((path, copy.deepcopy(params)))
                fragment = str(880001 + len(self.changes))
                self.changes[fragment] = {'id': fragment, 'account_id': '111111',
                    'ad_draft_id': '555555', 'ad_object_id': params['ad_object_id'],
                    'ad_object_type': params['ad_object_type'], 'values': copy.deepcopy(params['values'])}
                return {'id': fragment}
            if path.endswith('/publish') and params['fragments'][0] in self.changes:
                await before_submit()
                self.delivery_posts.append((path, copy.deepcopy(params)))
                operation = str(990001 + len(self.publications))
                self.publications[operation] = params['fragments'][0]
                if self.lose_publication_reply:
                    raise TimeoutError('reply lost after acceptance')
                return {'success': True, 'async_request_set_id': operation}
            return await original_post(path, params, before_submit=before_submit, files=files)

        async def get(path, params):
            fragment = path.removeprefix('/addraftfragment_')
            if fragment in self.changes:
                if self.consumed:
                    raise AssertionError('Accepted publication must not reread a consumed fragment')
                return {**copy.deepcopy(self.changes[fragment]),
                    'validation_status': self.validation, 'active_errors': copy.deepcopy(self.errors)}
            operation = (params.get('async_request_set_ids') or [''])[0]
            if operation in self.publications:
                fragment = self.publications[operation]
                if not self.pending:
                    change = self.changes[fragment]
                    self.statuses[change['ad_object_id']] = change['values'][0]['new_value']
                return {'data': [{'async_request_set_id': operation,
                    'status': 'IN_PROGRESS' if self.pending else 'SUCCEEDED',
                    'total_count': 1, 'success_count': 1, 'error_count': 0,
                    'canceled_count': 0, 'in_progress_count': 0, 'initial_count': 0,
                    'fragment_statuses': [{'fragment_id': fragment, 'status': 'SUCCESS'}]}]}
            row = await original_get(path, params)
            if path[1:] in self.statuses:
                row.update(status=self.statuses[path[1:]], effective_status=self.statuses[path[1:]])
            return row

        self.client.get, self.client.post = get, post
        self.delivery_payload = {'source_launch_key': 'image-launch', 'delivery_key': 'delivery-request',
            'status': 'ACTIVE', 'business_id': '222222', 'ad_account_id': '111111', 'page_id': '555555',
            'launch': copy.deepcopy(self.payload['launch'])}

    async def deliver(self):
        return await self.owner.run_delivery(item_id='delivery-fixture', profile_id='19', context=self.context,
            session=self.session, payload=self.delivery_payload)

    async def reopen(self):
        state = PrivateLaunchStateStore(Path(self.temp.name) / 'state.sqlite')
        await state.init()
        self.owner.state = state

    async def test_rejected_status_fragment_never_publishes_or_advances(self):
        await self.prepare_delivery()
        for status, errors in [('HAS_ERRORS', []), ('VALIDATED', [{'error_code': 12345}])]:
            self.validation, self.errors = status, errors
            with self.subTest(status=status), self.assertRaises(ProvisioningError) as caught:
                await self.deliver()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
            self.assertEqual(len(self.delivery_posts), 1)
            self.assertFalse(self.publications)
            row = await self.owner.state.step('delivery-request', OperationKey('ADS_MANAGER:DELIVERY_AD_FRAGMENT'))
            self.assertNotEqual(row['status'], 'SUCCESS')

    async def test_explicit_active_launch_publishes_paused_then_changes_children_first(self):
        await self.prepare_delivery(create_source=False)
        self.payload['launch']['delivery'] = dict.fromkeys(('campaign', 'adset', 'ad'), 'ACTIVE')
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(result['delivery'], self.payload['launch']['delivery'])
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(len(self.delivery_posts), 6)
        self.assertEqual([params['ad_object_type'] for _, params in self.delivery_posts if params.get('action')],
            ['ad', 'ad_set', 'campaign'])
        for fragment in self.client.fragments.values():
            self.assertEqual(next(v['new_value'] for v in fragment['values'] if v['field'] == 'status'), 'PAUSED')
        self.assertFalse(result['delivery_running_verified'])
        self.owner.state = PrivateLaunchStateStore(Path(self.temp.name) / 'state.sqlite')
        again = await self.launch()
        self.assertEqual(again['delivery'], result['delivery'])
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(len(self.delivery_posts), 6)

    async def test_pending_validation_resumes_exact_fragment_after_restart(self):
        await self.prepare_delivery()
        for pending in ('IN_PROGRESS', None, True, 'UNKNOWN'):
            self.validation = pending
            with self.subTest(validation=pending), self.assertRaises(ProvisioningError) as caught:
                await self.deliver()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_PENDING')
            self.assertTrue(caught.exception.retryable)
            self.assertEqual(len(self.delivery_posts), 1)
            await self.reopen()
        self.validation = 'VALIDATED'
        result = await self.deliver()
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.delivery_posts), 6)
        self.assertEqual(result['delivery'], dict.fromkeys(('campaign', 'adset', 'ad'), 'ACTIVE'))
        await self.deliver()
        self.assertEqual(len(self.delivery_posts), 6)

    async def test_accepted_status_publication_recovers_without_consumed_fragment(self):
        await self.prepare_delivery()
        self.pending = True
        with patch('app.private_ads_delivery.asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(ProvisioningError) as caught:
                await self.deliver()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_PUBLISH_PENDING')
        self.assertEqual(len(self.delivery_posts), 2)
        await self.reopen()
        self.pending, self.consumed = False, True
        # Only the first fragment is consumed; later new fragments remain readable.
        get = self.client.get
        async def consumed(path, params):
            self.consumed = path == '/addraftfragment_880001'
            return await get(path, params)
        self.client.get = consumed
        result = await self.deliver()
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.delivery_posts), 6)
        self.assertEqual(len(self.changes), 3)
        self.assertEqual(len(self.publications), 3)

    async def test_lost_publication_reply_never_replays_or_advances_after_restart(self):
        await self.prepare_delivery()
        self.lose_publication_reply = True
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.deliver()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
            await self.reopen()
        self.assertEqual(len(self.delivery_posts), 2)
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(len(self.publications), 1)

    async def test_payment_denial_prevents_status_posts(self):
        await self.prepare_delivery()
        self.funding['payment_methods'][0]['needs_verification'] = True
        with self.assertRaises(ProvisioningError):
            await self.deliver()
        self.assertEqual(self.delivery_posts, [])

    async def test_status_only_delivery_uses_full_publication_preflight(self):
        await self.prepare_delivery()
        self.delivery_payload.pop('launch')
        result = await self.deliver()
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.delivery_posts), 6)

    async def test_draft_label_cannot_bypass_payment_gate_for_activation(self):
        await self.prepare_delivery()
        self.delivery_payload['launch']['execution_mode'] = 'DRAFT'
        self.funding['payment_methods'][0]['needs_verification'] = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.deliver()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_CONFIRMATION_REQUIRED')
        self.assertEqual(self.delivery_posts, [])

    async def test_consumed_fragment_recovery_still_requires_independent_creative_match(self):
        await self.prepare_delivery()
        self.pending = True
        with patch('app.private_ads_delivery.asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(ProvisioningError):
                await self.deliver()
        await self.reopen()
        self.pending = False
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777774':
                row['object_story_spec']['link_data']['link'] = 'https://wrong.example/'
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.deliver()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_DELIVERY_SPEC_MISMATCH')
        self.assertEqual(len(self.delivery_posts), 2)

    async def test_orphan_publication_cannot_be_skipped_as_already_active(self):
        await self.prepare_delivery()
        self.pending = True
        with patch('app.private_ads_delivery.asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(ProvisioningError):
                await self.deliver()
        self.statuses['777773'] = 'ACTIVE'
        with self.owner.state._connect() as con:
            con.execute('DELETE FROM private_launch_steps WHERE launch_key=? AND step=?',
                ('delivery-request', 'ADS_MANAGER:DELIVERY_AD_FRAGMENT'))
        await self.reopen()
        with self.assertRaises(ProvisioningError) as caught:
            await self.deliver()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.delivery_posts), 2)


class AdPublicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = launch_fixtures.LaunchTests.asyncSetUp
    launch = launch_fixtures.LaunchTests.launch

    async def test_rejected_inline_ad_resumes_after_validation_without_recreating_parents(self):
        original = self.client.get
        rejected = True
        async def get(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad' and rejected:
                row.update(validation_status='HAS_ERRORS', active_errors=[{'error_code': 12345}])
            return row
        self.client.get = get
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
            self.assertEqual(len(self.client.posts), 4)
            self.assertEqual(self.client.uploads, 1)
            self.owner.state = PrivateLaunchStateStore(Path(self.temp.name) / 'state.sqlite')
        rejected = False
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(result['entities']['creative_id'], '777774')
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(len(self.client.fragments), 3)

    async def test_creative_mismatch_after_publication_recovers_by_reads_only(self):
        original = self.client.get
        wrong = True
        async def get(path, params):
            row = await original(path, params)
            if path == '/777774' and wrong:
                row['object_story_spec']['link_data']['message'] = 'unexpected content'
            return row
        self.client.get = get
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_CREATIVE_SPEC_MISMATCH')
        publication = await self.owner.state.step('image-launch', OperationKey('ADS_MANAGER:PUBLISH'))
        self.assertNotEqual(publication['status'], 'SUCCESS')
        self.owner.state = PrivateLaunchStateStore(Path(self.temp.name) / 'state.sqlite')
        wrong = False
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)

    async def test_unknown_ad_receipt_blocks_recreation_after_restart(self):
        original = self.client.post
        async def post(path, params, **options):
            result = await original(path, params, **options)
            if params.get('ad_object_type') == 'ad':
                raise TimeoutError('Ad accepted but receipt lost')
            return result
        self.client.post = post
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
            self.owner.state = PrivateLaunchStateStore(Path(self.temp.name) / 'state.sqlite')
        self.assertEqual(len(self.client.posts), 4)
        self.assertEqual(len(self.client.fragments), 3)
        self.assertEqual(self.client.uploads, 1)

    async def test_real_worker_queue_keeps_other_profile_running_after_rejected_ad(self):
        from app import runner
        from app.models import CreateJobRequest
        from app.runner import WorkerPool
        from app.store import JobStore

        store = JobStore(Path(self.temp.name) / 'state.sqlite')
        await store.init()
        pool = WorkerPool(store, concurrency=2)
        pool.private_launch = self.owner
        pool._restore_workspace_bindings = AsyncMock()
        clients = {'19': self.client, '20': launch_fixtures.ImageClient()}
        clients['20'].context.actor_id = '123457'
        contexts = {'19': self.context, '20': SimpleNamespace(
            cookies={'c_user': '123457'}, ad_accounts=copy.deepcopy(self.context.ad_accounts))}
        pool.resolver.resolve = AsyncMock(side_effect=lambda profile: contexts[profile])
        entered, concurrent = asyncio.Event(), asyncio.Event()
        original_bad, original_good = clients['19'].get, clients['20'].get

        async def bad(path, params):
            row = await original_bad(path, params)
            if row.get('ad_object_type') == 'ad':
                entered.set()
                await asyncio.wait_for(concurrent.wait(), 3)
                row.update(validation_status='HAS_ERRORS', active_errors=[{'error_code': 12345}])
            return row

        async def good(path, params):
            row = await original_good(path, params)
            if path == '/addraft_555555':
                row['author_id'] = '123457'
            if row.get('ad_object_type') == 'ad':
                await asyncio.wait_for(entered.wait(), 3)
                concurrent.set()
            return row

        clients['19'].get, clients['20'].get = bad, good
        boundary_web = object()
        class Session:
            def __init__(inner, context):
                inner.context = context
            async def __aenter__(inner):
                return inner
            async def __aexit__(inner, *args):
                pass
            async def facebook_web(inner):
                return boundary_web

        payloads = {profile: {**copy.deepcopy(self.payload), 'launch_key': 'worker-' + profile}
                    for profile in clients}
        for payload in payloads.values():
            payload['launch']['execution_mode'] = 'DRAFT'
        job, _ = await store.create_job(CreateJobRequest(profiles=[
            {'profile_id': profile, 'tasks': [{'action': 'private_launch', 'payload': payload}]}
            for profile, payload in payloads.items()]))
        async def open_client(web, *, actor_id, **kwargs):
            return clients['19' if actor_id == '123456' else '20']
        with patch.object(runner, 'ProfileSession', Session), patch(
                'app.private_ads_launch.AdsManagerPrivateHttp.open', side_effect=open_client):
            await pool.start()
            try:
                await pool.enqueue_job(job)
                async def completed():
                    while True:
                        view = await store.job_view(job)
                        if view['status'] in {'SUCCESS', 'FAILED', 'PARTIAL'}:
                            return view
                        await asyncio.sleep(0.01)
                view = await asyncio.wait_for(completed(), 5)
            finally:
                await pool.stop()
        self.assertTrue(concurrent.is_set())
        self.assertEqual(view['status'], 'PARTIAL')
        items = {item['profile_id']: item for item in view['items']}
        self.assertEqual(items['19']['status'], 'FAILED')
        self.assertEqual(items['19']['tasks'][0]['error_code'], 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        self.assertEqual(items['20']['status'], 'SUCCESS')
        self.assertTrue(items['20']['tasks'][0]['result']['draft_verified'])
        self.assertFalse(items['20']['tasks'][0]['result']['published'])
        for profile, client in clients.items():
            self.assertEqual(len(client.posts), 4)
            self.assertEqual(client.uploads, 1)
            records = await self.owner.state.all_steps('worker-' + profile)
            self.assertTrue(all(row['profile_id'] == profile for row in records))
            self.assertTrue(all(row['result']['evidence']['actor_id'] == contexts[profile].cookies['c_user'] for row in records))
