# Regras de Preenchimento — Campos para Cadastro de Ativo Novo no Beehus

> Documento de referência autocontido, complementar ao `REGRAS_BEEHUSNAME.md` (que cobre só a
> fórmula do campo `beehusName`). Este documento cobre o **objeto "ativo" completo**: todo campo
> que pode/deve ser preenchido para cadastrar um security novo no Beehus, sua origem, regras de
> obrigatoriedade por tipo, e como esses dados são consumidos depois. É agnóstico de formato de
> saída — não assume que o resultado final será um Excel.

---

## 1. O que é o "objeto ativo" e por que o formato de saída não importa

Cadastrar um ativo no Beehus significa produzir, para cada security novo, um **registro plano
(dict/objeto) com um conjunto fixo de campos nomeados e valores em enums específicos**. Existem
pelo menos dois formatos de saída conhecidos que consomem exatamente esse mesmo objeto:

1. **JSON** — lista de objetos, ex. `[{"beehusName": "...", "securityType": "bond", ...}, ...]`.
   É o formato aceito por importação em lote/API do Beehus. Na prática observada neste projeto,
   os lotes são fatiados em arquivos de **até 100 registros** cada (não confirmado como limite
   rígido da API, mas é o padrão adotado — vale considerar como referência de tamanho de lote
   seguro).
2. **Excel/CSV "Web Import"** — uma aba com header na linha 1 (nomes de campo entre aspas duplas
   literais, ex. `"beehusName"`) e uma linha por ativo a partir da linha 2. Strings entre aspas
   duplas, numéricos sem aspas, campos vazios em branco. `feederIds` (que no JSON é uma lista de
   objetos `{id, feederName}`) fica **achatado em duas colunas separadas** nesse formato
   (`id` e `feederName`, referentes ao **primeiro** feeder apenas).

**O que importa não é o formato do arquivo, e sim**:
- os **nomes exatos dos campos** (case-sensitive, iguais aos usados abaixo);
- os **valores enum exatos** (ex. `securityType`, `type`, `indexer`, `currency`, `country`);
- os **placeholders obrigatórios** onde a API não aceita `null` (ex. `maturityDate`);
- duas conversões de formatação específicas (ver seção 3) que mudam dependendo do destino:
  - `taxId` (CNPJ): formatado **com pontuação** para Excel humano-legível, mas **sem pontuação**
    (só os 14 dígitos) quando o destino é o payload/API do Beehus.
  - Para `securityType = "brazilianFund"`: internamente o CNPJ pode ser manipulado no campo
    `ticker`, mas **no objeto final enviado, o CNPJ vai em `taxId` e `ticker` fica vazio/omitido**
    — nunca envie CNPJ no campo `ticker`.

Um outro agente pode escolher qualquer forma de reunir essas informações (um dict Python, uma
lista JSON, um CSV, uma planilha diferente) — o que precisa é gerar, para cada ativo, um registro
com os campos corretos preenchidos segundo as regras abaixo, e no final serializar esse registro
no formato que o sistema de destino (Beehus) espera receber.

---

## 2. Tabela mestra de campos (schema completo observado)

