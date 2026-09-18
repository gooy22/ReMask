<?php
/**
 * v74 single-click Launch flow with automatic naming.
 * The Launch button must not be hard-disabled by opaque client readiness state.
 * On click we validate locally, run Launch Review automatically, and only then
 * create the job when the server says the selection is ready.
 */
$root = '/var/www/html';
$jsPath = $root . '/scripts/launch.js';
$phpPath = $root . '/launch.php';

if (!is_file($jsPath) || !is_file($phpPath)) {
    fwrite(STDERR, "[launch-flow] runtime files missing\n");
    exit(121);
}

$js = file_get_contents($jsPath);
$php = file_get_contents($phpPath);
if ($js === false || $php === false) {
    fwrite(STDERR, "[launch-flow] cannot read runtime files\n");
    exit(122);
}

$oldValidate = <<<'JS'
function validateReady() {
    const ids = updateSelectedCount();
    const assetReady = targetMode() ? targetBindingsReady() : Boolean($('page').value);
    const ready = Boolean(state.launchConfig && state.profile && ids.length && assetReady && currentObjectiveSpec()?.enabled);
    $('launchButton').disabled = !ready || state.processingJob;
    if ($('reviewLaunch')) $('reviewLaunch').disabled = !ready || state.processingJob;
    if ($('dryRunPlan')) $('dryRunPlan').disabled = !ready || state.processingJob;
}
JS;

$newValidate = <<<'JS'
/* REMASK_SINGLE_CLICK_LAUNCH_V1 */
function validateReady() {
    updateSelectedCount();
    const configReady = Boolean(state.launchConfig && currentObjectiveSpec()?.enabled);
    // Do not hide validation failures behind a disabled button.
    // createJobAndRun() performs explicit local checks and server Launch Review.
    $('launchButton').disabled = !configReady || state.processingJob;
    if ($('reviewLaunch')) $('reviewLaunch').disabled = !configReady || state.processingJob;
    if ($('dryRunPlan')) $('dryRunPlan').disabled = !configReady || state.processingJob;
}
JS;

$oldConfig = <<<'JS'
function currentLaunchConfigForRequest() {
    const accountIds = selectedAccountIds();
    if (!accountIds.length) throw new Error('Select at least one ad account.');
    const payload = buildPayload();
    const required = creativeFormat() === 'INSTAGRAM_POST'
        ? [payload.campaign.name, payload.adset.name, payload.creative.name, payload.ad.name]
        : [payload.campaign.name, payload.adset.name, payload.creative.name, payload.creative.message, payload.creative.link, payload.ad.name];
    if (required.some(v => !v)) throw new Error(creativeFormat() === 'INSTAGRAM_POST'
        ? 'Fill campaign/adset/creative/ad names.'
        : 'Fill campaign/adset/creative/ad names, Primary text and Destination URL.');
    const budget = Number(payload.campaign.daily_budget || payload.adset.daily_budget || 0);
    if (!Number.isInteger(budget) || budget <= 0) throw new Error('Daily budget must be a positive integer in minor currency units.');
    validatePayloadClient(payload, accountIds);
    const accountOverrides = collectAccountOverrides();
    if (creativeFormat() === 'CAROUSEL') {
        for (const override of Object.values(accountOverrides)) {
            if (!override || typeof override !== 'object' || !override.creative) continue;
            delete override.creative.existing_image_hash;
            delete override.creative.existing_creative_id;
        }
    }
    if (accountIds.length > 1) delete payload.adset.promoted_object;
    return {accountIds, payload, accountOverrides};
}
JS;

$newConfig = <<<'JS'
/* REMASK_AUTO_NAMING_V1 */
function ensureLaunchNames() {
    const campaign = $('campaignName');
    const adset = $('adsetName');
    const creative = $('creativeName');
    const ad = $('adName');

    if (campaign && !campaign.value.trim()) campaign.value = 'ReMask Campaign';
    if (adset && !adset.value.trim()) adset.value = 'ReMask Ad Set';
    if (creative && !creative.value.trim()) creative.value = 'ReMask Creative';
    if (ad && !ad.value.trim()) {
        const base = creative?.value.trim() || 'ReMask Creative';
        ad.value = `${base} Ad`;
    }
}

