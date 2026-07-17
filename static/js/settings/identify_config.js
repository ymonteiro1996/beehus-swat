/* Configurações — api/helpers e objeto IdentifyConfig.
   Escopo global compartilhado; ordem importa. */
  // ── Generic JSON fetch wrapper (mirrors the one in /beehus). Returns
  //    {ok, status, body} so callers can branch without try/catching.
  async function api(method, path, body) {
    const opts = {method, headers: {'Content-Type': 'application/json'}};
    if (body !== undefined) opts.body = JSON.stringify(body);
    try {
      const r = await fetch(path, opts);
      const respBody = await r.json().catch(() => null);
      return {ok: r.ok, status: r.status, body: respBody};
    } catch (e) {
      return {ok: false, status: 0, body: {error: String(e)}};
    }
  }

  function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  // Quote a string for a JS attribute (used in onclick="…(${jsAttr(x)})").
  function jsAttr(s) {
    return JSON.stringify(String(s ?? ''));
  }

  // ── Identificar Transações config ─────────────────────────────────────────
  // Two tabs:
  //   • Tipos com security  → /api/beehus/identify-transactions/config (GET/PUT)
  //   • Reforços (CRUD)     → /api/beehus/identify-transactions/reinforcements (GET)
  //                           /api/beehus/identify-transactions/reinforcement (POST/DELETE)
  // Loaded lazily: the types tab loads on page boot; reinforcements load
  // only when their tab is first opened so the Configurações page paints
  // instantly even if the rule file is large.
  const IdentifyConfig = {
    _allTypes: [],
    _typesNeedingSecurity: new Set(),
    _reinforcements: null,         // null until first load
    _editingKey: null,             // when set, the matching row renders inline-edit form

    async init() {
      const r = await api('GET', '/api/beehus/identify-transactions/config');
      const b = r.body || {};
      this._allTypes = Array.isArray(b.allTypes) ? b.allTypes : [];
      this._typesNeedingSecurity = new Set(b.typesNeedingSecurity || []);
      this.renderTypes();
    },

    switchTab(tab) {
      const isTypes = tab === 'types';
      document.getElementById('cfg-pane-types').classList.toggle('hidden', !isTypes);
      document.getElementById('cfg-pane-reinf').classList.toggle('hidden',  isTypes);
      const tt = document.getElementById('cfg-tab-types');
      const tr = document.getElementById('cfg-tab-reinf');
      tt.classList.toggle('cfg-tab-active',   isTypes);
      tt.classList.toggle('cfg-tab-inactive', !isTypes);
      tr.classList.toggle('cfg-tab-active',   !isTypes);
      tr.classList.toggle('cfg-tab-inactive',  isTypes);
      if (!isTypes && this._reinforcements === null) this.loadReinforcements();
    },

    renderTypes() {
      const body = document.getElementById('cfg-types-body');
      const items = (this._allTypes.length
        ? this._allTypes
        : Array.from(this._typesNeedingSecurity)).slice();
      if (!items.length) {
        body.innerHTML = '<p class="text-xs text-gray-400 italic">Nenhum tipo encontrado.</p>';
        return;
      }
      body.innerHTML = items.map(t => `
        <label class="flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-gray-50 cursor-pointer">
          <input type="checkbox" class="cfg-types-chk" data-type="${escHtml(t)}"
                 ${this._typesNeedingSecurity.has(t) ? 'checked' : ''} />
          <span>${escHtml(t)}</span>
        </label>`).join('');
    },

    async saveTypes() {
      const types = Array.from(document.querySelectorAll('.cfg-types-chk'))
        .filter(el => el.checked).map(el => el.dataset.type);
      const r = await api('PUT', '/api/beehus/identify-transactions/config', {typesNeedingSecurity: types});
      if (!r.ok) { alert('Falha ao salvar tipos.'); return; }
      const b = r.body || {};
      this._typesNeedingSecurity = new Set(b.typesNeedingSecurity || []);
      if (Array.isArray(b.allTypes) && b.allTypes.length) this._allTypes = b.allTypes;
      this.renderTypes();
      alert(`Tipos salvos: ${types.length}.`);
    },

    // ── Reinforcements ───────────────────────────────────────────────────
    async loadReinforcements() {
      const r = await api('GET', '/api/beehus/identify-transactions/reinforcements');
      this._reinforcements = r.ok ? ((r.body && r.body.reinforcements) || []) : [];
      this.renderReinforcements();
    },

    renderReinforcements() {
      const tbody = document.getElementById('cfg-reinf-rows');
      const empty = document.getElementById('cfg-reinf-empty');
      const rules = this._reinforcements || [];
      const q = (document.getElementById('cfg-reinf-search')?.value || '').trim().toLowerCase();
      const filtered = q
        ? rules.filter(r =>
            (r.key || '').toLowerCase().includes(q) ||
            (r.beehusTransactionType || '').toLowerCase().includes(q) ||
            (r.securityName || '').toLowerCase().includes(q) ||
            (r.securityMainId || '').toLowerCase().includes(q))
        : rules.slice();
      empty.classList.toggle('hidden', filtered.length > 0);
      tbody.innerHTML = filtered.map(r => this._row(r)).join('') ||
        '<tr><td colspan="6" class="text-center text-[11px] text-gray-400 py-4">—</td></tr>';
    },

    _row(r) {
      const isEditing = this._editingKey === r.key;
      const secLabel = r.securityId
        ? (r.securityMainId
            ? `${escHtml(r.securityMainId)} · ${escHtml(r.securityName || r.securityId)}`
            : escHtml(r.securityName || r.securityId))
        : '<span class="text-gray-400 italic">—</span>';
      const seenLabel = (r.lastSeenAt || '').slice(0, 10);
      if (!isEditing) {
        return `
          <tr>
            <td class="font-mono text-[11px]"
                style="white-space: pre-wrap; word-break: break-word">${escHtml(r.key)}</td>
            <td class="text-[11px]">${escHtml(r.beehusTransactionType) || '<span class="text-gray-400 italic">—</span>'}</td>
            <td class="text-[11px]">${secLabel}</td>
            <td class="text-right text-[11px]">${r.hits || 0}</td>
            <td class="text-[11px] text-gray-500">${escHtml(seenLabel)}</td>
            <td>
              <div class="flex gap-1">
                <button class="cfg-btn cfg-btn-muted" style="padding:1px 6px;font-size:10px"
                        onclick="IdentifyConfig.beginEdit(${jsAttr(r.key)})">✎ Editar</button>
                <button class="cfg-btn cfg-btn-danger" style="padding:1px 6px;font-size:10px"
                        onclick="IdentifyConfig.deleteRule(${jsAttr(r.key)})">Excluir</button>
              </div>
            </td>
          </tr>`;
      }
      // Inline edit form. Snippet renormalised on save; type is a select
      // populated from _allTypes; security identification accepts free-form
      // mainId/name + 24-hex objectId.
      const typeOptions = '<option value="">(nenhum)</option>' +
        (this._allTypes || []).map(t =>
          `<option value="${escHtml(t)}" ${t === r.beehusTransactionType ? 'selected' : ''}>${escHtml(t)}</option>`
        ).join('');
      return `
        <tr class="bg-amber-50">
          <td colspan="6" class="text-[11px]">
            <div class="grid gap-2 p-2">
              <div>
                <label class="text-gray-500 mb-0.5 block">Trecho (chave)</label>
                <textarea id="cfg-reinf-edit-key" rows="2"
                          class="cfg-input font-mono text-[11px]"
                          style="resize: vertical">${escHtml(r.key)}</textarea>
              </div>
              <div class="grid grid-cols-2 gap-2">
                <div>
                  <label class="text-gray-500 mb-0.5 block">Tipo</label>
                  <select id="cfg-reinf-edit-type" class="cfg-input">${typeOptions}</select>
                </div>
                <div>
                  <label class="text-gray-500 mb-0.5 block">SecurityId (24-hex)</label>
                  <input id="cfg-reinf-edit-secid" class="cfg-input font-mono"
                         value="${escHtml(r.securityId)}" placeholder="(opcional)" />
                </div>
                <div>
                  <label class="text-gray-500 mb-0.5 block">Security mainId</label>
                  <input id="cfg-reinf-edit-secmain" class="cfg-input font-mono"
                         value="${escHtml(r.securityMainId)}" />
                </div>
                <div>
                  <label class="text-gray-500 mb-0.5 block">Security name</label>
                  <input id="cfg-reinf-edit-secname" class="cfg-input"
                         value="${escHtml(r.securityName)}" />
                </div>
              </div>
              <div class="flex justify-end gap-2 mt-1">
                <button class="cfg-btn cfg-btn-muted"
                        onclick="IdentifyConfig.cancelEdit()">Cancelar</button>
                <button class="cfg-btn cfg-btn-success"
                        onclick="IdentifyConfig.saveEdit(${jsAttr(r.key)})">Salvar</button>
              </div>
            </div>
          </td>
        </tr>`;
    },

    beginEdit(key) {
      this._editingKey = key;
      this.renderReinforcements();
    },
    cancelEdit() {
      this._editingKey = null;
      this.renderReinforcements();
    },
    async deleteRule(key) {
      if (!confirm(`Excluir o reforço?\n\n${key}`)) return;
      const r = await api('DELETE', '/api/beehus/identify-transactions/reinforcement', {key});
      if (!r.ok) { alert('Falha ao excluir reforço.'); return; }
      await this.loadReinforcements();
    },
    async saveEdit(oldKey) {
      const newKeyRaw = (document.getElementById('cfg-reinf-edit-key').value || '').trim();
      const btt       = (document.getElementById('cfg-reinf-edit-type').value || '').trim();
      const sid       = (document.getElementById('cfg-reinf-edit-secid').value || '').trim();
      const smain     = (document.getElementById('cfg-reinf-edit-secmain').value || '').trim();
      const sname     = (document.getElementById('cfg-reinf-edit-secname').value || '').trim();
      if (!newKeyRaw)       { alert('O trecho não pode ficar vazio.'); return; }
      if (!btt && !sid)     { alert('Defina pelo menos o tipo ou o securityId.'); return; }
      // Mirror the server-side normalisation so we know whether the key
      // actually changed (uppercase + collapse-whitespace + strip-accents).
      const norm = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '')
                          .toUpperCase().replace(/\s+/g, ' ').trim();
      const newKey = norm(newKeyRaw);
      if (newKey !== oldKey) {
        const d = await api('DELETE', '/api/beehus/identify-transactions/reinforcement', {key: oldKey});
        if (!d.ok) { alert('Falha ao renomear (delete).'); return; }
      }
      const r = await api('POST', '/api/beehus/identify-transactions/reinforcement', {
        description:           newKeyRaw,
        beehusTransactionType: btt,
        securityId:            sid,
        securityName:          sname,
        securityMainId:        smain,
      });
      if (!r.ok) { alert('Falha ao salvar reforço.'); return; }
      this._editingKey = null;
      await this.loadReinforcements();
    },
  };

  // ── Company filter ─────────────────────────────────────────────────────────
