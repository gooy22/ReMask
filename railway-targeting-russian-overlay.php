<?php
$servicePath='/var/www/html/classes/MetaAdsService.php';
$service=file_get_contents($servicePath);
if($service===false){fwrite(STDERR,"[targeting-ru] read failed\n");exit(241);}

/* Force Russian localized suggestions for GEO / Interests / Behaviors. */
$patterns = [
    [
        "needle" => "'type' => 'adinterest',\n            'q' => $query,",
        "replace" => "'type' => 'adinterest',\n            'q' => $query,\n            'locale' => 'ru_RU',",
        "label" => "interests"
    ],
    [
        "needle" => "'type' => 'adgeolocation',\n            'q' => $query,",
        "replace" => "'type' => 'adgeolocation',\n            'q' => $query,\n            'locale' => 'ru_RU',",
        "label" => "geo"
    ],
    [
        "needle" => "'q' => $query,\n            'limit_type' => 'behaviors',",
        "replace" => "'q' => $query,\n            'locale' => 'ru_RU',\n            'limit_type' => 'behaviors',",
        "label" => "behaviors"
    ],
];

foreach($patterns as $p){
    if(strpos($service,$p['replace'])!==false) continue;
    if(strpos($service,$p['needle'])===false){
        fwrite(STDERR,"[targeting-ru] missing ".$p['label']." anchor\n");
        exit(242);
    }
    $service=str_replace($p['needle'],$p['replace'],$service,$n);
    if($n!==1){
        fwrite(STDERR,"[targeting-ru] ".$p['label']." replacement count=$n\n");
        exit(243);
    }
}

if(strpos($service,'REMASK_TARGETING_RU_LOCALE_V1')===false){
    $service=str_replace(
        "    public function searchInterests(string $query, int $limit = 25): array\n",
        "    /* REMASK_TARGETING_RU_LOCALE_V1 */\n    public function searchInterests(string $query, int $limit = 25): array\n",
        $service,
        $n
    );
}

file_put_contents($servicePath,$service);
fwrite(STDERR,"[targeting-ru] GEO / Interests / Behaviors locale=ru_RU enabled\n");
