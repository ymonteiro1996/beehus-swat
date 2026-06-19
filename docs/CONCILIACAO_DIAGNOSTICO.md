# Conciliação NAV

> `returnNavPerShare` é a fonte de verdade. O objetivo é fazer `returnContribution` convergir para esse valor.

---

## Princípio

O NAV é calculado a partir de saldos reais (posições, caixa, provisões). A cota deriva do NAV. Quando `returnContribution` diverge de `returnNavPerShare`, o problema está nos dados que alimentam o cálculo de contribuição — não no NAV.

A conciliação busca **corrigir `returnContribution`**, não "explicar o gap".

---

## Resolução da data anterior (`formerDate` / `formerNav`)

A **única fonte de verdade** para `formerDate` e `formerNav` é a coleção `navPackages`:

```
formerDate, formerNav = navPackages.find_one(
    walletId     = <wallet>,
    positionDate = {"$lt": <date>},
    trashed      = {"$ne": true}   # ignora pacotes rascunho/lixeira
).sort(positionDate DESC).limit(1)
```

- **Per-wallet.** Cada carteira tem sua própria `formerDate`; nunca é derivada de uma data "global".
- **Nunca use `processedPosition` para determinar a data anterior.** Um `processedPosition` pode existir em um dia onde o `navPackage` não foi calculado (ou foi descartado). Se essa data fosse adotada como referência, o `returnNavPerShare` seria computado sobre múltiplos dias de preços e geraria retornos diários absurdos (ex.: 138% em um dia).
- **Ignore `navPackages.formerNav` armazenado em `<date>`.** Esse campo é uma cópia em cache do momento em que o pacote foi gerado e pode apontar para um pacote que foi descartado depois. Sempre consulte o navPackage anterior real.
- O `processedPosition` **ainda é usado** para popular `formerMap` (PU/quantidade por security), mas sempre na data exata retornada pela consulta acima — nunca em "a data mais recente antes de `date`".

### Carteira sem navPackage anterior

Quando não existe nenhum `navPackage` anterior não-descartado para a carteira:

- `/api/conciliacao/rows` → carteira é **excluída** da listagem (fora do escopo).
- `/api/conciliacao/diagnose` → retorna **HTTP 404** com `error: "Carteira sem navPackage anterior — fora do escopo de análise."`.
- `/api/conciliacao/wallet-detail` → `formerDate = null` e `formerMap = {}` (não há posição de referência).

### navPackage sem `walletId` (órfão)

Podem existir `navPackages` não-descartados com `walletId` **null/ausente/vazio**
(artefato de geração upstream — documentos órfãos cujo `nav` espelha o de uma
carteira real). Esses pacotes são **excluídos** de toda listagem por carteira:
o filtro `walletId: {$nin: [None, ""]}` está embutido em `_mismatch_query`
(caminho escopado por `companyId`) e há uma guarda defensiva em `/rows`. Sem
isso, `str(None)` virava o nome de carteira literal `"None"` e o pacote
aparecia como uma linha-fantasma duplicando os dados de uma carteira real
(também inflava a contagem em `/api/conciliacao/dates`).

---

## Step 1 — Detectar

```
gap = returnNavPerShare − returnContribution
```

- `|gap| ≤ limiar` → sem problema. Fim.
- `|gap| > limiar` → seguir para Step 2.

### Limiar editável (DIFERENÇA)

O limiar que decide quais carteiras aparecem como divergência na listagem
é **editável pelo usuário** via input `Limiar |Δ| (%)` no topo da tela
Conciliação. O valor é persistido em `data/conciliacao_config.json` e vale
para todos os usuários da instância.

- Unidade: **percent** (ex.: `0.01` armazenado = `0.01%` na UI = `0.0001`
  decimal na query Mongo). O campo é chamado `diffThresholdPct` porque
  **o valor armazenado já está em porcentagem** — a função `_diff_threshold_decimal`
  divide por 100 antes de aplicar em Mongo.
- Default: `0.01%` (compatível com o destaque visual histórico, = 1 basis point).
- `0` recupera o comportamento anterior (qualquer `returnNavPerShare ≠ returnContribution`).
- Intervalo aceito: `[0, 10]` (valores acima de 10% não fazem sentido — retornos
  ficam em `-1..+1`).

> **Não confundir com `bayesian_config.tolerance`** (ver `CONCILIACAO_BAYESIAN.md`).
> São grandezas diferentes:
> - `diffThresholdPct` — limiar **absoluto** na diferença de retorno (`|ΔR|`),
>   em percent points. Filtra o que aparece na listagem da Conciliação.
> - `tolerance` — tolerância **relativa** ao `gapCash` (`|residual| ≤ |gap| × tolerance`),
>   em decimal. Usada apenas pelo otimizador Bayesiano para marcar um gap como
>   "resolvido". Um navPackage pode estar vermelho na lista (`|ΔR|` acima do limiar)
>   e ao mesmo tempo ser marcado como resolvido pelo Bayesiano (residual dentro da
>   tolerância relativa) — isso é esperado, as duas métricas medem coisas distintas.

