/* Painel de Controle — seções de detalhe (parte A).
   Escopo global; usa consts do bootstrap; ordem importa. */
  function _switchSecuritySearchSource(source) {
    if (source !== "positions" && source !== "cadastro") return;
    if (source === "positions" && !IDENTIFICAR_ENABLED) return;  // tab hidden when off
    if (source === _secSearchSource) return;
    _secSearchSource = source;
    _setSecuritySearchTab(source);
    _runSecuritySearch();
  }

  function _debouncedSecuritySearch() {
    if (_secSearchTimer) clearTimeout(_secSearchTimer);
    _secSearchTimer = setTimeout(_runSecuritySearch, 200);
  }

  function _renderSecurityRows(results) {
    // Render rows for either source. Wallet-positions rows surface the PU
    // and the wallet names alongside the regular columns; cadastro rows
    // keep the legacy 6-column layout. The thead is rewritten in
    // _setSecuritySearchHeader to match.
    const tbody = document.getElementById("sec-search-tbody");
    _secSearchResults = results;
    if (_secSearchSource === "positions") {
      tbody.innerHTML = results.map((s, i) => {
        const wallets = (s.walletNames || []).join(", ");
        const pu = (s.pu != null && Number.isFinite(Number(s.pu)))
          ? Number(s.pu).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 8})
          : "—";
        return `<tr class="border-t border-gray-100 hover:bg-blue-50 cursor-pointer"
                onclick="_selectSecurityByIndex(${i})">
          <td class="px-3 py-2 text-gray-700 text-[11px]">${_escHtml(s.beehusName) || '<span class="text-gray-400">— sem cadastro</span>'}</td>
          <td class="px-3 py-2 font-mono text-[10px] text-gray-500">${_escHtml(s.mainId)}</td>
          <td class="px-3 py-2 text-[10px] text-gray-500">${_escHtml(s.securityType)}</td>
          <td class="px-3 py-2 text-[10px] text-gray-500 truncate max-w-xs" title="${_escHtml(wallets)}">${_escHtml(wallets)}</td>
          <td class="px-3 py-2 text-right font-mono text-[10px] text-gray-600">${pu}</td>
          <td class="px-3 py-2 text-right">
            <button class="px-2 py-0.5 bg-blue-600 text-white rounded text-[10px] hover:bg-blue-700">Selecionar</button>
          </td>
        </tr>`;
      }).join("");
    } else {
      tbody.innerHTML = results.map((s, i) =>
        `<tr class="border-t border-gray-100 hover:bg-blue-50 cursor-pointer"
              onclick="_selectSecurityByIndex(${i})">
          <td class="px-3 py-2 text-gray-700 text-[11px]">${_escHtml(s.beehusName)}</td>
          <td class="px-3 py-2 font-mono text-[10px] text-gray-500">${_escHtml(s.mainId)}</td>
          <td class="px-3 py-2 font-mono text-[10px] text-gray-500">${_escHtml(s.ticker)}</td>
          <td class="px-3 py-2 text-[10px] text-gray-500">${_escHtml(s.securityType)}</td>
          <td class="px-3 py-2 text-[10px] text-gray-500">${_escHtml(String(s.maturityDate || "").slice(0,10))}</td>
          <td class="px-3 py-2 text-right">
            <button class="px-2 py-0.5 bg-blue-600 text-white rounded text-[10px] hover:bg-blue-700">Selecionar</button>
          </td>
        </tr>`).join("");
    }
  }

  function _setSecuritySearchHeader() {
    const thead = document.getElementById("sec-search-thead");
    if (_secSearchSource === "positions") {
      thead.innerHTML = `<tr>
        <th class="px-3 py-2 text-left">Nome</th>
        <th class="px-3 py-2 text-left">mainId</th>
        <th class="px-3 py-2 text-left">Tipo</th>
        <th class="px-3 py-2 text-left">Carteira(s)</th>
        <th class="px-3 py-2 text-right">PU</th>
        <th class="px-3 py-2"></th>
      </tr>`;
    } else {
      thead.innerHTML = `<tr>
        <th class="px-3 py-2 text-left">Nome</th>
        <th class="px-3 py-2 text-left">mainId</th>
        <th class="px-3 py-2 text-left">Ticker</th>
        <th class="px-3 py-2 text-left">Tipo</th>
        <th class="px-3 py-2 text-left">Vencimento</th>
        <th class="px-3 py-2"></th>
      </tr>`;
    }
  }

  function _runSecuritySearch() {
    const q    = document.getElementById("sec-search-input").value.trim();
    const type = document.getElementById("sec-search-type").value;
    const msg  = document.getElementById("sec-search-msg");
    const tbody = document.getElementById("sec-search-tbody");
    const countEl = document.getElementById("sec-search-count");

    _setSecuritySearchHeader();

    // ── Wallet positions branch ────────────────────────────────────────────
    if (_secSearchSource === "positions") {
      const wallets = _walletsByUid[_secSearchUid] || [];
      if (!wallets.length) {
        tbody.innerHTML = "";
        msg.textContent = "Sem carteiras associadas a este unprocessedId.";
        msg.style.display = "block";
        countEl.textContent = "";
        return;
      }
      msg.textContent = "Carregando posições da carteira…";
      msg.style.display = "block";

      const params = new URLSearchParams();
      params.set("walletIds", wallets.map(w => w.id).join(","));
      if (_currentDate) params.set("date", _currentDate);
      if (q)            params.set("q", q);
      params.set("limit", "200");

      const myReqId = ++_secSearchReqId;
      fetch(`/api/controlpanel/wallet-positions?${params}`)
        .then(r => r.json())
        .then(data => {
          if (myReqId !== _secSearchReqId) return;
          const results = data.results || [];
          if (data.error) {
            tbody.innerHTML = "";
            msg.textContent = `Erro: ${data.error}`;
            msg.style.display = "block";
            return;
          }
          _secSearchPositionsCount = data.securityCount ?? results.length;
          document.getElementById("sec-search-positions-badge").textContent =
            _secSearchPositionsCount ? `(${_secSearchPositionsCount})` : "";
          countEl.textContent = `${results.length} resultado${results.length !== 1 ? "s" : ""}`
            + (data.walletsScanned ? ` · ${data.walletsScanned} carteira(s)` : "");
          if (!results.length) {
            tbody.innerHTML = "";
            msg.textContent = q
              ? "Nenhuma posição encontrada para o filtro digitado."
              : "Nenhuma posição processada recente para esta(s) carteira(s).";
            msg.style.display = "block";
            return;
          }
          msg.style.display = "none";
          _renderSecurityRows(results);
        })
        .catch(() => {
          msg.textContent = "Erro ao buscar posições.";
          msg.style.display = "block";
        });
      return;
    }

    // ── Cadastro branch (legacy) ───────────────────────────────────────────
    if (!q && !type) {
      tbody.innerHTML = "";
      msg.textContent = "Digite algo para buscar.";
      msg.style.display = "block";
      countEl.textContent = "";
      return;
    }

    msg.textContent = "Buscando…";
    msg.style.display = "block";

    const params = new URLSearchParams();
    if (q)    params.set("q", q);
    if (type) params.set("type", type);
    params.set("limit", "100");

    const myReqId = ++_secSearchReqId;
    fetch(`/api/controlpanel/search-securities?${params}`)
      .then(r => r.json())
      .then(data => {
        // Drop stale responses — a newer search is already in flight/complete.
        if (myReqId !== _secSearchReqId) return;
        const results = data.results || [];
        const cacheCount = data.cacheCount ?? 0;
        countEl.textContent = `${results.length} resultado${results.length !== 1 ? "s" : ""}`;
        if (data.error) {
          tbody.innerHTML = "";
          msg.textContent = `Erro: ${data.error}`;
          msg.style.display = "block";
          return;
        }
        if (!cacheCount) {
          tbody.innerHTML = "";
          msg.textContent = "Cache de securities vazio — clique em \"Atualizar Cache\" no topo da página.";
          msg.style.display = "block";
          return;
        }
        if (!results.length) {
          tbody.innerHTML = "";
          msg.textContent = `Nenhum security encontrado (cache: ${cacheCount}).`;
          msg.style.display = "block";
          return;
        }
        msg.style.display = "none";
        _renderSecurityRows(results);
      })
      .catch(() => {
        msg.textContent = "Erro ao buscar.";
        msg.style.display = "block";
      });
  }

  function _selectSecurityByIndex(i) {
    const sec = _secSearchResults[i];
    if (!sec) return;
    _selectSecurity(sec);
  }

  function _selectSecurity(sec) {
    const uid = _secSearchUid;
    if (!uid) return;

    // Update cached row state so Gerar JSON / Copiar reflect the manual pick
    const row = _mappingRows.find(r => r.unprocessedSecurityId === uid);
    if (row) {
      row.candidate = {
        securityId: sec.securityId,
        mainId:     sec.mainId,
        beehusName: sec.beehusName,
        indexer:    sec.indexer || "",
        score:      "manual",
        confidence: "manual",
        matched_on: ["manual"],
      };
      row.lastPrice = null;
    }

    // Update DOM cells of the edited row
    const tr = document.querySelector(`#modal-tbody tr[data-uid="${CSS.escape(uid)}"]`);
    if (tr) {
      tr.dataset.securityId = sec.securityId;
      tr.dataset.score = "manual";
      tr.dataset.hasMatch = "1";

      const cells = tr.querySelectorAll("td");
      // cells: 0=cb, 1=uid, 2=type, 3=conf, 4=secEnc, 5=mainId, 6=indexer, 7=match, 8=cart, 9=pu, 10=preço
      const editIcon = `<svg class="w-3 h-3 inline-block text-gray-400 group-hover:text-blue-500 ml-1 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>`;
      if (cells[4]) {
        cells[4].className = "px-2 py-1.5 truncate cursor-pointer group hover:bg-blue-50";
        cells[4].title = sec.beehusName + " — clique para alterar";
        cells[4].innerHTML = `<span class="text-[10px] text-gray-700 truncate">${_escHtml(sec.beehusName)}</span>${editIcon}`;
      }
      if (cells[5]) {
        cells[5].className = "px-2 py-1.5 truncate";
        cells[5].title = sec.mainId;
        cells[5].innerHTML = `<span class="font-mono text-[10px] text-gray-500">${_escHtml(sec.mainId)}</span>`;
      }
      if (cells[6]) {
        cells[6].className = "px-2 py-1.5 truncate";
        cells[6].title = sec.indexer || "";
        cells[6].innerHTML = sec.indexer
          ? `<span class="text-[10px] text-gray-600">${_escHtml(sec.indexer)}</span>`
          : `<span class="text-gray-400 text-[10px]">—</span>`;
      }
      if (cells[7]) {
        cells[7].innerHTML = `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold bg-purple-100 text-purple-700" title="Seleção manual">manual</span>`;
      }
      // Enable + check the row's checkbox
      const cb = cells[0]?.querySelector("input[type=checkbox]");
      if (cb) { cb.disabled = false; cb.checked = true; }
      // Reset price cell until we fetch it
      if (cells[10]) cells[10].innerHTML = `<span class="text-gray-400">—</span>`;
    }

    _closeSecuritySearch();
    _applyMappingFilters();
    _updateSelectionCount();

    // Background: fetch price for the position date (or nearest available).
    const dateParam = _currentDate ? `&date=${encodeURIComponent(_currentDate)}` : "";
    fetch(`/api/controlpanel/last-price?securityId=${encodeURIComponent(sec.securityId)}${dateParam}`)
      .then(r => r.json())
      .then(({ lastPrice }) => {
        if (row) row.lastPrice = lastPrice || null;
        const tr2 = document.querySelector(`#modal-tbody tr[data-uid="${CSS.escape(uid)}"]`);
        if (!tr2) return;
        const cells = tr2.querySelectorAll("td");
        if (cells[10]) {
          cells[10].title = lastPrice?.date || "";
          cells[10].innerHTML = _fmtNum(lastPrice?.value);
        }
      })
      .catch(() => {});
  }

  function _confCls(conf) {
    if (conf >= 0.8) return "bg-green-100 text-green-700";
    if (conf >= 0.5) return "bg-yellow-100 text-yellow-700";
    return "bg-orange-100 text-orange-700";
  }

  // Cache security types for dropdown
  let _securityTypes = [];
  fetch("/api/controlpanel/security-types").then(r => r.json()).then(d => { _securityTypes = d.types || []; });

  // Per-securityType field config for the "Cadastrar ativos" modal (bottom line).
  // Source: data/security_type_fields.json. Loaded on page entry; the modal is
  // only reachable several clicks later, so it is always populated by then.
  // `_secTypeFields[<securityType>] = { label, fields:[{key,label,input,...}] }`.
  let _secTypeFields = {};
  fetch("/api/controlpanel/security-type-fields").then(r => r.json()).then(d => { _secTypeFields = d.types || {}; });

  function _regFieldsFor(stype) {
    return (_secTypeFields[stype] && _secTypeFields[stype].fields) || [];
  }

  function _typeSelect(uid, selected) {
    const opts = _securityTypes.map(t =>
      `<option value="${_escHtml(t)}"${t === selected ? " selected" : ""}>${_escHtml(t)}</option>`).join("");
    return `<select onchange="_saveOverride('${_escHtml(uid).replace(/'/g,"\\'")}', this.value)"
      class="w-full min-w-0 border rounded px-1.5 py-0.5 text-[10px] font-semibold text-gray-700 bg-white focus:ring-2 focus:ring-blue-400 focus:outline-none">
      ${opts}</select>`;
  }

  function _saveOverride(uid, stype) {
    fetch("/api/controlpanel/classify/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unprocessedId: uid, securityType: stype })
    });
  }

  function _matchScoreCls(score) {
    if (score >= 75) return "bg-green-100 text-green-700";
    if (score >= 50) return "bg-blue-100 text-blue-700";
    if (score >= 25) return "bg-yellow-100 text-yellow-700";
    return "bg-gray-100 text-gray-400";
  }

  // Tooltip explaining HOW a candidate's match score was obtained: each signal
  // with its point contribution (summing to the total), then the displayed-%
  // note. Mirrors the Identificar Transações score tooltip. The server attaches
  // `candidate.breakdown` (security_matcher._score_breakdown) plus the
  // price-vs-PU verification (only in this mapping flow). Returns a multi-line
  // string for the badge's native `title` (\n renders as line breaks).
  function _matchScoreTooltip(c) {
    if (!c) return "";
    if (c.score === "manual") return "Seleção manual do operador";
    const bd = c.breakdown || [];
    if (!bd.length) return "Score do match: " + c.score;
    // L3 only here — there is no wallet position to scope to in mapping.
    const lines = ["Como o score foi calculado (L3 — cadastro completo):"];
    bd.forEach(e => lines.push(`• ${e.label}: ${(e.points >= 0 ? "+" : "") + e.points}`));
    lines.push("──────────────");
    lines.push(`Score total: ${c.score}`);
    if (typeof c.score === "number" && c.score > 100) {
      lines.push("(exibido limitado a 100%)");
    }
    return lines.join("\n");
  }

  // ── Mapping filters ───────────────────────────────────────────────────────
  // Manual selections always pass the score filter (the user explicitly
  // chose that security, so it has no numeric score to compare).
  function _rowScoreForFilter(tr) {
    const raw = tr.dataset.score || "";
    if (raw === "manual") return Infinity;
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  }

  function _applyMappingFilters() {
    const identified = document.getElementById("filter-identified")?.value || "all";
    const stype      = document.getElementById("filter-stype")?.value || "";
    const minRaw     = document.getElementById("filter-min-score")?.value ?? "";
    const minScore   = minRaw === "" ? 0 : Number(minRaw);

    document.querySelectorAll("#modal-tbody tr[data-uid]").forEach(tr => {
      const score = _rowScoreForFilter(tr);
      const type  = tr.dataset.stype || "";
      const hasMatch = tr.dataset.hasMatch === "1";

      let show = true;
      if (identified === "yes"  && !hasMatch) show = false;
      if (identified === "no"   &&  hasMatch) show = false;
      if (stype && type !== stype)            show = false;
      if (minScore > 0 && score < minScore)   show = false;

      tr.style.display = show ? "" : "none";
    });
    _updateSelectionCount();
  }

  // Iterate only the checkboxes of rows that are currently visible in the
  // mapping modal — keeps filter "Match mínimo (%)" / "Identificado" / "Tipo"
  // consistent with downstream actions (Aplicar / Gerar JSON / Cadastrar).
  function _visibleCheckedMappingRows() {
    const out = [];
    document.querySelectorAll("#modal-tbody tr[data-uid]").forEach(tr => {
      if (tr.style.display === "none") return;
      const cb = tr.querySelector("input[type=checkbox]");
      if (!cb || !cb.checked) return;
      out.push(tr);
    });
    return out;
  }

  function _selectAll(checked) {
    document.querySelectorAll("#modal-tbody tr[data-uid]").forEach(tr => {
      if (tr.style.display === "none") return;
      const cb = tr.querySelector("input[type=checkbox]");
      if (cb && !cb.disabled) cb.checked = checked;
    });
    _updateSelectionCount();
  }

  function _updateSelectionCount() {
    // Count only visible+checked rows so the badge matches what the
    // action buttons (Aplicar / Gerar JSON / Cadastrar) will actually use.
    const rows = _visibleCheckedMappingRows();
    const checked = rows.length;
    const el = document.getElementById("selection-count");
    if (el) el.textContent = `${checked} selecionado${checked !== 1 ? "s" : ""}`;
    // "Mapear no sistema" only sends rows that have a target security, so its
    // counter reflects what the PATCH will actually map (a checked row without a
    // match is skipped). Disable the button when there's nothing to map.
    const mappable = rows.filter(tr => tr.dataset.securityId).length;
    const cnt = document.getElementById("apply-mapping-count");
    if (cnt) cnt.textContent = mappable;
    const btn = document.getElementById("apply-mapping-btn");
    if (btn) btn.disabled = mappable === 0;
  }

  function _buildFilterBar(rows) {
    const types = [...new Set(rows.map(r => r.type).filter(Boolean))].sort();
    return `<div class="flex flex-wrap items-center gap-3 px-6 py-2 border-b bg-gray-50 text-xs">
      <label class="text-gray-500">Identificado:
        <select id="filter-identified" onchange="_applyMappingFilters()" class="ml-1 border rounded px-1.5 py-0.5 text-xs bg-white">
          <option value="all">Todos</option>
          <option value="yes">Sim</option>
          <option value="no">Não</option>
        </select>
      </label>
      <label class="text-gray-500">Tipo:
        <select id="filter-stype" onchange="_applyMappingFilters()" class="ml-1 border rounded px-1.5 py-0.5 text-xs bg-white">
          <option value="">Todos</option>
          ${types.map(t => `<option value="${_escHtml(t)}">${_escHtml(t)}</option>`).join("")}
        </select>
      </label>
      <label class="text-gray-500">Match mínimo (%):
        <input id="filter-min-score" type="number" min="0" max="100" value="0" onchange="_applyMappingFilters()" oninput="_applyMappingFilters()"
          class="ml-1 border rounded px-1.5 py-0.5 text-xs bg-white w-14" />
      </label>
      <span class="border-l pl-3 flex items-center gap-2">
        <button onclick="_selectAll(true)"  class="px-2 py-0.5 bg-blue-100 text-blue-700 rounded hover:bg-blue-200">Selecionar visíveis</button>
        <button onclick="_selectAll(false)" class="px-2 py-0.5 bg-gray-100 text-gray-600 rounded hover:bg-gray-200">Limpar seleção</button>
        <span id="selection-count" class="text-gray-400">0 selecionados</span>
      </span>
      <span class="ml-auto flex items-center gap-2">
        <button id="complement-btn" onclick="_openComplementModal()" class="flex items-center gap-1.5 px-3 py-1.5 bg-slate-500 text-white rounded hover:bg-slate-600 text-xs font-semibold" title="Importar informações complementares para melhorar o match de ativos não mapeados">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l4-4m0 0l4 4m-4-4v12"/></svg>
          Complemento
        </button>
        <button onclick="_openRegistrationModal()" class="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 text-white rounded hover:bg-emerald-700 text-xs font-semibold">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>
          Cadastrar ativos
        </button>
        <button onclick="_generateMappingJSON()" class="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-xs font-semibold">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          Gerar JSON Mapeamento
        </button>
        <button id="apply-mapping-btn" onclick="_applyMappingDirect()" class="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-blue-600"
          title="Envia os mapeamentos selecionados direto para o sistema (PATCH na API Beehus). O número é a quantidade de linhas marcadas com security que serão mapeadas.">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>
          Mapear no sistema
          <span id="apply-mapping-count" class="inline-flex items-center justify-center min-w-[1.25rem] px-1 py-0.5 rounded-full bg-white/25 text-white text-[10px] font-bold leading-none">0</span>
        </button>
      </span>
    </div>`;
  }

  // ── Generate mapping JSON ─────────────────────────────────────────────────
  function _generateMappingJSON() {
    const checked = [];
    _visibleCheckedMappingRows().forEach(tr => {
      const uid = tr.dataset.uid;
      const secId = tr.dataset.securityId;
      if (uid && secId) checked.push({ from: uid, to: secId });
    });

    if (!checked.length) {
      alert("Nenhum item selecionado.");
      return;
    }

    // Fetch securityMappingId for this company
    fetch(`/api/controlpanel/security-mapping-id?companyId=${_currentCompanyId}`)
      .then(r => r.json()).then(({ securityMappingId }) => {
        if (!securityMappingId) {
          alert("securityMappingId não encontrado para esta empresa.");
          return;
        }
        const payload = {
          securityMappingId,
          mappings: {
            mappingsToInclude: checked,
            mappingsToExclude: []
          }
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `mapping_${_currentCompanyId}_${_currentDate}.json`;
        a.click();
        URL.revokeObjectURL(url);
      });
  }

  // ── Apply mapping directly via Beehus API ────────────────────────────────
  async function _applyMappingDirect() {
    const checked = [];
    _visibleCheckedMappingRows().forEach(tr => {
      const uid = tr.dataset.uid;
      const secId = tr.dataset.securityId;
      if (!uid || !secId) return;
      if (_crossCompanyMode) {
        // Empresas afetadas = empresas das carteiras que têm esse uid pendente
        // (populado em _companyByWallet quando o modal abriu pelo cabeçalho).
        const wallets = (_walletsByUid[uid] || []);
        const companyIds = [...new Set(
          wallets.map(w => _companyByWallet[w.id]?.id).filter(Boolean)
        )];
        if (companyIds.length) checked.push({ from: uid, to: secId, companyIds });
      } else {
        checked.push({ from: uid, to: secId });
      }
    });

    if (!checked.length) { alert("Nenhum item selecionado."); return; }
    if (!_currentCompanyId && !_crossCompanyMode) { alert("Empresa não identificada."); return; }

    const totalCompanies = _crossCompanyMode
      ? new Set(checked.flatMap(m => m.companyIds)).size
      : 1;
    const preview = checked.slice(0, 5)
      .map(m => `  • ${m.from}\n      → ${m.to}`).join("\n");
    const more = checked.length > 5 ? `\n  … +${checked.length - 5} outros` : "";
    const scopeMsg = _crossCompanyMode
      ? `Isso aplica em ${totalCompanies} empresa(s) de uma vez (cada mapeamento vale para todas as empresas onde esse ativo apareceu).\n\n`
      : "";
    if (!confirm(
      `Aplicar ${checked.length} mapeamento(s) direto no sistema?\n\n` +
      `${scopeMsg}${preview}${more}\n\n` +
      `Esta ação chama PATCH na API Beehus e não é reversível por esta tela.`
    )) return;

    const btn = document.getElementById("apply-mapping-btn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg> Aplicando…`;

    let applied = false;
    try {
      const r = _crossCompanyMode
        ? await api("POST", "/api/controlpanel/apply-mapping-all", { mappings: checked })
        : await api("POST", "/api/controlpanel/apply-mapping", {
            companyId: _currentCompanyId,
            mappingsToInclude: checked,
          });
      const data = r.body || {};
      if (r.status === 401) {
        alert(
          "Token Beehus não está carregado ou foi rejeitado.\n" +
          "Clique no badge \"Token\" no topo desta página e cole o token do dia."
        );
        return;
      }
      if (!r.ok) {
        const detail = data.upstream_body ? `\n\nDetalhe: ${String(data.upstream_body).slice(0, 500)}` : "";
        alert(`Falha ao aplicar mapeamento: ${data.error || `HTTP ${r.status}`}${detail}`);
        return;
      }
      applied = true;
      if (_crossCompanyMode) {
        const results = data.results || {};
        const names = data.companyNames || {};
        const okCids  = Object.entries(results).filter(([, v]) => v.ok);
        const failCids = Object.entries(results).filter(([, v]) => !v.ok);
        btn.innerHTML = `&#10003; ${okCids.length}/${okCids.length + failCids.length} empresas mapeadas`;
        if (failCids.length) {
          alert(`Falhou em ${failCids.length} empresa(s):\n` +
            failCids.map(([cid, v]) => `  • ${names[cid] || cid}: ${v.error || "erro"}`).join("\n"));
        }
      } else {
        btn.innerHTML = `&#10003; ${data.applied || checked.length} mapeados`;
      }
      // Uncheck applied rows so they can't be re-applied accidentally.
      document.querySelectorAll("#modal-tbody input[type=checkbox]:checked").forEach(cb => { cb.checked = false; });
      _updateSelectionCount?.();  // zera o badge "selecionados" imediatamente
      // Restore the button (and its counter/disabled state) after the
      // confirmation message has been shown.
      setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; _updateSelectionCount(); }, 4000);
    } finally {
      // Token state may have changed (refreshed age, or cleared on 401).
      Token.refresh();
      // On any non-success path (401, HTTP error, thrown), restore the button
      // immediately so it doesn't stay stuck on "Aplicando…".
      if (!applied) { btn.innerHTML = orig; btn.disabled = false; _updateSelectionCount(); }
    }
  }

  function _fmtNum(v) {
    if (v == null) return `<span class="text-gray-400">—</span>`;
    return `<span class="font-mono text-[10px] text-gray-600">${Number(v).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 6})}</span>`;
  }

  // ── Registration modal (Cadastrar ativos) ────────────────────────────────
  let _regRows = [];      // editable working set (see _buildRegRow)
  let _regRowSrc = [];    // parallel: original mapping row for each _regRows[i] (for tooltips)
  let _regActiveTab = null;  // securityType da aba ativa (filtra os cards)

  // Floating tooltip for the unprocessedId cell
  function _regShowTip(e, i) {
    const src = _regRowSrc[i]; if (!src) return;
    const c = src.candidate || null;
    const wallets = (src.wallets || []).slice(0, 8)
      .map(w => `  • ${w.name || "(sem nome)"} — ${w.id || ""}`).join("\n");
    const moreWallets = (src.wallets || []).length > 8 ? `\n  … +${src.wallets.length - 8} outras` : "";
    const lines = [
      `Unprocessed ID:`,
      `  ${src.unprocessedSecurityId || ""}`,
      ``,
      `External ID: ${src.externalId || "—"}`,
      `Tipo previsto: ${src.type || "—"}  (conf: ${src.typeConfidence ? (src.typeConfidence*100).toFixed(0)+"%" : "—"})`,
      `Candidato: ${c ? `${c.beehusName}  [match ${c.score === "manual" ? "manual" : Math.min(100, c.score) + "%"}]` : "— (sem match)"}`,
      `  mainId: ${c?.mainId || "—"}`,
      `PU: ${src.pu ?? "—"}`,
      `Último preço: ${src.lastPrice ? `${src.lastPrice.value} (${src.lastPrice.date})` : "—"}`,
      `Carteiras (${src.walletCount || 0}):`,
      wallets || "  (nenhuma)",
    ].join("\n") + moreWallets;
    const tip = document.getElementById("reg-tooltip");
    tip.textContent = lines;
    tip.style.display = "block";
    _regMoveTip(e);
  }

  function _regMoveTip(e) {
    const tip = document.getElementById("reg-tooltip");
    if (tip.style.display === "none") return;
    // Offset from cursor, clamped inside viewport
    const pad = 12;
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    const rect = tip.getBoundingClientRect();
    if (x + rect.width  > window.innerWidth  - 8) x = Math.max(8, e.clientX - rect.width  - pad);
    if (y + rect.height > window.innerHeight - 8) y = Math.max(8, e.clientY - rect.height - pad);
    tip.style.left = `${x}px`;
    tip.style.top  = `${y}px`;
  }

  function _regHideTip() {
    document.getElementById("reg-tooltip").style.display = "none";
  }

  // ── Parser para unprocessedId de RF BR (CDB/LCA/LCI) ──────────────────────
  // Spec: docs/CADASTRO_ATIVOS.md §14 (tabela canônica de emissores) e §15
  // (gramática do formato bruto do custódio).

  const _PT_MONTHS = { jan:1, fev:2, mar:3, abr:4, mai:5, jun:6, jul:7, ago:8, set:9, out:10, nov:11, dez:12,
                       feb:2, apr:4, may:5, aug:8, sep:9, oct:10, dec:12 };
  const _PT_MONTH_ABBR = ["", "Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"];

  // Tabelas canônicas vindas de data/parser_securities_data.json (gerado de
  // securities_cache.json + curadoria). Ver docs/PARSER_SECURITIES.md §8/§9.
  // NÃO editar à mão — edite o JSON e rode `_gen_js_maps.py` para regenerar.
  // Ordem por contagem de uso DESC; patterns por especificidade DESC.

  const _BANK_CANONICALS = [
    { canonical: "Banco C6", patterns: ["BANCO C6 CONSIGNADO S.A.", "BANCO C6 CONSIGNADO", "BANCO C6 PRÉ", "BANCO C6 PÓS", "BANCO C6", "C6 BANK", "C6"] },
    { canonical: "Bradesco", patterns: ["BANCO BRADESCO S.A.", "BANCO BRADESCO", "BRADESCO"] },
    { canonical: "BTG Pactual", patterns: ["BANCO BTG PACTUAL S.A", "BANCO BTG PACTUAL SA", "BANCO BTG PACTUAL", "BTG PACTUAL"] },
    { canonical: "Itaú", patterns: ["ITAU", "ITAÚ"] },
    { canonical: "BMG", patterns: ["BANCO BMG S.A", "BANCO BMG", "BMG"] },
    { canonical: "Pine", patterns: ["BANCO PINE", "PINE"] },
    { canonical: "Caixa Econômica", patterns: ["CAIXA ECONOMICA", "CAIXA ECONÔMICA"] },
    { canonical: "Safra", patterns: ["BANCO SAFRA", "SAFRA"] },
    { canonical: "Original", patterns: ["BANCO ORIGINAL S/A", "BANCO ORIGINAL", "ORIGINAL"] },
    { canonical: "BNDES", patterns: ["BNDES"] },
    { canonical: "XP", patterns: ["BANCO XP S.A.", "BANCO XP", "XP"] },
    { canonical: "Pan", patterns: ["BANCO PAN S/A", "BANCO PAN", "PAN"] },
    { canonical: "Banco ABC", patterns: ["BANCO ABC", "ABC"] },
    { canonical: "Santander", patterns: ["BANCO SANTANDER", "SANTANDER"] },
    { canonical: "Itauvest", patterns: ["ITAUVEST", "ITAÚVEST"] },
    { canonical: "Bocom BBM", patterns: ["BANCO BOCOM BBM SA", "BANCO BOCOM BBM", "BOCOM BBM"] },
    { canonical: "Agibank", patterns: ["BANCO AGIBANK S.A", "BANCO AGIBANK", "AGIBANK"] },
    { canonical: "Digimais", patterns: ["BANCO DIGIMAIS", "DIGIMAIS"] },
    { canonical: "Rabobank", patterns: ["BANCO RABOBANK", "RABOBANK"] },
    { canonical: "Daycoval", patterns: ["BANCO DAYCOVAL", "DAYCOVAL"] },
    { canonical: "Will Financeira", patterns: ["WILL FINANCEIRA"] },
    { canonical: "Fibra", patterns: ["BANCO FIBRA SA", "BANCO FIBRA", "FIBRA"] },
    { canonical: "Automatico Safra", patterns: ["AUTOMÁTICO SAFRA", "AUTOMATICO SAFRA"] },
    { canonical: "Picpay", patterns: ["PICPAY"] },
    { canonical: "Itaú Unibanco S.A.", patterns: ["ITAÚ UNIBANCO S.A.", "ITAU UNIBANCO"] },
    { canonical: "Banco Master", patterns: ["BANCO MASTER S/A", "BANCO MASTER", "MASTER"] },
    { canonical: "CEF", patterns: ["CEF"] },
    { canonical: "Banco Votorantim", patterns: ["BANCO VOTORANTIM", "VOTORANTIM"] },
    { canonical: "BV", patterns: ["BANCO BV S/A", "BANCO BV", "BV S.A", "BV"] },
    { canonical: "BS2", patterns: ["BANCO BS2", "BS2"] },
    { canonical: "Pleno", patterns: ["BANCO PLENO SA", "PLENO"] },
    { canonical: "BRB", patterns: ["BRB"] },
    { canonical: "Neon Financeira", patterns: ["NEON FINANCEIRA"] },
    { canonical: "Facta", patterns: ["FACTA"] },
    { canonical: "Sicoob", patterns: ["SICOOB"] },
    { canonical: "NBC", patterns: ["NBC"] },
    { canonical: "Banco do Brasil", patterns: ["BANCO DO BRASIL"] },
    { canonical: "John Deere", patterns: ["BANCO JOHN DEERE S.A.", "BANCO JOHN DEERE", "JOHN DEERE"] },
    { canonical: "Stellantis", patterns: ["STELLANTIS"] },
    { canonical: "Banco ABC Brasil", patterns: ["BANCO ABC BRASIL"] },
    { canonical: "Andbank", patterns: ["BANCO ANDBANK", "ANDBANK"] },
    { canonical: "CNH", patterns: ["BANCO CNH", "CNH"] },
    { canonical: "Luso Brasileiro", patterns: ["BANCO LUSO BRASILEIRO S.A", "LUSO BRASILEIRO"] },
    { canonical: "Sicredi", patterns: ["BANCO SICREDI", "SICREDI"] },
    { canonical: "Banco BBM", patterns: ["BANCO BBM", "BBM"] },
    { canonical: "Caruana", patterns: ["CARUANA S/A", "CARUANA"] },
    { canonical: "Inter", patterns: ["BANCO INTER S/A", "BANCO INTER", "INTER"] },
    { canonical: "Afinz", patterns: ["BANCO AFINZ SA", "AFINZ"] },
    { canonical: "DM Financeira", patterns: ["DM FINANCEIRA"] },
    { canonical: "Midway", patterns: ["MIDWAY"] },
    { canonical: "Pernambucanas", patterns: ["PERNAMBUCANAS"] },
    { canonical: "BRDE", patterns: ["BANCO BRDE", "BRDE"] },
    { canonical: "Banco Bocom", patterns: ["BANCO BOCOM", "BOCOM"] },
    { canonical: "Banco Cooperativo Sicoob", patterns: ["BANCO COOPERATIVO SICOOB"] },
    { canonical: "Caixa Economica Federal", patterns: ["CAIXA ECONOMICA FEDERAL"] },
    { canonical: "Omni Financeira", patterns: ["OMNI FINANCEIRA"] },
    { canonical: "Rodobens", patterns: ["RODOBENS"] },
    { canonical: "Caixa", patterns: ["CAIXA"] },
    { canonical: "Paulista", patterns: ["BANCO PAULISTA", "PAULISTA"] },
    { canonical: "Senff", patterns: ["SENFF"] },
    { canonical: "BRP", patterns: ["BRP"] },
    { canonical: "Banco Agibank Pós", patterns: ["BANCO AGIBANK PÓS"] },
    { canonical: "Banco CNH Capital", patterns: ["BANCO CNH CAPITAL", "CNH CAPITAL"] },
    { canonical: "Banco CNH Industrial Capital", patterns: ["BANCO CNH INDUSTRIAL CAPITAL"] },
    { canonical: "Banco Votorantim Pré", patterns: ["BANCO VOTORANTIM PRÉ"] },
    { canonical: "CCB", patterns: ["CCB"] },
    { canonical: "Facta Financeira", patterns: ["FACTA FINANCEIRA"] },
    { canonical: "Haitong", patterns: ["HAITONG"] },
    { canonical: "Nubank", patterns: ["BANCO NUBANK", "NUBANK"] },
    { canonical: "Poupex", patterns: ["POUPEX"] },
    { canonical: "Socinal", patterns: ["SOCINAL"] },
    { canonical: "BDMG", patterns: ["BANCO BDMG", "BDMG"] },
    { canonical: "BR Partners", patterns: ["BR PARTNERS"] },
    { canonical: "BRB Banco de Brasilia", patterns: ["BRB BANCO DE BRASILIA SA", "BRB BANCO DE BRASILIA"] },
    { canonical: "Banco Afinz Pós", patterns: ["BANCO AFINZ PÓS"] },
    { canonical: "Banco Paraná", patterns: ["BANCO PARANA", "BANCO PARANÁ"] },
    { canonical: "Banco Rendimento", patterns: ["BANCO RENDIMENTO", "RENDIMENTO"] },
    { canonical: "DLL", patterns: ["BANCO DLL", "DLL"] },
    { canonical: "Emissão Safra", patterns: ["EMISSÃO SAFRA"] },
    { canonical: "Engelhart", patterns: ["ENGELHART"] },
    { canonical: "Mercantil", patterns: ["MERCANTIL"] },
    { canonical: "NU - Teste", patterns: ["NU - TESTE"] },
    { canonical: "Neon Pós", patterns: ["NEON PÓS"] },
    { canonical: "Omni", patterns: ["OMNI"] },
    { canonical: "Omni Pós", patterns: ["OMNI PÓS"] },
    { canonical: "Pré", patterns: ["PRÉ"] },
    { canonical: "RP Financeira", patterns: ["RP FINANCEIRA"] },
    { canonical: "Sicredi Invest", patterns: ["SICREDI INVEST"] },
    { canonical: "Will", patterns: ["WILL"] },
    { canonical: "XCMG Brasil", patterns: ["BANCO XCMG BRASIL S/A", "XCMG BRASIL"] },
    { canonical: "- Banco Bradesco", patterns: ["- BANCO BRADESCO"] },
    { canonical: "Agrolend", patterns: ["AGROLEND"] },
    { canonical: "Alfa Investimento", patterns: ["ALFA INVESTIMENTO"] },
    { canonical: "BTG", patterns: ["BTG"] },
    { canonical: "Banco ABC Brasil Pré", patterns: ["BANCO ABC BRASIL PRÉ"] },
    { canonical: "Banco Andbank (Brasil) S/A", patterns: ["BANCO ANDBANK (BRASIL) S/A", "BANCO ANDBANK (BRASIL)"] },
    { canonical: "Banco Andbank Pós", patterns: ["BANCO ANDBANK PÓS"] },
    { canonical: "Banco BMG Pré", patterns: ["BANCO BMG PRÉ"] },
    { canonical: "Banco BMG Pós", patterns: ["BANCO BMG PÓS"] },
    { canonical: "Banco Cooperativo Sicredi Pré", patterns: ["BANCO COOPERATIVO SICREDI PRÉ"] },
    { canonical: "Banco Digimais Pós", patterns: ["BANCO DIGIMAIS PÓS"] },
    { canonical: "Banco Mercantil Pós", patterns: ["BANCO MERCANTIL PÓS"] },
    { canonical: "Banco Original Pós", patterns: ["BANCO ORIGINAL PÓS"] },
    { canonical: "Banco Santander Pré", patterns: ["BANCO SANTANDER PRÉ"] },
    { canonical: "Bank Of China", patterns: ["BANK OF CHINA"] },
    { canonical: "Bocon BBM", patterns: ["BOCON BBM"] },
    { canonical: "Concorbc", patterns: ["CONCORBC"] },
    { canonical: "Caixa Economica Federal Pré", patterns: ["CAIXA ECONOMICA FEDERAL PRÉ"] },
    { canonical: "Caruana Pós", patterns: ["CARUANA PÓS"] },
    { canonical: "Crediare", patterns: ["CREDIARE"] },
    { canonical: "Dcls", patterns: ["DCLS"] },
    { canonical: "Genial", patterns: ["GENIAL"] },
    { canonical: "Icbc", patterns: ["ICBC"] },
    { canonical: "Intermedium", patterns: ["INTERMEDIUM"] },
    { canonical: "Lebes Financeira", patterns: ["LEBES FINANCEIRA"] },
    { canonical: "Novo Banco Continental", patterns: ["NOVO BANCO CONTINENTAL"] },
    { canonical: "Novo Banco Continetal Pós", patterns: ["NOVO BANCO CONTINETAL PÓS"] },
    { canonical: "Omni Banco S/A", patterns: ["OMNI BANCO S/A", "OMNI BANCO"] },
    { canonical: "Ouribank", patterns: ["OURIBANK"] },
    { canonical: "Pernambucanas Financiadora", patterns: ["PERNAMBUCANAS FINANCIADORA"] },
    { canonical: "Semear", patterns: ["SEMEAR"] },
    { canonical: "Sorocred", patterns: ["SOROCRED"] },
    { canonical: "BB", patterns: ["BANCO DO BRASIL"] },
    { canonical: "BNB", patterns: ["BANCO DO NORDESTE DO BRASIL S.A.", "BANCO DO NORDESTE", "BNB"] },
    { canonical: "Banestes", patterns: ["BANCO BANESTES S.A.", "BANCO BANESTES", "BANESTES"] },
    { canonical: "CNH Capital", patterns: ["BANCO CNH CAPITAL S/A", "BANCO CNH CAPITAL", "CNH CAPITAL"] },
    { canonical: "Industrial", patterns: ["BANCO INDUSTRIAL DO BRASIL S.A.", "BANCO INDUSTRIAL DO BRASIL"] },
    { canonical: "Rendimento", patterns: ["BANCO RENDIMENTO S.A.", "BANCO RENDIMENTO"] },
    { canonical: "Sofisa", patterns: ["BANCO SOFISA S.A.", "BANCO SOFISA", "SOFISA"] },
    { canonical: "Triângulo", patterns: ["BANCO TRIANGULO S/A", "BANCO TRIANGULO", "TRIANGULO"] },
  ];

  const _DEVEDOR_CANONICALS = [
    { canonical: "Rede D'Or", patterns: ["REDE D'OR", "REDE DOR"] },
    { canonical: "Minerva", patterns: ["MINERVA"] },
    { canonical: "Marfrig", patterns: ["MARFRIG"] },
    { canonical: "BTG", patterns: ["BTG"] },
    { canonical: "Klabin", patterns: ["KLABIN"] },
    { canonical: "Raizen", patterns: ["RAIZEN", "RAÍZEN"] },
    { canonical: "JBS", patterns: ["JBS"] },
    { canonical: "JSL", patterns: ["JSL"] },
    { canonical: "BRF", patterns: ["BRF"] },
    { canonical: "Direcional", patterns: ["DIRECIONAL"] },
    { canonical: "Ipiranga", patterns: ["IPIRANGA"] },
    { canonical: "Raia Drogasil", patterns: ["RAIA DROGASIL"] },
    { canonical: "Riza Securitizadora", patterns: ["RIZA SECURITIZADORA"] },
    { canonical: "Vamos", patterns: ["VAMOS"] },
    { canonical: "Camil", patterns: ["CAMIL"] },
    { canonical: "Dasa", patterns: ["DASA"] },
    { canonical: "Localiza", patterns: ["LOCALIZA"] },
    { canonical: "MRV", patterns: ["MRV"] },
    { canonical: "Brookfield", patterns: ["BROOKFIELD"] },
    { canonical: "Cyrela", patterns: ["CYRELA"] },
    { canonical: "Opea Securitizadora", patterns: ["OPEA SECURITIZADORA S/A", "OPEA SECURITIZADORA S.A", "OPEA SECURITIZADORA"] },
    { canonical: "Fs Bio", patterns: ["FS BIO"] },
    { canonical: "GPA", patterns: ["GPA"] },
    { canonical: "Movida", patterns: ["MOVIDA"] },
    { canonical: "Opea", patterns: ["OPEA"] },
    { canonical: "Petrobras", patterns: ["PETROBRAS"] },
    { canonical: "Iguatemi", patterns: ["IGUATEMI"] },
    { canonical: "Light", patterns: ["LIGHT"] },
    { canonical: "Seara", patterns: ["SEARA"] },
    { canonical: "Vibra Energia S.A.", patterns: ["VIBRA ENERGIA S.A"] },
    { canonical: "Assai", patterns: ["ASSAI", "ASSAÍ"] },
    { canonical: "Atacadão", patterns: ["ATACADÃO", "ATACADAO"] },
    { canonical: "Barretos", patterns: ["BARRETOS"] },
    { canonical: "Dexco", patterns: ["DEXCO"] },
    { canonical: "Multiplan", patterns: ["MULTIPLAN"] },
    { canonical: "Petrobras", patterns: ["PETRÓLEO BRASILEIRO S.A"] },
    { canonical: "SLC", patterns: ["SLC"] },
    { canonical: "Allos", patterns: ["ALLOS SA", "ALLOS"] },
    { canonical: "Automob", patterns: ["AUTOMOB"] },
    { canonical: "BTG Commodities", patterns: ["BTG COMMODITIES"] },
    { canonical: "BTG Pactual", patterns: ["BANCO BTG PACTUAL", "BTG PACTUAL"] },
    { canonical: "Copersucar", patterns: ["COPERSUCAR"] },
    { canonical: "Cury", patterns: ["CURY"] },
    { canonical: "Eco Securitizadora", patterns: ["ECO SECURITIZADORA"] },
    { canonical: "Ecoagro", patterns: ["ECOAGRO"] },
    { canonical: "Hapvida", patterns: ["HAPVIDA"] },
    { canonical: "Jhsf", patterns: ["JHSF"] },
    { canonical: "Oncoclinicas", patterns: ["ONCOCLINICAS", "ONCOCLÍNICAS"] },
    { canonical: "True Securitizadora", patterns: ["TRUE SECURITIZADORA"] },
    { canonical: "Unidas", patterns: ["UNIDAS"] },
    { canonical: "Albert Einstein", patterns: ["ALBERT EINSTEIN"] },
    { canonical: "B3", patterns: ["B3"] },
    { canonical: "BR Foods", patterns: ["BR FOODS"] },
    { canonical: "Bild", patterns: ["BILD"] },
    { canonical: "Bradesco", patterns: ["BRADESCO"] },
    { canonical: "Braskem", patterns: ["BRASKEM"] },
    { canonical: "BTG Log", patterns: ["BTG LOG"] },
    { canonical: "Caramuru", patterns: ["CARAMURU"] },
    { canonical: "Cashme", patterns: ["CASHME"] },
    { canonical: "Cerradinho", patterns: ["CERRADINHO"] },
    { canonical: "Cocal", patterns: ["COCAL"] },
    { canonical: "Cogna", patterns: ["COGNA"] },
    { canonical: "Cooxupé", patterns: ["COOXUPÉ", "COOXUPE"] },
    { canonical: "Eneva", patterns: ["ENEVA"] },
    { canonical: "Faz. Boa Vista", patterns: ["FAZ. BOA VISTA"] },
    { canonical: "Fibria", patterns: ["FIBRIA"] },
    { canonical: "Hypera", patterns: ["HYPERA"] },
    { canonical: "Jhsf Participações", patterns: ["JHSF PARTICIPAÇÕES"] },
    { canonical: "Mercado Livre", patterns: ["MERCADO LIVRE"] },
    { canonical: "Natura", patterns: ["NATURA"] },
    { canonical: "Olfar", patterns: ["OLFAR"] },
    { canonical: "Real Parque", patterns: ["REAL PARQUE"] },
    { canonical: "Rede D'or", patterns: ["REDE D'OR SÃO LUIZ S.A"] },
    { canonical: "Rumo", patterns: ["RUMO"] },
    { canonical: "Sabesp", patterns: ["SABESP"] },
    { canonical: "Sendas", patterns: ["SENDAS"] },
    { canonical: "Tramontina", patterns: ["TRAMONTINA"] },
    { canonical: "Vert", patterns: ["VERT"] },
    { canonical: "Virgo", patterns: ["VIRGO"] },
    { canonical: "Votorantim Cimentos", patterns: ["VOTORANTIM CIMENTOS"] },
    { canonical: "Aiz", patterns: ["AIZ"] },
    { canonical: "Adecoagro Vale do Ivinhema", patterns: ["ADECOAGRO VALE DO IVINHEMA"] },
    { canonical: "Agibank", patterns: ["AGIBANK"] },
    { canonical: "Aliansce Sonae", patterns: ["ALIANSCE SONAE"] },
    { canonical: "Almarias", patterns: ["ALMARIAS"] },
    { canonical: "Açucareira", patterns: ["AÇUCAREIRA"] },
    { canonical: "Bari Securitizadora", patterns: ["BARI SECURITIZADORA"] },
    { canonical: "Barigui", patterns: ["BARIGUI"] },
    { canonical: "Bewiki", patterns: ["BEWIKI"] },
    { canonical: "Boa Nova", patterns: ["BOA NOVA"] },
    { canonical: "BRagro", patterns: ["BRAGRO"] },
    { canonical: "BrasilAgro", patterns: ["BRASILAGRO"] },
    { canonical: "CF Alves Adm Imobiliária", patterns: ["CF ALVES ADM IMOBILIÁRIA"] },
    { canonical: "CPFL Transmissao", patterns: ["CPFL TRANSMISSAO"] },
    { canonical: "Cyrela Brazil Realty AS Empreend", patterns: ["CYRELA BRAZIL REALTY AS EMPREEND"] },
    { canonical: "Energisa", patterns: ["ENERGISA"] },
    { canonical: "Fs Florestal", patterns: ["FS FLORESTAL"] },
    { canonical: "GDM", patterns: ["GDM"] },
    { canonical: "GTFoods", patterns: ["GTFOODS"] },
    { canonical: "GVI", patterns: ["GVI"] },
    { canonical: "Gramado", patterns: ["GRAMADO"] },
    { canonical: "Grupo JB", patterns: ["GRUPO JB"] },
    { canonical: "Grupo Pão de Açucar", patterns: ["GRUPO PÃO DE AÇUCAR"] },
    { canonical: "Irani", patterns: ["IRANI"] },
    { canonical: "Itaú", patterns: ["ITAÚ"] },
    { canonical: "Kallas", patterns: ["KALLAS"] },
    { canonical: "MRV Flex", patterns: ["MRV FLEX"] },
    { canonical: "Mateus Supermercados", patterns: ["MATEUS SUPERMERCADOS"] },
    { canonical: "Nagro", patterns: ["NAGRO"] },
    { canonical: "Neomille", patterns: ["NEOMILLE"] },
    { canonical: "Nissei", patterns: ["NISSEI"] },
    { canonical: "Pacaembu", patterns: ["PACAEMBU"] },
    { canonical: "Parque dos Ingleses", patterns: ["PARQUE DOS INGLESES"] },
    { canonical: "Patense", patterns: ["PATENSE"] },
    { canonical: "Patrimar", patterns: ["PATRIMAR"] },
    { canonical: "Piracanjuba", patterns: ["PIRACANJUBA"] },
    { canonical: "Plano & Plano", patterns: ["PLANO & PLANO"] },
    { canonical: "Planta", patterns: ["PLANTA"] },
    { canonical: "Rumo Malha Paulista", patterns: ["RUMO MALHA PAULISTA"] },
    { canonical: "Saneatins", patterns: ["SANEATINS"] },
    { canonical: "Solfacil", patterns: ["SOLFACIL"] },
    { canonical: "Stahel", patterns: ["STAHEL"] },
    { canonical: "Taesa", patterns: ["TAESA"] },
    { canonical: "Tanac", patterns: ["TANAC"] },
    { canonical: "Tenda Atacado", patterns: ["TENDA ATACADO"] },
    { canonical: "Tereos", patterns: ["TEREOS"] },
    { canonical: "Tramontina", patterns: ["TRAMONTINA S.A CUTELARIA"] },
    { canonical: "Trinity II", patterns: ["TRINITY II"] },
    { canonical: "Trisul", patterns: ["TRISUL"] },
    { canonical: "Unidas (Ouro Verde)", patterns: ["UNIDAS (OURO VERDE)"] },
    { canonical: "Usiminas", patterns: ["USIMINAS"] },
    { canonical: "Vale", patterns: ["VALE"] },
    { canonical: "Vicunha", patterns: ["VICUNHA"] },
    { canonical: "Virgo Companhia de Securitização", patterns: ["VIRGO COMPANHIA DE SECURITIZAÇÃO"] },
    { canonical: "Yduqs", patterns: ["YDUQS"] },
    { canonical: "Zamp", patterns: ["ZAMP"] },
  ];

  // Lookup case-insensitive (substring) — primeira entrada que casar vence.
