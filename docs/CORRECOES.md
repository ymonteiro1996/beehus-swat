# Correções — arquivo temporário único para geração de arquivos

> A página **Correções** (`/correcoes`) é, a partir desta refatoração, o **único lugar do projeto** que gera arquivos JSON ou Excel exportáveis. Todas as demais páginas (Painel, Conciliação, etc.) deixam de gerar arquivos diretamente e passam a **adicionar linhas** neste armazenamento temporário, que o usuário revisa, edita e exporta aqui.

---

## Visão geral

A página armazena coleções editáveis de linhas por **empresa + data**:

| Tipo            | Origem                                                              | Destino final                      |
|-----------------|---------------------------------------------------------------------|------------------------------------|
| Transações      | correções vindas de Painel (Anomalias em ativos) e de Conciliação   | `transactions.json` / aba Excel `Transactions` |
| Provisões       | correções vindas de Conciliação                                     | `provisions.json` / aba Excel `Provisions` **ou** `POST /beehus/provisions` (push direto via botão *Enviar via API* na aba Provisões) |
| Para Excluir    | bookkeeping interno (MISCLASSIFIED) — não exportado                  | apontador para exclusão manual no DB |
| Preços de Execução | aceite de `MISSING_EXECUTION_PRICE` na Conciliação                | `POST /beehus/financial/execution-prices` (push direto, sem arquivo) |

O formato dos registros de Transações/Provisões segue exatamente [FILE_GENERATION.md](FILE_GENERATION.md) §1 e §2. **Preços de execução** seguem o payload da API upstream (`companyId`, `walletId`, `securityId`, `positionDate`, `executionPrice` — mesmos campos do formulário em *Funções > Adicionar Preço de Execução*).

---

## Armazenamento em disco

```
data/correcoes/<companyId>/<date>/<walletId>.json
{
  "companyId":       "...",
  "walletId":        "...",
  "date":            "YYYY-MM-DD",
  "updatedAt":       "ISO-8601",
  "transactions":    [ { "id": "uuid", ...campos de FILE_GENERATION §1 }, ... ],
  "provisions":      [ { "id": "uuid", ...campos de FILE_GENERATION §2 }, ... ],
  "deletions":       [ { "id": "uuid", "originalId": "...", ... }, ... ],
  "executionPrices": [ { "id": "uuid", "inputed": false, ... }, ... ]
}
```

**Bucket `executionPrices`** — campos persistidos:

| Campo                 | Tipo    | Origem |
|-----------------------|---------|--------|
| `companyId`           | string  | wallet → `companyId` resolvido no servidor |
| `walletId`            | string  | item aceito |
| `securityId`          | string  | item aceito |
| `securityName`        | string  | display only |
| `positionDate`        | YYYY-MM-DD | data da diagnóstico |
| `executionPrice`      | number  | preço sugerido (`expectedExecPrice` do step 3.3 ou `−actualBalance/amountDiff`) |
| `expectedExecPrice`   | number  | snapshot do diagnóstico (igual ao `executionPrice` na criação) |
| `pu`                  | number  | snapshot do PU usado pelo sistema |
| `priorExecutionPrice` | number  | `executionPrice` que estava na posição |
| `amountDiff`          | number  | snapshot da Δqtd |
| `actualBalance`       | number  | snapshot do Σbalance(buySell) |
| `expectedValue`       | number  | snapshot de `−amountDiff × pu` |
| `description`         | string  | livre |
| `inputed`             | bool    | `true` após push bem-sucedido para a API upstream |
| `inputedAt`           | string  | ISO-8601 do push |
| `beehusId`            | string  | `_id` retornado pela API upstream |

- Um arquivo por tripla (companyId, date, walletId). Mantém o agrupamento natural usado pelos endpoints de geração (`generate-transactions` / `generate-provisions`).
- Cada linha recebe um `id` (UUID v4) estável usado pelo CRUD.
- Diretórios são criados sob demanda e podados quando esvaziam (wallet, date, companyId).
- Arquivos são escritos via `tmp + os.replace` para evitar corrupção em caso de interrupção.
- Validação rígida de segmentos de caminho: `[A-Za-z0-9_-]+` e data `YYYY-MM-DD`. Qualquer outro valor retorna 400.

---

## Endpoints

Blueprint: `pages/correcoes.py`.

