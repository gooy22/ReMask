<?php
require_once __DIR__.'/AtomicJsonFile.php';
require_once __DIR__ . '/DraftStoreInterface.php';

/** Persistent server-side Launch drafts. Drafts contain configuration, never profile credentials. */
final class MetaDraftStore implements DraftStoreInterface
{
    public function __construct(private string $file)
    {
        $dir = dirname($file);
        if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create draft directory: {$dir}");
        }
        AtomicJsonFile::initialize($this->file);
    }

    public function list(?string $profile = null): array
    {
        $items = $this->readAll();
        if ($profile !== null && $profile !== '') {
            $items = array_values(array_filter($items, fn($d) => (string)($d['profile'] ?? '') === $profile));
        }
        usort($items, fn($a, $b) => strcmp((string)($b['updated_at'] ?? ''), (string)($a['updated_at'] ?? '')));
        return $items;
    }

    public function get(string $id): ?array
    {
        foreach ($this->readAll() as $item) if (($item['id'] ?? '') === $id) return $item;
        return null;
    }

    public function save(?string $id, string $name, string $profile, array $data): array
    {
        $name = trim($name); $profile = trim($profile);
        if ($name === '') throw new InvalidArgumentException('Draft name is required.');
        if (strlen($name) > 120) throw new InvalidArgumentException('Draft name is too long.');
        if ($data === []) throw new InvalidArgumentException('Draft data is empty.');
        $handle = $this->lock();
        try {
            $items = $this->readHandle($handle);
            $now = gmdate('c'); $saved = null;
            $id = trim((string)$id);
            if ($id !== '') {
                foreach ($items as &$item) {
                    if (($item['id'] ?? '') !== $id) continue;
                    $item['name'] = $name;
                    $item['profile'] = $profile;
                    $item['data'] = $data;
                    $item['updated_at'] = $now;
                    $saved = $item;
                    break;
                }
                unset($item);
            }
            if ($saved === null) {
                $saved = [
                    'id' => gmdate('YmdHis') . '-' . bin2hex(random_bytes(4)),
                    'name' => $name,
                    'profile' => $profile,
                    'created_at' => $now,
                    'updated_at' => $now,
                    'data' => $data,
                ];
                $items[] = $saved;
            }
            $this->writeHandle($handle, $items);
            return $saved;
        } finally { $this->unlock($handle); }
    }

    public function delete(string $id): bool
    {
        $id = trim($id); if ($id === '') throw new InvalidArgumentException('Draft id is required.');
        $handle = $this->lock();
        try {
            $items = $this->readHandle($handle); $before = count($items);
            $items = array_values(array_filter($items, fn($d) => ($d['id'] ?? '') !== $id));
            $this->writeHandle($handle, $items);
            return count($items) < $before;
        } finally { $this->unlock($handle); }
    }

    private function readAll(): array
    {
        return AtomicJsonFile::readLocked($this->file);
    }

    private function lock()
    {
        return AtomicJsonFile::lock($this->file);
    }

    private function unlock($h): void { if (is_resource($h)) { flock($h, LOCK_UN); fclose($h); } }
    private function readHandle($h): array
    {
        return AtomicJsonFile::read($this->file);
    }

    private function writeHandle($h, array $items): void
    {
        AtomicJsonFile::replace($this->file, AtomicJsonFile::encode($items));
    }

}
