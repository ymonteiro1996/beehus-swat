# Beehus Data API

API REST de leitura para extração dos dados consolidados pela plataforma Beehus.
Somente `GET`. Schema dos itens reflete fielmente as collections do banco —
campos extras de auditoria/sistema estão marcados na coluna **Origem**.

---

## Convenções

- **Base URL:** `https://api.beehus.com.br/v1`
- **Auth:** `Authorization: Bearer <api_key>`. A `companyId` é derivada da chave.
- **Datas:** `YYYY-MM-DD` (string). Instantes: `YYYY-MM-DDTHH:mm:ssZ` (UTC).
- **Fuso de negócio:** America/Sao_Paulo (BRT, UTC-3).
- **IDs:** strings opacas. `_id` interno (`ObjectId`) é exposto como `id`.
- **Paginação:** offset-based — `?limit=N&offset=M`. `limit` máx = `1000`, default = `100`.
- **Range de datas:** sufixos `From`/`To`, inclusivos. Janela máx por chamada = **400 dias**.
- **Filtros multivalorados:** repetir o param ou separar por vírgula (máx 100 valores).
- **Ordenação:** `?sort=campo,-outro` (`-` = desc).
- **Projeção:** `?fields=a,b,c` (`id` sempre incluído).
- **Cache:** respostas vêm com `ETag`. Use `If-None-Match` para receber `304`.

### Envelope de resposta

```json
{
  "data":       [ /* registros */ ],
  "pagination": { "total": 1284, "limit": 100, "offset": 0, "next": "...", "previous": null },
  "meta":       { "requestId": "9c8f...", "generatedAt": "2026-05-07T13:42:11Z" }
}
```

### Erros

```json
{ "error": { "code": "INVALID_PARAMETER", "message": "...", "field": "...", "requestId": "..." } }
```

| HTTP | `code` |
|---|---|
| 400 | `INVALID_PARAMETER`, `MISSING_PARAMETER`, `RANGE_TOO_LARGE` |
| 401 | `UNAUTHENTICATED` |
| 403 | `FORBIDDEN_SCOPE`, `FORBIDDEN_COMPANY` |
| 404 | `NOT_FOUND` |
| 422 | `UNPROCESSABLE` |
| 429 | `RATE_LIMITED` (headers `Retry-After`, `X-RateLimit-*`) |
| 5xx | `INTERNAL_ERROR`, `UNAVAILABLE` |

### Limites

`600 req/min` por chave · `10 req` paralelas · resposta máx `25 MB`.

---

## Endpoints

### `GET /published-position-securities`

Posições publicadas — uma linha por (carteira, data, ativo). Imutável após publicação.

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `positionDateFrom` | `date` | |
| `positionDateTo` | `date` | Default = hoje |
| `walletIds` | `string[]` | |
| `entityIds` | `string[]` | |
| `securityIds` | `string[]` | |
| `groupingIds` | `string[]` | |
| `pricingType` | `string` | `closingPrice` \| `executionPrice` \| `manual` |
| `securityType` | `string` | Filtra pelo tipo do ativo |
| `published` | `bool` | Default `true` |
| `limit`, `offset` | `int` | |
| `sort` | `string` | Campos: `positionDate`, `walletId`, `securityId`, `amount`, `balance` |
| `fields` | `string[]` | |

**Schema do item** — todos os 63 campos da collection.

