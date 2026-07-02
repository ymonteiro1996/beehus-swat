# Conciliação (Não Processado) — relatório de implementação

Página nova que clona o fluxo da **Conciliação** (`#conciliacao`), mas faz a
análise **por ativo** em cima de **`unprocessedSecurityPositions`** em vez de
`processedPosition`.

> **Para ver no navegador, reinicie o servidor** (feche a janela do SWAT e rode
> `iniciar.bat` novamente). O servidor atual roda sem auto-reload, então o novo
> blueprint só é carregado num restart.

---

## 🔄 Atualização — tela inicial igual à original + NAV via navPackage

Duas mudanças sobre a versão anterior (que computava o NAV):

1. **Tela inicial (Step 1) idêntica à Conciliação original.** Mesmo cabeçalho,
   campo **Limiar |Δ| (%)** (compartilha o `/api/conciliacao/config` com a
   original), **cards de data no estilo original** (data completa + nº de
   carteiras), **data inicial = hoje** e **seleção automática da data mais
   recente**. Lista **apenas carteiras divergentes** (igual à original).
   **Colunas da grade (Step 1):** `[checkbox]`, **Carteira** (nome + botão de
   copiar o Wallet ID ao lado), NAV, *Cota*, *Quantidade*, Return NAV, Return
   Contrib, Diferença, GAP R$ e — preenchidas pelo diagnóstico — **Provisões**,
   **Preços Exec.**, **IRRF**, **Aj. Caixa** (contagens) + **Novo NAV**, **Nova
   Cota**, **Novo GAP $**, **Novo GAP %** (recálculo). A coluna **Wallet ID** foi
   removida (o ID é copiável pelo botão ao lado do nome). *Cota* e *Quantidade*
   são **opcionais** (`col-opt`) e **nascem ocultas** — o botão **Mostrar/Ocultar
   colunas** (acima da tabela) alterna a classe `cols-collapsed` no wrapper da
   tabela (mesmo padrão do Step 2; pronto para receber novas colunas opcionais).

   **Seleção múltipla.** Cada linha tem um checkbox (+ "selecionar tudo" no
   cabeçalho). Clique simples / **Ctrl/Cmd**+clique alternam uma linha (e movem a
   âncora); **Shift**+clique seleciona o intervalo desde a âncora. A seleção é
   guardada por `walletId` (sobrevive a filtro e re-render) e é **zerada** ao
   carregar uma lista nova (troca de data/empresa/limiar).

   **Diagnosticar selecionadas.** O botão roda o **diagnóstico EXISTENTE**
   (`GET /api/conciliacao-unprocessed/diagnose`) por carteira, com **concorrência
   limitada a 3** (rate limit / 429). Preenche **8 colunas de resultado**:
   - **4 contagens** — Provisões (Δqtd + transações órfãs), Preços Exec., IRRF,
     Aj. Caixa — vindas de `counts` no `/diagnose`.
   - **4 do recálculo** — Novo NAV, Nova Cota, Novo GAP $, Novo GAP % — vindas de
     `recalc`, **calculado NESTE projeto** (ver §abaixo).

   > Histórico: uma versão intermediária chamava `reconcile-by-date` do sistema
   > origem. **Removida** — o cálculo do novo NAV/GAP é feito aqui, não no
   > upstream.

   **Baixar JSON / Enviar via API.** Dois botões agem sobre as carteiras
   selecionadas **já diagnosticadas**:
   - **Baixar JSON** → arquivo local `conciliacao_naoproc_<empresa>_<data>.json`
     com tudo que foi calculado (gap, contagens, recálculo e os `items` de
     correção por carteira). É o "arquivo" do pedido.
   - **Enviar via API** → para cada carteira gera os payloads via os endpoints
     `generate-{transactions,provisions,execution-prices}` (locais) e envia tudo
     num único `POST /api/correcoes/bulk-submit`, que **grava o arquivo de
     auditoria em Correções E cria** as provisões/transações/preços de execução
     **no Beehus** (`create_*`). Ação destrutiva → pede `confirm()`. Reusa o
     mesmo caminho do "Implementar selecionados" do modal do Step 2.

   **Novo NAV/cota/GAP — cálculo neste projeto** (`_recompute_with_suggestions`,
   mesma cadeia de `_recalc_gap_with_corrections`): as **provisões** (Δqtd +
   órfãs + ajuste de caixa) mudam o NAV do dia/anterior conforme a janela de
   atividade, recompondo `navPerShare`, `returnNavPerShare` e o gap; **IRRF
   (taxes)** e **preço de execução** fecham o gap residual pela magnitude do
   impacto. Resultado em `_diagResults[wid].recalc`.

   **Veredito do Step 7 na coluna "Novo GAP % · Veredito".** Ao lado do Novo GAP %
   aparece um chip com o veredito do funil (`VERDICT_INFO`/`_verdictTag`) — qual
   lado o diagnóstico culpa: `→ contrib (ativos/transações)` (corrigir a
   contribuição; cota é referência), `→ caixa` (âncora é o `currentCash` real),
   `→ cota (NAV ant.?)` (suspeita da cota via `formerNav` — hipótese, baixa
   confiança) ou `sem gap`. O `title` traz o detalhe. **Por que num GAP não dá
   para saber a priori qual lado é o correto, e como ler o veredito:** ver
   [CONCILIACAO_QUAL_LADO.md](CONCILIACAO_QUAL_LADO.md).