| Método | Rota                           | Propósito |
|--------|--------------------------------|-----------|
| GET    | `/correcoes`                   | Página HTML |
| GET    | `/api/correcoes/dates`         | 10 pills de datas úteis com contagem total de linhas por data. Se `companyId` não for passado, agrega em **todas as empresas visíveis** ao usuário (`get_company_filter()`); caso contrário, escopa só àquela empresa. Quando `endDate` **não** é passado, o backend ancora a janela no **último dia que de fato contém correções** em disco (`_latest_date_with_data`) — se não há nada persistido, cai para hoje. Passar `endDate` explicitamente sempre respeita o valor |
| GET    | `/api/correcoes`               | Lê todas as linhas de uma data combinando os arquivos de todas as wallets. Se `companyId` não for passado, retorna linhas de **todas** as empresas visíveis. Cada linha carrega `companyId`. Resposta: `{transactions, provisions, wallets, walletsByCompany, companies}` — `wallets`: `{walletId: walletName}` achatado; `walletsByCompany`: `{companyId: {walletId: walletName}}` para o cascading dropdown do modal; `companies`: `{companyId: companyName}` |
| POST   | `/api/correcoes/items`         | Cria uma linha. Body: `{companyId, date, walletId, kind, row}` onde `kind ∈ {"transactions","provisions"}` |
| PUT    | `/api/correcoes/items`         | Atualiza uma linha. Body: igual ao POST, `row.id` obrigatório |
| DELETE | `/api/correcoes/items`         | Remove uma linha. Body: `{companyId, date, walletId, kind, id}` |
| POST   | `/api/correcoes/bulk`          | Anexa várias linhas agrupadas por wallet. Body: `{companyId, date, wallets: {walletId: {transactions:[], provisions:[]}}}` — usado pelos botões "aceitar correção" das páginas upstream |
| POST   | `/api/correcoes/bulk-submit`   | **Grava E envia direto pra API** em uma só chamada. Mesmo body do `/bulk`. Persiste as linhas (idempotente por `sourceAnomalyKey`, trilha de auditoria) e em seguida faz o push upstream de cada linha dos buckets `transactions`/`provisions`/`executionPrices` que ainda não estão `inputed` (reusa os mesmos cores `_push_*_row` dos `/submit` individuais). `deletions` são gravadas mas **nunca** enviadas. Usado pelo fluxo *Aceitar → modal de revisão → Confirmar* da Conciliação > Diagnóstico. Resposta `200`: `{added, skipped, rejected, submitted, failed, authFailed, results:[{walletId, kind, id, securityId, ok, beehusId?, error?, upstream_status?}]}`. **`200` não garante envio total** — o chamador deve inspecionar `failed`/`authFailed`/`rejected`. Linhas que falharam ficam com `inputed=false` para reenvio (re-aceitar só reenvia o que falta) |
| GET    | `/api/correcoes/anomaly-keys`  | Retorna `{keys: [...]}` com os `sourceAnomalyKey` distintos que já estão na loja para uma (empresa, data), unindo os buckets `transactions`, `provisions` e `executionPrices`. Páginas upstream usam para marcar linhas já movidas (ex.: Painel cinza) |
| POST   | `/api/correcoes/export`        | Gera e devolve o arquivo final. Body: `{companyId, date, format: "json"|"xlsx"}` |
| POST   | `/api/correcoes/execution-prices/submit` | Envia uma linha de `executionPrices` para a API upstream (`POST /beehus/financial/execution-prices`). Body: `{companyId, date, walletId, id}`. Em sucesso, marca a linha com `inputed=true`, `inputedAt=<utc-iso>`, `beehusId=<upstream _id>`. Em erro, devolve o status/body upstream para o usuário corrigir e reenviar |
| POST   | `/api/correcoes/provisions/submit` | Envia uma linha de `provisions` para a API upstream (`POST /beehus/provisions` — mesma função `beehus_api.create_provision` usada por *Funções > Criar Provisão* em `/beehus`). Body: `{companyId, date, walletId, id}`. `currencyId` é **sempre** resolvido da `db.wallets.<walletId>.currencyId` no momento do envio — o valor armazenado na linha é ignorado (pode estar obsoleto). Em sucesso, marca a linha com `inputed=true`, `inputedAt=<utc-iso>`, `beehusId=<upstream _id>` e persiste o `currencyId` autoritativo de volta na linha. Em erro, devolve o status/body upstream para o usuário corrigir e reenviar. A linha permanece em disco com `inputed=true` para auditoria — não é re-aplicada localmente pelos pipelines de diagnóstico (filtro embutido em `load_*_pending_provisions` e em `load_corrections_for_wallet`) |
| POST   | `/api/correcoes/transactions/submit` | Envia uma linha de `transactions` para a API upstream (`POST /beehus/financial/transactions` — mesma função `beehus_api.create_transaction` usada por *Funções > Criar Transação* em `/beehus`). Body: `{companyId, date, walletId, id}`. Encaminha `entityId`, `balance`, `operationDate`, `liquidationDate`, `currencyId`, `beehusTransactionType`, `description`, `comment`, `hide`, `inputType` e `securityId`. `currencyId` é **sempre** re-resolvido da `db.wallets.<walletId>.currencyId` no envio (o valor da linha é descartado para evitar moeda errada por dado legado ou troca de moeda da carteira após a captura); `entityId` ausente na linha é resolvido da `db.wallets`. Em sucesso, marca a linha com `inputed=true`, `inputedAt=<utc-iso>`, `beehusId=<upstream _id>` e persiste o `currencyId` autoritativo de volta na linha. A linha permanece em disco com `inputed=true` — `load_corrections_for_wallet` filtra `inputed=True` para que a transação não seja injetada de novo no diagnóstico local |

