# Processamento com cadeia de explosão (multi-nível)

**Tela:** Painel de Controle > Processar (e todo caminho que dispara
`POST /api/beehus/positions/process`).

## Problema

No Beehus, ao processar uma carteira que tem um **ativo de explosão**, a(s)
carteira(s) que esse ativo aponta precisam ser enviadas **junto** na mesma
requisição de `process`. Sem isso o processamento **não conclui**.

A relação pode ser **encadeada**: `A` tem um ativo de explosão que aponta para
`B`; `B` pode ter o seu próprio ativo apontando para `C`; e assim por diante. Ao
processar `A` é preciso enviar `A + B + C + …` (a cadeia inteira, achatada).

## Dados de origem

| O quê | Onde | Campo |
|-------|------|-------|
| Ativos de explosão de uma carteira | carteira (`partner_wallets` / `company_wallets_index`) | `securitiesForExplosion: [securityId, ...]` |
| Carteira que sofre a explosão | ativo (`security_doc` do catálogo, ou GET pontual `get_security`) | `correspondingWallet: {_id, name}` |

Ambas as fontes já são **cacheadas 5 min** neste seam (`beehus_catalog`), então
resolver a cadeia é barato e não refaz round-trips numa mesma janela — o catálogo
de securities normalmente já traz `correspondingWallet`, e só cai no GET pontual
`/beehus/securities/{id}` (cacheado por ativo em `corresponding_wallet::{id}`)
quando o catálogo não trouxe o campo.

## Algoritmo (BFS) — `beehus_catalog.explosion_chain(company_id, wallet_id)`

```
secmap = { walletId: [securityId, ...] }   # wallet_explosion_map(company_id)
seen   = { walletIdRaiz }                   # inclui a raiz → mata ciclo/auto-ref
out, fila = [], [ (walletIdRaiz, nivel=1) ]

enquanto fila:
    (atual, nivel) = fila.pop(0)
    para cada securityId em secmap.get(atual, []):
        cw = correspondingWallet(securityId)
        se cw ausente OU cw.id em seen: continua
        seen.add(cw.id)
        out.append({securityId, walletId: cw.id, name, level: nivel, viaWalletId: atual})
        fila.append( (cw.id, nivel+1) )

retorna out   # achatada; vazia quando não há explosão
```

`expand_wallets_with_explosion(company_id, wallet_ids)` aplica isso a um conjunto
de raízes e devolve `[raízes...] + arrastadas`, **deduplicado** e preservando a
ordem. `wallet_ids` vazio = "todas as carteiras" (contrato do `/process`) →
passa direto, sem expansão.

## Casos de borda (validados em produção)

- **Auto-referência** — ativo aponta para a própria carteira de origem: o `seen`
  (que inclui a raiz) bloqueia. Não entra na lista.
- **Ciclo entre carteiras** (`A→B→A`): idem, o `seen` interrompe.
- **Carteira-folha** — `correspondingWallet` sem `securitiesForExplosion`: a
  cadeia para naquele ramo.
- **Sem explosão** — retorna `[]`: processa normal, sem arrastar ninguém.
- **Múltiplos ativos apontando para a mesma correspondente** — dedup por `seen`
  (uma entrada só).

## Onde a expansão acontece

Server-side, dentro de `positions_process` (`pages/beehus_console.py`), que é o
**ponto único** por onde todo `process` passa (data única, faixa de datas,
Exceções etapa 5, etc.). Assim a correção vale para todos os chamadores sem
depender do frontend. A resposta inclui `draggedWallets: [...]` quando houve
arrasto.

## UX (Painel de Controle > Processar)

O modal de confirmação (`ProcessDates`, flag `explosionPreview`) lista, **antes**
de disparar, as carteiras que serão co-processadas — usando
`GET /api/beehus/positions/explosion-chain` por carteira selecionada. Usa `level`
para indentar a hierarquia e `viaWalletId` para o "(via <carteira>)" nos níveis
> 1. Sem carteiras selecionadas (= "todas"), não há nada a arrastar e o bloco
fica oculto. A falha do preview **não** bloqueia: o servidor ainda expande no
`/process`.

## Testes

`scripts/test_explosion_chain.py` — offline (monkeypatch das duas fontes),
cobre single-level, multi-nível, auto-ref, ciclo, folha, dedup, fallback GET e a
união do `expand_wallets_with_explosion`.