| Campo | Tipo | Uso geral | Observação |
|---|---|---|---|
| `beehusName` | string | Nome de exibição do ativo | Ver `REGRAS_BEEHUSNAME.md` para a fórmula completa por tipo |
| `securityType` | string (enum) | Categoria do ativo | Ver seção 4 — sempre obrigatório |
| `type` | string (enum) ou `null` | Subtipo dentro da categoria | Sempre precisa estar **presente** como chave; `null` só é valor válido para `stockEtf`/`brazilianFund` — em outros tipos, `null` gera erro de API |
| `subscriptionSettlementDays` | integer | Prazo de liquidação na subscrição (dias úteis) | Sempre obrigatório — ver defaults por família na seção 4 |
| `subscriptionNAVDays` | integer | Prazo de conversão em cota na subscrição | Sempre obrigatório |
| `redemptionNAVDays` | integer | Prazo de conversão em cota no resgate | Sempre obrigatório |
| `redemptionSettlementDays` | integer | Prazo de liquidação no resgate | Sempre obrigatório |
| `currency` | string (`"BRL"` \| `"USD"`) | Moeda do ativo | Sempre obrigatório |
| `country` | string (`"BR"` \| `"US"`) ou `null` | País do ativo | Obrigatório na maioria; `null` para título público BR e alguns fundos offshore |
| `maturityDate` | string `"YYYY-MM-DD"` | Data de vencimento | **Nunca `null`** — usar `"2099-01-01"` como placeholder quando não há vencimento real (ex. compromissada) |
| `yield` | number ou `null` | Spread/taxa | `0` para CDI% (não é `null`!); número para IPCA+/CDI+/Pré; `null` só quando a modalidade realmente não usa taxa (ex. título público BR) |
| `indexer` | string (`"CDI"` \| `"IPCA"` \| `"PRE"`) ou `null` | Indexador | `null` quando não aplicável (bonds USD, ações, fundos, título público) |
| `indexerPercentual` | number ou `null` | % do indexador aplicado | Ver tabela da seção 5 |
| `isIn` | string (ISIN, 12 caracteres) | Identificador internacional | **Campo do ID principal** para fundo offshore e título público BR — não confundir com `ticker` |
| `cusip` | string | Identificador alternativo (bonds USD) | Opcional — usar quando o ISIN não está disponível |
| `ticker` | string ou `null` | Identificador/código principal | Obrigatório para renda fixa BR, compromissada, bond USD, COE; **vazio para fundo offshore/título público** (usam `isIn`); **vazio para `brazilianFund`** (CNPJ vai em `taxId`, não aqui) |
| `taxId` | string (CNPJ) | CNPJ do fundo | Obrigatório só para `brazilianFund`. **Com pontuação no Excel; sem pontuação (14 dígitos) no JSON/API** |
| `selicCode` | string | Código Selic do título público | Só para `brazilianGovernmentBond` — campo **separado** de `isIn`; pode ficar vazio |
| `exchange` | string (código MIC) | Bolsa de negociação | Obrigatório só para `stockEtf` — ver tabela de MICs na seção 6 |
| `feederIds` | array de `{id, feederName}` | Vínculo com fonte de cotação/custódia | Obrigatório para fundo offshore e ações/ETFs (BR e USD); vazio (`[]`) nos demais |
| `walletIds` | array | (não populado por esta ferramenta) | Sempre enviar como `[]` se o payload for um objeto explícito |
| `companyIds` | array | (não populado por esta ferramenta) | Sempre enviar como `[]` |
| `issuer` | string | Nome do emissor (anotação livre) | Opcional — útil para rastreabilidade/auditoria interna; confirmar se a API do Beehus de fato usa este campo antes de depender dele |
| `notes` | string | Observação livre | Opcional — usado internamente para sinalizar pendências (ex. "taxa a confirmar"), não confirmado se a API persiste este campo |
| `initialDate` | string `"YYYY-MM-DD"` | Data de emissão/captação | Opcional — usado principalmente em COE para desambiguar tickers duplicados (dois produtos com mesmo emissor/vencimento mas captações em datas diferentes) |
| `cvmKlass`, `klass`, `underlying`, `strike`, `style`, `emission`, `series`, `manager`, `contractSize`, `underlyingMaturityDate`, `buyIndex`, `sellIndex`, `settlementPrice`, `correspondingWallet` | diversos | Campos do schema não utilizados por esta ferramenta | Ver seção 7 — provavelmente relevantes para `futures`/`options`/`privateMarket`/derivativos, não cobertos aqui. Deixar vazio/omitir salvo confirmação específica do schema do Beehus para esses tipos |

---

## 3. Regras de transformação por formato de destino

