<?php
declare(strict_types=1);
// REMASK_PRIVATE_LAUNCH_CATALOG_V1
$root='/var/www/html';
file_put_contents($root.'/classes/RemaskConfirmedPageIdentities.json',
    json_encode(require '/tmp/railway-confirmed-page-identities.php', JSON_UNESCAPED_UNICODE));
file_put_contents($root.'/classes/RemaskPrivateLaunchCatalog.php', <<<'CATALOG'
<?php
declare(strict_types=1);

/** Local private-worker catalog. Inventory identity is not live advertising permission. */
final class RemaskPrivateLaunchCatalog
{
    private static function id(mixed $value): string {
        $id = trim((string)$value);
        if (str_starts_with($id, 'act_')) $id = substr($id, 4);
        return preg_match('/^\d{5,30}$/', $id) ? $id : '';
    }

    public static function fromState(string $profile, array $snapshots, array $bindings = [], int $now = 0, array $pageIdentities = []): array {
        $now = $now ?: time();
        $snapshot = is_array($snapshots[$profile] ?? null) ? $snapshots[$profile] : [];
        $accounts = [];
        foreach ((array)($snapshot['ad_accounts'] ?? []) as $row) {
            if (!is_array($row)) continue;
            if (isset($row['profile']) && (string)$row['profile'] !== $profile) continue;
            $id = self::id($row['id'] ?? $row['account_id'] ?? '');
            if ($id === '') continue;
            $observedStatus = $row['_raw_account_status'] ?? $row['account_status'] ?? null;
            if (isset($accounts[$id]) && ($observedStatus === null || $accounts[$id]['account_status'] !== null)) continue;
            $accounts[$id] = [
                'id' => 'act_' . $id, 'account_id' => $id,
                'name' => trim((string)($row['name'] ?? $id)),
                'currency' => trim((string)($row['currency'] ?? '')),
                'account_status' => $observedStatus !== null ? (int)$observedStatus : null,
                'business_id' => self::id($row['business_id'] ?? ''),
                'profile' => $profile, 'source' => 'last_confirmed_private_inventory',
                'advertising_access_verified' => false,
            ];
        }
        $binding = is_array($bindings[$profile] ?? null) ? $bindings[$profile] : [];
        $items = isset($binding['ad_account_id']) ? [$binding] : (array)($binding['ad_accounts'] ?? []);
        foreach ($items as $businessKey => $row) {
            if (!is_array($row)) continue;
            $id = self::id($row['ad_account_id'] ?? '');
            $business = self::id($row['business_id'] ?? $businessKey);
            // This file is emitted only after exact worker RK confirmation.
            // Merge identity only: it does not prove funding, ACTIVE or Page access.
            if ($id === '' || $business === '' || isset($accounts[$id])) continue;
            $accounts[$id] = [
                'id'=>'act_'.$id, 'account_id'=>$id,
                'name'=>trim((string)($row['account_name'] ?? $id)),
                'currency'=>'', 'account_status'=>null, 'business_id'=>$business,
                'profile'=>$profile, 'source'=>'python_worker_confirmed_create',
                'advertising_access_verified'=>false,
            ];
        }
        $pageRows = array_values(array_filter((array)($snapshot['pages'] ?? []),
            static fn($row) => is_array($row) && (!isset($row['profile']) || (string)$row['profile'] === $profile)));
        $present = [];
        foreach ($pageRows as $row) {
            $id = self::id($row['id'] ?? '');
            if ($id !== '') $present[$id] = true;
        }
        $aliases = []; $names = []; $ambiguous = [];
        $evidence = (array)($pageIdentities[$profile] ?? []);
        foreach ($pageRows as $row) {
            if (($row['ownership_verified'] ?? null) === true && isset($row['profile_id']))
                $evidence[] = $row + ['identity_verified'=>true];
        }
        foreach ($evidence as $row) {
            if (!is_array($row) || ($row['identity_verified'] ?? null) !== true) continue;
            $id = self::id($row['id'] ?? '');
            $alias = self::id($row['profile_id'] ?? '');
            // Evidence can repair identities already in the saved snapshot; it
            // cannot add a Page or prove live advertising access.
            if ($id === '' || $alias === '' || $id === $alias || !isset($present[$id])) continue;
            if (isset($aliases[$alias]) && $aliases[$alias] !== $id) {
                $ambiguous[$alias] = true; unset($aliases[$alias]); continue;
            }
            if (isset($ambiguous[$alias])) continue;
            $aliases[$alias] = $id;
            if (trim((string)($row['name'] ?? '')) !== '') $names[$id] = trim((string)$row['name']);
        }
        $pages = [];
        foreach ($pageRows as $row) {
            if (!is_array($row)) continue;
            if (isset($row['profile']) && (string)$row['profile'] !== $profile) continue;
            $id = self::id($row['id'] ?? '');
            if ($id === '') continue;
            // Prefer the canonical Page ID when an alias mapping is present.
            $canonical = self::id($row['page_id'] ?? '') ?: ($aliases[$id] ?? $id);
            $pages[$canonical] = [
                'id'=>$canonical, 'name'=>$names[$canonical] ?? trim((string)($row['name'] ?? $canonical)),
                'business_id'=>self::id($row['business_id'] ?? ''),
                'source'=>'last_confirmed_private_inventory',
                'ad_account_page_access_verified'=>false,
            ];
        }
        $updated = (int)($snapshot['updated_at'] ?? 0);
        $cache = ['hit'=>true, 'fetched_at'=>$updated, 'stale'=>$updated <= 0 || $now-$updated > 1800,
                  'cached_only'=>true, 'source'=>'private_worker_snapshot'];
        return [
            'profile'=>$profile, 'catalog_only'=>true, 'session_ready'=>null,
            'ads_management_granted'=>null, 'ads_read_granted'=>null,
            'identity'=>['id'=>'', 'name'=>$profile], 'api_version'=>'private_worker_snapshot',
            'ad_accounts'=>['data'=>array_values($accounts)],
            'pages'=>['data'=>array_values($pages)],
            '_cache'=>$cache,
        ];
    }

    public static function readiness(
        array $catalog,
        string $accountId,
        array $proof = [],
        array $paymentProof = []
    ): array {
        $fundingAsset = self::asset($catalog, 'funding', $accountId);
        $pages = (array)($catalog['pages']['data'] ?? []);
        $target = self::id($fundingAsset['id']);
        $verified = [];
        if (($proof['profile_id'] ?? '') === $catalog['profile'] && ($proof['account_id'] ?? '') === $target
            && ($proof['checked_live'] ?? null) === true && ($proof['account_scope_verified'] ?? null) === true
            && ($proof['status'] ?? '') === 'VERIFIED' && (int)($proof['checked_at'] ?? 0) >= time()-120
            && (int)($proof['checked_at'] ?? 0) <= time()+5) {
            foreach ((array)($proof['data'] ?? []) as $row) {
                if (!is_array($row) || ($row['account_id'] ?? '') !== $target
                    || ($row['ad_account_page_access_verified'] ?? null) !== true
                    || ($row['source'] ?? '') !== 'scoped_private_promotable_pages') continue;
                $id = self::id($row['id'] ?? '');
                if ($id !== '') $verified[$id] = $row;
            }
        }
        foreach ($pages as &$page) {
            if (isset($verified[$page['id']])) $page = $verified[$page['id']];
        }
        unset($page);
        foreach ($verified as $id => $row) {
            if (!in_array($id, array_column($pages,'id'), true)) $pages[] = $row;
        }

        // Saved onboarding Confirm remains distinct from live RK permissions.
        if (($proof['profile_id'] ?? '') === $catalog['profile'] && ($proof['account_id'] ?? '') === $target) {
            foreach ((array)($proof['page_confirmations'] ?? []) as $confirmation) {
                if (!is_array($confirmation) || ($confirmation['main_business_confirmed'] ?? null) !== true
                    || ($confirmation['source'] ?? '') !== 'saved_worker_confirmation'
                    || self::id($confirmation['main_business_id'] ?? '') === '') continue;
                foreach ($pages as &$page) {
                    if ($page['id'] === self::id($confirmation['id'] ?? '')) {
                        $page['main_business_confirmed'] = true;
                        $page['main_business_id'] = $confirmation['main_business_id'];
                    }
                }
                unset($page);
            }
        }

        $pageStatus = $verified !== [] ? 'VERIFIED' : 'NOT_VERIFIED';
        $phone='UNKNOWN';
        if (($proof['profile_id'] ?? '') === $catalog['profile'] && ($proof['account_id'] ?? '') === $target
            && ($proof['checked_live'] ?? null) === true && ($proof['account_scope_verified'] ?? null) === true
            && (int)($proof['checked_at'] ?? 0) >= time()-120 && (int)($proof['checked_at'] ?? 0) <= time()+5
            && in_array($proof['advertiser_phone']['status'] ?? '', ['REQUIRED','VERIFIED'], true)) {
            $phone=$proof['advertiser_phone']['status'];
        }

        // Payment proof is accepted only from a fresh worker call made for this
        // exact profile/RK in this request. Never promote a cached vault state,
        // funding ID or success toast into live linkage.
        $paymentSources=[
            'private_facebook_billing_ui',
            'private_facebook_selected_rk_payment_tab',
        ];
        $paymentExact=(
            ($paymentProof['profile_id'] ?? '') === $catalog['profile']
            && ($paymentProof['account_id'] ?? '') === $target
            && ($paymentProof['checked_live'] ?? null) === true
            && ($paymentProof['account_scope_verified'] ?? null) === true
            && in_array($paymentProof['source'] ?? '', $paymentSources, true)
        );
        $paymentMethods=[];
        if ($paymentExact) {
            foreach ((array)($paymentProof['payment_methods'] ?? []) as $method) {
                if (!is_array($method)) continue;
                $type=trim((string)($method['type'] ?? ''));
                $last4=trim((string)($method['last4'] ?? ''));
                if ($type==='' || strlen($type)>40 || !preg_match('/^\d{4}$/D',$last4)) continue;
                $paymentMethods[]=[
                    'type'=>preg_replace('/[^A-Za-z ]/','',$type),
                    'last4'=>$last4,
                    'linkage_status'=>'OBSERVED',
                ];
                if (count($paymentMethods)>=10) break;
            }
        }
        $paymentVerification=$paymentExact
            ? (string)($paymentProof['verification_status'] ?? 'UNVERIFIED')
            : 'NOT_CHECKED';
        if (!in_array($paymentVerification,['LINKED','NONE','UNVERIFIED'],true)) {
            $paymentVerification=$paymentExact ? 'UNVERIFIED' : 'NOT_CHECKED';
        }
        if ($paymentVerification==='LINKED'
            && (($paymentProof['card_linked'] ?? null)!==true || $paymentMethods===[])) {
            $paymentVerification='UNVERIFIED';
        }
        if ($paymentVerification==='NONE'
            && (($paymentProof['card_linked'] ?? null)!==false || $paymentMethods!==[])) {
            $paymentVerification='UNVERIFIED';
        }
        $paymentDiagnostic=[];
        $rawPaymentDiagnostic=is_array($paymentProof['diagnostic'] ?? null)
            ? $paymentProof['diagnostic'] : [];
        foreach (['code','stage','path','masked_method_count','ui_preview_unavailable'] as $key) {
            $value=$rawPaymentDiagnostic[$key] ?? null;
            if (is_string($value) && strlen($value)<=160) $paymentDiagnostic[$key]=$value;
            elseif (is_int($value) && $value>=0 && $value<=100) $paymentDiagnostic[$key]=$value;
        }
        $payment=[
            'status'=>$paymentVerification,
            'verification_status'=>$paymentVerification,
            'card_linked'=>$paymentVerification==='LINKED' ? true
                : ($paymentVerification==='NONE' ? false : null),
            'payment_methods'=>$paymentMethods,
            'account_scope_verified'=>$paymentExact,
            'checked_live'=>$paymentExact,
            'source'=>$paymentExact ? (string)$paymentProof['source'] : '',
            // A visible linked card is not proof that a charge/funding source
            // assignment was financially verified.
            'funding_verified'=>false,
            'diagnostic'=>$paymentDiagnostic,
        ];

        $warnings=[];
        if ($verified===[]) $warnings[]='Доступ FP для рекламы в выбранном РК не подтверждён.';
        if ($paymentVerification!=='LINKED') $warnings[]='Привязка карты в выбранном РК не подтверждена live-проверкой Meta.';
        return [
            'account_id'=>$fundingAsset['id'], 'catalog_only'=>true, 'status'=>'NOT_VERIFIED',
            'pages'=>['status'=>$pageStatus, 'count'=>count($pages), 'data'=>$pages,
                      'ad_account_page_access_verified'=>$verified !== [],
                      'checked_live'=>($proof['checked_live'] ?? false) === true,
                      'diagnostic'=>$proof['diagnostic'] ?? []],
            'pixels'=>['status'=>'NOT_CHECKED'], 'media'=>['status'=>'NOT_CHECKED'],
            'advertiser_phone'=>['status'=>$phone],
            'funding'=>$payment,
            'warnings'=>$warnings,
            '_cache'=>$catalog['_cache'],
        ];
    }

    private static function json(string $path): array {
        $raw = @file_get_contents($path);
        $data = is_string($raw) ? json_decode($raw, true) : null;
        return is_array($data) ? $data : [];
    }

    public static function load(string $profile): array {
        return self::fromState($profile,
            self::json('/var/lib/remask/workspace-live-meta-snapshots.json'),
            self::json('/var/lib/remask/workspace-provisioning-bindings.json'), time(),
            self::json(__DIR__.'/RemaskConfirmedPageIdentities.json'));
    }

    public static function asset(array $catalog, string $resource, string $accountId = ''): array {
        if ($resource === 'pages') return $catalog['pages'] + ['_cache'=>$catalog['_cache'], 'catalog_only'=>true];
        if ($resource === 'ad_accounts') return $catalog['ad_accounts'] + ['_cache'=>$catalog['_cache'], 'catalog_only'=>true];
        if ($resource === 'funding') {
            $id = self::id($accountId);
            foreach ($catalog['ad_accounts']['data'] as $account) {
                if ($id === '' || $account['account_id'] !== $id) continue;
                // No live payment proof is present in the identity mirror.
                return ['id'=>'act_'.$id, 'account_status'=>$account['account_status'],
                        'currency'=>$account['currency'], 'funding_verified'=>false,
                        'verification_status'=>'NOT_CHECKED', '_cache'=>$catalog['_cache']];
            }
            throw new InvalidArgumentException('Selected RK is absent from this profile catalog.');
        }
        throw new DomainException('PRIVATE_LAUNCH_RESOURCE_UNAVAILABLE: '.$resource);
    }
}
CATALOG
);
file_put_contents($root.'/ajax/metaPreflight.php', <<<'PREFLIGHT'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/RemaskPrivateLaunchCatalog.php';

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    // Validate profile existence locally; never consult its old Graph token.
    MetaEndpoint::accountForName($profile);
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    MetaEndpoint::ok($catalog);
} catch (Throwable $e) { MetaEndpoint::fail($e); }
PREFLIGHT
);
file_put_contents($root.'/ajax/metaAssets.php', <<<'ASSETS'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/RemaskPrivateLaunchCatalog.php';

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    // Validate profile existence locally; never consult its old Graph token.
    MetaEndpoint::accountForName($profile);
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    $resource = strtolower(trim((string)($input['resource'] ?? '')));
    $accountId = trim((string)($input['account_id'] ?? ''));
    MetaEndpoint::ok(RemaskPrivateLaunchCatalog::asset($catalog, $resource, $accountId));
} catch (DomainException $e) {
    http_response_code(501);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok'=>false, 'error'=>['code'=>'PRIVATE_LAUNCH_RESOURCE_UNAVAILABLE',
        'message'=>'Этот ресурс ещё не подключён к текущему режиму Launch.']], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) { MetaEndpoint::fail($e); }
