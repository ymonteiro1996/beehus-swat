/* Precificação — estado + rótulos + helpers (escHtml/fmtNum).
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  // ── State ──────────────────────────────────────────────────────────────────
  let _selectedSec     = null;
  let _addedSecurities = [];
  let _resultsData     = null;
  let _txnRows         = [];

  const CALC_TYPE_LABELS = {
    pos_fixado:       "Pós-fixado",
    pre_fixado_curva: "Pré-fixado Curva",
    inflacao_curva:   "Benchmark +",
  };

  // ── Helpers ────────────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function fmtNum(v, d = 8) {
    return v == null ? "—" : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: d, maximumFractionDigits: d});
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  loadCompanies();
  loadSavedLists();

  // ── Companies & Wallets ────────────────────────────────────────────────────
