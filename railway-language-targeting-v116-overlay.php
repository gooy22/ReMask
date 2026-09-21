<?php
/**
 * v116: First-class Audience Language targeting.
 * Adds Meta adlocale search, visual multi-select in Creative + Launch,
 * persistence, preset hydration and targeting.locales payload wiring.
 */
$root='/var/www/html';
$servicePath=$root.'/classes/MetaAdsService.php';
$endpointPath=$root.'/ajax/metaTargetingSearch.php';
$launchJsPath=$root.'/scripts/launch.js';
$launchPhpPath=$root.'/launch.php';
$genericPath=$root.'/scripts/targeting-autocomplete.js';
$creativeJsPath=$root.'/scripts/creatives.js';
$creativePhpPath=$root.'/creatives.php';

foreach([$servicePath,$endpointPath,$launchJsPath,$launchPhpPath,$genericPath,$creativeJsPath,$creativePhpPath] as $p){
    if(!is_file($p)){fwrite(STDERR,"[languages-v116] missing $p\n");exit(431);}
}

/* ---------- Backend: official Meta locale search ---------- */
$service=file_get_contents($servicePath);
$endpoint=file_get_contents($endpointPath);
if($service===false||$endpoint===false){fwrite(STDERR,"[languages-v116] backend read failed\n");exit(432);}

if(strpos($service,'REMASK_LANGUAGE_SEARCH_V1')===false){
    $anchor=<<<'PHP_CODE'
    public function searchInterests(string $query, int $limit = 25): array
PHP_CODE;
    $method=<<<'PHP_CODE'
    /* REMASK_LANGUAGE_SEARCH_V1 */
    public function searchLocales(string $query, int $limit = 25): array
    {
        /*
         * Meta's current Business SDK lists Audience Languages by calling
         * /search?type=adlocale&limit=1000, without q and without a locale
         * parameter. Querying adlocale like adinterest can fail for valid
         * Ads Manager tokens with "Error loading application".
         */
        $response = $this->client->get('search', [
            'type' => 'adlocale',
            'limit' => 1000,
        ]);

        $rows = is_array($response['data'] ?? null) ? $response['data'] : [];
        $query = trim($query);
        if ($query !== '') {
            $needle = strtolower($query);
            $aliases = [
                'рус' => 'russian', 'рос' => 'russian',
                'укра' => 'ukrainian', 'укр' => 'ukrainian',
                'англ' => 'english',
                'нем' => 'german',
                'фран' => 'french',
                'исп' => 'spanish',
                'поль' => 'polish',
                'рум' => 'romanian',
                'итал' => 'italian',
                'порту' => 'portuguese',
                'тур' => 'turkish',
                'араб' => 'arabic',
            ];
            foreach ($aliases as $prefix => $english) {
                if (str_starts_with($needle, $prefix)) {
                    $needle = $english;
                    break;
                }
            }
            $rows = array_values(array_filter($rows, static function ($row) use ($needle) {
                if (!is_array($row)) return false;
                $name = strtolower(trim((string)($row['name'] ?? '')));
                $key = trim((string)($row['key'] ?? $row['id'] ?? ''));
                return ($name !== '' && str_contains($name, $needle))
                    || ($key !== '' && $key === $needle);
            }));
        }

        $response['data'] = array_slice($rows, 0, min(max($limit, 1), 100));
        unset($response['paging']);
        return $response;
    }

PHP_CODE;
    if(strpos($service,$anchor)===false){fwrite(STDERR,"[languages-v116] service anchor missing\n");exit(433);}
    $service=str_replace($anchor,$method.$anchor,$service,$n);
    if($n!==1){fwrite(STDERR,"[languages-v116] service patch count=$n\n");exit(434);}
}

