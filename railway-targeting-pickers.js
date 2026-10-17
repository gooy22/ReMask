/* REMASK_PRIVATE_TARGETING_PICKERS_V1 */
// Browse and search share IDs and the original template serializers.
if (typeof creativeTargetSearchConfig !== 'function') {
    const pickerControllers = {};
    const pickerConfig = {
        locations:{input:'geoQuery',results:'geoResults',pills:'selectedGeo',state:'geo'},
        interests:{input:'interestQuery',results:'interestResults',pills:'selectedInterests',state:'interests'},
        behaviors:{input:'behaviorQuery',results:'behaviorResults',pills:'selectedBehaviors',state:'behaviors'},
        locales:{input:'languageQuery',results:'languageResults',pills:'selectedLanguages',state:'languages'}
    };
    const runPicker = async type => {
        const cfg=pickerConfig[type], input=$(cfg.input), box=$(cfg.results);
        if(!input||!box)return;
        pickerControllers[type]?.abort();
        const controller=new AbortController(); pickerControllers[type]=controller;
        box.textContent='Загружаю варианты Meta…';
        try {
            const data=await apiJson('ajax/metaTemplateTargetingSearch.php',{
                ...formPost({type:type==='locales'?'languages':type,q:input.value.trim(),limit:100}),signal:controller.signal
            });
            if(pickerControllers[type]!==controller)return;
            box.textContent='';
            const rows=Array.isArray(data?.data)?data.data:[];
            for(const item of rows.slice(0,100)){
                const button=document.createElement('button');
                button.type='button'; button.className='search-item';
                button.dataset.remaskTargetChoice='1';
                button.style.cssText='display:block;width:100%;text-align:left;border:0;background:transparent;color:inherit';
                const detail=type==='locations'?[item.country_name,item.type==='region'? 'Регион':'' ].filter(Boolean).join(' · ')
                    :(item.path||[]).join(' › ');
                button.textContent=String(item.name||item.key||item.id)+(detail?' · '+detail:'');
                button.addEventListener('click',()=>{
                    pickerControllers[type]?.abort();delete pickerControllers[type];
                    const target=state[cfg.state]||(state[cfg.state]=[]);
                    const id=row=>type==='locations'? String(row.type)+':'+String(row.key||row.id):String(row.id||row.key);
                    if(!target.some(row=>id(row)===id(item)))target.push(item);
                    renderPills($(cfg.pills),target,cfg.state);
                    input.value='';box.textContent='';
                    invalidateLaunchReview();validateReady();
                });
                box.appendChild(button);
            }
            if(!rows.length)box.textContent='Ничего не найдено. Попробуйте другое название.';
            if(rows.length>100||data.truncated){
                const hint=document.createElement('div');hint.className='muted';
                hint.textContent='Введите название, чтобы сузить список.';box.appendChild(hint);
            }
        } catch(error){
            if(error?.name==='AbortError'||pickerControllers[type]!==controller)return;
            box.textContent=error.payload?.message||error.meta?.message||'Не удалось загрузить справочник Meta. Повторите поиск.';
        } finally {if(pickerControllers[type]===controller)delete pickerControllers[type];}
    };
    searchTargeting=type=>runPicker(type);
    searchAudienceLanguages=()=>runPicker('locales');
    if(typeof ensureLanguageTargetingUi==='function')ensureLanguageTargetingUi();
    if(typeof ensureBehaviorTargetingUi==='function')ensureBehaviorTargetingUi();
    for(const [type,cfg] of Object.entries(pickerConfig)){
        const input=$(cfg.input);if(!input)continue;
        input.placeholder='Выберите из списка или введите название';
        let focusTimer=0;
        input.addEventListener('focus',()=>{
            clearTimeout(focusTimer);focusTimer=setTimeout(()=>runPicker(type),240);
        });
        // Own the debounce before legacy input handlers, which otherwise issue
        // duplicate reads (and immediately submit one-character queries).
        input.addEventListener('input',event=>{
            event.stopImmediatePropagation();
            clearTimeout(focusTimer);
            pickerControllers[type]?.abort();delete pickerControllers[type];
            $(cfg.results).textContent='';
            focusTimer=setTimeout(()=>runPicker(type),240);
        },{capture:true});
        input.addEventListener('blur',()=>clearTimeout(focusTimer));
        input.addEventListener('keydown',event=>{
            if(event.key==='Enter'){
                event.preventDefault();event.stopImmediatePropagation();
                clearTimeout(focusTimer);runPicker(type);
            }
            if(event.key==='ArrowDown'){
                const first=$(cfg.results)?.querySelector('button');
                if(first){event.preventDefault();first.focus();}
            }
            if(event.key==='Escape'){
                event.stopImmediatePropagation();clearTimeout(focusTimer);
                pickerControllers[type]?.abort();delete pickerControllers[type];
                $(cfg.results).textContent='';
            }
        },{capture:true});
    }
}
