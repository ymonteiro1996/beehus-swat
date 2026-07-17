/* Conciliação (Não Proc.) — detalhe da carteira: securities, pills, transações.
   Escopo global compartilhado; ordem importa. */
function _notPreProcessedHTML() {
  return `<div class="mx-auto max-w-2xl my-8 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-left">
    <div class="flex items-start gap-2.5">
      <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m0 3.75h.008M10.34 3.94l-7.5 12.99A1.5 1.5 0 004.14 19.5h15.72a1.5 1.5 0 001.3-2.57l-7.5-12.99a1.5 1.5 0 00-2.62 0z"/></svg>
      <div>
        <p class="text-sm font-semibold text-amber-800">Posição ainda não pré-processada</p>
        <p class="text-[11px] text-amber-700 mt-1 leading-relaxed">A posição não processada de <b>${esc(_date)}</b> ainda não passou pelo pré-processamento: os ativos não têm <code>securityId</code> resolvido, então não é possível nomeá-los nem vincular transações/provisões. NAV, cota e caixa vêm do <code>navPackage</code> e continuam válidos. A análise por ativo aparece assim que o upstream processar esta data.</p>
      </div>
    </div>
  </div>`;
}

function loadWalletDetail() {
  const tbody = document.getElementById("wm-tbody");
  const table = document.getElementById("wm-table");
  const msg = document.getElementById("wm-msg");
  table.style.display = "none"; msg.style.display = ""; msg.textContent = "Carregando...";
  document.getElementById("wm-cash-pills").innerHTML = `<span class="text-[10px] text-gray-300 italic">Carregando...</span>`;
  { const pt = document.getElementById("prov-inline-table"), pm = document.getElementById("prov-inline-msg");
    if (pt) pt.style.display = "none";
    if (pm) { pm.style.display = ""; pm.textContent = "Carregando provisões..."; } }

  fetch(`/api/conciliacao-unprocessed/wallet-detail?walletId=${encodeURIComponent(_walletId)}&date=${encodeURIComponent(_date)}&companyId=${encodeURIComponent(_company)}`)
    .then(r => r.json()).then(data => {
      if (data.error) { msg.textContent = data.error; renderInlineProvisions([]); _stopCatalogWatch(); return; }
      _wallet = data;
      // Header: id + data anterior. O nome vem do payload — cobre o deep-link
      // (vindo do Tombamento), em que a carteira é aberta sem nome.
      if (data.walletName) {
        _walletName = data.walletName;
        document.getElementById("bc-wallet").textContent = data.walletName;
      }
      document.getElementById("hdr-wallet-id").textContent = data.walletId || "";
      document.getElementById("hdr-former-date").textContent =
        data.formerDate ? "· Data ant.: " + data.formerDate : "";
      // Pills
      renderNavPills(data);
      renderCashPills(data);
      renderEstimate(data);
      // Provisões: vêm no payload do wallet-detail (sem fetch /provisions separado)
      renderInlineProvisions(data.provisions || []);
      // Catálogo aquecendo p/ nomear provisão fora da posição → spinner + refaz ao aquecer
      if (data.catalogWarming) { _showCatalogWarm(true); _watchCatalogWarm(); }
      else { _stopCatalogWatch(); }
      // Securities
      _secData = data.securities || [];
      // Data ainda não pré-processada: NÃO usar o fallback unprocessedId-como-nome
      // — esvazia a tabela e mostra o aviso. NAV/cota/caixa acima seguem válidos.
      if (data.notPreProcessed) {
        _secData = [];
        document.getElementById("wm-tbody").innerHTML = "";
        table.style.display = "none";
        msg.style.display = ""; msg.innerHTML = _notPreProcessedHTML();
        _syncSecFilterBtn();
        return;
      }
      if (!_secData.length) { msg.textContent = "Sem ativos na posição não processada."; return; }
      msg.style.display = "none"; table.style.display = "";
      renderSecurities();
      _syncSecFilterBtn();
      _applyColsCollapsed();
    }).catch(() => { msg.textContent = "Falha ao carregar."; });
}

/* ── Securities table: filter + column toggle (igual ao original) ───────────── */
let _secData = [];
let _secFilterActive = true;   // wallet detail opens já filtrado
let _colsCollapsed = true;     // optional columns hidden by default

