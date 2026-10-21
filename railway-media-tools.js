/* Local managed-media tools shared by Creative Library and Auto Launch. */
(() => {
    'use strict';
    async function request(action, payload) {
        const csrf=document.querySelector('meta[name="remask-csrf"]')?.content||'';
        const response=await fetch('ajax/pythonWorkerJobs.php', {method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-ReMask-CSRF':csrf},
            body:JSON.stringify({action,...payload})});
        const data=await response.json();
        if(!response.ok||data.ok!==true) {
            const detail=data.error||data.detail||{};
            throw new Error(typeof detail==='string'?detail:(detail.code||'MEDIA_ERROR')+': '+(detail.message||'Не удалось проверить медиа'));
        }
        return data;
    }
    async function stage(file) {
        const csrf=document.querySelector('meta[name="remask-csrf"]')?.content||'';
        const body=new FormData();body.append('action','private_launch_media');body.append('media',file,file.name);
        const response=await fetch('ajax/pythonWorkerJobs.php',{method:'POST',credentials:'same-origin',
            headers:{'X-ReMask-CSRF':csrf},body});
        const data=await response.json();
        if(!response.ok||!/^m-[a-f0-9]{24}$/.test(data.media?.id||''))throw new Error(data.error||'MEDIA_UPLOAD_FAILED');
        return data.media;
    }
    function install(anchor, captureSelection, selectPrepared) {
        if(!anchor||document.getElementById('remaskMediaTools'))return;
        const panel=document.createElement('fieldset');panel.id='remaskMediaTools';
        const legend=document.createElement('legend');legend.textContent='Проверка и подготовка медиа';panel.appendChild(legend);
        const note=document.createElement('p');note.textContent='Оригинал сохраняется. Crop обрезает по центру; Pad добавляет выбранный фон. Подготовленный вариант применяется отдельно. Приём файла в Meta подтверждается отдельно после создания черновика.';panel.appendChild(note);
        const controls={};
        function field(name,label,type,values) {
            const wrapper=document.createElement('label');wrapper.textContent=label+' ';
            const input=document.createElement(type==='select'?'select':'input');input.id='remaskMedia'+name;
            if(type==='select')for(const [value,text] of values){const option=document.createElement('option');option.value=value;option.textContent=text;input.appendChild(option);}
            else {input.type=type;input.value=values;}
            wrapper.appendChild(input);panel.appendChild(wrapper);controls[name]=input;return input;
        }
        field('Card','Файл / карточка','select',[]);
        field('Encoding','Формат','select',[['PNG','PNG'],['JPEG','JPEG'],['MP4','MP4 H.264/AAC']]);
        field('Mode','Адаптация','select',[['fit','Fit: без увеличения'],['crop','Crop: обрезать по центру'],['pad','Pad: добавить фон']]);
        field('Width','Ширина','number','1080');field('Height','Высота','number','1080');
        field('Quality','Качество','number','85');field('Background','Фон','color','#ffffff');
        const status=document.createElement('pre');status.style.whiteSpace='pre-wrap';status.setAttribute('role','status');
        const preview=document.createElement('div');
        let captured=null, pending=null, busy=false;
        const options=()=>({encoding:controls.Encoding.value,mode:controls.Mode.value,
            width:Number(controls.Width.value),height:Number(controls.Height.value),
            quality:Number(controls.Quality.value),background:controls.Background.value});
        const buttons=[];
        function button(id,label,action){const b=document.createElement('button');b.type='button';b.id=id;b.textContent=label;
            b.addEventListener('click',async()=>{if(busy)return;busy=true;buttons.forEach(x=>x.disabled=true);
                try{await action();}catch(error){pending=null;status.textContent=error.message;}
                finally{busy=false;buttons.forEach(x=>x.disabled=false);}});panel.appendChild(b);buttons.push(b);return b;}
        button('remaskMediaInspect','Проверить файлы',async()=>{
            pending=null;preview.replaceChildren();captured=await captureSelection();
            const data=await request('private_launch_media_inspect',{creative:captured.creative,targeting:captured.targeting||{}});
            captured.assertCurrent();captured.media=data.media;
            controls.Card.replaceChildren();
            for(const [i,row] of data.media.entries()){const o=document.createElement('option');o.value=String(i);o.textContent=String(i+1)+' · '+row.id;controls.Card.appendChild(o);}
            status.textContent=data.media.map((row,i)=>{
                const f=row.inspection;
                return `${i+1}: ${f.encoding} ${f.width}×${f.height}, ${f.size_bytes} bytes`+
                    (f.duration_seconds?`, ${f.duration_seconds}s, ${f.video_codec}`:'')+
                    (f.requires_conversion?' · требуется подготовка':' · файл декодирован')+
                    (row.compatibility.warnings||[]).map(w=>'\n'+w.placement+': рекомендуется проверить Crop/Pad; это не отказ Meta').join('');
            }).join('\n');
        });
        button('remaskMediaPrepare','Подготовить вариант',async()=>{
            pending=null;if(!captured)throw new Error('Сначала проверьте файлы');captured.assertCurrent();
            const index=Number(controls.Card.value), original=captured.media[index];
            if(!original)throw new Error('Выберите файл');
            const selectedOptions=options();
            const result=await request('private_launch_media_prepare',{reference:{id:original.id,sha256:original.sha256},options:selectedOptions});
            captured.assertCurrent();
            pending={result,index,options:JSON.stringify(selectedOptions),captured};
            preview.replaceChildren();
            const element=document.createElement(result.media.media_type==='video'?'video':'img');
            element.src='ajax/pythonWorkerJobs.php?action=private_launch_media_preview&id='+encodeURIComponent(result.media.id);
            element.style.maxWidth='320px';element.style.maxHeight='400px';element.controls=true;element.alt='Подготовленный вариант';preview.appendChild(element);
            status.textContent='Вариант готов: '+result.media.id+' · '+result.inspection.width+'×'+result.inspection.height+'. Оригинал сохранён. Нажмите «Использовать вариант», чтобы изменить выбор.';
        });
        button('remaskMediaUse','Использовать вариант',async()=>{
            if(!pending)throw new Error('Сначала подготовьте вариант');
            pending.captured.assertCurrent();
            if(Number(controls.Card.value)!==pending.index||JSON.stringify(options())!==pending.options)
                throw new Error('Настройки подготовки изменились. Подготовьте новый вариант');
            await selectPrepared(pending.result.media,pending.index,pending.captured);
            pending=null;captured=null;status.textContent='Выбран подготовленный вариант. Повторите Review перед запуском.';
        });
        panel.appendChild(status);panel.appendChild(preview);anchor.insertAdjacentElement('afterend',panel);
    }
    globalThis.remaskMediaTools={request,stage,install};
})();
