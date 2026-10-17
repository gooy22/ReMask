"""A bounded read-only gateway to the owner's exact ReMask hostname.

Agent workers have only an internal Docker network. They cannot access the
internet, the PentAGI control plane, the model, credentials or production data.
"""
import hashlib
import http.client
import json
import re
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

TARGET = 'remask-production-7cbf.up.railway.app'
LIMIT = 80
LOCK = threading.Lock()
RECORDS = []
LAST = 0.0
HEADERS = ('Content-Type', 'Content-Security-Policy', 'X-Frame-Options',
           'X-Content-Type-Options', 'Strict-Transport-Security', 'Referrer-Policy',
           'Permissions-Policy', 'Cache-Control', 'Server', 'X-Powered-By')

def valid_path(path):
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    return (path.startswith('/') and not path.startswith('//') and len(path) < 300
            and not parsed.scheme and not parsed.netloc and not parsed.query
            and not parsed.fragment and not any(ord(c) < 32 for c in decoded)
            and '\\' not in decoded and '..' not in decoded.split('/'))

def sensitive(path, body, content_type):
    # Do not disclose JSON stores or a discovered configuration/backup file.
    plain = unquote(path).lower()
    private_path = bool(re.search(r'(accounts|bundles|credentials|cookies|settings|\.env|\.sqlite|\.bak|\.git)', plain))
    secret_content = bool(re.search(rb'(?:c_user|fb_dtsg|access_token|api_key|password|proxy_password)[\"\x27]?\s*[:=]\s*[\"\x27][^\"\x27]{3,}', body, re.I))
    return bool(body and (private_path or secret_content or
                ('json' in content_type.lower() and plain not in ('/ready.php', '/health'))))

def upstream(method, path):
    conn = http.client.HTTPSConnection(TARGET, 443, timeout=15,
                                      context=ssl.create_default_context())
    try:
        # No caller headers, cookies, auth, request body or redirects are forwarded.
        conn.request(method, path, headers={'User-Agent': 'ReMask-owner-PentAGI-audit/1.0',
                                          'Accept': '*/*', 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        headers = {name: response.getheader(name) for name in HEADERS if response.getheader(name)}
        cookies = [value for name, value in response.getheaders() if name.lower() == 'set-cookie']
        flags = [{key: key in {part.strip().split('=', 1)[0].lower() for part in cookie.split(';')[1:]}
                  for key in ('secure', 'httponly', 'samesite')} for cookie in cookies]
        body = response.read(32768) if method != 'HEAD' else b''
        return response.status, headers, body, flags
    finally:
        conn.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, headers=None):
        self.send_response_only(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def json(self, status, data):
        self.send(status, json.dumps(data).encode(), {'Content-Type': 'application/json'})

    def do_GET(self):
        global LAST
        if self.path == '/_audit/target':
            return self.json(200, {'target': 'https://' + TARGET,
                'transport': 'TLS verified HTTPS upstream through a read-only gateway',
                'allowed_methods': ['GET', 'HEAD', 'OPTIONS'], 'query_parameters': False,
                'max_requests': LIMIT, 'max_requests_per_second': 1,
                'credentials_forwarded': False, 'sensitive_bodies_redacted': True,
                'note': 'Gateway HTTP and redaction are not target vulnerabilities.'})
        if self.path == '/_audit/state':
            with LOCK:
                records = list(RECORDS)
            return self.json(200, {'target': TARGET, 'requests': records})
        if not valid_path(self.path):
            return self.json(400, {'error': 'Out-of-scope request target or query'})
        with LOCK:
            if len(RECORDS) >= LIMIT:
                return self.json(429, {'error': 'Audit request budget exhausted'})
            time.sleep(max(0, LAST + 1 - time.monotonic()))
            LAST = time.monotonic()
            try:
                status, headers, body, flags = upstream(self.command, self.path)
            except (OSError, http.client.HTTPException) as error:
                RECORDS.append({'method': self.command, 'path': self.path,
                                'error_type': type(error).__name__})
                return self.json(502, {'error': 'Origin unavailable', 'type': type(error).__name__})
            redacted = sensitive(self.path, body, headers.get('Content-Type', ''))
            record = {'method': self.command, 'path': self.path, 'status': status,
                      'headers': headers, 'cookie_flags': flags,
                      'body_bytes_observed': len(body), 'body_sha256': hashlib.sha256(body).hexdigest(),
                      'body_redacted': redacted}
            RECORDS.append(record)
            print(json.dumps(record), flush=True)
        headers['X-Audit-Cookie-Flags'] = json.dumps(flags)
        headers['X-Audit-Origin-TLS-Verified'] = 'true'
        if redacted:
            body = b'[AUDIT: response body redacted; use status and metadata as evidence]\n'
            headers['Content-Type'] = 'text/plain'
            headers['X-Audit-Body-Redacted'] = 'true'
        self.send(status, body, headers)

    do_HEAD = do_GET
    do_OPTIONS = do_GET

    def do_POST(self):
        self.json(405, {'error': 'Read-only audit; no request sent to origin'})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_CONNECT = do_POST

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
