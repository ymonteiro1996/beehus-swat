/* Precificação — modais de edição de PU e de configuração/benchmarks.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  let _editPuListIdx = null;

  function openEditPuModal(idx) {
    const list = _savedLists[idx];
    if (!list) return;
    _editPuListIdx = idx;
    document.getElementById("edit-pu-list-name").textContent = list.name;
    const tbody = document.getElementById("edit-pu-tbody");
    tbody.innerHTML = (list.securities || []).map((s, i) => {
      const datePU = s.positionDate || s.lastPUDate || "";
      return `
        <tr class="border-t border-gray-100">
          <td class="py-2 pr-3">
            <p class="font-medium text-gray-800">${escHtml(s.beehusName || s.id || "—")}</p>
            <p class="text-[10px] text-gray-400 font-mono">${escHtml(s.mainId || s.id || "")}</p>
          </td>
          <td class="py-2 pr-3 text-gray-500 text-[10px]">${escHtml(s.walletName || "—")}</td>
          <td class="py-2 text-right">
            <input type="date" data-sec-idx="${i}"
              class="edit-pu-date border border-gray-200 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-orange-400"
              value="${escHtml(datePU)}" />
          </td>
        </tr>`;
    }).join("");
    document.getElementById("edit-pu-modal").classList.remove("hidden");
  }

  function closeEditPuModal() {
    document.getElementById("edit-pu-modal").classList.add("hidden");
    _editPuListIdx = null;
  }

  function saveEditedPu() {
    if (_editPuListIdx === null) return;
    const list = _savedLists[_editPuListIdx];
    if (!list) return;
    const inputs = document.querySelectorAll("#edit-pu-tbody .edit-pu-date");
    const updated = list.securities.map((s, i) => {
      const newDate = (inputs[i]?.value || "").trim();
      return { ...s, positionDate: newDate };
    });
    fetch("/api/precificacao/lists", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: list.name, securities: updated }),
    })
      .then(r => r.json())
      .then(data => {
        if (data && data.error) { alert(data.error); return; }
        closeEditPuModal();
        loadSavedLists();
      })
      .catch(() => alert("Erro ao salvar."));
  }

  // ── Config Modal ───────────────────────────────────────────────────────────
  let _cfgBenchmarks  = [];
  let _cfgSelected    = null;
  let _cfgSearchTimer = null;

  function openConfigModal() {
    _cfgSelected = null;
    document.getElementById("cfg-search-input").value = "";
    document.getElementById("cfg-bm-name").value      = "";
    document.getElementById("cfg-selected-info").classList.add("hidden");
    fetch("/api/precificacao/config")
      .then(r => r.json())
      .then(({ benchmarks }) => {
        _cfgBenchmarks = benchmarks.map(b => ({ ...b }));
        renderCfgList();
        document.getElementById("config-modal").classList.remove("hidden");
      });
  }
  function closeConfigModal() { document.getElementById("config-modal").classList.add("hidden"); }

  function renderCfgList() {
    const el = document.getElementById("cfg-list");
    if (!_cfgBenchmarks.length) { el.innerHTML = '<p class="text-xs text-gray-300 italic">Nenhum benchmark.</p>'; return; }
    el.innerHTML = _cfgBenchmarks.map((b, i) => `
      <div class="flex items-center justify-between gap-3 border rounded-lg px-3 py-2 bg-gray-50 mb-1.5">
        <div class="min-w-0">
          <p class="text-xs font-semibold text-gray-800">${escHtml(b.name)}</p>
          <p class="text-[10px] font-mono text-gray-400 truncate">${escHtml(b.beehusName || "")} · ${escHtml(b.id)}</p>
          ${b.lastDate ? `<p class="text-[10px] text-gray-400">Último dado: ${escHtml(b.lastDate)}</p>` : ""}
        </div>
        <button onclick="_cfgBenchmarks.splice(${i},1);renderCfgList()" class="shrink-0 text-gray-300 hover:text-red-500 text-xs">&#10005;</button>
      </div>`).join("");
  }

  function onCfgSearchInput() {
    clearTimeout(_cfgSearchTimer);
    const q = document.getElementById("cfg-search-input").value.trim();
    if (q.length < 2) { hideCfgDropdown(); return; }
    _cfgSearchTimer = setTimeout(() => {
      fetch(`/api/precificacao/search?q=${encodeURIComponent(q)}`)
        .then(r => r.json())
        .then(({ results }) => {
          const dd = document.getElementById("cfg-search-dropdown");
          if (!results.length) { dd.innerHTML = '<p class="px-4 py-3 text-xs text-gray-400 italic">Nenhum resultado.</p>'; }
          else { dd.innerHTML = results.map(r => `
            <div class="drop-row cfg-drop-row" data-id="${escHtml(r.id)}" data-name="${escHtml(r.beehusName)}" data-mainid="${escHtml(r.mainId)}">
              <p class="font-medium text-gray-800 text-xs">${escHtml(r.beehusName)}</p>
              <p class="text-[10px] text-gray-400 font-mono mt-0.5">${escHtml(r.mainId)} · ${escHtml(r.securityType)}</p>
            </div>`).join(""); }
          dd.classList.remove("hidden");
        });
    }, 250);
  }
  function hideCfgDropdown() { setTimeout(() => document.getElementById("cfg-search-dropdown").classList.add("hidden"), 150); }

  document.getElementById("cfg-search-dropdown").addEventListener("mousedown", e => {
    const row = e.target.closest(".cfg-drop-row");
    if (!row) return;
    _cfgSelected = { id: row.dataset.id, beehusName: row.dataset.name, mainId: row.dataset.mainid };
    document.getElementById("cfg-search-input").value = row.dataset.name;
    document.getElementById("cfg-sel-name").textContent = row.dataset.name;
    document.getElementById("cfg-sel-id").textContent   = row.dataset.id;
    document.getElementById("cfg-selected-info").classList.remove("hidden");
    const ni = document.getElementById("cfg-bm-name"); if (!ni.value) ni.value = row.dataset.name;
    hideCfgDropdown();
  });

  function addBenchmark() {
    if (!_cfgSelected) { alert("Selecione um ativo na busca."); return; }
    const name = document.getElementById("cfg-bm-name").value.trim();
    if (!name) { alert("Informe um nome."); return; }
    if (_cfgBenchmarks.find(b => b.id === _cfgSelected.id)) { alert("Já adicionado."); return; }
    _cfgBenchmarks.push({ id: _cfgSelected.id, name, beehusName: _cfgSelected.beehusName, mainId: _cfgSelected.mainId });
    renderCfgList();
    _cfgSelected = null;
    document.getElementById("cfg-search-input").value = "";
    document.getElementById("cfg-bm-name").value = "";
    document.getElementById("cfg-selected-info").classList.add("hidden");
  }

  function saveConfig() {
    fetch("/api/precificacao/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ benchmarks: _cfgBenchmarks.map(b => ({ id: b.id, name: b.name })) }),
    }).then(() => {
      const opts = _cfgBenchmarks.length
        ? _cfgBenchmarks.map(b => `<option value="${escHtml(b.id)}">${escHtml(b.name)}</option>`).join("")
        : '<option value="">— configure benchmarks —</option>';
      document.getElementById("dp-benchmark").innerHTML          = opts;
      document.getElementById("dp-inflacao-benchmark").innerHTML = opts;
      closeConfigModal();
    });
  }

  // ── Keyboard / backdrop close ──────────────────────────────────────────────
  window.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!document.getElementById("formulas-modal").classList.contains("hidden")) closeFormulasModal();
    if (!document.getElementById("config-modal").classList.contains("hidden")) closeConfigModal();
    if (!document.getElementById("wallet-sec-modal").classList.contains("hidden")) closeWalletSecModal();
    if (!document.getElementById("save-modal").classList.contains("hidden")) closeSaveModal();
    if (!document.getElementById("edit-pu-modal").classList.contains("hidden")) closeEditPuModal();
    if (!document.getElementById("refdate-modal").classList.contains("hidden")) closeRefDateModal();
  }, true);
  // Prevent accidental modal close when dragging text from inside to backdrop