// Uma linha tem "atividade" (sobrevive ao filtro) quando há divergência REAL —
// não resíduo de ponto flutuante. As comparações usam as MESMAS tolerâncias dos
// realces visuais da linha: Diff Rent vermelho usa |x|>1e-6, e os valores em R$
// têm 2 casas (meio centavo). O `!== 0` estrito anterior deixava passar linhas
// com diffRent ~1e-8 (arredondado a 8 casas no backend) — visualmente cinza/sem
// divergência, mas tecnicamente ≠ 0 — que então não eram filtradas.
function _secHasActivity(s) {
  return (s.amountDifference   != null && Math.abs(s.amountDifference)   > 1e-6)
      || (s.transactionBalance != null && Math.abs(s.transactionBalance) > 0.005)
      || (s.diffRent           != null && Math.abs(s.diffRent)           > 1e-6)
      || (s.transactionCount   != null && s.transactionCount > 0);
}

function renderSecurities() {
  const data = _secFilterActive ? _secData.filter(_secHasActivity) : _secData;
  document.getElementById("wm-tbody").innerHTML = data.map(renderSecRow).join("");
}

function _syncSecFilterBtn() {
  const btn = document.getElementById("sec-filter-btn");
  if (!btn) return;
  btn.querySelector("svg").outerHTML = _secFilterActive
    ? `<svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z"/></svg>`
    : `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z"/></svg>`;
  btn.lastChild.textContent = _secFilterActive ? " Filtrado" : " Filtrar";
  // Estado ativo no padrão do design (fb-chip.active = azul sólido).
  btn.classList.toggle("active", _secFilterActive);
}

function toggleSecFilter() {
  _secFilterActive = !_secFilterActive;
  _syncSecFilterBtn();
  renderSecurities();
}

function _applyColsCollapsed() {
  // A caixa de rolagem dos Ativos é `.sec-table-wrap` (antes era `.overflow-x-auto`,
  // removida no ajuste de layout). É o ancestral que carrega `cols-collapsed`,
  // sob o qual a regra `.cols-collapsed .col-opt { display:none }` oculta as
  // colunas opcionais.
  const wrap = document.getElementById("wm-table").closest(".sec-table-wrap");
  if (wrap) wrap.classList.toggle("cols-collapsed", _colsCollapsed);
}

function toggleCols() {
  _colsCollapsed = !_colsCollapsed;
  _applyColsCollapsed();
  const btn = document.getElementById("toggle-cols-btn");
  btn.lastChild.textContent = _colsCollapsed ? " Mostrar" : " Esconder";
}