| Campo | Tipo | Nullable | Origem | Descrição |
|---|---|---|---|---|
| `id` | `string` | não | sistema | Identificador opaco (`_id` da collection) |
| `__v` | `int` | não | sistema | Versão interna do documento (Mongoose) |
| `positionDate` | `date` | não | posição | Data da posição |
| `companyId` | `string` | não | posição | ID da empresa |
| `entityId` | `string` | não | posição | ID da entidade (administrador / custodiante) |
| `entityName` | `string` | não | denormalizado | Nome da entidade |
| `walletId` | `string` | não | posição | ID da carteira |
| `walletName` | `string` | não | denormalizado | Nome da carteira |
| `walletNav` | `number` | não | denormalizado | NAV da carteira na data |
| `walletNavPercentual` | `number` | não | denormalizado | Percentual desta posição no NAV da carteira (decimal) |
| `groupingId` | `string` | não | denormalizado | ID do agrupamento da carteira |
| `groupingNav` | `number` | não | denormalizado | NAV do agrupamento na data |
| `groupingNavPercentual` | `number` | não | denormalizado | Percentual desta posição no NAV do agrupamento (decimal) |
| `accountCode` | `string` | não | carteira | Código contábil da carteira |
| `currency` | `string` | não | carteira | Moeda da carteira (ISO 4217) |
| `startDateConsolidation` | `date` | não | carteira | Data inicial de consolidação da carteira |
| `startDateReturn` | `date` | não | carteira | Data inicial de retorno da carteira |
| `securityId` | `string` | não | posição | ID do ativo |
| `beehusName` | `string` | não | ativo | Nome Beehus do ativo |
| `mainId` | `string` | não | ativo | ID externo principal (ex.: ISIN, ticker) |
| `securityType` | `string` | não | ativo | Tipo do ativo (`stock`, `bond`, `fund`, ...) |
| `pricingId` | `string` | não | ativo | ID do plano de pricing aplicado |
| `pricingType` | `string` | não | ativo | Tipo de pricing (`closingPrice`, `executionPrice`, `manual`) |
| `emissionDate` | `date` | sim | ativo | Data de emissão (renda fixa) |
| `emissionRate` | `string` | sim | ativo | Taxa de emissão (renda fixa) |
| `maturityDate` | `date` | sim | ativo | Data de vencimento (renda fixa) |
| `redemptionNAVDays` | `int` | não | ativo | Cotização do resgate (D+N) |
| `redemptionSettlementDays` | `int` | não | ativo | Liquidação do resgate (D+N) |
| `subscriptionNAVDays` | `int` | não | ativo | Cotização da aplicação (D+N) |
| `subscriptionSettlementDays` | `int` | não | ativo | Liquidação da aplicação (D+N) |
| `quantity` | `number` | não | posição | Quantidade detida na data |
| `pu` | `number` | não | posição | Preço unitário do ativo na data |
| `amount` | `number` | não | posição | `quantity × pu` |
| `balance` | `number` | não | posição | Saldo financeiro na moeda da carteira |
| `executionPrice` | `number` | não | posição | Preço de execução aplicado |
| `formerQuantity` | `number` | não | posição | Quantidade na data anterior |
| `formerPu` | `number` | não | posição | PU na data anterior |
| `formerBalance` | `number` | não | posição | Saldo financeiro na data anterior |
| `initialBalance` | `number` | não | posição | Saldo financeiro de entrada do ativo na carteira |
| `initialDateOnWallet` | `date` | não | posição | Data de entrada do ativo na carteira |
| `lastInputDate` | `date` | não | posição | Data da última atualização vinda do administrador |
| `indexNumber` | `number` | não | posição | Número índice (1.000 base na entrada) |
| `rentability` | `number` | não | posição | Rentabilidade acumulada do ativo na carteira (decimal) |
| `dailyContribution` | `number` | não | retorno | Contribuição diária ao retorno (decimal) |
| `intradayContribution` | `number` | não | retorno | Contribuição intraday ao retorno (decimal) |
| `eventContribution` | `number` | não | retorno | Contribuição por evento ao retorno (decimal) |
| `totalContribution` | `number` | não | retorno | Soma das contribuições na janela (decimal) |
| `totalContributionInBrl` | `number` | não | retorno | `totalContribution` convertido para BRL |
| `fxContributionInBrl` | `number` | não | retorno | Contribuição cambial em BRL |
| `variable1` | `string` | não | classificação | Nível 1 da classificação hierárquica |
| `variable2` | `string` | não | classificação | Nível 2 |
| `variable3` | `string` | sim | classificação | Nível 3 |
| `variable4` | `string` | sim | classificação | Nível 4 |
| `variable5` | `string` | sim | classificação | Nível 5 |
| `variableAlias1` | `string` | sim | classificação | Alias (nome de exibição) do nível 1 |
| `variableAlias2` | `string` | sim | classificação | Alias do nível 2 |
| `variableAlias3` | `string` | sim | classificação | Alias do nível 3 |
| `variableAlias4` | `string` | sim | classificação | Alias do nível 4 |
| `variableAlias5` | `string` | sim | classificação | Alias do nível 5 |
| `inputType` | `string` | não | sistema | Origem do registro (`web`, `sheets`, `api`, ...) |
| `published` | `bool` | não | sistema | Estado de publicação |
| `createdAt` | `datetime` | não | sistema | Criação do documento (UTC) |
| `updatedAt` | `datetime` | não | sistema | Última atualização (UTC) |

