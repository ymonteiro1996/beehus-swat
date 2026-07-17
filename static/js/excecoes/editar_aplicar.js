/* Exceções — editar, preview e aplicar exceção.
   Escopo global compartilhado; ordem importa. */
  function editException(id, cid) {
    fetch(`/api/excecoes/${encodeURIComponent(id)}?companyId=${encodeURIComponent(cid)}`)
      .then(r => r.json())
      .then(d => {
        if (d.error) { alert(d.error); return; }
        const e = d.exception;
        openSetupModal();
        _setupState.editingId = e.id;
        _setupState.companyId = e.companyId;
        document.getElementById("setup-company").value = e.companyId;
        document.getElementById("setup-name").value = e.name || "";
        document.getElementById("setup-subtitle").textContent = "Editando: " + (e.name || "");

        // Need wallets list to render dropdowns
        return fetch(`/api/excecoes/wallets?companyId=${encodeURIComponent(e.companyId)}`)
          .then(r => r.json())
          .then(({ wallets }) => {
            _setupState.walletsForCompany = wallets || [];
            const sel = document.getElementById("setup-source");
            sel.innerHTML = '<option value="">Selecione...</option>'
              + (wallets || []).map(w => `<option value="${escHtml(w.id)}">${escHtml(w.name)}</option>`).join("");
            sel.disabled = false;
            sel.value = e.sourceWalletId;
            _setupState.sourceWalletId = e.sourceWalletId;
            _setupState.outputWalletIds = (e.outputWalletIds || []).slice();
            renderOutputs();

            // Rebuild rules map indexed by uid.
            _setupState.rules = {};
            (e.rules || []).forEach(r => {
              _setupState.rules[r.unprocessedId] = {
                selected:           true,
                addToWalletId:      r.addToWalletId || "",
                removeFromWalletId: r.removeFromWalletId || "",
                caixa:              !!r.caixa,
              };
            });
            // Render rules using a synthetic source-securities list so the
            // user sees existing rules even before they reload the position.
            _setupState.sourceSecurities = (e.rules || []).map(r => ({
              unprocessedId: r.unprocessedId,
              quantity: null, pu: null, balance: null,
            }));
            renderSetupSecurities();
          });
      });
  }

  // ── Apply (daily routine) ─────────────────────────────────────────────────
  function openApplyModal(id, companyId) {
    _apply = {exceptionId: id, companyId, plan: null};
    document.getElementById("apply-modal").classList.remove("hidden");
    document.getElementById("apply-subtitle").textContent = "";
    document.getElementById("apply-date").value = todayISO();
    document.getElementById("apply-warnings").innerHTML = "";
    document.getElementById("apply-preview").classList.add("hidden");
    document.getElementById("apply-results").classList.add("hidden");
    document.getElementById("apply-results-list").innerHTML = "";
    document.getElementById("apply-wallets").innerHTML = "";
    document.getElementById("apply-run-btn").disabled = true;
    document.getElementById("apply-download-btn").disabled = true;

    const exc = _exceptions.find(e => e.id === id);
    if (exc) {
      document.getElementById("apply-subtitle").textContent =
        `${exc.name} — ${exc.companyName} — origem: ${exc.sourceWalletName}`;
    }
  }

  function closeApplyModal() {
    document.getElementById("apply-modal").classList.add("hidden");
  }

  function runPreview() {
    const date = document.getElementById("apply-date").value;
    if (!date) { alert("Selecione uma data."); return; }
    document.getElementById("apply-warnings").innerHTML =
      '<p class="text-xs text-gray-400">Calculando...</p>';
    document.getElementById("apply-preview").classList.add("hidden");
    document.getElementById("apply-results").classList.add("hidden");
    document.getElementById("apply-run-btn").disabled = true;
    document.getElementById("apply-download-btn").disabled = true;

    fetch(`/api/excecoes/${encodeURIComponent(_apply.exceptionId)}/preview`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({companyId: _apply.companyId, date}),
    })
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .then(({ok, d}) => {
        if (!ok || d.error) {
          document.getElementById("apply-warnings").innerHTML =
            `<p class="pill pill-error">${escHtml(d.error || "Falha")}</p>`;
          return;
        }
        _apply.plan = d;
        renderPreview(d);
        document.getElementById("apply-run-btn").disabled = false;
        document.getElementById("apply-download-btn").disabled = false;
      })
      .catch(() => {
        document.getElementById("apply-warnings").innerHTML =
          '<p class="pill pill-error">Erro de rede ao gerar pré-visualização.</p>';
      });
  }

  function renderPreview(plan) {
    const warns = [];
    if (plan.fallback) {
      warns.push(`<span class="pill pill-warn">Posição da origem usou fallback: ${escHtml(plan.sourceDate)} (alvo: ${escHtml(plan.targetDate)})</span>`);
    }
    if ((plan.missingRules || []).length) {
      warns.push(`<span class="pill pill-warn">${plan.missingRules.length} regra(s) sem ativo correspondente: ${plan.missingRules.map(escHtml).join(", ")}</span>`);
    }
    if ((plan.transactionsUnmapped || []).length) {
      warns.push(`<span class="pill pill-warn">${plan.transactionsUnmapped.length} regra(s) sem securityMappings — transações dessas regras não serão migradas: ${plan.transactionsUnmapped.map(escHtml).join(", ")}</span>`);
    }
    if (!Object.keys(plan.wallets || {}).length && !(plan.transactions || []).length) {
      warns.push('<span class="pill pill-error">Nenhuma carteira afetada e nenhuma transação para migrar — nada a enviar.</span>');
    }
    document.getElementById("apply-warnings").innerHTML = warns.join(" ") || "";

    const cont = document.getElementById("apply-wallets");
    // Order: wallets that strip first ("removed"/"both"), then add-only,
    // then any baseline-only — makes the user's review flow top-down by
    // intent ("what was taken out" → "what received it").
    const _opOrder = {removed: 0, both: 1, added: 2};
    const entries = Object.entries(plan.wallets || {})
      .sort(([, a], [, b]) => (_opOrder[a.op] ?? 3) - (_opOrder[b.op] ?? 3));

    cont.innerHTML = entries.map(([wid, payload]) => {
      const wname = (plan.walletNames || {})[wid] || wid;
      const rowMark = op => {
        if (op === "added")   return '<span class="pill pill-success" title="Ativo recebido">+ adicionado</span>';
        if (op === "removed") return '<span class="pill pill-error"   title="Ativo retirado">− removido</span>';
        if (op === "both")    return '<span class="pill pill-warn"    title="Ativo recebido e retirado">± ambos</span>';
        return '<span class="pill pill-muted" title="Ativo já existente, não tocado">baseline</span>';
      };
      const rowCls = op => {
        if (op === "added")   return "bg-emerald-50/60";
        if (op === "removed") return "bg-rose-50/60";
        if (op === "both")    return "bg-amber-50/60";
        return "";
      };
      const rows = (payload.rows || []).map(r => `
        <tr class="border-t border-gray-100 ${rowCls(r.op)}">
          <td class="px-2 py-1 text-center">${rowMark(r.op)}</td>
          <td class="px-2 py-1 font-mono text-[11px]">${escHtml(r.unprocessedId)}</td>
          <td class="px-2 py-1 text-right">${fmtNum(r.quantity)}</td>
          <td class="px-2 py-1 text-right">${fmtNum(r.pu)}</td>
          <td class="px-2 py-1 text-right">${fmtMoney(r.balance)}</td>
          <td class="px-2 py-1 text-center">${r.caixa ? "Sim" : "Não"}</td>
          <td class="px-2 py-1">${escHtml(r.currencyId || "")}</td>
        </tr>`).join("");

      const headerMark = (() => {
        if (payload.op === "added")   return '<span class="pill pill-success">Recebe ativos</span>';
        if (payload.op === "removed") return '<span class="pill pill-error">Sofre strip</span>';
        if (payload.op === "both")    return '<span class="pill pill-warn">Recebe e sofre strip</span>';
        return "";
      })();
      const sourceMark = payload.isSource
        ? '<span class="pill pill-info ml-1">Origem</span>' : '';

      return `<div class="border rounded">
        <div class="bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center gap-2">
          <span class="flex items-center gap-2">
            <span>${escHtml(wname)}</span>
            ${headerMark}${sourceMark}
          </span>
          <span class="text-gray-400 font-normal">${(payload.rows || []).length} ativo(s) — ${escHtml(payload.currencyId || "")}</span>
        </div>
        <table class="w-full text-xs">
          <thead class="bg-gray-50 text-gray-500 uppercase">
            <tr>
              <th class="px-2 py-1 text-center min-w-[100px]">Marca</th>
              <th class="px-2 py-1 text-left">Ativo</th>
              <th class="px-2 py-1 text-right">Quant.</th>
              <th class="px-2 py-1 text-right">PU</th>
              <th class="px-2 py-1 text-right">Saldo</th>
              <th class="px-2 py-1 text-center">Caixa</th>
              <th class="px-2 py-1 text-left">Moeda</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    }).join("");

    renderTransactionPreview(plan);
    document.getElementById("apply-preview").classList.remove("hidden");
  }

  function renderTransactionPreview(plan) {
    const txs = plan.transactions || [];
    const cont = document.getElementById("apply-transactions");
    const tbody = document.getElementById("apply-tx-tbody");
    const msg   = document.getElementById("apply-tx-msg");
    const count = document.getElementById("apply-tx-count");
    const wn = plan.walletNames || {};
    const sn = plan.securityNames || {};

    const adjCount = txs.reduce((acc, tx) => acc + ((tx.adjustments || []).length), 0);
    cont.classList.remove("hidden");
    count.textContent = `(${txs.length} migração(ões) + ${adjCount} ajuste(s))`;

    if (!txs.length) {
      tbody.innerHTML = "";
      msg.textContent = (plan.transactionsUnmapped || []).length
        ? `Nenhuma transação encontrada. Aviso: ${plan.transactionsUnmapped.length} regra(s) sem mapeamento securityMappings: ${plan.transactionsUnmapped.join(", ")}`
        : "Nenhuma transação para migrar nesta data.";
      msg.classList.remove("hidden");
      return;
    }
    msg.classList.add("hidden");

    const rows = [];
    for (const tx of txs) {
      const fromName = wn[tx.fromWalletId] || tx.fromWalletId;
      const toName   = wn[tx.toWalletId]   || tx.toWalletId;
      const secName  = sn[tx.securityId]   || tx.unprocessedId;
      rows.push(`<tr class="border-t border-gray-100 bg-amber-50/40">
        <td class="px-2 py-1">
          <div class="font-medium text-gray-800">${escHtml(secName)}</div>
          <div class="text-[10px] text-gray-400 font-mono">${escHtml(tx.unprocessedId)}</div>
        </td>
        <td class="px-2 py-1 text-gray-600">${escHtml(tx.type)}</td>
        <td class="px-2 py-1">
          <span class="pill pill-error">−</span>
          <span class="ml-1">${escHtml(fromName)}</span>
        </td>
        <td class="px-2 py-1">
          <span class="pill pill-success">+</span>
          <span class="ml-1">${escHtml(toName)}</span>
        </td>
        <td class="px-2 py-1 text-right">${tx.balance == null ? '<span class="text-gray-300">--</span>' : fmtMoney(tx.balance)}</td>
        <td class="px-2 py-1 text-right">${tx.quantity == null ? '<span class="text-gray-300">--</span>' : fmtNum(tx.quantity)}</td>
        <td class="px-2 py-1">${escHtml(tx.liquidationDate)}</td>
        <td class="px-2 py-1 text-gray-600">${escHtml(tx.description || "")}</td>
      </tr>`);

      for (const adj of (tx.adjustments || [])) {
        const wname = wn[adj.walletId] || adj.walletId || "";
        const isFrom = adj.side === "from";
        const balCell = adj.balance == null
          ? '<span class="text-gray-300">--</span>'
          : fmtMoney(adj.balance);
        const qtyCell = adj.quantity == null
          ? '<span class="text-gray-300">--</span>'
          : fmtNum(adj.quantity);
        rows.push(`<tr class="border-t border-gray-50 bg-sky-50/40 text-gray-600">
          <td class="px-2 py-1 pl-6">
            <div class="text-[10px] uppercase tracking-wide text-sky-700 font-semibold">↳ Ajuste</div>
            <div class="text-[10px] text-gray-500">${escHtml(adj.description || "")}</div>
          </td>
          <td class="px-2 py-1 text-gray-600">${escHtml(adj.type || "securityTransfer")}</td>
          <td class="px-2 py-1">${isFrom
            ? `<span class="pill pill-success">+</span><span class="ml-1">${escHtml(wname)}</span>`
            : '<span class="text-gray-300">--</span>'}</td>
          <td class="px-2 py-1">${!isFrom
            ? `<span class="pill pill-error">−</span><span class="ml-1">${escHtml(wname)}</span>`
            : '<span class="text-gray-300">--</span>'}</td>
          <td class="px-2 py-1 text-right">${balCell}</td>
          <td class="px-2 py-1 text-right">${qtyCell}</td>
          <td class="px-2 py-1">${escHtml(adj.liquidationDate || tx.liquidationDate)}</td>
          <td class="px-2 py-1 text-gray-500 italic">${escHtml(adj.description || "")}</td>
        </tr>`);
      }
    }
    tbody.innerHTML = rows.join("");
  }

  function runApply() {
    if (!_apply.plan) return;
    if (!confirm("Confirmar envio para a API Beehus?")) return;

    const date = document.getElementById("apply-date").value;
    document.getElementById("apply-results-list").innerHTML =
      '<p class="text-xs text-gray-400">Enviando...</p>';
    document.getElementById("apply-results").classList.remove("hidden");
    document.getElementById("apply-run-btn").disabled = true;

    fetch(`/api/excecoes/${encodeURIComponent(_apply.exceptionId)}/apply`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({companyId: _apply.companyId, date}),
    })
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .then(({ok, d}) => {
        renderResults(d);
        document.getElementById("apply-run-btn").disabled = !ok;
        if (ok) loadList();
      })
      .catch(() => {
        document.getElementById("apply-results-list").innerHTML =
          '<p class="pill pill-error">Erro de rede.</p>';
      });
  }

  function renderResults(d) {
    const list = document.getElementById("apply-results-list");
    const wn = (_apply.plan && _apply.plan.walletNames) || {};
    const sn = (_apply.plan && _apply.plan.securityNames) || {};
    let html = "";

    if (d.error) {
      html += `<p class="pill pill-error">${escHtml(d.error)}</p>`;
    }

    const txs = d.transactionResults || [];
    if (txs.length) {
      html += '<p class="text-xs font-semibold text-gray-700 mt-2 mb-1">Transações</p>';
      html += txs.map(r => {
        const pill = `<span class="pill pill-${r.status === "ok" ? "success" : "error"}">${escHtml(r.status)}</span>`;
        const secName  = sn[r.securityId]   || r.securityId   || "";
        const extra = r.error ? ` — ${escHtml(r.error)}` : "";
        if (r.kind === "adjust") {
          const wname = wn[r.walletId] || r.walletId || "";
          const sign  = r.side === "to" ? "−" : "+";
          const bal   = (r.balance == null)
            ? '<span class="text-gray-300">--</span>'
            : Number(r.balance).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
          return `<div class="text-xs text-gray-700">${pill} <span class="pill pill-muted">Ajuste ${sign}</span> <strong>${escHtml(secName)}</strong>: ${escHtml(wname)} · ${bal}${extra}</div>`;
        }
        const fromName = wn[r.fromWalletId] || r.fromWalletId || "";
        const toName   = wn[r.toWalletId]   || r.toWalletId   || "";
        return `<div class="text-xs text-gray-700">${pill} <span class="font-mono text-[10px] text-gray-400">${escHtml(r.id)}</span> <strong>${escHtml(secName)}</strong>: ${escHtml(fromName)} → ${escHtml(toName)}${extra}</div>`;
      }).join("");
    }

    const positions = d.results || [];
    if (positions.length) {
      html += '<p class="text-xs font-semibold text-gray-700 mt-3 mb-1">Posições</p>';
      html += positions.map(r => {
        const cls = r.status === "ok" ? "success" : (r.status === "skipped" ? "muted" : "error");
        const pill = `<span class="pill pill-${cls}">${escHtml(r.status)}</span>`;
        let extra = "";
        if (r.status === "ok") extra = ` — ${r.rows} linha(s)`;
        else if (r.error) extra = ` — ${escHtml(r.error)}`;
        else if (r.reason) extra = ` — ${escHtml(r.reason)}`;
        const wname = wn[r.walletId] || r.walletId;
        return `<div class="text-xs text-gray-700">${pill} <strong>${escHtml(wname)}</strong>${extra}</div>`;
      }).join("");
    }

    list.innerHTML = html || '<p class="text-xs text-gray-400">Nenhum resultado.</p>';
  }

  function downloadExcel() {
    const date = document.getElementById("apply-date").value;
    if (!date) return;
    const url = `/api/excecoes/${encodeURIComponent(_apply.exceptionId)}/excel?companyId=${encodeURIComponent(_apply.companyId)}&date=${encodeURIComponent(date)}`;
    window.open(url, "_blank");
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Ajustes day-trade — full-screen view that toggles with the regular list.
  //
  // Three-step flow:
  //   1. Filter (company, dates, optional groupings/wallets) → POST
  //      /api/excecoes/intraday/check returns the detected groups[].
  //   2. Operator picks groups via checkbox + "Gerar posições patcheadas" →
  //      POST /api/excecoes/intraday/build-patches recomputes server-side
  //      using the same filter envelope plus the picked group keys.
  //   3. Operator picks (wallet, date) patches via checkbox + "Enviar
  //      selecionadas" → POST /api/excecoes/intraday/apply uploads only the
  //      picked patches.
  //
  // The server never trusts cached client state: each step ships the full
  // filter envelope so detection + patch construction run from scratch.
  // ══════════════════════════════════════════════════════════════════════════
