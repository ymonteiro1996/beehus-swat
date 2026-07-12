# Plano — migração restante MongoDB → API Beehus

> **Objetivo:** tirar o dashboard (runtime `app.py` + `pages/*.py` + módulos
> importados) do acesso direto ao MongoDB, página por página, até poder
> desligar a conexão Mongo. Este doc é o **plano forward-looking**; o histórico
> "as-built" do que já foi migrado está em [ANALISE_API_VS_MONGO.md](ANALISE_API_VS_MONGO.md).
>
> **Estado atual (jun/2026):** o **fallback Mongo do read-seam foi REMOVIDO** — a
> API Beehus é a **fonte única** das coleções migradas (quando a API falha/vem
> vazia, o helper retorna vazio/None e a UI não mostra; sem leitura Mongo). O
> Mongo segue load-bearing **apenas** para os **gaps fora do seam** (securityPrices,
> securityEvents, by-id, cross-empresa, classificadores, escritas).
> A migração cobriu `securities`, `securityMappings`, `companies`, `entities`,
> `cashAccounts`, `provisions` (16/17), `issues` (1/5 bloqueantes), `wallets`,
> `groupings`, `processedPosition`/`unprocessedSecurityPositions` (no que A/B
> servem), `transactions`, e os reads **consolidados** de `navPackages` (→ `/results`).
>
> **Restrição operacional:** a API **rate-limita (429)** em ~1500 chamadas.
> Qualquer passo que faça fan-out por empresa/entidade precisa ser **lazy/sob
> demanda**, nunca um warm em massa (foi por isso que o warm-all foi removido).

---

## Inventário do que ainda puxa do Mongo (código vivo)

### Grupo A — reads com wrapper de API JÁ existente, ainda não migrados
Trabalho **só de dashboard** (o endpoint existe; falta trocar a chamada).

