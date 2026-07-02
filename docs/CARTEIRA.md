# Carteira — Visualização e edição de posições

> Documenta a página `/carteira`, incluindo o modo de edição que gera um
> novo `unprocessedSecurityPositions` para uma (carteira, data) via upload
> upstream multipart.

---

## Visão geral

A página `/carteira` mostra, para uma empresa, as posições por carteira
(`processedPosition.securities[]`), agrupando as colunas por data. Cada
bloco de carteira inclui linhas de rodapé com Contribuição total,
Provisões e Caixa. O rótulo da linha **Caixa** exibe o
`cashAccounts.unprocessedId` da carteira (ex.: `Caixa`, `Cash`,
`Caixa USD`) — fallback para a string literal `Caixa` apenas quando a
carteira não tem `cashAccounts` em produção.

Além da visualização, o operador pode entrar em modo de edição para uma
data específica e enviar uma nova carga `unprocessedSecurityPositions`
para o sistema — usa-se o mesmo endpoint multipart que o
[Repetir Posições](REPETIR_POSICOES.md) e [Exceções](EXCECOES.md).

### Fonte de dados (uma chamada por data)

O bloco principal vem de **uma** resposta de `processed-position` por data
(`beehus_catalog.carteira_position_bundle`). O envelope dessa resposta já traz,
por carteira, três blocos — `position` (securities), `cashAccounts` e
`provisions` —, então securities, a linha **Caixa** (saldo + `unprocessedId`) e
a linha **Provisões** saem todos da mesma chamada, sem reads adicionais. Em
particular, o bloco `provisions` do envelope contém **exatamente as provisões
ativas naquela data** (`initialDate <= data < liquidationDate`), inclusive
provisões longas iniciadas antes do período — por isso não há chamada separada a
`/beehus/provisions` (que filtra por `initialDate`-na-janela e exigiria varrer
desde 2000). A coluna `unprocessedSecurityId` usa uma segunda chamada,
`unprocessedSecurityPositions`, em lote para todas as carteiras, pegando o
snapshot mais recente **com `positionDate` ≤ data final** (on-or-before, não
restrita à janela — ver Colunas).

---

## Colunas

| Coluna                     | Origem                                                        |
|----------------------------|---------------------------------------------------------------|
| Security                   | `processedPosition.securities[].securityId` (nome via `securities.beehusName`) |
| **unprocessedSecurityId**  | `unprocessedSecurityPositions.securities[].preProcessingData.securityId` → `.unprocessedId` (par autoritativo lido direto do snapshot bruto da carteira) |
| Qtd / PU / Saldo           | `processedPosition.securities[].quantity / pu / balance`      |
| Contribuição (opcional)    | `processedPosition.securities[].totalContribution`            |

A coluna `unprocessedSecurityId` resolve-se no backend
(`_unprocessed_id_maps`) com **uma** chamada em lote
(`unprocessed_sid_uid_map`) para todas as carteiras. Cada item de
`securities[]` do snapshot bruto carrega o par AUTORITATIVO
`preProcessingData.securityId → unprocessedId` daquela carteira; varre-se os
snapshots **do mais novo p/ o mais antigo** e, por `securityId`, fica-se com o
`unprocessedId` do snapshot mais recente que contém o ativo (o rótulo `Ativo`
vigente). Olha-se **todos** os snapshots ≤ data final, não só o último — cada
upload traz só parte da posição, então a união maximiza a cobertura.

> **Por que `preProcessingData`, e não o cruzamento com `securityMappings`.**
> O mapa company-level `securityMappings` (`from → to`) **colide**: um
> `securityId` costuma ter vários `from` históricos (renomeações upstream,
> revisão de taxa, variação de formatação com/sem hífen — **~38%** do catálogo
> em produção, não ~6%). Cruzar isso com o snapshot tentava desambiguar, mas
> qualquer ativo cujo sid tinha múltiplos candidatos e não estava fixado pelo
> snapshot caía em "—". Ler `preProcessingData.securityId` direto do snapshot
> pega o par EXATO que a carteira de fato carregou, sem chutar. Validado
> (empresa 23313334000110, 1 carteira): **superconjunto estrito** do
> cruzamento antigo — 0 regressões, 0 divergências no uid escolhido, +7 ativos
> resolvidos.

