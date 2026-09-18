<?php
/**
 * v73 single-click Launch flow.
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
    '<script src="scripts/launch.js?v=20260918-single-click-v73" type="module"></script>',
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
fwrite(STDERR, "[launch-flow] v73 single-click Launch ready\n");