**Exemplo de item**

```json
{
  "id":                          "65a1b2c3d4e5f6a7b8c9d0e1",
  "__v":                         0,
  "positionDate":                "2026-04-30",
  "companyId":                   "10000000000000",
  "entityId":                    "67c0a6b471f5e8c88f76044b",
  "entityName":                  "BTG Pactual DTVM",
  "walletId":                    "68bb268b9a9a11e087ee53de",
  "walletName":                  "Fundo Alpha FIA",
  "walletNav":                   1909000.00,
  "walletNavPercentual":         0.804137,
  "groupingId":                  "67abc12345def67890123456",
  "groupingNav":                 5210000.00,
  "groupingNavPercentual":       0.294636,
  "accountCode":                 "12345-6",
  "currency":                    "BRL",
  "startDateConsolidation":      "2024-01-02",
  "startDateReturn":             "2024-01-02",
  "securityId":                  "67fee83756e1234567890abc",
  "beehusName":                  "BBDC4",
  "mainId":                      "BRBBDCACNPR8",
  "securityType":                "stock",
  "pricingId":                   "65f0a1b2c3d4e5f6a7b8c9d0",
  "pricingType":                 "closingPrice",
  "emissionDate":                null,
  "emissionRate":                null,
  "maturityDate":                null,
  "redemptionNAVDays":           0,
  "redemptionSettlementDays":    2,
  "subscriptionNAVDays":         0,
  "subscriptionSettlementDays":  2,
  "quantity":                    15000,
  "pu":                          102.34,
  "amount":                      1535100.00,
  "balance":                     1535100.00,
  "executionPrice":              102.34,
  "formerQuantity":              15000,
  "formerPu":                    101.10,
  "formerBalance":               1516500.00,
  "initialBalance":              1500000.00,
  "initialDateOnWallet":         "2024-03-15",
  "lastInputDate":               "2026-04-30",
  "indexNumber":                 1023.4,
  "rentability":                 0.0234,
  "dailyContribution":           0.00041,
  "intradayContribution":        0.00000,
  "eventContribution":           0.00012,
  "totalContribution":           0.00128,
  "totalContributionInBrl":      0.00128,
  "fxContributionInBrl":         0,
  "variable1":                   "Renda Variável",
  "variable2":                   "Ações Brasil",
  "variable3":                   null,
  "variable4":                   null,
  "variable5":                   null,
  "variableAlias1":              null,
  "variableAlias2":              null,
  "variableAlias3":              null,
  "variableAlias4":              null,
  "variableAlias5":              null,
  "inputType":                   "api",
  "published":                   true,
  "createdAt":                   "2026-04-30T22:12:08Z",
  "updatedAt":                   "2026-05-01T11:08:33Z"
}
```

`GET /published-position-securities/{id}` retorna um item.

---

### `GET /nav-packages`

Pacotes consolidados de NAV por carteira ou agrupamento.

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `positionDateFrom` | `date` | |
| `positionDateTo` | `date` | Default = hoje |
| `walletIds` | `string[]` | |
| `groupingIds` | `string[]` | |
| `level` | `string` | `wallet` (default) \| `grouping` |
| `published` | `bool` | Omitir = ambos |
| `includeTrashed` | `bool` | Default `false` |
| `limit`, `offset` | `int` | |
| `sort` | `string` | Campos: `positionDate`, `walletId`, `groupingId`, `nav`, `navPerShare`, `returnNavPerShare` |
| `fields` | `string[]` | |

**Schema do item**

