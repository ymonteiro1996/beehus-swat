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
  ao `unprocessedSecurityId`. Como uma issue de Mapeamento pode aparecer em
  várias carteiras (a tabela do Mapeamento agrega via `_groupBySecurity`),
  o endpoint aceita `walletIds` separados por vírgula e deduplica as
  securities encontradas. Usa `positionDate ≤ date` (data do pillbar) com
  fallback para o snapshot anterior mais próximo, então uma carteira que
  ainda não foi processada hoje exibe ontem.
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
  do cache, com `db.securities.find` (uma única consulta batch). Cada
  linha mostra Nome, mainId, Tipo, Carteira(s) que carregam o ativo, e PU
  da posição.
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

---

## Endpoints

### `GET /api/controlpanel/wallet-positions`

| Param      | Obrigatório | Descrição |
|------------|-------------|-----------|
| walletIds  | sim         | Lista separada por vírgula. Carteiras invisíveis ao `company_filter` são descartadas. |
| date       | não         | `YYYY-MM-DD`. Busca `processedPosition` com `positionDate ≤ date`. Sem `date`, pega o snapshot mais recente. |
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
`FUT…`), `taxId`/CNPJ, `isIn`/ISIN, `selicCode`, **ou um código
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

> **Igualdade total vs substring.** Um código que é IGUAL ao `mainId` inteiro
> (ex.: `cetip_code` == `mainId`) é exato (200). Matches por **substring/
> adjacência** (o código aparece DENTRO de um `mainId` maior; `selicCode` ±1)
> continuam aditivos (Regra 3, +35–50), não decisivos, e sujeitos à Regra 2.

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
  exige igualdade no dia (`YYYY-MM-DD`);
- **só mês/ano** (ex.: `SET/2029`) → compara apenas `YYYY-MM`, para não rejeitar
  por falso desencontro de dia.

Se o candidato não expõe nenhuma data, o gate é ignorado (não há como comparar)
e o fluxo segue para os sinais aditivos.

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
> **gate de data + bônus de data exata (+50)** o colocam no topo.

### Sinais aditivos (quando nenhuma regra dura dispara) — escala ~0–100

| Sinal | Pontos |
|---|---|
| `cetip_code` substring em `mainId` | +50 |
| `internal_code` substring em `mainId` | +45 |
| `external_code` substring em `mainId` | +40 |
| `fund_code` substring em `mainId` | +35 |
| `selicCode` adjacente (±1) | +40 |
| padrão estruturado de opção (`CALL_AAPL_280…`) em `mainId`/`ticker` | +50 |
| data concordante **com dia/mês/ano exatos** (`matched_on=["maturityDate="]`) | +50 |
| data concordante **só em mês/ano** (`SET/2029`; `matched_on=["maturityDate"]`) | +25 |
| `indexer` coincidente | +10 |
| **concordância de tipo** — `bond_type`/`instrument` (`NTN-B`, `LFT`, `CDB`, `CRI`…) presente no `beehusName`/`mainId` do candidato; **só** quando o candidato já tem score > 0 por outro sinal (desempate, nunca isolado), `matched_on=["type=…"]` | +12 |
| overlap de tokens do nome (accent-insensitive) | até +15 |
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
