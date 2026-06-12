# Otimização Bayesiana — Diagnóstico de Conciliação

> Dado o conjunto de flags diagnosticados (Steps 3–5), encontrar a combinação de correções que mais provavelmente fecha o gap, ponderada por confiança.

---

## Princípio

Cada flag do diagnóstico identifica uma **causa** e um **impacto**. Nem todos os impactos são certos — alguns são exatos, outros têm uma faixa provável. A otimização bayesiana atribui:

- **Confiança** (prior): probabilidade de que o flag seja realmente a causa
- **Distribuição do impacto**: valor exato ou faixa provável do fix

O objetivo: encontrar o subconjunto de flags cujos impactos somados fecham o gap.

```
P(fix_set | gap) ∝ P(gap | fix_set) × P(fix_set)

P(fix_set)      = Π(confidence_i)  para cada flag_i no fix_set
P(gap | fix_set) = N(0, σ²)        likelihood gaussiana centrada em 0
                   onde residual = gapCash − Σ(fix_impact_i)
```

---

## Tiers de Confiança

### Tier 1 — Determinístico (confidence ≈ 1.0)

O valor do fix é totalmente determinado pelos dados. Sem incerteza.

| Flag | Impacto | Distribuição | Confidence default |
|------|---------|--------------|-------------------|
| `MISSING_TRANSACTION` (offset=0) | `amountDiff × price` | Ponto fixo (delta) | **0.95** |
| `MISSING_PROVISION` | `amountDiff × price` | Ponto fixo (delta) | **0.90** |
| `MISSING_EVENT` | `expectedEventCash` | Ponto fixo (delta) | **0.85** |
| `WRONG_EVENT_BALANCE` | `expectedEventCash − eventTransactionTotal` | Ponto fixo (delta) | **0.85** |
| `WRONG_PROVISION_AMOUNT` | `expectedEventCash − provisionAmount` | Ponto fixo (delta) | **0.80** |
| `CASH_MISMATCH` (1 unclassified txn) | `cashDiff` | Ponto fixo (delta) | **0.90** |
| `UNCLASSIFIED_TRANSACTION` | `txn.balance` | Ponto fixo (delta) | **0.85** |

### Tier 2 — Bounded (confidence 0.5–0.9)

A causa é identificada mas o valor exato depende de um parâmetro desconhecido dentro de uma faixa.

| Flag | Parâmetro desconhecido | Distribuição | Confidence default |
|------|----------------------|--------------|-------------------|
| `MISSING_EXECUTION_PRICE` | `executionPrice` real | **Uniforme** `[execPrice_min, execPrice_max]` | **0.70** |
| `WITHHOLDING_TAX` | Alíquota de IR | **Discreta** `{15%, 22.5%}` com pesos `{0.6, 0.4}` | **0.75** |
| `WRONG_TRANSACTION_VALUE` | Valor correto da txn | **Gaussiana** `N(expectedValue, σ²)` | **0.65** |
| `CASH_MISMATCH` (múltiplas txns) | Qual txn é a causa | **Uniforme** sobre candidatas | **0.60** |

**Distribuições detalhadas:**

#### `MISSING_EXECUTION_PRICE`

O sistema usou `PU` como fallback, mas o preço real de execução é desconhecido.

```
execPrice_min = min(PU, formerPU) × (1 − margin)
execPrice_max = max(PU, formerPU) × (1 + margin)

impact = amountDiff × (PU − trueExecPrice)
```

**Parâmetro configurável:** `margin` (default: `0.05` = 5%)

#### `WITHHOLDING_TAX`

Para `brazilianFund`, IR é retido na fonte. A alíquota depende do prazo:

```
alíquotas = {15.0: 0.6, 22.5: 0.4}   // {alíquota%: peso}

impact = transactionBalance × (alíquota / 100)
```

**Parâmetro configurável:** `alíquotas` (mapa alíquota → peso)

#### `WRONG_TRANSACTION_VALUE`

O valor correto é próximo do esperado, mas com erro de medição.

```
impact ~ N(expectedValue − actualBalance, σ²)
σ = |expectedValue| × relative_error
```

**Parâmetro configurável:** `relative_error` (default: `0.02` = 2%)

