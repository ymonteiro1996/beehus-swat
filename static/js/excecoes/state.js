/* Exceções — estado e helpers de formato.
   Escopo global compartilhado; ordem importa. */
  // ── State ─────────────────────────────────────────────────────────────────
  let _exceptions = [];
  let _setupState = {
    editingId: null,
    companyId: "",
    sourceWalletId: "",
    outputWalletIds: [],
    walletsForCompany: [],   // [{id, name, currencyId}]
    sourceSecurities: [],    // [{unprocessedId, quantity, pu, balance}]
    rules: {},               // unprocessedId -> {selected, addToWalletId, removeFromWalletId, caixa}
  };
  let _apply = {
    exceptionId: null,
    companyId: "",
    plan: null,
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const fmtNum = v => v == null
    ? '<span class="text-gray-300">--</span>'
    : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 6, maximumFractionDigits: 6});
  const fmtMoney = v => v == null
    ? '<span class="text-gray-300">--</span>'
    : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  function escHtml(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
  }
  function todayISO() {
    const d = new Date();
    const y = d.getFullYear(), m = String(d.getMonth()+1).padStart(2,"0"), dd = String(d.getDate()).padStart(2,"0");
    return `${y}-${m}-${dd}`;
  }

  // ── List ──────────────────────────────────────────────────────────────────
