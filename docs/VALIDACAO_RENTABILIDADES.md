# Validação Rentabilidades

A página **Validação Rentabilidades** (`/validacao-rentabilidades`) calcula a rentabilidade diária
de cada *security* de uma empresa, carteira por carteira, e sinaliza **anomalias** comparando a
rentabilidade do dia contra *thresholds* estatísticos (3-sigma) calculados sobre o histórico.

Backend: [`pages/validacao_rentabilidades.py`](../pages/validacao_rentabilidades.py).
Template: [`templates/validacao_rentabilidades.html`](../templates/validacao_rentabilidades.html).
Thresholds persistidos em `data/rentability_thresholds.json`.

Fonte de dados: `db.processedPosition` (séries de `securities[]` por carteira/data) e
`db.navPackages` (descobre quais carteiras têm dados na empresa/data). Visibilidade por empresa é
aplicada em todas as rotas (`company_visible`); o detalhe resolve a empresa pela carteira
(`resolve_wallet`) antes de liberar o histórico de PU.

---

## Rotas

| Rota | Método | Propósito |
|------|--------|-----------|
| `/validacao-rentabilidades` | GET | Página (lista de empresas visíveis). |
| `/api/validacao-rentabilidades/dates` | GET | Cards das últimas `_NUM_DATES` (10) datas úteis com nº de carteiras que têm `processedPosition`. Params: `companyId`, `endDate?`. |
| `/api/validacao-rentabilidades/securities` | GET | Linhas de rentabilidade da empresa+data. Params: `companyId`, `date`. |
| `/api/validacao-rentabilidades/security-detail` | GET | Posição atual vs. anterior + histórico das últimas 10 datas de um ativo. Params: `walletId`, `securityId`, `date`. |
| `/api/validacao-rentabilidades/calculate-thresholds` | POST | Recalcula thresholds 3-sigma para todos os ativos da empresa. Body: `{companyId, numDays?}` (`numDays` clamp 1–365, default 60). |
| `/api/validacao-rentabilidades/c3-dispersion` | GET | Dispersão entre carteiras dos ativos C3 numa data (outliers). Params: `companyId`, `date`, `sigma?` (float, default 3.0, clamp 0.5–10). |

A página tem **dois modos** (toggle no topo da tabela): **Rentabilidade** (análise temporal,
abaixo) e **Dispersão C3** (análise cross-sectional, ver última seção).

---

## Fórmulas

Para cada ativo, compara-se a posição da data atual com a **posição anterior** (a `processedPosition`
mais recente com `positionDate < date` para aquela carteira).

```
event_per_unit     = eventContribution / quantity          (0 se quantity ausente/zero)
rentabPU           = (pu + event_per_unit) / pu_anterior - 1
saldo_anterior     = pu_anterior * quantity_anterior
rentabContribution = totalContribution / saldo_anterior
```

`rentabPU == None` quando faltam `pu` ou `pu_anterior` (ex.: ativo recém-adquirido sem posição no
dia anterior).

### Thresholds (3-sigma)

Sobre as últimas `numDays` rentabilidades (`rentabPU`) de cada `securityId`:

```
lowerBound = mean - 3 * stdDev
upperBound = mean + 3 * stdDev
```

Ativos com menos de 3 amostras são ignorados. Uma linha é **anomalia** quando
`rentabPU < lowerBound` ou `rentabPU > upperBound`.

---

## Securities B1 (preço de mercado) — tratamento "nível de ativo"

`pricingType == "B1"` é **preço de mercado**: o PU de um ativo B1 numa data é **idêntico em todas as
carteiras** que o detêm. Logo seus dados são de nível de ativo, não de carteira. Isso contrasta com
`pricingType == "C3"` (**curva**), cujo PU é específico por carteira (lookup estrito por `walletId` —
ver `REPETIR_POSICOES.md` / curva).

Conjunto configurável no topo do módulo: `_ASSET_LEVEL_PRICING_TYPES = {"B1"}` (somente B1 por ora;
`closingPrice` e demais tipos permanecem por carteira).

**`/securities`** — securities B1 são **consolidadas em uma única linha por `securityId`**, usando o
**primeiro registro encontrado** (primeira carteira que contém o ativo); as carteiras seguintes são
puladas. As colunas específicas de carteira (Event Contrib., Rentab Contrib.) exibem os valores desse
primeiro registro, e o `walletId` representativo alimenta o modal de detalhe (o histórico de PU é o
mesmo em qualquer carteira para B1). Securities não-B1 continuam com **uma linha por carteira**.

**`/calculate-thresholds`** — cada `(securityId, data)` de um ativo B1 contribui com **um único
retorno**, independentemente de em quantas carteiras o ativo aparece. Sem essa deduplicação, o mesmo
retorno seria empilhado N vezes (N = nº de carteiras), inflando `sampleSize` em N× e **contraindo o
desvio-padrão amostral** → bounds artificialmente estreitos e falsos positivos de anomalia. A marca
de "visto" só é registrada **após** o cálculo bem-sucedido do retorno, para que uma falha de cálculo
não consuma o slot e descarte um retorno válido de outra carteira.

---

## Modo "Dispersão C3" (análise entre carteiras)

Análise **cross-sectional** (segundo modo da página) para validar preços **C3** (curva). Um preço C3
é específico de (security × wallet), então o mesmo ativo pode ter **vários** `rentabPU`/`rentabContrib`
numa data — um por carteira. Esta análise **não usa histórico nem os thresholds persistidos**: compara
os valores **dentro da própria data**.

Conjunto configurável: `_CURVE_PRICING_TYPES = {"C3"}`. Helper `_rentab(sec, former)` (compartilhado
com `/securities`) computa `rentabPU`/`rentabContribution` de cada (carteira × ativo C3) na data.

Para **cada `securityId` C3** (rota `/c3-dispersion`):
1. Agrupa os valores de todas as carteiras; calcula **média e desvio-padrão amostral**
   (`statistics.mean`/`stdev`) de `rentabPU` e de `rentabContribution` (σ exige ≥2 valores).
2. Por carteira: `z = (valor − média) / σ` (0 se σ=0). É **outlier** se `|z| ≥ sigma` em PU **ou** em
   Contrib. (`sigma` vem do param, default 3.0, clamp 0.5–10). A linha sinaliza a carteira que destoa.
3. Ordena ativos com outlier primeiro; dentro do ativo, as carteiras pelo maior `|z|`.

**Limite (σ) ajustável na UI (default 3).** Atenção estatística: com σ **amostral**, o `|z|` máximo de
um ponto é `(n−1)/√n` (n = nº de carteiras com valor). Para `n < ~11`, **nenhum** ponto atinge 3σ —
logo, em ativos com poucas carteiras, reduza o limite (ex.: 2) para que outliers apareçam. Grupos com
`count < 2` aparecem como **"—" (não avaliável)** — não há dispersão a medir.

UI: toggle **Rentabilidade ↔ Dispersão C3** no topo da tabela. No modo C3, o botão "Calcular
Thresholds" some, surge o campo **"Limite (σ)"** e o filtro **"Apenas outliers"**; a tabela mostra uma
linha por carteira (agrupada por ativo, com `N`/média/σ na célula do ativo) e destaca as carteiras
outlier. Clicar numa linha abre o detalhe daquela carteira.
