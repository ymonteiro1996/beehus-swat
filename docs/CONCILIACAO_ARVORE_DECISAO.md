# Conciliação — Árvore de Decisão (criação de provisões)

> **Fonte de verdade:** `returnNavPerShare`. O objetivo é fazer `returnContribution`
> convergir, **criando os artefatos de correção certos** — provisão, transação,
> preço de execução e IRRF — e **verificando o caixa**, inclusive na **baixa da
> provisão**.

Este documento estrutura, como uma única árvore de decisão, a lógica que hoje
está espalhada no funil de 7 passos de [diagnostic_engine.py](../pages/diagnostic_engine.py)
e na geração de [pages/conciliacao.py](../pages/conciliacao.py). É o complemento
"orientado a ação" da [CONCILIACAO_DIAGNOSTICO.md](CONCILIACAO_DIAGNOSTICO.md)
(que descreve a detecção) e da [CONCILIACAO_RECALCULO.md](CONCILIACAO_RECALCULO.md)
(que descreve o recálculo do gap).

## Escopo

**Dentro:** dado um GAP, decidir e gerar o artefato de correção — com ênfase em
**provisões** — mais preço de execução, IRRF e a verificação do caixa projetado,
incluindo a **baixa da provisão** (camada nova).

**Fora (por enquanto):** a regra que **libera a carteira para publicação**. A
classificação de "bloqueante vs. diferível" será definida depois, em cima desta
árvore. Aqui apenas marcamos *quais saídas são diferidas* (cobertas por provisão,
dependem de informação futura) para que essa regra seja fácil de plugar.

---

## Princípio

O NAV vem de saldos reais (posições, caixa, provisões); a cota deriva do NAV.
Quando `returnContribution` diverge de `returnNavPerShare`, o erro está nos dados
que alimentam a contribuição — não no NAV. **A provisão é o instrumento que
"segura" um gap cuja liquidação ocorre no futuro:** ela entra no NAV hoje e é
baixada no `liquidationDate`, quando o caixa real deve se mover. Por isso a
provisão precisa ser criada com a **janela** e o **sinal** corretos, e precisa
ser **conferida na baixa**.

---

## A árvore

Notação: `◆` = nó de decisão · `▶` = ação (gera artefato) · `■` = sem ação
(eliminado/ok) · `⚠` = revisão manual.

