# Posição Projetada

> Rotina para **projetar a posição** de uma carteira numa data alvo,
> partindo do `processedPosition` mais recente disponível e aplicando
> movimentos conhecidos (compras/vendas, vencimentos, PUs da
> `processedPosition` da data alvo quando existir). O resultado é
> gravado como `unprocessedSecurityPositions` (novo `positionDate`) e
> enviado ao upstream via o mesmo endpoint multipart usado em
> **Exceções**.

> **Estrutura da página (abas):**
> - **Posição projetada** — roster + prévia por carteira. Não há mais
>   barra global de data: cada linha tem seu próprio "Última posição"
>   / "Data alvo" e um botão "Gerar prévia" individual. A prévia roda
>   **uma carteira por vez** para análise individual. Os blocos
>   **Transações** e **Provisões** (CRUD) aparecem **dentro da prévia
>   gerada** — escopados à carteira ativa, com a janela de data
>   pré-preenchida a partir de `[sourceDate+1, targetDate]` (txns —
>   o input `De` é inicializado em `sourceDate+1`, espelhando o
>   intervalo `(sourceDate, targetDate]` usado pela projeção de
>   caixa) e `[sourceDate, targetDate+1ano]` (provisões). Antes da
>   primeira prévia esses blocos não existem — não fazem sentido
>   sem uma carteira ativa.
>   - **Transações** — `db.transactions` escopado por carteira; linhas
>     em vermelho indicam tipos que exigem `securityId` mas estão sem
>     ativo associado. Backend: `/api/beehus/transactions`.
>   - **Provisões** — `db.provisions` escopado por carteira (criar e
>     excluir; o upstream não expõe PATCH). Backend:
>     `/api/beehus/provisions`.
> - **Seleção de Wallets** — gerenciamento dos presets nomeados.
> - **Regras** — documentação read-only do rule chain (em sincronia
>   com o objeto `_RULES` em `pages/repetir_posicoes.py`).

> **Modos de execução:**
> - **"Gerar prévia"** (por carteira) — botão na linha de cada
>   carteira no roster. Substitui o painel de prévia abaixo da tabela
>   pela análise daquela carteira; a operadora então pode "Revisar e
>   confirmar" para enviar ao upstream.
> - **"Executar (sem prévia)"** — *temporariamente desabilitado* até
>   o operador validar o novo pipeline via prévia. O endpoint backend
>   continua funcionando.

---

## Índice

