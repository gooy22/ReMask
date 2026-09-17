FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev xz-utils ca-certificates \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

# Use the verified clean-preview runtime as the authoritative Workspace/account flow.
COPY .deploy/clean-preview-valid/runtime.b64.* /tmp/remask-parts/
COPY railway-launch-overlay.php /tmp/railway-launch-overlay.php
COPY railway-proxy-overlay.php /tmp/railway-proxy-overlay.php
COPY railway-worker-overlay.php /tmp/railway-worker-overlay.php
COPY railway-retry-overlay.php /tmp/railway-retry-overlay.php
COPY railway-targeting-autocomplete-overlay.php /tmp/railway-targeting-autocomplete-overlay.php
COPY railway-selection-persistence-overlay.php /tmp/railway-selection-persistence-overlay.php
COPY railway-workspace-sync-fix-overlay.php /tmp/railway-workspace-sync-fix-overlay.php
COPY railway-meta-discovery-fix-overlay.php /tmp/railway-meta-discovery-fix-overlay.php
COPY railway-meta-read-fallback-overlay.php /tmp/railway-meta-read-fallback-overlay.php
COPY railway-meta-oauth-overlay.php /tmp/railway-meta-oauth-overlay.php
COPY railway-meta-auth-state-overlay.php /tmp/railway-meta-auth-state-overlay.php
COPY railway-meta-auth-persist-overlay.php /tmp/railway-meta-auth-persist-overlay.php
COPY railway-sync-diag-overlay.php /tmp/railway-sync-diag-overlay.php
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    php /tmp/railway-launch-overlay.php; \
    php /tmp/railway-proxy-overlay.php; \
    php /tmp/railway-worker-overlay.php; \
    php /tmp/railway-retry-overlay.php; \
    php /tmp/railway-targeting-autocomplete-overlay.php; \
    php /tmp/railway-selection-persistence-overlay.php; \
    php /tmp/railway-workspace-sync-fix-overlay.php; \
    php /tmp/railway-meta-discovery-fix-overlay.php; \
    php /tmp/railway-meta-read-fallback-overlay.php; \
    php /tmp/railway-meta-oauth-overlay.php; \
    php /tmp/railway-meta-auth-state-overlay.php; \
    php /tmp/railway-meta-auth-persist-overlay.php; \
    php /tmp/railway-sync-diag-overlay.php; \
    php -l /var/www/html/classes/RemaskProxy.php; \
    php -l /var/www/html/classes/MetaApiClient.php; \
    php -l /var/www/html/classes/MetaAdsService.php; \
    php -l /var/www/html/classes/MetaAuthStateStore.php; \
    php -l /var/www/html/ajax/checkAccount.php; \
    php -l /var/www/html/ajax/metaProfileManager.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    php -l /var/www/html/ajax/metaSyncProbe.php; \
    php -l /var/www/html/ajax/metaOAuthStatus.php; \
    php -l /var/www/html/ajax/metaAuthState.php; \
    php -l /var/www/html/meta-oauth-start.php; \
    php -l /var/www/html/meta-oauth-callback.php; \
    php -l /var/www/html/bin/remask-worker.php; \
    php -l /var/www/html/ajax/metaWorkerStatus.php; \
    php -l /var/www/html/ajax/metaJobRetry.php; \
    test -f /var/www/html/scripts/targeting-autocomplete.js; \
    test -f /var/www/html/scripts/selection-persistence.js; \
    test -f /var/www/html/scripts/meta-oauth.js; \
    test -f /var/www/html/scripts/meta-auth-state.js; \
    grep -q 'targeting-autocomplete.js' /var/www/html/launch.php; \
    grep -q 'selection-persistence.js' /var/www/html/launch.php; \
    grep -q 'scripts/meta-oauth.js' /var/www/html/workspace.php; \
    grep -q 'scripts/meta-auth-state.js' /var/www/html/workspace.php; \
    grep -q 'Синхронизация Meta НЕ выполнена' /var/www/html/scripts/workspace.js; \
    grep -q 'Business Manager list unavailable for this token; direct RK data kept' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'META_OAUTH_REQUIRED' /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'ads_management_granted' => \$preflight === null ? null" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'auth_state' => hierarchy_auth_state_store()->get(\$profile)" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'status'=>'oauth_required'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'fields' => 'id,name,account_status,currency,amount_spent'" /var/www/html/classes/MetaAdsService.php; \
    grep -q 'direct-fallback-after-proxy-407' /var/www/html/classes/MetaApiClient.php; \
    ! grep -q 'hierarchy-autosync.js' /var/www/html/workspace.php; \
    mkdir -p /var/www/html/health /var/lib/remask /var/lib/remask/jobs /var/lib/remask/bundles /var/lib/remask/meta-cache /var/lib/remask/job-media; \
    if [ ! -f /var/www/html/health/index.php ]; then printf '%s\n' '<?php http_response_code(200); header("Content-Type: application/json"); echo json_encode(["ok"=>true,"service"=>"remask","rev"=>getenv("REMASK_DEPLOY_REV")]);' > /var/www/html/health/index.php; fi; \
    [ -f /var/www/html/index.php ]; \
    [ -f /var/www/html/launch.php ]; \
    cp /tmp/docker-start.sh /var/www/html/docker-start.sh; \
    [ -f /var/lib/remask/accounts.json ] || printf '[]\n' > /var/lib/remask/accounts.json; \
    [ -f /var/lib/remask/bundles.json ] || printf '[]\n' > /var/lib/remask/bundles.json; \
    chown -R www-data:www-data /var/lib/remask /var/www/html; \
    chmod 700 /var/lib/remask; \
    chmod +x /var/www/html/docker-start.sh; \
    rm -rf /tmp/remask-parts /tmp/railway-launch-overlay.php /tmp/railway-proxy-overlay.php /tmp/railway-worker-overlay.php /tmp/railway-retry-overlay.php /tmp/railway-targeting-autocomplete-overlay.php /tmp/railway-selection-persistence-overlay.php /tmp/railway-workspace-sync-fix-overlay.php /tmp/railway-meta-discovery-fix-overlay.php /tmp/railway-meta-read-fallback-overlay.php /tmp/railway-meta-oauth-overlay.php /tmp/railway-meta-auth-state-overlay.php /tmp/railway-meta-auth-persist-overlay.php /tmp/railway-sync-diag-overlay.php /tmp/remask-runtime.b64 /tmp/remask-runtime.archive /tmp/docker-start.sh

ENV REMASK_META_CACHE_TTL=1800 \
    META_GRAPH_API_VERSION=v26.0 \
    REMASK_PROCESS_ROLE=web

EXPOSE 80
CMD ["/var/www/html/docker-start.sh"]