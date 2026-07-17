/* Conciliação (Não Proc.) — estado, companyNames(boot), helpers e init.
   Escopo global compartilhado; ordem importa. */
/* ── State ──────────────────────────────────────────────────────────────────── */
let _company = "";
let _companyName = "";
let _date = "";
let _rows = [];
let _walletId = "";
let _walletName = "";
let _pendingList = "";   // deep-link: data cuja lista de divergências carrega só no "Voltar"
let _listDate = "";      // data p/ a qual a lista do Step 1 foi carregada (ressincroniza no "Voltar" se a navegação ←/→ mudou a data)

// Seleção múltipla + diagnóstico em lote da lista do Step 1
let _selWallets = new Set();    // walletIds selecionados (persiste através do filtro)
let _anchorIdx = null;          // âncora p/ seleção por Shift (índice em _renderedRows)
let _renderedRows = [];         // linhas atualmente renderizadas (índice p/ shift-range)
let _diagResults = {};          // walletId -> { state:'loading'|'done'|'error', ... }
let _step1ColsCollapsed = true; // Cota/Quantidade ocultas por padrão (botão "Mostrar colunas")


/* ── Formatting (pt-BR) ─────────────────────────────────────────────────────── */
function fmtNum(v, dec = 2) {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(v) {
  if (v === null || v === undefined || v === "") return "—";
  return (Number(v) * 100).toLocaleString("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 4 }) + "%";
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function nz(v) { return v === null || v === undefined ? 0 : Number(v); }

/* ── Step 1 ─────────────────────────────────────────────────────────────────── */

// Default end-date = today (matches the original conciliação).
document.getElementById("end-date-input").value = new Date().toISOString().slice(0, 10);

// Limiar de divergência (diffThresholdPct, em %): compartilhado via
// data/conciliacao_config.json pela rota /api/conciliacao/config (a mesma que
// a conciliação processada usa). Editar aqui muda o filtro do /rows.
let _thresholdPct = 0.01;
let _thresholdTimer = null;
function _setThresholdStatus(text, color) {
  const el = document.getElementById("threshold-status");
  if (el) { el.textContent = text || ""; el.style.color = color || ""; }
}
fetch("/api/conciliacao/config").then(r => r.json()).then(cfg => {
  _thresholdPct = Number(cfg.diffThresholdPct ?? 0.01);
  document.getElementById("threshold-input").value = _thresholdPct;
}).catch(() => { document.getElementById("threshold-input").value = _thresholdPct; });

