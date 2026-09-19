<?php
$path='/var/www/html/launch.php';
$html=file_get_contents($path) ?: '';
echo "--- REMASK LAUNCH FIELD IDS BEGIN ---\n";
if($html===''){echo "missing launch.php\n";exit;}
libxml_use_internal_errors(true);
$dom=new DOMDocument();
$dom->loadHTML($html);
$xp=new DOMXPath($dom);
foreach($xp->query('//input|//select|//textarea|//button') as $n){
  $id=$n->getAttribute('id');
  if($id==='') continue;
  $type=$n->nodeName==='select'?'select':($n->nodeName==='textarea'?'textarea':($n->getAttribute('type')?:$n->nodeName));
  echo $id."|".$type;
  if($n->nodeName==='select'){
    $opts=[];
    foreach($n->getElementsByTagName('option') as $o){$opts[]=$o->getAttribute('value');}
    echo "|".implode(',',$opts);
  }
  echo "\n";
}
echo "--- REMASK LAUNCH FIELD IDS END ---\n";
