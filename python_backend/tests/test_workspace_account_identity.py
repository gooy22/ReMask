import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("php"), "PHP required in deployment CI")
class WorkspaceAccountIdentityTests(unittest.TestCase):
    def test_aliases_restore_disabled_observation_and_update_all_counts(self):
        source=(ROOT/"railway-payment-inspection-overlay.php").read_text()
        helper=re.search(r"<<<'CANONICAL'\n(.*?)\nCANONICAL;",source,re.S).group(1)
        sync_source=(ROOT/"railway-workspace-sync-fix-overlay.php").read_text()
        type_helper=re.search(r"// REMASK_HONEST_SYNC_OUTCOME_V1\n(.*?)\n// REMASK_PERSISTENT_BM_RK_BINDING_V1",sync_source,re.S).group(1)
        rows=[
            {"id":"act_123456789","business_id":"111111111","account_status":2,"disable_reason":1},
            {"id":"123456789","business_id":"111111111","_provisioned_only":True},
            {"id":"act_987654321","business_id":"111111111","account_status":2,"_provisioned_only":True,"_raw_account_status":2},
            {"id":"987654321","business_id":"111111111","_provisioned_only":True},
            {"id":"555555555","profile":"Other","business_id":"111111111","account_status":1},
            {"id":"act_111111111","business_id":"111111111","account_status":1},
            {"id":"666666666","business_id":"111111111","account_status":1},
        ]
        for ordered in [rows,list(reversed(rows))]:
            snapshot={"ad_accounts":ordered,"businesses":[{"id":"111111111"}],"pages":[{"id":"666666666"}],
                "profile":{"name":"Fixture"},"profiles":[{"name":"Fixture"}]}
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"helper.php"
                path.write_text("<?php\n"+type_helper+"\n"+helper)
                run=subprocess.run(["php","-r",'require $argv[1];$s=json_decode($argv[2],true);echo json_encode(hierarchy_canonical_account_snapshot("Fixture",$s));',
                    str(path),json.dumps(snapshot)],capture_output=True,text=True,check=True,timeout=10)
            result=json.loads(run.stdout)
            self.assertEqual(len(result["ad_accounts"]),2)
            self.assertTrue(all(row["account_status"]==2 for row in result["ad_accounts"]))
            self.assertTrue(all("_provisioned_only" not in row for row in result["ad_accounts"]))
            self.assertEqual(result["profile"]["rk_count"],2)
            self.assertEqual(result["profiles"][0]["rk_count"],2)
            self.assertEqual(result["businesses"][0]["ad_account_count"],2)
            self.assertEqual(len(result["businesses"][0]["accounts"]),2)
