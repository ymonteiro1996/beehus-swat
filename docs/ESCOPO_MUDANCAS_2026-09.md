# Escopo de mudanças: beehus-swat (set/2026)

Este arquivo é parte do escopo conjunto **conciliacao · ControleCargas · beehus-swat**, levantado em **25/09/2026** a partir da leitura do código. Ele traz:
- a visão geral dos três projetos;
- as decisões;
- os itens **deste repo**;
- os itens transversais.

Os itens dos outros repos estão no mesmo caminho (`docs/ESCOPO_MUDANCAS_2026-09.md`) dentro de cada um deles.

---

## 0. Roteiro para a sessão que vai implementar

1. Ler este arquivo inteiro e o `CLAUDE.md` do repo. As convenções de cada projeto estão no bloco PREP.
2. Fazer o **PREP** do repo: branch, pendências no git, como subir o servidor.
3. Seguir as **ondas** da tabela da seção 2. Para cada item:
   - implementar;
   - rodar o **teste manual** e conferir o **critério de aceite**;
   - atualizar o `docs/` do projeto;
   - fazer um commit pequeno;
   - marcar o Status na tabela.
   
   **Não dar push sem confirmar.**
4. **Parar e perguntar** ao usuário:
   - nas decisões marcadas com 🔴;
   - nas "confirmações de arquitetura" da seção 3.2;
   - se o código divergir do que está descrito aqui.
5. Ao terminar cada item, informar ao usuário:
   - o que mudou;
   - os arquivos alterados;
   - como testar.
6. Nada de gravar dado real no Beehus durante os testes. Usar carteira ou empresa de teste, ou uma data que possa ser revertida.

---

## 1. Como usar este documento

- Cada pedido tem um **ID** (`CONC-xx`, `CC-xx`, `SWAT-xx`). Itens que se repetem nos três projetos viraram itens **transversais** (`TRV-xx`), com um desenho único.
- As referências `arquivo:linha` foram levantadas em **25/09/2026**. Confira antes de editar, porque as linhas mudam.
- A sessão que for implementar deve atualizar a coluna **Status** da tabela abaixo no arquivo do repo onde o item foi feito. Status: `pendente` · `em andamento` · `em teste` · `feito` · `bloqueado`.
- Nenhum dos três projetos tem teste automatizado. Cada item traz um **teste manual** e um **critério de aceite**. Um item só vira `feito` depois do teste manual.

## 2. Visão geral e ordem de execução

A ordem prioriza o que pode gerar **dado errado no Beehus**. Depois vêm o token (que atrapalha tudo), os ajustes rápidos e, por último, os itens grandes.

| Onda | ID | Projeto | Item | Esforço | Depende de | Status |
|---|---|---|---|---|---|---|
| 0 | PREP | todos | Preparação: branch, commit do que está pendente, conferir host da API | P | — | feito (swat: branch `onda-1/escopo-2026-09`) |
| 1 | SWAT-05 | swat | Publicação publica acima da divergência: checar por data e por carteira no servidor | M | — | em teste — testado com API simulada (30 checks servidor + 18 na tela); falta o teste no Beehus real com empresa de teste. Confirmado pelo usuário (25/09): limite padrão 0,02% também no atalho "Publicar" do drill-down do Painel (editável em data/publicacao_config.json) e carteira do cadastro sem NAV na data bloqueia como "sem Δ". Limite 0 = só publica divergência zero (decisão do usuário). Causa do bug reproduzida no código antigo: 6 caminhos vazavam (faixa de datas só checava a data inicial; |Δ| era do agrupamento, não da pior carteira; seleção não rechecada; atalho do Painel sem limite) — 0 vazam no novo |
| 1 | CONC-01 | conciliacao | Colar valor na transação perde o decimal (manda valor errado ao Beehus) | M | — | pendente |
| 1 | TRV-02 | todos | Modal fecha ao arrastar a seleção de texto (transação, token e outros) | P | — | pendente |
| 1 | CC-04 | ControleCargas | Instituição "XP" aparece como "P" na matriz | P | — | pendente |
| 1 | SWAT-04 | swat | Groupings não aparecem | P–M | reprodução do usuário | feito — D7 respondido (Painel normal → Publicação, Eté): diagnóstico = todos já publicados na data (comportamento correto). Melhoria: rotas da Publicação não escondem mais falha da API como lista vazia (selo "falha ao consultar"). Hipóteses A e C não se aplicaram |
| 2 | TRV-01 | todos | Token expirado abre o pop-up de colar token na hora | M | PREP | pendente |
| 2 | TRV-03 | swat, conciliacao | Campo token começa vazio | P | TRV-02 | pendente |
| 3 | SWAT-07 | swat | Lote do Identificar Transações: 500 → 250 | P | — | pendente |
| 3 | SWAT-08 | swat | Faixa de datas vem preenchida (D-7 a D-1) nos 5 executores | P | — | pendente |
| 3 | SWAT-09 | swat | Transações: um scroll só | P | — | pendente |
| 3 | CONC-03 | conciliacao | Botão de copiar o valor do GAP | P–M | CONC-01 | pendente |
| 3 | CC-02 | ControleCargas | Renomear a aba atual para "Checklist Manual Cargas" | P | — | pendente |
| 3 | CC-05 | ControleCargas | Pauta com contorno azul; vermelho se o D-1 não foi processado | P | — | pendente |
| 4 | SWAT-01 | swat | Log temporário na tela e limpeza ao trocar de Company | M | SWAT-08 | pendente |
| 4 | SWAT-06 | swat | Várias empresas ou "Todas" nos 5 executores | M (Transações: G) | SWAT-05, SWAT-01 | pendente |
| 4 | CC-01 | ControleCargas | Campo "Data D0" que muda todo o D0 da ferramenta | M | — | pendente |
| 5 | CC-03 | ControleCargas | Novo "Controle de Cargas" (3A leitura · 3B API de jobs · 3C disparo) | G | CC-02, CC-01 | pendente |

