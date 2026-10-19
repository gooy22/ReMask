<?php
declare(strict_types=1);
$source = __DIR__.'/../railway-safe-CredentialVault.php';
if (!is_file($source)) $source = '/tmp/railway-safe-CredentialVault.php';
require_once $source;
function ensure(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
$saved = getenv('REMASK_CREDENTIALS_KEY');
try {
    // All encodings refer to the same bytes, including legacy v1 envelopes.
    $bytes = hex2bin(str_repeat('fbef', 16));
    $b64 = base64_encode($bytes);
    $url = rtrim(strtr($b64, '+/', '-_'), '=');
    $formats = [
        'standard'=>$b64, 'prefixed'=>'base64:'.$b64,
        'unpadded'=>rtrim($b64, '='), 'hex'=>bin2hex($bytes),
        'hex-prefixed'=>'hex:'.strtoupper(bin2hex($bytes)),
        'url'=>$url, 'url-padded'=>$url.'=', 'url-prefixed'=>'base64url:'.$url,
    ];
    $legacy = new CredentialVault($bytes);
    $context = 'profile-json:fixture';
    $payload = ['name'=>'fixture', 'cookies'=>['xs'=>'fixture-secret']];
    $envelope = $legacy->encryptJson($payload, $context);
    foreach ($formats as $label=>$value) {
        putenv('REMASK_CREDENTIALS_KEY='.$value);
        $vault = CredentialVault::fromEnvironment();
        ensure($vault->decryptJson($envelope, $context) === $payload, $label.' failed to read existing v1 envelope');
        ensure($legacy->decryptJson($vault->encryptJson($payload, $context), $context) === $payload, $label.' changed key bytes');
        ensure(getenv('REMASK_CREDENTIALS_KEY') === $value, 'Environment key was changed');
        try { $vault->decryptJson($envelope, 'profile-json:other'); throw new LogicException('AAD bypass'); }
        catch (RuntimeException $e) { ensure(str_contains($e->getMessage(), 'decryption failed'), 'Wrong context must fail'); }
    }
    foreach (['', 'base64:broken', 'hex:'.str_repeat('a', 63), str_repeat('a',32),
              'base64:'.base64_encode(str_repeat('x',31)), 'base64url:'.str_repeat('-',42)] as $invalid) {
        putenv('REMASK_CREDENTIALS_KEY='.$invalid);
        try { CredentialVault::fromEnvironment(); throw new LogicException('Invalid key accepted'); }
        catch (RuntimeException $e) {
            ensure(str_contains($e->getMessage(), 'REMASK_CREDENTIALS_KEY'), 'Unexpected key error');
            if ($invalid !== '') ensure(!str_contains($e->getMessage(), $invalid), 'Key leaked into diagnostics');
        }
    }
    putenv('REMASK_CREDENTIALS_KEY=hex:'.bin2hex(str_repeat('z',32)));
    try { CredentialVault::fromEnvironment()->decryptJson($envelope,$context); throw new LogicException('Wrong key accepted'); }
    catch (RuntimeException $e) { ensure(str_contains($e->getMessage(), 'decryption failed'), 'Wrong key must fail'); }
    echo "Credential key compatibility: 8 encodings preserve v1 ciphertext, context and wrong-key guards passed\n";
} finally {
    $saved === false ? putenv('REMASK_CREDENTIALS_KEY') : putenv('REMASK_CREDENTIALS_KEY='.$saved);
}
