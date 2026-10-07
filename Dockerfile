# deploy-trigger: bm-preflight-sync-hotfix 2026-09-24
FROM php:8.4-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4-openssl-dev libpq-dev xz-utils ca-certificates python3 python3-venv chromium \
    && docker-php-ext-install curl pdo_pgsql \
    && a2enmod rewrite headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

COPY python_backend/requirements.txt /tmp/remask-python-requirements.txt
RUN python3 -m venv /opt/remask-venv \
    && /opt/remask-venv/bin/pip install --no-cache-dir -r /tmp/remask-python-requirements.txt
COPY python_backend /opt/remask-python
RUN /opt/remask-venv/bin/python -m compileall -q /opt/remask-python \
    && cd /opt/remask-python \
    && /opt/remask-venv/bin/python -c "import fb_worker; from app.session import ProfileSession; from app.facebook_business_browser import FacebookBusinessBrowser; from app.provisioning.business_handler import business_handler; from app.provisioning.ad_account_handler import ad_account_handler; assert fb_worker.WebSessionManager is fb_worker.FacebookWebSession; assert callable(ProfileSession.facebook_business_browser)" \
    && /opt/remask-venv/bin/python -m unittest -q tests.test_fb_worker_bootstrap tests.test_business_docid_discovery tests.test_v14_docid_policy tests.test_fb_worker_request_envelope tests.test_business_create_exact_envelope tests.test_business_browser_flow tests.test_business_create_observer tests.test_ad_account_create tests.test_fan_page_create tests.test_fan_page_reconciliation tests.test_workspace_bindings tests.test_job_store_recovery tests.test_ads_manager_scope_sync tests.test_live_sync_contract tests.test_common_advertising_page tests.test_payment_card_binding \
    && grep -q 'class FacebookBusinessBrowser' /opt/remask-python/app/facebook_business_browser.py \
    && grep -q 'DIRECT_CREATE_URL = "https://business.facebook.com/create"' /opt/remask-python/app/facebook_business_browser.py \
    && grep -q 'business_guarded=' /opt/remask-python/app/runner.py \
    && grep -q 'CREATE_SUBMITTED' /opt/remask-python/app/provisioning/business_handler.py \
    && grep -q 'PAGE_ADD_SUBMITTED' /opt/remask-python/app/provisioning/business_handler.py \
    && grep -q 'facebook_business_suite_ui' /opt/remask-python/app/provisioning/business_handler.py \
    && grep -q 'async def reconcile_created_business' /opt/remask-python/app/facebook_business_browser.py \
    && grep -q 'async def add_existing_page' /opt/remask-python/app/facebook_business_browser.py \
    && grep -q 'business_suite_ui_v1' /opt/remask-python/main.py \
    && grep -q 'def facebook_business_browser' /opt/remask-python/app/session.py \
    && /opt/remask-venv/bin/python -c "import playwright; import shutil; assert shutil.which('chromium')" \
    && echo "[bm-browser-v1] Meta Business UI flow + resumable checkpoints passed"

COPY .deploy/clean-preview-valid/runtime.b64.* /tmp/remask-parts/
COPY railway-*.php railway-*.js /tmp/
COPY tests/payment-card-vault.test.php /tmp/remask-tests/payment-card-vault.test.php
COPY docker-start.sh /tmp/docker-start.sh

