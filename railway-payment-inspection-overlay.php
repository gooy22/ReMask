<?php
declare(strict_types=1);
// REMASK_PRIVATE_PAYMENT_INSPECTION_V1
$root = '/var/www/html';
foreach (['railway-payment-card-vault.php'=>'classes/RemaskPaymentCardVault.php', 'railway-payment-card-endpoint.php'=>'ajax/paymentCards.php'] as $source=>$target) {
    if (!copy('/tmp/'.$source, $root.'/'.$target)) throw new RuntimeException('Card module install failed');
}

$path = $root . '/ajax/metaHierarchy.php';
$php = file_get_contents($path);
if (!is_string($php)) throw new RuntimeException('Hierarchy missing');

$helper = <<<'CANONICAL'

function hierarchy_canonical_account_rows(array $rows, string $profile): array {
    $accounts = [];
    foreach ($rows as $row) {
        if (!is_array($row)) continue;
        if (isset($row['profile']) && (string)$row['profile'] !== $profile) continue;
        $id = preg_replace('/^act_/', '', trim((string)($row['id'] ?? $row['account_id'] ?? '')));
        if (!preg_match('/^\d{5,30}$/', $id)) continue;
        $row['id'] = 'act_' . $id;
        $row['account_id'] = $id;
        if (isset($row['_raw_account_status'])) {
            $row['account_status'] = $row['_raw_account_status'];
            $row['disable_reason'] = $row['_raw_disable_reason'] ?? $row['disable_reason'] ?? null;
        }
        if (isset($row['account_status'])) unset($row['_provisioned_only']);
        $existing = $accounts[$id] ?? null;
        // Actual observed status always outranks a CREATE-only worker binding.
        if ($existing === null || (!isset($existing['account_status']) && isset($row['account_status']))) {
            $accounts[$id] = $row;
        }
    }
    return array_values($accounts);
}

function hierarchy_canonical_account_snapshot(string $profile, array $snapshot): array {
    $accounts = hierarchy_canonical_account_rows((array)($snapshot['ad_accounts'] ?? []), $profile);
    $snapshot['ad_accounts'] = $accounts;
    $snapshot['ad_accounts_count'] = count($accounts);
    foreach ((array)($snapshot['businesses'] ?? []) as $index => $business) {
        if (!is_array($business)) continue;
        $rows = array_values(array_filter($accounts, static fn($row) =>
            (string)($row['business_id'] ?? '') === (string)($business['id'] ?? '')));
        $snapshot['businesses'][$index]['accounts'] = $rows;
        $snapshot['businesses'][$index]['ad_account_count'] = count($rows);
    }
    if (is_array($snapshot['profile'] ?? null)) {
        $snapshot['profile']['rk_count'] = count($accounts);
        $snapshot['profile']['ad_accounts_count'] = count($accounts);
    }
    foreach ((array)($snapshot['profiles'] ?? []) as $index => $row) {
        if (!is_array($row) || (string)($row['name'] ?? $row['profile'] ?? '') !== $profile) continue;
        $snapshot['profiles'][$index]['rk_count'] = count($accounts);
        $snapshot['profiles'][$index]['ad_accounts_count'] = count($accounts);
    }
    return $snapshot;
}

CANONICAL;
$needle = '    return hierarchy_created_businesses_apply_display($profile, $snapshot);';
if (substr_count($php, $needle) !== 1) throw new RuntimeException('Canonical snapshot boundary missing');
$php = str_replace($needle, '    return hierarchy_canonical_account_snapshot($profile, hierarchy_created_businesses_apply_display($profile, $snapshot));', $php);
$php .= $helper;
$start = strpos($php, "    if (\$action === 'funding_status') {");
$end = strpos($php, "    if (\$action === 'set_delivery_status') {", $start === false ? 0 : $start);
if ($start === false || $end === false) throw new RuntimeException('Funding action boundary missing');
$php = substr_replace($php, <<<'FUNDING'
    if ($action === 'funding_status') {
        require __DIR__ . '/pythonWorkerJobs.php';
        exit;
    }

FUNDING, $start, $end - $start);
file_put_contents($path, $php);

$path = $root . '/scripts/workspace.js';
$js = file_get_contents($path);
$start = strpos($js, 'async function showFunding(){');
$end = strpos($js, 'function annotateDeliveryRows(', $start === false ? 0 : $start);
if ($start === false || $end === false) throw new RuntimeException('Funding UI boundary missing');
$js = substr_replace($js, file_get_contents('/tmp/railway-payment-inspection-ui.js') . "\n", $start, $end - $start);
$js = preg_replace('/function fundingStatusValue\(f\)\{[^\n]+\}/', <<<'STATUS'
function fundingStatusValue(f){ if(!f)return 'NOT LOADED'; if(f.funding_verified===true && f.checked_live===true && f.account_scope_verified===true)return 'READY'; if(f.verification_status==='LINKED' && f.card_linked===true && f.account_scope_verified===true)return 'LINKED'; if(f.verification_status==='NONE' && f.account_scope_verified===true)return 'NONE'; return 'UNKNOWN'; }
STATUS, $js, 1, $count);
if ($count !== 1) throw new RuntimeException('Funding readiness boundary missing');
$js = preg_replace('/function fundingCell\(f\)\{[^\n]+\}/', <<<'CELL'
function fundingCell(f){ const status=fundingStatusValue(f); if(status==='LINKED')return pill('ПРИВЯЗАНА · НЕ ПРОВЕРЕНА','warn'); if(status==='NONE')return pill('НЕТ КАРТЫ','warn'); if(status==='READY')return pill('ПОДТВЕРЖДЕНО','ok'); return pill('НЕ ПРОВЕРЕНО','warn'); }
CELL, $js, 1, $count);
if ($count !== 1) throw new RuntimeException('Funding cell boundary missing');
$js = str_replace("function closeModal(){", "function closeModal(){ if(typeof remaskClearPaymentSecrets==='function')remaskClearPaymentSecrets();", $js);
file_put_contents($path, $js);

foreach (['accounts.php', 'workspace.php'] as $name) {
    $path = $root . '/' . $name;
    $html = file_get_contents($path);
    // These pages use custom modals; Bootstrap JS requires absent jQuery.
    $html = str_replace('<script src="styles/bootstrap.min.js"></script>', '', $html);
    $html = str_replace('python-worker-ui-v194-cookie-only-v1', 'python-worker-ui-v194-cookie-only-v1-payment-cards-v2', $html);
    file_put_contents($path, $html);
}
fwrite(STDERR, "[private-payment] profile browser inspection and canonical RK display installed\n");
