/* REMASK_WORKSPACE_STATE_V1 */
// Store only local selection/filter identifiers. Never retain form secrets.
const remaskWorkspaceStateKey = 'remask.workspace.state.v1';
const remaskWorkspaceInitialInventory = state.inventory;
const remaskWorkspaceRender = render;
const remaskWorkspaceSelectionUi = updateSelectionUi;
function remaskWorkspaceReadState() {
  try {
    const raw = localStorage.getItem(remaskWorkspaceStateKey);
    if (!raw || raw.length > 500000) return null;
    const saved = JSON.parse(raw);
    if (!saved || saved.version !== 1 || !Number.isFinite(saved.saved_at) ||
        Date.now() - saved.saved_at > 7 * 86400000) return null;
    return saved;
  } catch (_) { return null; }
}
function remaskWorkspaceSaveState() {
  if (state.inventory === remaskWorkspaceInitialInventory) return;
  try {
    localStorage.setItem(remaskWorkspaceStateKey, JSON.stringify({version:1, saved_at:Date.now(),
      tab:state.activeTab, selected:Object.fromEntries(Object.entries(state.selected)
        .map(([tab,keys])=>[tab,Array.from(keys).slice(0,5000)])),
      business_filter:Array.from(state.businessFilter).slice(0,5000),
      search:$('workspaceSearch').value, status:$('workspaceStatusFilter').value,
      group:$('workspaceGroupFilter').value, sort:$('workspaceSort').value,
      only_selected:$('onlySelected').checked === true}));
  } catch (_) { /* Quota/disabled storage must not interrupt an operation. */ }
}
const remaskWorkspaceSaved = remaskWorkspaceReadState();
let remaskWorkspaceGroupRestore = remaskWorkspaceSaved?.group || '';
if (remaskWorkspaceSaved) {
  for (const [tab,keys] of Object.entries(remaskWorkspaceSaved.selected || {})) {
    if (!(state.selected[tab] instanceof Set) || !Array.isArray(keys)) continue;
    state.selected[tab] = new Set(keys.slice(0,5000).filter(k=>typeof k==='string' && k.length<=150));
  }
  if (Array.isArray(remaskWorkspaceSaved.business_filter)) {
    state.businessFilter = new Set(remaskWorkspaceSaved.business_filter.slice(0,5000)
      .filter(k=>typeof k==='string' && k.length<=150));
  }
  if (!new URLSearchParams(location.search).has('tab') &&
      Object.hasOwn(state.selected, remaskWorkspaceSaved.tab)) {
    state.activeTab = remaskWorkspaceSaved.tab;
    document.querySelectorAll('.ws-tab').forEach(b=>b.classList.toggle('active', b.dataset.tab===state.activeTab));
  }
  for (const [id,key] of [['workspaceSearch','search'],['workspaceStatusFilter','status'],
                         ['workspaceGroupFilter','group'],['workspaceSort','sort']]) {
    if (typeof remaskWorkspaceSaved[key]==='string') $(id).value=remaskWorkspaceSaved[key].slice(0,500);
  }
  $('onlySelected').checked = remaskWorkspaceSaved.only_selected === true;
}
render = function() {
  if (state.inventory !== remaskWorkspaceInitialInventory) {
    for (const [tab,keys] of Object.entries(state.selected)) {
      // Delivery data is loaded separately. Keep dormant IDs until its rows
      // arrive; selectedRows() still requires a real row before any action.
      if (deliveryTabs.has(tab) && !(state.inventory[tab] || []).length) continue;
      const known = new Set((state.inventory[tab] || []).map(row=>entityKey(tab,row)));
      for (const key of keys) if (!known.has(key)) keys.delete(key);
    }
  }
  const result = remaskWorkspaceRender();
  if (remaskWorkspaceGroupRestore && state.inventory !== remaskWorkspaceInitialInventory) {
    const groups = $('workspaceGroupFilter');
    if (Array.from(groups.options || []).some(o=>o.value===remaskWorkspaceGroupRestore)) {
      groups.value = remaskWorkspaceGroupRestore;
      remaskWorkspaceRender();
    }
    remaskWorkspaceGroupRestore = '';
  }
  remaskWorkspaceSaveState();
  return result;
};
updateSelectionUi = function() {
  const result = remaskWorkspaceSelectionUi();
  remaskWorkspaceSaveState();
  return result;
};