### Validação

- Todas as rotas passam por `company_visible(companyId)` antes de ler/escrever.
- Campos dos registros são filtrados por **whitelist** antes de serem persistidos: `_TXN_FIELDS` e `_PROV_FIELDS` no módulo. Qualquer campo fora da lista é descartado silenciosamente. Isso impede que a UI injete campos arbitrários.
- `walletId` na linha é sempre sobrescrito pelo valor do path para coerência.
- `balance` é coagido para `float` (ou `0` se inválido).

### Campos meta internos

Além dos campos exportáveis, cada linha pode carregar metadados internos preservados em disco mas **nunca** incluídos no arquivo exportado:

| Campo              | Descrição |
|--------------------|-----------|
| `sourceAnomalyKey` | Chave composta `"companyId|date|walletId|flag|securityId"` que liga a correção de volta à anomalia/item que a gerou. Consultada via `GET /api/correcoes/anomaly-keys` pelas páginas upstream para marcar itens já movidos |
| `inputed`          | `true` após a linha ter sido enviada com sucesso para a API upstream — seja pelos botões *Enviar via API* por linha (executionPrices, provisions, transactions) em `/correcoes`, seja pelo `POST /api/correcoes/bulk-submit` disparado no *Aceitar* da Conciliação. Linhas inputadas são preservadas em disco para auditoria mas ficam fora do export e fora do recálculo local de gap |
| `inputedAt`        | ISO-8601 (UTC) do envio bem-sucedido |
| `beehusId`         | `_id` retornado pela API upstream — útil para rastrear o documento criado |

A chave é **wallet-level** (sempre contém o `walletId`) para que a mesma chave possa ser produzida por qualquer página upstream (Painel agregado por ativo, ou Conciliação item-a-item) sem ambiguidade. `_META_FIELDS` contém a lista — para adicionar outro metadado basta incluir o campo nessa tupla: `_pick` o preserva no write e `_strip_internal` o remove no export.

### Idempotência do bulk append

`POST /api/correcoes/bulk` é **idempotente por `sourceAnomalyKey`**: ao anexar, o endpoint consulta o índice de chaves já presentes no arquivo da wallet e pula qualquer linha cuja chave já existe. A resposta inclui `{added, skipped}` com as contagens por tipo. Isso garante que re-clicar "Enviar para Correções" (Painel) ou "Aceitar" (Conciliação) não duplica linhas.

---

## Exportação

`POST /api/correcoes/export` é o **único** lugar do projeto que emite arquivos para download. Para a mesma (empresa, data), combina todas as linhas de todas as wallets:

- **JSON**: payload `{"transactions": {"companyId", "transactions": [...]}, "provisions": {"provisions": [...]}}` — cada seção só aparece se tiver linhas. Os `id` internos são removidos. Nome: `correcoes_<companyId>_<date>.json`.
- **XLSX**: workbook com aba `Transactions` e/ou `Provisions` (usando `openpyxl`). Colunas na ordem de `_TXN_FIELDS` / `_PROV_FIELDS`. Nome: `correcoes_<companyId>_<date>.xlsx`.

Se não há nenhuma linha persistida para aquela (empresa, data), retorna 400 com mensagem `"nenhuma correcao para exportar"`.

---

## Página (`templates/correcoes.html`)

A página **não tem filtro global por empresa**. Em vez disso, mostra linhas de **todas as empresas visíveis** de uma vez e oferece filtros por coluna.

