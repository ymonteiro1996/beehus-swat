# HANDOFF — Conciliação (mov.): features restantes (AUTO-MODE)

> Cole este arquivo (ou "Leia HANDOFF_conciliacao_mov_features.md e execute em auto-mode")
> como primeiro prompt numa NOVA janela do Claude Code aberta NESTE projeto. Você está
> **continuando** um trabalho já em andamento. Trabalhe em **auto-mode**: implemente os
> itens abaixo na ordem, validando entre cada um, **sem parar para aprovação** — exceto
> onde explicitamente marcado "NÃO auto-implementar".

## Projeto / arquivos
- App: dashboard Flask **Mongo-free** ("SWAT — Controle de cargas"); lê a API Beehus via
  `beehus_catalog` / `beehus_api`. Locale **pt-BR** (vírgula decimal).
- Motor: [pages/conciliacao_mov.py](pages/conciliacao_mov.py) — função `_build_movement_for_wallet`
  (projeção origem→alvo) + `_build_diff` (diagnóstico). Template:
  [templates/conciliacao_mov.html](templates/conciliacao_mov.html). Doc:
  [docs/CONCILIACAO_MOV.md](docs/CONCILIACAO_MOV.md).
- Plano original (análise P0–P4): `C:\Users\gyamaguti\.claude\plans\stateful-tickling-panda.md`.
- Memória (já carregada na sessão): `project_conciliacao_mov`, `project_conciliacao_mov_diagnostic`
  — leia-as; descrevem todo o estado atual e as convenções.

