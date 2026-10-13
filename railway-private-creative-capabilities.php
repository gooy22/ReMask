<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/AccountStoreFactory.php';
require_once __DIR__.'/../classes/FbAccount.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
require_once __DIR__.'/../classes/RemaskAdvertisingAssetBridge.php';

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    $account = preg_replace('/^act_/', '', trim((string)($input['account_id'] ?? '')));
    $force = filter_var($input['refresh'] ?? false, FILTER_VALIDATE_BOOLEAN);
    $out = RemaskPrivateFormSchema::load();
    $out['profiles'] = [];
    foreach ((array)AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $row) {
        if (!$row instanceof FbAccount) continue;
        $out['profiles'][] = ['name'=>(string)$row->name, 'proxy_configured'=>$row->proxy !== null];
    }
    $out += ['profile'=>$profile,'account_id'=>$account,'ad_accounts'=>[], 'pages'=>[],
        'instagram_accounts'=>[],'pixels'=>[],'custom_audiences'=>[],'custom_conversions'=>[],
        'connected_instagram_accounts'=>[],'conversion_goals'=>[],'ad_images'=>[],
        'ad_videos'=>[],'ad_creatives'=>[],'warnings'=>[]];
    if ($profile !== '') {
        $catalog = RemaskPrivateLaunchCatalog::load($profile);
        $out['ad_accounts'] = $catalog['ad_accounts']['data'] ?? [];
        if ($account !== '') {
            foreach (['pages'=>'pages','pixels'=>'pixels','custom_audiences'=>'custom_audiences',
                'custom_conversions'=>'conversion_goals','instagram_accounts'=>'instagram_accounts',
                'ad_images'=>'images','ad_videos'=>'videos','ad_creatives'=>'creatives'] as $field=>$resource) {
                try {
                    $result = RemaskAdvertisingAssetBridge::read($input, $catalog, $resource, $force);
                    $out[$field] = $result['data'] ?? [];
                    $out['resource_proofs'][$field] = array_intersect_key($result,
                        array_flip(['checked_live','checked_at','account_scope_verified','_cache']));
                } catch (Throwable $error) {
                    $out['warnings'][] = ['resource'=>$field, 'error'=>['message'=>$error->getMessage()]];
                }
            }
            $out['connected_instagram_accounts'] = $out['instagram_accounts'];
        }
    }
    MetaEndpoint::ok($out);
} catch (Throwable $error) {
    MetaEndpoint::fail($error);
}
