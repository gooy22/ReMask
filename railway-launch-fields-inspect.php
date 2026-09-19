<?php
echo "--- REMASK BACKEND META INTEGRATION BEGIN ---\n";
$targets=[
  '/var/www/html/classes/MetaAdsService.php'=>['createCampaign','createAdSet','createImageCreative','createVideoCreative','createAd','uploadImage','uploadVideo'],
  '/var/www/html/classes/MetaJobExecutor.php'=>['process','execute','createCampaign','createAdSet','createCreative','createAd'],
  '/var/www/html/classes/MetaLaunchValidator.php'=>['validateAndNormalize','normalize','targeting'],
  '/var/www/html/ajax/metaJobCreate.php'=>['payload','targets','media_library_id','carousel_media_library_ids'],
];
foreach($targets as $path=>$needles){
  if(!is_file($path)) continue;
  echo "=== FILE: $path ===\n";
  $s=file_get_contents($path) ?: '';
  $lines=preg_split('/\R/',$s);
  $printed=[];
  foreach($needles as $needle){
    foreach($lines as $i=>$line){
      if(stripos($line,$needle)!==false){
        $from=max(0,$i-20); $to=min(count($lines)-1,$i+160);
        $key=$from.':'.$to;
        if(isset($printed[$key])) break;
        $printed[$key]=true;
        echo "--- $needle lines ".($from+1)."-".($to+1)." ---\n";
        for($j=$from;$j<=$to;$j++) echo ($j+1).": ".$lines[$j]."\n";
        break;
      }
    }
  }
}
echo "--- REMASK BACKEND META INTEGRATION END ---\n";
