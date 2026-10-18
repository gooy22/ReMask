import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('audit_gateway',Path(__file__).with_name('gateway.py'))
gateway=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)

class BoundaryTests(unittest.TestCase):
    def test_rejects_other_origins_queries_and_traversal(self):
        for path in ['http://other.invalid/','//other.invalid/','/x?password=y','/%2e%2e/x','/x%0d%0aHost:bad','/x\\y']:
            self.assertFalse(gateway.valid_path(path),path)
        self.assertTrue(gateway.valid_path('/bundles%2Ejson'))

    def test_private_data_redacted(self):
        self.assertTrue(gateway.sensitive('/bundles.json',b'[{"name":"private"}]','application/json'))
        self.assertTrue(gateway.sensitive('/backup.txt',b'password="private-value"','text/plain'))
        self.assertFalse(gateway.sensitive('/ready.php',b'{"ok":true}','application/json'))

    def test_http_writes_and_out_of_scope_targets_never_reach_origin(self):
        server=gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with patch.object(gateway,'upstream') as upstream:
                for path,method,status in [('/bundles.json','POST',405),('/x?evil=1','GET',400)]:
                    request=urllib.request.Request(url+path,method=method)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request)
                    self.assertEqual(caught.exception.code,status)
                upstream.assert_not_called()
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_real_status_preserved_but_private_body_removed(self):
        server=gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        gateway.RECORDS.clear();gateway.LAST=0
        try:
            with patch.object(gateway,'upstream',return_value=(200,{'Content-Type':'application/json'},b'[{"name":"private-value"}]',[])):
                with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/bundles.json') as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['X-Audit-Body-Redacted'],'true')
                    self.assertNotIn(b'private-value',response.read())
            self.assertNotIn('private-value',json.dumps(gateway.RECORDS))
            self.assertEqual(gateway.RECORDS[0]['headers'],{'Content-Type':'application/json'})
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':
    unittest.main()