```
RAIZ ── Achei um GAP?  gap = returnNavPerShare − returnContribution
│
├─ |gap| ≤ limiar ─────────────────────────────────────────────── ■ Sem gap. Fim.
│
└─ |gap| > limiar  →  para cada security SUSPEITO (Step 2: não eliminado)
   │
   ◆ N1 — Existe amountDifference?  (quantity ≠ formerQuantity)
   │
   ├─ SIM ─→ ◆ N1.1 — offset = settlementDays − navDays
   │        │         (resgate Δqty<0 usa redemption*; subscrição Δqty>0 usa subscription*)
   │        │
   │        ├─ offset == 0  (liquidação imediata)
   │        │     ◆ existe transação buySell no security?
   │        │        ├─ sim ─────────────────────────────────── ■ coberto pela transação
   │        │        └─ não ── FLAG MISSING_TRANSACTION ──────── ▶ criar TRANSAÇÃO buySell
   │        │                                                       impact = |Δqty| × preço
   │        │
   │        └─ offset ≠ 0  (liquidação futura | nav futuro)   ◀── ramo herói (provisão)
   │              ◆ existe PROVISÃO ATIVA?  (initialDate ≤ date < liquidationDate)
   │                 ├─ sim ──────────────────────────────────── ■ coberto pela provisão (DIFERIDO)
   │                 └─ não ── FLAG MISSING_PROVISION ─────────── ▶ CRIAR PROVISÃO
   │                                                                 janela = _prov_dates(date, offset)
   │                                                                 balance = sinal(offset, Δqty) × impact
   │                                                                 provisionType = buySell        (DIFERIDO)
   │        │
   │        └─ depois, COM Δqty, validar o VALOR financeiro ── ◆ N1.2 (IRRF / preço de execução)
   │              actual = Σbalance(buySell);  expected = −Δqty × preço
   │              ├─ fundo br  &  Δqty<0  &  actual < expected ── FLAG WITHHOLDING_TAX
   │              │                                               ▶ criar TRANSAÇÃO taxes  (balance = −|impacto|)
   │              ├─ execPrice ausente/=PU  &  |implícito−usado|>0,5% ── FLAG MISSING_EXECUTION_PRICE
   │              │                                               ▶ gravar PREÇO DE EXECUÇÃO  (não cria txn/provisão)
   │              │                                                 preço = −actual ÷ Δqty
   │              └─ valor diverge nos demais casos ───────────── ⚠ WRONG_TRANSACTION_VALUE
   │
   └─ NÃO ─→ ◆ N2 — Existe diferença de rentabilidade?  (rentabPU ≠ rentabContribution)
            │       expectedEventCash = (rentabContribution − rentabPU) × formerBalance
            │
            ├─ SIM ─→ ◆ tem transação de evento (coupon/amortization)?
            │           ├─ sim & bate ───────────────────────── ■ eliminado (evento explica)
            │           ├─ sim & diverge ── FLAG WRONG_EVENT_BALANCE ─ ⚠ corrigir valor da transação
            │           └─ não → ◆ tem provisão?
            │                     ├─ bate ─────────────────────── ■ eliminado (evento já provisionado)
            │                     ├─ diverge ── WRONG_PROVISION_AMOUNT ─ ▶ ATUALIZAR/RECRIAR PROVISÃO
            │                     └─ não ── FLAG MISSING_EVENT ── ▶ evento já ocorreu → TRANSAÇÃO coupon/amort
            │                                                       evento futuro/anunciado → PROVISÃO  ⟵ (ver Decisões em aberto)
            │
            └─ NÃO ─→ ◆ N3 — tem transação no security mas sem Δqty e sem Δrentab?
                        Σbalance(txns) ≈ gapCash?
                        └─ sim ── FLAG MISCLASSIFIED_EVENT_TYPE ── ⚠ revisar beehusTransactionType

CAMADA TRANSVERSAL — CAIXA (Step 5)            CAMADA NOVA — BAIXA DA PROVISÃO (Step 3.5)
projectedCash = formerCash + Σtxns             para cada provisão com liquidationDate == date:
cashDiff = projectedCash − currentCash           caixa moveu ≈ balance da provisão (ou há txn casando)?
├─ ≈ 0 ─────────────────── ■ consistente          ├─ sim ─────────────── ■ provisão CUMPRIDA → encerrar
├─ txn null ───────────── ⚠ unclassified_txns      └─ não ── PROVISION_NOT_SETTLED ── ⚠ estender / remover / ajustar
├─ sem txn ────────────── ⚠ missing_cash_txn
├─ txn ≈ cashDiff ─────── ⚠ likely_wrong_txn
└─ demais ─────────────── ⚠ value_error
```

---

## Tabela flag → artefato

| Nó | Flag | Condição-chave | Artefato gerado | Diferido? |
|----|------|----------------|-----------------|-----------|
| N1.1 | `MISSING_TRANSACTION` | Δqty≠0, offset=0, sem buySell | Transação `buySell` | não |
| **N1.1** | **`MISSING_PROVISION`** | **Δqty≠0, offset≠0, sem provisão ativa** | **Provisão `buySell`** | **sim** |
| N1.2 | `WITHHOLDING_TAX` | fundo br, resgate, recebeu menos | Transação `taxes` (`balance<0`) | não |
| N1.2 | `MISSING_EXECUTION_PRICE` | execPrice ausente/=PU, implícito≠usado | Preço de execução (bucket) | não |
| N1.2 | `WRONG_TRANSACTION_VALUE` | valor diverge, demais casos | — (revisão manual) | — |
| N2 | `MISSING_EVENT` | Δrentab≠0, sem txn/prov | Transação `coupon`/`amort` (ou provisão se futuro) | depende |
| N2 | `WRONG_EVENT_BALANCE` | txn de evento existe, valor errado | — (revisão manual) | — |
| **N2** | **`WRONG_PROVISION_AMOUNT`** | **provisão existe, valor diverge** | **Provisão (atualizar/recriar)** | **sim** |
| N3 | `MISCLASSIFIED_EVENT_TYPE` | Σtxn ≈ gap, sem Δqty/Δrentab | — (revisão manual) | — |
| 3.5 | `PROVISION_NOT_SETTLED` | provisão venceu, caixa não moveu | — (estender/remover/ajustar) | — |

