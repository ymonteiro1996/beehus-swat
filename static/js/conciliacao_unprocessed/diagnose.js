/* Conciliação (Não Proc.) — Step 1: diagnóstico, correções e navegação de datas.
   Escopo global compartilhado; ordem importa. */
function diagnoseSelected() {
  const wids = _renderedRows.filter(r => _selWallets.has(r.walletId)).map(r => r.walletId);
  if (!wids.length) return;
  const btn = document.getElementById("diagnose-sel-btn");
  btn.disabled = true; btn.textContent = "Diagnosticando...";
  let done = 0; _diagProgress(`0/${wids.length}`);
  wids.forEach(w => { _diagResults[w] = { state: "loading" }; _updateRowDiag(w); });
  _runPool(wids, 3, w => _diagnoseOne(w).finally(() => { done++; _diagProgress(`${done}/${wids.length}`); }))
    .then(() => { btn.textContent = "Diagnosticar selecionadas"; _diagProgress(""); _syncSelHeader(); });
}

function _diagnoseOne(wid) {
  return fetch(`/api/conciliacao-unprocessed/diagnose?walletId=${encodeURIComponent(wid)}&date=${encodeURIComponent(_date)}&companyId=${encodeURIComponent(_company)}`)
    .then(r => r.json())
    .then(d => {
      if (d && d.error) _diagResults[wid] = { state: "error", error: d.error };
      else _diagResults[wid] = {
        state: "done", counts: d.counts || {}, recalc: d.recalc || {},
        suggestions: d.suggestions || {}, gap: d.gap || {},
        verdict: d.verdict, verdictDetail: d.verdictDetail,
        notPreProcessed: !!d.notPreProcessed,
      };
    })
    .catch(() => { _diagResults[wid] = { state: "error", error: "falha ao diagnosticar" }; })
    .finally(() => _updateRowDiag(wid));
}

// Pool com concorrência `limit`; resolve com a lista de resultados (na ordem).
function _runPool(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  const next = () => {
    if (i >= items.length) return Promise.resolve();
    const idx = i++;
    return Promise.resolve(fn(items[idx], idx)).then(v => { out[idx] = v; return next(); });
  };
  return Promise.all(Array.from({ length: Math.min(limit, items.length) }, next)).then(() => out);
}

function _diagProgress(t) {
  const el = document.getElementById("diag-progress");
  if (el) el.textContent = t ? `diagnosticando ${t}` : "";
}

function _diagHasCorrections(r) {
  if (!r || r.state !== "done") return false;
  const c = r.counts || {};
  return (c.provisions || 0) + (c.executionPrices || 0) + (c.withholdingTax || 0) + (c.cashAdjustments || 0) > 0;
}

/* ── Tudo que foi calculado → itens de correção por carteira ───────────────────
   Converte as `suggestions` de uma carteira nos `items` que os endpoints
   generate-* esperam (mesmo mapeamento do modal do Step 2). */
function _walletItems(wid) {
  const s = (_diagResults[wid] || {}).suggestions || {};
  const prov = [], exec = [], txn = [];
  const pushProv = r => prov.push({
    flag: "MISSING_PROVISION", securityId: r.securityId, securityName: r.securityName,
    impact: r.impact, offset: r.offset, provisionData: r.provisionData, sourceAnomalyKey: r.key,
  });
  (s.provisionsQtyDiff || []).forEach(pushProv);
  (s.orphanTransactions || []).forEach(pushProv);
  (s.executionPrices || []).forEach(r => exec.push({
    flag: "MISSING_EXECUTION_PRICE", securityId: r.securityId, securityName: r.securityName,
    expectedExecPrice: r.expectedExecPrice, pu: r.pu, executionPrice: r.executionPrice,
    actualBalance: r.actualBalance, amountDiff: r.amountDiff, sourceAnomalyKey: r.key,
  }));
  (s.withholdingTax || []).forEach(r => txn.push({
    flag: "WITHHOLDING_TAX", securityId: r.securityId, securityName: r.securityName,
    impact: r.impact, sourceAnomalyKey: r.key,
  }));
  const cash = s.cashMismatch || {};
  if (cash.cashDiff != null && Math.abs(cash.cashDiff) >= 0.01) {
    const provBal = -cash.cashDiff;
    prov.push({
      flag: "MISSING_PROVISION", securityId: "", securityName: "Ajuste de caixa (cash mismatch)",
      impact: provBal, offset: 0, provisionData: { balance: provBal, provisionType: "buySell" },
      sourceAnomalyKey: `cash-mismatch:${wid}:${_date}`,
    });
  }
  return { prov, exec, txn };
}