A query Mongo aplica:
```
|returnNavPerShare − returnContribution|  >  diffThresholdPct / 100
```

Endpoints:
- `GET  /api/conciliacao/config` → `{ diffThresholdPct }`
- `PUT  /api/conciliacao/config` body `{ diffThresholdPct: <number 0..10> }`

Ao alterar o valor, a UI re-consulta `/api/conciliacao/dates` e `/rows`
para refletir o novo filtro imediatamente. O mesmo limiar também é aplicado
em `/api/conciliacao/global-analysis` (a carteiras selecionadas pela
análise global herdam o filtro).

---

## Step 2 — Eliminar

Uma carteira é composta por `cashAccounts`, `provisions` e `securities`. Securities são a principal fonte de divergências. Antes de investigar, eliminamos os que **com certeza** não são a causa.

Um security é eliminado quando **todas** as condições abaixo são verdadeiras:

| # | Condição | Significado |
|---|----------|-------------|
| a | `quantity == formerQuantity` | Sem variação de quantidade |
| b | Nenhuma transação associada na data | Sem movimentação financeira |
| c | Nenhuma provisão sendo criada ou liquidada na data | Sem evento de provisão |
| d | `rentabPU == rentabContribution` | Retornos iguais (`rentabPU = PU / formerPU − 1`, `rentabContribution = totalContribution / formerBalance`) |
| d′ | Se `d` falha: `Σbalance(coupon + amortization) ≈ expectedEventCash` | Evento explica a diferença → tratar como `d = true` |

Se **qualquer** condição falhar → security é suspeito e vai para Step 3.

---

## Step 3 — Diagnosticar Securities

Para cada security suspeito, investigar por tipo de causa.

> **Nota:** O output de `step3.securities` inclui **apenas** securities que possuem pelo menos um sub-step com resultado (step3_1, step3_2 ou step3_3 não-nulo). Securities que falharam na eliminação do Step 2 mas não têm nenhum flag diagnóstico no Step 3 são excluídos do array. Isso garante que a otimização bayesiana só recebe flags acionáveis.

### 3.1 — Amount Difference

Aplica quando `quantity ≠ formerQuantity`. Pode haver problema na **transação**, na **provisão**, ou em ambas.

Calcular offset a partir da coleção `securities`:

```
subscription (amountDiff > 0):  offset = subscriptionSettlementDays − subscriptionNavDays
redemption   (amountDiff < 0):  offset = redemptionSettlementDays − redemptionNavDays
```

| Offset | Significado | Esperado | Problema se ausente | Flag |
|--------|-------------|----------|---------------------|------|
| `== 0` | Liquidação imediata | Transação com o `securityId` | Transação faltando | `MISSING_TRANSACTION` |
| `> 0` | Liquidação futura (settlement após nav) | Provisão ativa | Provisão faltando | `MISSING_PROVISION` |
| `< 0` | Nav futuro (nav após settlement) | Provisão ativa | Provisão faltando | `MISSING_PROVISION` |

> **Datas da provisão gerada (`provisionData`):** a janela é ancorada na **data
> do navPackage em análise** (`date`), não na data de "hoje", e
> `liquidationDate = date + offset`:
> - `offset > 0` → `initialDate = date`, `liquidationDate = date + offset`
> - `offset < 0` → `initialDate = date + offset`, `liquidationDate = date`
> - `offset == 0` → ambas iguais a `date`
>
> **Mínimo de 1 dia útil:** toda provisão liquida no mínimo 1 dia útil após a
> data do nav — `liquidationDate = max(computado, próximo dia útil após date)`.
> Isso protege os casos `offset ≤ 0` (offset 0, `WRONG_PROVISION_AMOUNT`,
> "+ Provisão" manual) que produziriam liquidação no mesmo dia (ou passado),
> inválida para uma provisão prospectiva. O "dia útil" é Seg–Sex (sem calendário
> de feriados, igual ao resto do código — `get_biz_dates`). O mesmo piso é
> aplicado na provisão gerada pelo refinamento `OFFSET_OR_SETTLEMENT_DRIFT`.
>
> A regra vive em `_prov_dates` / `_next_biz_day` ([pages/conciliacao.py](../pages/conciliacao.py))
> e é a **fonte única** usada tanto no payload `step3_1.provisionData` quanto na
> geração final (`generate_provisions`) — incluindo o override manual
> "+ Provisão" e `WRONG_PROVISION_AMOUNT`. O `offset` viaja no item (top-level e
> dentro de `provisionData`).

