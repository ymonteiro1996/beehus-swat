# Geração de Arquivos JSON

> Documentação global dos templates de arquivo JSON usados para upload ao banco de dados. A partir desta iteração, a **única** página que gera arquivos exportáveis é a Correções (`/correcoes`); os templates abaixo descrevem o formato das linhas armazenadas lá e exportadas em JSON ou XLSX.

---

## Templates

### 1. Transactions

**Usado por:** Correções (fonte única de geração)

```json
{
  "companyId": "...",
  "transactions": [
    {
      "companyId": "00000000000000",
      "entityId": "67c0a6b471f5e8c88f76044b",
      "walletId": "68bb268b9a9a11e087ee53de",
      "currencyId": "BRL",
      "securityId": "...",
      "operationDate": "2026-02-24",
      "liquidationDate": "2026-02-24",
      "balance": -20000000,
      "description": "Resgate de Cotas PF",
      "inputType": "sheets",
      "beehusTransactionType": "withdrawalDeposit",
      "hide": false,
      "comment": ""
    }
  ]
}
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `companyId` | `db.wallets` → wallet.companyId |
| `entityId` | `db.wallets` → wallet.entityId |
| `walletId` | Wallet de origem ou destino |
| `currencyId` | `db.wallets` → wallet.currencyId |
| `securityId` | Do security associado (campo omitido se não há security) |
| `operationDate` | Data da operação |
| `liquidationDate` | Data de liquidação |
| `balance` | Valor financeiro |
| `description` | Descrição da transação |
| `inputType` | `"sheets"` |
| `beehusTransactionType` | Tipo: `buySell`, `withdrawalDeposit`, `dividend`, `gainsExpenses`, etc. |
| `hide` | `true` para correções |
| `comment` | Observação livre |

---

### 2. Wallets

**Legado** — formato mantido apenas como referência (feature Replicar Cenário foi removida, e Correções só exporta Transactions/Provisions).

```json
[
  {
    "name": "6485826",
    "hasDailyPosition": true,
    "companyId": "10000000000000",
    "currency": "BRL",
    "startDateConsolidation": "2026-03-11",
    "startDateReturn": "2026-03-11",
    "entityId": "67cf6a5c71f5e8c88f760505",
    "accountCode": "6485826",
    "consumptionIdentifiers": [],
    "securitiesForExplosion": []
  }
]
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `name` | Nome da carteira |
| `hasDailyPosition` | Se possui posição diária |
| `companyId` | ID da empresa |
| `currency` | Moeda da carteira |
| `startDateConsolidation` | Data de início da consolidação |
| `startDateReturn` | Data de início do retorno |
| `entityId` | ID da entidade |
| `accountCode` | Código da conta |
| `consumptionIdentifiers` | Lista de identificadores de consumo |
| `securitiesForExplosion` | Lista de securities para explosão |

---

### 3. Positions (unprocessedSecurities)

**Legado** — formato mantido apenas como referência (feature Replicar Cenário foi removida, e Correções só exporta Transactions/Provisions).

```json
{
  "companyId": "86246110100000",
  "unprocessedSecurities": [
    {
      "date": "2025-01-31",
      "walletId": "680928415ea164e619dc813d",
      "security": "INVESCO S&P 500 EQUAL WEIGHT",
      "quantity": 1837,
      "pu": 332937.88,
      "balance": 181.24,
      "currencyId": "USD",
      "cashAccount": "Nao"
    }
  ]
}
```

**Campos e origem:**

| Campo | Origem |
|-------|--------|
| `companyId` | ID da empresa |
| `date` | Data da posição |
| `walletId` | ID da carteira (destino) |
| `security` | Nome do security (`beehusName`) |
| `quantity` | Quantidade |
| `pu` | Preço unitário |
| `balance` | Saldo financeiro (`pu × quantity`) |
| `currencyId` | Moeda do security |
| `cashAccount` | Se é conta caixa (`"Sim"` / `"Nao"`) |

---

### 4. Provisions (clipboard/Excel)

**Usado por:** Correções (fonte única de geração)

Provisões não possuem upload JSON no momento. O sistema gera uma **tabela copiável** (clipboard) que pode ser colada diretamente em uma planilha Excel.

**Colunas:**

| Coluna | Origem |
|--------|--------|
| `walletId` | ID da carteira |
| `initialDate` | Data de início da provisão (formato `DD/MMM/YY`, ex: `01/abr/24`) |
| `liquidationDate` | Data de liquidação (formato `DD/MMM/YY`) — ver regra de offset abaixo |
| `provisionType` | Tipo: `dividend`, `buySell`, etc. |