### Tier 3 — Indicativo (confidence 0.1–0.5)

Correlações sem certeza. Úteis quando nenhum Tier 1/2 fecha o gap.

| Flag | Significado | Confidence default |
|------|------------|-------------------|
| Step 4.3 misclassified | Balance coincide, mas pode ser coincidência | **0.40** |
| Step 6 anomaly | Algo está errado, mas não identifica o quê | **0.20** |
| `WRONG_SECURITY` | Security pode estar errado na txn | **0.30** |

---

## Parâmetros Configuráveis

Todos os parâmetros podem ser ajustados pelo usuário via `data/bayesian_config.json`:

```json
{
  "tolerance": 0.01,
  "gaussian_sigma": 0.001,
  "monte_carlo_samples": 200,
  
  "confidence_overrides": {
    "MISSING_TRANSACTION": 0.95,
    "MISSING_PROVISION": 0.90,
    "MISSING_EVENT": 0.85,
    "WRONG_EVENT_BALANCE": 0.85,
    "WRONG_PROVISION_AMOUNT": 0.80,
    "MISSING_EXECUTION_PRICE": 0.70,
    "WITHHOLDING_TAX": 0.75,
    "WRONG_TRANSACTION_VALUE": 0.65,
    "UNCLASSIFIED_TRANSACTION": 0.85,
    "CASH_MISMATCH": 0.90,
    "MISCLASSIFIED": 0.40,
    "ANOMALY": 0.20,
    "WRONG_SECURITY": 0.30
  },

  "exec_price_margin": 0.05,
  
  "withholding_tax_rates": {
    "15.0": 0.6,
    "22.5": 0.4
  },

  "wrong_txn_relative_error": 0.02
}
```

### Onde configurar

| Parâmetro | Onde | Efeito |
|-----------|------|--------|
| `confidence_overrides` | Por flag | Aumenta/diminui a probabilidade prior de cada tipo de flag |
| `exec_price_margin` | Global | Amplia/reduz a faixa de busca do execution price |
| `withholding_tax_rates` | Global | Altera as alíquotas e seus pesos |
| `wrong_txn_relative_error` | Global | Controla a largura da gaussiana para valores incorretos |
| `tolerance` | Global | Threshold para considerar o gap como resolvido |
| `gaussian_sigma` | Global | Largura da likelihood (quão "exato" o match precisa ser) |
| `monte_carlo_samples` | Global | Número de iterações MC para Tier 2 (default: 200, 0 = desabilitado) |

---

## Algoritmo

### Input

- `gapCash` (R$) — gap atual
- `flags[]` — lista de flags diagnosticados, cada um com `impact` e `flag` type

### Processo

1. Para cada flag, buscar `confidence` e `distribution` do impacto
2. **Monte Carlo (Tier 2):** Para cada iteração de N amostras:
   - Para cada flag Tier 2, amostrar um impacto da sua distribuição
   - Flags Tier 1/3 mantêm impacto determinístico
3. Enumerar combinações de flags (power set, limitado a 15 flags — hard ceiling)
4. Para cada combinação:
   - `total_impact = Σ(fix_impact_i)`
   - `residual = gapCash − total_impact`
   - `likelihood = exp(−residual² / (2 × σ²))`
   - `prior = Π(confidence_i) × Π(1 − confidence_j)` para j fora do set
   - `posterior ∝ likelihood × prior`
5. Normalizar posteriors (incluindo cenário no-fix)
6. Se Tier 2 presente: média dos posteriors normalizados across N iterações
7. Retornar top-K combinações ordenadas por posterior

### Validação (formulas do CONCILIACAO_RECALCULO.md)

Após encontrar o `bestFix`:

1. Computar `fix_contribution_delta = totalImpact / formerNav`
2. Recalcular `returnContribution = oldReturnContribution + fix_contribution_delta`
3. Para flags que afetam NAV (MISSING_PROVISION, MISSING_TRANSACTION, MISSING_EVENT): recalcular `returnNavPerShare`
4. Novo `gapPct = newReturnNavPerShare − newReturnContribution`
5. Verificar caixa: `projectedCash = formerCash + totalTransactions + cashFix`
6. Validar:

| Validação | Condição |
|-----------|----------|
| Gap resolvido | `abs(gapPct) ≤ tolerance` |
| Caixa consistente | `abs(projectedCash − currentCash) ≤ tolerance` |

7. Se validação falha → tentar próxima alternativa por posterior
8. Se nenhuma alternativa passa → usar highest-posterior como fallback (com aviso)

### Output

```json
{
  "bestFix": {
    "flags": ["MISSING_TRANSACTION", "WITHHOLDING_TAX"],
    "flagDetails": [
      {"flag": "MISSING_TRANSACTION", "securityId": "...", "securityName": "...", "impact": 5000, "confidence": 0.95, "tier": 1}
    ],
    "totalImpact": 5230.50,
    "residualGap": 0.02,
    "posterior": 0.87,
    "gapResolved": true,
    "validation": {
      "valid": true,
      "gapResolved": true,
      "cashConsistent": true,
      "newGapPct": 0.000001,
      "newGapCash": 0.02,
      "newReturnNavPS": 0.0034,
      "newReturnContrib": 0.0034,
      "newProjectedCash": 5000.0,
      "currentCash": 5000.0
    }
  },
  "alternatives": [...],
  "totalScenarios": 7,
  "noFixPosterior": 0.0001,
  "indicativeFlags": [...],
  "monteCarlo": {"samples": 200, "tier2Count": 1},
  "validationUsed": true
}
```

---

## Detalhes de Implementação

### Signed Impact

O impacto de cada flag é calculado com sinal para que `Σ(impacts) ≈ gapCash`:

| Flag type | Cálculo do sinal |
|-----------|-----------------|
| Tier 1 (MISSING_TRANSACTION, PROVISION, EVENT) | `sign(gapCash) × abs(raw_impact)` |
| WITHHOLDING_TAX | `actualBalance − expectedValue` (negativo = tax withheld) |
| MISSING_EXECUTION_PRICE | `amountDiff × (PU − expectedExecPrice)` |
| WRONG_TRANSACTION_VALUE | `actualBalance − expectedValue` |
| WRONG_PROVISION_AMOUNT | `expectedEventCash − provisionAmount` |
| WRONG_EVENT_BALANCE | `expectedEventCash − eventTransactionTotal` |
| CASH_MISMATCH | `−cashDiff` (positive cashDiff = overstated txns) |
| Step 4 flags (UNCLASSIFIED, WRONG_SECURITY, MISCLASSIFIED) | Raw balance (actual cash flow direction) |

### effective_sigma

Para invariância de escala, a likelihood sigma é relativa ao gap:

```
effective_sigma = max(abs(gapCash) × gaussian_sigma, tolerance)
```

Isso previne posteriors colapsados em gaps pequenos (< R$ 1.000).

### Limites de segurança

- Hard ceiling de **15 flags** no power set (2^15 = 32.768 combinações)
- Config é carregada **uma vez** por request e passada para todas as funções
- JSON parse errors no config file são logados e defaults são usados

---

## Multi-Correção

### Quando ativa

Quando **nenhuma combinação** de flags fecha o gap (todos posteriors ≈ 0%) mas há **3+ flags com coverage > 1.5x**, o sistema entra em modo **multi-correção**.

Isso acontece quando todos os securities têm problemas simultâneos. A fórmula do gap (`returnNavPerShare − returnContribution`) não se decompõe linearmente quando múltiplos securities têm erros de quantidade, transação e provisão ao mesmo tempo — os impactos interagem.

### Critérios de ativação

```
1. impactCoverage > 1.5  (Σ|impacts| >> |gapCash|)
2. bestPosterior < 1%    (nenhum subconjunto fecha o gap)
3. actionableFlags >= 3
```

### Como funciona

Em vez de procurar o melhor subconjunto, o sistema:

1. Propõe **TODAS** as correções em conjunto
2. Valida qualitativamente (não tenta `gapCash − totalImpact = 0` pois a fórmula é não-linear)
3. Gera alternativas **leave-one-out** (todas menos uma) para identificar qual flag é dispensável

### Validação multi-correção

| Critério | Condição |
|----------|----------|
| Qualidade dos flags | Todos com confidence ≥ 50% (medium ou high) |
| Cobertura de suspeitos | Todos os suspects do Step 2 têm pelo menos um flag |
| Caixa consistente | `projectedCash ≈ currentCash` (apenas flags cash-always) |