| Campo | Tipo | Nullable | Origem | Descrição |
|---|---|---|---|---|
| `id` | `string` | não | sistema | Identificador opaco (`_id`) |
| `companyId` | `string` | não | pacote | |
| `walletId` | `string` | sim | pacote | Presente quando o pacote é da carteira |
| `groupingId` | `string` | sim | pacote | Presente quando o pacote é do agrupamento |
| `positionDate` | `date` | não | pacote | |
| `currency` | `string` | não | pacote | Moeda do pacote (ISO 4217) |
| `nav` | `number` | não | pacote | Patrimônio líquido |
| `navPerShare` | `number` | não | pacote | Valor da cota |
| `amount` | `number` | não | pacote | Quantidade total de cotas |
| `formerAmount` | `number` | não | pacote | Quantidade total de cotas no dia anterior |
| `inAndOutFlows` | `number` | não | pacote | Aportes − resgates do dia |
| `returnNavPerShare` | `number` | não | pacote | Retorno do dia derivado da cota (decimal) |
| `returnContribution` | `number` | não | pacote | Retorno do dia derivado das contribuições (decimal) |
| `published` | `bool` | não | sistema | Estado de publicação |
| `trashed` | `bool` | não | sistema | Soft-delete (sempre `false` salvo `includeTrashed=true`) |
| `trashedData` | `array` | não | sistema | Histórico de soft-deletes (vazio quando `trashed=false`) |
| `createdAt` | `datetime` | não | sistema | |
| `updatedAt` | `datetime` | não | sistema | |

> **Conciliação:** `|returnNavPerShare − returnContribution|` ≤ `1e-5`
> (= 0,001%) indica pacote conciliado.

**Exemplo de item**

```json
{
  "id":                  "65a1b2c3d4e5f6a7b8c9d0e1",
  "companyId":           "10000000000000",
  "walletId":            "68bb268b9a9a11e087ee53de",
  "groupingId":          null,
  "positionDate":        "2026-04-30",
  "currency":            "BRL",
  "nav":                 1909000.00,
  "navPerShare":         96.0,
  "amount":              19885.42,
  "formerAmount":        20000.0,
  "inAndOutFlows":       0,
  "returnNavPerShare":   -0.04,
  "returnContribution":  -0.04,
  "published":           true,
  "trashed":             false,
  "trashedData":         [],
  "createdAt":           "2026-04-30T22:12:08Z",
  "updatedAt":           "2026-05-01T11:08:33Z"
}
```

**Auxiliares**

- `GET /nav-packages/{id}` — item.
- `GET /nav-packages/wallets/{walletId}/timeseries?positionDateFrom=...&positionDateTo=...` — série diária, sem paginação (janela máx 400 dias).

---

### `GET /transactions`

Movimentações financeiras. Pelo menos um par `liquidationDate*` ou `operationDate*` é obrigatório.

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `liquidationDateFrom` | `date` | |
| `liquidationDateTo` | `date` | |
| `operationDateFrom` | `date` | |
| `operationDateTo` | `date` | |
| `walletIds` | `string[]` | |
| `entityIds` | `string[]` | |
| `securityIds` | `string[]` | |
| `beehusTransactionTypes` | `string[]` | Ver enum abaixo |
| `identified` | `bool` | `true` = somente classificadas; `false` = sem `beehusTransactionType` |
| `currencyId` | `string` | |
| `includeHidden` | `bool` | Default `false` |
| `includeTrashed` | `bool` | Default `false` |
| `limit`, `offset` | `int` | |
| `sort` | `string` | Campos: `liquidationDate`, `operationDate`, `walletId`, `balance` |
| `fields` | `string[]` | |

**Schema do item**

