<?php
/**
 * Build-only, read-only sync source audit.
 * Emits structural facts only; never reads env values, account storage or credentials.
 */
$root='/var/www/html';

function audit_method(string $file, string $method): array {
    $src=@file_get_contents($file);
    if ($src===false) return ['exists'=>false,'reason'=>'file_missing'];
    $needle='function '.$method.'(';
    $start=strpos($src,$needle);
    if ($start===false) return ['exists'=>false,'reason'=>'method_missing'];
    $brace=strpos($src,'{',$start);
    if ($brace===false) return ['exists'=>false,'reason'=>'brace_missing'];
    $depth=0; $end=null; $len=strlen($src);
    for($i=$brace;$i<$len;$i++){
        if($src[$i]==='{')$depth++;
        elseif($src[$i]==='}'){
            $depth--;
            if($depth===0){$end=$i+1;break;}
        }
    }
    if($end===null)return ['exists'=>false,'reason'=>'method_unclosed'];
    $body=substr($src,$start,$end-$start);
    preg_match_all("/['\"]limit['\"]\s*=>\s*(\d+)/",$body,$limits);
    preg_match_all("#['\"](/?me/adaccounts|/?me/businesses|/?[^'\"]*adaccounts[^'\"]*)['\"]#i",$body,$paths);
    return [
        'exists'=>true,
        'bytes'=>strlen($body),
        'limits'=>array_values(array_unique(array_map('intval',$limits[1]??[]))),
        'paths'=>array_values(array_unique($paths[1]??[])),
        'has_paging'=>stripos($body,'paging')!==false,
        'has_after_cursor'=>preg_match('/\bafter\b/i',$body)===1,
        'has_next'=>preg_match('/\bnext\b/i',$body)===1,
        'has_loop'=>preg_match('/\b(while|for|foreach)\s*\(/',$body)===1,
        'calls_client_request'=>strpos($body,'->request(')!==false || strpos($body,'->get(')!==false,
        'called_methods'=>(function() use ($body) { preg_match_all('/->([A-Za-z_][A-Za-z0-9_]*)\\s*\\(/',$body,$m); return array_values(array_unique($m[1]??[])); })(),
        'body'=>$body,
    ];
}

$service=$root.'/classes/MetaAdsService.php';
$out=[
  'listAdAccounts'=>audit_method($service,'listAdAccounts'),
  'listBusinesses'=>audit_method($service,'listBusinesses'),
  'listBusinessAdAccounts'=>audit_method($service,'listBusinessAdAccounts'),
];

$client=@file_get_contents($root.'/classes/MetaApiClient.php');
$out['client']=[
  'exists'=>$client!==false,
  'has_generic_pagination_helper'=>$client!==false && preg_match('/function\s+\w*(page|paginate|all)\w*\s*\(/i',$client)===1,
  'mentions_paging'=>$client!==false && stripos($client,'paging')!==false,
  'mentions_after_cursor'=>$client!==false && preg_match('/\bafter\b/i',$client)===1,
];

fwrite(STDERR,"[sync-source-audit] ".json_encode($out,JSON_UNESCAPED_SLASHES)."\n");
