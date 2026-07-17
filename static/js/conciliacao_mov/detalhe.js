/* Conciliação (Mov.) — modal de detalhe da carteira (render completo).
   Escopo global compartilhado; ordem importa. */
function _tbl(head, bodyRows, empty) {
  if (!bodyRows.length) return `<p class="text-[12px] text-gray-400">${empty}</p>`;
  return `<div style="overflow-x:auto"><table class="rp-tbl"><thead><tr>${head}</tr></thead><tbody>${bodyRows.join("")}</tbody></table></div>`;
}
const _DASH = '<span class="text-gray-300">—</span>';

/* Banda vermelha de alerta — "a gerar" (mesmo padrão do bloco de provisões
   esperadas da Posição Projetada). */
function _alertBand(title, sub) {
  return `<div class="mb-3" style="border:1px solid #fca5a5; border-radius:0.5rem; overflow:hidden">
    <div style="background:#fef2f2; color:#7f1d1d; padding:6px 10px; font-size:11px; display:flex; align-items:center; gap:8px; flex-wrap:wrap">
      <span class="rp-expected-prov-pill">${esc(title)}</span>
      <span>${esc(sub)}</span>
    </div>
  </div>`;
}
function _mDetails(title, headerRight, inner) {
  return `<details class="bg-white rounded-xl shadow p-4" open>
    <summary class="cursor-pointer list-none">
      <div class="flex items-center gap-3 flex-wrap">
        <span class="text-sm font-semibold text-gray-800">${esc(title)}</span>
        <span class="ml-auto">${headerRight || ""}</span>
      </div>
    </summary>
    <div class="mt-3">${inner}</div>
  </details>`;
}

/* ── Bloco NAV (3 snapshots: Anterior / Atual / Projetada) — igual à
      Posição Projetada ─────────────────────────────────────────────────── */
function _mNavCell(label, val, kind) {
  if (val === null || val === undefined) return `<div class="text-[10px] text-gray-300">${esc(label)}: —</div>`;
  const f = kind === "money" ? fmtMoney(val) : kind === "nps" ? fmtNum(val, 6)
          : kind === "amt" ? fmtNum(val, 6) : kind === "pct" ? fmtPct(val)
          : kind === "int" ? fmtNum(val, 0) : fmtNum(val, 2);
  let cls = "";
  if (kind === "pct" && Math.abs(nz(val)) >= 1e-6) cls = val > 0 ? "text-green-700" : "text-red-700";
  return `<div class="text-[10px]"><span class="text-gray-500">${esc(label)}:</span> <strong class="${cls}">${f}</strong></div>`;
}
/* Célula de GAP por etapa: verde quando ≈0 (etapa auto-contida — não gera GAP),
   âmbar quando há resíduo. `—` quando a etapa não se aplica (ex.: caixa sem oficial). */
function _mGapStageCell(label, val, title) {
  if (val === null || val === undefined)
    return `<div class="text-[10px] text-gray-300" title="${esc(title || "")}">${esc(label)}: —</div>`;
  const cls = Math.abs(nz(val)) < 0.01 ? "text-green-700" : "text-amber-700";
  return `<div class="text-[10px]" title="${esc(title || "")}"><span class="text-gray-500">${esc(label)}:</span> <strong class="${cls}">${fmtMoney(val)}</strong></div>`;
}
/* ── What-if de provisões ignoradas (#5) — NÃO grava nada ─────────────────────
   Ao ignorar uma provisão que está no NAV simulado, recalcula localmente NAV/cota/
   retornos/GAP da projetada, subtraindo a contribuição da provisão ao NAV. A
   contribuição-ao-NAV de cada provisão é a MESMA regra do servidor: provisões do
   motor e recon entram pelo saldo; oficiais entram só se NÃO deduplicadas (ver
   _official_prov_in_nav_total em pages/conciliacao_mov.py); liquidando na data estão
   FORA do NAV (contribuição 0). Retornos por contribuição NÃO mudam (provisão não
   entra na contribuição), então o GAP move-se pelo ΔNAV inteiro. */
function _offpInNav(p, engineSids, coveredSids) {
  // Mesma regra do servidor (_official_prov_in_nav_total): entra no NAV se o securityId
  // NÃO está coberto por provisão do motor nem por coveredByProvision. sid ausente também
  // conta (não está em nenhum dos conjuntos) — igual ao servidor.
  const s = p.securityId;
  return !engineSids.has(s) && !coveredSids.has(s);
}
function _provKey(bucket, p) {
  return [bucket, p.securityId || "", p.initialDate || "", p.liquidationDate || "",
          p.provisionType || "", Math.round(nz(p.balance) * 100)].join("|");
}
function _whatIfProj(r) {
  if (!_detIgnoredProvs || !_detIgnoredProvs.size) return null;
  // drop = Σ contribuição-ao-NAV das ignoradas; dropEngine/dropOfficial isolam os baldes
  // que entram na etapa 'mov' do GAP (recon é adoção → etapa amDiff).
  let drop = 0, dropEngine = 0, dropOfficial = 0;
  _detIgnoredProvs.forEach(k => {
    const p = _detProvByKey[k]; if (!p) return;
    const c = nz(p.navContribution); drop += c;
    if (p.bucket === "eng") dropEngine += c;
    else if (p.bucket === "off") dropOfficial += c;
  });
  const simNav = r.simNav == null ? null : Math.round((nz(r.simNav) - drop) * 100) / 100;
  const shares = nz(r.shares), fnps = nz(r.formerNavPerShare), fnav = nz(r.formerNav), inflows = nz(r.inflows);
  const simNps = (simNav != null && shares) ? (simNav - inflows) / shares : null;
  const retNps = (simNps != null && fnps) ? (simNps / fnps - 1) : null;
  const retC = r.simReturnContribution;   // provisão não afeta a contribuição
  const gapPct = (retNps != null && retC != null) ? (retNps - nz(retC)) : null;
  const gapCash = (gapPct != null && fnav) ? Math.round(gapPct * fnav * 100) / 100 : null;
  return { simNav, simNps, retNps, gapPct, gapCash, drop, dropEngine, dropOfficial, count: _detIgnoredProvs.size };
}
/* GAP em R$ para um NAV qualquer (mesma fórmula do servidor _gap_cash_for) e sua
   inversa (recupera o NAV da etapa a partir do gap enviado) — usadas p/ recompor os
   GAPs por etapa no cenário what-if da memória de cálculo. */
