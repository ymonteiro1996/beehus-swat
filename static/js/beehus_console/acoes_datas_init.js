/* Beehus Console — DeleteProv, UnpublishNav, DateUtils, augments e init.
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
const DeleteProv = {
  _inited: false,
  _items: [],

  async init() {
    const r = await api('GET', '/api/beehus/filters/companies');
    const el = document.getElementById('dpr-company');
    el.innerHTML = '<option value="">(selecione)</option>' +
      (r.body || []).map(c => `<option value="${c.id}">${this._escape(c.name)}</option>`).join('');
  },

  _escape(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },

  async search() {
    const cid = document.getElementById('dpr-company').value;
    const ini = document.getElementById('dpr-initialDate').value;
    const fin = document.getElementById('dpr-finalDate').value;
    if (!cid)         { alert('Selecione uma empresa.'); return; }
    if (!ini || !fin) { alert('Informe data inicial e final.'); return; }
    if (ini > fin)    { alert('Data inicial não pode ser maior que a final.'); return; }

    const r = await api('POST', '/api/beehus/provisions/search', {
      companyId: cid, initialDate: ini, finalDate: fin,
    });
    if (!r.ok) { alert('Falha na busca. Veja o Log.'); return; }
    this._items = r.body?.provisions || [];
    this._render(r.body?.truncated);

    // Auto-collapse the filter card so the result table can breathe.
    toggleCard('dpr-card-filters', true);
    const companyName = document.getElementById('dpr-company').selectedOptions[0]?.textContent || '';
    document.getElementById('dpr-search-summary').textContent =
      `${companyName} · ${ini} → ${fin} · ${this._items.length} provisão(ões)`;
  },

  expandFilters() { toggleCard('dpr-card-filters', false); },

  _render(truncated) {
    document.getElementById('dpr-result').classList.remove('hidden');
    document.getElementById('dpr-result-count').textContent = this._items.length;
    document.getElementById('dpr-result-truncated').classList.toggle('hidden', !truncated);

    const fmt = (n) => n == null ? '' : Number(n).toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const tbody = document.getElementById('dpr-rows');
    tbody.innerHTML = this._items.map(p => `
      <tr>
        <td><input type="checkbox" class="dpr-row" data-id="${p.id}" checked onchange="DeleteProv.updateSelected()"></td>
        <td>${this._escape(p.liquidationDate)}</td>
        <td>${this._escape(p.initialDate)}</td>
        <td>${this._escape(p.walletName)}</td>
        <td>${this._escape(p.securityName)}</td>
        <td>${this._escape(p.provisionType)}</td>
        <td>${this._escape(p.provisionSource)}</td>
        <td class="text-right">${fmt(p.balance)}</td>
        <td>${this._escape(p.description)}</td>
      </tr>
    `).join('');
    document.getElementById('dpr-select-all').checked = true;
    this.updateSelected();
  },

  toggleAll(checked) {
    document.querySelectorAll('.dpr-row').forEach(cb => cb.checked = checked);
    document.getElementById('dpr-select-all').checked = checked;
    this.updateSelected();
  },

  updateSelected() {
    const n = document.querySelectorAll('.dpr-row:checked').length;
    document.getElementById('dpr-selected-count').textContent = n;
  },

  _selectedIds() {
    return Array.from(document.querySelectorAll('.dpr-row:checked')).map(cb => cb.dataset.id);
  },

  confirmDelete() {
    const ids = this._selectedIds();
    if (!ids.length) { alert('Nenhuma provisão selecionada.'); return; }
    document.getElementById('dpr-confirm-count').textContent = ids.length;
    document.getElementById('dpr-confirm-modal').classList.add('show');
  },
  cancelConfirm() { document.getElementById('dpr-confirm-modal').classList.remove('show'); },

  async runDelete() {
    const ids = this._selectedIds();
    this.cancelConfirm();
    let ok = 0, fail = 0;
    for (const id of ids) {
      const r = await api('DELETE', `/api/beehus/provisions/${id}`);
      if (r.ok) ok++; else fail++;
    }
    Token.refresh();
    alert(`Concluído: ${ok} excluídas, ${fail} falhas. Veja o Log para detalhes.`);
    document.querySelectorAll('.dpr-row').forEach(cb => {
      const tr = cb.closest('tr');
      if (cb.checked) tr.remove();
    });
    this.updateSelected();
  },

  reset() {
    document.getElementById('dpr-company').value = '';
    document.getElementById('dpr-initialDate').value = '';
    document.getElementById('dpr-finalDate').value = '';
    document.getElementById('dpr-result').classList.add('hidden');
    document.getElementById('dpr-search-summary').textContent = '';
    this.expandFilters();
    this._items = [];
  },
};

/* ════════════════════════════════════════════════════════════════════════════
   Delete-positions section — mirrors Processar Posições (process-dates) so the
   operator can excluir posições for a single date OR over a faixa de datas
   (one DELETE per business day). In single-date mode the "Disponíveis" wallet
   pane is restricted to wallets that actually have a `processedPosition` on
   that date (debounced eligibility lookup); in range mode the pane shows the
   full company wallet list and the upstream resolves eligibility per day.
═════════════════════════════════════════════════════════════════════════════ */
const DeletePos = makeDatesPipeline({
  prefix:     'dp',
  kind:       'wallets',
  endpoint:   '/api/beehus/positions/delete',
  payloadKey: 'walletIds',
  stepLabel:  'Excluir Posições',
});

