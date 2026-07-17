/* Painel de Controle — estado, consts, tabela principal e cabeçalho.
   Escopo global; usa consts do bootstrap; ordem importa. */
  let _currentDate = null;
  let _currentType = null;
  let _currentCompanyId = null;
  let _modalIssues = [];
  let _mappingRows = [];           // cached rows for filtering
  let _c3Rows     = [];           // cached rows for C3 export
  let _gridRows   = [];           // last /api/controlpanel/rows response — feeds o Quadro da Esteira
  let _gridTxnByCompany = {};     // companyId -> contagem de TXN não identificada (chega depois, via /txn-counts)

  // Extras-column keys backed by /api/controlpanel/cell-detail. Kept in
  // sync with `_CELL_DETAIL_COLS` in pages/controlpanel.py — when a key
  // is in this set and the cell's `total > 0`, the cell becomes clickable
  // and opens the drill-down modal.
  const CELL_DETAIL_COLUMNS = new Set(["processed", "nav_wallet", "nav_grouping", "published"]);
  const CELL_DETAIL_LABELS = {
    processed:    "Posições Processadas",
    nav_wallet:   "NAV Wallet",
    nav_grouping: "NAV Grouping",
    published:    "Published",
  };
  const CELL_DETAIL_LEVEL = {
    processed:    "wallet",
    nav_wallet:   "wallet",
    nav_grouping: "grouping",
    published:    "grouping",
  };
  let _cellDetailData = null;      // last response from /cell-detail (for re-filtering)

  // Contagem de colunas (vinda do backend) usada para montar linhas "vazias"
  // quando uma empresa só tem transações pendentes e é anexada via /txn-counts.

  // ── Coluna TXN (transações não identificadas) — carregada à parte ──────────
  // O endpoint G (transactions) é o mais caro do /rows (~60% do tempo medido),
  // então a grade renderiza primeiro com E + /results e a coluna TXN é preenchida
  // depois por /api/controlpanel/txn-counts. Mantém o conjunto de empresas
  // correto: empresas que só têm transações pendentes (ausentes da grade inicial)
  // são ANEXADAS quando esta rota retorna.
  function _txnCellHtml(companyId, companyName, tx) {
    // tx === null → estado "carregando" ("…"); senão {count, label, cls}.
    // border-r sempre presente: TXN é a "estação" Reconciliação inteira (1
    // coluna só), separada do Processamento/NAV que vem depois.
    const data = `data-txn-company="${_escHtml(companyId)}" data-txn-name="${_escHtml(companyName)}"`;
    if (!tx) {
      return `<td class="px-1 py-1.5 text-center text-gray-400 col-extras-first border-r-2 border-gray-200 js-txn-cell" ${data}><span class="opacity-40">…</span></td>`;
    }
    if (tx.count > 0) {
      return `<td class="px-1 py-1.5 text-center ${tx.cls} col-extras-first border-r-2 border-gray-200 cursor-pointer hover:opacity-75 js-open-txn js-txn-cell" ${data}
                data-company-id="${_escHtml(companyId)}" data-date="${_escHtml(_currentDate || '')}"
                title="Abrir Identificar Transações para ${_escHtml(companyName)} em ${_escHtml(_currentDate || '')}">${_escHtml(tx.label)}</td>`;
    }
    return `<td class="px-1 py-1.5 text-center ${tx.cls} col-extras-first border-r-2 border-gray-200 js-txn-cell" ${data}>${_escHtml(tx.label)}</td>`;
  }

  function _emptyRowWithTxn(companyId, companyName, tx) {
    const issueCells = Array.from({length: _N_ISSUE_TYPES}, (_, i) => {
      const div = (i === 1 || i === _N_ISSUE_TYPES - 1) ? " border-r-2 border-gray-200" : "";
      return `<td class="px-1 py-1.5 text-center text-gray-400${div}">—</td>`;
    }).join("");
    const extraCells = Array.from({length: _N_EXTRA_COLS}, (_, i) => {
      // GAP é sempre a 3ª extra (índice 2) — mesmo corte usado no resto.
      const div = i === 2 ? " border-r-2 border-gray-200" : "";
      return `<td class="px-1 py-1.5 text-center text-gray-400${i === 0 ? ' col-extras-first' : ''}${div}">—</td>`;
    }).join("");
    return `<tr class="border-t border-gray-100 hover:bg-gray-50" data-company="${_escHtml(companyName.toLowerCase())}">
      <td class="sticky left-0 bg-white px-2 py-1.5 font-medium text-gray-700 z-10 truncate" title="${_escHtml(companyName)}">${_escHtml(companyName)}</td>
      ${issueCells}
      ${_txnCellHtml(companyId, companyName, tx)}
      ${extraCells}
    </tr>`;
  }

  function _loadTxnCounts(date) {
    fetch(`/api/controlpanel/txn-counts?date=${encodeURIComponent(date)}`)
      .then(r => r.json())
      .then(({ items }) => {
        if (date !== _currentDate) return;   // usuário trocou de data — descarta
        const byId = {};
        (items || []).forEach(it => { byId[it.companyId] = it; });
        // 1) Atualiza as células TXN das linhas já renderizadas.
        document.querySelectorAll("#tbody .js-txn-cell").forEach(cell => {
          const cid  = cell.dataset.txnCompany;
          const name = cell.dataset.txnName || cid;
          const it   = byId[cid];
          const tx   = it ? it.txn : {count: 0, label: "—", cls: "text-gray-400"};
          cell.outerHTML = _txnCellHtml(cid, name, tx);
          delete byId[cid];                  // consumida
        });
        // 2) Empresas que só têm transações pendentes (não vieram do /rows) →
        //    anexa linhas "vazias" com a coluna TXN preenchida.
        const leftover = Object.values(byId);
        if (leftover.length) {
          leftover.sort((a, b) => a.company.localeCompare(b.company));
          const tbody = document.getElementById("tbody");
          leftover.forEach(it =>
            tbody.insertAdjacentHTML("beforeend",
              _emptyRowWithTxn(it.companyId, it.company, it.txn)));
          filterRows();
        }
        // TXN chega depois do resto — só agora dá pra saber com certeza quem
        // está 100% liberado (linha verde) e o total de TXN pendente (totais).
        (items || []).forEach(it => { _gridTxnByCompany[it.companyId] = it.txn?.count || 0; });
        _markClearRows();
        _renderTotalsRow();
      })
      .catch(() => {/* TXN é best-effort; silencia para não derrubar a grade */});
  }

  // Reaproveita as mesmas etapas/leitura de pendência do Quadro da Esteira
  // (_ESTEIRA_STAGES/_esteiraPending, definidos mais abaixo — ok referenciar
  // aqui, closures só resolvem no momento da CHAMADA, não da declaração) pra
  // trazer a mesma inteligência de "gargalo" pra dentro da grade principal,
  // sem precisar abrir o Kanban.
  function _firstBlockingStage(r) {
    for (const stage of _ESTEIRA_STAGES) {
      if (_esteiraPending(r, stage)) return stage;
    }
    return null;
  }

  function _bottleneckBadge(r) {
    const stage = _firstBlockingStage(r);
    if (!stage) return "";
    return `<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-500 mr-1.5 flex-shrink-0 align-middle" title="Travado em: ${_escHtml(stage.label)}"></span>`;
  }

  // Zero pendência em toda a esteira (issues + extras + TXN) → "andon verde"
  // na linha inteira, não só nas colunas individuais.
  function _isRowClear(r) {
    if (!r) return false;
    if ((r.cells || []).some(c => c.count > 0)) return false;
    for (const e of (r.extras || [])) {
      if (e.total == null) { if (e.count > 0) return false; }        // GAP
      else if (e.total > 0 && e.count < e.total) return false;        // ratio parcial
    }
    if (_gridTxnByCompany[r.companyId] > 0) return false;
    return true;
  }

  function _markClearRows() {
    document.querySelectorAll("#tbody tr[data-company-id]").forEach(tr => {
      const r = _gridRows.find(row => row.companyId === tr.dataset.companyId);
      tr.classList.toggle("row-clear", _isRowClear(r));
    });
  }

  // Linha de totais (visão de frota) — soma cada coluna cruzando todas as
  // empresas visíveis. Fica dentro do <thead> (mesmo bloco sticky do
  // cabeçalho), então continua visível rolando a grade pra baixo.
  function _renderTotalsRow() {
    const row = document.getElementById("totals-row");
    if (!row || !_gridRows.length) return;

    const issueTds = Array.from({length: _N_ISSUE_TYPES}, (_, i) => {
      const total = _gridRows.reduce((s, r) => s + (r.cells[i]?.count || 0), 0);
      const div = (i === 1 || i === _N_ISSUE_TYPES - 1) ? " border-r-2 border-gray-300" : "";
      return `<td class="px-1 py-1.5 text-center${div}">${total || "—"}</td>`;
    }).join("");

    const txnTotal = Object.values(_gridTxnByCompany).reduce((a, b) => a + b, 0);
    const txnTd = `<td class="px-1 py-1.5 text-center col-extras-first border-r-2 border-gray-300">${txnTotal || "—"}</td>`;

    const extraTds = Array.from({length: _N_EXTRA_COLS}, (_, i) => {
      const sample = _gridRows.find(r => r.extras && r.extras[i]);
      const isCount = sample && sample.extras[i].total == null; // GAP
      const sumCount = _gridRows.reduce((s, r) => s + (r.extras[i]?.count || 0), 0);
      const sep = i === 0 ? " col-extras-first" : "";
      const div = (sample && sample.extras[i].total == null) ? " border-r-2 border-gray-300" : "";
      if (isCount) return `<td class="px-1 py-1.5 text-center${sep}${div}">${sumCount || "—"}</td>`;
      const sumTotal = _gridRows.reduce((s, r) => s + (r.extras[i]?.total || 0), 0);
      return `<td class="px-1 py-1.5 text-center${sep}${div}">${sumTotal ? `${sumCount}/${sumTotal}` : "—"}</td>`;
    }).join("");

    row.innerHTML = `
      <td class="sticky left-0 bg-gray-100 z-10 px-2 py-1.5 text-left">Totais</td>
      ${issueTds}${txnTd}${extraTds}`;
  }

  function selectDate(date) {
    _currentDate = date;
    _gridTxnByCompany = {};   // TXN é por data — descarta contagens da data anterior

    // highlight active card
    document.querySelectorAll(".date-card").forEach(c =>
      c.classList.toggle("active", c.dataset.date === date)
    );

    document.getElementById("msg").textContent     = "Carregando...";
    document.getElementById("msg").style.display   = "block";
    document.getElementById("table").style.display = "none";

    const params = new URLSearchParams({date});
    const th = document.getElementById("threshold-input")?.value;
    if (th !== undefined && th !== "") params.set("threshold", th);
    fetch(`/api/controlpanel/rows?${params}`).then(r => r.json()).then(({ rows }) => {
      _gridRows = rows;
      document.getElementById("tbody").innerHTML = rows.map(r =>
        `<tr class="border-t border-gray-100 hover:bg-gray-50" data-company="${_escHtml(r.company.toLowerCase())}" data-company-id="${_escHtml(r.companyId)}">
          <td class="sticky left-0 bg-white px-2 py-1.5 font-medium text-gray-700 z-10 truncate" title="${_escHtml(r.company)}">${_bottleneckBadge(r)}${_escHtml(r.company)}</td>
          ${r.cells.map((c, i) => {
            // Divisor de "estação da esteira" — depois da Posição (fim de
            // Pré-requisito) e depois da última (Preço para o dia, fim de
            // Processamento das Carteiras). Mesmos cortes do cabeçalho agrupado.
            const div = (i === 1 || i === r.cells.length - 1) ? " border-r-2 border-gray-200" : "";
            return c.count > 0
              ? `<td class="px-1 py-1.5 text-center ${c.cls}${div} cursor-pointer hover:opacity-75 js-open-modal"
                   data-company-id="${_escHtml(r.companyId)}"
                   data-company-name="${_escHtml(r.company)}"
                   data-issue-type="${_escHtml(c.type)}">${_escHtml(c.label)}</td>`
              : `<td class="px-1 py-1.5 text-center ${c.cls}${div}">${_escHtml(c.label)}</td>`;
          }).join("")}
          ${/* TXN: placeholder "…" — preenchido depois por _loadTxnCounts (G é
                o endpoint mais caro, carregado após a tela renderizar). */
            _txnCellHtml(r.companyId, r.company, null)}
          ${(r.extras || []).map((e, i) => {
            const sep = i === 0 ? " col-extras-first" : "";
            // Divisor de estação depois do GAP — fim de "Processamento / NAV",
            // antes de "Publicação" (NAV Grouping/Published). Mesmo corte do
            // cabeçalho agrupado.
            const div = e.key === "gap" ? " border-r-2 border-gray-200" : "";
            // Wallet/grouping progress columns become clickable when the
            // denominator is known (total > 0). The "—" placeholder cells
            // (total = 0, no work to inspect) stay inert. Click opens the
            // drill-down modal showing each grouping/wallet's status.
            if (CELL_DETAIL_COLUMNS.has(e.key) && e.total && e.total > 0) {
              return `<td class="px-1 py-1.5 text-center ${e.cls}${sep}${div} cursor-pointer hover:opacity-75 js-open-cell"
                        data-company-id="${_escHtml(r.companyId)}"
                        data-company-name="${_escHtml(r.company)}"
                        data-column="${_escHtml(e.key)}"
                        title="Ver detalhes de ${_escHtml(r.company)} em ${_escHtml(_currentDate || '')}">${_escHtml(e.label)}</td>`;
            }
            return `<td class="px-1 py-1.5 text-center ${e.cls}${sep}${div}">${_escHtml(e.label)}</td>`;
          }).join("")}
        </tr>`).join("");

      document.getElementById("msg").style.display   = "none";
      document.getElementById("table").style.display = "";
      filterRows();
      _markClearRows();   // 1ª leitura (TXN ainda não chegou — refinado abaixo)
      _renderTotalsRow();

      // Tela já visível com E + /results; agora busca a coluna TXN (endpoint G,
      // o mais caro) em segundo plano e preenche os "…".
      _loadTxnCounts(date);
    });
  }

  // ── GAP threshold input ──────────────────────────────────────────────────
  // Persisted in data/conciliacao_config.json so this page and /conciliacao
  // share a single source of truth. Saving auto-refreshes the table.
  const Threshold = {
    _saveTimer: null,
    save(rawValue) {
      // Clamp + sanitize before posting; the backend re-validates so this is
      // just a UX nicety to avoid spamming invalid PUTs while the user types.
      const v = parseFloat(rawValue);
      if (Number.isNaN(v) || v < 0 || v > 10) return;
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(async () => {
        try {
          const r = await fetch("/api/controlpanel/threshold", {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({diffThresholdPct: v}),
          });
          if (!r.ok) {
            const data = await r.json().catch(() => ({}));
            console.warn("threshold save failed:", data.error || r.status);
            return;
          }
          // Refresh the active date so the GAP column reflects the new threshold.
          if (_currentDate) selectDate(_currentDate);
        } catch (e) {
          console.warn("threshold save error:", e);
        }
      }, 250);
    },
  };

  // Delegated click handler: company names with apostrophes ("D'Or") would
  // break inline onclick='openModal("…")', and unescaped values are an XSS
  // path. Reading from data-* via dataset avoids both (HTML parser decodes
  // attributes, JS never re-parses them as strings).
  document.getElementById("tbody").addEventListener("click", e => {
    // Progress-column drill-down (Posições Processadas / NAV Wallet /
    // NAV Grouping / Published): open the cell-detail modal with the
    // grouping/wallet breakdown.
    const cellTd = e.target.closest("td.js-open-cell");
    if (cellTd) {
      openCellDetail(
        cellTd.dataset.companyId,
        cellTd.dataset.companyName,
        cellTd.dataset.column,
      );
      return;
    }
    // TXN drill-down: open Identificar Transações for this company+date.
    const txnTd = e.target.closest("td.js-open-txn");
    if (txnTd) {
      _openIdentifyTxn(txnTd.dataset.companyId, txnTd.dataset.date);
      return;
    }
    const td = e.target.closest("td.js-open-modal");
    if (!td) return;
    openModal(td.dataset.companyId, td.dataset.companyName, td.dataset.issueType);
  });

  // Header click → mesmo modal de detalhe de sempre, só que cruzando TODAS as
  // empresas visíveis nessa coluna (em vez de abrir empresa por empresa).
  // As 6 colunas de issue (Mapeamento/Classificação/Registro de Preço/Preço
  // p/ dia/Carteira/Posição) reusam o MESMO modal rico de sempre (openModal),
  // só que via openModalAllCompanies. As demais (TXN + colunas extra) usam o
  // modal simples de lista (openAllCompaniesModal) — não têm um "modal por
  // empresa" equivalente pra replicar.
  const _ISSUE_TYPE_KEYS = new Set([
    "missing_wallet", "missing_unprocessed_position", "security_unmapped",
    "security_missing_classification", "security_missing_price", "security_missing_history_price",
  ]);
  document.getElementById("table").querySelector("thead").addEventListener("click", e => {
    const th = e.target.closest("th[data-column]");
    if (!th || !_currentDate) return;
    const column = th.dataset.column, label = th.textContent.trim();
    if (_ISSUE_TYPE_KEYS.has(column)) openModalAllCompanies(column, label);
    else                              openAllCompaniesModal(column, label);
  });

  let _acmRows = [];
  let _acmColumn = "";
  let _acmOnlyGap = false;

  function _acmGapThreshold() {
    const raw = document.getElementById("threshold-input")?.value;
    const pct = parseFloat(raw);
    return (Number.isNaN(pct) ? 0.01 : pct) / 100;
  }

  function _acmIsGap(r) {
    if (r.detail === "pendente") return false;
    // Arredonda na mesma casa decimal exibida (2 casas do %) antes de comparar
    // com o threshold — sem isso, ruído de ponto flutuante do upstream (ex.
    // 0.00000002) passa como "diferente" mesmo quando a tela mostra 0.00% dos
    // dois lados, fazendo o toggle parecer que não filtra nada.
    const diffRounded = Math.round(Math.abs(r.returnDifference || 0) * 10000) / 10000;
    return diffRounded > _acmGapThreshold();
  }

  function _acmToggleOnlyGap(checked) {
    _acmOnlyGap = checked;
    _acmRenderRows();
  }

  function _acmRenderRows() {
    const body = document.getElementById("acm-body");
    const isNavWallet = _acmColumn === "nav_wallet";
    const rows = isNavWallet && _acmOnlyGap ? _acmRows.filter(_acmIsGap) : _acmRows;
    const _fmtPct = v => v == null ? "—" : `${(v * 100).toFixed(2)}%`;
    const toggle = isNavWallet ? `
      <label class="flex items-center gap-1.5 text-[11px] text-gray-600 mb-2 select-none">
        <input type="checkbox" ${_acmOnlyGap ? "checked" : ""} onchange="_acmToggleOnlyGap(this.checked)">
        Mostrar apenas carteiras com divergência
      </label>` : ``;
    if (!rows.length) {
      body.innerHTML = toggle + '<p class="text-xs text-gray-400 py-6 text-center">Nenhuma carteira nessa seleção.</p>';
      return;
    }
    body.innerHTML = toggle + `
      <table class="w-full text-xs">
        <thead><tr class="text-left text-gray-400 border-b">
          <th class="px-2 py-1.5">Empresa</th><th class="px-2 py-1.5">Carteira</th>
          ${isNavWallet
            ? `<th class="px-2 py-1.5">Status</th><th class="px-2 py-1.5">Rentab. NAV</th><th class="px-2 py-1.5">Rentab. Contribuição</th><th class="px-2 py-1.5">Diferença</th>`
            : `<th class="px-2 py-1.5">Detalhe</th>`}
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr class="border-b last:border-0">
              <td class="px-2 py-1.5 text-gray-700">${r.company}</td>
              <td class="px-2 py-1.5 text-gray-600">${r.walletName || "—"}</td>
              ${isNavWallet ? `
              <td class="px-2 py-1.5 ${r.detail === 'pendente' ? 'text-amber-600' : 'text-green-600'}">${r.detail}</td>
              <td class="px-2 py-1.5 font-mono text-gray-600">${_fmtPct(r.returnNavPerShare)}</td>
              <td class="px-2 py-1.5 font-mono text-gray-600">${_fmtPct(r.returnContribution)}</td>
              <td class="px-2 py-1.5 font-mono text-gray-600">${_fmtPct(r.returnDifference)}</td>`
              : `<td class="px-2 py-1.5 font-mono text-gray-500">${r.detail || ""}</td>`}
            </tr>`).join("")}
        </tbody>
      </table>`;
  }

  async function openAllCompaniesModal(column, label) {
    const modal = document.getElementById("all-companies-modal");
    const title = document.getElementById("acm-title");
    const body  = document.getElementById("acm-body");
    title.textContent = label;
    body.innerHTML = '<p class="text-xs text-gray-400 py-6 text-center">Carregando…</p>';
    modal.style.display = "flex";
    _acmColumn = column;
    _acmOnlyGap = false;
    document.getElementById("acm-action-btn").style.display = column === "txn" ? "" : "none";
    try {
      const res = await fetch(`/api/controlpanel/detail-all?date=${encodeURIComponent(_currentDate)}&column=${encodeURIComponent(column)}`);
      const data = await res.json();
      if (data.error) {
        body.innerHTML = `<p class="text-xs text-red-500 py-6 text-center">${data.error}</p>`;
        return;
      }
      _acmRows = data.rows || [];
      if (!_acmRows.length) {
        body.innerHTML = '<p class="text-xs text-gray-400 py-6 text-center">Nenhuma pendência — todas as empresas em dia. 🎉</p>';
        return;
      }
      _acmRenderRows();
    } catch (e) {
      body.innerHTML = `<p class="text-xs text-red-500 py-6 text-center">Erro: ${e}</p>`;
    }
  }

  function closeAllCompaniesModal() {
    document.getElementById("all-companies-modal").style.display = "none";
  }

  // ── Processar Transações (POST /data-science/heuristics) ────────────────
  // Botão dentro do modal de TXN — pede entidade + data (a empresa vem junto,
  // resolvida a partir da entidade escolhida em /api/controlpanel/entities,
  // já que uma entidade pertence a uma única empresa).
  let _ptxEntities = [];

  let _ptxAllRunning   = false;
  let _ptxAllCancelled = false;

  async function openProcessTxnModal() {
    const modal  = document.getElementById("process-txn-modal");
    const select = document.getElementById("ptx-entity");
    const dateEl = document.getElementById("ptx-date");
    const msg    = document.getElementById("ptx-msg");
    msg.textContent = "";
    dateEl.value = _currentDate || "";
    if (!_ptxAllRunning) document.getElementById("ptx-all-progress").innerHTML = "";
    select.innerHTML = `<option value="">Carregando…</option>`;
    modal.style.display = "flex";
    try {
      const r = await fetch("/api/controlpanel/entities");
      const data = await r.json();
      _ptxEntities = data.entities || [];
      if (!_ptxEntities.length) {
        select.innerHTML = `<option value="">Nenhuma entidade visível</option>`;
        return;
      }
      const byCompany = {};
      _ptxEntities.forEach(e => (byCompany[e.companyName] ??= []).push(e));
      select.innerHTML = Object.keys(byCompany).sort().map(companyName => `
        <optgroup label="${_escHtml(companyName)}">
          ${byCompany[companyName].map(e =>
            `<option value="${_escHtml(e.id)}" data-company-id="${_escHtml(e.companyId)}">${_escHtml(e.name)}</option>`
          ).join("")}
        </optgroup>`).join("");
    } catch (e) {
      select.innerHTML = `<option value="">Erro ao carregar entidades</option>`;
    }
  }

  function closeProcessTxnModal() {
    document.getElementById("process-txn-modal").style.display = "none";
  }

  async function _runProcessTxn() {
    const select = document.getElementById("ptx-entity");
    const dateEl = document.getElementById("ptx-date");
    const msg    = document.getElementById("ptx-msg");
    const opt    = select.selectedOptions[0];
    const entityId  = select.value;
    const companyId = opt ? opt.dataset.companyId : "";
    const date      = dateEl.value;

    if (!entityId || !companyId) { msg.innerHTML = `<span class="text-red-500">Selecione uma entidade.</span>`; return; }
    if (!date)                   { msg.innerHTML = `<span class="text-red-500">Selecione uma data.</span>`; return; }
    if (!confirm(
      `Processar transações da entidade "${opt.textContent}" em ${date}?\n\n` +
      `Esta ação chama POST na API Beehus (heurística de identificação) e não é reversível por esta tela.`
    )) return;

    const btn = document.getElementById("ptx-run-btn");
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Processando…";
    msg.textContent = "";

    try {
      const r = await api("POST", "/api/controlpanel/process-transactions", { entityId, companyId, date });
      const data = r.body || {};
      if (r.status === 401) {
        msg.innerHTML = `<span class="text-red-500">Token Beehus não está carregado ou foi rejeitado. Clique no badge "Token" no topo da página.</span>`;
        return;
      }
      if (!r.ok) {
        msg.innerHTML = `<span class="text-red-500">Falha: ${_escHtml(data.error || `HTTP ${r.status}`)}</span>`;
        return;
      }
      msg.innerHTML = `<span class="text-green-600">Processamento disparado com sucesso.</span>`;
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
    }
  }

  function _cancelProcessTxnAll() {
    _ptxAllCancelled = true;
  }

  async function _runProcessTxnAll() {
    if (_ptxAllRunning) return;
    const date = document.getElementById("ptx-date").value;
    if (!date) { alert("Selecione uma data primeiro."); return; }
    if (!_ptxEntities.length) { alert("Nenhuma entidade visível pra processar."); return; }

    const companyCount = new Set(_ptxEntities.map(e => e.companyId)).size;
    if (!confirm(
      `Processar transações de TODAS as ${_ptxEntities.length} entidade(s) visíveis ` +
      `(${companyCount} empresa(s)) em ${date}, uma de cada vez (não em paralelo)?\n\n` +
      `Pode levar um tempo — cada entidade só começa depois que a anterior termina. ` +
      `Chama POST na API Beehus e não é reversível por esta tela. Dá pra cancelar no meio.`
    )) return;

    _ptxAllRunning   = true;
    _ptxAllCancelled = false;
    const runBtn       = document.getElementById("ptx-run-all-btn");
    const cancelBtn    = document.getElementById("ptx-cancel-all-btn");
    const runSingleBtn = document.getElementById("ptx-run-btn");
    const progress      = document.getElementById("ptx-all-progress");
    runBtn.disabled       = true;
    runSingleBtn.disabled = true;
    cancelBtn.style.display = "";

    const total   = _ptxEntities.length;
    const results = [];

    const render = (currentLabel) => {
      const okCount   = results.filter(r => r.ok).length;
      const failCount = results.length - okCount;
      const header = currentLabel
        ? `<p class="text-gray-600 mb-2 flex items-center gap-1.5">
             <svg class="w-3.5 h-3.5 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
             <span>Processando ${results.length + 1} de ${total}: ${_escHtml(currentLabel)}…</span>
           </p>`
        : `<p class="text-gray-700 mb-2 font-medium">
             ${_ptxAllCancelled ? "Cancelado" : "Concluído"}: ${okCount} de ${results.length} com sucesso${failCount ? `, ${failCount} falha(s)` : ""}.
           </p>`;
      const list = results.slice().reverse().map(r => `
        <div class="flex items-start gap-1.5 py-0.5 ${r.ok ? "text-green-600" : "text-red-500"}">
          <span class="flex-shrink-0">${r.ok ? "✓" : "✗"}</span>
          <span class="truncate">${_escHtml(r.label)}${!r.ok && r.error ? ` — ${_escHtml(r.error)}` : ""}</span>
        </div>`).join("");
      progress.innerHTML = header +
        `<div class="max-h-40 overflow-y-auto border rounded p-2 bg-gray-50">${list || '<span class="text-gray-400">—</span>'}</div>`;
    };

    for (const entity of _ptxEntities) {
      if (_ptxAllCancelled) break;
      const label = `${entity.companyName} — ${entity.name}`;
      render(label);
      try {
        const r = await api("POST", "/api/controlpanel/process-transactions", {
          entityId: entity.id, companyId: entity.companyId, date,
        });
        const data = r.body || {};
        results.push(r.ok ? { ok: true, label } : { ok: false, label, error: data.error || `HTTP ${r.status}` });
      } catch (e) {
        results.push({ ok: false, label, error: String(e) });
      }
    }
    render(null);

    runBtn.disabled       = false;
    runSingleBtn.disabled = false;
    cancelBtn.style.display = "none";
    _ptxAllRunning = false;
  }

  // ── Processar Range de Datas ─────────────────────────────────────────────
  // As pendências só saem do pre-processing depois que a posição do dia é
  // processada — um dia ainda não tocado fica invisível pro resto da esteira
  // (Mapeamento/Classificação/etc. não mostram nada pra ele). Este fluxo
  // processa todas as empresas visíveis, dia por dia (dias úteis, sempre
  // sequencial), reaproveitando a mesma rota de data única — nunca dispara
  // pra empresa sem carteira pendente naquele dia específico.
  let _prangeRunning   = false;
  let _prangeCancelled = false;

  function openProcessRangeModal() {
    const modal = document.getElementById("process-range-modal");
    const start = document.getElementById("prange-start");
    const end   = document.getElementById("prange-end");
    if (!_prangeRunning) {
      document.getElementById("prange-progress").innerHTML = "";
      end.value   = _currentDate || "";
      start.value = _currentDate || "";
    }
    modal.style.display = "flex";
  }

  function closeProcessRangeModal() {
    document.getElementById("process-range-modal").style.display = "none";
  }

  function _cancelProcessRange() {
    _prangeCancelled = true;
  }

  // Dias úteis (seg-sex) entre as duas datas, inclusive, ordenados do mais
  // antigo pro mais recente — mesma aproximação (sem feriados) que
  // `get_biz_dates`/`biz_days_between` já usam no backend.
  function _businessDaysInRange(startIso, endIso) {
    const a = new Date(startIso + "T00:00:00");
    const b = new Date(endIso + "T00:00:00");
    if (isNaN(a) || isNaN(b)) return [];
    let lo = a <= b ? a : b;
    const hi = a <= b ? b : a;
    const out = [];
    for (let d = new Date(lo); d <= hi; d.setDate(d.getDate() + 1)) {
      const wd = d.getDay();
      if (wd !== 0 && wd !== 6) out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }

  async function _runProcessRange() {
    if (_prangeRunning) return;
    const startIso = document.getElementById("prange-start").value;
    const endIso   = document.getElementById("prange-end").value;
    if (!startIso || !endIso) { alert("Selecione as duas datas."); return; }
    const dates = _businessDaysInRange(startIso, endIso);
    if (!dates.length) { alert("Nenhum dia útil nesse range."); return; }

    if (!confirm(
      `Processar todas as empresas visíveis em ${dates.length} dia(s) útil(eis), ` +
      `de ${dates[0]} até ${dates[dates.length - 1]}, um dia de cada vez?\n\n` +
      `Cada dia só dispara pra empresas com carteira pendente naquele dia. ` +
      `Chama POST na API Beehus e não é reversível por esta tela. Dá pra cancelar no meio.`
    )) return;

    _prangeRunning   = true;
    _prangeCancelled = false;
    const runBtn    = document.getElementById("prange-run-btn");
    const cancelBtn = document.getElementById("prange-cancel-btn");
    const progress  = document.getElementById("prange-progress");
    runBtn.disabled = true;
    cancelBtn.style.display = "";

    const total   = dates.length;
    const results = [];

    const render = (currentDate) => {
      const header = currentDate
        ? `<p class="text-gray-600 mb-2 flex items-center gap-1.5">
             <svg class="w-3.5 h-3.5 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
             <span>Processando dia ${results.length + 1} de ${total}: ${currentDate}…</span>
           </p>`
        : `<p class="text-gray-700 mb-2 font-medium">${_prangeCancelled ? "Cancelado" : "Concluído"} — ${results.length} de ${total} dia(s).</p>`;
      const list = results.slice().reverse().map(r => `
        <div class="flex items-start gap-1.5 py-0.5 ${r.ok ? "text-gray-700" : "text-red-500"}">
          <span class="flex-shrink-0">${r.ok ? "✓" : "✗"}</span>
          <span class="truncate">${_escHtml(r.label)}</span>
        </div>`).join("");
      progress.innerHTML = header +
        `<div class="max-h-48 overflow-y-auto border rounded p-2 bg-gray-50">${list || '<span class="text-gray-400">—</span>'}</div>`;
    };

    for (const date of dates) {
      if (_prangeCancelled) break;
      render(date);
      try {
        const r = await api("POST", "/api/controlpanel/process-all-wallets", { date });
        const data = r.body || {};
        if (!r.ok) {
          results.push({ ok: false, label: `${date} — falha: ${_escHtml(data.error || `HTTP ${r.status}`)}` });
          continue;
        }
        const entries  = Object.entries(data.results || {});
        const okCount  = entries.filter(([, res]) => res && res.ok).length;
        const failCount = entries.length - okCount;
        const skipped  = data.skipped || 0;
        const label = entries.length
          ? `${date} — ${okCount} processada(s)${failCount ? `, ${failCount} falha(s)` : ""}${skipped ? `, ${skipped} sem pendência` : ""}`
          : `${date} — nenhuma empresa com carteira pendente`;
        results.push({ ok: failCount === 0, label });
      } catch (e) {
        results.push({ ok: false, label: `${date} — erro de rede: ${_escHtml(String(e))}` });
      }
    }
    render(null);

    runBtn.disabled = false;
    cancelBtn.style.display = "none";
    _prangeRunning = false;

    // Se a data em foco no grid está dentro do range processado, atualiza a
    // tela pra já mostrar o que apareceu.
    if (_currentDate && dates.includes(_currentDate)) selectDate(_currentDate);
  }

  // ── TXN → Identificar Transações drill-down ────────────────────────────
  // Opens the Identificar Transações tool INLINE (same iframe as the
  // "Identificar" chip, so the favorites bar stays visible — we do NOT navigate
  // the shell to another page) pre-filled with this company + date and the
  // "Não identificadas" filter, then runs the search so the listing matches the
  // counter the operator just clicked.
  function _openIdentifyTxn(companyId, date) {
    Funcoes.openIdentifyWith(companyId, date);
  }

  // ── Wallet-issues modal (Verificar por carteira) ────────────────────────
  // Clicking a company name opens this modal. View 1 (`#wi-picker`) lists the
  // company's wallets with a pending-issue count and a filter box; picking one
  // switches to View 2 (`#wi-issues`) with that wallet's issues on the active
  // date, grouped by issue type. Both views come from
  // /api/controlpanel/wallet-issues (single pre-processing call per company).
  let _wiCompanyId = null, _wiCompanyName = "", _wiWallets = null;

  async function openWalletIssues(companyId, companyName) {
    if (!_currentDate || !companyId) return;
    _wiCompanyId   = companyId;
    _wiCompanyName = companyName || companyId;
    _wiWallets     = null;

    document.getElementById("wallet-issues-modal").style.display = "flex";
    _wiShowPicker();
    document.getElementById("wi-title").textContent    = `Verificar por carteira — ${_wiCompanyName}`;
    document.getElementById("wi-subtitle").textContent = `${_wiCompanyName} • ${_currentDate}`;
    document.getElementById("wi-filter").value = "";

    const msg  = document.getElementById("wi-picker-msg");
    const list = document.getElementById("wi-picker-list");
    msg.style.display  = "";
    msg.textContent    = "Carregando...";
    list.style.display = "none";
    list.innerHTML     = "";

    try {
      const params = new URLSearchParams({companyId, date: _currentDate});
      const r = await fetch(`/api/controlpanel/wallet-issues?${params}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        msg.textContent = `Erro: ${err.error || r.status}`;
        return;
      }
      const data = await r.json();
      _wiWallets = data.wallets || [];
      _wiRenderPicker();
    } catch (e) {
      msg.textContent = "Erro de rede: " + e;
    }
  }

