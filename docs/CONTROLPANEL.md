# Painel de Controle — Mapeamento de securities

> Documenta o sub-fluxo de **identificar / mapear** ativos não cadastrados a
> partir do Painel de Controle (`/controlpanel`). Não cobre as demais funções
> da página (chips de NAV, Strip, Day-trade, etc.) — apenas o caminho
> "abrir issue de Mapeamento → escolher security → aplicar".

---

## Visão geral

```
[Painel de Controle]
   │
   │  clique na célula "Mapeamento" (issue type security_unmapped)
   ▼
[Modal: Mapeamento]   templates/controlpanel.html  (#modal)
   │  Cada linha: unprocessedId × wallets · tipo · candidato sugerido pelo
   │  matcher (security_matcher.SecurityMatcher).
   │
   │  Clique na célula "Security sugerido" de uma linha
   ▼
[Modal: Selecionar Security]   #security-search-modal
   │  Duas fontes/abas:
   │
   │   ┌──────────────────────────┬────────────────────────────┐
   │   │ Posições da carteira     │ Cadastro                   │
   │   │  GET /api/controlpanel/  │  GET /api/controlpanel/    │
   │   │       wallet-positions   │       search-securities    │
   │   │                          │                            │
   │   │  Lê do processedPosition │  Lê do SecurityCache       │
   │   │  mais recente das        │  in-memory (cadastro       │
   │   │  carteiras associadas    │  global da empresa, todas  │
   │   │  ao unprocessedId.       │  as securities visíveis).  │
   │   └──────────────────────────┴────────────────────────────┘
   │
   │  Clique numa linha → grava `candidate.score = "manual"` na linha do
   │  Mapeamento e marca o checkbox. O preço é buscado em background via
   │  /api/controlpanel/last-price (mesma data do pillbar selecionado).
   ▼
[Modal: Mapeamento]   o operador escolhe filtros e clica em
                      "Mapear no sistema" (PATCH na API Beehus) ou
                      "Gerar JSON Mapeamento" para download.
```

> **Edição antes da sugestão.** A tabela do Mapeamento renderiza as linhas
> imediatamente — antes do `/api/controlpanel/match` retornar — com a célula
> "Security Encontrado" já clicável. O operador pode abrir o modal de busca
> e fixar uma security manualmente enquanto o matcher ainda está rodando;
> picks manuais (`candidate.score === "manual"`) são preservados quando o
> resultado do matcher chega e a tabela é re-renderizada. Um indicador
> pequeno embaixo da tabela ("Buscando correspondências…") sinaliza que o
> matcher ainda está trabalhando.

---

## Fontes de sugestão no modal de busca

### 1. Posições da carteira (aba padrão)

- **De onde vem**: `processedPosition` mais recente das carteiras associadas
  ao `unprocessedSecurityId`, lido da **API processed-position** (via
  `beehus_catalog.processed_positions_map` — sem Mongo). Como uma issue de
  Mapeamento pode aparecer em várias carteiras (a tabela do Mapeamento agrega
  via `_groupBySecurity`), o endpoint aceita `walletIds` separados por vírgula
  e deduplica as securities encontradas. A API é single-date, então
  `_latest_positions_by_wallet` reproduz o antigo `positionDate ≤ date` (data
  do pillbar) com um **scan regressivo limitado** (`_WALLET_POS_MAX_BACK_DAYS`,
  7 dias úteis): uma carteira ainda não processada hoje exibe o snapshot do dia
  útil anterior mais próximo dentro da janela; sem snapshot na janela, a
  carteira simplesmente não aparece.
- **Por quê**: securities que o operador já viu na carteira são, na maioria
  esmagadora dos casos, o pareamento correto para um `unprocessedId` novo
  daquela carteira. Mostrar essa lista curta antes do cadastro inteiro
  reduz drasticamente cliques e risco de selecionar o ativo errado.
- **Filtros**: `q` (substring case/accent-insensitive sobre `beehusName`,
  `mainId`, `ticker`, `taxId`, `isIn`, `selicCode` — mesmos campos do
  `/search-securities`). O filtro de tipo do cadastro fica oculto nessa aba
  (poucos tipos por carteira; ruído desnecessário).
- **Enriquecimento**: cada `securityId` da posição é cruzado com o
  `SecurityCache` (preferência, sem hit em MongoDB) e, para ids ausentes
  do cache, com `beehus_catalog.securities_by_ids` (lookup no índice em
  memória do catálogo, via API — não Mongo). Cada linha mostra Nome,
  mainId, Tipo, Carteira(s) que carregam o ativo, e PU da posição.
- **Visibilidade**: o `companyId` da carteira é confrontado contra o
  `company_filter` da sessão antes da consulta; carteiras invisíveis são
  silenciosamente removidas.

### 2. Cadastro (aba secundária)

- **De onde vem**: `SecurityCache` em memória (`security_matcher.get_cache()`),
  populado por `/api/controlpanel/warmup` na entrada da página e renovado
  via `/api/controlpanel/refresh-cache`.
- Funcionamento idêntico ao anterior: substring sobre os mesmos campos +
  filtro opcional de `securityType`.
- Mostra todas as securities visíveis para a empresa, mesmo que ainda não
  apareçam em nenhuma posição processada — útil quando o operador está
  identificando um ativo recém-cadastrado.

### Aba padrão

A aba `Posições da carteira` é selecionada automaticamente quando o
`unprocessedId` tem pelo menos uma carteira associada (caso quase
universal). Sem carteiras (raro, mas possível quando a issue foi criada
sem `walletId`), cai diretamente em `Cadastro`.

### Flag `IDENTIFICAR_ENABLED` (instância)

A identificação roda numa **instância separada** (`db.IDENTIFICAR_ENABLED`,
env `SWAT_IDENTIFICAR`; default ON — set `SWAT_IDENTIFICAR=0` para desligar).
Quando **off** nesta instância:

- a aba **`Posições da carteira` fica escondida** (Jinja `{% if identificar_enabled %}`)
  e o modal abre direto no `Cadastro`;
