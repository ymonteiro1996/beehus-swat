/* Conciliação (Não Proc.) — modal de diagnóstico, confirmação e bootstrap final.
   Escopo global compartilhado; ordem importa. */
function closeDiagnostic() { document.getElementById("diag-modal").classList.add("hidden"); }

function openDiagnostic() {
  document.getElementById("diag-modal").classList.remove("hidden");
  document.getElementById("diag-wallet").textContent = _walletName;
  document.getElementById("diag-sub").textContent = `${_date} · gap NAV via navPackage · sugestões focadas`;
  document.getElementById("diag-body").innerHTML = `<p class="text-gray-400 py-10 text-center">Diagnosticando...</p>`;
  document.getElementById("diag-impl-btn").disabled = true;
  document.getElementById("diag-foot-msg").textContent = "";
  document.getElementById("diag-select-all").checked = false;
  _diag = null; _suggByKey = {};
  fetch(`/api/conciliacao-unprocessed/diagnose?walletId=${encodeURIComponent(_walletId)}&date=${encodeURIComponent(_date)}&companyId=${encodeURIComponent(_company)}`)
    .then(r => r.json()).then(d => {
      if (d.error) { document.getElementById("diag-body").innerHTML = `<p class="text-red-500 py-10 text-center">${esc(d.error)}</p>`; return; }
      // Data não pré-processada: sem ativos com securityId p/ atribuir o gap —
      // avisa em vez de mostrar um veredito falsamente "limpo".
      if (d.notPreProcessed) {
        document.getElementById("diag-body").innerHTML = _notPreProcessedHTML();
        document.getElementById("diag-impl-btn").disabled = true;
        return;
      }
      _diag = d;
      renderDiagnostic(d);
      if (d.catalogWarming) {
        document.getElementById("diag-body").insertAdjacentHTML("afterbegin",
          `<div class="mb-2 px-3 py-1.5 text-[11px] rounded bg-amber-50 border border-amber-200 text-amber-800 flex items-center gap-2"><span class="spin"></span>Aquecendo o catálogo de ativos para os dias de liquidação… (uma vez, ~20s)</div>`);
        _pollCatalogThen(() => { if (!document.getElementById("diag-modal").classList.contains("hidden")) openDiagnostic(); });
      }
    }).catch(() => { document.getElementById("diag-body").innerHTML = `<p class="text-red-500 py-10 text-center">Falha ao diagnosticar.</p>`; });
}

function verdictBanner(d) {
  const v = d.verdict || "—";
  const ok = v === "NO_GAP";
  const cls = ok ? "bg-green-50 text-green-700 border-green-200" : "bg-red-50 text-red-700 border-red-200";
  // "Lado a corrigir": qual lado o diagnóstico culpa (mesmo mapeamento da grade
  // do Step 1). Ver docs/CONCILIACAO_QUAL_LADO.md. Omitido em NO_GAP.
  const vi = VERDICT_INFO[v];
  const sideLine = (vi && vi.side && !ok)
    ? `<div class="mt-2 flex items-center gap-1.5">
         <span class="text-[10px] uppercase tracking-wide text-gray-400">Lado a corrigir</span>
         <span class="text-[11px] font-semibold ${vi.cls} bg-white border rounded px-1.5 py-0.5">${esc(vi.label)}</span>
         <span class="text-[11px] ${vi.cls}">${esc(vi.side)}</span>
       </div>`
    : "";
  return `<div class="border rounded-lg px-4 py-3 mb-4 ${cls}">
    <div class="font-semibold">${esc(VERDICT_LABEL[v] || v)}</div>
    <div class="text-xs mt-0.5">${esc(d.verdictDetail || "")}</div>
    ${sideLine}
  </div>`;
}

function catCard(title, hint, count, bodyHtml) {
  const badge = count > 0
    ? `<span class="bg-indigo-100 text-indigo-700 rounded-full px-2 py-0.5 text-[10px] font-semibold">${count}</span>`
    : `<span class="bg-gray-100 text-gray-400 rounded-full px-2 py-0.5 text-[10px]">0</span>`;
  return `<div class="mb-4 border rounded-lg overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 bg-gray-50 border-b">
      <h3 class="text-xs font-semibold text-gray-700">${title}</h3> ${badge}
      <span class="text-[10px] text-gray-400 ml-auto">${esc(hint)}</span>
    </div>
    <div class="p-2 overflow-x-auto">${bodyHtml}</div>
  </div>`;
}

