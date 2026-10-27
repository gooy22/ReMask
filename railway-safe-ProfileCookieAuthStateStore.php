<?php
declare(strict_types=1);

/** Profile-scoped, credential-free marker for explicit Meta auth-cookie deletion. */
final class ProfileCookieAuthStateStore
{
    private string $path;

    public function __construct(?string $path = null)
    {
        $base = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
        $this->path = $path ?: ($base . '/profile-cookie-auth-state.json');
        $dir = dirname($this->path);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException('Cannot create profile cookie auth state directory.');
        }
    }

    public function mark(string $profile, string $actor, string $expectedXsSha256): void
    {
        $this->validate($profile, $actor, $expectedXsSha256);
        $this->mutate(function (array $all) use ($profile, $actor, $expectedXsSha256): array {
            $all[$profile] = [
                'actor_id' => $actor,
                'xs_sha256' => strtolower($expectedXsSha256),
                'updated_at' => gmdate('c'),
            ];
            return $all;
        });
    }

    public function matches(string $profile, string $actor, string $xs): bool
    {
        if ($profile === '' || $actor === '' || $xs === '') return false;
        $expected = hash('sha256', $xs);
        $lock = $this->lock(LOCK_SH);
        try {
            $row = $this->readUnlocked()[$profile] ?? null;
            return is_array($row)
                && hash_equals((string)($row['actor_id'] ?? ''), $actor)
                && hash_equals((string)($row['xs_sha256'] ?? ''), $expected);
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    /** Remove only a marker that no longer matches the profile's saved session. */
    public function clearIfStale(string $profile, string $actor, string $xs): void
    {
        if ($profile === '') return;
        $currentHash = $xs === '' ? '' : hash('sha256', $xs);
        $lock = $this->lock(LOCK_EX);
        try {
            $all = $this->readUnlocked();
            $row = $all[$profile] ?? null;
            if (!is_array($row)) return;
            if (($row['actor_id'] ?? '') === $actor
                && $currentHash !== ''
                && hash_equals((string)($row['xs_sha256'] ?? ''), $currentHash)) {
                return;
            }
            unset($all[$profile]);
            $this->writeUnlocked($all);
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    private function validate(string $profile, string $actor, string $hash): void
    {
        if ($profile === '' || strlen($profile) > 255
            || !preg_match('/^[0-9]+$/D', $actor)
            || !preg_match('/^[a-f0-9]{64}$/iD', $hash)) {
            throw new InvalidArgumentException('INVALID_PROFILE_COOKIE_AUTH_STATE');
        }
    }

    private function lock(int $mode)
    {
        $handle = fopen($this->path . '.lock', 'c+');
        if ($handle === false || !flock($handle, $mode)) {
            if (is_resource($handle)) fclose($handle);
            throw new RuntimeException('Cannot lock profile cookie auth state.');
        }
        @chmod($this->path . '.lock', 0600);
        return $handle;
    }

    private function mutate(callable $callback): void
    {
        $lock = $this->lock(LOCK_EX);
        try {
            $all = $this->readUnlocked();
            $next = $callback($all);
            if (!is_array($next)) throw new RuntimeException('Invalid profile cookie auth state.');
            $this->writeUnlocked($next);
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    private function readUnlocked(): array
    {
        if (!is_file($this->path)) return [];
        $raw = @file_get_contents($this->path);
        if (!is_string($raw) || trim($raw) === '') return [];
        $decoded = json_decode($raw, true);
        return is_array($decoded) ? $decoded : [];
    }

    private function writeUnlocked(array $all): void
    {
        $json = json_encode($all, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $tmp = $this->path . '.tmp.' . getmypid() . '.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp, $json . "\n", LOCK_EX) === false) {
            throw new RuntimeException('Cannot write profile cookie auth state.');
        }
        @chmod($tmp, 0600);
        if (!rename($tmp, $this->path)) {
            @unlink($tmp);
            throw new RuntimeException('Cannot replace profile cookie auth state.');
        }
        @chmod($this->path, 0600);
    }
}