(function _augmentDeletePos() {
  const $$ = (s) => document.getElementById(`dp-${s}`);
  const _origResolveDays    = DeletePos._resolveDays.bind(DeletePos);
  const _origReset          = DeletePos.reset.bind(DeletePos);
  const _origConfirm        = DeletePos.confirm.bind(DeletePos);
  const _origRun            = DeletePos.run.bind(DeletePos);
  const _origRenderPanes    = DeletePos._renderPanes.bind(DeletePos);
  const _origOnCompanyChg   = DeletePos.onCompanyChange.bind(DeletePos);
  const _origOnRangeChange  = DeletePos.onRangeChange.bind(DeletePos);

  Object.assign(DeletePos, {
    _mode: 'single',
    // Multi-select grouping picker (mirrors ProcessDates).
    _groupingSelectedIds: new Set(),
    // Wallet ids with a `processedPosition` for the current single-mode date.
    // null = unknown (no date or in range mode).
    _eligibleIds: null,
    _dateTimer: null,

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        const fd = $$('finalDate'); if (fd) fd.value = '';
        // Refresh eligibility for whatever date is currently in the initial
        // input — single-mode narrows the wallets pane to wallets that have
        // a processedPosition.
        clearTimeout(this._dateTimer);
        this._dateTimer = setTimeout(() => {
          this._refreshEligibility().then(() => this._renderPanes());
        }, 0);
      } else {
        // Range mode: drop the eligibility filter — wallets pane shows all
        // wallets of the company; per-day eligibility is resolved upstream.
        this._eligibleIds = null;
        this._renderPanes();
      }
    },

    // Wraps the factory's onRangeChange (which recomputes _candidateDates) so
    // a typed date in single-mode also kicks off the eligibility refresh.
    onRangeChange() {
      _origOnRangeChange();
      if (this._mode === 'single') {
        clearTimeout(this._dateTimer);
        this._dateTimer = setTimeout(() => {
          this._refreshEligibility().then(() => this._renderPanes());
        }, 500);
      }
    },

    async _refreshEligibility() {
      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      if (!cid || !dt) { this._eligibleIds = null; return; }
      const url = `/api/beehus/filters/wallets-with-position` +
                  `?companyId=${encodeURIComponent(cid)}` +
                  `&positionDate=${encodeURIComponent(dt)}`;
      const r = await api('GET', url);
      this._eligibleIds = new Set((r.body || []).map(w => w.id));
      // Drop explicit selections that are no longer eligible.
      for (const id of Array.from(this._selectedIds)) {
        if (!this._eligibleIds.has(id)) this._selectedIds.delete(id);
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    // ── Groupings picker (dual-pane transfer) ──────────────────────────────
    _walletFilterFromGroupings() {
      if (!this._groupingSelectedIds || !this._groupingSelectedIds.size) return null;
      const ids = new Set();
      for (const g of (this._groupings || [])) {
        if (this._groupingSelectedIds.has(g.id)) {
          (g.walletIds || []).forEach(w => ids.add(w));
        }
      }
      return ids;
    },

    _renderGroupingPanes() {
      const avail = $$('grp-available');
      const sel   = $$('grp-selected');
      if (!avail || !sel) return;
      const all = this._groupings || [];
      const available = all.filter(g => !this._groupingSelectedIds.has(g.id));
      const selected  = all.filter(g =>  this._groupingSelectedIds.has(g.id));
      avail.innerHTML = available.map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      sel.innerHTML   = selected .map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      $$('grp-available-count').textContent = available.length;
      $$('grp-selected-count').textContent  = selected.length;
      // Wallet availability depends on the grouping selection (union filter).
      this._renderPanes();
    },

    _highlightedGrp(suffix) {
      const el = $$(`grp-${suffix}`);
      return el ? Array.from(el.selectedOptions).map(o => o.value) : [];
    },
    addGroupingSelected() {
      this._highlightedGrp('available').forEach(id => this._groupingSelectedIds.add(id));
      this._renderGroupingPanes();
    },
    addGroupingAll() {
      Array.from($$('grp-available').options)
        .forEach(o => { if (o.value) this._groupingSelectedIds.add(o.value); });
      this._renderGroupingPanes();
    },
    removeGroupingSelected() {
      this._highlightedGrp('selected').forEach(id => this._groupingSelectedIds.delete(id));
      this._renderGroupingPanes();
    },
    removeGroupingAll() {
      this._groupingSelectedIds.clear();
      this._renderGroupingPanes();
    },

    async onGrpExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = $$('grp-excel-status');
      if (statusEl) statusEl.textContent = 'Processando…';
      if (!(this._groupings || []).length) {
        if (statusEl) statusEl.textContent = 'selecione uma empresa primeiro';
        alert('Selecione uma empresa antes de subir os IDs.');
        ev.target.value = '';
        return;
      }
      const known = new Set(this._groupings.map(g => g.id));
      const r = await _excelToMatchedIds(file, known);
      ev.target.value = '';
      if (!r.ok) {
        if (statusEl) statusEl.textContent = `falha: ${r.error}`;
        alert('Falha ao ler o Excel: ' + r.error);
        return;
      }
      if (!r.total) {
        if (statusEl) statusEl.textContent = 'nenhum ID encontrado';
        return;
      }
      let added = 0;
      r.matched.forEach(id => {
        if (!this._groupingSelectedIds.has(id)) { this._groupingSelectedIds.add(id); added++; }
      });
      if (statusEl) statusEl.textContent =
        `${added} grouping(s) adicionado(s)` +
        (r.unmatched ? ` · ${r.unmatched} ID(s) ignorado(s)` : '');
      this._renderGroupingPanes();
    },

    // Inject groupings-derived filter (and, in single-mode, the date
    // eligibility set) into _filterIds before delegating to the shared
    // factory render. The `dp-no-date` badge reports an empty eligibility
    // set in single mode.
    _renderPanes() {
      const grpFilter = this._walletFilterFromGroupings();
      if (this._mode === 'single' && this._eligibleIds !== null) {
        // Intersect grouping union with eligibility.
        const intersect = new Set();
        for (const w of (this._wallets || [])) {
          if (this._eligibleIds.has(w.id) && (!grpFilter || grpFilter.has(w.id))) {
            intersect.add(w.id);
          }
        }
        this._filterIds = intersect;
      } else {
        this._filterIds = grpFilter;
      }
      _origRenderPanes();
      // Restore the "grouping" badge meaning — the factory's render hides it
      // whenever _filterIds is falsy, but in single mode we always overwrite
      // _filterIds. Toggle by whether the user actually picked groupings.
      const fb = $$('available-filter');
      if (fb) fb.classList.toggle('hidden', !grpFilter);
      // The "selecione data" badge only makes sense in single mode (range mode
      // doesn't pre-filter by eligibility).
      const noDate = $$('no-date');
      if (noDate) {
        const show = (this._mode === 'single' && this._eligibleIds === null);
        noDate.classList.toggle('hidden', !show);
      }
    },

    async onCompanyChange() {
      // Clear groupings selection before the factory refetches, otherwise
      // stale ids would survive across companies.
      this._groupingSelectedIds = new Set();
      this._eligibleIds = null;
      const statusEl = $$('grp-excel-status'); if (statusEl) statusEl.textContent = '';
      await _origOnCompanyChg();
      if (this._mode === 'single') await this._refreshEligibility();
      this._renderGroupingPanes();
    },

    _effectiveSelectedWallets() {
      // Explicit wallet selection wins; otherwise fall back to the union of
      // walletIds across selected groupings. In single mode the result is
      // intersected with the date-eligibility set so we don't ask upstream
      // to delete from wallets that don't have a position. Both empty →
      // empty set (= upstream "all wallets in company" contract).
      if (this._selectedIds && this._selectedIds.size) return new Set(this._selectedIds);
      const union = this._walletFilterFromGroupings();
      if (!union) return new Set();
      if (this._mode === 'single' && this._eligibleIds !== null) {
        const out = new Set();
        union.forEach(w => { if (this._eligibleIds.has(w)) out.add(w); });
        return out;
      }
      return new Set(union);
    },

    _confirmWalletsLabel() {
      const eff = this._effectiveSelectedWallets();
      if (!eff.size) {
        return this._mode === 'single'
          ? 'todas as wallets elegíveis da empresa para essa data'
          : 'todas as wallets';
      }
      const fromGroupings =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;
      return fromGroupings
        ? `${eff.size} wallet(s) via ${this._groupingSelectedIds.size} grouping(s)`
        : `${eff.size} selecionada(s)`;
    },

    confirm() {
      if (this._mode !== 'single') {
        _origConfirm();
        // The factory's confirm() wrote "X selecionada(s) / todas as wallets"
        // using _selectedIds only; overwrite with the groupings-aware label.
        const cw = $$('confirm-wallets');
        if (cw && $$('confirm-modal').classList.contains('show')) {
          cw.textContent = this._confirmWalletsLabel();
        }
        return;
      }
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      $$('confirm-wallets').textContent = this._confirmWalletsLabel();
      $$('confirm-modal').classList.add('show');
    },

    // When wallets are empty but groupings are selected, substitute the union
    // of grouping walletIds (∩ eligibility in single mode) into _selectedIds
    // for the duration of the upstream call so each per-day POST restricts to
    // those wallets. After the run, refresh single-mode eligibility so the
    // wallets pane reflects what was just deleted.
    async run() {
      const needsSubstitution =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;

      const finalize = async () => {
        if (this._mode === 'single') {
          await this._refreshEligibility();
          this._renderPanes();
        }
      };

      if (!needsSubstitution) {
        await _origRun();
        await finalize();
        return;
      }

      const effective = this._effectiveSelectedWallets();
      if (!effective.size) {
        await _origRun();
        await finalize();
        return;
      }
      const saved = this._selectedIds;
      this._selectedIds = effective;
      try { await _origRun(); }
      finally { this._selectedIds = saved; }
      await finalize();
    },

    reset() {
      _origReset();
      clearTimeout(this._dateTimer);
      this._groupingSelectedIds = new Set();
      this._eligibleIds = null;
      const ge = $$('grp-excel-status'); if (ge) ge.textContent = '';
      this._renderGroupingPanes();
      this._mode = 'single';
      const radios = document.querySelectorAll('input[name="dp-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('dp-finalDate-wrap')) {
        DeletePos.onModeChange('single');
        DeletePos._renderGroupingPanes();
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

/* ════════════════════════════════════════════════════════════════════════════
   Explosion-proportions — same shape as NavGroupingsDates: single-date /
   range-of-dates toggle, optional explicit-dates picker, grouping picker
   (kind='groupings'). Per day calls POST /api/beehus/nav/explosion-proportions
   with the selected groupings (or [] = "all groupings of the company").
═════════════════════════════════════════════════════════════════════════════ */
const ExplosionProp = makeDatesPipeline({
  prefix:     'ep',
  kind:       'groupings',
  endpoint:   '/api/beehus/nav/explosion-proportions',
  payloadKey: 'groupings',
  stepLabel:  'Proporcionalizar Explosão',
});

(function _augmentExplosionProp() {
  const $$ = (s) => document.getElementById(`ep-${s}`);
  const _origResolveDays = ExplosionProp._resolveDays.bind(ExplosionProp);
  const _origReset       = ExplosionProp.reset.bind(ExplosionProp);
  const _origConfirm     = ExplosionProp.confirm.bind(ExplosionProp);

  Object.assign(ExplosionProp, {
    _mode: 'single',

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        const fd = $$('finalDate'); if (fd) fd.value = '';
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    confirm() {
      if (this._mode !== 'single') return _origConfirm();
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      const ids = Array.from(this._selectedIds);
      $$('confirm-groupings').textContent = ids.length
        ? `${ids.length} selecionado(s)`
        : 'todos os groupings';
      $$('confirm-modal').classList.add('show');
    },

    reset() {
      _origReset();
      this._mode = 'single';
      const radios = document.querySelectorAll('input[name="ep-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('ep-finalDate-wrap')) {
        ExplosionProp.onModeChange('single');
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

/* ════════════════════════════════════════════════════════════════════════════
   Unpublish-NAV — only groupings with `navPackages.published=true` for the
   chosen company+date are listed in "Disponíveis".
═════════════════════════════════════════════════════════════════════════════ */
const UnpublishNav = {
  _inited: false,
  _groupings: [],
  _selectedIds: new Set(),
  _dateTimer: null,

  async init() {
    const r = await api('GET', '/api/beehus/filters/companies');
    this._fillSelect('un-company', r.body || [], '(selecione)');
  },

  _fillSelect(id, items, placeholder) {
    const el = document.getElementById(id);
    el.innerHTML = `<option value="">${placeholder}</option>` +
      items.map(i => `<option value="${i.id}">${this._escape(i.name)}</option>`).join('');
  },
  _escape(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },

  async onCompanyChange() {
    this._groupings = [];
    this._selectedIds = new Set();
    await this._refreshEligibility();
    this._renderPanes();
  },

  onDateChange() {
    clearTimeout(this._dateTimer);
    this._dateTimer = setTimeout(() => this._applyDateChange(), 500);
  },
  async _applyDateChange() {
    await this._refreshEligibility();
    const eligibleIds = new Set(this._groupings.map(g => g.id));
    for (const id of Array.from(this._selectedIds)) {
      if (!eligibleIds.has(id)) this._selectedIds.delete(id);
    }
    this._renderPanes();
  },

  async _refreshEligibility() {
    const cid = document.getElementById('un-company').value;
    const dt  = document.getElementById('un-positionDate').value;
    if (!cid || !dt) { this._groupings = []; return; }
    const url = `/api/beehus/filters/groupings-by-publish-state` +
                `?companyId=${encodeURIComponent(cid)}` +
                `&positionDate=${encodeURIComponent(dt)}` +
                `&published=true`;
    const r = await api('GET', url);
    this._groupings = r.body || [];
  },

  _renderPanes() {
    const avail = document.getElementById('un-available');
    const sel   = document.getElementById('un-selected');
    const available = this._groupings.filter(g => !this._selectedIds.has(g.id));
    const selected  = this._groupings.filter(g => this._selectedIds.has(g.id));

    avail.innerHTML = available
      .map(g => `<option value="${g.id}">${this._escape(g.name)}</option>`).join('');
    sel.innerHTML = selected
      .map(g => `<option value="${g.id}">${this._escape(g.name)}</option>`).join('');

    document.getElementById('un-available-count').textContent = available.length;
    document.getElementById('un-selected-count').textContent  = selected.length;

    const cid = document.getElementById('un-company').value;
    const dt  = document.getElementById('un-positionDate').value;
    document.getElementById('un-no-date').classList.toggle('hidden', !!(cid && dt));
  },

  _highlighted(id) {
    return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
  },
  addSelected()    { this._highlighted('un-available').forEach(id => this._selectedIds.add(id));   this._renderPanes(); },
  addAll()         { Array.from(document.getElementById('un-available').options)
                        .forEach(o => this._selectedIds.add(o.value));                              this._renderPanes(); },
  removeSelected() { this._highlighted('un-selected').forEach(id => this._selectedIds.delete(id)); this._renderPanes(); },
  removeAll()      { this._selectedIds.clear();                                                     this._renderPanes(); },

  // Bulk-upload an .xlsx of groupingIds (one per cell, no header). Only ids
  // that are currently published for the chosen company+date end up in
  // "Selecionadas" — anything else is reported as ignored.
  async onExcelChosen(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    const status = document.getElementById('un-excel-status');
    status.textContent = 'Processando…';
    if (!this._groupings.length) {
      status.textContent = 'selecione empresa e data primeiro';
      alert('Selecione empresa e data antes de subir os IDs.');
      ev.target.value = '';
      return;
    }
    const known = new Set(this._groupings.map(g => g.id));
    const r = await _excelToMatchedIds(file, known);
    ev.target.value = '';
    if (!r.ok) {
      status.textContent = `falha: ${r.error}`;
      alert('Falha ao ler o Excel: ' + r.error);
      return;
    }
    let added = 0;
    r.matched.forEach(id => {
      if (!this._selectedIds.has(id)) { this._selectedIds.add(id); added++; }
    });
    status.textContent =
      `${added} grouping(s) adicionado(s)` +
      (r.unmatched ? ` · ${r.unmatched} ID(s) ignorado(s)` : '');
    this._renderPanes();
  },

  confirm() {
    const cid = document.getElementById('un-company').value;
    const dt  = document.getElementById('un-positionDate').value;
    if (!cid) { alert('Selecione uma empresa.'); return; }
    if (!dt)  { alert('Informe a data da posição.'); return; }

    const groupings = Array.from(this._selectedIds);
    const companyName = document.getElementById('un-company').selectedOptions[0]?.textContent || cid;
    document.getElementById('un-confirm-company').textContent = companyName;
    document.getElementById('un-confirm-date').textContent    = dt;
    document.getElementById('un-confirm-groupings').textContent =
      groupings.length
        ? `${groupings.length} selecionado(s)`
        : 'todos os agrupamentos publicados da empresa';
    document.getElementById('un-confirm-modal').classList.add('show');
  },
  cancelConfirm() { document.getElementById('un-confirm-modal').classList.remove('show'); },

  async run() {
    this.cancelConfirm();
    const payload = {
      companyId:    document.getElementById('un-company').value,
      positionDate: document.getElementById('un-positionDate').value,
      groupingIds:  Array.from(this._selectedIds),
    };
    const status  = document.getElementById('un-status');
    const resBox  = document.getElementById('un-result');
    const resBody = document.getElementById('un-result-body');
    status.textContent = 'Despublicando…';
    resBox.classList.add('hidden');

    const r = await api('POST', '/api/beehus/nav/unpublish', payload);
    Token.refresh();
    if (!r.ok) {
      status.textContent = 'Falha — veja o Log.';
      alert('Falha ao despublicar agrupamentos. Veja o Log para detalhes.');
      return;
    }
    status.textContent = 'Despublicado.';
    resBody.textContent = typeof r.body === 'string' ? r.body : JSON.stringify(r.body, null, 2);
    resBox.classList.remove('hidden');

    // Refresh eligibility so just-unpublished groupings disappear from the picker.
    await this._refreshEligibility();
    const eligibleIds = new Set(this._groupings.map(g => g.id));
    for (const id of Array.from(this._selectedIds)) {
      if (!eligibleIds.has(id)) this._selectedIds.delete(id);
    }
    this._renderPanes();
  },

  reset() {
    clearTimeout(this._dateTimer);
    document.getElementById('un-company').value = '';
    document.getElementById('un-positionDate').value = '';
    this._groupings = [];
    this._selectedIds = new Set();
    this._renderPanes();
    document.getElementById('un-status').textContent = '';
    document.getElementById('un-result').classList.add('hidden');
  },
};


/* ════════════════════════════════════════════════════════════════════════════
   Single-step pipelines by dates — builder shared by Processar / NAV Wallets /
   NAV Groupings / Publicar Agrupamentos. Each instance iterates business days
   (or an explicit date list) and fires one upstream call per day.

   Kinds:
     'wallets'    — wallet picker + optional grouping filter; payload list is
                    selected wallet ids (empty = all wallets in company).
     'groupings'  — grouping picker; payload list is selected grouping ids
                    (empty = all groupings in company).
     'publish'    — no picker; per day, looks up unpublished groupings and
                    sends those as the payload. Days with nothing eligible
                    are marked skipped (—) and the loop continues.
═════════════════════════════════════════════════════════════════════════════ */
const _DateUtils = {
  parseLocalDate(s) {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  },
  fmt(d) {
    const yy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yy}-${mm}-${dd}`;
  },
  businessDays(iniStr, finStr) {
    const out = [];
    const cur = this.parseLocalDate(iniStr);
    const end = this.parseLocalDate(finStr);
    while (cur <= end) {
      const dow = cur.getDay();
      if (dow !== 0 && dow !== 6) out.push(this.fmt(cur));
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  },
  isMonthEndBusinessDay(dateStr) {
    const d = this.parseLocalDate(dateStr);
    const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
    while (last.getDay() === 0 || last.getDay() === 6) {
      last.setDate(last.getDate() - 1);
    }
    return d.getFullYear() === last.getFullYear()
        && d.getMonth()    === last.getMonth()
        && d.getDate()     === last.getDate();
  },
};

function _escDP(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Shared Excel-upload helper used by every picker view that supports bulk
// loading of ids from a .xlsx (one id per cell, no header). Resolves to
// `{ok, total, matched, unmatched (count), unmatchedIds (array), error?}`
// with `matched` already filtered against `knownIdSet` so the caller just
// iterates and pushes into its own `_selectedIds` set.
async function _excelToMatchedIds(file, knownIdSet) {
  const fd = new FormData();
  fd.append('file', file);
  const t0 = performance.now();
  let status = 0, body = null;
  try {
    const r = await fetch('/api/beehus/util/parse-strings-excel', {method: 'POST', body: fd});
    status = r.status;
    body   = await r.json().catch(() => null);
    ApiLog.add('POST', '/api/beehus/util/parse-strings-excel', status, body, Math.round(performance.now() - t0));
    if (!r.ok) {
      return {ok: false, error: (body && body.error) || `HTTP ${r.status}`};
    }
    const inputIds     = (body && body.values) || [];
    const matched      = inputIds.filter(id => knownIdSet.has(id));
    const unmatchedIds = inputIds.filter(id => !knownIdSet.has(id));
    return {
      ok:           true,
      total:        inputIds.length,
      matched,
      unmatched:    unmatchedIds.length,   // numeric, for status text
      unmatchedIds,                         // array of unrecognized ids
    };
  } catch (e) {
    ApiLog.add('POST', '/api/beehus/util/parse-strings-excel', status, body || {error: String(e)},
               Math.round(performance.now() - t0));
    return {ok: false, error: String(e)};
  }
}

function makeDatesPipeline(cfg) {
  const px = cfg.prefix;
  const id = (s) => `${px}-${s}`;
  const $  = (s) => document.getElementById(id(s));
  const fillSelect = (s, items, placeholder, {disabled = false} = {}) => {
    const el = $(s);
    el.disabled = disabled;
    el.innerHTML = `<option value="">${placeholder}</option>` +
      items.map(i => `<option value="${i.id}">${_escDP(i.name)}</option>`).join('');
  };
  // cfg.endpoint / cfg.stepLabel / cfg.eligibilityPublished / cfg.actionVerbPast
  // may be either a literal value or a function that resolves at call time —
  // PublishDates uses functions so the same pipeline can flip between
  // /publish and /unpublish based on the operator-selected action.
  const _resolveCfg = (key, fallback) => {
    const v = cfg[key];
    if (typeof v === 'function') return v();
    return (v === undefined) ? fallback : v;
  };

  return {
    _inited: false,
    _running: false,
    _wallets: [],
    _groupings: [],
    _selectedIds: new Set(),
    _filterIds: null,
    _useDateList: false,
    _candidateDates: [],
    _selectedDates: new Set(),
    _filterMonthEnd: false,

    async init() {
      const r = await api('GET', '/api/beehus/filters/companies');
      fillSelect('company', r.body || [], '(selecione)');
      this._renderPanes();
      this._renderDatePanes();
    },

    async onCompanyChange() {
      const cid = $('company').value;
      this._wallets = [];
      this._groupings = [];
      this._selectedIds = new Set();
      this._filterIds = null;

      if (cfg.kind === 'wallets') {
        if (!cid) {
          fillSelect('grouping', [], 'Selecione uma empresa primeiro', {disabled: true});
          this._renderPanes();
          return;
        }
        const [groupings, wallets] = await Promise.all([
          api('GET', `/api/beehus/filters/groupings?companyId=${encodeURIComponent(cid)}`),
          api('GET', `/api/beehus/filters/wallets?companyId=${encodeURIComponent(cid)}`),
        ]);
        this._groupings = groupings.body || [];
        this._wallets   = wallets.body   || [];
        const placeholder = this._groupings.length ? '(nenhum)' : '(empresa sem agrupamentos)';
        fillSelect('grouping', this._groupings, placeholder, {disabled: false});
      } else if (cfg.kind === 'groupings') {
        if (cid) {
          const r = await api('GET', `/api/beehus/filters/groupings?companyId=${encodeURIComponent(cid)}`);
          this._groupings = r.body || [];
        }
      }
      // 'publish': no picker, per-day eligibility is fetched in run()

      this._renderPanes();
    },

    onGroupingChange() {
      if (cfg.kind !== 'wallets') return;
      const gid = $('grouping').value;
      if (!gid) { this._filterIds = null; this._renderPanes(); return; }
      const g = this._groupings.find(x => x.id === gid);
      this._filterIds = new Set((g && g.walletIds) || []);
      this._renderPanes();
    },

    _renderPanes() {
      if (cfg.kind === 'publish') return;
      const items     = (cfg.kind === 'wallets') ? this._wallets : this._groupings;
      const filterIds = (cfg.kind === 'wallets') ? this._filterIds : null;

      const available = items.filter(it =>
        (!filterIds || filterIds.has(it.id)) && !this._selectedIds.has(it.id));
      const selected  = items.filter(it => this._selectedIds.has(it.id));

      $('available').innerHTML = available
        .map(it => `<option value="${it.id}">${_escDP(it.name)}</option>`).join('');
      $('selected').innerHTML  = selected
        .map(it => `<option value="${it.id}">${_escDP(it.name)}</option>`).join('');

      $('available-count').textContent = available.length;
      $('selected-count').textContent  = selected.length;
      const fb = $('available-filter');
      if (fb) fb.classList.toggle('hidden', !filterIds);
    },

    _highlighted(suffix) {
      return Array.from($(suffix).selectedOptions).map(o => o.value);
    },
    addSelected() {
      if (cfg.kind === 'publish') return;
      this._highlighted('available').forEach(id => this._selectedIds.add(id));
      this._renderPanes();
    },
    addAll() {
      if (cfg.kind === 'publish') return;
      Array.from($('available').options).forEach(o => this._selectedIds.add(o.value));
      this._renderPanes();
    },
    removeSelected() {
      if (cfg.kind === 'publish') return;
      this._highlighted('selected').forEach(id => this._selectedIds.delete(id));
      this._renderPanes();
    },
    removeAll() {
      if (cfg.kind === 'publish') return;
      this._selectedIds.clear();
      this._renderPanes();
    },

    // ── Date pane ──────────────────────────────────────────────────────────
    onRangeChange() {
      const ini = $('initialDate').value;
      const fin = $('finalDate').value;
      this._candidateDates = (ini && fin && ini <= fin)
        ? _DateUtils.businessDays(ini, fin) : [];
      if (this._useDateList) this._renderDatePanes();
    },
    onFilterChange() {
      this._filterMonthEnd = $('filter-monthend').checked;
      this._renderDatePanes();
    },
    onUseDateListChange() {
      this._useDateList = $('use-date-list').checked;
      $('dates-body').classList.toggle('hidden', !this._useDateList);
      if (!this._useDateList) {
        this._selectedDates = new Set();
        this._filterMonthEnd = false;
        const me = $('filter-monthend'); if (me) me.checked = false;
        const xs = $('excel-status');    if (xs) xs.textContent = '';
      } else {
        this.onRangeChange();
      }
      this._renderDatePanes();
    },
    _renderDatePanes() {
      const avail = $('date-available');
      const sel   = $('date-selected');
      if (!avail || !sel) return;

      if (!this._useDateList) {
        avail.innerHTML = ''; sel.innerHTML = '';
        $('date-available-count').textContent = '0';
        $('date-selected-count').textContent  = '0';
        $('date-monthend-badge').classList.add('hidden');
        return;
      }

      let candidates = this._candidateDates;
      if (this._filterMonthEnd) {
        candidates = candidates.filter(d => _DateUtils.isMonthEndBusinessDay(d));
      }
      const available = candidates.filter(d => !this._selectedDates.has(d));
      const selected  = Array.from(this._selectedDates).sort();

      avail.innerHTML = available.map(d => `<option value="${d}">${d}</option>`).join('');
      sel.innerHTML   = selected.map(d  => `<option value="${d}">${d}</option>`).join('');

      $('date-available-count').textContent = available.length;
      $('date-selected-count').textContent  = selected.length;
      $('date-monthend-badge').classList.toggle('hidden', !this._filterMonthEnd);
    },

    _highlightedDates(suffix) {
      return Array.from($(suffix).selectedOptions).map(o => o.value);
    },
    addSelectedDates()    { this._highlightedDates('date-available').forEach(d => this._selectedDates.add(d)); this._renderDatePanes(); },
    addAllDates()         { Array.from($('date-available').options).forEach(o => this._selectedDates.add(o.value)); this._renderDatePanes(); },
    removeSelectedDates() { this._highlightedDates('date-selected').forEach(d => this._selectedDates.delete(d)); this._renderDatePanes(); },
    removeAllDates()      { this._selectedDates.clear(); this._renderDatePanes(); },

    async onExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = $('excel-status');
      statusEl.textContent = 'Processando…';

      const fd = new FormData();
      fd.append('file', file);
      const t0 = performance.now();
      let status = 0, body = null;
      try {
        const r = await fetch('/api/beehus/util/parse-dates-excel', {method: 'POST', body: fd});
        status = r.status;
        body   = await r.json().catch(() => null);
        if (!r.ok) {
          statusEl.textContent = `falha (${r.status})`;
          alert('Falha ao ler o Excel: ' + (body?.error || r.status));
          return;
        }
        const dates = body?.dates || [];
        if (!dates.length) { statusEl.textContent = 'nenhuma data encontrada'; return; }
        dates.forEach(d => this._selectedDates.add(d));
        this._renderDatePanes();
        statusEl.textContent = `${dates.length} data(s) adicionadas a "Selecionadas"`;
      } catch (e) {
        body = {error: String(e)};
        statusEl.textContent = 'erro de rede';
        alert('Erro de rede ao subir Excel: ' + e);
      } finally {
        ApiLog.add('POST', '/api/beehus/util/parse-dates-excel', status, body, Math.round(performance.now() - t0));
        ev.target.value = '';
      }
    },

    // Upload an .xlsx of ids (one per cell, no header). The semantic depends
    // on `cfg.kind`:
    //   kind 'wallets'   → input cells are walletIds; matched ones go to
    //                      "Selecionadas" directly.
    //   kind 'groupings' → input cells are groupingIds; matched ones go to
    //                      "Selecionadas" directly.
    //   kind 'publish'   → no-op (publish has no picker; PublishDates uses
    //                      its own picker logic in PublishDatesPicker).
    async onGroupingsExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = $('groupings-excel-status');
      if (cfg.kind === 'publish' || !statusEl) {
        ev.target.value = '';
        return;
      }
      statusEl.textContent = 'Processando…';

      const cid = $('company').value;
      const items = (cfg.kind === 'wallets') ? this._wallets : this._groupings;
      if (!cid || !items.length) {
        statusEl.textContent = 'selecione uma empresa primeiro';
        alert('Selecione uma empresa antes de subir os IDs.');
        ev.target.value = '';
        return;
      }

      const known = new Set(items.map(it => it.id));
      const r = await _excelToMatchedIds(file, known);
      ev.target.value = '';
      if (!r.ok) {
        statusEl.textContent = `falha: ${r.error}`;
        alert('Falha ao ler o Excel: ' + r.error);
        return;
      }
      if (!r.total) {
        statusEl.textContent = 'nenhum ID encontrado';
        return;
      }
      let added = 0;
      r.matched.forEach(id => {
        if (!this._selectedIds.has(id)) { this._selectedIds.add(id); added++; }
      });
      const noun = (cfg.kind === 'wallets') ? 'wallet' : 'grouping';
      statusEl.textContent =
        `${added} ${noun}(s) adicionada(s)` +
        (r.unmatched ? ` · ${r.unmatched} ID(s) ignorado(s)` : '');
      this._renderPanes();
    },

    _resolveDays() {
      if (this._useDateList && this._selectedDates.size > 0) {
        return Array.from(this._selectedDates).sort();
      }
      const ini = $('initialDate').value;
      const fin = $('finalDate').value;
      if (!ini || !fin || ini > fin) return [];
      return _DateUtils.businessDays(ini, fin);
    },

    confirm() {
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $('company').value;
      const ini = $('initialDate').value;
      const fin = $('finalDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }

      const explicit = this._useDateList && this._selectedDates.size > 0;
      if (!explicit) {
        if (!ini || !fin) { alert('Informe data inicial e final.'); return; }
        if (ini > fin)    { alert('Data inicial não pode ser maior que a final.'); return; }
      }

      const days = this._resolveDays();
      if (!days.length) {
        alert(this._useDateList
          ? 'Selecione pelo menos uma data ou desmarque "Selecionar datas específicas".'
          : 'Não há dias úteis na faixa informada.');
        return;
      }

      $('confirm-company').textContent = $('company').selectedOptions[0]?.textContent || cid;
      $('confirm-range').textContent   = explicit
        ? `${days[0]} … ${days[days.length - 1]} (lista explícita)`
        : `${ini} → ${fin}`;
      $('confirm-days').textContent    = explicit ? `${days.length} (selecionados)` : `${days.length}`;

      if (cfg.kind === 'wallets') {
        const ids = Array.from(this._selectedIds);
        $('confirm-wallets').textContent = ids.length ? `${ids.length} selecionada(s)` : 'todas as wallets';
      } else if (cfg.kind === 'groupings') {
        const ids = Array.from(this._selectedIds);
        $('confirm-groupings').textContent = ids.length ? `${ids.length} selecionado(s)` : 'todos os groupings';
      }

      $('confirm-modal').classList.add('show');

      // Preview (opcional) da cadeia de explosão: lista as carteiras que serão
      // arrastadas junto das selecionadas. Assíncrono — a expansão de verdade
      // acontece no servidor no /process; aqui é só para o operador confirmar
      // ciente. Só roda quando a pipeline pede (cfg.explosionPreview).
      if (cfg.explosionPreview && cfg.kind === 'wallets') {
        this._previewExplosion(cid, Array.from(this._selectedIds));
      }
    },
    cancelConfirm() { $('confirm-modal').classList.remove('show'); },

    async _previewExplosion(cid, walletIds) {
      const box  = $('confirm-explosion');
      const body = $('confirm-explosion-body');
      if (!box || !body) return;
      // Sem carteiras selecionadas = "todas": o servidor processa a empresa
      // inteira, nada a arrastar (as dependências já estão no lote).
      if (!walletIds.length) { box.classList.add('hidden'); return; }

      box.classList.remove('hidden');
      body.textContent = 'Verificando dependências…';
      const seq = (this._explosionSeq = (this._explosionSeq || 0) + 1);

      // Mapa de nomes: começa com o picker; enriquece com os nomes vindos da
      // cadeia (para o "(via <carteira>)" nos níveis > 1 mostrar nome real).
      const names = new Map((this._wallets || []).map(w => [w.id, w.name]));
      const nameOf = (id) => names.get(id) || id;

      try {
        const results = await Promise.all(walletIds.map(async (wid) => {
          const r = await api('GET',
            `/api/beehus/positions/explosion-chain` +
            `?companyId=${encodeURIComponent(cid)}` +
            `&walletId=${encodeURIComponent(wid)}`);
          return {wid, chain: (r.ok && r.body && r.body.chain) || []};
        }));
        if (seq !== this._explosionSeq) return;   // modal reaberto/cancelado

        for (const {chain} of results) {
          for (const p of chain) if (p.name) names.set(p.walletId, p.name);
        }

        // Dedup por walletId arrastado (uma carteira pode ser arrastada por
        // várias raízes ou por vários ativos) — mostra a hierarquia por raiz.
        const seen = new Set(walletIds);
        const blocks = [];
        for (const {wid, chain} of results) {
          const fresh = chain.filter(p => {
            if (seen.has(p.walletId)) return false;
            seen.add(p.walletId);
            return true;
          });
          if (!fresh.length) continue;
          const rows = fresh.map(p => {
            const indent = '&nbsp;'.repeat(Math.max(0, (p.level - 1)) * 3);
            const via = p.level > 1 ? ` <span class="text-amber-500">(via ${_escDP(nameOf(p.viaWalletId))})</span>` : '';
            return `<div>${indent}• ${_escDP(p.name || p.walletId)}${via}</div>`;
          }).join('');
          blocks.push(`<div class="mb-1"><span class="font-medium">${_escDP(nameOf(wid))}</span> arrasta:${rows}</div>`);
        }

        if (!blocks.length) {
          box.classList.add('hidden');
        } else {
          body.innerHTML = blocks.join('');
        }
      } catch (e) {
        if (seq !== this._explosionSeq) return;
        // Falha do preview não bloqueia: o servidor ainda expande no /process.
        body.textContent = 'Não foi possível verificar as dependências agora (o processamento ainda as inclui automaticamente).';
      }
    },

    _renderDayRows(days) {
      const root = $('days');
      root.classList.remove('hidden');
      const stepLabel = _resolveCfg('stepLabel', '');
      root.innerHTML = days.map(d => `
        <li data-day="${d}">
          <span class="day-date">${d}</span>
          <span class="step-row">
            <span class="step-icon" data-state="pending" data-key="step" title="${_escDP(stepLabel)}">·</span>
          </span>
          <span class="step-msg"></span>
          <span class="day-elapsed text-gray-400"></span>
        </li>
      `).join('');
    },

    _setDayStep(day, state, msg = '') {
      const li = document.querySelector(`#${id('days')} li[data-day="${day}"]`);
      if (!li) return;
      const icon = li.querySelector('.step-icon[data-key="step"]');
      if (icon) {
        icon.dataset.state = state;
        icon.textContent = ({pending: '·', running: '⟳', done: '✓', error: '✗', skipped: '—'})[state] || '·';
      }
      if (msg) li.querySelector('.step-msg').textContent = msg;
    },
    _setDayElapsed(day, ms) {
      const li = document.querySelector(`#${id('days')} li[data-day="${day}"]`);
      if (li) li.querySelector('.day-elapsed').textContent = `${(ms / 1000).toFixed(1)}s`;
    },
    _scrollDayIntoView(day) {
      const li = document.querySelector(`#${id('days')} li[data-day="${day}"]`);
      if (li) li.scrollIntoView({block: 'nearest'});
    },

    async run() {
      this.cancelConfirm();
      if (this._running) return;
      this._running = true;
      $('run-btn').disabled = true;
      $('reset-btn').disabled = true;

      const cid    = $('company').value;
      const days   = this._resolveDays();
      const status = $('status');

      this._renderDayRows(days);
      const explicit = this._useDateList && this._selectedDates.size > 0;
      status.textContent = explicit
        ? `Iniciando — ${days.length} data(s) explícitas…`
        : `Iniciando — ${days.length} dia(s) úteis…`;

      // Resolve dynamic cfg once per run so a mid-run UI toggle can't change
      // the endpoint half-way through a date loop.
      const endpoint    = _resolveCfg('endpoint', '');
      const stepLabel   = _resolveCfg('stepLabel', '');
      const verbPast    = _resolveCfg('actionVerbPast', 'publicado');
      // For kind='publish' the per-day eligibility lookup picks docs in the
      // OPPOSITE state of what the action is about to do: publishing needs
      // `published=false`, unpublishing needs `published=true`. Default
      // preserves the legacy publish-only behavior.
      const eligPub     = _resolveCfg('eligibilityPublished', 'false');

      let stoppedAt = null;
      let totalSkipped = 0, totalRun = 0;
      outer: for (const day of days) {
        const dayT0 = performance.now();
        this._scrollDayIntoView(day);

        let ids;
        if (cfg.kind === 'publish') {
          // Per-day eligibility lookup: groupings in the opposite of the
          // target state (so we don't re-publish what's already published,
          // and don't unpublish what isn't published).
          const lookupVerb = (eligPub === 'true') ? 'publicados' : 'não publicados';
          this._setDayStep(day, 'running', `${day} · buscando ${lookupVerb}…`);
          const lookup = await api(
            'GET',
            `/api/beehus/filters/groupings-by-publish-state` +
            `?companyId=${encodeURIComponent(cid)}` +
            `&positionDate=${encodeURIComponent(day)}` +
            `&published=${eligPub}`,
          );
          if (!lookup.ok) {
            this._setDayStep(day, 'error', `lookup falhou em ${day}`);
            stoppedAt = {day};
            break outer;
          }
          ids = (lookup.body || []).map(g => g.id);
          // If the operator pre-selected a subset of groupings via the
          // picker, restrict per-day action to that intersection. Empty
          // selection = act on everything eligible (legacy behavior).
          if (this._selectedIds && this._selectedIds.size > 0) {
            const want = this._selectedIds;
            ids = ids.filter(id => want.has(id));
          }
          if (ids.length === 0) {
            this._setDayStep(day, 'skipped', `${day} · nada a ${verbPast.startsWith('des') ? 'despublicar' : 'publicar'}`);
            this._setDayElapsed(day, performance.now() - dayT0);
            totalSkipped++;
            continue;
          }
        } else {
          ids = Array.from(this._selectedIds);
        }

        this._setDayStep(day, 'running', `${day} · ${stepLabel}…`);
        const t0 = performance.now();
        const payload = {companyId: cid, positionDate: day, [cfg.payloadKey]: ids};
        const r = await api('POST', endpoint, payload);
        const ms = Math.round(performance.now() - t0);
        Token.refresh();
        if (!r.ok) {
          // Pipeline-specific "soft errors" — treated as a skip, not a stop.
          // Each pipeline opts in via cfg.skipOnError(body) → {skip, reason}|null.
          const skip = cfg.skipOnError && cfg.skipOnError(r.body);
          if (skip) {
            this._setDayStep(day, 'skipped', `${day} · ${skip.reason}`);
            this._setDayElapsed(day, performance.now() - dayT0);
            totalSkipped++;
            continue;
          }
          this._setDayStep(day, 'error', `falhou (${ms}ms)`);
          stoppedAt = {day};
          break outer;
        }
        const detail = (cfg.kind === 'publish')
          ? `${day} · ${ids.length} ${verbPast}(s) (${ms}ms)`
          : `${day} concluído (${ms}ms)`;
        this._setDayStep(day, 'done', detail);
        this._setDayElapsed(day, performance.now() - dayT0);
        totalRun++;
      }

      if (stoppedAt) {
        status.textContent = `Interrompido em ${stoppedAt.day} — veja o Log.`;
      } else if (cfg.kind === 'publish') {
        status.textContent = `Concluído — ${totalRun} dia(s) ${verbPast}(s), ${totalSkipped} ignorado(s).`;
      } else if (totalSkipped > 0) {
        status.textContent = `Concluído — ${totalRun} dia(s), ${totalSkipped} ignorado(s).`;
      } else {
        status.textContent = `Concluído em ${days.length} dia(s) úteis.`;
      }

      this._running = false;
      $('run-btn').disabled = false;
      $('reset-btn').disabled = false;
    },

    reset() {
      if (this._running) return;
      $('company').value     = '';
      $('initialDate').value = '';
      $('finalDate').value   = '';

      if (cfg.kind === 'wallets') {
        fillSelect('grouping', [], 'Selecione uma empresa primeiro', {disabled: true});
      }
      this._wallets = [];
      this._groupings = [];
      this._selectedIds = new Set();
      this._filterIds = null;
      this._renderPanes();

      this._useDateList = false;
      this._candidateDates = [];
      this._selectedDates = new Set();
      this._filterMonthEnd = false;
      const ud = $('use-date-list');         if (ud)  ud.checked = false;
      const db = $('dates-body');            if (db)  db.classList.add('hidden');
      const me = $('filter-monthend');       if (me)  me.checked = false;
      const xs = $('excel-status');          if (xs)  xs.textContent = '';
      const gs = $('groupings-excel-status'); if (gs) gs.textContent = '';
      this._renderDatePanes();

      $('status').textContent = '';
      const days = $('days');
      days.innerHTML = '';
      days.classList.add('hidden');
    },
  };
}

const ProcessDates = makeDatesPipeline({
  prefix:          'pcd',
  kind:            'wallets',
  endpoint:        '/api/beehus/positions/process',
  payloadKey:      'wallets',
  stepLabel:       'Processar Posições',
  // Mostra no modal de confirmação as carteiras que serão co-processadas pela
  // cadeia de explosão das selecionadas (a expansão real é server-side).
  explosionPreview: true,
});

/* ────────────────────────────────────────────────────────────────────────────
   Processar Posições — single-date / range mode toggle. Same shape as the
   NAV Wallets/Groupings augmentations: the "Data única" radio hides the
   final-date input and the explicit-date-list card; `_resolveDays()`
   returns just the initial date when single mode is on.
   ────────────────────────────────────────────────────────────────────────── */
(function _augmentProcessDates() {
  const $$ = (s) => document.getElementById(`pcd-${s}`);
  const _origResolveDays  = ProcessDates._resolveDays.bind(ProcessDates);
  const _origReset        = ProcessDates.reset.bind(ProcessDates);
  const _origConfirm      = ProcessDates.confirm.bind(ProcessDates);
  const _origRun          = ProcessDates.run.bind(ProcessDates);
  const _origRenderPanes  = ProcessDates._renderPanes.bind(ProcessDates);
  const _origOnCompanyChg = ProcessDates.onCompanyChange.bind(ProcessDates);

  Object.assign(ProcessDates, {
    _mode: 'single',
    // Multi-select grouping picker (mirrors DeleteTxn's _groupingSelectedIds).
    // Drives the union-based wallet filter and, when no wallets are explicitly
    // picked, supplies the effective wallet list for the upstream call.
    _groupingSelectedIds: new Set(),

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        const fd = $$('finalDate'); if (fd) fd.value = '';
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    // ── Groupings picker (dual-pane transfer) ──────────────────────────────
    _walletFilterFromGroupings() {
      if (!this._groupingSelectedIds || !this._groupingSelectedIds.size) return null;
      const ids = new Set();
      for (const g of (this._groupings || [])) {
        if (this._groupingSelectedIds.has(g.id)) {
          (g.walletIds || []).forEach(w => ids.add(w));
        }
      }
      return ids;
    },

    _renderGroupingPanes() {
      const avail = $$('grp-available');
      const sel   = $$('grp-selected');
      if (!avail || !sel) return;
      const all = this._groupings || [];
      const available = all.filter(g => !this._groupingSelectedIds.has(g.id));
      const selected  = all.filter(g =>  this._groupingSelectedIds.has(g.id));
      avail.innerHTML = available.map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      sel.innerHTML   = selected .map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      $$('grp-available-count').textContent = available.length;
      $$('grp-selected-count').textContent  = selected.length;
      // Wallet availability depends on the grouping selection (union filter).
      this._renderPanes();
    },

    _highlightedGrp(suffix) {
      const el = $$(`grp-${suffix}`);
      return el ? Array.from(el.selectedOptions).map(o => o.value) : [];
    },
    addGroupingSelected() {
      this._highlightedGrp('available').forEach(id => this._groupingSelectedIds.add(id));
      this._renderGroupingPanes();
    },
    addGroupingAll() {
      Array.from($$('grp-available').options)
        .forEach(o => { if (o.value) this._groupingSelectedIds.add(o.value); });
      this._renderGroupingPanes();
    },
    removeGroupingSelected() {
      this._highlightedGrp('selected').forEach(id => this._groupingSelectedIds.delete(id));
      this._renderGroupingPanes();
    },
    removeGroupingAll() {
      this._groupingSelectedIds.clear();
      this._renderGroupingPanes();
    },

    async onGrpExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = $$('grp-excel-status');
      if (statusEl) statusEl.textContent = 'Processando…';
      if (!(this._groupings || []).length) {
        if (statusEl) statusEl.textContent = 'selecione uma empresa primeiro';
        alert('Selecione uma empresa antes de subir os IDs.');
        ev.target.value = '';
        return;
      }
      const known = new Set(this._groupings.map(g => g.id));
      const r = await _excelToMatchedIds(file, known);
      ev.target.value = '';
      if (!r.ok) {
        if (statusEl) statusEl.textContent = `falha: ${r.error}`;
        alert('Falha ao ler o Excel: ' + r.error);
        return;
      }
      if (!r.total) {
        if (statusEl) statusEl.textContent = 'nenhum ID encontrado';
        return;
      }
      let added = 0;
      r.matched.forEach(id => {
        if (!this._groupingSelectedIds.has(id)) { this._groupingSelectedIds.add(id); added++; }
      });
      if (statusEl) statusEl.textContent =
        `${added} grouping(s) adicionado(s)` +
        (r.unmatched ? ` · ${r.unmatched} ID(s) ignorado(s)` : '');
      this._renderGroupingPanes();
    },

    // Inject groupings-derived filter into _filterIds before delegating to the
    // shared factory render. Keeps the legacy single-grouping plumbing alive
    // (hidden #pcd-grouping placeholder) while overriding behaviour cleanly.
    _renderPanes() {
      this._filterIds = this._walletFilterFromGroupings();
      _origRenderPanes();
    },

    async onCompanyChange() {
      // Clear groupings selection on every company change before the original
      // handler refetches groupings/wallets, otherwise stale selected ids
      // would survive across companies.
      this._groupingSelectedIds = new Set();
      const statusEl = $$('grp-excel-status'); if (statusEl) statusEl.textContent = '';
      await _origOnCompanyChg();
      this._renderGroupingPanes();
    },

    _effectiveSelectedWallets() {
      // Explicit wallet selection wins; otherwise fall back to the union of
      // walletIds across selected groupings. Both empty → empty set (= "all
      // wallets in company", preserving the legacy behaviour).
      if (this._selectedIds && this._selectedIds.size) return new Set(this._selectedIds);
      return this._walletFilterFromGroupings() || new Set();
    },

    _confirmWalletsLabel() {
      const eff = this._effectiveSelectedWallets();
      if (!eff.size) return 'todas as wallets';
      const fromGroupings =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;
      return fromGroupings
        ? `${eff.size} wallet(s) via ${this._groupingSelectedIds.size} grouping(s)`
        : `${eff.size} selecionada(s)`;
    },

    confirm() {
      if (this._mode !== 'single') {
        _origConfirm();
        // Original confirm wrote "X selecionada(s) / todas as wallets" using
        // _selectedIds only; overwrite with the groupings-aware label.
        const cw = $$('confirm-wallets');
        if (cw && $$('confirm-modal').classList.contains('show')) {
          cw.textContent = this._confirmWalletsLabel();
        }
        return;
      }
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      $$('confirm-wallets').textContent = this._confirmWalletsLabel();
      $$('confirm-modal').classList.add('show');
    },

    // When wallets are empty but groupings are selected, substitute the
    // union of grouping walletIds into _selectedIds for the duration of the
    // upstream call so each per-day POST restricts to those wallets.
    async run() {
      const needsSubstitution =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;
      if (!needsSubstitution) return _origRun();
      const union = this._walletFilterFromGroupings();
      if (!union || !union.size) return _origRun();
      const saved = this._selectedIds;
      this._selectedIds = union;
      try { await _origRun(); }
      finally { this._selectedIds = saved; }
    },

    reset() {
      _origReset();
      this._groupingSelectedIds = new Set();
      const ge = $$('grp-excel-status'); if (ge) ge.textContent = '';
      this._renderGroupingPanes();
      this._mode = 'single';
      const radios = document.querySelectorAll('input[name="pcd-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('pcd-finalDate-wrap')) {
        ProcessDates.onModeChange('single');
        ProcessDates._renderGroupingPanes();
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

const NavWalletsDates = makeDatesPipeline({
  prefix:     'nwd',
  kind:       'wallets',
  endpoint:   '/api/beehus/nav/calculate-wallets',
  payloadKey: 'wallets',
  stepLabel:  'Calcular NAV Wallets',
  // Upstream complains when a date has no processed positions yet — common
  // when iterating a long range that includes days the user hasn't processed.
  // Treat that specific error as a skip and move to the next date instead of
  // halting the whole loop.
  skipOnError(body) {
    const blob = JSON.stringify(body || '');
    if (blob.includes('Nenhuma posi') && blob.includes('processada') && blob.includes('encontrada')) {
      return {reason: 'sem posições processadas — ignorado'};
    }
    return null;
  },
});

/* ────────────────────────────────────────────────────────────────────────────
   NAV Wallets — single-date / range mode toggle. The single-day "Calcular
   NAV Wallets" view used to be a separate page; we fold it into the dates
   pipeline by adding a mode switch that hides the final-date input and the
   explicit-date-list card. In single mode `_resolveDays()` returns just the
   initial date (no business-day filter), so a Saturday/Sunday picked
   intentionally still runs.
   ────────────────────────────────────────────────────────────────────────── */
(function _augmentNavWalletsDates() {
  const $$ = (s) => document.getElementById(`nwd-${s}`);
  const _origResolveDays  = NavWalletsDates._resolveDays.bind(NavWalletsDates);
  const _origReset        = NavWalletsDates.reset.bind(NavWalletsDates);
  const _origConfirm      = NavWalletsDates.confirm.bind(NavWalletsDates);
  const _origRun          = NavWalletsDates.run.bind(NavWalletsDates);
  const _origRenderPanes  = NavWalletsDates._renderPanes.bind(NavWalletsDates);
  const _origOnCompanyChg = NavWalletsDates.onCompanyChange.bind(NavWalletsDates);

  Object.assign(NavWalletsDates, {
    _mode: 'single',   // 'single' | 'range' — defaults to single (most common ask)
    // Multi-select grouping picker (mirrors DeleteTxn's _groupingSelectedIds).
    // Drives the union-based wallet filter and, when no wallets are explicitly
    // picked, supplies the effective wallet list for the upstream call.
    _groupingSelectedIds: new Set(),

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      // Show / hide final-date input
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      // Re-label the first date input so it reflects the active mode
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      // Show / hide the optional date-list card (only useful in range mode)
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      // Leaving range mode wipes the date-list to avoid surprising state.
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        // Clear final-date so confirm-modal shows clean state.
        const fd = $$('finalDate'); if (fd) fd.value = '';
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    // ── Groupings picker (dual-pane transfer) ──────────────────────────────
    _walletFilterFromGroupings() {
      if (!this._groupingSelectedIds || !this._groupingSelectedIds.size) return null;
      const ids = new Set();
      for (const g of (this._groupings || [])) {
        if (this._groupingSelectedIds.has(g.id)) {
          (g.walletIds || []).forEach(w => ids.add(w));
        }
      }
      return ids;
    },

    _renderGroupingPanes() {
      const avail = $$('grp-available');
      const sel   = $$('grp-selected');
      if (!avail || !sel) return;
      const all = this._groupings || [];
      const available = all.filter(g => !this._groupingSelectedIds.has(g.id));
      const selected  = all.filter(g =>  this._groupingSelectedIds.has(g.id));
      avail.innerHTML = available.map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      sel.innerHTML   = selected .map(g => `<option value="${g.id}">${_escDP(g.name)}</option>`).join('');
      $$('grp-available-count').textContent = available.length;
      $$('grp-selected-count').textContent  = selected.length;
      // Wallet availability depends on the grouping selection (union filter).
      this._renderPanes();
    },

    _highlightedGrp(suffix) {
      const el = $$(`grp-${suffix}`);
      return el ? Array.from(el.selectedOptions).map(o => o.value) : [];
    },
    addGroupingSelected() {
      this._highlightedGrp('available').forEach(id => this._groupingSelectedIds.add(id));
      this._renderGroupingPanes();
    },
    addGroupingAll() {
      Array.from($$('grp-available').options)
        .forEach(o => { if (o.value) this._groupingSelectedIds.add(o.value); });
      this._renderGroupingPanes();
    },
    removeGroupingSelected() {
      this._highlightedGrp('selected').forEach(id => this._groupingSelectedIds.delete(id));
      this._renderGroupingPanes();
    },
    removeGroupingAll() {
      this._groupingSelectedIds.clear();
      this._renderGroupingPanes();
    },

    async onGrpExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const statusEl = $$('grp-excel-status');
      if (statusEl) statusEl.textContent = 'Processando…';
      if (!(this._groupings || []).length) {
        if (statusEl) statusEl.textContent = 'selecione uma empresa primeiro';
        alert('Selecione uma empresa antes de subir os IDs.');
        ev.target.value = '';
        return;
      }
      const known = new Set(this._groupings.map(g => g.id));
      const r = await _excelToMatchedIds(file, known);
      ev.target.value = '';
      if (!r.ok) {
        if (statusEl) statusEl.textContent = `falha: ${r.error}`;
        alert('Falha ao ler o Excel: ' + r.error);
        return;
      }
      if (!r.total) {
        if (statusEl) statusEl.textContent = 'nenhum ID encontrado';
        return;
      }
      let added = 0;
      r.matched.forEach(id => {
        if (!this._groupingSelectedIds.has(id)) { this._groupingSelectedIds.add(id); added++; }
      });
      if (statusEl) statusEl.textContent =
        `${added} grouping(s) adicionado(s)` +
        (r.unmatched ? ` · ${r.unmatched} ID(s) ignorado(s)` : '');
      this._renderGroupingPanes();
    },

    // Inject groupings-derived filter into _filterIds before delegating to the
    // shared factory render. Keeps the legacy single-grouping plumbing alive
    // (hidden #nwd-grouping placeholder) while overriding behaviour cleanly.
    _renderPanes() {
      this._filterIds = this._walletFilterFromGroupings();
      _origRenderPanes();
    },

    async onCompanyChange() {
      // Clear groupings selection on every company change before the original
      // handler refetches groupings/wallets, otherwise stale selected ids
      // would survive across companies.
      this._groupingSelectedIds = new Set();
      const statusEl = $$('grp-excel-status'); if (statusEl) statusEl.textContent = '';
      await _origOnCompanyChg();
      this._renderGroupingPanes();
    },

    _effectiveSelectedWallets() {
      // Explicit wallet selection wins; otherwise fall back to the union of
      // walletIds across selected groupings. Both empty → empty set (= "all
      // wallets in company", preserving the legacy behaviour).
      if (this._selectedIds && this._selectedIds.size) return new Set(this._selectedIds);
      return this._walletFilterFromGroupings() || new Set();
    },

    _confirmWalletsLabel() {
      const eff = this._effectiveSelectedWallets();
      if (!eff.size) return 'todas as wallets';
      const fromGroupings =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;
      return fromGroupings
        ? `${eff.size} wallet(s) via ${this._groupingSelectedIds.size} grouping(s)`
        : `${eff.size} selecionada(s)`;
    },

    confirm() {
      // In range mode the factory already does the right thing; in single
      // mode we skip the "informe data final" check and write the modal
      // text manually so the user sees just one date instead of "→".
      if (this._mode !== 'single') {
        _origConfirm();
        // Original confirm wrote "X selecionada(s) / todas as wallets" using
        // _selectedIds only; overwrite with the groupings-aware label.
        const cw = $$('confirm-wallets');
        if (cw && $$('confirm-modal').classList.contains('show')) {
          cw.textContent = this._confirmWalletsLabel();
        }
        return;
      }
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      $$('confirm-wallets').textContent = this._confirmWalletsLabel();
      $$('confirm-modal').classList.add('show');
    },

    // When wallets are empty but groupings are selected, substitute the
    // union of grouping walletIds into _selectedIds for the duration of the
    // upstream call so each per-day POST restricts to those wallets.
    async run() {
      const needsSubstitution =
        (!this._selectedIds || !this._selectedIds.size) &&
        this._groupingSelectedIds && this._groupingSelectedIds.size > 0;
      if (!needsSubstitution) return _origRun();
      const union = this._walletFilterFromGroupings();
      if (!union || !union.size) return _origRun();
      const saved = this._selectedIds;
      this._selectedIds = union;
      try { await _origRun(); }
      finally { this._selectedIds = saved; }
    },

    reset() {
      _origReset();
      this._groupingSelectedIds = new Set();
      const ge = $$('grp-excel-status'); if (ge) ge.textContent = '';
      this._renderGroupingPanes();
      this._mode = 'single';
      // Default the radio back to "Data única"
      const radios = document.querySelectorAll('input[name="nwd-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  // Apply default mode on load so initial UI matches `_mode`.
  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('nwd-finalDate-wrap')) {
        NavWalletsDates.onModeChange('single');
        NavWalletsDates._renderGroupingPanes();
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

const NavGroupingsDates = makeDatesPipeline({
  prefix:     'ngd',
  kind:       'groupings',
  endpoint:   '/api/beehus/nav/calculate-groupings',
  payloadKey: 'groupings',
  stepLabel:  'Calcular NAV Groupings',
});

/* ────────────────────────────────────────────────────────────────────────────
   NAV Groupings — single-date / range mode toggle. Same shape as the NAV
   Wallets augmentation: a "Data única" radio hides the final-date input and
   the explicit-date-list card; `_resolveDays()` returns just the initial
   date when single mode is on.
   ────────────────────────────────────────────────────────────────────────── */
(function _augmentNavGroupingsDates() {
  const $$ = (s) => document.getElementById(`ngd-${s}`);
  const _origResolveDays = NavGroupingsDates._resolveDays.bind(NavGroupingsDates);
  const _origReset       = NavGroupingsDates.reset.bind(NavGroupingsDates);
  const _origConfirm     = NavGroupingsDates.confirm.bind(NavGroupingsDates);

  Object.assign(NavGroupingsDates, {
    _mode: 'single',

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        const fd = $$('finalDate'); if (fd) fd.value = '';
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    confirm() {
      if (this._mode !== 'single') return _origConfirm();
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      const ids = Array.from(this._selectedIds);
      $$('confirm-groupings').textContent = ids.length
        ? `${ids.length} selecionado(s)`
        : 'todos os groupings';
      $$('confirm-modal').classList.add('show');
    },

    reset() {
      _origReset();
      this._mode = 'single';
      const radios = document.querySelectorAll('input[name="ngd-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('ngd-finalDate-wrap')) {
        NavGroupingsDates.onModeChange('single');
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

const PublishDates = makeDatesPipeline({
  prefix:     'pbd',
  kind:       'publish',
  payloadKey: 'groupingIds',
  // Action-aware resolvers — flipped by the radio toggle in the view.
  // _action lives on PublishDates itself (set by _augmentPublishDates below).
  // Defaults to 'publish' if the augment IIFE hasn't run yet.
  endpoint() {
    return (PublishDates._action === 'unpublish')
      ? '/api/beehus/nav/unpublish'
      : '/api/beehus/nav/publish';
  },
  stepLabel() {
    return (PublishDates._action === 'unpublish')
      ? 'Despublicar Agrupamentos'
      : 'Publicar Agrupamentos';
  },
  eligibilityPublished() {
    // We act on docs that are in the OPPOSITE state of the target —
    // publish needs the not-yet-published ones; unpublish needs the
    // already-published ones.
    return (PublishDates._action === 'unpublish') ? 'true' : 'false';
  },
  actionVerbPast() {
    return (PublishDates._action === 'unpublish') ? 'despublicado' : 'publicado';
  },
});

/* ────────────────────────────────────────────────────────────────────────────
   PublishDates picker — adds the Publicar-Agrupamentos-style "Disponíveis /
   Selecionadas" picker on top of the dates pipeline. The picker is loaded
   for the **initial date** as a reference; in run() the factory intersects
   the per-day eligibility set with the picker's selection so each day only
   publishes the user-curated subset (or all eligible, if the picker is
   empty). |Δ| values come from the worst-wallet of each grouping on the
   initial date, mirroring the single-day publicar agrupamentos view.
   ────────────────────────────────────────────────────────────────────────── */
(function _augmentPublishDates() {
  const $$ = (s) => document.getElementById(`pbd-${s}`);
  // Save the original lifecycle methods so we can call through after our
  // picker-refresh logic.
  const _origInit          = PublishDates.init.bind(PublishDates);
  const _origCompanyChange = PublishDates.onCompanyChange.bind(PublishDates);
  const _origRangeChange   = PublishDates.onRangeChange.bind(PublishDates);
  const _origReset         = PublishDates.reset.bind(PublishDates);
  const _origConfirm       = PublishDates.confirm.bind(PublishDates);

  Object.assign(PublishDates, {
    _availableGroupings: [],   // [{id, name, walletIds}] eligible on initialDate
    _groupingDeltas:     [],   // [{groupingId, deltaAbs, ...}]
    _availHighlighted:   new Set(),
    _selHighlighted:     new Set(),
    _prevDayFilter:      false,
    _prevDayIds:         new Set(),
    // Matrix (month overview)
    _matrixData:    null,   // {[gid]: {name, byDate: {dateStr: {deltaAbs, published}}}}
    _matrixFilter:  'all',  // 'all' | 'published' | 'unpublished'
    _matrixSortCol: null,   // null = sort by name; dateStr = sort by color cycle
    _matrixSortDir: 'asc',  // name col: 'asc'|'desc'; date col: 0|1|2|3 (cycle state)
    COLOR_CYCLES: [
      [0, 2, 3, 1, 4, 5],  // 0: verde primeiro
      [2, 0, 3, 1, 4, 5],  // 1: amarelo primeiro
      [3, 2, 0, 1, 4, 5],  // 2: vermelho primeiro
      [1, 3, 2, 0, 4, 5],  // 3: roxo primeiro
    ],
    // Action toggle — flips the picker source (published=false vs =true),
    // the action endpoint, button styling, and labels throughout the view.
    // Read by makeDatesPipeline's _resolveCfg getters above.
    _action: 'publish',

    // Labels that flip with the action toggle. `_applyActionLabels()` applies
    // them to the DOM whenever the action changes (and once on init).
    ACTION_LABELS: {
      publish: {
        sectionTitle:  'Publicar Agrupamentos',
        sectionHint:
          'Publica os agrupamentos não publicados. Em <strong>data única</strong> ' +
          'roda uma vez para a data informada; em <strong>faixa de datas</strong> ' +
          'roda para cada dia útil (segunda a sexta) entre as datas inicial e ' +
          'final. Dias sem agrupamentos elegíveis são ignorados. A picker abaixo ' +
          'usa a data inicial como referência — em modo faixa, só serão ' +
          'publicados os agrupamentos elegíveis em cada dia E na seleção (lista ' +
          'vazia = todos os elegíveis).',
        pickerHint:
          'Lista carregada para a <strong>data inicial</strong>. Em cada dia ' +
          'útil, só serão publicados os agrupamentos que estiverem elegíveis ' +
          '(não publicados) E na seleção. Lista "Selecionadas" vazia = ' +
          'publica TODOS os agrupamentos elegíveis em cada dia. A coluna ' +
          '|Δ| usa a pior wallet do agrupamento na data inicial.',
        emptyBadge:    'nenhum agrupamento não publicado',
        emptyBadgeTitle:
          'Não há agrupamentos com navPackage não-publicado nesta data — ' +
          'todos já estão publicados, ou ainda não houve cálculo NAV Groupings.',
        pkrNoDateText: 'selecione data',
        runBtn:        'Executar publicação',
        runBtnClass:   'btn-primary',
        confirmTitle:  'Confirmar publicação por datas',
        confirmHint:
          'Para cada dia útil, o sistema busca os agrupamentos não publicados ' +
          'e dispara a publicação. Dias sem agrupamentos elegíveis são ' +
          'ignorados. Não feche a aba enquanto rodar.',
        confirmRunBtn: 'Confirmar publicação',
        confirmRunBtnClass: 'btn-primary',
        showThreshold:     true,
        showPrevDayFilter: true,
      },
      unpublish: {
        sectionTitle:  'Despublicar Agrupamentos',
        sectionHint:
          'Despublica os agrupamentos publicados. Em <strong>data única</strong> ' +
          'roda uma vez para a data informada; em <strong>faixa de datas</strong> ' +
          'roda para cada dia útil (segunda a sexta) entre as datas inicial e ' +
          'final. Dias sem agrupamentos publicados são ignorados. A picker abaixo ' +
          'lista <strong>todos os agrupamentos da empresa</strong> independente ' +
          'da data — a interseção com o que está publicado em cada dia é feita ' +
          'no momento da execução (lista vazia = despublica todos os publicados).',
        pickerHint:
          'Lista carregada por <strong>empresa</strong> (todos os agrupamentos, ' +
          'independente da data). Em cada dia útil, só serão despublicados os ' +
          'agrupamentos publicados E na seleção. Lista "Selecionadas" vazia = ' +
          'despublica TODOS os agrupamentos publicados em cada dia.',
        emptyBadge:    'empresa sem agrupamentos',
        emptyBadgeTitle:
          'Nenhum agrupamento cadastrado para esta empresa.',
        pkrNoDateText: 'selecione empresa',
        runBtn:        'Executar despublicação',
        runBtnClass:   'btn-danger',
        confirmTitle:  'Confirmar despublicação por datas',
        confirmHint:
          'Para cada dia útil, o sistema busca os agrupamentos publicados ' +
          'e dispara a despublicação. Dias sem agrupamentos publicados são ' +
          'ignorados. Não feche a aba enquanto rodar.',
        confirmRunBtn: 'Confirmar despublicação',
        confirmRunBtnClass: 'btn-danger',
        showThreshold:     false,
        showPrevDayFilter: false,
      },
    },

    async init() {
      await _origInit();
      this._applyActionLabels();
    },

    async onCompanyChange() {
      await _origCompanyChange();
      await this._refreshPicker();
      this._scheduleMatrixRefresh(300);
    },
    onRangeChange() {
      _origRangeChange();
      // No despublicar o picker é company-driven (lista todos os agrupamentos
      // da empresa, independente da data), então mudar a data não recarrega
      // nada — evita o flicker/refresh ao digitar a data. No publicar o
      // picker é date-aware (|Δ| depende da data), então recarrega com um
      // debounce maior porque inputs `type=date` no Chromium disparam
      // `change` a cada dígito digitado e 500ms estava cedo demais.
      if (this._action !== 'unpublish') {
        clearTimeout(this._pickerDebounce);
        this._pickerDebounce = setTimeout(() => this._refreshPicker(), 1500);
      }
      this._scheduleMatrixRefresh(2000);
    },

    // Switch between publish and unpublish modes. Clears the picker selection
    // (eligibility set changes entirely between modes), refetches the
    // available groupings against the new `published` flag, and rewrites all
    // user-facing labels in the view + confirmation modal.
    async onActionChange(action) {
      this._action = (action === 'unpublish') ? 'unpublish' : 'publish';
      this._selectedIds = new Set();
      this._availHighlighted.clear();
      this._selHighlighted.clear();
      this._availableGroupings = [];
      this._groupingDeltas = [];
      const xs = $$('pkr-excel-status'); if (xs) xs.textContent = '';
      this._applyActionLabels();
      await this._refreshPicker();
    },

    _applyActionLabels() {
      const L = this.ACTION_LABELS[this._action] || this.ACTION_LABELS.publish;
      const setHtml = (sfx, html) => { const el = $$(sfx); if (el) el.innerHTML = html; };
      const setText = (sfx, txt)  => { const el = $$(sfx); if (el) el.textContent = txt; };
      setText('section-title', L.sectionTitle);
      setHtml('section-hint',  L.sectionHint);
      setText('pkr-title',     'Agrupamentos (opcional)');
      setHtml('pkr-hint',      L.pickerHint);

      const allPub = $$('pkr-all-published');
      if (allPub) {
        allPub.textContent = L.emptyBadge;
        allPub.title       = L.emptyBadgeTitle;
      }

      // "selecione data" no publicar / "selecione empresa" no despublicar —
      // o picker do despublicar é company-driven, então é a empresa que falta
      // antes de carregar a lista, não a data.
      const noDate = $$('pkr-no-date');
      if (noDate && L.pkrNoDateText) noDate.textContent = L.pkrNoDateText;

      // Threshold control only makes sense for "Publicar" — it filters out
      // groupings whose |Δ| is too large to safely publish. For "Despublicar"
      // there's no safety semantics, so we hide the input.
      const thrWrap = $$('threshold-wrap');
      if (thrWrap) thrWrap.classList.toggle('hidden', !L.showThreshold);

      // "Publicado na data anterior" filter — only meaningful for publish mode.
      const prevDayWrap = $$('prev-day-wrap');
      if (prevDayWrap) prevDayWrap.classList.toggle('hidden', !L.showPrevDayFilter);
      if (!L.showPrevDayFilter) {
        this._prevDayFilter = false;
        this._prevDayIds    = new Set();
        const pdcb = $$('prev-day-filter');
        if (pdcb) pdcb.checked = false;
      }

      const runBtn = $$('run-btn');
      if (runBtn) {
        runBtn.textContent = L.runBtn;
        runBtn.classList.remove('btn-primary', 'btn-danger');
        runBtn.classList.add(L.runBtnClass);
      }

      setText('confirm-title', L.confirmTitle);
      setText('confirm-hint',  L.confirmHint);
      const confirmRun = $$('confirm-run-btn');
      if (confirmRun) {
        confirmRun.textContent = L.confirmRunBtn;
        confirmRun.classList.remove('btn-primary', 'btn-danger');
        confirmRun.classList.add(L.confirmRunBtnClass);
      }
    },

    reset() {
      _origReset();
      this._availableGroupings = [];
      this._groupingDeltas = [];
      this._availHighlighted.clear();
      this._selHighlighted.clear();
      // HTML5 number inputs require dot-decimal in the value (".value = '0,02'"
      // is silently rejected and console-warns). pt-BR users still see the comma
      // because the browser formats `type="number"` per its locale on render.
      const t = $$('threshold');     if (t) t.value = '0.02';
      const xs = $$('pkr-excel-status'); if (xs) xs.textContent = '';
      this._prevDayFilter = false;
      this._prevDayIds    = new Set();
      const pdcb = $$('prev-day-filter'); if (pdcb) pdcb.checked = false;
      // Clear matrix
      this._matrixData    = null;
      this._matrixFilter  = 'all';
      this._matrixSortCol = null;
      this._matrixSortDir = 'asc';
      const dc = document.getElementById('pbd-detail-card');
      if (dc) dc.classList.add('hidden');
      const radiosFilter = document.querySelectorAll('input[name="pbd-matrix-filter"]');
      radiosFilter.forEach(r => { r.checked = (r.value === 'all'); });
      // Reset back to "Publicar" — both the radios and the resulting labels.
      this._action = 'publish';
      const radios = document.querySelectorAll('input[name="pbd-action"]');
      radios.forEach(r => { r.checked = (r.value === 'publish'); });
      this._applyActionLabels();
      this._renderPickerPanes();
    },

    confirm() {
      // Keep the existing summary modal but include the picker-selected count.
      _origConfirm();
      const node = document.getElementById('pbd-confirm-modal');
      if (!node) return;
      // Annotate the modal body with the picker selection (best-effort —
      // if the modal markup doesn't have a slot, we just skip).
      const slot = node.querySelector('[data-picker-summary]');
      if (slot) {
        const n = this._selectedIds ? this._selectedIds.size : 0;
        slot.textContent = n ? `${n} selecionado(s)` : 'todos os elegíveis em cada dia';
      }
    },

    _thresholdDecimal() {
      // For "Despublicar", the |Δ| safety filter has no meaning — there's no
      // "safe to despublicar" notion, so we bypass it entirely. The threshold
      // input is also hidden in unpublish mode (see _applyActionLabels).
      if (this._action === 'unpublish') return 0;
      const raw = (($$('threshold')?.value) ?? '').toString().replace(',', '.').trim();
      const pct = parseFloat(raw);
      if (!isFinite(pct) || pct < 0) return 0;
      return pct / 100;
    },

    onThresholdChange() { this._renderPickerPanes(); },

    _prevBusinessDay(dateStr) {
      const d = new Date(dateStr + 'T12:00:00');
      do { d.setDate(d.getDate() - 1); } while (d.getDay() === 0 || d.getDay() === 6);
      return d.toISOString().slice(0, 10);
    },

    async _fetchPrevDayIds() {
      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      if (!cid || !dt || !this._prevDayFilter) {
        this._prevDayIds = new Set();
        return;
      }
      const prevDt = this._prevBusinessDay(dt);
      const resp = await api('GET',
        `/api/beehus/filters/groupings-by-publish-state` +
        `?companyId=${encodeURIComponent(cid)}` +
        `&positionDate=${encodeURIComponent(prevDt)}` +
        `&published=true`);
      this._prevDayIds = new Set((resp.body || []).map(g => g.id));
    },

    async onPrevDayFilterChange() {
      const cb = $$('prev-day-filter');
      this._prevDayFilter = cb ? cb.checked : false;
      await this._fetchPrevDayIds();
      this._renderPickerPanes();
    },

    async _refreshPicker() {
      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      const noDate = $$('pkr-no-date');
      const isUnpub = (this._action === 'unpublish');

      // Publicar precisa de empresa + data (|Δ| depende da data e a lista
      // só faz sentido contra os navPackages do dia). Despublicar precisa
      // apenas da empresa — o picker lista TODOS os agrupamentos cadastrados
      // e o run() faz a interseção com os publicados por dia.
      if (!cid || (!isUnpub && !dt)) {
        this._availableGroupings = [];
        this._groupingDeltas = [];
        if (noDate) noDate.classList.remove('hidden');
        this._renderPickerPanes();
        return;
      }
      if (noDate) noDate.classList.add('hidden');

      if (isUnpub) {
        const r = await api('GET',
          `/api/beehus/filters/groupings?companyId=${encodeURIComponent(cid)}`);
        this._availableGroupings = r.body || [];
        this._groupingDeltas     = [];
      } else {
        // publish → published=false (carrega os ainda não publicados).
        const pub = 'false';
        const [eligible, deltas] = await Promise.all([
          api('GET',
            `/api/beehus/filters/groupings-by-publish-state` +
            `?companyId=${encodeURIComponent(cid)}` +
            `&positionDate=${encodeURIComponent(dt)}` +
            `&published=${pub}`),
          api('GET',
            `/api/beehus/filters/grouping-return-deltas` +
            `?companyId=${encodeURIComponent(cid)}` +
            `&positionDate=${encodeURIComponent(dt)}` +
            `&published=${pub}`),
        ]);
        this._availableGroupings = (eligible.body || []);
        this._groupingDeltas     = (deltas.body && Array.isArray(deltas.body)) ? deltas.body : [];
      }

      await this._fetchPrevDayIds();

      // Drop any selection que não pertence mais à lista disponível (ex:
      // empresa trocou, ou voltamos para publicar e a data invalidou ids).
      const eligibleIds = new Set(this._availableGroupings.map(g => g.id));
      for (const id of Array.from(this._selectedIds)) {
        if (!eligibleIds.has(id)) this._selectedIds.delete(id);
      }
      this._renderPickerPanes();
    },

    _fmtPct(decimal) {
      if (decimal === null || decimal === undefined || !isFinite(decimal)) return '—';
      return (decimal * 100).toLocaleString('pt-BR', {
        minimumFractionDigits: 4, maximumFractionDigits: 4,
      }) + '%';
    },
    _esc(s) {
      return String(s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    },

    _renderPickerPanes() {
      const avail = $$('pkr-available');
      const sel   = $$('pkr-selected');
      if (!avail || !sel) return;

      const threshold  = this._thresholdDecimal();
      const deltaByGid = new Map();
      for (const d of this._groupingDeltas) deltaByGid.set(d.groupingId, d);

      const passes = (g) => {
        if (threshold <= 0) return true;
        const d = deltaByGid.get(g.id);
        if (!d || d.deltaAbs === null || d.deltaAbs === undefined) return false;
        return d.deltaAbs < threshold;
      };

      const rowHTML = (g, hlSet) => {
        const d    = deltaByGid.get(g.id);
        const dval = d ? d.deltaAbs : null;
        const tag  = (dval === null || dval === undefined) ? '—' : this._fmtPct(dval);
        const cls  = 'picker-row' + (hlSet.has(g.id) ? ' highlighted' : '');
        return `<div class="${cls}" data-id="${this._esc(g.id)}" title="${this._esc(g.name)}">
                  <span class="picker-row-name">${this._esc(g.name)}</span>
                  <span class="picker-row-delta">${tag}</span>
                </div>`;
      };

      const available = this._availableGroupings.filter(g =>
        !this._selectedIds.has(g.id) && passes(g) &&
        (!this._prevDayFilter || this._prevDayIds.has(g.id)));
      const selected  = this._availableGroupings.filter(g => this._selectedIds.has(g.id));

      for (const id of Array.from(this._availHighlighted)) {
        if (!available.find(g => g.id === id)) this._availHighlighted.delete(id);
      }
      for (const id of Array.from(this._selHighlighted)) {
        if (!selected.find(g => g.id === id)) this._selHighlighted.delete(id);
      }

      avail.innerHTML = available.map(g => rowHTML(g, this._availHighlighted)).join('');
      sel.innerHTML   = selected .map(g => rowHTML(g, this._selHighlighted)).join('');

      $$('pkr-available-count').textContent = available.length;
      $$('pkr-selected-count').textContent  = selected.length;

      const filterBadge = $$('pkr-filter-summary');
      if (filterBadge) {
        if (threshold > 0 && this._availableGroupings.length > 0) {
          const safe = this._availableGroupings.filter(passes).length;
          const limitPct = (threshold * 100).toLocaleString('pt-BR', {
            minimumFractionDigits: 2, maximumFractionDigits: 4,
          });
          filterBadge.textContent = `${safe}/${this._availableGroupings.length} < ${limitPct}%`;
          filterBadge.classList.remove('hidden');
        } else {
          filterBadge.classList.add('hidden');
          filterBadge.textContent = '';
        }
      }

      const prevDayBadge = $$('pkr-prev-day-badge');
      if (prevDayBadge) {
        const cid2 = $$('company').value;
        const dt2  = $$('initialDate').value;
        if (this._prevDayFilter && cid2 && dt2) {
          const n = this._availableGroupings.filter(g =>
            !this._selectedIds.has(g.id) && passes(g) && this._prevDayIds.has(g.id)).length;
          const prevDt = this._prevBusinessDay(dt2);
          const [, pdm, pdd] = prevDt.split('-');
          prevDayBadge.textContent = `${n} pub. em ${pdd}/${pdm}`;
          prevDayBadge.title = `Publicados em ${prevDt}`;
          prevDayBadge.classList.remove('hidden');
        } else {
          prevDayBadge.classList.add('hidden');
        }
      }

      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      const isUnpub = (this._action === 'unpublish');
      const noDate = $$('pkr-no-date');
      // No despublicar, basta a empresa para popular o picker; no publicar
      // ainda precisamos da data porque a busca é date-aware.
      if (noDate) {
        noDate.classList.toggle('hidden', isUnpub ? !!cid : !!(cid && dt));
      }
      // "Empty state" badge — visível quando os pré-requisitos foram
      // atendidos, a busca já terminou (sem groupings disponíveis) e a
      // fila de Selecionadas também está vazia. No despublicar o pré-
      // requisito é só a empresa; no publicar é empresa + data.
      const allPub = $$('pkr-all-published');
      if (allPub) {
        const ready = isUnpub ? !!cid : !!(cid && dt);
        const empty = ready
                   && this._availableGroupings.length === 0
                   && this._selectedIds.size === 0;
        allPub.classList.toggle('hidden', !empty);
      }
    },

    _handlePickerRowClick(side, event) {
      const row = event.target && event.target.closest && event.target.closest('.picker-row');
      if (!row || !row.dataset.id) return;
      const id  = row.dataset.id;
      const set = (side === 'avail') ? this._availHighlighted : this._selHighlighted;
      if (event.ctrlKey || event.metaKey) {
        if (set.has(id)) set.delete(id);
        else             set.add(id);
      } else {
        set.clear();
        set.add(id);
      }
      this._renderPickerPanes();
    },

    addPickerSelected() {
      const threshold = this._thresholdDecimal();
      const deltaByGid = new Map();
      for (const d of this._groupingDeltas) deltaByGid.set(d.groupingId, d);
      const passes = (g) => {
        if (threshold <= 0) return true;
        const d = deltaByGid.get(g.id);
        if (!d || d.deltaAbs === null || d.deltaAbs === undefined) return false;
        return d.deltaAbs < threshold;
      };
      // If user explicitly highlighted rows, add only those (always allowed
      // — Ctrl-click bypasses the threshold filter on intent). Otherwise no-op.
      if (this._availHighlighted.size > 0) {
        for (const id of this._availHighlighted) this._selectedIds.add(id);
        this._availHighlighted.clear();
      } else {
        for (const g of this._availableGroupings) {
          if (passes(g)) this._selectedIds.add(g.id);
        }
      }
      this._renderPickerPanes();
    },
    addPickerAll() {
      const threshold = this._thresholdDecimal();
      const deltaByGid = new Map();
      for (const d of this._groupingDeltas) deltaByGid.set(d.groupingId, d);
      const passes = (g) => {
        if (threshold <= 0) return true;
        const d = deltaByGid.get(g.id);
        if (!d || d.deltaAbs === null || d.deltaAbs === undefined) return false;
        return d.deltaAbs < threshold;
      };
      for (const g of this._availableGroupings) {
        if (passes(g)) this._selectedIds.add(g.id);
      }
      this._availHighlighted.clear();
      this._renderPickerPanes();
    },
    removePickerSelected() {
      if (this._selHighlighted.size > 0) {
        for (const id of this._selHighlighted) this._selectedIds.delete(id);
        this._selHighlighted.clear();
      }
      this._renderPickerPanes();
    },
    removePickerAll() {
      this._selectedIds.clear();
      this._selHighlighted.clear();
      this._renderPickerPanes();
    },

    // Bulk-upload an .xlsx of groupingIds (one per cell, no header). The
    // picker is loaded against the **initial date** — ids that aren't
    // eligible on that date are reported via the same classify-diagnostic
    // used by Publicar Agrupamentos.
    async onPickerExcelChosen(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const status = $$('pkr-excel-status');
      status.textContent = 'Processando…';
      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      const isUnpub = (this._action === 'unpublish');
      // Despublicar é company-driven, então só exige empresa antes do
      // upload. Publicar continua exigindo a data inicial porque a lista
      // disponível é date-aware.
      const missingPrereq = isUnpub
        ? (!cid || !this._availableGroupings.length)
        : (!cid || !dt || !this._availableGroupings.length);
      if (missingPrereq) {
        const msg = isUnpub
          ? 'selecione empresa primeiro'
          : 'selecione empresa e data inicial primeiro';
        status.textContent = msg;
        alert(isUnpub
          ? 'Selecione a empresa antes de subir os IDs.'
          : 'Selecione empresa e data inicial antes de subir os IDs.');
        ev.target.value = '';
        return;
      }
      const known = new Set(this._availableGroupings.map(g => g.id));
      const r = await _excelToMatchedIds(file, known);
      ev.target.value = '';
      if (!r.ok) {
        status.textContent = `falha: ${r.error}`;
        alert('Falha ao ler o Excel: ' + r.error);
        return;
      }
      let added = 0;
      r.matched.forEach(id => {
        if (!this._selectedIds.has(id)) { this._selectedIds.add(id); added++; }
      });
      const unmatched = r.total - r.matched.length;
      // Clear stale Disponíveis-side highlight pointing at moved rows.
      for (const id of r.matched) this._availHighlighted.delete(id);
      this._renderPickerPanes();

      status.textContent =
        `${added} grouping(s) adicionado(s)` +
        (unmatched ? ` · ${unmatched} ignorado(s)` : '');
    },
    // ── Matrix (month overview) ──────────────────────────────────────────────

    _matrixMonthDates(dateStr) {
      if (!dateStr) return [];
      const [y, m] = dateStr.split('-').map(Number);
      const dates = [];
      // Last business day of previous month
      const prev = new Date(y, m - 1, 0); // day-0 = last day of prev month
      while (prev.getDay() === 0 || prev.getDay() === 6) prev.setDate(prev.getDate() - 1);
      dates.push(prev.toISOString().slice(0, 10));
      // All business days of the current month
      const lastDay = new Date(y, m, 0).getDate();
      for (let d = 1; d <= lastDay; d++) {
        const date = new Date(y, m - 1, d);
        if (date.getDay() !== 0 && date.getDay() !== 6) {
          dates.push(date.toISOString().slice(0, 10));
        }
      }
      return dates;
    },

    _scheduleMatrixRefresh(delay) {
      clearTimeout(this._matrixDebounce);
      this._matrixDebounce = setTimeout(() => this._fetchMatrixData(), delay);
    },

    async _fetchMatrixData() {
      const cid = $$('company').value;
      const dt  = $$('initialDate').value;
      const card    = document.getElementById('pbd-detail-card');
      const loading = document.getElementById('pbd-detail-loading');
      const wrap    = document.getElementById('pbd-detail-table-wrap');

      if (!cid || !dt) {
        this._matrixData = null;
        if (card) card.classList.add('hidden');
        return;
      }

      const dates = this._matrixMonthDates(dt);
      if (!dates.length) return;

      if (card)    card.classList.remove('hidden');
      if (loading) loading.classList.remove('hidden');
      if (wrap)    wrap.classList.add('hidden');

      // Update title with month label
      const [y, mo] = dt.split('-').map(Number);
      const monthName = new Date(y, mo - 1, 1)
        .toLocaleString('pt-BR', { month: 'long', year: 'numeric' });
      const titleEl = document.getElementById('pbd-detail-title');
      if (titleEl) titleEl.textContent =
        `Rentabilidade dos Agrupamentos — ${monthName.charAt(0).toUpperCase() + monthName.slice(1)}`;

      // One request per date (published=all returns both states in one call)
      const results = await Promise.all(dates.map(d =>
        api('GET',
          `/api/beehus/filters/grouping-return-deltas` +
          `?companyId=${encodeURIComponent(cid)}` +
          `&positionDate=${encodeURIComponent(d)}` +
          `&published=all`
        ).then(r => ({ d, items: (r.body && Array.isArray(r.body)) ? r.body : [] }))
         .catch(() => ({ d, items: [] }))
      ));

      // Build: gid → {name, byDate: {dateStr → {deltaAbs, published}}}
      const matrix = {};
      for (const { d, items } of results) {
        for (const item of items) {
          const gid = item.groupingId;
          if (!gid) continue;
          if (!matrix[gid]) matrix[gid] = { name: item.groupingName || gid, byDate: {} };
          matrix[gid].byDate[d] = {
            deltaAbs:  item.deltaAbs,
            published: !!item.published,
          };
        }
      }

      this._matrixData    = matrix;
      this._matrixSortCol = null;
      this._matrixSortDir = 'asc';
      if (loading) loading.classList.add('hidden');
      this._renderMatrix();
    },

    onMatrixFilterChange(filter) {
      this._matrixFilter = filter;
      this._renderMatrix();
    },

    onMatrixSortChange(col) {
      if (col === null) {
        if (this._matrixSortCol === null) {
          this._matrixSortDir = (this._matrixSortDir === 'asc') ? 'desc' : 'asc';
        } else {
          this._matrixSortCol = null;
          this._matrixSortDir = 'asc';
        }
      } else {
        if (this._matrixSortCol === col) {
          const cur = typeof this._matrixSortDir === 'number' ? this._matrixSortDir : -1;
          this._matrixSortDir = (cur + 1) % 4;
        } else {
          this._matrixSortCol = col;
          this._matrixSortDir = 0;
        }
      }
      this._renderMatrix();
    },

    _renderMatrix() {
      const card  = document.getElementById('pbd-detail-card');
      const wrap  = document.getElementById('pbd-detail-table-wrap');
      const thead = document.getElementById('pbd-detail-thead');
      const tbody = document.getElementById('pbd-detail-tbody');
      const loading = document.getElementById('pbd-detail-loading');

      if (!this._matrixData || !Object.keys(this._matrixData).length) {
        if (card) card.classList.add('hidden');
        return;
      }
      // Skip if still loading
      if (loading && !loading.classList.contains('hidden')) return;

      const threshold = this._thresholdDecimal();
      const filter    = this._matrixFilter;
      const sortCol   = this._matrixSortCol;
      const sortDir   = this._matrixSortDir;
      const dt        = $$('initialDate').value;

      // Infer date list from matrix keys (union of all dates across rows)
      const dateSet = new Set();
      for (const g of Object.values(this._matrixData)) {
        for (const d of Object.keys(g.byDate)) dateSet.add(d);
      }
      const dates = Array.from(dateSet).sort();

      // Determine "published" for a row: ALL dates with data are published
      const isPubEverywhere = (row) => {
        const vals = Object.values(row.byDate);
        return vals.length > 0 && vals.every(c => c.published);
      };

      let rows = Object.entries(this._matrixData)
        .map(([gid, g]) => ({ gid, name: g.name, byDate: g.byDate }));

      if (filter === 'published')   rows = rows.filter(isPubEverywhere);
      if (filter === 'unpublished') rows = rows.filter(r => !isPubEverywhere(r));

      // Color ID per cell: 0=verde,1=roxo,2=amarelo,3=vermelho,4=cinza,5=vazio
      const getColorId = (row, col) => {
        const cell = row.byDate[col];
        if (!cell) return 5;
        const { deltaAbs, published } = cell;
        const hasData = deltaAbs !== null && deltaAbs !== undefined && isFinite(deltaAbs);
        const above = threshold > 0 && hasData && deltaAbs >= threshold;
        const below = threshold > 0 && hasData && deltaAbs < threshold;
        if (below &&  published) return 0;
        if (below && !published) return 1;
        if (above &&  published) return 2;
        if (above && !published) return 3;
        return published ? 4 : 5;
      };

      const deltaVal = (row, col) => {
        const c = row.byDate[col];
        return (c && c.deltaAbs !== null && c.deltaAbs !== undefined && isFinite(c.deltaAbs))
          ? c.deltaAbs : -Infinity;
      };

      rows.sort((a, b) => {
        if (!sortCol) {
          const cmp = (a.name || '').localeCompare(b.name || '', 'pt-BR');
          return sortDir === 'desc' ? -cmp : cmp;
        }
        const cycle = typeof sortDir === 'number' ? sortDir % 4 : 0;
        const prio  = this.COLOR_CYCLES[cycle];
        const pa = prio[getColorId(a, sortCol)];
        const pb = prio[getColorId(b, sortCol)];
        if (pa !== pb) return pa - pb;
        const da = deltaVal(a, sortCol), db = deltaVal(b, sortCol);
        if (da !== db) return db - da;
        return (a.name || '').localeCompare(b.name || '', 'pt-BR');
      });

      const fmtPct = (v) => {
        if (v === null || v === undefined || !isFinite(v)) return null;
        return (v * 100).toLocaleString('pt-BR', {
          minimumFractionDigits: 4, maximumFractionDigits: 4,
        }) + '%';
      };

      const _cycleColors = ['#16a34a', '#eab308', '#dc2626', '#7c3aed'];

      // Header
      if (thead) {
        const nameIcon = !sortCol ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
        const hdCols = dates.map(d => {
          const [, mm, dd] = d.split('-');
          const isRef    = (d === dt);
          const isSorted = (sortCol === d);
          let icon = '';
          if (isSorted && typeof sortDir === 'number') {
            icon = ` <span style="color:${_cycleColors[sortDir % 4]}">▼</span>`;
          }
          const base = 'py-1 px-1.5 text-center whitespace-nowrap cursor-pointer select-none border-b';
          const cls  = isRef
            ? `${base} bg-blue-50 text-blue-700 hover:bg-blue-100`
            : `${base} hover:bg-gray-50 ${isSorted ? 'text-gray-700 font-semibold' : 'text-gray-400'}`;
          return `<th class="${cls}" style="min-width:4.8rem"
                      onclick="PublishDates.onMatrixSortChange('${d}')">${dd}/${mm}${icon}</th>`;
        }).join('');
        thead.innerHTML =
          `<tr class="text-[10px] uppercase tracking-wide">
            <th class="py-1 pr-3 text-left sticky left-0 bg-white z-10 border-r border-b border-gray-200 cursor-pointer select-none hover:bg-gray-50 ${!sortCol ? 'text-gray-700 font-semibold' : 'text-gray-400'}"
                style="min-width:13rem"
                onclick="PublishDates.onMatrixSortChange(null)">Agrupamento${nameIcon}</th>
            ${hdCols}
          </tr>`;
      }

      // Body
      if (tbody) {
        tbody.innerHTML = rows.map(r => {
          const cells = dates.map(d => {
            const cell = r.byDate[d];
            const pct  = cell ? fmtPct(cell.deltaAbs) : null;
            let bg = '', txt = 'text-gray-300';
            if (cell) {
              const { deltaAbs, published } = cell;
              const hasData = deltaAbs !== null && deltaAbs !== undefined && isFinite(deltaAbs);
              const above   = threshold > 0 && hasData && deltaAbs >= threshold;
              const below   = threshold > 0 && hasData && deltaAbs < threshold;
              if      (below &&  published) { bg = 'bg-green-200';  txt = 'text-green-800 font-semibold'; }
              else if (below && !published) { bg = 'bg-purple-100 border-l border-r border-purple-200'; txt = 'text-purple-700'; }
              else if (above &&  published) { bg = 'bg-yellow-200'; txt = 'text-yellow-800 font-semibold'; }
              else if (above && !published) { bg = 'bg-red-200';    txt = 'text-red-800 font-semibold'; }
              else if (published)           { bg = 'bg-gray-100';   txt = 'text-gray-500'; }
            }
            return `<td class="${bg} ${txt} py-0.5 px-1.5 text-center border-b border-gray-100">${pct || '—'}</td>`;
          }).join('');
          const escName = this._esc(r.name);
          return `<tr class="hover:bg-gray-50">
            <td class="py-0.5 pr-3 text-left sticky left-0 bg-white z-10 border-r border-b border-gray-100"
                style="min-width:13rem" title="${escName}">${escName}</td>
            ${cells}
          </tr>`;
        }).join('');
      }

      // Subtitle count
      const sub = document.getElementById('pbd-detail-subtitle');
      if (sub) sub.textContent = `${rows.length} agrupamento${rows.length !== 1 ? 's' : ''}`;

      if (wrap) wrap.classList.remove('hidden');
    },
  });
})();

/* ────────────────────────────────────────────────────────────────────────────
   PublishDates mode toggle — same single/range pattern used by ProcessDates,
   NavWalletsDates and NavGroupingsDates. Layered AFTER the picker IIFE so it
   wraps the picker's own overrides of `confirm` / `reset` (i.e. the picker
   keeps working in both modes; the mode wrapper only short-circuits range
   validation when the user picked single mode).
   ────────────────────────────────────────────────────────────────────────── */
(function _augmentPublishDatesMode() {
  const $$ = (s) => document.getElementById(`pbd-${s}`);
  const _origResolveDays = PublishDates._resolveDays.bind(PublishDates);
  const _origReset       = PublishDates.reset.bind(PublishDates);
  const _origConfirm     = PublishDates.confirm.bind(PublishDates);

  Object.assign(PublishDates, {
    _mode: 'single',

    onModeChange(mode) {
      this._mode = (mode === 'range') ? 'range' : 'single';
      const finalWrap = $$('finalDate-wrap');
      if (finalWrap) finalWrap.classList.toggle('hidden', this._mode !== 'range');
      const lbl = $$('initialDate-label');
      if (lbl) lbl.innerHTML = (this._mode === 'range')
        ? 'Data inicial <span class="text-red-500">*</span>'
        : 'Data da posição <span class="text-red-500">*</span>';
      const dCard = $$('dates-card');
      if (dCard) dCard.classList.toggle('hidden', this._mode !== 'range');
      if (this._mode === 'single') {
        const ud = $$('use-date-list');
        if (ud && ud.checked) {
          ud.checked = false;
          this.onUseDateListChange();
        }
        const fd = $$('finalDate'); if (fd) fd.value = '';
      }
    },

    _resolveDays() {
      if (this._mode === 'single') {
        const ini = $$('initialDate').value;
        return ini ? [ini] : [];
      }
      return _origResolveDays();
    },

    confirm() {
      // Range mode delegates to the original (which is itself wrapped by the
      // picker IIFE, so picker-summary still gets injected). Single mode
      // skips the "informe data final" check and writes the modal text.
      if (this._mode !== 'single') return _origConfirm();
      if (this._running) { alert('Já existe um fluxo em execução.'); return; }
      const cid = $$('company').value;
      const ini = $$('initialDate').value;
      if (!cid) { alert('Selecione uma empresa.'); return; }
      if (!ini) { alert('Informe a data da posição.'); return; }

      $$('confirm-company').textContent = $$('company').selectedOptions[0]?.textContent || cid;
      $$('confirm-range').textContent   = ini;
      $$('confirm-days').textContent    = '1';
      $$('confirm-modal').classList.add('show');
    },

    reset() {
      _origReset();
      this._mode = 'single';
      const radios = document.querySelectorAll('input[name="pbd-mode"]');
      radios.forEach(r => { r.checked = (r.value === 'single'); });
      this.onModeChange('single');
    },
  });

  if (typeof window !== 'undefined') {
    const apply = () => {
      if (document.getElementById('pbd-finalDate-wrap')) {
        PublishDates.onModeChange('single');
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', apply);
    } else {
      apply();
    }
  }
})();

/* ════════════════════════════════════════════════════════════════════════════
   Boot
═════════════════════════════════════════════════════════════════════════════ */
// Token is set once a day from its origin — refresh the badge on page load
// and again only after the user triggers an action that actually uses the
// token (Create/Delete txn, Process positions). No periodic polling.
Token.refresh();

// Open the view named in the URL hash (e.g. #delete) — falls back to menu.
// The legacy 'menu' aggregator was removed: chips in the Issues
// favorites-bar always pass a specific deep view in the URL hash. If
// somehow no hash arrives (direct visits to /beehus, malformed link),
// fall back to 'identify' — the most common landing tool — so the
// operator never sees a blank page. View.show silently no-ops on an
// unknown view, so an out-of-range hash is harmless either way.
// `const IdentifyTxn` is a script-scoped lexical binding, so it is NOT a
// property of `window`. The Painel de Controle (parent frame) reaches into
// this iframe via `contentWindow.IdentifyTxn` to drive the TXN drill-down, so
// expose it explicitly.
window.IdentifyTxn = IdentifyTxn;

const _initialView = location.hash.slice(1);
const _allowed = ['identify', 'delete-prov', 'delete-pos', 'explosion-prop', 'unpublish-nav', 'process-dates', 'nav-wallets-dates', 'nav-groupings-dates', 'publish-dates'];
const _bootView = _allowed.includes(_initialView) ? _initialView : 'identify';
View.show(_bootView);

// Painel de Controle "TXN" drill-down: ?companyId=&date= pre-fills the
// Identificar Transações search and runs it. Only the identify view honours
// these params (it's the default landing view, so a dropped deep-hash still
// arrives here).
if (_bootView === 'identify') {
  const _q = new URLSearchParams(location.search);
  const _cid = _q.get('companyId');
  const _date = _q.get('date');
  if (_cid && _date) IdentifyTxn.prefillFromPainel(_cid, _date);
}
