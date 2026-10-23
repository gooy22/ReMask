"""Exercise the installed PHP handler contract across independent processes."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class WorkspaceSyncCoordinationTests(unittest.TestCase):
    def test_indexed_rk_merge_preserves_identity_and_partial_rows(self):
        """Execute the installed PHP reconciliation, not a reimplementation."""
        source = (Path(__file__).resolve().parents[2] / 'railway-workspace-sync-fix-overlay.php').read_text()
        start = source.index('        $rkPartial = false;')
        end = source.index('        // Provisioning bindings are historical state only.', start)
        code = source[start:end]
        self.assertIn('REMASK_LINEAR_EXISTING_RK_MERGE_V1', code)
        fixture = r'''
function hierarchy_binding_put_many($profile,$records) {
    foreach ($records as $row) {
        $GLOBALS['recorded'][]=[$profile, $row['business_id'],
            $row['ad_account_id'], $row['account_name']];
    }
}
$profile = 'fixture'; $businessRows = []; $adAccountRows = [];
$seenBusiness = []; $seenAccount = []; $syncWarnings = [];
$existingSnapshot = ['ad_accounts'=>[
    ['id'=>'act_2222222222','business_id'=>'1111111111',
     'name'=>'Saved RK Name','business_asset_id'=>'saved-asset'],
    ['id'=>'act_3333333333','business_id'=>'1111111111','name'=>'Preserve RK'],
    ['id'=>'act_2222222222','business_id'=>'9999999999','name'=>'Foreign RK'],
]];
$liveInventory = ['businesses'=>[
    ['id'=>'1111111111','name'=>'BM One','ad_accounts_partial'=>true,
     'ad_accounts'=>[
        ['id'=>'2222222222','name'=>'2222222222'],
        ['id'=>'5555555555','name'=>'Fresh RK'],
    ]],
    ['id'=>'7777777777','name'=>'BM Two','ad_accounts'=>[
        ['id'=>'8888888888','name'=>'Second BM RK'],
    ]],
]];
'''
        php = fixture + code + r'''
echo json_encode(['accounts'=>$adAccountRows,
    'businesses'=>$businessRows,'partial'=>$rkPartial,
    'recorded'=>$GLOBALS['recorded']??[]]);
'''
        result = subprocess.run(['php', '-r', php], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        records = output['accounts']
        self.assertEqual([row['id'] for row in records],
                         ['3333333333', '2222222222', '5555555555', '8888888888'])
        self.assertTrue(records[0]['_sync_preserved'])
        self.assertEqual(records[1]['name'], 'Saved RK Name')
        self.assertEqual(records[1]['business_asset_id'], 'saved-asset')
        self.assertEqual(records[1]['business_id'], '1111111111')
        self.assertTrue(output['partial'])
        self.assertEqual([row['ad_account_count'] for row in output['businesses']], [2,1])
        self.assertEqual(len(output['recorded']), 3)
        self.assertFalse(any(row.get('name') == 'Foreign RK' for row in records))

    def test_parallel_profile_snapshot_writes_never_lose_another_profile(self):
        """The cross-profile UI pool must preserve both persisted snapshots."""
        source = (Path(__file__).resolve().parents[2] / 'railway-workspace-sync-fix-overlay.php').read_text()
        helper = source[source.index('function hierarchy_live_snapshot_file('):
                        source.index('function hierarchy_live_snapshot_apply_display(')]
        with tempfile.TemporaryDirectory() as directory:
            helper = helper.replace('/var/lib/remask', directory)
            script = helper + r'''
fgets(STDIN);
$profile=$argv[1]; $business=$argv[2]; $account=$argv[3];
for ($i=0;$i<80;$i++) {
    hierarchy_live_snapshot_put(
        $profile,
        [['id'=>$business,'name'=>'Live BM']],
        [['id'=>$account,'business_id'=>$business]],
        [], ['name'=>$profile], false
    );
}
echo 'SAVED';
'''
            profiles = [
                ('alpha', '1111111111', '2222222222'),
                ('bravo', '3333333333', '4444444444'),
                ('charlie', '5555555555', '6666666666'),
            ]
            children = []
            try:
                for args in profiles:
                    child = subprocess.Popen(['php', '-r', script, *args],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
                    children.append(child)
                for child in children:
                    child.stdin.write('start\\n')
                    child.stdin.flush()
                for child in children:
                    output, errors = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, errors)
                    self.assertEqual(output, 'SAVED')
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)
            file = Path(directory) / 'workspace-live-meta-snapshots.json'
            content = json.loads(file.read_text())
            self.assertEqual(set(content), {entry[0] for entry in profiles})
            for profile, business, account in profiles:
                self.assertEqual(content[profile]['businesses'][0]['id'], business)
                self.assertEqual(content[profile]['ad_accounts'][0]['id'], account)
                self.assertEqual(content[profile]['profile']['name'], profile)

    def test_batched_bindings_preserve_old_businesses_and_last_rk_wins(self):
        source = (Path(__file__).resolve().parents[2] / 'railway-workspace-sync-fix-overlay.php').read_text()
        start = source.index('function hierarchy_binding_file(): string')
        end = source.index('PHP_HELPERS;', start)
        with tempfile.TemporaryDirectory() as directory:
            helpers = source[start:end].replace('/var/lib/remask', directory)
            fixture = helpers + r'''
hierarchy_binding_put_many('alpha',[
    ['business_id'=>'1111111111','ad_account_id'=>'2222222222','account_name'=>'First'],
    ['business_id'=>'1111111111','ad_account_id'=>'3333333333','account_name'=>'Second'],
    ['business_id'=>'4444444444','ad_account_id'=>'5555555555','account_name'=>'Other'],
]);
hierarchy_binding_put_many('bravo',[
    ['business_id'=>'7777777777','ad_account_id'=>'8888888888'],
]);
hierarchy_binding_put_many('alpha',[
    ['business_id'=>'4444444444','ad_account_id'=>'6666666666'],
]);
echo json_encode([hierarchy_binding_get('alpha'),hierarchy_binding_get('bravo')]);
'''
            result = subprocess.run(['php', '-r', fixture],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            alpha, bravo = json.loads(result.stdout)
            self.assertEqual({row['business_id']: row['ad_account_id'] for row in alpha},
                {'1111111111':'3333333333', '4444444444':'6666666666'})
            self.assertEqual(bravo[0]['ad_account_id'], '8888888888')
            path = Path(directory) / 'workspace-provisioning-bindings.json'
            self.assertEqual(set(json.loads(path.read_text())), {'alpha','bravo'})

    def test_busy_profile_never_starts_another_meta_read_and_other_profiles_are_free(self):
        source = (Path(__file__).resolve().parents[2] / 'railway-workspace-sync-fix-overlay.php').read_text()
        helper = source[source.index('function hierarchy_profile_sync_lock('):
                        source.index('function hierarchy_sync_result_file(')]
        handler = re.search(r"\$syncProfileReplacement = <<<'PHP'\n(.*?)\nPHP;", source, re.S)[1]
        with tempfile.TemporaryDirectory() as directory:
            helper = helper.replace('/var/lib/remask', directory)
            holder = subprocess.Popen(['php', '-r', helper +
                "$held=hierarchy_profile_sync_lock('35'); echo \"LOCKED\\n\"; fgets(STDIN);"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'LOCKED')
                script = helper + r'''
class SyncReply extends RuntimeException {
    public array $data;
    function __construct(array $data) { $this->data=$data; }
}
class MetaEndpoint { static function ok(array $data) { throw new SyncReply($data); } }
function hierarchy_sync_result_put($request, $profile, $result) { $GLOBALS['saved']=$result; }
function hierarchy_worker_live_inventory() { throw new RuntimeException('DUPLICATE_META_READ'); }
function run_sync() { $action='sync_profile';
    $input=['profile'=>'35','request_id'=>'fixture_sync_request'];
''' + handler + r'''
}
try { run_sync(); } catch (SyncReply $reply) {
    if ($reply->data['sync_error_kind']!=='SYNC_IN_PROGRESS'
        || $reply->data['sync_complete']!==false || empty($GLOBALS['saved'])) exit(2);
}
$other=hierarchy_profile_sync_lock('36');
if (!is_resource($other)) exit(3);
fclose($other);
echo json_encode(['busy'=>true,'other_profile_free'=>true]);
'''
                response = subprocess.run(['php', '-r', script], capture_output=True, text=True, timeout=10)
                self.assertEqual(response.returncode, 0, response.stderr)
                self.assertEqual(json.loads(response.stdout), {'busy': True, 'other_profile_free': True})
                self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600
                                    for path in Path(directory).glob('*.lock')))
            finally:
                holder.stdin.write('\n')
                holder.stdin.flush()
                holder.communicate(timeout=10)
            # Process exit releases the lease even without explicit cleanup.
            response = subprocess.run(['php', '-r', helper +
                "$lock=hierarchy_profile_sync_lock('35'); echo is_resource($lock)?'FREE':'BUSY';"],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(response.stdout, 'FREE')
