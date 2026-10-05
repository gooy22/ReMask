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
    def test_personal_portfolio_and_unmapped_accounts_are_excluded_with_correct_counts(self):
        source=(ROOT/'railway-workspace-sync-fix-overlay.php').read_text()
        helper=re.search(r'// REMASK_HONEST_SYNC_OUTCOME_V1\n(.*?)\n// REMASK_PERSISTENT_BM_RK_BINDING_V1',source,re.S).group(1)
        snapshot={'personal_scope_id':'61594882851656','profile':{'name':'9'},'profiles':[{'name':'9'}],
            'businesses':[{'id':'61594882851656'},{'id':'934505709362142'}],
            'ad_accounts':[
                {'id':'2279305019588057','business_id':'61594882851656'},
                {'id':'1152836437079070','business_id':'934505709362142'},
                {'id':'123456789'}]}
        run=subprocess.run(['php','-r',helper+'\n$s=json_decode(file_get_contents("php://stdin"),true);echo json_encode(hierarchy_asset_types_snapshot($s));'],
            input=json.dumps(snapshot),capture_output=True,text=True,check=True,timeout=5)
        result=json.loads(run.stdout)
        self.assertEqual([row['id'] for row in result['businesses']],['934505709362142'])
        self.assertEqual([row['id'] for row in result['ad_accounts']],['1152836437079070'])
        self.assertEqual(result['profile']['bm_count'],1)
        self.assertEqual(result['profile']['rk_count'],1)
        self.assertEqual(result['profiles'][0]['rk_count'],1)

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
