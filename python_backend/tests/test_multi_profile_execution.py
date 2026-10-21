"""Real SQLite scheduling with deterministic local faults, never Meta traffic.

Counts describe distinct authenticated profiles, not theoretical worker capacity.
Events establish ordering; elapsed fixture time is not a proxy/live benchmark.
"""
import asyncio
from collections import Counter
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import runner
from app.models import CreateJobRequest
from app.private_ads_state import AdsManagerSubmitter
from app.private_launch import PrivateLaunchStateStore
from app.private_launch_extensions import OperationKey
from app.provisioning.models import ProvisioningError
from app.runner import WorkerPool, _await_with_hard_watchdog
from app.store import JobStore


class FixtureSession:
    def __init__(self, context):
        self.context = context

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class MultiProfileExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = JobStore(Path(self.directory.name) / 'jobs.sqlite')
        await self.store.init()

    async def pool(self, concurrency, handler):
        pool = WorkerPool(self.store, concurrency=concurrency)
        await pool.provisioning_state.init()
        # Avoid the unrelated PHP mirror. The real queue, locks, task statuses
        # and durable store still run through WorkerPool._worker/_run_item.
        pool._restore_workspace_bindings = AsyncMock()
        pool.resolver.resolve = AsyncMock(side_effect=lambda profile: SimpleNamespace(
            profile_id=profile, proxy='fixture-proxy-' + profile,
            cookies={'c_user': 'fixture-actor-' + profile}))
        pool.registry.register('fixture', handler)
        return pool

    async def create(self, profiles, key):
        request = CreateJobRequest(idempotency_key=key, profiles=profiles)
        return await self.store.create_job(request)

    @staticmethod
    def start(pool):
        pool._workers = [asyncio.create_task(pool._worker(index))
                         for index in range(pool.concurrency)]

    async def terminal(self, job):
        async def wait():
            while True:
                view = await self.store.job_view(job)
                if view['status'] not in {'QUEUED', 'RUNNING'}:
                    return view
                await asyncio.sleep(.005)
        return await asyncio.wait_for(wait(), 8)

    async def test_distinct_profiles_overlap_but_accounts_of_one_profile_serialize(self):
        for count in (1, 3, 5, 10, 30):
            with self.subTest(profiles=count):
                reached, release = asyncio.Event(), asyncio.Event()
                active, calls, entered = Counter(), [], set()

                async def handler(session, payload):
                    profile = session.context.profile_id
                    self.assertEqual(session.context.proxy, 'fixture-proxy-' + profile)
                    self.assertEqual(session.context.cookies['c_user'], 'fixture-actor-' + profile)
                    active[profile] += 1
                    try:
                        self.assertEqual(active[profile], 1, 'same profile session overlapped')
                        calls.append((profile, payload['account']))
                        if payload['account'] == 'first':
                            entered.add(profile)
                            if len(entered) == count:
                                reached.set()
                            await release.wait()
                        return {'profile_id': profile, 'account': payload['account']}
                    finally:
                        active[profile] -= 1

                pool = await self.pool(count, handler)
                profiles = [{'profile_id': f'p{count}-{index}', 'tasks': [
                    {'action': 'fixture', 'payload': {'account': account}}]}
                    for index in range(count) for account in ('first', 'second')]
                job, created = await self.create(profiles, f'overlap-{count}')
                self.assertTrue(created)
                # Identical HTTP submission and repeated queue notifications
                # cannot create extra items or execute a mutation twice.
                self.assertEqual(await self.create(profiles, f'overlap-{count}'), (job, False))
                with patch.object(runner, 'ProfileSession', FixtureSession):
                    await pool.enqueue_job(job)
                    await pool.enqueue_job(job)
                    self.start(pool)
                    try:
                        await asyncio.wait_for(reached.wait(), 8)
                        self.assertEqual(len(calls), count)
                        self.assertEqual(len(pool.queue.active), count)
                        self.assertEqual(len(set(pool.queue.active.values())), count)
                        release.set()
                        view = await self.terminal(job)
                        self.assertEqual(view['status'], 'SUCCESS')
                        self.assertEqual(len(view['items']), count * 2)
                        self.assertEqual(len(calls), count * 2)
                        self.assertEqual(len(set(calls)), count * 2)
                        self.assertTrue(all(item['attempt'] == 1 for item in view['items']))
                    finally:
                        release.set()
                        await pool.stop()

    async def test_timeout_rate_limit_expired_session_and_permission_failures_are_isolated(self):
        faults = ('timeout', 'rate_limit', 'expired_session', 'permission')
        for count in (1, 3, 5, 10, 30):
            for fault in faults:
                with self.subTest(profiles=count, fault=fault):
                    entered, release = asyncio.Event(), asyncio.Event()
                    calls = Counter()

                    async def handler(session, payload):
                        profile = session.context.profile_id
                        calls[profile] += 1
                        if payload['fault']:
                            entered.set()
                            await release.wait()
                            if fault == 'timeout':
                                await _await_with_hard_watchdog(asyncio.Event().wait(),
                                    timeout_seconds=.01, code='PRIVATE_HTTP_TIMEOUT', message='fixture timeout')
                            raise ProvisioningError({
                                'rate_limit': 'META_RATE_LIMITED',
                                'expired_session': 'SESSION_EXPIRED',
                                'permission': 'META_PERMISSION_DENIED',
                            }[fault], 'fixture fault', retryable=fault == 'rate_limit')
                        return {'ok': True, 'profile_id': profile}

                    pool = await self.pool(count, handler)
                    profiles = [{'profile_id': f'f{count}-{fault}-{index}', 'tasks': [
                        {'action': 'fixture', 'payload': {'fault': index == 0}},
                        {'action': 'fixture', 'payload': {'fault': False}}]}
                        for index in range(count)]
                    job, _ = await self.create(profiles, f'fault-{count}-{fault}')
                    with patch.object(runner, 'ProfileSession', FixtureSession):
                        self.start(pool)
                        try:
                            await asyncio.wait_for(entered.wait(), 8)
                            # Wait for every unaffected profile while the
                            # defective one is deliberately still blocked.
                            async def peers_done():
                                while True:
                                    view = await self.store.job_view(job)
                                    peers = [item for item in view['items'] if not item['profile_id'].endswith('-0')]
                                    if all(item['status'] == 'SUCCESS' for item in peers):
                                        return
                                    await asyncio.sleep(.005)
                            await asyncio.wait_for(peers_done(), 8)
                            release.set()
                            view = await self.terminal(job)
                            failed = [item for item in view['items'] if item['status'] == 'FAILED']
                            self.assertEqual(len(failed), 1)
                            self.assertEqual(view['status'], 'FAILED' if count == 1 else 'PARTIAL')
                            self.assertEqual(calls[failed[0]['profile_id']], 1)
                            self.assertEqual(failed[0]['tasks'][1]['status'], 'QUEUED')
                            self.assertEqual(sum(calls.values()), (count - 1) * 2 + 1)
                            self.assertEqual(failed[0]['error_code'], {
                                'timeout': 'PRIVATE_HTTP_TIMEOUT', 'rate_limit': 'META_RATE_LIMITED',
                                'expired_session': 'SESSION_EXPIRED', 'permission': 'META_PERMISSION_DENIED'}[fault])
                            self.assertEqual(bool(failed[0]['retryable']), fault in {'timeout', 'rate_limit'})
                        finally:
                            release.set()
                            await pool.stop()

    async def test_restart_preserves_uncertain_ad_intent_and_never_replays_post(self):
        state = PrivateLaunchStateStore(self.store.path)
        await state.init()
        dispatched, release = asyncio.Event(), asyncio.Event()
        posts = []

        class LostReplyClient:
            async def post(self, path, params, *, before_submit, files):
                await before_submit()
                posts.append(path)
                dispatched.set()
                await release.wait()
                raise AssertionError('fixture interrupted before response')

        async def handler(session, payload):
            if session.context.profile_id == 'healthy':
                return {'ok': True}
            sender = AdsManagerSubmitter(state, launch_key='restart-launch', profile_id='uncertain',
                business_id='222222', account_id='111111', actor_id='123456',
                launch_fingerprint='fixture-fingerprint', source_digest='fixture-source')
            return await sender.submit(LostReplyClient(), operation='FRAGMENT_AD',
                path='/555555/addraft_fragments', params={'values': []},
                receipt=lambda row: {'fragment_id': row['id']})

        job, _ = await self.create([{'profile_id': profile, 'tasks': [{'action': 'fixture'}]}
                                    for profile in ('uncertain', 'healthy')], 'restart-job')
        first = await self.pool(2, handler)
        with patch.object(runner, 'ProfileSession', FixtureSession):
            self.start(first)
            try:
                await asyncio.wait_for(dispatched.wait(), 8)
            finally:
                await first.stop()
            row = await state.step('restart-launch', OperationKey('ADS_MANAGER:FRAGMENT_AD'))
            self.assertEqual(row['status'], 'SUBMITTING')
            self.assertEqual(row['result']['evidence']['receipt'], {})
            self.assertEqual(posts, ['/555555/addraft_fragments'])
            # A new store/pool approximates restart using the actual durable
            # state. Job recovery may re-run a handler; the sender must refuse
            # a second POST when the first outcome has no receipt.
            self.store = JobStore(self.store.path)
            await self.store.init()
            await self.store.recover()
            state = PrivateLaunchStateStore(self.store.path)
            second = await self.pool(2, handler)
            self.start(second)
            try:
                view = await self.terminal(job)
                outcomes = {item['profile_id']: item for item in view['items']}
                self.assertEqual(outcomes['healthy']['status'], 'SUCCESS')
                self.assertEqual(outcomes['uncertain']['status'], 'FAILED')
                self.assertEqual(outcomes['uncertain']['error_code'], 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
                self.assertEqual(posts, ['/555555/addraft_fragments'])
                after = await state.step('restart-launch', OperationKey('ADS_MANAGER:FRAGMENT_AD'))
                self.assertEqual(after, row)
            finally:
                await second.stop()

    async def test_interrupted_sqlite_job_insert_rolls_back_all_partial_profiles(self):
        # Fail after the first profile/task has been inserted but before the
        # transaction commits. No orphan queued task may reach a worker.
        profiles = [{'profile_id': profile, 'tasks': [{'action': 'fixture'}]}
                    for profile in ('first', 'second')]
        with patch('app.store.uuid.uuid4', return_value=SimpleNamespace(hex='collision')):
            with self.assertRaises(sqlite3.IntegrityError):
                await self.create(profiles, 'interrupted-insert')
        with self.store._connect() as connection:
            for table in ('jobs', 'job_items', 'job_tasks'):
                self.assertEqual(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(await self.store.queue_count(), 0)
        # The same logical request remains usable; a failed transaction must
        # not reserve its idempotency key or poison admission for healthy work.
        job, created = await self.create(profiles, 'interrupted-insert')
        self.assertTrue(created)
        self.assertEqual(len(await self.store.queued_item_ids(job)), 2)


if __name__ == '__main__':
    unittest.main()