**Nota**: MISSING_TRANSACTION não afeta caixa em multi-correção pois são transações novas (o caixa já reflete a ausência delas).

### Output multi-correção

```json
{
  "bestFix": {
    "flags": ["MISSING_PROVISION", "MISSING_TRANSACTION", ...],
    "posterior": null,
    "gapResolved": true,
    "validation": {
      "valid": true,
      "mode": "multi-correction",
      "combinedConfidence": 0.925,
      "minConfidence": 0.90,
      "flagCount": 6,
      "perFlag": [
        {"flag": "MISSING_PROVISION", "securityName": "Fund A", "confidence": 0.90, "tier": 1, "quality": "high"},
        ...
      ],
      "coverageComplete": true,
      "unaddressedSuspects": [],
      "cashConsistent": true
    }
  },
  "alternatives": [
    {"excluded": "MISSING_PROVISION (Fund A)", "validation": {"valid": false, "coverageComplete": false}},
    ...
  ],
  "multiCorrection": true,
  "validationUsed": true
}
```

### Na interface

O card mostra **"Multi-Correção"** com badge **"Todas as correções"** em vez de posterior. O botão aceita todos os flags de uma vez. As alternativas mostram o impacto de remover cada flag individualmente.

---

## Transação de Fechamento (Closing Transaction)

Quando a melhor correção não fecha completamente o gap (`gapResolved = false`), o sistema gera automaticamente uma transação sintética `withdrawalDeposit` para fechar o residual.

### Cálculo

```
residual = validation.newGapCash  OU  bestFix.residualGap
WD = residual   (não gapCash)
```

A transação entra em `inAndOutFlows` (Fórmula 2 do Recálculo), reduzindo `returnNavPerShare` sem alterar `returnContribution`. Quando `WD = residual`, o gap → 0.

### Condição de geração

```python
if abs(residual) > abs(gap_cash) * tolerance:
    closing_txn = _build_closing_transaction(diag, residual)
```

Só é gerada se o residual excede a tolerância (default 5%).

### Output

```json
{
  "closingTransaction": {
    "type": "withdrawalDeposit",
    "balance": 1700.79,
    "description": "Ajuste de passivo — fecha gap residual de conciliação",
    "formula": "WD = gapCash(23,404.50) − totalImpact(21,703.71) = R$ 1,700.79"
  }
}
```

---

## MISCLASSIFIED — Aceite e Exclusão da Transação Original

Quando o usuário aceita uma correção do tipo `MISCLASSIFIED` (Step 4.3 ou via Bayesian), o sistema entende que:

1. Existe uma transação no banco que foi **classificada no security errado** — mas ela representa um movimento de caixa real.
2. A correção é **reclassificar** esse movimento para o security correto (o *target* sugerido pelo Step 4.3 em `matches[]`).

### Duas mutações no store de correções

O aceite gera **dois registros** em `data/correcoes/<companyId>/<date>/<walletId>.json`, em uma única operação atômica:

| Bucket | Conteúdo |
|--------|----------|
| `transactions` | Nova transação sob o `securityId` *target* (o correto). Mesmo `balance`, `operationDate`, `liquidationDate`, `beehusTransactionType` da original. |
| `deletions` | Ponteiro para a transação original: `{originalId, walletId, securityId, balance, operationDate, beehusTransactionType, reason: "MISCLASSIFIED", sourceAnomalyKey}`. |

### Efeito no pipeline de diagnóstico

- `load_corrections_for_wallet(...)` agora retorna **3-tupla**: `(transactions, provisions, deletions)`.
- No carregamento de transações do DB (Step 3/4), cada `db.transactions` doc traz o `_id` propagado para `all_txns_flat` como `txnId`.
- Antes de Steps 4/5 rodarem, o pipeline **filtra** `all_txns_flat` removendo qualquer entrada cujo `txnId` esteja no conjunto de `deletions[*].originalId`.
- Resultado: a conciliação enxerga o **pós-correção** — a transação original some do cálculo de caixa e a nova transação (no security correto) entra.

### Bookkeeping apenas

