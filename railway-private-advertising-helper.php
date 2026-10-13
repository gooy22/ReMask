<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    $account = preg_replace('/^act_/', '', trim((string)($input['account_id'] ?? '')));
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    if ($profile === '' || !preg_match('/^\d{5,30}$/D', $account)
        || !in_array($account, array_column($catalog['ad_accounts']['data'] ?? [], 'account_id'), true)) {
        throw new InvalidArgumentException('SELECT_ADVERTISING_ACCOUNT');
    }
    $helper = match (basename(__FILE__)) {
        'metaPlacementCapabilities.php'=>'placements', 'metaCreativePreview.php'=>'preview', default=>'audience_estimate'
    };
    $request = ['account_id'=>$account];
    foreach (['targeting_spec','optimization_goal','promoted_object','bid_strategy','attribution_spec',
        'special_ad_categories','campaign_destination_type','creative','ad_format'] as $key) {
        if (!array_key_exists($key, $input)) continue;
        $value = $input[$key];
        if (in_array($key, ['targeting_spec','promoted_object','attribution_spec','special_ad_categories','creative'], true)
            && is_string($value)) $value = json_decode($value, true, 32, JSON_THROW_ON_ERROR);
        $request[$key] = $value;
    }
    $raw = json_encode($request, JSON_THROW_ON_ERROR);
    if (strlen($raw)>65536) throw new InvalidArgumentException('ADVERTISING_INPUT_TOO_LARGE');
    $base = rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081'), '/');
    $key = (string)(getenv('REMASK_WORKER_API_KEY') ?: '');
    if ($key==='') throw new RuntimeException('ADVERTISING_WORKER_UNAVAILABLE');
    if (session_status()===PHP_SESSION_ACTIVE) session_write_close();
    $context = stream_context_create(['http'=>['method'=>'POST','timeout'=>85,
        'ignore_errors'=>true,'follow_location'=>0,'content'=>$raw,
        'header'=>"Content-Type: application/json\r\nAccept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n"]]);
    $body = @file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/advertising-helper/'.$helper,
        false,$context,0,524289);
    $data = is_string($body) && strlen($body)<=524288 ? json_decode($body,true) : null;
    if (!is_array($data) || ($data['ok'] ?? null)!==true || ($data['profile'] ?? '')!==$profile
        || ($data['account_id'] ?? '')!==$account || ($data['checked_live'] ?? null)!==true
        || ($data['account_scope_verified'] ?? null)!==true || ($data['transport'] ?? '')!=='private_http_h2') {
        throw new RuntimeException(is_array($data['detail'] ?? null)
            ? (string)($data['detail']['code'] ?? 'ADVERTISING_HELPER_UNAVAILABLE') : 'ADVERTISING_HELPER_UNAVAILABLE');
    }
    MetaEndpoint::ok($data);
} catch (Throwable $error) { MetaEndpoint::fail($error); }
