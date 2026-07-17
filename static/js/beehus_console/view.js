/* Beehus Console — View: alternância de telas.
   Escopo global compartilhado; ordem importa (IIFEs de load no último). */
const View = {
  show(name) {
    const targetView = name;
    document.querySelectorAll('.view').forEach(el => {
      el.classList.toggle('active', el.dataset.view === targetView);
    });
    location.hash = name === 'menu' ? '' : name;
    // Header back-to-menu button was removed when this template was
     // absorbed into the Issues favorites-bar; guard against future
     // re-introduction or partial templates by going through optional
     // chaining instead of crashing on a null lookup.
    document.getElementById('back-to-menu')?.classList.toggle('hidden', name === 'menu');
    // View-scoped header actions: show only the buttons whose data-view-action
    // matches the current target view.
    document.querySelectorAll('[data-view-action]').forEach(el => {
      el.classList.toggle('hidden', el.dataset.viewAction !== targetView);
    });
    // Tell the parent shell which view we're on so it can mirror it in its
    // own URL hash (so this state survives sidebar round-trips and full
    // page reloads even if the iframe gets recreated for any reason).
    if (window.parent !== window) {
      try {
        window.parent.postMessage(
          {type: 'viewChange', view: name === 'menu' ? '' : name},
          '*',
        );
      } catch (e) { /* iframe-less or cross-origin — ignore */ }
    }
    if (name === 'identify' && !IdentifyTxn._inited) {
      IdentifyTxn._inited = true;
      // Keep the init promise so a deep-link prefill (from the Painel TXN
      // column) can await the company list before selecting one.
      IdentifyTxn._initPromise = IdentifyTxn.init();
    }
    if (name === 'delete-prov' && !DeleteProv._inited) {
      DeleteProv._inited = true;
      DeleteProv.init();
    }
    if (name === 'delete-pos' && !DeletePos._inited) {
      DeletePos._inited = true;
      DeletePos.init();
    }
    if (name === 'explosion-prop' && !ExplosionProp._inited) {
      ExplosionProp._inited = true;
      ExplosionProp.init();
    }
    if (name === 'unpublish-nav' && !UnpublishNav._inited) {
      UnpublishNav._inited = true;
      UnpublishNav.init();
    }
    if (name === 'process-dates' && !ProcessDates._inited) {
      ProcessDates._inited = true;
      ProcessDates.init();
    }
    if (name === 'nav-wallets-dates' && !NavWalletsDates._inited) {
      NavWalletsDates._inited = true;
      NavWalletsDates.init();
    }
    if (name === 'nav-groupings-dates' && !NavGroupingsDates._inited) {
      NavGroupingsDates._inited = true;
      NavGroupingsDates.init();
    }
    if (name === 'publish-dates' && !PublishDates._inited) {
      PublishDates._inited = true;
      PublishDates.init();
    }
  },
};

/* ════════════════════════════════════════════════════════════════════════════
   Shared API helper — every call goes through this so the log modal sees it.
═════════════════════════════════════════════════════════════════════════════ */
