/* Correções — modais de adicionar/editar item e o formulário.
   Escopo global compartilhado com os demais pedaços; ordem importa. */
function openEditModal(kind, idx) {
  const row = JSON.parse(JSON.stringify(_rows[kind][idx])); // clone
  _editing = { kind, row, isNew: false };
  const titles = {
    transactions:    'Editar Transação',
    provisions:      'Editar Provisão',
    deletions:       'Editar Exclusão',
    executionPrices: 'Editar Preço de Execução',
  };
  document.getElementById("modal-title").textContent = titles[kind] || 'Editar linha';
  document.getElementById("modal-delete-btn").style.display = "";
  _renderModalForm();
  document.getElementById("item-modal").classList.remove("hidden");
}

/* ── Modal: open for add ─────────────────────────────────────────────────── */
function openAddModal() {
  const activeTabId = document.querySelector(".tab-btn.active")?.dataset.tab || "tab-txn";
  // Manual "add" is only meaningful for txns/provisions/execution-prices —
  // deletions are always created by accepting a MISCLASSIFIED diagnostic,
  // never from scratch. Fall back to the transaction form if user clicked
  // "+ Adicionar" while the deletions tab is active.
  const kind = activeTabId === "tab-prov" ? "provisions"
             : activeTabId === "tab-exec" ? "executionPrices"
             : "transactions";

  // Prefer a company whose filter column is set; otherwise fall back to the first
  // available company in the loaded data.
  const filterCompanyName = (_filters[activeTabId] || {}).companyName || "";
  let defaultCompany = "";
  if (filterCompanyName) {
    const match = Object.entries(_companies)
      .find(([, name]) => String(name).toLowerCase().includes(filterCompanyName.toLowerCase()));
    if (match) defaultCompany = match[0];
  }
  if (!defaultCompany) defaultCompany = Object.keys(_companies)[0] || Object.keys(_initialCompanies)[0] || "";

  const walletsOfCompany = _walletsByCompany[defaultCompany] || {};
  const defaultWallet = Object.keys(walletsOfCompany)[0] || "";

  let row;
  if (kind === "transactions") {
    row = {
      companyId: defaultCompany, walletId: defaultWallet, entityId: "",
      securityId: "", operationDate: _date, liquidationDate: _date,
      balance: 0, description: "", inputType: "sheets",
      beehusTransactionType: "buySell", hide: true, comment: "",
    };
  } else if (kind === "provisions") {
    row = {
      companyId: defaultCompany, walletId: defaultWallet,
      initialDate: _date, liquidationDate: _date,
      provisionType: "buySell", securityId: "", balance: 0,
      description: "", provisionSource: "adjustments",
    };
  } else {  // executionPrices
    row = {
      companyId: defaultCompany, walletId: defaultWallet,
      securityId: "", securityName: "", positionDate: _date,
      executionPrice: 0, pu: null, description: "",
      inputed: false, inputedAt: null, beehusId: null,
    };
  }

  _editing = { kind, row, isNew: true };
  const titles = {transactions: 'Nova Transação', provisions: 'Nova Provisão',
                  executionPrices: 'Novo Preço de Execução'};
  document.getElementById("modal-title").textContent = titles[kind] || 'Nova linha';
  document.getElementById("modal-delete-btn").style.display = "none";
  _renderModalForm();
  document.getElementById("item-modal").classList.remove("hidden");
}

function closeItemModal() {
  document.getElementById("item-modal").classList.add("hidden");
  _editing = null;
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.getElementById("item-modal").classList.contains("hidden")) {
    closeItemModal();
  }
});

/* ── Modal: render fields ────────────────────────────────────────────────── */
function _renderModalForm() {
  if (!_editing) return;
  const fields = FIELDS_BY_KIND[_editing.kind] || TXN_FIELDS;
  const row = _editing.row;

  const companyList = Object.keys(_companies).length
    ? Object.entries(_companies)
    : Object.entries(_initialCompanies);

  const companyOpts = companyList
    .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
    .map(([cid, name]) =>
      `<option value="${escHtml(cid)}" ${cid === row.companyId ? 'selected' : ''}>${escHtml(name)}</option>`
    ).join("");

  const walletsForRow = _walletsByCompany[row.companyId] || {};
  const walletOpts = Object.entries(walletsForRow)
    .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
    .map(([wid, name]) =>
      `<option value="${escHtml(wid)}" ${wid === row.walletId ? 'selected' : ''}>${escHtml(name)}</option>`
    ).join("");

  document.getElementById("modal-body").innerHTML = fields.map(f => {
    const val = row[f.key];
    const req = f.required ? '<span class="text-red-500">*</span>' : '';
    let input = "";
    if (f.type === "company") {
      input = `<select id="fld-${f.key}" onchange="_onCompanyFieldChange()"
        class="w-full border rounded px-3 py-2 text-sm">
        ${companyOpts || '<option value="">(sem empresas)</option>'}
      </select>`;
    } else if (f.type === "wallet") {
      input = `<select id="fld-${f.key}" class="w-full border rounded px-3 py-2 text-sm">
        ${walletOpts || '<option value="">(sem carteiras)</option>'}
      </select>`;
    } else if (f.type === "checkbox") {
      input = `<input type="checkbox" id="fld-${f.key}" ${val ? 'checked' : ''} class="rounded">`;
    } else if (f.type === "date") {
      input = `<input type="date" id="fld-${f.key}" value="${escHtml(val ?? '')}" class="w-full border rounded px-3 py-2 text-sm">`;
    } else if (f.type === "number") {
      input = `<input type="number" step="any" id="fld-${f.key}" value="${val ?? 0}" class="w-full border rounded px-3 py-2 text-sm">`;
    } else {
      input = `<input type="text" id="fld-${f.key}" value="${escHtml(val ?? '')}" class="w-full border rounded px-3 py-2 text-sm">`;
    }
    return `<div>
      <label class="block text-xs text-gray-500 mb-1">${escHtml(f.label)} ${req}</label>
      ${input}
    </div>`;
  }).join("");
}

/* Re-render wallet dropdown when company selection changes. Preserve every
   other already-entered value so we don't clobber the user's work. */
function _onCompanyFieldChange() {
  if (!_editing) return;
  const el = document.getElementById("fld-companyId");
  if (!el) return;
  // Persist current form values back into editing.row first.
  _editing.row = _collectForm();
  // Reset walletId so the first wallet of the newly selected company wins.
  const wallets = _walletsByCompany[el.value] || {};
  _editing.row.walletId = Object.keys(wallets)[0] || "";
  _renderModalForm();
}

/* ── Modal: collect form values ──────────────────────────────────────────── */
function _collectForm() {
  if (!_editing) return null;
  const fields = FIELDS_BY_KIND[_editing.kind] || TXN_FIELDS;
  const out = { ..._editing.row };
  for (const f of fields) {
    const el = document.getElementById(`fld-${f.key}`);
    if (!el) continue;
    if (f.type === "checkbox") out[f.key] = el.checked;
    else if (f.type === "number") out[f.key] = el.value === "" ? 0 : parseFloat(el.value);
    else out[f.key] = el.value;
  }
  return out;
}

/* ── Save (create or update) ─────────────────────────────────────────────── */
