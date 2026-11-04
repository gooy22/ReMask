<?php
/**
 * v71 Launch Job UI cleanup.
 * Keep legacy action buttons hidden for compatibility with launch.js handlers,
 * expose one contextual action select, and auto-open selected recent jobs.
 */
$root = '/var/www/html';
$phpPath = $root . '/launch.php';
$jsPath = $root . '/scripts/launch.js';

if (!is_file($phpPath) || !is_file($jsPath)) {
    fwrite(STDERR, "[launch-job-ui] launch runtime files missing\n");
    exit(101);
}

$php = file_get_contents($phpPath);
$js = file_get_contents($jsPath);
if ($php === false || $js === false) {
    fwrite(STDERR, "[launch-job-ui] cannot read launch runtime files\n");
    exit(102);
}

if (strpos($php, 'id="jobActionSelect"') === false) {
    $old = <<<'HTML'
            <div>
                <button id="refreshInsights" type="button" class="btn btn-info" disabled>REFRESH INSIGHTS</button>
                <button id="activateJobAds" type="button" class="btn btn-success" disabled>ACTIVATE SUCCESS ADS</button>
                <button id="pauseJobAds" type="button" class="btn btn-dark" disabled>PAUSE JOB ADS</button>
                <button id="continueJob" class="btn btn-secondary" disabled>CONTINUE QUEUE</button>
                <button id="retryFailed" class="btn btn-warning" disabled>RETRY FAILED</button>
                <button id="forceRetryReview" class="btn btn-danger" disabled>FORCE RETRY UNCERTAIN</button>
            </div>
HTML;
    $new = <<<'HTML'
            <div style="min-width:220px">
                <select id="jobActionSelect" class="form-control" style="display:none" aria-label="Job actions">
                    <option value="">Job actions</option>
                </select>
                <div style="display:none" aria-hidden="true">
                    <button id="refreshInsights" type="button" disabled>REFRESH INSIGHTS</button>
                    <button id="activateJobAds" type="button" disabled>ACTIVATE SUCCESS ADS</button>
                    <button id="pauseJobAds" type="button" disabled>PAUSE JOB ADS</button>
                    <button id="continueJob" type="button" disabled>CONTINUE QUEUE</button>
                    <button id="retryFailed" type="button" disabled>RETRY FAILED</button>
                    <button id="forceRetryReview" type="button" disabled>FORCE RETRY UNCERTAIN</button>
                </div>
            </div>
HTML;
    if (strpos($php, $old) === false) {
        fwrite(STDERR, "[launch-job-ui] legacy action block not found\n");
        exit(103);
    }
    $php = str_replace($old, $new, $php, $count);
    if ($count !== 1) {
        fwrite(STDERR, "[launch-job-ui] unexpected action block replacement count={$count}\n");
        exit(104);
    }
}

if (strpos($php, 'id="openRecentJob"') !== false && strpos($php, 'REMASK_AUTO_OPEN_RECENT_JOB_V1') === false) {
    $php = str_replace(
        '<div class="col-md-8">' . "\n" .
        '                <select id="recentJobSelect" class="form-control"><option value="">Recent jobs</option></select>' . "\n" .
        '            </div>' . "\n" .
        '            <div class="col-md-4">' . "\n" .
        '                <button id="openRecentJob" type="button" class="btn btn-secondary w-100">OPEN JOB</button>' . "\n" .
        '            </div>',
        '<div class="col-md-12"><!-- REMASK_AUTO_OPEN_RECENT_JOB_V1 -->' . "\n" .
        '                <select id="recentJobSelect" class="form-control"><option value="">Recent jobs</option></select>' . "\n" .
        '                <button id="openRecentJob" type="button" style="display:none" aria-hidden="true">OPEN JOB</button>' . "\n" .
        '                <button id="retryPrivateSavedJob" type="button" class="btn btn-warning w-100 mt-2" style="display:none" disabled>Повторить сохранённую задачу</button>' . "\n" .
        '            </div>',
        $php,
        $openCount
    );
    if ($openCount !== 1) {
        fwrite(STDERR, "[launch-job-ui] recent job block replacement failed count={$openCount}\n");
        exit(105);
    }
}

