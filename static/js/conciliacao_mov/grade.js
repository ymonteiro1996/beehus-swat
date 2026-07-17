/* Conciliação (Mov.) — Step 1: grade, filtros, what-if e seleção.
   Escopo global compartilhado; ordem importa. */
function selectDate(date) {
  _date = date;
  _selWallets.clear(); _anchorIdx = null; _movResults = {};
  document.getElementById("table-section").classList.remove("hidden");
  document.getElementById("main-table").style.display = "none";
  document.getElementById("table-msg").innerHTML = _loaderHTML("Carregando carteiras...");
  document.getElementById("table-msg").style.display = "block";
  document.getElementById("tbody").innerHTML = "";
  const tgt = _targetDate();
  fetch(`/api/conciliacao-mov/rows?companyId=${encodeURIComponent(_company)}&date=${encodeURIComponent(_date)}&targetDate=${encodeURIComponent(tgt || "")}`)
    .then(r => r.json()).then(({ rows }) => {
      if (!rows || !rows.length) { document.getElementById("table-msg").textContent = "Nenhuma carteira com posição não-processada na origem."; return; }
      rows.sort((a, b) => Math.abs(nz(b.sysGapPct)) - Math.abs(nz(a.sysGapPct)));   // divergentes (sistema) no topo
      _rows = rows;
      document.getElementById("wallet-filter").value = "";
      _applyFilters();
      document.getElementById("table-msg").style.display = "none";
      document.getElementById("main-table").style.display = "";
    }).catch(() => { document.getElementById("table-msg").textContent = "Erro ao carregar carteiras."; });
}

/* ── Render ──────────────────────────────────────────────────────────────────── */
function renderRow(r, i, th) {
  const on = _selWallets.has(r.walletId);
  const _th = th === undefined ? _gapThreshDec() : th;   // lote passa o limiar 1×; linha avulsa relê
  const sysGapBad = r.sysGapPct != null && Math.abs(nz(r.sysGapPct)) > (_th > 0 ? _th : 1e-6);
  return `<tr data-wid="${esc(r.walletId)}" class="${on ? "bg-blue-50" : ""}">
    <td style="text-align:center; position:sticky; left:0; z-index:2; background:#fff; width:40px; min-width:40px; max-width:40px">
      <input type="checkbox" class="row-cb" data-idx="${i}" ${on ? "checked" : ""} onclick="onRowCbClick(event, ${i})">
    </td>
    <td class="rp-tbl-left whitespace-nowrap" style="position:sticky; left:40px; z-index:2; background:#fff">
      <div class="flex items-center gap-1.5">
        <span class="font-medium text-gray-700">${esc(r.walletName || r.walletId)}</span>
        <button type="button" onclick="copyWalletId('${esc(r.walletId)}', this)" title="Copiar Wallet ID" class="shrink-0 text-gray-400 hover:text-blue-600 p-0.5 rounded leading-none">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </button>
      </div>
    </td>
    <td class="whitespace-nowrap">${fmtMoney(r.sysNav)}</td>
    <td class="whitespace-nowrap col-opt">${fmtNum(r.sysNavPerShare, 6)}</td>
    <td class="whitespace-nowrap">${fmtMoney(r.sysGapCash)}</td>
    <td class="whitespace-nowrap ${sysGapBad ? "text-red-600" : (r.sysGapPct == null ? "text-gray-300" : "text-green-600")}">${fmtPct(r.sysGapPct)}</td>
    ${_simCells(r.walletId)}
  </tr>`;
}
function renderRows(rows) {
  _renderedRows = rows;
  const th = _gapThreshDec();   // lê o limiar UMA vez p/ o lote todo (não por linha)
  document.getElementById("tbody").innerHTML = rows.map((r, i) => renderRow(r, i, th)).join("");
  _applyCols(); _syncSelHeader();
}
function _updateRow(wid) {
  const tr = document.querySelector(`#tbody tr[data-wid="${_cssEsc(wid)}"]`);
  const i = _renderedRows.findIndex(r => r.walletId === wid);
  if (tr && i >= 0) tr.outerHTML = renderRow(_renderedRows[i], i);
}
function _gapThreshDec() {
  const v = Number(document.getElementById("threshold-input").value || 0);
  return (isFinite(v) && v > 0) ? v / 100 : 0;   // limiar em % → decimal
}
function _applyFilters() {
  // Mostra TODAS as carteiras projetáveis (inclui GAP=0); o limiar só DESTACA
  // divergentes em vermelho (não esconde) — senão a grade some num dia conciliado.
  // O seletor opcional de groupings/carteiras (_scopeWids) restringe o conjunto exibido.
  const q = (document.getElementById("wallet-filter").value || "").trim().toLowerCase();
  const scope = _scopeWids();
  let rows = _rows;
  if (scope) rows = rows.filter(r => scope.has(r.walletId));
  if (q) rows = rows.filter(r => (r.walletName || r.walletId).toLowerCase().includes(q));
  renderRows(rows);
}
function filterWallets() { _applyFilters(); }
function onTargetDateChange() {
  // Não recarrega automaticamente — o operador clica "Listar carteiras" p/ atualizar
  // as colunas do sistema (que dependem do alvo).
  if (!document.getElementById("table-section").classList.contains("hidden")) _markGridStale(true);
}

