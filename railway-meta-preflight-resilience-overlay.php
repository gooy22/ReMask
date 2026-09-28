<?php
$servicePath = '/var/www/html/classes/MetaAdsService.php';

function remask_replace_service_method(string $source, string $methodName, string $replacement): string
{
    $needle = 'function ' . $methodName . '(';
    $start = strpos($source, $needle);
    if ($start === false) throw new RuntimeException($methodName . ' method not found');

    $brace = strpos($source, '{', $start);
    if ($brace === false) throw new RuntimeException($methodName . ' opening brace not found');

    $depth = 0;
    $end = null;
    $len = strlen($source);
    for ($i = $brace; $i < $len; $i++) {
        if ($source[$i] === '{') $depth++;
        elseif ($source[$i] === '}') {
            $depth--;
            if ($depth === 0) {
                $end = $i + 1;
                break;
            }
        }
    }
    if ($end === null) throw new RuntimeException($methodName . ' closing brace not found');

    return substr($source, 0, $start) . $replacement . substr($source, $end);
}

$service = file_get_contents($servicePath);
if ($service === false) throw new RuntimeException('MetaAdsService.php not found');

$preflightMethod = <<<'PHP_METHOD'
// REMASK_META_PREFLIGHT_RESILIENT_V1
function preflight(?string $accountId = null): array
    {
        $warnings = [];

        try {
            $identity = $this->getIdentity();
        } catch (Throwable $identityError) {
            throw new RuntimeException(
                'META_PREFLIGHT_IDENTITY_FAILED: ' . $identityError->getMessage(),
                0,
                $identityError
            );
        }

        $permissions = ['data' => []];
        $permissionsAvailable = true;
        try {
            $permissions = $this->getPermissions();
        } catch (Throwable $permissionsError) {
            $permissionsAvailable = false;
            $warnings[] = [
                'stage' => 'permissions',
                'message' => (string)$permissionsError->getMessage(),
            ];
        }

        $granted = [];
        foreach (($permissions['data'] ?? []) as $permission) {
            if (($permission['status'] ?? '') === 'granted' && isset($permission['permission'])) {
                $granted[] = $permission['permission'];
            }
        }

        $result = [
            'api_version' => $this->client->getApiVersion(),
            'identity' => $identity,
            'permissions' => $granted,
            'permissions_available' => $permissionsAvailable,
            'ads_management_granted' => in_array('ads_management', $granted, true),
            'ads_read_granted' => in_array('ads_read', $granted, true),
            'business_management_granted' => in_array('business_management', $granted, true),
        ];

        try {
            if ($accountId !== null && trim($accountId) !== '') {
                $result['ad_account'] = $this->getAdAccount($accountId);
            } else {
                $result['ad_accounts'] = $this->listAdAccounts();
            }
        } catch (Throwable $accountError) {
            throw new RuntimeException(
                'META_PREFLIGHT_AD_ACCOUNTS_FAILED: ' . $accountError->getMessage(),
                0,
                $accountError
            );
        }

        if ($warnings !== []) {
            $result['_preflight_warnings'] = $warnings;
        }
        $result['api_usage'] = $this->getApiUsage();
        return $result;
    }
PHP_METHOD;

$service = remask_replace_service_method($service, 'preflight', $preflightMethod);
file_put_contents($servicePath, $service);

fwrite(STDERR, "[meta-preflight-resilience] permissions are optional; identity + RK inventory remain authoritative\n");
