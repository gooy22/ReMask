<?php
/**
 * v90: block obviously invalid low daily budgets before any Meta mutation.
 *
 * Launch currently sends Meta daily_budget in minor currency units. For the
 * common 2-decimal currencies used by the current Launch UI, values <= 100
 * are at or below 1.00 and are rejected by Meta for this traffic setup.
 * Keep the invariant client-side before Campaign creation so we never leave
 * an orphan PAUSED Campaign just to fail at Ad Set.
 */
$path='/var/www/html/scripts/launch.js';
$phpPath='/var/www/html/launch.php';
if(!is_file($path)||!is_file($phpPath)){fwrite(STDERR,"[budget-guard] launch runtime missing\n");exit(151);}
$s=file_get_contents($path);
$p=file_get_contents($phpPath);
if($s===false||$p===false){fwrite(STDERR,"[budget-guard] cannot read runtime\n");exit(152);}

if(strpos($s,'REMASK_DAILY_BUDGET_GUARD_V1')===false){
    $old=<<<'JS'
    const campaignBudget = Number(payload.campaign.daily_budget || 0);
    const adsetBudget = Number(payload.adset.daily_budget || 0);
    if ((campaignBudget > 0) === (adsetBudget > 0)) {
        throw new Error('Set a positive Daily budget on exactly one level: Campaign or Ad Set.');
    }
JS;
    $new=<<<'JS'
    /* REMASK_DAILY_BUDGET_GUARD_V1 */
    const campaignBudget = Number(payload.campaign.daily_budget || 0);
    const adsetBudget = Number(payload.adset.daily_budget || 0);
    if ((campaignBudget > 0) === (adsetBudget > 0)) {
        throw new Error('Set a positive Daily budget on exactly one level: Campaign or Ad Set.');
    }
    const activeDailyBudget = campaignBudget > 0 ? campaignBudget : adsetBudget;
    if (!Number.isInteger(activeDailyBudget)) {
        throw new Error('Daily budget must be an integer in minor currency units.');
    }
    if (activeDailyBudget <= 100) {
        throw new Error('Daily budget is too low. Enter more than 100 minor units (for USD: 101 = $1.01; recommended 200 = $2.00 or more).');
    }
JS;
    if(strpos($s,$old)===false){fwrite(STDERR,"[budget-guard] validation target missing\n");exit(153);}
    $s=str_replace($old,$new,$s,$n);
    if($n!==1){fwrite(STDERR,"[budget-guard] validation replacement count=$n\n");exit(154);}
    file_put_contents($path,$s);
}

$p=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-budget-guard-v90" type="module"></script>',
    $p,
    1,
    $count
) ?? $p;
if($count!==1){fwrite(STDERR,"[budget-guard] launch.js tag missing\n");exit(155);}
file_put_contents($phpPath,$p);
fwrite(STDERR,"[budget-guard] v90 daily budget guard ready\n");