2. **NAV não é mais computado — vem do `navPackages`.** `returnNavPerShare`,
   `returnContribution`, NAV, cota, passivo e o **gap** são lidos direto do
   navPackage (via os mesmos helpers da Conciliação original:
   `_mismatch_query`, `_find_former_nav`, `_recalc_gap_with_corrections`). Por
   isso as colunas de NAV **batem exatamente** com a original — por construção.
   **Fallback:** carteira **sem navPackage na data** (ou sem navPackage anterior
   para o NAV anterior) **não é exibida**.

O que **continua computado da unprocessed** é a **análise por ativo** (colunas
Tot. Contrib., Return PU, Return Contrib, Diff Rent na tabela de ativos do
Step 2) — esse é o propósito da página: comparar o que a posição não processada
+ suas transações/provisões dizem de cada ativo contra o gap oficial do
navPackage.

> ⚠️ **Validação live pendente:** no momento da edição o Atlas estava
> inacessível (rede caiu após reiniciar o PC). Os *imports* e o *blueprint*
> (7 rotas) carregam OK e os números de NAV reutilizam os helpers originais
> (iguais por construção). Rode o script
> `scratchpad/validate_navpkg.py` quando o banco voltar para confirmar a
> igualdade campo-a-campo `/rows` clone × original.

---

## O que foi feito

| Arquivo | Mudança |
|---|---|
| `pages/conciliacao_unprocessed.py` | **novo** blueprint com os endpoints da página |
| `pages/diagnostic_engine.py` | **novo** — motor do diagnóstico (funil de 6 passos) extraído como função pura, alimentado por dados computados |
| `templates/conciliacao_unprocessed.html` | **novo** template (Step 1 → carteiras, Step 2 → detalhe, modal **Diagnosticar**). **Visual:** adota o design system da **Posição Projetada** (`{% include 'partials/_repetir_styles.html' %}` → tabelas `.rp-tbl`, botões `.rp-btn*`/`.fb-chip`, inputs `.rp-input`, modais `.rp-modal*`). **Layout:** as listas são dimensionadas pelo conteúdo (sem o antigo modelo de "dividir a viewport" que deixava cartões esticados com espaço vazio) — cada lista rola dentro da própria caixa (`max-height` em `.table-wrap`/`.sec-table-wrap`) e a página rola se passar da tela. Mudança puramente de apresentação — nenhuma funcionalidade/`id`/handler alterado. |
| `app.py` | importa e registra `conciliacao_unprocessed_bp` |
| `templates/shell.html` | nova entrada no menu "Validações": **Conciliação (Não Proc.)** + rota no `PAGES` (`#conciliacao_unprocessed` → `/conciliacao-unprocessed`) |

