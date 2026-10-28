<?php
require_once __DIR__ . '/ProfileStoreInterface.php';
require_once __DIR__ . '/AtomicJsonFile.php';
require_once __DIR__ . '/CredentialVault.php';

class FbAccountSerializer implements ProfileStoreInterface
{
    private string $filename;

    public function __construct($filename)
    {
        $this->filename = (string)$filename;
        $dir = dirname($this->filename);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException("Could not create account storage directory: {$dir}");
        }
        AtomicJsonFile::initialize($this->filename);
        // Production requires a durable key. Tests without one may read legacy fixtures.
        if (getenv('REMASK_ENCRYPT_PROFILE_JSON') === '1') CredentialVault::fromEnvironment();
    }

    public function serialize(array $facebookAccounts): void
    {
        $this->mutate(function () use ($facebookAccounts) {
            return array_values($facebookAccounts);
        });
    }

    /** Migrate plaintext once; an encrypted profile must never rotate its backup on boot. */
    public function ensureEncrypted(): void
    {
        $handle = AtomicJsonFile::lock($this->filename);
        try {
            $rows = AtomicJsonFile::read($this->filename);
            $accounts = $this->decodeAccounts(AtomicJsonFile::encode($rows));
            if (getenv('REMASK_ENCRYPT_PROFILE_JSON') !== '1') return;
            if (count($rows) === count($accounts) && count(array_filter($rows,
                static fn($row) => is_array($row) && is_string($row['credentials_encrypted'] ?? null))) === count($rows)) return;
            AtomicJsonFile::replace($this->filename, $this->encodeAccounts($accounts), true,
                $this->encodeAccounts($accounts));
        } finally { AtomicJsonFile::unlock($handle); }
    }

    public function deserialize(): array
    {
        $handle = AtomicJsonFile::lock($this->filename, LOCK_SH);
        try { return $this->decodeAccounts(AtomicJsonFile::encode(AtomicJsonFile::read($this->filename))); }
        finally { AtomicJsonFile::unlock($handle); }
    }

    public function addOrUpdateAccount(FbAccount $acc): void
    {
        if ($acc->name === '') throw new InvalidArgumentException('Profile name is required.');
        $this->mutate(function (array $accounts) use ($acc) {
            $found = false;
            foreach ($accounts as $i => $account) {
                if ($account->name === $acc->name) {
                    $accounts[$i] = $acc;
                    $found = true;
                    break;
                }
            }
            if (!$found) $accounts[] = $acc;
            return array_values($accounts);
        });
    }

    public function compareAndSwapCookies(string $name, string $actor, string $expectedXs, array $cookies): bool
    {
        require_once __DIR__ . '/RemaskCookieSession.php';
        try {
            $this->mutate(function (array $accounts) use ($name, $actor, $expectedXs, $cookies) {
                foreach ($accounts as $index=>$account) {
                    if ($account->name!==$name) continue;
                    $next=RemaskCookieSession::replacement($account, $actor, $expectedXs, $cookies);
                    if ($next===null) throw new RuntimeException('PROFILE_SESSION_CHANGED');
                    $updated=clone $account;
                    $updated->cookies=$next;
                    $accounts[$index]=$updated;
                    return $accounts;
                }
                throw new RuntimeException('PROFILE_SESSION_CHANGED');
            });
            return true;
        } catch (RuntimeException $error) {
            if ($error->getMessage()==='PROFILE_SESSION_CHANGED') return false;
            throw $error;
        }
    }

    public function getAccountByName($name): ?FbAccount
    {
        foreach ($this->deserialize() as $account) {
            if ($account->name === $name) return $account;
        }
        return null;
    }

    private function resolvedStoragePath(): string
    {
        if (is_link($this->filename)) {
            $resolved = realpath($this->filename);
            if ($resolved === false) throw new RuntimeException('STORAGE_LINK_UNAVAILABLE');
            return $resolved;
        }
        return $this->filename;
    }

    public function getPreviousAccountByName($name): ?FbAccount
    {
        if (!is_string($name) || $name === '') return null;
        $storagePath = $this->resolvedStoragePath();
        $backupPath = $storagePath . '.previous';
        if (!is_file($backupPath)) return null;
        $handle = AtomicJsonFile::lock($storagePath, LOCK_SH);
        try {
            if (!is_file($backupPath)) return null;
            $rows = AtomicJsonFile::read($backupPath);
            foreach ($this->decodeAccounts(AtomicJsonFile::encode($rows)) as $account) {
                if ($account->name === $name) return $account;
            }
            return null;
        } finally { AtomicJsonFile::unlock($handle); }
    }

    /**
     * Restore a verified same-actor session from the encrypted previous snapshot.
     * The current and backup fingerprints are checked while holding the profile-store lock.
     */
    public function restorePreviousCookiesIfUnchanged(
        string $name,
        string $actor,
        string $expectedCurrentXsSha256,
        string $expectedPreviousXsSha256,
        array $verifiedCookies
    ): bool {
        require_once __DIR__ . '/RemaskCookieSession.php';
        if ($name === '' || $actor === ''
            || !preg_match('/^[a-f0-9]{64}$/D', $expectedCurrentXsSha256)
            || !preg_match('/^[a-f0-9]{64}$/D', $expectedPreviousXsSha256)) {
            return false;
        }
        try {
            $this->mutate(function (array $accounts) use (
                $name, $actor, $expectedCurrentXsSha256, $expectedPreviousXsSha256, $verifiedCookies
            ) {
                $currentIndex = null;
                foreach ($accounts as $index => $account) {
                    if ($account->name === $name) { $currentIndex = $index; break; }
                }
                if ($currentIndex === null) throw new RuntimeException('PROFILE_SESSION_CHANGED');
                $current = $accounts[$currentIndex];
                $currentCookies = RemaskCookieSession::map((array)$current->cookies);
                $currentXs = (string)($currentCookies['xs'] ?? '');
                if ((string)($currentCookies['c_user'] ?? '') !== $actor || $currentXs === ''
                    || !hash_equals($expectedCurrentXsSha256, hash('sha256', $currentXs))) {
                    throw new RuntimeException('PROFILE_SESSION_CHANGED');
                }

                $backupPath = $this->resolvedStoragePath() . '.previous';
                if (!is_file($backupPath)) throw new RuntimeException('PROFILE_SESSION_CHANGED');
                $backupRows = AtomicJsonFile::read($backupPath);
                $backupAccounts = $this->decodeAccounts(AtomicJsonFile::encode($backupRows));
                $backup = null;
                foreach ($backupAccounts as $account) {
                    if ($account->name === $name) { $backup = $account; break; }
                }
                if (!$backup) throw new RuntimeException('PROFILE_SESSION_CHANGED');
                $backupCookies = RemaskCookieSession::map((array)$backup->cookies);
                $backupXs = (string)($backupCookies['xs'] ?? '');
                if ((string)($backupCookies['c_user'] ?? '') !== $actor || $backupXs === ''
                    || !hash_equals($expectedPreviousXsSha256, hash('sha256', $backupXs))) {
                    throw new RuntimeException('PROFILE_SESSION_CHANGED');
                }

                $next = RemaskCookieSession::replacement(
                    $current, $actor, $currentXs, $verifiedCookies
                );
                if ($next === null) throw new RuntimeException('PROFILE_SESSION_CHANGED');
                $nextCookies = RemaskCookieSession::map($next);
                $nextXs = (string)($nextCookies['xs'] ?? '');
                if ($nextXs === ''
                    || hash('sha256', $nextXs) === $expectedCurrentXsSha256) {
                    throw new RuntimeException('PROFILE_SESSION_CHANGED');
                }
                $updated = clone $current;
                $updated->cookies = $next;
                $accounts[$currentIndex] = $updated;
                return $accounts;
            });
            return true;
        } catch (RuntimeException $error) {
            if ($error->getMessage() === 'PROFILE_SESSION_CHANGED') return false;
            throw $error;
        }
    }

    public function deleteAccountByName($name): array
    {
        return $this->mutate(function (array $accounts) use ($name) {
            return array_values(array_filter($accounts, fn($account) => $account->name !== $name));
        });
    }

    private function mutate(callable $callback): array
    {
        $handle = AtomicJsonFile::lock($this->filename);
        try {
            $old = $this->decodeAccounts(AtomicJsonFile::encode(AtomicJsonFile::read($this->filename)));
            $accounts = $callback($old);
            if (!is_array($accounts)) throw new RuntimeException('Account storage mutation returned invalid data.');
            // Re-encrypt the previous snapshot too; do not retain plaintext during migration.
            AtomicJsonFile::replace($this->filename, $this->encodeAccounts($accounts), true, $this->encodeAccounts($old));
            return $accounts;
        } finally { AtomicJsonFile::unlock($handle); }
    }

    private function encodeAccounts(array $accounts): string
    {
        $encrypted = getenv('REMASK_ENCRYPT_PROFILE_JSON') === '1' || trim((string)getenv('REMASK_CREDENTIALS_KEY')) !== '';
        $vault = $encrypted ? CredentialVault::fromEnvironment() : null;
        $rows = [];
        foreach ($accounts as $account) {
            $data = $account->toArray();
            $rows[] = $vault ? ['name'=>$account->name, 'credentials_encrypted'=>$vault->encryptJson($data, 'profile-json:'.$account->name)] : $data;
        }
        return AtomicJsonFile::encode($rows);
    }

    private function decodeAccounts(string $raw): array
    {
        $data = json_decode($raw ?: '[]', true);
        if (!is_array($data)) throw new RuntimeException('Account storage contains invalid JSON.');
        $accounts = [];
        foreach ($data as $accountData) {
            if (!is_array($accountData)) continue;
            if (isset($accountData['credentials_encrypted'])) {
                $name = (string)($accountData['name'] ?? '');
                $accountData = CredentialVault::fromEnvironment()->decryptJson((string)$accountData['credentials_encrypted'], 'profile-json:'.$name);
                if (!is_array($accountData) || (string)($accountData['name'] ?? '') !== $name) throw new RuntimeException('PROFILE_CREDENTIAL_CONTEXT_INVALID');
            }
            $accounts[] = FbAccount::fromArray($accountData);
        }
        return $accounts;
    }
}
