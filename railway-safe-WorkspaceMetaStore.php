<?php
require_once __DIR__.'/AtomicJsonFile.php';

/** Small persistent UI metadata store (groups/tags) kept separate from credentials. */
final class WorkspaceMetaStore
{
    public function __construct(private string $file)
    {
        $dir = dirname($file);
        if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) throw new RuntimeException("Could not create workspace metadata directory: {$dir}");
        AtomicJsonFile::initialize($this->file, "{}\n");
    }

    public function all(): array
    {
        $h = AtomicJsonFile::lock($this->file, LOCK_SH);
        try { return AtomicJsonFile::readAny($this->file); }
        finally { AtomicJsonFile::unlock($h); }
    }

    public function setProxyHealth(string $profile, array $health): array
    {
        $profile = trim($profile);
        if ($profile === '') throw new InvalidArgumentException('profile is required');
        return $this->mutate(function(array $data) use ($profile, $health) {
            $row = is_array($data[$profile] ?? null) ? $data[$profile] : [];
            $row['proxy_health'] = $health;
            $data[$profile] = $row;
            return $data;
        });
    }

    public function setGroup(array $profiles, string $group): array
    {
        $profiles = array_values(array_unique(array_filter(array_map(static fn($v) => trim((string)$v), $profiles))));
        $group = trim($group);
        $length = function_exists('mb_strlen') ? mb_strlen($group) : strlen($group);
        if ($length > 80) throw new InvalidArgumentException('Group name is too long.');
        return $this->mutate(function(array $data) use ($profiles, $group) {
            foreach ($profiles as $profile) {
                $row = is_array($data[$profile] ?? null) ? $data[$profile] : [];
                if ($group === '') unset($row['group']); else $row['group'] = $group;
                if ($row === []) unset($data[$profile]); else $data[$profile] = $row;
            }
            return $data;
        });
    }

    private function mutate(callable $fn): array
    {
        $h = AtomicJsonFile::lock($this->file);
        try {
            $data = $fn(AtomicJsonFile::readAny($this->file));
            $json = json_encode((object)$data, JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR)."\n";
            AtomicJsonFile::replace($this->file, $json);
            return $data;
        } finally { AtomicJsonFile::unlock($h); }
    }
}