> **Importante:** `pages/conciliacao.py` (a Conciliação original) **não foi
> alterado** — o motor do diagnóstico foi *copiado* para `diagnostic_engine.py`
> e validado contra o endpoint original (ver abaixo). Risco zero para a página
> em produção.

### Rotas próprias da página
- `GET /conciliacao-unprocessed` — página
- `GET /api/conciliacao-unprocessed/dates` — pílulas de data (nº = carteiras com navPackage divergente **e** posição não processada)
- `GET /api/conciliacao-unprocessed/rows` — linha por carteira divergente (**NAV/gap do navPackage**, colunas iguais à original)
- `GET /api/conciliacao-unprocessed/wallet-detail` — NAV do navPackage + **reconciliação por ativo computada** da unprocessed + linha de **estimativa sem explosão** (`estimate`)
- `GET /api/conciliacao-unprocessed/diagnose` — **diagnóstico focado**: reduz o funil às 5 categorias acionáveis (abaixo). Agora também devolve `counts` (4 contagens) e `recalc` (Novo NAV/Cota/GAP $/GAP %, calculados neste projeto via `_recompute_with_suggestions`). Usado pelo modal **Diagnosticar** do Step 2 **e** pelo botão **Diagnosticar selecionadas** do Step 1 (por carteira, concorrência 3)
- **Envio das correções** (Step 1, botão **Enviar via API**) reusa rotas existentes: `POST /api/conciliacao/generate-{transactions,provisions,execution-prices}` (montagem dos payloads) → `POST /api/correcoes/bulk-submit` (grava auditoria em Correções **e** cria no Beehus). Nenhuma rota nova de envio
- `GET /api/conciliacao-unprocessed/transactions` / `…/provisions` — listas auxiliares

### Estimativa "sem explosão" (linha no detalhamento)
Lançamentos da **explosão de contribuição** do Beehus (txn/provisões cuja descrição
contém **"explosão"**, ex.: `"...(explosão carteira X )"`, `"...oriundos da explosão..."`)
são **pintados de amarelo** nas tabelas de transações e provisões. Acima das tabelas
aparece uma **linha de estimativa** (só quando há explosão na data) recalculando
NAV, cota, RET. NAV, RET CONTR, Gap% e Gap$ **desconsiderando** esses lançamentos.

Modelo (`_explosao_estimate`), apoiado na identidade validada `nav == navPerShare × amount`
(→ `amount` = nº de cotas, `nav` = PL):

```
E_all  = Σ explosão (txn na data + provisões ativas na data)
E_flow = Σ explosão txn de tipo {withdrawalDeposit, taxes, securityTransfer, withdrawalDepositAdjustment}
cotas = amount ;  formerCota = formerNav / formerAmount
nav_est        = nav − E_all
cotas_est      = amount − E_flow / navPerShare     (txn de fluxo alteram inAndOutFlows → nº de cotas)
navPerShare_est= nav_est / cotas_est
retNav_est     = navPerShare_est / formerCota − 1
retContr_est   = returnContribution − E_all / formerNav
Gap%_est = retNav_est − retContr_est ;  Gap$_est = Gap%_est × formerNav
```
Consequência: o gap só muda por `E_flow/formerNav` (lançamentos de fluxo); explosão
puramente de contribuição sai dos dois lados e mantém o gap. Tudo é só leitura — não
altera o navPackage nem cria correções.