Aliases (o mesmo trabalho aparece em mais de um lugar do pedido original):
- `CONC-02` (transação fecha ao selecionar) = **TRV-02**
- `CONC-04`, `CC-06` e `SWAT-03` (token estourou → pop-up) = **TRV-01**
- `SWAT-02` (campo token preenchido e fechando ao arrastar) = **TRV-03** + **TRV-02**

A descoberta da API de jobs (`CC-03`, fase 3B) depende de uma captura feita pelo usuário no navegador. Ela pode começar em paralelo, já na onda 1.

---

## 3. Decisões

### 3.1 Já respondidas pelo usuário (25/09/2026)

| Tema | Decisão |
|---|---|
| SWAT-07, "250 transações" | É o tamanho do lote do **Identificar Transações** (hoje 500). Não é um teto global no servidor. |
| SWAT-05, Δ nulo | Carteira ou agrupamento **sem Δ calculado** na data **não publica** e aparece no log como "sem Δ calculado". |
| SWAT-05, como checar | A checagem é **por data e pela diferença em cada data**, carteira a carteira, e não só pela data inicial da faixa. |
| CC-05, anel vermelho | Numa célula de Pauta, o vermelho aparece **quando o dia útil anterior (D-1) não tem posição processada**, mesmo que o dia da Pauta ainda não esteja processado. Isso **muda a regra atual** para células de Pauta. |
| CC-03, "rodar carga" | **Entender via API.** O usuário indicou que o Beehus web chama `GET https://api.controladoria.beehus.com.br/beehus/jobs/logger?date=AAAA-MM-DD` (200 OK), e que esse módulo de jobs deve ajudar no disparo. Nenhum dos três projetos usa esse módulo hoje. Ver a fase 3B do CC-03. |

### 3.2 Pendentes

A sessão pode seguir com o **padrão proposto** e marcar o item como "a confirmar". Os itens marcados com 🔴 bloqueiam a implementação.

| # | Tema | Padrão proposto |
|---|---|---|
| D1 🔴 | **CC-03**: a frase "podendo inserir um range de datas com intervalo de …" ficou cortada. Intervalo de quê? Tamanho máximo da faixa, passo entre execuções ou espera entre disparos? | Faixa De/Até de no máximo **10 dias úteis**, uma chamada por data, em sequência. |
| D2 🔴 | **CC-03**: a regra "D+3" vale **só para XP**, ou para todo mundo via coluna **Defasagem** do Template? No Template, D-3 também aparece em Goldman Sachs (21), BTG Pactual US (19), JP Morgan NY (17), Avenue (11) etc.; D-2 em Morgan Stanley NY (130); D-1 em BTG, Itaú etc. | Usar a Defasagem do Template (XP = D-3 → D+3). Se for só XP, deixar a lista de instituições em `data/controle_cargas_config.json`. |
| D3 | **CC-03**: a métrica de "carga efetivada" | Ver a proposta refinada no CC-03 (três níveis, dias úteis, trava contra falha prolongada). |
| D4 | **SWAT-08**: D-7 e D-1 em **dias úteis ANBIMA** ou dias corridos? | Dias úteis ANBIMA (o projeto já tem `bizdays` e `wallet_scope.deslocar_du`). |
| D5 | **SWAT-01**: ao trocar de Company, as datas ficam **em branco** ou **voltam ao padrão** D-7/D-1? O log é por ferramenta ou um só para o painel? | Voltar ao padrão D-7/D-1 e limpar o log. Um log por ferramenta, logo abaixo do botão Executar. |
| D6 | **SWAT-05**: manter um jeito de **forçar** a publicação (hoje existe Ctrl-clique no seletor)? | Não. O servidor sempre bloqueia. Se precisar, criar um "Forçar" explícito com confirmação e registro no log. |
| D7 🔴 | **SWAT-04**: em qual painel e depois de quais passos os Groupings somem? | Seguir a hipótese A (company desatualizada no iframe da ferramenta) e confirmar com o teste descrito no item. |
| D8 | **SWAT-07**: o modal "Processar Transações" do painel também tem um limite de 500 linhas (`_PTX_MAX_ROWS`, `controlpanel.html:2063`). Muda para 250? | Não mexer. Só o lote do Identificar. |
| D9 | **CC-05**: qual tom de azul? O badge fúcsia de Pauta continua? | Um azul diferente do azul de seleção (`#1d4ed8`), por exemplo sky-600. O badge continua. |
| D10 | **CC-01**: o D0 fica salvo entre recargas da página? | Não. Sempre abre em hoje, e aparece uma faixa "D0 simulado" quando ele for diferente de hoje. Trocar o D0 não roda o Atualizar sozinho. |
| D11 | **TRV-01**: depois de colar um token novo, a ação que falhou é repetida sozinha? | Não. Só aparece a mensagem "Token salvo, repita a ação". Repetir um POST automaticamente pode duplicar escrita. |
| D12 | **TRV-02**: os modais de criar/editar (transação, token) devem fechar com clique no fundo? | Token e transação fecham só pelos botões ou Esc. Os outros modais mantêm o clique no fundo, com a proteção contra arrasto. |

