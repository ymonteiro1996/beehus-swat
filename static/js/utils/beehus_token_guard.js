/* ── Token Beehus vencido → pop-up de colar token NA HORA (TRV-01) ─────────────────────────────
   [2026-09-25, pedido do usuário: "Quando tentar algo e estourar o token, já mostrar na tela o pop
   up de colar o token"] O servidor marca TODA resposta /api/* com o cabeçalho
   `X-Beehus-Token: expired` enquanto o token Beehus estiver ausente/vencido/rejeitado — inclusive
   quando a rota "engoliu" o erro e devolveu 200 com lista vazia. Este arquivo envolve o
   `window.fetch` e, ao ver o cabeçalho:
     • dentro de iframe: avisa a janela de cima (postMessage, mesma origem);
     • na janela de cima: abre o modal de token que a página registrou em
       `BeehusTokenGuard.registrarModal({abrir, estaAberto})`.
   A resposta segue intacta para quem chamou (nada é repetido sozinho — D11: repetir um POST
   poderia duplicar escrita). Não reabre com o modal aberto nem nos 20 s depois de fechado (o
   polling de 60 s do banner e os de 2,5 s do swat ficariam reabrindo).
   Mesmo arquivo nos três projetos (conciliacao, ControleCargas, beehus-swat). */
(function () {
  const CABECALHO = "X-Beehus-Token";
  const ROTAS_DE_TOKEN = ["/api/beehus/token", "/api/beehus-token"];
  const ESPERA_APOS_FECHAR_MS = 20000;
  const MENSAGEM_EXPIRADO = "beehus-token-expired";
  const MENSAGEM_SALVO = "beehus-token-saved";
  const estado = { modal: null, fechadoEm: 0 };

  /* Contexto:
     Diz se a URL de um fetch é a própria rota de token (não dispara o pop-up). Retorna boolean.

     Pseudocódigo:
       1. Normaliza para o caminho (sem origem nem query).
       2. Compara com as rotas de token conhecidas dos três projetos. */
  function ehRotaDeToken(url) {
    let caminho = String(url || "");
    try { caminho = new URL(caminho, location.href).pathname; } catch (e) { /* mantém */ }
    return ROTAS_DE_TOKEN.some(r => caminho === r || caminho.startsWith(r + "/"));
  }

  /* Contexto:
     Abre o modal de token desta janela, respeitando as travas anti-loop. Chamado quando uma
     resposta vem marcada ou quando um iframe avisa. Não retorna nada.

     Pseudocódigo:
       1. Sem modal registrado -> mostra um aviso fixo (página aberta fora do shell).
       2. Modal já aberto, ou fechado há menos de 20 s -> não faz nada.
       3. Senão, abre. */
  function abrirModalLocal() {
    if (!estado.modal) { mostrarAviso("O token do Beehus expirou. Cole um novo token (botão Token) e repita a ação.", true); return; }
    if (estado.modal.estaAberto && estado.modal.estaAberto()) return;
    if (Date.now() - estado.fechadoEm < ESPERA_APOS_FECHAR_MS) return;
    estado.modal.abrir();
  }

  /* Contexto:
     Reage a uma resposta marcada como "token vencido". Não retorna nada.

     Pseudocódigo:
       1. Dentro de iframe -> avisa a janela de cima (mesma origem) e para.
       2. Na janela de cima -> abre o modal local. */
  function avisarTokenVencido() {
    if (window.top !== window) {
      try { window.top.postMessage({ type: MENSAGEM_EXPIRADO }, location.origin); return; } catch (e) { /* cai no local */ }
    }
    abrirModalLocal();
  }

  /* Contexto:
     Aviso curto no canto da tela (ex.: "Token salvo. Repita a ação."). `fixo` = fica até ser
     clicado. Não retorna nada.

     Pseudocódigo:
       1. Reusa o elemento se já existir; senão cria.
       2. Mostra o texto; some sozinho em 6 s se não for fixo. */
  function mostrarAviso(texto, fixo) {
    let el = document.getElementById("beehus-token-aviso");
    if (!el) {
      el = document.createElement("div");
      el.id = "beehus-token-aviso";
      el.setAttribute("role", "status");
      el.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483600;max-width:360px;padding:10px 14px;" +
        "border-radius:8px;font:600 12px/1.4 system-ui,sans-serif;background:#1e3a8a;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.25);cursor:pointer";
      el.addEventListener("click", () => { el.style.display = "none"; });
      (document.body || document.documentElement).appendChild(el);
    }
    el.textContent = texto;
    el.style.display = "";
    clearTimeout(el._timer);
    if (!fixo) el._timer = setTimeout(() => { el.style.display = "none"; }, 6000);
  }

  const fetchOriginal = window.fetch.bind(window);
  window.fetch = function (entrada, opcoes) {
    const url = typeof entrada === "string" ? entrada : (entrada && entrada.url) || "";
    return fetchOriginal(entrada, opcoes).then(resposta => {
      try {
        if (resposta.headers.get(CABECALHO) === "expired" && !ehRotaDeToken(url)) avisarTokenVencido();
      } catch (e) { /* nunca quebra quem chamou */ }
      return resposta;
    });
  };

  window.addEventListener("message", evento => {
    if (evento.origin !== location.origin || !evento.data) return;
    if (evento.data.type === MENSAGEM_EXPIRADO) avisarTokenVencido();
    // MENSAGEM_SALVO chega aos iframes só para quem quiser reagir (ex.: recarregar um seletor);
    // o aviso "Token salvo. Repita a ação." aparece uma vez só, na janela de cima.
  });

  window.BeehusTokenGuard = {
    /* Página de cima registra o seu modal: {abrir(), estaAberto()}. */
    registrarModal(modal) { estado.modal = modal; },
    /* Chamar no fechar do modal: liga a espera de 20 s antes de reabrir. */
    marcarFechado() { estado.fechadoEm = Date.now(); },
    /* Chamar depois de salvar um token válido: avisa esta janela e os iframes (D11). */
    notificarTokenSalvo() {
      estado.fechadoEm = 0;
      mostrarAviso("Token salvo. Repita a ação.");
      document.querySelectorAll("iframe").forEach(f => {
        try { f.contentWindow.postMessage({ type: MENSAGEM_SALVO }, location.origin); } catch (e) { /* ignora */ }
      });
    },
    mostrarAviso,
  };
})();