### Diagnóstico focado (modal Diagnosticar)
> **Sem Bayesiano** nesta página (mantido só na Conciliação original). O modal é
> organizado em **blocos por tipo de execução** (não por flag): **Provisões**,
> **Transações**, **Preços de execução** e **Cash mismatch** (provisão de ajuste opcional). Cada
> linha tem checkbox e o botão **"Implementar selecionados"** abre um **modal de
> confirmação** com os itens escolhidos; ao confirmar, gera as correções e envia
> para **Correções** (staging — revisável antes de ingerir no banco).

O backend usa o funil compartilhado (`diagnostic_engine.run_funnel`) para
detectar as flags, e o endpoint `/diagnose` **reduz** o resultado a estas
fontes (devolve `{gap, verdict, suggestions:{...}}`, sem `step1..step7`). A UI
as **agrupa por tipo de execução**:

| Bloco (UI) | Fontes de flag | Ação ("Implementar") |
|---|---|---|
| **Provisões** | `MISSING_PROVISION` (Δ qtd, Step 3.1, offset≠0) **+** Δ qtd sem transação (offset 0, ex-`MISSING_TRANSACTION`) **+** transação órfã (detector novo) | `generate-provisions` |
| **Transações** | `WITHHOLDING_TAX` (IR retido, Step 3.3) | `generate-transactions` (`taxes`, valor negativo) |
| **Preços de execução** | `MISSING_EXECUTION_PRICE` (Step 3.3) | `generate-execution-prices` |
| **Cash mismatch** | Step 5 (`cashDiff` + transações suspeitas) | provisão de ajuste **opcional** (saldo = Δ caixa com sinal invertido) → `generate-provisions` |

As demais flags do funil original (MISSING_EVENT, WRONG_EVENT_BALANCE,
MISCLASSIFIED, anomalias 3σ/limiar) **não são mais exibidas** nesta página — o
foco é só nas 5 acima, conforme pedido.

**Δ quantidade sem transação (offset 0)**: quando um ativo tem `amountDifference`
mas `offset = settlementDays − navDays = 0` (liquidação imediata) e não há `buySell`,
o funil marca `MISSING_TRANSACTION`. Por opção do fluxo, esta página **converte
esse caso em provisão** (em `_build_suggestions`): `buySell` com `initialDate = data`,
`liquidationDate = D+1 útil` (`_prov_dates(data, 0)` já fixa o piso no próximo dia
útil), saldo = ±impacto (compra → negativo / venda → positivo), origem
"Δ qtd (sem transação)" e descrição **"Mudança de quantidade sem transação"**.
Itens com impacto < R$ 0,01 são ignorados. A descrição é sobrescrita no front antes
do `bulk-submit` (o `generate-provisions` de produção usa descrição genérica e não
pode ser alterado).

**Detector de "transação órfã"** (novo, em `_orphan_transactions`): uma transação
`buySell` cujo `securityId` **está** na posição, mas cuja quantidade implícita
(`-balance/preço`) **não casa** com o `amountDifference` do ativo (considerando o
offset settlement−nav) e que **não tem provisão ativa**. Ou seja, o caixa se moveu
sem o movimento de posição correspondente → sugere-se uma **provisão** com saldo =
valor da transação, na janela do offset (`_prov_dates`).

A geração (categorias 1–4) **reutiliza os builders originais** sem duplicar
backend: `generate-provisions`, `generate-execution-prices`,
`generate-transactions` e `bulk-submit`. Step 6 (anomalias) e o Bayesiano foram
removidos desta página.

## Onde cada número nasce (navPackage vs. computado)

**Nível carteira (NAV / gap) → `navPackages`, NÃO computado.** Lidos direto do
documento do navPackage da carteira na data, exatamente como a Conciliação
original:

```
returnNavPerShare, returnContribution, NAV, cota, passivo  ← navPackages (campo)
NAV_anterior          ← nav do navPackage anterior não-trashed (_find_former_nav)
gap%                  = returnNavPerShare − returnContribution
gap R$                = gap% × NAV_anterior
Novo Gap %            = _recalc_gap_with_corrections(...)   (correções pendentes)
```

