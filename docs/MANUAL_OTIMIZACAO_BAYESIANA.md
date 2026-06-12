# Manual — Otimização Bayesiana de Conciliação

## O que é

Quando o sistema detecta uma divergência (gap) entre `returnNavPerShare` e `returnContribution` em uma carteira, o diagnóstico identifica possíveis causas (flags). A otimização bayesiana responde à pergunta:

> **Qual combinação de correções tem a maior probabilidade de fechar o gap?**

Em vez de o operador testar manualmente cada combinação, o sistema calcula automaticamente a melhor correção, valida se ela funciona, e apresenta o resultado com um grau de confiança.

---

## Como funciona — visão geral

```
Diagnóstico (6 steps)
       │
       ▼
┌─────────────────┐
│ Extração de      │  Cada flag vira um "candidato" com:
│ Fatores          │  - impacto em R$
│                  │  - confiança (0-100%)
│                  │  - distribuição (exata ou faixa)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Scoring          │  Testa TODAS as combinações possíveis.
│ Bayesiano        │  Para cada uma:
│                  │  - soma os impactos
│                  │  - calcula se fecha o gap
│                  │  - pondera pela confiança
│ (Monte Carlo     │  Para flags com incerteza (Tier 2):
│  para Tier 2)    │  repete 200x com valores amostrados
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Validação        │  Simula a correção aplicando as fórmulas
│ (Recálculo)      │  de NAV e caixa. Verifica:
│                  │  - Gap fecha? (gapPct ≈ 0)
│                  │  - Caixa bate? (projetado = atual)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Resultado        │  "Melhor Correção" com posterior,
│                  │  alternativas, e métricas
└─────────────────┘
```

---

## O que aparece na tela

Ao clicar **Diagnosticar** em uma carteira com divergência, o modal mostra os 6 steps habituais. Abaixo do Step 6, aparece a seção **Otimização Bayesiana**:

### Card "Melhor Correção"

| Elemento | Significado |
|----------|-------------|
| **Posterior: X%** | Probabilidade de que esta combinação seja a causa real. 100% = certeza total. |
| **Gap resolvido** / **Gap parcial** | Se a correção fecha o gap completamente ou não. |
| **Flag badges** | Cada flag na combinação, com seu impacto em R$, tier, e confiança individual. |
| **Total** | Soma dos impactos de todos os flags. |
| **Residual** | O que sobra do gap após aplicar a correção. Zero = perfeito. |
| **Cenários avaliados** | Quantas combinações o sistema testou. |
| **Aceitar Correção** | Um clique aceita todos os flags e abre a geração de arquivos. |

### Alternativas (colapsável)

Se existem outras combinações viáveis, aparecem aqui ordenadas por probabilidade. Cada uma mostra os flags, total, residual e posterior.

### Sinais indicativos

Anomalias de rentabilidade (Step 6) que não têm impacto direto mas sinalizam que algo está errado. Mostram o z-score (quantos desvios padrão do normal).

### Métricas de complexidade (colapsável)

| Métrica | O que mede |
|---------|-----------|
| **Cobertura** | % do gap explicado pelos flags. 100% = os flags cobrem exatamente o gap. Acima de 100% = flags overlapping. |
| **Complexidade** | Score ponderado pelo tipo de flags. 1-2 = simples, 3-4 = moderado, 5+ = complexo. |
| **Suspeitos** | Quantos securities passaram para análise vs quantos foram eliminados. |
| **Flags** | Total de problemas detectados. |
| **Max Z-Score** | Maior anomalia estatística encontrada (apenas se houver). |

---

## Os três Tiers

### Tier 1 — Determinístico (confiança alta: 80-95%)

O impacto é exato. Não há dúvida sobre o valor da correção.

