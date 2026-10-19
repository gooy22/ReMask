<?php

/**
 * Authenticated encryption for credentials persisted outside the legacy JSON store.
 *
 * Envelope format: v1:<base64(nonce || ciphertext+tag)>
 * Context is authenticated as AAD so a token ciphertext cannot be replayed as a
 * cookie/proxy field (or under another profile) without decryption failing.
 */
final class CredentialVault
{
    private const PREFIX = 'v1:';

    public function __construct(private string $key)
    {
        if (!extension_loaded('sodium')) {
            throw new RuntimeException('The sodium PHP extension is required for credential encryption.');
        }
        if (strlen($this->key) !== SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_KEYBYTES) {
            throw new InvalidArgumentException('Credential encryption key must be exactly 32 bytes.');
        }
    }

    public static function fromEnvironment(): self
    {
        [$key] = self::environmentKey();
        return new self($key);
    }

    /** Safe startup diagnostics: encoding only, never key material or a fingerprint. */
    public static function environmentEncoding(): string
    {
        return self::environmentKey()[1];
    }

    private static function environmentKey(): array
    {
        $raw = trim((string)(getenv('REMASK_CREDENTIALS_KEY') ?: ''));
        if ($raw === '') {
            throw new RuntimeException('REMASK_CREDENTIALS_KEY is required before encrypted profile storage can be used.');
        }

        $encoding = 'base64';
        $value = $raw;
        if (str_starts_with($raw, 'hex:') || preg_match('/^[0-9a-fA-F]{64}$/D', $raw)) {
            $encoding = 'hex';
            $value = str_starts_with($raw, 'hex:') ? substr($raw, 4) : $raw;
            $key = preg_match('/^[0-9a-fA-F]{64}$/D', $value) ? hex2bin($value) : false;
        } elseif (str_starts_with($raw, 'base64url:')) {
            $encoding = 'base64url';
            $value = substr($raw, 10);
            $key = self::decodeUrlKey($value);
        } else {
            if (str_starts_with($raw, 'base64:')) $value = substr($raw, 7);
            // Keep the historical standard-base64 interpretation unchanged.
            $key = base64_decode($value, true);
            if ((!is_string($key) || strlen($key) !== 32) && $value === $raw) {
                $encoding = 'base64url';
                $key = self::decodeUrlKey($value);
            }
        }
        if ($key === false || strlen($key) !== SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_KEYBYTES) {
            throw new RuntimeException('REMASK_CREDENTIALS_KEY must encode exactly 32 random bytes as base64, base64url or hex; configured_chars='.strlen($raw).'.');
        }
        return [$key, $encoding];
    }

    private static function decodeUrlKey(string $value): string|false
    {
        if (!preg_match('/^[A-Za-z0-9_-]{43}=?$/D', $value)) return false;
        $key = base64_decode(strtr(rtrim($value, '='), '-_', '+/').'=', true);
        // Reject non-canonical padding bits rather than silently changing key material.
        if (!is_string($key) || rtrim(strtr(base64_encode($key), '+/', '-_'), '=') !== rtrim($value, '=')) return false;
        return $key;
    }

    public static function generateEnvironmentValue(): string
    {
        if (!extension_loaded('sodium')) {
            throw new RuntimeException('The sodium PHP extension is required.');
        }
        return 'base64:' . base64_encode(random_bytes(SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_KEYBYTES));
    }

    public function encrypt(string $plaintext, string $context): string
    {
        $nonce = random_bytes(SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_NPUBBYTES);
        $ciphertext = sodium_crypto_aead_xchacha20poly1305_ietf_encrypt(
            $plaintext,
            $context,
            $nonce,
            $this->key
        );
        return self::PREFIX . base64_encode($nonce . $ciphertext);
    }

    public function decrypt(string $envelope, string $context): string
    {
        if (!str_starts_with($envelope, self::PREFIX)) {
            throw new RuntimeException('Unknown credential envelope version.');
        }
        $blob = base64_decode(substr($envelope, strlen(self::PREFIX)), true);
        $nonceBytes = SODIUM_CRYPTO_AEAD_XCHACHA20POLY1305_IETF_NPUBBYTES;
        if ($blob === false || strlen($blob) <= $nonceBytes) {
            throw new RuntimeException('Credential envelope is malformed.');
        }
        $nonce = substr($blob, 0, $nonceBytes);
        $ciphertext = substr($blob, $nonceBytes);
        $plaintext = sodium_crypto_aead_xchacha20poly1305_ietf_decrypt(
            $ciphertext,
            $context,
            $nonce,
            $this->key
        );
        if ($plaintext === false) {
            throw new RuntimeException('Credential decryption failed: wrong key/context or corrupted ciphertext.');
        }
        return $plaintext;
    }

    public function encryptJson(mixed $value, string $context): string
    {
        return $this->encrypt(json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR), $context);
    }

    public function decryptJson(string $envelope, string $context): mixed
    {
        return json_decode($this->decrypt($envelope, $context), true, 512, JSON_THROW_ON_ERROR);
    }
}
