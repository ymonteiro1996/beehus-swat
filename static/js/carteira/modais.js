/* Carteira — modais: busca de security (abrir/buscar/escolher) e o
   fluxo de revisão + envio (confirmar e aplicar as posições editadas). */
Object.assign(Carteira, {
    // ── Security-search modal ────────────────────────────────────────
    openSecModal(flow, walletId, rowIndex) {
      this._st._secModal = { flow, walletId, rowIndex };
      const ctx = document.getElementById("ct-sec-modal-context");
      const editing = this._st.editing.get(walletId);
      const wname = (this._st.lastData?.wallets || []).find(x => x.walletId === walletId)?.walletName || walletId;
      ctx.textContent = `${wname} · ${editing?.targetDate || ""} · ${flow === "add" ? "Adicionar security" : "Trocar security da linha"}`;
      document.getElementById("ct-sec-modal-q").value = "";
      document.getElementById("ct-sec-modal-body").innerHTML =
        `<p class="text-center text-gray-400 py-6 text-xs">Digite algo para buscar.</p>`;
      document.getElementById("ct-sec-modal-count").textContent = "";
      const extra = document.getElementById("ct-sec-modal-extra");
      if (flow === "add") {
        extra.classList.remove("hidden");
        document.getElementById("ct-sec-modal-qty").value = "";
        document.getElementById("ct-sec-modal-pu").value = "";
      } else {
        extra.classList.add("hidden");
      }
      document.getElementById("ct-sec-modal").classList.remove("hidden");
      setTimeout(() => document.getElementById("ct-sec-modal-q").focus(), 50);
    },
    closeSecModal() {
      this._st._secModal = null;
      document.getElementById("ct-sec-modal").classList.add("hidden");
    },
    _debouncedSecSearch() {
      clearTimeout(this._st._secSearchTimer);
      this._st._secSearchTimer = setTimeout(() => this._secSearch(), 250);
    },
    async _secSearch() {
      const body = document.getElementById("ct-sec-modal-body");
      const countEl = document.getElementById("ct-sec-modal-count");
      const q = document.getElementById("ct-sec-modal-q").value.trim();
      if (!q) {
        body.innerHTML = `<p class="text-center text-gray-400 py-6 text-xs">Digite algo para buscar.</p>`;
        countEl.textContent = "";
        return;
      }
      body.innerHTML = `<p class="text-center text-gray-400 py-6 text-xs">Buscando...</p>`;
      try {
        const r = await fetch(`/api/carteira/search-securities?q=${encodeURIComponent(q)}&limit=80`);
        const data = await r.json();
        const items = data.results || [];
        countEl.textContent = `${items.length} resultado(s)`;
        if (!items.length) {
          body.innerHTML = `<p class="text-center text-gray-400 py-6 text-xs">Nenhum resultado.</p>`;
          return;
        }
        // Carry securityId/name via data-* attributes so quotes / apostrophes
        // in beehusName don't break the inline handler — see `pickSecurity`.
        body.innerHTML = `<table class="ct-sec-tbl">
          <thead><tr>
            <th>Nome</th><th>mainId</th><th>Ticker</th><th>Tipo</th><th>Vencimento</th>
          </tr></thead>
          <tbody>
            ${items.map(s => `<tr data-sid="${this._esc(s.securityId)}"
                                  data-sname="${this._esc(s.beehusName || s.mainId || s.securityId)}"
                                  onclick="Carteira.pickSecurity(this.dataset.sid, this.dataset.sname)">
              <td>${this._esc(s.beehusName || "")}</td>
              <td><span class="ct-id">${this._esc(s.mainId || "")}</span></td>
              <td>${this._esc(s.ticker || "")}</td>
              <td>${this._esc(s.securityType || "")}</td>
              <td>${this._esc((s.maturityDate || "").slice(0,10))}</td>
            </tr>`).join("")}
          </tbody></table>`;
      } catch (e) {
        body.innerHTML = `<p class="text-center text-red-600 py-6 text-xs">Erro: ${this._esc(e.message || e)}</p>`;
      }
    },
    /* Contexto: callback ao escolher uma security no modal de busca; resolve o
       unprocessedId e insere (fluxo "add") ou troca (fluxo "edit") a linha.
       Pseudocódigo:
         1. Recupera o contexto do modal e o estado de edição (aborta se ausente).
         2. Resolve unprocessedId via /lookup-mapping (fallback beehusName); se
            nada resolver, avisa e aborta.
         3. Fluxo "add": lê qtd/pu do modal, valida, barra duplicata e insere.
            Fluxo "edit": barra duplicata em outra linha e atualiza a linha.
         4. Fecha o modal e re-renderiza. */
    async pickSecurity(securityId, beehusName) {
      const ctx = this._st._secModal;
      if (!ctx) return;
      const editing = this._st.editing.get(ctx.walletId);
      if (!editing) { this.closeSecModal(); return; }
      const companyId = editing.companyId;

      // Auto-resolve `unprocessedId` via securityMappings (with beehusName
      // fallback). Failure shouldn't block the operator — surface a
      // warning instead.
      let mapping = { unprocessedId: "", beehusName: beehusName, source: "" };
      try {
        const r = await fetch(
          `/api/carteira/lookup-mapping?companyId=${encodeURIComponent(companyId)}&securityId=${encodeURIComponent(securityId)}`);
        if (r.ok) {
          mapping = await r.json();
        }
      } catch (_) { /* swallow — beehusName fallback follows */ }
      const resolvedUid = (mapping.unprocessedId || beehusName || "").trim();
      if (!resolvedUid) {
        alert("Não foi possível resolver um unprocessedId nem um beehusName para essa security.");
        return;
      }

      if (ctx.flow === "add") {
        const qty = this._parseEditNum(document.getElementById("ct-sec-modal-qty").value);
        const pu  = this._parseEditNum(document.getElementById("ct-sec-modal-pu").value);
        if (qty == null) { alert("Informe a quantidade."); return; }
        if (pu  == null) { alert("Informe o PU."); return; }
        // Prevent duplicate unprocessedId in the same wallet — the upstream
        // upload would silently collapse them.
        if (editing.rows.some(r => (r.unprocessedId || "") === resolvedUid)) {
          alert("Essa security já está na lista.");
          return;
        }
        editing.rows.push({
          securityId,
          securityName: beehusName,
          unprocessedId: resolvedUid,
          quantity: qty,
          pu,
          isNew: true,
        });
      } else {
        const row = editing.rows[ctx.rowIndex];
        if (!row) { this.closeSecModal(); return; }
        if (editing.rows.some((r, i) =>
            i !== ctx.rowIndex && (r.unprocessedId || "") === resolvedUid)) {
          alert("Essa security já está em outra linha desta carteira.");
          return;
        }
        row.securityId    = securityId;
        row.securityName  = beehusName;
        row.unprocessedId = resolvedUid;
      }
      this.closeSecModal();
      this._rerender();
    },

    // ── Confirm + apply ──────────────────────────────────────────────
    openConfirmModal(walletId) {
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      // Validation: every row needs an unprocessedId (lookup-mapping
      // shouldn't return empty, but a manual delete + re-add could in
      // theory leave one blank; better to catch here than upstream).
      const blank = editing.rows.filter(r => !(r.unprocessedId || "").trim());
      if (blank.length) {
        alert(`Há ${blank.length} linha(s) sem unprocessedId. Selecione uma security válida ou remova a linha antes de confirmar.`);
        return;
      }
      const dupes = new Set();
      const seen = new Set();
      for (const r of editing.rows) {
        const u = (r.unprocessedId || "").trim();
        if (seen.has(u)) dupes.add(u);
        seen.add(u);
      }
      if (dupes.size) {
        alert(`Ativos duplicados: ${[...dupes].join(", ")}`);
        return;
      }
      this._st._confirmWallet = walletId;
      const wData = (this._st.lastData?.wallets || []).find(x => x.walletId === walletId);
      const wname = wData?.walletName || walletId;
      const totalBal = editing.rows.reduce(
        (a, r) => a + (this._computeBalance(r) || 0), 0);

      // Compare the working `cashUnprocessedId` against the snapshot in
      // `lastData` so the preview can highlight the change (or warn that
      // it won't be sent when no cash value is set).
      const origCashUid = (wData?.cashUnprocessedId || "").trim();
      const newCashUid  = (editing.cashUnprocessedId || "").trim();
      const cashUidChanged = origCashUid !== newCashUid;
      const cashUidEffective = newCashUid || "Caixa";

      const cashSummary = editing.cash == null
        ? (cashUidChanged
            ? `<span style="color:#b91c1c">(não enviar — alteração do Ativo perdida)</span>`
            : "(não enviar)")
        : `${this._fmtMoney(editing.cash)} <span class="ct-id" style="font-size:11px;color:#374151">(Ativo: ${this._esc(cashUidEffective)}${cashUidChanged ? ' <span style="color:#16a34a">alterado</span>' : ''})</span>`;
      const headline = `<p class="text-xs text-gray-700 mb-3">
        <strong>${editing.rows.length}</strong> security(ies) ·
        Saldo total: <strong>${this._fmtMoney(totalBal)}</strong> ·
        Caixa: <strong>${cashSummary}</strong>
      </p>
      <p class="text-[11px] text-gray-500 mb-3">
        Carteira <strong>${this._esc(wname)}</strong> · Data alvo <strong>${this._esc(editing.targetDate)}</strong>
      </p>`;
      const rows = editing.rows.map(r => `<tr>
        <td>${this._esc(r.securityName || r.securityId)}</td>
        <td><span class="ct-id">${this._esc(r.unprocessedId)}</span></td>
        <td style="text-align:right">${this._fmtQty(r.quantity)}</td>
        <td style="text-align:right">${this._fmtPu(r.pu)}</td>
        <td style="text-align:right">${this._fmtMoney(this._computeBalance(r))}</td>
      </tr>`).join("");

      // Dedicated Caixa row in the preview, so the operator can see the
      // exact (Ativo, valor) pair that will be uploaded — and the change
      // is visible even when only the unprocessedId was edited.
      let cashRow = "";
      if (editing.cash != null) {
        const uidCell = cashUidChanged
          ? `<span class="ct-id" style="color:#16a34a;font-weight:600">${this._esc(cashUidEffective)}</span>
             <span style="font-size:10px;color:#6b7280">(antes: ${this._esc(origCashUid || "Caixa")})</span>`
          : `<span class="ct-id">${this._esc(cashUidEffective)}</span>`;
        cashRow = `<tr style="background:#f9fafb;font-weight:500">
          <td>Caixa</td>
          <td>${uidCell}</td>
          <td style="text-align:right;color:#9ca3af">—</td>
          <td style="text-align:right;color:#9ca3af">—</td>
          <td style="text-align:right">${this._fmtMoney(editing.cash)}</td>
        </tr>`;
      } else if (cashUidChanged) {
        // No cash value → backend skips the caixa line entirely, so the
        // unprocessedId change is lost upstream. Surface that loudly here
        // instead of letting the operator confirm a no-op.
        cashRow = `<tr style="background:#fef3c7">
          <td colspan="5" style="font-size:11px;color:#854d0e;padding:8px">
            ⚠ Alteração do Ativo da Caixa
            (<span class="ct-id">${this._esc(origCashUid || "Caixa")}</span>
             → <span class="ct-id">${this._esc(cashUidEffective)}</span>)
            <strong>não será enviada</strong> — o campo "valor" da Caixa está vazio.
            Informe o valor atual da Caixa para enviar a alteração junto.
          </td>
        </tr>`;
      }

      const bodyRows = (rows || "") + cashRow;
      document.getElementById("ct-confirm-summary").innerHTML = headline + `
        <table class="ct-sec-tbl">
          <thead><tr><th>Security</th><th>Ativo</th><th style="text-align:right">Qtd</th><th style="text-align:right">PU</th><th style="text-align:right">Saldo</th></tr></thead>
          <tbody>${bodyRows || `<tr><td colspan="5" style="text-align:center;color:#9ca3af">Sem securities (apenas caixa será enviado).</td></tr>`}</tbody>
        </table>`;
      document.getElementById("ct-confirm-modal").classList.remove("hidden");
    },
    closeConfirmModal() {
      document.getElementById("ct-confirm-modal").classList.add("hidden");
    },
    /* Contexto: envia as posições editadas como um novo unprocessedSecurityPositions
       (POST /api/carteira/apply) e recarrega a tela ao concluir.
       Pseudocódigo:
         1. Recupera a carteira/edição em confirmação (aborta se ausente).
         2. Trava reentrância e desabilita o botão.
         3. Monta o payload (securities + caixa + uid da caixa) e faz o POST.
         4. Em erro, avisa; em sucesso, fecha o modal, limpa a edição e re-busca.
         5. Sempre reabilita o botão no final. */
    async submitApply() {
      const walletId = this._st._confirmWallet;
      if (!walletId) return;
      const editing = this._st.editing.get(walletId);
      if (!editing) return;
      const btn = document.getElementById("ct-confirm-btn");
      if (this._st._submitting) return;
      this._st._submitting = true;
      btn.disabled = true;
      btn.textContent = "Enviando...";
      try {
        const payload = {
          companyId:  editing.companyId,
          walletId,
          targetDate: editing.targetDate,
          securities: editing.rows.map(r => ({
            unprocessedId: r.unprocessedId,
            quantity:      r.quantity,
            pu:            r.pu,
          })),
          cash:              editing.cash,
          cashUnprocessedId: (editing.cashUnprocessedId || "").trim(),
        };
        const r = await fetch("/api/carteira/apply", {
          method:  "POST",
          headers: {"Content-Type": "application/json"},
          body:    JSON.stringify(payload),
        });
        const data = await r.json();
        if (!r.ok || !data.ok) {
          alert("Erro no envio: " + (data.error || `HTTP ${r.status}`));
          return;
        }
        alert(`Envio concluído: ${data.rows} security(ies)${data.cashSent ? " + caixa" : ""}.`);
        this.closeConfirmModal();
        this._st.editing.delete(walletId);
        // Refresh from the backend so the wallet block reflects what was
        // just uploaded (the upstream may have re-keyed unprocessedIds).
        await this.run();
      } catch (e) {
        alert("Erro de rede: " + (e.message || e));
      } finally {
        this._st._submitting = false;
        btn.disabled = false;
        btn.textContent = "Confirmar e enviar";
      }
    },
});
