"""Bounded HTTP core phase names only; trace payloads are deliberately ignored."""
import time


_PHASES = frozenset({
    'connection.connect_tcp', 'connection.start_tls', 'proxy.start_tls',
    'http11.send_request_headers', 'http11.send_request_body',
    'http11.receive_response_headers', 'http11.receive_response_body',
    'http2.send_connection_init', 'http2.receive_remote_settings',
    'http2.send_request_headers', 'http2.send_request_body',
    'http2.receive_response_headers', 'http2.receive_response_body',
})


class PrivateRequestStages:
    def __init__(self):
        self.started = time.monotonic()
        self.phase, self.state = 'unobserved', 'unobserved'
        self.tcp_connect_observed = self.tls_completed = False
        self.h2_headers_sent = self.h2_body_sent = False
        self.external_trace = False
        self.errors = 0

    async def trace(self, event_name, _info):
        try:
            if not isinstance(event_name, str):
                return
            phase, _, state = event_name.rpartition('.')
            if phase not in _PHASES or state not in {'started', 'complete', 'failed'}:
                return
            self.phase, self.state = phase, state
            if phase == 'connection.connect_tcp' and state == 'started':
                self.tcp_connect_observed = True
            if phase in {'connection.start_tls', 'proxy.start_tls'} and state == 'complete':
                self.tls_completed = True
            if state == 'complete' and phase == 'http2.send_request_headers':
                self.h2_headers_sent = True
            if state == 'complete' and phase == 'http2.send_request_body':
                self.h2_body_sent = True
        except Exception:
            self.errors += 1

    def snapshot(self):
        return {'phase':self.phase, 'state':self.state,
            'tcp_connect_observed':self.tcp_connect_observed, 'tls_completed':self.tls_completed,
            'h2_headers_sent':self.h2_headers_sent, 'h2_body_sent':self.h2_body_sent,
            'external_trace':self.external_trace, 'measurement_errors':self.errors,
            'elapsed_ms':max(0, round((time.monotonic() - self.started) * 1000))}
