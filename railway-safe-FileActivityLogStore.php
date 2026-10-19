<?php

require_once __DIR__ . '/ActivityLogStoreInterface.php';
require_once __DIR__.'/AtomicJsonFile.php';

class FileActivityLogStore implements ActivityLogStoreInterface
{
    public function __construct(private string $path)
    {
        $dir = dirname($path);
        if (!is_dir($dir) && !mkdir($dir, 0770, true) && !is_dir($dir)) {
            throw new RuntimeException("Unable to create activity log directory: {$dir}");
        }
    }

    public function append(array $event): array
    {
        $row = self::normalize($event);
        $row['id'] = $row['id'] ?: bin2hex(random_bytes(8));
        $row['occurred_at'] = $row['occurred_at'] ?: gmdate('c');
        $encoded = json_encode($row, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . "\n";
        $maximum = max(1048576, (int)(getenv('REMASK_ACTIVITY_LOG_MAX_BYTES') ?: 8388608));
        if (strlen($encoded) > min($maximum, 1048576)) throw new RuntimeException('ACTIVITY_EVENT_TOO_LARGE');
        $lock = AtomicJsonFile::lock($this->path);
        try {
            clearstatcache(true, $this->path);
            if (is_file($this->path) && filesize($this->path) + strlen($encoded) > $maximum) {
                if (!@rename($this->path, $this->path.'.previous')) throw new RuntimeException('ACTIVITY_LOG_ROTATION_FAILED');
            }
            AtomicJsonFile::assertSpace($this->path, strlen($encoded));
            $fh = fopen($this->path, 'ab');
            if ($fh === false) throw new RuntimeException('Unable to open activity log.');
            @chmod($this->path, 0600);
            try {
                $offset = 0;
                while ($offset < strlen($encoded)) {
                    $n = fwrite($fh, substr($encoded, $offset));
                    if ($n === false || $n === 0) throw new RuntimeException('ACTIVITY_LOG_SHORT_WRITE');
                    $offset += $n;
                }
                if (!fflush($fh)) throw new RuntimeException('ACTIVITY_LOG_FLUSH_FAILED');
            } finally { fclose($fh); }
        } finally { AtomicJsonFile::unlock($lock); }
        return $row;
    }

    public function recent(int $limit = 100, array $filters = []): array
    {
        $limit = max(1, min(500, $limit));
        if (!is_file($this->path)) return [];
        $lines = file($this->path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
        $rows = [];
        foreach (array_reverse($lines) as $line) {
            $row = json_decode($line, true);
            if (!is_array($row) || !self::matches($row, $filters)) continue;
            $rows[] = $row;
            if (count($rows) >= $limit) break;
        }
        return $rows;
    }

    private static function matches(array $row, array $filters): bool
    {
        foreach (['profile_name', 'business_id', 'account_id', 'action', 'status'] as $key) {
            $wanted = trim((string)($filters[$key] ?? ''));
            if ($wanted !== '' && (string)($row[$key] ?? '') !== $wanted) return false;
        }
        $jobId = trim((string)($filters['job_id'] ?? ''));
        if ($jobId !== '' && (string)(($row['details'] ?? [])['job_id'] ?? '') !== $jobId) return false;
        return true;
    }

    public static function normalize(array $event): array
    {
        $details = is_array($event['details'] ?? null) ? $event['details'] : [];
        foreach (['token','access_token','cookies','password','proxy_password','secret','authorization'] as $key) unset($details[$key]);
        return [
            'id' => (string)($event['id'] ?? ''),
            'occurred_at' => (string)($event['occurred_at'] ?? ''),
            'action' => trim((string)($event['action'] ?? 'unknown')),
            'entity_type' => trim((string)($event['entity_type'] ?? '')),
            'entity_id' => trim((string)($event['entity_id'] ?? '')),
            'profile_name' => trim((string)($event['profile_name'] ?? '')),
            'business_id' => trim((string)($event['business_id'] ?? '')),
            'account_id' => trim((string)($event['account_id'] ?? '')),
            'status' => strtoupper(trim((string)($event['status'] ?? 'SUCCESS'))),
            'summary' => trim((string)($event['summary'] ?? '')),
            'details' => $details,
        ];
    }
}
