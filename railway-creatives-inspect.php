<?php
echo "--- REMASK CREATIVES ROUTES BEGIN ---\n";
foreach(glob('/var/www/html/*') ?: [] as $path){
    if(is_file($path)) echo basename($path)."\n";
}
foreach(['/var/www/html/index.php','/var/www/html/workspace.php','/var/www/html/ad.php','/var/www/html/ads.php','/var/www/html/creative.php','/var/www/html/creatives.php'] as $path){
    if(!is_file($path)) continue;
    echo "=== FILE: $path ===\n";
    $s=file_get_contents($path);
    $lines=preg_split('/\R/',$s ?: '');
    foreach($lines as $i=>$line){
        if(preg_match('/href=|nav|tab|кре|creat|launch|workspace/i',$line)){
            $from=max(0,$i-4); $to=min(count($lines)-1,$i+12);
            echo "--- lines ".($from+1)."-".($to+1)." ---\n";
            for($j=$from;$j<=$to;$j++) echo ($j+1).": ".$lines[$j]."\n";
        }
    }
}
echo "--- REMASK CREATIVES ROUTES END ---\n";
