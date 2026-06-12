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

---

## Colunas

| Coluna                     | Origem                                                        |
|----------------------------|---------------------------------------------------------------|
| Security                   | `processedPosition.securities[].securityId` (nome via `securities.beehusName`) |
| **unprocessedSecurityId**  | `securityMappings.mappings[].from`, desambiguado pelo snapshot mais recente `unprocessedSecurityPositions` da carteira ≤ data final |
| Qtd / PU / Saldo           | `processedPosition.securities[].quantity / pu / balance`      |
| Contribuição (opcional)    | `processedPosition.securities[].totalContribution`            |

A coluna `unprocessedSecurityId` resolve-se uma vez por carteira no
backend (`_unprocessed_id_maps`) — um `find_one` em
`unprocessedSecurityPositions` mais recente da carteira (sort desc por
`positionDate`), cruzado com `securityMappings.mappings` para inverter
`from → to` em `to → from`. A resolução por security segue três regras:

1. Se o `from` do mapping está no snapshot mais recente, usa-o — o
   snapshot desambigua um `securityId` que carrega mais de um `from`
   histórico (renomeações upstream, revisão de taxa, variação de
   formatação com/sem hífen — ~6% dos mappings na prática).
2. Senão, se o `securityId` tem **exatamente um** `from` nos mappings da
   empresa, cai nesse valor. Isso mantém a security **editável** quando
   ela saiu do snapshot mais recente — caso típico: **venceu na data
   final**. Nesse dia ela ainda aparece no `processedPosition`, mas o
   upstream já parou de emiti-la em `unprocessedSecurityPositions` (o
   último snapshot com ela é o dia útil anterior). Sem esse fallback a
   célula renderizava "—" e o modo de edição rejeitava a linha como "sem
   unprocessedId".
3. Um `securityId` com vários `from` e nenhum no snapshot fica **não
   resolvido** ("—") — não se adivinha qual `Ativo` enviar upstream.

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
