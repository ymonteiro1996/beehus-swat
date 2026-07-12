# Conciliação (Movimentação)

Página em **Validações** que projeta a posição de uma carteira de uma data
**origem** para uma data **alvo** (D+1 dia útil, ajustável) e calcula
**NAV / navPerShare / GAP simulados**. Permite baixar a posição projetada (.xlsx),
enviá-la ao Beehus (unprocessed da data-alvo) e enviar as provisões calculadas;
um modal de diagnóstico compara o calculado com a unprocessed **real** do alvo.

Arquivos: [pages/conciliacao_mov.py](../pages/conciliacao_mov.py),
[templates/conciliacao_mov.html](../templates/conciliacao_mov.html). Registrada em
[app.py](../app.py) e no menu/`PAGES` de [templates/shell.html](../templates/shell.html).

## Tela inicial (Step 1)

Lista as carteiras com **posição não-processada na ORIGEM** (projetáveis) e mostra
o **navPackage do SISTEMA na data-ALVO** — **não** analisa dados da origem.
`GET /api/conciliacao-mov/rows?companyId&date(origem)&targetDate(alvo)`:
`unprocessed_existing_wallets(origem)` ∩ `nav_results(alvo)`. **Colunas:** `[✓]`,
Carteira (+copiar ID), e do **sistema (alvo)**: **NAV, *Cota*, GAP $, GAP %**
(`GAP % = returnNavPerShare − returnContribution`, `GAP $ =
financialValueReturnDifference`). *Cota* é opcional (`col-opt`; botão **Mostrar
colunas**). O grid mostra **todas** as projetáveis (inclui GAP=0, p/ validação); o
campo de limiar só **destaca** em vermelho o GAP % acima dele (não esconde).
Multi-seleção por checkbox (clique / Ctrl / Shift). Datas **Origem** e **Alvo**
(default `alvo = D+1 dia útil`).

**A grade NÃO abre automaticamente:** escolher empresa/datas apenas prepara os filtros;
o operador clica **"Listar carteiras"** (`loadWallets`) para carregar a listagem.
Trocar Origem/Alvo com a grade já aberta **não recarrega sozinho** — o botão muda para
**"Atualizar listagem"** (realce âmbar/stale) até um novo clique. Escolher a empresa
(re)carrega os dados do seletor de escopo (`_loadScopeData`) e dispara o pré-aquecimento
opcional, sem abrir a grade.

**Seletor OPCIONAL de groupings / carteiras** (botão *"Groupings / carteiras"* →
`toggleScopePanel`; espelha *Painel de Controle > Processar*). Dois *dual-pane transfers*
(`sp-pick-*`): **Groupings** (fonte `GET /api/beehus/filters/groupings?companyId` →
`[{id,name,walletIds}]`) e **Carteiras** (`GET /api/excecoes/wallets?companyId` →
`{wallets:[{id,name,currencyId}]}`), ambos com **upload de Excel** de IDs
(`/api/beehus/util/parse-strings-excel`). Selecionar groupings **filtra** as carteiras
*Disponíveis* pela UNIÃO dos `walletIds` (pill "grouping"). O **escopo efetivo** da grade
(`_scopeWids`) = carteiras *Selecionadas*; se nenhuma, a UNIÃO dos groupings selecionados;
**vazio = todas**. É um filtro **client-side** aplicado em `_applyFilters` (a rota `/rows`
segue buscando a empresa inteira) — combina com o filtro de texto e com o realce de limiar.

Após **Movimentar** (carteiras selecionadas, pool ≤3), aparecem as colunas
**NAV sim. · Cota sim. · GAP $ sim. · GAP % sim.** e um botão 🔍 por linha que abre
a **aba de detalhamento** (master→detail, **layout idêntico ao da Posição
Projetada**): some a grade e aparece o detalhe da carteira em. **Ao clicar na 🔍 a
aba abre IMEDIATAMENTE com um spinner** ("Carregando detalhamento completo…") — o
resultado do lote (`_lite`) é reprojetado individualmente (chamada de rede de alguns
segundos) e só então a tela é preenchida; erro cai no corpo do detalhe.

1. **Painel da prévia** (`rp-preview-pane`) — uma **barra NAV compacta** com 3
   snapshots lado a lado (**Anterior (origem)** · **Atual (alvo real)** · **Projetada
   (sim.)**), cada um com até **3 sub-colunas — posição | retornos | GAP** (lado a
   lado, p/ aproveitar o espaço horizontal e reduzir a altura): **posição** = NAV,
   cota e **fluxos** (`inAndOutFlows` — exibido tanto na Atual quanto na Projetada);
   **retornos** = rent. NAV, rent. contrib.; **GAP** = GAP %, GAP R$.
   Ao lado da Projetada, um 4º card **GAPs por etapa** (`gapStages`, verde quando ≈0 =
   etapa auto-contida; âmbar quando há resíduo) decompõe o GAP por etapa da projeção
   p/ **localizar de onde ele vem** (identidade `GAP$ = simNav − inflows − formerNav −
   contribuição`, linear em `simNav`): **mov** = GAP da projeção (posição-origem +
   movimentos + provisões), ANTES da adoção amDiff — o resíduo da projeção em si
   (estimativa de qtd, IRRF de fundo); **amDiff** = incremento da adoção da qtd-alvo
   (reconProvision/coveredByProvision) — **≈ 0 por construção**: a adoção soma o P&L
   intraday `Δqty×(PU−execPrice)` ao NAV (cota ao PU + provisão ao execPrice) **e o mesmo
   valor à contribuição** (`intradayContribution`), então NAV e contribuição sobem juntos
   e o GAP não muda; ≠0 só se a neutralidade quebrar; **caixa** = caixa PROJETADO − caixa OFICIAL
   (**INFORMATIVO** — caixa-âncora eliminado; não classifica ajustes) — **não é GAP de retorno**,
   é só o resíduo de caixa exibido (`—` em forward, sem caixa oficial). Identidade: `mov + amDiff = simGapCash`.
   **What-if de provisões ignoradas (`_whatIfProj`):** quando há provisão(ões) ignorada(s)
   no Bloco Provisões, o card **Projetada (sim.)** recalcula NAV/cota/rent. NAV/GAP **ao vivo**
   (fica âmbar `det-proj-whatif` + "what-if", com nota "N prov. ignorada(s) · Δ NAV"). O
   recálculo subtrai do `sim_nav` a **contribuição-ao-NAV** de cada provisão ignorada —
   MESMA regra do servidor (`_official_prov_in_nav_total`): motor e recon entram pelo saldo;
   oficiais só se **não** deduplicadas; **liquidando na data estão FORA do NAV** (contribuição 0).
   `returnContribution` **não** muda (provisão não entra na contribuição), então o GAP move-se
   pelo ΔNAV inteiro. É **só exibição — NÃO grava nada** e reseta ao trocar de carteira; a grade
   e o payload seguem com a projeção real. Logo abaixo, uma barra de **Ativos** com o contador
   `mostrados/total` e o botão **Filtrar / Filtrado** (funil; **ligado por padrão**,
   `fb-chip.active`) que aplica o **mesmo critério da tela de Conciliação**
   (`_secHasActivity`): só ativos com **atividade real** — divergência de qtd/saldo
   vs alvo além da tolerância, transação na janela, vencimento, cupom/amort,
   não-mapeado ou ativo de um só lado. Por fim a **tabela de securities** (`rp-tbl`,
   estilo da prévia): colunas anterior → **alvo** (Qtd da unprocessed real; **PU/Saldo
   ao PU em uso**, ver Passo 8) → **projetada** + **Δ Qtd** e **Diff vs alvo** (sem coluna
   de SecurityId — o `securityId` é copiável por um **botão 📋 ao lado do nome** do ativo),
   mais a linha-apêndice de **Caixa** (banda verde, com badge neutro *resíduo R$…* = Δ vs
   caixa oficial, **informativo** — sem verdito bate/não bate) — **sempre visível, fora do filtro**. As **provisões NÃO entram nesta
   tabela** (apareciam como linhas-apêndice; removidas p/ não duplicar) — ficam só no
   **Bloco Provisões** dedicado abaixo. Linhas pintadas: divergência
   de qtd = borda vermelha (`rp-row-qty-mismatch`), vencido = texto vermelho,
   *só no movimentado* / *só no alvo* por flag. (Divergência só-de-PU não ocorre mais — o
   PU bruto da unprocessed não é comparado.) **A `Δ Qtd` é `Qtd projetada − Qtd alvo`.**

   **Colunas de MÉTRICAS (opcionais, nascem OCULTAS)** — logo após "Saldo projetada",
   um grupo `.mov-extra` com 5 colunas, ligado/desligado pelo botão **Métricas** (ao lado
   do **Filtrar**; toggle ao vivo via classe `.det-hide-extra`, sem re-render): **Contrib $**
   = `contribution` do ativo (R$); **Rent PU** = `PU projetada ÷ PU anterior − 1`; **Rent
   Contrib** = `contribuição ÷ NAV origem` (fatia do ativo no `returnContribution`); **Transac**
   = Σ saldo das `transactions` da janela do ativo; **Prov** = Σ saldo das provisões do ativo
   (motor + oficiais + recon + liquidando). Contrib $/Rent PU/Rent Contrib aparecem **só no
   lado projetado** (o alvo não tem contribuição/rentabilidade por ativo — só qtd/PU/saldo);
   Transac/Prov são valor único do ativo. **Tudo derivado no front** a partir do payload da
   projeção — sem chamada extra ao backend.
