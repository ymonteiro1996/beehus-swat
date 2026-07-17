/* Precificação — empresa/data, modal de wallet-security e seleção.
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  function loadCompanies() {
    fetch("/api/precificacao/companies")
      .then(r => r.json())
      .then(companies => {
        const sel = document.getElementById("company-select");
        sel.innerHTML = '<option value="">— selecione —</option>' +
          companies.map(c => `<option value="${escHtml(c.id)}">${escHtml(c.name)}</option>`).join("");
      });
  }

  function onCompanyChange() {
    const cid  = document.getElementById("company-select").value;
    const wSel = document.getElementById("wallet-select");
    if (!cid) { wSel.innerHTML = '<option value="">— selecione —</option>'; return; }
    wSel.innerHTML = '<option value="">Carregando...</option>';
    fetch(`/api/precificacao/wallets?companyId=${encodeURIComponent(cid)}`)
      .then(r => r.json())
      .then(wallets => {
        wSel.innerHTML = wallets.length
          ? wallets.map(w => `<option value="${escHtml(w.id)}">${escHtml(w.name)}</option>`).join("")
          : '<option value="">Nenhuma carteira</option>';
        fetchLatestDate();
      });
  }

  // Fluxo data-driven: o usuário informa a data e clica Atualizar — nada é
  // pré-puxado do servidor (a rota latest-position-date foi removida).
  function fetchLatestDate() {
    document.getElementById("position-date-input").value = "";
  }

  // Listen for wallet dropdown change
  document.getElementById("wallet-select").addEventListener("change", fetchLatestDate);

  // ── Wallet Securities Modal ────────────────────────────────────────────────
  function openWalletSecModal() {
    const wSel        = document.getElementById("wallet-select");
    const walletId    = wSel.value;
    const posDateVal  = document.getElementById("position-date-input").value;
    if (!walletId) { alert("Selecione uma carteira."); return; }
    const walletName = wSel.options[wSel.selectedIndex]?.text || "";
    document.getElementById("wallet-id-label").textContent = walletId;
    document.getElementById("wsm-subtitle").textContent = walletName + (posDateVal ? ` · ${posDateVal}` : "");
    document.getElementById("wsm-body").innerHTML = '<p class="text-xs text-gray-400 italic">Carregando...</p>';
    document.getElementById("wallet-sec-modal").classList.remove("hidden");

    let url = `/api/precificacao/wallet-securities?walletId=${encodeURIComponent(walletId)}`;
    if (posDateVal) url += `&positionDate=${encodeURIComponent(posDateVal)}`;
    fetch(url)
      .then(r => r.json())
      .then(data => renderWalletSecModal(data, walletId, walletName))
      .catch(() => {
        document.getElementById("wsm-body").innerHTML = '<p class="text-xs text-red-400">Erro ao carregar ativos.</p>';
      });
  }

  function closeWalletSecModal() {
    document.getElementById("wallet-sec-modal").classList.add("hidden");
  }

  function renderWalletSecModal(data, walletId, walletName) {
    const body = document.getElementById("wsm-body");
    if (data.error) { body.innerHTML = `<p class="text-xs text-red-400">${escHtml(data.error)}</p>`; return; }
    const secs = data.securities || [];
    if (data.positionDate) {
      document.getElementById("wsm-subtitle").textContent += ` · posição ${data.positionDate}`;
    }
    if (!secs.length) {
      body.innerHTML = '<p class="text-xs text-gray-400 italic">Nenhum ativo nesta carteira.</p>';
      return;
    }
    body.innerHTML = `
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="text-[10px] uppercase text-gray-400 border-b">
            <th class="text-left py-2 pr-3 font-normal">Ativo</th>
            <th class="text-left py-2 pr-3 font-normal">Tipo Precif.</th>
            <th class="text-right py-2 pr-3 font-normal">Quantidade</th>
            <th class="text-right py-2 pr-3 font-normal">PU</th>
            <th class="text-right py-2 font-normal">Saldo (R$)</th>
          </tr>
        </thead>
        <tbody>
          ${secs.map(s => `
            <tr class="border-t border-gray-100 hover:bg-orange-50 cursor-pointer"
                onclick='selectSecurity(${JSON.stringify({
                  securityId:    s.securityId,
                  walletId:      walletId,
                  walletName:    walletName,
                  pricingType:   s.pricingType || "",
                  posQuantity:   s.quantity,
                  posPU:         s.pu,
                  positionDate:  data.positionDate || "",
                }).replace(/'/g, "&#39;")})'>
              <td class="py-2 pr-3">
                <p class="font-medium text-gray-800">${escHtml(s.beehusName || s.securityId)}</p>
                <p class="text-[10px] text-gray-400 font-mono">${escHtml(s.mainId || s.securityId)}</p>
              </td>
              <td class="py-2 pr-3 text-gray-500">${escHtml(s.pricingType || "—")}</td>
              <td class="py-2 pr-3 text-right font-mono text-gray-600">${s.quantity != null ? fmtNum(s.quantity, 2) : "—"}</td>
              <td class="py-2 pr-3 text-right font-mono text-gray-600">${s.pu != null ? fmtNum(s.pu, 8) : "—"}</td>
              <td class="py-2 text-right font-mono text-gray-600">
                ${s.balance != null ? s.balance.toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2}) : "—"}
              </td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  }

  // ── Select Security → fetch detail + show panel ────────────────────────────
  function selectSecurity(posData) {
    closeWalletSecModal();
    const { securityId, walletId, walletName, pricingType, posQuantity, posPU, positionDate } = posData;

    let secUrl = `/api/precificacao/security/${encodeURIComponent(securityId)}?walletId=${encodeURIComponent(walletId)}`;
    if (positionDate) secUrl += `&positionDate=${encodeURIComponent(positionDate)}`;
    fetch(secUrl)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => {
        if (data.error) { console.error(data.error); return; }
        _selectedSec = { ...data, walletId, walletName, positionDate: positionDate || "" };
        renderDetailPanel(data);
      })
      .catch(err => console.error("selectSecurity failed:", err));
  }

