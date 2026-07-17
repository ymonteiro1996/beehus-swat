/* Beehus Console — ApiLog: painel de log de API.
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
const ApiLog = {
  entries: [],
  total: 0,                  // running total of calls made this session
  MAX_STORED: 1000,          // newest N kept in memory; older entries roll off
  add(method, path, status, body, durationMs) {
    this.total += 1;
    this.entries.unshift({ts: new Date(), method, path, status, body, durationMs});
    if (this.entries.length > this.MAX_STORED) this.entries.length = this.MAX_STORED;
    this.updateBadge();
    // Bridge to the parent (Painel de Controle) so the consolidated Log
    // button there sees calls made from inside this Funções iframe. No-op
    // when running standalone (window.parent === window). Wrapped in
    // try/catch so a serialization edge case (e.g. body contains a
    // non-cloneable object) never breaks the local log.
    if (window.parent && window.parent !== window) {
      try {
        window.parent.postMessage({
          type: 'apiLog',
          method, path, status,
          body: (typeof body === 'string') ? body : JSON.parse(JSON.stringify(body ?? null)),
          durationMs,
        }, '*');
      } catch (_e) { /* swallow */ }
    }
  },
  clear() { this.entries.length = 0; this.total = 0; this.updateBadge(); this.render(); },
  async copy() {
    if (!this.entries.length) { alert('Nada a copiar — o log está vazio.'); return; }
    // Newest first (matches the modal order). Dates serialize to ISO strings.
    const payload = {
      exportedAt: new Date().toISOString(),
      total:      this.total,
      stored:     this.entries.length,
      maxStored:  this.MAX_STORED,
      entries:    this.entries,
    };
    const text = JSON.stringify(payload, null, 2);
    const btn = document.getElementById('log-copy-btn');
    const restore = () => { if (btn) btn.textContent = 'Copiar'; };
    try {
      await navigator.clipboard.writeText(text);
      if (btn) { btn.textContent = `Copiado (${this.entries.length})`; setTimeout(restore, 1500); }
    } catch (e) {
      // navigator.clipboard can fail on http or in some sandboxes — fall back
      // to a hidden textarea + execCommand.
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
    // Show the running total, not just what's stored — so you know if older
    // calls have rolled off when total exceeds MAX_STORED.
    badge.textContent = this.total;
    badge.title = this.total > this.entries.length
      ? `${this.entries.length} mais recentes em memória (total da sessão: ${this.total})`
      : `${this.total} chamada(s) nesta sessão`;
  },
  open() { this.render(); document.getElementById('log-modal').classList.add('show'); },
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

/* ── Blocking busy overlay ────────────────────────────────────────────────────
   showBusy(label?) freezes interaction with a darkened overlay + spinner.
   hideBusy() lifts it. Always call hideBusy() in a `finally` so a thrown
   error doesn't strand the user behind the overlay.
*/
function showBusy(label) {
  let el = document.getElementById('busy-overlay');
  if (!el) {
    el = document.createElement('div');
    el.id = 'busy-overlay';
    el.className = 'busy-overlay';
    el.innerHTML = '<div class="busy-spinner"></div><div class="busy-label" id="busy-label"></div>';
    document.body.appendChild(el);
  }
  document.getElementById('busy-label').textContent = label || 'Carregando…';
  el.classList.add('show');
}
function hideBusy() {
  const el = document.getElementById('busy-overlay');
  if (el) el.classList.remove('show');
}

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

/* ════════════════════════════════════════════════════════════════════════════
   Token section
═════════════════════════════════════════════════════════════════════════════ */
