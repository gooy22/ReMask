"""Real PHP/HTTP boundary: typed review failures and released session locks."""
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


class LaunchReviewBridgeTests(unittest.TestCase):
    def test_review_keeps_typed_worker_error_and_releases_session_before_read(self):
        source = (Path(__file__).resolve().parents[2] / 'railway-python-worker-jobs-overlay.php').read_text()
        helper = source[source.index('final class RmxPwjWorkerHttpError'):
                        source.index('\ntry {', source.index('final class RmxPwjWorkerHttpError'))]
        branch = source[source.index("    if ($action === 'private_launch_review') {"):
                        source.index("    if ($action === 'create') {")]
        catch = source[source.index('} catch (Throwable $e) {'):
                       source.rindex('\nPHP_CODE;')]
        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / 'sess_fixture-review'
            observed = []

            class Worker(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    with session_file.open('r') as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed.append((self.path, body))
                    self.send_response(409)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'detail': {
                        'code': 'PRIVATE_ADS_DRAFT_PAGE_UNVERIFIED',
                        'message': 'Fixture Page access was not verified', 'retryable': False,
                    }}).encode())

            server = ThreadingHTTPServer(('127.0.0.1', 0), Worker)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                prepare = r'''
function rmx_pwj_out(array $data, int $status=200): void { echo json_encode($data); exit; }
session_save_path($argv[1]); session_id('fixture-review'); session_start();
$_SESSION['remask_authenticated']=true;
$action='private_launch_review';
$input=['profile_id'=>'35','business_id'=>'111111','ad_account_id'=>'222222',
        'page_id'=>'333333','launch'=>['base'=>['creative'=>['name'=>'fixture']]]];
'''
                script = helper + prepare + '\ntry {\n' + branch + '\n' + catch
                response = subprocess.run(['php', '-r', script, directory], capture_output=True,
                    text=True, timeout=10, env={**os.environ,
                        'REMASK_PYTHON_WORKER_URL': f'http://127.0.0.1:{server.server_port}',
                        'REMASK_WORKER_API_KEY': 'fixture-internal-key'})
                self.assertEqual(response.returncode, 0, response.stderr)
                result = json.loads(response.stdout)
                self.assertFalse(result['ok'])
                self.assertEqual(result['code'], 'PRIVATE_ADS_DRAFT_PAGE_UNVERIFIED')
                self.assertEqual(result['message'], 'Fixture Page access was not verified')
                self.assertFalse(result['retryable'])
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0][0], '/api/v1/profiles/35/private-launch-review')
                self.assertEqual(observed[0][1]['page_id'], '333333')
                self.assertEqual(observed[0][1]['business_id'], '111111')
                self.assertEqual(observed[0][1]['ad_account_id'], '222222')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
