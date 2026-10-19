<?php
declare(strict_types=1);

/** Local infrastructure only: no profile sessions or Meta requests. */
final class RemaskReadiness {
    public static function check(array $directories, string $accountsFile, callable $workerProbe): array {
        $storage = true;
        foreach (array_unique($directories) as $directory) {
            if (!is_dir($directory) || !is_writable($directory)) { $storage=false; break; }
            $file = rtrim($directory, '/').'/.ready-'.bin2hex(random_bytes(8));
            $handle = @fopen($file, 'x');
            if ($handle === false) { $storage=false; break; }
            try {
                $storage = fwrite($handle, 'ready')===5 && fflush($handle);
            } finally { fclose($handle); @unlink($file); }
            if (!$storage) break;
        }
        foreach (array_unique($directories) as $directory) {
            $free = @disk_free_space($directory);
            if ($free === false || $free < max(1048576, (int)(getenv('REMASK_STORAGE_RESERVE_BYTES') ?: 33554432))) $storage = false;
        }
        $accounts = false;
        // Do not expose or return account contents in a public health response.
        if (is_file($accountsFile) && is_readable($accountsFile)) {
            try {
                $data = json_decode((string)file_get_contents($accountsFile), true, 512, JSON_THROW_ON_ERROR);
                $accounts = is_array($data);
            } catch (Throwable $error) {}
        }
        $worker = false;
        try {
            $result = $workerProbe();
            $worker = is_array($result) && ($result['ok'] ?? null)===true
                && ($result['service'] ?? '')==='remask-python-worker'
                && (int)($result['worker_concurrency'] ?? 0)>0;
        } catch (Throwable $error) {}
        return ['ok'=>$storage && $accounts && $worker, 'service'=>'remask',
            'checks'=>['storage'=>$storage, 'accounts'=>$accounts, 'worker'=>$worker]];
    }
}
