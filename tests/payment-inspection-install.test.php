<?php
declare(strict_types=1);

// Exercise the real installer against an isolated runtime, including the URL
// loaded by returning users who already cached the previous workspace bundle.
$directory=sys_get_temp_dir().'/remask-payment-install-'.bin2hex(random_bytes(5));
mkdir($directory.'/html/ajax',0700,true);
mkdir($directory.'/html/classes',0700,true);
mkdir($directory.'/html/scripts',0700,true);
mkdir($directory.'/input',0700,true);
try {
    foreach(['railway-payment-card-vault.php','railway-payment-card-endpoint.php'] as $file){
        copy(__DIR__.'/../'.$file,$directory.'/input/'.$file);
    }
    $overlay=file_get_contents(__DIR__.'/../railway-payment-inspection-overlay.php');
    $overlay=strtr($overlay,["'/var/www/html'"=>var_export($directory.'/html',true),"'/tmp/"=>"'".$directory.'/input/']);
    file_put_contents($directory.'/install.php',$overlay);
    $ui=file_get_contents(__DIR__.'/../railway-payment-inspection-ui.js');
    $urls=[];
    foreach([$ui,$ui."\n// fixture release change\n",$ui."\n// fixture release change\n"] as $index=>$source){
        file_put_contents($directory.'/input/railway-payment-inspection-ui.js',$source);
        file_put_contents($directory.'/html/ajax/metaHierarchy.php', <<<'PHP'
<?php
function fixture($profile,$snapshot){
    return hierarchy_asset_types_snapshot($snapshot);
}
function actions($action){
    if ($action === 'funding_status') {
        old_funding();
    }
    if ($action === 'set_delivery_status') {
        delivery();
    }
}
PHP);
        file_put_contents($directory.'/html/scripts/workspace.js', <<<'JS'
function fundingStatusValue(f){return 'READY';}
function fundingCell(f){return 'old';}
function closeModal(){return;}
async function showFunding(){return;}
function annotateDeliveryRows(rows){return rows;}
JS);
        $html='<script src="scripts/workspace.js?v=20261004-python-worker-ui-v219-cookie-only-v1-numbered-v1-txt-v3"></script>';
        foreach(['accounts.php','workspace.php'] as $page)file_put_contents($directory.'/html/'.$page,$html);
        $output=[];$exit=0;
        exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($directory.'/install.php').' 2>&1',$output,$exit);
        if($exit!==0)throw new RuntimeException('Installer failed: '.implode("\n",$output));
        foreach(['accounts.php','workspace.php'] as $page){
            preg_match('/src="([^"]+)"/',file_get_contents($directory.'/html/'.$page),$match);
            $urls[$page][]=$match[1]??'';
        }
        if(!str_contains(file_get_contents($directory.'/html/scripts/workspace.js'),$source))throw new RuntimeException('New UI was not installed');
        if(!is_file($directory.'/html/ajax/paymentCards.php')||!is_file($directory.'/html/classes/RemaskPaymentCardVault.php'))throw new RuntimeException('Card endpoint or vault not installed');
    }
    foreach($urls as $page=>$versions){
        if($versions[0]===''||$versions[0]===$versions[1]||$versions[1]!==$versions[2])throw new RuntimeException('Workspace cache invalidation failed for '.$page);
        if(!str_contains($versions[1],'-numbered-v1-txt-v3'))throw new RuntimeException('Other bundle versions were lost');
    }
    // Worker recovery changes must invalidate Workspace even when the card
    // bundle is unchanged; later overlays must preserve that revision.
    $workerInstaller=file_get_contents(__DIR__.'/../railway-python-worker-ui-overlay.php');
    $workerInstaller=strtr($workerInstaller,[
        "'/var/www/html/workspace.php'"=>var_export($directory.'/html/workspace.php',true),
        "'/var/www/html/scripts/workspace.js'"=>var_export($directory.'/html/scripts/workspace.js',true),
        "'/tmp/railway-python-worker-ui.js'"=>var_export($directory.'/input/railway-python-worker-ui.js',true),
        "'/var/www/html/ajax/pythonWorkerPages.php'"=>var_export($directory.'/html/ajax/pythonWorkerPages.php',true),
        "'/var/www/html/ajax/pythonWorkerBusinesses.php'"=>var_export($directory.'/html/ajax/pythonWorkerBusinesses.php',true),
    ]);
    file_put_contents($directory.'/worker-install.php',$workerInstaller);
    $workerUi=file_get_contents(__DIR__.'/../railway-python-worker-ui.js');
    $workerUrls=[];
    foreach([$workerUi,$workerUi."\n// worker fixture release\n",$workerUi."\n// worker fixture release\n"] as $source){
        file_put_contents($directory.'/input/railway-python-worker-ui.js',$source);
        file_put_contents($directory.'/html/workspace.php',
            '<body><script src="scripts/workspace.js?v=old"></script></body>');
        file_put_contents($directory.'/html/scripts/workspace.js',
            "function fundingStatusValue(f){return 'READY';}\nfunction fundingCell(f){return 'old';}\nfunction closeModal(){return;}\nasync function showFunding(){return;}\nfunction annotateDeliveryRows(rows){return rows;}\n");
        $output=[];$exit=0;
        exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($directory.'/worker-install.php').' 2>&1',$output,$exit);
        if($exit!==0)throw new RuntimeException('Worker installer failed: '.implode("\n",$output));
        $workspace=file_get_contents($directory.'/html/workspace.php');
        // Same prefix insertion performed by the cookie-only overlay.
        $workspace=str_replace('20261004-python-worker-ui-v219',
            '20261004-python-worker-ui-v219-cookie-only-v1',$workspace);
        file_put_contents($directory.'/html/workspace.php',$workspace);
        $output=[];$exit=0;
        exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($directory.'/install.php').' 2>&1',$output,$exit);
        if($exit!==0)throw new RuntimeException('Payment installer after worker failed: '.implode("\n",$output));
        preg_match('/src="([^"]+)"/',file_get_contents($directory.'/html/workspace.php'),$match);
        $url=$match[1]??'';
        if(!str_contains($url,'-worker-')||!str_contains($url,'-payment-cards-')||
            !str_contains($url,'-cookie-only-v1'))throw new RuntimeException('Worker revision was lost by a later overlay');
        if(!str_contains(file_get_contents($directory.'/html/scripts/workspace.js'),$source))
            throw new RuntimeException('Recovery UI was lost by a later overlay');
        $workerUrls[]=$url;
    }
    if($workerUrls[0]===$workerUrls[1]||$workerUrls[1]!==$workerUrls[2])
        throw new RuntimeException('Worker recovery cache invalidation failed');
    echo "worker installer: recovery UI revision survives cookie/card overlays and invalidates cache\n";
    echo "card installer: changed UI gets a new bundle URL; identical builds stay stable\n";
} finally {
    $files=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($directory,FilesystemIterator::SKIP_DOTS),RecursiveIteratorIterator::CHILD_FIRST);
    foreach($files as $file){if($file->isDir())rmdir($file->getPathname());else unlink($file->getPathname());}
    rmdir($directory);
}
