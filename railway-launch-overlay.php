<?php
/**
 * Railway build overlay for the clean-preview runtime.
 * The normal unified patch may not match scripts/launch.js from this runtime,
 * so apply the small Launch tracking change with tolerant string/regex edits.
 */

function replace_once(string &$text, string $search, string $replace, string $label): void
{
    $count = 0;
    $text = str_replace($search, $replace, $text, $count);
    if ($count > 0) {
        fwrite(STDERR, "[remask overlay] {$label}: {$count}\n");
    }
}

$root = '/var/www/html';
$launchJsPath = $root . '/scripts/launch.js';
$launchPhpPath = $root . '/launch.php';

if (is_file($launchJsPath)) {
    $js = file_get_contents($launchJsPath);
    if ($js === false) {
        fwrite(STDERR, "[remask overlay] cannot read scripts/launch.js\n");
        exit(30);
    }

    if (strpos($js, 'url_tags') === false) {
        $count = 0;
        $js = preg_replace(
            "/existing_creative_id:\\s*''\\s*}/",
            "existing_creative_id: '', url_tags: ''}",
            $js,
            1,
            $count
        );
        fwrite(STDERR, "[remask overlay] binding url_tags default: {$count}\n");
    }

    replace_once($js, 'colspan="8" class="muted">No RK selected.', 'colspan="9" class="muted">No RK selected.', 'empty selected RK colspan');

    if (strpos($js, "const trackingTd = document.createElement('td');") === false) {
        $trackingBlock = <<<'JS'
        const trackingTd = document.createElement('td');
        const trackingInput = document.createElement('input');
        trackingInput.className = 'form-control';
        trackingInput.style.minWidth = '230px';
        trackingInput.placeholder = 'Use global URL tags';
        trackingInput.value = binding.url_tags || '';
        trackingInput.addEventListener('change', () => {
            binding.url_tags = trackingInput.value.trim();
            invalidateLaunchReview();
            validateReady();
        });
        trackingTd.appendChild(trackingInput);
        const trackingHint = document.createElement('div');
        trackingHint.className = 'muted mt-1';
        trackingHint.textContent = binding.url_tags ? 'Per-RK override' : 'Global tracking';
        trackingTd.appendChild(trackingHint);

JS;
        $needle = "        const statusReady = Boolean(binding.page_id) && (!needsPixel || Boolean(binding.pixel_id));";
        if (strpos($js, $needle) !== false) {
            $js = str_replace($needle, $trackingBlock . $needle, $js, $count);
            fwrite(STDERR, "[remask overlay] tracking input block: {$count}\n");
        } else {
            fwrite(STDERR, "[remask overlay] tracking input insertion point not found\n");
            exit(31);
        }
    }

    $before = $js;
    $js = preg_replace(
        '/tr\.appendChild\(pageTd\);\s*tr\.appendChild\(pixelTd\);\s*tr\.appendChild\(audienceTd\);\s*tr\.appendChild\(mediaTd\);\s*tr\.appendChild\(statusTd\);/',
        'tr.appendChild(pageTd); tr.appendChild(pixelTd); tr.appendChild(audienceTd); tr.appendChild(mediaTd); tr.appendChild(trackingTd); tr.appendChild(statusTd);',
        $js,
        1,
        $appendCount
    );
    if ($appendCount > 0) {
        fwrite(STDERR, "[remask overlay] append tracking td: {$appendCount}\n");
    } elseif (strpos($js, 'tr.appendChild(trackingTd);') === false) {
        fwrite(STDERR, "[remask overlay] append tracking insertion point not found\n");
        exit(32);
    }

    if (strpos($js, 'row.creative.url_tags') === false) {
        $needle = "            else if (binding.existing_image_hash) row.creative.existing_image_hash = binding.existing_image_hash;";
        if (strpos($js, $needle) !== false) {
            $replace = $needle . "\n            if (binding.url_tags) row.creative.url_tags = binding.url_tags;";
            $js = str_replace($needle, $replace, $js, $count);
            fwrite(STDERR, "[remask overlay] payload url_tags exact: {$count}\n");
        } else {
            $js = preg_replace(
                '/(binding\.existing_image_hash\)[^;]*row\.creative\.existing_image_hash\s*=\s*binding\.existing_image_hash;)/',
                '$1' . "\n            if (binding.url_tags) row.creative.url_tags = binding.url_tags;",
                $js,
                1,
                $count
            );
            fwrite(STDERR, "[remask overlay] payload url_tags regex: {$count}\n");
            if ($count === 0) {
                fwrite(STDERR, "[remask overlay] payload url_tags insertion point not found\n");
                exit(33);
            }
        }
    }

    file_put_contents($launchJsPath, $js);
    fwrite(STDERR, "[remask overlay] scripts/launch.js ready\n");
} else {
    fwrite(STDERR, "[remask overlay] scripts/launch.js missing\n");
    exit(34);
}

if (is_file($launchPhpPath)) {
    $php = file_get_contents($launchPhpPath);
    if ($php === false) {
        fwrite(STDERR, "[remask overlay] cannot read launch.php\n");
        exit(35);
    }
    if (strpos($php, 'Existing Media / Tracking') === false) {
        replace_once($php, 'Existing Media</small>', 'Existing Media / Tracking</small>', 'launch title tracking');
    }
    if (strpos($php, '<th>Tracking</th>') === false) {
        replace_once($php, '<th>Existing Media</th><th>Status</th>', '<th>Existing Media</th><th>Tracking</th><th>Status</th>', 'launch table tracking th');
    }
    replace_once($php, 'colspan="8" class="muted">Open Launch from Workspace with selected RK.', 'colspan="9" class="muted">Open Launch from Workspace with selected RK.', 'launch table empty colspan');
    file_put_contents($launchPhpPath, $php);
    fwrite(STDERR, "[remask overlay] launch.php ready\n");
}
