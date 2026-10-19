<?php
require_once __DIR__.'/AtomicJsonFile.php';
require_once __DIR__ . '/BundleStoreInterface.php';

class MetaBundleStore implements BundleStoreInterface
{
    public function __construct(private string $file)
    {
        $dir = dirname($file);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create bundle storage directory: {$dir}");
        }
        AtomicJsonFile::initialize($this->file);
    }

    public function list(): array
    {
        $items = $this->readAll();
        usort($items, fn($a, $b) => strcmp((string)($b['updated_at'] ?? ''), (string)($a['updated_at'] ?? '')));
        return $items;
    }

    public function save(?string $id, string $name, array $data): array
    {
        $name = trim($name);
        if ($name === '') throw new InvalidArgumentException('Bundle name is required.');
        if (strlen($name) > 120) throw new InvalidArgumentException('Bundle name is too long.');
        if ($data === []) throw new InvalidArgumentException('Bundle data is empty.');

        $handle = AtomicJsonFile::lock($this->file);
        try {
            $items = AtomicJsonFile::read($this->file);
            $now = gmdate('c');
            $found = false;
            if ($id !== null && $id !== '') {
                foreach ($items as &$item) {
                    if (($item['id'] ?? '') === $id) {
                        $item['name'] = $name;
                        $item['data'] = $data;
                        $item['updated_at'] = $now;
                        $found = true;
                        $saved = $item;
                        break;
                    }
                }
                unset($item);
            }
            if (!$found) {
                $saved = [
                    'id' => gmdate('YmdHis') . '-' . bin2hex(random_bytes(4)),
                    'name' => $name,
                    'created_at' => $now,
                    'updated_at' => $now,
                    'data' => $data,
                ];
                $items[] = $saved;
            }
            $this->rewriteHandle($handle, $items);
            return $saved;
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    public function delete(string $id): bool
    {
        $id = trim($id);
        if ($id === '') throw new InvalidArgumentException('Bundle id is required.');
        $handle = AtomicJsonFile::lock($this->file);
        try {
            $items = AtomicJsonFile::read($this->file);
            $before = count($items);
            $items = array_values(array_filter($items, fn($item) => ($item['id'] ?? '') !== $id));
            $this->rewriteHandle($handle, $items);
            return count($items) < $before;
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }

    private function readAll(): array
    {
        return AtomicJsonFile::readLocked($this->file);
    }
    private function rewriteHandle($handle, array $items): void
    {
        AtomicJsonFile::replace($this->file, AtomicJsonFile::encode($items));
    }
}
