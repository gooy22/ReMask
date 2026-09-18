<?php
$root='/var/www/html';

function rmx_audit_method(string $file,string $name): string {
    $src=@file_get_contents($file);
    if($src===false)return '';
    $needle='function '.$name.'(';
    $start=strpos($src,$needle);
    if($start===false)return '';
    $brace=strpos($src,'{',$start);
    if($brace===false)return '';
    $depth=0;$end=null;$len=strlen($src);
    for($i=$brace;$i<$len;$i++){
        if($src[$i]==='{')$depth++;
        elseif($src[$i]==='}'){
            $depth--;
            if($depth===0){$end=$i+1;break;}
        }
    }
    return $end===null?'':substr($src,$start,$end-$start);
}

function rmx_contexts(string $src,string $needle,int $radius=900,int $max=12): array {
    $out=[];$offset=0;
    while(count($out)<$max && ($p=strpos($src,$needle,$offset))!==false){
        $start=max(0,$p-$radius);
        $out[]=substr($src,$start,min(strlen($src)-$start,$radius*2+strlen($needle)));
        $offset=$p+strlen($needle);
    }
    return $out;
}

$endpoint=$root.'/classes/MetaEndpoint.php';
$client=$root.'/classes/MetaApiClient.php';
$workspace=@file_get_contents($root.'/scripts/workspace.js') ?: '';
$hierarchy=@file_get_contents($root.'/ajax/metaHierarchy.php') ?: '';

$cacheCandidates=[];
foreach(glob($root.'/classes/*.php') ?: [] as $file){
    $src=@file_get_contents($file);
    if($src===false || strpos($src,'function remember(')===false) continue;
    $cacheCandidates[basename($file)]=[
        'remember'=>rmx_audit_method($file,'remember'),
        'get'=>rmx_audit_method($file,'get'),
        'put'=>rmx_audit_method($file,'put'),
        'delete'=>rmx_audit_method($file,'delete'),
    ];
}

$out=[
    'cacheStore'=>rmx_audit_method($endpoint,'cacheStore'),
    'peekCachedPreflight'=>rmx_audit_method($endpoint,'peekCachedPreflight'),
    'peekCachedAsset'=>rmx_audit_method($endpoint,'peekCachedAsset'),
    'invalidatePreflight'=>rmx_audit_method($endpoint,'invalidatePreflight'),
    'invalidateAsset'=>rmx_audit_method($endpoint,'invalidateAsset'),
    'cacheCandidates'=>$cacheCandidates,
    'clientGet'=>rmx_audit_method($client,'get'),
    'clientPost'=>rmx_audit_method($client,'post'),
    'clientRequest'=>rmx_audit_method($client,'request'),
    'workspaceSyncCalls'=>rmx_contexts($workspace,'syncSelection(',1100,12),
    'workspaceHierarchyCalls'=>rmx_contexts($workspace,'metaHierarchy.php',1100,20),
    'hierarchyCatchTail'=>substr($hierarchy,max(0,strlen($hierarchy)-5000)),
];
fwrite(STDERR,'[sync-cache-error-audit] '.json_encode($out,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE)."\n");
