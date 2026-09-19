<?php
/** v104: strict funding review and generated-Creative ownership. */
$root = rtrim((string)(getenv('REMASK_RUNTIME_ROOT') ?: '/var/www/html'), '/');
$path = $root . '/classes/MetaEndpoint.php';
$servicePath = $root . '/classes/MetaAdsService.php';
$healthPath = $root . '/health.php';
$versionPath = $root . '/version.php';
$source = file_get_contents($path);
if ($source === false) {
    fwrite(STDERR, "[v104-launch-safety] could not read MetaEndpoint.php\n");
    exit(401);
}

if (strpos($source, "MetaFundingGuard.php") === false) {
    $requireAnchor = "require_once __DIR__ . '/MetaLaunchAssetValidator.php';";
    if (strpos($source, $requireAnchor) === false) {
        fwrite(STDERR, "[v104-launch-safety] funding guard require anchor missing\n");
        exit(406);
    }
    $source = str_replace(
        $requireAnchor,
        $requireAnchor . "\nrequire_once __DIR__ . '/MetaFundingGuard.php';",
        $source,
        $requireCount
    );
    if ($requireCount !== 1) {
        fwrite(STDERR, "[v104-launch-safety] funding guard require count={$requireCount}\n");
        exit(407);
    }
}

if (strpos($source, 'REMASK_REQUIRED_FUNDING_REVIEW_V1') === false) {
    $old = <<<'PHP_CODE'
                // Funding is useful context, but it is not a compatibility blocker.
                // Never turn a bulk Launch Review into N extra network calls just to
                // discover payment metadata. The explicit CHECK FUNDING action primes
                // this cache; Review only consumes what is already known.
                $funding = self::peekCachedAsset($profile, 'funding', $accountId);
                if ($funding !== null) {
                    $row['funding'] = $funding;
                    if (!empty($funding['expired_funding_source_details'])) {
                        $row['warnings'][] = 'Meta reports an expired funding source.';
                    } elseif (empty($funding['funding_source']) && empty($funding['funding_source_details']) && empty($funding['is_prepay_account'])) {
                        $row['warnings'][] = 'Funding source is UNKNOWN from the cached official API response.';
                    }
                    if (!empty($funding['_cache']['stale'])) {
                        $row['warnings'][] = 'Funding snapshot is stale; run CHECK FUNDING to refresh it.';
                    }
                }
PHP_CODE;
    $new = <<<'PHP_CODE'
                /* REMASK_REQUIRED_FUNDING_REVIEW_V1 */
                // A Launch may reuse a still-fresh funding snapshot, but an absent or
                // expired cache is loaded from Meta before any mutation is queued.
                try {
                    $funding = self::cachedAsset($profile, 'funding', $accountId, $force);
                } catch (Throwable $fundingError) {
                    throw new RuntimeException('PAYMENT CHECK FAILED: ' . $fundingError->getMessage(), 0, $fundingError);
                }
                $row['funding'] = $funding;

                MetaFundingGuard::assertLaunchReady($funding);
PHP_CODE;
    if (strpos($source, $old) === false) {
        fwrite(STDERR, "[v104-launch-safety] funding review anchor missing\n");
        exit(402);
    }
    $source = str_replace($old, $new, $source, $count);
    if ($count !== 1) {
        fwrite(STDERR, "[v104-launch-safety] funding replacement count={$count}\n");
        exit(403);
    }
}

$source = str_replace("'funding_mode' => 'cached_only'", "'funding_mode' => 'required_fresh_cache'", $source, $modeCount);
if ($modeCount !== 2 && strpos($source, "'funding_mode' => 'required_fresh_cache'") === false) {
    fwrite(STDERR, "[v104-launch-safety] funding mode replacement failed\n");
    exit(404);
}

if (file_put_contents($path, $source) === false) {
    fwrite(STDERR, "[v104-launch-safety] could not write MetaEndpoint.php\n");
    exit(405);
}

$service = file_get_contents($servicePath);
if ($service === false) {
    fwrite(STDERR, "[v104-launch-safety] could not read MetaAdsService.php\n");
    exit(408);
}
if (strpos($service, 'generatedStoryCreativeParams') === false) {
    $service = str_replace(
        "MetaOfficialFields::officialParams('creative', \$creative)",
        "MetaOfficialFields::generatedStoryCreativeParams(\$creative)",
        $service,
        $creativeCount
    );
    if ($creativeCount !== 3) {
        fwrite(STDERR, "[v104-launch-safety] generated Creative replacement count={$creativeCount}\n");
        exit(409);
    }
}
if (file_put_contents($servicePath, $service) === false) {
    fwrite(STDERR, "[v104-launch-safety] could not write MetaAdsService.php\n");
    exit(410);
}

$health = file_get_contents($healthPath);
if ($health === false) {
    fwrite(STDERR, "[v104-launch-safety] could not read health.php\n");
    exit(411);
}
$health = str_replace(
    '$ok = $dataWritable && $databaseOk && $credentialsOk;',
    '$ok = $dataWritable && $databaseOk && $credentialsOk && $queueOperational;',
    $health,
    $healthCount
);
if ($healthCount !== 1 && strpos($health, '&& $queueOperational;') === false) {
    fwrite(STDERR, "[v104-launch-safety] health queue anchor missing\n");
    exit(412);
}
if (file_put_contents($healthPath, $health) === false) {
    fwrite(STDERR, "[v104-launch-safety] could not write health.php\n");
    exit(413);
}

$version = <<<'PHP_CODE'
<?php
header('Content-Type: text/plain; charset=utf-8');
$release = trim((string)(getenv('REMASK_RELEASE_VERSION') ?: 'v104'));
$revision = trim((string)(getenv('REMASK_DEPLOY_REV') ?: ''));
echo 'ReMask ' . $release . ($revision !== '' ? ' (' . $revision . ')' : '');
PHP_CODE;
if (file_put_contents($versionPath, $version . "\n") === false) {
    fwrite(STDERR, "[v104-launch-safety] could not write version.php\n");
    exit(414);
}

fwrite(STDERR, "[v104-launch-safety] strict funding and Creative ownership installed\n");
