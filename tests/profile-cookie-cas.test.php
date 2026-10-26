<?php
declare(strict_types=1);
$runtime=getcwd();
require $runtime.'/classes/FbAccount.php';
require $runtime.'/classes/RemaskProxy.php';
require $runtime.'/classes/FbAccountSerializer.php';
function expectCookie(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
$dir=sys_get_temp_dir().'/remask-cookie-cas-'.bin2hex(random_bytes(5));
mkdir($dir,0700);
$path=$dir.'/accounts.json';
try {
    $store=new FbAccountSerializer($path);
    $first=new FbAccount('first','fixture-token',json_encode([['name'=>'c_user','value'=>'12345'],['name'=>'xs','value'=>'old-xs']]),null,null);
    $second=new FbAccount('second','fixture-token-2',json_encode([['name'=>'c_user','value'=>'67890'],['name'=>'xs','value'=>'other-xs']]),null,null);
    $store->addOrUpdateAccount($first); $store->addOrUpdateAccount($second);
    $other=serialize($store->getAccountByName('second'));
    expectCookie($store->compareAndSwapCookies('first','12345','old-xs',['c_user'=>'12345','xs'=>'fresh-xs']), 'rotation failed');
    $saved=$store->getAccountByName('first');
    expectCookie(RemaskCookieSession::map($saved->cookies)['xs']==='fresh-xs','fresh session not saved');
    expectCookie($saved->userId==='12345' && array_is_list($saved->cookies),'profile identity lost during reload');
    expectCookie($saved->token==='fixture-token' && $saved->proxy===null,'unrelated profile fields changed');
    expectCookie(serialize($store->getAccountByName('second'))===$other,'another profile changed');
    $before=file_get_contents($path);
    expectCookie(!$store->compareAndSwapCookies('first','12345','old-xs',['c_user'=>'12345','xs'=>'stale-xs']), 'stale session won');
    expectCookie(file_get_contents($path)===$before,'stale rejection rewrote storage');
    expectCookie(!$store->compareAndSwapCookies('missing','12345','fresh-xs',['c_user'=>'12345','xs'=>'new-xs']), 'missing profile was created');
    foreach ([['c_user'=>'67890','xs'=>'other-xs'], ['c_user'=>'12345'],
              [['name'=>'c_user','value'=>'12345'],['name'=>'xs','value'=>'one'],['name'=>'xs','value'=>'two']]] as $bad) {
        try { $store->compareAndSwapCookies('first','12345','fresh-xs',$bad); throw new RuntimeException('invalid cookies accepted'); }
        catch (InvalidArgumentException $error) {}
        expectCookie(file_get_contents($path)===$before,'invalid session changed storage');
    }
    echo "Cookie refresh is atomic, rejects stale sessions, and preserves other profiles.\n";
} finally {
    foreach (glob($dir.'/*') as $file) unlink($file);
    rmdir($dir);
}