/* What-if da GRADE (tela inicial): recalcula NAV/cota/GAP da carteira descontando as
   provisões AUTO-ignoradas — as recon que DUPLICAM uma oficial do sistema
   (`duplicatesOfficial`), que dobrariam o `sim_nav`. Mesma fórmula do detalhe
   (`_whatIfProj`), mas SEM depender de `_detProvByKey` (a grade é stateless por-carteira;
   os toggles manuais são exploração do detalhe, a grade mostra o default recomendado). */
function _walletWhatIf(wid) {
  const r = _movResults[wid];
  if (!r || r.state !== "done" || r.simNav == null) return null;
  let drop = 0, count = 0;
  (r.reconProvisions || []).forEach(p => { if (p.duplicatesOfficial) { drop += nz(p.balance); count++; } });
  (r.provisions || []).forEach(p => { if (p.duplicatesOfficial) { drop += nz(p.balance); count++; } });
  if (!count) return null;
  const shares = nz(r.shares), fnps = nz(r.formerNavPerShare), fnav = nz(r.formerNav), inflows = nz(r.inflows);
  const simNav = Math.round((nz(r.simNav) - drop) * 100) / 100;
  const simNps = shares ? (simNav - inflows) / shares : null;
  const retNps = (simNps != null && fnps) ? (simNps / fnps - 1) : null;
  const retC = r.simReturnContribution;
  const gapPct = (retNps != null && retC != null) ? (retNps - nz(retC)) : null;
  const gapCash = (gapPct != null && fnav) ? Math.round(gapPct * fnav * 100) / 100 : null;
  return { simNav, simNps, retNps, gapPct, gapCash, drop, count };
}
function _simCells(wid) {
  const r = _movResults[wid];
  const dash = `<span class="text-gray-300">—</span>`;
  const bl = "border-left:2px solid #c7d2fe";
  if (!r) return `<td style="${bl}">${dash}</td><td>${dash}</td><td>${dash}</td><td>${dash}</td><td style="text-align:center">${dash}</td><td style="text-align:center">${dash}</td>`;
  if (r.state === "loading") return `<td colspan="6" class="rp-tbl-left" style="${bl}"><span class="wallet-loader"><span class="spin"></span>Movimentando…</span></td>`;
  if (r.state === "error") return `<td colspan="6" class="rp-tbl-left" style="${bl}"><span class="text-red-500 text-[11px]" title="${esc(r.error || "")}">erro: ${esc(r.error || "falha")}</span></td>`;
  // Números da tela inicial descontam as provisões auto-ignoradas (duplicatas do sistema).
  const wi = _walletWhatIf(wid);
  const simNav = wi ? wi.simNav : r.simNav, simNps = wi ? wi.simNps : r.simNavPerShare;
  const gapCash = wi ? wi.gapCash : r.simGapCash, gapPct = wi ? wi.gapPct : r.simGapPct;
  const gapBad = gapPct != null && Math.abs(nz(gapPct)) > 1e-6;
  // Coluna final: provisões IGNORADAS (duplicatas do sistema) / SUGERIDAS (a gerar = motor + recon).
  const nIgn = wi ? wi.count : 0, nSug = _provCount(r);
  const provCol = nSug
    ? `<span${nIgn ? ' class="text-amber-700" style="font-weight:600"' : ' class="text-gray-500"'} title="${nIgn} provisão(ões) ignorada(s) (duplicata(s) do sistema) de ${nSug} sugerida(s) a gerar">${nIgn} / ${nSug}</span>`
    : dash;
  return `
    <td class="whitespace-nowrap" style="${bl}">${fmtMoney(simNav)}</td>
    <td class="whitespace-nowrap">${fmtNum(simNps, 6)}</td>
    <td class="whitespace-nowrap">${fmtMoney(gapCash)}</td>
    <td class="whitespace-nowrap ${gapBad ? "text-red-600" : "text-green-600"}">${fmtPct(gapPct)}</td>
    <td class="whitespace-nowrap" style="text-align:center">${provCol}</td>
    <td style="text-align:center"><button type="button" onclick="openDetail('${esc(wid)}')" title="Abrir detalhamento (securities, provisões, transações, caixa)" class="text-[11px] px-1.5 py-0.5 rounded bg-indigo-50 border border-indigo-200 text-indigo-700 hover:bg-indigo-100">🔍</button></td>`;
}