---

## Convenções de provisão

A **fonte única** das datas é `_prov_dates(date, offset)` /
`_next_biz_day` ([pages/conciliacao.py](../pages/conciliacao.py)), usada tanto no
diagnóstico (`step3_1.provisionData`) quanto na geração (`generate_provisions`),
incluindo o override manual "+ Provisão" e `WRONG_PROVISION_AMOUNT`.

**Janela** (ancorada na data do navPackage em análise, nunca em "hoje"):

| offset | initialDate | liquidationDate | Significado |
|--------|-------------|-----------------|-------------|
| `> 0` | `date` | `date + offset` | Liquidação futura (settlement após nav) |
| `< 0` | `date + offset` | `date` | Nav futuro (nav após settlement) |
| `== 0` | `date` | `date` | Imediata (só via `WRONG_PROVISION_AMOUNT` / manual) |

**Piso:** `liquidationDate = max(computado, próximo dia útil após date)`. Toda
provisão liquida ≥ 1 dia útil após o nav (dia útil = Seg–Sex, sem feriados).

**Sinal do `balance`** (com `impact = |Δqty| × preço`, sempre ≥ 0):

| offset | Δqty | balance | Leitura |
|--------|------|---------|---------|
| `> 0` | `> 0` (compra) | `−impact` | passivo: caixa a pagar no settlement |
| `> 0` | `< 0` (resgate) | `+impact` | ativo: caixa a receber no settlement |
| `< 0` | `> 0` | `+impact` | espelho do nav futuro |
| `< 0` | `< 0` | `−impact` | espelho do nav futuro |

Carga no NAV: `nav_D = Σsecurities.balance + Σprovisions.balance + Σcash`. Uma
compra futura entra como `−impact` (passivo) que zera contra o caixa que sai no
`liquidationDate`; um resgate futuro entra como `+impact` (recebível).

---

## Camada nova — Baixa da provisão (Step 3.5)

