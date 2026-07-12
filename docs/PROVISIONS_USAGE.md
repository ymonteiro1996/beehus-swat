# Mapa de uso de Provisions

> Levantamento de **onde** as provisions são buscadas e **qual cálculo** cada
> consumidor faz. Base para revisar as regras de data/cálculo.
> Gerado em 2026-06-22 a partir de varredura do código.

## Fato confirmado em produção (decisivo)

O endpoint `GET /beehus/provisions` **filtra por `initialDate` DENTRO da janela
`[initialDate, finalDate]`**, NÃO por overlap/atividade. Provado:

- Provisão `6984d549…` (`initialDate=2025-12-01`, `liquidationDate=2028-08-28`),
  claramente **ativa** em `2026-03-02`. A query `[2026-03-02, 2026-03-02]`
  retornou **0 docs** — não a trouxe.
- Query `[2026-05-29, 2026-05-29]` → 25 docs, **todos** com `initialDate==2026-05-29`.

**Implicação:** para obter "provisões ativas em D" é obrigatório buscar de uma
data-piso bem atrás (hoje: `2000-01-01`) e filtrar a atividade no cliente.
Estreitar a janela de busca para a janela da tela **perde** provisões iniciadas
antes do período e ainda ativas.

⚠️ **Docstring corrigida (jun/2026):** [`beehus_api/provisions.py`](../beehus_api/provisions.py)
afirmava "ativas no intervalo" — estava **incorreta**, agora corrigida. Os
comentários em `beehus_catalog.py` (`provisions_active`/`provisions_overlapping`)
estão certos.

## Atalho confirmado: o envelope de processed-position já traz as provisões da data

Verificado em produção (jun/2026): o bloco `provisions` da resposta de
`GET /beehus/financial/positions/processed-position` (por carteira, por data)
contém **exatamente as provisões ATIVAS naquela data** (`initialDate <= data <
liquidationDate`), **inclusive provisões longas iniciadas antes** da data. Prova:
para a carteira `6920c624…` em `2026-05-29`, o envelope trouxe a provisão
`6984d549` (initial=2025-12-01, liq=2028-08-28) — a mesma que o endpoint
`/beehus/provisions[D,D]` não traz. `envelope.provisions` == `provisions_active(D)`
(mesmos IDs, mesma soma), em ~280 ms vs ~8.700 ms.

➡️ **Consequência:** onde já se busca a posição processada da (carteira, data),
as provisões ativas **não precisam de chamada extra nem do piso 2000** — basta
ler `envelope.provisions`. Já aplicado na **Carteira** (ver consumidor A).
Os demais consumidores (B–E) ainda usam o endpoint `/beehus/provisions`; migrar
cada um exige confirmar que a regra de data deles é "ativa na data" (a maioria é).

## Camada de busca (`beehus_catalog.py`)

| Função | Janela buscada | Regra de filtro (no cliente) | Aceita carteira? |
|---|---|---|---|
| `_fetch_provisions(cid, ini, fin, wids)` | `[ini, fin]` | só normaliza/dedup; **não** filtra por carteira no servidor (passa company-wide, filtra `want` no cliente) | client-side |
| `provisions_active(cid, D, wids)` | `[2000-01-01, D]` | `initial <= D < liq`, não trashed | client-side |
| `provisions_overlapping(cid, ini, fin, wids)` | `[2000-01-01, fin]` | `initial <= fin AND liq >= ini`, não trashed | client-side |
| `provisions_lifecycle_sids(cid, D, wids)` | `[2000-01-01, D]` | `initial == D` **OU** `liq == D`, com securityId | client-side |
| `provisions_search(cid, …)` | `[ini, fin]` (default 2000..2999) | securityId + provisionType, sort liq desc, limit | client-side |

**Todas dependem do piso `2000-01-01`** para correção (consequência do filtro
por `initialDate` do endpoint). `_fetch_provisions` **ignora** `wallet_ids` no
servidor e filtra no cliente — embora o endpoint aceite `wallet_id` (singular)
server-side (ver "Oportunidade" abaixo).

## Consumidores e cálculos

### A. Carteira — linha "Provisões"  ([carteira.py](../pages/carteira.py)) — ✅ MIGRADA p/ envelope
- **Busca:** nenhuma chamada dedicada. Lê `envelope.provisions` da resposta de
  `processed-position` já buscada para securities/caixa
  (`beehus_catalog.carteira_position_bundle`).
- **Cálculo:** por (carteira, data), soma `balance` das provisões não-trashed do
  envelope daquela data (= ativas na data) → `prov_map[(wallet, dt)]`.