O bucket `deletions` é **estritamente interno**:

- **NÃO** entra no export JSON/XLSX (arquivos destinados ao ingest do banco).
- **É** visível na página **Correções** em uma aba dedicada ("Para Excluir"), para o usuário auditar e, se necessário, remover/editar antes do próximo recálculo.
- A exclusão efetiva da transação no banco é feita **fora do sistema** (DBA/ingest manual), usando a lista da aba como referência.

### Idempotência

A chave `sourceAnomalyKey` é gravada tanto na nova transação quanto no registro de exclusão. Reenviar o mesmo aceite não duplica linhas — o `bulk_append` dedupe por essa chave em cada bucket.

---

## Refinamento ±Window (offset/settlement drift)

> Disparado **manualmente** pelo botão **"Refinar (±2d)"** no header do modal Diagnosticar. Varre a vizinhança de ±N dias (default 2) para detectar que o diagnóstico original pode estar errado por assumir que os offsets do security estão corretos e a liquidação da instituição ocorreu na data esperada.

### Motivação

O diagnóstico principal (Steps 3–5) deriva o flag de cada security a partir do **offset configurado**:

```
offset = settlementDays − navDays
```

e, no caso de `MISSING_TRANSACTION` (offset=0) ou `MISSING_PROVISION` (offset≠0), assume que o valor teria liquidado exatamente em `date` ou na provisão alinhada com `date + offset`. Se o security tem offset **mal cadastrado** no banco, ou a instituição liquidou em um dia diferente do esperado, o pipeline original produz flags que não correspondem à realidade — e nenhuma combinação bayesiana fecha o gap.

### Endpoint

```
GET /api/conciliacao/diagnose/refine?walletId={wid}&date={yyyy-mm-dd}&window=2
                                     [&securityId={sid}]    # scope to Analysis 1 on one security
                                     [&txnSecurityId={sid}] # scope to Analysis 2 on txns of one security
```

- `window` aceita valores 1–7 (default 2, clampeado).
- **Dias vizinhos = dias com `processedPosition` para esta wallet** (não dias de calendário). Pega os `window` dias **antes** mais recentes e os `window` dias **depois** mais próximos. Fins de semana, feriados e gaps de reconciliação são automaticamente ignorados.
- Não executa o pipeline completo — apenas as duas análises descritas abaixo.
- `securityId` e `txnSecurityId` são **opcionais e mutuamente exclusivos na prática** (se passados juntos, ambas as análises retornam vazio). Usados pelos botões **Refinar** por-item no Step 3 e Step 4.2 respectivamente — cada clique consulta o endpoint com o escopo do item clicado, evitando re-scanning da carteira inteira.

### Análise 1 — `securityRefinements`

Para cada security com `amountDiff ≠ 0` na posição de `date`:

1. Buscar **todas as transações** em `db.transactions` onde `walletId` bate e `liquidationDate ∈ nearbyDates` (sem filtrar por securityId).
2. Para cada transação da vizinhança, incluir como match se **qualquer** dos dois critérios baterem:
   - **`securityId`** — mesmo `securityId` do suspeito; ou
   - **`balance`** — `_approx(|balance|, expectedImpact)` onde `expectedImpact = |amountDiff| × price`
3. Para cada match, reportar:
   - `daysDelta = liquidationDate − date` (dias de **calendário** — semântica do cadastro `subscriptionSettlementDays` etc.)
   - `positionDayDelta` — rank da `liquidationDate` entre os dias em que **esta carteira** tem `processedPosition` (ex.: `-1` = dia de reconciliação imediatamente anterior, `+1` = próximo). É o valor exibido na UI como `D±N` — ignora fins de semana, feriados e gaps.
   - `impliedOffset = daysDelta` (qual offset em dias de calendário zeraria o gap se o match fosse a transação real)
   - `matchReason ∈ {"securityId","balance","both"}`
   - `sameSecurity` (bool) — se `matchReason = "balance"` isoladamente, o securityId da transação é **diferente** do suspeito → pista de **WRONG_SECURITY** na ingestão
   - `signMatch` (bool) — se o sinal do `balance` bate com a direção esperada (subscrição → negativo; resgate → positivo)
