  /* ════════════════════════════════════════════════════════════════════
     Tela Carteira (Posições) — script de página.

     Contexto:
     Visualizador read-only de processedPosition + provisões + caixa, por
     (carteira, dia útil) dentro de uma faixa de datas. A página não renderiza
     nada até "Buscar": Carteira.run() faz POST do envelope de filtros em
     /api/carteira/data e reconstrói a seção de resultados a partir da resposta.
     Também permite editar UMA (carteira, data) e reenviar como um novo
     unprocessedSecurityPositions.

     Depende de window.Fmt (static/js/utils/format.js), carregado ANTES deste
     arquivo. Fica exposto como `Carteira` (global) para os handlers inline
     (onclick="Carteira.x()") do template — por isso NÃO envolver em IIFE.
  ════════════════════════════════════════════════════════════════════ */
  const Carteira = {
    _st: {
      mode: "single",                // "single" | "range"
      groupings: [],                 // [{id, name, walletIds: [...]}]
      groupingSelectedIds: new Set(),
      wallets: [],                   // [{id, name}]
      walletSelectedIds:   new Set(),
      lastData: null,                // cached `{wallets, dates}` for re-render
      // Edit-mode state keyed by walletId. Each entry is a working copy
      // produced by `_seedEditState(walletData)` — the source response in
      // `lastData` stays untouched so cancelling reverts cleanly.
      editing: new Map(),
      // Security-search modal is shared (add-row + edit-cell); `_secModal`
      // remembers which flow opened it so the result callback knows
      // whether to mutate an existing row or append a new one.
      _secModal: null,
      _secSearchTimer: null,
      _submitting: false,
    },

    // ── Helpers de formatação (delegam ao util compartilhado window.Fmt) ──
    // Mantidos como métodos finos para NÃO alterar os ~40 call-sites
    // (this._esc / this._fmtMoney / …) e ainda reaproveitar a lógica única do
    // util (regra do CLAUDE.md: comum -> utils/, reusar em vez de duplicar).

    /* Contexto: escapa string para inserção segura em HTML. Delega a Fmt.esc. */
    _esc(s) { return window.Fmt.esc(s); },
    /* Contexto: número pt-BR com `frac` casas, ou "—" se nulo. Delega a Fmt.numberOrDash. */
    _fmtNum(v, frac) { return window.Fmt.numberOrDash(v, frac); },
    /* Contexto: quantidade formatada (6 casas). */
    _fmtQty(v)   { return window.Fmt.qty(v); },
    /* Contexto: PU formatado (6 casas). */
    _fmtPu(v)    { return window.Fmt.pu(v); },
    /* Contexto: valor monetário (2 casas). */
    _fmtMoney(v) { return window.Fmt.money(v); },
    /* Contexto: data de hoje em ISO local 'YYYY-MM-DD'. Delega a Fmt.todayISO. */
    _todayISO() { return window.Fmt.todayISO(); },

};
