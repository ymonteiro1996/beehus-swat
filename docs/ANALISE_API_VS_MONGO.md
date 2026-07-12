# Análise: substituir leituras diretas do MongoDB por APIs Beehus

> **Nota (jun/2026): a página _Tombamento_ foi REMOVIDA deste projeto** — migrada
> para o standalone `SWAT\tombamento\` (sem MongoDB, travado em Oikos). As seções
> e linhas que tratavam especificamente dela foram podadas deste documento.

> Engenharia reversa do runtime do dashboard (jun/2026). Mapeia **toda
> funcionalidade viva** que lê `processedPosition`, `unprocessedSecurityPositions`,
> `securities` e `securityMappings` direto do Mongo, e avalia se os **endpoints
> GET já existentes** da API Beehus podem substituir cada leitura.
>
> Escopo "vivo" = `app.py`, `pages/*.py` e os módulos que eles importam
> (`db.py`, `security_matcher.py`, `transaction_security_classifier.py`,
> `security_type_classifier.py`). `scripts/*` são ETL
> offline e ficam fora (continuam Mongo direto).

## Descoberta principal (TL;DR)

Houve um plano (change OpenSpec `build-node-read-api`, **descartado e removido**)
de **construir uma API Node nova** para substituir o Mongo. **Não foi necessário**:
a API Beehus **já expõe endpoints GET de leitura** para quase tudo que pedimos. O
caminho adotado foi **embrulhar esses GETs em `beehus_api/`** (como já fizemos com
escrita) e trocar `db.X.find(...)` por chamada HTTP, página por página — sem subir
serviço novo.

Dois desses endpoints (`list_groupings()` = **H** e `get_nav_contribution()` = **I**)
**já estavam embrulhados e em uso** antes desta análise — a base sobre a qual o
restante da migração foi construído.

## Endpoints de leitura disponíveis (inventário)

| # | Endpoint GET | Coleção que substitui | Wrapper em `beehus_api/` |
|---|---|---|---|
| **A** | `/beehus/financial/positions/processed-position?companyId&walletIds&date` | `processedPosition` | ✅ `get_processed_position()` |
| **B** | `/beehus/financial/positions/unprocessed-security-positions?companyId&walletIds&initialDate&finalDate` | `unprocessedSecurityPositions` | ✅ `get_unprocessed_security_positions()` |
| **C** | `/beehus/securities` | `securities` | ✅ `list_securities()` |
| **D** | `/beehus/financial/security-mappings` | `securityMappings` | ✅ `get_security_mappings()` |
| **E** | `/beehus/financial/positions/processed-position/pre-processing?positionDate&companyId` | status do painel | ✅ `get_preprocessing_status()` |
| **F** | `/beehus/partner-info/{companyId}/wallets` | `wallets` | ✅ `partner_wallets()` |
| **G** | `/beehus/financial/transactions?companyId&walletIds&groupingIds&entityIds&securityIds&dateType&initialDate&finalDate` | `transactions` | ✅ `list_transactions()` |
| **H** | `/beehus/grouping?companyId` | `groupings` (+ `wallets` populados) | ✅ `list_groupings()` |
| **I** | `/beehus/consolidation/nav-contribution-calculation?id&companyId&type=grouping\|wallet&initialDate=2000-01-01&finalDate` | série `navPackages` por entidade | ✅ `get_nav_contribution()` |
| **J** | `/beehus/consolidation/nav-contribution-calculation/results?companyId&positionDate` | **consolidado por empresa+data** (todas carteiras+agrupamentos, contagens, divergência pré-calc.) | ✅ `get_nav_results()` — **1 chamada/empresa, ao vivo**; validado 1:1 vs navPackages. Substitui o warm por-entidade nas telas consolidadas |

→ **Todos os 9 endpoints (A–I) agora têm wrapper.** Há ainda `list_companies()`
(`/beehus/partner-info/...`) para a coleção `companies`.

## Status de implementação (atualizado)

> **⚠️ ATUALIZAÇÃO jun/2026 — fallback Mongo REMOVIDO.** As menções a "fallback
> automático ao Mongo" / `ALLOW_MONGO_FALLBACK` abaixo são **históricas**. O flag
> e **todos** os branches `_*_from_mongo` do `beehus_catalog.py` foram apagados: a
> API Beehus é a **fonte única** das coleções migradas. Quando a API falha/vem
> vazia, o helper retorna vazio/None e a UI **não mostra** (sem leitura Mongo).
> `db.resolve_wallet` mantém Mongo só p/ o gap de campo `currencyId`. O Mongo
> restante são **leituras diretas nas páginas** (gaps fora do seam:
> securityPrices/securityEvents/by-id/cross-empresa/classificadores/escritas).

Feito nesta rodada:

1. **Wrappers GET** A, B, C, D, E criados em `beehus_api/` (G/H/I já existiam;
   F = `partner_wallets`). Exportados em `beehus_api/__init__.py`.
2. **`beehus_catalog.py`** — camada cacheada (TTL 5 min) sobre `list_securities`
   e `get_security_mappings`, com **fallback automático ao Mongo** (flag
   `ALLOW_MONGO_FALLBACK`) e validação de shape, para não quebrar o dashboard
   enquanto o contrato da API não é confirmado em produção.
3. **`db.get_security_names()`** passou a delegar ao catálogo (API → fallback
   Mongo). Migra de uma vez todos os consumidores do mapa de nomes.
4. **`securityMappings.find_one` migrado** em `controlpanel`, `carteira`,
   `excecoes`, `repetir_posicoes`, `conciliacao_unprocessed` →
   `beehus_catalog.security_mappings_doc()` / `security_mapping_id()`.
5. **Todos os `db.securities.find/find_one` migrados** ao catálogo (API →
   fallback Mongo): `conciliacao` (12 sites), `precificacao` (busca/detalhe/
   resolução/upload), `excecoes` (benchmarks via `benchmarks()` + CDI + mainId),
   `controlpanel`, `beehus_console`, `carteira`, `conciliacao_unprocessed`,
   `repetir_posicoes`, e os full scans de `security_matcher.load_from_db` e dos
   classificadores (`security_type_classifier`, `transaction_security_classifier`).
   **Não há mais nenhuma leitura de `securities`/`securityMappings` em código vivo**
   (só restam menções em comentários e nos `scripts/` offline). Helpers do
   catálogo: `securities_by_ids`, `security_doc`, `security_by_main_id`,
   `security_names`, `search`, `benchmarks`, `all_securities`,
   `security_mappings_doc`, `unprocessed_to_security`, `security_to_unprocessed`,
   `all_security_mappings`. Lógica validada por smoke-test com payload stub.

Pendente (próxima rodada — exige confirmação do backend antes de migrar):

- **`processedPosition` / `unprocessedSecurityPositions`** (endpoints A/B) e
  **`transactions`** (G). Os wrappers existem, mas a migração das ~43 leituras de
  posição envolve padrões que A/B podem não cobrir 1:1 (posição **anterior**,
  "mais recente ≤ D", `$in` de várias datas, agregações "última por carteira").
  **Pré-requisito:** confirmar se A aceita faixa/`$lte` de data (como o B) e o
  shape de `pre-processing` (E). Por isso ficaram fora desta rodada.
- Coleções **sem API** (seguem Mongo): `securityPrices`, `securityEvents`,
  `fxRates`, `navPackages` (divergência), `palettes`.

## Rodada 3 — cashAccounts, provisions, issues, companies, entities

Endpoints adicionais fornecidos: `GET /beehus/provisions` (provisions),
caixa via array `cashAccounts` da resposta de `processed-position`, issues via
`pre-processing`, `GET /beehus/entities`, `GET /beehus/partners/companies`.
Wrappers: `list_provisions`, `list_entities`, `list_companies`. Helpers no
catálogo: provisions_active/lifecycle_sids/overlapping/search,
cash_sums_by_dates/cash_accounts_docs/cash_unprocessed_ids,
blocking_issues_by_wallet, company_names/entity_names. Todos com **fallback
Mongo** e (referência) self-validation; provisions/cash/issues **não** são
cacheados (mudam na conciliação).

| Coleção | Status | Observações |
|---|---|---|
| **companies** | ✅ migrado | `get_company_names` → API (`/partners/companies`); + rota da precificação |
| **entities** | ✅ migrado | `get_entity_names` → API (`/entities`) |
| **cashAccounts** | ✅ migrado | `sum_cash_by_dates` (chokepoint, 8 sites) + export + split + label, via `processedPosition.cashAccounts.values` (mesmo shape `{date,value}`). companyId resolvido da carteira (Mongo `wallets` — transitório). |
| **provisions** | ✅ migrado (16/17) | 16 sites via `provisions_active/lifecycle/overlapping/search` (janela-larga + filtro estrito ativa-em-D). **Exceção:** `conciliacao.py:900` (`find_one` por `_id`, validação pré-delete) fica no Mongo — não há GET por id. |
| **issues** | ⚠️ parcial (1/5) | `_blocking_issues_by_wallet` via `pre-processing` (parser best-effort + fallback). Os **4 outros** (contagens por tipo, date-cards, resumo por empresa, lista de detalhe) **ficam no Mongo** — não há endpoint geral de issues. |

**Ainda sem API (seguem Mongo):** `securityPrices`, `securityEvents`,
`fxRates`, `navPackages` (query de divergência), `palettes`; a coleção
`wallets` é lida só para resolver companyId (transitório); os 4 sites de issues
gerais; e a validação de provisão por `_id`.

> **Riscos a validar com token (fallback cobre só ERRO de API, não divergência
> semântica):** (1) `GET /beehus/provisions?initialDate=2000-01-01&finalDate=D`
> devolve as provisões ATIVAS em D (superset). (2) shape de `pre-processing` p/
> o parser de issues bloqueantes. (3) caixa: `processed-position` devolve
> `cashAccounts.values` com histórico completo (confirmado no exemplo). ✅
> Validados os shapes; **fallback REMOVIDO** (jun/2026) — API é fonte única, sem Mongo no seam.

### Review adversarial de paridade (4 agentes) + hardening aplicado

Veredito: **paridade financeira preservada; nenhum blocker que produza número
errado.** O `$ifNull`/soma/filtros de provisões traduzem 1:1; o "vazamento
multi-tenant" levantado é falso-positivo (`walletId` é ObjectId único e o código
original já escopava só por walletId). Hardening defensivo aplicado a partir dos
achados:
- **Dedup por `provision._id`** em `_fetch_provisions` (zera o risco de soma
  dobrada caso o upstream devolva cópias do mesmo `_id`).
- **Match estrito `walletId == wid`** no parse de caixa (espelha o `$in` do
  Mongo; rejeita cashAccounts sem walletId).
- **Remoção de `_id`** dos docs de caixa do caminho-API (espelha `{_id: 0}`).
- Docstring de `get_processed_position` corrigida: a resposta real é
  `[{position, provisions, cashAccounts}]` (ativos sob `item["position"]
  ["securities"]`; `cashAccounts[].values` = histórico completo). Importa para a
  futura migração de posições.

Confirmado por smoke-test: dedup soma `_id` uma vez; caixa exclui carteira
errada/sem walletId; export de caixa sem `_id`.

> **Validação recomendada com token:** abrir uma página que liste ativos
> (ex.: busca da Precificação) com o token colado e confirmar que os nomes/dados
> vêm corretos. **(jun/2026: validado e fallback REMOVIDO — não há mais Mongo no
> read-seam; se a API falhar, a UI não mostra.)**

---

## 1 + 2 — `processedPosition` e `unprocessedSecurityPositions` (endpoints A e B)

### `processedPosition` — leituras diretas hoje

| Funcionalidade (rota) | Arquivo | Tipo de consulta | Cobre com A? |
|---|---|---|---|
| Detalhe da carteira (atual + anterior) | [conciliacao.py:426-439](pages/conciliacao.py#L426) | `find_one` por `walletId`+`positionDate` | ✅ direto |
| Funil de diagnóstico (issues-summary) | [conciliacao.py:1368-4068](pages/conciliacao.py#L1368) | `find`/`aggregate` em lote por `walletId:$in`+data; "posição anterior" via `$lt`+`$sort`+`$group $first` | ⚠️ parcial (ver nota agregação) |
| Modal de diagnóstico | [conciliacao.py:4259-4264](pages/conciliacao.py#L4259) | `find_one` atual + anterior | ✅ |
| Resumo de portfólio | [beehus_console.py:649](pages/beehus_console.py#L649) | `find` `companyId`+data → `walletId` | ✅ |
| Portfólio por execution-prices (L1/L2) | [beehus_console.py:1937-2060](pages/beehus_console.py#L1937) | `find` por faixa de datas + `$in` | ⚠️ multi-data (loop) |
| Debug histórico da carteira | [beehus_console.py:2203](pages/beehus_console.py#L2203) | `find` toda a carteira | ⚠️ sem data |
| Tabela de posições da carteira | [carteira.py:387](pages/carteira.py#L387) | `find` `walletId:$in`+`positionDate:$in` | ⚠️ multi-data |
| Painel: carteiras processadas | [controlpanel.py:698](pages/controlpanel.py#L698) | `find` data+`walletId:$in` → flag | ✅ (ou endpoint E) |
| Mudanças de posição / colunas extra | [controlpanel.py:1503](pages/controlpanel.py#L1503) | `find` faixa temporal | ⚠️ range |
| Contexto de precificação | [precificacao.py:192,501](pages/precificacao.py#L192) | `find().next()` exata + fallback `$lt` mais recente | ⚠️ "mais recente ≤ D" |
| Classificador de transações (L1/L2) | [transaction_security_classifier.py:267-396](transaction_security_classifier.py#L267) | `find` `$lte`/`$in` + `securities.securityId` | ⚠️ agregação |

### `unprocessedSecurityPositions` — leituras diretas hoje

| Funcionalidade (rota) | Arquivo | Tipo de consulta | Cobre com B? |
|---|---|---|---|
| Pills de datas / linhas por data | [conciliacao_unprocessed.py:326-375](pages/conciliacao_unprocessed.py#L326) | `aggregate`/`find` por data | ✅ (range) |
| Datas disponíveis da carteira | [conciliacao_unprocessed.py:496](pages/conciliacao_unprocessed.py#L496) | `distinct("positionDate")` | ⚠️ derivar do range |
| Detalhe atual + anterior | [conciliacao_unprocessed.py:544-865](pages/conciliacao_unprocessed.py#L544) | `find_one` atual + anterior | ✅ |
| Mapa securityId→unprocessedId | [carteira.py:198](pages/carteira.py#L198) | `find_one` mais recente (`sort -1`) | ⚠️ "mais recente" |
| Painel: posição bruta existe? | [controlpanel.py:718](pages/controlpanel.py#L718) | `find` data → flag | ✅ (ou endpoint E) |
| PU recente para exceções | [controlpanel.py:1030](pages/controlpanel.py#L1030) | `find sort -1 limit 200` | ⚠️ range+ordenação |
| Fonte p/ repetir posições | [repetir_posicoes.py:1165-1170](pages/repetir_posicoes.py#L1165) | `find_one` exata + fallback `$lte` | ✅ / ⚠️ |
| Agregação de exceções | [excecoes.py:248,4485](pages/excecoes.py#L248) | `find_one` por data | ✅ |
| Set de unprocessedIds (classificador) | [security_type_classifier.py:31](security_type_classifier.py#L31) | `aggregate` distinct | ⚠️ agregação |

**Viável?** **Sim**, para todas as leituras de *snapshot* (carteira + data, ou
faixa de datas). Ressalvas a confirmar com o backend:

- **Endpoint A** mostra só `date=` (data única). Os padrões "posição anterior",
  "mais recente ≤ D" e "$in de várias datas" exigiriam **várias chamadas** ou um
  parâmetro de range. **Endpoint B já tem `initialDate`/`finalDate`** (range) —
  melhor; A idealmente deveria aceitar o mesmo.
- **Agregações** ("última posição por carteira", "max date", "distinct dates")
  passam a ser feitas **no cliente** sobre o resultado do range, ou exigem um
  endpoint `/latest` em lote (que **não existe** na API Beehus — é justamente o
  que o plano Node propõe). Para o volume atual, agregar no cliente é aceitável.
- **A aceita `walletIds` (plural)** → dá pra resolver várias carteiras numa data
  numa só chamada (mata o N+1 do painel/carteira por carteira).

---

## 3 — `securities` e `securityMappings` (endpoints C e D)

### `securities` — leituras diretas hoje

Padrão dominante: **resolver um conjunto de ids → `beehusName` (+ às vezes
`securityType` e os 4 campos de settlement/NAV days)**. Hoje feito com
`find({"_id": {"$in": [...]}})` em quase toda página, mais 2 *full scans*.

| Funcionalidade | Arquivo | O que lê |
|---|---|---|
| Mapa nome (cache 5 min) | [db.py:339 `get_security_names`](db.py#L334) | `find({},{beehusName})` **full scan** |
| Nomes/settlement em conciliação | [conciliacao.py](pages/conciliacao.py#L507) (≈12 pontos: 507, 1044, 1457, 2166, 2204, 2792, 2881, 3032, 3517, 3866, 4044, 4317) | `beehusName`, `securityType`, `*NavDays`, `*SettlementDays`, `currency` |
| Busca/detalhe/resolução | [precificacao.py:278-1239](pages/precificacao.py#L278) | por `_id`, por `mainId`, search regex; muitos campos |
| Benchmarks (CDI etc.) | [excecoes.py:904-4092](pages/excecoes.py#L904) | `securityType:"benchmark"` + `companyIds` |
| Enriquecer issues / colunas extra | [controlpanel.py:610,1556](pages/controlpanel.py#L610) | `beehusName`, `mainId` |
| Offsets de settlement | [repetir_posicoes.py:1122](pages/repetir_posicoes.py#L1122) | `maturityDate`, settlement/NAV days |
| Enriquecer transações | [beehus_console.py:2088,2192](pages/beehus_console.py#L2088) | `ticker`, `taxId`, `isIn`, `selicCode`, `securityType`… |
| Nome único | [carteira.py:579](pages/carteira.py#L579) | `beehusName` |
| Nomes + **full scan** | [conciliacao_unprocessed.py:269,816](pages/conciliacao_unprocessed.py#L269) | `find({})` **full scan** |
| Cache do matcher (ML) | [security_matcher.py:1289](security_matcher.py#L1289) | `find({}, _CACHE_FIELDS)` **full scan** |
| Offsets (classificador) | [transaction_security_classifier.py:373](transaction_security_classifier.py#L373) | settlement days, `$in` misto str/ObjectId |

### `securityMappings` — leituras diretas hoje

Padrão **único e simples**: `find_one({"companyId": …}, {"mappings": 1})` →
constrói mapa `{from(unprocessedId): to(securityId)}` (ou o inverso). Pontos:
[controlpanel.py:1274,1310](pages/controlpanel.py#L1274) (resolve `_id` p/ o
PATCH), [conciliacao_unprocessed.py:75](pages/conciliacao_unprocessed.py#L75),
[carteira.py:179,566](pages/carteira.py#L179),
[excecoes.py:236,4273](pages/excecoes.py#L236),
[repetir_posicoes.py:1186](pages/repetir_posicoes.py#L1186).

**Viável?** **Sim, e é o caso mais fácil.** As duas coleções são **pequenas,
mudam pouco e já têm padrão de cache TTL** (`get_security_names`):

- **Endpoint C** (`GET /beehus/securities`) → buscar a lista inteira **1×**,
  cachear 5 min (igual `_cached_ttl`) e resolver ids/mainId/search **em memória**.
  Substitui os 3 full scans **e** todos os `find({_id:$in})` de uma vez.
- **Endpoint D** (`GET /beehus/financial/security-mappings`) → 1 GET por empresa,
  cacheado; entrega `mappings[]` **e** o `_id` que o PATCH precisa.
- A confirmar: que C devolva **todos os campos** consumidos (settlement/NAV days,
  `mainId`, `ticker`, `taxId`, `isIn`, `selicCode`, `securityType`, `currency`,
  `companyIds`) e que aceite (ou que o cliente tolere) o **id misto string/ObjectId**.

---

## 4 — Tela inicial do painel (endpoint E)

A grade do painel (`controlpanel`) monta o status por carteira lendo **6
coleções** direto do Mongo:

| Dado da grade | Função | Coleção | Substituível por |
|---|---|---|---|
| Carteiras da empresa | `_wallets_for_company` [:662](pages/controlpanel.py#L662) | `wallets` | **F** (`partner-info/.../wallets`) |
| Agrupamentos | `_untrashed_groupings_for_company` [:671](pages/controlpanel.py#L671) | `groupings` (cache) | **H** (`list_groupings`) |
| Carteiras **processadas** | `_processed_done_wallets` [:691](pages/controlpanel.py#L691) | `processedPosition` | **E** ou **A** |
| Posição **bruta** existe | `_unprocessed_existing_wallets` [:708](pages/controlpanel.py#L708) | `unprocessedSecurityPositions` | **E** ou **B** |
| **Issues bloqueantes** por carteira | `_blocking_issues_by_wallet` [:728](pages/controlpanel.py#L728) | `issues` (`aggregate`) | **E** (se incluir issues) |
| NAV calculado por carteira | `_nav_done_wallets` [:777](pages/controlpanel.py#L777) | `navPackages` | **I** (`nav-contribution`) |

**Viável?** **Sim.** O endpoint **E** (`processed-position/pre-processing`) tende
a devolver exatamente o status de pré-processamento por carteira (processada /
posição bruta / bloqueios) — colapsando as 3 primeiras leituras em 1 chamada.
Somado a **F** (wallets), **H** (groupings) e **I** (NAV), o painel inteiro fica
API-driven. **A confirmar:** o shape de E (inclui as issues bloqueantes? inclui o
flag de NAV ou isso fica no endpoint I?).

---

## 5 — O que AINDA puxa do Mongo sem API (após A–I)

Mesmo embrulhando todos os endpoints fornecidos, **estas leituras continuam sem
API** e seguiriam no Mongo (ou exigiriam o serviço Node do OpenSpec):

| Coleção / dado | Onde é usado | Há endpoint? |
|---|---|---|
| `companies` (nomes/paletas) | `get_company_names`, todo header | ❌ |
| `entities` (nomes) | `get_entity_names` | ❌ (parcial via F, que traz `entityId`) |
| `palettes` (cores) | ~~Carteira gerencial (relatório A4)~~ — exceção **removida**; sem leitor vivo | ✅ **não precisa migrar** |
| `fxRates` (câmbio) | conciliação/NAV multi-moeda | ❌ |
| `securityPrices` (`historyPrice`) | precificação, curva, séries de benchmark | ❌ **gap relevante** |
| `securityEvents` (cupom/amort) | curva (`repetir_posicoes`) | ❌ **gap relevante** |
| `provisions` (leitura) | conciliação, repetir posições | ❌ (só há escrita) |
| `issues` (lista geral) | conciliação, painel | ⚠️ parte via E (só bloqueantes) |
| `cashAccounts` (`sum_cash`) | conciliação, carteira, repetir | ❌ **gap relevante** |
| `navPackages` — **consolidado por-empresa** (divergência/former/publish) | conciliação `dates`/`rows`/`global-analysis`, beehus_console publish | ✅ **migrado para `/results` AO VIVO** (ver linhas Painel/Conciliação/Console abaixo). O **warm por-empresa** (`warm_all_async` + startup warm + helpers `nav_mismatch_docs`/`nav_former_by_wallet`/`nav_latest_date`) foi **REMOVIDO** — sem consumidor vivo após a migração para `/results`. Sobra só o cache lazy `nav_packages` (scenario-capture) lido oportunisticamente pelos helpers per-entidade |
| `navPackages` — **por-entidade** (find_one detalhe) | conciliação diagnose/recalc/history, unprocessed, repetir_posicoes | ✅ **migrado** via `nav_doc_for_entity_date`/`nav_former_for_entity`/`nav_series_for_entity` (cache OU 1 chamada direta a I) |
| `navPackages` — **Painel** (contagens por empresa + cell-detail) | controlpanel `/rows`, `/cell-detail` | ✅ **migrado** via endpoint **J** `/results` (1 chamada/empresa, ao vivo, fallback Mongo). controlpanel sem `db.navPackages` |
| `navPackages` — **Conciliação grade** (`/dates` só datas; `/rows` divergência na data) | conciliação tela inicial + Não-Processado | ✅ **migrado** via **J** `/results` (1 chamada/data, sem NAV anterior). NAV anterior só no detalhe (`/wallet-detail`, per-entidade) |
| `navPackages` — **global-analysis** (mismatch + NAV anterior) | conciliação análise cruzada | ✅ **off cache**: mismatch via **J** `/results`; NAV anterior = aggregate Mongo per-carteira (data anterior). processedPosition/txns/provisions seguem Mongo |
| `navPackages` — **Console** (publish-state / return-deltas) | beehus_console | ✅ **migrado** via **J** `/results` (`nav_grouping_docs`) |
| `navPackages` — **tela NAV** (matriz cobertura) | ~~`nav.py`~~ | ✅ **removida** — tela órfã (sem template/caller, `/nav`→`/`); `pages/nav.py` deletado. Função coberta pelo Painel |
| `navPackages` — **diagnóstico** (trashed/cross-company/tipo) | beehus_console classify+probes | ⚠️ **segue Mongo de propósito** (inspeciona estado bruto). *(scenario-capture REMOVIDO jun/2026)* |
| `publishedPositionSecurities` | relatórios | ⚠️ talvez via I |
| `diagnosticFeedback` (**escrita**) | feedback do diagnóstico | ❌ insert direto (fica local) |
| espelho de `transactions` (**escrita**) | PATCH local pós-confirmação | ❌ (some quando leitura vier de G) |

Mais: **todo `scripts/*`** continua Mongo direto (ETL
offline, fora de escopo).

> **Atualização (jun/2026):** a exceção *Carteira gerencial* (`managed_portfolio`)
> foi **removida** da página Stripping. Ela era o **único leitor vivo** de
> `palettes` / `companies.selectedPalletes` (cores do relatório A4, via
> `_load_company_palette` em `pages/excecoes.py`). Com a remoção, `palettes`
> deixou de ter consumidor em código vivo — **não precisa de API nem migração**.
> `publishedPositionSecurities`, `securityPrices` (séries de benchmark) e
> `transactions`, que o mesmo relatório também lia, **continuam** sendo usados
> por outras páginas e seguem nas linhas acima.

### Resumo da cobertura

- **Cobertura total via API existente:** `processedPosition`,
  `unprocessedSecurityPositions`, `securities`, `securityMappings`, `wallets`,
  `groupings`, `transactions`, NAV por entidade, status de pré-processamento.
- **Lacunas que travam o "zero Mongo":** `securityPrices`/`securityEvents`
  (precificação/curva), `fxRates`, `issues` (lista completa), e o **consolidado
  cross-empresa** de `navPackages` (contagens do painel + `nav.py`, que somam
  todas as empresas — precisam de endpoint server-side). `cashAccounts`,
  `provisions` (leitura), `companies`/`entities` e o `navPackages`
  consolidado/por-entidade **por-empresa** já foram migrados via `beehus_catalog`
  e validados ao vivo (jun/2026).

## Recomendação de sequência (menor risco → maior)

1. **`securities` + `securityMappings` (C, D)** — wrappers + cache; troca trivial,
   alto alcance (toca quase toda página). Começo ideal.
2. **`wallets` + `groupings` (F, H)** — referência; H já existe.
3. **Painel (E, F, H, I)** — valida o padrão API-driven (consolida wallets,
   groupings, status de pré-processamento e NAV numa só tela).
4. **`processedPosition` + `unprocessedSecurityPositions` (A, B)** — snapshots por
   página (`carteira`, `conciliacao(_unprocessed)`, `repetir_posicoes`).
5. **`transactions` (G)**.
6. **Lacunas** (`securityPrices`, `cash`, `provisions`, divergência NAV): decidir
   entre (a) novos endpoints na API Beehus ou (b) o serviço Node do OpenSpec.
