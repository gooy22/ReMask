<?php
require dirname(__DIR__).'/railway-readiness.php';
function expectReady(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}
$root = sys_get_temp_dir().'/remask-ready-'.bin2hex(random_bytes(8));
mkdir($root, 0700);
$accounts = $root.'/accounts.json';
$healthy = static fn()=>['ok'=>true, 'service'=>'remask-python-worker', 'worker_concurrency'=>8];
try {
    file_put_contents($accounts, '[]');
    $result=RemaskReadiness::check([$root], $accounts, $healthy);
    expectReady($result['ok']===true, 'empty workspace with healthy worker is ready');
    expectReady(count(glob($root.'/.ready-*'))===0, 'probe files are cleaned');
    foreach ([null, ['ok'=>false], ['ok'=>true,'service'=>'foreign','worker_concurrency'=>8],
        ['ok'=>true,'service'=>'remask-python-worker','worker_concurrency'=>0]] as $bad) {
        expectReady(RemaskReadiness::check([$root],$accounts,static fn()=>$bad)['ok']===false,
            'dead, malformed or foreign worker cannot pass readiness');
    }
    $failed=RemaskReadiness::check([$root],$accounts,static function(){throw new RuntimeException('secret');});
    expectReady(!$failed['ok'] && !str_contains(json_encode($failed),'secret'), 'failed probe is bounded and redacted');
    expectReady(!RemaskReadiness::check([$root.'/missing'],$accounts,$healthy)['ok'], 'missing disk directory is not ready');
    file_put_contents($accounts, '{broken');
    expectReady(!RemaskReadiness::check([$root],$accounts,$healthy)['checks']['accounts'], 'corrupt profiles cannot pass readiness');
    unlink($accounts);
    expectReady(!RemaskReadiness::check([$root],$accounts,$healthy)['ok'], 'missing profiles cannot pass readiness');
} finally { @unlink($accounts); rmdir($root); }
echo "Readiness requires writable storage, readable profiles and a healthy Python worker.\n";