- Cabeçalho: badge **Token** (clicável) à direita do título — abre o mesmo modal de Bearer Token usado em `/beehus` (página *Funções*). O token é compartilhado entre as páginas (estado em memória de processo em `beehus_api/client.py`) e usado pelos botões *Enviar via API* das abas Provisões e Preços de Execução. Endpoints reutilizados: `GET/POST/DELETE /api/beehus/token`. O badge troca de cor: verde quando carregado, vermelho quando ausente, e é refrescado automaticamente após qualquer falha de envio para sinalizar token expirado/revogado.
- Modal de Adicionar/Editar **não expõe** o campo `currencyId`. O backend resolve a moeda diretamente da carteira (`db.wallets.<walletId>.currencyId`) em todos os endpoints de escrita (`POST /api/correcoes/items`, `PUT /api/correcoes/items`, `POST /api/correcoes/bulk` e o helper interno `append_rows_for_wallet`) — vale tanto para `transactions` quanto para `provisions`. Qualquer `currencyId` enviado pelo cliente é descartado/sobrescrito; isto evita que um default obsoleto de "BRL" no front-end (Painel, Conciliação, modal manual) corrompa carteiras em USD/EUR/etc. Fallback é `"BRL"` apenas quando a wallet não tem o campo.
- Topo: apenas `<input type="date">` de "Data final".
- **Pills de 10 datas úteis** — agregam contagem total em todas as empresas visíveis. Auto-carrega ao abrir a página (última data por padrão).
- **Barra de ações**: `+ Adicionar`, `Exportar ▾`, `Limpar filtros` (só aparece se há filtros ativos), status.
- **Abas** `Transações` / `Provisões` com badges de contagem `visíveis/total` quando há filtro ativo.
- **Tabela por aba** — colunas: `Empresa`, `Carteira`, `Tipo`, datas, valor (pt-BR), descrição, securityId, ícone × para excluir rapidamente.
  - Cada linha do `<thead>` tem uma segunda linha (`.filter-row`) com inputs `<input class="col-filter">` que fazem **filtragem client-side** nas colunas: Empresa, Carteira, Tipo, Descrição, Security ID. O evento `input` dispara `renderRows()` imediatamente.
  - Filtro por Empresa / Carteira casa pelo **nome** (resolvido via `_companies` / `_wallets`), não pelo id.
- **Clique na linha** → modal de edição com todos os campos relevantes. Para adição/edição, o modal expõe um dropdown de **Empresa** que dispara `_onCompanyFieldChange()` para repopular o dropdown de **Carteira** com as wallets daquela empresa (`_walletsByCompany[companyId]`).
- **Adicionar** abre o mesmo modal em branco, com a aba ativa determinando o tipo (transação ou provisão). Se o filtro de coluna "Empresa" estiver preenchido, a empresa default no modal é a que casa com o filtro; caso contrário, a primeira empresa disponível.
- **Exportar** abre um mini-popover com:
  1. Select de **Empresa** (populado só com empresas que têm linhas na view atual).
  2. Dois botões: **JSON** / **XLSX**.
  O backend (`/api/correcoes/export`) continua exigindo `companyId` — export é sempre per-empresa (mesma data). Para exportar várias empresas, o usuário gera um arquivo por empresa.

---

## Migração das páginas upstream

> Esta seção será preenchida em iterações seguintes, quando cada página for ajustada. A lista abaixo é o escopo de redirecionamento acordado.

Serão redirecionados para `POST /api/correcoes/bulk` e perderão o download direto:

- **Painel** → aba *Anomalias em ativos* → botão renomeado para **"Enviar para Correções"**. Clicar no botão agora:
  1. Agrupa as linhas selecionadas por `walletId`.
  2. Para cada carteira, chama `generate-transactions` + `generate-provisions` com `items` carregando o `sourceAnomalyKey` wallet-level (`companyId|date|walletId|flag|securityId`).
  3. Envia o resultado via `POST /api/correcoes/bulk` (idempotente — dedupa por chave).
  4. Re-busca `GET /api/correcoes/anomaly-keys` e re-renderiza a tabela — uma linha aparece **cinza** (opacidade 50%, flag com line-through, sem checkbox) quando **todas** as carteiras dela já têm a correção na loja. Se o usuário deletar alguma das correções em `/correcoes`, a linha volta a ser selecionável.
