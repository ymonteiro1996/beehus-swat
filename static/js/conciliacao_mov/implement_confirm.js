/* Conciliação (Mov.) — download xlsx, confirmar implementação, gains/expenses.
   Escopo global compartilhado; ordem importa. */
function downloadImplementXlsx() {
  const wids = _implementWids; if (!wids.length) return;
  const btn = document.getElementById("implement-dl-btn"); const old = btn.textContent; btn.disabled = true; btn.textContent = "Gerando...";
  fetch("/api/conciliacao-mov/xlsx", { method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify({ companyId: _company, sourceDate: _date, targetDate: _targetDate(), walletIds: wids }) })
    .then(async res => {
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.error || `HTTP ${res.status}`); }
      const blob = await res.blob();
      _downloadBlob(blob, `conciliacao_mov_${_company}_${_targetDate()}.xlsx`);
    }).catch(err => alert("Falha ao baixar: " + err.message))
    .finally(() => { btn.textContent = old; btn.disabled = false; });
}
function _implStep(label, d, okFmt) {
  if (!d || d.error) return `<div style="color:#b91c1c">✗ ${esc(label)}: ${esc((d && d.error) || "falha")}</div>`;
  const extra = d.failed ? ` <span style="color:#b45309">(${d.failed} falha(s))</span>` : "";
  return `<div style="color:#166534">✓ ${esc(label)}: ${esc(okFmt(d))}${extra}</div>`;
}
async function confirmImplement() {
  const wids = _implementWids; if (!wids.length) return;
  const cb = document.getElementById("implement-confirm-btn"); cb.disabled = true; cb.textContent = "Implementando...";
  const dl = document.getElementById("implement-dl-btn"); if (dl) dl.disabled = true;
  const stat = document.getElementById("implement-status");
  const base = { companyId: _company, sourceDate: _date, targetDate: _targetDate() };
  // Lê a seleção dos checkboxes ANTES de substituir o corpo do modal pelo resultado.
  const optExec = _implChecked("impl-opt-exec"), optIrrf = _implChecked("impl-opt-irrf");
  const optRecon = _implChecked("impl-opt-recon"), optProv = _implChecked("impl-opt-prov");
  const shiftSel = {};
  document.querySelectorAll("#implement-body .impl-shift:checked").forEach(cb => {
    const w = cb.getAttribute("data-wid"), id = cb.getAttribute("data-id");
    if (w && id) (shiftSel[w] = shiftSel[w] || []).push(id);
  });
  const log = [];
  try {
    const execW = optExec ? wids.filter(w => ((_movResults[w] || {}).executionPriceFixes || []).length) : [];
    if (execW.length) { stat.textContent = "Preços de execução…"; const d = await _post("/api/conciliacao-mov/execution-prices", { ...base, walletIds: execW }); log.push(_implStep("Preços de execução", d, x => `${x.updated || 0} atualizado(s), ${x.created || 0} criado(s)`)); }
    const irrfW = optIrrf ? wids.filter(w => ((_movResults[w] || {}).irrf || []).some(e => !e.covered)) : [];
    if (irrfW.length) { stat.textContent = "IRRF…"; const d = await _post("/api/conciliacao-mov/irrf", { ...base, walletIds: irrfW }); log.push(_implStep("IRRF", d, x => `${x.created || 0} criado(s)`)); }
    const reconW = optRecon ? wids.filter(w => ((_movResults[w] || {}).reconTransactions || []).length) : [];
    if (reconW.length) { stat.textContent = "Transações de ajuste…"; const d = await _post("/api/conciliacao-mov/reconcile-txn", { ...base, walletIds: reconW }); log.push(_implStep("Transações de ajuste", d, x => `${x.created || 0} criada(s)`)); }
    const provW = optProv ? wids.filter(w => _provCount(_movResults[w] || {})) : [];
    if (provW.length) { stat.textContent = "Provisões…"; const d = await _post("/api/conciliacao-mov/provisions", { ...base, walletIds: provW }); log.push(_implStep("Provisões", d, x => `${x.created || 0} criada(s)${x.skipped ? `, ${x.skipped} ignorada(s)` : ""}`)); }
    const shiftW = Object.keys(shiftSel);
    if (shiftW.length) { stat.textContent = "Deslocando provisões…"; const d = await _post("/api/conciliacao-mov/shift-provisions", { ...base, walletIds: shiftW, selected: shiftSel }); log.push(_implStep("Provisões deslocadas", d, x => `${x.created || 0} criada(s)${x.skipped ? `, ${x.skipped} ignorada(s)` : ""}${x.liquidationDate ? ` · liq. ${x.liquidationDate}` : ""}`)); }
    stat.textContent = "Upload da unprocessed…"; const du = await _post("/api/conciliacao-mov/apply", { ...base, walletIds: wids }); log.push(_implStep("Upload da unprocessed", du, x => `${x.wallets || 0} carteira(s), ${x.rows || 0} linha(s)`));
  } catch (err) {
    log.push(`<div style="color:#b91c1c">✗ Falha de rede durante a implementação — passos seguintes não executados.</div>`);
  }
  stat.textContent = "";
  document.getElementById("implement-body").innerHTML =
    `<div class="text-[10px] uppercase tracking-widest text-gray-400 mb-2">Resultado</div><div style="display:flex;flex-direction:column;gap:4px;font-size:12px">${log.join("") || '<div class="text-gray-400">Nada a aplicar.</div>'}</div>`;
  cb.textContent = "Atualizando…";
  await _reprojectWids(wids);
  _syncSelHeader();
  // Se a ação partiu do detalhe (1 carteira, tela de detalhe aberta), reprojeta a
  // própria tela de detalhe com os dados frescos (mesma prática do createGainsExpenses).
  if (_detWid && wids.length === 1 && wids[0] === _detWid
      && !document.getElementById("mov-detail").classList.contains("hidden")) openDetail(_detWid);
  cb.textContent = "Fechar"; cb.disabled = false; cb.onclick = closeImplementModal;
}

