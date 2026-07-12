# Regras de definição do `type` (subtipo do ativo)

Este documento descreve, de forma autocontida, como derivar o campo **`type`** de um ativo financeiro na plataforma Beehus Controladoria, a partir do **nome/símbolo bruto do ativo** e do **`securityType`** já conhecido (`bond`, `brazilianFund`, `stockEtf`, `fund`, `otc`, etc.).

`type` é um **subcampo vinculado** a `securityType` — cada combinação válida pertence a um domínio fechado (não existe `type` livre; fora dessa lista o cadastro é inválido). O documento irmão **beehusName-regras.md** cobre como formatar o *nome de exibição*; este documento cobre como decidir o *subtipo técnico*.

---

## 0. Domínio completo `securityType` → `type`

Antes de tentar inferir o subtipo, confirme que o par que você vai atribuir existe nesta lista. Esta é a tabela autoritativa (equivalente à aba "tipos" do arquivo interno `Tipos.xlsx`):

| securityType | type | Instrumento |
|---|---|---|
| `stockEtf` | *(vazio)* | Ação, ETF, FII listado, FIDC listado, FIP listado |
| `bond` | `cdb` | CDB |
| `bond` | `cra` | CRA |
| `bond` | `cri` | CRI |
| `bond` | `lca` | LCA |
| `bond` | `lci` | LCI |
| `bond` | `lcd` | LCD |
| `bond` | `lf` | Letra Financeira |
| `bond` | `lf-sub` | Letra Financeira Subordinada |
| `bond` | `lig` | LIG (Letra Imobiliária Garantida) |
| `bond` | `lc` | LC (Letra de Câmbio) |
| `bond` | `ccb` | CCB (Cédula de Crédito Bancário) |
| `bond` | `cd` | CD / CDCA |
| `bond` | `debenture` | Debênture simples |
| `bond` | `infrastructureDebenture` | Debênture incentivada (infraestrutura) |
| `bond` | `inflation` | Bond indexado à inflação (genérico, sem prefixo próprio) |
| `bond` | `over` | Overnight / Compromissada simples |
| `bond` | `np` | Nota Promissória |
| `bond` | `precatorio` | Precatório |
| `bond` | `fixed` | Bond internacional taxa fixa |
| `bond` | `floating` | Bond internacional taxa variável |
| `brazilianGovernmentBond` | `lft` | Tesouro Selic (LFT) |
| `brazilianGovernmentBond` | `ltn` | Tesouro Prefixado (LTN) |
| `brazilianGovernmentBond` | `ntnb` | Tesouro IPCA+ com cupom (NTN-B) |
| `brazilianGovernmentBond` | `ntnb-p` | Tesouro IPCA+ sem cupom (NTN-B Principal) |
| `brazilianGovernmentBond` | `ntnc` | NTN-C (indexada ao IGP-M) |
| `sovereignBonds` | `fixed` | Título soberano taxa fixa (ex.: US Treasury) |
| `sovereignBonds` | `eurobonds` | Eurobonds soberanos |
| `sovereignBonds` | `tips` | TIPS (Treasury Inflation-Protected Securities) |
| `sovereignBonds` | `inflation` | Soberano indexado à inflação (fora do padrão TIPS) |
| `sovereignBonds` | `treasuryNote` | Treasury Note / T-Bill |
| `sovereignBonds` | `munis` | Municipals |
| `brazilianFund` | *(vazio)* | Fundo brasileiro (FIM, FIC, FIP, FIDC, FII não listado) |
| `fund` | `mutualFund` | Fundo de investimento offshore (padrão/default) |
| `fund` | `hedgeFund` | Hedge fund offshore |
| `fund` | `money-market` | Money market offshore |
| `fund` | `privateEquity` | Private equity offshore |
| `fund` | `ventureCapital` | Venture capital offshore |
| `fund` | `reits` | REITs offshore |
| `futures` | *(vazio)* | Contrato futuro (dólar, índice, commodities) |
| `privateMarket` | *(vazio)* | Mercado privado (fundo não classificado) |
| `options` | `call` | Opção de compra |
| `options` | `put` | Opção de venda |
| `otc` | `structuredNote` | Nota estruturada / COE / CLN |
| `otc` | `swap` | Swap |
| `otc` | `forward` | Forward |
| `otc` | `leverage` | Alavancagem / adiantamento |
| `otc` | `brazilianRepo` | Repo/compromissada brasileira negociada em balcão |
| `otc` | `brazilianTerm` | Termo brasileiro |
| `otc` | `others` | Outros OTC (cripto, commodities físicas) |
| `brazilianRepo` | `lft` | Compromissada lastreada em LFT |
| `brazilianRepo` | `ltn` | Compromissada lastreada em LTN |
| `brazilianRepo` | `ntnb` | Compromissada lastreada em NTN-B |
| `realAssets` | `realEstate` | Imóvel |
| `realAssets` | `vehicles` | Veículos |
| `realAssets` | `tangibleAssets` | Ativos tangíveis (joias, obras de arte) |
| `realAssets` | `privateEquity` | Participação societária direta |
| `realAssets` | `startups` | Startups |
| `realAssets` | `credit` | Crédito / dívida ativa |
| `realAssets` | `afac` | AFAC (Adiantamento para Futuro Aumento de Capital) |
| `realAssets` | `other` | Outros ativos patrimoniais |
| `poc` | *(vazio)* | Placeholder — ativo sem classificação definitiva |
| `benchmark` | `pu` | Benchmark de PU (uso interno) |
| `benchmark` | `return` | Benchmark de retorno — CDI, IPCA (uso interno) |

