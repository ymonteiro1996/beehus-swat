/* Exceções — intradiário: montar patches, aplicar, init.
   Escopo global compartilhado; ordem importa. */
  async function runIntradayBuildPatches() {
    if (!_it.selectedGroupKeys.size) return;
    const payload = _intradayBuildPayload();
    if (!_intradayValidate(payload)) return;

    payload.selectedGroups = Array.from(_it.selectedGroupKeys).map(k => {
      const [walletId, securityId, date] = k.split("|");
      return {walletId, securityId, date};
    });

    document.getElementById("it-status").textContent = "Calculando posições...";
    document.getElementById("it-patched-section").classList.add("hidden");
    document.getElementById("it-results").classList.add("hidden");
    _it.patched           = [];
    _it.selectedPatchKeys = new Set();
    _it.transactions      = [];
    document.getElementById("it-txn-preview").classList.add("hidden");

    let resp;
    try {
      const r = await fetch("/api/excecoes/intraday/build-patches", {
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

    _it.patched = resp.patched || [];
    // Names may have grown (build-patches resolves walletNames for the
    // patched wallets even if some weren't in the detected groups list).
    Object.assign(_it.walletNames,   resp.walletNames   || {});
    Object.assign(_it.securityNames, resp.securityNames || {});
    _it.transactions = resp.transactions || [];
    // Default selection: every patch that has at least one row to zero —
    // skipping the no-op patches reduces accidental empty uploads.
    _it.selectedPatchKeys = new Set(
      _it.patched
        .filter(p => (p.zeroedUnprocessedIds || []).length > 0)
        .map(p => `${p.walletId}|${p.date}`),
    );

    _renderIntradayPatched();
    _renderIntradayTransactionsPreview();
    document.getElementById("it-status").textContent =
      `${_it.patched.length} posição(ões) gerada(s) · ${_it.transactions.length} transação(ões) planejada(s).`;
  }

  function _renderIntradayTransactionsPreview() {
    const wrap  = document.getElementById("it-txn-preview");
    const tbody = document.getElementById("it-txn-tbody");
    const count = document.getElementById("it-txn-count");
    const txns  = _it.transactions;
    if (!txns.length) {
      wrap.classList.add("hidden");
      tbody.innerHTML = "";
      return;
    }
    wrap.classList.remove("hidden");
    count.textContent = `(${txns.length})`;
    tbody.innerHTML = txns.map(t => {
      const wname = _it.walletNames[t.walletId]   || t.walletId;
      const sname = t.securityBeehusName
                  || _it.securityNames[t.securityId]
                  || t.securityId;
      // Skipped rows show their reason in muted pill so the operator
      // knows why a planned transaction won't actually be created.
      const status = t.skip
        ? `<span class="pill pill-warn" title="${escHtml(t.skipReason || '')}">skip — ${escHtml(t.skipReason || '')}</span>`
        : '<span class="pill pill-info">criar</span>';
      return `<tr class="border-t border-gray-100">
        <td class="px-2 py-1.5">${escHtml(t.date)}</td>
        <td class="px-2 py-1.5">${escHtml(wname)}</td>
        <td class="px-2 py-1.5">
          <div class="font-medium text-gray-800">${escHtml(sname)}</div>
          <div class="text-[10px] text-gray-400 font-mono">${escHtml(t.securityId)}</div>
        </td>
        <td class="px-2 py-1.5 text-gray-600">${escHtml(t.description || "")}</td>
        <td class="px-2 py-1.5 text-right">${fmtMoney(t.balance)}</td>
        <td class="px-2 py-1.5">${escHtml(t.currencyId || "")}</td>
        <td class="px-2 py-1.5">${status}</td>
      </tr>`;
    }).join("");
  }

  function _renderIntradayPatched() {
    const sec   = document.getElementById("it-patched-section");
    const count = document.getElementById("it-patched-count");
    const list  = document.getElementById("it-patched-list");
    const patched = _it.patched;
    const wn = _it.walletNames;

    // Aggregate warnings/info about mappings + positions. "added" rows
    // (synthetic zero entries for round-trips that left no carry) and
    // "no position doc" cases are reported as info pills rather than
    // warnings — they're the expected shape for clean intraday closes.
    const warns = [];
    const unmapped = new Set();
    let addedCount = 0;
    let noPositionCount = 0;
    patched.forEach(p => {
      (p.unmappedSecurityIds || []).forEach(s => unmapped.add(s));
      addedCount += (p.addedUnprocessedIds || []).length;
      if (!p.hasPosition) noPositionCount++;
    });
    if (unmapped.size) {
      warns.push(`<p class="pill pill-warn">${unmapped.size} security(ies) sem <span class="font-mono">beehusName</span> em <span class="font-mono">db.securities</span> — não há nome para usar como Ativo no patch.</p>`);
    }
    if (addedCount) {
      warns.push(`<p class="pill pill-info">${addedCount} linha(s) day-trade adicionada(s) ao patch (Ativo = <span class="font-mono">beehusName</span> da security, valores zerados).</p>`);
    }
    if (noPositionCount) {
      warns.push(`<p class="pill pill-warn">${noPositionCount} (carteira, data) sem documento em <span class="font-mono">unprocessedSecurityPositions</span>.</p>`);
    }
    document.getElementById("it-warnings").innerHTML = warns.join(" ");

    if (!patched.length) {
      sec.classList.add("hidden");
      list.innerHTML = "";
      return;
    }
    sec.classList.remove("hidden");
    count.textContent = `(${patched.length})`;
    list.innerHTML = patched.map(p => {
      const key   = `${p.walletId}|${p.date}`;
      const wname = wn[p.walletId] || p.walletId;
      const zeroedSet = new Set(p.zeroedUnprocessedIds || []);
      const checked   = _it.selectedPatchKeys.has(key) ? "checked" : "";
      // Disable the checkbox when the patch has nothing to zero — the
      // server would skip it anyway, no point letting the operator pick it.
      const disabled  = zeroedSet.size ? "" : "disabled";
      const addedSet = new Set(p.addedUnprocessedIds || []);
      const rows = (p.rows || []).map(r => {
        const cls = r.zeroed ? "it-row-zeroed" : "";
        const mark = r.added
          ? '<span class="pill pill-info"  title="linha adicionada (security não estava na posição original)">adicionar</span>'
          : (r.zeroed
              ? '<span class="pill pill-error" title="zerado">zerar</span>'
              : '<span class="pill pill-muted" title="baseline — não tocado">baseline</span>');
        return `<tr class="border-t border-gray-100 ${cls}">
          <td class="px-2 py-1 text-center">${mark}</td>
          <td class="px-2 py-1 font-mono text-[11px]">${escHtml(r.unprocessedId)}</td>
          <td class="px-2 py-1 text-right">${fmtNum(r.quantity)}</td>
          <td class="px-2 py-1 text-right">${fmtNum(r.pu)}</td>
          <td class="px-2 py-1 text-right">${fmtMoney(r.balance)}</td>
          <td class="px-2 py-1">${escHtml(r.currencyId || "")}</td>
        </tr>`;
      }).join("");
      const zeroedExisting = zeroedSet.size - addedSet.size;
      const headerInfo = [
        `${(p.rows || []).length} ativo(s)`,
        zeroedExisting > 0 ? `<span class="pill pill-error">${zeroedExisting} zerado(s)</span>` : "",
        addedSet.size      ? `<span class="pill pill-info">${addedSet.size} adicionado(s)</span>` : "",
        zeroedSet.size === 0 ? '<span class="pill pill-muted">nenhum a zerar/adicionar</span>' : "",
        p.hasPosition ? "" : '<span class="pill pill-warn">sem doc de posição</span>',
      ].filter(Boolean).join(" ");
      const emptyMsg = (p.rows || []).length
        ? ""
        : '<p class="text-center text-gray-400 text-xs py-3">Nenhuma linha em unprocessedSecurityPositions.</p>';
      return `<div class="border rounded">
        <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center gap-2">
          <span class="flex items-center gap-2">
            <input type="checkbox" data-it-patch-key="${escHtml(key)}" ${checked} ${disabled} onchange="onIntradayPatchToggle(this)" />
            <span>${escHtml(wname)} — ${escHtml(p.date)}</span>
          </span>
          <span class="text-gray-400 font-normal">${headerInfo}</span>
        </div>
        ${(p.rows || []).length ? `<table class="w-full text-xs">
          <thead class="bg-gray-50 text-gray-500 uppercase">
            <tr>
              <th class="px-2 py-1 text-center min-w-[80px]">Marca</th>
              <th class="px-2 py-1 text-left">Ativo</th>
              <th class="px-2 py-1 text-right">Quant.</th>
              <th class="px-2 py-1 text-right">PU</th>
              <th class="px-2 py-1 text-right">Saldo</th>
              <th class="px-2 py-1 text-left">Moeda</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>` : emptyMsg}
      </div>`;
    }).join("");

    _refreshIntradayApplyButton();
  }

  function onIntradayPatchToggle(cb) {
    const key = cb.dataset.itPatchKey;
    if (cb.checked) _it.selectedPatchKeys.add(key);
    else            _it.selectedPatchKeys.delete(key);
    _refreshIntradayApplyButton();
  }

  function toggleIntradayPatchesAll(checked) {
    if (checked) {
      // Only check patches that have rows to zero (the disabled ones).
      _it.selectedPatchKeys = new Set(
        _it.patched
          .filter(p => (p.zeroedUnprocessedIds || []).length > 0)
          .map(p => `${p.walletId}|${p.date}`),
      );
    } else {
      _it.selectedPatchKeys = new Set();
    }
    _renderIntradayPatched();
  }

  function _refreshIntradayApplyButton() {
    const n = _it.selectedPatchKeys.size;
    document.getElementById("it-apply-count").textContent = String(n);
    document.getElementById("it-apply-btn").disabled      = (n === 0);
  }

  // ── Excel download (uses the patched-positions selection) ───────────
  function downloadIntradayExcel() {
    const payload = _intradayBuildPayload();
    if (!_intradayValidate(payload)) return;
    // Pass selectedGroups so the workbook covers what the operator picked
    // in step 1; without it the server would produce a "snapshot of
    // everything detected" which doesn't match what's on screen.
    if (_it.selectedGroupKeys.size) {
      payload.selectedGroups = Array.from(_it.selectedGroupKeys).map(k => {
        const [walletId, securityId, date] = k.split("|");
        return {walletId, securityId, date};
      });
    }
    fetch("/api/excecoes/intraday/excel", {
      method:  "POST",
      headers: {"Content-Type": "application/json"},
      body:    JSON.stringify(payload),
    }).then(async r => {
      if (!r.ok) {
        const d = await r.json().catch(() => ({error: "falha"}));
        alert("Erro ao baixar Excel: " + (d.error || r.status));
        return;
      }
      const blob = await r.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url;
      a.download = `intraday_${payload.companyId}_${payload.initialDate}_${payload.finalDate}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }).catch(() => alert("Erro de rede ao baixar Excel."));
  }

  // ── Step 3: apply (upload selected patches) ─────────────────────────
  // Mirrors `submitSelectedTransactions` in templates/correcoes.html: each
  // transaction is shipped via its own HTTP call so the operator gets per-
  // row progress and a 401 short-circuits the whole batch. After the txn
  // sweep (which fans out from the client) the patched positions go up
  // in the same single bulk call as before — the upstream replaces the
  // entire wallet position in one request, so per-row HTTP gains nothing.
  async function runIntradayApply() {
    if (!_it.selectedPatchKeys.size) { alert("Marque pelo menos uma posição patcheada."); return; }
    const payload = _intradayBuildPayload();
    if (!_intradayValidate(payload)) return;

    const groupPayloads = Array.from(_it.selectedGroupKeys).map(k => {
      const [walletId, securityId, date] = k.split("|");
      return {walletId, securityId, date};
    });
    payload.selectedGroups  = groupPayloads;
    payload.selectedPatches = Array.from(_it.selectedPatchKeys).map(k => {
      const [walletId, date] = k.split("|");
      return {walletId, date};
    });

    // Plans that survived the build-patches preview unmarked as `skip`
    // are the ones the per-row submit will actually attempt.
    const txnPlans = (_it.transactions || [])
      .filter(t => !t.skip);
    const confirmMsg =
      `Enviar para a API Beehus:\n` +
      `  • ${txnPlans.length} transação(ões) securityContributionAdjustment\n` +
      `  • ${payload.selectedPatches.length} posição(ões) patcheada(s)\n\n` +
      `Confirmar?`;
    if (!confirm(confirmMsg)) return;

    document.getElementById("it-results").classList.remove("hidden");
    document.getElementById("it-apply-btn").disabled = true;
    const list = document.getElementById("it-results-list");
    list.innerHTML = '<p class="text-xs text-gray-400">Enviando transações...</p>';

    // ── Phase 1: sequential transaction submit ─────────────────────────
    const txnResults = [];
    let authBroke = null;
    for (let i = 0; i < txnPlans.length; i++) {
      const t = txnPlans[i];
      list.innerHTML =
        `<p class="text-xs text-gray-400">Enviando transação ${i + 1}/${txnPlans.length}...</p>`;
      let r, data;
      try {
        r = await fetch("/api/excecoes/intraday/transactions/submit", {
          method:  "POST",
          headers: {"Content-Type": "application/json"},
          body:    JSON.stringify({
            ...payload,                             // filter envelope
            group: {walletId: t.walletId, securityId: t.securityId, date: t.date},
          }),
        });
        data = await r.json().catch(() => ({}));
      } catch (e) {
        txnResults.push({...t, status: "error", error: "rede: " + (e.message || "")});
        continue;
      }
      if (r.ok && data.ok) {
        txnResults.push({...t, ...data});
      } else {
        txnResults.push({...t, ...data,
                         status: data.status || (r.status === 401 ? "auth_error" : "error"),
                         error:  data.error || `HTTP ${r.status}`});
        if (r.status === 401) {
          authBroke = data;
          break;
        }
      }
    }

    // ── Phase 2: bulk patched-position upload (only if auth still good) ─
    let posData = null;
    if (!authBroke) {
      list.innerHTML += '<p class="text-xs text-gray-400 mt-2">Enviando posições patcheadas...</p>';
      try {
        const r = await fetch("/api/excecoes/intraday/apply", {
          method:  "POST",
          headers: {"Content-Type": "application/json"},
          body:    JSON.stringify(payload),
        });
        posData = await r.json().catch(() => ({}));
      } catch (e) {
        posData = {error: "rede: " + (e.message || "")};
      }
    }

    _renderIntradayApplyResults({
      transactionResults: txnResults,
      ...(posData || {}),
      ...(authBroke ? {error: authBroke.error} : {}),
    });
    _refreshIntradayApplyButton();
  }

  function _renderIntradayApplyResults(d) {
    const list = document.getElementById("it-results-list");
    const wn = _it.walletNames || {};
    const sn = _it.securityNames || {};
    let html = "";
    if (d.error) {
      html += `<p class="pill pill-error">${escHtml(d.error)}</p>`;
    }

    const txns = d.transactionResults || [];
    if (txns.length) {
      html += '<p class="text-xs font-semibold text-gray-700 mt-1 mb-1">Transações criadas</p>';
      html += txns.map(r => {
        const cls = r.status === "ok" ? "success"
                  : (r.status === "skipped" ? "muted" : "error");
        const pill = `<span class="pill pill-${cls}">${escHtml(r.status)}</span>`;
        let extra = "";
        if (r.status === "ok") {
          extra = r.createdId ? ` — id <span class="font-mono text-[10px]">${escHtml(r.createdId)}</span>` : "";
        } else if (r.error)  extra = ` — ${escHtml(r.error)}`;
        else if (r.reason)   extra = ` — ${escHtml(r.reason)}`;
        const wname = wn[r.walletId]   || r.walletId;
        const sname = r.securityBeehusName
                    || sn[r.securityId] || r.securityId || "";
        return `<div class="text-xs text-gray-700">${pill} <strong>${escHtml(sname)}</strong> · ${escHtml(wname)} · ${escHtml(r.date)} · ${fmtMoney(r.balance)}${extra}</div>`;
      }).join("");
    }

    const rows = d.results || [];
    if (rows.length) {
      html += '<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">Posições patcheadas</p>';
      html += rows.map(r => {
        const cls = r.status === "ok" ? "success" : (r.status === "skipped" ? "muted" : "error");
        const pill = `<span class="pill pill-${cls}">${escHtml(r.status)}</span>`;
        let extra = "";
        if (r.status === "ok") extra = ` — ${r.rows} linha(s), ${r.zeroed} zerada(s)`;
        else if (r.error)  extra = ` — ${escHtml(r.error)}`;
        else if (r.reason) extra = ` — ${escHtml(r.reason)}`;
        const wname = wn[r.walletId] || r.walletId;
        return `<div class="text-xs text-gray-700">${pill} <strong>${escHtml(wname)}</strong> · ${escHtml(r.date)}${extra}</div>`;
      }).join("");
    }
    list.innerHTML = html || '<p class="text-xs text-gray-400">Nenhum resultado.</p>';
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  loadList();
  // Deep-link: `#day-trade` opens straight into the Ajustes day-trade view.
  // Used by the workflow helper on /painel so step 3 lands the operator on
  // the intraday screen without an extra click.
  if (location.hash === "#day-trade") {
    showIntradayView();
  }
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      closeSetupModal();
      closeApplyModal();
      // Esc on the day-trade view returns to the list view (only when the
      // view is actually showing — otherwise it'd clobber other dialogs).
      if (!document.getElementById("intraday-view").classList.contains("hidden")) {
        hideIntradayView();
      }
    }
  });