| Situação | Se o destino é **JSON/API** | Se o destino é **Excel/CSV humano** |
|---|---|---|
| `taxId` (CNPJ) | Só dígitos: `"35755044000106"` | Com pontuação: `"35.755.044/0001-06"` |
| CNPJ de `brazilianFund` | Vai em `taxId`; **omitir/limpar `ticker`** | Mesmo: coluna `ticker` vazia, CNPJ na coluna `taxId` |
| `feederIds` | Array de objetos: `[{"id": "LU:LU123...", "feederName": "CD"}]` | Duas colunas separadas (`id`, `feederName`) do **primeiro** item da lista |
| Campos vazios/`None`/lista vazia | Regra recomendada: **omitir a chave** do objeto (não enviar `null` para campos que não são o placeholder documentado) | Deixar a célula em branco |
| Strings vs números | Strings normais (`"BRL"`), números sem aspas (`100.0`) | Mesma regra — mas nos exemplos de planilha vistos, até strings costumam vir entre aspas duplas literais dentro da célula (convenção específica desse "Web Import"; confirmar se o formato de destino do outro projeto usa a mesma convenção antes de replicá-la) |
| `walletIds`, `companyIds` | Incluir como `[]` mesmo vazio (padrão observado no gerador original) | N/A (não tem coluna equivalente simples) |

---

## 4. Checklist de campos por tipo de ativo

Convenções da tabela: **✅ obrigatório e específico** · `valor fixo` · *depende do ativo* · `—` não aplicável/deixar vazio.

### 4.1 Compromissada
```
securityType = "otc"                 ✅
type         = "brazilianRepo"       ✅
currency     = "BRL"                 ✅
country      = "BR"                  ✅
maturityDate = "2099-01-01"          ✅ (placeholder obrigatório)
indexer      = "CDI"                 ✅
indexerPercentual = % do CDI, ex. 90 ✅ (número, origem: extrato do custódio)
yield        = 0                     ✅
ticker       = "COMP{pct}CDI{código}"  ✅ (gerado — ver REGRAS_BEEHUSNAME.md §6)
subscriptionSettlementDays = subscriptionNAVDays = redemptionNAVDays = redemptionSettlementDays = 0  ✅
isIn / cusip / taxId / exchange / selicCode = —
feederIds = []
```

### 4.2 CDB, LCA, LCI, LCD, CD, CCB, LC (Renda Fixa BR "simples")
```
securityType = "bond"                ✅
type         = "cdb"|"lca"|"lci"|"lcd"|"cd"|"ccb"|"lc"   ✅ (conforme sigla do custódio)
currency     = "BRL", country = "BR" ✅
maturityDate = data real de vencimento (ISO)  ✅ — origem: extrato do custódio
indexer      = "CDI"|"IPCA"|"PRE"    ✅ — origem: taxa informada no extrato
yield        = spread (CDI+/IPCA+) | taxa (PRE) | 0 (CDI%)   ✅
indexerPercentual = % CDI | 100 (CDI+/IPCA+) | null (PRE)    ✅
ticker       = código CETIP do extrato (preferencial) ou sintético (REGRAS_BEEHUSNAME.md §6)  ✅
subscriptionSettlementDays = subscriptionNAVDays = redemptionNAVDays = redemptionSettlementDays = 0  ✅
isIn / cusip / taxId / exchange / selicCode = —
feederIds = []
```

### 4.3 LF / LF-SUB (Letra Financeira)
Mesma base de 4.2, mudando:
```
type = "lf" | "lf-sub"
```
Demais campos idênticos a 4.2 (o que muda entre LF e CDB é só o `beehusName`, não os campos).

### 4.4 LIG, NP
Mesma base de 4.2, mudando:
```
type = "lig" | "np"
```
Idem — diferença é só de nomenclatura, campos técnicos iguais.

### 4.5 CRI, CRA
Mesma base de 4.2, mudando:
```
type = "cri" | "cra"
```

### 4.6 Debênture / Debênture de Infraestrutura
```
type = "debenture" | "infrastructureDebenture"
```
Demais campos iguais a 4.2. (Ver ressalva de nomenclatura no `REGRAS_BEEHUSNAME.md` §4.5.)