- `GET /api/controlpanel/wallet-positions` **curto-circuita** retornando
  `{results:[], walletsScanned:0, securityCount:0}` — o flag espelha apenas o
  papel da instância (a rota já não lê Mongo; os dados vêm da API
  processed-position);
- a busca **`Cadastro`** (catálogo global via `SecurityCache`, API) continua
  100% funcional, então o operador ainda acha/seleciona o security.

---

## Endpoints

### `GET /api/controlpanel/rows`

Preenche a grade da tela inicial (uma linha por empresa) para a `date` escolhida.
Combina **quatro fontes**: dois fan-outs AO VIVO por empresa contra a API Beehus
e dois índices de catálogo em cache (5 min):

| Fonte | Função | Endpoint | Colunas |
|-------|--------|----------|---------|
| Pré-processamento (E) | `_preproc_counts` | `get_preprocessing_status` | 6 tipos de issue + Posições Processadas (numerador) |
| Resultados NAV | `_navpackage_counts_by_company` | `/results` (consolidado) | NAV Wallet, GAP, NAV Grouping, Published (numeradores) |
| Carteiras (cache) | `_wallets_by_company` | `wallets_index` | denominador das colunas de carteira |
| Agrupamentos (cache) | `_groupings_by_company` | `grouping_index` | denominador das colunas de agrupamento |

Cada fan-out já roda com 10 workers internos (`_NAV_WARM_WORKERS`). A coluna TXN
é renderizada como placeholder `…`; o front a preenche depois via `/txn-counts`.

> **Denominador vazio não gruda no TTL.** Os dois índices de catálogo
> (`wallets_index`/`grouping_index`) são os **denominadores** das colunas de
> progresso (Posições Processadas / NAV Wallet / NAV Grouping / Published, que o
> front mostra como `feito/total`). Se o fan-out por empresa volta vazio numa
> janela transitória — servidor recém-iniciado com o token ainda não pronto,
> token recém-colado, ou um 429/hiccup — o índice `{}` **não** é cacheado pelos
> 5 min: `wallets_index()`/`grouping_index()` (e as caches derivadas
> `_wallets_by_company`/`_groupings_by_company`) **invalidam a própria entrada
> quando o resultado é vazio**, então a próxima requisição re-busca assim que a
> API responde. Sem esse guard, `total=0` fazia `_extra_cell` renderizar `—` em
> todas as quatro colunas (numeradores presentes, denominador zerado) por até 5
> minutos. Mesmo padrão de `company_names()`.

> **Por que TXN não vem no `/rows`.** Medição (31/12/2025, 19 empresas): o endpoint
> de transações (G) custa ~60% do tempo total e segurava a tela inteira. Movê-lo
> para uma chamada separada derrubou o **tempo-até-a-tela de ~16,6 s para ~4,2 s
> (-74%)**; o TXN (~11,8 s) carrega em segundo plano com a grade já visível.
> Tentar paralelizar os três fan-outs (E + /results + G) num só `/rows` **não**
> ajudou (ganho ~1%, dentro do ruído): o gargalo é o servidor Beehus, já saturado
> pelos 10 workers de cada fan-out. Por isso a separação, e não a concorrência.

Para medir o custo por endpoint numa abertura: `beehus_api.client.enable_timing(True)`
+ `get_timing()` (soma de ms por path).

### `GET /api/controlpanel/txn-counts`

Contagem de transações não identificadas por empresa (coluna **TXN**) na `date`.
Servido separado do `/rows` (ver acima). Resposta:

```json
{
  "date": "YYYY-MM-DD",
  "items": [
    {"companyId": "<id>", "company": "Nome",
     "txn": {"count": 3, "label": "3", "cls": "bg-green-100 ..."}}
  ]
}
```

Só empresas com `count > 0` aparecem (o fan-out de G só conta positivos). O front
(`_loadTxnCounts`): (1) atualiza as células TXN das linhas já renderizadas — as
ausentes da resposta viram `—`; (2) **anexa** linhas para empresas que só têm
transações pendentes (sem issue/posição/NAV), preservando o conjunto de empresas
que o `/rows` antigo cobria. A célula já vem pronta (`{count,label,cls}`) para o
front não replicar a regra de cor (`_cell_cls`). Respeita `company_filter`.

### `GET /api/controlpanel/wallet-positions`

| Param      | Obrigatório | Descrição |
|------------|-------------|-----------|
| walletIds  | sim         | Lista separada por vírgula. Carteiras invisíveis ao `company_filter` são descartadas. |
| date       | não         | `YYYY-MM-DD`. Busca o `processedPosition` mais recente com `positionDate ≤ date` (scan regressivo de até 7 dias úteis via API). Sem `date`, usa hoje (BRT) como ponto de partida. |
| q          | não         | Substring case/accent-insensitive aplicada após o enrichment. |
| limit      | não         | Default 100, máximo 500. |

Resposta:

```json
{
  "results": [
    {
      "securityId":   "<id>",
      "beehusName":   "...",
      "mainId":       "...",
      "ticker":       "...",
      "taxId":        "...",
      "isIn":         "...",
      "selicCode":    "...",
      "securityType": "...",
      "maturityDate": "YYYY-MM-DD",
      "pu":           1234.56,
      "quantity":     1000,
      "pricingType":  "MTM" | "Curva" | null,
      "walletIds":    ["<wid1>", "<wid2>"],
      "walletNames":  ["Wallet A", "Wallet B"]
    }
  ],
  "walletsScanned": 2,
  "securityCount": 17
}
```

Ordenação: primeiro os enriquecidos (`beehusName` não-vazio), depois
alfabética por `beehusName` e `mainId`. Securities órfãs (no
`processedPosition` mas sem registro em `securities`) descem para o fim
da lista e exibem "— sem cadastro" no nome.

### `GET /api/controlpanel/search-securities`

Continua como antes (busca pelo cache global). Compartilha os mesmos
campos de busca textual.

### `GET /api/controlpanel/last-price`

