/* REMASK_COOKIE_PROFILE_UI_V1 */
function remaskCookieRows(raw) {
  let value;
  try { value = JSON.parse(raw); } catch (_) { throw new Error('Cookies должны быть валидным JSON.'); }
  if (!value || typeof value !== 'object') throw new Error('Нужен JSON cookies с c_user и xs.');
  const rows = Array.isArray(value) ? value : Object.entries(value).map(([name, value]) => ({name, value}));
  const auth = new Map();
  for (const row of rows) {
    if (!row || typeof row.name !== 'string' || !row.name.trim() ||
        !['string', 'number'].includes(typeof row.value)) throw new Error('Каждая cookie должна содержать name и value.');
    const name = row.name.trim();
    const value = String(row.value);
    if (['c_user', 'xs'].includes(name)) {
      if (auth.has(name) && auth.get(name) !== value) throw new Error('Cookies содержат разные значения ' + name + '.');
      auth.set(name, value);
    }
  }
  if (!/^\d+$/.test(auth.get('c_user') || '') || !String(auth.get('xs') || '').trim()) {
    throw new Error('В cookies нужны непустые c_user и xs.');
  }
  return rows;
}
async function prepareAddProfile() {
  const sequence = await apiJson('ajax/metaProfileManager.php', post({action:'next_number'}));
  const nextNumber = String(sequence.next_number);
  openModal('Добавить FB аккаунт', `<div class="ws-form"><div><label>Номер аккаунта</label><input id="newProfileName" value="${esc(nextNumber)}" readonly></div><div class="full"><label>Прокси</label><input id="newProfileProxy" placeholder="http:ip:port:login:password"></div><div class="full"><label>Facebook cookies JSON</label><textarea id="newProfileCookies" rows="5" placeholder="JSON cookies с c_user и xs"></textarea></div></div><div class="ws-muted mt-2">Авторизация через cookies. Токен Ads Manager не нужен. Сохранение профиля не подтверждает действительность Facebook-сессии.</div>`, 'Добавить', async () => {
    const name = $('newProfileName').value.trim();
    const proxy = $('newProfileProxy').value.trim();
    if (!name) throw new Error('Название профиля обязательно.');
    if (!proxy) throw new Error('Прокси обязателен.');
    const cookies = JSON.stringify(remaskCookieRows($('newProfileCookies').value.trim()));
    await apiJson('ajax/metaProfileManager.php', post({action:'create', name, cookies, proxy, auto_number:'1'}));
    await loadInventory('Профиль сохранён. Facebook-сессия ещё не проверена.');
    closeModal();
  });
}
function prepareEditProfile() {
  const p = selectedRows('profiles')[0];
  if (!p) return;
  openModal(`Редактировать ${p.name}`, `<div class="ws-form"><div class="full"><label>Новый прокси (пусто = сохранить текущий)</label><input id="editProxy" placeholder="http:ip:port:login:password"></div><div class="full"><label>Новые cookies JSON (пусто = сохранить текущие)</label><textarea id="editCookies" rows="5"></textarea></div><div class="full"><label><input id="editClearProxy" type="checkbox" style="width:auto"> удалить текущий прокси</label></div></div><div class="ws-muted mt-2">Токен Ads Manager не нужен. Пустое поле cookies сохраняет текущую сессию.</div>`, 'Сохранить', async () => {
    const raw = $('editCookies').value.trim();
    const cookies = raw ? JSON.stringify(remaskCookieRows(raw)) : '';
    await apiJson('ajax/metaProfileManager.php', post({action:'save', name:p.name, cookies, proxy:$('editProxy').value.trim(), clear_proxy:$('editClearProxy').checked ? '1' : '0'}));
    await loadInventory('Настройки профиля сохранены. Facebook-сессия ещё не проверена.');
    closeModal();
  });
}
