/* Beehus Console — IdentifyTxn: identificar transações (objeto grande).
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
const IdentifyTxn = {
  _inited: false,
  _txns: [],
  _suggestions: {},   // txnId → {beehusTransactionType, securityId, needsSecurity, executionPrice, irrf, pu, …}
  // Operator overrides for the Preço exec. / IRRF columns: txnId → {executionPrice?, irrf?}.
  // A key present with '' / null means "cleared" (won't be uploaded). Absent key =
  // use the server-computed value from _suggestions[id]. Reset on every search/reset
  // and per-row on re-identify so a fresh calculation always wins.
  _execEdits: {},

  // Monotonic generation counter. Bumped at the start of every search() and
  // runIdentify(); responses whose stamp is older than the current value are
  // discarded so a stale identify cannot write into a freshly-loaded result.
  _opGen: 0,
  _truncated: false,
  _skippedRows: [],

  // Client-side description filter (case-insensitive substring). Applied
  // alongside the confidence filter inside `_visibleTxns()`. Empty string
  // means "no filter".
  _descFilter: '',

  // Client-side filter on `t.type` (current DB transactionType). Empty
  // string means "Todos". The sentinel "(vazio)" matches rows whose
  // `type` is null/empty/missing — useful for narrowing the "Não
  // identificadas" view to the rows that still need a tipo suggestion.
  _typeFilter: '',

  // Anchor index for shift+click range selection on the result table.
  // Stores the DOM index of the last `.i-row` checkbox the operator
  // clicked (against the current rendered ordering). Reset to null on
  // every `_render()` because filter/sort changes invalidate indices.
  _lastClickedRowIdx: null,

  // Filter state — mirrors DeleteTxn for parity.
  _groupings: [],
  _groupingSelectedIds: new Set(),
  _wallets: [],
  _walletSelectedIds: new Set(),
  _entities: [],
  _securities: [],
  _entityIds: new Set(),
  _securityIds: new Set(),
  _typeIds: new Set(),

  // Config — populated from /api/beehus/identify-transactions/config.
  _allTypes: [],
  _typesNeedingSecurity: new Set(),

  // ── Edit/Delete state (merged from the former DeleteTxn view) ───────────
  // The unified screen edits and deletes transactions on the SAME grid it
  // identifies them on. These mirror DeleteTxn's edit machinery, adapted to
  // IdentifyTxn's state (`_txns`, `_selectedIds`, `_render`):
  //   _edits[fieldKey] = { oldValue: newValue }   — broad, by current-value bucket
  //   _tempEdits[txnId] = { fieldKey: newValue }   — narrow, per-row inline edits
  // `_computeEditPatches` merges both (per-row wins on conflict). Cleared on
  // every search()/reset()/runEdit(). NOTE: distinct from `_execEdits`
  // (Preço exec./IRRF) and from `_suggestions` (identify flow) — they never
  // overlap. The names `_computePatches`/`_pendingPatches`/temp-reinforce are
  // already taken by the identify flow, hence the `Edit`-suffixed variants.
  _edits: {},
  _editingField: null,
  _tempEdits: {},
  _inlineEditField: null,
  _pendingEditPatches: null,
  // Persistent selection set (txn ids), the single source of truth for which
  // rows are selected. View-independent: a filter (descrição/tipo/fonte/conf)
  // only changes which rows are VISIBLE, never the selection — so narrowing then
  // widening a filter never silently drops a tick (and never silently re-selects
  // either, the mass-delete footgun once Excluir lives on this grid). Operations
  // act on VISIBLE ∩ selected (see _selectedIds); a fresh search() selects all.
  _selSet: new Set(),
  // Max selected+visible rows to render inline-edit inputs for. Above this the
  // inline column falls back to plain text + the value-mapping modal (rendering
  // thousands of large <select>s would hang the browser).
  INLINE_EDIT_MAX_ROWS: 200,

  // Field metadata for the edit configurator. `txnKey` is the property on the
  // search-result transaction; `apiKey` is what the upstream PATCH expects.
  EDIT_FIELDS: {
    liquidationDate:       {label: 'Data de liquidação', txnKey: 'liquidationDate', apiKey: 'liquidationDate', kind: 'date'},
    entityId:              {label: 'Entidade',           txnKey: 'entityId',        apiKey: 'entityId',        kind: 'entity'},
    beehusTransactionType: {label: 'Tipo',               txnKey: 'type',            apiKey: 'beehusTransactionType', kind: 'type'},
    securityId:            {label: 'Security',           txnKey: 'securityId',      apiKey: 'securityId',      kind: 'security'},
    description:           {label: 'Descrição',          txnKey: 'description',     apiKey: 'description',     kind: 'text'},
  },
  // Static fallback for the type controls when the config endpoint hasn't
  // populated `_allTypes` yet. Kept in sync with DEVELOPER_GUIDE.md.
  EDIT_TYPES: [
    'amortization', 'brokerageFee', 'buySell', 'bzFundTaxes',
    'contributionAdjustment', 'coupon', 'dividend', 'dividendOnboarding',
    'gainsExpenses', 'interestOnEquity', 'managementFee', 'maturity', 'other',
    'otherFee', 'performanceFee', 'rebate', 'securityContributionAdjustment',
    'securityTransfer', 'taxes', 'withdrawalDeposit', 'withdrawalDepositAdjustment',
  ],
  _editTypes() {
    return (this._allTypes && this._allTypes.length) ? this._allTypes : this.EDIT_TYPES;
  },

  async init() {
    const r = await api('GET', '/api/beehus/filters/companies');
    this._fillSelect('i-company', r.body || [], '(selecione)');
    await this._loadConfig();
    this._renderTypes();
    this._renderEntities();
    this._renderSecurities();
  },

  // Deep-link entry point used by the Painel de Controle "TXN" column. Selects
  // the given company + liquidation date, forces the "Não identificadas"
  // filter, and runs the search so the operator lands on exactly the rows
  // behind the counter they clicked. Awaits init() so the company <select> is
  // already populated (View.show kicks init off but doesn't await it).
  async prefillFromPainel(companyId, date) {
    if (!companyId || !date) return;
    if (!this._inited) { this._inited = true; this._initPromise = this.init(); }
    try { await this._initPromise; } catch (e) { /* init failed; bail below */ }

    const comp = document.getElementById('i-company');
    comp.value = companyId;
    if (comp.value !== companyId) {
      alert('Empresa não disponível no Beehus Console (sem visibilidade ou não carregada).');
      return;
    }
    await this.onCompanyChange();          // load groupings/wallets/entities/securities

    this.onModeChange('single');
    document.getElementById('i-initialDate').value = date;

    const radio = document.querySelector('input[name="i-identified"][value="false"]');
    if (radio) radio.checked = true;

    await this.search();
  },

  async _loadConfig() {
    const r = await api('GET', '/api/beehus/identify-transactions/config');
    const b = r.body || {};
    this._allTypes = Array.isArray(b.allTypes) ? b.allTypes : [];
    this._typesNeedingSecurity = new Set(b.typesNeedingSecurity || []);
  },

  // ── Mode switch (single date vs range) ────────────────────────────────
  // Segmented toggle next to the "Data de liquidação" label. The mode is
  // tracked on the IdentifyTxn instance so we don't need a hidden radio
  // group anymore — the visible state of the buttons IS the source of truth.
  _mode: 'single',
  onModeChange(mode) {
    if (mode !== 'range') mode = 'single';
    this._mode = mode;
    document.getElementById('i-finalDate-wrap').classList.toggle('hidden', mode !== 'range');
    const label = document.getElementById('i-initialDate-label');
    if (label) label.innerHTML =
      (mode === 'range' ? 'Data inicial' : 'Data de liquidação') + ' <span class="text-red-500">*</span>';
    const btnSingle = document.getElementById('i-mode-single');
    const btnRange  = document.getElementById('i-mode-range');
    if (btnSingle) btnSingle.classList.toggle('active', mode === 'single');
    if (btnRange)  btnRange .classList.toggle('active', mode === 'range');
  },
  _modeIsRange() { return this._mode === 'range'; },

  _fillSelect(id, items, placeholder, {disabled = false} = {}) {
    const el = document.getElementById(id);
    el.disabled = disabled;
    el.innerHTML = `<option value="">${placeholder}</option>` +
      items.map(i => `<option value="${i.id}">${this._escape(i.name)}</option>`).join('');
  },
  _escape(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },
  // Safely interpolate a value as a JS string literal inside an
  // HTML="..." attribute. Returns e.g. `"abc"` (with HTML-quoted quotes)
  // so it survives both JS and HTML parsing — a `'` in the value can no
  // longer break out of the JS string.
  _jsAttr(s) { return JSON.stringify(String(s ?? '')).replace(/"/g, '&quot;'); },
  _highlighted(id) {
    return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
  },

  // ── Type chip filter ──────────────────────────────────────────────────
  _renderTypes() {
    const sel = document.getElementById('i-type-add');
    const remaining = this._allTypes.filter(t => !this._typeIds.has(t));
    sel.innerHTML = '<option value="">(adicionar tipo)</option>' +
      remaining.map(t => `<option value="${t}">${this._escape(t)}</option>`).join('');
    sel.disabled = remaining.length === 0;
    const chips = document.getElementById('i-type-chips');
    chips.innerHTML = Array.from(this._typeIds).map(t => `
      <span class="type-chip">${this._escape(t)}
        <button type="button" class="chip-x" title="Remover"
                onclick="IdentifyTxn.removeType('${t.replace(/'/g, "\\'")}')">×</button>
      </span>`).join('');
  },
  addType(t)    { if (!t) return; this._typeIds.add(t); document.getElementById('i-type-add').value = ''; this._renderTypes(); },
  removeType(t) { this._typeIds.delete(t); this._renderTypes(); },

  _renderEntities() {
    const sel = document.getElementById('i-entity-add');
    const remaining = this._entities.filter(e => !this._entityIds.has(e.id));
    sel.innerHTML = '<option value="">(adicionar entidade)</option>' +
      remaining.map(e => `<option value="${e.id}">${this._escape(e.name)}</option>`).join('');
    sel.disabled = !this._entities.length;
    const chips = document.getElementById('i-entity-chips');
    const byId = new Map(this._entities.map(e => [e.id, e.name]));
    chips.innerHTML = Array.from(this._entityIds).map(id => `
      <span class="type-chip">${this._escape(byId.get(id) || id)}
        <button type="button" class="chip-x" title="Remover"
                onclick="IdentifyTxn.removeEntity('${id.replace(/'/g, "\\'")}')">×</button>
      </span>`).join('');
  },
  addEntity(id)    { if (!id) return; this._entityIds.add(id); document.getElementById('i-entity-add').value = ''; this._renderEntities(); },
  removeEntity(id) { this._entityIds.delete(id); this._renderEntities(); },

  _renderSecurities() {
    const sel = document.getElementById('i-security-add');
    const remaining = this._securities.filter(s => !this._securityIds.has(s.id));
    const slice = remaining.slice(0, 2000);
    sel.innerHTML = '<option value="">(adicionar security)</option>' +
      slice.map(s => `<option value="${s.id}">${this._escape(s.name)}</option>`).join('') +
      (remaining.length > slice.length
        ? `<option value="" disabled>… ${remaining.length - slice.length} omitidos</option>`
        : '');
    sel.disabled = !this._securities.length;
    const chips = document.getElementById('i-security-chips');
    const byId = new Map(this._securities.map(s => [s.id, s.name]));
    chips.innerHTML = Array.from(this._securityIds).map(id => `
      <span class="type-chip">${this._escape(byId.get(id) || id)}
        <button type="button" class="chip-x" title="Remover"
                onclick="IdentifyTxn.removeSecurity('${id.replace(/'/g, "\\'")}')">×</button>
      </span>`).join('');
  },
  addSecurity(id)    { if (!id) return; this._securityIds.add(id); document.getElementById('i-security-add').value = ''; this._renderSecurities(); },
  removeSecurity(id) { this._securityIds.delete(id); this._renderSecurities(); },

  // ── Company change ────────────────────────────────────────────────────
  async onCompanyChange() {
    const cid = document.getElementById('i-company').value;
    this._groupings = [];
    this._groupingSelectedIds = new Set();
    this._wallets = [];
    this._walletSelectedIds = new Set();
    this._entities = [];
    this._entityIds = new Set();
    this._securities = [];
    this._securityIds = new Set();
    this._renderGroupingPanes();
    this._renderPanes();
    this._renderEntities();
    this._renderSecurities();
    if (!cid) return;

    const [groupings, wallets, entities, securities] = await Promise.all([
      api('GET', `/api/beehus/filters/groupings?companyId=${encodeURIComponent(cid)}`),
      api('GET', `/api/beehus/filters/wallets?companyId=${encodeURIComponent(cid)}`),
      api('GET', `/api/beehus/filters/entities?companyId=${encodeURIComponent(cid)}`),
      api('GET', '/api/beehus/filters/securities'),
    ]);
    this._groupings  = groupings.body  || [];
    this._wallets    = wallets.body    || [];
    this._entities   = entities.body   || [];
    this._securities = securities.body || [];
    this._renderGroupingPanes();
    this._renderPanes();
    this._renderEntities();
    this._renderSecurities();
  },

  // ── Groupings picker ──────────────────────────────────────────────────
  _walletFilterFromGroupings() {
    if (!this._groupingSelectedIds.size) return null;
    const ids = new Set();
    for (const g of this._groupings) {
      if (this._groupingSelectedIds.has(g.id)) {
        (g.walletIds || []).forEach(w => ids.add(w));
      }
    }
    return ids;
  },
  _renderGroupingPanes() {
    const avail = document.getElementById('i-grp-available');
    const sel   = document.getElementById('i-grp-selected');
    const available = this._groupings.filter(g => !this._groupingSelectedIds.has(g.id));
    const selected  = this._groupings.filter(g =>  this._groupingSelectedIds.has(g.id));
    avail.innerHTML = available.map(g => `<option value="${g.id}">${this._escape(g.name)}</option>`).join('');
    sel.innerHTML   = selected .map(g => `<option value="${g.id}">${this._escape(g.name)}</option>`).join('');
    document.getElementById('i-grp-available-count').textContent = available.length;
    document.getElementById('i-grp-selected-count').textContent  = selected.length;
    this._renderPanes();
  },
  addGroupingSelected()    { this._highlighted('i-grp-available').forEach(id => this._groupingSelectedIds.add(id)); this._renderGroupingPanes(); },
  addGroupingAll()         { Array.from(document.getElementById('i-grp-available').options).forEach(o => o.value && this._groupingSelectedIds.add(o.value)); this._renderGroupingPanes(); },
  removeGroupingSelected() { this._highlighted('i-grp-selected').forEach(id => this._groupingSelectedIds.delete(id)); this._renderGroupingPanes(); },
  removeGroupingAll()      { this._groupingSelectedIds.clear(); this._renderGroupingPanes(); },

  // ── Wallets picker ────────────────────────────────────────────────────
  _renderPanes() {
    const filter = this._walletFilterFromGroupings();
    const avail = document.getElementById('i-available');
    const sel   = document.getElementById('i-selected');
    const available = this._wallets.filter(w =>
      (!filter || filter.has(w.id)) && !this._walletSelectedIds.has(w.id)
    );
    const selected = this._wallets.filter(w => this._walletSelectedIds.has(w.id));
    avail.innerHTML = available.map(w => `<option value="${w.id}">${this._escape(w.name)}</option>`).join('');
    sel.innerHTML   = selected .map(w => `<option value="${w.id}">${this._escape(w.name)}</option>`).join('');
    document.getElementById('i-available-count').textContent = available.length;
    document.getElementById('i-wallet-count').textContent    = selected.length;
    document.getElementById('i-available-filter').classList.toggle('hidden', !filter);
  },
  addSelected()    { this._highlighted('i-available').forEach(id => this._walletSelectedIds.add(id)); this._renderPanes(); },
  addAll()         { Array.from(document.getElementById('i-available').options).forEach(o => o.value && this._walletSelectedIds.add(o.value)); this._renderPanes(); },
  removeSelected() { this._highlighted('i-selected').forEach(id => this._walletSelectedIds.delete(id)); this._renderPanes(); },
  removeAll()      { this._walletSelectedIds.clear(); this._renderPanes(); },

  // ── Search ────────────────────────────────────────────────────────────
  // `opts.preserveSuggestions` — when true, keeps `_suggestions` entries
  // whose txn id is still present in the new result set. Used by
  // `runApply` so the implicit refresh after Implementar selecionadas
  // doesn't force the operator to re-run Identificar on rows that
  // weren't part of the just-implemented batch. Stale entries (rows
  // that dropped out of the result, e.g. now Identificadas under a
  // "Não identificadas" toggle) are pruned so the suggestion map can't
  // grow unbounded across many Implementar cycles.
  async search(opts) {
    const preserveSug = !!(opts && opts.preserveSuggestions);
    const cid  = document.getElementById('i-company').value;
    const ini  = document.getElementById('i-initialDate').value;
    const isRange = this._modeIsRange();
    const fin  = isRange ? document.getElementById('i-finalDate').value : ini;

    if (!cid)        { alert('Selecione uma empresa.'); return; }
    if (!ini)        { alert('Informe a data de liquidação.'); return; }
    if (isRange) {
      if (!document.getElementById('i-finalDate').value) { alert('Informe a data final.'); return; }
      if (ini > fin)                                     { alert('Data inicial não pode ser maior que a final.'); return; }
    }

    const identified = (document.querySelector('input[name="i-identified"]:checked') || {}).value || '';

    const payload = {
      companyId:              cid,
      initialDate:            ini,
      finalDate:              fin,
      groupingIds:            Array.from(this._groupingSelectedIds),
      walletIds:              Array.from(this._walletSelectedIds),
      beehusTransactionTypes: Array.from(this._typeIds),
      entityIds:              Array.from(this._entityIds),
      securityIds:            Array.from(this._securityIds),
      identified:             identified,
    };
    const myGen = ++this._opGen;
    showBusy('Buscando transações…');
    let r;
    try {
      r = await api('POST', '/api/beehus/transactions/search', payload);
    } finally {
      hideBusy();
    }
    if (myGen !== this._opGen) return;   // a newer search/identify started
    if (!r.ok) { alert('Falha na busca. Veja o Log.'); return; }
    this._txns = r.body?.transactions || [];
    if (preserveSug) {
      // Drop suggestions for ids that aren't in the new result set; keep
      // the rest so the operator can keep filtering/implementing without
      // re-running Identificar.
      const aliveIds = new Set(this._txns.map(t => t.id));
      for (const id of Object.keys(this._suggestions)) {
        if (!aliveIds.has(id)) delete this._suggestions[id];
      }
      for (const id of Object.keys(this._execEdits)) {
        if (!aliveIds.has(id)) delete this._execEdits[id];
      }
    } else {
      this._suggestions = {};
      this._execEdits = {};
    }
    this._truncated = !!r.body?.truncated;
    // A new result set is a fresh selection: select every row. Subsequent
    // re-renders (filtros/sort/sugestão/inline) read from _selSet and so
    // preserve the operator's ticks.
    this._selSet = new Set(this._txns.map(t => t.id));
    // Editar/Excluir: per-row overrides and the active inline-edit column target
    // specific ids that don't carry across a new result set.
    this._tempEdits = {};
    this._inlineEditField = null;
    if (!preserveSug) {
      // Bucket mappings (`_edits`) are by current-value and apply to ANY checked
      // row whose value matches — keeping them across a fresh search would let an
      // old mapping silently hit a different/larger set. Drop them on a fresh
      // search (mirrors `_tempEdits`); preserveSug (the post-Implementar refresh)
      // keeps them so an in-progress edit batch survives the implicit reload.
      this._edits = {};
      // Reset the description filter so a stale "trecho" from a previous
      // result set doesn't silently hide rows the operator expects to see.
      this._descFilter = '';
      const descInput = document.getElementById('i-desc-filter');
      if (descInput) descInput.value = '';
      // Same rationale for the tipo filter: a fresh result set is a fresh
      // starting point. `_refreshTypeFilterOptions` repopulates the options
      // from the new `_txns` during the render.
      this._typeFilter = '';
      // Fonte filter is a static dropdown — reset both state and the <select>.
      this._sourceFilter = '';
      const srcSel = document.getElementById('i-source-filter');
      if (srcSel) srcSel.value = '';
    }
    // (When preserveSug is true — i.e. the implicit refresh after
    // Implementar — `_descFilter` and `_typeFilter` are kept so the
    // operator can keep iterating on the same narrowing intent.
    // `_refreshTypeFilterOptions` will reset `_typeFilter` if the
    // previously selected bucket disappeared from the new result.)
    this._render();

    // Auto-hide the optional groupings + wallets cards once the search has
    // run. The selections themselves stay in `_groupingSelectedIds` /
    // `_walletSelectedIds` (the search summary line below the action row
    // shows what was applied), so reopening the advanced toggle restores
    // exactly what the operator picked. Keeps the action surface tight when
    // the result table is visible.
    document.getElementById('i-card-groupings').classList.add('hidden');
    document.getElementById('i-card-wallets').classList.add('hidden');
    const advBtn = document.getElementById('i-toggle-advanced');
    if (advBtn) advBtn.textContent = '+ Groupings & Wallets';

    const companyName = document.getElementById('i-company').selectedOptions[0]?.textContent || '';
    const dateLabel = isRange ? `${ini} → ${fin}` : ini;
    const idLabel = identified === 'true' ? 'identificadas'
                  : identified === 'false' ? 'não identificadas'
                  : 'todas';
    const summary = [
      companyName,
      dateLabel,
      idLabel,
      this._groupingSelectedIds.size ? `${this._groupingSelectedIds.size} grouping(s)` : null,
      this._walletSelectedIds.size   ? `${this._walletSelectedIds.size} wallet(s)`     : 'todas as wallets',
      this._entityIds.size           ? `${this._entityIds.size} entidade(s)`            : null,
      this._typeIds.size             ? `tipos: ${Array.from(this._typeIds).join(', ')}` : null,
      this._securityIds.size         ? `${this._securityIds.size} security(ies)`        : null,
    ].filter(Boolean).join(' · ');
    document.getElementById('i-search-summary').textContent = summary;
  },

  // Toggle the optional groupings + wallets cards together. Most runs leave
  // them empty (search defaults to the entire company) so we keep the
  // initial view tight.
  toggleAdvancedFilters() {
    const grp = document.getElementById('i-card-groupings');
    const wal = document.getElementById('i-card-wallets');
    const btn = document.getElementById('i-toggle-advanced');
    const willShow = grp.classList.contains('hidden');
    grp.classList.toggle('hidden', !willShow);
    wal.classList.toggle('hidden', !willShow);
    if (willShow) {
      // Make sure the bodies are expanded when we reveal the cards;
      // otherwise the operator sees a header strip with no obvious next step.
      toggleCard('i-card-groupings', false);
      toggleCard('i-card-wallets', false);
    }
    if (btn) btn.textContent = willShow
      ? '− Ocultar groupings & wallets'
      : '+ Groupings & Wallets';
  },

  // ── Render result table ───────────────────────────────────────────────
  // The original implementation baked per-row <select> dropdowns for both
  // type and security; rendering 1000 rows × ~2000 securities froze the
  // browser. The dropdowns were replaced with text + Edit-button + modal,
  // so each row now ships a fixed-size cell and the per-company option-HTML
  // caching is no longer necessary.

  // Confidence cell renderer. Buckets follow the thresholds in
  // data/transaction_type_rules.json (`_thresholds`):
  //   ≥ 0.95 → high (green)   — basically all rule matches
  //   0.70–0.95 → mid (blue)
  //   0.50–0.70 → review (amber) — UI flags `needs_review`
  //   < 0.50 → critical (red) — backend treats as unclassified
  _confidenceBadge(sug) {
    if (!sug || sug.confidence == null) {
      return '<span class="text-gray-400 italic">—</span>';
    }
    const c = sug.confidence;
    let cls;
    if      (c >= 0.95) cls = 'badge-ok';
    else if (c >= 0.70) cls = 'badge-info';
    else if (c >= 0.50) cls = 'badge-amber';
    else                cls = 'badge-err';
    const pct = (c * 100).toFixed(c >= 0.995 ? 0 : 1) + '%';
    const tip = sug.source === 'ml'
      ? `modelo ML${sug.needsReview ? ' • revisar' : ''}`
      : sug.source ? `regra: ${sug.source}` : '';
    return `<span class="conf-badge ${cls}" title="${this._escape(tip)}">${pct}</span>`;
  },

  // "Tipo sugerido" cell — text + Edit button. Mirrors the security cell:
  // the chosen type lives in `_suggestions[txnId].beehusTransactionType`
  // and Implementar reads from there via _computePatches.
  _typeSuggestedCell(t, sug) {
    const suggested = sug && sug.beehusTransactionType ? sug.beehusTransactionType : '';
    const isReview  = !!(sug && sug.needsReview);
    let display;
    if (suggested) {
      const cls = isReview ? 'text-amber-700 dark:text-amber-300' : '';
      display = `<span class="text-[11px] ${cls}">${this._escape(suggested)}</span>`;
    } else {
      display = `<span class="text-gray-400 italic text-[11px]">(manter)</span>`;
    }
    const btn = `<button class="btn btn-muted" style="padding:1px 6px;font-size:10px;line-height:1.2"
                         onclick="IdentifyTxn.openTypeEdit(${this._jsAttr(t.id)})"
                         title="Editar tipo">✎ Editar</button>`;
    return `<div class="flex items-center gap-1">${display}${btn}</div>`;
  },

  // "Security sugerido" cell — text + Edit button. The chosen securityId
  // lives in `_suggestions[txnId]` (no hidden select), and Implementar
  // reads from that store via _computePatches.
  // The Edit button is **always enabled** so the operator can attach a
  // security to any row — including rows whose suggested type doesn't
  // normally need one (the `(N/A)` label is kept as a visual hint, not a
  // hard block). `openSecEdit` initialises an empty `_suggestions[txnId]`
  // entry on demand, so clicking from a row the classifier never touched
  // works the same as editing an existing suggestion.
  _securitySuggestedCell(t, sug, enabled) {
    const id   = sug && sug.securityId ? sug.securityId : '';
    const name = sug && sug.securityName ? sug.securityName : '';
    const main = sug && sug.securityMainId ? sug.securityMainId : '';
    let display;
    if (id && (name || main)) {
      const label = main ? (main + (name ? ' · ' : '') + name) : name;
      display = `<span class="text-[11px]">${this._escape(label.slice(0, 60))}</span>`;
    } else if (enabled) {
      display = `<span class="text-gray-400 italic text-[11px]">(manter)</span>`;
    } else {
      display = `<span class="text-gray-300 italic text-[11px]">(N/A)</span>`;
    }
    const btn = `<button class="btn btn-muted" style="padding:1px 6px;font-size:10px;line-height:1.2"
                         onclick="IdentifyTxn.openSecEdit(${this._jsAttr(t.id)})"
                         title="Editar security (disponível para qualquer linha)">✎ Editar</button>`;
    return `<div class="flex items-center gap-1">${display}${btn}</div>`;
  },

  // Human-readable label for a security-identification source. Mirrors the
  // Fonte-cell MAP; used in the score tooltip header.
  _secSourceLabel(src) {
    return ({
      reinforcement:              'Reforço (match exato)',
      reinforcement_partial:      'Reforço (substring)',
      temp_reinforcement:         'Reforço temporário (exato)',
      temp_reinforcement_partial: 'Reforço temporário (substring)',
      level1:                     'L1 — carteira em D',
      level2:                     'L2 — carteira em D-1/D-2 dias úteis',
      level3:                     'L3 — cadastro completo',
      amount_diff_tiebreaker:     'Desempate por amountDifference',
      user:                       'Seleção manual',
    })[src] || src || '—';
  },

  // The per-candidate score decomposition as text lines. `breakdown` is the
  // array the backend attaches to each alternative (_score_breakdown); the
  // entries always sum to `score`. Returns [] when there is no breakdown
  // (e.g. a reinforcement-sourced pick has no cascade score to decompose).
  _scoreBreakdownLines(breakdown, score) {
    if (!breakdown || !breakdown.length) return [];
    const lines = breakdown.map(c => `• ${c.label}: ${(c.points >= 0 ? '+' : '') + c.points}`);
    lines.push('──────────────');
    lines.push(`Score total: ${score != null ? score : '—'}`);
    return lines;
  },

  // Full tooltip for the security identification: HOW the score was computed
  // (each signal's point contribution, summing to the total) and how that
  // total maps to the confidence % (score/100, clamped at 100%, capped at 65%
  // when ambiguous), plus the buySell desempate detail when it applied. Falls
  // back to a short origin note for reinforcement-sourced picks (no cascade
  // score to decompose).
  _securityScoreTooltip(sug) {
    if (!sug || !sug.needsSecurity) return '';
    const src    = sug.securitySource || '';
    const alts   = sug.securityAlternatives || [];
    const chosen = alts.find(a => a.securityId === sug.securityId) || alts[0] || null;
    const breakdown = chosen ? chosen.breakdown : null;
    const score  = chosen && chosen.score != null ? chosen.score
                 : (sug.securityScore != null ? sug.securityScore : null);
    const confPct = sug.securityConfidence != null
      ? (sug.securityConfidence * 100).toFixed(0) + '%' : '—';

    if (!breakdown || !breakdown.length) {
      return `Origem: ${this._secSourceLabel(src)}\nConfiança: ${confPct}`;
    }

    const lines = [`Como o score foi calculado — Fonte: ${this._secSourceLabel(src)}`];
    lines.push(...this._scoreBreakdownLines(breakdown, score));
    if (score != null) {
      if (score > 100) {
        lines.push(`Confiança = min(1; ${score}/100) → limitada a 100%`);
      } else {
        lines.push(`Confiança = ${score}/100 = ${score}%`);
      }
    }
    if (sug.securityAmbiguous) {
      lines.push('Ambíguo: 2º candidato ≥ 85% do 1º → confiança limitada a 65%');
    }
    if (sug.securityAmbiguous || (score != null && score > 100)) {
      lines.push(`Confiança final: ${confPct}`);
    }
    const tb = sug.securityTiebreak;
    if (tb && src === 'amount_diff_tiebreaker') {
      lines.push(`Desempate (buySell): ΔPosição ${tb.amountDifference != null ? tb.amountDifference : '?'} ` +
                 `entre ${tb.priorDate || '?'} (D-1 útil) e ${tb.navDate || '?'} (D)`);
    }
    return lines.join('\n');
  },

  // Security confidence cell. Adds a "Revisar" button when the classifier
  // flagged the candidate set as ambiguous (top-2 / top-1 ≥ 0.85). Clicking
  // the badge opens a modal listing the top-3 alternatives. The badge tooltip
  // shows the full score calculation (see _securityScoreTooltip).
  _securityConfidenceCell(txnId, sug) {
    if (!sug || !sug.needsSecurity) {
      return '<span class="text-gray-400 italic">—</span>';
    }
    if (sug.securityConfidence == null && (!sug.securityAlternatives || !sug.securityAlternatives.length)) {
      return '<span class="text-gray-400 italic">—</span>';
    }
    const c = sug.securityConfidence ?? 0;
    let cls;
    if      (c >= 0.80) cls = 'badge-ok';
    else if (c >= 0.55) cls = 'badge-info';
    else if (c >= 0.30) cls = 'badge-amber';
    else                cls = 'badge-err';
    const pct = (c * 100).toFixed(c >= 0.995 ? 0 : 0) + '%';
    let tip = this._securityScoreTooltip(sug);
    const reviewable = (sug.securityAlternatives && sug.securityAlternatives.length > 1) || sug.securityAmbiguous;
    if (reviewable) tip += '\n(clique para revisar candidatos)';
    const onclick = reviewable ? ` onclick="IdentifyTxn.openSecEdit(${this._jsAttr(txnId)})" style="cursor:pointer"` : '';
    const ambIcon = sug.securityAmbiguous ? ' ⚠' : '';
    return `<span class="conf-badge ${cls}" title="${this._escape(tip)}"${onclick}>${pct}${ambIcon}</span>`;
  },

  // "Fonte" cell — where the SECURITY identification came from, shown next to
  // Conf. Sec. Maps the raw `securitySource` to a short badge:
  //   Reforço (rule, exact/~partial) · L1/L2/L3 (wallet cascade depth) ·
  //   Desempate (amountDifference tie-breaker) · Manual (operator pick).
  // L3 (cadastro completo) is the weakest signal, so it's amber.
  _securitySourceCell(sug) {
    if (!sug || !sug.needsSecurity) return '<span class="text-gray-400 italic">—</span>';
    const src = sug.securitySource || '';
    const MAP = {
      reinforcement:              ['Reforço',     'badge-ok',    'Reforço — match exato da descrição'],
      reinforcement_partial:      ['Reforço~',    'badge-ok',    'Reforço — match por substring'],
      temp_reinforcement:         ['Reforço tmp', 'badge-info',  'Reforço temporário — match exato'],
      temp_reinforcement_partial: ['Reforço tmp~','badge-info',  'Reforço temporário — substring'],
      level1:                     ['L1',          'badge-ok',    'L1 — posição da carteira em D (liquidação)'],
      level2:                     ['L2',          'badge-info',  'L2 — carteira em D-1/D-2 dias úteis'],
      level3:                     ['L3',          'badge-amber', 'L3 — cadastro completo (busca ampla)'],
      amount_diff_tiebreaker:     ['Desempate',   'badge-info',  'Desempate por amountDifference (buySell)'],
      user:                       ['Manual',      'badge-info',  'Seleção manual do operador'],
    };
    const m = MAP[src];
    if (!m) {
      return src
        ? `<span class="conf-badge" title="${this._escape(src)}">${this._escape(src)}</span>`
        : '<span class="text-gray-400 italic">—</span>';
    }
    return `<span class="conf-badge ${m[1]}" title="${this._escape(m[2])}">${this._escape(m[0])}</span>`;
  },

  // ── Security edit modal ───────────────────────────────────────────────
  // Replaces the per-row "Security sugerido" dropdown. User clicks the
  // "Editar" button (or the ambiguous Conf. Sec. badge) to open this modal.
  // The modal shows the classifier's top-3 alternatives at the top and a
  // free-form search box below to find ANY other security in the company's
  // cached list.
  openSecEdit(txnId) {
    if (!this._suggestions[txnId]) {
      // Initialise an empty suggestion so the user can edit even rows the
      // classifier hasn't run on yet.
      this._suggestions[txnId] = {
        beehusTransactionType: '', securityId: '', needsSecurity: true,
        confidence: null, source: '', needsReview: false,
        securityName: '', securityMainId: '',
        securityConfidence: null, securitySource: '',
        securityAmbiguous: false, securityAlternatives: [],
      };
    }
    this._secEditTxnId = txnId;
    // Reset the per-modal expand/collapse state so a previously expanded
    // L1 from another row doesn't leak into the current open.
    this._secEditExpanded = {};
    this._secEditGroups = null;
    this._renderSecEditFull('');
    document.getElementById('i-sec-edit-modal').classList.add('show');
    setTimeout(() => {
      const inp = document.getElementById('i-sec-edit-search');
      if (inp) inp.focus();
    }, 50);
  },
  closeSecEdit() {
    document.getElementById('i-sec-edit-modal').classList.remove('show');
    this._secEditTxnId = null;
  },
  // Triggered oninput of the search input. Renders ONLY the results table
  // (not the input itself) so focus and cursor position are preserved
  // between keystrokes.
  secEditSearch() {
    const inp = document.getElementById('i-sec-edit-search');
    this._renderSecEditResults(inp ? inp.value : '');
  },

  // Renders the full modal body once when the modal opens. Search-driven
  // re-renders go through `_renderSecEditResults` which only touches the
  // results table.
  _renderSecEditFull(query) {
    const txnId = this._secEditTxnId;
    const sug = this._suggestions[txnId] || {};
    const txn = this._txns.find(t => t.id === txnId);
    const headerLabel = txn
      ? `${this._escape(txn.liquidationDate)} · ${this._escape(txn.walletName)} · ${this._escape((txn.description || '').slice(0, 120))}`
      : '';
    const currentLabel = sug.securityId
      ? (sug.securityMainId || '') + ' · ' + (sug.securityName || '')
      : '<em>(vazio)</em>';

    // ── Top-N alternatives split into 3 levels (L1/L2/L3) ──────────────────
    // The classifier tags every alternative with a `source`:
    //   level1     — security está na posição mais recente da carteira (T)
    //   level2     — security está em T-1/T-2 da carteira, mas não em T
    //   collection — security não está em nenhum processedPositions recente,
    //                vem do cadastro completo (fallback L3)
    // Cada grupo é renderizado num `#i-sec-edit-level-{l1|l2|l3}` próprio.
    // O grupo L1 ganha um botão "Expandir posição completa" que troca a
    // tabela curta (top-N do matcher) pela posição inteira via
    // /api/beehus/identify-transactions/wallet-position-detail.
    const alts = sug.securityAlternatives || [];
    const groups = {l1: [], l2: [], l3: []};
    alts.forEach(a => {
      if (a.source === 'level1') groups.l1.push(a);
      else if (a.source === 'level2') groups.l2.push(a);
      else groups.l3.push(a);   // 'collection' or unknown
    });
    // Stash the matcher-tagged groups so the expand/collapse handlers can
    // restore the original short list after the operator backs out of the
    // full-position view.
    this._secEditGroups = groups;
    const topSection = `
      <div class="text-[11px] font-medium text-gray-700 mb-1 mt-1">
        Sugestões do classificador <span class="text-gray-500">(agrupadas por nível)</span>
      </div>
      ${this._renderSecLevelGroup('l1', groups.l1, sug)}
      ${this._renderSecLevelGroup('l2', groups.l2, sug)}
      ${this._renderSecLevelGroup('l3', groups.l3, sug)}
    `;

    document.getElementById('i-sec-edit-body').innerHTML = `
      <div class="text-[11px] text-gray-600 mb-3 flex items-start justify-between gap-2">
        <div class="flex-1">
          <div>Transação: <span class="font-mono">${headerLabel}</span></div>
          <div>Tipo sugerido: <strong>${this._escape(sug.beehusTransactionType || '∅')}</strong>
               · Atualmente: ${currentLabel}</div>
        </div>
        <button onclick="IdentifyTxn.openReinforce()" class="btn btn-muted whitespace-nowrap"
                title="Salvar a descrição desta transação como reforço (match exato em identifies futuros)">
          ★ Salvar como reforço
        </button>
      </div>
      ${topSection}
      <div class="text-[11px] font-medium text-gray-700 mb-1">
        Buscar outra security <span class="text-gray-500">(cadastro completo da empresa)</span>
      </div>
      <div class="flex items-center gap-2 mb-2">
        <input id="i-sec-edit-search" class="input flex-1"
               placeholder="Buscar por mainId ou beehusName (ex: PETR4, Oikos, CRI22…)"
               oninput="IdentifyTxn.secEditSearch()" value="${this._escape(query || '')}" />
        <button class="btn btn-muted"
                onclick="IdentifyTxn.pickSecForEdit('', '', '')">
          Limpar (sem security)
        </button>
        <span id="i-sec-edit-hint" class="text-[10px] text-gray-500"></span>
      </div>
      <div class="table-wrap border rounded" style="max-height: 40vh">
        <table class="txn-table">
          <thead><tr>
            <th>mainId</th><th>beehusName</th><th>securityId</th><th></th>
          </tr></thead>
          <tbody id="i-sec-edit-results"></tbody>
        </table>
      </div>`;
    // Render the results section once on open so the user sees the
    // "Digite ao menos 2 caracteres" hint immediately.
    this._renderSecEditResults(query || '');
  },

  // ── Per-level group rendering (L1 / L2 / L3) ─────────────────────────────
  // Render one of the three "Sugestões do classificador" groups. `level` is
  // 'l1' | 'l2' | 'l3' and `items` is the slice of alternatives tagged with
  // that level (may be empty). When empty, we still render the section so
  // the operator can see "0 candidatos" and — for L1 — has the expand
  // button available even when the matcher returned no L1 hits.
  _renderSecLevelGroup(level, items, sug) {
    const labels = {
      l1: {title: 'L1 — Carteira (D)',              help: 'Securities na posição em D (liquidação)'},
      l2: {title: 'L2 — Carteira (D-1 / D-2 úteis)', help: 'Securities em D-1 ou D-2 dias úteis, ausentes de D'},
      l3: {title: 'L3 — Cadastro completo',     help: 'Fallback no cadastro global (ativo não está na carteira recente)'},
    };
    const meta = labels[level] || {title: level.toUpperCase(), help: ''};
    const badgeCls = level === 'l1' ? 'badge-ok'
                   : level === 'l2' ? 'badge-amber'
                   : 'badge-amber';
    // L1 is the only level with a "ver posição completa" action. For L2 the
    // ROI is low (operators rarely want to scroll T-1/T-2 in full); they can
    // still type into the search box if they need to find something off-screen.
    const expandBtn = level === 'l1'
      ? `<button id="i-sec-edit-l1-expand-btn"
                 class="btn btn-muted text-[10px] px-1.5 py-0.5"
                 onclick="IdentifyTxn.expandSecLevel('l1')"
                 title="Carregar todos os ativos que a carteira tem em D (liquidação)">
           Expandir posição completa
         </button>`
      : '';
    const count = items.length;
    return `
      <div class="border rounded mb-2" id="i-sec-edit-level-${level}-wrap">
        <div class="flex items-center justify-between px-2 py-1.5 bg-gray-50 dark:bg-gray-800 border-b">
          <div class="flex items-center gap-2">
            <span class="conf-badge ${badgeCls}" title="${this._escape(meta.help)}">${this._escape(meta.title)}</span>
            <span class="text-[10px] text-gray-500" id="i-sec-edit-level-${level}-count">${count} candidato${count === 1 ? '' : 's'}</span>
          </div>
          ${expandBtn}
        </div>
        <div id="i-sec-edit-level-${level}-body">
          ${this._secLevelTableHtml(items, sug, /*expanded*/ false)}
        </div>
      </div>`;
  },

  // Build the inner table for a level. Used both for the initial render
  // (matcher top-N) and for the post-expand render (full position).
  // `expanded=true` switches the columns to position-detail mode (with
  // quantity + PU) instead of score + reasons. Empty rows render an empty
  // table-wrap with a single "—" row so the layout stays consistent.
  _secLevelTableHtml(items, sug, expanded) {
    if (!items || !items.length) {
      return `<div class="px-3 py-2 text-[10px] text-gray-400 italic">Nenhum candidato neste nível.</div>`;
    }
    const headerCols = expanded
      ? `<th>mainId</th><th>beehusName</th><th>tipo</th><th class="text-right">qtd</th><th class="text-right">PU</th><th></th>`
      : `<th>mainId</th><th>beehusName</th><th>maturity</th><th class="text-right">score</th><th>razões</th><th></th>`;
    const rows = items.map(a => {
      const isPicked = a.securityId === sug.securityId;
      const reasonsText = (a.reasons || []).join(' · ');
      // Hover the score to see how it was computed (each signal's points).
      const scoreTip = this._scoreBreakdownLines(a.breakdown, a.score).join('\n');
      const scoreCell = scoreTip
        ? `<td class="text-right font-mono text-[11px]" style="cursor:help" title="${this._escape(scoreTip)}">${a.score != null ? a.score : '—'}</td>`
        : `<td class="text-right font-mono text-[11px]">${a.score != null ? a.score : '—'}</td>`;
      const bodyCols = expanded
        ? `<td class="font-mono text-[11px]">${this._escape(a.mainId || '')}</td>
           <td class="text-[11px]">${this._escape(a.beehusName || '')}</td>
           <td class="text-[10px] text-gray-500">${this._escape(a.securityType || '')}</td>
           <td class="text-right font-mono text-[11px]">${this._fmtSecNum(a.quantity)}</td>
           <td class="text-right font-mono text-[11px]">${this._fmtSecNum(a.pu)}</td>`
        : `<td class="font-mono text-[11px]">${this._escape(a.mainId || '')}</td>
           <td class="text-[11px]">${this._escape(a.beehusName || '')}</td>
           <td class="text-[11px]">${this._escape(a.maturityDate || '')}</td>
           ${scoreCell}
           <td class="text-[10px] text-gray-500">${this._escape(reasonsText)}</td>`;
      return `<tr class="${isPicked ? 'bg-amber-50 dark:bg-amber-900/30' : ''}">
        ${bodyCols}
        <td>
          <button class="btn ${isPicked ? 'btn-muted' : 'btn-primary'}"
                  onclick="IdentifyTxn.pickSecForEdit(${this._jsAttr(a.securityId)}, ${this._jsAttr(a.beehusName || '')}, ${this._jsAttr(a.mainId || '')})">
            ${isPicked ? 'Atual ✓' : 'Selecionar'}
          </button>
        </td>
      </tr>`;
    }).join('');
    return `<div class="table-wrap" style="max-height: 30vh">
      <table class="txn-table">
        <thead><tr>${headerCols}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  },

  _fmtSecNum(v) {
    if (v == null) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 8});
  },

  // Fetch the full L1 position (or L2 in the future) and replace the level
  // group's body with the expanded view. The button toggles to "Recolher"
  // so the operator can revert to the short matcher list at any time.
  // While loading we show a spinner; on error we restore the original list.
  async expandSecLevel(level) {
    const txnId = this._secEditTxnId;
    if (!txnId) return;
    const txn = this._txns.find(t => t.id === txnId);
    if (!txn) return;
    const body = document.getElementById(`i-sec-edit-level-${level}-body`);
    const btn  = document.getElementById(`i-sec-edit-${level}-expand-btn`);
    if (!body) return;

    // If already expanded, collapse back to the matcher's short list.
    if (this._secEditExpanded && this._secEditExpanded[level]) {
      const sug = this._suggestions[txnId] || {};
      const items = (this._secEditGroups || {})[level] || [];
      body.innerHTML = this._secLevelTableHtml(items, sug, false);
      this._secEditExpanded[level] = false;
      if (btn) btn.textContent = 'Expandir posição completa';
      const countEl = document.getElementById(`i-sec-edit-level-${level}-count`);
      if (countEl) countEl.textContent = `${items.length} candidato${items.length === 1 ? '' : 's'}`;
      return;
    }

    body.innerHTML = `<div class="px-3 py-3 text-[11px] text-gray-500">Carregando posição completa…</div>`;
    if (btn) btn.textContent = 'Carregando…';
    try {
      const params = new URLSearchParams({
        walletId:        txn.walletId,
        liquidationDate: txn.liquidationDate,
        level:           level,
      });
      const resp = await fetch(`/api/beehus/identify-transactions/wallet-position-detail?${params}`);
      const data = await resp.json();
      const secs = (data && data.securities) || [];
      const sug  = this._suggestions[txnId] || {};
      body.innerHTML = this._secLevelTableHtml(secs, sug, /*expanded*/ true);
      this._secEditExpanded = this._secEditExpanded || {};
      this._secEditExpanded[level] = true;
      if (btn) btn.textContent = 'Recolher';
      const countEl = document.getElementById(`i-sec-edit-level-${level}-count`);
      if (countEl) countEl.textContent = `${secs.length} ativo${secs.length === 1 ? '' : 's'} na posição`;
    } catch (e) {
      const sug = this._suggestions[txnId] || {};
      const items = (this._secEditGroups || {})[level] || [];
      body.innerHTML = this._secLevelTableHtml(items, sug, false);
      if (btn) btn.textContent = 'Expandir posição completa';
      console.error('expandSecLevel error', e);
    }
  },

  // Render only the results table + hint. Called on every keystroke. Does
  // NOT touch `i-sec-edit-search` so the input retains focus and the
  // browser doesn't reset the cursor position mid-typing.
  _renderSecEditResults(query) {
    const txnId = this._secEditTxnId;
    if (!txnId) return;
    const sug = this._suggestions[txnId] || {};
    const tbody = document.getElementById('i-sec-edit-results');
    const hint  = document.getElementById('i-sec-edit-hint');
    if (!tbody || !hint) return;

    const q = (query || '').trim().toLowerCase();
    if (q.length < 2) {
      tbody.innerHTML = '';
      hint.textContent = 'Digite ao menos 2 caracteres';
      return;
    }

    // Search the entire SecurityCache (full cadastro of the company). The
    // wallet-scoped pool (L1∪L2) is already exposed at the top of the modal
    // through the L1/L2 groups and the "Expandir posição completa" button —
    // restricting the free-text search to L1∪L2 too would just duplicate
    // that view and prevent the operator from picking any other security
    // present in the cadastro (legitimate use case: ativo novo, transferência
    // entre carteiras, recém-cadastrado, etc).
    const universe = this._securities || [];
    const matches = universe.filter(s => {
      const id = (s.id || '').toLowerCase();
      const nm = (s.name || '').toLowerCase();
      const mid = (s.mainId || '').toLowerCase();
      return id.includes(q) || nm.includes(q) || mid.includes(q);
    });
    const slice = matches.slice(0, 100);
    tbody.innerHTML = slice.map(s => {
      const isPicked = s.id === sug.securityId;
      const mainId = s.mainId || '';
      return `
        <tr class="${isPicked ? 'bg-amber-50 dark:bg-amber-900/30' : ''}">
          <td class="font-mono text-[11px]">${mainId ? this._escape(mainId) : '<span class="text-gray-400">—</span>'}</td>
          <td class="text-[11px]">${this._escape(s.name || '')}</td>
          <td class="font-mono text-[10px] text-gray-500">${this._escape(s.id || '')}</td>
          <td>
            <button class="btn ${isPicked ? 'btn-muted' : 'btn-primary'}"
                    onclick="IdentifyTxn.pickSecForEdit(${this._jsAttr(s.id)}, ${this._jsAttr(s.name || '')}, ${this._jsAttr(mainId)})">
              ${isPicked ? 'Atual ✓' : 'Selecionar'}
            </button>
          </td>
        </tr>`;
    }).join('');
    const tail = matches.length > slice.length ? ` · mostrando ${slice.length}` : '';
    hint.textContent = `${matches.length} resultado(s) · ${universe.length} no cadastro${tail}`;
  },

  // Apply user's pick (from either the top-N or the search results) to the
  // suggestion store. The row re-render reads from `_suggestions` so the
  // text + edit button cell updates automatically. Setting `securityId=''`
  // clears the suggestion (when "Limpar" is clicked).
  pickSecForEdit(securityId, beehusName, mainId) {
    const txnId = this._secEditTxnId;
    if (!txnId) { this.closeSecEdit(); return; }
    const sug = this._suggestions[txnId] || {};
    const cleared = !securityId;
    sug.securityId        = securityId || '';
    sug.securityName      = beehusName || '';
    // The new pick is authoritative: take its mainId as-is and do NOT fall back
    // to the previous suggestion's mainId. The full-catalog search passes mainId=''
    // (the catalog list carries only id/name), so a stale fallback would weld the
    // OLD mainId onto the NEW name and render "OLD_mainId · NEW_name" in the cell.
    sug.securityMainId    = mainId || '';
    sug.securityAmbiguous = false;
    sug.securitySource    = 'user';
    // A user-confirmed pick is by definition trustworthy — surface it as 100%
    // so the badge and the security-confidence sort don't keep showing the
    // ML score for a security the operator already approved. Clearing the
    // pick (Limpar) drops the confidence back to null so the row re-enters
    // the "Sem sugestão" bucket.
    sug.securityConfidence = cleared ? null : 1.0;
    this._suggestions[txnId] = sug;
    this.closeSecEdit();
    this._render();
  },

  // ── Reinforcement (manual save) ───────────────────────────────────────
  // Operator clicks "★ Salvar como reforço" inside the security-edit modal.
  // We open a second modal layered above (z-index 60 vs 50) so the security
  // pick stays in view, populate it with the row's raw description plus the
  // chosen type/security, and let the operator confirm or cancel before
  // anything is written to disk. The PATCH route no longer auto-saves
  // reinforcements — this is the only path that creates them.
  openReinforce() {
    const txnId = this._secEditTxnId;
    if (!txnId) return;
    const txn = this._txns.find(t => t.id === txnId);
    const sug = this._suggestions[txnId] || {};
    if (!txn) return;
    const desc  = txn.description || '';
    if (!desc.trim()) {
      alert('Esta transação não tem descrição — não há chave para o reforço.');
      return;
    }
    const btt   = sug.beehusTransactionType || txn.type || '';
    const sid   = sug.securityId || txn.securityId || '';
    const sname = sug.securityName || txn.securityName || '';
    const smain = sug.securityMainId || '';
    if (!btt && !sid) {
      alert('Escolha um tipo ou uma security antes de salvar o reforço.');
      return;
    }
    // The original description is preserved separately so the "Original"
    // button can restore it after the operator edits/shortens the snippet.
    this._reinforceOriginalDesc = desc;
    this._reinforceContext = {
      beehusTransactionType: btt,
      securityId:            sid,
      securityName:          sname,
      securityMainId:        smain,
    };
    const ta = document.getElementById('i-reinforce-desc');
    if (ta) ta.value = desc;
    document.getElementById('i-reinforce-type').textContent = btt || '(nenhum)';
    const secLabel = sid
      ? (smain ? `${smain} · ${sname || sid}` : (sname || sid))
      : '(nenhuma)';
    document.getElementById('i-reinforce-sec').textContent = secLabel;
    // "Sem security" is mutually exclusive with a chosen security: disable +
    // uncheck it when a securityId is present so the operator can't save a
    // contradictory rule.
    const nosec = document.getElementById('i-reinforce-nosec');
    if (nosec) { nosec.checked = false; nosec.disabled = !!sid; }
    this._refreshReinforcePreview();
    document.getElementById('i-reinforce-modal').classList.add('show');
    setTimeout(() => { if (ta) ta.focus(); }, 50);
  },

  // Operator clicks "★ Salvar tipo como reforço" inside the type-edit modal.
  // Same flow/confirm modal as openReinforce(), but the reinforcement is
  // TYPE-ONLY: it records the chosen beehusTransactionType with no security,
  // so future identifies whose description contains this snippet inherit the
  // type. Reuses the shared #i-reinforce-modal and runSaveReinforcement().
  openReinforceType() {
    const txnId = this._typeEditTxnId;
    if (!txnId) return;
    const txn = this._txns.find(t => t.id === txnId);
    const sug = this._suggestions[txnId] || {};
    if (!txn) return;
    const desc = txn.description || '';
    if (!desc.trim()) {
      alert('Esta transação não tem descrição — não há chave para o reforço.');
      return;
    }
    const btt = sug.beehusTransactionType || txn.type || '';
    if (!btt) {
      alert('Escolha um tipo antes de salvar o reforço.');
      return;
    }
    this._reinforceOriginalDesc = desc;
    // Type-only: no security context (the save endpoint accepts a rule with
    // beehusTransactionType and an empty securityId).
    this._reinforceContext = {
      beehusTransactionType: btt,
      securityId:            '',
      securityName:          '',
      securityMainId:        '',
    };
    const ta = document.getElementById('i-reinforce-desc');
    if (ta) ta.value = desc;
    document.getElementById('i-reinforce-type').textContent = btt;
    document.getElementById('i-reinforce-sec').textContent = '(nenhuma — reforço só de tipo)';
    // Type-only rule: "Sem security" is available (no security chosen). Default
    // unchecked — the operator opts in to mark this as a no-security txn.
    const nosec = document.getElementById('i-reinforce-nosec');
    if (nosec) { nosec.checked = false; nosec.disabled = false; }
    this._refreshReinforcePreview();
    document.getElementById('i-reinforce-modal').classList.add('show');
    setTimeout(() => { if (ta) ta.focus(); }, 50);
  },

  // Restore the textarea to the unedited transaction description.
  resetReinforceDesc() {
    const ta = document.getElementById('i-reinforce-desc');
    if (ta && this._reinforceOriginalDesc != null) {
      ta.value = this._reinforceOriginalDesc;
    }
    this._refreshReinforcePreview();
  },

  // Live coverage preview so the operator sees what score this snippet
  // would give against the original description. Mirrors the server-side
  // calculation in `_lookup_reinforcement` (coverage clamped to 70-99%
  // for substring matches; 100% for exact equality after normalisation).
  _refreshReinforcePreview() {
    const ta = document.getElementById('i-reinforce-desc');
    const out = document.getElementById('i-reinforce-preview');
    if (!ta || !out) return;
    const snippet = (ta.value || '').trim();
    const orig    = (this._reinforceOriginalDesc || '').trim();
    if (!snippet) { out.textContent = '⚠ trecho vazio — não pode ser salvo'; return; }
    const norm = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '')
                        .toUpperCase().replace(/\s+/g, ' ').trim();
    const ns = norm(snippet);
    const no = norm(orig);
    if (!ns) { out.textContent = '⚠ trecho vazio após normalização'; return; }
    if (ns === no) {
      out.textContent = `Score na descrição original: 100% (match exato).`;
      return;
    }
    if (no.includes(ns)) {
      const cov = ns.length / Math.max(no.length, 1);
      const score = Math.max(0.70, Math.min(0.99, cov));
      out.textContent = `Score na descrição original: ${(score * 100).toFixed(0)}% ` +
                        `(substring; cobre ${(cov * 100).toFixed(0)}% do texto).`;
      return;
    }
    out.textContent = '⚠ o trecho não aparece na descrição original — verifique antes de salvar.';
  },

  closeReinforce() {
    document.getElementById('i-reinforce-modal').classList.remove('show');
    this._reinforceOriginalDesc = null;
    this._reinforceContext = null;
  },

  async runSaveReinforcement() {
    const ctx = this._reinforceContext;
    if (!ctx) { this.closeReinforce(); return; }
    const ta = document.getElementById('i-reinforce-desc');
    const desc = ta ? ta.value.trim() : '';
    if (!desc) {
      alert('O trecho não pode ficar vazio.');
      return;
    }
    const nosecEl = document.getElementById('i-reinforce-nosec');
    const noSecurity = !!(nosecEl && nosecEl.checked && !nosecEl.disabled) && !ctx.securityId;
    const payload = {
      description:           desc,
      beehusTransactionType: ctx.beehusTransactionType,
      securityId:            ctx.securityId,
      securityName:          ctx.securityName,
      securityMainId:        ctx.securityMainId,
      noSecurity:            noSecurity,
    };
    const btn = document.getElementById('i-reinforce-confirm-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Salvando…'; }
    let r;
    try {
      r = await api('POST', '/api/beehus/identify-transactions/reinforcement', payload);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Salvar reforço'; }
    }
    if (!r.ok) {
      alert('Falha ao salvar reforço. Veja o Log.');
      return;
    }
    this.closeReinforce();
    const what = ctx.securityId
      ? 'este tipo e security'
      : (noSecurity ? 'este tipo, sem security (não será buscada)' : 'este tipo');
    alert(`Reforço salvo. Em identifies futuros, qualquer descrição que contenha esse trecho receberá automaticamente ${what}.`);
  },

  // ── Temporary reinforcement (in-memory only) ──────────────────────────
  // Shares the substring + coverage-score logic with the persisted
  // reinforcement endpoint, but never writes to disk. The rule lives only
  // until the next search() (which clears _suggestions). Designed for
  // batches that look one-off — applying this lets the operator classify
  // dozens of similar rows without polluting the curated rules file.
  _tempNorm(s) {
    return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toUpperCase().replace(/\s+/g, ' ').trim();
  },

  // Score a substring match the same way the server does. Returns 0 when
  // the snippet doesn't appear in the description (no match).
  _tempScore(snippetNorm, descNorm) {
    if (!snippetNorm || !descNorm) return 0;
    if (snippetNorm === descNorm) return 1.0;
    if (descNorm.indexOf(snippetNorm) === -1) return 0;
    const cov = snippetNorm.length / Math.max(descNorm.length, 1);
    return Math.max(0.70, Math.min(0.99, cov));
  },

  openTempReinforce() {
    if (!this._txns || !this._txns.length) {
      alert('Faça uma busca primeiro — o reforço temporário aplica regras ao listing já carregado.');
      return;
    }
    // Reset state.
    this._tempSec = null;            // {securityId, beehusName, mainId}
    document.getElementById('i-tr-snippet').value = '';
    document.getElementById('i-tr-apply-type').checked = true;
    document.getElementById('i-tr-apply-sec').checked  = false;
    // Populate type select with the full known list, defaulting to the first.
    const typeSel = document.getElementById('i-tr-type');
    typeSel.innerHTML = (this._allTypes || []).map(t =>
      `<option value="${this._escape(t)}">${this._escape(t)}</option>`
    ).join('');
    document.getElementById('i-tr-sec-search').value = '';
    document.getElementById('i-tr-sec-current').textContent = '(nenhuma)';
    this._refreshTempReinforcePanes();
    this._refreshTempReinforceMatchCount();
    this._renderTempReinforceSecResults();
    document.getElementById('i-temp-reinforce-modal').classList.add('show');
    setTimeout(() => document.getElementById('i-tr-snippet').focus(), 50);
  },

  closeTempReinforce() {
    document.getElementById('i-temp-reinforce-modal').classList.remove('show');
    this._tempSec = null;
  },

  // Show/hide the type and security panes based on the checkboxes.
  _refreshTempReinforcePanes() {
    const tp = document.getElementById('i-tr-apply-type').checked;
    const sp = document.getElementById('i-tr-apply-sec').checked;
    document.getElementById('i-tr-type-pane').classList.toggle('hidden', !tp);
    document.getElementById('i-tr-sec-pane').classList.toggle('hidden',  !sp);
  },

  // Live count of how many of `_txns` would be touched by the snippet.
  _refreshTempReinforceMatchCount() {
    const snippet = document.getElementById('i-tr-snippet').value;
    const ns = this._tempNorm(snippet);
    const hint = document.getElementById('i-tr-match-hint');
    if (!ns) { hint.textContent = 'Digite o trecho.'; return; }
    let exact = 0, substr = 0;
    for (const t of this._txns) {
      const s = this._tempScore(ns, this._tempNorm(t.description || ''));
      if (s === 1.0) exact++;
      else if (s > 0) substr++;
    }
    hint.textContent = `${exact + substr} linha(s) bateriam · ${exact} match exato · ${substr} substring`;
  },

  _tempReinforceClearSec() {
    this._tempSec = null;
    document.getElementById('i-tr-sec-current').textContent = '(nenhuma)';
    this._renderTempReinforceSecResults();
  },

  _pickSecForTempReinforce(securityId, beehusName, mainId) {
    this._tempSec = {securityId, beehusName, mainId};
    document.getElementById('i-tr-sec-current').textContent =
      mainId ? `${mainId} · ${beehusName || securityId}` : (beehusName || securityId);
    this._renderTempReinforceSecResults();
  },

  // Search the company-level securities list (no wallet scoping — the rule
  // applies across rows from many wallets, so a per-wallet filter would be
  // wrong). Mirrors the security-edit modal's table layout for familiarity.
  _renderTempReinforceSecResults() {
    const tbody = document.getElementById('i-tr-sec-results');
    const hint  = document.getElementById('i-tr-sec-hint');
    if (!tbody || !hint) return;
    const q = (document.getElementById('i-tr-sec-search').value || '').trim().toLowerCase();
    if (q.length < 2) {
      tbody.innerHTML = '';
      hint.textContent = 'Digite ao menos 2 caracteres';
      return;
    }
    const universe = this._securities || [];
    const matches = universe.filter(s => {
      const id = (s.id || '').toLowerCase();
      const nm = (s.name || '').toLowerCase();
      return id.includes(q) || nm.includes(q);
    });
    const slice = matches.slice(0, 100);
    const pickedId = (this._tempSec && this._tempSec.securityId) || '';
    tbody.innerHTML = slice.map(s => {
      const isPicked = s.id === pickedId;
      return `
        <tr class="${isPicked ? 'bg-amber-50 dark:bg-amber-900/30' : ''}">
          <td class="font-mono text-[11px]">${this._escape(s.id || '')}</td>
          <td class="text-[11px]">${this._escape(s.name || '')}</td>
          <td>
            <button class="btn ${isPicked ? 'btn-muted' : 'btn-primary'}"
                    onclick="IdentifyTxn._pickSecForTempReinforce(${this._jsAttr(s.id)}, ${this._jsAttr(s.name || '')}, '')">
              ${isPicked ? 'Atual ✓' : 'Selecionar'}
            </button>
          </td>
        </tr>`;
    }).join('');
    const tail = matches.length > slice.length ? ` · mostrando ${slice.length}` : '';
    hint.textContent = `${matches.length} resultado(s)${tail}`;
  },

  // Walk the loaded listing and patch _suggestions for every txn whose
  // description contains the snippet. Score follows the same coverage
  // formula as the persisted reinforcement (exact = 1.0; substring clamps
  // to 0.70-0.99). Source = 'temp_reinforcement' / 'temp_reinforcement_partial'
  // so the badges visually distinguish from real reinforcements.
  applyTempReinforcement() {
    const snippet = document.getElementById('i-tr-snippet').value;
    const ns = this._tempNorm(snippet);
    if (!ns) { alert('Digite o trecho a procurar.'); return; }
    const applyType = document.getElementById('i-tr-apply-type').checked;
    const applySec  = document.getElementById('i-tr-apply-sec').checked;
    if (!applyType && !applySec) {
      alert('Selecione ao menos um campo a aplicar (Tipo e/ou Security).');
      return;
    }
    const newType = applyType ? document.getElementById('i-tr-type').value : '';
    if (applyType && !newType) {
      alert('Escolha o tipo desejado.');
      return;
    }
    if (applySec && !(this._tempSec && this._tempSec.securityId)) {
      alert('Escolha a security desejada.');
      return;
    }

    let touched = 0, exact = 0, partial = 0;
    for (const t of this._txns) {
      const score = this._tempScore(ns, this._tempNorm(t.description || ''));
      if (score === 0) continue;
      const sug = this._suggestions[t.id] || {};
      const isExact = score === 1.0;
      const effectiveType = applyType ? newType : (sug.beehusTransactionType || t.type || '');
      sug.beehusTransactionType = applyType ? newType : (sug.beehusTransactionType || '');
      // Refresh needsSecurity from the (possibly new) effective type.
      sug.needsSecurity = this._typesNeedingSecurity.has(effectiveType);
      if (applyType) {
        sug.confidence = score;
        sug.source     = isExact ? 'temp_reinforcement' : 'temp_reinforcement_partial';
        sug.needsReview = !isExact && score < 0.85;
      }
      if (applySec && sug.needsSecurity) {
        sug.securityId         = this._tempSec.securityId;
        sug.securityName       = this._tempSec.beehusName || '';
        sug.securityMainId     = this._tempSec.mainId || '';
        sug.securityConfidence = score;
        sug.securitySource     = isExact ? 'temp_reinforcement' : 'temp_reinforcement_partial';
        sug.securityAmbiguous  = false;
        // Clear classifier alternatives so the modal's top-N table shows
        // the operator's pick alone (no stale ranked alternatives).
        sug.securityAlternatives = [];
      }
      this._suggestions[t.id] = sug;
      touched++;
      if (isExact) exact++; else partial++;
    }
    this.closeTempReinforce();
    this._render();
    alert(`Reforço temporário aplicado em ${touched} linha(s) (${exact} exato + ${partial} substring). Revise e use Implementar para gravar.`);
  },

  // Apply current filter+sort selections. Re-renders the table.
  applyConfControls() { this._render(); },

  // Description filter — case-insensitive substring match. The typed text
  // must appear exactly as a substring of the description (no accent
  // stripping or fuzzy matching); applied inside `_visibleTxns()` so it
  // composes naturally with the confidence filter/sort.
  applyDescFilter(value) {
    this._descFilter = (value || '').trim();
    this._render();
  },

  clearDescFilter() {
    const el = document.getElementById('i-desc-filter');
    if (el) el.value = '';
    this._descFilter = '';
    this._render();
  },

  applyTypeFilter(value) {
    this._typeFilter = value || '';
    this._render();
  },

  applySourceFilter(value) {
    this._sourceFilter = value || '';
    this._render();
  },

  // Canonical category of a suggestion's SECURITY source, used by the Fonte
  // filter (and matching the Fonte column's buckets): 'reforco' | 'level1' |
  // 'level2' | 'level3' | 'tiebreak' | 'manual' | 'none'.
  _sourceCategory(sug) {
    if (!sug || !sug.needsSecurity) return 'none';
    switch (sug.securitySource || '') {
      case 'reinforcement':
      case 'reinforcement_partial':
      case 'temp_reinforcement':
      case 'temp_reinforcement_partial': return 'reforco';
      case 'level1':                     return 'level1';
      case 'level2':                     return 'level2';
      case 'level3':                     return 'level3';
      case 'amount_diff_tiebreaker':     return 'tiebreak';
      case 'user':                       return 'manual';
      default:                           return 'none';
    }
  },

  // Effective type for the tipo filter: prefer the classifier's suggested
  // `beehusTransactionType` (the value that the operator just ran
  // **Identificar selecionadas** to obtain), fall back to the row's
  // current DB `t.type` for rows that didn't get a suggestion. This way
  // the dropdown is meaningful BOTH before and after Identificar:
  //   • "Não identificadas" + no suggestions yet → all rows roll into
  //     `__EMPTY__` and the dropdown stays a no-op.
  //   • After Identificar → the buckets reflect the suggested types and
  //     the operator can scope the listing to e.g. all "dividend"
  //     suggestions for a focused review.
  //   • "Identificadas" search (rows with `t.type` filled, no suggestion
  //     run) → the fallback to `t.type` keeps the dropdown useful.
  _effectiveTypeFor(t) {
    const sug = this._suggestions ? this._suggestions[t.id] : null;
    const sugType = sug && sug.beehusTransactionType ? sug.beehusTransactionType : '';
    return (sugType || t.type || '').trim();
  },

  // Rebuild the `#i-type-filter` <select> options from the distinct
  // effective types in the loaded result set (see `_effectiveTypeFor`).
  // Counts are added in parentheses so the operator can see how many
  // rows each option matches before picking it. The current
  // `_typeFilter` selection is preserved when still applicable;
  // otherwise it falls back to "Todos".
  _refreshTypeFilterOptions() {
    const sel = document.getElementById('i-type-filter');
    if (!sel) return;
    const counts = new Map();
    for (const t of this._txns) {
      const key = this._effectiveTypeFor(t) || '__EMPTY__';
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const entries = Array.from(counts.entries()).sort((a, b) => {
      // Empty bucket sorts last; the rest alphabetically.
      if (a[0] === '__EMPTY__') return 1;
      if (b[0] === '__EMPTY__') return -1;
      return a[0].localeCompare(b[0], 'pt-BR');
    });
    const opts = [`<option value="">Todos (${this._txns.length})</option>`];
    for (const [key, n] of entries) {
      const label = key === '__EMPTY__' ? '(sem tipo)' : key;
      const value = key === '__EMPTY__' ? '__EMPTY__' : key;
      opts.push(`<option value="${this._escape(value)}">${this._escape(label)} (${n})</option>`);
    }
    sel.innerHTML = opts.join('');
    // Restore the previous selection if it's still represented in the
    // new result set; otherwise reset to "Todos".
    const stillValid = this._typeFilter === '' || counts.has(
      this._typeFilter === '__EMPTY__' ? '__EMPTY__' : this._typeFilter
    );
    if (!stillValid) this._typeFilter = '';
    sel.value = this._typeFilter || '';
  },

  // Row selection on the Identificar Transações table. Handled at the `<tr>`
  // level (the checkbox click bubbles up here too), so clicking anywhere on a
  // row toggles its `.i-row` checkbox — except on the row's own controls
  // (Editar buttons, the clickable confidence badges, links/selects), which
  // keep their behaviour. Modifier semantics mirror Excel / file managers:
  //   • plain click  → toggle this row and re-arm the anchor.
  //   • shift+click  → fill the contiguous span between the anchor and this
  //                    row with this row's NEW state; the anchor follows the
  //                    latest endpoint.
  //   • ctrl/cmd+click → toggle ONLY this row (avulso) and deliberately leave
  //                    the anchor untouched, so a later shift+click still
  //                    ranges from the original anchor.
  // Indices reference the current rendered ordering, so the anchor is reset on
  // every `_render()` to avoid stale offsets after a filter/sort repaint.
  onRowClick(event) {
    const t = event.target;
    // Let the row's interactive controls handle their own clicks.
    if (t.closest('button, a, select, textarea, .conf-badge')) return;
    // Inline-edit inputs (Preço exec./IRRF and the generic edit-field date/text
    // inputs) are <input> elements that aren't the row checkbox — clicking into
    // one to type must not toggle row selection.
    if (t.tagName === 'INPUT' && !t.classList.contains('i-row')) return;

    const cb = event.currentTarget.querySelector('input.i-row');
    if (!cb) return;
    const rows = Array.from(document.querySelectorAll('.i-row'));
    const idx  = rows.indexOf(cb);
    if (idx < 0) return;

    // A direct checkbox click was already toggled by the browser; a click on
    // the row body was not, so toggle it ourselves.
    const clickedCheckbox = (t === cb);
    if (!clickedCheckbox) cb.checked = !cb.checked;

    if (event.shiftKey && this._lastClickedRowIdx != null
        && this._lastClickedRowIdx !== idx) {
      // Propagate this row's state across the span. Rows whose `.checked` we
      // mutate directly don't fire their own `change`, so updateSelected()
      // runs once at the end.
      const state = cb.checked;
      const [start, end] = this._lastClickedRowIdx < idx
        ? [this._lastClickedRowIdx, idx]
        : [idx, this._lastClickedRowIdx];
      for (let i = start; i <= end; i++) {
        if (rows[i].checked !== state) rows[i].checked = state;
      }
      this._lastClickedRowIdx = idx;        // anchor follows the latest endpoint
    } else if (event.ctrlKey || event.metaKey) {
      // Avulso: this row toggled in isolation; keep the shift anchor intact.
    } else {
      this._lastClickedRowIdx = idx;        // plain click re-arms the anchor
    }
    this._syncVisibleToSel();   // mirror the DOM checkbox changes into _selSet
    this.updateSelected();
  },

  // Returns the visible (filtered + sorted) subset of `this._txns`.
  // Filter modes: 'all' | 'high' | 'mid' | 'review' | 'none' (operates on type confidence)
  // Sort modes  : 'default' | 'conf-asc' | 'conf-desc' | 'sec-asc' | 'sec-desc'
  // Plus a free-text description filter (`_descFilter`) applied first.
  _visibleTxns() {
    const filterMode = (document.getElementById('i-conf-filter') || {}).value || 'all';
    const sortMode   = (document.getElementById('i-conf-sort')   || {}).value || 'default';
    const conf = (id) => {
      const s = this._suggestions[id];
      return (s && typeof s.confidence === 'number') ? s.confidence : null;
    };
    // Security confidence is only meaningful for rows that need a security.
    // Rows that don't need one are sorted to the end (asc) or front (desc) by
    // returning null, then we use a sentinel that pushes them out of the way.
    const secConf = (id) => {
      const s = this._suggestions[id];
      if (!s) return null;
      const needs = !!s.needsSecurity;
      if (!needs) return null;
      return (typeof s.securityConfidence === 'number') ? s.securityConfidence : null;
    };
    let rows = this._txns.slice();
    const descQ = (this._descFilter || '').toLowerCase();
    if (descQ) rows = rows.filter(t => (t.description || '').toLowerCase().includes(descQ));
    const typeQ = this._typeFilter || '';
    if (typeQ === '__EMPTY__') {
      rows = rows.filter(t => !this._effectiveTypeFor(t));
    } else if (typeQ) {
      rows = rows.filter(t => this._effectiveTypeFor(t) === typeQ);
    }
    const srcQ = this._sourceFilter || '';
    if (srcQ) rows = rows.filter(t => this._sourceCategory(this._suggestions[t.id]) === srcQ);
    if (filterMode === 'high')   rows = rows.filter(t => { const c = conf(t.id); return c != null && c >= 0.95; });
    if (filterMode === 'mid')    rows = rows.filter(t => { const c = conf(t.id); return c != null && c >= 0.70 && c < 0.95; });
    if (filterMode === 'review') rows = rows.filter(t => { const c = conf(t.id); return c != null && c < 0.70; });
    if (filterMode === 'none')   rows = rows.filter(t => conf(t.id) == null);
    if (sortMode === 'conf-asc')  rows.sort((a, b) => (conf(a.id) ?? -1) - (conf(b.id) ?? -1));
    if (sortMode === 'conf-desc') rows.sort((a, b) => (conf(b.id) ?? -1) - (conf(a.id) ?? -1));
    // For security sorts, push rows with no security confidence to the bottom
    // in both directions so the user sees actionable rows first.
    if (sortMode === 'sec-asc')   rows.sort((a, b) => (secConf(a.id) ?? Infinity)  - (secConf(b.id) ?? Infinity));
    if (sortMode === 'sec-desc')  rows.sort((a, b) => (secConf(b.id) ?? -Infinity) - (secConf(a.id) ?? -Infinity));
    return rows;
  },

  // Single-source-of-truth render. The truncation indicator reflects the
  // last search response (`_truncated`); callers no longer pass it as a
  // per-call argument, so re-renders triggered by filter/sort/edit don't
  // accidentally wipe a "(truncado em 1000)" warning that's still relevant.
  _render() {
    document.getElementById('i-result').classList.remove('hidden');
    document.getElementById('i-result-count').textContent = this._txns.length;
    document.getElementById('i-result-truncated').classList.toggle('hidden', !this._truncated);

    // Repainting the tbody throws away the previous DOM nodes, so any
    // index-based anchor for shift+click range selection no longer maps
    // to a real row. Clear it here; the next plain click on a checkbox
    // re-arms the anchor.
    this._lastClickedRowIdx = null;

    // Refresh the tipo filter options FIRST so the dropdown reflects the
    // current `_txns` set; this may also reset `_typeFilter` to '' if the
    // previously selected value disappeared, which we then honour in
    // `_visibleTxns()`.
    this._refreshTypeFilterOptions();

    const visible = this._visibleTxns();
    const shownEl = document.getElementById('i-result-shown');
    if (shownEl) {
      shownEl.textContent = (visible.length !== this._txns.length)
        ? `· mostrando ${visible.length} de ${this._txns.length}`
        : '';
    }

    const fmt = (n) => n == null ? '' : Number(n).toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const tbody = document.getElementById('i-rows');
    // Selection model: `_selSet` (txn ids) is the persistent, view-independent
    // source of truth. A checkbox is checked iff its id ∈ _selSet. A fresh
    // search() selects all; filters/sort/sugestão/inline only change the VIEW, so
    // the operator's ticks survive a filter narrow→widen (no silent loss) and a
    // filter never re-selects rows (the mass-delete footgun). Actions act on
    // VISIBLE ∩ selected (see _selectedIds), so a filtered op never touches
    // hidden rows.
    const keep = (id) => this._selSet.has(id);
    // Inline edit: the active field's cells become inputs ONLY on rows that will
    // actually be patched (checked) — so "célula editável" == "linha no PATCH".
    // Capped: rendering thousands of (up to 2000-option) selects would hang the
    // browser, so above the cap we fall back to the value-mapping modal.
    const keptCount = visible.reduce((n, t) => n + (keep(t.id) ? 1 : 0), 0);
    const inlineOn = !!this._inlineEditField && keptCount <= this.INLINE_EDIT_MAX_ROWS;
    const vazio = '<span class="text-gray-400 italic">(vazio)</span>';
    // For the 5 editable fields: inline input (when inlineOn + row checked) →
    // per-row override preview ("atual → novo ✎", or a "mantido" marker for a
    // keep-sentinel that cancels a bucket edit) → plain label.
    const renderCell = (t, fieldKey, currentLabel) => {
      if (inlineOn && this._inlineEditField === fieldKey && keep(t.id)) {
        return this._inlineEditControl(t, fieldKey);
      }
      const ov = this._tempEdits[t.id];
      if (ov && (fieldKey in ov)) {
        const raw = this._txnFieldValue(t, fieldKey);
        if (ov[fieldKey] === raw) {
          return `${currentLabel === '' ? vazio : this._escape(currentLabel)}
                  <span class="text-gray-500 ml-0.5" title="Mantido — não será alterado, mesmo com mapeamento por valor">⊘</span>`;
        }
        const newLbl = this._labelForValue(fieldKey, ov[fieldKey]);
        return `<span class="text-gray-400 line-through">${currentLabel === '' ? '∅' : this._escape(currentLabel)}</span>
                <span class="text-amber-700"> → ${this._escape(newLbl)}</span>
                <span class="text-amber-600 ml-0.5" title="Edição por linha">✎</span>`;
      }
      return currentLabel === '' ? vazio : this._escape(currentLabel);
    };
    tbody.innerHTML = visible.map(t => {
      const sug = this._suggestions[t.id] || {beehusTransactionType: '', securityId: '', needsSecurity: this._typesNeedingSecurity.has(t.type), confidence: null, source: '', needsReview: false};
      const securityCellEnabled = sug.needsSecurity || this._typesNeedingSecurity.has(t.type);
      const clsList = [];
      if (sug.needsReview) clsList.push('conf-review-row');
      if (this._tempEdits[t.id]) clsList.push('bg-amber-50');
      const rowClass = clsList.length ? ` class="${clsList.join(' ')}"` : '';
      return `
      <tr data-id="${t.id}" style="cursor:pointer"${rowClass} onclick="IdentifyTxn.onRowClick(event)">
        <td><input type="checkbox" class="i-row" data-id="${t.id}" ${keep(t.id) ? 'checked' : ''}
                   onchange="IdentifyTxn.onRowCheck(this)"></td>
        <td>${renderCell(t, 'liquidationDate', t.liquidationDate || '')}</td>
        <td>${this._escape(t.walletName)}</td>
        <td>${renderCell(t, 'entityId', t.entityName || t.entityId || '')}</td>
        <td>${renderCell(t, 'beehusTransactionType', t.type || '')}</td>
        <td>
          ${this._typeSuggestedCell(t, sug)}
        </td>
        <td class="text-center">${this._confidenceBadge(sug)}</td>
        <td>${renderCell(t, 'securityId', t.securityName || t.securityId || '')}</td>
        <td>
          ${this._securitySuggestedCell(t, sug, securityCellEnabled)}
        </td>
        <td class="text-center">${this._securityConfidenceCell(t.id, sug)}</td>
        <td class="text-center">${this._securitySourceCell(sug)}</td>
        <td class="text-right">${fmt(t.balance)}</td>
        <td class="text-right">${fmt(t.quantity)}</td>
        <td class="text-right">${this._execCell(t, sug)}</td>
        <td class="text-right">${this._irrfCell(t, sug)}</td>
        <td>${renderCell(t, 'description', t.description || '')}</td>
      </tr>`;
    }).join('');
    this.updateSelected();   // sets counters + i-select-all from the fresh DOM
    // Keep the edit configurator consistent with the (possibly new) result set.
    this._pruneEdits();
    this._renderEditChips();
    if (this._inlineEditField && !inlineOn) {
      const s = document.getElementById('i-edit-summary');
      if (s) s.textContent = (s.textContent ? s.textContent + ' · ' : '') +
        `edição inline oculta (>${this.INLINE_EDIT_MAX_ROWS} linhas marcadas) — use o mapeamento por valor`;
    }
  },

  // ── Preço exec. / IRRF columns ────────────────────────────────────────────
  // Effective value for a derived column: the operator override (when the row
  // has an entry in _execEdits for this kind) else the server value. Returns
  // '' when the operator cleared it, null when nothing applies.
  _extraVal(t, sug, kind) {
    const e = this._execEdits[t.id];
    if (e && Object.prototype.hasOwnProperty.call(e, kind)) return e[kind];
    return (sug && sug[kind] != null) ? sug[kind] : null;
  },

  _execColorCls(val, pu) {
    const v = Number(val), p = Number(pu);
    if (val == null || val === '' || isNaN(v) || !pu || isNaN(p) || p <= 0) return '';
    const diff = Math.abs(v - p) / Math.abs(p);
    if (diff <= 0.20) return 'badge-ok';
    if (diff <= 0.40) return 'badge-amber';
    return 'badge-err';
  },

  _irrfColorCls(val, bal) {
    const v = Number(val), b = Number(bal);
    if (val == null || val === '' || isNaN(v) || !bal || isNaN(b) || b === 0) return '';
    const ratio = Math.abs(v) / Math.abs(b);
    if (ratio <= 0.15) return 'badge-ok';
    if (ratio <= 0.22) return 'badge-amber';
    return 'badge-err';
  },

  // pt-BR aware parse: "1.234,56" → 1234.56; "12.5" → 12.5 (dot is decimal when
  // no comma is present). Returns null for empty / non-numeric.
  _parseNum(s) {
    if (s == null) return null;
    let str = String(s).trim().replace(/\s/g, '');
    if (!str) return null;
    if (str.indexOf(',') >= 0) str = str.replace(/\./g, '').replace(',', '.');
    const n = Number(str);
    return isNaN(n) ? null : n;
  },

  // Comma-decimal, no thousands separators, trailing zeros trimmed — clean to
  // edit inline.
  _fmtEditNum(val, dec) {
    if (val == null || val === '') return '';
    const n = Number(val);
    if (isNaN(n)) return '';
    return n.toFixed(dec).replace('.', ',').replace(/,?0+$/, '');
  },

  _extraTip(t, sug, kind) {
    const f = (n) => n == null ? '—' : Number(n).toLocaleString('pt-BR', {maximumFractionDigits: 6});
    const etype = (sug && sug.beehusTransactionType) || t.type || '∅';
    const gate = (sug && sug.withinGate)
      ? 'dentro da janela (< 3 dias úteis)'
      : 'fora da janela (≥ 3 dias úteis)';
    const dates = `operação ${(sug && sug.formerDate) || t.operationDate || '?'} → liquidação ${t.liquidationDate || '?'}`;
    const hasEdit = (k) => this._execEdits[t.id] && (k in this._execEdits[t.id]);
    if (kind === 'executionPrice') {
      if (!(sug && sug.executionPrice != null) && !hasEdit('executionPrice')) {
        if (!['buySell', 'maturity'].includes(etype)) return `Preço de execução só para buySell/maturity (tipo: ${etype})`;
        if ((sug && sug.securityType) === 'brazilianFund') return 'Preço de execução não se aplica a brazilianFund (apenas IRRF)';
        if (!(sug && sug.securityId)) return 'Sem security identificada — preço de execução indisponível';
        return `Preço de execução indisponível — ${gate}.\n${dates}`;
      }
      return [
        'Preço de execução = -balance / amountDifference',
        `balance: ${f(t.balance)} · amountDifference (qtd): ${f(sug && sug.amountDifference)} · PU: ${f(sug && sug.pu)}`,
        `tipo: ${etype} · ${gate}`,
        dates,
        'Cor: |exec−PU|/PU ≤20% verde · ≤40% amarelo · >40% vermelho',
      ].join('\n');
    }
    // IRRF
    if (!(sug && sug.irrf != null) && !hasEdit('irrf')) {
      if (etype !== 'buySell') return `IRRF só para buySell de brazilianFund (tipo: ${etype})`;
      if ((sug && sug.securityType) !== 'brazilianFund') return `IRRF só para brazilianFund (tipo do ativo: ${(sug && sug.securityType) || '?'})`;
      if (!(t.balance > 0)) return 'IRRF só quando balance > 0 (venda/resgate de fundo)';
      return `IRRF indisponível — ${gate}.\n${dates}`;
    }
    return [
      'IRRF = balance + amountDifference × PU',
      `balance: ${f(t.balance)} · amountDifference (qtd): ${f(sug && sug.amountDifference)} · PU: ${f(sug && sug.pu)}`,
      `${dates} · ${gate}`,
      'Gera transação "taxes" ao Implementar. Cor: |IRRF|/|balance| ≤15% verde · ≤22% amarelo · >22% vermelho',
    ].join('\n');
  },

  _execCell(t, sug) {
    const has = (sug && sug.executionPrice != null)
      || (this._execEdits[t.id] && 'executionPrice' in this._execEdits[t.id]);
    const tip = this._escape(this._extraTip(t, sug, 'executionPrice'));
    if (!has) return `<span class="text-gray-300" title="${tip}">—</span>`;
    const val = this._extraVal(t, sug, 'executionPrice');
    const cls = this._execColorCls(val, sug && sug.pu);
    return `<input type="text" inputmode="decimal" class="exec-inp ${cls}"
              data-id="${t.id}" data-kind="executionPrice" value="${this._fmtEditNum(val, 6)}"
              title="${tip}" oninput="IdentifyTxn.onExtraEdit(event)"
              onclick="event.stopPropagation()">`;
  },

  _irrfCell(t, sug) {
    const has = (sug && sug.irrf != null)
      || (this._execEdits[t.id] && 'irrf' in this._execEdits[t.id]);
    const tip = this._escape(this._extraTip(t, sug, 'irrf'));
    if (!has) return `<span class="text-gray-300" title="${tip}">—</span>`;
    const val = this._extraVal(t, sug, 'irrf');
    const cls = this._irrfColorCls(val, t.balance);
    return `<input type="text" inputmode="decimal" class="exec-inp ${cls}"
              data-id="${t.id}" data-kind="irrf" value="${this._fmtEditNum(val, 2)}"
              title="${tip}" oninput="IdentifyTxn.onExtraEdit(event)"
              onclick="event.stopPropagation()">`;
  },

  // Operator edits a derived value: store the override (parsed number, or '' if
  // cleared) and re-grade the cell colour live, without a full re-render.
  onExtraEdit(event) {
    const el = event.target;
    const id = el.getAttribute('data-id');
    const kind = el.getAttribute('data-kind');
    if (!id || !kind) return;
    const parsed = this._parseNum(el.value);
    const edit = this._execEdits[id] || (this._execEdits[id] = {});
    edit[kind] = (el.value.trim() === '' || parsed == null) ? '' : parsed;
    const sug = this._suggestions[id] || {};
    const t = this._txns.find(x => x.id === id) || {};
    const cls = (kind === 'executionPrice')
      ? this._execColorCls(edit[kind], sug.pu)
      : this._irrfColorCls(edit[kind], t.balance);
    el.className = 'exec-inp ' + cls;
  },

  // The "Tipo sugerido" select drives whether the security cell is editable —
  // change the type and the security select toggles disabled state in
  // accordance with the saved `typesNeedingSecurity` config.
  // ── Type edit modal (replaces the old <select>) ───────────────────────
  // Opens from the "Editar" button on the Tipo sugerido cell. Lists every
  // beehusTransactionType from `_allTypes` as a clickable button; the
  // current pick is highlighted. Picking one writes to `_suggestions[txnId]`
  // and re-renders the row (which also updates whether the security
  // Editar button is enabled, since types_need_security depends on type).
  openTypeEdit(txnId) {
    if (!this._suggestions[txnId]) {
      this._suggestions[txnId] = {
        beehusTransactionType: '', securityId: '', needsSecurity: false,
        confidence: null, source: '', needsReview: false,
        securityName: '', securityMainId: '',
        securityConfidence: null, securitySource: '',
        securityAmbiguous: false, securityAlternatives: [],
      };
    }
    this._typeEditTxnId = txnId;
    this._renderTypeEditModal();
    document.getElementById('i-type-edit-modal').classList.add('show');
  },
  closeTypeEdit() {
    document.getElementById('i-type-edit-modal').classList.remove('show');
    this._typeEditTxnId = null;
  },
  _renderTypeEditModal() {
    const txnId = this._typeEditTxnId;
    const sug = this._suggestions[txnId] || {};
    const txn = this._txns.find(t => t.id === txnId);
    const headerLabel = txn
      ? `${this._escape(txn.liquidationDate)} · ${this._escape(txn.walletName)} · ${this._escape((txn.description || '').slice(0, 140))}`
      : '';
    const currentType = sug.beehusTransactionType || '';
    const currentLabel = currentType ? `<strong>${this._escape(currentType)}</strong>` : '<em>(vazio)</em>';
    const sourceLabel = sug.source ? ` · origem: <code>${this._escape(sug.source)}</code>` : '';
    const confLabel = (sug.confidence != null)
      ? ` · confiança ${(sug.confidence * 100).toFixed(0)}%` : '';

    const btns = (this._allTypes || []).map(t => {
      const isPicked = (t === currentType);
      return `
        <button class="btn ${isPicked ? 'btn-success' : 'btn-muted'}"
                style="padding:6px 10px;font-size:11px;text-align:left;justify-content:flex-start"
                onclick="IdentifyTxn.pickTypeForEdit(${this._jsAttr(t)})">
          ${isPicked ? '✓ ' : ''}${this._escape(t)}
        </button>`;
    }).join('');

    document.getElementById('i-type-edit-body').innerHTML = `
      <div class="text-[11px] text-gray-600 mb-3 flex items-start justify-between gap-2">
        <div class="flex-1">
          <div>Transação: <span class="font-mono">${headerLabel}</span></div>
          <div>Tipo atual da DB: ${this._escape(txn ? (txn.type || '∅') : '∅')}</div>
          <div>Sugestão: ${currentLabel}${sourceLabel}${confLabel}</div>
        </div>
        <button onclick="IdentifyTxn.openReinforceType()" class="btn btn-muted whitespace-nowrap"
                title="Salvar a descrição desta transação como reforço de TIPO — em identifies futuros, descrições parecidas recebem este beehusTransactionType automaticamente">
          ★ Salvar tipo como reforço
        </button>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-3 gap-1 mb-3">
        ${btns}
      </div>
      <div class="flex gap-2">
        <button class="btn btn-muted" onclick="IdentifyTxn.pickTypeForEdit('')">
          Limpar (manter tipo da DB)
        </button>
      </div>`;
  },
  pickTypeForEdit(newType) {
    const txnId = this._typeEditTxnId;
    if (!txnId) { this.closeTypeEdit(); return; }
    const sug = this._suggestions[txnId] || {};
    sug.beehusTransactionType = newType || '';
    sug.source       = 'user';
    sug.needsReview  = false;
    // A user-confirmed type pick is treated as 100%; "Limpar" returns the row
    // to "Sem sugestão" so confidence-based filters/sort behave intuitively.
    sug.confidence   = newType ? 1.0 : null;
    // Re-evaluate whether the new type requires a security; clear orphan
    // security suggestion if not.
    if (newType) {
      sug.needsSecurity = this._typesNeedingSecurity.has(newType);
      if (!sug.needsSecurity) {
        sug.securityId = '';
        sug.securityName = '';
        sug.securityMainId = '';
        sug.securityConfidence = null;
      }
    } else {
      // No suggested type — fall back to whether the row's current DB type
      // needs a security.
      const txn = this._txns.find(t => t.id === txnId);
      sug.needsSecurity = !!(txn && this._typesNeedingSecurity.has(txn.type));
    }
    this._suggestions[txnId] = sug;
    this.closeTypeEdit();
    this._render();
  },

  toggleAll(checked) {
    // "Marcar/Desmarcar todas" acts on the VISIBLE rows (what the operator
    // sees). Hidden-by-filter selections in _selSet are left untouched, so a
    // filtered "desmarcar todas" doesn't silently wipe a selection made under a
    // different filter; they reappear ticked when the filter widens.
    document.querySelectorAll('.i-row').forEach(cb => cb.checked = checked);
    this._syncVisibleToSel();
    // With an inline-edit column active, the inline inputs track the selected
    // rows — re-render so they follow the bulk toggle (safe: button-driven, no
    // input focus to steal). Otherwise just refresh counters + master checkbox.
    if (this._inlineEditField) this._render();
    else this.updateSelected();
  },

  // Reconcile _selSet with the CURRENTLY-RENDERED checkboxes. Only visible rows
  // are in the DOM, so hidden-by-filter selections are preserved untouched.
  _syncVisibleToSel() {
    for (const cb of document.querySelectorAll('.i-row')) {
      if (cb.checked) this._selSet.add(cb.dataset.id);
      else this._selSet.delete(cb.dataset.id);
    }
  },

  // Per-row checkbox onchange.
  onRowCheck(cb) {
    if (cb.checked) this._selSet.add(cb.dataset.id);
    else this._selSet.delete(cb.dataset.id);
    this.updateSelected();
  },

  updateSelected() {
    // Counters reflect VISIBLE ∩ selected — i.e. exactly what the action buttons
    // will touch (operations are scoped to the current view via _selectedIds).
    const n = this._selectedIds().length;
    document.getElementById('i-identify-count').textContent = n;
    document.getElementById('i-apply-count').textContent    = n;
    const editEl = document.getElementById('i-edit-selected-count');
    if (editEl) editEl.textContent = n;
    const delEl = document.getElementById('i-delete-selected-count');
    if (delEl) delEl.textContent = n;
    const boxes = Array.from(document.querySelectorAll('.i-row'));
    const sa = document.getElementById('i-select-all');
    if (sa) sa.checked = boxes.length > 0 && boxes.every(cb => cb.checked);
  },

  _selectedIds() {
    // VISIBLE ∩ selected: operations act only on rows that are BOTH selected and
    // currently shown. Selection (`_selSet`) persists across filter toggles, so
    // narrowing then widening never silently drops a tick; scoping to visible
    // means a filtered action never touches rows the operator can't see.
    return this._visibleTxns().filter(t => this._selSet.has(t.id)).map(t => t.id);
  },

  // ── Identify (server-side suggestion stub) ────────────────────────────
  async runIdentify() {
    const ids = this._selectedIds();
    if (!ids.length) { alert('Nenhuma transação selecionada.'); return; }
    const myGen = ++this._opGen;
    showBusy(`Identificando ${ids.length} transação(ões)…`);
    // As linhas de /search (this._txns) já trazem todos os campos que o
    // classificador e o cálculo de execution-extras usam, então enviamos os
    // objetos direto — o servidor NÃO re-busca no Mongo (sem ida por id).
    const sel = new Set(ids);
    const transactions = this._txns.filter(t => sel.has(t.id));
    let r;
    try {
      r = await api('POST', '/api/beehus/identify-transactions/identify', {transactions});
    } finally {
      hideBusy();
    }
    if (myGen !== this._opGen) return;   // result is stale — a newer op started
    if (!r.ok) { alert('Falha na identificação. Veja o Log.'); return; }
    const sugs = (r.body && r.body.suggestions) || [];
    let filledType = 0, filledSec = 0;
    for (const s of sugs) {
      this._suggestions[s.transactionId] = {
        beehusTransactionType: s.beehusTransactionType || '',
        securityId:            s.securityId || '',
        needsSecurity:         !!s.needsSecurity,
        confidence:            (typeof s.confidence === 'number') ? s.confidence : null,
        source:                s.source || '',
        needsReview:           !!s.needsReview,
        securityName:          s.securityName || '',
        securityMainId:        s.securityMainId || '',
        securityConfidence:    (typeof s.securityConfidence === 'number') ? s.securityConfidence : null,
        securitySource:        s.securitySource || '',
        securityAmbiguous:     !!s.securityAmbiguous,
        securityAlternatives:  Array.isArray(s.securityAlternatives) ? s.securityAlternatives : [],
        // Derived execution-price / IRRF fields (see _execCell / _irrfCell).
        executionPrice:        (typeof s.executionPrice === 'number') ? s.executionPrice : null,
        irrf:                  (typeof s.irrf === 'number') ? s.irrf : null,
        pu:                    (typeof s.pu === 'number') ? s.pu : null,
        amountDifference:      (typeof s.amountDifference === 'number') ? s.amountDifference : null,
        securityType:          s.securityType || '',
        formerDate:            s.formerDate || '',
        withinGate:            !!s.withinGate,
        execGroupKey:          s.execGroupKey || '',
      };
      // A fresh calculation supersedes any prior manual edit for this row.
      delete this._execEdits[s.transactionId];
      if (s.beehusTransactionType) filledType++;
      if (s.securityId)            filledSec++;
    }
    // Re-render so the suggestion selects pick up the new values.
    this._render();
    if (!filledType && !filledSec) {
      alert('Identificação concluída — nenhuma sugestão automática foi gerada (algoritmo ainda em definição). Edite os campos manualmente e clique em Implementar.');
    }
  },

  // ── Apply (PATCH per-row) ─────────────────────────────────────────────
  // Both the Tipo sugerido and Security sugerido cells were replaced by a
  // text + Edit button + modal flow. The modals write into `_suggestions`,
  // so the diff against the row's DB values is computed from there (no
  // hidden inputs / DOM scraping anymore).
  // Build the list of effective patches. The typed-but-unsecured shape is
  // allowed through — the operator may legitimately want to set a type now
  // and fill the security later (e.g. when the security record hasn't been
  // ingested yet). Reinforcement rules are no longer auto-saved here; the
  // operator opts in explicitly via the "Salvar como reforço" button in the
  // security-edit modal.
  _computePatches() {
    const ids = new Set(this._selectedIds());
    const out = [];
    for (const t of this._txns) {
      if (!ids.has(t.id)) continue;
      const sug = this._suggestions[t.id] || {};
      const newType = sug.beehusTransactionType || '';
      const effectiveType = newType || t.type;
      const cellEnabled = (sug.needsSecurity || this._typesNeedingSecurity.has(effectiveType));
      const newSec = cellEnabled ? (sug.securityId || '') : '';
      const payload = {};
      if (newType && newType !== t.type)               payload.beehusTransactionType = newType;
      if (newSec  && newSec  !== (t.securityId || '')) payload.securityId            = newSec;
      if (Object.keys(payload).length) out.push({id: t.id, txn: t, payload});
    }
    this._skippedRows = [];
    return out;
  },

  // Effective derived value coerced to a number, or null when not applicable /
  // cleared by the operator.
  _effExtra(t, sug, kind) {
    const v = this._extraVal(t, sug, kind);
    if (v === '' || v == null) return null;
    const n = Number(v);
    return isNaN(n) ? null : n;
  },

  // Build the executionPrice + IRRF uploads for the SELECTED rows. executionPrices
  // are deduped by wallet|security|liquidationDate (one price per group); an
  // operator-edited value wins over the computed one. IRRF makes one `taxes`
  // transaction per source row.
  _buildExecExtras() {
    const ids = new Set(this._selectedIds());
    const execMap = new Map();
    const taxes = [];
    for (const t of this._txns) {
      if (!ids.has(t.id)) continue;
      const sug = this._suggestions[t.id] || {};
      // Eligibility is re-checked against the CURRENT effective type so a manual
      // type change after Identificar (without re-identifying) can't upload a
      // stale price/IRRF for a now-ineligible row.
      const etype = sug.beehusTransactionType || t.type || '';
      // brazilianFund → only IRRF, never an execution price (mirrors backend).
      const execEligible = ['buySell', 'maturity'].includes(etype) && sug.securityType !== 'brazilianFund';
      const ex = execEligible ? this._effExtra(t, sug, 'executionPrice') : null;
      if (ex != null) {
        const secId = sug.securityId || t.securityId || '';
        if (secId && t.walletId && t.liquidationDate) {
          const key = `${t.walletId}|${secId}|${t.liquidationDate}`;
          const edited = !!(this._execEdits[t.id] && 'executionPrice' in this._execEdits[t.id]);
          const prev = execMap.get(key);
          if (!prev || (edited && !prev.edited)) {
            execMap.set(key, {
              walletId: t.walletId, securityId: secId, positionDate: t.liquidationDate,
              executionPrice: ex, edited,
              _sec: sug.securityName || t.securityName || secId, _wallet: t.walletName,
            });
          }
        }
      }
      const ir = (etype === 'buySell') ? this._effExtra(t, sug, 'irrf') : null;
      if (ir != null) {
        // Send the source-txn fields the backend needs (it no longer re-reads
        // the txn by id from Mongo). securityId prefers the identified one.
        taxes.push({
          sourceTransactionId: t.id, balance: ir,
          walletId: t.walletId, entityId: t.entityId,
          securityId: (sug.securityId || t.securityId || ''),
          currencyId: t.currencyId, operationDate: t.operationDate,
          liquidationDate: t.liquidationDate, description: t.description,
          _sec: sug.securityName || t.securityName || '', _wallet: t.walletName, _liq: t.liquidationDate,
        });
      }
    }
    return {execPrices: Array.from(execMap.values()), taxes};
  },

  confirmApply() {
    const ids = this._selectedIds();
    if (!ids.length) { alert('Nenhuma transação selecionada.'); return; }
    const patches = this._computePatches();
    const extras = this._buildExecExtras();
    const nExec = extras.execPrices.length, nTax = extras.taxes.length;
    if (!patches.length && !nExec && !nTax) {
      alert('Nenhuma alteração efetiva nas linhas selecionadas — preencha o tipo/security ou um preço de execução / IRRF antes de implementar.');
      return;
    }
    const securityNameById = new Map(this._securities.map(s => [s.id, s.name]));
    const fmt = (n) => n == null ? '' : Number(n).toLocaleString('pt-BR', {maximumFractionDigits: 6});
    const rows = patches.map(p => {
      const cells = [];
      if ('beehusTransactionType' in p.payload) {
        cells.push(`Tipo: ${this._escape(p.txn.type || '∅')} → ${this._escape(p.payload.beehusTransactionType)}`);
      }
      if ('securityId' in p.payload) {
        const newName = securityNameById.get(p.payload.securityId) || p.payload.securityId;
        cells.push(`Security: ${this._escape(p.txn.securityName || '∅')} → ${this._escape(newName)}`);
      }
      return `<tr>
        <td>${this._escape(p.txn.liquidationDate)}</td>
        <td>${this._escape(p.txn.walletName)}</td>
        <td>${cells.join(' · ')}</td>
      </tr>`;
    }).join('');
    const execRows = extras.execPrices.map((e, i) => `<tr>
        <td><input type="checkbox" class="i-exec-chk" data-idx="${i}" checked style="cursor:pointer"
                   onchange="IdentifyTxn._syncExtraMaster('exec')"></td>
        <td>${this._escape(e.positionDate)}</td>
        <td>${this._escape(e._wallet)}</td>
        <td>${this._escape(e._sec)}</td>
        <td class="text-right">${fmt(e.executionPrice)}</td>
      </tr>`).join('');
    const taxRows = extras.taxes.map((x, i) => `<tr>
        <td><input type="checkbox" class="i-tax-chk" data-idx="${i}" checked style="cursor:pointer"
                   onchange="IdentifyTxn._syncExtraMaster('tax')"></td>
        <td>${this._escape(x._liq)}</td>
        <td>${this._escape(x._wallet)}</td>
        <td>${this._escape(x._sec)}</td>
        <td class="text-right">${fmt(x.balance)}</td>
      </tr>`).join('');
    document.getElementById('i-apply-confirm-body').innerHTML = `
      <div class="text-[11px] text-gray-600 mb-2">
        <strong>${patches.length}</strong> de ${ids.length} transações selecionadas
        serão atualizadas. Linhas sem alteração efetiva são ignoradas.
        ${(nExec || nTax) ? `<br>Marque/desmarque abaixo o que deseja enviar à API:
          <strong>${nExec}</strong> preço(s) de execução · <strong>${nTax}</strong> lançamento(s)
          de IRRF (transações <code>taxes</code>).` : ''}
      </div>
      ${patches.length ? `<div class="table-wrap border rounded mb-2">
        <table class="txn-table"><thead>
          <tr><th>Data liquid.</th><th>Wallet</th><th>Alterações</th></tr>
        </thead><tbody>${rows}</tbody></table>
      </div>` : ''}
      ${nExec ? `<h4 class="text-[12px] font-semibold mt-2 mb-1">
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" id="i-exec-all" checked onchange="IdentifyTxn.toggleExtraAll('exec', this.checked)">
          Enviar preços de execução (API)
        </label>
      </h4>
      <div class="table-wrap border rounded mb-2">
        <table class="txn-table"><thead>
          <tr><th style="width:28px"></th><th>Data (positionDate)</th><th>Wallet</th><th>Security</th><th class="text-right">Preço</th></tr>
        </thead><tbody>${execRows}</tbody></table>
      </div>` : ''}
      ${nTax ? `<h4 class="text-[12px] font-semibold mt-2 mb-1">
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" id="i-tax-all" checked onchange="IdentifyTxn.toggleExtraAll('tax', this.checked)">
          Criar lançamentos de IRRF (transações <code>taxes</code>)
        </label>
      </h4>
      <div class="table-wrap border rounded">
        <table class="txn-table"><thead>
          <tr><th style="width:28px"></th><th>Data liquid.</th><th>Wallet</th><th>Security</th><th class="text-right">Balance (IRRF)</th></tr>
        </thead><tbody>${taxRows}</tbody></table>
      </div>` : ''}`;
    this._pendingPatches = patches;
    this._pendingExtras = extras;
    document.getElementById('i-apply-confirm-modal').classList.add('show');
  },

  // Master checkbox for a confirm-modal extras bucket ('exec' | 'tax'): toggle
  // every per-row checkbox in that bucket.
  toggleExtraAll(kind, checked) {
    const sel = kind === 'exec' ? '.i-exec-chk' : '.i-tax-chk';
    document.querySelectorAll('#i-apply-confirm-body ' + sel)
      .forEach(c => { c.checked = checked; });
  },

  // Keep a bucket's master checkbox in sync (checked / unchecked / indeterminate)
  // as the operator toggles individual rows.
  _syncExtraMaster(kind) {
    const sel = kind === 'exec' ? '.i-exec-chk' : '.i-tax-chk';
    const master = document.getElementById(kind === 'exec' ? 'i-exec-all' : 'i-tax-all');
    if (!master) return;
    const boxes = Array.from(document.querySelectorAll('#i-apply-confirm-body ' + sel));
    const n = boxes.filter(c => c.checked).length;
    master.checked = n === boxes.length;
    master.indeterminate = n > 0 && n < boxes.length;
  },

  cancelApply() {
    document.getElementById('i-apply-confirm-modal').classList.remove('show');
    this._pendingPatches = null;
    this._pendingExtras = null;
  },

  async runApply() {
    const patches = this._pendingPatches || [];
    const allExtras = this._pendingExtras || {execPrices: [], taxes: []};
    // Honour the operator's per-row / per-category checkboxes in the confirm
    // modal — read them BEFORE hiding it (the DOM is still intact). An empty
    // selection in a bucket means "don't send that bucket".
    const picked = (cls) => {
      const set = new Set();
      document.querySelectorAll('#i-apply-confirm-body ' + cls)
        .forEach(c => { if (c.checked) set.add(Number(c.dataset.idx)); });
      return set;
    };
    const execSel = picked('.i-exec-chk');
    const taxSel  = picked('.i-tax-chk');
    const extras = {
      execPrices: (allExtras.execPrices || []).filter((_, i) => execSel.has(i)),
      taxes:      (allExtras.taxes || []).filter((_, i) => taxSel.has(i)),
    };
    document.getElementById('i-apply-confirm-modal').classList.remove('show');
    const nExtra = extras.execPrices.length + extras.taxes.length;
    if (!patches.length && !nExtra) return;
    // Null-out the queue immediately so a re-click while the loop is running
    // can't spawn duplicate PATCHes / uploads for rows we're already processing.
    this._pendingPatches = null;
    this._pendingExtras = null;
    let ok = 0, fail = 0, mongoStale = 0, abortedAuth = false;
    showBusy(`Implementando 0/${patches.length}…`);
    try {
      for (let i = 0; i < patches.length; i++) {
        const p = patches[i];
        showBusy(`Implementando ${i + 1}/${patches.length}…`);
        const r = await api('PATCH', `/api/beehus/transactions/${p.id}`, p.payload);
        if (r.ok) {
          ok++;
          // Upstream succeeded but the local Mongo mirror missed (no row
          // matched, or write error). Surface the count so the operator knows
          // a re-search may show stale values until the next external sync.
          if (r.body && r.body.mongoOk === false) mongoStale++;
          continue;
        }
        fail++;
        // Token expired / revoked — there is no point firing the remaining
        // PATCHes; bail and tell the operator to refresh login.
        if (r.status === 401) { abortedAuth = true; break; }
      }
    } finally {
      hideBusy();
    }

    // Push executionPrices + create the IRRF `taxes` transactions in one call —
    // unless the token already expired mid-PATCH (then bail so the operator
    // re-logs in before any upload fires).
    let execMsg = '';
    if (!abortedAuth && nExtra) {
      showBusy(`Enviando ${extras.execPrices.length} preço(s) de execução e ${extras.taxes.length} IRRF…`);
      try {
        const r = await api('POST', '/api/beehus/identify-transactions/execution-extras', {
          executionPrices: extras.execPrices.map(e => ({
            walletId: e.walletId, securityId: e.securityId,
            positionDate: e.positionDate, executionPrice: e.executionPrice,
          })),
          taxes: extras.taxes.map(x => ({
            sourceTransactionId: x.sourceTransactionId, balance: x.balance,
            walletId: x.walletId, entityId: x.entityId, securityId: x.securityId,
            currencyId: x.currencyId, operationDate: x.operationDate,
            liquidationDate: x.liquidationDate, description: x.description,
          })),
        });
        if (r.ok && r.body) {
          const b = r.body;
          execMsg = ` Preços exec.: ${b.execOk || 0} ok/${b.execFail || 0} falha ·`
                  + ` IRRF: ${b.taxOk || 0} ok/${b.taxFail || 0} falha.`;
          if (Array.isArray(b.errors) && b.errors.length) {
            console.warn('execution-extras errors:', b.errors);
          }
        } else if (r.status === 401) {
          abortedAuth = true;
        } else {
          execMsg = ' ⚠ Falha ao enviar preços de execução / IRRF (veja o Log).';
        }
      } finally {
        hideBusy();
      }
    }

    Token.refresh();
    const remaining = patches.length - (ok + fail);
    const tail = abortedAuth
      ? ` Token expirado — interrompido (${remaining} PATCH restante(s) e/ou uploads não enviados).`
      : '';
    const mongoTail = mongoStale
      ? ` ⚠ ${mongoStale} sem espelho local — atualize via re-busca após o próximo sync.`
      : '';
    alert(`Concluído: ${ok} atualizadas, ${fail} falhas.${execMsg}${tail}${mongoTail} Veja o Log para detalhes.`);
    // Refresh from Mongo so the just-implemented rows reflect the new DB values
    // (or drop out of "Não identificadas") and any new IRRF `taxes` row shows
    // up; keep the existing classifier output for surviving rows so the operator
    // can continue the filter→Implementar cycle without re-identifying.
    if (ok || (nExtra && !abortedAuth)) await this.search({preserveSuggestions: true});
  },

  // ── Config modal — types-needing-security only ───────────────────────
  openConfig() {
    const body = document.getElementById('i-config-body');
    const items = (this._allTypes.length ? this._allTypes : Array.from(this._typesNeedingSecurity)).slice();
    body.innerHTML = items.map(t => `
      <label class="flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-gray-50 cursor-pointer">
        <input type="checkbox" class="i-cfg-chk" data-type="${this._escape(t)}"
               ${this._typesNeedingSecurity.has(t) ? 'checked' : ''} />
        <span>${this._escape(t)}</span>
      </label>`).join('');
    document.getElementById('i-config-modal').classList.add('show');
  },

  cancelConfig() {
    document.getElementById('i-config-modal').classList.remove('show');
  },

  // ── Reforços modal (standalone) — opened from the ★ Reforços button in
  // the filters card. Reuses the same CRUD table IDs (#i-reinf-*) the old
  // Config "Reforços" tab used, so the edit/delete flow is unchanged.
  openReinforcements() {
    this._reinforcements = null;
    this._reinfSelected = new Set();
    const s = document.getElementById('i-reinf-search');
    if (s) s.value = '';
    document.getElementById('i-reinf-list-modal').classList.add('show');
    this._loadReinforcements();
  },
  closeReinforcements() {
    document.getElementById('i-reinf-list-modal').classList.remove('show');
  },

  async saveConfig() {
    const types = Array.from(document.querySelectorAll('.i-cfg-chk'))
      .filter(el => el.checked).map(el => el.dataset.type);
    const r = await api('PUT', '/api/beehus/identify-transactions/config', {typesNeedingSecurity: types});
    if (!r.ok) { alert('Falha ao salvar configurações. Veja o Log.'); return; }
    const b = r.body || {};
    this._typesNeedingSecurity = new Set(b.typesNeedingSecurity || []);
    if (Array.isArray(b.allTypes) && b.allTypes.length) this._allTypes = b.allTypes;
    this.cancelConfig();
    // Re-render the result table so each row's security cell reflects the
    // new config.
    if (this._txns.length) this._render();
  },

  // ── Reinforcements CRUD ───────────────────────────────────────────────
  // Loaded lazily into `this._reinforcements` (array). The list is display-only
  // with per-row selection checkboxes; editing and deleting go through the
  // dedicated #i-reinf-edit-modal / #i-reinf-del-modal modals instead of an
  // inline form + native confirm().
  async _loadReinforcements() {
    const r = await api('GET', '/api/beehus/identify-transactions/reinforcements');
    if (!r.ok) {
      alert('Falha ao carregar reforços. Veja o Log.');
      this._reinforcements = [];
    } else {
      this._reinforcements = (r.body && r.body.reinforcements) || [];
    }
    // Drop selections whose rule no longer exists after a reload.
    const live = new Set(this._reinforcements.map(x => x.key));
    this._reinfSelected = new Set([...(this._reinfSelected || [])].filter(k => live.has(k)));
    this._renderReinforcementsTable();
  },

  _reinfRuleByKey(key) {
    return (this._reinforcements || []).find(x => x.key === key) || null;
  },

  _renderReinforcementsTable() {
    const tbody  = document.getElementById('i-reinf-rows');
    const empty  = document.getElementById('i-reinf-empty');
    if (!tbody) return;
    if (!this._reinfSelected) this._reinfSelected = new Set();
    const rules = this._reinforcements || [];
    const q = ((document.getElementById('i-reinf-search') || {}).value || '').trim().toLowerCase();
    const filtered = q
      ? rules.filter(r =>
          (r.key || '').toLowerCase().includes(q) ||
          (r.beehusTransactionType || '').toLowerCase().includes(q) ||
          (r.securityName || '').toLowerCase().includes(q) ||
          (r.securityMainId || '').toLowerCase().includes(q))
      : rules.slice();
    // Selection is scoped to the visible (filtered) rows: drop any selected key
    // the current search hides, so the bulk-delete count + master checkbox always
    // match what's on screen and a hidden rule can never be deleted unseen.
    const visibleKeys = new Set(filtered.map(r => r.key));
    this._reinfSelected = new Set([...this._reinfSelected].filter(k => visibleKeys.has(k)));
    empty.classList.toggle('hidden', filtered.length > 0);
    tbody.innerHTML = filtered.map(r => this._reinforcementRow(r)).join('');
    this._reinfSyncSelectionUI();
  },

  _reinforcementRow(r) {
    const checked = this._reinfSelected.has(r.key) ? 'checked' : '';
    const secLabel = r.securityId
      ? (r.securityMainId
          ? `${this._escape(r.securityMainId)} · ${this._escape(r.securityName || r.securityId)}`
          : this._escape(r.securityName || r.securityId))
      : (r.noSecurity
          ? '<span class="badge-info text-[10px] px-1.5 py-0.5 rounded" title="Reforço marcado como sem security — a cascata de security é pulada">sem security</span>'
          : '<span class="text-gray-400 italic">—</span>');
    const seenLabel = (r.lastSeenAt || '').slice(0, 10);
    return `
      <tr>
        <td><input type="checkbox" class="i-reinf-chk" data-key="${this._escape(r.key)}" ${checked}
                   style="cursor:pointer"
                   onchange="IdentifyTxn._reinfToggleRow(${this._jsAttr(r.key)}, this.checked)"></td>
        <td class="font-mono text-[11px]"
            style="white-space: pre-wrap; word-break: break-word">${this._escape(r.key)}</td>
        <td class="text-[11px]">${this._escape(r.beehusTransactionType) || '<span class="text-gray-400 italic">—</span>'}</td>
        <td class="text-[11px]">${secLabel}</td>
        <td class="text-right text-[11px]">${r.hits || 0}</td>
        <td class="text-[11px] text-gray-500">${this._escape(seenLabel)}</td>
        <td>
          <div class="flex gap-1">
            <button class="btn btn-muted" style="padding:1px 6px;font-size:10px"
                    onclick="IdentifyTxn._reinfOpenEdit(${this._jsAttr(r.key)})">✎ Editar</button>
            <button class="btn btn-danger" style="padding:1px 6px;font-size:10px"
                    onclick="IdentifyTxn._reinfOpenDelete([${this._jsAttr(r.key)}])">Excluir</button>
          </div>
        </td>
      </tr>`;
  },

  // ── Selection (bulk delete) ───────────────────────────────────────────
  _reinfToggleRow(key, checked) {
    if (checked) this._reinfSelected.add(key); else this._reinfSelected.delete(key);
    this._reinfSyncSelectionUI();
  },
  _reinfToggleAll(checked) {
    // Toggle only the rows currently visible under the search filter.
    document.querySelectorAll('#i-reinf-rows .i-reinf-chk').forEach(c => {
      c.checked = checked;
      if (checked) this._reinfSelected.add(c.dataset.key); else this._reinfSelected.delete(c.dataset.key);
    });
    this._reinfSyncSelectionUI();
  },
  _reinfSyncSelectionUI() {
    const boxes = Array.from(document.querySelectorAll('#i-reinf-rows .i-reinf-chk'));
    const nVisible = boxes.filter(c => c.checked).length;
    const master = document.getElementById('i-reinf-all');
    if (master) {
      master.checked = boxes.length > 0 && nVisible === boxes.length;
      master.indeterminate = nVisible > 0 && nVisible < boxes.length;
    }
    const btn = document.getElementById('i-reinf-bulk-del');
    if (btn) {
      const total = this._reinfSelected.size;
      btn.disabled = total === 0;
      btn.textContent = total ? `Excluir selecionados (${total})` : 'Excluir selecionados';
    }
  },
  _reinfBulkDelete() {
    const keys = [...this._reinfSelected];
    if (keys.length) this._reinfOpenDelete(keys);
  },

  // ── Edit modal ────────────────────────────────────────────────────────
  async _reinfOpenEdit(key) {
    const r = this._reinfRuleByKey(key);
    if (!r) return;
    this._reinfEditKey = key;
    this._reinfEditSec = {securityId: r.securityId || '', securityName: r.securityName || ''};
    const sel = document.getElementById('i-reinf-edit-type');
    sel.innerHTML = '<option value="">(nenhum)</option>' +
      (this._allTypes || []).map(t =>
        `<option value="${this._escape(t)}" ${t === r.beehusTransactionType ? 'selected' : ''}>${this._escape(t)}</option>`
      ).join('');
    document.getElementById('i-reinf-edit-key').value = r.key;
    document.getElementById('i-reinf-edit-secmain').value = r.securityMainId || '';
    // Trust the stored flag; _reinfEditNoSecToggle() below disables + unchecks it
    // when a security is present, so the mutual exclusion is enforced uniformly.
    document.getElementById('i-reinf-edit-nosec').checked = !!r.noSecurity;
    document.getElementById('i-reinf-edit-sec-search').value = '';
    const res = document.getElementById('i-reinf-edit-sec-results');
    res.classList.add('hidden'); res.innerHTML = '';
    this._reinfRenderPickedSecurity();
    this._reinfEditNoSecToggle();
    this._reinfEditPreview();
    document.getElementById('i-reinf-edit-modal').classList.add('show');
    // The securities list (id+name) is company-agnostic; lazy-load if empty so
    // the picker works even before a company/search was run.
    if (!this._securities || !this._securities.length) {
      const sr = await api('GET', '/api/beehus/filters/securities');
      if (sr.ok) this._securities = sr.body || [];
    }
  },
  _reinfCloseEdit() {
    document.getElementById('i-reinf-edit-modal').classList.remove('show');
    this._reinfEditKey = null;
    this._reinfEditSec = null;
    clearTimeout(this._reinfPreviewTimer);
  },
  _reinfRenderPickedSecurity() {
    const el = document.getElementById('i-reinf-edit-sec-picked');
    const s = this._reinfEditSec || {};
    el.innerHTML = s.securityId
      ? `<span class="badge-ok text-[10px] px-1.5 py-0.5 rounded">${this._escape(s.securityName || s.securityId)}</span>
         <button class="btn btn-muted ml-1" style="padding:0 6px;font-size:10px"
                 onclick="IdentifyTxn._reinfClearSecurity()">✕ limpar</button>`
      : '<span class="text-gray-400 italic">nenhuma security escolhida</span>';
  },
  _reinfSecSearch() {
    const q = (document.getElementById('i-reinf-edit-sec-search').value || '').trim().toLowerCase();
    const res = document.getElementById('i-reinf-edit-sec-results');
    if (!q) { res.classList.add('hidden'); res.innerHTML = ''; return; }
    const hits = (this._securities || [])
      .filter(s => (s.name || '').toLowerCase().includes(q) || (s.id || '').toLowerCase().includes(q))
      .slice(0, 50);
    res.innerHTML = hits.length
      ? hits.map(s => `
          <div class="px-2 py-1 hover:bg-gray-50 cursor-pointer text-[11px]"
               onclick="IdentifyTxn._reinfPickSecurity(${this._jsAttr(s.id)}, ${this._jsAttr(s.name || '')})">
            ${this._escape(s.name || s.id)}
          </div>`).join('')
      : '<div class="px-2 py-1 text-gray-400 text-[11px]">Nenhuma security encontrada.</div>';
    res.classList.remove('hidden');
  },
  _reinfPickSecurity(id, name) {
    this._reinfEditSec = {securityId: id, securityName: name};
    // mainId is unknown for a freshly-picked security → clear it (operator can
    // fill manually); a chosen security clears the mutually-exclusive nosec.
    document.getElementById('i-reinf-edit-secmain').value = '';
    document.getElementById('i-reinf-edit-nosec').checked = false;
    document.getElementById('i-reinf-edit-sec-search').value = '';
    const res = document.getElementById('i-reinf-edit-sec-results');
    res.classList.add('hidden'); res.innerHTML = '';
    this._reinfRenderPickedSecurity();
    this._reinfEditNoSecToggle();
  },
  _reinfClearSecurity() {
    this._reinfEditSec = {securityId: '', securityName: ''};
    this._reinfRenderPickedSecurity();
    this._reinfEditNoSecToggle();
  },
  // "Sem security" is mutually exclusive with a chosen security.
  _reinfEditNoSecToggle() {
    const hasSec = !!(this._reinfEditSec && this._reinfEditSec.securityId);
    const nosec = document.getElementById('i-reinf-edit-nosec');
    if (nosec) { if (hasSec) nosec.checked = false; nosec.disabled = hasSec; }
  },
  // Debounced normalized-key preview + collision check via the server normalizer
  // (single source of truth — the JS NFD/uppercase approximation misses token
  // masking like <OPCODE>/CDB<CODE>).
  _reinfEditPreview() {
    clearTimeout(this._reinfPreviewTimer);
    const out = document.getElementById('i-reinf-edit-preview');
    const raw = (document.getElementById('i-reinf-edit-key').value || '').trim();
    if (!raw) { out.innerHTML = '<span class="text-amber-700">⚠ trecho vazio — não pode ser salvo</span>'; return; }
    out.textContent = 'normalizando…';
    this._reinfPreviewTimer = setTimeout(async () => {
      const r = await api('POST', '/api/beehus/identify-transactions/reinforcement/normalize', {description: raw});
      // Bail if the modal was closed while the request was in flight (don't
      // paint a hidden modal with stale text).
      if (!document.getElementById('i-reinf-edit-modal').classList.contains('show')) return;
      const key = (r.ok && r.body && r.body.key) || '';
      let html = `Chave normalizada: <span class="font-mono">${this._escape(key) || '—'}</span>`;
      if (key && key !== this._reinfEditKey) {
        html += ` · <span class="text-amber-700">a chave vai mudar — o reforço antigo será removido</span>`;
        if (this._reinfRuleByKey(key)) {
          html += `<br><span class="text-red-600">⚠ já existe um reforço com esta chave — salvar vai sobrescrevê-lo.</span>`;
        }
      }
      out.innerHTML = html;
    }, 250);
  },

  // ── Delete modal (single + bulk) ──────────────────────────────────────
  _reinfOpenDelete(keys) {
    const list = (keys || []).map(k => this._reinfRuleByKey(k)).filter(Boolean);
    if (!list.length) return;
    this._reinfDeleteKeys = list.map(r => r.key);
    const rows = list.map(r => {
      const sec = r.securityId
        ? (r.securityMainId ? `${r.securityMainId} · ${r.securityName || ''}` : (r.securityName || r.securityId))
        : (r.noSecurity ? 'sem security' : '—');
      return `<tr>
        <td class="font-mono text-[11px]" style="white-space:pre-wrap;word-break:break-word">${this._escape(r.key)}</td>
        <td class="text-[11px]">${this._escape(r.beehusTransactionType || '—')}</td>
        <td class="text-[11px]">${this._escape(sec)}</td>
        <td class="text-right text-[11px]">${r.hits || 0}</td>
      </tr>`;
    }).join('');
    document.getElementById('i-reinf-del-body').innerHTML = `
      <table class="txn-table"><thead>
        <tr><th>Trecho (chave)</th><th>Tipo</th><th>Security</th><th class="text-right">Hits</th></tr>
      </thead><tbody>${rows}</tbody></table>`;
    document.getElementById('i-reinf-del-confirm').textContent =
      list.length > 1 ? `Excluir ${list.length}` : 'Excluir';
    document.getElementById('i-reinf-del-modal').classList.add('show');
  },
  _reinfCloseDelete() {
    document.getElementById('i-reinf-del-modal').classList.remove('show');
    this._reinfDeleteKeys = null;
  },
  async _reinfConfirmDelete() {
    const keys = this._reinfDeleteKeys || [];
    document.getElementById('i-reinf-del-modal').classList.remove('show');
    this._reinfDeleteKeys = null;
    if (!keys.length) return;
    let ok = 0, fail = 0;
    showBusy(`Excluindo 0/${keys.length}…`);
    try {
      for (let i = 0; i < keys.length; i++) {
        showBusy(`Excluindo ${i + 1}/${keys.length}…`);
        const r = await api('DELETE', '/api/beehus/identify-transactions/reinforcement', {key: keys[i]});
        if (r.ok) { ok++; this._reinfSelected.delete(keys[i]); } else { fail++; }
      }
    } finally {
      hideBusy();
    }
    if (fail) alert(`Excluídos: ${ok}. Falhas: ${fail}. Veja o Log.`);
    await this._loadReinforcements();
  },

  // Save flow. The authoritative normalized key comes from the server (matches
  // storage exactly, incl. token masking); if it changed we delete the old key
  // first, then POST upserts. Type/security-only edits keep the same key.
  async _reinfSaveEdit() {
    const raw   = (document.getElementById('i-reinf-edit-key').value || '').trim();
    const btt   = (document.getElementById('i-reinf-edit-type').value || '').trim();
    const sec   = this._reinfEditSec || {};
    const sid   = sec.securityId || '';
    const sname = sec.securityName || '';
    const smain = (document.getElementById('i-reinf-edit-secmain').value || '').trim();
    const noSecEl = document.getElementById('i-reinf-edit-nosec');
    const noSecurity = !!(noSecEl && noSecEl.checked) && !sid;
    if (!raw) { alert('O trecho não pode ficar vazio.'); return; }
    if (!btt && !sid) { alert('Defina pelo menos o tipo ou uma security.'); return; }
    const nr = await api('POST', '/api/beehus/identify-transactions/reinforcement/normalize', {description: raw});
    const newKey = (nr.ok && nr.body && nr.body.key) || '';
    if (!newKey) { alert('Trecho vazio após normalização.'); return; }
    const oldKey = this._reinfEditKey;
    if (newKey !== oldKey) {
      if (this._reinfRuleByKey(newKey) &&
          !confirm(`Já existe um reforço com a chave:\n\n${newKey}\n\nSalvar vai sobrescrevê-lo. Continuar?`)) return;
      const d = await api('DELETE', '/api/beehus/identify-transactions/reinforcement', {key: oldKey});
      if (!d.ok) { alert('Falha ao renomear (delete). Veja o Log.'); return; }
    }
    const r = await api('POST', '/api/beehus/identify-transactions/reinforcement', {
      description:           raw,
      beehusTransactionType: btt,
      securityId:            sid,
      securityName:          sname,
      securityMainId:        smain,
      noSecurity:            noSecurity,
    });
    if (!r.ok) { alert('Falha ao salvar. Veja o Log.'); return; }
    this._reinfCloseEdit();
    await this._loadReinforcements();
  },

  // ── Reset ─────────────────────────────────────────────────────────────
  // ══════════════════════════════════════════════════════════════════════════
  // Editar / Excluir (fundido da antiga view DeleteTxn) — opera sobre `_txns`,
  // `_selectedIds()` e o mesmo grid. Edita o valor do BANCO (colunas "atual"),
  // separado do fluxo de sugestão (colunas "sugerido"). Via PATCH/DELETE em
  // /api/beehus/transactions/<id> — os mesmos endpoints já usados por runApply.
  // ══════════════════════════════════════════════════════════════════════════

  // ── Edit: per-field value-mapping ─────────────────────────────────────────
  _txnFieldValue(t, fieldKey) {
    const f = this.EDIT_FIELDS[fieldKey];
    return f ? (t[f.txnKey] || '') : '';
  },
  _txnFieldLabel(t, fieldKey) {
    const f = this.EDIT_FIELDS[fieldKey];
    if (!f) return '';
    if (f.kind === 'entity')   return t.entityName   || t.entityId   || '';
    if (f.kind === 'security') return t.securityName || t.securityId || '';
    return this._txnFieldValue(t, fieldKey);
  },

  _uniqueValues(fieldKey, scopeIds) {
    const map = new Map(); // value → display label
    for (const t of this._txns) {
      if (scopeIds && !scopeIds.has(t.id)) continue;
      const v = this._txnFieldValue(t, fieldKey);
      if (!map.has(v)) map.set(v, this._txnFieldLabel(t, fieldKey));
    }
    return Array.from(map, ([value, label]) => ({value, label}))
      .sort((a, b) => String(a.label).localeCompare(String(b.label), 'pt-BR'));
  },

  _newValueControl(fieldKey, oldValue, currentNewValue, idAttr) {
    const f = this.EDIT_FIELDS[fieldKey];
    const cur = currentNewValue == null ? '' : currentNewValue;
    const escAttr = (s) => this._escape(s).replace(/"/g, '&quot;');
    if (f.kind === 'date') {
      return `<input type="date" class="input" id="${idAttr}" data-old="${escAttr(oldValue)}" value="${escAttr(cur)}" />`;
    }
    if (f.kind === 'text') {
      return `<input type="text" class="input" id="${idAttr}" data-old="${escAttr(oldValue)}" value="${escAttr(cur)}" />`;
    }
    if (f.kind === 'type') {
      const opts = this._editTypes().map(t => `<option value="${t}" ${t===cur?'selected':''}>${this._escape(t)}</option>`).join('');
      return `<select class="input" id="${idAttr}" data-old="${escAttr(oldValue)}">
                <option value="">(manter)</option>${opts}
              </select>`;
    }
    if (f.kind === 'entity') {
      const opts = this._entities.map(e => `<option value="${e.id}" ${e.id===cur?'selected':''}>${this._escape(e.name)}</option>`).join('');
      return `<select class="input" id="${idAttr}" data-old="${escAttr(oldValue)}">
                <option value="">(manter)</option>${opts}
              </select>`;
    }
    if (f.kind === 'security') {
      const cap = 2000;
      const slice = this._securities.slice(0, cap);
      const inSlice = slice.some(s => s.id === cur);
      const tail = (cur && !inSlice) ? this._securities.find(s => s.id === cur) : null;
      const opts = slice.map(s => `<option value="${s.id}" ${s.id===cur?'selected':''}>${this._escape(s.name)}</option>`).join('') +
                   (tail ? `<option value="${tail.id}" selected>${this._escape(tail.name)}</option>` : '');
      return `<select class="input" id="${idAttr}" data-old="${escAttr(oldValue)}">
                <option value="">(manter)</option>${opts}
              </select>`;
    }
    return '';
  },

  openEditField(fieldKey) {
    if (!fieldKey) return;
    if (!this._txns.length) { alert('Faça uma busca antes de configurar a edição.'); document.getElementById('i-edit-field-add').value = ''; return; }
    const f = this.EDIT_FIELDS[fieldKey];
    if (!f) return;

    // The rows currently CHECKED are the edit target. The mapping table and the
    // inline cell inputs run over THIS live set — no frozen scope, no
    // force-unchecking (the single source of truth stays the DOM checkboxes, so
    // Identificar/Implementar/Editar/Excluir all act on the same rows).
    const checked = this._selectedIds();
    if (!checked.length) {
      alert('Selecione ao menos uma transação no listing antes de configurar a edição.');
      document.getElementById('i-edit-field-add').value = '';
      return;
    }
    const checkedSet = new Set(checked);
    // Turn this field's cells (on the checked rows, bounded by
    // INLINE_EDIT_MAX_ROWS) into inline inputs so the operator can tweak
    // individual rows. Does NOT change the selection.
    this._inlineEditField = fieldKey;
    this._render();

    this._editingField = fieldKey;
    document.getElementById('i-edit-field-add').value = '';
    document.getElementById('i-edit-field-title').textContent = f.label;

    const values = this._uniqueValues(fieldKey, checkedSet);
    const counts = new Map();
    for (const t of this._txns) {
      if (!checkedSet.has(t.id)) continue;
      const v = this._txnFieldValue(t, fieldKey);
      counts.set(v, (counts.get(v) || 0) + 1);
    }
    const existing = this._edits[fieldKey] || {};

    const body = document.getElementById('i-edit-field-body');
    body.innerHTML = `
      <div class="text-[11px] text-gray-500 mb-2">${values.length} valor(es) único(s) em ${checkedSet.size} transação(ões) selecionada(s).</div>
      <table class="txn-table"><thead>
        <tr><th>Valor atual</th><th style="width:40%">Novo valor</th><th class="text-right" style="width:80px">Qtd</th></tr>
      </thead><tbody>
        ${values.map((v, i) => `
          <tr>
            <td>${v.label === '' ? '<span class="text-gray-400 italic">(vazio)</span>' : this._escape(v.label)}
                ${v.label !== v.value ? `<div class="text-[10px] text-gray-400">${this._escape(v.value)}</div>` : ''}</td>
            <td>${this._newValueControl(fieldKey, v.value, existing[v.value], 'i-edit-new-' + i)}</td>
            <td class="text-right">${counts.get(v.value) || 0}</td>
          </tr>`).join('')}
      </tbody></table>`;

    document.getElementById('i-edit-field-modal').classList.add('show');
  },

  cancelEditField() {
    document.getElementById('i-edit-field-modal').classList.remove('show');
    this._editingField = null;
  },

  clearEditField() {
    document.querySelectorAll('#i-edit-field-body [id^="i-edit-new-"]').forEach(el => { el.value = ''; });
  },

  saveEditField() {
    if (!this._editingField) return;
    const map = {};
    document.querySelectorAll('#i-edit-field-body [id^="i-edit-new-"]').forEach(el => {
      const oldVal = el.dataset.old || '';
      const newVal = el.value;
      if (newVal !== '' && newVal !== oldVal) map[oldVal] = newVal;
    });
    if (Object.keys(map).length) this._edits[this._editingField] = map;
    else                          delete this._edits[this._editingField];
    this.cancelEditField();
    this._renderEditChips();
  },

  removeEditField(fieldKey) {
    delete this._edits[fieldKey];
    this._renderEditChips();
  },

  _pruneEdits() {
    // Drop mappings whose oldValue no longer appears in the current results.
    for (const fieldKey of Object.keys(this._edits)) {
      const present = new Set(this._uniqueValues(fieldKey).map(x => x.value));
      const map = this._edits[fieldKey];
      for (const oldVal of Object.keys(map)) if (!present.has(oldVal)) delete map[oldVal];
      if (!Object.keys(map).length) delete this._edits[fieldKey];
    }
  },

  // ── Inline cell editing (per-row overrides → `_tempEdits`) ────────────────
  _inlineEditControl(t, fieldKey) {
    const f = this.EDIT_FIELDS[fieldKey];
    if (!f) return '';
    const ov = this._tempEdits[t.id];
    const cur = (ov && fieldKey in ov) ? ov[fieldKey] : this._txnFieldValue(t, fieldKey);
    const escAttr = (s) => this._escape(String(s ?? '')).replace(/"/g, '&quot;');
    const txnId = escAttr(t.id);
    const handler = `onchange="IdentifyTxn.onInlineCellChange('${txnId}','${fieldKey}', this.value)"`;
    const css = 'style="padding:2px 4px;font-size:11px;height:24px;min-width:0;width:100%;max-width:240px"';
    if (f.kind === 'date') {
      return `<input type="date" class="input" ${css} ${handler} value="${escAttr(cur)}" />`;
    }
    if (f.kind === 'text') {
      return `<input type="text" class="input" ${css} ${handler} value="${escAttr(cur)}" />`;
    }
    if (f.kind === 'type') {
      const opts = this._editTypes().map(tp => `<option value="${tp}" ${tp===cur?'selected':''}>${this._escape(tp)}</option>`).join('');
      return `<select class="input" ${css} ${handler}>${opts}</select>`;
    }
    if (f.kind === 'entity') {
      const opts = this._entities.map(e => `<option value="${e.id}" ${e.id===cur?'selected':''}>${this._escape(e.name)}</option>`).join('');
      return `<select class="input" ${css} ${handler}>${opts}</select>`;
    }
    if (f.kind === 'security') {
      const cap = 2000;
      const slice = this._securities.slice(0, cap);
      const inSlice = slice.some(s => s.id === cur);
      const tail = (cur && !inSlice) ? this._securities.find(s => s.id === cur) : null;
      const opts = slice.map(s => `<option value="${s.id}" ${s.id===cur?'selected':''}>${this._escape(s.name)}</option>`).join('') +
                   (tail ? `<option value="${tail.id}" selected>${this._escape(tail.name)}</option>` : '');
      return `<select class="input" ${css} ${handler}>${opts}</select>`;
    }
    return '';
  },

  onInlineCellChange(txnId, fieldKey, value) {
    const t = this._txns.find(x => x.id === txnId);
    if (!t || !this.EDIT_FIELDS[fieldKey]) return;
    const oldVal = this._txnFieldValue(t, fieldKey);
    if (value === oldVal) {
      // Reverting a cell to its current value means "don't change this row".
      // If a value-mapping bucket (_edits) would otherwise patch this row, we
      // must record a KEEP-sentinel (override === current value) so
      // _computeEditPatches can cancel the bucket for this row. Without a
      // bucket, just drop the override.
      const bucket = this._edits[fieldKey];
      if (bucket && bucket[oldVal] !== undefined) {
        this._tempEdits[txnId] = this._tempEdits[txnId] || {};
        this._tempEdits[txnId][fieldKey] = oldVal;   // keep-sentinel
      } else if (this._tempEdits[txnId]) {
        delete this._tempEdits[txnId][fieldKey];
        if (!Object.keys(this._tempEdits[txnId]).length) delete this._tempEdits[txnId];
      }
    } else {
      this._tempEdits[txnId] = this._tempEdits[txnId] || {};
      this._tempEdits[txnId][fieldKey] = value;
    }
    // Refresh chips but DON'T re-render the table (would steal focus from the
    // input that just fired). Sync only the row's amber highlight.
    this._renderEditChips();
    for (const cb of document.querySelectorAll('.i-row')) {
      if (cb.dataset.id === txnId) {
        const tr = cb.closest('tr');
        if (tr) tr.classList.toggle('bg-amber-50', !!this._tempEdits[txnId]);
        break;
      }
    }
  },

  clearInlineEdits() {
    if (!Object.keys(this._tempEdits).length) return;
    this._tempEdits = {};
    if (this._txns && this._txns.length) this._render();
    else this._renderEditChips();
  },

  // ── Reforço temporário (edição em massa por descrição) ────────────────────
  // Aplica um valor a um campo em TODAS as linhas cujo `description` casa com o
  // trecho; grava override por linha em `_tempEdits` (mesmo caminho da edição
  // inline → `_computeEditPatches`). Em memória até a próxima busca. Distinto do
  // reforço de IDENTIFICAÇÃO (`openTempReinforce`, que escreve `_suggestions`);
  // reusa os helpers `_tempNorm`/`_tempScore`. Só as linhas SELECIONADAS são
  // gravadas no PATCH (computeEditPatches filtra por _selectedIds) — o preview
  // aparece em todas as que casam.
  EDIT_REINFORCE_MIN_SNIPPET: 4,

  openEditReinforce() {
    if (!this._txns || !this._txns.length) {
      alert('Faça uma busca primeiro — o reforço temporário aplica a edição ao listing já carregado.');
      return;
    }
    document.getElementById('i-er-snippet').value = '';
    document.getElementById('i-er-field').value = 'beehusTransactionType';
    this._editReinforceRefreshValue();
    this._editReinforceMatchCount();
    document.getElementById('i-edit-reinforce-modal').classList.add('show');
    requestAnimationFrame(() => document.getElementById('i-er-snippet').focus());
  },

  closeEditReinforce() {
    document.getElementById('i-edit-reinforce-modal').classList.remove('show');
  },

  _editReinforceRefreshValue() {
    const fieldKey = document.getElementById('i-er-field').value;
    document.getElementById('i-er-value-pane').innerHTML =
      this._newValueControl(fieldKey, '', '', 'i-er-value');
  },

  _editReinforceMatchCount() {
    const ns = this._tempNorm(document.getElementById('i-er-snippet').value);
    const hint = document.getElementById('i-er-match-hint');
    if (!ns) { hint.textContent = 'Digite o trecho.'; return; }
    let exact = 0, substr = 0;
    for (const t of this._txns) {
      const s = this._tempScore(ns, this._tempNorm(t.description || ''));
      if (s === 1.0) exact++;
      else if (s > 0) substr++;
    }
    hint.textContent = `${exact + substr} linha(s) bateriam · ${exact} match exato · ${substr} substring`;
  },

  applyEditReinforce() {
    const ns = this._tempNorm(document.getElementById('i-er-snippet').value);
    if (!ns) { alert('Digite o trecho a procurar.'); return; }
    if (ns.length < this.EDIT_REINFORCE_MIN_SNIPPET) {
      alert(`O trecho normalizado tem ${ns.length} caractere(s); precisa de pelo menos ${this.EDIT_REINFORCE_MIN_SNIPPET} para reduzir o risco de bater em linhas erradas.`);
      return;
    }
    const fieldKey = document.getElementById('i-er-field').value;
    if (!fieldKey || !this.EDIT_FIELDS[fieldKey]) { alert('Escolha o campo a aplicar.'); return; }
    const valEl = document.getElementById('i-er-value');
    if (!valEl) { alert('Informe o novo valor.'); return; }
    const newVal = valEl.value;
    if (newVal === '') { alert('Informe o novo valor.'); return; }

    let touched = 0;
    for (const t of this._txns) {
      if (this._tempScore(ns, this._tempNorm(t.description || '')) === 0) continue;
      const oldVal = this._txnFieldValue(t, fieldKey);
      if (newVal === oldVal) continue;          // no-op
      this._tempEdits[t.id] = this._tempEdits[t.id] || {};
      this._tempEdits[t.id][fieldKey] = newVal;
      touched++;
    }
    // Large-batch guard (mirrors the former DeleteTxn flow): a single click
    // rewriting many rows on a PATCH-ready flow deserves a second look.
    if (touched > 20) {
      if (!confirm(`Reforço temporário vai marcar ${touched} linha(s) com o novo valor. Continuar?`)) {
        for (const t of this._txns) {           // roll back what we just wrote
          const ov = this._tempEdits[t.id];
          if (ov && ov[fieldKey] === newVal) {
            delete ov[fieldKey];
            if (!Object.keys(ov).length) delete this._tempEdits[t.id];
          }
        }
        return;
      }
    }
    this.closeEditReinforce();
    if (!touched) {
      alert('Nenhuma linha foi afetada — verifique o trecho ou o novo valor (talvez já corresponda ao atual).');
      return;
    }
    this._render();
    const label = this.EDIT_FIELDS[fieldKey].label;
    alert(`Reforço temporário aplicado em ${touched} linha(s) (campo "${label}"). Revise no grid, selecione as linhas e use "Editar selecionadas" para gravar.`);
  },

  _renderEditChips() {
    const chips = document.getElementById('i-edit-chips');
    const summary = document.getElementById('i-edit-summary');
    if (!chips || !summary) return;
    const keys = Object.keys(this._edits);
    // Per-row inline edits chip (separate from the per-field bucket chips).
    const tempIds = Object.keys(this._tempEdits);
    const tempFields = new Set();
    for (const id of tempIds) {
      for (const f of Object.keys(this._tempEdits[id] || {})) tempFields.add(f);
    }
    const tempChip = tempIds.length
      ? `<span class="type-chip" style="background:#fef3c7;color:#92400e">
          ✎ Edições por linha · ${tempIds.length} linha(s) · ${tempFields.size} campo(s)
          <button type="button" class="chip-x" title="Limpar edições por linha (inline + reforço temporário)"
                  onclick="IdentifyTxn.clearInlineEdits()">×</button>
        </span>`
      : '';
    if (!keys.length && !tempIds.length) {
      chips.innerHTML = '';
      summary.textContent = 'nenhum campo configurado';
      return;
    }
    chips.innerHTML = keys.map(k => {
      const f = this.EDIT_FIELDS[k];
      const n = Object.keys(this._edits[k]).length;
      return `<span class="type-chip">
        ${this._escape(f ? f.label : k)} · ${n} valor(es)
        <button type="button" class="chip-x" title="Editar"
                onclick="IdentifyTxn.openEditField('${k}')">✎</button>
        <button type="button" class="chip-x" title="Remover"
                onclick="IdentifyTxn.removeEditField('${k}')">×</button>
      </span>`;
    }).join('') + tempChip;
    const parts = [];
    if (keys.length)    parts.push(`${keys.length} campo(s) configurado(s)`);
    if (tempIds.length) parts.push(`${tempIds.length} linha(s) com edição por linha`);
    summary.textContent = parts.join(' · ');
  },

  // ── Edit confirmation report ──────────────────────────────────────────────
  // Merges `_edits` (broad, by current-value bucket) with `_tempEdits`
  // (narrow, per-row inline). The per-row map wins on conflict.
  _computeEditPatches() {
    const ids = new Set(this._selectedIds());
    if (!ids.size) return [];
    const out = [];
    for (const t of this._txns) {
      if (!ids.has(t.id)) continue;
      const payload = {};
      for (const [fieldKey, map] of Object.entries(this._edits)) {
        const f = this.EDIT_FIELDS[fieldKey];
        if (!f) continue;
        const oldVal = this._txnFieldValue(t, fieldKey);
        const newVal = map[oldVal];
        if (newVal !== undefined && newVal !== '' && newVal !== oldVal) {
          payload[f.apiKey] = newVal;
        }
      }
      const tempOv = this._tempEdits[t.id];
      if (tempOv) {
        for (const [fieldKey, newVal] of Object.entries(tempOv)) {
          const f = this.EDIT_FIELDS[fieldKey];
          if (!f) continue;
          const oldVal = this._txnFieldValue(t, fieldKey);
          if (newVal === oldVal) {
            // KEEP-sentinel: a per-row override equal to the current value means
            // "don't change this row" — cancel any bucket edit for this field.
            delete payload[f.apiKey];
          } else if (newVal !== undefined && newVal !== '') {
            payload[f.apiKey] = newVal;
          }
        }
      }
      if (Object.keys(payload).length) out.push({id: t.id, txn: t, payload});
    }
    return out;
  },

  confirmEdit() {
    const ids = this._selectedIds();
    if (!ids.length)                    { alert('Nenhuma transação selecionada.'); return; }
    if (!Object.keys(this._edits).length && !Object.keys(this._tempEdits).length){
      alert('Configure ao menos um campo para editar (em "Configurar edição" ou edição inline na célula).');
      return;
    }

    const patches = this._computeEditPatches();
    if (!patches.length) {
      alert('Os mapeamentos atuais não geram nenhuma alteração efetiva nas transações selecionadas.');
      return;
    }

    const fieldGroups = new Map();
    for (const p of patches) {
      for (const apiKey of Object.keys(p.payload)) {
        const fieldKey = Object.keys(this.EDIT_FIELDS).find(k => this.EDIT_FIELDS[k].apiKey === apiKey);
        const f = this.EDIT_FIELDS[fieldKey];
        const oldVal = this._txnFieldValue(p.txn, fieldKey);
        const newVal = p.payload[apiKey];
        const oldLbl = this._txnFieldLabel(p.txn, fieldKey);
        const newLbl = this._labelForValue(fieldKey, newVal);
        const key = `${fieldKey}::${oldVal}::${newVal}`;
        const grp = fieldGroups.get(key) || {field: f.label, oldLbl, newLbl, count: 0};
        grp.count++;
        fieldGroups.set(key, grp);
      }
    }
    const summaryRows = Array.from(fieldGroups.values())
      .sort((a, b) => a.field.localeCompare(b.field, 'pt-BR'))
      .map(g => `<tr>
          <td>${this._escape(g.field)}</td>
          <td>${g.oldLbl === '' ? '<span class="text-gray-400 italic">(vazio)</span>' : this._escape(g.oldLbl)}</td>
          <td>→ ${this._escape(g.newLbl)}</td>
          <td class="text-right">${g.count}</td>
        </tr>`).join('');

    const detailRows = patches.map(p => {
      const cells = Object.entries(p.payload).map(([apiKey, newVal]) => {
        const fieldKey = Object.keys(this.EDIT_FIELDS).find(k => this.EDIT_FIELDS[k].apiKey === apiKey);
        const f = this.EDIT_FIELDS[fieldKey];
        const oldLbl = this._txnFieldLabel(p.txn, fieldKey);
        const newLbl = this._labelForValue(fieldKey, newVal);
        return `${this._escape(f.label)}: ${this._escape(oldLbl || '∅')} → ${this._escape(newLbl)}`;
      }).join(' · ');
      return `<tr>
        <td>${this._escape(p.txn.liquidationDate)}</td>
        <td>${this._escape(p.txn.walletName)}</td>
        <td>${this._escape(cells)}</td>
      </tr>`;
    }).join('');

    document.getElementById('i-edit-confirm-body').innerHTML = `
      <div class="text-[11px] text-gray-600 mb-2">
        <strong>${patches.length}</strong> de ${this._selectedIds().length} transações
        selecionadas serão modificadas. Linhas não afetadas (sem alteração efetiva)
        são silenciosamente ignoradas.
      </div>
      <h4 class="text-[12px] font-semibold mt-2 mb-1">Resumo por campo</h4>
      <table class="txn-table mb-3"><thead>
        <tr><th>Campo</th><th>De</th><th>Para</th><th class="text-right">Qtd</th></tr>
      </thead><tbody>${summaryRows}</tbody></table>
      <h4 class="text-[12px] font-semibold mt-2 mb-1">Detalhe por transação</h4>
      <table class="txn-table"><thead>
        <tr><th>Data liquid.</th><th>Wallet</th><th>Alterações</th></tr>
      </thead><tbody>${detailRows}</tbody></table>`;

    this._pendingEditPatches = patches;
    document.getElementById('i-edit-confirm-modal').classList.add('show');
  },

  cancelEditConfirm() {
    document.getElementById('i-edit-confirm-modal').classList.remove('show');
    this._pendingEditPatches = null;
  },

  _labelForValue(fieldKey, value) {
    const f = this.EDIT_FIELDS[fieldKey];
    if (!f) return value;
    if (f.kind === 'entity') {
      const e = this._entities.find(x => x.id === value);
      return e ? e.name : value;
    }
    if (f.kind === 'security') {
      const s = this._securities.find(x => x.id === value);
      return s ? s.name : value;
    }
    return value;
  },

  async runEdit() {
    const patches = this._pendingEditPatches || [];
    document.getElementById('i-edit-confirm-modal').classList.remove('show');
    if (!patches.length) return;
    let ok = 0, fail = 0;
    showBusy(`Editando 0/${patches.length}…`);
    try {
      for (let i = 0; i < patches.length; i++) {
        showBusy(`Editando ${i + 1}/${patches.length}…`);
        const r = await api('PATCH', `/api/beehus/transactions/${patches[i].id}`, patches[i].payload);
        if (r.ok) ok++; else fail++;
      }
    } finally {
      hideBusy();
    }
    Token.refresh();
    alert(`Concluído: ${ok} editadas, ${fail} falhas. Veja o Log para detalhes.`);
    this._pendingEditPatches = null;
    this._edits = {};
    this._tempEdits = {};
    this._inlineEditField = null;
    this._renderEditChips();
    if (ok) await this.search();
  },

  // ── Delete ────────────────────────────────────────────────────────────────
  confirmDelete() {
    const ids = this._selectedIds();   // visible ∩ selected — só estas serão excluídas
    if (!ids.length) { alert('Nenhuma transação selecionada (no filtro atual).'); return; }
    // Selected rows hidden by the current filter are NOT deleted; surface the
    // count so a filtered selection never surprises the operator.
    const hidden = this._selSet.size - ids.length;
    document.getElementById('i-confirm-count').textContent =
      ids.length + (hidden > 0 ? ` (${hidden} selecionada(s) fora do filtro não serão excluídas)` : '');
    document.getElementById('i-confirm-delete-modal').classList.add('show');
  },
  cancelDelete() { document.getElementById('i-confirm-delete-modal').classList.remove('show'); },

  async runDelete() {
    const ids = this._selectedIds();
    this.cancelDelete();
    if (!ids.length) return;
    let ok = 0, fail = 0;
    showBusy(`Excluindo 0/${ids.length}…`);
    try {
      for (let i = 0; i < ids.length; i++) {
        showBusy(`Excluindo ${i + 1}/${ids.length}…`);
        const r = await api('DELETE', `/api/beehus/transactions/${ids[i]}`);
        if (r.ok) ok++; else fail++;
      }
    } finally {
      hideBusy();
    }
    Token.refresh();
    alert(`Concluído: ${ok} excluídas, ${fail} falhas. Veja o Log para detalhes.`);
    const idSet = new Set(ids);
    this._txns = this._txns.filter(t => !idSet.has(t.id));
    for (const id of ids) { delete this._tempEdits[id]; delete this._suggestions[id]; delete this._execEdits[id]; this._selSet.delete(id); }
    // Drop the active inline-edit column and re-render from the pruned _txns so
    // counters/chips/selection stay consistent (the deleted rows were the
    // checked ones; the remaining rows keep their prior checked state via
    // _render's selection-preservation). Avoids leaving a stale inline column
    // or selection referencing just-deleted ids.
    this._inlineEditField = null;
    this._render();
  },

  reset() {
    document.getElementById('i-company').value = '';
    document.getElementById('i-initialDate').value = '';
    document.getElementById('i-finalDate').value = '';
    this.onModeChange('single');
    document.querySelector('input[name="i-identified"][value="false"]').checked = true;
    this._groupings = [];
    this._groupingSelectedIds = new Set();
    this._wallets = [];
    this._walletSelectedIds = new Set();
    this._entities = [];
    this._entityIds = new Set();
    this._securities = [];
    this._securityIds = new Set();
    this._typeIds = new Set();
    this._txns = [];
    this._suggestions = {};
    this._execEdits = {};
    this._edits = {};
    this._tempEdits = {};
    this._inlineEditField = null;
    this._editingField = null;
    this._selSet = new Set();
    this._descFilter = '';
    const descInput = document.getElementById('i-desc-filter');
    if (descInput) descInput.value = '';
    this._typeFilter = '';
    const typeFilterEl = document.getElementById('i-type-filter');
    if (typeFilterEl) typeFilterEl.value = '';
    this._renderTypes();
    this._renderEntities();
    this._renderSecurities();
    this._renderGroupingPanes();
    this._renderPanes();
    const editAdd = document.getElementById('i-edit-field-add');
    if (editAdd) editAdd.value = '';
    this._renderEditChips();
    document.getElementById('i-result').classList.add('hidden');
    document.getElementById('i-search-summary').textContent = '';
    // Collapse the optional advanced cards back to their default hidden state.
    document.getElementById('i-card-groupings').classList.add('hidden');
    document.getElementById('i-card-wallets').classList.add('hidden');
    const advBtn = document.getElementById('i-toggle-advanced');
    if (advBtn) advBtn.textContent = '+ Groupings & Wallets';
  },
};

/* ════════════════════════════════════════════════════════════════════════════
   Delete-provisions — search local Mongo by company + liquidationDate range,
   pick rows with checkboxes, fire DELETE per id.
═════════════════════════════════════════════════════════════════════════════ */
