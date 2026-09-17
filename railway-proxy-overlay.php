<?php
/** Runtime overlay for proxy support in the extracted clean-preview tree. */
$root = '/var/www/html';

$proxyClassPath = $root . '/classes/RemaskProxy.php';
$proxyClassPhp = <<<'PHP'
<?php

class RemaskProxy
{
    public string $ip = '';
    public int $port = 0;
    public string $login = '';
    public string $password = '';
    public string $type = 'http';

    public function toArray(): array
    {
        return ['type'=>$this->type,'ip'=>$this->ip,'port'=>$this->port,'login'=>$this->login,'password'=>$this->password];
    }

    public static function fromSemicolonString($proxyString): ?RemaskProxy
    {
        $proxyString = trim((string)$proxyString);
        if ($proxyString === '') return null;
        return self::parse($proxyString);
    }

    public static function parse(string $raw): RemaskProxy
    {
        $raw = preg_replace('/\s+/', '', trim($raw)) ?? trim($raw);
        if ($raw === '') throw new InvalidArgumentException('Proxy cannot be empty.');
        $p = new RemaskProxy();

        if (preg_match('#^(https?|socks5h?|socks)://#i', $raw)) {
            $u = parse_url($raw);
            if (!is_array($u) || empty($u['host']) || empty($u['port'])) {
                throw new InvalidArgumentException('Invalid proxy URL. Use http://host:port or http://user:pass@host:port.');
            }
            $p->type = self::normalizeType((string)($u['scheme'] ?? 'http'));
            $p->ip = (string)$u['host'];
            $p->port = (int)$u['port'];
            $p->login = isset($u['user']) ? rawurldecode((string)$u['user']) : '';
            $p->password = isset($u['pass']) ? rawurldecode((string)$u['pass']) : '';
            return self::validated($p);
        }

        $items = explode(':', $raw);
        if (count($items) === 5) {
            [$type, $host, $port, $login, $password] = $items;
            $p->type = self::normalizeType($type);
            $p->ip = $host;
            $p->port = (int)$port;
            $p->login = $login;
            $p->password = $password;
            return self::validated($p);
        }
        if (count($items) === 4) {
            [$host, $port, $login, $password] = $items;
            $p->type = 'http';
            $p->ip = $host;
            $p->port = (int)$port;
            $p->login = $login;
            $p->password = $password;
            return self::validated($p);
        }
        if (count($items) === 3 && in_array(strtolower($items[0]), ['http','https','socks','socks5','socks5h'], true)) {
            [$type, $host, $port] = $items;
            $p->type = self::normalizeType($type);
            $p->ip = $host;
            $p->port = (int)$port;
            return self::validated($p);
        }
        if (count($items) === 2) {
            [$host, $port] = $items;
            $p->type = 'http';
            $p->ip = $host;
            $p->port = (int)$port;
            return self::validated($p);
        }

        throw new InvalidArgumentException('Invalid proxy format. Accepted: http:host:port:user:pass, host:port:user:pass, http://user:pass@host:port, http://host:port.');
    }

    private static function normalizeType(string $type): string
    {
        $type = strtolower(trim($type));
        if ($type === 'https') return 'http';
        if ($type === 'socks') return 'socks5';
        if (!in_array($type, ['http','socks5','socks5h'], true)) throw new InvalidArgumentException('Proxy type must be http, socks5, or socks5h.');
        return $type;
    }

    private static function validated(RemaskProxy $p): RemaskProxy
    {
        $p->ip = trim($p->ip);
        if ($p->ip === '' || preg_match('/[\s\/]/', $p->ip)) throw new InvalidArgumentException('Proxy host is invalid.');
        if ($p->port < 1 || $p->port > 65535) throw new InvalidArgumentException('Proxy port is invalid.');
        return $p;
    }

    public static function fromArray($proxyData): ?RemaskProxy
    {
        if (!$proxyData) return null;
        $p = new RemaskProxy();
        $p->type = self::normalizeType((string)($proxyData['type'] ?? 'http'));
        $p->ip = (string)($proxyData['ip'] ?? '');
        $p->port = (int)($proxyData['port'] ?? 0);
        $p->login = (string)($proxyData['login'] ?? '');
        $p->password = (string)($proxyData['password'] ?? '');
        return self::validated($p);
    }

    public function AddToCurlOptions(array &$optArray): void
    {
        $type = strtolower($this->type);
        if ($type === 'http') $optArray[CURLOPT_PROXYTYPE] = CURLPROXY_HTTP;
        elseif ($type === 'socks5h' && defined('CURLPROXY_SOCKS5_HOSTNAME')) $optArray[CURLOPT_PROXYTYPE] = CURLPROXY_SOCKS5_HOSTNAME;
        else $optArray[CURLOPT_PROXYTYPE] = CURLPROXY_SOCKS5;
        $optArray[CURLOPT_PROXY] = ($type === 'http' ? 'http://' : 'socks5://') . $this->ip;
        $optArray[CURLOPT_PROXYPORT] = $this->port;
        if ($this->login !== '' || $this->password !== '') $optArray[CURLOPT_PROXYUSERPWD] = $this->login . ':' . $this->password;
    }
}
PHP;
file_put_contents($proxyClassPath, $proxyClassPhp);
fwrite(STDERR, "[remask proxy overlay] classes/RemaskProxy.php ready\n");

