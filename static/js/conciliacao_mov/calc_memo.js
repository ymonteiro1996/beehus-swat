/* Conciliação (Mov.) — memória de cálculo (estilo + render).
   Escopo global compartilhado; ordem importa. */
function openCalcMemo() {
  const r = _movResults[_detWid];
  if (!_detWid || !r || r.state !== "done") { _detStatus("Projeção ainda não concluída para esta carteira.", "error"); return; }
  const _wiN = (_detIgnoredProvs && _detIgnoredProvs.size) || 0;
  document.getElementById("calcmemo-sub").textContent =
    `${r.walletName || _detWid} · ${_date} → ${_targetDate()}` + (_wiN ? ` · what-if: ${_wiN} provisão(ões) ignorada(s)` : "");
  document.getElementById("calcmemo-body").innerHTML = _renderCalcMemo(r);
  const m = document.getElementById("calcmemo-modal");
  m.style.display = "flex"; m.classList.remove("hidden");
}
function closeCalcMemo() {
  const m = document.getElementById("calcmemo-modal");
  m.style.display = "none"; m.classList.add("hidden");
}
/* ══════ Memória de cálculo — render compacto (5 blocos = estágios do motor) ══════
   Espelha os estágios PUROS de conciliacao_mov.py: B1 _project_rows · B2 _adopt_target_qty
   · B3 caixa · B4 _diagnose · B5 _compute_nav_and_gaps. Cada bloco ABRE com seu RESULTADO
   no cabeçalho; linhas compactas (rótulo · fórmula · valor) com a REGRA no tooltip do
   rótulo pontilhado. "⚠ não calculado" = falta insumo (ex.: navPackage da origem) ·
   "sem ocorrência" = nada a calcular neste caso. Só lê campos do payload (read-only). */
