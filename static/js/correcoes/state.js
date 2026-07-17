/* Correções — estado, schemas de campos, helpers e Token.
   Escopo global compartilhado com os demais pedaços; ordem importa. */
/* ── State ────────────────────────────────────────────────────────────────── */
let _date     = '';
let _wallets  = {};   // {walletId: walletName} — flat, across all companies
let _walletCurrencies = {}; // {walletId: currencyId} — live from db.wallets;
                            // matches what the submit endpoints will forward.
let _walletsByCompany = {}; // {companyId: {walletId: walletName}}
let _companies = {};  // {companyId: companyName}
let _rows     = { transactions: [], provisions: [], deletions: [], executionPrices: [] };
let _editing  = null; // {kind, row, isNew}
let _filters  = { "tab-txn": {}, "tab-prov": {}, "tab-del": {}, "tab-exec": {} };


/* ── Field schemas (mirror backend whitelists). `company` is a pseudo-field
 *    that drives the cascading walletId dropdown in the modal. ───────────── */
// `currencyId` is intentionally NOT exposed for user edit — the backend now
// always overwrites it with the wallet's currency from `db.wallets`.
const TXN_FIELDS = [
  {key:"companyId",             label:"Empresa",      type:"company", required:true},
  {key:"walletId",              label:"Carteira",     type:"wallet",  required:true},
  {key:"beehusTransactionType", label:"Tipo",         type:"text",    required:true},
  {key:"securityId",            label:"Security ID",  type:"text"},
  {key:"operationDate",         label:"Data Op.",     type:"date",    required:true},
  {key:"liquidationDate",       label:"Liquidação",   type:"date",    required:true},
  {key:"balance",               label:"Valor",        type:"number",  required:true},
  {key:"description",           label:"Descrição",    type:"text"},
  {key:"entityId",              label:"Entity ID",    type:"text"},
  {key:"inputType",             label:"Input Type",   type:"text"},
  {key:"hide",                  label:"Hide",         type:"checkbox"},
  {key:"comment",               label:"Comentário",   type:"text"},
];
const PROV_FIELDS = [
  {key:"companyId",       label:"Empresa",     type:"company", required:true},
  {key:"walletId",        label:"Carteira",    type:"wallet",  required:true},
  {key:"provisionType",   label:"Tipo",        type:"text",    required:true},
  {key:"securityId",      label:"Security ID", type:"text"},
  {key:"initialDate",     label:"Data Inicial", type:"date",   required:true},
  {key:"liquidationDate", label:"Liquidação",   type:"date",   required:true},
  {key:"balance",         label:"Valor",       type:"number",  required:true},
  {key:"description",     label:"Descrição",   type:"text"},
  {key:"provisionSource", label:"Origem",      type:"text"},
];
const DEL_FIELDS = [
  {key:"companyId",             label:"Empresa",      type:"company", required:true},
  {key:"walletId",              label:"Carteira",     type:"wallet",  required:true},
  {key:"originalId",            label:"Original ID",  type:"text",    required:true},
  {key:"beehusTransactionType", label:"Tipo",         type:"text"},
  {key:"operationDate",         label:"Data Op.",     type:"date"},
  {key:"liquidationDate",       label:"Liquidação",   type:"date"},
  {key:"balance",               label:"Valor",        type:"number"},
  {key:"securityId",            label:"Security ID",  type:"text"},
  {key:"description",           label:"Descrição",    type:"text"},
  {key:"reason",                label:"Motivo",       type:"text"},
];
const EXECPRICE_FIELDS = [
  {key:"companyId",      label:"Empresa",       type:"company", required:true},
  {key:"walletId",       label:"Carteira",      type:"wallet",  required:true},
  {key:"securityId",     label:"Security ID",   type:"text",    required:true},
  {key:"securityName",   label:"Security",      type:"text"},
  {key:"positionDate",   label:"Data Posição",  type:"date",    required:true},
  {key:"executionPrice", label:"Preço sugerido",type:"number",  required:true},
  {key:"pu",             label:"PU usado",      type:"number"},
  {key:"description",    label:"Descrição",     type:"text"},
];
// Map tab ids ↔ backend kind ↔ field schema.
const KIND_BY_TAB  = {"tab-txn": "transactions", "tab-prov": "provisions", "tab-del": "deletions", "tab-exec": "executionPrices"};
const TAB_BY_KIND  = {"transactions": "tab-txn", "provisions": "tab-prov", "deletions": "tab-del", "executionPrices": "tab-exec"};
const FIELDS_BY_KIND = {"transactions": TXN_FIELDS, "provisions": PROV_FIELDS, "deletions": DEL_FIELDS, "executionPrices": EXECPRICE_FIELDS};

/* ── Formatters ───────────────────────────────────────────────────────────── */
const fmtMoney = v =>
  v == null || v === '' ? '<span class="text-gray-300">\u2014</span>'
    : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
                        .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ── Bearer token (shared with /beehus Funções page) ─────────────────────── */
const Token = {
  open()  {
    document.getElementById('token-modal').classList.add('show');
    setTimeout(() => document.getElementById('token-input').focus(), 0);
  },
  close() { document.getElementById('token-modal').classList.remove('show'); },

  async refresh() {
    let s = {};
    try {
      const r = await fetch('/api/beehus/token');
      s = await r.json().catch(() => ({}));
    } catch (_) { /* offline; leave badge as "—" */ }
    const badge  = document.getElementById('token-badge');
    const status = document.getElementById('token-status');
    if (s.loaded) {
      const mins = Math.floor((s.age_seconds || 0) / 60);
      badge.textContent = `Token: carregado (${mins} min)`;
      badge.className = 'text-[11px] px-2 py-1 rounded badge-ok cursor-pointer hover:ring-2 hover:ring-emerald-300';
      status.textContent = `Token ativo há ${mins} minutos.`;
    } else {
      badge.textContent = 'Token: não definido';
      badge.className = 'text-[11px] px-2 py-1 rounded badge-err cursor-pointer hover:ring-2 hover:ring-red-300';
      status.textContent = 'Nenhum token carregado. As chamadas "Enviar via API" irão falhar até você salvar um.';
    }
  },
  async save() {
    const token = document.getElementById('token-input').value;
    if (!token.trim()) return;
    const r = await fetch('/api/beehus/token', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('Erro ao salvar token: ' + (err.error || r.status));
      return;
    }
    document.getElementById('token-input').value = '';
    await this.refresh();
    this.close();
  },
  async clear() {
    await fetch('/api/beehus/token', {method: 'DELETE'});
    this.refresh();
  },
};

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('token-modal').classList.contains('show')) {
    Token.close();
  }
});

/* ── Date pills ──────────────────────────────────────────────────────────── */
