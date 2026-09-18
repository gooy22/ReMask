<?php
$servicePath='/var/www/html/classes/MetaAdsService.php';
$endpointPath='/var/www/html/ajax/metaTargetingSearch.php';
$service=file_get_contents($servicePath);
$endpoint=file_get_contents($endpointPath);
if($service===false||$endpoint===false){fwrite(STDERR,"[behaviors-backend] read failed\n");exit(211);}

if(strpos($service,'REMASK_BEHAVIOR_SEARCH_V1')===false){
    $anchor=<<<'PHP'
    public function searchLocations(string $query, array $locationTypes = ['country', 'region', 'city'], int $limit = 25): array
PHP;
    $method=<<<'PHP'
    /* REMASK_BEHAVIOR_SEARCH_V1 */
    public function searchBehaviors(string $accountId, string $query, int $limit = 25): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $query = trim($query);
        if ($query === '') throw new InvalidArgumentException('Behavior search query cannot be empty.');
        return $this->client->get("{$accountId}/targetingsearch", [
            'q' => $query,
            'limit_type' => 'behaviors',
            'fields' => 'id,name,audience_size_upper_bound,type,path',
            'limit' => min(max($limit, 1), 100),
        ]);
    }

PHP;
    if(strpos($service,$anchor)===false){fwrite(STDERR,"[behaviors-backend] service anchor missing\n");exit(212);}
    $service=str_replace($anchor,$method.$anchor,$service,$n);
    if($n!==1){fwrite(STDERR,"[behaviors-backend] service count=$n\n");exit(213);}
}

if(strpos($endpoint,"'behavior', 'behaviors'")===false){
    $old=<<<'PHP'
        'interest', 'interests' => $service->searchInterests($query, $limit),
        'location', 'locations', 'geo' => $service->searchLocations($query, is_array($input['location_types'] ?? null) ? $input['location_types'] : ['country', 'region', 'city'], $limit),
        default => throw new InvalidArgumentException('type must be interests or locations'),
PHP;
    $new=<<<'PHP'
        'interest', 'interests' => $service->searchInterests($query, $limit),
        'behavior', 'behaviors' => $service->searchBehaviors(trim((string)($input['account_id'] ?? '')), $query, $limit),
        'location', 'locations', 'geo' => $service->searchLocations($query, is_array($input['location_types'] ?? null) ? $input['location_types'] : ['country', 'region', 'city'], $limit),
        default => throw new InvalidArgumentException('type must be interests, behaviors or locations'),
PHP;
    if(strpos($endpoint,$old)===false){fwrite(STDERR,"[behaviors-backend] endpoint anchor missing\n");exit(214);}
    $endpoint=str_replace($old,$new,$endpoint,$n);
    if($n!==1){fwrite(STDERR,"[behaviors-backend] endpoint count=$n\n");exit(215);}
}

file_put_contents($servicePath,$service);
file_put_contents($endpointPath,$endpoint);
fwrite(STDERR,"[behaviors-backend] ready\n");