> **Regra de datas da provisão (`MISSING_PROVISION` / `WRONG_PROVISION_AMOUNT`):**
> a janela é ancorada na **data do navPackage em análise** e tem `|offset|` dias —
> `liquidationDate = data do navPackage + offset` (nunca a data de "hoje"). Para
> `offset > 0` (liquidação futura): `initialDate = data`, `liquidationDate = data+offset`;
> para `offset < 0` (nav futuro): `initialDate = data+offset`, `liquidationDate = data`;
> para `offset == 0`: ambas iguais à data. A regra é centralizada no backend
> (`_prov_dates` em `generate_provisions`) e aplicada em **todos** os caminhos,
> inclusive o override manual "+ Provisão" — que antes ignorava o offset.
>
> **Mínimo de 1 dia útil:** a `liquidationDate` é sempre clampada para no mínimo
> **data do nav + 1 dia útil** (`max(computado, próximo dia útil)`, Seg–Sex sem
> feriados). Assim os casos `offset ≤ 0` nunca geram liquidação no mesmo dia/passado.
| `securityId` | ID do security |
| `balance` | Valor da provisão |
| `description` | Descrição da provisão |
| `provisionSource` | Origem: `adjustments` |
| `currencyId` | Moeda: `BRL`, `USD`, etc. |

**Exemplo:**

```
walletId	initialDate	liquidationDate	provisionType	securityId	balance	description	provisionSource	currencyId
688246985...	01/abr/24	30/abr/25	dividend	67fee83756e	1303,68	Juros sobre capital próprio de BBDC4 a receber em 30/abr/2025	adjustments	BRL
```

> **Nota:** Upload JSON para provisions será adicionado futuramente. Até lá, o fluxo é copiar a tabela e colar na planilha de upload.

---

## Uso por Página

### Conciliação — Correções

Após o diagnóstico (Steps 1-6), o analista aceita itens identificados como causa do gap.

**Fluxo (envio direto via API):**

```
Diagnóstico → Aceitar item → Modal "Revisar e enviar via API"
            → (analista revisa/edita as linhas) → Confirmar
            → grava em /correcoes (auditoria) + envia direto pra API Beehus
```

Ao aceitar (botão **Aceitar**, **+ Transação**, **+ Provisão** ou "Aceitar
Correção" do Bayesiano), o sistema gera as linhas (transações, provisões,
preços de execução) e abre um **modal de confirmação editável** — o mesmo
procedimento de edição da página Correções. Ao confirmar, as linhas são
gravadas em `data/correcoes/...` (trilha de auditoria, idempotente por
`sourceAnomalyKey`) **e** enviadas imediatamente à API Beehus via
`POST /api/correcoes/bulk-submit` (ver [CORRECOES.md](CORRECOES.md)). As linhas
que falharem no envio permanecem em `/correcoes` (com `inputed=false`) para
reenvio. `deletions` (MISCLASSIFIED) são gravadas como auditoria mas **não**
são enviadas — a transação original precisa ser removida manualmente no upstream.

**Itens aceitáveis:**

| Step | Tipo | Quando aparece |
|------|------|---------------|
| 3.1 | Amount Difference | Transação ou provisão faltando |
| 3.2 | Rentability Difference | Evento errado, provisão errada, ou ausente |
| 3.3 | Withholding Tax / Execution Price | IR, execution price, ou valor errado |
| 4.1 | Transação não identificada | `beehusTransactionType == null` |
| 4.2 | Security divergente | Security não está na posição |
| 5 | Cash mismatch | Diferença entre caixa projetado e atual |

**Mapeamento flag → transactionType:**

| Flag | `beehusTransactionType` |
|------|------------------------|
| `MISSING_TRANSACTION` | `buySell` |
| `MISSING_EVENT` | `coupon` |
| `WITHHOLDING_TAX` | `taxes` (ver nota abaixo) |
| `MISSING_EXECUTION_PRICE` | — (vai para o bucket `executionPrices`, não vira transação) |
| `WRONG_TRANSACTION_VALUE` | `buySell` |
| `CASH_MISMATCH` | `gainsExpenses` |

> **`WITHHOLDING_TAX` → `taxes` com valor negativo:** IR retido na fonte é uma
> **saída de caixa**, então a transação de correção é gerada com
> `beehusTransactionType = "taxes"` e `balance = -|impact|` (o `impact` do
> diagnóstico é sempre uma magnitude positiva — `abs(expected − actual)` no
> Step 3.3 —, multiplicado por −1 na geração). O tipo `taxes` entra em
> `inAndOutFlows` no recálculo (ver [CONCILIACAO_RECALCULO.md](CONCILIACAO_RECALCULO.md)),
> coerente com o tratamento de IR como dinheiro que saiu. A transação mantém
> o `securityId` do ativo (o motor de precificação casa `taxes` por securityId
> na mesma data do cupom/amortização — ver [PRECIFICACAO.md](PRECIFICACAO.md)).

**Flags que não geram transações:**
- `MISSING_PROVISION` — provisão (futuro Excel)
- `WRONG_PROVISION_AMOUNT` — ajuste de provisão
- `WRONG_EVENT_BALANCE` — transação existente com valor errado
- `WRONG_SECURITY` — reclassificação
- `UNCLASSIFIED_TRANSACTION` — reclassificação

**Indicador "Provável Causa":** quando o impacto de um flag é igual ao gap (±1%), um badge **"PROVÁVEL CAUSA"** aparece.

---

