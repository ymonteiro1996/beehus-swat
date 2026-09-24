# Painel de Controle — Template Carteiras (e Somente SLA)

Dois painéis derivados do Painel de Controle (`/controlpanel`), com **os mesmos
fluxos**, mas com o universo de carteiras recortado ao cadastro
`TemplateCarteiras.xlsx` do ControleCargas.

| Menu lateral | Rota | Modo (`X-Swat-Scope`) |
|---|---|---|
| Painel · Template | `/controlpanel/template` | `template` |
| Painel · Template (SLA) | `/controlpanel/template-sla` | `template_sla` |

## Regras

- **Universo**: carteiras do TemplateCarteiras.xlsx (coluna B, WalletID) que
  existem no Beehus e não estão trashed. A empresa vem do Beehus (o Template
  não tem companyId).
- **Empresa**: seletor na barra do painel — "Todas as empresas" ou uma só.
  O recorte vale para tudo (grade, drill-downs e ferramentas).
- **Agrupamentos**: os listados em "Agrupamentos Indexados" (coluna G) das
  carteiras do escopo, só os não-trashed (mesma fonte do ControleCargas).
- **Somente SLA**: a data do card é a **data de execução**. Cada carteira só
  entra na sua data de SLA = execução − Defasagem (coluna I), em dias úteis
  ANBIMA (`bizdays`, mesmo calendário do ControleCargas). **"M" conta como D-1**
  (decisão do usuário em 24/09/2026; o ControleCargas usa 0). A grade tem uma
  linha por (empresa, data de SLA), ex.: `Oikos · D-2 · 22/09`.

## Como o escopo chega nas rotas

```
controlpanel.html  ──define──▶ window.__SWAT_SCOPE__ = {mode, runDate, companyId}
      │  (tools em iframe não definem nada: herdam do painel-pai)
base.html (wrapper de fetch) ──toda chamada /api/──▶ X-Swat-Scope / X-Swat-Run-Date / X-Swat-Scope-Company
      ▼
wallet_scope.install(app) (before_request) ──▶ flask.g
      ▼
rotas: wallet_scope.carteiras(companyId, data[, data_final]) / agrupamentos(...) / empresas() / pares(data)
       → None sem escopo (painel normal intacto) · set com escopo
```

- Listagens filtram pelo set; ações com lista vazia ("todas da empresa" no
  contrato upstream) viram a lista do escopo (`_scope_restrict` em
  `pages/beehus_console.py`); pedido 100% fora do escopo → 400.
- Publicar "todos" com escopo também tira agrupamentos com carteira
  "Deve Publicar = Não".
- `wallet_scope` depende do contexto da requisição: resolva tudo **antes** de
  um fan-out em threads (as threads não têm `flask.g`).

## Arquivos

- `template_carteiras.py` — leitura do Excel (cache por mtime). Caminho:
  biblioteca do SharePoint sincronizada
  (`~/Beehus Tecnologia Ltda/Beehus Tecnologia Ltda - Documentos/SWAT/ControleCargas/prototype/data`),
  override `TEMPLATE_CARTEIRAS_PATH`; o clone Git irmão é só último recurso.
- `wallet_scope.py` — escopo, datas de SLA, recortes.
- `pages/controlpanel.py` — `_scoped_rows`, `_scoped_unidentified_txn_counts`,
  `_scoped_detail_all`, `_scoped_process_all_wallets` + recortes nos drill-downs.
- `pages/beehus_console.py`, `pages/carteira.py` — recortes nas ferramentas.
- `data/template_wallet_companies.json` — cache local (fora do git) do último
  walletId→companyId visto. O índice global de carteiras sai **parcial** quando a
  API devolve 429 (rajada do painel normal) e fica 5 min em cache; sem essa
  memória metade do Template sumia do painel.

## Fora do escopo (não recortado)

- Stripping, Posição Projetada, Day-trade, Conciliação: páginas próprias do
  menu lateral, não abertas de dentro do painel.
- "Processar Transações" (heurística): a API roda por **(empresa, entidade)
  inteira** — não aceita carteiras. O recorte fica na lista de entidades (as
  que têm carteira do Template) e na TELA: depois de processar (ou pelo botão
  "Ver transações do Template"), o modal lista só as transações da data das
  carteiras do Template (no Somente SLA, das que têm SLA na data), via
  `/api/beehus/transactions/search` com escopo, com atalho "Abrir em Transações".
- Lista de entidades (`_visible_entities`): é por par (entidade, empresa) —
  38 das 100 entidades são compartilhadas entre empresas (Itaú, BTG, XP...);
  antes só a 1ª empresa de cada entidade aparecia (bug corrigido em 24/09/2026,
  vale também para o painel normal).