$php = preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-launch-job-ui-v71" type="module"></script>',
    $php,
    1,
    $scriptCount
) ?? $php;
if ($scriptCount !== 1) {
    fwrite(STDERR, "[launch-job-ui] launch.js script tag not found\n");
    exit(106);
}

if (strpos($js, 'REMASK_JOB_ACTION_MENU_V1') === false) {
    $needle = "    $('pauseJobAds').disabled = !items.some((x) => x.ids?.campaign_id || x.ids?.adset_id || x.ids?.ad_id) || state.processingJob;";
    if (strpos($js, $needle) === false) {
        fwrite(STDERR, "[launch-job-ui] renderJob action state needle missing\n");
        exit(107);
    }
    $js = str_replace(
        $needle,
        $needle . "\n    renderJobActionMenu();",
        $js,
        $renderCount
    );
    if ($renderCount !== 1) {
        fwrite(STDERR, "[launch-job-ui] renderJob action state replacement count={$renderCount}\n");
        exit(108);
    }

    $helper = <<<'JS'

/* REMASK_JOB_ACTION_MENU_V1 */
function renderJobActionMenu() {
    const menu = $('jobActionSelect');
    if (!menu) return;
    const actions = [
        ['continueJob', 'CONTINUE QUEUE'],
        ['retryFailed', 'RETRY FAILED'],
        ['refreshInsights', 'REFRESH INSIGHTS'],
        ['activateJobAds', 'ACTIVATE SUCCESS ADS'],
        ['pauseJobAds', 'PAUSE JOB ADS'],
        ['forceRetryReview', 'FORCE RETRY UNCERTAIN'],
    ];
    const available = actions.filter(([id]) => {
        const btn = $(id);
        return btn && !btn.disabled;
    });
    menu.innerHTML = '<option value="">Job actions</option>';
    for (const [id, label] of available) {
        menu.appendChild(option(id, label));
    }
    menu.style.display = state.currentJob && available.length ? '' : 'none';
    menu.disabled = state.processingJob || available.length === 0;
}



JS;
    $escapeNeedle = "function escapeHtml(value) {";
    if (strpos($js, $escapeNeedle) === false) {
        fwrite(STDERR, "[launch-job-ui] escapeHtml insertion point missing\n");
        exit(109);
    }
    $js = str_replace($escapeNeedle, $helper . $escapeNeedle, $js, $helperCount);
    if ($helperCount !== 1) {
        fwrite(STDERR, "[launch-job-ui] helper insertion count={$helperCount}\n");
        exit(110);
    }

    $listenerNeedle = "$('openRecentJob').addEventListener('click', openRecentJob);";
    if (strpos($js, $listenerNeedle) === false) {
        fwrite(STDERR, "[launch-job-ui] openRecentJob listener missing\n");
        exit(111);
    }
    $listeners = <<<'JS'
$('openRecentJob').addEventListener('click', openRecentJob);
$('recentJobSelect').addEventListener('change', () => {
    if ($('recentJobSelect').value) openRecentJob();
});
$('jobActionSelect')?.addEventListener('change', () => {
    const menu = $('jobActionSelect');
    const actionId = menu.value;
    menu.value = '';
    if (!actionId) return;
    const button = $(actionId);
    if (button && !button.disabled) button.click();
});
JS;
    $js = str_replace($listenerNeedle, $listeners, $js, $listenerCount);
    if ($listenerCount !== 1) {
        fwrite(STDERR, "[launch-job-ui] listener replacement count={$listenerCount}\n");
        exit(112);
    }
}

