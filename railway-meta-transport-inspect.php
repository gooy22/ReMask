<?php
$file = '/var/www/html/classes/MetaApiClient.php';
fwrite(STDERR, "[meta-transport-inspect] begin\n");
if (is_file($file)) {
    $lines = file($file, FILE_IGNORE_NEW_LINES);
    if (is_array($lines)) {
        foreach ($lines as $i => $line) {
            $n = $i + 1;
            if (($n >= 355 && $n <= 390) || stripos($line, 'Authorization: Bearer') !== false || stripos($line, 'graph.facebook.com') !== false) {
                fwrite(STDERR, sprintf("[meta-transport-inspect] %04d %s\n", $n, trim((string)$line)));
            }
        }
    }
}
fwrite(STDERR, "[meta-transport-inspect] end\n");