| Flag | Causa | Exemplo |
|------|-------|---------|
| `MISSING_TRANSACTION` | Transação buySell não registrada | Vendeu 2.000 cotas mas não tem transação correspondente |
| `MISSING_PROVISION` | Provisão não encontrada para liquidação futura | Comprou fundo com D+30 mas sem provisão |
| `MISSING_EVENT` | Evento (cupom/amortização) não registrado | CRI pagou cupom mas não há transação |
| `WRONG_EVENT_BALANCE` | Transação de evento com valor errado | Cupom deveria ser R$ 10k mas veio R$ 8k |
| `WRONG_PROVISION_AMOUNT` | Provisão com valor divergente | Provisão de R$ 50k deveria ser R$ 55k |
| `CASH_MISMATCH` | Caixa projetado ≠ caixa atual | Former + transações = R$ 70k, mas caixa atual = R$ 5k |
| `UNCLASSIFIED_TRANSACTION` | Transação sem tipo identificado | Movimento de R$ 30k sem beehusTransactionType |

### Tier 2 — Bounded (confiança média: 65-75%)

A causa é identificada, mas o valor exato depende de um parâmetro desconhecido. O sistema usa **Monte Carlo** (200 amostras) para estimar a distribuição do impacto.

| Flag | Parâmetro incerto | Como o sistema lida |
|------|-------------------|---------------------|
| `MISSING_EXECUTION_PRICE` | Preço real de execução | Amostra uniformemente entre min(PU, formerPU) × 0.95 e max × 1.05 |
| `WITHHOLDING_TAX` | Alíquota de IR retido | Escolhe entre 15% (peso 60%) e 22.5% (peso 40%) |
| `WRONG_TRANSACTION_VALUE` | Valor correto da transação | Amostra de gaussiana centrada no valor esperado |

### Tier 3 — Indicativo (confiança baixa: 20-40%)

Sinais de que algo está errado, mas sem impacto mensurável. Úteis quando Tier 1/2 não explicam o gap.

| Flag | O que significa |
|------|---------------|
| `MISCLASSIFIED` | Transação pode estar no security errado (balance coincide) |
| `ANOMALY` | Rentabilidade fora de 3σ (sem causa específica) |
| `WRONG_SECURITY` | Security na transação não está na posição |

---

## Como o scoring funciona

### Passo 1 — Extração

Para cada flag detectado nos Steps 3-6, o sistema calcula:

- **Impacto com sinal**: quanto o gap muda se este flag for corrigido
  - Flags Tier 1: impacto = ±raw_impact (sinal do gap)
  - WITHHOLDING_TAX: impacto = actual − expected (negativo = tax retido)
  - MISSING_EXECUTION_PRICE: impacto = amountDiff × (PU − expectedExecPrice)
  - CASH_MISMATCH: impacto = −cashDiff

### Passo 2 — Combinações

O sistema testa **todas** as combinações possíveis dos flags (power set). Com 3 flags, são 7 combinações. Com 10 flags, são 1.023. Limite máximo: 15 flags (32.768 combinações).

### Passo 3 — Posterior por combinação

Para cada combinação:

```
residual   = gapCash − Σ(impactos dos flags incluídos)
likelihood = exp(−residual² / (2 × σ²))    ← "quão perto de fechar o gap?"
prior      = Π(confiança_i) × Π(1 − confiança_j)   ← "quão provável essa combinação?"
posterior  = likelihood × prior
```

A combinação que fecha o gap (residual ≈ 0) E tem alta confiança ganha o maior posterior.

### Passo 4 — Monte Carlo (só Tier 2)

Se há flags Tier 2, o passo 3 roda 200 vezes. Em cada iteração, os impactos dos flags Tier 2 são amostrados da sua distribuição (uniforme, discreta ou gaussiana). Os posteriors são **média** das 200 iterações.

### Passo 5 — Normalização

Todos os posteriors (incluindo o cenário "nenhuma correção") são normalizados para somar 100%.

---

## Validação

Depois de encontrar a melhor combinação, o sistema **simula** a correção sem tocar no banco:

### Gap

```
newGapCash = gapCash − totalImpact
newGapPct  = newGapCash / formerNav
```

Se o flag afeta o NAV (MISSING_PROVISION, MISSING_TRANSACTION, MISSING_EVENT), o `returnNavPerShare` também é ajustado.

