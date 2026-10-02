async function showFunding(){
  const rows=selectedRows('ad_accounts');
  if(!rows.length)return;
  openModal('Funding / карта — '+rows.length+' РК',
    '<div class="ws-muted">Проверяю платёжные методы через Facebook-сессию выбранного профиля. РК проверяются по одному.</div><div id="fundingResults"></div>','',null);
  const container=$('fundingResults');
  await concurrent(rows,1,async r=>{
    try{return await apiJson('ajax/pythonWorkerJobs.php',post({action:'payment_status',profile:r.profile,account_id:r.id}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],f=res?.funding;
    const line=document.createElement('div');
    line.className='ws-result';
    if(res?.error || !f){
      line.className+=' bad';
      const message=String(res?.error||'Нет результата проверки');
      line.textContent=r.profile+' / '+r.id+': '+message;
      if(/CHECKPOINT_REQUIRED/.test(message))line.textContent+=' — Meta требует проверки аккаунта; проверка карты не выполнена.';
      else if(/SESSION_EXPIRED/.test(message))line.textContent+=' — Facebook-сессия истекла; проверка карты не выполнена.';
      else if(/TWO_FACTOR_REQUIRED/.test(message))line.textContent+=' — Meta требует двухфакторной проверки.';
      r.funding={funding_verified:false,verification_status:'UNVERIFIED'};
    }else{
      r.funding=f;
      const methods=(f.payment_methods||[]).map(m=>m.type+' •••• '+m.last4).join(', ');
      const status=f.verification_status==='LINKED'?'Карта присутствует; платёжная проверка не подтверждена':
        f.verification_status==='NONE'?'Meta показывает отсутствие платёжных методов':
        'Платёжный метод или соответствие выбранному РК не удалось подтвердить';
      line.textContent=r.profile+' / '+r.id+': '+status+(methods?' — '+methods:'');
    }
    container.appendChild(line);setProgress(d,t);
  });
  const note=document.createElement('div');note.className='ws-muted mt-3';
  note.textContent='Эта проверка читает платёжные настройки Meta. Она не добавляет карту и не списывает деньги. Для добавления нужна реальная карта.';
  container.appendChild(note);render();
}