### 3.2 — Diferença de Rentabilidade

Aplica quando `rentabPU ≠ rentabContribution`. Indica provável evento (amortização, cupom).

Converter a diferença para valor monetário:

```
expectedEventCash = (rentabContribution − rentabPU) × formerBalance
```

Buscar transações de evento (`amortization`, `coupon`) para o security na data:

| Situação | Resultado | Flag |
|----------|-----------|------|
| Transação existe e `Σbalance ≈ expectedEventCash` | Explicado → security **eliminado** | — |
| Transação existe mas `Σbalance ≠ expectedEventCash` | Valor da transação errado | `WRONG_EVENT_BALANCE` |
| Sem transação → provisão existe e `amount ≈ expectedEventCash` | Explicado → security **eliminado** | — |
| Sem transação → provisão existe mas `amount ≠ expectedEventCash` | Valor da provisão errado | `WRONG_PROVISION_AMOUNT` |
| Sem transação e sem provisão | Transação/provisão ausente | `MISSING_EVENT` |

> **Nota:** `dividend` **não** está em `_EVENT_TYPES` — apenas `amortization` e `coupon` são detectados como transações de evento. Para dividendos, a diferença de rentab é tratada via provisão: na data do anúncio, deve existir uma provisão; na data do pagamento, a provisão é liquidada. A transação de dividendo em si não é usada no matching de Step 3.2.

### 3.3 — Withholding Tax ou Execution Price

Aplica quando há `amountDifference`. Valida o valor financeiro da transação.

```
expectedValue = amountDifference × executionPrice
```

> **Fallback quando `executionPrice` é `null`:** o campo NÃO é auto-preenchido
> no documento `processedPosition` (a partir de 2026-04-10 o motor upstream
> parou de copiar `PU → executionPrice` para securities sem preço real
> informado). Fallbacks:
>
> - **Backend (cálculos):** `price = executionPrice or pu or 0` — veja
>   [pages/conciliacao.py](../pages/conciliacao.py) em `_diagnose_wallet`,
>   refinamento e simulação. Todas as fórmulas de impacto usam esse fallback
>   quando `executionPrice` é null.
> - **UI (exibição):** a coluna *"Preço Exec."* renderiza `PU` em cinza
>   itálico com tooltip *"Fallback PU — executionPrice ausente"*. A coluna
>   *Δ Saldo* também usa `executionPrice ?? PU` no multiplicador (veja
>   `fmtExecPrice` em [templates/conciliacao.html](../templates/conciliacao.html)).
>
> **Detecção independente de fallback explícito:** o ramo aproximado dispara
> `MISSING_EXECUTION_PRICE` sempre que o **preço implícito** (= −Σbalance ÷
> ΔQtd) diverge do **preço efetivamente usado** (`price = executionPrice or
> PU`) em mais de 0,5%. Cobre tanto o caso histórico `executionPrice == PU`
> (auto-fill) quanto o caso de liquidação total (`PU = 0`, `executionPrice`
> herdado do dia anterior) sem precisar de tratamento explícito.

A tolerância `_approx_txn` é 10% (mais larga que as demais). Prioridade dos
flags dentro da sub-etapa (de cima pra baixo — primeira condição vencedora
ganha):

| # | Situação | Resultado | Flag |
|---|----------|-----------|------|
| 1 | `securityType == "brazilianFund"` **e** `amountDifference < 0` **e** `Σbalance < expectedValue` | Provável **IR retido na fonte** — checada antes de `MISSING_EXECUTION_PRICE` mesmo quando a diferença está dentro de 10% (caso contrário o desvio "preço efetivo − preço usado" seria mal interpretado como fallback de preço) | `WITHHOLDING_TAX` |
| 2 | `expectedValue ≈ Σbalance(buySell)` (10%) **e** `price > 0` **e** `\|implied − price\| > 0,5% × price` (onde `price = executionPrice or PU`) | Preço implícito diverge do usado — `expectedExecPrice = -Σbalance ÷ ΔQtd` | `MISSING_EXECUTION_PRICE` |
| 3 | `expectedValue ≈ Σbalance` sem as condições acima | Transação correta | — |
| 4 | `expectedValue ≠ Σbalance` (> 10%) **e** `securityType == "brazilianFund"` **e** `amountDifference < 0` | IR retido maior que a tolerância | `WITHHOLDING_TAX` |
| 5 | `expectedValue ≠ Σbalance` **e** (`executionPrice == null` **ou** `executionPrice == PU`) | Execution price ausente | `MISSING_EXECUTION_PRICE` |
| 6 | `expectedValue ≠ Σbalance` nos demais casos | Erro no valor da transação | `WRONG_TRANSACTION_VALUE` |

