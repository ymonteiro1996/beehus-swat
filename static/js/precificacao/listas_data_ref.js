/* Precificação — modal de data de referência e listas salvas.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  let _refDateMode = null;       // "import" | "refresh"
  let _pendingRefDate = "";      // populated by confirmRefDateModal()

  function openRefDateModal(mode) {
    _refDateMode = mode;
    const title = document.getElementById("refdate-modal-title");
    const hint  = document.getElementById("refdate-modal-hint");
    const status = document.getElementById("refdate-modal-status");
    const go    = document.getElementById("refdate-modal-go");
    if (mode === "refresh") {
      title.textContent = "Atualizar lista — data de referência";
      hint.innerHTML = (
        "Reapura <strong>posPU</strong>, <strong>positionDate</strong>, " +
        "<strong>pricingType</strong>, <strong>lastPU</strong> e " +
        "<strong>lastPUDate</strong> dos ativos atualmente na lista, usando " +
        "essa data como referência (<code>processedPosition</code> exato + " +
        "último <code>historyPrice</code> com <code>date ≤</code> referência). " +
        "Não mexe em <code>calcType</code>, lotes HTM, benchmark nem transações."
      );
      go.textContent = "Atualizar";
      if (!_addedSecurities.length) {
        status.textContent = "A lista está vazia — adicione ativos antes de atualizar.";
        // Não aborta a abertura — o operador pode cancelar.
      } else {
        status.textContent = "";
      }
    } else {
      title.textContent = "Importar Excel — data de referência";
      hint.innerHTML = (
        "O sistema usará essa data para buscar PU em <code>securityPrices</code> " +
        "(último <code>historyPrice</code> com <code>date ≤</code> referência) e " +
        "a posição em <code>processedPosition</code> exatamente nessa data, " +
        "em vez de pegar o all-time-latest da base."
      );
      go.textContent = "Continuar";
      status.textContent = "";
    }
    // Pré-popula com a última data usada (se houver) ou hoje.
    const input = document.getElementById("refdate-input");
    if (!input.value) {
      const t = new Date();
      const iso = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
      input.value = _pendingRefDate || iso;
    }
    document.getElementById("refdate-modal").classList.remove("hidden");
    try { input.focus(); } catch (e) {}
  }

  function closeRefDateModal() {
    document.getElementById("refdate-modal").classList.add("hidden");
    _refDateMode = null;
  }

  function confirmRefDateModal() {
    const input  = document.getElementById("refdate-input");
    const status = document.getElementById("refdate-modal-status");
    const ref = (input.value || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(ref)) {
      status.textContent = "Informe uma data válida (YYYY-MM-DD).";
      return;
    }
    _pendingRefDate = ref;
    const mode = _refDateMode;
    closeRefDateModal();
    if (mode === "import") {
      // Abre o file picker; o `change` handler abaixo pega `_pendingRefDate`.
      document.getElementById("upload-input").click();
    } else if (mode === "refresh") {
      refreshListByDate(ref);
    }
  }

  function refreshListByDate(refDate) {
    if (!_addedSecurities.length) {
      alert("A lista está vazia — adicione ativos antes de atualizar.");
      return;
    }
    fetch("/api/precificacao/refresh-list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        securities: _addedSecurities,
        referenceDate: refDate,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) { alert(data.error); return; }
        const updated = data.securities || [];
        const errors  = data.errors     || [];
        if (updated.length !== _addedSecurities.length) {
          // Defensivo — não esperado, mas evita perder linhas sem aviso.
          console.warn("refresh-list mudou a cardinalidade da lista:",
                       updated.length, "vs", _addedSecurities.length);
        }
        _addedSecurities = updated;
        renderSecList();
        document.getElementById("results-section").classList.add("hidden");
        let msg = `${updated.length} ativo(s) reapurados em ${refDate}.`;
        if (errors.length) msg += "\n\nLinhas com erro:\n• " + errors.join("\n• ");
        alert(msg);
      })
      .catch(err => alert(`Falha ao atualizar: ${err.message}`));
  }

  document.getElementById("upload-input").addEventListener("change", e => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    // `_pendingRefDate` foi setada pelo confirmRefDateModal antes do click().
    // Se chamarem o file picker direto (atalho do teclado, etc.), segue sem
    // ref date e o backend cai no comportamento legado de "all-time latest".
    if (_pendingRefDate) fd.append("referenceDate", _pendingRefDate);
    fetch("/api/precificacao/upload", { method: "POST", body: fd })
      .then(r => r.json())
      .then(data => {
        if (data.error) { alert(data.error); return; }
        const incoming = data.securities || [];
        const errors   = data.errors     || [];
        let inserted = 0, skipped = 0;
        for (const s of incoming) {
          // Dedupe by (id, calcType) — mirrors addSecurity()'s rule.
          if (_addedSecurities.find(x => x.id === s.id && x.calcType === s.calcType)) {
            skipped++; continue;
          }
          _addedSecurities.push(s);
          inserted++;
        }
        renderSecList();
        document.getElementById("results-section").classList.add("hidden");
        let msg = `${inserted} ativo${inserted === 1 ? "" : "s"} importado${inserted === 1 ? "" : "s"}`;
        if (_pendingRefDate) msg += ` (ref ${_pendingRefDate})`;
        msg += ".";
        if (skipped) msg += ` ${skipped} já estava${skipped === 1 ? "" : "m"} na lista.`;
        if (errors.length) msg += "\n\nLinhas ignoradas:\n• " + errors.join("\n• ");
        alert(msg);
      })
      .catch(err => alert(`Falha no upload: ${err.message}`))
      .finally(() => { e.target.value = ""; });
  });

  function exportarResultados() {
    if (!_resultsData) return;
    fetch("/api/precificacao/exportar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ results: _resultsData.results }),
    }).then(r => {
      if (!r.ok) throw new Error("Erro ao gerar Excel");
      return r.blob();
    }).then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "precificacao.xlsx";
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    }).catch(err => alert(err.message));
  }

  // ── Saved Lists (multiple named lists) ─────────────────────────────────
  let _savedLists = [];

  function loadSavedLists() {
    fetch("/api/precificacao/lists")
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data)) {
          _savedLists = data
            .map(l => ({
              name: String(l?.name || "").trim(),
              securities: Array.isArray(l?.securities) ? l.securities : [],
            }))
            .filter(l => l.name);
        } else if (data && Array.isArray(data.securities)) {
          // Defensive: tolerate the legacy single-template envelope.
          _savedLists = data.securities.length
            ? [{ name: "Lista padrão", securities: data.securities }]
            : [];
        } else {
          _savedLists = [];
        }
        renderSavedLists();
      });
  }

  function renderSavedLists() {
    const body = document.getElementById("saved-lists-body");
    if (!_savedLists.length) {
      body.innerHTML = `<p class="text-xs text-gray-300 italic">Nenhuma lista salva.</p>`;
      return;
    }
    body.innerHTML = `<div class="flex flex-col gap-1.5 max-h-40 overflow-y-auto pr-1">
      ${_savedLists.map((l, i) => {
        const n = (l.securities || []).length;
        return `
          <div class="flex items-center gap-1 border rounded-lg pl-3 pr-1 py-1 bg-orange-50/60">
            <button class="load-list-btn flex-1 text-left text-xs font-medium text-orange-700 hover:text-orange-900 truncate"
                    data-idx="${i}" title="Carregar lista">
              ${escHtml(l.name)} <span class="ml-1 text-[10px] text-gray-500 font-normal">(${n} ativo${n === 1 ? "" : "s"})</span>
            </button>
            <button class="edit-pu-btn text-[11px] text-gray-300 hover:text-orange-500 px-1.5"
                    data-idx="${i}" title="Editar Data PU">&#9998;</button>
            <button class="delete-list-btn text-[11px] text-gray-300 hover:text-red-500 px-1.5"
                    data-idx="${i}" title="Excluir lista">&#10005;</button>
          </div>`;
      }).join("")}
    </div>`;
  }

  document.getElementById("saved-lists-body").addEventListener("click", e => {
    const loadBtn = e.target.closest(".load-list-btn");
    if (loadBtn) {
      const list = _savedLists[Number(loadBtn.dataset.idx)];
      if (!list) return;
      _addedSecurities = [...(list.securities || [])];
      renderSecList();
      document.getElementById("results-section").classList.add("hidden");
      return;
    }
    const editPuBtn = e.target.closest(".edit-pu-btn");
    if (editPuBtn) {
      openEditPuModal(Number(editPuBtn.dataset.idx));
      return;
    }
    const delBtn = e.target.closest(".delete-list-btn");
    if (delBtn) {
      const list = _savedLists[Number(delBtn.dataset.idx)];
      if (!list) return;
      if (!confirm(`Excluir a lista "${list.name}"? Esta ação não pode ser desfeita.`)) return;
      fetch(`/api/precificacao/lists?name=${encodeURIComponent(list.name)}`, { method: "DELETE" })
        .then(r => r.json())
        .then(data => {
          if (data && data.error) { alert(data.error); return; }
          loadSavedLists();
        });
    }
  });

  // ── Save Modal (asks for the list name; clicking an existing list
  //    fills the input so the operator can overwrite it in one click) ────
  function openSaveModal() {
    if (!_addedSecurities.length) { alert("Adicione pelo menos um ativo antes de salvar."); return; }
    document.getElementById("save-name-input").value = "";
    document.getElementById("save-existing-hint").classList.add("hidden");
    renderSaveExistingChips();
    document.getElementById("save-modal").classList.remove("hidden");
    setTimeout(() => document.getElementById("save-name-input").focus(), 50);
  }
  function closeSaveModal() { document.getElementById("save-modal").classList.add("hidden"); }

  function renderSaveExistingChips() {
    const ex = document.getElementById("save-existing-list");
    if (!_savedLists.length) {
      ex.innerHTML = '<span class="italic text-gray-300">Nenhuma.</span>';
      return;
    }
    const currentName = document.getElementById("save-name-input").value.trim();
    ex.innerHTML = _savedLists.map(l => {
      const selected = l.name === currentName;
      const base = "save-existing-chip inline-block px-2 py-0.5 border rounded-md cursor-pointer transition-colors";
      const tone = selected
        ? "bg-orange-100 border-orange-300 text-orange-700"
        : "bg-gray-50 hover:bg-orange-50 hover:border-orange-200";
      return `<span class="${base} ${tone}" data-name="${escHtml(l.name)}" title="Clique para substituir esta lista">
        ${escHtml(l.name)} <span class="text-gray-400">(${(l.securities||[]).length})</span>
      </span>`;
    }).join("");
  }

  document.getElementById("save-existing-list").addEventListener("click", e => {
    const chip = e.target.closest(".save-existing-chip");
    if (!chip) return;
    const input = document.getElementById("save-name-input");
    input.value = chip.dataset.name;
    input.dispatchEvent(new Event("input"));
    input.focus();
  });

  document.getElementById("save-name-input").addEventListener("input", e => {
    const v = e.target.value.trim();
    const exists = v && _savedLists.some(l => l.name === v);
    document.getElementById("save-existing-hint").classList.toggle("hidden", !exists);
    renderSaveExistingChips();
  });

  document.getElementById("save-name-input").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); saveList(); }
  });

  function openFormulasModal() { document.getElementById("formulas-modal").classList.remove("hidden"); }
  function closeFormulasModal() { document.getElementById("formulas-modal").classList.add("hidden"); }

  function saveList() {
    const name = document.getElementById("save-name-input").value.trim();
    if (!name) { alert("Informe o nome da lista."); return; }
    fetch("/api/precificacao/lists", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        securities: _addedSecurities.map(s => ({
          id: s.id, beehusName: s.beehusName, mainId: s.mainId || "",
          securityType: s.securityType || "", indexer: s.indexer || "",
          indexerPercentual: s.indexerPercentual, calcType: s.calcType || "",
          benchmarkId: s.benchmarkId || "", benchmarkName: s.benchmarkName || "",
          walletId: s.walletId || "", walletName: s.walletName || "",
          pricingType: s.pricingType || "", lastPU: s.lastPU, lastPUDate: s.lastPUDate,
          posPU: s.posPU ?? null, positionDate: s.positionDate || "",
          transactions: s.transactions || [], weightedYield: s.weightedYield ?? null,
        })),
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) { alert(data.error); return; }
        closeSaveModal();
        loadSavedLists();
      });
  }

  // ── Edit Data PU Modal ────────────────────────────────────────────────────