Continua como antes. Chamado em background após a seleção para preencher
a coluna "Preço" da linha do Mapeamento.

---

## Pontuação do match (`_score_candidate`)

> Define como o matcher (`security_matcher.SecurityMatcher`) ranqueia os
> candidatos de cada `unprocessedId`. **Compartilhado** com o *Identificar
> Transações* (`TransactionSecurityClassifier` reusa `_score_candidate`), então
> qualquer mudança aqui vale para os dois recursos.

Duas regras **duras** dominam os sinais aditivos:

### Regra 1 — Identificador exato = match decisivo (vence a data e o ranking)

Igualdade estrita em um identificador único — `ticker` (inclui a variante
`FUT…`), `taxId`/CNPJ (**ou o CNPJ == `mainId` em `brazilianFund`**, ver nota),
`isIn`/ISIN, `selicCode`, **ou um código
(`cetip`/`internal`/`external`/`fund`) que seja IGUAL ao `mainId` inteiro** —
**identifica o ativo sozinho** (a igualdade total exige `mainId` com **≥ 8
caracteres**, para um código curto não virar um exato falso). Quando acontece,
o score é **`_EXACT_SCORE` = 300**, o cálculo é curto-circuitado e **a checagem
de data é ignorada**. `matched_on = [<id>, "exact"]`.

> **Por que 300 e não 100?** A trilha aditiva (Regra 3) **não tem teto** e seus
> bônus se somam: id parcial 50 + **padrão estruturado de opção 50** (um `if`
> separado, empilha com o parcial) + data exata 50 + indexador 10 + nome 15 +
> raridade 30 + tipo 12 = **217**; a verificação de preço do Mapeamento ainda
> soma até **+30** (→247), enquanto um match exato pode levar **−25** de preço
> (→ `_EXACT_SCORE − 25`). Com o exato em 100 (ou mesmo 200) um empilhamento
> coincidente podia **ultrapassar** um identificador exato no ranking. Em 300 o
> exato fica **sempre acima** (300 − 25 = 275 > 247). Os badges de confiança
> continuam limitados a 100% (o Mapeamento exibe o score como % com clamp em
> 100%; o classificador deriva `min(1.0, score/100)`), então o efeito visível é
> só no ranking e na **decomposição do tooltip**, cujo total é 300 num exato.

> **CNPJ é comparado sem pontuação.** Alguns textos de transação trazem o CNPJ
> como 14 dígitos crus (`12345678000190`) enquanto a security guarda pontuado
> (`12.345.678/0001-90`). A comparação é feita só com os dígitos
> (`_digits_only`), então o dígito-cru bate com o pontuado e conta como o
> **mesmo match exato** que `ticker`/`isin`. O extrator de transações
> (`_extract_generic_safe`) captura o CNPJ cru de 14 dígitos quando não há um
> pontuado, e a varredura L3 inclui a forma só-dígitos do `taxId` no haystack
> de pré-filtro para o candidato certo não ser descartado antes da pontuação.

> **CNPJ == `mainId` em fundos brasileiros.** O `mainId` de `brazilianFund`
> normalmente **É** o CNPJ em dígitos crus (ex.: `mainId = 30934757000113`).
> Quando o `unprocessedId` traz o CNPJ — pontuado **ou** como 14 dígitos crus no
> começo (ex.: `30934757000113 - BTG CRED CORP INCENTIVADO …`) — o
> `_extract_brazilian_fund` extrai esse CNPJ (`features["cnpj"]`) e o
> `_exact_identifier_match` compara os dígitos contra **`taxId` E `mainId`**:
> se bater com o `mainId`, é um **match exato decisivo (300)**,
> `matched_on=["mainId/cnpj","exact"]`, rótulo "CNPJ = mainId" no tooltip. Como
> só o extrator de fundos seta `cnpj`, a regra fica naturalmente restrita a
> `brazilianFund`. A varredura de candidatos (`SecurityCache.search`) também
> consulta o índice `mainId` pela forma só-dígitos do CNPJ, então o fundo certo
> entra na pontuação mesmo quando os tokens de nome não se sobrepõem.

> **Igualdade total vs substring.** Um código que é IGUAL ao `mainId` inteiro
> (ex.: `cetip_code` == `mainId`) é exato (200). Matches por **substring/
> adjacência** (o código aparece DENTRO de um `mainId` maior; `selicCode` ±1)
> continuam aditivos (Regra 3, +35–50), não decisivos, e sujeitos à Regra 2.

### Regra 1b — Veículo/lastro divergente = score 0 (`bond`)

O **veículo** define a identidade de um título de renda fixa: um **CRA não é um
LCA**, um **CRI não é um CRA**, um **LCA não é um LCI**. Mesmo emissor + mesmo
indexador + mesmo vencimento, mas veículo diferente = **título diferente**.
Quando a descrição nomeia um veículo (`features["instrument"]`, ex.: `CRA`) **e**
o candidato é claramente **outro** veículo, o score é **0**, `matched_on =
["vehicle≠(CRA)"]` — não importa quão bem emissor/indexador/vencimento batam.

O veículo do candidato é lido de **duas fontes**: as palavras do `beehusName`
(com fronteira de palavra) **e** o **prefixo do `mainId` comprimido** (ex.:
`LCABANCOABC9300CDI10022026` → `LCA`). Veículos reconhecidos (`_VEHICLE_CANON` em
`security_matcher.py`): `DEB`(=`Debênture`), `CRA`, `CRI`, `CDCA`, `LCA`, `LCI`,
`LCD`, `LIG`, `CDB`, `CCB`, `LF`, `LFS` (alias `LFSN` — **subordinada**, tratada
como veículo distinto da `LF` comum), `FIDC`.