**Confirmações de arquitetura** (os CLAUDE.md do conciliacao e do ControleCargas, §7, exigem aval antes):
1. TRV-01 intercepta o `fetch` globalmente nos três projetos.
2. CC-03 cria uma aba nova, um blueprint novo e uma chamada nova à API de jobs.
3. CC-03, fase 3C: o ControleCargas passaria a fazer chamadas de **escrita** (disparo de job), contrariando a regra atual de "app somente leitura" (§8).

---

## 4. Itens deste repo (beehus-swat)

### PREP (swat)

- **Pasta e branch.** A pasta é `...\Projeto - Servidor\beehus-swat` e a branch é `development`. Não há mudança real pendente além da pasta não rastreada `arquivos externos/`. O `git status` mostra muitos "M", mas é só CRLF.
- **Cuidado ao rodar.** O `iniciar.bat` faz **`git pull`** antes de subir o servidor. Numa branch de feature, prefira rodar o `start.ps1` ou `python app.py` direto.
  - A porta é **5000**. O log vai para `.swat-server.out` e `.swat-server.err`.
  - O `CLAUDE.md` pede `use_reloader=False`.
- **Testes.** Não há suíte para os painéis. Depois de mexer nos partials, rode `python scripts/check_template_literal_safety.py`.
- **Mapa dos painéis.**
  - Os três painéis são **um template com três modos**: `pages/controlpanel.py:419`, função `_render_panel(panel_mode)`, rotas `/controlpanel` (l. 443), `/controlpanel/template` (l. 448) e `/controlpanel/template-sla` (l. 453).
  - No shell, cada painel é um iframe separado que continua vivo quando se troca de painel (`shell.html:274-276`, `navigateTo` l. 316-349).
  - O escopo Template chega ao servidor pelos headers `X-Swat-Scope`, `X-Swat-Run-Date` e `X-Swat-Scope-Company`, colocados pelo wrapper de `fetch` em `base.html:23-50`. Do lado do servidor, é aplicado por `wallet_scope.py`.
- **Executar.**
  - Os chips ficam em `controlpanel.html:532-570`. Cada ferramenta abre num iframe `/beehus?_frame=1#<deep>` (`Funcoes.open`, l. 6834-6842).
  - As ferramentas são views de **`templates/beehus_console.html`** (~9.500 linhas), servidas por `pages/beehus_console.py`.
  - Processar, NAV Wallets, NAV Groupings e Publicação vêm da mesma fábrica, `makeDatesPipeline(cfg)` (l. 7316), com `run()` na l. 7739. Essa fábrica **também** serve Excluir Posições e Explosão. Qualquer mudança nela chega nessas ferramentas.

| Ferramenta | Company / datas | Executar | Backend |
|---|---|---|---|
| Processar | `pcd-company` l. 277, `pcd-initialDate/finalDate` l. 283/287 | l. 474 | POST `/api/beehus/positions/process` (py l. 655) |
| NAV Wallets | `nwd-*` l. 545-555 | l. 742 | `/api/beehus/nav/calculate-wallets` (py l. 781) |
| NAV Groupings | `ngd-*` l. 804-814 | l. 937 | `/api/beehus/nav/calculate-groupings` (py l. 861) |
| Publicação | `pbd-*` l. 1016-1026, limite `pbd-threshold` l. 1110 | l. 1221 | por dia: GET `filters/groupings-by-publish-state` (py l. 322), depois POST `nav/publish` (py l. 968) |
| Transações | `i-company` l. 1277, `i-initialDate/finalDate` l. 1291/1295 | "Buscar" l. 1335 | POST `/api/beehus/transactions/search` (py l. 1079) |

### SWAT-05: Publicação publica carteiras acima da divergência (prioridade)

**Pedido:** "BUG Publicação, permitindo publicar carteiras com divergência maior do que o selecionado". O usuário complementou: "acho que precisa de uma verificação melhor por data e diferença em cada data".

**Causas (todas conferidas no código)**
1. **O limite só filtra o que aparece na tela.** No `run()`, `beehus_console.html:7792-7799`:
   ```js
   ids = (lookup.body || []).map(g => g.id);
   if (this._selectedIds && this._selectedIds.size > 0) { ids = ids.filter(...) }
   ```
   Com "Selecionadas" vazia, publica **todos** os agrupamentos elegíveis de cada dia, sem olhar a divergência.
