# Exceções — regras reutilizáveis para reescrever posições

A página **Exceções** (`/excecoes`) define regras persistentes que reescrevem o `unprocessedSecurityPositions` de uma carteira em outras carteiras. Hoje existem dois tipos (`kind`):

- **`position_strip`** — uma carteira "origem" (sinalizada como exceção) tem ativos individuais transferidos para carteiras "saída" todos os dias úteis. É o caso histórico e tudo nas seções abaixo se refere a ele por padrão.
- **`wallet_slice`** ("Fatiar carteira") — envia um percentual (`%`) da carteira de origem para uma única carteira de destino. O percentual é aplicado às quantidades dos ativos, ao `cashAccount`, às `provisions` e às `transactions` da origem. A origem **não** é modificada — só a carteira de destino recebe os dados fatiados. Detalhes em [§ Fatiar carteira](#fatiar-carteira-wallet_slice).
- **`class_strip`** ("Stripping por classe") — varre uma **lista de carteiras de origem** e roteia cada ativo para uma carteira de destino com base na classificação `processedPosition.securities.hierarchicalVariable.variable1`. Cada `variable1` configurado tem uma carteira destino (one-to-one); securities cuja `variable1` não aparece nas rotas ficam intactas. Migra também provisions (`dividend`/`interestOnEquity`) e transactions (`coupon`/`amortization`/`securityContributionAdjustment`) ligadas às securities movidas, e cria ajustes `withdrawalDepositAdjustment` na carteira destino para neutralizar `amountDifference` e settlement na Data Base. Detalhes em [§ Stripping por classe](#stripping-por-classe-class_strip).

> **Onde mora a UI hoje**: a partir desta iteração, o fluxo de **Position Stripping** está espelhado no **Painel de Controle** (`/controlpanel`), acionado pelo chip **Strip** dos grupos *Lançamentos* e da segunda linha de *Pipeline* (passo 4). O chip não navega mais — abre o painel `#strip-view` inline (irmão do `#daytrade-view`) e consome os mesmos endpoints `/api/excecoes/*` documentados aqui. A página `/excecoes` continua disponível como rota fallback. O fluxo **Ajustes day-trade** já havia sido portado da mesma forma (chip **Day-trade**).
>
> O JS vive em `templates/controlpanel.html` no módulo `Strip` (CSS prefixado por `.sp-`, IDs `sp-*`), espelhando o módulo `Daytrade` adjacente. O backend não muda: `pages/excecoes.py` continua sendo a única fonte de verdade para listar, criar, editar, excluir, prever e aplicar exceções.

A página é dividida em três entradas:

| Entrada | Onde | Propósito |
|---------|------|-----------|
| **Setup** | botão "+ Nova Exceção" (no `#strip-view` do Painel de Controle, ou em `/excecoes`) | Configura uma exceção persistente. Tipo `position_strip` (origem, saídas, regras por ativo), `wallet_slice` (origem, destino, percentual) ou `class_strip` (lista de origens, rotas por `variable1` → destino) — radio no topo da modal |
| **Rotina diária** | botão "Aplicar" na lista | Aplica a exceção a uma data: gera Excel(s) e envia para a API Beehus |
| **Ajustes day-trade** | chip "Day-trade" no Painel de Controle (ou botão "Ajustes day-trade" em `/excecoes`) | Detecção ad-hoc de buy/sell intraday e patch de `unprocessedSecurityPositions` (sem persistência). Detalhes em [§ Ajustes day-trade](#ajustes-day-trade) |

> **Escopo desta versão**: manipulação de **posições** (`unprocessedSecurityPositions`) **e migração de transações** ligadas aos ativos stripados. O tratamento de **`cashAccounts`** será especificado em uma iteração futura.

---

## Modelo de dados

```
data/excecoes/<companyId>/<exceptionId>.json
{
  "id":              "uuid",
  "companyId":       "...",
  "name":            "...",
  "kind":            "position_strip",
  "sourceWalletId":  "...",
  "outputWalletIds": ["...", "..."],
  "rules": [
    {
      "unprocessedId":      "<id do ativo na origem>",
      "addToWalletId":      "..." | null,
      "removeFromWalletId": "..." | null,
      "caixa":              false
    }
  ],
  "createdAt":   "ISO-8601",
  "updatedAt":   "ISO-8601",
  "lastApplied": {"date": "YYYY-MM-DD", "at": "ISO-8601"} | null
}
```

- `id`, `companyId`, `name` são obrigatórios.
- `sourceWalletId` é a carteira **sinalizada como exceção**. Sua posição diária é a fonte dos valores que migram para as carteiras de saída.
- `outputWalletIds` é a lista de carteiras alvo. A origem **não** é incluída automaticamente — só aparece como destino se o usuário a escolher explicitamente em uma regra.
- `kind` é o tipo de exceção. Hoje só existe `position_strip`; futuros tipos vão coexistir no mesmo arquivo.
- `rules[].unprocessedId` referencia um ativo agregado da posição da origem (mesmo agrupamento usado em `pages/posicoes._group_unprocessed`).
- Cada regra deve ter `addToWalletId` e/ou `removeFromWalletId`. Ambas precisam pertencer a `{sourceWalletId} ∪ outputWalletIds`. Regras sem nenhum dos dois lados são silenciosamente descartadas no save.
- `caixa` controla a coluna `Caixa` (Sim/Não) do Excel enviado. Default `false`.
- Validação rígida de path: `[A-Za-z0-9_-]+` para ids e `YYYY-MM-DD` para datas. Outros valores → 400.
- Escrita atômica via `atomic_write_json` (tempfile + `os.replace`) — segura em paths OneDrive.

---

## Setup (criação de exceção)

Fluxo da modal:

1. **Empresa + Nome**: empresa filtrada por `company_filter` da sessão.
2. **Carteira de origem**: carregada a partir de `db.wallets` filtrada por `companyId`.
3. **Carteiras de saída**: checkbox multi-seleção, exclui a origem.
4. **Data de visualização**: serve apenas para o usuário enxergar quais ativos existem hoje na origem. Não é gravada.
5. **Seleção de ativos**: agrupados por `unprocessedId` (com soma de quantidade/saldo e PU médio ponderado, idêntico a Validação Proc.).
6. **Por ativo selecionado**:
   - `Adicionar em` — dropdown com `{origem} ∪ {saídas selecionadas}`
   - `Remover de` — mesma lista
   - `Caixa` — toggle Sim/Não

A lista de ativos disponíveis para regras só inclui aqueles presentes na posição da origem na data carregada. Para incluir um ativo que apareceu em uma data diferente, o usuário recarrega a posição com outra data e a tabela de regras atualiza preservando seleções pré-existentes.

#### Cross-company target/output

`position_strip.outputWalletIds` e `wallet_slice.targetWalletId` agora aceitam carteiras de **qualquer empresa visível** ao operador (`company_filter`), não só da empresa da exceção. `sourceWalletId` continua estrito à empresa da exceção.

- Backend: novo helper `_wallet_visible(wid)` checa se a wallet existe e a empresa dela está no filtro. Substitui o `_wallet_in_company` nos validators dos outputs/target dessas duas kinds.
- Picker: o endpoint `/api/excecoes/wallets?crossCompany=1` devolve wallets de todas as empresas visíveis com `companyId` + `companyName` por item. A modal carrega essa lista em paralelo com `walletsForCompany` (que continua restrita à empresa da exceção e alimenta só o picker de origem) e a usa nos checkboxes de outputs (`_renderOutputs`) + dropdown da target (`_renderSliceTarget`). Outputs ficam agrupados por empresa com header "OUTRA EMPRESA" destacado em âmbar quando a carteira é cross-company.
- Apply: `_resolve_wallet_meta` foi estendido para retornar também `companyId` por wallet. No `_apply_strip` (Step 2 — upload de posições) e em todas as chamadas upstream do `_apply_slice` (upload de posições + `create_provision` + `create_transaction`), a `companyId` enviada para Beehus é a **real da carteira destino**, não a da exceção. Beehus indexa `(companyId, walletId)`; subir uma carteira de empresa B sob o `companyId` da empresa A faria o write sumir do lugar certo.
- **Caveat — migração de transactions cross-company (position_strip)**: a Etapa 1 do `_apply_strip` faz `PATCH /beehus/financial/transactions/{id}` apenas com `walletId` (e `balance`/`price`/`currencyId` quando cross-currency). O `companyId` da transação **não é** patchado — Beehus pode ou não auto-resolver a partir do novo `walletId`. Se o upstream rejeitar, ajustar a transação manualmente. Para evitar surpresa, esse caminho é mantido como antes em strips dentro da mesma empresa (caso comum) e apenas o upload de posições e os creates do slice foram migrados pra `companyId` real da target.

#### Opt-in: Utilizar preços na curva

Acima do passo 3 há um checkbox **"Utilizar preços na curva"** (campo `useCurvaPrice` no blob da exceção). Quando ligado:

- O backend roda a engine de **Preços na Curva** (`pages.precificacao.calculate_curva` sobre os templates salvos em `_load_lists`) e monta um mapa `{walletId|securityId|date: pu}` via `_compute_strip_curva_pu_map(target_date, allowed_wallets={src_w} ∪ out_ws)`. O `allowed_wallets` é um filtro de **input** da engine: como o lookup é estrito por `walletId` (ver abaixo), só processamos securities cujo `walletId` no template está entre as wallets do plano — corta o custo da `calculate_curva` em bases com templates grandes sem alterar a semântica.
- Depois que as regras já foram aplicadas e o `positions[w]` de cada carteira do plano contém os valores pós-strip, há um **passe único de curva** que percorre **todas as linhas de todas as wallets afetadas pelo plano** (linhas tocadas por regra + linhas baseline que sobem juntas no upload). Para cada `(walletId, unprocessedId)` cujo `securityId` (traduzido por `_build_unprocessed_to_security_map(companyId)`) tem entrada na curva em `(walletId, securityId, target_date)`, o PU é substituído pelo valor calculado e o `balance` re-derivado como `quantity × pu_curva`. Quantity nunca muda.
- O lookup é **estrito por walletId** — sem fallback agnóstico `*|sid|dt` (mesma decisão de [§ Repetir Posições](REPETIR_POSICOES.md), para não contaminar carteiras distintas com PU de outra). Linhas sem `securityId` mapeado, sem entrada na curva ou com `quantity == 0` ficam inalteradas.
- Como o passe é **post-rules**, a substituição tem precedência sobre o resultado da conversão cambial: numa migração cross-currency, se a carteira destino tiver entrada na curva para o ativo migrado, o PU pós-curva é o da curva da destino (já em moeda da destino), substituindo o `pu = balance_convertido / quantity` derivado da conversão FX.
- Cada row do plano carrega `priceSource: "curva" | "unprocessed" | "baseline"`:
  - `curva` — passe da curva substituiu o PU.
  - `unprocessed` — linha tocada por regra mas a curva não tinha entrada — PU veio do upstream.
  - `baseline` — linha não tocada por regra **e** sem curva — só ride-along do `unprocessedSecurityPositions` da própria carteira destino.
- No preview a UI mostra (a) um banner topo com a contagem `N via curva · M via unprocessedPosition · K baseline (sem curva)` cobrindo o plano inteiro e (b) uma coluna **"Fonte PU"** com pill `curva` (azul) / `unprocessed` (cinza) / `baseline` (cinza) por linha.
- Falhas da engine (load_lists exception, calculate_curva exception, templates vazios) caem silenciosamente para o caminho `unprocessedPosition` — não bloqueiam o `/preview` nem o `/apply`.

Escopo: implementado **apenas para `position_strip`**. `wallet_slice` e `class_strip` não consultam a curva (o checkbox fica oculto pelo `_applySetupKindVisibility`).

### Edição

Re-abrir a exceção no botão "Editar" reidrata o formulário com:

- empresa, nome, origem, saídas;
- regras existentes (mesmo sem recarregar a posição), com `selected = true`;

para que o usuário possa ajustar destinos sem precisar acessar a posição original.

---

## Rotina diária (aplicação)

### UI no Painel de Controle (`#strip-view`)

A partir desta iteração, a aplicação no Painel de Controle é **em lote, com aprovação explícita via tela de resumo**. A coluna de ações da linha tem apenas `Editar` e `Excluir`; a antiga coluna `Aplicar` foi substituída por:

- **Checkbox por linha** (com checkbox master no header — `marcar/desmarcar todos`, com tri-state `indeterminate` quando algumas estão marcadas). O master opera **apenas sobre as linhas visíveis** (respeita o filtro de empresa abaixo).
- **Data Base por linha** (`<input type=date>` na coluna `Data Base`) — **data informada pelo operador**, com padrão **hoje** (`_todayISO()`) na primeira renderização. Edits do operador são persistidos em `Strip._rowDates[exceptionId]` e sobrevivem a re-renders até o operador sair da view ou clicar em **Reiniciar**. (Antes era semeada do `latestProcessedDate` da carteira de origem — `max(processedPosition.positionDate)` — mas essa leitura no Mongo foi removida no desligamento do Mongo; o fluxo de apply só precisava da data escolhida pelo operador.)
- **Toolbar global** acima da tabela com:
  - **+ Nova Exceção** — abre a modal de setup.
  - **Filtro de empresa** (popover com checkboxes) — limita as linhas visíveis às empresas marcadas. Quando o operador tica uma empresa, **todas as linhas visíveis dessa empresa são pré-selecionadas** (a request foi "filtro para marcar quais serão executadas"); o operador pode então destigar individualmente. **Marcar todas** seleciona toda a base; **Limpar** zera o filtro e a seleção. Linhas escondidas pelo filtro são removidas de `_selectedIds` para que **Aplicar selecionadas** nunca pegue uma linha invisível.
  - **Filtro de data** (popover com checkboxes, espelha o filtro de empresa — `#sp-filter-dates-wrap`) — lista as datas únicas exibidas na coluna `Data Base` das linhas que passam pelo filtro de empresa (sorted desc, formatadas `dd/mm/yyyy` para display, `value` em ISO). É **filtro puro**: marcar/desmarcar checkboxes apenas esconde/mostra linhas em `_visibleExceptions()` (`_filter.dates: Set | null`) — **não muta `_rowDates`** nem o conteúdo da tabela. **Marcar todas** captura snapshot explícito das datas correntes (mesma semântica do filtro de empresa); **Limpar** volta a "todas". Seleções de datas que deixam de existir após edição por-linha são removidas em `_refreshDateFilterPanel()`.
  - **Aplicar selecionadas (N)** — disabled quando `N=0`. Conta apenas linhas com checkbox marcado **e** atualmente visíveis.

Fluxo:

1. O operador escolhe empresas no filtro (ou deixa "todas"), o que pré-marca os checkboxes das linhas visíveis dessas empresas, e opcionalmente reduz a lista a um subconjunto de datas via filtro de data (puro — não altera `Data Base`). Edita `Data Base` por-linha onde necessário.
2. Clicar em **Aplicar selecionadas** **não envia nada** — abre o **modal de resumo / aprovação** (`#sp-confirm-modal`). O modal:
   - Congela a seleção atual + data num snapshot (`_bulkConfirm`), para que mudanças de checkbox em segundo plano não afetem o lote sob revisão.
   - Lista as exceções selecionadas em tabela (`Empresa · Exceção · Origem · Regras · Pré-visualização`).
   - Dispara **POST `/api/excecoes/<id>/preview` em paralelo** para cada exceção do snapshot (sem efeitos colaterais, só leitura). Cada resposta atualiza a célula "Pré-visualização" com pills compactas:
     - `X carteira(s)` (verde) + `Y transação(ões)` (azul) quando há mudanças,
     - `nada a enviar` (cinza) para no-ops (nem carteiras nem transações),
     - `fallback` (amarelo) se a posição de origem usou fallback,
     - `N regra(s) sem ativo` (amarelo) para `missingRules`,
     - `N sem mapping` (amarelo) para `transactionsUnmapped`,
     - `auth` / `erro` (amarelo/vermelho) quando o `/preview` falhou.
   - Mostra contadores agregados no topo (data, total selecionado, quantos carregando, quantos com erro, quantos no-op).
3. O operador clica em **Confirmar e enviar** (ou em `Cancelar` / `Esc` para sair sem efeitos).
4. Confirmado, o front-end faz **um POST `/api/excecoes/<id>/apply` por seleção, sequencialmente** (não em paralelo — barra de progresso `Enviando i/N`; um 401 interrompe o resto sem queimar token).
5. Cada resposta vira um bloco no painel **Resultado do envio** abaixo da tabela: pill `ok`/`erro`/`auth` + as listas de `transactionResults[]` e `results[]` (mesmo formato per-exception do endpoint).
6. Se `r.status === 401` em qualquer item, o loop para; o resumo no toolbar mostra um `N não tentadas (auth break)`.
7. Ao final, `loadList()` é chamado para refletir `lastApplied` atualizado nas linhas.

Decisões de design:

- **Preview em paralelo, apply sequencial**: `/preview` não toca a API externa (lê só do Mongo local) então é seguro paralelizar; `/apply` toca o Beehus + faz upload multipart e exige sequência para preservar progresso e interromper em auth break.
- **Snapshot ao abrir o modal**: o conjunto sob revisão fica imutável até o operador `Cancelar` ou `Confirmar`. Marcar/desmarcar checkboxes na tabela atrás do modal não interfere.
- **Aprovação obrigatória**: a aplicação só dispara quando o operador clica explicitamente em "Confirmar e enviar" dentro do modal. Não há mais `window.confirm()` de uma linha.

### Semântica do `apply` (idêntica a antes)

`POST /api/excecoes/<id>/apply` (body: `{companyId, date}`):

- **Etapa 1 — transações**: para cada regra com `addToWalletId` *e* `removeFromWalletId`, faz `PATCH /beehus/financial/transactions/{id}` mudando `walletId` para o destino. Critério de busca:
  - `walletId == removeFromWalletId`,
  - `securityId == securityMappings[unprocessedId]` (mapeamento por empresa),
  - `liquidationDate == data alvo`,
  - `companyId == companyId` da exceção (defesa contra carteiras movidas entre empresas).
  Para que o `PATCH` aceite a mudança de carteira, `walletId` foi adicionado a `_PATCHABLE_FIELDS` em `beehus_api/transactions.py`.
- **Etapa 1b — ajustes pareados (`securityTransfer`)**: cada migração bem-sucedida dispara dois `POST /beehus/financial/transactions` adicionais para deixar um lastro contábil casado nas duas carteiras envolvidas:
  - na **carteira de origem** (`removeFromWalletId`): `beehusTransactionType = "securityTransfer"`, `description = "Ajuste " + descrição original`, mesmas `operationDate`/`liquidationDate`/`securityId`, `balance` e `quantity` **iguais** aos da transação **original** (na moeda da origem);
  - na **carteira de destino** (`addToWalletId`): mesmos campos, porém com `balance` e `quantity` **multiplicados por −1** (sinal invertido). Em migração cross-moeda, o `balance` deste lado é o **convertido** e a `currencyId` é a da carteira destino (ver [§ Moeda da carteira + conversão cambial](#moeda-da-carteira--conversão-cambial-fxrates)).

  Os planos dos dois ajustes são montados por `_build_adjustment_plans(tx)` já na fase de `preview`/`apply`, anexados a cada migração como `tx.adjustments[]`. O `/api/excecoes/<id>/preview` retorna esse array dentro de `transactions[]`, e a tabela **"Transações que serão migradas + ajustes pareados"** do Painel de Controle desenha as duas linhas `securityTransfer` logo abaixo da migração que as gerou (recuadas, fundo azul claro, com `+` na coluna de origem para o ajuste do sender e `−` na coluna de destino para o ajuste do receiver). O contador acima da tabela mostra `N migração(ões) + 2N ajuste(s)` e o pill resumo na modal de confirmação inclui o sufixo `+ X ajuste(s)`.

  Ajustes só são criados quando o `PATCH` da migração retornou 2xx (sem migração não há par a registrar). Eles entram no mesmo `transactionResults[]` da resposta, marcados com `kind: "adjust"` + `side: "from" | "to"` (e `walletId`, `balance`, `description`, `createdId`); a linha original da migração ganha `kind: "migrate"`. Falhas individuais nos ajustes são reportadas por linha mas **não** cancelam as migrações seguintes; um 401 ainda interrompe tudo. Para criar o `securityTransfer` o backend resolve `entityId`/`currencyId` da carteira via `_resolve_wallet_meta`; carteiras sem `entityId` produzem uma linha `status: "error"` com a razão `"wallet sem entityId"`.
- **Etapa 2 — posições**: gera um arquivo XLSX por carteira afetada com colunas `Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda` e envia cada arquivo via `POST /beehus/financial/positions/unprocessed-security-positions/file` (multipart, com `companyId` no form).
- As duas etapas são executadas em sequência. Erros de auth (401) interrompem tudo. Erros não-auth são acumulados por item e a resposta final é 502 com `transactionResults[]` + `results[]` (posições) — clientes que precisam saber o que de fato subiu inspecionam ambos.
- Regras sem entry em `securityMappings` aparecem em `transactionsUnmapped[]` (warning, não bloqueia).
- `lastApplied = {date, at}` é gravado **apenas** quando nada falhou em nenhuma das duas etapas.

#### "Origem sem posição" — pill por linha + banner agregado

Quando uma regra dispara sobre um `unprocessedId` cuja origem (`unprocessedSecurityPositions` na data alvo) trouxe **`quantity == 0` e `balance == 0`**, a regra contribui zero pro destino: o `_conv_bal` recebe 0, FX vira no-op, e a posição visível no destino acaba sendo só o **baseline pré-existente** (qty/PU/saldo da própria carteira destino antes do strip). Esse cenário é ambíguo na UI — fica parecido com "o conversor não rodou" mesmo quando a engine de FX está OK. Por isso o `_build_plan` rastreia esses casos em `src_empty_uids` (set) e expõe duas marcas:

- `plan.emptySourceUids` — array no topo da resposta com a lista de uids zerados na origem. Renderizado como pill **`N ativo(s) com origem sem posição (regra contribui 0)`** no banner de `#sp-apply-warnings` (tooltip lista os uids).
- `row.srcEmpty` (`true`/`false`) — flag por linha (apenas em rows tocadas por regra; baseline fica `false`). O `_renderApplyPreview` desenha um pill **`origem vazia`** (warn) na coluna "Marca" ao lado do `+ adicionado` / `− removido` / `± ambos`.

Não bloqueia o `/apply` — só sinaliza. O upload do XLSX sobe a baseline inteira da carteira inalterada (no-op upstream), o que preserva o estado prévio.

#### Moeda da carteira + conversão cambial (`fxRates`)

A moeda de cada carteira vem de `wallets.currency` (ISO 4217, ex.: `BRL`/`USD`) — o mesmo valor que `processedPosition.currency` denormaliza. O helper `_wallet_currency(w)` lê `currency` e só cai para `currencyId`/`"BRL"` quando o campo está ausente. É essa moeda que aparece na listagem de **Carteiras de saída** do setup, no dropdown "Adicionar em" e na coluna **Moeda** do XLSX de posições.

O ativo **stripado (enviado para outra carteira)** é avaliado na moeda da carteira **destino**. Quando a carteira destino precifica em moeda diferente da origem, `PU` e `SALDO` (balance) são convertidos via `db.fxRates`; a `quantity` é unidade, não dinheiro, e nunca converte (o `PU` da posição agregada é re-derivado de `saldo ÷ quantidade`, já convertido). O lado de **remoção** permanece na moeda da própria carteira que perde o ativo (normalmente a origem → conversão neutra).

- **fxRates**: documentos `{currencyId, value, date}` — `value` é "1 `currencyId` = `value` BRL", ou seja **BRL é a moeda âncora implícita** (não tem doc próprio; `value=1` é assumido). Constante `_FX_REF_CURRENCY = "BRL"`. Para qualquer par X→Y: `_fx_rate` busca `value(X)` e `value(Y)` na data exata e devolve `value(X) / value(Y)` (X→BRL = `value(X)`, BRL→Y = `1/value(Y)`, X→Y = cross via BRL). `_fx_date_query` casa `date` como string `YYYY-MM-DD` (formato real do mock + prod) ou como datetime de meia-noite (robustez). Se `value(X)` ou `value(Y)` está ausente / não é numérico / é zero → retorna `None` e a chamada acima cai no fluxo de override manual.
- **Sem taxa → prompt manual**: rate ausente para um par necessário (após tentar direta e inversa em `fxRates`) → `_build_plan` retorna `{"error": "...", "pendingFxRates": [{from, to, date}, ...]}` com 422. O front-end abre o modal **`#sp-fx-prompt`** (`Strip._promptForFxRates`) listando cada par + data e pedindo a taxa **na convenção de mercado**: quando o par envolve BRL, o lado não-BRL é sempre o `base` (ex.: USD/BRL → "1 USD = X BRL" → o operador digita 4,9587 = "dólar a R$ 4,9587"); fora desse caso, mantém origem→destino. O operador preenche, clica **Continuar** e o JS resubmete `/preview` (ou `/apply`) com `fxOverrides: [{from: base, to: quote, date, rate}, ...]`. Como `_fx_rate` faz lookup direto + inverso, o sentido em que o override é armazenado não muda o resultado da conversão: para um strip BRL→USD com override `{from:USD, to:BRL, rate:4.9587}`, `_fx_rate(BRL, USD, date)` cai no lookup inverso e devolve `1/4.9587` ≈ 0,2017, que é o multiplicador correto BRL→USD. As taxas informadas valem **só nessa execução** — nada é gravado em `fxRates`, e os overrides só são consultados **depois** de `fxRates` (a base oficial sempre vence se existir). Cancelar o modal aborta a operação: na pré-visualização inline a operação fica em estado de erro; no fluxo multi-data a data afetada vira `erro` e o loop segue para a próxima.
- **Transactions**: a transação migrada que cruza moeda tem `balance`/`price` convertidos e `currencyId` setado para a moeda destino no `PATCH` (`price` foi adicionado a `_PATCHABLE_FIELDS`); `quantity` não muda. Dos ajustes pareados `securityTransfer`: o **lado origem** mantém moeda + `balance` originais; o **lado destino** usa moeda destino + `balance` convertido (sinal invertido). O `create_transaction` dos ajustes resolve a moeda por `adj.currencyId`.
- **Baseline da carteira destino — replace, não blend**: quando uma regra adiciona um ativo a uma carteira destino com contribuição **não-zero**, a linha pré-existente daquele `unprocessedId` no `unprocessedSecurityPositions` da destino é **descartada** antes da regra aplicar. O resultado final reflete só a contribuição convertida da origem (`PU = converted_pu`, `qty = source_qty`) em vez da média ponderada com o baseline. `_build_plan` rastreia o `(walletId, unprocessedId)` em `replaced_baseline` para garantir que o clear acontece só uma vez por par, mesmo quando múltiplas regras tocam o mesmo destino + uid. Quando a origem está vazia (qty=0 + bal=0 → `srcEmpty`), o baseline **NÃO** é descartado: o pill `origem vazia` avisa que o strip não moveu nada e o baseline sobe inalterado. Linhas de outros uids da carteira destino (que não foram tocados por nenhuma regra desta exceção) ficam como `baseline` normalmente.
- **Escopo**: implementado para `position_strip`. `wallet_slice` e `class_strip` ainda não convertem moeda no apply.

Como o bulk apply dispara N chamadas independentes, cada exceção é avaliada/aplicada de forma isolada pelo backend — uma falha em uma exceção (ex.: regra sem `securityMappings`) **não** afeta as demais do lote.

O endpoint `GET /api/excecoes/<id>/excel?date=...` continua disponível para inspeção offline (workbook único com todas as carteiras), mas não está mais exposto na UI do Painel de Controle.

### Aplicar por linha — seleção de datas (strip / fatiar / classe)

A coluna **Ações** de cada linha tem um botão **Aplicar** (além de `Editar` / `Excluir`). Para `position_strip`, `wallet_slice` e `class_strip`, clicar em **Aplicar** **não** dispara o `/preview` imediatamente — abre primeiro o modal **`#sp-apply-dates`** ("Aplicar — selecionar datas"), que reusa o mesmo padrão de datas do **Painel de Controle > Processar**:

- **Data única / Faixa de datas** (radios). Em data única o campo nasce semeado com a `Data Base` da linha (`_rowDates[id] || hoje`).
- Em faixa, aparece o card **"Selecionar datas específicas (opcional)"** (checkbox `#sp-apply-dates-uselist`) — quando marcado, mostra o filtro **"Apenas fim de mês útil"**, o botão **⬆ Subir Excel de datas** (`POST /api/beehus/util/parse-dates-excel`, uma data por célula, sem cabeçalho) e o dual-pane **Disponíveis / Selecionadas**. Sem marcar, a faixa é expandida em dias úteis (seg–sex) por `_businessDays`.

Ao confirmar (**Continuar**), o front resolve a lista de datas (`_resolveApplyDates`) e bifurca:

- **1 data** → mantém o fluxo rico atual: abre o painel inline `#sp-apply-inline` com a pré-visualização (`/preview`) e o botão **Confirmar e enviar** (`_startApplyInline` → `runApply`).
- **>1 datas** → pede `confirm()` e roda o painel **`#sp-apply-multi`**: um **`POST /api/excecoes/<id>/apply` por data, sequencialmente** (mesma semântica de auth-break do bulk — um 401 interrompe o restante). Cada linha da tabela atualiza `pendente → enviando → ok/erro`, com um detalhe compacto por data (`_applyDaySummary`: contagens `ok/total` por bucket de resultado) e um resumo agregado no header. **Não há preview por data** (seria pesado). Ao final, `loadList()` reflete o `lastApplied`.

---

## Endpoints

Blueprint: `pages/excecoes.py`. Todas as rotas aplicam `company_visible` no `companyId` recebido.

| Método | Rota | Propósito |
|--------|------|-----------|
| GET    | `/excecoes` | Página HTML |
| GET    | `/api/excecoes` | Lista todas as exceções visíveis (sem corpo). Resposta: `{exceptions: [...]}`. A coluna **Data Base** do painel não vem do backend — é editável no front, padrão **hoje** (o campo `latestProcessedDate` e a leitura `processedPosition` foram removidos no desligamento do Mongo). |
| GET    | `/api/excecoes/wallets?companyId=` | Carteiras de uma empresa: `[{id, name, currencyId}]` |
| GET    | `/api/excecoes/source-position?companyId=&walletId=&date=` | Ativos agregados da origem em uma data: `{date, walletId, securities: [{unprocessedId, quantity, pu, balance}]}` |
| GET    | `/api/excecoes/<id>?companyId=` | Lê uma exceção + nomes das wallets envolvidas |
| POST   | `/api/excecoes` | Cria. Despacha por `kind` (default `position_strip`). Body strip: `{kind?, companyId, name, sourceWalletId, outputWalletIds, rules}`. Body slice: `{kind: "wallet_slice", companyId, name, sourceWalletId, targetWalletId, percent}` |
| PUT    | `/api/excecoes/<id>` | Atualiza. Mesmo body. Preserva `id`/`createdAt`/`lastApplied`. **Rejeita** mudança de `kind` em uma exceção existente |
| DELETE | `/api/excecoes/<id>?companyId=` | Remove o arquivo |
| POST   | `/api/excecoes/<id>/preview` | Calcula o plano sem efeitos. Body: `{companyId, date}`. **Strip** retorna `{wallets, transactions, transactionsUnmapped, walletNames, securityNames, ...}`. **Slice** retorna `{kind:"wallet_slice", percent, summary, securities, cashAccount, provisions, transactions, ...}` |
| GET    | `/api/excecoes/<id>/excel?companyId=&date=` | Faz download do XLSX. Strip: workbook consolidado (todas as carteiras). Slice: XLSX da carteira de destino (securities fatiadas + Caixa). Nenhum dos dois inclui provisions/transactions |
| POST   | `/api/excecoes/<id>/apply` | Strip: migra transações + envia XLSX por carteira para a API. Slice: envia XLSX da carteira destino + cria provisions + cria transactions na carteira destino. Atualiza `lastApplied` se tudo der 2xx |

---

## Regras anti-fraude / segurança de path

- `_safe(segment)`: aceita só `[A-Za-z0-9_-]+` para `companyId` e `id`.
- `_safe_date(date)`: aceita só `YYYY-MM-DD`.
- `_wallet_in_company`: para qualquer `walletId` (origem, saída, addTo, removeFrom), o `companyId` da carteira no DB precisa bater com o `companyId` do payload. Bloqueia "vazamento entre empresas".
- Regras com `addToWalletId`/`removeFromWalletId` fora de `{sourceWalletId} ∪ outputWalletIds` são rejeitadas com 400.
- O escopo da listagem (`/api/excecoes`) respeita o `company_filter` da sessão; arquivos órfãos (companyId sem documento em `companies`) **não** vazam — só aparecem se `companies` os contiver.

---

## Arquitetura — pontos de extensão

- O campo `kind` permite que outros tipos de exceção convivam (ex.: `transaction_strip`, `cash_strip`). Cada novo `kind` ganha seus próprios validadores e seu próprio `_build_plan`. A página atual filtra por `kind == "position_strip"` implicitamente; a lista mostra a origem como "carteira sinalizada" porque hoje só existe esse tipo.
- A geração de Excel está concentrada em `_build_xlsx`. Quando o tratamento de transações/caixa for especificado, novas funções de plano podem produzir DataFrames diferentes consumidos pelos respectivos endpoints upstream sem mexer no fluxo de save/list.
- O cliente `beehus_api` ganhou `request_multipart` para uploads de arquivo. Reutilizável por qualquer outro endpoint multipart no futuro.

---

## Fatiar carteira (`wallet_slice`)

Tipo adicional de exceção que **fatia uma carteira de origem** e envia um percentual dela para uma carteira de destino. Não tem regras por ativo — o percentual é aplicado **uniformemente** sobre:

1. **Securities** (`unprocessedSecurityPositions.securities[]`) da origem — `quantity` e `balance` são multiplicados pelo `%/100`, `pu` é recalculado a partir dos novos valores.
2. **CashAccount** — o saldo de `db.cashAccounts.values[].value` para a `(walletId, date)` é multiplicado pelo `%/100` e vira uma linha `Caixa=Sim` no upload da posição.
3. **Provisions** — cada provision com `[initialDate, liquidationDate)` cobrindo a data alvo é replicada na carteira de destino com `balance × %/100` via `POST /beehus/provisions`.
4. **Transactions** — cada transação da origem com `liquidationDate == data alvo` é replicada na carteira de destino com `balance × %/100` e `quantity × %/100` via `POST /beehus/financial/transactions`.

A carteira de origem **não** sofre nenhuma alteração. O fluxo só **adiciona** dados na carteira de destino.

### Modelo de dados

```
data/excecoes/<companyId>/<exceptionId>.json
{
  "id":              "uuid",
  "companyId":       "...",
  "name":            "...",
  "kind":            "wallet_slice",
  "sourceWalletId":  "...",
  "targetWalletId":  "...",
  "percent":         30.0,
  "createdAt":   "ISO-8601",
  "updatedAt":   "ISO-8601",
  "lastApplied": {"date": "YYYY-MM-DD", "at": "ISO-8601"} | null
}
```

- `kind` precisa ser `"wallet_slice"` para esta variante.
- `percent` é tratado como percentual (`0 < p ≤ 100`). Internamente vira `ratio = percent/100`.
- `targetWalletId` precisa pertencer à mesma `companyId` da origem **e** ser diferente de `sourceWalletId` (fatiar uma carteira em si mesma resultaria num upload parcial sobre a posição original).
- O endpoint de update (`PUT /api/excecoes/<id>`) **rejeita** trocar o `kind` de uma exceção existente — para mudar o tipo, exclua e recrie. Isso evita silenciosamente apagar campos que só existem em um dos kinds.

### Setup

A modal "Nova Exceção" no `#strip-view` do Painel de Controle ganha um seletor **Tipo de exceção** com dois radios:

- `Position Stripping` (default) — mostra os passos 3–5 históricos (output wallets, data, ativos + regras).
- `Fatiar carteira` — esconde os passos histórico-only e mostra dois campos: **Carteira de destino** (select com as outras carteiras da empresa) e **Percentual** (input numérico `0 < p ≤ 100`, default 30).

A `Empresa`, `Nome` e `Carteira a ser sinalizada` ficam compartilhados entre os dois kinds. O radio fica desabilitado em modo de edição (o backend não permite trocar `kind`).

### Preview

`POST /api/excecoes/<id>/preview` despacha por `kind`. Para `wallet_slice`, devolve diretamente o plano fatiado (sem o envelope `{wallets, transactions, ...}` do `position_strip`):

```jsonc
{
  "kind":             "wallet_slice",
  "percent":          30.0,
  "ratio":            0.3,
  "sourceDate":       "YYYY-MM-DD",   // data efetivamente usada para a posição (com fallback até 5 dias atrás)
  "targetDate":       "YYYY-MM-DD",   // data alvo (do request)
  "fallback":         true | false,
  "sourceWalletId":   "...",
  "targetWalletId":   "...",
  "sourceCurrencyId": "BRL",
  "targetCurrencyId": "BRL",
  "summary": {
    "source": {nav, securities, cashAccount, provisions, transactions},
    "sliced": {nav, securities, cashAccount, provisions, transactions}
  },
  "securities":   [{unprocessedId, sourceQuantity, sourcePu, sourceBalance,
                    quantity, pu, balance, caixa: false, currencyId, walletId}, …],
  "cashAccount":  {unprocessedId, sourceBalance, balance, caixa: true,
                   quantity: 0, pu: 0, currencyId, walletId} | null,
  "provisions":   [{sourceId, description, sourceBalance, balance,
                    initialDate, liquidationDate, provisionType,
                    provisionSource, currencyId, securityId}, …],
  "transactions": [{sourceId, securityId, sourceBalance, balance,
                    sourceQuantity, quantity, liquidationDate,
                    operationDate, description, beehusTransactionType,
                    currencyId}, …],
  "walletNames":   {<walletId>: name},
  "securityNames": {<securityId>: beehusName},
  "targetMeta":    {entityId, currencyId, name}
}
```

A UI mostra:

- **Resumo — origem vs. fatia** (5 cards): NAV, Securities, CashAccount, Provisions, Transactions. Cada card carrega o valor da origem e quanto vai para a carteira de destino (`× ratio`). `cashAccount` aparece como "sem cashAccount" quando o doc não existe para a data.
- **Securities + Cash** — uma linha por security + uma linha amarelinha `Caixa` (quando há).
- **Provisions** — uma linha por provision; replica a coluna "Liquidação" / "Initial" / "Saldo origem" / "Saldo fatia".
- **Transactions** — uma linha por transação com `liquidationDate == data alvo`; mostra ativo, tipo, saldo, quantidade.

### Apply

`POST /api/excecoes/<id>/apply` despacha por `kind`. Para `wallet_slice`:

1. **Etapa 1 — posições**: gera **um único** XLSX com as securities fatiadas + a linha `Caixa=Sim` (sliced cashAccount) e envia para a carteira de destino via `POST /beehus/financial/positions/unprocessed-security-positions/file`. Mesmo endpoint multipart do `position_strip`.
2. **Etapa 2 — provisions**: para cada provision da origem cobrindo `target_date`, faz `POST /beehus/provisions` com `walletId = targetWalletId` e `balance = source.balance × ratio`. `description`, `currencyId`, `provisionType`/`provisionSource`, `initialDate`, `liquidationDate`, `securityId` carregam direto da provision de origem (com fallbacks: tipo → `"adjustments"`, currency → currency da target wallet).
3. **Etapa 3 — transactions**: para cada transação da origem com `liquidationDate == target_date`, faz `POST /beehus/financial/transactions` com `walletId = targetWalletId`, `balance × ratio`, `quantity × ratio`. `entityId` é resolvido a partir de `db.wallets.<targetWalletId>.entityId`; sem `entityId` a linha vira `status: "error"` com razão `"wallet sem entityId"` (mesmo padrão dos ajustes do `position_strip`).

Erros 401 (auth) **interrompem** o pipeline na etapa em que ocorrem — as etapas seguintes não são executadas. Outros erros são acumulados por linha em `provisionResults[]` / `transactionResults[]` e a resposta final vira 502 se houver pelo menos um item com `status != "ok"`. `lastApplied` é gravado **apenas** quando todas as três etapas terminam sem falhas.

Resposta:

```jsonc
{
  "kind":               "wallet_slice",
  "percent":            30.0,
  "sourceDate":         "...",
  "targetDate":         "...",
  "fallback":           bool,
  "positionResult":     {walletId, status, rows, upstream | error | reason},
  "provisionResults":   [{sourceId, description, balance, status, createdId | error}, …],
  "transactionResults": [{sourceId, securityId, balance, quantity, type, status, createdId | error}, …],
  "walletNames":        {...},
  "securityNames":      {...}
}
```

### Download Excel

`GET /api/excecoes/<id>/excel?companyId=&date=` também despacha por `kind` — para `wallet_slice` baixa só o workbook das **posições fatiadas** (security rows + linha Caixa) para inspeção offline. Provisions e transactions não entram no XLSX porque vão via `POST` no apply (não viram linhas de planilha).

---

## Stripping por classe (`class_strip`)

Variante que **strippa por classificação** em vez de por ativo individual. O operador define uma **lista de carteiras de origem** e um mapa `variable1 → carteira destino` (one-to-one). Em cada aplicação, cada origem é varrida; ativos cuja `processedPosition.securities[].hierarchicalVariable.variable1` está no mapa migram para a carteira destino correspondente. Ativos sem rota configurada **não são tocados**.

Diferenças-chave em relação ao `position_strip`:

- Fonte da seleção é a **classificação hierárquica** (`processedPosition`), não o `unprocessedId` individual da posição original.
- A origem pode ser **N carteiras**, todas roteadas pelas mesmas regras. Quando o mesmo `variable1` aparece em múltiplas origens, todas as securities desse `variable1` convergem para a mesma carteira destino.
- O destino é **uma única carteira por classe** — não há multi-saída ponderada como no `position_strip` tradicional.
- Inclui automaticamente ajustes financeiros (`withdrawalDepositAdjustment`) para neutralizar `amountDifference` e contracorrente para settlements na Data Base — não há configuração por ativo dessas regras.
- **`cashAccount` não é tocado** — class_strip movimenta apenas securities + provisions + transactions dos tipos configurados. Se o operador precisar movimentar caixa entre carteiras, deve usar outra ferramenta (Funções > Criar transação, ou wallet_slice).

### Modelo de dados

```
data/excecoes/<companyId>/<exceptionId>.json
{
  "id":             "uuid",
  "companyId":      "...",
  "name":           "...",
  "kind":           "class_strip",
  "sourceWalletIds": ["...", "..."],
  "classRoutes": [
    {"variable1": "Renda Variável", "targetWalletId": "..."},
    {"variable1": "Renda Fixa",     "targetWalletId": "..."}
  ],
  "createdAt":   "ISO-8601",
  "updatedAt":   "ISO-8601",
  "lastApplied": {"date": "YYYY-MM-DD", "at": "ISO-8601"} | null
}
```

- `sourceWalletIds` precisa ser não-vazia; todas as carteiras pertencem a `companyId`.
- `classRoutes[]` precisa ser não-vazia; cada `variable1` é único na lista (não há "rota duplicada" — uma classe vai para exatamente uma carteira). `targetWalletId` pertence à mesma `companyId` e **não** pode coincidir com nenhuma das origens (stripar de uma origem para ela mesma seria no-op com risco de duplicar securities).
- `targetWalletId` **pode** se repetir entre rotas diferentes (várias classes alvo da mesma "carteira coringa") — não há restrição de "destino único".
- O endpoint de update (`PUT /api/excecoes/<id>`) rejeita trocar `kind` da exceção existente — para mudar tipo, exclua e recrie.

### Setup

A modal "Nova Exceção" ganha um terceiro radio **Stripping por classe**. Quando ativo:

1. **Empresa + Nome** — compartilhados com os outros kinds.
2. **Groupings (opcional)** — dual-pane transfer (`#sp-setup-class-grp-*`) **idêntico** ao do *Painel de Controle > NAV Wallets*: lista "Disponíveis" + lista "Selecionados" + botões `→ » ← «` + upload de Excel sem cabeçalho (uma `groupingId` por célula) via `POST /api/beehus/util/parse-strings-excel`. Quando há groupings selecionados, a lista "Disponíveis" da seção de wallets é filtrada pela **união** dos `walletIds` dos groupings escolhidos (pill "grouping" no header indica filtro ativo).
3. **Carteiras de origem** — dual-pane transfer (`#sp-setup-class-wallet-*`) com o mesmo padrão: "Disponíveis" / "Selecionadas" / botões / upload de Excel sem cabeçalho (uma `walletId` por célula). Aceita ambos os tipos de entrada (grouping-filtrado OU Excel direto). Não há campo de data — esta tela só configura o **shape** da exceção; a data alvo é fornecida na aplicação.
4. **Rotas variable1 → carteira destino** — lista de pares (`#sp-setup-class-routes-*`). Para cada rota:
   - `variable1` (input com autocomplete). O autocomplete oferece os valores únicos de `processedPosition.securities[].hierarchicalVariable.variable1` encontrados nas origens selecionadas na Data Base do header (Servido por `GET /api/excecoes/class-strip/variables?companyId=&date=&walletIds=`). O operador pode digitar um valor que não está na lista (free-form) para preparar rotas para classes que ainda vão aparecer.
   - `targetWalletId` — select com todas as carteiras da empresa **menos** as que estão na lista de origens.
   - Botão **+ Nova rota** adiciona uma linha vazia. Botão de remover por linha.

Validação client-side: pelo menos 1 origem, pelo menos 1 rota completa (`variable1` não-vazia + `targetWalletId` selecionada), nenhuma rota com `variable1` duplicada.

### Preview

`POST /api/excecoes/<id>/preview` despacha por `kind`. Para `class_strip` devolve:

```jsonc
{
  "kind":           "class_strip",
  "targetDate":     "YYYY-MM-DD",
  "sourceWalletIds": ["...", "..."],
  "classRoutes":    [{"variable1": "...", "targetWalletId": "..."}, ...],
  "perSource": [{
    "walletId":      "...",
    "sourceDate":    "YYYY-MM-DD",     // data efetivamente usada (com fallback)
    "fallback":      bool,
    "matched":       [{securityId, unprocessedId, variable1, targetWalletId,
                       quantity, pu, balance, puSource: "unprocessed"|"processed",
                       amountDifference, executionPrice}, ...],
    "skipped":       [{securityId, variable1, reason}, ...]   // variable1 sem rota
  }, ...],
  "perTarget": [{                       // agregado por carteira destino
    "walletId":         "...",
    "variable1s":       ["..."],
    "securities":       [{unprocessedId, quantity, pu, balance, currencyId}, ...],
    "provisions":       [{sourceId, sourceWalletId, securityId, balance,
                          initialDate, liquidationDate, provisionType, ...}, ...],
    "transactions":     [{sourceId, sourceWalletId, securityId, balance, quantity,
                          liquidationDate, beehusTransactionType, ...}, ...],
    "adjustments": {
      "amountDifference": [{securityId, amountDifference, executionPrice,
                            balance, description}, ...],
      "provisionSettlement": [{provisionSourceId, balance, description}, ...],
      "transactionSettlement": [{transactionSourceId, balance, description}, ...]
    }
  }, ...],
  "summary": {
    "matchedSecurities":   N,
    "migratedProvisions":  N,
    "migratedTransactions": N,
    "adjustmentTransactions": N      // soma das 3 categorias
  },
  "walletNames":   {...},
  "securityNames": {...}
}
```

**Origem da `pu` em `matched[]`:** o plano lê `processedPosition.securities[]` para identificar a `variable1` + `amountDifference` + `executionPrice` de cada ativo, **mas** consulta também o `unprocessedSecurityPositions` da mesma `(walletId, sourceDate)` e usa o `quantity`/`pu`/`balance` desse documento para a linha matched (são os valores que o operador efetivamente subiu para a Beehus). Quando o `unprocessedSecurityPositions` não tem doc na data, cai de volta para os valores computados do `processedPosition` e marca `puSource: "processed"` no row (a UI exibe um pill "PU proc" amarelo nessa coluna para o operador saber).

A UI mostra:

- **Por carteira de origem** — uma card por origem com:
  - Pill `Data: <sourceDate>` (amarela se `fallback=true`).
  - Tabela de securities matched (variable1 + destino + **PU vindo de `unprocessedSecurityPositions`**).
  - Pill `N ativos sem rota` quando há `skipped` (linhas hidden por padrão, expandível).
- **Por carteira destino** — uma card por destino com:
  - Pill "X securities · Y provisions · Z transactions · W ajustes".
  - Tabela "Securities a receber" (consolidada — soma `quantity`, `balance` quando o mesmo `unprocessedId` vem de múltiplas origens).
  - Tabela "Provisions a migrar".
  - Tabela "Transactions a migrar".
  - Tabela "Ajustes a criar" — três sub-grupos com a fórmula de cada (`amountDifference × executionPrice`, `-balance` para settlement de provision, `-balance` para settlement de transaction).

### Apply

`POST /api/excecoes/<id>/apply` despacha por `kind`. Para `class_strip` o pipeline é:

1. **Etapa 1a — uploads nas origens**: para cada `sourceWalletId`, gera XLSX de `unprocessedSecurityPositions` zerando os `unprocessedId` correspondentes às securities matched (via `securityMappings[securityId → unprocessedId]`). As linhas dos ativos que **não** entram em nenhuma rota são preservadas (a posição é reescrita com base na atual + zerar matched). `cashAccount` **não é tocado**. Upload via `upload_unprocessed_security_positions_file`.
2. **Etapa 1b — uploads nas destinos**: para cada `targetWalletId`, gera XLSX adicionando as securities migradas (somadas de todas as origens que rotearam para ele). Upload via mesmo endpoint multipart.
3. **Etapa 2 — provisions**: para cada provision elegível (`securityId ∈ securities migradas`, `provisionType ∈ {dividend, interestOnEquity}`, `[initialDate, liquidationDate)` cobre `target_date`, `walletId == sourceWalletId`), faz `PATCH /beehus/provisions/<id>` mudando `walletId` para o `targetWalletId` da rota da security. (Patch em vez de delete+create para preservar id e histórico upstream.)
4. **Etapa 3 — transactions**: para cada transação elegível (`securityId ∈ securities migradas`, `beehusTransactionType ∈ {coupon, amortization, securityContributionAdjustment}`, `liquidationDate == target_date`, `walletId == sourceWalletId`), faz `PATCH /beehus/financial/transactions/<id>` mudando `walletId` para o destino da rota.
5. **Etapa 4 — ajustes**: três sub-passos sequenciais, todos via `POST /beehus/financial/transactions` na carteira destino com `beehusTransactionType = "withdrawalDepositAdjustment"`:
   - **(i)** Por security com `amountDifference != 0`: `balance = amountDifference × executionPrice`, `liquidationDate = operationDate = target_date`, `description = "Ajuste classe — diferença " + security.beehusName`.
   - **(ii)** Por provision migrada com `liquidationDate == target_date`: `balance = -provision.balance`, `description = "Ajuste classe — provisão liquidando " + provision.description`.
   - **(iii)** Por transaction migrada com `liquidationDate == target_date`: `balance = -transaction.balance`, `description = "Ajuste classe — tx liquidando " + transaction.description`.
6. **Etapa 5 — processamento**: para cada carteira destino, dispara `POST /beehus/financial/positions/processed-position/process` com `{companyId, positionDate: target_date, wallets: [<targetWalletId>]}` — mesma operação que o chip *Painel de Controle > Processamento* executa por dia/wallet. Garante que a `processedPosition` da carteira destino reflita a posição recém-upload + transactions/provisões migradas antes do passo de NAV.
7. **Etapa 6 — NAV Wallets**: para cada carteira destino, dispara `POST /beehus/consolidation/nav-contribution-calculation/wallets` com o mesmo payload — espelha o chip *Painel de Controle > NAV Wallets*. Recalcula a contribuição NAV da wallet na data alvo. Roda **depois** do processamento porque o cálculo NAV depende do `processedPosition` atualizado.

`entityId` da carteira destino é resolvido via `db.wallets.<targetWalletId>.entityId`; ausência marca os ajustes correspondentes como `status: "error"` com razão `"wallet sem entityId"` (mesma semântica do `wallet_slice`).

Erros 401 (auth) interrompem o pipeline na etapa onde ocorrem. Outros erros são acumulados por linha; resposta final 502 se houver pelo menos um `status != "ok"`. `lastApplied` só é gravado quando todas as etapas terminam sem falhas.

Resposta:

```jsonc
{
  "kind":               "class_strip",
  "targetDate":         "...",
  "sourceResults":      [{walletId, status, rows, upstream | error}, ...],
  "targetResults":      [{walletId, status, rows, upstream | error}, ...],
  "provisionResults":   [{sourceId, sourceWalletId, targetWalletId, status, error?}, ...],
  "transactionResults": [{sourceId, sourceWalletId, targetWalletId, status, error?}, ...],
  "adjustmentResults":  {
    "amountDifference":      [{walletId, securityId, balance, status, createdId?, error?}, ...],
    "provisionSettlement":   [{walletId, balance, status, createdId?, error?}, ...],
    "transactionSettlement": [{walletId, balance, status, createdId?, error?}, ...]
  },
  "processResults":     [{walletId, status, upstream?, error?}, ...],
  "navResults":         [{walletId, status, upstream?, error?}, ...],
  "walletNames":   {...},
  "securityNames": {...}
}
```

### Download Excel

`GET /api/excecoes/<id>/excel?companyId=&date=` para `class_strip` baixa um workbook único com **uma aba por carteira tocada** (origens + destinos), cada aba contendo as linhas que serão enviadas para aquela carteira (securities zeradas/preservadas na origem; securities adicionadas no destino).

### Endpoint auxiliar — discover variable1

`GET /api/excecoes/class-strip/variables?companyId=&date=&walletIds=w1,w2`:

Retorna os valores únicos de `processedPosition.securities[].hierarchicalVariable.variable1` encontrados nas carteiras informadas na data alvo (com fallback para a posição processada mais recente quando `date` não tem doc). Usado pelo autocomplete do wizard ao adicionar rotas. Resposta:

```jsonc
{
  "date":     "YYYY-MM-DD",
  "fallback": bool,
  "variables": [{"variable1": "...", "count": N, "totalBalance": N}, ...]
}
```


### Bulk apply

O fluxo de bulk apply (modal "Aplicar selecionadas (N)" + tela de resumo) **funciona igual** para os dois kinds — o front-end identifica `plan.kind` na resposta do `/preview` e renderiza as pills corretas (`% · N ativo(s) · M provision(s) · K transação(ões)` para slice; `N carteira(s) · Y transação(ões) [+ Z ajuste(s)]` para strip). O envio sequencial (um POST `/apply` por exceção, break em 401) é o mesmo, e os blocos de resultado abaixo da tabela renderizam o shape específico de cada kind.

---

## Ajustes day-trade

Botão **"Ajustes day-trade"** no header da página (cor âmbar) **substitui** a tela de listagem por uma view full-screen dedicada, sem persistência em `data/excecoes/`. É uma rotina de **diagnóstico + patch** ad-hoc: detecta transações `buySell` cuja security não chegou a entrar no `processedPosition` da carteira na data e zera as linhas correspondentes em `unprocessedSecurityPositions` para que o pipeline upstream pare de carregar quantidade/saldo fantasma.

A view tem botão **Voltar** (canto superior direito) e Esc para retornar à listagem.

### Fluxo da view (3 passos com checkbox)

1. **Filtro** (card 1): empresa + datas (toggle único/faixa, mesma estrutura de "Funções > Identificar Transações") + filtros opcionais de groupings/wallets em painéis transfer alimentados por `GET /api/beehus/filters/{groupings,wallets}`. Botão **Verificar intraday** → `POST /api/excecoes/intraday/check` retorna apenas `groups[]`.
2. **Day-trades detectados** (card 2, aparece após o check): tabela com **uma checkbox por linha** `(walletId, securityId, date)`. Default = todas marcadas. Botão **Gerar posições patcheadas (N) →** envia a seleção para `POST /api/excecoes/intraday/build-patches`, que recomputa a detecção server-side com o mesmo envelope de filtros e restringe ao subset escolhido. Inclui "Marcar/Desmarcar todos".
3. **Posições patcheadas** (card 3, aparece após gerar): blocos por `(walletId, date)` com **uma checkbox por bloco**. Default = checked **apenas** quando há linhas a zerar (`zeroedUnprocessedIds[]` não-vazio); blocos sem nada a zerar vêm com checkbox desabilitada. Linhas em vermelho são `unprocessedId`s que vão a zero, linhas neutras são o baseline da posição que sobe junto no upload (o endpoint upstream substitui a posição inteira). Logo abaixo, a tabela **"Transações `securityContributionAdjustment` que serão criadas"** mostra a preview das transações planejadas para os groups selecionados (uma por day-trade). Botões:
   - **Baixar Excel** → `POST /api/excecoes/intraday/excel` com `selectedGroups[]` da etapa 2 — workbook único com todas as `(carteira, data)` selecionadas para inspeção offline.
   - **Enviar selecionadas (M)** → `POST /api/excecoes/intraday/apply` com **`selectedGroups[]` + `selectedPatches[]`**. Cria as transações + envia um arquivo XLSX por `(walletId, date)` selecionado via `POST /beehus/financial/positions/unprocessed-security-positions/file`.
4. **Resultado** (card 4): duas seções — **Transações criadas** (`transactionResults[]`) e **Posições patcheadas** (`results[]`), cada uma com status `ok` / `skipped` / `error` / `auth_error`.

### Critérios de detecção

Uma transação só é considerada day-trade se atender **todas**:

| Critério | Origem do dado |
|----------|----------------|
| `beehusTransactionType == "buySell"` | `db.transactions` |
| `trashed != True` | `db.transactions` |
| ≥ 2 transações com mesmo `(walletId, securityId, liquidationDate)` | agregação em memória |
| `securityId` ausente de `processedPosition.securities[].securityId` para `(walletId, positionDate=liquidationDate)` | `db.processedPosition` |
| `Σ balance != 0` (a 2 casas decimais) | agregação em memória |

A penúltima cláusula é o desempate principal: se a security entrou no `processedPosition` mesmo após o round-trip, o pipeline reconhece a posição como real e **não** se trata de day-trade. A última remove rounds-trips perfeitamente neutros (`Σ balance == 0`) — sem contribuição residual não há nada a zerar em `unprocessedSecurityPositions`.

### Patch de posição

Regra do **"Ativo"** das rows day-trade: usa o `beehusName` da security (lido de `db.securities` via `get_security_names()`). Não usa o `unprocessedId` legado (formato longo tipo `BRBMEFDOL819_DOL_K26_2026-05-04`) porque o nome operacional (`DOLFK26`) é o que combina com o resto da superfície da plataforma.

Para cada `(walletId, date)` com pelo menos uma security day-traded:

1. Lê o `unprocessedSecurityPositions` correspondente e agrega por `unprocessedId` (mesmo agrupamento de `pages/posicoes._group_unprocessed`).
2. Identifica os `unprocessedId`s do baseline que mapeiam (via `securityMappings.from→to`) para alguma security day-traded — esses são **removidos** do upload (`replacedUnprocessedIds[]`) para evitar dois nomes para a mesma security lógica no payload.
3. Adiciona **uma** row sintética por `securityId` day-traded, com `quantity = pu = balance = 0` e `Ativo = beehusName`. Currency: a registrada na carteira (`db.wallets.currencyId`); fallback: a `currencyId` da transação.
4. Securities cujo `securityId` não tem `beehusName` em `db.securities` ficam em `unmappedSecurityIds[]` (aviso amarelo) — sem nome não dá para construir a row.
5. As demais rows do baseline seguem **inalteradas** no upload (replace-completo do upstream).

Sinalizadores devolvidos para o front-end:

- `zeroedUnprocessedIds[]` / `addedUnprocessedIds[]`: lista dos `beehusName`s das rows day-trade adicionadas. Cada row aparece com pill azul "adicionar" na UI.
- `replacedUnprocessedIds[]`: `unprocessedId`s do baseline removidos do upload (substituídos pelas rows day-trade).
- `unmappedSecurityIds[]`: securities day-traded sem `beehusName` em `db.securities`. Aviso amarelo.
- `hasPosition: false`: sem doc em `unprocessedSecurityPositions`. Aviso amarelo (a subida cria um doc novo só com as rows day-trade).

### Transação `securityContributionAdjustment` (uma por day-trade)

A apply, antes de subir as posições, cria **uma transação por group selecionado** (chamando `POST /beehus/financial/transactions` via `beehus_api.create_transaction`). Estrutura igual à de `Funções › Criar Transações`, com os campos resolvidos automaticamente:

| Campo | Origem |
|-------|--------|
| `companyId` | empresa do filtro |
| `walletId` | `group.walletId` (carteira do day-trade) |
| `entityId` | `db.wallets.<walletId>.entityId` |
| `currencyId` | `transaction.currencyId` (fallback `db.wallets.<walletId>.currencyId` → `BRL`) |
| `securityId` | `group.securityId` |
| `beehusTransactionType` | `securityContributionAdjustment` (fixo) |
| `description` | `"day-trade " + db.securities.<securityId>.beehusName` |
| `balance` | `Σ balance` das transações `buySell` do group (= "contribuição") |
| `operationDate` / `liquidationDate` | `group.date` |

`build-patches` retorna `transactions[]` (planos com flag `skip` quando a wallet não tem `entityId` ou a security não tem `beehusName`) — a UI mostra a preview na mesma página, em tabela abaixo das posições patcheadas. Skips aparecem como pill amarela com a razão.

**Submissão (mesmo padrão de Correções > Transações > Enviar selecionadas via API):** ao clicar **Enviar selecionadas**, o front-end faz um `POST /api/excecoes/intraday/transactions/submit` por group (sequencial, com progresso `Enviando i/N`), depois um `POST /api/excecoes/intraday/apply` único para subir todos os arquivos XLSX patcheados. Cada per-row submit retorna `{ok, status, createdId, walletId, securityId, date, balance, description, currencyId, entityId, securityBeehusName, error?}`. Um 401 em qualquer call interrompe a sweep — as transações restantes não são tentadas e o upload de posições é pulado.

O servidor revalida o group em cada per-row submit: re-roda a detecção a partir do envelope de filtros e devolve 404 se o `(walletId, securityId, date)` informado não está mais na lista detectada (defesa contra payload do cliente fora de sincronia com o estado real).

### Endpoints

Filter envelope (FE) = `{companyId, initialDate, finalDate, groupingIds[], walletIds[]}`.

| Método | Rota | Body | Propósito |
|--------|------|------|-----------|
| POST   | `/api/excecoes/intraday/check`         | FE | Detecta `groups[]`. **Sem** patches — a UI mostra checkbox para o operador escolher o subset antes do passo 2. |
| POST   | `/api/excecoes/intraday/build-patches` | FE + `selectedGroups: [{walletId, securityId, date}]` (obrigatório, não-vazio) | Recomputa detecção, restringe a `selectedGroups` e devolve `patched[]` + `transactions[]` (preview dos `securityContributionAdjustment`). |
| POST   | `/api/excecoes/intraday/excel`         | FE + `selectedGroups[]` (opcional — sem ele cobre tudo o que foi detectado) | Workbook único `Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda` com as posições patcheadas do subset. |
| POST   | `/api/excecoes/intraday/transactions/submit` | FE + `group: {walletId, securityId, date}` | Cria **uma** `securityContributionAdjustment` para o group informado, via `beehus_api.create_transaction` → `POST /beehus/financial/transactions`. Mesmo contrato per-row de `/api/correcoes/transactions/submit`. Resposta `{ok, status, createdId, …}`. Erros: 400 (skip — wallet sem `entityId` / security sem `beehusName` / group fora da detecção atual), 401 (auth, com `upstream_status`/`upstream_body`), 502 (outros erros do upstream). |
| POST   | `/api/excecoes/intraday/apply`         | FE + `selectedGroups[]` (obrigatório) + `selectedPatches: [{walletId, date}]` (obrigatório, não-vazio) | Recomputa patches a partir de `selectedGroups`, filtra por `selectedPatches`, gera XLSX por `(walletId, date)` e envia via `upload_unprocessed_security_positions_file`. **Não cria transações** (a UI faz isso antes via per-row submit). Resposta: `{results[], groupCount, patchedCount}`. Erros não-auth → 502; auth → 401. |

**Defesa em profundidade**: o servidor nunca confia no estado em cache do cliente — cada passo recebe o envelope de filtros completo e refaz detecção do zero. O cliente apenas declara qual subset do que o servidor detectou ele quer materializar/aplicar.

A validação de path/datas reusa `_safe` e `_safe_date`. Os filtros opcionais (`groupingIds`, `walletIds`) seguem a mesma resolução wallet-set usada em `pages/beehus_console.transactions_search`: `companyId → ∪ groupings.walletIds → ∩ walletIds explícitos`.

---

## Arquivos relacionados

| Arquivo | Função |
|---|---|
| [`pages/excecoes.py`](../pages/excecoes.py) | Blueprint, persistência em `data/excecoes/`, validação, preview, apply, builder `.xlsx` upstream, endpoints `/api/excecoes/*` (incluindo `intraday/*`). Continua sendo a **fonte de verdade** mesmo após o port da UI. |
| [`templates/excecoes.html`](../templates/excecoes.html) | Página `/excecoes` original (list view + setup modal + apply modal + intraday view). Continua disponível como rota fallback. |
| [`templates/controlpanel.html`](../templates/controlpanel.html) | Painel de Controle: chip **Strip** dos grupos *Lançamentos* (`chip-strip`) e da segunda linha de *Pipeline* (passo 4) — ambos chamam `Strip.open(this)`. Painel inline `#strip-view` (irmão de `#daytrade-view`), módulo JS `Strip` (CSS prefixado por `.sp-`, IDs `sp-*`), porta verbatim do fluxo list + setup + apply contra `/api/excecoes/*`. O fluxo Ajustes day-trade já estava portado no módulo `Daytrade`/`#daytrade-view`. |
| [`templates/shell.html`](../templates/shell.html) | Roteamento URL-hash: hash key `excecoes` mapeia para `/excecoes`; o chip Strip do Painel de Controle não navega — atua dentro do shell `controlpanel`. |
| [`beehus_api/positions.py`](../beehus_api/positions.py) | `upload_unprocessed_security_positions_file` — usado pelo `apply` para subir o XLSX por carteira/data. |
| [`data/excecoes/<companyId>/<exceptionId>.json`](../data/excecoes/) | Storage por empresa das exceções `position_strip`. |
