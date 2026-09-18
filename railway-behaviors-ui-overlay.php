<?php
$jsPath='/var/www/html/scripts/launch.js';
$genericPath='/var/www/html/scripts/targeting-autocomplete.js';
$phpPath='/var/www/html/launch.php';
$js=file_get_contents($jsPath);
$generic=file_get_contents($genericPath);
$php=file_get_contents($phpPath);
if($js===false||$generic===false||$php===false){fwrite(STDERR,"[behaviors-ui] read failed\n");exit(221);}

if(strpos($js,'behaviors: []')===false){
    $old="    geo: [],\n    interests: [],\n";
    $new="    geo: [],\n    interests: [],\n    behaviors: [],\n";
    if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] state anchor missing\n");exit(222);}
    $js=str_replace($old,$new,$js,$n);
    if($n!==1){fwrite(STDERR,"[behaviors-ui] state count=$n\n");exit(223);}
}

$old="const targetingSearchControllers = {locations: null, interests: null};\nconst targetingSearchSeq = {locations: 0, interests: 0};";
$new="const targetingSearchControllers = {locations: null, interests: null, behaviors: null};\nconst targetingSearchSeq = {locations: 0, interests: 0, behaviors: 0};";
if(strpos($js,$old)!==false) $js=str_replace($old,$new,$js,$n);

$old=<<<'JS'
    const isGeo = type === 'locations';
    const input = $(isGeo ? 'geoQuery' : 'interestQuery');
    const resultsEl = $(isGeo ? 'geoResults' : 'interestResults');
    const query = String(input?.value || '').trim();
JS;
$new=<<<'JS'
    const isGeo = type === 'locations';
    const isBehavior = type === 'behaviors';
    const input = $(isGeo ? 'geoQuery' : (isBehavior ? 'behaviorQuery' : 'interestQuery'));
    const resultsEl = $(isGeo ? 'geoResults' : (isBehavior ? 'behaviorResults' : 'interestResults'));
    const query = String(input?.value || '').trim();
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] picker anchor missing\n");exit(224);}
$js=str_replace($old,$new,$js,$n);
if($n!==1){fwrite(STDERR,"[behaviors-ui] picker count=$n\n");exit(225);}

$old=<<<'JS'
    if (!state.profile) {
        if (resultsEl) resultsEl.textContent = 'Run preflight first.';
        return;
    }

    targetingSearchControllers[type]?.abort();
JS;
$new=<<<'JS'
    if (!state.profile) {
        if (resultsEl) resultsEl.textContent = 'Run preflight first.';
        return;
    }
    const behaviorAccountId = isBehavior
        ? (selectedAccountIds()[0] || state.accounts?.[0]?.id || '')
        : '';
    if (isBehavior && !behaviorAccountId) {
        if (resultsEl) resultsEl.textContent = 'Select at least one RK first.';
        return;
    }

    targetingSearchControllers[type]?.abort();
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] profile anchor missing\n");exit(226);}
$js=str_replace($old,$new,$js,$n);

$old="            ...formPost({profile: state.profile, type, q: query, limit: 25}),";
$new="            ...formPost({profile: state.profile, type, q: query, limit: 25, ...(behaviorAccountId ? {account_id: behaviorAccountId} : {})}),";
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] request anchor missing\n");exit(227);}
$js=str_replace($old,$new,$js,$n);

$old=<<<'JS'
                const target = isGeo ? state.geo : state.interests;
                const identity = isGeo ? `${item.type}:${item.key || item.country_code || item.name}` : item.id;
                const exists = target.some((x) => (isGeo ? `${x.type}:${x.key || x.country_code || x.name}` : x.id) === identity);
                if (!exists) target.push(item);
                renderPills($(isGeo ? 'selectedGeo' : 'selectedInterests'), target, isGeo ? 'geo' : 'interests');
JS;
$new=<<<'JS'
                const target = isGeo ? state.geo : (isBehavior ? state.behaviors : state.interests);
                const identity = isGeo ? (String(item.type) + ':' + String(item.key || item.country_code || item.name)) : item.id;
                const exists = target.some((x) => (isGeo ? (String(x.type) + ':' + String(x.key || x.country_code || x.name)) : x.id) === identity);
                if (!exists) target.push(item);
                const pillsId = isGeo ? 'selectedGeo' : (isBehavior ? 'selectedBehaviors' : 'selectedInterests');
                const stateKey = isGeo ? 'geo' : (isBehavior ? 'behaviors' : 'interests');
                renderPills($(pillsId), target, stateKey);
                invalidateLaunchReview();
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] select anchor missing\n");exit(228);}
$js=str_replace($old,$new,$js,$n);

