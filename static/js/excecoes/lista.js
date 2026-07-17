/* Exceções — carga e render da lista de exceções.
   Escopo global compartilhado; ordem importa. */
  function loadList() {
    fetch("/api/excecoes")
      .then(r => r.json())
      .then(({ exceptions }) => {
        _exceptions = exceptions || [];
        renderList();
      })
      .catch(() => {
        document.getElementById("exc-msg").textContent = "Erro ao carregar exceções.";
      });
  }

  function renderList() {
    const tbody = document.getElementById("exc-tbody");
    const msg   = document.getElementById("exc-msg");
    if (!_exceptions.length) {
      tbody.innerHTML = "";
      msg.textContent = "Nenhuma exceção configurada.";
      msg.style.display = "block";
      return;
    }
    msg.style.display = "none";
    tbody.innerHTML = _exceptions.map(e => {
      const last = e.lastApplied
        ? `${escHtml(e.lastApplied.date || "")} <span class="text-[10px] text-gray-400">(${escHtml((e.lastApplied.at || "").slice(0,10))})</span>`
        : '<span class="text-gray-300">--</span>';
      return `<tr class="border-t border-gray-100 hover:bg-gray-50">
        <td class="px-3 py-2 text-gray-700">${escHtml(e.companyName)}</td>
        <td class="px-3 py-2 font-medium text-gray-800">${escHtml(e.name)}</td>
        <td class="px-3 py-2 text-gray-700">${escHtml(e.sourceWalletName)}</td>
        <td class="px-3 py-2 text-gray-700">${(e.outputWalletNames || []).map(escHtml).join(", ")}</td>
        <td class="px-3 py-2 text-right">${e.ruleCount}</td>
        <td class="px-3 py-2 text-gray-700">${last}</td>
        <td class="px-3 py-2 text-center">
          <button onclick="openApplyModal('${escHtml(e.id)}', '${escHtml(e.companyId)}')"
            class="bg-emerald-600 hover:bg-emerald-700 text-white rounded px-2 py-1 text-[11px] font-semibold mr-1">Aplicar</button>
          <button onclick="editException('${escHtml(e.id)}', '${escHtml(e.companyId)}')"
            class="bg-blue-600 hover:bg-blue-700 text-white rounded px-2 py-1 text-[11px]">Editar</button>
          <button onclick="deleteException('${escHtml(e.id)}', '${escHtml(e.companyId)}')"
            class="bg-red-600 hover:bg-red-700 text-white rounded px-2 py-1 text-[11px]">Excluir</button>
        </td>
      </tr>`;
    }).join("");
  }

  function deleteException(id, companyId) {
    if (!confirm("Excluir esta exceção?")) return;
    fetch(`/api/excecoes/${encodeURIComponent(id)}?companyId=${encodeURIComponent(companyId)}`, {method: "DELETE"})
      .then(r => r.json())
      .then(d => {
        if (d.error) { alert(d.error); return; }
        loadList();
      });
  }

  // ── Setup wizard ──────────────────────────────────────────────────────────
