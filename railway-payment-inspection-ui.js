function remaskClearPaymentSecrets(){
  ['paymentCardNumber','paymentCardCvv'].forEach(id=>{const el=$(id);if(el)el.value='';});
}

function paymentCardMessage(result){
  const names={number:'номер карты',cvv:'CVV',holder:'имя владельца',expiry:'срок действия',month:'месяц',year:'год',country:'страна',address:'платёжный адрес',city:'город',region:'область / штат',postal_code:'индекс',unknown_required_field:'дополнительное поле Meta'};
  const messages={
    CARD_FORM_READY:'Форма Meta доступна для выбранного РК.',
    CARD_LINK_OBSERVED:'Карта привязана к выбранному РК. Платёжная проверка не выполнена.',
    ALREADY_LINKED:'Эта привязка ранее подтверждена. Для текущего состояния нажмите «Проверить привязанные карты».',
    CARD_LINK_NOT_VERIFIED:'Карта отправлена в Meta; привязка пока не подтверждена. Повторное добавление остановлено.',
    PAYMENT_ACCOUNT_SCOPE_UNVERIFIED:'Meta не подтвердила точный РК. Карта не отправлена.',
    PAYMENT_ACCOUNT_BINDING_MISSING:'Нет однозначного соответствия профиля и РК. Обновите выбранный РК.',
    PAYMENT_ACCOUNT_ROW_MISSING:'Meta не показала строку выбранного РК.',
    PAYMENT_ADD_CONTROL_MISSING:'У выбранного РК Meta не показала кнопку добавления способа оплаты.',
    PAYMENT_AD_ACCOUNT_DISABLED:'Meta отключила выбранный РК. Добавление карты остановлено до восстановления РК.',
    PAYMENT_ACCOUNT_SETUP_REQUIRED:'Meta сначала требует настройки страны, валюты и часового пояса РК. Эти настройки нельзя изменить после подтверждения. Карта не отправлена.',
    PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING:'Не удалось выбрать указанную настройку оплаты в форме Meta. Карта не отправлена.',
    PAYMENT_SETUP_INVALID:'Укажите Украина, USD и Europe/Kyiv для этой настройки оплаты.',
    PAYMENT_FORM_NOT_EXPOSED:'Meta не открыла форму карты.',
    CARD_BILLING_FIELDS_REQUIRED:'Нужны дополнительные реквизиты владельца или платёжного адреса.',
    CARD_BANK_CONFIRMATION_REQUIRED:'Требуется подтверждение банка. Повторная отправка остановлена.',
    PAYMENT_FINANCIAL_ACTION_REQUIRED:'Meta требует платёжное действие; автоматическое списание остановлено.',
    PAYMENT_TERMS_CONFIRMATION_REQUIRED:'Meta требует принятия условий. Нужное действие должно быть подтверждено пользователем.',
    CARD_SAVE_CONTROL_UNAVAILABLE:'Meta не показала доступную кнопку сохранения карты.',
    CARD_BROWSER_INTERRUPTED:'Браузер Meta прервал обработку.',
    CARD_FLOW_TIMEOUT:'Meta не завершила обработку вовремя; автоматический повтор остановлен.',
    CHECKPOINT_REQUIRED:'Meta требует проверки Facebook-аккаунта.',
    SESSION_EXPIRED:'Facebook-сессия истекла.',
    TWO_FACTOR_REQUIRED:'Meta требует двухфакторной проверки.'
  };
  const fields=[...new Set((result.fields||[]).filter(f=>f.required&&f.kind).map(f=>names[f.kind]||f.kind))];
  return (messages[result.code]||result.code||'Не удалось подтвердить результат')+
    (result.missing_fields?.length?' Поля: '+result.missing_fields.map(f=>names[f]||f).join(', ')+'.':'')+
    (fields.length?' Поля формы Meta: '+fields.join(', ')+'.':'');
}

