/* Conciliação (Mov.) — painel de escopo (groupings/wallets).
   Escopo global compartilhado; ordem importa. */
function _gid(id) { return document.getElementById(id); }

/* ── Botão "Listar carteiras" / estado stale ─────────────────────────────────── */
function _markGridStale(on) {
  const btn = _gid("load-wallets-btn"); if (!btn) return;
  btn.textContent = on ? "Atualizar listagem" : "Listar carteiras";
  btn.style.boxShadow = on ? "0 0 0 2px #f59e0b" : "";
  btn.title = on ? "As datas mudaram — clique para recarregar a listagem"
                 : "Carregar a listagem de carteiras da empresa e origem selecionadas";
}
function loadWallets() {
  if (!_company) { alert("Selecione uma empresa."); return; }
  const v = _gid("end-date-input").value;
  if (!v || !/^\d{4}-\d{2}-\d{2}$/.test(v)) { alert("Informe a data de origem (Origem)."); return; }
  const td = _targetDate();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(td) || !(td > v)) { alert("Data-alvo inválida (deve ser posterior à origem)."); return; }
  _gid("mov-detail").classList.add("hidden");
  _gid("mov-filters").classList.remove("hidden");
  _markGridStale(false);
  selectDate(v);
}

/* ── Seletor de groupings / carteiras (escopo opcional da grade) ──────────────── */
let _scopeState = { groupings: [], walletsAll: [], selGrp: new Set(), selWlt: [] };
function toggleScopePanel() {
  const p = _gid("mov-scope-panel"); if (!p) return;
  p.classList.toggle("hidden");
  const btn = _gid("mov-scope-toggle"); if (btn) btn.classList.toggle("active", !p.classList.contains("hidden"));
}
function _scopeWalletFilterFromGroupings() {
  const sel = _scopeState.selGrp;
  if (!sel || !sel.size) return null;
  const out = new Set();
  (_scopeState.groupings || []).forEach(g => { if (sel.has(g.id)) (g.walletIds || []).forEach(w => out.add(w)); });
  return out;
}
/* Conjunto de walletIds do escopo (ou null = todas). Carteiras selecionadas têm
   precedência; sem elas, a união dos groupings selecionados. */
