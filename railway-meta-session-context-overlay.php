<?php
/**
 * Restore the working Sep-16 transport behavior: keep the modern MetaApiClient
 * and official Graph endpoints, but attach the saved Facebook session cookies
 * from the ReMask profile to the same cURL request. No cookie/token values are
 * logged or exposed.
 */
$root = '/var/www/html';
$clientPath = $root . '/classes/MetaApiClient.php';
$endpointPath = $root . '/classes/MetaEndpoint.php';

$client = file_get_contents($clientPath);
if ($client === false) throw new RuntimeException('MetaApiClient.php not found');

if (!str_contains($client, 'private string $sessionCookies')) {
    $anchor = "class MetaApiClient\n{";
    $insert = <<<'PHP_CODE'
class MetaApiClient
{
    /** Saved Facebook browser-session cookies for the same profile/token. */
    private string $sessionCookies = '';

    public function setSessionCookies(string $cookies): self
    {
        $this->sessionCookies = trim($cookies);
        return $this;
    }
PHP_CODE;
    if (!str_contains($client, $anchor)) {
        throw new RuntimeException('MetaApiClient class anchor not found');
    }
    $client = str_replace($anchor, $insert, $client, $count);
    if ($count !== 1) throw new RuntimeException('MetaApiClient class patch count: ' . $count);
}

if (!str_contains($client, 'CURLOPT_COOKIE] = $this->sessionCookies')) {
    $proxyAnchor = <<<'PHP_CODE'
        if ($this->proxy !== null) {
            $this->proxy->AddToCurlOptions($options);
        }
PHP_CODE;
    $replacement = <<<'PHP_CODE'
        if ($this->sessionCookies !== '') {
            $options[CURLOPT_COOKIE] = $this->sessionCookies;
        }
        if ($this->proxy !== null) {
            $this->proxy->AddToCurlOptions($options);
        }
PHP_CODE;
    if (!str_contains($client, $proxyAnchor)) {
        throw new RuntimeException('MetaApiClient proxy anchor not found');
    }
    $client = str_replace($proxyAnchor, $replacement, $client, $count);
    if ($count !== 1) throw new RuntimeException('MetaApiClient cookie transport patch count: ' . $count);
}
file_put_contents($clientPath, $client);

$endpoint = file_get_contents($endpointPath);
if ($endpoint === false) throw new RuntimeException('MetaEndpoint.php not found');

if (!str_contains($endpoint, 'setSessionCookies($account->getCurlCookies())')) {
    $old = <<<'PHP_CODE'
        $usageKey = 'profile:' . hash('sha256', $accountName . '|' . $account->token);
        return new MetaAdsService(new MetaApiClient(
            $account->token,
            $account->proxy,
            null,
            60,
            self::usageStore(),
            $usageKey
        ));
PHP_CODE;
    $new = <<<'PHP_CODE'
        $usageKey = 'profile:' . hash('sha256', $accountName . '|' . $account->token);
        $client = new MetaApiClient(
            $account->token,
            $account->proxy,
            null,
            60,
            self::usageStore(),
            $usageKey
        );
        if ($account->isLegacyReady()) {
            $client->setSessionCookies($account->getCurlCookies());
        }
        return new MetaAdsService($client);
PHP_CODE;
    if (!str_contains($endpoint, $old)) {
        throw new RuntimeException('MetaEndpoint serviceForAccountName anchor not found');
    }
    $endpoint = str_replace($old, $new, $endpoint, $count);
    if ($count !== 1) throw new RuntimeException('MetaEndpoint session-context patch count: ' . $count);
}
file_put_contents($endpointPath, $endpoint);

fwrite(STDERR, "[meta-session-context] saved FB cookies wired into MetaApiClient; secrets not logged\n");