2. **Na faixa de datas, o |Δ| é carregado só para a data inicial** (`_refreshPicker`, l. ~8825: `dt = initialDate`). Os demais dias nunca são checados. É exatamente o "por data" que o usuário apontou.
3. **Caminhos que furam o limite:**
   - "Selecionadas" não é rechecada quando o limite baixa (l. ~8919);
   - o upload de Excel adiciona ids sem olhar o limite (l. ~9084);
   - o Ctrl-clique ignora o limite (l. ~9015).
4. **O |Δ| é do agrupamento, não das carteiras.** `filter_grouping_return_deltas` (py l. 389-477) diz usar a pior carteira, mas itera `nav_grouping_docs` (l. 423), que é um documento por agrupamento. Um agrupamento com |Δ| pequeno publica carteiras cujo |Δ| individual passa do limite.
5. **O servidor não checa nada.** `nav_publish` (`pages/beehus_console.py:968-1022`) não recebe nem verifica divergência.

As unidades estão consistentes (percentual ÷ 100 dos dois lados); o problema não é esse.

**O que fazer: a verificação passa a ser do servidor, por data e por carteira**
1. **Front:** enviar `maxDeltaAbs` (o limite selecionado) no POST de cada dia.
2. **`nav_publish`, para cada `positionDate` recebido:**
   - buscar os resultados NAV **daquela data**, com as carteiras de cada agrupamento (`walletsWithNavDetailed`, da mesma chamada `nav_results`);
   - para cada agrupamento, calcular o **pior |rnps − rc|** entre as suas carteiras e o próprio agrupamento;
   - **bloquear** se esse valor for > limite **ou nulo** (decisão do usuário), e publicar só o resto;
   - devolver `blocked: [{groupingId, nome, walletId, carteira, delta, motivo: "acima_limite" | "sem_delta"}]`.
3. **Sem `maxDeltaAbs`:** o servidor deve recusar (400) ou usar um padrão seguro. Assim nenhum caminho do front (Excel, Ctrl-clique, seleção antiga) escapa. Sobre forçar a publicação, ver D6.
4. **Corrigir `filter_grouping_return_deltas`** para usar de fato a pior carteira, para que o seletor mostre o mesmo número que o servidor vai usar.
5. **Front, na faixa de datas:** deixar claro que o seletor mostra o |Δ| da data inicial e que os demais dias são checados na hora de publicar. Na linha de cada dia, mostrar "publicados X · bloqueados Y" com um link para a lista de bloqueados.
6. **Painéis Template:** a restrição de escopo e o "Deve Publicar = Não" (py l. 993-1003) continuam valendo, aplicados antes da checagem de Δ.

**Aceite**
- Limite 0,02%, um agrupamento com Δ < 0,02% mas com uma carteira de Δ 0,05%, seleção vazia: **não publica** e aparece como bloqueado ("acima_limite").
- Faixa de 3 dias com divergência só no dia 3: os dias 1 e 2 publicam e o dia 3 bloqueia aquele agrupamento.
- Carteira com Δ nulo na data: bloqueada ("sem_delta").
- Upload de Excel com um id acima do limite: bloqueado.
- Painel normal, Template e SLA: todos respeitam a regra.

**Teste:** usar uma empresa de teste ou uma data já publicada e despublicar depois. Confirmar no Beehus que o estado `published` bate com o log.

**Esforço:** M.

**Risco:** mais agrupamentos passam a ser bloqueados. Isso é o esperado; o log precisa explicar o motivo.

### SWAT-04: Groupings não aparecem

**Pedido:** "Corrigir BUG de não aparecer Groupings."

**De onde vêm:** `filter_groupings` (`pages/beehus_console.py:248-274`) filtra o `get_grouping_index()` pela company. Nos modos Template, cruza com `wallet_scope.agrupamentos(company_id)`, que usa a coluna G "Agrupamentos Indexados" do Template. Na cópia local do Template, o parse da coluna G está ok: 1.370 de 1.412 carteiras têm agrupamento, 1.137 ids distintos.

**Hipóteses, da mais provável para a menos provável (ver D7)**
- **A. Company desatualizada no iframe da ferramenta (painéis Template).**
  - A ferramenta carrega a lista de companies uma vez só (`init` com `_inited`, l. 3232 e 7348), e o iframe continua vivo. Já os headers de escopo são relidos a cada chamada.
  - Depois de trocar a company na barra de escopo, a ferramenta ainda pede `groupings?companyId=A` com o escopo em B, e recebe `[]`.
  - **Correção:** `ScopeBar.setCompany` (`controlpanel.html:1432-1438`) manda uma mensagem para cada `Funcoes._frames[*]`, e as ferramentas refazem o `init` e o reset. Isso se junta ao SWAT-01.
- **B. O seletor da Publicação esconde agrupamentos sem Δ.** `passes()` (l. 8898-8903) faz `if (!d || d.deltaAbs == null) return false`. Resolver junto com o SWAT-05: mostrar esses agrupamentos como "sem Δ (bloqueado)" em vez de sumir com eles.
- **C. Índice de agrupamentos parcial depois de erro 429.** `beehus_catalog._grouping_index_from_api` (l. 641-675) pula a company que falhou e guarda o resultado parcial em cache por 5 min. Os agrupamentos não têm o fallback que as carteiras têm (`data/template_wallet_companies.json`).