- **Saída:** total de provisões por (carteira, data) na matriz.
- **Antes:** `provisions_overlapping(company, dates[0], dates[-1], wallet_ids)` (piso
  2000, ~14 s). Removido — resultado idêntico (mesma soma) e ~30× mais rápido.

### B. Repetir Posições  ([repetir_posicoes.py](../pages/repetir_posicoes.py))
- `_provisions_detail(company, wallet, target_date)` (l.~1440): `provisions_active`
  → lista linha-a-linha `{id, description, balance, initial, liq, kind, securityId}`.
  Regra: **ativa em target_date** (`initial <= target < liq`).
- `_provisions_sum` (l.1683): soma `balance` do `_provisions_detail` → total/chip da prévia.
- `_find_orphan_transactions` (l.1561): `provisions_active(None, target, [wallet])` →
  conjunto de `securityId` cobrindo o dia → **regra secundária de orfandade** (txn não
  é órfã se há provisão ativa do mesmo security no target).

### C. Exceções  ([excecoes.py](../pages/excecoes.py))
- `_slice_source_provisions(company, wallet, target_date)` (l.2070): `provisions_active`
  → lista linha-a-linha para a prévia do wallet-slice. Regra: ativa em target_date.
- **class_strip** (l.2803): `provisions_active(company, target, [src_wallet])` filtrado por
  `securityId ∈ matched` **e** `provisionType ∈ whitelist` → migra provisões p/ a carteira destino.

### D. Conciliação (unprocessed)  ([conciliacao_unprocessed.py](../pages/conciliacao_unprocessed.py))
- l.505: `provisions_active(None, date, [wallet])` → agrupa por securityId, soma `balance`
  (`balance` com fallback `amount`) → `prov_total_by_sid`, `explosao_prov_total`.
- l.828: `provisions_active(None, date, [wallet])` → `prov_map[sid] += p.get("amount")`.
  ⚠️ **Bug latente:** usa só `amount` (sem fallback p/ `balance`); o endpoint só tem
  `balance` → contribui **0** sempre. Ver "Pendências".
- l.833: `provisions_lifecycle_sids(None, date, [wallet])` → securityIds com evento no dia.
- l.1128: `provisions_active(None, date, [wallet])` → endpoint que lista provisões p/ UI.

### E. Beehus Console — busca de provisões  ([beehus_console.py:2730](../pages/beehus_console.py#L2730))
- `coverDate` → `provisions_active(company, coverDate, [wallet?])` (ponto único).
- `initialDate+finalDate` → `provisions_overlapping(company, ini, fin, [wallet?])` (overlap legado).

### F. Conciliação — guarda de posse  ([conciliacao.py:453](../pages/conciliacao.py#L453))
- `_find_wallet_provision(wallet, prov_id)`: `db.provisions.find_one({_id, walletId})`
  — **acesso direto ao Mongo** (não passa pela API), só p/ validar posse antes de delete/patch.

### G. Índice  ([db.py:182](../db.py#L182))
- `db.provisions.create_index(...)` em `ensure_indexes()` — falha por falta de permissão
  (sem efeito; reads não-indexados).

## Oportunidade de performance (preserva correção)

`_fetch_provisions` busca **company-wide** e filtra carteira no cliente. O endpoint
aceita `wallet_id` (**singular**) server-side. Medido (carteira `680a9ce5…`, piso 2000):

| Busca | docs | tempo |
|---|---|---|
| `[2000..FIN]` empresa toda | 4154 | 4.859 ms |
| `[2000..FIN]` + `wallet_id` server-side | 89 | **188 ms** |

**~26× sem mudar semântica** (piso 2000 mantido). A maioria dos consumidores (B,C,D
e parte do E) já consulta **uma** carteira → ganho direto. Carteira (A) é multi-carteira
→ exigiria fan-out paralelo por carteira (ou manter company-wide quando há muitas).

## Pendências para o dono dos cálculos decidir

1. **`amount` vs `balance`** em `conciliacao_unprocessed.py:831` — provavelmente deveria
   ser `balance` (como nos sites 516/1134). Hoje provisões entram como 0 nesse ponto.
2. **Migrar B–E para o envelope** (`envelope.provisions` em vez de `/beehus/provisions`):
   só onde já se busca a posição processada da (carteira, data) e a regra é "ativa na data".
   Para os que não buscam posição, alternativa = `wallet_id` server-side (preserva piso
   2000, ~26×).
3. ✅ **Docstring de `list_provisions` corrigida** (initialDate-na-janela, não overlap).
4. ✅ **Carteira migrada** para o envelope (consumidor A).