> **Gatilho:** dentro do diagnóstico do dia `D` (resposta confirmada: *"no
> diagnóstico do dia"*, sem job separado). O motor já marca em
> `prov_lifecycle_sids` os securities com provisão **criada ou liquidada** em `D`
> (Step 2, condição `c`). Falta **verificar o caixa** quando a provisão **baixa**.

**Quando:** para cada provisão da carteira com `liquidationDate == D`. Nessa data
a provisão deixa de estar ativa (`init ≤ D < liq` é falso em `D == liq`) — ela sai
do NAV. O valor provisionado deve ter se materializado no **caixa real**.

**Expectativa:** na liquidação deve existir, em `D`, movimento de caixa
≈ `provision.balance` — equivalentemente, uma transação (`buySell`/`coupon`/
`amortization`/`taxes`) com `liquidationDate == D`, mesmo `securityId`, somando
≈ `provision.balance`. Reaproveita o caixa projetado do Step 5:

```
movimento_esperado_D ≈ provision.balance
```

**Decisão:**

| Caso | Verificação | Resultado | Ação |
|------|-------------|-----------|------|
| A | caixa moveu ≈ balance (ou há txn casando) | provisão **CUMPRIDA** | encerrar a provisão; idealmente **substituí-la** pela transação real (evita dupla contagem) |
| B | caixa **não** moveu / diverge | `PROVISION_NOT_SETTLED` | ramificar (abaixo) |

**Ramos do caso B** (`PROVISION_NOT_SETTLED`):

- **Liquidação atrasou** (settlement real é depois) → **estender** `liquidationDate`
  (recriar a provisão com novo offset).
- **Liquidação não vai ocorrer** (operação cancelada) → **remover** a provisão.
- **Valor diferente** do projetado → **ajustar** a provisão **ou** criar a
  transação da diferença.

> Esta é a única saída que olha para o **futuro** da provisão. As demais (N1–N3)
> olham o gap do dia. É aqui que um item "diferido" vira "resolvido" (caso A) ou
> "reaberto" (caso B).

---

## Camada caixa (Step 5) — resumo

```
projectedCash = formerCash + Σbalance(todas as txns de D)   ;   cashDiff = projectedCash − currentCash
```

| `cashDiff` | `diagnosis` | Ação |
|-----------|-------------|------|
| `≈ 0` | `consistent` | ok |
| `≠0` + txn `type=null` | `unclassified_txns` | identificar a transação |
| `≠0` + sem txns | `missing_cash_txn` | provável `gainsExpenses`/`rebate`/`otherFee` faltando |
| `≠0` + txn com `balance ≈ cashDiff` | `likely_wrong_txn` | revisar txn suspeita (5.1) |
| `≠0` demais | `value_error` | valor incorreto ou txn faltando |

Tipos `gainsExpenses`, `rebate`, `otherFee` afetam caixa e NAV mas **não** entram
em `inAndOutFlows`/`totalContribution` — causa frequente de gap.

---

## Estrutura de código proposta

A árvore acima sugere isolar a **decisão** (qual artefato) da **detecção** (o
funil) e da **geração** (payload do upstream). Mantém `run_funnel` como produtor
de flags e introduz um *resolver* explícito:

```
pages/diagnostic_engine.py     run_funnel(ctx) -> {step1..step7}        # detecção (existe)
                               + step3_5: baixa de provisão             # NOVO
pages/conciliacao.py           generate_provisions / _transactions /    # geração (existe)
                               _execution_prices
            (proposto)         resolve_intents(diag) -> [Intent]        # NOVO: flag → artefato + "diferido?"
                               verify_settlements(ctx) -> [Settlement]  # NOVO: baixa da provisão
```

- `Intent` = `{kind: provision|transaction|execution_price|none, payload, deferred: bool, flag}`.
  Centraliza o mapa `_FLAG_TXN_TYPE`/`_PROVISION_FLAGS` e o atributo **diferido**
  que a futura regra de publicação vai consumir.
- `verify_settlements` roda dentro do diagnóstico de `D`, itera as provisões com
  `liquidationDate == D` e devolve `{provisionId, status: settled|not_settled,
  expected, observed, suggestion}`.

---

## Decisões em aberto

1. **`MISSING_EVENT` futuro vira provisão?** Hoje sempre gera transação
   `coupon`/`amortization`. Para evento **anunciado mas não pago** (futuro), o
   correto é **provisão** (espelha a nota sobre dividendos em
   [CONCILIACAO_DIAGNOSTICO.md](CONCILIACAO_DIAGNOSTICO.md): anúncio → provisão;
   pagamento → baixa). Critério de "futuro" = existe `offset`/data de pagamento à
   frente? **A definir.**
2. **Baixa da provisão — "substituir" ou "encerrar"?** No caso A, basta confirmar
   a liquidação ou devemos **deletar a provisão** assim que a transação real
   aparece (para o NAV não contar os dois)? **A definir.**
3. **Tolerância da baixa.** Reusar `_approx_txn` (10%) ou a tolerância estreita do
   Step 5.1 (`max(0,01; 0,001×|balance|)`)? **A definir.**
4. **Publicação.** Quais saídas são "bloqueantes" vs. "diferidas" — a definir pelo
   usuário depois, em cima da coluna *Diferido?* da tabela acima.