function pill(label, value, cls) {
  return `<div class="bg-white border rounded-lg px-2.5 py-0.5">
    <div class="text-[9px] uppercase tracking-wide text-gray-400">${label}</div>
    <div class="text-xs font-semibold ${cls || "text-gray-700"}">${value}</div>
  </div>`;
}
function renderNavPills(d) {
  const gapBad = d.gapPct != null && Math.abs(nz(d.gapPct)) > 1e-6;
  document.getElementById("wm-nav-pills").innerHTML =
    pill("NAV Anterior", fmtMoney(d.formerNav)) +
    pill("RET. NAV", fmtPct(d.returnNavPerShare)) +
    pill("RET CONTR", fmtPct(d.returnContribution)) +
    pill("Gap %", fmtPct(d.gapPct), gapBad ? "text-red-600" : "text-green-600") +
    pill("Gap R$", fmtMoney(d.gapCash), gapBad ? "text-red-600" : "text-green-600");
}
function renderCashPills(d) {
  const diffBad = d.cashDifference != null && Math.round(nz(d.cashDifference) * 100) !== 0;
  document.getElementById("wm-cash-pills").innerHTML =
    pill("Caixa Ant.", fmtMoney(d.formerCash)) +
    pill("TOT TXN", fmtMoney(d.totalTransactions)) +
    pill("Caixa Projetado", fmtMoney(d.projectedCash)) +
    pill("Caixa Atual", fmtMoney(d.currentCash)) +
    pill("Δ Caixa", fmtMoney(d.cashDifference), diffBad ? "text-red-600" : "text-green-600");
}
// Linha de estimativa: recalcula NAV/cota/retornos/gap desconsiderando os
// lançamentos de "explosão" (ver _explosao_estimate no backend). Some quando
// a carteira não tem nenhum lançamento de explosão na data.
function renderEstimate(d) {
  const el = document.getElementById("wm-estimate");
  const e = d.estimate;
  if (!e) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  const gapBad = e.gapPct != null && Math.abs(nz(e.gapPct)) > 1e-6;
  el.innerHTML =
    `<span class="text-[9px] font-semibold uppercase tracking-wide text-amber-800 bg-yellow-100 border border-yellow-300 rounded px-2 py-1"
       title="Desconsidera ${e.count} lançamento(s) de explosão · Σ ${fmtMoney(e.explosaoTotal)} (fluxo Σ ${fmtMoney(e.explosaoFlow)})">Estimativa s/ explosão</span>`
    + pill("NAV (est)", fmtMoney(e.nav))
    + pill("Cota (est)", fmtNum(e.navPerShare, 8))
    + pill("RET. NAV (est)", fmtPct(e.returnNavPerShare))
    + pill("RET CONTR (est)", fmtPct(e.returnContribution))
    + pill("Gap % (est)", fmtPct(e.gapPct), gapBad ? "text-red-600" : "text-green-600")
    + pill("Gap R$ (est)", fmtMoney(e.gapCash), gapBad ? "text-red-600" : "text-green-600");
}
function renderSecRow(s) {
  const tag = s.isNew ? ` <span class="text-[9px] text-blue-500">(novo)</span>` : "";
  const dqCls = nz(s.amountDifference) !== 0 ? "text-gray-800" : "text-gray-400";
  const dbCls = nz(s.balanceDifference) !== 0 ? "text-gray-800" : "text-gray-400";
  const drBad = s.diffRent !== null && Math.abs(nz(s.diffRent)) > 1e-6 && !s.eventCorrected;
  const drCls = s.eventCorrected ? "text-blue-500" : (drBad ? "text-red-600 font-semibold" : "text-gray-400");
  const evTag = s.eventCorrected ? ' <span class="text-[8px] text-blue-400" title="Diferença explicada por cupom/amortização">ev</span>' : "";
  return `<tr>
    <td class="rp-tbl-left font-medium text-gray-700" title="unprocessedId: ${esc(s.unprocessedId)}">${esc(s.securityName)}${tag}</td>
    <td class="text-gray-700">${fmtMoney(s.balance)}</td>
    <td class="text-gray-600">${fmtNum(s.pu, 6)}</td>
    <td class="text-gray-600">${fmtNum(s.quantity, 4)}</td>
    <td class="text-gray-400 col-opt">${fmtMoney(s.formerBalance)}</td>
    <td class="text-gray-400 col-opt">${fmtNum(s.formerPu, 6)}</td>
    <td class="text-gray-400 col-opt">${fmtNum(s.formerQuantity, 4)}</td>
    <td class="${dqCls}">${fmtNum(s.amountDifference, 4)}</td>
    <td class="${dbCls}">${fmtMoney(s.balanceDifference)}</td>
    <td class="text-gray-600 col-opt">${s.transactionBalance === null ? "—" : fmtMoney(s.transactionBalance)}</td>
    <td class="text-gray-600 col-opt">${s.provisionBalance === null ? "—" : fmtMoney(s.provisionBalance)}</td>
    <td class="text-gray-700">${s.totalContribution === null ? "—" : fmtMoney(s.totalContribution)}</td>
    <td class="text-gray-500 col-opt">${s.eventContribution === null ? "—" : fmtMoney(s.eventContribution)}</td>
    <td class="text-gray-600">${fmtPct(s.returnPU)}</td>
    <td class="text-gray-600">${fmtPct(s.returnContrib)}</td>
    <td class="${drCls}">${fmtPct(s.diffRent)}${evTag}</td>
  </tr>`;
}

/* Seções de transações/provisões sem dados encolhem (flex:0 0 auto), liberando
   altura para a seção de ativos. */
function _setSectionCollapsed(id, collapsed) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("sec-collapsed", collapsed);
}

// Ativos só cresce para preencher a sobra quando NÃO há transações nem
// provisões para recebê-la; caso contrário fica do tamanho do conteúdo
// (reduzido) e a sobra vai para txn/prov.
function _relayoutSections() {
  const tx = document.getElementById("txn-section"),
        pv = document.getElementById("prov-section"),
        at = document.getElementById("ativos-section");
  if (!tx || !pv || !at) return;
  const bothEmpty = tx.classList.contains("sec-collapsed") && pv.classList.contains("sec-collapsed");
  at.classList.toggle("sec-grow", bothEmpty);
}

