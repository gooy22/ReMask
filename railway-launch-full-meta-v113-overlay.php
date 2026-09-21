<?php
/**
 * v113: Expose every writable field from ReMask's official Meta SDK schema in Launch.
 * Main Launch controls remain the primary UX; fields not already represented there
 * are generated dynamically from MetaSdkSchema and merged into the exact Review/Job payload.
 */
$root='/var/www/html';
$phpPath=$root.'/launch.php';
$jsPath=$root.'/scripts/launch.js';

if(!is_file($phpPath)||!is_file($jsPath)){
    fwrite(STDERR,"[launch-full-meta-v113] runtime files missing\n");
    exit(401);
}
$php=file_get_contents($phpPath);
$js=file_get_contents($jsPath);
if($php===false||$js===false){
    fwrite(STDERR,"[launch-full-meta-v113] read failed\n");
    exit(402);
}

if(strpos($php,'REMASK_FULL_META_LAUNCH_V1')===false){
    $panel=<<<'HTML'
<!-- REMASK_FULL_META_LAUNCH_V1 -->
<style>
#launchMetaAllFields{margin-top:16px}
#launchMetaAllFields .rm-meta-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
#launchMetaAllFields .rm-meta-search{min-width:260px;max-width:420px;flex:1}
#launchMetaAllFields .rm-meta-groups{display:grid;gap:10px;margin-top:12px}
#launchMetaAllFields details{border:1px solid rgba(148,163,184,.22);border-radius:10px;padding:0;background:rgba(15,23,42,.18)}
#launchMetaAllFields summary{cursor:pointer;padding:11px 13px;display:flex;justify-content:space-between;gap:10px;font-weight:700}
#launchMetaAllFields .rm-meta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;padding:0 12px 12px}
#launchMetaAllFields .rm-meta-field{min-width:0}
#launchMetaAllFields .rm-meta-field label{display:flex;justify-content:space-between;gap:8px;margin-bottom:5px;font-size:12px}
#launchMetaAllFields .rm-meta-type{opacity:.58;font-weight:400}
#launchMetaAllFields textarea{min-height:84px;resize:vertical}
#launchMetaAllFields .rm-meta-hidden{display:none!important}
#launchMetaAllFields .rm-meta-note{opacity:.72;font-size:12px;margin-top:6px}
</style>
<section class="card" id="launchMetaAllFields">
  <div class="rm-meta-head">
    <div>
      <h3 style="margin:0">Meta Ads — все API-настройки</h3>
      <div class="rm-meta-note">Поля, которые уже есть выше в Launch, здесь не дублируются. Остальные writable-параметры Campaign / Ad Set / Targeting / Creative / Ad берутся из официальной SDK-схемы и реально входят в Review и Job.</div>
    </div>
    <input id="launchMetaFieldSearch" class="form-control rm-meta-search" type="search" placeholder="Найти поле Meta: custom_audiences, brand_safety, attribution…">
  </div>
  <div id="launchMetaSchemaStatus" class="muted" style="margin-top:8px">Загрузка Meta SDK schema…</div>
  <div id="launchMetaSdkFields" class="rm-meta-groups"></div>
</section>
HTML;
    $pattern='#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#';
    if(!preg_match($pattern,$php)){
        fwrite(STDERR,"[launch-full-meta-v113] launch script tag missing\n");
        exit(403);
    }
    $php=preg_replace($pattern,$panel."\n".'$0',$php,1,$n) ?? $php;
    if($n!==1){
        fwrite(STDERR,"[launch-full-meta-v113] panel insert count=$n\n");
        exit(404);
    }
}

