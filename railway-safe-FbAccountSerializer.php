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
