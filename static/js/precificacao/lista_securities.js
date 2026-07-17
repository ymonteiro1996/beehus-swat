/* Precificação — montagem/edição da lista de securities.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  function addSecurity() {
    if (!_selectedSec) return;
    const calcType = document.getElementById("dp-calc-type").value;

    if (_addedSecurities.find(s => s.id === _selectedSec.id && s.calcType === calcType)) {
      alert("Ativo já está na lista com este tipo de cálculo."); return;
    }

    const entry = { ..._selectedSec, calcType };

    if (calcType === "pos_fixado") {
      const bmSel = document.getElementById("dp-benchmark");
      const bmId  = bmSel.value;
      if (!bmId) { alert("Selecione um benchmark."); return; }
      entry.benchmarkId   = bmId;
      entry.benchmarkName = bmSel.options[bmSel.selectedIndex]?.text || "";
      const idxRaw = document.getElementById("dp-idx-pct").value.trim();
      entry.indexerPercentual = idxRaw !== "" ? Number(idxRaw) : null;
    } else if (calcType === "pre_fixado_curva" || calcType === "inflacao_curva") {
      const validRows = _txnRows.filter(r => r.quantity != null && r.quantity > 0 && r.yield != null);
      if (!validRows.length) { alert("Adicione ao menos um lote com quantidade e yield."); return; }
      entry.transactions = validRows.map(r => ({ ...r }));
      const totalQty = validRows.reduce((s, r) => s + r.quantity, 0);
      entry.weightedYield = validRows.reduce((s, r) => s + r.quantity * r.yield, 0) / totalQty;
      if (calcType === "inflacao_curva") {
        const bmSel = document.getElementById("dp-inflacao-benchmark");
        const bmId  = bmSel.value;
        if (!bmId) { alert("Selecione um benchmark de inflação."); return; }
        entry.benchmarkId   = bmId;
        entry.benchmarkName = bmSel.options[bmSel.selectedIndex]?.text || "";
      }
    }

    _addedSecurities.push(entry);
    renderSecList();
  }

  function clearAll() {
    _addedSecurities = [];
    renderSecList();
    document.getElementById("results-section").classList.add("hidden");
  }

  function resetAll() {
    if (!confirm("Reiniciar tudo? Todos os dados serão perdidos.")) return;
    _selectedSec = null;
    _addedSecurities = [];
    _resultsData = null;
    _txnRows = [];
    document.getElementById("company-select").value = "";
    document.getElementById("wallet-select").innerHTML = '<option value="">— selecione —</option>';
    document.getElementById("position-date-input").value = "";
    document.getElementById("wallet-id-label").textContent = "";
    document.getElementById("detail-panel").classList.add("hidden");
    document.getElementById("results-section").classList.add("hidden");
    document.getElementById("calc-msg").classList.add("hidden");
    renderSecList();
  }

  document.getElementById("sec-list").addEventListener("click", e => {
    const removeBtn = e.target.closest(".remove-sec-btn");
    if (removeBtn) { _addedSecurities.splice(Number(removeBtn.dataset.idx), 1); renderSecList(); return; }

    const editBtn = e.target.closest(".edit-sec-btn");
    if (editBtn) { editSecurity(Number(editBtn.dataset.idx)); }
  });

  function editSecurity(idx) {
    const s = _addedSecurities[idx];
    if (!s) return;

    _addedSecurities.splice(idx, 1);
    renderSecList();

    _selectedSec = { ...s };

    // Reuse shared rendering
    document.getElementById("dp-name").textContent  = s.beehusName || s.id;
    document.getElementById("dp-id").textContent    = s.id;
    document.getElementById("dp-fields").innerHTML  = _buildFieldBadges(s);

    document.getElementById("dp-calc-type").value = s.calcType || "pos_fixado";
    document.getElementById("dp-idx-pct").value   = s.indexerPercentual != null ? s.indexerPercentual : "";
    if (s.benchmarkId) {
      document.getElementById("dp-benchmark").value          = s.benchmarkId;
      document.getElementById("dp-inflacao-benchmark").value = s.benchmarkId;
    }

    _txnRows = (s.transactions || []).map(t => ({ ...t }));
    if (_txnRows.length) renderTxnTable();

    document.getElementById("detail-panel").classList.remove("hidden");
    onCalcTypeChange();
  }

  function renderSecList() {
    const el = document.getElementById("sec-list");
    const countEl = document.getElementById("sec-count");
    if (!_addedSecurities.length) {
      el.innerHTML = '<p class="text-xs text-gray-300 italic">Nenhum ativo adicionado.</p>';
      countEl.textContent = "";
      return;
    }
    countEl.textContent = `(${_addedSecurities.length})`;
    el.innerHTML = `
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="text-[10px] uppercase text-gray-400">
            <th class="text-left py-1.5 pr-3 font-normal">Ativo</th>
            <th class="text-left py-1.5 pr-3 font-normal">Carteira</th>
            <th class="text-left py-1.5 pr-3 font-normal">Tipo Precif.</th>
            <th class="text-left py-1.5 pr-3 font-normal">Tipo Cálc.</th>
            <th class="text-left py-1.5 pr-3 font-normal">Parâmetros</th>
            <th class="text-right py-1.5 pr-3 font-normal">Último PU</th>
            <th class="text-right py-1.5 pr-3 font-normal">Data PU</th>
            <th class="py-1.5 w-5"></th>
          </tr>
        </thead>
        <tbody>
          ${_addedSecurities.map((s, i) => {
            let params = "—";
            if (s.calcType === "pos_fixado") {
              const pct = s.indexerPercentual != null ? s.indexerPercentual + "%" : "—";
              params = `${escHtml(s.benchmarkName || "—")} ${pct}`;
            } else if (s.calcType === "pre_fixado_curva" || s.calcType === "inflacao_curva") {
              const n = (s.transactions || []).length;
              params = `${n} lote${n !== 1 ? "s" : ""} · yield pond. ${s.weightedYield != null ? s.weightedYield.toFixed(4) + "%" : "—"}`;
              if (s.calcType === "inflacao_curva") params += ` · ${escHtml(s.benchmarkName || "—")}`;
            }
            return `
            <tr class="border-t border-gray-100 hover:bg-gray-50">
              <td class="py-2 pr-3">
                <p class="font-medium text-gray-800">${escHtml(s.beehusName)}</p>
                <p class="text-[10px] text-gray-400 font-mono">${escHtml(s.id)}</p>
              </td>
              <td class="py-2 pr-3 text-gray-500 text-[10px]">${escHtml(s.walletName || "—")}</td>
              <td class="py-2 pr-3 text-gray-500 text-[10px]">${escHtml(s.pricingType || "—")}</td>
              <td class="py-2 pr-3 text-gray-600">${escHtml(CALC_TYPE_LABELS[s.calcType] || s.calcType)}</td>
              <td class="py-2 pr-3 text-gray-600">${params}</td>
              <td class="py-2 pr-3 text-right font-mono text-gray-600">${s.posPU != null ? fmtNum(s.posPU, 8) : (s.lastPU != null ? fmtNum(s.lastPU, 8) : "—")}</td>
              <td class="py-2 pr-3 text-right text-gray-400">${escHtml(s.positionDate || s.lastPUDate || "—")}</td>
              <td class="py-2 flex items-center gap-2">
                <button class="edit-sec-btn text-gray-400 hover:text-orange-600 text-lg px-1.5 py-0.5" data-idx="${i}" title="Editar">&#9998;</button>
                <button class="remove-sec-btn text-red-400 hover:text-red-600 text-lg px-1.5 py-0.5" data-idx="${i}" title="Remover">&#10005;</button>
              </td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>`;
  }

  // ── Calculate ──────────────────────────────────────────────────────────────