> **Famílias agro vs imobiliário vs subordinada.** Os pares que mais se confundem
> são separados de propósito: `CRA`/`CDCA`/`LCA` (agro) ≠ `CRI`/`LCI`/`LIG`
> (imobiliário); `CDB` ≠ `CCB`; e `LF` (comum) ≠ `LFS`/`LFSN` (subordinada).
> `LFS` e `LFSN` são o **mesmo** veículo (canônico `LFS`), então `LFS`↔`LFSN`
> **não** se rejeitam entre si — só contra `LF`. As detecções de prefixo de
> `mainId` são longest-first (`LFSN` > `LFS` > `LF`) para não confundir
> subordinada com comum.

> **Conservador nos dois lados** (evita falso-corte): só rejeita quando a
> descrição nomeia um veículo **E** o candidato expõe veículo(s) **E** não há
> interseção. Se a descrição não traz veículo, ou o veículo do candidato não é
> determinável (sem palavra no `beehusName` e sem prefixo no `mainId`), o gate
> **não** dispara. Restrito a `securityType == "bond"` (onde esses veículos
> competem); um identificador exato (Regra 1) já teria vencido antes daqui.

> **Descrição com 2+ veículos = ambígua (não corta).** Quando a descrição nomeia
> mais de um veículo distinto — o caso clássico é o rótulo combinado **`CRI/CRA`**
> que o upstream emite quando não sabe — `_extract_bond` marca
> `vehicle_ambiguous` e o gate **não** dispara (não dá para rejeitar contra
> nenhum dos dois). No acervo isso cobre 74 descrições `CRI/CRA`; o gate de
> data/nome ainda filtra um candidato de emissor/vencimento errado.

> **Prefixo de `mainId` só com dígito em seguida.** A leitura do veículo pelo
> prefixo do `mainId` comprimido só é confiável quando o token é seguido de um
> **dígito** (o código do papel: `CRA00123`, `LF0020003KK`). Quando segue
> **letra** (`LCABANCO…`, `CRAUSINACORURIPE…`), é ambíguo com um nome de emissor
> que começa igual (`LIGHT`→`LIG`, `CRISTAL`→`CRI`), então o prefixo **não** é
> usado — o veículo vem do `beehusName` (que quase sempre traz a palavra). No
> acervo real, só ~35 bonds perdem o veículo por inteiro assim, **todos**
> placeholders `Excluir`. Preferimos esse falso-negativo raro a um falso-corte de
> um papel correto (ex.: derrubar uma debênture da Light).
>
> **Caso real:** `unprocessed "CRA BANCO ABC 98,50% CDI 10/02/26"` vs candidato
> `"LCA Banco Abc Pré-fixado 10/Fev/2026"` (mainId `LCABANCOABC…`) marcava **83%**
> (vencimento exato +50, emissor, nome no mainId) porque nada penalizava o
> CRA↔LCA — o `_TYPE_AGREE_BONUS` só **premiava** concordância, nunca **rejeitava**
> divergência. Com o gate, vira **0**.

### Regra 1c — Indexador/regime divergente = score 0 (`bond`)

O **regime de remuneração** faz parte da identidade: um título **pós-fixado /
inflação** (`CDI`/`IPCA`/`SELIC`/`IGPM`) **não é** o mesmo que um **Pré-fixado**
— mesmo com veículo, emissor e vencimento iguais. O gate trabalha em **buckets
grossos** — **`PRE`** (pré-fixado) vs **`POS`** (pós-fixado/inflação) — e rejeita
(score **0**, `matched_on = ["indexer≠(PRE)"]` ou `["indexer≠(POS)"]`) quando o
bucket **primário** da descrição e o(s) bucket(s) do candidato são conhecidos e
**não se cruzam**.

- **Descrição:** bucket do regime primário (`features["indexer"]`, o regime
  ativo mais à esquerda, via `_scan_regimes`).
- **Candidato:** `_candidate_buckets` — do campo `indexer` **+** `beehusName`.

> **Por que buckets `PRE`/`POS` (e não `CDI`≠`IPCA`)?** O pedido é pré vs pós
> ("CDI, IPCA é pós-fixado, mas a security diz Pré-fixado → não é o mesmo"). E o
> acervo tem **inconsistências `CDI`↔`IPCA`** (o campo `indexer` discorda do nome
> em ~0,4% dos bonds); distinguir `CDI`≠`IPCA` no gate geraria falso-corte nesses
> casos. Entre candidatos `POS`, o **bônus +10** de indexador ainda ranqueia o
> match exato (`IPCA` acima de `CDI`) — então "IPCA é palavra forte" sobrevive
> como sinal **suave**, sem virar corte duro.

> **Coeficiente importa (fórmula BR `0%CDI+NN%aa`).** `_scan_regimes` é
> **coefficient-aware**: um índice precedido de coeficiente **zero** (`0%CDI`,
> `0,00% CDI`) **não** conta como aquele regime. A fórmula `0%CDI+12,05%aa`
> ("0% do CDI + 12,05% a.a.") é **pré-fixado** → bucket `PRE`. Sem isso, o `CDI`
> de peso zero (escrito à esquerda) era lido como o regime e rejeitava 116
> pré-fixados reais contra a própria security registrada. `100%CDI`/`108%CDI`
> seguem ativos (`POS`).

> **Detecção de `PRE`.** Pré-fixado em qualquer grafia — `PREFIXADO`, `PRE-FIX`,
> `PRE FIXADO`, `PRE-FIXADOS`, `PRE-FIXO` (`PRE[\s-]?FIX[A-Z]*`) — e o inglês
> `FIXED` viram `PRE`. Um **`PRE` isolado** conta (ex.: `… Pré FIAgro`,
> `CDB BMG Pré 19/mar/2027`) **exceto** quando é palavra de estrutura
> (`PRÉ-PAGAMENTO`/`EMBARQUE`/`PAGO`/`OPERACIONAL`), que **não** viram regime.

