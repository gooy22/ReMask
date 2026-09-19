<?php
echo "--- REMASK MEDIA LIBRARY INSPECT BEGIN ---\n";
$paths=[];
foreach(glob('/var/www/html/classes/*MediaLibrary*') ?: [] as $p) $paths[]=$p;
foreach(['/var/www/html/ajax/metaJobCreate.php','/var/www/html/scripts/launch.js','/var/www/html/launch.php'] as $p) if(is_file($p)) $paths[]=$p;
$paths=array_values(array_unique($paths));
sort($paths);
foreach($paths as $path){
  echo "=== FILE: $path ===\n";
  $s=file_get_contents($path) ?: '';
  $lines=preg_split('/\R/',$s);
  if(str_contains($path,'MediaLibrary') || str_ends_with($path,'metaJobCreate.php')){
    foreach($lines as $i=>$line) echo ($i+1).": ".$line."\n";
    continue;
  }
  foreach($lines as $i=>$line){
    if(preg_match('/mediaLibrary|media library|existingMedia|existing_image|existing_video|existing_creative|carousel|FormData|jobCreate|metaJobCreate|creativeFormat|\bid="media"/i',$line)){
      $from=max(0,$i-12);$to=min(count($lines)-1,$i+45);
      echo "--- lines ".($from+1)."-".($to+1)." ---\n";
      for($j=$from;$j<=$to;$j++) echo ($j+1).": ".$lines[$j]."\n";
    }
  }
}
echo "--- REMASK MEDIA LIBRARY INSPECT END ---\n";
