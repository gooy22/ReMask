<?php
/**
 * v103: expose the writable official Meta SDK field schema to the Creative/Ads Builder UI.
 * Existing v102 MetaOfficialFields remains authoritative for actual Graph forwarding.
 */
$root='/var/www/html';
$schemaSource='/tmp/remask-v102-MetaSdkSchema.php';
$schemaTarget=$root.'/classes/MetaSdkSchema.php';

if(!is_file($schemaSource)){fwrite(STDERR,"[meta-schema-v103] missing schema source\n");exit(331);}
if(!copy($schemaSource,$schemaTarget)){fwrite(STDERR,"[meta-schema-v103] schema copy failed\n");exit(332);}

$endpoint=<<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaSdkSchema.php';

try {
    MetaEndpoint::ok([
        'source' => 'facebook/facebook-python-business-sdk',
        'captured_at' => '2026-09-19',
        'schema' => MetaSdkSchema::schema(),
    ]);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;

if(file_put_contents($root.'/ajax/metaSdkSchema.php',$endpoint)===false){
    fwrite(STDERR,"[meta-schema-v103] endpoint write failed\n");
    exit(333);
}

fwrite(STDERR,"[meta-schema-v103] writable SDK schema endpoint ready\n");
