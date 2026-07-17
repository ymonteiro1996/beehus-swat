/* Painel de Controle — picker de work-items e busca de cadastro.
   Escopo global; usa consts do bootstrap; ordem importa. */
  function closeWalletIssues() {
    document.getElementById("wallet-issues-modal").style.display = "none";
    _wiCompanyId = null; _wiWallets = null;
  }

  // Toggle back to the wallet picker (from the issues view).
  function _wiShowPicker() {
    document.getElementById("wi-picker").style.display = "flex";
    document.getElementById("wi-issues").style.display = "none";
    document.getElementById("wi-back").style.display   = "none";
    document.getElementById("wi-subtitle").textContent = `${_wiCompanyName} • ${_currentDate}`;
  }

  function _wiRenderPicker() {
    const list = document.getElementById("wi-picker-list");
    const msg  = document.getElementById("wi-picker-msg");
    if (!_wiWallets) return;
    const q = (document.getElementById("wi-filter").value || "").trim().toLowerCase();
    const rows = _wiWallets.filter(w => !q || (w.walletName || "").toLowerCase().includes(q));

    if (!_wiWallets.length) {
      msg.style.display = ""; msg.textContent = "Nenhuma carteira nesta empresa.";
      list.style.display = "none"; return;
    }
    msg.style.display = "none";
    list.style.display = "";

    const withIssues = _wiWallets.filter(w => w.issueCount > 0).length;
    const header = `<p class="text-[11px] text-gray-500 mb-2">
        <span class="font-semibold text-gray-700">${withIssues}</span> de
        <span class="font-semibold text-gray-700">${_wiWallets.length}</span>
        carteira(s) com issues pendentes em ${_escHtml(_currentDate)}
      </p>`;

    if (!rows.length) {
      list.innerHTML = header + `<p class="text-center text-gray-400 py-4">Nenhuma carteira corresponde ao filtro.</p>`;
      return;
    }

    list.innerHTML = header + `<div class="flex flex-col gap-1">` + rows.map(w => {
      const badge = w.issueCount > 0
        ? `<span class="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-700 flex-shrink-0">${w.issueCount} issue${w.issueCount > 1 ? "s" : ""}</span>`
        : `<span class="inline-block px-2 py-0.5 rounded text-[10px] bg-green-100 text-green-700 flex-shrink-0">sem issues</span>`;
      return `<button type="button"
                data-wallet-id="${_escHtml(w.walletId)}"
                data-wallet-name="${_escHtml(w.walletName || w.walletId)}"
                class="js-wi-pick w-full flex items-center justify-between gap-3 px-3 py-2 rounded border border-gray-100 hover:bg-gray-50 text-left">
                <span class="text-gray-700 text-xs truncate" title="${_escHtml(w.walletId)}">${_escHtml(w.walletName || w.walletId)}</span>
                ${badge}
              </button>`;
    }).join("") + `</div>`;
  }

  // Delegated pick handler (avoids inline onclick with wallet names/ids).
  document.getElementById("wi-picker-list").addEventListener("click", e => {
    const btn = e.target.closest("button.js-wi-pick");
    if (!btn) return;
    loadWalletIssues(btn.dataset.walletId, btn.dataset.walletName);
  });

  async function loadWalletIssues(walletId, walletName) {
    if (!_wiCompanyId || !_currentDate || !walletId) return;
    document.getElementById("wi-picker").style.display = "none";
    document.getElementById("wi-issues").style.display = "";
    document.getElementById("wi-back").style.display   = "";
    document.getElementById("wi-subtitle").textContent = `${walletName || walletId} • ${_currentDate}`;

    const msg     = document.getElementById("wi-issues-msg");
    const content = document.getElementById("wi-issues-content");
    msg.style.display     = "";
    msg.textContent       = "Carregando...";
    content.style.display = "none";
    content.innerHTML     = "";

    try {
      const params = new URLSearchParams({companyId: _wiCompanyId, date: _currentDate, walletId});
      const r = await fetch(`/api/controlpanel/wallet-issues?${params}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        msg.textContent = `Erro: ${err.error || r.status}`;
        return;
      }
      _wiRenderIssues(await r.json());
    } catch (e) {
      msg.textContent = "Erro de rede: " + e;
    }
  }

  function _wiRenderIssues(data) {
    const msg     = document.getElementById("wi-issues-msg");
    const content = document.getElementById("wi-issues-content");
    const groups  = data.types || [];

    if (!data.total) {
      msg.style.display = "";
      msg.textContent   = "Nenhum issue pendente para esta carteira na data selecionada.";
      content.style.display = "none";
      return;
    }
    msg.style.display     = "none";
    content.style.display = "";

    const secCell = (i) => {
      const name = i.beehusName || "";
      const mid  = i.mainId || "";
      if (!name && !mid && !i.securityId) return '<span class="text-gray-300">—</span>';
      return `${_escHtml(name || "— sem cadastro")}${mid ? ` <span class="text-gray-400">· ${_escHtml(mid)}</span>` : ""}`;
    };

    content.innerHTML = `
      <p class="text-[11px] text-gray-500 mb-3">
        <span class="font-semibold text-gray-700">${data.total}</span>
        issue(s) pendente(s) em ${groups.length} tipo(s)
      </p>` + groups.map(g => `
      <div class="mb-4">
        <div class="flex items-center gap-2 mb-1">
          <span class="font-semibold text-gray-700 text-xs">${_escHtml(g.label)}</span>
          <span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600">${g.issues.length}</span>
        </div>
        <table class="w-full text-xs border-collapse">
          <thead class="bg-gray-50 text-gray-400 uppercase text-[9px]">
            <tr>
              <th class="px-2 py-1 text-left font-medium">External ID</th>
              <th class="px-2 py-1 text-left font-medium">Security</th>
              <th class="px-2 py-1 text-left font-medium">Unprocessed Security</th>
              <th class="px-2 py-1 text-left font-medium">Criado em</th>
            </tr>
          </thead>
          <tbody>
            ${g.issues.map(i => `
              <tr class="border-b border-gray-50">
                <td class="px-2 py-1 text-gray-600">${_escHtml(i.externalId || "—")}</td>
                <td class="px-2 py-1 text-gray-700">${secCell(i)}</td>
                <td class="px-2 py-1 text-gray-500 break-all">${_escHtml(i.unprocessedSecurityId || "—")}</td>
                <td class="px-2 py-1 text-gray-400 whitespace-nowrap">${_escHtml(i.createdAt || "—")}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`).join("");
  }

  // ── Cell-detail modal (Posições Processadas / NAV Wallet / NAV Grouping
  //                       / Published) ───────────────────────────────────────
  // openCellDetail fetches /api/controlpanel/cell-detail and stashes the
  // response in `_cellDetailData`; renderCellDetail() re-runs every time a
  // filter input changes, narrowing the in-memory payload locally so the
  // operator gets instant feedback without a round-trip.
  async function openCellDetail(companyId, companyName, column) {
    if (!_currentDate || !companyId || !column) return;
    if (!CELL_DETAIL_COLUMNS.has(column)) return;

    const modal      = document.getElementById('cell-detail-modal');
    const isWallet   = CELL_DETAIL_LEVEL[column] === 'wallet';
    const colLabel   = CELL_DETAIL_LABELS[column] || column;
    const walletFlt  = document.getElementById('cd-filter-wallet');

    document.getElementById('cd-title').textContent    = `${colLabel} — ${companyName}`;
    document.getElementById('cd-subtitle').textContent = `${companyName} • ${_currentDate}`;
    document.getElementById('cd-filter-grouping').value = '';
    walletFlt.value = '';
    // Wallet-level columns expose both filters; grouping-level columns
    // hide the wallet filter (no wallet axis to narrow).
    walletFlt.style.display = isWallet ? '' : 'none';

    const msg     = document.getElementById('cd-msg');
    const content = document.getElementById('cd-content');
    msg.style.display    = '';
    msg.textContent      = 'Carregando...';
    content.style.display = 'none';
    content.innerHTML    = '';
    modal.style.display  = 'flex';
    _cellDetailData      = null;

    try {
      const params = new URLSearchParams({
        companyId, date: _currentDate, column,
      });
      const r = await fetch(`/api/controlpanel/cell-detail?${params}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        msg.textContent = `Erro: ${err.error || r.status}`;
        return;
      }
      _cellDetailData = await r.json();
      renderCellDetail();
    } catch (e) {
      msg.textContent = 'Erro de rede: ' + e;
    }
  }

  function closeCellDetail() {
    document.getElementById('cell-detail-modal').style.display = 'none';
    _cellDetailData = null;
  }

  function _clearCdFilters() {
    document.getElementById('cd-filter-grouping').value = '';
    document.getElementById('cd-filter-wallet').value   = '';
    renderCellDetail();
  }

  function _cdStatusBadge(done, column) {
    // Per-column labels: the wallet-level columns report a per-wallet OK /
    // Pendente; for groupings we use semantically richer "Calculado /
    // Publicado" wording so the operator immediately knows what's missing.
    const labels = {
      processed:    ['Processada',    'Pendente'],
      nav_wallet:   ['NAV calculado', 'Pendente'],
      nav_grouping: ['Calculado',     'Não calculado'],
      published:    ['Publicado',     'Não publicado'],
    };
    const [ok, ng] = labels[column] || ['OK', 'Pendente'];
    return done
      ? `<span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-700">✓ ${_escHtml(ok)}</span>`
      : `<span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-50 text-amber-700">${_escHtml(ng)}</span>`;
  }

  // Side flag shown only in the "Posições Processadas" drill-down: does
  // a `unprocessedSecurityPositions` doc exist for (walletId, date)?
  // Distinct colouring from `_cdStatusBadge` (sky vs green) so the two
  // columns don't read as redundant when the wallet is fully processed.
  // Mesma família de pílula (rounded-full + font-medium) do `_cdStatusBadge`.
  function _cdUnprocessedBadge(has) {
    return has
      ? '<span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-sky-100 text-sky-700">✓ Existe</span>'
      : '<span class="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 text-gray-500">Não existe</span>';
  }

  // Per-wallet pending issue chips for the "Posições Processadas" view —
  // surfaces *why* a wallet with raw positions (unprocessed) hasn't been
  // processed yet. Backend pre-filters to blocking types only (see
  // `_PROCESSING_BLOCKING_ISSUE_TYPES`). Clicking a chip closes the
  // cell-detail modal and opens the existing per-type issue list so the
  // operator can drill into the specific occurrences.
  function _cdIssuesChips(issues) {
    if (!Array.isArray(issues) || !issues.length) return '';
    return issues.map(it => {
      const lbl = it.count > 1
        ? `${_escHtml(it.label)} ×${it.count}`
        : _escHtml(it.label);
      const t = String(it.type || '').replace(/'/g, "\\'");
      return `<button type="button"
                onclick="_cdOpenIssueFromCell('${t}')"
                class="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 text-amber-700 hover:bg-amber-200 cursor-pointer transition-colors"
                title="Abrir lista de '${_escHtml(it.label)}' para esta empresa+data">${lbl}</button>`;
    }).join(' ');
  }

  // Closes the cell-detail modal and pivots to the canonical per-type
  // issue list (the same modal triggered by clicking an issue-type
  // column on the main table). Uses the company/date from the active
  // cell-detail payload so we don't depend on _currentCompanyId state.
  function _cdOpenIssueFromCell(type) {
    if (!_cellDetailData || !type) return;
    const cid   = _cellDetailData.companyId;
    const cname = _cellDetailData.companyName;
    closeCellDetail();
    openModal(cid, cname, type);
  }

  // Publica o agrupamento direto do drill-down de "Posições Processadas" —
  // atalho pra quando o operador já vê ali que todas as carteiras do
  // agrupamento foram processadas (não espera o NAV Grouping/Published já
  // ter sido calculado; se ainda não calculou, o upstream retorna erro claro
  // em vez de publicar algo incompleto).
  async function _cdPublishGrouping(groupingId, groupingName, btn) {
    const data = _cellDetailData;
    if (!data) return;
    if (!confirm(
      `Publicar o agrupamento "${groupingName}" (${data.companyName} · ${data.date})?\n\n` +
      `Esta ação chama PATCH na API Beehus e não é reversível por esta tela.`
    )) return;

    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "Publicando…";
    try {
      const r = await api("POST", "/api/beehus/nav/publish", {
        companyId: data.companyId, positionDate: data.date, groupingIds: [groupingId],
      });
      if (r.status === 401) {
        alert(
          "Token Beehus não está carregado ou foi rejeitado.\n" +
          "Clique no badge \"Token\" no topo desta página e cole o token do dia."
        );
        btn.disabled = false;
        btn.innerHTML = orig;
        return;
      }
      if (!r.ok) {
        const body = r.body || {};
        alert(`Falha ao publicar: ${body.error || `HTTP ${r.status}`}`);
        btn.disabled = false;
        btn.innerHTML = orig;
        return;
      }
      btn.textContent = "Publicado ✓";
      btn.classList.remove("ec-btn-primary");
      btn.classList.add("bg-green-100", "text-green-700");
    } catch (e) {
      alert("Erro de rede: " + e);
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }

  function renderCellDetail() {
    const data = _cellDetailData;
    if (!data) return;
    const grpQ = (document.getElementById('cd-filter-grouping').value || '').trim().toLowerCase();
    const wltQ = (document.getElementById('cd-filter-wallet').value   || '').trim().toLowerCase();
    const col  = data.column;
    const colLabel = CELL_DETAIL_LABELS[col] || col;
    const content  = document.getElementById('cd-content');
    const msg      = document.getElementById('cd-msg');

    if (data.level === 'wallet') {
      const groupingMatches = (g) =>
        !grpQ || (g.groupingName || '').toLowerCase().includes(grpQ);

      const filteredGroupings = (data.groupings || [])
        .filter(groupingMatches)
        .map(g => ({
          ...g,
          wallets: (g.wallets || []).filter(w =>
            !wltQ || (w.walletName || '').toLowerCase().includes(wltQ),
          ),
        }))
        // After wallet narrowing the grouping may have zero matches — hide it
        // only when the user is actually filtering wallets (otherwise an
        // empty grouping is still useful context).
        .filter(g => !wltQ || g.wallets.length > 0);

      // Wallets that don't belong to any grouping. We surface them in their
      // own "Sem grouping" bucket so the operator can still inspect them,
      // but only when the grouping filter is empty (they have no grouping
      // name to match against).
      const orphans = (!grpQ ? (data.orphanWallets || []) : [])
        .filter(w => !wltQ || (w.walletName || '').toLowerCase().includes(wltQ));

      // The "Posições Processadas" drill-down carries two extra columns:
      // (1) whether an `unprocessedSecurityPositions` doc exists for the
      // wallet on the date, and (2) the pending blocking-issues for the
      // wallet — but only when the wallet has unprocessed (without raw
      // positions the blocker is upstream, not an actionable issue).
      // We render a small thead once per grouping section so the column
      // meanings stay obvious without a global header that wouldn't
      // line up across the per-grouping tables.
      const showUnprocessed = (col === 'processed');
      const colCount = showUnprocessed ? 4 : 2;
      const theadHtml = showUnprocessed ? `
        <thead class="bg-white text-gray-400 uppercase text-[9px]">
          <tr>
            <th class="px-3 py-1 text-left font-medium">Wallet</th>
            <th class="px-3 py-1 text-right font-medium">Posição Processada</th>
            <th class="px-3 py-1 text-right font-medium">Unprocessed</th>
            <th class="px-3 py-1 text-left font-medium">Issues bloqueantes</th>
          </tr>
        </thead>` : '';

      const totalUnprocessedHtml = (showUnprocessed && typeof data.totalUnprocessed === 'number') ? `
        <div class="text-[10px] text-gray-500">
          <span class="font-semibold text-gray-700">${data.totalUnprocessed}/${data.totalWallets}</span>
          com Unprocessed
        </div>` : '';

      const headerHtml = `
        <div class="mb-3 flex items-center justify-between gap-3 text-xs text-gray-500">
          <div>
            <span class="font-semibold text-gray-700">${data.doneWallets}/${data.totalWallets}</span>
            wallet(s) com ${_escHtml(colLabel)}
          </div>
          ${totalUnprocessedHtml}
          <div class="text-[10px] text-gray-400">${data.date}</div>
        </div>`;

      const _walletRow = (w) => {
        if (!showUnprocessed) {
          return `
            <tr class="border-b border-gray-50">
              <td class="px-3 py-1.5 text-gray-700 truncate" title="${_escHtml(w.walletId)}">${_escHtml(w.walletName)}</td>
              <td class="px-3 py-1.5 text-right whitespace-nowrap">${_cdStatusBadge(w.done, col)}</td>
            </tr>`;
        }
        // Issues column only renders chips when the wallet actually has
        // unprocessed (per user spec: "quando existe unprocessed"). When
        // no unprocessed, the blocker is upstream and the chips would be
        // noise; we show an em dash placeholder to keep the column
        // visually balanced.
        const issuesCell = (w.hasUnprocessed && Array.isArray(w.issues) && w.issues.length)
          ? `<div class="flex flex-wrap gap-1 justify-start">${_cdIssuesChips(w.issues)}</div>`
          : '<span class="text-gray-400">—</span>';
        return `
          <tr class="border-b border-gray-50">
            <td class="px-3 py-1.5 text-gray-700 truncate" title="${_escHtml(w.walletId)}">${_escHtml(w.walletName)}</td>
            <td class="px-3 py-1.5 text-right whitespace-nowrap">${_cdStatusBadge(w.done, col)}</td>
            <td class="px-3 py-1.5 text-right whitespace-nowrap">${_cdUnprocessedBadge(!!w.hasUnprocessed)}</td>
            <td class="px-3 py-1.5 text-left">${issuesCell}</td>
          </tr>`;
      };

      const groupingsHtml = filteredGroupings.map(g => {
        const rows = g.wallets.map(_walletRow).join('');
        const empty = g.wallets.length === 0
          ? `<tr><td colspan="${colCount}" class="px-3 py-1.5 text-center text-[10px] text-gray-400 italic">— sem wallets correspondentes ao filtro —</td></tr>`
          : '';
        const canPublish = g.doneCount > 0 && g.doneCount === g.totalCount;
        return `
          <div class="mb-4">
            <div class="flex items-center justify-between gap-2 px-3 py-1.5 bg-gray-100 rounded-t">
              <span class="text-xs font-semibold text-gray-700 truncate" title="${_escHtml(g.groupingId)}">${_escHtml(g.groupingName)}</span>
              <span class="flex items-center gap-2 flex-shrink-0">
                ${canPublish ? `
                <button type="button" onclick="_cdPublishGrouping('${_escJs(g.groupingId)}','${_escJs(g.groupingName)}',this)"
                        class="ec-btn ec-btn-primary text-[10px] px-2 py-0.5">Publicar</button>` : ``}
                <span class="text-[10px] text-gray-500 whitespace-nowrap">${g.doneCount}/${g.totalCount}</span>
              </span>
            </div>
            <table class="w-full text-xs border-collapse border border-gray-100 rounded-b">
              ${theadHtml}
              <tbody>${rows || empty}</tbody>
            </table>
          </div>`;
      }).join('');

      const orphansDone = orphans.filter(w => w.done).length;
      const orphansHtml = orphans.length ? `
        <div class="mb-4">
          <div class="flex items-center justify-between px-3 py-1.5 bg-gray-50 rounded-t">
            <span class="text-xs font-semibold text-gray-500 italic">Sem grouping</span>
            <span class="text-[10px] text-gray-400 whitespace-nowrap">${orphansDone}/${orphans.length}</span>
          </div>
          <table class="w-full text-xs border-collapse border border-gray-100 rounded-b">
            ${theadHtml}
            <tbody>${orphans.map(_walletRow).join('')}</tbody>
          </table>
        </div>` : '';

      const empty = !groupingsHtml && !orphansHtml;
      content.innerHTML       = empty ? '' : (headerHtml + groupingsHtml + orphansHtml);
      content.style.display   = empty ? 'none' : '';
      msg.style.display       = empty ? '' : 'none';
      if (empty) msg.textContent = 'Nenhum resultado com os filtros aplicados.';
      return;
    }

    // Grouping-level columns (nav_grouping / published)
    const items = (data.groupings || []).filter(g =>
      !grpQ || (g.groupingName || '').toLowerCase().includes(grpQ),
    );
    const headerHtml = `
      <div class="mb-3 flex items-center justify-between gap-3 text-xs text-gray-500">
        <div>
          <span class="font-semibold text-gray-700">${data.doneGroupings}/${data.totalGroupings}</span>
          grouping(s) com ${_escHtml(colLabel)}
        </div>
        <div class="text-[10px] text-gray-400">${data.date}</div>
      </div>`;
    const rows = items.map(g => `
      <tr class="border-b border-gray-100">
        <td class="px-3 py-1.5 text-gray-700 truncate" title="${_escHtml(g.groupingId)}">${_escHtml(g.groupingName)}</td>
        <td class="px-3 py-1.5 text-right whitespace-nowrap">${_cdStatusBadge(g.done, col)}</td>
      </tr>`).join('');
    const tableHtml = items.length ? `
      <table class="w-full text-xs border-collapse">
        <thead class="bg-gray-50 text-gray-500 uppercase">
          <tr>
            <th class="px-3 py-2 text-left">Grouping</th>
            <th class="px-3 py-2 text-right">Status</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>` : '';

    content.innerHTML     = items.length ? (headerHtml + tableHtml) : '';
    content.style.display = items.length ? '' : 'none';
    msg.style.display     = items.length ? 'none' : '';
    if (!items.length) msg.textContent = 'Nenhum grouping com os filtros aplicados.';
  }

  function filterRows() {
    const q = document.getElementById("companyFilter").value.toLowerCase();
    document.querySelectorAll("#tbody tr").forEach(tr =>
      tr.style.display = tr.dataset.company.includes(q) ? "" : "none"
    );
  }

  // ── Date-card navigation ────────────────────────────────────────────────
  // Shift business-day window by 10 days; direction: -1 (older) or +1 (newer).
  function _shiftBizDays(isoDate, n) {
    const d = new Date(isoDate + "T12:00:00"); // noon avoids DST/TZ edge cases
    const dir = n > 0 ? 1 : -1;
    let remaining = Math.abs(n);
    while (remaining > 0) {
      d.setDate(d.getDate() + dir);
      const wd = d.getDay();
      if (wd !== 0 && wd !== 6) remaining--;
    }
    return d.toISOString().slice(0, 10);
  }

  function _renderDateCards(newCards) {
    const grid = document.getElementById("date-cards");
    grid.innerHTML = newCards.map((c, i) => `
      <button data-date="${_escHtml(c.date)}" onclick="selectDate(this.dataset.date)"
        class="date-card border-2 border-gray-200 rounded-lg px-2 py-1.5 text-center bg-white hover:border-blue-300 transition-colors flex items-center justify-center whitespace-nowrap overflow-hidden${i === newCards.length - 1 ? " active" : ""}">
        <span class="card-date text-xs font-semibold text-gray-600 truncate">${_escHtml(c.date)}</span>
      </button>`).join("");
    if (newCards.length) {
      const last = newCards[newCards.length - 1].date;
      selectDate(last);
      const picker = document.getElementById("end-date-picker");
      if (picker) picker.value = last;
    }
  }

  function _loadDateCards(endDate) {
    const btnPrev = document.getElementById("date-prev");
    const btnNext = document.getElementById("date-next");
    btnPrev.disabled = btnNext.disabled = true;

    return fetch(`/api/controlpanel/date-cards?endDate=${endDate}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} — reinicie o Flask (o endpoint /api/controlpanel/date-cards é novo).`);
        return r.json();
      })
      .then(({ cards: newCards }) => _renderDateCards(newCards))
      .catch(err => {
        console.error("loadDateCards error:", err);
        const msg = document.getElementById("msg");
        msg.textContent = `Erro ao carregar datas: ${err.message}`;
        msg.style.color = "#b91c1c";
        msg.style.display = "block";
      })
      .finally(() => { btnPrev.disabled = btnNext.disabled = false; });
  }

  function shiftDateCards(direction) {
    const cards = [...document.querySelectorAll("#date-cards .date-card")];
    if (!cards.length) return;
    const firstDate = cards[0].dataset.date;
    const lastDate  = cards[cards.length - 1].dataset.date;
    // Window size = number of visible cards. Forward jump moves a whole
    // window ahead so successive clicks step cleanly without overlap.
    const endDate = direction < 0
      ? _shiftBizDays(firstDate, -1)              // window ending right before the current first
      : _shiftBizDays(lastDate,  cards.length);   // advance one full window forward
    _loadDateCards(endDate);
  }

  // Debounce so typing year-by-digit (e.g. 2,0,2,6) does not fire a fetch per
  // keystroke — Chrome's date input dispatches `change` on every committed
  // segment, not only on the full date.
  let _endDateTimer = null;
  function setEndDate(value) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
    clearTimeout(_endDateTimer);
    _endDateTimer = setTimeout(() => _loadDateCards(value), 500);
  }

  function _escHtml(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  // Só populado quando o modal é aberto pelo CABEÇALHO da coluna (todas as
  // empresas de uma vez) — mapeia walletId -> {id, name} da empresa, pra
  // _applyMappingDirect saber em quais empresas aplicar cada mapeamento.
  let _companyByWallet = {};
  let _crossCompanyMode = false;

  function _groupBySecurity(issues) {
    const map = {};
    _companyByWallet = {};
    issues.forEach(i => {
      const key = i.unprocessedSecurityId || i.externalId || "(sem id)";
      if (!map[key]) map[key] = { externalId: i.externalId, unprocessedSecurityId: i.unprocessedSecurityId, wallets: {}, companies: {} };
      const wid = i.walletId;
      if (wid && !map[key].wallets[wid]) map[key].wallets[wid] = i.walletName || "";
      if (wid && i.companyId) {
        _companyByWallet[wid] = { id: i.companyId, name: i.company || i.companyId };
        if (!map[key].companies[i.companyId]) map[key].companies[i.companyId] = i.company || i.companyId;
      }
    });
    return Object.values(map)
      .map(r => {
        const walletList = Object.entries(r.wallets).map(([id, name]) => ({ id, name }));
        const companyList = Object.entries(r.companies).map(([id, name]) => ({ id, name }));
        return { externalId: r.externalId, unprocessedSecurityId: r.unprocessedSecurityId, walletCount: walletList.length, wallets: walletList, companies: companyList };
      })
      .sort((a, b) => b.walletCount - a.walletCount);
  }

  // Stored per-uid wallet data for the sub-modal
  let _walletsByUid = {};

  function _openWallets(uid) {
    const wallets = _walletsByUid[uid] || [];
    document.getElementById("wallets-tbody").innerHTML = wallets.map(w =>
      `<tr class="border-t border-gray-100">
        <td class="px-3 py-2 font-mono text-gray-500">${_escHtml(w.id)}</td>
        <td class="px-3 py-2 text-gray-700">${_escHtml(w.name)}</td>
      </tr>`).join("") || `<tr><td colspan="2" class="px-3 py-4 text-center text-gray-400">Nenhuma carteira</td></tr>`;
    document.getElementById("wallets-modal").style.display = "flex";
  }

  // ── Security search sub-modal ────────────────────────────────────────────
  let _secSearchUid = null;       // uid of the row being edited
  let _secSearchTimer = null;
  let _secSearchTypesLoaded = false;
  let _secSearchResults = [];     // last search results (for click lookup by index)
  let _secSearchReqId   = 0;      // monotonic id — ignore out-of-order responses
  // Source pool: "positions" reads from the wallet's most recent
  // processedPosition; "cadastro" reads from the in-memory securities cache.
  // The positions tab is the default when at least one wallet has a
  // snapshot — those are the strongest signal for the right pick.
  let _secSearchSource = "cadastro";
  let _secSearchPositionsCount = null;  // null = not loaded yet
  // Identification (the wallet-positions source) runs in a separate instance.
  // When off, this instance hides the "Posições da carteira" tab and never
  // calls /wallet-positions; the global "Cadastro" search remains available.

  function _openSecuritySearch(uid) {
    _secSearchUid = uid;
    document.getElementById("sec-search-uid").textContent = uid;
    document.getElementById("sec-search-input").value = "";
    document.getElementById("sec-search-tbody").innerHTML = "";
    document.getElementById("sec-search-msg").textContent = "Carregando…";
    document.getElementById("sec-search-msg").style.display = "block";
    document.getElementById("sec-search-count").textContent = "";
    // The "Posições da carteira" tab + badge only exist when identification is
    // enabled (gated by the identificar_enabled Jinja flag). Guard so the
    // Cadastro-only flow on a Mongo-free instance doesn't throw on a null element.
    const _posBadge = document.getElementById("sec-search-positions-badge");
    if (_posBadge) _posBadge.textContent = "";
    _secSearchPositionsCount = null;

    // Populate type dropdown once from the cached _securityTypes list
    if (!_secSearchTypesLoaded && _securityTypes.length) {
      const sel = document.getElementById("sec-search-type");
      _securityTypes.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t; opt.textContent = t;
        sel.appendChild(opt);
      });
      _secSearchTypesLoaded = true;
    }

    // Start with no type filter — the classifier's label set and the cache's
    // securityType values can diverge, so pre-filtering risks hiding everything.
    document.getElementById("sec-search-type").value = "";

    // Default tab: wallet positions when we have wallets for this uid;
    // otherwise fall back to the global cadastro.
    const wallets = _walletsByUid[uid] || [];
    _secSearchSource = (IDENTIFICAR_ENABLED && wallets.length) ? "positions" : "cadastro";
    _setSecuritySearchTab(_secSearchSource);

    document.getElementById("security-search-modal").style.display = "flex";
    setTimeout(() => document.getElementById("sec-search-input").focus(), 50);

    // Kick off the initial render for the chosen source.
    _runSecuritySearch();
  }

  function _closeSecuritySearch() {
    document.getElementById("security-search-modal").style.display = "none";
    _secSearchUid = null;
    if (_secSearchTimer) { clearTimeout(_secSearchTimer); _secSearchTimer = null; }
  }

  function _setSecuritySearchTab(source) {
    const positions = document.getElementById("sec-search-tab-positions");
    const cadastro  = document.getElementById("sec-search-tab-cadastro");
    const typeSel   = document.getElementById("sec-search-type");
    const input     = document.getElementById("sec-search-input");
    const active   = "border-blue-600 text-blue-700 font-semibold bg-white";
    const inactive = "border-transparent text-gray-500 hover:text-blue-600";
    // `positions` is absent when identification is off (tab hidden by Jinja);
    // guard every access so the Cadastro tab still works on a Mongo-free instance.
    if (source === "positions") {
      if (positions) positions.className = "px-3 py-1.5 rounded-t border-b-2 " + active;
      cadastro.className  = "px-3 py-1.5 rounded-t border-b-2 " + inactive;
      // Type filter only makes sense over the global cadastro — wallets
      // typically carry a handful of types, so hide it to reduce noise.
      typeSel.style.display = "none";
      input.placeholder = "Filtrar posições por nome, mainId, ticker, taxId, ISIN, selicCode...";
    } else {
      cadastro.className  = "px-3 py-1.5 rounded-t border-b-2 " + active;
      if (positions) positions.className = "px-3 py-1.5 rounded-t border-b-2 " + inactive;
      typeSel.style.display = "";
      input.placeholder = "Buscar por nome, mainId, ticker, taxId, ISIN, selicCode...";
    }
  }