**Critério**: `|newGapPct| ≤ 1%` (tolerance configurável)

### Caixa

```
newProjectedCash = formerCash + totalTransactions + cashFix
```

Onde `cashFix` inclui apenas flags que realmente movem caixa:
- **Sempre**: CASH_MISMATCH, UNCLASSIFIED_TRANSACTION, WITHHOLDING_TAX, WRONG_TRANSACTION_VALUE
- **Só se caixa já estava errado**: MISSING_TRANSACTION
- **Nunca**: MISCLASSIFIED (reclassificação não muda o total), MISSING_PROVISION (provisão não é caixa)

**Critério**: `|newProjectedCash − currentCash| ≤ 1%`

### Fallback

Se a melhor combinação falha na validação, o sistema tenta as alternativas em ordem de posterior. Se nenhuma passa, usa a de maior posterior com um aviso.

### Fechar Gap Residual

Se a melhor correção fecha parcialmente o gap (card amarelo, `gapResolved = false`), o sistema propõe automaticamente uma transação sintética de **withdrawalDeposit** para fechar o residual.

- **Valor**: `gapCash − totalImpact` (apenas o residual, não o gap completo)
- **Tipo**: `withdrawalDeposit` — entra em `inAndOutFlows` (Fórmula 2), reduzindo `returnNavPerShare` sem alterar `returnContribution`
- **Instrução**: aplicar **após** as correções da melhor combinação serem processadas e o sistema re-processar

> **Exemplo:** Gap = R$ 23.404,50. Melhor correção tem impacto R$ 21.703,71. O WD proposto será R$ 1.700,79 (o residual), não R$ 23.404,50.

### Validação de Qualidade dos Dados

Antes de calcular impactos, o sistema verifica a qualidade dos dados de cada flag:

| Verificação | Condição | Consequência |
|-------------|----------|-------------|
| **Decimal shift** | Razão de quantidade > 1.000 e prefixo numérico coincide | Flag `DATA_QUALITY_ERROR` emitido (confiança 0.99) |
| **Razão absurda** | Razão de quantidade > 10.000 | Flag `DATA_QUALITY_ERROR` emitido (confiança 0.99) |
| **Impacto vs NAV** | Impacto / formerNav > 100 | Flag `DATA_QUALITY_ERROR` emitido (confiança 0.99) |

O flag `DATA_QUALITY_ERROR` é emitido **ao lado** do flag original. A confiança alta (0.99) faz com que a otimização bayesiana priorize a correção do problema de dados antes de outras causas.

---

## Configuração

O arquivo `data/bayesian_config.json` permite ajustar todos os parâmetros:

| Parâmetro | Default | O que faz |
|-----------|---------|-----------|
| `tolerance` | 0.01 (1%) | Threshold para considerar gap resolvido |
| `gaussian_sigma` | 0.001 | Largura da likelihood — menor = mais exigente no match |
| `monte_carlo_samples` | 200 | Iterações MC para Tier 2. 0 = desabilita MC |
| `confidence_overrides` | (ver abaixo) | Confiança por tipo de flag |
| `exec_price_margin` | 0.05 (5%) | Faixa de busca do execution price |
| `withholding_tax_rates` | {15%: 0.6, 22.5%: 0.4} | Alíquotas e pesos |
| `wrong_txn_relative_error` | 0.02 (2%) | Largura da gaussiana para WRONG_TRANSACTION_VALUE |

### Quando ajustar

- **Muitos falsos positivos** (sistema sugere correções erradas): aumentar `gaussian_sigma` ou reduzir confidences
- **Muitos "Gap parcial"** (não fecha completamente): aumentar `tolerance`
- **Tier 2 muito lento**: reduzir `monte_carlo_samples` (50 é suficiente para a maioria dos casos)
- **IR sempre 15%**: alterar `withholding_tax_rates` para `{"15.0": 1.0}`

---

## Endpoints da API

