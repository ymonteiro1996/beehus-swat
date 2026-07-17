/* Precificação — guarda de fechamento de modais (bootstrap).
   Parte da tela; compartilha escopo global com os demais pedaços
   (funções/estado top-level). Carregar na ordem definida no template. */
  let _modalDownTarget = null;
  function _guardedClose(modalId, closeFn) {
    document.getElementById(modalId).addEventListener("mousedown", e => { _modalDownTarget = e.target; });
    document.getElementById(modalId).addEventListener("click", e => {
      if (e.target === e.currentTarget && _modalDownTarget === e.currentTarget) closeFn();
      _modalDownTarget = null;
    });
  }
  _guardedClose("formulas-modal",   closeFormulasModal);
  _guardedClose("config-modal",     closeConfigModal);
  _guardedClose("wallet-sec-modal", closeWalletSecModal);
  _guardedClose("save-modal",       closeSaveModal);
  _guardedClose("edit-pu-modal",    closeEditPuModal);
  _guardedClose("refdate-modal",    closeRefDateModal);
