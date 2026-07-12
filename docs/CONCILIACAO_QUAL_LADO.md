# Conciliação — num GAP, qual lado é o correto: `returnNavPerShare` ou `returnContribution`?

> Investigação sobre o modelo de conciliação NAV deste projeto. Resposta curta:
> **não dá para saber a priori** — descobre-se rodando o funil de diagnóstico
> até a causa-raiz, e mesmo aí parte é **premissa de negócio**, não medição.

## TL;DR

- `returnNavPerShare` (a **cota**) e `returnContribution` são **duas estimativas
  da MESMA grandeza** (o retorno do dia da carteira) por caminhos diferentes a
  partir dos **mesmos dados**. O **GAP** entre eles é um **teste de consistência**,
  não uma medição de verdade — num gap genérico **nenhum dos dois é "o certo"**.
- Este projeto **não calcula** nenhum dos dois: ambos vêm prontos do `navPackage`
  e são apenas lidos e subtraídos (`pages/conciliacao_unprocessed.py:703-708`).
- O sistema **assume por regra de negócio** que a cota é a verdade e que o
  `returnContribution` é o lado a corrigir (`docs/CONCILIACAO_DIAGNOSTICO.md:3,9,11`).
- A forma de saber qual lado está distorcido é **o veredito do Step 7 + a
  causa-raiz** do funil (`pages/diagnostic_engine.py:551-565`), **não o número do
  gap**.

---

## 1. Por que não dá para saber a priori

**(a) O projeto não mede — só lê e subtrai.** `returnNavPerShare` e
`returnContribution` (nível-carteira) vêm do `navPackage`; o gap é a subtração
direta:

```
return_nav_per_share = nav_pkg["returnNavPerShare"]      # conciliacao_unprocessed.py:703
return_contribution  = nav_pkg["returnContribution"]     # :704
gap_pct  = return_nav_per_share - return_contribution    # :706
gap_cash = gap_pct * former_nav                          # :708
```

Como não há um terceiro cálculo independente para arbitrar o conflito, **num gap
isolado não existe evidência interna para eleger o lado certo**.

**(b) São o mesmo número por dois caminhos.**

- `returnNavPerShare` → deriva do PL/NAV apurado por **saldos reais** (posições +
  caixa + provisões) → base da **cota**.
- `returnContribution` → deriva da **soma das contribuições por ativo**.

Por construção deveriam coincidir; quando discordam, a discordância **denuncia
que algum insumo está errado** — mas não diz de qual lado.

**(c) "Oficial" ≠ "correto".** O número publicado ao investidor é a **cota**
(`navPerShare`/`returnNavPerShare`), cuja divulgação é controlada pelo flag
`published` (`beehus_api/consolidation.py:45,49` — `publishedGroupings`,
`groupingsDetailed.published/publishedAt`). Mas "oficial" não significa
"matematicamente livre de erro": a cota é tratada como verdade **por princípio de
construção** (NAV de saldos reais), não por verificação externa. A prova é o
veredito `LIKELY_WRONG_FORMER_NAV`, em que a própria cota (via `formerNav`) pode
estar errada.

**(d) Cuidado com o `formerNav` (denominador comum).** Os dois retornos **dependem
do mesmo `formerNav`** — `returnContribution` diretamente no denominador, e
`returnNavPerShare` via `formerNavPerShare = formerNav / formerAmount`
(`docs/CONCILIACAO_RECALCULO.md:108,116`; a doc simplifica para `nav/formerNav−1`
em `docs/CONCILIACAO_DIAGNOSTICO.md:449`). Se o `formerNav` (snapshot do dia
anterior) estiver mal resolvido, **os DOIS retornos ficam distorcidos juntos** e o
GAP não localiza nada. O `formerNav` é resolvido como o `navPackage` não-trashed
imediatamente anterior (`pages/conciliacao_shared.py:83-87`;
`beehus_catalog.py:nav_former_for_entity`) — usar `processedPosition` produziria
retornos absurdos (a doc cita 138%/dia, `docs/CONCILIACAO_DIAGNOSTICO.md:17-30`).

> **Não confundir homônimos:** existe um `returnContrib` de **nível-ATIVO**
> computado localmente em `_reconcile_security`
> (`pages/conciliacao_unprocessed.py:183-184`), usado só para atribuir o gap aos
> ativos (`# computed (informational)`, `:741`). Ele **não é** o
> `returnContribution` de nível-carteira que entra no GAP headline (`:704`).

---

## 2. Como descobrir na prática — o veredito do Step 7

