<?php
$root='/var/www/html';

function extract_method(string $src,string $name): string {
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

$out=['cache_files'=>[],'endpoint_cacheStore'=>''];
$endpoint=@file_get_contents($root.'/classes/MetaEndpoint.php') ?: '';
$out['endpoint_cacheStore']=extract_method($endpoint,'cacheStore');

foreach(glob($root.'/classes/*.php')?:[] as $file){
    $src=@file_get_contents($file);
    if($src===false||strpos($src,'function remember(')===false)continue;
    $out['cache_files'][]=[
        'file'=>basename($file),
        'remember'=>extract_method($src,'remember'),
        'get'=>extract_method($src,'get'),
        'peek'=>extract_method($src,'peek'),
        'invalidate'=>extract_method($src,'invalidate'),
    ];
}
fwrite(STDERR,"[sync-cache-audit] ".json_encode($out,JSON_UNESCAPED_SLASHES)."\n");
