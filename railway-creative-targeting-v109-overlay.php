<?php
/**
 * v109: Accountless targeting search for Creative Library.
 * If UI does not provide a profile, use the first stored ReMask profile
 * only as hidden transport for Meta targeting search.
 */
$root='/var/www/html';
$endpoint=$root.'/ajax/metaTargetingSearch.php';
if(!is_file($endpoint)){fwrite(STDERR,"[creative-targeting-v109] endpoint missing\n");exit(371);}
$src=file_get_contents($endpoint);
if($src===false){fwrite(STDERR,"[creative-targeting-v109] read failed\n");exit(372);}

if(strpos($src,'REMASK_ACCOUNTLESS_TARGETING_V1')===false){
    $reqAnchor="require_once __DIR__ . '/../checkpassword.php';";
    if(strpos($src,$reqAnchor)!==false && strpos($src,'AccountStoreFactory.php')===false){
        $src=str_replace(
            $reqAnchor,
            $reqAnchor."\nrequire_once __DIR__ . '/../classes/AccountStoreFactory.php';\nrequire_once __DIR__ . '/../classes/FbAccount.php';\nrequire_once __DIR__ . '/../classes/MetaEndpoint.php';",
            $src,
            $n
        );
    }

    $profilePattern='/\$profile\s*=\s*trim\(\(string\)\(\$input\[\'profile\'\]\s*\?\?\s*\'\'\)\);/';
    if(!preg_match($profilePattern,$src,$m,PREG_OFFSET_CAPTURE)){
        // Older endpoint may read request directly.
        $profilePattern='/\$profile\s*=\s*trim\(\(string\)\(\$_(?:POST|REQUEST)\[\'profile\'\]\s*\?\?\s*\'\'\)\);/';
        if(!preg_match($profilePattern,$src,$m,PREG_OFFSET_CAPTURE)){
            fwrite(STDERR,"[creative-targeting-v109] profile anchor missing\n");
            exit(373);
        }
    }
    $match=$m[0][0];
    $replacement=$match."\n".
"    /* REMASK_ACCOUNTLESS_TARGETING_V1 */\n".
"    /* REMASK_ACCOUNTLESS_TRANSPORT_POOL_V3 */\n".
"    if (\$profile === '') {\n".
"        \$store = AccountStoreFactory::create(ACCOUNTSFILENAME);\n".
"        \$stored = [];\n".
"        foreach ((array)\$store->deserialize() as \$candidate) {\n".
"            if (!\$candidate instanceof FbAccount) continue;\n".
"            \$candidateName = trim((string)\$candidate->name);\n".
"            if (\$candidateName === '' || trim((string)\$candidate->token) === '') continue;\n".
"            \$stored[\$candidateName] = \$candidate;\n".
"        }\n".
"        if (\$stored === []) throw new RuntimeException('No stored Meta profile is available for targeting search.');\n".
"\n".
"        \$dataRoot = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');\n".
"        \$transportCacheDir = \$dataRoot . '/meta-cache';\n".
"        if (!is_dir(\$transportCacheDir)) @mkdir(\$transportCacheDir, 0770, true);\n".
"        \$transportCacheFile = \$transportCacheDir . '/creative-targeting-transport.json';\n".
"        \$transportCache = is_file(\$transportCacheFile)\n".
"            ? json_decode((string)@file_get_contents(\$transportCacheFile), true)\n".
"            : [];\n".
"        if (!is_array(\$transportCache)) \$transportCache = [];\n".
"\n".
"        \$cachedName = trim((string)(\$transportCache['profile'] ?? ''));\n".
"        \$cachedAt = (int)(\$transportCache['verified_at'] ?? 0);\n".
"        if (\$cachedName !== '' && isset(\$stored[\$cachedName]) && (time() - \$cachedAt) < 900) {\n".
"            \$profile = \$cachedName;\n".
"        } else {\n".
"            // Targeting autocomplete must stay fast: never run a full Meta preflight here.\n".
"            // Prefer a saved profile without a proxy, otherwise use the first stored profile.\n".
"            foreach (\$stored as \$candidateName => \$candidate) {\n".
"                if (\$candidate->proxy === null) {\n".
"                    \$profile = \$candidateName;\n".
"                    break;\n".
"                }\n".
"            }\n".
"            if (\$profile === '') \$profile = (string)array_key_first(\$stored);\n".
"            @file_put_contents(\$transportCacheFile, json_encode([\n".
"                'profile' => \$profile,\n".
"                'verified_at' => time(),\n".
"                'source' => 'fast_targeting_transport',\n".
"            ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));\n".
"        }\n".
"        if (\$profile === '') throw new RuntimeException('No Meta profile transport is available for targeting search.');\n".
"    }";
    $src=preg_replace($profilePattern,$replacement,$src,1,$count) ?? $src;
    if($count!==1){fwrite(STDERR,"[creative-targeting-v109] profile patch count=$count\n");exit(374);}

    if(strpos($src,'REMASK_ACCOUNTLESS_BEHAVIOR_ACCOUNT_V1')===false){
        $serviceAnchor='$service = MetaEndpoint::serviceForAccountName($profile);';
        if(strpos($src,$serviceAnchor)===false){
            fwrite(STDERR,"[creative-targeting-v109] service anchor missing\n");
            exit(375);
        }
        $serviceReplacement=<<<'PHP_CODE'
$service = MetaEndpoint::serviceForAccountName($profile);

/* REMASK_ACCOUNTLESS_BEHAVIOR_ACCOUNT_V1 */
$remaskTargetingType = strtolower(trim((string)($input['type'] ?? '')));
if (in_array($remaskTargetingType, ['behavior','behaviors'], true)
    && trim((string)($input['account_id'] ?? '')) === '') {
    $accountRows = $service->listAdAccounts(1);
    $behaviorAccountId = '';
    foreach ((array)($accountRows['data'] ?? []) as $row) {
        if (!is_array($row)) continue;
        $candidate = preg_replace('/^act_/i', '', trim((string)($row['id'] ?? ''))) ?? '';
        if ($candidate !== '' && preg_match('/^\\d+$/', $candidate)) {
            $behaviorAccountId = $candidate;
            break;
        }
    }
    if ($behaviorAccountId === '') {
        throw new RuntimeException('No Meta ad account is available for Behaviors search.');
    }
    $input['account_id'] = $behaviorAccountId;
}
PHP_CODE;
        $src=str_replace($serviceAnchor,$serviceReplacement,$src,$serviceCount);
        if($serviceCount!==1){fwrite(STDERR,"[creative-targeting-v109] behavior account patch count=$serviceCount\n");exit(376);}
    }

    file_put_contents($endpoint,$src);
}
fwrite(STDERR,"[creative-targeting-v109] fast cached targeting transport + accountless Behaviors context enabled\n");