// Entradas geradas pela "explosão" de contribuição (descrição contém
// "explosão") — destacadas em amarelo e desconsideradas na linha de estimativa.
function _isExplosao(desc) {
  return (desc || "").toLowerCase().includes("explosão");
}

function loadInlineTransactions() {
  const tbody = document.getElementById("txn-inline-tbody");
  const table = document.getElementById("txn-inline-table");
  const msg = document.getElementById("txn-inline-msg");
  table.style.display = "none"; msg.style.display = ""; msg.textContent = "Carregando transações...";
  fetch(`/api/conciliacao-unprocessed/transactions?walletId=${encodeURIComponent(_walletId)}&date=${encodeURIComponent(_date)}&companyId=${encodeURIComponent(_company)}`)
    .then(r => r.json()).then(data => {
      const txns = data.transactions || [];
      _setSectionCollapsed("txn-section", !txns.length);
      _relayoutSections();
      if (!txns.length) { msg.textContent = "Sem transações nesta data."; return; }
      msg.style.display = "none"; table.style.display = "";
      tbody.innerHTML = txns.map(t => {
        const actions = t.txnId
          ? `<td class="whitespace-nowrap" style="text-align:center">
               <button type="button" onclick='openTxnEdit(${_attrJson(t)})' title="Editar transação via API"
                 class="text-[11px] px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-700 hover:bg-blue-100 font-medium">✎</button>
               <button type="button" onclick="deleteWalletTxn('${esc(t.txnId)}', '${esc(t.operationDate || "")}', '${esc(t.liquidationDate || "")}')" title="Excluir transação via API"
                 class="ml-1 text-[11px] px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 font-medium">🗑</button>
             </td>`
          : `<td class="text-gray-300" style="text-align:center">—</td>`;
        return `<tr class="${_isExplosao(t.description) ? "bg-yellow-100" : ""}">
        <td class="rp-tbl-left text-gray-500">${esc(t.description || "")}</td>
        <td class="rp-tbl-left text-gray-600">${t.operationDate || "—"}</td>
        <td class="rp-tbl-left text-gray-600">${t.liquidationDate || "—"}</td>
        <td class="rp-tbl-left text-gray-700">${esc(t.securityName || t.securityId || "—")}</td>
        <td class="rp-tbl-left text-gray-600">${esc(t.beehusTransactionType || "—")}</td>
        <td class="text-gray-600">${t.quantity === null || t.quantity === undefined ? "—" : fmtNum(t.quantity, 4)}</td>
        <td class="text-gray-600">${t.price === null || t.price === undefined ? "—" : fmtNum(t.price, 6)}</td>
        <td class="text-gray-700">${fmtMoney(t.balance)}</td>
        ${actions}
      </tr>`;}).join("");
    }).catch(() => { msg.textContent = "Falha ao carregar transações."; });
}

// Provisões agora chegam no payload do wallet-detail (data.provisions) — não há
// fetch separado a /provisions (que refazia o processed-position à toa). Apenas
// renderiza a lista já recebida.
function renderInlineProvisions(provs) {
  const tbody = document.getElementById("prov-inline-tbody");
  const table = document.getElementById("prov-inline-table");
  const msg = document.getElementById("prov-inline-msg");
  provs = provs || [];
  _setSectionCollapsed("prov-section", !provs.length);
  _relayoutSections();
  if (!provs.length) {
    table.style.display = "none"; msg.style.display = "";
    msg.textContent = "Sem provisões ativas nesta data.";
    return;
  }
  msg.style.display = "none"; table.style.display = "";
  tbody.innerHTML = provs.map(p => {
        const actions = p.provisionId
          ? `<td class="whitespace-nowrap" style="text-align:center">
               <button type="button" onclick='openProvEdit(${_attrJson(p)})' title="Editar provisão via API"
                 class="text-[11px] px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-700 hover:bg-blue-100 font-medium">✎</button>
               <button type="button" onclick="deleteWalletProv('${esc(p.provisionId)}')" title="Excluir provisão via API"
                 class="ml-1 text-[11px] px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 font-medium">🗑</button>
             </td>`
          : `<td class="text-gray-300" style="text-align:center">—</td>`;
        return `<tr class="${_isExplosao(p.description) ? "bg-yellow-100" : ""}">
        <td class="rp-tbl-left text-gray-500">${esc(p.description || "")}</td>
        <td class="rp-tbl-left text-gray-700">${esc(p.securityName || p.securityId || "—")}</td>
        <td class="rp-tbl-left text-gray-600">${p.initialDate || "—"}</td>
        <td class="rp-tbl-left text-gray-600">${p.liquidationDate || "—"}</td>
        <td class="rp-tbl-left text-gray-600">${esc(p.provisionType || "—")}</td>
        <td class="text-gray-700">${fmtMoney(p.balance)}</td>
        ${actions}
      </tr>`;}).join("");
}

