<?php
$roots=['/var/www/html'];
echo "--- REMASK CREATIVES INSPECT BEGIN ---\n";
$matches=[];
$it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator('/var/www/html',FilesystemIterator::SKIP_DOTS));
foreach($it as $fi){
    if(!$fi->isFile()) continue;
    $path=$fi->getPathname();
    if($fi->getSize()>350000) continue;
    $base=strtolower($fi->getFilename());
    $ext=strtolower($fi->getExtension());
    if(!in_array($ext,['php','js','html','css'],true)) continue;
    $s=@file_get_contents($path);
    if($s===false) continue;
    if(str_contains($base,'creat') || stripos($s,'creative')!==false || stripos($s,'креатив')!==false){
        $matches[$path]=$s;
    }
}
ksort($matches);
foreach($matches as $path=>$s){
    echo "=== FILE: $path ===\n";
    $lines=preg_split('/\R/',$s);
    $printed=0;
    foreach($lines as $i=>$line){
        if(stripos($line,'creative')!==false || stripos($line,'креатив')!==false || str_contains(strtolower($path),'creat')){
            $from=max(0,$i-10); $to=min(count($lines)-1,$i+35);
            echo "--- lines ".($from+1)."-".($to+1)." ---\n";
            for($j=$from;$j<=$to;$j++) echo ($j+1).": ".$lines[$j]."\n";
            $printed++;
            if($printed>=10) break;
        }
    }
}
echo "--- REMASK CREATIVES INSPECT END ---\n";