Como reutiliza os helpers originais, **bate exatamente** com `/api/conciliacao/rows`
para qualquer carteira que apareça nos dois. Fallback: sem navPackage → carteira
omitida.

**Nível ativo (atribuição) → computado da unprocessed.** A tabela de ativos do
Step 2 mostra o que a posição não processada + transações dizem de cada ativo:

```
contribuição(ativo)   = (saldoAtual − saldoAnterior) + Σ(transações do ativo na data)
returnContrib(ativo)  = contribuição / saldoAnterior
returnPU(ativo)       = PU / PU_anterior − 1
diffRent(ativo)       = returnPU − returnContrib
```

O diagnóstico então **atribui o gap oficial (navPackage)** a sugestões de
correção por ativo (provisões, preço de execução, IR). A soma dessas sugestões
pode não cobrir o gap inteiro quando unprocessed e processed divergem — isso é
informativo (mede o quanto a posição não processada explica do gap oficial).

### Validação contra a Conciliação original
- **NAV / gap × original:** por construção (mesmos helpers). O script
  `scratchpad/validate_navpkg.py` confere campo-a-campo (`nav`, `navPerShare`,
  `amount`, `inAndOutFlows`, `returnNavPerShare`, `returnContribution`,
  `formerNav`, `newGapPct`) `/rows` clone × original — rodar quando o Atlas
  voltar.
- **Contribuição por ativo computada × processed** (versão anterior): em ~470
  securities de mesmo PU batia **94,7%**; divergências = futuros (saldo nocional)
  e fundos de zeragem (sweeps sem transação).
- **Motor do diagnóstico × endpoint original:** o funil (`diagnostic_engine.py`)
  é cópia fiel — alimentado com dados *processed* batia **100%** passo a passo,
  flag a flag (única diferença: uma frase do Step 7).

---

## Como a análise foi montada (regras do pedido)

1. **Quantidade e PU vêm SOMENTE da `unprocessedSecurityPositions`.** Nunca da
   processed. As linhas cruas (uma por lote) são agrupadas por `unprocessedId`
   (soma qtde/saldo, PU = saldo/qtde) e depois **consolidadas por `securityId`**
   (ver ponto de atenção nº 1).
2. **Mapeamento dos ativos via `securityMappings`.** Cada `unprocessedId` é
   resolvido para um `securityId` pela entrada `{"from": unprocessedId, "to":
   securityId}` do documento da empresa. Ativos sem mapeamento aparecem como
   **"Não mapeado"** (badge âmbar) e não conseguem casar com transações/provisões.
3. **Transações e provisões são ligadas exclusivamente pelo `securityId`** do
   mapeamento (o lado "to"). Transações cujo `securityId` não bate com nenhum
   ativo da posição vão para a lista "sem ativo correspondente".
4. **Caixa continua vindo do mesmo lugar** (`cashAccounts` via `sum_cash`).
   Reconciliação de caixa = `caixaAnterior + Σtransações` vs `caixaAtual`
   (mesma heurística da conciliação original).
5. **Data anterior do NAV** = o `navPackage` anterior não-trashed mais recente
   (`_find_former_nav`, igual à original) — define o `NAV_anterior` e o gap.
   Já a **posição anterior por ativo** (colunas "Ant." / Δ) é buscada na
   `unprocessedSecurityPositions` nessa mesma data anterior do navPackage.

---

## Validação (clone × original)

Comparei o `wallet-detail` do clone contra o da conciliação original via test
client, restringindo a comparação numérica aos ativos com **mesmo PU** (como
você pediu — diferenças de PU são esperadas e legítimas).

- **Data 2026-06-18, 80 carteiras testadas** (priorizando as 9 com transações
  liquidando na data): **79 batem 100%** em qtde, PU, saldo, saldo anterior,
  qtde anterior, **total de transações por ativo** e **todos os campos de caixa**.
- A 1 carteira "divergente" (YELLOW UNICORN) **não é erro de cálculo** — é o
  ponto de atenção nº 2 abaixo (mesmos números, `securityId` diferente).