**Confirmação rápida (logado no app):** comparar as três URLs abaixo e ver qual devolve vazio.
- `/api/beehus/filters/groupings?companyId=<cid>`
- a mesma com `&_scope=template`
- a mesma com `&_scope=template&_scopeCompany=<outra>`

**Aceite:** nos três painéis, depois de trocar a company na barra de escopo, as ferramentas listam os agrupamentos da company certa.

**Esforço:** P (A) · M (A + B + C).

### SWAT-01: log temporário na tela e limpeza ao trocar de Company

**Pedido:** "Gerar um Log Temporário na tela do que foi feito, horário que foi finalizado… Limpar tela de datas do Executar e LOG do que foi feito ao selecionar outra Company ou mudar algo."

**Como está hoje**
- Processar, NAV Wallets, NAV Groupings e Publicação têm `#xxx-status` e uma lista de dias com data, ✓/✗, mensagem e segundos. Essa lista **não tem horário de término nem company**, e é apagada no próximo run ou no Limpar (`reset`, l. 7863).
- Transações só tem o `i-search-summary` (l. 3582). Implementar, Editar e Excluir terminam em `alert()` (l. 5509, 6386, 6424).
- O `ApiLog` (l. 2946) é log técnico, não histórico de negócio.

**O que fazer**
- **Componente.** Criar um `ActionLog` pequeno em `beehus_console.html`, embaixo do botão Executar de cada ferramenta (D5), só em memória.
- **Cada entrada:** ação, company, faixa ou quantidade de dias, contagens (ok / pulados / falhas / publicados / bloqueados), status e **horário de término `HH:MM:SS`**.
- **Onde gravar:** no fim de `makeDatesPipeline.run()` (l. 7839-7858), o que cobre as 4 ferramentas mais Excluir Posições e Explosão, e em `IdentifyTxn.search`, `runIdentify`, `runApply`, `runEdit` e `runDelete` (trocar o `alert` pela entrada no log).
- **Limpeza ao trocar de company:**
  - `makeDatesPipeline.onCompanyChange` (l. 7355; os add-ons sobrescrevem em l. 8048, 8310, 8647) e `IdentifyTxn.onCompanyChange` (l. 3367) limpam `#xxx-days`, `#xxx-status` e o log, e voltam as datas ao padrão do SWAT-08 (D5);
  - a barra de escopo também dispara essa limpeza (ver SWAT-04 A).

**Aceite:**
- Cada execução gera uma entrada com o horário de término.
- Trocar de company limpa o log e as datas.
- Implementar, Editar e Excluir no Transações aparecem no log.

**Esforço:** M.

**Depende de:** SWAT-08.

### SWAT-02: campo Token vazio e sem fechar ao arrastar

Ver **TRV-03** (vazio) e **TRV-02** (arrasto). Aqui:
- shell: `templates/shell.html:207-223`, input na l. 216, JS na l. 447-466;
- modais avulsos: `beehus_console.html:2664/2677`, `controlpanel.html:1327/1339` (JS l. 6515-6572) e `correcoes.html:306/319`.

### SWAT-03: token estourou → pop-up

Ver **TRV-01**. No swat, o wrapper de `fetch` de `base.html:15-75` é o ponto central. Remover os ~12 `alert` de `status===401`:
- `controlpanel.html:1960, 2633, 3657, 5025, 6341`;
- `beehus_console.html:5459`;
- `excecoes.html:1848`;
- `_strip_script.html:1093, 3350`;
- entre outros.

### SWAT-06: várias empresas ou "Todas" nos 5 executores

**Pedido:** "Permitir seleção de todas Empresas ou selecionar > 1 em Processar, NAV Wallets, NAV Groupings, Publicação e Transação."

**Como está hoje:** cada ferramenta tem um `<select>` único. `confirm()` e `run()` leem `$('company').value` (l. 7596, 7746), e todas as rotas recebem um `companyId` só. Já existe um precedente de loop no front: `_runProcessRange` (`controlpanel.html:2226-2290`), com botão de cancelar.

**O que fazer**
- **Loop no front, uma chamada por vez:** dia por fora, company por dentro. O backend não muda.
  - As linhas passam a ser chaveadas por `cid|dia` (`_renderDayRows` e `_setDayStep`, l. 7699-7725).
  - Uma falha marca a linha e o loop **continua** com as outras companies; hoje ele faz `break outer`.
- **Seletor multi-escolha** com "Todas". "Todas" = `/api/beehus/filters/companies`, que já respeita o escopo Template.
- **Com mais de uma company**, desabilitar os seletores de carteira e agrupamento ("todas de cada empresa").
- **Publicação** só pode ter várias companies **depois do SWAT-05**, porque a checagem de Δ precisa estar no servidor.
- **Transações:** N buscas juntadas numa tabela, com uma coluna Company. As listas de edição são por company, então esta parte é maior (G). Pode ficar para uma segunda etapa.
- **Erro 429:** manter as chamadas sequenciais, com uma pausa curta e backoff ao receber 429. O log de 25/09 mostra 6 companies batendo 429 ao mesmo tempo.

**Aceite:**
- Processar com 3 companies e 2 dias gera 6 linhas, com resultado e horário no log.
- Uma falha numa company não interrompe as outras.
- "Todas" respeita o escopo do painel.

