<?php
$servicePath='/var/www/html/classes/MetaAdsService.php';
$service=file_get_contents($servicePath);
if($service===false){fwrite(STDERR,"[targeting-ru] read failed\n");exit(241);}

$patterns = [
    [
        'needle' => <<<'PHP'
            'type' => 'adinterest',
            'q' => $query,
PHP,
        'replace' => <<<'PHP'
            'type' => 'adinterest',
            'q' => $query,
            'locale' => 'ru_RU',
PHP,
        'label' => 'interests',
    ],
    [
        'needle' => <<<'PHP'
            'type' => 'adgeolocation',
            'q' => $query,
PHP,
        'replace' => <<<'PHP'
            'type' => 'adgeolocation',
            'q' => $query,
            'locale' => 'ru_RU',
PHP,
        'label' => 'geo',
    ],
    [
        'needle' => <<<'PHP'
            'q' => $query,
            'limit_type' => 'behaviors',
PHP,
        'replace' => <<<'PHP'
            'q' => $query,
            'locale' => 'ru_RU',
            'limit_type' => 'behaviors',
PHP,
        'label' => 'behaviors',
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
    $anchor=<<<'PHP'
    public function searchInterests(string $query, int $limit = 25): array
PHP;
    $replacement=<<<'PHP'
    /* REMASK_TARGETING_RU_LOCALE_V1 */
    public function searchInterests(string $query, int $limit = 25): array
PHP;
    if(strpos($service,$anchor)===false){fwrite(STDERR,"[targeting-ru] marker anchor missing\n");exit(244);}
    $service=str_replace($anchor,$replacement,$service,$n);
}

file_put_contents($servicePath,$service);
fwrite(STDERR,"[targeting-ru] GEO / Interests / Behaviors locale=ru_RU enabled\n");