if(strpos($js,'REMASK_BEHAVIOR_UI_V1')===false){
    $anchor="function targetingPayload() {";
    $ui=<<<'JS'
/* REMASK_BEHAVIOR_UI_V1 */
function ensureBehaviorTargetingUi() {
    if ($('behaviorQuery')) return;
    const interestInput = $('interestQuery');
    if (!interestInput) return;
    const interestBlock = interestInput.closest('.col-md-6, .col-md-5, .col-md-4, .col-lg-6, .col-lg-4, .form-group') || interestInput.parentElement;
    if (!interestBlock) return;

    const block = document.createElement('div');
    block.className = interestBlock.className || 'col-md-6';
    block.dataset.remaskBehaviorTargeting = '1';

    const label = document.createElement('label');
    label.htmlFor = 'behaviorQuery';
    label.textContent = 'Behaviors';

    const input = document.createElement('input');
    input.id = 'behaviorQuery';
    input.type = 'text';
    input.className = interestInput.className || 'form-control';
    input.placeholder = 'Type behavior — search starts after 2 characters';
    input.autocomplete = 'off';

    const selected = document.createElement('div');
    selected.id = 'selectedBehaviors';
    selected.className = $('selectedInterests')?.className || 'mt-2';

    const results = document.createElement('div');
    results.id = 'behaviorResults';
    results.className = $('interestResults')?.className || '';

    block.appendChild(label);
    block.appendChild(input);
    block.appendChild(selected);
    block.appendChild(results);
    interestBlock.insertAdjacentElement('afterend', block);
    renderPills(selected, state.behaviors, 'behaviors');
}

JS;
    if(strpos($js,$anchor)===false){fwrite(STDERR,"[behaviors-ui] payload anchor missing\n");exit(229);}
    $js=str_replace($anchor,$ui.$anchor,$js,$n);
}

$old=<<<'JS'
    if (state.interests.length) {
        targeting.flexible_spec = [{
            interests: state.interests.map(x => ({id: String(x.id), name: x.name})),
        }];
    }
JS;
$new=<<<'JS'
    const detailedTargeting = {};
    if (state.interests.length) {
        detailedTargeting.interests = state.interests.map(x => ({id: String(x.id), name: x.name}));
    }
    if (state.behaviors.length) {
        detailedTargeting.behaviors = state.behaviors.map(x => ({id: String(x.id), name: x.name}));
    }
    if (Object.keys(detailedTargeting).length) targeting.flexible_spec = [detailedTargeting];
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] flexible anchor missing\n");exit(230);}
$js=str_replace($old,$new,$js,$n);

$old="        geo: state.geo,\n        interests: state.interests,\n";
$new="        geo: state.geo,\n        interests: state.interests,\n        behaviors: state.behaviors,\n";
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] bundle save anchor missing\n");exit(231);}
$js=str_replace($old,$new,$js,$n);

$old=<<<'JS'
    state.geo = Array.isArray(data?.geo) ? data.geo : [];
    state.interests = Array.isArray(data?.interests) ? data.interests : [];
    renderPills($('selectedGeo'), state.geo, 'geo');
    renderPills($('selectedInterests'), state.interests, 'interests');
JS;
$new=<<<'JS'
    state.geo = Array.isArray(data?.geo) ? data.geo : [];
    state.interests = Array.isArray(data?.interests) ? data.interests : [];
    state.behaviors = Array.isArray(data?.behaviors) ? data.behaviors : [];
    renderPills($('selectedGeo'), state.geo, 'geo');
    renderPills($('selectedInterests'), state.interests, 'interests');
    ensureBehaviorTargetingUi();
    renderPills($('selectedBehaviors'), state.behaviors, 'behaviors');
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] bundle apply anchor missing\n");exit(232);}
$js=str_replace($old,$new,$js,$n);

$old=<<<'JS'
$('geoSearch')?.addEventListener('click', () => searchTargeting('locations'));
$('interestSearch')?.addEventListener('click', () => searchTargeting('interests'));
installLiveTargetingInput('geoQuery', 'locations');
installLiveTargetingInput('interestQuery', 'interests');
JS;
$new=<<<'JS'
$('geoSearch')?.addEventListener('click', () => searchTargeting('locations'));
$('interestSearch')?.addEventListener('click', () => searchTargeting('interests'));
ensureBehaviorTargetingUi();
installLiveTargetingInput('geoQuery', 'locations');
installLiveTargetingInput('interestQuery', 'interests');
installLiveTargetingInput('behaviorQuery', 'behaviors');
JS;
if(strpos($js,$old)===false){fwrite(STDERR,"[behaviors-ui] event anchor missing\n");exit(233);}
$js=str_replace($old,$new,$js,$n);

$old="    if (input?.id === 'geoQuery' || input?.id === 'interestQuery') return false;";
$new="    if (input?.id === 'geoQuery' || input?.id === 'interestQuery' || input?.id === 'behaviorQuery') return false;";
if(strpos($generic,$old)!==false) $generic=str_replace($old,$new,$generic,$n);

$php=preg_replace(
    '#<script src="scripts/launch\\.js(?:\\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260919-behaviors-v96" type="module"></script>',
    $php,1,$lc
) ?? $php;
if($lc!==1){fwrite(STDERR,"[behaviors-ui] launch script tag missing\n");exit(234);}

$php=preg_replace(
    "#<script\\s+src=[\"']scripts/targeting-autocomplete\\.js(?:\\?v=[^\"']*)?[\"']></script>#i",
    '<script src="scripts/targeting-autocomplete.js?v=20260919-behaviors-v96"></script>',
    $php,1,$tc
) ?? $php;
if($tc!==1){fwrite(STDERR,"[behaviors-ui] generic script tag missing\n");exit(235);}

file_put_contents($jsPath,$js);
file_put_contents($genericPath,$generic);
file_put_contents($phpPath,$php);
fwrite(STDERR,"[behaviors-ui] v96 ready\n");