**Casos de atenção que geram erro comum:**
- Debênture **incentivada** (infraestrutura) → `infrastructureDebenture`, **não** `debenture`
- Letra Financeira **Subordinada** → `lf-sub`, **não** `lf`
- CDCA → `cd` (mesmo `securityType` `bond`; não existe `type` "cdca" separado)
- Fundo offshore de Money Market → `money-market` (não usar `mutualFund` como genérico)
- Fundo offshore de REITs → `reits` (não `mutualFund`)
- `stockEtf` e `brazilianFund` → `type` **sempre vazio**, mesmo que o nome sugira um subtipo (FIP, FIDC listado etc. — a granularidade fica só no `beehusName`)
- Compromissada por NTN-B → `securityType = brazilianRepo`, `type = ntnb` (não `bond`)
- COE, nota estruturada e CLN (credit-linked note) → `securityType = otc`, `type = structuredNote` (mesmo sendo tecnicamente uma dívida, CLN não vai para `bond`)

---

## 1. Ordem de prioridade para decidir o `type`

Ao processar um ativo novo, seguir esta ordem — pare na primeira que resolver com confiança:

1. **Prefixo/palavra-chave explícita no nome** (seção 2) — mais confiável, cobre a maioria dos casos onshore (CDB, CRI, CRA, LCI, LCA, DEB, LFT, LTN, NTN-B...).
2. **Identificação via código do ativo (ISIN/CNPJ/ticker) + pesquisa na Web** (seção 3) — quando o nome não deixa claro o subtipo, ou quando o prefixo pode ser ambíguo (ex.: bond internacional sem indicação de fixed/floating).
3. **Heurística por palavra-chave de estratégia/estrutura** (seção 4) — para `fund` offshore, `otc`, `realAssets`, onde não há um "prefixo de mercado" padronizado.
4. **Cruzamento com `Category` + `Market`** (Apêndice A) — quando esses campos de origem (ex.: planilha da corretora) estiverem disponíveis, usar como confirmação ou como fallback de última instância.
5. **Sinalizar incerteza** (`[?]`) — nunca inventar um `type`; se nada acima resolver com segurança, deixar marcado para revisão manual.

---

## 2. `securityType == 'bond'` — inferência pelo prefixo do nome

A grande maioria dos bonds onshore expõe o subtipo diretamente no início do nome/símbolo. Extrair a primeira palavra (ou primeiras duas, no caso de "Letra Financeira") e mapear:

| Prefixo no nome | `type` | Observação |
|---|---|---|
| `CDB` | `cdb` | Certificado de Depósito Bancário |
| `CRA` | `cra` | Certificado de Recebíveis do Agronegócio |
| `CRI` | `cri` | Certificado de Recebíveis Imobiliários |
| `LCA` | `lca` | Letra de Crédito do Agronegócio |
| `LCI` | `lci` | Letra de Crédito Imobiliário |
| `LCD` | `lcd` | Letra de Crédito do Desenvolvimento |
| `LF`, `Letra Financeira` (sem "Subordinada") | `lf` | Letra Financeira |
| `LF Sub`, `Letra Financeira Subordinada` | `lf-sub` | Verificar explicitamente a palavra "Subordinada"/"Sub" — sem ela, tratar como `lf` |
| `LIG` | `lig` | Letra Imobiliária Garantida |
| `LC` (isolado, não seguido de "I" ou "A" — cuidado para não confundir com LCI/LCA) | `lc` | Letra de Câmbio — pouco comum |
| `CCB` | `ccb` | Cédula de Crédito Bancário |
| `CDCA`, `CD` | `cd` | CDCA usa o mesmo `type` que "CD" genérico |
| `DEB`, `Debênture`, `Debenture` | `debenture` | Ver exceção de debênture incentivada abaixo |
| `NP`, `Nota Promissória` | `np` | Nota Promissória comercial |
| `Precatório`, `Precatorio` | `precatorio` | Precatório judicial |
| *(termos como "Compromissada", "Overnight")* quando registrados como `bond` (raro — normalmente vai para `brazilianRepo`/`otc`) | `over` | Confirmar se não deveria ser `brazilianRepo` antes de usar este type |

### 2.1 Debênture simples vs. incentivada (`debenture` vs `infrastructureDebenture`)

O prefixo `DEB`/`Debênture` sozinho **não** diz se é incentivada. Debêntures incentivadas (Lei 12.431/2011, isentas de IR para pessoa física) são emitidas por empresas de **infraestrutura** (energia, saneamento, transporte/rodovias, telecom) e frequentemente — mas não sempre — o nome ou a documentação menciona "incentivada" ou "infraestrutura".

**Como decidir:**
1. Se o nome contiver explicitamente "incentivada" ou "infraestrutura" → `infrastructureDebenture`.
2. Caso contrário, verificar o setor do emissor (via pesquisa): concessionárias de energia, rodovias, saneamento, telecom são fortes candidatas a `infrastructureDebenture`. Bancos, varejo, agronegócio geral → `debenture` simples.
3. Em caso de dúvida real, pesquisar o código/ISIN/CNPJ da emissão (ex.: "Debênture XYZ CVM" ou o código na B3/CETIP) — o prospecto normalmente identifica se é uma debênture de infraestrutura (art. 2º da Lei 12.431).
4. Se não for possível confirmar, usar `debenture` (mais conservador) e sinalizar `[?]`.

### 2.2 Bonds internacionais (`fixed` vs `floating`)

Quando `securityType == 'bond'` e o ativo é offshore (moeda ≠ BRL):
- Se a taxa é fixa e conhecida no nome (ex.: `4,90%`, `5,625%`) e o ativo não menciona "Floating Rate Note"/"FRN"/"variável" → `fixed`.
- Se o nome contém "Floating Rate", "FRN", "SOFR+", "Libor+", ou indexador variável → `floating`.
- Sem taxa explícita e sem indicação de FRN → assumir `fixed` (mais comum) e sinalizar `[?]` se não houver confirmação.

### 2.3 Bond indexado à inflação sem prefixo próprio (`inflation`)

Usado quando o instrumento onshore não se encaixa em nenhum prefixo de mercado (CDB/CRI/CRA/etc.) mas é claramente um bond indexado à inflação — caso raro; normalmente a inflação já é apenas o `indexer` (IPCA) de um CDB/CRI/CRA/Debênture, e o `type` continua sendo o do instrumento (`cdb`, `cri`...). Só usar `inflation` como `type` quando não há outro prefixo de instrumento identificável.

### Exemplos de inferência por prefixo