function _gapCashFor(r, nav) {
  const fnav = nz(r.formerNav), fnps = nz(r.formerNavPerShare), shares = nz(r.shares), inflows = nz(r.inflows), retC = r.simReturnContribution;
  if (nav == null || !shares || !fnps || retC == null || !fnav) return null;
  const nps = (nav - inflows) / shares;
  return Math.round(((nps / fnps - 1) - nz(retC)) * fnav * 100) / 100;
}
function _navMovFromGap(r, gapMov) {
  const fnav = nz(r.formerNav), fnps = nz(r.formerNavPerShare), shares = nz(r.shares), inflows = nz(r.inflows), retC = r.simReturnContribution;
  if (gapMov == null || !fnav || !fnps || !shares || retC == null) return null;
  return fnps * (gapMov / fnav + nz(retC) + 1) * shares + inflows;
}
function _projSubcols(r) {
  const wi = _whatIfProj(r);
  const nav = wi ? wi.simNav : r.simNav, nps = wi ? wi.simNps : r.simNavPerShare;
  const rNps = wi ? wi.retNps : r.simReturnNavPerShare, gPct = wi ? wi.gapPct : r.simGapPct, gCash = wi ? wi.gapCash : r.simGapCash;
  const subCol = (t, cells) => `<div style="padding:0 6px; white-space:nowrap">${t ? `<div class="text-[9px] uppercase tracking-wide text-gray-400" style="border-bottom:1px dashed #e5e7eb;padding-bottom:1px;margin-bottom:1px">${esc(t)}</div>` : ""}${cells.join("")}</div>`;
  // Coluna "ignoradas": quantidade de provisões ignoradas do NAV/GAP (what-if — não grava)
  // + o Δ NAV que elas retiram. Sempre presente (0 quando nada ignorado). As duplicatas do
  // sistema (`duplicatesOfficial`) nascem ignoradas → contam aqui.
  const ignN = (_detIgnoredProvs && _detIgnoredProvs.size) || 0;
  const ignCol = subCol("ignoradas", [_mNavCell("provisões", ignN, "int"), _mNavCell("Δ NAV", wi ? -wi.drop : 0, "money")]);
  return subCol("posição", [_mNavCell("NAV", nav, "money"), _mNavCell("cota", nps, "nps"), _mNavCell("fluxos", r.inflows, "money")])
    + subCol("retornos", [_mNavCell("rent. NAV", rNps, "pct"), _mNavCell("rent. contrib.", r.simReturnContribution, "pct")])
    + subCol("GAP", [_mNavCell("GAP %", gPct, "pct"), _mNavCell("GAP R$", gCash, "money")])
    + ignCol;
}
function _projCardHTML(r) {
  const wiActive = _detIgnoredProvs && _detIgnoredProvs.size;
  return `<div id="det-proj-card" class="${wiActive ? "det-proj-whatif" : ""}" style="flex:1 0 auto; min-width:230px; padding:5px 10px; border-right:1px solid #f3f4f6">
    <div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600;margin-bottom:2px">Projetada (sim.)${wiActive ? ' · <span style="color:#b45309">what-if</span>' : ""}</div>
    <div style="display:flex; gap:4px; flex-wrap:nowrap">${_projSubcols(r)}</div></div>`;
}
function _renderProjCard() {
  const r = _movResults[_detWid]; if (!r) return;
  const el = document.getElementById("det-proj-card"); if (!el) return;
  const wiActive = _detIgnoredProvs && _detIgnoredProvs.size;
  el.className = wiActive ? "det-proj-whatif" : "";
  el.innerHTML = `<div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600;margin-bottom:2px">Projetada (sim.)${wiActive ? ' · <span style="color:#b45309">what-if</span>' : ""}</div>
    <div style="display:flex; gap:4px; flex-wrap:nowrap">${_projSubcols(r)}</div>`;
}
function _updateProvWhatIfSummary() {
  const el = document.getElementById("det-prov-whatif"); if (!el) return;
  const r = _movResults[_detWid] || {};
  const wi = _whatIfProj(r);
  el.innerHTML = wi ? ` · <span style="color:#b45309;font-weight:600">${wi.count} ignorada(s) · Δ NAV ${fmtMoney(-wi.drop)}</span>` : "";
}
function toggleIgnoreProv(key) {
  if (_detIgnoredProvs.has(key)) _detIgnoredProvs.delete(key); else _detIgnoredProvs.add(key);
  const ign = _detIgnoredProvs.has(key);
  document.querySelectorAll(`#det-body tr[data-provkey="${_cssEsc(key)}"]`).forEach(tr => {
    tr.classList.toggle("prov-ignored", ign);
    const btn = tr.querySelector(".prov-ign-btn");
    if (btn) {
      btn.classList.toggle("rp-btn-primary", ign); btn.classList.toggle("rp-btn-muted", !ign);
      btn.textContent = ign ? "↩ considerar" : "⊘ ignorar";
      btn.title = ign ? "Voltar a considerar esta provisão no NAV/GAP da projetada"
                      : "Ignorar esta provisão nos cálculos de NAV e GAP da projetada (what-if — não grava)";
    }
  });
  _renderProjCard();
  _updateProvWhatIfSummary();
}
function _mNavBlock(r) {
  const d = r.diff || {}, real = d.real || {};
  const aRetNps = real.returnNavPerShare, aRetC = real.returnContribution;
  const aGap = (aRetNps != null && aRetC != null) ? (nz(aRetNps) - nz(aRetC)) : null;
  const aGapCash = (aGap != null && r.formerNav) ? aGap * nz(r.formerNav) : null;
  // Sub-coluna: aproveita o espaço HORIZONTAL (posição | retornos | GAP lado a
  // lado) pra reduzir a altura do cabeçalho. white-space:nowrap dá à sub-coluna
  // a largura do conteúdo, e o container abaixo é nowrap — assim o grupo GAP
  // nunca cai pra baixo de posição/retornos (o card cresce ou, em tela estreita,
  // o card inteiro quebra de linha pelo flex-wrap do rp-nav-content).
  const subCol = (titulo, cells) => `<div style="padding:0 6px; white-space:nowrap">
    ${titulo ? `<div class="text-[9px] uppercase tracking-wide text-gray-400" style="border-bottom:1px dashed #e5e7eb;padding-bottom:1px;margin-bottom:1px">${esc(titulo)}</div>` : ""}${cells.join("")}</div>`;
  const card = (titulo, subcols) => `<div style="flex:1 0 auto; min-width:230px; padding:5px 10px; border-right:1px solid #f3f4f6">
    <div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600;margin-bottom:2px">${esc(titulo)}</div>
    <div style="display:flex; gap:4px; flex-wrap:nowrap">${subcols.join("")}</div></div>`;
  const anterior = card("Anterior (origem)", [
    subCol("", [_mNavCell("NAV", r.formerNav, "money"), _mNavCell("cota", r.formerNavPerShare, "nps"), _mNavCell("amount", r.shares, "amt")]),
  ]);
  const atual = d.hasTarget ? card("Atual (alvo real)", [
    subCol("posição", [_mNavCell("NAV", real.nav, "money"), _mNavCell("cota", real.navPerShare, "nps"), _mNavCell("fluxos", real.inAndOutFlows, "money")]),
    subCol("retornos", [_mNavCell("rent. NAV", aRetNps, "pct"), _mNavCell("rent. contrib.", aRetC, "pct")]),
    subCol("GAP", [_mNavCell("GAP %", aGap, "pct"), _mNavCell("GAP R$", aGapCash, "money")]),
  ]) : card("Atual (alvo real)", [subCol("", [`<div class="text-[10px] text-gray-300 italic">sem navPackage / unprocessed do alvo</div>`])]);
  // Card projetado: id fixo p/ re-render in-place quando o what-if de provisões muda (#5).
  const projetada = _projCardHTML(r);
  // GAPs por ETAPA — verifica que cada etapa da projeção é "auto-contida" (não gera GAP)
  // e localiza de ONDE vem um GAP: mov (projeção) · amDiff (≈0, NAV-neutro) · caixa (proj−oficial).
  const gs = r.gapStages || {};
  const etapas = card("GAPs por etapa", [
    subCol("", [
      _mGapStageCell("mov", gs.mov,
        "GAP da projeção: posição-origem + movimentos + provisões (derivadas/oficiais), ANTES da adoção amDiff. É o resíduo da projeção em si (estimativa de qtd, IRRF de fundo, etc.)."),
      _mGapStageCell("amDiff", gs.amDiff,
        "Incremento de GAP da etapa amountDifference (adoção da qtd-alvo: reconProvision / coberta por provisão oficial). DEVE ser ~0 — a adoção soma o P&L intraday Δqty×(PU−execPrice) ao NAV (cota ao PU + provisão ao execPrice) E o MESMO valor à contribuição (intradayContribution), então NAV e contribuição sobem juntos e o GAP não muda. ≠0 indicaria quebra dessa neutralidade."),
      _mGapStageCell("caixa", gs.cash,
        "Caixa PROJETADO − caixa OFICIAL (INFORMATIVO). Não é GAP de retorno e NÃO classifica ajustes — o caixa-âncora foi eliminado (caixa de carteira não isola por ativo). É só o resíduo de caixa exibido; — quando não há caixa oficial no alvo (forward)."),
    ]),
  ]);
  return `<div class="rp-nav-bar" style="border-bottom:1px solid #e5e7eb; background:#fafafa">
    <div class="rp-nav-content" style="display:flex; flex-wrap:wrap; padding:3px 10px 4px">${anterior}${atual}${projetada}${etapas}</div>
  </div>`;
}

/* ── Tabela de securities (estilo prévia da Posição Projetada) ──────────── */
function _mDeltaQty(d) {
  if (Math.abs(d) < 1e-6) return `<span class="rp-diff-zero">0</span>`;
  return `<span class="${d > 0 ? "rp-diff-pos" : "rp-diff-neg"}">${d > 0 ? "+" : ""}${fmtNum(d, 4)}</span>`;
}
function _mDiffCell(rw, real, dqty, dpu, dbal) {
  if (!real) return `<span class="rp-diff-na">—</span>`;
  const CENT = 0.01;
  const puRef = Math.max(Math.abs(nz(rw.pu)), Math.abs(nz(real.pu)));
  const qtyRef = Math.max(Math.abs(nz(rw.quantity)), Math.abs(nz(real.q)));
  const cls = v => (v > 0 ? "rp-diff-pos" : "rp-diff-neg");
  const parts = [];
  if (Math.abs(dqty) * puRef >= CENT) parts.push(`<div class="${cls(dqty)}">qtd: ${fmtNum(dqty, 4)}</div>`);
  if (Math.abs(dpu) * qtyRef >= CENT) parts.push(`<div class="${cls(dpu)}">pu: ${fmtNum(dpu, 6)}</div>`);
  if (Math.abs(dbal) >= CENT) parts.push(`<div class="${cls(dbal)}">sld: ${fmtMoney(dbal)}</div>`);
  if (!parts.length) return `<span class="rp-diff-zero" title="Projetada bate com a unprocessed do alvo (impacto < R$ 0,01)">—</span>`;
  return `<div class="text-[11px]">${parts.join("")}</div>`;
}
/* Botão de copiar securityId (reusa copyWalletId — cópia genérica + feedback ✓). */
function _copyBtn(text) {
  if (!text) return "";
  return `<button type="button" onclick="copyWalletId('${esc(text)}', this)" title="Copiar securityId (${esc(text)})" class="shrink-0 text-gray-400 hover:text-blue-600 p-0.5 rounded leading-none align-middle"><svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>`;
}
/* ── Colunas de MÉTRICAS por ativo (#3) — só do lado PROJETADO ────────────────
   contrib $  = contribuição do ativo (R$) para o returnContribution
   rent PU    = PU projetada ÷ PU anterior − 1
   rent Contrib = contribuição do ativo ÷ NAV origem (fatia no returnContribution)
   transac    = Σ saldo das transactions da janela do ativo
   prov       = Σ saldo das provisions do ativo (motor + oficiais + recon + liquidando)
   O lado ALVO não tem contribuição/rentabilidade por ativo (só qtd/PU/saldo), então
   contrib$/rent* aparecem só na projetada; transac/prov são um valor único do ativo.
   Nascem OCULTAS (classe .mov-extra escondida por .det-hide-extra). */
