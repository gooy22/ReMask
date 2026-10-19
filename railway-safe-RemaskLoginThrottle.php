<?php
declare(strict_types=1);
require_once __DIR__.'/AtomicJsonFile.php';
final class RemaskLoginThrottle
{
    private string $file;
    public function __construct(string $directory)
    {
        // Do not trust arbitrary X-Forwarded-For values. One bounded file per service.
        $this->file = rtrim($directory, '/').'/login-attempts.json';
        AtomicJsonFile::initialize($this->file);
    }
    public function allow(): bool
    {
        $h = AtomicJsonFile::lock($this->file);
        try {
            $now = time();
            $rows = array_values(array_filter(AtomicJsonFile::read($this->file), fn($ts)=>is_int($ts) && $ts>$now-300));
            if (count($rows) >= 30) return false;
            $rows[] = $now;
            AtomicJsonFile::replace($this->file, AtomicJsonFile::encode($rows), false);
            return true;
        } finally { AtomicJsonFile::unlock($h); }
    }
    public function clear(): void
    {
        $h = AtomicJsonFile::lock($this->file);
        try { AtomicJsonFile::replace($this->file, "[]\n", false); }
        finally { AtomicJsonFile::unlock($h); }
    }
}
