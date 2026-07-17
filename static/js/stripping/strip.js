/* Stripping — módulo (extraído p/ static/, CLAUDE.md). Reusado por stripping.html e controlpanel.html. */
/* ════════════════════════════════════════════════════════════════════════
   Strip partial — extraído de controlpanel.html para reaproveitamento
   pela página /stripping. Estrutura: const Strip = {...} com a lista,
   wizard e apply; mais os handlers de Esc e click-outside no final.

   Quando incluído pelo controlpanel.html, `Funcoes` já está definido
   pelo script anterior e Strip.open/close usam-no pra alternar com o
   dashboard. Quando incluído pela página standalone (/stripping),
   Funcoes não existe — o guard abaixo instala stubs no-op pra que Strip
   funcione sem o shell de chips. O único método com efeito visível é
   `showIssues()`, que volta pro painel raiz.

   IMPORTANTE: não envolver este código em IIFE. Os onclick inline da
   view (Strip.open, Strip.openSetup, etc.) precisam que `const Strip`
   esteja em script-scope, não escondido em closure.
════════════════════════════════════════════════════════════════════════ */
if (typeof Funcoes === 'undefined') {
  window.Funcoes = {
    _hideAllFrames(){},
    _currentDeep: null,
    _inDaytrade:  false,
    _inStrip:     false,
    _setActiveChip(){},
    _syncResetChip(){},
    showIssues(){ window.location.href = '/'; },
  };
}

  /* ══════════════════════════════════════════════════════════════════════
     Strip — in-page port of /excecoes > Position Stripping.
     Self-contained: own DOM (#strip-view), own state, own render functions.
     Activated by the Lançamentos > Strip chip (Strip.open) and dismissed
     via the Voltar button or the Home chip (which calls Funcoes.showIssues,
     hiding this view too).
     Backend: same /api/excecoes/* endpoints used by /excecoes.
  ══════════════════════════════════════════════════════════════════════ */
  const Strip = {
    _inited: false,
    _exceptions: [],
    // Per-row checkbox selection (exception id). Used by the bulk-apply
    // toolbar; survives loadList() because IDs that no longer appear in
    // the refreshed list are pruned on render.
    _selectedIds: new Set(),
    // Per-row "Data Base" values keyed by exception id. Each row's date
    // defaults to today on first render; operator edits live here until the
    // row disappears. (Previously seeded from the source wallet's latest
    // processedPosition — that Mongo read was removed.)
    _rowDates: {},
    // Toolbar filter state — drives both the visible rows and the bulk
    // selection (rows hidden by the filter are dropped from
    // `_selectedIds` so "Aplicar selecionadas" never silently picks up
    // an off-screen row). `companyIds: null` means "no filter" (show all);
    // an empty Set means "filter active but nothing selected" → table
    // empty. The set membership is what `_isVisible(exc)` consults.
    _filter: {companyIds: null, dates: null},
    // Snapshot of "what the summary modal is showing". Frozen at the
    // moment the operator clicks "Aplicar selecionadas", so checkbox
    // changes behind the modal don't shift the lot mid-review.
    // `dates` is a per-exception {id: "YYYY-MM-DD"} map captured from the
    // table's row inputs at click time — there is no global date input.
    _bulkConfirm: {
      exceptions: [],          // [exception, …] snapshot at modal-open time
      dates: {},               // {exceptionId: "YYYY-MM-DD"} snapshot
      previews: {},            // {exceptionId: {ok, plan?, error?}}
    },
    // Setup wizard state — mirrors the _setupState object in /excecoes.
    _setup: {
      editingId: null,
      kind: "position_strip",   // "position_strip" | "wallet_slice" | "class_strip"
      companyId: "",
      sourceWalletId: "",
      outputWalletIds: [],
      walletsForCompany: [],   // [{id, name, currencyId}] — empresa da exceção (origem)
      // Lista cross-company (`/api/excecoes/wallets?crossCompany=1`)
      // usada **apenas** pelos pickers de outputs (position_strip) e
      // target (wallet_slice). Cada item carrega `companyId`/`companyName`
      // pra label "Nome (Empresa) [moeda]" e pra deixar visualmente claro
      // quando o destino está em outra empresa. O source picker continua
      // restrito a `walletsForCompany`.
      walletsCrossCompany: [], // [{id, name, currencyId, companyId, companyName}]
      sourceSecurities: [],    // [{unprocessedId, quantity, pu, balance}]
      rules: {},               // unprocessedId -> {selected, addToWalletId, removeFromWalletId, caixa}
      // wallet_slice-only:
      targetWalletId: "",
      percent: 30,
      // class_strip-only:
      classSourceIds: [],         // [walletId, …] in insertion order
      classRoutes: [],            // [{variable1, targetWalletId}] (order preserved by UI)
      classVariables: [],         // [{variable1, count, totalBalance}] from /class-strip/variables
      classGroupings: [],         // [{id, name, walletIds: [...]}] from /api/beehus/filters/groupings
      classGroupingIds: new Set(),// selected groupings → narrows the wallet "Disponíveis" pane
    },
    // Single-row apply state — set by openApplyModal, cleared on
    // closeApplyInline. `plan` is populated by _runApplyPreview and
    // consumed by runApply / downloadApplyExcel; `date` is the row's
    // Data Base at the moment Aplicar was clicked.
    _apply: {exceptionId: "", companyId: "", date: "", plan: null, fxOverrides: []},

    // Apply date-picker state — set by openApplyDates, consumed by the
    // `#sp-apply-dates` modal handlers. `candidateDates` is the business-day
    // expansion of the current range; `selectedDates` is the explicit list
    // built via the dual-pane transfer / Excel upload (only when
    // `useDateList` is on). Re-initialized on every open.
    _applyDates: {
      exceptionId: "", companyId: "", kind: "", name: "", exc: null,
      candidateDates: [], selectedDates: new Set(),
      useDateList: false, filterMonthEnd: false,
    },

    // ── Formatters / helpers (kept local so the module is self-contained) ──
    _fmtNum(v) {
      return v == null
        ? '<span class="text-gray-300">--</span>'
        : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 6, maximumFractionDigits: 6});
    },
    _fmtMoney(v) {
      return v == null
        ? '<span class="text-gray-300">--</span>'
        : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    },
    _esc(s) {
      return String(s ?? "").replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    },
    _todayISO() {
      const d = new Date();
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${dd}`;
    },
    // Date helpers for the apply date picker. Local re-implementation of
    // beehus_console's `_DateUtils` (which isn't loaded on /stripping):
    // parse a `YYYY-MM-DD` string as a *local* date (no TZ shift), expand
    // a range into business days (Mon–Fri), and test month-end-business-day.
    _parseLocalDate(s) {
      const [y, m, d] = String(s).split("-").map(Number);
      return new Date(y, m - 1, d);
    },
    _businessDays(iniStr, finStr) {
      const out = [];
      const cur = this._parseLocalDate(iniStr);
      const end = this._parseLocalDate(finStr);
      while (cur <= end) {
        const dow = cur.getDay();
        if (dow !== 0 && dow !== 6) {
          const y = cur.getFullYear();
          const m = String(cur.getMonth() + 1).padStart(2, "0");
          const d = String(cur.getDate()).padStart(2, "0");
          out.push(`${y}-${m}-${d}`);
        }
        cur.setDate(cur.getDate() + 1);
      }
      return out;
    },
    _isMonthEndBusinessDay(dateStr) {
      const d = this._parseLocalDate(dateStr);
      const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
      while (last.getDay() === 0 || last.getDay() === 6) {
        last.setDate(last.getDate() - 1);
      }
      return d.getFullYear() === last.getFullYear()
          && d.getMonth()    === last.getMonth()
          && d.getDate()     === last.getDate();
    },

    // ── Open / close the inline view ─────────────────────────────────────
    async open(btn) {
      // Same swap pattern Daytrade uses: hide every iframe tool + the
      // dashboard, show the Strip panel. Funcoes is the source of truth
      // para qual chip está ativo; `_inStrip=true` keeps the Reiniciar chip
      // wired to Strip.resetList until the operator leaves.
      //
      // Os getElementById abaixo são null-safe (`?.`) porque na página
      // standalone /stripping não existem `tool-view`, `controlpanel-view`,
      // nem `daytrade-view` — só `strip-view` (que aqui não tem a classe
      // hidden-view, mas o remove é idempotente).
      Funcoes._hideAllFrames();
      Funcoes._currentDeep = null;
      Funcoes._inDaytrade  = false;
      Funcoes._inStrip     = true;
      Funcoes._inRepetir   = false;
      document.getElementById('tool-view')?.classList.add('hidden-view');
      document.getElementById('controlpanel-view')?.classList.add('hidden-view');
      document.getElementById('daytrade-view')?.classList.add('hidden-view');
      document.getElementById('repetir-view')?.classList.add('hidden-view');
      document.getElementById('strip-view')?.classList.remove('hidden-view');
      Funcoes._setActiveChip(btn || document.getElementById('chip-strip'));
      Funcoes._syncResetChip();

      // Lazy-init: load the company list (same source Daytrade uses) once
      // per session so the setup wizard's "Empresa" select is ready before
      // the operator clicks + Nova Exceção. Retry on every open() until it
      // actually succeeds — otherwise a transient backend failure on the
      // very first visit (e.g. Mongo timeout) would permanently leave the
      // wizard's company select stuck on "Erro ao carregar empresas".
      if (!this._companiesLoaded) {
        await this._loadCompanies();
      }
      this._inited = true;
      this.loadList();
    },

    close() { Funcoes.showIssues(); },

    async _loadCompanies() {
      const sel = document.getElementById("sp-setup-company");
      try {
        // Same endpoint Daytrade uses — applies the user's company_filter
        // from settings.json automatically. Treat HTTP errors (e.g. 503 from
        // a Mongo timeout) as failures — without `r.ok` check the response
        // body would be parsed as if it were a valid company list.
        const r = await fetch("/api/beehus/filters/companies");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const items = Array.isArray(data) ? data : (data.body || []);
        items.sort((a, b) => (a.name || "").localeCompare(b.name || "", "pt-BR"));
        sel.innerHTML = '<option value="">Selecione...</option>' +
          items.map(c => `<option value="${this._esc(c.id)}">${this._esc(c.name || c.id)}</option>`).join("");
        // Mark companies as loaded; consulted by open() to decide whether
        // a future visit should re-attempt the load (e.g. after a transient
        // backend failure).
        this._companiesLoaded = true;
      } catch (e) {
        if (sel) sel.innerHTML = '<option value="">Erro ao carregar empresas</option>';
        this._companiesLoaded = false;
      }
    },

    // ── List ─────────────────────────────────────────────────────────────
    // Without `r.ok` check, a 5xx body that happens to be valid JSON gets
    // destructured as `{exceptions: undefined}` → "Nenhuma exceção
    // configurada." silently — so a transient Mongo outage looks like a
    // clean empty list. Force the error path on any non-2xx response.
    loadList() {
      const msg = document.getElementById("sp-msg");
      const tbody = document.getElementById("sp-tbody");
      if (msg) {
        msg.textContent = "Carregando...";
        msg.style.display = "block";
      }
      if (tbody) tbody.innerHTML = "";
      fetch("/api/excecoes")
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then(({ exceptions }) => {
          this._exceptions = exceptions || [];
          // Wrap renderList in its own try so a row-render exception doesn't
          // get masked by the network-error catch below (which previously
          // swallowed everything silently and made "list not loading" look
          // like a fetch failure with no console trace).
          try {
            this.renderList();
          } catch (err) {
            console.error("Strip.renderList failed:", err);
            if (msg) {
              msg.innerHTML =
                'Falha ao renderizar a lista. ' +
                '<button type="button" onclick="Strip.loadList()" ' +
                'class="text-blue-600 hover:underline ml-1">Tentar novamente</button>';
              msg.style.display = "block";
            }
          }
        })
        .catch(err => {
          // Render an inline retry link instead of a dead-end message so the
          // operator can recover without a full reload (the standalone page
          // has no "Reiniciar" chip like controlpanel does). Log the error
          // so transient network issues are diagnosable in the console.
          console.error("Strip.loadList fetch failed:", err);
          if (!msg) return;
          msg.innerHTML =
            'Erro ao carregar exceções. ' +
            '<button type="button" onclick="Strip.loadList()" ' +
            'class="text-blue-600 hover:underline ml-1">Tentar novamente</button>';
          msg.style.display = "block";
        });
    },

    // Reiniciar chip handler when in the Strip view: clear the bulk
    // selection + results panel, reload the list, and dismiss any open
    // modals (setup + bulk-confirm) so the operator gets a fresh start
    // without leaving the panel.
    resetList() {
      this.closeSetup();
      this.closeBulkConfirm();
      this.closeApplyModal();
      this._selectedIds = new Set();
      // Wipe row-level Data Base values so each row falls back to today
      // again on the next render.
      this._rowDates = {};
      // Clear the toolbar filters too — Reiniciar means "fresh start",
      // including any company or date narrowing the operator had
      // applied on the previous session.
      this._filter = {companyIds: null, dates: null};
      const compPanel = document.getElementById("sp-filter-companies-panel");
      if (compPanel) compPanel.classList.add("hidden");
      const datePanel = document.getElementById("sp-filter-dates-panel");
      if (datePanel) datePanel.classList.add("hidden");
      const res = document.getElementById("sp-results");
      if (res) res.classList.add("hidden");
      const status = document.getElementById("sp-bulk-status");
      if (status) status.textContent = "";
      this.loadList();
    },

    renderList() {
      const tbody = document.getElementById("sp-tbody");
      const msg   = document.getElementById("sp-msg");
      // Prune selections + row-date values for exceptions that
      // disappeared since the last render (deleted, or visibility
      // narrowed). Keeps the bulk count honest and drops stale row
      // dates for rows that no longer exist.
      const liveIds = new Set(this._exceptions.map(e => e.id));
      for (const id of [...this._selectedIds]) {
        if (!liveIds.has(id)) this._selectedIds.delete(id);
      }
      this._rowDates = this._rowDates || {};
      for (const id of Object.keys(this._rowDates)) {
        if (!liveIds.has(id)) delete this._rowDates[id];
      }

      // Refresh the company-filter popover whenever the underlying list
      // changes — keeps the checkbox list in sync with what's actually
      // returned by /api/excecoes.
      this._refreshCompanyFilterPanel();
      // Drop selections for rows that the company filter is currently
      // hiding. "Aplicar selecionadas" should only ever act on visible
      // rows; if the operator narrows the filter, those rows visually
      // disappear so they must also leave the selection.
      const visibleSet = new Set(this._visibleExceptions().map(e => e.id));
      for (const id of [...this._selectedIds]) {
        if (!visibleSet.has(id)) this._selectedIds.delete(id);
      }

      if (!this._exceptions.length) {
        tbody.innerHTML = "";
        msg.textContent = "Nenhuma exceção configurada.";
        msg.style.display = "block";
        this._refreshBulkButton();
        return;
      }
      const visible = this._visibleExceptions();
      if (!visible.length) {
        tbody.innerHTML = "";
        msg.textContent = "Nenhuma exceção visível com o filtro atual.";
        msg.style.display = "block";
        this._refreshBulkButton();
        this._refreshMaster();
        return;
      }
      msg.style.display = "none";
      tbody.innerHTML = visible.map(e => {
        const last = e.lastApplied
          ? `${this._esc(e.lastApplied.date || "")} <span class="text-[10px] text-gray-400">(${this._esc((e.lastApplied.at || "").slice(0,10))})</span>`
          : '<span class="text-gray-300">--</span>';
        const checked = this._selectedIds.has(e.id) ? "checked" : "";
        // Data Base column removed — the date is chosen at apply time
        // (single: Aplicar panel; bulk: single picker in the confirm modal).
        // Kind-aware columns:
        //   wallet_slice       → single target + percent
        //   class_strip        → first source + "+N" / first target + "+N" / route count
        //   position_strip (default) → output list + ruleCount
        const isSlice   = e.kind === "wallet_slice";
        const isClass   = e.kind === "class_strip";
        let kindPill, sourceCell, outsCell, ruleCell;
        if (isClass) {
          kindPill = '<span class="sp-pill sp-pill-warn">Classe</span>';
          const srcs  = e.sourceWalletNames || [];
          const tgts  = e.uniqueTargetNames || [];
          const srcFirst = srcs[0] || "";
          const tgtFirst = tgts[0] || "";
          const srcMore = srcs.length > 1 ? ` <span class="text-[10px] text-gray-400">+${srcs.length - 1}</span>` : "";
          const tgtMore = tgts.length > 1 ? ` <span class="text-[10px] text-gray-400">+${tgts.length - 1}</span>` : "";
          sourceCell = `${this._esc(srcFirst)}${srcMore}`;
          outsCell   = `${this._esc(tgtFirst)}${tgtMore}`;
          ruleCell   = String((e.classRoutes || []).length);
        } else {
          kindPill = isSlice
            ? '<span class="sp-pill sp-pill-info">Fatiar carteira</span>'
            : '<span class="sp-pill sp-pill-muted">Strip</span>';
          sourceCell = this._esc(e.sourceWalletName);
          outsCell = isSlice
            ? `${this._esc(e.targetWalletName || "")}`
            : `${(e.outputWalletNames || []).map(n => this._esc(n)).join(", ")}`;
          ruleCell = isSlice
            ? `<span class="font-mono">${this._esc((Number(e.percent || 0)).toLocaleString("pt-BR", {minimumFractionDigits: 0, maximumFractionDigits: 4}))}%</span>`
            : String(e.ruleCount);
        }
        return `<tr class="border-t border-gray-100 hover:bg-gray-50">
          <td class="px-3 py-2 text-center">
            <input type="checkbox" ${checked}
              data-sp-id="${this._esc(e.id)}"
              data-sp-cid="${this._esc(e.companyId)}"
              onchange="Strip.onRowToggle(this)" />
          </td>
          <td class="px-3 py-2">${kindPill}</td>
          <td class="px-3 py-2 text-gray-700">${this._esc(e.companyName)}</td>
          <td class="px-3 py-2 font-medium text-gray-800">${this._esc(e.name)}</td>
          <td class="px-3 py-2 text-gray-700">${sourceCell}</td>
          <td class="px-3 py-2 text-gray-700">${outsCell}</td>
          <td class="px-3 py-2 text-right">${ruleCell}</td>
          <td class="px-3 py-2 text-gray-700">${last}</td>
          <td class="px-3 py-2 text-center">
            <button onclick="Strip.openApplyModal('${this._esc(e.id)}', '${this._esc(e.companyId)}')"
              class="bg-emerald-600 hover:bg-emerald-700 text-white rounded px-2 py-1 text-[11px] mr-1">Aplicar</button>
            <button onclick="Strip.editException('${this._esc(e.id)}', '${this._esc(e.companyId)}')"
              class="bg-blue-600 hover:bg-blue-700 text-white rounded px-2 py-1 text-[11px] mr-1">Editar</button>
            <button onclick="Strip.deleteException('${this._esc(e.id)}', '${this._esc(e.companyId)}')"
              class="bg-red-600 hover:bg-red-700 text-white rounded px-2 py-1 text-[11px]">Excluir</button>
          </td>
        </tr>`;
      }).join("");
      this._refreshBulkButton();
      this._refreshMaster();
      this._refreshDateFilterPanel();
    },

    // Operator edit on the per-row Data Base input — store it so that
    // re-renders (toggleAll, refreshes after apply) don't wipe the value,
    // and so bulk/single apply read the live edit instead of the default.
    // Refreshes the toolbar date filter panel so
    // newly-introduced dates show up as options and stale ones fall off
    // (and any active date-filter selection that no longer matches any
    // row gets pruned in _refreshDateFilterPanel).
    onRowDateChange(input) {
      const id = input.dataset.spId;
      if (!id) return;
      this._rowDates = this._rowDates || {};
      this._rowDates[id] = input.value || "";
      // IMPORTANT: do **not** call `renderList()` here. The native
      // `<input type="date">` fires `onchange` every time the date becomes
      // valid mid-typing (e.g. after the day is entered, then again after
      // the month, then again after the year). Re-rendering the whole
      // tbody on each fire destroys the input element under the operator's
      // cursor and they can't finish typing — exactly the "atualização
      // está muito rápida" symptom.
      //
      // Bookkeeping that's safe to do now:
      //   - `_rowDates[id]` is updated so single/bulk apply read the live
      //     value when they fire later.
      //   - Refresh the date-filter popover so newly-introduced dates show
      //     up as options. That refresh only touches the popover, not the
      //     table rows, so it can't steal focus from the active input.
      //
      // Visibility (the date filter narrowing the visible rows) is
      // recomputed on the next natural re-render — i.e. when the operator
      // ticks another checkbox, opens a popover, or hits "Atualizar".
      // Trading a tiny lag in filter application for a usable date input
      // is the right call.
      this._refreshDateFilterPanel();
    },

    // Master checkbox in the header — selects/unselects every **visible**
    // row in the current list (visibility honours the company filter).
    // Rows hidden by the filter are not touched because they wouldn't be
    // included in "Aplicar selecionadas" anyway.
    toggleAll(checked) {
      const visibleIds = this._visibleExceptions().map(e => e.id);
      if (checked) {
        for (const id of visibleIds) this._selectedIds.add(id);
      } else {
        for (const id of visibleIds) this._selectedIds.delete(id);
      }
      this.renderList();
    },

    onRowToggle(cb) {
      const id = cb.dataset.spId;
      if (cb.checked) this._selectedIds.add(id);
      else            this._selectedIds.delete(id);
      this._refreshBulkButton();
      this._refreshMaster();
    },

    _refreshBulkButton() {
      const n = this._selectedIds.size;
      const count = document.getElementById("sp-bulk-count");
      const btn   = document.getElementById("sp-bulk-apply-btn");
      if (count) count.textContent = String(n);
      if (btn)   btn.disabled = (n === 0);
    },

    _refreshMaster() {
      const master = document.getElementById("sp-master");
      if (!master) return;
      // Master reflects the **visible** rows only — when a filter is
      // active, "select all" semantics should ignore rows the operator
      // can't see. Without this, a filter narrowing the visible set to
      // 3 would leave the master un-checked even after all 3 boxes
      // were ticked.
      const visible = this._visibleExceptions();
      const total = visible.length;
      const sel   = visible.filter(e => this._selectedIds.has(e.id)).length;
      master.checked       = (sel === total && total > 0);
      master.indeterminate = (sel > 0 && sel < total);
    },

    // ── Toolbar filters ──────────────────────────────────────────────────
    // `_visibleExceptions()` is the single source of truth for "what
    // the operator sees" — both rendering and the master checkbox /
    // bulk-apply count consult it so visibility and action stay aligned.
    // Combines the company filter AND the date filter. A row's "date"
    // for filtering purposes is its rendered Data Base value
    // (`_rowDates[id]`) — the same value the operator sees in the table.
    // The date filter is pure (read-only): it hides rows, it never
    // mutates `_rowDates`.
    _visibleExceptions() {
      const compSet = this._filter && this._filter.companyIds;
      const dateSet = this._filter && this._filter.dates;
      let rows = this._exceptions;
      if (compSet) rows = rows.filter(e => compSet.has(e.companyId));
      if (dateSet && dateSet.size) {
        rows = rows.filter(e => {
          const d = (this._rowDates && this._rowDates[e.id]) || "";
          return d && dateSet.has(d);
        });
      }
      return rows.slice ? rows.slice() : [...rows];
    },

    toggleCompanyFilter() {
      const panel = document.getElementById("sp-filter-companies-panel");
      if (!panel) return;
      panel.classList.toggle("hidden");
    },

    _refreshCompanyFilterPanel() {
      const list = document.getElementById("sp-filter-companies-list");
      const label = document.getElementById("sp-filter-companies-label");
      if (!list) return;
      // Build the company set from the currently-loaded exceptions —
      // filtering on companies that have no exception to begin with
      // would just be noise.
      const byId = new Map();
      for (const e of this._exceptions) {
        if (e.companyId && !byId.has(e.companyId)) {
          byId.set(e.companyId, e.companyName || e.companyId);
        }
      }
      const companies = [...byId.entries()]
        .map(([id, name]) => ({id, name}))
        .sort((a, b) => (a.name || "").localeCompare(b.name || "", "pt-BR"));

      // Drop any selections that no longer correspond to a live company
      // (e.g. the operator deleted every exception of a company).
      if (this._filter.companyIds) {
        const live = new Set(companies.map(c => c.id));
        for (const cid of [...this._filter.companyIds]) {
          if (!live.has(cid)) this._filter.companyIds.delete(cid);
        }
        if (!this._filter.companyIds.size) this._filter.companyIds = null;
      }

      list.innerHTML = companies.map(c => {
        const checked = (this._filter.companyIds && this._filter.companyIds.has(c.id))
          ? "checked" : "";
        return `<label class="flex items-center gap-2 py-1 cursor-pointer text-xs text-gray-700">
          <input type="checkbox" ${checked} value="${this._esc(c.id)}"
            onchange="Strip.onCompanyFilterToggle(this)" />
          <span>${this._esc(c.name)}</span>
        </label>`;
      }).join("");

      // Label: "todas" when no filter; otherwise the selected count.
      if (label) {
        const sel = this._filter.companyIds;
        label.textContent = (sel && sel.size)
          ? `${sel.size} selecionada(s)`
          : "todas";
      }
    },

    onCompanyFilterToggle(cb) {
      // Lazily allocate the Set — the moment the operator ticks any
      // checkbox the filter goes active; unticking the last one
      // restores the "all visible" default.
      if (!this._filter.companyIds) this._filter.companyIds = new Set();
      if (cb.checked) this._filter.companyIds.add(cb.value);
      else            this._filter.companyIds.delete(cb.value);
      if (!this._filter.companyIds.size) this._filter.companyIds = null;
      // Auto-pick: when the operator narrows by company, also pre-check
      // every row that becomes (or stays) visible — the request was for
      // the company filter to "mark which will be executed". Hidden rows
      // were already pruned in renderList().
      const visibleIds = this._exceptions
        .filter(e => !this._filter.companyIds || this._filter.companyIds.has(e.companyId))
        .map(e => e.id);
      if (this._filter.companyIds) {
        // Filter active — pre-select the visible subset so "Aplicar
        // selecionadas" reflects the company pick out of the box. The
        // operator can still untick individual rows afterwards.
        this._selectedIds = new Set(visibleIds);
      }
      this.renderList();
    },

    setCompanyFilterAll(allOn) {
      // "Marcar todas" wires the filter to every loaded company id —
      // not the same as no filter, because the operator may delete a
      // company afterwards and the filter should still narrow to the
      // explicitly-picked ids. "Limpar" returns to the unfiltered
      // default and clears the bulk selection.
      if (allOn) {
        const all = new Set();
        for (const e of this._exceptions) {
          if (e.companyId) all.add(e.companyId);
        }
        this._filter.companyIds = all.size ? all : null;
        const visibleIds = this._exceptions.map(e => e.id);
        this._selectedIds = new Set(visibleIds);
      } else {
        this._filter.companyIds = null;
        this._selectedIds = new Set();
      }
      this.renderList();
    },

    // ── Date filter (read-only popover) ──────────────────────────────────
    // Mirrors the company filter: popover + checkboxes. Pure filter —
    // never writes to `_rowDates`. The option set is derived from the
    // Data Base values currently shown in rows that pass the *company*
    // filter (the date filter doesn't feed back into its own options;
    // that would let a single tick collapse the rest of the list to a
    // single-date choice).
    toggleDateFilter() {
      const panel = document.getElementById("sp-filter-dates-panel");
      if (!panel) return;
      panel.classList.toggle("hidden");
    },

    _refreshDateFilterPanel() {
      const list = document.getElementById("sp-filter-dates-list");
      const label = document.getElementById("sp-filter-dates-label");
      if (!list) return;

      // Source rows = company-filter-pass-through only. We deliberately
      // don't apply the date filter here so unticking + ticking another
      // date keeps the full option set in view.
      const compSet = this._filter && this._filter.companyIds;
      const sourceRows = compSet
        ? this._exceptions.filter(e => compSet.has(e.companyId))
        : this._exceptions;

      const dates = new Set();
      for (const e of sourceRows) {
        const d = (this._rowDates && this._rowDates[e.id]) || "";
        if (d) dates.add(d);
      }
      const sorted = [...dates].sort().reverse();

      // Prune stale selections — dates that are no longer present in
      // any visible-by-company row (e.g. operator edited the row date
      // away). Otherwise the label count would lie.
      if (this._filter.dates) {
        const live = new Set(sorted);
        for (const d of [...this._filter.dates]) {
          if (!live.has(d)) this._filter.dates.delete(d);
        }
        if (!this._filter.dates.size) this._filter.dates = null;
      }

      const fmt = (iso) => {
        // ISO yyyy-mm-dd → dd/mm/yyyy for pt-BR display. Fallback to raw
        // value so a malformed date is still pickable rather than hidden.
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
        return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
      };

      list.innerHTML = sorted.map(d => {
        const checked = (this._filter.dates && this._filter.dates.has(d))
          ? "checked" : "";
        return `<label class="flex items-center gap-2 py-1 cursor-pointer text-xs text-gray-700">
          <input type="checkbox" ${checked} value="${this._esc(d)}"
            onchange="Strip.onDateFilterToggle(this)" />
          <span>${this._esc(fmt(d))}</span>
        </label>`;
      }).join("") || '<p class="text-[11px] text-gray-400 py-1">Nenhuma data.</p>';

      if (label) {
        const sel = this._filter.dates;
        label.textContent = (sel && sel.size)
          ? `${sel.size} selecionada(s)`
          : "todas";
      }
    },

    onDateFilterToggle(cb) {
      if (!this._filter.dates) this._filter.dates = new Set();
      if (cb.checked) this._filter.dates.add(cb.value);
      else            this._filter.dates.delete(cb.value);
      if (!this._filter.dates.size) this._filter.dates = null;
      this.renderList();
    },

    setDateFilterAll(allOn) {
      // "Marcar todas" = explicitly select every date currently offered
      // (under the company filter). "Limpar" = back to no filter. The
      // explicit-set choice matters because a later row-date edit can
      // introduce a new date — that new date will only show up if the
      // filter is in "all" mode (null), not in an "all currently-known"
      // explicit Set. We keep parity with the company filter behaviour
      // (which also stores the explicit set) so the UX is consistent.
      if (allOn) {
        const compSet = this._filter && this._filter.companyIds;
        const sourceRows = compSet
          ? this._exceptions.filter(e => compSet.has(e.companyId))
          : this._exceptions;
        const all = new Set();
        for (const e of sourceRows) {
          const d = (this._rowDates && this._rowDates[e.id]) || "";
          if (d) all.add(d);
        }
        this._filter.dates = all.size ? all : null;
      } else {
        this._filter.dates = null;
      }
      this.renderList();
    },

    deleteException(id, companyId) {
      if (!confirm("Excluir esta exceção?")) return;
      fetch(`/api/excecoes/${encodeURIComponent(id)}?companyId=${encodeURIComponent(companyId)}`, {method: "DELETE"})
        .then(r => r.json())
        .then(d => {
          if (d.error) { alert(d.error); return; }
          this.loadList();
        });
    },

    // ── Single-row apply (inline preview panel) ──────────────────────────
    // Clicking "Aplicar" on a row fires /preview immediately with the
    // row's Data Base and renders the wallet + transaction breakdown
    // inline (no modal). The operator then confirms / downloads / closes
    // from the panel header.
    openApplyModal(id, companyId) {
      const exc = (this._exceptions || []).find(e => e.id === id);
      // strip / slice / class — let the operator pick a single date (which
      // keeps the rich inline preview below) or a range / explicit list of
      // dates (sequential apply, no per-date preview). The date picker
      // decides which path to take when the operator confirms.
      this.openApplyDates(id, companyId, exc);
    },

    // Rich single-date preview flow. Reached from the date picker
    // (confirmApplyDates) when the operator resolves exactly one date:
    // sets `_apply`, renders the inline preview panel, then fires /preview.
    _startApplyInline(id, companyId, date) {
      const exc = (this._exceptions || []).find(e => e.id === id);

      this._apply = {exceptionId: id, companyId, date, plan: null, fxOverrides: []};

      // The multi-date results panel and the single-date inline panel are
      // mutually exclusive — hide the former when opening the latter.
      document.getElementById("sp-apply-multi").classList.add("hidden");

      // Reset + show the inline panel before firing /preview so the user
      // sees the loading state next to the row instead of a blank gap.
      const panel = document.getElementById("sp-apply-inline");
      panel.classList.remove("hidden");
      document.getElementById("sp-apply-name").textContent = exc ? exc.name : "";
      document.getElementById("sp-apply-subtitle").textContent = exc
        ? `${exc.companyName} — origem: ${exc.sourceWalletName}`
        : "";
      document.getElementById("sp-apply-date-pill").textContent = date;
      document.getElementById("sp-apply-warnings").innerHTML =
        '<span class="text-xs text-gray-400">Calculando pré-visualização...</span>';
      document.getElementById("sp-apply-preview").classList.add("hidden");
      document.getElementById("sp-apply-slice").classList.add("hidden");
      document.getElementById("sp-apply-slice-summary").innerHTML = "";
      document.getElementById("sp-apply-slice-sec-tbody").innerHTML = "";
      document.getElementById("sp-apply-slice-prov-tbody").innerHTML = "";
      document.getElementById("sp-apply-slice-tx-tbody").innerHTML = "";
      // class_strip preview block — reset to empty so reopening the panel
      // on a different exception doesn't briefly flash the previous one.
      const csClass = document.getElementById("sp-apply-class");
      if (csClass) {
        csClass.classList.add("hidden");
        const csSum = document.getElementById("sp-apply-class-summary");
        const csSrc = document.getElementById("sp-apply-class-sources");
        const csTgt = document.getElementById("sp-apply-class-targets");
        if (csSum) csSum.innerHTML = "";
        if (csSrc) csSrc.innerHTML = "";
        if (csTgt) csTgt.innerHTML = "";
      }
      document.getElementById("sp-apply-results").classList.add("hidden");
      document.getElementById("sp-apply-results-list").innerHTML = "";
      document.getElementById("sp-apply-wallets").innerHTML = "";
      document.getElementById("sp-apply-transactions").classList.add("hidden");
      document.getElementById("sp-apply-tx-tbody").innerHTML = "";
      document.getElementById("sp-apply-run-btn").disabled = true;
      document.getElementById("sp-apply-download-btn").disabled = true;
      // Bring it into view — useful when the table is tall and the panel
      // sits below the fold.
      try { panel.scrollIntoView({behavior: "smooth", block: "start"}); } catch (e) {}

      this._runApplyPreview();
    },

    closeApplyInline() {
      document.getElementById("sp-apply-inline").classList.add("hidden");
      this._apply = {exceptionId: "", companyId: "", date: "", plan: null, fxOverrides: []};
    },

    // Kept as an alias so the Escape handler + resetList still find it.
    closeApplyModal() { this.closeApplyInline(); },

    // ── Apply date picker (#sp-apply-dates) ──────────────────────────────
    // Opened for strip / slice / class rows from openApplyModal. Lets the
    // operator pick a single date (→ inline preview) or a range / explicit
    // date list (→ sequential apply). Mirrors "Painel de Controle >
    // Processar" date controls.
    openApplyDates(id, companyId, exc) {
      exc = exc || (this._exceptions || []).find(e => e.id === id);
      const base = (this._rowDates && this._rowDates[id])
        || this._todayISO();
      this._applyDates = {
        exceptionId: id, companyId,
        kind: (exc && exc.kind) || "",
        name: (exc && exc.name) || "",
        exc: exc || null,
        candidateDates: [], selectedDates: new Set(),
        useDateList: false, filterMonthEnd: false,
      };
      // Reset controls to single-date mode seeded with the row's Data Base.
      const single = document.querySelector('input[name="sp-apply-dates-mode"][value="single"]');
      if (single) single.checked = true;
      document.getElementById("sp-apply-dates-ini").value = base;
      document.getElementById("sp-apply-dates-fin").value = "";
      document.getElementById("sp-apply-dates-uselist").checked = false;
      document.getElementById("sp-apply-dates-monthend").checked = false;
      document.getElementById("sp-apply-dates-excel-status").textContent = "";
      document.getElementById("sp-apply-dates-status").textContent = "";
      document.getElementById("sp-apply-dates-subtitle").textContent = exc
        ? `${exc.companyName || ""} — ${exc.name || ""}`
        : "";
      this.onApplyDatesModeChange("single");
      const modal = document.getElementById("sp-apply-dates");
      modal.classList.remove("hidden");
      modal.classList.add("flex");
    },

    closeApplyDates() {
      const modal = document.getElementById("sp-apply-dates");
      modal.classList.add("hidden");
      modal.classList.remove("flex");
    },

    _applyDatesMode() {
      const r = document.querySelector('input[name="sp-apply-dates-mode"]:checked');
      return r ? r.value : "single";
    },

    onApplyDatesModeChange(mode) {
      const isRange = mode === "range";
      document.getElementById("sp-apply-dates-fin-wrap").classList.toggle("hidden", !isRange);
      document.getElementById("sp-apply-dates-ini-label").textContent = isRange ? "Data inicial" : "Data";
      document.getElementById("sp-apply-dates-card").classList.toggle("hidden", !isRange);
      if (!isRange) {
        // Leaving range mode resets the explicit-list state so a later
        // switch back doesn't resurrect a stale selection.
        this._applyDates.useDateList = false;
        document.getElementById("sp-apply-dates-uselist").checked = false;
        document.getElementById("sp-apply-dates-body").classList.add("hidden");
      }
      this.onApplyDatesRangeChange();
    },

    onApplyDatesRangeChange() {
      if (this._applyDatesMode() === "range") {
        const ini = document.getElementById("sp-apply-dates-ini").value;
        const fin = document.getElementById("sp-apply-dates-fin").value;
        this._applyDates.candidateDates = (ini && fin && ini <= fin)
          ? this._businessDays(ini, fin) : [];
      } else {
        this._applyDates.candidateDates = [];
      }
      if (this._applyDates.useDateList) this._renderApplyDatesPanes();
    },

    onApplyDatesUseListChange() {
      const on = document.getElementById("sp-apply-dates-uselist").checked;
      this._applyDates.useDateList = on;
      document.getElementById("sp-apply-dates-body").classList.toggle("hidden", !on);
      if (!on) {
        this._applyDates.selectedDates = new Set();
        this._applyDates.filterMonthEnd = false;
        const me = document.getElementById("sp-apply-dates-monthend"); if (me) me.checked = false;
        const xs = document.getElementById("sp-apply-dates-excel-status"); if (xs) xs.textContent = "";
      } else {
        this.onApplyDatesRangeChange();
      }
      this._renderApplyDatesPanes();
    },

    onApplyDatesFilterChange() {
      this._applyDates.filterMonthEnd = document.getElementById("sp-apply-dates-monthend").checked;
      this._renderApplyDatesPanes();
    },

    _renderApplyDatesPanes() {
      const avail = document.getElementById("sp-apply-dates-available");
      const sel   = document.getElementById("sp-apply-dates-selected");
      if (!avail || !sel) return;
      if (!this._applyDates.useDateList) {
        avail.innerHTML = ""; sel.innerHTML = "";
        document.getElementById("sp-apply-dates-available-count").textContent = "0";
        document.getElementById("sp-apply-dates-selected-count").textContent  = "0";
        document.getElementById("sp-apply-dates-monthend-badge").classList.add("hidden");
        return;
      }
      let candidates = this._applyDates.candidateDates;
      if (this._applyDates.filterMonthEnd) {
        candidates = candidates.filter(d => this._isMonthEndBusinessDay(d));
      }
      const selSet    = this._applyDates.selectedDates;
      const available = candidates.filter(d => !selSet.has(d));
      const selected  = Array.from(selSet).sort();
      avail.innerHTML = available.map(d => `<option value="${d}">${d}</option>`).join("");
      sel.innerHTML   = selected.map(d  => `<option value="${d}">${d}</option>`).join("");
      document.getElementById("sp-apply-dates-available-count").textContent = available.length;
      document.getElementById("sp-apply-dates-selected-count").textContent  = selected.length;
      document.getElementById("sp-apply-dates-monthend-badge").classList.toggle("hidden", !this._applyDates.filterMonthEnd);
    },

    _applyDatesHighlighted(elId) {
      return Array.from(document.getElementById(elId).selectedOptions).map(o => o.value);
    },
    applyDatesAddSelected()   { this._applyDatesHighlighted("sp-apply-dates-available").forEach(d => this._applyDates.selectedDates.add(d)); this._renderApplyDatesPanes(); },
    applyDatesAddAll()        { Array.from(document.getElementById("sp-apply-dates-available").options).forEach(o => this._applyDates.selectedDates.add(o.value)); this._renderApplyDatesPanes(); },
    applyDatesRemoveSelected(){ this._applyDatesHighlighted("sp-apply-dates-selected").forEach(d => this._applyDates.selectedDates.delete(d)); this._renderApplyDatesPanes(); },
    applyDatesRemoveAll()     { this._applyDates.selectedDates.clear(); this._renderApplyDatesPanes(); },

    async onApplyDatesExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = document.getElementById("sp-apply-dates-excel-status");
      statusEl.textContent = "Processando…";
      const fd = new FormData();
      fd.append("file", file);
      try {
        const r = await fetch("/api/beehus/util/parse-dates-excel", {method: "POST", body: fd});
        const body = await r.json().catch(() => null);
        if (!r.ok) {
          statusEl.textContent = `falha (${r.status})`;
          alert("Falha ao ler o Excel: " + ((body && body.error) || r.status));
          return;
        }
        const dates = (body && body.dates) || [];
        if (!dates.length) { statusEl.textContent = "nenhuma data encontrada"; return; }
        // Make sure the explicit-list pane is on so the operator sees the
        // uploaded dates land in "Selecionadas".
        if (!this._applyDates.useDateList) {
          document.getElementById("sp-apply-dates-uselist").checked = true;
          this.onApplyDatesUseListChange();
        }
        dates.forEach(d => this._applyDates.selectedDates.add(d));
        this._renderApplyDatesPanes();
        statusEl.textContent = `${dates.length} data(s) adicionadas a "Selecionadas"`;
      } catch (e) {
        statusEl.textContent = "erro de rede";
        alert("Erro de rede ao subir Excel: " + e);
      } finally {
        ev.target.value = "";
      }
    },

    // Resolve the operator's choice into a sorted list of YYYY-MM-DD days.
    _resolveApplyDates() {
      if (this._applyDatesMode() === "single") {
        const d = document.getElementById("sp-apply-dates-ini").value;
        return d ? [d] : [];
      }
      if (this._applyDates.useDateList && this._applyDates.selectedDates.size > 0) {
        return Array.from(this._applyDates.selectedDates).sort();
      }
      const ini = document.getElementById("sp-apply-dates-ini").value;
      const fin = document.getElementById("sp-apply-dates-fin").value;
      if (!ini || !fin || ini > fin) return [];
      return this._businessDays(ini, fin);
    },

    confirmApplyDates() {
      const status = document.getElementById("sp-apply-dates-status");
      const explicit = this._applyDates.useDateList && this._applyDates.selectedDates.size > 0;
      if (this._applyDatesMode() === "range") {
        const ini = document.getElementById("sp-apply-dates-ini").value;
        const fin = document.getElementById("sp-apply-dates-fin").value;
        if (!explicit) {
          if (!ini || !fin) { status.textContent = "Informe data inicial e final."; return; }
          if (ini > fin)    { status.textContent = "Data inicial não pode ser maior que a final."; return; }
        }
      } else if (!document.getElementById("sp-apply-dates-ini").value) {
        status.textContent = "Informe a data.";
        return;
      }

      const days = this._resolveApplyDates();
      if (!days.length) {
        status.textContent = explicit
          ? 'Selecione pelo menos uma data ou desmarque "Selecionar datas específicas".'
          : "Não há dias úteis na faixa informada.";
        return;
      }

      const {exceptionId, companyId, exc} = this._applyDates;
      if (days.length === 1) {
        // Single date keeps the rich inline preview / "Confirmar e enviar".
        this.closeApplyDates();
        this._startApplyInline(exceptionId, companyId, days[0]);
        return;
      }
      // Multiple dates → confirm, then sequential direct apply.
      if (!confirm(`Aplicar "${(exc && exc.name) || "exceção"}" em ${days.length} data(s)?\n` +
                   `O envio é sequencial e cada data envia para a API Beehus.`)) return;
      this.closeApplyDates();
      this._runMultiDateApply(exc || {id: exceptionId, companyId, name: ""}, companyId, days);
    },

    // ── Multi-date apply (#sp-apply-multi) ───────────────────────────────
    // Sequential POST /api/excecoes/<id>/apply, one per date — same
    // contract the bulk-apply loop uses (sequential, 401 breaks the rest).
    // No per-date preview: each row updates pendente → enviando → ok/erro.
    async _runMultiDateApply(exc, companyId, days) {
      const id = exc.id || (this._applyDates && this._applyDates.exceptionId);
      // Mutually exclusive with the single-date inline panel.
      document.getElementById("sp-apply-inline").classList.add("hidden");
      const panel = document.getElementById("sp-apply-multi");
      panel.classList.remove("hidden");
      document.getElementById("sp-apply-multi-name").textContent = exc.name || "";
      document.getElementById("sp-apply-multi-subtitle").textContent =
        `${exc.companyName || ""} — ${days.length} data(s) · envio sequencial`;
      document.getElementById("sp-apply-multi-summary").innerHTML =
        '<span class="sp-pill sp-pill-muted">enviando…</span>';
      const tbody = document.getElementById("sp-apply-multi-tbody");
      tbody.innerHTML = days.map(d => `
        <tr data-sp-md-day="${this._esc(d)}" class="border-t border-gray-100">
          <td class="px-3 py-2 font-mono text-[11px] text-gray-700">${this._esc(d)}</td>
          <td class="px-3 py-2"><span class="sp-pill sp-pill-muted">pendente</span></td>
          <td class="px-3 py-2 text-gray-400">—</td>
        </tr>`).join("");
      try { panel.scrollIntoView({behavior: "smooth", block: "start"}); } catch (e) {}

      // Operator-supplied FX overrides accumulate across the whole run so a
      // pair already prompted for date D1 doesn't need to be re-typed for D2
      // — though dates differ in the key, so in practice each date prompts
      // for its own pairs unless the operator reuses the same date by hand.
      let fxOverrides = [];
      let okCount = 0, errCount = 0, authBroke = false, done = 0;
      for (const day of days) {
        this._setMultiDayRow(day, "running", "");
        document.getElementById("sp-apply-multi-summary").innerHTML =
          `<span class="sp-pill sp-pill-muted">${done}/${days.length}</span>`;
        let ok = false, data = {}, httpStatus = 0;
        // Inner loop only for the manual-FX retry path — bounded so a buggy
        // backend can't trap the operator into an endless prompt cycle.
        for (let attempt = 0; attempt < 5; attempt++) {
          try {
            const r = await fetch(`/api/excecoes/${encodeURIComponent(id)}/apply`, {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({companyId, date: day, fxOverrides}),
            });
            httpStatus = r.status;
            data = await r.json().catch(() => ({}));
            ok = r.ok && !(data && data.error);
          } catch (err) {
            data = {error: "rede: " + (err.message || "")};
            httpStatus = 0; ok = false;
          }
          if (data && Array.isArray(data.pendingFxRates) && data.pendingFxRates.length) {
            const collected = await this._promptForFxRates(data.pendingFxRates);
            if (!collected) {
              // Operator cancelled — fall through, this date is marked error.
              data = {error: "Conversão cancelada (sem taxa informada)."};
              ok = false;
              break;
            }
            fxOverrides = fxOverrides.concat(collected);
            continue;  // retry this date with the new overrides
          }
          break;
        }
        done++;
        if (ok) {
          okCount++;
          this._setMultiDayRow(day, "ok", this._applyDaySummary(data));
        } else {
          errCount++;
          const isAuth = httpStatus === 401;
          this._setMultiDayRow(day, isAuth ? "auth" : "error",
            `<span class="text-rose-700">${this._esc((data && data.error) || (isAuth ? "auth" : `HTTP ${httpStatus}`))}</span>`);
          if (isAuth) { authBroke = true; break; }
        }
      }

      const skipped = days.length - okCount - errCount;
      const summary = [`<span class="sp-pill sp-pill-success">${okCount} ok</span>`];
      if (errCount) summary.push(`<span class="sp-pill sp-pill-error">${errCount} com erro</span>`);
      if (skipped)  summary.push(`<span class="sp-pill sp-pill-warn">${skipped} não tentada(s)${authBroke ? " (auth break)" : ""}</span>`);
      document.getElementById("sp-apply-multi-summary").innerHTML = summary.join(" ");

      // Reflect updated lastApplied on the rows.
      this.loadList();
    },

    _setMultiDayRow(day, state, detail) {
      const tr = document.querySelector(`#sp-apply-multi-tbody tr[data-sp-md-day="${day}"]`);
      if (!tr) return;
      const cls   = ({pending: "muted", running: "info", ok: "success", error: "error", auth: "warn"})[state] || "muted";
      const label = ({pending: "pendente", running: "enviando…", ok: "ok", error: "erro", auth: "auth"})[state] || state;
      const cells = tr.children;
      cells[1].innerHTML = `<span class="sp-pill sp-pill-${cls}">${label}</span>`;
      cells[2].innerHTML = detail || (state === "running"
        ? '<span class="text-gray-400">…</span>'
        : '<span class="text-gray-400">—</span>');
    },

    // Compact per-date detail for the multi-date table — counts of ok/total
    // per result bucket, dispatched by kind (same buckets the single-date
    // result renderers read).
    _applyDaySummary(data) {
      if (!data) return '<span class="text-gray-400">—</span>';
      const kind = data.kind || "";
      const okOf = (rows) => (rows || []).filter(r => r.status === "ok").length;
      const pills = [];
      const add = (rows, label) => {
        const total = (rows || []).length;
        if (!total) return;
        const n = okOf(rows);
        const cls = (n === total) ? "success" : (n === 0 ? "error" : "warn");
        pills.push(`<span class="sp-pill sp-pill-${cls}">${label}: ${n}/${total}</span>`);
      };
      if (kind === "wallet_slice") {
        const pos = data.positionResult;
        if (pos) {
          const cls = pos.status === "ok" ? "success" : (pos.status === "skipped" ? "muted" : "error");
          pills.push(`<span class="sp-pill sp-pill-${cls}">posição: ${this._esc(pos.status)}</span>`);
        }
        add(data.provisionResults,   "provisions");
        add(data.transactionResults, "transações");
      } else if (kind === "class_strip") {
        add(data.sourceResults,      "origens");
        add(data.targetResults,      "destinos");
        add(data.provisionResults,   "provisions");
        add(data.transactionResults, "transações");
        const adj = data.adjustmentResults || {};
        add([].concat(adj.amountDifference || [], adj.provisionSettlement || [], adj.transactionSettlement || []), "ajustes");
        add(data.processResults,     "process");
        add(data.navResults,         "nav");
      } else {
        add(data.results,            "posições");
        add(data.transactionResults, "transações");
      }
      return pills.length ? pills.join(" ") : '<span class="sp-pill sp-pill-muted">nada a enviar</span>';
    },

    closeApplyMulti() {
      document.getElementById("sp-apply-multi").classList.add("hidden");
    },

    // ── Manual FX-rate prompt (#sp-fx-prompt) ────────────────────────────
    // The conversion layer surfaces uncovered (from, to, date) triples in
    // `pendingFxRates`; this modal collects one rate per row and resolves
    // a promise so the caller (preview / apply / multi-date apply) can
    // resubmit with `fxOverrides`. Returns null on cancel.
    _promptForFxRates(pending) {
      return new Promise((resolve) => {
        this._fxPromptResolve = resolve;
        // The strip's `from/to` are *source/target wallet currencies* (the
        // direction the conversion flows). For human input we flip the
        // display so the rate matches market convention: when BRL is in
        // the pair we always show "1 {non-BRL} = X BRL" (the way "dólar a
        // R$5,00" is quoted in Brazil); otherwise we keep source→target.
        // The override is sent back using the *displayed* base/quote, and
        // `_fx_rate` on the server already does an inverse lookup when the
        // conversion direction is the opposite of the stored pair — so the
        // wire shape is symmetric regardless of which side the operator
        // typed.
        this._fxPromptPending = (pending || []).map(p => {
          const from = p.from || "", to = p.to || "", date = p.date || "";
          let base, quote;
          if (to === "BRL")        { base = from; quote = "BRL"; }
          else if (from === "BRL") { base = to;   quote = "BRL"; }
          else                     { base = from; quote = to; }
          return {from, to, date, base, quote};
        });
        const list = document.getElementById("sp-fx-prompt-list");
        list.innerHTML = this._fxPromptPending.map((p, i) => `
          <div class="flex items-start gap-2 text-xs" data-sp-fx-i="${i}">
            <span class="flex-1 min-w-0">
              <span class="font-mono text-[11px] text-gray-700">${this._esc(p.base)} / ${this._esc(p.quote)}</span>
              <span class="text-gray-400 ml-1">em ${this._esc(p.date)}</span>
              <div class="text-[10px] text-gray-500">1 ${this._esc(p.base)} = X ${this._esc(p.quote)}</div>
            </span>
            <input type="number" step="any" min="0" data-sp-fx-rate
                   class="border rounded px-2 py-1 text-xs w-32 text-right"
                   placeholder="ex.: 4,9587" />
          </div>`).join("");
        document.getElementById("sp-fx-prompt-status").textContent = "";
        const modal = document.getElementById("sp-fx-prompt");
        modal.classList.remove("hidden");
        modal.classList.add("flex");
        const first = document.querySelector("#sp-fx-prompt-list [data-sp-fx-rate]");
        if (first) try { first.focus(); } catch (e) {}
      });
    },

    confirmFxPrompt() {
      const inputs = document.querySelectorAll("#sp-fx-prompt-list [data-sp-fx-rate]");
      const overrides = [];
      for (let i = 0; i < inputs.length; i++) {
        const raw = (inputs[i].value || "").replace(",", ".");
        const rate = parseFloat(raw);
        if (!isFinite(rate) || rate <= 0) {
          document.getElementById("sp-fx-prompt-status").textContent =
            'Informe uma taxa positiva para todos os pares.';
          try { inputs[i].focus(); } catch (e) {}
          return;
        }
        // Submit using the *displayed* base/quote (what the operator typed).
        // `_fx_rate` on the server inverts automatically when the strip's
        // conversion direction is the opposite of the stored pair, so e.g.
        // a BRL→USD strip with the override stored as USD/BRL yields the
        // correct multiplier (1/rate) without the operator having to invert
        // mentally.
        const p = this._fxPromptPending[i] || {};
        overrides.push({from: p.base, to: p.quote, date: p.date, rate});
      }
      this._closeFxPrompt();
      const r = this._fxPromptResolve;
      this._fxPromptResolve = null;
      this._fxPromptPending = null;
      if (r) r(overrides);
    },

    cancelFxPrompt() {
      this._closeFxPrompt();
      const r = this._fxPromptResolve;
      this._fxPromptResolve = null;
      this._fxPromptPending = null;
      if (r) r(null);
    },

    _closeFxPrompt() {
      const modal = document.getElementById("sp-fx-prompt");
      modal.classList.add("hidden");
      modal.classList.remove("flex");
    },

    async _runApplyPreview() {
      const {exceptionId, companyId, date} = this._apply || {};
      if (!exceptionId || !date) return;

      // The fx override list accumulates across retries so the operator only
      // types a missing rate once even if the preview is re-fired multiple
      // times for the same exception/date.
      this._apply.fxOverrides = this._apply.fxOverrides || [];

      try {
        const r = await fetch(`/api/excecoes/${encodeURIComponent(exceptionId)}/preview`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({companyId, date, fxOverrides: this._apply.fxOverrides}),
        });
        const d = await r.json().catch(() => ({}));

        // Manual FX prompt path: backend asks the operator for the missing
        // pairs, we resubmit with the collected overrides. A cancel from the
        // operator leaves the preview in the unconverted error state.
        if (d && Array.isArray(d.pendingFxRates) && d.pendingFxRates.length) {
          const collected = await this._promptForFxRates(d.pendingFxRates);
          if (!collected) {
            document.getElementById("sp-apply-warnings").innerHTML =
              '<span class="sp-pill sp-pill-error">Conversão de câmbio cancelada — pré-visualização interrompida.</span>';
            return;
          }
          this._apply.fxOverrides = this._apply.fxOverrides.concat(collected);
          return this._runApplyPreview();
        }

        if (!r.ok || (d && d.error)) {
          document.getElementById("sp-apply-warnings").innerHTML =
            `<span class="sp-pill sp-pill-error">${this._esc((d && d.error) || "Falha")}</span>`;
          return;
        }
        this._apply.plan = d;
        if (d.kind === "wallet_slice") {
          this._renderSlicePreview(d);
        } else if (d.kind === "class_strip") {
          this._renderClassStripPreview(d);
        } else {
          this._renderApplyPreview(d);
        }
        document.getElementById("sp-apply-run-btn").disabled = false;
        document.getElementById("sp-apply-download-btn").disabled = false;
      } catch (e) {
        document.getElementById("sp-apply-warnings").innerHTML =
          '<span class="sp-pill sp-pill-error">Erro de rede ao gerar pré-visualização.</span>';
      }
    },

    // ── Slice preview rendering ──────────────────────────────────────────
    _renderSlicePreview(plan) {
      // Toggle the two preview shells — the strip block stays hidden when
      // the active plan is a slice, and vice versa.
      document.getElementById("sp-apply-preview").classList.add("hidden");
      document.getElementById("sp-apply-slice").classList.remove("hidden");

      const warns = [];
      if (plan.fallback) {
        warns.push(`<span class="sp-pill sp-pill-warn">Posição da origem usou fallback: ${this._esc(plan.sourceDate)} (alvo: ${this._esc(plan.targetDate)})</span>`);
      }
      const wn = plan.walletNames || {};
      warns.push(`<span class="sp-pill sp-pill-info">${this._esc(wn[plan.sourceWalletId] || plan.sourceWalletId)} → ${this._esc(wn[plan.targetWalletId] || plan.targetWalletId)} · ${this._fmtMoney(plan.percent)}%</span>`);
      document.getElementById("sp-apply-warnings").innerHTML = warns.join(" ");

      const sum = plan.summary || {source: {}, sliced: {}};
      const src = sum.source || {}, sli = sum.sliced || {};
      const card = (label, srcVal, sliVal, opts) => {
        const isCash = !!(opts && opts.cash);
        const srcText = (isCash && srcVal == null)
          ? '<span class="text-gray-300">sem cashAccount</span>'
          : this._fmtMoney(srcVal);
        const sliText = (isCash && sliVal == null)
          ? '<span class="text-gray-300">--</span>'
          : this._fmtMoney(sliVal);
        return `<div class="border rounded p-2 bg-gray-50">
          <p class="text-[10px] uppercase tracking-wide text-gray-500">${this._esc(label)}</p>
          <p class="text-[11px] text-gray-700">Origem: <strong>${srcText}</strong></p>
          <p class="text-[11px] text-emerald-700">Fatia: <strong>${sliText}</strong></p>
        </div>`;
      };
      document.getElementById("sp-apply-slice-summary").innerHTML =
          card("NAV",          src.nav,          sli.nav)
        + card("Securities",   src.securities,   sli.securities)
        + card("CashAccount",  src.cashAccount,  sli.cashAccount, {cash: true})
        + card("Provisions",   src.provisions,   sli.provisions)
        + card("Transactions", src.transactions, sli.transactions);

      // Securities + Caixa list
      const secs = (plan.securities || []).slice();
      if (plan.cashAccount) secs.push({...plan.cashAccount, _isCash: true});
      const secTbody = document.getElementById("sp-apply-slice-sec-tbody");
      const secMsg   = document.getElementById("sp-apply-slice-sec-msg");
      const secCount = document.getElementById("sp-apply-slice-sec-count");
      secCount.textContent = `(${secs.length})`;
      if (!secs.length) {
        secTbody.innerHTML = "";
        secMsg.textContent = "Nenhum ativo ou caixa para fatiar nesta data.";
        secMsg.classList.remove("hidden");
      } else {
        secMsg.classList.add("hidden");
        secTbody.innerHTML = secs.map(s => {
          const cls = s._isCash ? "bg-amber-50/40" : "";
          const ativo = s._isCash
            ? `<span class="font-mono">${this._esc(s.unprocessedId)}</span> <span class="sp-pill sp-pill-warn">Caixa</span>`
            : `<span class="font-mono">${this._esc(s.unprocessedId)}</span>`;
          const qtyS = s._isCash ? '<span class="text-gray-300">--</span>' : this._fmtNum(s.sourceQuantity);
          const qtyT = s._isCash ? '<span class="text-gray-300">--</span>' : this._fmtNum(s.quantity);
          const puT  = s._isCash ? '<span class="text-gray-300">--</span>' : this._fmtNum(s.pu);
          return `<tr class="border-t border-gray-100 ${cls}">
            <td class="px-2 py-1">${ativo}</td>
            <td class="px-2 py-1 text-right">${qtyS}</td>
            <td class="px-2 py-1 text-right">${qtyT}</td>
            <td class="px-2 py-1 text-right">${puT}</td>
            <td class="px-2 py-1 text-right">${this._fmtMoney(s.sourceBalance)}</td>
            <td class="px-2 py-1 text-right">${this._fmtMoney(s.balance)}</td>
            <td class="px-2 py-1 text-center">${s._isCash ? "Sim" : "Não"}</td>
            <td class="px-2 py-1">${this._esc(s.currencyId || "")}</td>
          </tr>`;
        }).join("");
      }

      // Provisions
      const provs = plan.provisions || [];
      const provTbody = document.getElementById("sp-apply-slice-prov-tbody");
      const provMsg   = document.getElementById("sp-apply-slice-prov-msg");
      document.getElementById("sp-apply-slice-prov-count").textContent = `(${provs.length})`;
      if (!provs.length) {
        provTbody.innerHTML = "";
        provMsg.textContent = "Nenhuma provision ativa nesta data.";
        provMsg.classList.remove("hidden");
      } else {
        provMsg.classList.add("hidden");
        provTbody.innerHTML = provs.map(p => `<tr class="border-t border-gray-100">
          <td class="px-2 py-1 text-gray-700">${this._esc(p.description)}</td>
          <td class="px-2 py-1 text-gray-600">${this._esc(p.provisionType || "")}</td>
          <td class="px-2 py-1 text-right">${this._fmtMoney(p.sourceBalance)}</td>
          <td class="px-2 py-1 text-right">${this._fmtMoney(p.balance)}</td>
          <td class="px-2 py-1">${this._esc(p.initialDate)}</td>
          <td class="px-2 py-1">${this._esc(p.liquidationDate)}</td>
          <td class="px-2 py-1">${this._esc(p.currencyId || "")}</td>
        </tr>`).join("");
      }

      // Transactions
      const txs = plan.transactions || [];
      const txTbody = document.getElementById("sp-apply-slice-tx-tbody");
      const txMsg   = document.getElementById("sp-apply-slice-tx-msg");
      const sn = plan.securityNames || {};
      document.getElementById("sp-apply-slice-tx-count").textContent = `(${txs.length})`;
      if (!txs.length) {
        txTbody.innerHTML = "";
        txMsg.textContent = "Nenhuma transação com liquidationDate nesta data.";
        txMsg.classList.remove("hidden");
      } else {
        txMsg.classList.add("hidden");
        txTbody.innerHTML = txs.map(t => {
          const secName = t.securityId ? (sn[t.securityId] || t.securityId) : '<span class="text-gray-300">--</span>';
          const qtyS = t.sourceQuantity == null ? '<span class="text-gray-300">--</span>' : this._fmtNum(t.sourceQuantity);
          const qtyT = t.quantity        == null ? '<span class="text-gray-300">--</span>' : this._fmtNum(t.quantity);
          return `<tr class="border-t border-gray-100">
            <td class="px-2 py-1">${secName}</td>
            <td class="px-2 py-1 text-gray-600">${this._esc(t.beehusTransactionType || "")}</td>
            <td class="px-2 py-1 text-right">${this._fmtMoney(t.sourceBalance)}</td>
            <td class="px-2 py-1 text-right">${this._fmtMoney(t.balance)}</td>
            <td class="px-2 py-1 text-right">${qtyS}</td>
            <td class="px-2 py-1 text-right">${qtyT}</td>
            <td class="px-2 py-1">${this._esc(t.liquidationDate)}</td>
            <td class="px-2 py-1 text-gray-600">${this._esc(t.description || "")}</td>
          </tr>`;
        }).join("");
      }
    },

    _renderSliceResults(d) {
      const list = document.getElementById("sp-apply-results-list");
      let html = "";
      if (d.error) {
        html += `<p class="sp-pill sp-pill-error">${this._esc(d.error)}</p>`;
      }
      const wn = (this._apply && this._apply.plan && this._apply.plan.walletNames) || {};
      const sn = (this._apply && this._apply.plan && this._apply.plan.securityNames) || {};

      const pos = d.positionResult;
      if (pos) {
        const cls = pos.status === "ok" ? "success" : (pos.status === "skipped" ? "muted" : "error");
        const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(pos.status)}</span>`;
        const wname = wn[pos.walletId] || pos.walletId;
        let extra = "";
        if (pos.status === "ok") extra = ` — ${pos.rows} linha(s)`;
        else if (pos.error)  extra = ` — ${this._esc(pos.error)}`;
        else if (pos.reason) extra = ` — ${this._esc(pos.reason)}`;
        html += `<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Posições</p>`;
        html += `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(wname)}</strong>${extra}</div>`;
      }

      const provs = d.provisionResults || [];
      if (provs.length) {
        html += '<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">Provisions</p>';
        html += provs.map(r => {
          const cls = r.status === "ok" ? "success" : "error";
          const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
          const extra = r.error ? ` — ${this._esc(r.error)}` : "";
          return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(r.description || "")}</strong> · ${this._fmtMoney(r.balance)}${extra}</div>`;
        }).join("");
      }

      const txs = d.transactionResults || [];
      if (txs.length) {
        html += '<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">Transactions</p>';
        html += txs.map(r => {
          const cls = r.status === "ok" ? "success" : "error";
          const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
          const secName = r.securityId ? (sn[r.securityId] || r.securityId) : "";
          const extra = r.error ? ` — ${this._esc(r.error)}` : "";
          return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(secName || r.description || "")}</strong> · ${this._esc(r.type || "")} · ${this._fmtMoney(r.balance)}${extra}</div>`;
        }).join("");
      }

      list.innerHTML = html || '<p class="text-xs text-gray-400">Nenhum resultado.</p>';
    },

    // ── class_strip preview rendering ────────────────────────────────────
    // 3-section panel: summary pills, per-source matched/skipped cards, and
    // per-target consolidated upload + provisions/transactions/adjustments.
    // Mirrors the backend `perSource[]`/`perTarget[]` arrays one-to-one so
    // the operator sees exactly what each carteira will receive.
    _renderClassStripPreview(plan) {
      document.getElementById("sp-apply-preview").classList.add("hidden");
      document.getElementById("sp-apply-slice").classList.add("hidden");
      document.getElementById("sp-apply-class").classList.remove("hidden");

      const wn = plan.walletNames || {};
      const sn = plan.securityNames || {};
      const summary = plan.summary || {};

      const warns = [];
      const fallbackSources = (plan.perSource || []).filter(ps => ps.fallback);
      if (fallbackSources.length) {
        warns.push(`<span class="sp-pill sp-pill-warn">${fallbackSources.length} origem(ns) com fallback de data — veja abaixo.</span>`);
      }
      const skippedTotal = (plan.perSource || []).reduce((acc, ps) => acc + (ps.skipped || []).length, 0);
      if (skippedTotal) {
        warns.push(`<span class="sp-pill sp-pill-muted">${skippedTotal} ativo(s) sem rota / sem mapping ignorado(s).</span>`);
      }
      document.getElementById("sp-apply-warnings").innerHTML = warns.join(" ");

      const sumEl = document.getElementById("sp-apply-class-summary");
      const sumPills = [];
      sumPills.push(`<span class="sp-pill sp-pill-info">${(plan.sourceWalletIds || []).length} origem(ns)</span>`);
      sumPills.push(`<span class="sp-pill sp-pill-info">${(plan.perTarget || []).length} destino(s)</span>`);
      sumPills.push(`<span class="sp-pill sp-pill-success">${summary.matchedSecurities || 0} ativo(s) matched</span>`);
      if (summary.migratedProvisions)     sumPills.push(`<span class="sp-pill sp-pill-info">${summary.migratedProvisions} provision(s)</span>`);
      if (summary.migratedTransactions)   sumPills.push(`<span class="sp-pill sp-pill-info">${summary.migratedTransactions} transação(ões)</span>`);
      if (summary.adjustmentTransactions) sumPills.push(`<span class="sp-pill sp-pill-info">${summary.adjustmentTransactions} ajuste(s)</span>`);
      if (sumEl) sumEl.innerHTML = sumPills.join(" ");

      // Per-source cards.
      const sourcesEl = document.getElementById("sp-apply-class-sources");
      if (sourcesEl) {
        sourcesEl.innerHTML = (plan.perSource || []).map(ps => {
          const matchedRows = (ps.matched || []).map(m => {
            // PU pill — distinguishes whether the value came from the
            // operator's unprocessed upload (canonical) vs the processed
            // fallback (rare). Operators need to know when they're about
            // to push a derived PU back into the destination.
            const puPill = m.puSource === "processed"
              ? '<span class="sp-pill sp-pill-warn ml-1" title="PU vindo de processedPosition (sem unprocessed correspondente).">PU proc</span>'
              : '';
            return `
            <tr class="border-t border-gray-100">
              <td class="px-2 py-1 text-gray-700">${this._esc(sn[m.securityId] || m.securityId)}</td>
              <td class="px-2 py-1 text-[11px] text-gray-500">${this._esc(m.variable1)}</td>
              <td class="px-2 py-1 text-gray-700">${this._esc(wn[m.targetWalletId] || m.targetWalletId)}</td>
              <td class="px-2 py-1 text-right">${this._fmtNum(m.quantity)}</td>
              <td class="px-2 py-1 text-right">${this._fmtNum(m.pu)}${puPill}</td>
              <td class="px-2 py-1 text-right">${this._fmtMoney(m.balance)}</td>
              <td class="px-2 py-1 text-right text-gray-500">${m.amountDifference ? this._fmtNum(m.amountDifference) : '<span class="text-gray-300">--</span>'}</td>
            </tr>`;
          }).join("");
          const skippedHtml = (ps.skipped || []).length
            ? `<details class="mt-1"><summary class="text-[10px] text-gray-500 cursor-pointer">${ps.skipped.length} ativo(s) sem rota</summary>
                <ul class="text-[10px] text-gray-500 pl-4 mt-1 space-y-0.5">
                  ${ps.skipped.map(s => `<li>${this._esc(sn[s.securityId] || s.securityId)} <span class="text-gray-400">— ${this._esc(s.variable1 || "(sem variable1)")} · ${this._esc(s.reason)}</span></li>`).join("")}
                </ul></details>`
            : "";
          const fb = ps.fallback
            ? `<span class="sp-pill sp-pill-warn ml-2" title="Posição usou fallback de data">fallback ${this._esc(ps.sourceDate)}</span>`
            : `<span class="sp-pill sp-pill-muted ml-2">${this._esc(ps.sourceDate || "")}</span>`;
          return `<div class="border rounded">
            <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center flex-wrap gap-1">
              <span><span class="sp-pill sp-pill-info">origem</span> ${this._esc(wn[ps.walletId] || ps.walletId)}${fb}</span>
              <span class="text-gray-400 font-normal text-[10px]">${(ps.matched || []).length} matched · ${(ps.skipped || []).length} sem rota</span>
            </div>
            <div class="px-3 py-2">
              ${(ps.matched || []).length ? `<table class="w-full text-xs"><thead class="bg-gray-50 text-gray-500 uppercase"><tr>
                <th class="px-2 py-1 text-left min-w-[160px]">Ativo</th>
                <th class="px-2 py-1 text-left min-w-[140px]">variable1</th>
                <th class="px-2 py-1 text-left min-w-[160px]">Destino</th>
                <th class="px-2 py-1 text-right min-w-[100px]">Quant.</th>
                <th class="px-2 py-1 text-right min-w-[110px]">PU (unp.)</th>
                <th class="px-2 py-1 text-right min-w-[110px]">Saldo</th>
                <th class="px-2 py-1 text-right min-w-[100px]">amountDiff</th>
              </tr></thead><tbody>${matchedRows}</tbody></table>` : '<p class="text-[11px] text-gray-400">Nenhum ativo matched para esta origem.</p>'}
              ${skippedHtml}
            </div>
          </div>`;
        }).join("");
      }

      // Per-target cards.
      const targetsEl = document.getElementById("sp-apply-class-targets");
      if (targetsEl) {
        targetsEl.innerHTML = (plan.perTarget || []).map(pt => {
          const adj = pt.adjustments || {};
          const adjAmount = adj.amountDifference || [];
          const adjProv   = adj.provisionSettlement || [];
          const adjTx     = adj.transactionSettlement || [];
          const secRows = (pt.securities || []).map(s => `
            <tr class="border-t border-gray-100">
              <td class="px-2 py-1 text-gray-700">${this._esc(s.securityName || s.securityId)}</td>
              <td class="px-2 py-1 text-right">${this._fmtNum(s.quantity)}</td>
              <td class="px-2 py-1 text-right">${this._fmtNum(s.pu)}</td>
              <td class="px-2 py-1 text-right">${this._fmtMoney(s.balance)}</td>
            </tr>`).join("");
          const provRows = (pt.provisions || []).map(p => `
            <tr class="border-t border-gray-100">
              <td class="px-2 py-1 text-[11px] text-gray-700">${this._esc(sn[p.securityId] || p.securityId || "—")}</td>
              <td class="px-2 py-1 text-[11px] text-gray-700">${this._esc(p.provisionType)}</td>
              <td class="px-2 py-1 text-[11px] text-gray-500">${this._esc(p.description)}</td>
              <td class="px-2 py-1 text-right">${this._fmtMoney(p.balance)}</td>
              <td class="px-2 py-1 text-[11px] text-gray-500">${this._esc(p.liquidationDate)}</td>
            </tr>`).join("");
          const txRows = (pt.transactions || []).map(t => `
            <tr class="border-t border-gray-100">
              <td class="px-2 py-1 text-[11px] text-gray-700">${this._esc(sn[t.securityId] || t.securityId || "—")}</td>
              <td class="px-2 py-1 text-[11px] text-gray-700">${this._esc(t.beehusTransactionType)}</td>
              <td class="px-2 py-1 text-[11px] text-gray-500">${this._esc(t.description)}</td>
              <td class="px-2 py-1 text-right">${this._fmtMoney(t.balance)}</td>
              <td class="px-2 py-1 text-[11px] text-gray-500">${this._esc(t.liquidationDate)}</td>
            </tr>`).join("");
          const adjAmountHtml = adjAmount.length ? `
            <details class="mt-2"><summary class="text-[11px] font-semibold text-gray-700 cursor-pointer">Ajustes (i) — amountDifference (${adjAmount.length})</summary>
              <ul class="text-[11px] text-gray-700 pl-4 mt-1 space-y-0.5">
                ${adjAmount.map(a => `<li>${this._esc(a.securityName || a.securityId)} · ${this._fmtNum(a.amountDifference)} × ${this._fmtNum(a.executionPrice)} = <strong>${this._fmtMoney(a.balance)}</strong></li>`).join("")}
              </ul></details>` : "";
          const adjProvHtml = adjProv.length ? `
            <details class="mt-2"><summary class="text-[11px] font-semibold text-gray-700 cursor-pointer">Ajustes (ii) — provisões liquidando (${adjProv.length})</summary>
              <ul class="text-[11px] text-gray-700 pl-4 mt-1 space-y-0.5">
                ${adjProv.map(a => `<li>${this._esc(a.description)} · <strong>${this._fmtMoney(a.balance)}</strong></li>`).join("")}
              </ul></details>` : "";
          const adjTxHtml = adjTx.length ? `
            <details class="mt-2"><summary class="text-[11px] font-semibold text-gray-700 cursor-pointer">Ajustes (iii) — transações liquidando (${adjTx.length})</summary>
              <ul class="text-[11px] text-gray-700 pl-4 mt-1 space-y-0.5">
                ${adjTx.map(a => `<li>${this._esc(a.description)} · <strong>${this._fmtMoney(a.balance)}</strong></li>`).join("")}
              </ul></details>` : "";
          return `<div class="border rounded">
            <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center flex-wrap gap-2">
              <span><span class="sp-pill sp-pill-success">destino</span> ${this._esc(pt.walletName || pt.walletId)}
                <span class="text-[10px] text-gray-400 ml-1">[${this._esc(pt.currencyId)}]</span>
                <span class="ml-2 text-[10px] text-gray-500">${(pt.variable1s || []).map(v => this._esc(v)).join(", ")}</span></span>
              <span class="text-gray-400 font-normal text-[10px]">${(pt.securities || []).length} ativo(s) · ${(pt.provisions || []).length} provision(s) · ${(pt.transactions || []).length} transação(ões) · ${adjAmount.length + adjProv.length + adjTx.length} ajuste(s)</span>
            </div>
            <div class="px-3 py-2 space-y-2">
              ${(pt.securities || []).length ? `<div><p class="text-[11px] font-semibold text-gray-700 mb-1">Securities a receber</p>
                <table class="w-full text-xs"><thead class="bg-gray-50 text-gray-500 uppercase"><tr>
                  <th class="px-2 py-1 text-left min-w-[160px]">Ativo</th>
                  <th class="px-2 py-1 text-right min-w-[100px]">Quant.</th>
                  <th class="px-2 py-1 text-right min-w-[100px]">PU</th>
                  <th class="px-2 py-1 text-right min-w-[120px]">Saldo</th>
                </tr></thead><tbody>${secRows}</tbody></table></div>` : ""}
              ${(pt.provisions || []).length ? `<div><p class="text-[11px] font-semibold text-gray-700 mb-1">Provisions a migrar</p>
                <table class="w-full text-xs"><thead class="bg-gray-50 text-gray-500 uppercase"><tr>
                  <th class="px-2 py-1 text-left">Ativo</th>
                  <th class="px-2 py-1 text-left">Tipo</th>
                  <th class="px-2 py-1 text-left">Descrição</th>
                  <th class="px-2 py-1 text-right min-w-[110px]">Saldo</th>
                  <th class="px-2 py-1 text-left">Liquidação</th>
                </tr></thead><tbody>${provRows}</tbody></table></div>` : ""}
              ${(pt.transactions || []).length ? `<div><p class="text-[11px] font-semibold text-gray-700 mb-1">Transactions a migrar</p>
                <table class="w-full text-xs"><thead class="bg-gray-50 text-gray-500 uppercase"><tr>
                  <th class="px-2 py-1 text-left">Ativo</th>
                  <th class="px-2 py-1 text-left">Tipo</th>
                  <th class="px-2 py-1 text-left">Descrição</th>
                  <th class="px-2 py-1 text-right min-w-[110px]">Saldo</th>
                  <th class="px-2 py-1 text-left">Liquidação</th>
                </tr></thead><tbody>${txRows}</tbody></table></div>` : ""}
              ${adjAmountHtml}${adjProvHtml}${adjTxHtml}
            </div>
          </div>`;
        }).join("");
      }
    },

    _renderApplyPreview(plan) {
      const warns = [];
      if (plan.fallback) {
        warns.push(`<span class="sp-pill sp-pill-warn">Posição da origem usou fallback: ${this._esc(plan.sourceDate)} (alvo: ${this._esc(plan.targetDate)})</span>`);
      }
      if ((plan.missingRules || []).length) {
        warns.push(`<span class="sp-pill sp-pill-warn">${plan.missingRules.length} regra(s) sem ativo correspondente: ${this._esc(plan.missingRules.join(", "))}</span>`);
      }
      if ((plan.transactionsUnmapped || []).length) {
        warns.push(`<span class="sp-pill sp-pill-warn">${plan.transactionsUnmapped.length} regra(s) sem securityMappings — transações dessas regras não serão migradas: ${this._esc(plan.transactionsUnmapped.join(", "))}</span>`);
      }
      // Origem zerada: a regra disparou mas a posição da origem em
      // `unprocessedSecurityPositions` veio com quantity=0 + balance=0 pro
      // ativo nesta data. Sinaliza no topo (lista de uids no tooltip) e
      // ainda marca cada linha tocada com o pill "origem vazia".
      if ((plan.emptySourceUids || []).length) {
        const uids = plan.emptySourceUids;
        warns.push(
          `<span class="sp-pill sp-pill-warn" title="${this._esc(uids.join(", "))}">` +
          `${uids.length} ativo(s) com origem sem posição (regra contribui 0)</span>`
        );
      }
      // Curva opt-in banner — conta TODAS as linhas do preview (baseline
      // incluso) por `priceSource` para deixar claro a cobertura da curva
      // sobre o plano inteiro, não só os ativos tocados por regra.
      if (plan.useCurvaPrice) {
        let curvaCount = 0, unprocCount = 0, baselineCount = 0;
        Object.values(plan.wallets || {}).forEach(p => {
          (p.rows || []).forEach(r => {
            if (r.priceSource === "curva") curvaCount++;
            else if (r.priceSource === "baseline") baselineCount++;
            else unprocCount++;
          });
        });
        const pills = ['<span class="sp-pill sp-pill-info">Preços na curva habilitado</span>'];
        pills.push(`<span class="sp-pill sp-pill-success">${curvaCount} via curva</span>`);
        if (unprocCount)   pills.push(`<span class="sp-pill sp-pill-muted">${unprocCount} via unprocessedPosition</span>`);
        if (baselineCount) pills.push(`<span class="sp-pill sp-pill-muted">${baselineCount} baseline (sem curva)</span>`);
        warns.push(pills.join(" "));
      }
      if (!Object.keys(plan.wallets || {}).length && !(plan.transactions || []).length) {
        warns.push('<span class="sp-pill sp-pill-error">Nenhuma carteira afetada e nenhuma transação para migrar — nada a enviar.</span>');
      }
      document.getElementById("sp-apply-warnings").innerHTML = warns.join(" ") || "";

      const cont = document.getElementById("sp-apply-wallets");
      // Order: wallets that strip first ("removed"/"both"), then add-only,
      // then any baseline-only — same top-down "removed → added" review
      // order used by /excecoes.
      const _opOrder = {removed: 0, both: 1, added: 2};
      const entries = Object.entries(plan.wallets || {})
        .sort(([, a], [, b]) => (_opOrder[a.op] ?? 3) - (_opOrder[b.op] ?? 3));

      cont.innerHTML = entries.map(([wid, payload]) => {
        const wname = (plan.walletNames || {})[wid] || wid;
        const rowMark = op => {
          if (op === "added")   return '<span class="sp-pill sp-pill-success" title="Ativo recebido">+ adicionado</span>';
          if (op === "removed") return '<span class="sp-pill sp-pill-error"   title="Ativo retirado">− removido</span>';
          if (op === "both")    return '<span class="sp-pill sp-pill-warn"    title="Ativo recebido e retirado">± ambos</span>';
          return '<span class="sp-pill sp-pill-muted" title="Ativo já existente, não tocado">baseline</span>';
        };
        const rowCls = op => {
          if (op === "added")   return "bg-emerald-50/60";
          if (op === "removed") return "bg-rose-50/60";
          if (op === "both")    return "bg-amber-50/60";
          return "";
        };
        // Pill da origem do PU desta linha. O backend já consolidou três
        // estados em `priceSource`: "curva" (engine de Preços na Curva),
        // "unprocessed" (tocada por regra mas sem entrada na curva) e
        // "baseline" (não tocada por regra e sem curva). Quando a curva
        // alcança a linha — inclusive baseline — `priceSource === "curva"`
        // tem prioridade.
        const priceMark = r => {
          if (r.priceSource === "curva")      return '<span class="sp-pill sp-pill-info"  title="PU veio da engine de Preços na Curva">curva</span>';
          if (r.priceSource === "baseline")   return '<span class="sp-pill sp-pill-muted" title="Linha baseline — não tocada por regra e sem entrada na curva">baseline</span>';
          return '<span class="sp-pill sp-pill-muted" title="PU veio do unprocessedPosition da origem">unprocessed</span>';
        };
        // Aviso "origem vazia" — vem do backend (`srcEmpty`) e indica que
        // a regra disparou sobre um ativo cuja origem (`unprocessedSecurityPositions`)
        // trouxe quantity=0 e balance=0 na data. A linha está aí porque
        // a regra existe, mas não houve contribuição real do strip; o
        // valor visível é só a baseline (ou zero, na origem).
        const emptyMark = r => r.srcEmpty
          ? ' <span class="sp-pill sp-pill-warn" title="Origem sem posição neste ativo nesta data — a regra contribuiu zero. O valor exibido é a baseline pré-existente (no destino) ou zero (na origem).">origem vazia</span>'
          : "";
        const rows = (payload.rows || []).map(r => `
          <tr class="border-t border-gray-100 ${rowCls(r.op)}">
            <td class="px-2 py-1 text-center">${rowMark(r.op)}${emptyMark(r)}</td>
            <td class="px-2 py-1 font-mono text-[11px]">${this._esc(r.unprocessedId)}</td>
            <td class="px-2 py-1 text-right">${this._fmtNum(r.quantity)}</td>
            <td class="px-2 py-1 text-right">${this._fmtNum(r.pu)}</td>
            <td class="px-2 py-1 text-center">${priceMark(r)}</td>
            <td class="px-2 py-1 text-right">${this._fmtMoney(r.balance)}</td>
            <td class="px-2 py-1 text-center">${r.caixa ? "Sim" : "Não"}</td>
            <td class="px-2 py-1">${this._esc(r.currencyId || "")}</td>
          </tr>`).join("");

        const headerMark = (() => {
          if (payload.op === "added")   return '<span class="sp-pill sp-pill-success">Recebe ativos</span>';
          if (payload.op === "removed") return '<span class="sp-pill sp-pill-error">Sofre strip</span>';
          if (payload.op === "both")    return '<span class="sp-pill sp-pill-warn">Recebe e sofre strip</span>';
          return "";
        })();
        const sourceMark = payload.isSource
          ? '<span class="sp-pill sp-pill-info ml-1">Origem</span>' : '';

        return `<div class="border rounded">
          <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center gap-2">
            <span class="flex items-center gap-2">
              <span>${this._esc(wname)}</span>
              ${headerMark}${sourceMark}
            </span>
            <span class="text-gray-400 font-normal">${(payload.rows || []).length} ativo(s) — ${this._esc(payload.currencyId || "")}</span>
          </div>
          <table class="w-full text-xs">
            <thead class="bg-gray-50 text-gray-500 uppercase">
              <tr>
                <th class="px-2 py-1 text-center min-w-[100px]">Marca</th>
                <th class="px-2 py-1 text-left">Ativo</th>
                <th class="px-2 py-1 text-right">Quant.</th>
                <th class="px-2 py-1 text-right">PU</th>
                <th class="px-2 py-1 text-center min-w-[90px]" title="Origem do PU usado na regra">Fonte PU</th>
                <th class="px-2 py-1 text-right">Saldo</th>
                <th class="px-2 py-1 text-center">Caixa</th>
                <th class="px-2 py-1 text-left">Moeda</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
      }).join("");

      this._renderApplyTransactions(plan);
      document.getElementById("sp-apply-preview").classList.remove("hidden");
    },

    _renderApplyTransactions(plan) {
      const txs = plan.transactions || [];
      const cont = document.getElementById("sp-apply-transactions");
      const tbody = document.getElementById("sp-apply-tx-tbody");
      const msg   = document.getElementById("sp-apply-tx-msg");
      const count = document.getElementById("sp-apply-tx-count");
      const wn = plan.walletNames || {};
      const sn = plan.securityNames || {};

      // Each migration drags its two paired adjustments along, so the
      // displayed count is `migrations + Σ adjustments`. The operator
      // sees both numbers spelled out below the table title.
      const adjCount = txs.reduce((acc, tx) => acc + ((tx.adjustments || []).length), 0);
      cont.classList.remove("hidden");
      count.textContent = `(${txs.length} migração(ões) + ${adjCount} ajuste(s))`;

      if (!txs.length) {
        tbody.innerHTML = "";
        msg.textContent = (plan.transactionsUnmapped || []).length
          ? `Nenhuma transação encontrada. Aviso: ${plan.transactionsUnmapped.length} regra(s) sem mapeamento securityMappings: ${plan.transactionsUnmapped.join(", ")}`
          : "Nenhuma transação para migrar nesta data.";
        msg.classList.remove("hidden");
        return;
      }
      msg.classList.add("hidden");

      const rows = [];
      for (const tx of txs) {
        const fromName = wn[tx.fromWalletId] || tx.fromWalletId;
        const toName   = wn[tx.toWalletId]   || tx.toWalletId;
        const secName  = sn[tx.securityId]   || tx.unprocessedId;
        rows.push(`<tr class="border-t border-gray-100 bg-amber-50/40">
          <td class="px-2 py-1">
            <div class="font-medium text-gray-800">${this._esc(secName)}</div>
            <div class="text-[10px] text-gray-400 font-mono">${this._esc(tx.unprocessedId)}</div>
          </td>
          <td class="px-2 py-1 text-gray-600">${this._esc(tx.type)}</td>
          <td class="px-2 py-1">
            <span class="sp-pill sp-pill-error">−</span>
            <span class="ml-1">${this._esc(fromName)}</span>
          </td>
          <td class="px-2 py-1">
            <span class="sp-pill sp-pill-success">+</span>
            <span class="ml-1">${this._esc(toName)}</span>
          </td>
          <td class="px-2 py-1 text-right">${tx.balance == null ? '<span class="text-gray-300">--</span>' : this._fmtMoney(tx.balance)}</td>
          <td class="px-2 py-1 text-right">${tx.quantity == null ? '<span class="text-gray-300">--</span>' : this._fmtNum(tx.quantity)}</td>
          <td class="px-2 py-1">${this._esc(tx.liquidationDate)}</td>
          <td class="px-2 py-1 text-gray-600">${this._esc(tx.description || "")}</td>
        </tr>`);

        // Paired adjustments — render right below the migration so the
        // matched +/− is visually obvious. The wallet is shown in the
        // column that matches its sign (sender adjust = + on the From
        // column; receiver adjust = − on the To column).
        for (const adj of (tx.adjustments || [])) {
          const wname = wn[adj.walletId] || adj.walletId || "";
          const isFrom = adj.side === "from";
          const balCell = adj.balance == null
            ? '<span class="text-gray-300">--</span>'
            : this._fmtMoney(adj.balance);
          const qtyCell = adj.quantity == null
            ? '<span class="text-gray-300">--</span>'
            : this._fmtNum(adj.quantity);
          rows.push(`<tr class="border-t border-gray-50 bg-sky-50/40 text-gray-600">
            <td class="px-2 py-1 pl-6">
              <div class="text-[10px] uppercase tracking-wide text-sky-700 font-semibold">↳ Ajuste</div>
              <div class="text-[10px] text-gray-500">${this._esc(adj.description || "")}</div>
            </td>
            <td class="px-2 py-1 text-gray-600">${this._esc(adj.type || "securityTransfer")}</td>
            <td class="px-2 py-1">${isFrom
              ? `<span class="sp-pill sp-pill-success">+</span><span class="ml-1">${this._esc(wname)}</span>`
              : '<span class="text-gray-300">--</span>'}</td>
            <td class="px-2 py-1">${!isFrom
              ? `<span class="sp-pill sp-pill-error">−</span><span class="ml-1">${this._esc(wname)}</span>`
              : '<span class="text-gray-300">--</span>'}</td>
            <td class="px-2 py-1 text-right">${balCell}</td>
            <td class="px-2 py-1 text-right">${qtyCell}</td>
            <td class="px-2 py-1">${this._esc(adj.liquidationDate || tx.liquidationDate)}</td>
            <td class="px-2 py-1 text-gray-500 italic">${this._esc(adj.description || "")}</td>
          </tr>`);
        }
      }
      tbody.innerHTML = rows.join("");
    },

    runApply() {
      if (!this._apply || !this._apply.plan) return;
      const kind = (this._apply.plan && this._apply.plan.kind) || "";
      if (!confirm("Confirmar envio para a API Beehus?")) return;

      const {exceptionId, companyId, date} = this._apply;
      const list = document.getElementById("sp-apply-results-list");
      list.innerHTML = '<p class="text-xs text-gray-400">Enviando...</p>';
      document.getElementById("sp-apply-results").classList.remove("hidden");
      document.getElementById("sp-apply-run-btn").disabled = true;

      fetch(`/api/excecoes/${encodeURIComponent(exceptionId)}/apply`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({companyId, date, fxOverrides: this._apply.fxOverrides || []}),
      })
        .then(r => r.json().then(d => ({ok: r.ok, d})))
        .then(async ({ok, d}) => {
          // pendingFxRates here means the operator-supplied overrides from
          // the preview didn't cover everything (rare: e.g. they cancelled
          // the prompt before, or fxRates changed). Re-prompt + retry.
          if (d && Array.isArray(d.pendingFxRates) && d.pendingFxRates.length) {
            const collected = await this._promptForFxRates(d.pendingFxRates);
            if (collected) {
              this._apply.fxOverrides = (this._apply.fxOverrides || []).concat(collected);
              this.runApply();
              return;
            }
            list.innerHTML = '<p class="sp-pill sp-pill-error">Conversão cancelada — envio interrompido.</p>';
            document.getElementById("sp-apply-run-btn").disabled = false;
            return;
          }
          if (kind === "wallet_slice") {
            this._renderSliceResults(d);
          } else if (kind === "class_strip") {
            this._renderClassResults(d);
          } else {
            this._renderApplyResults(d);
          }
          document.getElementById("sp-apply-run-btn").disabled = !ok;
          if (ok) this.loadList();
        })
        .catch(() => {
          list.innerHTML = '<p class="sp-pill sp-pill-error">Erro de rede.</p>';
        });
    },

    // Renders the class_strip apply response. The response shape is
    // distinct from position_strip (no flat `results[]`/`transactionResults[]`)
    // — it ships per-step result arrays + a `adjustmentResults` map. We list
    // each section as its own block with a count + per-row status pill so
    // the operator can scan for failures.
    _renderClassResults(d) {
      const list = document.getElementById("sp-apply-results-list");
      const wn = (this._apply && this._apply.plan && this._apply.plan.walletNames)   || {};
      const sn = (this._apply && this._apply.plan && this._apply.plan.securityNames) || {};
      let html = "";
      if (d.error) html += `<p class="sp-pill sp-pill-error">${this._esc(d.error)}</p>`;

      const renderRows = (label, rows, fmt) => {
        if (!rows || !rows.length) return "";
        const okCount = rows.filter(r => r.status === "ok").length;
        let out = `<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">${this._esc(label)} <span class="text-[10px] text-gray-400">(${okCount}/${rows.length})</span></p>`;
        out += rows.map(r => {
          const cls = r.status === "ok" ? "success"
                    : (r.status === "auth_error" ? "warn" : "error");
          const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
          const extra = r.error ? ` — ${this._esc(r.error)}` : "";
          return `<div class="text-xs text-gray-700">${pill} ${fmt(r)}${extra}</div>`;
        }).join("");
        return out;
      };

      html += renderRows("Posições (origens)", d.sourceResults || [], r => {
        const wname = wn[r.walletId] || r.walletId;
        return r.status === "ok"
          ? `<strong>${this._esc(wname)}</strong> — ${r.rows} linha(s)`
          : `<strong>${this._esc(wname)}</strong>`;
      });
      html += renderRows("Posições (destinos)", d.targetResults || [], r => {
        const wname = wn[r.walletId] || r.walletId;
        return r.status === "ok"
          ? `<strong>${this._esc(wname)}</strong> — ${r.rows} linha(s)`
          : `<strong>${this._esc(wname)}</strong>`;
      });
      html += renderRows("Provisions migradas", d.provisionResults || [], r => {
        const fromN = wn[r.sourceWalletId] || r.sourceWalletId || "";
        const toN   = wn[r.targetWalletId] || r.targetWalletId || "";
        const secN  = sn[r.securityId]     || r.securityId     || "";
        return `<strong>${this._esc(secN)}</strong>: ${this._esc(fromN)} → ${this._esc(toN)}`;
      });
      html += renderRows("Transactions migradas", d.transactionResults || [], r => {
        const fromN = wn[r.sourceWalletId] || r.sourceWalletId || "";
        const toN   = wn[r.targetWalletId] || r.targetWalletId || "";
        const secN  = sn[r.securityId]     || r.securityId     || "";
        const typeBit = r.type ? ` · ${this._esc(r.type)}` : "";
        return `<strong>${this._esc(secN)}</strong>${typeBit}: ${this._esc(fromN)} → ${this._esc(toN)}`;
      });

      const adj = d.adjustmentResults || {};
      const adjLabels = {
        amountDifference:      "Ajustes — amountDifference",
        provisionSettlement:   "Ajustes — provision liquidando",
        transactionSettlement: "Ajustes — transaction liquidando",
      };
      Object.entries(adjLabels).forEach(([k, label]) => {
        html += renderRows(label, adj[k] || [], r => {
          const wname = wn[r.walletId] || r.walletId || "";
          const secN  = sn[r.securityId] || r.securityId || "";
          const bal = (r.balance == null)
            ? '<span class="text-gray-300">--</span>'
            : this._fmtMoney(r.balance);
          return `<strong>${this._esc(secN)}</strong>: ${this._esc(wname)} · ${bal}`;
        });
      });

      // Processamento + NAV — disparados após os ajustes para deixar a
      // carteira destino com processedPosition e NAV recalculados na
      // mesma data alvo. Cada wallet vira uma linha de status.
      html += renderRows("Processamento (destinos)", d.processResults || [], r => {
        const wname = wn[r.walletId] || r.walletId || "";
        return `<strong>${this._esc(wname)}</strong>`;
      });
      html += renderRows("NAV Wallets (destinos)", d.navResults || [], r => {
        const wname = wn[r.walletId] || r.walletId || "";
        return `<strong>${this._esc(wname)}</strong>`;
      });

      list.innerHTML = html || '<p class="text-xs text-gray-400">Nada a reportar.</p>';
    },

    _renderApplyResults(d) {
      const list = document.getElementById("sp-apply-results-list");
      const wn = (this._apply && this._apply.plan && this._apply.plan.walletNames)   || {};
      const sn = (this._apply && this._apply.plan && this._apply.plan.securityNames) || {};
      let html = "";

      if (d.error) {
        html += `<p class="sp-pill sp-pill-error">${this._esc(d.error)}</p>`;
      }

      const txs = d.transactionResults || [];
      if (txs.length) {
        html += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Transações</p>';
        html += txs.map(r => {
          const pill = `<span class="sp-pill sp-pill-${r.status === "ok" ? "success" : "error"}">${this._esc(r.status)}</span>`;
          const secName  = sn[r.securityId]   || r.securityId   || "";
          const extra = r.error ? ` — ${this._esc(r.error)}` : "";
          if (r.kind === "adjust") {
            // Synthetic "Ajuste …" securityTransfer paired with each
            // migration: one stays on the sending wallet (+balance),
            // the other lands on the receiving wallet (−balance).
            const wname = wn[r.walletId] || r.walletId || "";
            const sign  = r.side === "to" ? "−" : "+";
            const tag   = `<span class="sp-pill sp-pill-muted" title="securityTransfer paired with migration">Ajuste ${sign}</span>`;
            const bal   = (r.balance == null)
              ? '<span class="text-gray-300">--</span>'
              : this._fmtMoney(r.balance);
            return `<div class="text-xs text-gray-700">${pill} ${tag} <strong>${this._esc(secName)}</strong>: ${this._esc(wname)} · ${bal}${extra}</div>`;
          }
          const fromName = wn[r.fromWalletId] || r.fromWalletId || "";
          const toName   = wn[r.toWalletId]   || r.toWalletId   || "";
          return `<div class="text-xs text-gray-700">${pill} <span class="font-mono text-[10px] text-gray-400">${this._esc(r.id)}</span> <strong>${this._esc(secName)}</strong>: ${this._esc(fromName)} → ${this._esc(toName)}${extra}</div>`;
        }).join("");
      }

      const positions = d.results || [];
      if (positions.length) {
        html += '<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">Posições</p>';
        html += positions.map(r => {
          const cls = r.status === "ok" ? "success" : (r.status === "skipped" ? "muted" : "error");
          const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
          let extra = "";
          if (r.status === "ok") extra = ` — ${r.rows} linha(s)`;
          else if (r.error) extra = ` — ${this._esc(r.error)}`;
          else if (r.reason) extra = ` — ${this._esc(r.reason)}`;
          const wname = wn[r.walletId] || r.walletId;
          return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(wname)}</strong>${extra}</div>`;
        }).join("");
      }

      list.innerHTML = html || '<p class="text-xs text-gray-400">Nenhum resultado.</p>';
    },

    downloadApplyExcel() {
      if (!this._apply) return;
      const {exceptionId, companyId, date} = this._apply;
      if (!exceptionId || !date) return;
      const url = `/api/excecoes/${encodeURIComponent(exceptionId)}/excel?companyId=${encodeURIComponent(companyId)}&date=${encodeURIComponent(date)}`;
      window.open(url, "_blank");
    },

    // ── Setup wizard ─────────────────────────────────────────────────────
    openSetup() {
      this._setup = {
        editingId: null,
        kind: "position_strip",
        companyId: "",
        sourceWalletId: "",
        outputWalletIds: [],
        walletsForCompany: [],
        walletsCrossCompany: [],
        sourceSecurities: [],
        rules: {},
        // position_strip opt-in: replace the source's unprocessedPosition
        // PU with the curva engine's PU during /preview and /apply.
        useCurva: false,
        targetWalletId: "",
        percent: 30,
        classSourceIds: [],
        classRoutes: [],
        classSearch: "",
        classVariables: [],
        classGroupings: [],
        classGroupingIds: new Set(),
      };
      // Curva checkbox starts unchecked on a fresh open (and on editException
      // the state is hydrated below from `e.useCurvaPrice`).
      const curvaChk = document.getElementById("sp-setup-use-curva");
      if (curvaChk) curvaChk.checked = false;
      document.getElementById("sp-setup-modal").classList.remove("hidden");
      document.getElementById("sp-setup-subtitle").textContent = "";
      // Reset the kind radio to position_strip (default) so editing flows
      // that target the default kind don't carry slice/class mode from a
      // prior session-level open of the modal.
      const kindRadios = document.querySelectorAll('input[name="sp-setup-kind"]');
      kindRadios.forEach(r => { r.checked = (r.value === "position_strip"); });
      document.getElementById("sp-setup-company").value = "";
      document.getElementById("sp-setup-name").value = "";
      document.getElementById("sp-setup-source").innerHTML = '<option value="">Selecione uma empresa primeiro...</option>';
      document.getElementById("sp-setup-source").disabled = true;
      document.getElementById("sp-setup-outputs").innerHTML = "Selecione uma carteira de origem primeiro.";
      document.getElementById("sp-setup-date").value = this._todayISO();
      document.getElementById("sp-setup-secs-tbody").innerHTML = "";
      document.getElementById("sp-setup-secs-msg").textContent = "Configure os passos anteriores e carregue a posição.";
      document.getElementById("sp-setup-secs-msg").style.display = "block";
      document.getElementById("sp-setup-status").textContent = "";
      // Slice-only inputs — reset to defaults so a fresh open doesn't
      // carry residual values from a previous slice exception edit.
      const tSel = document.getElementById("sp-setup-slice-target");
      tSel.innerHTML = '<option value="">Selecione uma carteira de origem primeiro...</option>';
      tSel.disabled = true;
      tSel.value = "";
      document.getElementById("sp-setup-slice-percent").value = "30";
      // class_strip-only inputs — wipe the NAV-style pickers + routes
      // table + the variable1 datalist so a fresh open starts blank.
      const wipeIds = [
        "sp-setup-class-grp-available", "sp-setup-class-grp-selected",
        "sp-setup-class-wallet-available", "sp-setup-class-wallet-selected",
      ];
      wipeIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = "";
      });
      const wipeCounts = [
        "sp-setup-class-grp-available-count", "sp-setup-class-grp-selected-count",
        "sp-setup-class-wallet-available-count", "sp-setup-class-wallet-selected-count",
      ];
      wipeCounts.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = "0";
      });
      const grpStatus = document.getElementById("sp-setup-class-grp-excel-status");
      if (grpStatus) grpStatus.textContent = "";
      const walletStatus = document.getElementById("sp-setup-class-wallet-excel-status");
      if (walletStatus) walletStatus.textContent = "";
      const filterPill = document.getElementById("sp-setup-class-wallet-available-filter");
      if (filterPill) filterPill.classList.add("hidden");
      const cTbody = document.getElementById("sp-setup-class-routes-tbody");
      if (cTbody) cTbody.innerHTML = "";
      const cMsg = document.getElementById("sp-setup-class-routes-msg");
      if (cMsg) { cMsg.textContent = "Nenhuma rota configurada."; cMsg.style.display = "block"; }
      const cVarDate = document.getElementById("sp-setup-class-vardate");
      if (cVarDate) cVarDate.value = this._todayISO();
      const cVarStatus = document.getElementById("sp-setup-class-vars-status");
      if (cVarStatus) cVarStatus.textContent = "";
      const cDatalist = document.getElementById("sp-setup-class-vars-datalist");
      if (cDatalist) cDatalist.innerHTML = "";
      this._applySetupKindVisibility();
    },

    onSetupKindChange(kind) {
      this._setup.kind = kind;
      // The "editingId" forbids changing kind mid-edit (backend rejects
      // it). Keep the radio visually locked while editing.
      if (this._setup.editingId) {
        const radios = document.querySelectorAll('input[name="sp-setup-kind"]');
        radios.forEach(r => { r.checked = (r.value === this._setup.kind); });
        return;
      }
      this._applySetupKindVisibility();
    },

    // Curva price opt-in handler. The DOM checkbox is the source of truth
    // while the modal is open; `_setup.useCurva` mirrors it so saveException
    // can serialize the flag without re-reading the DOM at submit time.
    onSetupUseCurvaChange() {
      const chk = document.getElementById("sp-setup-use-curva");
      this._setup.useCurva = !!(chk && chk.checked);
    },

    _applySetupKindVisibility() {
      const isSlice   = this._setup.kind === "wallet_slice";
      const isClass   = this._setup.kind === "class_strip";
      const classPanesVisible = isClass;
      document.getElementById("sp-setup-strip-only").classList.toggle("hidden", isSlice || classPanesVisible);
      document.getElementById("sp-setup-slice-only").classList.toggle("hidden", !isSlice);
      const sharedSrc = document.getElementById("sp-setup-shared-source");
      if (sharedSrc) sharedSrc.classList.toggle("hidden", classPanesVisible);
      const classOnly = document.getElementById("sp-setup-class-only");
      if (classOnly) classOnly.classList.toggle("hidden", !classPanesVisible);

      document.getElementById("sp-setup-title").textContent =
        isClass ? "Nova Exceção — Stripping por classe"
        : isSlice ? "Nova Exceção — Fatiar carteira"
        : "Nova Exceção — Position Stripping";
      // Lock the kind radios when editing — switching kind on an existing
      // exception is rejected by the backend (would silently strip fields
      // that only belong to the old kind).
      const lock = !!this._setup.editingId;
      document.querySelectorAll('input[name="sp-setup-kind"]').forEach(r => {
        r.disabled = lock;
      });
      // First time we expose the class picker, prime the available list so
      // it isn't empty for the operator (uses the already-loaded company
      // wallets if companyId was picked before the kind change).
      if (classPanesVisible) this._renderClassPicker();
    },

    onSetupTargetChange() {
      this._setup.targetWalletId = document.getElementById("sp-setup-slice-target").value;
    },

    closeSetup() {
      document.getElementById("sp-setup-modal").classList.add("hidden");
    },

    onSetupCompanyChange() {
      const cid = document.getElementById("sp-setup-company").value;
      this._setup.companyId = cid;
      this._setup.sourceWalletId = "";
      this._setup.outputWalletIds = [];
      this._setup.walletsForCompany = [];
      this._setup.sourceSecurities = [];
      this._setup.rules = {};
      this._setup.targetWalletId = "";
      document.getElementById("sp-setup-source").innerHTML = '<option value="">Carregando...</option>';
      document.getElementById("sp-setup-source").disabled = true;
      document.getElementById("sp-setup-outputs").innerHTML = "Selecione uma carteira de origem primeiro.";
      document.getElementById("sp-setup-secs-tbody").innerHTML = "";
      document.getElementById("sp-setup-secs-msg").style.display = "block";
      const tSel = document.getElementById("sp-setup-slice-target");
      tSel.innerHTML = '<option value="">Selecione uma carteira de origem primeiro...</option>';
      tSel.disabled = true;

      // class_strip-only — wipe the pickers + routes when the company
      // changes (cross-company wallets aren't applicable).
      this._setup.classSourceIds = [];
      this._setup.classRoutes = [];
      this._setup.classVariables = [];
      this._setup.classGroupings = [];
      this._setup.classGroupingIds = new Set();
      const wipeIds = [
        "sp-setup-class-grp-available", "sp-setup-class-grp-selected",
        "sp-setup-class-wallet-available", "sp-setup-class-wallet-selected",
      ];
      wipeIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = "";
      });
      const wipeCounts = [
        "sp-setup-class-grp-available-count", "sp-setup-class-grp-selected-count",
        "sp-setup-class-wallet-available-count", "sp-setup-class-wallet-selected-count",
      ];
      wipeCounts.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = "0";
      });
      const cTbody = document.getElementById("sp-setup-class-routes-tbody");
      if (cTbody) cTbody.innerHTML = "";
      const cMsg = document.getElementById("sp-setup-class-routes-msg");
      if (cMsg) { cMsg.textContent = "Nenhuma rota configurada."; cMsg.style.display = "block"; }
      const cDatalist = document.getElementById("sp-setup-class-vars-datalist");
      if (cDatalist) cDatalist.innerHTML = "";

      if (!cid) return;
      // Fetch wallets + groupings in parallel.
      Promise.all([
        fetch(`/api/excecoes/wallets?companyId=${encodeURIComponent(cid)}`).then(r => r.json()),
        fetch(`/api/beehus/filters/groupings?companyId=${encodeURIComponent(cid)}`).then(r => r.json()),
        // Lista cross-company para os pickers de outputs/target. Sem
        // `companyId` na query — backend devolve wallets de todas as
        // empresas visíveis (`company_filter`).
        fetch(`/api/excecoes/wallets?crossCompany=1`).then(r => r.json()).catch(() => ({wallets: []})),
      ])
        .then(([walletsRes, groupingsRes, crossRes]) => {
          const wallets = (walletsRes && walletsRes.wallets) || [];
          this._setup.walletsForCompany = wallets;
          this._setup.walletsCrossCompany = (crossRes && crossRes.wallets) || [];
          this._setup.classGroupings = Array.isArray(groupingsRes) ? groupingsRes : (groupingsRes.body || []);
          const sel = document.getElementById("sp-setup-source");
          sel.innerHTML = '<option value="">Selecione...</option>'
            + this._setup.walletsForCompany.map(w =>
              `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`).join("");
          sel.disabled = false;
          // Re-render the class picker now that the company's wallets and
          // groupings are available (the operator may already have switched
          // to class_strip, which reuses the same groupings + wallets
          // dual-pane).
          if (this._setup.kind === "class_strip") {
            this._renderClassPicker();
          }
        })
        .catch(() => {
          document.getElementById("sp-setup-source").innerHTML = '<option value="">Erro ao carregar</option>';
        });
    },

    onSetupSourceChange() {
      this._setup.sourceWalletId = document.getElementById("sp-setup-source").value;
      this._setup.outputWalletIds = [];
      this._renderOutputs();
      this._renderSliceTarget();
    },

    // Helpers for cross-company pickers. Esses pickers (outputs do
    // position_strip e target do wallet_slice) consomem
    // `walletsCrossCompany`, que carrega wallets de **todas** as empresas
    // visíveis ao operador. O picker de origem (Step 2) continua restrito
    // a `walletsForCompany` — só a empresa da exceção.
    _crossCompanyCandidates() {
      const src      = this._setup.sourceWalletId;
      const srcCompany = this._setup.companyId || "";
      const all      = this._setup.walletsCrossCompany || [];
      // Filtra a origem fora. Mantém wallets de qualquer empresa visível;
      // operador decide ativar cross-company escolhendo um item cuja
      // `companyId` difere da exceção (caso em que o label fica destacado).
      return all
        .filter(w => w.id !== src)
        .map(w => ({...w, isCrossCompany: w.companyId && w.companyId !== srcCompany}));
    },

    _renderSliceTarget() {
      const sel = document.getElementById("sp-setup-slice-target");
      if (!this._setup.sourceWalletId) {
        sel.innerHTML = '<option value="">Selecione uma carteira de origem primeiro...</option>';
        sel.disabled = true;
        return;
      }
      const candidates = this._crossCompanyCandidates();
      const cur = this._setup.targetWalletId || "";
      sel.innerHTML = '<option value="">Selecione...</option>'
        + candidates.map(w => {
            const cross = w.isCrossCompany ? ` — ${this._esc(w.companyName || "outra empresa")}` : "";
            return `<option value="${this._esc(w.id)}" ${cur === w.id ? "selected" : ""}>${this._esc(w.name)}${cross} (${this._esc(w.currencyId || "")})</option>`;
          }).join("");
      sel.disabled = false;
      // If the previously selected target is no longer in the list
      // (operator changed the source), clear it.
      if (cur && !candidates.find(w => w.id === cur)) {
        this._setup.targetWalletId = "";
        sel.value = "";
      }
    },

    _renderOutputs() {
      const cont = document.getElementById("sp-setup-outputs");
      if (!this._setup.sourceWalletId) {
        cont.innerHTML = "Selecione uma carteira de origem primeiro.";
        cont.classList.add("text-gray-400");
        return;
      }
      const candidates = this._crossCompanyCandidates();
      if (!candidates.length) {
        cont.innerHTML = "Nenhuma outra carteira disponível.";
        return;
      }
      cont.classList.remove("text-gray-400");
      // Agrupa por empresa: a empresa da exceção primeiro, depois as outras
      // (alfabéticas). Cross-company recebe um pill "outra empresa" sutil
      // pra que o operador veja com clareza quando o destino sai da
      // empresa da exceção.
      const srcCompany = this._setup.companyId || "";
      const groups = {};
      candidates.forEach(w => {
        const key = w.companyId || "";
        if (!groups[key]) groups[key] = {companyId: key, companyName: w.companyName || "(sem empresa)", wallets: []};
        groups[key].wallets.push(w);
      });
      const ordered = Object.values(groups).sort((a, b) => {
        if (a.companyId === srcCompany) return -1;
        if (b.companyId === srcCompany) return 1;
        return (a.companyName || "").localeCompare(b.companyName || "");
      });
      cont.innerHTML = ordered.map(g => {
        const isCross = g.companyId && g.companyId !== srcCompany;
        const header  = isCross
          ? `<div class="text-[10px] font-semibold text-amber-700 uppercase tracking-wide mt-2 mb-0.5">${this._esc(g.companyName)} <span class="font-normal normal-case text-amber-600">(outra empresa)</span></div>`
          : `<div class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mt-2 mb-0.5">${this._esc(g.companyName)}</div>`;
        const items = g.wallets.map(w => `
          <label class="flex items-center gap-2 py-1 cursor-pointer text-xs text-gray-700">
            <input type="checkbox" value="${this._esc(w.id)}" onchange="Strip.toggleOutputWallet('${this._esc(w.id)}')"
              ${this._setup.outputWalletIds.includes(w.id) ? "checked" : ""} />
            <span>${this._esc(w.name)} <span class="text-[10px] text-gray-400">${this._esc(w.currencyId || "")}</span></span>
          </label>`).join("");
        return header + items;
      }).join("");
    },

    toggleOutputWallet(wid) {
      const i = this._setup.outputWalletIds.indexOf(wid);
      if (i >= 0) this._setup.outputWalletIds.splice(i, 1);
      else this._setup.outputWalletIds.push(wid);
      // Output-wallet set drives the add/remove dropdowns — re-render so
      // each rule row sees the updated option list.
      if (this._setup.sourceSecurities.length) this._renderSetupSecurities();
    },

    loadSourcePosition() {
      const cid  = this._setup.companyId;
      const wid  = this._setup.sourceWalletId;
      const date = document.getElementById("sp-setup-date").value;
      if (!cid || !wid || !date) {
        alert("Empresa, carteira e data são obrigatórios.");
        return;
      }
      document.getElementById("sp-setup-secs-msg").textContent = "Carregando...";
      document.getElementById("sp-setup-secs-msg").style.display = "block";

      fetch(`/api/excecoes/source-position?companyId=${encodeURIComponent(cid)}&walletId=${encodeURIComponent(wid)}&date=${encodeURIComponent(date)}`)
        .then(r => r.json())
        .then(d => {
          this._setup.sourceSecurities = d.securities || [];
          this._setup.sourceSecurities.forEach(s => {
            if (!this._setup.rules[s.unprocessedId]) {
              this._setup.rules[s.unprocessedId] = {
                selected: false,
                addToWalletId: "",
                removeFromWalletId: "",
                caixa: false,
              };
            }
          });
          this._renderSetupSecurities();
        })
        .catch(() => {
          document.getElementById("sp-setup-secs-msg").textContent = "Erro ao carregar posição.";
        });
    },

    _walletOpts(selected) {
      const allowed = [];
      if (this._setup.sourceWalletId) allowed.push(this._setup.sourceWalletId);
      this._setup.outputWalletIds.forEach(w => allowed.push(w));
      // Lookup combinando empresa-da-exceção + cross-company. Necessário
      // porque outputs podem ser de outra empresa e o `walletsForCompany`
      // sozinho não conhece esses nomes. Cross-company vence quando os
      // dois contêm o mesmo `id` (raro, mas seguro).
      const byId = Object.fromEntries([
        ...this._setup.walletsForCompany,
        ...(this._setup.walletsCrossCompany || []),
      ].map(w => [w.id, w]));
      const srcCompany = this._setup.companyId || "";
      let html = '<option value="">--</option>';
      allowed.forEach(wid => {
        const w = byId[wid] || {id: wid, name: wid};
        const crossLabel = (w.companyId && srcCompany && w.companyId !== srcCompany)
          ? ` — ${this._esc(w.companyName || "outra empresa")}`
          : "";
        html += `<option value="${this._esc(wid)}" ${selected === wid ? "selected" : ""}>${this._esc(w.name)}${crossLabel}</option>`;
      });
      return html;
    },

    _renderSetupSecurities() {
      const tbody = document.getElementById("sp-setup-secs-tbody");
      const msg   = document.getElementById("sp-setup-secs-msg");
      const secs = this._setup.sourceSecurities;
      if (!secs.length) {
        tbody.innerHTML = "";
        msg.textContent = "Nenhum ativo encontrado para esta carteira/data.";
        msg.style.display = "block";
        return;
      }
      msg.style.display = "none";
      tbody.innerHTML = secs.map(s => {
        const r = this._setup.rules[s.unprocessedId] || {};
        const disabled = r.selected ? "" : "disabled";
        const rowCls = r.selected ? "" : "opacity-60";
        return `<tr class="border-t border-gray-100 ${rowCls}">
          <td class="px-2 py-2 text-center">
            <input type="checkbox" ${r.selected ? "checked" : ""}
              onchange="Strip.toggleRule('${this._esc(s.unprocessedId)}', this.checked)" />
          </td>
          <td class="px-2 py-2 font-mono text-[11px] text-gray-700">${this._esc(s.unprocessedId)}</td>
          <td class="px-2 py-2 text-right">${this._fmtNum(s.quantity)}</td>
          <td class="px-2 py-2 text-right">${this._fmtNum(s.pu)}</td>
          <td class="px-2 py-2 text-right">${this._fmtMoney(s.balance)}</td>
          <td class="px-2 py-2">
            <select ${disabled} onchange="Strip.updateRule('${this._esc(s.unprocessedId)}', 'addToWalletId', this.value)"
              class="w-full border rounded px-1 py-0.5 text-[11px] bg-white">
              ${this._walletOpts(r.addToWalletId)}
            </select>
          </td>
          <td class="px-2 py-2">
            <select ${disabled} onchange="Strip.updateRule('${this._esc(s.unprocessedId)}', 'removeFromWalletId', this.value)"
              class="w-full border rounded px-1 py-0.5 text-[11px] bg-white">
              ${this._walletOpts(r.removeFromWalletId)}
            </select>
          </td>
          <td class="px-2 py-2 text-center">
            <input type="checkbox" ${r.caixa ? "checked" : ""} ${disabled}
              onchange="Strip.updateRule('${this._esc(s.unprocessedId)}', 'caixa', this.checked)" />
          </td>
        </tr>`;
      }).join("");
    },

    toggleRule(uid, selected) {
      const r = this._setup.rules[uid] || {selected: false, addToWalletId: "", removeFromWalletId: "", caixa: false};
      r.selected = selected;
      this._setup.rules[uid] = r;
      this._renderSetupSecurities();
    },

    updateRule(uid, field, value) {
      const r = this._setup.rules[uid] || {selected: true, addToWalletId: "", removeFromWalletId: "", caixa: false};
      r[field] = value;
      this._setup.rules[uid] = r;
    },

    // ── class_strip wizard helpers ───────────────────────────────────────
    // The picker has two stateful lists: `classSourceIds` (the operator's
    // selection, ordered) and an implicit "available = walletsForCompany \
    // classSourceIds" view. The search box filters the visible available
    // entries by substring (name OR id, case-insensitive). All updates go
    // through `_renderClassPicker` so the two <select multiple> elements
    // and their counters stay in sync.

    // Render both transfer pickers — groupings on top, wallets below.
    // Wallets-available is computed as `walletsForCompany \ classSourceIds`,
    // optionally narrowed by the union of `walletIds` across the selected
    // groupings (matches the NAV Wallets UX in Painel de Controle).
    _renderClassPicker() {
      this._renderClassGroupingPanes();
      this._renderClassWalletPanes();
      this._renderClassRoutes();
    },

    // ── Groupings transfer ─────────────────────────────────────────────
    _renderClassGroupingPanes() {
      const availEl = document.getElementById("sp-setup-class-grp-available");
      const selEl   = document.getElementById("sp-setup-class-grp-selected");
      const availCnt = document.getElementById("sp-setup-class-grp-available-count");
      const selCnt   = document.getElementById("sp-setup-class-grp-selected-count");
      if (!availEl || !selEl) return;
      const all = this._setup.classGroupings || [];
      const selSet = this._setup.classGroupingIds || new Set();
      const available = all.filter(g => !selSet.has(g.id));
      const selected  = all.filter(g =>  selSet.has(g.id));
      availEl.innerHTML = available.map(g =>
        `<option value="${this._esc(g.id)}" title="${this._esc(g.id)}">${this._esc(g.name)}</option>`).join("");
      selEl.innerHTML = selected.map(g =>
        `<option value="${this._esc(g.id)}" title="${this._esc(g.id)}">${this._esc(g.name)}</option>`).join("");
      if (availCnt) availCnt.textContent = String(available.length);
      if (selCnt)   selCnt.textContent   = String(selected.length);
    },

    _classGroupingHighlighted(suffix) {
      const el = document.getElementById(`sp-setup-class-grp-${suffix}`);
      return el ? [...el.selectedOptions].map(o => o.value) : [];
    },

    onClassGroupingAddSelected() {
      if (!this._setup.classGroupingIds) this._setup.classGroupingIds = new Set();
      this._classGroupingHighlighted("available").forEach(id => this._setup.classGroupingIds.add(id));
      this._renderClassPicker();
    },
    onClassGroupingAddAll() {
      if (!this._setup.classGroupingIds) this._setup.classGroupingIds = new Set();
      (this._setup.classGroupings || []).forEach(g => this._setup.classGroupingIds.add(g.id));
      this._renderClassPicker();
    },
    onClassGroupingRemoveSelected() {
      if (!this._setup.classGroupingIds) return;
      this._classGroupingHighlighted("selected").forEach(id => this._setup.classGroupingIds.delete(id));
      this._renderClassPicker();
    },
    onClassGroupingRemoveAll() {
      this._setup.classGroupingIds = new Set();
      this._renderClassPicker();
    },

    async onClassGroupingExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const status = document.getElementById("sp-setup-class-grp-excel-status");
      if (status) status.textContent = "Processando…";
      const groupings = this._setup.classGroupings || [];
      if (!groupings.length) {
        if (status) status.textContent = "Selecione uma empresa primeiro.";
        alert("Selecione uma empresa antes de subir os IDs.");
        ev.target.value = "";
        return;
      }
      const known = new Set(groupings.map(g => g.id));
      try {
        const fd = new FormData();
        fd.append("file", file);
        const r = await fetch("/api/beehus/util/parse-strings-excel", {method: "POST", body: fd});
        ev.target.value = "";
        if (!r.ok) {
          const d = await r.json().catch(() => null);
          if (status) status.textContent = `falha: ${(d && d.error) || `HTTP ${r.status}`}`;
          return;
        }
        const body = await r.json();
        const input = body.values || [];
        const matched   = input.filter(id => known.has(id));
        const unmatched = input.length - matched.length;
        if (!input.length) {
          if (status) status.textContent = "nenhum ID encontrado";
          return;
        }
        if (!this._setup.classGroupingIds) this._setup.classGroupingIds = new Set();
        let added = 0;
        matched.forEach(id => {
          if (!this._setup.classGroupingIds.has(id)) { this._setup.classGroupingIds.add(id); added++; }
        });
        if (status) status.textContent =
          `${added} grouping(s) adicionado(s)` + (unmatched ? ` · ${unmatched} ID(s) ignorado(s)` : "");
        this._renderClassPicker();
      } catch (e) {
        if (status) status.textContent = `falha: ${String(e)}`;
        ev.target.value = "";
      }
    },

    // Returns the union of `walletIds` across the selected groupings, or
    // null when no grouping is selected (= no filter, show everything).
    _classWalletFilterFromGroupings() {
      const sel = this._setup.classGroupingIds;
      if (!sel || !sel.size) return null;
      const out = new Set();
      for (const g of (this._setup.classGroupings || [])) {
        if (sel.has(g.id)) (g.walletIds || []).forEach(w => out.add(w));
      }
      return out;
    },

    // ── Wallets transfer ───────────────────────────────────────────────
    _renderClassWalletPanes() {
      const availEl = document.getElementById("sp-setup-class-wallet-available");
      const selEl   = document.getElementById("sp-setup-class-wallet-selected");
      const availCnt = document.getElementById("sp-setup-class-wallet-available-count");
      const selCnt   = document.getElementById("sp-setup-class-wallet-selected-count");
      const filterPill = document.getElementById("sp-setup-class-wallet-available-filter");
      if (!availEl || !selEl) return;
      const all = this._setup.walletsForCompany || [];
      const selSet = new Set(this._setup.classSourceIds || []);
      const grpFilter = this._classWalletFilterFromGroupings();
      let available = all.filter(w => !selSet.has(w.id));
      if (grpFilter) available = available.filter(w => grpFilter.has(w.id));
      const wById = Object.fromEntries(all.map(w => [w.id, w]));
      availEl.innerHTML = available.map(w =>
        `<option value="${this._esc(w.id)}" title="${this._esc(w.id)}">${this._esc(w.name)} <span class="text-[10px] text-gray-400">[${this._esc(w.currencyId || "")}]</span></option>`).join("");
      // Selected pane preserves insertion order — operator scans top-down.
      selEl.innerHTML = (this._setup.classSourceIds || []).map(wid => {
        const w = wById[wid] || {id: wid, name: wid, currencyId: ""};
        return `<option value="${this._esc(wid)}" title="${this._esc(wid)}">${this._esc(w.name)} <span class="text-[10px] text-gray-400">[${this._esc(w.currencyId || "")}]</span></option>`;
      }).join("");
      if (availCnt) availCnt.textContent = String(available.length);
      if (selCnt)   selCnt.textContent   = String((this._setup.classSourceIds || []).length);
      if (filterPill) filterPill.classList.toggle("hidden", !grpFilter);
    },

    _classWalletHighlighted(suffix) {
      const el = document.getElementById(`sp-setup-class-wallet-${suffix}`);
      return el ? [...el.selectedOptions].map(o => o.value) : [];
    },

    _classWalletAdd(ids) {
      const seen = new Set(this._setup.classSourceIds);
      ids.forEach(id => {
        if (!id || seen.has(id)) return;
        this._setup.classSourceIds.push(id);
        seen.add(id);
      });
      this._renderClassPicker();
    },

    _classWalletRemove(ids) {
      const toDrop = new Set(ids);
      this._setup.classSourceIds = this._setup.classSourceIds.filter(id => !toDrop.has(id));
      // Drop routes whose target became a source — same defense as the
      // legacy picker had; the backend rejects the overlap, so we surface
      // the change in the UI immediately instead of at save time.
      this._setup.classRoutes = this._setup.classRoutes.filter(r =>
        !this._setup.classSourceIds.includes(r.targetWalletId));
      this._renderClassPicker();
    },

    onClassWalletAddSelected() { this._classWalletAdd(this._classWalletHighlighted("available")); },
    onClassWalletAddAll() {
      // "Adicionar todas" moves every currently-visible (grouping-filtered)
      // entry — matches the NAV Wallets UX.
      const el = document.getElementById("sp-setup-class-wallet-available");
      if (!el) return;
      this._classWalletAdd([...el.options].map(o => o.value));
    },
    onClassWalletRemoveSelected() { this._classWalletRemove(this._classWalletHighlighted("selected")); },
    onClassWalletRemoveAll() { this._classWalletRemove([...this._setup.classSourceIds]); },

    async onClassWalletExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const status = document.getElementById("sp-setup-class-wallet-excel-status");
      if (status) status.textContent = "Processando…";
      const all = this._setup.walletsForCompany || [];
      if (!all.length) {
        if (status) status.textContent = "Selecione uma empresa primeiro.";
        alert("Selecione uma empresa antes de subir os IDs.");
        ev.target.value = "";
        return;
      }
      const known = new Set(all.map(w => w.id));
      try {
        const fd = new FormData();
        fd.append("file", file);
        const r = await fetch("/api/beehus/util/parse-strings-excel", {method: "POST", body: fd});
        ev.target.value = "";
        if (!r.ok) {
          const d = await r.json().catch(() => null);
          if (status) status.textContent = `falha: ${(d && d.error) || `HTTP ${r.status}`}`;
          return;
        }
        const body = await r.json();
        const input = body.values || [];
        const matched   = input.filter(id => known.has(id));
        const unmatched = input.length - matched.length;
        if (!input.length) {
          if (status) status.textContent = "nenhum ID encontrado";
          return;
        }
        const before = (this._setup.classSourceIds || []).length;
        this._classWalletAdd(matched);
        const added = (this._setup.classSourceIds || []).length - before;
        if (status) status.textContent =
          `${added} wallet(s) adicionada(s)` + (unmatched ? ` · ${unmatched} ID(s) ignorado(s)` : "");
      } catch (e) {
        if (status) status.textContent = `falha: ${String(e)}`;
        ev.target.value = "";
      }
    },

    _renderClassRoutes() {
      const tbody = document.getElementById("sp-setup-class-routes-tbody");
      const msg   = document.getElementById("sp-setup-class-routes-msg");
      if (!tbody) return;
      const routes = this._setup.classRoutes || [];
      if (!routes.length) {
        tbody.innerHTML = "";
        if (msg) { msg.textContent = "Nenhuma rota configurada."; msg.style.display = "block"; }
        return;
      }
      if (msg) msg.style.display = "none";
      // Targets must NOT overlap sources — same constraint the backend
      // enforces. Build the option list from wallets minus sources.
      const sourceSet = new Set(this._setup.classSourceIds || []);
      const targetCandidates = (this._setup.walletsForCompany || [])
        .filter(w => !sourceSet.has(w.id));
      tbody.innerHTML = routes.map((r, i) => {
        const opts = '<option value="">Selecione...</option>' +
          targetCandidates.map(w =>
            `<option value="${this._esc(w.id)}" ${r.targetWalletId === w.id ? "selected" : ""}>${this._esc(w.name)} <span>[${this._esc(w.currencyId || "")}]</span></option>`
          ).join("");
        return `<tr class="border-t border-gray-100">
          <td class="px-2 py-2">
            <input type="text" list="sp-setup-class-vars-datalist"
              value="${this._esc(r.variable1 || "")}"
              oninput="Strip.onClassRouteVarChange(${i}, this.value)"
              placeholder="ex.: Renda Variável"
              class="w-full border rounded px-2 py-1 text-[11px] bg-white text-gray-700" />
          </td>
          <td class="px-2 py-2">
            <select onchange="Strip.onClassRouteTargetChange(${i}, this.value)"
              class="w-full border rounded px-2 py-1 text-[11px] bg-white text-gray-700">
              ${opts}
            </select>
          </td>
          <td class="px-2 py-2 text-right">
            <button type="button" onclick="Strip.onClassRemoveRoute(${i})"
              class="bg-red-500 hover:bg-red-600 text-white rounded px-2 py-1 text-[11px]">Remover</button>
          </td>
        </tr>`;
      }).join("");
    },

    onClassAddRoute() {
      if (!this._setup.classSourceIds.length) {
        alert("Selecione ao menos uma carteira de origem antes de adicionar rotas.");
        return;
      }
      this._setup.classRoutes.push({variable1: "", targetWalletId: ""});
      this._renderClassRoutes();
    },

    onClassRouteVarChange(idx, value) {
      const r = this._setup.classRoutes[idx];
      if (!r) return;
      r.variable1 = value;
      // Don't re-render — the <input> already owns the value and reseting
      // the DOM would steal focus mid-typing.
    },

    onClassRouteTargetChange(idx, value) {
      const r = this._setup.classRoutes[idx];
      if (!r) return;
      r.targetWalletId = value;
    },

    onClassRemoveRoute(idx) {
      this._setup.classRoutes.splice(idx, 1);
      this._renderClassRoutes();
    },

    loadClassVariables() {
      const cid  = this._setup.companyId;
      const date = (document.getElementById("sp-setup-class-vardate") || {}).value || this._todayISO();
      const wids = (this._setup.classSourceIds || []).slice();
      const status = document.getElementById("sp-setup-class-vars-status");
      if (!cid)        { if (status) status.textContent = "Selecione uma empresa."; return; }
      if (!wids.length){ if (status) status.textContent = "Selecione ao menos uma carteira de origem."; return; }
      if (status) status.textContent = "Carregando...";
      const url = `/api/excecoes/class-strip/variables?companyId=${encodeURIComponent(cid)}&date=${encodeURIComponent(date)}&walletIds=${encodeURIComponent(wids.join(","))}`;
      fetch(url)
        .then(r => r.json().then(d => ({ok: r.ok, d})))
        .then(({ok, d}) => {
          if (!ok || d.error) {
            if (status) status.textContent = "Erro: " + (d.error || "falha");
            return;
          }
          this._setup.classVariables = d.variables || [];
          const dl = document.getElementById("sp-setup-class-vars-datalist");
          if (dl) {
            dl.innerHTML = this._setup.classVariables.map(v =>
              `<option value="${this._esc(v.variable1)}">${v.count} ativos · ${Number(v.totalBalance || 0).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2})}</option>`
            ).join("");
          }
          if (status) {
            const fb = d.fallback ? " (fallback)" : "";
            status.textContent = `${this._setup.classVariables.length} classe(s) encontrada(s)${fb}`;
          }
        })
        .catch(() => {
          if (status) status.textContent = "Erro de rede.";
        });
    },

    saveException() {
      const name = document.getElementById("sp-setup-name").value.trim();
      const cid  = this._setup.companyId;
      if (!cid)  { alert("Selecione uma empresa."); return; }
      if (!name) { alert("Informe um nome."); return; }

      let body;
      if (this._setup.kind === "class_strip") {
        const srcs = (this._setup.classSourceIds || []).slice();
        if (!srcs.length) { alert("Selecione ao menos uma carteira de origem."); return; }
        // Strip empty rows + check duplicates + cross-wallet conflicts up
        // front so the operator gets a single alert instead of a server
        // round-trip per problem.
        const routes = [];
        const seenVars = new Set();
        for (const r of this._setup.classRoutes || []) {
          const v1  = (r.variable1 || "").trim();
          const tgt = (r.targetWalletId || "").trim();
          if (!v1 && !tgt) continue;
          if (!v1)  { alert("Existe uma rota sem variable1."); return; }
          if (!tgt) { alert(`Rota "${v1}" sem carteira destino.`); return; }
          if (seenVars.has(v1)) { alert(`variable1 duplicada: ${v1}`); return; }
          if (srcs.includes(tgt)) { alert(`Destino "${tgt}" não pode ser uma das origens.`); return; }
          seenVars.add(v1);
          routes.push({variable1: v1, targetWalletId: tgt});
        }
        if (!routes.length) { alert("Adicione ao menos uma rota (variable1 + destino)."); return; }
        body = {
          kind:             "class_strip",
          companyId:        cid,
          name,
          sourceWalletIds:  srcs,
          classRoutes:      routes,
        };
      } else if (this._setup.kind === "wallet_slice") {
        if (!this._setup.sourceWalletId) { alert("Selecione a carteira de origem."); return; }
        const tgt = document.getElementById("sp-setup-slice-target").value;
        const pct = parseFloat(document.getElementById("sp-setup-slice-percent").value || "0");
        if (!tgt)                    { alert("Selecione a carteira de destino."); return; }
        if (tgt === this._setup.sourceWalletId) { alert("A carteira de destino precisa ser diferente da origem."); return; }
        if (!(pct > 0 && pct <= 100)) { alert("O percentual deve ser maior que 0 e menor ou igual a 100."); return; }
        body = {
          kind:            "wallet_slice",
          companyId:       cid,
          name,
          sourceWalletId:  this._setup.sourceWalletId,
          targetWalletId:  tgt,
          percent:         pct,
        };
      } else {
        if (!this._setup.sourceWalletId) { alert("Selecione a carteira de origem."); return; }
        if (!this._setup.outputWalletIds.length) { alert("Selecione ao menos uma carteira de saída."); return; }
        const rules = [];
        Object.entries(this._setup.rules).forEach(([uid, r]) => {
          if (!r.selected) return;
          if (!r.addToWalletId && !r.removeFromWalletId) return;
          rules.push({
            unprocessedId:      uid,
            addToWalletId:      r.addToWalletId || null,
            removeFromWalletId: r.removeFromWalletId || null,
            caixa:              !!r.caixa,
          });
        });
        if (!rules.length) {
          alert("Selecione ao menos um ativo e configure adicionar/remover.");
          return;
        }
        body = {
          kind:            "position_strip",
          companyId:       cid,
          name,
          sourceWalletId:  this._setup.sourceWalletId,
          outputWalletIds: this._setup.outputWalletIds,
          rules,
          // Curva opt-in — backend validates and persists this on the
          // exception blob; preview/apply read it from the blob.
          useCurvaPrice:   !!this._setup.useCurva,
        };
      }

      const url    = this._setup.editingId
        ? `/api/excecoes/${encodeURIComponent(this._setup.editingId)}`
        : "/api/excecoes";
      const method = this._setup.editingId ? "PUT" : "POST";

      document.getElementById("sp-setup-status").textContent = "Salvando...";
      fetch(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
        .then(r => r.json().then(d => ({ok: r.ok, d})))
        .then(({ok, d}) => {
          if (!ok || d.error) {
            document.getElementById("sp-setup-status").textContent = "Erro: " + (d.error || "falha");
            return;
          }
          this.closeSetup();
          this.loadList();
        })
        .catch(() => {
          document.getElementById("sp-setup-status").textContent = "Erro de rede.";
        });
    },

    editException(id, cid) {
      fetch(`/api/excecoes/${encodeURIComponent(id)}?companyId=${encodeURIComponent(cid)}`)
        .then(r => r.json())
        .then(d => {
          if (d.error) { alert(d.error); return; }
          const e = d.exception;
          this.openSetup();
          this._setup.editingId = e.id;
          this._setup.companyId = e.companyId;
          this._setup.kind = e.kind || "position_strip";
          // Sync the kind radios + visibility before we hydrate so the
          // kind-specific sub-fields exist when we try to set them.
          document.querySelectorAll('input[name="sp-setup-kind"]').forEach(r => {
            r.checked = (r.value === this._setup.kind);
          });
          this._applySetupKindVisibility();

          document.getElementById("sp-setup-company").value = e.companyId;
          document.getElementById("sp-setup-name").value = e.name || "";
          document.getElementById("sp-setup-subtitle").textContent = "Editando: " + (e.name || "");

          return Promise.all([
            fetch(`/api/excecoes/wallets?companyId=${encodeURIComponent(e.companyId)}`).then(r => r.json()),
            fetch(`/api/beehus/filters/groupings?companyId=${encodeURIComponent(e.companyId)}`).then(r => r.json()),
            // Cross-company list — alimenta os pickers de outputs/target,
            // que aceitam carteiras de qualquer empresa visível.
            fetch(`/api/excecoes/wallets?crossCompany=1`).then(r => r.json()).catch(() => ({wallets: []})),
          ])
            .then(([walletsRes, groupingsRes, crossRes]) => {
              const wallets = (walletsRes && walletsRes.wallets) || [];
              this._setup.walletsForCompany = wallets;
              this._setup.walletsCrossCompany = (crossRes && crossRes.wallets) || [];
              this._setup.classGroupings = Array.isArray(groupingsRes) ? groupingsRes : (groupingsRes.body || []);
              const sel = document.getElementById("sp-setup-source");
              sel.innerHTML = '<option value="">Selecione...</option>'
                + wallets.map(w => `<option value="${this._esc(w.id)}">${this._esc(w.name)}</option>`).join("");
              sel.disabled = false;
              sel.value = e.sourceWalletId || "";
              this._setup.sourceWalletId = e.sourceWalletId || "";

              if (this._setup.kind === "wallet_slice") {
                this._setup.targetWalletId = e.targetWalletId || "";
                this._setup.percent = e.percent || 0;
                this._renderSliceTarget();
                document.getElementById("sp-setup-slice-target").value = this._setup.targetWalletId;
                document.getElementById("sp-setup-slice-percent").value = String(this._setup.percent);
                return;
              }

              if (this._setup.kind === "class_strip") {
                this._setup.classSourceIds = (e.sourceWalletIds || []).slice();
                this._setup.classRoutes = (e.classRoutes || []).map(r => ({
                  variable1:      r.variable1 || "",
                  targetWalletId: r.targetWalletId || "",
                }));
                // No grouping selection is persisted on the exception — the
                // picker starts empty on edit; the operator can re-apply
                // groupings if they want to narrow the available wallets.
                this._setup.classGroupingIds = new Set();
                this._renderClassPicker();
                return;
              }

              this._setup.outputWalletIds = (e.outputWalletIds || []).slice();
              this._renderOutputs();

              // Hydrate the curva opt-in checkbox from the stored blob so
              // re-saves preserve the choice instead of silently reverting
              // to unchecked.
              this._setup.useCurva = !!e.useCurvaPrice;
              const curvaChk = document.getElementById("sp-setup-use-curva");
              if (curvaChk) curvaChk.checked = this._setup.useCurva;

              this._setup.rules = {};
              (e.rules || []).forEach(r => {
                this._setup.rules[r.unprocessedId] = {
                  selected:           true,
                  addToWalletId:      r.addToWalletId || "",
                  removeFromWalletId: r.removeFromWalletId || "",
                  caixa:              !!r.caixa,
                };
              });
              // Synthetic source-securities list so existing rules render
              // even before the operator reloads the position from Beehus.
              this._setup.sourceSecurities = (e.rules || []).map(r => ({
                unprocessedId: r.unprocessedId,
                quantity: null, pu: null, balance: null,
              }));
              this._renderSetupSecurities();
            });
        });
    },

    // ── Bulk apply (daily routine) ───────────────────────────────────────
    // The operator picks N exceptions via checkbox + a single target date,
    // then clicks "Aplicar selecionadas". The click does NOT fire the
    // apply directly — it opens a summary/approval modal. The modal
    // snapshots the selection, runs /preview for each exception in
    // parallel (so the operator can see how many wallets/transactions are
    // about to be touched + any warnings), and only then offers the
    // "Confirmar e enviar" button that triggers the actual sequential
    // /apply calls.
    openBulkConfirm() {
      const ids = [...this._selectedIds];
      if (!ids.length) { alert("Selecione pelo menos uma exceção."); return; }

      const byId = Object.fromEntries(this._exceptions.map(e => [e.id, e]));
      const selected = ids.map(id => byId[id]).filter(Boolean);
      if (!selected.length) { alert("As exceções selecionadas não estão mais visíveis. Recarregue a lista."); return; }

      // Single bulk date chosen in the confirm modal (default today),
      // applied to ALL selected exceptions. Replaces the old per-row Data
      // Base column. The {id: date} map is what the rest of the bulk flow
      // (preview/apply) already consumes, so only the date SOURCE changed.
      const dateInput = document.getElementById("sp-confirm-date");
      const date = (dateInput && dateInput.value) || this._todayISO();
      if (dateInput) dateInput.value = date;
      const dates = {};
      selected.forEach(e => { dates[e.id] = date; });

      // Freeze the selection — checkbox changes after this point don't shift
      // what the modal is reviewing. The bulk date can still be changed in
      // the modal (onBulkDateChange re-previews).
      this._bulkConfirm = {exceptions: selected, dates, date, previews: {}};

      document.getElementById("sp-confirm-modal").classList.remove("hidden");
      document.getElementById("sp-confirm-subtitle").textContent =
        `${selected.length} exceção(ões) — ${date}`;
      document.getElementById("sp-confirm-count").textContent = String(selected.length);
      document.getElementById("sp-confirm-status").textContent = "";
      document.getElementById("sp-confirm-run-btn").disabled = false;

      this._renderBulkConfirm();
      this._loadBulkPreviews();
    },

    // Operator changed the single bulk date in the confirm modal: re-point
    // every selected exception at the new date, clear stale previews, and
    // reload them. No-op if no batch is being reviewed.
    onBulkDateChange() {
      if (!this._bulkConfirm || !this._bulkConfirm.exceptions) return;
      const input = document.getElementById("sp-confirm-date");
      const date = (input && input.value) || this._todayISO();
      if (input) input.value = date;
      const dates = {};
      this._bulkConfirm.exceptions.forEach(e => { dates[e.id] = date; });
      this._bulkConfirm.dates = dates;
      this._bulkConfirm.date = date;
      this._bulkConfirm.previews = {};
      document.getElementById("sp-confirm-subtitle").textContent =
        `${this._bulkConfirm.exceptions.length} exceção(ões) — ${date}`;
      this._renderBulkConfirm();
      this._loadBulkPreviews();
    },

    closeBulkConfirm() {
      document.getElementById("sp-confirm-modal").classList.add("hidden");
    },

    // Fire /preview for every snapshot exception in parallel. We don't
    // serialize because previews are read-only — and the operator wants
    // the summary populated as fast as possible. Each response slots its
    // result into `_bulkConfirm.previews` and re-renders the table row.
    _loadBulkPreviews() {
      const {dates, exceptions} = this._bulkConfirm;
      exceptions.forEach(e => {
        const date = dates[e.id];
        if (!date) {
          this._bulkConfirm.previews[e.id] = {ok: false, error: "Data Base ausente"};
          this._renderBulkConfirm();
          return;
        }
        fetch(`/api/excecoes/${encodeURIComponent(e.id)}/preview`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({companyId: e.companyId, date}),
        })
          .then(r => r.json().then(d => ({ok: r.ok, status: r.status, d})))
          .then(({ok, status, d}) => {
            if (!ok || d.error) {
              this._bulkConfirm.previews[e.id] = {
                ok: false,
                error: d.error || `HTTP ${status}`,
                isAuth: status === 401,
              };
            } else {
              this._bulkConfirm.previews[e.id] = {ok: true, plan: d};
            }
          })
          .catch(err => {
            this._bulkConfirm.previews[e.id] = {ok: false, error: "rede: " + (err.message || "")};
          })
          .finally(() => this._renderBulkConfirm());
      });
    },

    // Compact per-row status pill — keeps the summary scannable. Only
    // surfaces what the operator needs to decide whether to proceed: how
    // many wallets/transactions will be touched, any non-fatal warnings,
    // and explicit "nothing to apply" / "error" states.
    _previewSummaryHtml(prev) {
      if (!prev) {
        return '<span class="sp-pill sp-pill-muted">carregando…</span>';
      }
      if (!prev.ok) {
        const cls = prev.isAuth ? "sp-pill-warn" : "sp-pill-error";
        const label = prev.isAuth ? "auth" : "erro";
        return `<span class="sp-pill ${cls}" title="${this._esc(prev.error || "")}">${label}</span>
                <span class="text-[10px] text-gray-500 ml-1">${this._esc(prev.error || "")}</span>`;
      }
      const plan = prev.plan || {};
      if (plan.kind === "wallet_slice") {
        const secCount  = (plan.securities  || []).length + (plan.cashAccount ? 1 : 0);
        const provCount = (plan.provisions  || []).length;
        const txCount   = (plan.transactions || []).length;
        const pills = [];
        pills.push(`<span class="sp-pill sp-pill-info">${this._fmtMoney(plan.percent)}%</span>`);
        if (!secCount && !provCount && !txCount) {
          pills.push('<span class="sp-pill sp-pill-muted">nada a enviar</span>');
        } else {
          if (secCount)  pills.push(`<span class="sp-pill sp-pill-success">${secCount} ativo(s)</span>`);
          if (provCount) pills.push(`<span class="sp-pill sp-pill-info">${provCount} provision(s)</span>`);
          if (txCount)   pills.push(`<span class="sp-pill sp-pill-info">${txCount} transação(ões)</span>`);
        }
        if (plan.fallback) {
          pills.push(`<span class="sp-pill sp-pill-warn" title="Posição da origem usou fallback: ${this._esc(plan.sourceDate)} (alvo: ${this._esc(plan.targetDate)})">fallback</span>`);
        }
        return pills.join(" ");
      }
      if (plan.kind === "class_strip") {
        const summary = plan.summary || {};
        const pills = [];
        const matched = summary.matchedSecurities || 0;
        const provs   = summary.migratedProvisions || 0;
        const txs     = summary.migratedTransactions || 0;
        const adjs    = summary.adjustmentTransactions || 0;
        if (!matched && !provs && !txs && !adjs) {
          pills.push('<span class="sp-pill sp-pill-muted">nada a enviar</span>');
        } else {
          if (matched) pills.push(`<span class="sp-pill sp-pill-success">${matched} ativo(s)</span>`);
          if (provs)   pills.push(`<span class="sp-pill sp-pill-info">${provs} provision(s)</span>`);
          if (txs)     pills.push(`<span class="sp-pill sp-pill-info">${txs} transação(ões)</span>`);
          if (adjs)    pills.push(`<span class="sp-pill sp-pill-info">${adjs} ajuste(s)</span>`);
        }
        // Surface per-source fallbacks/skipped securities as a single
        // aggregated warning pill — the operator can drill into details
        // in the single-row apply panel.
        const sourceFb = (plan.perSource || []).filter(ps => ps.fallback).length;
        if (sourceFb) {
          pills.push(`<span class="sp-pill sp-pill-warn" title="${sourceFb} origem(ns) usaram fallback de data.">fallback (${sourceFb})</span>`);
        }
        const skipped = (summary.skippedSecurities || 0);
        if (skipped) {
          pills.push(`<span class="sp-pill sp-pill-muted" title="Ativos cuja variable1 não tem rota / sem mapping.">${skipped} sem rota</span>`);
        }
        return pills.join(" ");
      }
      const walletCount = Object.keys(plan.wallets || {}).length;
      const txCount     = (plan.transactions || []).length;
      const adjCount    = (plan.transactions || [])
        .reduce((acc, tx) => acc + ((tx.adjustments || []).length), 0);
      const pills = [];
      if (!walletCount && !txCount) {
        pills.push('<span class="sp-pill sp-pill-muted">nada a enviar</span>');
      } else {
        pills.push(`<span class="sp-pill sp-pill-success">${walletCount} carteira(s)</span>`);
        if (txCount) {
          const adjTag = adjCount ? ` + ${adjCount} ajuste(s)` : "";
          pills.push(`<span class="sp-pill sp-pill-info">${txCount} transação(ões)${adjTag}</span>`);
        }
      }
      if (plan.fallback) {
        pills.push(`<span class="sp-pill sp-pill-warn" title="Posição da origem usou fallback: ${this._esc(plan.sourceDate)} (alvo: ${this._esc(plan.targetDate)})">fallback</span>`);
      }
      if ((plan.missingRules || []).length) {
        pills.push(`<span class="sp-pill sp-pill-warn" title="Regras sem ativo correspondente: ${this._esc((plan.missingRules || []).join(", "))}">${plan.missingRules.length} regra(s) sem ativo</span>`);
      }
      if ((plan.transactionsUnmapped || []).length) {
        pills.push(`<span class="sp-pill sp-pill-warn" title="Regras sem securityMappings — transações não migrarão: ${this._esc((plan.transactionsUnmapped || []).join(", "))}">${plan.transactionsUnmapped.length} sem mapping</span>`);
      }
      return pills.join(" ");
    },

    _renderBulkConfirm() {
      const {exceptions, previews, dates} = this._bulkConfirm;
      const tbody = document.getElementById("sp-confirm-tbody");
      tbody.innerHTML = exceptions.map(e => {
        const prev = previews[e.id];
        const d    = dates[e.id] || "";
        // class_strip exceptions don't have a single source wallet — show
        // the first source + "+N" hint and use route count instead of
        // ruleCount (which is always 0 for class_strip).
        let sourceCell, ruleCount;
        if (e.kind === "class_strip") {
          const srcs = e.sourceWalletNames || [];
          const more = srcs.length > 1 ? ` <span class="text-[10px] text-gray-400">+${srcs.length - 1}</span>` : "";
          sourceCell = `${this._esc(srcs[0] || "")}${more}`;
          ruleCount  = (e.classRoutes || []).length;
        } else {
          sourceCell = this._esc(e.sourceWalletName);
          ruleCount  = e.ruleCount;
        }
        return `<tr class="border-t border-gray-100">
          <td class="px-3 py-2 text-gray-700">${this._esc(e.companyName)}</td>
          <td class="px-3 py-2 font-medium text-gray-800">${this._esc(e.name)}</td>
          <td class="px-3 py-2 text-gray-700">${sourceCell}</td>
          <td class="px-3 py-2 text-gray-700 font-mono text-[11px]">${this._esc(d)}</td>
          <td class="px-3 py-2 text-right">${ruleCount}</td>
          <td class="px-3 py-2">${this._previewSummaryHtml(prev)}</td>
        </tr>`;
      }).join("");

      // Overview pills at the top: data range + counts + aggregate warnings.
      const total       = exceptions.length;
      const completed   = Object.keys(previews).length;
      const loading     = total - completed;
      const errored     = Object.values(previews).filter(p => !p.ok).length;
      const noOp        = Object.values(previews)
        .filter(p => {
          if (!p.ok) return false;
          const pl = p.plan || {};
          if (pl.kind === "wallet_slice") {
            const sec = (pl.securities || []).length + (pl.cashAccount ? 1 : 0);
            const pr  = (pl.provisions || []).length;
            const tx  = (pl.transactions || []).length;
            return !sec && !pr && !tx;
          }
          if (pl.kind === "class_strip") {
            const s = pl.summary || {};
            return !s.matchedSecurities && !s.migratedProvisions
                && !s.migratedTransactions && !s.adjustmentTransactions;
          }
          return !Object.keys(pl.wallets || {}).length && !(pl.transactions || []).length;
        })
        .length;
      const distinct    = [...new Set(Object.values(dates))].sort();
      const dateLabel   = distinct.length === 1 ? distinct[0] : `${distinct.length} datas`;
      const overview = [];
      overview.push(`<span class="sp-pill sp-pill-info">Datas: ${this._esc(dateLabel)}</span>`);
      overview.push(`<span class="sp-pill sp-pill-muted">${total} selecionada(s)</span>`);
      if (loading) overview.push(`<span class="sp-pill sp-pill-muted">${loading} preview carregando…</span>`);
      if (errored) overview.push(`<span class="sp-pill sp-pill-error">${errored} com erro no preview</span>`);
      if (noOp)    overview.push(`<span class="sp-pill sp-pill-warn">${noOp} sem mudanças</span>`);
      document.getElementById("sp-confirm-overview").innerHTML = overview.join(" ");
    },

    // Approved by the operator — close the modal and run the sequential
    // /apply loop against the frozen snapshot. We don't re-validate the
    // selection against the live `_selectedIds` here because the
    // snapshot is what the operator just reviewed.
    confirmBulkApply() {
      const {exceptions, dates} = this._bulkConfirm;
      if (!exceptions.length || !dates || !Object.keys(dates).length) { this.closeBulkConfirm(); return; }
      this.closeBulkConfirm();
      this._executeBulkApply(exceptions, dates);
    },

    async _executeBulkApply(exceptions, dates) {
      const status = document.getElementById("sp-bulk-status");
      const btn    = document.getElementById("sp-bulk-apply-btn");
      const res    = document.getElementById("sp-results");
      const list   = document.getElementById("sp-results-list");
      btn.disabled = true;
      res.classList.remove("hidden");
      list.innerHTML = '<p class="text-xs text-gray-400">Enviando...</p>';
      status.textContent = "";

      const results = [];
      let authBroke = false;
      for (let i = 0; i < exceptions.length; i++) {
        const e = exceptions[i];
        const date = dates[e.id];
        status.textContent = `Enviando ${i + 1}/${exceptions.length} — ${e.name} (${date || "?"})`;
        if (!date) {
          results.push({exception: e, ok: false, status: 0, data: {error: "Data Base ausente"}, date: ""});
          continue;
        }
        try {
          const r = await fetch(`/api/excecoes/${encodeURIComponent(e.id)}/apply`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({companyId: e.companyId, date}),
          });
          const d = await r.json().catch(() => ({}));
          results.push({exception: e, ok: r.ok, status: r.status, data: d, date});
          if (r.status === 401) {
            authBroke = true;
            break;
          }
        } catch (err) {
          results.push({exception: e, ok: false, status: 0, data: {error: "rede: " + (err.message || "")}, date});
        }
      }

      this._renderBulkResults(results, {authBroke, total: exceptions.length, dates});
      this._refreshBulkButton();
      // Refresh the list so `lastApplied` columns reflect successful runs.
      this.loadList();
    },

    _renderBulkResults(results, {authBroke, total, dates}) {
      const list = document.getElementById("sp-results-list");
      const status = document.getElementById("sp-bulk-status");
      const okCount    = results.filter(r => r.ok && !(r.data && r.data.error)).length;
      const errCount   = results.length - okCount;
      const skippedCnt = total - results.length;

      const distinct  = [...new Set(Object.values(dates || {}))].sort();
      const dateLabel = distinct.length === 1 ? distinct[0] : `${distinct.length} datas`;
      const summary = [];
      summary.push(`<span class="sp-pill sp-pill-info">Datas: ${this._esc(dateLabel)}</span>`);
      summary.push(`<span class="sp-pill sp-pill-success">${okCount} ok</span>`);
      if (errCount)   summary.push(`<span class="sp-pill sp-pill-error">${errCount} com erro</span>`);
      if (skippedCnt) summary.push(`<span class="sp-pill sp-pill-warn">${skippedCnt} não tentadas (auth break)</span>`);
      status.innerHTML = summary.join(" ");

      const blocks = results.map(({exception, ok, status: httpStatus, data, date}) => {
        const headerPill = (ok && !(data && data.error))
          ? '<span class="sp-pill sp-pill-success">ok</span>'
          : `<span class="sp-pill sp-pill-error">${this._esc(httpStatus === 401 ? "auth" : "erro")}</span>`;
        const datePill = date
          ? `<span class="sp-pill sp-pill-info">${this._esc(date)}</span>`
          : "";
        const wn = (data && data.walletNames)   || {};
        const sn = (data && data.securityNames) || {};

        let body = "";
        if (data && data.error) {
          body += `<p class="text-xs text-rose-700 mt-1">${this._esc(data.error)}</p>`;
        }
        const isSlice = data && data.kind === "wallet_slice";
        if (isSlice) {
          const pos = data.positionResult;
          if (pos) {
            const cls = pos.status === "ok" ? "success" : (pos.status === "skipped" ? "muted" : "error");
            const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(pos.status)}</span>`;
            const wname = wn[pos.walletId] || pos.walletId;
            let extra = "";
            if (pos.status === "ok") extra = ` — ${pos.rows} linha(s)`;
            else if (pos.error)  extra = ` — ${this._esc(pos.error)}`;
            else if (pos.reason) extra = ` — ${this._esc(pos.reason)}`;
            body += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Posições</p>';
            body += `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(wname)}</strong>${extra}</div>`;
          }
          const provs = data.provisionResults || [];
          if (provs.length) {
            body += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Provisions</p>';
            body += provs.map(r => {
              const cls = r.status === "ok" ? "success" : "error";
              const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
              const extra = r.error ? ` — ${this._esc(r.error)}` : "";
              return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(r.description || "")}</strong> · ${this._fmtMoney(r.balance)}${extra}</div>`;
            }).join("");
          }
          const txs2 = data.transactionResults || [];
          if (txs2.length) {
            body += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Transactions</p>';
            body += txs2.map(r => {
              const cls = r.status === "ok" ? "success" : "error";
              const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
              const secName = r.securityId ? (sn[r.securityId] || r.securityId) : "";
              const extra = r.error ? ` — ${this._esc(r.error)}` : "";
              return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(secName || r.description || "")}</strong> · ${this._esc(r.type || "")} · ${this._fmtMoney(r.balance)}${extra}</div>`;
            }).join("");
          }
        } else {
          const txs = (data && data.transactionResults) || [];
          if (txs.length) {
            body += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Transações</p>';
            body += txs.map(r => {
              const pill = `<span class="sp-pill sp-pill-${r.status === "ok" ? "success" : "error"}">${this._esc(r.status)}</span>`;
              const fromName = wn[r.fromWalletId] || r.fromWalletId || "";
              const toName   = wn[r.toWalletId]   || r.toWalletId   || "";
              const secName  = sn[r.securityId]   || r.securityId   || "";
              const extra = r.error ? ` — ${this._esc(r.error)}` : "";
              return `<div class="text-xs text-gray-700">${pill} <span class="font-mono text-[10px] text-gray-400">${this._esc(r.id)}</span> <strong>${this._esc(secName)}</strong>: ${this._esc(fromName)} → ${this._esc(toName)}${extra}</div>`;
            }).join("");
          }
          const positions = (data && data.results) || [];
          if (positions.length) {
            body += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Posições</p>';
            body += positions.map(r => {
              const cls = r.status === "ok" ? "success" : (r.status === "skipped" ? "muted" : "error");
              const pill = `<span class="sp-pill sp-pill-${cls}">${this._esc(r.status)}</span>`;
              let extra = "";
              if (r.status === "ok") extra = ` — ${r.rows} linha(s)`;
              else if (r.error)  extra = ` — ${this._esc(r.error)}`;
              else if (r.reason) extra = ` — ${this._esc(r.reason)}`;
              const wname = wn[r.walletId] || r.walletId;
              return `<div class="text-xs text-gray-700">${pill} <strong>${this._esc(wname)}</strong>${extra}</div>`;
            }).join("");
          }
        }
        if (!body) {
          body = '<p class="text-[11px] text-gray-400 mt-1">Nenhum resultado retornado.</p>';
        }

        return `<div class="border rounded">
          <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center gap-2">
            <span class="flex items-center gap-2">
              ${headerPill}${datePill}
              <span>${this._esc(exception.companyName)} — ${this._esc(exception.name)}</span>
            </span>
            <span class="text-gray-400 font-normal text-[10px]">origem: ${this._esc(exception.sourceWalletName)}</span>
          </div>
          <div class="px-3 py-2">${body}</div>
        </div>`;
      }).join("");

      const tail = authBroke
        ? '<p class="sp-pill sp-pill-warn mt-2">Aplicação interrompida em 401 (auth). As exceções restantes não foram tentadas — renove o token e tente de novo.</p>'
        : "";
      list.innerHTML = blocks + tail;
    },
  };

  // Esc dismisses the Strip setup modal, the bulk-confirm summary modal,
  // or the inline single-row apply panel when any is open. Doesn't touch
  // other dialogs — listeners coexist on the document.
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const setup   = document.getElementById("sp-setup-modal");
    const confirm = document.getElementById("sp-confirm-modal");
    const apply   = document.getElementById("sp-apply-inline");
    const applyDt = document.getElementById("sp-apply-dates");
    const fxPmpt  = document.getElementById("sp-fx-prompt");
    const compPop = document.getElementById("sp-filter-companies-panel");
    const datePop = document.getElementById("sp-filter-dates-panel");
    // The FX prompt is modal over preview/apply, so Esc should dismiss it
    // first (resolving its promise with null) before bubbling further.
    if (fxPmpt  && !fxPmpt.classList.contains("hidden"))  { Strip.cancelFxPrompt(); return; }
    if (setup   && !setup.classList.contains("hidden"))   Strip.closeSetup();
    if (confirm && !confirm.classList.contains("hidden")) Strip.closeBulkConfirm();
    if (applyDt && !applyDt.classList.contains("hidden")) Strip.closeApplyDates();
    if (apply   && !apply.classList.contains("hidden"))   Strip.closeApplyInline();
    if (compPop && !compPop.classList.contains("hidden")) compPop.classList.add("hidden");
    if (datePop && !datePop.classList.contains("hidden")) datePop.classList.add("hidden");
  });

  // Click-outside handler for the toolbar popovers (company + date).
  // Closes whichever panel the click landed outside of — matches the
  // standard popover dismiss UX used elsewhere in the shell.
  document.addEventListener("click", e => {
    const compWrap = document.getElementById("sp-filter-companies-wrap");
    if (compWrap && !compWrap.contains(e.target)) {
      const panel = document.getElementById("sp-filter-companies-panel");
      if (panel && !panel.classList.contains("hidden")) panel.classList.add("hidden");
    }
    const dateWrap = document.getElementById("sp-filter-dates-wrap");
    if (dateWrap && !dateWrap.contains(e.target)) {
      const panel = document.getElementById("sp-filter-dates-panel");
      if (panel && !panel.classList.contains("hidden")) panel.classList.add("hidden");
    }
  });
