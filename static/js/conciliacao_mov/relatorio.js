/* Conciliação (Mov.) — relatório de erros.
   Escopo global compartilhado; ordem importa. */
function _collectReportIssues(r) {
  const d = r.diff || {}, out = [];
  // ── INSUMO (independe do alvo) ───────────────────────────────────────────────
  (r.rows || []).forEach(rw => {
    if (!rw.mapped) out.push({ nature: "insumo", tag: "Não mapeado",
      text: `${rw.securityName || rw.unprocessedId || "—"} — ativo sem securityId resolvido na origem` });
  });
  (r.transactions || []).forEach(t => {
    const sid = String(t.securityId || "").trim(), typ = t.type || "";
    if (!sid) {
      // Só é órfã se o TIPO exige securityId; senão (gainsExpenses/rebate/withdrawalDeposit/
      // taxes/contributionAdjustment/…) a ausência de ativo é normal → não sinaliza.
      if (_SID_REQUIRED_TXN_TYPES.has(typ)) {
        const isCoupon = typ === "coupon" || typ === "amortization";
        out.push({ nature: "insumo", tag: isCoupon ? "Cupom/amort. sem ativo" : "Transação órfã",
          text: `tipo=${typ || "?"} sem securityId · ${t.liquidationDate || t.operationDate || ""} · ${fmtMoney(t.balance)}${t.description ? " · " + t.description : ""}` });
      }
    } else if (typ && !_KNOWN_TXN_TYPES.has(typ)) {
      out.push({ nature: "insumo", tag: "Tipo desconhecido",
        text: `${t.securityName || sid} · tipo="${typ}" não tratado · ${fmtMoney(t.balance)}` });
    }
  });
  // ── RECONCILIAÇÃO (só com alvo) ──────────────────────────────────────────────
  // Caixa-âncora eliminado: o resíduo de caixa NÃO classifica divergência de ativo nem
  // dispara correção/recon. MAS, quando o caixa PROJETADO diverge do caixa OFICIAL do
  // alvo, isso é reportado aqui como "Divergência de caixa" — puramente INFORMATIVO
  // (sem escrita). Só no Tipo A (há alvo → há caixa oficial p/ comparar).
  const cashRes = nz(d.cashResidual);
  if (d.officialCash != null && Math.abs(cashRes) >= 0.01) {
    out.push({ nature: "reconciliacao", tag: "Divergência de caixa",
      text: `caixa projetado ${fmtMoney(d.movedCash)} × oficial ${fmtMoney(d.officialCash)} → resíduo ${fmtMoney(cashRes)}; informativo — não dispara correção` });
  }
  // As reconTransactions são só resgate no VENCIMENTO.
  (r.reconTransactions || []).forEach(x => {
    out.push({ nature: "reconciliacao", tag: "Resgate no vencimento",
      text: `${x.securityName || x.securityId} — ${x.direction || ""} qtd ${fmtNum(x.quantity, 4)} @ ${fmtNum(x.price, 6)} (${fmtMoney(x.balance)}); transação de resgate no vencimento` });
  });
  // Divergência só-de-PU não existe mais: o saldo-alvo é avaliado ao PU em uso, então
  // qualquer divergência de saldo vem de quantidade (já reportada via reconTransactions/
  // reconProvisions / Só na projetada/alvo abaixo). Nada a emitir aqui.
  (d.onlyCalc || []).forEach(x => out.push({ nature: "reconciliacao", tag: "Só na projetada",
    text: `${x.securityName || x.securityId} — presente na projetada, ausente no alvo (qtd ${fmtNum(x.quantity, 4)})` }));
  (d.onlyReal || []).forEach(x => out.push({ nature: "reconciliacao", tag: "Só no alvo",
    text: `${x.securityName || x.securityId} — presente no alvo, ausente na projetada (qtd ${fmtNum(x.quantity, 4)})` }));
  (r.irrf || []).forEach(e => {
    if (e.covered) return;
    out.push({ nature: "reconciliacao", tag: e.multiEvent ? "IRRF (revisar — multi-resgate)" : "IRRF ausente",
      text: `${e.securityName || e.securityId} — IRRF estimado ${fmtMoney(e.irrf)}` });
  });
  const se = r.stockEtfLiquidation;
  if (se && Math.abs(nz(se.residual)) >= 1) out.push({ nature: "reconciliacao", tag: "Liquidação stockETF/B3",
    text: `resíduo ${fmtMoney(se.residual)} (provisões ${fmtMoney(se.provisionsTotal)} × B3 ${fmtMoney(se.b3Balance)})` });
  // ── REFERÊNCIA (erro no Tipo A; suprimida no forward) ────────────────────────
  (r.rows || []).forEach(rw => {
    if ((rw.pricingType || "") === "REPETIDO") out.push({ nature: "referencia", tag: "PU repetido",
      text: `${rw.securityName || rw.securityId} — sem PU na data-alvo; repetiu o PU da origem (${fmtNum(rw.pu, 6)})` });
  });
  (r.executionPriceFixes || []).forEach(f => out.push({ nature: "referencia", tag: "Preço de execução a corrigir",
    text: `${f.securityName || f.securityId} — ${f.status === "ausente" ? "execPrice ausente" : "execPrice = PU (placeholder)"} → ${f.action || "corrigir"} (calc ${fmtNum(f.calculatedPrice, 6)})` }));
  return out;
}