function suggCheckbox(key) {
  return `<input type="checkbox" class="diag-sugg-cb" data-key="${esc(key)}" onchange="diagSelChanged()" checked>`;
}
function emptyMsg(msg) { return `<p class="text-[11px] text-gray-400 px-1 py-1">${esc(msg)}</p>`; }
function thead(cols) {
  return `<thead><tr>${cols.map(c => {
    const right = c[1] === "text-right";
    return `<th ${right ? 'style="text-align:right"' : 'class="rp-tbl-left"'}>${c[0]}</th>`;
  }).join("")}</tr></thead>`;
}

// Provisions block groups BOTH provision sources (Δ quantidade + transação
// órfã) — one table per execution type, with an "Origem" column.
function renderProvisionsTable(qtyRows, orphanRows) {
  const all = [
    ...qtyRows.map(r => ({ ...r, _origem: r.origemLabel || "Δ quantidade", _txnBal: null })),
    ...orphanRows.map(r => ({ ...r, _origem: "transação órfã", _txnBal: r.txnBalance })),
  ];
  if (!all.length) return emptyMsg("Nenhuma provisão sugerida.");
  const body = all.map(r => {
    const pd = r.provisionData || {};
    _suggByKey[r.key] = { kind: "prov", summary: `${r.securityName} · provisão ${fmtMoney(pd.balance)} (${r._origem})`,
      item: { flag: "MISSING_PROVISION", securityId: r.securityId, securityName: r.securityName,
              impact: r.impact, offset: r.offset, provisionData: pd, sourceAnomalyKey: r.key } };
    return `<tr>
      <td style="text-align:center">${suggCheckbox(r.key)}</td>
      <td class="rp-tbl-left text-gray-700">${esc(r.securityName)}</td>
      <td class="rp-tbl-left text-[10px] text-gray-500">${esc(r._origem)}</td>
      <td class="text-gray-600">${fmtNum(r.amountDiff, 4)}</td>
      <td class="text-gray-500">${r._txnBal == null ? "—" : fmtMoney(r._txnBal)}</td>
      <td class="text-gray-700">${fmtMoney(pd.balance)}</td>
      <td class="rp-tbl-left text-gray-400 text-[10px]">${esc(pd.initialDate||"")} → ${esc(pd.liquidationDate||"")}</td>
    </tr>`;
  }).join("");
  return `<table class="rp-tbl">${thead([["",""],["Ativo"],["Origem"],["Δ Qtd","text-right"],["Valor txn","text-right"],["Saldo provisão","text-right"],["Janela"]])}<tbody>${body}</tbody></table>`;
}

function renderExecTable(rows) {
  if (!rows.length) return emptyMsg("Nenhum preço de execução divergente.");
  const body = rows.map(r => {
    _suggByKey[r.key] = { kind: "exec", summary: `${r.securityName} · preço exec. ${fmtNum(r.expectedExecPrice,6)}`,
      item: { flag: "MISSING_EXECUTION_PRICE", securityId: r.securityId, securityName: r.securityName,
              expectedExecPrice: r.expectedExecPrice, pu: r.pu, executionPrice: r.executionPrice,
              actualBalance: r.actualBalance, amountDiff: r.amountDiff, sourceAnomalyKey: r.key } };
    return `<tr>
      <td style="text-align:center">${suggCheckbox(r.key)}</td>
      <td class="rp-tbl-left text-gray-700">${esc(r.securityName)}</td>
      <td class="text-gray-500">${fmtNum(r.pu,6)}</td>
      <td class="text-gray-500">${r.executionPrice==null?"—":fmtNum(r.executionPrice,6)}</td>
      <td class="text-gray-800 font-medium">${fmtNum(r.expectedExecPrice,6)}</td>
      <td class="text-gray-700">${fmtMoney(r.impact)}</td>
    </tr>`;
  }).join("");
  return `<table class="rp-tbl">${thead([["",""],["Ativo"],["PU","text-right"],["Preço exec. atual","text-right"],["Preço sugerido","text-right"],["Impacto","text-right"]])}<tbody>${body}</tbody></table>`;
}

