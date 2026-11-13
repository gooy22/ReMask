/* REMASK_META_MODULE_CONTROLS_V1: shared source-backed choices in both editors. */
const remaskModuleControls = (() => {
    let metadata = {}, scheduled = false;
    const primary = {mbUserDevice:['targeting','user_device','csv'],mbUserOs:['targeting','user_os','csv']};
    const primaryEnums = {mbBuyingType:['campaign','buying_type']};
    const style = document.createElement('style');
    style.textContent = '.rm-module-source,.cr-editor .form-control.rm-module-source,.wf-launch .form-control.rm-module-source{display:none!important}.rm-module-field .rm-meta-visual-actions{display:none!important}.rm-module-pills{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.rm-module-choice{display:block;width:100%;text-align:left;padding:8px;border:0;background:transparent;color:inherit;cursor:pointer}.rm-module-results{max-height:240px;overflow:auto;border-radius:8px}.rm-module-results:not(:empty){border:1px solid #384455;padding:4px}.rm-module-pill{border:1px solid #465366;border-radius:14px;background:#263346;color:inherit;padding:5px 10px;cursor:pointer}.rm-module-hint{font-size:12px;opacity:.72;padding:6px}';
    style.textContent += '.rm-module-exclusion-control{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;width:100%}.rm-module-exclusion-group{min-width:0;padding:12px;border:1px solid #34445a;border-radius:12px;background:rgba(15,23,42,.24)}.rm-module-exclusion-label{display:block;margin:0 0 8px;font-size:12px;font-weight:700;color:#d7e2f1}.rm-module-exclusion-group .form-control{width:100%;min-width:0}.rm-module-exclusion-settings{grid-column:1/-1;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.rm-module-structure{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.rm-module-structure>.rm-module-hint{flex-basis:100%}@media(max-width:640px){.rm-module-exclusion-control{grid-template-columns:minmax(0,1fr)}.rm-module-exclusion-group{padding:10px}}';
    document.head.appendChild(style);
    const key = row => String(typeof row === 'object' ? row?.id ?? row?.key : row);
    const fieldLabels=source=>[...new Set([...Array.from(source.labels||[]),source.closest('.cr-field,.cr-sdk-field,.rm-meta-field')?.querySelector('label')].filter(Boolean))];
    function edited(source) {
        const binding=primary[source.id]||primaryComplex[source.id];
        const group=source.dataset.metaGroup||source.dataset.launchMetaGroup||binding?.[0];
        const field=source.dataset.metaField||source.dataset.launchMetaField||binding?.[1];
        if(group&&field&&group!==field)source.dispatchEvent(new CustomEvent('remask-module-edit',{bubbles:true,detail:{group,field}}));
    }
    function button(text, className, action) {
        const node=document.createElement('button');node.type='button';node.className=className;
        node.dataset.remaskTargetChoice='1';node.textContent=text;node.addEventListener('click',action);return node;
    }
    function attachTargetingExclusions(source) {
        if(source.dataset.remaskTargetingExclusions==='1')return;
        source.dataset.remaskTargetingExclusions='1';
        attachStructure(source,'exclusions','JSON');
        source.classList.add('rm-module-source');
        source.closest('.rm-meta-field,.cr-sdk-field')?.classList.add('rm-module-field');
        const root=document.createElement('div');root.className='rm-module-control rm-module-exclusion-control';
        const sections=[
            {key:'interests',lookup:'interests',label:'Исключить интересы',placeholder:'Найти интерес для исключения'},
            {key:'behaviors',lookup:'behaviors',label:'Исключить поведение',placeholder:'Найти категорию поведения'}
        ];
        const descriptor=Object.getOwnPropertyDescriptor(source,'value')||
            Object.getOwnPropertyDescriptor(source instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value');
        const readRoot=()=>{
            const raw=String(descriptor.get.call(source)||'').trim();
            if(!raw)return {};
            const parsed=JSON.parse(raw);
            if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Структура исключений должна быть объектом.');
            return parsed;
        };
        const rowsFor=section=>{
            try{const rows=readRoot()[section.key];return Array.isArray(rows)?rows:[];}catch{return [];}
        };
        function paint(){
            for(const section of sections){
                section.pills.replaceChildren();
                for(const row of rowsFor(section)){
                    const label=String(row?.name||row?.id||row?.key||'Выбранный пункт');
                    const pill=button(label+' ×','rm-module-pill',()=>commit(section,rowsFor(section).filter(value=>key(value)!==key(row))));
                    pill.setAttribute('aria-label','Убрать из исключений: '+label);
                    section.pills.appendChild(pill);
                }
            }
        }
        function commit(section,rows){
            let current;
            try{current=readRoot();}catch(error){
                section.status.textContent=error.message||'Исправьте структуру через «Настроить».';
                return false;
            }
            if(rows.length)current[section.key]=rows;
            else delete current[section.key];
            source.value=Object.keys(current).length?JSON.stringify(current):'';
            edited(source);
            source.dispatchEvent(new Event('input',{bubbles:true}));
            source.dispatchEvent(new Event('change',{bubbles:true}));
            return true;
        }
        for(const section of sections){
            const card=document.createElement('div');card.className='rm-module-exclusion-group';
            const label=document.createElement('label');label.className='rm-module-exclusion-label';label.textContent=section.label;
            const search=document.createElement('input');search.type='search';search.className='form-control';
            search.id=source.id+'_exclude_'+section.key;search.placeholder=section.placeholder;search.autocomplete='off';
            search.setAttribute('role','combobox');search.setAttribute('aria-autocomplete','list');search.setAttribute('aria-expanded','false');
            search.setAttribute('aria-controls',search.id+'_results');label.htmlFor=search.id;
            const pills=document.createElement('div');pills.className='rm-module-pills';
            const results=document.createElement('div');results.className='rm-module-results';results.id=search.id+'_results';results.setAttribute('role','group');
            const status=document.createElement('div');status.className='rm-module-hint';status.setAttribute('role','status');
            card.append(label,search,pills,results,status);root.appendChild(card);
            section.search=search;section.pills=pills;section.results=results;section.status=status;
            let controller=null,timer=0,generation=0;
            function choose(row){
                if(!row||row.id===undefined||row.id===null)return;
                const current=rowsFor(section);
                if(!current.some(value=>key(value)===String(row.id))){
                    if(!commit(section,[...current,{id:String(row.id),name:String(row.name||row.id)}]))return;
                }
                controller?.abort();generation++;search.value='';results.replaceChildren();status.textContent='';search.setAttribute('aria-expanded','false');
            }
            async function browse(){
                clearTimeout(timer);controller?.abort();const run=++generation;controller=new AbortController();
                const q=search.value.trim();results.replaceChildren();status.textContent='Загружаю варианты Meta…';
                try{
                    const body=new URLSearchParams({type:section.lookup,q,limit:'100'}).toString();
                    const options={method:'POST',headers:{
                        'Content-Type':'application/x-www-form-urlencoded',
                        'X-ReMask-CSRF':document.querySelector('meta[name="remask-csrf"]')?.content||''
                    },body,signal:controller.signal};
                    let data;
                    if(typeof remaskPrivateTargetingScope==='function'){
                        data=await apiJson('ajax/metaTemplateTargetingSearch.php',options);
                    }else{
                        const response=await fetch('ajax/metaTemplateTargetingSearch.php',options);
                        const payload=JSON.parse(await response.text());
                        if(!response.ok||payload.ok!==true)throw new Error(payload.error?.message||'Справочник Meta временно недоступен.');
                        data=payload.data||{};
                    }
                    if(run!==generation||!source.isConnected)return;
                    const available=Array.isArray(data?.data)?data.data:[];
                    const selected=new Set(rowsFor(section).map(key));
                    const rows=available.filter(row=>!selected.has(String(row.id??row.key)));
                    for(const row of rows.slice(0,100)){
                        const detail=Array.isArray(row.path)?row.path.join(' › '):'';
                        results.appendChild(button(String(row.name||row.id)+(detail?' · '+detail:''),'rm-module-choice',()=>choose(row)));
                    }
                    search.setAttribute('aria-expanded',results.children.length?'true':'false');
                    status.textContent=Boolean(data?.truncated)||rows.length>100?'Уточните поиск, чтобы увидеть остальные совпадения.':rows.length?'':'Нет подходящих вариантов.';
                }catch(error){
                    if(error.name==='AbortError'||run!==generation)return;
                    status.textContent=error.message||'Не удалось загрузить варианты Meta.';
                }
            }
            search.addEventListener('focus',()=>{clearTimeout(timer);timer=setTimeout(browse,180);});
            search.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();generation++;timer=setTimeout(browse,220);});
            search.addEventListener('blur',()=>clearTimeout(timer));
            search.addEventListener('keydown',event=>{
                if(event.key==='Enter'){event.preventDefault();browse();}
                if(event.key==='ArrowDown'){const first=results.querySelector('button');if(first){event.preventDefault();first.focus();}}
                if(event.key==='Escape'){clearTimeout(timer);controller?.abort();generation++;results.replaceChildren();status.textContent='';search.setAttribute('aria-expanded','false');}
            });
        }
        const valueDescriptor=Object.getOwnPropertyDescriptor(source,'value');
        if(valueDescriptor?.configurable){
            Object.defineProperty(source,'value',{configurable:true,get(){return valueDescriptor.get.call(this);},set(value){valueDescriptor.set.call(this,value);paint();}});
        }
        source.addEventListener('change',paint);source.after(root);paint();
    }
    function attach(source, spec, csv=false) {
        if(spec?.kind==='targeting_exclusions'){attachTargetingExclusions(source);return;}
        if(source.dataset.remaskModuleControl==='1')return;
        source.dataset.remaskModuleControl='1';source.classList.add('rm-module-source');
        source.closest('.rm-meta-field,.cr-sdk-field')?.classList.add('rm-module-field');
        const root=document.createElement('div');root.className='rm-module-control';
        const search=document.createElement('input');search.type='text';search.className='form-control';
        search.id=source.id+'_choices';search.placeholder='Выберите из списка или найдите по названию';
        search.autocomplete='off';search.setAttribute('role','combobox');search.setAttribute('aria-expanded','false');
        const pills=document.createElement('div');pills.className='rm-module-pills';
        const results=document.createElement('div');results.className='rm-module-results';results.id=search.id+'_results';
        results.setAttribute('role','group');search.setAttribute('aria-controls',results.id);
        const status=document.createElement('div');status.className='rm-module-hint';status.setAttribute('role','status');
        root.append(search,pills,results,status);source.after(root);
        for(const label of fieldLabels(source))label.htmlFor=search.id;
        // Original serializers and bundle hydration keep writing this exact
        // element. Observe its native value setter rather than duplicating state.
        const proto=source instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const descriptor=Object.getOwnPropertyDescriptor(proto,'value');
        let controller, timer=0, generation=0;
        function values() {
            const raw=String(descriptor.get.call(source)||'').trim();if(!raw)return [];
            if(csv)return raw.split(',').map(v=>v.trim()).filter(Boolean);
            try{const rows=JSON.parse(raw);if(Array.isArray(rows))return rows;}catch{}
            const type=source.dataset.launchMetaType||source.dataset.metaType||'';
            if(type==='list<string>'||type==='list<enum>')return raw.split(',').map(v=>v.trim()).filter(Boolean);
            return [];
        }
        function label(value) {
            const id=key(value);return (typeof value==='object'&&value.name)||spec.options?.find(row=>key(row)===id)?.name||id;
        }
        function paint() {
            pills.replaceChildren();
            for(const value of values()){
                const pill=button(label(value)+' ×','rm-module-pill',()=>commit(values().filter(row=>key(row)!==key(value))));
                pill.setAttribute('aria-label','Удалить '+label(value));pills.appendChild(pill);
            }
        }
        Object.defineProperty(source,'value',{configurable:true,get(){return descriptor.get.call(this);},set(value){descriptor.set.call(this,value);paint();}});
        function commit(rows) {
            source.value=rows.length?(csv?rows.join(','):JSON.stringify(rows)):'';
            edited(source);
            source.dispatchEvent(new Event('input',{bubbles:true}));source.dispatchEvent(new Event('change',{bubbles:true}));
        }
        function choose(row) {
            let value;
            if(spec.representation==='numbers'){
                value=Number(row.value??row.id);if(!Number.isSafeInteger(value)||value<1)return;
            }else if(spec.representation==='strings')value=String(row.value??row.id);
            else value={id:String(row.id),name:String(row.name||row.id)};
            const existing=values();if(!existing.some(item=>key(item)===key(value)))commit([...existing,value]);
            controller?.abort();generation++;search.value='';results.replaceChildren();status.textContent='';search.setAttribute('aria-expanded','false');
        }
        async function browse() {
            clearTimeout(timer);controller?.abort();const run=++generation;controller=new AbortController();
            const q=search.value.trim();let rows=spec.options||[],truncated=false;
            status.textContent='';results.replaceChildren();
            try{
                if(spec.kind==='lookup'){
                    status.textContent='Загружаю варианты…';
                    const body=new URLSearchParams({type:spec.lookup,q,limit:'100'}).toString();
                    const options={method:'POST',headers:{
                        'Content-Type':'application/x-www-form-urlencoded',
                        'X-ReMask-CSRF':document.querySelector('meta[name="remask-csrf"]')?.content||''
                    },body,signal:controller.signal};
                    let data;
                    if(typeof remaskPrivateTargetingScope==='function'){
                        data=await apiJson('ajax/metaTemplateTargetingSearch.php',options);
                    }else{
                        const response=await fetch('ajax/metaTemplateTargetingSearch.php',options);
                        const payload=JSON.parse(await response.text());
                        if(!response.ok||payload.ok!==true)throw new Error(payload.error?.message||'Справочник Meta временно недоступен.');
                        data=payload.data||{};
                    }
                    rows=Array.isArray(data?.data)?data.data:[];truncated=Boolean(data?.truncated);
                }else{
                    const needle=q.toLocaleLowerCase();rows=rows.filter(row=>(String(row.name)+' '+String(row.id)).toLocaleLowerCase().includes(needle));
                }
                if(run!==generation||!source.isConnected)return;
                const selected=new Set(values().map(key));rows=rows.filter(row=>!selected.has(key(row)));
                for(const row of rows.slice(0,100)){
                    const detail=Array.isArray(row.path)?row.path.join(' › '):'';
                    const choice=button(String(row.name||row.id)+(detail?' · '+detail:''),'rm-module-choice',()=>choose(row));
                    results.appendChild(choice);
                }
                search.setAttribute('aria-expanded',results.children.length?'true':'false');
                status.textContent=rows.length>100||truncated?'Уточните название для остальных вариантов.':rows.length?'':'Нет подходящих вариантов.';
            }catch(error){
                if(error.name==='AbortError'||run!==generation)return;
                status.textContent=error.message||'Не удалось загрузить варианты.';
            }
        }
        search.addEventListener('focus',()=>{clearTimeout(timer);timer=setTimeout(browse,240);});
        search.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();generation++;timer=setTimeout(browse,240);});
        search.addEventListener('blur',()=>clearTimeout(timer));
        search.addEventListener('keydown',event=>{
            if(event.key==='Enter'){event.preventDefault();browse();}
            if(event.key==='ArrowDown'){const first=results.querySelector('button');if(first){event.preventDefault();first.focus();}}
            if(event.key==='Escape'){clearTimeout(timer);controller?.abort();generation++;results.replaceChildren();status.textContent='';search.setAttribute('aria-expanded','false');}
        });
        source.addEventListener('change',paint);paint();
    }
    const shapes = {asset_feed_spec:'AdsAPIAdAssetFeedFields',promoted_object:'AdsAPIPromotedObjectFields',
        adset_schedule:'AdsApiScheduleObjectFields',
        bid_constraints:'AdsAPIAdCampaignGroupBidConstraintsFields',creative_features_spec:'AdCreativeFeatureCustomizationsSpecFields'};
    const primaryComplex = {mbAttributionSpec:['adset','attribution_spec'],mbPromotedObject:['adset','promoted_object'],
        mbFlexibleSpec:['targeting','flexible_spec'],mbExclusions:['targeting','exclusions'],mbDegreesOfFreedom:['creative','degrees_of_freedom_spec'],
        mbAssetFeedSpec:['creative','asset_feed_spec'],mbPlatformCustomizations:['creative','platform_customizations'],mbTrackingSpecs:['ad','tracking_specs'],
        mbAdvancedCampaign:['campaign','campaign'],mbAdvancedAdset:['adset','adset'],mbAdvancedTargeting:['targeting','targeting'],mbAdvancedCreative:['creative','creative'],mbAdvancedAd:['ad','ad']};
    const listFields = new Set(['attribution_spec','flexible_spec','adlabels','execution_options','frequency_control_specs','adset_schedule','tracking_specs','ad_schedule','budget_schedule_specs']);
    function attachStructure(source, field, type) {
        if(source.dataset.remaskModuleControl==='1')return;
        source.dataset.remaskModuleControl='1';source.classList.add('rm-module-source');
        source.closest('.rm-meta-field,.cr-sdk-field')?.classList.add('rm-module-field');
        const root=document.createElement('div');root.className='rm-module-structure';
        const summary=document.createElement('span');summary.className='rm-module-hint';
        const configure=button('Настроить','btn btn-secondary',open);
        const clear=button('Очистить','btn btn-secondary',()=>{source.value='';edited(source);source.dispatchEvent(new Event('change',{bubbles:true}));});
        root.append(configure,clear,summary);source.after(root);
        const descriptor=Object.getOwnPropertyDescriptor(source instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value');
        const empty=()=>type.startsWith('list<')||listFields.has(field)?[]:{};
        function read(){const raw=descriptor.get.call(source).trim();return raw?JSON.parse(raw):empty();}
        function paint(){try{const value=read();summary.textContent=Array.isArray(value)?value.length+' элементов':value&&typeof value==='object'?Object.keys(value).length+' параметров':String(value);}catch{summary.textContent='Проверьте сохранённое значение';}}
        Object.defineProperty(source,'value',{configurable:true,get(){return descriptor.get.call(this);},set(value){descriptor.set.call(this,value);paint();}});
        source.addEventListener('change',paint);paint();
        function open(){
            let draft;try{draft=structuredClone(read());}catch{summary.textContent='Сохранённое значение не удалось разобрать.';return;}
            const overlay=document.createElement('div');overlay.style.cssText='position:fixed;inset:0;z-index:14000;background:#020617bf;display:flex;justify-content:center;align-items:center;padding:12px';
            const dialog=document.createElement('section');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');
            const fieldLabel=fieldLabels(source)[0];
            const title=(fieldLabel?.querySelector('span')?.textContent||fieldLabel?.textContent||field).trim();dialog.setAttribute('aria-label',title);
            dialog.style.cssText='width:min(900px,100%);max-height:90vh;overflow:auto;background:#111827;border:1px solid #465366;border-radius:14px;padding:18px;color:#e2e8f0';
            const heading=document.createElement('h3');heading.textContent=title;dialog.appendChild(heading);
            const body=document.createElement('div');dialog.appendChild(body);
            const error=document.createElement('div');error.setAttribute('role','alert');dialog.appendChild(error);
            const close=()=>{overlay.remove();configure.focus();};
            const cancel=button('Отмена','btn btn-secondary',close);
            const save=button('Сохранить','btn btn-primary',()=>{
                try{source.value=JSON.stringify(draft);edited(source);source.dispatchEvent(new Event('input',{bubbles:true}));source.dispatchEvent(new Event('change',{bubbles:true}));close();}catch{error.textContent='Не удалось сохранить настройки.';}
            });dialog.append(cancel,save);overlay.appendChild(dialog);document.body.appendChild(overlay);
            let count=0;
            const kind=value=>value===null?'null':Array.isArray(value)?'array':typeof value==='object'?'object':typeof value==='boolean'?'boolean':typeof value==='number'?'number':'string';
            const initial={string:'',number:0,boolean:false,object:{},array:[],null:null};
            function render(){body.replaceChildren();count=0;tree(body,draft,value=>{draft=value;},field,0);}
            function tree(parent,value,setValue,path,depth){
                if(++count>600||depth>20){error.textContent='Слишком много вложенных параметров для редактора.';save.disabled=true;return;}
                const row=document.createElement('div');row.style.cssText='margin:7px 0;padding:8px;border:1px solid #334155;border-radius:8px';
                const select=document.createElement('select');select.className='form-control';select.style.cssText='width:auto;display:inline-block;margin-bottom:5px';
                for(const [type,label] of [['object','Параметры'],['array','Список'],['string','Текст'],['number','Число'],['boolean','Да / Нет'],['null','Без значения']])select.appendChild(new Option(label,type));
                select.value=kind(value);select.setAttribute('aria-label','Тип значения '+path);row.appendChild(select);parent.appendChild(row);
                select.addEventListener('change',()=>{setValue(structuredClone(initial[select.value]));render();});
                if(kind(value)==='object'||kind(value)==='array'){
                    const array=Array.isArray(value);const children=document.createElement('div');children.style.marginLeft='10px';row.appendChild(children);
                    for(const [name,item] of Object.entries(value)){
                        const card=document.createElement('div');const label=document.createElement('strong');label.textContent=array?'Элемент '+(Number(name)+1):name;card.appendChild(label);
                        card.appendChild(button('Удалить','btn btn-secondary',()=>{if(array)value.splice(Number(name),1);else delete value[name];render();}));children.appendChild(card);
                        tree(card,item,next=>{value[name]=next;},name,depth+1);
                    }
                    let name;
                    if(!array){
                        const options=metadata.object_definitions?.[shapes[path]]||(metadata.schema?.[path]?.fields?Object.keys(metadata.schema[path].fields):null);
                        if(options?.length){name=document.createElement('select');name.className='form-control';name.appendChild(new Option('Выберите параметр',''));for(const key of options.filter(key=>!Object.hasOwn(value,key)))name.appendChild(new Option(key,key));}
                        else{name=document.createElement('input');name.className='form-control';name.placeholder='Название параметра';}
                        name.setAttribute('aria-label','Добавляемый параметр '+path);row.appendChild(name);
                    }
                    row.appendChild(button(array?'Добавить элемент':'Добавить параметр','btn btn-primary',()=>{
                        if(array)value.push('');else{
                            const key=name.value.trim();if(!key||['__proto__','prototype','constructor'].includes(key)||Object.hasOwn(value,key)){error.textContent='Выберите новый параметр.';return;}
                            Object.defineProperty(value,key,{value:'',writable:true,enumerable:true,configurable:true});
                        }
                        error.textContent='';render();
                    }));
                }else if(kind(value)==='boolean'){
                    const input=document.createElement('select');input.className='form-control';input.append(new Option('Да','true'),new Option('Нет','false'));input.value=String(value);input.setAttribute('aria-label','Значение '+path);input.addEventListener('change',()=>setValue(input.value==='true'));row.appendChild(input);
                }else if(value!==null){
                    const input=document.createElement('input');input.className='form-control';input.type=kind(value)==='number'?'number':'text';input.value=String(value);input.setAttribute('aria-label','Значение '+path);
                    input.addEventListener('input',()=>{if(input.type==='number'){const number=Number(input.value);if(Number.isFinite(number))setValue(number);}else setValue(input.value);});row.appendChild(input);
                }
            }
            dialog.addEventListener('keydown',event=>{
                if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}
                if(event.key==='Tab'){
                    const nodes=Array.from(dialog.querySelectorAll('button,input,select')).filter(node=>!node.disabled);
                    const first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
                }
            });
            render();dialog.querySelector('select,input,button')?.focus();
        }
    }

    function choiceLabels(source){const search=document.getElementById(source.id+'_choices');if(search)for(const label of fieldLabels(source))label.htmlFor=search.id;}
    function scan() {
        for(const source of document.querySelectorAll('[data-meta-group][data-meta-field],[data-launch-meta-group][data-launch-meta-field]')){
            const group=source.dataset.metaGroup||source.dataset.launchMetaGroup;
            const field=source.dataset.metaField||source.dataset.launchMetaField;
            let spec=metadata.field_controls?.[group]?.[field];
            if(!spec && String(source.dataset.metaType||source.dataset.launchMetaType)==='list<enum>'){
                const options=metadata.schema?.[group]?.enums?.[field];
                if(options?.length)spec={kind:'choices',representation:'strings',options:options.map(value=>({id:value,name:value,value}))};
            }
            if(spec){attach(source,spec);choiceLabels(source);}
            else {const type=String(source.dataset.metaType||source.dataset.launchMetaType||'');if(type==='JSON'||type.startsWith('list<')||type.includes('Object')||type.includes('map'))attachStructure(source,field,type);}
        }
        for(const [id,[group,field,format]] of Object.entries(primary)){
            const source=document.getElementById(id),spec=metadata.field_controls?.[group]?.[field];
            if(source&&spec){attach(source,spec,format==='csv');choiceLabels(source);}
        }
        for(const [id,[group,field]] of Object.entries(primaryComplex)){
            const source=document.getElementById(id);if(!source||!metadata.schema)continue;
            const spec=metadata.field_controls?.[group]?.[field];
            if(spec?.kind==='targeting_exclusions'){attach(source,spec);choiceLabels(source);}
            else attachStructure(source,field,String(metadata.schema[group]?.fields?.[field]||'JSON'));
        }
        for(const [id,[group,field]] of Object.entries(primaryEnums)){
            const select=document.getElementById(id),options=metadata.schema?.[group]?.enums?.[field];
            if(!select||!options?.length||select.dataset.remaskModuleEnum==='1')continue;
            const saved=select.value;select.replaceChildren(...options.map(value=>new Option(value,value)));
            if(options.includes(saved))select.value=saved;select.dataset.remaskModuleEnum='1';
        }
    }
    function queue() {if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;scan();});}
    new MutationObserver(queue).observe(document.body,{childList:true,subtree:true});
    return {configure(data){metadata=data||{};queue();},isTargetingType(type){return Object.hasOwn(metadata.targeting_types||{},type);},scan};
})();
