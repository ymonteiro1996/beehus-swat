# Pedido ao backend Beehus — o que falta para desligar o Mongo

> **Para quem:** time da API Beehus.
> **Por quê:** o dashboard SWAT (Controle de Cargas) já lê da API Beehus **tudo
> que a API expõe**. As leituras que ainda batem direto no MongoDB sobram porque
> a API **não tem endpoint/campo** para elas, ou porque o endpoint existente tem
> uma **limitação** (single-date sem range/sort). Este doc enumera exatamente o
> que precisa ser exposto, mapeado aos pontos de uso no código, para podermos
> remover a conexão Mongo do runtime.
>
> **Contexto de validação:** cada item será validado com **1 empresa / 1 data**
> contra o Mongo atual (paridade de contagem + valores) — nunca fan-out por todas
> as empresas (a API rate-limita em ~1500 chamadas → 429). Os shapes pedidos
> espelham os docs Mongo (mesma forma; só normalizamos refs populadas no cliente).
>
> Ordenado por **impacto** (nº de pontos de uso que destrava).

---

## 1. `processed-position` com **range de datas + sort/limit**  ⬅️ maior impacto

> **Atualização (jun/2026):** a **Precificação foi migrada** e SAIU deste pedido.
> Os fluxos dela são por **data única** (a data que o usuário informa no botão
> Atualizar) → usam o **endpoint A que já existe** (`beehus_catalog.processed_doc`):
> wallet-securities, security/`<id>`, upload, refresh-list e a **curva**
> (`_event_pu_impacts` divide o cupom/amort pela quantidade da **posição-base**,
> via A). A rota `latest-position-date` (auto "mais recente") e a feature
> `security-transactions` foram **removidas**. Logo a Precificação **não depende
> mais** de range/sort. Sobram só os reads de **identificação** (P1 controlpanel
> + P3 console — tratados em outra instância). *(Saíram desta frente, todos sem
> backend: precificação; excecoes/"Data Base" → data editável padrão hoje;
> Excluir Posições/P5 → endpoint E `processedWalletsDetailed`; repetir/picker P2
> → mudança de UX (operador informa a Data-fonte, E confirma elegibilidade).)*

### O que vem do Mongo (a informação lida)

Em **todos** os pontos, o dado lido é o **mesmo doc `processedPosition`** — e é
**exatamente o que o endpoint A já devolve** em `item["position"]` (mesmas chaves).
Nenhum campo novo é necessário. Os campos efetivamente consumidos:

| Nível | Campo | Para quê |
|---|---|---|
| topo | `walletId` | chave de agrupamento |
| topo | `companyId` | filtro (1 site: console "wallets-with-position") |
| topo | `positionDate` | ordenação / "mais recente ≤ D" / `$max` |
| topo | `trashed` | filtro `trashed != true` (sites do console) |
| `securities[]` | `securityId` | casar com transação/preço |
| `securities[]` | `pu` | PU da posição |
| `securities[]` | `quantity` | quantidade da posição (Δqty, "dia anterior") |
| `securities[]` | `pricingType` | tipo de precificação (controlpanel/console L1-L2) |

> **Conclusão:** não falta *dado* — falta **capacidade de consulta**. O endpoint A
> é **single-date**, **exige `walletIds` explícito** (vazio → 0) e **não tem
> sort/limit/range**. Por isso os 5 padrões abaixo continuam no Mongo.

### Os 5 padrões de consulta que A não expressa

> *(jun/2026: **precificação saiu** — migrada p/ endpoint A, ver topo. **conciliação
> saiu** — refatorada p/ usar `beehus_catalog.nav_former_for_entity`/
> `nav_doc_for_entity_date` (NAV), não mais `processedPosition` agg; isso eliminou
> o antigo P1 da conciliação e o **P4 inteiro**. **excecoes/"Data Base" saiu** —
> a data do stripping virou editável (padrão hoje), sem `processedPosition`.
> **Excluir Posições/P5 saiu** — migrado p/ o endpoint E `processedWalletsDetailed`.
> **repetir/picker P2 saiu** — mudança de UX (operador informa a Data-fonte; E
> confirma quais carteiras têm posição nela). Restam só os de **identificação**
> (outra instância): console (P3, Identificar Transações) e controlpanel (P1,
> Identificar).)*

