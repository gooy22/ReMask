<?php
$files = [
    '/var/www/html/classes/MetaApiClient.php',
    '/var/www/html/classes/FbRequests.php',
    '/var/www/html/classes/FbRequest.php',
    '/var/www/html/ajax/checkAccount.php',
    '/var/www/html/ajax/addAccount.php',
];
$patterns = ['ApiGet','graph.facebook.com','access_token','Authorization: Bearer','CURLOPT_HTTPHEADER','CURLOPT_URL','CURLOPT_COOKIE','AddToCurlOptions','META_GRAPH_API_VERSION','curl_init','http_build_query'];
fwrite(STDERR, "[meta-transport-inspect] begin\n");
foreach ($files as $file) {
    if (!is_file($file)) continue;
    fwrite(STDERR, "[meta-transport-inspect] FILE {$file}\n");
    $lines = file($file, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) continue;
    foreach ($lines as $i => $line) {
        foreach ($patterns as $pattern) {
            if (stripos($line, $pattern) !== false) {
                $safe = preg_replace('/(Bearer\\s*[\'\"]?)[^\'\"\\s]+/i', '$1<TOKEN>', $line);
                fwrite(STDERR, sprintf("[meta-transport-inspect] %04d %s\n", $i + 1, trim((string)$safe)));
                break;
            }
        }
    }
}
fwrite(STDERR, "[meta-transport-inspect] end\n");
