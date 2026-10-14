<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
require_once __DIR__.'/../classes/RemaskAdvertisingHelperClient.php';
try {
    $input=MetaEndpoint::input();
    $profile=trim((string)($input['profile']??''));
    $account=preg_replace('/^act_/', '',trim((string)($input['account_id']??'')));
    $catalog=RemaskPrivateLaunchCatalog::load($profile);
    $helper=match(basename(__FILE__)) {
        'metaPlacementCapabilities.php'=>'placements', 'metaCreativePreview.php'=>'preview', default=>'audience_estimate'
    };
    MetaEndpoint::ok(RemaskAdvertisingHelperClient::read($profile,$account,$catalog,$helper,
        RemaskAdvertisingHelperClient::input($input)));
} catch(Throwable $error){MetaEndpoint::fail($error);}
