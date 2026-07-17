/* Correções — abas, filtros e renderização das tabelas.
   Escopo global compartilhado com os demais pedaços; ordem importa. */
function switchTab(tabId) {
  document.querySelectorAll(".tab-content").forEach(t => t.classList.add("hidden"));
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".tab-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.tab === tabId);
  });
}

/* ── Column filters ──────────────────────────────────────────────────────── */
function _rowMatchesFilters(row, tabId) {
  const f = _filters[tabId] || {};
  for (const [key, val] of Object.entries(f)) {
    if (!val) continue;
    const needle = val.toLowerCase();
    let hay = "";
    if (key === "companyName") hay = _companies[row.companyId] || row.companyId || "";
    else if (key === "walletName") hay = _wallets[row.walletId] || row.walletId || "";
    else if (key === "currencyId") hay = _walletCurrencies[row.walletId] || row.currencyId || "";
    else if (key === "status") hay = row.inputed ? "inputado" : "pendente";
    else hay = row[key] ?? "";
    if (!String(hay).toLowerCase().includes(needle)) return false;
  }
  return true;
}

function _hasAnyFilter() {
  for (const tab of Object.keys(_filters)) {
    for (const v of Object.values(_filters[tab] || {})) {
      if (v) return true;
    }
  }
  return false;
}

document.addEventListener("input", (e) => {
  if (!e.target.classList.contains("col-filter")) return;
  const tab = e.target.dataset.tab;
  const key = e.target.dataset.key;
  _filters[tab] = _filters[tab] || {};
  _filters[tab][key] = e.target.value || "";
  renderRows();
  document.getElementById("clear-filters-btn").classList.toggle("hidden", !_hasAnyFilter());
});

function clearFilters() {
  _filters = { "tab-txn": {}, "tab-prov": {}, "tab-del": {}, "tab-exec": {} };
  document.querySelectorAll(".col-filter").forEach(el => { el.value = ""; });
  document.getElementById("clear-filters-btn").classList.add("hidden");
  renderRows();
}

