<?php
/**
 * v109: Accountless targeting search for Creative Library.
 * If UI does not provide a profile, use stored ReMask profiles as a hidden
 * transport pool. Cache only a profile that has completed a real targeting call.
 */
$root='/var/www/html';
$endpoint=$root.'/ajax/metaTargetingSearch.php';
if(!is_file($endpoint)){fwrite(STDERR,"[creative-targeting-v109] endpoint missing\n");exit(371);}
$src=file_get_contents($endpoint);
if($src===false){fwrite(STDERR,"[creative-targeting-v109] read failed\n");exit(372);}

if(strpos($src,'REMASK_ACCOUNTLESS_TARGETING_V1')===false){
    $reqAnchor="require_once __DIR__ . '/../checkpassword.php';";
    if(strpos($src,$reqAnchor)!==false && strpos($src,'AccountStoreFactory.php')===false){
        $src=str_replace(
            $reqAnchor,
            $reqAnchor."\nrequire_once __DIR__ . '/../classes/AccountStoreFactory.php';\nrequire_once __DIR__ . '/../classes/FbAccount.php';\nrequire_once __DIR__ . '/../classes/MetaEndpoint.php';",
            $src,
            $n
        );
    }

    $profilePattern='/\$profile\s*=\s*trim\(\(string\)\(\$input\[\'profile\'\]\s*\?\?\s*\'\'\)\);/';
    if(!preg_match($profilePattern,$src,$m,PREG_OFFSET_CAPTURE)){
        // Older endpoint may read request directly.
        $profilePattern='/\$profile\s*=\s*trim\(\(string\)\(\$_(?:POST|REQUEST)\[\'profile\'\]\s*\?\?\s*\'\'\)\);/';
        if(!preg_match($profilePattern,$src,$m,PREG_OFFSET_CAPTURE)){
            fwrite(STDERR,"[creative-targeting-v109] profile anchor missing\n");
            exit(373);
        }
    }
    $match=$m[0][0];
    $replacement=$match."\n".
"    /* REMASK_ACCOUNTLESS_TARGETING_V1 */\n".
"    /* REMASK_ACCOUNTLESS_TRANSPORT_POOL_V4 */\n".
"    \$remaskAccountlessTargeting = (\$profile === '');\n".
"    \$remaskTargetingCandidateNames = [];\n".
"    \$remaskTargetingStored = [];\n".
"    \$remaskTransportCacheFile = '';\n".
"    if (\$remaskAccountlessTargeting) {\n".
"        \$store = AccountStoreFactory::create(ACCOUNTSFILENAME);\n".
"        foreach ((array)\$store->deserialize() as \$candidate) {\n".
"            if (!\$candidate instanceof FbAccount) continue;\n".
"            \$candidateName = trim((string)\$candidate->name);\n".
"            if (\$candidateName === '' || trim((string)\$candidate->token) === '') continue;\n".
"            \$remaskTargetingStored[\$candidateName] = \$candidate;\n".
"        }\n".
"        if (\$remaskTargetingStored === []) throw new RuntimeException('No stored Meta profile is available for targeting search.');\n".
"\n".
"        \$dataRoot = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');\n".
"        \$transportCacheDir = \$dataRoot . '/meta-cache';\n".
"        if (!is_dir(\$transportCacheDir)) @mkdir(\$transportCacheDir, 0770, true);\n".
"        \$remaskTransportCacheFile = \$transportCacheDir . '/creative-targeting-transport.json';\n".
"        \$transportCache = is_file(\$remaskTransportCacheFile)\n".
"            ? json_decode((string)@file_get_contents(\$remaskTransportCacheFile), true)\n".
"            : [];\n".
"        if (!is_array(\$transportCache)) \$transportCache = [];\n".
"        \$cachedName = trim((string)(\$transportCache['profile'] ?? ''));\n".
"        \$cachedAt = (int)(\$transportCache['verified_at'] ?? 0);\n".
"        \$cachedSource = trim((string)(\$transportCache['source'] ?? ''));\n".
"        \$failedMap = is_array(\$transportCache['failed'] ?? null) ? \$transportCache['failed'] : [];\n".
"\n".
"        if (\$cachedSource === 'successful_targeting_call'\n".
"            && \$cachedName !== ''\n".
"            && isset(\$remaskTargetingStored[\$cachedName])\n".
"            && (time() - \$cachedAt) < 900) {\n".
"            \$remaskTargetingCandidateNames[] = \$cachedName;\n".
"        }\n".
"        foreach (\$remaskTargetingStored as \$candidateName => \$candidate) {\n".
"            \$failedAt = (int)(\$failedMap[\$candidateName] ?? 0);\n".
"            if (\$failedAt > 0 && (time() - \$failedAt) < 120) continue;\n".
"            if (\$candidate->proxy === null && !in_array(\$candidateName, \$remaskTargetingCandidateNames, true)) {\n".
"                \$remaskTargetingCandidateNames[] = \$candidateName;\n".
"            }\n".
"        }\n".
"        foreach (array_keys(\$remaskTargetingStored) as \$candidateName) {\n".
"            \$failedAt = (int)(\$failedMap[\$candidateName] ?? 0);\n".
"            if (\$failedAt > 0 && (time() - \$failedAt) < 120) continue;\n".
"            if (!in_array(\$candidateName, \$remaskTargetingCandidateNames, true)) \$remaskTargetingCandidateNames[] = \$candidateName;\n".
"        }\n".
"        if (\$remaskTargetingCandidateNames === []) {\n".
"            // All transports failed very recently. Retry them once instead of getting stuck forever.\n".
"            \$remaskTargetingCandidateNames = array_keys(\$remaskTargetingStored);\n".
"        }\n".
"        \$profile = (string)(\$remaskTargetingCandidateNames[0] ?? '');\n".
"        if (\$profile === '') throw new RuntimeException('No Meta profile transport is available for targeting search.');\n".
"    }";
    $src=preg_replace($profilePattern,$replacement,$src,1,$count) ?? $src;
    if($count!==1){fwrite(STDERR,"[creative-targeting-v109] profile patch count=$count\n");exit(374);}

    if(strpos($src,'REMASK_ACCOUNTLESS_BEHAVIOR_ACCOUNT_V3')===false){
        $serviceAnchor='$service = MetaEndpoint::serviceForAccountName($profile);';
        if(strpos($src,$serviceAnchor)===false){
            fwrite(STDERR,"[creative-targeting-v109] service anchor missing\n");
            exit(375);
        }
        $serviceReplacement=<<<'PHP_CODE'
if (!empty($remaskAccountlessTargeting)) {
    /* REMASK_TARGETING_RUNTIME_FAILOVER_V1 */
    $service = new class($remaskTargetingCandidateNames, $remaskTransportCacheFile) {
        private array $candidates;
        private string $cacheFile;

        public function __construct(array $candidates, string $cacheFile)
        {
            $this->candidates = array_values(array_filter(array_map('strval', $candidates)));
            $this->cacheFile = $cacheFile;
        }

        private static function retryable(Throwable $e): bool
        {
            $m = strtolower(trim((string)$e->getMessage()));
            if ($m === '') return false;
            foreach ([
                'proxy', 'transport error', 'connection', 'timed out', 'timeout',
                'could not connect', 'couldn\'t connect', 'empty reply', 'recv failure',
                'connection reset', 'connection closed', 'failed to connect',
                'oauth', 'error loading application', 'invalid oauth', 'code 190'
            ] as $needle) {
                if (str_contains($m, $needle)) return true;
            }
            return false;
        }

        private function cacheRead(): array
        {
            if ($this->cacheFile === '' || !is_file($this->cacheFile)) return [];
            $data = json_decode((string)@file_get_contents($this->cacheFile), true);
            return is_array($data) ? $data : [];
        }

        private function cacheFailure(string $profile): void
        {
            if ($this->cacheFile === '') return;
            $data = $this->cacheRead();
            $failed = is_array($data['failed'] ?? null) ? $data['failed'] : [];
            $failed[$profile] = time();
            $data['failed'] = $failed;
            @file_put_contents($this->cacheFile, json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
        }

        private function cacheSuccess(string $profile): void
        {
            if ($this->cacheFile === '') return;
            $data = $this->cacheRead();
            $failed = is_array($data['failed'] ?? null) ? $data['failed'] : [];
            unset($failed[$profile]);
            @file_put_contents($this->cacheFile, json_encode([
                'profile' => $profile,
                'verified_at' => time(),
                'source' => 'successful_targeting_call',
                'failed' => $failed,
            ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
        }

        private function run(callable $fn): array
        {
            $lastError = null;
            foreach ($this->candidates as $profile) {
                try {
                    $real = MetaEndpoint::serviceForAccountName($profile);
                    $result = $fn($real);
                    $this->cacheSuccess($profile);
                    return is_array($result) ? $result : [];
                } catch (Throwable $e) {
                    $lastError = $e;
                    if (!self::retryable($e)) throw $e;
                    $this->cacheFailure($profile);
                }
            }
            if ($lastError instanceof Throwable) throw $lastError;
            throw new RuntimeException('No Meta targeting transport candidate is available.');
        }

        public function searchAccountTargeting(string $accountId, string $query, array $whitelistedTypes, int $limit = 25, ?string $limitType = null): array
        {
            return $this->run(fn($s) => $s->searchAccountTargeting($accountId, $query, $whitelistedTypes, $limit, $limitType));
        }

        public function searchInterests(string $query, int $limit = 25): array
        {
            return $this->run(fn($s) => $s->searchInterests($query, $limit));
        }

        public function searchLocations(string $query, array $locationTypes = ['country','region','city'], int $limit = 25): array
        {
            return $this->run(fn($s) => $s->searchLocations($query, $locationTypes, $limit));
        }

        public function searchBehaviors(string $accountId, string $query, int $limit = 25): array
        {
            return $this->run(fn($s) => $s->searchBehaviors($accountId, $query, $limit));
        }

        public function listAdAccounts(int $limit = 1): array
        {
            return $this->run(fn($s) => $s->listAdAccounts($limit));
        }
    };
} else {
    $service = MetaEndpoint::serviceForAccountName($profile);
}

/* REMASK_ACCOUNTLESS_TARGETING_ACCOUNT_V2 */
$remaskTargetingType = strtolower(trim((string)($input['type'] ?? '')));
if (in_array($remaskTargetingType, ['behavior','behaviors'], true)
    && trim((string)($input['account_id'] ?? '')) === '') {
    $accountRows = $service->listAdAccounts(1);
    $targetingAccountId = '';
    foreach ((array)($accountRows['data'] ?? []) as $row) {
        if (!is_array($row)) continue;
        $candidate = preg_replace('/^act_/i', '', trim((string)($row['id'] ?? ''))) ?? '';
        if ($candidate !== '' && preg_match('/^\\d+$/', $candidate)) {
            $targetingAccountId = $candidate;
            break;
        }
    }
    if ($targetingAccountId === '') {
        throw new RuntimeException('No Meta ad account is available for Behaviors search.');
    }
    $input['account_id'] = $targetingAccountId;
}
PHP_CODE;
        $src=str_replace($serviceAnchor,$serviceReplacement,$src,$serviceCount);
        if($serviceCount!==1){fwrite(STDERR,"[creative-targeting-v109] behavior account patch count=$serviceCount\n");exit(376);}
    }

    file_put_contents($endpoint,$src);
}
fwrite(STDERR,"[creative-targeting-v109] real-call targeting failover + behavior-only account context enabled\n");
