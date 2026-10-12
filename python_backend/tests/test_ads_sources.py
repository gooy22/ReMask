import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fb_worker import AuthenticationError, RemoteRequestError

from app.contract_maintenance.ads_sources import compact_record, deferred_script_urls, module_records, module_source, public_script_url
from app.contract_maintenance.launch_http_audit import collect_sources, inspect_saved_sources, error_diagnostic, run


class LaunchAuditFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_transport_metadata_is_actionable_without_credentials_or_false_auth_gate(self):
        for kind,code in [('ProxyError','PROFILE_PROXY_UNAVAILABLE'),('ConnectError','PRIVATE_HTTP_CONNECTION_UNAVAILABLE'),
                          ('ConnectTimeout','PRIVATE_HTTP_CONNECTION_UNAVAILABLE'),('ReadTimeout','PRIVATE_HTTP_TIMEOUT')]:
            error=RemoteRequestError('secret-proxy-user secret-session',meta_payload={'transport_failure':{'kind':kind,
                'proxy_configured':True}},request_may_have_been_sent=False,transport_stage='bootstrap')
            result=error_diagnostic(error)
            self.assertEqual(result['code'],code)
            self.assertTrue(result['post_not_sent'])
            self.assertEqual(result['transport_stage'],'bootstrap')
            self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(error_diagnostic(httpx.ProxyError('secret'))['code'],'PROFILE_PROXY_UNAVAILABLE')

    def test_checkpoint_requires_structured_auth_evidence(self):
        error=AuthenticationError('login/checkpoint')
        error.meta_payload={'business_precheck':[{'auth_reason':'checkpoint_redirect'}]}
        self.assertEqual(error_diagnostic(error)['code'],'CHECKPOINT_REQUIRED')
        self.assertEqual(error_diagnostic(RuntimeError('login/checkpoint'))['code'],'ADS_HTTP_AUDIT_INCONCLUSIVE')

    async def test_failed_profile_connection_stops_before_sources_pages_or_payment_queries(self):
        web=SimpleNamespace(bootstrap=AsyncMock(side_effect=RemoteRequestError('secret',
            meta_payload={'transport_failure':{'kind':'ProxyError'}},request_may_have_been_sent=False,transport_stage='bootstrap')),
            graphql=AsyncMock(),fetch_text=AsyncMock())
        session=SimpleNamespace(facebook_web=AsyncMock(return_value=web))
        class FixtureSession:
            async def __aenter__(self):return session
            async def __aexit__(self,*args):pass
        pool=SimpleNamespace(profile_locks={'19':asyncio.Lock()},resolver=SimpleNamespace(resolve=AsyncMock(return_value=object())),
            private_launch=SimpleNamespace(contracts=SimpleNamespace(status=lambda:{})))
        with patch.dict(os.environ,{'REMASK_LAUNCH_HTTP_AUDIT_PROFILE':'19','REMASK_LAUNCH_HTTP_AUDIT_ACCOUNT':'123456789',
                    'REMASK_LAUNCH_HTTP_AUDIT_BUSINESS':'987654321','REMASK_LAUNCH_HTTP_AUDIT_SOURCES':'1',
                    'REMASK_LAUNCH_HTTP_AUDIT_SAVED':'0'}),patch('app.contract_maintenance.launch_http_audit.ProfileSession',return_value=FixtureSession()),\
             patch('app.contract_maintenance.launch_http_audit.collect_sources',AsyncMock()) as sources,\
             patch('app.contract_maintenance.launch_http_audit.inspect_pages',AsyncMock()) as pages,\
             patch('app.contract_maintenance.launch_http_audit.inspect_methods',AsyncMock()) as methods,\
             patch('app.contract_maintenance.launch_http_audit.log') as logger:
            await run(pool)
        sources.assert_not_awaited();pages.assert_not_awaited();methods.assert_not_awaited()
        web.graphql.assert_not_awaited();web.fetch_text.assert_not_awaited()
        self.assertEqual(json.loads(logger.info.call_args.args[-1])['code'],'PROFILE_PROXY_UNAVAILABLE')


class AdsBootstrapAuditTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, *, error=None, inventory=True, returned_account='123456789'):
        from app.private_ads_http import AdsManagerContext
        context = SimpleNamespace(cookies={'c_user': '123456'})
        web = SimpleNamespace(bootstrap=AsyncMock(), graphql=AsyncMock(), fetch_text=AsyncMock())
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=web))
        class FixtureSession:
            async def __aenter__(self): return session
            async def __aexit__(self, *args): pass
        client = SimpleNamespace(context=AdsManagerContext('123456789', '987654321', '123456',
            'https://adsmanager-graph.facebook.com', 'v22.0', '654321', 'synthetic-session-secret'),
            get=AsyncMock(return_value={'id': 'act_' + returned_account, 'business': {'id': '987654321'}}))
        pool = SimpleNamespace(profile_locks={'19': asyncio.Lock()},
            resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)),
            private_launch=SimpleNamespace(contracts=SimpleNamespace(status=lambda: {}),
                _inventory_row=lambda *args: {} if inventory else None))
        with patch.dict(os.environ, {'REMASK_LAUNCH_HTTP_AUDIT_PROFILE': '19',
                'REMASK_LAUNCH_HTTP_AUDIT_ACCOUNT': '123456789', 'REMASK_LAUNCH_HTTP_AUDIT_BUSINESS': '987654321',
                'REMASK_LAUNCH_HTTP_AUDIT_ADS_BOOTSTRAP': '1', 'REMASK_LAUNCH_HTTP_AUDIT_SOURCES': '1',
                'REMASK_LAUNCH_HTTP_AUDIT_SAVED': '0'}), \
             patch('app.contract_maintenance.launch_http_audit.ProfileSession', return_value=FixtureSession()), \
             patch('app.private_ads_http.AdsManagerPrivateHttp.open', new=AsyncMock(return_value=client, side_effect=error)) as opened, \
             patch('app.contract_maintenance.launch_http_audit.collect_sources', new=AsyncMock()) as sources, \
             patch('app.contract_maintenance.launch_http_audit.inspect_pages', new=AsyncMock()) as pages, \
             patch('app.contract_maintenance.launch_http_audit.inspect_methods', new=AsyncMock()) as methods, \
             patch('app.contract_maintenance.launch_http_audit.log') as logger:
            await run(pool)
        sources.assert_not_awaited(); pages.assert_not_awaited(); methods.assert_not_awaited()
        web.bootstrap.assert_not_awaited(); web.graphql.assert_not_awaited()
        self.assertNotIn('synthetic-session-secret', str(logger.info.call_args_list))
        self.assertIn('mutation_posts=0', logger.info.call_args.args[0])
        if error is not None or not inventory:
            client.get.assert_not_awaited()
        else:
            client.get.assert_awaited_once_with('/act_123456789', {'fields': ['id', 'business']})
        return opened, json.loads(logger.info.call_args.args[-1])

    async def test_supported_fresh_context_is_reported_without_session_credentials_or_payment_reads(self):
        opened, result = await self.probe()
        opened.assert_awaited_once()
        self.assertEqual(opened.await_args.kwargs, {'account_id': '123456789',
            'business_id': '987654321', 'actor_id': '123456'})
        self.assertEqual(result['code'], 'ADS_MANAGER_BOOTSTRAP_READY')
        self.assertTrue(result['account_scope_verified'])
        self.assertTrue(result['actor_verified'])
        self.assertTrue(result['account_read_checked_live'])

    async def test_foreign_live_account_is_not_bootstrap_success(self):
        _, result = await self.probe(returned_account='888888888')
        self.assertEqual(result['code'], 'PRIVATE_ADS_DRAFT_ACCOUNT_SCOPE_UNVERIFIED')

    async def test_foreign_target_inventory_prevents_document_fetch(self):
        opened, result = await self.probe(inventory=False)
        opened.assert_not_awaited()
        self.assertEqual(result['code'], 'PRIVATE_ADS_DRAFT_ACCOUNT_CONTEXT_UNAVAILABLE')

    async def test_dead_proxy_diagnostic_stops_after_one_read_attempt(self):
        error = RemoteRequestError('secret-proxy-user', meta_payload={'transport_failure': {'kind': 'ProxyError'}},
            request_may_have_been_sent=False, transport_stage='bootstrap')
        opened, result = await self.probe(error=error)
        opened.assert_awaited_once()
        self.assertEqual(result['code'], 'PROFILE_PROXY_UNAVAILABLE')
        self.assertTrue(result['post_not_sent'])


