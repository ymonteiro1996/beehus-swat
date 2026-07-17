/* Correções — exportação e bootstrap final (Token/loadDates).
   Escopo global compartilhado com os demais pedaços; ordem importa. */
async function _doDelete(kind, row) {
  try {
    const res = await fetch("/api/correcoes/items", {
      method: "DELETE",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        companyId: row.companyId, date: _date,
        walletId: row.walletId, kind, id: row.id,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    await loadRows();
    await loadDates();
  } catch (err) {
    alert("Erro ao excluir: " + err.message);
  }
}

/* ── Export menu ─────────────────────────────────────────────────────────── */
function toggleExportMenu() {
  document.getElementById("export-menu").classList.toggle("hidden");
}

function _refreshExportCompanyOptions() {
  const sel = document.getElementById("export-company");
  if (!sel) return;
  // Only show companies that actually have at least one row in the current view.
  // Only txn+prov drive export — deletions are internal bookkeeping.
  const present = new Set();
  for (const r of _rows.transactions) if (r.companyId) present.add(r.companyId);
  for (const r of _rows.provisions)   if (r.companyId) present.add(r.companyId);

  const opts = Array.from(present)
    .map(cid => [cid, _companies[cid] || cid])
    .sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  sel.innerHTML = opts.length
    ? opts.map(([cid, name]) => `<option value="${escHtml(cid)}">${escHtml(name)}</option>`).join("")
    : '<option value="">(sem linhas)</option>';
}

document.addEventListener("click", (e) => {
  const menu = document.getElementById("export-menu");
  const btn  = document.getElementById("export-btn");
  if (menu && btn && !btn.contains(e.target) && !menu.contains(e.target)) {
    menu.classList.add("hidden");
  }
});

async function exportFile(format) {
  const companyId = document.getElementById("export-company").value;
  if (!companyId) { alert("Selecione uma empresa para exportar."); return; }
  document.getElementById("export-menu").classList.add("hidden");
  const status = document.getElementById("action-status");
  status.textContent = "Gerando arquivo...";
  try {
    const res = await fetch("/api/correcoes/export", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ companyId, date: _date, format }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const cd   = res.headers.get("Content-Disposition") || "";
    const m    = /filename="?([^"]+)"?/.exec(cd);
    const name = m ? m[1] : `correcoes_${companyId}_${_date}.${format}`;

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    status.textContent = "Arquivo gerado.";
  } catch (err) {
    status.textContent = "Erro: " + err.message;
  }
}

/* ── Boot ────────────────────────────────────────────────────────────────── */
Token.refresh();
loadDates();