| Coleção | Endpoint / wrapper | Onde (telas) | Padrões que exigem cuidado |
|---|---|---|---|
| `processedPosition` | **A** `get_processed_position(companyId, walletIds, date)` | conciliacao, beehus_console, controlpanel, repetir_posicoes, precificacao, excecoes, carteira | "posição **anterior**" / "mais recente ≤ D" / `$in` de várias datas / `aggregate $group $max\|$first` → resolver no cliente sobre janela, ou N chamadas. **A só expõe `date=` única.** |
| `transactions` | **G** `list_transactions(...)` | conciliacao, beehus_console, controlpanel (`aggregate`), conciliacao_unprocessed, precificacao, excecoes | agregações por carteira/data → cliente. Há **1 escrita** `transactions.update_one` (espelho local no console). |
| `unprocessedSecurityPositions` | **B** `get_unprocessed_security_positions(...,initialDate,finalDate)` | carteira, conciliacao_unprocessed (`find/distinct/find_one`), controlpanel, repetir_posicoes, excecoes | B **tem range** → mais fácil. `distinct(positionDate)` / "mais recente" / `sort -1 limit` → cliente. |
| `groupings` | **H** `list_groupings(companyId)` | `db.get_grouping_index` (**scan all-company**), [controlpanel.py:196](../pages/controlpanel.py#L196) `aggregate` | H é **por empresa**; `get_grouping_index` varre todas → fan-out (ver Grupo B). |

### Grupo B — dados de referência em `db.py` que leem `wallets` (linchpin)
**Sem API por-id nem all-wallets.** `partner_wallets` (**F**) é **por empresa**.

| Helper (`db.py`) | Lê | Usado por |
|---|---|---|
| `resolve_wallet(id, projection)` | `db.wallets.find_one({_id})` | **`beehus_catalog._company_of_wallet`** (resolve companyId p/ cash/provisions), e várias páginas |
| `get_wallet_names` | `db.wallets.find({})` | nomes em quase toda página |
| `get_wallet_currencies` | `db.wallets.find({currency})` | desambiguação de datas US em transações |
| `valid_wallet_ids` | `db.wallets.find({})` | validação de carteira |
| `build_wallet_map` | `db.wallets.find({})` | mapa carteira→(empresa,entidade) do painel |

> **`resolve_wallet` é o nó crítico:** o próprio catálogo depende dele para
> descobrir o `companyId` de uma carteira (cash, provisions). Migrar `wallets`
> é pré-requisito de fechar o catálogo. Como `resolve_wallet` é chamado **sem
> companyId**, precisa de um endpoint **wallet-by-id** ou de um **cache global
> de carteiras** (ver decisão na Fase 1).

### Grupo C — GAP: SEM API (exige endpoint NOVO no backend Beehus)

| Coleção | Onde | O que lê |
|---|---|---|
| `securityPrices` (`historyPrice`) | [precificacao.py:125-149](../pages/precificacao.py#L125), [controlpanel.py:984-1001](../pages/controlpanel.py#L984), [repetir_posicoes.py:3179](../pages/repetir_posicoes.py#L3179) | série de PU por `securityId` (+ por data) |
| `securityEvents` (cupom/amort) | [repetir_posicoes.py:1353](../pages/repetir_posicoes.py#L1353) | eventos por `securityId`/`operationDate`/`eventType` (curva) |

> Estes **bloqueiam o "zero Mongo"** e **não são trabalho de dashboard** —
> dependem do time da API Beehus expor os endpoints. Correção vs. análise
> antiga: **`fxRates` NÃO é mais Mongo** (migrou para arquivo local,
> [excecoes.py:1445](../pages/excecoes.py#L1445)); `palettes` e `users` foram
> **removidos** (sem leitor vivo).

### Grupo D — exceções intencionais e escritas (decidir: manter vs endpoint novo)

| Item | Onde | Situação |
|---|---|---|
| `issues` (controlpanel) | **contagens do grid /rows MIGRADAS para E** (pre-processing, fan-out por empresa via `preprocessing_status_many`) — 6 tipos + `processedWallets`, paridade 1:1 validada. Os 2 tipos de **explosão foram REMOVIDOS** do Painel (E não os expõe). **date-cards: contador removido** (só mostra as datas = dias úteis, sem DB). **Tela inicial do Painel 100% Mongo-free.** Ainda no Mongo só os **drill-downs on-click** (não a tela inicial): **issues-summary** e **detail** (lista de docs). | sem endpoint geral de issues p/ o resto. |
| `provisions` `find_one({_id})` | [conciliacao.py](../pages/conciliacao.py) (validação pré-delete) | **sem GET por id**. 1 site. |
| `navPackages` (diagnóstico) | beehus_console (trashed/cross-company) | **proposital** (inspeção de estado bruto). *(global-analysis NÃO lê mais navPackages; scenario-capture REMOVIDO.)* |
| `publishedPositionSecurities` `find_one` | [beehus_console.py:177](../pages/beehus_console.py#L177) | talvez via **I**; senão gap. |
| `diagnosticFeedback.insert_one` (**escrita**) | [conciliacao.py:2150](../pages/conciliacao.py#L2150) | feedback do diagnóstico; sem endpoint de escrita → fica local ou pedir endpoint. |
| `transactions.update_one` (**escrita**, espelho) | beehus_console | some quando os reads vierem de **G**, ou vira endpoint de mutação. |

### Grupo E — infraestrutura (só removível depois de A–D zerados)
- **Mongo-only:** `MongoClient`/`_DbProxy`/`swap_mongo_client`/`ensure_indexes` ([db.py](../db.py)), `db_profiler.py` (monitor pymongo), `pages/setup.py` ("Conexão DB").
- **LOCAL — permanece** (não é Mongo): `today_in_brt`, `get_biz_dates`, `biz_days_*`, `atomic_write_json/text`, loaders de config (`load_config*`, `load_settings`), `get_company_filter`/`company_visible`, `cell_cls`/`wallet_cls`, `_cached_ttl`. Ao desligar o Mongo, isto migra para um módulo `core`/`config` (ou um `db.py` enxuto).
- **Hardening final — ✅ FEITO (jun/2026):** flag `ALLOW_MONGO_FALLBACK` e **todos** os branches `_*_from_mongo` removidos do catálogo; `db.resolve_wallet` mantém Mongo só p/ o gap de campo `currencyId`. API é fonte única; quando não há dado, a UI não mostra.

### Grupo F — ETL offline (fora do escopo do dashboard)
~40 `scripts/*` leem Mongo direto. A maioria precisa de
estado bruto sem API (dumps rawbtg, auditoria de reforços). **Recomendação:**
fora do alvo "dashboard zero-Mongo"; mantêm conexão própria se necessário.

---

## Endpoints NOVOS a pedir ao backend (dependência externa — caminho crítico)

> **Pedido consolidado, pronto para entregar ao backend, em
> [PEDIDO_BACKEND_MONGO.md](PEDIDO_BACKEND_MONGO.md)** — cada endpoint mapeado aos
> call sites atuais (file:line), com shape de request/response e critério de aceite.

Resumo por impacto (detalhe + pontos de uso no doc acima):

1. **`processed-position` com range + sort/limit** — destrava ~21 reads "latest ≤ D" / recent-N / `aggregate $group`. *Maior impacto.*
2. **`securityPrices`** — série `historyPrice` por `securityId` (e ponto por data). *Bloqueia precificação + curva (7 pontos).*
3. **`currencyId` no payload de `wallets`** (`partner_wallets`/by-id) — destrava `resolve_wallet` (linchpin do seam) + excecoes (9 pontos).
4. **`transactions`/`provisions` por id** — 4 pontos, baixo esforço.
5. **`securityEvents`** — eventos (cupom/amort) por `securityId`. *Bloqueia curva.*
6. **`issues`** drill-down por empresa+data (lista/detalhe — contagens do grid já vêm do E).
7. **última data publicada** (`publishedPositionSecurities`) — 1 ponto.

---

## Sequência recomendada (menor risco/dependência → maior)

> Cada fase entrega valor isolado e é faseável por página. "Pronto" = nenhuma
> leitura Mongo daquele grupo em código vivo + validado vs Mongo com **1
> empresa/1 data** (nunca fan-out — risco 429).

- **Fase 0 — Endurecer o já migrado. ✅ FEITO.** Shapes validados com token
  (securities, mappings, companies, entities, `/results`, provisions, cash);
  **fallback removido** — flag `ALLOW_MONGO_FALLBACK` e branches `_*_from_mongo`
  apagados; API vira fonte única. *Removeu a dependência silenciosa do Mongo.*
- **Fase 1 — Camada `wallets` (Grupo B).** Decisão de arquitetura:
  **(a)** pedir endpoint wallet-by-id/all-wallets (limpo, preferido), ou
  **(b)** cache global de carteiras montado **lazy** via `partner_wallets` por
  empresa (cuidado 429). Reescreve `resolve_wallet`/names/currencies/valid_ids/
  build_wallet_map sobre a fonte nova. **Destrava o catálogo (cash/provisions).**
- **Fase 2 — `processedPosition` + `unprocessedSecurityPositions` (A, B).**
  Snapshots por página: carteira → conciliacao(_unprocessed) → repetir_posicoes
  → precificacao → excecoes → controlpanel → beehus_console. Agregações
  ("última/anterior") no cliente sobre janela. *Prereq: confirmar semântica de
  data do A (range vs N chamadas).*
- **Fase 3 — `transactions` (G)** + aposentar o espelho `transactions.update_one`.
- **Fase 4 — `groupings` (H)** em `get_grouping_index` + `controlpanel:196`
  (mesma decisão de fan-out da Fase 1).
- **Fase 5 — GAPs (BLOQUEADORES, dependem do backend):** `securityPrices`,
  `securityEvents`, e (se for tirar do Mongo) issues gerais / provisions-by-id /
  publishedPositionSecurities / escritas. *Não começa sem os endpoints novos.*
- **Fase 6 — Decomissionar o Mongo.** Com A–E zerados e gaps cobertos: remover
  `MongoClient`/`_DbProxy`/`ensure_indexes`/`db_profiler`/`pages/setup.py`;
  enxugar `db.py` para os helpers locais (mover p/ `core`/`config`); tirar as
  deps `pymongo`/`bson`/`certifi`/`dnspython` do runtime do dashboard; trocar a
  tela "Conexão DB" por setup só-de-token. Decidir o destino dos `scripts/`.

## Caminho crítico (resumo)
```
Fase 0 (hardening) ─┐
                    ├─► Fase 1 (wallets) ─► Fase 2 (posições) ─► Fase 3 (transactions) ─► Fase 4 (groupings) ─► Fase 6 (desligar Mongo)
backend: wallets ───┘                                                                     ▲
backend: securityPrices + securityEvents ────────────────► Fase 5 ────────────────────────┘
```
O dashboard **não chega a zero-Mongo** sem os endpoints novos (securityPrices,
securityEvents, wallets-by-id). Tudo de Fase 0–4 é factível só com o que a API
já expõe.

---

## Status de execução (jun/2026)

**Feito e validado ao vivo** (paridade contagem + valores, 1 empresa/1 data):
- **Fase 0**: gate documentado e **executado** — fallback Mongo do seam REMOVIDO (flag `ALLOW_MONGO_FALLBACK` + branches `_*_from_mongo`); API é fonte única. `db.resolve_wallet` lê Mongo só p/ o gap `currencyId`.
- **Fase 1 (wallets)**: índice global no catálogo (`wallets_index`/`wallet_doc`/`wallet_names`/`wallet_currencies`/`valid_wallet_ids`/`wallet_pairs`/`wallets_for_company`); `db.py` delega; `_company_of_wallet` usa o índice.
- **Fase 4 (groupings)**: `grouping_index`; `db.get_grouping_index` delega; `controlpanel._groupings_by_company` derivado.
- **Fase 2 (posições)** — helpers A/B no catálogo: `processed_doc`/`processed_positions_map`/`processed_existing_wallets`, `unprocessed_doc`/`unprocessed_existing_wallets`/`unprocessed_dates_for_wallet`/`unprocessed_docs_map`/`unprocessed_sid_uid_map`. Páginas migradas: `conciliacao_unprocessed` (B), `carteira` (A+B), `conciliacao` (6 detalhe exact-date), `controlpanel` (grid), `excecoes` (incl. 2 batches reescritos sem N+1), `repetir_posicoes` (3 sites).
  - `unprocessed_sid_uid_map(company, walletIds, on_or_before)` → `{walletId: {securityId: unprocessedId}}` lido DIRETO do snapshot bruto (`securities[].preProcessingData.securityId → .unprocessedId`), todos os snapshots ≤ data, mais novo vence. Substituiu o cruzamento com `securityMappings` (que COLIDE, ~38% do catálogo) em `carteira._unprocessed_id_maps` e `repetir_posicoes._unprocessed_id_map`: superconjunto estrito (0 regressão, 0 divergência de uid, +ativos resolvidos). Helpers `unprocessed_latest_map`/`unprocessed_latest_on_or_before` removidos (sem mais chamador).
- 🐛 Corrigido: `positionDate` no FUTURO (2027) era truncado por `_nav_today()` como teto de range (`unprocessed_dates_for_wallet`, `nav_series_for_entity`, `_warm_nav_from_api`) → `2999-12-31`.

**LIMITAÇÃO CONFIRMADA do endpoint A** (validado ao vivo): `processed-position`
é **single-date, exige `walletIds` explícito (vazio→0), sem sort/limit/range**.
Logo **NÃO serve** e ficam no Mongo (leitura direta na página, sem fallback de seam) até o backend evoluir o A:
- "N datas mais recentes ≤ D" / `sort+limit` (beehus_console exec-prices L1/L2 + histórico — 7 reads).
- "latest ≤ D" / "< date" por carteira (precificacao 2; controlpanel position-changes).
- aggregates `$group $max/$first` (repetir 784; excecoes 332). *(O former-nav do global-analysis da conciliacao foi removido — gap em R$ vem pronto de `financialValueReturnDifference` no `/results`.)*
- ~~contagem cross-empresa whole-collection (controlpanel `_processed_count_by_company`)~~ **MIGRADO**: `processedWallets` vem do endpoint E (pre-processing) via fan-out por empresa; `_processed_count_by_company` removido.
- janela de datas (conciliacao 3-sigma, intencional).

**Pedido extra ao backend** (além de securityPrices/securityEvents/wallets-by-id):
**`processed-position` com range de datas + sort/limit** destravaria ~21 reads
hoje presos no Mongo por essa limitação.

**Fase 3 (transactions G) — COMPLETA.** Helper `transactions_search`/
`transactions_on_date` (validado ao vivo: balance, security_ids, range 929==929).
Migrados em conciliacao (single + multi-wallet), conciliacao_unprocessed, excecoes,
repetir_posicoes, precificacao, beehus_console (search), **controlpanel coluna
TXN** (não-identificadas por empresa → `transactions_search_many`, fan-out por
empresa na data; paridade 1:1 validada em 5 datas). **Ficam no Mongo (intencional):**
by-`_id` (conciliacao 713, console 2410/2916 — sem endpoint), e o espelho-write
`transactions.update_one` (console 802 — até
todos os reads saírem do Mongo). Migração via workflow paralelo + verify adversarial
(pegou/corrigiu 1 bug de paridade no console).

**Sweep `db.wallets` inline — COMPLETO.** Reads inline (name/_id/companyId/entityId/
accountCode) migrados ao índice (helpers `wallets_in_company`/`wallet_company_map`/
`wallet_entity_map` + `wallets_for_company`/`wallet_doc`, validados ao vivo) em
config/correcoes/carteira/controlpanel/console/excecoes/repetir/conciliacao/
precificacao (via workflow paralelo + verify adversarial). **Ficam no Mongo
(intencional):** reads de `currencyId` em batch (excecoes — wallet.currencyId NÃO
existe na API; `currency`-string existe e foi usada onde possível). find_one por _id
usa `db.resolve_wallet`, que lê Mongo **só** p/ o gap de campo `currencyId`; quando a
carteira NÃO está no índice da API, retorna None (sem mais fallback).

**Scenario-capture REMOVIDO (jun/2026):** a feature "Capturar Cenário" (botão +
modal + rotas capture/analyze/implement/list + helpers) e o diretório
`tests/scenarios/` foram **apagados** — eliminou os reads brutos de Mongo
(wallets/navPackages/processedPosition/transactions) que ela fazia.

**Restante (tudo backend-dependente):** Fase 5 — `securityPrices`/`securityEvents`
(sem API) + `processed-position` com range/sort (destrava os reads "latest≤D"/
recent-N/aggregate hoje no Mongo) + wallet-by-id com currencyId (ou aceitar Mongo).

> **Estado:** o dashboard agora lê da **API Beehus para tudo que a API expõe**.
> O que resta no Mongo é, sem exceção, ou **gap sem endpoint** (securityPrices/
> securityEvents/currencyId), ou **limitação do endpoint A** (single-date, sem
> sort/range/aggregate), ou **by-id sem endpoint**, ou **cross-empresa aggregate**,
> ou **write-mirror** intencional — leituras Mongo **diretas nas
> páginas** (gaps fora do seam), documentadas. O **read-seam não tem mais fallback**:
> a API é fonte única e, quando ela não serve, a UI não mostra ("quando não houver,
> não mostrar").