// Baixa um JSON com TUDO que foi calculado (contagens, recálculo e correções)
// das carteiras selecionadas já diagnosticadas — o "arquivo" do item 4.
function downloadCorrections() {
  const diagSel = _renderedRows.filter(r => _selWallets.has(r.walletId) && (_diagResults[r.walletId] || {}).state === "done");
  if (!diagSel.length) return;
  const wallets = {};
  diagSel.forEach(r => {
    const d = _diagResults[r.walletId];
    wallets[r.walletId] = {
      walletName: r.walletName, verdict: d.verdict, verdictDetail: d.verdictDetail,
      gap: d.gap, counts: d.counts, recalc: d.recalc, items: _walletItems(r.walletId),
    };
  });
  const payload = { companyId: _company, companyName: _companyName, date: _date,
                    wallets, walletCount: diagSel.length };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `conciliacao_naoproc_${_company}_${_date}.json`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ── Enviar via API (gera as correções e POSTa para o sistema origem) ──────────
   Para cada carteira diagnosticada com correções: gera os payloads via
   generate-* (locais) e depois envia tudo num único /api/correcoes/bulk-submit,
   que GRAVA o arquivo de auditoria em Correções E cria as provisões/transações/
   preços de execução no Beehus. AÇÃO DESTRUTIVA — pede confirmação. */
function sendCorrectionsSelected() {
  const diagSel = _renderedRows.filter(r => _selWallets.has(r.walletId) && _diagHasCorrections(_diagResults[r.walletId]));
  if (!diagSel.length) return;
  const totalCorr = diagSel.reduce((acc, r) => {
    const c = _diagResults[r.walletId].counts || {};
    return acc + (c.provisions || 0) + (c.executionPrices || 0) + (c.withholdingTax || 0) + (c.cashAdjustments || 0);
  }, 0);
  if (!confirm(`Enviar via API ${totalCorr} correção(ões) de ${diagSel.length} carteira(s) para o sistema origem (Beehus)?\n\nCria provisões/transações/preços de execução REAIS no Beehus e grava o arquivo de auditoria em Correções.`)) return;
  const sd = document.getElementById("send-corr-btn");
  sd.disabled = true; sd.textContent = "Enviando...";
  _diagProgress("gerando correções…");

  _runPool(diagSel, 3, (r) => {
    const wid = r.walletId;
    const { prov, exec, txn } = _walletItems(wid);
    const base = { walletId: wid, date: _date };
    return Promise.all([
      txn.length  ? _post("/api/conciliacao/generate-transactions",     { ...base, items: txn })  : Promise.resolve({}),
      prov.length ? _post("/api/conciliacao/generate-provisions",       { ...base, items: prov }) : Promise.resolve({}),
      exec.length ? _post("/api/conciliacao/generate-execution-prices", { ...base, items: exec }) : Promise.resolve({}),
    ]).then(([t, p, e]) => {
      const provisions = p.provisions || [];
      provisions.forEach(pr => {
        if (typeof pr.sourceAnomalyKey === "string" && pr.sourceAnomalyKey.endsWith(":QTY_NO_TXN"))
          pr.description = "Mudança de quantidade sem transação";
      });
      return { wid, payload: {
        transactions: t.transactions || [], deletions: t.deletions || [],
        provisions, executionPrices: e.executionPrices || [],
      }};
    }).catch(() => null);
  }).then(results => {
    const wallets = {};
    (results || []).filter(Boolean).forEach(({ wid, payload }) => {
      const tot = payload.transactions.length + payload.provisions.length + payload.executionPrices.length;
      if (tot) wallets[wid] = payload;
    });
    const nW = Object.keys(wallets).length;
    if (!nW) { alert("Nada gerado para enviar."); return; }
    _diagProgress(`enviando ${nW} carteira(s)…`);
    return _post("/api/correcoes/bulk-submit", { companyId: _company, date: _date, wallets }).then(data => {
      const submitted = data.submitted || 0;
      const failed = (data.failed || 0) + ((data.rejected || []).length);
      const auth = data.authFailed ? " (token rejeitado — repasse hoje em /beehus)" : "";
      alert(failed
        ? `Enviado com ${failed} falha(s)${auth}. ${submitted} enviada(s) com sucesso. Veja a página Correções.`
        : `✓ ${submitted} correção(ões) enviada(s) ao Beehus e gravada(s) em Correções.`);
    });
  }).catch(() => alert("Falha ao enviar correções via API."))
    .finally(() => { sd.textContent = "Enviar via API"; _diagProgress(""); _syncSelHeader(); });
}

/* ── Step 2 ─────────────────────────────────────────────────────────────────── */
function openWallet(walletId, walletName) {
  _walletId = walletId;
  _walletName = walletName;
  _secFilterActive = true;   // cada carteira nasce com o filtro de ativos ligado
  document.getElementById("step-1").classList.add("hidden");
  document.getElementById("step-2").classList.remove("hidden");
  document.getElementById("bc-company").textContent = _companyName;
  document.getElementById("bc-wallet").textContent = walletName;
  document.getElementById("hdr-wallet-id").textContent = walletId;
  document.getElementById("hdr-former-date").textContent = "";
  renderDateNav();      // exibe só a data atual (sem navegação ← → / wallet-dates)
  loadWalletDetail();   // provisões vêm no payload e são renderizadas aqui dentro
  loadInlineTransactions();
  window.scrollTo(0, 0);
}

function goBack() {
  _stopCatalogWatch();   // sai do detalhe → para o poll de aquecimento
  document.getElementById("step-2").classList.add("hidden");
  document.getElementById("step-1").classList.remove("hidden");
  // Deep-link: ao abrir direto uma carteira, a lista de divergências não foi
  // carregada. Carrega sob demanda na primeira vez que o usuário volta.
  if (_pendingList) { const d = _pendingList; _pendingList = ""; selectDate(d); return; }
  // A navegação ←/→ no detalhe pode ter mudado a data analisada; ressincroniza
  // a lista de divergências (e o campo "Data") p/ refletir a data atual.
  if (_date && _date !== _listDate) {
    const inp = document.getElementById("end-date-input");
    if (inp) inp.value = _date;
    selectDate(_date);
  }
}

// Navegação ←/→ no detalhe: move a análise 1 dia útil p/ trás / p/ frente.
// Não usa a chamada wallet-dates (que custava ~11s): apenas recalcula a data
// (pulando fim de semana) e recarrega o detalhe. A lista do Step 1 é
// ressincronizada no "Voltar" se a data mudou (ver goBack).
function renderDateNav() {
  document.getElementById("wm-date-nav").innerHTML =
    `<button onclick="shiftWalletDate(-1)" title="Analisar dia útil anterior" class="fb-chip" data-kind="nav">
       <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>
     </button>
     <span class="px-1 text-xs font-semibold text-gray-700 tabular-nums">${esc(_date)}</span>
     <button onclick="shiftWalletDate(1)" title="Analisar próximo dia útil" class="fb-chip" data-kind="nav">
       <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
     </button>`;
}

// Desloca uma data ISO (YYYY-MM-DD) por `delta` dias ÚTEIS (pula sáb/dom, sem
// calendário de feriados — mesma convenção de _next_biz_day no backend). Usa
// componentes locais (sem toISOString) p/ não escorregar de dia por fuso.
function _shiftBizDate(dateStr, delta) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr || "");
  if (!m) return dateStr;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const step = delta > 0 ? 1 : -1;
  let remaining = Math.abs(delta) || 1;
  while (remaining > 0) {
    d.setDate(d.getDate() + step);
    const wd = d.getDay();           // 0=dom, 6=sáb
    if (wd !== 0 && wd !== 6) remaining--;
  }
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

function shiftWalletDate(delta) {
  if (!_date) return;
  _date = _shiftBizDate(_date, delta);
  document.getElementById("hdr-former-date").textContent = "";
  renderDateNav();
  loadWalletDetail();        // re-renderiza pills, ativos, provisões e data ant.
  loadInlineTransactions();
}

// Aviso quando a posição bruta da data ainda não foi pré-processada (nenhum
// ativo com securityId resolvido). Em vez de listar os ativos com o
// unprocessedId como nome (fallback enganoso), mostramos este banner. Reusado
// no detalhe e no modal Diagnosticar.
