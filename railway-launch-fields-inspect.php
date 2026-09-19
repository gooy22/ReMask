<?php
$path='/var/www/html/launch.php';
$html=file_get_contents($path);
echo "--- REMASK LAUNCH FIELDS BEGIN ---\n";
if($html===false){echo "missing launch.php\n";exit(1);}
libxml_use_internal_errors(true);
$dom=new DOMDocument();
$dom->loadHTML($html);
$xp=new DOMXPath($dom);
$heads=$xp->query('//h1|//h2|//h3|//h4|//h5|//legend');
foreach($heads as $h){echo "HEADING: ".trim(preg_replace('/\s+/',' ',$h->textContent))."\n";}
$nodes=$xp->query('//input|//select|//textarea|//button');
foreach($nodes as $n){
  $id=$n->getAttribute('id');
  $name=$n->getAttribute('name');
  $type=$n->nodeName==='select'?'select':($n->nodeName==='textarea'?'textarea':($n->getAttribute('type')?:$n->nodeName));
  if($id===''&&$name==='') continue;
  $label='';
  if($id!==''){
    $ls=$xp->query('//label[@for="'.addslashes($id).'"]');
    if($ls && $ls->length) $label=trim(preg_replace('/\s+/',' ',$ls->item(0)->textContent));
  }
  if($label===''){
    $p=$n->parentNode;
    for($i=0;$i<3 && $p;$i++,$p=$p->parentNode){
      foreach($p->childNodes as $c){
        if($c->nodeType===XML_ELEMENT_NODE && strtolower($c->nodeName)==='label'){
          $label=trim(preg_replace('/\s+/',' ',$c->textContent)); break 2;
        }
      }
    }
  }
  $opts=[];
  if($n->nodeName==='select'){
    foreach($n->getElementsByTagName('option') as $o){
      $opts[]=trim($o->getAttribute('value').':'.preg_replace('/\s+/',' ',$o->textContent));
      if(count($opts)>=25){$opts[]='...';break;}
    }
  }
  echo "FIELD id=$id name=$name type=$type label=".json_encode($label,JSON_UNESCAPED_UNICODE);
  if($opts) echo " options=".json_encode($opts,JSON_UNESCAPED_UNICODE);
  echo "\n";
}
echo "--- REMASK LAUNCH FIELDS END ---\n";
