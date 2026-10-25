<?php
require_once __DIR__.'/../railway-private-launch-meta-marker.php';

function verify(bool $condition, string $message): void
{
    if (!$condition) {
        fwrite(STDERR, $message."\n");
        exit(1);
    }
}

$legacy = "    config.payload.execution_mode=executionMode;\n    const creative=config.payload.creative;\n";
$patched = remask_patch_private_launch_meta_marker($legacy);
verify(substr_count($patched, 'REMASK_PRIVATE_LAUNCH_META_MARKER_V1') === 1, 'Installer must stamp its runtime patch.');
verify(substr_count($patched, 'delete config.payload._meta_official_v1;') === 1, 'Installer must strip the UI-only marker exactly once.');
verify(strpos($patched, 'executionMode;') < strpos($patched, '_meta_official_v1;'), 'Marker stripping must follow execution-mode normalization.');
verify(remask_patch_private_launch_meta_marker($patched) === $patched, 'Runtime patch must be idempotent.');

$alreadySanitized = "    config.payload.execution_mode=executionMode;\n    delete config.payload._meta_official_v1;\n";
$stamped = remask_patch_private_launch_meta_marker($alreadySanitized);
verify(substr_count($stamped, 'delete config.payload._meta_official_v1;') === 1, 'Existing sanitizer must not be duplicated.');
verify(substr_count($stamped, 'REMASK_PRIVATE_LAUNCH_META_MARKER_V1') === 1, 'Existing sanitizer must receive the install sentinel.');

$failed = false;
try {
    remask_patch_private_launch_meta_marker('const unrelated = true;');
} catch (RuntimeException $error) {
    $failed = str_contains($error->getMessage(), 'anchor');
}
verify($failed, 'Unexpected runtime source must fail the build instead of silently shipping stale code.');

fwrite(STDOUT, "Private Launch compiled-runtime sanitizer patch passed.\n");
