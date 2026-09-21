<?php
/**
 * v114: Functional visual editors for every complex writable Meta SDK field in Launch.
 * Complex list/map/object fields no longer require typing raw JSON: each gets
 * Configure / Clear controls and a modal row/card editor that serializes back
 * to the existing Meta builder value consumed by Review and Job.
 */
$root='/var/www/html';
$phpPath=$root.'/launch.php';
$jsPath=$root.'/scripts/launch.js';

if(!is_file($phpPath)||!is_file($jsPath)){
    fwrite(STDERR,"[meta-editors-v114] runtime files missing\n");
    exit(421);
}
$php=file_get_contents($phpPath);
$js=file_get_contents($jsPath);
if($php===false||$js===false){
    fwrite(STDERR,"[meta-editors-v114] read failed\n");
    exit(422);
}

if(strpos($php,'REMASK_META_VISUAL_EDITORS_V1')===false){
$modal=<<<'HTML'
<!-- REMASK_META_VISUAL_EDITORS_V1 -->
<style>
.rm-meta-visual-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.rm-meta-visual-actions .btn{min-height:36px}
.rm-meta-visual-summary{font-size:12px;opacity:.72;min-width:80px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rm-meta-visual-source{display:none!important}
#rmMetaVisualModal{position:fixed;inset:0;z-index:12000;background:rgba(2,6,23,.72);display:none;align-items:center;justify-content:center;padding:18px}
#rmMetaVisualModal.open{display:flex}
#rmMetaVisualModal .rm-meta-modal-card{width:min(920px,100%);max-height:90vh;overflow:hidden;display:flex;flex-direction:column;border:1px solid rgba(148,163,184,.28);border-radius:16px;background:#111827;box-shadow:0 28px 80px rgba(0,0,0,.48)}
#rmMetaVisualModal .rm-meta-modal-head,#rmMetaVisualModal .rm-meta-modal-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid rgba(148,163,184,.18)}
#rmMetaVisualModal .rm-meta-modal-foot{border-bottom:0;border-top:1px solid rgba(148,163,184,.18);justify-content:flex-end}
#rmMetaVisualModal .rm-meta-modal-body{padding:14px 16px;overflow:auto}
#rmMetaVisualModal .rm-meta-row{display:grid;grid-template-columns:minmax(120px,1fr) minmax(160px,1.6fr) auto;gap:8px;align-items:start;margin-bottom:8px}
#rmMetaVisualModal .rm-meta-kv-row{display:grid;grid-template-columns:minmax(130px,1fr) 118px minmax(180px,1.5fr) auto;gap:8px;align-items:start;margin-bottom:8px}
#rmMetaVisualModal .rm-meta-object-card{border:1px solid rgba(148,163,184,.20);border-radius:12px;padding:10px;margin-bottom:10px}
#rmMetaVisualModal .rm-meta-object-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px}
#rmMetaVisualModal .rm-meta-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
#rmMetaVisualModal .rm-meta-empty{opacity:.65;padding:12px 0}
#rmMetaVisualModal textarea.form-control{min-height:80px;resize:vertical}
#rmMetaVisualModal .rm-meta-idname-row{display:grid;grid-template-columns:minmax(140px,1fr) minmax(180px,1.4fr) auto;gap:8px;margin-bottom:8px}
@media(max-width:700px){
  #rmMetaVisualModal{padding:8px}
  #rmMetaVisualModal .rm-meta-row,
  #rmMetaVisualModal .rm-meta-kv-row,
  #rmMetaVisualModal .rm-meta-idname-row{grid-template-columns:1fr}
}
</style>
<div id="rmMetaVisualModal" aria-hidden="true">
  <div class="rm-meta-modal-card" role="dialog" aria-modal="true" aria-labelledby="rmMetaVisualTitle">
    <div class="rm-meta-modal-head">
      <div>
        <div id="rmMetaVisualTitle" style="font-weight:800">Meta field</div>
        <div id="rmMetaVisualType" class="muted" style="font-size:12px"></div>
      </div>
      <button id="rmMetaVisualClose" class="btn btn-secondary" type="button">Закрыть</button>
    </div>
    <div id="rmMetaVisualBody" class="rm-meta-modal-body"></div>
    <div class="rm-meta-modal-foot">
      <button id="rmMetaVisualRaw" class="btn btn-secondary" type="button">Raw JSON</button>
      <button id="rmMetaVisualCancel" class="btn btn-secondary" type="button">Отмена</button>
      <button id="rmMetaVisualSave" class="btn btn-primary" type="button">Сохранить</button>
    </div>
  </div>
</div>
HTML;
    $pattern='#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#';
    if(!preg_match($pattern,$php)){
        fwrite(STDERR,"[meta-editors-v114] launch script tag missing\n");
        exit(423);
    }
    $php=preg_replace($pattern,$modal."\n".'$0',$php,1,$n) ?? $php;
    if($n!==1){
        fwrite(STDERR,"[meta-editors-v114] modal insert count=$n\n");
        exit(424);
    }
}

if(strpos($js,'REMASK_META_VISUAL_EDITORS_V1')===false){
$addon=<<<'JS'

/* REMASK_META_VISUAL_EDITORS_V1 */
let rmMetaVisualInput=null;
let rmMetaVisualMode='visual';
let rmMetaVisualDraft=undefined;

function rmMetaEl(id){ return document.getElementById(id); }
function rmMetaComplexType(type){
    type=String(type||'');
    return type==='Object'||type==='map'||type.startsWith('map<')||type.startsWith('list<');
}
function rmMetaParseExisting(input){
    const raw=String(input?.value||'').trim();
    if(!raw) {
        const type=String(input?.dataset.launchMetaType||'');
        return type.startsWith('list<') ? [] : {};
    }
    try { return JSON.parse(raw); }
    catch {
        const type=String(input?.dataset.launchMetaType||'');
        if(type==='list<string>'||type==='list<enum>') return raw.split(',').map(v=>v.trim()).filter(Boolean);
        if(type==='list<unsigned int>') return raw.split(',').map(v=>Number(v.trim())).filter(Number.isInteger);
        return type.startsWith('list<') ? [] : {};
    }
}
function rmMetaClone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch { return value; }
}
function rmMetaSummary(input){
    const type=String(input?.dataset.launchMetaType||'');
    const value=rmMetaParseExisting(input);
    if(Array.isArray(value)) return value.length ? value.length+' элементов' : 'Не настроено';
    if(value&&typeof value==='object') {
        const count=Object.keys(value).length;
        return count ? count+' параметров' : 'Не настроено';
    }
    return String(value||'Не настроено');
}
function rmMetaRefreshSummary(input){
    const wrap=input?.closest('.rm-meta-field');
    const summary=wrap?.querySelector('.rm-meta-visual-summary');
    if(summary) {
        summary.textContent=rmMetaSummary(input);
        summary.title=String(input.value||'');
    }
}
function rmMetaTypeValue(raw,kind){
    if(kind==='number'){
        const n=Number(raw);
        if(!Number.isFinite(n)) throw new Error('Введите корректное число.');
        return n;
    }
    if(kind==='bool') return raw==='true';
    if(kind==='json'){
        try { return JSON.parse(raw); } catch { throw new Error('В одном из значений невалидный JSON.'); }
    }
    return String(raw??'');
}
function rmMetaValueKind(value){
    if(typeof value==='boolean') return 'bool';
    if(typeof value==='number') return 'number';
    if(value&&typeof value==='object') return 'json';
    return 'string';
}
function rmMetaValueText(value,kind){
    if(kind==='json') return JSON.stringify(value??{});
    if(kind==='bool') return value===true?'true':'false';
    return value===undefined||value===null?'':String(value);
}
function rmMetaButton(text,cls='btn btn-secondary'){
    const b=document.createElement('button');
    b.type='button'; b.className=cls; b.textContent=text;
    return b;
}
function rmMetaInput(value='',type='text'){
    const i=document.createElement('input');
    i.className='form-control'; i.type=type; i.value=value??'';
    return i;
}
function rmMetaSelect(options,value=''){
    const s=document.createElement('select'); s.className='form-control';
    for(const [v,label] of options){ s.appendChild(new Option(label,v)); }
    s.value=String(value??'');
    return s;
}
function rmMetaRemoveRowButton(){
    const b=rmMetaButton('Удалить','btn btn-secondary');
    b.addEventListener('click',()=>b.closest('.rm-meta-row,.rm-meta-kv-row,.rm-meta-idname-row,.rm-meta-object-card')?.remove());
    return b;
}
function rmMetaRenderPrimitiveList(body,input,type,value){
    const field=input.dataset.launchMetaField||'';
    const enumValues=remaskLaunchMetaSchema?.[input.dataset.launchMetaGroup]?.enums?.[field]||[];
    const list=Array.isArray(value)?value:[];
    const rows=document.createElement('div'); rows.dataset.rmList='1'; body.appendChild(rows);
    const add=()=>{
        const row=document.createElement('div'); row.className='rm-meta-row';
        let editor;
        if(type==='list<enum>'&&enumValues.length){
            editor=rmMetaSelect(enumValues.map(v=>[v,v]),'');
        } else {
            editor=rmMetaInput('',type==='list<unsigned int>'?'number':'text');
        }
        editor.dataset.rmValue='1';
        row.appendChild(editor);
        const spacer=document.createElement('div'); spacer.className='muted'; spacer.textContent='';
        row.appendChild(spacer);
        row.appendChild(rmMetaRemoveRowButton());
        rows.appendChild(row);
        return editor;
    };
    for(const item of list){ const e=add(); e.value=String(item); }
    if(!list.length) rows.innerHTML='<div class="rm-meta-empty">Список пуст.</div>';
    const toolbar=document.createElement('div'); toolbar.className='rm-meta-toolbar';
    const addBtn=rmMetaButton('Добавить значение','btn btn-primary');
    addBtn.addEventListener('click',()=>{ rows.querySelector('.rm-meta-empty')?.remove(); add().focus(); });
    toolbar.appendChild(addBtn); body.appendChild(toolbar);
}
function rmMetaRenderIdNameList(body,value){
    const list=Array.isArray(value)?value:[];
    const rows=document.createElement('div'); rows.dataset.rmIdNameList='1'; body.appendChild(rows);
    const add=(item={})=>{
        const row=document.createElement('div'); row.className='rm-meta-idname-row';
        const id=rmMetaInput(item?.id||item?.key||''); id.placeholder='ID Meta'; id.dataset.rmId='1';
        const name=rmMetaInput(item?.name||''); name.placeholder='Название (для удобства)'; name.dataset.rmName='1';
        row.append(id,name,rmMetaRemoveRowButton()); rows.appendChild(row); return id;
    };
    for(const item of list) add(item);
    if(!list.length) rows.innerHTML='<div class="rm-meta-empty">Ничего не выбрано.</div>';
    const toolbar=document.createElement('div'); toolbar.className='rm-meta-toolbar';
    const addBtn=rmMetaButton('Добавить ID','btn btn-primary');
    addBtn.addEventListener('click',()=>{ rows.querySelector('.rm-meta-empty')?.remove(); add().focus(); });
    toolbar.appendChild(addBtn); body.appendChild(toolbar);
}
function rmMetaAddKvRow(container,key='',value=''){
    const row=document.createElement('div'); row.className='rm-meta-kv-row';
    const keyInput=rmMetaInput(key); keyInput.placeholder='Параметр'; keyInput.dataset.rmKey='1';
    const kind=rmMetaValueKind(value);
    const kindSelect=rmMetaSelect([['string','Текст'],['number','Число'],['bool','Да / Нет'],['json','Объект / массив']],kind);
    kindSelect.dataset.rmKind='1';
    let valueEl = kind==='json' ? document.createElement('textarea') : (kind==='bool' ? rmMetaSelect([['true','Да'],['false','Нет']],rmMetaValueText(value,kind)) : rmMetaInput(rmMetaValueText(value,kind),kind==='number'?'number':'text'));
    valueEl.className='form-control'; valueEl.dataset.rmValue='1';
    if(kind==='json') valueEl.value=rmMetaValueText(value,kind);
    const replaceValue=(nextKind)=>{
        const current=valueEl.value;
        const next=nextKind==='json'?document.createElement('textarea'):(nextKind==='bool'?rmMetaSelect([['true','Да'],['false','Нет']],current):rmMetaInput(current,nextKind==='number'?'number':'text'));
        next.className='form-control'; next.dataset.rmValue='1';
        valueEl.replaceWith(next); valueEl=next;
    };
    kindSelect.addEventListener('change',()=>replaceValue(kindSelect.value));
    row.append(keyInput,kindSelect,valueEl,rmMetaRemoveRowButton());
    container.appendChild(row);
    return keyInput;
}
function rmMetaRenderObject(body,value){
    const obj=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
    const rows=document.createElement('div'); rows.dataset.rmKv='1'; body.appendChild(rows);
    for(const [k,v] of Object.entries(obj)) rmMetaAddKvRow(rows,k,v);
    if(!Object.keys(obj).length) rows.innerHTML='<div class="rm-meta-empty">Параметры не добавлены.</div>';
    const toolbar=document.createElement('div'); toolbar.className='rm-meta-toolbar';
    const addBtn=rmMetaButton('Добавить параметр','btn btn-primary');
    addBtn.addEventListener('click',()=>{ rows.querySelector('.rm-meta-empty')?.remove(); rmMetaAddKvRow(rows).focus(); });
    toolbar.appendChild(addBtn); body.appendChild(toolbar);
}
function rmMetaRenderObjectList(body,value){
    const list=Array.isArray(value)?value:[];
    const cards=document.createElement('div'); cards.dataset.rmObjectList='1'; body.appendChild(cards);
    const add=(item={})=>{
        const card=document.createElement('div'); card.className='rm-meta-object-card';
        const head=document.createElement('div'); head.className='rm-meta-object-head';
        const title=document.createElement('strong'); title.textContent='Элемент';
        const remove=rmMetaButton('Удалить','btn btn-secondary'); remove.addEventListener('click',()=>card.remove());
        head.append(title,remove); card.appendChild(head);
        const rows=document.createElement('div'); rows.dataset.rmKv='1'; card.appendChild(rows);
        const obj=item&&typeof item==='object'&&!Array.isArray(item)?item:{};
        for(const [k,v] of Object.entries(obj)) rmMetaAddKvRow(rows,k,v);
        const addProp=rmMetaButton('Добавить параметр','btn btn-secondary');
        addProp.addEventListener('click',()=>rmMetaAddKvRow(rows).focus());
        card.appendChild(addProp); cards.appendChild(card); return card;
    };
    for(const item of list) add(item);
    if(!list.length) cards.innerHTML='<div class="rm-meta-empty">Список пуст.</div>';
    const toolbar=document.createElement('div'); toolbar.className='rm-meta-toolbar';
    const addBtn=rmMetaButton('Добавить элемент','btn btn-primary');
    addBtn.addEventListener('click',()=>{ cards.querySelector('.rm-meta-empty')?.remove(); add(); });
    toolbar.appendChild(addBtn); body.appendChild(toolbar);
}
function rmMetaReadKv(container){
    const out={};
    container.querySelectorAll(':scope > .rm-meta-kv-row').forEach(row=>{
        const key=String(row.querySelector('[data-rm-key]')?.value||'').trim();
        if(!key) return;
        const kind=row.querySelector('[data-rm-kind]')?.value||'string';
        const raw=row.querySelector('[data-rm-value]')?.value??'';
        out[key]=rmMetaTypeValue(raw,kind);
    });
    return out;
}
function rmMetaCollectVisual(input){
    const body=rmMetaEl('rmMetaVisualBody');
    const type=String(input.dataset.launchMetaType||'');
    if(type==='list<string>'||type==='list<unsigned int>'||type==='list<enum>'){
        return Array.from(body.querySelectorAll('[data-rm-list] > .rm-meta-row')).map(row=>{
            const raw=row.querySelector('[data-rm-value]')?.value??'';
            if(type==='list<unsigned int>'){
                const n=Number(raw); if(!Number.isInteger(n)) throw new Error('Список должен содержать целые числа.'); return n;
            }
            return String(raw).trim();
        }).filter(v=>v!==''&&v!==null);
    }
    if(type==='list<IDName>'){
        return Array.from(body.querySelectorAll('[data-rm-idname-list] > .rm-meta-idname-row')).map(row=>{
            const id=String(row.querySelector('[data-rm-id]')?.value||'').trim();
            const name=String(row.querySelector('[data-rm-name]')?.value||'').trim();
            return id ? (name?{id,name}:{id}) : null;
        }).filter(Boolean);
    }
    if(type==='list<Object>'||type==='list<map>'||type.startsWith('list<map')){
        return Array.from(body.querySelectorAll('[data-rm-object-list] > .rm-meta-object-card')).map(card=>rmMetaReadKv(card.querySelector('[data-rm-kv]')));
    }
    if(type==='Object'||type==='map'||type.startsWith('map<')){
        return rmMetaReadKv(body.querySelector('[data-rm-kv]'));
    }
    return rmMetaVisualDraft;
}
function rmMetaRenderRaw(body,input,value){
    const ta=document.createElement('textarea'); ta.className='form-control'; ta.id='rmMetaRawJson'; ta.style.minHeight='320px';
    ta.value=JSON.stringify(value,input.dataset.launchMetaType?.startsWith('list<')?null:null,2);
    body.appendChild(ta);
}
function rmMetaRenderVisual(){
    const input=rmMetaVisualInput; if(!input) return;
    const body=rmMetaEl('rmMetaVisualBody'); body.innerHTML='';
    const type=String(input.dataset.launchMetaType||'');
    const value=rmMetaClone(rmMetaVisualDraft);
    if(rmMetaVisualMode==='raw'){ rmMetaRenderRaw(body,input,value); return; }
    if(type==='list<string>'||type==='list<unsigned int>'||type==='list<enum>') return rmMetaRenderPrimitiveList(body,input,type,value);
    if(type==='list<IDName>') return rmMetaRenderIdNameList(body,value);
    if(type==='list<Object>'||type==='list<map>'||type.startsWith('list<map')) return rmMetaRenderObjectList(body,value);
    if(type==='Object'||type==='map'||type.startsWith('map<')) return rmMetaRenderObject(body,value);
    rmMetaRenderRaw(body,input,value);
}
function rmMetaOpenVisual(input){
    rmMetaVisualInput=input;
    rmMetaVisualMode='visual';
    rmMetaVisualDraft=rmMetaClone(rmMetaParseExisting(input));
    rmMetaEl('rmMetaVisualTitle').textContent=(input.dataset.launchMetaGroup||'').toUpperCase()+' · '+(input.dataset.launchMetaField||'');
    rmMetaEl('rmMetaVisualType').textContent=input.dataset.launchMetaType||'';
    rmMetaRenderVisual();
    const modal=rmMetaEl('rmMetaVisualModal'); modal.classList.add('open'); modal.setAttribute('aria-hidden','false');
}
function rmMetaCloseVisual(){
    const modal=rmMetaEl('rmMetaVisualModal'); modal?.classList.remove('open'); modal?.setAttribute('aria-hidden','true');
    rmMetaVisualInput=null; rmMetaVisualDraft=undefined;
}
function rmMetaSwitchMode(){
    if(!rmMetaVisualInput) return;
    if(rmMetaVisualMode==='visual'){
        try { rmMetaVisualDraft=rmMetaCollectVisual(rmMetaVisualInput); } catch(e){ alert(e.message); return; }
        rmMetaVisualMode='raw'; rmMetaEl('rmMetaVisualRaw').textContent='Визуальный редактор';
    } else {
        const raw=String(rmMetaEl('rmMetaRawJson')?.value||'').trim();
        try { rmMetaVisualDraft=raw?JSON.parse(raw):(String(rmMetaVisualInput.dataset.launchMetaType||'').startsWith('list<')?[]:{}); }
        catch { alert('Невалидный JSON.'); return; }
        rmMetaVisualMode='visual'; rmMetaEl('rmMetaVisualRaw').textContent='Raw JSON';
    }
    rmMetaRenderVisual();
}
function rmMetaSaveVisual(){
    if(!rmMetaVisualInput) return;
    try {
        let value;
        if(rmMetaVisualMode==='raw'){
            const raw=String(rmMetaEl('rmMetaRawJson')?.value||'').trim();
            value=raw?JSON.parse(raw):(String(rmMetaVisualInput.dataset.launchMetaType||'').startsWith('list<')?[]:{});
        } else value=rmMetaCollectVisual(rmMetaVisualInput);
        rmMetaVisualInput.value=JSON.stringify(value);
        rmMetaVisualInput.dispatchEvent(new Event('change',{bubbles:true}));
        rmMetaRefreshSummary(rmMetaVisualInput);
        rmMetaCloseVisual();
    } catch(e){ alert(e.message||String(e)); }
}
function rmMetaEnhanceComplexFields(){
    document.querySelectorAll('[data-launch-meta-group][data-launch-meta-field][data-launch-meta-type]').forEach(input=>{
        const type=String(input.dataset.launchMetaType||'');
        if(!rmMetaComplexType(type)||input.dataset.rmVisualEnhanced==='1') return;
        input.dataset.rmVisualEnhanced='1';
        input.classList.add('rm-meta-visual-source');
        const actions=document.createElement('div'); actions.className='rm-meta-visual-actions';
        const configure=rmMetaButton('Настроить','btn btn-primary');
        configure.dataset.rmMetaConfigure='1';
        configure.addEventListener('click',()=>rmMetaOpenVisual(input));
        const clear=rmMetaButton('Очистить','btn btn-secondary');
        clear.dataset.rmMetaClear='1';
        clear.addEventListener('click',()=>{
            input.value='';
            input.dispatchEvent(new Event('change',{bubbles:true}));
            rmMetaRefreshSummary(input);
        });
        const summary=document.createElement('span'); summary.className='rm-meta-visual-summary';
        actions.append(configure,clear,summary);
        input.insertAdjacentElement('afterend',actions);
        rmMetaRefreshSummary(input);
    });
}
const rmMetaObserver=new MutationObserver(()=>rmMetaEnhanceComplexFields());
const rmMetaRoot=rmMetaEl('launchMetaSdkFields');
if(rmMetaRoot) rmMetaObserver.observe(rmMetaRoot,{childList:true,subtree:true});
rmMetaEnhanceComplexFields();

rmMetaEl('rmMetaVisualClose')?.addEventListener('click',rmMetaCloseVisual);
rmMetaEl('rmMetaVisualCancel')?.addEventListener('click',rmMetaCloseVisual);
rmMetaEl('rmMetaVisualSave')?.addEventListener('click',rmMetaSaveVisual);
rmMetaEl('rmMetaVisualRaw')?.addEventListener('click',rmMetaSwitchMode);
rmMetaEl('rmMetaVisualModal')?.addEventListener('click',e=>{ if(e.target===rmMetaEl('rmMetaVisualModal')) rmMetaCloseVisual(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape'&&rmMetaEl('rmMetaVisualModal')?.classList.contains('open')) rmMetaCloseVisual(); });
JS;
    $js.="\n".$addon."\n";
}

$php=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260921-meta-editors-v114" type="module"></script>',
    $php,1,$cacheCount
) ?? $php;
if($cacheCount!==1){
    fwrite(STDERR,"[meta-editors-v114] cache-bust failed\n");
    exit(425);
}

file_put_contents($phpPath,$php);
file_put_contents($jsPath,$js);
fwrite(STDERR,"[meta-editors-v114] complex Meta fields now have functional visual editors\n");
