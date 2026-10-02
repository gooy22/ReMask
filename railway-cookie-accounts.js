import {Requests} from './requests.js';

let remaskEditingProfile = false;

async function remaskLoadNextProfileNumber() {
    try {
        const response = await fetch('ajax/metaProfileManager.php', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'next_number'})});
        const data = await response.json();
        if (!data.ok) throw new Error(data.message || data.error || 'Не удалось получить номер');
        document.add.name.value = String(data.next_number);
        document.add.name.readOnly = true;
    } catch (error) { alert(error.message); }
}

window.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.delaccount').forEach(button => button.addEventListener('click', async (event) => delAccount(event.target.dataset.name)));
    document.querySelectorAll('.editaccount').forEach(button => button.addEventListener('click', async (event) => editAccount(event.target.dataset.name)));
    const loadingIcon = document.getElementById('loadingIcon');
    document.getElementById('addaccountbutton').addEventListener('click', async () => {
        loadingIcon.style.display = 'inline-block';
        try { await addAccount(); } finally { loadingIcon.style.display = 'none'; }
    });
    loadStorageStatus();
    remaskLoadNextProfileNumber();
});

async function loadStorageStatus() {
    const banner = document.getElementById('storageBanner');
    try {
        const response = await fetch('ajax/storageStatus.php', {cache: 'no-store'});
        const body = await response.json();
        if (!body.ok) throw new Error(body?.error?.message || 'Storage check failed');
        const s = body.data;
        if (s.warning) {
            banner.textContent = `⚠ ${s.warning} Data dir: ${s.data_dir}`;
            banner.style.borderColor = '#a86b43';
        } else {
            banner.textContent = `Storage: ${s.persistent_storage_detected ? 'persistent Railway Volume' : 'local filesystem'} · ${s.data_dir} · ${s.writable ? 'writable' : 'NOT WRITABLE'}`;
        }
    } catch (e) {
        banner.textContent = `Storage status unavailable: ${e.message}`;
    }
}

async function addAccount() {
    const name = document.add.name.value.trim();
    const token = ''; // historical function argument only; never collected/sent
    const cookies = document.add.cookies.value.trim();
    const proxy = document.add.proxy.value.trim();
    const editing = remaskEditingProfile;
    if (!await validateForm(name, token, cookies, proxy, editing)) return;

    const check = await Requests.post('ajax/checkAccount.php', `name=${encodeURIComponent(name)}&cookies=${encodeURIComponent(editing ? cookies : (cookies || '[]'))}&proxy=${encodeURIComponent(proxy)}`);
    const checked = await Requests.checkResponse(check);
    if (!checked.success) {
        alert(`Проверка cookies/proxy не пройдена: ${checked.error}`);
        return;
    }

    const resp = await Requests.post('ajax/addAccount.php', `action=${editing?'save':'create'}&auto_number=${editing?'0':'1'}&name=${encodeURIComponent(name)}&cookies=${encodeURIComponent(editing ? cookies : (cookies || '[]'))}&proxy=${encodeURIComponent(proxy)}`);
    const saved = await Requests.checkResponse(resp, false);
    if (saved.success) window.location.reload();
    else alert(`Error saving account: ${saved.error}`);
}

async function editAccount(name) {
    const resp = await fetch(`ajax/editAccount.php?name=${encodeURIComponent(name)}`);
    const checked = await Requests.checkResponse(resp);
    if (!checked.success) return alert(`Error editing account: ${checked.error}`);
    const data = checked.data;
    remaskEditingProfile = true;
    document.add.name.value = data.name;
    document.add.name.readOnly = true;
    document.add.cookies.value = '';
    document.add.cookies.placeholder = data.legacy_ready ? 'Leave blank to keep current cookies' : '[{"name":"c_user","value":"..."}]';
    document.add.proxy.value = '';
    document.add.proxy.placeholder = data.proxy_configured
        ? `Leave blank to keep current proxy (${data.proxy_hint?.type || ''}:${data.proxy_hint?.ip || ''}:${data.proxy_hint?.port || ''})`
        : 'http:ip:port:login:pass';
}

async function delAccount(name) {
    const resp = await Requests.post('ajax/delAccount.php', `name=${encodeURIComponent(name)}`);
    const checked = await Requests.checkResponse(resp, false);
    if (checked.success) window.location.reload();
    else alert(`Error deleting account: ${checked.error}`);
}

async function validateForm(name, token, cookies, proxy, editing = false) {
    if (!name) { alert('Название профиля обязательно.'); return false; }
    if (!editing && (!cookies || !proxy)) { alert('Нужны Facebook cookies и прокси.'); return false; }
    if (cookies) {
        let parsed;
        try { parsed = JSON.parse(cookies); } catch { alert('Cookies must be a valid JSON array.'); return false; }
        if (!parsed || typeof parsed !== 'object') { alert('Нужен JSON cookies.'); return false; }
        const rows = Array.isArray(parsed) ? parsed : Object.entries(parsed).map(([name,value])=>({name,value}));
        const values = new Map();
        for (const row of rows) {
            if (!row || typeof row.name !== 'string' || !['string','number'].includes(typeof row.value)) { alert('Каждая cookie должна содержать name и value.'); return false; }
            if (['c_user','xs'].includes(row.name)) {
                if (values.has(row.name) && values.get(row.name) !== String(row.value)) { alert('В cookies разные значения '+row.name); return false; }
                values.set(row.name,String(row.value));
            }
        }
        if (!/^\d+$/.test(values.get('c_user')||'') || !String(values.get('xs')||'').trim()) { alert('Нужны непустые c_user и xs.'); return false; }
    }
    return true;
}
