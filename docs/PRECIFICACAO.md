# Preços na Curva

> Motor de precificação **na curva** de ativos (sobretudo renda fixa).
> Dado um PU-base e uma taxa/indexador, projeta o PU dia a dia ao longo
> do calendário de um benchmark. Implementação:
> `_calculate_curva_impl` em [`pages/precificacao.py`](../pages/precificacao.py).
>
> A função pura `calculate_curva(securities_list)` é o ponto de entrada
> reusado por **Repetir Posições** (`_compute_curva_pu_map`) e
> **Exceções** — qualquer mudança no motor reflete nas três telas.

---

## Tipos de cálculo (`calcType`)

| `calcType` | Modelo | PU-base | Acrual diário |
|---|---|---|---|
| `pos_fixado` | Pós-fixado (ex.: % do CDI) | `posPU`/`positionDate` da `processedPosition` da carteira → senão último `securityPrices.historyPrice` | `pu *= 1 + fator_bm × (indexerPercentual/100)` por data do benchmark |
| `pre_fixado_curva` | HTM pré-fixado por lotes | `posPU` → `initialPU` → último `historyPrice` | `pu *= daily_factor`, com `daily_factor = (1 + yield_ponderado/100)^(1/252)` |
| `inflacao_curva` | HTM indexado a inflação (ex.: IPCA+) | idem | `pu *= daily_factor × (1 + fator_inflação_do_dia)` |

`yield_ponderado` = média dos `yield` dos lotes ativos ponderada pela
`quantity`. O calendário (datas em que o PU avança) é o conjunto de
datas do benchmark configurado.

---

## Ajuste por cupom / amortização (degrau no PU)

> **Problema que resolve.** O acrual diário acima **só capitaliza** — o
> PU nunca devolve valor. Para um ativo de RF que paga **juros (cupom)**
> ou **amortização** periódica, isso superestima o PU entre pagamentos.
> Na data do pagamento o valor sai do título e o PU deve **cair**.

Na data de cada evento, o PU sofre um **degrau para baixo** igual ao
valor pago **por unidade**:

```
impacto_por_unidade = (Σ balance[coupon|amortization]
                       + Σ balance[taxes])           ÷  qtd_dia_anterior

PU_pós = PU_acruado_do_dia  −  impacto_por_unidade
```

### Fonte e casamento

- **Transações** `db.transactions` com `walletId` + `securityId` do ativo
  precificado, `trashed != true`, e
  `beehusTransactionType ∈ {coupon, amortization}`.
  `walletId`/`securityId` são casados como **ObjectId e string** (mesmas
  duas vias usadas no resto de `precificacao.py`).
  **Todas** as transações lançadas para a data são somadas — **não há
  deduplicação** (decisão da operação). Se o upstream tiver lançamentos
  duplicados, o degrau os reflete; a higiene fica na origem do dado
  (mesma convenção do `coupon_amort_by_sid` do Repetir e do termo
  `event` da contribuição, que também somam sem deduplicar).
- **Data do degrau** = `liquidationDate` da transação. Quando essa data
  não existe no calendário do benchmark (ex.: liquidação em data sem
  fator publicado), o degrau é aplicado no **primeiro dia de calendário
  ≥ `liquidationDate`** (não se perde o evento).
- **`taxes` (IR retido):** se existir transação `taxes` com **mesmo
  `walletId`, `securityId` e `liquidationDate`**, seu `balance` (que já é
  **negativo**) entra **somando** — reduzindo o degrau para o valor
  **líquido**. Sem `taxes` casada na data, considera-se só o
  cupom/amortização. Transação `taxes` numa data **sem** cupom/amort não
  gera degrau.

### Quantidade (divisor)

`qtd_dia_anterior` = `quantity` do ativo na `processedPosition` mais
recente da carteira com **`positionDate < liquidationDate`** ("dia
anterior"). Essas transações **só reduzem o PU, nunca a quantidade**, de
modo que `balance ÷ qtd_dia_anterior` recupera exatamente o valor pago
por unidade. `qtd` ausente ou `0` → o degrau é **pulado** (sem divisão
por zero).

### Convenção de marcação (caixa-neutro)

Como o degrau usa o valor **líquido de IR** (cupom bruto + `taxes`
negativo), no dia do evento a marcação é **caixa-neutra**: o PU cai pelo
líquido e o caixa da carteira sobe pelo líquido — o NAV não se altera
pelo evento em si. (Não se modela o IR como perda de NAV à parte.)

### Roll incremental (por que é necessário)

O degrau **só funciona com acrual incremental** (`pu[t] = pu[t-1] × …`),
não com a forma fechada `pu = base × fator^n`. Com o roll incremental o
degrau se propaga: todos os dias **posteriores** ao evento partem do PU
já reduzido — e, no caso de **amortização**, o acrual seguinte passa a
incidir sobre a base menor (comportamento correto). A conversão para
incremental é **matematicamente idêntica** à forma fechada na ausência
de eventos, então não altera nenhum PU já calculado hoje.

### Idempotência vs. PU-base

Eventos com `liquidationDate ≤` data da **PU-base** (`last_pu_date`) são
**ignorados** — a PU-base já os incorpora. Só entram eventos posteriores
à base e dentro do horizonte do calendário.

### Saída

Nas datas com degrau, o resultado da curva carrega o campo
`eventImpact` (delta **negativo** aplicado ao PU, por unidade). A UI de
**Preços na curva** destaca a célula "PU Calculado" desses dias com um
marcador e o valor do abatimento; o export `.xlsx` traz a coluna
correspondente. Em datas sem evento o campo é ausente/`null`.

### Aplica-se a todos os `calcType`

O degrau roda nos três tipos (`pos_fixado`, `pre_fixado_curva`,
`inflacao_curva`), sempre **depois** do acrual do dia. Requer
`walletId` na entrada (sem carteira não há como casar a transação) —
entradas `pos_fixado` sem `walletId` simplesmente não recebem degrau.

---

## Arquivos relacionados

- [`pages/precificacao.py`](../pages/precificacao.py) — motor
  (`_calculate_curva_impl`, `calculate_curva`, helpers
  `_event_pu_impacts` / `_qty_before`).
- [`templates/precificacao.html`](../templates/precificacao.html) — UI.
- [`docs/REPETIR_POSICOES.md`](REPETIR_POSICOES.md) — consumidor via
  `RULE_CURVA_PRICE` (lookup estrito `walletId|securityId|date`).