**Esforço:** M para as 4 da fábrica, G para Transações.

**Risco:** os add-ons que sobrescrevem `confirm` e `run` (ProcessDates, NavWalletsDates).

### SWAT-07: lote de 250 no Identificar Transações

**Pedido:** "Limitar 250 transações por consulta". O usuário confirmou que é o lote.

**O que fazer:**
- trocar `IdentifyTxn.BATCH_LIMIT: 500 → 250` (`beehus_console.html:3207`, enviado como `limit` na l. 3481);
- ajustar os textos das l. 1320-1331 e 1462-1464;
- a validação do backend (py l. 1120-1131) já aceita 250;
- **não** mexer no `_TXN_SEARCH_CAP` nem no `_PTX_MAX_ROWS` (D8).

**Aceite:** uma busca com mais de 250 transações traz os lotes de 250, e os textos batem.

**Esforço:** P.

### SWAT-08: faixa de datas preenchida (D-7 a D-1)

**Pedido:** "Trazer as datas preenchidas na seleção faixa, default data inicial como D-7 e data final como D-1 em Processar, NAV Wallets, NAV Groupings, Publicação e Transação."

**Como está hoje:** os inputs começam vazios, e o `reset()` também os deixa vazios (l. 7866-7867; Transações l. 6446-6447). Tudo começa no modo "Data única". O front só conhece segunda a sexta (`_DateUtils`, l. 7242). O calendário ANBIMA existe só no servidor (`wallet_scope.deslocar_du`, `bizdays`).

**O que fazer**
- **Endpoint novo** `GET /api/beehus/util/default-range`, devolvendo `{ini: D-7du, fin: D-1du}` via `deslocar_du` (D4).
- **Ao escolher o modo Faixa**, e também no `reset` e na troca de company, preencher `ini` e `fin` e gravar `defaultValue`.
- **Ajustar o `_isPristine`** (`controlpanel.html:6778`) para considerar o valor padrão como "intocado". Senão, a limpeza de iframes ociosos para de funcionar.
- A Publicação dispara o `onRangeChange`, o que atualiza o seletor. Isso é esperado.

**Aceite:** nas 5 ferramentas, escolher Faixa já traz D-7 e D-1 em dias úteis, pulando feriados.

**Esforço:** P.

### SWAT-09: Transações com um scroll só

**Pedido:** "Atualmente há um Scroll bar dentro de outro Scroll bar… ficar apenas no Scroll bar da tela."

**Causa:**
- `.table-wrap { max-height: 50vh; overflow: auto }` (`beehus_console.html:119`) e `#i-result .table-wrap { max-height: calc(100vh - 240px) }` (l. 123) criam o scroll interno;
- os filtros acima empurram a página do iframe para além de `#tool-view { height: calc(100vh - 49px - 90px) }` (`controlpanel.html:333-342`), o que cria o scroll externo.

**O que fazer:** `#i-result .table-wrap { max-height: none; overflow-x: auto; }`. É o mesmo padrão das tabelas Issues e Strip (`controlpanel.html:27-33`).

**Efeito colateral:** com `overflow-x`, o cabeçalho sticky deixa de grudar ao rolar a página. Aceitar, como já acontece na tabela Issues, ou fazer um cabeçalho flutuante em JS (M). Conferir também que os 90px fixos não criam um terceiro scroll com a barra de menu expandida.

**Aceite:** na busca de Transações com muitas linhas, existe só a barra de rolagem da tela, e a rolagem horizontal da tabela continua funcionando.

**Esforço:** P.

---

## 5. Itens transversais (valem para os três projetos)

### TRV-01: token estourou → pop-up de colar token na hora

**Pedido:** "Quando tentar algo e estourar o token, já mostrar na tela o pop up de colar o token" (conciliacao, ControleCargas e swat).

**Como está hoje**

- **Detecção no cliente.** O status 401 **ou 403** do Beehus liga a flag `rejected` e lança `BeehusAuthError`. Quando não há token, `_headers()` lança o mesmo erro sem ligar a flag.
  - conciliacao e swat: `beehus_api/client.py:279-285` e `:331-337` (os dois arquivos são idênticos, exceto a linha 28).
  - CC: `prototype/beehus_api/client.py:585-593`.
  - `exceptions.py` é igual nos três.
- **Como o erro chega na tela.**
  - conciliacao: `utils/respostas.py:27-48` devolve 401 `{error, upstream_status, upstream_body}`, sem código próprio.
  - swat: dois helpers devolvem 401 (`pages/beehus_console.py:106-112`, `pages/conciliacao.py:734-739`). Porém ~39 blocos `except BeehusAPIError` e várias rotas em lote devolvem 502, ou 200 com o erro dentro do corpo (ex.: `pages/excecoes.py:1902-1905`).
  - CC: 4 rotas devolvem 401 com uma mensagem amigável (`app.py:847`, `:1355`, `:1439`, `pages/carteiras_nao_cadastradas.py:232`).
