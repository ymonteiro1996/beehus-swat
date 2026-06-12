# Recálculo de GAP — Simulação de Correções

> Dado um diagnóstico com correções propostas (transações, provisões, preços), recalcular a posição, o NAV e o novo GAP para validar se as correções resolveriam a divergência.

---

## Objetivo

Antes de aplicar correções no sistema, simular o impacto recalculando:

1. **Posição** — saldos dos securities após as correções
2. **Caixa** — saldo de caixa após as correções
3. **NAV** — novo NAV projetado
4. **Contribuições** — daily, intraday, event por security + wallet-level
5. **Rentabilidades** — navPerShare, contribution, por security
6. **GAP** — novo `returnNavPerShare` vs `returnContribution`

---

## Dados de entrada

- `walletId` e `positionDate`
- Dados existentes: `processedPosition` (current + former), `navPackage` (current + former), `transactions`, `provisions`, `cashAccounts`, `securityEvents`
- Correções propostas: novas transações, provisões, ou alterações de preço geradas pelo diagnóstico

---

## Fórmulas

### 1. NAV

```
nav = Σ(securities.balance) + Σ(provisions.amount) + Σ(cashAccounts.values)
```

Onde `security.balance = quantity × PU` para cada security na posição.

### 2. Cota (navPerShare)

```
inAndOutFlows = Σbalance(transactions where type in {withdrawalDeposit, securityTransfer, taxes, bzFundTax})

navPerShare = (nav − inAndOutFlows) / formerAmount
```

> Na primeira data (sem posição anterior), `navPerShare = 10`. Isso não ocorre em contexto de reconciliação, pois não há diagnóstico na primeira data.

`formerAmount` = amount of shares da data anterior (do `navPackage` anterior).

### 3. Quantidade de cotas (amount)

```
amount = nav / navPerShare
```

---

### 4. Contribuição por security — dailyContribution

```
dailyContribution = formerQuantity × (PU − formerPU)
```

### 5. Contribuição por security — intradayContribution

```
intradayContribution = (quantity − formerQuantity) × (PU − executionPrice)
```

> Se o usuário não informou `executionPrice`, o sistema assume `executionPrice = PU`, resultando em `intradayContribution = 0`.

### 6. Contribuição por security — eventContribution

```
couponAmortEvents = Σbalance(transactions where type in {coupon, amortization} and securityId = sid)
dividendEvents    = Σ(amount from securityEvents where securityId = sid and operationDate = positionDate and type = dividend)

eventContribution = (couponAmortEvents / formerQuantity + dividendEvents) × formerQuantity
```

Simplificando:

```
eventContribution = couponAmortEvents + dividendEvents × formerQuantity
```

### 7. Contribuição total do security

```
security.totalContribution = dailyContribution + intradayContribution + eventContribution
```

---

### 8. Contribuição da carteira (wallet-level)

```
walletContribution = Σbalance(transactions where type in {gainsExpenses, rebate, contributionAdjustment, other} and liquidationDate = positionDate)
```

> Estas transações afetam o NAV/caixa mas não estão associadas a nenhum security específico.

---

### 9. Rentabilidade navPerShare

```
returnNavPerShare = navPerShare / formerNavPerShare − 1
```

### 10. Rentabilidade contribution

```
totalContribution = walletContribution + Σ(security.totalContribution)

returnContribution = totalContribution / formerNav
```

---

### 11. Rentabilidade PU (por security)

```
rentabPU = PU / formerPU − 1
```

### 12. Rentabilidade contribution (por security)

```
formerBalance = formerQuantity × formerPU

rentabContribution = security.totalContribution / formerBalance
```

---

### 13. Caixa projetado

```
projectedCash = formerCash + Σbalance(all transactions where liquidationDate = positionDate)
```

---

### GAP

```
gapPct  = returnNavPerShare − returnContribution
gapCash = gapPct × formerNav
```

---

## Fluxo de simulação

1. Carregar dados atuais (posição, navPackage, transações, provisões, caixa, securityEvents)
2. Aplicar correções propostas (adicionar/modificar transações, provisões)
3. Recalcular fórmulas 1–13 na ordem
4. Validar:

| Validação | Condição de sucesso |
|-----------|---------------------|
| **GAP resolvido** | `gapPct ≈ 0` (dentro de tolerância) |
| **Caixa consistente** | `projectedCash == currentCash` (caixa real da data) |

Se ambas condições são verdadeiras → correções propostas são suficientes.
Se GAP persiste → faltam correções ou há outro problema não identificado.
Se caixa diverge → há transação ausente ou com valor incorreto impactando o caixa.

---

## Recálculo após Aceitar (`_recalc_gap_with_corrections`)

A simulação completa acima é executada apenas pelo motor de simulação. O recálculo de gap exibido em tempo real no modal de Diagnóstico (pill "Gap recalc.") usa um modelo mais leve em [pages/conciliacao.py](../pages/conciliacao.py#L686) que mistura:

| Tipo de correção  | Estratégia                                                                                                       |
|-------------------|------------------------------------------------------------------------------------------------------------------|
| **Provisões**     | Recálculo NAV completo (Fórmulas 1–2) substituindo `nav_D` e `nav_former` pelos novos valores ativos             |
| **Transações**    | Heurística "fecha o gap": cada `\|balance\|` é subtraído da magnitude do gap (`recalc ± Σ\|balance\|`)             |
| **Deletions**     | Contado em `correctionsCount`, mas o efeito vem das transações de reposição                                      |
| **Preços de execução** | Mesma heurística "fecha o gap", com `\|impact\|` derivado da Fórmula 5 (intradayContribution)               |

### Fórmula do impacto de Preços de Execução

Aplicar um novo `executionPrice` muda a Fórmula 5 (intradayContribution) — o sistema usava o preço errado (PU como fallback ou `executionPrice` herdado), e o usuário aceitou um valor diferente:

```
delta_intraday_cash = amountDifference × (priceUsed − newExecutionPrice)

priceUsed = priorExecutionPrice (= row["priorExecutionPrice"])  if non-zero
            else PU              (= row["pu"])
```

O gap fecha por essa magnitude:

```
recalc_gap_cash = gap_cash − sign(gap_cash) × |delta_intraday_cash|
```

A validação faz uso do mesmo padrão `recalc ± Σ|impact|` de transações, então no caso canônico (impact = |gapCash|) o gap recalculado vai exatamente a 0.

### Filtro `inputed`

Apenas linhas com `inputed = false` são aplicadas. Quando o usuário envia o preço para o upstream (`POST /api/correcoes/execution-prices/submit` → marca `inputed = true`/`inputedAt`/`beehusId`), o motor de cota upstream já aplicou o preço — aplicar de novo localmente causaria contagem dupla. Detalhes em [CORRECOES.md](CORRECOES.md#fluxo-missing_execution_price--aceitar-e-enviar-via-api).

### Matching por data

O recálculo só aplica linhas cujo `positionDate` é exatamente a data sendo diagnosticada. Diferente das provisões (que têm janela ativa `[initialDate, liquidationDate)`), um preço de execução afeta uma única data — a posição em que o trade ocorreu. Isso evita que aceitar um MEP no dia X afete o gap recalculado dos dias subsequentes.
