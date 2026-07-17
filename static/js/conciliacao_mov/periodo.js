/* Conciliação (Mov.) — implementação por período (datas, execução).
   Escopo global compartilhado; ordem importa. */
function openImplementChoice() {
  const wids = _selectedDoneWids();
  if (!wids.length) return;
  _gid("impl-choice-sub").textContent = `${wids.length} carteira(s) · origem ${_date}`;
  const m = _gid("impl-choice-modal"); m.style.display = "flex"; m.classList.remove("hidden");
}
function closeImplementChoice() { const m = _gid("impl-choice-modal"); m.style.display = "none"; m.classList.add("hidden"); }
function chooseImplementSingle() { closeImplementChoice(); openImplementModal(); }
function chooseImplementPeriod() { closeImplementChoice(); openPeriodDates(); }

/* ══════════ Implementar em período — seleção de datas (mesmo componente do Processar) ══════════ */
let _period = { candidateDates: [], selectedDates: new Set(), useDateList: false, filterMonthEnd: false };
function _selectedVisibleWids() { return _renderedRows.filter(r => _selWallets.has(r.walletId)).map(r => r.walletId); }
function openPeriodDates() {
  const wids = _selectedVisibleWids();
  if (!wids.length) { alert("Selecione ao menos uma carteira na grade."); return; }
  _period = { candidateDates: [], selectedDates: new Set(), useDateList: false, filterMonthEnd: false };
  _gid("period-dates-sub").textContent = `${wids.length} carteira(s) selecionada(s)`;
  _gid("period-origin-lbl").textContent = _date;
  const range = document.querySelector('input[name="period-mode"][value="range"]'); if (range) range.checked = true;
  _gid("period-ini").value = _targetDate() || _nextBizDay(_date);
  _gid("period-fin").value = "";
  _gid("period-uselist").checked = false;
  _gid("period-monthend").checked = false;
  _gid("period-excel-status").textContent = "";
  _gid("period-dates-status").textContent = "";
  ["exec", "irrf", "recon", "prov"].forEach(k => { const cb = _gid("period-opt-" + k); if (cb) cb.checked = true; });
  onPeriodModeChange("range");
  const m = _gid("period-dates-modal"); m.style.display = "flex"; m.classList.remove("hidden");
}
function closePeriodDates() { const m = _gid("period-dates-modal"); m.style.display = "none"; m.classList.add("hidden"); }
function _periodMode() { const r = document.querySelector('input[name="period-mode"]:checked'); return r ? r.value : "range"; }
function onPeriodModeChange(mode) {
  const isRange = mode === "range";
  _gid("period-fin-wrap").classList.toggle("hidden", !isRange);
  _gid("period-ini-label").textContent = isRange ? "Data inicial" : "Data";
  _gid("period-card").classList.toggle("hidden", !isRange);
  if (!isRange) { _period.useDateList = false; _gid("period-uselist").checked = false; _gid("period-body").classList.add("hidden"); }
  onPeriodRangeChange();
}
function onPeriodRangeChange() {
  if (_periodMode() === "range") {
    const ini = _gid("period-ini").value, fin = _gid("period-fin").value;
    _period.candidateDates = (ini && fin && ini <= fin) ? _bizDays(ini, fin) : [];
  } else _period.candidateDates = [];
  if (_period.useDateList) _renderPeriodPanes();
}
function onPeriodUseListChange() {
  const on = _gid("period-uselist").checked; _period.useDateList = on;
  _gid("period-body").classList.toggle("hidden", !on);
  if (!on) {
    _period.selectedDates = new Set(); _period.filterMonthEnd = false;
    const me = _gid("period-monthend"); if (me) me.checked = false;
    const xs = _gid("period-excel-status"); if (xs) xs.textContent = "";
  } else onPeriodRangeChange();
  _renderPeriodPanes();
}
function onPeriodFilterChange() { _period.filterMonthEnd = _gid("period-monthend").checked; _renderPeriodPanes(); }
function _renderPeriodPanes() {
  const avail = _gid("period-available"), sel = _gid("period-selected");
  if (!avail || !sel) return;
  if (!_period.useDateList) {
    avail.innerHTML = ""; sel.innerHTML = "";
    _gid("period-available-count").textContent = "0"; _gid("period-selected-count").textContent = "0";
    _gid("period-monthend-badge").classList.add("hidden"); return;
  }
  let cand = _period.candidateDates;
  if (_period.filterMonthEnd) cand = cand.filter(d => _isMonthEndBiz(d));
  const selSet = _period.selectedDates;
  const available = cand.filter(d => !selSet.has(d)), selected = Array.from(selSet).sort();
  avail.innerHTML = available.map(d => `<option value="${d}">${d}</option>`).join("");
  sel.innerHTML = selected.map(d => `<option value="${d}">${d}</option>`).join("");
  _gid("period-available-count").textContent = available.length;
  _gid("period-selected-count").textContent = selected.length;
  _gid("period-monthend-badge").classList.toggle("hidden", !_period.filterMonthEnd);
}
function _periodHi(id) { return Array.from(_gid(id).selectedOptions).map(o => o.value); }
function periodAddSelected() { _periodHi("period-available").forEach(d => _period.selectedDates.add(d)); _renderPeriodPanes(); }
function periodAddAll() { Array.from(_gid("period-available").options).forEach(o => _period.selectedDates.add(o.value)); _renderPeriodPanes(); }
function periodRemoveSelected() { _periodHi("period-selected").forEach(d => _period.selectedDates.delete(d)); _renderPeriodPanes(); }
function periodRemoveAll() { _period.selectedDates.clear(); _renderPeriodPanes(); }
async function onPeriodExcelChosen(ev) {
  const file = ev.target.files && ev.target.files[0]; if (!file) return;
  const status = _gid("period-excel-status"); status.textContent = "Processando…";
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/beehus/util/parse-dates-excel", { method: "POST", body: fd });
    const body = await r.json().catch(() => null);
    if (!r.ok) { status.textContent = `falha (${r.status})`; return; }
    const dates = (body && body.dates) || [];
    if (!dates.length) { status.textContent = "nenhuma data encontrada"; return; }
    if (!_period.useDateList) { _gid("period-uselist").checked = true; onPeriodUseListChange(); }
    dates.forEach(d => _period.selectedDates.add(d)); _renderPeriodPanes();
    status.textContent = `${dates.length} data(s) adicionadas a "Selecionadas"`;
  } catch (e) { status.textContent = "erro de rede"; } finally { ev.target.value = ""; }
}
/* Resolve a escolha do operador numa lista ordenada de datas YYYY-MM-DD. */
function _periodResolveDates() {
  if (_periodMode() === "single") { const d = _gid("period-ini").value; return d ? [d] : []; }
  if (_period.useDateList && _period.selectedDates.size > 0) return Array.from(_period.selectedDates).sort();
  const ini = _gid("period-ini").value, fin = _gid("period-fin").value;
  if (!ini || !fin || ini > fin) return [];
  return _bizDays(ini, fin);
}
function confirmPeriodDates() {
  const status = _gid("period-dates-status");
  const wids = _selectedVisibleWids();
  if (!wids.length) { status.textContent = "Nenhuma carteira selecionada."; return; }
  const days = _periodResolveDates();
  if (!days.length) { status.textContent = "Selecione ao menos uma data válida."; return; }
  // Alvos = datas > origem, em ordem crescente; encadeia a partir da origem da grade.
  const targets = Array.from(new Set(days)).filter(d => d > _date).sort();
  if (!targets.length) { status.textContent = `Selecione datas posteriores à origem (${_date}).`; return; }
  const pairs = []; let src = _date;
  targets.forEach(t => { pairs.push([src, t]); src = t; });
  const opts = { exec: _gid("period-opt-exec").checked, irrf: _gid("period-opt-irrf").checked,
                 recon: _gid("period-opt-recon").checked, prov: _gid("period-opt-prov").checked };
  if (!confirm(`Implementar ${wids.length} carteira(s) em ${pairs.length} passo(s) encadeado(s)?\n` +
               `De ${_date} até ${targets[targets.length - 1]}.\n` +
               `O envio é sequencial e grava na API Beehus a cada passo.`)) return;
  closePeriodDates();
  _runPeriod(pairs, wids, opts);
}

