/* Repetir Posições — módulo Repetir (objeto único). Extraído p/ static/ (CLAUDE.md).
   Reusado por repetir_posicoes.html e controlpanel.html (chip Projetada). */
/* ════════════════════════════════════════════════════════════════════════
   Repetir partial — extraído de repetir_posicoes.html para reuso pelo
   painel inline em controlpanel.html (chip Projetada). Mesmo módulo
   `const Repetir = {...}` + helpers; SEM o DOMContentLoaded final do
   template original (movido pro repetir_posicoes.html standalone).

   IMPORTANTE: não envolver este código em IIFE. Os onclick inline da
   view (Repetir.switchTab, Repetir.runDaily, etc.) precisam que
   `const Repetir` esteja em script-scope, e o `window.Repetir = Repetir`
   ao final garante o fallback pra browsers/contextos onde o lookup
   pela Global Lexical Environment não funciona.

   Quando incluído pelo controlpanel.html, `Funcoes` já está definido
   pelo script anterior. Quando incluído pela página standalone, o stub
   abaixo entra (Repetir.open() usa Funcoes pra alternar chips).
════════════════════════════════════════════════════════════════════════ */
if (typeof Funcoes === 'undefined') {
  window.Funcoes = {
    _hideAllFrames(){},
    _currentDeep: null,
    _inDaytrade:  false,
    _inStrip:     false,
    _inRepetir:   false,
    _setActiveChip(){},
    _syncResetChip(){},
    showIssues(){ window.location.href = '/'; },
  };
}

  // Top-of-script breadcrumb so the operator can verify in DevTools
  // that this script actually ran (visible in the Console tab).
  console.log("[Repetir] script loaded", new Date().toISOString());

  // Use `var` (not `const`) so the binding lives on `window` directly.
  // `const Repetir = {...}` at top-level lives in the global lexical
  // scope but does NOT attach to `window`, which can cause inline
  // `onclick="Repetir.switchTab(...)"` to silently throw
  // ReferenceError in some browsers — especially inside iframes.
  // Switching to `var` is the universally-compatible fix.
  var Repetir = {
    _st: {
      activeTab: "projetada",
      // ── Seleção de Wallets tab ──
      // The transfer-list composes a list-in-progress; "Salvar como
      // lista..." persists it as a named preset. There's no separate
      // "active flagged set" anymore — the daily routine reads only
      // from saved presets.
      walletsByCompany:  [],    // current company's wallets (browsing pool)
      walletsMeta:       {},    // walletId -> {name, companyId, companyName}
      walletsSelected:   new Set(),  // current list-builder selection (NOT persisted until save)
      companyNames:      {},    // companyId -> name (cached from /filters/companies)
      searchAvailable:   "",    // typeahead filter for the Disponíveis pane
      searchSelected:    "",    // typeahead filter for the Selecionadas pane
      loadedListName:    "",    // name of the list currently loaded for editing (informational)
      // ── Rotina diária tab ──
      // The daily roster = union of walletIds across `selectedLists`.
      selectedLists:     new Set(),  // names of lists checked on the daily tab
      daily:             [],    // [{walletId, walletName, lastDate, suggestedTarget}]
      dailyChecked:      new Set(),
      dailySourceDate:   {},    // walletId -> override de "Última posição" (user-editable)
      dailyTargetDate:   {},    // walletId -> target date (user-editable)
      preview:           [],    // server response
      puOverrides:       {},    // "<walletId>|<securityId>|<targetDate>" -> pu
      walletLists:       [],    // [{name, walletIds[], wallets[], addedAt}] — saved presets
      lastLogReport:     null,  // last /apply diff report (used by "Baixar JSON")
      activeWalletId:    null,  // walletId currently being previewed (single-wallet mode)
      txns:              [],    // current Transações tab listing
      provs:             [],    // current Provisões tab listing
      hiddenCols:        new Set(),  // column keys hidden in the preview table
      onlyDiffs:         false,      // filter: show only rows where repetição diverge de target
      // ── Carteiras da empresa (atalho p/ rodar prévia) ──
      // Lista as carteiras do /results na data escolhida (sem divergência) e
      // dispara a prévia ao clicar. O único estado extra é criado sob demanda:
      // `clInjectedWallets` (Set) — carteiras injetadas no roster por esse
      // atalho, removidas em `closePreview`.
    },

    // Registry of every column in the preview table — drives the
    // "Colunas" modal. The keys MUST match the `data-col` attribute
    // on the corresponding `<th>` / `<td>` (see _renderPreview's table
    // markup). Order = display order in the modal.
    _COLUMNS: [
      {key: "accepted",        label: "Aceito (checkbox)"},
      {key: "ativo",           label: "Ativo"},
      {key: "securityId",      label: "SecurityId"},
      {key: "unprocessedId",   label: "UnprocessedId"},
      {key: "pricing",         label: "Pricing"},
      {key: "offset",          label: "Offset (settle − NAV)"},
      {key: "qtyOriginal",     label: "Qtd anterior"},
      {key: "puOriginal",      label: "PU anterior"},
      {key: "balanceOriginal", label: "Saldo anterior"},
      {key: "qtyTarget",       label: "Qtd atual"},
      {key: "puTarget",        label: "PU atual"},
      {key: "balanceTarget",   label: "Saldo atual"},
      {key: "contribActual",    label: "Contrib. atual"},
      {key: "qtyRep",          label: "Qtd projetada"},
      {key: "puRep",           label: "PU projetada"},
      {key: "balanceRep",      label: "Saldo projetado"},
      {key: "contribProjected", label: "Contrib. projetada"},
      {key: "amountDiff",      label: "Δ Qtd (anterior − atual)"},
      {key: "provisions",      label: "Provisões"},
      {key: "diff",            label: "Diff vs atual"},
      {key: "txns",            label: "Transação"},
      {key: "flags",           label: "Flags"},
    ],

    _esc(s) {
      return String(s ?? "").replace(/[&<>"']/g,
        c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    },
    _fmtNum(v, frac) {
      if (v == null) return '<span class="text-gray-300">—</span>';
      const n = Number(v);
      if (!isFinite(n)) return '<span class="text-gray-300">—</span>';
      return n.toLocaleString("pt-BR", {minimumFractionDigits: frac, maximumFractionDigits: frac});
    },
    _fmtQty(v)   { return this._fmtNum(v, 6); },
    // Versão de **display** com 2 casas — usada só nas colunas
    // Qtd anterior / Qtd atual / Qtd projetada / Δ Qtd da tabela
    // principal de securities. Os dados brutos no payload mantêm a
    // precisão completa (6+ casas) — só a apresentação é arredondada.
    // O `_fmtQty` (6 casas) continua sendo usado em tooltips, diff
    // cells, tabela de Transações e demais lugares onde detalhe importa.
    _fmtQtyDisplay(v) { return this._fmtNum(v, 2); },
    _fmtPu(v)    { return this._fmtNum(v, 6); },
    _fmtMoney(v) { return this._fmtNum(v, 2); },

    // ── Offset (settlement − NAV) ─────────────────────────────────────
    // Lê `subscriptionOffset` / `redemptionOffset` do payload (já
    // calculados no backend via `_security_meta`). Quando ambos são
    // iguais, mostra um número só (`D+N`). Quando divergem, mostra
    // `sub D+N / red D+M` em fonte menor. Quando o payload não traz
    // (caixa, provisão), o caller já renderiza um `—`.
    _offsetHTML(r) {
      const sub = Number(r.subscriptionOffset || 0);
      const red = Number(r.redemptionOffset   || 0);
      if (sub === red) return `D+${sub}`;
      return `<span style="font-size:10px">sub D+${sub} / res D+${red}</span>`;
    },
    _offsetTitle(r) {
      const sub = Number(r.subscriptionOffset || 0);
      const red = Number(r.redemptionOffset   || 0);
      return `subscription: settlement − NAV = D+${sub} · redemption: settlement − NAV = D+${red}`;
    },

    // ── Bloco NAV (Anterior / Projetada / Atual) ─────────────────────
    // Renderiza três colunas lado a lado mostrando `nav`,
    // `navPerShare` e `amount`. A do meio (Projetada) também mostra
    // `inAndOutFlows`. Quando o backend devolve um campo `null`
    // (sem navPackage pra data ou denominador zero), a célula vira
    // `—`. O bloco fica logo abaixo do header da prévia.
    _navCell(label, val, kind) {
      if (val == null) return `<div class="text-[10px] text-gray-300">${this._esc(label)}: —</div>`;
      const formatted = kind === "money" ? this._fmtMoney(val)
                       : kind === "nps"   ? this._fmtNum(val, 8)
                       : kind === "amt"   ? this._fmtNum(val, 6)
                       : kind === "pct"   ? `${this._fmtNum(val * 100, 4)}%`
                       : this._fmtNum(val, 2);
      // Cor pra retornos/gap — positivo verde, negativo vermelho,
      // próximo de zero cinza. Vale apenas pra kind=pct/money quando
      // o label sugere métrica de performance.
      let cls = "";
      if (kind === "pct" && Math.abs(val) >= 1e-6) {
        cls = val > 0 ? "text-green-700" : "text-red-700";
      }
      return `<div class="text-[10px]"><span class="text-gray-500">${this._esc(label)}:</span> <strong class="${cls}">${formatted}</strong></div>`;
    },
    _renderNavBlock(p) {
      const a = p.navAnterior  || null;
      const j = p.navProjetada || null;
      const t = p.navAtual     || null;
      // Layout: 3 cards lado a lado (Anterior/Projetada/Atual).
      // Cada card de Projetada/Atual é dividido internamente em
      // 3 sub-colunas — `posição`, `contribuição`, `retornos` —
      // pra ocupar menos espaço vertical (operadora pediu).
      // Anterior tem só 3 valores, então fica em coluna única.
      const subCol = (titulo, cells) => `
        <div style="flex:1 1 0; min-width:0; padding:0 6px">
          ${titulo ? `<div class="text-[10px] text-gray-400" style="border-bottom:1px dashed #e5e7eb;padding-bottom:2px;margin-bottom:2px">${this._esc(titulo)}</div>` : ""}
          ${cells.join("")}
        </div>`;
      const card = (titulo, src, opts = {}) => {
        if (!src) {
          return `
            <div style="flex:1 1 0; min-width:200px; padding:6px 10px; border-right:1px solid #f3f4f6">
              <div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600">${this._esc(titulo)}</div>
              <div class="text-[10px] text-gray-300 italic">sem navPackage</div>
            </div>`;
        }
        // Coluna "posição" — valores extraídos do navPackage. Para
        // Atual e Projetada, `inAndOutFlows` aparece sempre (mostra "—"
        // quando o campo é null), pra que o operador veja explicitamente
        // que o navPackage da data alvo ainda não foi gravado vs. foi
        // gravado com fluxo zero.
        const posCells = [
          this._navCell("NAV",         src.nav,         "money"),
          this._navCell("navPerShare", src.navPerShare, "nps"),
          this._navCell("amount",      src.amount,      "amt"),
        ];
        if (opts.showInflows) {
          posCells.push(this._navCell("inAndOutFlows", src.inAndOutFlows, "money"));
        }
        // Modo simples (Anterior): 1 coluna só.
        if (!opts.withReturns) {
          return `
            <div style="flex:1 1 0; min-width:160px; padding:6px 10px; border-right:1px solid #f3f4f6">
              <div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600;margin-bottom:3px">${this._esc(titulo)}</div>
              ${posCells.join("")}
            </div>`;
        }
        // Modo completo (Projetada/Atual): 3 sub-colunas base
        // (posição/contribuição/retornos) + 1 sub-coluna "composição"
        // quando o backend emite `composition` (decomposição calculada
        // da NAV em Σ saldos + provisões + caixa).
        const contribCells = [
          this._navCell("ativos", src.securitiesContribution, "money"),
          this._navCell("wallet", src.walletContribution,     "money"),
          this._navCell("total",  src.totalContribution,      "money"),
        ];
        const retornosCells = [
          this._navCell("rent. NAV/share", src.returnNavPerShare,  "pct"),
          this._navCell("rent. contrib.",  src.returnContribution, "pct"),
          this._navCell("GAP %",           src.gapPct,             "pct"),
          this._navCell("GAP R$",          src.gapCash,            "money"),
        ];
        // Composição — só renderiza quando o backend mandar (atual e
        // projetada têm; anterior não). `expectedProvisions` só vira
        // linha visível quando > 0 (caso comum: zero pra Atual).
        // `total` (NAV calculada localmente = Σ parcelas) fechada no
        // fim — comparação direta com o NAV vindo do navPackage que
        // aparece na sub-coluna "navPackage" da Atual.
        let compSubCol = "";
        if (src.composition) {
          const c = src.composition;
          const compCells = [
            this._navCell("saldos",    c.securitiesBalance, "money"),
            this._navCell("provisões", c.provisions,        "money"),
          ];
          if (c.expectedProvisions != null && Math.abs(c.expectedProvisions) >= 0.005) {
            compCells.push(this._navCell("prov. esperadas", c.expectedProvisions, "money"));
          }
          compCells.push(this._navCell("caixa", c.cash, "money"));
          // "NAV (calc)" só renderiza quando o card pede explicitamente
          // (`opts.showCompositionTotal`). Útil na Atual — comparação
          // direta com `navPackage.nav`. Na Projetada é redundante:
          // `composition.total ≡ posição.NAV` por construção.
          if (opts.showCompositionTotal && c.total != null) {
            compCells.push(this._navCell("NAV (calc)", c.total, "money"));
          }
          compSubCol = subCol("composição", compCells);
        }
        // Label da sub-coluna de "posição": valores vindos diretamente
        // do navPackage (Atual, Anterior) recebem o label explícito
        // "navPackage" pra deixar claro que **não** são números
        // calculados pela prévia. Projetada mantém "posição" porque
        // os 4 campos lá são todos derivados do cálculo (e não de um
        // doc do navPackages).
        const posLabel = opts.posLabel || "posição";
        return `
          <div style="flex:2 1 0; min-width:360px; padding:6px 10px; border-right:1px solid #f3f4f6">
            <div class="text-[10px] uppercase tracking-wide text-gray-500" style="font-weight:600;margin-bottom:3px">${this._esc(titulo)}</div>
            <div style="display:flex; gap:2px">
              ${subCol(posLabel,       posCells)}
              ${compSubCol}
              ${subCol("contribuição", contribCells)}
              ${subCol("retornos",     retornosCells)}
            </div>
          </div>`;
      };
      // Estado de colapso persistido em localStorage para sobreviver
      // a rerenders (cada vez que o usuário roda nova prévia, o bloco
      // é remontado do zero). Default = aberto.
      const collapsed = (typeof localStorage !== "undefined") &&
                        localStorage.getItem("rp.navBlock.collapsed") === "1";
      const chev = collapsed ? "▸" : "▾";
      const lbl  = collapsed ? "Mostrar bloco NAV" : "Ocultar bloco NAV";
      // Inline handler: toggla classe `.rp-nav-collapsed` no wrapper,
      // alterna o ícone e persiste no localStorage. Mantém zero
      // dependência de outros handlers e funciona mesmo se este HTML
      // for re-renderizado.
      const toggleJS =
        "var w=this.closest('.rp-nav-bar');" +
        "var c=w.classList.toggle('rp-nav-collapsed');" +
        "this.textContent=c?'▸':'▾';" +
        "this.title=c?'Mostrar bloco NAV':'Ocultar bloco NAV';" +
        "try{localStorage.setItem('rp.navBlock.collapsed', c?'1':'0');}catch(e){}";
      return `
        <div class="rp-nav-bar ${collapsed ? "rp-nav-collapsed" : ""}" style="position:relative; border-bottom:1px solid #e5e7eb; background:#fafafa">
          <button type="button"
                  onclick="${toggleJS}"
                  title="${this._esc(lbl)}"
                  style="position:absolute; top:2px; right:6px; z-index:2; width:18px; height:18px; padding:0; line-height:1; font-size:11px; border:1px solid #d1d5db; border-radius:3px; background:white; cursor:pointer; color:#6b7280">${chev}</button>
          <div class="rp-nav-content" style="display:flex; flex-wrap:wrap; padding:4px 28px 6px 12px">
            ${card("Anterior",  a)}
            ${card("Atual",     t, {withReturns: true, showInflows: true, posLabel: "navPackage", showCompositionTotal: true})}
            ${card("Projetada", j, {withReturns: true, showInflows: true})}
          </div>
        </div>`;
    },

    // ── Contribuição (totalContribution) por security ───────────────
    // Fórmulas 4-7 do `docs/CONCILIACAO_RECALCULO.md` consolidadas
    // num número: `daily + intraday + event`. UI mostra o total como
    // moeda (verde positivo / vermelho negativo / zero cinza) e o
    // tooltip explora as três parcelas.
    _contribHTML(c) {
      if (!c) return '<span class="text-gray-300">—</span>';
      const t = Number(c.total);
      if (!Number.isFinite(t)) return '<span class="text-gray-300">—</span>';
      if (Math.abs(t) < 0.005) return '<span class="rp-diff-zero">0</span>';
      const cls = t > 0 ? "rp-diff-pos" : "rp-diff-neg";
      const sig = t > 0 ? "+" : "";
      return `<span class="${cls}">${sig}${this._fmtMoney(t)}</span>`;
    },
    _contribTitle(c) {
      if (!c) return "Sem contribuição (security sem entrada na posição correspondente).";
      return `daily: ${this._fmtMoney(c.daily)} · intraday: ${this._fmtMoney(c.intraday)} · event: ${this._fmtMoney(c.event)} → total: ${this._fmtMoney(c.total)}`;
    },

    // ── Provisões por ativo ────────────────────────────────────────
    // `provisionsBalance` é o somatório das provisões ativas em
    // targetDate cujo `securityId` casa com este ativo. `null` quando
    // o security não tem nenhuma provisão → célula `—`. Quando há
    // mais de uma provisão, o tooltip lista descrição + janela
    // [initialDate, liquidationDate) de cada uma (mesma régua do
    // header da prévia). Quando o ativo tem `expectedProvision` (Δqty
    // sem buySell/provisão), a célula vira "Pendente: R$ X" em
    // vermelho — sinal pro operador criar a provisão antes do apply.
    _provisionsHTML(r) {
      const exp = r && r.expectedProvision;
      const bal = r && r.provisionsBalance;
      if (exp) {
        const ebal = Number(exp.balance);
        const txt = Number.isFinite(ebal) ? this._fmtMoney(ebal) : "?";
        return `<span class="rp-expected-prov-pill">${txt}</span>`;
      }
      if (bal == null) return '<span class="text-gray-300">—</span>';
      const n = Number(bal);
      if (!Number.isFinite(n) || Math.abs(n) < 0.005) return '<span class="rp-diff-zero">0</span>';
      return this._fmtMoney(n);
    },
    // ── Bloco "Provisões esperadas" ────────────────────────────────
    // Renderiza uma sub-tabela acima da listagem CRUD de provisões da
    // carteira, com as provisões SUGERIDAS pelo sistema (a partir de
    // `amountDifference` sem buySell/provisão correspondente). Banda
    // vermelha pra destacar como pendência. Quando não há expectativa,
    // o bloco não é renderizado.
    _renderExpectedProvisionsBlock(p) {
      const list = (p && p.expectedProvisions) || [];
      if (!list.length) return "";
      const rows = list.map(pr => {
        const desc = pr.description || "Provisão esperada";
        const win  = `${pr.initialDate || "?"} → ${pr.liquidationDate || "?"}`;
        const dir  = pr.direction === "subscription" ? "compra" : "resgate";
        // Tooltip difere por fonte:
        //   amountDifference → fórmula `executionPrice × Δqty × (−1)`
        //   transaction      → fórmula `Σ balance(txns) × (−1)`
        let formulaLine;
        if (pr.provisionSource === "transaction") {
          const tot = (pr.transactionsTotal != null) ? this._fmtMoney(pr.transactionsTotal) : "?";
          const ntx = (pr.transactionIds || []).length;
          formulaLine = `Σ balance(${ntx} transação(ões)) × (−1) = ${tot} × (−1) = ${this._fmtMoney(pr.balance)}`;
        } else {
          const dq = pr.amountDifference;
          const ep = pr.executionPrice;
          formulaLine = `executionPrice × Δqty × (−1) = ${ep} × ${dq} × (−1) = ${this._fmtMoney(pr.balance)}`;
        }
        const titleStr = (
          `PROVISÃO ESPERADA (sugerida pelo sistema)\n` +
          `${desc}\n` +
          `Source: ${pr.provisionSource} · Direção: ${dir} · offset=D+${pr.offset} (dias úteis)\n` +
          `Janela: ${win}\n` +
          `${formulaLine}\n` +
          `Type=${pr.provisionType}`
        );
        return `<tr title="${this._esc(titleStr)}">
          <td>${this._esc(pr.initialDate || "—")}</td>
          <td>${this._esc(pr.liquidationDate || "—")}</td>
          <td class="rp-tbl-left">${this._esc(pr.provisionType || "—")}</td>
          <td class="rp-tbl-left">${this._esc(desc)}</td>
          <td class="rp-tbl-left">${this._esc(pr.securityName || pr.securityId || "—")}</td>
          <td>${this._fmtMoney(pr.balance)}</td>
          <td><span class="rp-expected-prov-pill">${this._esc(dir)} · D+${pr.offset}</span></td>
        </tr>`;
      }).join("");
      return `
        <div class="mb-3" style="border:1px solid #fca5a5; border-radius:0.5rem; overflow:hidden">
          <div style="background:#fef2f2; color:#7f1d1d; padding:6px 10px; font-size:11px; font-weight:600; display:flex; align-items:center; gap:8px">
            <span class="rp-expected-prov-pill">Pendentes (${list.length})</span>
            <span>Provisões esperadas — duas fontes: <code>amountDifference</code> (Δqty sem buySell) e <code>transaction</code> (buySell/maturity sem Δqty).</span>
          </div>
          <table class="rp-tbl">
            <thead>
              <tr>
                <th>Início</th>
                <th>Liquidação</th>
                <th class="rp-tbl-left">Tipo</th>
                <th class="rp-tbl-left">Descrição</th>
                <th class="rp-tbl-left">Ativo</th>
                <th>Saldo</th>
                <th>Direção · Offset</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    },

    _provisionsTitle(r) {
      const exp  = r && r.expectedProvision;
      const list = (r && r.provisionsList) || [];
      if (exp) {
        const win = `${exp.initialDate || "?"} → ${exp.liquidationDate || "?"}`;
        const bal = (exp.balance != null) ? this._fmtMoney(exp.balance) : "—";
        const dir = exp.direction === "subscription" ? "compra" : "resgate";
        let formulaLine;
        if (exp.provisionSource === "transaction") {
          const tot = (exp.transactionsTotal != null) ? this._fmtMoney(exp.transactionsTotal) : "?";
          const ntx = (exp.transactionIds || []).length;
          formulaLine = `Σ balance(${ntx} transação(ões)) × (−1) = ${tot} × (−1) = ${bal}`;
        } else {
          const dq = exp.amountDifference;
          const ep = exp.executionPrice;
          formulaLine = `executionPrice × Δqty × (−1) = ${ep} × ${dq} × (−1) = ${bal}`;
        }
        return (
          `PROVISÃO PENDENTE (Source=${exp.provisionSource})\n` +
          `${exp.description}\n` +
          `Direção: ${dir} · offset=D+${exp.offset} (dias úteis)\n` +
          `Janela: ${win}\n` +
          `${formulaLine}\n` +
          `Type=${exp.provisionType}`
        );
      }
      if (!list.length) return "Sem provisões ativas para este ativo na data alvo.";
      const lines = list.map(p => {
        const desc = (p.description || p.kind || "Provisão").trim();
        const win  = `${p.initialDate || "?"} → ${p.liquidationDate || "?"}`;
        const bal  = (p.balance != null) ? this._fmtMoney(p.balance) : "—";
        return `${desc} · ${win} · ${bal}`;
      });
      return lines.join("\n");
    },

    // ── Δ Qtd (original − target) ─────────────────────────────────────
    // Útil para auditar movimento independente de transação: positivo
    // = posição diminuiu entre source e target (resgate); negativo =
    // posição aumentou (compra). Dash quando não há entrada no target.
    _amountDiffHTML(r) {
      const tp = r.targetProcessed;
      if (!tp) return '<span class="text-gray-300">—</span>';
      const origQ = Number((r.original && r.original.quantity) || 0);
      const tgtQ  = Number(tp.quantity || 0);
      const d = origQ - tgtQ;
      if (Math.abs(d) < 1e-6) return '<span class="rp-diff-zero">0</span>';
      const cls = d > 0 ? "rp-diff-pos" : "rp-diff-neg";
      const sign = d > 0 ? "+" : "";
      // Display com 2 casas — Δ Qtd na coluna principal.
      return `<span class="${cls}">${sign}${this._fmtQtyDisplay(d)}</span>`;
    },
    _todayISO() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
    },
    // Next Mon-Fri strictly after `fromISO` (or today). Mirrors the
    // server-side `_next_biz_day` in pages/repetir_posicoes.py so the
    // pre-filled value matches what the backend would have suggested.
    _nextBizDayISO(fromISO) {
      const base = fromISO ? new Date(fromISO + "T12:00:00") : new Date();
      base.setDate(base.getDate() + 1);
      while (base.getDay() === 0 || base.getDay() === 6) {
        base.setDate(base.getDate() + 1);
      }
      const y = base.getFullYear();
      const m = String(base.getMonth() + 1).padStart(2, "0");
      const day = String(base.getDate()).padStart(2, "0");
      return `${y}-${m}-${day}`;
    },

    async init() {
      // Each step is wrapped so a single fetch failure doesn't leave
      // the page half-initialized (and, more importantly, doesn't
      // throw out of an inline event handler and silently kill the
      // rest of the boot). Tabs must remain clickable even if every
      // fetch fails — they're inline `onclick="Repetir.switchTab(...)"`.
      try { await this._loadColumnsConfig(); }
      catch (e) { console.error("_loadColumnsConfig failed:", e); }
      try { await this._loadCompanies(); }
      catch (e) { console.error("_loadCompanies failed:", e); }
      try { await this._loadWalletLists(); }
      catch (e) { console.error("_loadWalletLists failed:", e); }
      // Entrada da tela: listas nascem DESMARCADAS e o roster da rotina
      // diária NÃO é carregado automaticamente. O operador entra com o
      // foco no bloco "Carteiras da empresa" — o roster só popula quando
      // ele marca uma lista. (Antes marcávamos todas as listas e
      // disparávamos runDaily, enchendo a tela de cara.)
      this._st.selectedLists = new Set();
      try { this._renderListPicker(); }
      catch (e) { console.error("_renderListPicker failed:", e); }
      // Roster vazio sem fazer fetch a /daily (sem listas → zero linhas).
      this._st.daily = [];
      try { this._renderDailyTable(); }
      catch (e) { console.error("_renderDailyTable failed:", e); }
      const rpStatus = document.getElementById("rp-status");
      if (rpStatus) rpStatus.textContent = "Marque pelo menos uma lista para carregar carteiras.";
    },

    async _loadCompanies() {
      const sel = document.getElementById("rp-company");
      // Selector do bloco "Carteiras da empresa" — populado com a mesma
      // lista. Mantém UI consistente e evita uma segunda fetch.
      const cl  = document.getElementById("rp-cl-company");
      try {
        const r = await fetch("/api/repetir-posicoes/filters/companies");
        const items = await r.json();
        const list = Array.isArray(items) ? items : [];
        this._st.companyNames = {};
        for (const c of list) this._st.companyNames[c.id] = c.name || c.id;
        const optsHTML =
          '<option value="">Selecione...</option>' +
          list.map(c =>
            `<option value="${this._esc(c.id)}">${this._esc(c.name || c.id)}</option>`
          ).join("");
        if (sel) sel.innerHTML = optsHTML;
        if (cl)  cl.innerHTML  = optsHTML;
      } catch (e) {
        if (sel) sel.innerHTML = '<option value="">Erro ao carregar empresas</option>';
        if (cl)  cl.innerHTML  = '<option value="">Erro ao carregar empresas</option>';
      }
    },

    switchTab(name) {
      // Diagnostic log so the operator can verify clicks reach this
      // handler — visible in browser DevTools (F12 → Console). Remove
      // once the tab-click problem is fully understood.
      console.log("[Repetir] switchTab ->", name);
      this._st.activeTab = name;
      for (const t of ["projetada", "config", "regras"]) {
        const btn = document.getElementById(`rp-tab-${t}-btn`);
        const sec = document.getElementById(`rp-tab-${t}`);
        if (!btn || !sec) {
          console.warn("[Repetir] missing tab element for", t,
                       "btn=", !!btn, "sec=", !!sec);
          continue;
        }
        btn.classList.toggle("active", name === t);
        // Use both the Tailwind `.hidden` class AND an explicit inline
        // `display` so the switch works even before Tailwind's CDN
        // finishes injecting class rules (iframes occasionally race
        // the parent on stylesheet readiness).
        sec.classList.toggle("hidden", name !== t);
        sec.style.display = (name === t) ? "" : "none";
      }
    },

    // _populateCrudWalletPickers removed — Transações/Provisões blocks
    // are now rendered inside the preview output for the active wallet,
    // so a page-level wallet picker is no longer needed.

    async onCompanyChange() {
      // Empresa pertence ao tab "Seleção de Wallets" — só serve para
      // popular o pane "Disponíveis". A Rotina diária independe dela.
      const cid = document.getElementById("rp-company").value;
      this._st.walletsByCompany = [];
      this._renderConfigPanes();
      if (!cid) return;
      await this._loadWallets(cid);
    },

    async _loadWallets(cid) {
      try {
        const r = await fetch(`/api/repetir-posicoes/filters/wallets?companyId=${encodeURIComponent(cid)}`);
        const items = await r.json();
        this._st.walletsByCompany = Array.isArray(items) ? items : [];
        // Refresh meta for these wallets so adds from this view know the
        // (companyId, companyName) — needed for the Selecionadas pane
        // labels when the operator hasn't switched away yet.
        const cname = this._st.companyNames[cid] || cid;
        for (const w of this._st.walletsByCompany) {
          this._st.walletsMeta[w.id] = {
            name:        w.name,
            companyId:   cid,
            companyName: cname,
          };
        }
      } catch (e) {
        this._st.walletsByCompany = [];
      }
      this._renderConfigPanes();
    },

    // ── Config: transfer-list ─────────────────────────────────────────
    // Available pane = wallets of the currently selected company minus the
    //                  globally flagged ones.
    // Selected  pane = the global flagged set (with company name suffix),
    //                  shown across all companies.
    // Both panes honour a typeahead filter (case-insensitive substring
    // match on name + company name). The "add all / remove all" buttons
    // operate on the **currently visible** options, so a search-then-«
    // does NOT wipe the entire list — only what the operator can see.
    _matchSearch(needle, ...haystacks) {
      if (!needle) return true;
      const n = needle.toLowerCase();
      return haystacks.some(h => (h || "").toLowerCase().includes(n));
    },
    _renderConfigPanes() {
      const cid = document.getElementById("rp-company").value;
      const avail = document.getElementById("rp-wal-available");
      const sel   = document.getElementById("rp-wal-selected");
      const qA = this._st.searchAvailable || "";
      const qS = this._st.searchSelected  || "";

      const available = (cid
        ? this._st.walletsByCompany.filter(w => !this._st.walletsSelected.has(w.id))
        : []
      ).filter(w => this._matchSearch(qA, w.name, w.id));

      const selectedIds = Array.from(this._st.walletsSelected);
      const selected = selectedIds.map(id => {
        const m = this._st.walletsMeta[id] || {};
        return {id, name: m.name || id, companyName: m.companyName || ""};
      }).filter(w => this._matchSearch(qS, w.name, w.companyName, w.id))
        .sort((a, b) => {
          const ca = (a.companyName || "").toLowerCase();
          const cb = (b.companyName || "").toLowerCase();
          if (ca !== cb) return ca < cb ? -1 : 1;
          return (a.name || "").toLowerCase().localeCompare((b.name || "").toLowerCase());
        });

      avail.innerHTML = available.map(w => `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`).join("");
      sel.innerHTML   = selected .map(w => {
        const tag = w.companyName ? ` — ${this._esc(w.companyName)}` : "";
        return `<option value="${this._esc(w.id)}">${this._esc(w.name)}${tag}</option>`;
      }).join("");

      // Counts reflect totals (not filtered) when no search is active,
      // and "filtrado/total" when the operator is typing — keeps awareness
      // that more entries exist behind the filter.
      const totalAvail = cid
        ? this._st.walletsByCompany.filter(w => !this._st.walletsSelected.has(w.id)).length
        : 0;
      const totalSel = selectedIds.length;
      document.getElementById("rp-wal-available-count").textContent =
        !cid ? "—" :
        (qA ? `${available.length}/${totalAvail}` : String(totalAvail));
      document.getElementById("rp-wal-selected-count").textContent =
        qS ? `${selected.length}/${totalSel}` : String(totalSel);
    },
    onSearchChange(pane, value) {
      if (pane === "available") this._st.searchAvailable = value || "";
      else                       this._st.searchSelected  = value || "";
      this._renderConfigPanes();
    },
    _highlighted(id) {
      return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
    },
    addWalletSelected()    { this._highlighted("rp-wal-available").forEach(id => this._st.walletsSelected.add(id)); this._renderConfigPanes(); },
    addWalletAll()         { Array.from(document.getElementById("rp-wal-available").options).forEach(o => o.value && this._st.walletsSelected.add(o.value)); this._renderConfigPanes(); },
    removeWalletSelected() { this._highlighted("rp-wal-selected").forEach(id => this._st.walletsSelected.delete(id)); this._renderConfigPanes(); },
    removeWalletAll()      { Array.from(document.getElementById("rp-wal-selected").options).forEach(o => o.value && this._st.walletsSelected.delete(o.value)); this._renderConfigPanes(); },

    clearSelection() {
      this._st.walletsSelected = new Set();
      this._st.loadedListName  = "";
      this._renderConfigPanes();
      const status = document.getElementById("rp-config-status");
      status.textContent = "Seleção limpa.";
    },

    // ── Saved wallet lists (named presets) ───────────────────────────
    // The named-presets list IS the daily routine's source of truth.
    // Loading a preset populates the transfer-list for editing; saving
    // (via "Salvar como lista...") with the same name overwrites the
    // preset. The daily tab picks WHICH presets to run via checkboxes.
    async _loadWalletLists() {
      try {
        const r = await fetch("/api/repetir-posicoes/wallet-lists");
        const data = await r.json();
        this._st.walletLists = (data && data.lists) || [];
        // Hydrate walletsMeta from the enriched preset payload so the
        // Selecionadas pane can show names/companies for wallets the
        // operator hasn't yet pulled from a company picker.
        for (const l of this._st.walletLists) {
          for (const w of (l.wallets || [])) {
            this._st.walletsMeta[w.walletId] = {
              name:        w.walletName,
              companyId:   w.companyId,
              companyName: w.companyName,
            };
          }
        }
      } catch (e) {
        this._st.walletLists = [];
      }
      this._renderWalletLists();
      this._renderListPicker();
    },

    // ── List-picker on the Rotina tab ────────────────────────────────
    _renderListPicker() {
      const root = document.getElementById("rp-list-picker");
      if (!root) return;
      if (!this._st.walletLists.length) {
        root.innerHTML = `<p class="text-xs text-gray-300 italic">Nenhuma lista salva — crie em "Seleção de Wallets".</p>`;
        return;
      }
      root.innerHTML = `<div class="flex flex-wrap gap-2">
        ${this._st.walletLists.map(l => {
          const checked = this._st.selectedLists.has(l.name) ? "checked" : "";
          const n = (l.walletIds || []).length;
          return `<label class="inline-flex items-center gap-1.5 border rounded-lg pl-2 pr-3 py-1 cursor-pointer hover:bg-blue-50 text-xs ${checked ? 'bg-blue-50 border-blue-300' : ''}">
            <input type="checkbox" class="rp-list-pick" data-name="${this._esc(l.name)}" ${checked} />
            <span class="font-medium text-gray-800">${this._esc(l.name)}</span>
            <span class="text-[10px] text-gray-500">(${n})</span>
          </label>`;
        }).join("")}
      </div>`;
      root.querySelectorAll(".rp-list-pick").forEach(cb => {
        cb.addEventListener("change", e => {
          const name = e.target.dataset.name;
          if (e.target.checked) this._st.selectedLists.add(name);
          else                  this._st.selectedLists.delete(name);
          this._renderListPicker();   // refresh the highlighted state
          this.runDaily();
        });
      });
    },
    toggleAllLists(on) {
      if (on) {
        this._st.selectedLists = new Set(this._st.walletLists.map(l => l.name));
      } else {
        this._st.selectedLists = new Set();
      }
      this._renderListPicker();
      this.runDaily();
    },
    _renderWalletLists() {
      const body = document.getElementById("rp-wallet-lists-body");
      if (!this._st.walletLists.length) {
        body.innerHTML = `<p class="text-xs text-gray-300 italic">Nenhuma lista salva.</p>`;
        return;
      }
      body.innerHTML = `<div class="flex flex-wrap gap-2">
        ${this._st.walletLists.map((l, i) => {
          const n = (l.walletIds || []).length;
          return `<div class="flex items-center gap-1 border rounded-lg pl-3 pr-1 py-1 bg-blue-50/60">
            <button class="rp-load-list-btn text-left text-xs font-medium text-blue-700 hover:text-blue-900 truncate"
                    data-idx="${i}" title="Carregar lista (substitui a seleção em tela)">
              ${this._esc(l.name)} <span class="ml-1 text-[10px] text-gray-500 font-normal">(${n} carteira${n === 1 ? "" : "s"})</span>
            </button>
            <button class="rp-del-list-btn text-[11px] text-gray-400 hover:text-red-500 px-1.5"
                    data-idx="${i}" title="Excluir lista">&#10005;</button>
          </div>`;
        }).join("")}
      </div>`;

      // Wire up click delegation each render — innerHTML wipes listeners.
      body.querySelectorAll(".rp-load-list-btn").forEach(btn => {
        btn.addEventListener("click", () => this.loadWalletList(Number(btn.dataset.idx)));
      });
      body.querySelectorAll(".rp-del-list-btn").forEach(btn => {
        btn.addEventListener("click", () => this.deleteWalletList(Number(btn.dataset.idx)));
      });
    },
    loadWalletList(idx) {
      const list = this._st.walletLists[idx];
      if (!list) return;
      // Replace the in-memory selection with the loaded list's wallets so
      // the transfer-list lets the operator edit it. Saving with the same
      // name (via "Salvar como lista...") then overwrites the preset.
      this._st.walletsSelected = new Set(list.walletIds || []);
      this._st.loadedListName  = list.name;
      // Hydrate walletsMeta from the embedded enrichment — it was already
      // populated by _loadWalletLists, but loading a stale list could
      // contain orphan ids; we only set what's present.
      for (const w of (list.wallets || [])) {
        this._st.walletsMeta[w.walletId] = {
          name:        w.walletName,
          companyId:   w.companyId,
          companyName: w.companyName,
        };
      }
      this._renderConfigPanes();
      const status = document.getElementById("rp-config-status");
      status.textContent = `Lista "${list.name}" carregada (${list.walletIds.length} carteira(s)). Edite e use "Salvar como lista..." para sobrescrever.`;
    },
    async deleteWalletList(idx) {
      const list = this._st.walletLists[idx];
      if (!list) return;
      if (!confirm(`Excluir a lista "${list.name}"?`)) return;
      try {
        const r = await fetch(
          `/api/repetir-posicoes/wallet-lists?name=${encodeURIComponent(list.name)}`,
          {method: "DELETE"},
        );
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        await this._loadWalletLists();
      } catch (e) {
        alert(`Erro: ${e.message}`);
      }
    },
    openSaveListModal() {
      if (!this._st.walletsSelected.size) {
        alert("Selecione pelo menos uma carteira antes de salvar.");
        return;
      }
      // Pre-fill with the loaded list name so saving overwrites by default
      // — the operator just edited it, the natural intent is "update".
      const input = document.getElementById("rp-save-list-name");
      input.value = this._st.loadedListName || "";
      this._renderSaveListExisting();
      const exists = !!this._st.loadedListName &&
                     this._st.walletLists.some(l => l.name === this._st.loadedListName);
      document.getElementById("rp-save-list-hint").classList.toggle("hidden", !exists);
      document.getElementById("rp-save-list-modal").classList.remove("hidden");
      setTimeout(() => { input.focus(); input.select(); }, 50);
    },
    closeSaveListModal() {
      document.getElementById("rp-save-list-modal").classList.add("hidden");
    },
    _renderSaveListExisting() {
      const ex = document.getElementById("rp-save-list-existing");
      if (!this._st.walletLists.length) {
        ex.innerHTML = '<span class="italic text-gray-300">Nenhuma.</span>';
        return;
      }
      const currentName = (document.getElementById("rp-save-list-name").value || "").trim();
      ex.innerHTML = this._st.walletLists.map(l => {
        const selected = l.name === currentName;
        const tone = selected
          ? "bg-blue-100 border-blue-300 text-blue-700"
          : "bg-gray-50 hover:bg-blue-50 hover:border-blue-200";
        return `<span class="rp-save-list-chip inline-block px-2 py-0.5 border rounded-md cursor-pointer ${tone}"
                       data-name="${this._esc(l.name)}" title="Clique para substituir esta lista">
          ${this._esc(l.name)} <span class="text-gray-400">(${(l.walletIds||[]).length})</span>
        </span>`;
      }).join("");
      ex.querySelectorAll(".rp-save-list-chip").forEach(chip => {
        chip.addEventListener("click", () => {
          const input = document.getElementById("rp-save-list-name");
          input.value = chip.dataset.name;
          input.dispatchEvent(new Event("input"));
          input.focus();
        });
      });
    },
    async saveWalletList() {
      const name = (document.getElementById("rp-save-list-name").value || "").trim();
      if (!name) { alert("Informe o nome da lista."); return; }
      try {
        const r = await fetch("/api/repetir-posicoes/wallet-lists", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            name,
            walletIds: Array.from(this._st.walletsSelected),
          }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this.closeSaveListModal();
        this._st.loadedListName = name;
        // New lists join the daily picker as checked by default — a save
        // is almost always immediately followed by "include this in the
        // routine" intent. Existing lists keep their prior checked state.
        const wasUnknown = !this._st.walletLists.some(l => l.name === name);
        if (wasUnknown) this._st.selectedLists.add(name);
        await this._loadWalletLists();
        // If the saved list is currently checked on the daily picker,
        // re-run /daily so the roster reflects any wallets added/removed.
        if (this._st.selectedLists.has(name)) await this.runDaily();
        const status = document.getElementById("rp-config-status");
        status.textContent = `Lista "${name}" salva (${data.count} carteira(s)).`;
      } catch (e) {
        alert(`Erro: ${e.message}`);
      }
    },

    // ── Daily routine ─────────────────────────────────────────────────
    // Roster = união dos walletIds das listas marcadas no list-picker.
    // Quando nada está marcado, o backend devolve zero linhas.
    // O operador informa uma "Data-fonte" (de onde repetir) no topo; o
    // backend marca, via pre-processing, quais carteiras das listas têm
    // posição processada nessa data (essas ficam elegíveis, com Data-fonte
    // pré-preenchida e Data alvo = próximo dia útil). Sem Data-fonte, as
    // linhas vêm em branco e o operador preenche a origem manualmente por
    // carteira.
    onDailySourceDateChange() { this.runDaily(); },

    async runDaily() {
      const status = document.getElementById("rp-status");
      const lists = Array.from(this._st.selectedLists);
      status.textContent = "Carregando...";
      try {
        const params = new URLSearchParams();
        if (lists.length) params.set("lists", lists.join(","));
        const srcEl = document.getElementById("rp-daily-source-date");
        const srcDate = srcEl ? (srcEl.value || "").trim() : "";
        if (srcDate) params.set("date", srcDate);
        const qs = params.toString();
        const r = await fetch(`/api/repetir-posicoes/daily${qs ? "?" + qs : ""}`);
        const data = await r.json();
        this._st.daily = data.wallets || [];
        // Reset overrides so the server-provided lastDate / suggestedTarget
        // are shown again after a refresh.
        this._st.dailySourceDate = {};
        this._st.dailyTargetDate = {};
        // Hydrate walletsMeta with whatever the roster returned —
        // makes the Txns/Provs wallet pickers work without an extra
        // call when the operator switches tabs.
        for (const w of this._st.daily) {
          this._st.walletsMeta[w.walletId] = {
            name:        w.walletName,
            companyId:   w.companyId,
            companyName: w.companyName,
          };
        }
        const companies = new Set(this._st.daily.map(w => w.companyId).filter(Boolean));
        if (!lists.length) {
          status.textContent = "Marque pelo menos uma lista para carregar carteiras.";
        } else {
          status.textContent = `${this._st.daily.length} carteira(s) em ${companies.size} empresa(s) (${lists.length} lista(s)).`;
        }
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
        this._st.daily = [];
      }
      this._renderDailyTable();
    },

    _renderDailyTable() {
      const tbody = document.getElementById("rp-daily-tbody");
      if (!this._st.daily.length) {
        const msg = this._st.selectedLists.size
          ? `Nenhuma carteira nas listas marcadas.`
          : `Marque pelo menos uma lista acima para carregar carteiras.`;
        tbody.innerHTML = `<tr><td colspan="5" class="rp-tbl-left" style="text-align:center; color:#9ca3af; padding:14px 0">${msg}</td></tr>`;
        return;
      }
      const srcChosen = (document.getElementById("rp-daily-source-date") || {}).value || "";
      tbody.innerHTML = this._st.daily.map(w => {
        const sourceOverride = this._st.dailySourceDate[w.walletId];
        const sourceVal = sourceOverride || w.lastDate || "";
        const eligible = !!sourceVal;
        const target = this._st.dailyTargetDate[w.walletId] || w.suggestedTarget || "";
        // `w.lastDate` is set only when the wallet has a processed position on
        // the chosen Data-fonte. Without it: red hint when a date is chosen but
        // this wallet has no position on it; neutral nudge when no date yet.
        const missingHint = w.lastDate
          ? ""
          : (srcChosen
              ? `<div style="font-size:9px;color:#dc2626;margin-top:2px">sem posição em ${this._esc(srcChosen)}</div>`
              : `<div style="font-size:9px;color:#9ca3af;margin-top:2px">informe a Data-fonte</div>`);
        const lastCell = `<td><input type="date" class="rp-input rp-input-date" data-wid="${this._esc(w.walletId)}" value="${this._esc(sourceVal)}" onchange="Repetir.onSourceChange(this)"/>${missingHint}</td>`;
        const targetCell = eligible
          ? `<td><input type="date" class="rp-input rp-input-date" data-wid="${this._esc(w.walletId)}" value="${this._esc(target)}" onchange="Repetir.onTargetChange(this)"/></td>`
          : `<td><span class="text-gray-300">—</span></td>`;
        // Per-row "Gerar prévia" — preview runs ONE wallet at a time
        // for individual analysis. Disabled when the row has no
        // source date (operator hasn't typed one and there's no
        // server-provided lastDate).
        const actionCell = `<td><button class="rp-btn rp-btn-primary" style="padding:0.25rem 0.55rem; font-size:11px"
          ${eligible ? "" : "disabled"}
          onclick="Repetir.runPreviewFor('${this._esc(w.walletId)}')"
          title="Gera prévia apenas desta carteira.">Gerar prévia</button></td>`;
        const activeCls = this._st.activeWalletId === w.walletId ? "rp-row-changed" : "";
        return `<tr class="${activeCls}">
          <td class="rp-tbl-left">${this._esc(w.companyName || "—")}</td>
          <td class="rp-tbl-left">${this._esc(w.walletName)}</td>
          ${lastCell}
          ${targetCell}
          ${actionCell}
        </tr>`;
      }).join("");
    },

    onSourceChange(el) {
      const wid = el.dataset.wid;
      const val = el.value || "";
      const w = this._st.daily.find(x => x.walletId === wid);
      // Re-render only when eligibility actually flips — typing into a
      // date input fires `change` on each commit and we don't want to
      // steal focus mid-edit.
      const prevEligible = !!(this._st.dailySourceDate[wid] || (w && w.lastDate));
      if (val) this._st.dailySourceDate[wid] = val;
      else delete this._st.dailySourceDate[wid];
      const newEligible = !!(this._st.dailySourceDate[wid] || (w && w.lastDate));
      if (prevEligible !== newEligible) this._renderDailyTable();
    },
    onTargetChange(el) {
      this._st.dailyTargetDate[el.dataset.wid] = el.value;
    },

    // ── Hidden columns config ─────────────────────────────────────────
    // Persists in data/posicao_projetada_columns.json. Loaded once at
    // init; modal updates rewrite the stylesheet so changes take
    // effect immediately on the currently rendered prévia.
    async _loadColumnsConfig() {
      // Stale-server resilience: if the Flask process predates this
      // endpoint (use_reloader=False — new routes need a manual
      // restart), the response is an HTML 404 page. Guard r.ok +
      // content-type so we don't pollute the console with
      // "Unexpected token '<'" and fall back to the built-in default
      // hidden set so UnprocessedId stays hidden until the operator
      // configures it explicitly.
      let hidden = ["unprocessedId", "securityId"];
      try {
        const r = await fetch("/api/repetir-posicoes/columns");
        const ct = (r.headers.get("content-type") || "").toLowerCase();
        if (r.ok && ct.indexOf("application/json") !== -1) {
          const data = await r.json();
          if (data && Array.isArray(data.hiddenColumns)) {
            hidden = data.hiddenColumns;
          }
        } else {
          console.warn(
            "[Repetir] columns endpoint unavailable (status " + r.status +
            "); using built-in defaults — restart the Flask server to enable saving."
          );
        }
      } catch (e) {
        console.warn("[Repetir] _loadColumnsConfig failed:", e);
      }
      this._st.hiddenCols = new Set(hidden);
      this._applyColumnVisibility();
    },
    _applyColumnVisibility() {
      // Builds the dynamic CSS that hides any th/td with a matching
      // `data-col`. Wiping innerHTML and rewriting is cheap and avoids
      // stylesheet bookkeeping.
      const style = document.getElementById("rp-hidden-cols-style");
      if (!style) return;
      const hidden = Array.from(this._st.hiddenCols);
      if (!hidden.length) { style.innerHTML = ""; return; }
      style.innerHTML = hidden
        .map(k => `[data-col="${k.replace(/"/g,'\\"')}"]{display:none !important;}`)
        .join("\n");
    },
    openColumnsModal() {
      const root = document.getElementById("rp-columns-list");
      root.innerHTML = this._COLUMNS.map(c => {
        const checked = this._st.hiddenCols.has(c.key) ? "" : "checked";
        return `<label class="flex items-center gap-2 p-1.5 rounded hover:bg-gray-50">
          <input type="checkbox" data-col-key="${this._esc(c.key)}" ${checked} />
          <span>${this._esc(c.label)}</span>
        </label>`;
      }).join("");
      document.getElementById("rp-columns-status").textContent = "";
      document.getElementById("rp-columns-modal").classList.remove("hidden");
    },
    closeColumnsModal() {
      document.getElementById("rp-columns-modal").classList.add("hidden");
    },

    // ── Filtro "Só com divergência" ────────────────────────────────────
    // Toggle 100% CSS — não re-renderiza a prévia (rerender resetaria
    // os filtros de data dos blocos Transações/Provisões e dispararia
    // refetch). Alterna a classe `rp-only-diffs` no(s) pane(s) e
    // atualiza o rótulo + cor do botão. O CSS esconde
    // `.rp-row-no-diff`, `.rp-row-cash`, `.rp-row-provision`.
    toggleOnlyDiffs() {
      this._st.onlyDiffs = !this._st.onlyDiffs;
      const on = this._st.onlyDiffs;
      document.querySelectorAll(".rp-preview-pane").forEach(p => {
        p.classList.toggle("rp-only-diffs", on);
      });
      // Sincroniza o chip do topbar (label + estado .active).
      const btn = document.getElementById("rp-only-diffs-topbtn");
      const lbl = document.getElementById("rp-only-diffs-label");
      if (lbl) lbl.textContent = on ? "Mostrar todos" : "Só com divergência";
      if (btn) btn.classList.toggle("active", on);
    },
    async saveColumnsConfig() {
      // Collect UNCHECKED keys (operator wants those hidden).
      const hidden = [];
      document.querySelectorAll("#rp-columns-list input[type=checkbox]").forEach(cb => {
        if (!cb.checked) hidden.push(cb.dataset.colKey);
      });
      const status = document.getElementById("rp-columns-status");
      status.textContent = "Salvando...";
      try {
        const r = await fetch("/api/repetir-posicoes/columns", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({hiddenColumns: hidden}),
        });
        const ct = (r.headers.get("content-type") || "").toLowerCase();
        if (ct.indexOf("application/json") === -1) {
          // Stale server: route doesn't exist yet. Apply locally so the
          // operator sees the effect, but warn that persistence needs
          // a restart.
          if (r.status === 404) {
            this._st.hiddenCols = new Set(hidden);
            this._applyColumnVisibility();
            this.closeColumnsModal();
            status.textContent = "";
            console.warn("[Repetir] columns endpoint 404 — applied locally; restart Flask to persist.");
            return;
          }
          throw new Error(`HTTP ${r.status} (resposta não-JSON)`);
        }
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this._st.hiddenCols = new Set(data.hiddenColumns || hidden);
        this._applyColumnVisibility();
        this.closeColumnsModal();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      }
    },

    // ── Logs history ──────────────────────────────────────────────────
    // Reads /api/repetir-posicoes/logs (manifest only — cheap headers)
    // and lets the operator drill into any past run. Clicking a row
    // fetches the full report via /logs/<runId> and hands it off to
    // `_showLogReport`, so the history view and post-run view are the
    // same modal.
    async openLogsList() {
      document.getElementById("rp-logs-modal").classList.remove("hidden");
      await this.refreshLogsList();
    },
    closeLogsList() {
      document.getElementById("rp-logs-modal").classList.add("hidden");
    },
    async refreshLogsList() {
      const root   = document.getElementById("rp-logs-list");
      const status = document.getElementById("rp-logs-status");
      status.textContent = "Carregando...";
      root.innerHTML = `<p class="text-xs text-gray-400 text-center py-4">Carregando...</p>`;
      try {
        const r = await fetch("/api/repetir-posicoes/logs?limit=100");
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        const logs = (data && data.logs) || [];
        if (!logs.length) {
          root.innerHTML = `<p class="text-xs text-gray-400 text-center py-4">Nenhum log encontrado em <code>data/repeat_positions_logs/</code>.</p>`;
          status.textContent = "";
          return;
        }
        status.textContent = `${logs.length} execução(ões)`;
        root.innerHTML = `<table class="rp-tbl">
          <thead><tr>
            <th class="rp-tbl-left">runId / data</th>
            <th class="rp-tbl-left">Empresas</th>
            <th>Carteiras</th>
            <th>Linhas</th>
            <th>Δ saldo</th>
            <th>Status</th>
            <th></th>
          </tr></thead><tbody>
          ${logs.map(l => {
            const t = l.totals || {};
            const totalDiff = Number(t.totalBalanceDiff) || 0;
            const isPreview = (l.mode || "") === "preview";
            // Cor do Δ saldo: prévias usam o sinal só (verde/âmbar/vermelho
            // pela magnitude), porque "uploaded=false" aí é estado natural,
            // não falha. Aplicações falhadas continuam em vermelho.
            const cls = isPreview
              ? (Math.abs(totalDiff) < 0.01 ? "text-green-700"
                : (totalDiff > 0 ? "text-amber-700" : "text-red-700"))
              : (!l.uploaded ? "text-red-700"
                : (Math.abs(totalDiff) < 0.01 ? "text-green-700"
                  : (totalDiff > 0 ? "text-amber-700" : "text-red-700")));
            // Status: 3 estados — prévia (azul, informativo), ok (verde),
            // falhou (vermelho, só pra /apply que tentou subir e errou).
            let statusBadge;
            if (isPreview) {
              statusBadge = '<span class="rp-flag rp-flag-curvaPrice" title="Log gerado por /preview — nenhum upload foi tentado nesse runId">prévia</span>';
            } else if (l.uploaded) {
              statusBadge = '<span class="rp-flag rp-flag-b1Price">ok</span>';
            } else {
              statusBadge = '<span class="rp-flag rp-flag-maturity" title="Upload em /apply foi tentado mas pelo menos um destino falhou">falhou</span>';
            }
            const created = l.createdAt
              ? new Date(l.createdAt).toLocaleString("pt-BR")
              : "";
            const companies = isPreview
              ? "—"
              : ((l.companies || []).join(", ") || "—");
            return `<tr>
              <td class="rp-tbl-left">
                <div style="font-family:ui-monospace,Consolas,monospace;font-size:11px">${this._esc(l.runId || "")}</div>
                <div style="font-size:10px;color:#9ca3af">${this._esc(created)}</div>
              </td>
              <td class="rp-tbl-left" style="font-size:11px">${this._esc(companies)}</td>
              <td>${l.walletCount || 0}</td>
              <td>${l.totalRows || 0}</td>
              <td class="${cls}">${this._fmtMoney(totalDiff)}</td>
              <td>${statusBadge}</td>
              <td class="rp-tbl-left">
                <button class="rp-btn rp-btn-muted" data-rid="${this._esc(l.runId || "")}" onclick="Repetir.openLog(this.dataset.rid)">Abrir</button>
              </td>
            </tr>`;
          }).join("")}
          </tbody></table>`;
      } catch (e) {
        status.textContent = "";
        root.innerHTML = `<p class="text-xs text-red-600 text-center py-4">Erro: ${this._esc(e.message)}</p>`;
      }
    },
    async openLog(runId) {
      if (!runId) return;
      const status = document.getElementById("rp-logs-status");
      const prev = status.textContent;
      status.textContent = `Abrindo ${runId}...`;
      try {
        const r = await fetch(`/api/repetir-posicoes/logs/${encodeURIComponent(runId)}`);
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this.closeLogsList();
        this._showLogReport(data);
      } catch (e) {
        alert("Erro: " + e.message);
        status.textContent = prev;
      }
    },

    // ── Diff log report modal ─────────────────────────────────────────
    // Surfaces the report returned by /apply (both flows). The report
    // contains per-wallet `differences` partitioned into `diverged` and
    // `missingInTarget` kinds — see `_build_diff_report` in
    // pages/repetir_posicoes.py for the contract.
    _showLogReport(report) {
      if (!report) return;
      this._st.lastLogReport = report;
      const t = report.totals || {};
      const isPreview = (report.mode || "") === "preview";
      const head = document.getElementById("rp-log-runid");
      head.textContent = report.runId ? `runId ${report.runId}` : "";
      const status = document.getElementById("rp-log-status");
      const totalDiff = Number(t.totalBalanceDiff) || 0;
      // Em prévia, `uploaded=false` é estado natural — não dá pra ler
      // como falha. Mostra Δ saldo total + um aviso "(prévia)" pra
      // operadora entender o contexto.
      const cls = isPreview
        ? (Math.abs(totalDiff) < 0.01 ? "text-green-700" : (totalDiff > 0 ? "text-amber-700" : "text-red-700"))
        : (report.uploaded
            ? (Math.abs(totalDiff) < 0.01 ? "text-green-700" : (totalDiff > 0 ? "text-amber-700" : "text-red-700"))
            : "text-red-700");
      status.className = `text-[11px] ml-auto ${cls}`;
      status.textContent = isPreview
        ? `Δ saldo total ${this._fmtMoney(totalDiff)} (prévia)`
        : (report.uploaded
            ? `Δ saldo total ${this._fmtMoney(totalDiff)}`
            : "Falha no envio");

      const sum = document.getElementById("rp-log-summary");
      const footerLine = isPreview
        ? `<div class="mt-1 text-[11px] text-gray-500">Log gerado por <code>/preview</code> — nenhum upload foi tentado neste runId.</div>`
        : `<div class="mt-1 text-[11px] text-gray-500">Total enviado: ${report.totalRows || 0} linha(s) em ${(report.uploads || []).length} arquivo(s).</div>`;
      sum.innerHTML = `
        <div class="flex flex-wrap gap-3">
          <span><strong>${t.wallets || 0}</strong> carteira(s) (${t.withTarget || 0} com processedPosition na data atual)</span>
          <span><strong class="text-green-700">${t.matched || 0}</strong> ok</span>
          <span><strong class="text-amber-700">${t.diverged || 0}</strong> divergente(s)</span>
          <span><strong class="text-blue-700">${t.missingInTarget || 0}</strong> ausente(s) na atual</span>
          <span><strong class="text-purple-700">${t.missingInRepetition || 0}</strong> ausente(s) na repetição</span>
        </div>
        ${footerLine}
      `;

      const body = document.getElementById("rp-log-body");
      const wallets = report.wallets || [];
      if (!wallets.length) {
        body.innerHTML = `<p class="text-xs text-gray-400 text-center py-4">Sem carteiras no relatório.</p>`;
      } else {
        body.innerHTML = wallets.map(w => this._renderLogWallet(w)).join("");
      }

      document.getElementById("rp-log-modal").classList.remove("hidden");
    },

    _renderLogWallet(w) {
      const s = w.summary || {};
      const diffs = w.differences || [];
      const hasTarget = !!s.hasTarget;
      const headerNote = hasTarget
        ? `<span class="text-[11px] text-gray-500">${s.matched || 0} ok · ${s.diverged || 0} div · ${s.missingInTarget || 0} a+ · ${s.missingInRepetition || 0} a− · Δ saldo ${this._fmtMoney(s.totalBalanceDiff || 0)}</span>`
        : `<span class="text-[11px] text-gray-300 italic">sem processedPosition na data atual</span>`;
      const provHTML = (w.provisions != null && Number(w.provisions) !== 0)
        ? `<span class="rp-prov-chip">Provisões: ${this._fmtMoney(w.provisions)}</span>`
        : "";
      const cash = w.cash || {};
      const cashHTML = `<span class="text-[11px] text-gray-400">Caixa: ${this._fmtMoney(cash.former)} ${cash.delta != null ? (Number(cash.delta) >= 0 ? "+" : "") + this._fmtMoney(cash.delta) : ""} → <strong>${this._fmtMoney(cash.new)}</strong></span>`;

      // ── Bloco-resumo estruturado (pedido do operador) ────────────
      // 3 indicadores curtos no topo da carteira: caixa (ok/diverge),
      // contagem de divergências em ativos, contagem de transações
      // órfãs. O detalhamento completo (tabela de diffs, lista de
      // órfãs) vem abaixo. Os campos `cashStatus`, `differencesCount`
      // e `orphanCount` são populados pelo backend em
      // `_build_diff_report` — defaults locais cobrem payloads antigos.
      const cashStatus = w.cashStatus ||
        (cash.target == null ? "noTarget"
         : (Math.abs(Number(cash.targetDelta) || 0) < 0.01 ? "ok" : "diverge"));
      const cashStatusHTML = (() => {
        if (cashStatus === "ok") {
          return `<span class="rp-flag rp-flag-b1Price" title="Caixa projetado bate com cashAccounts na data atual (|Δ| < R$ 0,01)">caixa ok</span>`;
        }
        if (cashStatus === "noTarget") {
          return `<span class="rp-flag rp-flag-priceUnchanged" title="Sem cashAccounts na data atual">caixa: sem atual</span>`;
        }
        const td = Number(cash.targetDelta || 0);
        const sig = td >= 0 ? "+" : "";
        return `<span class="rp-flag rp-flag-missingB1Price" title="Caixa projetado − cashAccounts(data atual) = ${this._fmtMoney(td)}">caixa diverge ${sig}${this._fmtMoney(td)}</span>`;
      })();
      const diffCount = (w.differencesCount != null) ? w.differencesCount : diffs.length;
      const orphans   = w.orphanTransactions || [];
      const orphanCount = (w.orphanCount != null) ? w.orphanCount : orphans.length;
      const diffCountCls = diffCount > 0 ? "rp-flag-buysell" : "rp-flag-priceUnchanged";
      const orphanCountCls = orphanCount > 0 ? "rp-flag-missingB1Price" : "rp-flag-priceUnchanged";
      const summaryBoxHTML = `
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px 0">
          ${cashStatusHTML}
          <span class="rp-flag ${diffCountCls}" title="Ativos com divergência (rep ≠ target) ≥ R$ 0,01 de impacto">${diffCount} divergência(s) em ativos</span>
          <span class="rp-flag ${orphanCountCls}" title="Transações sem amountDifference na posição nem provisão correspondente">${orphanCount} transação(ões) órfã(s)</span>
        </div>`;

      // Lista de órfãs (renderizada inline quando há). Exibe tipo,
      // direção (sub/red), offset, balance, problema. Operadora usa
      // como ponto de partida para cadastrar provisão ou ajustar
      // amountDifference upstream.
      let orphanRowsHTML = "";
      if (orphans.length) {
        orphanRowsHTML = `<div style="overflow-x:auto;margin-top:6px">
          <table class="rp-tbl">
            <thead><tr>
              <th class="rp-tbl-left">Liquidação</th>
              <th class="rp-tbl-left">Tipo</th>
              <th class="rp-tbl-left">Direção</th>
              <th>Offset</th>
              <th class="rp-tbl-left">Ativo</th>
              <th>Saldo</th>
              <th class="rp-tbl-left">Problema</th>
            </tr></thead>
            <tbody>
              ${orphans.map(o => `<tr class="rp-row-qty-mismatch">
                <td class="rp-tbl-left">${this._esc(o.liquidationDate || "")}</td>
                <td class="rp-tbl-left">${this._esc(o.type || "")}</td>
                <td class="rp-tbl-left">${this._esc(o.direction || "—")}</td>
                <td>${o.offset != null ? `D${o.offset >= 0 ? "+" : ""}${o.offset}` : "—"}</td>
                <td class="rp-tbl-left" title="${this._esc(o.description || "")}">${this._esc(o.securityName || o.securityId || "—")}</td>
                <td>${this._fmtMoney(o.balance)}</td>
                <td class="rp-tbl-left text-[11px]">${this._esc(o.problem || "")}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>`;
      }

      let rows = "";
      if (!diffs.length) {
        rows = `<p class="text-[11px] text-gray-400 italic">Sem divergências.</p>`;
      } else {
        rows = `<div style="overflow-x:auto"><table class="rp-tbl">
          <thead><tr>
            <th class="rp-tbl-left">Tipo</th>
            <th class="rp-tbl-left">Ativo</th>
            <th>Qtd projetada</th>
            <th>PU projetada</th>
            <th>Saldo projetado</th>
            <th>Qtd atual</th>
            <th>PU atual</th>
            <th>Saldo atual</th>
            <th class="rp-tbl-left">Diff (projetada − atual)</th>
          </tr></thead><tbody>
          ${diffs.map(d => {
            const rep = d.repetition || {};
            const tp  = d.targetProcessed || {};
            const df  = d.diff || {};
            const kindHTML = d.kind === "diverged"
              ? `<span class="rp-flag rp-flag-buysell">diverged</span>`
              : (d.kind === "missingInTarget"
                  ? `<span class="rp-flag rp-flag-targetPrice">missing-target</span>`
                  : `<span class="rp-flag rp-flag-priceUnchanged">missing-rep</span>`);
            const sldClass = (v, tol) => {
              const n = Number(v) || 0;
              if (Math.abs(n) < tol) return "rp-diff-zero";
              return n > 0 ? "rp-diff-pos" : "rp-diff-neg";
            };
            const diffCell = (df && df.balance != null)
              ? `<div class="${sldClass(df.quantity, 1e-6)}">qtd: ${this._fmtQty(df.quantity)}</div>
                 <div class="${sldClass(df.pu, 1e-6)}">pu: ${this._fmtPu(df.pu)}</div>
                 <div class="${sldClass(df.balance, 0.01)}">sld: ${this._fmtMoney(df.balance)}</div>`
              : `<span class="rp-diff-na">—</span>`;
            return `<tr>
              <td class="rp-tbl-left">${kindHTML}</td>
              <td class="rp-tbl-left">${this._esc(d.securityName || d.securityId || "")}<div style="font-size:9px;color:#9ca3af;font-family:ui-monospace,Consolas,monospace">${this._esc(d.unprocessedId || d.securityId || "")}</div></td>
              <td>${this._fmtQty(rep.quantity)}</td>
              <td>${this._fmtPu(rep.pu)}</td>
              <td>${this._fmtMoney(rep.balance)}</td>
              <td>${tp.quantity != null ? this._fmtQty(tp.quantity) : '<span class="rp-diff-na">—</span>'}</td>
              <td>${tp.pu != null ? this._fmtPu(tp.pu) : '<span class="rp-diff-na">—</span>'}</td>
              <td>${tp.balance != null ? this._fmtMoney(tp.balance) : '<span class="rp-diff-na">—</span>'}</td>
              <td class="rp-tbl-left">${diffCell}</td>
            </tr>`;
          }).join("")}
          </tbody></table></div>`;
      }

      return `<div style="border:1px solid #e5e7eb; border-radius:6px; padding:10px 12px">
        <div style="display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin-bottom:6px">
          <strong class="text-sm text-gray-800">${this._esc(w.walletName || w.walletId)}</strong>
          <span class="text-[10px] text-gray-400">[${this._esc(w.companyName || w.companyId || "")}]</span>
          <span class="text-[11px] text-gray-500">${this._esc(w.sourceDate)} → <strong>${this._esc(w.targetDate)}</strong></span>
          ${provHTML}
          ${headerNote}
          <span class="ml-auto">${cashHTML}</span>
        </div>
        ${summaryBoxHTML}
        ${orphanRowsHTML}
        ${rows}
      </div>`;
    },

    closeLogModal() {
      document.getElementById("rp-log-modal").classList.add("hidden");
    },

    downloadLog() {
      const report = this._st.lastLogReport;
      if (!report) return;
      const blob = new Blob([JSON.stringify(report, null, 2)],
                            {type: "application/json"});
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = `${report.runId || "repeat_log"}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },

    // ── Direct apply (no preview) ─────────────────────────────────────
    // Temporarily disabled (see button comment in the markup). The
    // function stays in the module so re-enabling is a one-line change
    // — when the page reaches a single-wallet model, this should also
    // operate on `activeWalletId` rather than the old bulk selection.
    async runDirect() {
      const wid = this._st.activeWalletId;
      if (!wid) return;
      const w = this._st.daily.find(x => x.walletId === wid);
      if (!w) return;
      const source = this._st.dailySourceDate[wid] || w.lastDate;
      const target = this._st.dailyTargetDate[wid] || w.suggestedTarget;
      if (!source || !target) return;
      const items = [{walletId: wid, sourceDate: source, targetDate: target}];
      if (!confirm(`Executar repetição direta para ${w.walletName} sem gerar prévia? O envio é imediato.`)) return;

      const status  = document.getElementById("rp-preview-status");
      const btn     = document.getElementById("rp-run-direct-btn");
      btn.disabled = true;
      const prevLabel = btn.textContent;
      btn.textContent = "Enviando...";
      status.textContent = "Aplicando direto (sem prévia)...";
      try {
        const r = await fetch("/api/repetir-posicoes/apply", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({items}),
        });
        const data = await r.json();
        if (!r.ok || !data.uploaded) {
          const fails = (data.uploads || []).filter(u => u.status !== "ok");
          const msg = fails.length
            ? fails.map(f => `${f.companyName || f.companyId}: ${f.error || f.status}`).join("\n")
            : (data.error || `HTTP ${r.status}`);
          status.textContent = "Erro no envio direto.";
          alert("Erro no envio:\n" + msg);
          // Even on failure, show whatever diff report we got — the
          // operator may have partial uploads or want to inspect the
          // computed positions before retrying.
          if (data.diffReport) this._showLogReport(data.diffReport);
        } else {
          const total   = data.totalRows ?? 0;
          const uploads = data.uploads || [];
          status.textContent = `${uploads.length} arquivo(s) enviado(s). Total: ${total} linha(s).`;
          // Direct path: no preview was rendered, so the log modal is
          // the operator's only visibility into what diverged from the
          // target processedPosition. Auto-open instead of alerting.
          if (data.diffReport) {
            this._showLogReport(data.diffReport);
          } else {
            alert(`${uploads.length} arquivo(s) enviado(s).\nTotal: ${total} linha(s).`);
          }
        }
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      } finally {
        btn.disabled = false;
        btn.textContent = prevLabel;
      }
    },

    // ── Preview (single wallet) ───────────────────────────────────────
    // The page runs a prévia for **one wallet at a time** — the
    // per-row button calls `runPreviewFor(walletId)`. The result panel
    // below the roster is replaced per click; the operator can switch
    // wallets without losing the active-wallet highlight on the table.
    async runPreviewFor(walletId) {
      const w = this._st.daily.find(x => x.walletId === walletId);
      if (!w) return;
      const source = this._st.dailySourceDate[walletId] || w.lastDate;
      const target = this._st.dailyTargetDate[walletId] || w.suggestedTarget;
      if (!source) { alert("Defina a Última posição antes de gerar a prévia."); return; }
      if (!target) { alert("Defina a Data atual antes de gerar a prévia.");      return; }

      this._st.activeWalletId = walletId;
      this._renderDailyTable();    // highlights the active row
      // Hide the list-picker and the wallet roster — the operator is
      // now focused on the active wallet's prévia. "Voltar para a
      // seleção" (top action bar) restores them.
      this._togglePreviewMode(true);

      const status = document.getElementById("rp-preview-status");
      status.textContent = `Gerando prévia para ${w.walletName}...`;
      // Carteiras vindas do atalho "Carteiras da empresa" (`clInjectedWallets`)
      // não estão registradas em walletLists — passamos `adhoc: true`
      // pro backend pular essa checagem (mantém `company_visible`).
      const isAdhoc = !!(this._st.clInjectedWallets && this._st.clInjectedWallets.has(walletId));
      try {
        const r = await fetch("/api/repetir-posicoes/preview", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            items: [{walletId, sourceDate: source, targetDate: target}],
            adhoc: isAdhoc,
          }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this._st.preview = data.results || [];
        this._st.puOverrides = {};
        // Toda prévia nova nasce filtrada para "Só com divergência" — o
        // operador raramente quer olhar a lista inteira logo de cara, e
        // tem o botão "Mostrar todos" no header se precisar. Resetar a
        // cada load (em vez de só na primeira) garante o default mesmo
        // depois de alternar para "Mostrar todos" numa prévia anterior.
        this._st.onlyDiffs = true;
        // Guarda o log da prévia (gerado e persistido pelo backend) —
        // assim o botão "Log" mostra o relatório atual sem precisar
        // de uma rodada de /apply primeiro. `runId` começa com
        // `preview_*` pra distinguir de `repeat_*` (apply).
        if (data.diffReport) {
          this._st.lastLogReport = data.diffReport;
        }
        status.textContent = this._st.preview.length
          ? `Prévia carregada — ${w.walletName}.`
          : `Sem dados para ${w.walletName}.`;
        this._renderPreview();
        // Os botões `rp-b1-btn` e `rp-confirm-btn` agora ficam no
        // header dinâmico da prévia (criados pelo `_renderPreview()`).
        // Os toggles abaixo são defensivos — só rodam se os elementos
        // existirem (i.e., a prévia foi efetivamente renderizada).
        const b1Btn = document.getElementById("rp-b1-btn");
        const cfBtn = document.getElementById("rp-confirm-btn");
        if (b1Btn) b1Btn.disabled = !this._st.preview.length;
        if (cfBtn) cfBtn.disabled = !this._st.preview.length;
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      }
    },

    _togglePreviewMode(on) {
      // on=true  → esconde picker + roster + conciliação (prévia tomou a tela)
      //            e mostra o botão "Voltar" no header principal.
      // on=false → restaura tudo e esconde o "Voltar".
      const picker      = document.getElementById("rp-picker-section");
      const roster      = document.getElementById("rp-roster-section");
      const conciliacao = document.getElementById("rp-conciliacao-section");
      const backBtn     = document.getElementById("rp-back-btn");
      if (picker)      picker.classList.toggle("hidden", !!on);
      if (roster)      roster.classList.toggle("hidden", !!on);
      if (conciliacao) conciliacao.classList.toggle("hidden", !!on);
      // `rp-back-btn` agora é estático no topbar — controlamos visibilidade
      // aqui (oposto do roster/picker: aparece quando prévia abre).
      if (backBtn)     backBtn.classList.toggle("hidden", !on);
    },

    closePreview() {
      // Wipe preview state and restore the picker + roster.
      this._st.activeWalletId = null;
      this._st.preview        = [];
      this._st.puOverrides    = {};
      const out = document.getElementById("rp-preview-results");
      if (out) out.innerHTML = "";
      // Os botões `rp-b1-btn`/`rp-confirm-btn`/`rp-back-btn` viviam no
      // topbar antes — agora ficam dentro do `rp-preview-results` e
      // são removidos juntos quando wipamos o HTML acima. Guards
      // mantidos pra tolerar refactors futuros.
      const b1Btn = document.getElementById("rp-b1-btn");
      const cfBtn = document.getElementById("rp-confirm-btn");
      if (b1Btn) b1Btn.disabled = true;
      if (cfBtn) cfBtn.disabled = true;
      const status = document.getElementById("rp-preview-status");
      if (status) status.textContent = "";
      // Remove carteiras "fantasma" injetadas via `resultsRunPreview`
      // (atalho "Carteiras da empresa") — só estavam ali pra dar
      // suporte ao `runPreviewFor`. Sem isso, o roster ficaria com
      // entradas que o operador não selecionou conscientemente.
      if (this._st.clInjectedWallets && this._st.clInjectedWallets.size) {
        const inj = this._st.clInjectedWallets;
        this._st.daily = (this._st.daily || []).filter(x => !inj.has(x.walletId));
        inj.clear();
      }
      this._togglePreviewMode(false);
      this._renderDailyTable();    // clears the active-row highlight
    },

    // ── Atalho "Carteiras da empresa" ─────────────────────────────────
    // Lista as carteiras do `/results` (walletsWithNavDetailed) da empresa
    // na data escolhida, via /api/repetir-posicoes/results-wallets. SEM
    // conciliação/divergência: mostra TODAS as carteiras que o /results
    // devolve. Cada uma tem posição processada na data, então clicar dispara
    // a prévia com source = data selecionada e target = próximo dia útil
    // (semântica nativa da Posição Projetada). Reusa `runPreviewFor`.
    resultsOnCompanyChange() {
      // Esconde a tabela ao trocar de empresa; recarrega se já houver data.
      const wEl = document.getElementById("rp-cl-wallets");
      if (wEl) wEl.classList.add("hidden");
      this.resultsLoadWallets();
    },

    async resultsLoadWallets() {
      const cidEl = document.getElementById("rp-cl-company");
      const dtEl  = document.getElementById("rp-cl-date");
      const stEl  = document.getElementById("rp-cl-status");
      const wWrap = document.getElementById("rp-cl-wallets");
      const wBody = document.getElementById("rp-cl-wallets-tbody");
      const wCnt  = document.getElementById("rp-cl-wallets-count");
      const cid  = cidEl ? cidEl.value : "";
      const date = dtEl ? (dtEl.value || "").trim() : "";
      if (!cid || !date) {
        if (wWrap) wWrap.classList.add("hidden");
        if (stEl) stEl.textContent = (cid && !date) ? "Informe a data." : "";
        return;
      }
      if (stEl) stEl.textContent = "Carregando carteiras...";
      try {
        const qs = new URLSearchParams({companyId: cid, date});
        const r = await fetch(`/api/repetir-posicoes/results-wallets?${qs}`);
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        const rows = data.wallets || [];
        if (!rows.length) {
          wBody.innerHTML = `<tr><td colspan="5" class="rp-tbl-left" style="text-align:center; color:#9ca3af; padding:12px 0">Nenhuma carteira com resultado de NAV em ${this._esc(date)}.</td></tr>`;
          if (wCnt) wCnt.textContent = "(0)";
          wWrap.classList.remove("hidden");
          if (stEl) stEl.textContent = "";
          return;
        }
        const target = this._nextBizDayISO(date);   // alvo = próximo dia útil
        wBody.innerHTML = rows.map(r => {
          const navHTML  = (r.nav         != null) ? this._fmtMoney(r.nav)        : '<span class="rp-diff-na">—</span>';
          const cotaHTML = (r.navPerShare != null) ? this._fmtNum(r.navPerShare, 8) : '<span class="rp-diff-na">—</span>';
          const qtyHTML  = (r.amount      != null) ? this._fmtNum(r.amount, 6)    : '<span class="rp-diff-na">—</span>';
          return `<tr>
            <td class="rp-tbl-left">${this._esc(r.walletName || r.walletId)}</td>
            <td style="text-align:right">${navHTML}</td>
            <td style="text-align:right">${cotaHTML}</td>
            <td style="text-align:right">${qtyHTML}</td>
            <td style="text-align:center">
              <button type="button" class="rp-btn rp-btn-primary"
                      data-wid="${this._esc(r.walletId)}"
                      data-wname="${this._esc(r.walletName || r.walletId)}"
                      data-sd="${this._esc(date)}"
                      data-td="${this._esc(target)}"
                      data-cid="${this._esc(cid)}"
                      onclick="Repetir.resultsRunPreview(this.dataset.wid, this.dataset.wname, this.dataset.sd, this.dataset.td, this.dataset.cid)">
                Gerar prévia
              </button>
            </td>
          </tr>`;
        }).join("");
        if (wCnt) wCnt.textContent = `(${rows.length})`;
        wWrap.classList.remove("hidden");
        if (stEl) stEl.textContent = "";
      } catch (e) {
        wBody.innerHTML = `<tr><td colspan="5" class="rp-tbl-left" style="text-align:center; color:#dc2626; padding:12px 0">Erro: ${this._esc(e.message)}</td></tr>`;
        wWrap.classList.remove("hidden");
        if (stEl) stEl.textContent = "";
      }
    },

    async resultsRunPreview(walletId, walletName, sourceDate, targetDate, companyId) {
      if (!walletId || !sourceDate || !targetDate) return;
      // Garante que `walletsMeta` tem o `companyId` da carteira pra que
      // os blocos CRUD (transações/provisões) consigam montar as URLs.
      // Idem `daily` pra que o highlight do roster não quebre se a
      // carteira já estiver lá; quando não está, adicionamos um stub
      // mínimo (será removido no `closePreview` → `runDaily`).
      this._st.walletsMeta = this._st.walletsMeta || {};
      this._st.walletsMeta[walletId] = {companyId, walletName};
      this._st.clInjectedWallets = this._st.clInjectedWallets || new Set();
      const existing = (this._st.daily || []).find(x => x.walletId === walletId);
      if (!existing) {
        this._st.daily = (this._st.daily || []).concat([{
          walletId, walletName, companyId,
          lastDate: sourceDate, suggestedTarget: targetDate,
        }]);
        // Marcamos a injeção pra que `closePreview` remova essa linha
        // do roster (evita "linha fantasma" aparecer junto com as
        // carteiras da rotina diária).
        this._st.clInjectedWallets.add(walletId);
      }
      // Seta as datas usadas pelo runPreviewFor.
      this._st.dailySourceDate = this._st.dailySourceDate || {};
      this._st.dailyTargetDate = this._st.dailyTargetDate || {};
      this._st.dailySourceDate[walletId] = sourceDate;
      this._st.dailyTargetDate[walletId] = targetDate;
      // Dispara a prévia pela rotina existente — sem duplicar lógica
      // de status/render. `runPreviewFor` esconde picker/roster (e a
      // própria seção via `_togglePreviewMode`).
      this.runPreviewFor(walletId);
    },


    // ── Incluir preços B1 ─────────────────────────────────────────────
    // Iterates the current preview, builds the (securityId, targetDate)
    // batch, POSTs /b1-prices, and overlays the returned PUs onto each
    // matching row. Skips rows already overridden by curva (the curva
    // rule wins by design — it represents an HTM lot-level calculation
    // whereas B1 is the upstream's daily quote). Rows with no B1 entry
    // for the targetDate get a `missingB1Price` flag so the operator
    // can audit gaps before confirming.
    async includeB1Prices() {
      if (!this._st.preview.length) return;
      const btn = document.getElementById("rp-b1-btn");
      const status = document.getElementById("rp-preview-status");

      // Collect every (securityId, targetDate) we care about. Curva rows
      // are excluded — the curva PU already won the rule chain and the
      // B1 override would silently undo it.
      const items = [];
      const seen  = new Set();
      for (const p of this._st.preview) {
        for (const r of (p.rows || [])) {
          if ((r.flags || []).includes("curvaPrice")) continue;
          const key = `${r.securityId}|${p.targetDate}`;
          if (seen.has(key)) continue;
          seen.add(key);
          items.push({securityId: r.securityId, targetDate: p.targetDate});
        }
      }
      if (!items.length) {
        status.textContent = "Sem ativos elegíveis para preços B1.";
        return;
      }

      btn.disabled = true;
      status.textContent = "Buscando preços B1...";
      try {
        const r = await fetch("/api/repetir-posicoes/b1-prices", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({items}),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        const prices = (data && data.prices) || {};

        // Apply overrides into the preview rows + puOverrides map. The
        // map is keyed by `walletId|securityId|targetDate` because /apply
        // applies PU overrides per wallet (a single security across
        // wallets shares the same B1 PU, but the override map is wallet-
        // scoped for symmetry with the other rules).
        let applied = 0, missing = 0;
        for (const p of this._st.preview) {
          for (const r of (p.rows || [])) {
            if ((r.flags || []).includes("curvaPrice")) continue;
            const hit = prices[`${r.securityId}|${p.targetDate}`];
            // Drop any prior B1 marker so re-clicking the button after a
            // partial fix doesn't accumulate stale flags.
            r.flags = (r.flags || []).filter(f => f !== "b1Price" && f !== "missingB1Price");
            if (hit && hit.pu != null) {
              const pu = Number(hit.pu);
              if (isFinite(pu)) {
                r.repetition.pu      = pu;
                r.repetition.balance = Math.round(pu * (Number(r.repetition.quantity) || 0) * 100) / 100;
                r.flags.push("b1Price");
                this._st.puOverrides[`${p.walletId}|${r.securityId}|${p.targetDate}`] = pu;
                applied++;
                continue;
              }
            }
            r.flags.push("missingB1Price");
            missing++;
          }
        }
        status.textContent = `${applied} PU(s) B1 aplicado(s)${missing ? `, ${missing} sem cotação` : ""}.`;
        this._renderPreview();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      } finally {
        btn.disabled = !this._st.preview.length;
      }
    },

    _flagBadges(flags) {
      if (!flags || !flags.length) return "";
      // `buySell` removido da coluna Flags — a presença de transações
      // já é evidente pela coluna "Transação" (com saldo + tooltip de
      // descrição). Operador queria menos ruído visual na Flags.
      const HIDDEN = new Set(["buySell"]);
      return flags
        .filter(f => !HIDDEN.has(f))
        .map(f =>
          `<span class="rp-flag rp-flag-${this._esc(f)}">${this._esc(f)}</span>`
        ).join("");
    },

    _rowClass(row, walletHasTarget) {
      const classes = [];
      // Qty-mismatch vs target processedPosition wins visually — it's
      // the signal the operator asked for and supersedes the
      // "rep changed source" highlight (which is informational only).
      if (row.quantityMismatch) classes.push("rp-row-qty-mismatch");
      if ((row.flags || []).includes("maturity")) classes.push("rp-row-matured");
      else if (row.isNew) classes.push("rp-row-new");
      else {
        const o = row.original || {}, p = row.repetition || {};
        const changedQty = (Number(o.quantity) || 0) !== (Number(p.quantity) || 0);
        const changedPu  = (Number(o.pu)       || 0) !== (Number(p.pu)       || 0);
        if (changedQty || changedPu) classes.push("rp-row-changed");
      }
      // hasDivergence vs target — régua unificada de **impacto em $**
      // de ao menos 1 centavo. Cada componente (qty, pu, saldo) é
      // convertido em dinheiro antes da comparação. Quando a carteira
      // não tem target (walletHasTarget=false), não marca nada como
      // diff (senão o filtro esconderia tudo).
      const d = row.diff || {};
      const tp = row.targetProcessed || {};
      const repPu  = Number((row.repetition && row.repetition.pu)       || 0);
      const repQty = Number((row.repetition && row.repetition.quantity) || 0);
      const puRefR  = Math.max(Math.abs(repPu),  Math.abs(Number(tp.pu)       || 0));
      const qtyRefR = Math.max(Math.abs(repQty), Math.abs(Number(tp.quantity) || 0));
      const qImp = d.qtyImpact     != null ? Math.abs(Number(d.qtyImpact))     : Math.abs(Number(d.quantity) || 0) * puRefR;
      const pImp = d.puImpact      != null ? Math.abs(Number(d.puImpact))      : Math.abs(Number(d.pu)       || 0) * qtyRefR;
      const bImp = d.balanceImpact != null ? Math.abs(Number(d.balanceImpact)) : Math.abs(Number(d.balance)  || 0);
      const hasNumericDiff = (qImp >= 0.01) || (pImp >= 0.01) || (bImp >= 0.01);
      // "missing in target" só vira divergência quando o saldo da
      // repetição é ≥ 1 centavo — mesma lógica do backend.
      const repBalAbs = Math.abs(Number((row.repetition && row.repetition.balance) || 0));
      const missingInTarget = walletHasTarget && !row.targetProcessed && repBalAbs >= 0.01;
      // Provisão pendente conta como divergência — o ativo está
      // descasado entre posição (alvo) e caixa (sem buySell/provisão
      // real). Operador precisa ver essa row no filtro "Só com
      // divergência" pra criar a provisão correspondente.
      const hasExpectedProv = !!row.expectedProvision;
      const hasDivergence = !!row.quantityMismatch || hasNumericDiff || missingInTarget || hasExpectedProv;
      if (hasDivergence) classes.push("rp-row-has-diff");
      else classes.push("rp-row-no-diff");
      return classes.join(" ");
    },

    _renderPreview() {
      const out = document.getElementById("rp-preview-results");
      if (!this._st.preview.length) {
        out.innerHTML = `<p class="text-xs text-gray-400 text-center py-6">Sem prévia disponível.</p>`;
        return;
      }
      out.innerHTML = this._st.preview.map((p, pi) => {
        const walletHasTarget = !!(p.targetSummary && p.targetSummary.hasTarget);
        const rows = (p.rows || []).map((r, ri) => {
          const cls = this._rowClass(r, walletHasTarget);
          const isTarget = (r.flags || []).includes("targetPrice");
          const isCurva  = (r.flags || []).includes("curvaPrice");
          const isB1     = (r.flags || []).includes("b1Price");
          // Wrap the repetição PU cell in a pill when the value was
          // overridden by:
          //  • targetPrice (RULE_TARGET_PROCESSED_PRICE — fuchsia, wins
          //    over all others; PU came from processedPosition at target)
          //  • curva (RULE_CURVA_PRICE — blue)
          //  • B1 button (securityPrices.historyPrice — green)
          // Precedence is target > curva > B1, mirroring the rule chain.
          let puRepHTML;
          if (isTarget) {
            puRepHTML = `<span class="rp-target-cell" title="PU obtido da processedPosition na data atual">${this._fmtPu(r.repetition.pu)}<span class="rp-target-tag">ATUAL</span></span>`;
          } else if (isCurva) {
            puRepHTML = `<span class="rp-curva-cell" title="PU obtido da página 'Preços na Curva'">${this._fmtPu(r.repetition.pu)}<span class="rp-curva-tag">CURVA</span></span>`;
          } else if (isB1) {
            puRepHTML = `<span class="rp-b1-cell" title="PU obtido de securityPrices.historyPrice (type=B1, data atual exata)">${this._fmtPu(r.repetition.pu)}<span class="rp-b1-tag">B1</span></span>`;
          } else {
            puRepHTML = this._fmtPu(r.repetition.pu);
          }
          // Diff cell — repetição − processedPosition(target). Apenas
          // as linhas com diferença efetiva são renderizadas (qtd, pu
          // ou saldo fora da tolerância). Se as três estão zeradas,
          // mostra um dash discreto — operador só presta atenção em
          // linha quando há divergência real.
          const diff  = r.diff;
          const tp    = r.targetProcessed;
          let diffHTML;
          if (!tp || diff == null) {
            diffHTML = `<span class="rp-diff-na" title="Sem processedPosition para esta security na data atual">—</span>`;
          } else {
            // Régua unificada: impacto em $ ≥ 1 centavo por componente.
            // Backend já calcula qtyImpact / puImpact / balanceImpact
            // (em $); frontend tem fallback caso o response venha sem
            // esses campos (versão antiga).
            const CENT = 0.01;
            const cls = (v) => (Number(v) > 0 ? "rp-diff-pos" : "rp-diff-neg");
            const repPu  = Number((r.repetition && r.repetition.pu) || 0);
            const repQty = Number((r.repetition && r.repetition.quantity) || 0);
            const tgtPu  = Number(tp.pu || 0);
            const tgtQty = Number(tp.quantity || 0);
            const puRef  = Math.max(Math.abs(repPu),  Math.abs(tgtPu));
            const qtyRef = Math.max(Math.abs(repQty), Math.abs(tgtQty));
            const qtyImpact = diff.qtyImpact     != null ? Math.abs(Number(diff.qtyImpact))
                                                         : Math.abs(Number(diff.quantity) || 0) * puRef;
            const puImpact  = diff.puImpact      != null ? Math.abs(Number(diff.puImpact))
                                                         : Math.abs(Number(diff.pu)       || 0) * qtyRef;
            const balImpact = diff.balanceImpact != null ? Math.abs(Number(diff.balanceImpact))
                                                         : Math.abs(Number(diff.balance)  || 0);
            const parts = [];
            if (qtyImpact >= CENT) {
              parts.push(`<div class="${cls(diff.quantity)}" title="Impacto em $: ${this._fmtMoney(qtyImpact)}">qtd: ${this._fmtQty(diff.quantity)}</div>`);
            }
            if (puImpact >= CENT) {
              parts.push(`<div class="${cls(diff.pu)}" title="Impacto em $: ${this._fmtMoney(puImpact)}">pu: ${this._fmtPu(diff.pu)}</div>`);
            }
            if (balImpact >= CENT) {
              parts.push(`<div class="${cls(diff.balance)}">sld: ${this._fmtMoney(diff.balance)}</div>`);
            }
            if (!parts.length) {
              diffHTML = `<span class="rp-diff-zero" title="Posição projetada bate com o processedPosition da data atual (impacto < R$ 0,01)">—</span>`;
            } else {
              const tgtTitle = `Atual: qtd=${this._fmtQty(tp.quantity)}, pu=${this._fmtPu(tp.pu)}, saldo=${this._fmtMoney(tp.balance)}`;
              diffHTML = `<div class="text-[11px]" title="${this._esc(tgtTitle)}">${parts.join("")}</div>`;
            }
          }
          // Transactions column — uma sub-linha por buySell vinculada,
          // mostrando só o **saldo** (monoespaçado). A descrição da
          // transação vai pro `title` (tooltip no hover) — operador
          // pediu pra remover o texto da célula, manter só o número.
          const txnsHTML = (r.txns || []).length
            ? (r.txns || []).map(t => `
                <div class="rp-tx-row" title="${this._esc(t.description || '')}">
                  <span class="rp-tx-balance">${this._fmtMoney(t.balance)}</span>
                </div>`).join("")
            : `<span class="text-gray-300">—</span>`;
          // `unprocessedId` é o identificador que a planilha de upload
          // usa na coluna "Ativo". Vazio significa que a carteira não tem
          // unprocessedSecurityPositions em sourceDate (ou a securityMappings
          // não traduz o securityId de volta) — o upload cai para o beehusName.
          const uidHTML = r.unprocessedId
            ? `<span style="font-family:ui-monospace,Consolas,monospace;font-size:11px">${this._esc(r.unprocessedId)}</span>`
            : `<span class="text-gray-300" title="Sem unprocessedId — o upload usará o beehusName">—</span>`;
          // Qty mismatch hint inside the qtd-rep cell — the cell-level
          // class lights up via the row's `rp-row-qty-mismatch` class.
          const qtyRepCellTitle = r.quantityMismatch && r.targetProcessed
            ? `Qtd atual: ${this._fmtQty(r.targetProcessed.quantity)}`
            : (r.quantityMismatch ? "Sem entrada na atual — quantidade diverge de zero" : "");
          // `tp` (target processedPosition) já está declarado acima no
          // bloco de diff vs target — reusa, não redeclara, ou o JS
          // levanta "Identifier 'tp' has already been declared" e o
          // script inteiro morre sem definir Repetir.
          const tpCells = tp || {};
          const tgtQty = tpCells.quantity != null ? this._fmtQtyDisplay(tpCells.quantity) : '<span class="rp-diff-na">—</span>';
          const tgtPu  = tpCells.pu       != null ? this._fmtPu(tpCells.pu)        : '<span class="rp-diff-na">—</span>';
          const tgtBal = tpCells.balance  != null ? this._fmtMoney(tpCells.balance): '<span class="rp-diff-na">—</span>';
          // Saldo original = Qtd original × PU original (calculado em
          // tempo de render). O backend devolve `r.original.balance`
          // do processedPosition, mas o operador pediu derivar — assim
          // ficamos imunes a discrepâncias de arredondamento upstream.
          const balOrigCalc = (Number(r.original.quantity) || 0) *
                              (Number(r.original.pu)       || 0);
          return `<tr class="${cls}">
            <td data-col="accepted"><input type="checkbox" data-pi="${pi}" data-ri="${ri}" ${r.accepted ? "checked" : ""} onchange="Repetir.onRowToggle(this)" /></td>
            <td data-col="ativo" class="rp-tbl-left">${this._esc(r.securityName)}</td>
            <td data-col="securityId" class="rp-tbl-left" style="font-family:ui-monospace,Consolas,monospace;font-size:10px;color:#6b7280">${this._esc(r.securityId)}</td>
            <td data-col="unprocessedId" class="rp-tbl-left">${uidHTML}</td>
            <td data-col="pricing">${this._esc(r.pricingType || "—")}</td>
            <td data-col="offset" title="${this._esc(this._offsetTitle(r))}">${this._offsetHTML(r)}</td>
            <td data-col="qtyOriginal">${this._fmtQtyDisplay(r.original.quantity)}</td>
            <td data-col="puOriginal">${this._fmtPu(r.original.pu)}</td>
            <td data-col="balanceOriginal">${this._fmtMoney(balOrigCalc)}</td>
            <td data-col="qtyTarget">${tgtQty}</td>
            <td data-col="puTarget">${tgtPu}</td>
            <td data-col="balanceTarget">${tgtBal}</td>
            <td data-col="contribActual"    title="${this._esc(this._contribTitle(r.contributionActual))}">${this._contribHTML(r.contributionActual)}</td>
            <td data-col="qtyRep" class="rp-tbl-qty-rep" title="${this._esc(qtyRepCellTitle)}">${this._fmtQtyDisplay(r.repetition.quantity)}</td>
            <td data-col="puRep">${puRepHTML}</td>
            <td data-col="balanceRep">${this._fmtMoney(r.repetition.balance)}</td>
            <td data-col="contribProjected" title="${this._esc(this._contribTitle(r.contributionProjected))}">${this._contribHTML(r.contributionProjected)}</td>
            <td data-col="amountDiff" title="Quantidade anterior − Quantidade atual. Mostra o movimento entre a data anterior e a data atual sem depender da regra buySell.">${this._amountDiffHTML(r)}</td>
            <td data-col="provisions"       title="${this._esc(this._provisionsTitle(r))}">${this._provisionsHTML(r)}</td>
            <td data-col="diff" class="rp-tbl-left">${diffHTML}</td>
            <td data-col="txns" class="rp-tbl-left rp-tx-cell">${txnsHTML}</td>
            <td data-col="flags" class="rp-tbl-left">${this._flagBadges(r.flags)}</td>
          </tr>`;
        }).join("");

        // ── Caixa + Provisões — rendered as extra rows in the same
        // table so the operator sees the full "wallet context" in one
        // place. These rows are display-only (no checkboxes, no diffs).
        // Caixa goes at the top of the appendix (matches the upload's
        // ordering — `Caixa = "Sim"` rows come after positions in the
        // .xlsx, but the operator reads top-down). Provisions follow.
        const cash2 = p.cash || {};
        // Diff vs target da caixa = new (projetado: former + Σ txns)
        //                          − target (cashAccounts na data alvo).
        // Mostra `—` quando |Δ| < 0,01 ou quando o target não existe.
        const cashDiff = cash2.targetDelta;
        const cashDiffSignificant = cashDiff != null && Math.abs(Number(cashDiff) || 0) >= 0.01;
        const cashDiffCls = cashDiffSignificant
          ? (Number(cashDiff) >= 0 ? 'rp-diff-pos' : 'rp-diff-neg')
          : 'rp-diff-zero';
        const cashDiffHTML = cashDiffSignificant
          ? (Number(cashDiff) >= 0 ? "+" : "") + this._fmtMoney(cashDiff)
          : '—';
        // Caixa diverge → row ganha `.rp-row-cash-diverge` pra escapar
        // do filtro "Só com divergência" (CSS deixa visível quando a
        // class está presente). Mesma tolerância de R$ 0,01 usada nas
        // outras réguas de divergência.
        const cashRowClass = `rp-row-cash${cashDiffSignificant ? " rp-row-cash-diverge" : ""}`;
        // Soma de **todas** as transactions da wallet na janela
        // `(sourceDate, targetDate]` — vem do backend em `cash.delta`
        // (= `txn_bundle.all_cash_delta`, ver pages/repetir_posicoes.py).
        // Inclui buySell, cash events (coupon/amort/dividend/JCP),
        // wallet-level (withdrawalDeposit, rebate, taxes, …) — qualquer
        // tipo. Fecha exatamente com `new = former + delta`.
        const txnsTotal = (cash2.delta != null) ? Number(cash2.delta) : null;
        const txnsTotalCls = (txnsTotal == null || Math.abs(txnsTotal) < 0.005)
                           ? "rp-diff-zero"
                           : (txnsTotal > 0 ? "rp-diff-pos" : "rp-diff-neg");
        const txnsTotalHTML = (txnsTotal != null)
          ? `<span class="rp-tx-balance ${txnsTotalCls}" style="font-weight:600" title="Σ balance de todas as transactions da wallet em (sourceDate, targetDate]">Σ ${this._fmtMoney(txnsTotal)}</span>`
          : '<span class="text-gray-300">—</span>';
        const cashRowHTML = (cash2.new == null && cash2.former == null && cash2.target == null) ? "" : `
          <tr class="${cashRowClass}">
            <td data-col="accepted"></td>
            <td data-col="ativo" class="rp-tbl-left">Caixa</td>
            <td data-col="securityId" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="unprocessedId" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="pricing" class="text-gray-400">—</td>
            <td data-col="offset" class="text-gray-400">—</td>
            <td data-col="qtyOriginal" class="text-gray-400">—</td>
            <td data-col="puOriginal" class="text-gray-400">—</td>
            <td data-col="balanceOriginal">${this._fmtMoney(cash2.former)}</td>
            <td data-col="qtyTarget" class="text-gray-400">—</td>
            <td data-col="puTarget" class="text-gray-400">—</td>
            <td data-col="balanceTarget">${cash2.target != null ? this._fmtMoney(cash2.target) : '<span class="text-gray-300">—</span>'}</td>
            <td data-col="contribActual" class="text-gray-400">—</td>
            <td data-col="qtyRep" class="text-gray-400">—</td>
            <td data-col="puRep" class="text-gray-400">—</td>
            <td data-col="balanceRep">${this._fmtMoney(cash2.new)}</td>
            <td data-col="contribProjected" class="text-gray-400">—</td>
            <td data-col="amountDiff" class="text-gray-400">—</td>
            <td data-col="provisions" class="text-gray-400">—</td>
            <td data-col="diff" class="rp-tbl-left text-[11px] ${cashDiffCls}" title="Δ = projetado − cashAccounts(data atual)">${cashDiffHTML}</td>
            <td data-col="txns" class="rp-tbl-left">${txnsTotalHTML}</td>
            <td data-col="flags" class="rp-tbl-left"><span class="rp-flag rp-flag-curvaPrice">caixa</span></td>
          </tr>`;
        const provs = p.provisionsList || [];
        const provRowsHTML = provs.map(pr => {
          const desc = pr.description || pr.kind || "Provisão";
          const win = `${pr.initialDate || "?"} → ${pr.liquidationDate || "?"}`;
          return `<tr class="rp-row-provision">
            <td data-col="accepted"></td>
            <td data-col="ativo" class="rp-tbl-left">${this._esc(desc)}<div style="font-size:9px;color:#6b7280">${this._esc(win)}</div></td>
            <td data-col="securityId" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="unprocessedId" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="pricing" class="text-gray-400">${this._esc(pr.kind || "—")}</td>
            <td data-col="offset" class="text-gray-400">—</td>
            <td data-col="qtyOriginal" class="text-gray-400">—</td>
            <td data-col="puOriginal" class="text-gray-400">—</td>
            <td data-col="balanceOriginal" class="text-gray-400">—</td>
            <td data-col="qtyTarget" class="text-gray-400">—</td>
            <td data-col="puTarget" class="text-gray-400">—</td>
            <td data-col="balanceTarget" class="text-gray-400">—</td>
            <td data-col="contribActual" class="text-gray-400">—</td>
            <td data-col="qtyRep" class="text-gray-400">—</td>
            <td data-col="puRep" class="text-gray-400">—</td>
            <td data-col="balanceRep">${this._fmtMoney(pr.balance)}</td>
            <td data-col="contribProjected" class="text-gray-400">—</td>
            <td data-col="amountDiff" class="text-gray-400">—</td>
            <td data-col="provisions" class="text-gray-400">—</td>
            <td data-col="diff" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="txns" class="rp-tbl-left text-gray-400">—</td>
            <td data-col="flags" class="rp-tbl-left"><span class="rp-flag rp-flag-buysell">provisão</span></td>
          </tr>`;
        }).join("");

        const warns = (p.warnings || []).length
          ? `<p class="text-[11px] text-red-600 mb-2">${p.warnings.map(w => this._esc(w)).join(" · ")}</p>`
          : "";
        const cash = p.cash || {};
        const prov = p.provisions;
        const ts   = p.targetSummary || {};
        const provHTML = (prov != null && Number(prov) !== 0)
          ? `<span class="rp-prov-chip" title="Soma de provisions cuja janela cobre a data atual (mesma regra do Painel de Controle > Carteira)">Provisões: ${this._fmtMoney(prov)}</span>`
          : "";
        // Diff vs target summary — only shown when the target
        // processedPosition exists. Compact one-liner so the operator
        // can see at a glance whether the repetition matched the
        // upstream's actual snapshot.
        let summaryHTML = "";
        if (ts.hasTarget) {
          const diverged = ts.diverged || 0;
          const matched  = ts.matched  || 0;
          const missingT = ts.missingInTarget || 0;
          const missingR = ts.missingInRepetition || 0;
          const totalDiff = ts.totalBalanceDiff || 0;
          const diffCls = Math.abs(totalDiff) < 0.01 ? "text-green-700"
            : (totalDiff > 0 ? "text-amber-700" : "text-red-700");
          summaryHTML = `<span class="text-[11px] ${diffCls}" title="Comparação com processedPosition na data atual">
            <strong>vs atual:</strong> ${matched} ok · ${diverged} div · ${missingT} a+ · ${missingR} a− · Δ saldo ${this._fmtMoney(totalDiff)}
          </span>`;
        } else {
          summaryHTML = `<span class="text-[11px] text-gray-300 italic" title="Sem processedPosition na data atual para comparar">vs atual: —</span>`;
        }
        // Bloco NAV — três snapshots lado a lado (Anterior / Projetada
        // / Atual). Cada coluna mostra `nav`, `navPerShare`, `amount` +
        // o `inAndOutFlows` na coluna do meio (só faz sentido pra
        // projetada). Quando o backend devolve `null` (sem navPackage
        // para a data, ou denominador zero), a célula vira `—`.
        const navBlockHTML = this._renderNavBlock(p);
        return `
          <div class="bg-white rounded-xl shadow overflow-hidden rp-preview-pane${this._st.onlyDiffs ? ' rp-only-diffs' : ''}">
            <div style="display:flex; align-items:center; gap:6px; padding:10px 12px; border-bottom:1px solid #e5e7eb; flex-wrap:wrap; background:white">
              <h3 class="text-sm font-semibold text-gray-800" style="margin-right:8px">${this._esc(p.walletName)}</h3>
              <!-- Datas da prévia no header: posição atual (targetDate) e
                   posição anterior (sourceDate). Pedido do operador para,
                   ao rodar a prévia (inclusive pelo atalho do bloco
                   "Carteiras com divergência / Conciliação NAV"), enxergar
                   sobre quais datas a projeção está sendo feita. -->
              <span class="text-[11px] text-gray-500" style="margin-right:8px; white-space:nowrap"
                    title="A projeção parte da posição anterior e mira a posição atual.">
                Pos. atual: <strong class="text-gray-700">${this._esc(p.targetDate || "—")}</strong>
                <span class="text-gray-300" style="margin:0 4px">·</span>
                Pos. anterior: <strong class="text-gray-700">${this._esc(p.sourceDate || "—")}</strong>
              </span>
              <!-- Acoes da previa - "Voltar" foi movido para o header
                   principal (rp-topbar) ao lado de "Colunas"; IDs dos
                   demais botoes preservados pra que runPreview() /
                   closePreview() continuem ligando os toggles disabled. -->
              <button type="button" class="fb-chip${this._st.onlyDiffs ? ' active' : ''}" id="rp-only-diffs-topbtn" data-kind="process"
                      onclick="Repetir.toggleOnlyDiffs()"
                      title="Esconde linhas em que a posição projetada bate exatamente com o processedPosition na data atual (e linhas de contexto: caixa, provisão).">
                <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg><span id="rp-only-diffs-label">${this._st.onlyDiffs ? "Mostrar todos" : "Só com divergência"}</span>
              </button>
              <button type="button" class="fb-chip" id="rp-b1-btn" data-kind="process"
                      onclick="Repetir.includeB1Prices()"
                      title="Busca em securityPrices.historyPrice (type=B1, data=targetDate) e preenche a coluna PU REPETIÇÃO.">
                <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>Incluir preços B1
              </button>
              <button type="button" class="fb-chip" id="rp-confirm-btn" data-kind="publish"
                      onclick="Repetir.openModal()"
                      title="Revisa as linhas aceitas e envia ao upstream.">
                <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>Revisar e confirmar
              </button>
              <button type="button" class="fb-chip" id="rp-run-direct-btn" data-kind="publish"
                      onclick="Repetir.runDirect()" disabled
                      title="Temporariamente desabilitado.">
                <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>Executar
              </button>
            </div>
            ${navBlockHTML}
            <!-- Scrollable inner area — caps the panel height so the
                 action bar (Revisar e confirmar) stays visible without
                 the operator having to scroll the whole page. -->
            <div class="rp-preview-scroll" style="padding:8px 12px; max-height:560px; overflow:auto">
              ${warns}
              <!-- The data-col attribute on each th/td drives the
                   "Colunas" toggle: hidden columns get
                   display:none via a dynamic style block built from
                   the config (see _applyColumnVisibility). -->
              <table class="rp-tbl">
                <thead>
                  <tr>
                    <th data-col="accepted" style="width:36px"><input type="checkbox" data-pi="${pi}" checked onchange="Repetir.toggleAllRows(this)" /></th>
                    <th data-col="ativo" class="rp-tbl-left">Ativo</th>
                    <th data-col="securityId" class="rp-tbl-left" title="securityId (ObjectId/UUID interno) — útil para auditoria; oculto por padrão">SecurityId</th>
                    <th data-col="unprocessedId" class="rp-tbl-left" title="securities.unprocessedId em unprocessedSecurityPositions — viajará na coluna Ativo do .xlsx">UnprocessedId</th>
                    <th data-col="pricing">Pricing</th>
                    <th data-col="offset" title="Offset (settlement − NAV) por liquidação. Lê de db.securities.subscriptionSettlementDays/NavDays e redemptionSettlementDays/NavDays. Quando subscription e redemption divergem, mostra 'sub/red'.">Offset</th>
                    <th data-col="qtyOriginal">Qtd anterior</th>
                    <th data-col="puOriginal">PU anterior</th>
                    <th data-col="balanceOriginal" title="Calculado: qtd anterior × PU anterior">Saldo anterior</th>
                    <th data-col="qtyTarget" title="processedPosition na data atual — quantidade">Qtd atual</th>
                    <th data-col="puTarget" title="processedPosition na data atual — PU">PU atual</th>
                    <th data-col="balanceTarget" title="processedPosition na data atual — saldo">Saldo atual</th>
                    <th data-col="contribActual" title="totalContribution na posição atual (daily + intraday + event), usando os valores do processedPosition em targetDate.">Contrib. atual</th>
                    <th data-col="qtyRep">Qtd projetada</th>
                    <th data-col="puRep">PU projetada</th>
                    <th data-col="balanceRep">Saldo projetado</th>
                    <th data-col="contribProjected" title="totalContribution na posição projetada (daily + intraday + event). Ver docs/CONCILIACAO_RECALCULO.md §§4-7.">Contrib. projetada</th>
                    <th data-col="amountDiff" title="amountDifference = quantidade anterior (source) − quantidade atual (target). Mostra quanto a posição se moveu entre a data anterior e a data atual, sem depender de transação.">Δ Qtd (anterior − atual)</th>
                    <th data-col="provisions" title="Somatório de db.provisions.balance ativas em targetDate (initialDate ≤ targetDate AND liquidationDate > targetDate) cujo securityId casa com este ativo. Tooltip detalha cada provisão.">Provisões</th>
                    <th data-col="diff" class="rp-tbl-left" title="Diferença entre o valor calculado e o processedPosition na data atual (projetada − atual)">Diff vs atual</th>
                    <th data-col="txns" class="rp-tbl-left">Transação</th>
                    <th data-col="flags" class="rp-tbl-left">Flags</th>
                  </tr>
                </thead>
                <tbody>${cashRowHTML}${rows || `<tr><td colspan="22" class="rp-tbl-left" style="text-align:center; color:#9ca3af; padding:14px 0">Sem ativos para repetir.</td></tr>`}${provRowsHTML}</tbody>
              </table>
            </div>
          </div>`;
      }).join("");

      // "Revisar e confirmar" foi movido para a barra de ações no
      // topo (#rp-confirm-btn) — fica sempre visível ao rolar a prévia.

      // Transações + Provisões — blocos CRUD acoplados à prévia. Ficam
      // visíveis apenas quando há uma prévia ativa; usam o
      // `activeWalletId` + a janela [sourceDate, targetDate] para
      // pré-popular o filtro. Os tbody/inputs mantêm os mesmos IDs
      // (`rp-txn-*`, `rp-prov-*`) porque só existe uma prévia ativa
      // por vez — os handlers existentes (refreshTxns/Provs, save…,
      // delete…) continuam funcionando sem alterações.
      const single = this._st.preview[0] || {};
      const src = single.sourceDate || "";
      const tgt = single.targetDate || "";
      out.insertAdjacentHTML("beforeend", this._renderCrudBlocks(src, tgt, single));
      // Kick off the auto-refresh of both blocks for the active
      // wallet. Pre-fills the date range using (source, target) so
      // the operator lands on a focused view.
      this.refreshTxns();
      this.refreshProvs();
    },

    _renderCrudBlocks(sourceDate, targetDate, p) {
      // Two `<details open>` blocks rendered as part of the preview
      // output. The wallet picker is gone — the active wallet is
      // already known. Date defaults: txns use **(source+1 dia,
      // target]** — coerente com o cash projection (Σ txns em
      // `(sourceDate, targetDate]`) e com o que o operador quer ver
      // (movimentos que aconteceram ENTRE a posição de origem e a
      // alvo, sem incluir o dia da própria posição de origem, que já
      // está refletido em `processedPosition(source)`). Provisions
      // widen forward por 1 ano pra cobrir provisões futuras.
      let txnFrom = "";
      if (sourceDate) {
        const d = new Date(sourceDate + "T12:00:00");
        d.setDate(d.getDate() + 1);
        txnFrom = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
      }
      const txnTo = targetDate || "";
      // Provisões: a busca usa estritamente a DATA ALVO (sem janela
      // editável pelo usuário). `refreshProvs` lê `targetDate` direto
      // do `_st.preview[0]` em runtime — aqui só rendemos a label.
      // Ordem dos blocos: Provisões **antes** de Transações (pedido
      // do operador — provisões pendentes são prioridade visual quando
      // o operador abre a prévia).
      return `
        <details class="bg-white rounded-xl shadow p-4 mt-3" open>
          <summary class="cursor-pointer list-none">
            <div class="flex items-center gap-3 flex-wrap">
              <span class="text-sm font-semibold text-gray-800">Provisões</span>
              <span class="flex items-center gap-3 flex-wrap ml-auto" onclick="event.preventDefault();event.stopPropagation()">
                <span class="text-[11px] text-gray-600">
                  Data alvo:
                  <span class="font-medium text-gray-800">${this._esc(targetDate || "—")}</span>
                </span>
                <button type="button" class="rp-btn rp-btn-primary" onclick="Repetir.openProvEditor(null)">+ Nova provisão</button>
                <span id="rp-prov-status" class="text-[11px] text-gray-500"></span>
              </span>
            </div>
          </summary>

          ${this._renderExpectedProvisionsBlock(p)}

          <div class="bg-white rounded-xl border overflow-hidden" style="max-height:380px; overflow-y:auto">
            <table class="rp-tbl">
              <thead>
                <tr>
                  <th>Início</th>
                  <th>Liquidação</th>
                  <th class="rp-tbl-left">Tipo</th>
                  <th class="rp-tbl-left">Descrição</th>
                  <th class="rp-tbl-left">Ativo</th>
                  <th>Saldo</th>
                  <th style="width:90px"></th>
                </tr>
              </thead>
              <tbody id="rp-prov-tbody">
                <tr><td colspan="7" class="rp-tbl-left" style="text-align:center;color:#9ca3af;padding:14px 0">Carregando...</td></tr>
              </tbody>
            </table>
          </div>
        </details>

        <details class="bg-white rounded-xl shadow p-4 mt-3" open>
          <summary class="cursor-pointer list-none">
            <div class="flex items-center gap-3 flex-wrap">
              <span class="text-sm font-semibold text-gray-800">Transações</span>
              <span class="flex items-center gap-3 flex-wrap ml-auto" onclick="event.preventDefault();event.stopPropagation()">
                <label class="flex items-center gap-1.5 text-[11px] text-gray-600">
                  De
                  <input id="rp-txn-from" type="date" class="rp-input rp-input-date" value="${this._esc(txnFrom)}" onchange="Repetir.refreshTxns()" />
                </label>
                <label class="flex items-center gap-1.5 text-[11px] text-gray-600">
                  Até
                  <input id="rp-txn-to" type="date" class="rp-input rp-input-date" value="${this._esc(txnTo)}" onchange="Repetir.refreshTxns()" />
                </label>
                <label class="text-[11px] flex items-center gap-1">
                  <input id="rp-txn-only-missing" type="checkbox" onchange="Repetir.renderTxns()" />
                  Só sem ativo
                </label>
                <button type="button" class="rp-btn rp-btn-muted" onclick="Repetir.refreshTxns()">Atualizar</button>
                <button type="button" class="rp-btn rp-btn-primary" onclick="Repetir.openTxnEditor(null)">+ Nova transação</button>
                <span id="rp-txn-status" class="text-[11px] text-gray-500"></span>
              </span>
            </div>
          </summary>
          <div class="bg-white rounded-xl border overflow-hidden" style="max-height:380px; overflow-y:auto">
            <table class="rp-tbl">
              <thead>
                <tr>
                  <th>Liq.</th>
                  <th class="rp-tbl-left">Tipo</th>
                  <th class="rp-tbl-left">Ativo</th>
                  <th class="rp-tbl-left">Descrição</th>
                  <th>Qtd</th>
                  <th>Saldo</th>
                  <th title="amountDifference do ativo da transação entre processedPosition(anterior) e processedPosition(atual). Vazio quando a transação não tem securityId ou quando o ativo não aparece em nenhuma das duas posições.">Δ Qtd posição</th>
                  <th style="width:110px"></th>
                </tr>
              </thead>
              <tbody id="rp-txn-tbody">
                <tr><td colspan="8" class="rp-tbl-left" style="text-align:center;color:#9ca3af;padding:14px 0">Carregando...</td></tr>
              </tbody>
            </table>
          </div>
        </details>`;
    },

    // fetchCurvaPrices removed — curva PUs are now applied by the
    // backend during /preview (see _rule_curva_price in
    // pages/repetir_posicoes.py). The "Incluir preços na curva" button
    // was eliminated with it.

    onRowToggle(el) {
      const p = this._st.preview[+el.dataset.pi];
      if (!p) return;
      const r = p.rows[+el.dataset.ri];
      if (!r) return;
      r.accepted = !!el.checked;
    },
    toggleAllRows(el) {
      const p = this._st.preview[+el.dataset.pi];
      if (!p) return;
      const on = !!el.checked;
      p.rows.forEach(r => r.accepted = on);
      this._renderPreview();
    },

    // ── Modal + apply ─────────────────────────────────────────────────
    openModal() {
      const summary = document.getElementById("rp-modal-summary");
      const overrideCount = Object.keys(this._st.puOverrides || {}).length;
      const totalAccepted = this._st.preview.reduce(
        (n, p) => n + (p.rows || []).filter(r => r.accepted).length, 0);
      // Map preview wallets onto their companyId via the daily list (the
      // backend will do the same to split the upload).
      const wid2co = {};
      for (const w of this._st.daily) wid2co[w.walletId] = w.companyId;
      const companies = new Set(this._st.preview.map(p => wid2co[p.walletId]).filter(Boolean));

      const headline = `<p class="text-xs text-gray-700 mb-3">
        <strong>${totalAccepted}</strong> ativo(s) em <strong>${this._st.preview.length}</strong> carteira(s)
        serão enviados em <strong>${companies.size} arquivo(s) .xlsx</strong> (um por empresa).
        ${overrideCount ? `<span class="text-blue-700">${overrideCount} PU(s) sobrescrito(s) (curva/B1).</span>` : ""}
      </p>`;
      summary.innerHTML = headline + this._st.preview.map(p => {
        const accepted = (p.rows || []).filter(r => r.accepted);
        const cname = this._st.companyNames[wid2co[p.walletId]] || "";
        const cTag  = cname ? `<span class="text-[10px] text-gray-400">[${this._esc(cname)}]</span> ` : "";
        return `<div style="border:1px solid #e5e7eb; border-radius:6px; padding:8px 10px">
          <div class="font-semibold text-gray-800 text-xs">${cTag}${this._esc(p.walletName)}</div>
          <div class="text-[11px] text-gray-500">${this._esc(p.sourceDate)} → <strong>${this._esc(p.targetDate)}</strong> · ${accepted.length} de ${p.rows.length} ativos aceitos</div>
        </div>`;
      }).join("");
      document.getElementById("rp-modal").classList.remove("hidden");
    },
    closeModal() {
      document.getElementById("rp-modal").classList.add("hidden");
    },

    // ── Transações CRUD ───────────────────────────────────────────────
    // The list is fetched via /api/beehus/transactions/search scoped to
    // a single walletId + date range. Mutation endpoints (create/edit/
    // delete) hit the existing Beehus console routes — we don't
    // duplicate that code here, just orchestrate the UI.
    _txnTypesAll: [
      "amortization","brokerageFee","buySell","bzFundTaxes",
      "contributionAdjustment","coupon","dividend","dividendOnboarding",
      "gainsExpenses","interestOnEquity","managementFee","maturity",
      "other","otherFee","performanceFee","rebate",
      "securityContributionAdjustment","securityTransfer","taxes",
      "withdrawalDeposit","withdrawalDepositAdjustment",
    ],
    // Tipos de transação que **exigem** um securityId. `taxes` saiu da
    // lista a pedido do operador — taxas são genéricas (corretagem,
    // IRRF, etc.) e não necessariamente atreladas a um ativo
    // específico; quando vazias não devem disparar o badge "SEM
    // ATIVO" nem entrar na contagem de órfãs.
    _txnTypesNeedSecurity: new Set([
      "amortization","buySell","coupon","dividend","dividendOnboarding",
      "interestOnEquity","maturity","securityContributionAdjustment",
      "securityTransfer",
    ]),

    async refreshTxns() {
      // Scope = active wallet from the preview. Without a preview, the
      // block isn't even in the DOM, so this is a defensive bail.
      const wid = this._st.activeWalletId;
      const tbody = document.getElementById("rp-txn-tbody");
      const status = document.getElementById("rp-txn-status");
      if (!wid || !tbody) return;
      const meta = this._st.walletsMeta[wid] || {};
      const cid  = meta.companyId;
      if (!cid) {
        if (status) status.textContent = "Wallet sem companyId.";
        return;
      }
      const fromEl = document.getElementById("rp-txn-from");
      const toEl   = document.getElementById("rp-txn-to");
      if (!fromEl || !toEl) return;
      if (!fromEl.value || !toEl.value) {
        const today = new Date();
        const past  = new Date(today.getTime() - 60*86400*1000);
        const iso   = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
        if (!toEl.value)   toEl.value   = iso(today);
        if (!fromEl.value) fromEl.value = iso(past);
      }
      if (status) status.textContent = "Carregando...";
      try {
        const r = await fetch("/api/beehus/transactions/search", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            companyId:   cid,
            walletIds:   [wid],
            initialDate: fromEl.value,
            finalDate:   toEl.value,
          }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this._st.txns = data.transactions || [];
        const orphans = (this._st.preview[0] && this._st.preview[0].orphanTransactions) || [];
        const orphanSuffix = orphans.length
          ? ` · ${orphans.length} órfã(s)`
          : "";
        status.textContent =
          `${this._st.txns.length} transação(ões)${data.truncated ? " (truncado)" : ""}${orphanSuffix}.`;
        this.renderTxns();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
        this._st.txns = [];
      }
    },

    renderTxns() {
      const tbody = document.getElementById("rp-txn-tbody");
      const onlyMissing = document.getElementById("rp-txn-only-missing").checked;
      const rows = (this._st.txns || []).filter(t => {
        if (!onlyMissing) return true;
        return this._txnTypesNeedSecurity.has(t.type) && !t.securityId;
      });
      // Indexa órfãs por id pra cruzar com a lista local de
      // transactions — o backend já entrega `orphanTransactions` no
      // payload da prévia. Sem prévia ativa, fica vazio.
      const orphans = (this._st.preview[0] && this._st.preview[0].orphanTransactions) || [];
      const orphanById = new Map(orphans.map(o => [o.id, o]));
      // Mapa de Δqty por security entre source e target (já calculado
      // no backend a partir do `processedPosition`). Usado pra mostrar
      // por transação se o ativo se moveu na carteira — bate com a
      // intuição "esse ativo existe na tabela de securities, não
      // deveria ser órfã".
      const amtDiff = (this._st.preview[0] && this._st.preview[0].amountDifferenceBySecurityId) || {};
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="rp-tbl-left" style="text-align:center;color:#9ca3af;padding:14px 0">Nenhuma transação no filtro.</td></tr>`;
        return;
      }
      tbody.innerHTML = rows.map(t => {
        const missing = this._txnTypesNeedSecurity.has(t.type) && !t.securityId;
        const orph = orphanById.get(t.id);
        const isOrphan = !!orph;
        const cls = (missing || isOrphan) ? "rp-row-qty-mismatch" : "";
        const orphanBadge = isOrphan
          ? `<span class="rp-flag rp-flag-missingB1Price" title="Órfã: ${this._esc(orph.problem || "")} · offset=${orph.offset != null ? orph.offset : "?"}">ÓRFÃ</span> `
          : "";
        const ativoHTML = missing
          ? `<span class="rp-flag rp-flag-missingB1Price">SEM ATIVO</span>`
          : `${orphanBadge}${t.securityName ? this._esc(t.securityName) : (t.securityId ? `<span style="font-family:ui-monospace,Consolas,monospace;font-size:10px">${this._esc(t.securityId)}</span>` : '<span class="text-gray-300">—</span>')}`;
        // Δ Qtd posição: só faz sentido pra transações com securityId.
        // Verde quando há movimento, dash quando não há (ou quando a
        // transação não tem ativo).
        let amtDiffHTML = '<span class="text-gray-300">—</span>';
        if (t.securityId && Object.prototype.hasOwnProperty.call(amtDiff, t.securityId)) {
          const d = Number(amtDiff[t.securityId]) || 0;
          if (Math.abs(d) >= 1e-6) {
            const c = d > 0 ? "rp-diff-pos" : "rp-diff-neg";
            const sig = d > 0 ? "+" : "";
            amtDiffHTML = `<span class="${c}" title="qty(atual) − qty(anterior) do ativo">${sig}${this._fmtQty(d)}</span>`;
          } else {
            amtDiffHTML = '<span class="rp-diff-zero" title="qty(atual) = qty(anterior) — sem movimento">0</span>';
          }
        }
        return `<tr class="${cls}">
          <td>${this._esc(t.liquidationDate || "")}</td>
          <td class="rp-tbl-left">${this._esc(t.type || "—")}</td>
          <td class="rp-tbl-left">${ativoHTML}</td>
          <td class="rp-tbl-left" title="${this._esc(t.description || "")}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this._esc(t.description || "")}</td>
          <td>${t.quantity != null ? this._fmtQty(t.quantity) : '<span class="text-gray-300">—</span>'}</td>
          <td>${this._fmtMoney(t.balance)}</td>
          <td>${amtDiffHTML}</td>
          <td class="rp-tbl-left">
            <button class="rp-btn rp-btn-muted" style="padding:0.2rem 0.5rem;font-size:11px" onclick="Repetir.openTxnEditor('${this._esc(t.id)}')">Editar</button>
            <button class="rp-btn rp-btn-muted" style="padding:0.2rem 0.5rem;font-size:11px" onclick="Repetir.deleteTxn('${this._esc(t.id)}')" title="Excluir transação">×</button>
          </td>
        </tr>`;
      }).join("");
    },

    openTxnEditor(txnId) {
      // `txnId == null` → create flow. Editing pulls fields from the
      // already-loaded row in state, no extra fetch needed (the search
      // endpoint surfaces everything patchable).
      const t = txnId ? (this._st.txns.find(x => x.id === txnId) || null) : null;
      const head = document.getElementById("rp-txn-modal-head");
      head.textContent = t ? `Editar transação` : `Nova transação`;
      head.dataset.id  = t ? t.id : "";
      const typeSel = document.getElementById("rp-txn-type");
      typeSel.innerHTML = `<option value="">Selecione...</option>` +
        this._txnTypesAll.map(tp => `<option value="${tp}" ${t && t.type === tp ? "selected" : ""}>${tp}</option>`).join("");
      document.getElementById("rp-txn-liq").value     = t ? (t.liquidationDate || "") : "";
      document.getElementById("rp-txn-op").value      = t ? (t.operationDate   || "") : "";
      document.getElementById("rp-txn-balance").value = t ? (t.balance ?? "") : "";
      document.getElementById("rp-txn-qty").value     = t ? (t.quantity ?? "") : "";
      document.getElementById("rp-txn-currency").value= t ? (t.currencyId || "BRL") : "BRL";
      document.getElementById("rp-txn-sec").value     = t ? (t.securityId || "") : "";
      document.getElementById("rp-txn-entity").value  = t ? (t.entityId || "") : "";
      document.getElementById("rp-txn-desc").value    = t ? (t.description || "") : "";
      document.getElementById("rp-txn-modal-status").textContent = "";
      this._updateTxnSecRequiredHint();
      typeSel.onchange = () => this._updateTxnSecRequiredHint();
      document.getElementById("rp-txn-modal").classList.remove("hidden");
    },
    _updateTxnSecRequiredHint() {
      const tp = document.getElementById("rp-txn-type").value;
      const hint = document.getElementById("rp-txn-sec-required");
      hint.classList.toggle("hidden", !this._txnTypesNeedSecurity.has(tp));
    },
    closeTxnEditor() {
      document.getElementById("rp-txn-modal").classList.add("hidden");
    },
    async saveTxnEditor() {
      const wid = this._st.activeWalletId;
      const meta = this._st.walletsMeta[wid] || {};
      const id  = document.getElementById("rp-txn-modal-head").dataset.id;
      const body = {
        beehusTransactionType: document.getElementById("rp-txn-type").value,
        liquidationDate:       document.getElementById("rp-txn-liq").value,
        operationDate:         document.getElementById("rp-txn-op").value,
        balance:               document.getElementById("rp-txn-balance").value,
        currencyId:            document.getElementById("rp-txn-currency").value || "BRL",
        securityId:            document.getElementById("rp-txn-sec").value || null,
        entityId:              document.getElementById("rp-txn-entity").value || "",
        description:           document.getElementById("rp-txn-desc").value || "",
      };
      const status = document.getElementById("rp-txn-modal-status");
      status.textContent = id ? "Atualizando..." : "Criando...";
      try {
        let r;
        if (id) {
          // PATCH only sends what changed. To keep it simple, send the
          // common patchable fields — the backend whitelists by key.
          const patch = {};
          for (const k of ["beehusTransactionType","liquidationDate","operationDate","balance","currencyId","securityId","entityId","description"]) {
            if (body[k] !== "" && body[k] !== null && body[k] !== undefined) patch[k] = body[k];
          }
          r = await fetch(`/api/beehus/transactions/${encodeURIComponent(id)}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(patch),
          });
        } else {
          // Create requires companyId + walletId + entityId + balance + dates.
          if (!body.balance || !body.liquidationDate || !body.operationDate || !body.entityId) {
            status.textContent = "Preencha tipo, liquidação, operação, saldo e entidade.";
            return;
          }
          r = await fetch(`/api/beehus/transactions`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              ...body,
              companyId: meta.companyId,
              walletId:  wid,
            }),
          });
        }
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this.closeTxnEditor();
        await this.refreshTxns();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      }
    },
    async deleteTxn(id) {
      if (!confirm("Excluir esta transação?")) return;
      try {
        const r = await fetch(`/api/beehus/transactions/${encodeURIComponent(id)}`, {method: "DELETE"});
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        await this.refreshTxns();
      } catch (e) {
        alert(`Erro: ${e.message}`);
      }
    },

    // ── Provisões CRUD ────────────────────────────────────────────────
    async refreshProvs() {
      const wid = this._st.activeWalletId;
      const tbody = document.getElementById("rp-prov-tbody");
      const status = document.getElementById("rp-prov-status");
      if (!wid || !tbody) return;
      const meta = this._st.walletsMeta[wid] || {};
      const cid  = meta.companyId;
      if (!cid) {
        if (status) status.textContent = "Wallet sem companyId.";
        return;
      }
      // A busca de provisões usa o modo `coverDate` da rota
      // `/api/beehus/provisions/search`, que aplica a régua **estrita**
      // de `_provisions_detail` (initialDate <= coverDate AND
      // liquidationDate > coverDate). Garante alinhamento perfeito
      // com o cálculo do NAV projetado — provisões liquidando
      // exatamente na data alvo NÃO entram (já estão refletidas via
      // transaction do dia).
      const targetDate = (this._st.preview[0] || {}).targetDate || "";
      if (!targetDate) {
        if (status) status.textContent = "Sem prévia ativa.";
        this._st.provs = [];
        this.renderProvs();
        return;
      }
      if (status) status.textContent = "Carregando...";
      try {
        const r = await fetch("/api/beehus/provisions/search", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            companyId: cid,
            walletId:  wid,
            coverDate: targetDate,
          }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        // Backend já restringe por walletId + janela colada na data
        // alvo — sem filtro client-side adicional.
        this._st.provs = data.provisions || [];
        status.textContent = `${this._st.provs.length} provisão(ões).`;
        this.renderProvs();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
        this._st.provs = [];
      }
    },

    renderProvs() {
      const tbody = document.getElementById("rp-prov-tbody");
      const rows = this._st.provs || [];
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="rp-tbl-left" style="text-align:center;color:#9ca3af;padding:14px 0">Nenhuma provisão no filtro.</td></tr>`;
        return;
      }
      tbody.innerHTML = rows.map(p => `<tr>
        <td>${this._esc(p.initialDate || "")}</td>
        <td>${this._esc(p.liquidationDate || "")}</td>
        <td class="rp-tbl-left">${this._esc(p.provisionType || "—")}</td>
        <td class="rp-tbl-left" title="${this._esc(p.description || "")}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${this._esc(p.description || "")}</td>
        <td class="rp-tbl-left">${p.securityName ? this._esc(p.securityName) : '<span class="text-gray-300">—</span>'}</td>
        <td>${this._fmtMoney(p.balance)}</td>
        <td class="rp-tbl-left">
          <button class="rp-btn rp-btn-muted" style="padding:0.2rem 0.5rem;font-size:11px" onclick="Repetir.deleteProv('${this._esc(p.id)}')" title="Excluir provisão">×</button>
        </td>
      </tr>`).join("");
    },

    openProvEditor() {
      document.getElementById("rp-prov-type").value     = "";
      document.getElementById("rp-prov-source").value   = "manual";
      document.getElementById("rp-prov-initial").value  = "";
      document.getElementById("rp-prov-liq").value      = "";
      document.getElementById("rp-prov-balance").value  = "";
      document.getElementById("rp-prov-currency").value = "BRL";
      document.getElementById("rp-prov-sec").value      = "";
      document.getElementById("rp-prov-desc").value     = "";
      document.getElementById("rp-prov-modal-status").textContent = "";
      document.getElementById("rp-prov-modal").classList.remove("hidden");
    },
    closeProvEditor() {
      document.getElementById("rp-prov-modal").classList.add("hidden");
    },
    async saveProvEditor() {
      const wid = this._st.activeWalletId;
      const meta = this._st.walletsMeta[wid] || {};
      const body = {
        companyId:       meta.companyId,
        walletId:        wid,
        provisionType:   document.getElementById("rp-prov-type").value,
        provisionSource: document.getElementById("rp-prov-source").value || "manual",
        initialDate:     document.getElementById("rp-prov-initial").value,
        liquidationDate: document.getElementById("rp-prov-liq").value,
        balance:         document.getElementById("rp-prov-balance").value,
        currencyId:      document.getElementById("rp-prov-currency").value || "BRL",
        securityId:      document.getElementById("rp-prov-sec").value || null,
        description:     document.getElementById("rp-prov-desc").value || "",
      };
      const status = document.getElementById("rp-prov-modal-status");
      if (!body.companyId || !body.walletId || !body.provisionType ||
          !body.initialDate || !body.liquidationDate || !body.balance) {
        status.textContent = "Preencha tipo, datas e saldo.";
        return;
      }
      status.textContent = "Criando...";
      try {
        const r = await fetch(`/api/beehus/provisions`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        this.closeProvEditor();
        await this.refreshProvs();
      } catch (e) {
        status.textContent = `Erro: ${e.message}`;
      }
    },
    async deleteProv(id) {
      if (!confirm("Excluir esta provisão?")) return;
      try {
        const r = await fetch(`/api/beehus/provisions/${encodeURIComponent(id)}`, {method: "DELETE"});
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        await this.refreshProvs();
      } catch (e) {
        alert(`Erro: ${e.message}`);
      }
    },

    async confirmApply() {
      const items = this._st.preview.map(p => ({
        walletId:            p.walletId,
        sourceDate:          p.sourceDate,
        targetDate:          p.targetDate,
        acceptedSecurityIds: (p.rows || []).filter(r => r.accepted).map(r => r.securityId),
      }));
      const btn = document.getElementById("rp-modal-confirm");
      btn.disabled = true;
      btn.textContent = "Enviando...";
      try {
        const r = await fetch("/api/repetir-posicoes/apply", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            items,
            puOverrides: this._st.puOverrides || {},
          }),
        });
        const data = await r.json();
        if (!r.ok || !data.uploaded) {
          // Compose a message per failed upload so the operator knows
          // which company-file blew up.
          const fails = (data.uploads || []).filter(u => u.status !== "ok");
          const msg = fails.length
            ? fails.map(f => `${f.companyName || f.companyId}: ${f.error || f.status}`).join("\n")
            : (data.error || `HTTP ${r.status}`);
          alert("Erro no envio:\n" + msg);
          if (data.diffReport) this._showLogReport(data.diffReport);
        } else {
          this.closeModal();
          // Replace the success alert with the log modal — the report
          // makes the prior "files: rows" detail redundant and gives
          // the operator the diff vs target context for audit.
          if (data.diffReport) {
            this._showLogReport(data.diffReport);
          } else {
            const total   = data.totalRows ?? 0;
            const uploads = data.uploads || [];
            alert(`${uploads.length} arquivo(s) enviado(s).\nTotal: ${total} linha(s).`);
          }
        }
      } catch (e) {
        alert("Erro: " + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Confirmar e enviar";
      }
    },
  };

  // Expose Repetir on window explicitly. `const` declarations at top
  // level live in the global lexical scope but do NOT attach to
  // `window`, and some browsers fail to resolve them from inline
  // `onclick="Repetir.switchTab(...)"` attributes. Without this line
  // the tabs render but appear unclickable — the inline handler
  // raises a silent ReferenceError that never reaches the console
  // unless you wrap each handler in a try/catch. Assigning to
  // `window.Repetir` makes the handlers work universally.
  // ── Pattern #1 (painel inline) ────────────────────────────────────
  // Suporta o chip "Projetada" no controlpanel.html. Mesma assinatura
  // que Strip.open(): esconde as outras views inline, remove a classe
  // `hidden-view` de #repetir-view, marca chip ativo e lazy-inita.
  // No standalone /repetir-posicoes, `Repetir.open` não é chamado
  // (o template chama Repetir.init() direto no DOMContentLoaded).
  Repetir.open = async function(btn) {
    Funcoes._hideAllFrames();
    Funcoes._currentDeep = null;
    Funcoes._inDaytrade  = false;
    Funcoes._inStrip     = false;
    Funcoes._inRepetir   = true;
    document.getElementById('tool-view')?.classList.add('hidden-view');
    document.getElementById('controlpanel-view')?.classList.add('hidden-view');
    document.getElementById('daytrade-view')?.classList.add('hidden-view');
    document.getElementById('strip-view')?.classList.add('hidden-view');
    document.getElementById('repetir-view')?.classList.remove('hidden-view');
    Funcoes._setActiveChip(btn || document.getElementById('chip-repetir'));
    Funcoes._syncResetChip?.();
    if (!Repetir._inited) {
      try { await Repetir.init(); }
      catch (e) { console.error('Repetir.init() falhou:', e); }
      Repetir._inited = true;
    }
  };
  Repetir.close = function() { Funcoes.showIssues(); };


  window.Repetir = Repetir;