| Campo | Tipo | Nullable | Origem | Descrição |
|---|---|---|---|---|
| `id` | `string` | não | sistema | Identificador opaco (`_id`) |
| `companyId` | `string` | não | transação | |
| `entityId` | `string` | não | transação | |
| `walletId` | `string` | não | transação | |
| `securityId` | `string` | sim | transação | Ausente em transações puramente financeiras |
| `currencyId` | `string` | não | transação | ISO 4217 |
| `operationDate` | `date` | não | transação | Data da operação |
| `liquidationDate` | `date` | não | transação | Data de liquidação financeira |
| `balance` | `number` | não | transação | Impacto no caixa (positivo = entrada; negativo = saída) |
| `quantity` | `number` | não | transação | Sinal: + compra, − venda |
| `price` | `number` | não | transação | Preço unitário |
| `beehusTransactionType` | `string` | sim | classificação | `null` = não classificada |
| `description` | `string` | não | transação | Descrição livre |
| `comment` | `string` | não | transação | Comentário interno |
| `correspondingWallet` | `string` | sim | transação | Carteira contraparte (transferências entre carteiras) |
| `score` | `number` | sim | classificação | Score do classificador (debug) |
| `improvedScore` | `number` | sim | classificação | Score aprimorado (debug) |
| `inputType` | `string` | não | sistema | Origem do registro (`web`, `sheets`, `api`, `pdf`, ...) |
| `hide` | `bool` | não | sistema | `true` esconde o registro de relatórios padrão |
| `createdProcessedTransaction` | `bool` | não | sistema | Indica se gerou transação processada vinculada |
| `fileData` | `object` | não | sistema | `{ sentToBucket: bool }` — metadados do arquivo de origem |
| `updateHistory` | `array<object>` | não | sistema | Histórico de updates (auditoria) — ver schema abaixo |
| `trashed` | `bool` | não | sistema | Soft-delete |
| `trashedData` | `array` | não | sistema | Histórico de soft-deletes |
| `createdAt` | `datetime` | não | sistema | |
| `updatedAt` | `datetime` | não | sistema | |

**`updateHistory[]` schema** — registro de mudanças por update:

| Campo | Tipo |
|---|---|
| `formerBalance`, `updatedBalance` | `number` |
| `formerBeehusTransactionType`, `updatedBeehusTransactionType` | `string` |
| `formerDescription`, `updatedDescription` | `string` |
| `formerLiquidationDate`, `updatedLiquidationDate` | `date` |
| `formerOperationDate`, `updatedOperationDate` | `date` |
| `formerSecurityId`, `updatedSecurityId` | `string` |
| `updatedAt` | `datetime` |
| `updatedBy` | `string` (user id ou identificador) |

**`beehusTransactionType` enum**

`amortization`, `brokerageFee`, `buySell`, `bzFundTaxes`,
`contributionAdjustment`, `coupon`, `dividend`, `dividendOnboarding`,
`gainsExpenses`, `interestOnEquity`, `managementFee`, `maturity`, `other`,
`otherFee`, `performanceFee`, `rebate`, `securityContributionAdjustment`,
`securityTransfer`, `taxes`, `withdrawalDeposit`, `withdrawalDepositAdjustment`.
`null` = não classificada.

**Exemplo de item**

```json
{
  "id":                          "65a1b2c3d4e5f6a7b8c9d0e1",
  "companyId":                   "10000000000000",
  "entityId":                    "67c0a6b471f5e8c88f76044b",
  "walletId":                    "68bb268b9a9a11e087ee53de",
  "securityId":                  "67fee83756e1234567890abc",
  "currencyId":                  "BRL",
  "operationDate":               "2026-02-10",
  "liquidationDate":             "2026-02-10",
  "balance":                     3000.00,
  "quantity":                    -1000,
  "price":                       5.00,
  "beehusTransactionType":       "buySell",
  "description":                 "Resgate Fund J — IR retido na fonte",
  "comment":                     "",
  "correspondingWallet":         null,
  "score":                       0.92,
  "improvedScore":               0.97,
  "inputType":                   "api",
  "hide":                        false,
  "createdProcessedTransaction": true,
  "fileData":                    { "sentToBucket": true },
  "updateHistory": [
    {
      "formerBalance":                3500.00,
      "updatedBalance":               3000.00,
      "formerBeehusTransactionType":  "buySell",
      "updatedBeehusTransactionType": "buySell",
      "formerDescription":            "Resgate Fund J",
      "updatedDescription":           "Resgate Fund J — IR retido na fonte",
      "formerLiquidationDate":        "2026-02-10",
      "updatedLiquidationDate":       "2026-02-10",
      "formerOperationDate":          "2026-02-10",
      "updatedOperationDate":         "2026-02-10",
      "formerSecurityId":             "67fee83756e1234567890abc",
      "updatedSecurityId":            "67fee83756e1234567890abc",
      "updatedAt":                    "2026-02-10T19:05:42Z",
      "updatedBy":                    "user@cliente.com"
    }
  ],
  "trashed":     false,
  "trashedData": [],
  "createdAt":   "2026-02-10T18:22:01Z",
  "updatedAt":   "2026-02-10T19:05:42Z"
}
```