if(strpos($endpoint,'public function searchLocales(')===false){
    $marker='/* REMASK_TARGETING_RUNTIME_FAILOVER_V1 */';
    $markerPos=strpos($endpoint,$marker);
    if($markerPos===false){fwrite(STDERR,"[languages-v116] failover marker missing\n");exit(435);}
    $interestPos=strpos($endpoint,'        public function searchInterests(string $query, int $limit = 25): array',$markerPos);
    if($interestPos===false){fwrite(STDERR,"[languages-v116] wrapper interest method missing\n");exit(436);}
    $wrapperMethod=<<<'PHP_CODE'
        public function searchLocales(string $query, int $limit = 25): array
        {
            /*
             * Locale discovery is a distinct Meta /search capability.
             * Preserve the original Meta exception so code/subcode/fbtrace_id
             * are not flattened into a generic RuntimeException.
             */
            $lastError = null;
            foreach ($this->candidates as $profile) {
                try {
                    $real = MetaEndpoint::serviceForAccountName($profile);
                    $result = $real->searchLocales($query, $limit);
                    $this->cacheSuccess($profile);
                    return is_array($result) ? $result : [];
                } catch (Throwable $e) {
                    $lastError = $e;
                }
            }
            if ($lastError instanceof Throwable) throw $lastError;
            throw new RuntimeException('No Meta locale transport candidate is available.');
        }

PHP_CODE;
    $endpoint=substr($endpoint,0,$interestPos).$wrapperMethod.substr($endpoint,$interestPos);
}

if(strpos($endpoint,"'language', 'languages', 'locale', 'locales'")===false){
    $anchor=<<<'PHP_CODE'
        'behavior', 'behaviors' => $service->searchBehaviors(trim((string)($input['account_id'] ?? '')), $query, $limit),
PHP_CODE;
    $replacement=$anchor."        'language', 'languages', 'locale', 'locales' => \$service->searchLocales(\$query, \$limit),\n";
    if(strpos($endpoint,$anchor)===false){fwrite(STDERR,"[languages-v116] endpoint switch anchor missing\n");exit(437);}
    $endpoint=str_replace($anchor,$replacement,$endpoint,$n);
    if($n!==1){fwrite(STDERR,"[languages-v116] endpoint switch patch count=$n\n");exit(438);}
}

file_put_contents($servicePath,$service);
file_put_contents($endpointPath,$endpoint);

/* ---------- Launch: visual Audience Languages ---------- */
$js=file_get_contents($launchJsPath);
$php=file_get_contents($launchPhpPath);
$generic=file_get_contents($genericPath);
if($js===false||$php===false||$generic===false){fwrite(STDERR,"[languages-v116] launch read failed\n");exit(439);}

