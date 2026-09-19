<?php
echo "--- REMASK MENU INSPECT BEGIN ---\n";
$path='/var/www/html/menu.php';
if(is_file($path)){
  $lines=preg_split('/\R/',file_get_contents($path) ?: '');
  foreach($lines as $i=>$line) echo ($i+1).": ".$line."\n";
}
echo "--- REMASK MENU INSPECT END ---\n";
