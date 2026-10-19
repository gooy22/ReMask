<?php
declare(strict_types=1);
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/AccountStoreFactory.php';
require_once __DIR__.'/../classes/FbAccount.php';
require_once __DIR__.'/../classes/RemaskCookieProfile.php';
require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
require_once __DIR__.'/../classes/RemaskTemplateTargetingContext.php';
require_once __DIR__.'/../classes/RemaskAdvertisingHelperClient.php';
try {
    $input=MetaEndpoint::input();$profiles=[];
    foreach((array)AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $row) {
        if(!$row instanceof FbAccount)continue;
        try {RemaskCookieProfile::validate((array)$row->cookies,$row->proxy,false);$ready=true;}
        catch(InvalidArgumentException $error){$ready=false;}
        $profiles[]=['name'=>(string)$row->name,'proxy_configured'=>$row->proxy!==null,'cookies_ready'=>$ready,
            'direct_ready'=>RemaskCookieProfile::directTestUntil((string)$row->name,(string)$row->userId)>time()];
    }
    $reference=new RemaskTemplateTargetingReference(dirname(ACCOUNTSFILENAME).'/template-targeting-'.hash('sha256',ACCOUNTSFILENAME).'.json');
    $until=(int)(getenv('REMASK_DIRECT_TEST_UNTIL')?:0);
    $preferred=$until>time() && $until<=time()+7200 ? trim((string)(getenv('REMASK_DIRECT_TEST_PROFILE')?:'')) : '';
    $excluded=array_values(array_filter(array_map('trim',explode(',',(string)(getenv('REMASK_TEMPLATE_LOOKUP_EXCLUDED_PROFILES')?:'')))));
    $candidates=RemaskTemplateTargetingContext::candidates($profiles,[RemaskPrivateLaunchCatalog::class,'load'],
        $preferred,array_unique(array_merge($excluded,$reference->blocked(time()))),true);
    $read=static function($profile,$account,$catalog,$helper,$request) use($reference) {
        try {return $account===''
            ? RemaskTemplateTargetingContext::readReference($profile,$helper,$request)
            : RemaskAdvertisingHelperClient::read($profile,$account,$catalog,$helper,$request);}
        catch(Throwable $error) {
            if(RemaskTemplateTargetingContext::sourceFailure($error))$reference->block($profile,time());
            throw $error;
        }
    };
    MetaEndpoint::ok(RemaskAdvertisingHelperClient::template($input,$candidates,$read));
} catch(Throwable $error) {
    http_response_code($error instanceof InvalidArgumentException?400:409);
    header('Content-Type: application/json; charset=utf-8');header('Cache-Control: no-store');
    $failure=RemaskTemplateTargetingContext::failure($error);
    $failure['message']='Meta не вернула рекламную оценку. Настройки можно изменить и сохранить; для чтения нужна действующая сессия с доступным транспортом.';
    echo json_encode(['ok'=>false,'error'=>$failure],JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
}