### 4.7 Fundo Brasileiro (`brazilianFund`)
```
securityType = "brazilianFund"       ✅
type         = null / vazio          ✅ (válido ser null aqui)
currency     = "BRL"                 ✅
country      = "BR" (ou vazio — confirmar convenção do destino) 
taxId        = CNPJ do fundo         ✅ — origem: extrato ou lookup CVM (`cad_fi.csv`, campo `CNPJ_FUNDO`)
ticker       = — (não usar; CNPJ vai só em taxId)
maturityDate = "2099-01-01" (fundo não tem vencimento — usar mesmo placeholder por segurança, confirmar se API aceita null aqui)
subscriptionSettlementDays = 0       ✅
subscriptionNAVDays        = 0       ✅
redemptionNAVDays          : 999 se condomínio fechado (FII/FIP/FIDC/CVM CONDOM="Fechado"); senão prazo real da lâmina/Economatica; fallback = 1
redemptionSettlementDays   : 999 se condomínio fechado; senão prazo real da lâmina/Economatica; fallback = 4
feederIds    = [{"id": "<CNPJ com pontuação>", "feederName": "CD"}]  quando o fundo tem cota "CD" — caso contrário []
indexer / yield / indexerPercentual = null (renda fixa não se aplica a fundos)
isIn / cusip / selicCode / exchange = —
```
> Origem do nome oficial e da classificação "fechado/aberto": cadastro público CVM
> (`https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv`) — colunas relevantes: `DENOM_SOCIAL`
> (nome), `CONDOM` (Aberto/Fechado), `TP_FUNDO` (tipo, usado como fallback quando `CONDOM` vem
> vazio: FII/FIP/FIDC/FIIM/FMIEE/FUNCINE/FICART/FPCE/FCCE são tratados como fechados por padrão),
> `SIT` (situação — evitar cadastrar fundo com situação "CANCELADA"), `CD_CVM` (código CVM).

### 4.8 Fundo Offshore (ISIN)
```
securityType = "fund"                ✅
type         = "mutualFund"          ✅ (ou "hedgeFund"/"privateEquity" se aplicável)
currency     = "USD"                 ✅
country      = — (geralmente vazio)
ticker       = — (não usar; ID principal vem de isIn)
isIn         = ISIN do fundo (12 caracteres)   ✅ — origem: extrato/Bloomberg/Morningstar
feederIds    = [{"id": "{2 primeiras letras do ISIN}:{ISIN}", "feederName": "CD"}]   ✅
subscriptionNAVDays = 1, subscriptionSettlementDays = 3   ✅
redemptionNAVDays   = 1, redemptionSettlementDays   = 3   ✅
indexer / yield / indexerPercentual / taxId / cusip / selicCode / exchange = —
maturityDate = "2099-01-01" (placeholder, fundo não tem vencimento)
```

### 4.9 COE (Certificado de Operações Estruturadas)
```
securityType = "otc"                 ✅ (NÃO "bond")
type         = "structuredNote"      ✅ (NÃO "coe")
currency     = "BRL", country = "BR" ✅
maturityDate = data real de vencimento do COE  ✅
ticker       = sintético: emissor + abreviação produto/estratégia + data (REGRAS_BEEHUSNAME.md §4.7/§6)  ✅
initialDate  = data de emissão/captação (opcional, mas recomendado para evitar colisão de ticker)
subscriptionSettlementDays = subscriptionNAVDays = redemptionNAVDays = redemptionSettlementDays = 0  ✅
indexer / yield / indexerPercentual / isIn / cusip / taxId / exchange / selicCode = —
feederIds = []
```

