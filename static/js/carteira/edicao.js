/* Carteira — modo de edição: renderização editável, ciclo de vida da
   edição (entrar/cancelar), campos qtd/pu/caixa e remoção de linhas. */
Object.assign(Carteira, {
    // ── Edit-mode rendering ──────────────────────────────────────────
    _renderEditBlock(w, editing) {
      // Edit-mode columns: Security | unprocessedId | Qtd | PU | Saldo | ×
      const rowsHtml = editing.rows.map((row, i) => {
        const sId = row.securityId || "";
        const sName = row.securityName || sId || "(sem nome)";
        const newCls = row.isNew ? "ct-edit-row ct-new-row" : "ct-edit-row";
        const uidDisplay = (row.unprocessedId || "").trim()
          ? this._esc(row.unprocessedId)
          : `<span style="color:#dc2626">faltando</span>`;
        const balance = this._fmtMoney(this._computeBalance(row));
        return `<tr class="${newCls}" data-ri="${i}">
          <td class="ct-tbl-sec" style="text-align:left">
            <div class="font-medium text-gray-800">
              <a class="ct-edit-link" onclick="Carteira.openSecModal('edit','${this._esc(w.walletId)}',${i})">${this._esc(sName)}</a>
            </div>
            <div class="ct-id">${this._esc(sId)}</div>
          </td>
          <td class="ct-tbl-sec" style="text-align:left">
            <span class="ct-id" style="font-size:11px;color:#374151">${uidDisplay}</span>
          </td>
          <td><input type="text" class="ct-edit-input" value="${this._fmtEditNum(row.quantity)}"
                     oninput="Carteira.onEditField('${this._esc(w.walletId)}',${i},'quantity',this.value)" /></td>
          <td><input type="text" class="ct-edit-input" value="${this._fmtEditNum(row.pu)}"
                     oninput="Carteira.onEditField('${this._esc(w.walletId)}',${i},'pu',this.value)" /></td>
          <td>${balance}</td>
          <td style="text-align:center">
            <button class="ct-row-delete" title="Remover linha"
                    onclick="Carteira.deleteRow('${this._esc(w.walletId)}',${i})">×</button>
          </td>
        </tr>`;
      }).join("");

      // Cash row: Security cell is a fixed "Caixa" label so the operator
      // can locate the row; the unprocessedSecurityId cell is an editable
      // text input bound to `editing.cashUnprocessedId`. Empty = fall
      // back to the wallet's existing cashAccount label on apply.
      const cashFooter = `<tr class="ct-footer-row">
        <td class="ct-tbl-sec" style="text-align:left">Caixa</td>
        <td class="ct-tbl-sec" style="text-align:left">
          <input type="text" class="ct-edit-input" style="text-align:left"
                 value="${this._esc(editing.cashUnprocessedId || "")}"
                 placeholder="Caixa"
                 oninput="Carteira.onEditCashUid('${this._esc(w.walletId)}',this.value)" />
        </td>
        <td colspan="2"><input type="text" class="ct-edit-input" value="${this._fmtEditNum(editing.cash)}"
                               oninput="Carteira.onEditCash('${this._esc(w.walletId)}',this.value)" /></td>
        <td>${this._fmtMoney(editing.cash)}</td>
        <td></td>
      </tr>`;

      return `
        <div class="bg-white rounded-xl shadow overflow-hidden">
          <div class="ct-wallet-header">
            <h3>${this._esc(w.walletName)}</h3>
            <span class="ct-id">${this._esc(w.walletId)}</span>
            <span class="text-[11px] text-amber-700" style="margin-left:8px">Editando · ${this._esc(editing.targetDate)}</span>
          </div>
          <div class="ct-edit-bar">
            <span>Clique nome do ativo para trocar · qtd/pu editáveis · saldo = qtd × pu</span>
            <span class="ct-spacer"></span>
            <button class="ct-btn ct-btn-muted" onclick="Carteira.addRow('${this._esc(w.walletId)}')">+ Adicionar security</button>
            <button class="ct-btn ct-btn-muted" onclick="Carteira.cancelEdit('${this._esc(w.walletId)}')">Cancelar</button>
            <button class="ct-btn ct-btn-success" onclick="Carteira.openConfirmModal('${this._esc(w.walletId)}')">Revisar e confirmar</button>
          </div>
          <div style="overflow-x:auto">
            <table class="ct-tbl">
              <thead>
                <tr>
                  <th class="ct-tbl-sec" style="text-align:left">Security</th>
                  <th class="ct-tbl-sec" style="text-align:left">unprocessedSecurityId</th>
                  <th>Qtd</th>
                  <th>PU</th>
                  <th>Saldo</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${rowsHtml || `<tr><td colspan="6" style="text-align:center;color:#9ca3af;padding:12px 0" class="ct-tbl-sec">Nenhuma linha. Use "Adicionar security" ou cancele para voltar.</td></tr>`}
                ${cashFooter}
              </tbody>
            </table>
          </div>
        </div>`;
    },

    /* Contexto: formata número para input de edição no estilo pt-BR (vírgula
       decimal, sem zeros à direita). Delega a Fmt.formatDecimalBR. */
    _fmtEditNum(v) { return window.Fmt.formatDecimalBR(v); },
    /* Contexto: interpreta um decimal pt-BR digitado -> Number|null. Inverso de
       _fmtEditNum. Delega a Fmt.parseDecimalBR. */
    _parseEditNum(s) { return window.Fmt.parseDecimalBR(s); },
    _computeBalance(row) {
      const q = Number(row.quantity);
      const p = Number(row.pu);
      if (!isFinite(q) || !isFinite(p)) return null;
      return q * p;
    },

    // ── Edit lifecycle ───────────────────────────────────────────────

    /* Contexto: entra no modo de edição de UMA (carteira, data), criando uma
       cópia de trabalho editável — a resposta original em lastData fica intacta
       para o cancelamento reverter limpo.
       Pseudocódigo:
         1. Acha a carteira em lastData (aborta se não achar).
         2. Copia cada security como linha editável (qtd/pu default 0).
         3. Registra em _st.editing (data-alvo, linhas, caixa, companyId, uid da
            caixa) e re-renderiza. */
    enterEditMode(walletId, targetDate) {
      const w = (this._st.lastData?.wallets || []).find(x => x.walletId === walletId);
      if (!w) return;
      const cash = (w.cashByDate || {})[targetDate];
      const rows = (w.securities || []).map(s => {
        const c = (s.byDate || {})[targetDate] || {};
        const qty = (c.quantity == null) ? 0 : Number(c.quantity);
        const pu  = (c.pu       == null) ? 0 : Number(c.pu);
        return {
          securityId:    s.securityId,
          securityName:  s.securityName,
          unprocessedId: s.unprocessedId || "",
          quantity:      isFinite(qty) ? qty : 0,
          pu:             isFinite(pu)  ? pu  : 0,
          isNew:         false,
        };
      });
      this._st.editing.set(walletId, {
        targetDate,
        rows,
        cash: (cash == null ? null : Number(cash)),
        // Pull companyId out of the parent selector — every wallet on the
        // page necessarily belongs to it.
        companyId: document.getElementById("ct-company").value || "",
        // `cashAccounts.unprocessedId` is rendered in the Caixa row's
        // unprocessedSecurityId cell. Empty string is meaningful → on
        // apply, the backend re-resolves it from cashAccounts (falling
        // back to literal "Caixa" if none).
        cashUnprocessedId: (w.cashUnprocessedId || ""),
      });
      this._rerender();
    },
    cancelEdit(walletId) {
      this._st.editing.delete(walletId);
      this._rerender();
    },
    onEditField(walletId, rowIndex, field, value) {
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      const row = editing.rows[rowIndex];
      if (!row) return;
      const parsed = this._parseEditNum(value);
      row[field] = parsed == null ? 0 : parsed;
      // Update only the live Saldo cell — re-rendering the whole block
      // would steal focus from the input the operator is typing into.
      const tr = document.querySelector(`tr.ct-edit-row[data-ri="${rowIndex}"]`);
      if (tr) {
        const balCell = tr.children[4];
        if (balCell) balCell.innerHTML = this._fmtMoney(this._computeBalance(row));
      }
    },
    onEditCash(walletId, value) {
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      const parsed = this._parseEditNum(value);
      editing.cash = parsed; // null is meaningful → "don't send cash"
      // Refresh only the cash footer's read-only Saldo mirror.
      // Cash row column layout: [label][uid input][value input (colspan=2)][mirror][×]
      // so the mirror is `children[3]`.
      const block = this._editBlockEl(walletId);
      if (!block) return;
      const footRows = block.querySelectorAll("tbody tr.ct-footer-row");
      const cashRow = footRows[footRows.length - 1];
      if (cashRow && cashRow.children[3]) {
        cashRow.children[3].innerHTML = this._fmtMoney(parsed);
      }
    },
    onEditCashUid(walletId, value) {
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      // Whitespace-only input is treated as "clear" so the apply call
      // falls back to the wallet's stored cashAccount.unprocessedId.
      editing.cashUnprocessedId = String(value || "").trim();
      // No DOM mirror to refresh — the input *is* the source of truth.
    },
    deleteRow(walletId, rowIndex) {
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      const row = editing.rows[rowIndex];
      if (!row) return;
      if (!confirm(`Remover a linha "${row.securityName || row.securityId || ""}"?`)) return;
      editing.rows.splice(rowIndex, 1);
      this._rerender();
    },
    addRow(walletId) {
      this.openSecModal("add", walletId, null);
    },
    _editBlockEl(walletId) {
      // Walk the rendered blocks to find the one matching this walletId.
      // We don't carry data-* attributes on the outer card because the
      // existing non-edit renderer doesn't either; instead the wallet id
      // chip is unique per block, so anchor on that.
      const blocks = document.querySelectorAll("#ct-results > .bg-white");
      for (const b of blocks) {
        const idEl = b.querySelector(".ct-wallet-header .ct-id");
        if (idEl && idEl.textContent === walletId) return b;
      }
      return null;
    },

});
