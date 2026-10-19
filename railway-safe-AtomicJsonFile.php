<?php
declare(strict_types=1);

/** One stable lock inode; data and its last valid snapshot are atomically replaced. */
final class AtomicJsonFile
{
    private static function canonical(string $path): string
    {
        // Legacy webroot paths are symlinks into the Railway volume. Never replace the link.
        if (is_link($path)) {
            $resolved = realpath($path);
            if ($resolved === false) throw new RuntimeException('STORAGE_LINK_UNAVAILABLE');
            return $resolved;
        }
        return $path;
    }
    public static function lock(string $path, int $mode = LOCK_EX)
    {
        $path = self::canonical($path);
        $dir = dirname($path);
        if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) throw new RuntimeException('STORAGE_DIRECTORY_UNAVAILABLE');
        $h = @fopen($path.'.lock', 'c+');
        if ($h === false) throw new RuntimeException('STORAGE_LOCK_UNAVAILABLE');
        @chmod($path.'.lock', 0600);
        if (!flock($h, $mode)) { fclose($h); throw new RuntimeException('STORAGE_LOCK_UNAVAILABLE'); }
        return $h;
    }
    public static function unlock($h): void
    {
        if (is_resource($h)) { flock($h, LOCK_UN); fclose($h); }
    }
    public static function initialize(string $path, string $initial = "[]\n"): void
    {
        $path = self::canonical($path);
        $h = self::lock($path);
        try { if (!file_exists($path)) self::replace($path, $initial, false); }
        finally { self::unlock($h); }
    }
    public static function readAny(string $path): array
    {
        $path = self::canonical($path);
        $raw = @file_get_contents($path);
        if ($raw === false || trim($raw) === '') throw new RuntimeException('STORAGE_INVALID_JSON');
        try { $rows = json_decode($raw, true, 512, JSON_THROW_ON_ERROR); }
        catch (JsonException $error) { throw new RuntimeException('STORAGE_INVALID_JSON', 0, $error); }
        if (!is_array($rows)) throw new RuntimeException('STORAGE_INVALID_JSON');
        return $rows;
    }
    public static function read(string $path): array
    {
        $rows = self::readAny($path);
        if (!array_is_list($rows)) throw new RuntimeException('STORAGE_INVALID_JSON');
        return $rows;
    }
    public static function readLocked(string $path): array
    {
        $h = self::lock($path, LOCK_SH);
        try { return self::read($path); } finally { self::unlock($h); }
    }
    public static function assertSpace(string $path, int $bytes = 0): void
    {
        $reserve = max(1048576, (int)(getenv('REMASK_STORAGE_RESERVE_BYTES') ?: 33554432));
        $free = @disk_free_space(dirname($path));
        if ($free === false || $free < $reserve + max(0, $bytes)) throw new RuntimeException('STORAGE_LOW_SPACE');
    }
    private static function stage(string $path, string $bytes): string
    {
        self::assertSpace($path, strlen($bytes));
        $temp = $path.'.tmp-'.bin2hex(random_bytes(12));
        $h = @fopen($temp, 'x+b');
        if ($h === false) throw new RuntimeException('STORAGE_WRITE_FAILED');
        @chmod($temp, 0600);
        try {
            $offset = 0; $length = strlen($bytes);
            while ($offset < $length) {
                $n = fwrite($h, substr($bytes, $offset));
                if ($n === false || $n === 0) throw new RuntimeException('STORAGE_SHORT_WRITE');
                $offset += $n;
            }
            if (!fflush($h) || (function_exists('fsync') && !fsync($h))) throw new RuntimeException('STORAGE_FLUSH_FAILED');
            fclose($h); return $temp;
        } catch (Throwable $error) {
            if (is_resource($h)) fclose($h);
            @unlink($temp); throw $error;
        }
    }
    /** Caller must hold lock(path). previousBytes may be re-encrypted during migration. */
    public static function replace(string $path, string $json, bool $backup = true, ?string $previousBytes = null): void
    {
        $path = self::canonical($path);
        json_decode($json, true, 512, JSON_THROW_ON_ERROR);
        $temp = self::stage($path, $json);
        try {
            if ($backup && is_file($path)) {
                self::readAny($path); // Never rotate corrupt input over a recoverable snapshot.
                $old = $previousBytes ?? file_get_contents($path);
                if (!is_string($old)) throw new RuntimeException('STORAGE_READ_FAILED');
                $snapshot = self::stage($path.'.previous', $old);
                if (!@rename($snapshot, $path.'.previous')) { @unlink($snapshot); throw new RuntimeException('STORAGE_BACKUP_FAILED'); }
            }
            if (!@rename($temp, $path)) throw new RuntimeException('STORAGE_COMMIT_FAILED');
            // The file contents were fsynced before rename. Persist the directory entry too.
            $dir = @fopen(dirname($path), 'r');
            if (is_resource($dir)) { if (function_exists('fsync')) @fsync($dir); fclose($dir); }
        } finally { if (is_file($temp)) @unlink($temp); }
    }
    public static function encode(array $rows): string
    {
        return json_encode(array_values($rows), JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR)."\n";
    }
    public static function restorePrevious(string $path): void
    {
        $path = self::canonical($path);
        $h = self::lock($path);
        try { self::readAny($path.'.previous'); self::replace($path, (string)file_get_contents($path.'.previous'), false); }
        finally { self::unlock($h); }
    }
}