| Nome bruto do ativo | securityType | type inferido |
|---|---|---|
| `CDB Pan IPCA+5.55% 24/Mar/2027` | `bond` | `cdb` |
| `CDB BTG Pactual 100.72%CDI 16/Jul/2029` | `bond` | `cdb` |
| `CRA BTG Pactual 12,57% - 16/Nov/2033` | `bond` | `cra` |
| `CRA SLC CDI+0,60% - 15/Jul/2031` | `bond` | `cra` |
| `CRI Allos 105%CDI - 16/Abr/2029` | `bond` | `cri` |
| `CRI Brookfield CDI+0,79% - 19/Abr/2027` | `bond` | `cri` |
| `LCI CEF 96,50%CDI - 15/Mar/2027` | `bond` | `lci` |
| `LCA Itaú CDI 21/Out/2027` | `bond` | `lca` |
| `CDCA Vamos IPCA + 7.91% 15/Set/2031` | `bond` | `cd` |
| `Debênture Vamos IPCA - 15/Out/2031` | `bond` | `debenture` (confirmar se Vamos não é infra — logística, provavelmente `debenture` simples) |
| `DEB Concessionária Rodovia MG-050 IPCA 15/Dez/2030` | `bond` | `infrastructureDebenture` (concessionária de rodovia → infraestrutura) |
| `Wells Fargo - 4,90% - 25/Jul/2033` | `bond` | `fixed` |
| `Ford Motor Company 4.346% 08/Dec/2026` | `bond` | `fixed` |

---

## 3. Identificação via código do ativo (ISIN/CNPJ/ticker) + pesquisa

Quando o `securityType` já é conhecido mas o `type` não é óbvio pelo nome (ex.: bond internacional sem indicação clara de fixed/floating; ou o nome vier truncado/genérico), usar o identificador do ativo para pesquisar:

1. Se houver um **ISIN** (formato `[A-Z]{2}[A-Z0-9]{10}`) → pesquisar o ISIN diretamente. O resultado geralmente traz o tipo de cupom (fixed/floating), o emissor e se é um instrumento de infraestrutura.
2. Se houver um **CNPJ** (fundo brasileiro) → pesquisar o CNPJ na CVM/ANBIMA para confirmar a classificação ANBIMA do fundo (ajuda a decidir, por analogia, se um `fund` offshore correspondente seria `hedgeFund`/`mutualFund`/etc., ou simplesmente para confirmar que é `brazilianFund` sem subtipo).
3. Se houver um **código de negociação/ticker** de bond onshore (ex.: `RURA15`, `CPTS15`) → o prefixo de 4 letras costuma identificar o emissor e o dígito final costuma indicar a série; pesquisar o código para confirmar o instrumento subjacente (às vezes o código não deixa claro se é CRI ou CRA, por exemplo).

**Limite de pesquisa**: no máximo 3 buscas na Web e 1 fetch de página por ativo, para não gastar tempo/orçamento excessivo — extrair o máximo de informação possível de cada busca antes de repetir.

Se a pesquisa não retornar um tipo claro, aplicar a heurística de prefixo/palavra-chave (seções 2 e 4) como fallback e sinalizar `[?]`.

---

## 4. Heurística por palavra-chave — tipos sem prefixo de mercado padronizado

### 4.1 `securityType == 'fund'` (fundo offshore)

Não existe um "prefixo" como em bonds onshore — o subtipo é inferido pela **estratégia/estrutura** descrita no nome do fundo (e, quando disponível, pela liquidez/perfil de resgate do ativo):

| Palavras-chave no nome | `type` | Notas |
|---|---|---|
| "Money Market", "Liquidity", "Cash", "Short Duration" (muito líquido, D+0) | `money-market` | Ex.: `JPM USD Liquidity` |
| "Macro", "Long/Short", "Absolute Return", "Discretionary", "Tactical"; ou fundo com resgate muito ilíquido (ex.: 999/999/999/999 dias) | `hedgeFund` | Ex.: `Renaissance Equity Access` |
| "Private Equity", "PE", "Buyout", "Growth Equity", "Partners [Fundo] [Numeral romano]", "Capital Fund X" | `privateEquity` | Ex.: `Thoma Bravo` |
| "Venture", "VC", "Seed", "Early Stage" | `ventureCapital` | Ex.: `Signalfire` |
| "REIT", "Real Estate Investment Trust", "Realty", "Properties Trust" | `reits` | Fundo imobiliário listado/estruturado offshore |
| Nenhuma das anteriores; fundo "tradicional" (UCITS, sistemático, mandato amplo, líquido — D+1 a D+4) | `mutualFund` | Default/fallback quando não há sinal específico — ex.: `PIMCO GIS Income`, `ROBECO HY Bonds` |

> Quando a estratégia parecer híbrida (ex.: "Multimercado" offshore discricionário vs. sistemático/UCITS): usar `hedgeFund` se a gestão é discricionária/tática, e `mutualFund` se é sistemática ou estruturada como UCITS.