> **Conservador.** Só rejeita com bucket conhecido **dos dois lados** e sem
> interseção. Regime indeterminável em qualquer lado → não dispara. Restrito a
> `securityType == "bond"`; um identificador exato (Regra 1) já venceu antes.
>
> **Bônus de indexador (+10) corrigido junto.** Antes, candidato **sem**
> `indexer` ganhava o +10 indevido (`"" in "IPCA"` é `True` em Python); agora
> exige os dois lados preenchidos.
>
> **Caso real:** `"CRA RAIZEN IPCA+6,40% 17/10/33"` casava com `"CRA Raizen
> Pré-fixado 17/Out/2033"` a ~83% (mesmo veículo/emissor/data). Com o gate: **0**
> (`indexer≠(PRE)` — desc `POS` vs candidato `PRE`).

### Regra 1d — Emissor bancário divergente = score 0 (só veículo bancário)

Num título **bancário**, o emissor = o banco, nomeado de forma inequívoca
(`CDB BTG Pactual …`, `LCA Banco ABC …`). Dois bancos **diferentes** = título
diferente. Quando a descrição e o candidato resolvem para emissores bancários
**conhecidos e diferentes**, o score é **0**, `matched_on = ["issuer≠(BTG)"]`.

- **Escopo:** só veículos **bancários** (`_BANK_VEHICLES`: `CDB/RDB/LF/LFS/LCI/LCA/LCD/LIG`).
- **Dicionário:** [`data/issuers.json`](../data/issuers.json) — canônico → tokens-gatilho
  (alias), **editável**. Carregado em `_ISSUER_ALIAS`; arquivo ausente → gate inerte.
- Resolução por **token exato** (`_issuers_in`): casa `XP`/`BV`/`C6` curtos, mas
  nunca taxa/data/código. Candidato memoizado em `_candidate_issuers`.

> **Por que só invalidar (sem bônus de confirmar)?** O emissor **já** é pontuado
> pelo overlap de nome (+15), raridade (+30), nome-equivalente (+50) e nome-no-
> mainId (+20). Um bônus dedicado de emissor **contaria em dobro** e inflaria a
> escala. Falta só o *invalidar* — o caso `CDB Santander …06/Ago/2027` × `CDB BTG
> …06/Ago/2027` marcava **52** (data+indexador+tipo; overlap de nome = 0, pois os
> bancos não compartilham token) e entrava como "identificado". Só um gate corta.

> **Por que NÃO contamina as outras regras.** É short-circuit **pré-aditivo**
> (depois do exato → CNPJ/ticker/ISIN ficam **imunes**), independente dos gates
> de veículo/regime/data, não altera a matemática aditiva, e escopo `bond` +
> bancário. Securitizados **`CRI/CRA/CDCA`** ficam **de fora** (ambiguidade
> securitizadora × lastro: mesmo ativo, dois nomes válidos), e **`DEB`** também
> (emissor corporativo de cauda longa). Conservador: emissor desconhecido em
> qualquer lado → não dispara. Aliases (`Itaú/Itauvest`, `SafraBM/Safra`,
> `CEF/Caixa`, `BTG Pactual`) evitam falso-corte de mesma instituição.

> **Validação no acervo:** o gate rejeita **16** mapeamentos existentes — **todos**
> em veículo bancário (CDB 11, LCA 3, LCI 2), **zero** em CRI/CRA/CDCA/DEB — e
> todos são bancos genuinamente diferentes (BTG↔Sicredi/Pan/Itaú, Digimais↔C6,
> Original↔BTG…). O *validar* implícito (emissor concordando) cobre ~54% dos bonds
> via os sinais de nome, sem peso novo.

### Regra 2 — Data divergente = score 0 (ativo diferente)

Quando **não** há identificador exato e o operador nomeou uma data
(`maturity_date`/`expiry`) **e** o candidato expõe alguma data que **não bate**,
o score é **0** — data diferente significa ativo diferente. As datas do candidato
vêm de **ambas as fontes**:

- o campo estruturado `maturityDate`; **e**
- qualquer data embutida no `beehusName` (via `_extract_all_dates`, ex.:
  `… 02/Jan/2029`).

Basta a data do operador bater com **uma** das datas do candidato para concordar.
A comparação respeita a precisão informada pelo operador:

- **dia especificado** (`maturity_day_specified`, ou `expiry` de opções) →
  concorda dentro de uma janela de **±`_DATE_TOLERANCE_BIZ_DAYS` (2) dias
  úteis** (seg–sex, sem feriados — mesma convenção de `db.biz_days_between`) do
  vencimento do candidato. O match **exato** vale +30 (`maturityDate=`); dentro
  da tolerância, mas não exato, vale +15 (`maturityDate`);
- **só mês/ano** (ex.: `SET/2029`) → compara apenas `YYYY-MM`, para não rejeitar
  por falso desencontro de dia.

> **Por que o bônus de data é baixo (+30/+15) e não +50/+25?** A data já é o
> **gate** desta regra (vencimento divergente → 0), então **todo** candidato que
> chega aos sinais aditivos já tem a data certa — o bônus não desempata, só soma.
> Em +50 a data **sozinha** atingia o corte de 50 (`already_registered`),
> fazendo um irmão de **mesmo emissor + mesmo vencimento** (série/tranche
> diferente) entrar como "identificado" sem confirmação de nome/ID. Mantido
> **abaixo de 50** (+30 exato) para que nome/identificador decidam; data exata
> ainda vale mais que mês/ano (+30 > +15).

> **`maturity_day_specified` é setado pelos extratores** (via `_set_maturity` →
> `_parse_date_ex`) sempre que o texto nomeia um dia real — qualquer formato
> menos o mês/ano puro (`NOV/2032`). Antes a flag não era populada, então o gate
> de bonds só comparava mês/ano. Agora compara o dia **com tolerância de ±2 dias
> úteis** (`_DATE_TOLERANCE_BIZ_DAYS` em `security_matcher.py`, tunável; conta
> seg–sex via `db.biz_days_between`, **sem** feriados): absorve roll de dia
> útil/feriado sem aceitar ano/mês errados nem um vencimento distinto do mesmo
> mês. Um **roll de fim de semana** (sex→seg = 1 d.u., 3 dias corridos) concorda;
> um gap de **3 dias úteis** (ex.: seg→qui) **não** — mais apertado que o antigo
> ±5 dias corridos. Um guard de dias corridos (≤4) evita contar dias úteis no
> caminho quente para os candidatos de vencimento distante.

