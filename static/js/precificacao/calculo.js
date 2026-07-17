/* Precificação — cálculo e renderização dos resultados.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  function calcular() {
    if (!_addedSecurities.length) { alert("Adicione pelo menos um ativo."); return; }
    const msg = document.getElementById("calc-msg");
    msg.textContent = "Calculando..."; msg.classList.remove("hidden");

    fetch("/api/precificacao/calcular", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        securities: _addedSecurities.map(s => ({
          id: s.id, beehusName: s.beehusName, calcType: s.calcType || "pos_fixado",
          benchmarkId: s.benchmarkId, benchmarkName: s.benchmarkName,
          indexerPercentual: s.indexerPercentual,
          transactions: s.transactions || [],
          walletId: s.walletId || "", walletName: s.walletName || "",
          pricingType: s.pricingType || "",
          initialPU: s.initialPU ?? null, initialPUDate: s.initialPUDate || "",
          posPU: s.posPU ?? null, positionDate: s.positionDate || "",
        })),
      }),
    })
      .then(r => r.json())
      .then(data => {
        msg.classList.add("hidden");
        if (data.error) { alert(data.error); return; }
        _resultsData = data;
        renderResults(data);
      })
      .catch(() => { msg.textContent = "Erro ao calcular."; });
  }

  function renderResults(data) {
    const { results } = data;
    document.getElementById("results-thead-row").innerHTML =
      `<th class="px-3 py-2.5 text-left">Ativo</th>
       <th class="px-3 py-2.5 text-left">Carteira</th>
       <th class="px-3 py-2.5 text-left">Tipo Cálc.</th>
       <th class="px-3 py-2.5 text-right">Data</th>
       <th class="px-3 py-2.5 text-left">Benchmark</th>
       <th class="px-3 py-2.5 text-right">Fator Diário</th>
       <th class="px-3 py-2.5 text-right">Yield BM (% a.a.)</th>
       <th class="px-3 py-2.5 text-right text-orange-600">PU Calculado</th>`;

    const count = results.filter(r => !r.error).length;
    document.getElementById("results-count").textContent = `${count} linha${count !== 1 ? "s" : ""}`;

    document.getElementById("results-tbody").innerHTML = results.map(r => {
      if (r.error) {
        return `<tr class="border-t border-gray-100"><td class="px-3 py-2.5" colspan="8">
          <span class="font-medium text-gray-700">${escHtml(r.beehusName)}</span>
          <span class="ml-2 text-red-500">${escHtml(r.error)}</span></td></tr>`;
      }
      return `<tr class="border-t border-gray-100 hover:bg-gray-50">
        <td class="px-3 py-2.5">
          <p class="font-medium text-gray-800">${escHtml(r.beehusName)}</p>
          <p class="text-[10px] text-gray-400 font-mono">${escHtml(r.securityId)}</p>
        </td>
        <td class="px-3 py-2.5 text-gray-500 text-[10px]">${escHtml(r.walletName || "—")}</td>
        <td class="px-3 py-2.5 text-gray-500 text-[10px]">${escHtml(CALC_TYPE_LABELS[r.calcType] || r.calcType || "—")}</td>
        <td class="px-3 py-2.5 text-right font-mono text-gray-600">${escHtml(r.date || "—")}</td>
        <td class="px-3 py-2.5 text-gray-600 text-xs">${escHtml(r.benchmarkName || "—")}</td>
        <td class="px-3 py-2.5 text-right font-mono text-gray-500 text-[10px]">${r.benchmarkFactor != null ? fmtNum(r.benchmarkFactor, 10) : "—"}</td>
        <td class="px-3 py-2.5 text-right font-mono text-gray-500 text-[10px]">${r.benchmarkYield != null ? r.benchmarkYield.toFixed(4) + "%" : "—"}</td>
        <td class="px-3 py-2.5 text-right font-mono font-semibold text-orange-600">${r.eventImpact ? `<span class="inline-block mr-1 px-1 rounded bg-red-100 text-red-600 text-[9px] align-middle" title="Cupom/amortização (líquido de IR), por unidade: ${fmtNum(r.eventImpact, 8)}">▼${fmtNum(r.eventImpact, 8)}</span>` : ""}${fmtNum(r.pu, 8)}</td>
      </tr>`;
    }).join("");

    document.getElementById("results-section").classList.remove("hidden");
  }

  function copyResults() {
    if (!_resultsData) return;
    const headers = ["Ativo", "ID", "Carteira", "Tipo Cálc.", "Data", "Benchmark", "Fator", "Yield BM (% a.a.)", "PU", "Ajuste Evento"];
    const fmtBR = v => v == null || v === "" ? "" : String(v).replace(".", ",");
    const rows = _resultsData.results.filter(r => !r.error).map(r => [
      r.beehusName, r.securityId, r.walletName || "", CALC_TYPE_LABELS[r.calcType] || "",
      r.date ?? "", r.benchmarkName ?? "", fmtBR(r.benchmarkFactor), fmtBR(r.benchmarkYield), fmtBR(r.pu),
      fmtBR(r.eventImpact),
    ]);
    const tsv = [headers, ...rows].map(r => r.join("\t")).join("\n");
    navigator.clipboard.writeText(tsv).then(() => {
      const btn = document.querySelector("[onclick='copyResults()']");
      const orig = btn.innerHTML; btn.textContent = "Copiado!";
      setTimeout(() => { btn.innerHTML = orig; }, 2000);
    });
  }

  // ── Bulk Import via Excel ─────────────────────────────────────────────
  function downloadTemplate() {
    window.location.href = "/api/precificacao/upload-template";
  }

  // ── Modal de "Data de referência" — compartilhada por Importar Excel e
  // Atualizar. `_refDateMode` registra a ação que dispara o Continuar.