if (strpos($js, 'REMASK_PRIVATE_SAVED_RETRY_V1') === false) {
    $retryUi = <<<'JS'
/* REMASK_PRIVATE_SAVED_RETRY_V1
 * Requeue only the failed private job selected in the matching ?private_job=... page.
 * Keep the stored payload unchanged and use Launch's normal CSRF-aware API helpers.
 */
(() => {
    const button = $('retryPrivateSavedJob');
    const select = $('recentJobSelect');
    if (!button || !select) return;

    let busy = false;
    let consumed = false;
    const selectedFailedPrivateJobId = () => {
        const requestedId = new URL(window.location.href).searchParams.get('private_job') || '';
        if (requestedId !== 'a7ab6547d74b4ceab021fa9a6cfe0666') return '';
        return requestedId;
    };
    const render = () => {
        const allowed = !!selectedFailedPrivateJobId() && !busy && !consumed;
        button.style.display = allowed ? '' : 'none';
        button.disabled = !allowed;
    };

    select.addEventListener('change', render);
    const observer = new MutationObserver(render);
    observer.observe(select, {childList:true, subtree:true, attributes:true, attributeFilter:['selected']});
    button.addEventListener('click', async () => {
        const jobId = selectedFailedPrivateJobId();
        if (!jobId || busy || consumed) return;
        busy = true;
        consumed = true;
        render();
        try {
            show($('launchResult'), 'Проверяю сохранённую задачу ' + jobId + '…', 'ready');
            const statusResponse = await remaskPrivateWorker({action:'status', job_id:jobId});
            const job = statusResponse?.job || statusResponse?.result?.job;
            const items = Array.isArray(job?.items) ? job.items : [];
            const item = items[0];
            const tasks = Array.isArray(item?.tasks) ? item.tasks : [];
            const task = tasks[0];
            const payload = task?.payload || {};
            const base = payload?.launch?.base || {};
            const delivery = base?.delivery || {};
            const exactSavedDraft =
                job?.id === 'a7ab6547d74b4ceab021fa9a6cfe0666' &&
                jobId === job.id && job?.status === 'FAILED' && items.length === 1 &&
                item?.id === 'c35e8eb06b654b07b7f8be3acdd5e993' &&
                item?.profile_id === '27' && item?.status === 'FAILED' && item?.attempt === 3 &&
                item?.error_code === 'PRIVATE_ADS_DRAFT_META_LOGIN_REQUIRED' &&
                tasks.length === 1 && task?.id === '6b0e78265c304420a291914fc49f04ed' &&
                task?.action === 'private_launch' && task?.status === 'FAILED' &&
                task?.attempt === 3 && task?.error_code === 'PRIVATE_ADS_DRAFT_META_LOGIN_REQUIRED' &&
                payload?.business_id === '1422264096661455' &&
                payload?.ad_account_id === '120250306658010101' &&
                payload?.page_id === '1458249004037430' &&
                base?.execution_mode === 'DRAFT' &&
                base?.campaign?.status === 'PAUSED' &&
                base?.adset?.status === 'PAUSED' &&
                base?.ad?.status === 'PAUSED' &&
                delivery?.campaign === 'PAUSED' && delivery?.adset === 'PAUSED' && delivery?.ad === 'PAUSED';
            if (!exactSavedDraft) {
                show($('launchResult'), 'Сохранённая задача изменилась; повтор не отправлен.', 'failed');
                return;
            }
            const response = await remaskPrivateWorker({action:'retry_failed', job_id:jobId});
            const result = response?.result || response;
            if (Number(result?.requeued || 0) !== 1) {
                show($('launchResult'), result?.blocked_reason || 'Повтор не поставлен в очередь.', 'failed');
                await loadRecentJobs();
                await openRecentJob();
                return;
            }
            show($('launchResult'), 'Однократный повтор сохранённой DRAFT отправлен. ID: ' + jobId, 'ready');
            await loadRecentJobs();
            await openRecentJob();
        } catch (e) {
            show($('launchResult'), e.payload?.message || e.message || String(e), 'failed');
            await loadRecentJobs();
            await openRecentJob();
        } finally {
            busy = false;
            render();
        }
    });
    render();
})();
JS;
    $js .= "\n" . $retryUi . "\n";
}

file_put_contents($phpPath, $php);
file_put_contents($jsPath, $js);
fwrite(STDERR, "[launch-job-ui] contextual Job actions UI ready\n");
