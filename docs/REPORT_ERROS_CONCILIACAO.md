# Relatório de Erros de Conciliação (mov.)

Relatório **textual** que consolida, por carteira, os problemas de dado que aparecem ao
movimentar a posição origem→alvo. Objetivo: dar visibilidade ao que precisa de correção
para chegar a **GAP = 0**, inclusive os casos que hoje o motor **absorve em silêncio**.

O relatório é **texto puro** (para enviar ao cliente), gerado no front a partir do
resultado da projeção (`_build_movement_for_wallet`) que já está em memória — **não faz
chamada nova ao backend**. Abre num modal com **Copiar** e **Exportar .txt**.

## Dois tipos de relatório (discriminador: existe posição-alvo?)

O **mesmo sinal muda de significado** conforme a unprocessed da data-alvo já foi
processada. O motor já expõe o gatilho:

- **`diff.hasTarget`** — existe unprocessed na data-alvo. **Gatilho principal.**
- **`diff.cashStatus`** ∈ `{match, mismatch, unknown}` — `unknown` quando não há caixa
  oficial no alvo (sinal fino, secundário).

| Tipo | Condição | O que reporta |
|---|---|---|
| **A — Conciliação** | `hasTarget = true` | catálogo completo (reconciliação + referência + insumo) |
| **B — Forward / preparação** | `hasTarget = false` | **somente** defeitos de **insumo** |

O motor já se comporta assim: com `hasTarget=false` o diff não é computado e o recon só
roda `if hasTarget and cashStatus != "unknown"` — logo divergências/recon **não existem**
no Tipo B (não há risco de reportá-los à toa).

## Três naturezas (mapeiam nos dois tipos)

1. **insumo** — defeito de origem/transação/catálogo; **independe do alvo** → reporta em **A e B**.
2. **reconciliação** — só existe com alvo → **só A** (em B é N/A, nem é calculado).
3. **referência** — lacuna de dado-de-referência **esperada em forward** → **erro em A, ruído suprimido em B**.

> Nuance importante: **PU repetido** é natureza *referência* (o alvo é a fonte primária de
> PU; sem alvo, repetir é esperado → suprimir em B). Já **settlementDays vazio** é natureza
> *insumo* (vem do `securitySecInfo` da transação/catálogo, **não** da posição-alvo) → é
> defeito **sempre**, reporta em A e B. (Correção do agrupamento inicial do usuário.)

## Catálogo por natureza

### Insumo (reporta em A **e** B)

| Caso | Origem do sinal | Derivável no front hoje? |
|---|---|---|
| Transação órfã (sem `securityId`) | `transactions[].securityId` vazio | **Sim** |
| Transação de tipo desconhecido | `transactions[].type` fora do conjunto tratado | **Sim** |
| Cupom/amortização sem `securityId` | `transactions[]` tipo coupon/amortization + sid vazio | **Sim** |
| Ativo não mapeado na origem | `rows[].mapped == false` | **Sim** |
| settlementDays vazio / offset=0 | offset do `securitySecInfo` da txn | **Não** — precisa expor no backend |
| Split esperado sem `factor` | `securityEvents` (catch silencioso) | **Não** — precisa expor |
| Mapeamento divergente origem×alvo | `_enrich_src_from_target` sobrescreve sem flag | **Não** — precisa expor |
| `currencyId` ausente → fallback BRL | `resolve_wallet` | **Não** — precisa expor |

### Reconciliação (só Tipo A)

| Caso | Origem do sinal |
|---|---|
| Transação ausente (caixa não bate) | `reconTransactions[]` / `diff.diverged` `suggestedAction="transaction"` |
| Divergência de caixa | `diff.cashStatus="mismatch"` + `diff.cashResidual` |
| ~~Só PU/saldo diverge~~ | **removido** — saldo-alvo avaliado ao PU em uso; `priceDiverged` sempre `False` |
| Ativo só na projetada | `diff.onlyCalc[]` |
| Ativo só no alvo | `diff.onlyReal[]` |
| Múltiplas divergências (confiança média) | contagem de `diff.diverged` com `qtyDiverged` |
| IRRF ausente | `irrf[]` `covered=false` |
| IRRF multi-resgate (revisar) | `irrf[]` `multiEvent=true` |
| Liquidação stockETF × B3 residual | `stockEtfLiquidation.residual` |
| Mapeamento divergente origem×alvo | (insumo, mas só detectável com alvo) |

### Referência (erro em A; suprimido em B)

| Caso | Origem do sinal |
|---|---|
| PU repetido (sem PU-alvo) | `rows[].pricingType == "REPETIDO"` |
| Preço de execução a corrigir | `executionPriceFixes[]` (status placeholder/ausente) |
| Vencimento sem execPrice | (silencioso — precisa expor no backend) |

### Benigno / by-design (NÃO entra)

Qtd diverge + **caixa bate** (provisão de liquidação futura, NAV-neutra); **coberto por
provisão oficial** (adoção); ajustes de vencimento (`_CASH_EXCLUDED_TYPES`); posição-origem
zerada descartada; alta confiança (1 divergência). Caixa **INDETERMINADO** entra apenas como
nota de "faltou dado para classificar" (não como erro).

## UI

- **Tela principal (grade):** botão **"Relatório de erros"** gera para **todas as carteiras
  selecionadas** (checkbox) que já foram movimentadas (`state="done"`). Habilita junto com os
  demais botões de envio (`_syncSelHeader`).
- **Tela de detalhe:** botão **"Relatório de erros"** gera para a carteira aberta.
- **Modal de texto:** conteúdo em `<textarea>` monoespaçado (texto puro), com **Copiar**
  (clipboard) e **Exportar .txt** (download `erros_conciliacao_<empresa>_<data>.txt`).
  Fecha por `✕`, clique no fundo ou `Esc`.

Formato do texto: cabeçalho (empresa, janela, data de geração, nº de carteiras) →
**SEÇÃO TIPO A** (carteiras com alvo) → **SEÇÃO TIPO B** (forward, só insumo) → total.
Cada carteira lista seus itens como `• [Tag] descrição`; carteira sem erros vira `OK`.

## Pendências (fase 2 — exigem backend)

Para reportar os insumos hoje silenciosos, o motor precisa **expor** no payload:
- `settlementDaysUnknown` por transação (offset=0 por `securitySecInfo` ausente);
- `splitFactorMissing` quando há evento de split esperado mas `factor` não veio;
- `mappingConflict` quando origem e alvo resolvem o mesmo `unprocessedId` para `securityId`
  diferentes (`_enrich_src_from_target`);
- `currencyFallback` quando `currencyId` caiu no default BRL.

## Bug relacionado (a confirmar)

`_diagnose` monta `off_prov_sids` **sem filtrar por `provisionType`** (o comentário ao lado
diz "só as de offset/buySell cobrem divergência de QUANTIDADE"). Uma provisão oficial de
**dividendo/JCP** suprimiria indevidamente o recon de uma divergência de qtd, marcaria
`coveredByProvision` e — com a adoção da qtd-alvo — **adotaria** a qtd como se fosse
liquidação futura, mascarando um erro de qtd real. Correção provável: filtrar `off_prov_sids`
para tipos `buySell` (como `passo6_sids`).