RUN set -eux; \
    php /tmp/remask-tests/payment-card-vault.test.php; \
    cp /tmp/railway-v100-creativeLibrary.php /tmp/remask-v100-creativeLibrary.php; \
    cp /tmp/railway-v100-creativePreview.php /tmp/remask-v100-creativePreview.php; \
    cp /tmp/railway-v100-creatives.php /tmp/remask-v100-creatives.php; \
    cp /tmp/railway-v100-creatives.js /tmp/remask-v100-creatives.js; \
    cp /tmp/railway-v102-MetaSdkSchema.php /tmp/remask-v102-MetaSdkSchema.php; \
    php -r '$out=""; $files=glob("/tmp/remask-parts/runtime.b64.*"); sort($files, SORT_NATURAL); foreach ($files as $file) { $out .= preg_replace("/\\s+/", "", file_get_contents($file)); } if ($out === "") { fwrite(STDERR, "empty ReMask runtime payload\n"); exit(20); } file_put_contents("/tmp/remask-runtime.b64", $out);'; \
    base64 -d /tmp/remask-runtime.b64 > /tmp/remask-runtime.archive; \
    echo "84b51ad4c062e45a13fd61b1ca8c66d0d1896b2bab2ebf2513dfd782998fdd95  /tmp/remask-runtime.archive" | sha256sum -c -; \
    if xz -t /tmp/remask-runtime.archive; then tar -xJf /tmp/remask-runtime.archive -C /var/www/html; \
    elif gzip -t /tmp/remask-runtime.archive; then tar -xzf /tmp/remask-runtime.archive -C /var/www/html; \
    else echo "Unsupported or corrupt ReMask runtime archive" >&2; exit 21; fi; \
    php -l /tmp/railway-persistence-overlay.php; \
    php /tmp/railway-persistence-overlay.php; \
    php -l /tmp/railway-ui-session-persistence-overlay.php; \
    php /tmp/railway-ui-session-persistence-overlay.php; \
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
    php -l /tmp/railway-meta-auth-state-overlay.php; \
    php /tmp/railway-meta-auth-state-overlay.php; \
    php -l /tmp/railway-launch-private-catalog-overlay.php; \
    php /tmp/railway-launch-private-catalog-overlay.php; \
    php -l /tmp/railway-cookie-only-overlay.php; \
    php /tmp/railway-cookie-only-overlay.php; \
    php -l /tmp/railway-payment-inspection-overlay.php; \
    php /tmp/railway-payment-inspection-overlay.php; \
    grep -q 'REMASK_UI_SESSION_PERSISTENCE_V2' /var/www/html/checkpassword.php; \
    php -l /var/www/html/checkpassword.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    php -l /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'hierarchy_canonical_account_snapshot' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'payment_status' /var/www/html/scripts/workspace.js; \
    ! grep -q 'styles/bootstrap.min.js' /var/www/html/workspace.php /var/www/html/accounts.php || exit 93; \
    php -l /var/www/html/classes/RemaskCookieProfile.php; \
    php -l /var/www/html/classes/RemaskCookieTxt.php; \
    grep -q REMASK_COOKIE_TXT_IMPORT_V1 /var/www/html/scripts/cookie-txt-import.js; \
    php -l /var/www/html/ajax/addAccount.php; \
    grep -q 'COOKIE_ONLY_GRAPH_DISABLED' /var/www/html/classes/MetaApiClient.php; \
    grep -q 'REMASK_COOKIE_PROFILE_UI_V1' /var/www/html/scripts/workspace.js; \
    ! grep -q 'newProfileToken\|id="editToken"\|name="token"' /var/www/html/scripts/workspace.js /var/www/html/accounts.php || exit 92; \
    php -l /var/www/html/classes/RemaskPrivateLaunchCatalog.php; \
    php -l /var/www/html/ajax/metaPreflight.php; \
    php -l /var/www/html/ajax/metaAssets.php; \
    grep -q 'pythonWorkerItemCanRetry' /var/www/html/scripts/workspace.js; \
    grep -q 'python-worker-ui-v219' /var/www/html/workspace.php;     grep -q 'auth_evidence' /opt/remask-python/app/facebook_business_browser.py;     grep -q 'checkpoint_url' /opt/remask-python/app/facebook_business_browser.py;     ! grep -q '"checkpoint" in body\[:4000\]' /opt/remask-python/app/facebook_business_browser.py;     grep -q 'pythonWorkerFilterFanPageReadyProfiles' /var/www/html/scripts/workspace.js;     grep -q '_request_fan_page_profile_ids' /opt/remask-python/main.py;     grep -q '_require_fp_auth_ready' /opt/remask-python/main.py;     grep -q 'REMASK_PROFILE_MUTATION_COOLDOWN_SECONDS' /opt/remask-python/app/provisioning/service.py;     grep -q '_await_profile_mutation_cooldown' /opt/remask-python/app/provisioning/service.py;     grep -q 'pythonWorkerIsProfileAuthBlockedCode' /var/www/html/scripts/workspace.js;     grep -q 'preflight.auth_blocked = authBlocked' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_META_PERMISSION_TRISTATE_FINAL_V2' /var/www/html/scripts/workspace.js; \
    grep -q 'existingProfile=state.inventory.profiles.find' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_SYNC_CSRF_SAFE_TRANSPORT_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_SYNC_RESULT_RECONCILIATION_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_FAILED_SYNC_DOES_NOT_MUTATE_WORKSPACE_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_SYNC_BROWSER_SERIAL_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'const syncConcurrency=1;' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_BUSINESS_TAB_SELECTED_SCOPE_V2' /var/www/html/scripts/workspace.js; \
    ! grep -q 'r=>syncBusinessSafe(r)' /var/www/html/scripts/workspace.js; \
    /opt/remask-venv/bin/python -c "from pathlib import Path; s=Path('/var/www/html/scripts/workspace.js').read_text(encoding='utf-8'); a=s.index('const finishSyncResponse='); b=s.index('const syncProfileSafe=',a); q=s[a:b]; assert q.index('sync_complete===false') < q.index('applySnapshot(d)'), 'failed sync mutates Workspace before failure check'" ; \
    grep -q 'REMASK_SYNC_CSRF_SAFE_TRANSPORT_V1' /var/www/html/scripts/workspace.js; \
    grep -q "action:'sync_result'" /var/www/html/scripts/workspace.js; \
    grep -q "hierarchy_sync_result_put" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "\$action === 'sync_result'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'syncApiJson' /var/www/html/scripts/workspace.js; \
    ! grep -q 'нет ads_management' /var/www/html/scripts/workspace.js; \
    grep -Fq "'proxy_configured' => \$proxy !== null" /var/www/html/ajax/metaHierarchy.php; \
    /opt/remask-venv/bin/python -c "from pathlib import Path; from playwright.sync_api import sync_playwright; src=Path('/var/www/html/scripts/workspace.js').read_text(encoding='utf-8'); p=sync_playwright().start(); b=p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox']); page=b.new_page(); err=page.evaluate('(src)=>{try{new Function(src);return \\\"\\\"}catch(e){return e.name+\\\": \\\"+e.message}}', src); b.close(); p.stop(); assert not err, err"; \
    grep -q 'ad_account_bindings' /opt/remask-python/main.py; \
    grep -q 'confirmed_ad_account_bindings_for_profile' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'confirmed_ad_account_binding_groups' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'confirmed_ad_account_binding_groups' /opt/remask-python/app/runner.py; \
    grep -q 'REMASK_WORKER_CONFIRMED_FP_MERGE_V1' /var/www/html/ajax/pythonWorkerPages.php; \
    grep -q 'REMASK_SYNC_SNAPSHOT_PAGE_SOURCE_V1' /var/www/html/ajax/pythonWorkerPages.php; \
    ! grep -q "MetaEndpoint::cachedAsset(.*'pages'" /var/www/html/ajax/pythonWorkerPages.php; \
    grep -q 'REMASK_SYNC_SNAPSHOT_BUSINESS_SOURCE_V1' /var/www/html/ajax/pythonWorkerBusinesses.php; \
    ! grep -q "MetaEndpoint::cachedAsset(.*'businesses'" /var/www/html/ajax/pythonWorkerBusinesses.php; \
    php -r '$allowedRaw=["/var/www/html/classes/MetaApiClient.php"=>true,"/var/www/html/classes/FbRequests.php"=>true,"/var/www/html/classes/ProxyHealthService.php"=>true]; $allowedLegacy=["/var/www/html/ajax/payUnsettled.php"=>true,"/var/www/html/ajax/policyAppeal.php"=>true,"/var/www/html/ajax/disapproveAppeal.php"=>true,"/var/www/html/ajax/metaHierarchy.php"=>true,"/var/www/html/ajax/metaProfileManager.php"=>true]; $violations=[]; foreach(["/var/www/html/ajax","/var/www/html/classes","/var/www/html/bin"] as $root){$it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,FilesystemIterator::SKIP_DOTS)); foreach($it as $fi){if(!$fi->isFile()||$fi->getExtension()!=="php")continue;$path=$fi->getPathname();$s=file_get_contents($path);if((str_contains($s,"graph.facebook.com")||str_contains($s,"curl_init("))&&!isset($allowedRaw[$path]))$violations[]="raw-meta-transport:".$path;if($fi->getFilename()!=="FbRequests.php"&&preg_match("/new\\s+FbRequests\\s*\\(/",$s)&&!isset($allowedLegacy[$path]))$violations[]="legacy-fbrequests-ref:".$path;}} if($violations){fwrite(STDERR,"Meta transport invariant failed: ".implode(", ",$violations)."\\n");exit(91);} fwrite(STDERR,"[transport-invariant] canonical Graph transport enforced; legacy browser transport limited to payment/appeal endpoints\\n");'; \
    php -r '$s=file_get_contents("/var/www/html/classes/FbRequests.php"); preg_match_all("/(?:public|protected|private)?\\s*function\\s+([A-Za-z0-9_]+)\\s*\\(([^)]*)\\)/",$s,$m,PREG_SET_ORDER|PREG_OFFSET_CAPTURE); $out=[]; foreach($m as $row){$out[]=$row[1][0]."(".preg_replace("/\\s+/"," ",trim($row[2][0])).")";} fwrite(STDERR,"[fbrequests-all-methods] ".json_encode($out,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE)."\\n"); $needle="function GetNewToken"; $p=strpos($s,$needle); if($p!==false){$chunk=substr($s,$p,7000); $chunk=preg_replace("/\\s+/"," ",$chunk); fwrite(STDERR,"[fbrequests-getnewtoken-source] ".$chunk."\\n");}'; \
    php -r '$s=file_get_contents("/var/www/html/classes/FbRequests.php"); foreach(["ApiGet","ApiPost","PrivateApiPost","GetDtsg"] as $fn){$needle="function ".$fn;$p=strpos($s,$needle);if($p!==false){$chunk=substr($s,$p,5000);$chunk=preg_replace("/\\s+/"," ",$chunk);fwrite(STDERR,"[fbrequests-source-".$fn."] ".$chunk."\\n");}} $it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator("/var/www/html",FilesystemIterator::SKIP_DOTS)); foreach($it as $fi){if(!$fi->isFile()||$fi->getExtension()!=="php")continue;$p=$fi->getPathname(); if($p==="/var/www/html/classes/FbRequests.php")continue; $c=file_get_contents($p); if(!preg_match("/->(ApiGet|ApiPost|PrivateApiPost|GetDtsg)\\s*\\(/",$c))continue; preg_match_all("/.{0,220}->(ApiGet|ApiPost|PrivateApiPost|GetDtsg)\\s*\\([^;]{0,500}/s",$c,$mm); foreach($mm[0] as $hit){$hit=preg_replace("/\\s+/"," ",$hit);fwrite(STDERR,"[fbrequests-callsite] ".$p." :: ".$hit."\\n");}}'; \
    php -l /var/www/html/classes/RemaskProxy.php; \
    php -l /var/www/html/classes/MetaApiClient.php;     grep -q 'REMASK_META_GRAPH_ERROR_DETAIL_V1' /var/www/html/classes/MetaApiClient.php; \
    php -l /var/www/html/classes/MetaAdsService.php; \
    php -l /var/www/html/classes/MetaEndpoint.php; \
    php -l /var/www/html/ajax/checkAccount.php; \
    php -l /var/www/html/ajax/metaProfileManager.php; \
    php -l /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'X-Remask-Internal-Key' /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'REMASK_INTERNAL_KEY' /var/www/html/ajax/pythonProfileContext.php; \
    grep -q 'browser_user_agent' /var/www/html/ajax/pythonProfileContext.php; \
    grep -q "'action' => 'list'\|'action'] ?? .*'resolve'" /var/www/html/ajax/pythonProfileContext.php || grep -q "action === 'list'" /var/www/html/ajax/pythonProfileContext.php; \
    test -x /opt/remask-venv/bin/uvicorn; \
    test -f /opt/remask-python/main.py; \
    grep -q 'async def facebook_web' /opt/remask-python/app/session.py; \
    grep -q 'def facebook_business_browser' /opt/remask-python/app/session.py; \
    grep -q 'facebook_business_suite_ui' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'REMASK_PAGE_ATTACH_DEADLINE_V1' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'REMASK_PAGE_ATTACH_CANCEL_SAFE_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'PAGE_ATTACH_TIMEOUT' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'provisioning_state.checkpoint' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'async def checkpoint' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'resume_from' /opt/remask-python/app/provisioning/business_handler.py; \
    grep -q 'FacebookBusinessBrowser(' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'capture_ad_account_create_request(' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'create_ad_account_with_docids(' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'facebook_private_graphql_live_capture' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    ! grep -q 'create_ad_account_for_business(' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'classify_meta_request_error' /opt/remask-python/app/provisioning/meta_errors.py; \
    grep -q "@app.get('/ready'" /opt/remask-python/main.py; \
    grep -q "profile_preflight" /opt/remask-python/main.py; \
    grep -q "action === 'preflight'" /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'pythonWorkerProfilePreflight' /var/www/html/scripts/workspace.js; \
    php -l /var/www/html/ajax/pythonWorkerJobs.php; \
    php -l /var/www/html/ajax/pythonWorkerPages.php; \
    php -l /var/www/html/ajax/pythonWorkerBusinesses.php; \
    ! grep -q 'pythonProvisionAdAccount' /var/www/html/workspace.php; \
    grep -q 'pythonWorkerEnhanceProfileThreeDots' /var/www/html/scripts/workspace.js; \
    grep -q 'data-python-worker-fp-menu' /var/www/html/scripts/workspace.js; \
    grep -q 'pythonWorkerOpenOwnFanPageModal' /var/www/html/scripts/workspace.js; \
    grep -q 'data-python-worker-rk-menu' /var/www/html/scripts/workspace.js; \
    grep -q 'pythonWorkerOpenOwnAdAccountModal' /var/www/html/scripts/workspace.js; \
    grep -q 'function pythonWorkerSelectedBusinessTargets()' /var/www/html/scripts/workspace.js; \
    grep -q 'function pythonWorkerStartBusinessAdAccountTargets(' /var/www/html/scripts/workspace.js; \
    grep -q 'function pythonWorkerInstallBusinessAddRkInterceptor()' /var/www/html/scripts/workspace.js; \
    grep -q 'workspace-add-rk-selected-bm-' /var/www/html/scripts/workspace.js; \
    grep -q 'latest_ad_account_resume_for_business' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'latest_profile_entities' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'latest_profile_fan_pages' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'confirmed_ad_account_bindings' /opt/remask-python/app/provisioning/state.py; \
    grep -q 'python_worker_capture_ui_history' /opt/remask-python/app/provisioning/state.py; \
    grep -q '_capture_ui_confirmed_ad_account_id' /opt/remask-python/app/provisioning/state.py; \
    grep -q '_restore_workspace_bindings' /opt/remask-python/app/runner.py; \
    grep -q "'fan_pages':fan_pages" /opt/remask-python/main.py; \
    grep -q 'REMASK_WORKER_CONFIRMED_FP_MERGE_V1' /var/www/html/ajax/pythonWorkerPages.php; \
    grep -q "count.value = '1'" /var/www/html/scripts/workspace.js; \
    grep -q 'profile_provisioning_state' /opt/remask-python/main.py; \
    grep -q "action === 'profile_state'" /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'CREATE_AD_ACCOUNT_RESULT_UNKNOWN' /opt/remask-python/app/facebook_ad_account_create.py; \
    grep -q 'CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT' /opt/remask-python/app/facebook_ad_account_create.py; \
    grep -q 'request_may_have_been_sent' /opt/remask-python/fb_worker.py; \
    grep -q 'PRE_SUBMIT_NAVIGATION_TIMEOUT_RECOVERED' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'REMASK_AD_ACCOUNT_CURRENT_UI_SUCCESS_V1' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'business_settings_ui_capture_current_attempt' /opt/remask-python/app/provisioning/ad_account_handler.py; \
    grep -q 'list_ad_accounts_for_business' /opt/remask-python/app/facebook_graph_api.py; \
    grep -q 'pythonWorkerOpenOwnBmModal' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_BM_MENU_PYTHON_ONLY_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'data-python-worker-bm-menu' /var/www/html/scripts/workspace.js; \
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
    grep -q 'REMASK_PYTHON_WORKER_UI_V158' /var/www/html/scripts/workspace.js; \
    ! grep -q 'manual_doc_id' /var/www/html/scripts/workspace.js; \
    grep -q 'pythonWorkerCsrf' /var/www/html/scripts/workspace.js; \
    grep -q 'pythonWorkerLoadPages(profileId, csrfRetried)' /var/www/html/scripts/workspace.js; \
    grep -q "'X-REMASK-CSRF': csrf" /var/www/html/scripts/workspace.js; \
    grep -q 'X-REMASK-CSRF' /var/www/html/scripts/workspace.js; \
    grep -q "action === 'csrf'" /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'remask_csrf_token' /var/www/html/ajax/pythonWorkerJobs.php; \
    grep -q 'pythonWorkerJobs.php' /var/www/html/scripts/workspace.js; \
    grep -q 'Retry Failed' /var/www/html/workspace.php; \
    php -l /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_PRIVATE_BROWSER_SYNC_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'hierarchy_worker_live_inventory' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_REQUESTED_BUSINESS_SYNC_SCOPE_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_SCOPED_SYNC_MERGE_LIVE_SIBLINGS_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_FULL_SYNC_REQUIRES_PAGES_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_PERSIST_ONLY_COMPLETE_META_SNAPSHOT_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_PRESERVE_CONFIRMED_PAGES_ON_PARTIAL_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'private_business_suite_browser' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_FBTOOL_ADS_TOKEN_REFRESH_V1' /var/www/html/classes/FbRequests.php; \

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
    grep -q '20261002-private-catalog-v1' /var/www/html/launch.php; \
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
    grep -q '20261002-private-catalog-v1' /var/www/html/launch.php; \
    grep -q 'languages-v117' /var/www/html/creatives.php; \
    grep -q 'rmMetaVisualModal' /var/www/html/launch.php; \
    grep -q '20261002-private-catalog-v1' /var/www/html/launch.php; \
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
    grep -q 'REMASK_PRIVATE_BROWSER_SYNC_V1' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'PRIVATE_SYNC_FAILED' /var/www/html/ajax/metaHierarchy.php; \
    grep -q "kind='META_REQUEST'" /var/www/html/scripts/workspace.js; \
    grep -q 'Private Meta sync timeout after' /var/www/html/scripts/workspace.js; \
    grep -Fq "\$('workspaceActions').disabled=n===0;" /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_SESSION_CLEAR_EXPLICIT_V2' /var/www/html/ajax/metaProfileManager.php; \
    grep -q 'REMASK_SESSION_ONLY_UPDATE_V1' /var/www/html/ajax/metaProfileManager.php; \
    ! grep -q 'profile7-' /var/www/html/docker-start.sh 2>/dev/null || true; \
    grep -q 'profileSaveJson' /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_SESSION_REFRESH_UI_V1' /var/www/html/scripts/workspace.js; \
    grep -q 'remaskSessionRefreshBtn' /var/www/html/scripts/workspace.js; \
    grep -q 'python-worker-ui-v219' /var/www/html/workspace.php; \
    if grep -Fq '\\`' /var/www/html/scripts/workspace.js; then echo 'workspace-invalid-backtick' >&2; exit 92; fi; \
    grep -q 'error_id' /var/www/html/ajax/metaProfileManager.php; \
    ! grep -q 'Добавь Cookies JSON текущей FB-сессии' /var/www/html/scripts/workspace.js; \
    ! grep -Fq "value.trim()||'[]'" /var/www/html/scripts/workspace.js; \
    grep -q 'metaProfileManager.php' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'bak.session-safe' /var/www/html/ajax/metaProfileManager.php; \
    grep -q 'existing->cookies' /var/www/html/ajax/metaProfileManager.php; \
    grep -q 'setSessionCookies' /var/www/html/classes/MetaApiClient.php; \
    grep -q 'setSessionCookies($account->getCurlCookies())' /var/www/html/classes/MetaEndpoint.php; \
    grep -q 'cookie_format_valid' /var/www/html/ajax/checkAccount.php; \
    grep -q "'session_verified'=>false" /var/www/html/ajax/checkAccount.php; \
    ! grep -q 'new MetaApiClient' /var/www/html/ajax/checkAccount.php; \
    ! grep -q 'curl_init' /var/www/html/ajax/checkAccount.php; \
    ! grep -q 'graph.facebook.com' /var/www/html/ajax/checkAccount.php; \
    grep -q 'private_business_suite_browser' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'hierarchy_worker_live_inventory' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'REMASK_PRIVATE_BUSINESS_INVENTORY_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_BUSINESS_SELECTOR_HARD_DEADLINE_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_ADS_REQUEST_SCOPE_EXPECTED_CONFIRM_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_AD_ACCOUNT_COMPARE_DIGITS_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_BROWSER_CLOSE_HARD_DEADLINE_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_BROWSER_OPEN_CANCEL_CLEANUP_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_BROWSER_OPEN_EARLY_CANCEL_CLEANUP_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'business_suite_private_inventory' /opt/remask-python/main.py; \
    grep -q "'pages':pages" /opt/remask-python/main.py; \
    grep -q "'pages' => array_values(\$cleanPages)" /var/www/html/ajax/metaHierarchy.php; \
    ! grep -q "rmx_pwp_worker_preflight" /var/www/html/ajax/pythonWorkerPages.php; \
    grep -q 'ad_account_hints' /opt/remask-python/main.py; \
    grep -q 'REMASK_CONFIRMED_HINT_FAST_REVALIDATION_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_REQUESTED_BUSINESS_WORKER_SCOPE_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_HARD_DEADLINE_TASK_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_LIVE_INVENTORY_TOTAL_BUDGET_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_SYNC_RESOLVER_BOUNDED_V1' /opt/remask-python/main.py; \
    grep -q 'browser_open_timeout=budget(24.0)' /opt/remask-python/main.py; \
    grep -q 'REMASK_LIVE_TARGET_SET_REQUIRED_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_LIVE_PAYLOAD_EXCLUDES_DURABLE_FALLBACK_V1' /opt/remask-python/main.py; \
    ! grep -q 'worker_confirmed_fallback' /opt/remask-python/main.py; \
    grep -q 'REMASK_FULL_PROFILE_SYNC_PAGES_V2' /opt/remask-python/main.py; \
    grep -q 'REMASK_SYNC_PRIVATE_LIST_PAGES_FIRST_V1' /opt/remask-python/main.py; \
    grep -q 'list_pages_via_private_graphql' /opt/remask-python/main.py; \
    grep -q 'browser.discover_managed_pages_isolated(' /opt/remask-python/main.py; \
    grep -q 'warm_retry=page_evidence_present' /opt/remask-python/main.py; \
    grep -q 'REMASK_ADAPTIVE_PAGE_PHASE_BUDGET_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_ISOLATED_PAGE_PRIMARY_V2' /opt/remask-python/main.py; \
    /opt/remask-venv/bin/python -c "from pathlib import Path; s=Path('/opt/remask-python/main.py').read_text(encoding='utf-8'); assert s.count('browser.discover_managed_pages_isolated(') == 1, 'isolated Page probe must appear exactly once in live sync'; assert 'warm_retry=page_evidence_present' in s; assert 'REMASK_ADAPTIVE_PAGE_PHASE_BUDGET_V1' in s; assert s.index('REMASK_ISOLATED_PAGE_PRIMARY_V2') < s.index('REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1'), 'isolated Page probe must run before lower-confidence fallbacks'"; \
    grep -q 'REMASK_ISOLATED_PAGE_INVENTORY_V1' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'REMASK_PAGE_LIVE_RELAY_DISCOVERY_V1' /opt/remask-python/app/facebook_business_browser.py; \
    ! grep -q 'skipped_for_business_scoped_sync' /opt/remask-python/main.py; \
    grep -q 'ads_manager_hint_revalidation_unconfirmed' /opt/remask-python/main.py; \
    grep -q 'REMASK_DURABLE_BINDING_ACCOUNT_HINTS_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_SCOPED_HINT_FASTPATH_ONLY_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_HISTORICAL_HINTS_ARE_FALLBACK_ONLY_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_STALE_HINT_ROWS_EXCLUDED_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_FULL_PROFILE_DISCOVERY_BUDGET_V1' /opt/remask-python/main.py; \
    grep -q 'REMASK_DISCOVERY_TIMEOUT_HINT_FALLBACK_V2' /opt/remask-python/main.py; \
    grep -q 'async def reopen_inventory_browser' /opt/remask-python/main.py; \
    grep -q 'ads_manager_scope_timeout' /opt/remask-python/main.py; \
    grep -q 'business_inventory_confirmed_empty' /opt/remask-python/main.py; \
    ! grep -q 'REMASK_EXACT_HINTS_REMAIN_REQUIRED_TARGETS_V1' /opt/remask-python/main.py; \
    grep -q 'ads_manager_live_act_matches_confirmed_snapshot' /opt/remask-python/app/facebook_business_browser.py; \
    grep -q 'hierarchy_live_snapshot_get($profile)' /var/www/html/ajax/metaHierarchy.php; \
    grep -q 'graph_preflight_available' /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'token_status'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'proxy_status'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'pages_count'" /var/www/html/ajax/metaHierarchy.php; \
    grep -q "'network_identity' => 'profile_bound'" /var/www/html/ajax/metaHierarchy.php; \
    grep -Fq "p.proxy_configured===true && ps!==''&&ps!=='LIVE'" /var/www/html/scripts/workspace.js; \
    grep -q 'REMASK_BM_OWNED_CLIENT_V2' /var/www/html/classes/MetaAdsService.php; \
    grep -Eq 'REMASK_DIRECT_RK_FUNDING_V(2|3)' /var/www/html/classes/MetaAdsService.php; \
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
    ! grep -Eq 'new[[:space:]]+MutationObserver' /var/www/html/scripts/selection-persistence.js || exit 92; \
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
    grep -q 'PRIVATE_LAUNCH_VERIFICATION_REQUIRED' /var/www/html/ajax/metaJobCreate.php; \
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
    grep -q '20261002-private-catalog-v1' /var/www/html/launch.php; \
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
    grep -q 'PRIVATE_LAUNCH_VERIFICATION_REQUIRED' /var/www/html/ajax/metaJobCreate.php; \
    grep -q 'carousel_media_library_ids' /var/www/html/scripts/launch.js; \
    grep -q 'PRIVATE_LAUNCH_VERIFICATION_REQUIRED' /var/www/html/ajax/metaJobCreate.php; \
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
    grep -q 'accounts.js?v=20261002-cookie-only-v1' /var/www/html/accounts.php; \
    grep -q "'network_identity' => 'profile_bound'" /var/www/html/classes/MetaEndpoint.php; \
    grep -q "'direct_fallback' => false" /var/www/html/classes/MetaEndpoint.php; \
    ! grep -q 'data-remask-fp-action="1"' /var/www/html/scripts/workspace.js; \
    grep -q 'function pythonWorkerPageTargetPlan(' /var/www/html/scripts/workspace.js; \
    grep -q 'async function pythonWorkerPrepareCommonPage()' /var/www/html/scripts/workspace.js; \
    grep -q 'data-python-common-page' /var/www/html/scripts/workspace.js; \
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
    ! grep -q 'profile7-' /tmp/docker-start.sh; \
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
# railway deploy trigger: private-page-business-loader-v190 2026-09-29

