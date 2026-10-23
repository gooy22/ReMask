"""Exercise the installed PHP handler contract across independent processes."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class WorkspaceSyncCoordinationTests(unittest.TestCase):
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
