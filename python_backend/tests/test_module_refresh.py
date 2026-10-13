import copy
import json
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
        self.assertEqual(refresh.effective_doc_id(web, self.name, self.old), self.old)
        refresh._apply_snapshot(web, refresh.select_updates(self.updated(), self.refs))
        refresh._apply_snapshot(web, {'accepted': {}, 'blocked': [self.name]})
        with self.assertRaises(ProvisioningError):
            refresh.effective_doc_id(web, self.name, self.old)


class AutomaticReadsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        refresh._cache.clear()
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

    async def test_checkpoint_and_foreign_urls_never_start_collection(self):
        for url in ['https://business.facebook.com/checkpoint/', 'https://example.com/latest/settings']:
            await refresh.refresh_from_document(self.web, self.document, url)
        await refresh.refresh_from_document(self.web, '<script src="https://example.com/evil.js"></script>', self.url)
        self.web.fetch_text.assert_not_awaited()

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