1. [Visão geral do fluxo](#visão-geral-do-fluxo)
2. [Configuração](#configuração)
3. [Rotina diária](#rotina-diária)
4. [Regras de transformação](#regras-de-transformação)
   1. [Preço pela processedPosition do target (`RULE_TARGET_PROCESSED_PRICE`)](#preço-pela-processedposition-do-target-rule_target_processed_price)
   2. [a. Quantidade alterada por transação `buySell`](#a-quantidade-alterada-por-transação-buysell)
   3. [b. Quantidade zerada por vencimento (`maturityDate`)](#b-quantidade-zerada-por-vencimento-maturitydate)
   4. [c. Preço (PU) — fallbacks](#c-preço-pu)
   5. [Caixa (`cashAccounts`)](#caixa-cashaccounts)
   6. [Provisões](#provisões)
5. [Relatório de log (audit trail)](#relatório-de-log-audit-trail)
6. [Relatório de diferenças vs processedPosition](#relatório-de-diferenças-vs-processedposition)
7. [Modelo de dados / endpoints](#modelo-de-dados--endpoints)
8. [Como adicionar uma nova regra](#como-adicionar-uma-nova-regra)
9. [Arquivos relacionados](#arquivos-relacionados)

---

## Visão geral do fluxo

```
                   ┌─────────────────────────────────────────────┐
[Painel de         │ Chip "Repetir" em templates/controlpanel.html │
 Controle]      ─► │   (grupo Lançamentos)                       │
                   │ openRepetirInline(chip) → carrega           │
                   │ /repetir-posicoes inline via                │
                   │ Funcoes._showPage('__repetir__', …)         │
                   │ — não há redirect, apenas um iframe interno │
                   │ no #tool-view. URL sem params (campos       │
                   │ vazios; o operador monta a consulta).       │
                   └─────────────────────┬───────────────────────┘
                                         ▼
       /repetir-posicoes  (página dedicada, mesma shell que /carteira)
                                │
   ┌────────────────────────────┼────────────────────────────┐
   ▼                            ▼                            ▼
1) SELEÇÃO DE WALLETS      2) ROTINA DIÁRIA            3) APLICAR
   cria/edita listas         checkboxes selecionam         • Preview por carteira
   nomeadas de carteiras     quais listas rodar →          • Aceita/Rejeita por
   (transfer-list +          tabela = união dos              security.
   "Salvar como lista...").  walletIds das listas         • Envia upload
                             marcadas.                      multipart .xlsx
                             Filtros: data.                 via upstream.
```

O chip "Repetir" no Painel de Controle **não tem mais modal de
confirmação rápida**: clicar abre direto `/repetir-posicoes` dentro do
mesmo Painel (iframe gerenciado por `Funcoes._showPage`, mesmo mecanismo
de `openCarteira` / `openSettings`). A página inicializa com `#rp-date` e
`#rp-global-target` vazios e **não roda prévia automaticamente** — o
operador escolhe a data, marca as listas e dispara "Gerar prévia"
manualmente. O `runDaily` está ligado ao `onchange` do `#rp-date`, então
a tabela popula assim que o operador escolhe a data.

Deep links com `?date=YYYY-MM-DD&targetDate=YYYY-MM-DD` continuam
suportados: nesse caso `Repetir.init()` pré-preenche os dois inputs,
auto-marca todas as carteiras elegíveis e dispara `runPreview()` —
mesma experiência de um link compartilhado entre operadores. O chip do
Painel de Controle não passa params, então o auto-prévia só acontece
para links externos.

O fluxo abaixo refere-se sempre a **uma única `positionDate` de origem**
(`D`) e à **próxima data alvo** (`D+1`, definida pelo usuário) — análoga ao
modelo de **Exceções > Aplicar exceção**, em que cada execução gera um
único conjunto de `unprocessedSecurityPositions` por carteira.

---

## Configuração

A rotina é composta exclusivamente de **listas nomeadas** de carteiras
(`walletLists[]`). Não existe mais uma "lista ativa" separada — a Rotina
diária roda sobre a **união dos walletIds das listas que o operador
marcar** via checkbox na aba "Rotina diária".

A configuração persiste em **`data/repeat_positions_config.json`** (atômico,
mesmo padrão de `data/config.json`):

```json
{
  "wallets": [],
  "priceRepeatSecurities": [
    {
      "companyId":  "10000000000000",
      "securityId": "67fee83756e1234567890abc",
      "addedAt":    "2026-05-11T13:42:11Z"
    }
  ],
  "walletLists": [
    {
      "name":      "Carteiras HTM",
      "walletIds": ["68bb268b9a9a11e087ee53de", "680a9ce73b2296d86127143f"],
      "addedAt":   "2026-05-12T21:36:45Z"
    },
    {
      "name":      "Cliente XYZ",
      "walletIds": ["680a9ce73b2296d86127143f", "680a9cea3b2296d861271652"],
      "addedAt":   "2026-05-12T22:11:08Z"
    }
  ]
}
```

Campos:

| Campo | Tipo | Descrição |
|---|---|---|
| `wallets[]` | — | **Legado** — não é mais lido. Configs pré-refactor são migradas automaticamente em `_load_config`: `wallets[]` populado + `walletLists[]` vazio → cria uma "Lista padrão" com todos os `walletId`s antigos. Após migração o `wallets[]` permanece no arquivo (audit trail) mas é ignorado. |
| `priceRepeatSecurities[]` | — | Reserva para a regra **C3** (ver §4.3). |
| `walletLists[].name` | `string` | Nome único do preset. Chave de upsert — salvar com nome existente substitui. |
| `walletLists[].walletIds` | `string[]` | IDs de carteiras (`db.wallets._id` como string). A mesma carteira pode aparecer em múltiplas listas — a rotina deduplica na união. |
| `walletLists[].addedAt` | `string` | ISO-8601 do último upsert. |

A UI da aba **"Seleção de Wallets"** usa a **transfer-list** padrão
(`rp-xfer*` CSS — mesmo modelo de `/carteira`). Fluxo:

1. Filtra por empresa para popular "Disponíveis".
2. Move carteiras para "Selecionadas".
3. Clica **"Salvar como lista..."**: o modal pede um nome. Nome novo →
   cria preset; nome existente → sobrescreve (o input já vem
   pré-preenchido quando uma lista foi carregada para edição).
4. Painel **"Listas Salvas"** abaixo da transfer-list lista todos os
   presets. Click no nome carrega a lista no transfer-list para edição;
   `×` exclui o preset.

O botão **"Limpar seleção"** zera o transfer-list mas não toca em
nenhuma lista salva.

Endpoints:

- `GET /api/repetir-posicoes/filters/wallets?companyId=...` — carteiras
  de uma empresa (browsing helper).
- `GET /api/repetir-posicoes/wallet-lists` — retorna os presets com
  **metadata enriquecida** por carteira (`walletName`, `companyId`,
  `companyName`) para que a UI não precise fazer lookups adicionais.
  Shape: `{lists: [{name, addedAt, walletIds[], wallets[{walletId,
  walletName, companyId, companyName}]}]}`. O `company_filter` do
  operador é aplicado **apenas** ao array enriquecido `wallets[]`; o
  `walletIds[]` cru permanece intacto para que edição/save não perca
  silenciosamente ids que o operador atual não vê.
- `POST /api/repetir-posicoes/wallet-lists` — upsert. Body:
  `{name, walletIds[]}`. WalletIds inválidos ou sem visibilidade pelo
  `company_filter` são descartados silenciosamente.
- `DELETE /api/repetir-posicoes/wallet-lists?name=<n>` — remove um
  preset.

> **Nota futura:** quando o "Painel de Controle" passar a expor uma área de
> configuração unificada, esta lista deve migrar para lá. O contrato JSON
> permanece o mesmo — o frontend é que muda de lugar.

---

## Rotina diária

A rotina diária é **company-agnostic** — opera sobre a união dos
walletIds das listas que o operador marcar via checkbox. Não há
seletor de empresa neste tab; a empresa de cada carteira é resolvida
no backend via `db.wallets` e o upload upstream é particionado: **um
arquivo `.xlsx` por empresa** (o endpoint multipart exige `companyId`
no form data, então cross-company numa mesma chamada é fisicamente
impossível). Carteiras da mesma empresa viajam juntas no mesmo
arquivo.

A página `/repetir-posicoes` (na aba **"Rotina diária"**) mostra:

0. **Listas a incluir** — painel de checkboxes (um por preset salvo).
   A página inicia com **todas as listas DESMARCADAS** e **não carrega
   o roster automaticamente** — o operador entra com o foco no bloco
   "Carteiras com divergência (Conciliação NAV)" e só marca uma lista
   quando quer trabalhar a rotina diária. Botões `Selecionar todas` /
   `Limpar` no header. Cada toggle re-dispara `/api/repetir-posicoes/daily`
   com o querystring `?lists=...` atualizado. (Comportamento anterior:
   a tela abria com todas as listas marcadas e o roster cheio.)

1. **Filtros:** apenas `positionDate` (opcional, default = hoje em BRT).
   O filtro restringe a tabela a carteiras com `lastDate <= positionDate`.
2. **Tabela de carteiras (união das listas marcadas):**

   | Coluna | Origem |
   |---|---|
   | Empresa | `db.wallets.companyId` → `db.companies.name` |
   | Carteira | `db.wallets.name` |
   | Última `processedPosition` | input editável (default = maior `positionDate` em `processedPosition` para a carteira). Operador pode sobrescrever para repetir a partir de uma data anterior, ou digitar uma data quando a carteira não tem `processedPosition` (a linha então fica elegível e entra na prévia, com o aviso `sem processedPosition em sourceDate` se o backend nada encontrar) |
   | Data alvo | input editável (default = próximo dia útil após `lastDate`, ou o valor da Data alvo global se preenchida) |
   | ✓ | checkbox para incluir/excluir da execução |

3. **Data alvo global** — input `Data alvo (todas as carteiras)` define a
   mesma `targetDate` para todas as linhas. **Pré-preenchido** com o
   próximo dia útil contado a partir de hoje (BRT), espelhando o cálculo
   do helper `_next_biz_day` no backend; o operador raramente precisa
   tocar nele. Sobrescreve a data sugerida por carteira e permanece
   aplicada após "Atualizar" (a UI re-aplica o valor ao recarregar a
   lista). Operadores ainda podem editar a data linha-a-linha. Limpar o
   campo restaura a `suggestedTarget` (próximo dia útil após o
   `lastDate` de cada carteira).

4. **Botão "Executar (sem prévia)"** — *fluxo padrão*. Chama direto
   `POST /api/repetir-posicoes/apply` (sem passar por preview) com os
   items das carteiras marcadas e **sem `acceptedSecurityIds`** — o
   backend aceita o output completo do rule chain. Apresenta um
   `confirm()` rápido antes do envio. Caixa é incluída automaticamente
   (linha `Caixa = "Sim"` no `.xlsx`, valor = `caixa(source) + Σ
   transactions liquidando em (source, target]`).

   **Botão "Gerar prévia"** (opcional) — chama `POST /api/repetir-posicoes/preview`
   com `{items: [{walletId, sourceDate, targetDate}, ...]}` e devolve,
   por carteira, a lista de securities da posição de origem com as colunas:
   - `original`  → snapshot do processedPosition em `sourceDate`
   - `repetição` → resultado das regras aplicadas para `targetDate`
   - `diff vs target` → diferença `repetição − processedPosition(target)`
                   para quantidade, PU e saldo. **Só renderiza a linha
                   (`qtd:`, `pu:` ou `sld:`) quando o respectivo
                   impacto em $ é ≥ R$ 0,01** — drift puro de
                   quantidade (ex.: 0,000015 unid. × PU 100 ≈
                   R$ 0,0015) deixa de ser sinalizado. O backend
                   entrega esses impactos pré-calculados em
                   `diff.qtyImpact = |qty_d × pu_ref|`,
                   `diff.puImpact = |pu_d × qty_ref|` e
                   `diff.balanceImpact = |bal_d|`, onde
                   `pu_ref`/`qty_ref` são o máximo absoluto entre
                   repetição e target (cobre o cenário em que um dos
                   lados está zerado). Quando os três impactos estão
                   sob R$ 0,01, a célula vira `—`. Âmbar quando a
                   divergência é positiva (repetição > target),
                   vermelho quando negativa. Hover de cada linha
                   mostra o impacto financeiro daquele componente.
                   Linhas onde a security não existe no target
                   processedPosition mostram `—` (italic). Ver
                   §[Relatório de diferenças](#relatório-de-diferenças-vs-processedposition).

                   O **cabeçalho da prévia** mostra o nome da carteira
                   seguido das datas da projeção — **Pos. atual**
                   (`targetDate`) e **Pos. anterior** (`sourceDate`) —
                   para o operador confirmar sobre quais datas a prévia
                   está rodando. É especialmente útil no atalho
                   "Carteiras com divergência (Conciliação NAV)", em que
                   essas datas vêm da grade de conciliação (`targetDate` =
                   data selecionada; `sourceDate` = `formerDate` da
                   carteira).

                   Botão **"Só com divergência"** no cabeçalho da prévia
                   ativa um filtro **100% CSS** (sem rerender, sem
                   refetch dos blocos CRUD): aplica a classe
                   `rp-only-diffs` no `.rp-preview-pane`, e o CSS
                   esconde:
                   - `tr.rp-row-no-diff` — toda linha em que a repetição
                     bate o `processedPosition` em `targetDate` dentro
                     da **tolerância unificada de R$ 0,01 de impacto
                     financeiro** (todos os três componentes —
                     `qtyImpact`, `puImpact`, `balanceImpact` — abaixo
                     de R$ 0,01). A marcação é feita por
                     `_rowClass(row, walletHasTarget)` em tempo de
                     render — quando a carteira não tem target
                     (`walletHasTarget=false`), nenhuma linha é marcada
                     como "no-diff" (senão o filtro esconderia tudo).
                   - `tr.rp-row-cash`, `tr.rp-row-provision` — linhas de
                     contexto somem junto, porque o filtro é sobre
                     "ativos com divergência" e elas não são ativos.

                   **Coloração das linhas** dirigida por divergência
                   contra o `processedPosition` em `targetDate`:
                   - Linhas **sem divergência** (`rp-row-no-diff`) ficam
                     sem cor de fundo — leitura limpa.
                   - Linhas **com divergência** (`rp-row-has-diff`,
                     mesmo critério do filtro acima) ficam amarelas
                     (`#fef9c3`).
                   - As classes antigas (`rp-row-changed`,
                     `rp-row-matured`, `rp-row-new`) **deixaram de pintar
                     fundo** — eram baseadas em "rep mudou em relação ao
                     source", que é ortogonal a "diverge do target". A
                     marca `rp-row-matured` ainda escurece o texto e
                     `rp-row-qty-mismatch` mantém a barrinha vermelha à
                     esquerda como acento.
   - `securityId` → coluna **monoespaçada** com o `securityId` interno
                   (ObjectId/UUID). Útil para auditoria/debug. **Oculta
                   por padrão** (`_DEFAULT_HIDDEN_COLUMNS` inclui
                   `securityId` no backend e no fallback do frontend);
                   re-exibir via botão "Colunas".
   - `unprocessedId` → coluna **monoespaçada** com o
                   `unprocessedSecurityPositions.securities[].unprocessedId`
                   correspondente a cada security, traduzido via
                   `securityMappings`. É exatamente o valor que será
                   gravado na coluna `Ativo` do `.xlsx` enviado ao
                   upstream (ver §3.7). Vazio (`—`) quando a carteira
                   não tem `unprocessedSecurityPositions` em `sourceDate`
                   nem em datas anteriores — nesse caso o upload faz
                   fallback para o `beehusName`.
   - `offset`    → `D+N`, onde `N = subscriptionSettlementDays −
                   subscriptionNavDays` (mostrado em uma única forma
                   quando subscription/redemption são iguais). Quando
                   divergem, mostra `sub D+N / res D+M` em fonte menor.
                   Tooltip detalha os dois valores. Fonte:
                   `db.securities.subscriptionSettlementDays/NavDays` e
                   `redemptionSettlementDays/NavDays`, normalizados
                   por `_security_meta` (mesma fórmula de
                   `transaction_security_classifier._fetch_offsets`).
   - `Δ Qtd (orig − target)` → diferença direta entre `original.quantity`
                   e `targetProcessed.quantity` — independente de
                   transação. Positivo = posição diminuiu entre source
                   e target (resgate); negativo = posição aumentou
                   (compra). `0` em verde quando `|Δ| < 1e-6`; `—`
                   quando não há entrada no target.
   - `transação` → lista das transações `buySell` (em `liquidationDate ==
                   targetDate`) que justificam a mudança de quantidade —
                   cada item mostra **só o saldo** (mono); a descrição
                   da transação está no `title` (tooltip). Vazio quando
                   a regra `a` não disparou.
   - `flags`     → motivos da mudança (`targetPrice`, `maturity`,
                   `priceUnchanged`, `curvaPrice`, `b1Price`,
                   `missingB1Price`, ...). `buySell` **não aparece**
                   aqui — a coluna "Transação" já carrega esse sinal,
                   e a presença do badge era ruído visual.
   - `aceito`    → checkbox (default `true`).

   No cabeçalho do bloco de cada carteira:
   - **Provisões** (chip âmbar): soma de `provisions` cuja janela
     `[initialDate, liquidationDate)` cobre `targetDate` (mesma regra
     do **Painel de Controle > Carteira**). Display-only.
   - **vs target**: resumo agregado das diferenças (`matched`,
     `diverged`, `missingInTarget`, `missingInRepetition`, `Δ saldo`).
     Verde quando total ≈ 0, âmbar/vermelho de acordo com o sinal.

   **Marcador B1:** linhas cujo `PU repetição` veio do botão "Incluir
   preços B1" (ver §3.6) exibem o valor envolto numa pílula verde com
   tag "B1" — facilita auditoria visual antes da confirmação. A flag
   `b1Price` permanece como rótulo textual na coluna de flags; quando
   o ativo não tem cotação B1 publicada para a `targetDate`, a flag
   é `missingB1Price` (sem alteração no PU).

5. **Modal de confirmação** — recapitula linha-a-linha o que será gravado e
   exige confirmação do usuário antes de chamar
   `POST /api/repetir-posicoes/apply`.

6. **PUs de curva automáticos** — não há mais botão "Incluir preços na
   curva". A própria `/api/repetir-posicoes/preview` carrega o template
   global de `/precificacao`, roda `calculate_curva()` e aplica o PU
   resultante como **primeira** regra do chain (`RULE_CURVA_PRICE`),
   antes do cálculo de quantidade. Linhas com match recebem `pu` da
   curva + flag `curvaPrice` + destaque visual `CURVA` na célula. Como
   a substituição ocorre antes de `RULE_BUYSELL_QTY`, o fallback
   `balance / pu` dessa regra usa o PU correto.

   **Botão "Incluir preços B1"** (mecanismo separado, ainda manual,
   posicionado **ao lado do botão "Gerar prévia"**) — busca em
   `db.securityPrices.historyPrice[]` o PU publicado pelo sistema na
   `targetDate` (match exato em `historyPrice.date`, sem carry-forward
   — uma data faltante no feed upstream precisa aparecer como "sem
   cotação", não ser silenciosamente coberta pela cotação anterior).
   "B1" é um rótulo da UI para "PU do sistema na data alvo", **não**
   um discriminador no banco: o upstream grava apenas `date`, `value`
   e `adjustedQuantity` por entrada, sem campo `type`. Substitui o
   `repetition.pu` da linha, recalcula `balance` e adiciona flag
   `b1Price`. Ativos sem entrada exata para a `targetDate` recebem
   `missingB1Price` (sem alteração no PU). PUs ajustados viajam para
   o `/apply` via `puOverrides`. Esse botão **não conflita** com o
   curva: rows já marcadas com `curvaPrice` são puladas e mantêm o
   PU da curva (a regra de curva representa um cálculo HTM por lote
   — vence sobre a cotação genérica B1).

7. **Aplicação** — as carteiras aceitas são **agrupadas por empresa** e cada
   grupo gera **um arquivo Excel** (`Data, Carteira, Ativo, Quant, PU,
   SaldoBruto, Caixa, Moeda`), enviado em uma chamada multipart via
   `upload_unprocessed_security_positions_file(...)` (ver
   [`beehus_api/positions.py`](../beehus_api/positions.py)). N empresas =
   N uploads (limitação física do endpoint upstream — `companyId` é
   parte do form data). Dentro de cada arquivo, carteiras com
   `targetDate` diferentes convivem (o upstream roteia por `Carteira`
   e `Data`).

   A coluna `Ativo` do `.xlsx` carrega o `unprocessedId` da row (mesmo
   valor exibido na coluna **UnprocessedId** da prévia — espelha a
   convenção de `/excecoes`). Quando a row não tem `unprocessedId`
   resolvido (carteira sem snapshot em `sourceDate` nem em datas
   anteriores, ou ausência da entrada correspondente em
   `securityMappings`), o builder faz fallback para o `beehusName` do
   security — o operador vê o "—" na prévia antes de confirmar e pode
   ajustar a `sourceDate` ou completar o mapeamento.

---

## Regras de transformação

> **Princípio:** a posição de **origem** é o `processedPosition.securities[]`
> do dia `sourceDate` (mais recente disponível). A posição de **destino** é
> derivada item-a-item, com `targetDate = sourceDate + 1 dia útil` por
> default. Cada regra é independente e pode ser ligada/desligada por
> security (via UI futura).

> **Filtro de origem** (antes das regras): linhas do `processedPosition` cuja
> `quantity` no `sourceDate` seja `0` (ou nula) são **descartadas** — são
> posições já liquidadas/vencidas em datas anteriores e não devem ser
> repetidas. Ativos *novos* (que aparecem apenas em `transactions` da
> `targetDate` e não existiam no `sourceDate`) continuam sendo incluídos
> como `isNew: true`, porque representam compras na data alvo.

Cada regra implementa a interface mínima:

```python
def apply(row_in, ctx) -> row_out_or_None
# row_in  : {"securityId", "quantity", "pu", "balance", "pricingType", ...}
# ctx     : {"walletId", "sourceDate", "targetDate", "companyId",
#            "transactions_by_security", "security_meta_by_id", "config"}
# row_out : mesma estrutura — ou None para remover a linha.
```

As regras são chamadas em **ordem fixa**, de modo que cada uma só vê o
estado já processado pelas anteriores. A ordem atual é:

1. `RULE_TARGET_PROCESSED_PRICE` — primeira; PU autoritativo da
   `processedPosition` em `targetDate`.
2. `RULE_CURVA_PRICE` — fallback de PU pela curva (só roda quando
   `targetPrice` não marcou a linha).
3. `RULE_BUYSELL_QTY` — quantidade ajustada por transações.
4. `RULE_MATURITY` — zera quantidade no vencimento.
5. `RULE_PRICE` — tagging final (`priceUnchanged`, `pendingPrice`).

### Preço pela processedPosition do target (`RULE_TARGET_PROCESSED_PRICE`)

| Identificador | `RULE_TARGET_PROCESSED_PRICE` |
|---|---|
| Ordem | **Primeira** regra do chain — define o PU autoritativo antes de qualquer outra regra. |
| Fonte | `db.processedPosition.find_one({"walletId": <wid>, "positionDate": <targetDate>})` (match exato — `_target_processed_position` em [`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py)). |
| Disparo | A security está em `processedPosition.securities[]` no `targetDate` com `pu` não-nulo. |
| Ação | Substitui `row.pu` pelo PU do target, recalcula `row.balance = pu × quantity` (a quantidade ainda é a herdada do source — `_rule_buysell_qty` ajusta depois), e adiciona flag `targetPrice`. |
| Visual | Pílula fuchsia + tag `TARGET` na célula "PU repetição". Vence sobre `CURVA` e `B1`. |
| Falhas | Sem doc no target ou security ausente → regra é no-op silenciosa; o chain segue com `RULE_CURVA_PRICE` (e depois o PU original). |
| Observações | Este é o novo padrão recomendado para o fluxo "Executar sem prévia": quando o upstream já produziu `processedPosition` para o `targetDate`, usar esse PU é mais correto que recomputar pela curva. |

### a. Quantidade alterada por transação `buySell`

| Identificador | `RULE_BUYSELL_QTY` |
|---|---|
| Disparo | Existe ≥ 1 transação com `beehusTransactionType == "buySell"` cuja `liquidationDate == targetDate + offset(security)` e `walletId == wallet` e `securityId == row.securityId`. |
| Ação | `new_quantity = row.quantity + Σ delta_t`, onde para cada transação `t`:<br>• se `t.quantity` existe → `delta_t = -t.quantity` (sinal invertido);<br>• senão, se `t.balance` existe e há PU disponível → `delta_t = -t.balance / pu_for_div`;<br>• senão → ignora.<br>`new_balance = new_quantity × row.pu` (PU corrente — pode ser 0 quando o target zerou a PU, refletindo posição liquidada). |
| PU do divisor (`pu_for_div`) | Prefere `row.pu` corrente (já pode ter sido sobrescrita por `RULE_TARGET_PROCESSED_PRICE` ou `RULE_CURVA_PRICE`). Quando a PU corrente está em **0** — caso típico de security resgatada/vencida na data alvo, em que o `processedPosition(target)` traz `pu = 0` — cai na PU **da source** (preservada em `_sourcePu` no momento da seed do row). Sem esse fallback, transações com apenas `balance` (RESGATE, RECOMPRA COMPROMISSADA) seriam silenciosamente ignoradas (`balance / 0` é descartado pelo `if pu_for_div`), a quantidade ficaria igual à da source, e a coluna `Diff vs target` mostraria a posição inteira como divergência. |
| Convenção de sinal | `transactions.balance` e `transactions.quantity` estão em **convenção caixa**: negativo = saída de dinheiro/quantidade (compra do ponto de vista do comprador), positivo = entrada (venda). O efeito na posição é o oposto, daí a inversão de sinal nas duas vias. |
| Flag | `buySell` (com a `Σbalance` exibida no preview para auditoria). |
| Observações | • O `offset` é uma constante por security — atualmente `0` para renda variável e renda fixa convencional, mas o campo está reservado em `security_meta` para ajustes futuros (`subscriptionSettlementDays` / `redemptionSettlementDays`).<br>• `quantity == 0` após a regra → a linha é **mantida com zero** (não removida) para que o upstream registre a baixa explicitamente. Vencimentos é que apagam a linha (§b).<br>• A quantidade derivada por esta regra é definitiva: overrides de PU aplicados **depois** (botões HTM/B1 ou Curva) recalculam apenas o `balance` exibido — não revisam a quantidade. |

### b. Quantidade zerada por vencimento (`maturityDate`)

| Identificador | `RULE_MATURITY` |
|---|---|
| Disparo | `db.securities[row.securityId].maturityDate == targetDate` (comparação string `YYYY-MM-DD`). |
| Ação | `quantity = 0`, `balance = 0`. A linha é mantida no payload (consistente com a regra `a`) para que o upstream registre a baixa. |
| Flag | `maturity` |
| Observações | • `maturityDate == None` → regra é no-op.<br>• Combina com regra `a`: se houve compra **e** vencimento na mesma data alvo, prevalece o vencimento (zera). |

### c1. Preço pela curva (HTM)

| Identificador | `RULE_CURVA_PRICE` |
|---|---|
| Ordem | **Primeira** regra do chain — roda **antes** de `RULE_BUYSELL_QTY` para que o fallback `balance / pu` da regra `a` use o PU da curva quando aplicável. |
| Fonte | Mapa precomputado `{walletId\|securityId\|targetDate → pu}` montado uma vez por request via `_compute_curva_pu_map(target_dates, wallet_ids)`, que carrega o template global de `/precificacao` (ver `data/precificacao_lists.json`) e roda `calculate_curva(securities)` (função pura extraída de [`pages/precificacao.py`](../pages/precificacao.py)). |
| Escopo (perf) | O motor da curva roda **apenas sobre as securities das wallets desta rodada** (`wallet_ids` passado por `/preview` e `/apply`). Como o lookup é estrito por `walletId`, entradas de outras wallets jamais casariam — filtrá-las antes do cálculo é semanticamente idêntico a filtrar depois, mas evita recalcular a curva inteira do template a cada "Gerar prévia" (a prévia roda **uma wallet por vez**). Chamar `_compute_curva_pu_map(dates)` sem `wallet_ids` preserva o comportamento antigo (template inteiro). |
| Lookup | **Estrito por wallet**: consulta apenas a chave `<walletId>\|<securityId>\|<targetDate>`. Não há fallback agnóstico — entradas do template sem `walletId` preenchido são ignoradas (e logadas), e wallets que não estejam explicitamente listadas no template para aquele ativo não recebem PU da curva. Isso vale para todos os `calcType` (`pos_fixado`, `pre_fixado_curva`, `inflacao_curva`) e evita contaminar wallets distintas com o PU de outra. |
| Ação | Substitui `row.pu` pelo PU da curva, recalcula `row.balance = pu × quantity` (a quantidade ainda é a herdada do source — `_rule_buysell_qty` ajusta depois), e adiciona flag `curvaPrice`. |
| Visual | A célula "PU repetição" é destacada com uma pílula verde + tag `CURVA` (CSS `rp-curva-cell` / `rp-curva-tag`). |
| Falhas | Template vazio ou erro no engine → mapa vazio → regra é no-op silenciosa. A prévia continua usando os PUs de origem. |

### c2. Preço (fallback / tagging)

| Identificador | `RULE_PRICE` |
|---|---|
| Disparo | Avalia `row.pricingType` da posição original, **apenas** se `RULE_CURVA_PRICE` não tiver marcado a linha (sem `curvaPrice` em flags). |
| Ação | • `pricingType == "B1"` → mantém o `pu` original na prévia, com flag `priceUnchanged`. O **botão "Incluir preços B1"** (ao lado de "Gerar prévia") substitui esse PU pelo valor publicado em `db.securityPrices.historyPrice[]` com `type == "B1"` e `date == targetDate` (ver §3.6 e o endpoint `/api/repetir-posicoes/b1-prices`).<br>• `pricingType == "C3"` → marca a linha como `pendingPrice`.<br>• Outros tipos → flag `priceUnchanged`. |
| Flags possíveis | `priceUnchanged`, `pendingPrice` (emitidas pela regra). Quando o botão "Incluir preços B1" troca o PU, ele **adiciona** `b1Price` à linha (ou `missingB1Price` quando não há cotação publicada). O override viaja via `puOverrides` para `/apply`. |
| Observações | A interface `apply` está pronta para novos sub-tipos sem alterar as regras `a` e `b`. |

### Caixa (`cashAccounts`)

Além da posição de securities, a rotina espelha o **caixa** do `sourceDate`
no `targetDate` da seguinte maneira:

1. `formerCash = sum_cash(walletId, sourceDate)` (helper já existente em
   [`db.py`](../db.py)).
2. `delta = Σ transactions.balance` para as transações (com
   `trashed != True`) cuja `liquidationDate` cai em
   `(sourceDate, targetDate]`. Inclui `buySell`, rendimentos, taxas,
   depósitos/retiradas — espelhando o raciocínio de **Painel de
   Controle > Carteira**. **Exceção:** transações do tipo
   `securityTransfer` são **desconsideradas** desta soma (pedido da
   operadora — a transferência de ativo move `balance` mas não é fluxo
   de caixa real do investidor para o SALDO PROJETADO do Caixa). O
   sinal já está embutido em `transactions.balance`. Acumulado em
   `all_cash_delta` por `_load_window_transactions`
   ([`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py)).
3. `newCash = formerCash + delta` (projeção).
4. `targetCash = sum_cash(walletId, targetDate)` — caixa **já gravado
   em `cashAccounts` na data alvo** (snapshot do upstream, não
   projeção). É `None` quando não há entrada de cash para a wallet em
   `targetDate`.
5. `targetDelta = newCash − targetCash` (Δ da projeção contra o
   upstream). É `None` quando algum dos dois é `None`.

A prévia mostra essas duas novas colunas na linha de **Caixa** (que
agora é a **primeira linha da tabela**, antes das securities) — `Saldo
target` exibe `targetCash` e `Diff vs target` exibe `targetDelta`
(amarelo na linha inteira se |Δ| ≥ 0,01).

A partir desta versão, o caixa **é uploaded** junto com as positions:
cada carteira recebe uma linha `Caixa = "Sim"` com `Ativo = "Caixa"`,
`SaldoBruto = newCash` (mesmo formato usado por `/carteira` e
`/excecoes`). O envio pode ser desligado por chamada via
`body.includeCash = false` em `/apply`.

### Provisões

A `processedPosition` da target é **observada** mas não-uploaded.
Provisões representam fluxos de caixa esperados que ainda não
liquidaram (mesmo conceito de **Painel de Controle > Carteira**) e
servem apenas de contexto visual:

```
db.provisions.find({
  companyId, walletId,
  initialDate     <= targetDate,
  liquidationDate >  targetDate,
  trashed != True
})
```

A prévia expõe a **lista detalhada** (`provisionsList[]`) — uma
entrada por provisão com `id`, `description`, `balance`,
`initialDate`, `liquidationDate`, `kind`, `securityId`. O total
agregado (`provisions`) continua disponível no header como chip
âmbar. Implementação em `_provisions_detail` / `_provisions_sum`.

**Coluna por ativo (`Provisões`):** cada row de security carrega
`provisionsBalance` (somatório das provisões cujo `securityId` casa
com aquele ativo) e `provisionsList` (lista filtrada pra o tooltip).
Provisões **sem** `securityId` (taxas wallet-level, por exemplo) só
aparecem nas rows display-only abaixo da tabela e no chip do header
— não entram em nenhum row de security.

A UI renderiza **uma linha por provisão** no fim da tabela da
prévia (banda âmbar à esquerda). A linha de **Caixa** (banda verde),
posicionada no **topo da tabela**, mostra `cashAccounts(source) →
newCash` em `Saldo original`/`Saldo repetição` e
`cashAccounts(target)` em `Saldo target`, com Δ na coluna `Diff vs
target`. Ambas (Caixa e Provisões) são linhas display-only (sem
checkbox, sem checkbox de aceite).

#### Provisões esperadas (`expectedProvisions[]`)

Quando o `processedPosition` da data alvo registra um Δqty
(`target.quantity ≠ source.quantity`) para um security mas **não
há buySell** na janela `(source, target]` **nem provisão** cobrindo
`target_date`, o sistema infere que a operação aconteceu no
upstream mas o caixa ainda não foi movimentado. Dois efeitos:

1. **Override de `rep.quantity`** — o rule chain é seguido por um
   passo extra que adota `rep.quantity = target.quantity` (e
   recalcula `rep.balance = qty × pu`). Flag `amtDiffOverride` é
   adicionada à row pra rastreabilidade. Sem o override, a projeção
   ficaria travada em `source.quantity` e a row apareceria como
   divergência de qty na prévia.
2. **Sugestão de provisão pendente** — uma entrada é emitida em
   `wallet.expectedProvisions[]` com os campos:

   | Campo | Valor |
   |---|---|
   | `initialDate` | `target_date` |
   | `liquidationDate` | `target_date + offset` (dias úteis, Mon-Fri) |
   | `balance` | `executionPrice × amountDifference × (−1)` |
   | `description` | `"Provisão de ajuste por diferença na quantidade do ativo {beehusName}"` |
   | `provisionType` | `"buySell"` |
   | `provisionSource` | `"amountDifference"` |
   | `direction` | `"subscription"` (Δqty > 0) ou `"redemption"` (Δqty < 0) |
   | `offset` | `subscriptionOffset` ou `redemptionOffset` (de `db.securities`) |

   `executionPrice` vem do `processedPosition.securities[].executionPrice`
   na data alvo (mesma fonte usada pelo intraday contribution).

   A UI renderiza essas provisões num sub-bloco **"Pendentes"** com
   banda vermelha **acima** da listagem CRUD em "Provisões da
   carteira", e o ativo correspondente exibe uma pill `Pendente:
   R$ X` na coluna `Provisões` da tabela de securities.

Sem o override, a operadora veria divergências fantasmas em toda
prévia em que o caixa estivesse atrasado. Com ele, a projeção bate
com a posição e o operador só precisa criar as provisões listadas
no sub-bloco "Pendentes" antes de aplicar.

### Blocos NAV (Anterior / Projetada / Atual)

Logo abaixo do header da prévia, **três cards lado a lado** mostram
`nav`, `navPerShare` e `amount` da carteira em três momentos:

| Bloco | Fonte | Campos extras |
|---|---|---|
| **Anterior**  | `db.navPackages` em `sourceDate` (snapshot upstream) | — |
| **Projetada** | Calculado em tempo de prévia                         | `inAndOutFlows` |
| **Atual**     | `db.navPackages` em `targetDate` (snapshot upstream) | — |

**Cálculo da Projetada:**

```
nav            = Σ saldos(rep) + caixa(target) + provisões(target)
navPerShare    = (NAV_projetada + inAndOutFlows) / amount_anterior
amount         = nav / navPerShare
```

`inAndOutFlows` = soma de `transactions.balance` em
`(sourceDate, targetDate]` para os tipos
`_TXN_TYPES_IN_OUT_FLOWS = {withdrawalDeposit,
withdrawalDepositAdjustment, securityTransfer, taxes}`. Sinal
preservado do upstream (negativo = saída = aporte de capital;
positivo = entrada = resgate). Somar `inAndOutFlows` ao
`NAV_projetada` neutraliza o efeito de fluxo (o NAV já inclui o
fluxo como variação de caixa) e isola o componente de preço — o
que o `navPerShare` deve refletir.

Quando o `db.navPackages` não tem entrada para uma data (`Anterior` ou
`Atual` = `null`), o card mostra "sem navPackage" em italic. Quando o
denominador é zero (`amount_anterior == 0` ou
`navPerShare == 0`), o cálculo da Projetada cai pra `null` no campo
afetado e a célula vira `—`. Implementação em `_nav_package_for` +
`_in_and_out_flows` em [`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py).

**Métricas de contribuição e GAP** (em `Projetada` e `Atual` —
fórmulas 4–10 + GAP do [`CONCILIACAO_RECALCULO.md`](CONCILIACAO_RECALCULO.md)).
Cada bloco carrega:

| Campo | Descrição |
|---|---|
| `securitiesContribution` | `Σ rows[].contributionProjected.total` (ou `contributionActual` para o card Atual). |
| `walletContribution` | `Σ transactions.balance` em `(source, target]` para `{gainsExpenses, rebate, contributionAdjustment, other}`. Não atrelado a security. |
| `totalContribution` | `securitiesContribution + walletContribution`. |
| `returnNavPerShare` | Projetada: `navPerShare_proj / navPerShare_anterior − 1`. Atual: valor já gravado em `navPackages.returnNavPerShare`. |
| `returnContribution` | Projetada: `totalContribution / NAV_anterior`. Atual: `navPackages.returnContribution` (canônico do upstream). |
| `gapPct` | `returnNavPerShare − returnContribution`. |
| `gapCash` | `gapPct × NAV_anterior`. |

**Por security** (no `rows[]` da resposta):

| Campo | Descrição |
|---|---|
| `contributionProjected` | `{daily, intraday, event, total}` calculado com `rep.pu` (PU projetado) e `targetProcessed.quantity` (qty pós-trade real). |
| `contributionActual`    | Mesmo cálculo usando `targetProcessed.{quantity, pu}`. `null` quando a security não existe na posição atual. |

> **Quantity no intraday.** A parcela `intraday = (q − fq) × (PU − ep)`
> representa o trade efetivamente executado na janela. Esse trade é
> um fato imutável do upstream — não depende de o `rep.quantity` ter
> sido movido por nenhuma regra da projeção. Por isso usamos
> `targetProcessed.quantity` (qty real pós-trade) nas DUAS
> contribuições; usar `rep.quantity` aqui faria o `intraday` zerar
> sempre que a projeção não tivesse buySell pra mover a posição
> (caso típico: resgate sem transaction de trade no banco), quebrando
> a conservação `daily + intraday ≈ 0` em resgates ao
> `executionPrice`. Fallback: se `targetProcessed` não existe, usa
> `rep.quantity`.

Fórmulas por security (mesmas do recálculo):

```
daily    = formerQuantity × (PU − formerPU)
intraday = (quantity − formerQuantity) × (PU − executionPrice)
event    = couponAmort + dividendPerShare × formerQuantity
total    = daily + intraday + event
```

`executionPrice` é lido direto do `db.processedPosition.securities[]`
na **data alvo** — é o preço de execução gravado pelo motor de cota
upstream para a security naquele dia. Quando o security não tem
entrada no processedPosition da data alvo (ainda não rodou,
resgatado, etc.) ou quando o campo está vazio/nulo, o intraday cai
em `PU − PU = 0`. O mesmo `executionPrice` alimenta os dois
cenários (`contributionProjected` e `contributionActual`) — a Δqty
source→target reflete o mesmo trade nos dois lados.

`couponAmort` vem de `db.transactions` com tipo `coupon` ou
`amortization` em `(source, target]`. `dividendPerShare` vem de
`db.securityEvents` com `eventType ∈ {cashDividend,
interestOnEquity}` e `operationDate = targetDate`. Implementação:
`_target_processed_position` (lê `executionPrice`),
`_coupon_amort_by_sid`, `_dividend_events_by_sid`,
`_security_contribution` em
[`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py).

A UI renderiza duas colunas extras na tabela de posições
(`contribProjected`, `contribActual`) e mostra o detalhamento
(`daily / intraday / event / total`) no tooltip de cada célula.

---

## Relatório de log (audit trail)

Tanto `/preview` quanto `/apply` geram um **relatório de log**
persistido em `data/repeat_positions_logs/<runId>.json` e devolvido
inline na resposta da chamada (`diffReport`). Diferenciação pelo
campo `mode` e pelo prefixo do `runId`:

| Origem | `mode` | `runId` | `uploaded` | `uploads[]` |
|---|---|---|---|---|
| `/api/repetir-posicoes/preview` | `"preview"` | `preview_<ts>_<uuid>` | `false` | `[]` (sem envio) |
| `/api/repetir-posicoes/apply`   | (ausente)   | `repeat_<ts>_<uuid>`  | `true`/`false` (envio ok ou falhou) | preenchido |

A persistência do log de prévia foi adicionada a pedido do operador:
"checar a geração de log; apareceu 'nenhum log encontrado'". Quando
o operador só roda prévia (sem aplicar), o arquivo já fica no disco
e aparece na listagem do botão **"Log"** sem precisar de uma rodada
de `/apply` primeiro. O implementador é `_build_preview_log_report`
(empacota o mesmo formato de `/apply`) + `_persist_diff_log` (única
gravação compartilhada). `_RUN_ID_RE` cobre os dois prefixos porque
ambos só usam `[A-Za-z0-9_\-]`.

Conteúdo do relatório:

```json
{
  "runId":     "repeat_20260514_201500_a1b2c3d4",
  "createdAt": "2026-05-14T20:15:00Z",
  "uploaded":  true,
  "totalRows": 27,
  "uploads": [
    {"companyId": "12312312312312", "companyName": "Cliente A",
     "status": "ok", "rows": 18, "filename": "repeat_..._20260514201500.xlsx"}
  ],
  "totals": {
    "wallets":             3,
    "withTarget":          2,
    "matched":             8,
    "diverged":            2,
    "missingInTarget":     1,
    "missingInRepetition": 0,
    "totalBalanceDiff":    123.45
  },
  "wallets": [
    {
      "walletId":   "...",
      "walletName": "Fundo X",
      "companyId":  "...",
      "companyName":"Cliente A",
      "sourceDate": "2026-05-13",
      "targetDate": "2026-05-14",
      "currencyId": "BRL",
      "cash":       {"former": 1000.0, "delta": -500.0, "new": 500.0, "target": 510.0, "targetDelta": -10.0},
      "provisions": 4500.0,
      "summary":    {"matched": 5, "diverged": 1, "missingInTarget": 0,
                     "missingInRepetition": 0, "totalBalanceDiff": 10.0,
                     "hasTarget": true},
      "cashStatus":         "diverge",
      "cashDetail":         {"former": 1000.0, "new": 500.0,
                             "target": 510.0, "targetDelta": -10.0},
      "differencesCount":   1,
      "orphanCount":        2,
      "orphanTransactions": [
        {
          "id": "...", "type": "buySell",
          "securityId": "...", "securityName": "LCA BNDES",
          "balance": -8371.62, "quantity": null,
          "liquidationDate": "2026-05-06",
          "offset": 2, "direction": "subscription",
          "problem": "sem provisão ativa (offset=+2)"
        }
      ],
      "differences": [
        {
          "kind": "diverged",
          "securityId":    "...",
          "securityName":  "BBDC4",
          "unprocessedId": "BBDC4",
          "repetition":      {"quantity": 1000, "pu": 102.5,  "balance": 102500.0},
          "targetProcessed": {"quantity": 1000, "pu": 102.4,  "balance": 102400.0},
          "diff":            {"quantity": 0,    "pu": 0.10,   "balance": 100.0},
          "flags":           ["buySell", "targetPrice"]
        }
      ]
    }
  ]
}
```

**Indicadores resumidos por carteira** (no topo do bloco; pedidos pelo
operador):

| Campo | Significado |
|---|---|
| `cashStatus` | `"ok"` quando `|cashDetail.targetDelta| < R$ 0,01`; `"diverge"` quando |Δ| ≥ R$ 0,01; `"noTarget"` quando `cashAccounts(targetDate)` está vazio (`cash.target == null`). |
| `cashDetail` | Snapshot dos números de caixa (`former`, `new`, `target`, `targetDelta`) — duplica os campos já em `cash` pra leitura rápida. |
| `differencesCount` | Quantidade de linhas em `differences[]`. Mesmo critério da prévia: impacto em $ ≥ R$ 0,01. |
| `orphanCount` | Quantidade de transactions órfãs em `(sourceDate, targetDate]`. |
| `orphanTransactions[]` | Lista detalhada das órfãs (ver detecção abaixo). |

**Detecção de transactions órfãs** (`_find_orphan_transactions`):
para cada transação cujo `beehusTransactionType` está em
`_TXN_TYPES_REQUIRING_SECURITY` (todos os tipos que exigem ativo —
`amortization`, `buySell`, `coupon`, `dividend`,
`dividendOnboarding`, `interestOnEquity`, `maturity`,
`securityContributionAdjustment`, `securityTransfer`; **`taxes`
foi removido** pois taxas são genéricas e não exigem ativo).

A régua **bifurca por tipo** de transação:

#### Trade events (`buySell`, `securityTransfer`, `maturity`, `securityContributionAdjustment`)
Tipos que **movem `quantity`**. Régua em duas etapas:

1. **amountDifference entre source e target.** Compara
   `qty(target, sid)` com `qty(source, sid)`. Se a diferença for
   `≥ 1e-6`, a transação está explicada por um movimento real do
   ativo na janela → **não-órfã**. Esse é o sinal primário —
   substitui a checagem antiga "liq vs liq−1", que falhava em fim
   de semana e em movimentos em datas intermediárias.
2. **Provisão ativa em `target_date`** —
   `initialDate ≤ target_date  AND  liquidationDate > target_date`
   em `db.provisions`, escopado por `(walletId, securityId)`. **Mesma
   régua** usada pela lista/total de provisões da prévia
   (`_provisions_detail`); antes o gating do órfã usava
   `init ≤ txn.liq < liq`, duas réguas convivendo. Unificado a
   pedido do operador. Se houver provisão cobrindo `target_date` →
   não-órfã.
3. Caso contrário → órfã. `problem` distingue dois cenários:
   - `"ativo ausente das posições source e target"`
   - `"qty inalterada entre source e target (X → Y) e sem provisão"`

#### Cash events (`coupon`, `amortization`, `dividend`, `dividendOnboarding`, `interestOnEquity`)
Tipos que movem **apenas caixa** (não `quantity`). A régua de
Δqty não se aplica — qty inalterada é o comportamento **esperado**
para cupom/dividendo. Em vez disso, a régua é a **presença do
security**:

- Se `qty(source) > 0` ou `qty(target) > 0` → security esteve ativo
  em alguma das datas → **não-órfã** (evento esperado).
- Se ambas zero → cash event sem ativo correspondente → **órfã** com
  `problem = "cash event sem ativo correspondente em source nem target"`.

Adicionalmente, essas transações **aparecem em `row.txns`** da linha
do security correspondente (coluna "Transação" da prévia), populadas
por `_cash_events_by_security`. Operador vê coupon/dividend ao lado
das buySells, no mesmo lugar.

Transações sem `securityId` nos tipos que exigem caem como órfãs
com `problem = "sem securityId"` (cadastro upstream precisa de
ajuste).

`direction` (`subscription`/`redemption`) e `offset = settlement −
NAV` continuam expostos no payload das órfãs para diagnóstico, mas
**não fazem parte do gating** — num horizonte amplo como
`(source, target]`, o movimento pode aparecer em qualquer NAV
intermediário, e o sinal mais confiável é o agregado source →
target.

**Mapa `amountDifferenceBySecurityId`** no payload da prévia:
`{securityId → Δqty}` (= `qty(target) − qty(source)`). Computado
junto com a montagem de rows e exposto sem agregação. A UI usa pra
renderizar a coluna **"Δ Qtd posição"** no bloco Transações (verde
com sinal quando há movimento, "0" verde quando bate, dash quando a
transação não tem `securityId` ou o ativo não aparece nas posições).
Ajuda a operadora a entender visualmente por que uma transação **é
ou não** órfã — bate com a régua acima.

Regras de filtragem de `differences[]`:
- `kind = "diverged"`: a security existe na repetição **e** no
  `processedPosition(target)`, e algum dos componentes (qty/pu/saldo)
  excedeu a tolerância **unificada de impacto em $ ≥ 0,01** (ver
  §[Detecção de divergência](#detecção-de-divergência-de-quantidade)
  para a fórmula).
- `kind = "missingInTarget"`: a security está na repetição mas não no
  target — provavelmente uma compra nova; `diff` é `null`. Só conta
  como divergência quando `|repetição.balance| ≥ 0,01`.
- `kind = "missingInRepetition"`: a security está no target mas o
  rule chain não produziu uma linha (qty=0 no source sem transaction,
  etc.); a entrada vem no objeto da wallet sem detalhes da repetição.

Linhas que **bateram** dentro da tolerância **não** aparecem em
`differences[]` — só são contadas em `summary.matched`. Isso mantém o
log focado em divergências; rebroadcastar 200 linhas idênticas seria
ruído.

### Endpoints relacionados

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/repetir-posicoes/logs` | Lista os relatórios persistidos (mais recente primeiro). Querystring `?limit=50` (clamped 1..500). Cada item traz `{runId, createdAt, uploaded, totalRows, totals, companies[], walletCount, mtime}`. |
| `GET` | `/api/repetir-posicoes/logs/<runId>` | Retorna o JSON completo de um relatório (mesma estrutura do `diffReport` inline). 404 quando o arquivo não existe; 400 quando `runId` não casa com `[A-Za-z0-9_\-]{1,80}`. |

O botão **"Baixar JSON"** do modal salva o relatório atual como
`<runId>.json` no download do browser (mesmo conteúdo do arquivo
persistido no servidor).

---

## Relatório de diferenças vs processedPosition

Para cada security calculada, a prévia compara o resultado contra a
`processedPosition` em `targetDate` (quando existir):

```
diff.quantity = repetição.quantity - target.quantity
diff.pu       = repetição.pu       - target.pu
diff.balance  = repetição.balance  - target.balance
```

Por linha (coluna **"Diff vs target"** na prévia):
- Cada componente (`qtd:`, `pu:`, `sld:`) **só aparece quando seu
  impacto em $ ≥ 0,01**. Régua unificada (ver
  §[Detecção de divergência](#detecção-de-divergência-de-quantidade));
  componente cujo impacto financeiro é menor que 1 centavo é omitido
  da célula — drift puramente computacional não polui a leitura.
- Quando todos os componentes têm impacto < R$ 0,01, a célula vira
  `—` discreto. Hover do `—` informa "impacto < R$ 0,01".
- Cada linha individual exibe no `title` o impacto em $ daquele
  componente — útil para auditoria fina (ex.: "drift de qty equivale a
  R$ 0,03").
- âmbar quando positivo (repetição > target);
- vermelho quando negativo (repetição < target);
- `—` em italic quando não há entrada no target (sem comparação possível).

Por carteira (chip no header):
- `matched`: linhas idênticas dentro da tolerância;
- `diverged`: linhas presentes em ambos com alguma diferença;
- `missingInTarget` (`a+`): a security existe na repetição mas não no
  target — provavelmente uma compra recente;
- `missingInRepetition` (`a−`): a security existe no target mas a
  repetição não tem (ex.: zerada no source, sem transaction na
  target);
- `quantityMismatches`: subset de `diverged` em que a quantidade
  diverge (sinal isolado para futura lógica de ação);
- `Δ saldo`: soma dos diffs de saldo, com cor agregada (verde quando
  ≈ 0, âmbar/vermelho de acordo com o sinal).

### Detecção de divergência de quantidade

Cada linha da prévia carrega `quantityMismatch: bool`, marcado quando
o **impacto em $** da diferença de quantidade — `|qty_d × pu_ref|`,
onde `pu_ref = max(|rep.pu|, |target.pu|)` — atinge **R$ 0,01**. Sob
essa régua, drifts numéricos que não movem dinheiro (ex.:
`0,000015 unid. × PU 100 = R$ 0,0015`) param de marcar a linha como
divergente. Versão anterior usava `abs(qty_d) ≥ 1e-6` direto, que
flaggava ruído de ponto flutuante; substituída pela régua de impacto
financeiro a pedido do operador.

No bloco do "missing in target" (security existe na repetição mas
não no `processedPosition` de `targetDate`), a régua é equivalente:
`quantityMismatch` é marcado quando `|rep.balance| ≥ R$ 0,01` (o
impacto em $ aqui é simplesmente o saldo replicado, já que o
contraponto no target é zero).

Na UI a linha que dispara `quantityMismatch` recebe:
- Borda vermelha à esquerda (`rp-row-qty-mismatch`).
- Célula "Qtd repetição" destacada em vermelho.
- Tooltip mostrando `Qtd target: <valor>`.

Esse sinal é independente do `diverged` total — futura lógica de
ação (a definir) deve usar `row.quantityMismatch` ou
`summary.quantityMismatches`.

Sem `processedPosition` no target → `vs target: —` (italic gray) e
todas as colunas de diff mostram `—`.

---

## Modelo de dados / endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET`  | `/repetir-posicoes` | Render da página (Jinja2). |
| `GET`  | `/api/repetir-posicoes/filters/companies` | Lista de empresas visíveis (espelha `/api/carteira/filters/companies`). |
| `GET`  | `/api/repetir-posicoes/filters/wallets?companyId=...` | Carteiras de uma empresa, com flag `enabled` baseada na união de todas as listas salvas (helper do picker). |
| `GET`  | `/api/repetir-posicoes/wallet-lists` | Presets nomeados com metadata enriquecida: `{lists: [{name, addedAt, walletIds[], wallets: [{walletId, walletName, companyId, companyName}]}]}`. |
| `POST` | `/api/repetir-posicoes/wallet-lists` | Upsert de um preset. Body: `{name, walletIds[]}` → `{ok, name, count}`. |
| `DELETE` | `/api/repetir-posicoes/wallet-lists?name=<n>` | Remove um preset. |
| `GET`  | `/api/repetir-posicoes/daily?lists=L1,L2&date=YYYY-MM-DD` | Carteiras da união dos presets em `lists` (CSV, **obrigatório** — vazio devolve `wallets: []`), com `companyId`/`companyName` + maior `processedPosition.positionDate`. Sentinela `lists=*` → união de **todos** os presets salvos (usado pelo fast-path do Painel de Controle). Sem `companyId` no querystring — a rotina é company-agnostic. |
| `POST` | `/api/repetir-posicoes/preview` | Body: `{items: [{walletId, sourceDate, targetDate}, ...]}` → preview por carteira, com regras aplicadas. O backend resolve `companyId` por wallet via `db.wallets`. |
| `POST` | `/api/repetir-posicoes/b1-prices` | Body: `{items: [{securityId, targetDate}, ...]}` → `{prices: {"<securityId>\|<targetDate>": {pu, date}}}`. Fonte: `db.securityPrices.historyPrice[]` filtrado por `date == targetDate` (match exato, sem carry-forward). Entradas têm apenas `date`/`value`/`adjustedQuantity`; "B1" é rótulo da UI, não campo no banco. `securityId` é consultado tanto como string quanto como `ObjectId` (legado misto). |
| `POST` | `/api/repetir-posicoes/apply` | Body: `{items: [{walletId, sourceDate, targetDate, acceptedSecurityIds?: [...]}, ...], puOverrides?: {"<walletId>\|<securityId>\|<targetDate>": pu}, includeCash?: bool (default true)}` → agrupa por empresa, constrói **um `.xlsx` por empresa** e envia N chamadas via `upload_unprocessed_security_positions_file`. **`acceptedSecurityIds` é opcional** — omitir / passar `null` aceita todo o output do rule chain (modo "Executar sem prévia"). **Caixa**: cada carteira recebe uma linha `Caixa = "Sim"` com `SaldoBruto = cash.new`, a menos que `includeCash=false`. Resposta: `{uploaded, uploads[{companyId, companyName, status, rows, filename, ...}], results[{cashSent, ...}], totalRows, runId, diffTotals, diffReport}` (ver [§Relatório de log](#relatório-de-log-audit-trail)). |
| `GET` | `/api/repetir-posicoes/logs` | Lista os relatórios persistidos (mais recente primeiro). Querystring `?limit=50`. |
| `GET` | `/api/repetir-posicoes/logs/<runId>` | Retorna o JSON completo de um relatório persistido. |

### Body de preview — exemplo

`companyId` **não** aparece no topo do body — a rota é company-agnostic e
resolve a empresa por walletId via `db.wallets` no servidor. O backend
também rejeita `targetDate <= sourceDate` (sem repetir no mesmo dia).

```json
{
  "items": [
    {
      "walletId":   "68bb268b9a9a11e087ee53de",
      "sourceDate": "2026-05-09",
      "targetDate": "2026-05-12"
    }
  ]
}
```

### Resposta de preview — exemplo

```json
{
  "results": [
    {
      "walletId":   "68bb268b9a9a11e087ee53de",
      "walletName": "Fundo Alpha FIA",
      "sourceDate": "2026-05-09",
      "targetDate": "2026-05-12",
      "currencyId": "BRL",
      "cash":       {"former": 1250.50, "delta": -30000.00, "new": -28749.50,
                     "target": -28749.50, "targetDelta": 0.0},
      "provisions": 4500.00,
      "targetSummary": {
        "matched":              5,
        "diverged":             2,
        "missingInTarget":      1,
        "missingInRepetition":  0,
        "totalBalanceDiff":   123.45,
        "hasTarget":          true
      },
      "rows": [
        {
          "securityId":    "67fee83756e1234567890abc",
          "securityName":  "BBDC4",
          "unprocessedId": "BBDC4",
          "pricingType":   "B1",
          "original":        {"quantity": 15000, "pu": 102.34, "balance": 1535100.00},
          "repetition":      {"quantity": 17000, "pu": 102.50, "balance": 1742500.00},
          "targetProcessed": {"quantity": 17000, "pu": 102.50, "balance": 1742500.00},
          "diff":            {"quantity": 0,     "pu": 0,      "balance": 0},
          "flags":           ["buySell", "targetPrice"],
          "txnIds":          ["65a1b2..."],
          "accepted":        true,
          "isNew":           false
        }
      ],
      "warnings": []
    }
  ]
}
```

---

## Como adicionar uma nova regra

1. **Implementar** a função `apply(row_in, ctx)` em
   [`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py), seguindo o
   padrão das regras existentes.
2. **Registrar** a regra no array `_RULES` (ordenado — a posição importa).
3. **Persistir** qualquer configuração nova no JSON conforme estrutura
   acima. Não criar arquivos paralelos.
4. **Documentar** a regra adicionando uma nova subseção em §4 deste arquivo
   com os mesmos campos (`Identificador`, `Disparo`, `Ação`, `Flag`,
   `Observações`). É mandatório — o spec é a fonte de verdade.

Anti-padrões a evitar:

- ❌ Mutar `row_in` em vez de retornar uma nova `row_out`.
- ❌ Tocar em outras coleções (`navPackages`, `publishedPositionSecurities`)
   dentro da regra — a rotina escreve apenas `unprocessedSecurityPositions`.
- ❌ Acoplar a regra ao `transactions_by_security` sem checar
   `tx.trashed != True` (default já filtrado pelo loader).

---

## Arquivos relacionados

| Arquivo | Função |
|---|---|
| [`pages/repetir_posicoes.py`](../pages/repetir_posicoes.py) | Blueprint, loaders, regras, preview, apply. |
| [`templates/repetir_posicoes.html`](../templates/repetir_posicoes.html) | Página única com abas **Configuração** / **Rotina**. |
| [`templates/controlpanel.html`](../templates/controlpanel.html) | Painel de Controle: chip "Repetir" → `openRepetirInline(chip)` abre `/repetir-posicoes` no iframe interno (`Funcoes._showPage('__repetir__', …)`), sem modal e sem URL params. |
| [`templates/shell.html`](../templates/shell.html) | Roteamento URL-hash: hash key `controlpanel` mapeia para `/controlpanel`; sidebar entry direto para `/repetir-posicoes`. |
| [`data/repeat_positions_config.json`](../data/repeat_positions_config.json) | Persistência dos presets nomeados (`walletLists[]`). |
| `data/repeat_positions_logs/<runId>.json` | Relatórios de log persistidos por execução (uma chamada `/apply` = um arquivo). Diretório criado lazy pelo `_persist_diff_log`. |
| [`pages/excecoes.py`](../pages/excecoes.py) | Referência para o builder `.xlsx` e o upload upstream. |
| [`beehus_api/positions.py`](../beehus_api/positions.py) | `upload_unprocessed_security_positions_file`. |
