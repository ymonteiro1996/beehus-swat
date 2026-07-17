/* Conciliação (Mov.) — estado, companyNames(boot), helpers, empresa/data, prewarm.
   Escopo global compartilhado; ordem importa. */
/* ── State ──────────────────────────────────────────────────────────────────── */
let _company = "", _companyName = "", _date = "", _rows = [];
let _selWallets = new Set(), _anchorIdx = null, _renderedRows = [];
let _movResults = {};          // walletId -> {state, ...movement}
let _colsCollapsed = true;
let _detSecFilter = true, _detWid = null;   // detalhe: filtro de ativos (igual à Conciliação) + carteira ativa
let _detExtraCols = false;                   // detalhe: colunas de métricas (contrib $/rent PU/rent Contrib/transac/prov) — nascem OCULTAS
let _detIgnoredProvs = new Set();            // detalhe: chaves das provisões ignoradas no NAV/GAP (what-if — não grava)
let _detDupIgnoreWid = null;                 // detalhe: carteira p/ qual já pré-marcamos as recon duplicadas como ignoradas (1×/carteira)
let _detProvByKey = {};                      // detalhe: chave da provisão -> {navContribution, inNav} (populado ao renderizar o bloco)
let _thresholdPct = 0.01, _thresholdTimer = null, _sourceTimer = null;


/* ── Formatting (pt-BR) ─────────────────────────────────────────────────────── */
function fmtNum(v, dec = 2) { if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: dec, maximumFractionDigits: dec }); }
function fmtMoney(v) { return fmtNum(v, 2); }
function fmtPct(v) { if (v === null || v === undefined || v === "") return "—";
  return (Number(v) * 100).toLocaleString("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 4 }) + "%"; }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function nz(v) { return v === null || v === undefined ? 0 : Number(v); }
function _cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s; }

/* ── Datas: alvo default = D+1 dia útil da origem ───────────────────────────── */
function _nextBizDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || ""); if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function _syncTargetDefault() {
  // Ao (re)selecionar a origem, o alvo é SEMPRE reposicionado para +1 dia útil
  // (sobrescreve qualquer valor anterior). O operador ainda pode editar o alvo
  // manualmente depois — só volta a +1 du quando a origem mudar de novo.
  const src = document.getElementById("end-date-input").value;
  const tin = document.getElementById("target-date-input");
  if (src && /^\d{4}-\d{2}-\d{2}$/.test(src)) tin.value = _nextBizDay(src);
}

document.getElementById("end-date-input").value = new Date().toISOString().slice(0, 10);
_syncTargetDefault();

fetch("/api/conciliacao/config").then(r => r.json()).then(cfg => {
  _thresholdPct = Number(cfg.diffThresholdPct ?? 0.01);
  document.getElementById("threshold-input").value = _thresholdPct;
}).catch(() => { document.getElementById("threshold-input").value = _thresholdPct; });

