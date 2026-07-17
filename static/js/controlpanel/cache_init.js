/* Painel de Controle — cache, complemento, rebuild e init (auto-seleção).
   Escopo global; usa consts do bootstrap; ordem importa. */
  function _showCacheBanner(msg) {
    const b = document.getElementById("cache-banner");
    document.getElementById("cache-banner-msg").textContent = msg;
    b.style.display = "flex";
  }
  function _hideCacheBanner() { document.getElementById("cache-banner").style.display = "none"; }

  function _updateCacheBadge(data) {
    const el = document.getElementById("cache-badge");
    const text = data.loaded
      ? `Cache: ${data.count} securities (${data.loadedDate})`
      : '';
    if (el) {
      if (text) { el.textContent = text; el.classList.remove("hidden"); }
      else      { el.classList.add("hidden"); }
    }
    // Mirror to shell sidebar (visible cache slot lives there).
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({type: 'beehus-cache-msg', text}, '*');
      }
    } catch (e) {}
  }

  function refreshCache() {
    const btn = document.getElementById("cache-btn");
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    _showCacheBanner("Atualizando cache de securities…");
    fetch("/api/controlpanel/refresh-cache", { method: "POST" })
      .then(r => r.json())
      .then(data => {
        _hideCacheBanner();
        btn.innerHTML = `&#10003; ${data.count} securities`;
        _updateCacheBadge({ loaded: true, count: data.count, loadedDate: data.date });
        setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 3000);
      })
      .catch(() => {
        _hideCacheBanner();
        btn.innerHTML = "Erro!";
        setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 3000);
      });
  }

  // ── Complement upload modal ────────────────────────────────────────────────
  let _complementFile = null;

  function _openComplementModal() {
    document.getElementById('complement-modal').style.display = 'flex';
  }

  function _closeComplementModal() {
    document.getElementById('complement-modal').style.display = 'none';
    _complementFile = null;
    document.getElementById('complement-file-input').value = '';
    const nameEl = document.getElementById('complement-filename');
    nameEl.textContent = '';
    nameEl.classList.add('hidden');
    document.getElementById('complement-confirm-btn').disabled = true;
    const dz = document.getElementById('complement-dropzone');
    dz.classList.remove('border-slate-400', 'bg-slate-50');
    dz.classList.add('border-gray-300');
  }

  (function () {
    function _onComplementFile(file) {
      if (!file) return;
      _complementFile = file;
      const nameEl = document.getElementById('complement-filename');
      nameEl.textContent = file.name;
      nameEl.classList.remove('hidden');
      document.getElementById('complement-confirm-btn').disabled = false;
      const dz = document.getElementById('complement-dropzone');
      dz.classList.add('border-slate-400', 'bg-slate-50');
      dz.classList.remove('border-gray-300');
    }

    document.getElementById('complement-file-input').addEventListener('change', function (e) {
      _onComplementFile(e.target.files[0]);
    });

    const dz = document.getElementById('complement-dropzone');
    dz.addEventListener('dragover', function (e) {
      e.preventDefault();
      dz.classList.add('border-slate-500', 'bg-slate-100');
    });
    dz.addEventListener('dragleave', function () {
      dz.classList.remove('border-slate-500', 'bg-slate-100');
    });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('border-slate-500', 'bg-slate-100');
      const file = e.dataTransfer.files[0];
      if (file) {
        // sync file-input so the change event fires consistently
        try {
          const dt = new DataTransfer();
          dt.items.add(file);
          document.getElementById('complement-file-input').files = dt.files;
        } catch (_) {}
        _onComplementFile(file);
      }
    });
  })();

  // Check cache on page load — warmup classifier + cache if needed
  fetch("/api/controlpanel/cache-status").then(r => r.json()).then(data => {
    _updateCacheBadge(data);
    const needsWarmup = data.stale || !data.loaded || !data.classifierReady;
    if (needsWarmup) {
      const msg = (data.stale || !data.loaded)
        ? "Primeira execução do dia — carregando cache de securities…"
        : "Preparando classificador…";
      _showCacheBanner(msg);
      fetch("/api/controlpanel/warmup", { method: "POST" })
        .then(r => r.json())
        .then(d => {
          _hideCacheBanner();
          if (d.ok) _updateCacheBadge({ loaded: true, count: d.count, loadedDate: new Date().toISOString().slice(0,10) });
        })
        .catch(() => _hideCacheBanner());
    }
  });

  // ── Rebuild classifier ─────────────────────────────────────────────────────
  function rebuildClassifier() {
    const btn = document.getElementById("rebuild-btn");
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg class="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Recalculando…`;
    fetch("/api/controlpanel/rebuild-mapping", { method: "POST" })
      .then(r => r.json())
      .then(({ total, mapped }) => {
        btn.innerHTML = `&#10003; ${mapped} mapeados de ${total}`;
        // refresh cached types
        fetch("/api/controlpanel/security-types").then(r => r.json()).then(d => { _securityTypes = d.types || []; });
        setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 4000);
      })
      .catch(() => {
        btn.innerHTML = "Erro!";
        setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 3000);
      });
  }

  // auto-select today − default_delay business days
  const cards = document.querySelectorAll(".date-card");
  if (cards.length) {
    const delay = _DEFAULT_DELAY;
    const idx   = Math.max(0, cards.length - 1 - delay);
    selectDate(cards[idx].dataset.date);
    const picker = document.getElementById("end-date-picker");
    if (picker) picker.value = cards[cards.length - 1].dataset.date;
  }

  /* ════════════════════════════════════════════════════════════════════════
     Token + Log — duplicated from templates/beehus_console.html so the user
     can manage the Beehus bearer token and see API calls without leaving
     this page. The backend /api/beehus/token store is process-global, so
     pasting the token here also exposes it to the Funções page (and any
     other page that calls the Beehus client). ApiLog is page-local.
     ══════════════════════════════════════════════════════════════════════ */
  const ApiLog = {
    entries: [],
    total: 0,
    MAX_STORED: 1000,
    add(method, path, status, body, durationMs) {
      this.total += 1;
      this.entries.unshift({ts: new Date(), method, path, status, body, durationMs});
      if (this.entries.length > this.MAX_STORED) this.entries.length = this.MAX_STORED;
      this.updateBadge();
    },
    clear() { this.entries.length = 0; this.total = 0; this.updateBadge(); this.render(); },
    async copy() {
      if (!this.entries.length) { alert('Nada a copiar — o log está vazio.'); return; }
      const payload = {
        exportedAt: new Date().toISOString(),
        total: this.total, stored: this.entries.length, maxStored: this.MAX_STORED,
        entries: this.entries,
      };
      const text = JSON.stringify(payload, null, 2);
      const btn = document.getElementById('log-copy-btn');
      const restore = () => { if (btn) btn.textContent = 'Copiar'; };
      try {
        await navigator.clipboard.writeText(text);
        if (btn) { btn.textContent = `Copiado (${this.entries.length})`; setTimeout(restore, 1500); }
      } catch (e) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        if (ok && btn) { btn.textContent = `Copiado (${this.entries.length})`; setTimeout(restore, 1500); }
        else            { alert('Falha ao copiar: ' + e); }
      }
    },
    updateBadge() {
      const badge = document.getElementById('log-count');
      if (badge) {
        badge.textContent = this.total;
        badge.title = this.total > this.entries.length
          ? `${this.entries.length} mais recentes em memória (total da sessão: ${this.total})`
          : `${this.total} chamada(s) nesta sessão`;
      }
      // Mirror the count to the shell sidebar (templates/shell.html
      // hosts the visible Log button + counter badge — this iframe's
      // own UI was removed when the page header was dropped).
      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({
            type: 'beehus-log-count',
            total: this.total,
            stored: this.entries.length,
          }, '*');
        }
      } catch (e) {}
    },
    open()  { this.render(); document.getElementById('log-modal').classList.add('show'); },
    close() { document.getElementById('log-modal').classList.remove('show'); },
    render() {
      const root = document.getElementById('log-body');
      if (!this.entries.length) { root.innerHTML = '<p class="text-xs text-gray-500">Nenhuma chamada registrada nesta sessão.</p>'; return; }
      const overflow = this.total - this.entries.length;
      const overflowMsg = overflow > 0
        ? `<p class="text-[10px] text-amber-600 mb-2">Mostrando as ${this.entries.length} mais recentes — ${overflow} chamada(s) anteriores foram descartadas (limite de ${this.MAX_STORED}).</p>`
        : '';
      root.innerHTML = overflowMsg + this.entries.map(e => {
        const cls = e.status >= 200 && e.status < 300 ? 'badge-ok' : 'badge-err';
        const ts  = e.ts.toLocaleTimeString('pt-BR');
        const body = typeof e.body === 'string' ? e.body : JSON.stringify(e.body, null, 2);
        return `
          <div class="log-entry">
            <div class="flex items-center justify-between text-xs">
              <span><span class="px-2 py-0.5 rounded ${cls}">${e.status}</span>
                    <strong class="ml-2">${e.method}</strong> ${e.path}</span>
              <span class="text-gray-400">${ts} · ${e.durationMs}ms</span>
            </div>
            <pre class="mt-1 text-gray-600">${body}</pre>
          </div>`;
      }).join('');
    },
  };

  async function api(method, path, body) {
    const t0 = performance.now();
    const opts = {method, headers: {'Content-Type': 'application/json'}};
    if (body !== undefined) opts.body = JSON.stringify(body);
    let status = 0, respBody = null;
    try {
      const r = await fetch(path, opts);
      status = r.status;
      respBody = await r.json().catch(() => null);
      return {ok: r.ok, status, body: respBody};
    } catch (e) {
      respBody = {error: String(e)};
      return {ok: false, status: 0, body: respBody};
    } finally {
      ApiLog.add(method, path, status, respBody, Math.round(performance.now() - t0));
    }
  }

  async function processAllWallets() {
    if (!_currentDate) { alert("Selecione uma data primeiro."); return; }
    if (!confirm(
      `Processar as carteiras que têm posição bruta pronta e ainda não foram processadas, ` +
      `em todas as empresas visíveis, na data ${_currentDate}?\n\n` +
      `Empresas sem nada pendente pra processar são puladas automaticamente. ` +
      `Esta ação chama POST no processamento de posições da API Beehus, empresa por empresa, ` +
      `e não é reversível por esta tela.`
    )) return;

    const btn = document.getElementById("process-all-wallets-btn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg> Processando…`;

    try {
      const r = await api("POST", "/api/controlpanel/process-all-wallets", { date: _currentDate });
      const data = r.body || {};
      if (r.status === 401) {
        alert(
          "Token Beehus não está carregado ou foi rejeitado.\n" +
          "Clique no badge \"Token\" no topo desta página e cole o token do dia."
        );
        return;
      }
      if (!r.ok) {
        alert(`Falha ao processar: ${data.error || `HTTP ${r.status}`}`);
        return;
      }
      const results = data.results || {};
      const companyNames = data.companyNames || {};
      const entries = Object.entries(results);
      const ok = entries.filter(([, res]) => res && res.ok).length;
      const failed = entries.filter(([, res]) => !res || !res.ok);
      const skipped = data.skipped || 0;
      let msg = entries.length
        ? `Processamento disparado: ${ok} de ${entries.length} empresa(s) com sucesso.`
        : `Nenhuma empresa tinha carteira pendente pra processar.`;
      if (skipped) msg += `\n${skipped} empresa(s) sem nada pendente foram puladas.`;
      if (failed.length) {
        const preview = failed.slice(0, 10)
          .map(([cid, res]) => `  • ${companyNames[cid] || cid}: ${res?.error || "falha desconhecida"}`)
          .join("\n");
        const more = failed.length > 10 ? `\n  … +${failed.length - 10} outras` : "";
        msg += `\n\nFalhas:\n${preview}${more}`;
      }
      alert(msg);
      selectDate(_currentDate);
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }

  // ── Quadro da Esteira ────────────────────────────────────────────────────
  // Kanban 100% client-side a partir de _gridRows (já carregado pelo /rows do
  // grid). Uma empresa aparece em TODA etapa onde ainda tem pendência (pode
  // repetir em várias colunas) — bucketar só na primeira etapa escondia
  // pendências reais de etapas seguintes. "Concluído" (bucket final, verde) =
  // zero pendência em todas as etapas — NÃO confundir com a coluna "Published"
  // (etapa de publicação em si; quem aparece lá ainda está pendente nela).
  // GAP fica de fora da sequência (é checagem de qualidade,
  // não etapa bloqueante) — vira só um aviso ⚠ no card.
  const _ESTEIRA_STAGES = [
    { key: "missing_wallet",                  label: "Carteira",          kind: "issue", noun: "carteira(s) faltando" },
    { key: "missing_unprocessed_position",    label: "Posição",           kind: "issue", noun: "posição(ões) pendente(s)" },
    { key: "security_unmapped",               label: "Mapeamento",        kind: "issue", noun: "ativo(s) sem mapear" },
    { key: "security_missing_classification", label: "Classificação",     kind: "issue", noun: "ativo(s) sem classificar" },
    { key: "security_missing_price",          label: "Registro de Preço", kind: "issue", noun: "ativo(s) sem preço" },
    { key: "security_missing_history_price",  label: "Preço para o dia",  kind: "issue", noun: "ativo(s) sem preço do dia" },
    { key: "processed",                       label: "Processar",         kind: "extra", noun: "carteira(s) processada(s)" },
    { key: "nav_wallet",                      label: "NAV Wallet",        kind: "extra", noun: "carteira(s) com NAV" },
    { key: "nav_grouping",                    label: "NAV Grouping",      kind: "extra", noun: "agrupamento(s) com NAV" },
    { key: "published",                       label: "Published",        kind: "extra", noun: "agrupamento(s) publicado(s)" },
  ];

  // {count, total|null} — count/total pro "kind:extra" (ratio, ex. 8/12
  // processadas); count = pendências e total = null pro "kind:issue".
  function _esteiraCounts(row, stage) {
    if (stage.kind === "issue") {
      const cell = (row.cells || []).find(c => c.type === stage.key);
      return { count: cell ? cell.count : 0, total: null };
    }
    const extra = (row.extras || []).find(e => e.key === stage.key);
    return { count: extra ? extra.count : 0, total: extra ? extra.total : null };
  }

  function _esteiraCountLabel(row, stage) {
    const { count, total } = _esteiraCounts(row, stage);
    return total == null ? `${count} ${stage.noun}` : `${count}/${total} ${stage.noun}`;
  }

  function _esteiraPending(row, stage) {
    const { count, total } = _esteiraCounts(row, stage);
    return total == null ? count > 0 : (total > 0 && count < total);
  }

  function _escJs(s) {
    return String(s ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  }

  function openEsteiraModal() {
    const modal = document.getElementById("esteira-modal");
    const body  = document.getElementById("esteira-body");
    document.getElementById("esteira-date").textContent = _currentDate || "";
    modal.style.display = "flex";
    if (!_gridRows.length) {
      body.innerHTML = '<p class="text-xs text-gray-400 py-6 text-center">Carregue uma data primeiro.</p>';
      return;
    }

    // Uma empresa aparece em TODA etapa onde ainda tem pendência — não só na
    // primeira (bucketar só na primeira escondia pendências reais de etapas
    // mais à frente sempre que a empresa também tinha algo pendente antes).
    // "Concluído" (bucket final) = zero pendência em TODAS as etapas.
    const byStage = new Map(_ESTEIRA_STAGES.map(s => [s.key, []]));
    const gapByCompany = {};
    _gridRows.forEach(row => {
      _ESTEIRA_STAGES.forEach(stage => {
        if (_esteiraPending(row, stage)) byStage.get(stage.key).push(row);
      });
      const gapExtra = (row.extras || []).find(e => e.key === "gap");
      if (gapExtra && gapExtra.count > 0) gapByCompany[row.companyId] = gapExtra.count;
    });
    const done = _gridRows.filter(row => !_ESTEIRA_STAGES.some(stage => _esteiraPending(row, stage)));

    const cardHtml = (row, stage) => {
      const onclick = stage
        ? (stage.kind === "issue"
            ? `openModal('${_escJs(row.companyId)}','${_escJs(row.company)}','${_escJs(stage.key)}')`
            : `openCellDetail('${_escJs(row.companyId)}','${_escJs(row.company)}','${_escJs(stage.key)}')`)
        : "";
      const gap = gapByCompany[row.companyId];
      const gapBadge = gap ? `<span class="text-amber-500 flex-shrink-0" title="${gap} carteira(s) com GAP">⚠</span>` : "";
      const countLine = stage
        ? `<span class="block text-[10px] text-gray-400 mt-0.5">${_escHtml(_esteiraCountLabel(row, stage))}</span>` : "";
      return `
        <button ${onclick ? `onclick="${onclick}"` : "disabled"}
          class="w-full text-left px-2 py-1.5 bg-white border border-gray-200 rounded text-[11px] text-gray-700 ${onclick ? "hover:border-blue-300 hover:bg-blue-50 cursor-pointer" : "cursor-default"}"
          title="${_escHtml(row.company)}">
          <span class="flex items-center justify-between gap-1">
            <span class="truncate font-medium">${_escHtml(row.company)}</span>${gapBadge}
          </span>
          ${countLine}
        </button>`;
    };

    const columnHtml = (stage) => {
      const rowsHere = byStage.get(stage.key);
      const totalPending = rowsHere.reduce((sum, row) => {
        const { count, total } = _esteiraCounts(row, stage);
        return sum + (total == null ? count : Math.max(0, total - count));
      }, 0);
      return `
        <div class="flex-shrink-0 w-60 bg-gray-50 rounded-lg border border-gray-200 flex flex-col h-full">
          <div class="px-3 py-2 border-b bg-white rounded-t-lg flex items-center justify-between flex-shrink-0">
            <span class="text-xs font-semibold text-gray-700">${stage.label}</span>
            <span class="text-[10px] font-mono ${rowsHere.length ? "text-amber-600" : "text-gray-400"}"
                  title="${rowsHere.length} empresa(s) · ${totalPending} pendência(s) no total">
              ${rowsHere.length} emp · ${totalPending}
            </span>
          </div>
          <div class="p-2 overflow-y-auto flex-1 space-y-1.5">
            ${rowsHere.length ? rowsHere.map(row => cardHtml(row, stage)).join("")
                              : `<p class="text-[10px] text-gray-400 text-center py-2">—</p>`}
          </div>
        </div>`;
    };

    body.innerHTML = `
      <div class="flex gap-3 h-full overflow-x-auto pb-1">
        ${_ESTEIRA_STAGES.map(columnHtml).join("")}
        <div class="flex-shrink-0 w-60 bg-green-50 rounded-lg border border-green-200 flex flex-col h-full">
          <div class="px-3 py-2 border-b bg-white rounded-t-lg flex items-center justify-between flex-shrink-0">
            <span class="text-xs font-semibold text-green-700" title="Zero pendência em todas as etapas, incluindo publicação — não confundir com a coluna \"Published\" (que é a etapa de publicação em si, ainda pendente pra quem aparece lá)">Concluído</span>
            <span class="text-[10px] font-mono text-green-600">${done.length}</span>
          </div>
          <div class="p-2 overflow-y-auto flex-1 space-y-1.5">
            ${done.length ? done.map(row => cardHtml(row, null)).join("")
                          : `<p class="text-[10px] text-gray-400 text-center py-2">—</p>`}
          </div>
        </div>
      </div>`;
  }

  function closeEsteiraModal() {
    document.getElementById("esteira-modal").style.display = "none";
  }

  const Token = {
    open()  {
      document.getElementById('token-modal').classList.add('show');
      setTimeout(() => document.getElementById('token-input').focus(), 0);
    },
    close() { document.getElementById('token-modal').classList.remove('show'); },
    async refresh() {
      const r = await api('GET', '/api/beehus/token');
      const s = r.body || {};
      const badge = document.getElementById('token-badge');
      const status = document.getElementById('token-status');
      const loaded = !!s.loaded;
      const mins   = Math.floor((s.age_seconds || 0) / 60);
      if (badge) {
        if (loaded) {
          badge.textContent = `Token: carregado (${mins} min)`;
          badge.className = 'text-[10px] px-1.5 py-0.5 rounded badge-ok cursor-pointer hover:ring-2 hover:ring-blue-300';
        } else {
          badge.textContent = 'Token: não definido';
          badge.className = 'text-[10px] px-1.5 py-0.5 rounded badge-err cursor-pointer hover:ring-2 hover:ring-blue-300';
        }
      }
      // Mirror the state onto the Sistema-group Token chip so the operator
      // can see at a glance — green when loaded, red when missing — without
      // opening the modal.
      const chip = document.getElementById('chip-token');
      if (chip) {
        chip.classList.toggle('fb-chip-token-on',  loaded);
        chip.classList.toggle('fb-chip-token-off', !loaded);
      }
      if (status) {
        status.textContent = loaded
          ? `Token ativo há ${mins} minuto(s).`
          : 'Nenhum token carregado. Mapear no sistema irá falhar até você salvar um.';
      }
      // Mirror state to the shell sidebar (visible token entry lives there).
      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({
            type: 'beehus-token-state', loaded, ageMin: mins,
          }, '*');
        }
      } catch (e) {}
    },
    async save() {
      const token = document.getElementById('token-input').value;
      if (!token.trim()) return;
      const r = await api('POST', '/api/beehus/token', {token});
      if (!r.ok) { alert('Erro ao salvar token: ' + (r.body?.error || r.status)); return; }
      document.getElementById('token-input').value = '';
      await this.refresh();
      this.close();
    },
    async clear() {
      await api('DELETE', '/api/beehus/token');
      this.refresh();
    },
  };

  // ESC closes the new beehus modals before falling through to the Painel
  // de Controle modal stack — listener is a separate one (capture phase) so it runs
  // before the existing handler at the top of the file.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const tm = document.getElementById('token-modal');
    const lm = document.getElementById('log-modal');
    if (tm.classList.contains('show')) { tm.classList.remove('show'); e.stopPropagation(); return; }
    if (lm.classList.contains('show')) { lm.classList.remove('show'); e.stopPropagation(); return; }
  }, true);

  // Boot: refresh the token badge once on load. No periodic polling — the
  // age is re-computed after every action that exercises the token.
  Token.refresh();

  // Receive bridged API log entries from Beehus iframes (templates/beehus_console.html
  // postMessages every ApiLog.add through to its parent). Origin check is
  // skipped because Beehus runs at the same origin via Flask, but we still
  // shape-check the payload to ignore stray messages from other sources.
  window.addEventListener('message', (e) => {
    const m = e.data;
    if (!m || typeof m !== 'object') return;
    // Bridged API log entries from Beehus iframes (beehus_console.html).
    if (m.type === 'apiLog') {
      ApiLog.add(m.method || '', m.path || '', m.status || 0, m.body, m.durationMs || 0);
      return;
    }
    // Commands from the parent shell sidebar (templates/shell.html). The
    // visible Token / Log buttons live there; clicking them postMessages
    // us to open the corresponding modal here.
    if (m.type === 'beehus-shell-cmd') {
      if (m.cmd === 'open-token') Token.open();
      else if (m.cmd === 'open-log') ApiLog.open();
      return;
    }
  });

  // ── Funções favorites-bar ──────────────────────────────────────────────
  // Each chip is a launcher for a /beehus deep view. We talk to the parent
  // shell (templates/shell.html) via postMessage; the shell extends its
  // existing `navigate` handler to accept a `deep` field and pre-seeds
  // _lastDeep so the iframe loads at /beehus#<deep>. Inside Beehus,
  // View.show() reads location.hash on init and activates the right form.
  const Funcoes = {
    // One iframe per deep-view, kept alive across chip switches so each
    // tool's form state (selections, search results, in-flight edits)
    // survives hopping between tools. Memory pressure is bounded by two
    // mechanisms instead of an LRU (which would silently destroy work):
    //   1. Idle eviction: tools untouched for >IDLE_THRESHOLD_MS that are
    //      *also* pristine (no non-empty form inputs) get torn down on
    //      the next chip click. Tools with user input — even idle ones —
    //      are preserved.
    //   2. Manual "Reiniciar" chip: explicit, predictable reset for the
    //      active tool when the operator wants a fresh start.
    _frames: {},          // { deepView: HTMLIFrameElement }
    _lastTouched: {},     // { deepView: ms-since-epoch of last activation }
    _currentDeep: null,   // deep view of the currently visible iframe (or null on dashboard)
    _inDaytrade: false,   // true while the inline day-trade panel is the active "tool"
    _inStrip:    false,   // true while the inline Strip (Position Stripping) panel is the active "tool"
    IDLE_THRESHOLD_MS: 30 * 60 * 1000,

    open(btn) {
      const deep = btn?.dataset?.deep;
      if (!deep) return;
      this._showTool(deep, btn);
    },

    showIssues() {
      // Hide every tool iframe but keep them in the DOM — their state
      // (form values, fetched lists) stays in memory so the next chip
      // click finds the tool exactly as the operator left it. The idle
      // sweep still fires so any tool over the threshold + pristine gets
      // collected when we revisit the dashboard.
      this._hideAllFrames();
      this._currentDeep = null;
      this._inDaytrade   = false;
      this._inStrip      = false;
      this._inRepetir    = false;
      this._sweepIdleFrames();
      document.getElementById('tool-view').classList.add('hidden-view');
      // The day-trade view is a sibling inline panel (not an iframe), so
      // it's also hidden when returning to the dashboard. Same goes for
      // o Strip panel e o Repetir Posições panel — todos toggle via
      // .hidden-view, sem tear-down de iframe.
      const dt = document.getElementById('daytrade-view');
      if (dt) dt.classList.add('hidden-view');
      const sp = document.getElementById('strip-view');
      if (sp) sp.classList.add('hidden-view');
      const rp = document.getElementById('repetir-view');
      if (rp) rp.classList.add('hidden-view');
      document.getElementById('controlpanel-view').classList.remove('hidden-view');
      this._setActiveChip(document.getElementById('chip-dashboard'));
      this._syncHeaderActions(null);
      this._syncResetChip();
    },

    // Manual reset action for the Reiniciar chip. Three modes:
    //   - Home view (no tool open, not in day-trade): full page reload so
    //     the operator can recover from a wedged dashboard state.
    //   - Day-trade panel (inline DOM view, not an iframe): delegates to
    //     Daytrade.resetFilters (filter form + results), skipping the
    //     iframe destroy/recreate dance.
    //   - Strip panel (also inline DOM, not an iframe): closes any open
    //     modals and reloads the exception list from /api/excecoes.
    //   - Any other tool: tear down its iframe and create a fresh one at
    //     the same deep view. Destroys unconditionally (no pristine
    //     check) since intent is explicit — confirms first because dirty
    //     form data is the common case and silent destruction would
    //     surprise.
    resetCurrent() {
      if (this._inDaytrade) {
        const dirty = !!document.getElementById('dt-company')?.value
                   || !!this._st_dirty_check_daytrade();
        if (dirty && !confirm('Reiniciar irá limpar os filtros e os resultados do day-trade. Continuar?')) return;
        if (typeof Daytrade !== 'undefined') Daytrade.resetFilters();
        return;
      }
      if (this._inStrip) {
        // Strip has no global filter form to clobber — its setup/apply
        // dialogs are modals over the list. Reset just dismisses them and
        // refreshes the list, so the operator can pick up clean from the
        // catalog of saved exceptions.
        if (typeof Strip !== 'undefined') Strip.resetList();
        return;
      }
      const deep = this._currentDeep;
      if (!deep) {
        // Home view: nothing tool-specific to reset, so refresh the page.
        window.location.reload();
        return;
      }
      const old     = this._frames[deep];
      // _frameSrc is only populated by _showPage, so its presence is the
      // signal that this key is a page (Repetir/Carteira/Configurações)
      // rather than a /beehus deep view. Read BEFORE the iframe teardown
      // since we still need the src to rehydrate.
      const pageSrc = this._frameSrc && this._frameSrc[deep];
      const dirty = old ? !this._isPristine(old) : false;
      if (dirty && !confirm('Reiniciar este tool descartará dados de formulário não salvos. Continuar?')) return;
      // Destroy the existing iframe; the reopen below will recreate it.
      if (old) {
        old.remove();
        delete this._frames[deep];
        delete this._lastTouched[deep];
        if (this._frameSrc) delete this._frameSrc[deep];
      }
      // Pages opened via _showPage live under synthetic keys (e.g.
      // `__repetir__`) that don't match any chip data-deep attribute, and
      // routing them through _showTool would build /beehus?#__repetir__ —
      // which the console doesn't know and silently falls back to the
      // first deep view (Identificar). Re-dispatch on the original opener.
      if (pageSrc) {
        // No chip carries data-deep="__page__"; the originally-active chip
        // is still .active in the DOM (the Reiniciar button doesn't toggle
        // active state), so we can recover it for re-highlighting.
        const chip = document.querySelector('#funcoes-bar .fb-chip.active');
        this._showPage(deep, pageSrc, chip);
      } else {
        const chip = document.querySelector(`.fb-chip[data-deep="${CSS.escape(deep)}"]`);
        this._showTool(deep, chip);
      }
    },

    // Cheap heuristic: any detected groups OR a non-default company select
    // count as "has unsaved work" for the day-trade panel.
    _st_dirty_check_daytrade() {
      try {
        return (Daytrade?._st?.detectedGroups?.length || 0) > 0
            || (Daytrade?._st?.patched?.length        || 0) > 0;
      } catch (e) { return false; }
    },

    // Forwards a click on a header tool-action button into the currently
    // active tool's iframe. Looks up the requested module on the iframe's
    // window and calls the named method (same-origin, so no postMessage).
    callTool(moduleName, methodName) {
      const frame = this._frames[this._currentDeep];
      try {
        const mod = frame?.contentWindow?.[moduleName];
        if (mod && typeof mod[methodName] === 'function') mod[methodName]();
      } catch (e) { /* iframe gone or not yet loaded — silently ignore */ }
    },

    // Show/hide the per-view header buttons based on the active deep view.
    // null/empty hides every tool-action button (used when returning to
    // the dashboard).
    _syncHeaderActions(deep) {
      document.querySelectorAll('[data-tool-action]').forEach(el => {
        el.classList.toggle('hidden', el.dataset.toolAction !== deep);
      });
    },

    // The Reiniciar chip is always visible — its behaviour is dispatched
    // by resetCurrent based on the active view: Home → full page reload,
    // day-trade panel → Daytrade.resetFilters, any other tool → iframe
    // teardown + reopen. Kept as a no-op so existing call sites still
    // work without spreading conditional removal across the file.
    _syncResetChip() { /* chip always visible — behaviour dispatches on view */ },

    _hideAllFrames() {
      for (const f of Object.values(this._frames)) f.style.display = 'none';
    },

    // Pristine = no user-typed value anywhere in the iframe's forms. We
    // intentionally err on the conservative side: any error reading the
    // child document keeps the iframe alive, so we never destroy work
    // because of a transient access glitch.
    _isPristine(frame) {
      try {
        const doc = frame?.contentDocument;
        if (!doc) return true;  // not yet loaded → safe to evict
        const textInputs = doc.querySelectorAll(
          'input[type=text], input[type=number], input[type=date], input[type=search], input[type=email], input[type=tel], textarea'
        );
        for (const el of textInputs) {
          if ((el.value || '').trim()) return false;
        }
        // Selects: any non-default selection
        for (const sel of doc.querySelectorAll('select')) {
          const opts = sel.options || [];
          const defaultIdx = Array.from(opts).findIndex(o => o.defaultSelected);
          const baseline = defaultIdx >= 0 ? defaultIdx : 0;
          if (sel.selectedIndex !== baseline) return false;
        }
        // Checkboxes / radios: any flipped from default
        for (const cb of doc.querySelectorAll('input[type=checkbox], input[type=radio]')) {
          if (cb.checked !== cb.defaultChecked) return false;
        }
        return true;
      } catch (e) {
        return false;
      }
    },

    // Sweep idle, pristine, non-current iframes. Triggered on every chip
    // click and on returning to the dashboard, so memory is bounded as
    // long as the operator interacts with the page; long idle periods
    // (e.g. overnight) get reclaimed on the next interaction rather than
    // by a wakeup timer.
    _sweepIdleFrames() {
      const now = Date.now();
      for (const [deep, frame] of Object.entries(this._frames)) {
        if (deep === this._currentDeep) continue;
        const lastUsed = this._lastTouched[deep] || 0;
        if (now - lastUsed < this.IDLE_THRESHOLD_MS) continue;
        if (!this._isPristine(frame)) continue;
        frame.remove();
        delete this._frames[deep];
        delete this._lastTouched[deep];
      }
    },

    _showTool(deep, btn) {
      const tool   = document.getElementById('tool-view');
      const issues = document.getElementById('controlpanel-view');

      this._hideAllFrames();
      let frame = this._frames[deep];
      if (!frame) {
        // First visit to this tool — create its dedicated iframe. The
        // hash directs beehus_console.html's init code to land on the
        // requested view; we never mutate src after this, so each tool's
        // window stays independent of the others.
        frame = document.createElement('iframe');
        frame.title = 'Funções';
        frame.loading = 'eager';
        frame.style.cssText = 'width:100%;height:100%;border:0;display:block';
        frame.src = '/beehus?_frame=1#' + encodeURIComponent(deep);
        frame.addEventListener('load', () => {
          try {
            const isDark = document.documentElement.classList.contains('dark');
            frame.contentDocument.documentElement.classList.toggle('dark', isDark);
          } catch (e) {}
        }, { once: true });
        tool.appendChild(frame);
        this._frames[deep] = frame;
      } else {
        frame.style.display = 'block';
      }

      this._currentDeep = deep;
      this._inDaytrade  = false;
      this._inStrip     = false;
      this._lastTouched[deep] = Date.now();
      tool.classList.remove('hidden-view');
      issues.classList.add('hidden-view');
      // Hide os panels inline irmãos (Day-trade, Strip, Repetir) se o
      // operador veio de um deles; semântica do click do chip é "switch
      // to me", não "stack".
      const dt = document.getElementById('daytrade-view');
      if (dt) dt.classList.add('hidden-view');
      const sp = document.getElementById('strip-view');
      if (sp) sp.classList.add('hidden-view');
      const rp = document.getElementById('repetir-view');
      if (rp) rp.classList.add('hidden-view');
      this._setActiveChip(btn);
      this._syncHeaderActions(deep);
      this._syncResetChip();
      this._sweepIdleFrames();
    },

    // Loader over the tool iframe — covers the whole "open tool → wait for it
    // to become ready → fetch transactions" sequence so the operator isn't
    // staring at a blank/stale iframe after clicking the TXN counter.
    _showToolLoader(label) {
      const tool = document.getElementById('tool-view');
      if (!tool) return;
      let el = document.getElementById('tool-loader');
      if (!el) {
        el = document.createElement('div');
        el.id = 'tool-loader';
        el.innerHTML =
          '<svg class="w-8 h-8 animate-spin text-blue-600" fill="none" viewBox="0 0 24 24">'
          + '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>'
          + '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>'
          + '<div id="tool-loader-label"></div>';
        tool.appendChild(el);
      }
      document.getElementById('tool-loader-label').textContent = label || 'Carregando…';
      el.classList.add('show');
    },
    _hideToolLoader() {
      const el = document.getElementById('tool-loader');
      if (el) el.classList.remove('show');
    },

    _setActiveChip(activeBtn) {
      document.querySelectorAll('#funcoes-bar .fb-chip').forEach(c => c.classList.remove('active'));
      if (activeBtn) activeBtn.classList.add('active');
    },

    // Open an arbitrary page URL inside the tool-view iframe (below the
    // favorites bar). Used by chips that load full pages instead of
    // /beehus deep views — e.g. Configurações. The page is treated as a
    // long-lived tool (kept in `_frames` and toggled via display:none),
    // identical to _showTool except for the explicit src URL.
    _showPage(key, src, btn) {
      const tool   = document.getElementById('tool-view');
      const issues = document.getElementById('controlpanel-view');
      this._hideAllFrames();
      let frame = this._frames[key];
      if (!frame) {
        frame = document.createElement('iframe');
        frame.title = 'Página';
        frame.loading = 'eager';
        frame.style.cssText = 'width:100%;height:100%;border:0;display:block';
        frame.src = src;
        frame.addEventListener('load', () => {
          try {
            const isDark = document.documentElement.classList.contains('dark');
            frame.contentDocument.documentElement.classList.toggle('dark', isDark);
          } catch (e) {}
        }, { once: true });
        tool.appendChild(frame);
        this._frames[key] = frame;
        this._frameSrc = this._frameSrc || {};
        this._frameSrc[key] = src;
      } else {
        // Pages with dynamic query params (e.g. `__repetir__`'s ?date= and
        // ?targetDate=) need a reload when the caller passes a different src.
        // Compare against the **last requested** src we stored, not against
        // `frame.src` — the browser may normalize/resolve it, breaking the
        // naive string compare. Stable-URL pages (Configurações, Carteira)
        // pass the same src each time and skip the reload path.
        this._frameSrc = this._frameSrc || {};
        if (this._frameSrc[key] !== src) {
          frame.src = src;
          this._frameSrc[key] = src;
        }
        frame.style.display = 'block';
      }
      this._currentDeep = key;
      this._inDaytrade  = false;
      this._inStrip     = false;
      this._inRepetir   = false;
      this._lastTouched[key] = Date.now();
      tool.classList.remove('hidden-view');
      issues.classList.add('hidden-view');
      const dt = document.getElementById('daytrade-view');
      if (dt) dt.classList.add('hidden-view');
      const sp = document.getElementById('strip-view');
      if (sp) sp.classList.add('hidden-view');
      const rp = document.getElementById('repetir-view');
      if (rp) rp.classList.add('hidden-view');
      this._setActiveChip(btn);
      this._syncHeaderActions(null);
      this._syncResetChip();
      this._sweepIdleFrames();
    },

    // Configurações chip: load /settings inside the tool-view iframe so
    // the favorites bar stays visible (operators can return to the Painel
    // de Controle or hop to other tools without navigating the whole shell).
    openSettings(btn) {
      this._showPage('__settings__', '/settings?_frame=1', btn);
    },

    // Carteira chip (Posições group): read-only viewer for
    // processedPosition + provisions + cashAccounts per (wallet, date).
    // Same inline-iframe pattern as Configurações — kept alive so the
    // operator's filter state survives chip-hopping.
    openCarteira(btn) {
      this._showPage('__carteira__', '/carteira?_frame=1', btn);
    },

    // TXN column drill-down: open the inline Identificar Transações tool (same
    // iframe the "Identificar" chip uses, so the favorites bar stays visible —
    // no whole-shell navigation) and run its search pre-filled with this
    // company + date. We reach into the same-origin iframe and call
    // IdentifyTxn.prefillFromPainel directly (same pattern as `callTool`),
    // which works whether the tool was just created or reused for a previous
    // company — so clicking TXN for a different row always re-targets.
    openIdentifyWith(companyId, date) {
      if (!companyId || !date) return;
      const btn = document.querySelector('#funcoes-bar .fb-chip[data-deep="identify"]');
      this._showTool('identify', btn);
      // Loader from the click until the iframe is ready AND its search has
      // returned. prefillFromPainel is async (awaits init → filters → search),
      // so we hide the loader when its promise settles.
      this._showToolLoader('Buscando transações…');
      const frame = this._frames['identify'];
      if (!frame) { this._hideToolLoader(); return; }
      // Poll until the tool iframe has parsed enough to expose
      // IdentifyTxn.prefillFromPainel, then call it once. Polling (instead of a
      // one-shot `load` listener) survives every timing case: a reused frame
      // whose load already fired, a fresh frame, and the spurious about:blank
      // `load` some browsers emit before the real src finishes. prefillFromPainel
      // itself awaits init() so the company <select> is populated before we set
      // it — we only need the function to exist here.
      let tries = 0;
      const MAX = 100;   // ~10s at 100 ms
      const tick = () => {
        try {
          const w = frame.contentWindow;
          if (w && w.IdentifyTxn && typeof w.IdentifyTxn.prefillFromPainel === 'function') {
            Promise.resolve(w.IdentifyTxn.prefillFromPainel(companyId, date))
              .catch(() => {})
              .finally(() => this._hideToolLoader());
            return;
          }
        } catch (e) { /* iframe mid-navigation — retry */ }
        if (++tries < MAX) setTimeout(tick, 100);
        else {
          console.warn('openIdentifyWith: IdentifyTxn never became ready in the tool iframe');
          this._hideToolLoader();
        }
      };
      tick();
    },
  };
  // Note: the Identificar Transações config dialog now lives directly in
  // /settings (Configurações sidebar entry) rather than as a header
  // button on Issues — there is no longer an auto-open hook here.

  /* ══════════════════════════════════════════════════════════════════════
     Daytrade — in-page port of /excecoes > Ajustes day-trade.
     Self-contained: own DOM (#daytrade-view), own state (this._st), own
     render functions. Activated by the Lançamentos > Day-trade chip
     (Daytrade.open) and dismissed via the Voltar button or the Issues
     chip (which calls Funcoes.showIssues, hiding this view too).
     Backend: same `/api/excecoes/intraday/*` endpoints used by /excecoes.
  ══════════════════════════════════════════════════════════════════════ */
