<?php
require_once __DIR__.'/AtomicJsonFile.php';

final class CreativePresetStore
{
    private string $root;
    private string $indexFile;

    public function __construct(?string $root = null)
    {
        $dataRoot = rtrim((string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')), '/');
        $this->root = rtrim($root ?: (getenv('REMASK_CREATIVE_PRESET_DIR') ?: ($dataRoot . '/creative-presets')), '/');
        $this->indexFile = $this->root . '/index.json';
        if (!is_dir($this->root) && !@mkdir($this->root, 0700, true) && !is_dir($this->root)) {
            throw new RuntimeException('Could not create Creative Library directory.');
        }
        AtomicJsonFile::initialize($this->indexFile);
    }

    public function list(): array
    {
        $rows = $this->readAll();
        usort($rows, static fn(array $a, array $b): int =>
            strcmp((string)($b['updated_at'] ?? $b['created_at'] ?? ''), (string)($a['updated_at'] ?? $a['created_at'] ?? ''))
        );
        return $rows;
    }

    public function get(string $id): ?array
    {
        $id = trim($id);
        foreach ($this->readAll() as $row) {
            if ((string)($row['id'] ?? '') === $id) return $row;
        }
        return null;
    }

    public function save(?string $id, array $data): array
    {
        $id = trim((string)$id);
        $h = $this->lock();
        try {
            $rows = $this->readHandle($h);
            $now = gmdate('c');
            $found = null;
            foreach ($rows as $i => $row) {
                if ($id !== '' && (string)($row['id'] ?? '') === $id) {
                    $found = $i;
                    break;
                }
            }
            if ($found === null) {
                $id = 'cr-' . bin2hex(random_bytes(8));
                $record = array_merge([
                    'id' => $id,
                    'created_at' => $now,
                ], $data, ['updated_at' => $now]);
                $rows[] = $record;
            } else {
                $record = array_merge($rows[$found], $data, ['id'=>$id,'updated_at'=>$now]);
                $rows[$found] = $record;
            }
            $this->writeHandle($h,$rows);
            return $record;
        } finally {
            $this->unlock($h);
        }
    }

    public function duplicate(string $id): array
    {
        $row = $this->get($id);
        if ($row === null) throw new InvalidArgumentException('Creative not found.');
        unset($row['id'],$row['created_at'],$row['updated_at']);
        $row['name'] = trim((string)($row['name'] ?? 'Creative')) . ' copy';
        return $this->save(null,$row);
    }

    public function delete(string $id): ?array
    {
        $id = trim($id);
        $h = $this->lock();
        try {
            $rows = $this->readHandle($h);
            $removed = null;
            $kept = [];
            foreach ($rows as $row) {
                if ((string)($row['id'] ?? '') === $id) $removed = $row;
                else $kept[] = $row;
            }
            if ($removed !== null) $this->writeHandle($h,$kept);
            return $removed;
        } finally {
            $this->unlock($h);
        }
    }

    public function mediaReferenced(string $mediaId): bool
    {
        foreach ($this->readAll() as $row) {
            if ((string)($row['media_library_id'] ?? '') === $mediaId) return true;
        }
        return false;
    }

    private function readAll(): array
    {
        return AtomicJsonFile::readLocked($this->indexFile);
    }

    private function lock()
    {
        return AtomicJsonFile::lock($this->indexFile);
    }

    private function unlock($h): void
    {
        if (is_resource($h)) { flock($h,LOCK_UN); fclose($h); }
    }

    private function readHandle($h): array
    {
        return AtomicJsonFile::read($this->indexFile);
    }

    private function writeHandle($h,array $rows): void
    {
        AtomicJsonFile::replace($this->indexFile, AtomicJsonFile::encode($rows));
    }

}