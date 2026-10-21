"""Exercise generated PHP bridge over HTTP with an isolated worker stub."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('php'), 'PHP required in deployment CI')
class AdvertisingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        parts = sorted((ROOT / '.deploy/clean-preview-valid').glob('runtime.b64.*'))
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(''.join(p.read_text() for p in parts)))) as archive:
            archive.extractall(self.root, filter='data')
        (self.root / 'settings.php').write_text('<?php define("REMASK_MEDIA_LIBRARY_STORE","file");'
            + 'define("REMASK_MEDIA_LIBRARY_DIR",' + json.dumps(str(self.root / 'media')) + ');'
            + 'define("REMASK_MEDIA_LIBRARY_MAX_BYTES",1048576);')
        (self.root / 'checkpassword.php').write_text('<?php // isolated fixture auth')
        source = (ROOT / 'railway-python-worker-jobs-overlay.php').read_text()
        php = re.search(r"<<<'PHP_CODE'\n(.*?)\nPHP_CODE;", source, re.S).group(1)
        begin = php.index('function rmx_pwj_worker_request(')
        end = php.index('\ntry {\n    $input', begin)
        stub = '''function rmx_pwj_worker_request(string $method, string $path, ?array $payload = null, int $timeoutSeconds = 20): array {
            if ($method === 'GET') return json_decode(getenv('TEST_WORKER_REPLY'), true);
            return ['forwarded'=>$payload];
        }
        '''
        (self.root / 'ajax/pythonWorkerJobs.php').write_text(php[:begin] + stub + php[end:])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/ajax/pythonWorkerJobs.php'
        rows = {step: {'configured': True, 'active': step != 'AD',
                'verification_configured': step != 'CREATIVE', 'verification_code': 'fixture'}
                for step in ('CAMPAIGN', 'ADSET', 'CREATIVE', 'AD')}
        self.process = subprocess.Popen(['php', '-S', f'127.0.0.1:{port}', '-t', str(self.root)],
            env={**os.environ, 'TEST_WORKER_REPLY': json.dumps({'contracts': rows})},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_server)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + '?action=private_launch_contracts', timeout=.2).close()
                break
            except (urllib.error.URLError, TimeoutError):
                if self.process.poll() is not None:
                    self.fail('PHP fixture server failed to start')
                time.sleep(.02)
        else:
            self.fail('PHP fixture server unavailable')

    def stop_server(self):
        self.process.terminate()
        self.process.wait(timeout=5)

    def request(self, payload=None, *, body=None, content_type='application/json'):
        data = body if body is not None else json.dumps(payload).encode()
        request = urllib.request.Request(self.url, data=data, headers={'Content-Type': content_type})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_selected_masked_card_survives_real_php_review_bridge(self):
        payload = {'action': 'private_launch_review', 'profile_id': '7', 'business_id': '111111111',
            'ad_account_id': '222222222', 'page_id': '333333333', 'launch': {'creative': {'message': 'fixture'}},
            'selected_payment_method': {'type': 'Visa', 'last4': '1234'}}
        status, result = self.request(payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['review']['forwarded']['selected_payment_method'], {'type': 'Visa', 'last4': '1234'})
        payload['selected_payment_method']['number'] = 'forbidden-fixture'
        status, result = self.request(payload)
        self.assertEqual(status, 400)
        self.assertEqual(result['error'], 'PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID')

    def test_disabled_and_missing_read_contract_flags_survive_bridge(self):
        _, result = self.request({'action': 'private_launch_contracts'})
        self.assertFalse(result['contracts']['AD']['active'])
        self.assertFalse(result['contracts']['CREATIVE']['verification_configured'])
        self.assertTrue(result['contracts']['CAMPAIGN']['active'])

    def test_saved_draft_publication_keeps_original_intent_through_php(self):
        original = {'launch_key': 'retained-draft', 'publish_saved_draft': True,
            'business_id': '222222', 'ad_account_id': '111111', 'page_id': '555555',
            'launch': {'execution_mode': 'DRAFT', 'creative': {'message': 'Original text'}}}
        status, result = self.request({'action': 'create', 'idempotency_key': 'draft-publish-source-task',
            'profiles': [{'profile_id': '39', 'tasks': [{'action': 'private_launch',
                'idempotency_key': 'draft-publish-source-task', 'payload': original}]}]})
        self.assertEqual(status, 200)
        forwarded = result['job']['forwarded']
        self.assertEqual(forwarded['idempotency_key'], 'draft-publish-source-task')
        self.assertEqual(forwarded['profiles'][0]['profile_id'], '39')
        self.assertEqual(forwarded['profiles'][0]['tasks'][0]['payload'], original)

    def test_multipart_media_uses_existing_durable_content_addressed_library(self):
        content = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
        boundary = 'remask-test-boundary'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\nprivate_launch_media\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="ad.png"\r\n'
                'Content-Type: image/png\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        result = None
        for _ in range(2):
            status, current = self.request(body=body, content_type=f'multipart/form-data; boundary={boundary}')
            self.assertEqual(status, 200, current)
            if result is not None:
                self.assertEqual(result, current)
            result = current
        self.assertEqual(result['media']['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(result['media']['size_bytes'], len(content))
        self.assertNotIn('path', result['media'])
        self.assertEqual(len(list((self.root / 'media/files').glob('*'))), 1)