Um `securityId` presente na posição processada mas em **nenhum** snapshot bruto
da carteira (ex.: componentes de look-through / linhas criadas por evento) fica
**não resolvido** ("—") — não há rótulo de upload bruto a anexar, por
construção.

### Erro no arquivo bruto (unprocessed vazio/ausente)

As linhas de securities vêm do snapshot **bruto** (`unprocessed-security-positions`)
da **data exata** (cada linha carrega seu `unprocessedId` editável). Quando, numa
data, a carteira **tem posição processada** (logo, deveria ter posição) mas o
snapshot bruto daquela data está **vazio (0 ativos) ou ausente**, o backend marca
`unprocessedErrorByDate[data] = true` e **não faz fallback**: nenhum ativo é exibido
para a data e a UI mostra um **aviso vermelho** ("Erro no arquivo de posição bruta")
+ marca o cabeçalho da data com ⚠. Decisão de produto: **informar o erro do arquivo
e não fazer nada** (não recompor da processada). A data com erro **sobrevive ao corte
de colunas** (senão o aviso sumiria) e entra no gate `any_data` (a carteira aparece
mesmo que só tenha o erro). Correção é operacional: **reenviar o upload bruto** correto
daquela data. Caso real: carteira *MML PF BTG BRL* em **2026-05-29** (bruto com 0
ativos, processada com 12) — resolvido quando o snapshot bruto foi recriado.

---

## Modo de edição

Pré-requisito: a consulta foi feita no modo **data única** (faixa
desabilita o botão "Editar"). Editar uma faixa não faz sentido porque o
upload aceita um único `positionDate`.

### Fluxo

1. Operador clica em **Editar** no header da carteira.
2. O bloco vira modo de edição:
   - Qtd e PU viram inputs numéricos (formato pt-BR aceita vírgula
     ou ponto como separador decimal).
   - Saldo é calculado dinamicamente como `Qtd × PU` após edição.
   - Cada linha ganha um botão "×" para remover.
   - Rodapé Caixa: a célula **unprocessedSecurityId** vira input
     editável (o valor inicial vem de `cashAccounts.unprocessedId`), e
     o valor de caixa em si fica em input separado (deixar vazio =
     não enviar). Limpar o input do `unprocessedId` faz o backend
     reusar o `cashAccounts.unprocessedId` da carteira ao enviar; se
     também não houver `cashAccounts`, cai no literal `Caixa`.
   - Botões "Adicionar security", "Cancelar" e "Revisar e confirmar"
     aparecem na barra de ações.
3. **Editar security existente** ou **adicionar nova**: clique no nome
   do ativo (ou no botão "Adicionar security") abre o modal de busca.
4. **Modal de busca**: digite nome/mainId/ticker/taxId/ISIN/selicCode.
   Resultados vêm de `/api/carteira/search-securities` (proxy do cache
   `security_matcher` global).
5. **Auto-mapeamento**: ao escolher uma security, o frontend chama
   `/api/carteira/lookup-mapping?companyId=…&securityId=…` para
   resolver o `unprocessedSecurityId`:
   - Busca em `securityMappings` da empresa por `mappings[].to ==
     securityId`; retorna `mappings[].from`.
   - Se não há mapping, faz fallback para `securities.beehusName`.
   - Se ambos faltam, o frontend bloqueia a seleção.
6. **Revisar e confirmar**: abre modal de confirmação listando
   security, Ativo (unprocessedId), Qtd, PU, Saldo. Inclui também uma
   linha **Caixa** dedicada quando o operador informou um valor de
   caixa — mostrando `Ativo` (o `cashUnprocessedId` em edição) e o
   valor. Se o `cashUnprocessedId` foi alterado em relação ao snapshot
   carregado, o novo valor aparece destacado em verde com o valor
   anterior à direita (`antes: <origUid>`). Se o operador alterou o
   `cashUnprocessedId` **sem informar um valor de Caixa**, uma linha
   de aviso em amarelo é exibida deixando claro que a alteração do
   Ativo **não será enviada** (o backend só grava a linha de caixa no
   `.xlsx` quando `cash != null`). Validações:
   - Toda linha precisa ter `unprocessedId`.
   - Não pode haver `unprocessedId` duplicado na carteira.