function _mMoneyOrDash(v) {
  if (v === null || v === undefined || Math.abs(nz(v)) < 0.005) return `<span class="rp-diff-zero">—</span>`;
  return `<span class="${nz(v) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(v)}</span>`;
}
function _mPctCell(v) {
  if (v === null || v === undefined) return _DASH;
  return `<span class="${v < 0 ? "rp-diff-neg" : ""}">${fmtPct(v)}</span>`;
}
function _mExtraColsHead() {
  return `<th class="mov-extra" style="text-align:right" title="Contribuição do ativo (R$) para o returnContribution da carteira">Contrib $</th>
    <th class="mov-extra" style="text-align:right" title="Rentabilidade do PU do ativo: PU projetada ÷ PU anterior − 1">Rent PU</th>
    <th class="mov-extra" style="text-align:right" title="Rentabilidade por contribuição: contribuição do ativo ÷ NAV origem">Rent Contrib</th>
    <th class="mov-extra" style="text-align:right" title="Σ saldo das transações da janela associadas a este ativo">Transac</th>
    <th class="mov-extra" style="text-align:right" title="Σ saldo das provisões associadas a este ativo (motor + oficiais + recon + liquidando na data)">Prov</th>`;
}
// rw = linha projetada (ou null p/ caixa); sid usado p/ transac/prov (valor do ativo, não por lado).
function _mExtraCells(sid, rw, ctx) {
  const tv = sid ? ctx.txnBySid[sid] : null;
  const pv = sid ? ctx.provBySid[sid] : null;
  let contribCell = _DASH, rentPuCell = _DASH, rentContribCell = _DASH;
  if (rw) {
    const fp = nz(rw.formerPu), pu = nz(rw.pu), contrib = nz(rw.contribution);
    const rentPu = Math.abs(fp) > 1e-9 ? (pu / fp - 1) : null;
    const rentContrib = ctx.formerNav ? (contrib / ctx.formerNav) : null;
    contribCell = _mMoneyOrDash(rw.contribution);
    rentPuCell = _mPctCell(rentPu);
    rentContribCell = _mPctCell(rentContrib);
  }
  return `<td class="mov-extra">${contribCell}</td><td class="mov-extra">${rentPuCell}</td><td class="mov-extra">${rentContribCell}</td><td class="mov-extra">${_mMoneyOrDash(tv)}</td><td class="mov-extra">${_mMoneyOrDash(pv)}</td>`;
}
function _mExtraColsBtn() {
  return `<button id="det-extra-cols-btn" type="button" onclick="toggleDetExtraCols()" class="fb-chip${_detExtraCols ? " active" : ""}" data-kind="nav"
    title="Mostrar/ocultar colunas de métricas: Contrib $, Rent PU, Rent Contrib, Transac, Prov">
    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h10M4 18h7"/></svg>${_detExtraCols ? " Ocultar métricas" : " Métricas"}</button>`;
}
function toggleDetExtraCols() {
  _detExtraCols = !_detExtraCols;
  const t = document.getElementById("det-sec-table");
  if (t) t.classList.toggle("det-hide-extra", !_detExtraCols);
  const b = document.getElementById("det-extra-cols-btn");
  if (b) { b.classList.toggle("active", _detExtraCols); b.lastChild.textContent = _detExtraCols ? " Ocultar métricas" : " Métricas"; }
}
function _mSecRow(rw, real, isOnlyCalc, coveredByProv, onlyCalcInfo, ctx) {
  const sid = rw.securityId;
  let dqty = 0, dpu = 0, dbal = 0;
  if (real) { dqty = nz(rw.quantity) - nz(real.q); dpu = nz(rw.pu) - nz(real.pu); dbal = nz(rw.balance) - nz(real.bal); }
  let cls = "";
  if (rw.matured) cls = "rp-row-matured";
  if (real && Math.abs(dqty) > 1e-6) cls = "rp-row-qty-mismatch";
  else if (!cls && real && Math.abs(dbal) > 0.01) cls = "rp-row-has-diff";
  else if (!cls && isOnlyCalc) cls = "rp-row-has-diff";
  const flags = [];
  if (!rw.mapped) flags.push(`<span class="rp-flag rp-flag-missingB1Price">não mapeado</span>`);
  if (rw.matured) flags.push(`<span class="rp-flag rp-flag-buysell">vencido</span>`);
  if (isOnlyCalc) {
    flags.push(`<span class="rp-flag rp-flag-missingB1Price" title="${esc((onlyCalcInfo && onlyCalcInfo.because) || "Ativo só na carteira movimentada")}">só no movimentado</span>`);
    // onlyCalc é FLAG-ONLY → pílula de revisão manual + confiança (sem escrita).
    flags.push(_reviewBadge(onlyCalcInfo && onlyCalcInfo.because).trim());
    if (onlyCalcInfo && onlyCalcInfo.confidence) { const b = _confBadge(onlyCalcInfo.confidence, onlyCalcInfo.because); if (b) flags.push(b.trim()); }
  }
  if (coveredByProv) flags.push(`<span class="rp-flag rp-flag-buysell" style="background:#dcfce7;color:#166534" title="Divergência de qtd já coberta por provisão existente no sistema">coberto por provisão</span>`);
  if (real && real.adopted) flags.push(`<span class="rp-flag rp-flag-buysell" style="background:#dbeafe;color:#1e40af" title="Qtd projetada ADOTADA da posição-alvo (liquidação futura, sem transaction na janela): a provisão de ajuste casa o caixa pendente — NAV-neutro, não altera o GAP">qtd adotada do alvo</span>`);
  if (rw.adoptedFromTarget) flags.push(`<span class="rp-flag rp-flag-buysell" style="background:#dbeafe;color:#1e40af" title="Ativo presente SÓ na posição-ALVO (confirmado na processed-position oficial, qtd ≠ 0; entrou por transferência/reestruturação sem transação na janela). Copiado p/ a projeção — sem P&L (contribuição 0), entra no NAV pelo saldo.">copiado do alvo</span>`);
  // (Não há mais flag "preço divergente": o saldo-alvo é avaliado ao PU em uso, então
  //  divergência só-de-PU da unprocessed não ocorre — o saldo-alvo segue o PU em uso.)
  if (real && real.conf) { const b = _confBadge(real.conf, real.because); if (b) flags.push(b.trim()); }
  return `<tr class="${cls}">
    <td data-col="ativo" class="rp-tbl-left"><span class="inline-flex items-center gap-1">${esc(rw.securityName || sid || "—")}${_copyBtn(sid)}</span></td>
    <td>${esc(rw.pricingType || "—")}</td>
    <td data-col="qtyOriginal">${fmtNum(rw.formerQuantity, 4)}</td>
    <td>${fmtNum(rw.formerPu, 6)}</td>
    <td>${fmtMoney(rw.formerBalance)}</td>
    <td data-col="qtyTarget">${real ? fmtNum(real.q, 4) : _DASH}</td>
    <td>${real ? fmtNum(real.pu, 6) : _DASH}</td>
    <td>${real ? fmtMoney(real.bal) : _DASH}</td>
    <td data-col="qtyRep">${fmtNum(rw.quantity, 4)}</td>
    <td>${fmtNum(rw.pu, 6)}</td>
    <td>${fmtMoney(rw.balance)}</td>
    ${_mExtraCells(sid, rw, ctx)}
    <td>${real ? _mDeltaQty(dqty) : _DASH}</td>
    <td data-col="diff" class="rp-tbl-left">${_mDiffCell(rw, real, dqty, dpu, dbal)}</td>
    <td class="rp-tbl-left">${flags.join(" ") || _DASH}</td>
  </tr>`;
}
function _mOnlyRealRow(x, ctx) {
  // onlyReal é FLAG-ONLY → "só no alvo" + revisão manual + confiança (tooltip
  // com o "porquê"); nenhuma escrita é gerada automaticamente p/ este caso.
  const flags = [`<span class="rp-flag rp-flag-missingB1Price" title="${esc(x.because || "Ativo só na unprocessed do alvo")}">só no alvo</span>`,
                 _reviewBadge(x.because).trim()];
  if (x.confidence) { const b = _confBadge(x.confidence, x.because); if (b) flags.push(b.trim()); }
  return `<tr class="rp-row-has-diff">
    <td data-col="ativo" class="rp-tbl-left"><span class="inline-flex items-center gap-1">${esc(x.securityName || x.securityId || "—")}${_copyBtn(x.securityId)}</span></td>
    <td>${_DASH}</td>
    <td data-col="qtyOriginal">${_DASH}</td><td>${_DASH}</td><td>${_DASH}</td>
    <td data-col="qtyTarget">${fmtNum(x.quantity, 4)}</td><td>${_DASH}</td><td>${fmtMoney(x.balance)}</td>
    <td data-col="qtyRep">${_DASH}</td><td>${_DASH}</td><td>${_DASH}</td>
    ${_mExtraCells(x.securityId, null, ctx)}
    <td>${_DASH}</td>
    <td data-col="diff" class="rp-tbl-left"><span class="rp-diff-neg text-[11px]">só no alvo</span></td>
    <td class="rp-tbl-left">${flags.join(" ")}</td>
  </tr>`;
}
function _mCashRow(r, ctx) {
  const c = r.cash || {}, d = r.diff || {}, oc = d.officialCash;
  // Caixa-âncora ELIMINADO: sem verdito BATE/NÃO bate. Mostra só o resíduo (informativo).
  const cd = (c.new != null && oc != null) ? nz(c.new) - nz(oc) : null;
  const cdSig = cd != null && Math.abs(cd) >= 0.01;
  const cdCls = cdSig ? (cd >= 0 ? "rp-diff-pos" : "rp-diff-neg") : "rp-diff-zero";
  const cdHTML = cdSig ? (cd >= 0 ? "+" : "") + fmtMoney(cd) : "—";
  const badge = `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-500" title="Resíduo de caixa = projetado − oficial do alvo (informativo — não classifica ajustes)">resíduo ${oc != null ? fmtMoney(cd) : "—"}</span>`;
  return `<tr class="rp-row-cash">
    <td class="rp-tbl-left">Caixa</td>
    <td class="text-gray-400">—</td>
    <td data-col="qtyOriginal" class="text-gray-400">—</td><td class="text-gray-400">—</td><td>${fmtMoney(c.former)}</td>
    <td data-col="qtyTarget" class="text-gray-400">—</td><td class="text-gray-400">—</td><td>${oc != null ? fmtMoney(oc) : _DASH}</td>
    <td data-col="qtyRep" class="text-gray-400">—</td><td class="text-gray-400">—</td><td>${fmtMoney(c.new)}</td>
    ${_mExtraCells(null, null, ctx)}
    <td class="text-gray-400">—</td>
    <td data-col="diff" class="rp-tbl-left ${cdCls}" title="Δ = caixa movimentado − caixa oficial (alvo)">${cdHTML}</td>
    <td class="rp-tbl-left">${badge}</td>
  </tr>`;
}
/* Pílula de confiança da classificação (alta/média/baixa) + o "porquê" no tooltip.
   Usada nas linhas a gerar (recon prov/txn) e nas divergências da tabela de ativos. */