- Carteira de teste "limpa" (Onshore A, 5/5 ativos com PU idêntico): caixa e
  todos os ativos idênticos ao original.

### Correção feita durante o teste
A primeira versão deixava **uma linha por `unprocessedId`**. Como vários
`unprocessedId` distintos mapeiam para o **mesmo `securityId`** (ex.: dois lotes
de "CDB BTG Pactual", "Latitud", debêntures da carteira Santo Inácio), o clone
mostrava o ativo "fatiado" e **a quantidade/saldo não batiam** com a processed
(que soma os lotes). Pior: a transação ligada ao `securityId` seria contada em
cada fatia. **Corrigido**: agora os ativos mapeados são consolidados por
`securityId` (soma qtde/saldo, PU médio ponderado), igual à processed. Depois
da correção, 25/25 e 79/80 carteiras passaram a bater.

---

## ⚠️ Pontos de atenção para você revisar

### 1. Consolidação por `securityId`
Mantive a regra "qtde e PU só da unprocessed", **mas** agrupei os lotes que
caem no mesmo `securityId` somando qtde/saldo (PU = média ponderada). Sem isso o
ativo apareceria repetido e não casaria com a processed nem com a transação.
Se você **preferir ver cada `unprocessedId` separado**, dá pra alternar — só
sinalizar.

### 2. `securityMappings` × processed apontando para `securityId` diferentes
Encontrei pelo menos um caso real (ativo **"Latitud"**, carteira YELLOW UNICORN,
empresa 23313334000110):
- A `securityMappings` aponta `LATITUD → 67fee973…` (doc de security **sem
  ticker**).
- A `processedPosition` registrou o ativo sob `68e02588…` (doc **com ticker**).
- Mesma qtde (100.000) e mesmo PU (1,7179008) → **é o mesmo ativo, em dois
  cadastros de security duplicados.**

Como o pedido diz "integrar pelo `securityId` da `securityMappings`", o clone
segue o `67fee973…`. **Consequência:** se uma transação/provisão foi lançada
contra o `securityId` que a processed usa (`68e025…`), ela aparece como
**"sem ativo correspondente"** no clone, mesmo casando na conciliação original.
→ Vale revisar os **securities duplicados** e/ou apontar o mapeamento para o
cadastro que o processamento de fato usa.

### 3. Ativos não mapeados ficam "órfãos"
Ativo da unprocessed sem entrada em `securityMappings` aparece com badge
**"Não mapeado"** e **não** liga a transações/provisões (sem `securityId`).
Carteiras com ativos não mapeados são marcadas como **Divergente** no Step 1.

> **Data inteira não pré-processada.** Caso particular: quando o snapshot bruto
> da data ainda **não passou pelo pré-processamento** upstream, o
> `preProcessingData.securityId` (e o `beehusName`) vêm **nulos em TODOS os
> ativos** — o bloco `preProcessingData` existe, mas vazio. O detalhe da
> carteira **não** lista mais os ativos com o `unprocessedId` como nome (fallback
> enganoso): o `wallet-detail` devolve `notPreProcessed: true` e o front mostra o
> aviso **"Posição ainda não pré-processada"** no lugar da tabela de Ativos. O
> modal **Diagnosticar** também exibe o aviso (em vez de um veredito falsamente
> "limpo"). NAV/cota/caixa continuam válidos (vêm do `navPackage`). Some sozinho
> quando o upstream processa a data. Detecção: `posição tem ativos` **e**
> `nenhum com securityId`.

### 4. Diferenças de PU são exibidas, não corrigidas
Quando o PU da unprocessed difere do PU da processed, o saldo, Δ saldo e
Return PU vão refletir o PU da **unprocessed** (correto pelo pedido). Isso faz a
linha divergir da conciliação original — é esperado.