/* ── Render tables ────────────────────────────────────────────────────────── */
function renderRows() {
  const txns  = _rows.transactions;
  const provs = _rows.provisions;
  const dels  = _rows.deletions;
  const execs = _rows.executionPrices;
  const visibleTxns  = txns.filter(r => _rowMatchesFilters(r, "tab-txn"));
  const visibleProvs = provs.filter(r => _rowMatchesFilters(r, "tab-prov"));
  const visibleDels  = dels.filter(r => _rowMatchesFilters(r, "tab-del"));
  const visibleExecs = execs.filter(r => _rowMatchesFilters(r, "tab-exec"));

  document.getElementById("badge-txn").textContent  =
    visibleTxns.length === txns.length ? txns.length : `${visibleTxns.length}/${txns.length}`;
  document.getElementById("badge-prov").textContent =
    visibleProvs.length === provs.length ? provs.length : `${visibleProvs.length}/${provs.length}`;
  document.getElementById("badge-del").textContent =
    visibleDels.length === dels.length ? dels.length : `${visibleDels.length}/${dels.length}`;
  document.getElementById("badge-exec").textContent =
    visibleExecs.length === execs.length ? execs.length : `${visibleExecs.length}/${execs.length}`;

  // Transactions
  // Always reset the bulk selector at the end of this block (handled below).
  if (!txns.length) {
    document.getElementById("tbl-txn").style.display = "none";
    document.getElementById("empty-txn").style.display = "";
    document.getElementById("tbody-txn").innerHTML = "";
  } else {
    document.getElementById("tbl-txn").style.display = "";
    document.getElementById("empty-txn").style.display = "none";
    document.getElementById("tbody-txn").innerHTML = visibleTxns.map(r => {
      const i = txns.indexOf(r);
      const inputed = !!r.inputed;
      const statusBadge = inputed
        ? `<span class="inline-flex items-center gap-1 bg-emerald-50 border border-emerald-200 text-emerald-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">✓ Inputada</span>`
        : `<span class="inline-flex items-center gap-1 bg-amber-50 border border-amber-200 text-amber-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">Pendente</span>`;
      // Inputed rows can't be re-sent — omit the checkbox so the bulk
      // selector skips them implicitly without us having to filter again.
      const checkbox = inputed
        ? ''
        : `<input type="checkbox" class="txn-row-cb" data-idx="${i}" onclick="event.stopPropagation(); _onTxnCheckboxChange();">`;
      return `
      <tr class="border-t border-gray-100 hover:bg-blue-50 cursor-pointer" onclick="openEditModal('transactions', ${i})">
        <td class="px-2 py-2.5 text-center" onclick="event.stopPropagation()">${checkbox}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_companies[r.companyId] || r.companyId)}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_wallets[r.walletId] || r.walletId)}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap font-mono text-[11px]">${escHtml(_walletCurrencies[r.walletId] || r.currencyId || '')}</td>
        <td class="px-3 py-2.5 text-gray-700">${escHtml(r.beehusTransactionType)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.operationDate)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.liquidationDate)}</td>
        <td class="px-3 py-2.5 text-right font-mono">${fmtMoney(r.balance)}</td>
        <td class="px-3 py-2.5 text-gray-600">${escHtml(r.description)}</td>
        <td class="px-3 py-2.5 text-gray-400 text-[10px] font-mono">${escHtml(r.securityId)}</td>
        <td class="px-3 py-2.5">${statusBadge}</td>
        <td class="px-3 py-2.5 text-center" onclick="event.stopPropagation()">
          <button onclick="quickDelete('transactions', ${i})" title="Excluir"
            class="text-red-500 hover:text-red-700 text-lg leading-none">&times;</button>
        </td>
      </tr>`;
    }).join("");
  }
  _onTxnCheckboxChange();

  // Provisions
  if (!provs.length) {
    document.getElementById("tbl-prov").style.display = "none";
    document.getElementById("empty-prov").style.display = "";
  } else {
    document.getElementById("tbl-prov").style.display = "";
    document.getElementById("empty-prov").style.display = "none";
    document.getElementById("tbody-prov").innerHTML = visibleProvs.map(r => {
      const i = provs.indexOf(r);
      const inputed = !!r.inputed;
      const statusBadge = inputed
        ? `<span class="inline-flex items-center gap-1 bg-emerald-50 border border-emerald-200 text-emerald-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">✓ Inputada</span>`
        : `<span class="inline-flex items-center gap-1 bg-amber-50 border border-amber-200 text-amber-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">Pendente</span>`;
      const submitBtn = inputed
        ? `<span class="text-[10px] text-gray-400">Já enviada</span>`
        : `<button onclick="event.stopPropagation(); submitProvision(${i})" id="prov-submit-${i}"
            class="bg-blue-600 hover:bg-blue-700 text-white rounded px-2 py-1 text-[10px] font-semibold">
            Enviar via API
          </button>`;
      return `
      <tr class="border-t border-gray-100 hover:bg-blue-50 cursor-pointer" onclick="openEditModal('provisions', ${i})">
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_companies[r.companyId] || r.companyId)}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_wallets[r.walletId] || r.walletId)}</td>
        <td class="px-3 py-2.5 text-gray-700">${escHtml(r.provisionType)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.initialDate)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.liquidationDate)}</td>
        <td class="px-3 py-2.5 text-right font-mono">${fmtMoney(r.balance)}</td>
        <td class="px-3 py-2.5 text-gray-600">${escHtml(r.description)}</td>
        <td class="px-3 py-2.5 text-gray-400 text-[10px] font-mono">${escHtml(r.securityId)}</td>
        <td class="px-3 py-2.5">${statusBadge}</td>
        <td class="px-3 py-2.5 text-center" onclick="event.stopPropagation()">
          <div class="flex items-center justify-center gap-2">
            ${submitBtn}
            <button onclick="quickDelete('provisions', ${i})" title="Excluir"
              class="text-red-500 hover:text-red-700 text-lg leading-none">&times;</button>
          </div>
        </td>
      </tr>`;
    }).join("");
  }

  // Execution prices (MISSING_EXECUTION_PRICE accepts — pushed to upstream API)
  if (!execs.length) {
    document.getElementById("tbl-exec").style.display = "none";
    document.getElementById("empty-exec").style.display = "";
  } else {
    document.getElementById("tbl-exec").style.display = "";
    document.getElementById("empty-exec").style.display = "none";
    document.getElementById("tbody-exec").innerHTML = visibleExecs.map(r => {
      const i = execs.indexOf(r);
      const inputed = !!r.inputed;
      const fmtPrice = v => v == null || v === ''
        ? '<span class="text-gray-300">—</span>'
        : Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 4, maximumFractionDigits: 8});
      const statusBadge = inputed
        ? `<span class="inline-flex items-center gap-1 bg-emerald-50 border border-emerald-200 text-emerald-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">✓ Inputado</span>`
        : `<span class="inline-flex items-center gap-1 bg-amber-50 border border-amber-200 text-amber-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">Pendente</span>`;
      const submitBtn = inputed
        ? `<span class="text-[10px] text-gray-400">Já enviado</span>`
        : `<button onclick="event.stopPropagation(); submitExecutionPrice(${i})" id="exec-submit-${i}"
            class="bg-blue-600 hover:bg-blue-700 text-white rounded px-2 py-1 text-[10px] font-semibold">
            Enviar via API
          </button>`;
      return `
      <tr class="border-t border-gray-100 hover:bg-blue-50 cursor-pointer" onclick="openEditModal('executionPrices', ${i})">
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_companies[r.companyId] || r.companyId)}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_wallets[r.walletId] || r.walletId)}</td>
        <td class="px-3 py-2.5 text-gray-700">${escHtml(r.securityName || r.securityId)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.positionDate)}</td>
        <td class="px-3 py-2.5 text-right font-mono">${fmtPrice(r.executionPrice)}</td>
        <td class="px-3 py-2.5 text-right font-mono text-gray-400">${fmtPrice(r.pu)}</td>
        <td class="px-3 py-2.5">${statusBadge}</td>
        <td class="px-3 py-2.5 text-center" onclick="event.stopPropagation()">
          <div class="flex items-center justify-center gap-2">
            ${submitBtn}
            <button onclick="quickDelete('executionPrices', ${i})" title="Excluir"
              class="text-red-500 hover:text-red-700 text-lg leading-none">&times;</button>
          </div>
        </td>
      </tr>`;
    }).join("");
  }

  // Deletions (MISCLASSIFIED markers — original txns to be excluded from DB)
  if (!dels.length) {
    document.getElementById("tbl-del").style.display = "none";
    document.getElementById("empty-del").style.display = "";
  } else {
    document.getElementById("tbl-del").style.display = "";
    document.getElementById("empty-del").style.display = "none";
    document.getElementById("tbody-del").innerHTML = visibleDels.map(r => {
      const i = dels.indexOf(r);
      return `
      <tr class="border-t border-gray-100 hover:bg-red-50 cursor-pointer" onclick="openEditModal('deletions', ${i})">
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_companies[r.companyId] || r.companyId)}</td>
        <td class="px-3 py-2.5 text-gray-700 whitespace-nowrap">${escHtml(_wallets[r.walletId] || r.walletId)}</td>
        <td class="px-3 py-2.5 text-gray-600 text-[10px] font-mono">${escHtml(r.originalId)}</td>
        <td class="px-3 py-2.5 text-gray-700">${escHtml(r.beehusTransactionType)}</td>
        <td class="px-3 py-2.5 text-gray-500">${escHtml(r.operationDate)}</td>
        <td class="px-3 py-2.5 text-right font-mono">${fmtMoney(r.balance)}</td>
        <td class="px-3 py-2.5 text-gray-400 text-[10px] font-mono">${escHtml(r.securityId)}</td>
        <td class="px-3 py-2.5 text-red-700 font-medium">${escHtml(r.reason)}</td>
        <td class="px-3 py-2.5 text-center" onclick="event.stopPropagation()">
          <button onclick="quickDelete('deletions', ${i})" title="Remover marcação"
            class="text-red-500 hover:text-red-700 text-lg leading-none">&times;</button>
        </td>
      </tr>`;
    }).join("");
  }
}

/* ── Modal: open for edit ────────────────────────────────────────────────── */
