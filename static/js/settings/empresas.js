/* Configurações — filtro de empresas e bootstrap (init).
   Escopo global compartilhado; ordem importa. */
  let _companies = [];

  function renderCompanies() {
    const el = document.getElementById('company-list');
    if (!_companies.length) {
      el.innerHTML = '<p class="text-xs text-gray-400 italic">Nenhuma empresa encontrada.</p>';
      return;
    }
    el.innerHTML = _companies.map(c => `
      <label class="flex items-center gap-2.5 cursor-pointer group">
        <input type="checkbox" data-id="${c.id}" ${c.visible ? 'checked' : ''}
          onchange="toggleCompany('${c.id}', this.checked)"
          class="w-4 h-4 rounded border-gray-300 text-blue-600 cursor-pointer"/>
        <span class="text-sm text-gray-700 group-hover:text-gray-900">${escHtml(c.name)}</span>
      </label>`).join('');
  }

  function toggleCompany(id, checked) {
    const co = _companies.find(c => c.id === id);
    if (co) co.visible = checked;
  }

  function selectAllCompanies(visible) {
    _companies.forEach(c => c.visible = visible);
    renderCompanies();
  }

  function loadCompanies() {
    fetch('/api/settings/companies').then(r => r.json()).then(({ companies }) => {
      _companies = companies;
      renderCompanies();
    });
  }

  // Save just the company filter — the legacy cargas/nav toggles
  // (only_daily_position / only_with_consumption) lost their UI; their
  // last-saved values are preserved in data/{nav_,}settings.json.
  function saveCompanyFilter() {
    const allVisible = _companies.every(c => c.visible);
    const companyFilter = allVisible ? [] : _companies.filter(c => c.visible).map(c => c.id);
    fetch('/api/settings/save', {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_filter: companyFilter }),
    }).then(() => alert('Filtro de empresas salvo.'));
  }

  document.addEventListener("keydown", e => { if (e.key === "Escape") history.back(); });

  // Boot
  IdentifyConfig.init();
  loadCompanies();
