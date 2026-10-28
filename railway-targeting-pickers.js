/* REMASK_PRIVATE_TARGETING_PICKERS_V1 */
// Browse and search share IDs and the original template serializers.
if (typeof creativeTargetSearchConfig !== 'function') {
    const pickerControllers = {};
    const pickerPages = {};
    const pickerConfig = {
        locations:{input:'geoQuery',results:'geoResults',pills:'selectedGeo',state:'geo'},
        interests:{input:'interestQuery',results:'interestResults',pills:'selectedInterests',state:'interests'},
        behaviors:{input:'behaviorQuery',results:'behaviorResults',pills:'selectedBehaviors',state:'behaviors'},
        locales:{input:'languageQuery',results:'languageResults',pills:'selectedLanguages',state:'languages'}
    };
    // Search is automatic while typing; remove legacy controls entirely.
    for (const id of ['geoSearch','interestSearch']) {
        const button=$(id);
        if(button)button.remove();
    }
    const targetingFailureMessage = error => {
        const raw=String(error?.code||error?.payload?.error?.code||error?.payload?.code||
            error?.payload?.error||error?.payload?.message||error?.message||'').toUpperCase();
        if(/CHECKPOINT/.test(raw))return 'Чекпоинт Meta: профиль остановлен. Новые запросы запрещены.';
        if(/TWO_FACTOR_REQUIRED/.test(raw))return 'Meta запросила подтверждение входа. Список недоступен.';
        if(/TARGETING_META_ACCESS_STOPPED|SESSION_EXPIRED|BUSINESS_LOGIN_GATE|LOGIN/.test(raw))return 'Meta не подтвердила вход. Список недоступен.';
        if(/RESOURCE_READ_INCOMPLETE/.test(raw))return 'Meta не вернула список для выбранного РК. Доступ не подтверждён.';
        if(/TIMEOUT|TIMED_OUT/.test(raw))return 'Meta отвечает слишком долго. Список не загружен.';
        return 'Список сейчас недоступен. Выбранные настройки не изменены.';
    };
    const runPicker = async (type, offset=0) => {
        const cfg=pickerConfig[type], input=cfg&&$(cfg.input), box=cfg&&$(cfg.results);
        if(!input||!box)return;
        const query=input.value.trim();
        if(query.length<2){
            pickerControllers[type]?.abort();delete pickerControllers[type];delete pickerPages[type];
            box.textContent='';return;
        }
        if(offset && (pickerPages[type]?.query!==query || pickerPages[type]?.next_offset!==offset))return;
        pickerControllers[type]?.abort();
        const controller=new AbortController(); pickerControllers[type]=controller;
        const slowStatusTimer=!offset?setTimeout(()=>{
            if(pickerControllers[type]===controller && input.value.trim()===query)
                box.textContent='Загрузка списка занимает дольше обычного…';
        },3000):0;
        if(!offset) {delete pickerPages[type];box.textContent='Загружаю варианты Meta…';}
        try {
            const data=await apiJson('ajax/metaTemplateTargetingSearch.php',{
                ...formPost({type:type==='locales'?'languages':type,q:query,limit:100,
                    ...(offset?{offset}: {})}),signal:controller.signal
            });
            if(pickerControllers[type]!==controller || input.value.trim()!==query)return;
            if(offset && (data?.offset!==offset || !Array.isArray(data?.data)))
                throw new Error('Страница справочника устарела. Повторите поиск.');
            const rows=Array.isArray(data?.data)?data.data:[];
            const oldRows=offset?(pickerPages[type]?.rows||[]):[];
            const id=row=>type==='locations'? String(row.type)+':'+String(row.key||row.id):String(row.id||row.key);
            const seen=new Set(oldRows.map(id));
            const all=oldRows.slice();
            for(const row of rows) {
                if(!seen.has(id(row))) {seen.add(id(row));all.push(row);}
            }
            const next=Number.isSafeInteger(data?.next_offset)&&data.next_offset>offset
                && data.next_offset<=19900 ? data.next_offset : null;
            pickerPages[type]={rows:all,query,next_offset:next};
            box.textContent='';
            for(const item of all){
                const button=document.createElement('button');
                button.type='button'; button.className='search-item';
                button.dataset.remaskTargetChoice='1';
                button.style.cssText='display:block;width:100%;text-align:left;border:0;background:transparent;color:inherit';
                const detail=type==='locations'?[item.country_name,item.type==='region'?'Регион':''].filter(Boolean).join(' · ')
                    :(item.path||[]).join(' › ');
                button.textContent=String(item.name||item.key||item.id)+(detail?' · '+detail:'');
                button.addEventListener('click',()=>{
                    pickerControllers[type]?.abort();delete pickerControllers[type];
                    delete pickerPages[type];
                    const target=state[cfg.state]||(state[cfg.state]=[]);
                    if(!target.some(row=>id(row)===id(item)))target.push(item);
                    renderPills($(cfg.pills),target,cfg.state);
                    input.value='';box.textContent='';
                    invalidateLaunchReview();validateReady();
                });
                box.appendChild(button);
            }
            if(!all.length)box.textContent='Ничего не найдено. Попробуйте другое название.';
            if(data?.source==='private_meta_template_reference' && data.reference_checked_at){
                const hint=document.createElement('div');hint.className='muted';
                hint.textContent='Сохранённые данные Meta · '+new Date(data.reference_checked_at*1000).toLocaleString('ru-RU')+
                    (data.reference_stale?' · актуальность не проверена':'');box.appendChild(hint);
            }
            if(next!==null){
                const more=document.createElement('button');
                more.type='button';more.className='search-item';
                more.dataset.remaskTargetMore='1';
                more.textContent='Показать ещё 100 вариантов';
                more.addEventListener('click',()=>runPicker(type,next));
                box.appendChild(more);
            } else if(data?.truncated){
                const hint=document.createElement('div');hint.className='muted';
                hint.textContent='Введите название, чтобы сузить список.';box.appendChild(hint);
            }
        } catch(error){
            if(error?.name==='AbortError'||pickerControllers[type]!==controller)return;
            if(offset){
                box.textContent=targetingFailureMessage(error);
            } else box.textContent=targetingFailureMessage(error);
        } finally {
            if(slowStatusTimer)clearTimeout(slowStatusTimer);
            if(pickerControllers[type]===controller)delete pickerControllers[type];
        }
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
            clearTimeout(focusTimer);
            if(input.value.trim().length>=2)focusTimer=setTimeout(()=>runPicker(type),240);
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