Se o candidato não expõe nenhuma data, o gate é ignorado (não há como comparar)
e o fluxo segue para os sinais aditivos.

> **Ano de 2 dígitos (`DD/MM/AA`).** `_parse_date`/`_extract_all_dates` também
> leem datas numéricas com ano de 2 dígitos (ex.: `17/07/28` → `2028-07-17`),
> desambiguando por valor como no caso de 4 dígitos: parte > 12 fixa o dia
> (`17/07/28` é BR `DD/MM`; `04/17/26` é US `MM/DD`); quando ambas ≤ 12, segue o
> `prefer_mdy` do contexto (opções = americano `MM/DD`, BR = `DD/MM`). Sem isso,
> uma maturity BR como `CRA … 17/07/28` ficava **sem data**, o gate era pulado e
> um candidato de vencimento errado podia ser sugerido só pelo nome.

> **Formato de data americano (MM/DD) por currency.** Datas numéricas
> **ambíguas** (ambas as partes ≤ 12, ex.: `12/01/2034`) são lidas como
> **MM/DD/YYYY** (americano, → `2034-12-01`) quando o contexto é USD; senão
> caem no padrão BR **DD/MM/YYYY** (→ `2034-01-12`). O sinal de "americano" é a
> **currency**: a da **wallet** da transação para a data da descrição
> (`predict` → `_extract_generic_safe(prefer_mdy=...)` via
> `db.get_wallet_currencies()`), e a da **própria security** para as datas do
> `beehusName` (`_candidate_dates` lê `sec.currency`) — assim os dois lados do
> gate concordam. Datas **inequívocas** (uma parte > 12, ex.: `15/08` ou
> `12/15`) são detectadas pelo valor e ignoram o flag. Sem isso, um título
> como `"SUNCOR ENERGY … 12/01/2034"` numa wallet USD era lido como 12/jan e
> rejeitado contra a maturidade real (01/dez/2034), apontando um fundo.

> **Pré-filtro do L3 (recall de títulos públicos).** A varredura L3
> (`_score_l3`) só pontua candidatos cujo haystack contém alguma *needle* da
> descrição. Além de códigos e tokens de nome, as needles incluem o
> **tipo de instrumento** (`bond_type`/`instrument`: `NTN-B`, `LFT`, `CDB`,
> `CRI`… — em ambas as grafias, com e sem hífen) e o **ano de vencimento**.
> Sem isso, uma descrição como `"Compra de Tesouro Direto: NTN-B 15/08/2040"`
> (cujas needles seriam só `TESOURO`/`DIRETO`) nunca traria o título
> `"NTN-B Ago/2040"` para a pontuação — só fundos com "Tesouro" no nome
> passavam. Com o tipo e o ano como needles, o título entra no scoring e o
> **gate de data + bônus de data exata (+30)** o colocam no topo.

### Sinais aditivos (quando nenhuma regra dura dispara) — escala ~0–100

| Sinal | Pontos |
|---|---|
| `ticker` substring em `mainId` (≥4 chars; ex.: `PETR4` em `BRPETR4ACNOR9`) — o ticker == `mainId` inteiro já vira **exato (300)** antes; aqui é substring de um `mainId` maior, `matched_on=["mainId/ticker"]` | +50 |
| `isin` substring em `mainId` (≥8 chars), `matched_on=["mainId/isin"]` | +50 |
| `cetip_code` substring em `mainId` | +50 |
| `internal_code` substring em `mainId` | +45 |
| `external_code` substring em `mainId` | +40 |
| `fund_code` substring em `mainId` | +35 |
| `selicCode` adjacente (±1) | +40 |
| padrão estruturado de opção (`CALL_AAPL_280…`) em `mainId`/`ticker` | +50 |
| data concordante **com dia/mês/ano exatos** (`matched_on=["maturityDate="]`) — abaixo do corte 50 de propósito (a data já é gate; ver nota) | +30 |
| data concordante **só em mês/ano** (`SET/2029`; `matched_on=["maturityDate"]`) | +15 |
| `indexer` coincidente — **ambos os lados preenchidos** (campo `indexer` do candidato não-vazio); conflito de indexador/regime já foi rejeitado pela Regra 1c | +10 |
| **concordância de tipo** — `bond_type`/`instrument` (`NTN-B`, `LFT`, `CDB`, `CRI`…) presente no `beehusName`/`mainId` do candidato; **só** quando o candidato já tem score > 0 por outro sinal (desempate, nunca isolado), `matched_on=["type=…"]` | +12 |
| overlap de tokens do nome (accent-insensitive) | até +15 |
| **nome equivalente** — conjuntos de tokens distintivos batem **nos dois sentidos** (cobertura ≥80% na descrição **e** no candidato, ≥2 tokens no overlap); ex.: `Kayros FIM CP IE` == `Kayros FIM CP IE`. Date-gated. Resolve o teto de ~45 do match de nome puro (15 overlap + 30 raridade) que fazia uma identificação óbvia parecer baixa confiança. A cobertura bidirecional impede que uma descrição que é **subconjunto** de um ativo mais específico dispare (Coruripe). **Bloqueado** quando há conflito de discriminador de fundo (abaixo). `matched_on=["name~equiv"]` | +50 |
| **classe/série/tranche de fundo difere** (`brazilianFund`) — o nome-base bate (cobertura ≥80%) mas o discriminador conflita. Duas categorias (`_fund_discriminator_sig`): **(1) tranche** — `Senior`/`Sr`/`Sub`/`Subordinada`/`Mezanino`/`Mez` — diferença **em qualquer sentido** penaliza (`FIDC … XI` vs `… XI Senior`); **(2) valor de classe** — letra solta (A/B/C/O), número (2/14/30) ou romano de série (I/IV/XV) — só penaliza em **conflito** (ambos têm valor e diferem: `Bogari Value A` vs `… O`; `… Verde 30` vs `… 14`). Uma **assimetria** de valor (um lado omite) **não** penaliza, para um `Classe A` extra na descrição não derrubar um security sem classe (caso Sports). `matched_on=["fund~discr≠"]` | −25 |
| **classe/tranche de fundo confere** (`brazilianFund`) — mesmo nome-base e mesmo valor/tranche não-vazio; ranqueia a classe exata acima do irmão-base assimétrico (`Bogari Value A` vs a classe-base). `matched_on=["fund~class="]` | +8 |
| **nome no `mainId` comprimido** — token distintivo da descrição (≥4 chars, sem genéricos) encontrado dentro do `mainId` **comprimido** (sem espaço/pontuação/acento; ex.: `usina` em `CRAUSINACORURIPE…`); **+8 por token, teto +20**, `matched_on=["mainId/name(N)"]`. **Date-gated**: só aplica quando a data concordou (ou a descrição não trouxe data) — assim um nome que é prefixo de um `mainId` mais específico (outro ativo) não pontua quando as datas divergem | até +20 |
| **bônus de raridade** — token do overlap que aparece em **≤20 securities** da base (nome próprio raro, ex.: `DELFOS`); ponderado pela raridade (mais raro = mais pontos), `matched_on=["name~rare(N)"]` | até +30 |
| **verificação de preço (só no Mapeamento)** — PU da `unprocessedPositionSecurities` × `lastPrice` (`securityPrices.historyPrice`) do candidato: Δ ≤ 0,1% **+30**, Δ ≤ 1% **+20**, Δ ≤ 5% **+8**, Δ > 20% **−10**, Δ > 50% **−25** (`breakdown[].code="price"`) | +30 … −25 |

