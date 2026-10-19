<?php
require_once __DIR__.'/AtomicJsonFile.php';

/** Small persistent JSON cache for read-only Meta discovery data. */
class MetaCacheStore
{
    public function __construct(private string $directory, private int $defaultTtl = 1800)
    {
        $this->defaultTtl = max(30, $defaultTtl);
        if (!is_dir($this->directory) && !@mkdir($this->directory, 0700, true) && !is_dir($this->directory)) {
            throw new RuntimeException("Could not create Meta cache directory: {$this->directory}");
        }
    }

    /**
     * Return a cached Meta discovery value, loading it at most once per key at a time.
     *
     * The lock deliberately covers the loader call. Meta discovery can take many seconds
     * through a profile proxy; without a per-key single-flight lock, simultaneous page
     * loads all observe the same miss and hammer Meta with duplicate requests.
     */
    public function remember(string $key, callable $loader, ?int $ttl = null, bool $force = false): array
    {
        $ttl = max(30, $ttl ?? $this->defaultTtl);
        $path = $this->pathForKey($key);
        $now = time();
        $initialFingerprint = $this->fingerprintPath($path);

        if (!$force) {
            $cached = $this->readFresh($path, $now);
            if ($cached !== null) return $this->formatResult($cached, $ttl, true, false);
        }

        $lock = $this->lockPath($path);
        try {
            // A request that waited for another loader should reuse what that request
            // just wrote instead of immediately performing the same expensive refresh.
            $lockedNow = time();
            $currentFingerprint = $this->fingerprintPath($path);
            $cached = $this->readFresh($path, $lockedNow);
            $changedWhileWaiting = $initialFingerprint !== $currentFingerprint;

            if ($cached !== null && (!$force || $changedWhileWaiting)) {
                return $this->formatResult($cached, $ttl, true, true);
            }

            $value = $loader();
            if (!is_array($value)) throw new RuntimeException('Meta cache loader must return an array.');

            $recordNow = time();
            $record = [
                'key' => $key,
                'revision' => bin2hex(random_bytes(8)),
                'fetched_at' => gmdate('c', $recordNow),
                'expires_at' => $recordNow + $ttl,
                'value' => $value,
            ];
            $this->writePath($path, $record);
            return $this->formatResult($record, $ttl, false, false);
        } finally {
            $this->unlockPath($lock);
        }
    }

    /** Read a cached value without calling Meta. Expired entries are returned as stale. */
    public function peek(string $key): ?array
    {
        $record = $this->readPath($this->pathForKey($key));
        if ($record === null || !array_key_exists('value', $record)) return null;
        $now = time();
        $expiresAt = (int)($record['expires_at'] ?? 0);
        return [
            'value' => is_array($record['value']) ? $record['value'] : [],
            'cache' => [
                'hit' => true,
                'stale' => $expiresAt <= $now,
                'coalesced' => false,
                'fetched_at' => $record['fetched_at'] ?? null,
                'expires_at' => $expiresAt ?: null,
                'ttl_seconds' => $this->defaultTtl,
            ],
        ];
    }

    public function forgetPrefix(string $prefix): int
    {
        $count = 0;
        foreach (glob(rtrim($this->directory, '/') . '/*.json') ?: [] as $path) {
            $record = $this->readPath($path);
            if ($record !== null && str_starts_with((string)($record['key'] ?? ''), $prefix)) {
                if (@unlink($path)) $count++;
            }
        }
        return $count;
    }

    private function pathForKey(string $key): string
    {
        return rtrim($this->directory, '/') . '/' . hash('sha256', $key) . '.json';
    }

    private function readFresh(string $path, int $now): ?array
    {
        $record = $this->readPath($path);
        if ($record === null || !array_key_exists('value', $record)) return null;
        return (int)($record['expires_at'] ?? 0) > $now ? $record : null;
    }

    private function formatResult(array $record, int $ttl, bool $hit, bool $coalesced): array
    {
        return [
            'value' => is_array($record['value'] ?? null) ? $record['value'] : [],
            'cache' => [
                'hit' => $hit,
                'stale' => false,
                'coalesced' => $coalesced,
                'fetched_at' => $record['fetched_at'] ?? null,
                'expires_at' => $record['expires_at'] ?? null,
                'ttl_seconds' => $ttl,
            ],
        ];
    }

    private function readPath(string $path): ?array
    {
        if (!is_file($path)) return null;
        $raw = @file_get_contents($path);
        if ($raw === false || $raw === '') return null;
        $decoded = json_decode($raw, true);
        return is_array($decoded) ? $decoded : null;
    }

    private function fingerprintPath(string $path): ?string
    {
        if (!is_file($path)) return null;
        $raw = @file_get_contents($path);
        return $raw === false ? null : hash('sha256', $raw);
    }

    private function writePath(string $path, array $record): void
    {
        $path = $path;
        $json = json_encode($record, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
        AtomicJsonFile::replace($path, $json, false);
    }

    private function lockPath(string $path)
    {
        $handle = fopen($path . '.lock', 'c+');
        if ($handle === false || !flock($handle, LOCK_EX)) {
            if (is_resource($handle)) fclose($handle);
            throw new RuntimeException('Could not lock Meta cache key.');
        }
        @chmod($path . '.lock', 0600);
        return $handle;
    }

    private function unlockPath($handle): void
    {
        if (is_resource($handle)) {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }
}
