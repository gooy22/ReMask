"""Bounded profile-scoped CDN evidence reuse, with real fixture byte counts."""
import asyncio
import copy
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.contract_maintenance import module_refresh as refresh
from app.contract_maintenance import module_source_cache as cache
from app.provisioning.models import ProvisioningError

ROOT = Path(__file__).resolve().parents[1]


class ModuleSourceCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'REMASK_DATA_DIR':folder.name});env.start();self.addCleanup(env.stop)
        refresh._cache.clear()
        cache._cache.clear()
        self.a = 'https://static.xx.fbcdn.net/a.js'
        self.b = 'https://static.xx.fbcdn.net/b.js'
        self.source = '__d("FixtureModule",[],function(){return {};});'
        self.fixture = (ROOT/'tests/fixtures/meta_create_rk_observed_20261008.js').read_text()
        self.web = self.peer()
        for name in ('refresh_registry','refresh_reference','refresh_editor'):
            context = patch.object(refresh,name)
            setattr(self,name,context.start())
            self.addCleanup(context.stop)

    def peer(self):
        return SimpleNamespace(profile=SimpleNamespace(name='fixture', proxy='http://proxy.test:8080',
            cookies={'c_user':'111111111'}), fetch_text=AsyncMock())

    async def collect(self, web=None, *, urls=None, page='/latest/settings', now=None):
        web = web or self.web
        document = ''.join('<script src="'+url+'"></script>' for url in (urls or [self.a]))
        if now is None:
            return await refresh.refresh_from_document(web,document,'https://business.facebook.com'+page)
        with patch('time.monotonic',return_value=now), patch('time.time',return_value=1_700_000_000+now):
            return await refresh.refresh_from_document(web,document,'https://business.facebook.com'+page)

    async def test_overlap_between_pages_and_superset_downloads_each_fixture_url_once(self):
        downloaded = []
        async def fetch(url,**kwargs):
            downloaded.append(len(self.fixture.encode()))
            return 200,self.fixture,url
        self.web.fetch_text.side_effect = fetch
        await self.collect()
        await self.collect(page='/adsmanager/manage')
        await self.collect(urls=[self.a,self.b])
        # Baseline on fed13c7: A three times + B once = 4*19,520.
        self.assertEqual(self.web.fetch_text.await_count,2)
        self.assertEqual(sum(downloaded),39_040)
        self.assertEqual(len(self.fixture.encode()),19_520)
        self.assertEqual(self.web._module_collection_progress['collected_scripts'],2)

    async def test_profile_actor_and_proxy_each_isolate_the_source_cache(self):
        self.web.fetch_text.side_effect = lambda url,**kwargs:(200,self.source,url)
        await self.collect()
        for field,value in (('name','other'),('actor','222222222'),('proxy','http://other:8080')):
            peer = self.peer()
            if field == 'actor':peer.profile.cookies['c_user']=value
            else:setattr(peer.profile,field,value)
            peer.fetch_text.side_effect = lambda url,**kwargs:(200,self.source,url)
            await self.collect(peer,page='/different')
            peer.fetch_text.assert_awaited_once()

    async def test_reuse_keeps_original_proof_and_metadata_deadline_and_expiry_reads_again(self):
        self.web.fetch_text.side_effect = lambda url,**kwargs:(200,self.fixture,url)
        name='BizKitSettingsCreateAdAccountMutation'
        await self.collect(now=1000)
        await self.collect(now=1061,page='/different')
        self.assertEqual(self.web.fetch_text.await_count,1)
        self.assertEqual(self.web._reviewed_module_ids['accepted'][name]['until'],1000+refresh.TTL)
        self.assertEqual(self.refresh_editor.call_args.kwargs['observed_at'],1_700_001_000)
        await self.collect(now=2801,page='/third')
        self.assertEqual(self.web.fetch_text.await_count,2)
        self.assertEqual(self.web._reviewed_module_ids['accepted'][name]['until'],2801+refresh.TTL)

    async def test_concurrent_different_windows_share_same_public_source_read(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(url,**kwargs):
            entered.set();await release.wait()
            return 200,self.fixture,url
        self.web.fetch_text.side_effect=fetch
        peer=self.peer();peer.fetch_text.side_effect=AssertionError('duplicate CDN read')
        owner=asyncio.create_task(self.collect())
        await entered.wait()
        waiter=asyncio.create_task(self.collect(peer,page='/different'))
        await asyncio.sleep(0)
        release.set();await asyncio.gather(owner,waiter)
        self.web.fetch_text.assert_awaited_once();peer.fetch_text.assert_not_awaited()
        self.assertEqual(self.web._reviewed_module_ids,peer._reviewed_module_ids)

    async def test_cancelled_waiter_cannot_cancel_owner_or_poison_next_window(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(url,**kwargs):
            entered.set();await release.wait()
            return 200,self.source,url
        self.web.fetch_text.side_effect=fetch
        owner=asyncio.create_task(self.collect())
        await entered.wait()
        waiter=asyncio.create_task(self.collect(page='/different'))
        await asyncio.sleep(0);waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiter
        self.assertFalse(owner.done())
        release.set();await owner
        await self.collect(page='/third')
        self.web.fetch_text.assert_awaited_once()

    async def test_cancelled_owner_installs_nothing_and_independent_waiter_can_fetch(self):
        entered=asyncio.Event()
        async def fetch(url,**kwargs):
            entered.set();await asyncio.Event().wait()
        self.web.fetch_text.side_effect=fetch
        peer=self.peer();peer.fetch_text.side_effect=lambda url,**kwargs:(200,self.source,url)
        owner=asyncio.create_task(self.collect())
        await entered.wait()
        waiter=asyncio.create_task(self.collect(peer,page='/different'))
        await asyncio.sleep(0);owner.cancel()
        with self.assertRaises(asyncio.CancelledError):await owner
        await waiter
        self.web.fetch_text.assert_awaited_once();peer.fetch_text.assert_awaited_once()
        self.assertEqual(len(cache._cache),1)
        self.assertEqual(peer._module_collection_progress['collected_scripts'],1)

    async def test_failed_foreign_and_oversized_reads_never_install_cached_evidence(self):
        for response in ((503,self.fixture,self.a),(200,self.fixture,'https://example.com/evil.js'),
                         (200,'x'*(refresh.SCRIPT_BYTES+1),self.a)):
            refresh._cache.clear();cache._cache.clear()
            self.web.fetch_text.return_value=response
            await self.collect()
            self.assertEqual(len(cache._cache),0)
            self.assertEqual(self.web._module_collection_progress['collected_scripts'],0)

    async def test_cached_sources_still_consume_strict_per_window_source_budget(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.source,url)
        await self.collect(urls=[self.a,self.b],page='/warm')
        self.web.fetch_text.reset_mock()
        with patch.object(refresh,'TOTAL_BYTES',len(self.source.encode())):
            await self.collect(urls=[self.a,self.b],page='/bounded')
        self.web.fetch_text.assert_not_awaited()
        self.assertEqual(self.web._module_collection_progress['collected_scripts'],1)
        self.assertEqual(self.web._module_collection_progress['pending_scripts'],1)
        self.assertFalse(self.web._module_collection_progress['collection_complete'])

    async def test_smaller_window_budget_cannot_install_a_larger_cached_script(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect()
        peer=self.peer();peer.fetch_text.side_effect=AssertionError('must use known cached size')
        with patch.object(refresh,'SCRIPT_BYTES',len(self.fixture.encode())-1):
            await self.collect(peer,page='/smaller-budget')
        peer.fetch_text.assert_not_awaited()
        self.assertEqual(peer._reviewed_module_ids['accepted'],{})
        self.assertEqual(peer._module_collection_progress['collected_scripts'],0)

    async def test_smaller_budget_rejects_cached_source_before_copying_parsed_factories(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect()
        with patch.object(cache,'_copy',side_effect=AssertionError('over-budget factories cannot be copied')):
            result=await cache.read_script(self.web,self.a,referer='https://business.facebook.com/latest/settings',
                wanted=[],budget=1,ttl=refresh.TTL)
        self.assertFalse(result['valid'])
        self.assertTrue(result['cache_hit'])
        self.assertNotIn('rows',result)
        self.web.fetch_text.assert_awaited_once()

    async def test_new_url_with_changed_schema_is_read_and_revokes_previous_proof(self):
        name='BizKitSettingsCreateAdAccountMutation'
        old=refresh.baselines()[name]['doc_id']
        changed=self.fixture.replace(old,'99999999999999991').replace('currency','new_currency')
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture if url==self.a else changed,url)
        await self.collect()
        await self.collect(urls=[self.a,self.b],page='/changed')
        self.assertEqual(self.web.fetch_text.await_count,2)
        self.assertIn(name,self.web._reviewed_module_ids['blocked'])
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.web,name,old)

    async def reject_contract(self):
        name='BizKitSettingsCreateAdAccountMutation'
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,
            self.fixture if url==self.a else self.fixture.replace('currency','new_currency'),url)
        await self.collect()
        await self.collect(urls=[self.b],page='/changed')
        return name,refresh.baselines()[name]['doc_id']

    async def test_old_whole_window_cannot_clear_a_newer_rejection(self):
        name,old=await self.reject_contract()
        previous=copy.deepcopy(next(iter(refresh._cache.values()))['snapshot'])
        refresh._apply_snapshot(self.web,previous)
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.web,name,old)

    async def test_another_session_cannot_use_old_scope_cache_after_rejection(self):
        name,old=await self.reject_contract()
        peer=self.peer();peer.fetch_text.side_effect=OSError('Fresh source unavailable')
        await self.collect(peer,page='/different')
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(peer,name,old)

    async def test_rejection_survives_restart_without_source_or_window_caches(self):
        name,old=await self.reject_contract()
        refresh._cache.clear();cache._cache.clear()
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.peer(),name,old)

    async def test_fresh_full_source_can_recover_but_stale_negative_window_cannot_revoke_it(self):
        name,old=await self.reject_contract()
        rejected=copy.deepcopy(list(refresh._cache.values())[-1]['snapshot'])
        await self.collect(page='/fresh-recovery')
        self.assertEqual(refresh.effective_doc_id(self.web,name,old),old)
        self.assertEqual(self.web.fetch_text.await_count,3,'recovery must reacquire the full source')
        refresh._apply_snapshot(self.web,rejected)
        self.assertEqual(refresh.effective_doc_id(self.web,name,old),old)

    async def test_inflight_old_collection_cannot_restore_a_concurrently_rejected_contract(self):
        entered,release=asyncio.Event(),asyncio.Event()
        peer=self.peer()
        async def delayed(url,**kwargs):
            entered.set();await release.wait();return 200,self.fixture,url
        peer.fetch_text.side_effect=delayed
        pending=asyncio.create_task(self.collect(peer,urls=['https://static.xx.fbcdn.net/pending.js'],page='/pending'))
        await entered.wait()
        name,old=await self.reject_contract()
        release.set();await pending
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(peer,name,old)

    async def test_expired_baseline_observation_cannot_silently_fall_back_to_static_id(self):
        name='BizKitSettingsCreateAdAccountMutation';old=refresh.baselines()[name]['doc_id']
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect(now=1000)
        with patch('time.monotonic',return_value=2801),self.assertRaises(ProvisioningError):
            refresh.effective_doc_id(self.web,name,old)

    async def test_expired_snapshot_cannot_authorize_a_new_session_baseline(self):
        name='BizKitSettingsCreateAdAccountMutation';old=refresh.baselines()[name]['doc_id']
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect(now=1000)
        snapshot=copy.deepcopy(next(iter(refresh._cache.values()))['snapshot']);peer=self.peer()
        with patch('time.monotonic',return_value=2801):
            refresh._apply_snapshot(peer,snapshot)
            with self.assertRaises(ProvisioningError):refresh.effective_doc_id(peer,name,old)

    async def test_rejection_is_isolated_by_profile_actor_and_proxy(self):
        name,old=await self.reject_contract()
        for field,value in [('name','other'),('actor','222222222'),('proxy','http://other:8080')]:
            peer=self.peer()
            if field=='actor':peer.profile.cookies['c_user']=value
            else:setattr(peer.profile,field,value)
            self.assertEqual(refresh.effective_doc_id(peer,name,old),old)

    async def test_concurrent_rejection_replay_cannot_advance_or_erase_newer_state(self):
        from app.contract_maintenance import source_invalidation as invalidation
        name,old=await self.reject_contract()
        rejected=copy.deepcopy(list(refresh._cache.values())[-1]['snapshot'])
        before=invalidation.state(self.web)
        await asyncio.gather(*(asyncio.to_thread(refresh._apply_snapshot,self.peer(),rejected) for _ in range(8)))
        self.assertEqual(invalidation.state(self.web),before)
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.peer(),name,old)

    async def test_failed_rejection_persistence_blocks_other_sessions_and_cached_acceptance(self):
        from app.contract_maintenance import source_invalidation as invalidation
        import sqlite3
        name='BizKitSettingsCreateAdAccountMutation';old=refresh.baselines()[name]['doc_id']
        with patch.object(invalidation.sqlite3,'connect',side_effect=sqlite3.OperationalError('fixture disk full')):
            with self.assertRaises(ProvisioningError):
                refresh._apply_snapshot(self.web,{'accepted':{},'blocked':[name]})
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.peer(),name,old)

    async def test_changed_actor_cannot_use_previous_session_proof_or_snapshot(self):
        name='BizKitSettingsCreateAdAccountMutation';old=refresh.baselines()[name]['doc_id']
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect()
        snapshot=copy.deepcopy(next(iter(refresh._cache.values()))['snapshot'])
        self.web.profile.cookies['c_user']='222222222'
        with self.assertRaises(ProvisioningError):refresh.effective_doc_id(self.web,name,old)
        with self.assertRaises(ProvisioningError):refresh._apply_snapshot(self.web,snapshot)

    async def test_actor_change_during_collection_cannot_install_retained_proof(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def delayed(url,**kwargs):
            entered.set();await release.wait();return 200,self.fixture,url
        self.web.fetch_text.side_effect=delayed
        pending=asyncio.create_task(self.collect())
        await entered.wait();self.web.profile.cookies['c_user']='222222222';release.set()
        with self.assertRaises(ProvisioningError):await pending
        self.assertFalse(getattr(self.web,'_reviewed_module_ids',{}).get('accepted'))

    async def test_changed_code_at_expired_same_url_is_fetched_and_blocked(self):
        name='BizKitSettingsCreateAdAccountMutation'
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture,url)
        await self.collect(now=1000)
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.fixture.replace('currency','new_currency'),url)
        await self.collect(now=2801,page='/expired')
        self.assertEqual(self.web.fetch_text.await_count,2)
        self.assertIn(name,self.web._reviewed_module_ids['blocked'])

    async def test_cache_memory_and_entry_budgets_bound_reuse_without_losing_collection(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.source,url)
        with patch.object(cache,'MAX_ENTRIES',1):
            await self.collect(urls=[self.a,self.b])
            self.assertEqual(len(cache._cache),1)
            await self.collect(page='/evicted')
        self.assertEqual(self.web.fetch_text.await_count,3)
        cache._cache.clear()
        with patch.object(cache,'MAX_BYTES',1):
            await self.collect(page='/too-large-for-cache')
            self.assertEqual(len(cache._cache),0)
        self.assertTrue(self.web._module_collection_progress['collection_complete'])

    async def test_cache_hit_cannot_bypass_current_document_auth_gate(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.source,url)
        await self.collect()
        await self.collect(page='/checkpoint/')
        self.web.fetch_text.assert_awaited_once()

    async def test_mutating_returned_rows_cannot_poison_another_window(self):
        self.web.fetch_text.side_effect=lambda url,**kwargs:(200,self.source,url)
        kwargs={'referer':'https://business.facebook.com/latest/settings','wanted':[],
                'budget':refresh.SCRIPT_BYTES,'ttl':refresh.TTL}
        first=await cache.read_script(self.web,self.a,**kwargs)
        first['rows'][0]['name']='ChangedByCaller'
        second=await cache.read_script(self.web,self.a,**kwargs)
        self.assertEqual(second['rows'][0]['name'],'FixtureModule')
        self.assertTrue(second['cache_hit'])

    async def test_profile_change_while_waiting_for_source_cannot_reuse_old_actor_evidence(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(url,**kwargs):
            entered.set();await release.wait()
            return 200,self.source,url
        self.web.fetch_text.side_effect=fetch
        peer=self.peer();peer.fetch_text.side_effect=AssertionError('changed actor cannot fetch under old scope')
        kwargs={'referer':'https://business.facebook.com/latest/settings','wanted':[],
                'budget':refresh.SCRIPT_BYTES,'ttl':refresh.TTL}
        owner=asyncio.create_task(cache.read_script(self.web,self.a,**kwargs))
        await entered.wait()
        waiter=asyncio.create_task(cache.read_script(peer,self.a,**kwargs))
        await asyncio.sleep(0)
        peer.profile.cookies['c_user']='222222222'
        release.set();await owner
        with self.assertRaises(ValueError):await waiter
        peer.fetch_text.assert_not_awaited()

    async def test_fetch_failure_is_a_cache_miss_and_never_creates_an_entry(self):
        self.web.fetch_text.side_effect=OSError('SECRET proxy')
        stats={'hits':0,'misses':0}
        with self.assertRaises(OSError):
            await cache.read_script(self.web,self.a,referer='https://business.facebook.com/latest/settings',
                wanted=[],budget=refresh.SCRIPT_BYTES,ttl=refresh.TTL,stats=stats)
        self.assertEqual(stats,{'hits':0,'misses':1})
        self.assertEqual(len(cache._cache),0)


if __name__ == '__main__':unittest.main()