function _confBadge(conf, because) {
  const m = { high: ["alta", "#dcfce7", "#166534"], medium: ["média", "#fef9c3", "#854d0e"], low: ["baixa", "#fee2e2", "#991b1b"] };
  const x = m[conf]; if (!x) return "";
  return ` <span class="rp-flag" style="background:${x[1]};color:${x[2]}" title="${esc(because || "")}">conf. ${x[0]}</span>`;
}
/* Pílula "⚠ revisar manual": achados de onlyCalc/onlyReal/PU-only que são
   FLAG-ONLY — diagnóstico, sem geração de escrita automática (precisam validação
   com dado real). O "porquê" vai no tooltip. */
function _reviewBadge(because) {
  return ` <span class="rp-flag" style="background:#fef3c7;color:#92400e" title="${esc(because || "Requer revisão manual — nenhuma escrita é gerada automaticamente")}">⚠ revisar manual</span>`;
}
/* Critério de "atividade" do ativo — mesmo espírito do filtro da tela de
   Conciliação (`_secHasActivity`): divergência REAL vs alvo (qtd/PU/saldo
   além da tolerância), movimento por transação na janela, vencimento,
   cupom/amort, não-mapeado ou ativo só de um lado. */
function _mRowActive(rw, real, isOnlyCalc, tx) {
  if (!rw.mapped) return true;
  if (rw.matured) return true;
  if (isOnlyCalc) return true;
  if (rw.couponAmort != null && Math.abs(nz(rw.couponAmort)) > 0.005) return true;
  if (tx && (tx.c > 0 || Math.abs(tx.b) > 0.005)) return true;
  // Movimento entre datas: a QUANTIDADE mudou da posição anterior (origem) para a
  // atual (alvo real quando há; senão a projetada). PU/saldo variam todo dia por
  // marcação a mercado — por isso o critério aqui é só quantidade (movimento real).
  if (Math.abs(nz(rw.formerQuantity) - nz(real ? real.q : rw.quantity)) > 1e-6) return true;
  if (real) {
    if (Math.abs(nz(rw.quantity) - nz(real.q)) > 1e-6) return true;
    if (Math.abs(nz(rw.balance) - nz(real.bal)) > 0.01) return true;
    const qtyRef = Math.max(Math.abs(nz(rw.quantity)), Math.abs(nz(real.q)));
    if (Math.abs(nz(rw.pu) - nz(real.pu)) * qtyRef > 0.01) return true;
  }
  return false;
}
function _mSecRowsModel(r) {
  const d = r.diff || {}, hasTarget = !!d.hasTarget;
  const divMap = {}; (d.diverged || []).forEach(x => divMap[x.securityId] = x);
  const onlyCalcMap = {}; (d.onlyCalc || []).forEach(x => onlyCalcMap[x.securityId] = x);
  const txnBySid = {};
  (r.transactions || []).forEach(t => {
    const s = t.securityId || ""; if (!s) return;
    const o = txnBySid[s] || { c: 0, b: 0 }; o.c++; o.b += nz(t.balance); txnBySid[s] = o;
  });
  return (r.rows || []).map(rw => {
    let real = null, isOnlyCalc = false, coveredByProv = false, onlyCalcInfo = null;
    if (hasTarget && rw.securityId) {
      const dv = divMap[rw.securityId];
      if (dv) { real = { q: dv.realQuantity, pu: dv.realPu, bal: dv.realBalance, conf: dv.confidence, because: dv.because, needsManualReview: dv.needsManualReview, adopted: !!dv.adoptedTargetQty }; coveredByProv = !!dv.coveredByProvision; }
      else if (onlyCalcMap[rw.securityId]) { isOnlyCalc = true; onlyCalcInfo = onlyCalcMap[rw.securityId]; }   // só no movimentado
      else real = { q: rw.quantity, pu: rw.pu, bal: rw.balance }; // bate
    }
    // Linha com qtd ADOTADA do alvo fica com Δqty≈0 (projetada=alvo) → o critério de
    // atividade não a pegaria; mantém visível sob o filtro (foi um evento de provisão).
    const active = _mRowActive(rw, real, isOnlyCalc, txnBySid[rw.securityId || ""]) || coveredByProv || !!(real && real.adopted);
    return { rw, real, isOnlyCalc, active, coveredByProv, onlyCalcInfo };
  });
}
function _mSecTable(r, model) {
  const d = r.diff || {};
  // Mapas por securityId p/ as colunas de métricas (#3): saldo das transactions e das
  // provisões (todos os baldes) do ativo. Valor único do ativo (não é "por lado").
  const ctx = { txnBySid: {}, provBySid: {}, formerNav: nz(r.formerNav) };
  (r.transactions || []).forEach(t => { const s = t.securityId; if (s) ctx.txnBySid[s] = nz(ctx.txnBySid[s]) + nz(t.balance); });
  [].concat(r.provisions || [], r.reconProvisions || [], r.officialProvisions || [], r.liquidatingProvisions || [])
    .forEach(p => { const s = p.securityId; if (s) ctx.provBySid[s] = nz(ctx.provBySid[s]) + nz(p.balance); });
  const secModel = _detSecFilter ? model.filter(m => m.active) : model;
  const body = [];
  secModel.forEach(m => body.push(_mSecRow(m.rw, m.real, m.isOnlyCalc, m.coveredByProv, m.onlyCalcInfo, ctx)));
  (d.onlyReal || []).forEach(x => body.push(_mOnlyRealRow(x, ctx)));   // só no alvo = sempre relevante
  body.push(_mCashRow(r, ctx));
  // Provisões NÃO entram na tabela de securities — aparecem no bloco dedicado de
  // Provisões (_mProvBlock) logo abaixo, evitando duplicação.
  if (_detSecFilter && !secModel.length) {
    body.unshift(`<tr><td colspan="19" class="rp-tbl-left" style="text-align:center;color:#9ca3af;padding:10px 0">Nenhum ativo com divergência/movimento (filtro ligado). Veja Caixa/Provisões abaixo ou clique em "Filtrado" para mostrar todos.</td></tr>`);
  }
  const head = `<th data-col="ativo" class="rp-tbl-left">Ativo</th>
    <th>Pricing</th>
    <th data-col="qtyOriginal">Qtd anterior</th><th>PU anterior</th><th>Saldo anterior</th>
    <th data-col="qtyTarget" title="Quantidade do alvo (unprocessed do alvo — autoritativa)">Qtd alvo</th><th title="PU EM USO — oficial da processed-position quando o alvo está processado; o PU bruto da unprocessed não é mais usado nem comparado">PU alvo</th><th title="Saldo do alvo avaliado ao PU em uso (Qtd alvo × PU em uso)">Saldo alvo</th>
    <th data-col="qtyRep">Qtd projetada</th><th title="PU em uso (mesma fonte do 'PU alvo')">PU projetada</th><th>Saldo projetada</th>
    ${_mExtraColsHead()}
    <th title="projetada − alvo">Δ Qtd</th>
    <th data-col="diff" class="rp-tbl-left" title="Diferença projetada − alvo (ambos avaliados ao PU em uso) → reflete só a quantidade">Diff vs alvo</th>
    <th class="rp-tbl-left">Flags</th>`;
  const note = d.hasTarget ? ""
    : `<p class="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-2">${esc(d.note || "Sem unprocessed do alvo para comparar — colunas 'alvo' e 'diff vs alvo' ficam vazias.")}</p>`;
  // A unprocessed do alvo não estava pré-processada (sem securityId) — a posição-alvo veio da
  // processed-position (oficial). Sinaliza a procedência p/ o operador não confundir com defeito.
  const srcNote = (r.targetSource === "processed-fallback")
    ? `<p class="text-[12px] text-sky-800 bg-sky-50 border border-sky-200 rounded px-3 py-2 mb-2">⚙ Posição-alvo obtida da <b>processed-position</b> (a unprocessed do alvo ainda não estava pré-processada). Comparação válida; sem isso a tela apontaria toda a carteira para revisão.</p>`
    : "";
  return note + srcNote + `<table id="det-sec-table" class="rp-tbl${_detExtraCols ? "" : " det-hide-extra"}"><thead><tr>${head}</tr></thead><tbody>${body.join("")}</tbody></table>`;
}
function _mSecFilterBtn() {
  const funnel = _detSecFilter
    ? `<svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z"/></svg>`
    : `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z"/></svg>`;
  return `<button id="det-sec-filter-btn" type="button" onclick="toggleDetSecFilter()" class="fb-chip${_detSecFilter ? " active" : ""}" data-kind="nav"
    title="Mostra só ativos com divergência/movimento (mesmo critério da Conciliação)">${funnel}${_detSecFilter ? " Filtrado" : " Filtrar"}</button>`;
}
function toggleDetSecFilter() {
  _detSecFilter = !_detSecFilter;
  if (_detWid) openDetail(_detWid);
}