- **Erro engolido.** O `beehus_catalog.py` captura `BeehusAuthError` junto com `Exception` (21 lugares no conciliacao, 30 no swat). Exemplo: no conciliacao, `/api/conciliacao-mov/rows` devolve **200 `{"rows":[]}`** com o token vencido (`pages/conciliacao_mov.py:118-142`). Olhar só o status HTTP não resolve.
- **Conflito com o login local.** No conciliacao e no swat, o `auth.py:98` já devolve `("unauthorized", 401)` em texto quando falta o cookie de sessão. Por isso o front **não pode** tratar todo 401 como token vencido.
- **Tela.**
  - conciliacao e swat: um banner consulta `/api/beehus/token` a cada 60 s e mostra o link "Colar token →" (`templates/partials/_token_banner.html:18,30-57`). Nada reage na hora em que a ação falha.
  - swat: há ~12 `alert` espalhados para `status===401`.
  - CC: o modal abre sozinho só quando a página carrega (`static/js/controle_cargas/beehus_token.js:26-33`).

**O que fazer (mesmo desenho nos três)**

*Backend*
1. **Hook `after_request`.** Para toda resposta `/api/*` que não seja a própria rota de token: se `token_status()` indicar `rejected` ou nenhum token carregado, adicionar o header **`X-Beehus-Token: expired`**.
   - Isso cobre erro engolido, erro dentro de lote e erro em thread de fundo, porque a flag é "grudenta".
   - No CC funciona por sessão, porque o `before_request` já amarra o `sid`.
2. **Código de erro.** Acrescentar `error_code: "BEEHUS_TOKEN_EXPIRED"` nos helpers de 401 que já existem e registrar `@app.errorhandler(BeehusAuthError)` como rede de segurança. O status continua 401.
3. **Antes de mexer no 403:** descobrir o que a API devolve para token vencido.
   - Se for só 401, parar de tratar 403 como token rejeitado. Senão, um 403 de permissão abriria o pop-up.
   - Se a API usa 403 para token vencido, manter como está.

*Frontend: `static/js/utils/beehus_token_guard.js`, o mesmo arquivo copiado nos três*
1. **Envolver `window.fetch`.** Se a resposta vier com `X-Beehus-Token: expired` e a URL não for a rota de token:
   - dentro de iframe: `parent.postMessage({type:'beehus-token-expired'}, location.origin)`;
   - fora de iframe: abrir o modal local.
   A resposta segue sem alteração para quem chamou.
2. **No shell**, tratar a mensagem `beehus-token-expired`, conferindo `event.origin`. Abrir o modal com o campo **vazio** e o texto "Seu token expirou, cole um novo".
3. **Evitar reabrir em loop.** Não reabrir se o modal já estiver aberto, e esperar ~20 s depois de o usuário fechar. Sem isso, o polling do banner (60 s) e os pollings de 2,5 s do swat ficariam reabrindo o modal.
4. **Depois de salvar:** avisar os iframes e mostrar "Token salvo. Repita a ação." (ver D11). **Não** repetir POSTs automaticamente.
5. **Remover os ~12 `alert` de 401 no swat**, para não aparecerem o alert e o modal ao mesmo tempo.

*Onde encaixar em cada projeto*
- **swat:** dentro do wrapper de `fetch` que já existe em `templates/base.html:15-75` (hoje ele injeta os headers `X-Swat-Scope`). Precisa reestruturar para rodar sempre, e não só quando há escopo. Todas as 14 páginas estendem `base.html`.
- **conciliacao:** no `<head>` do `base.html` da página, mais o tratamento da mensagem no `templates/shell.html`. O `Token.open()` fica em `shell.html:31` e o modal em `:55-71`.
- **CC:** dentro do `beehus_token.js` (não precisa de tag nova). Se for criar uma tag nova, `index.html` e `index_template.html` precisam ficar **idênticos**.
  - Hoje o token usa o modal genérico (`paineis.js:22-33`), então abrir o token **substituiria** um painel aberto. Criar um elemento de modal próprio para o token.

**Critérios de aceite**
- Com o token apagado ou inválido, qualquer ação que chama a API abre o pop-up na hora, com o campo vazio.
- Isso vale também para ações cujo erro hoje é engolido, por exemplo carregar as linhas da conciliação.
- Um 401 do login local (sem cookie) **não** abre o pop-up.
- O pop-up não reabre em loop enquanto estiver aberto, nem logo depois de ser fechado.
- Depois de colar um token válido, repetir a ação funciona.

**Teste manual**
1. Apagar o token com o `DELETE /api/beehus/token` (conciliacao e swat) ou pela rota equivalente do CC, e disparar uma ação.
2. Colar um token propositalmente inválido (último caractere trocado) e disparar uma ação.
3. Deixar a tela parada por 3 minutos com o token vencido e confirmar que o pop-up não pisca.

**Esforço:** conciliacao P–M · swat M · CC P.

**Atenção:** o conciliacao tem uma alteração **não commitada** justamente em `utils/respostas.py`. Ver PREP.

---

### TRV-02: modal fecha ao arrastar a seleção de texto

**Pedido:**
- conciliacao: "Ao selecionar o range de um valor para editar, ele fecha a janela da transação".
- swat: "[campo Token não deve] fechar quando arrasta o range de seleção com o mouse".

**Causa (a mesma em todos os projetos, e reproduzida com Playwright)**