- **Painel** → aba *Preços de Execução* → mesma mecânica, escopo wallet+security:
  1. `GET /api/conciliacao/global-execution-prices?companyId=…&date=…` lista todos os candidatos (Δqtd ≠ 0 com transação buySell e preço implícito divergindo > 0,5% do `executionPrice or PU` usado pelo sistema). Cada linha já vem com `accepted: bool` (matching por `sourceAnomalyKey` no bucket `executionPrices`).
  2. Usuário marca os checkboxes desejados → "Enviar para Correções" agrupa por `walletId`, chama `POST /api/conciliacao/generate-execution-prices` por carteira.
  3. Os payloads convertidos vão para `POST /api/correcoes/bulk` no bucket `executionPrices`. Idempotente pelo mesmo `sourceAnomalyKey`.
  4. Re-busca `anomaly-keys` e marca as linhas como `✓ Em Correções`. A partir daí o usuário visita `/correcoes` na aba *Preços de Execução* para clicar em **Enviar via API** linha a linha.
- **Conciliação** → botões **"Aceitar"** (individual), **"+ Transação"** / **"+ Provisão"** (override manual) e **"Aceitar Correção"** (bulk, Bayesian best-fix):
  - Cada item clicado é tagueado com o `sourceAnomalyKey` wallet-level usando `_currentWalletId` como walletId.
  - Chama `generate-transactions` + `generate-provisions` + `generate-execution-prices` para materializar as linhas e **abre o modal `confirm-send-modal`** ("Revisar e enviar via API") — tabelas editáveis com o mesmo procedimento da página Correções (editar célula, remover linha).
  - Ao **Confirmar**, envia tudo via `POST /api/correcoes/bulk-submit`: as linhas são **gravadas em `/correcoes` (auditoria) E enviadas direto pra API Beehus** na mesma chamada. O modal mostra o resultado por linha; falhas ficam em `/correcoes` (`inputed=false`) e o botão vira **"Reenviar pendentes"** (idempotente — só reenvia o que falta). Em sucesso total, o botão de aceite vira "✓ Aceito".
  - `deletions` (MISCLASSIFIED) são gravadas como auditoria mas **não** enviadas — a transação original precisa ser removida manualmente no upstream (o modal avisa).
  - Ao abrir o modal de diagnóstico, a página busca `GET /api/correcoes/anomaly-keys` e pré-marca como "✓ Aceito" qualquer botão cujo key já está na loja. A marcação é feita lendo `button[data-anomaly-key]` — o `_acceptBtn` injeta esse atributo em cada botão renderizado.
  - Os endpoints `generate-transactions` / `generate-provisions` permanecem vivos como *conversores internos* `items → rows` e agora propagam `sourceAnomalyKey` quando recebido.
- **Conciliação → Replicação de Cenário** → **removido completamente** (endpoint `replicate-scenario`, modal `#replicate-modal`, botão "Replicar Cenário", funções `openReplicateModal` / `closeReplicateModal` / `executeReplicate` / `_repDownloadBtn` / `_downloadRepFile` — todos deletados).

---

## Integração com o pipeline de diagnóstico (`pages/conciliacao.py`)

A partir desta iteração, todas as linhas persistidas em `/correcoes` são consideradas pelo motor de análise como se já estivessem aplicadas. Isso mantém a UI consistente — flags que foram aceitos "desaparecem" da próxima análise.

### Helper público

`pages.correcoes.load_corrections_for_wallet(company_id, date, wallet_id) → (transactions, provisions, deletions)` — retorna as linhas cruas do arquivo `data/correcoes/<companyId>/<date>/<walletId>.json` (ou `([], [], [])` quando inexistente/invalido). Semântica: *correções aceitas na data X*. Usada pelo pipeline de diagnóstico para injetar correções do dia corrente.

`pages.correcoes.load_active_pending_provisions(company_id, wallet_id, target_date) → [provision, ...]` — varre **todas** as subpastas `YYYY-MM-DD` sob `data/correcoes/<companyId>/` e coleta provisões do `<walletId>.json` cuja janela ativa `[initialDate, liquidationDate)` **contém** `target_date`. Cada linha retornada inclui um campo extra `acceptanceDate`. Semântica: *provisões pendentes **ativas** nesta data*, independentemente de onde foram aceitas. Usada em endpoints de visualização e no estimador *rough* de gap-reduction.

`pages.correcoes.load_all_pending_provisions(company_id, wallet_id) → [provision, ...]` — como acima, mas **sem** filtro de data. Retorna todas as provisões pendentes da carteira em todas as pastas de aceitação, cada uma com o campo `acceptanceDate`. Usada pelo pipeline de diagnóstico e por `global-analysis`, onde é necessário aplicar tanto o filtro de janela ativa (`initialDate ≤ date < liquidationDate` → injeção em `prov_map`) quanto o filtro de *lifecycle* (`initialDate == date OR liquidationDate == date` → injeção em `prov_lifecycle_sids`). Mantém o mesmo consumidor-filtra-seu-subconjunto já implementado na linha ~861.