### 4.2 `securityType == 'otc'`

| Palavras-chave no nome | `type` | Notas |
|---|---|---|
| "COE", "Nota Estruturada", "Certificate", "CLN" (Credit-Linked Note) | `structuredNote` | Inclui COEs domésticos e CLNs internacionais |
| "Swap" | `swap` | Derivativo de troca de indexadores/taxas |
| "Forward", "NDF", "Termo" (quando internacional/câmbio) | `forward` | |
| "Alavancagem", "Adiantamento", "Margin Loan" | `leverage` | |
| "Compromissada"/"Repo" negociada em balcão (não classificada como `securityType = brazilianRepo` isolado) | `brazilianRepo` | Ver distinção com o `securityType` `brazilianRepo` abaixo |
| "Termo" (mercado brasileiro, ações/futuros a termo) | `brazilianTerm` | |
| Criptomoedas, commodities físicas (ouro físico, etc.), qualquer OTC que não se encaixe acima | `others` | Fallback conservador |

### 4.3 `securityType == 'brazilianRepo'` (compromissada com lastro identificado)

O `type` aqui reflete o **papel que lastreia** a operação compromissada, não o instrumento em si:

| Lastro identificado no nome | `type` |
|---|---|
| LFT (Tesouro Selic) | `lft` |
| LTN (Tesouro Prefixado) | `ltn` |
| NTN-B (Tesouro IPCA+) | `ntnb` |

### 4.4 `securityType == 'brazilianGovernmentBond'`

| Prefixo no nome | `type` |
|---|---|
| `LFT` | `lft` |
| `LTN` | `ltn` |
| `NTN-B` (com cupom) | `ntnb` |
| `NTN-B Principal` (sem cupom, zero cupom) | `ntnb-p` |
| `NTN-C` | `ntnc` |

> Atenção: `NTN-B` sozinho é diferente de `NTN-B Principal` — o segundo é um título com cupom zero (sem pagamento de juros semestrais). Verificar se o nome menciona "Principal" antes de decidir entre `ntnb` e `ntnb-p`.

### 4.5 `securityType == 'sovereignBonds'`

| Palavras-chave no nome | `type` |
|---|---|
| "US Treasury", "Treasury Bond", taxa fixa | `fixed` |
| "Eurobond" | `eurobonds` |
| "TIPS" | `tips` |
| Indexado à inflação, mas não TIPS (outro país) | `inflation` |
| "Treasury Note", "T-Bill", "T-Note" | `treasuryNote` |
| "Municipal", "Muni" | `munis` |

### 4.6 `securityType == 'options'`

| Sinal no nome/ticker | `type` |
|---|---|
| "Call", "C" antes do strike, ou letra de série de opção B3 nas faixas A–L (convenção B3: mês+tipo) | `call` |
| "Put", "P" antes do strike, ou letra de série de opção B3 nas faixas M–X | `put` |

> A convenção de letras de série de opções na B3 (A a L para calls de jan a dez; M a X para puts de jan a dez) é uma regra geral de mercado, não documentada nos materiais internos revisados — usar como apoio, mas confirmar pelo nome/descrição sempre que possível.

### 4.7 `securityType == 'realAssets'`

| Palavras-chave no nome | `type` |
|---|---|
| "Imóvel", "Apartamento", "Terreno", "Fazenda", "Sala Comercial" | `realEstate` |
| "Veículo", "Carro", "Barco", "Aeronave" | `vehicles` |
| "Joia", "Obra de Arte", "Relógio", "Coleção" | `tangibleAssets` |
| "Participação em [Empresa]", "Quotas de [Empresa]" (holding direta, não via fundo) | `privateEquity` |
| "Startup", empresa em estágio inicial (investimento direto, não via FIP) | `startups` |
| "Empréstimo", "Mútuo", "Dívida Ativa", "Recebível" (fora da estrutura CRI/CRA/FIDC) | `credit` |
| "AFAC" (Adiantamento para Futuro Aumento de Capital) — geralmente aparece explícito no nome | `afac` |
| Nenhum dos anteriores | `other` |

### 4.8 Sem subtipo (`type` vazio por definição)

`stockEtf`, `brazilianFund`, `futures`, `privateMarket`, `poc` → **sempre** `type` vazio, independentemente do nome do ativo. Não tentar inferir um subtipo para esses `securityType`.

