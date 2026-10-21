<?php
require_once __DIR__.'/AtomicJsonFile.php';
require_once __DIR__ . '/MediaLibraryStoreInterface.php';

/** Local reusable media library. Storage abstraction can later be swapped for object storage. */
final class MediaLibraryStore implements MediaLibraryStoreInterface
{
    private string $indexFile;
    private string $filesDir;

    public function __construct(private string $root, private int $maxBytes = 157286400)
    {
        $this->root = rtrim($root, '/');
        $this->indexFile = $this->root . '/index.json';
        $this->filesDir = $this->root . '/files';
        foreach ([$this->root, $this->filesDir] as $dir) {
            if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) throw new RuntimeException("Could not create media library directory: {$dir}");
        }
        AtomicJsonFile::initialize($this->indexFile);
    }

    public function list(): array
    {
        $items = $this->readAll();
        usort($items, fn($a, $b) => strcmp((string)($b['created_at'] ?? ''), (string)($a['created_at'] ?? '')));
        return array_map([$this, 'publicRecord'], $items);
    }

    public function get(string $id): ?array
    {
        foreach ($this->readAll() as $item) if (($item['id'] ?? '') === $id) return $this->publicRecord($item);
        return null;
    }

    /** Internal record includes the storage path for Job creation. */
    public function getInternal(string $id): ?array
    {
        foreach ($this->readAll() as $item) {
            if (($item['id'] ?? '') !== $id) continue;
            $path = (string)($item['path'] ?? '');
            if ($path === '' || !is_file($path)) return null;
            return $item;
        }
        return null;
    }

    public function add(string $sourcePath, string $originalName): array
    {
        if (!is_file($sourcePath) || !is_readable($sourcePath)) throw new InvalidArgumentException('Uploaded media file is unavailable.');
        $size = filesize($sourcePath);
        if ($size === false || $size <= 0) throw new InvalidArgumentException('Uploaded media file is empty.');
        if ($size > $this->maxBytes) throw new InvalidArgumentException('MEDIA_LOCAL_FILE_LIMIT: file exceeds the configured Media Library per-file limit.');
        $mime = function_exists('mime_content_type') ? (mime_content_type($sourcePath) ?: '') : '';
        $type = str_starts_with($mime, 'image/') ? 'image' : (str_starts_with($mime, 'video/') ? 'video' : '');
        if ($type === '') throw new InvalidArgumentException("Unsupported media MIME type: {$mime}");
        $sha = hash_file('sha256', $sourcePath);
        if ($sha === false) throw new RuntimeException('Could not hash uploaded media.');

        $h = $this->lock();
        try {
            $items = $this->readHandle($h);
            foreach ($items as $item) {
                if (($item['sha256'] ?? '') === $sha && is_file((string)($item['path'] ?? ''))) return $this->publicRecord($item);
            }
            $ext = $this->safeExtension($originalName, $mime, $type);
            $id = 'm-' . substr($sha, 0, 24);
            $target = $this->filesDir . '/' . $id . ($ext !== '' ? '.' . $ext : '');
            $quota = max(1048576, (int)(getenv('REMASK_MEDIA_LIBRARY_TOTAL_BYTES') ?: 268435456));
            $used = 0;
            foreach (new DirectoryIterator($this->filesDir) as $file) if ($file->isFile()) $used += $file->getSize();
            if ($used + $size > $quota) throw new RuntimeException('MEDIA_LIBRARY_QUOTA_EXCEEDED');
            AtomicJsonFile::assertSpace($this->indexFile, $size + 1048576);
            if (!@copy($sourcePath, $target)) throw new RuntimeException('Could not persist Media Library file.');
            @chmod($target, 0600);
            $record = [
                'id' => $id,
                'original_name' => basename($originalName),
                'media_type' => $type,
                'mime_type' => $mime,
                'sha256' => $sha,
                'size_bytes' => $size,
                'path' => $target,
                'created_at' => gmdate('c'),
            ];
            $items[] = $record;
            try { $this->writeHandle($h, $items); }
            catch (Throwable $error) { @unlink($target); throw $error; }
            return $this->publicRecord($record);
        } finally { $this->unlock($h); }
    }

    public function delete(string $id): bool
    {
        $id = trim($id); if (!preg_match('/^m-[a-f0-9]{24}$/', $id)) throw new InvalidArgumentException('Invalid media id.');
        $h = $this->lock();
        try {
            $items = $this->readHandle($h); $removed = null; $kept = [];
            foreach ($items as $item) {
                if (($item['id'] ?? '') === $id) $removed = $item; else $kept[] = $item;
            }
            if ($removed === null) return false;
            $this->writeHandle($h, $kept);
            $path = (string)($removed['path'] ?? '');
            $root = realpath($this->filesDir);
            $real = $path !== '' && is_file($path) ? realpath($path) : false;
            if ($root !== false && $real !== false && str_starts_with($real, $root . DIRECTORY_SEPARATOR)) @unlink($real);
            return true;
        } finally { $this->unlock($h); }
    }

    private function publicRecord(array $record): array
    {
        unset($record['path']); return $record;
    }
    private function safeExtension(string $name, string $mime, string $type): string
    {
        $ext = strtolower(preg_replace('/[^a-zA-Z0-9]/', '', pathinfo($name, PATHINFO_EXTENSION)) ?: '');
        $allowedImage = ['jpg','jpeg','png','gif','webp']; $allowedVideo = ['mp4','mov','m4v','webm'];
        if ($type === 'image' && in_array($ext, $allowedImage, true)) return $ext;
        if ($type === 'video' && in_array($ext, $allowedVideo, true)) return $ext;
        return match ($mime) {
            'image/jpeg' => 'jpg', 'image/png' => 'png', 'image/gif' => 'gif', 'image/webp' => 'webp',
            'video/quicktime' => 'mov', 'video/webm' => 'webm', default => $type === 'video' ? 'mp4' : 'bin',
        };
    }
    private function readAll(): array
    {
        return AtomicJsonFile::readLocked($this->indexFile);
    }

    private function lock()
    {
        return AtomicJsonFile::lock($this->indexFile);
    }

    private function unlock($h): void { if (is_resource($h)) { flock($h, LOCK_UN); fclose($h); } }
    private function readHandle($h): array
    {
        return AtomicJsonFile::read($this->indexFile);
    }

    private function writeHandle($h, array $items): void
    {
        AtomicJsonFile::replace($this->indexFile, AtomicJsonFile::encode($items));
    }

}
