/* Beehus Console — Token: sessão/token.
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
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
    if (s.loaded) {
      const mins = Math.floor((s.age_seconds || 0) / 60);
      badge.textContent = `Token: carregado (${mins} min)`;
      badge.className = 'text-xs px-2 py-1 rounded badge-ok';
      status.textContent = `Token ativo há ${mins} minutos.`;
    } else {
      badge.textContent = 'Token: não definido';
      badge.className = 'text-xs px-2 py-1 rounded badge-err';
      status.textContent = 'Nenhum token carregado. APIs irão falhar até você salvar um.';
    }
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

/* ════════════════════════════════════════════════════════════════════════════
   Identificar Transações — search + suggest beehusTransactionType /
   securityId, then PATCH the chosen rows. The identification heuristic
   itself is server-side (POST /api/beehus/identify-transactions/identify)
   and is currently a stub returning empty suggestions; the UI is built so
   the moment that endpoint starts returning real values, every row's
   "Tipo sugerido" / "Security sugerido" cells light up automatically.
═════════════════════════════════════════════════════════════════════════════ */