---

## 5. Checklist de decisão resumido

Dado o nome bruto do ativo + `securityType` já definido:

1. **`securityType` não tem subtipo** (`stockEtf`, `brazilianFund`, `futures`, `privateMarket`, `poc`) → `type` vazio. Fim.
2. **`securityType == 'bond'`**:
   a. Ativo onshore → procurar prefixo de mercado no início do nome (seção 2) → mapear direto.
   b. Se o prefixo for `DEB`/`Debênture` → checar se é incentivada/infraestrutura (seção 2.1).
   c. Ativo offshore → decidir `fixed` vs `floating` (seção 2.2).
   d. Prefixo ambíguo ou ausente → pesquisar pelo ISIN/código (seção 3).
3. **`securityType == 'brazilianGovernmentBond'`** → mapear por prefixo (LFT/LTN/NTN-B/NTN-B Principal/NTN-C) (seção 4.4).
4. **`securityType == 'sovereignBonds'`** → mapear por palavra-chave (seção 4.5).
5. **`securityType == 'fund'`** (offshore) → aplicar heurística de estratégia/liquidez (seção 4.1); default `mutualFund` se nada se destacar.
6. **`securityType == 'otc'`** → aplicar heurística de palavra-chave (seção 4.2); default `others` se nada se encaixar.
7. **`securityType == 'brazilianRepo'`** → mapear pelo papel-lastro (seção 4.3).
8. **`securityType == 'options'`** → decidir call/put pelo nome/ticker (seção 4.6).
9. **`securityType == 'realAssets'`** → mapear por palavra-chave (seção 4.7); default `other`.
10. Em qualquer etapa, se a confiança for baixa → registrar o valor mais provável e sinalizar `[?]` para revisão humana. **Nunca deixar `type` vazio para um `securityType` que exige subtipo, e nunca inventar um valor fora do domínio da seção 0.**

---

## Apêndice A — Cruzamento com `Category` + `Market` (quando disponíveis)

Quando os dados de origem trazem também as colunas `Category` e `Market` (comum em planilhas de corretoras/custodiantes), esta tabela ajuda a **confirmar ou como fallback** para o par `securityType`/`type` quando o nome sozinho não é conclusivo:

| Category | Market | securityType | type | Observação |
|---|---|---|---|---|
| Renda Variável | Bovespa | `stockEtf` | *(vazio)* | Ações, FIIs, ETFs, FIDCs listados em B3 |
| Renda Variável | Fundos | `brazilianFund` | *(vazio)* | Fundo de ações (FIA, FIC FIA) |
| Renda Variável | Balcão | `stockEtf` | *(vazio)* | BDRs; se derivativo equity → `otc` → `swap` |
| Investimentos Alternativos | Bovespa | `stockEtf` | *(vazio)* | FIP, FIDC, FII listados em B3 |
| Investimentos Alternativos | Fundos | `brazilianFund` | *(vazio)* | FIP, FIDC, FII não listados |
| Investimentos Alternativos | Balcão | `otc` | `structuredNote` | COEs e notas estruturadas |
| Investimentos Alternativos | BM&F | `futures` | *(vazio)* | Futuros; se opção de bolsa → `options` → `call`/`put` |
| Investimentos Alternativos | Balcão - Internacional | `otc` | `structuredNote` | COE/nota estruturada internacional; se bond puro → `bond` → `fixed`/`floating` |
| Fixed Income | Balcão | `bond` | por instrumento | CDB, CRI, CRA, LCI, LCA, DEB etc. — inferir `type` pelo prefixo do symbol (seção 2) |
| Fixed Income | NYSE | `bond` | `fixed` ou `floating` | Bond corporativo internacional listado em NYSE |
| Fixed Income | Balcão - Internacional | `bond` | `fixed` ou `floating` | Bond offshore OTC |
| Multimercado | Fundos | `brazilianFund` | *(vazio)* | FIM, FIC MM, FIF MM |
| Multimercado | Carteira Offshore | `fund` | `hedgeFund` ou `mutualFund` | Usar `hedgeFund` se discricionário, `mutualFund` se sistemático/UCITS |
| Multimercado | Balcão | `otc` | `swap` | Derivativo/swap multimercado |
| Multimercado | Balcão - Internacional | `fund` | `hedgeFund` | Fundo offshore ou derivativo — verificar instrumento |
| OPCAO | Bovespa | `options` | `call` ou `put` | Opções listadas em B3 — inferir call/put pelo symbol |
| Private Equity | Fundos | `brazilianFund` | *(vazio)* | FIP nacional; se offshore → `fund` → `privateEquity` |
| RE Private Equity | Fundos | `brazilianFund` | *(vazio)* | FIP Imobiliário; se offshore → `fund` → `privateEquity` |
| RF Inflação | Balcão | `bond` | por instrumento | CRI, CRA, DEB indexados a IPCA — inferir pelo prefixo |
| RF Inflação | Fundos | `brazilianFund` | *(vazio)* | Fundo RF inflação (FIM, FIC RF) |
| RF Inflação | Titulos publicos | `brazilianGovernmentBond` | `ntnb` / `ntnb-p` / `ntnc` | NTN-B (com cupom), NTN-B Principal, NTN-C (IGP-M) |
| RF Inflação | Bovespa | `stockEtf` | *(vazio)* | ETF de renda fixa IPCA listado; ou `bond` se for CRI/CRA listado |
| RF Pós Fixado | Balcão | `bond` | por instrumento | CDB, CRI, CRA, LCI, LCA CDI/DI — inferir pelo prefixo |
| RF Pós Fixado | Fundos | `brazilianFund` | *(vazio)* | Fundo RF pós-fixado |
| RF Pós Fixado | Bovespa | `stockEtf` | *(vazio)* | ETF DI listado; ou `bond` se for título listado |
| RF Pós Fixado | Migração XPG | `bond` | por instrumento | Ativo em migração de custódia — usar type do instrumento |
| RF Pós Fixado | Balcão - Internacional | `bond` | `floating` | Bond offshore taxa variável (floating rate note) |
| RF Pré Fixado | Balcão | `bond` | por instrumento | CDB, CRI, CRA pré-fixados — inferir pelo prefixo |
| RF Pré Fixado | Fundos | `brazilianFund` | *(vazio)* | Fundo RF pré-fixado |
| RF Pré Fixado | Titulos publicos | `brazilianGovernmentBond` | `ltn` | Tesouro Prefixado (LTN) |
| Equity | NYSE / NASDAQ / bolsa internacional | `stockEtf` | *(vazio)* | Ações e ETFs internacionais |
| Titles Soberanos / Sovereign | Internacional | `sovereignBonds` | por instrumento | US Treasury → `treasuryNote`; TIPS → `tips`; outros → `fixed` |
| Compromissadas | Balcão | `brazilianRepo` | por lastro | `lft`, `ltn` ou `ntnb` conforme papel lastro |
| Patrimoniais | — | `realAssets` | por subtipo | `realEstate`, `vehicles`, `credit`, etc. |
| Padrão | Mercado de moedas | `futures` | *(vazio)* | FX futuro (dólar, euro); se OTC → `otc` → `forward` |
| Padrão | *(qualquer ou vazio)* | `poc` | *(vazio)* | Placeholder — sem classificação definitiva; geralmente é posição de caixa |

---

## Apêndice B — Notas gerais

- **Ordem de confiança**: prefixo explícito no nome > confirmação via ISIN/CNPJ/pesquisa > heurística de palavra-chave > cruzamento Category+Market > default conservador com `[?]`.
- **Nunca combinar `securityType` e `type` fora do domínio da seção 0** — se a combinação não existir na tabela, o cadastro é inválido mesmo que pareça semanticamente razoável.
- **`stockEtf` e `brazilianFund` nunca têm `type`** — resistir à tentação de "criar" um subtipo (ex.: "FII", "FIP") nesse campo; essa granularidade fica só no `beehusName`.
- **Debênture incentivada é o erro mais comum** — por padrão, se não há confirmação clara do setor de infraestrutura do emissor, prefira `debenture` simples e marque `[?]`, em vez de assumir `infrastructureDebenture` sem verificação (o inverso — perder a isenção fiscal reportada — é mais grave operacionalmente).
- Quando o ativo tiver mesmo `isin_code`/CNPJ que outro já cadastrado (mesmo papel, séries diferentes só variando taxa), o `type` deve ser **idêntico** entre as séries — nunca infira tipos diferentes para o mesmo papel.