| Endpoint | Método | Input | DB? | Descrição |
|----------|--------|-------|-----|-----------|
| `/api/conciliacao/bayesian` | GET | `walletId`, `date` (query params) | Sim | Roda diagnóstico + otimização completa |
| `/api/conciliacao/bayesian/from-payload` | POST | JSON do diagnóstico (body) | Não | Otimização sobre payload já computado |

### Exemplo de chamada (from-payload)

```bash
curl -X POST http://localhost:5000/api/conciliacao/bayesian/from-payload \
  -H "Content-Type: application/json" \
  -d @diagnostico.json
```

### Resposta

```json
{
  "walletId": "699cbf8d...",
  "date": "2026-02-10",
  "factors": {
    "gap_cash": -157000.0,
    "flags": [
      {"flag": "MISSING_EXECUTION_PRICE", "impact": -1000.0, "tier": 2, "confidence": 0.70, ...},
      {"flag": "CASH_MISMATCH", "impact": -156000.0, "tier": 1, "confidence": 0.90, ...}
    ]
  },
  "optimization": {
    "bestFix": {
      "flags": ["MISSING_EXECUTION_PRICE", "CASH_MISMATCH"],
      "totalImpact": -157000.0,
      "residualGap": 0.0,
      "posterior": 1.0,
      "gapResolved": true,
      "validation": {
        "valid": true,
        "gapResolved": true,
        "cashConsistent": true,
        "newGapCash": 0.0,
        "newProjectedCash": 1000.0,
        "currentCash": 1000.0
      }
    },
    "alternatives": [...],
    "totalScenarios": 3,
    "noFixPosterior": 0.0,
    "monteCarlo": {"samples": 200, "tier2Count": 1},
    "validationUsed": true
  },
  "summary": {
    "gapPctAbs": 0.372,
    "impactCoverage": 1.29,
    "complexityScore": 5,
    ...
  }
}
```

---

## Glossário

| Termo | Definição |
|-------|-----------|
| **Gap** | Diferença entre `returnNavPerShare` e `returnContribution`. Indica que a posição e as transações não estão reconciliadas. |
| **Flag** | Um problema identificado pelo diagnóstico. Cada flag tem um tipo, impacto, e confiança. |
| **Impacto** | Quanto o gap muda (em R$) se este flag for corrigido. Positivo = fecha gap positivo, negativo = fecha gap negativo. |
| **Confiança** | Probabilidade (0-100%) de que o flag realmente é a causa do gap. |
| **Posterior** | Probabilidade final da combinação, considerando tanto a confiança dos flags quanto o quão bem a soma fecha o gap. |
| **Residual** | O que sobra do gap após aplicar os impactos: `gapCash − totalImpact`. Zero = gap completamente fechado. |
| **Tier** | Nível de certeza do impacto. Tier 1 = exato, Tier 2 = faixa provável (usa Monte Carlo), Tier 3 = indicativo apenas. |
| **Monte Carlo** | Técnica que repete o cálculo N vezes com valores amostrados aleatoriamente, para lidar com incerteza nos flags Tier 2. |
| **Validação** | Simulação das fórmulas de NAV e caixa para confirmar que a correção proposta realmente fecha o gap. |
| **Fallback** | Se a melhor combinação falha na validação, o sistema tenta automaticamente as alternativas em ordem de probabilidade. |
| **Power set** | Todas as combinações possíveis dos flags. Com N flags, existem 2^N − 1 combinações não-vazias. |
| **Likelihood** | "Quão perto esta combinação fecha o gap?" — gaussiana centrada em zero: residual 0 = likelihood máxima. |
| **Prior** | "Quão provável é esta combinação existir?" — produto das confianças dos flags incluídos × (1 − confiança) dos excluídos. |
| **Closing Transaction** | Transação sintética `withdrawalDeposit` gerada para fechar o gap residual após a melhor correção. Valor = `gapCash − totalImpact`. |
| **Data Quality Error** | Flag emitido quando os dados de um security apresentam inconsistência (decimal shift, razão absurda, ou impacto desproporcional ao NAV). |