/* ── Bloco Provisões — JÁ NO SISTEMA (verde) + LIQUIDANDO na data (amarelo, fora
      do NAV) + do movimento + A GERAR (vermelho). stockETF marcado; linha-resumo
      da liquidação consolidada B3 ao final. ─────────────────────────────────── */
function _etfTag(p) {
  return p.isStockEtf ? ` <span class="rp-flag" style="background:#e0e7ff;color:#3730a3" title="stockETF — liquidação consolidada na B3">stockETF</span>` : "";
}
// Item 1 — pill do casamento provisão-que-liquida ↔ liquidação de caixa.
// verde = ativo+valor batem · amarelo = só o valor · vermelho = nada semelhante.
const _LIQ_MATCH = {
  green:  ["#dcfce7", "#166534", "caixa casado"],
  yellow: ["#fef9c3", "#854d0e", "caixa provável"],
  red:    ["#fee2e2", "#991b1b", "sem caixa"],
};
function _liqMatchPill(p) {
  const [bg, fg, label] = _LIQ_MATCH[p.matchStatus] || _LIQ_MATCH.red;
  return `<span class="rp-flag" style="background:${bg};color:${fg}" title="${esc(p.matchBecause || "")}">${label}</span>`;
}
const _LIQ_BORDER = { green: "#16a34a", yellow: "#ca8a04", red: "#dc2626" };
// Célula final "ignorar/considerar" (#5) — só p/ provisões que estão NO NAV simulado;
// as de fora do NAV (liquidam na data, oficiais deduplicadas) não têm efeito → rótulo.
function _provActionCell(key, inNav, ign) {
  if (!inNav) return `<td class="rp-tbl-left"><span class="text-[10px] text-gray-300" title="Provisão fora do NAV simulado — ignorar não altera NAV/GAP">fora do NAV</span></td>`;
  return `<td class="rp-tbl-left"><button type="button" class="prov-ign-btn rp-btn ${ign ? "rp-btn-primary" : "rp-btn-muted"}" style="padding:0.1rem 0.4rem" onclick="toggleIgnoreProv('${esc(key)}')" title="${ign ? "Voltar a considerar esta provisão no NAV/GAP da projetada" : "Ignorar esta provisão nos cálculos de NAV e GAP da projetada (what-if — não grava)"}">${ign ? "↩ considerar" : "⊘ ignorar"}</button></td>`;
}
function _mProvBlock(r) {
  const provs = r.provisions || [], rp = r.reconProvisions || [], offp = r.officialProvisions || [], liq = r.liquidatingProvisions || [];
  _detProvByKey = {};   // reconstruído a cada render do bloco (chave -> {navContribution, inNav})
  // Dedup das oficiais idêntica ao servidor (_official_prov_in_nav_total): entra no NAV
  // só se o securityId NÃO está coberto por provisão do motor nem por coveredByProvision.
  const engineSids = new Set(provs.map(p => p.securityId).filter(Boolean));
  const coveredSids = new Set(((r.diff || {}).diverged || []).filter(d => d.coveredByProvision).map(d => d.securityId));
  // Envolve uma provisão numa <tr> com data-provkey + registro do navContribution + botão.
  const wrap = (bucket, p, inNav, navContribution, baseClass, style, inner) => {
    const key = _provKey(bucket, p);
    _detProvByKey[key] = { navContribution: inNav ? nz(navContribution) : 0, inNav: !!inNav, bucket };
    const ign = _detIgnoredProvs.has(key);
    const cls = ((baseClass ? baseClass + " " : "") + (ign ? "prov-ignored" : "")).trim();
    return `<tr data-provkey="${esc(key)}"${cls ? ` class="${cls}"` : ""}${style ? ` style="${style}"` : ""}>${inner}${_provActionCell(key, inNav, ign)}</tr>`;
  };
  const head = `<th>Início</th><th>Liquidação</th><th class="rp-tbl-left">Tipo</th><th class="rp-tbl-left">Descrição</th><th class="rp-tbl-left">Ativo</th><th style="text-align:right">Δ Qtd</th><th style="text-align:right">PU</th><th style="text-align:right">Saldo</th><th class="rp-tbl-left">Origem</th><th class="rp-tbl-left" title="Ignorar a provisão dos cálculos de NAV/GAP da projetada (what-if — não grava)">NAV</th>`;
  const body = [];
  offp.forEach(p => body.push(wrap("off", p, _offpInNav(p, engineSids, coveredSids), p.balance, "", "", `
    <td>${esc(p.initialDate || "—")}</td><td>${esc(p.liquidationDate || "—")}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(p.provisionType || "—")}</td>
    <td class="rp-tbl-left" title="${esc(p.provisionSource || "")}">${esc(p.description || p.provisionSource || "—")}</td>
    <td class="rp-tbl-left"><span class="inline-flex items-center gap-1">${esc(p.securityName || p.securityId || "—")}${_copyBtn(p.securityId)}</span></td>
    <td class="text-gray-300">—</td><td class="text-gray-300">—</td>
    <td class="${nz(p.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(p.balance)}</td>
    <td class="rp-tbl-left"><span class="rp-flag rp-flag-buysell" style="background:#dcfce7;color:#166534">no sistema</span>${_etfTag(p)}</td>`)));
  liq.forEach(p => body.push(wrap("liq", p, false, 0, "rp-row-provision", `border-left:3px solid ${_LIQ_BORDER[p.matchStatus] || _LIQ_BORDER.red}`, `
    <td>${esc(p.initialDate || "—")}</td><td>${esc(p.liquidationDate || "—")}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(p.provisionType || "—")}</td>
    <td class="rp-tbl-left" title="${esc(p.provisionSource || "")}">${esc(p.description || p.provisionSource || "—")}</td>
    <td class="rp-tbl-left"><span class="inline-flex items-center gap-1">${esc(p.securityName || p.securityId || "—")}${_copyBtn(p.securityId)}</span></td>
    <td class="text-gray-300">—</td><td class="text-gray-300">—</td>
    <td class="${nz(p.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(p.balance)}</td>
    <td class="rp-tbl-left">${_liqMatchPill(p)} <span class="rp-flag" style="background:#fef9c3;color:#854d0e" title="Liquida nesta data — o saldo já virou caixa, NÃO está no NAV do alvo (vem do envelope da origem)">liquida na data · fora do NAV</span>${_etfTag(p)}</td>`)));
  provs.forEach(p => body.push(wrap("eng", p, true, p.balance, "", "", `
    <td>${esc(p.initialDate || "—")}</td><td>${esc(p.liquidationDate || "—")}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(p.provisionType || "buySell")}</td>
    <td class="rp-tbl-left">${esc(p.description || "—")}</td>
    <td class="rp-tbl-left">${esc(p.securityName || p.securityId || "—")}</td>
    <td class="text-gray-300">—</td><td class="text-gray-300">—</td>
    <td class="${nz(p.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(p.balance)}</td>
    <td class="rp-tbl-left"><span class="rp-flag rp-flag-buysell">${p.provisionSource === "subscription" ? "subscrição · cota futura" : "liq. futura"}</span>${p.duplicatesOfficial ? ` <span class="rp-flag" style="background:#fef3c7;color:#92400e" title="Já existe uma provisão IDÊNTICA no sistema (mesmo valor e tipo). Gerada, mas nasce pré-marcada como IGNORAR no NAV/GAP p/ não contar em dobro. Clique em ↩ considerar p/ reincluir.">duplica no sistema · ignorada</span>` : ""}</td>`)));
  rp.forEach(p => body.push(wrap("recon", p, true, p.balance, "rp-row-qty-mismatch", "", `
    <td>${esc(p.initialDate || "—")}</td><td>${esc(p.liquidationDate || "—")}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(p.provisionType || "buySell")}</td>
    <td class="rp-tbl-left">${esc(p.description || "—")}</td>
    <td class="rp-tbl-left">${esc(p.securityName || p.securityId || "—")}</td>
    <td>${fmtNum(p.amountDifference, 2)}</td><td title="${esc(p.priceSource === "pu-alvo" ? "PU em uso (oficial da processed-position quando disponível) — proxy do execPrice do trade ausente/futuro, que não é conhecido" : "")}">${fmtNum(p.executionPrice, 4)}${p.priceSource === "pu-alvo" ? ' <span class="text-gray-400 text-[9px]">PU</span>' : ""}</td>
    <td class="rp-diff-neg">${fmtMoney(p.balance)}</td>
    <td class="rp-tbl-left">${p.coveredByOfficial ? `<span class="rp-flag rp-flag-buysell" style="background:#e5e7eb;color:#4b5563">reconciliação</span> <span class="rp-flag" style="background:#fef3c7;color:#92400e" title="Já existe uma provisão OFICIAL no sistema cobrindo esta divergência de quantidade (MESMO ativo — ex.: a 'Compra'/subscrição do fundo). A provisão de ajuste é GERADA para você VER a reconciliação, mas nasce IGNORADA no NAV/GAP (o saldo é descontado, como as demais duplicatas do sistema — evita contar 2× com a oficial). Clique em ↩ considerar para reincluí-la no NAV/GAP (what-if). NÃO é enviada ao sistema pela rota /provisions (a oficial já existe → não duplica no Beehus).">coberta por provisão oficial · ignorada</span>` : `<span class="rp-expected-prov-pill">a gerar · ajuste</span>${p.duplicatesOfficial ? ` <span class="rp-flag" style="background:#fef3c7;color:#92400e" title="Já existe uma provisão IDÊNTICA no sistema (mesmo valor a 2 casas e mesmo tipo). Gerada, mas nasce pré-marcada como IGNORAR no NAV/GAP p/ não contar a provisão em dobro. Clique em ↩ considerar p/ reincluir.">duplica no sistema · ignorada</span>` : ""}`}${Math.abs(nz(p.intradayContribution)) >= 0.005 ? ` <span class="rp-flag" style="background:#ecfdf5;color:#065f46" title="P&L intraday do trade adotado = Δqty×(PU−execPrice). Somado ao returnContribution (intradayContribution) p/ parear o mesmo valor que a provisão soma ao NAV → adoção NAV-neutra no GAP (amDiff ≈ 0).">intraday ${fmtMoney(p.intradayContribution)} → contribuição</span>` : ""}${_confBadge(p.confidence, p.because)}</td>`)));
  // A liquidação consolidada stockETF/B3 NÃO tem mais bloco-resumo aqui — a correção
  // sugerida virou uma transação "a gerar" no bloco de Transações (_mTxnBlock), com botão
  // de aprovar; o texto explicativo está no tooltip dessa linha.
  const liqRed = liq.filter(p => (p.matchStatus || "red") === "red").length;
  const liqLbl = `${liq.length} liquidando na data` + (liqRed ? ` <span style="color:#991b1b">(${liqRed} sem caixa)</span>` : "");
  const chips = `<span class="text-[11px] text-gray-500">${offp.length} no sistema · ${liqLbl} · ${provs.length} liq. futura · ${rp.length} a gerar<span id="det-prov-whatif"></span></span>`;
  const out = _mDetails("Provisões", chips, _tbl(head, body, "Sem provisões."));
  return out;
}