> **Bônus de raridade (por que existe).** Sem ele, um token distintivo como
> `DELFOS` (em 2 securities) pesava o mesmo que palavras genéricas
> (`INVESTIMENTO`, `MULTIMERCADO`, `FUNDO`), e o score de overlap
> (`acertos / total_de_tokens_da_descrição`) era **diluído** por descrições
> verbosas. Resultado: o ativo certo empatava em ~3 pontos com dezenas de
> fundos irrelevantes. A frequência de cada token na base (`_TOKEN_DF`,
> calculada uma vez por carga do `SecurityCache`) deixa o match num nome
> próprio raro valer até +30, surfando o ativo correto. É **aditivo** ao
> overlap normal e limitado a +30 para nunca dominar um identificador exato.
>
> **Guarda contra falsos positivos genéricos (`_GENERIC_NAME_TOKENS`).** Termos
> de classe de ativo / estrutura (`investimento`, `multimercado`, `financeiro`,
> `referenciado`, `cambial`, `previdência`, `fi`, `mm`, `rf`, `cdb`…) são
> removidos **dos dois lados** antes do overlap, então não contam **nem** no
> score-base **nem** no bônus de raridade. Isso é necessário porque o
> beehusName é abreviado (`Delfos FI MM`) e a descrição vem por extenso
> (`Fundo de Investimento Multimercado`): sem a guarda, esses genéricos por
> extenso ficam raros no corpus (`referenciado` df=2) e ganhariam o bônus
> indevidamente. Só tokens **distintivos** (nome próprio) pontuam por nome.
> Complementa o `_NOISE_TOKENS` (verbos/conectivos) do extrator de transações.

`_confidence_label`: ≥75 `high`, ≥50 `medium`, <50 `low`. `already_registered`
= top-1 ≥ 50.

---

## Cadastrar ativos (modal de cadastro)

> Sub-fluxo separado do Mapeamento: depois de marcar linhas no Mapeamento, o
> botão **"Cadastrar ativos"** abre o `#registration-modal` para gerar o JSON
> de **cadastro de securities** (não é mapeamento — é criar a security que
> ainda não existe). Origem das linhas: as marcadas **e** visíveis no
> Mapeamento (respeita os filtros Match mínimo / Tipo / Identificado).

### Abas por tipo de ativo

O modal tem uma **barra de abas** (`#reg-tabs`, `_regRenderTabs`) com **todos os
tipos configurados** em `security_type_fields.json` (ordem do arquivo) — `bond`,
`brazilianFund`, `fund`, `stockEtf`, `options`, … — mais qualquer tipo presente
nos ativos que não esteja configurado (anexado ao fim, para nenhum card ficar
sem aba). Cada aba mostra o rótulo do tipo e o **contador** de ativos
(`Bond (8)`); abas sem ativos ficam **desabilitadas/cinza** (não clicáveis). A
aba ativa **filtra** os cards exibidos (`_regAllTypes`/`_regDefaultTab`/
`_regSetTab`). A aba padrão ao abrir é o **primeiro tipo com ativos**. Trocar o
tipo de um card (`_regSetType`) — ou em lote (`_regBulkSet('securityType')`) —
**leva o card para a aba do novo tipo** e ativa essa aba, para o operador não
"perder" o card. Os cards são renderizados com o **índice global** em
`_regRows` preservado, então os handlers (`_regSetVal`, `include`, beehusName)
acertam a linha certa mesmo com a tela filtrada.

### Layout: 3 seções por ativo (card)

Cada ativo é um **card** com três seções:

- **Seção 1 — informações do ativo (leitura) + tipo.** `unprocessedId`
  (mono, com tooltip de carteiras/candidato/PU via `_regShowTip`), o **seletor
  de tipo de ativo** (`securityType`, dropdown alterável, pré-preenchido com o
  tipo detectado — ou `bond` quando o parser de RF BR reconhece o ativo), e os
  identificadores **extraídos** do texto do `unprocessedId` pelo matcher: `type`
  (instrumento, quando o parser reconhece), `ISIN`, `ticker`, `taxId` (CNPJ
  cru). Vêm no campo `extracted` da resposta de `/api/controlpanel/match`
  (`isin`, `ticker`, `taxId`←`cnpj`, `type`←`bond_type`/`instrument`). Em muitos
  ativos (ex.: fundos) só o `taxId` é preenchido — o resto fica `—`.