### 4.10 Título Público Brasileiro (`brazilianGovernmentBond`)
```
securityType = "brazilianGovernmentBond"   ✅
type         = "ntnb" | "lft" | "ltn" | "ntnb-p"   ✅
ticker       = null (SEMPRE)               ✅
isIn         = ISIN do título (ex. "BRSTNCNTB674")  ✅ — campo correto do ID principal (API deriva mainId daqui)
selicCode    = código numérico Selic, ex. "760198"  — campo separado de isIn, pode ficar vazio
currency     = "BRL", country = null       ✅
yield = null, indexer = null, indexerPercentual = null   ✅ (nunca preencher taxa neste tipo)
subscriptionSettlementDays = subscriptionNAVDays = redemptionNAVDays = redemptionSettlementDays = 0  ✅
cusip / taxId / exchange = —
feederIds = []
```

### 4.11 Ação / ETF / FII — Brasil (`stockEtf`)
```
securityType = "stockEtf"            ✅
type         = null                  ✅ (válido ser null aqui)
currency     = "BRL", country = "BR" ✅
exchange     = "BVMF"                ✅
ticker       = "BR:{TICKER_B3}"      ✅
feederIds    = [{"id": "BR:{TICKER_B3}", "feederName": "CD"}]   ✅
subscriptionSettlementDays = redemptionSettlementDays = 2   ✅
subscriptionNAVDays = redemptionNAVDays = 0                 ✅
indexer / yield / indexerPercentual / isIn / cusip / taxId / selicCode = —
```

### 4.12 Ação / ETF — EUA/Internacional (`stockEtf`)
```
securityType = "stockEtf"            ✅
type         = null                  ✅
currency     = "USD", country = "US" ✅
exchange     = código MIC (ver seção 6)   ✅
ticker       = "US:{SYMBOL}"         ✅
feederIds    = [{"id": "US:{SYMBOL}", "feederName": "CD"}]   ✅
subscriptionSettlementDays = redemptionSettlementDays = 2   ✅
subscriptionNAVDays = redemptionNAVDays = 0                 ✅
indexer / yield / indexerPercentual / isIn / cusip / taxId / selicCode = —
```

### 4.13 Bond USD (corporativo/soberano)
```
securityType = "bond"                ✅
type         = "fixed" (pré) | "floating" (pós)   ✅
currency     = "USD"                 ✅
country      = "US" (emissor americano) | "BR" (emissor BR emitindo no exterior)  ✅
maturityDate = data real de vencimento   ✅
ticker       = ISIN ou CUSIP do bond  ✅ — usar isIn/cusip como campos auxiliares também, se disponíveis
subscriptionSettlementDays = redemptionSettlementDays = 1   ✅
subscriptionNAVDays = redemptionNAVDays = 0                 ✅
feederIds    = []
indexer / yield / indexerPercentual / taxId / exchange / selicCode = —
```

### 4.14 Options (suporte parcial/experimental neste projeto — usar com cautela)
```
securityType = "options"
type         = "call" | "put"
currency     = "USD", country = "US"
maturityDate = data de expiração
ticker       = sintético: "{CALL|PUT}_{ATIVO}_{STRIKE}_{DDMMYYYY}"
```
> Os campos dedicados `underlying`/`strike`/`style` existem no schema mas **não são preenchidos**
> pela lógica observada — o subjacente e o strike ficam só embutidos no `ticker`/`beehusName`.
> Se o schema real do Beehus exigir esses campos separadamente, será necessário confirmar/testar
> diretamente, pois este projeto nunca os populou.

---

## 5. Tabela de indexador/yield/indexerPercentual (recapitulação — renda fixa BR)

| Modalidade | `indexer` | `yield` | `indexerPercentual` |
|---|---|---|---|
| % do CDI | `"CDI"` | `0` | percentual do CDI (ex. `100`, `84`, `95`) |
| CDI + spread | `"CDI"` | spread numérico | `100` |
| IPCA + spread | `"IPCA"` | spread numérico | `100` |
| Pré-fixado | `"PRE"` | taxa numérica | `null` |
| Não se aplica (ações, fundos, bond USD, título público) | `null` | `null` (ou `0` só no caso da compromissada, que é `"CDI"`/`indexerPercentual` real mas `yield=0`) | `null` |

---

## 6. Códigos MIC de exchange (para `stockEtf`)