/* ── Badges "o que foi feito" por transação da janela — cada chip é um bucket do
      cálculo que a transação alimentou (vem de t.effects, montado no motor por
      _txn_effects, espelhando os conjuntos de tipo). Cor por code; tooltip = regra. */
const _EFF_COLORS = {
  b3:        ["#ede9fe", "#5b21b6"],   // liquidação B3 (violeta)
  qty:       ["#dbeafe", "#1e40af"],   // quantidade (azul)
  event:     ["#dcfce7", "#166534"],   // cupom/amort. (verde)
  flow:      ["#e0e7ff", "#3730a3"],   // fluxo de capital (índigo)
  contrib:   ["#fef3c7", "#92400e"],   // contribuição/P&L (âmbar)
  principal: ["#ccfbf1", "#115e59"],   // resgate de principal (teal)
  cash:      ["#e2e8f0", "#334155"],   // caixa (cinza-ardósia — o menos distintivo)
};
function _effChips(effects) {
  if (!Array.isArray(effects) || !effects.length) return "";
  return effects.map(e => {
    const c = _EFF_COLORS[e.code] || ["#e5e7eb", "#374151"];
    return `<span class="rp-flag" style="background:${c[0]};color:${c[1]}" title="${esc(e.title || "")}">${esc(e.label)}</span>`;
  }).join("");
}

/* ── Bloco Transações — transações da janela + transações/IRRF A GERAR,
      tudo no mesmo bloco; as geradas pintadas para alertar ──────────────── */