4. Ordenação dos matches: `balanceApprox` primeiro, depois `signMatch`, depois `|daysDelta|`, depois `sameSecurity` como desempate.
5. O security é marcado como **"provável causa"** (`likelyCause = true`) se existir pelo menos um match com `balanceApprox ∧ signMatch`.

Hipóteses apresentadas ao usuário (podem coexistir — **não são mutuamente exclusivas**):

- **`WRONG_SECURITY`** — aparece quando `best.sameSecurity = false`. A transação foi lançada em outro security (mesma família/emissor/vencimento, tipo errado) mas o movimento de quantidade ocorreu no security suspeito — provável erro de ingestão.
- **`OFFSET_OR_SETTLEMENT_DRIFT`** — aparece quando `best.positionDayDelta ≠ 0`. O offset configurado está incorreto (deveria refletir `best.daysDelta` dias de calendário) — **OU** a instituição liquidou / atualizou a quantidade em data diferente da prevista.

Se **ambos** aplicam (match em securityId diferente **e** data diferente), as duas hipóteses são exibidas juntas com o rótulo "não mutuamente exclusivas". O usuário decide qual é a causa real e registra via botão **Aceitar**.

### Botões "Aceitar" por hipótese

Cada hipótese aplicável é renderizada como um **card colorido** (violet para `WRONG_SECURITY`, orange para `OFFSET_OR_SETTLEMENT_DRIFT`) com **seu próprio** botão **Aceitar**. Quando ambas aplicam, o usuário aceita cada uma independentemente — elas não são mutuamente exclusivas.

O clique:

- Faz `POST /api/conciliacao/diagnose/refine/accept` com o suspect, o match **e** a `hypothesis` específica (campo obrigatório — um dos: `WRONG_SECURITY` | `OFFSET_OR_SETTLEMENT_DRIFT`).
- Persiste registro de auditoria em `data/refinement_feedback/<companyId>/<date>/<walletId>.json`, deduplicado por `(securityId, matchTxnId, hypothesis)` — idempotente. `firstAcceptedAt` preservado entre cliques; `updatedAt` atualizado.
- Se `hypothesis = OFFSET_OR_SETTLEMENT_DRIFT`: **também** grava uma provisão em `data/correcoes/<companyId>/<date>/<walletId>.json` (bucket `provisions`) para fechar o gap pendente. Deduplicada por `sourceAnomalyKey = "OFFSET_DRIFT|<securityId>|<matchTxnId>"`.
- Se `hypothesis = WRONG_SECURITY`: **não** grava correção aqui. Esse fix pertence ao fluxo `MISCLASSIFIED` (deleção + transação de reposição), fora do escopo deste endpoint.

#### Regra de geração da provisão (apenas OFFSET_OR_SETTLEMENT_DRIFT)

A provisão deve preencher o intervalo em que o NAV estaria *descasado* entre a data em que o caixa se movimenta e a data em que a quantidade aparece na posição. A janela ativa é dada pela query de `db.provisions`:

```
initialDate ≤ d < liquidationDate
```

(limite superior **exclusivo** — no dia `liquidationDate` a provisão já não está ativa, pois a outra ponta do par cash/quantity já se realizou.)

**Datas** — sempre: `initialDate = data do evento anterior`; `liquidationDate = data do evento posterior`.

| Cenário | `match.liquidationDate` vs `date` | `initialDate` | `liquidationDate` | Dias ativos |
|---|---|---|---|---|
| A (match no passado) | `<` date | `match.liquidationDate` | `date` | `[match.liq, date−1]` |
| B (match no futuro)  | `>` date | `date`                  | `match.liquidationDate` | `[date, match.liq−1]` |

**Sinal** — depende de **qual lado já se movimentou** durante a janela ativa, pois a provisão deve compensar isso para manter `nav = Σ(securities.balance) + Σ(provisions.amount) + Σ(cashAccounts.values)` estável:

| Cenário | Janela ativa | Já movimentou | Subscrição (qty ↑) | Resgate (qty ↓) |
|---|---|---|---|---|
| **B** (match futuro) | `[date, match.liq)` | qty (na posição de hoje) | **negativo** (payable) | **positivo** (receivable) |
| **A** (match passado) | `[match.liq, date)` | caixa (no dia do match) | **positivo** (asset receivable) | **negativo** (asset payable) |