const _CM_STYLE = `<style>
.cm{font-size:12px;color:#1f2937}
.cm-sec{background:#fff;border:1px solid #e5e7eb;border-radius:10px;margin-bottom:10px;overflow:hidden}
.cm-sec-h{display:flex;align-items:center;gap:9px;padding:9px 13px;border-bottom:1px solid #f1f5f9;background:#fafbfc}
.cm-sec-t{font-weight:600;font-size:13px;color:#111827;white-space:nowrap}
.cm-fill{flex:1}
.cm-res{display:flex;align-items:center;gap:8px;font-size:12px;color:#6b7280;flex-wrap:wrap;justify-content:flex-end}
.cm-res b{color:#111827}
.cm-sec-b{padding:11px 13px}
.cm-neg{color:#b91c1c}
.cm-val{font-size:13px;font-weight:700;color:#111827}
/* ── resultado (GAP) ── */
.cm-strip-l{font-size:10px;font-weight:600;color:#6b7280;margin-bottom:5px}
/* ── tabelas (substituem as tiras de pills) ── */
.cm-tbl{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px}
.cm-tbl th{font-size:9px;text-transform:uppercase;letter-spacing:.04em;color:#9ca3af;font-weight:600;text-align:right;padding:5px 9px;border-bottom:1px solid #e5e7eb;white-space:nowrap}
.cm-tbl th.cm-tl,.cm-tbl td.cm-tl{text-align:left}
.cm-tbl td{padding:6px 9px;border-bottom:1px solid #f5f7fa;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;vertical-align:top}
.cm-tbl tbody:last-child tr:last-child td,.cm-tbl tr:last-child td{border-bottom:0}
.cm-tbl-tot td{font-weight:700;background:#f9fafb;border-top:2px solid #e5e7eb;font-size:13px}
.cm-tbl th.cm-col-sep,.cm-tbl td.cm-col-sep{border-left:2px solid #e5e7eb}
.cm-tbl th.cm-col-em,.cm-tbl td.cm-col-em{font-weight:700;background:#f9fafb}
.cm-tbl tr.cm-row-sec td{background:#eef0f3;color:#8894a6}
.cm-tbl tr.cm-row-sec td.cm-tl{color:#6b7686}
.cm-tbl-op{display:inline-block;width:11px;color:#c7ccd6;font-weight:700}
.cm-tbl-sub{font-size:10px;color:#9ca3af;font-weight:400;margin-top:1px;white-space:normal}
.cm-tbl th.cm-alvo,.cm-tbl td.cm-alvo{background:#f8fafc;color:#475569}
.cm-tbl td.cm-alvo b{color:#1e293b}
.cm-tbl-dash{color:#d1d5db}
.cm-tgt{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px;padding:7px 10px;background:#f1f5f9;border:1px solid #e2e8f0;border-left:3px solid #64748b;border-radius:8px}
.cm-tgt-b{font-size:9px;font-weight:700;letter-spacing:.06em;color:#fff;background:#64748b;padding:2px 8px;border-radius:99px;white-space:nowrap;flex:none}
.cm-tgt-c{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;color:#475569}
.cm-tgt-c b{color:#1e293b}
/* ── badges + chips ── */
.cm-b{font-size:10px;font-weight:600;padding:2px 7px;border-radius:99px;white-space:nowrap;display:inline-block}
.cm-na{background:#fef3c7;color:#92400e}.cm-none{background:#f3f4f6;color:#9ca3af}
.cm-ok{background:#dcfce7;color:#166534}.cm-warn{background:#fef9c3;color:#854d0e}.cm-bad{background:#fee2e2;color:#991b1b}
.cm-chip{font-size:10px;background:#f1f5f9;color:#334155;padding:2px 7px;border-radius:99px;display:inline-block}
/* ── diagnósticos ── */
.cm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px}
.cm-card{border:1px solid #f1f5f9;border-radius:8px;padding:7px 10px;background:#fcfcfd}
.cm-card-t{font-weight:600;color:#374151;font-size:11px;cursor:help;border-bottom:1px dotted #d1d5db;display:inline-block;margin-bottom:4px}
.cm-card-b{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
/* ── fórmulas ── */
.cm-fg{margin-bottom:11px}
.cm-fg:last-child{margin-bottom:0}
.cm-fg-t{font-size:10px;font-weight:700;color:#6366f1;text-transform:uppercase;letter-spacing:.05em;margin:0 0 5px}
.cm-fr{display:flex;gap:11px;align-items:baseline;padding:4px 0;border-bottom:1px solid #f8fafc}
.cm-fr:last-child{border-bottom:0}
.cm-f{font-family:ui-monospace,Consolas,monospace;font-size:10px;background:#eef2ff;color:#4338ca;padding:2px 7px;border-radius:5px;white-space:nowrap;flex:none}
.cm-fr-d{font-size:10px;color:#6b7280;line-height:1.4}
.cm details{margin:6px 0 2px}.cm summary{cursor:pointer;font-size:11px;color:#6366f1}
.cm table.rp-tbl{margin-top:5px}
.cm-note{font-size:10px;color:#9ca3af}
</style>`;
function _renderCalcMemo(r) {
  // ── Cenário WHAT-IF (#5): se há provisões ignoradas, recalcula NAV/cota/retornos/GAP e
  // os GAPs por etapa, e SUBSTITUI os campos correspondentes em `r` — assim TODA a memória
  // (composição, resultado, etapas) reflete o cenário recalculado. Nada é gravado.
  const _wi = _whatIfProj(r);
  if (_wi) {
    const _navMov0 = _navMovFromGap(r, (r.gapStages || {}).mov);
    const _navMovWi = _navMov0 == null ? null : _navMov0 - _wi.dropEngine - _wi.dropOfficial;
    const _gapMovWi = _gapCashFor(r, _navMovWi);
    const _gapAmdiffWi = (_wi.gapCash != null && _gapMovWi != null) ? Math.round((_wi.gapCash - _gapMovWi) * 100) / 100 : null;
    r = Object.assign({}, r, {
      simNav: _wi.simNav, simNavPerShare: _wi.simNps, simReturnNavPerShare: _wi.retNps,
      simGapPct: _wi.gapPct, simGapCash: _wi.gapCash,
      provisionsTotal: Math.round((nz(r.provisionsTotal) - _wi.dropEngine) * 100) / 100,
      officialProvisionsInNav: Math.round((nz(r.officialProvisionsInNav) - _wi.dropOfficial) * 100) / 100,
      gapStages: Object.assign({}, r.gapStages || {}, { mov: _gapMovWi, amDiff: _gapAmdiffWi }),
    });
  }
  const rows = r.rows || [], d = r.diff || {}, cash = r.cash || {}, gs = r.gapStages || {};
  const sum = (a, f) => a.reduce((s, x) => s + nz(f(x)), 0);
  const sumBal = sum(rows, x => x.balance);
  const sumContrib = sum(rows, x => x.contribution);
  const NAV_WHY = "NAV/cotas da data-origem indisponíveis (navPackage oficial não encontrado) → retorno/cota/GAP em R$ não são calculáveis.";
  // ── renderers básicos ──
  const naB = why => `<span class="cm-b cm-na" title="${esc(why || 'Não foi possível calcular — falta insumo.')}">⚠ não calculado</span>`;
  const noneB = why => `<span class="cm-b cm-none" title="${esc(why || '')}">sem ocorrência</span>`;
  const money = (v, why) => (v == null) ? naB(why) : `<b class="cm-val ${nz(v) < 0 ? "cm-neg" : ""}">${fmtMoney(v)}</b>`;
  const numOrNa = (v, dec, why) => (v == null) ? naB(why) : `<b class="cm-val">${fmtNum(v, dec == null ? 2 : dec)}</b>`;
  const sec = (title, resHTML, body) => `<div class="cm-sec">
    <div class="cm-sec-h"><span class="cm-sec-t">${esc(title)}</span><span class="cm-fill"></span><span class="cm-res">${resHTML || ""}</span></div>
    <div class="cm-sec-b">${body}</div></div>`;

  // ── insumos derivados (reusados nas tabelas + detalhes) ──
  // Caixa-âncora ELIMINADO: sem verdito "bate/não bate". O resíduo de caixa (projetado −
  // oficial) fica só como INFORMAÇÃO (não classifica ajustes). Divergência de qtd → provisão.
  const cashRes = d.cashResidual;
  const csBadge = cashRes == null ? noneB("sem caixa oficial (forward)")
    : `<span class="cm-chip" title="Resíduo de caixa = projetado − oficial do alvo (informativo — não classifica ajustes)">resíduo ${fmtMoney(cashRes)}</span>`;
  const moved = rows.filter(x => Math.abs(nz(x.quantity) - nz(x.formerQuantity)) > 1e-6);
  const ptHist = {}; rows.forEach(x => { const k = (x.pricingType || "—").split(" ")[0] || "—"; ptHist[k] = (ptHist[k] || 0) + 1; });
  const ptChips = Object.entries(ptHist).map(([k, n]) => `<span class="cm-chip">${esc(k)}·${n}</span>`).join(" ");
  const rp = r.reconProvisions || [], rt = r.reconTransactions || [];
  const offp = r.officialProvisions || [];
  const diverged = d.diverged || [];
  const oc = d.officialCash;
  const irrfMissing = (r.irrf || []).filter(e => !e.covered);
  const se = r.stockEtfLiquidation;
  const liq = r.liquidatingProvisions || [];
  const liqBy = { green: 0, yellow: 0, red: 0 }; liq.forEach(p => { liqBy[p.matchStatus || "red"] = (liqBy[p.matchStatus || "red"] || 0) + 1; });
  const epFix = r.executionPriceFixes || [];
  const adopted = rows.filter(x => x.adoptedFromTarget);
  const manual = (d.counts || {}).manualReview || 0;
  const reconInNav = (r.simNav != null)
    ? Math.round((nz(r.simNav) - sumBal - nz(cash.new) - nz(r.provisionsTotal) - nz(r.officialProvisionsInNav)) * 100) / 100 : null;

  // ── POSIÇÃO ALVO (real): números do alvo por seção, quando `diff.hasTarget`. Fonte:
  // diff.real (navPackage OFICIAL do alvo) + diff.officialCash + diff.counts. Os campos de
  // diff.real ficam null quando o alvo EXISTE mas ainda não foi processado (forward) → "n/d".
  const real = d.real || {}, hasTgt = !!d.hasTarget, counts = d.counts || {};
  const ndB = why => `<span class="cm-b cm-none" title="${esc(why || "Alvo não processado — sem valor oficial na data.")}">n/d</span>`;
  const tMoney = v => v == null ? ndB() : `<b class="${nz(v) < 0 ? "cm-neg" : ""}">${fmtMoney(v)}</b>`;
  const tPct = v => v == null ? ndB() : `<b class="${nz(v) < 0 ? "cm-neg" : ""}">${fmtPct(v)}</b>`;
  const tNum = (v, dec) => v == null ? ndB() : `<b>${fmtNum(v, dec == null ? 2 : dec)}</b>`;
  const tchip = (label, valHTML) => `<span class="cm-chip">${esc(label)} ${valHTML}</span>`;
  const tgtBar = inner => `<div class="cm-tgt"><span class="cm-tgt-b">POSIÇÃO ALVO</span><div class="cm-tgt-c">${inner}</div></div>`;
  // GAP do PRÓPRIO alvo (resíduo interno do alvo), p/ comparar com o GAP simulado
  const tgtGapPct = (real.returnNavPerShare != null && real.returnContribution != null)
    ? real.returnNavPerShare - real.returnContribution : null;
  const tgtGapCash = (tgtGapPct != null && r.formerNav != null) ? tgtGapPct * nz(r.formerNav) : null;

  // ═════ Composição do NAV — TABELA TRANSPOSTA (componentes nas COLUNAS) ═════
  const dCellM = (proj, tgt) => (proj == null || tgt == null) ? "" : `<span class="${(proj - tgt) < 0 ? "cm-neg" : ""}">${fmtMoney(proj - tgt)}</span>`;
  const dCellP = (sim, tgt) => (sim == null || tgt == null) ? "" : `<span class="${(sim - tgt) < 0 ? "cm-neg" : ""}">${fmtPct(sim - tgt)}</span>`;
  const dash = `<span class="cm-tbl-dash">—</span>`;
  const pctS = v => v == null ? naB(NAV_WHY) : `<b class="cm-val ${nz(v) < 0 ? "cm-neg" : ""}">${fmtPct(v)}</b>`;
  // Renderiza uma tabela TRANSPOSTA (jul/2026, a pedido): os ITENS viram COLUNAS (rótulo no
  // header, texto de apoio no tooltip) e as DIMENSÕES DE VALOR viram LINHAS. cols = [{label
  // (HTML), title, tot}]; valRows = [{label, cells:[HTML…], sub?, alvo?, tot?}]. Colunas `tot`
  // (NAV/GAP) ganham realce; a linha `alvo` recebe o fundo cm-alvo. Rola em X se estreito.
  // classe da coluna: `sep`=separador de grupo (border-left); `em`=realce (negrito+fundo);
  // `tot`=ambos (total). Retrocompatível com as tabelas que passam só `tot`.
  const _ccls = c => !c ? "" : ((c.tot || c.sep ? "cm-col-sep " : "") + (c.tot || c.em ? "cm-col-em" : "")).trim();
  const cmTrans = (corner, cols, valRows) => {
    const th = `<th class="cm-tl">${esc(corner || "")}</th>` + cols.map(c =>
      `<th class="${_ccls(c)}"${c.title ? ` title="${esc(c.title)}"` : ""}>${esc(c.label)}</th>`).join("");
    const tb = valRows.map(vr => {
      const ex = vr.alvo ? " cm-alvo" : "";
      return `<tr class="${vr.tot ? "cm-tbl-tot" : vr.sec ? "cm-row-sec" : ""}"><td class="cm-tl${ex}">${esc(vr.label)}${vr.sub ? `<div class="cm-tbl-sub">${vr.sub}</div>` : ""}</td>`
        + vr.cells.map((cell, i) => `<td class="${(_ccls(cols[i]) + ex).trim()}">${(cell == null || cell === "") ? dash : cell}</td>`).join("")
        + `</tr>`;
    }).join("");
    return `<div style="overflow-x:auto"><table class="cm-tbl"><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`;
  };
  const _ptTxt = Object.entries(ptHist).map(([k, n]) => `${k}·${n}`).join(" ");
  const _csTxt = cashRes == null ? "sem caixa oficial (forward)" : `resíduo de caixa ${fmtMoney(cashRes)} (informativo)`;
  const compCols = [
    { label: "Σ saldos", title: `${rows.length} ativo(s)${moved.length ? ` · ${moved.length} movimentado(s)` : ""} · contribuição ${fmtMoney(sumContrib)}${_ptTxt ? ` · ${_ptTxt}` : ""}` },
    { label: "+ Caixa", title: `origem ${fmtMoney(cash.former)} · Σ transações ${fmtMoney(cash.delta)}${Math.abs(nz(cash.maturedDelta)) >= 0.005 ? ` · vencimentos ${fmtMoney(cash.maturedDelta)}` : ""} · ${_csTxt}` },
    { label: "+ Provisões", title: "dividendo/JCP + compra de liq. futura (as de recon entram na coluna Recon)" },
    { label: "+ Recon", title: `offset da adoção da qtd-alvo (−Δqty×execPrice): ${rp.length} provisão(ões) recon + coberto-por-provisão · o P&L intraday resultante entra na contribuição → neutro NO GAP · as ${rt.length} transação(ões) recon ficam FORA do NAV` },
    { label: "+ Oficiais", title: offp.length ? `${offp.length} provisão(ões) oficial(is)` : "sem oficiais ativas no alvo" },
    { label: "= NAV simulado", title: "soma das colunas anteriores", tot: true },
  ];
  const compValRows = [{ label: "Projetado", cells: [money(sumBal), money(cash.new), money(r.provisionsTotal), money(reconInNav, NAV_WHY), money(r.officialProvisionsInNav), money(r.simNav)] }];
  if (hasTgt) {
    const tgtBal = real.securitiesBalance;   // Σ saldos da posição-ALVO (vem do motor: diff.real)
    compValRows.push({ label: "Alvo (real)", alvo: true, cells: [tMoney(tgtBal), tMoney(oc), dash, dash, dash, tMoney(real.nav)] });
    compValRows.push({ label: "Δ (proj − alvo)", cells: [dCellM(sumBal, tgtBal), dCellM(cash.new, oc), "", "", "", dCellM(r.simNav, real.nav)] });
  }
  const compTbl = cmTrans("Componente", compCols, compValRows);
  const secComp = sec("Composição do NAV simulado", "", compTbl);

  // ═════ RESULTADO — GAP e retorno (TABELAS) ═════
  // O GAP nasce da diferença entre dois retornos que DEVERIAM coincidir (pela cota × pela
  // contribuição) e se decompõe por etapa: mov (projeção) + amDiff (adoção) = GAP final.
  // total_contribution = Σcontrib(posição) + walletContribution (par contábil do motor).
  // P&L intraday das adoções da qtd-alvo (§8.6): o motor o soma ao total_contribution mas
  // ele não fica em nenhuma linha de ativo → entra na coluna "Σ contribuições" (posições)
  // p/ o retorno por contribuição fechar com o do motor. Normalmente 0 (execPrice = PU).
  const _adopt = nz(r.adoptionIntradayContribution);
  const sumContribFinal = sumContrib + _adopt;                        // Σ contribuições (linha Simulado)
  const totalContrib = sumContribFinal + nz(r.walletContribution);    // == simReturnContribution × formerNav
  // etapa "mov" = PRÉ-adoção: contribuição SEM o intraday das adoções.
  const totalContribMov = sumContrib + nz(r.walletContribution);
  const movRetContrib = (r.formerNav != null && nz(r.formerNav)) ? totalContribMov / nz(r.formerNav) : r.simReturnContribution;
  // (1) UMA tabela transposta com as colunas granulares (a pedido): grupo COTA (navPerShare ·
  // former navPerShare · retorno navPerShare) | grupo CONTRIBUIÇÃO (Σ contribuições · P&L de
  // carteira · NAV anterior · retorno contribuição) | GAP (% · $). Linhas = Simulado (+ Alvo real).
  const _wcLabel = {
    managementFee: "Taxa de consultoria/gestão", otherFee: "Outras taxas", brokerageFee: "Corretagem",
    gainsExpenses: "Ganhos/despesas", rebate: "Rebate",
    contributionAdjustment: "Ajuste de contribuição", securityContributionAdjustment: "Ajuste de contribuição (ativo)",
    stockEtfLiquidation: "Ajuste liquidação stockETF (sugerido)",
  };
  const wcb = r.walletContributionBreakdown || [];
  const _nContrib = rows.filter(x => Math.abs(nz(x.contribution)) >= 0.005).length;
  const dNav = (r.simNav != null && r.formerNav != null) ? nz(r.simNav) - nz(r.formerNav) : null;
  const _wcTip = wcb.length ? wcb.map(w => `${_wcLabel[w.type] || w.type}: ${fmtMoney(w.balance)}`).join(" · ") : "sem taxas/ganhos/ajustes na janela";
  const _plCell = v => v == null ? naB(NAV_WHY) : `<b class="cm-val ${nz(v) < 0 ? "cm-neg" : ""}" title="${esc(_wcTip)}">${fmtMoney(v)}</b>`;
  const retCols = [
    { label: "navPerShare", title: "cota simulada na data-alvo" },
    { label: "former navPerShare", title: "cota da data-origem (base do retorno)" },
    { label: "retorno navPerShare", title: "navPerShare ÷ former navPerShare − 1", em: true },
    { label: "Σ contribuições", title: `Σ contribuição das posições (${_nContrib} ativo(s)) — variação diária fq·(PU−fPU) + intraday + eventos${Math.abs(_adopt) >= 0.005 ? ` + intraday das adoções da qtd-alvo ${fmtMoney(_adopt)}` : ""}`, sep: true },
    { label: "P&L de carteira", title: "taxas/rebate/ganhos/ajustes de carteira (passe o mouse na célula p/ o detalhe por tipo)" },
    { label: "NAV anterior", title: "NAV da data-origem (denominador do retorno por contribuição)" },
    { label: "retorno contribuição", title: "(Σ contribuições + P&L de carteira) ÷ NAV anterior", em: true },
    { label: "GAP %", title: "retorno navPerShare − retorno contribuição", tot: true },
    { label: "GAP $", title: "GAP % × NAV anterior", em: true },
  ];
  // Estágio "mov" DERIVADO de gs.mov (gap% = gap$ ÷ NAV anterior → retNPS = gap% + retContrib →
  // nps = (1+retNPS)·formerNPS) — sem novo dado do motor, funciona também no what-if. "amDiff" =
  // final − mov (deltas nas colunas que mudam; as de contribuição não mudam → "—").
  const _fn = nz(r.formerNav), _fnps = nz(r.formerNavPerShare);
  // "mov" derivado de gs.mov, mas com o retorno por contribuição PRÉ-adoção (movRetContrib) —
  // senão a linha Gap mov não fecha (col8 = col3 − col7) quando há intraday de adoção.
  const movGapPct = (r.formerNav != null && gs.mov != null) ? gs.mov / _fn : null;
  const movRetNps = (movGapPct != null && movRetContrib != null) ? movGapPct + nz(movRetContrib) : null;
  const movNps = (movRetNps != null && r.formerNavPerShare != null) ? (1 + movRetNps) * _fnps : null;
  const amNps = (r.simNavPerShare != null && movNps != null) ? nz(r.simNavPerShare) - movNps : null;
  const amRetNps = (r.simReturnNavPerShare != null && movRetNps != null) ? nz(r.simReturnNavPerShare) - movRetNps : null;
  const amGapPct = (r.simGapPct != null && movGapPct != null) ? nz(r.simGapPct) - movGapPct : null;
  // deltas de contribuição da etapa amDiff (Simulado − Gap mov) = o intraday da adoção.
  const amRetContrib = (nz(r.formerNav) && Math.abs(_adopt) >= 0.005) ? _adopt / _fn : null;
  const _pctD = v => v == null ? dash : `<b class="cm-val ${v < 0 ? "cm-neg" : ""}">${fmtPct(v)}</b>`;
  const _numD = (v, dec) => v == null ? dash : `<b class="cm-val ${v < 0 ? "cm-neg" : ""}">${fmtNum(v, dec)}</b>`;
  const _mD = v => (v == null || Math.abs(nz(v)) < 0.005) ? dash : `<b class="cm-val ${nz(v) < 0 ? "cm-neg" : ""}">${fmtMoney(v)}</b>`;
  // UMA tabela só: Alvo (real) → Simulado → Gap mov → GAP amDiff. As duas últimas são
  // informação SECUNDÁRIA (linhas `sec`, tom apagado). Sem "= GAP final" (== Simulado) nem
  // "Caixa (à parte)" (o Δ de caixa já aparece na Composição do NAV) nem "Δ (sim − alvo)".
  const resultRows = [];
  if (hasTgt) {
    resultRows.push({ label: "Alvo (real)", alvo: true, cells: [
      tNum(real.navPerShare, 8), numOrNa(r.formerNavPerShare, 8, NAV_WHY), tPct(real.returnNavPerShare),
      ndB("Alvo não expõe a composição da contribuição"), ndB("Alvo não expõe a composição da contribuição"),
      money(r.formerNav, NAV_WHY), tPct(real.returnContribution), tPct(tgtGapPct), tMoney(tgtGapCash)] });
  }
  resultRows.push({ label: "Simulado", cells: [
    numOrNa(r.simNavPerShare, 8, NAV_WHY), numOrNa(r.formerNavPerShare, 8, NAV_WHY), pctS(r.simReturnNavPerShare),
    money(sumContribFinal), _plCell(r.walletContribution), money(r.formerNav, NAV_WHY), pctS(r.simReturnContribution),
    pctS(r.simGapPct), money(r.simGapCash, NAV_WHY)] });
  resultRows.push({ label: "Gap mov", sec: true, sub: "resíduo da projeção (antes da adoção da qtd-alvo; contribuição sem o intraday das adoções)", cells: [
    numOrNa(movNps, 8, NAV_WHY), numOrNa(r.formerNavPerShare, 8, NAV_WHY), pctS(movRetNps),
    money(sumContrib), _plCell(r.walletContribution), money(r.formerNav, NAV_WHY), pctS(movRetContrib),
    pctS(movGapPct), money(gs.mov, NAV_WHY)] });
  resultRows.push({ label: "GAP amDiff", sec: true, sub: "GAP final − GAP mov: efeito isolado da adoção da qtd-alvo. As cotas sobem +Δqty×PU e o recon −Δqty×execPrice; o P&L intraday Δqty×(PU−execPrice) entra na Σ contribuições ⇒ retorno por cota e por contribuição sobem juntos ⇒ GAP ≈ 0", cells: [
    _numD(amNps, 8), dash, _pctD(amRetNps), _mD(_adopt), dash, dash, _pctD(amRetContrib), _pctD(amGapPct), money(gs.amDiff, NAV_WHY)] });
  const resultTbl = cmTrans("Cenário", retCols, resultRows);
  // Reconciliação: ΔNAV = total_contribution + fluxos de capital + GAP. Torna VISÍVEL o
  // que move o NAV mas fica FORA da contribuição (fluxos de capital) e o resíduo (GAP).
  const contribRecon = dNav == null ? "" :
    `<div class="cm-strip-l" style="margin-top:9px">Reconciliação com o NAV — <b>ΔNAV = contribuição + fluxos de capital + GAP</b></div>`
    + `<div class="cm-tbl-sub" style="margin-top:3px;line-height:1.7">`
    + `ΔNAV ${fmtMoney(dNav)} = total_contribution ${fmtMoney(totalContrib)} + fluxos de capital ${fmtMoney(nz(r.inflows))} + GAP ${r.simGapCash == null ? "n/c" : fmtMoney(r.simGapCash)}`
    + `</div>`;

  const secResult = sec("Resultado — GAP e retorno", csBadge, [
    `<div class="cm-strip-l">Retornos e composição da contribuição — Alvo × Simulado devem coincidir (a diferença é o GAP); "Gap mov"/"amDiff" são secundárias</div>`,
    resultTbl,
    contribRecon,
  ].join(""));

  // ═════ DIAGNÓSTICOS E AJUSTES (antigo "Outros") ═════
  const card = (title, tip, bodyHTML) => `<div class="cm-card"><span class="cm-card-t" title="${esc(tip)}">${esc(title)}</span><div class="cm-card-b">${bodyHTML}</div></div>`;
  const nOccur = [irrfMissing.length, se ? 1 : 0, liq.length, epFix.length, offp.length, adopted.length, manual].filter(n => n > 0).length;
  const secDiag = sec("Diagnósticos e ajustes",
    `<span class="cm-chip">${nOccur} de 7 com ocorrência</span>`, `<div class="cm-grid">` + [
      card("IRRF de resgate de fundo", "1% sobre a SOMA dos taxes; nunca ≥ 0. NÃO entra no NAV simulado — é uma transação taxes a criar (explica parte do resíduo do GAP mov).",
        irrfMissing.length ? `<span class="cm-chip">${irrfMissing.length} ausente(s)</span> ${money(r.irrfMissingTotal)}` : noneB("Sem resgate de fundo com IRRF ausente")),
      card("Liquidação stockETF / B3", "Σ provisões stockETF que liquidam × liquidação consolidada da B3. O resíduo (custo de execução) entra na CONTRIBUIÇÃO; a Opção 1 lança ajuste cash-neutro.",
        se ? `<span class="cm-chip">prov ${fmtMoney(se.provisionsTotal)}</span> <span class="cm-chip">B3 ${fmtMoney(se.b3Balance)}</span> resíduo <b class="cm-val ${nz(se.residual) < 0 ? "cm-neg" : ""}">${fmtMoney(se.residual)}</b>` : noneB("Sem stockETF liquidando / liquidação B3")),
      card("Provisões que liquidam na data", "Vêm do envelope da origem; já liquidaram no alvo (fora do NAV). Casadas 1:1 com liquidações de caixa da janela. Base do 'deslocar provisões'.",
        liq.length ? `<span class="cm-b cm-ok">${liqBy.green} casada</span> <span class="cm-b cm-warn">${liqBy.yellow} provável</span> <span class="cm-b cm-bad">${liqBy.red} sem caixa</span>` : noneB("Nenhuma liquidando na data-alvo")),
      card("Preços de execução a corrigir", "executionPrice placeholder (=PU) ou ausente, recalculado de −Σsaldo/Δqty; corrigido via PATCH/create. O impacto intraday já está na contribuição.",
        epFix.length ? `<span class="cm-chip">${epFix.length} a corrigir</span>` : noneB("Nenhum a corrigir (ou carteira só de fundos)")),
      card("Provisões oficiais no alvo", "Contexto/guardrail anti-duplicação. A parcela não duplicada entra no NAV (linha 'Oficiais' da composição).",
        offp.length ? `<span class="cm-chip">${offp.length} no sistema</span> no NAV ${money(r.officialProvisionsInNav)}` : noneB("Sem provisões oficiais ativas no alvo")),
      card("Ativos copiados do alvo", "Presentes só no alvo (confirmados na processed-alvo, qtd≠0); entram no NAV pelo saldo, sem P&L (contribuição 0).",
        adopted.length ? `<span class="cm-chip">${adopted.length} copiado(s)</span>` : noneB("Nenhum ativo copiado do alvo")),
      card("Revisão manual · procedência", "onlyCalc/onlyReal são flag-only (não geram escrita). targetSource='processed-fallback' = alvo veio da processed-position.",
        `${manual ? `<span class="cm-b cm-warn">${manual} p/ revisão</span>` : noneB("Nada p/ revisão manual")} <span class="cm-chip">alvo: ${esc(r.targetSource || "—")}</span>`),
    ].join("") + `</div>` + (hasTgt ? tgtBar(
      tchip("ativos divergentes", `<b>${counts.diverged || 0}</b>`) + tchip("só no alvo", `<b>${counts.onlyReal || 0}</b>`)
      + tchip("só na projeção", `<b>${counts.onlyCalc || 0}</b>`) + tchip("caixa", csBadge)
      + tchip("confiança", `<b>${esc(d.classificationConfidence || "—")}</b>`)) : ""));

  // ═════ DETALHES (tabelas colapsáveis) ═════
  const baseTbl = `<details><summary>Posição-origem — ${rows.length} ativo(s)</summary>${_tbl(
    `<th class="rp-tbl-left">Ativo</th><th style="text-align:right">Qtd origem</th><th style="text-align:right">PU origem</th><th style="text-align:right">Saldo origem</th>`,
    rows.map(x => `<tr><td class="rp-tbl-left">${esc(x.securityName || x.unprocessedId || "—")}</td><td>${fmtNum(x.formerQuantity, 4)}</td><td>${fmtNum(x.formerPu, 6)}</td><td>${fmtMoney(x.formerBalance)}</td></tr>`), "—")}</details>`;
  const movedTbl = moved.length
    ? `<details><summary>Ativos com Δ quantidade — ${moved.length}</summary>${_tbl(
        `<th class="rp-tbl-left">Ativo</th><th style="text-align:right">Qtd origem</th><th style="text-align:right">Qtd projetada</th><th style="text-align:right">Δ Qtd</th><th class="rp-tbl-left">Fonte PU</th>`,
        moved.map(x => `<tr><td class="rp-tbl-left">${esc(x.securityName || "—")}</td><td>${fmtNum(x.formerQuantity, 4)}</td><td>${fmtNum(x.quantity, 4)}</td><td class="${nz(x.quantity) - nz(x.formerQuantity) < 0 ? "cm-neg" : ""}">${fmtNum(nz(x.quantity) - nz(x.formerQuantity), 4)}</td><td class="rp-tbl-left text-[10px] text-gray-500">${esc(x.pricingType || "—")}</td></tr>`), "—")}</details>` : "";
  const divTbl = diverged.length
    ? `<details><summary>Divergências de quantidade (projetada × alvo) — ${diverged.length}</summary>${_tbl(
        `<th class="rp-tbl-left">Ativo</th><th style="text-align:right">Qtd projetada</th><th style="text-align:right">Qtd alvo</th><th style="text-align:right">Δ Qtd</th><th class="rp-tbl-left">Ação</th>`,
        diverged.map(x => `<tr><td class="rp-tbl-left">${esc(x.securityName || "—")}</td><td>${fmtNum(x.calcQuantity, 4)}</td><td>${fmtNum(x.realQuantity, 4)}</td><td class="${nz(x.qtyDiff) < 0 ? "cm-neg" : ""}">${fmtNum(x.qtyDiff, 6)}</td><td class="rp-tbl-left text-[10px]">${esc(x.suggestedAction || "—")}${x.adoptedTargetQty ? ' · <span class="cm-chip">qtd adotada</span>' : ""}${x.coveredByProvision ? ' · <span class="cm-chip">coberto por provisão</span>' : ""}${x.matured ? ' · <span class="cm-chip">vencimento → transação</span>' : ""}</td></tr>`), "—")}</details>` : "";
  const txByType = {}; (r.transactions || []).forEach(t => { const k = t.type || "—"; const o = txByType[k] || { n: 0, b: 0 }; o.n++; o.b += nz(t.balance); txByType[k] = o; });
  const txChips = Object.entries(txByType).map(([k, o]) => `<span class="cm-chip">${esc(k)} · ${o.n} · ${fmtMoney(o.b)}</span>`).join(" ") || noneB("Nenhuma transação na janela");
  const secDet = sec("Detalhes",
    `<span class="cm-chip">janela (origem , alvo]</span>`,
    (hasTgt ? tgtBar(
      tchip("origem do alvo", `<b>${esc(r.targetSource || "—")}</b>`) + tchip("caixa oficial", tMoney(oc))
      + tchip("divergências projetada × alvo", `<b>${diverged.length}</b>`)) : "")
    + `<div style="margin:6px 0">Transações da janela: ${txChips}</div>${baseTbl}${movedTbl}${divTbl}`);

  // ═════ FÓRMULAS E REGRAS (ao final) ═════
  const fr = (formula, desc) => `<div class="cm-fr"><span class="cm-f">${esc(formula)}</span><span class="cm-fr-d">${esc(desc)}</span></div>`;
  const fgroup = (title, rowsArr) => `<div class="cm-fg"><div class="cm-fg-t">${esc(title)}</div>${rowsArr.map(x => fr(x[0], x[1])).join("")}</div>`;
  const secFormulas = sec("Fórmulas e regras utilizadas", "", [
    fgroup("1 · Posição-origem e projeção", [
      ["PU origem = Σsaldo ÷ Σqtd", "Snapshot bruto (unprocessed) agregado por securityId. O PU-oficial da processed-origem sobrepõe quando existe."],
      ["qtd projetada = origem + Σ buySell", "Sem quantity na transação, infere −saldo ÷ PU (escada: execução → PU atual → securityPrices@data → PU origem → 1). Split multiplica; vencimento zera."],
      ["PU alvo: PROC → ALVO → securityPrices@data → REPETIDO", "Cascata de fontes do PU da data-alvo (processed-alvo oficial primeiro; por fim repete o PU da origem)."],
      ["saldo = PU × qtd", "Cada ativo re-precificado ao PU da data-alvo."],
      ["contribuição = fq·(PU−fPU) + (q−fq)·(PU−exec) + eventos", "P&L por ativo (variação diária + intraday + cupom/dividendo) somado ao P&L de carteira (gainsExpenses/rebate/ajustes)."],
    ]),
    fgroup("2 · amountDifference", [
      ["amountDifference (Δqty) = qtd alvo − qtd projetada", "Divergência de quantidade projetada × alvo (realQuantity − calcQuantity)."],
      ["divergência de qtd → PROVISÃO · vencimento → TRANSAÇÃO", "SEM caixa-âncora (verificação contra o caixa eliminada): a divergência vira sempre PROVISÃO de ajuste (adota a qtd-alvo, considerando o offset). Só o VENCIMENTO (o alvo ainda carrega o ativo vencido) vira TRANSAÇÃO de resgate. Gera inclusive no forward (sem caixa oficial)."],
      ["provisão = −execPrice × Δqty", "Balance da provisão de ajuste. execPrice = executionPrice capturado (endpoint execution-prices, data-alvo) → PU-alvo (realPu → calcPu) como fallback. Δ>0 (alvo tem MAIS = compra perdida) → caixa a pagar (balance<0); Δ<0 → resgate (balance>0). Tipo buySell / source amountDifference. NÃO cria se já há provisão (Passo 6 / oficial)."],
      ["datas: pelo OFFSET (settlementDays − navDays) do trade", "_prov_dates(alvo, offset): offset≠0 desloca a liquidação; ausente/0 → +1 dia útil (piso = próximo dia útil). Não é analisada contra o caixa."],
      ["intradayContribution = Δqty × (PU_linha − execPrice)", "P&L intraday do trade adotado (cotas marcadas ao PU × caixa a pagar ao execPrice). O sistema o SOMA ao returnContribution p/ parear o mesmo valor que a adoção soma ao NAV → a adoção fica NAV-neutra NO GAP. No fallback pu-alvo (execPrice = PU) é 0."],
      ["+Δqty×PU_linha (cotas) ⟷ −Δqty×execPrice (recon) + intraday (contribuição)", "Adoção: a linha adota a qtd-alvo (+Δqty×PU_linha no saldo), a provisão entra no NAV (−execPrice×Δqty) E o P&L intraday Δqty×(PU−execPrice) entra na contribuição. NAV e contribuição sobem juntos ⇒ o GAP não muda (amDiff ≈ 0), inclusive quando execPrice ≠ PU."],
    ]),
    fgroup("3 · Caixa", [
      ["caixa projetado = origem + Σ transações + vencimentos", "Caixa da origem mais o fluxo das transações da janela e o produto de vencimentos."],
      ["resíduo = projetado − oficial", "INFORMATIVO (caixa-âncora eliminado — não classifica ajustes). É a etapa 'caixa' dos GAPs por etapa (resíduo à parte)."],
      ["IRRF = 1% × Σ taxes", "Nunca ≥ 0. NÃO entra no NAV simulado — é uma transação taxes a criar."],
    ]),
    fgroup("4 · NAV e GAP", [
      ["NAV simulado = Σsaldos + caixa + provisões + recon + oficiais", "Composição da tabela do topo. 'recon' entra ao execPrice (−Δqty×execPrice); o P&L intraday resultante entra na contribuição → 'recon' é neutro NO GAP."],
      ["cota = (NAV − aportes) ÷ cotas(origem)", "Cotas da DATA-ORIGEM (o alvo ainda não tem contagem de cotas na projeção forward); subtrair aportes torna comparável."],
      ["retCota = cotaSim ÷ cotaOrigem − 1", "Retorno pela variação da cota."],
      ["retContrib = Σ contribuição ÷ NAV(origem)", "Retorno pela contribuição. Deve coincidir com retCota; a diferença é o GAP."],
      ["GAP mov = (retCota(sem recon) − retContrib) × NAV(orig)", "GAP do NAV ANTES da adoção da qtd-alvo (sim_nav SEM 'recon'): cotaMov = (NAV_mov − aportes) ÷ cotas. É o resíduo da projeção em si."],
      ["GAP amDiff = GAP final − GAP mov", "Efeito ISOLADO da adoção da qtd-alvo no GAP. A adoção soma +Δqty×PU_linha às cotas E −Δqty×execPrice em 'recon'; o P&L intraday Δqty×(PU−execPrice) daí resultante é somado à contribuição (intradayContribution). NAV e contribuição sobem juntos ⇒ amDiff ≈ 0 SEMPRE (também com execPrice ≠ PU). Em R$ = (retCota − retCotaMov) × NAV(orig)."],
      ["GAP final = mov + amDiff", "O caixa é resíduo à parte (não é retorno)."],
    ]),
  ].join(""));

  const banner = (r.simNav != null && r.simNavPerShare == null)
    ? `<div class="cm-note" style="background:#fef3c7;color:#92400e;padding:8px 10px;border-radius:8px;margin-bottom:10px">⚠ Esta carteira não tem NAV/cotas oficiais na data-origem (navPackage indisponível): o NAV simulado é calculado, mas cota, retornos e GAP em R$ ficam <b>não calculados</b>.</div>`
    : "";
  // Banner do cenário WHAT-IF — deixa explícito que os números abaixo NÃO são a projeção real.
  const wiBanner = _wi
    ? `<div class="cm-note" style="background:#fffbeb;color:#b45309;border:1px solid #fde68a;padding:8px 10px;border-radius:8px;margin-bottom:10px">⚠ <b>Cenário what-if</b> — ${_wi.count} provisão(ões) ignorada(s) do NAV/GAP (não grava). NAV simulado ajustado em <b>${fmtMoney(-_wi.drop)}</b>; todos os números abaixo já refletem o cenário recalculado.</div>`
    : "";
  return _CM_STYLE + `<div class="cm">${wiBanner}${banner}${secComp}${secResult}${secDiag}${secDet}${secFormulas}</div>`;
}
