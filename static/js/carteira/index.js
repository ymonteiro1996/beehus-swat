/* Carteira — bootstrap da tela. Contexto: registra init() no
   DOMContentLoaded, que pré-preenche datas, entra no modo faixa e
   carrega as empresas. Depende de state.js (define Carteira). */
Object.assign(Carteira, {
    // ── Bootstrap ──────────────────────────────────────────────────────

    /* Contexto: inicializa a tela no carregamento (DOMContentLoaded).
       Pseudocódigo:
         1. Pré-preenche as datas inicial/final com hoje.
         2. Entra no modo "faixa" por padrão.
         3. Carrega a lista de empresas no seletor. */
    async init() {
      document.getElementById("ct-initialDate").value = this._todayISO();
      document.getElementById("ct-finalDate").value   = this._todayISO();
      this.onModeChange("range");
      await this._loadCompanies();
    },

});

  document.addEventListener("DOMContentLoaded", () => Carteira.init());
