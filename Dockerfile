FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev xz-utils ca-certificates python3 python3-venv \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

COPY python_backend/requirements.txt /tmp/remask-python-requirements.txt
RUN python3 -m venv /opt/remask-venv \
    && /opt/remask-venv/bin/pip install --no-cache-dir -r /tmp/remask-python-requirements.txt
COPY python_backend /opt/remask-python
RUN /opt/remask-venv/bin/python -m compileall -q /opt/remask-python \
    && grep -q 'class FacebookWebSession' /opt/remask-python/fb_worker.py \
    && grep -q 'WebSessionManager = FacebookWebSession' /opt/remask-python/fb_worker.py \
    && cd /opt/remask-python \
    && /opt/remask-venv/bin/python -c "import fb_worker; from app.session import ProfileSession; from app.provisioning.business_handler import business_handler; from app.provisioning.ad_account_handler import ad_account_handler; from app.provisioning.meta_errors import classify_meta_request_error; from app.facebook_graph_api import FacebookGraphApi; from app.business_create_service import create_business_resilient; from app.facebook_page_discovery import discover_pages_via_web; from app.facebook_query_discovery import discover_persisted_query; assert fb_worker.WebSessionManager is fb_worker.FacebookWebSession"

COPY .deploy/clean-preview-valid/runtime.b64.* /tmp/remask-parts/
COPY railway-persistence-overlay.php /tmp/railway-persistence-overlay.php
COPY railway-launch-overlay.php /tmp/railway-launch-overlay.php
COPY railway-launch-job-ui-overlay.php /tmp/railway-launch-job-ui-overlay.php
COPY railway-launch-flow-overlay.php /tmp/railway-launch-flow-overlay.php
COPY railway-media-persistence-overlay.php /tmp/railway-media-persistence-overlay.php
COPY railway-campaign-budget-sharing-overlay.php /tmp/railway-campaign-budget-sharing-overlay.php
COPY railway-job-error-ui-overlay.php /tmp/railway-job-error-ui-overlay.php
COPY railway-budget-guard-overlay.php /tmp/railway-budget-guard-overlay.php
COPY railway-retry-current-payload-overlay.php /tmp/railway-retry-current-payload-overlay.php
COPY railway-launch-multi-profile-overlay.php /tmp/railway-launch-multi-profile-overlay.php
COPY railway-check-account-session-overlay.php /tmp/railway-check-account-session-overlay.php
COPY railway-profile-session-guard-overlay.php /tmp/railway-profile-session-guard-overlay.php
COPY railway-workspace-session-integrity-overlay.php /tmp/railway-workspace-session-integrity-overlay.php
COPY railway-meta-session-context-overlay.php /tmp/railway-meta-session-context-overlay.php
COPY railway-workspace-sync-fix-overlay.php /tmp/railway-workspace-sync-fix-overlay.php
COPY railway-sync-smoke.php /tmp/railway-sync-smoke.php
COPY railway-worker-overlay.php /tmp/railway-worker-overlay.php
COPY railway-retry-overlay.php /tmp/railway-retry-overlay.php
COPY railway-targeting-autocomplete-overlay.php /tmp/railway-targeting-autocomplete-overlay.php
COPY railway-live-targeting-overlay.php /tmp/railway-live-targeting-overlay.php
COPY railway-behaviors-backend-overlay.php /tmp/railway-behaviors-backend-overlay.php
COPY railway-behaviors-ui-overlay.php /tmp/railway-behaviors-ui-overlay.php
COPY railway-targeting-russian-overlay.php /tmp/railway-targeting-russian-overlay.php
COPY railway-creative-targeting-v109-overlay.php /tmp/railway-creative-targeting-v109-overlay.php
COPY railway-creative-library-overlay.php /tmp/railway-creative-library-overlay.php
COPY railway-v100-creativeLibrary.php /tmp/remask-v100-creativeLibrary.php
COPY railway-v100-creativePreview.php /tmp/remask-v100-creativePreview.php
COPY railway-v100-creatives.php /tmp/remask-v100-creatives.php
COPY railway-v100-creatives.js /tmp/remask-v100-creatives.js
COPY railway-creative-library-v100-overlay.php /tmp/railway-creative-library-v100-overlay.php
COPY railway-meta-official-fields.php /tmp/railway-meta-official-fields.php
COPY railway-meta-builder-v102-overlay.php /tmp/railway-meta-builder-v102-overlay.php
COPY railway-v102-MetaSdkSchema.php /tmp/remask-v102-MetaSdkSchema.php
COPY railway-meta-schema-ui-v103-overlay.php /tmp/railway-meta-schema-ui-v103-overlay.php
COPY railway-creative-capabilities-v105-overlay.php /tmp/railway-creative-capabilities-v105-overlay.php
COPY railway-placement-capabilities-v106-overlay.php /tmp/railway-placement-capabilities-v106-overlay.php
COPY railway-launch-full-meta-v113-overlay.php /tmp/railway-launch-full-meta-v113-overlay.php
COPY railway-launch-meta-editors-v114-overlay.php /tmp/railway-launch-meta-editors-v114-overlay.php
COPY railway-language-targeting-v116-overlay.php /tmp/railway-language-targeting-v116-overlay.php
COPY railway-selection-persistence-overlay.php /tmp/railway-selection-persistence-overlay.php
COPY railway-profile-error-fix-overlay.php /tmp/railway-profile-error-fix-overlay.php
COPY railway-python-worker-bridge-overlay.php /tmp/railway-python-worker-bridge-overlay.php
COPY railway-python-worker-jobs-overlay.php /tmp/railway-python-worker-jobs-overlay.php
COPY railway-python-worker-state-overlay.php /tmp/railway-python-worker-state-overlay.php
COPY railway-python-worker-ui-overlay.php /tmp/railway-python-worker-ui-overlay.php
COPY railway-python-worker-ui.js /tmp/railway-python-worker-ui.js
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    php -l /tmp/railway-persistence-overlay.php; \
    php /tmp/railway-persistence-overlay.php; \
    php /tmp/railway-launch-overlay.php; \
    php /tmp/railway-launch-job-ui-overlay.php; \
    php /tmp/railway-launch-flow-overlay.php; \
    php /tmp/railway-media-persistence-overlay.php; \
    php /tmp/railway-campaign-budget-sharing-overlay.php; \
    php /tmp/railway-job-error-ui-overlay.php; \
    php /tmp/railway-budget-guard-overlay.php; \
    php /tmp/railway-check-account-session-overlay.php; \
    php /tmp/railway-profile-session-guard-overlay.php; \
    php /tmp/railway-workspace-session-integrity-overlay.php; \
    php /tmp/railway-meta-session-context-overlay.php; \
    php /tmp/railway-workspace-sync-fix-overlay.php; \
    php /tmp/railway-worker-overlay.php; \
    php /tmp/railway-retry-overlay.php; \
    php /tmp/railway-retry-current-payload-overlay.php; \
    php /tmp/railway-launch-multi-profile-overlay.php; \
    php /tmp/railway-targeting-autocomplete-overlay.php; \
    php /tmp/railway-live-targeting-overlay.php; \
    php -l /tmp/railway-behaviors-backend-overlay.php; \
    php -l /tmp/railway-behaviors-ui-overlay.php; \
    php /tmp/railway-behaviors-backend-overlay.php; \
    php /tmp/railway-behaviors-ui-overlay.php; \
    php -l /tmp/railway-targeting-russian-overlay.php; \
    php /tmp/railway-targeting-russian-overlay.php; \
    php -l /tmp/railway-creative-targeting-v109-overlay.php; \
    php /tmp/railway-creative-targeting-v109-overlay.php; \
    php -l /tmp/railway-creative-library-overlay.php; \
    php /tmp/railway-creative-library-overlay.php; \
    php -l /tmp/remask-v100-creativeLibrary.php; \
    php -l /tmp/remask-v100-creativePreview.php; \
    php -l /tmp/remask-v100-creatives.php; \
    php -l /tmp/railway-creative-library-v100-overlay.php; \
    php /tmp/railway-creative-library-v100-overlay.php; \
    php -l /tmp/railway-meta-official-fields.php; \
    php -l /tmp/railway-meta-builder-v102-overlay.php; \
    php /tmp/railway-meta-builder-v102-overlay.php; \
    php -l /tmp/remask-v102-MetaSdkSchema.php; \
    php -l /tmp/railway-meta-schema-ui-v103-overlay.php; \
    php /tmp/railway-meta-schema-ui-v103-overlay.php; \
    php -l /tmp/railway-creative-capabilities-v105-overlay.php; \
    php /tmp/railway-creative-capabilities-v105-overlay.php; \
    php -l /tmp/railway-placement-capabilities-v106-overlay.php; \
    php /tmp/railway-placement-capabilities-v106-overlay.php; \
    php -l /tmp/railway-launch-full-meta-v113-overlay.php; \
    php /tmp/railway-launch-full-meta-v113-overlay.php; \
    php /tmp/railway-selection-persistence-overlay.php; \
    php /tmp/railway-profile-error-fix-overlay.php; \
    php -l /tmp/railway-python-worker-bridge-overlay.php; \
    php /tmp/railway-python-worker-bridge-overlay.php; \
    php -l /tmp/railway-python-worker-jobs-overlay.php; \
    php /tmp/railway-python-worker-jobs-overlay.php; \
    php -l /tmp/railway-python-worker-state-overlay.php; \
    php /tmp/railway-python-worker-state-overlay.php; \
    php -l /tmp/railway-launch-meta-editors-v114-overlay.php; \
    php /tmp/railway-launch-meta-editors-v114-overlay.php; \
    php -l /tmp/railway-language-targeting-v116-overlay.php; \
    php /tmp/railway-language-targeting-v116-overlay.php; \
    php -l /tmp/railway-python-worker-ui-overlay.php; \
    php /tmp/railway-python-worker-ui-overlay.php; \
    php -r '$allowedRaw=["/var/www/html/classes/MetaApiClient.php"=>true,"/var/www/html/classes/FbRequests.php"=>true,"/var/www/html/classes/ProxyHealthService.php"=>true]; $allowedLegacy=["/var/www/html/ajax/payUnsettled.php"=>true,"/var/www/html/ajax/policyAppeal.php"=>true,"/var/www/html/ajax/disapproveAppeal.php"=>true]; $violations=[]; foreach(["/var/www/html/ajax","/var/www/html/classes","/var/www/html/bin"] as $root){$it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,FilesystemIterator::SKIP_DOTS)); foreach($it as $fi){if(!$fi->isFile()||$fi->getExtension()!=="php")continue;$path=$fi->getPathname();$s=file_get_contents($path);if((str_contains($s,"graph.facebook.com")||str_contains($s,"curl_init("))&&!isset($allowedRaw[$path]))$violations[]="raw-meta-transport:".$path;if($fi->getFilename()!=="FbRequests.php"&&preg_match("/new\\s+FbRequests\\s*\\(/",$s)&&!isset($allowedLegacy[$path]))$violations[]="legacy-fbrequests-ref:".$path;}} if($violations){fwrite(STDERR,"Meta transport invariant failed: ".implode(", ",$violations)."\\n");exit(91);} fwrite(STDERR,"[transport-invariant] canonical Graph transport enforced; legacy browser transport limited to payment/appeal endpoints\\n");'; \
    php -l /var/www/html/classes/RemaskProxy.php; \
    php -l /var/www/html/classes/MetaApiClient.php; \
    php -l /var/www/html/classes/MetaAdsService.php; \
    php -l /var/www/html/classes/MetaEndpoint.php; \
    php -l /var/www/html/ajax/checkAccount.php; \
    php -l /var/www/html/ajax/metaProfileManager.php; \
    php -l /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'X-Remask-Internal-Key' /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'REMASK_INTERNAL_KEY' /var/www/html/ajax/pythonProfileContext.php; \
    grep -q "'action' => 'list'\|'action'] ?? .*'resolve'" /var/www/html/ajax/pythonProfileContext.php || grep -q "action === 'list'" /var/www/html/ajax/pythonProfileContext.php; \
    test -x /opt/remask-venv/bin/uvicorn; \
    test -f /opt/remask-python/main.py; \
    grep -q 'async def facebook_web' /opt/remask-python/app/session.py; \
    grep -q 'await session.facebook_controller()' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'await session.facebook_controller()' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'classify_meta_request_error' /opt/remask-python/app/provisioning/meta_errors.py; \
    grep -q "@app.get('/ready'" /opt/remask-python/main.py; \
    grep -q "profile_preflight" /opt/remask-python/main.py; \
    grep -q "action === 'preflight'" /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'pythonWorkerProfilePreflight' /var/www/html/scripts/workspace.js; \
    grep -q 'Graph API /me/accounts' /var/www/html/scripts/workspace.js; \
    grep -q "'access_token' => trim((string)\$account->token)" /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'create_business_resilient' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'class FacebookGraphApi' /opt/remask-python/app/facebook_graph_api.py; \
    grep -q 'AccountQualityUserPagesWrapper_UserPageQuery' /opt/remask-python/app/facebook_docids.py; \
    grep -q 'discover_pages_via_web' /opt/remask-python/app/facebook_page_discovery.py; \
    grep -q 'discover_persisted_query' /opt/remask-python/app/facebook_query_discovery.py; \
    grep -q 'BUSINESS_EMAIL_REQUIRED' /opt/remask-python/app/business_create_service.py; \
    grep -q 'retryable INTEGER NOT NULL DEFAULT 0' /opt/remask-python/app/store.py; \
    grep -q 'retryable=bool(exc.retryable)' /opt/remask-python/app/runner.py; \
    grep -q 'hasRetryableFailed' /var/www/html/scripts/workspace.js; \
    grep -q 'businessEmail' /var/www/html/scripts/workspace.js; \
    grep -q "'pages_source':pages_source" /opt/remask-python/main.py; \
    grep -q "LIST_PAGES" /var/www/html/ajax/pythonWorkerJobs.php; \
    php -l /var/www/html/ajax/pythonWorkerJobs.php; \
    php -l /var/www/html/ajax/pythonWorkerPages.php; \
    grep -q 'pythonWorkerOpenOwnBmModal' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_PYTHON_WORKER_URL' /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'retry-failed' /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'internalAuthorized' /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'X_REMASK_INTERNAL_KEY' /var/www/html/ajax/pythonWorkerJobs.php; \
    php -l /var/www/html/ajax/pythonWorkerState.php; \
    grep -q 'python-worker-jobs' /var/www/html/ajax/pythonWorkerState.php; \
    grep -q 'RAW_PAYMENT_DATA_REJECTED' /var/www/html/ajax/pythonWorkerState.php; \
    php -l /var/www/html/workspace.php; \
    grep -q 'REMASK_PYTHON_WORKER_PANEL_V1' /var/www/html/workspace.php; \
    grep -q 'REMASK_PYTHON_WORKER_UI_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_PYTHON_WORKER_UI_V154' /var/www/html/scripts/workspace.js; \
    grep -q 'pythonWorkerJobs.php' /var/www/html/scripts/workspace.js; \
    grep -q 'Retry Failed' /var/www/html/workspace.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    php -l /var/www/html/bin/remask-worker.php; \
    php -l /var/www/html/ajax/metaWorkerStatus.php; \
    php -l /var/www/html/ajax/metaJobRetry.php; \
    php -l /var/www/html/ajax/metaCreativeCapabilities.php; \
    php -l /var/www/html/ajax/metaAudienceEstimate.php; \
    php -l /var/www/html/ajax/metaCreativePreview.php; \
    php -l /var/www/html/ajax/metaPlacementCapabilities.php; \
    php -l /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_ACCOUNTLESS_TARGETING_V1' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_ACCOUNTLESS_TRANSPORT_POOL_V4' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_TARGETING_RUNTIME_FAILOVER_V1' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'creative-targeting-transport.json' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'successful_targeting_call' /var/www/html/ajax/metaTargetingSearch.php; \
    ! grep -q 'cachedPreflight($candidateName, true)' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_ACCOUNTLESS_BEHAVIOR_ACCOUNT_V3' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_EFFECTIVE_META_BUILDER_V2' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_PLACEMENT_PREFLIGHT_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_FULL_META_LAUNCH_V1' /var/www/html/scripts/launch.js; \
    grep -q 'launchMetaSdkFields' /var/www/html/launch.php; \
    grep -q 'remaskCurrentMetaBuilderForJob' /var/www/html/scripts/launch.js; \
    grep -q 'languages-v117' /var/www/html/launch.php; \
    grep -q 'REMASK_META_VISUAL_EDITORS_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_LANGUAGE_SEARCH_V1' /var/www/html/classes/MetaAdsService.php; \
    grep -q "'type' => 'adlocale'" /var/www/html/classes/MetaAdsService.php; \
    grep -q "'limit' => 1000" /var/www/html/classes/MetaAdsService.php; \
    grep -q 'No Meta locale transport candidate is available' /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q "'language', 'languages', 'locale', 'locales'" /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_AUDIENCE_LANGUAGES_V1' /var/www/html/scripts/launch.js; \
    grep -q 'languageQuery' /var/www/html/scripts/launch.js; \
    grep -q 'mbLanguageSearch' /var/www/html/creatives.php; \
    grep -q 'languages:{input' /var/www/html/scripts/creatives.js; \
    grep -q 'languages-v117' /var/www/html/launch.php; \
    grep -q 'languages-v117' /var/www/html/creatives.php; \
    grep -q 'rmMetaVisualModal' /var/www/html/launch.php; \
    grep -q 'languages-v117' /var/www/html/launch.php; \
    grep -Fq "kind === 'geo' || kind === 'excludedGeo'" /var/www/html/scripts/creatives.js; \
    grep -q "state.behaviors = Array.isArray(targeting.behaviors)" /var/www/html/scripts/launch.js; \
    grep -q 'mbGeoSearch' /var/www/html/creatives.php; \
    grep -q 'mbExcludedGeoSearch' /var/www/html/creatives.php; \
    grep -q 'mbInterestSearch' /var/www/html/creatives.php; \
    grep -q 'mbBehaviorSearch' /var/www/html/creatives.php; \
    grep -q 'searchCreativeTargeting' /var/www/html/scripts/creatives.js; \
    grep -q 'languages-v117' /var/www/html/creatives.php; \
    grep -q 'REMASK_PLACEMENT_CAPABILITIES_V1' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'targetingbrowse' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'placementOptions' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'Manual placements' /var/www/html/creatives.php; \
    grep -q 'Advantage+ placements' /var/www/html/creatives.php; \
    grep -q 'data-position-group' /var/www/html/scripts/creatives.js; \
    grep -q 'placementMode()' /var/www/html/scripts/creatives.js; \
    ! grep -q 'mbFacebookPositions' /var/www/html/creatives.php; \
    ! grep -q 'mbInstagramPositions' /var/www/html/creatives.php; \
    grep -q 'REMASK_CREATIVE_CAPABILITIES_V1' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'delivery_estimate' /var/www/html/classes/MetaAdsService.php; \
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
    grep -q 'is_adset_budget_sharing_enabled' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_META_ERROR_DETAILS_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_DAILY_BUDGET_GUARD_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_RETRY_WITH_CURRENT_FORM_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_RETRY_CURRENT_PAYLOAD_V1' /var/www/html/ajax/metaJobRetry.php; \
    grep -q 'REMASK_MULTI_PROFILE_PICKER_V1' /var/www/html/launch.php; \
    grep -q 'REMASK_MULTI_PROFILE_SELECTION_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_MULTI_PROFILE_PREFLIGHT_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_RK_TABLE_EVENTS_V1' /var/www/html/scripts/launch.js; \
    grep -q 'selectedProfileNames' /var/www/html/scripts/launch.js; \
    grep -q 'rkPickerRows' /var/www/html/launch.php; \
    grep -q 'REMASK_DIRECT_FUNDING_SNAPSHOT_V2' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'RemaskProxy::fromSemicolonString' /var/www/html/ajax/checkAccount.php; \
    ! test -f /var/www/html/ajax/metaSyncProbe.php; \
    ! test -f /var/www/html/remask-session-recover.php; \
    ! test -f /var/www/html/bin/remask-sync-smoke.php; \
    test -f /var/www/html/scripts/targeting-autocomplete.js; \
    test -f /var/www/html/scripts/selection-persistence.js; \
    ! grep -q 'MutationObserver' /var/www/html/scripts/selection-persistence.js; \
    grep -q 'targeting-autocomplete.js' /var/www/html/launch.php; \
    grep -q 'REMASK_LIVE_GEO_INTEREST_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_SKIP_NATIVE_LIVE_TARGETING_V1' /var/www/html/scripts/targeting-autocomplete.js; \
    grep -q 'behaviors-v96' /var/www/html/launch.php; \
    grep -q "installLiveTargetingInput('geoQuery', 'locations')" /var/www/html/scripts/launch.js; \
    grep -q "installLiveTargetingInput('interestQuery', 'interests')" /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_BEHAVIOR_SEARCH_V1' /var/www/html/classes/MetaAdsService.php; \
    grep -q "'behavior', 'behaviors'" /var/www/html/ajax/metaTargetingSearch.php; \
    grep -q 'REMASK_BEHAVIOR_UI_V1' /var/www/html/scripts/launch.js; \
    grep -q "installLiveTargetingInput('behaviorQuery', 'behaviors')" /var/www/html/scripts/launch.js; \
    grep -q 'detailedTargeting.behaviors' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_TARGETING_RU_LOCALE_V1' /var/www/html/classes/MetaAdsService.php; \
    test "$(grep -c "'locale' => 'ru_RU'" /var/www/html/classes/MetaAdsService.php)" -ge 3; \
    php -l /var/www/html/classes/CreativePresetStore.php; \
    php -l /var/www/html/ajax/creativeLibrary.php; \
    php -l /var/www/html/ajax/creativePreview.php; \
    php -l /var/www/html/creatives.php; \
    test -f /var/www/html/scripts/creatives.js; \
    grep -q 'creative-meta-builder-v102' /var/www/html/creatives.php; \
    grep -q 'mbObjective' /var/www/html/creatives.php; \
    grep -q 'mbOptimizationGoal' /var/www/html/creatives.php; \
    grep -q 'mbBehaviors' /var/www/html/creatives.php; \
    grep -q 'mbAdvancedTargeting' /var/www/html/creatives.php; \
    grep -q "form.append('meta_builder'" /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_META_BUILDER_JOB_V1' /var/www/html/ajax/metaJobCreate.php; \
    grep -q 'REMASK_META_OFFICIAL_VALIDATOR_V1' /var/www/html/classes/MetaLaunchValidator.php; \
    grep -q 'REMASK_META_OFFICIAL_FORWARD_V1' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'REMASK_META_BUILDER_BUDGET_V1' /var/www/html/scripts/launch.js; \
    php -l /var/www/html/classes/MetaOfficialFields.php; \
    test -f /var/www/html/classes/MetaOfficialFields.php; \
    grep -q 'ONLINE_GAMBLING_AND_GAMING' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'STORE_VISITS' /var/www/html/classes/MetaSdkSchema.php; \
    ! grep -q 'metaProfileContext' /var/www/html/creatives.php; \
    ! grep -q 'metaAccountContext' /var/www/html/creatives.php; \
    ! grep -q 'audienceEstimateValue' /var/www/html/creatives.php; \
    ! grep -q 'metaExistingMedia' /var/www/html/creatives.php; \
    ! grep -q 'presetInstagramMediaId' /var/www/html/creatives.php; \
    grep -q 'placement_options' /var/www/html/ajax/metaCreativeCapabilities.php; \
    grep -q 'Official Meta SDK placements' /var/www/html/creatives.php; \
    grep -q 'placementHoverPreview' /var/www/html/creatives.php; \
    grep -q 'showPlacementHoverPreview' /var/www/html/scripts/creatives.js; \
    grep -q 'data-placement-preview' /var/www/html/scripts/creatives.js; \
    grep -q 'currentPlacementPreviewMedia' /var/www/html/scripts/creatives.js; \
    grep -q 'populatePrimaryMetaControls' /var/www/html/scripts/creatives.js; \
    grep -q 'metaCreativeCapabilities.php' /var/www/html/scripts/creatives.js; \
    grep -q 'generateCreativePreview' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'generatepreviews' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'adPreviewFormats' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'creativeCallToActionTypes' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'promotedObjectCustomEventTypes' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'listConnectedInstagramAccounts' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'listConversionGoals' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'connected_instagram_accounts' /var/www/html/ajax/metaCreativeCapabilities.php; \
    grep -q 'conversion_goals' /var/www/html/ajax/metaCreativeCapabilities.php; \
    grep -q 'liveOptimizationGoals' /var/www/html/scripts/creatives.js; \
    grep -q 'publisher_platforms' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'device_platforms' /var/www/html/classes/MetaSdkSchema.php; \
    grep -q 'async function loadMetaContext' /var/www/html/scripts/creatives.js; \
    ! grep -q 'async async function' /var/www/html/scripts/creatives.js; \
    grep -q 'listCreativeImages' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'listCreativeVideos' /var/www/html/classes/MetaAdsService.php; \
    grep -q 'cl100_meta_media_ref' /var/www/html/ajax/creativeLibrary.php; \
    grep -q 'existing_media' /var/www/html/classes/MetaOfficialFields.php; \
    grep -q 'existing_creative_id' /var/www/html/classes/MetaOfficialFields.php; \
    php -r 'require "/var/www/html/classes/MetaOfficialFields.php"; $base=["campaign"=>[],"adset"=>["targeting"=>[]],"creative"=>[],"ad"=>[]]; foreach([["image_hash"=>"abcDEF_123"],["video_id"=>"123456"],["creative_id"=>"987654"]] as $m){$p=MetaOfficialFields::applyBuilderToPayload($base,["existing_media"=>$m]);$c=$p["creative"]; if(isset($m["image_hash"])&&($c["existing_image_hash"]??"")!==$m["image_hash"])exit(71); if(isset($m["video_id"])&&($c["existing_video_id"]??"")!==$m["video_id"])exit(72); if(isset($m["creative_id"])&&($c["existing_creative_id"]??"")!==$m["creative_id"])exit(73);}'; \
    grep -q 'ad_creatives' /var/www/html/ajax/metaCreativeCapabilities.php; \
    grep -q "'video_id'" /var/www/html/classes/MetaOfficialFields.php; \
    grep -q 'languages-v117' /var/www/html/launch.php; \
    php -l /var/www/html/classes/MetaSdkSchema.php; \
    php -l /var/www/html/ajax/metaSdkSchema.php; \
    grep -q 'metaSdkFields' /var/www/html/creatives.php; \
    grep -q 'loadMetaSdkSchema' /var/www/html/scripts/creatives.js; \
    grep -q 'renderMetaSdkFields' /var/www/html/scripts/creatives.js; \
    grep -q 'facebook/facebook-python-business-sdk' /var/www/html/ajax/metaSdkSchema.php; \
    grep -q 'presetCreativeName' /var/www/html/creatives.php; \
    grep -q 'presetAdName' /var/www/html/creatives.php; \
    grep -q 'presetMessage' /var/www/html/creatives.php; \
    grep -q 'presetHeadline' /var/www/html/creatives.php; \
    grep -q 'presetDescription' /var/www/html/creatives.php; \
    grep -q 'presetUrl' /var/www/html/creatives.php; \
    grep -q 'presetCta' /var/www/html/creatives.php; \
    grep -q 'presetTags' /var/www/html/creatives.php; \
    grep -q 'presetFormat' /var/www/html/creatives.php; \
    grep -q 'presetCarousel' /var/www/html/creatives.php; \
    grep -q 'REMASK_COMPLETE_CREATIVE_PRESET_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_CAROUSEL_LIBRARY_V1' /var/www/html/ajax/metaJobCreate.php; \
    grep -q 'carousel_media_library_ids' /var/www/html/scripts/launch.js; \
    grep -q 'carousel_media_library_ids' /var/www/html/ajax/metaJobCreate.php; \
    grep -q 'cr-grid' /var/www/html/creatives.php; \
    grep -q 'creativeModal' /var/www/html/creatives.php; \
    grep -q 'REMASK_PERSISTENCE_ROOT_V1' /var/www/html/settings.php; \
    grep -q 'REMASK_CREATIVE_LIBRARY_V1' /var/www/html/settings.php; \
    grep -q "'creatives.php','fa-images','Креативы'" /var/www/html/menu.php; \
    grep -q 'creativeLibrarySelect' /var/www/html/launch.php; \
    grep -q 'REMASK_CREATIVE_LIBRARY_LAUNCH_V1' /var/www/html/scripts/launch.js; \
    grep -q "form.append('media_library_id'" /var/www/html/scripts/launch.js; \
    grep -q 'creative_preset' /var/www/html/scripts/launch.js; \
    grep -q 'jobActionSelect' /var/www/html/launch.php; \
    grep -q 'REMASK_AUTO_OPEN_RECENT_JOB_V1' /var/www/html/launch.php; \
    grep -q 'REMASK_JOB_ACTION_MENU_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_SINGLE_CLICK_LAUNCH_V1' /var/www/html/scripts/launch.js; \
    grep -q 'REMASK_AUTO_NAMING_V1' /var/www/html/scripts/launch.js; \
    grep -q 'Missing: ' /var/www/html/scripts/launch.js; \
    grep -q 'Launch blocked: one or more selected RK failed Launch Review' /var/www/html/scripts/launch.js; \
    test -f /var/www/html/scripts/media-draft.js; \
    grep -q 'REMASK_MEDIA_DRAFT_V2' /var/www/html/scripts/media-draft.js; \
    grep -q 'DataTransfer' /var/www/html/scripts/media-draft.js; \
    grep -q 'media-draft.js?v=20260918-media-draft-v77' /var/www/html/launch.php; \
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
    mkdir -p /var/www/html/health /var/lib/remask /var/lib/remask/jobs /var/lib/remask/bundles /var/lib/remask/meta-cache /var/lib/remask/job-media /var/lib/remask/media-library /var/lib/remask/creative-presets; \
    if [ ! -f /var/www/html/health/index.php ]; then printf '%s\n' '<?php http_response_code(200); header("Content-Type: application/json"); echo json_encode(["ok"=>true,"service":"remask","rev"=>getenv("REMASK_DEPLOY_REV")]);' > /var/www/html/health/index.php; fi; \
    [ -f /var/www/html/index.php ]; \
    [ -f /var/www/html/launch.php ]; \
    bash -n /tmp/docker-start.sh; \
    cp /tmp/docker-start.sh /var/www/html/docker-start.sh; \
    mkdir -p /var/www/html/bin; \
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
