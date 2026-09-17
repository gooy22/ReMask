<?php
/**
 * Keep the profile's configured proxy for normal Meta traffic, but do not let a
 * broken HTTPS CONNECT tunnel make the read-only account inventory unusable.
 * Only GET requests may retry directly, and only after a concrete proxy tunnel
 * failure (407 / CONNECT tunnel failure). Mutations never bypass the proxy.
 */
$clientPath = '/var/www/html/classes/MetaApiClient.php';
$php = file_get_contents($clientPath);
if ($php === false) {
    throw new RuntimeException('MetaApiClient.php not found');
}

$oldCall = <<<'PHP'
            [$raw, $curlError, $curlErrno, $httpStatus] = $this->executeCurl($method, $path, $params, $multipart, $responseHeaders);
            $this->lastResponseHeaders = $responseHeaders;
PHP;
$newCall = <<<'PHP'
            [$raw, $curlError, $curlErrno, $httpStatus] = $this->executeCurl($method, $path, $params, $multipart, $responseHeaders);

            // A proxy can be reachable for a generic health check yet reject an
            // HTTPS CONNECT to graph.facebook.com (HTTP 407). For account sync
            // reads, retry the same official Graph request directly. This does
            // not alter the saved proxy and can never affect POST/DELETE/upload.
            $proxyErrorText = strtolower((string)$curlError);
            $proxyTunnelRejected = $raw === false
                && $method === 'GET'
                && $this->proxy !== null
                && (
                    $httpStatus === 407
                    || str_contains($proxyErrorText, 'response 407')
                    || str_contains($proxyErrorText, 'connect tunnel failed')
                    || str_contains($proxyErrorText, 'proxy authentication required')
                );
            if ($proxyTunnelRejected) {
                $responseHeaders = [];
                [$raw, $curlError, $curlErrno, $httpStatus] = $this->executeCurl(
                    $method,
                    $path,
                    $params,
                    $multipart,
                    $responseHeaders,
                    true
                );
                if ($raw !== false) {
                    $responseHeaders['x-remask-read-transport'] = 'direct-fallback-after-proxy-407';
                }
            }

            $this->lastResponseHeaders = $responseHeaders;
PHP;
$count = 0;
$php = str_replace($oldCall, $newCall, $php, $count);
if ($count !== 1) {
    throw new RuntimeException('MetaApiClient request patch failed: ' . $count);
}

$oldSignature = "    private function executeCurl(string \$method, string \$path, array \$params, bool \$multipart, array &\$responseHeaders): array";
$newSignature = "    private function executeCurl(string \$method, string \$path, array \$params, bool \$multipart, array &\$responseHeaders, bool \$bypassProxy = false): array";
$count = 0;
$php = str_replace($oldSignature, $newSignature, $php, $count);
if ($count !== 1) {
    throw new RuntimeException('MetaApiClient executeCurl signature patch failed: ' . $count);
}

$oldProxy = <<<'PHP'
        if ($this->proxy !== null) {
            $this->proxy->AddToCurlOptions($options);
        }
PHP;
$newProxy = <<<'PHP'
        if ($this->proxy !== null && !$bypassProxy) {
            $this->proxy->AddToCurlOptions($options);
        }
PHP;
$count = 0;
$php = str_replace($oldProxy, $newProxy, $php, $count);
if ($count !== 1) {
    throw new RuntimeException('MetaApiClient proxy bypass patch failed: ' . $count);
}

file_put_contents($clientPath, $php);
fwrite(STDERR, "[meta-read-fallback] GET direct fallback after proxy 407 enabled; mutations remain proxied\n");