Equivalentemente em termos de `match.balance`:

- **B** (futuro): `provision.balance = match.balance`
- **A** (passado): `provision.balance = −match.balance`  *(sinal invertido)*

**Magnitude** — `match.balance` é preferido (preserva ajustes de taxas / IR / variações cambiais). Fallback: `±|amountDiff × price|` com o sinal da tabela acima.

**Exemplo concreto (LCA BV 81.50%CDI, wallet `69b99c…`, `date = 2026-04-13`):**

- `amountDiff = +178` (subscrição), `match.liquidationDate = 2026-04-10` (passado), `match.balance = −178.000`
- Cenário A → `initialDate = 2026-04-10`, `liquidationDate = 2026-04-13`, `balance = +178.000`
- Janela ativa: 2026-04-10 / 2026-04-11 / 2026-04-12 (dia 2026-04-13 **excluído**)
- Efeito: eleva `nav_2026-04-10` de `C₀ − 178.000` para `C₀`, corrigindo o `former_nav` usado no cálculo de retorno de 2026-04-13 e fechando o gap.

> **Nota histórica:** o gerador legado de `MISSING_PROVISION` em `Step 3.1` usava um sinal **cenário-agnóstico** (`+impact` para subscrição sempre), o que acertava o Cenário A e errava o Cenário B. Corrigido nesta mesma mudança para seguir a tabela acima. Registros pré-existentes em `db.provisions` com sinal errado **não foram retro-corrigidos** — continuarão como estão até alguém os reingerir.

#### Recálculo do `gapCash` após aceitar uma provisão (Option 1 — full NAV recalc)

Quando o usuário aceita uma provisão com janela `[initialDate, liquidationDate)` que **não inclui** `date` (Cenário A, com `liquidationDate == date`), o `correctionsImpact` rudimentar (`gap_cash ± Σ|balance|`) não captura o efeito — a provisão atua retroativamente sobre `former_nav`, não sobre o NAV de `date`. O backend aplica agora o recálculo completo pela cadeia de fórmulas:

```
nav_D             = Σ securities.balance + Σ provisions.amount + Σ cash
navPerShare_D     = (nav_D − inAndOutFlows_D) / formerAmount_D
returnNavPS_today = navPerShare_today / navPerShare_former − 1
gapPct_today      = returnNavPS_today − returnContribution_today
gapCash_today     = gapPct_today × former_nav
```

**Implementação** — `_recalc_gap_with_corrections(company_id, wallet_id, date, nav_pkg, former_date, former_nav, return_contrib, gap_cash)` em [pages/conciliacao.py](../pages/conciliacao.py):

1. Carrega provisões pendentes ativas em `date` (`delta_nav_today`) e em `former_date` (`delta_nav_former`), via `load_active_pending_provisions` em cada ponta.
2. Busca o `navPackage` de `former_date` para extrair `formerAmount` e `inAndOutFlows` — o doc de hoje já traz os seus.
3. `new_nav_today = nav_T + delta_nav_today`; `new_nav_former = former_nav + delta_nav_former`.
4. `new_nps_today / new_nps_former` pela fórmula acima.
5. `new_return_nav_ps = new_nps_today / new_nps_former − 1`.
6. `new_gap_pct = new_return_nav_ps − return_contribution`; `new_gap_cash = new_gap_pct × new_nav_former`.
7. Aplica heurística legada de `|balance|` para transações pendentes *por cima* do resultado (transações ainda não têm tratamento rigoroso — pendente para um PR futuro).

**Exemplo (wallet `69b99c…`, `date = 2026-04-13`, provisão +R$ 178.000 ativa em [2026-04-10, 2026-04-13)):**

| Grandeza | Antes | Depois |
|---|---:|---:|
| `nav_2026-04-10` | R$ 4.695.476,00 | R$ 4.873.476,00 |
| `nps_2026-04-10` | 9,2747 | 9,6403 |
| `nav_2026-04-13` | R$ 4.883.024,50 | idem (provisão não ativa) |
| `nps_2026-04-13` | 9,6452 | idem |
| `returnNavPerShare_2026-04-13` | 0,03994 | 0,000509 |
| `gapPct_2026-04-13` | 0,03793 | −0,001509 |
| **`gapCash_2026-04-13`** | **+R$ 178.078,72** | **−R$ 7.380,51** |