/* ── Seleção múltipla ───────────────────────────────────────────────────────── */
function onRowCbClick(ev, idx) {
  const row = _renderedRows[idx]; if (!row) return;
  if (ev.shiftKey && _anchorIdx != null) {
    ev.preventDefault();
    const lo = Math.min(_anchorIdx, idx), hi = Math.max(_anchorIdx, idx);
    for (let i = lo; i <= hi; i++) { const w = _renderedRows[i]; if (w) _selWallets.add(w.walletId); }
  } else {
    if (_selWallets.has(row.walletId)) _selWallets.delete(row.walletId); else _selWallets.add(row.walletId);
    _anchorIdx = idx;
  }
  _syncSelection();
}
function toggleSelectAll(on) {
  _renderedRows.forEach(r => { if (on) _selWallets.add(r.walletId); else _selWallets.delete(r.walletId); });
  _anchorIdx = null; _syncSelection();
}
function _syncSelection() {
  document.querySelectorAll("#tbody tr").forEach(tr => {
    const wid = tr.getAttribute("data-wid"); const on = _selWallets.has(wid);
    const cb = tr.querySelector(".row-cb"); if (cb) cb.checked = on;
    tr.classList.toggle("bg-blue-50", on);
  });
  _syncSelHeader();
}
function _syncSelHeader() {
  const total = _renderedRows.length;
  const visSel = _renderedRows.filter(r => _selWallets.has(r.walletId)).length;
  const all = document.getElementById("sel-all-cb");
  if (all) { all.checked = total > 0 && visSel === total; all.indeterminate = visSel > 0 && visSel < total; }
  const n = _selWallets.size;
  const mv = document.getElementById("movimentar-btn");
  if (mv && mv.textContent !== "Movimentando...") mv.disabled = n === 0;
  const cnt = document.getElementById("sel-count");
  if (cnt) cnt.textContent = n ? `${n} carteira(s) selecionada(s)` : "";
  // Implementar (revisar+aplicar tudo) e Relatório: dependem de carteiras JÁ movimentadas.
  const done = _renderedRows.filter(r => _selWallets.has(r.walletId) && (_movResults[r.walletId] || {}).state === "done");
  const imp = document.getElementById("implementar-btn"); if (imp) imp.disabled = done.length === 0;
  const rep = document.getElementById("report-btn"); if (rep) rep.disabled = done.length === 0;
}
function copyWalletId(wid, btn) {
  navigator.clipboard.writeText(wid).then(() => {
    if (!btn) return; const old = btn.innerHTML;
    btn.innerHTML = `<span class="text-[11px] text-green-600 font-semibold">✓</span>`;
    setTimeout(() => { btn.innerHTML = old; }, 1200);
  }).catch(() => {});
}

/* ── Colunas opcionais ──────────────────────────────────────────────────────── */
function _applyCols() {
  const wrap = document.querySelector("#wallet-table-body .table-wrap");
  if (wrap) wrap.classList.toggle("cols-collapsed", _colsCollapsed);
}
function toggleCols() {
  _colsCollapsed = !_colsCollapsed; _applyCols();
  const btn = document.getElementById("cols-btn");
  if (btn) { btn.lastChild.textContent = _colsCollapsed ? " Mostrar colunas" : " Ocultar colunas"; btn.classList.toggle("active", !_colsCollapsed); }
}

/* ── Movimentar (projeção) ──────────────────────────────────────────────────── */