function renderTaxTable(rows) {
  if (!rows.length) return emptyMsg("Nenhum IR retido na fonte detectado.");
  const body = rows.map(r => {
    _suggByKey[r.key] = { kind: "txn", summary: `${r.securityName} · IR ${fmtMoney(r.impact)} (saída)`,
      item: { flag: "WITHHOLDING_TAX", securityId: r.securityId, securityName: r.securityName,
              impact: r.impact, sourceAnomalyKey: r.key } };
    return `<tr>
      <td style="text-align:center">${suggCheckbox(r.key)}</td>
      <td class="rp-tbl-left text-gray-700">${esc(r.securityName)}</td>
      <td class="text-gray-500">${fmtMoney(r.expectedValue)}</td>
      <td class="text-gray-500">${fmtMoney(r.actualBalance)}</td>
      <td class="text-gray-700">${fmtMoney(r.impact)}</td>
    </tr>`;
  }).join("");
  return `<table class="rp-tbl">${thead([["",""],["Ativo"],["Valor esperado","text-right"],["Valor real","text-right"],["IR (impacto)","text-right"]])}<tbody>${body}</tbody></table>`;
}

function renderCashBlock(c) {
  if (c.cashDiff == null) return emptyMsg("Sem dados de caixa.");
  if (Math.abs(c.cashDiff) < 0.01) return `<p class="text-[11px] text-green-600 px-1 py-1">Caixa consistente.</p>`;

  // Sugestão (opt-in): provisão de ajuste com o MESMO valor do Δ caixa e sinal
  // invertido — balanceia o caixa. Reusa o caminho de provisões (kind:'prov').
  const provBal = -c.cashDiff;
  const cashKey = "cash-mismatch-prov";
  _suggByKey[cashKey] = {
    kind: "prov",
    summary: `Ajuste de caixa · provisão ${fmtMoney(provBal)} (Δ caixa invertido)`,
    item: { flag: "MISSING_PROVISION", securityId: "", securityName: "Ajuste de caixa (cash mismatch)",
            impact: provBal, offset: 0,
            provisionData: { balance: provBal, provisionType: "buySell" },
            sourceAnomalyKey: `cash-mismatch:${_walletId}:${_date}` },
  };

  let h = `<div class="flex flex-wrap gap-2 mb-2">
    ${pill("Caixa Ant.", fmtMoney(c.formerCash))}
    ${pill("Σ Transações", fmtMoney(c.totalTransactions))}
    ${pill("Caixa Proj.", fmtMoney(c.projectedCash))}
    ${pill("Caixa Atual", fmtMoney(c.currentCash))}
    ${pill("Δ Caixa", fmtMoney(c.cashDiff), "text-red-600")}
  </div>
  <p class="text-[11px] text-gray-500 mb-2">Diagnóstico: <b>${esc(c.diagnosis||"—")}</b> · investigue a transação suspeita ou crie a provisão de ajuste abaixo.</p>
  <table class="rp-tbl">${thead([["",""],["Sugestão"],["Δ Caixa","text-right"],["Saldo provisão","text-right"]])}<tbody>
    <tr>
      <td style="text-align:center"><input type="checkbox" class="diag-sugg-cb" data-key="${cashKey}" onchange="diagSelChanged()"></td>
      <td class="rp-tbl-left text-gray-700">Provisão de ajuste de caixa</td>
      <td class="text-gray-500">${fmtMoney(c.cashDiff)}</td>
      <td class="text-gray-700 font-medium">${fmtMoney(provBal)}</td>
    </tr>
  </tbody></table>
  <p class="text-[10px] text-gray-400 mt-1">Cria provisão (buySell) sem ativo com saldo = Δ caixa invertido (liquidação = data + offset, D+1 útil). Enviada para Correções.</p>`;
  const sus = c.suspectTxns || [];
  if (sus.length) {
    h += `<p class="text-[10px] text-gray-400 mt-1">Transações suspeitas (saldo ≈ Δ caixa):</p>
      <table class="rp-tbl"><tbody>${sus.map(t => `<tr>
        <td class="rp-tbl-left text-gray-600">${esc(t.securityName || t.securityId || "(carteira)")}</td>
        <td class="rp-tbl-left text-gray-500">${esc(t.type || "—")}</td>
        <td class="text-gray-700">${fmtMoney(t.balance)}</td>
        <td class="rp-tbl-left text-[10px] text-gray-400">${t.pending ? "pendente" : ""}</td>
      </tr>`).join("")}</tbody></table>`;
  }
  return h;
}