| Exchange | MIC |
|---|---|
| NYSE | `XNYS` |
| NASDAQ | `XNAS` |
| B3 (Bovespa) | `BVMF` |
| Tokyo | `XTKS` |
| London | `XLON` |
| Paris | `XPAR` |
| Luxembourg | `XLUX` |
| CME | `XCME` |

> ⚠️ Usar sempre o código MIC de 4 letras — enviar o nome da bolsa por extenso (ex. `"NYSE"`)
> gera erro de enum inválido na API.

---

## 7. Campos do schema não utilizados por esta ferramenta

Estes campos aparecem no header/schema observado, mas nenhum parser ou builder deste projeto os
preenche em nenhum fluxo: `cvmKlass`, `klass`, `underlying`, `strike`, `style`, `emission`,
`series`, `manager`, `contractSize`, `underlyingMaturityDate`, `buyIndex`, `sellIndex`,
`settlementPrice`, `correspondingWallet`. Isso sugere que pertencem a tipos de ativo não cobertos
em profundidade aqui (`futures`, `options` mais completos, `privateMarket`, `swap`/`forward`
dentro de `otc`). **Recomendação**: se o outro projeto precisar cadastrar um desses tipos, não
assumir os valores/regras daqui — validar diretamente contra a documentação/schema real da API
Beehus para esse tipo específico antes de gerar o payload.

---

## 8. Como isso é consumido depois

1. O registro de cada ativo (no formato escolhido pelo outro projeto) é agrupado num lote.
2. Ativos que **já existem** no Beehus (por identificador — `ticker`/`isIn`/`taxId`/`mainId`,
   ver conversa anterior sobre matching) **não devem** ser reenviados como cadastro novo — eles
   entram, em vez disso, num fluxo separado de **mapeamento** (custódio → ativo já existente),
   que é um objeto diferente (`{"from": ..., "to": <id do ativo existente>}`), não o objeto de
   cadastro descrito aqui.
3. Só ativos **sem match** (não encontrados por nenhum identificador) devem virar um registro
   novo com todos os campos desta tabela preenchidos.
4. O lote de registros novos é então importado no Beehus — seja via tela de "Web Import"
   (upload de planilha/CSV formatado), seja via API de importação em lote (payload JSON), seja via
   qualquer outro mecanismo que o projeto de destino use. Em todos os casos, o **schema de campos
   e regras de obrigatoriedade por tipo são os mesmos** — o que muda é só a casca de serialização.
5. Boa prática observada neste projeto: dividir lotes grandes em partes de até ~100 registros por
   arquivo/requisição, e sempre gerar também um artefato de conferência humana (lista/planilha)
   antes do import definitivo, já que erros de enum/campo obrigatório só aparecem no momento do
   import real na plataforma.

---

## 9. Checklist final antes de enviar um ativo novo

1. `securityType` e `type` batem com a tabela da seção 4 para a família do ativo?
2. Todos os 4 campos de prazo (`subscription*`/`redemption*`) estão preenchidos com número (nunca `null`)?
3. `maturityDate` está preenchido (real ou placeholder `"2099-01-01"`) — nunca `null`?
4. O identificador principal certo está no campo certo (`ticker` vs `isIn` vs `taxId` vs `selicCode`
   conforme a família — ver seção 4)?
5. `indexer`/`yield`/`indexerPercentual` seguem a tabela da seção 5 (atenção ao caso `yield=0`
   vs `yield=null`, que são diferentes e ambos aparecem em cenários reais)?
6. Para fundo/ação: `feederIds` está no formato `[{"id": ..., "feederName": "CD"}]`?
7. Se o destino final é o payload da API: `taxId` sem pontuação, `ticker` vazio para
   `brazilianFund`, `walletIds`/`companyIds` presentes como `[]`?
8. `exchange` usa código MIC de 4 letras (não nome da bolsa)?
9. Nenhum valor de texto contém aspas duplas literais coladas por engano (erro comum ao copiar de
   planilhas)?