O funil (`pages/diagnostic_engine.py`, Steps 1–7) atribui o GAP a uma
**causa-raiz**; é a causa que diz qual lado está distorcido. A cadeia de veredito
é avaliada **em ordem fixa, primeira regra que bate vence**
(`diagnostic_engine.py:551-565`):

```
NO_GAP  →  SECURITY_ISSUES  →  TRANSACTION_ISSUES  →  CASH_ISSUES  →  LIKELY_WRONG_FORMER_NAV
```

As três do meio localizam a causa em **inputs da contribuição**; só a última
inverte e culpa o lado do NAV anterior.

| Causa-raiz (Step) | Lado distorcido | Referência confiável | Por quê |
|---|---|---|---|
| **sem gap → `NO_GAP`** (Step 1 ok) | — | — | Os dois concordam; a pergunta não se coloca. O funil só roda quando discordam. |
| **MISSING_TRANSACTION / MISSING_PROVISION** (3.1) | `returnContribution` | cota / Δqtd real | A variação de quantidade real é a âncora; falta a transação/provisão (`diagnostic_engine.py:258-288`). |
| **MISSING_EVENT / WRONG_EVENT_BALANCE / WRONG_PROVISION_AMOUNT** (3.2) | `returnContribution` | cota (PU) | `expectedEventCash = −diffRent×formerBalance` ancorado no PU; a contribuição não capturou o evento (`:292,301-323`). |
| **MISSING_EXECUTION_PRICE / WITHHOLDING_TAX / WRONG_TRANSACTION_VALUE** (3.3) | `returnContribution` | cota / qtd real | Preço implícito ou caixa real divergem do valor que entrou na contribuição (`:333-372`). |
| **MISCLASSIFIED_EVENT_TYPE** (3.4) | `returnContribution` | cota | Caso mais explícito: "o NAV enxerga o dinheiro, a contribuição não" (`:377-389`). |
| **WRONG_SECURITY / misclassified / unclassified** (Step 4) | `returnContribution` | cota | Transações reais não atribuídas a um ativo → ficaram fora da contribuição (`:397-455`). |
| **CASH mismatch** (Step 5) | ambos (NAV projetado + contribuição) | **o `currentCash` real** (não os retornos) | `projectedCash = formerCash + Σbalance` vs `currentCash`; a régua é o caixa gravado (`:461-498`). |
| **suspectTxns** (Step 5.1) | possivelmente `returnNavPerShare` (caixa inflado) | nenhum | Único ponto onde o suspeito é uma txn inflando o NAV. **Não-conclusivo, não muda o veredito** (`:467-481`). |
| **LIKELY_WRONG_FORMER_NAV** (Step 7) | **ambos os retornos publicados** (via `formerNav`) | nenhum — só os *numeradores do dia* ficam presumidos limpos | Por **eliminação**: Steps 3/4/5 limpos e gap persiste → culpa o `formerNav`. **Default sem medir o formerNav** → baixa força probatória (`:560-565`). |
| **anomalia 3σ** (Step 6) | — | — | Sinal estatístico sobre `returnNavPerShare`; não entra no veredito, só reforça `LIKELY_WRONG_FORMER_NAV` (`:500-535,570-572`). |

**Padrão:** na grande maioria das causas catalogadas o lado distorcido é
`returnContribution` e a cota é a referência — **mas isso é a premissa de entrada
se materializando, não uma prova independente em cada caso.** As exceções
(`LIKELY_WRONG_FORMER_NAV` e `suspectTxns`) são justamente onde o NAV/cota é o
suspeito, e ambas têm baixa força probatória.

---

## 3. Ressalvas que importam

1. **`INCONCLUSIVE` está na doc mas é inalcançável no código atual.** O `else`
   final de `diagnostic_engine.py:560` **sempre** cai em
   `LIKELY_WRONG_FORMER_NAV`. Não há branch `INCONCLUSIVE` no `run_funnel`
   (ele existe só na matriz documental, `docs/CONCILIACAO_DIAGNOSTICO.md:440`). Na
   prática o sistema **nunca diz "não sei"** — todo resíduo não-localizado vira
   "suspeita da cota", o que torna esse veredito **ainda menos probatório** (ele
   absorve também os casos genuinamente inconclusivos).

2. **Correções de IR / preço de execução não provam nada sobre o lado errado.**
   Elas **não recalculam nenhum dos dois retornos** — apenas **encolhem
   `|gap_cash|` pela magnitude `abs(impact)`**, de forma simétrica/agnóstica de
   sinal (`conciliacao_unprocessed.py:1264-1266`); só fecham o gap quando
   `|impact| ≈ |gap|`, e **podem mascarar um erro real** de qualquer lado.