2. **Bloco Provisões** (`<details>`): lista, no mesmo bloco, **(a)** as provisões
   **já no sistema** (`officialProvisions`, envelope do alvo — pill verde "no
   sistema") — **ativas no alvo e somadas ao `sim_nav`** (`officialProvisionsInNav`),
   pois o NAV oficial as inclui; o motor as soma quando **não** as deriva da janela
   (dedup: pula as já em `prov_total` por securityId e as `coveredByProvision`);
   **(b)** as que **LIQUIDAM na data-alvo** (`liquidatingProvisions`, pill
   "liquida na data · fora do NAV") — vêm do **envelope da ORIGEM** (no
   alvo já liquidaram → saldo virou caixa, **não** entram no NAV simulado nem em
   `officialProvisionsInNav`, pois o envelope do alvo só traz `ini <= data < liq`), só
   contexto/reconciliação. **Cada uma é CASADA com uma liquidação de caixa** da janela
   (`_match_liquidating_provisions` → `matchStatus`/`matchTxnId`/`matchBalance`/`matchBecause`):
   uma provisão de liquidação futura, ao liquidar, tem que ser igualada por uma transação de
   **mesmo sinal** e **valor semelhante** (min/max dos saldos ≥ `_LIQ_MATCH_RATIO` = **90%**,
   ±10%) — p/ equilibrar o NAV. **QUALQUER** `beehusTransactionType` conta. Estados (pill +
   borda esquerda da linha): **🟢 verde `caixa casado`** = há transação do **mesmo `securityId`**
   e valor ≥90% (provisões de `buySell`/`dividend`/`interestOnEquity` carregam securityId); **🟡
   amarelo `caixa provável`** = há transação com valor ≥90% mas o `securityId` **não** bate — ou
   a provisão afeta a wallet **sem ativo** (aí o balance é só indício); **🔴 vermelho `sem caixa`**
   = nenhuma transação de mesmo sinal dentro dos 90% (pendência — some no chip
   `N liquidando na data (X sem caixa)`). Casamento **1:1 guloso** (melhor proximidade primeiro):
   a transação casada é **consumida**. Transações contra "Liquidação B3" (`_B3_LIQ_SECURITY`) ficam
   FORA do 1:1 — as **stockETF** liquidam CONSOLIDADAS (Σ provisões × Σ buySell B3); se a soma casa
   (≥90%), as stockETF vermelhas viram amarelas ("consolidada B3"). **Futures fora de escopo por ora.**
   **(c)** as de **liquidação futura** (Passo 6); **(d)** as
   **A GERAR** (ajuste, linha vermelha + pill "a gerar · ajuste"). Provisões de
   **stockETF** ganham a tag `stockETF`. **Coluna final `NAV` (what-if, #5):** cada linha
   que está NO `sim_nav` (motor, recon, oficial não-deduplicada) tem um botão **⊘ ignorar /
   ↩ considerar** que a exclui/reinclui do cálculo de NAV/GAP da projetada (recálculo ao vivo,
   **não grava** — ver `_whatIfProj`); a linha ignorada fica riscada. As que estão **fora do NAV**
   (liquidando na data, oficiais deduplicadas) mostram o rótulo "fora do NAV" (ignorar não muda
   nada). A chave da provisão (`_provKey`: `bucket|securityId|initialDate|liquidationDate|type|
   round(balance)`) e sua contribuição-ao-NAV ficam em `_detProvByKey`. A coluna **Descrição** mostra o `description`
   OFICIAL da provisão (ex.: `"TED ... APLICAÇÃO FUNDOS ARX FUJI..."`) — propagado por
   `_prov_entry`; fallback para `provisionSource` (`title`) só quando vazio. (Antes as
   linhas oficiais/liquidando exibiam o `provisionSource` — `"adjustments"` — na coluna.) A **liquidação consolidada da B3**
   (`stockEtfLiquidation`) compara Σ provisões stockETF que liquidam × Σ buySell da security
   "Liquidação B3" (`_B3_LIQ_SECURITY` `6a3fd49986ea629551686213`) → **resíduo** (`diff` +
   gainsExpenses + **ajustes-B3** já lançados — `b3AdjustTotal`). **"Liquidação B3" NÃO é uma
   posição:** é um ativo GENÉRICO de liquidação/ajuste, então **não vira linha da tabela de
   securities** (pulado no laço de `rows`) **nem entra no diff** (fora de diverged/onlyCalc/
   onlyReal e dos counts) — suas transações (o buySell de liquidação, o
   `contributionAdjustment`) são settlements/ajustes e aparecem só nos blocos de
   **Transações/Provisões**. O **caixa** desse buySell segue contado (laço de transações,
   com o marcador omit-do-caixa) e o resumo acima é apurado em `_diagnose` — ambos
   independem da linha. **A correção sugerida NÃO
   tem mais bloco-resumo próprio:** ela aparece como uma **transação "a gerar" no bloco de
   Transações** (pill "a gerar · stockETF" + botão **"Aprovar"** na linha; o texto explicativo
   é um **tooltip fixo**, sem números). Aprovar → cria um `contributionAdjustment` **no
   ativo "Liquidação B3"** (cash-neutro: `_CASH_EXCLUDED_TYPES`, não move o caixa). **O resíduo
   sugerido (−resíduo) já entra no `returnContribution` simulado** (`wallet_contrib`) — o GAP
   reflete a correção no preview, antes de aprovar. **Idempotente:** após criado, o ajuste
   entra no `b3AdjustTotal`, o resíduo zera e a sugestão some (o fold da contribuição também
   vai a zero → sem dupla contagem). **(Opção 2, só informativo — o
   sistema NÃO faz)**: voltar à `initialDate` das provisões, lançar o `executionPrice`
   correto, ajustar o `balance` e recalcular o NAV. Quando uma divergência de qtd
   **já é coberta por provisão oficial**, o recon NÃO duplica e o ativo ganha a flag
   **"coberto por provisão"**.
3. **Bloco Transações** (`<details>`): lista as transações **da janela** **e as
   transações A GERAR** (só **resgate no VENCIMENTO** — a divergência de qtd comum vira
   provisão, não transação) **+ o IRRF a criar**
   (`taxes`) **no mesmo bloco**, com as geradas/ausentes **pintadas para alertar**
   (linha vermelha + pill "a gerar · resgate venc." / "a gerar · IRRF"); IRRF já lançado
   aparece em verde ("já lançado"). Cada transação **da janela** tem na coluna
   **"Caixa"** um botão **"desconsiderar"** (`toggleTxnCash`): exclusão **SÓ NA
   SESSÃO** do **caixa projetado**. Mantém um conjunto local (`_omitCash[walletId]`)
   de ids de transação que é enviado ao `POST /movimentar` em `omitCashTxnIds[]`; o
   projetor não soma o `balance` dessas transações no `all_cash` (vale p/ QUALQUER
   tipo; os demais efeitos seguem — gainsExpenses/rebate continua na contribuição,
   flows continuam em inAndOutFlows). **NADA é gravado no Beehus** — o conjunto zera
   ao trocar de empresa/data e ao recarregar. Usado p/ tirar do caixa um valor que o
   `cashAccounts` oficial já reflete por outra via (ex.: a B3 liquidou o líquido). A
   linha omitida fica riscada (badge "fora do caixa ✕"); o botão re-projeta a carteira
   (com `useCache`, quase instantâneo) e re-renderiza o detalhe. Durante a reprojeção
   (e na criação do gainsExpenses) um **status no cabeçalho do detalhe** (`_detStatus`,
   `#det-status`) mostra "…recalculando…/criando…" com spinner, depois ✓ ou erro — p/ o
   usuário acompanhar e não achar que travou.
   Cada transação **da janela** também traz, na coluna **"Origem"** (ao lado do chip
   "janela"), **badges "o que o motor fez com ela"** (`_effChips` ← `t.effects`, montado
   no motor por **`_txn_effects`**): um chip por **bucket do cálculo** que a transação
   alimentou — **quantidade** (buySell soma na projeção), **cupom/amort.** (evento na
   contribuição), **fluxo de capital** (aporte/resgate/transferência/taxas — muda cotas,
   sai do NAV/cota), **contribuição** (P&L/taxa/ajuste; ajuste NÃO move caixa),
   **resgate de principal** (caixa positivo que reduz o resíduo do guard de vencimento),
   **liquidação B3** (ativo consolidado) e **caixa** (Σ do caixa projetado). Uma mesma
   transação pode ter vários chips (ex.: venda `buySell` = quantidade + resgate de
   principal + caixa); a lista **espelha exatamente** os conjuntos de tipo consumidos em
   `_aggregate_window_txns`/`_project_rows`/eventos, e cada chip tem tooltip com a regra.
4. **Bloco Preços de execução** (`<details>`, **sempre visível**): lista os preços de
   execução (capturados do sistema) **da DATA-ALVO** dos ativos com record/trade — não só
   os placeholder. (Só os de `positionDate == alvo`: o intraday do dia-alvo não pode usar
   execPrice de outra data.) Colunas: execPrice atual · PU · execPrice calculado ·
   **Δ Contrib. (intraday)** · Situação. A coluna **Δ Contrib. (intraday)**
   (`intradayContribution`) mostra `(qtd − qtd anterior) × (PU − execPrice em uso)` — o
   **impacto na contribuição** desse preço de execução (o termo *intraday* da fórmula 5):
   num placeholder ≈PU o intraday era ~0, então é exatamente quanto a **correção
   acrescenta ao `returnContribution`** (já refletido no GAP do preview, pois o derivado
   já entra na contribuição). Cada linha tem status: **`ok`** (execPrice real ≠ PU — só referência),
   **`== PU`** (placeholder, apenas repetiu o PU) ou **`ausente`**. As duas últimas
   (`isFix`) são recalculadas de `-Σbalance/amountDifference` e **pintadas para
   alertar** (pill "atualizar" = PATCH no record / "criar" = POST). O botão "Enviar
   preços de execução" só envia as `isFix`.

Botão **← Voltar à grade** (ou Esc) retorna. Backend devolve `transactions` (visão
da janela), `reconProvisions`/`reconTransactions` (correções), `irrf`,
`executionPrices` (todos, p/ o bloco) e `executionPriceFixes` (subconjunto isFix) no
movimento; a aba é **somente leitura** — o envio é pelos
botões da grade (**Enviar provisões / Enviar transações / Enviar IRRF / Enviar
preços de execução**).

## Algoritmo de projeção (`_build_movement_for_wallet`)

Tudo calculado **neste projeto** (não no sistema origem):

1. **Base:** `unprocessed_doc(origem)` agregada por `securityId`
   (`conciliacao_unprocessed._aggregate_positions`), **unida aos `securityId`
   transacionados na janela** — ativos comprados/aportados depois da origem
   entram com `former=0` (senão ficariam de fora da projeção).
   **Enriquecimento BIDIRECIONAL origem↔alvo (`_uid_to_pp` + `_enrich_secs`):**
   o pré-processamento de **uma das datas** (origem OU alvo) às vezes vem sem
   `securityId` resolvido (`preProcessingData` com campos nulos — posição
   importada mas ainda não pré-processada na data). Como o `unprocessedId` é
   **estável por ativo entre datas**, a data RESOLVIDA doa a resolução à não
   resolvida (copia `securityId`/`pricingType`/`beehusName`/`mainId`/`pricingId`).
   Sem o lado **ALVO** deste enriquecimento, um alvo não pré-processado fazia o
   `diff` perder a contraparte real e apontar **toda** a carteira como
   `onlyCalc`/`onlyReal` (revisão manual em massa) — **bug recorrente** (ver
   Scenario TG/TF). Sem o lado **ORIGEM**, todo ativo da origem cairia em "não
   mapeado" (só o `unprocessedId`).
   **Safety net — fallback p/ a processed-position:** se, mesmo após o
   enriquecimento, a unprocessed do alvo continuar **sem nenhum `securityId`**
   resolvido (a origem não resolve aqueles `unprocessedId`), o motor usa a
   **processed-position do alvo** (`_rows_from_env`) como contraparte OFICIAL do
   `diff` — `securityId` + quantidade autoritativos. O campo `targetSource`
   (`"unprocessed"` | `"processed-fallback"`) registra a procedência; a UI mostra
   um aviso azul quando o fallback dispara. Garante que um alvo não pré-processado
   **nunca** vire revisão em massa quando a processed-position existe.
2. **Quantidade — regra ÚNICA p/ TODO ativo (inclusive fundo):**
   `qty projetada = qty da ORIGEM + Σ quantidade MOVIMENTADA` por transação de
   quantidade (`buySell`) da janela `(origem, alvo]`. A **quantidade movimentada** de
   cada transação segue esta PRIORIDADE de divisor:
   1. `quantity` da transação (quando vier preenchida — ≈0% desta base);
   2. `−balance / executionPrice` **capturado** (endpoint execution-prices) — **só
      quando o execPrice difere do PU** (= foi inputado de verdade; quando ≈ PU é
      placeholder, ignora e cai no 3);
   3. `−balance / PU da POSIÇÃO ATUAL` (processed-alvo → unprocessed-alvo);
   4. `−balance / PU do securityPrices` na data-alvo (se houver `securityId`);
   5. `−balance / former_pu` (PU da origem);
   6. `−balance / 1` (assume PU = 1) — último recurso.
   Sinal: compra/aplicação (`balance<0`) aumenta a qtd; venda/resgate (`balance>0`)
   reduz. `split/inplit` aplicam `factor` de `securityEvents` por cima.
   **Cota FUTURA — não soma posição (navDate > alvo, jul/2026):** uma `buySell` cuja **data
   da cota** (`navDate = operationDate + subscriptionNAVDays/redemptionNAVDays dias úteis`,
   `_txn_nav_date`) é POSTERIOR ao alvo **NÃO entra na quantidade** — a cota ainda não existe
   na posição (ex.: subscrição de fundo com `subscriptionSettlementDays=0` + `subscriptionNAVDays=1`:
   o caixa já saiu, a cota só precifica no dia seguinte). Só as `buySell` `position-effective`
   (`navDate ≤ alvo`) somam qtd; se a única transação do ativo for future-nav, a linha nem é
   criada. Em vez de POSIÇÃO, o motor cria uma **provisão de subscrição em trânsito** (Passo 6b,
   `provisionSource="subscription"`, `balance = −txnBalance` = a receber), que nasce
   `duplicatesOfficial` (pré-ignorada) quando já há provisão oficial igual por valor+tipo (§8.6)
   — senão a projeção mostraria cotas que o alvo não tem (`onlyCalc` + GAP). SEM `operationDate` a
   transação é tratada como cota PRESENTE (não usa o `liquidationDate` como proxy — é settlement,
   pode ser futuro sem que a cota seja; ver Scenario B).
   **NÃO há mais** via "qtd-do-alvo" para fundo nem `amountDifference` (Δqty entre
   processed-positions) no cálculo da quantidade — o `amountDifference` só serve para
   criar **provisão** quando NÃO há transação associada à security.
   **Fundo (brazilianFund):** segue a MESMA regra. Como o `balance` do resgate vem
   **líquido de IRRF**, o divisor (PU) sobre o líquido deixa um **resíduo = IRRF** na
   quantidade projetada (esperado): resgate parcial fica levemente acima da qtd-alvo e
   o resgate **total NÃO zera** (deixa o IRRF em cotas). Esse resíduo aparece como
   divergência no diff e é **explicado** pelo bloco de IRRF (passo 6.5); no NAV o
   resíduo (+Δqty×PU) tende a **compensar** o IRRF ausente (−), mantendo o GAP ~0. O
   fundo continua **fora** da visão de preços de execução (não tem execPrice a corrigir).
   Ativo só transacionado (não na origem) entra com `former=0`; ativo da origem sem
   transação repete a qtd da origem.
   **Filtro de origem:** ativo **já zerado na origem** (qtd 0 **e** saldo 0 — vencido/
   liquidado numa data anterior; a posição-origem ainda mostra a linha, mas zerada) e
   **sem transação** que o reabra é **descartado** da projeção (não vira linha). Ativos
   NOVOS (só transacionados) continuam.
   **Filtro de alvo (posição oficialmente fechada):** ativo que **sumiu da unprocessed-alvo**,
   **sem transação de quantidade** na janela, **não vencido** (vencimento tem passo próprio) e
   cuja **`processed-position` do alvo (autoritativa) o lista com qtd ≈ 0** é **descartado** —
   o sistema já encerrou a posição; projetar a qtd da origem fabricaria um `onlyCalc` falso
   (ex.: **futuro** liquidado/ajustado sem `buySell` na janela — caso real "Futuro Mini Dólar -
   Jun/2026", origem −30 → some do alvo, processed-alvo qtd 0). **Só descarta COM confirmação
   oficial** (o ativo a 0 no processed-alvo); em **forward** (sem processed-alvo, ou ativo
   ausente dele) **mantém + sinaliza** a divergência (`onlyCalc`) normalmente.
   **Adoção do alvo (espelho INVERTIDO — `adoptedFromTarget`):** ativo presente **só na
   posição-ALVO** (ausente da origem e de transação na janela) mas **confirmado na
   `processed-position` do alvo com qtd ≠ 0** é **copiado** p/ a projeção (qtd/PU/saldo da
   `unprocessed`-alvo, flag "copiado do alvo") — entrou por `securityTransfer`/reestruturação
   que o motor não deriva; sem isto sumia como `onlyReal` e o `/apply` o perderia. **Regra de
   qtd UNIFICADA:** só fica de fora quando a qtd oficial é **0** (aí o filtro de fechamento
   cuida); qualquer qtd **≠ 0 — inclusive NEGATIVA** (futuro/vendido a descoberto) — é adotada.
   Adotado entra no `sim_nav` pelo **saldo** mas **sem P&L** (contribuição 0; não é compra).
   Forward (sem processed-alvo) → não adota → segue `onlyReal`. Caso real `69b99c5a` 12→13/mai
   (Investo ETF 200, CDB BS2 50): `simNav` 928k→1.002k, GAP −84.588→−10.245 (resíduo = o
   `securityTransfer` de reestruturação, não modelado nas posições).
3. **Vencimento (no período):** `maturityDate <= alvo` → `quantity=0`, `balance=0`;
   o ativo **permanece** (qtd 0) para registrar a baixa, e o saldo de origem vai ao
   caixa (passo 5). O resgate no vencimento entra na **contribuição intraday** como
   uma "venda" de `formerQty` ao **preço de execução** do dia (passo 7): `intraday =
   (0 − formerQty) × (PU − execPrice) = formerQty × (execPrice − formerPU)`. O `PU`
   da linha fica em `formerPU`, então a P&L do resgate é atribuída ao intraday.
4. **PU:** **fonte primária = PU OFICIAL da `processed-position` da data-ALVO**
   (`_pu_by_sid_from_env(tgt_env)`; rótulo `<pt>·proc`/`PROC`) — é o preço autoritativo do
   sistema. O `tgt_env`/`src_env` (`beehus_catalog.processed_envelope`) é UMA leitura por
   data que serve PU + quantidade (amountDifference) + caixa oficial + provisões.
   Resolve a diferença de NAV em ativos **B1/B2/C1/C2**: a `unprocessed`-alvo **não carrega**
   o PU desses (cairiam no `securityPrices` e divergiriam levemente do oficial). No cenário
   **forward** (alvo **não** processado → `proc_pu` vazio) o sistema cai na cadeia:
   (1) PU da **`unprocessed` da data-ALVO** (snapshot — cobre o `C3`; rótulo `<pt>·alvo`);
   (2) `security_prices_resolved` (historyPrice na data-alvo, escopo C3→…→B1) — primário p/
   `B1/B2/C1/C2` (rótulo **`<pt> · securityPrices dd/mm`**, ex.: `B1 · securityPrices 30/06`)
   **e** o fallback p/ ativo **SEM `pricingType`** que saiu do snapshot (rótulo
   `securityPrices dd/mm`): regra **"processed primeiro; securityPrices só sem processed"**.
   O rótulo é EXPLÍCITO na coluna *Pricing* p/ o operador ver a procedência (cota da curva
   capturada na data-alvo vs. repetição — tarefa da UI).
   Casa por data ESTRITA. Caso central: **fundo VENDIDO POR COMPLETO** — saiu da `unprocessed`-alvo
   e a `processed`-alvo tem PU 0, então a cota da data-alvo do `securityPrices` é a ÚNICA fonte do
   PU bruto (captura a valorização do dia no `daily`). *(O fetch em lote pode deixar o fundo de fora
   por limite de requisição → busca-se esses poucos ativos DIRETO p/ completar o `price_hp`.)*
   (3) **repetir** PU da origem (rótulo **`<pt> · repetido origem`**, ex.: `B1 · repetido origem`;
   sem `pricingType` → `REPETIDO`; com PU inverso ao fator de split/inplit p/ preservar saldo). **O `formerPu` (origem) também usa o PU
   OFICIAL da `processed-position` da ORIGEM** (`src_proc_pu`) — alinha a **contribuição diária**
   (`formerQty×(PU−formerPU)`) ao `dailyContribution` oficial; forward/origem não processada →
   mantém o da unprocessed-origem. **O `formerBalance` NÃO é recomposto** — fica o snapshot REAL
   da unprocessed-origem (usado no principal de vencimento `matured_cash`, no filtro de posição
   zerada e no display/diff; a contribuição não o usa). Vem do MESMO envelope (`src_env`) que
   serve caixa/provisões/amountDifference da origem → sem leitura extra. **Efeito:** com PU-alvo e formerPu ambos
   do oficial, a projeção reconcilia com o NAV/retorno oficiais — no caso real (`69b99c57`
   02→03/jun) o GAP fechou de −1.997 para **0,00**.
5. **Caixa:** `caixa(origem) + Σ transações`. **Ajustes de contribuição fora do caixa:**
   `securityContributionAdjustment` e `contributionAdjustment` (`_CASH_EXCLUDED_TYPES`)
   **NÃO** somam ao `all_cash` (o `cashAccounts` oficial não os reflete — incluí-los gerava
   resíduo espúrio no caixa) **e entram na CONTRIBUIÇÃO** (via `wallet_contrib`, ver
   passo 7): são o par contábil do P&L de vencimento que entra no caixa via txns `maturity`
   mas não na marcação (que usa `former_pu`) — sem isso abriria um GAP (caso real 6a0f558b
   08→11/mai: GAP$ 341,09 = Σ ajustes). Ativo vencido devolve o principal —
   soma ao caixa só o **RESÍDUO** ainda não lançado = `max(0, former_bal − Σ caixa de
   resgate já lançado p/ o ativo)`, onde "caixa de resgate" = balances POSITIVOS de
   `_MATURITY_REDEMPT_TYPES` (`maturity`/`buySell`/`amortization`/`securityTransfer`/
   `withdrawalDeposit`). Assim (P2-10): resgate lançado por **qualquer** desses tipos
   **não duplica** (antes faltava `maturity` → o resgate no vencimento lançado como
   `maturity` era contado 2× — txn `maturity` em `all_cash` + `matured_cash` — dobrando o
   caixa e distorcendo o resíduo de caixa/GAP; e o whitelist
   `{buySell,amortization}` perdia transfer/deposit); amortização **parcial** devolve o
   resto no vencimento; cupom/taxes/dividendo (não-principal) não reduzem o resíduo.
6. **Provisão de LIQUIDAÇÃO FUTURA:** o `liquidationDate` da transação **É o dia do
   SETTLEMENT** (o caixa entra/sai nele; o `navDate` fica `offset = settlementDays − NAVDays`
   dias ANTES). A provisão só é criada quando o settlement é **FUTURO em relação ao alvo**
   (`liquidationDate > alvo`) — aí o caixa ainda não liquidou e a provisão (`balance = −caixa`,
   datas via `_prov_dates`) o difere no NAV simulado. **Como as transações são buscadas por
   `liquidationDate ≤ alvo`** (`transactions_search` `date_type="liquidation"`), na prática o
   caixa **já liquidou na janela** (entra no `all_cash`) e **NÃO se cria provisão** — provisionar
   dobraria o caixa. ⚠ **Bug corrigido (jun/2026, caso real RIZA LOTUS):** antes a provisão era
   criada p/ QUALQUER offset≠0, datada `alvo+offset`, e **cancelava o caixa real** (resgate de
   R$ 200.700 liquidando NO alvo) → **GAP espúrio de ~−200k**. Agora exige `liquidationDate >
   alvo`. (Ramo era "sem dado p/ exercitar"; a 1ª transação real expôs o erro.)
6.5. **IRRF de resgate de fundo (brazilianFund):** o `balance` do resgate é
   **líquido** (bruto − IRRF). `IRRF = Σbalance + amountDifference × PU_bruto`
   (negativo = imposto), só para **resgate** (`Σbalance > 0`); `amountDifference`
   bruto vem da reconciliação (qty_alvo − qty_origem). **Dois casos:** resgate
   **PARCIAL** (fundo SEGUE na `unprocessed`-alvo) → `PU_bruto` = cota do snapshot-alvo,
   qty_alvo real; resgate **TOTAL** (fundo SAIU do alvo) → `PU_bruto` = **PU resolvido da
   linha** (cota do `securityPrices` na data-alvo, ver Passo 4, já que a `processed`-alvo
   tem PU 0), qty_alvo = 0. Sem o caso TOTAL o resgate completo caía no fluxo de execPrice
   (que fundo não tem); agora vira IRRF. *(Se o líq ≈ bruto, o IRRF dá ~0 e é filtrado — não
   houve retenção; a diferença foi só valorização da cota, capturada no `daily`.)* **Coberto** = `|Σ taxes/
   bzFundTaxes lançados − IRRF| ≤ max(_IRRF_ABS_TOL, |IRRF|×_IRRF_REL_TOL)` — compara
   contra a **SOMA** dos taxes (não por-transação) e com banda **estreita (1%)**.
   (Antes: 10% e por-transação → discrepância grande passava "coberta" e IRRF lançado
   em 2 parcelas era duplicado.) **IRRF é sempre débito (`< 0`):** valor **≥ 0** (típico
   artefato de **vários resgates com PUs diferentes** agregados a um PU único) **NÃO é
   proposto** — e a rota `/irrf` nunca cria um `taxes ≥ 0` (defesa: seria "restituição").
   Janela com **mais de uma data de resgate** marca `multiEvent` + alerta **"⚠ revisar"**
   (IRRF agregado pouco confiável). Sem cobertura → IRRF **ausente** (`covered:false`),
   proposto para criação. A entry leva `taxSum`/`multiEvent`/`because`. **Não** é somado
   de novo ao NAV (a qtd de fundo já vem bruta do alvo); é um `taxes` a criar.
6.6. **Provisão de DIVIDENDO/JCP (securityEvents):** para cada `securityEvent` de
   `cashDividend`/`interestOnEquity` com `operationDate == alvo`, gera uma provisão de
   **recebível**: `balance = qtd-ORIGEM × balance-por-cota` (sinal `+` = a receber),
   `initialDate = operationDate` (dia-ex), `liquidationDate = liquidationDate` do
   evento, `provisionType = "dividend"`/`"interestOnEquity"`,
   `provisionSource = "corporate-actions"`. **Entra no NAV simulado** (em
   `provisions[]`/`prov_total`) e **pareia** o rendimento que o evento credita na
   contribuição (`div_ps·fq`, passo 7). Sem ela, o `daily` negativo do drop
   ex-dividendo ficaria sem contrapartida no NAV → **GAP de um dia = −div_ps·fq**;
   com ela o GAP do dia-ex fecha por construção. Diferente do Passo 6, **não** inverte
   o sinal (não há caixa lançado a estornar — o caixa do dividendo só entra na
   liquidação). **Guardrail anti-duplicação:** se o sistema origem **já tem** uma
   provisão OFICIAL de `dividend`/`interestOnEquity` para o mesmo ativo (no envelope do
   alvo), a nossa é marcada `coveredByOfficial` — **permanece no NAV simulado**
   (diagnóstico, fecha o GAP) mas o envio `/provisions` a **pula** (não duplica). As
   **não** cobertas são enviáveis pela rota `/provisions` junto das demais, sempre só
   **após o aceite** do usuário (botão + `confirm()`); o `/provisions` devolve também
   `skipped` (quantas foram puladas pelo guardrail). **Nota (jul/2026):** a flag
   `coveredByOfficial` (envio sempre pula) passou a marcar TAMBÉM o **recon de ajuste de qtd**
   coberto por provisão oficial (Passo 3 — ver Guardrails), com uma diferença: a de **dividendo**
   (Passo 6.5) fica no `sim_nav` e **não** é pré-ignorada na UI; a de **ajuste de qtd** entra no
   `sim_nav` cru mas TAMBÉM ganha `duplicatesOfficial` → **nasce IGNORADA/descontada na UI**
   (born-ignored, toggleável) para não contar 2× com a oficial.
7. **NAV/GAP:** `navPerShare = (Σ saldos + caixa + provisões − inAndOutFlows) /
   formerAmount(origem)`; `returnNavPerShare = nps_sim/nps_origem − 1`;
   `returnContribution = (Σ contribuições por ativo + contribuição nível-carteira) /
   nav_origem`; `GAP = rnps − rc`. **Contribuição nível-carteira** = Σ balances de
   transações de P&L da carteira (`_WALLET_CONTRIB_TYPES` = `gainsExpenses`, `rebate`,
   `managementFee`, `otherFee`, `brokerageFee`) — ganhos/despesas e **TAXAS** (consultoria/
   gestão, corretagem, outras) que **entram no `returnContribution` E movimentam o
   caixa/NAV** (como qualquer transação), mas **não** são capital (fora dos
   `inAndOutFlows`). *(Regra jul/2026: as taxas foram incluídas — uma taxa é um CUSTO
   que reduz o retorno, não retirada de capital. Sem isso ela derrubava a cota
   `returnNavPerShare` mas ficava fora do `returnContribution`, abrindo GAP do tamanho
   exato da taxa — caso real 6a0f558b 15→16/jun: managementFee −4.751,32 = GAP −4.751,28,
   fechado ao classificar.)* Some-se **os ajustes de contribuição** (`_CASH_EXCLUDED_TYPES` =
   `securityContributionAdjustment`/`contributionAdjustment`), que entram no
   `returnContribution` **sem** mover o caixa (par do P&L de vencimento; fecha o GAP do
   dia-ex de vencimento). Exposta em `walletContribution` (+ `walletContributionBreakdown`
   por tipo, p/ o modal "Memória de cálculo" detalhar o somatório). O P&L intraday das
   adoções da qtd-alvo (§8.6) entra no mesmo `total_contribution` e é exposto em
   `adoptionIntradayContribution`. Obs.: quando o `cashAccounts`
   oficial ainda não reflete esse custo, o projetado fica acima do oficial e o
   **`cashResidual`** (informativo) mostra a diferença — sinal legítimo, não erro do motor.
   A contribuição por ativo (`_security_contribution`) soma
   `daily = qtdAnt×(PU−PUant)` + `intraday = amountDifference×(PU−execPrice)` +
   `evento = cupom/amort + dividendo×qtdAnt`. **Preço de execução do intraday:**
   capturado do sistema via endpoint `execution-prices`
   (`beehus_catalog.execution_prices_by_sid`, por carteira, **`positionDate == alvo`** —
   só a data-alvo, pois o intraday do dia não pode usar execPrice de outra data); **só
   quando ausente OU igual ao PU** (placeholder sem informação
   intraday) é derivado de `-Σbalance/amountDifference` (= executionPrice real do
   trade). Assim um trade comprado a um preço ≠ marcação gera contribuição intraday
   correta na simulada. **Quando o execPrice é derivado (placeholder/ausente) E o
   derivado DIFERE materialmente do PU** (`> _EXECPRICE_REL_TOL`, P0-5),
   registra-se um `executionPriceFix` — assim NÃO se sobrescreve um execPrice real
   que por acaso ≈ PU, nem se propõe um fix inócuo (derivado ≈ PU = nada a acrescentar).
   O fix é `{securityId, recordId, positionDate, `{securityId, recordId, positionDate,
   capturedPrice, pu, calculatedPrice, action}` (`action=update` se há record p/
   PATCH, senão `create`) — exibido no bloco 4 do detalhe e enviável pela grade.
8. **Diff:** compara por `securityId` calculado × **unprocessed do alvo** + nav/cota/GAP
   real (navPackage do alvo) + caixa. **O PU-alvo := PU EM USO** (o `pu` da projeção —
   oficial da `processed-position` quando o alvo está processado; ver Passo 4), de modo que
   o diff confronta **só a quantidade** — o PU **bruto** da `unprocessed` não é mais
   comparado em cenário algum (ele não carrega o PU de B1/B2/C1/C2 e gerava "preço
   divergente" espúrio contra o oficial). A divergência de saldo é **DERIVADA da de
   quantidade** ao PU em uso: `balanceDiff = round(PU em uso × Δqty, 2)` e o saldo-alvo
   `realBalance = saldo projetado − balanceDiff` (**consistente** com o projetado). NÃO se
   subtraem dois saldos arredondados de forma independente — senão o arredondamento de
   `PU×qtd` (PU herdado da processed, com >6 casas, × quantidade grande) geraria um resíduo
   de centavos **mesmo com qtd e PU iguais** (caso real `69cc1d08`: 0,03 espúrio → "saldo
   diverge" falso). Assim `balanceDiff = 0` EXATO quando a qtd bate, e `realPu == calcPu`.
   Na tela, "PU alvo"/"Saldo alvo" mostram esse PU em uso. **Limiar de materialidade
   (jul/2026):** um ativo só entra em `diverged` quando o IMPACTO EM $ é material —
   `abs(balanceDiff) = abs(Δqty × PU em uso) > _BALANCE_ABS_TOL` (R$ 0,01, PISO ABSOLUTO). Uma
   diferença de qtd ínfima cujo valor financeiro é < R$ 0,01 (ex.: `0,000329 cota × PU 2,112772
   = R$ 0,0007`, caso real FIDC Brave 90 / SID `68657bff…`) é **arredondamento** da inferência
   `−saldo/PU`, não uma divergência real → é **ignorada** (fora de `diverged`, logo **sem recon
   de ajuste**). A qtd tem tolerância ABSOLUTA — o threshold % de saldo (`balance_tol_pct`) não
   suprime a divergência (Scenario P1). Antes bastava `abs(Δqty) > 1e-6` para marcar, gerando
   ajuste espúrio de saldo R$ 0,00.
8.5. **Classificação da divergência de quantidade (SEM caixa-âncora):** a verificação
   contra o caixa foi **ELIMINADA** (jul/2026). O caixa é de **carteira** e não isola por
   ativo quando há **várias transações/provisões simultâneas**, então não serve de âncora.
   Cada divergência de **quantidade** (qty movimentada × qty na unprocessed do alvo)
   recebe uma `suggestedAction` decidida **só pela natureza do ativo**, sem olhar o caixa:
   • **VENCIMENTO** (`matured=True`: a projeção zerou o ativo, o alvo ainda o carrega) →
     `"transaction"` — falta a **transação de RESGATE no vencimento** (o principal ENTRA
     no caixa). É a **única** exceção que continua transação.
   • **demais** → `"provision"` — **provisão de ajuste** que adota a qtd-alvo (NAV-neutro),
     **considerando o offset de liquidação** do trade. Gerada **inclusive no forward**
     (sem caixa oficial no alvo) — a classificação não depende mais do caixa.
   • **Fundo com IRRF** (`brazilianFund` cujo resíduo de qtd é o IRRF/PU, já apurado no
     bloco de IRRF) → **nada** (marca `explainedByIrrf`, `suggestedAction=None`);
     provisionar duplicaria o IRRF.
   **Confiança por divergência** (`confidence` + `because`): **alta** (a classificação é
   determinística — não depende mais de isolar o caixa por ativo); **média** só no
   **vencimento** (o alvo pode estar apenas atrasado). **Divergência só de PU não existe
   mais** (o saldo-alvo é avaliado ao PU em uso → `priceDiverged` sempre `False`, mantido só
   p/ compat de schema). O diff devolve **`cashResidual`** (= caixa projetado − oficial,
   **INFORMATIVO**, não classifica nada), `movedCash`, `officialCash`,
   `classificationConfidence`, `counts.qtyDiverged` e, por divergência, `suggestedAction`/
   `confidence`/`because`. **`cashStatus`/`cashMatch` não existem mais.** Os
   `reconProvisions`/`reconTransactions` carregam o mesmo `confidence`/`because`.
   **O resíduo de caixa (`cashResidual`) é mantido só como informação** — aparece na etapa
   `caixa` dos GAPs por etapa (`gapStages.cash`), na linha Caixa do detalhe (badge neutro
   "resíduo R$…", sem verdito BATE/NÃO bate) e, quando **`|resíduo| ≥ 0,01`**, como a linha
   **"Divergência de caixa"** do **Relatório de erros** (natureza reconciliação, só Tipo A) —
   **informativa: NÃO dispara correção/recon/escrita** (ver docs/REPORT_ERROS_CONCILIACAO.md).
   IRRF, preços de execução e a liquidação
   stockETF/gainsExpenses seguem calculados a partir da unprocessed-alvo/transações,
   válidos inclusive no *forward* (preparar o D+1).
8.6. **Geração da correção** (`reconProvisions` / `reconTransactions`): por
   divergência de quantidade, `Δqty = qty(unprocessed alvo) − qty(movimentada)`,
   `balance = −execPrice × Δqty`. **PU (jul/2026): `executionPrice` CAPTURADO** (endpoint
   execution-prices na data-alvo, `exec_price_by_sid[sid].price`) **primeiro**
   (`priceSource:"executionPrice"`); **fallback = PU-alvo** (`realPu = calcPu`, o PU EM USO —
   oficial da processed quando disponível), `priceSource:"pu-alvo"` (exibido com tag "PU" +
   tooltip). Datas pelo **offset do trade** (`_prov_dates(alvo, offset)`, `offset = settlementDays −
   navDays` do `securitySecInfo`); só offset **POSITIVO** desloca (liquidação futura), `≤ 0` → `+1`
   dia útil (piso — evita initialDate antes do alvo). **Efeito no NAV +
   `intradayContribution` (jul/2026):** a adoção da qtd-alvo soma `+Δqty×PU_linha` às cotas
   e `−Δqty×execPrice` em `recon` → net = `Δqty×(PU−execPrice)` = **P&L intraday** do trade
   adotado. O sistema **calcula esse P&L** (`intradayContribution`, gravado em cada
   `reconProvision` e somado em `adoptionIntradayContribution`) e o **soma ao
   `returnContribution`** — assim NAV e contribuição sobem juntos e a adoção fica
   **NAV-neutra NO GAP** (`amDiff ≈ 0`) **sempre**, inclusive quando `execPrice ≠ PU-alvo`
   (no fallback `pu-alvo`, `execPrice = PU` ⇒ intraday 0). A etapa `mov` do GAP usa a
   contribuição **sem** esse termo (pré-adoção); `mov + amDiff = simGapCash`. **Sem
   caixa-âncora:** divergência **não vencida** → **provisão** (`provisionSource=
   "amountDifference"`), com **datas pelo OFFSET do trade** — `_prov_dates(alvo, offset)`,
   `offset = settlementDays − navDays` capturado do buySell da janela (`securitySecInfo`); só
   offset **POSITIVO** desloca (liquidação futura), `≤ 0` → `+1` dia útil (piso; não data
   initialDate antes do alvo). A provisão carrega o campo
   `offset`. Divergência de **vencimento** → **transação** buySell de resgate. **Guardrails:**
   só ativo **mapeado** (no `diverged`); pula (não gera) ativo que já tem provisão do **Passo 6**
   (o motor já derivou a provisão da janela) e pula **fundo com IRRF** (`explainedByIrrf` — o
   resíduo de qtd é o IRRF, já resolvido no bloco de IRRF). **Coberto por provisão OFICIAL com
   securityId (`coveredByOfficial` + `duplicatesOfficial`, jul/2026):** quando a divergência de qtd
   já é coberta por uma provisão OFICIAL do sistema para o MESMO ativo — seja uma provisão **ATIVA
   na data-alvo** (`off_prov_sids`), seja uma provisão **buySell que LIQUIDA na data-alvo**
   (`liq_prov_sids`, jul/2026 — ela não está em `off_provs`/`official_prov_in_nav`, pois o envelope
   do alvo traz só `initialDate ≤ data < liquidationDate`; sem este ramo o recon do MESMO ativo
   nascia ATIVO e dobrava a provisão que já liquida — caso real More Crédito FICFIDC 18→19/jun: recon
   −155.000 = GAP −154.971; born-ignored → +28) — o recon
   de ajuste **É gerado mesmo assim, nascendo como uma DUPLICATA IGNORADA da oficial** — trata-se
   igual ao mecanismo `duplicatesOfficial` (ver adiante), com uma diferença de envio. Recebe
   `duplicatesOfficial:true` DETERMINISTICAMENTE (por securityId, na criação — não pelo casamento
   por valor, por isso é EXCLUÍDO de `_dup_candidates` p/ não consumir 2× a contagem da oficial) →
   **entra no `sim_nav` CRU pela adoção da qtd-alvo** (`_adopt_target_qty` branch (a), como
   qualquer recon; NÃO marcamos `coveredByProvision`, então a oficial TAMBÉM entra em
   `official_prov_in_nav` — o par oficial+recon dobra a compra no NAV cru). A UI o **pré-marca
   IGNORADO** e o **desconta** (`_walletWhatIf`/`_whatIfProj`) → NAV/GAP corretos por construção,
   MAS a linha é **TOGGLEÁVEL**: o usuário reabre em "↩ considerar" (what-if que passa a CONTAR a
   provisão sobre a oficial → o GAP se move pelo valor dela). `coveredByOfficial:true` faz o envio
   `/provisions` **PULAR** (a oficial já existe → não duplicar no Beehus; devolve `skipped`). O
   recon coberto por oficial **ATIVA** (`off_prov_sids`) é **valorado ao PU** (não ao execPrice
   capturado) → a adoção gera intraday 0 → o desconto do born-ignored reproduz EXATAMENTE o GAP
   "sem o recon" (robusto a execPrice≠PU; senão o intraday somado à contribuição não seria desfeito
   e — como a ÂNCORA oficial fica no NAV — o GAP born-ignored derivaria). **Exceção do caso que só
   LIQUIDA na data** (`liq_prov_sids`, sem âncora em `official_prov_in_nav`): **MANTÉM o execPrice
   capturado** — não há oficial no NAV para duplicar, então o P&L intraday do trade
   (Δqty×(PU−execPrice)) é REAL e segue na contribuição; o drop do born-ignored tira só o saldo do
   recon do NAV e o intraday explica a variação → GAP≈0 (real More Crédito: −558 valorando ao PU →
   +28 mantendo o execPrice). **Dois
   endurecimentos da revisão adversarial (jul/2026):** (1) `_official_prov_in_nav_total` deduplica
   engine↔oficial por **(securityId, provisionType)** — não só por securityId: um ativo com
   provisão de DIVIDENDO (Passo 6.5) E oficial buySell coberta no mesmo sid mantinha a buySell
   FORA do NAV (excluída pelo dividendo) enquanto o recon era descontado → passivo contado ZERO
   vezes → GAP falso. (2) as oficiais **já reivindicadas por um recon coveredByOfficial** (por
   securityId) são EXCLUÍDAS de `_off_counts` (casamento por valor) — senão a mesma oficial seria
   consumida 2× e marcaria uma provisão IRMÃ (ex.: subscrição em trânsito do Passo 6b, mesmo
   valor/tipo, sem oficial própria) como `duplicatesOfficial` espúria, descontando o saldo dela do
   NAV. **Iteração anterior (descartada):** o recon nascia FORA do NAV (`inNav=false`, sem toggle,
   rótulo morto "fora do NAV") — o usuário não conseguia reativar; agora é uma duplicata ignorada
   normal, reativável, como as demais `duplicatesOfficial`. Motivação real: fundo MORE CRED FICFIDC
   (subscrição offset=0) com provisão oficial "Compra More Crédito FICFIDC" já no sistema — a
   divergência de qtd não gerava artefato visível. **Duplicata sem securityId
   (`duplicatesOfficial`, jul/2026):** quando a provisão oficial vem **SEM securityId** (ex.:
   provisão genérica de `adjustments`/`Compra <fundo>`), o guardrail por sid não a pega e a
   provisão CRIADA acaba DUPLICANDO-a. Ela **continua sendo gerada**, mas se casar uma
   oficial por **valor financeiro a 2 casas + tipo** (casamento por CONTAGEM — consome uma
   oficial por provisão) recebe `duplicatesOfficial:true`; a UI então a **pré-marca como
   IGNORADA** no NAV/GAP (como se o usuário clicasse ⊘ ignorar — 1×/carteira, toggle
   preservado no re-render), evitando contar a mesma provisão 2× no `sim_nav` (uma na
   `official_prov_in_nav`, outra na adoção `recon_prov_in_nav`). É what-if (não grava). O
   ajuste aparece nos **3 lugares**: (1) o card "Projetada (sim.)" do detalhe recalcula
   NAV/cota/GAP (`_whatIfProj`) e ganhou a subcoluna **"ignoradas"** com a QUANTIDADE de
   provisões ignoradas + Δ NAV; (2) a **tela inicial** (grade) desconta as `duplicatesOfficial`
   por carteira (`_walletWhatIf`, stateless) — NAV/GAP já vêm ajustados, e uma **coluna antes da "Diag."** ("Prov. ign./sug.") mostra `# ignoradas / # sugeridas a gerar`; (3) a
   ordem de render do detalhe garante que `_mProvBlock` popula `_detProvByKey` ANTES do
   `_mNavBlock` (senão o NAV não recalculava na 1ª render). Nada disso muta os dados
   (`_movResults`/payload) — export/envio seguem lendo o `sim_nav` cru. **O mesmo
   `duplicatesOfficial` cobre também as provisões de SUBSCRIÇÃO EM TRÂNSITO**
   (`provisionSource="subscription"`, bucket `eng` — ver Passo 6b): o card/grade/coluna e o
   pré-ignore incluem `provisions` além de `reconProvisions`. **Adoção da qtd-alvo (caso PROVISÃO):**
   quando a divergência vira **provisão** (não vencida → liquidação futura, sem
   transaction na janela), a posição **projetada ADOTA a quantidade do ALVO** e a provisão de
   ajuste entra no `sim_nav` — **NEUTRO NO GAP** (as cotas adotadas `+Δqty×PU` e a provisão
   `−Δqty×execPrice` deixam o P&L intraday `Δqty×(PU−execPrice)`, que é somado à
   contribuição via `intradayContribution` → NAV e contribuição sobem juntos): só iguala a
   projetada ao alvo em quantidade, o GAP não muda; a divergência recebe a flag
   `adoptedTargetQty`. **A adoção
   vale para os DOIS caminhos:** (a) a provisão de ajuste **criada** agora
   (`reconProvisions`, branch (a) de `_adopt_target_qty`) — **inclusive o recon
   `coveredByOfficial`/`duplicatesOfficial`** (jul/2026), que é adotado como qualquer recon (entra
   no `sim_nav` cru) e apenas nasce pré-ignorado/descontado na UI; e (b) a divergência **coberta
   por provisão OFICIAL** com `coveredByProvision` (branch (b)) — hoje **só** quando o ativo já tem
   TAMBÉM provisão do **Passo 6** (both-case; o off-only virou o caminho (a) da duplicata). No caso
   (b) a provisão oficial fica **fora** do `sim_nav`, então o offset NAV-neutro `−Δqty×PU` é
   **sintetizado** (com o PU da própria linha) só para casar a cota adotada. As de **transação**
   (só **vencimento**) seguem **write-only, fora do NAV**.

### Diagnóstico puro `_diagnose` (P4-15)

Os passos **6.5 (IRRF)** + **8/8.5/8.6 (diff + classificação + correções)** + a
liquidação stockETF/B3 + a visão de transações foram extraídos de
`_build_movement_for_wallet` para uma função **pura** `_diagnose(...)` — **sem I/O nem
cache**. O motor faz o *fetch* (API) e a *projeção*; depois entrega ao `_diagnose` os
insumos já prontos (`rows` projetadas, `tgt_rows`, `txns`, `txn_by_sid`, caixa
projetado, `tgt_nav`/`prov_meta`/`balance_tol_pct` pré-buscados, `sec_meta`,
`name_hints`, provisões oficiais/origem…) e recebe de volta o bloco de diagnóstico
(`irrf`, `diff`, `reconProvisions`/`reconTransactions`, provisões oficiais/liquidando,
`stockEtfLiquidation`, `transactions`). `_diagnose` **anota em lugar** `provisions`
(`coveredByOfficial`) e as divergências do `diff` (`coveredByProvision`). O `tgt_nav`
(navPackage do alvo) deixou de ser buscado **dentro** de `_build_diff` — chega
pré-buscado (param opcional `tgt_nav`; quando `None`, `_build_diff` ainda busca, p/ os
testes diretos). **Comportamento idêntico** (suíte offline verde byte-a-byte). Ganho:
o diagnóstico fica **testável isolado** (sem stubar a API) e **recomputável sem
refetch**. A **projeção** (quantidade/PU/contribuição/NAV e os `executionPrices`, que
são calculados por-linha) **permanece** em `_build_movement_for_wallet`.

### Cascata (waterfall) do GAP (P3-12)

O motor devolve `gapWaterfall: [{label, kind, value}]` + `gapUnexplained`
(`_gap_waterfall`, função pura) que **decompõem o `simGapCash`** nos achados que
**genuinamente entram na sua identidade** (`GAP = ΔNAV − inflows − contribuição` — ver
`project_conciliacao_mov_gap_identity`), com a **identidade exata por construção**:
`simGapCash = Σ(gapWaterfall.value) + gapUnexplained`.

- **Componentes atribuídos** (só os mecânicos/econômicos): **`provisionFuture`** =
  Σ saldos das **provisões de liquidação futura do Passo 6** (`buySell`) — entram no
  `sim_nav` mas **não** têm contrapartida na contribuição → empurram o GAP (cenário B:
  GAP=50=provisão). As de **dividendo/JCP** (Passo 6.5) são **pareadas** pela
  contribuição `div_ps·fq` → **GAP-neutras** (fora). **`irrfMissing`** = `irrfMissingTotal`
  — perda econômica do resgate de fundo não capturada na contribuição nem somada ao
  `sim_nav` (cenário F: GAP=−20=IRRF ausente).
- **Provisões oficiais pendentes (`officialProvisionsInNav`):** as provisões OFICIAIS
  ativas no alvo que o motor **não** deriva da janela (ex.: settlement que liquida DEPOIS
  do alvo → sem transação na janela; provisão sem securityId) são **somadas ao `sim_nav`**
  — o NAV oficial as inclui, então omiti-las deixava o `sim_nav` ACIMA do oficial pelo valor
  da provisão (caso real `69b99c5a` 26→27/mai: `buySell −197.841,77` = GAP idêntico, fechou
  p/ ~R$60). **Dedup (jul/2026, casa por `(securityId, provisionType)` e só contra engine-provision
  ATIVA):** pula a oficial já representada por uma engine-provision de MESMO `(sid, tipo)` em
  `prov_total` OU por `coveredByProvision` (recon NAV-neutro). **Duas correções da revisão
  adversarial + verificação do usuário:** (a) casa por `(sid, TIPO)` e não só por sid — senão uma
  provisão de DIVIDENDO (Passo 6.5) excluía uma oficial buySell de mesmo sid; (b) uma engine-provision
  **`duplicatesOfficial`** (que será DESCONTADA no born-ignored — ex.: subscrição em trânsito do
  Passo 6b com oficial de MESMO sid, caso real Kapitalo NW3) **NÃO exclui** a oficial: se excluísse,
  a compra ficaria contada ZERO vezes (engine descontada + oficial excluída) → NAV baixo e GAP falso
  (−180k no wallet do usuário). Mantendo a oficial, o desconto do duplicata a deixa contada 1×.
- **Fora da cascata, de propósito:** `reconTransactions` e as divergências calc×alvo
  **não** entram. A **adoção da qtd-alvo** (provisão de ajuste **criada** — `reconProvisions` —
  OU divergência **coberta por provisão oficial** — `coveredByProvision`) entra
  no `sim_nav` mas de forma **NAV-NEUTRA** (cotas adotadas `+Δqty×PU` = provisão/offset
  `−PU×Δqty`), então **não gera GAP simulado** tampouco. A projeção é internamente consistente
  (qtd derivada do caixa/PU ou adotada do alvo). Logo o **resíduo de janela longa** (drift de PU no fallback
  `q=−balance/PU`, execPrice≠implícito, vencimento, arredondamento) cai inteiro em
  `gapUnexplained` — é onde o usuário lê "quanto do GAP ainda não tem explicação".
- **UI** (`_mGapWaterfall`, **read-only**): um bloco no painel da prévia, logo abaixo
  da barra NAV, com a linha **"GAP R$X = R$Y explicado + R$W inexplicado"**, a lista de
  componentes e a linha de resíduo. Some quando não há GAP material nem achados
  (reconciliação limpa).

### Onde a discrepância aterrissa (decomposição)

> **Sem caixa-âncora (jul/2026):** a classificação da divergência **não olha mais o caixa**
> (que é de carteira e não isola por ativo com várias transações/provisões simultâneas). Toda
> divergência de qtd **não vencida** vira **provisão** de ajuste (adota o alvo, NAV-neutro),
> **considerando o offset**; só o **vencimento** vira **transação** de resgate; e o **fundo com
> IRRF** não vira nada (o resíduo é o IRRF). O resíduo de caixa (`cashResidual`) fica como
> **informação** à parte — não é gatilho de nada. O resíduo do GAP (estimativa) é outro bucket
> — também **não** é diferença de caixa.

A discrepância "aterrissa" em **buckets distintos** (nenhum decidido pelo caixa):

| Classificação | Posição projetada | Onde fica a discrepância |
|---|---|---|
| Qtd diverge (não vencida) / coberto por provisão oficial | **adota o alvo → igual** | na **PROVISÃO** (liquidação futura, datas pelo offset); NAV-neutro, GAP intacto |
| Qtd diverge + **VENCIMENTO** (`matured`) | não adota → diverge | **transação de RESGATE** a gerar (write-only, fora do NAV) |
| Qtd diverge + **fundo com IRRF** | não adota → resíduo | **NADA** — o resíduo de qtd é o IRRF/PU (bloco de IRRF resolve); marca `explainedByIrrf`, `suggestedAction=None` |
| Qtd diverge + **buySell da janela `offset≠0`** | (Passo 6) | **PROVISÃO de liquidação futura** já criada no Passo 6 (não passa pelo recon) |
| Só no movimentado / só no alvo (`onlyCalc`/`onlyReal`) | não adota → diverge | **POSIÇÃO**, revisão manual (flag-only) |
| ~~Só PU (qtd igual)~~ | — | **não ocorre mais** — saldo-alvo avaliado ao PU em uso (`priceDiverged` sempre `False`) |
| Janela longa / estimativa | igual | **GAP** (drift do fallback `q=−balance/PU`, execPrice≠implícito, vencimento, arredondamento) |

**Caso real (Investo V8, carteira `6a0f558b`, 08→11/mai):** o caixa **bate exato**; a posição é
**adotada** (27.120 → 39.520 = alvo); os **−3,57** que restam são **resíduo do GAP** (estimativa),
**não** diferença de caixa.

### Classificação flag-only de onlyCalc / onlyReal / só-PU (P2-8)

As divergências que **não** são de quantidade acionável ganham uma classificação de
**diagnóstico** — mas **FLAG-ONLY**: rotuladas, exibidas e marcadas p/ **revisão
manual**, **sem nenhuma geração de escrita automática** (precisam de validação com dado
real do que cada caso significa). Cada uma leva `suggestedAction`, `confidence`,
`because` e `needsManualReview`:

- **`onlyCalc`** (ativo na movimentada, ausente no alvo): `suggestedAction="review"`,
  `confidence="low"`, `needsManualReview=true`. Hipóteses no `because`: venda/resgate
  não capturado na janela, ou baixa/transferência no alvo.
- **`onlyReal`** (ativo no alvo, ausente na movimentada): idem; hipóteses: compra/aporte
  não capturado, ou **origem não mapeada** (sem `securityId`).
- ~~**Divergência só de PU/saldo**~~ (`priceDiverged`): **não ocorre mais** — o saldo-alvo
  é avaliado ao **PU em uso** (oficial da processed), então o PU **bruto** da `unprocessed`
  não é comparado e não há divergência só-de-PU. `priceDiverged` fica sempre `False`.

O `diff.counts.manualReview` soma os três p/ um chip de resumo na barra **Ativos**.
**Write-safety (crítico):** a geração de correção (`reconProvisions`/`reconTransactions`)
**continua iterando só `diff.diverged` com `qtyDiverged`** (vencimento → transação, demais →
provisão) — **nunca** `onlyCalc`/`onlyReal` nem o valor `"review"` de `suggestedAction` (que
**não tem nenhum leitor** que dispare escrita). **UI:** pílula **"⚠ revisar manual"** (`_reviewBadge`,
âmbar) + pílula de confiança nas linhas onlyCalc/onlyReal/preço-divergente, mais o chip
de contagem. A **geração de escrita** p/ esses casos fica para **depois da validação**
com dado real (a pedido do usuário) — não é feita automaticamente.

## Rotas

- `GET /conciliacao-mov` — página.
- `GET /api/conciliacao-mov/dates` — datas (default últimos 10 dias úteis).
- `GET /api/conciliacao-mov/rows?companyId&date(origem)&targetDate(alvo)` — grade:
  carteiras com unprocessed na origem + navPackage do **sistema no alvo**
  (`sysNav/sysNavPerShare/sysGapCash/sysGapPct`). Sem análise da origem.
- `POST /api/conciliacao-mov/movimentar` `{companyId, walletId, sourceDate, targetDate,
  omitCashTxnIds[], useCache}` — projeta **uma** carteira.
  Devolve o movement (rows, cash, NAV/nps/GAP simulados, provisions, diff).
  `omitCashTxnIds[]` = transações que o usuário marcou (na sessão) p/ NÃO contar no caixa
  projetado. `useCache` (só o **toggle** de caixa envia `true`) reusa **TODAS** as
  leituras pesadas da projeção anterior da MESMA carteira/datas via um cache TTL local
  (`_cget`/`_proj_cache`, 300s): unprocessed×2, transações, preços, eventos, **2 envelopes
  processed-position** (`procenv`, origem+alvo — UMA leitura por data que serve PU +
  amountDifference + caixa + provisões), NAV-origem, **NAV-alvo** (`tgt_nav` em `_build_diff`)
  e exec-prices — reprojeção do toggle **sem nenhuma chamada à API**. A projeção inicial e
  as pós-criação enviam `false` → releem FRESCO e re-aquecem o cache (mantém correção logo
  após criar transação). **Custo por carteira/data:** ~10 requisições à Beehus API
  (unprocessed×2, transactions, security-price, security-events, execution-prices,
  processed-position×2, nav-contribution×2); +1 prices se houver fundo totalmente resgatado;
  +amountDifference só quando a liquidação de um `buySell` cai fora de origem/alvo.
  **Latência (jul/2026):** essas ~10 leituras deixaram de ser sequenciais — a rota faz
  **prewarm PARALELO** (`_prewarm_single`, ThreadPool): purga do `_proj_cache` toda chave da
  carteira e dispara as leituras independentes em pipeline (Onda A: unprocessed×2 ∥ txns ∥
  exec-prices ∥ envelopes×2 ∥ nav×2 ∥ resolve_wallet ∥ **warm do índice global de securities**
  — a leitura mais cara no processo frio, ~2s; Onda B: prices ∥ events, disparada assim que
  unproc+txns+wallet chegam, SEM esperar nav/envelopes), e o motor roda com `use_cache=True`
  lendo só o que a request acabou de buscar — **mesmo frescor** do antigo `use_cache=False`
  (purge garante que nada ≤TTL de request anterior sobrevive) e **payload byte-idêntico**
  (validado ao vivo + Scenario PW da suíte: identidade com `inAndOutFlows`, frescor
  pós-mutação, purge). Fail-open: leitura que falhar no prewarm é refeita pelo motor no fluxo
  normal (o erro surfaceia igual; token expirado dá 502 imediato). Medido no caso real (1
  carteira, 23→24/jun): **frio ~6,5s → ~3,0s** (limitado pela chamada irredutível do índice
  `securities`); quente ~0,77s → ~0,64s. **Concorrência (Flask threaded):** as mutações do
  `_proj_cache` (escrita e o purge, que ITERA o dict) são serializadas por `_proj_lock`, e a
  projeção lê o snapshot do PRÓPRIO request via um **overlay thread-local** (`_tl.overlay`) —
  imune a purge/overwrite de um request concorrente da mesma carteira (senão misturaria txns
  de um instante com eventos de outro). O purge também expulsa entradas >TTL (o cache não
  crescia sem teto). Coberto pelo Scenario PW da suíte (inclui stress multi-thread purge∥_cput).
- `POST /api/conciliacao-mov/prewarm` `{companyId?, wait?}` — **pré-aquecimento OPT-IN (Nível 1)**.
  Aquece os índices de catálogo caros que o 1º cálculo da sessão pagaria em série:
  `securities_index` (~2s, custo fixo dominante) e — com empresa — `wallets_for_company`. São
  caches TTL (5min) COMPARTILHADOS por todas as telas: nenhum dado novo, nenhuma escrita,
  resultado idêntico ao caminho normal — só antecipa. **NÃO** faz o prefetch pesado por-carteira
  (isso é do /movimentar[-batch]); o custo NÃO cresce com o tamanho da seleção. Best-effort:
  token ausente/erro → silencia (o fluxo normal re-busca e sinaliza). O warm roda SEMPRE numa
  **thread daemon** (sobrevive à desconexão do cliente e ao cap de espera). **Contrato de
  resposta:** `wait` (padrão `true`) faz o request **aguardar o warm terminar** (cap
  `_PREWARM_WAIT_CAP`=20s) e devolver **200 `{warming, done}`** — assim o front sabe QUANDO o
  aquecimento realmente acabou e esconde o spinner no tempo REAL (não num atraso chutado); se
  estourar o cap, devolve `done:false` e o daemon segue. `wait:false` → **202** imediato
  (fire-and-forget, comportamento antigo). **UI:** toggle "⚡ Pré-aquecer" na barra de filtros +
  **spinner "aquecendo…"** ao lado (vira "✓ pronto" por ~1,5s ao concluir; contador cobre warms
  global+empresa sobrepostos) + **pergunta única na 1ª abertura** (banner Sim/Não), preferência
  salva em `localStorage` (`cmov_prewarm`). Dispara no load (índice global) e ao trocar de empresa
  (índice da empresa), com dedup por sessão. Desligado por padrão até o usuário optar — quem vai
  mexer em poucas carteiras deixa off e evita a leitura grande.
- `POST /api/conciliacao-mov/movimentar-batch` `{companyId, walletIds[], sourceDate,
  targetDate}` — projeta **VÁRIAS** carteiras numa requisição. É o que o front usa ao
  **Movimentar** a seleção (antes: 1 `/movimentar` por carteira, pool ≤3 → ~10·N round-trips).
  Faz o **prefetch em LOTE** (`_prefetch_batch`): 1 chamada por endpoint p/ TODO o conjunto —
  unprocessed (range origem..alvo, walletIds) · processed-position ×2 (walletIds) ·
  transactions (walletIds) · execution-prices (empresa/data, sem walletId) · `nav_results`
  consolidado ×2 · security-price (UNIÃO dos sids + re-fetch de omissões) · security-events
  (união) — e **pré-popula o `_proj_cache`** com as chaves que o motor lê; depois roda a MESMA
  `_build_movement_for_wallet(use_cache=True)` por carteira + preços/eventos injetados →
  **ZERO I/O por carteira**. `securities`/`wallets` saem de índices warm. Reduz **~10·N → ~9
  fixos** (re-chunka a cada 150 carteiras nos endpoints A/B). **As ~9 fixas também deixaram
  de ser sequenciais (jul/2026):** Onda A com 9 leituras paralelas (incl. warm do índice de
  securities e de wallets) + Onda B (prices ∥ events) — falha em qualquer leitura de dado
  aborta o prefetch como antes (502/500); só os warms de índice são não-fatais. **Seleções
  GRANDES (>150 carteiras / união de sids >150):** os CHUNKS de 150 dentro de cada leitura
  (walletIds em positions/transactions, securityIds em prices/events) também rodam em
  PARALELO (`ex.map`, ordem de concatenação e semântica de erro preservadas) — o wall-time
  de cada task volta a ~1 latência em vez de n_chunks×latência. Resultado por carteira é
  **idêntico** ao `/movimentar` 1-a-1, EXCETO `diff.real.inAndOutFlows` (fluxos do navPackage
  do alvo — só exibição no card "Atual (alvo real)"; o consolidado `nav_results` não o traz, e
  a série por-entidade é a única fonte, per-wallet). Por isso o front marca os resultados do
  lote `_lite` e, ao **abrir o detalhe**, recarrega a carteira completa 1× (`/movimentar`
  individual, `useCache=false` → nav fresco com fluxos + re-aquece o cache da carteira p/ os
  toggles seguintes). Devolve `{results: [...]}` (1 por carteira, na ordem pedida; erro por
  carteira vem como `{walletId, error}` sem derrubar o lote). Coberto por Scenario BAT.
- `POST /api/conciliacao-mov/xlsx` `{...walletIds[]}` — .xlsx da posição projetada.
- `POST /api/conciliacao-mov/apply` `{...walletIds[]}` — **envia** a unprocessed
  projetada ao Beehus (`upload_unprocessed_security_positions_file`). Destrutivo →
  `confirm()` no front. **Lotes separados:** quando >1 `unprocessedId` cai no mesmo
  `securityId` (ex.: dois lotes/aliases do mesmo fundo — ISIN real + placeholder
  `BR0000000000`), a projeção agrega por `securityId` p/ NAV/diff/PU, mas o upload
  **PRESERVA as linhas separadas** (campo `row.lots` — `_lots_by_sid`/`_project_lots`):
  divide a qtd/saldo projetados de volta nos lotes de origem (proporcional à qtd —
  EXATO quando não houve movimento; o último lote fecha a soma). Sem isso o `Ativo` do
  upload virava a **concatenação** dos unprocessedId (`"A, B"`) — uma linha inválida.
- `POST /api/conciliacao-mov/provisions` `{...walletIds[]}` — cria as provisões via
  `create_provision`. Destrutivo → `confirm()`.
- `POST /api/conciliacao-mov/irrf` `{...walletIds[]}` — cria as transações `taxes`
  do **IRRF ausente** dos resgates de fundo (`create_transaction`, só as
  `not covered`). Destrutivo → `confirm()`.
- `POST /api/conciliacao-mov/provisions` também envia as **provisões de ajuste**
  (`reconProvisions`, `provisionSource="amountDifference"`) além das do Passo 6.
- `POST /api/conciliacao-mov/reconcile-txn` `{...walletIds[]}` — cria as
  **transações de resgate** (`reconTransactions`, buySell) das divergências de
  **VENCIMENTO** (única classe que vira transação; as demais viram provisão de ajuste).
  Destrutivo → `confirm()`.
- `POST /api/conciliacao-mov/shift-provisions` `{...walletIds[], selected:{walletId:[provId,…]}}`
  — **desloca p/ a data-alvo** as provisões que **LIQUIDAM na data-alvo**
  (`liquidatingProvisions`), SELECIONADAS pelo usuário. Para cada uma, cria uma
  **CÓPIA** (a original é mantida) via `create_provision` com **`initialDate = data-alvo`**
  e **`liquidationDate = data-alvo + 1 dia útil`** (`_next_biz_day`, seg–sex sem feriados);
  demais campos (`balance`/`provisionType`/`provisionSource`/`securityId`/`description`)
  idênticos — EXCETO `provisionSource`, **normalizado** (`_valid_prov_source`) p/ um valor
  ACEITO pela API (`adjustments`/`performanceFee`/`managementFee`/`amountDifference`/
  `transaction`/`xml`); fora da lista (ex.: `xml-5`, `corporate-actions`) → `adjustments`
  (a cópia é um ajuste do operador). **Sem isso a API rejeita com 400** "valor de
  'provisionSource' é inválido" — as provisões que liquidam na data costumam ter source de
  importação (`xml-5`). Mesma normalização aplicada em `/provisions`. Casa por `id`
  (= `_id` original, campo novo em `_prov_entry`). **Guardrail
  anti-duplicação:** pula quando já há provisão OFICIAL no alvo com mesmo ativo/tipo,
  `initialDate` na data-alvo e saldo semelhante (cópia já criada num run anterior).
  Devolve `{created, skipped, failed, liquidationDate}`. Destrutivo → confirmação no modal.
- `POST /api/conciliacao-mov/execution-prices` `{...walletIds[]}` — corrige os
  **preços de execução** placeholder/ausentes (`executionPriceFixes`): `PATCH`
  (`update_execution_price`, por `recordId`) no record existente, ou `POST`
  (`create_execution_price`) quando não há. Destrutivo → `confirm()`.
- `POST /api/conciliacao-mov/gains-expenses` `{companyId, walletId, date, balance,
  currencyId}` — **Opção 1** da liquidação stockETF/B3: cria um **ajuste de contribuição**
  `contributionAdjustment` **no ativo "Liquidação B3"** (`_B3_LIQ_SECURITY` — é por
  esse ativo que provisão e transação casam) p/ o resíduo (`diff` + gainsExpenses + **ajustes-B3**
  já lançados) e **devolve `transactionId`**. Esse tipo é
  **CASH-NEUTRO** (`_CASH_EXCLUDED_TYPES`): **não entra no caixa projetado**, só na
  contribuição — por isso **não há mais** prompt/parâmetro de "omitir do caixa" (o caixa
  oficial já reflete só o líquido da B3). Destrutivo (cria a transação) → `confirm()`.
  *(O path da rota segue `/gains-expenses` por compat; o tipo criado mudou de `gainsExpenses`
  para `contributionAdjustment` — versões antigas criavam `securityContributionAdjustment`.)*
  **Idempotente:** o resíduo (`stockEtfLiquidation`) soma também os ajustes de contribuição
  JÁ lançados **no ativo B3** (`b3AdjustTotal` — qualquer tipo em `_CASH_EXCLUDED_TYPES`, i.e.
  `contributionAdjustment` ou o legado `securityContributionAdjustment`) — após criar, o resíduo
  zera e o botão some; o filtro por securityId do B3 evita conflitar com ajustes de **vencimento**
  (mesmos tipos, em outros ativos). Coberto por Scenario N(d)/N(e).

**Implementar — escolha data única / período (tela inicial).** Na **grade**, o botão
**Implementar** (`openImplementChoice`) abre primeiro um modal de **escolha de escopo de
datas**: **Data única** (`chooseImplementSingle` → o modal de aplicação em lote descrito
abaixo, escopo `_selectedDoneWids`) ou **Período (encadeado)** (`chooseImplementPeriod`).
*(No detalhe de 1 carteira, o botão Implementar continua indo direto p/ data única.)*

**Período (roll-forward).** Abre o **mesmo componente de datas do Processar**
(`period-dates-modal`): toggle **Data única / Faixa**, cartão **"Selecionar datas
específicas"** (filtro *Apenas fim de mês útil* + **Subir Excel de datas**
`/api/beehus/util/parse-dates-excel` + *dual-pane transfer*), e checkboxes **"O que
aplicar em cada data"** (upload 🔒 sempre; preços/IRRF/ajustes/provisões opcionais).
`confirmPeriodDates` resolve a lista de datas, mantém só as **> origem da grade**, ordena
e **encadeia a partir da origem** (`_date`): pares consecutivos `(_date→d₀), (d₀→d₁), …`.
`_runPeriod` executa **sequencialmente** (roll-forward): por passo faz `movimentar-batch`
(origem→alvo) sobre as **carteiras SELECIONADAS na grade** (`_selectedVisibleWids`), aplica
as rotas escolhidas (só carteiras com artefato) e o **upload** — que materializa a
unprocessed do alvo, tornando-a a **origem do próximo passo** (leitura live via
`unprocessed_docs_map`, sem TTL; `_proj_purge` limpa o cache de projeção). Um passo com
falha **interrompe a cadeia** (os seguintes dependem dele) — marcados "ignorado". Sem
preview por data; o modal `period-run-modal` mostra `origem→alvo · status · detalhe` por
passo. Ao fim, a grade é reprojetada (`_reprojectWids`).

**Modal "Implementar" (aplicação em lote — data única).** Um único botão abre o modal de
confirmação com o **resumo do que será gerado** e aplica na ORDEM: preços de execução
→ IRRF → transações de ajuste → provisões → **provisões deslocadas** → upload da
unprocessed. O **upload da unprocessed é SEMPRE aplicado** (sem checkbox, com cadeado);
cada um dos demais artefatos gerados tem **um checkbox por linha** (preços de execução /
IRRF / transações de ajuste / provisões), marcado por padrão quando há algo a aplicar —
o usuário desmarca o que não quer subir. A seção **"Provisões que liquidam na data —
deslocar p/ a data-alvo"** lista as `liquidatingProvisions` com um checkbox cada
(DESMARCADAS por padrão, pois criam dado novo) + botões *Marcar todas / Limpar*; as
selecionadas vão p/ `/shift-provisions`. Cada passo reprojeta no servidor (vê o passo
anterior → idempotente); erro de backend num passo é registrado e segue, falha de rede
aborta o resto.

**Modal "Memória de cálculo" (auditoria da composição).** Botão no cabeçalho do detalhe
(`openCalcMemo`) abre um modal READ-ONLY (largo, ~1320px) que reconstrói, em **TABELAS
TRANSPOSTAS** (jul/2026, a pedido — helper `cmTrans`: os ITENS ficam nas COLUNAS, com o texto
de apoio/fórmula no tooltip do header; as DIMENSÕES DE VALOR ficam nas LINHAS; colunas de total
NAV/GAP com realce `cm-col-tot`; linha do alvo com fundo `cm-alvo`), todo o caminho da
posição-origem até o resultado final. **(1) "Composição do NAV simulado"** — **colunas** `Σ saldos ·
+ Caixa · + Provisões · + Recon · + Oficiais · = NAV simulado`; **linhas** `Projetado` (+ `Alvo (real)`
e `Δ (proj − alvo)` quando há alvo). *(O NAV não é repetido no cabeçalho da seção — já é a coluna
total da tabela.)* **(2) "Resultado — GAP e retorno"** — **UMA tabela** transposta (o GAP não é
repetido em destaque no topo — já são colunas), com **9 colunas granulares** em 3 grupos
(separadores `cm-col-sep`; colunas-resultado em negrito `cm-col-em`): COTA `navPerShare · former
navPerShare · retorno navPerShare` ‖ CONTRIBUIÇÃO `Σ contribuições · P&L de carteira · NAV anterior ·
retorno contribuição` ‖ `GAP % · GAP $`. **Linhas (nesta ordem):** `Alvo (real)` (quando há alvo;
fundo slate; `Σ/P&L` = `n/d`, o alvo não expõe a composição), `Simulado`, e — como informação
**SECUNDÁRIA** (fundo apagado `cm-row-sec`) — `Gap mov` (estado da projeção ANTES da adoção da
qtd-alvo — derivado client-side de `gs.mov`: `gap% = gap$ ÷ NAV anterior → retNPS = gap% + retContrib_mov
→ nps = (1+retNPS)·formerNPS`, com **`retContrib_mov` = contribuição PRÉ-adoção** = `(Σ contribuições
das posições + P&L carteira) ÷ NAV anterior`, SEM o intraday das adoções) e `GAP amDiff` (efeito da
adoção = final − mov; **deltas**: o **P&L intraday da adoção** aparece como Δ de `Σ contribuições` **e** de
`retorno contribuição`, casando o Δ de `retorno navPerShare` que a adoção gera no NAV ⇒ `GAP % ≈ 0` ⇒
`GAP $ ≈ 0`). **NÃO** há mais `= GAP final` (== `Simulado`), `Caixa (à parte)` (o Δ de caixa já
aparece na Composição do NAV) nem `Δ (sim − alvo)`. Na linha `Simulado` a coluna **`Σ contribuições`
inclui o intraday das adoções** (`adoptionIntradayContribution`) para `retorno contribuição =
(Σ contribuições + P&L carteira) ÷ NAV anterior` fechar com o do motor. A célula "P&L de carteira" mostra o total com o
**detalhe por tipo no tooltip** (`walletContributionBreakdown`); abaixo, a reconciliação
`ΔNAV = contribuição + fluxos de capital + GAP`. **(3) "Diagnósticos e ajustes"** (grade de 7 cards: IRRF —
**fora do NAV**; liquidação stockETF/B3; provisões que liquidam na data + matchStatus; preços de
execução a corrigir; provisões oficiais; ativos copiados do alvo; revisão manual/`targetSource`).
**(4) "Detalhes"** (tabelas colapsáveis: posição-origem, ativos com Δ qtd, divergências, chips de
transações da janela). **(5) "Fórmulas e regras utilizadas"** — TODAS as fórmulas agrupadas em 4 blocos
(projeção · amountDifference · caixa · NAV/GAP), cada uma com a regra de negócio ao lado.

Quando **há posição alvo** (`diff.hasTarget`), os números REAIS do alvo entram nas tabelas como
**linhas "Alvo (real)"** (fundo cinza-slate) + **linha Δ** (projetado/simulado − alvo): na
Composição, `Σ saldos` (`diff.real.securitiesBalance` — soma do balance das linhas da posição-alvo,
exclui a "Liquidação B3"), `Caixa` (`diff.officialCash`) e `NAV simulado` (`diff.real.nav`); nos retornos,
`diff.real.returnNavPerShare`/`returnContribution` e o GAP do próprio alvo
(`real.returnNavPerShare − real.returnContribution`). A `cota alvo` fica no rodapé "Base origem".
Nas seções **sem tabela comparável** (Diagnósticos, Detalhes) o alvo aparece numa **barra
"POSIÇÃO ALVO"**: Diagnósticos → `divergentes / só-no-alvo / só-na-projeção` + caixa + confiança;
Detalhes → `origem do alvo` (`targetSource`) · `caixa oficial` · nº de divergências. Campos de
`diff.real` ficam **"n/d"** quando o alvo existe mas ainda **não foi processado** (forward);
`caixa oficial` e as contagens aparecem mesmo assim. Os **Δ** só surgem quando ambos os lados são
numéricos. Sem `hasTarget`, nenhuma coluna/barra de alvo é renderizada. Cada etapa distingue
**"⚠ não calculado"** (falta insumo, ex.: navPackage da origem ausente → cota/retorno/GAP nulos)
de **"sem ocorrência"** (nada a calcular no caso). Só lê campos do payload (`_renderCalcMemo`);
nenhuma escrita. NÃO existe `gapWaterfall`/`gapUnexplained` no código — a decomposição disponível
é só `gapStages` (mov/amDiff/cash); o IRRF ausente NÃO é somado ao `sim_nav` (é um `taxes` a criar).

**Cenário what-if na memória (#5).** Se há provisão(ões) ignorada(s) no Bloco Provisões, o
`_renderCalcMemo` recalcula e **substitui** em `r` os campos `simNav`/`simNavPerShare`/
`simReturnNavPerShare`/`simGapPct`/`simGapCash`, o `provisionsTotal` (−Σ ignoradas do motor),
o `officialProvisionsInNav` (−Σ ignoradas oficiais no NAV) e os `gapStages` (`mov` recomposto
via `_navMovFromGap`/`_gapCashFor` − dropEngine − dropOfficial; `amDiff = GAP what-if − mov`;
`cash` inalterado) — assim **toda** a memória (tabelas de composição, resultado, etapas) reflete o
cenário recalculado, com um **banner âmbar "Cenário what-if — N provisão(ões) ignorada(s)"** no
topo e no subtítulo do modal. O `recon-neutro` derivado (`simNav − Σsaldos − caixa − provisões
− oficiais`) fica consistente por construção. É **só exibição — não grava**.

A exclusão de uma transação do **caixa projetado** é **só de sessão** (não há rota
que altere o Beehus): o front mantém `_omitCash[walletId]` e o envia em
`omitCashTxnIds[]` no `/movimentar`. A regra `omit_cash` vale p/ **qualquer tipo** de
transação (não é restrita a `_WALLET_CONTRIB_TYPES`).

## Arquitetura do motor (estágios puros)

`_build_movement_for_wallet` é um **ORQUESTRADOR**: concentra todo o I/O (unprocessed,
txns, preços, eventos, envelopes, navPackages) e delega os cálculos a **estágios PUROS**
(sem I/O, testáveis isolados), organizados nos MESMOS blocos da "Memória de cálculo" da UI:

| Bloco | Função | Produz |
|---|---|---|
| B1a | `_aggregate_window_txns(txns)` | `txn_by_sid`, `all_cash`, `inflows`, `wallet_contrib`, `redempt_cash_by_sid` |
| B1b | `_project_rows(**insumos)` | `rows`, `provisions` (Passos 2–4, 6, 6.5 + adoção do alvo + sort), `exec_prices_view`, `total_contribution`, `matured_cash` |
| B3 | inline (3 linhas) | `new_cash = caixa-origem + Σtxns + vencimentos` |
| B4 | `_diagnose(**insumos)` | IRRF, resíduo de caixa (info), diff, recon, liquidando/oficiais, stockETF, txn view |
| B2 | `_adopt_target_qty(rows, diag)` | adoção da qtd-alvo (muta rows/diff + grava `intradayContribution` em cada recon); `base_rows_balance`, `recon_prov_in_nav`, `adoption_intraday` (P&L intraday → somado ao `total_contribution`) |
| B5 | `_official_prov_in_nav_total` + `_compute_nav_and_gaps(..., adoption_intraday)` | `official_prov_in_nav`; `sim_nav`/cota/retornos/GAP + `gapStages` (etapa `mov` usa a contribuição pré-adoção = `total_contribution − adoption_intraday`) |

A **ordem de cálculo** (B1→B3→B4→B2→B5) difere da de exibição porque o B4 precisa do
caixa e o B5 precisa da adoção do B2. Refactor validado por **golden-master byte-idêntico**
(carteira real) + suíte offline verde a cada extração.

## Reuso

`_aggregate_positions`, `_position_name_hints` (conciliacao_unprocessed);
`_diff_threshold_decimal` (conciliacao_shared); `_prov_dates` (diagnostic_engine);
seams `beehus_catalog` (`unprocessed_doc`, `transactions_search`,
`securities_by_ids`, `security_prices_resolved`, `nav_results`,
`nav_doc_for_entity_date`, `processed_envelope` + `cash_and_provisions_from_envelope`
(consolidam PU + caixa + provisões numa só leitura processed-position por data; o
`wallet_cash_and_provisions` antigo passou a delegar a esse par), `processed_doc`
(amountDifference fora de origem/alvo), `unprocessed_existing_wallets`,
`wallets_for_company`); `beehus_api` (`security_events`,
`upload_unprocessed_security_positions_file`, `create_provision`). Não importa o
motor da **Repetir Posições** (base processedPosition + PU por curva — incompatível
com a spec); usa o `_security_meta` de lá? **Não** — offsets vêm do
`securitySecInfo` da transação (casing correto `*NAVDays`).

## Interpretações / pendências (validar com dados reais)

- **executionPrice / quantity nulos**: nesta base **`quantity` e `executionPrice`
  vêm nulos em ~100% das transações** (validado na company 58454495000109). Por
  isso a quantidade é **inferida** de `q = -balance/PU` e o PU de divisão cai no PU
  da origem / PU-alvo resolvido. Consequência: a quantidade só é exata quando o PU
  de divisão = PU real do trade — ótimo em **D→D+1**, com erro que **acumula** em
  janelas longas (PU-alvo ≠ PU da compra semanas antes + cupom/amortização no
  meio). Fechar isso de verdade depende de popular `executionPrice`/`quantity` no
  sistema origem (ou usar o PU na `liquidationDate` de cada transação como
  divisor — refinamento p/ janela longa).
- O catálogo de securities também **não** traz `*SettlementDays/*NAVDays`; o
  offset de provisão vem do `securitySecInfo` da transação. No vencimento sem
  `executionPrice`, o PU cai no da origem.
- **split/inplit**: quantidade multiplicativa pelo `factor` de `securityEvents`
  (fonte única — não some `quantity` de transação de split p/ não duplicar).
  Assumido que `buySell` registra **delta** de quantidade.
- **returnContribution simulado**: soma as contribuições por ativo (Fórmulas 4–7)
  **+ as de nível-carteira** (`gainsExpenses`/`rebate`/`managementFee`/`otherFee`/
  `brokerageFee` + ajustes de contribuição) — ver Passo 7. `walletContributionBreakdown`
  expõe o somatório por tipo p/ o modal detalhar de onde vem.
- **caixa de vencido**: soma só o RESÍDUO `max(0, former_bal − Σ resgate já lançado)`
  (P2-10, ver Passo 5). Resgate por buySell/amortization/securityTransfer/
  withdrawalDeposit não duplica; amortização parcial devolve o resto; cupom/taxes/
  dividendo não reduzem. A classificação por TIPO ainda é heurística — validar com
  dados reais quais tipos carregam o principal em cada custódia.

## Validação com dados reais (jun/2026)

Company **58454495000109**, 3 carteiras **GAP=0** no alvo (reconciliadas), origem
30/04 → alvo 24/06 e D→D+1 (23/06→24/06). Objetivo: a carteira **movimentada** tem
de reproduzir a **real** do alvo.

- **D→D+1 (uso pretendido): reproduz a original quase exatamente.** Após o
  refinamento do `amountDifference`, ΔNAV de **−0,09 / −0,14 / +0,16** (GAP$ sim ≈
  0, `diverged` 0/1/0); caixa bate (150.405,74 vs 150.405,56). A CRA cuja qtd era
  inferida (80 vs 78,40) passou a casar pelo Δqty processado.
- **Salto de 2 meses (off-label): melhorou 50–76%** após as correções (ΔNAV
  −1,02M→−575k; −5,13M→−1,23M; −371k→−161k), resíduo dominado pela imprecisão de
  `q=-balance/PU` ao longo de 8 semanas.
- **Correções aplicadas:** (C1) sinal do fallback de quantidade `buySell` estava
  invertido — resgate aumentava a posição; agora `q=-balance/PU`. (C2) as linhas
  passam a sair de source ∪ securityIds transacionados — ativos comprados na
  janela deixaram de sumir (`onlyReal` 8→0 numa carteira). (C3) `amountDifference`
  = Δqty da posição **processada** que cerca a liquidação → quantidade exata de
  renda fixa (D→D+1 ficou exato: 6a23 −1.624 → +0,16). (C4) **fundo** usa qtd
  bruta do alvo; **IRRF** ausente de resgate de fundo é calculado e proposto como
  `taxes` (validado: Real Investor FIC −4.542,83 e BTG Selic −462,67, nenhum
  coberto). Com (C4) o 6a10 caiu de −1,22M → **−502k** (sumiram os mismatches de
  aplicação de fundo).
- **Dependência de dados restante:** o GAP de janela longa que sobra é
  `onlyReal`/origem **não mapeada** (sem `preProcessingData.securityId`) e drift de
  PU de renda fixa ao longo de semanas — não é IRRF nem bug do motor.

## Verificação

1. `python -m py_compile pages/conciliacao_mov.py app.py` → ok.
2. `import app` registra as 8 rotas `/conciliacao-mov*`; Jinja compila
   `conciliacao_mov.html` e `shell.html` → ok.
3. Subir o app e, com **1 empresa / 1 data** (rate limit): grade lista divergentes;
   selecionar 1–2 carteiras → **Movimentar** preenche NAV/cota/GAP simulados; abrir
   🔍 e conferir o diff vs unprocessed real do alvo; **Baixar .xlsx** e validar o
   conteúdo; testar **Enviar ao Beehus** / **Enviar provisões** numa carteira de
   teste (destrutivo).
4. Conferir NAV/GAP simulados contra a expectativa manual numa carteira simples
   (sem eventos/vencimentos).
