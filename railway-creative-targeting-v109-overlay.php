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
            $reqAnchor."\nrequire_once __DIR__ . '/../classes/AccountStoreFactory.php';\nrequire_once __DIR__ . '/../classes/FbAccount.php';",
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
"    if (\$profile === '') {\n".
"        \$store = AccountStoreFactory::create(ACCOUNTSFILENAME);\n".
"        foreach ((array)\$store->deserialize() as \$candidate) {\n".
"            if (!\$candidate instanceof FbAccount) continue;\n".
"            if (trim((string)\$candidate->name) === '') continue;\n".
"            \$profile = (string)\$candidate->name;\n".
"            break;\n".
"        }\n".
"        if (\$profile === '') throw new RuntimeException('No stored Meta profile is available for targeting search.');\n".
"    }";
    $src=preg_replace($profilePattern,$replacement,$src,1,$count) ?? $src;
    if($count!==1){fwrite(STDERR,"[creative-targeting-v109] profile patch count=$count\n");exit(374);}

    file_put_contents($endpoint,$src);
}
fwrite(STDERR,"[creative-targeting-v109] hidden Meta transport enabled for Creative targeting search\n");