`pages.correcoes.load_all_pending_provisions_by_wallet(company_id) → {walletId: [provision, ...]}` — varre a árvore inteira de `data/correcoes/<companyId>/*/*.json` uma única vez e agrupa provisões por `walletId`. Otimização N+1→1 usada em `/api/conciliacao/rows` (evita rescannear o diretório para cada wallet da listagem).

`pages.correcoes.load_pending_execution_prices(company_id, wallet_id) → [executionPrice, ...]` — varre todas as pastas `YYYY-MM-DD` da empresa e retorna as linhas de `executionPrices` da carteira. Cada linha inclui `acceptanceDate`. **Pipelines que aplicam correções de preço devem filtrar `inputed == False`** — linhas inputadas já estão refletidas no upstream e aplicar localmente causaria contagem dupla.

> **Importante — `acceptanceDate` vs janela ativa:** a pasta em que uma
> correção é persistida é a **data de aceitação** (dia em que o usuário
> clicou "Aceitar" no modal), não necessariamente a data em que a provisão
> está ativa. A janela ativa é definida pelos campos `initialDate` e
> `liquidationDate` da provisão (regra upper-bound exclusive:
> `initialDate ≤ target_date < liquidationDate`).
>
> Exemplo concreto do fluxo OFFSET_OR_SETTLEMENT_DRIFT:
> - Usuário no modal da data **2026-04-13** aceita uma hipótese de offset drift
> - A provisão gerada tem `initialDate=2026-04-10`, `liquidationDate=2026-04-13`
> - Ela é salva em `data/correcoes/<company>/2026-04-13/<wallet>.json` (pasta
>   = data da aceitação)
> - O pipeline de diagnóstico em **2026-04-10** precisa enxergar essa provisão
>   mesmo não havendo pasta `2026-04-10` — por isso o helper varre todas as
>   pastas e filtra por janela ativa, não por nome da pasta.

### Pontos de injeção

| Endpoint | O que muda |
|----------|------------|
| `GET /api/conciliacao/diagnose` | Injeta as transactions pendentes em `txns_by_security` / `wallet_txns` / `all_txns_flat` (cada uma carrega `pending: True` para rastreamento). Injeta provisions pendentes em `prov_map` (quando `initialDate ≤ date < liquidationDate`) e `prov_lifecycle_sids` (quando `initialDate == date` ou `liquidationDate == date`). `event_txns_by_sec` é derivado de `all_txns_flat` depois da injeção, logo também absorve as correções. Adiciona ao `step1`: `correctionsCount`, `correctionsImpact` (soma de `\|balance\|`), `recalculatedGapCash`, `recalculatedGapPct`. O gap recalculado fecha `gap_cash` em direção a zero na magnitude total das correções |
| `GET /api/conciliacao/wallet-detail` | Adiciona ao payload: `correctionsCount` e `correctionsImpact`. A página de detalhe da carteira usa esses campos para renderizar pills "Gap % recalc." / "Gap R$ recalc." ao lado dos pills originais |
| `GET /api/conciliacao/transactions` | Anexa correção-transactions à resposta, cada linha com `isPending: true` e `correctionId` preservado |
| `GET /api/conciliacao/provisions` | Anexa correção-provisions à resposta, cada linha com `isPending: true` e `correctionId` preservado |
| `GET /api/conciliacao/global-analysis` | Injeta correções em `all_txns`, `all_provs` e `all_prov_lifecycle` para cada `walletId` do escopo, de modo que flags resolvidas não apareçam mais na aba *Anomalias em ativos* do Painel |

### Helper interno

`_wallet_company(wallet_id)` resolve o `companyId` de um `walletId` (via `db.wallets`) — permite que os endpoints que só recebem `walletId` ainda localizem o arquivo de correções em disco.

### UI (`templates/conciliacao.html`)

