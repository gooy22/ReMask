"""Instrument streaming bytes, not Content-Length estimates or proxy charges."""
import asyncio
import gzip
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import runner
from app.models import CreateJobRequest
from app.private_http import PrivateHttpClient
from app.private_http_metrics import begin_operation, end_operation, observe_profile_read, measure_phase
from app.runner import WorkerPool
from app.store import JobStore


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error, self.closed = chunks, error, False
        self.consumed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


class PrivateHttpMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, handler=None, profile='fixture', proxy='http://127.0.0.1:1'):
        client = PrivateHttpClient(profile_name=profile, cookies={'c_user':'fixture-secret'},
            proxy=proxy, user_agent='fixture-UA', timeout_seconds=2, pool_size=2)
        self.addAsyncCleanup(client.close)
        if handler is not None:
            hooks = client._client.event_hooks
            await client._client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                event_hooks=hooks, trust_env=False)
        return client

    async def test_operation_phase_metrics_are_scoped_allowlisted_and_sanitized(self):
        with self.assertLogs('remask_worker', level='INFO') as captured:
            metric, token = begin_operation('fixture', 'sync_profile')
            try:
                with measure_phase('resolver'):
                    await asyncio.sleep(0.001)
                with measure_phase('account_proof'):
                    await asyncio.sleep(0)
                with measure_phase('resolver'):
                    await asyncio.sleep(0)
                with measure_phase('SECRET-cookie-or-url'):
                    await asyncio.sleep(0)
            finally:
                end_operation(metric, token, outcome='success')
        record = json.loads(next(line.split('private_http_operation ', 1)[1]
            for line in captured.output if 'private_http_operation ' in line))
        self.assertEqual(record['phase_count'], {'account_proof': 1, 'resolver': 2})
        self.assertGreater(record['phase_ms']['resolver'], 0)
        self.assertGreaterEqual(record['phase_ms']['account_proof'], 0)
        self.assertRegex(record['trace_id'], r'^[0-9a-f]{12}
        payload = b'compressible text ' * 1024
        encoded = gzip.compress(payload)
        requests = []

        async def origin(reader, writer):
            try:
                requests.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: ' +
                    str(len(encoded)).encode() + b'\r\nConnection: close\r\n\r\n' + encoded)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        client = await self.client(proxy='http://127.0.0.1:' + str(server.sockets[0].getsockname()[1]))
        metric, token = begin_operation('fixture', 'sync_profile')
        try:
            async with client.get('http://fixture.test/?private=secret') as response:
                self.assertEqual(await response.content.read(), payload)
        finally:
            end_operation(metric, token, outcome='success')
        result = metric.snapshot()
        self.assertEqual(result['request_attempts'], 1)
        self.assertEqual(result['responses'], 1)
        self.assertEqual(result['encoded_response_body_bytes'], len(encoded))
        self.assertEqual(result['decoded_body_bytes_delivered'], len(payload))
        self.assertLess(result['encoded_response_body_bytes'], result['decoded_body_bytes_delivered'])
        self.assertEqual(len(requests), 1)
        self.assertFalse(result['request_body_bytes_measured'])
        self.assertNotIn('wire_bytes', result)

    async def test_partial_reads_count_consumed_chunks_without_draining_pending_body(self):
        stream = Chunks([b'first', b'second'])
        client = await self.client(lambda request: httpx.Response(200, stream=stream))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/') as response:
                self.assertEqual(await response.content.read(3), b'fir')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(stream.consumed, 1)
        self.assertTrue(stream.closed)
        self.assertEqual(metric.encoded_response_body_bytes, 5)
        self.assertEqual(metric.decoded_body_bytes_delivered, 3)

    async def test_preloaded_mock_content_is_not_estimated_as_encoded_network_bytes(self):
        client = await self.client(lambda request: httpx.Response(200, content=b'preloaded'))
        metric, token = begin_operation('fixture', 'provisioning')
        try:
            async with client.get('https://www.facebook.com/') as response:
                self.assertEqual(await response.text(), 'preloaded')
        finally:
            end_operation(metric, token, outcome='success')
        # A preloaded MockTransport response has no raw stream download. Do
        # not invent byte counts from decompressed .content or Content-Length.
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(metric.decoded_body_bytes_delivered, 9)

    async def test_measurement_or_logging_failure_cannot_override_response_or_handler_result(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'ok'])))
        metric, token = begin_operation('fixture', 'provision')
        try:
            with patch('app.private_http_metrics.RequestTraffic.finish', side_effect=RuntimeError('SECRET metric')):
                async with client.get('https://www.facebook.com/') as response:
                    self.assertEqual(await response.text(), 'ok')
        finally:
            with patch('app.private_http_metrics.log.info', side_effect=RuntimeError('SECRET log')):
                end_operation(metric, token, outcome='success')
        self.assertEqual(metric.operation, 'provision')
        self.assertEqual(metric.measurement_errors, 1)
        self.assertFalse(metric.snapshot()['complete'])

    async def test_response_reference_budget_marks_incomplete_without_limiting_transport(self):
        def handler(request):
            if request.url.path != '/end':
                return httpx.Response(302, headers={'location':'/end'}, stream=Chunks([b'first']))
            return httpx.Response(200, stream=Chunks([b'last']))
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with patch('app.private_http_metrics.MAX_RESPONSE_REFERENCES', 1):
                async with client.get('https://www.facebook.com/start', allow_redirects=True) as response:
                    self.assertEqual(await response.text(), 'last')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 2)
        self.assertEqual(metric.untracked_responses, 1)
        self.assertFalse(metric.snapshot()['complete'])
        self.assertEqual(metric.encoded_response_body_bytes, 5)

    async def test_multiple_bounded_reads_count_each_delivered_byte_once(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'first', b'second'])))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/') as response:
                for size, wanted in ((3,b'fir'), (4,b'stse'), (-1,b'cond'), (-1,b'')):
                    self.assertEqual(await response.content.read(size), wanted)
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.encoded_response_body_bytes, 11)
        self.assertEqual(metric.decoded_body_bytes_delivered, 11)

    async def test_followed_redirect_includes_compressed_redirect_body_once(self):
        encoded = gzip.compress(b'redirect content')
        def handler(request):
            if request.url.path == '/start':
                return httpx.Response(302, headers={'location':'/end?session=secret', 'content-encoding':'gzip'},
                    stream=Chunks([encoded]))
            return httpx.Response(200, stream=Chunks([b'final']))
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/start', allow_redirects=True) as response:
                self.assertEqual(await response.text(), 'final')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 2)
        self.assertEqual(metric.redirects, 1)
        self.assertEqual(metric.encoded_response_body_bytes, len(encoded) + 5)
        self.assertEqual(metric.decoded_redirect_body_bytes, len(b'redirect content'))
        self.assertEqual(metric.decoded_body_bytes_delivered, 5)
        self.assertEqual(metric.status_classes, {'2xx':1,'3xx':1})

    async def test_redirect_failure_retains_first_response_but_does_not_retry(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(302, headers={'location':'/end?token=SECRET'}, stream=Chunks([b'abc']))
            raise httpx.ReadTimeout('SECRET credentials')
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(httpx.ReadTimeout):
                async with client.get('https://www.facebook.com/start', allow_redirects=True):
                    self.fail('no final response')
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(calls, ['/start','/end'])
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 3)
        self.assertEqual(metric.errors, {'timeout':1})

    async def test_partial_failed_body_retains_encoded_bytes_not_undelivered_decoded_buffer(self):
        client = await self.client(lambda request: httpx.Response(200,
            stream=Chunks([b'first'], error=httpx.ReadError('SECRET'))))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(httpx.ReadError):
                async with client.get('https://www.facebook.com/') as response:
                    await response.content.read()
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(metric.request_attempts, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 5)
        self.assertEqual(metric.decoded_body_bytes_delivered, 0)
        self.assertEqual(metric.errors, {'ReadError':1})

    async def test_whole_request_body_deadline_is_recorded_without_retry(self):
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.Event().wait()
                yield b'unreachable'
        client = await self.client(lambda request: httpx.Response(200, stream=Slow()))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(TimeoutError):
                async with client.get('https://www.facebook.com/', request_timeout_seconds=.01) as response:
                    await response.text()
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(metric.request_attempts, 1)
        self.assertEqual(metric.responses, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(metric.errors, {'timeout':1})

    async def test_status_rate_limit_and_safe_logs_do_not_capture_payload_secrets(self):
        client = await self.client(lambda request: httpx.Response(429, stream=Chunks([b'SECRET response'])))
        with self.assertLogs('remask_worker', level='INFO') as captured:
            metric, token = begin_operation('fixture', 'arbitrary SECRET action')
            try:
                async with client.post('https://www.facebook.com/private-path?token=SECRET',
                        data={'password':'SECRET request'}) as response:
                    await response.text()
            finally:
                end_operation(metric, token, outcome='arbitrary SECRET outcome')
        summaries = [line for line in captured.output if 'private_http_operation ' in line]
        self.assertEqual(len(summaries), 1)
        self.assertNotIn('SECRET', str(captured.output))
        self.assertNotIn('private-path', str(captured.output))
        self.assertNotIn('fixture-secret', str(captured.output))
        report = json.loads(summaries[0].split('private_http_operation ', 1)[1])
        self.assertEqual(report['operation'], 'other')
        self.assertEqual(report['outcome'], 'other')
        self.assertEqual(report['rate_limit_responses'], 1)
        self.assertEqual(report['status_classes'], {'4xx':1})

    async def test_concurrent_profile_contexts_and_unscoped_reads_do_not_mix(self):
        entered, release = asyncio.Event(), asyncio.Event()
        a = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'aaaaa'])), profile='A')
        b = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'bbb'])), profile='B')
        async def first():
            metric, token = begin_operation('A', 'sync_profile')
            try:
                entered.set()
                await release.wait()
                async with a.get('https://www.facebook.com/') as response:
                    await response.text()
                # An unexpected cross-profile client must not be attributed
                # to A. Its transport authorization rules remain unchanged.
                async with b.get('https://www.facebook.com/') as response:
                    await response.text()
                return metric
            finally:
                end_operation(metric, token, outcome='success')
        async def second():
            await entered.wait()
            metric, token = begin_operation('B', 'private_launch')
            try:
                for _ in range(2):
                    async with b.get('https://www.facebook.com/') as response:
                        await response.text()
                release.set()
                return metric
            finally:
                end_operation(metric, token, outcome='success')
        left, right = await asyncio.gather(first(), second())
        self.assertEqual((left.request_attempts,left.encoded_response_body_bytes), (1,5))
        self.assertEqual((right.request_attempts,right.encoded_response_body_bytes), (2,6))
        self.assertEqual(left.profile_scope_mismatches, 1)
        self.assertFalse(left.snapshot()['complete'])
        self.assertTrue(right.snapshot()['complete'])
        async with a.get('https://www.facebook.com/') as response:
            await response.text()
        self.assertEqual((left.request_attempts,right.request_attempts), (1,2))

    async def test_detached_request_cannot_mutate_finished_operation_or_next_profile(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await release.wait()
                yield b'late body'
        client = await self.client(lambda request: httpx.Response(200, stream=Slow()))
        metric, token = begin_operation('fixture', 'private_launch')
        async def fetch():
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
        task = asyncio.create_task(fetch())
        await entered.wait()
        end_operation(metric, token, outcome='cancelled')
        self.assertFalse(metric.snapshot()['complete'])
        following, following_token = begin_operation('fixture', 'private_delivery')
        try:
            release.set()
            await task
        finally:
            end_operation(following, following_token, outcome='success')
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(following.request_scopes, 0)

    async def test_runner_emits_one_scope_for_each_task_and_resets_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / 'jobs.sqlite')
            await store.init()
            pool = WorkerPool(store, concurrency=1)
            await pool.provisioning_state.init()
            pool._restore_workspace_bindings = AsyncMock()
            pool.resolver.resolve = AsyncMock(side_effect=lambda profile: SimpleNamespace(profile_id=profile))
            client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'ok'])))
            async def handler(session, payload):
                async with client.get('https://www.facebook.com/') as response:
                    await response.text()
                if payload.get('fail'):
                    raise RuntimeError('SECRET task failure')
                return {'ok':True}
            pool.registry.register('fixture', handler)
            class Session:
                def __init__(self, context):self.context = context
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
            job, _ = await store.create_job(CreateJobRequest(profiles=[{'profile_id':'fixture', 'tasks':[
                {'action':'fixture','payload':{'fail':True}}]}, {'profile_id':'fixture','tasks':[
                {'action':'fixture'}]}]))
            with patch.object(runner,'ProfileSession',Session), self.assertLogs('remask_worker',level='INFO') as captured:
                for item in await store.queued_item_ids(job):
                    await pool._run_item(item)
            reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                       if 'private_http_operation ' in line]
            self.assertEqual([row['outcome'] for row in reports], ['failed','success'])
            self.assertEqual([row['request_attempts'] for row in reports], [1,1])
            self.assertEqual([row['encoded_response_body_bytes'] for row in reports], [2,2])
            self.assertTrue(all(row['complete'] for row in reports))
            self.assertNotIn('SECRET', str(captured.output))

    async def run_direct_inventory(self, client, *, resolver_error=None):
        import main as api
        from app.session import ProfileContext
        context = ProfileContext('fixture', {'c_user':'999999999999'}, 'http://fixture:8080', 'fixture')
        state = SimpleNamespace(**{name:AsyncMock(return_value=default) for name,default in (
            ('confirmed_ad_account_bindings_for_profile',[]), ('confirmed_business_page_bindings_for_profile',[]),
            ('latest_profile_fan_pages',[]), ('latest_profile_fan_page_batch',{}), ('latest_profile_entities',{}))})
        resolver = AsyncMock(return_value=context, side_effect=resolver_error)
        pool = SimpleNamespace(resolver=SimpleNamespace(resolve=resolver), provisioning_state=state)
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=SimpleNamespace()))
        lease = AsyncMock()
        lease.__aenter__.return_value = session
        async def snapshot(*args, **kwargs):
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
            return {'ready':False, 'businesses':[], 'diagnostics':[]}
        with patch.object(api,'pool',pool), patch.object(api,'ProfileSession',return_value=lease), \
                patch.object(api,'private_inventory_snapshot',side_effect=snapshot):
            return await api.profile_live_inventory('fixture', business_ids='111111111111',
                ad_account_hints='111111111111:222222222222')

    async def test_direct_inventory_scope_preserves_fastapi_signature_and_marks_partial(self):
        import main as api
        self.assertEqual(inspect.signature(api.profile_live_inventory),
            inspect.signature(api.profile_live_inventory.__wrapped__))
        route = next(route for route in api.app.routes
                     if getattr(route,'path',None) == '/api/v1/profiles/{profile_id}/live-inventory')
        self.assertEqual([field.name for field in route.dependant.path_params], ['profile_id'])
        self.assertEqual([field.name for field in route.dependant.query_params],
            ['business_ids','ad_account_hints','page_hints'])
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'sync'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            result = await self.run_direct_inventory(client)
        self.assertFalse(result['live_ready'])
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['operation'], 'sync_profile')
        self.assertEqual(reports[0]['outcome'], 'partial')
        self.assertEqual(reports[0]['encoded_response_body_bytes'],4)
        self.assertEqual(reports[0]['transport_coverage'],'PrivateHttpClient_only')

    async def test_direct_inventory_does_not_double_count_existing_profile_task_scope(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'kit'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            metric, token = begin_operation('fixture','provisioning')
            try:
                result = await self.run_direct_inventory(client)
                self.assertFalse(result['live_ready'])
            finally:
                end_operation(metric,token,outcome='success')
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['operation'],'provisioning')
        self.assertEqual(metric.request_attempts,1)
        self.assertEqual(metric.encoded_response_body_bytes,3)

    async def test_direct_inventory_exception_restores_context_without_leaking_resolver_error(self):
        from fastapi import HTTPException
        from app.session import ProfileContextError
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'outside'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            with self.assertRaises(HTTPException):
                await self.run_direct_inventory(client, resolver_error=ProfileContextError('SECRET resolver'))
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['outcome'],'failed')
        self.assertEqual(reports[0]['request_scopes'],0)
        self.assertNotIn('SECRET',str(captured.output))



    async def test_direct_reference_metrics_separate_sources_without_exposing_query(self):
        import main as api
        for handler in (api.private_advertising_resources, api.private_template_reference):
            self.assertEqual(inspect.signature(handler), inspect.signature(handler.__wrapped__))
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'five!'])))
        @observe_profile_read
        async def read(*, profile_id, resource=None, query='SECRET query'):
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
            return {'ok': True}
        with self.assertLogs('remask_worker', level='INFO') as captured:
            await read(profile_id='fixture', resource='interests')
            await read(profile_id='fixture', resource='not-a-registered-resource')
            await read(profile_id='fixture')
        reports = [json.loads(line.split('private_http_operation ',1)[1])
                   for line in captured.output if 'private_http_operation ' in line]
        self.assertEqual([row['operation'] for row in reports],
                         ['resource_interests', 'resource_other', 'template_reference'])
        self.assertEqual([row['encoded_response_body_bytes'] for row in reports], [5,5,5])
        self.assertTrue(all(row['complete'] for row in reports))
        self.assertNotIn('SECRET query',str(captured.output))


