/* Correções — salvar/excluir, envio em lote e por linha.
   Escopo global compartilhado com os demais pedaços; ordem importa. */
async function saveItem() {
  if (!_editing) return;
  const row = _collectForm();
  if (!row.companyId) { alert("Empresa é obrigatória."); return; }
  if (!row.walletId)  { alert("Carteira é obrigatória."); return; }

  const saveBtn = document.getElementById("modal-save-btn");
  saveBtn.disabled = true;
  try {
    const body = JSON.stringify({
      companyId: row.companyId, date: _date, walletId: row.walletId,
      kind: _editing.kind, row,
    });
    const url = "/api/correcoes/items";
    const method = _editing.isNew ? "POST" : "PUT";
    const res = await fetch(url, {
      method, headers: {"Content-Type": "application/json"}, body,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    closeItemModal();
    await loadRows();
    await loadDates();
  } catch (err) {
    alert("Erro ao salvar: " + err.message);
  } finally {
    saveBtn.disabled = false;
  }
}

/* ── Delete via modal ────────────────────────────────────────────────────── */
async function deleteItem() {
  if (!_editing || _editing.isNew) return;
  if (!confirm("Excluir esta linha?")) return;
  await _doDelete(_editing.kind, _editing.row);
  closeItemModal();
}

/* ── Delete directly from row action ─────────────────────────────────────── */
async function quickDelete(kind, idx) {
  if (!confirm("Excluir esta linha?")) return;
  const row = _rows[kind][idx];
  await _doDelete(kind, row);
}

/* ── Bulk send for Transações ────────────────────────────────────────────── */
function _checkedTxnIndices() {
  return Array.from(document.querySelectorAll(".txn-row-cb:checked"))
    .map(cb => parseInt(cb.dataset.idx, 10))
    .filter(n => Number.isInteger(n));
}

function _onTxnCheckboxChange() {
  const n = _checkedTxnIndices().length;
  const btn   = document.getElementById("txn-bulk-submit-btn");
  const count = document.getElementById("txn-bulk-count");
  if (count) count.textContent = String(n);
  if (btn)   btn.disabled = n === 0;

  // "select-all" reflects the visible state: checked when every visible
  // pending checkbox is checked; otherwise unchecked.
  const all  = document.querySelectorAll(".txn-row-cb");
  const sel  = document.getElementById("txn-select-all");
  if (sel) sel.checked = all.length > 0 && n === all.length;
}

function toggleAllTxns(checked) {
  document.querySelectorAll(".txn-row-cb").forEach(cb => { cb.checked = !!checked; });
  _onTxnCheckboxChange();
}

async function submitSelectedTransactions() {
  const idxs = _checkedTxnIndices();
  if (!idxs.length) return;
  if (!confirm(`Enviar ${idxs.length} transação(ões) selecionada(s) para a API Beehus?`)) return;

  const btn    = document.getElementById("txn-bulk-submit-btn");
  const status = document.getElementById("txn-bulk-status");
  btn.disabled = true;
  status.textContent = `Enviando 0/${idxs.length}...`;

  // Snapshot row ids up-front. Indices change after `loadRows()` reorders the
  // array, but the row uuid is stable on disk, so we send by id.
  const targets = idxs
    .map(i => _rows.transactions[i])
    .filter(r => r && !r.inputed);

  let ok = 0;
  const errors = [];
  // Sequential to keep the upstream API happy and to surface a 401 immediately
  // — the next call after a token failure would just fail the same way.
  for (let i = 0; i < targets.length; i++) {
    const row = targets[i];
    status.textContent = `Enviando ${i + 1}/${targets.length}...`;
    try {
      const res = await fetch("/api/correcoes/transactions/submit", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          companyId: row.companyId, date: _date,
          walletId: row.walletId, id: row.id,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const upstream = data.upstream_body
          ? ` — upstream: ${typeof data.upstream_body === 'string' ? data.upstream_body : JSON.stringify(data.upstream_body)}`
          : '';
        errors.push({ row, msg: (data.error || `HTTP ${res.status}`) + upstream });
        // Token expired/revoked — refresh badge and stop the run; the
        // remaining transactions would just fail with the same 401.
        if (res.status === 401) {
          Token.refresh();
          break;
        }
      } else {
        ok++;
      }
    } catch (err) {
      errors.push({ row, msg: err.message });
    }
  }

  await loadRows();

  if (errors.length === 0) {
    status.textContent = `${ok} transação(ões) enviada(s) com sucesso.`;
  } else {
    const sample = errors.slice(0, 3)
      .map(e => `• ${e.row.beehusTransactionType || ''} ${e.row.securityId || ''}: ${e.msg}`)
      .join("\n");
    const more = errors.length > 3 ? `\n…e mais ${errors.length - 3}.` : "";
    alert(`${ok} ok, ${errors.length} com erro:\n${sample}${more}`);
    status.textContent = `${ok} ok, ${errors.length} com erro.`;
  }
  // _onTxnCheckboxChange is called by renderRows() inside loadRows().
}

async function submitProvision(idx) {
  const row = _rows.provisions[idx];
  if (!row || row.inputed) return;
  const balanceFmt = Number(row.balance || 0)
    .toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (!confirm(`Enviar provisão (${row.provisionType}, ${balanceFmt}) para a API Beehus?`)) return;
  const btn = document.getElementById(`prov-submit-${idx}`);
  if (btn) { btn.disabled = true; btn.textContent = "Enviando..."; }
  try {
    const res = await fetch("/api/correcoes/provisions/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        companyId: row.companyId, date: _date,
        walletId: row.walletId, id: row.id,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      Token.refresh();
      const upstream = data.upstream_body ? ` — upstream: ${typeof data.upstream_body === 'string' ? data.upstream_body : JSON.stringify(data.upstream_body)}` : '';
      throw new Error((data.error || `HTTP ${res.status}`) + upstream);
    }
    await loadRows();
  } catch (err) {
    alert("Erro ao enviar provisão: " + err.message);
    if (btn) { btn.disabled = false; btn.textContent = "Enviar via API"; }
  }
}

async function submitExecutionPrice(idx) {
  const row = _rows.executionPrices[idx];
  if (!row || row.inputed) return;
  if (!confirm(`Enviar preço de execução ${row.executionPrice} para a API?`)) return;
  const btn = document.getElementById(`exec-submit-${idx}`);
  if (btn) { btn.disabled = true; btn.textContent = "Enviando..."; }
  try {
    const res = await fetch("/api/correcoes/execution-prices/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        companyId: row.companyId, date: _date,
        walletId: row.walletId, id: row.id,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      Token.refresh();
      const upstream = data.upstream_body ? ` — upstream: ${typeof data.upstream_body === 'string' ? data.upstream_body : JSON.stringify(data.upstream_body)}` : '';
      throw new Error((data.error || `HTTP ${res.status}`) + upstream);
    }
    await loadRows();
  } catch (err) {
    alert("Erro ao enviar preço: " + err.message);
    if (btn) { btn.disabled = false; btn.textContent = "Enviar via API"; }
  }
}

