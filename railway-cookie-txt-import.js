/* REMASK_COOKIE_TXT_IMPORT_V1: shop secrets stay in memory, never in UI/storage/logs. */
(() => {
  'use strict';
  const labels = {ready:'Готов к импорту', imported:'Импортирован', already_exists:'Уже есть в системе',
    duplicate_in_file:'Повтор в файле', error:'Ошибка'};
  const errors = {COOKIE_JSON_MISSING:'Не найден JSON cookies', COOKIE_JSON_INVALID:'Повреждён JSON cookies',
    COOKIE_SESSION_INVALID:'Некорректные c_user / xs или домен cookies', LOGIN_COOKIE_MISMATCH:'Логин не совпадает с c_user',
    MULTIPLE_COOKIE_SESSIONS:'Несколько сессий в одной записи', CONFLICTING_COOKIE_SESSIONS:'Разные сессии одного аккаунта',
    TXT_SAVE_FAILED:'Сохранение остановлено. Повторный импорт проверит уже сохранённые профили.'};
  let dialog, raw = '', preview = null, busy = false;

  async function request(action, extra = {}) {
    const response = await fetch('ajax/metaProfileManager.php', {method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, text:raw, ...extra})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || 'Не удалось обработать TXT.');
    return result;
  }
  const field = id => dialog.querySelector('#' + id);
  function setBusy(value) {
    busy = value;
    for (const id of ['txtFile','txtProxy','txtPreview','txtClose','txtSelectAll']) field(id).disabled = value;
    field('txtCommit').disabled = value || !preview || !dialog.querySelector('input[data-txt-line]:checked');
    dialog.querySelectorAll('input[data-txt-line]').forEach(input => { input.disabled = value; });
  }
  function clear() {
    raw = ''; preview = null;
    field('txtResults').replaceChildren();
    field('txtStatus').textContent = '';
    field('txtCommit').disabled = true;
    field('txtSelectAll').checked = false;
  }
  function render(data, selectable) {
    const body = field('txtResults'); body.replaceChildren();
    for (const record of data.records) {
      const row = document.createElement('tr');
      const select = document.createElement('td');
      if (selectable && record.status === 'ready') {
        const box = document.createElement('input'); box.type = 'checkbox'; box.checked = true;
        box.dataset.txtLine = String(record.line); box.setAttribute('aria-label', 'Импортировать строку ' + record.line);
        box.addEventListener('change', () => setBusy(false)); select.append(box);
      }
      row.append(select);
      for (const text of [record.line, record.user_id || '—', record.profile_name || '—',
        (labels[record.status] || record.status) + (record.error ? ': ' + (errors[record.error] || record.error) : '')]) {
        const cell = document.createElement('td'); cell.textContent = String(text); row.append(cell);
      }
      body.append(row);
    }
    field('txtSelectAll').checked = selectable && data.ready > 0;
    field('txtStatus').textContent = selectable
      ? `Записей: ${data.total}. Готовы: ${data.ready}. Пропуски: ${data.skipped}. Ошибки: ${data.errors}. Номера уточняются при сохранении.`
      : `Импортировано: ${data.imported}. Пропуски: ${data.skipped}. Ошибки: ${data.errors}. Обработано: ${data.processed} из ${data.total}. Facebook-сессии ещё не проверены.`;
  }
  function createDialog() {
    dialog = document.createElement('dialog'); dialog.id = 'remaskTxtDialog';
    dialog.setAttribute('aria-labelledby', 'txtTitle');
    dialog.style.cssText = 'width:min(900px,94vw);max-height:88vh;overflow:auto;background:#111827;color:#e5e7eb;border:1px solid #475569;border-radius:12px;padding:24px;';
    dialog.innerHTML = `<h2 id="txtTitle">Импорт Facebook аккаунтов из TXT</h2>
      <p>Логин и пароль отделяются от JSON cookies автоматически. Аккаунт определяется по c_user. Пароль и 2FA не сохраняются.</p>
      <div style="display:grid;gap:12px"><label>TXT файл (UTF-8, до 2 МБ, до 500 записей)<br><input id="txtFile" type="file" accept=".txt,text/plain"></label>
      <label>Прокси для выбранных аккаунтов<br><input id="txtProxy" type="password" autocomplete="off" placeholder="http:ip:port:login:password" style="width:100%"></label></div>
      <p>Сначала распознай файл, затем выбери записи для импорта. Дубликаты пропускаются. Проверка формата не подтверждает вход в Facebook.</p>
      <button type="button" id="txtPreview">Распознать файл</button>
      <p id="txtStatus" role="status" aria-live="polite"></p>
      <div style="max-height:38vh;overflow:auto"><table style="width:100%;text-align:left"><thead><tr>
      <th><input id="txtSelectAll" type="checkbox" aria-label="Выбрать все готовые записи"></th><th>Строка</th><th>Facebook ID</th><th>№ профиля</th><th>Результат</th>
      </tr></thead><tbody id="txtResults"></tbody></table></div>
      <div style="display:flex;gap:12px;margin-top:18px"><button type="button" id="txtCommit" disabled>Импортировать выбранные</button><button type="button" id="txtClose">Закрыть</button></div>`;
    document.body.append(dialog);
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('close', () => { clear(); field('txtFile').value = ''; field('txtProxy').value = ''; });
    field('txtClose').addEventListener('click', () => dialog.close());
    field('txtFile').addEventListener('change', clear);
    field('txtSelectAll').addEventListener('change', () => {
      dialog.querySelectorAll('input[data-txt-line]').forEach(box => { box.checked = field('txtSelectAll').checked; });
      setBusy(false);
    });
    field('txtPreview').addEventListener('click', async () => {
      if (busy) return;
      clear(); setBusy(true);
      try {
        const file = field('txtFile').files[0];
        if (!file) throw new Error('Выберите TXT файл.');
        if (file.size > 2097152) throw new Error('Максимальный размер файла — 2 МБ.');
        // Fatal decoder prevents silent corruption in non UTF-8 shop exports.
        raw = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer());
        preview = await request('import_preview'); render(preview, true);
      } catch (error) { raw = ''; preview = null; field('txtStatus').textContent = error.message; }
      finally { setBusy(false); }
    });
    field('txtCommit').addEventListener('click', async () => {
      if (busy || !preview) return;
      const selected_lines = [...dialog.querySelectorAll('input[data-txt-line]:checked')].map(box => Number(box.dataset.txtLine));
      const proxy = field('txtProxy').value.trim();
      if (!proxy) { field('txtStatus').textContent = 'Прокси обязателен для импорта.'; return; }
      if (!selected_lines.length) return;
      setBusy(true);
      try {
        const result = await request('import_txt', {selected_lines, proxy});
        render(result, false); raw = ''; preview = null; field('txtProxy').value = '';
        field('txtFile').value = '';
        // Refresh Workspace after closing, while keeping the import result reviewable.
        if (result.imported > 0) dialog.addEventListener('close', () => window.location.reload(), {once:true});
      } catch (error) { field('txtStatus').textContent = error.message; }
      finally { setBusy(false); }
    });
  }
  function install() {
    const anchor = document.getElementById('addProfileTop') || document.getElementById('addaccountbutton');
    if (!anchor || document.getElementById('remaskTxtImport')) return;
    const button = document.createElement('button'); button.type = 'button'; button.id = 'remaskTxtImport';
    button.className = anchor.className; button.textContent = '+ Импорт TXT'; button.style.marginLeft = '8px';
    anchor.insertAdjacentElement('afterend', button);
    button.addEventListener('click', () => { if (!dialog) createDialog(); dialog.showModal(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
