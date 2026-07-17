/* Correções — datas, seleção de data e carga das linhas.
   Escopo global compartilhado com os demais pedaços; ordem importa. */
async function loadDates() {
  const end = document.getElementById("end-date-input").value;
  const qs = end ? `endDate=${encodeURIComponent(end)}` : '';
  const res = await fetch(`/api/correcoes/dates${qs ? '?' + qs : ''}`);
  const { cards } = await res.json();

  document.getElementById("date-cards").innerHTML = (cards || []).map(c => `
    <button data-date="${escHtml(c.date)}" onclick="selectDate('${escHtml(c.date)}')"
      class="date-card border-2 border-gray-200 rounded-xl px-3 py-3 text-left bg-white hover:border-blue-300 transition-colors">
      <p class="card-date text-[10px] font-semibold text-gray-500 truncate">${escHtml(c.date)}</p>
      <p class="card-total text-2xl font-bold ${c.total > 0 ? 'text-gray-700' : 'text-gray-300'} mt-1">${c.total}</p>
      <p class="text-[10px] text-gray-400 mt-0.5">linhas</p>
    </button>`).join("");

  // Preserve current _date if present in the new list, else default to last.
  if (cards && cards.length) {
    const matching = cards.find(c => c.date === _date);
    selectDate((matching || cards[cards.length - 1]).date);
  } else {
    hideResults();
  }
}

function selectDate(date) {
  _date = date;
  document.querySelectorAll(".date-card").forEach(c =>
    c.classList.toggle("active", c.dataset.date === date)
  );
  loadRows();
}

function hideResults() {
  document.getElementById("results").classList.add("hidden");
  document.getElementById("action-bar").classList.add("hidden");
}

/* ── Load rows for selected date (all visible companies) ──────────────────── */
async function loadRows() {
  if (!_date) return;
  document.getElementById("action-status").textContent = "Carregando...";

  const dt = encodeURIComponent(_date);
  const res = await fetch(`/api/correcoes?date=${dt}`);
  const data = await res.json();
  _wallets          = data.wallets          || {};
  _walletCurrencies = data.walletCurrencies || {};
  _walletsByCompany = data.walletsByCompany || {};
  _companies        = data.companies        || {};
  _rows.transactions    = data.transactions    || [];
  _rows.provisions      = data.provisions      || [];
  _rows.deletions       = data.deletions       || [];
  _rows.executionPrices = data.executionPrices || [];

  // Merge Jinja-provided company names for any missing ids.
  for (const [cid, name] of Object.entries(_initialCompanies)) {
    if (!_companies[cid]) _companies[cid] = name;
  }

  _refreshExportCompanyOptions();

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("action-bar").classList.remove("hidden");
  document.getElementById("action-status").textContent = "";
  renderRows();
  // Keep current tab selection; fallback to transactions.
  if (!document.querySelector(".tab-btn.active")) switchTab('tab-txn');
}

/* ── Tab switching ────────────────────────────────────────────────────────── */
