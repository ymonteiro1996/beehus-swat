/* Exceções — modal de criação/edição de exceção.
   Escopo global compartilhado; ordem importa. */
  function openSetupModal() {
    _setupState = {
      editingId: null,
      companyId: "",
      sourceWalletId: "",
      outputWalletIds: [],
      walletsForCompany: [],
      sourceSecurities: [],
      rules: {},
    };
    document.getElementById("setup-modal").classList.remove("hidden");
    document.getElementById("setup-subtitle").textContent = "";
    document.getElementById("setup-company").value = "";
    document.getElementById("setup-name").value = "";
    document.getElementById("setup-source").innerHTML = '<option value="">Selecione uma empresa primeiro...</option>';
    document.getElementById("setup-source").disabled = true;
    document.getElementById("setup-outputs").innerHTML = "Selecione uma carteira de origem primeiro.";
    document.getElementById("setup-date").value = todayISO();
    document.getElementById("setup-secs-tbody").innerHTML = "";
    document.getElementById("setup-secs-msg").textContent = "Configure os passos anteriores e carregue a posição.";
    document.getElementById("setup-secs-msg").style.display = "block";
    document.getElementById("setup-status").textContent = "";
  }

  function closeSetupModal() {
    document.getElementById("setup-modal").classList.add("hidden");
  }

  function onSetupCompanyChange() {
    const cid = document.getElementById("setup-company").value;
    _setupState.companyId = cid;
    _setupState.sourceWalletId = "";
    _setupState.outputWalletIds = [];
    _setupState.walletsForCompany = [];
    _setupState.sourceSecurities = [];
    _setupState.rules = {};
    document.getElementById("setup-source").innerHTML = '<option value="">Carregando...</option>';
    document.getElementById("setup-source").disabled = true;
    document.getElementById("setup-outputs").innerHTML = "Selecione uma carteira de origem primeiro.";
    document.getElementById("setup-secs-tbody").innerHTML = "";
    document.getElementById("setup-secs-msg").style.display = "block";

    if (!cid) return;
    fetch(`/api/excecoes/wallets?companyId=${encodeURIComponent(cid)}`)
      .then(r => r.json())
      .then(({ wallets }) => {
        _setupState.walletsForCompany = wallets || [];
        const sel = document.getElementById("setup-source");
        sel.innerHTML = '<option value="">Selecione...</option>'
          + (_setupState.walletsForCompany).map(w =>
            `<option value="${escHtml(w.id)}">${escHtml(w.name)}</option>`).join("");
        sel.disabled = false;
      })
      .catch(() => {
        document.getElementById("setup-source").innerHTML = '<option value="">Erro ao carregar</option>';
      });
  }

  function onSetupSourceChange() {
    _setupState.sourceWalletId = document.getElementById("setup-source").value;
    _setupState.outputWalletIds = [];
    renderOutputs();
  }

  function renderOutputs() {
    const cont = document.getElementById("setup-outputs");
    if (!_setupState.sourceWalletId) {
      cont.innerHTML = "Selecione uma carteira de origem primeiro.";
      cont.classList.add("text-gray-400");
      return;
    }
    const candidates = _setupState.walletsForCompany.filter(w => w.id !== _setupState.sourceWalletId);
    if (!candidates.length) {
      cont.innerHTML = "Nenhuma outra carteira disponível para esta empresa.";
      return;
    }
    cont.classList.remove("text-gray-400");
    cont.innerHTML = candidates.map(w => `
      <label class="flex items-center gap-2 py-1 cursor-pointer text-xs text-gray-700">
        <input type="checkbox" value="${escHtml(w.id)}" onchange="toggleOutputWallet('${escHtml(w.id)}')"
          ${_setupState.outputWalletIds.includes(w.id) ? "checked" : ""} />
        <span>${escHtml(w.name)} <span class="text-[10px] text-gray-400">${escHtml(w.currencyId || "")}</span></span>
      </label>`).join("");
  }

  function toggleOutputWallet(wid) {
    const i = _setupState.outputWalletIds.indexOf(wid);
    if (i >= 0) _setupState.outputWalletIds.splice(i, 1);
    else _setupState.outputWalletIds.push(wid);
    // If a rule's add/remove was using this wallet, the dropdown options
    // change — re-render the rules table.
    if (_setupState.sourceSecurities.length) renderSetupSecurities();
  }

  function loadSourcePosition() {
    const cid  = _setupState.companyId;
    const wid  = _setupState.sourceWalletId;
    const date = document.getElementById("setup-date").value;
    if (!cid || !wid || !date) {
      alert("Empresa, carteira e data são obrigatórios.");
      return;
    }
    document.getElementById("setup-secs-msg").textContent = "Carregando...";
    document.getElementById("setup-secs-msg").style.display = "block";

    fetch(`/api/excecoes/source-position?companyId=${encodeURIComponent(cid)}&walletId=${encodeURIComponent(wid)}&date=${encodeURIComponent(date)}`)
      .then(r => r.json())
      .then(d => {
        _setupState.sourceSecurities = d.securities || [];
        // Initialise rule rows for any new uids; preserve existing.
        _setupState.sourceSecurities.forEach(s => {
          if (!_setupState.rules[s.unprocessedId]) {
            _setupState.rules[s.unprocessedId] = {
              selected: false,
              addToWalletId: "",
              removeFromWalletId: "",
              caixa: false,
            };
          }
        });
        renderSetupSecurities();
      })
      .catch(() => {
        document.getElementById("setup-secs-msg").textContent = "Erro ao carregar posição.";
      });
  }

  function _walletOpts(selected) {
    // Allowed = source wallet + selected output wallets.
    const allowed = [];
    if (_setupState.sourceWalletId) allowed.push(_setupState.sourceWalletId);
    _setupState.outputWalletIds.forEach(w => allowed.push(w));
    const byId = Object.fromEntries(_setupState.walletsForCompany.map(w => [w.id, w]));
    let html = '<option value="">--</option>';
    allowed.forEach(wid => {
      const w = byId[wid] || {id: wid, name: wid};
      html += `<option value="${escHtml(wid)}" ${selected === wid ? "selected" : ""}>${escHtml(w.name)}</option>`;
    });
    return html;
  }

  function renderSetupSecurities() {
    const tbody = document.getElementById("setup-secs-tbody");
    const msg   = document.getElementById("setup-secs-msg");
    const secs = _setupState.sourceSecurities;
    if (!secs.length) {
      tbody.innerHTML = "";
      msg.textContent = "Nenhum ativo encontrado para esta carteira/data.";
      msg.style.display = "block";
      return;
    }
    msg.style.display = "none";
    tbody.innerHTML = secs.map(s => {
      const r = _setupState.rules[s.unprocessedId] || {};
      const disabled = r.selected ? "" : "disabled";
      const rowCls = r.selected ? "" : "opacity-60";
      return `<tr class="border-t border-gray-100 ${rowCls}">
        <td class="px-2 py-2 text-center">
          <input type="checkbox" ${r.selected ? "checked" : ""}
            onchange="toggleRule('${escHtml(s.unprocessedId)}', this.checked)" />
        </td>
        <td class="px-2 py-2 font-mono text-[11px] text-gray-700">${escHtml(s.unprocessedId)}</td>
        <td class="px-2 py-2 text-right">${fmtNum(s.quantity)}</td>
        <td class="px-2 py-2 text-right">${fmtNum(s.pu)}</td>
        <td class="px-2 py-2 text-right">${fmtMoney(s.balance)}</td>
        <td class="px-2 py-2">
          <select ${disabled} onchange="updateRule('${escHtml(s.unprocessedId)}', 'addToWalletId', this.value)"
            class="w-full border rounded px-1 py-0.5 text-[11px] bg-white">
            ${_walletOpts(r.addToWalletId)}
          </select>
        </td>
        <td class="px-2 py-2">
          <select ${disabled} onchange="updateRule('${escHtml(s.unprocessedId)}', 'removeFromWalletId', this.value)"
            class="w-full border rounded px-1 py-0.5 text-[11px] bg-white">
            ${_walletOpts(r.removeFromWalletId)}
          </select>
        </td>
        <td class="px-2 py-2 text-center">
          <input type="checkbox" ${r.caixa ? "checked" : ""} ${disabled}
            onchange="updateRule('${escHtml(s.unprocessedId)}', 'caixa', this.checked)" />
        </td>
      </tr>`;
    }).join("");
  }

  function toggleRule(uid, selected) {
    const r = _setupState.rules[uid] || {selected: false, addToWalletId: "", removeFromWalletId: "", caixa: false};
    r.selected = selected;
    _setupState.rules[uid] = r;
    renderSetupSecurities();
  }

  function updateRule(uid, field, value) {
    const r = _setupState.rules[uid] || {selected: true, addToWalletId: "", removeFromWalletId: "", caixa: false};
    r[field] = value;
    _setupState.rules[uid] = r;
  }

  function saveException() {
    const name = document.getElementById("setup-name").value.trim();
    const cid  = _setupState.companyId;
    if (!cid)               { alert("Selecione uma empresa."); return; }
    if (!name)              { alert("Informe um nome."); return; }
    if (!_setupState.sourceWalletId) { alert("Selecione a carteira de origem."); return; }
    if (!_setupState.outputWalletIds.length) { alert("Selecione ao menos uma carteira de saída."); return; }

    const rules = [];
    Object.entries(_setupState.rules).forEach(([uid, r]) => {
      if (!r.selected) return;
      if (!r.addToWalletId && !r.removeFromWalletId) return;
      rules.push({
        unprocessedId:      uid,
        addToWalletId:      r.addToWalletId || null,
        removeFromWalletId: r.removeFromWalletId || null,
        caixa:              !!r.caixa,
      });
    });
    if (!rules.length) {
      alert("Selecione ao menos um ativo e configure adicionar/remover.");
      return;
    }

    const body = {
      companyId:       cid,
      name,
      sourceWalletId:  _setupState.sourceWalletId,
      outputWalletIds: _setupState.outputWalletIds,
      rules,
    };
    const url    = _setupState.editingId
      ? `/api/excecoes/${encodeURIComponent(_setupState.editingId)}`
      : "/api/excecoes";
    const method = _setupState.editingId ? "PUT" : "POST";

    document.getElementById("setup-status").textContent = "Salvando...";
    fetch(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .then(({ok, d}) => {
        if (!ok || d.error) {
          document.getElementById("setup-status").textContent = "Erro: " + (d.error || "falha");
          return;
        }
        closeSetupModal();
        loadList();
      })
      .catch(() => {
        document.getElementById("setup-status").textContent = "Erro de rede.";
      });
  }