function renderDiagnostic(d) {
  const g = d.gap || {};
  const s = d.suggestions || {};
  _suggByKey = {};
  let html = "";
  if (d.note) html += `<p class="text-[10px] text-gray-400 italic mb-2">${esc(d.note)}</p>`;
  html += verdictBanner(d);

  html += `<div class="flex flex-wrap gap-2 mb-4">
    ${pill("RET. NAV", fmtPct(g.returnNavPerShare))}
    ${pill("RET CONTR", fmtPct(g.returnContribution))}
    ${pill("Gap %", fmtPct(g.gapPct), "text-red-600")}
    ${pill("Gap R$", fmtMoney(g.gapCash), "text-red-600")}
    ${pill("NAV Anterior", fmtMoney(g.formerNav))}
    ${d.unmappedCount ? pill("Não mapeados", d.unmappedCount, "text-amber-600") : ""}
  </div>`;

  const cash = s.cashMismatch || {};
  const cashCount = (cash.cashDiff != null && Math.abs(cash.cashDiff) >= 0.01) ? 1 : 0;
  const provQty = s.provisionsQtyDiff || [], orphans = s.orphanTransactions || [];
  // Grouped by EXECUTION TYPE (not by flag): Provisões / Transações / Preços de execução.
  html += catCard("Provisões", "cria provisão (buySell) — Δ quantidade e transações órfãs",
    provQty.length + orphans.length, renderProvisionsTable(provQty, orphans));
  html += catCard("Transações", "cria transação (ex.: IR retido na fonte → taxes, saída de caixa)",
    (s.withholdingTax||[]).length, renderTaxTable(s.withholdingTax || []));
  html += catCard("Preços de execução", "ajusta executionPrice",
    (s.executionPrices||[]).length, renderExecTable(s.executionPrices || []));
  html += catCard("Cash mismatch", "cria provisão de ajuste (Δ caixa, sinal invertido)", cashCount, renderCashBlock(cash));

  document.getElementById("diag-body").innerHTML = html;
  diagSelChanged();
}

function _selectedKeys() {
  return Array.from(document.querySelectorAll(".diag-sugg-cb:checked")).map(c => c.dataset.key);
}

function diagSelChanged() {
  const n = _selectedKeys().length;
  document.getElementById("diag-impl-btn").disabled = n === 0;
  document.getElementById("diag-foot-msg").textContent = n ? `${n} sugestão(ões) selecionada(s)` : "";
}

function diagSelectAll(on) {
  document.querySelectorAll(".diag-sugg-cb").forEach(cb => { cb.checked = on; });
  diagSelChanged();
}

function diagOpenConfirm() {
  const keys = _selectedKeys();
  if (!keys.length) return;
  const groups = { prov: [], exec: [], txn: [] };
  keys.forEach(k => { const s = _suggByKey[k]; if (s) groups[s.kind].push(s); });
  const section = (label, arr) => arr.length ? `<div class="mb-3">
    <p class="text-xs font-semibold text-gray-600 mb-1">${label} (${arr.length})</p>
    <ul class="text-[11px] text-gray-600 list-disc pl-5">${arr.map(s => `<li>${esc(s.summary)}</li>`).join("")}</ul>
  </div>` : "";
  document.getElementById("diag-confirm-body").innerHTML =
    section("Provisões", groups.prov) +
    section("Preços de execução", groups.exec) +
    section("Transações (IR retido)", groups.txn) +
    `<p class="text-[11px] text-gray-400 mt-2">${keys.length} item(ns) serão gerados e enviados para Correções.</p>`;
  document.getElementById("diag-confirm-msg").textContent = "";
  document.getElementById("diag-confirm-btn").disabled = false;
  document.getElementById("diag-confirm-modal").classList.remove("hidden");
}

function diagCloseConfirm() { document.getElementById("diag-confirm-modal").classList.add("hidden"); }

