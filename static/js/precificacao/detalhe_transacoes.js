/* Precificação — painel de detalhe do ativo e linhas de transação.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  function _buildFieldBadges(d) {
    const fields = [
      ["mainId",         d.mainId],
      ["Tipo",           d.securityType],
      ["Sub-tipo",       d.type],
      ["Moeda",          d.currency],
      ["Vencimento",     d.maturityDate],
      ["Emissor",        d.issuer],
      ["Tipo Precif.",   d.pricingType],
      ["Indexador",      d.indexer],
      ["% Indexador",    d.indexerPercentual != null ? d.indexerPercentual : null],
      ["Taxa",           d.yield != null ? d.yield : null],
      ["Quantidade",     d.posQuantity != null ? fmtNum(d.posQuantity, 2) : null],
      ["Último PU",      d.posPU != null ? fmtNum(d.posPU, 8) : (d.lastPU != null ? fmtNum(d.lastPU, 8) : null)],
      ["Data Posição",   d.positionDate || null],
    ];
    return fields
      .filter(([, v]) => v != null && v !== "")
      .map(([l, v]) => `<span class="sec-badge"><span class="label">${escHtml(l)}</span><span class="val">${escHtml(String(v))}</span></span>`)
      .join("");
  }

  function renderDetailPanel(d) {
    document.getElementById("dp-name").textContent = d.beehusName || d.id;
    document.getElementById("dp-id").textContent   = d.id;
    document.getElementById("dp-fields").innerHTML  = _buildFieldBadges({ ...d, positionDate: _selectedSec?.positionDate });

    document.getElementById("dp-idx-pct").value = d.indexerPercentual != null ? d.indexerPercentual : "";
    _txnRows = [];
    document.getElementById("dp-txn-table-wrap").innerHTML =
      '<p class="text-[10px] text-gray-300 italic">Adicione ao menos um lote.</p>';
    document.getElementById("detail-panel").classList.remove("hidden");
    onCalcTypeChange();
  }

  // ── Calc type toggle ───────────────────────────────────────────────────────
  function onCalcTypeChange() {
    const ct = document.getElementById("dp-calc-type").value;
    const isPos      = ct === "pos_fixado";
    const isHTM      = ct === "pre_fixado_curva" || ct === "inflacao_curva";
    const isInflacao = ct === "inflacao_curva";

    document.getElementById("dp-benchmark-wrap").classList.toggle("hidden", !isPos);
    document.getElementById("dp-field-idx-pct").classList.toggle("hidden", !isPos);
    document.getElementById("dp-inflacao-bm-wrap").classList.toggle("hidden", !isInflacao);
    document.getElementById("dp-add-btn-inline").classList.toggle("hidden", isHTM);
    document.getElementById("dp-transactions-wrap").classList.toggle("hidden", !isHTM);
    if (isHTM && !_txnRows.length) addTxnRow();
  }

  // ── Transaction lots (HTM) ─────────────────────────────────────────────────
  function addTxnRow() {
    _txnRows.push({ date: "", quantity: null, yield: null });
    renderTxnTable();
  }

  function removeTxnRow(i) {
    _txnRows.splice(i, 1);
    renderTxnTable();
  }

  function updateTxnField(i, field, raw) {
    if (!_txnRows[i]) return;
    _txnRows[i][field] = (field === "date") ? raw : (raw === "" ? null : Number(raw));
  }

  function renderTxnTable() {
    const wrap = document.getElementById("dp-txn-table-wrap");
    if (!_txnRows.length) {
      wrap.innerHTML = '<p class="text-[10px] text-gray-300 italic">Adicione ao menos um lote.</p>';
      return;
    }
    const inp = (i, field, val, type, w) =>
      `<input type="${type}" step="any" value="${escHtml(val ?? '')}"
        oninput="updateTxnField(${i},'${field}',this.value)"
        class="border rounded px-2 py-1 text-[10px] ${w} focus:outline-none focus:ring-1 focus:ring-orange-400" />`;
    wrap.innerHTML = `
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="text-[10px] uppercase text-gray-400 border-b">
            <th class="text-left py-1.5 pr-2 font-normal">Data</th>
            <th class="text-right py-1.5 pr-2 font-normal">Quantidade</th>
            <th class="text-right py-1.5 pr-2 font-normal">Yield (% a.a.)</th>
            <th class="py-1.5 w-5"></th>
          </tr>
        </thead>
        <tbody>
          ${_txnRows.map((r, i) => `
            <tr class="border-t border-gray-100">
              <td class="py-1 pr-2">${inp(i,'date',r.date,'date','w-30')}</td>
              <td class="py-1 pr-2 text-right">${inp(i,'quantity',r.quantity,'number','w-28 text-right')}</td>
              <td class="py-1 pr-2 text-right">${inp(i,'yield',r.yield,'number','w-28 text-right')}</td>
              <td class="py-1 text-center">
                <button onclick="removeTxnRow(${i})" class="text-red-400 hover:text-red-600 text-[10px]">&#10005;</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  }

  // ── Add security to list ───────────────────────────────────────────────────