> **Aceitar `WITHHOLDING_TAX`:** o flag gera uma transação de correção com
> `beehusTransactionType = "taxes"` e `balance = -|impact|` (IR retido é saída
> de caixa; o `impact` é sempre positivo — `abs(expectedValue − actualBalance)` —
> e é multiplicado por −1 na geração). A transação carrega o `securityId` do
> ativo. Ver [FILE_GENERATION.md](FILE_GENERATION.md#uso-por-página) para o
> mapeamento completo flag → `beehusTransactionType`.

> **Apresentação no modal de Diagnóstico — `MISSING_EXECUTION_PRICE`:**
> - Cada flag exibe um **rótulo em português** (`Preço de execução divergente`)
>   ao lado do título do issue. O nome técnico (`MISSING_EXECUTION_PRICE`) está
>   no `title=` do badge para inspeção em hover.
> - Quando o issue for expandido, um **callout roxo** mostra lado a lado o
>   *preço usado pelo sistema* e o *preço sugerido* (= `−Σbalance ÷ ΔQtd`),
>   com `Δ %` e `Impacto R$`. Quando o impacto bate exatamente com o `gapCash`
>   (`|impact|/|gapCash| ∈ [0,99 ; 1,01]`), o callout exibe a tag **CASA COM O GAP**
>   (vermelho) — sinal de alta confiança de que esta é a causa do gap.
> - A linha do pipeline (verificação 3.3) mostra o mesmo resumo
>   ("Aplicar X · sistema usou Y · Δ Z%"), sem precisar abrir o callout.
>
> **Aceitar `MISSING_EXECUTION_PRICE`:** o flag **não** é convertido em transação ou provisão.
> O botão **Aceitar** cria uma linha no bucket `executionPrices` em
> `data/correcoes/<companyId>/<date>/<walletId>.json` com o `expectedExecPrice` calculado.
> Na página `/correcoes`, a aba **Preços de Execução** oferece um botão por linha que chama
> `POST /api/correcoes/execution-prices/submit`, que por sua vez delega a
> `beehus_api.create_execution_price(...)` (`POST /beehus/financial/execution-prices` —
> mesma rota usada por *Funções > Adicionar Preço de Execução*). Após o sucesso, a linha
> é marcada `inputed=true` e deixa de ser considerada por qualquer recálculo local de gap
> (a correção já está no upstream). Detalhes do fluxo: [CORRECOES.md](CORRECOES.md#fluxo-missing_execution_price--aceitar-e-enviar-via-api).

### 3.4 — Transação existe, mas quantidade e rentabilidade não mudaram

Aplica quando o security caiu como suspeito **apenas** porque possui transações na data (`amountDiff == 0` e `diffRent == 0 | null`). Nessa situação, os sub-steps 3.1/3.2/3.3 não disparam (não há variação de quantidade nem de PU), mas pode existir uma transação "invisível" ao cálculo de contribuição.

Regra de detecção:

```
Σbalance(transações do security na data) ≈ gapCash   (em módulo)
```

Quando o somatório das transações do security **coincide com o gap**, é altíssima a probabilidade de que a(s) transação(ões) esteja(m) com um `beehusTransactionType` que **não** é contabilizado em `eventContribution` — por exemplo, `dividend`, `rebate` ou `otherFee`. Como o sistema só considera `coupon` e `amortization` como eventos, qualquer outro tipo que cause entrada/saída de caixa sem variação de PU produz exatamente esse padrão: o NAV enxerga o dinheiro, mas a contribuição não.

| Situação | Resultado | Flag |
|----------|-----------|------|
| `Σbalance ≈ gapCash` e `amountDiff == 0` e `diffRent ∈ {0, null}` | Provável erro de classificação do tipo da transação | `MISCLASSIFIED_EVENT_TYPE` |

**Payload do flag:**

```json
"step3_4": {
  "status": "flag",
  "flag":   "MISCLASSIFIED_EVENT_TYPE",
  "impact": 5696.21,
  "transactionTotal": 5696.21,
  "transactionTypes": ["dividend"],
  "transactionCount": 1,
  "detail": "Transações neste security somam R$ 5.696,21, valor idêntico ao gap. Tipo(s) presente(s): dividend. Provável erro de classificação — o tipo atual não é reconhecido como evento (coupon/amortization) e por isso não impacta eventContribution."
}
```

**Geração de arquivos:** este flag é **apenas diagnóstico** — não aparece no fluxo de **Aceitar** nem é incluído na otimização bayesiana. A correção requer revisão manual do `beehusTransactionType` da transação em questão (o tipo alvo depende do instrumento: `coupon`, `amortization`, ou outro que de fato impacte `eventContribution`).

---

## Step 4 — Diagnosticar Transações

Independente dos securities, analisar se alguma **transação** é elegível como causa do gap.

### 4.1 — Transação não identificada

Se `beehusTransactionType == null` → a transação não foi contabilizada. Problema identificado.

### 4.2 — Identificação incorreta de security

Se `transaction.securityId` não é nulo mas não existe entre os securities do `processedPosition`, a transação **pode** estar com o security errado.

Porém, há situações legítimas:

| Situação | Condição para NÃO ser erro |
|----------|---------------------------|
| **Compra de security novo** | `subscriptionNavDays > 0` — security ainda não entrou na posição. Confirmado se existe provisão para o securityId da transação. |
| **Venda com settlement futuro** | Security ainda não saiu da posição (settlement após NAV date, offset > 0). Provisão deve existir compensando o valor a receber. Confirmado se existe provisão ativa para o securityId da transação. |

Se nenhuma das condições acima se aplica (sem provisão correspondente) → provável erro de identificação do security na transação.

### 4.3 — Provável transação mal classificada

Cruzamento entre os valores faltantes identificados no Step 3 (`impact`) e as transações da data. Se um security tem um valor faltante (flag com `impact`) e existe uma transação com `|balance|` igual a esse valor que **não** está associada ao security, há alta probabilidade de que a transação esteja mal classificada (deveria pertencer àquele security).

---

## Step 5 — Validar Caixa

O caixa projetado deve ser igual ao caixa real. Caso contrário, há uma transação ausente ou com valor errado que impacta o NAV.

### Cálculo

```
projectedCash = formerCash + Σbalance(todas as transações da data)
cashDiff      = projectedCash − currentCash
```

- `formerCash` = soma dos `cashAccounts.values` na data anterior
- `currentCash` = soma dos `cashAccounts.values` na data atual
- Transações incluem todas as da carteira na data (`liquidationDate == date`)

### Diagnóstico

Avaliado na ordem abaixo (primeira regra que bate vence):

| Situação | `diagnosis` | Resultado |
|----------|-------------|-----------|
| `cashDiff ≈ 0` | `consistent` | Caixa consistente |
| `cashDiff ≠ 0` e existem transações com `beehusTransactionType == null` | `unclassified_txns` | Transação não identificada impactando o caixa |
| `cashDiff ≠ 0` e não há transações na data | `missing_cash_txn` | Transação de caixa ausente (provável `gainsExpenses`, `rebate` ou `otherFee`) |
| `cashDiff ≠ 0` e `suspectTxns` não vazio (ver 5.1) | `likely_wrong_txn` | Transação suspeita: `balance` idêntico ao `cashDiff` |
| `cashDiff ≠ 0` nos demais casos | `value_error` | Valor de transação incorreto ou transação faltando |

> Transações de tipo `gainsExpenses`, `rebate` e `otherFee` afetam o caixa e o NAV mas **não** entram em `inAndOutFlows` nem no `totalContribution` — são uma causa frequente de gap.

### 5.1 — Transação suspeita de match próximo

Quando `cashDiff ≠ 0`, verificar se existe alguma transação na data cujo
`balance` seja aproximadamente igual ao `cashDiff` (mesmo sinal). Se
existir, é altíssima a probabilidade de que essa transação esteja
**errada** — pode estar na carteira errada, na data errada, ser uma
duplicata, ou simplesmente não deveria existir. Remover essa transação
do cálculo zeraria o `cashDiff`.

Regra de detecção — tolerância específica deste step (mais estreita que
o `_approx` genérico, para evitar falso-positivo em carteiras com fluxo
alto):

```
tol = max(0,01;  0,001 × |cashDiff|)       # 0,01 BRL OU 0,1% relativo
∃ t ∈ txns_da_data : |balance(t) − cashDiff| ≤ tol
```

Resultado é ordenado por `|balance − cashDiff|` ascendente, então o
candidato mais próximo aparece primeiro.

**Payload (`step5.suspectTxns`):** lista de candidatos; cada item:

```json
{
  "txnId":       "663abc...",       // null para correções pendentes
  "balance":     5696.21,
  "type":        "dividend",        // pode ser null
  "securityId":  "662xyz...",       // pode ser null
  "securityName": "XPTO11",         // resolvido via sec_info (pode ser null)
  "pending":     false              // true se veio de corrections/ pending
}
```

Não é um veredito definitivo — é um candidato para revisão manual.
Múltiplos candidatos podem aparecer (todos com mesmo `balance`); nesse caso,
**um deles** é provavelmente o erro. A correção depende de investigação
(confirmar se a txn pertence a esta carteira, a esta data, se é duplicata,
etc.).

Observações:

- Prioridade maior que `value_error`, menor que `unclassified_txns` e
  `missing_cash_txn`. Se houver transação `null-type` e também um candidato
  com `balance == cashDiff`, o diagnóstico continua `unclassified_txns`
  (raiz mais provável); `suspectTxns` ainda é preenchido para inspeção.
- Não muda o veredito do Step 7 (`CASH_ISSUES` continua disparando quando
  `diagnosis != "consistent"`).
- Não alimenta a otimização bayesiana — é apenas informativo na UI.

---

## Step 6 — Anomalias de Rentabilidade

Validações estatísticas para identificar rentabilidades fora do esperado. Não explicam o gap diretamente, mas sinalizam dados potencialmente incorretos.

### 6.1 — Rentabilidade da carteira (`returnNavPerShare`)

Comparar `returnNavPerShare` da data atual contra o histórico da própria carteira.

```
threshold = média(returnNavPerShare histórico) ± 3 × desvio_padrão
```

Se `returnNavPerShare` da data está fora do threshold → flag de anomalia.

### 6.2 — Rentabilidade por security

Comparar `rentabPU` (`PU / formerPU − 1`) de cada security contra seu histórico.

> **Escopo:** apenas securities suspeitos (que falharam na eliminação do Step 2) são verificados. Securities eliminados não passam por esta validação.

> **Nota:** os thresholds por security ficam em `rentability_thresholds.json`, lido por esta etapa. **A página Validação Rentabilidades — única produtora desse arquivo — foi removida**, então atualmente não há gerador de thresholds: sem o arquivo, esta etapa degrada graciosamente (guard `if thresholds:`) e não sinaliza anomalias de rentabilidade por security. Para reativar, é preciso reintroduzir um produtor (batch/pré-cálculo) que recompute os thresholds 3-sigma.

---

## Step 7 — Causa Provável

Síntese dos Steps 1-6 em um **único veredito** que indica a causa mais provável do gap. Executado após todos os outros steps e exposto em `step7` no payload de `/api/conciliacao/diagnose`.

### Matriz de veredito

Avaliada na seguinte ordem (primeira regra que bate vence):

| # | Condição | Veredito | Significado |
|---|----------|----------|-------------|
| 0 | `step1.status == "ok"` | `NO_GAP` | Sem divergência. Nenhuma ação necessária. |
| 1 | Algum sub-step do Step 3 com `status == "flag"` | `SECURITY_ISSUES` | A causa está em securities específicos. Ver Step 3. |
| 2 | Step 4 com `unclassified`, `WRONG_SECURITY` ou `misclassified` | `TRANSACTION_ISSUES` | A causa está em transações. Ver Step 4. |
| 3 | `step5.diagnosis != "consistent"` | `CASH_ISSUES` | O caixa projetado diverge do caixa real. Ver Step 5. |
| 4 | Gap existe mas Steps 3, 4 e 5 estão todos limpos | `LIKELY_WRONG_FORMER_NAV` | Causa provável: NAV anterior incorreto. |
| 5 | Caso contrário | `INCONCLUSIVE` | Fluxo diagnóstico não conseguiu localizar a causa. |

> **Regra "Step 3 limpo":** apenas `status == "flag"` desqualifica. Sub-steps com `status == "ok"`, `status == "eliminated"` ou ausentes (`null`) são considerados limpos.

### `LIKELY_WRONG_FORMER_NAV` — detalhe

Quando securities (Step 3), transações (Step 4) e caixa (Step 5) estão todos consistentes mas o gap persiste (Step 1), a equação do gap aponta para `formerNav` como a variável fora de lugar:

```
returnNavPerShare  = nav / formerNav − 1
returnContribution = Σ(security contributions) / formerNav
gap                = returnNavPerShare − returnContribution
```

Se os saldos atuais (NAV) e os movimentos intradia batem com as contribuições, o que resta é o denominador: o `formerNav` (ou a posição do dia anterior que o gerou) está incorreto.

### Payload

```json
"step7": {
  "status": "warning",
  "verdict": "LIKELY_WRONG_FORMER_NAV",
  "detail": "Securities, transações e caixa estão consistentes, mas o gap persiste. Causa mais provável: o NAV anterior (R$ 12.878.186,52 em 2026-04-08) está incorreto. Verifique a posição e o navPackage do dia anterior.",
  "formerNav": 12878186.52,
  "formerDate": "2026-04-08",
  "gapCash": 6772603.17,
  "gapPct": 0.5259,
  "signals": {
    "step3HasFlags": false,
    "step4HasIssues": false,
    "step5Consistent": true,
    "step6WalletAnomaly": false,
    "allSuspectsMissingContribution": true
  }
}
```

### Sinais de reforço

Quando `verdict == "LIKELY_WRONG_FORMER_NAV"`, os sinais abaixo **não mudam o veredito** mas aumentam a confiança quando presentes:

| Sinal | Significado quando `true` |
|-------|--------------------------|
| `allSuspectsMissingContribution` | Todos os suspeitos têm `totalContribution == null` → o sistema não conseguiu calcular a contribuição individual, reforçando que o snapshot anterior está corrompido. |
| `step6WalletAnomaly` | `returnNavPerShare` está fora do intervalo 3σ histórico → rentabilidade da carteira é atípica, consistente com NAV anterior incorreto. |

---

## Análise Global por Security

Endpoint: `GET /api/conciliacao/global-analysis?companyId=...&date=...`

Executa Steps 1-3 de forma otimizada em **todas** as carteiras com divergência de uma empresa/data, e agrupa os flags determinísticos (Tier 1) por security.

### Flags coletados (Tier 1)

| Flag | Descrição |
|------|-----------|
| `MISSING_TRANSACTION` | Transação buySell ausente (liquidação imediata) |
| `MISSING_PROVISION` | Provisão ausente (liquidação futura / nav futuro) |
| `MISSING_EVENT` | Transação de evento e provisão ausentes |
| `WRONG_EVENT_BALANCE` | Valor da transação de evento diverge do esperado |
| `WRONG_PROVISION_AMOUNT` | Valor da provisão diverge do esperado |

### Output

Cada entrada representa uma combinação `(securityId, flag)`:

| Campo | Descrição |
|-------|-----------|
| `securityId` | ID do security |
| `securityName` | Nome do security |
| `securityType` | Tipo (brazilianFund, stockEtf, etc.) |
| `flag` | Tipo do flag (Tier 1) |
| `walletCount` | Quantidade de carteiras afetadas |
| `totalImpact` | Soma dos impactos em todas as carteiras |
| `avgImpact` | Impacto médio por carteira |
| `wallets` | Lista de carteiras afetadas com impacto e gapCash individuais |

### Uso

Permite identificar **problemas sistêmicos** — por exemplo, um security com `MISSING_PROVISION` em 20 carteiras indica uma configuração incorreta de settlement days, não 20 problemas independentes.

---

## Verificação de Transações buySell

Endpoint: `GET /api/conciliacao/transaction-check?companyId=...&date=...`

Verifica todas as transações `buySell` de uma empresa/data e identifica aquelas que **não têm** uma correspondência na posição (amountDifference) ou provisão.

### Lógica

Para cada transação buySell:

1. Determinar direção:
   - `balance < 0` → **subscrição** → `offset = subscriptionSettlementDays − subscriptionNavDays`
   - `balance > 0` → **resgate** → `offset = redemptionSettlementDays − redemptionNavDays`

2. Verificar correspondência:
   - `offset = 0` → deve existir `amountDifference ≠ 0` na processedPosition para o mesmo security/wallet/date
   - `offset ≠ 0` → deve existir provisão ativa para o security/wallet na data

3. Se não houver correspondência → transação é reportada como "sem correspondência"

### Output

| Campo | Descrição |
|-------|-----------|
| `securityId` / `securityName` | Security da transação |
| `walletId` / `walletName` | Carteira |
| `balance` | Valor financeiro da transação |
| `quantity` | Quantidade transacionada |
| `price` | Preço de execução |
| `direction` | `subscription` ou `redemption` |
| `offset` | Diferença em dias (settlement − nav) |
| `operationDate` | Data de operação |
| `liquidationDate` | Data de liquidação |
| `problem` | Descrição do problema encontrado |

### Exemplos

- **Offset 0, sem amountDifference**: transação buySell de subscrição existe, mas a posição não mostra mudança de quantidade → possível erro de processamento
- **Offset > 0, sem provisão**: transação buySell com liquidação futura existe, mas nenhuma provisão foi criada → provisão faltando

---

## Endpoints auxiliares

Além do `/diagnose` principal, a UI do modal Diagnosticar consome dois grupos
de endpoints complementares. A descrição completa (incluindo geração de
provisão em `OFFSET_OR_SETTLEMENT_DRIFT` e regras de sinal) vive em
[CONCILIACAO_BAYESIAN.md](CONCILIACAO_BAYESIAN.md).

### Editar / excluir transação e provisão (direto na API)

Na tela de **detalhamento da carteira** (`step-2`), há botões **✎ Editar** e
**🗑 Excluir** (coluna *Ações*) que agem direto no sistema Beehus. Aparecem em
**três lugares**:

1. **Transações vinculadas** a cada ativo (sub-row expansível) e as órfãs
   "sem ativo";
2. **Bloco "Transações"** abaixo da listagem de ativos;
3. **Bloco "Provisões"** abaixo da listagem de ativos.

Disponíveis **apenas** para registros reais do banco (`txnId` / `provisionId`
presente). Linhas **pendentes** (`isPending`, vindas de `/correcoes`) e
transações já marcadas para exclusão (`pendingDeletion`) não têm os botões —
essas se editam na página Correções.

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/conciliacao/transaction/delete` | Body: `{walletId, txnId}`. `DELETE /beehus/financial/transactions/{id}` (`beehus_api.delete_transaction`). |
| POST | `/api/conciliacao/transaction/update` | Body: `{walletId, txnId, patch}`. `PATCH /beehus/financial/transactions/{id}` (`beehus_api.update_transaction`). |
| POST | `/api/conciliacao/provision/delete` | Body: `{walletId, provisionId}`. `DELETE /beehus/provisions/{id}` (`beehus_api.delete_provision`). |
| POST | `/api/conciliacao/provision/update` | Body: `{walletId, provisionId, patch}`. `PATCH /beehus/provisions/{id}` (`beehus_api.update_provision`). |
| POST | `/api/conciliacao/calculate-nav` | Body: `{walletId, date}`. Dispara o recálculo do NAV da carteira via `POST /beehus/consolidation/nav-contribution-calculation/wallets` (`beehus_api.calculate_nav_wallets`, `wallets=[walletId]`). Botão **Calcular NAV** no bloco de ações da carteira (`step-2`); ao concluir, recarrega o detalhamento. Timeout amplo (recálculo pode demorar). |

- **Segurança:** todos exigem carteira visível (`_require_visible_wallet`) e
  confirmam que o id pertence ao `walletId` (`_find_wallet_txn` /
  `_find_wallet_provision`, consulta `db.transactions` / `db.provisions` com
  `_id` em ObjectId **ou** string) — impossível editar/excluir registro de
  outra carteira por adivinhação de id. (Por isso `get_provisions` agora
  devolve `provisionId`.)
- **Campos editáveis transação (`_TXN_EDIT_FIELDS`):** `balance`,
  `beehusTransactionType`, `operationDate`, `liquidationDate`, `price`,
  `securityId`, `description`. (`quantity` não é patchável upstream;
  `walletId`/`entityId`/`currencyId` ficam para a rotina Exceções.)
- **Campos editáveis provisão (`_PROV_EDIT_FIELDS`):** `balance`,
  `provisionType`, `initialDate`, `liquidationDate`, `securityId`,
  `description`.
- `balance`/`price` são coagidos para float; campos em branco = "manter como
  está".
- **Erros:** `401` (token Beehus ausente/expirado), `502` (API upstream),
  `404` (registro não pertence à carteira), `400` (validação).
- Após sucesso, a UI re-busca o detalhamento (`openWalletDetail`, que recarrega
  a listagem de ativos **e** os blocos inline de transações/provisões).

### Refinamento ±N dias (offset / settlement drift)

Varredura em dias vizinhos para detectar transações/posições fora da data
esperada — útil quando `settlementDays/navDays` do cadastro do security estão
errados ou a instituição liquidou fora da data prevista.

| Método | Rota | Descrição |
|--------|------|-----------|
| GET  | `/api/conciliacao/diagnose/refine?walletId=&date=&window=N[&securityId=][&txnSecurityId=]` | Executa varredura. `window` default=2. Parâmetros opcionais `securityId` / `txnSecurityId` limitam a análise a um security específico (usado pelos botões Refinar inline no card). |
| POST | `/api/conciliacao/diagnose/refine/accept` | Confirma uma hipótese (`WRONG_SECURITY` ou `OFFSET_OR_SETTLEMENT_DRIFT`). Em `OFFSET_OR_SETTLEMENT_DRIFT` também gera uma provisão pendente em `/correcoes`. |
| GET  | `/api/conciliacao/diagnose/refine/accepted?walletId=&date=` | Lista triplas `(securityId, matchTxnId, hypothesis)` já confirmadas. UI usa para pré-marcar botões como ✓ Aceito. |

Persistência: `data/refinement_feedback/<companyId>/<date>/<walletId>.json`
(mesmo padrão filesystem das correções — o usuário Mongo não tem privilégio
para `createCollection`).

### Descarte de flags (dismiss)

Marca um flag detectado como não-acionável (ruído, já investigado, fora de
escopo). A UI esconde da lista ativa de problemas mas preserva o registro
para auditoria.

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/conciliacao/diagnose/dismiss` | Body: `{walletId, date, anomalyKey, flag?, securityId?, securityName?, reason?}`. Chave `anomalyKey` é a mesma usada pelo `/correcoes` (`<company>|<date>|<wallet>|<flag>|<securityId>`) — idempotente. |
| POST | `/api/conciliacao/diagnose/dismiss/undo` | Body: `{walletId, date, anomalyKey}`. Remove o registro e o flag volta a aparecer. |
| GET  | `/api/conciliacao/diagnose/dismissed?walletId=&date=` | Retorna todos os registros de descarte da carteira/data (inclui `reason`, `firstDismissedAt`, `updatedAt`). |

Persistência: `data/dismissed_flags/<companyId>/<date>/<walletId>.json`.