function diagConfirmImplement() {
  const keys = _selectedKeys();
  const groups = { prov: [], exec: [], txn: [] };
  keys.forEach(k => { const s = _suggByKey[k]; if (s) groups[s.kind].push(s.item); });
  const msg = document.getElementById("diag-confirm-msg");
  msg.textContent = "Gerando...";
  document.getElementById("diag-confirm-btn").disabled = true;
  const base = { walletId: _walletId, date: _date };
  Promise.all([
    groups.txn.length  ? _post("/api/conciliacao/generate-transactions",     { ...base, items: groups.txn })  : Promise.resolve({}),
    groups.prov.length ? _post("/api/conciliacao/generate-provisions",       { ...base, items: groups.prov }) : Promise.resolve({}),
    groups.exec.length ? _post("/api/conciliacao/generate-execution-prices", { ...base, items: groups.exec }) : Promise.resolve({}),
  ]).then(([t, p, e]) => {
    const provisions = p.provisions || [];
    // Provisões originadas de "mudança de quantidade sem transação" (offset 0):
    // o generate-provisions fixa uma descrição genérica; sobrescrevemos aqui
    // pela descrição pedida antes de enviar para Correções.
    provisions.forEach(pr => {
      if (typeof pr.sourceAnomalyKey === "string" && pr.sourceAnomalyKey.endsWith(":QTY_NO_TXN"))
        pr.description = "Mudança de quantidade sem transação";
    });
    const payload = {
      transactions: t.transactions || [], deletions: t.deletions || [],
      provisions: provisions, executionPrices: e.executionPrices || [],
    };
    const total = payload.transactions.length + payload.provisions.length + payload.executionPrices.length;
    if (!total) { msg.textContent = "Nada gerado."; document.getElementById("diag-confirm-btn").disabled = false; return; }
    msg.textContent = "Enviando para Correções...";
    return _post("/api/correcoes/bulk-submit", {
      companyId: _company, date: _date, wallets: { [_walletId]: payload },
    }).then(data => {
      const failed = (data.failed || 0) + ((data.rejected || []).length);
      msg.textContent = failed ? `Enviado com ${failed} falha(s). Veja Correções.` : `✓ Enviado: ${total} correção(ões).`;
      document.getElementById("diag-foot-msg").textContent = "✓ Implementado — ver página Correções.";
    });
  }).catch(() => { msg.textContent = "Falha ao implementar."; document.getElementById("diag-confirm-btn").disabled = false; });
}

function _post(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" }, body: JSON.stringify(body) })
    .then(r => r.json());
}

/* ── ESC fecha o modal aberto mais ao topo (uma camada por vez) ─────────────── */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const layers = [
    ["txn-edit-modal", closeTxnEditModal],
    ["prov-edit-modal", closeProvEditModal],
    ["diag-confirm-modal", diagCloseConfirm],
    ["diag-modal", closeDiagnostic],
  ];
  for (const [id, close] of layers) {
    const el = document.getElementById(id);
    if (el && !el.classList.contains("hidden")) { close(); break; }
  }
});

/* ── Deep-link vindo do Tombamento ───────────────────────────────────────────
   O shell recria este iframe com ?companyId=&date=&walletId= (ver goToConciliacao
   no Tombamento). Com walletId → abre direto o detalhe da carteira na data; só
   empresa+data → lista as divergências. O nome da carteira é resolvido no
   wallet-detail. */
(function bootFromParams() {
  const q = new URLSearchParams(location.search);
  const cid = q.get("companyId") || "";
  const dt  = q.get("date") || "";
  const wid = q.get("walletId") || "";
  if (!cid || !/^\d{4}-\d{2}-\d{2}$/.test(dt)) return;   // sem deep-link válido
  _company = cid;
  _companyName = companyNames[cid] || cid;
  const csel = document.getElementById("company-select"); if (csel) csel.value = cid;
  const din  = document.getElementById("end-date-input"); if (din)  din.value  = dt;
  _date = dt;
  if (wid) {
    _pendingList = dt;      // a lista carrega só se o usuário clicar em "Voltar"
    openWallet(wid, wid);   // nome real chega no wallet-detail e atualiza o breadcrumb
  } else {
    selectDate(dt);         // sem carteira: mostra a lista da empresa+data
  }
})();
