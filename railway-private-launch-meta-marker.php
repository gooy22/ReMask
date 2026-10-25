<?php
/**
 * Patch the already-installed private Launch module in the curated runtime image.
 * Later source edits must also reach an existing compiled copy.
 */
function remask_patch_private_launch_meta_marker(string $launchJs): string
{
    $sentinel = 'REMASK_PRIVATE_LAUNCH_META_MARKER_V1';
    if (strpos($launchJs, $sentinel) !== false) return $launchJs;

    $anchor = '    config.payload.execution_mode=executionMode;';
    if (substr_count($launchJs, $anchor) !== 1) {
        throw new RuntimeException('Private Launch compiled serializer anchor did not match exactly once');
    }
    $offset = strpos($launchJs, $anchor) + strlen($anchor);
    $lookAhead = substr($launchJs, $offset, 400);
    if (strpos($lookAhead, 'delete config.payload._meta_official_v1;') !== false) {
        return substr_replace($launchJs, "\n    // ".$sentinel, $offset, 0);
    }

    $replacement = $anchor."\n    // ".$sentinel."\n    delete config.payload._meta_official_v1;";
    return str_replace($anchor, $replacement, $launchJs);
}
