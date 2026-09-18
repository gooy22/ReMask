<?php
/**
 * v93: Retry FAILED with the current Launch form payload.
 * Preserve already-created Meta IDs/steps so a failed Ad Set resumes under the
 * existing Campaign instead of creating a duplicate Campaign.
 */
$retryPath='/var/www/html/ajax/metaJobRetry.php';
$jsPath='/var/www/html/scripts/launch.js';
$phpPath='/var/www/html/launch.php';

foreach([$retryPath,$jsPath,$phpPath] as $f){
    if(!is_file($f)){fwrite(STDERR,"[retry-current-payload] missing $f\n");exit(161);}
}

$retry=file_get_contents($retryPath);
$js=file_get_contents($jsPath);
$php=file_get_contents($phpPath);
if($retry===false||$js===false||$php===false){fwrite(STDERR,"[retry-current-payload] read failed\n");exit(162);}

if(strpos($retry,'REMASK_RETRY_CURRENT_PAYLOAD_V1')===false){
    $needle=<<<'PHP'
    $key = remask_retry_items_key($job);
    $changed = 0;
PHP;
    $replacement=<<<'PHP'
    /* REMASK_RETRY_CURRENT_PAYLOAD_V1 */
    $payloadUpdated = false;
    $payloadJson = trim((string)($input['payload_json'] ?? ''));
    if ($payloadJson !== '') {
        require_once __DIR__ . '/../classes/MetaLaunchValidator.php';
        $candidatePayload = json_decode($payloadJson, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($candidatePayload)) throw new InvalidArgumentException('payload_json must decode to an object.');
        $job['payload'] = MetaLaunchValidator::validateAndNormalize($candidatePayload);

        $overridesJson = trim((string)($input['account_overrides_json'] ?? ''));
        if ($overridesJson !== '') {
            $candidateOverrides = json_decode($overridesJson, true, 512, JSON_THROW_ON_ERROR);
            if (!is_array($candidateOverrides)) throw new InvalidArgumentException('account_overrides_json must decode to an object.');
            $job['account_overrides'] = $candidateOverrides;
        }

        $job['retry_payload_updated_at'] = gmdate('c');
        $payloadUpdated = true;
    }

    $key = remask_retry_items_key($job);
    $changed = 0;
PHP;
    if(strpos($retry,$needle)===false){fwrite(STDERR,"[retry-current-payload] retry insertion point missing\n");exit(163);}
    $retry=str_replace($needle,$replacement,$retry,$n);
    if($n!==1){fwrite(STDERR,"[retry-current-payload] retry insertion count=$n\n");exit(164);}

    $old=<<<'PHP'
        'items_requeued' => $changed,
        'file' => basename($file),
PHP;
    $new=<<<'PHP'
        'items_requeued' => $changed,
        'payload_updated' => $payloadUpdated,
        'file' => basename($file),
PHP;
    if(strpos($retry,$old)===false){fwrite(STDERR,"[retry-current-payload] response patch target missing\n");exit(165);}
    $retry=str_replace($old,$new,$retry,$rn);
    if($rn!==1){fwrite(STDERR,"[retry-current-payload] response patch count=$rn\n");exit(166);}
    file_put_contents($retryPath,$retry);
}

if(strpos($js,'REMASK_RETRY_WITH_CURRENT_FORM_V1')===false){
    $append=<<<'JS'

/* REMASK_RETRY_WITH_CURRENT_FORM_V1 */
(() => {
    const button = $('retryFailed');
    if (!button) return;

    button.addEventListener('click', async (event) => {
        // Capture before the legacy bubble handler. Retry must use the form the
        // operator has just fixed, not the stale payload stored in the failed Job.
        event.preventDefault();
        event.stopImmediatePropagation();

        if (!state.currentJobId || state.processingJob) return;

        let config;
        try {
            config = currentLaunchConfigForRequest();
        } catch (e) {
            show($('launchResult'), e.message || String(e), 'failed');
            return;
        }

        const failedAccountIds = (state.currentJob?.items || [])
            .filter((item) => item.status === 'FAILED')
            .map((item) => String(item.account_id || '').replace(/^act_/, ''))
            .filter(Boolean);
        const selected = new Set((config.accountIds || []).map((id) => String(id).replace(/^act_/, '')));
        const missing = failedAccountIds.filter((id) => !selected.has(id));
        if (missing.length) {
            show($('launchResult'), 'Select the failed RK before Retry so its current form settings can be applied.', 'failed');
            return;
        }

        state.processingJob = true;
        validateReady();
        try {
            show($('launchResult'), 'Updating failed Job with current Launch settings and retrying...', 'ready');
            const result = await apiJson('ajax/metaJobRetry.php', formPost({
                job_id: state.currentJobId,
                mode: 'retry_failed',
                payload_json: JSON.stringify(config.payload),
                account_overrides_json: JSON.stringify(config.accountOverrides || {}),
            }));
            if (!result?.items_requeued) {
                show($('launchResult'), result?.message || 'No FAILED items were requeued.', 'failed');
                await refreshCurrentJob();
                return;
            }
            await refreshCurrentJob();

            // processCurrentJob owns the processing flag itself. Release our
            // short endpoint-update lock first or it will intentionally no-op.
            state.processingJob = false;
            validateReady();
            await processCurrentJob();
        } catch (e) {
            show($('launchResult'), e.payload?.message || e.message || String(e), 'failed');
            await refreshCurrentJob();
        } finally {
            state.processingJob = false;
            validateReady();
            await loadRecentJobs();
        }
    }, true);
})();
JS;
    $js .= "\n" . $append . "\n";
    file_put_contents($jsPath,$js);
}

$php=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-retry-current-v93" type="module"></script>',
    $php,
    1,
    $count
) ?? $php;
if($count!==1){fwrite(STDERR,"[retry-current-payload] launch.js tag missing\n");exit(167);}
file_put_contents($phpPath,$php);

fwrite(STDERR,"[retry-current-payload] v93 retry uses current form, preserves successful IDs, and resumes processing\n");