A diferença residual (−R$ 7.380,51) reflete o `returnContribution ≈ 0,2%` não explicado por esta correção específica — é um problema de outro flag, não desta provisão.

> **Nota de armazenamento:** o mesmo padrão de `data/correcoes/…` é reutilizado (arquivo JSON local), pois o usuário do Mongo usado pela app não possui privilégio `createCollection` e esses registros são app-owned.

A lista `GET /api/conciliacao/diagnose/refine/accepted` retorna tuplas `(securityId, matchTxnId, hypothesis)` já confirmadas e é pré-carregada quando o refinamento é executado, de modo que cada botão aparece marcado como **✓ Aceito** (disabled) independentemente.

### Análise 2 — `transactionRefinements`

Para cada transação em `date` cujo `securityId` **não está na posição** de `date` (casos `WRONG_SECURITY` e não classificadas com `securityId`):

1. Buscar `db.processedPosition` onde `walletId` bate e `positionDate ∈ [date-W, date+W] \ {date}`.
2. Para cada posição vizinha que contém o mesmo `securityId`, reportar `positionDate`, `quantity`, `pu`, `daysDelta`.

Hipótese apresentada ao usuário:

> A transação foi datada em `date` mas o security só existe na carteira em dia(s) vizinho(s) — possível erro de data na ingestão (liquidação antecipada/atrasada pela instituição) ou offset mal configurado.

### Quando usar

Considere o refinamento como **etapa complementar**: se os flags iniciais + otimização bayesiana **não fecham o gap** (`gapResolved = false` ou `noFixPosterior` alto), rode o refinamento para checar se a raiz é temporal (offset / liquidação fora da data).

O refinamento **não gera correções automáticas**. Ele surfa evidências para guiar o usuário a:

- Corrigir `subscriptionSettlementDays` / `subscriptionNavDays` / `redemptionSettlementDays` / `redemptionNavDays` no cadastro do security, ou
- Confirmar que a liquidação real aconteceu em `date ± N` e re-ingerir a transação com a data correta.

### Saída

```json
{
  "walletId": "...",
  "date": "2026-04-23",
  "window": 2,
  "nearbyDates": ["2026-04-21", "2026-04-22", "2026-04-24", "2026-04-25"],
  "securityRefinements": [
    {
      "securityId": "...",
      "securityName": "Fundo X",
      "amountDiff": -1000.0,
      "expectedImpact": 50000.00,
      "direction": "redemption",
      "configuredOffset": 0,
      "settlementDays": 1,
      "navDays": 1,
      "likelyCause": true,
      "matches": [
        {"txnId": "...", "liquidationDate": "2026-04-24", "balance": 50000.00,
         "beehusTransactionType": "buySell", "daysDelta": 1,
         "balanceApprox": true, "impliedOffset": 1}
      ]
    }
  ],
  "transactionRefinements": [
    {
      "txnId": "...",
      "securityId": "...",
      "securityName": "Fundo Y",
      "balance": -12345.67,
      "beehusTransactionType": "buySell",
      "direction": "subscription",
      "configuredOffset": 1,
      "matches": [
        {"positionDate": "2026-04-24", "quantity": 100, "pu": 123.45, "daysDelta": 1}
      ]
    }
  ]
}
```

---

## Validação de Qualidade dos Dados

Antes da extração de fatores, cada flag é verificado contra checks de qualidade:

| Check | Condição | Flag emitido |
|-------|----------|-------------|
| Decimal shift | `qty_ratio > 1000` e prefixo numérico coincide | `DATA_QUALITY_ERROR` (conf. 0.99) |
| Razão absurda | `qty_ratio > 10000` | `DATA_QUALITY_ERROR` (conf. 0.99) |
| Impacto vs NAV | `impact / formerNav > 100` | `DATA_QUALITY_ERROR` (conf. 0.99) |

O flag `DATA_QUALITY_ERROR` é emitido **ao lado** do flag original (não o substitui). A confiança 0.99 garante que a otimização priorize a investigação do problema de dados.