class AdsSourceTests(unittest.TestCase):
    def test_only_observed_unambiguous_public_resources_are_followed(self):
        resources = {"1": {"type": "js", "src": "https://static.xx.fbcdn.net/editor.js"},
                     "2": {"type": "js", "src": "https://static.xx.fbcdn.net/publish.js"},
                     "3": {"type": "js", "src": "https://attacker.test/fake.js"}}
        components = {"AdsEditor": {"r": ["1"], "rdfds": {"m": ["dependency"]}},
                      "dependency": {"rds": {"r": ["2", "3"], "m": ["AdsEditor"]}},
                      "BillingSaveCard": {"r": ["3"]}}
        document = json.dumps({"rsrcMap": resources, "compMap": components})
        self.assertEqual(deferred_script_urls(document), [resources["1"]["src"], resources["2"]["src"]])
        conflict = json.dumps({"rsrcMap": {"2": {"type": "js", "src": "https://static.xx.fbcdn.net/other.js"}}})
        self.assertEqual(deferred_script_urls(document + conflict), [resources["1"]["src"]])
        self.assertEqual(deferred_script_urls(document + json.dumps({"compMap": {"AdsEditor": {"r": ["2"]}}})), [])

    def test_url_credentials_ports_fragments_and_foreign_hosts_are_rejected(self):
        for url in ("http://static.xx.fbcdn.net/a.js", "https://fbcdn.net.attacker.test/a.js",
                    "https://user@static.xx.fbcdn.net/a.js", "https://static.xx.fbcdn.net:443/a.js",
                    "https://static.xx.fbcdn.net/a.js#x", "https://www.facebook.com/a.js"):
            self.assertFalse(public_script_url(url))

    def test_sender_dependencies_precede_unrelated_publish_roots(self):
        resources = {str(i): {"type": "js", "src": f"https://static.xx.fbcdn.net/{i}.js"} for i in range(4)}
        components = {"AdsPublishEntry": {"r": ["0"], "rdfds": {"m": ["AdsDraftSender"]}},
            "AdsPublishOther": {"r": ["3"]},
            "AdsDraftSender": {"r": ["1"], "m": ["transport"]}, "transport": {"r": ["2"]}}
        urls = deferred_script_urls(json.dumps({"rsrcMap": resources, "compMap": components}),
                                    preferred=["AdsPublishEntry"])
        self.assertEqual(urls, [resources[str(i)]["src"] for i in range(4)])

    def test_lowercase_publication_and_selected_generic_transport_are_roots(self):
        resources = {str(i): {"type": "js", "src": f"https://static.xx.fbcdn.net/{i}.js"} for i in range(2)}
        components = {"adsDraftPublishingPublishAction": {"r": ["0"]}, "AsyncRequest": {"r": ["1"]}}
        document = json.dumps({"rsrcMap": resources, "compMap": components})
        self.assertEqual(deferred_script_urls(document), [resources["0"]["src"]])
        self.assertEqual(deferred_script_urls(document, preferred=["AsyncRequest"]),
                         [resources["1"]["src"], resources["0"]["src"]])

    def test_explicit_sender_priority_is_kept_in_requested_order(self):
        resources = {str(i): {"type": "js", "src": f"https://static.xx.fbcdn.net/{i}.js"} for i in range(3)}
        components = {name: {"r": [str(i)]} for i, name in enumerate(
            ("AdsGraphAPI", "AdsDraftPublishDataManager", "AdsPublishUI"))}
        urls = deferred_script_urls(json.dumps({"rsrcMap": resources, "compMap": components}),
            preferred=["AdsDraftPublishDataManager", "AdsGraphAPI", "AdsDraftPublishDataManager"])
        self.assertEqual(urls, [resources[str(i)]["src"] for i in (1, 0, 2)])

    def test_exact_selected_literal_transport_helper_is_retained_without_execution(self):
        source = '__d("AsyncRequest",[],(function(a,b,c,d,e,f){f.send=function(){throw "never run"};}),98);'
        self.assertEqual(list(module_records(source)), [])
        row, = module_records(source, selected=["AsyncRequest"])
        self.assertEqual(module_source(compact_record(row)), source)

    def test_literal_dependencies_and_compileable_wrapper_are_preserved(self):
        source = '__d("AdsPublish",["cr:20373","AdsGraphAPI"],function(a,b,c,d,e,f){f.publish=c("AdsGraphAPI");},98);'
        row, = module_records(source)
        self.assertEqual(row["dependencies"], ["cr:20373", "AdsGraphAPI"])
        self.assertEqual(row["module_source"], source)
        compact = compact_record(row)
        self.assertNotIn("module_source", compact)
        self.assertEqual(module_source(compact), source)
        self.assertEqual(module_source(row), source)
        wrapped = source.replace("function(a,b,c,d,e,f){f.publish=c(\"AdsGraphAPI\");}", "(function(a,b,c,d,e,f){f.publish=c(\"AdsGraphAPI\");})")
        self.assertEqual(next(module_records(wrapped))["module_source"], wrapped)
        self.assertEqual(module_source(compact_record(next(module_records(wrapped)))), wrapped)
        self.assertFalse(list(module_records(source.replace('["cr:20373","AdsGraphAPI"]', '[dynamic]'))))

    def test_saved_export_is_exact_bounded_and_never_includes_inline_auth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "maintenance"; root.mkdir()
            rows = [{"name": "AdsPublish", "source": "function(){return 1;}", "source_kind": "public_cdn"},
                    {"name": "AdsOther", "source": "other"},
                    {"name": "AdsInline", "source": "function(){return access_token;}"}]
            (root / "ads-public-modules-candidate.json").write_text(json.dumps({"modules": rows}))
            with patch.dict(os.environ, {"REMASK_DATA_DIR": temporary,
                                          "REMASK_LAUNCH_HTTP_AUDIT_MODULES": "AdsPublish,AdsInline,../bad"}), \
                 patch("app.contract_maintenance.launch_http_audit.log") as log:
                inspect_saved_sources()
            evidence = [call.args[1] for call in log.info.call_args_list if "evidence=" in call.args[0]]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(json.loads(evidence[0])["name"], "AdsPublish")
            report = [call.args[1] for call in log.info.call_args_list if "selection=" in call.args[0]]
            self.assertEqual(len(report), 1)
            self.assertTrue(next(row for row in json.loads(report[0]) if row["name"] == "AdsPublish")["present"])

    def test_default_saved_inspection_exports_sender_and_transport_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/"maintenance"; root.mkdir()
            rows = [{"name": name, "source": "function(){return 1;}", "source_kind": "public_cdn"}
                    for name in ("AsyncRequest", "AdsPromotablePageListLoaderGAPI", "AdsDraftPublishDataManager", "AdsNoise")]
            (root/"ads-public-modules-candidate.json").write_text(json.dumps({"modules": rows}))
            with patch.dict(os.environ, {"REMASK_DATA_DIR": temporary,
                "REMASK_LAUNCH_HTTP_AUDIT_MODULES": ""}), \
                    patch("app.contract_maintenance.launch_http_audit.log") as log:
                inspect_saved_sources()
            exported = [json.loads(call.args[1])["name"] for call in log.info.call_args_list if "evidence=" in call.args[0]]
            self.assertEqual(exported, ["AdsDraftPublishDataManager", "AsyncRequest", "AdsPromotablePageListLoaderGAPI"])