3. **O recálculo nunca recomputa `returnContribution`** — ele entra como parâmetro
   fixo (`return_contrib`) e só o `returnNavPerShare` é reconstruído
   (`conciliacao_shared.py:169-170`; `conciliacao_unprocessed.py:1256-1262`). As
   correções de **provisão / Δqtd / ajuste de caixa** efetivamente reconstroem o
   NAV em direção à contribuição (tratando a contribuição como referência) —
   **essas** são informativas sobre a direção do erro.

---

## 4. Na tela (Conciliação Não Proc.)

A grade do Step 1 mostra o **veredito do Step 7 ao lado do "Novo GAP %"** depois de
"Diagnosticar selecionadas" — para ler de imediato **qual lado o diagnóstico está
culpando**:

| Chip | Veredito | Significado |
|---|---|---|
| `sem gap` (verde) | `NO_GAP` | Os dois retornos concordam. |
| `→ contrib (ativos)` (índigo) | `SECURITY_ISSUES` | Corrigir a contribuição; a cota é a referência. |
| `→ contrib (transações)` (índigo) | `TRANSACTION_ISSUES` | Idem — transações não atribuídas. |
| `→ caixa` (âmbar) | `CASH_ISSUES` | A âncora é o `currentCash` real, não os retornos. |
| `→ cota (NAV ant.?)` (vermelho) | `LIKELY_WRONG_FORMER_NAV` | Suspeita da **cota** (`formerNav`) — **hipótese, baixa confiança** (por eliminação). |

O `title` (tooltip) do chip traz o detalhe completo (`verdictDetail`) e o lado
culpado. **Leia o chip, não só o número** do Novo GAP: um Novo GAP ≈ 0 só
significa que as correções fecham o resíduo — não prova qual lado estava certo
(ver ressalva 2).

No **Step 2** (modal **Diagnosticar** de uma carteira), o mesmo veredito aparece
no banner do topo, agora com uma linha **"Lado a corrigir"** (mesmo mapeamento
`VERDICT_INFO`/`_verdictTag`) explicitando qual lado o diagnóstico culpa e o
porquê — ex.: `→ contrib (ativos)` "Corrigir a contribuição — a cota
(returnNavPerShare) é a referência", ou `→ cota (NAV ant.?)` "Suspeita da COTA via
formerNav — hipótese por eliminação, baixa confiança".

---

## 5. Bottom line

- **O GAP não diz quem está certo** — só prova que os dois caminhos discordam.
  "NAV certo / contribuição errada" é **premissa fixa de negócio**, não veredito
  do dado.
- Decida pelo **veredito + causa-raiz**: `SECURITY/TRANSACTION/CASH_ISSUES` → erro
  nos inputs da contribuição (cota é referência); `LIKELY_WRONG_FORMER_NAV` →
  suspeite da cota, **como hipótese, não prova**.
- Em `CASH_ISSUES`, a âncora não é nenhum retorno — é o **`currentCash` real**.
- **Desconfie de IR/preço que "fecham" o gap exatamente**; confie mais nas
  correções de **provisão/caixa** para inferir a direção do erro.

**Em uma frase:** num gap arbitrário você não sabe a priori qual lado é o certo —
descobre rodando o funil até a causa-raiz; até lá o sistema apenas *pressupõe* que
a cota é a verdade e enumera erros da contribuição, invertendo essa presunção (com
baixa confiança, por eliminação) só no resíduo `LIKELY_WRONG_FORMER_NAV`.

---

### Referências de código

- `pages/conciliacao_unprocessed.py:703-708` — leitura/subtração dos dois retornos (sem cálculo próprio).
- `pages/conciliacao_unprocessed.py:183-184,741` — homônimo `returnContrib` nível-ativo (informational).
- `pages/conciliacao_unprocessed.py:1256-1266` — recálculo: provisões reconstroem o NAV; IR/exec encolhem `|gap|` por magnitude.
- `pages/conciliacao_shared.py:83-87,162-178` — `_find_former_nav` e `_recalc_gap_with_corrections`.
- `pages/diagnostic_engine.py:258-389,397-498,500-535,551-565` — Steps 3/4/5/6 e a cadeia de veredito do Step 7.
- `beehus_api/consolidation.py:45,49` — flag `published` / `publishedGroupings`.
- `docs/CONCILIACAO_DIAGNOSTICO.md:3,9,11,17-30,440,449-454` — premissa "cota é a verdade", matriz de vereditos, alerta do `formerNav`.
- `docs/CONCILIACAO_RECALCULO.md:108,116,148` — fórmulas de `returnNavPerShare` e `returnContribution`.
