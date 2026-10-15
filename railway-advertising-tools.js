/* Reviewed private advertising reads, shared by universal templates and Launch. */
(() => {
    'use strict';
    const byId=id=>document.getElementById(id);
    const creative=Boolean(byId('creativeForm'));
    const root=creative?byId('creativeForm'):document.querySelector('.launch-wrap');
    if(!root)return;
    const style=document.createElement('style');
    style.textContent=`.rm-ad-estimate{border:1px solid #37404c;background:#191e25;border-radius:9px;padding:14px;margin:14px 0;color:#e3e8f1;min-width:0;overflow-wrap:anywhere}.rm-ad-estimate h4{font-size:14px;margin:0 0 10px}.rm-ad-estimate p{font-size:12px;color:#9da7b7;margin:8px 0;line-height:1.5}.rm-ad-estimate dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:12px 0}.rm-ad-estimate dt{font-size:11px;color:#9da7b7}.rm-ad-estimate dd{font-size:21px;margin:3px 0}.rm-ad-estimate button{margin:4px 8px 4px 0}.rm-ad-estimate iframe{width:100%;height:500px;border:0;background:white}.rm-ad-estimate [role=status]{white-space:pre-line;overflow-wrap:anywhere}
    .wf-summary .rm-ad-estimate{margin:0 0 24px}.wf-summary .rm-ad-estimate h4{text-transform:none;letter-spacing:0;color:#e3e8f1;font-size:14px;margin:0 0 10px}.wf-summary .rm-ad-estimate dl{grid-template-columns:1fr;gap:12px;margin:12px 0}.wf-summary .rm-ad-estimate dt{margin:0}.wf-summary .rm-ad-estimate dd{font-size:21px;line-height:1.3;margin:3px 0}.wf-summary .rm-ad-estimate button{display:block;width:100%;white-space:normal;margin:8px 0 0;font-size:12px;padding:8px}
    .launch-wrap.rm-estimate-layout{display:grid;grid-template-columns:minmax(0,1fr) 280px;column-gap:18px;max-width:1460px;align-items:start}.launch-wrap.rm-estimate-layout>*{grid-column:1;min-width:0}.launch-wrap.rm-estimate-layout>.rm-estimate-rail{grid-column:2;position:sticky;top:140px;max-height:calc(100dvh - 160px);overflow:auto;margin:20px 0;scrollbar-color:#425064 #181e28;scrollbar-width:thin}.rm-estimate-rail .rm-ad-estimate{margin:0}.rm-estimate-rail .rm-ad-estimate dl{grid-template-columns:1fr}.rm-estimate-rail button{width:100%;white-space:normal}
    @media(max-width:1000px){.launch-wrap.rm-estimate-layout{display:block}.launch-wrap.rm-estimate-layout>.rm-estimate-rail{position:static;max-height:none;margin:16px 0}.rm-estimate-rail .rm-ad-estimate dl{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}.rm-estimate-rail button{width:auto}}
    @media(max-width:900px){.wf-summary .rm-ad-estimate{border:0;padding:0;margin:0}.wf-summary .rm-ad-estimate dl{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:8px 0}.wf-summary .rm-ad-estimate dd{font-size:17px}.wf-summary .rm-ad-estimate button{display:inline-block;width:auto;margin:4px 6px 0 0}.wf-summary .rm-ad-estimate p{font-size:11px;margin:4px 0}}
    `;
    document.head.appendChild(style);
    const panel=document.createElement('section');panel.className='rm-ad-estimate';panel.id='remaskAdvertisingEstimate';
    panel.setAttribute('aria-label','Потенциальная аудитория');
    panel.innerHTML='<h4>Потенциальная аудитория</h4><div data-rm-estimates></div><p data-rm-estimate-status role="status" aria-live="polite">Добавьте географию. Размер аудитории загрузится автоматически.</p>';
    if(creative) {
        // workflow-ui may execute before or after this compiled editor module.
        // Its setup moves this same node, preserving readers and event handlers.
        const sidebar=root.querySelector('.wf-summary');
        if(sidebar)sidebar.prepend(panel);else root.appendChild(panel);
    } else {
        const rail=document.createElement('aside');rail.className='rm-estimate-rail';rail.setAttribute('aria-label','Оценка аудитории');
        rail.appendChild(panel);root.prepend(rail);root.classList.add('rm-estimate-layout');
        const span=()=>{
            // Include anonymous grid items, but never manufacture empty rows.
            const rows=Array.from(root.childNodes).filter(n=>n.nodeType===1||(n.nodeType===3&&n.textContent.trim())).length;
            rail.style.gridRow='1 / span '+Math.max(1,rows-1);
        };
        span();new MutationObserver(span).observe(root,{childList:true});
    }
    const results=panel.querySelector('[data-rm-estimates]');
    const status=panel.querySelector('[data-rm-estimate-status]');
    function emptyMetrics() {
        results.innerHTML='<dl data-rm-placeholder><div><dt>Возможная аудитория</dt><dd>—</dd></div></dl>';
    }
    emptyMetrics();
    let timer=null, generation=0, controller=null, lastFingerprint='', needsRefresh=true, lastReadAt=0;
    const valid=n=>typeof n==='number'&&Number.isFinite(n)&&n>=0;
    const fmt=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n);
    function range(low,high,value) {
        if(valid(low)&&valid(high)&&low<=high)return fmt(low)+' – '+fmt(high);
        return valid(value)?'≈ '+fmt(value):'Нет оценки';
    }
    function specification() {
        if(creative) {
            const builder=buildMetaBuilder();
            return {campaign:builder.campaign||{},adset:builder.adset||{},targeting:builder.targeting||{}};
        }
        const payload=remaskApplySavedMetaBuilderToPayload(buildPayload());
        return {...payload,targeting:payload.adset?.targeting||{}};
    }
    function destinations() {
        if(creative)return [{}];
        if(targetMode())return selectedTargets().length?selectedTargets().map(t=>({profile:String(t.profile),account_id:String(t.account_id).replace(/^act_/,''),binding:bindingFor(t.account_id)})):[{}];
        const accounts=Array.from(byId('adAccount')?.selectedOptions||[]).filter(o=>o.value);
        return state.profile && accounts.length?accounts.map(o=>({profile:String(state.profile),account_id:o.value.replace(/^act_/,''),binding:{}})):[{}];
    }
    function requestSpec(spec,destination={}) {
        const campaign=spec.campaign,adset=spec.adset;
        const cbo=Boolean(campaign.daily_budget||campaign.lifetime_budget);
        const request={include_forecast:false,targeting_spec:JSON.parse(JSON.stringify(spec.targeting)),optimization_goal:adset.optimization_goal,
            bid_strategy:(cbo?campaign.bid_strategy:adset.bid_strategy)||adset.bid_strategy||campaign.bid_strategy||'LOWEST_COST_WITHOUT_CAP',
            special_ad_categories:campaign.special_ad_categories||[],is_cbo_enabled:cbo};
        for(const key of ['promoted_object','attribution_spec','bid_amount'])if(adset[key]!==undefined)request[key]=adset[key];
        if(adset.destination_type)request.campaign_destination_type=adset.destination_type;
        const budget=cbo?campaign.daily_budget:adset.daily_budget;
        if(budget!==undefined)request.daily_budget=budget;
        if(destination.account_id) {
            const binding=destination.binding||{};
            for(const key of ['custom_audiences','excluded_custom_audiences'])if(Array.isArray(binding[key])) {
                if(binding[key].length)request.targeting_spec[key]=binding[key].map(id=>({id:String(id)}));
                else if(targetMode())delete request.targeting_spec[key];
            }
            if(binding.pixel_id)request.promoted_object={...request.promoted_object,pixel_id:String(binding.pixel_id),custom_event_type:binding.conversion_event||adset.promoted_object?.custom_event_type};
        }
        return request;
    }
    function snapshot() {
        const spec=specification();const targets=destinations();
        return {spec,targets,fingerprint:JSON.stringify(targets.map(t=>({...requestSpec(spec,t),profile:t.profile,account_id:t.account_id})))};
    }
    async function read(helper,input,destination,signal) {
        const scoped=Boolean(destination?.account_id);
        const endpoint=scoped?{audience_estimate:'metaAudienceEstimate.php',placements:'metaPlacementCapabilities.php',preview:'metaCreativePreview.php'}[helper]:'metaTemplateAdvertisingHelper.php';
        const body=new URLSearchParams();
        for(const [key,value] of Object.entries(input))if(value!==undefined)body.set(key,typeof value==='object'?JSON.stringify(value):String(value));
        if(scoped){body.set('profile',destination.profile);body.set('account_id',destination.account_id);}else body.set('helper',helper);
        const data=await (creative?api:apiJson)('ajax/'+endpoint,{method:'POST',body,signal});
        if(!data||data.checked_live!==true||data.account_scope_verified!==true||data.transport!=='private_http_h2'
            ||(scoped?(data.profile!==destination.profile||String(data.account_id)!==destination.account_id):data.template_account_bound!==false))
            throw new Error('Источник оценки не подтверждён. Настройки можно сохранить.');
        return data;
    }
    function render(data,destination) {
        const box=document.createElement('article');
        if(destination.account_id){const title=document.createElement('p');title.textContent='РК '+destination.account_id;box.appendChild(title);}
        const dl=document.createElement('dl');
        function metric(label,text){const cell=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=text;cell.append(dt,dd);dl.appendChild(cell);}
        metric('Возможная аудитория',data.estimate_ready?range(data.lower_bound,data.upper_bound,data.estimate_mau):'Ещё не готова');
        if(data.estimate_ready&&valid(data.estimate_mau_advantage_audience_enabled))metric('С расширением Advantage+',fmt(data.estimate_mau_advantage_audience_enabled));
        if(data.estimate_ready&&valid(data.estimate_mau_relaxation_aware))metric('С расширением таргетинга',fmt(data.estimate_mau_relaxation_aware));
        box.appendChild(dl);results.appendChild(box);
    }
    function invalidate(message='Настройки изменились. Оценка обновляется…') {
        generation++;controller?.abort();clearTimeout(timer);lastFingerprint='';emptyMetrics();status.textContent=message;
    }
    async function estimate(force=false,attempt=0) {
        if(document.hidden||(creative&&!byId('creativeModal')?.classList.contains('open')))return;
        let current;
        try{current=snapshot();if(!current.spec.targeting.geo_locations||!Object.keys(current.spec.targeting.geo_locations).length)throw new Error('Выберите географию аудитории.');}
        catch(error){invalidate(error.message);lastFingerprint='';return;}
        if(!force&&current.fingerprint===lastFingerprint)return;
        invalidate('Получаем оценку Meta…');lastFingerprint=current.fingerprint;
        const seq=generation;controller=new AbortController();const signal=controller.signal;
        let success=0, pending=false, failed=false;lastReadAt=Date.now();
        // Bound concurrent profile reads; never reuse one account's numbers for another.
        for(const target of current.targets.slice(0,20)) {
            if(seq!==generation)return;
            try {
                const data=await read('audience_estimate',requestSpec(current.spec,target),target,signal);
                if(seq!==generation||snapshot().fingerprint!==current.fingerprint)return;
                results.querySelector('[data-rm-placeholder]')?.remove();render(data,target);success++;pending ||= data.estimate_ready!==true;
            } catch(error) {
                if(seq!==generation||signal.aborted)return;
                failed=true;const p=document.createElement('p');p.textContent=(target.account_id?'РК '+target.account_id+': ':'')+'Размер аудитории временно недоступен.';p.title=error.message;results.appendChild(p);
            }
        }
        if(seq!==generation)return;
        needsRefresh=pending||failed;
        status.textContent=pending?'Meta готовит размер аудитории…':failed?'Данные Meta временно недоступны. Шаблон можно сохранить.':'Размер аудитории получен из Meta.';
        if(current.targets.length>20)status.textContent+=' Показаны первые 20 выбранных РК.';
        // Back off while the visible editor still needs a real Meta response.
        // The worker bounds each read and cools down unavailable sources.
        if(needsRefresh)timer=setTimeout(()=>{
            try{if(seq===generation&&snapshot().fingerprint===current.fingerprint)estimate(true,Math.min(2,attempt+1));}catch{}
        },failed?([15000,45000][attempt]||60000):([2000,5000][attempt]||30000));
    }
    function changed(force=false) {
        if(creative&&!byId('creativeModal')?.classList.contains('open'))return;
        let same=false;try{same=snapshot().fingerprint===lastFingerprint;}catch{}
        if(same&&!force)return;
        needsRefresh=true;invalidate();timer=setTimeout(()=>estimate(),900);
    }
    if(!creative) {
        const preview=document.createElement('section');preview.className='rm-ad-estimate';
        preview.setAttribute('aria-label','Предпросмотр Meta');
        preview.innerHTML='<h4>Предпросмотр Meta</h4><label for="remaskPreviewFormat">Место показа</label><select id="remaskPreviewFormat" class="form-control"><option value="DESKTOP_FEED_STANDARD">Лента Facebook · компьютер</option></select><button type="button" class="btn btn-secondary" data-rm-preview>Показать предпросмотр</button><p data-rm-preview-status role="status" aria-live="polite">Выберите РК, страницу и существующее медиа Meta. Для файла из библиотеки доступен локальный предпросмотр объявления.</p><div data-rm-preview-body></div>';
        (byId('creativeName')?.closest('.launch-card')||root).appendChild(preview);
        const previewStatus=preview.querySelector('[data-rm-preview-status]'),body=preview.querySelector('[data-rm-preview-body]');
        let seq=0,abort=null;
        const clear=()=>{seq++;abort?.abort();body.replaceChildren();previewStatus.textContent='Настройки изменились. Обновите предпросмотр.';};
        root.addEventListener('input',e=>{if(!preview.contains(e.target))clear();});
        root.addEventListener('change',clear);
        function previewInput(spec,target) {
            const c=spec.creative||{},binding=target.binding||{};
            const page=targetMode()?binding.page_id:c.page_id;
            const actor=targetMode()?binding.instagram_actor_id:c.instagram_actor_id;
            if(!target.account_id||!page)throw new Error('Выберите РК и доступную ему страницу.');
            const result={};
            for(const key of ['degrees_of_freedom_spec','asset_feed_spec','platform_customizations','url_tags'])if(c[key]!==undefined)result[key]=c[key];
            const story={page_id:String(page)};
            if(actor)story.instagram_actor_id=String(actor);
            if(creativeFormat()==='INSTAGRAM_POST') {
                const post=targetMode()?binding.source_instagram_media_id:c.source_instagram_media_id;
                if(!post)throw new Error('Выберите публикацию Instagram для этого РК.');
                result.source_instagram_media_id=String(post);
            } else {
                const selected=targetMode()?binding:state.pendingWorkspaceMedia;
                if(!selected||(!targetMode()&&String(selected.account_id).replace(/^act_/,'')!==target.account_id))
                    throw new Error('Выберите существующее медиа Meta для этого РК. Файл из библиотеки показан в локальном предпросмотре.');
                const hash=selected.existing_image_hash||selected.image_hash,video=selected.existing_video_id||selected.video_id;
                const data={message:c.message||'',call_to_action:{type:c.call_to_action||'LEARN_MORE',value:{link:c.link||''}}};
                if(hash)story.link_data={...data,image_hash:hash,link:c.link||'',name:c.headline||'',description:c.description||''};
                else if(video)story.video_data={...data,video_id:String(video),title:c.headline||''};
                else throw new Error('Для предпросмотра выберите изображение, видео или публикацию Instagram.');
            }
            result.object_story_spec=story;
            return {creative:result,ad_format:byId('remaskPreviewFormat').value};
        }
        function previewSnapshot(){const spec=specification(),targets=destinations();return targets.map(target=>({target,input:previewInput(spec,target)}));}
        preview.querySelector('[data-rm-preview]').addEventListener('click',async()=>{
            clear();const token=seq;abort=new AbortController();const signal=abort.signal;
            try {
                const requests=previewSnapshot(),fingerprint=JSON.stringify(requests);
                previewStatus.textContent='Получаем предпросмотр Meta…';
                for(const {target,input} of requests.slice(0,20)) {
                    const data=await read('preview',input,target,signal);
                    if(token!==seq||JSON.stringify(previewSnapshot())!==fingerprint)return;
                    const title=document.createElement('p');title.textContent='РК '+target.account_id;
                    const frame=document.createElement('iframe');frame.title='Предпросмотр Meta · РК '+target.account_id;
                    frame.setAttribute('sandbox','');frame.referrerPolicy='no-referrer';frame.srcdoc=data.body;
                    body.append(title,frame);
                }
                if(token===seq)previewStatus.textContent='Предпросмотр получен из Meta. Реклама не публикуется.';
            } catch(error){if(token===seq&&!signal.aborted)previewStatus.textContent=error.message;}
        });
        // The format list comes from the same reviewed metadata as the worker.
        fetch('ajax/metaPrivateSchema.php').then(r=>r.text()).then(raw=>{
            const formats=JSON.parse(raw).data?.preview_formats;
            if(!Array.isArray(formats)||!formats.length)return;
            const select=byId('remaskPreviewFormat');select.replaceChildren();
            for(const value of formats){const option=document.createElement('option');option.value=value;option.textContent=value.replaceAll('_',' ');select.appendChild(option);}
            if(formats.includes('DESKTOP_FEED_STANDARD'))select.value='DESKTOP_FEED_STANDARD';
        }).catch(()=>{});
        root.addEventListener('click',e=>{if(e.target.closest('#bundleLoad,#creativeLibraryLaunch,#clearExistingMedia'))clear();});
    }
    root.addEventListener('input',e=>{if(!panel.contains(e.target))changed();});
    root.addEventListener('change',e=>{if(!panel.contains(e.target))changed();});
    root.addEventListener('click',()=>setTimeout(()=>changed(),0));
    if(creative) {
        const originalOpen=openEditor;
        openEditor=function(...args){const value=originalOpen(...args);lastFingerprint='';changed();return value;};
        const originalClose=closeEditor;
        closeEditor=function(...args){invalidate('Задайте географию аудитории.');lastFingerprint='';return originalClose(...args);};
    } else {
        const originalRestore=applyBundleData;
        applyBundleData=async function(...args){invalidate('Загрузка настроек…');lastFingerprint='';try{return await originalRestore(...args);}finally{changed();}};
        const originalPreset=applyCreativeLibrarySelection;
        applyCreativeLibrarySelection=function(...args){const value=originalPreset(...args);changed();return value;};
        changed();
    }
    const resume=()=>{if(needsRefresh&&!document.hidden&&Date.now()-lastReadAt>=15000)changed(true);};
    window.addEventListener('focus',resume);window.addEventListener('online',resume);document.addEventListener('visibilitychange',resume);
})();