**P1 — "última posição ≤ D" por carteira (com `securities`)**
Para um conjunto de carteiras, o doc mais recente com `positionDate ≤ D`,
lendo `securities[].{securityId,pu,quantity,pricingType}`.
- controlpanel [1438](../pages/controlpanel.py#L1438) (`find(walletId:$in[,positionDate≤D]).sort(-1)`, pega o 1º por carteira — drill-down "ver posições")

**P2 — só a `MAX(positionDate)` por carteira (sem ler `securities`)** — variante leve de P1
- ~~excecoes 330~~ **REMOVIDO (jun/2026)**: a "Data Base" do stripping virou data editável (padrão hoje), informada pelo operador — não semeada mais do `processedPosition`. Mudança de feature, **sem backend** (espelha o que foi feito na precificação). O apply continua lendo posição na Data Base via API.
- ~~repetir_posicoes 781~~ **MIGRADO (jun/2026, sem backend — mudança de UX)**: em vez de descobrir a última data por carteira, o operador informa uma **Data-fonte** única; o `/daily` usa o pre-processing (E, fan-out só nas empresas do roster) p/ marcar quais carteiras têm posição nessa data (elegíveis → `lastDate`=data, alvo=próximo dia útil). Perde a precisão "última data própria por carteira"; o operador ainda pode editar a origem por linha. Validado ao vivo (93 elegíveis p/ `23313334000110`@2026-06-19).

**P3 — as N posições mais recentes ≤ D (ou as N últimas) de 1 carteira** — exige `sort+limit` — *o grosso do #1 hoje*
Sempre com `trashed != true`; lê `securities[].{securityId,pu,quantity,pricingType}`. Tudo no **Beehus Console** (ferramentas de Identificar Transações / Excluir Posições):
- console [2040](../pages/beehus_console.py#L2040)/[2051](../pages/beehus_console.py#L2051) (top-3 datas ≤ liq → securities dessas datas — wallet-securities)
- console [2120](../pages/beehus_console.py#L2120)/[2142](../pages/beehus_console.py#L2142)/[2163](../pages/beehus_console.py#L2163) (L1 = T; L2 = T-1 ∪ T-2; `find_one` na data L1 — detalhe da posição)
- console [2301](../pages/beehus_console.py#L2301) (`_positions`: `.sort(-1).limit(40)` — cache de PU/Δqty)

**P5 — quais carteiras da empresa têm posição na data exata D (existência, company-wide)**
- ~~console 679~~ **MIGRADO (jun/2026, sem backend)**: via pre-processing (E) `processedWalletsDetailed` (lista `[{walletId, name, …}]` das carteiras processadas em `(company, date)` — validado ao vivo: 93 carteiras p/ `23313334000110`@2026-06-19; sábado→0). Seam `beehus_catalog.wallets_with_position(company_id, date)` → `[{id, name}]`; rota `wallets-with-position` (Excluir Posições) consome.

### Pedido (resolve P1–P5)

1. **Range + sort/limit** no `processed-position`: aceitar `initialDate`/`finalDate`
   (como o unprocessed já faz) + `sort=positionDate:desc` + `limit`. → resolve P3
   e, com `limit=1`, P1.
2. **Modo "latest ≤ D por carteira"** (atalho do P1, evita trazer a janela toda):
   dado `walletIds` + `date`, devolver 1 doc por carteira = o de maior
   `positionDate ≤ date`. → resolveria P1. *(P2 já saiu por mudança de UX; P1 é
   da outra instância — então este pedido só importa se/quando o P1 voltar p/ cá.)*
3. ~~**P5**~~ **RESOLVIDO sem backend** — o endpoint E já expõe
   `processedWalletsDetailed` (lista de carteiras com posição na data). Migrado.

Idealmente expor também o filtro `trashed` (default não-trashed) para o P3.

**Aceite:** para uma carteira+data conhecida, "última posição ≤ D" via API ==
`find_one({walletId, positionDate:$lte D}, sort positionDate -1)` no Mongo (mesmas
`securities[]`, mesmos valores); e, para um conjunto de carteiras, o `$max
positionDate` por carteira == agregação Mongo.

---

## 2. `securityPrices` — série `historyPrice` por `securityId`

> **✅ ENDPOINT EXISTE (descoberto jun/2026) — JÁ MIGRADO.** Deixou de ser pedido
> ao backend; foi implementado no dashboard (+ correção de escopo). Wrapper
> `beehus_api.filtered_security_price` + seam `beehus_catalog.security_prices_resolved`
> (resolve C3→C2→C1→B2→B1, approved/não-trashed). **Migrados e validados ao vivo:**
> precificacao (`_find_price`/`_find_all_prices`/`_find_price_as_of` + `sec_last`
> por carteira), controlpanel (`_batch_last_price` com contexto), repetir
> (`b1-prices` → B1 global). `precificacao.py` ficou 100% Mongo-free. Detalhe abaixo.
>
> `GET /beehus/security-prices/filtered-security-price?securityIds=<csv>&pricingType=B1,B2,C1,C2,C3`
> (`pricingType` **obrigatório** → 400 sem ele). Devolve **lista** de docs
> `securityPrices` (mesmo shape do Mongo): `type` (=pricingType), `securityId`
> (**populado**), `companyId`/`entityId`/`walletId` (escopo, **populados**),
> `status`, `trashed`, `historyPrice[]` (`date`/`value`/`adjustedQuantity`).

**Hoje (Mongo, a substituir):** lemos `db.securityPrices` direto para PU/preço.
É o **núcleo de preço** (precificação) + comparação preço×PU (painel) + curva.
**Os benchmarks são docs `securityPrices`** (o `benchmarkId` é um `securityId`),
então este mesmo endpoint serve ativos **e** benchmarks.

### O que vem do Mongo (a informação lida)

| Nível | Campo | Para quê |
|---|---|---|
| topo | `securityId` | chave — **armazenado como string OU ObjectId** (aceitar ambos no filtro) |
| `historyPrice[]` | `date` (YYYY-MM-DD) | ordenação / "≤ data" / data exata / mais próxima |
| `historyPrice[]` | `value` | o PU/preço |
| `historyPrice[]` | `rentability` | **só benchmarks** — fator diário da curva (`bm_factor_map`) |
| `historyPrice[]` | `adjustedQuantity` | presente no schema (pouco usado nos reads atuais) |

> `historyPrice` pode vir como **array** ou como **dict único** (schema legado) —
> o cliente já normaliza (`_extract_all_hp`). **Sem** campo `type`.

### Os padrões de consulta (todos são leitura por `securityId`)

**Q1 — último preço por securityId** (entrada `historyPrice` mais recente)
- precificacao [125](../pages/precificacao.py#L125)/[132](../pages/precificacao.py#L132) (`_find_price`: `sort historyPrice.date -1 limit 1`)
- controlpanel [994](../pages/controlpanel.py#L994) (`_batch_last_price` sem data: `$slice -1`, **batch `securityId:$in`**)

**Q2 — série completa por securityId** (todas as entradas, ascendente) — alimenta o `bm_factor_map` da curva (usa `rentability`/`value`) e o as-of
- precificacao [142](../pages/precificacao.py#L142)/[149](../pages/precificacao.py#L149) (`_find_all_prices`; benchmark com filtro `historyPrice.date ≥ global_start`)

**Q3 — ponto "≤ data" (as-of)** — último `value` com `date ≤ ref_date`
- precificacao [154](../pages/precificacao.py#L154) (`_find_price_as_of`; usado por upload e refresh-list)

**Q4 — ponto na data exata, senão a mais próxima** — **batch `securityId:$in`**
- controlpanel [1011](../pages/controlpanel.py#L1011) (`_batch_last_price(target_date)`: exata ou menor `|Δdias|`)
- repetir_posicoes [3161](../pages/repetir_posicoes.py#L3161) (`b1-prices`: PU na data por securityId, batch — alimenta o preview/curva do Repetir)

### Onde aparece (funcionalidades)

- **Precificação:** lastPU do ativo (`/security`, `/calcular`, `/config`), PU-base do `pos_fixado`, **série do benchmark** da curva, e o PU as-of no `upload`/`refresh-list`. *(é o único Mongo que sobrou na Precificação.)*
- **Painel de Controle:** `_batch_last_price` no `match` (compara preço×PU p/ score), no `wallet-positions` e na rota `last-price`.
- **Repetir Posições:** `b1-prices` (PU por data, em lote) para o preview.

### Resolução de `pricingType` (regra de negócio — a parte nova)

Cada record carrega `type` ∈ {B1,B2,C1,C2,C3}, definindo o **escopo** do preço:

| type | Escopo (chaves que casam) |
|---|---|
| **B1** | `securityId` (global) |
| **B2** | `securityId` + `entityId` |
| **C1** | `securityId` + `companyId` |
| **C2** | `securityId` + `companyId` + `entityId` |
| **C3** | `securityId` + `companyId` + `walletId` |

O endpoint devolve **todos** os records dos tipos pedidos; o **cliente resolve**
para o contexto `(companyId, entityId, walletId)` escolhendo o **mais específico**
na ordem **C3 → C2 → C1 → B2 → B1**. O 1º record cujo escopo casa com o contexto
é o usado — e o seu `historyPrice` é a série de PU. Filtros (decisão do usuário,
jun/2026): **`trashed != true` E `status == "approved"`**. A resolução é
**uniforme p/ todos os consumidores, inclusive o Repetir** (fallback até B1
permitido — C3 é casado por carteira, então nunca vaza o C3 de outra wallet; sem
C3 da carteira, usa o global B1; ver [[feedback_curva_strict_wallet]]).

**Validado ao vivo (jun/2026):** o endpoint **não pré-resolve** (um ativo com
2 carteiras devolveu 2 records C3, um por wallet); `companyId`/`entityId`/
`walletId` vêm **populados** (normalizar p/ `_id` ao casar). Na base atual só
existem **B1** (global) e **C3** (por carteira) — B2/C1/C2 definidos mas sem uso.

> **⚠️ Correção, não só migração:** os reads Mongo atuais fazem `find({securityId})`
> e pegam um record **qualquer** (sort por data) — **ignoram o escopo**. Quando há
> C3 por carteira, pegam o PU de uma **carteira arbitrária**. A resolução acima
> conserta isso.

### Plano de implementação (sem backend novo)

1. **`beehus_api`**: `filtered_security_price(security_ids, pricing_types=("B1","B2","C1","C2","C3"))` → lista crua.
2. **`beehus_catalog`**: normalizar refs populadas (`securityId`/`companyId`/`entityId`/`walletId` → `_id`) + resolver `resolve_price_record(security_id, *, company_id, entity_id, wallet_id)` (ordem C3→…→B1) e pickers sobre `historyPrice`: **latest**, **as-of (≤ data)**, **exato/mais-próximo**, **série** — drop-in de `_find_price`/`_find_price_as_of`/`_batch_last_price`/`_b1_price_on_date`.
3. **Migrar consumidores**, passando o contexto:
   - precificacao (`_find_price*`): tem company+wallet+entity → resolve C3.
   - controlpanel (`_batch_last_price`): tem companyId (sem wallet) → C1/B1.
   - repetir (`b1-prices`): trabalha **por carteira** → resolução uniforme C3→…→B1 com walletId no contexto (o nome "b1" é legado).

**Aceite:** para `(security, company, wallet)` conhecidos, o record resolvido ==
o que a regra C3→B1 escolhe; `historyPrice` idêntico ao do Mongo; o PU usado na
precificação/curva bate com o atual **onde hoje já acerta a carteira** (e corrige
onde hoje pega carteira errada).

---

## 3. ~~Campo `currencyId` em `wallets`~~ — ✅ RESOLVIDO (jun/2026, SEM backend)

**Descoberta:** o payload de `partner_wallets` **já traz a moeda da carteira** —
no campo top-level **`currency`** (código, ex. `"BRL"`/`"USD"`, presente em 962/962
carteiras de amostra) e também em `companyId.currencyId` (moeda da *empresa*). Não
há `currencyId` top-level, mas todos os consumidores usam `currencyId` como **código
de moeda** (`str`, default `"BRL"` — upload xlsx de carteira/repetir,
`excecoes._wallet_currency`), então o código basta.

**Correção (não só migração):** derivar de `currency` é mais correto — o Mongo às
vezes guardava `currencyId` como ObjectId e `str(ObjectId)` virava hex no upload.
A distribuição real de moedas (BRL 497 / USD 386 / EUR 49 / CHF 10 / GBP 17 / JPY 2 /
AUD 1) confirma que é a moeda **da carteira** (top-level `currency`), não a da empresa.

**Feito:**
- `beehus_catalog._normalize_wallet_doc`: deriva `currencyId := currency` quando ausente
  → o doc da carteira no índice fica completo.
- `db.resolve_wallet`: **removido** o `wallets.find_one({_id})` (gap-read). Agora é
  API-only, **zero Mongo**. `db.py` não tem mais reads de runtime no Mongo.
- `pages/excecoes.py`: os 7 `db.wallets.find(...)` migrados p/ o seam (`wallets_index`/
  `wallets_in_company`/novo `_wallet_docs`); `_to_oids` (helper bson) removido (morto).

**Validado ao vivo:** `wallet_doc`/`resolve_wallet` devolvem `currencyId` correto por
carteira (carteira USD → "USD"), id inválido → None, sem tocar Mongo.

---

## 4. ~~`transactions`/`provisions` por id~~ — ✅ RESOLVIDO (jun/2026, SEM backend)

**Insight (do usuário):** as rotas que **populam** a tela já entregam o `_id` (e os
demais campos). Logo não é preciso GET-by-id: basta o dashboard **carregar o id/os
campos da chamada que populou** e usá-los adiante.

**Reavaliação dos call sites (os números antigos estavam defasados):** eram 3 reads
reais, não 4 (o "lote 2415" já tinha migrado):
- **Guardas de posse da conciliação** (`_find_wallet_txn` / `_find_wallet_provision`,
  antes de excluir/editar txn/provisão) — eram **só** verificação de que o id pertence
  à carteira. Como a listagem da carteira já vem **escopada via API (endpoint G)** com
  os ids, o par (carteira, id) é válido por construção. **Decisão do usuário: confiar
  no id** (manter só a checagem `_require_visible_wallet`). Reads **REMOVIDOS** →
  **`conciliacao.py` ficou 100% Mongo-free** (saiu também o import do proxy `db`).
- **IRRF (console 2931)** — lê a transação-origem por id p/ criar uma `taxes`. Vive
  **dentro do grid Identificar Transações** → **outra instância**. Mesma técnica
  resolve sem backend: o grid já tem os campos da origem (walletId/entityId/securityId/
  currencyId/datas/description), basta enviá-los no item `taxes` em vez de re-buscar.

**Conclusão:** #4 **não precisa de endpoint novo**. Resolvido por reuso do id/campos já
entregues pela chamada que popula. (Trade-off aceito nas guardas: uma requisição forjada
{carteira visível + id de outra empresa} não é mais barrada pela posse — só pela
visibilidade da carteira; aceitável p/ ferramenta interna.)

---

## 5. ~~`securityEvents` por securityId~~ — ✅ RESOLVIDO (jun/2026, ENDPOINT JÁ EXISTE)

**Endpoint:** `GET /beehus/security-events?securities=<csv>` (por securityId; aceita
CSV). Devolve **lista crua** de eventos: `_id`, `securityId` (**string**), `eventType`
(`cashDividend`/`interestOnEquity`/`interest`/`coupon`/`amortization`/…),
`operationDate` ("YYYY-MM-DD"), `liquidationDate`, `balance` (dividendo POR COTA),
`newSecurityId`, `factor`. **Sem campo `trashed`** (a API já exclui). O cliente
filtra por `operationDate`+`eventType` e agrega.

**Correção de entendimento:** o read da curva é **dividendo** (`cashDividend` +
`interestOnEquity`), não cupom/amort — estes vêm via `transactions`.

**Feito (sem backend):** wrapper `beehus_api.security_events(security_ids)` + seam
`beehus_catalog.dividend_events_by_sid(security_ids, date)` (filtra eventType+data,
soma `balance` por sid; chunk 150). `repetir._dividend_events_by_sid` delega ao seam
e agora recebe os **`securityIds` da carteira** (`sec_ids`) em vez do `wallet_id`
morto. Escopo por sid é equivalente ao scan global por data (só ativos com
posição-base contribuem dividendo).

**Validado ao vivo:** API == Mongo nas 3 datas de evento de um ativo de amostra
(8 casas decimais); casos vazios → `{}`.

---

## 6. ~~`issues` — drill-down~~ — ✅ RESOLVIDO (jun/2026, SEM backend, via E)

Os dois reads de `issues` saíram do Mongo:
- **`/issues-summary`** (contagens) → E via `_counts_from_status` (mesma regra do grid).
- **`/detail`** (linhas) → E via novo seam **`beehus_catalog.issues_detail(company_id,
  date, type)`** — drop-in do `db.issues.find({companyId, status:'pending', date, type})`.

**Correção do entendimento anterior:** eu havia concluído que o `/detail` precisava de
backend (campos `description`/`externalOrigin` ausentes no E + agregação). **A validação
ao vivo refutou isso:** esses campos **não são exibidos** nas tabelas (só o cabeçalho usa
`description`), e os `*Detailed` do E, **expandidos por `affectedWallets`** (cada item =
`{id, name, entity, entityId}`), reproduzem o Mongo **exatamente**. O E ainda traz
nome+entity da carteira inline.

**Validado ao vivo (`00000000000001 @ 2026-06-17`, 5 tipos):** contagem seam == Mongo em
todos — missing_position 13/13, security_unmapped 10/10, missing_classification 5/5,
missing_price 3/3, missing_history_price 45/45; 0 divergências de (security×carteira);
`_format_issue` OK (createdAt datetime). O enriquecimento (walletName/beehusName/mainId)
segue na rota (cache + securities API), igual a antes.

**Como o seam mapeia** (1 linha por ocorrência, drop-in da projeção antiga):
`missingPositionDetailed`/`securityMissingHistoryPriceDetailed` já são por-carteira;
`securityUnmapped`/`MissingClassification`/`MissingPrice` são por-security com
`affectedWallets[]` → expandidos em 1 linha por carteira. `description`/`externalOrigin`
vêm `''` (não exibidos). `db.issues` **zerado no runtime**.

---

## 7. ~~Última data publicada — `publishedPositionSecurities`~~ — ✅ RESOLVIDO (jun/2026, feature REMOVIDA)

O único consumidor era o auto-fill de data da view **Fluxo** do Console. **Decisão
do usuário: remover as 4 features de pipeline** (Fluxo / Reverter / Fluxo por datas /
Reverter por datas). Com elas saiu a rota exclusiva `flow/latest-position-date` e o
**`db.publishedPositionSecurities`** (zerado no projeto). **Sem backend.**

Removido: views `daily-flow`/`revert-flow`/`flow-dates`/`revert-dates`; controladores
JS `Fluxo`/`RevertFlow`/`FlowDates`/`RevertDates`; os 2 chips do favorites-bar
(controlpanel.html); branches do View router + allowlist de deep-link; CSS `.fx-*`
morto; a rota `flow_latest_position_date` (beehus_console.py). Rotas compartilhadas
(`process`/`nav/*`/`publish`/`unpublish`/filters) **mantidas** (pipelines single-step
"por datas" continuam). Import OK; sem refs órfãs.

---

## Fora do pedido ao backend (decisões internas / não-runtime)

Estes **não** dependem do backend — são decisões nossas ao desligar o Mongo.
*(Lista revista jun/2026 — `diagnosticFeedback.insert_one` e os reads em
`transaction_security_classifier.py` NÃO existem mais; conciliacao e esse
classificador estão Mongo-free.)*

- **A) ~~Escrita-espelho — `transactions.update_one`~~ ✅ REMOVIDA (jun/2026):** o
  `_apply_patch_to_mongo` + `_TXN_OBJECTID_FIELDS` + o `mongoOk` da resposta saíram; a
  rota `PATCH /api/beehus/transactions/{id}` só faz o PATCH upstream (API
  `update_transaction`, fonte de verdade). O front do **Editar Transações** teve o aviso
  "sem espelho local" removido. **Não há mais NENHUMA escrita Mongo no runtime.**
  *(Nota p/ a outra instância: o front do Identificar Transações ainda tem um
  `r.body.mongoOk === false` que virou inerte — `mongoOk` não existe mais na resposta.)*
- **B) ~~PU-map~~ ✅ MIGRADO p/ endpoint B (jun/2026, decisão do usuário):** o `_batch_pu`
  (Step 3 do `/api/controlpanel/match`, enriquecimento de PU) deixou de ler
  `db.unprocessedSecurityPositions` — agora busca o **PU bruto via endpoint B**
  (`beehus_catalog.unprocessed_docs_map` → `unprocessed-security-positions`), escopado
  às carteiras da empresa, **só na data exata selecionada** (não mais o scan dos 200
  docs ≤ data). Validado ao vivo: 22/23 uids batem com o Mongo na data; a 1 divergência
  é **ambiguidade pré-existente** (uid baseado em descrição aparece 2× na data com PUs
  diferentes — antigo e novo pegam um dos dois). **A rota `/match` ficou Mongo-free**
  (matcher/classifier via beehus_catalog; lastPrice via securityPrices; PU via B).
  ⚠️ *Trade-off:* busca as carteiras da empresa por data (chunk ~150) — mais chamadas
  que o read único antigo. Se virar gargalo, o front pode passar os walletIds relevantes
  (affectedWallets) e escopar a chamada. Resto do matcher = outra instância (mas já não-Mongo).
- **C) ~~Diagnóstico `navPackages`~~ ✅ REMOVIDO (jun/2026, decisão do usuário):** as 2
  rotas de diagnóstico do console — `POST /grouping-id-classify` + o manual
  `GET /grouping-id-probe` — eram as **últimas leituras `db.navPackages` diretas do
  runtime** e foram apagadas, junto da UI que as consumia no Publicar Agrupamentos
  (bloco que chamava o classify após o upload, botão **"Copiar lista"**, labels
  `IGNORED_REASON_LABELS`/`PROBE_BUCKET_LABELS`, estado `_lastIgnoredClassification`,
  método `copyIgnoredIds`). O upload de Excel ainda mostra `N adicionado(s) ·
  M ignorado(s)`, só **sem a quebra por motivo**. Era introspecção do Mongo cru
  (docs incluindo trashed, varredura cross-empresa, tipos brutos) que a API normaliza —
  se precisar de novo, volta como **script offline** (junto do E). **`db.navPackages`
  zerado.** As outras 2 rotas de filtro de agrupamento (`groupings-by-publish-state`,
  `grouping-return-deltas`) já eram API (`nav_grouping_docs` → `/results`), não Mongo.
- **D) ~~Caminho morto — `build_wallet_map`~~ ✅ REMOVIDO (jun/2026):** o ramo filtrado
  (`db.wallets.find`) e o helper morto `wallet_filter_query` saíram; `build_wallet_map`
  ficou API-only (`beehus_catalog.wallet_pairs`, `settings` ignorado). **db.py agora não
  tem nenhuma leitura de Mongo** — só `create_index` em `ensure_indexes` (no-op por
  permissão).
- **E) Classificadores / ETL — parcialmente MIGRADO (jun/2026, sugestão do usuário):**
  - **E1) `security_type_classifier.rebuild_mapping` ✅ MIGRADO** (era o único E **no
    runtime**: rota `POST /api/controlpanel/rebuild-mapping`, botão "Recalcular"). Deixou
    de varrer `db.unprocessedSecurityPositions.aggregate` — os `unprocessedId` são os
    `from` dos próprios security-mappings, então passou a usar o novo **`MappingCache`**
    (espelha o `SecurityCache`: arquivo-do-dia → `all_security_mappings()` → persiste em
    `data/security_mappings_cache.json`; warmado no `/warmup`). O route invalida
    `all_mappings` + `MappingCache.refresh()` antes de recalcular (pega mappings
    recém-rotulados). **Validado ao vivo:** corpus novo (31.026) é **superset exato** do
    antigo (29.110, positions ∩ mappings) — 0 linhas perdidas, +1.916 exemplos extras.
    **`rebuild_mapping` ficou Mongo-free.**
  - **E2) `transaction_type_classifier.rebuild_training_data`** [86](../transaction_type_classifier.py#L86)
    (`transactions.find`, todas empresas) — **só CLI, não está em route**. Sem endpoint
    all-company de transactions → fan-out (429). Fica **ETL offline**.
  - **E3) `scripts/update_liquidation_dates.py`** [44](../scripts/update_liquidation_dates.py#L44)/[68](../scripts/update_liquidation_dates.py#L68)
    (`wallets.find`+`transactions.find`) — script de manutenção pontual. **Offline.**

**Estado:** A, B, C, D ✅, **E1 ✅** e **a identificação ✅** feitos (zero escritas no runtime;
db.py sem leituras; `/match` e `/rebuild-mapping` Mongo-free; `db.navPackages` zerado).
**Identificação (jun/2026):** **P1** (`/wallet-positions`) guardado por flag
`IDENTIFICAR_ENABLED`; **R2** (`identify-transactions/identify`, PU/Δqty) e **R3**
(`identify-transactions/execution-extras`, IRRF) **migrados p/ a API** (endpoint A
walk-back data-exata + campos da origem vindos do cliente). **O runtime do dashboard não
tem mais NENHUMA leitura Mongo.**

**Infra gateada ✅ (jun/2026):** a conexão Mongo, o `ensure_indexes`, o `db_profiler` e o
handler `PyMongoError` agora são condicionados a `IDENTIFICAR_ENABLED`, e o import do
`pymongo` é **lazy** (em `db.py`/`app.py`/`pages/setup.py`; `db_profiler` só é importado se
a flag estiver ON). **Validado:** com `SWAT_IDENTIFICAR=0` o `app` sobe **sem carregar
`pymongo`** e sem conectar (`bson`/ObjectId permanece — lib independente, não o driver).
A instância de identificação e os CLIs **E2/E3** rodam com a flag default ON → conectam
normal. `pymongo` continua **instalado** (codebase compartilhado), mas **não é usado nem
carregado** nesta instância. Único restante de Mongo: **E2/E3** (processos CLI offline).

---

## Resumo / prioridade

| # | Pedido | Pontos | Bloqueia |
|---|---|---|---|
| ~~1~~ | ~~`processed-position` com range + sort/limit~~ **— RESOLVIDO sem backend**: identificação migrada/guardada (P1 flag `IDENTIFICAR_ENABLED`; R2 PU/Δqty via endpoint A walk-back; R3 campos vindos do cliente). *(antes: precificação, excecoes/Data-Base, Excluir-Posições/P5, repetir/picker P2 — todas sem backend)* | — | (resolvido) |
| ~~2~~ | ~~`securityPrices`~~ **— ENDPOINT JÁ EXISTE** (`filtered-security-price`) → vira trabalho de dashboard (resolver pricingType C3→B1) + correção; **não é pedido ao backend** | — | precificação, painel, curva, repetir |
| ~~3~~ | ~~`currencyId` no payload de wallets~~ **— JÁ VEM** (`currency` top-level) → migrado sem backend: `resolve_wallet` API-only + 7 reads de excecoes no seam | — | ~~resolve_wallet + excecoes~~ (resolvido) |
| ~~4~~ | ~~`transactions`/`provisions` por id~~ **— SEM backend** (reusa o id que a listagem já entrega): guardas de conciliação removidas → conciliacao Mongo-free; IRRF (console) = outra instância | — | (resolvido) |
| ~~5~~ | ~~`securityEvents` por securityId~~ **— ENDPOINT JÁ EXISTE** (`/beehus/security-events?securities=`) → migrado sem backend (seam `dividend_events_by_sid`); API==Mongo validado | — | (resolvido) |
| ~~6~~ | ~~`issues` detalhe~~ **— migrado p/ E** (seam `issues_detail`, expande affectedWallets); summary+detail no E; `db.issues` zerado; API==Mongo validado | — | (resolvido) |
| ~~7~~ | ~~última data publicada~~ **— feature REMOVIDA** (Fluxo/Reverter + por-datas; auto-fill saiu, `publishedPositionSecurities` zerado) | — | (resolvido) |

**TODOS os 7 itens foram resolvidos SEM backend** (endpoint/campo já existiam, reuso do
id que a listagem entrega, migração p/ o E, ou remoção de feature). **Não resta nenhum
pedido real ao backend.** O **#1** (identificação) também foi resolvido sem backend:
P1 guardado por flag, R2/R3 migrados p/ a API. **Nenhuma leitura Mongo de runtime resta.**

No **runtime** do dashboard não resta NENHUMA leitura Mongo de **decisão interna**: as
escritas-espelho (A), o PU-map (B), o diagnóstico `navPackages` (C) e o rebuild de
security-types (E1, agora via `MappingCache`) já saíram. O que ainda lê Mongo é
**fora do processo do dashboard**: o ETL offline `transaction_type_classifier.py` (E2,
só CLI) + `scripts/*` (E3). Resolvida a frente de **identificação**
(`processedPosition`/IRRF — outra instância), o runtime fica sem leituras Mongo, e o
`MongoClient`/`pymongo` só permanece para esses jobs offline (Fase 6 do
[PLANO_MIGRACAO_MONGO.md](PLANO_MIGRACAO_MONGO.md)).