function _buildErrorReportText(results) {
  const A = [], B = [];
  results.forEach(r => {
    const fwd = !(r.diff && r.diff.hasTarget);
    let issues = _collectReportIssues(r);
    if (fwd) issues = issues.filter(i => i.nature === "insumo");  // forward: só insumo
    (fwd ? B : A).push({ r, issues });
  });
  const first = results[0] || {};
  const L = [];
  L.push("RELATÓRIO DE ERROS DE CONCILIAÇÃO");
  L.push(`Empresa: ${_companyName || ""}${_company ? " (" + _company + ")" : ""}`);
  L.push(`Janela: ${first.sourceDate || ""} -> ${first.targetDate || _date || ""}`);
  L.push(`Gerado em: ${new Date().toLocaleString("pt-BR")}`);
  L.push(`Carteiras: ${results.length}`);
  L.push("");
  const sec = (title, list, fwd) => {
    L.push("=".repeat(64));
    L.push(title);
    L.push("=".repeat(64));
    if (fwd) L.push("(posição-alvo NÃO processada — lacunas de dado são esperadas e omitidas; só defeitos de insumo)");
    L.push("");
    if (!list.length) { L.push("(nenhuma carteira)"); L.push(""); return; }
    list.forEach(({ r, issues }) => {
      const gap = (r.simGapCash != null) ? ` · GAP sim. ${fmtMoney(r.simGapCash)}` : "";
      L.push(`> ${r.walletName || r.walletId}${gap}`);
      if (!issues.length) L.push("   OK — sem erros detectados");
      else issues.forEach(i => L.push(`   - [${i.tag}] ${i.text}`));
      L.push("");
    });
  };
  sec("TIPO A — CONCILIAÇÃO (posição-alvo existe)", A, false);
  sec("TIPO B — FORWARD / PREPARAÇÃO (posição-alvo não processada)", B, true);
  const totalIssues = A.concat(B).reduce((a, e) => a + e.issues.length, 0);
  L.push("-".repeat(64));
  L.push(`Total de itens reportados: ${totalIssues}`);
  return L.join("\n");
}

function _openReportModal(text, sub) {
  document.getElementById("report-text").value = text;
  document.getElementById("report-modal-sub").textContent = sub || "";
  const m = document.getElementById("report-modal");
  m.style.display = "flex"; m.classList.remove("hidden");
}
function closeReportModal() {
  const m = document.getElementById("report-modal");
  m.style.display = "none"; m.classList.add("hidden");
}
function copyReport() {
  const t = document.getElementById("report-text").value;
  navigator.clipboard.writeText(t).then(() => {
    const b = document.getElementById("report-copy-btn"), o = b.textContent;
    b.textContent = "Copiado!"; setTimeout(() => { b.textContent = o; }, 1500);
  });
}
function exportReport() {
  const t = document.getElementById("report-text").value;
  const blob = new Blob([t], { type: "text/plain;charset=utf-8" });
  _downloadBlob(blob, `erros_conciliacao_${_company || "export"}_${_date || ""}.txt`);
}
function gerarRelatorioErros() {
  const results = _selectedDoneWids().map(w => _movResults[w]).filter(r => r && r.state === "done");
  if (!results.length) return;
  _openReportModal(_buildErrorReportText(results), `${results.length} carteira(s) selecionada(s)`);
}
function gerarRelatorioErrosDetalhe() {
  const r = _movResults[_detWid];
  if (!r || r.state !== "done") return;
  _openReportModal(_buildErrorReportText([r]), r.walletName || _detWid);
}

/* ══════════════════════════════════════════════════════════════════════════════
   Listagem manual + seletor de groupings/carteiras + Implementar (data única/período)
   ────────────────────────────────────────────────────────────────────────────────
   • A grade NÃO abre automaticamente ao escolher empresa/datas: o operador clica
     "Listar carteiras" (loadWallets). Trocar as datas com a grade aberta marca o
     botão como "Atualizar listagem" (stale), sem recarregar sozinho.
   • Seletor OPCIONAL de groupings/carteiras (espelha Processar): restringe o conjunto
     exibido. Escopo = carteiras "Selecionadas"; se nenhuma, união dos groupings
     "Selecionados"; vazio = todas. Aplicado no filtro client-side (_applyFilters).
   • Implementar (tela inicial) → escolha data única (modal atual) OU período: seleção
     de datas (mesmo componente do Processar) e execução ENCADEADA (roll-forward), em
     que cada data usa a anterior como origem (D0→D1→D2…), sobre as carteiras
     SELECIONADAS na grade. Reusa as rotas existentes por passo (sem novo backend). */
