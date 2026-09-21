<?php
/**
 * v118: Use ad-account scoped targetingsearch for all audience autocomplete.
 * Avoids global /search app-context failures ("Error loading application")
 * and binds lookup to the same RK context used for delivery.
 */
$root='/var/www/html';
$servicePath=$root.'/classes/MetaAdsService.php';
$endpointPath=$root.'/ajax/metaTargetingSearch.php';
$launchJsPath=$root.'/scripts/launch.js';

foreach([$servicePath,$endpointPath,$launchJsPath] as $p){
    if(!is_file($p)){fwrite(STDERR,"[account-targeting-v118] missing $p\n");exit(461);}
}
$service=file_get_contents($servicePath);
$endpoint=file_get_contents($endpointPath);
$js=file_get_contents($launchJsPath);
if($service===false||$endpoint===false||$js===false){fwrite(STDERR,"[account-targeting-v118] read failed\n");exit(462);}

if(strpos($service,'REMASK_ACCOUNT_TARGETING_SEARCH_V1')===false){
    $anchor=<<<'PHP_CODE'
    public function searchBehaviors(string $accountId, string $query, int $limit = 25): array
PHP_CODE;
    $method=<<<'PHP_CODE'
    /* REMASK_ACCOUNT_TARGETING_SEARCH_V1 */
    public function searchAccountTargeting(
        string $accountId,
        string $query,
        array $whitelistedTypes,
        int $limit = 25,
        ?string $limitType = null
    ): array {
        $accountId = self::normalizeAccountId($accountId);
        $query = trim($query);
        if ($query === '') throw new InvalidArgumentException('Targeting search query cannot be empty.');

        $types = array_values(array_unique(array_filter(array_map(
            static fn($v) => trim((string)$v),
            $whitelistedTypes
        ))));
        if ($types === []) throw new InvalidArgumentException('Targeting whitelisted_types cannot be empty.');

        $params = [
            'q' => $query,
            'whitelisted_types' => json_encode($types, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE),
            'fields' => 'id,key,name,raw_name,type,path,parent,description,audience_size_lower_bound,audience_size_upper_bound',
            'limit' => min(max($limit, 1), 100),
        ];
        if ($limitType !== null && trim($limitType) !== '') {
            $params['limit_type'] = trim($limitType);
        }

        return $this->client->get("{$accountId}/targetingsearch", $params);
    }

PHP_CODE;
    if(strpos($service,$anchor)===false){fwrite(STDERR,"[account-targeting-v118] service anchor missing\n");exit(463);}
    $service=str_replace($anchor,$method.$anchor,$service,$n);
    if($n!==1){fwrite(STDERR,"[account-targeting-v118] service patch count=$n\n");exit(464);}
}

if(strpos($endpoint,'REMASK_ACCOUNT_TARGETING_DISPATCH_V1')===false){
    $pattern='#\$result\s*=\s*match\s*\(\$type\)\s*\{.*?default\s*=>\s*throw\s+new\s+InvalidArgumentException\([^;]+;\s*\};#s';
    if(!preg_match($pattern,$endpoint,$m)){
        fwrite(STDERR,"[account-targeting-v118] endpoint match block missing\n");
        exit(465);
    }
    $dispatch=<<<'PHP_CODE'
/* REMASK_ACCOUNT_TARGETING_DISPATCH_V1 */
$accountId = trim((string)($input['account_id'] ?? ''));
if ($accountId === '') {
    throw new RuntimeException('Targeting search requires an accessible Meta ad account.');
}

$result = match ($type) {
    'interest', 'interests' => $service->searchAccountTargeting(
        $accountId,
        $query,
        ['interests'],
        $limit,
        'interests'
    ),
    'behavior', 'behaviors' => $service->searchAccountTargeting(
        $accountId,
        $query,
        ['behaviors'],
        $limit,
        'behaviors'
    ),
    'language', 'languages', 'locale', 'locales' => $service->searchAccountTargeting(
        $accountId,
        $query,
        ['locales'],
        $limit,
        null
    ),
    'location', 'locations', 'geo' => $service->searchAccountTargeting(
        $accountId,
        $query,
        ['countries','regions','cities','zips'],
        $limit,
        null
    ),
    default => throw new InvalidArgumentException('type must be interests, behaviors, languages or locations'),
};

if (in_array($type, ['location','locations','geo'], true)) {
    $rows = is_array($result['data'] ?? null) ? $result['data'] : [];
    $normalized = [];
    foreach ($rows as $row) {
        if (!is_array($row)) continue;
        $rawType = strtolower(trim((string)($row['type'] ?? '')));
        $typeMap = [
            'countries'=>'country',
            'country'=>'country',
            'regions'=>'region',
            'region'=>'region',
            'cities'=>'city',
            'city'=>'city',
            'zips'=>'zip',
            'zip'=>'zip',
        ];
        $row['type'] = $typeMap[$rawType] ?? $rawType;
        if (!isset($row['key']) || trim((string)$row['key']) === '') {
            $row['key'] = (string)($row['id'] ?? '');
        }
        $normalized[] = $row;
    }
    $result['data'] = $normalized;
}
PHP_CODE;
    $endpoint=preg_replace($pattern,$dispatch,$endpoint,1,$n) ?? $endpoint;
    if($n!==1){fwrite(STDERR,"[account-targeting-v118] dispatch patch count=$n\n");exit(466);}
}

/* Launch should pass the selected RK instead of forcing backend discovery. */
$oldGeo="...formPost({profile: state.profile, type, q: query, limit: 25}),";
$newGeo="...formPost({profile: state.profile, account_id:(selectedAccountIds()[0] || state.accounts?.[0]?.id || ''), type, q: query, limit: 25}),";
if(strpos($js,$oldGeo)!==false){
    $js=str_replace($oldGeo,$newGeo,$js,$n);
}
$oldLang="...formPost({profile:state.profile,type:'languages',q,limit:50}),";
$newLang="...formPost({profile:state.profile,account_id:(selectedAccountIds()[0] || state.accounts?.[0]?.id || ''),type:'languages',q,limit:50}),";
if(strpos($js,$oldLang)!==false){
    $js=str_replace($oldLang,$newLang,$js,$n);
}

file_put_contents($servicePath,$service);
file_put_contents($endpointPath,$endpoint);
file_put_contents($launchJsPath,$js);
fwrite(STDERR,"[account-targeting-v118] all audience autocomplete uses act_<RK>/targetingsearch\n");