if(strpos($js,'REMASK_FULL_META_LAUNCH_V1')===false){
    $addon=<<<'JS'

/* REMASK_FULL_META_LAUNCH_V1 */
let remaskLaunchMetaSchema = null;
let remaskLaunchMetaPendingBuilder = null;

const remaskLaunchCoveredFields = {
    campaign:new Set([
        'name','objective','buying_type','special_ad_categories','bid_strategy',
        'daily_budget','lifetime_budget','spend_cap','start_time','stop_time','status'
    ]),
    adset:new Set([
        'name','optimization_goal','billing_event','bid_strategy','bid_amount','destination_type',
        'daily_budget','lifetime_budget','start_time','end_time','attribution_spec','promoted_object',
        'is_dynamic_creative','is_incremental_attribution_enabled','status'
    ]),
    targeting:new Set([
        'age_min','age_max','genders','locales','geo_locations','excluded_geo_locations',
        'interests','behaviors','flexible_spec','exclusions','publisher_platforms',
        'facebook_positions','instagram_positions','messenger_positions','audience_network_positions',
        'threads_positions','whatsapp_positions','device_platforms','user_os','user_device'
    ]),
    creative:new Set([
        'name','degrees_of_freedom_spec','asset_feed_spec','platform_customizations',
        'url_tags','image_hash','video_id','source_instagram_media_id'
    ]),
    ad:new Set(['name','status','conversion_domain','priority','tracking_specs'])
};

function remaskLaunchMetaFieldId(group, field) {
    return 'launchMeta_' + group + '_' + field.replace(/[^a-z0-9_]+/gi, '_');
}
function remaskLaunchMetaComplex(type) {
    return /^(list<|map|Object|IDName|Ad|Ads)/.test(String(type || ''));
}
function remaskLaunchMetaParse(raw, type, field) {
    raw=String(raw ?? '').trim();
    if(raw==='') return undefined;
    if(type==='bool'){
        if(raw==='true') return true;
        if(raw==='false') return false;
        return undefined;
    }
    if(type==='unsigned int'||type==='int'){
        const n=Number(raw);
        if(!Number.isInteger(n)) throw new Error(field + ': нужно целое число');
        return n;
    }
    if(type==='float'){
        const n=Number(raw);
        if(!Number.isFinite(n)) throw new Error(field + ': нужно число');
        return n;
    }
    if(remaskLaunchMetaComplex(type)){
        try { return JSON.parse(raw); }
        catch {
            if(type==='list<string>'||type==='list<enum>') return raw.split(',').map(v=>v.trim()).filter(Boolean);
            if(type==='list<unsigned int>') return raw.split(',').map(v=>Number(v.trim())).filter(Number.isInteger);
            throw new Error(field + ': нужен JSON');
        }
    }
    return raw;
}
function remaskLaunchMetaDisplay(value) {
    if(value===undefined||value===null) return '';
    if(typeof value==='object') return JSON.stringify(value);
    return String(value);
}
function remaskRenderLaunchMetaFields() {
    const root=$('launchMetaSdkFields');
    if(!root||!remaskLaunchMetaSchema) return;
    root.innerHTML='';
    for(const group of ['campaign','adset','targeting','creative','ad']){
        const spec=remaskLaunchMetaSchema[group]||{};
        const fields=spec.fields||{};
        const enums=spec.enums||{};
        const names=Object.keys(fields).filter(field=>!remaskLaunchCoveredFields[group]?.has(field));

        const details=document.createElement('details');
        details.dataset.launchMetaGroup=group;
        const summary=document.createElement('summary');
        summary.innerHTML='<span>'+group.toUpperCase()+'</span><span class="muted">'+names.length+' дополнительных полей</span>';
        details.appendChild(summary);

        const grid=document.createElement('div');
        grid.className='rm-meta-grid';
        for(const field of names){
            const type=String(fields[field]||'string');
            const wrap=document.createElement('div');
            wrap.className='rm-meta-field';
            wrap.dataset.search=(group+' '+field+' '+type).toLowerCase();

            const label=document.createElement('label');
            label.htmlFor=remaskLaunchMetaFieldId(group,field);
            label.innerHTML='<span>'+field+'</span><span class="rm-meta-type">'+type+'</span>';
            wrap.appendChild(label);

            let input;
            const enumValues=Array.isArray(enums[field])?enums[field]:[];
            if(type==='bool'){
                input=document.createElement('select');
                input.className='form-control';
                input.innerHTML='<option value="">Meta default</option><option value="true">true</option><option value="false">false</option>';
            } else if(type==='enum'&&enumValues.length){
                input=document.createElement('select');
                input.className='form-control';
                input.appendChild(new Option('Meta default',''));
                for(const value of enumValues) input.appendChild(new Option(value,value));
            } else if(remaskLaunchMetaComplex(type)){
                input=document.createElement('textarea');
                input.className='form-control';
                input.placeholder=type.startsWith('list<')?'[]':'{}';
            } else {
                input=document.createElement('input');
                input.className='form-control';
                if(type==='unsigned int'||type==='int'||type==='float') input.type='number';
                else input.type='text';
                if(type==='datetime') input.placeholder='ISO 8601 / Meta datetime';
            }
            input.id=remaskLaunchMetaFieldId(group,field);
            input.dataset.launchMetaGroup=group;
            input.dataset.launchMetaField=field;
            input.dataset.launchMetaType=type;
            input.addEventListener('change',()=>{
                invalidateLaunchReview();
                validateReady();
            });
            wrap.appendChild(input);
            grid.appendChild(wrap);
        }
        details.appendChild(grid);
        root.appendChild(details);
    }
    const status=$('launchMetaSchemaStatus');
    if(status) status.textContent='Официальная SDK-схема загружена. Поля ниже участвуют в Launch Review и создании Job.';
    if(remaskLaunchMetaPendingBuilder){
        const pending=remaskLaunchMetaPendingBuilder;
        remaskLaunchMetaPendingBuilder=null;
        remaskPopulateLaunchMetaSdkFields(pending);
    }
}
function remaskReadLaunchMetaSdkFields() {
    const out={campaign:{},adset:{},targeting:{},creative:{},ad:{}};
    document.querySelectorAll('[data-launch-meta-group][data-launch-meta-field]').forEach(el=>{
        const group=el.dataset.launchMetaGroup;
        const field=el.dataset.launchMetaField;
        const type=el.dataset.launchMetaType||'string';
        const value=remaskLaunchMetaParse(el.value,type,field);
        if(value!==undefined) out[group][field]=value;
    });
    return out;
}
function remaskPopulateLaunchMetaSdkFields(builder) {
    if(!remaskLaunchMetaSchema||!$('launchMetaSdkFields')?.children.length){
        remaskLaunchMetaPendingBuilder=builder||{};
        return;
    }
    builder=builder||{};
    document.querySelectorAll('[data-launch-meta-group][data-launch-meta-field]').forEach(el=>{
        const group=el.dataset.launchMetaGroup;
        const field=el.dataset.launchMetaField;
        el.value=remaskLaunchMetaDisplay(builder[group]?.[field]);
    });
}
function remaskFilterLaunchMetaFields() {
    const q=String($('launchMetaFieldSearch')?.value||'').trim().toLowerCase();
    document.querySelectorAll('#launchMetaSdkFields details').forEach(group=>{
        let visible=0;
        group.querySelectorAll('.rm-meta-field').forEach(field=>{
            const match=!q||String(field.dataset.search||'').includes(q);
            field.classList.toggle('rm-meta-hidden',!match);
            if(match) visible++;
        });
        group.classList.toggle('rm-meta-hidden',visible===0);
        if(q&&visible) group.open=true;
    });
}
async function remaskLoadLaunchMetaSdkSchema() {
    const data=await apiJson('ajax/metaSdkSchema.php');
    remaskLaunchMetaSchema=data?.schema||{};
    remaskRenderLaunchMetaFields();
}
function remaskCurrentMetaBuilderForJob() {
    const preset=remaskSelectedCreativePreset?.meta_builder||{};
    const extra=remaskReadLaunchMetaSdkFields();
    return {
        campaign:remaskMergeBuilderObject(preset.campaign||{},extra.campaign||{}),
        adset:remaskMergeBuilderObject(preset.adset||{},extra.adset||{}),
        targeting:remaskMergeBuilderObject(preset.targeting||{},extra.targeting||{}),
        identity:remaskMergeBuilderObject({},preset.identity||{}),
        existing_media:remaskMergeBuilderObject({},preset.existing_media||{}),
        creative:remaskMergeBuilderObject(preset.creative||{},extra.creative||{}),
        ad:remaskMergeBuilderObject(preset.ad||{},extra.ad||{})
    };
}
function remaskApplyLaunchMetaFields(payload) {
    const extra=remaskReadLaunchMetaSdkFields();
    const out=remaskMergeBuilderObject({},payload||{});
    out.campaign=remaskMergeBuilderObject(out.campaign||{},extra.campaign||{});
    out.adset=remaskMergeBuilderObject(out.adset||{},extra.adset||{});
    out.adset.targeting=remaskMergeBuilderObject(out.adset.targeting||{},extra.targeting||{});
    out.creative={...(out.creative||{})};
    out.creative.official_params=remaskMergeBuilderObject(out.creative.official_params||{},extra.creative||{});
    out.ad=remaskMergeBuilderObject(out.ad||{},extra.ad||{});
    out._meta_official_v1=true;
    return out;
}

$('launchMetaFieldSearch')?.addEventListener('input',remaskFilterLaunchMetaFields);
remaskLoadLaunchMetaSdkSchema().catch(error=>{
    const status=$('launchMetaSchemaStatus');
    if(status) status.textContent='Meta SDK schema недоступна: '+(error?.payload?.message||error?.message||String(error));
});
JS;
    $js .= "\n".$addon."\n";

    // Ensure every effective payload, with or without a Creative preset, receives Launch-level Meta fields.
    $early="    if (!builder || typeof builder !== 'object') return payload;";
    if(strpos($js,$early)===false){
        fwrite(STDERR,"[launch-full-meta-v113] effective builder early-return anchor missing\n");
        exit(405);
    }
    $js=str_replace($early,"    if (!builder || typeof builder !== 'object') return remaskApplyLaunchMetaFields(payload);",$js,$n);
    if($n!==1){
        fwrite(STDERR,"[launch-full-meta-v113] early-return patch count=$n\n");
        exit(406);
    }

    $fnStart=strpos($js,'function remaskApplySavedMetaBuilderToPayload(payload)');
    $fnEnd=$fnStart===false?false:strpos($js,'/* REMASK_PLACEMENT_PREFLIGHT_V1 */',$fnStart);
    if($fnStart===false||$fnEnd===false){
        fwrite(STDERR,"[launch-full-meta-v113] effective builder boundaries missing\n");
        exit(407);
    }
    $before=substr($js,0,$fnStart);
    $fn=substr($js,$fnStart,$fnEnd-$fnStart);
    $after=substr($js,$fnEnd);
    $fn=str_replace("    return out;\n}","    return remaskApplyLaunchMetaFields(out);\n}",$fn,$returnCount);
    if($returnCount!==1){
        fwrite(STDERR,"[launch-full-meta-v113] final effective-return patch count=$returnCount\n");
        exit(408);
    }
    $js=$before.$fn.$after;

    // Hydrate all additional Launch fields when a saved Creative preset is selected.
    $applyStart=strpos($js,'function applyCreativeLibrarySelection()');
    $applyEnd=$applyStart===false?false:strpos($js,'async function loadCreativeLibraryForLaunch',$applyStart);
    if($applyStart===false||$applyEnd===false){
        fwrite(STDERR,"[launch-full-meta-v113] preset hydrate boundaries missing\n");
        exit(409);
    }
    $applyBefore=substr($js,0,$applyStart);
    $applyFn=substr($js,$applyStart,$applyEnd-$applyStart);
    $applyAfter=substr($js,$applyEnd);
    if(strpos($applyFn,'remaskPopulateLaunchMetaSdkFields(metaBuilder)')===false){
        $hydrateNeedle="    renderCreativeLibraryStatus();\n    invalidateLaunchReview();";
        $hydrateReplace="    renderCreativeLibraryStatus();\n    remaskPopulateLaunchMetaSdkFields(metaBuilder);\n    invalidateLaunchReview();";
        if(strpos($applyFn,$hydrateNeedle)===false){
            fwrite(STDERR,"[launch-full-meta-v113] preset hydrate anchor missing\n");
            exit(410);
        }
        $applyFn=str_replace($hydrateNeedle,$hydrateReplace,$applyFn,$hydrateCount);
        if($hydrateCount!==1){
            fwrite(STDERR,"[launch-full-meta-v113] preset hydrate count=$hydrateCount\n");
            exit(411);
        }
    }
    $js=$applyBefore.$applyFn.$applyAfter;

    // Backend receives the same effective builder, not the stale saved preset.
    $formNeedle="    if (libraryPreset?.meta_builder) form.append('meta_builder', JSON.stringify(libraryPreset.meta_builder));";
    $formReplace=<<<'JS'
    const remaskEffectiveBuilder = remaskCurrentMetaBuilderForJob();
    if (Object.values(remaskEffectiveBuilder).some((section) => section && Object.keys(section).length)) {
        form.append('meta_builder', JSON.stringify(remaskEffectiveBuilder));
    }
JS;
    if(strpos($js,$formNeedle)===false){
        fwrite(STDERR,"[launch-full-meta-v113] meta_builder form anchor missing\n");
        exit(411);
    }
    $js=str_replace($formNeedle,$formReplace,$js,$formCount);
    if($formCount!==1){
        fwrite(STDERR,"[launch-full-meta-v113] meta_builder form count=$formCount\n");
        exit(412);
    }
}

$php=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260921-full-meta-v113" type="module"></script>',
    $php,1,$cacheCount
) ?? $php;
if($cacheCount!==1){
    fwrite(STDERR,"[launch-full-meta-v113] cache-bust failed\n");
    exit(413);
}

file_put_contents($phpPath,$php);
file_put_contents($jsPath,$js);
fwrite(STDERR,"[launch-full-meta-v113] all writable SDK fields are exposed and wired into Review/Job\n");
