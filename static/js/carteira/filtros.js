/* Carteira — filtros: empresa/data/modo, painéis de transferência de
   groupings↔wallets, reset e montagem/validação do payload de busca. */
Object.assign(Carteira, {
    async _loadCompanies() {
      const sel = document.getElementById("ct-company");
      try {
        const r = await fetch("/api/carteira/filters/companies");
        const items = await r.json();
        sel.innerHTML = '<option value="">Selecione...</option>' +
          (Array.isArray(items) ? items : []).map(c =>
            `<option value="${this._esc(c.id)}">${this._esc(c.name || c.id)}</option>`
          ).join("");
      } catch (e) {
        sel.innerHTML = '<option value="">Erro ao carregar empresas</option>';
      }
    },

    onModeChange(mode) {
      if (mode !== "range") mode = "single";
      this._st.mode = mode;
      // `invisible` keeps the grid cell so toggling única/faixa doesn't
      // shuffle the row layout — same trick used on the /controlpanel filter.
      document.getElementById("ct-finalDate-wrap").classList.toggle("invisible", mode !== "range");
      document.getElementById("ct-mode-single").classList.toggle("active", mode === "single");
      document.getElementById("ct-mode-range").classList.toggle("active", mode === "range");
      document.getElementById("ct-initialDate-label").textContent =
        (mode === "range" ? "Data inicial *" : "Data *");
      // Plain text content won't render the red asterisk styled span;
      // rewrite as innerHTML so the * stays red.
      const lbl = document.getElementById("ct-initialDate-label");
      lbl.innerHTML = (mode === "range" ? "Data inicial" : "Data")
                    + ' <span class="text-red-500">*</span>';
    },

    toggleAdvanced() {
      const adv = document.getElementById("ct-advanced");
      const btn = document.getElementById("ct-toggle-advanced");
      const willShow = adv.classList.contains("hidden");
      adv.classList.toggle("hidden", !willShow);
      btn.innerHTML = willShow ? "− Groupings &amp; Wallets" : "+ Groupings &amp; Wallets";
    },

    async onCompanyChange() {
      const cid = document.getElementById("ct-company").value;
      this._st.groupingSelectedIds = new Set();
      this._st.walletSelectedIds   = new Set();
      const single = document.getElementById("ct-single-wallet");
      single.innerHTML = '<option value="">Todas</option>';
      if (!cid) {
        this._st.groupings = [];
        this._st.wallets   = [];
        this._renderPanes(); this._renderWalletPanes();
        return;
      }
      try {
        const [gResp, wResp] = await Promise.all([
          fetch(`/api/carteira/filters/groupings?companyId=${encodeURIComponent(cid)}`).then(r => r.json()),
          fetch(`/api/carteira/filters/wallets?companyId=${encodeURIComponent(cid)}`).then(r => r.json()),
        ]);
        this._st.groupings = Array.isArray(gResp) ? gResp : [];
        this._st.wallets   = Array.isArray(wResp) ? wResp : [];
      } catch (e) {
        this._st.groupings = [];
        this._st.wallets   = [];
      }
      // Mirror the wallet list into the single-wallet dropdown so the
      // operator can pick a single wallet without opening the advanced pane.
      single.innerHTML = '<option value="">Todas</option>' +
        this._st.wallets.map(w =>
          `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`
        ).join("");
      this._renderPanes(); this._renderWalletPanes();
    },

    // ── Groupings ↔ wallets transfer panes ───────────────────────────
    _walletFilter() {
      if (!this._st.groupingSelectedIds.size) return null;
      const ids = new Set();
      for (const g of this._st.groupings) {
        if (this._st.groupingSelectedIds.has(g.id)) {
          (g.walletIds || []).forEach(w => ids.add(w));
        }
      }
      return ids;
    },
    _renderPanes() {
      const avail = document.getElementById("ct-grp-available");
      const sel   = document.getElementById("ct-grp-selected");
      const available = this._st.groupings.filter(g => !this._st.groupingSelectedIds.has(g.id));
      const selected  = this._st.groupings.filter(g =>  this._st.groupingSelectedIds.has(g.id));
      avail.innerHTML = available.map(g => `<option value="${this._esc(g.id)}">${this._esc(g.name)}</option>`).join("");
      sel.innerHTML   = selected .map(g => `<option value="${this._esc(g.id)}">${this._esc(g.name)}</option>`).join("");
      document.getElementById("ct-grp-available-count").textContent = available.length;
      document.getElementById("ct-grp-selected-count").textContent  = selected.length;
      this._renderWalletPanes();
    },
    _renderWalletPanes() {
      const filter = this._walletFilter();
      const avail  = document.getElementById("ct-wal-available");
      const sel    = document.getElementById("ct-wal-selected");
      const available = this._st.wallets.filter(w =>
        (!filter || filter.has(w.id)) && !this._st.walletSelectedIds.has(w.id)
      );
      const selected  = this._st.wallets.filter(w => this._st.walletSelectedIds.has(w.id));
      avail.innerHTML = available.map(w => `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`).join("");
      sel.innerHTML   = selected .map(w => `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`).join("");
      document.getElementById("ct-wal-available-count").textContent = available.length;
      document.getElementById("ct-wal-selected-count").textContent  = selected.length;
    },
    _highlighted(id) {
      return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
    },
    addGroupingSelected()    { this._highlighted("ct-grp-available").forEach(id => this._st.groupingSelectedIds.add(id)); this._renderPanes(); },
    addGroupingAll()         { Array.from(document.getElementById("ct-grp-available").options).forEach(o => o.value && this._st.groupingSelectedIds.add(o.value)); this._renderPanes(); },
    removeGroupingSelected() { this._highlighted("ct-grp-selected").forEach(id => this._st.groupingSelectedIds.delete(id)); this._renderPanes(); },
    removeGroupingAll()      { this._st.groupingSelectedIds.clear(); this._renderPanes(); },
    addWalletSelected()      { this._highlighted("ct-wal-available").forEach(id => this._st.walletSelectedIds.add(id)); this._renderWalletPanes(); },
    addWalletAll()           { Array.from(document.getElementById("ct-wal-available").options).forEach(o => o.value && this._st.walletSelectedIds.add(o.value)); this._renderWalletPanes(); },
    removeWalletSelected()   { this._highlighted("ct-wal-selected").forEach(id => this._st.walletSelectedIds.delete(id)); this._renderWalletPanes(); },
    removeWalletAll()        { this._st.walletSelectedIds.clear(); this._renderWalletPanes(); },

    reset() {
      document.getElementById("ct-company").value = "";
      document.getElementById("ct-single-wallet").innerHTML = '<option value="">Todas</option>';
      this._st.groupingSelectedIds = new Set();
      this._st.walletSelectedIds   = new Set();
      this._st.groupings = [];
      this._st.wallets   = [];
      this._renderPanes(); this._renderWalletPanes();
      this.onModeChange("range");
      document.getElementById("ct-initialDate").value = this._todayISO();
      document.getElementById("ct-finalDate").value   = this._todayISO();
      const adv = document.getElementById("ct-advanced");
      const advBtn = document.getElementById("ct-toggle-advanced");
      if (adv && !adv.classList.contains("hidden")) {
        adv.classList.add("hidden");
        if (advBtn) advBtn.innerHTML = "+ Groupings &amp; Wallets";
      }
      document.getElementById("ct-results").innerHTML = "";
      document.getElementById("ct-status").textContent = "";
      document.getElementById("ct-warnings").innerHTML = "";
      const cb = document.getElementById("ct-show-contrib");
      if (cb) cb.checked = false;
      this._st.lastData = null;
    },

    /* Contexto: monta o corpo do POST /api/carteira/data a partir do estado dos
       filtros. Retorna {companyId, initialDate, finalDate, groupingIds, walletIds}.
       Pseudocódigo:
         1. Lê data inicial e, no modo "faixa", a final (senão repete a inicial).
         2. Precedência: se o avançado tem groupings/wallets, usa-os; senão usa a
            wallet única do seletor inline; senão nada (= todas as wallets). */
    _payload() {
      const ini = document.getElementById("ct-initialDate").value;
      const fin = this._st.mode === "range"
        ? document.getElementById("ct-finalDate").value
        : ini;
      // Precedence: advanced (groupings OR wallets) overrides the inline
      // single-wallet selector. With neither set, "Todas as wallets" applies.
      const advGroupings = Array.from(this._st.groupingSelectedIds);
      const advWallets   = Array.from(this._st.walletSelectedIds);
      const singleWal    = document.getElementById("ct-single-wallet").value;
      const useAdvanced  = advGroupings.length > 0 || advWallets.length > 0;
      return {
        companyId:   document.getElementById("ct-company").value,
        initialDate: ini,
        finalDate:   fin,
        groupingIds: useAdvanced ? advGroupings : [],
        walletIds:   useAdvanced ? advWallets : (singleWal ? [singleWal] : []),
      };
    },

    _validate(p) {
      if (!p.companyId)   { alert("Selecione uma empresa."); return false; }
      if (!p.initialDate) { alert("Informe a data."); return false; }
      if (this._st.mode === "range" && !p.finalDate) { alert("Informe a data final."); return false; }
      if (p.initialDate > p.finalDate) { alert("Data inicial > data final."); return false; }
      return true;
    },

    /* Contexto: ação do botão "Buscar" — busca a matriz de posições e a renderiza.
       Pseudocódigo:
         1. Monta e valida o payload dos filtros (aborta se inválido).
         2. Recolhe o painel avançado e mostra "Carregando...".
         3. POST /api/carteira/data; em erro (HTTP ou body.error), mostra aviso.
         4. Guarda a resposta em _st.lastData e atualiza o status.
         5. Sem carteiras -> mensagem vazia; senão chama _rerender(). */
});