class AdsSourceTransportTests(unittest.IsolatedAsyncioTestCase):
    def document(self, count):
        urls = [f"https://static.xx.fbcdn.net/{i}.js" for i in range(count)]
        return json.dumps({"rsrcMap": {str(i): {"type": "js", "src": url} for i, url in enumerate(urls)},
            "compMap": {"AdsDraftPublishDataManager": {"r": [str(i) for i in range(count)]}}}), urls

    async def test_default_sender_capture_reaches_400_observed_files_with_three_workers(self):
        document, urls = self.document(401)
        calls, active, peak = [], 0, 0
        async def fetch(url, **kwargs):
            nonlocal active, peak
            if url not in urls:
                return 200, document, url
            calls.append(url)
            active += 1; peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                name = "AsyncRequest" if url == urls[399] else "AdsFixture" + str(urls.index(url))
                return 200, f'__d("{name}",[],function(a,b,c,d,e,f){{f.x=1;}});', url
            finally:
                active -= 1
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
            "REMASK_DATA_DIR": temporary, "REMASK_LAUNCH_HTTP_AUDIT_MODULES": ""}):
            web = SimpleNamespace(fetch_text=AsyncMock(side_effect=fetch), graphql=AsyncMock())
            result = await collect_sources(web, account="123456789", business="987654321")
            self.assertEqual(result["scripts_requested"], 400)
            self.assertEqual(result["modules"], 400)
            self.assertEqual(set(calls), set(urls[:400]))
            self.assertEqual(peak, 3)
            saved = json.loads((Path(temporary)/"maintenance"/"ads-public-modules-candidate.json").read_text())
            self.assertIn("AsyncRequest", {row["name"] for row in saved["modules"]})
            self.assertEqual(saved["capture"]["scripts_requested"], 400)
            web.graphql.assert_not_awaited()

    async def test_concurrent_byte_reservations_and_failed_reads_cannot_refill_budget(self):
        document, urls = self.document(10)
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary, \
                    patch.dict(os.environ, {"REMASK_DATA_DIR": temporary}), \
                    patch("app.contract_maintenance.launch_http_audit.SCRIPT_BYTE_LIMIT", 100), \
                    patch("app.contract_maintenance.launch_http_audit.CAPTURE_BYTE_LIMIT", 250):
                requested = []
                async def fetch(url, **kwargs):
                    if url not in urls:
                        return 200, document, url
                    requested.append(kwargs["max_bytes"])
                    await asyncio.sleep(0)
                    if fail:
                        raise RemoteRequestError("partial response lost")
                    body = '__d("AdsFixture",[],function(a,b,c,d,e,f){f.x=1;});'
                    return 200, body.ljust(kwargs["max_bytes"]), url
                web = SimpleNamespace(fetch_text=AsyncMock(side_effect=fetch), graphql=AsyncMock())
                result = await collect_sources(web, account="123456789", business="987654321")
                self.assertEqual(sorted(requested), [50, 100, 100])
                self.assertEqual(result["charged_bytes"], 250)
                self.assertEqual(result["scripts_requested"], 3)
                self.assertEqual(result["scripts_failed"], 3 if fail else 0)
                web.graphql.assert_not_awaited()

    async def test_proxy_or_auth_failure_cancels_other_reads_and_preserves_saved_evidence(self):
        document, urls = self.document(400)
        proxy_failure = RemoteRequestError("proxy route unavailable")
        proxy_failure.__cause__ = httpx.ProxyError("407 fixture-secret")
        for failure in (proxy_failure, AuthenticationError("login gate")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary, \
                    patch.dict(os.environ, {"REMASK_DATA_DIR": temporary}):
                target = Path(temporary)/"maintenance"/"ads-public-modules-candidate.json"
                target.parent.mkdir(); target.write_text('{"modules":[{"name":"AdsPrevious"}]}')
                previous = target.read_bytes()
                started, cancelled = [], []
                async def fetch(url, **kwargs):
                    if url not in urls:
                        return 200, document, url
                    started.append(url)
                    if url == urls[0]:
                        await asyncio.sleep(0)
                        raise failure
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.append(url)
                web = SimpleNamespace(fetch_text=AsyncMock(side_effect=fetch), graphql=AsyncMock())
                with self.assertRaises(type(failure)) as caught:
                    await collect_sources(web, account="123456789", business="987654321")
                self.assertIs(caught.exception, failure)
                self.assertLessEqual(len(started), 3)
                self.assertEqual(set(cancelled), set(started) - {urls[0]})
                self.assertEqual(target.read_bytes(), previous)
                self.assertFalse(target.with_suffix(".tmp").exists())
                web.graphql.assert_not_awaited()

    async def test_collection_only_gets_observed_public_urls_and_rejects_foreign_redirect(self):
        source = '__d("AdsPublish",[],function(a,b,c,d,e,f){f.x=1;},98);'
        document = '<script src="https://static.xx.fbcdn.net/a.js"></script><script src="https://evil.test/b.js"></script>'
        for final, expected in (("https://static.xx.fbcdn.net/a.js", 1), ("https://evil.test/a.js", 0)):
            with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"REMASK_DATA_DIR": temporary}):
                root = Path(temporary)/"maintenance"; root.mkdir()
                target = root/"ads-public-modules-candidate.json"
                target.write_text(json.dumps({"scope": "candidate_only_not_installed", "modules": [{"name": "AdsPrevious"}]}))
                web = SimpleNamespace(fetch_text=AsyncMock(side_effect=[
                    (200, document, "https://adsmanager.facebook.com/adsmanager/manage/campaigns"),
                    (200, source, final)]), graphql=AsyncMock())
                result = await collect_sources(web, account="123456789", business="987654321")
                self.assertEqual(result["modules"], expected)
                self.assertEqual(result["scripts"], 1)
                self.assertEqual(web.fetch_text.await_count, 2)
                web.graphql.assert_not_awaited()
                saved = json.loads(target.read_text())
                self.assertEqual(saved["scope"], "candidate_only_not_installed")
                if not expected:
                    self.assertEqual(saved["modules"], [{"name": "AdsPrevious"}])
                else:
                    self.assertEqual(module_source(saved["modules"][0]), source)
                    self.assertEqual(saved["scripts_observed"], ["https://static.xx.fbcdn.net/a.js"])


if __name__ == "__main__":
    unittest.main()
