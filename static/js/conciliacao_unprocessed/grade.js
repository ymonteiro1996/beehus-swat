/* Conciliação (Não Proc.) — Step 1: limiar, filtros, grade e seleção.
   Escopo global compartilhado; ordem importa. */
function onThresholdChange() {
  const raw = document.getElementById("threshold-input").value;
  const v = raw === "" ? 0 : Number(raw);
  if (!isFinite(v) || v < 0 || v > 10) { _setThresholdStatus("valor inválido (0–10)", "#b91c1c"); return; }
  _thresholdPct = v;
  _setThresholdStatus("salvando…", "#9ca3af");
  clearTimeout(_thresholdTimer);
  _thresholdTimer = setTimeout(() => {
    fetch("/api/conciliacao/config", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ diffThresholdPct: _thresholdPct }) })
      .then(r => r.json().then(j => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok) { _setThresholdStatus((j && j.error) || "erro ao salvar", "#b91c1c"); return; }
        _setThresholdStatus("✓ salvo", "#059669");
        setTimeout(() => _setThresholdStatus("", ""), 1500);
        // recarrega as divergências da data atual já com o novo limiar
        const dv = document.getElementById("end-date-input").value;
        if (_company && /^\d{4}-\d{2}-\d{2}$/.test(dv)) selectDate(dv);
      }).catch(() => _setThresholdStatus("erro ao salvar", "#b91c1c"));
  }, 300);
}

function onCompanyChange() {
  _company = document.getElementById("company-select").value;
  _companyName = companyNames[_company] || _company;
  _date = ""; _rows = [];
  document.getElementById("table-section").classList.add("hidden");
  document.getElementById("tbody").innerHTML = "";
  document.getElementById("main-table").style.display = "none";
  document.getElementById("table-msg").textContent = "Selecione uma empresa e uma data.";
  document.getElementById("table-msg").style.display = "block";
  if (!_company) return;
  // Sem cards: carrega direto as divergências da data no campo "Data".
  const v = document.getElementById("end-date-input").value;
  if (v && /^\d{4}-\d{2}-\d{2}$/.test(v)) selectDate(v);
}