- **Pill "Gap recalculado"** ao lado do badge de gap no cabeçalho do modal de diagnóstico. Só aparece quando `step1.correctionsCount > 0`. Verde (sem gap residual) ou âmbar (gap residual).
- **Banner "GAP resolvido com as alterações aceitadas pelo usuário"** no topo do corpo do modal de diagnóstico, exibido quando o gap original era diferente de zero, há ao menos uma correção pendente, e `recalculatedGapPct` fica dentro da tolerância `|x| ≤ 0.0001`. Mostra também a soma de impacto (`correctionsImpact`) e a contagem de correções aplicadas.
- **Pills "Gap % recalc." / "Gap R$ recalc."** na página de detalhe da carteira (step 2), ao lado dos pills "Gap %" / "Gap R$" originais. Só aparecem quando `correctionsCount > 0` para a carteira+data. Fórmula client-side idêntica à do backend: `recalc = gap - sign(gap) * correctionsImpact`.
- **Listas de Transações e Provisões** no wallet-detail destacam linhas pendentes com fundo âmbar, borda âmbar, e um selo "PENDENTE" ao lado do nome do ativo. A UI continua aceitando que o usuário leia esses valores como se já estivessem aplicados.

Os endpoints `generate-transactions` e `generate-provisions` continuam existindo como conversores de `items → rows` (ex.: consumidos internamente pela página Painel ao enviar um bulk), mas não produzem mais downloads.

---

## Fluxo Transações — exportar **ou** enviar via API

A aba **Transações** em `/correcoes` aceita dois destinos finais para cada linha:

1. **Exportar** (fluxo tradicional) — JSON/XLSX via botão *Exportar*. A operação financeira do upstream consome o arquivo gerado.
2. **Enviar selecionadas via API** (push em lote) — checkbox por linha pendente + checkbox "selecionar todas" no cabeçalho da aba **Transações** + botão *Enviar selecionadas via API* com contador acima da tabela. Faz N chamadas **sequenciais** a `POST /api/correcoes/transactions/submit` (uma por linha selecionada). Internamente delega a `beehus_api.create_transaction(...)` (mesma função usada pelo formulário em *Funções > Criar Transação*, página `/beehus`). Encaminha `entityId`, `walletId`, `companyId`, `currencyId`, `balance`, `operationDate`, `liquidationDate`, `beehusTransactionType`, `description`, `comment`, `hide`, `inputType` e `securityId`. `currencyId` é **sempre** re-resolvido da `db.wallets.<walletId>.currencyId` no servidor (o valor da linha é descartado — evita enviar moeda errada quando o dado local está obsoleto ou a carteira teve a moeda trocada após a captura); `entityId` ausente na linha é resolvido da mesma forma. Em `2xx`, a linha é marcada `inputed=true`/`inputedAt=<utc>`/`beehusId=<_id upstream>` e a UI passa a exibir o badge **✓ Inputada**. Vantagens da serialização: a primeira falha de auth (401) interrompe a execução e refresca o badge **Token** — não faz sentido continuar enviando se o token está expirado; e a API upstream não recebe rajada paralela. Linhas já inputadas não exibem checkbox (não podem ser reenviadas). Ao final, mostra contador `ok/erro`; até três mensagens de erro são exibidas em `alert()` com os respectivos `upstream_body` para o usuário corrigir e tentar novamente. Não há botão *Enviar via API* por linha — o push individual é apenas um caso particular do lote (selecionar uma linha e clicar no botão).

### Recálculo local de gap (transactions)

Transações com `inputed=true` representam correções **já aplicadas no upstream**. `load_corrections_for_wallet` filtra `inputed=True` por padrão para evitar dupla contagem, simétrico ao tratamento aplicado a provisões — após o envio bem-sucedido a linha continua visível em `/correcoes` mas fica **invisível** para o pipeline de diagnóstico.

### Em erro

A resposta da API upstream (`upstream_status`, `upstream_body`) é exposta em `alert()` para o usuário corrigir os campos da linha (clicando para editar) e reenviar. O badge **Token** no cabeçalho é refrescado em qualquer falha do push para sinalizar token expirado/revogado. A linha permanece com `inputed=false` enquanto o push falha.

---

## Fluxo Provisões — exportar **ou** enviar via API

A aba **Provisões** em `/correcoes` aceita dois destinos finais para cada linha:

1. **Exportar** (fluxo tradicional) — JSON/XLSX via botão *Exportar*. A operação financeira do upstream consome o arquivo gerado.
2. **Enviar via API** (push direto) — botão por linha que chama `POST /api/correcoes/provisions/submit`. Internamente delega a `beehus_api.create_provision(...)` (mesma função usada pelo formulário em *Funções > Criar Provisão*, página `/beehus`). Em `2xx`, a linha é marcada `inputed=true`/`inputedAt=<utc>`/`beehusId=<_id upstream>`. A UI passa a exibir o badge **✓ Inputada** e o botão é substituído por um rótulo neutro.

### Recálculo local de gap (provisions)

