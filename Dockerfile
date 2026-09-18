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
COPY railway-page-helper-overlay.php /tmp/railway-page-helper-overlay.php
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    php /tmp/railway-launch-overlay.php; \
    php /tmp/railway-check-account-session-overlay.php; \
    php /tmp/railway-profile-session-guard-overlay.php; \
    php /tmp/railway-workspace-session-integrity-overlay.php; \
    php /tmp/railway-meta-session-context-overlay.php; \
    php /tmp/railway-workspace-sync-fix-overlay.php; \
    php /tmp/railway-worker-overlay.php; \
    mkdir -p /var/www/html/bin; cp /tmp/railway-sync-smoke.php /var/www/html/bin/remask-sync-smoke.php; \
    php /tmp/railway-retry-overlay.php; \
    php /tmp/railway-targeting-autocomplete-overlay.php; \
    php /tmp/railway-selection-persistence-overlay.php; \
    php /tmp/railway-profile-error-fix-overlay.php; \
    php /tmp/railway-page-helper-overlay.php; \
    php -r '$s=file_get_contents("/var/www/html/scripts/workspace.js"); $p=strpos($s,"Добавить BM"); if($p===false){fwrite(STDERR,"[menu-source] Add BM marker missing\\n");}else{fwrite(STDERR,"[menu-source] ".substr($s,max(0,$p-2200),4400)."\\n");}'; \
    php -l /var/www/html/classes/RemaskProxy.php; \
    php -l /var/www/html/classes/MetaApiClient.php; \
    php -l /var/www/html/classes/MetaAdsService.php; \
    php -l /var/www/html/classes/MetaEndpoint.php; \
    php -l /var/www/html/ajax/checkAccount.php; \
    php -l /var/www/html/ajax/metaProfileManager.php; \
    php -l /var/www/html/ajax/metaPageHelper.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    php -l /var/www/html/bin/remask-worker.php; \
    php -l /var/www/html/ajax/metaWorkerStatus.php; \
    php -l /var/www/html/ajax/metaJobRetry.php; \
    php -l /var/www/html/bin/remask-sync-smoke.php; \
    grep -q 'REMASK_SYNC_ERROR_CLASSIFIER_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'clear_session' /var/www/html/ajax/metaProfileManager.php; \
    grep -q 'profileSaveJson' /var/www/html/scripts/workspace.js; \
    grep -q 'error_id' /var/www/html/ajax/metaProfileManager.php; \
    ! grep -q 'Добавь Cookies JSON текущей FB-сессии' /var/www/html/scripts/workspace.js; \
    ! grep -Fq "value.trim()||'[]'" /var/www/html/scripts/workspace.js; \
    grep -q 'bak.hierarchy-safe' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'bak.proxy-safe' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'existing->cookies' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'setSessionCookies' /var/www/html/classes/MetaApiClient.php; \
    grep -q 'setSessionCookies($account->getCurlCookies())' /var/www/html/classes/MetaEndpoint.php; \
    grep -q 'CURLOPT_COOKIE' /var/www/html/ajax/checkAccount.php; \
    grep -q 'session_used' /var/www/html/ajax/checkAccount.php; \
    grep -q 'rmx_check_graph_list_all' /var/www/html/ajax/checkAccount.php; \
    grep -q 'direct_ad_accounts_with_optional_business_enrichment' /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq "p.proxy_configured && ps!==''&&ps!=='LIVE'" /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_BM_OWNED_CLIENT_V2' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_DIRECT_RK_FUNDING_V2' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_DIRECT_FUNDING_SNAPSHOT_V2' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'RemaskProxy::fromSemicolonString' /var/www/html/ajax/checkAccount.php; \
    ! test -f /var/www/html/ajax/metaSyncProbe.php; \
    ! test -f /var/www/html/remask-session-recover.php; \
    test -f /var/www/html/scripts/targeting-autocomplete.js; \
    test -f /var/www/html/scripts/selection-persistence.js; \
    grep -q 'targeting-autocomplete.js' /var/www/html/launch.php; \
    grep -q 'selection-persistence.js' /var/www/html/launch.php; \
    test -f /var/www/html/scripts/page-helper.js; \
    grep -q 'page-helper.js' /var/www/html/workspace.php; \
    grep -q 'Обновить Pages' /var/www/html/scripts/page-helper.js; \
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
    chmod +x /var/www/html/docker-start.sh; \
    rm -rf /tmp/remask-parts /tmp/railway-launch-overlay.php /tmp/railway-check-account-session-overlay.php /tmp/railway-profile-session-guard-overlay.php /tmp/railway-workspace-session-integrity-overlay.php /tmp/railway-meta-session-context-overlay.php /tmp/railway-workspace-sync-fix-overlay.php /tmp/railway-sync-smoke.php /tmp/railway-worker-overlay.php /tmp/railway-retry-overlay.php /tmp/railway-targeting-autocomplete-overlay.php /tmp/railway-selection-persistence-overlay.php /tmp/railway-profile-error-fix-overlay.php /tmp/railway-page-helper-overlay.php /tmp/remask-runtime.b64 /tmp/remask-runtime.archive /tmp/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]
