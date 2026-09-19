<?php
$target = trim((string)(getenv('REMASK_TOKEN_HISTORY_DIAG_PROFILE') ?: ''));
if ($target === '') {
    exit(0);
}
$root = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
$rows = [];

function rmx_hash_scalar($value): ?string {
    if (!is_scalar($value)) return null;
    $s = (string)$value;
    if ($s === '') return null;
    return substr(hash('sha256', $s), 0, 16) . ':' . strlen($s);
}

function rmx_collect_secrets($node, array &$out, string $path = ''): void {
    if (!is_array($node)) return;
    foreach ($node as $k => $v) {
        $key = strtolower((string)$k);
        $childPath = $path === '' ? (string)$k : $path . '.' . $k;
        if (is_array($v)) {
            rmx_collect_secrets($v, $out, $childPath);
            continue;
        }
        if (preg_match('/token|cookie|proxy/', $key)) {
            $h = rmx_hash_scalar($v);
            if ($h !== null) $out[$childPath] = $h;
        }
    }
}

function rmx_walk_target($node, string $target, array &$matches, string $path = '$'): void {
    if (!is_array($node)) return;
    $encoded = json_encode($node, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if (is_string($encoded) && strpos($encoded, $target) !== false) {
        $secrets = [];
        rmx_collect_secrets($node, $secrets, $path);
        if ($secrets !== []) {
            $matches[] = ['path' => $path, 'hashes' => $secrets];
        }
    }
    foreach ($node as $k => $v) {
        if (is_array($v)) rmx_walk_target($v, $target, $matches, $path . '.' . $k);
    }
}

try {
    if (!is_dir($root)) {
        fwrite(STDERR, "[profile-history] data dir missing\n");
        exit(0);
    }
    $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS));
    foreach ($it as $fi) {
        if (!$fi->isFile() || $fi->getSize() > 20 * 1024 * 1024) continue;
        $file = $fi->getPathname();
        $raw = @file_get_contents($file);
        if (!is_string($raw) || strpos($raw, $target) === false) continue;
        $json = json_decode($raw, true);
        if (!is_array($json)) continue;
        $matches = [];
        rmx_walk_target($json, $target, $matches);
        foreach ($matches as $m) {
            $rows[] = [
                'file' => $file,
                'mtime' => $fi->getMTime(),
                'path' => $m['path'],
                'hashes' => $m['hashes'],
            ];
        }
    }
    usort($rows, fn($a,$b) => ($a['mtime'] <=> $b['mtime']) ?: strcmp($a['file'], $b['file']));
    if ($rows === []) {
        fwrite(STDERR, "[profile-history] no matching persisted records\n");
    } else {
        foreach ($rows as $row) {
            fwrite(STDERR,
                "[profile-history] mtime=" . gmdate('c', $row['mtime']) .
                " file=" . $row['file'] .
                " path=" . $row['path'] .
                " hashes=" . json_encode($row['hashes'], JSON_UNESCAPED_SLASHES) . "\n"
            );
        }
    }
} catch (Throwable $e) {
    fwrite(STDERR, "[profile-history] diagnostic failed: " . get_class($e) . ": " . $e->getMessage() . "\n");
}
