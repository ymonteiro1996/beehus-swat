/* Conciliação (Não Proc.) — modais de edição de transação/provisão.
   Escopo global compartilhado; ordem importa. */
function openTxnEdit(txn) {
  if (!txn || !txn.txnId) return;
  _txnEditCtx = { txnId: txn.txnId, operationDate: txn.operationDate || "", liquidationDate: txn.liquidationDate || "" };
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v == null ? "" : v); };
  document.getElementById("txn-edit-subtitle").textContent = `${_walletName} • txn ${txn.txnId}`;
  set("txn-edit-operationDate", txn.operationDate || "");
  set("txn-edit-liquidationDate", txn.liquidationDate || "");
  set("txn-edit-type", txn.beehusTransactionType || "");
  set("txn-edit-balance", txn.balance);
  set("txn-edit-price", txn.price);
  set("txn-edit-securityId", txn.securityId || "");
  set("txn-edit-description", txn.description || "");
  const st = document.getElementById("txn-edit-status"); st.textContent = ""; st.className = "text-[11px]";
  const btn = document.getElementById("txn-edit-save"); btn.disabled = false; btn.textContent = "Salvar via API";
  document.getElementById("txn-edit-modal").classList.remove("hidden");
}
function closeTxnEditModal() { document.getElementById("txn-edit-modal").classList.add("hidden"); _txnEditCtx = null; }

function saveTxnEdit() {
  if (!_txnEditCtx || !_txnEditCtx.txnId) return;
  const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
  const patch = {};
  const opDate = val("txn-edit-operationDate"), liqDate = val("txn-edit-liquidationDate");
  const type = val("txn-edit-type"), balance = val("txn-edit-balance"), price = val("txn-edit-price");
  const secId = val("txn-edit-securityId"), desc = document.getElementById("txn-edit-description").value;
  if (opDate) patch.operationDate = opDate;
  if (liqDate) patch.liquidationDate = liqDate;
  if (type) patch.beehusTransactionType = type;
  if (balance !== "") patch.balance = parseFloat(balance);
  if (price !== "") patch.price = parseFloat(price);
  if (secId) patch.securityId = secId;
  if (desc.trim() !== "") patch.description = desc;
  const st = document.getElementById("txn-edit-status");
  if (!Object.keys(patch).length) { st.textContent = "Nenhum campo preenchido para alterar."; st.className = "text-[11px] text-amber-600"; return; }
  const btn = document.getElementById("txn-edit-save"); btn.disabled = true; btn.textContent = "Salvando...";
  fetch("/api/conciliacao/transaction/update", {
    method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify({ walletId: _walletId, txnId: _txnEditCtx.txnId, patch, operationDate: _txnEditCtx.operationDate, liquidationDate: _txnEditCtx.liquidationDate }),
  }).then(async res => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_apiErrMsg(data, res.status));
    closeTxnEditModal(); _refreshDetail();
  }).catch(err => {
    btn.disabled = false; btn.textContent = "Salvar via API";
    st.textContent = "Erro: " + err.message; st.className = "text-[11px] text-red-600";
  });
}

function deleteWalletTxn(txnId, operationDate, liquidationDate) {
  if (!txnId) return;
  if (!confirm(`Excluir esta transação DEFINITIVAMENTE via API?\n\ntxnId: ${txnId}\n\nRemove o documento no Beehus e não pode ser desfeita.`)) return;
  fetch("/api/conciliacao/transaction/delete", {
    method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify({ walletId: _walletId, txnId, operationDate, liquidationDate }),
  }).then(async res => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_apiErrMsg(data, res.status));
    _refreshDetail();
  }).catch(err => alert("Erro ao excluir transação: " + err.message));
}

let _provEditCtx = null;
function openProvEdit(prov) {
  if (!prov || !prov.provisionId) return;
  _provEditCtx = { provisionId: prov.provisionId };
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v == null ? "" : v); };
  document.getElementById("prov-edit-subtitle").textContent = `${_walletName} • provisão ${prov.provisionId}`;
  set("prov-edit-initialDate", prov.initialDate || "");
  set("prov-edit-liquidationDate", prov.liquidationDate || "");
  set("prov-edit-type", prov.provisionType || "");
  set("prov-edit-balance", prov.balance);
  set("prov-edit-securityId", prov.securityId || "");
  set("prov-edit-description", prov.description || "");
  const st = document.getElementById("prov-edit-status"); st.textContent = ""; st.className = "text-[11px]";
  const btn = document.getElementById("prov-edit-save"); btn.disabled = false; btn.textContent = "Salvar via API";
  document.getElementById("prov-edit-modal").classList.remove("hidden");
}
function closeProvEditModal() { document.getElementById("prov-edit-modal").classList.add("hidden"); _provEditCtx = null; }

function saveProvEdit() {
  if (!_provEditCtx || !_provEditCtx.provisionId) return;
  const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
  const patch = {};
  const initDate = val("prov-edit-initialDate"), liqDate = val("prov-edit-liquidationDate");
  const type = val("prov-edit-type"), balance = val("prov-edit-balance");
  const secId = val("prov-edit-securityId"), desc = document.getElementById("prov-edit-description").value;
  if (initDate) patch.initialDate = initDate;
  if (liqDate) patch.liquidationDate = liqDate;
  if (type) patch.provisionType = type;
  if (balance !== "") patch.balance = parseFloat(balance);
  if (secId) patch.securityId = secId;
  if (desc.trim() !== "") patch.description = desc;
  const st = document.getElementById("prov-edit-status");
  if (!Object.keys(patch).length) { st.textContent = "Nenhum campo preenchido para alterar."; st.className = "text-[11px] text-amber-600"; return; }
  const btn = document.getElementById("prov-edit-save"); btn.disabled = true; btn.textContent = "Salvando...";
  fetch("/api/conciliacao/provision/update", {
    method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify({ walletId: _walletId, provisionId: _provEditCtx.provisionId, patch }),
  }).then(async res => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_apiErrMsg(data, res.status));
    closeProvEditModal(); _refreshDetail();
  }).catch(err => {
    btn.disabled = false; btn.textContent = "Salvar via API";
    st.textContent = "Erro: " + err.message; st.className = "text-[11px] text-red-600";
  });
}

function deleteWalletProv(provisionId) {
  if (!provisionId) return;
  if (!confirm(`Excluir esta provisão DEFINITIVAMENTE via API?\n\nprovisionId: ${provisionId}\n\nRemove o documento no Beehus e não pode ser desfeita.`)) return;
  fetch("/api/conciliacao/provision/delete", {
    method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" },
    body: JSON.stringify({ walletId: _walletId, provisionId }),
  }).then(async res => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_apiErrMsg(data, res.status));
    _refreshDetail();
  }).catch(err => alert("Erro ao excluir provisão: " + err.message));
}

/* ══════════════════ DIAGNOSTIC MODAL (focado) ══════════════════ */
let _diag = null;
let _suggByKey = {};   // key -> {kind:'prov'|'exec'|'txn', item, summary}

const VERDICT_LABEL = {
  NO_GAP: "Sem divergência", SECURITY_ISSUES: "Problemas em ativos",
  TRANSACTION_ISSUES: "Problemas em transações", CASH_ISSUES: "Divergência de caixa",
  LIKELY_WRONG_FORMER_NAV: "Provável posição anterior incorreta",
};

