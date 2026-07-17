/* Exceções — visão intradiária: filtros e verificação.
   Escopo global compartilhado; ordem importa. */
  let _it = {
    companyId: "",
    mode: "single",
    groupings: [],                     // [{id, name, walletIds: [...]}]
    groupingSelectedIds: new Set(),
    wallets: [],                       // [{id, name}]
    walletSelectedIds: new Set(),
    detectedGroups:    [],             // groups[] returned by /check
    selectedGroupKeys: new Set(),      // "wid|sid|date" — checkbox state
    walletNames:       {},
    securityNames:     {},
    patched:           [],             // patched[] returned by /build-patches
    selectedPatchKeys: new Set(),      // "wid|date" — checkbox state
  };

  function showIntradayView() {
    // Reset every piece of view state so reopening the screen feels fresh.
    _it = {
      companyId: "", mode: "single",
      groupings: [], groupingSelectedIds: new Set(),
      wallets:   [], walletSelectedIds:   new Set(),
      detectedGroups: [], selectedGroupKeys: new Set(),
      walletNames: {}, securityNames: {},
      patched: [], selectedPatchKeys: new Set(),
      transactions: [],   // securityContributionAdjustment plans (preview)
    };
    document.getElementById("exc-list-view").classList.add("hidden");
    document.getElementById("intraday-view").classList.remove("hidden");
    document.getElementById("it-company").value = "";
    document.getElementById("it-initialDate").value = todayISO();
    document.getElementById("it-finalDate").value   = todayISO();
    document.getElementById("it-finalDate-wrap").classList.add("hidden");
    document.getElementById("it-mode-single").classList.add("active");
    document.getElementById("it-mode-range").classList.remove("active");
    document.getElementById("it-advanced").classList.add("hidden");
    document.getElementById("it-toggle-advanced").textContent = "+ Mostrar";
    document.getElementById("it-warnings").innerHTML = "";
    document.getElementById("it-status").textContent = "";
    document.getElementById("it-groups-section").classList.add("hidden");
    document.getElementById("it-patched-section").classList.add("hidden");
    document.getElementById("it-results").classList.add("hidden");
    document.getElementById("it-build-patches-btn").disabled = true;
    document.getElementById("it-apply-btn").disabled         = true;
    document.getElementById("it-build-count").textContent = "0";
    document.getElementById("it-apply-count").textContent = "0";
    _renderIntradayPanes();
    _renderIntradayWalletPanes();
    window.scrollTo(0, 0);
  }

  function hideIntradayView() {
    document.getElementById("intraday-view").classList.add("hidden");
    document.getElementById("exc-list-view").classList.remove("hidden");
  }

  function onIntradayModeChange(mode) {
    if (mode !== "range") mode = "single";
    _it.mode = mode;
    document.getElementById("it-finalDate-wrap").classList.toggle("hidden", mode !== "range");
    document.getElementById("it-mode-single").classList.toggle("active", mode === "single");
    document.getElementById("it-mode-range").classList.toggle("active", mode === "range");
    document.getElementById("it-initialDate-label").textContent =
      (mode === "range" ? "Data inicial" : "Data");
  }

  function toggleIntradayAdvanced() {
    const adv = document.getElementById("it-advanced");
    const btn = document.getElementById("it-toggle-advanced");
    const willShow = adv.classList.contains("hidden");
    adv.classList.toggle("hidden", !willShow);
    btn.textContent = willShow ? "− Ocultar" : "+ Mostrar";
  }

  async function onIntradayCompanyChange() {
    _it.companyId = document.getElementById("it-company").value;
    _it.groupings = [];
    _it.groupingSelectedIds = new Set();
    _it.wallets = [];
    _it.walletSelectedIds = new Set();
    _renderIntradayPanes();
    _renderIntradayWalletPanes();
    if (!_it.companyId) return;
    // The two filter endpoints already enforce `company_visible`, so it's
    // safe to call them in parallel without a server-side join here.
    try {
      const [gResp, wResp] = await Promise.all([
        fetch(`/api/beehus/filters/groupings?companyId=${encodeURIComponent(_it.companyId)}`).then(r => r.json()),
        fetch(`/api/beehus/filters/wallets?companyId=${encodeURIComponent(_it.companyId)}`).then(r => r.json()),
      ]);
      _it.groupings = Array.isArray(gResp) ? gResp : [];
      _it.wallets   = Array.isArray(wResp) ? wResp : [];
    } catch (e) {
      _it.groupings = [];
      _it.wallets   = [];
    }
    _renderIntradayPanes();
    _renderIntradayWalletPanes();
  }

  // Wallets visible in the "Disponíveis" pane = company wallets ∩ union of
  // selected groupings' walletIds (when at least one grouping is picked).
  function _intradayWalletFilter() {
    if (!_it.groupingSelectedIds.size) return null;
    const ids = new Set();
    for (const g of _it.groupings) {
      if (_it.groupingSelectedIds.has(g.id)) {
        (g.walletIds || []).forEach(w => ids.add(w));
      }
    }
    return ids;
  }

  function _renderIntradayPanes() {
    const avail = document.getElementById("it-grp-available");
    const sel   = document.getElementById("it-grp-selected");
    const available = _it.groupings.filter(g => !_it.groupingSelectedIds.has(g.id));
    const selected  = _it.groupings.filter(g =>  _it.groupingSelectedIds.has(g.id));
    avail.innerHTML = available.map(g => `<option value="${escHtml(g.id)}">${escHtml(g.name)}</option>`).join("");
    sel.innerHTML   = selected .map(g => `<option value="${escHtml(g.id)}">${escHtml(g.name)}</option>`).join("");
    document.getElementById("it-grp-available-count").textContent = available.length;
    document.getElementById("it-grp-selected-count").textContent  = selected.length;
    // A grouping selection narrows the wallet picker — re-render that pane too.
    _renderIntradayWalletPanes();
  }

  function _renderIntradayWalletPanes() {
    const filter = _intradayWalletFilter();
    const avail  = document.getElementById("it-wal-available");
    const sel    = document.getElementById("it-wal-selected");
    const available = _it.wallets.filter(w =>
      (!filter || filter.has(w.id)) && !_it.walletSelectedIds.has(w.id)
    );
    const selected  = _it.wallets.filter(w => _it.walletSelectedIds.has(w.id));
    avail.innerHTML = available.map(w => `<option value="${escHtml(w.id)}">${escHtml(w.name)}</option>`).join("");
    sel.innerHTML   = selected .map(w => `<option value="${escHtml(w.id)}">${escHtml(w.name)}</option>`).join("");
    document.getElementById("it-wal-available-count").textContent = available.length;
    document.getElementById("it-wal-selected-count").textContent  = selected.length;
  }

  function _itHighlighted(id) {
    return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
  }

  function addIntradayGroupingSelected()    { _itHighlighted("it-grp-available").forEach(id => _it.groupingSelectedIds.add(id)); _renderIntradayPanes(); }
  function addIntradayGroupingAll()         { Array.from(document.getElementById("it-grp-available").options).forEach(o => o.value && _it.groupingSelectedIds.add(o.value)); _renderIntradayPanes(); }
  function removeIntradayGroupingSelected() { _itHighlighted("it-grp-selected").forEach(id => _it.groupingSelectedIds.delete(id)); _renderIntradayPanes(); }
  function removeIntradayGroupingAll()      { _it.groupingSelectedIds.clear(); _renderIntradayPanes(); }

  function addIntradayWalletSelected()    { _itHighlighted("it-wal-available").forEach(id => _it.walletSelectedIds.add(id)); _renderIntradayWalletPanes(); }
  function addIntradayWalletAll()         { Array.from(document.getElementById("it-wal-available").options).forEach(o => o.value && _it.walletSelectedIds.add(o.value)); _renderIntradayWalletPanes(); }
  function removeIntradayWalletSelected() { _itHighlighted("it-wal-selected").forEach(id => _it.walletSelectedIds.delete(id)); _renderIntradayWalletPanes(); }
  function removeIntradayWalletAll()      { _it.walletSelectedIds.clear(); _renderIntradayWalletPanes(); }

  function _intradayBuildPayload() {
    const cid = _it.companyId;
    const ini = document.getElementById("it-initialDate").value;
    const fin = _it.mode === "range"
      ? document.getElementById("it-finalDate").value
      : ini;
    return {
      companyId:    cid,
      initialDate:  ini,
      finalDate:    fin,
      groupingIds:  Array.from(_it.groupingSelectedIds),
      walletIds:    Array.from(_it.walletSelectedIds),
    };
  }

  function _intradayValidate(payload) {
    if (!payload.companyId)   { alert("Selecione uma empresa."); return false; }
    if (!payload.initialDate) { alert("Informe a data."); return false; }
    if (_it.mode === "range" && !payload.finalDate) { alert("Informe a data final."); return false; }
    if (payload.initialDate > payload.finalDate) { alert("Data inicial > data final."); return false; }
    return true;
  }

  // ── Step 1: detect ────────────────────────────────────────────────────
  // Hits /intraday/check and renders the day-trade groups table with one
  // checkbox per row. Operator picks which rows survive into step 2.
  async function runIntradayCheck() {
    const payload = _intradayBuildPayload();
    if (!_intradayValidate(payload)) return;

    document.getElementById("it-status").textContent = "Calculando...";
    document.getElementById("it-warnings").innerHTML = "";
    document.getElementById("it-groups-section").classList.add("hidden");
    document.getElementById("it-patched-section").classList.add("hidden");
    document.getElementById("it-results").classList.add("hidden");
    _it.detectedGroups    = [];
    _it.selectedGroupKeys = new Set();
    _it.patched           = [];
    _it.selectedPatchKeys = new Set();
    _it.transactions      = [];
    document.getElementById("it-build-patches-btn").disabled = true;
    document.getElementById("it-apply-btn").disabled         = true;
    document.getElementById("it-build-count").textContent = "0";
    document.getElementById("it-apply-count").textContent = "0";
    document.getElementById("it-txn-preview").classList.add("hidden");

    let resp;
    try {
      const r = await fetch("/api/excecoes/intraday/check", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      resp = await r.json();
      if (!r.ok || resp.error) {
        document.getElementById("it-status").textContent = "";
        document.getElementById("it-warnings").innerHTML =
          `<p class="pill pill-error">${escHtml(resp.error || "Falha")}</p>`;
        return;
      }
    } catch (e) {
      document.getElementById("it-status").textContent = "";
      document.getElementById("it-warnings").innerHTML =
        '<p class="pill pill-error">Erro de rede.</p>';
      return;
    }

    _it.detectedGroups = resp.groups || [];
    _it.walletNames    = resp.walletNames   || {};
    _it.securityNames  = resp.securityNames || {};
    // Default to all groups checked — the operator typically wants
    // everything that was detected; deselecting is faster than picking N.
    _it.selectedGroupKeys = new Set(
      _it.detectedGroups.map(g => `${g.walletId}|${g.securityId}|${g.date}`),
    );

    document.getElementById("it-status").textContent =
      `${resp.candidateWalletCount || 0} carteira(s) inspecionada(s).`;
    _renderIntradayGroups();

    if (!_it.detectedGroups.length) {
      document.getElementById("it-warnings").innerHTML =
        '<p class="pill pill-muted">Nenhum day-trade detectado para os filtros informados.</p>';
    }
  }

  function _renderIntradayGroups() {
    const sec    = document.getElementById("it-groups-section");
    const tbody  = document.getElementById("it-groups-tbody");
    const count  = document.getElementById("it-groups-count");
    const master = document.getElementById("it-groups-master");
    const groups = _it.detectedGroups;
    const wn = _it.walletNames, sn = _it.securityNames;

    if (!groups.length) {
      sec.classList.add("hidden");
      tbody.innerHTML = "";
      return;
    }
    sec.classList.remove("hidden");
    count.textContent = `(${groups.length})`;
    tbody.innerHTML = groups.map(g => {
      const key = `${g.walletId}|${g.securityId}|${g.date}`;
      const wname = wn[g.walletId]   || g.walletId;
      const sname = sn[g.securityId] || g.securityId;
      const checked = _it.selectedGroupKeys.has(key) ? "checked" : "";
      return `<tr class="border-t border-gray-100">
        <td class="px-2 py-1.5 text-center">
          <input type="checkbox" data-it-group-key="${escHtml(key)}" ${checked} onchange="onIntradayGroupToggle(this)" />
        </td>
        <td class="px-2 py-1.5">${escHtml(g.date)}</td>
        <td class="px-2 py-1.5">${escHtml(wname)}</td>
        <td class="px-2 py-1.5">
          <div class="font-medium text-gray-800">${escHtml(sname)}</div>
          <div class="text-[10px] text-gray-400 font-mono">${escHtml(g.securityId)}</div>
        </td>
        <td class="px-2 py-1.5 text-right">${g.transactionCount}</td>
        <td class="px-2 py-1.5 text-right">${fmtMoney(g.contribution)}</td>
        <td class="px-2 py-1.5">${escHtml(g.currencyId || "")}</td>
      </tr>`;
    }).join("");

    master.checked = groups.length > 0
      && groups.every(g => _it.selectedGroupKeys.has(`${g.walletId}|${g.securityId}|${g.date}`));
    _refreshIntradayBuildButton();
  }

  function onIntradayGroupToggle(cb) {
    const key = cb.dataset.itGroupKey;
    if (cb.checked) _it.selectedGroupKeys.add(key);
    else            _it.selectedGroupKeys.delete(key);
    document.getElementById("it-groups-master").checked = _it.detectedGroups.length > 0
      && _it.detectedGroups.every(g => _it.selectedGroupKeys.has(`${g.walletId}|${g.securityId}|${g.date}`));
    _refreshIntradayBuildButton();
  }

  function toggleIntradayGroupsAll(checked) {
    _it.selectedGroupKeys = new Set(
      checked
        ? _it.detectedGroups.map(g => `${g.walletId}|${g.securityId}|${g.date}`)
        : [],
    );
    _renderIntradayGroups();
  }

  function _refreshIntradayBuildButton() {
    const n = _it.selectedGroupKeys.size;
    document.getElementById("it-build-count").textContent = String(n);
    document.getElementById("it-build-patches-btn").disabled = (n === 0);
  }

  // ── Step 2: build patches for selected groups ───────────────────────