function _setThresholdStatus(t, c) { const el = document.getElementById("threshold-status"); if (el) { el.textContent = t || ""; el.style.color = c || ""; } }
function onThresholdChange() {
  const raw = document.getElementById("threshold-input").value;
  const v = raw === "" ? 0 : Number(raw);
  if (!isFinite(v) || v < 0 || v > 10) { _setThresholdStatus("inválido (0–10)", "#b91c1c"); return; }
  _thresholdPct = v; _applyFilters();   // filtro de divergência é client-side (sem recarregar)
  _setThresholdStatus("salvando…", "#9ca3af");
  clearTimeout(_thresholdTimer);
  _thresholdTimer = setTimeout(() => {
    fetch("/api/conciliacao/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ diffThresholdPct: _thresholdPct }) })
      .then(r => r.json().then(j => ({ ok: r.ok, j }))).then(({ ok, j }) => {
        if (!ok) { _setThresholdStatus((j && j.error) || "erro", "#b91c1c"); return; }
        _setThresholdStatus("✓ salvo", "#059669"); setTimeout(() => _setThresholdStatus("", ""), 1500);
      }).catch(() => _setThresholdStatus("erro", "#b91c1c"));
  }, 300);
}

function onCompanyChange() {
  document.getElementById("mov-detail").classList.add("hidden");   // sai do detalhe
  _company = document.getElementById("company-select").value;
  _companyName = companyNames[_company] || _company;
  _date = ""; _rows = [];
  document.getElementById("table-section").classList.add("hidden");
  document.getElementById("tbody").innerHTML = "";
  document.getElementById("main-table").style.display = "none";
  _loadScopeData();     // (re)carrega groupings/carteiras da empresa p/ o seletor opcional
  _markGridStale(false);
  if (!_company) return;
  _firePrewarm();   // opt-in: aquece o índice de carteiras da empresa recém-escolhida
  // A grade NÃO abre automaticamente: o operador clica "Listar carteiras".
}

/* ── Pré-aquecimento (Nível 1, OPT-IN) ─────────────────────────────────────────
   Aquece em segundo plano os índices de catálogo caros (securities ~2s + carteiras
   da empresa) que o 1º cálculo pagaria em série. Preferência salva em localStorage;
   perguntada 1× na abertura (banner) e alterável pelo toggle "⚡ Pré-aquecer". Só
   dispara com pref "on"; dedup por sessão (índice global 1×, empresa 1×). É
   best-effort e silencioso — nunca bloqueia nem quebra a UI. */
let _warmedGlobal = false;
const _warmedCompanies = new Set();
let _warmingCount = 0, _warmDoneTimer = null;
function _prewarmPref() { try { return localStorage.getItem("cmov_prewarm"); } catch (e) { return null; } }
function _setPrewarmPref(v) { try { localStorage.setItem("cmov_prewarm", v); } catch (e) {} }
/* Spinner do pré-aquecimento: ligado enquanto houver warm em voo (contador cobre o
   global + o por-empresa que podem sobrepor); ao zerar, mostra "✓ pronto" por ~1,5s
   e some. O warm é assíncrono (não trava a UI); o spinner só reflete o estado. */
function _prewarmSpin(on) {
  const s = document.getElementById("prewarm-spin");
  if (!s) return;
  if (on) {
    if (_warmDoneTimer) { clearTimeout(_warmDoneTimer); _warmDoneTimer = null; }
    s.innerHTML = '<span class="spin" style="width:12px;height:12px"></span>aquecendo…';
    s.classList.remove("hidden");
  } else {
    s.innerHTML = '<span style="color:#16a34a;font-weight:600">✓</span> pronto';
    _warmDoneTimer = setTimeout(() => { s.classList.add("hidden"); _warmDoneTimer = null; }, 1500);
  }
}
function _firePrewarm() {
  if (_prewarmPref() !== "on") return;
  const needGlobal = !_warmedGlobal;
  const needCompany = _company && !_warmedCompanies.has(_company);
  if (!needGlobal && !needCompany) return;   // nada novo a aquecer nesta sessão
  _warmedGlobal = true;
  if (_company) _warmedCompanies.add(_company);
  const body = _company ? { companyId: _company } : {};
  _warmingCount++; _prewarmSpin(true);
  fetch("/api/conciliacao-mov/prewarm", { method: "POST",
    headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify(body) })   // resolve QUANDO o warm termina (rota espera)
    .catch(() => {})
    .then(() => { _warmingCount = Math.max(0, _warmingCount - 1); if (_warmingCount === 0) _prewarmSpin(false); });
}
function onPrewarmToggle() {
  const on = document.getElementById("prewarm-cb").checked;
  _setPrewarmPref(on ? "on" : "off");
  if (on) _firePrewarm();
}
function setPrewarm(on) {   // resposta ao banner da 1ª abertura
  _setPrewarmPref(on ? "on" : "off");
  const cb = document.getElementById("prewarm-cb"); if (cb) cb.checked = on;
  const p = document.getElementById("prewarm-prompt"); if (p) p.classList.add("hidden");
  if (on) _firePrewarm();
}
(function _initPrewarm() {
  const pref = _prewarmPref();
  const cb = document.getElementById("prewarm-cb"); if (cb) cb.checked = (pref === "on");
  if (pref === "on") _firePrewarm();            // já opt-in → aquece o índice global já
  else if (pref === null) {                     // nunca escolheu → PERGUNTA na abertura
    const p = document.getElementById("prewarm-prompt"); if (p) p.classList.remove("hidden");
  }
})();

function onSourceDateChange() {
  _syncTargetDefault();
  // Não recarrega automaticamente — o operador clica "Listar carteiras".
  if (!document.getElementById("table-section").classList.contains("hidden")) _markGridStale(true);
}

function _loaderHTML(t) { return `<span class="wallet-loader"><span class="spin"></span>${esc(t || "Carregando...")}</span>`; }

