/* ── Guarda contra "clique fantasma" ao arrastar uma seleção de texto (TRV-02) ─────────────────
   [2026-09-25, pedido do usuário: "Ao selecionar o range de um valor para editar, ele fecha a
   janela da transação" / "[campo Token não deve] fechar quando arrasta o range de seleção"]

   Os fundos escuros dos modais fecham no `click` com `if (event.target === this) fechar()`.
   Quando o mouse DESCE dentro de um campo e SOBE em cima do fundo, o navegador entrega o `click`
   ao ancestral comum dos dois pontos — o próprio fundo — e o modal fecha no meio da seleção. O
   `onclick="event.stopPropagation()"` do painel interno não ajuda (o clique nem passa por ele).

   Esta guarda roda em FASE DE CAPTURA no documento e cancela só esse clique fantasma. Vale para
   todos os modais da página sem mexer em cada um. Cada documento precisa carregá-la (o shell e a
   página no iframe são documentos diferentes). Arquivo idêntico nos três projetos (conciliacao,
   ControleCargas, beehus-swat), em `static/js/utils/guarda_arrasto.js`. */
(function () {
  const SELETOR_CAMPO = 'input, textarea, select, [contenteditable=""], [contenteditable="true"]';
  let alvoDoMousedown = null;
  let mousedownEmCampo = false;
  // Houve texto selecionado em ALGUM momento do arrasto? Medido no mousemove porque, no Chrome,
  // arrastar de um texto comum até o fundo vazio zera a seleção ANTES do mouseup — olhar só na
  // hora do click perdia esse caso (achado no teste de 25/09).
  let houveSelecaoNoArrasto = false;

  /* Contexto:
     Diz se há texto selecionado agora (seleção não vazia). Usada no `click` para reconhecer um
     arrasto que selecionou texto fora de campo. Retorna boolean.

     Pseudocódigo:
       1. Sem API de seleção → false.
       2. Seleção não colapsada e com texto → true. */
  function haTextoSelecionado() {
    const selecao = window.getSelection ? window.getSelection() : null;
    return !!(selecao && !selecao.isCollapsed && String(selecao).length > 0);
  }

  /* Contexto:
     Decide se um `click` é o fantasma de um arrasto (e deve ser cancelado). Retorna boolean.

     Pseudocódigo (as três condições precisam valer):
       1. O mousedown começou num campo editável, ou o arrasto selecionou texto (agora ou em
          algum momento enquanto o botão estava apertado).
       2. O alvo do click é DIFERENTE do alvo do mousedown.
       3. O alvo do click CONTÉM o do mousedown (é um ancestral — tipicamente o fundo do modal).
       Um clique normal num botão com <span> dentro tem o mesmo alvo nos dois eventos e passa;
       texto de botão não é selecionável, então nem o arrasto curto dentro dele é barrado. */
  function ehCliqueDeArrasto(evento, origem) {
    if (!origem || !(evento.target instanceof Node)) return false;
    if (!mousedownEmCampo && !houveSelecaoNoArrasto && !haTextoSelecionado()) return false;
    if (evento.target === origem) return false;
    return evento.target.contains(origem);
  }

  document.addEventListener("mousedown", function (evento) {
    alvoDoMousedown = evento.target;
    mousedownEmCampo = !!(evento.target && evento.target.closest && evento.target.closest(SELETOR_CAMPO));
    houveSelecaoNoArrasto = false;
  }, true);

  document.addEventListener("mousemove", function (evento) {
    if (!alvoDoMousedown || !(evento.buttons & 1) || houveSelecaoNoArrasto) return;
    if (haTextoSelecionado()) houveSelecaoNoArrasto = true;
  }, { capture: true, passive: true });

  document.addEventListener("click", function (evento) {
    const origem = alvoDoMousedown;
    alvoDoMousedown = null;
    if (!ehCliqueDeArrasto(evento, origem)) return;
    evento.stopImmediatePropagation();
    evento.preventDefault();
  }, true);
})();
