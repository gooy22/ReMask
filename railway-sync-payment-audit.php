<?php
$root='/var/www/html';

function method_body(string $file,string $name): string {
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

function contexts(string $src,string $needle,int $radius=450,int $max=12): array {
    $out=[];$offset=0;
    while(count($out)<$max && ($p=stripos($src,$needle,$offset))!==false){
        $start=max(0,$p-$radius);
        $out[]=substr($src,$start,min(strlen($src)-$start,$radius*2+strlen($needle)));
        $offset=$p+strlen($needle);
    }
    return $out;
}

$service=$root.'/classes/MetaAdsService.php';
$workspace=@file_get_contents($root.'/scripts/workspace.js') ?: '';
$hierarchy=@file_get_contents($root.'/ajax/metaHierarchy.php') ?: '';

$out=[
  'getFundingStatus'=>method_body($service,'getFundingStatus'),
  'listAdAccounts'=>method_body($service,'listAdAccounts'),
  'workspace_funding_contexts'=>contexts($workspace,'funding',500,10),
  'workspace_payment_contexts'=>contexts($workspace,'payment',500,10),
  'hierarchy_funding_contexts'=>contexts($hierarchy,'funding',500,10),
];
fwrite(STDERR,"[sync-payment-audit] ".json_encode($out,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE)."\n");
