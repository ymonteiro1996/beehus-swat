# Funções (Beehus API Console)

The page at `/beehus` is a thin UI over a small Flask blueprint
(`pages/beehus_console.py`) that forwards calls to the upstream
`https://controladoria.beehus.com.br` API. Everything is rendered inside
the parent shell's iframe; the page is split into many short
"functionality" views that share helpers (Token modal, API log, view
switcher, two-pane wallet/grouping pickers).

This doc is the living index — keep it in sync when adding routes.

---

## Architecture

```
templates/shell.html          ─ parent: sidebar + iframe container
└── /beehus iframe
    └── templates/beehus_console.html ─ all functionality views in one HTML
        └── pages/beehus_console.py   ─ Flask blueprint (forwards to upstream)
            └── beehus_api/*          ─ stateless HTTP client per upstream area
```

`beehus_api/` is Flask-agnostic and reusable from any blueprint:

| Module | Functions |
|---|---|
| `client.py` | `set_token`, `get_token`, `clear_token`, `token_status`, low-level `request()` |
| `transactions.py` | `create_transaction`, `delete_transaction` |
| `positions.py` | `process_processed_position`, `delete_processed_position` |
| `provisions.py` | `create_provision`, `delete_provision` |
| `execution_prices.py` | `create_execution_price` |
| `consolidation.py` | `calculate_nav_wallets`, `calculate_nav_groupings`, `proportion_explosion`, `publish_nav`, `unpublish_nav` |
| `security_mappings.py` | `update_security_mappings` (PATCH `mappingsToInclude` / `mappingsToExclude` on a company's `securityMappings` document — used by the Issues page "Mapear no sistema" button) |

---

## Bearer token

The upstream API requires a daily JWT. It lives in process memory
(`beehus_api/client.py`) **and is persisted** to `~/.swat/beehus.token`
(the same local, current-user-only directory as the session token — **not**
the OneDrive-synced `data/` dir, so the credential never syncs to the cloud).
The token is reloaded at import time, so **restarting Flask comes back up with
the previously-entered token** instead of forcing a re-paste. It may still
expire upstream; a stale reload just yields a 401 on the next call and the
operator re-pastes — persistence only saves the re-paste while the token is
still valid (e.g. a mid-day restart). `clear_token` (DELETE) removes the file.

- UI: the **`Token` button lives in the global sidebar** (`templates/shell.html`,
  bottom nav block) — it opens a shell-owned modal that hits the API directly,
  so it works on every screen hosted in the iframe regardless of that screen's
  own markup. A coloured badge on the button shows state (green `Nm` = loaded /
  red `—` = none). Screens that used to carry their own Token button hide it
  when embedded in the shell (`?_frame=1`): `controlpanel.html` hides its
  favorites-bar `#chip-token`, `beehus_console.html` hides its whole header.
  Standalone pages outside the shell (e.g. direct `/correcoes`, direct `/beehus`)
  keep their own in-page Token badge as the only affordance there.
- Routes: `GET / POST / DELETE /api/beehus/token`.
- Refresh policy: the sidebar badge is refreshed on shell load, whenever the
  modal opens, and after save/clear. In-frame screens also mirror their own
  `Token.refresh()` to the sidebar via a `beehus-token-state` postMessage
  (e.g. after a 401), so the badge stays current. No periodic polling.
- **Global expiry banner** (`templates/partials/_token_banner.html`): because the
  dashboard reads everything from the API with **no Mongo fallback**, an
  expired/absent in-process token makes pages fail *silently* (empty lists, no
  error). The banner closes that gap: a fixed red top bar that polls
  `GET /api/beehus/token` on load, every 60 s, and on tab-focus, showing
  "Token Beehus expirado/ausente — colar token →" (links to `/beehus`) until the
  token is valid again. `token_status()` now returns `exp` (JWT `exp` claim,
  decoded **without** signature verification) and `expired` (bool, computed from
  the **in-process** token — so an instance whose in-memory token aged out is
  detected even when the file already holds a fresh one a sibling process
  pasted). Included by `shell.html` (top-level host) and by `base.html` only when
  **not** framed (`?_frame=1`), so it never double-renders inside an iframe.

---

## Filter routes (local Mongo, no upstream call)

Used by the cascading dropdowns and the eligibility-based pickers.

| Route | Purpose |
|---|---|
| `GET /api/beehus/filters/companies` | Companies visible to the current user (API-backed via `list_companies` → `GET /beehus/partners/companies`). **Sempre devolve um array `[{id,name}]`** (vazio inclusive) — o shape nunca muda, então os consumidores que fazem `r.body.map(...)` não quebram. Quando o array vier **vazio**, o MOTIVO vai no header **`X-Companies-Empty-Reason`** (token expirado/ausente via `BeehusAuthError`, falha de rede, ou todas as empresas excluídas pelo filtro de visibilidade em Configurações). Só o dropdown Day-trade do Painel lê esse header para exibir a mensagem; as demais dropdowns o ignoram. O `{}` de falha **não** fica em cache (ver `beehus_catalog.company_names`), então re-colar o token recarrega na próxima chamada |
| `GET /api/beehus/filters/groupings?companyId=` | Groupings of a company (uses `groupings.companyId`; `walletIds` extracted from embedded `wallets[].walletId`) |
| `GET /api/beehus/filters/wallets?companyId=&groupingId=` | Wallets of a company, optionally narrowed by a grouping. Returns `{id, name, entityId, entityName}` per row |
| `GET /api/beehus/filters/entities?companyId=` | Entities reachable through the company's wallets |
| `GET /api/beehus/filters/securities` | Security catalog |
| `GET /api/beehus/filters/wallets-with-position?companyId=&positionDate=` | Wallets that have a `processedPosition` for that company + date — drives the Excluir Posições "Disponíveis" pane in **data única** mode (range mode skips this pre-filter and lets the upstream resolve eligibility per day) |
| `GET /api/beehus/filters/groupings-by-publish-state?companyId=&positionDate=&published=true\|false` | Groupings whose `navPackages.published` matches — drives Publicar / Despublicar Agrupamentos |
| `GET /api/beehus/filters/grouping-return-deltas?companyId=&positionDate=&published=true\|false` | Per-grouping `{groupingId, groupingName, returnNavPerShare, returnContribution, deltaAbs}` from `navPackages` for that company + date. Aggregated as the **worst wallet** in each grouping (the navPackage doc with the largest `|returnNavPerShare − returnContribution|`); reported `returnNavPerShare`/`returnContribution` come from that worst-wallet doc. Sorted by `deltaAbs` desc, nulls last. Drives the Publicar Agrupamentos "Agrupamentos — diferença ≥ limite" table. `published` defaults to `false` |

**Schema notes (verified against production):**

- `groupings` documents store wallets in an embedded array of objects
  (`wallets[].walletId`, with `initialDateOnGrouping` / `finalDateOnGrouping`).
  There is **no** flat `walletIds` field — `get_grouping_index()` extracts it.
- `navPackages` carries `groupingId` directly; each grouping is cleanly
  fully-published or fully-unpublished on a given date (no mixed state
  observed in production).
- Provisions use `balance` (not `amount`) for the monetary value.

---

## Action routes (forward to upstream)

| Local route | Upstream | Notes |
|---|---|---|
| `POST /api/beehus/transactions` | `POST /beehus/financial/transactions` | Create a financial transaction |
| `DELETE /api/beehus/transactions/<id>` | `DELETE /beehus/financial/transactions/<id>` | Delete one transaction |
| `PATCH /api/beehus/transactions/<id>` | `PATCH /beehus/financial/transactions/<id>` | Partial update — body keys filtered to the patchable set (`balance`, `beehusTransactionType`, `currencyId`, `description`, `entityId`, `liquidationDate`, `operationDate`, `securityId`); unknown keys are dropped server-side |
| `POST /api/beehus/transactions/search` | `GET /beehus/financial/transactions` (endpoint G, via `beehus_catalog.transactions_search`) | Search for the Editar Transações and Identificar Transações tables. Accepts `groupingIds[]` (preferred, multi) or legacy `groupingId` (str), plus an optional `identified` filter (`'true'` = `beehusTransactionType` filled, `'false'` = empty/missing, anything else = both buckets). **Grouping scope is a union, not a narrowing:** a row matches if its `walletId` is in the (grouping-narrowed) wallet set **OR** its own `groupingId` is one of the selected groupings — so transactions attached directly to a grouping (whose `walletId` is outside the grouping's members, or null) are listed alongside the wallet-level rows. The endpoint ANDs its filters, so the wallet⋁grouping OR is run as two searches unioned by `_id`; the type/`identified`/`trashed` predicates, the `liquidationDate`-desc sort and the cap are reapplied client-side. Selected groupings are validated against the company via `get_grouping_index()`; the `groupingId` IN-clause spans both string and ObjectId representations (the field is stored as ObjectId in production). **`walletIds` is chunked to 150/request** in `list_transactions` (like the positions client) — an empty `walletIds` widens to the company's full wallet set (often hundreds–thousands of ids), whose CSV would otherwise blow the URL limit and the host would drop the connection (HTTP 414-class), silently returning `[]`. Sort: **`liquidationDate` desc** (most recent settlement first). cap: 10 000 rows (`truncated: true` when reached) |
| `GET / PUT /api/beehus/identify-transactions/config` | — | Load / replace the `typesNeedingSecurity[]` list used by Identificar Transações to decide whether each row's security cell is editable. GET also returns `allTypes[]` (the full known catalogue of `beehusTransactionType` values) so the config modal can render every checkbox without a second round-trip. Persisted to `data/identify_transactions_config.json` via the same atomic-replace helper used by `conciliacao_config.json` |
| `POST /api/beehus/identify-transactions/identify` | — | Body `{transactionIds: [str]}` (cap 5000). Returns one suggestion per id `{transactionId, beehusTransactionType, securityId, needsSecurity, securityAlternatives:[...]}`. Each alternative carries `source ∈ {level1, level2, collection}` so the security-edit modal can split them into three groups (L1 = carteira em T, L2 = carteira em T-1/T-2, L3 = cadastro completo). The identification heuristic itself is currently a **stub** — see `_suggest_for_transaction` in `pages/beehus_console.py`. Each suggestion is also enriched (one batch pass, `_compute_execution_extras`) with the **Preço exec. / IRRF** fields `{executionPrice, irrf, pu, amountDifference, securityType, formerDate, withinGate, execGroupKey}` — see the *Preço de execução & IRRF* subsection below |
| `POST /api/beehus/identify-transactions/execution-extras` | `POST /beehus/financial/execution-prices` + `POST /beehus/financial/transactions` | Fired by **Implementar** after the per-row PATCHes. Body `{executionPrices:[{walletId, securityId, positionDate, executionPrice}], taxes:[{sourceTransactionId, balance}]}`. `companyId` is resolved server-side from the wallet (transactions carry none); each IRRF item creates a new `taxes` transaction copying entity/wallet/security/dates/currency from its source. Returns `{execOk, execFail, taxOk, taxFail, errors[]}`; an upstream 401 aborts the rest |
| `GET /api/beehus/identify-transactions/wallet-securities` | — | Returns `{securityIds:[...]}` — union of securityIds the wallet holds in the **3 most recent** processedPositions on/before `liquidationDate`. **Currently unused** by the security-edit modal (the free-text search hits the full cadastro now), kept around in case a future "restringir ao escopo da carteira" toggle wants the same payload |
| `GET /api/beehus/identify-transactions/wallet-position-detail?walletId=&liquidationDate=&level=l1\|l2` | — | Returns the **enriched** processedPositions snapshot for one level: `{level, positionDate (l1) or positionDates (l2), securities:[{securityId, beehusName, mainId, ticker, securityType, maturityDate, quantity, pu, pricingType}, ...]}`. Powers the "Expandir posição completa" button on the L1 group of the security-edit modal — replaces the matcher's short top-N with the wallet's full position so the operator can pick any held security, not just the classifier's picks. L2 mode excludes ids already in L1 so the two levels stay disjoint |
| `POST /api/beehus/positions/process` | `POST /beehus/financial/positions/processed-position/process` | `wallets: []` upstream means "all". **Cadeia de explosão**: cada wallet pedida é expandida server-side com as carteiras que ela arrasta (ativo de explosão, todos os níveis) via `beehus_catalog.expand_wallets_with_explosion` antes de chamar o upstream — sem isso o processamento não conclui. A resposta inclui `draggedWallets: [...]` quando houve arrasto. Ver [EXPLOSION_CHAIN.md](EXPLOSION_CHAIN.md) |
| `GET /api/beehus/positions/explosion-chain?companyId=&walletId=` | — | Preview da cadeia de explosão de UMA carteira: `{chain: [{securityId, walletId, name, level, viaWalletId}, ...]}` (achatada, vazia sem explosão). Alimenta o modal de confirmação do Processar (`explosionPreview`) |
| `POST /api/beehus/positions/delete` | `DELETE /beehus/financial/positions/processed-position/delete` | Body field is **`walletIds`** (note the `Ids` suffix vs. the sibling /process endpoint's `wallets`) |
| `POST /api/beehus/provisions` | `POST /beehus/provisions` | Create a provision |
| `DELETE /api/beehus/provisions/<id>` | `DELETE /beehus/provisions/<id>` | Delete one provision |
| `POST /api/beehus/provisions/search` | — | **Local Mongo** search; date filter is **interval-overlap** on `[initialDate, liquidationDate]` |
| `POST /api/beehus/execution-prices` | `POST /beehus/financial/execution-prices` | Create an execution price for `(companyId, walletId, securityId, positionDate)` |
| `POST /api/beehus/nav/calculate-wallets` | `POST /beehus/consolidation/nav-contribution-calculation/wallets` | Body field `wallets` |
| `POST /api/beehus/nav/explosion-proportions` | `POST /beehus/consolidation/nav-contribution-calculation/explosion-proportions` | Body field `groupings` |
| `POST /api/beehus/nav/calculate-groupings` | `POST /beehus/consolidation/nav-contribution-calculation/groupings` | Body field `groupings` |
| `POST /api/beehus/nav/publish` | `PATCH /beehus/consolidation/nav-contribution-calculation/publish` | Upstream takes a JSON body `{companyId, positionDate, groupingIds[]}`. Local route forwards the same shape; long lists are split into 50-id batches for partial-success granularity and bounded per-call latency |
| `POST /api/beehus/nav/unpublish` | `PATCH /beehus/consolidation/nav-contribution-calculation/unpublish` | Same JSON-body shape as `/publish` |
| `POST /api/controlpanel/apply-mapping` | `PATCH /beehus/financial/security-mappings/{id}` | Painel de Controle route: maps the selected unprocessed→security pairs into Beehus directly. Body: `{companyId, mappingsToInclude:[{from,to}…]}`. The route looks up `securityMappings._id` server-side from `companyId` (so the client cannot tamper with it) and only forwards `mappingsToInclude` (exclusions are out of scope here). 401/403 → 401, anything else upstream → 502 |

All forwarders run `company_visible(companyId)` before touching upstream
and return `_api_error_response(e)` on `BeehusAPIError` (mapping
401/403 → 401, everything else → 502).

---

## UI sections

The menu view groups every operation into six themed sections (in
display order). Each card opens its own view with the standard filter
card → action card → result panel layout.

| # | Group | Cards |
|---|---|---|
| 1 | **Lançamentos** | Transações (Identificar / Editar / Excluir) · Excluir Provisões |
| 2 | **Posições** | Processar Posições · Excluir Posições (data única ou faixa) · Processar por datas |
| 3 | **NAV Wallets, Explosões e NAV Groupings** | Calcular NAV Wallets · Proporcionalizar Explosão · Calcular NAV Groupings |
| 4 | **Publicação** | Publicar Agrupamentos · Despublicar Agrupamentos · Publicar Agrupamentos por datas |

The "por datas" cards (groups 2–4) are single-step batch pipelines
described later in this doc. *(O grupo **Rotina Diária** — pipelines multi-step
Fluxo diário / Reverter dia / Fluxo por datas / Reverter por datas — foi
**removido em jun/2026**.)*

### Per-card behavior

- **Transações (Identificar / Editar / Excluir)** — uma tela única
  (view `#identify`, controlador `IdentifyTxn`) faz as três operações sobre o
  **mesmo grid**. *(As antigas views "Editar Transações" / "Excluir Transações"
  — `#delete` / `#delete-only`, controlador `DeleteTxn` — e o body-class
  `delete-only-mode` foram **removidos em jun/2026**; tudo vive agora na tela de
  Identificar.)* Os filtros (Empresa + data única/faixa, painéis de
  transferência de Groupings e Wallets, chips de Entidade/Tipo/Security) e o
  radio **Não identificadas / Identificadas / Todas** servem aos três fluxos —
  para editar/excluir transações já identificadas, troque o radio para
  "Todas"/"Identificadas".

  **Modelo de seleção (importante).** A seleção vive num **conjunto persistente
  de ids** (`_selSet`), independente do filtro — um checkbox está marcado sse o id
  ∈ `_selSet`. Uma **busca nova** seleciona todas as linhas; filtros
  (descrição/tipo/fonte/confiança) e ordenação só mudam **o que é visível**, nunca
  a seleção. Assim, estreitar e depois alargar um filtro **não** perde marcações
  (nem re-seleciona silenciosamente — o gatilho de exclusão em massa). As ações
  (Identificar / Implementar / Editar / Excluir) agem em **visível ∩ selecionado**
  (`_selectedIds()` = linhas visíveis que estão no `_selSet`), então uma ação sob
  filtro nunca toca linhas que o operador não vê; os contadores dos botões
  refletem esse mesmo conjunto, e o modal de exclusão avisa quando há linhas
  selecionadas fora do filtro (que não serão excluídas).

  **Editar** — escolha um campo em **Configurar edição** (Data de liquidação /
  Entidade / Tipo / Security / Descrição). O modal lista os valores únicos das
  linhas **marcadas** e mapeia cada valor atual → novo (em branco = manter); salvar
  mostra uma pílula com o campo e a contagem de valores mapeados. As células do
  campo escolhido, **nas linhas marcadas**, viram input/select inline (limitado a
  `INLINE_EDIT_MAX_ROWS` = 200 linhas; acima disso a coluna inline é omitida e usa-se
  só o mapeamento por valor). Não há "force-uncheck": a célula editável == linha que
  irá no PATCH, porque ambas seguem o mesmo checkbox. Overrides por linha vão para
  `_tempEdits` e vencem o mapeamento por valor; reverter uma célula ao valor atual
  quando há mapeamento por valor grava um **sentinela de "manter"** (marcado com ⊘)
  que cancela o bucket só naquela linha. **Editar selecionadas** abre um relatório
  (resumo por campo + detalhe por transação) e roda
  `PATCH /beehus/financial/transactions/<id>` por linha afetada
  (`_computeEditPatches` / `_pendingEditPatches` / `runEdit`). A pílula
  "✎ Edições por linha" resume os overrides e o × (`clearInlineEdits`) os limpa.
  Os mapeamentos por valor (`_edits`) são descartados em cada busca nova (sobrevivem
  só no refresh pós-Implementar). O configurador edita o valor do **banco** (colunas "atual"), separado
  do fluxo de sugestão (colunas "sugerido"). O botão **★ Reforço temporário
  (edição)** (no painel "Configurar edição") aplica um valor a um campo em todas
  as linhas cujo `description` casa com um trecho — grava override por linha em
  `_tempEdits` (mesmo caminho da edição inline; só as linhas **selecionadas** vão
  no PATCH, o preview aparece em todas as que casam), com guardas de trecho ≥ 4
  caracteres e confirmação acima de 20 linhas (`applyEditReinforce`, modal
  `#i-edit-reinforce-modal`, reusando `_tempNorm`/`_tempScore`). É **distinto** do
  **★ Reforço temporário** de identificação (que escreve `_suggestions`); os dois
  coexistem na tela.

  **Excluir** — **Excluir selecionadas** confirma num modal e roda
  `DELETE /beehus/financial/transactions/<id>` por linha (`confirmDelete` /
  `runDelete`), removendo as linhas do grid ao final.

  Editar e Excluir usam os **mesmos** endpoints PATCH/DELETE que o "Implementar
  selecionadas" do Identificar já chamava — a unificação foi só de UI. As ações
  longas travam a tela com o overlay global (`showBusy`/`hideBusy`): "Editando
  i/N…" / "Excluindo i/N…". A coluna **TXN** do Painel de Controle continua
  abrindo esta tela inline e pré-filtrando company+date (`prefillFromPainel`,
  via `Funcoes.openIdentifyWith`).

- **Identificar Transações** — the same three-card filter shape
  (Empresa+datas / Groupings / Wallets), with a date-mode
  switch (data única vs faixa) so the user can target a single
  liquidation date without filling both ends. The "Filtros adicionais"
  card carries the usual entity/tipo/security chip pickers plus an
  **identified toggle** (Não identificadas / Identificadas / Todas) that
  pre-filters the result by whether `beehusTransactionType` is set —
  default is "Não identificadas". This view is also a **drill-down target**
  from the Painel de Controle **TXN** column: that column counts, per company,
  the transactions on the selected date whose `beehusTransactionType` is null/
  empty. Clicking the number does NOT navigate the whole shell — it opens this
  screen **inline** in the Painel (the same `/beehus#identify` iframe the
  "Identificar" chip uses, so the favorites bar stays visible) and reaches into
  that same-origin iframe to call `IdentifyTxn.prefillFromPainel(companyId,
  date)` directly (`Funcoes.openIdentifyWith`). That method awaits `init()`,
  selects the company, sets the single-date liquidation filter, forces the
  "Não identificadas" radio, and runs the search — so the operator lands on
  exactly the rows behind the counter they clicked, and re-targeting (clicking
  TXN for a different company) reuses the live iframe. The same method is also
  invoked on boot from `?companyId=&date=` query params, so a direct
  `/beehus?companyId=&date=` link works as a fallback. Above the result table,
  alongside the
  Confiança filter and the Ordenar selector, lives a free-text
  **Descrição** input that composes with both: a case-insensitive
  substring filter applied inside `_visibleTxns()` before the confidence
  buckets and the sort. The typed text must appear exactly as a
  substring of `description` (no accent stripping, no normalisation —
  same "100% match" semantics as the bulk-edit description filter). The
  filter resets on every new search and on **Limpar filtros**; the ×
  button next to the input clears it on demand. Next to it, a **Tipo**
  dropdown surfaces every distinct **effective type** found in the
  loaded result set (`_effectiveTypeFor(t)` = suggested type from
  `_suggestions[id].beehusTransactionType` if present, fallback to the
  row's current `t.type`). Counts are appended per bucket, and a
  `(sem tipo)` entry shows up whenever any row has neither a suggested
  nor a current type. Picking one narrows the table to rows whose
  effective type matches — so the daily workflow "Não identificadas →
  Identificar selecionadas → filtrar por dividend" works without
  re-querying Mongo. The options auto-rebuild on every render
  (including after **Identificar selecionadas** populates suggestions
  and after manual ✎ picks via the Tipo edit modal), so the dropdown
  always reflects the current universe of effective types. Both
  filters compose with the Confiança filter and the Ordenar sort —
  Ordenar already exposes "Confiança do security ↑ / ↓" inside the
  **Confiança do security** optgroup, which sorts visible rows by
  `_suggestions[id].securityConfidence` (rows that don't need a security
  fall to the bottom in both directions). Row selection is handled at the
  `<tr>` level (`IdentifyTxn.onRowClick`), so **clicking anywhere on a row**
  toggles its checkbox — except on the row's own controls (✎ Editar buttons,
  the clickable confidence badges, links/selects), which keep working. Three
  Excel-style modes: a **plain click** toggles the row and arms the anchor; a
  **shift+click** propagates the clicked row's new state to every row in the
  contiguous span back to the anchor (handy for de-marking large blocks before
  **Identificar selecionadas**); a **ctrl/cmd+click** toggles that single row
  avulso and deliberately leaves the shift anchor untouched, so a later
  shift+click still ranges from the original anchor. The anchor is per-render:
  any filter/sort change repaints the tbody and resets the anchor, so the next
  plain click re-arms it. The result table has
  per-row inline selects for **Tipo sugerido** and **Security
  sugerido**. The Tipo sugerido cell auto-disables when the (effective)
  tipo is **not** in the configured `typesNeedingSecurity` list (the
  label shows `(N/A)`), but the **✎ Editar** button on the Security
  sugerido cell is always clickable — the operator can attach a security
  to any row, including ones whose suggested type doesn't normally need
  one. `openSecEdit` initialises an empty `_suggestions[txnId]` entry
  on demand so editing a never-classified row behaves identically to
  editing an existing suggestion. Clicking **✎ Editar** on the **Tipo
  sugerido** cell opens the **Editar tipo** modal — a grid of every known
  `beehusTransactionType`; picking one applies it to the row (and clears any
  orphan security suggestion when the new type doesn't need one).

  **Reinforcement from either edit modal.** Both the **Editar security** and
  the **Editar tipo** modals carry a header button **★ Salvar … como reforço**
  that opens the shared confirm modal (`#i-reinforce-modal`, layered z-60) and
  persists a rule via `POST /api/beehus/identify-transactions/reinforcement`:
    - **Editar security** → saves **type + security** (`openReinforce`).
    - **Editar tipo** → saves a **type-only** rule with an empty `securityId`
      (`openReinforceType`).

  **Explicit `noSecurity` flag ⇒ no security cascade.** A reinforcement can
  carry `noSecurity: true` — the operator's deliberate signal that the matched
  transaction has *no* security at all (e.g. `AJ POS DE FUT`, futures position
  adjustments; bolsa/câmbio liquidations). When a matched rule has this flag,
  `_suggest_for_transaction` sets `reinforced_no_security`, which forces
  `needsSecurity = False` and skips the L1/L2/L3 cascade entirely — so the row
  is never flagged pending-security nor gets a security invented for it, even
  though its type (e.g. `buySell`) is in `typesNeedingSecurity`. The flag is set
  via the **Sem security** checkbox in the reinforce modal (both the *Editar
  security* and *Editar tipo* flows) and is editable from the **Reforços** modal's
  CRUD table (it renders a `sem security` badge in the Security
  column). It is the explicit, unambiguous alternative to an empty `securityId`:
  a plain type-only rule (empty `securityId`, no flag) still runs the security
  cascade — so a generic snippet like `COR JSCP` matched inside `COR JSCP ITUB4`
  still classifies the concrete security. Storage is lean: the key is written
  only when `true`; absence reads as "needs a security normally". On the wire:
  `POST …/reinforcement` accepts `noSecurity` (omit ⇒ leave existing untouched
  on update; `true`/`false` ⇒ set/clear); the list endpoint echoes it back.

  Both reuse the exact same rationale and confirm flow as the security
  reinforcement: the operator edits the snippet in a textarea with a live
  coverage preview (mirrors `_lookup_reinforcement`: 100% exact, else
  substring coverage clamped to 70–99%) and confirms before anything is
  written. In future identifies, any normalised description containing the
  snippet inherits the saved type (and security, when present). A **Fonte**
  column sits right after
  **Conf. Sec.** and surfaces where the SECURITY identification came from
  (`_securitySourceCell` mapping `_suggestions[id].securitySource`):
  **Reforço** / **Reforço~** (rule, exact vs substring), **L1 / L2 / L3**
  (wallet cascade depth — current position / T-1·T-2 / full cadastro, L3
  shown amber as the weakest signal), **Desempate** (the buySell
  amountDifference tie-breaker reordered the candidates) and **Manual**
  (operator pick); it reads `—` for rows that don't need a security or
  weren't identified yet. A **Fonte** dropdown in the filter bar narrows the
  listing to one of those buckets (`_sourceFilter` → `_sourceCategory` inside
  `_visibleTxns`, composing with the Confiança/Tipo/Descrição filters and the
  sort); it resets on every fresh Buscar.

  **Score-calculation tooltip.** Hovering the **Conf. Sec.** badge (and the
  score cell of each candidate inside the security-edit modal) shows *how the
  score was obtained*: every matched signal with its point contribution,
  summing to the candidate's total score, then the score→confidence mapping.
  The decomposition is computed server-side by
  `transaction_security_classifier._score_breakdown(reasons, score)`, which
  mirrors the additive weights of `security_matcher._score_candidate`
  (`mainId/cetip` +50, `mainId/code` +45, `mainId/external` +40,
  `mainId/fund_code` +35, `selicCode~` +40, `mainId/structured` +50,
  `maturityDate=` +50, `maturityDate` +25, `indexer` +10, name overlap
  `int(o/t·15)`, type-agreement +12, compressed-name substring +35 /
  compressed-mainId +25) and the two hard rules (exact unique id —
  ticker/CNPJ/ISIN/SELIC **or a code equal to the whole `mainId`**, ≥8 chars →
  `_EXACT_SCORE` = 300, above the uncapped additive+price ceiling (217 + 30) so
  an exact id always outranks a partial pile-up; a disagreeing maturity date → 0,
  rejected). The dynamic rare-name bonus —
  `min(30, round(rare_w·30))`, not recoverable from the reason code alone — is
  taken as the **residual** (`score − Σ recovered`), which also makes the
  breakdown self-correcting: the listed parts always sum to the real score even
  if a weight drifts. It is attached to each entry of
  `securityAlternatives[].breakdown` by `_alt`, so it reaches the client without
  changing `_score_candidate`'s `(score, reasons)` contract. The tooltip header
  names the **Fonte** (L1/L2/L3/Desempate/Reforço); the footer shows
  `Confiança = score/100` clamped to 100%, the **65 % cap** when the match is
  ambiguous (2º/1º ≥ 0.85), and — for buySell rows resolved by the
  `amountDifference` desempate — the expected vs actual settlement gap (forwarded
  as `securityTiebreak`). Reinforcement-sourced picks have no cascade score, so
  the tooltip falls back to a short *origem + confiança* note.

  Two action buttons: **Identificar
  selecionadas** (calls `POST /api/beehus/identify-transactions/identify`,
  populates the suggestion selects with the server's response — currently
  a stub returning empty values) and **Implementar selecionadas** (PATCHes
  every row whose suggestion differs from the current value, after a
  confirmation modal that lists the per-row diffs). After Implementar
  the page **silently re-searches** to refresh the listing with the new
  DB state — and on that implicit refresh `_suggestions`, `_descFilter`
  and `_typeFilter` are **preserved** (rather than wiped, as they would
  be on an explicit Buscar). Stale `_suggestions` entries for rows that
  dropped out of the new result set (e.g. just-identified rows under
  "Não identificadas") are pruned, but everything else survives — so
  the operator can keep iterating filter → Implementar without having
  to re-run Identificar selecionadas on the surviving rows. A gear button
  ⚙ Configurações opens a modal listing every known
  `beehusTransactionType` with a checkbox: only checked types include a
  security identification step. Defaults: `amortization`, `buySell`,
  `coupon`, `dividend`, `dividendOnboarding`, `interestOnEquity`,
  `maturity`, `securityContributionAdjustment`, `securityTransfer`,
  `taxes`. Saved to `data/identify_transactions_config.json` (atomic
  replace, OneDrive-safe).

#### Reforços — listagem, edição & exclusão (modal autônomo)

  O modal **Reforços de identificação** (`#i-reinf-list-modal`, `openReinforcements`)
  é aberto pelo botão **★ Reforços** no card de filtros (bloco company/datas) da
  tela Identificar — **migrado da antiga aba "Reforços" do modal ⚙ Configurações**
  (que voltou a ser só "Tipos com security"). É mais largo (`min(1200px, 96vw)`) e
  as regras são **globais** (não filtradas por company). Lista todas as regras
  (busca client-side por trecho/tipo/security) e oferece edição e exclusão por
  **modais dedicados** (substituíram o antigo form inline + `confirm()` nativo):

  - **Editar** (`#i-reinf-edit-modal`, `_reinfOpenEdit`) — trecho (textarea) com
    **preview ao vivo da chave normalizada** e **aviso de colisão**: a chave vem
    do endpoint `POST …/reinforcement/normalize` (fonte única de verdade — inclui
    o mascaramento de tokens `<OPCODE>`/`CDB<CODE>`, que a aproximação NFD do JS
    não faz). Tipo (select), **seletor de security por autocomplete** (busca em
    `/api/beehus/filters/securities`, id+name; preenche `securityId`/`securityName`
    e limpa o `mainId`, que fica como campo manual opcional), e o flag
    **noSecurity** (mutuamente exclusivo com uma security escolhida). Ao salvar,
    a chave normalizada autoritativa vem do servidor; se mudou, o reforço antigo
    é `DELETE`-ado (com confirmação extra em caso de colisão) antes do `POST`
    (upsert).
  - **Excluir** (`#i-reinf-del-modal`, `_reinfOpenDelete`) — modal de confirmação
    que **lista o(s) reforço(s)** (chave, tipo, security, hits) antes de remover,
    em vez do `confirm()` nativo. Atende exclusão **individual** (botão na linha)
    e **em lote**: checkboxes por linha + master no cabeçalho selecionam regras;
    o botão **"Excluir selecionados (N)"** abre o mesmo modal com todas. A seleção
    persiste pela busca e é podada no reload.

  Backend inalterado salvo o novo `POST …/reinforcement/normalize`
  (`identify_txn_reinforcement_normalize`) que ecoa a chave normalizada.

#### Preço de execução & IRRF (colunas derivadas)

  A grade tem duas colunas extras, **Preço exec.** e **IRRF**, calculadas por
  `_compute_execution_extras` (backend) no momento do **Identificar** e
  exibidas como inputs editáveis. Convenção de sinais (`docs/API_COLLECTIONS.md`):
  `balance` `+`=entrada/`−`=saída; `quantity` `+`=compra/`−`=venda.

  - **Preço de execução** — para `buySell` ou `maturity`, **exceto** quando
    `securityType == brazilianFund` (nesses casos só o IRRF se aplica — o caixa
    do fundo já reflete NAV/cotas, então não há preço de execução):
    `executionPrice = −Σbalance / Σquantity`. Para `buySell`, soma-se balances
    e quantidades **agrupando por `(walletId, securityId, liquidationDate)`** na
    leva identificada, de modo que múltiplas execuções parciais no mesmo ativo/dia
    compartilham **um único preço**; `maturity` é por linha. `amountDifference` é o
    `quantity` da própria transação e, **quando ausente**, cai para o Δquantidade
    entre a posição anterior e a mais recente.
  - **IRRF** — para `buySell` de `securityType == brazilianFund` com `balance > 0`
    (resgate de fundo): `IRRF = balance + amountDifference × PU` (resulta ≈ `−imposto`,
    negativo). **PU** = `securities.pu` da posição mais recente com
    `positionDate ≤ liquidationDate`.
  - **Fonte das posições (sem Mongo).** PU e Δquantidade vêm da **API
    processed-position** (endpoint A, `beehus_catalog.processed_doc`), **não do
    Mongo**. Como A é **data-exata** (sem range/«≤ data»), `_compute_execution_extras`
    caminha dias úteis para trás a partir do `liquidationDate` até a posição
    `≤ liq` (d0, p/ o PU) e — só quando o Δqty é realmente necessário (txn **sem**
    `quantity`) — até a posição anterior (d1), com teto de **25 dias úteis** e cache
    por `(carteira, data)`. Carteiras de posição **diária** resolvem em 1-2 chamadas
    e batem 100 % com o Mongo (validado); carteiras com gap > 25 d.u. degradam de
    forma graciosa (PU `None` / Δqty = quantidade absoluta), como a antiga janela de
    40 posições já fazia para `liq` fora do seu alcance.
  - **Gate temporal** (ambos): só calcula quando
    `biz_days_between(operationDate, liquidationDate) < 3` (dias úteis seg–sex;
    **feriados não são excluídos** — aproximação, ver `db.biz_days_between`). Fora
    da janela a célula mostra `—` (com tooltip explicando o motivo).
  - **Color-grading.** Preço exec. vs PU (`|exec−PU|/PU`): ≤20 % verde · ≤40 %
    amarelo · >40 % vermelho. IRRF vs balance (`|IRRF|/|balance|`): ≤15 % verde ·
    ≤22 % amarelo · >22 % vermelho. As cores reusam `.badge-ok/.badge-amber/.badge-err`.
  - **Edição/remoção.** O operador pode sobrescrever ou **limpar** qualquer valor
    (override em `_execEdits[txnId]`; vazio = não enviado). A cor é recalculada ao
    vivo. Reidentificar a linha descarta o override (cálculo novo prevalece). O
    tooltip de cada célula mostra a memória de cálculo (balance, amountDifference,
    PU, fórmula, janela de dias úteis).
  - **No Implementar.** Após os PATCHes de tipo/security, o front dispara
    `POST /api/beehus/identify-transactions/execution-extras`: sobe os
    **preços de execução** (deduplicados por wallet/security/data, `positionDate =
    liquidationDate`) e cria uma transação **`taxes`** por IRRF (entity/wallet/
    security/datas/moeda da transação de origem, `description = "IRRF — <descrição
    origem>"`). Esses campos da origem **vêm do cliente** (a grade já os carregou
    via `/search`) — o backend **não** re-busca a transação por `_id` no Mongo
    (mesmo padrão do `/identify`); `companyId` é resolvido da carteira e validado
    por visibilidade (autorização por carteira visível, não por posse do `_id`).
    O modal de confirmação lista tudo que será enviado antes do disparo. ⚠ Os valores refletem a identificação
    **no momento do Identificar**; alterar tipo/security depois exige reidentificar
    para recomputar.

  Clicking the **Security sugerido** cell opens the **Editar security**
  modal. The modal splits the classifier's alternatives into three
  distinct groups, ordered top-down by relevance:
    - **L1 — Carteira (T)**: securities na posição mais recente da carteira
      (o snapshot mais provável de conter o ativo correto). Tem um botão
      **Expandir posição completa** que troca a tabela curta (top-N do
      matcher) pela posição inteira — todos os ativos que a carteira tem em
      T, com quantidade e PU — permitindo selecionar mesmo quando o matcher
      errou (por exemplo, quando a descrição usa uma nomenclatura diferente
      do `beehusName`). Clicar de novo no botão (já rotulado "Recolher")
      restaura a lista curta original.
    - **L2 — Carteira (T-1 / T-2)**: securities que estavam em T-1 ou T-2
      mas saíram em T (vencimentos, vendas). Útil quando a transação
      liquida um ativo que já não consta da posição atual.
    - **L3 — Cadastro completo**: fallback no `SecurityCache` global —
      ativos que **não** estão em nenhuma posição recente dessa carteira.
      Cada linha L3 deveria ser revisada com mais cautela: é o sinal de
      que a carteira nunca segurou esse papel.

  **Fonte da posição (L1/L2 e "Expandir posição completa")** — a busca é
  por data **exata** (T, e T-1/T-2 para L2), preferindo a posição
  **processada**. Quando a carteira **não tem posição processada** naquela
  data — típico de carteiras em **tombamento**, que só têm a foto-base
  processada — cai na posição **bruta (unprocessed)** da **mesma data**,
  lendo o `preProcessingData.securityId` já resolvido de cada ativo. Assim o
  cenário L1 (carteira em T) aparece mesmo antes de a posição ser
  processada. Continua sendo data exata — **sem scan para trás** em datas
  esparsas; só quando nem processada nem bruta existem é que o nível fica
  vazio e a cascata desce para L3.

  Abaixo das três tabelas o operador ainda tem o campo **Buscar outra
  security**, que pesquisa o **cadastro completo da empresa** (o
  `SecurityCache` em memória, mesmo universo do L3). Os resultados trazem as
  colunas **mainId** (id legível — ticker/CNPJ/código), **beehusName** e
  **securityId**, e a busca textual casa contra mainId **ou** beehusName; o
  pick carrega o mainId, então a célula de sugestão renderiza `mainId ·
  nome`. O escopo L1∪L2 da carteira já está exposto nos grupos do topo,
  então restringir a busca textual a esse mesmo recorte só duplicaria a
  visão e impediria pegar ativos legítimos fora dele (transferências entre
  carteiras, ativos recém-cadastrados, etc.).
- **Excluir Provisões** — same shape as the Transações search; date filter
  is **interval-overlap**.
- **Processar Posições** — Empresa / data row, then an optional
  two-pane **Groupings (opcional)** transfer (mirrors the Transações picker) and
  a two-pane **Wallets** picker (Available ↔ Selected). Selecting
  groupings filters the available wallets pane to the **union** of
  those groupings' `walletIds`. If no wallet is moved to "Selecionadas"
  but at least one grouping is selected, the upstream call is
  restricted to the **union** of those grouping walletIds (matches the
  Transações picker semantics); empty groupings + empty wallets ⇒ "all
  wallets in company".
- **Excluir Posições** — same shape as **Processar Posições**: **Data única
  / Faixa de datas** toggle, optional explicit-dates picker (range mode),
  **Groupings (opcional)** dual-pane transfer (union-filters the wallet
  picker), **Wallets** dual-pane, and "⬆ Subir Excel" buttons for groupings /
  dates / wallets. In single-date mode the "Disponíveis" wallets pane is
  restricted to wallets that have a `processedPosition` for the chosen
  date (eligibility query, debounced 500ms); when no wallet is moved to
  "Selecionadas" but at least one grouping is selected, the upstream call
  is restricted to the **union of those grouping walletIds ∩ eligibility**
  (only wallets that actually have a `processedPosition` to delete) —
  empty groupings + empty wallets ⇒ "all eligible wallets for the date".
  In range mode the wallets pane shows the full company list (no eligibility
  pre-filter — the upstream resolves per day) and the pipeline POSTs to
  `/api/beehus/positions/delete` per business day (or per explicit-list date);
  empty groupings + empty wallets ⇒ "all wallets in company" per day. Built
  with `makeDatesPipeline({kind:'wallets', payloadKey:'walletIds'})` + the
  `_augmentDeletePos` IIFE that adds the mode toggle, the groupings picker,
  and the single-date eligibility filter.
- **Calcular NAV Wallets** — same shape as Processar, including the
  optional **Groupings (opcional)** dual-pane transfer with the same
  union-fallback semantics for the wallets payload.
- **Proporcionalizar Explosão** — same shape as **Calcular NAV Groupings**:
  **Data única / Faixa de datas** toggle, optional explicit-dates picker
  (range mode), and groupings dual-pane transfer (no wallet picker —
  groupings are the entity). In range mode iterates POSTs to
  `/api/beehus/nav/explosion-proportions` per business day; empty grouping
  selection = "all groupings of the company" per day. Built with
  `makeDatesPipeline({kind:'groupings'})` + the `_augmentExplosionProp`
  IIFE that adds the mode toggle.
- **Calcular NAV Groupings** — same shape as Proporcionalizar Explosão.
- **Publicar Agrupamentos** — Empresa / data + groupings transfer
  pane, but the available pane is restricted to groupings with
  `navPackages.published=false` (eligibility query, debounced). The
  picker is a div-based listbox with two columns: **Grouping** name on
  the left and **|Δ|** right-aligned (`|returnNavPerShare −
  returnContribution|`, computed as the **worst wallet** in that
  grouping). Click highlights a row, Ctrl/⌘+click toggles, double-click
  moves to the other side. A **threshold input** (default `0,02` in
  pct points, parsed pt-BR with comma or dot) filters the
  **Disponíveis** pane to only show groupings whose `|Δ|` is
  `< threshold` — i.e. the "safe to publish" ones. Groupings with `|Δ|
  ≥ threshold` (or with no delta data) are hidden so the user can't
  accidentally publish unreconciled numbers. Set the threshold to `0`
  to disable the filter and see everything. The filter never hides
  items already in **Selecionadas** — once the user picks something it
  stays visible regardless of threshold edits. A small badge on the
  Disponíveis header reports `X/Y < L%` while the filter is active.
  Threshold edits filter client-side only; company/date changes
  refetch deltas. When empresa + data are set but the eligibility
  query returns **no groupings** (and the user hasn't moved any to
  Selecionadas), the Disponíveis header shows a **"nenhum agrupamento
  não publicado"** badge (or **"nenhum agrupamento publicado"** in
  unpublish mode) so the operator knows the empty pane is the correct
  answer, not a stuck request — typical when every grouping on that
  date is already in the target state, or when NAV Groupings hasn't
  been calculated yet (only wallet-level navPackages exist, which the
  publish filter excludes via `groupingId != None`).
  The view also exposes an **Ação** radio toggle (Publicar /
  Despublicar) at the top of the filter card. Flipping it switches the
  picker source, the action endpoint (`/api/beehus/nav/publish` →
  `/api/beehus/nav/unpublish`), the |Δ| threshold (hidden in unpublish
  mode — no "safe to despublish" semantics), the run-button label/class
  (primary → danger), and every user-facing label in the view +
  confirm modal. In **publicar** the picker is date-aware
  (`/api/beehus/filters/groupings-by-publish-state?published=false`
  for `(companyId, positionDate)`) and the |Δ| column requires
  per-date navPackages, so changing the initial date refetches the
  picker (debounced 1500ms because Chromium `type=date` fires
  `change` per typed digit). In **despublicar** the picker is
  company-driven only (`/api/beehus/filters/groupings?companyId=…`
  returns every untrashed grouping for the company) — the operator
  pre-selects regardless of date and the per-day intersection with
  what's actually `published=true` happens inside `run()` via
  `groupings-by-publish-state`. Date changes in unpublish mode don't
  trigger a picker reload (it doesn't depend on the date) so typing
  the date doesn't churn the UI. The picker selection is cleared on
  toggle (eligibility model changes entirely between modes). The
  standalone **Despublicar Agrupamentos** view (`un-` prefix) is still
  available as a single-date shortcut without the faixa-de-datas /
  picker chrome.
- **Despublicar Agrupamentos** — mirror of Publicar but
  `published=true`. Action button is red/danger.

### Pipelines "por datas" (single-step)

> **Removido (jun/2026):** as pipelines multi-step **Fluxo diário**, **Reverter
> dia**, **Fluxo por datas** e **Reverter por datas** (grupo "Rotina Diária")
> foram **removidas** — views (`daily-flow`/`revert-flow`/`flow-dates`/
> `revert-dates`), controladores JS (`Fluxo`/`RevertFlow`/`FlowDates`/
> `RevertDates`), os 2 chips do favorites-bar, a init/allowlist do View router,
> o CSS `.fx-*` e a rota exclusiva `flow/latest-position-date` (último
> `publishedPositionSecurities` — fechou o item #7 do Mongo). As rotas
> compartilhadas (`process`/`nav/*`/`publish`/`unpublish`) **permanecem** para as
> pipelines single-step abaixo (grupos 2–4).

Cada pipeline single-step itera dias úteis (ou uma lista explícita de datas) e
dispara uma chamada upstream por dia. Step icons: `·` pending, `⟳` running,
`✓` done, `✗` error, `—` skipped.

| Card | Pipeline | Notes |
|---|---|---|
| **Processar por datas** | Processar Posições only | Single-step variant. Iterated over business days (or an explicit date list). Optional **Groupings (opcional)** two-pane transfer (mirrors the Transações picker) above the wallet picker — selecting groupings filters available wallets to the union of their `walletIds`; if no wallet is moved to "Selecionadas" but at least one grouping is, the upstream call is restricted to that wallet union. Empty groupings + empty wallets ⇒ "all wallets". |
| **NAV Wallets por datas** | Calcular NAV Wallets only | Same shape as Processar por datas, different endpoint — including the optional **Groupings (opcional)** dual-pane transfer with the same union-fallback semantics (if no wallet picked but groupings are, union of grouping walletIds is sent). **Skip-on-error**: days where upstream replies *"Nenhuma posição processada foi encontrada para as carteiras e a data informada"* are marked `—` and the loop continues to the next date (common when the range spans days that haven't been processed yet). |
| **NAV Groupings por datas** | Calcular NAV Groupings only | Single-step variant with a grouping picker (no wallet/grouping derivation). Empty selection ⇒ "all groupings". Iterated over business days or explicit date list. |
| **Publicar Agrupamentos por datas** | Publicar (per-day eligibility) | No picker. **Per day** looks up groupings with `navPackages.published=false` and publishes those. Days with nothing eligible are marked `—` (skipped) and the loop continues. Iterated over business days or explicit date list. |

The four "single-step por datas" pipelines share a single JS builder
(`makeDatesPipeline` in `templates/beehus_console.html`) parametrized by
`{prefix, kind, endpoint, payloadKey, stepLabel, skipOnError?}`. `kind`
determines the picker (`wallets` / `groupings` / `publish` — the last has
no picker and runs a per-day eligibility lookup before each upstream call).
Optional `skipOnError(body) → {reason}|null` lets a pipeline classify
specific upstream errors as a per-day skip instead of a hard stop (used by
NAV Wallets por datas to ignore days without processed positions).

**Bulk-upload groupings (Processar / NAV Wallets / NAV Groupings por datas
+ Publicar Agrupamentos)** — each of those views has a "⬆ Subir Excel de
groupings" button next to its picker title. On **Processar por datas**
the groupings uploader sits on the **Groupings (opcional)** card and the
**Wallets** card has its own "⬆ Subir Excel de wallets" uploader for
wallet ids. The .xlsx is sent to `POST
/api/beehus/util/parse-strings-excel` (every non-empty cell becomes a
string id, deduped, no header expected). For wallet pickers
(`kind='wallets'`) the resolved groupings are expanded to their
`walletIds` and added to "Selecionadas"; for grouping pickers
(`kind='groupings'`, plus the standalone Publicar Agrupamentos picker)
the recognised grouping ids are added directly to "Selecionadas",
bypassing the threshold filter. IDs not present in the empresa's
eligible groupings are counted in the status line.
For the por-datas views the user must select an empresa first; for
Publicar Agrupamentos both empresa **and** data must be set so the
non-published catalogue is loaded. Otherwise the upload bails with a
hint.

On Publicar Agrupamentos, when at least one id is dropped, the status
line reports the counts (e.g. `590 adicionado(s) · 32 ignorado(s)`). It
does **not** break the ignored ids down by reason: the diagnostic
endpoints that produced that breakdown (`grouping-id-classify` and the
manual `grouping-id-probe`) were the last direct `db.navPackages` reads
in the runtime and were removed — they needed trashed-inclusive,
cross-company, raw-typed docs that the API deliberately normalizes away.
If the raw per-id diagnosis is ever needed again, it returns as an
offline script alongside the ETL classifiers.

---

## Session-state preservation

The page has its own internal view router (`#create`, `#delete`,
`#process-dates`, …). To keep that state across sidebar round-trips and
full reloads, the parent shell mirrors it:

1. The child's `View.show(name)` writes its own `location.hash` **and**
   `postMessage`s `{type: 'viewChange', view: name}` to the parent.
2. The parent (`templates/shell.html`) keeps a `_lastDeep` map and
   writes the parent URL hash as `#<page>/<view>` via
   `history.replaceState` (so it doesn't trigger our own `hashchange`
   listener).
3. When `navigateTo(key)` (re)creates an iframe, it appends
   `#<lastDeep[key]>` to the iframe `src`. The child's boot script
   reads `location.hash` and calls `View.show(_initialView)`, landing
   exactly where the user left off.

Drill-downs with `extraParams` ignore the deep hash by design — those
are explicit context switches.

---

## API call log

Every call from `templates/beehus_console.html` goes through the shared
`api(method, path, body)` helper, which timestamps it and writes it
into the `ApiLog` ring buffer (newest first, `MAX_STORED = 1000`). The
header badge shows the **running session total** (so you know if older
entries have rolled off). The modal exposes:

- **Copiar** — drops the full log (with metadata: `exportedAt`, `total`,
  `stored`, `maxStored`, `entries`) onto the clipboard as JSON via
  `navigator.clipboard` (with a hidden-textarea + `execCommand` fallback).
- **Limpar** — empties both the ring buffer and the running total.

ESC closes any open modal (token, log, every confirm modal).

---

## Reinforcement pipeline (offline scripts)

The reinforcement table at `data/identify_transactions_reinforcements.json`
drives the Tier-0/1/2 lookup inside `_lookup_reinforcement` (see the
"Identificar Transações" section above). Rules can be added through the
UI one at a time, but the bulk of the table is now seeded from
production history via the scripts under `scripts/`. They share a
common normalisation/masking module — `reinforcement_keys.py` — so a
key written by any of them lines up exactly with the key the live
lookup will derive from an incoming transaction description.

| Script | What it does | When to use |
|---|---|---|
| `list_entities_by_volume.py` | One-shot triage: prints entities by transaction volume + current classification coverage + how many recurring descriptions would qualify for a rule. | First step before pointing any of the other scripts at a new entity. |
| `build_reinforcements_from_history.py` | Aggregates one entity's transactions, groups by (normalised description, securityId, beehusTransactionType), and emits a rule for every combination that meets `--min-hits`, `--dominance`, and `--min-informative-share`. **Additive only** — existing keys are preserved (the first writer wins). Emits three rule kinds: full, type-only (no securityId), and security-only. | Per-entity seeding; pass `--apply` to actually write. |
| `build_reinforcements_all_entities.py` | Wrapper that runs `build_reinforcements_from_history.py` against every entity with at least `--min-entity-volume` transactions. Iterates highest-volume first; the additive contract means re-runs are safe. | Backfill the entire org from history in one command. |
| `migrate_reinforcements_normalization.py` | Re-keys existing rules after a change to `reinforcement_keys.normalize_reinforcement_key`. Detects **ambiguous collisions** (multiple old keys collapsing to the same new key with *different* securityIds) and drops them rather than mergeing silently. Drops a timestamped `.bak` next to the file. | Whenever a new masking pattern is added to `mask_variable_tokens`. |
| `seed_reinforcements_from_csv.py` | Bulk import from a CSV of `description,type,securityId` rows. Bootstraps the first manual classification for descriptions that have no historical signal (e.g. fund names never tagged in production). | Preparing a "starter pack" of rules from operator knowledge / from an Excel sheet. |
| `test_reinforcements_on_month.py` | Plays back a date range through the live lookup and reports coverage (exact + substring + miss) and accuracy (type / security) against the stored ground truth. Snapshots the rules dict once at start to avoid OneDrive read races. | Audit accuracy after a rebuild or before/after a migration. |
| `audit_reinforcement_rules.py` | Tallies per-rule hit / miss counts on a recent window, flags rules whose miss rate exceeds `--miss-threshold`, and (with `--apply`) auto-updates rules to the majority class when the winner is unambiguous (≥70% of the labelled hits). | Periodic cleanup of rules drifting away from the operator's current convention. |
| `inspect_unmatched_desc.py` | For a substring of `description`, dumps every distinct normalised key in the entity's history along with the (securityId, type) distribution per key. | Debugging a stubbornly-unmatched description — figures out why the build skipped it. |

### Pipeline contract

All scripts share the same normalisation pipeline and writing
discipline:

1. **Normalisation** — `reinforcement_keys.normalize_reinforcement_key`
   pipes the raw description through
   `transaction_type_classifier.normalize` (mojibake fix → strip
   accents → UPPER → collapse spaces) and then
   `mask_variable_tokens` (replaces per-event identifiers with stable
   placeholders: `<OPCODE>` for `\d{2}[A-Z]\d{7,8}`, `CDB<CODE>` for
   `CDB\d{4}[A-Z]{4}`, `REF. <PERIOD>` for `REF.\s*[A-Z]{3}\d{2}`, and
   strips the leading transaction-type counter `^([A-Z][A-Z ]*?)_\d+`
   so `RENDIMENTO_6 - ...` / `RENDIMENTO_7 - ...` collapse to a single
   `RENDIMENTO - ...` rule; and `<ACCT>` for standalone 4-8 digit
   account/product codes in either bracketed (`[54582]`, `(53632)`) or
   trailing (`... FI 54582`, `... FI - 54582 -`) form). The counter strip
   is anchored at `^` and limited to a letters/spaces prefix, and the
   `<ACCT>` strip is guarded (lookbehind + word boundaries + trailing-only
   for the bare form), so neither ever touches a real discriminator — a
   CNPJ (`.../0001-97`), ISIN (`IE0030624948`), maturity token
   (`NTNB_07032006_15052035`), rate (`IPCA+10,00%`), or mid-string bank
   routing block (`... CTA 31359001 - ...`). Rates and dates are
   deliberately **not** masked: across the live rule set they discriminate
   distinct securities, and masking them collapses zero duplicates.
2. **Atomic write** — every write goes through `db.atomic_write_json`
   (tempfile + `os.replace`) so partial writes are impossible.
3. **Cache invalidation** — production reads via
   `_load_reinforcements` cache the rules dict by file mtime; the
   `os.replace` from `atomic_write_json` bumps mtime atomically, so
   the next call sees the new state without any explicit
   invalidation.

### Defensive thresholds

- **`--min-informative-share`** (default 0.30) — skip descriptions
  where less than 30% of rows have *any* classification. Prevents
  extrapolating a small classified slice onto a large unlabelled mass.
- **`--dominance`** (default 0.80) — the picked (securityId, type)
  pair must hold at least 80% of the *informative* subset.
- **`_MIN_KEY_LEN = 4`** in the build, **`_TIER2_MIN_KEY_LEN = 10`**
  in the lookup — keys shorter than 4 chars are never emitted; bare
  single-word keys 4–9 chars are reachable only by Tier-1 (exact).
  Single-word keys like `SAQUE` or `CUPOM` would otherwise gain enormous
  false-positive surface via Tier-2 substring matching.
  - **Multi-word exception (`_is_tier2_eligible`, `_TIER2_MIN_MULTIWORD_LEN
    = 6`)**: a key that contains a space is far more specific (two adjacent
    tokens) and qualifies for Tier-2 from 6 chars — so a short snippet like
    `COR JSCP` (8 chars) matches `COR JSCP ITUB4` / `COR JSCP BBDC4` instead
    of staying exact-only. Bare short single words remain exact-only.
- **Tier-0 negating prefixes** — `IR -`, `IRRF`, `IOF`, `DEBITO IOF`,
  `DEBITO CBLC IRRF` strip and lookup the inner description, then
  force the type to `taxes` (security from the inner rule is
  preserved). See `reinforcement_keys.strip_negating_prefix`.

---

## Performance notes (caching & memoization)

`identify-transactions/identify`, `transactions/search` and the `filters/*`
routes are **CPU-bound, not DB-bound** — the slow-request profiler
(`db_profiler.py`, env `DB_PROFILE_SLOW_REQUEST_MS`) reports them with
"0 mongo ops". The hot work is Python looping over the ~16k-security cache
and the reinforcement table, so the optimizations are all about **doing
constant per-security / per-rule work once** instead of per transaction.

- **Per-security derived data is memoised on the cache dict.** The
  `SecurityCache` dicts are long-lived (reloaded at most once per day) and
  shared across WSGI threads. Scoring caches its derived data on each dict
  under underscore-prefixed keys, computed lazily on first use and reused for
  every subsequent transaction:
  - `security_matcher._candidate_dates` → `_cand_dates` (frozenset of maturity
    dates from `maturityDate` + dates parsed out of `beehusName`).
  - `security_matcher._candidate_name_tokens` → `_name_tokens` (frozenset of
    accent-stripped `beehusName` tokens for the name-overlap score).
  - `transaction_security_classifier._name_substring_bonus` → `_name_c` /
    `_main_c` (compressed `beehusName` / `mainId`).
  - `transaction_security_classifier._score_l3` → `_hay` (the uppercased
    search blob the L3 sweep pre-filters on). This was previously rebuilt for
    **all 16k securities on every transaction** — the single biggest cost.

  These are `frozenset` / `str` (immutable) and must never be mutated by
  callers. They are **not persisted**: `SecurityCache._save` serialises only
  `_id` + `_CACHE_FIELDS`, so the memos never leak into
  `data/securities_cache.json` and a concurrent daily reload can't trip a
  `set`-not-serialisable / dict-changed-size error.

- **`_SecLookup` rebuilds its `{_id: sec}` index once per cache reload, not
  per request.** `reset_request_cache()` resets only the wallet candidate
  pool; the id index self-invalidates against `SecurityCache.loaded_date`.

- **Reinforcement Tier-2 eligibility is precomputed per file-load.** The list
  of `(key, rule)` pairs with `len(key) >= _TIER2_MIN_KEY_LEN` lives in the
  sibling cache field `_REINFORCEMENTS_CACHE["eligible"]` (NOT inside the
  returned state dict — that dict gets written back to disk on save).
  `_eligible_rules(state)` serves it only when `state` is the cached object
  (identity check); writes (`_record_reinforcement`, reinforcement DELETE)
  reset the cache mtime so the next read rebuilds rules + eligible together.

- **Filter responses are cached (5-min TTL via `db._cached_ttl`).**
  `filters/securities` (the full ~16k catalog, sorted) under a process-wide
  key; `filters/entities` / `filters/wallets` keyed by `companyId`
  (+ `groupingId`). Trade-off: a newly-registered wallet/entity can take up
  to 5 min to appear in the dropdowns — consistent with the pre-existing
  5-min TTL on `get_wallet_names()` / `get_entity_names()`. Returned lists are
  shared-immutable; do not mutate them in place.

---

## Adding a new functionality — checklist

When introducing a new view that mirrors an existing template:

1. **Backend client** — add a function under the appropriate `beehus_api/<area>.py` (or create a new file for a new upstream namespace). Export via `beehus_api/__init__.py`.
2. **Flask route** — add a forwarder in `pages/beehus_console.py`. Always:
   - Validate required body fields and return 400 on missing.
   - Enforce `company_visible(companyId)` and return 403 on mismatch.
   - Wrap the upstream call in `try/except BeehusAPIError → _api_error_response(e)`.
3. **HTML view** — add a menu card in the right section, a `<div class="view max-w-6xl" data-view="<key>">` block following the three-card template (filters → action → result), and a confirm modal.
4. **JS module** — follow the established shape: `init()` loads filter dropdowns, `onCompanyChange()` cascades, `confirm()` validates + opens the modal, `run()` does the API call and `Token.refresh()`s, `reset()` resets state.
5. **Wiring** — register the lazy-init in `View.show()`, add the new view key to the URL-hash allow-list at the bottom of the file.
6. **Update this doc** — add the new route to the action-routes table and the menu entry to the appropriate UI section.