Os fundos dos modais fecham no `click` com `if(event.target===this) fechar()`. Quando o mouse desce dentro do input e sobe em cima do fundo, o navegador entrega o `click` ao ancestral comum, que é o próprio fundo, e o modal fecha. O `onclick="event.stopPropagation()"` no painel interno não ajuda.

**Onde o padrão aparece**
- **conciliacao** (10 lugares):
  - `static/js/conciliacao_mov/edicao.js:83` (transação), `:267` (provisão) e `:351` (preço de execução);
  - `templates/conciliacao_mov.html:274, 291, 312, 334, 435`;
  - `templates/shell.html:55-56` (token) e `:80-81` (cache).
- **swat** (~45 lugares):
  - `beehus_console.html` (24), `conciliacao_mov.html` (10), `controlpanel.html` (8, mais `:5932-5938`);
  - modais de token em `shell.html:208`, `beehus_console.html:2664/2677`, `controlpanel.html:1327/1339` e `correcoes.html:306/319`.
  - O swat já tem o padrão certo em um lugar: `_guardedClose` em `templates/precificacao.html:1514-1528`.
- **CC:** o fundo do modal genérico, em `static/js/controle_cargas/paineis.js:824` (`e.target.id==='modal-backdrop'`).

**O que fazer**
1. **Guarda global em fase de captura** (`static/js/utils/guarda_arrasto.js`, um por projeto):
   - no `mousedown`, guardar o alvo;
   - no `click`, cancelar (`stopImmediatePropagation` + `preventDefault`) **somente se** as três condições valerem:
     - o `mousedown` começou em `input`, `textarea`, `select`, `[contenteditable]` ou em texto que ficou selecionado;
     - o alvo do `click` é diferente do alvo do `mousedown`;
     - o alvo do `click` **contém** o alvo do `mousedown`.
   - Não cancelar qualquer clique em ancestral, porque isso quebraria cliques normais em botões que têm um `<span>` dentro.
2. **Tirar o fechamento por clique no fundo** dos modais de **token** e de **criar/editar transação/provisão/preço**, deixando só os botões Fechar e Esc (ver D12). O Esc já fecha no conciliacao (`detalhe_blocos.js:1030`).
3. **`shell.html` é outro documento** (as páginas rodam dentro de iframe). Precisa carregar a guarda também.

**Critérios de aceite**
- Arrastar para selecionar texto num campo e soltar em cima do fundo **não** fecha nenhum modal.
- Clique simples no fundo continua fechando os modais que devem fechar.
- Botões com ícone ou `<span>` interno continuam respondendo ao clique.

**Teste manual:** em cada modal listado, arrastar do meio do campo até o fundo escuro. Depois clicar no fundo. Depois apertar Esc.

**Esforço:** P por projeto.

---

### TRV-03: campo token começa vazio

**Pedido (swat):** "Campo Token não deve começar preenchido".

**Causa:** o código nunca preenche o campo. O que acontece é o seguinte:
- `Token.close()` só esconde o modal. O input é limpo **apenas depois de salvar com sucesso** (swat `shell.html:447-466`; conciliacao `shell.html:296`). Uma colagem que falhou e foi fechada volta a aparecer na próxima abertura.
- O input é `type="password"` sem `autocomplete` (swat `shell.html:216`), então o gerenciador de senhas do navegador pode preenchê-lo.

**O que fazer**
- Limpar o input no `open()` e no `close()`.
- Acrescentar `autocomplete="new-password" data-lpignore="true" data-1p-ignore spellcheck="false"`.
- Aplicar o mesmo nos modais de token avulsos do swat (`beehus_console.html`, `controlpanel.html:6515-6572`, `correcoes.html`) e no `shell.html` do conciliacao, que é idêntico.
- O CC já recria o modal a cada abertura e não precisa mudar.

**Aceite:** abrir o modal sempre mostra o campo vazio, inclusive depois de uma colagem que falhou e depois de salvar.

**Esforço:** P.

---

## 6. Achados fora do pedido (não fazer sem aval do usuário)

| # | Projeto | Achado | Sugestão |
|---|---|---|---|
| A1 | conciliacao | `beehus_api/client.py:28` ainda usa o host antigo `controladoria.beehus.com.br`. O swat e o CC usam `api.controladoria.beehus.com.br`. | Confirmar se há timeouts. Se houver, alinhar o host (P). |
| A2 | CC × conciliacao/swat | Os três gravam o **mesmo** `~/.swat/beehus.token`, mas em formatos incompatíveis: o CC grava `{"sessions":{…}}` e os outros gravam `{"token","set_at"}`. Depois de um restart, um app descarta o token do outro. Isso também atrapalha o teste do TRV-01. | Separar os arquivos (ex.: `~/.swat/beehus_cc.token`) ou fazer o CC ler o formato antigo (P). |
| A3 | todos | O cliente trata **403** como token rejeitado. | Ver o passo 3 do backend no TRV-01. |
| A4 | swat | O `iniciar.bat` faz `git pull` a cada início. | Avisar quem trabalha em branch de feature, ou tirar o pull do .bat. |
| A5 | swat | `filter_grouping_return_deltas` diz usar a "pior carteira", mas usa o documento do agrupamento. | Está incluído no SWAT-05. |