if(strpos($js,'REMASK_AUDIENCE_LANGUAGES_V1')===false){
    if(strpos($js,'    languages: [],')===false){
        $stateAnchor="    behaviors: [],\n";
        if(strpos($js,$stateAnchor)===false){fwrite(STDERR,"[languages-v116] state behaviors anchor missing\n");exit(440);}
        $js=str_replace($stateAnchor,$stateAnchor."    languages: [],\n",$js,$n);
        if($n!==1){fwrite(STDERR,"[languages-v116] state patch count=$n\n");exit(441);}
    }

    $helperAnchor='function targetingPayload() {';
    if(strpos($js,$helperAnchor)===false){fwrite(STDERR,"[languages-v116] targetingPayload anchor missing\n");exit(442);}
    $helpers=<<<'JS_CODE'
/* REMASK_AUDIENCE_LANGUAGES_V1 */
let remaskLanguageTimer = 0;
let remaskLanguageController = null;

function remaskLanguageId(item) {
    const value = Number(item?.key ?? item?.id);
    return Number.isInteger(value) && value > 0 ? value : 0;
}

function ensureLanguageTargetingUi() {
    if ($('languageQuery')) return;
    const anchorInput = $('behaviorQuery') || $('interestQuery');
    if (!anchorInput) return;
    const anchorBlock = anchorInput.closest('.col-md-6, .col-md-5, .col-md-4, .col-lg-6, .col-lg-4, .form-group') || anchorInput.parentElement;
    if (!anchorBlock) return;

    const block = document.createElement('div');
    block.className = anchorBlock.className || 'col-md-6';
    block.dataset.remaskLanguageTargeting = '1';

    const label = document.createElement('label');
    label.htmlFor = 'languageQuery';
    label.textContent = 'Языки аудитории';

    const input = document.createElement('input');
    input.id = 'languageQuery';
    input.type = 'text';
    input.className = anchorInput.className || 'form-control';
    input.placeholder = 'Начни вводить язык — от 2 символов';
    input.autocomplete = 'off';

    const selected = document.createElement('div');
    selected.id = 'selectedLanguages';
    selected.className = $('selectedInterests')?.className || 'mt-2';

    const results = document.createElement('div');
    results.id = 'languageResults';
    results.className = $('interestResults')?.className || '';

    const hint = document.createElement('div');
    hint.className = 'muted mt-1';
    hint.textContent = 'Meta Audience Languages · можно выбрать несколько';

    block.append(label,input,selected,results,hint);
    anchorBlock.insertAdjacentElement('afterend',block);
    renderPills(selected,state.languages,'languages');
}

function renderLanguageResults(items) {
    const results=$('languageResults');
    if(!results) return;
    results.innerHTML='';
    for(const raw of items||[]){
        const id=remaskLanguageId(raw);
        if(!id) continue;
        const item={id:String(id),key:id,name:String(raw?.name||raw?.label||('Язык #'+id))};
        const row=document.createElement('div');
        row.className='search-item';
        row.textContent=item.name+' — '+id;
        row.addEventListener('click',()=>{
            if(!state.languages.some(x=>Number(x.id??x.key)===id)) state.languages.push(item);
            renderPills($('selectedLanguages'),state.languages,'languages');
            $('languageQuery').value='';
            results.innerHTML='';
            invalidateLaunchReview();
            validateReady();
        });
        results.appendChild(row);
    }
    if(!results.children.length) results.textContent='Ничего не найдено.';
}

async function searchAudienceLanguages() {
    const input=$('languageQuery');
    const results=$('languageResults');
    const q=String(input?.value||'').trim();
    if(q.length<2){
        remaskLanguageController?.abort();
        remaskLanguageController=null;
        if(results) results.textContent='';
        return;
    }
    if(!state.profile){
        if(results) results.textContent='Сначала выбери FB-профиль / RK.';
        return;
    }
    remaskLanguageController?.abort();
    const controller=new AbortController();
    remaskLanguageController=controller;
    if(results) results.textContent='Ищу языки в Meta…';
    try{
        const data=await apiJson('ajax/metaTargetingSearch.php',{
            ...formPost({profile:state.profile,type:'languages',q,limit:50}),
            signal:controller.signal
        });
        if(remaskLanguageController!==controller) return;
        renderLanguageResults(data?.data??[]);
    }catch(e){
        if(e?.name==='AbortError') return;
        if(remaskLanguageController!==controller) return;
        if(results) results.textContent='Meta: '+(e?.payload?.message||e?.message||String(e));
    }finally{
        if(remaskLanguageController===controller) remaskLanguageController=null;
    }
}

function installLanguageTargetingInput() {
    ensureLanguageTargetingUi();
    const input=$('languageQuery');
    if(!input||input.dataset.remaskLanguageBound==='1') return;
    input.dataset.remaskLanguageBound='1';
    input.addEventListener('input',()=>{
        clearTimeout(remaskLanguageTimer);
        remaskLanguageTimer=setTimeout(searchAudienceLanguages,240);
    });
    input.addEventListener('keydown',e=>{
        if(e.key==='Enter'){e.preventDefault();clearTimeout(remaskLanguageTimer);searchAudienceLanguages();}
    });
}

function setLaunchLanguagesFromLocaleIds(values) {
    const ids=Array.isArray(values)?values:[];
    state.languages=ids.map(value=>{
        const id=Number(value);
        return Number.isInteger(id)&&id>0?{id:String(id),key:id,name:'Язык #'+id}:null;
    }).filter(Boolean);
    ensureLanguageTargetingUi();
    renderPills($('selectedLanguages'),state.languages,'languages');
}

JS_CODE;
    $js=str_replace($helperAnchor,$helpers.$helperAnchor,$js,$n);

    $payloadAnchor='    if (Object.keys(detailedTargeting).length) targeting.flexible_spec = [detailedTargeting];';
    if(strpos($js,$payloadAnchor)===false){fwrite(STDERR,"[languages-v116] detailed targeting anchor missing\n");exit(443);}
    $payloadReplacement=$payloadAnchor."\n    const remaskLocaleIds = [...new Set((state.languages || []).map(remaskLanguageId).filter(Boolean))];\n    if (remaskLocaleIds.length) targeting.locales = remaskLocaleIds;\n    else delete targeting.locales;";
    $js=str_replace($payloadAnchor,$payloadReplacement,$js,$n);
    if($n!==1){fwrite(STDERR,"[languages-v116] locales payload count=$n\n");exit(444);}

    $bundleAnchor="        behaviors: state.behaviors,\n";
    if(strpos($js,$bundleAnchor)!==false){
        $js=str_replace($bundleAnchor,$bundleAnchor."        languages: state.languages,\n",$js,$n);
    }

    $applyAnchor="    state.behaviors = Array.isArray(data?.behaviors) ? data.behaviors : [];\n";
    if(strpos($js,$applyAnchor)!==false){
        $js=str_replace($applyAnchor,$applyAnchor."    state.languages = Array.isArray(data?.languages) ? data.languages : [];\n",$js,$n);
    }

    $renderBeh="    renderPills($('selectedBehaviors'), state.behaviors, 'behaviors');";
    if(strpos($js,$renderBeh)!==false){
        $js=str_replace($renderBeh,$renderBeh."\n    ensureLanguageTargetingUi();\n    renderPills($('selectedLanguages'), state.languages, 'languages');",$js,$n);
    }

    // When applying a Creative preset, hydrate Meta targeting.locales into the visible picker.
    $presetAnchor="    state.behaviors = Array.isArray(targeting.behaviors) ? targeting.behaviors : flexBehaviors;\n";
    if(strpos($js,$presetAnchor)!==false){
        $js=str_replace($presetAnchor,$presetAnchor."    setLaunchLanguagesFromLocaleIds(targeting.locales || []);\n",$js,$n);
    }

    // Main Launch UI must win over the saved preset, including clearing all languages.
    $effectiveAnchor='    return remaskApplyLaunchMetaFields(out);';
    if(strpos($js,$effectiveAnchor)===false){fwrite(STDERR,"[languages-v116] effective payload return missing\n");exit(445);}
    $effectiveReplacement=<<<'JS_CODE'
    const remaskLocales = [...new Set((state.languages || []).map(remaskLanguageId).filter(Boolean))];
    out.adset = out.adset || {};
    out.adset.targeting = out.adset.targeting || {};
    if (remaskLocales.length) out.adset.targeting.locales = remaskLocales;
    else delete out.adset.targeting.locales;
    return remaskApplyLaunchMetaFields(out);
JS_CODE;
    $js=str_replace($effectiveAnchor,$effectiveReplacement,$js,$n);
    if($n!==1){fwrite(STDERR,"[languages-v116] effective locales count=$n\n");exit(446);}

    $js.="\ninstallLanguageTargetingInput();\n";
}