async function inspectFundingRows(rows,container){
  await concurrent(rows,1,async r=>{
    try{return await apiJson('ajax/pythonWorkerJobs.php',post({action:'payment_status',profile:r.profile,account_id:r.id}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],f=res?.funding,line=document.createElement('div');line.className='ws-result';
    if(res?.error||!f){
      line.className+=' bad';line.textContent=r.profile+' / '+r.id+': '+(res?.error||'Нет результата проверки');
      if(/CHECKPOINT_REQUIRED/.test(res?.error||''))line.textContent+=' — Meta требует проверки аккаунта; проверка карты не выполнена.';
      r.funding={funding_verified:false,verification_status:'UNVERIFIED'};
    }else{
      r.funding=f;
      const methods=(f.payment_methods||[]).map(m=>m.type+' •••• '+m.last4).join(', ');
      const status=f.verification_status==='LINKED'?'Карта присутствует; платёжная проверка не подтверждена':
        f.verification_status==='NONE'?'Meta показывает отсутствие платёжных методов':'Платёжный метод или соответствие выбранному РК не удалось подтвердить';
      line.textContent=r.profile+' / '+r.id+': '+status+(methods?' — '+methods:'');
    }
    container.appendChild(line);setProgress(d,t);
  });render();
}

async function savePaymentCard(){
  const expiry=$('paymentCardExpiry').value.trim().match(/^(\d{1,2})\s*\/\s*(\d{2}|\d{4})$/);
  if(!expiry)throw new Error('Срок действия: ММ/ГГ.');
  const payload={action:'add',number:$('paymentCardNumber').value,month:expiry[1],year:expiry[2]};
  ['holder','country','address','city','region','postal_code','label'].forEach(key=>{payload[key]=$('paymentCard_'+key).value.trim();});
  try{return (await apiJson('ajax/paymentCards.php',post(payload))).card;}
  finally{$('paymentCardNumber').value='';payload.number='';}
}

async function bindPaymentCard(rows,card,cvv,container){
  if(!card?.id)throw new Error('Выберите сохранённую карту или добавьте новую.');
  if(!/^\d{3,4}$/.test(cvv))throw new Error('Введите CVV для этой операции. Он не сохраняется.');
  // One browser at a time; uncertain submission never triggers a retry.
  await concurrent(rows,1,async r=>{
    try{return await apiJson('ajax/paymentCards.php',post({action:'bind',card_id:card.id,cvv,profile:r.profile,account_id:r.id,setup_country:$('paymentSetupCountry').value||'UA',setup_currency:$('paymentSetupCurrency').value||'USD',setup_timezone:$('paymentSetupTimezone').value||'Europe/Kyiv'}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],result=res?.result,line=document.createElement('div');
    line.className='ws-result '+(result?.status==='LINKED'?'ok':'bad');
    line.textContent=r.profile+' / '+r.id+' · •••• '+card.last4+': '+(result?paymentCardMessage(result):res?.error||'Результат неизвестен');
    r.funding=result?.funding||{funding_verified:false,verification_status:'UNVERIFIED'};
    container.appendChild(line);setProgress(d,t);
  });render();
}

async function showFunding(){
  const rows=selectedRows('ad_accounts');if(!rows.length)return;
  const selected=rows.map(r=>esc(r.profile+' / '+r.id)).join('<br>');
  openModal('Карты и привязка — '+rows.length+' РК',`
    <div class="ws-muted">Выбранные рекламные аккаунты: ${selected}</div>
    <div class="ws-form mt-3">
      <div class="full"><label for="paymentCardSelect">Сохранённая карта</label><select id="paymentCardSelect"><option value="">Загрузка карт…</option></select></div>
      <div><label for="paymentCardCvv">CVV — только для текущей привязки</label><input id="paymentCardCvv" type="password" inputmode="numeric" maxlength="4" autocomplete="off"></div>
      <div><label for="paymentSetupCountry">Страна оплаты РК</label><select id="paymentSetupCountry"><option value="UA">Украина</option></select></div>
      <div><label for="paymentSetupCurrency">Валюта оплаты РК</label><select id="paymentSetupCurrency"><option value="USD">USD — доллар США</option></select></div>
      <div><label for="paymentSetupTimezone">Часовой пояс РК</label><input id="paymentSetupTimezone" value="Europe/Kyiv"></div>
    </div>
    <details id="paymentCardNew" class="mt-3"><summary>Добавить новую карту</summary>
      <div class="ws-form mt-2">
        <div><label for="paymentCardNumber">Номер карты</label><input id="paymentCardNumber" type="password" inputmode="numeric" autocomplete="off"></div>
        <div><label for="paymentCardExpiry">Срок действия</label><input id="paymentCardExpiry" placeholder="ММ/ГГ" autocomplete="off"></div>
        <div><label for="paymentCard_holder">Имя владельца карты</label><input id="paymentCard_holder" autocomplete="off"></div>
        <div><label for="paymentCard_label">Название карты в ReMask</label><input id="paymentCard_label" maxlength="80"></div>
        <div><label for="paymentCard_country">Страна платёжного адреса</label><input id="paymentCard_country" placeholder="Как указано у банка"></div>
        <div><label for="paymentCard_postal_code">Почтовый индекс</label><input id="paymentCard_postal_code"></div>
        <div class="full"><label for="paymentCard_address">Платёжный адрес</label><input id="paymentCard_address"></div>
        <div><label for="paymentCard_city">Город</label><input id="paymentCard_city"></div>
        <div><label for="paymentCard_region">Область / штат</label><input id="paymentCard_region"></div>
      </div>
      <button id="paymentCardSave" type="button" class="mt-2">Сохранить карту</button>
      <button id="paymentCardSaveBind" type="button" class="mt-2">Сохранить и привязать к выбранным РК</button>
    </details>
    <div class="mt-3"><button id="paymentCardBind" type="button">Привязать выбранную карту</button>
      <button id="paymentCardPrepare" type="button">Проверить форму Meta</button>
      <button id="paymentCardInspect" type="button">Проверить привязанные карты</button></div>
    <div class="ws-muted mt-2">Реквизиты сохраняются в зашифрованном виде, CVV не сохраняется. РК обрабатываются по одному. Привязка и платёжная проверка показываются отдельно.</div>
    <div id="paymentCardAssignments" class="ws-muted mt-2"></div>
    <div id="paymentCardProgress" class="ws-muted mt-2" aria-live="polite"></div>
    <div id="fundingResults" aria-live="polite"></div>`,'',null);
  const container=$('fundingResults'),select=$('paymentCardSelect');let cards=[],busy=false;
  const refreshCards=async(preferred='')=>{
    const data=await apiJson('ajax/paymentCards.php',post({action:'list'}));cards=data.cards||[];
    select.innerHTML='<option value="">Выберите карту</option>';
    cards.forEach(card=>{const option=document.createElement('option');option.value=card.id;
      option.textContent=(card.label?card.label+' · ':'')+card.brand+' •••• '+card.last4+' · '+String(card.month).padStart(2,'0')+'/'+String(card.year).slice(-2);select.appendChild(option);});
    const assignments=$('paymentCardAssignments');assignments.textContent='';
    (data.bindings||[]).filter(binding=>rows.some(r=>r.profile===binding.profile&&String(r.id).replace(/^act_/,'')===binding.account_id)).forEach(binding=>{
      const line=document.createElement('div');
      const status={LINKED:'ранее подтверждена',BLOCKED:'не привязана',FAILED:'ошибка',IN_PROGRESS:'операция начата',SUBMITTED_UNVERIFIED:'результат требует проверки',ACTION_REQUIRED:'требуется подтверждение'}[binding.status]||binding.status;
      line.textContent=binding.profile+' / act_'+binding.account_id+' · •••• '+binding.last4+' · '+status;assignments.appendChild(line);
    });
    if(preferred)select.value=preferred;
  };
  const run=async(task)=>{
    if(busy)return;busy=true;
    const controls=$('workspaceModalBody').querySelectorAll('input,select,button');controls.forEach(el=>el.disabled=true);
    $('paymentCardProgress').textContent='Выполняется проверка выбранного РК. Ожидаю ответ Meta…';
    try{await task();}catch(e){const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
    finally{remaskClearPaymentSecrets();controls.forEach(el=>el.disabled=false);$('paymentCardProgress').textContent='';busy=false;}
  };
  const save=async(bind)=>{
    const cvv=$('paymentCardCvv').value;
    if(bind&&!/^\d{3,4}$/.test(cvv))throw new Error('Введите CVV перед привязкой.');
    const card=await savePaymentCard();await refreshCards(card.id);$('paymentCardNew').open=false;
    const line=document.createElement('div');line.className='ws-result ok';line.textContent='Карта •••• '+card.last4+' сохранена в ReMask.';container.appendChild(line);
    if(bind){await bindPaymentCard(rows,card,cvv,container);await refreshCards(card.id);}
  };
  $('paymentCardSave').addEventListener('click',()=>run(()=>save(false)));
  $('paymentCardSaveBind').addEventListener('click',()=>run(()=>save(true)));
  $('paymentCardBind').addEventListener('click',()=>run(async()=>{const card=cards.find(c=>c.id===select.value);await bindPaymentCard(rows,card,$('paymentCardCvv').value,container);await refreshCards(card.id);}));
  $('paymentCardInspect').addEventListener('click',()=>run(()=>inspectFundingRows(rows,container)));
  $('paymentCardPrepare').addEventListener('click',()=>run(async()=>{
    for(let i=0;i<rows.length;i++){
      const r=rows[i],data=await apiJson('ajax/paymentCards.php',post({action:'prepare',profile:r.profile,account_id:r.id,setup_country:$('paymentSetupCountry').value,setup_currency:$('paymentSetupCurrency').value,setup_timezone:$('paymentSetupTimezone').value}));
      const line=document.createElement('div');line.className='ws-result '+(data.result.status==='FORM_READY'?'ok':'bad');
      line.textContent=r.profile+' / '+r.id+': '+paymentCardMessage(data.result);container.appendChild(line);setProgress(i+1,rows.length);
      if(data.result.ui_preview&&/^[A-Za-z0-9+/=]+$/.test(data.result.ui_preview)){
        const preview=document.createElement('details'),summary=document.createElement('summary'),image=document.createElement('img');
        summary.textContent='Экран Meta перед вводом карты';image.alt='Meta — '+r.profile+' / '+r.id;image.style.maxWidth='100%';
        image.src='data:image/jpeg;base64,'+data.result.ui_preview;preview.appendChild(summary);preview.appendChild(image);container.appendChild(preview);
      }
    }
  }));
  try{await refreshCards();}catch(e){select.innerHTML='<option value="">Список карт недоступен</option>';const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
}
