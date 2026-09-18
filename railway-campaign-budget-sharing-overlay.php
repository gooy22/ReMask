<?php
$path='/var/www/html/classes/MetaAdsService.php';
$s=file_get_contents($path);
$old=<<<'PHP'
        foreach (['daily_budget', 'lifetime_budget', 'bid_strategy'] as $field) {
            if (array_key_exists($field, $campaign) && $campaign[$field] !== '' && $campaign[$field] !== null) {
                $params[$field] = $campaign[$field];
            }
        }
        return $this->client->post("{$accountId}/campaigns", $params);
PHP;
$new=<<<'PHP'
        $hasCampaignBudget = false;
        foreach (['daily_budget', 'lifetime_budget'] as $field) {
            if (array_key_exists($field, $campaign) && $campaign[$field] !== '' && $campaign[$field] !== null) {
                $params[$field] = $campaign[$field];
                $hasCampaignBudget = true;
            }
        }
        if (array_key_exists('bid_strategy', $campaign) && $campaign['bid_strategy'] !== '' && $campaign['bid_strategy'] !== null) {
            $params['bid_strategy'] = $campaign['bid_strategy'];
        }
        if (!$hasCampaignBudget) {
            $params['is_adset_budget_sharing_enabled'] = false;
        }
        return $this->client->post("{$accountId}/campaigns", $params);
PHP;
if(strpos($s,'is_adset_budget_sharing_enabled')===false){
  if(strpos($s,$old)===false){fwrite(STDERR,"campaign patch target missing\n");exit(1);}
  $s=str_replace($old,$new,$s,$n);
  if($n!==1){fwrite(STDERR,"campaign patch count=$n\n");exit(2);}
  file_put_contents($path,$s);
}
fwrite(STDERR,"[campaign-budget-sharing] ready\n");