### 5. "Data anterior" pode estar distante
Se a carteira tem snapshots esparsos de unprocessed, a data anterior pode ser
semanas/meses antes (ex.: Onshore A: 2027-01-30 → anterior 2026-03-31). O
Return PU / Δ saldo então refletem esse intervalo longo. (A original teve a
mesma data anterior nesse caso, porque o navPackage anterior coincidia.)

### 6. Gap vem do navPackage (atribuição por ativo pode não cobrir 100%)
O gap agora é o **gap oficial do navPackage** (igual à original). A novidade é a
**atribuição**: o diagnóstico tenta explicar esse gap com os `diffRent` por ativo
computados da unprocessed. Quando o navPackage foi calculado com PUs/posições
diferentes da unprocessed (ou há futuros/zeragem), as sugestões por ativo
**podem não cobrir o gap inteiro**. Isso é informativo (mede o quanto a
unprocessed explica do gap oficial), não um bug.

### 7. Step 1 lista apenas carteiras divergentes (igual à original)
Lista só carteiras com `|returnNavPerShare − returnContribution| > limiar`,
**restritas às que também têm posição não processada na data**. O nº no card de
data e as linhas seguem essa regra. O limiar é o **mesmo** da Conciliação
original (`/api/conciliacao/config`) — editar aqui muda lá. Carteira sem
navPackage não aparece (fallback).

A grade do Step 1 difere da original em três pontos (UI-only, mesmo `/rows`):
**(a)** sem coluna **Wallet ID** — o ID é copiável por um botão ao lado do nome;
**(b)** **Cota** e **Quantidade** são opcionais (`col-opt`) e nascem ocultas,
alternadas pelo botão **Mostrar/Ocultar colunas**; **(c)** **checkbox por linha**
+ "selecionar tudo" com multi-seleção (clique / Ctrl / Shift), e os botões
**Diagnosticar selecionadas** (roda o `/diagnose` em lote, concorrência 3,
preenchendo as 8 colunas de resultado — contagens + recálculo NAV/GAP),
**Baixar JSON** e **Enviar via API**. Ver o detalhamento no topo
("🔄 Atualização — tela inicial").

> As colunas de cobertura de mapeamento (Ativos / Mapeados / Não Map.) e a
> reconciliação de caixa que existiam no Step 1 da versão anterior **saíram do
> Step 1** (para ficar igual à original). Continuam disponíveis no **Step 2**
> (badges de "Não mapeado", pílulas de caixa, alertas). Se quiser uma coluna de
> "não mapeados" de volta no Step 1, é só pedir.

### 8. Diagnóstico focado (5 categorias) — sem Bayesiano
- O modal **não tem mais Bayesiano** (só na Conciliação original) e **não mostra**
  as flags fora do escopo (MISSING_TRANSACTION buySell, MISSING_EVENT,
  WRONG_EVENT_BALANCE, MISCLASSIFIED, anomalias). Mostra só as 5 categorias.
- **Gap continua vindo do navPackage** (idêntico ao original); a atribuição por
  ativo usa a unprocessed.
- **Sinal/valor da provisão de "transação órfã" é uma SUGESTÃO** — uso `balance =
  valor da transação` na janela do offset. O caso é heurístico (offset 0 vs ±) e
  vale conferir o sinal no modal de confirmação / em Correções antes de ingerir.
  Validado em dados reais: Esparta gerou provisão `+160.810,60` e IR `-50.000,09`;
  CV PF BTG gerou provisão `-468.729,53` — todos com datas/offset corretos.
- **Cash mismatch**: mostra Δ caixa e as transações suspeitas (saldo ≈ Δ) e
  oferece uma **provisão de ajuste opcional** (checkbox **desmarcado** por padrão)
  com saldo = Δ caixa **com sinal invertido**, gerada via `generate-provisions`
  (`securityId` vazio, liquidação D+1 útil). É opt-in justamente por ser um
  lançamento de balanceamento.