Provisões com `inputed=true` representam correções **já aplicadas no upstream** — recalcular localmente faria contagem dupla. Os helpers `load_corrections_for_wallet`, `load_all_pending_provisions`, `load_active_pending_provisions` e `load_all_pending_provisions_by_wallet` já filtram `inputed=True` por padrão, de modo que após o envio bem-sucedido a linha continua visível em `/correcoes` mas é **invisível** para o pipeline de diagnóstico — exatamente como o documento equivalente em MongoDB já passa a ser considerado pela query padrão de `db.provisions`.

### Em erro

A resposta da API upstream (`upstream_status`, `upstream_body`) é exposta em `alert()` para o usuário corrigir os campos da linha (clicando para editar) e reenviar. A linha permanece com `inputed=false` enquanto o push falha.

---

## Fluxo `MISSING_EXECUTION_PRICE` — aceitar e enviar via API

`MISSING_EXECUTION_PRICE` (Step 3.3 da Conciliação) **não** vira transação nem provisão. O aceite cria uma linha no bucket `executionPrices`, e a página `/correcoes` oferece um botão por linha para fazer o push direto para a API upstream.

### Aceitar no modal de Diagnóstico

1. Botão **Aceitar** no card do flag dispara `_sendItemsToCorrecoes([item], …)`.
2. O front-end chama, em paralelo, `generate-transactions`, `generate-provisions` e `generate-execution-prices` com o mesmo `items[]`. Cada gerador filtra os flags que conhece — para `MISSING_EXECUTION_PRICE`, só `generate-execution-prices` produz uma linha (`_FLAG_TXN_TYPE["MISSING_EXECUTION_PRICE"] = None` faz `generate-transactions` ignorar o item).
3. As três respostas são mescladas e enviadas a `POST /api/correcoes/bulk` no bucket `executionPrices`. Idempotente por `sourceAnomalyKey` como os demais buckets.
4. O backend converte os campos diagnósticos (`expectedExecPrice`, `pu`, `executionPrice`, `actualBalance`, `expectedValue`, `amountDiff`) em uma linha do bucket com `inputed=false`.

### Enviar para o upstream

1. Aba **Preços de Execução** em `/correcoes` lista as linhas pendentes.
2. Botão **Enviar via API** por linha chama `POST /api/correcoes/execution-prices/submit`, que internamente delega a `beehus_api.create_execution_price(...)` (mesma função usada pelo formulário em *Funções > Adicionar Preço de Execução*, página `/beehus`).
3. Em `2xx`, a linha é marcada `inputed=true`/`inputedAt=<utc>`/`beehusId=<_id upstream>`. A UI passa a exibir o badge **✓ Inputado** e o botão é substituído por um rótulo neutro.
4. Em erro, a resposta da API upstream (`upstream_status`, `upstream_body`) é exposta em `alert()` para o usuário corrigir e reenviar.

### Recálculo local de gap

Linhas com `inputed=true` representam correções **já aplicadas no upstream** — recalcular localmente faria contagem dupla. Pipelines que aplicarem correções de preço devem filtrar `inputed=False` via `load_pending_execution_prices(company_id, wallet_id)`.

`_recalc_gap_with_corrections` em [pages/conciliacao.py](../pages/conciliacao.py) aplica linhas pendentes (`inputed=False`) cuja `positionDate` bate com a data sendo diagnosticada. O impacto vem da Fórmula 5 do Recálculo (`intradayContribution`):

```
delta_intraday_cash = amountDifference × (priceUsed − newExecutionPrice)
priceUsed           = priorExecutionPrice  (or PU when prior is zero)
```

E fecha o gap pela mesma heurística usada para transações:
```
recalc_gap_cash = gap_cash − sign(gap_cash) × |delta_intraday_cash|
```

No caso canônico em que `|delta_intraday_cash|` casa exatamente com `|gapCash|` (ver `step3.3` — flag dispara só quando o preço implícito diverge do usado em > 0,5%), o gap recalculado vai a 0 imediatamente após o Aceitar. Detalhes da fórmula em [CONCILIACAO_RECALCULO.md](CONCILIACAO_RECALCULO.md#recálculo-após-aceitar-_recalc_gap_with_corrections).

`step1.correctionsCount` e `step1.correctionsImpact` no payload de `/api/conciliacao/diagnose` somam linhas e impactos de **todos** os buckets aplicáveis (txns + provs + dels + execPrices não-inputados), de modo que o banner "GAP resolvido com as alterações aceitadas pelo usuário" se acende para qualquer combinação que zere o gap.