if __name__ == '__main__':
    unittest.main()
)
        self.assertNotIn('SECRET', str(captured.output))
        with measure_phase('resolver'):
            await asyncio.sleep(0)
        self.assertEqual(metric.phase_count['resolver'], 2)

    async def test_timing_cannot_modify_a_finished_or_unrelated_operation(self):
        first, token = begin_operation('one', 'sync_profile')
        try:
            with measure_phase('resolver'):
                second, second_token = begin_operation('two', 'sync_profile')
                try:
                    with measure_phase('reference_read'):
                        await asyncio.sleep(0)
                finally:
                    end_operation(second, second_token, outcome='success')
        finally:
            end_operation(first, token, outcome='success')
        self.assertNotIn('resolver', first.phase_count)
        self.assertEqual(second.phase_count['reference_read'], 1)

    async def test_real_gzip_stream_counts_encoded_body_separately_from_decoded_delivery(self):
        payload = b'compressible text ' * 1024
        encoded = gzip.compress(payload)
        requests = []

        async def origin(reader, writer):
            try:
                requests.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: ' +
                    str(len(encoded)).encode() + b'\r\nConnection: close\r\n\r\n' + encoded)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        client = await self.client(proxy='http://127.0.0.1:' + str(server.sockets[0].getsockname()[1]))
        metric, token = begin_operation('fixture', 'sync_profile')
        try:
            async with client.get('http://fixture.test/?private=secret') as response:
                self.assertEqual(await response.content.read(), payload)
        finally:
            end_operation(metric, token, outcome='success')
        result = metric.snapshot()
        self.assertEqual(result['request_attempts'], 1)
        self.assertEqual(result['responses'], 1)
        self.assertEqual(result['encoded_response_body_bytes'], len(encoded))
        self.assertEqual(result['decoded_body_bytes_delivered'], len(payload))
        self.assertLess(result['encoded_response_body_bytes'], result['decoded_body_bytes_delivered'])
        self.assertEqual(len(requests), 1)
        self.assertFalse(result['request_body_bytes_measured'])
        self.assertNotIn('wire_bytes', result)

    async def test_partial_reads_count_consumed_chunks_without_draining_pending_body(self):
        stream = Chunks([b'first', b'second'])
        client = await self.client(lambda request: httpx.Response(200, stream=stream))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/') as response:
                self.assertEqual(await response.content.read(3), b'fir')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(stream.consumed, 1)
        self.assertTrue(stream.closed)
        self.assertEqual(metric.encoded_response_body_bytes, 5)
        self.assertEqual(metric.decoded_body_bytes_delivered, 3)

    async def test_preloaded_mock_content_is_not_estimated_as_encoded_network_bytes(self):
        client = await self.client(lambda request: httpx.Response(200, content=b'preloaded'))
        metric, token = begin_operation('fixture', 'provisioning')
        try:
            async with client.get('https://www.facebook.com/') as response:
                self.assertEqual(await response.text(), 'preloaded')
        finally:
            end_operation(metric, token, outcome='success')
        # A preloaded MockTransport response has no raw stream download. Do
        # not invent byte counts from decompressed .content or Content-Length.
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(metric.decoded_body_bytes_delivered, 9)

    async def test_measurement_or_logging_failure_cannot_override_response_or_handler_result(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'ok'])))
        metric, token = begin_operation('fixture', 'provision')
        try:
            with patch('app.private_http_metrics.RequestTraffic.finish', side_effect=RuntimeError('SECRET metric')):
                async with client.get('https://www.facebook.com/') as response:
                    self.assertEqual(await response.text(), 'ok')
        finally:
            with patch('app.private_http_metrics.log.info', side_effect=RuntimeError('SECRET log')):
                end_operation(metric, token, outcome='success')
        self.assertEqual(metric.operation, 'provision')
        self.assertEqual(metric.measurement_errors, 1)
        self.assertFalse(metric.snapshot()['complete'])

    async def test_response_reference_budget_marks_incomplete_without_limiting_transport(self):
        def handler(request):
            if request.url.path != '/end':
                return httpx.Response(302, headers={'location':'/end'}, stream=Chunks([b'first']))
            return httpx.Response(200, stream=Chunks([b'last']))
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with patch('app.private_http_metrics.MAX_RESPONSE_REFERENCES', 1):
                async with client.get('https://www.facebook.com/start', allow_redirects=True) as response:
                    self.assertEqual(await response.text(), 'last')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 2)
        self.assertEqual(metric.untracked_responses, 1)
        self.assertFalse(metric.snapshot()['complete'])
        self.assertEqual(metric.encoded_response_body_bytes, 5)

    async def test_multiple_bounded_reads_count_each_delivered_byte_once(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'first', b'second'])))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/') as response:
                for size, wanted in ((3,b'fir'), (4,b'stse'), (-1,b'cond'), (-1,b'')):
                    self.assertEqual(await response.content.read(size), wanted)
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.encoded_response_body_bytes, 11)
        self.assertEqual(metric.decoded_body_bytes_delivered, 11)

    async def test_followed_redirect_includes_compressed_redirect_body_once(self):
        encoded = gzip.compress(b'redirect content')
        def handler(request):
            if request.url.path == '/start':
                return httpx.Response(302, headers={'location':'/end?session=secret', 'content-encoding':'gzip'},
                    stream=Chunks([encoded]))
            return httpx.Response(200, stream=Chunks([b'final']))
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            async with client.get('https://www.facebook.com/start', allow_redirects=True) as response:
                self.assertEqual(await response.text(), 'final')
        finally:
            end_operation(metric, token, outcome='success')
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 2)
        self.assertEqual(metric.redirects, 1)
        self.assertEqual(metric.encoded_response_body_bytes, len(encoded) + 5)
        self.assertEqual(metric.decoded_redirect_body_bytes, len(b'redirect content'))
        self.assertEqual(metric.decoded_body_bytes_delivered, 5)
        self.assertEqual(metric.status_classes, {'2xx':1,'3xx':1})

    async def test_redirect_failure_retains_first_response_but_does_not_retry(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(302, headers={'location':'/end?token=SECRET'}, stream=Chunks([b'abc']))
            raise httpx.ReadTimeout('SECRET credentials')
        client = await self.client(handler)
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(httpx.ReadTimeout):
                async with client.get('https://www.facebook.com/start', allow_redirects=True):
                    self.fail('no final response')
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(calls, ['/start','/end'])
        self.assertEqual(metric.request_attempts, 2)
        self.assertEqual(metric.responses, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 3)
        self.assertEqual(metric.errors, {'timeout':1})

    async def test_partial_failed_body_retains_encoded_bytes_not_undelivered_decoded_buffer(self):
        client = await self.client(lambda request: httpx.Response(200,
            stream=Chunks([b'first'], error=httpx.ReadError('SECRET'))))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(httpx.ReadError):
                async with client.get('https://www.facebook.com/') as response:
                    await response.content.read()
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(metric.request_attempts, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 5)
        self.assertEqual(metric.decoded_body_bytes_delivered, 0)
        self.assertEqual(metric.errors, {'ReadError':1})

    async def test_whole_request_body_deadline_is_recorded_without_retry(self):
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.Event().wait()
                yield b'unreachable'
        client = await self.client(lambda request: httpx.Response(200, stream=Slow()))
        metric, token = begin_operation('fixture', 'private_launch')
        try:
            with self.assertRaises(TimeoutError):
                async with client.get('https://www.facebook.com/', request_timeout_seconds=.01) as response:
                    await response.text()
        finally:
            end_operation(metric, token, outcome='failed')
        self.assertEqual(metric.request_attempts, 1)
        self.assertEqual(metric.responses, 1)
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(metric.errors, {'timeout':1})

    async def test_status_rate_limit_and_safe_logs_do_not_capture_payload_secrets(self):
        client = await self.client(lambda request: httpx.Response(429, stream=Chunks([b'SECRET response'])))
        with self.assertLogs('remask_worker', level='INFO') as captured:
            metric, token = begin_operation('fixture', 'arbitrary SECRET action')
            try:
                async with client.post('https://www.facebook.com/private-path?token=SECRET',
                        data={'password':'SECRET request'}) as response:
                    await response.text()
            finally:
                end_operation(metric, token, outcome='arbitrary SECRET outcome')
        summaries = [line for line in captured.output if 'private_http_operation ' in line]
        self.assertEqual(len(summaries), 1)
        self.assertNotIn('SECRET', str(captured.output))
        self.assertNotIn('private-path', str(captured.output))
        self.assertNotIn('fixture-secret', str(captured.output))
        report = json.loads(summaries[0].split('private_http_operation ', 1)[1])
        self.assertEqual(report['operation'], 'other')
        self.assertEqual(report['outcome'], 'other')
        self.assertEqual(report['rate_limit_responses'], 1)
        self.assertEqual(report['status_classes'], {'4xx':1})

    async def test_concurrent_profile_contexts_and_unscoped_reads_do_not_mix(self):
        entered, release = asyncio.Event(), asyncio.Event()
        a = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'aaaaa'])), profile='A')
        b = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'bbb'])), profile='B')
        async def first():
            metric, token = begin_operation('A', 'sync_profile')
            try:
                entered.set()
                await release.wait()
                async with a.get('https://www.facebook.com/') as response:
                    await response.text()
                # An unexpected cross-profile client must not be attributed
                # to A. Its transport authorization rules remain unchanged.
                async with b.get('https://www.facebook.com/') as response:
                    await response.text()
                return metric
            finally:
                end_operation(metric, token, outcome='success')
        async def second():
            await entered.wait()
            metric, token = begin_operation('B', 'private_launch')
            try:
                for _ in range(2):
                    async with b.get('https://www.facebook.com/') as response:
                        await response.text()
                release.set()
                return metric
            finally:
                end_operation(metric, token, outcome='success')
        left, right = await asyncio.gather(first(), second())
        self.assertEqual((left.request_attempts,left.encoded_response_body_bytes), (1,5))
        self.assertEqual((right.request_attempts,right.encoded_response_body_bytes), (2,6))
        self.assertEqual(left.profile_scope_mismatches, 1)
        self.assertFalse(left.snapshot()['complete'])
        self.assertTrue(right.snapshot()['complete'])
        async with a.get('https://www.facebook.com/') as response:
            await response.text()
        self.assertEqual((left.request_attempts,right.request_attempts), (1,2))

    async def test_detached_request_cannot_mutate_finished_operation_or_next_profile(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await release.wait()
                yield b'late body'
        client = await self.client(lambda request: httpx.Response(200, stream=Slow()))
        metric, token = begin_operation('fixture', 'private_launch')
        async def fetch():
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
        task = asyncio.create_task(fetch())
        await entered.wait()
        end_operation(metric, token, outcome='cancelled')
        self.assertFalse(metric.snapshot()['complete'])
        following, following_token = begin_operation('fixture', 'private_delivery')
        try:
            release.set()
            await task
        finally:
            end_operation(following, following_token, outcome='success')
        self.assertEqual(metric.encoded_response_body_bytes, 0)
        self.assertEqual(following.request_scopes, 0)

    async def test_runner_emits_one_scope_for_each_task_and_resets_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / 'jobs.sqlite')
            await store.init()
            pool = WorkerPool(store, concurrency=1)
            await pool.provisioning_state.init()
            pool._restore_workspace_bindings = AsyncMock()
            pool.resolver.resolve = AsyncMock(side_effect=lambda profile: SimpleNamespace(profile_id=profile))
            client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'ok'])))
            async def handler(session, payload):
                async with client.get('https://www.facebook.com/') as response:
                    await response.text()
                if payload.get('fail'):
                    raise RuntimeError('SECRET task failure')
                return {'ok':True}
            pool.registry.register('fixture', handler)
            class Session:
                def __init__(self, context):self.context = context
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
            job, _ = await store.create_job(CreateJobRequest(profiles=[{'profile_id':'fixture', 'tasks':[
                {'action':'fixture','payload':{'fail':True}}]}, {'profile_id':'fixture','tasks':[
                {'action':'fixture'}]}]))
            with patch.object(runner,'ProfileSession',Session), self.assertLogs('remask_worker',level='INFO') as captured:
                for item in await store.queued_item_ids(job):
                    await pool._run_item(item)
            reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                       if 'private_http_operation ' in line]
            self.assertEqual([row['outcome'] for row in reports], ['failed','success'])
            self.assertEqual([row['request_attempts'] for row in reports], [1,1])
            self.assertEqual([row['encoded_response_body_bytes'] for row in reports], [2,2])
            self.assertTrue(all(row['complete'] for row in reports))
            self.assertNotIn('SECRET', str(captured.output))

    async def run_direct_inventory(self, client, *, resolver_error=None):
        import main as api
        from app.session import ProfileContext
        context = ProfileContext('fixture', {'c_user':'999999999999'}, 'http://fixture:8080', 'fixture')
        state = SimpleNamespace(**{name:AsyncMock(return_value=default) for name,default in (
            ('confirmed_ad_account_bindings_for_profile',[]), ('confirmed_business_page_bindings_for_profile',[]),
            ('latest_profile_fan_pages',[]), ('latest_profile_fan_page_batch',{}), ('latest_profile_entities',{}))})
        resolver = AsyncMock(return_value=context, side_effect=resolver_error)
        pool = SimpleNamespace(resolver=SimpleNamespace(resolve=resolver), provisioning_state=state)
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=SimpleNamespace()))
        lease = AsyncMock()
        lease.__aenter__.return_value = session
        async def snapshot(*args, **kwargs):
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
            return {'ready':False, 'businesses':[], 'diagnostics':[]}
        with patch.object(api,'pool',pool), patch.object(api,'ProfileSession',return_value=lease), \
                patch.object(api,'private_inventory_snapshot',side_effect=snapshot):
            return await api.profile_live_inventory('fixture', business_ids='111111111111',
                ad_account_hints='111111111111:222222222222')

    async def test_direct_inventory_scope_preserves_fastapi_signature_and_marks_partial(self):
        import main as api
        self.assertEqual(inspect.signature(api.profile_live_inventory),
            inspect.signature(api.profile_live_inventory.__wrapped__))
        route = next(route for route in api.app.routes
                     if getattr(route,'path',None) == '/api/v1/profiles/{profile_id}/live-inventory')
        self.assertEqual([field.name for field in route.dependant.path_params], ['profile_id'])
        self.assertEqual([field.name for field in route.dependant.query_params],
            ['business_ids','ad_account_hints','page_hints'])
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'sync'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            result = await self.run_direct_inventory(client)
        self.assertFalse(result['live_ready'])
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['operation'], 'sync_profile')
        self.assertEqual(reports[0]['outcome'], 'partial')
        self.assertEqual(reports[0]['encoded_response_body_bytes'],4)
        self.assertEqual(reports[0]['transport_coverage'],'PrivateHttpClient_only')

    async def test_direct_inventory_does_not_double_count_existing_profile_task_scope(self):
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'kit'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            metric, token = begin_operation('fixture','provisioning')
            try:
                result = await self.run_direct_inventory(client)
                self.assertFalse(result['live_ready'])
            finally:
                end_operation(metric,token,outcome='success')
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['operation'],'provisioning')
        self.assertEqual(metric.request_attempts,1)
        self.assertEqual(metric.encoded_response_body_bytes,3)

    async def test_direct_inventory_exception_restores_context_without_leaking_resolver_error(self):
        from fastapi import HTTPException
        from app.session import ProfileContextError
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'outside'])))
        with self.assertLogs('remask_worker',level='INFO') as captured:
            with self.assertRaises(HTTPException):
                await self.run_direct_inventory(client, resolver_error=ProfileContextError('SECRET resolver'))
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
        reports = [json.loads(line.split('private_http_operation ',1)[1]) for line in captured.output
                   if 'private_http_operation ' in line]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['outcome'],'failed')
        self.assertEqual(reports[0]['request_scopes'],0)
        self.assertNotIn('SECRET',str(captured.output))



    async def test_direct_reference_metrics_separate_sources_without_exposing_query(self):
        import main as api
        for handler in (api.private_advertising_resources, api.private_template_reference):
            self.assertEqual(inspect.signature(handler), inspect.signature(handler.__wrapped__))
        client = await self.client(lambda request: httpx.Response(200, stream=Chunks([b'five!'])))
        @observe_profile_read
        async def read(*, profile_id, resource=None, query='SECRET query'):
            async with client.get('https://www.facebook.com/') as response:
                await response.text()
            return {'ok': True}
        with self.assertLogs('remask_worker', level='INFO') as captured:
            await read(profile_id='fixture', resource='interests')
            await read(profile_id='fixture', resource='not-a-registered-resource')
            await read(profile_id='fixture')
        reports = [json.loads(line.split('private_http_operation ',1)[1])
                   for line in captured.output if 'private_http_operation ' in line]
        self.assertEqual([row['operation'] for row in reports],
                         ['resource_interests', 'resource_other', 'template_reference'])
        self.assertEqual([row['encoded_response_body_bytes'] for row in reports], [5,5,5])
        self.assertTrue(all(row['complete'] for row in reports))
        self.assertNotIn('SECRET query',str(captured.output))


if __name__ == '__main__':
    unittest.main()
