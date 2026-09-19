<?php
echo "--- REMASK CREATE JOB INSPECT BEGIN ---\n";
$path='/var/www/html/scripts/launch.js';
$s=file_get_contents($path) ?: '';
$lines=preg_split('/\R/',$s);
$targets=['async function createJobAndRun','function clearExistingMediaSelection','function renderExistingMediaSelection','function buildPayload','function payload'];
foreach($targets as $needle){
  foreach($lines as $i=>$line){
    if(str_contains($line,$needle)){
      $from=max(0,$i-15);$to=min(count($lines)-1,$i+180);
      echo "=== $needle @ ".($i+1)." ===\n";
      for($j=$from;$j<=$to;$j++) echo ($j+1).": ".$lines[$j]."\n";
      break;
    }
  }
}
echo "--- REMASK CREATE JOB INSPECT END ---\n";