## JÁ FEITO (não refazer) — 150 testes offline verdes
P0-1 (confiança/because por divergência), P0-2 (`cash_status` tri-estado match/mismatch/**unknown**
→ no unknown NÃO gera recon), P0-3 (IRRF coberto por **SOMA** 1% + **IRRF≥0 nunca proposto** +
flag `multiEvent`), P0-4 (`priceSource:"pu-alvo"` no recon), P0-5 (fix de execPrice só se derivado
difere do PU > `_EXECPRICE_REL_TOL`), P1-6/7 (saldo usa `_diff_threshold_decimal()` + tolerâncias
nomeadas `_CASH_*`/`_IRRF_*`/`_EXECPRICE_*`/`_QTY_DIFF_TOL`/`_BALANCE_ABS_TOL`), P2-9 (nome do
`onlyReal` via `name_hints`), P2-10 (caixa de vencido = **net residual** `max(0, former_bal − Σ
resgate já lançado)`, `_MATURITY_REDEMPT_TYPES`), P3-11 (confiança/because na UI), P4-14 (toggle
100% cache via `_cget`/`use_cache`).

## FAZER (nesta ordem)

### 1) P4-15 — extrair `_diagnose` puro (refactor; sem mudança de comportamento)
Hoje `_build_movement_for_wallet` (~600 linhas) mistura **fetch (API) + projeção + diagnóstico**.
Extrair uma função **pura** `_diagnose(inputs, omit_cash_ids)` (sem I/O) que recebe os insumos já
buscados (rows projetadas, tgt_rows, txns, caixa, navs, provs, sec_meta…) e devolve o bloco de
diagnóstico (diff + cash_status + recon + irrf + execfixes + stockEtfLiquidation). Objetivo:
testabilidade e habilitar recompute sem refetch. **É refactor — o comportamento deve ficar idêntico**;
o critério de sucesso é **os 150 testes continuarem verdes byte-a-byte** nos valores. Faça em passos
pequenos e rode a suíte a cada passo. NÃO mude fórmulas.

### 2) P3-12 — cascata (waterfall) do GAP
Decompor `simGapCash` nos achados do diagnóstico e expor o **resíduo inexplicado**. Backend: somar as
contribuições atribuíveis (provisões a gerar, IRRF ausente, recon, eventos) e devolver
`gapWaterfall: [{label, value}], gapUnexplained`. Frontend: um bloco no detalhe mostrando
"GAP R$X = Σ explicado + R$W inexplicado". É **read-only** (não dispara escrita). Cuidado para a
identidade bater com `simGapCash` já existente (ver memória `project_conciliacao_mov_gap_identity`).

### 3) P2-8 — `onlyCalc`/`onlyReal`/divergência-só-de-PU (⚠ **FLAG-ONLY**, NÃO auto-escrever)
Hoje são só exibidos. Classifique-os (suggestedAction + confidence/because) e mostre na UI. **NÃO**
gere `reconProvisions`/`reconTransactions` automáticos para `onlyReal`/`onlyCalc` nem ligue ao
`/reconcile-txn`//provisions` — esses casos têm implicação de **escrita destrutiva** e precisam de
validação com **dado real** do que cada caso significa. Entregue como diagnóstico/flag + um aviso
"precisa revisão manual"; deixe a geração de escrita para depois da validação (peça ao usuário).

## VALIDAÇÃO (rode SEMPRE; comandos exatos)
A suíte offline e o checker de JS vivem no scratchpad da sessão ANTERIOR (caminho absoluto, persiste
em disco). Use-os por caminho absoluto (Bash tool):

```bash
SP="C:/Users/GYAMAG~1/AppData/Local/Temp/claude/c--Users-gyamaguti-OneDrive---Beehus-Tecnologia-Ltda-Beehus-Tecnologia-Ltda---Documentos-SWAT-Controle-de-cargas/d6e21c17-ab06-4a85-ba23-d486e47dc259/scratchpad"
PROJ="c:/Users/gyamaguti/OneDrive - Beehus Tecnologia Ltda/Beehus Tecnologia Ltda - Documentos/SWAT/Controle de cargas"
cd "$PROJ"
python -m py_compile pages/conciliacao_mov.py            # backend compila
python "$SP/jscheck.py" templates/conciliacao_mov.html   # JS balanceado (ERRORS: NONE)
python -c "import jinja2; jinja2.Environment(loader=jinja2.FileSystemLoader('templates')).get_template('conciliacao_mov.html'); print('JINJA_OK')"
PYTHONIOENCODING=utf-8 python "$SP/test_conciliacao_mov.py" "$PROJ"   # deve dar "150 passed, 0 failed" (ou mais, se você adicionar cenários)
```
- Se esses arquivos sumirem do temp, **copie-os do caminho acima** para o seu próprio scratchpad e
  rode de lá. NÃO comite arquivos de teste no repo (convenção do projeto: testes no scratchpad).
- Toda mudança que adiciona comportamento → **adicione um cenário offline** (mundo stubado, sem rede).
  O harness stuba TODOS os seams (`install_world`): inclui `processed_doc`, `_diff_threshold_decimal`
  (→0), `execution_prices_by_sid`, etc. SEM stub → chama a API real e TRAVA.

## GUARDRAILS (regras do projeto — obrigatórias)
- **Write-safety:** o diagnóstico dirige escritas destrutivas (rotas `/provisions`, `/reconcile-txn`,
  `/irrf`, `/execution-prices`, `/gains-expenses`). Qualquer mudança que afete o que é proposto exige
  cuidado redobrado. P2-8 = **flag-only** (acima).
- **API 429:** valide com **1 empresa / 1 data**; NUNCA faça fan-out por todas as empresas/carteiras
  (rate limit). A suíte offline é a fonte de verdade — não dependa de chamadas ao vivo.
- **Verificação adversarial:** depois de cada item que toca caminho de escrita, faça uma verificação
  cética (no mínimo cenários offline de borda; se tiver orquestração multi-agente disponível, rode um
  workflow de céticos como nesta linhagem). Os testes "felizes" não pegam regressão — a 1ª tentativa do
  P2-10 passava 134 testes e tinha um **double-count** que invertia escrita; só a verificação pegou.
- **Docs:** toda mudança de spec vai para [docs/CONCILIACAO_MOV.md](docs/CONCILIACAO_MOV.md) junto com o
  código. Atualize a memória `project_conciliacao_mov_diagnostic` ao concluir.
- **Dev:** Flask com `use_reloader=False`; o projeto está em caminho com **espaços** (OneDrive) — já
  tratado, mas cuidado com paths. Avise o usuário p/ reiniciar o Flask ao testar na tela.
- **Não invente dados:** securities de teste são exemplos, não dados reais.

## Convenções-chave do diagnóstico (para não quebrar)
- `cash_status` ∈ {match, mismatch, unknown}; `cash_match` é alias compat. No `unknown` recon NÃO é
  gerado, mas IRRF/preços/liquidação seguem (são ancorados em unprocessed-alvo/origem, não no caixa).
- Cada divergência leva `confidence` (high/medium/low) + `because`; recon copia verbatim.
- Cache: `_cget(use_cache, key, loader)`; só o toggle passa `useCache=true`. Projeção inicial e
  pós-criação = `use_cache=False` (relê fresco + re-aquece). NÃO quebre essa regra.
- Tolerâncias só em constantes nomeadas no topo do módulo.
- IRRF: nunca propor `irrf ≥ 0`; `multiEvent` (>1 data de resgate) marca "⚠ revisar".

## AUTO-MODE
Implemente P4-15 → P3-12 → P2-8(flag-only), validando a suíte verde a cada passo e atualizando docs +
memória. Reporte progresso ao final de cada item. Pare e pergunte apenas: (a) antes de qualquer escrita
destrutiva nova em P2-8, ou (b) se um teste não puder ficar verde sem mudar comportamento.
