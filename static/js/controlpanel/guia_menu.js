/* Painel — guia visual + barra de menu (toggle/fallback). */
/* ── Guia toggle + fallback de navegação ─────────────────────────────────────
   `toggleWorkflow` alterna `.collapsed` em `#wf-row` (oculta o guia visual) e
   espelha o estado em `#chip-wf-toggle` para girar o chevron. `goToWorkflow`
   permanece porque o fallback do Repetir (sem shell parent) ainda o usa para
   navegar para /repetir-posicoes preservando companyId/date — os antigos
   chips de pipeline que o chamavam foram removidos. */
function goToWorkflow(page, deep) {
  const params = new URLSearchParams();
  if (typeof _currentCompanyId === "string" && _currentCompanyId) params.set("companyId", _currentCompanyId);
  if (typeof _currentDate      === "string" && _currentDate)      params.set("date", _currentDate);
  const qs = params.toString();
  if (window.parent !== window) {
    window.parent.postMessage({ type: 'navigate', path: page, params: qs, deep: deep || '' }, '*');
  } else {
    const hash = deep ? `#${deep}` : '';
    window.location.href = qs ? `${page}?${qs}${hash}` : `${page}${hash}`;
  }
}
function toggleWorkflow() {
  const row    = document.getElementById('wf-row');
  const toggle = document.getElementById('chip-wf-toggle');
  const collapsed = row.classList.toggle('collapsed');
  if (toggle) {
    toggle.classList.toggle('collapsed', collapsed);
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

/* Toggle do menu bar (primeira fb-bar-row). Quando colapsa, força o #wf-row
   (guia) a expandir para que a barra nunca colapse a altura zero — o operador
   sempre vê o guia visual. O estado persiste em localStorage. */
const MENU_BAR_KEY = 'controlpanel.menuBarCollapsed';
function toggleMenuBar(forceCollapsed) {
  const bar     = document.getElementById('funcoes-bar');
  const wfRow   = document.getElementById('wf-row');
  const wfChip  = document.getElementById('chip-wf-toggle');
  const btn     = document.getElementById('menu-bar-toggle');
  if (!bar) return;
  const collapsed = typeof forceCollapsed === 'boolean'
    ? forceCollapsed
    : !bar.classList.contains('menu-collapsed');
  bar.classList.toggle('menu-collapsed', collapsed);
  if (collapsed && wfRow) {
    // Garante o guia visível para a barra não colapsar 100%.
    wfRow.classList.remove('collapsed');
    if (wfChip) {
      wfChip.classList.remove('collapsed');
      wfChip.setAttribute('aria-expanded', 'true');
    }
  }
  if (btn) {
    btn.setAttribute('aria-label', collapsed ? 'Mostrar barra de menu' : 'Ocultar barra de menu');
    btn.title = collapsed ? 'Mostrar barra de menu' : 'Ocultar barra de menu';
  }
  try { localStorage.setItem(MENU_BAR_KEY, collapsed ? '1' : '0'); } catch (e) {}
}
// Restaura o estado escolhido pelo operador na última visita. Default
// é colapsado: na primeira visita (sem chave) o menu nasce oculto e o
// guia aparece expandido. Só permanece visível se o operador tiver
// explicitamente reaberto a barra antes (chave === '0').
try {
  if (localStorage.getItem(MENU_BAR_KEY) !== '0') toggleMenuBar(true);
} catch (e) { toggleMenuBar(true); }
