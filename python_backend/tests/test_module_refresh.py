import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
import re
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.contract_maintenance import module_refresh as refresh
from app.contract_maintenance.ads_sources import module_records
from app.contract_maintenance.build_module_refresh import build
from app.provisioning.models import ProvisioningError

ROOT = Path(__file__).resolve().parents[1]


def observed_rows():
    rows = []
    for path in (ROOT / 'tests/fixtures').glob('meta_*observed*.js'):
        source = path.read_text()
        selected = re.findall(r'__d\([\"\']([^\"\']+)', source)
        rows.extend(module_records(source, selected=selected))
    return rows


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.refs = refresh.baselines()
        self.rows = observed_rows()
        self.name = 'BizKitSettingsCreateAdAccountMutation'
        self.old = self.refs[self.name]['doc_id']
        self.new = '99999999999999991'

    def test_baselines_reproduce_reviewed_source_and_all_installed_rows(self):
        self.assertEqual(build(ROOT), json.loads(refresh.BASELINES.read_text()))
        self.assertEqual(len(self.refs), 14)
        selected = refresh.select_updates(self.rows, self.refs)
        self.assertEqual(set(selected['accepted']), set(self.refs))
        self.assertEqual(selected['blocked'], [])

    def updated(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if row['name'] in self.refs[self.name]['artifact_modules']:
                row['source'] = row['source'].replace(self.old, self.new)
                row['module_source'] = row['module_source'].replace(self.old, self.new)
        return rows

    def test_only_id_change_is_automatically_used_without_template_rewrite(self):
        result = refresh.select_updates(self.updated(), self.refs)
        web = SimpleNamespace()
        refresh._apply_snapshot(web, result)
        self.assertEqual(refresh.effective_doc_id(web, self.name, self.old), self.new)
        self.assertEqual(refresh.effective_doc_id(web, self.name, 'other-id'), 'other-id')

    def test_changed_schema_blocks_submission(self):
        rows = self.updated()
        for row in rows:
            if row['name'] == self.name + '.graphql':
                row['source'] = row['source'].replace('currency', 'new_currency')
        web = SimpleNamespace()
        refresh._apply_snapshot(web, refresh.select_updates(rows, self.refs))
        with self.assertRaises(ProvisioningError) as error:
            refresh.effective_doc_id(web, self.name, self.old)
        self.assertEqual(error.exception.code, 'PRIVATE_MODULE_SCHEMA_CHANGED')

    def test_changed_sender_does_not_authorize_new_id(self):
        rows = self.updated()
        for row in rows:
            if row['name'] in self.refs[self.name]['senders']:
                row['source'] += '\n// changed sender'
        self.assertIn(self.name, refresh.select_updates(rows, self.refs)['blocked'])

    def test_changed_sender_also_invalidates_an_unchanged_baseline_id(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:
            if row['name'] in self.refs[self.name]['senders']:
                row['source'] += '\n// changed sender'
        self.assertIn(self.name,refresh.select_updates(rows,self.refs)['invalidated'])

    def test_missing_sender_is_pending_collection_not_a_rejected_contract(self):
        rows=[row for row in self.updated() if row['name'] not in self.refs[self.name]['senders']]
        result=refresh.select_updates(rows,self.refs)
        self.assertIn(self.name,result['blocked'])
        self.assertNotIn(self.name,result['invalidated'])

    def test_ambiguous_artifact_cannot_override_id(self):
        rows = self.updated()
        old = next(row for row in self.rows if row['name'] == self.name + '_facebookRelayOperation')
        self.assertIn(self.name, refresh.select_updates(rows + [old], self.refs)['blocked'])

    def test_missing_sender_and_unknown_operation_cannot_install(self):
        rows = [row for row in self.updated() if row['name'] not in self.refs[self.name]['senders']]
        result = refresh.select_updates(rows, self.refs)
        self.assertNotIn(self.name, result['accepted'])
        self.assertIn(self.name, result['blocked'])
        self.assertEqual(refresh.select_updates(rows, {})['accepted'], {})

    def test_expired_proof_is_not_used_and_changed_module_revokes_old_proof(self):
        result = refresh.select_updates(self.updated(), self.refs, now=time.monotonic()-refresh.TTL-1)
        web = SimpleNamespace(_reviewed_module_ids=result)
        with self.assertRaises(ProvisioningError) as error:
            refresh.effective_doc_id(web, self.name, self.old)
        self.assertEqual(error.exception.code, 'PRIVATE_MODULE_REFRESH_REQUIRED')
        refresh._apply_snapshot(web, refresh.select_updates(self.updated(), self.refs))
        refresh._apply_snapshot(web, {'accepted': {}, 'blocked': [self.name]})
        with self.assertRaises(ProvisioningError):
            refresh.effective_doc_id(web, self.name, self.old)

    def test_expired_new_id_is_not_downgraded_after_incomplete_refresh(self):
        web=SimpleNamespace(_reviewed_module_ids=refresh.select_updates(self.updated(),self.refs,now=time.monotonic()-refresh.TTL-1))
        refresh._apply_snapshot(web, {'accepted':{},'blocked':[]})
        with self.assertRaises(ProvisioningError):
            refresh.effective_doc_id(web,self.name,self.old)

    def test_long_lived_session_refreshes_expired_proof_with_backoff(self):
        web=SimpleNamespace(_reviewed_module_ids=refresh.select_updates(self.updated(),self.refs,now=time.monotonic()-refresh.TTL-1))
        self.assertTrue(refresh.refresh_due(web))
        web._module_refresh_attempted_at=time.monotonic()
        self.assertFalse(refresh.refresh_due(web))

    def test_incomplete_collection_resumes_even_without_an_expired_relay_id(self):
        web=SimpleNamespace(_reviewed_module_ids={'accepted':{},'blocked':[]},
            _module_collection_progress={'collection_complete':False},
            _module_collection_due_at=1060,_module_refresh_attempted_at=1000)
        with patch.object(refresh.time,'monotonic',return_value=1059):
            self.assertFalse(refresh.refresh_due(web))
        with patch.object(refresh.time,'monotonic',return_value=1061):
            self.assertTrue(refresh.refresh_due(web))


class AutomaticReadsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'REMASK_DATA_DIR':folder.name});env.start();self.addCleanup(env.stop)
        refresh._cache.clear()
        from app.contract_maintenance import module_source_cache
        module_source_cache._cache.clear()
        self.web = SimpleNamespace(profile=SimpleNamespace(name='fixture', proxy='http://proxy.test:8080',
            cookies={'c_user':'111111111'}), fetch_text=AsyncMock())
        self.url = 'https://business.facebook.com/latest/settings'
        self.cdn = 'https://static.xx.fbcdn.net/rsrc.php/v1/example.js'
        self.document = '<script src="' + self.cdn + '"></script>'

    async def test_normal_document_automatically_reads_observed_cdn_once(self):
        source = (ROOT / 'tests/fixtures/meta_create_rk_observed_20261008.js').read_text()
        self.web.fetch_text.return_value = (200, source, self.cdn)
        await refresh.refresh_from_document(self.web, self.document, self.url)
        await refresh.refresh_from_document(self.web, self.document, self.url)
        self.assertEqual(self.web.fetch_text.await_count, 1)
        self.assertIn('BizKitSettingsCreateAdAccountMutation', self.web._reviewed_module_ids['accepted'])
        self.assertEqual(self.web.fetch_text.call_args.args, (self.cdn,))
        self.assertEqual(self.web.fetch_text.call_args.kwargs['referer'], self.url)

    async def test_concurrent_profile_sessions_share_one_collection_window(self):
        source = (ROOT / 'tests/fixtures/meta_create_rk_observed_20261008.js').read_text()
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(url, **kwargs):
            entered.set()
            await release.wait()
            return 200, source, url
        self.web.fetch_text.side_effect = fetch
        peers = [self.web] + [SimpleNamespace(profile=copy.deepcopy(self.web.profile),
            fetch_text=AsyncMock(side_effect=AssertionError('Duplicate CDN request'))) for _ in range(3)]
        first = asyncio.create_task(refresh.refresh_from_document(peers[0], self.document, self.url))
        await entered.wait()
        others = [asyncio.create_task(refresh.refresh_from_document(peer, self.document, self.url)) for peer in peers[1:]]
        await asyncio.sleep(0)
        self.assertTrue(all(not task.done() for task in others))
        release.set()
        await asyncio.gather(first, *others)
        self.assertEqual(self.web.fetch_text.await_count, 1)
        for peer in peers:
            self.assertIn('BizKitSettingsCreateAdAccountMutation', peer._reviewed_module_ids['accepted'])
        self.assertTrue(all(peer.fetch_text.await_count == 0 for peer in peers[1:]))

    async def test_cancelled_waiter_does_not_cancel_collection_or_duplicate_it(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(url, **kwargs):
            entered.set()
            await release.wait()
            return 200, '__d("FixtureModule",[],function(){return {};});', url
        self.web.fetch_text.side_effect = fetch
        owner = asyncio.create_task(refresh.refresh_from_document(self.web, self.document, self.url))
        await entered.wait()
        waiter = asyncio.create_task(refresh.refresh_from_document(self.web, self.document, self.url))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        await owner
        await refresh.refresh_from_document(self.web, self.document, self.url)
        self.assertEqual(self.web.fetch_text.await_count, 1)

    async def test_distinct_profiles_never_share_collection_or_proxy(self):
        other = SimpleNamespace(profile=SimpleNamespace(name='other', proxy='http://other.test:8080',
            cookies={'c_user':'222222222'}), fetch_text=AsyncMock())
        source = '__d("FixtureModule",[],function(){return {};});'
        self.web.fetch_text.return_value = other.fetch_text.return_value = (200, source, self.cdn)
        await asyncio.gather(refresh.refresh_from_document(self.web, self.document, self.url),
            refresh.refresh_from_document(other, self.document, self.url))
        self.assertEqual(self.web.fetch_text.await_count, 1)
        self.assertEqual(other.fetch_text.await_count, 1)

    async def test_all_modules_and_later_script_windows_are_collected(self):
        import os,tempfile
        cdns=['https://static.xx.fbcdn.net/fixture-%d.js'%i for i in range(29)]
        doc=''.join('<script src="'+url+'"></script>' for url in cdns)
        async def fetch(url,**kwargs):
            index=cdns.index(url)
            return 200,'__d("GeneralMetaModule%d",["MetaDependency"],function(){return {};});'%index,url
        self.web.fetch_text.side_effect=fetch
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            with patch.object(refresh.time,'monotonic',return_value=1000):
                await refresh.refresh_from_document(self.web,doc,self.url)
            self.assertEqual(self.web.fetch_text.await_count,24)
            with patch.object(refresh.time,'monotonic',return_value=1030):
                await refresh.refresh_from_document(self.web,doc,self.url)
            self.assertEqual(self.web.fetch_text.await_count,24)
            with patch.object(refresh.time,'monotonic',return_value=1061):
                await refresh.refresh_from_document(self.web,doc,self.url)
            self.assertEqual(self.web.fetch_text.await_count,29)
            saved=json.loads((Path(folder)/'private-module-catalog.json').read_text())
            self.assertTrue(all('GeneralMetaModule%d'%i in saved['modules'] for i in range(29)))
            self.assertNotIn('source',saved['modules']['GeneralMetaModule28'])
            self.assertFalse(saved['checked_live'])

    async def test_checkpoint_and_foreign_urls_never_start_collection(self):
        for url in ['https://business.facebook.com/checkpoint/', 'https://example.com/latest/settings']:
            await refresh.refresh_from_document(self.web, self.document, url)
        await refresh.refresh_from_document(self.web, '<script src="https://example.com/evil.js"></script>', self.url)
        self.web.fetch_text.assert_not_awaited()

    async def collect(self, sources, *, document=None, fail_once=()):
        import os, tempfile
        failed = set()
        async def fetch(url, **kwargs):
            if url in fail_once and url not in failed:
                failed.add(url)
                return 503, '', url
            return 200, sources[url], url
        self.web.fetch_text.side_effect = fetch
        doc = document or ''.join('<script src="' + url + '"></script>' for url in sources)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'REMASK_DATA_DIR': folder}):
            for index in range(40):
                with patch.object(refresh.time, 'monotonic', return_value=1000 + index * 61):
                    await refresh.refresh_from_document(self.web, doc, self.url)
                if next(iter(refresh._cache.values()))['drained']:
                    break
            return json.loads((Path(folder) / 'private-module-catalog.json').read_text())

    async def test_inventory_collects_beyond_the_old_256_url_ceiling(self):
        sources = {f'https://static.xx.fbcdn.net/{i}.js':
            f'__d("MetaFullInventory{i}",[],function(){{return {{}};}});' for i in range(270)}
        result = await self.collect(sources)
        self.assertEqual(self.web.fetch_text.await_count, 270)
        self.assertIn('MetaFullInventory269', result['modules'])
        self.assertTrue(self.web._module_collection_progress['collection_complete'])

    async def test_deferred_maps_discovered_in_later_scripts_are_collected(self):
        root = 'https://static.xx.fbcdn.net/root.js'
        child = 'https://static.xx.fbcdn.net/child.js'
        sources = {root: 'var metadata=' + json.dumps({'rsrcMap': {'child': {'type': 'js', 'src': child}}}) + ';',
            child: '__d("DeferredGeneralMetaModule",[],function(){return {};});'}
        result = await self.collect(sources, document='<script src="' + root + '"></script>')
        self.assertIn('DeferredGeneralMetaModule', result['modules'])
        self.assertEqual(self.web._module_collection_progress['collected_scripts'], 2)

    async def test_transient_failed_resource_is_retried_without_losing_later_modules(self):
        sources = {f'https://static.xx.fbcdn.net/{i}.js':
            f'__d("MetaRetry{i}",[],function(){{return {{}};}});' for i in range(26)}
        result = await self.collect(sources, fail_once=[next(iter(sources))])
        self.assertEqual(self.web.fetch_text.await_count, 27)
        self.assertIn('MetaRetry0', result['modules'])
        self.assertIn('MetaRetry25', result['modules'])
        self.assertTrue(self.web._module_collection_progress['collection_complete'])

    async def test_artifact_and_sender_in_separate_windows_form_one_complete_proof(self):
        name = 'BizKitSettingsCreateAdAccountMutation'
        specs = refresh.baselines()[name]
        old, new = specs['doc_id'], '99999999999999991'
        rows = [copy.deepcopy(row) for row in observed_rows()
            if row['name'] in set(specs['artifact_modules']) | set(specs['senders'])]
        for row in rows:
            row['module_source'] = row['module_source'].replace(old, new)
        artifacts = [row for row in rows if row['name'] in specs['artifact_modules']]
        senders = [row for row in rows if row['name'] in specs['senders']]
        sources = {f'https://static.xx.fbcdn.net/{i}.js': '' for i in range(25)}
        sources['https://static.xx.fbcdn.net/0.js'] = '\n'.join(row['module_source'] for row in artifacts)
        sources['https://static.xx.fbcdn.net/24.js'] = '\n'.join(row['module_source'] for row in senders)
        await self.collect(sources)
        self.assertEqual(self.web._reviewed_module_ids['accepted'][name]['doc_id'], new)
        self.assertNotIn(name, self.web._reviewed_module_ids['blocked'])

    async def test_invalid_editor_table_does_not_suppress_unrelated_module_inventory(self):
        source = '__d("AdsTargetingEducation",[],function(){return {BAD:fetch("invalid")};});' \
            '__d("IndependentSyncModule",[],function(){return {};});'
        with patch.object(refresh, 'refresh_editor', side_effect=ValueError('changed schema')):
            result = await self.collect({self.cdn: source})
        self.assertIn('IndependentSyncModule', result['modules'])

    async def test_failed_capture_is_never_reported_as_complete(self):
        source='__d("SuccessfulGeneralModule",[],function(){return {};});'
        good='https://static.xx.fbcdn.net/good.js'
        bad='https://static.xx.fbcdn.net/bad.js'
        async def fetch(url,**kwargs):
            return (200,source,url) if url==good else (503,'',url)
        self.web.fetch_text.side_effect=fetch
        import os,tempfile
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'REMASK_DATA_DIR':folder}):
            for now in (1000,1061,1122):
                with patch.object(refresh.time,'monotonic',return_value=now):
                    await refresh.refresh_from_document(self.web,
                        '<script src="'+good+'"></script><script src="'+bad+'"></script>',self.url)
        self.assertEqual(self.web.fetch_text.await_count,4)
        self.assertFalse(self.web._module_collection_progress['collection_complete'])
        self.assertEqual(self.web._module_collection_progress['failed_scripts'],1)

    async def test_unavailable_proxy_does_not_reroute_or_replay(self):
        self.web.fetch_text.side_effect = OSError('private proxy password must never be recorded')
        with self.assertLogs('remask.module_refresh', level='INFO') as logs:
            await refresh.refresh_from_document(self.web, self.document, self.url)
        self.assertEqual(self.web.fetch_text.await_count, 1)
        self.assertNotIn('password', '\n'.join(logs.output))
        self.assertEqual(self.web._reviewed_module_ids['accepted'], {})

    async def test_cdn_redirect_and_changed_profile_cannot_reuse_proof(self):
        self.web.fetch_text.return_value = (200, 'unexpected code', 'https://example.com/evil.js')
        await refresh.refresh_from_document(self.web, self.document, self.url)
        self.assertEqual(self.web._reviewed_module_ids['accepted'], {})
        self.web.profile.name = 'second-profile'
        await refresh.refresh_from_document(self.web, self.document, self.url)
        self.assertEqual(self.web.fetch_text.await_count, 2)

    async def test_cdn_uses_http2_only_and_never_receives_facebook_cookie(self):
        from app.private_http import PrivateHttpClient
        import httpx
        client = PrivateHttpClient(profile_name='fixture', proxy='http://127.0.0.1:8080',
            cookies={'c_user':'111111111'}, user_agent='fixture', timeout_seconds=5, pool_size=2)
        try:
            transport = client._client._transport_for_url(httpx.URL(self.cdn))
            self.assertTrue(transport._pool._http2)
            self.assertFalse(transport._pool._http1)
            request = client._client.build_request('GET', self.cdn)
            self.assertNotIn('cookie', request.headers)
        finally:
            await client.close()


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'REMASK_DATA_DIR':folder.name});env.start();self.addCleanup(env.stop)

    async def test_verified_id_reaches_existing_http_sender_once_with_same_variables(self):
        from fb_worker import FacebookWebSession, FacebookBootstrap, WebProfile
        web = FacebookWebSession(WebProfile(name='fixture', cookies={'c_user':'111111111'},
            proxy='http://proxy.test:8080', user_agent='fixture'))
        web._bootstrap = FacebookBootstrap(fb_dtsg='fixture-token', actor_id='111111111')
        web._business_bootstrap = AsyncMock(return_value=web._bootstrap)
        name = 'BizKitSettingsCreateAdAccountMutation'
        old = refresh.baselines()[name]['doc_id']
        new = '99999999999999991'
        refresh._apply_snapshot(web, {'accepted':{name:{'doc_id':new,'baseline_doc_id':old,'until':time.monotonic()+60}},'blocked':[]})
        sent=[]
        class Response:
            status=200
            headers={}
            url='https://business.facebook.com/api/graphql/'
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def text(self): return '{"data":{"fixture":true}}'
        def post(url,**kwargs):
            sent.append(kwargs['data']); return Response()
        web._ensure_session = AsyncMock(return_value=SimpleNamespace(post=post))
        intent=AsyncMock()
        variables={'businessID':'222222222','currency':'EUR'}
        await web.graphql(old,variables,friendly_name=name,before_submit=intent)
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['doc_id'],new)
        self.assertEqual(json.loads(sent[0]['variables']),variables)
        self.assertEqual(sent[0]['fb_dtsg'],'fixture-token')
        intent.assert_awaited_once()

    async def test_changed_module_stops_before_durable_intent_and_post(self):
        from fb_worker import FacebookWebSession, FacebookBootstrap, WebProfile
        web=FacebookWebSession(WebProfile(name='fixture',cookies={'c_user':'111111111'},
            proxy='http://proxy.test:8080',user_agent='fixture'))
        web._bootstrap=FacebookBootstrap(fb_dtsg='fixture-token',actor_id='111111111')
        web._business_bootstrap=AsyncMock(return_value=web._bootstrap)
        name='BizKitSettingsCreateAdAccountMutation'
        web._reviewed_module_ids={'accepted':{},'blocked':[name]}
        web._ensure_session=AsyncMock()
        intent=AsyncMock()
        with self.assertRaises(ProvisioningError):
            await web.graphql(refresh.baselines()[name]['doc_id'],{},friendly_name=name,before_submit=intent)
        intent.assert_not_awaited()
        web._ensure_session.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