function _mTxnBlock(r) {
  const txns = r.transactions || [], rtx = r.reconTransactions || [], irrf = r.irrf || [];
  const irrfMissing = irrf.filter(e => !e.covered);
  const head = `<th>Liq.</th><th>Op.</th><th class="rp-tbl-left">Tipo</th><th class="rp-tbl-left">Ativo</th><th class="rp-tbl-left">Descrição</th><th style="text-align:right">Qtd</th><th style="text-align:right">Saldo</th><th class="rp-tbl-left">Origem</th>`;
  const body = [];
  txns.forEach(t => {
    body.push(`<tr>
    <td class="text-[10px]">${esc(t.liquidationDate)}</td><td class="text-[10px]">${esc(t.operationDate)}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(t.type)}</td>
    <td class="rp-tbl-left">${esc(t.securityName || t.securityId || "—")}</td>
    <td class="rp-tbl-left" title="${esc(t.description || "")}" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.description ? esc(t.description) : '<span class="text-gray-300">—</span>'}</td>
    <td>${t.quantity == null ? _DASH : fmtNum(t.quantity, 4)}</td>
    <td class="${nz(t.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(t.balance)}</td>
    <td class="rp-tbl-left"><span class="rp-flag" style="background:#f1f5f9;color:#64748b" title="Transação real já existente na janela origem→alvo (não é gerada pela conciliação)">janela</span>${_effChips(t.effects)}</td></tr>`);
  });
  rtx.forEach(x => body.push(`<tr class="rp-row-qty-mismatch">
    <td class="text-[10px]">${esc(x.liquidationDate)}</td><td class="text-[10px]">${esc(x.operationDate)}</td>
    <td class="rp-tbl-left text-[10px] text-gray-500">${esc(x.beehusTransactionType || "buySell")}</td>
    <td class="rp-tbl-left">${esc(x.securityName || x.securityId || "—")}</td>
    <td class="rp-tbl-left">${esc(x.description || "—")}</td>
    <td>${fmtNum(x.quantity, 4)}</td>
    <td class="${nz(x.balance) < 0 ? "rp-diff-neg" : "rp-diff-pos"}">${fmtMoney(x.balance)}</td>
    <td class="rp-tbl-left"><span class="rp-expected-prov-pill">a gerar · ${x.matured ? "resgate venc." : "ajuste"}</span>${_confBadge(x.confidence, x.because)}</td></tr>`));
  irrf.forEach(e => {
    const gen = !e.covered;
    body.push(`<tr class="${gen ? "rp-row-qty-mismatch" : ""}">
      <td class="text-[10px]">${esc(e.liquidationDate)}</td><td class="text-[10px]">${esc(e.operationDate)}</td>
      <td class="rp-tbl-left text-[10px] text-gray-500">taxes (IRRF)</td>
      <td class="rp-tbl-left">${esc(e.securityName || e.securityId || "—")}</td>
      <td class="rp-tbl-left">${esc(e.description || "IRRF resgate de fundo")}</td>
      <td class="text-gray-300">—</td>
      <td class="${gen ? "rp-diff-neg" : ""}">${fmtMoney(e.irrf)}</td>
      <td class="rp-tbl-left" title="${esc(e.because || "")}">${gen ? `<span class="rp-expected-prov-pill">a gerar · IRRF</span>` : `<span class="rp-flag rp-flag-buysell" style="background:#dcfce7;color:#166534">já lançado</span>`}${e.multiEvent ? ` <span class="rp-flag" style="background:#fee2e2;color:#991b1b" title="Múltiplos resgates na janela (datas/PUs diferentes) — IRRF agregado pouco confiável; revisar antes de criar">⚠ revisar</span>` : ""}</td></tr>`);
  });
  // (stockETF/B3) correção SUGERIDA = securityContributionAdjustment no ativo "Liquidação B3"
  // (cash-neutro; já entra no returnContribution simulado). Texto explicativo FIXO no tooltip
  // (sem números). Aprovação inline (botão na própria linha → rota /gains-expenses).
  const se = r.stockEtfLiquidation;
  const b3Sug = (se && Math.abs(nz(se.residual)) >= 0.01) ? 1 : 0;
  if (b3Sug) {
    const toCreate = -nz(se.residual);
    const TIP = "A B3 consolida a liquidação dos stockETF numa única transação; a diferença entre as "
      + "provisões que liquidam e o líquido recebido é o custo de execução. É lançado como ajuste de "
      + "contribuição (securityContributionAdjustment) no ativo \"Liquidação B3\": não move o caixa, "
      + "entra no returnContribution. Alternativa manual: voltar à data inicial das provisões, lançar o "
      + "executionPrice correto e recalcular o NAV.";
    body.push(`<tr class="rp-row-qty-mismatch">
      <td class="text-[10px]">${esc(r.targetDate)}</td><td class="text-[10px]">${esc(r.targetDate)}</td>
      <td class="rp-tbl-left text-[10px] text-gray-500">securityContributionAdjustment</td>
      <td class="rp-tbl-left">Liquidação B3</td>
      <td class="rp-tbl-left" title="${esc(TIP)}">Ajuste de contribuição — custo de execução stockETF <span style="cursor:help;color:#9ca3af">ⓘ</span></td>
      <td class="text-gray-300">—</td>
      <td class="rp-diff-neg">${fmtMoney(toCreate)}</td>
      <td class="rp-tbl-left"><span class="rp-expected-prov-pill" title="${esc(TIP)}">a gerar · stockETF</span> <button type="button" onclick="createGainsExpenses('${esc(r.walletId)}')" class="rp-btn rp-btn-success" style="padding:0.15rem 0.5rem" title="Aprovar e lançar o ajuste de contribuição no Beehus">Aprovar</button></td></tr>`);
  }
  const genN = rtx.length + irrfMissing.length + b3Sug;
  const chips = `<span class="text-[11px] text-gray-500">${txns.length} na janela · ${genN} a gerar</span>`;
  return _mDetails("Transações", chips, _tbl(head, body, "Sem transações na janela."));
}

/* ── Bloco Preços de execução — lista TODOS os preços de execução (capturados do
      sistema) dos ativos com record/trade; os que apenas repetiam o PU (==PU) ou
      estavam ausentes são recalculados e pintados como "a corrigir". O bloco
      aparece SEMPRE (mesmo sem nada a corrigir). ─────────────────────────────── */
function _mExecBlock(r) {
  const items = r.executionPrices || [];
  const head = `<th class="rp-tbl-left">Ativo</th><th>Data</th><th style="text-align:right">execPrice atual</th><th style="text-align:right">PU</th><th style="text-align:right">execPrice calculado</th><th style="text-align:right" title="Impacto na contribuição: intraday = (qtd − qtd anterior) × (PU − execPrice). É quanto este preço de execução acrescenta ao returnContribution (num placeholder ≈PU o intraday era ~0; já refletido no GAP do preview).">Δ Contrib. (intraday)</th><th class="rp-tbl-left">Situação</th>`;
  const body = items.map(f => {
    const fix = f.isFix;
    const actLbl = f.action === "update" ? "atualizar" : "criar";
    const sit = f.status === "placeholder" ? `<span class="rp-expected-prov-pill">== PU → ${actLbl}</span>`
      : f.status === "ausente" ? `<span class="rp-expected-prov-pill">ausente → ${actLbl}</span>`
      : `<span class="rp-flag rp-flag-buysell" style="background:#dcfce7;color:#166534">ok</span>`;
    const ic = nz(f.intradayContribution);
    const icCell = Math.abs(ic) < 0.005 ? '<span class="rp-diff-zero">—</span>'
      : `<span class="${ic >= 0 ? "rp-diff-pos" : "rp-diff-neg"}">${fmtMoney(ic)}</span>`;
    return `<tr class="${fix ? "rp-row-qty-mismatch" : ""}">
      <td class="rp-tbl-left"><span class="inline-flex items-center gap-1">${esc(f.securityName || f.securityId)}${_copyBtn(f.securityId)}</span></td>
      <td class="text-[10px]">${esc(f.positionDate)}</td>
      <td>${f.capturedPrice == null ? '<span class="text-gray-300">ausente</span>' : fmtNum(f.capturedPrice, 6)}</td>
      <td>${fmtNum(f.pu, 6)}</td>
      <td>${f.calculatedPrice == null ? '<span class="text-gray-300">—</span>' : `<span class="${fix ? "rp-diff-neg" : "text-gray-400"}">${fmtNum(f.calculatedPrice, 6)}</span>`}</td>
      <td>${icCell}</td>
      <td class="rp-tbl-left">${sit}</td></tr>`;
  });
  const nFix = items.filter(x => x.isFix).length;
  const band = nFix ? _alertBand(`A corrigir: ${nFix} preço(s) de execução`,
    `executionPrice == PU (apenas repetiu o PU) ou ausente → recalculado por -Σbalance/amountDifference. Enviar via "Enviar preços de execução" na grade.`) : "";
  const chips = `<span class="text-[11px] text-gray-500">${items.length} ativo(s) · ${nFix} a corrigir</span>`;
  return _mDetails("Preços de execução", chips, band + _tbl(head, body, "Nenhum preço de execução nesta carteira/janela."));
}

