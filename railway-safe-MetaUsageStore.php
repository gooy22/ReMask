<?php
require_once __DIR__.'/AtomicJsonFile.php';
require_once __DIR__ . '/MetaUsageStoreInterface.php';

class MetaUsageStore implements MetaUsageStoreInterface
{
    public function __construct(private string $file)
    {
        $dir = dirname($this->file);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create Meta usage directory: {$dir}");
        }
    }

    public function get(string $key): ?array
    {
        $key = trim($key);
        if ($key === '') return null;
        $all = $this->readAll();
        $value = $all[$key] ?? null;
        return is_array($value) ? $value : null;
    }

    public function put(string $key, array $usage): void
    {
        $key = trim($key);
        if ($key === '') throw new InvalidArgumentException('Meta usage key is required.');
        $handle = fopen($this->file . '.lock', 'c+');
        if ($handle === false || !flock($handle, LOCK_EX)) {
            throw new RuntimeException('Could not lock Meta usage store.');
        }
        try {
            $all = $this->readAllUnlocked();
            $all[$key] = [
                'updated_at' => gmdate('c'),
                'usage' => $usage,
            ];
            $this->writeAllUnlocked($all);
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    private function readAll(): array
    {
        $handle = fopen($this->file . '.lock', 'c+');
        if ($handle === false || !flock($handle, LOCK_SH)) return [];
        try {
            return $this->readAllUnlocked();
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    private function readAllUnlocked(): array
    {
        if (!is_file($this->file)) return [];
        $raw = file_get_contents($this->file);
        if ($raw === false || trim($raw) === '') return [];
        $data = json_decode($raw, true);
        return is_array($data) ? $data : [];
    }

    private function writeAllUnlocked(array $data): void
    {
        $path = $this->file;
        $json = json_encode($data, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
        AtomicJsonFile::replace($path, $json, false);
    }

}