function currentLaunchConfigForRequest() {
    const accountIds = selectedAccountIds();
    if (!accountIds.length) throw new Error('Select at least one ad account.');

    ensureLaunchNames();
    const payload = buildPayload();

    const missing = [];
    if (!payload.campaign?.name) missing.push('Campaign name');
    if (!payload.adset?.name) missing.push('Ad Set name');
    if (!payload.creative?.name) missing.push('Creative name');
    if (!payload.ad?.name) missing.push('Ad name');
    if (creativeFormat() !== 'INSTAGRAM_POST') {
        if (!payload.creative?.message) missing.push('Primary text');
        if (!payload.creative?.link) missing.push('Destination URL');
    }
    if (missing.length) throw new Error(`Missing: ${missing.join(', ')}.`);

    const budget = Number(payload.campaign.daily_budget || payload.adset.daily_budget || 0);
    if (!Number.isInteger(budget) || budget <= 0) throw new Error('Daily budget must be a positive integer in minor currency units.');

    validatePayloadClient(payload, accountIds);
    const accountOverrides = collectAccountOverrides();
    if (creativeFormat() === 'CAROUSEL') {
        for (const override of Object.values(accountOverrides)) {
            if (!override || typeof override !== 'object' || !override.creative) continue;
            delete override.creative.existing_image_hash;
            delete override.creative.existing_creative_id;
        }
    }
    if (accountIds.length > 1) delete payload.adset.promoted_object;
    return {accountIds, payload, accountOverrides};
}
JS;

if (strpos($js, 'REMASK_AUTO_NAMING_V1') === false) {
    if (strpos($js, $oldConfig) === false) {
        fwrite(STDERR, "[launch-flow] currentLaunchConfigForRequest block not found\n");
        exit(128);
    }
    $js = str_replace($oldConfig, $newConfig, $js, $nc);
    if ($nc !== 1) {
        fwrite(STDERR, "[launch-flow] naming replacement count={$nc}\n");
        exit(129);
    }
}

if (strpos($js, 'REMASK_SINGLE_CLICK_LAUNCH_V1') === false) {
    if (strpos($js, $oldValidate) === false) {
        fwrite(STDERR, "[launch-flow] validateReady block not found\n");
        exit(123);
    }
    $js = str_replace($oldValidate, $newValidate, $js, $vc);
    if ($vc !== 1) {
        fwrite(STDERR, "[launch-flow] validateReady replacement count={$vc}\n");
        exit(124);
    }
}

$needle = <<<'JS'
    const payload = config.payload;
    const accountOverrides = config.accountOverrides;

    const form = new FormData();
JS;

$replacement = <<<'JS'
    const payload = config.payload;
    const accountOverrides = config.accountOverrides;

    // One-click flow: validate the exact selection against Meta before any mutation.
    show($('launchResult'), `Checking ${accountIds.length} ad account(s) before launch...`);
    const review = await reviewSelected(false);
    if (!review) {
        show($('launchResult'), 'Launch Review failed. Fix the error shown above and try again.', 'failed');
        return;
    }
    if (review.ready !== true) {
        show($('launchResult'), 'Launch blocked: one or more selected RK failed Launch Review. See the review table above.', 'failed');
        return;
    }

    const form = new FormData();
JS;

if (strpos($js, "Launch blocked: one or more selected RK failed Launch Review") === false) {
    if (strpos($js, $needle) === false) {
        fwrite(STDERR, "[launch-flow] job creation insertion point not found\n");
        exit(125);
    }
    $js = str_replace($needle, $replacement, $js, $jc);
    if ($jc !== 1) {
        fwrite(STDERR, "[launch-flow] job creation replacement count={$jc}\n");
        exit(126);
    }
}

$php = preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-single-click-v74" type="module"></script>',
    $php,
    1,
    $sc
) ?? $php;
if ($sc !== 1) {
    fwrite(STDERR, "[launch-flow] launch.js tag not found\n");
    exit(127);
}

file_put_contents($jsPath, $js);
file_put_contents($phpPath, $php);
fwrite(STDERR, "[launch-flow] v74 single-click Launch + auto naming ready\n");