function openDetail(wid) {
  const r = _movResults[wid]; if (!r || r.state !== "done") return;
  // Ao TROCAR de carteira, zera o what-if de provisões ignoradas (é por carteira, não grava).
  // Re-render da mesma carteira (filtro/reprojeção) preserva o que estava ignorado.
  if (_detWid !== wid) _detIgnoredProvs = new Set();
  // Resultado do LOTE (`_lite`): recarrega a carteira COMPLETA uma vez (projeção individual,
  // sem cache → nav fresco com fluxos-alvo) e re-renderiza. Bulk fica barato; o detalhe é
  // autoritativo. Só acontece ao abrir o detalhe, não na grade.
  if (r._lite) {
    _detWid = wid;
    // Abre o detalhe IMEDIATAMENTE com um spinner — o resultado do lote é reprojetado
    // (chamada de rede de alguns segundos); sem isso o clique na 🔍 não daria retorno
    // visual nenhum até a projeção completa terminar.
    document.getElementById("det-wallet").textContent = r.walletName || wid;
    document.getElementById("det-sub").textContent = `${r.sourceDate} → ${r.targetDate} · carregando detalhamento…`;
    document.getElementById("det-body").innerHTML =
      `<div class="bg-white rounded-xl shadow" style="padding:40px; text-align:center">${_loaderHTML("Carregando detalhamento completo…")}</div>`;
    document.getElementById("mov-filters").classList.add("hidden");
    document.getElementById("table-section").classList.add("hidden");
    document.getElementById("mov-detail").classList.remove("hidden");
    window.scrollTo(0, 0);
    _detStatus("carregando detalhe completo...", "");
    _movimentarOne(wid, r.targetDate).then(() => {
      const rr = _movResults[wid] || {};
      if (rr.state === "done") { _detStatus(""); if (_detWid === wid) openDetail(wid); }
      else {
        _detStatus(rr.error || "falha ao carregar detalhe", "error");
        if (_detWid === wid) document.getElementById("det-body").innerHTML =
          `<div class="bg-white rounded-xl shadow text-red-600 text-sm" style="padding:24px">${esc(rr.error || "Falha ao carregar o detalhamento.")}</div>`;
      }
    });
    return;
  }
  if (_detWid !== wid) _detStatus("");   // limpa status ao trocar de carteira
  _detWid = wid;
  // Pré-marca como IGNORADAS (1×/carteira) as reconProvisions que DUPLICAM uma provisão
  // oficial já no sistema (`duplicatesOfficial`, casada por valor 2-casas + tipo no motor):
  // continuam GERADAS ("a gerar · ajuste"), mas nascem ignoradas no NAV/GAP p/ não dobrar a
  // provisão existente — exatamente como se o usuário clicasse ⊘ ignorar. Idem as
  // `coveredByOfficial` (divergência de qtd já coberta por provisão OFICIAL do mesmo ativo):
  // nascem ignoradas e informacionais (não estão no NAV → drop = 0). Só na 1ª render da
  // carteira (guard `_detDupIgnoreWid`): re-render/filtro preserva os toggles do usuário.
  if (_detDupIgnoreWid !== wid) {
    (r.reconProvisions || []).forEach(p => {
      if (p.duplicatesOfficial || p.coveredByOfficial) _detIgnoredProvs.add(_provKey("recon", p));
    });
    (r.provisions || []).forEach(p => {
      if (p.duplicatesOfficial) _detIgnoredProvs.add(_provKey("eng", p));   // subscrição em trânsito duplicada
    });
    _detDupIgnoreWid = wid;
  }
  const d = r.diff || {};
  document.getElementById("det-wallet").textContent = r.walletName || wid;
  // Caixa-âncora eliminado: a divergência de qtd vira PROVISÃO de ajuste (vencimento → transação),
  // independente do caixa. O resíduo de caixa fica só informativo.
  const cmTxt = "divergência de qtd → PROVISÃO de ajuste (vencimento → transação) · caixa não classifica";
  document.getElementById("det-sub").textContent = `${r.sourceDate} → ${r.targetDate} · projetada × unprocessed do alvo · ${cmTxt}`;
  const model = _mSecRowsModel(r);
  const total = model.length, shown = _detSecFilter ? model.filter(m => m.active).length : total;
  const secBar = `<div style="display:flex; align-items:center; gap:8px; padding:4px 12px; border-bottom:1px solid #f3f4f6; background:#fff">
    <span class="text-[10px] uppercase tracking-widest text-gray-400">Ativos</span>
    <span class="text-[11px] text-gray-500">${shown}/${total}${_detSecFilter ? " (com divergência/movimento)" : ""}</span>
    ${(r.diff && r.diff.counts && r.diff.counts.manualReview) ? `<span class="rp-flag" style="background:#fef3c7;color:#92400e" title="Achados (só no movimentado / só no alvo / divergência só de PU) que precisam de revisão manual — diagnóstico, sem escrita automática">⚠ ${r.diff.counts.manualReview} p/ revisão manual</span>` : ""}
    <span class="ml-auto flex items-center gap-2">${_mExtraColsBtn()}${_mSecFilterBtn()}</span>
  </div>`;
  // Renderiza o bloco de PROVISÕES ANTES do NAV: `_mProvBlock` popula `_detProvByKey`
  // (chave → {navContribution, inNav, bucket}), de que o what-if do NAV da projetada
  // (`_whatIfProj`, dentro de `_mNavBlock`) depende p/ recalcular o NAV com as provisões
  // ignoradas — inclusive as PRÉ-ignoradas (`duplicatesOfficial`) já na 1ª render. Se o NAV
  // for montado antes, o map está vazio → `drop=0` → o NAV não é recalculado (só mostra o rótulo).
  const provHtml = _mProvBlock(r);
  let h = `<div class="bg-white rounded-xl shadow overflow-hidden rp-preview-pane">
    ${_mNavBlock(r)}
    ${secBar}
    <div class="rp-preview-scroll" style="padding:8px 12px; max-height:560px; overflow:auto">${_mSecTable(r, model)}</div>
  </div>`;
  h += provHtml;
  h += _mTxnBlock(r);
  h += _mExecBlock(r);
  document.getElementById("det-body").innerHTML = h;
  _updateProvWhatIfSummary();   // reflete provisões ignoradas (what-if) após re-render
  document.getElementById("mov-filters").classList.add("hidden");
  document.getElementById("table-section").classList.add("hidden");
  document.getElementById("mov-detail").classList.remove("hidden");
  window.scrollTo(0, 0);
}
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  const prun = document.getElementById("period-run-modal");
  if (prun && !prun.classList.contains("hidden")) { closePeriodRun(); return; }
  const pdm = document.getElementById("period-dates-modal");
  if (pdm && !pdm.classList.contains("hidden")) { closePeriodDates(); return; }
  const ich = document.getElementById("impl-choice-modal");
  if (ich && !ich.classList.contains("hidden")) { closeImplementChoice(); return; }
  const cm = document.getElementById("calcmemo-modal");
  if (cm && !cm.classList.contains("hidden")) { closeCalcMemo(); return; }
  const imp = document.getElementById("implement-modal");
  if (imp && !imp.classList.contains("hidden")) { closeImplementModal(); return; }
  const modal = document.getElementById("report-modal");
  if (modal && !modal.classList.contains("hidden")) { closeReportModal(); return; }
  if (!document.getElementById("mov-detail").classList.contains("hidden")) backToGrid();
});

/* ══════════ Relatório de erros de conciliação (texto puro) ══════════
   Montado 100% no front a partir do resultado da projeção em memória. Separa em DOIS
   tipos pelo discriminador `diff.hasTarget` (existe posição-alvo?):
     • Tipo A (Conciliação, hasTarget=true): reconciliação + referência + insumo.
     • Tipo B (Forward, hasTarget=false): SÓ insumo (defeitos de origem/transação que
       independem do alvo); lacunas de dado-de-referência (PU repetido etc.) são esperadas
       e omitidas. Ver docs/REPORT_ERROS_CONCILIACAO.md. */
const _KNOWN_TXN_TYPES = new Set(["buySell", "withdrawalDeposit", "taxes", "bzFundTaxes",
  "coupon", "amortization", "maturity", "dividend", "interestOnEquity", "cashDividend",
  "securityTransfer", "gainsExpenses", "rebate", "securityContributionAdjustment",
  "contributionAdjustment", "split", "inplit"]);
// Tipos em que o securityId é OBRIGATÓRIO — só estes viram "Transação órfã" quando vêm
// sem securityId. Tipos de CARTEIRA (gainsExpenses, rebate, withdrawalDeposit, taxes,
// contributionAdjustment, split, inplit…) legitimamente NÃO têm ativo → não são órfãs.
const _SID_REQUIRED_TXN_TYPES = new Set(["dividend", "cashDividend", "interestOnEquity",
  "buySell", "coupon", "amortization", "securityContributionAdjustment", "maturity",
  "futuresSettlement", "bzFundTax", "bzFundTaxes", "dividendOnboarding", "securityTransfer"]);

// Devolve [{nature:'insumo'|'reconciliacao'|'referencia', tag, text}] de uma carteira.
