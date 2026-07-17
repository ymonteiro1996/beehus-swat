/* Painel — fallback legado openRepetirInline. */
// Mantido como fallback p/ qualquer chamador legado que ainda invoque
// `openRepetirInline` (bookmarks, código externo). Encaminha pro novo
// fluxo inline. Pode ser removido quando confirmar que ninguém chama.
function openRepetirInline(chip) {
  if (typeof Repetir !== 'undefined' && typeof Repetir.open === 'function') {
    Repetir.open(chip);
  }
}
