/* Painel de Controle — seções de detalhe (parte C).
   Escopo global; usa consts do bootstrap; ordem importa. */
  function _buildMappingRowHtml(r) {
    const tc = r.typeConfidence;
    const tcPct = tc ? (tc * 100).toFixed(0) + "%" : "—";
    const tcBadge = tc
      ? `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold ${_confCls(tc)}">${tcPct}</span>`
      : `<span class="text-gray-400">—</span>`;

    const c = r.candidate;
    const hasMatch = c && c.score > 0;
    const secId = c?.securityId || "";
    let secCell, mainIdCell, indexerCell, scoreBadge;
    const editIcon = `<svg class="w-3 h-3 inline-block text-gray-400 group-hover:text-blue-500 ml-1 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>`;
    if (c) {
      secCell = `<span class="text-[10px] text-gray-700 truncate">${_escHtml(c.beehusName)}</span>${editIcon}`;
      mainIdCell = `<span class="font-mono text-[10px] text-gray-500">${_escHtml(c.mainId)}</span>`;
      indexerCell = c.indexer
        ? `<span class="text-[10px] text-gray-600">${_escHtml(c.indexer)}</span>`
        : `<span class="text-gray-400 text-[10px]">—</span>`;
      scoreBadge = c.score === "manual"
        ? `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold bg-purple-100 text-purple-700" title="Seleção manual">manual</span>`
        : `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold ${_matchScoreCls(c.score)}" style="cursor:help" title="${_escHtml(_matchScoreTooltip(c))}">${Math.min(100, c.score)}%</span>`;
    } else {
      secCell = `<span class="text-gray-400 text-[10px]">— selecionar</span>${editIcon}`;
      mainIdCell = `<span class="text-gray-400 text-[10px]">—</span>`;
      indexerCell = `<span class="text-gray-400 text-[10px]">—</span>`;
      scoreBadge = `<span class="text-gray-400">—</span>`;
    }

    // All checkboxes are always clickable — a checked row with no match
    // is simply skipped by "Gerar JSON Mapeamento" and can still be
    // included/excluded from the "Cadastrar ativos" flow.
    const cbChecked = hasMatch && (c.score === "manual" || c.score >= 50) ? "checked" : "";
    const lp = r.lastPrice;
    const lpTitle = lp ? `${lp.date}` : "";

    return `<tr class="border-t border-gray-100 hover:bg-gray-50"
                data-uid="${_escHtml(r.unprocessedSecurityId)}"
                data-security-id="${_escHtml(secId)}"
                data-score="${c?.score || 0}"
                data-stype="${_escHtml(r.type)}"
                data-has-match="${hasMatch ? 1 : 0}">
      <td class="px-1 py-1.5 text-center">
        <input type="checkbox" ${cbChecked} onchange="_updateSelectionCount()" class="rounded" />
      </td>
      ${_crossCompanyMode ? _walletCompanyCells(r) : ``}
      <td class="px-2 py-1.5 text-gray-400 font-mono text-[10px] truncate"
          title="${_escHtml(_uidTooltip(r))}">${_escHtml(r.unprocessedSecurityId)}</td>
      <td class="px-2 py-1.5 clf-type">${r.type ? _typeSelect(r.unprocessedSecurityId, r.type) : '<span class="text-gray-400">—</span>'}</td>
      <td class="px-1 py-1.5 clf-conf text-center">${tcBadge}</td>
      <td class="px-2 py-1.5 truncate cursor-pointer group hover:bg-blue-50"
          title="${c ? _escHtml(c.beehusName) + ' — clique para alterar' : 'Clique para selecionar um security'}"
          onclick="_openSecuritySearch('${_escHtml(r.unprocessedSecurityId).replace(/'/g,"\\'")}')">${secCell}</td>
      <td class="px-2 py-1.5 truncate" title="${c ? _escHtml(c.mainId) : ''}">${mainIdCell}</td>
      <td class="px-2 py-1.5 truncate" title="${c && c.indexer ? _escHtml(c.indexer) : ''}">${indexerCell}</td>
      <td class="px-1 py-1.5 text-center">${scoreBadge}</td>
      <td class="px-1 py-1.5 text-center">
        <button onclick="_openWallets('${_escHtml(r.unprocessedSecurityId).replace(/'/g,"\\'")}')"
          class="inline-block px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 text-[10px] font-semibold hover:bg-blue-100 hover:text-blue-700 cursor-pointer" title="Ver carteiras">${r.walletCount}</button>
      </td>
      ${_crossCompanyMode ? `` : `
      <td class="px-1 py-1.5 text-right whitespace-nowrap tabular-nums">${_fmtNum(r.pu)}</td>
      <td class="px-1 py-1.5 text-right whitespace-nowrap tabular-nums" title="${lpTitle}">${_fmtNum(lp?.value)}</td>`}
    </tr>`;
  }

  // Single full-modal loading overlay shown while the matcher is identifying.
  function _mappingLoader(show) {
    const card = document.querySelector("#modal > div");
    const existing = document.getElementById("mapping-loader");
    if (!show) { existing?.remove(); return; }
    if (!card || existing) return;
    const ov = document.createElement("div");
    ov.id = "mapping-loader";
    ov.className = "absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white/75 rounded-xl";
    ov.innerHTML = `<svg class="w-10 h-10 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg><span class="text-sm font-medium text-gray-500">Identificando…</span>`;
    card.appendChild(ov);
  }

  function _renderMapeamento(issues) {
    const thead = document.getElementById("modal-thead");
    const tbody = document.getElementById("modal-tbody");
    const msg   = document.getElementById("modal-msg");
    // Fixed layout so the declared column widths (summing to 100%) are honoured
    // and the `truncate` cells clip long ids/names instead of stretching the
    // table past the modal (the modal body scrolls vertically only).
    thead.parentElement.style.tableLayout = "fixed";
    thead.innerHTML = `<tr>
      <th class="px-1 py-2 text-center" style="width:3%"><input type="checkbox" onchange="_selectAll(this.checked)" title="Selecionar todos" /></th>
      ${_crossCompanyMode ? `
      <th class="px-2 py-2 text-left" style="width:9%">Empresa</th>
      <th class="px-2 py-2 text-left" style="width:9%">Carteira</th>` : ``}
      <th class="px-2 py-2 text-left" style="width:${_crossCompanyMode ? 15 : 19}%">Unprocessed Security</th>
      <th class="px-2 py-2 text-left" style="width:${_crossCompanyMode ? 9 : 11}%">Tipo</th>
      <th class="px-1 py-2 text-center" style="width:${_crossCompanyMode ? 5 : 6}%">Conf.</th>
      <th class="px-2 py-2 text-left" style="width:${_crossCompanyMode ? 13 : 16}%">Security Encontrado</th>
      <th class="px-2 py-2 text-left" style="width:${_crossCompanyMode ? 9 : 11}%">mainId</th>
      <th class="px-2 py-2 text-left" style="width:${_crossCompanyMode ? 5 : 7}%">Indexador</th>
      <th class="px-1 py-2 text-center" style="width:${_crossCompanyMode ? 5 : 6}%">Match</th>
      <th class="px-1 py-2 text-center" style="width:5%">Cart.</th>
      ${_crossCompanyMode ? `` : `
      <th class="px-1 py-2 text-right" style="width:8%">PU</th>
      <th class="px-1 py-2 text-right" style="width:8%">Preço</th>`}
    </tr>`;
    tbody.innerHTML = "";
    msg.innerHTML = "";
    msg.style.display = "none";

    // Remove old filter bar / other renderers' toolbars if present
    document.getElementById("mapping-filters")?.remove();
    document.getElementById("c3-toolbar")?.remove();
    document.getElementById("class-toolbar")?.remove();

    const grouped = _groupBySecurity(issues);
    const ids = grouped.map(r => r.unprocessedSecurityId).filter(Boolean);
    if (!ids.length) {
      msg.textContent = "Nenhum item.";
      msg.style.display = "block";
      return;
    }

    // Store wallet data per uid for the sub-modal
    _walletsByUid = {};
    grouped.forEach(r => { _walletsByUid[r.unprocessedSecurityId] = r.wallets; });

    // Render skeleton rows immediately so the operator can pick a security
    // without waiting for the matcher. _mappingRows is initialised here so
    // any manual pick made via _selectSecurity during the wait survives the
    // post-fetch merge below.
    _mappingRows = grouped.map(r => ({
      ...r,
      type:           "",
      typeConfidence: 0,
      candidate:      null,
      pu:             null,
      lastPrice:      null,
    }));
    tbody.innerHTML = _mappingRows.map(_buildMappingRowHtml).join("");
    _updateSelectionCount();

    // Single full-modal spinner while the matcher identifies all rows.
    msg.style.display = "none";
    _mappingLoader(true);

    const items = ids.map(id => ({ unprocessedId: id }));

    fetch("/api/controlpanel/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, companyId: _currentCompanyId, date: _currentDate })
    }).then(r => r.json()).then(({ results }) => {
      const byId = {};
      (results || []).forEach(r => byId[r.unprocessedId] = r);

      // Merge match results into existing _mappingRows. A row whose
      // candidate.score === "manual" was picked by the operator while the
      // matcher was still running — preserve that pick instead of letting
      // the server suggestion overwrite it.
      _mappingRows.forEach(row => {
        const m = byId[row.unprocessedSecurityId];
        if (!m) return;
        row.type           = m.predicted_type || "";
        row.typeConfidence = m.type_confidence || 0;
        row.pu             = m.pu ?? null;
        row.extracted      = m.extracted || {};   // isin/ticker/taxId/type for the asset-info line
        const isManual = row.candidate && row.candidate.score === "manual";
        if (!isManual) {
          row.candidate = m.candidate || null;
          row.lastPrice = m.lastPrice ?? null;
        }
      });

      // Manual picks sort to the top — they have no numeric score to
      // compare against the matcher's 0–100 range.
      _mappingRows.sort((a, b) => {
        const sa = a.candidate?.score === "manual" ? Infinity : (a.candidate?.score || 0);
        const sb = b.candidate?.score === "manual" ? Infinity : (b.candidate?.score || 0);
        return sb - sa;
      });

      // Insert filter bar above table (depends on the set of types the
      // matcher returned, so it has to wait until now).
      const tableParent = tbody.closest(".overflow-y-auto");
      if (tableParent) {
        const bar = document.createElement("div");
        bar.id = "mapping-filters";
        bar.innerHTML = _buildFilterBar(_mappingRows);
        tableParent.parentElement.insertBefore(bar, tableParent);
      }

      tbody.innerHTML = _mappingRows.map(_buildMappingRowHtml).join("");
      msg.style.display = "none";
      _mappingLoader(false);
      _updateSelectionCount();

      // Fallback: fetch price individually for candidates whose price was not
      // resolved in the batch (mirrors the manual-selection flow exactly).
      const _lpDateParam = _currentDate ? `&date=${encodeURIComponent(_currentDate)}` : "";
      _mappingRows.forEach(row => {
        if (!row.candidate || row.candidate.score === "manual") return;
        if (row.lastPrice && row.lastPrice.value != null) return;
        const sid = row.candidate.securityId;
        if (!sid) return;
        fetch(`/api/controlpanel/last-price?securityId=${encodeURIComponent(sid)}${_lpDateParam}`)
          .then(r => r.json())
          .then(({ lastPrice }) => {
            if (!lastPrice || lastPrice.value == null) return;
            row.lastPrice = lastPrice;
            const tr2 = document.querySelector(`#modal-tbody tr[data-uid="${CSS.escape(row.unprocessedSecurityId)}"]`);
            if (!tr2) return;
            const cells2 = tr2.querySelectorAll("td");
            if (cells2[10]) {
              cells2[10].title = lastPrice.date || "";
              cells2[10].innerHTML = _fmtNum(lastPrice.value);
            }
          })
          .catch(() => {});
      });
    }).catch(err => {
      console.error("match error:", err);
      msg.style.display = "none";
      // Drop the loader so a failed matcher doesn't leave the modal spinning;
      // skeleton rows stay so the operator can still pick a security manually.
      _mappingLoader(false);
    });
  }

  function _renderDefault(issues) {
    document.getElementById("mapping-filters")?.remove();
    document.getElementById("c3-toolbar")?.remove();
    document.getElementById("class-toolbar")?.remove();
    const thead0 = document.getElementById("modal-thead");
    // Reset to auto layout (the Mapeamento render switches it to fixed).
    thead0.parentElement.style.tableLayout = "";
    const cross = _crossCompanyMode;
    const showPrice = _currentType === "security_missing_history_price";
    thead0.innerHTML = `<tr>
      ${cross ? `<th class="px-3 py-2 text-left">Empresa</th>` : ``}
      <th class="px-3 py-2 text-left">External ID</th>
      <th class="px-3 py-2 text-left">Carteira</th>
      <th class="px-3 py-2 text-left">Security</th>
      ${showPrice ? `<th class="px-3 py-2 text-left">BeehusName</th><th class="px-3 py-2 text-left">Tipo de Preço</th>` : ``}
      <th class="px-3 py-2 text-left">Unprocessed Security</th>
      <th class="px-3 py-2 text-left">Criado em</th>
      <th class="px-3 py-2"></th>
    </tr>`;
    document.getElementById("modal-tbody").innerHTML = issues.map(i =>
      `<tr class="border-t border-gray-100 hover:bg-gray-50">
        ${cross ? `<td class="px-3 py-2 text-gray-700 text-[10px] truncate">${_escHtml(i.company || '')}</td>` : ``}
        <td class="px-3 py-2 text-gray-600 font-mono text-[10px]">${_escHtml(i.externalId)}</td>
        <td class="px-3 py-2 text-gray-700 text-[10px]">
          ${_escHtml(i.walletName || '')}
          ${i.walletId ? `<span class="block text-gray-400 font-mono">${_escHtml(i.walletId)}</span>` : ''}
        </td>
        <td class="px-3 py-2 text-gray-400 font-mono text-[10px]">${_escHtml(i.securityId)}</td>
        ${showPrice ? `
        <td class="px-3 py-2 text-gray-700 text-[10px]">${i.beehusName ? _escHtml(i.beehusName) : '—'}</td>
        <td class="px-3 py-2 text-gray-700 text-[10px] font-mono">${i.priceType ? _escHtml(i.priceType) : '—'}</td>` : ``}
        <td class="px-3 py-2 text-gray-400 font-mono text-[10px]">${_escHtml(i.unprocessedSecurityId)}</td>
        <td class="px-3 py-2 text-gray-400 whitespace-nowrap">${_escHtml(i.createdAt)}</td>
        <td class="px-3 py-2"></td>
      </tr>`).join("");
  }

  // ── Classificação (security_missing_classification) ────────────────────────
  // Cada empresa tem sua PRÓPRIA árvore de categorias (Renda Fixa > Caixa,
  // etc — tela Parceiros > Classificação de Ativos). Não existe (ainda) rota
  // de escrita conhecida pra vincular um ativo a um nó dessa árvore, então:
  // o operador seleciona a categoria por linha aqui e "Gerar JSON
  // Classificação" salva um arquivo em arquivos externos/ pra aplicar
  // manualmente — mesma ideia do "Gerar JSON Mapeamento".

  let _classificacaoRows = [];
  let _companyVariablesCache = {};  // companyId -> [{id, label}]

  function _dedupeClassificacao(issues) {
    const map = {};
    issues.forEach(i => {
      const cid = i.companyId || _currentCompanyId || "";
      const key = `${cid}::${i.securityId || i.externalId || ""}`;
      if (!map[key]) {
        map[key] = {
          companyId: cid,
          company: i.company || "",
          securityId: i.securityId || "",
          beehusName: i.beehusName || "",
          mainId: i.mainId || "",
          unprocessedId: i.unprocessedSecurityId || "",
          wallets: {},
        };
      }
      if (i.walletId) map[key].wallets[i.walletId] = i.walletName || "";
    });
    return Object.values(map).map(r => ({ ...r, walletNames: Object.values(r.wallets) }));
  }

  async function _loadCompanyVariables(companyId) {
    if (!companyId) return [];
    if (_companyVariablesCache[companyId]) return _companyVariablesCache[companyId];
    try {
      const res = await fetch(`/api/controlpanel/company-variables?companyId=${encodeURIComponent(companyId)}`);
      const data = await res.json();
      _companyVariablesCache[companyId] = data.nodes || [];
    } catch (e) {
      _companyVariablesCache[companyId] = [];
    }
    return _companyVariablesCache[companyId];
  }

  async function _renderClassificacao(issues) {
    const thead = document.getElementById("modal-thead");
    thead.parentElement.style.tableLayout = "";
    const cross = _crossCompanyMode;
    thead.innerHTML = `<tr>
      <th class="px-2 py-2 text-center w-8"><input type="checkbox" onchange="_classSelectAll(this.checked)" title="Selecionar todos" /></th>
      ${cross ? `<th class="px-3 py-2 text-left">Empresa</th><th class="px-3 py-2 text-left">Carteira</th>` : ``}
      <th class="px-3 py-2 text-left">beehusName</th>
      <th class="px-3 py-2 text-left">mainId</th>
      <th class="px-3 py-2 text-left">Categoria</th>
    </tr>`;

    _classificacaoRows = _dedupeClassificacao(issues);
    document.getElementById("modal-tbody").innerHTML =
      `<tr><td colspan="6" class="px-3 py-6 text-center text-gray-400">Carregando árvore de categorias…</td></tr>`;

    const companyIds = [...new Set(_classificacaoRows.map(r => r.companyId).filter(Boolean))];
    await Promise.all(companyIds.map(cid => _loadCompanyVariables(cid)));

    document.getElementById("modal-tbody").innerHTML = _classificacaoRows.map((r, idx) => `
      <tr class="border-t border-gray-100 hover:bg-gray-50" data-idx="${idx}">
        <td class="px-2 py-2 text-center"><input type="checkbox" class="class-check" onchange="_classUpdateCount()" /></td>
        ${cross ? `
        <td class="px-3 py-2 text-[10px] text-gray-700 truncate">${_escHtml(r.company)}</td>
        <td class="px-3 py-2 text-[10px] text-gray-500 truncate">${r.walletNames.length === 1 ? _escHtml(r.walletNames[0]) : `${r.walletNames.length} carteiras`}</td>` : ``}
        <td class="px-3 py-2 text-[10px] text-gray-700">${r.beehusName ? _escHtml(r.beehusName) : "—"}</td>
        <td class="px-3 py-2 font-mono text-[10px] text-gray-500">${r.mainId ? _escHtml(r.mainId) : "—"}</td>
        <td class="px-3 py-2">
          <div class="flex items-center gap-1.5">
            <select class="class-select border rounded px-1.5 py-1 text-[10px] bg-white w-full max-w-[220px]" onchange="_classUpdateCount()">
              <option value="">— selecionar —</option>
              ${(_companyVariablesCache[r.companyId] || []).map(n =>
                `<option value="${_escHtml(n.id)}">${_escHtml(n.label)}</option>`
              ).join("")}
            </select>
          </div>
        </td>
      </tr>`).join("") || `<tr><td colspan="6" class="px-3 py-6 text-center text-gray-400">Nenhum item.</td></tr>`;

    document.getElementById("mapping-filters")?.remove();
    document.getElementById("c3-toolbar")?.remove();
    document.getElementById("class-toolbar")?.remove();
    const wrap = document.getElementById("modal-tbody").closest(".overflow-y-auto");
    const bar = document.createElement("div");
    bar.id = "class-toolbar";
    bar.className = "sticky top-0 bg-white border-b px-6 py-2 flex items-center gap-4 text-xs z-10";
    bar.innerHTML = `
      <span id="class-sel-count" class="text-gray-400">0 selecionado(s)</span>
      <span class="flex-1"></span>
      <button onclick="_gerarJsonClassificacao()" id="class-json-btn"
        class="px-3 py-1.5 bg-indigo-600 text-white rounded text-xs font-medium opacity-40 cursor-not-allowed" disabled
        title="Salva um JSON em arquivos externos/ com as classificações escolhidas — não grava direto na Beehus (ainda não existe rota de escrita conhecida)">
        &#8595; Gerar JSON Classificação
      </button>`;
    wrap.parentElement.insertBefore(bar, wrap);
    _classUpdateCount();
  }

  function _classSelectAll(checked) {
    document.querySelectorAll("#modal-tbody .class-check").forEach(cb => cb.checked = checked);
    _classUpdateCount();
  }

  function _classUpdateCount() {
    const rows = Array.from(document.querySelectorAll("#modal-tbody tr[data-idx]"));
    const checkedRows = rows.filter(tr => tr.querySelector(".class-check")?.checked);
    const el = document.getElementById("class-sel-count");
    if (el) el.textContent = `${checkedRows.length} selecionado(s)`;
    const withCategory = checkedRows.filter(tr => tr.querySelector(".class-select")?.value);
    const btn = document.getElementById("class-json-btn");
    if (btn) {
      btn.disabled = withCategory.length === 0;
      btn.className = withCategory.length > 0
        ? "px-3 py-1.5 bg-indigo-600 text-white rounded text-xs font-medium hover:bg-indigo-700"
        : "px-3 py-1.5 bg-indigo-600 text-white rounded text-xs font-medium opacity-40 cursor-not-allowed";
    }
  }

  async function _gerarJsonClassificacao() {
    const rows = Array.from(document.querySelectorAll("#modal-tbody tr[data-idx]"));
    const items = [];
    rows.forEach(tr => {
      const cb = tr.querySelector(".class-check");
      const sel = tr.querySelector(".class-select");
      if (!cb?.checked || !sel?.value) return;
      const r = _classificacaoRows[Number(tr.dataset.idx)];
      items.push({
        companyId: r.companyId, company: r.company,
        securityId: r.securityId, beehusName: r.beehusName,
        unprocessedId: r.unprocessedId,
        nodeId: sel.value, nodeLabel: sel.options[sel.selectedIndex].textContent,
      });
    });
    if (!items.length) { alert("Nenhum item com categoria selecionada."); return; }

    const btn = document.getElementById("class-json-btn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "Gerando…";
    try {
      const res = await fetch("/api/controlpanel/classificacao/gerar-json", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      const data = await res.json();
      if (data.error) { alert(`Erro: ${data.error}`); return; }
      alert(`JSON gerado: ${data.file} (${data.count} item(ns)) em "arquivos externos/".`);
    } catch (e) {
      alert(`Erro: ${e}`);
    } finally {
      btn.innerHTML = orig;
      _classUpdateCount();
    }
  }

  // ── Registro de Preço (security_missing_price) ─────────────────────────────

  function _renderRegistroPreco(issues) {
    _c3Rows = issues.map(i => ({ ...i, c3: false, consumoAuto: false }));
    const cross = _crossCompanyMode;

    // toolbar above table
    const wrap = document.getElementById("modal-tbody").closest(".overflow-y-auto");
    document.getElementById("mapping-filters")?.remove();
    document.getElementById("class-toolbar")?.remove();
    document.getElementById("c3-toolbar")?.remove();
    const bar = document.createElement("div");
    bar.id = "c3-toolbar";
    bar.className = "sticky top-0 bg-white border-b px-6 py-2 flex items-center gap-4 text-xs z-10";
    bar.innerHTML = `
      <span id="c3-sel-count" class="text-gray-400">0 selecionados</span>
      <span class="flex-1"></span>
      <button onclick="_exportC3()" id="c3-export-btn"
        class="px-3 py-1.5 bg-green-600 text-white rounded text-xs font-medium opacity-40 cursor-not-allowed"
        disabled>
        &#8595; Gerar Excel C3
      </button>`;
    wrap.parentElement.insertBefore(bar, wrap);

    document.getElementById("modal-thead").innerHTML = `<tr>
      <th class="px-2 py-2 text-center w-8">
        <input type="checkbox" title="Selecionar todos" onchange="_c3SelectAll(this.checked)">
      </th>
      ${cross ? `<th class="px-3 py-2 text-left">Empresa</th><th class="px-3 py-2 text-left">Carteira</th>` : ``}
      <th class="px-3 py-2 text-left">Security ID</th>
      <th class="px-3 py-2 text-left">BeehusName</th>
      ${cross ? `` : `<th class="px-3 py-2 text-left">Carteira</th>`}
      <th class="px-3 py-2 text-center w-20">C3</th>
      <th class="px-3 py-2 text-center w-32">Consumo Automático</th>
    </tr>`;

    document.getElementById("modal-tbody").innerHTML = _c3Rows.map((r, i) =>
      `<tr class="border-t border-gray-100 hover:bg-gray-50">
        <td class="px-2 py-2 text-center">
          <input type="checkbox" onchange="_c3Toggle(${i}, this.checked)">
        </td>
        ${cross ? `
        <td class="px-3 py-2 text-[10px] text-gray-700 truncate">${r.company ? _escHtml(r.company) : '—'}</td>
        <td class="px-3 py-2 text-[10px] text-gray-500 truncate">${r.walletName ? _escHtml(r.walletName) : '—'}</td>` : ``}
        <td class="px-3 py-2 font-mono text-[10px] text-gray-600">${r.securityId ? _escHtml(r.securityId) : '—'}</td>
        <td class="px-3 py-2 text-[10px] text-gray-700">${r.beehusName ? _escHtml(r.beehusName) : '—'}</td>
        ${cross ? `` : `
        <td class="px-3 py-2 text-[10px] text-gray-500">
          ${r.walletName ? `<span class="text-gray-700">${_escHtml(r.walletName)}</span>` : ''}
          ${r.walletId ? `<span class="block font-mono text-gray-400">${_escHtml(r.walletId)}</span>` : '—'}
        </td>`}
        <td class="px-2 py-2 text-center">
          <input type="checkbox" id="c3-c3-${i}" onchange="_c3ToggleC3(${i}, this.checked)">
        </td>
        <td class="px-2 py-2 text-center">
          <input type="checkbox" id="c3-auto-${i}" onchange="_c3ToggleAuto(${i}, this.checked)">
        </td>
      </tr>`
    ).join("");
  }

  function _c3SelectAll(checked) {
    _c3Rows.forEach((_, i) => {
      _c3Rows[i].c3 = checked;
      _c3Rows[i].consumoAuto = checked;
      const cb = document.getElementById(`c3-c3-${i}`);
      if (cb) cb.checked = checked;
      const auto = document.getElementById(`c3-auto-${i}`);
      if (auto) auto.checked = checked;
    });
    document.querySelectorAll("#modal-tbody > tr > td:first-child input").forEach(cb => cb.checked = checked);
    _c3UpdateCount();
  }

  function _c3Toggle(i, checked) {
    // Selecionar a linha inteira (checkbox ao lado de Empresa) marca C3 e
    // Consumo Automático junto — evita o operador ter que marcar 3 caixas
    // por linha.
    if (checked) {
      _c3Rows[i].c3 = true;
      _c3Rows[i].consumoAuto = true;
      const c3cb = document.getElementById(`c3-c3-${i}`);
      if (c3cb) c3cb.checked = true;
      const autoCb = document.getElementById(`c3-auto-${i}`);
      if (autoCb) autoCb.checked = true;
    }
    _c3UpdateCount();
  }

  function _c3ToggleC3(i, checked) {
    _c3Rows[i].c3 = checked;
    _c3UpdateCount();
  }

  function _c3ToggleAuto(i, checked) {
    _c3Rows[i].consumoAuto = checked;
    // Consumo Automático implica C3 (o ativo consumido automaticamente
    // também precisa ir pro C3).
    if (checked) {
      _c3Rows[i].c3 = true;
      const c3cb = document.getElementById(`c3-c3-${i}`);
      if (c3cb) c3cb.checked = true;
      _c3UpdateCount();
    }
  }

  function _c3UpdateCount() {
    const sel = _c3Rows.filter(r => r.c3).length;
    const countEl = document.getElementById("c3-sel-count");
    const btn     = document.getElementById("c3-export-btn");
    if (countEl) countEl.textContent = `${sel} selecionado${sel !== 1 ? "s" : ""} (C3)`;
    if (btn) {
      btn.disabled = sel === 0;
      btn.className = sel > 0
        ? "px-3 py-1.5 bg-green-600 text-white rounded text-xs font-medium hover:bg-green-700"
        : "px-3 py-1.5 bg-green-600 text-white rounded text-xs font-medium opacity-40 cursor-not-allowed";
    }
  }

  function _exportC3() {
    const selected = _c3Rows.filter(r => r.c3);
    if (!selected.length) return;

    const items = selected.map(r => ({
      securityId:        r.securityId  || "",
      beehusName:        r.beehusName  || "",
      entityId:          "",
      companyId:         r.companyId || _currentCompanyId || "",
      walletId:          r.walletId    || "",
      pu:                0,
      consumoAutomatico: r.consumoAuto,
    }));

    const btn = document.getElementById("c3-export-btn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "Gerando…";

    fetch("/api/controlpanel/export-c3", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, date: _currentDate || "" }),
    }).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.blob();
    }).then(blob => {
      const url = URL.createObjectURL(blob);
      const a   = document.createElement("a");
      a.href     = url;
      a.download = "c3_registro_preco.xlsx";
      a.click();
      URL.revokeObjectURL(url);
      btn.innerHTML = orig;
      btn.disabled  = false;
    }).catch(err => {
      alert("Erro ao gerar Excel: " + err.message);
      btn.innerHTML = orig;
      btn.disabled  = false;
    });
  }

  function openModal(companyId, company, type) {
    _crossCompanyMode = false;
    _currentType = type;
    _currentCompanyId = companyId;
    document.getElementById("modal-title").textContent    = company;
    document.getElementById("modal-subtitle").textContent = `${type.replaceAll("_"," ")} · ${_currentDate}`;
    document.getElementById("modal-msg").textContent      = "Carregando...";
    document.getElementById("modal-msg").style.display    = "block";
    document.getElementById("modal-tbody").innerHTML      = "";
    document.getElementById("modal").style.display        = "flex";

    fetch(`/api/controlpanel/detail?companyId=${companyId}&date=${_currentDate}&type=${type}`)
      .then(r => r.json()).then(({ issues }) => {
        _modalIssues = issues;
        if (!issues.length) {
          document.getElementById("modal-msg").textContent = "Nenhum issue para esta seleção.";
          return;
        }
        document.getElementById("modal-description").textContent = issues[0]?.description || "";
        // Default to auto layout; _renderMapeamento opts into fixed. Reset here
        // so switching from Mapeamento to another issue type doesn't inherit it.
        document.getElementById("modal-thead").parentElement.style.tableLayout = "";
        _mappingLoader(false);  // drop any stale loader from a previous open
        if      (type === "security_unmapped")              _renderMapeamento(issues);
        else if (type === "security_missing_price")          _renderRegistroPreco(issues);
        else if (type === "security_missing_classification") _renderClassificacao(issues);
        else                                                 _renderDefault(issues);
        document.getElementById("modal-msg").style.display = "none";
      });
  }

  // Mesmo modal/renderers do openModal (Mapeamento/Classificação/Registro de
  // Preço/Default), só que cruzando TODAS as empresas visíveis de uma vez —
  // aberto pelo cabeçalho da coluna em vez de uma célula de uma empresa só.
  // _currentCompanyId fica "" (sem empresa única); _companyByWallet (populado
  // dentro de _groupBySecurity a partir de issue.companyId) é quem permite ao
  // "Mapear no sistema" saber em quais empresas aplicar cada mapeamento.
  function openModalAllCompanies(type, label) {
    _crossCompanyMode = true;
    _currentType = type;
    _currentCompanyId = "";
    document.getElementById("modal-title").textContent    = `${label} — todas as empresas`;
    document.getElementById("modal-subtitle").textContent = `${type.replaceAll("_"," ")} · ${_currentDate}`;
    document.getElementById("modal-msg").textContent      = "Carregando...";
    document.getElementById("modal-msg").style.display    = "block";
    document.getElementById("modal-tbody").innerHTML      = "";
    document.getElementById("modal").style.display        = "flex";

    fetch(`/api/controlpanel/detail-all-full?date=${_currentDate}&type=${type}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(({ issues }) => {
        _modalIssues = issues || [];
        if (!_modalIssues.length) {
          document.getElementById("modal-msg").textContent = "Nenhum issue para esta seleção — todas as empresas em dia.";
          return;
        }
        document.getElementById("modal-description").textContent = _modalIssues[0]?.description || "";
        document.getElementById("modal-thead").parentElement.style.tableLayout = "";
        _mappingLoader(false);
        if      (type === "security_unmapped")              _renderMapeamento(_modalIssues);
        else if (type === "security_missing_price")          _renderRegistroPreco(_modalIssues);
        else if (type === "security_missing_classification") _renderClassificacao(_modalIssues);
        else                                                 _renderDefault(_modalIssues);
        document.getElementById("modal-msg").style.display = "none";
      })
      .catch(err => {
        document.getElementById("modal-msg").textContent = `Erro ao carregar dados: ${err.message}`;
        document.getElementById("modal-msg").style.display = "block";
      });
  }

  function closeModal() {
    document.getElementById("modal").style.display = "none";
    document.getElementById("modal-description").textContent = "";
    _crossCompanyMode = false;
    _companyByWallet = {};
    _mappingLoader(false);
    document.getElementById("mapping-filters")?.remove();
    document.getElementById("c3-toolbar")?.remove();
    document.getElementById("class-toolbar")?.remove();
    _mappingRows = [];
    _c3Rows     = [];
  }

  function copyToClipboard() {
    let headers, rows;
    if (_currentType === "security_unmapped") {
      headers = ["Unprocessed Security", "Tipo Previsto", "Confiança", "Security Encontrado", "mainId", "Indexador", "Match", "Carteiras", "PU (unprocessed)", "Último Preço"];
      rows    = _mappingRows.map(r => {
        const c = r.candidate;
        return [
          r.unprocessedSecurityId,
          r.type,
          r.typeConfidence ? (r.typeConfidence * 100).toFixed(0) + "%" : "",
          c?.beehusName || "",
          c?.mainId || "",
          c?.indexer || "",
          c == null ? "" : (c.score === "manual" ? "manual" : Math.min(100, c.score) + "%"),
          r.walletCount,
          r.pu != null ? r.pu : "",
          r.lastPrice?.value != null ? r.lastPrice.value : "",
        ];
      });
    } else {
      // Demais renderers (classificação, registro de preço, default): copia o
      // que está visível no modal lendo direto da tabela. Mantém Copiar
      // alinhado com qualquer mudança de colunas sem precisar atualizar duas
      // listas em paralelo. Ignora <th> sem texto (ex. coluna de ações).
      const thead = document.getElementById("modal-thead");
      const tbody = document.getElementById("modal-tbody");
      const ths   = Array.from(thead?.querySelectorAll("th") || []);
      const keep  = ths.map(th => (th.textContent || "").trim() !== "");
      headers     = ths.filter((_, i) => keep[i]).map(th => th.textContent.trim());
      rows        = Array.from(tbody?.querySelectorAll("tr") || []).map(tr => {
        const tds = Array.from(tr.querySelectorAll("td"));
        return tds.filter((_, i) => keep[i]).map(td => {
          // Para células com input (Registro de Preço C3): exporta o estado
          // do checkbox como "sim"/"" em vez de string vazia.
          const cb = td.querySelector('input[type="checkbox"]');
          if (cb) return cb.checked ? "sim" : "";
          return (td.textContent || "").replace(/\s+/g, " ").trim();
        });
      });
    }
    const tsv = [headers, ...rows].map(r => r.join("\t")).join("\n");
    navigator.clipboard.writeText(tsv).then(() => {
      const btn = document.getElementById("copy-btn");
      btn.textContent = "Copiado!";
      setTimeout(() => btn.innerHTML = `<svg class="w-3.5 h-3.5 inline mr-1" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>Copiar`, 2000);
    });
  }

  document.getElementById("modal").addEventListener("click", e => { if (e.target === document.getElementById("modal")) closeModal(); });
  document.getElementById("wallets-modal").addEventListener("click", e => { if (e.target === document.getElementById("wallets-modal")) document.getElementById("wallets-modal").style.display = "none"; });
  document.getElementById("security-search-modal").addEventListener("click", e => {
    if (e.target === document.getElementById("security-search-modal")) _closeSecuritySearch();
  });
  document.getElementById("registration-modal").addEventListener("click", e => {
    if (e.target === document.getElementById("registration-modal")) {
      document.getElementById("registration-modal").style.display = "none";
      _regHideTip();
    }
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const sm = document.getElementById("security-search-modal");
    const rm = document.getElementById("registration-modal");
    const wm = document.getElementById("wallets-modal");
    const cd = document.getElementById("cell-detail-modal");
    const wi = document.getElementById("wallet-issues-modal");
    if (sm.style.display !== "none") _closeSecuritySearch();
    else if (rm.style.display !== "none") rm.style.display = "none";
    else if (wm.style.display !== "none") wm.style.display = "none";
    else if (cd && cd.style.display !== "none") closeCellDetail();
    // In the wallet-issues modal, Escape first steps back from the issues
    // view to the wallet picker; a second Escape closes the modal.
    else if (wi && wi.style.display !== "none") {
      if (document.getElementById("wi-issues").style.display !== "none") _wiShowPicker();
      else closeWalletIssues();
    }
    else closeModal();
  });

  // ── Cache management ───────────────────────────────────────────────────────