/* Opção 1 (liquidação stockETF/B3): cria um AJUSTE DE CONTRIBUIÇÃO
   (securityContributionAdjustment) p/ o resíduo. Esse tipo é CASH-NEUTRO (não entra no
   caixa projetado, só na contribuição), então NÃO há prompt de "omitir do caixa": o caixa
   oficial já reflete só o líquido da B3 e o ajuste não o desbalanceia. Botão na linha-resumo. */
function createGainsExpenses(wid) {
  const r = _movResults[wid]; if (!r || !r.stockEtfLiquidation) return;
  const se = r.stockEtfLiquidation;
  const value = -nz(se.residual);   // ajuste a lançar = -(resíduo)
  if (Math.abs(value) < 0.01) { alert("Sem resíduo a lançar (já reconciliado)."); return; }
  const expl = `A B3 liquidou o valor líquido (${fmtMoney(se.b3Balance)}), mas as provisões dos stockETF somavam ${fmtMoney(se.provisionsTotal)}`
    + (Math.abs(nz(se.gainsExpensesTotal)) >= 0.01 ? ` (já há ${fmtMoney(se.gainsExpensesTotal)} de gainsExpenses lançado)` : "")
    + `. A diferença residual (${fmtMoney(se.residual)}) é o custo de execução — será lançada como um ajuste de contribuição (securityContributionAdjustment) de ${fmtMoney(value)} em ${r.targetDate}. Esse tipo NÃO move o caixa projetado (entra só na contribuição).`;
  if (!confirm(`Criar ajuste de contribuição de ${fmtMoney(value)} em ${r.targetDate}?\n\n${expl}\n\nAção destrutiva (cria a transação no Beehus).`)) return;
  _detStatus("Criando ajuste de contribuição e reprojetando…");
  _post("/api/conciliacao-mov/gains-expenses", { companyId: _company, walletId: wid, date: r.targetDate, balance: value, currencyId: r.currencyId })
    .then(d => {
      if (d && d.error) { _detStatus("Falha ao criar o ajuste de contribuição.", "error"); alert("Erro: " + d.error); return; }
      // reprojeta na MESMA data do detalhe aberto (não no campo de data, que pode ter mudado).
      return _movimentarOne(wid, r.targetDate).then(() => {
        if (_movResults[wid] && _movResults[wid].state === "done" && _detWid === wid) openDetail(wid);
        _detStatus("✓ ajuste de contribuição criado (securityContributionAdjustment — fora do caixa).", "ok");
      });
    }).catch(() => { _detStatus("Falha ao criar o ajuste de contribuição.", "error"); alert("Falha ao criar o ajuste de contribuição."); });
}

/* ── Detalhamento da carteira (aba master→detail) ───────────────────────────── */
function backToGrid() {
  _detStatus(""); _detWid = null;
  document.getElementById("mov-detail").classList.add("hidden");
  document.getElementById("mov-filters").classList.remove("hidden");
  document.getElementById("table-section").classList.remove("hidden");
  window.scrollTo(0, 0);
}