7. **Confirmar e enviar**: dispara `POST /api/carteira/apply`. Backend
   monta um `.xlsx` com schema `Data, Carteira, Ativo, Quant, PU,
   SaldoBruto, Caixa, Moeda` (o mesmo schema usado por
   `pages.repetir_posicoes` e `pages.excecoes`) e envia via
   `beehus_api.upload_unprocessed_security_positions_file`. Em sucesso,
   o bloco recarrega via `/api/carteira/data` para refletir o novo
   snapshot.

### Linha de Caixa

Quando o operador preenche o input de Caixa em modo de edição, uma
linha adicional é incluída no `.xlsx`:

```
Data | Carteira | Ativo                     | Quant | PU | SaldoBruto | Caixa | Moeda
…    | <walletId> | <cashUnprocessedId>     | 0     | 0  | <valor>    | Sim   | <currencyId>
```

O valor da coluna `Ativo` é o `cashUnprocessedId` que o operador
deixou no input da linha Caixa em modo de edição. Quando vazio, o
backend resolve novamente a partir de `cashAccounts.unprocessedId`
da carteira; sem `cashAccounts`, o fallback é o literal `Caixa`
(mantendo o comportamento histórico).

Linhas comuns saem com `Caixa = "Não"`. Se o input do valor ficar
vazio (null), nenhuma linha de caixa é gravada — o operador está
dizendo "não sobrescrever".

---

## Endpoints

| Verbo | Rota                              | Função |
|-------|-----------------------------------|--------|
| GET   | `/carteira`                       | Renderiza a página |
| GET   | `/api/carteira/filters/companies` | Lista empresas visíveis ao operador |
| GET   | `/api/carteira/filters/groupings` | Groupings da empresa |
| GET   | `/api/carteira/filters/wallets`   | Wallets da empresa |
| POST  | `/api/carteira/data`              | Matriz wallet × data com `unprocessedId` por security |
| GET   | `/api/carteira/lookup-mapping`    | `{companyId, securityId} → {unprocessedId, beehusName, source}` |
| GET   | `/api/carteira/search-securities` | Busca livre no cache global de securities (proxy do controlpanel) |
| POST  | `/api/carteira/apply`             | Monta `.xlsx` e envia upstream |

### `POST /api/carteira/apply`

Body:

```json
{
  "companyId":         "<companyId>",
  "walletId":          "<walletId>",
  "targetDate":        "2026-05-14",
  "currencyId":        "BRL",
  "securities": [
    { "unprocessedId": "<from-securityMappings>", "quantity": 100, "pu": 12.5 }
  ],
  "cash":              250000.00,
  "cashUnprocessedId": "Caixa"
}
```

`cashUnprocessedId` é opcional: quando omitido ou vazio, o backend
resolve o valor consultando `cashAccounts.unprocessedId` da carteira
(filtra `trashed != true`); sem cashAccount, usa o literal `Caixa`.
Esse campo só é gravado no `.xlsx` quando `cash != null`.

Respostas:

- `200 {ok: true, filename, rows, cashSent, upstream}` — upload aceito.
- `400` — `unprocessedId` faltando, ativo duplicado, qtd/pu inválidos
  ou `cash` numericamente inválido.
- `401` — `BeehusAuthError` (token expirado).
- `502` — `BeehusAPIError` na chamada upstream.

### `GET /api/carteira/lookup-mapping`

Query: `companyId`, `securityId`. Resposta:

```json
{
  "unprocessedId": "PETR4",
  "beehusName":    "PETROBRAS PN",
  "source":        "mapping"   // ou "beehusName" ou ""
}
```

`source` indica como o `unprocessedId` foi resolvido. O frontend usa o
campo para decidir se mostra um aviso "ativo sem mapeamento — usando
nome".

---

## Validações e segurança

- Toda rota chama `company_visible(companyId)` antes de aceitar a
  requisição — respeita o `company_filter` do `settings.json`.
- `apply_edits` verifica que `wallets._id == walletId` tem
  `companyId == requestedCompanyId` (evita re-target cross-company).
- `unprocessedId` duplicado dentro da mesma carteira → rejeitado no
  frontend e no backend (`_build_carteira_xlsx` consolidaria silenciosamente).
- O backend não persiste nada localmente — apenas monta o `.xlsx` em
  memória e despacha via `beehus_api.upload_unprocessed_security_positions_file`.
