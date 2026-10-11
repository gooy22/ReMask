import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.contract_maintenance.ads_sources import deferred_script_urls, module_records, public_script_url
from app.contract_maintenance.launch_http_audit import collect_sources, inspect_saved_sources


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

    def test_literal_dependencies_and_compileable_wrapper_are_preserved(self):
        source = '__d("AdsPublish",["cr:20373","AdsGraphAPI"],function(a,b,c,d,e,f){f.publish=c("AdsGraphAPI");},98);'
        row, = module_records(source)
        self.assertEqual(row["dependencies"], ["cr:20373", "AdsGraphAPI"])
        self.assertEqual(row["module_source"], source)
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


class AdsSourceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_collection_only_gets_observed_public_urls_and_rejects_foreign_redirect(self):
        source = '__d("AdsPublish",[],function(a,b,c,d,e,f){f.x=1;},98);'
        document = '<script src="https://static.xx.fbcdn.net/a.js"></script><script src="https://evil.test/b.js"></script>'
        for final, expected in (("https://static.xx.fbcdn.net/a.js", 1), ("https://evil.test/a.js", 0)):
            with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"REMASK_DATA_DIR": temporary}):
                web = SimpleNamespace(fetch_text=AsyncMock(side_effect=[
                    (200, document, "https://adsmanager.facebook.com/adsmanager/manage/campaigns"),
                    (200, source, final)]), graphql=AsyncMock())
                result = await collect_sources(web, account="123456789", business="987654321")
                self.assertEqual(result["modules"], expected)
                self.assertEqual(result["scripts"], 1)
                self.assertEqual(web.fetch_text.await_count, 2)
                web.graphql.assert_not_awaited()
                saved = json.loads((Path(temporary)/"maintenance"/"ads-public-modules-candidate.json").read_text())
                self.assertEqual(saved["scope"], "candidate_only_not_installed")


if __name__ == "__main__":
    unittest.main()
