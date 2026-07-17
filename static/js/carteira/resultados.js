/* Carteira — resultados (modo leitura): dispara a busca (run),
   re-renderiza e monta o bloco/tabela de cada carteira por data. */
Object.assign(Carteira, {
    async run() {
      const payload = this._payload();
      if (!this._validate(payload)) return;

      // Collapse the advanced groupings/wallets pane on submit — the
      // operator wants the results to take focus, and the filter selections
      // are preserved in state so re-opening shows the same picks.
      const adv = document.getElementById("ct-advanced");
      const advBtn = document.getElementById("ct-toggle-advanced");
      if (adv && !adv.classList.contains("hidden")) {
        adv.classList.add("hidden");
        if (advBtn) advBtn.innerHTML = "+ Groupings &amp; Wallets";
      }

      const status = document.getElementById("ct-status");
      const warns  = document.getElementById("ct-warnings");
      const out    = document.getElementById("ct-results");
      const btn    = document.getElementById("ct-run-btn");
      status.textContent = "Carregando...";
      warns.innerHTML = "";
      out.innerHTML   = "";
      btn.disabled    = true;

      let data;
      try {
        const r = await fetch("/api/carteira/data", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        data = await r.json();
        if (!r.ok || data.error) {
          status.textContent = "";
          warns.innerHTML = `<p class="text-xs text-red-600">${this._esc(data.error || "Falha (HTTP " + r.status + ")")}</p>`;
          return;
        }
      } catch (e) {
        status.textContent = "";
        warns.innerHTML = `<p class="text-xs text-red-600">Erro de rede: ${this._esc(e.message || "")}</p>`;
        return;
      } finally {
        btn.disabled = false;
      }

      const wallets = data.wallets || [];
      const dates   = data.dates   || [];
      this._st.lastData = {wallets, dates};
      status.textContent = `${wallets.length} carteira(s) · ${dates.length} dia(s) útil(eis).`;
      if (!wallets.length) {
        out.innerHTML = `<p class="text-xs text-gray-400 text-center py-6">Nenhuma posição encontrada para os filtros informados.</p>`;
        return;
      }
      this._rerender();
    },

    _rerender() {
      const data = this._st.lastData;
      if (!data) return;
      const out = document.getElementById("ct-results");
      out.innerHTML = data.wallets.map(w => this._renderWalletBlock(w, data.dates)).join("");
    },

    onShowContribChange() {
      // Re-render from the cached response — no need to re-hit the API,
      // totalContribution is already in the per-cell payload.
      this._rerender();
    },

    // ── Per-wallet table ─────────────────────────────────────────────

    /* Contexto: gera o HTML do bloco (card + tabela) de UMA carteira no modo de
       leitura. Retorna string HTML.
       Pseudocódigo:
         1. Se a carteira está em edição, delega a _renderEditBlock.
         2. Define nº de sub-colunas por data (3, ou 4 com "Contribuição").
         3. Monta cabeçalho de datas (marcando datas com erro de arquivo bruto).
         4. Monta uma linha por security e as linhas de rodapé (contribuição,
            provisões, caixa).
         5. Mostra botão "Editar" só quando há exatamente 1 data.
         6. Retorna o card completo com aviso de erro quando houver. */
    _renderWalletBlock(w, dates) {
      const editing = this._st.editing.get(w.walletId) || null;
      // Edit mode locks the view to a single date — the inline form only
      // makes sense for one (wallet, date) pair, and the apply endpoint
      // uploads exactly one snapshot per call.
      if (editing) return this._renderEditBlock(w, editing);

      // Per-security totalContribution is an opt-in column — toggled by
      // the checkbox in the action row. Each date group is 3 or 4 sub-cols.
      const showContrib = !!document.getElementById("ct-show-contrib")?.checked;
      const perDateCols = showContrib ? 4 : 3;

      // Datas com ERRO no arquivo bruto (unprocessed vazio/ausente enquanto há
      // posição processada). Regra de produto: informar o erro e NÃO exibir ativos
      // dessas datas (sem fallback). Ver /api/carteira/data → unprocessedErrorByDate.
      const errByDate = w.unprocessedErrorByDate || {};
      const errDates = dates.filter(d => errByDate[d]);
      const dateGroupHeader = dates.map(d =>
        errByDate[d]
          ? `<th class="ct-tbl-date-group" colspan="${perDateCols}" style="background:#fef2f2;color:#991b1b" title="Erro no arquivo unprocessed desta data — snapshot vazio/ausente">${this._esc(d)} ⚠</th>`
          : `<th class="ct-tbl-date-group" colspan="${perDateCols}">${this._esc(d)}</th>`
      ).join("");
      const subHeader = dates.map(() =>
        showContrib
          ? `<th class="ct-tbl-date-group">Qtd</th><th>PU</th><th>Saldo</th><th>Contrib.</th>`
          : `<th class="ct-tbl-date-group">Qtd</th><th>PU</th><th>Saldo</th>`
      ).join("");

      const secRows = w.securities.map(s => {
        const cells = dates.map(d => {
          const c = (s.byDate || {})[d] || {};
          const extra = showContrib
            ? `<td>${this._fmtMoney(c.totalContribution)}</td>`
            : "";
          return `
            <td class="ct-tbl-date-group">${this._fmtQty(c.quantity)}</td>
            <td>${this._fmtPu(c.pu)}</td>
            <td>${this._fmtMoney(c.balance)}</td>
            ${extra}`;
        }).join("");
        const cls = s.quantityChanged ? "ct-row-changed" : "";
        const uid = (s.unprocessedId || "").trim();
        const uidCell = uid
          ? `<span class="ct-id" style="font-size:11px;color:#374151">${this._esc(uid)}</span>`
          : `<span class="ct-id" style="color:#d1d5db">—</span>`;
        return `<tr class="${cls}">
          <td class="ct-tbl-sec">
            <div class="font-medium text-gray-800">${this._esc(s.securityName)}</div>
            <div class="ct-id">${this._esc(s.securityId)}</div>
          </td>
          <td class="ct-tbl-sec" style="text-align:left">${uidCell}</td>
          ${cells}
        </tr>`;
      }).join("");

      // Footer rows: total contribution / provisions / cash. Each spans
      // qty+pu+balance (+ contribution when shown) per date.
      const footerRow = (label, valueFor) => {
        const cells = dates.map(d => {
          const v = valueFor(d);
          return `<td class="ct-tbl-date-group" colspan="${perDateCols}">${this._fmtMoney(v)}</td>`;
        }).join("");
        return `<tr class="ct-footer-row">
          <td class="ct-tbl-sec" colspan="2">${label}</td>
          ${cells}
        </tr>`;
      };
      // Caixa label is the wallet's `cashAccounts.unprocessedId` (the
      // value the upstream parser uses as `Ativo` for the cash row).
      // Falls back to the literal "Caixa" when the wallet has no
      // cashAccount document on file.
      const cashLabel = (w.cashUnprocessedId || "").trim() || "Caixa";
      const footerHtml = [
        footerRow("Contribuição total", d => (w.totalContributionByDate || {})[d]),
        footerRow("Provisões",          d => (w.provisionsByDate        || {})[d]),
        footerRow(this._esc(cashLabel), d => (w.cashByDate              || {})[d]),
      ].join("");

      // Edit button is only shown when the result set has exactly one
      // date — editing a range makes no sense (the upload writes to a
      // single positionDate). We still surface the unprocessedId column
      // in faixa mode so the operator can see the mapping at a glance.
      const canEdit = dates.length === 1;
      const editBtn = canEdit
        ? `<button class="ct-btn ct-btn-primary" style="margin-left:auto"
                   onclick="Carteira.enterEditMode('${this._esc(w.walletId)}','${this._esc(dates[0])}')">Editar</button>`
        : `<span class="text-[10px] text-gray-400" style="margin-left:auto">Edição disponível apenas no modo "única" data.</span>`;

      const colCount = 2 + perDateCols * dates.length;
      const errBanner = errDates.length
        ? `<div style="margin:10px 12px;padding:8px 12px;border:1px solid #fecaca;background:#fef2f2;color:#991b1b;border-radius:8px;font-size:12px">
             ⚠ <strong>Erro no arquivo de posição bruta (unprocessed)</strong> em ${this._esc(errDates.join(", "))}:
             o snapshot está vazio ou ausente, mas há posição <em>processada</em> nessa(s) data(s).
             Nenhum ativo é exibido para essa(s) data(s) — reenvie o arquivo unprocessed correto.
           </div>`
        : "";
      return `
        <div class="bg-white rounded-xl shadow overflow-hidden">
          <div class="ct-wallet-header">
            <h3>${this._esc(w.walletName)}</h3>
            <span class="ct-id">${this._esc(w.walletId)}</span>
            ${editBtn}
          </div>
          ${errBanner}
          <div style="overflow-x:scroll">
            <table class="ct-tbl">
              <thead>
                <tr>
                  <th rowspan="2" class="ct-tbl-sec" style="text-align:left">Security</th>
                  <th rowspan="2" class="ct-tbl-sec" style="text-align:left">unprocessedSecurityId</th>
                  ${dateGroupHeader}
                </tr>
                <tr>${subHeader}</tr>
              </thead>
              <tbody>
                ${secRows || `<tr><td class="ct-tbl-sec" colspan="${colCount}" style="text-align:center;color:#9ca3af;padding:12px 0">${errDates.length ? "Sem ativos exibidos — arquivo unprocessed com erro (ver aviso acima)." : "Nenhuma security em processedPosition na faixa selecionada."}</td></tr>`}
                ${footerHtml}
              </tbody>
            </table>
          </div>
        </div>`;
    },

});