if(strpos($generic,"input?.id === 'languageQuery'")===false){
    $old="input?.id === 'geoQuery' || input?.id === 'interestQuery' || input?.id === 'behaviorQuery'";
    $new=$old." || input?.id === 'languageQuery'";
    if(strpos($generic,$old)!==false) $generic=str_replace($old,$new,$generic,$n);
}

$php=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260921-languages-v117" type="module"></script>',
    $php,1,$lc
) ?? $php;
if($lc!==1){fwrite(STDERR,"[languages-v116] launch cache bust failed\n");exit(447);}

file_put_contents($launchJsPath,$js);
file_put_contents($launchPhpPath,$php);
file_put_contents($genericPath,$generic);

/* ---------- Creative: ensure cache-bust for new language picker ---------- */
$creativePhp=file_get_contents($creativePhpPath);
if($creativePhp===false){fwrite(STDERR,"[languages-v116] creative php read failed\n");exit(448);}
$creativePhp=preg_replace(
    '#<script src="scripts/creatives\.js(?:\?[^"]*)?"></script>#',
    '<script src="scripts/creatives.js?v=20260921-languages-v117"></script>',
    $creativePhp,1,$cc
) ?? $creativePhp;
if($cc!==1){
    $creativePhp=preg_replace(
        '#<script src="scripts/creatives\.js(?:\?[^"]*)?" type="module"></script>#',
        '<script src="scripts/creatives.js?v=20260921-languages-v117" type="module"></script>',
        $creativePhp,1,$cc2
    ) ?? $creativePhp;
    if($cc2!==1){fwrite(STDERR,"[languages-v116] creative cache bust failed\n");exit(449);}
}
file_put_contents($creativePhpPath,$creativePhp);

fwrite(STDERR,"[languages-v117] Meta audience language targeting uses official adlocale list flow\n");