### 9. Stores compartilhados com a Conciliação original
Gerar correções e "Implementar" (enviar) usam os **mesmos endpoints e a mesma
staging** (`/correcoes`) da Conciliação original — as correções caem na mesma
fila do `/correcoes`. É intencional (reaproveitamento), mas é bom saber.

### 10. Provisões: campo `amount` vs `balance`
No `wallet-detail` e no diagnóstico, o valor da provisão usa `balance` com
fallback para `amount` (igual ao original). O motor agrega provisões ativas por
`securityId` via `$sum amount` (idêntico ao original).

### 11. Performance
`/rows` agora segue o caminho da original (query de navPackages divergentes +
`former_map` por agregação + recalc de correções), com 1 filtro extra das
carteiras que têm posição não processada na data. Custo equivalente ao `/rows`
original. O `wallet-detail` faz a análise por ativo da unprocessed; o
diagnóstico por carteira (modal) roda em < 1,5 s.

### 12. Editar/excluir transações e provisões = ação DESTRUTIVA (via API)
O Step 2 agora tem (igual à original) os botões **✎ editar** e **🗑 excluir** em
cada transação/provisão, além de **Filtrar ativos** e **Mostrar todas** na tabela
de Ativos. O **Filtrar ativos** (`_secHasActivity`) mostra só linhas com
divergência REAL e usa as **mesmas tolerâncias dos realces visuais** — `|diffRent|
> 1e-6` (igual à cor vermelha da coluna Diff Rent), `|Δqtd| > 1e-6`, valor de
transação > meio centavo, ou ≥ 1 transação vinculada. (Antes o teste era `!== 0`
estrito, então linhas com `diffRent` ~1e-8 — resíduo do arredondamento a 8 casas,
visualmente cinza/sem divergência — vazavam pelo filtro.) Editar/excluir
**reutilizam os mesmos endpoints da original**
(`/api/conciliacao/{transaction,provision}/{update,delete}`) e agem **direto no
Beehus (PATCH/DELETE)** — **não** é staging, não passa por `/correcoes` e **não
pode ser desfeito**. Há `confirm()` antes de excluir.

### 13. Navegação de data no detalhe (←/→ no canto superior direito)
No cabeçalho do Step 2, ao lado da data analisada, há os botões **←** (dia útil
anterior) e **→** (próximo dia útil). Eles deslocam a análise **1 dia útil**
(pula sáb/dom, sem calendário de feriados — mesma convenção de `_next_biz_day`)
e recarregam o detalhe (`wallet-detail` + `transactions`) **sem** chamar
`wallet-dates` (a navegação antiga, que custava ~11 s, continua removida). Se a
data for alterada por aqui, ao clicar **Voltar** a lista do Step 1 e o campo
**Data** são ressincronizados para a data atual.

---

## Resumo

A página tem a **tela inicial igual à Conciliação original** (cabeçalho, limiar
compartilhado, cards de data, data inicial = hoje, seleção automática da mais
recente, tabela de carteiras divergentes com as colunas NAV/Cota/.../Novo Gap %).
O **NAV e o gap vêm do `navPackages`** (não computados), pelos mesmos helpers da
original — portanto batem por construção; carteira sem navPackage não aparece
(fallback). O que roda sobre a `unprocessedSecurityPositions` é a **análise por
ativo** (qtde/PU da unprocessed, ativos via `securityMappings`,
transações/provisões pelo `securityId`): contribuição, Return PU e Diff Rent por
ativo, usados pelo diagnóstico para **atribuir o gap oficial** aos ativos. O
**Diagnosticar** é **focado em 5 categorias** (provisões por Δqtd e por transação
órfã, preço de execução, IR retido na fonte e cash mismatch), organizado em
**tabelas com seleção** e um **modal de confirmação** que gera as correções
(reaproveitando os builders originais) e envia para Correções. **Sem Bayesiano**
(mantido só na original). Validado em dados reais: `/rows` bate campo-a-campo com
a original (0 divergências) e os builders geram provisões/IR/preço corretamente.