let _endDateTimer = null;
function onEndDateChange() {
  if (!_company) return;
  clearTimeout(_endDateTimer);
  _endDateTimer = setTimeout(() => {
    const v = document.getElementById("end-date-input").value;
    if (!v || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return;
    _rows = [];
    selectDate(v);   // sem cards: a data digitada já dispara as divergências
  }, 500);
}

function _loaderHTML(text) {
  return `<span class="wallet-loader"><span class="spin"></span>${esc(text || "Carregando...")}</span>`;
}

function selectDate(date) {
  _date = date;
  _listDate = date;
  // Lista nova = contexto novo: limpa seleção e resultados de diagnóstico.
  _selWallets.clear(); _anchorIdx = null; _diagResults = {};
  document.getElementById("table-section").classList.remove("hidden");
  document.getElementById("main-table").style.display = "none";
  document.getElementById("table-msg").innerHTML = _loaderHTML("Carregando carteiras...");
  document.getElementById("table-msg").style.display = "block";
  document.getElementById("tbody").innerHTML = "";
  fetch(`/api/conciliacao-unprocessed/rows?companyId=${encodeURIComponent(_company)}&date=${encodeURIComponent(_date)}`)
    .then(r => r.json()).then(({ rows }) => {
      if (!rows || !rows.length) { document.getElementById("table-msg").textContent = "Nenhuma divergência para esta data."; return; }
      rows.sort((a, b) => {
        const da = (a.returnNavPerShare != null && a.returnContribution != null) ? Math.abs(a.returnNavPerShare - a.returnContribution) : -Infinity;
        const dbv = (b.returnNavPerShare != null && b.returnContribution != null) ? Math.abs(b.returnNavPerShare - b.returnContribution) : -Infinity;
        return dbv - da;
      });
      _rows = rows;
      document.getElementById("wallet-filter").value = "";
      renderRows(rows);
      document.getElementById("table-msg").style.display = "none";
      document.getElementById("main-table").style.display = "";
    }).catch(() => { document.getElementById("table-msg").textContent = "Erro ao carregar carteiras."; });
}

function renderRow(r, i) {
  const diff = (r.returnNavPerShare != null && r.returnContribution != null) ? r.returnNavPerShare - r.returnContribution : null;
  // Diferença de retorno: vermelho do design (rp-diff-neg) — todas as
  // linhas exibidas já são divergentes (filtro de limiar no backend).
  const diffCls = diff == null ? "" : "rp-diff-neg";
  const on = _selWallets.has(r.walletId);
  return `<tr data-wid="${esc(r.walletId)}" class="${on ? "bg-blue-50" : ""}">
    <td style="text-align:center; position:sticky; left:0; z-index:2; background:#fff; width:40px; min-width:40px; max-width:40px">
      <input type="checkbox" class="row-cb" data-idx="${i}" ${on ? "checked" : ""} onclick="onRowCbClick(event, ${i})">
    </td>
    <td class="rp-tbl-left whitespace-nowrap" style="position:sticky; left:40px; z-index:2; background:#fff">
      <div class="flex items-center gap-1.5">
        <button onclick="openWallet('${esc(r.walletId)}', ${esc(JSON.stringify(r.walletName))})" class="font-medium text-blue-600 hover:text-blue-800 hover:underline text-left">${esc(r.walletName || r.walletId)}</button>
        <button type="button" onclick="copyWalletId('${esc(r.walletId)}', this)" title="Copiar Wallet ID" class="shrink-0 text-gray-400 hover:text-blue-600 p-0.5 rounded leading-none">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </button>
      </div>
    </td>
    <td class="whitespace-nowrap">${fmtMoney(r.nav)}</td>
    <td class="whitespace-nowrap col-opt">${fmtNum(r.navPerShare, 6)}</td>
    <td class="whitespace-nowrap col-opt">${fmtMoney(r.amount)}</td>
    <td class="whitespace-nowrap">${fmtPct(r.returnNavPerShare)}</td>
    <td class="whitespace-nowrap">${fmtPct(r.returnContribution)}</td>
    <td class="whitespace-nowrap ${diffCls}">${fmtPct(diff)}</td>
    <td class="whitespace-nowrap">${fmtMoney(r.gapCash)}</td>
    ${_resultCells(r.walletId)}
  </tr>`;
}

function renderRows(rows) {
  _renderedRows = rows;
  document.getElementById("tbody").innerHTML = rows.map((r, i) => renderRow(r, i)).join("");
  _applyStep1Cols();
  _syncSelHeader();
}

// Re-renderiza só uma linha (após o diagnóstico daquela carteira concluir),
// preservando seleção e scroll.
function _updateRowDiag(wid) {
  const tr = document.querySelector(`#tbody tr[data-wid="${(window.CSS && CSS.escape) ? CSS.escape(wid) : wid}"]`);
  const i = _renderedRows.findIndex(r => r.walletId === wid);
  if (tr && i >= 0) tr.outerHTML = renderRow(_renderedRows[i], i);
}

function filterWallets() {
  const q = (document.getElementById("wallet-filter").value || "").trim().toLowerCase();
  const filtered = q ? _rows.filter(r => (r.walletName || r.walletId).toLowerCase().includes(q)) : _rows;
  renderRows(filtered);
}

function filterWallets() {
  const q = (document.getElementById("wallet-filter").value || "").trim().toLowerCase();
  const filtered = q ? _rows.filter(r => (r.walletName || r.walletId).toLowerCase().includes(q)) : _rows;
  renderRows(filtered);
}

/* ── Seleção múltipla (checkbox + Shift/Ctrl) ─────────────────────────────────
   A seleção é guardada por walletId em `_selWallets`, então sobrevive ao filtro
   e ao re-render. `_anchorIdx` é o índice (em `_renderedRows`) da última linha
   clicada sem Shift — base para a seleção por intervalo. */
function onRowCbClick(ev, idx) {
  const row = _renderedRows[idx];
  if (!row) return;
  if (ev.shiftKey && _anchorIdx != null) {
    // Shift: marca todo o intervalo da âncora até aqui.
    ev.preventDefault();
    const lo = Math.min(_anchorIdx, idx), hi = Math.max(_anchorIdx, idx);
    for (let i = lo; i <= hi; i++) { const w = _renderedRows[i]; if (w) _selWallets.add(w.walletId); }
  } else {
    // Clique simples ou Ctrl/Cmd: alterna só esta linha e move a âncora.
    if (_selWallets.has(row.walletId)) _selWallets.delete(row.walletId); else _selWallets.add(row.walletId);
    _anchorIdx = idx;
  }
  _syncSelection();
}

function toggleSelectAll(on) {
  _renderedRows.forEach(r => { if (on) _selWallets.add(r.walletId); else _selWallets.delete(r.walletId); });
  _anchorIdx = null;
  _syncSelection();
}

// Reflete `_selWallets` no DOM (checkbox + realce) sem re-renderizar a tabela.
function _syncSelection() {
  document.querySelectorAll("#tbody tr").forEach(tr => {
    const wid = tr.getAttribute("data-wid");
    const on = _selWallets.has(wid);
    const cb = tr.querySelector(".row-cb");
    if (cb) cb.checked = on;
    tr.classList.toggle("bg-blue-50", on);
  });
  _syncSelHeader();
}

// Atualiza o checkbox "selecionar tudo", o contador e os botões de ação.
function _syncSelHeader() {
  const total = _renderedRows.length;
  const visSel = _renderedRows.filter(r => _selWallets.has(r.walletId)).length;
  const all = document.getElementById("sel-all-cb");
  if (all) { all.checked = total > 0 && visSel === total; all.indeterminate = visSel > 0 && visSel < total; }
  const n = _selWallets.size;
  const btn = document.getElementById("diagnose-sel-btn");
  if (btn && btn.textContent !== "Diagnosticando...") btn.disabled = n === 0;
  const cnt = document.getElementById("sel-count");
  if (cnt) cnt.textContent = n ? `${n} carteira(s) selecionada(s)` : "";
  // Baixar JSON: ≥1 selecionada já diagnosticada. Enviar via API: ≥1 selecionada
  // diagnosticada COM correções a gerar.
  const sel = _renderedRows.filter(r => _selWallets.has(r.walletId));
  const diagSel = sel.filter(r => (_diagResults[r.walletId] || {}).state === "done");
  const withCorr = diagSel.filter(r => _diagHasCorrections(_diagResults[r.walletId]));
  const dl = document.getElementById("download-corr-btn");
  if (dl && dl.textContent !== "Gerando...") dl.disabled = diagSel.length === 0;
  const sd = document.getElementById("send-corr-btn");
  if (sd && sd.textContent !== "Enviando...") sd.disabled = withCorr.length === 0;
}

function copyWalletId(wid, btn) {
  navigator.clipboard.writeText(wid).then(() => {
    if (!btn) return;
    const old = btn.innerHTML;
    btn.innerHTML = `<span class="text-[11px] text-green-600 font-semibold">✓</span>`;
    setTimeout(() => { btn.innerHTML = old; }, 1200);
  }).catch(() => {});
}

/* ── Mostrar/ocultar colunas opcionais (Cota, Quantidade — e futuras) ──────────
   Reusa o padrão `.cols-collapsed .col-opt { display:none }` (já no <style>):
   a classe entra no wrapper de rolagem da tabela do Step 1. */
function _applyStep1Cols() {
  const wrap = document.querySelector("#wallet-table-body .table-wrap");
  if (wrap) wrap.classList.toggle("cols-collapsed", _step1ColsCollapsed);
}
function toggleStep1Cols() {
  _step1ColsCollapsed = !_step1ColsCollapsed;
  _applyStep1Cols();
  const btn = document.getElementById("step1-cols-btn");
  if (btn) {
    btn.lastChild.textContent = _step1ColsCollapsed ? " Mostrar colunas" : " Ocultar colunas";
    btn.classList.toggle("active", !_step1ColsCollapsed);
  }
}

/* Veredito do Step 7 → rótulo curto + lado culpado + cor, exibido ao lado do
   Novo GAP %. Ver docs/CONCILIACAO_QUAL_LADO.md. SECURITY/TRANSACTION → corrigir
   a contribuição (cota é referência); CASH → âncora é o caixa real;
   LIKELY_WRONG_FORMER_NAV → suspeita da COTA (hipótese, baixa confiança). */
const VERDICT_INFO = {
  NO_GAP:                  { label: "sem gap",                side: "Sem divergência",                                                        cls: "text-green-600" },
  SECURITY_ISSUES:         { label: "→ contrib (ativos)",     side: "Corrigir a contribuição — a cota (returnNavPerShare) é a referência",     cls: "text-indigo-700" },
  TRANSACTION_ISSUES:      { label: "→ contrib (transações)", side: "Corrigir a contribuição — a cota (returnNavPerShare) é a referência",     cls: "text-indigo-700" },
  CASH_ISSUES:             { label: "→ caixa",                side: "Âncora é o caixa real (currentCash), não os retornos",                    cls: "text-amber-700" },
  LIKELY_WRONG_FORMER_NAV: { label: "→ cota (NAV ant.?)",     side: "Suspeita da COTA via formerNav — hipótese por eliminação, baixa confiança", cls: "text-red-600" },
};
function _verdictTag(r) {
  const v = r.verdict;
  if (!v) return "";
  const vi = VERDICT_INFO[v] || { label: v, side: "", cls: "text-gray-500" };
  const tip = (vi.side ? vi.side + " · " : "") + (r.verdictDetail || "");
  return `<div class="text-[9px] leading-tight ${vi.cls}" title="${esc(tip)}">${esc(vi.label)}</div>`;
}

/* ── Células de resultado do diagnóstico (8 colunas) ──────────────────────────
   4 contagens (Provisões / Preços Exec. / IRRF / Aj. Caixa) + 4 do recálculo
   (Novo NAV / Nova Cota / Novo GAP $ / Novo GAP %), preenchidas por
   "Diagnosticar selecionadas". A última célula mostra também o VEREDITO do Step 7
   (qual lado o diagnóstico culpa). Antes do diagnóstico ficam com "—". */
function _resultCells(wid) {
  const r = _diagResults[wid];
  const dash = `<span class="text-gray-300">—</span>`;
  const bl = "border-left:2px solid #c7d2fe";
  if (!r) return `<td style="${bl}">${dash}</td><td>${dash}</td><td>${dash}</td><td>${dash}</td>`
               + `<td style="${bl}">${dash}</td><td>${dash}</td><td>${dash}</td><td>${dash}</td>`;
  if (r.state === "loading")
    return `<td colspan="8" class="rp-tbl-left" style="${bl}"><span class="wallet-loader"><span class="spin"></span>Diagnosticando…</span></td>`;
  if (r.state === "error")
    return `<td colspan="8" class="rp-tbl-left" style="${bl}"><span class="text-red-500 text-[11px]" title="${esc(r.error || "")}">erro: ${esc(r.error || "falha")}</span></td>`;
  if (r.notPreProcessed)
    return `<td colspan="8" class="rp-tbl-left" style="${bl}"><span class="text-amber-600 text-[11px]">posição não pré-processada</span></td>`;
  const c = r.counts || {}, rc = r.recalc || {};
  const cnt = v => v ? `<span class="font-semibold text-indigo-700">${v}</span>` : `<span class="text-gray-300">0</span>`;
  const gapBad = rc.newGapPct != null && Math.abs(nz(rc.newGapPct)) > 1e-6;
  return `
    <td style="${bl}">${cnt(c.provisions)}</td>
    <td>${cnt(c.executionPrices)}</td>
    <td>${cnt(c.withholdingTax)}</td>
    <td>${cnt(c.cashAdjustments)}</td>
    <td class="whitespace-nowrap" style="${bl}">${rc.newNav == null ? dash : fmtMoney(rc.newNav)}</td>
    <td class="whitespace-nowrap">${rc.newNavPerShare == null ? dash : fmtNum(rc.newNavPerShare, 6)}</td>
    <td class="whitespace-nowrap">${rc.newGapCash == null ? dash : fmtMoney(rc.newGapCash)}</td>
    <td class="whitespace-nowrap"><span class="${gapBad ? "text-red-600" : "text-green-600"}">${rc.newGapPct == null ? dash : fmtPct(rc.newGapPct)}</span>${_verdictTag(r)}</td>`;
}

/* ── Diagnosticar carteiras selecionadas (endpoint /diagnose) ──────────────────
   Roda o diagnóstico EXISTENTE por carteira, com concorrência limitada a 3
   (rate limit / 429). Preenche as 8 colunas de resultado. O backend devolve as
   contagens (`counts`), o recálculo NAV/cota/GAP (`recalc`, feito neste projeto)
   e as `suggestions` (usadas no Baixar JSON / Enviar via API). */