$accountsJsPath = $root . '/scripts/accounts.js';
if (is_file($accountsJsPath)) {
    $js = file_get_contents($accountsJsPath);
    if ($js !== false) {
        $js = str_replace(": 'http:ip:port:login:pass';", ": 'http:host:port:user:pass | host:port:user:pass | http://user:pass@host:port';", $js);
        $old = <<<'JS'
    if (proxy) {
        const parts = proxy.split(':');
        if (parts.length !== 5) { alert("Proxy must be 'type:ip:port:login:pass'."); return false; }
        if (!['http', 'socks'].includes(parts[0].toLowerCase())) { alert("Proxy type must be 'http' or 'socks'."); return false; }
    }
JS;
        $new = <<<'JS'
    if (proxy) {
        const compact = proxy.replace(/\s+/g, '');
        const ok = /^(https?|socks5?h?):\/\/[^\s]+:\d+/.test(compact)
            || /^(https?|socks5?h?|socks):[^:\/\s]+:\d+(:[^:]+:.+)?$/.test(compact)
            || /^[^:\/\s]+:\d+(:[^:]+:.+)?$/.test(compact);
        if (!ok) {
            alert("Proxy format: http:host:port:user:pass, host:port:user:pass, http://user:pass@host:port, or http://host:port.");
            return false;
        }
    }
JS;
        if (strpos($js, $old) !== false) $js = str_replace($old, $new, $js);
        if (strpos($js, 'Proxy format: http:host:port:user:pass') === false) {
            fwrite(STDERR, "[remask proxy overlay] accounts validation block not patched\n");
            exit(41);
        }
        file_put_contents($accountsJsPath, $js);
        fwrite(STDERR, "[remask proxy overlay] scripts/accounts.js ready\n");
    }
}

$checkAccountPath = $root . '/ajax/checkAccount.php';
$checkAccountPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/ResponseFormatter.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';

function remask_graph_get(string $path, array $params, string $token, ?RemaskProxy $proxy): array
{
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/' . ltrim($path, '/');
    if ($params !== []) $url .= '?' . http_build_query($params);
    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 35,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'Authorization: Bearer ' . $token],
        CURLOPT_USERAGENT => 'ReMask-MetaApiCheck/1.1',
    ];
    if ($proxy !== null) $proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    $curlError = curl_error($ch);
    $curlErrno = curl_errno($ch);
    $httpStatus = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if ($raw === false) throw new RuntimeException('Proxy/transport failed before Meta response: ' . ($curlError ?: ('cURL errno ' . $curlErrno)));
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) throw new RuntimeException('Meta returned non-JSON response, HTTP ' . $httpStatus . '.');
    if (isset($decoded['error']) && is_array($decoded['error'])) {
        $err = $decoded['error'];
        $message = trim((string)($err['message'] ?? 'Meta rejected request.'));
        $type = trim((string)($err['type'] ?? ''));
        $code = isset($err['code']) ? (int)$err['code'] : 0;
        $subcode = isset($err['error_subcode']) ? (int)$err['error_subcode'] : 0;
        $parts = [$message];
        if ($type !== '') $parts[] = 'type ' . $type;
        if ($code) $parts[] = 'code ' . $code;
        if ($subcode) $parts[] = 'subcode ' . $subcode;
        throw new RuntimeException('Meta API check failed: ' . implode(', ', $parts));
    }
    if ($httpStatus < 200 || $httpStatus >= 300) throw new RuntimeException('Meta API returned HTTP ' . $httpStatus . '.');
    return $decoded;
}

try {
    $token = trim((string)($_POST['token'] ?? $_POST['access_token'] ?? ''));
    if ($token === '') throw new InvalidArgumentException('Access token is required.');
    $proxyRaw = trim((string)($_POST['proxy'] ?? ''));
    $proxy = $proxyRaw !== '' ? RemaskProxy::fromSemicolonString($proxyRaw) : null;
    $me = remask_graph_get('me', ['fields' => 'id,name'], $token, $proxy);
    $permissions = remask_graph_get('me/permissions', ['limit' => 200], $token, $proxy);
    $adsManagementGranted = false;
    foreach ((array)($permissions['data'] ?? []) as $permission) {
        if (is_array($permission) && ($permission['permission'] ?? '') === 'ads_management' && ($permission['status'] ?? '') === 'granted') { $adsManagementGranted = true; break; }
    }
    if (!$adsManagementGranted) throw new RuntimeException('ads_management permission is not granted for this token.');
    $adAccounts = remask_graph_get('me/adaccounts', ['fields'=>'id,name,account_status,currency,disable_reason','limit'=>50], $token, $proxy);
    ResponseFormatter::Respond(['res' => json_encode([
        'ok'=>true,
        'profile'=>['id'=>(string)($me['id'] ?? ''), 'name'=>(string)($me['name'] ?? '')],
        'ads_management_granted'=>true,
        'ad_accounts_count'=>count((array)($adAccounts['data'] ?? [])),
        'proxy_used'=>$proxy !== null,
        'proxy_hint'=>$proxy ? ($proxy->type . ':' . $proxy->ip . ':' . $proxy->port) : '',
        'message'=>'Meta API profile is valid.',
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)]);
} catch (Throwable $e) {
    http_response_code(200);
    ResponseFormatter::Respond(['error' => $e->getMessage()]);
}
PHP;
file_put_contents($checkAccountPath, $checkAccountPhp);
fwrite(STDERR, "[remask proxy overlay] ajax/checkAccount.php ready\n");
