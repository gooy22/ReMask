function remaskClearPaymentSecrets(){
  ['paymentCardNumber','paymentCardCvv'].forEach(id=>{const el=$(id);if(el)el.value='';});
}

function paymentCardMessage(result){
  const names={number:'номер карты',cvv:'CVV',holder:'имя владельца',expiry:'срок действия',month:'месяц',year:'год',country:'страна',address:'платёжный адрес',city:'город',region:'область / штат',postal_code:'индекс',unknown_required_field:'дополнительное поле Meta'};
  const messages={
    CARD_FORM_READY:'Форма Meta доступна для выбранного РК.',
    CARD_LINK_OBSERVED:'Карта привязана к выбранному РК. Платёжная проверка не выполнена.',
    ALREADY_LINKED:'Эта привязка ранее подтверждена. Для текущего состояния нажмите «Проверить привязанные карты».',
    CARD_AND_CVV_REQUIRED:'Для новой привязки нужен CVV. Введите его один раз для выбранной группы РК.',
    CARD_BINDING_RECONCILE_REQUIRED:'Предыдущая привязка ещё не подтверждена. Сначала проверьте состояние карты в Meta; повтор остановлен.',
    CARD_RECONCILE_UNVERIFIED:'Meta пока не подтвердила эту карту у выбранного РК. Повторное добавление остаётся заблокированным.',
    CARD_RECONCILE_NO_METHOD:'Meta показывает отсутствие способа оплаты у выбранного РК. Карта не привязана; результат предыдущей отправки требует разбора.',
    CARD_META_REJECTED:'Meta показала ошибку сохранения карты. Привязка не подтверждена; повторная отправка остановлена.',
    CARD_RETRY_REVIEW_REQUIRED:'Сначала проверьте результат предыдущей попытки в Meta.',
    CARD_RETRY_REVIEW_EXPIRED:'Проверка устарела. Нажмите «Проверить результат» ещё раз.',
    CARD_RETRY_REVIEW_INVALID:'Подтверждение проверки не подходит этой попытке. Проверьте результат заново.',
    CARD_RETRY_ACCOUNT_NOT_EMPTY:'Meta больше не подтверждает отсутствие карты у этого РК. Повтор остановлен.',
    CARD_BINDING_CARD_MISMATCH:'Для проверки выберите карту предыдущей попытки привязки.',
    CARD_BINDING_CHANGED:'Состояние привязки изменилось во время проверки. Обновите результат.',
    CARD_BINDING_IN_PROGRESS:'Предыдущая операция ещё выполняется. Повторная отправка остановлена.',
    CARD_LINK_NOT_VERIFIED:'Карта отправлена в Meta; привязка пока не подтверждена. Повторное добавление остановлено.',
    PAYMENT_ACCOUNT_SCOPE_UNVERIFIED:'Meta не подтвердила точный РК. Карта не отправлена.',
    CARD_ACCOUNT_SCOPE_UNVERIFIED:'Meta не подтвердила выбор «Только этот аккаунт». Карта не отправлена; добавление карты для всего BM остановлено.',
    PAYMENT_ACCOUNT_BINDING_MISSING:'Нет однозначного соответствия профиля и РК. Обновите выбранный РК.',
    PAYMENT_ACCOUNT_ROW_MISSING:'Meta не показала строку выбранного РК.',
    PAYMENT_ADD_CONTROL_MISSING:'У выбранного РК Meta не показала кнопку добавления способа оплаты.',
    PAYMENT_AD_ACCOUNT_DISABLED:'Meta отключила выбранный РК. Добавление карты остановлено до восстановления РК.',
    PAYMENT_ACCOUNT_SETUP_REQUIRED:'Meta сначала требует настройки страны, валюты и часового пояса РК. Эти настройки нельзя изменить после подтверждения. Карта не отправлена.',
    PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING:'Не удалось выбрать указанную настройку оплаты в форме Meta. Карта не отправлена.',
    PAYMENT_SETUP_INVALID:'Укажите Украину или текущую страну Meta, USD и Europe/Kyiv для этой настройки оплаты.',
    PAYMENT_COUNTRY_UNVERIFIED:'Не удалось однозначно прочитать страну в форме Meta. Настройки не подтверждены, карта не отправлена.',
    PAYMENT_COUNTRY_LOCKED:'Meta заблокировала поле страны. Можно сохранить текущую страну Meta; карта не отправлена.',
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
  const setup=result.billing_setup_observed;
  const country=setup?.country_label?' Страна в форме Meta: '+setup.country_label+
    (setup.country_preserved?' — сохранён текущий выбор'+(setup.country_reason==='meta_control_locked'?' (поле заблокировано Meta)':''):'')+
    '. Сохранение настроек в Meta пока не подтверждено.':'';
  const availability=result.card_availability==='only_this_account'?' В форме выбрано «Только этот РК».':'';
  return (messages[result.code]||result.code||'Не удалось подтвердить результат')+country+availability+
    (result.missing_fields?.length?' Поля: '+result.missing_fields.map(f=>names[f]||f).join(', ')+'.':'')+
    (fields.length?' Поля формы Meta: '+fields.join(', ')+'.':'');
}

function paymentSetupPayload(){
  return {setup_country:'UA',setup_country_mode:$('paymentSetupCountry').value==='CURRENT'?'current':'prefer_ua',
    setup_currency:$('paymentSetupCurrency').value||'USD',setup_timezone:$('paymentSetupTimezone').value||'Europe/Kyiv'};
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

async function bindPaymentCard(rows,card,cvv,container,reviews={}){
  const missing=[];
  if(!card?.id)throw new Error('Выберите сохранённую карту или добавьте новую.');
  if(cvv&&!/^\d{3,4}$/.test(cvv))throw new Error('CVV должен содержать 3 или 4 цифры.');
  // One browser at a time; uncertain submission never triggers a retry.
  await concurrent(rows,1,async r=>{
    const review=reviews[r.profile+'|'+String(r.id).replace(/^act_/,'')];
    try{return await apiJson('ajax/paymentCards.php',post({action:'bind',card_id:card.id,...(cvv?{cvv}:{}),
      ...(review?{retry_confirmed:'1',retry_review:review.token}:{}),profile:r.profile,account_id:r.id,...paymentSetupPayload()}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],result=res?.result,line=document.createElement('div');
    if(result?.code==='CARD_BILLING_FIELDS_REQUIRED')missing.push(...(result.missing_fields||[]));
    line.className='ws-result '+(result?.status==='LINKED'?'ok':'bad');
    line.textContent=r.profile+' / '+r.id+' · •••• '+card.last4+': '+paymentCardMessage(result||{code:res?.error||'Результат неизвестен'});
    r.funding=result?.funding||{funding_verified:false,verification_status:'UNVERIFIED'};
    container.appendChild(line);setProgress(d,t);
  });render();return [...new Set(missing)];
}

function paymentCardTargetPlan(rows,bindings,cardId){
  const plan={fresh:[],pending:[],linked:[],blocked:[]},seen=new Set();
  for(const row of rows){
    const account=String(row.id).replace(/^act_/,''); const key=row.profile+'|'+account;
    if(seen.has(key))continue;seen.add(key);
    const binding=bindings.find(b=>b.profile===row.profile&&b.account_id===account);
    if(binding&&['IN_PROGRESS','SUBMITTED_UNVERIFIED','ACTION_REQUIRED'].includes(binding.status)){
      plan[!cardId||binding.card_id===cardId?'pending':'blocked'].push(row);
    }else if(binding?.status==='LINKED'&&binding.card_id===cardId)plan.linked.push(row);
    else plan.fresh.push(row);
  }
  return plan;
}

async function reconcilePaymentCard(rows,card,container){
  if(!card?.id)throw new Error('Выберите карту предыдущей попытки привязки.');
  const reviews={};
  await concurrent(rows,1,async r=>{
    try{return await apiJson('ajax/paymentCards.php',post({action:'reconcile',card_id:card.id,profile:r.profile,account_id:r.id}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],result=res?.result,line=document.createElement('div');
    line.className='ws-result '+(result?.status==='LINKED'?'ok':'bad');
    line.textContent=r.profile+' / '+r.id+' · •••• '+card.last4+': '+paymentCardMessage(result||{code:res?.error||'Результат неизвестен'});
    r.funding=result?.funding||{funding_verified:false,verification_status:'UNVERIFIED'};
    if(result?.code==='CARD_RECONCILE_NO_METHOD'&&result.retry_review?.token&&result.retry_review?.expires_at)
      reviews[r.profile+'|'+String(r.id).replace(/^act_/,'')]={...result.retry_review,card_id:card.id};
    container.appendChild(line);
    const f=result?.funding;
    if(f?.ui_preview&&/^[A-Za-z0-9+/=]+$/.test(f.ui_preview)){
      const preview=document.createElement('details'),summary=document.createElement('summary'),image=document.createElement('img');
      summary.textContent='Экран проверки Meta';image.alt='Meta — '+r.profile+' / '+r.id;image.style.maxWidth='100%';
      image.src='data:image/jpeg;base64,'+f.ui_preview;preview.appendChild(summary);preview.appendChild(image);container.appendChild(preview);
    }
    setProgress(d,t);
  });render();return reviews;
}

async function showFunding(){
  const rows=selectedRows('ad_accounts');if(!rows.length)return;
  const selected=rows.map(r=>esc(r.profile+' / '+r.id)).join('<br>');
  openModal('Карты и привязка — '+rows.length+' РК',`
    <div class="ws-muted">Выбранные рекламные аккаунты: ${selected}</div>
    <div class="ws-form mt-3">
      <div class="full"><label for="paymentCardSelect">Сохранённая карта</label><select id="paymentCardSelect"><option value="">Загрузка карт…</option></select></div>
      <div id="paymentCardCvvField"><label for="paymentCardCvv">CVV для новой привязки</label><input id="paymentCardCvv" type="password" inputmode="numeric" maxlength="4" autocomplete="off"></div>
    </div>
    <details class="mt-2"><summary>Настройки оплаты</summary><div class="ws-form mt-2">
      <div><label for="paymentSetupCountry">Страна оплаты РК</label><select id="paymentSetupCountry"><option value="UA">Украина; если поле заблокировано — текущая страна Meta</option><option value="CURRENT">Текущая страна Meta</option></select></div>
      <div><label for="paymentSetupCurrency">Валюта оплаты РК</label><select id="paymentSetupCurrency"><option value="USD">USD — доллар США</option></select></div>
      <div><label for="paymentSetupTimezone">Часовой пояс РК</label><input id="paymentSetupTimezone" value="Europe/Kyiv"></div>
    </div></details>
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
    <div id="paymentCardBillingMissing" class="ws-form mt-2"></div>
    <label id="paymentCardRetryField" hidden class="mt-2"><input id="paymentCardRetryConfirmed" type="checkbox"> Разрешаю одну повторную попытку после проверки Meta</label>
    <div class="mt-3"><button id="paymentCardBind" type="button">Привязать и проверить</button></div>
    <details class="mt-2"><summary>Диагностика</summary>
      <button id="paymentCardPrepare" type="button">Проверить форму Meta</button>
      <button id="paymentCardInspect" type="button">Проверить привязанные карты</button></details>
    <div class="ws-muted mt-2">CVV не нужен для проверки результата. РК обрабатываются по одному.</div>
    <div id="paymentCardAssignments" class="ws-muted mt-2"></div>
    <div id="paymentCardProgress" class="ws-muted mt-2" aria-live="polite"></div>
    <div id="fundingResults" aria-live="polite"></div>`,'',null);
  const container=$('fundingResults'),select=$('paymentCardSelect');let cards=[],bindings=[],busy=false,reviews={},billingMissing=[];
  const targetPlan=()=>paymentCardTargetPlan(rows,bindings,select.value);
  const pending=()=>targetPlan().pending.length>0&&targetPlan().fresh.length===0;
  const retryAvailable=()=>pending()&&bindings.filter(b=>b.card_id===select.value&&['IN_PROGRESS','SUBMITTED_UNVERIFIED','ACTION_REQUIRED'].includes(b.status)).every(b=>{
    const review=reviews[b.profile+'|'+b.account_id];return review?.card_id===select.value&&Date.parse(review.expires_at)>Date.now();
  });
  const retryConfirmed=()=>retryAvailable()&&$('paymentCardRetryConfirmed').checked===true;
  const updatePrimary=()=>{
    const plan=targetPlan(),checking=pending(),retry=retryConfirmed();
    $('paymentCardBind').textContent=checking?(retry?'Повторить привязку':'Проверить результат'):
      plan.fresh.length?(plan.pending.length||plan.linked.length||plan.blocked.length?'Продолжить привязку — '+plan.fresh.length+' РК':'Привязать и проверить'):
      plan.blocked.length?'Выберите карту незавершённой привязки':'Все выбранные РК уже привязаны';
    $('paymentCardBind').disabled=busy||(!plan.fresh.length&&!plan.pending.length);
    $('paymentCardRetryField').hidden=!retryAvailable();
    $('paymentCardCvvField').hidden=checking&&!retry;
    $('paymentCardSaveBind').disabled=busy||checking;
  };
  const refreshCards=async(preferred='')=>{
    const data=await apiJson('ajax/paymentCards.php',post({action:'list'}));cards=data.cards||[];
    select.innerHTML='<option value="">Выберите карту</option>';
    cards.forEach(card=>{const option=document.createElement('option');option.value=card.id;
      option.textContent=(card.label?card.label+' · ':'')+card.brand+' •••• '+card.last4+' · '+String(card.month).padStart(2,'0')+'/'+String(card.year).slice(-2);select.appendChild(option);});
    const assignments=$('paymentCardAssignments');assignments.textContent='';
    bindings=(data.bindings||[]).filter(binding=>rows.some(r=>r.profile===binding.profile&&String(r.id).replace(/^act_/,'')===binding.account_id));
    bindings.forEach(binding=>{
      const line=document.createElement('div');
      const status={LINKED:'ранее подтверждена',BLOCKED:'не привязана',FAILED:'ошибка',IN_PROGRESS:'операция начата',SUBMITTED_UNVERIFIED:'результат требует проверки',ACTION_REQUIRED:'требуется подтверждение'}[binding.status]||binding.status;
      line.textContent=binding.profile+' / act_'+binding.account_id+' · •••• '+binding.last4+' · '+status+
        (binding.last_result_code?' · '+paymentCardMessage({code:binding.last_result_code}):'');assignments.appendChild(line);
    });
    if(preferred)select.value=preferred;
    else if(!select.value&&rows.length===1&&bindings.length===1)select.value=bindings[0].card_id;
    updatePrimary();
  };
  const showMissingBilling=missing=>{
    const labels={holder:'Имя владельца карты',country:'Страна платёжного адреса',address:'Платёжный адрес',city:'Город',region:'Область / штат',postal_code:'Почтовый индекс'};
    billingMissing=[...new Set(missing.filter(k=>labels[k]))];
    $('paymentCardBillingMissing').innerHTML=billingMissing.map(k=>'<div><label for="paymentCardExisting_'+k+'">'+labels[k]+' — требуется Meta</label><input id="paymentCardExisting_'+k+'" autocomplete="off"></div>').join('');
  };
  const saveExistingBilling=async card=>{
    const patch={};for(const key of billingMissing){const value=$('paymentCardExisting_'+key).value.trim();if(!value)throw new Error('Заполните обязательные реквизиты карты.');patch[key]=value;}
    if(Object.keys(patch).length){await apiJson('ajax/paymentCards.php',post({action:'billing_update',card_id:card.id,...patch}));showMissingBilling([]);}
  };
  const run=async(task,clearSecrets=true)=>{
    if(busy)return;busy=true;container.innerHTML='';
    const controls=$('workspaceModalBody').querySelectorAll('input,select,button');controls.forEach(el=>el.disabled=true);
    $('paymentCardProgress').textContent='Обработка группы из '+rows.length+' РК. Ожидаю ответ Meta…';
    try{await task();}catch(e){const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
    finally{if(clearSecrets)remaskClearPaymentSecrets();controls.forEach(el=>el.disabled=false);$('paymentCardProgress').textContent='';busy=false;updatePrimary();}
  };
  const save=async(bind)=>{
    if(bind&&pending())throw new Error(paymentCardMessage({code:'CARD_BINDING_RECONCILE_REQUIRED'}));
    const cvv=$('paymentCardCvv').value;
    const card=await savePaymentCard();await refreshCards(card.id);$('paymentCardNew').open=false;
    const line=document.createElement('div');line.className='ws-result ok';line.textContent='Карта •••• '+card.last4+' сохранена в ReMask.';container.appendChild(line);
    if(bind){const plan=targetPlan();showMissingBilling(await bindPaymentCard(plan.fresh,card,cvv,container));await refreshCards(card.id);}
  };
  select.addEventListener('change',()=>{$('paymentCardCvv').value='';$('paymentCardRetryConfirmed').checked=false;reviews={};showMissingBilling([]);updatePrimary();});
  $('paymentCardRetryConfirmed').addEventListener('change',updatePrimary);
  $('paymentCardSave').addEventListener('click',()=>run(()=>save(false),false));
  $('paymentCardSaveBind').addEventListener('click',()=>run(()=>save(true)));
  $('paymentCardBind').addEventListener('click',()=>run(async()=>{
    const card=cards.find(c=>c.id===select.value);
    if(pending()&&!retryConfirmed()){
      $('paymentCardRetryConfirmed').checked=false;
      reviews=await reconcilePaymentCard(targetPlan().pending,card,container);
    }else{
      if(!card?.id)throw new Error('Выберите сохранённую карту.');
      const confirmed=retryConfirmed(),cvv=$('paymentCardCvv').value;
      if(confirmed&&!/^\d{3,4}$/.test(cvv))throw new Error('Введите CVV для одной повторной попытки.');
      await saveExistingBilling(card);
      const plan=targetPlan(),targets=confirmed?plan.pending:plan.fresh;
      if(!targets.length)throw new Error(plan.blocked.length?'Для незавершённых РК выберите карту предыдущей попытки.':'Все выбранные РК уже привязаны.');
      if(plan.pending.length&&!confirmed){const note=document.createElement('div');note.className='ws-result';note.textContent='Незавершённые привязки: '+plan.pending.length+' РК. Повторная отправка пропущена.';container.appendChild(note);}
      if(plan.linked.length){const note=document.createElement('div');note.className='ws-result ok';note.textContent='Уже привязаны: '+plan.linked.length+' РК. Пропущены.';container.appendChild(note);}
      if(plan.blocked.length){const note=document.createElement('div');note.className='ws-result bad';note.textContent='Для '+plan.blocked.length+' РК нужно проверить предыдущую карту. Остальные продолжаются.';container.appendChild(note);}
      try{showMissingBilling(await bindPaymentCard(targets,card,cvv,container,confirmed?reviews:{}));}
      finally{reviews={};$('paymentCardRetryConfirmed').checked=false;}
    }
    await refreshCards(card?.id||'');
  }));
  $('paymentCardInspect').addEventListener('click',()=>run(()=>inspectFundingRows(rows,container),false));
  $('paymentCardPrepare').addEventListener('click',()=>run(async()=>{
    for(let i=0;i<rows.length;i++){
      const r=rows[i],data=await apiJson('ajax/paymentCards.php',post({action:'prepare',...(select.value?{card_id:select.value}:{}),profile:r.profile,account_id:r.id,...paymentSetupPayload()}));
      const line=document.createElement('div');line.className='ws-result '+(data.result.status==='FORM_READY'?'ok':'bad');
      line.textContent=r.profile+' / '+r.id+': '+paymentCardMessage(data.result);container.appendChild(line);setProgress(i+1,rows.length);
      if(data.result.code==='CARD_BILLING_FIELDS_REQUIRED')showMissingBilling([...billingMissing,...(data.result.missing_fields||[])]);
      if(data.result.ui_preview&&/^[A-Za-z0-9+/=]+$/.test(data.result.ui_preview)){
        const preview=document.createElement('details'),summary=document.createElement('summary'),image=document.createElement('img');
        summary.textContent='Экран Meta перед вводом карты';image.alt='Meta — '+r.profile+' / '+r.id;image.style.maxWidth='100%';
        image.src='data:image/jpeg;base64,'+data.result.ui_preview;preview.appendChild(summary);preview.appendChild(image);container.appendChild(preview);
      }
    }
  },false));
  try{await refreshCards();}catch(e){select.innerHTML='<option value="">Список карт недоступен</option>';const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
}