- **Seção 2 — `beehusName`** (sempre presente, largura total, editável).
- **Seção 3 — campos do tipo (editáveis).** Os **campos daquele `securityType`**
  (de `security_type_fields.json`). Um tipo sem campos configurados mostra o
  aviso *"Campos de … ainda não definidos."* em vez da seção.

### Campos por tipo — `data/security_type_fields.json`

Os campos editáveis da **seção 3** são **dirigidos por configuração**, não
hard-coded. Trocar o dropdown (`_regSetType`) re-renderiza o card com os campos
do tipo escolhido (e ativa a aba desse tipo); valores de chaves em comum são
preservados.

- Arquivo: [data/security_type_fields.json](../data/security_type_fields.json).
  Servido por `GET /api/controlpanel/security-type-fields` → `{ "types": {...} }`.
- Estrutura: `types[<securityType>] = { label, fields: [ {key,label,input,…} ] }`.
  `beehusName` e `securityType` são **estruturais** (sempre presentes) — não
  entram em `fields`. Cada field:

  | chave | uso |
  |---|---|
  | `key` | nome do campo no JSON de cadastro **e** chave de armazenamento do valor |
  | `label` | rótulo curto acima do input |
  | `input` | `text` \| `date` \| `number` |
  | `default` | valor inicial (opcional) |
  | `include` | `always` (sempre envia, mesmo vazio/zero) \| `ifPresent` (só quando preenchido) — default `ifPresent` |
  | `transform` | `upperIndexer` (opcional) — MAIÚSCULAS + `IPC-A`→`IPCA` |
  | `width`, `title` | min-width do input (px) e tooltip do rótulo (opcionais) |

- Hoje só **`bond`** está definido (os campos de antes: `type`, `ticker`,
  `maturityDate`, `indexer`, `indexerPercentual`, `yield`, `currency`,
  `country`, `subscription*/redemption*Days`). Os demais securityTypes
  (`brazilianFund`, `stockEtf`, `options`, …) estão como **placeholders**
  (`fields: []`) — basta editar o JSON para habilitá-los, sem mexer em código.
  Um tipo sem campos configurados mostra, na seção 3 do card, o aviso
  *"Campos de … ainda não definidos."* (beehusName continua na seção 2).

### Geração do JSON (`_generateRegistrationJSON`)

Para cada linha marcada: `{ beehusName, securityType }` + um campo por entrada
de `fields` (respeitando `include`/`transform`/`input`) + `walletIds`,
`companyIds`, `feederIds` vazios. Download: `registration_<companyId>_<date>.json`.
Os botões **"Aplicar … em lote"** preenchem o valor nas linhas marcadas
(`securityType` troca o tipo; os demais escrevem em `values[<field>]`).

> **Exibição.** No modal de Mapeamento o score aparece na coluna **Match** como
> percentual (badge colorido pelos mesmos limiares 75/50/25, espelhando o
> formato de confiança do CONF). O valor é exibido com clamp em 100% (`manual`
> para picks manuais); o "Copiar" exporta a mesma string `%`.
>
> **Tooltip "como o score foi calculado".** O `title` do badge **Match** mostra
> a decomposição completa do score — cada sinal com sua contribuição em pontos,
> somando ao total — igual ao tooltip de *Identificar Transações*. A
> decomposição vem de `security_matcher._score_breakdown(matched_on, score)`
> (fonte única, reusada pelos dois fluxos) e, **só no Mapeamento**, recebe a
> linha extra da verificação de preço (`code="price"`). Por ser L3-only (varre o
> cadastro completo, sem posição de carteira para escopar como L1/L2 na
> identificação), o cabeçalho do tooltip diz "L3 — cadastro completo". O front
> (`_matchScoreTooltip` em `templates/controlpanel.html`) só monta as linhas; a
> matemática toda é server-side. A verificação de preço só é aplicada quando o
> candidato já tem `score > 0` por outro sinal — uma coincidência de PU nunca
> promove um ativo sem nenhum outro indício — e é limitada para não derrubar o
> score abaixo de 0; o `breakdown` sempre soma exatamente o score exibido.

---

## Arquivos relacionados

- Backend: [pages/controlpanel.py](../pages/controlpanel.py)
  (`wallet_positions`, `search_securities`, `match_securities`,
  `security_type_fields`).
- Frontend: [templates/controlpanel.html](../templates/controlpanel.html)
  — bloco `#security-search-modal` e funções `_openSecuritySearch`,
  `_runSecuritySearch`, `_renderSecurityRows`,
  `_switchSecuritySearchSource`.
- Cadastrar ativos: [templates/controlpanel.html](../templates/controlpanel.html)
  — bloco `#registration-modal` e funções `_buildRegRow`, `_buildRegCardHtml`,
  `_renderRegistrationTable`, `_regSetType`, `_regEnsureDefaults`,
  `_generateRegistrationJSON`. Config de campos por tipo:
  [data/security_type_fields.json](../data/security_type_fields.json).
- Cache de securities: [security_matcher.py](../security_matcher.py)
  (classe `SecurityCache`).
- Cache de security-mappings: [security_matcher.py](../security_matcher.py)
  (classe `MappingCache`, `get_mapping_cache()`). Espelha o `SecurityCache`
  (arquivo-do-dia → API → persiste em `data/security_mappings_cache.json`,
  staleness diária), carregado da rota `GET /beehus/financial/security-mappings`
  via `beehus_catalog.all_security_mappings()`. É a fonte de `unprocessedId →
  securityId` do botão **"Recalcular"** (`POST /api/controlpanel/rebuild-mapping`),
  que regenera `data/unprocessed_security_types.json` **sem ler MongoDB** — os
  `unprocessedId` são os próprios `from` dos mappings. O route força um refresh
  do cache (invalida `all_mappings` + `MappingCache.refresh()`) antes de
  recalcular, para capturar mappings recém-rotulados. Warmado na abertura pelo
  `/warmup`, junto do `SecurityCache`.
