import unittest
from app.private_http_stages import PrivateRequestStages


class PrivateRequestStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_sent_body_and_waiting_headers_are_distinguished_from_connect(self):
        phase = PrivateRequestStages()
        for event in ('connection.connect_tcp.started', 'connection.connect_tcp.complete',
                'proxy.start_tls.complete', 'http2.send_request_headers.complete',
                'http2.send_request_body.complete', 'http2.receive_response_headers.started',
                'http2.receive_response_headers.failed', 'http2.response_closed.complete'):
            await phase.trace(event, {'SECRET':'must never be copied'})
        result = phase.snapshot()
        self.assertEqual((result['phase'], result['state']), ('http2.receive_response_headers','failed'))
        self.assertTrue(result['tcp_connect_observed'])
        self.assertTrue(result['tls_completed'])
        self.assertTrue(result['h2_headers_sent'])
        self.assertTrue(result['h2_body_sent'])
        self.assertNotIn('SECRET', str(result))

    async def test_reused_connection_does_not_report_a_new_tcp_or_tls_handshake(self):
        phase = PrivateRequestStages()
        await phase.trace('http2.send_request_headers.complete', {})
        await phase.trace('http2.send_request_body.complete', {})
        await phase.trace('http2.receive_response_headers.started', {})
        self.assertFalse(phase.snapshot()['tcp_connect_observed'])
        self.assertFalse(phase.snapshot()['tls_completed'])
        self.assertTrue(phase.snapshot()['h2_body_sent'])

    async def test_payloads_are_not_inspected_and_unknown_event_names_are_not_retained(self):
        class Payload:
            def __repr__(self): raise AssertionError('payload inspected')
            def __iter__(self): raise AssertionError('payload inspected')
        phase = PrivateRequestStages()
        await phase.trace('connection.connect_tcp.started', Payload())
        for event in (None, 'SECRET.cookie.complete', 'http2.send_request_body.SECRET'):
            await phase.trace(event, Payload())
        result = phase.snapshot()
        self.assertEqual(result['phase'], 'connection.connect_tcp')
        self.assertEqual(result['state'], 'started')
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(result['measurement_errors'], 0)
