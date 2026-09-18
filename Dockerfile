FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev xz-utils ca-certificates \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

COPY .deploy/clean-preview-valid/runtime.b64.* /tmp/remask-parts/
COPY railway-launch-overlay.php /tmp/railway-launch-overlay.php
COPY railway-launch-job-ui-overlay.php /tmp/railway-launch-job-ui-overlay.php
COPY railway-launch-flow-overlay.php /tmp/railway-launch-flow-overlay.php
COPY railway-check-account-session-overlay.php /tmp/railway-check-account-session-overlay.php
COPY railway-profile-session-guard-overlay.php /tmp/railway-profile-session-guard-overlay.php
COPY railway-workspace-session-integrity-overlay.php /tmp/railway-workspace-session-integrity-overlay.php
COPY railway-meta-session-context-overlay.php /tmp/railway-meta-session-context-overlay.php
COPY railway-workspace-sync-fix-overlay.php /tmp/railway-workspace-sync-fix-overlay.php
COPY railway-sync-smoke.php /tmp/railway-sync-smoke.php
COPY railway-worker-overlay.php /tmp/railway-worker-overlay.php
COPY railway-retry-overlay.php /tmp/railway-retry-overlay.php
COPY railway-targeting-autocomplete-overlay.php /tmp/railway-targeting-autocomplete-overlay.php
COPY railway-selection-persistence-overlay.php /tmp/railway-selection-persistence-overlay.php
COPY railway-profile-error-fix-overlay.php /tmp/railway-profile-error-fix-overlay.php
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    php /tmp/railway-launch-overlay.php; \
    php /tmp/railway-launch-job-ui-overlay.php; \
    php /tmp/railway-launch-flow-overlay.php; \
    php /tmp/railway-check-account-session-overlay.php; \
    php /tmp/railway-profile-session-guard-overlay.php; \
    php /tmp/railway-workspace-session-integrity-overlay.php; \
    php /tmp/railway-meta-session-context-overlay.php; \
    php /tmp/railway-workspace-sync-fix-overlay.php; \
    php /tmp/railway-worker-overlay.php; \
    php /tmp/railway-retry-overlay.php; \
    php /tmp/railway-targeting-autocomplete-overlay.php; \
    php /tmp/railway-selection-persistence-overlay.php; \
    php /tmp/railway-profile-error-fix-overlay.php; \
    php -r '$allowedRaw=["/var/www/html/classes/MetaApiClient.php"=>true,"/var/www/html/classes/FbRequests.php"=>true,"/var/www/html/classes/ProxyHealthService.php"=>true]; $allowedLegacy=["/var/www/html/ajax/payUnsettled.php"=>true,"/var/www/html/ajax/policyAppeal.php"=>true,"/var/www/html/ajax/disapproveAppeal.php"=>true]; $violations=[]; foreach(["/var/www/html/ajax","/var/www/html/classes","/var/www/html/bin"] as $root){$it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,FilesystemIterator::SKIP_DOTS)); foreach($it as $fi){if(!$fi->isFile()||$fi->getExtension()!=="php")continue;$path=$fi->getPathname();$s=file_get_contents($path);if((str_contains($s,"graph.facebook.com")||str_contains($s,"curl_init("))&&!isset($allowedRaw[$path]))$violations[]="raw-meta-transport:".$path;if($fi->getFilename()!=="FbRequests.php"&&preg_match("/new\\s+FbRequests\\s*\\(/",$s)&&!isset($allowedLegacy[$path]))$violations[]="legacy-fbrequests-ref:".$path;}} if($violations){fwrite(STDERR,"Meta transport invariant failed: ".implode(", ",$violations)."\\n");exit(91);} fwrite(STDERR,"[transport-invariant] canonical Graph transport enforced; legacy browser transport limited to payment/appeal endpoints\\n");'; \
    php -r '$pairs=[["/var/www/html/scripts/accounts.js","",24000],["/var/www/html/menu.php","",18000]]; foreach($pairs as [$file,$needle,$len]){$s=file_get_contents($file);fwrite(STDERR,"[accounts-js-inspect] FILE=".$file."\\n".substr($s,0,$len)."\\n");}'; \
    grep -Rni -B 24 -A 60 -E 'No media is selected for this RK|Funding snapshot is stale|media_plan|upload_image|upload_video' /var/www/html/ajax /var/www/html/classes /var/www/html/scripts/launch.js || true; \
    php -l /var/www/html/classes/RemaskProxy.php; \
    php -l /var/www/html/classes/MetaApiClient.php; \
    php -l /var/www/html/classes/MetaAdsService.php; \
    php -l /var/www/html/classes/MetaEndpoint.php; \
    php -l /var/www/html/ajax/checkAccount.php; \
    php -l /var/www/html/ajax/metaProfileManager.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    php -l /var/www/html/bin/remask-worker.php; \
    php -l /var/www/html/ajax/metaWorkerStatus.php; \
    php -l /var/www/html/ajax/metaJobRetry.php; \
    grep -q 'REMASK_SYNC_ERROR_CLASSIFIER_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'Meta request timeout after' /var/www/html/scripts/workspace.js; \
    grep -Fq "\$('workspaceActions').disabled=n===0;" /var/www/html/scripts/workspace.js; \
    grep -q 'clear_session' /var/www/html/ajax/metaProfileManager.php; \
    grep -q 'profileSaveJson' /var/www/html/scripts/workspace.js; \
    if grep -Fq '\\`' /var/www/html/scripts/workspace.js; then echo 'workspace-invalid-backtick' >&2; exit 92; fi; \
    grep -q 'error_id' /var/www/html/ajax/metaProfileManager.php; \
    ! grep -q 'Добавь Cookies JSON текущей FB-сессии' /var/www/html/scripts/workspace.js; \
    ! grep -Fq "value.trim()||'[]'" /var/www/html/scripts/workspace.js; \
    grep -q 'bak.hierarchy-safe' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'bak.proxy-safe' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'existing->cookies' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'setSessionCookies' /var/www/html/classes/MetaApiClient.php; \
    grep -q 'setSessionCookies($account->getCurlCookies())' /var/www/html/classes/MetaEndpoint.php; \
    grep -q 'session_used' /var/www/html/ajax/checkAccount.php; \
    grep -q 'new MetaApiClient' /var/www/html/ajax/checkAccount.php; \
    grep -q "network_identity'=>'profile_bound'" /var/www/html/ajax/checkAccount.php; \
    ! grep -q 'curl_init' /var/www/html/ajax/checkAccount.php; \
    ! grep -q 'graph.facebook.com' /var/www/html/ajax/checkAccount.php; \
    grep -q 'direct_ad_accounts_with_optional_business_enrichment' /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq 'cachedPreflight($profile, true)' /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq "cachedAsset(\$profile, 'pages', '', true)" /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq "cachedAsset(\$profile, 'businesses', '', true)" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'token_status'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'proxy_status'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'pages_count'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'network_identity' => 'profile_bound'" /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq "p.proxy_configured && ps!==''&&ps!=='LIVE'" /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_BM_OWNED_CLIENT_V2' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_DIRECT_RK_FUNDING_V2' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_DIRECT_FUNDING_SNAPSHOT_V2' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'RemaskProxy::fromSemicolonString' /var/www/html/ajax/checkAccount.php; \
    ! test -f /var/www/html/ajax/metaSyncProbe.php; \
    ! test -f /var/www/html/remask-session-recover.php; \
    ! test -f /var/www/html/bin/remask-sync-smoke.php; \
    test -f /var/www/html/scripts/targeting-autocomplete.js; \
    test -f /var/www/html/scripts/selection-persistence.js; \
    ! grep -q 'MutationObserver' /var/www/html/scripts/selection-persistence.js; \
    grep -q 'targeting-autocomplete.js' /var/www/html/launch.php; \
    grep -q 'jobActionSelect' /var/www/html/launch.php; \
    grep -q 'REMASK_AUTO_OPEN_RECENT_JOB_V1' /var/www/html/launch.php; \
    grep -q 'REMASK_JOB_ACTION_MENU_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_SINGLE_CLICK_LAUNCH_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_AUTO_NAMING_V1' /var/www/html/scripts/launch.js; \
    grep -q 'Missing: ' /var/www/html/scripts/launch.js; \
    grep -q 'Launch blocked: one or more selected RK failed Launch Review' /var/www/html/scripts/launch.js; \
    grep -q 'launch.js?v=20260918-single-click-v74' /var/www/html/launch.php; \
    for f in /var/www/html/index.php /var/www/html/workspace.php /var/www/html/launch.php /var/www/html/campaigns.php /var/www/html/adsets.php /var/www/html/accounts.php; do [ ! -f "$f" ] || ! grep -q 'selection-persistence.js' "$f"; done; \
    grep -q 'accounts.js?v=20260918-accounts-v69' /var/www/html/accounts.php; \
    grep -q "'network_identity' => 'profile_bound'" /var/www/html/classes/MetaEndpoint.php; \
    grep -q "'direct_fallback' => false" /var/www/html/classes/MetaEndpoint.php; \
    ! grep -q 'data-remask-fp-action="1"' /var/www/html/scripts/workspace.js; \
    ! grep -q 'Добавить FP' /var/www/html/scripts/workspace.js; \
    ! test -f /var/www/html/ajax/metaPageHelper.php; \
    ! test -f /var/www/html/scripts/page-helper.js; \
    ! grep -q 'page-helper.js' /var/www/html/workspace.php; \
    grep -Fq 'MetaEndpoint::cachedAsset($profile, $resource' /var/www/html/ajax/metaAssetManager.php; \
    grep -q "resource:'pages'" /var/www/html/scripts/workspace.js; \
    ! grep -q 'hierarchy-autosync.js' /var/www/html/workspace.php; \
    mkdir -p /var/www/html/health /var/lib/remask /var/lib/remask/jobs /var/lib/remask/bundles /var/lib/remask/meta-cache /var/lib/remask/job-media; \
    if [ ! -f /var/www/html/health/index.php ]; then printf '%s\n' '<?php http_response_code(200); header("Content-Type: application/json"); echo json_encode(["ok"=>true,"service":"remask","rev"=>getenv("REMASK_DEPLOY_REV")]);' > /var/www/html/health/index.php; fi; \
    [ -f /var/www/html/index.php ]; \
    [ -f /var/www/html/launch.php ]; \
    cp /tmp/docker-start.sh /var/www/html/docker-start.sh; \
    [ -f /var/lib/remask/accounts.json ] || printf '[]\n' > /var/lib/remask/accounts.json; \
    [ -f /var/lib/remask/bundles.json ] || printf '[]\n' > /var/lib/remask/bundles.json; \
    chown -R www-data:www-data /var/lib/remask /var/www/html; \
    chmod 700 /var/lib/remask; \
    chmod +x /var/www/html/docker-start.sh;

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