/* ══════════ Implementar em período — execução sequencial (roll-forward) ══════════ */
let _periodDone = true;
function openPeriodRun(pairs) {
  _periodDone = false;
  _gid("period-run-sub").textContent = `${pairs.length} passo(s) · encadeado`;
  _gid("period-run-status").textContent = "";
  _gid("period-run-close").disabled = true;
  const rows = pairs.map((p, i) => `<tr id="prun-${i}" class="border-t border-gray-100">
      <td class="rp-tbl-left" style="font-family:ui-monospace,Menlo,Consolas,monospace">${esc(p[0])} → ${esc(p[1])}</td>
      <td class="rp-tbl-left"><span class="sp-pill sp-pill-muted">pendente</span></td>
      <td class="rp-tbl-left text-gray-400">—</td></tr>`).join("");
  _gid("period-run-body").innerHTML =
    `<div style="overflow:auto"><table class="rp-tbl"><thead><tr>
       <th class="rp-tbl-left">Origem → Alvo</th><th class="rp-tbl-left">Status</th><th class="rp-tbl-left">Detalhe</th></tr></thead>
       <tbody>${rows}</tbody></table></div>`;
  const m = _gid("period-run-modal"); m.style.display = "flex"; m.classList.remove("hidden");
}
function _prunRow(i, kind, detail) {
  const tr = _gid("prun-" + i); if (!tr) return;
  const cells = tr.querySelectorAll("td");
  const pill = kind === "ok" ? '<span class="sp-pill sp-pill-success">ok</span>'
    : kind === "error" ? '<span class="sp-pill sp-pill-error">erro</span>'
    : kind === "running" ? '<span class="sp-pill sp-pill-info">processando…</span>'
    : kind === "skipped" ? '<span class="sp-pill sp-pill-muted">ignorado</span>'
    : '<span class="sp-pill sp-pill-muted">pendente</span>';
  cells[1].innerHTML = pill;
  if (detail !== undefined) cells[2].innerHTML = `<span class="text-[11px]">${esc(detail)}</span>`;
}
function closePeriodRun() { if (!_periodDone) return; const m = _gid("period-run-modal"); m.style.display = "none"; m.classList.add("hidden"); }
async function _runPeriod(pairs, wids, opts) {
  openPeriodRun(pairs);
  let okCount = 0, failed = false;
  for (let i = 0; i < pairs.length; i++) {
    const [src, tgt] = pairs[i];
    _prunRow(i, "running", "");
    _gid("period-run-status").textContent = `Passo ${i + 1}/${pairs.length}: ${src} → ${tgt}`;
    try {
      // 1) Projeta o passo (mesma rota da grade) — dá os artefatos por carteira p/ filtrar.
      const proj = await _post("/api/conciliacao-mov/movimentar-batch", { companyId: _company, walletIds: wids, sourceDate: src, targetDate: tgt });
      if (proj && proj.error) throw new Error(proj.error);
      const byId = {}; ((proj && proj.results) || []).forEach(d => { if (d && d.walletId) byId[d.walletId] = d; });
      const base = { companyId: _company, sourceDate: src, targetDate: tgt };
      const notes = [];
      // 2) Aplica na ordem (só carteiras com algo a fazer), respeitando os checkboxes.
      if (opts.exec) { const ww = wids.filter(w => (((byId[w] || {}).executionPriceFixes) || []).length); if (ww.length) { const d = await _post("/api/conciliacao-mov/execution-prices", { ...base, walletIds: ww }); if (d && d.error) throw new Error("preços: " + d.error); notes.push(`preços ${d.updated || 0}u/${d.created || 0}c`); } }
      if (opts.irrf) { const ww = wids.filter(w => (((byId[w] || {}).irrf) || []).some(e => !e.covered)); if (ww.length) { const d = await _post("/api/conciliacao-mov/irrf", { ...base, walletIds: ww }); if (d && d.error) throw new Error("IRRF: " + d.error); notes.push(`IRRF ${d.created || 0}`); } }
      if (opts.recon) { const ww = wids.filter(w => (((byId[w] || {}).reconTransactions) || []).length); if (ww.length) { const d = await _post("/api/conciliacao-mov/reconcile-txn", { ...base, walletIds: ww }); if (d && d.error) throw new Error("ajustes: " + d.error); notes.push(`ajustes ${d.created || 0}`); } }
      if (opts.prov) { const ww = wids.filter(w => _provCount(byId[w] || {})); if (ww.length) { const d = await _post("/api/conciliacao-mov/provisions", { ...base, walletIds: ww }); if (d && d.error) throw new Error("provisões: " + d.error); notes.push(`prov ${d.created || 0}` + (d.skipped ? `/${d.skipped}ig` : "")); } }
      // 3) Upload da unprocessed — SEMPRE (é o que materializa a origem do próximo passo).
      const du = await _post("/api/conciliacao-mov/apply", { ...base, walletIds: wids });
      if (du && du.error) throw new Error("upload: " + du.error);
      notes.push(`upload ${du.wallets || 0}w/${du.rows || 0}l`);
      _prunRow(i, "ok", notes.join(" · ") || "ok");
      okCount++;
    } catch (e) {
      _prunRow(i, "error", (e && e.message) || "falha");
      failed = true;
      // Encadeamento: o próximo passo depende deste (origem = este alvo) → interrompe.
      for (let j = i + 1; j < pairs.length; j++) _prunRow(j, "skipped", "encadeamento interrompido");
      break;
    }
  }
  _periodDone = true;
  _gid("period-run-status").textContent = failed
    ? `Interrompido após ${okCount} passo(s) — o encadeamento depende do passo anterior.`
    : `Concluído: ${okCount} passo(s).`;
  _gid("period-run-close").disabled = false;
  // Refresca a grade (reprojeta as carteiras na origem/alvo atuais da tela).
  _reprojectWids(wids).then(() => _syncSelHeader());
}

/* ── Helpers de data (dias úteis / fim de mês útil) — espelham o Processar ─────── */
function _parseLocal(s) { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ""); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(s); }
function _bizDays(ini, fin) {
  const out = [], cur = _parseLocal(ini), end = _parseLocal(fin);
  while (cur <= end) {
    const dow = cur.getDay();
    if (dow !== 0 && dow !== 6) out.push(`${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(cur.getDate()).padStart(2, "0")}`);
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}
function _isMonthEndBiz(s) {
  const d = _parseLocal(s), last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
  while (last.getDay() === 0 || last.getDay() === 6) last.setDate(last.getDate() - 1);
  return d.getFullYear() === last.getFullYear() && d.getMonth() === last.getMonth() && d.getDate() === last.getDate();
}