/* ── Aquecimento do catálogo (nomes de provisões fora da posição) ──────────────
   Quando o wallet-detail devolve catalogWarming=true, o backend disparou o warm
   do catálogo em background; mostramos o spinner e refazemos a chamada assim que
   o catálogo esfriar→aquecer (poll leve no /catalog-status). */
let _catalogPoll = null;
function _showCatalogWarm(show) {
  const b = document.getElementById("catalog-warm-banner");
  if (b) b.classList.toggle("hidden", !show);
}
function _stopCatalogWatch() {
  if (_catalogPoll) { clearInterval(_catalogPoll); _catalogPoll = null; }
  _showCatalogWarm(false);
}
function _watchCatalogWarm() {
  if (_catalogPoll) return;
  _catalogPoll = setInterval(() => {
    fetch("/api/conciliacao-unprocessed/catalog-status")
      .then(r => r.json()).then(j => {
        if (j && j.warm) {
          _stopCatalogWatch();
          // só refaz se ainda estamos no detalhe da carteira
          if (_walletId && _date && !document.getElementById("step-2").classList.contains("hidden"))
            loadWalletDetail();
        }
      }).catch(() => {});
  }, 2500);
}

// Poll genérico: chama `cb` quando o catálogo aquecer (uma vez). Usado pelo
// diagnóstico (sec_info de ativos de posição sem transação).
function _pollCatalogThen(cb) {
  const iv = setInterval(() => {
    fetch("/api/conciliacao-unprocessed/catalog-status")
      .then(r => r.json()).then(j => { if (j && j.warm) { clearInterval(iv); cb(); } })
      .catch(() => {});
  }, 2500);
}

function copySecurities() {
  const names = (_wallet && _wallet.securities || []).map(s => s.securityName).join("\n");
  navigator.clipboard.writeText(names).then(() => {
    const btn = document.getElementById("copy-sec-btn");
    const old = btn.innerHTML;
    btn.innerHTML = "✓ Copiado";
    setTimeout(() => { btn.innerHTML = old; }, 1500);
  });
}
let _wallet = null;

/* ══════════════════ EDITAR / EXCLUIR txn & provisão via API ══════════════════ */
/* Reutiliza os mesmos endpoints da Conciliação original (source-agnostic):
   /api/conciliacao/{transaction,provision}/{update,delete}. AÇÃO DESTRUTIVA:
   altera/remove o documento real no Beehus (PATCH/DELETE), não é staging. */
function _attrJson(obj) {
  // Escapa & primeiro (senão um literal "&#39;" no dado seria decodificado para
  // ' pelo parser e quebraria o atributo onclick='...'), depois ' e </.
  return JSON.stringify(obj).replace(/&/g, "&amp;").replace(/'/g, "&#39;").replace(/<\//g, "<\\/");
}
function _apiErrMsg(data, status) {
  let msg = (data && data.error) ? data.error : `HTTP ${status}`;
  if (data && data.upstream_status) msg += ` · upstream ${data.upstream_status}`;
  if (data && data.upstream_body) {
    const b = (typeof data.upstream_body === "string") ? data.upstream_body : JSON.stringify(data.upstream_body);
    if (b && b.trim()) msg += `\n\n${b}`;
  }
  return msg;
}
function _refreshDetail() {
  loadWalletDetail();   // re-renderiza as provisões a partir do próprio payload
  loadInlineTransactions();
}

let _txnEditCtx = null;