function _scopeWids() {
  if (!_scopeState) return null;
  if (_scopeState.selWlt.length) return new Set(_scopeState.selWlt);
  const gf = _scopeWalletFilterFromGroupings();
  return (gf && gf.size) ? gf : null;
}
function _renderScopeGroupingPanes() {
  const availEl = _gid("cmov-grp-available"), selEl = _gid("cmov-grp-selected");
  if (!availEl || !selEl) return;
  const all = _scopeState.groupings || [], sel = _scopeState.selGrp;
  const available = all.filter(g => !sel.has(g.id)), selected = all.filter(g => sel.has(g.id));
  const opt = g => `<option value="${esc(g.id)}" title="${esc(g.id)}">${esc(g.name || g.id)}</option>`;
  availEl.innerHTML = available.map(opt).join("");
  selEl.innerHTML = selected.map(opt).join("");
  _gid("cmov-grp-available-count").textContent = available.length;
  _gid("cmov-grp-selected-count").textContent = selected.length;
}
function _scopeWltOpt(w) {
  return `<option value="${esc(w.id)}" title="${esc(w.id)}">${esc(w.name || w.id)} <span class="text-[10px] text-gray-400">[${esc(w.currencyId || "")}]</span></option>`;
}
function _renderScopeWalletPanes() {
  const availEl = _gid("cmov-wlt-available"), selEl = _gid("cmov-wlt-selected");
  if (!availEl || !selEl) return;
  const all = _scopeState.walletsAll || [], selSet = new Set(_scopeState.selWlt || []);
  const grpFilter = _scopeWalletFilterFromGroupings();
  let available = all.filter(w => !selSet.has(w.id));
  if (grpFilter) available = available.filter(w => grpFilter.has(w.id));
  const wById = Object.fromEntries(all.map(w => [w.id, w]));
  availEl.innerHTML = available.map(_scopeWltOpt).join("");
  selEl.innerHTML = (_scopeState.selWlt || []).map(id => _scopeWltOpt(wById[id] || { id, name: id, currencyId: "" })).join("");
  _gid("cmov-wlt-available-count").textContent = available.length;
  _gid("cmov-wlt-selected-count").textContent = (_scopeState.selWlt || []).length;
  const pill = _gid("cmov-wlt-available-filter"); if (pill) pill.classList.toggle("hidden", !grpFilter);
}
function _renderScopePanes() { _renderScopeGroupingPanes(); _renderScopeWalletPanes(); }
function _onScopeChanged() {
  _renderScopePanes();
  const scope = _scopeWids();
  const st = _gid("cmov-scope-status");
  if (st) st.textContent = scope ? `Escopo: ${scope.size} carteira(s)` : "Escopo: todas as carteiras da empresa";
  if (_rows.length) _applyFilters();   // re-filtra a grade já carregada
}
function _loadScopeData() {
  _scopeState = { groupings: [], walletsAll: [], selGrp: new Set(), selWlt: [] };
  const gs = _gid("cmov-grp-excel-status"); if (gs) gs.textContent = "";
  const ws = _gid("cmov-wlt-excel-status"); if (ws) ws.textContent = "";
  if (!_company) { _onScopeChanged(); return; }
  const st = _gid("cmov-scope-status"); if (st) st.textContent = "Carregando groupings/carteiras…";
  Promise.all([
    fetch(`/api/beehus/filters/groupings?companyId=${encodeURIComponent(_company)}`).then(r => r.json()).catch(() => []),
    fetch(`/api/excecoes/wallets?companyId=${encodeURIComponent(_company)}`).then(r => r.json()).catch(() => ({ wallets: [] })),
  ]).then(([g, w]) => {
    _scopeState.groupings = Array.isArray(g) ? g : ((g && g.body) || []);
    _scopeState.walletsAll = (w && w.wallets) || [];
    _onScopeChanged();
  });
}
function clearScope() { _scopeState.selGrp.clear(); _scopeState.selWlt = []; _onScopeChanged(); }
/* Groupings transfer */
function _grpHi(suffix) { const el = _gid("cmov-grp-" + suffix); return el ? [...el.selectedOptions].map(o => o.value) : []; }
function onScopeGroupingAddSelected() { _grpHi("available").forEach(id => _scopeState.selGrp.add(id)); _onScopeChanged(); }
function onScopeGroupingAddAll() { (_scopeState.groupings || []).forEach(g => _scopeState.selGrp.add(g.id)); _onScopeChanged(); }
function onScopeGroupingRemoveSelected() { _grpHi("selected").forEach(id => _scopeState.selGrp.delete(id)); _onScopeChanged(); }
function onScopeGroupingRemoveAll() { _scopeState.selGrp.clear(); _onScopeChanged(); }
/* Wallets transfer (ordem de inserção preservada) */
function _wltHi(suffix) { const el = _gid("cmov-wlt-" + suffix); return el ? [...el.selectedOptions].map(o => o.value) : []; }
function _wltAdd(ids) { const seen = new Set(_scopeState.selWlt); ids.forEach(id => { if (id && !seen.has(id)) { _scopeState.selWlt.push(id); seen.add(id); } }); _onScopeChanged(); }
function _wltRemove(ids) { const drop = new Set(ids); _scopeState.selWlt = _scopeState.selWlt.filter(id => !drop.has(id)); _onScopeChanged(); }
function onScopeWalletAddSelected() { _wltAdd(_wltHi("available")); }
function onScopeWalletAddAll() { const el = _gid("cmov-wlt-available"); if (!el) return; _wltAdd([...el.options].map(o => o.value)); }
function onScopeWalletRemoveSelected() { _wltRemove(_wltHi("selected")); }
function onScopeWalletRemoveAll() { _wltRemove([..._scopeState.selWlt]); }
async function _scopeExcel(ev, kind) {
  const file = ev.target.files && ev.target.files[0]; if (!file) return;
  const status = _gid(kind === "grp" ? "cmov-grp-excel-status" : "cmov-wlt-excel-status");
  if (status) status.textContent = "Processando…";
  const known = new Set((kind === "grp" ? (_scopeState.groupings || []).map(g => g.id) : (_scopeState.walletsAll || []).map(w => w.id)));
  if (!known.size) { if (status) status.textContent = "Selecione uma empresa primeiro."; ev.target.value = ""; return; }
  try {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/beehus/util/parse-strings-excel", { method: "POST", body: fd });
    ev.target.value = "";
    const body = await r.json().catch(() => null);
    if (!r.ok) { if (status) status.textContent = `falha: ${(body && body.error) || ("HTTP " + r.status)}`; return; }
    const input = (body && body.values) || [], matched = input.filter(id => known.has(id));
    const unmatched = input.length - matched.length;
    if (!input.length) { if (status) status.textContent = "nenhum ID encontrado"; return; }
    let added = 0;
    if (kind === "grp") { matched.forEach(id => { if (!_scopeState.selGrp.has(id)) { _scopeState.selGrp.add(id); added++; } }); }
    else { const before = _scopeState.selWlt.length; _wltAdd(matched); added = _scopeState.selWlt.length - before; }
    if (status) status.textContent = `${added} ${kind === "grp" ? "grouping" : "carteira"}(s) adicionado(s)` + (unmatched ? ` · ${unmatched} ID(s) ignorado(s)` : "");
    _onScopeChanged();
  } catch (e) { if (status) status.textContent = "erro de rede"; ev.target.value = ""; }
}
function onScopeGroupingExcel(ev) { return _scopeExcel(ev, "grp"); }
function onScopeWalletExcel(ev) { return _scopeExcel(ev, "wlt"); }

/* ══════════ Implementar — escolha data única / período ══════════ */