ASSETS
);
file_put_contents($root.'/ajax/metaAssetReadiness.php', <<<'READINESS'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/RemaskPrivateLaunchCatalog.php';
try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    MetaEndpoint::accountForName($profile);
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    $account = preg_replace('/^act_/', '', trim((string)($input['account_id'] ?? '')));
    RemaskPrivateLaunchCatalog::asset($catalog,'funding',$account);
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
    $base = rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081'),'/');
    $key = (string)(getenv('REMASK_WORKER_API_KEY') ?: '');
    if ($key === '') throw new RuntimeException('PAGE_ACCESS_WORKER_UNAVAILABLE');

    // Fetch historical Confirm before starting the expensive live browser probe.
    $savedCtx=stream_context_create(['http'=>['method'=>'GET',
        'header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
        'timeout'=>5,'ignore_errors'=>true,'follow_location'=>0]]);
    $savedRaw=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/provisioning-state',false,$savedCtx);
    $savedState=is_string($savedRaw) ? json_decode($savedRaw,true) : null;
    $confirmations=[];
    foreach ((array)($savedState['fan_pages'] ?? []) as $page) {
        if (!is_array($page) || ($page['main_business_confirmed'] ?? null) !== true
            || !preg_match('/^\d{5,30}$/',(string)($page['id'] ?? ''))
            || !preg_match('/^\d{5,30}$/',(string)($page['main_business_id'] ?? ''))) continue;
        $confirmations[]=['id'=>$page['id'],'main_business_id'=>$page['main_business_id'],
            'main_business_confirmed'=>true,'source'=>'saved_worker_confirmation'];
    }

    $ctx = stream_context_create(['http'=>['method'=>'GET',
        'header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
        'timeout'=>82,'ignore_errors'=>true,'follow_location'=>0]]);
    $raw = @file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/page-access?account_id='.rawurlencode($account),false,$ctx);
    $proof = is_string($raw) ? json_decode($raw,true) : null;
    if (!is_array($proof) || ($proof['profile_id'] ?? '') !== $profile || ($proof['account_id'] ?? '') !== $account) {
        $code = is_array($proof) ? (string)($proof['detail'] ?? '') : '';
        $known = ['CHECKPOINT_REQUIRED','SESSION_EXPIRED','TWO_FACTOR_REQUIRED','PROFILE_CONTEXT_ERROR','PAGE_ACCESS_INSPECTION_TIMEOUT'];
        $proof = ['diagnostic'=>['code'=>in_array($code,$known,true) ? $code : 'PAGE_ACCESS_RESULT_UNAVAILABLE']];
    }
    if (!isset($proof['profile_id'])) $proof['profile_id']=$profile;
    if (!isset($proof['account_id'])) $proof['account_id']=$account;
    $proof['page_confirmations']=array_merge($confirmations,(array)($proof['page_confirmations'] ?? []));

    // Payment inspection is expensive and uses the same single Chromium slot.
    // Run it only when this RK already has at least one live-proven Page.
    $pageReady=false;
    if (($proof['profile_id'] ?? '')===$profile && ($proof['account_id'] ?? '')===$account
        && ($proof['checked_live'] ?? null)===true && ($proof['account_scope_verified'] ?? null)===true
        && ($proof['status'] ?? '')==='VERIFIED') {
        foreach ((array)($proof['data'] ?? []) as $row) {
            if (is_array($row) && ($row['account_id'] ?? '')===$account
                && ($row['ad_account_page_access_verified'] ?? null)===true
                && ($row['source'] ?? '')==='scoped_private_promotable_pages') {
                $pageReady=true; break;
            }
        }
    }

    $paymentProof=['diagnostic'=>['code'=>$pageReady
        ? 'PAYMENT_RESULT_UNAVAILABLE'
        : 'PAYMENT_SKIPPED_PAGE_ACCESS_UNVERIFIED']];
    if ($pageReady) {
        $paymentCtx=stream_context_create(['http'=>['method'=>'GET',
            'header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
            'timeout'=>72,'ignore_errors'=>true,'follow_location'=>0]]);
        $paymentRaw=@file_get_contents(
            $base.'/api/v1/profiles/'.rawurlencode($profile).'/payment-methods?account_id='.rawurlencode($account),
            false,$paymentCtx
        );
        $decoded=is_string($paymentRaw) ? json_decode($paymentRaw,true) : null;
        if (is_array($decoded)
            && ($decoded['profile_id'] ?? '')===$profile
            && ($decoded['account_id'] ?? '')===$account) {
            $paymentProof=$decoded;
        } else {
            $code=is_array($decoded) ? (string)($decoded['detail'] ?? '') : '';
            $known=['CHECKPOINT_REQUIRED','SESSION_EXPIRED','TWO_FACTOR_REQUIRED',
                'PROFILE_CONTEXT_ERROR','PAYMENT_INSPECTION_TIMEOUT','PAYMENT_BROWSER_CRASHED',
                'PAYMENT_UI_UNAVAILABLE'];
            $paymentProof=['diagnostic'=>['code'=>in_array($code,$known,true)
                ? $code : 'PAYMENT_RESULT_UNAVAILABLE']];
        }
    }

    MetaEndpoint::ok(RemaskPrivateLaunchCatalog::readiness(
        $catalog,$account,$proof,$paymentProof
    ));
} catch (Throwable $e) { MetaEndpoint::fail($e); }
READINESS
);
$workspacePath=$root.'/scripts/workspace.js';
$workspace=file_get_contents($workspacePath);
$start=is_string($workspace) ? strpos($workspace,'async function checkAssetsSelection(){') : false;
$end=$start!==false ? strpos($workspace,'async function showFunding(){',$start) : false;
if ($start===false || $end===false) throw new RuntimeException('Assets readiness UI boundary missing');
$workspace=substr_replace($workspace, <<<'READINESS_UI'
async function checkAssetsSelection(){
  const rows=selectedRows('ad_accounts');
  if(!rows.length)return;
  openModal(`Assets — ${rows.length} РК`,`<div class="ws-muted mb-2">Проверка доступа FP и live-состояния оплаты в выбранном РК через FB-сессию профиля.</div><div id="assetReadinessProgress">Проверка Meta…</div><div id="assetReadinessRows"></div>`,'',null);
  const body=$('assetReadinessRows'), progress=$('assetReadinessProgress');
  await concurrent(rows,1,async r=>apiJson('ajax/metaAssetReadiness.php',post({profile:r.profile,account_id:r.id})),(done,total,res,idx)=>{
    const r=rows[idx], block=document.createElement('div');
    const pages=res?.pages?.data||[];
    const names=pages.map(p=>`${p.name||p.id} · ${p.id}`+
      (p.main_business_confirmed===true?` · Confirm основного BM ${p.main_business_id}: выполнен`:'')+
      ` — ${p.ad_account_page_access_verified===true?'доступ РК подтверждён':'доступ РК не подтверждён'}`).join(' · ');
    const verified=res?.pages?.ad_account_page_access_verified===true;
    const diagnostic=res?.pages?.diagnostic;
    const funding=res?.funding||{status:'NOT_CHECKED'};
    const methods=(funding.payment_methods||[]).map(m=>`${m.type} •••• ${m.last4}`).join(', ');
    const paymentText=funding.status==='LINKED'
      ? 'Карта присутствует у выбранного РК'+(methods?' — '+methods:'')+'. Платёжная/charge verification не подтверждена.'
      : funding.status==='NONE'
        ? 'Meta live подтверждает отсутствие payment methods у выбранного РК.'
        : funding.diagnostic?.code==='PAYMENT_SKIPPED_PAGE_ACCESS_UNVERIFIED'
          ? 'Оплата не проверялась: сначала нужен подтверждённый доступ FP в этом РК.'
          : funding.diagnostic?.code==='CHECKPOINT_REQUIRED'
            ? 'Оплата не проверена: Meta требует проверку Facebook-аккаунта.'
            : 'Платёжная привязка выбранного РК live не подтверждена.';
    const phone={REQUIRED:'Meta требует подтверждения номера телефона перед рекламой.',VERIFIED:'Номер телефона уже подтверждён в Meta.',UNKNOWN:'Требование номера телефона не установлено. Отсутствие сообщения не подтверждает возможность публикации.'}[res?.advertiser_phone?.status||'UNKNOWN'];
    const reason=diagnostic?.code==='PAGE_ACCESS_MEMORY_LIMIT'?'Проверка доступа РК остановлена: недостаточно памяти для формы Ads Manager.':
      diagnostic?.code==='PAGE_ACCESS_INSPECTION_TIMEOUT'?'Проверка доступа РК не завершилась за отведённое время.':
      diagnostic?.code==='CHECKPOINT_REQUIRED'?'Meta требует проверку Facebook-аккаунта.':
      diagnostic?.code==='PAGE_ACCESS_RESULT_UNAVAILABLE'?'Не удалось получить результат проверки доступа РК.':'';
    block.innerHTML=`<b>${esc(r.name||r.id)}</b><div class="sub">${esc(r.profile)} · ${esc(r.id)}</div>`+
      (res?.error ? `<div class="job-failed">${esc(res.error)}</div>` :
      `<div>FP: ${esc(names||'В сохранённом списке страниц нет.')}</div><div>${pill(verified?'ДОСТУП FP ПОДТВЕРЖДЁН':'ДОСТУП FP НЕ ПОДТВЕРЖДЁН',verified?'ok':'warn')}</div><div>${esc(phone)}</div><div>Оплата: ${esc(paymentText)}</div><div>Pixel и медиа: не проверены.</div>`+
      (reason?`<div class="sub">${esc(reason)}</div>`:diagnostic?.code?`<div class="sub">${esc(diagnostic.code)}</div>`:'')+
      (funding.diagnostic?.code?`<div class="sub">Payment: ${esc(funding.diagnostic.code)}</div>`:'')+
      (diagnostic?`<details><summary>Диагностика проверки</summary><pre>${esc(JSON.stringify(diagnostic,null,2))}</pre></details>`:''));
    body.appendChild(block); progress.textContent=`Загружено ${done}/${total}`; setProgress(done,total);
  });
  progress.textContent='Проверка FP и оплаты завершена.';
}

READINESS_UI
, $start, $end-$start);
$workspace=str_replace('const n=state.selected[state.activeTab].size;', 'const n=selectedRows(state.activeTab).length;', $workspace);
file_put_contents($workspacePath,$workspace);
// Review/submit still have only a legacy Graph implementation. Do not silently
// use that implementation when the user selected private-worker operation.
foreach (['metaLaunchReview.php','metaDryRun.php','metaJobCreate.php','metaLaunch.php'] as $endpoint) {
    file_put_contents($root.'/ajax/'.$endpoint, <<<'BLOCKED'
<?php
declare(strict_types=1);
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
http_response_code(409);
header('Content-Type: application/json; charset=utf-8');
echo json_encode(['ok'=>false,'error'=>[
    'code'=>'PRIVATE_LAUNCH_VERIFICATION_REQUIRED',
    'message'=>'Проверка рекламного доступа FP и оплаты в текущем режиме ещё не выполнена. Запуск рекламы недоступен.'
]], JSON_UNESCAPED_UNICODE);
BLOCKED
    );
}
$jsPath=$root.'/scripts/launch.js';
$js=file_get_contents($jsPath);
if (!is_string($js)) throw new RuntimeException('launch.js is missing');
if (strpos($js,'REMASK_PRIVATE_LAUNCH_CATALOG_V1')===false) {
    $js=str_replace("statusReady ? 'READY' :", "statusReady ? 'SELECTED / ACCESS NOT CHECKED' :", $js);
    $js=str_replace('Bindings loaded: ${ready}/${targets.length} RK READY.', 'Выбраны страницы: ${ready}/${targets.length}. Доступ FP для рекламы не проверен.', $js);
    // Render the confirmed Page catalog even when optional Pixel lookup fails.
    $js=str_replace("state.targetBindingAssets.pages[profile] = data.data || [];", "state.targetBindingAssets.pages[profile] = data.data || []; renderTargetBindings();", $js);
    $js.="\n".file_get_contents('/tmp/railway-launch-private-catalog.js');
}
file_put_contents($jsPath,$js);
$phpPath=$root.'/launch.php';
$php=file_get_contents($phpPath);
$php=preg_replace('#scripts/launch\\.js(?:\\?[^"\\\']*)?#','scripts/launch.js?v=20261002-private-catalog-v1',$php,1);
$php=str_replace('official API, read only','состояние оплаты, read only',$php);
$php=str_replace('Одна настройка → много FB-профилей и RK. Каждая цель использует свой token, proxy и account-specific assets.',
    'Сохранённые РК и FP доступны без новой синхронизации. Доступ FP для рекламы и привязку карты ещё нужно проверить.',$php);
$php=str_replace('PAUSED-BY-DEFAULT','ЗАПУСК НЕ ПРОВЕРЕН',$php);
$php=str_replace('OFFICIAL META API','КАТАЛОГ РК И FP',$php);
$php=str_replace('Checks token access, RK status, Page/Instagram, Pixel, Custom Audiences and funding context before Campaign creation.',
    'Запуск рекламы недоступен: проверка доступа страницы и оплаты в текущем режиме ещё не выполнена.',$php);
$php=str_replace('Shows the current funding source/status Meta exposes for each selected RK. ReMask does not change payment methods here.',
    'Платёжная привязка требует отдельной проверки Meta. Сохранённые ID не подтверждают готовность карты.',$php);
file_put_contents($phpPath,$php);
fwrite(STDERR,"[launch-private-catalog] local RK/Page reads installed; live advertising/payment permissions remain unverified\n");
