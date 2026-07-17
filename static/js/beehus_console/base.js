/* Beehus Console — guards de modal (ESC/click-outside) e toggleCard.
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
/* ════════════════════════════════════════════════════════════════════════════
   ESC closes any open modal — log, token, confirm-*, etc. Lighter than wiring
   each modal individually, and matches the existing click-outside behavior.
═════════════════════════════════════════════════════════════════════════════ */
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
});

/* ════════════════════════════════════════════════════════════════════════════
   Click-outside-to-close guard. Every overlay closes on a backdrop click via
   its inline `onclick="if(event.target===this) …close()"`. But a drag-select
   that STARTS inside the modal content and ENDS over the backdrop (e.g. swiping
   a textarea right-to-left and releasing past its edge) fires a `click` whose
   target is the overlay (the common ancestor of the mousedown/​mouseup), which
   would close the modal mid-selection. Guard: remember where the mousedown
   landed and, if a click lands on an overlay the press did NOT start on, swallow
   it in the capture phase before the inline close handler runs. A genuine
   backdrop click (press AND release on the overlay) is untouched. One listener
   covers every `.modal-overlay`, so no per-modal wiring is needed.
═════════════════════════════════════════════════════════════════════════════ */
(function () {
  let mdOverlay = null;
  const isOverlay = (el) => !!(el && el.classList && el.classList.contains('modal-overlay'));
  document.addEventListener('mousedown', (e) => {
    mdOverlay = isOverlay(e.target) ? e.target : null;
  }, true);
  document.addEventListener('click', (e) => {
    if (isOverlay(e.target) && e.target !== mdOverlay) {
      e.stopImmediatePropagation();
    }
  }, true);
})();

/* ════════════════════════════════════════════════════════════════════════════
   Collapsible cards — toggle .collapsed on a section that has a
   .collapsible-header + .collapsible-body inside.
═════════════════════════════════════════════════════════════════════════════ */
function toggleCard(id, force) {
  const el = document.getElementById(id);
  if (!el) return;
  if (force === true)       el.classList.add('collapsed');
  else if (force === false) el.classList.remove('collapsed');
  else                      el.classList.toggle('collapsed');
}

/* ════════════════════════════════════════════════════════════════════════════
   View switcher — show one functionality view at a time. Token stays global.
═════════════════════════════════════════════════════════════════════════════ */
