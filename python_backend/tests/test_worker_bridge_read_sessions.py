"""Local HTTP proof that worker reads do not retain the operator's PHP session."""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
from threading import Event, Thread
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("php") and os.name == "posix", "PHP required in deployment CI")
class WorkerReadSessionTests(unittest.TestCase):
    def setUp(self):
        self.entered, self.release = Event(), Event()
        self.addCleanup(self.release.set)
        self.requests = []
        entered, release, requests = self.entered, self.release, self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(handler):
                requests.append((handler.path, handler.headers.get("X-Remask-Worker-Key")))
                if handler.path in {"/health", "/api/v1/jobs/fixture-job", "/fixture-sync"}:
                    entered.set()
                    release.wait(6)
                payload = {"ok": True, "ready": True, "service": "remask-python-worker",
                           "worker_concurrency": 2}
                data = json.dumps(payload).encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)

        self.worker = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.worker.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_worker)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "ajax").mkdir()
        (self.root / "sessions").mkdir()
        (self.root / "settings.php").write_text("<?php // local fixture")
        auth = ("<?php ini_set('session.save_path',"
                + json.dumps(str(self.root / "sessions"))
                + "); session_start(); $_SESSION['authorized']=true;"
                + "function remask_csrf_token(){"
                + "if(!isset($_SESSION['csrf'])) $_SESSION['csrf']=bin2hex(random_bytes(16));"
                + "return $_SESSION['csrf'];}")
        (self.root / "checkpassword.php").write_text(auth)
        source = (ROOT / "railway-python-worker-jobs-overlay.php").read_text()
        php = re.search(r"<<<'PHP_CODE'\n(.*?)\nPHP_CODE;", source, re.S).group(1)
        (self.root / "ajax/pythonWorkerJobs.php").write_text(php)
        sync_source = (ROOT / "railway-workspace-sync-fix-overlay.php").read_text()
        sync = sync_source.split("$syncProfileReplacement = <<<'PHP'\n", 1)[1].split("\nPHP;", 1)[0]
        stub = ("<?php require __DIR__.'/../checkpassword.php';"
                "$action='sync_profile'; $input=['profile'=>'fixture'];"
                "class MetaEndpoint {static function ok($data){"
                "header('Content-Type: application/json');"
                "echo json_encode(['ok'=>true,'data'=>$data]);exit;}}"
                "function hierarchy_sync_result_put(...$args){}"
                "function hierarchy_profile_sync_lock($profile){"
                '$ctx=stream_context_create(["http"=>["header"=>'
                '"X-Remask-Worker-Key: fixture-server-key\\r\\n"]]);'
                "file_get_contents(getenv('REMASK_PYTHON_WORKER_URL').'/fixture-sync',false,$ctx);"
                "return false;}")
        (self.root / "ajax/fixtureSync.php").write_text(stub + sync)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/ajax/pythonWorkerJobs.php"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.php = subprocess.Popen(
            ["php", "-S", f"127.0.0.1:{port}", "-t", str(self.root)],
            env={**os.environ, "PHP_CLI_SERVER_WORKERS": "4", "REMASK_INTERNAL_KEY": "",
                 "REMASK_WORKER_API_KEY": "fixture-server-key",
                 "REMASK_PYTHON_WORKER_URL": f"http://127.0.0.1:{self.worker.server_port}"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.stop_php)
        for _ in range(100):
            try:
                self.token = self.request("csrf", timeout=.2)["csrf"]
                break
            except (OSError, TimeoutError):
                if self.php.poll() is not None:
                    self.fail("PHP multiworker fixture failed to start")
                time.sleep(.02)
        else:
            self.fail("PHP fixture unavailable")

    def stop_worker(self):
        self.release.set()
        self.worker.shutdown()
        self.worker.server_close()
        self.thread.join(timeout=5)

    def stop_php(self):
        try:
            os.killpg(self.php.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.php.wait(timeout=5)

    def request(self, action, *, timeout=8):
        suffix = "?action=" + action
        if action == "status":
            suffix += "&job_id=fixture-job"
        url = self.url.rsplit("/", 1)[0] + "/fixtureSync.php" if action == "fixture_sync" else self.url + suffix
        request = urllib.request.Request(url,
            headers={"Cookie": "PHPSESSID=remask-session-fixture"})
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def assert_nonblocking_read(self, action):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(self.request, action)
            try:
                self.assertTrue(self.entered.wait(3), "Worker read did not begin")
                csrf = pool.submit(self.request, "csrf")
                try:
                    result = csrf.result(timeout=2)
                except concurrent.futures.TimeoutError:
                    self.fail("Worker read retained the same operator session lock")
                self.assertEqual(result["csrf"], self.token)
                self.assertFalse(slow.done(), "Proof needs the worker read still blocked")
            finally:
                self.release.set()
            self.assertTrue(slow.result(timeout=3)["ok"])
        self.assertEqual(self.request("csrf")["csrf"], self.token)
        self.assertTrue(self.requests)
        self.assertTrue(all(key == "fixture-server-key" for _, key in self.requests))

    def test_slow_health_does_not_block_same_session_csrf_or_lose_token(self):
        self.assert_nonblocking_read("health")

    def test_slow_status_does_not_block_same_session_csrf_or_lose_token(self):
        self.assert_nonblocking_read("status")

    def test_slow_sync_does_not_block_same_session_csrf_or_lose_token(self):
        self.assert_nonblocking_read("fixture_sync")
