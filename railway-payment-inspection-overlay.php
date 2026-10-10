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
    $snapshot = hierarchy_asset_types_snapshot($snapshot);
    $accounts = hierarchy_canonical_account_rows((array)($snapshot['ad_accounts'] ?? []), $profile);
    // Rehydrate masked binding information from the durable card vault. This
    // is cached attachment evidence, never a fresh bank/funding verification.
    try {
        require_once __DIR__.'/../classes/RemaskPaymentCardVault.php';
        $cardData = (new RemaskPaymentCardVault())->all();
        $cards = array_column($cardData['cards'], null, 'id');
        foreach ($accounts as &$account) {
            unset($account['cached_card_binding']);
            foreach ($cardData['bindings'] as $binding) {
                if (($binding['profile'] ?? null) !== $profile ||
                    ($binding['account_id'] ?? null) !== $account['account_id']) continue;
                $card = $cards[$binding['card_id'] ?? ''] ?? null;
                if (!is_array($card) || ($binding['last4'] ?? null) !== $card['last4']) continue;
                $account['cached_card_binding'] = array_intersect_key($binding,
                    array_flip(['status','last4','updated_at','last_result_code','card_confirmation_status']));
                $account['cached_card_binding']['brand'] = $card['brand'];
            }
        }
        unset($account);
    } catch (Throwable $e) { /* An unavailable vault must not hide inventory. */ }
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
$needle = '    return hierarchy_asset_types_snapshot($snapshot);';
if (substr_count($php, $needle) !== 1) throw new RuntimeException('Canonical snapshot boundary missing');
$php = str_replace($needle, '    return hierarchy_canonical_account_snapshot($profile, $snapshot);', $php);
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
$cardUi = file_get_contents('/tmp/railway-payment-inspection-ui.js');
if (!is_string($cardUi) || $cardUi === '') throw new RuntimeException('Card UI missing');
$js = substr_replace($js, $cardUi . "\n", $start, $end - $start);
$js = preg_replace('/function fundingStatusValue\(f\)\{[^\n]+\}/', <<<'STATUS'
function fundingStatusValue(f){ if(!f)return 'NOT LOADED'; if(f.funding_verified===true && f.checked_live===true && f.account_scope_verified===true)return 'READY'; if(f.verification_status==='LINKED' && f.card_linked===true && f.account_scope_verified===true)return 'LINKED'; if(f.verification_status==='NONE' && f.account_scope_verified===true)return 'NONE'; return 'UNKNOWN'; }
STATUS, $js, 1, $count);
if ($count !== 1) throw new RuntimeException('Funding readiness boundary missing');
$js = preg_replace('/function fundingCell\(f\)\{[^\n]+\}/', <<<'CELL'
function fundingCell(f,cached){ const status=fundingStatusValue(f); const methods=f?.payment_methods||[]; const card=methods.find(m=>/^\d{4}$/.test(String(m.last4||''))); const mask=card?esc((card.type||'Карта')+' •••• '+card.last4):cached&&/^\d{4}$/.test(String(cached.last4||''))?esc((cached.brand||'Карта')+' •••• '+cached.last4):''; const detail=mask?'<div class="sub">'+mask+'</div>':''; if(card&&(card.needs_verification===true||card.verification_tasks?.length)||cached?.status==='ACTION_REQUIRED'&&cached?.last_result_code==='CARD_BANK_CONFIRMATION_REQUIRED'&&status!=='NONE'&&card?.card_confirmation_status!=='CLEAR')return pill('ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ','warn')+detail; if(status==='LINKED')return pill('КАРТА ПРИВЯЗАНА','ok')+detail+(card?.card_confirmation_status==='CLEAR'?'<div class="sub">Банковская авторизация не проверена</div>':''); if(status==='NONE')return pill('НЕТ КАРТЫ','warn'); if(status==='READY')return pill('ПОДТВЕРЖДЕНО','ok')+detail; if(cached?.status==='LINKED')return pill('КАРТА ПРИВЯЗАНА','ok')+detail+'<div class="sub">Сохранённый результат Meta</div>'; if(cached?.last_result_code==='CARD_SAVED_CREDENTIAL_UNLINKED')return pill('СОХРАНЕНА · НЕ ПРИВЯЗАНА','warn')+detail; return pill('НЕ ПРОВЕРЕНО','warn')+detail; }
CELL, $js, 1, $count);
if ($count !== 1) throw new RuntimeException('Funding cell boundary missing');
$js = str_replace('fundingCell(row.funding)', 'fundingCell(row.funding,row.cached_card_binding)', $js, $count);
if ($count !== 1) throw new RuntimeException('Funding row boundary missing');
$js = str_replace("function closeModal(){", "function closeModal(){ if(typeof remaskClearPaymentSecrets==='function')remaskClearPaymentSecrets();", $js);
file_put_contents($path, $js);

foreach (['accounts.php', 'workspace.php'] as $name) {
    $path = $root . '/' . $name;
    $html = file_get_contents($path);
    // These pages use custom modals; Bootstrap JS requires absent jQuery.
    $html = str_replace('<script src="styles/bootstrap.min.js"></script>', '', $html);
    // The workspace bundle is cached by browsers. Every card UI change must
    // produce a new URL, including deployments that leave the worker UI version
    // unchanged. A content hash also stays stable across identical builds.
    $cardHash = substr(hash('sha256', $cardUi), 0, 12);
    // PHP templates may escape attribute quotes. Match the asset URL itself
    // and update every reference without depending on HTML quote syntax.
    $html = preg_replace_callback(
        '~scripts/workspace\.js(?:\?[^"\'<>\x5c\s]*)?~i',
        static function (array $match) use ($cardHash): string {
            $url = html_entity_decode($match[0], ENT_QUOTES | ENT_HTML5, 'UTF-8');
            $fragment = '';
            if (($position = strpos($url, '#')) !== false) {
                $fragment = substr($url, $position);
                $url = substr($url, 0, $position);
            }
            $parts = explode('?', $url, 2);
            $parameters = array_values(array_filter(
                explode('&', $parts[1] ?? ''),
                static fn(string $part): bool => $part !== ''
                    && urldecode(explode('=', $part, 2)[0]) !== 'payment_cards',
            ));
            $parameters[] = 'payment_cards=' . $cardHash;
            $url = $parts[0] . '?' . implode('&', $parameters) . $fragment;
            return htmlspecialchars($url, ENT_QUOTES, 'UTF-8');
        },
        $html,
        -1,
        $count,
    );
    // Some pages do not load workspace.js; leave their asset references alone.
    if (!is_string($html)) throw new RuntimeException('Workspace card cache rewrite failed for ' . $name);
    file_put_contents($path, $html);
}
fwrite(STDERR, "[private-payment] profile browser inspection and canonical RK display installed\n");