`GET /transactions/{id}` retorna um item.

---

### `GET /cash-accounts`

Documento por carteira contendo o histórico de saldos em caixa no array
`values[]`. Para consumo desnormalizado (uma linha por data),
ver [`/cash-accounts/balances`](#get-cash-accountsbalances).

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `walletIds` | `string[]` | |
| `currency` | `string` | |
| `includeTrashed` | `bool` | Default `false` |
| `limit`, `offset` | `int` | |
| `sort` | `string` | Campos: `walletId`, `updatedAt` |
| `fields` | `string[]` | |

**Schema do item**

| Campo | Tipo | Nullable | Origem | Descrição |
|---|---|---|---|---|
| `id` | `string` | não | sistema | Identificador opaco (`_id`) |
| `companyId` | `string` | não | conta | |
| `walletId` | `string` | não | conta | |
| `unprocessedId` | `string` | não | conta | Identificador externo do administrador |
| `currency` | `string` | não | conta | Moeda do caixa (ISO 4217) |
| `values` | `array<object>` | não | conta | Histórico de saldos: `[{ date, value }]` |
| `values[].date` | `date` | não | conta | |
| `values[].value` | `number` | não | conta | Saldo em caixa naquela data |
| `updateHistory` | `array<object>` | não | sistema | Histórico de updates por data — ver schema abaixo |
| `trashed` | `bool` | não | sistema | Soft-delete |
| `trashedData` | `array` | não | sistema | |
| `createdAt` | `datetime` | não | sistema | |
| `updatedAt` | `datetime` | não | sistema | |

**`updateHistory[]` schema**

| Campo | Tipo | Descrição |
|---|---|---|
| `date` | `date` | Data afetada pelo update |
| `formerValue` | `number` | Valor anterior |
| `currentValue` | `number` | Valor após o update |
| `updatedAt` | `datetime` | Timestamp do update |
| `updatedBy` | `string` | Usuário ou processo que disparou |
| `updatedByExternalSource` | `bool` | `true` quando vindo de carga externa |

**Exemplo de item**

```json
{
  "id":            "65a1b2c3d4e5f6a7b8c9d0e2",
  "companyId":     "10000000000000",
  "walletId":      "68bb268b9a9a11e087ee53de",
  "unprocessedId": "CC-12345-6",
  "currency":      "BRL",
  "values": [
    { "date": "2026-04-29", "value": 1250.50 },
    { "date": "2026-04-30", "value": 980.12 }
  ],
  "updateHistory": [
    {
      "date":                    "2026-04-30",
      "formerValue":             1100.00,
      "currentValue":            980.12,
      "updatedAt":               "2026-04-30T22:08:14Z",
      "updatedBy":               "ingestion-pipeline",
      "updatedByExternalSource": true
    }
  ],
  "trashed":     false,
  "trashedData": [],
  "createdAt":   "2024-03-15T11:00:00Z",
  "updatedAt":   "2026-04-30T22:08:14Z"
}
```

#### `GET /cash-accounts/balances`

Saldos de caixa **desnormalizados** — uma linha por (`walletId`, `date`),
extraída do array `values[]`.

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `dateFrom`, `dateTo` | `date` | Obrigatório |
| `walletIds` | `string[]` | |
| `currency` | `string` | |
| `limit`, `offset`, `sort`, `fields` | — | Sortável: `date`, `walletId`, `value` |

**Schema do item desnormalizado**

| Campo | Tipo | Nullable |
|---|---|---|
| `walletId` | `string` | não |
| `currency` | `string` | não |
| `date` | `date` | não |
| `value` | `number` | não |

**Auxiliares**

- `GET /cash-accounts/balances/{walletId}/{date}` — saldo único; `404` se ausente.
- `GET /cash-accounts/wallets/{walletId}/timeseries?dateFrom=...&dateTo=...` — série diária.

---

### `GET /provisions`

Valores provisionados. Use **um** dentre os modos de filtro de data:

| Modo | Param(s) | Match |
|---|---|---|
| Ativa em data | `activeOn` | `initialDate ≤ activeOn ≤ liquidationDate` |
| Ativa em janela | `activeBetweenFrom`, `activeBetweenTo` | overlap com a janela |
| Estrito | `initialDateFrom/To` ou `liquidationDateFrom/To` | filtro literal nos campos |

Combinar modos = `400`.

**Query parameters**

| Param | Tipo | Notas |
|---|---|---|
| `activeOn` | `date` | |
| `activeBetweenFrom` | `date` | |
| `activeBetweenTo` | `date` | |
| `initialDateFrom`, `initialDateTo` | `date` | |
| `liquidationDateFrom`, `liquidationDateTo` | `date` | |
| `walletIds` | `string[]` | |
| `securityIds` | `string[]` | |
| `provisionTypes` | `string[]` | Mesmos códigos de `beehusTransactionType` |
| `provisionSources` | `string[]` | `adjustments`, `corporate-actions`, `auto`, ... |
| `currencyId` | `string` | |
| `includeTrashed` | `bool` | Default `false` |
| `limit`, `offset` | `int` | |
| `sort` | `string` | Campos: `initialDate`, `liquidationDate`, `walletId`, `balance` |
| `fields` | `string[]` | |

**Schema do item**

| Campo | Tipo | Nullable | Origem | Descrição |
|---|---|---|---|---|
| `id` | `string` | não | sistema | Identificador opaco (`_id`) |
| `__v` | `int` | não | sistema | Versão interna do documento |
| `companyId` | `string` | não | provisão | |
| `walletId` | `string` | não | provisão | |
| `securityId` | `string` | não | provisão | |
| `currencyId` | `string` | não | provisão | ISO 4217 |
| `initialDate` | `date` | não | provisão | Início da janela de impacto |
| `liquidationDate` | `date` | não | provisão | Liquidação prevista |
| `balance` | `number` | não | provisão | Valor provisionado (sinal: + a receber, − a pagar) |
| `provisionType` | `string` | não | provisão | Mesmos códigos de `beehusTransactionType` |
| `provisionSource` | `string` | não | provisão | `adjustments`, `corporate-actions`, `auto`, ... |
| `description` | `string` | não | provisão | Descrição livre |
| `correspondingWallet` | `string` | sim | provisão | Carteira contraparte (transferências entre carteiras) |
| `alreadyIncludedInProcessedPosition` | `bool` | não | sistema | `true` se a provisão já foi consolidada na posição processada |
| `updateHistory` | `array` | não | sistema | Histórico de updates |
| `trashed` | `bool` | não | sistema | Soft-delete |
| `trashedData` | `array` | não | sistema | |
| `createdAt` | `datetime` | não | sistema | |
| `updatedAt` | `datetime` | não | sistema | |

> **Múltiplas provisões ativas** podem coexistir para `(walletId, securityId)`
> — em consolidações, somar.

**Exemplo de item**

```json
{
  "id":                                "65a1b2c3d4e5f6a7b8c9d0e3",
  "__v":                               0,
  "companyId":                         "10000000000000",
  "walletId":                          "68bb268b9a9a11e087ee53de",
  "securityId":                        "67fee83756e1234567890abc",
  "currencyId":                        "BRL",
  "initialDate":                       "2026-04-01",
  "liquidationDate":                   "2026-04-30",
  "balance":                           1303.68,
  "provisionType":                     "dividend",
  "provisionSource":                   "adjustments",
  "description":                       "JCP BBDC4",
  "correspondingWallet":               null,
  "alreadyIncludedInProcessedPosition": true,
  "updateHistory":                     [],
  "trashed":                           false,
  "trashedData":                       [],
  "createdAt":                         "2026-03-29T10:14:02Z",
  "updatedAt":                         "2026-03-29T10:14:02Z"
}
```

`GET /provisions/{id}` retorna um item.

---

## Suporte

`tecnologia@beehus.com.br` — incluir o `requestId` (header `X-Request-Id` ou `meta.requestId`).
