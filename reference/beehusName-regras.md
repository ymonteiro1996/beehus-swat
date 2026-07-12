# Regras de definição do `beehusName`

Este documento descreve, de forma autocontida, como derivar o campo **`beehusName`** de um ativo financeiro na plataforma Beehus Controladoria, a partir do **nome/símbolo bruto do ativo** (como vem da corretora/custodiante, ex: coluna `symbol`) e do **tipo do ativo** (`securityType` + `type`).

`beehusName` é o **nome final de exibição** do ativo no sistema — já deve sair no formato refinado, pronto para uso, sem necessidade de uma etapa posterior de "limpeza". (Existe um campo derivado adicional, `beehusNameXP`, usado para uma visão ainda mais simplificada voltada a um parceiro específico — não é o foco deste documento, mas está descrito no Apêndice C caso seja necessário.)

---

## 0. Contexto: `securityType` e `type`

`securityType` e `type` são campos vinculados (domínio fechado) que classificam o instrumento. As regras de `beehusName` abaixo dependem de qual combinação o ativo tem. Tabela de referência resumida (as mais relevantes para este documento):

| securityType | type | Instrumento |
|---|---|---|
| `stockEtf` | *(vazio)* | Ação, ETF, FII listado, FIDC/FIP listado em bolsa |
| `bond` | `cdb`, `cra`, `cri`, `lca`, `lci`, `lcd`, `lf`, `lf-sub`, `lig`, `lc`, `ccb`, `cd`, `debenture`, `infrastructureDebenture`, `inflation`, `over`, `np`, `precatorio` | Títulos de crédito privado onshore |
| `bond` | `fixed`, `floating` | Bond internacional (offshore) |
| `brazilianGovernmentBond` | `lft`, `ltn`, `ntnb`, `ntnb-p`, `ntnc` | Títulos públicos federais brasileiros |
| `sovereignBonds` | `fixed`, `eurobonds`, `tips`, `inflation`, `treasuryNote`, `munis` | Títulos soberanos internacionais |
| `brazilianFund` | *(vazio)* | Fundo brasileiro não listado (FIM, FIC, FIP, FIDC, FII não listado) |
| `fund` | `mutualFund`, `hedgeFund`, `money-market`, `privateEquity`, `ventureCapital`, `reits` | Fundo offshore |
| `otc` | `structuredNote` (COE), `swap`, `forward`, `leverage`, `brazilianRepo`, `brazilianTerm`, `others` | OTC / produtos estruturados |
| `privateMarket`, `realAssets` | diversos | Mercado privado / ativos patrimoniais |

**Como saber se um ativo é offshore** (afeta qual conjunto de regras de `beehusName` se aplica):
1. `currency == 'USD'` (ou outra moeda ≠ BRL) → offshore
2. `type in ('fixed', 'floating')` → offshore (bond internacional)
3. `securityType == 'sovereignBonds'` → tratar com as mesmas regras de offshore

---

## 1. Capitalização geral (aplicar a TODOS os tipos, como última etapa)

Usar **Title Case modificado**: cada palavra inicia com maiúscula, exceto nas categorias abaixo.

### 1.1 Siglas — manter ALL CAPS

| Categoria | Siglas |
|---|---|
| Tipos de fundo/veículo | `FIDC`, `FIC`, `FII`, `FIP`, `FICFIP`, `FIM`, `FIF`, `ETF`, `BDR`, `UCITS` |
| Prefixos de título | `CDB`, `CRI`, `CRA`, `LCI`, `LCA`, `LCD`, `LF`, `DEB`, `COE`, `LTN`, `LFT`, `NTN-B`, `NTN-F`, `CDCA`, `CCB`, `CCE` |
| Sufixos de série/classe | `RL`, `RF`, `CP`, `IE`, `MM`, `IQ`, `FI` |
| Entidades/marcas | `XP`, `BTG`, `CEF`, `BB`, `BNDES`, `ANBIMA` |
| Índices | `CDI`, `IPCA`, `IGPM`, `SELIC`, `DI` |
| Internacionais | `MSCI`, `ACWI`, `SSAC`, `TIPS`, `S&P`, `NASDAQ` |

### 1.2 Preposições e artigos — minúsculas no meio da frase
`de`, `do`, `da`, `dos`, `das`, `e`, `a`, `o`, `em`, `com`, `para`, `no`, `na`, `por`

> A primeira palavra do `beehusName` sempre inicia com maiúscula, mesmo que seja preposição.

### 1.3 Algarismos romanos / letras de série — maiúsculas
`I`, `II`, `III`, `IV`, `V`, `VI` (quando indicam série/número de fundo); letras isoladas de classe (`A`, `B`, `C`, `D`).

### 1.4 Marcas com casing especial — preservar exatamente
- `iShares` (i minúsculo, S maiúsculo)
- `Itaú` (não `ItaúUnibanco`/`ITAU`)
- `Bradesco` (não `BRADESCO`)

### 1.5 Tickers B3 / mainId — manter ALL CAPS
Ex.: `KNCE11`, `XPML11`, `BTLG11` — nunca converter para minúsculas/misto.

### 1.6 Datas — não alterar
Ex.: `01/Mar/2027`, `15/Jun/2029` — preservar exatamente como formatadas (ver seção 6).

### Exemplos de capitalização

| beehusName incorreto | beehusName correto |
|---|---|
| `AGROJIVE I FIDC MEZANINO A` | `Agrojive I FIDC Mezanino A` |
| `ARTESANAL FIC FIDC II RL` | `Artesanal FIC FIDC II RL` |
| `JIVEMAUÁ BOSSANOVA CRÉDITO SECURITIZADO II FIDC RL SÊNIOR` | `Jivemauá Bossanova Crédito Securitizado II FIDC RL Sênior` |
| `KNCE11 - KINEA CRÉDITO ESTRUTURADO FIDC` | `KNCE11 - Kinea Crédito Estruturado FIDC` |
| `DEB RODOVIAS DO TIETÊ 17/MAR/2045` | `DEB Rodovias do Tietê 17/Mar/2045` |
| `ITAU PRIVATE WEALTH IQ RF FIC` | `Itaú Private Wealth IQ RF FIC` |
| `ISHARES MSCI ACWI UCITS - SSAC` | `iShares MSCI ACWI UCITS - SSAC` |

---

## 2. Regras por `securityType`/`type`

Aplicar a regra correspondente ao tipo do ativo. **Ordem de prioridade**: primeiro checar se é offshore (seção 2.1); se não for, seguir para a regra específica do `securityType`.

### 2.1 Ativos offshore (`currency != 'BRL'` ou `type in ('fixed','floating')`) e `sovereignBonds`

Aplicar as transformações abaixo **nesta ordem**, sem alterar o nome do emissor:

1. **Decimal ponto → vírgula** em taxas: regex `(\d+)\.(\d+)` → `\1,\2`
2. **Remover " - " antes de taxa**: regex `\s+-\s+(?=\d+[.,]\d+)` → ` ` (um espaço)
3. **Remover código ISIN no final**: regex `\s*-\s*[A-Z]{2}[A-Z0-9]{10}\s*$` → `` (string vazia)
4. **Converter data para `dd/Mmm/yyyy`** (meses em **inglês**, aplicar após os passos 1–3):
   - `YYYY-MM-DD` → regex `\b(\d{4})-(\d{2})-(\d{2})\b` → `\3/Mmm(\2)/\1`
   - `DD-MM-YYYY` → regex `\b(\d{1,2})-(\d{2})-(\d{4})\b` → `\1/Mmm(\2)/\3`

Tabela de meses (número → abreviação em inglês):

| Nº | Abrev | Nº | Abrev | Nº | Abrev | Nº | Abrev |
|---|---|---|---|---|---|---|---|
| 01 | Jan | 04 | Apr | 07 | Jul | 10 | Oct |
| 02 | Feb | 05 | May | 08 | Aug | 11 | Nov |
| 03 | Mar | 06 | Jun | 09 | Sep | 12 | Dec |

#### Exemplos

| symbol original | beehusName final |
|---|---|
| `BANK AMER CORP MEDIUM 3,974% 2030-07-02` | `BANK AMER CORP MEDIUM 3,974% 02/Jul/2030` |
| `Bombardier 6,00% 15-02-2028` | `Bombardier 6,00% 15/Feb/2028` |
| `Centrais eletricas Brasileiras 6,50% 11-01-2035` | `Centrais eletricas Brasileiras 6,50% 11/Jan/2035` |
| `Federative Republic Of Brazil 5,625% 21-02-2047` | `Federative Republic Of Brazil 5,625% 21/Feb/2047` |
| `Wells Fargo - 4,90% - 25/Jul/2033` | `Wells Fargo 4,90% 25/Jul/2033` |
| `Ford Motor Company 4.346% 08/Dec/2026 - US345370CR99` | `Ford Motor Company 4,346% 08/Dec/2026` |
| `HP Enterprises 4.85% 15/Oct/2031 - US42824CBU27` | `HP Enterprises 4,85% 15/Oct/2031` |
| `CLN Brazil - 7.00% - 06/Feb/2029` | `CLN Brazil 7,00% 06/Feb/2029` |
| `JPMorgan Chase - 5,72% - 14/Sep/2033` | `JPMorgan Chase 5,72% 14/Sep/2033` |
| `Powershares QQQ ETF` | `Powershares QQQ ETF` |
| `PIMCO GIS Income E Acc USD` | `PIMCO GIS Income E Acc USD` |

**Nota:** meses em português (Fev, Abr, Set, Out, Dez) indicam ativo onshore; meses em inglês indicam offshore.

---

### 2.2 Ações, FIIs e ETFs brasileiros (`securityType == 'stockEtf'`, NÃO offshore)

**Formato final:** `<ticker> - <nome limpo> <sufixo>`

O ticker (que também vai para `mainId`) fica no **início**, separado por ` - `. O nome não repete o ticker.

Transformações (nesta ordem):
1. Se o ticker aparecer no meio do nome original, removê-lo.
2. Se o nome tiver `" - SUFIXO"` inline (ex.: `"Kinea High Yield - FII"`): remover o `" - "` e mover o sufixo para o final → `"Kinea High Yield FII"`.
3. Se `"FII"` estiver no início do nome (ex.: `"FII Permuta X"`): mover para o final → `"Permuta X FII"`.
4. Sufixos reconhecidos que vão para o final: `FII`, `ETF`, `PN`, `ON`, `BDR` (normalizar para maiúsculas).
5. Para `FIDC`, `FIP`, `FIAGRO`: mantê-los na posição em que já aparecem (não mover).
6. Fallback: se não há sufixo reconhecível e o ticker termina em `11`, verificar (ex.: via Fundamentus) se é FII antes de decidir.

#### Exemplos

| symbol | ticker (mainId) | beehusName |
|---|---|---|
| `Bradesco - PN` | `BBDC4` | `BBDC4 - Bradesco PN` |
| `Kinea High Yield - FII` | `KNHY11` | `KNHY11 - Kinea High Yield FII` |
| `Xp Malls - FII` | `XPML11` | `XPML11 - Xp Malls FII` |
| `BTG Pactual Logística FII` | `BTLG12` | `BTLG12 - BTG Pactual Logística FII` |
| `XP FIC Fi Infra - XPID11 FII` | `XPID11` | `XPID11 - XP FIC Fi Infra FII` |
| `It Now SP 500 ETF` | `SPXR11` | `SPXR11 - It Now SP 500 ETF` |
| `Kinea Crédito Estruturado FIDC` | `KNCE11` | `KNCE11 - Kinea Crédito Estruturado FIDC` |
| `Kinea Estrat Infra CDI FIP` | `KNDI11` | `KNDI11 - Kinea Estrat Infra CDI FIP` |

---

### 2.3 Equities internacionais (`securityType == 'stockEtf'`, ticker de bolsa estrangeira, offshore)

**Formato final:** `<Nome oficial> - <TICKER>`

O ticker fica no **final**, separado por ` - `. O nome oficial é o nome da empresa/fundo em inglês, **sem sufixos societários** (`Inc.`, `Corp.`, `Ltd.`, `plc`, `N.V.`).

**Como obter o nome oficial:** pesquisar `"TICKER equity"` (ex.: `"PFE equity"`, `"EEM equity"`) e extrair o nome principal — preferir a versão curta amplamente reconhecida, evitando nomes muito longos.

Quando o ticker já é amplamente conhecido (`SPY`, `QQQ`, `MSFT`, `AAPL`, etc.), o nome pode ser preenchido sem pesquisa.

#### Exemplos

| ticker | beehusName |
|---|---|
| `PFE` | `Pfizer - PFE` |
| `EEM` | `iShares MSCI Emerging Markets - EEM` |
| `META` | `Meta Platforms - META` |
| `GDX` | `VanEck Gold Miners - GDX` |
| `MSFT` | `Microsoft - MSFT` |

---

### 2.4 Títulos públicos (`securityType == 'brazilianGovernmentBond'`)

Manter o nome existente (`LFT`, `LTN`, `NTN-B`, `NTN-C`...), substituindo o formato `MMM/AAAA` por `DD/MMM/AAAA`, usando a data completa do campo `maturityDate`.

Se `maturityDate` estiver ausente, manter o formato original `MMM/AAAA` (sem inventar um dia).

#### Exemplos

| symbol | maturityDate | beehusName |
|---|---|---|
| `LFT MAR-27` | `2027-03-01` | `LFT 01/Mar/2027` |
| `NTN-B AGO-26` | `2026-08-15` | `NTN-B 15/Ago/2026` |
| `LTN JAN-27` | `2027-01-01` | `LTN 01/Jan/2027` |
| `LTN JAN-26` | *(ausente)* | `LTN Jan/2026` |

---

### 2.5 Bonds onshore (`securityType == 'bond'`, NÃO offshore — CDB, CRI, CRA, LCI, LCA, LCD, LF, Debênture, CDCA, CCB etc.)

**Objetivo:** prefixo padronizado no início + remoção do indexador e da taxa, mantendo emissor e data de vencimento.

#### Passo 0 — Normalizar o prefixo do tipo

| Prefixo original | Código final |
|---|---|
| `Debênture`, `Debenture`, `Deb` | `DEB` |
| `CRI`, `CRA`, `LCI`, `LCA`, `CDB`, `CDCA` | já abreviados — sem alteração |

Regex: `^Deb(?:[eê]nture)?\s+` → `DEB `

#### Passo 1 — Detectar se é pré-fixado

Um bond é **pré-fixado** se:
1. O nome NÃO contém "Pós-fixado" (se contém, é pós-fixado — parar aqui, não é pré); **e**
2. Qualquer uma das condições é verdadeira:
   - `indexer == 'Pré'`
   - o nome contém "Pré-fixado", "Prefixado" ou "% Pré"

Bonds pré-fixados recebem a palavra literal **"Pré"** no lugar da taxa (não a taxa numérica).

#### Passo 2 — Sequência de remoção (aplicar NESTA ordem)

Definir `RATE = \d+(?:[,.]\d+)?` (número inteiro ou decimal, ex.: `104`, `5,55`, `6.00`).

1. **Índice + spread**: `IPCA+5,55%`, `CDI+0,60%` → remover
   - Regex: `\b(?:IPCA|CDI|IGPM|DI|Selic)\s*[+]\s*{RATE}%?\s*` → ``
2. **Taxa% + índice**: `104% CDI`, `100.72%CDI` → remover
   - Regex: `{RATE}%\s*(?:CDI|IPCA|IGPM|DI|Selic)\b\s*` → ``
3. **Taxa% + Pré** (só se pré-fixado): `12,57% Pré` → substituir por um token temporário `PREF_TOKEN`
   - Regex: `{RATE}%\s*Pré\b\s*` → `PREF_TOKEN`
4. **"Pós-fixado"**: remover
   - Regex: `Pós[-\s]?fixado\s*` → ``
5. **"Pré-fixado"/"Prefixado"** (só se pré-fixado) → `PREF_TOKEN`
   - Regex: `Pré[-\s]?fixado\s*|Prefixado\s*` → `PREF_TOKEN`
6. **Indexador isolado remanescente**: `IPCA - `, `CDI - `, ` CDI ` → remover
   - Regex: `\b(?:IPCA|CDI|IGPM|DI|Selic)\b\s*[-–]?\s*` → ``
7. **Taxa isolada remanescente**: `6.00%`, `12,57%` → remover
   - Regex: `{RATE}%\s*` → ``
8. **Limpar `" - "`** que sobrou antes da data ou no final do nome
9. **Inserir "Pré"** antes da data — apenas se o ativo é pré-fixado e o `PREF_TOKEN` ainda não foi inserido:
   - Regex: `(\b\d{1,2}/MÊS/\d{2,4}\b)` → `Pré \1`
10. **Restaurar o token**: `PREF_TOKEN` → `Pré`
11. **Limpar espaços múltiplos** (colapsar `  ` → ` `)

> **Nota:** hifens que fazem parte do nome do emissor (ex.: `MG-050`, `D'Or`) são preservados — as regras de remoção acima têm word boundaries e não afetam esses casos.

#### Exemplos

| symbol / nome bruto | beehusName |
|---|---|
| `Debênture Vamos IPCA - 15/Out/2031` | `DEB Vamos 15/Out/2031` |
| `Debênture UHE São Simão IPCA - 15/Out/2036` | `DEB UHE São Simão 15/Out/2036` |
| `Debênture Engie Pré-fixado - 15/Ago/2029` | `DEB Engie Pré 15/Ago/2029` |
| `Debênture Taesa IGPM - 15/Mar/2034` | `DEB Taesa 15/Mar/2034` |
| `DEB Concessionária Rodovia MG-050 IPCA 15/Dez/2030` | `DEB Concessionária Rodovia MG-050 15/Dez/2030` |
| `CDB Pan IPCA+5.55% 24/Mar/2027` | `CDB Pan 24/Mar/2027` |
| `CDB BTG Pactual 100.72%CDI 16/Jul/2029` | `CDB BTG Pactual 16/Jul/2029` |
| `CDB Banco Master Pré-fixado 22/Fev/2029` | `CDB Banco Master Pré 22/Fev/2029` |
| `CRA BTG Pactual 12,57% - 16/Nov/2033` | `CRA BTG Pactual Pré 16/Nov/2033` |
| `CRA BTG Pactual Pré-fixado 16/Nov/2033` | `CRA BTG Pactual Pré 16/Nov/2033` |
| `CRA Minerva Pré-fixado 16/Abr/2035` | `CRA Minerva Pré 16/Abr/2035` |
| `CRA SLC CDI+0,60% - 15/Jul/2031` | `CRA SLC 15/Jul/2031` |
| `CRI Allos 105%CDI - 16/Abr/2029` | `CRI Allos 16/Abr/2029` |
| `CRI Brookfield CDI+0,79% - 19/Abr/2027` | `CRI Brookfield 19/Abr/2027` |
| `CRI Corp Log III Pós-fixado 15/Set/2028` | `CRI Corp Log III 15/Set/2028` |
| `CRI Solfacil Pré-fixado - 07/Jun/2032` | `CRI Solfacil Pré 07/Jun/2032` |
| `LCI CEF 96,50%CDI - 15/Mar/2027` | `LCI CEF 15/Mar/2027` |
| `LCA Itaú CDI 21/Out/2027` | `LCA Itaú 21/Out/2027` |
| `CDCA Vamos IPCA + 7.91% 15/Set/2031` | `CDCA Vamos 15/Set/2031` |

---

### 2.6 COEs e produtos estruturados (`securityType == 'otc'`, `type == 'structuredNote'`)

Remover apenas o `" - "` separador imediatamente antes da data de vencimento — o restante do nome (estratégia, ativo-referência, banco emissor) é preservado integralmente.

Regex: `\s*[-–]\s*(?=\d{1,2}/(?:MMM|\d{2})/\d{2,4})` → ` ` (um espaço)

Aplicar também a regra de conversão/limpeza de data da seção 3 (converter data numérica para `DD/Mmm/YYYY` em português).

#### Exemplos

| symbol / nome bruto | beehusName |
|---|---|
| `COE XP 1X1 Índice Ações Globais - 16/Abr/2027` | `COE XP 1X1 Índice Ações Globais 16/Abr/2027` |
| `COE XP Dragões Asiáticos (China e Taiwan) - Alta Ilimitada 03/Mai/2027` | *(sem alteração — já correto)* |
| `COE XP 1X1 MAN MULTIMERCADO 16/04/27` | `COE XP 1X1 MAN MULTIMERCADO 16/Abr/2027` |

---

### 2.7 Fundos onshore (`securityType == 'brazilianFund'`)

Aplicar as abreviações de mercado abaixo ao nome, **nesta ordem** (mais específico primeiro), e depois a capitalização (seção 1).

| Padrão no nome | Substituição | Regex |
|---|---|---|
| `FICFIP` | `FIC FIP` | `\bFICFIP\b` |
| `FICFI` / `FICFIF` / demais `FICFI*` | `FIC` | `\bFICFI\w*\b` |
| `Crédito Privado` / `Créd Priv` | `CP` | `\bCr[eé]d(?:ito)?\s*\.?\s*Priv(?:ado)?\b` |
| `FIC de FIRF` / `FIC FIRF` | `FIC RF` | `\bFIC\s+(?:de\s+)?FIRF\b` |
| `Multimercado` | `MM` | `\bMultimercado\b` |
| `Mult` (isolado, sem continuação) | `MM` | `\bMult\b` |
| `Multestratégia` (typo) | `Multiestratégia` | `\bMult[Ee]strat[eé]gia\b` |
| `Fip` / `fip` | `FIP` | `\b[Ff][Ii][Pp]\b` |
| `Btg` | `BTG` | `\bBtg\b` |

**Notas importantes:**
- `\bMult\b` **não** afeta `Multimercado` (não há word boundary entre "t" e "i") nem `Multiestratégia`/`Multestratégia`.
- `FICFIP` deve ser aplicado **antes** de `FICFI*` para preservar o tipo FIP no resultado (senão viraria `FIC` genérico e perderia a informação de que é FIP).
- Limpar espaços múltiplos no final.

#### Exemplos

| symbol / nome bruto | beehusName |
|---|---|
| `Angá Crédito Estruturado FICFI MM CP` | `Angá Crédito Estruturado FIC MM CP` |
| `Artesanal CP FICFI MM` | `Artesanal CP FIC MM` |
| `Spectra VI Latam Pro FICFIP Multiestratégia` | `Spectra VI Latam Pro FIC FIP Multiestratégia` |
| `BTG Pactual Bond Pré II FIM Créd Priv IE` | `BTG Pactual Bond Pré II FIM CP IE` |
| `Trend Pós-fixado XP Seg Prev FIC FIRF` | `Trend Pós-fixado XP Seg Prev FIC RF` |
| `Trend PE VII FIC de FIRF Simples` | `Trend PE VII FIC RF Simples` |
| `Alpes II FIF Mult` | `Alpes II FIF MM` |
| `Vinci IV FIF Mult` | `Vinci IV FIF MM` |
| `Pipo Capital I Fip Multestratégia` | `Pipo Capital I FIP Multiestratégia` |
| `Btg Pactual Digital Tesouro Selic Simples FI RF` | `BTG Pactual Digital Tesouro Selic Simples FI RF` |

Para interpretar/expandir siglas adicionais que aparecerem em nomes de fundos (não cobertas pela tabela acima), consultar o **Apêndice A** deste documento.

---

### 2.8 Fundos offshore e demais tipos (copiar sem alteração)

`fund` (qualquer `type`: `mutualFund`, `hedgeFund`, `money-market`, `privateEquity`, `ventureCapital`, `reits`), `sovereignBonds` *(exceto a normalização de data/decimal da seção 2.1, que já se aplica)*, `privateMarket`, `realAssets` → copiar o nome de origem **sem alteração adicional**.

Para fundos offshore, usar o nome oficial em inglês, sem sufixos societários redundantes.

---

## 3. Regra de data e limpeza — ativos nacionais com vencimento no `beehusName`

Aplica-se a: `bond` onshore, `brazilianGovernmentBond`, `otc` (COE), `brazilianRepo`. (Não confundir com a regra de data em inglês da seção 2.1, que é exclusiva de ativos offshore.)

### Passo A — Converter data numérica para `dd/Mmm/yyyy` (meses em PORTUGUÊS)

- Formato `DD/MM/YY` (ano com 2 dígitos) → `DD/Mmm/20YY`
  - Regex: `\b(\d{1,2})/(\d{2})/(\d{2})\b` (o grupo do meio é numérico, não letras)
- Formato `DD/MM/YYYY` (ano com 4 dígitos) → `DD/Mmm/YYYY`
  - Regex: `\b(\d{1,2})/(\d{2})/(\d{4})\b`

Datas já no formato `DD/Mmm/YYYY` (ex.: `01/Mai/2027`) permanecem sem alteração — ir direto ao Passo B.

Tabela de meses em português:

| Nº | Abrev | Nº | Abrev | Nº | Abrev | Nº | Abrev |
|---|---|---|---|---|---|---|---|
| 01 | Jan | 04 | Abr | 07 | Jul | 10 | Out |
| 02 | Fev | 05 | Mai | 08 | Ago | 11 | Nov |
| 03 | Mar | 06 | Jun | 09 | Set | 12 | Dez |

### Passo B — Remover tudo que vier depois da data

Após normalizar a data para `DD/Mmm/YYYY`, remover **tudo** o que vier a seguir dela: espaços residuais, apóstrofes, algarismos romanos soltos, taxas, indexadores etc.

Regex (aplicar após o Passo A): `(\d{1,2}/[A-Za-z]{3}/\d{4})\s*.*$` → manter apenas o grupo 1 (a data).

#### Exemplos

| beehusName original/intermediário | beehusName final |
|---|---|
| `LFSN BRB 10/08/29` | `LFSN BRB 10/Ago/2029` |
| `LCI ITAU 05/05/27''` | `LCI ITAU 05/Mai/2027` |
| `TIET19 15/03/27 II` | `TIET19 15/Mar/2027` |
| `NEOE16 15/06/29` | `NEOE16 15/Jun/2029` |
| `COE XP 1X1 MAN MULTIMERCADO 16/04/27` | `COE XP 1X1 MAN MULTIMERCADO 16/Abr/2027` |
| `NTN-B 01/Mai/2055 IPCA + 5,72%` | `NTN-B 01/Mai/2055` |
| `NTN-B 01/Mai/2027'` | `NTN-B 01/Mai/2027` |
| `NTN-F 01/Jan/2031 11,31%'` | `NTN-F 01/Jan/2031` |
| `LFT 01/Mar/2027` *(já limpo)* | `LFT 01/Mar/2027` *(sem alteração)* |

---

## 4. Fluxo de decisão resumido (checklist)

Dado um `symbol`/nome bruto de ativo + `securityType` (+ `type`, `currency`, `indexer`, `maturityDate` quando disponíveis):

1. **É offshore?** (`currency != BRL` ou `type in ('fixed','floating')`) OU `securityType == 'sovereignBonds'`
   → aplicar regras da seção **2.1** (ponto→vírgula, remover `" - "` antes de taxa, remover ISIN final, data em inglês). Fim.
2. **`securityType == 'stockEtf'` e não offshore** → seção **2.2** (`TICKER - Nome Sufixo`).
3. **`securityType == 'stockEtf'` e offshore (ticker estrangeiro)** → seção **2.3** (`Nome oficial - TICKER`).
4. **`securityType == 'brazilianGovernmentBond'`** → seção **2.4** (manter nome, expandir `MMM/AAAA` → `DD/MMM/AAAA`).
5. **`securityType == 'bond'` e não offshore** → seção **2.5** (prefixo padronizado + remoção de indexador/taxa) seguida da seção **3** (formatação de data).
6. **`securityType == 'otc'` (`type == 'structuredNote'`, COE)** → seção **2.6** + seção **3**.
7. **`securityType == 'brazilianFund'`** → seção **2.7** (abreviações de mercado) + capitalização (seção **1**).
8. **Demais (`fund` offshore, `privateMarket`, `realAssets`)** → seção **2.8** (copiar como está, nome oficial em inglês se offshore).
9. **Sempre por último**: aplicar a capitalização geral (seção **1**), a menos que o tipo já tenha regra própria de capitalização (offshore mantém capitalização original do emissor).

---

## Apêndice A — Siglas de fundos brasileiros (de-para completo)

Fonte: análise de ~27.000 nomes reais de fundos brasileiros. Usar para expandir/interpretar siglas que não estejam cobertas na tabela de substituição da seção 2.7 (ex.: para entender a estrutura de um nome antes de decidir a abreviação final, ou para casos não previstos).

### Tipos de fundo (estrutura legal / veículo)

| Sigla | Significado completo | Obs. |
|---|---|---|
| FI | Fundo de Investimento | Genérico, classificação antiga |
| FIF | Fundo de Investimento Financeiro | Nova classificação — Resolução CVM 175/2022 |
| FIC | Fundo de Investimento em Cotas | Classificação antiga (investe em cotas de outros FI) |
| FC | Fundo de Cotas | Nova nomenclatura para FIC — Res. CVM 175/2022 |
| FIM | Fundo de Investimento Multimercado | Classificação antiga |
| FIA | Fundo de Investimento em Ações | Classificação antiga |
| FIRF | Fundo de Investimento em Renda Fixa | Abreviação composta usada em nomes |
| FIFE | Fundo de Investimento Financeiro Específico | Exclusivo para previdência — Res. CVM 175/2022 |
| FIE | Fundo de Investimento Específico | Exclusivo para previdência (Tipos I e II) |
| FIDC | Fundo de Investimento em Direitos Creditórios | Fundo de crédito estruturado |
| FIP | Fundo de Investimento em Participações | Private equity / venture capital |
| FII | Fundo de Investimento Imobiliário | Fundo imobiliário |
| FOF | Fundo de Fundos | Investe em cotas de outros fundos (Fund of Funds) |

### Sufixos / qualificadores de nome

| Sigla | Significado completo | Obs. |
|---|---|---|
| RL | Responsabilidade Limitada | Perdas dos cotistas limitadas ao patrimônio do fundo |
| CP | Crédito Privado | Fundo que investe em ativos de crédito privado |
| LP | Longo Prazo | Prazo médio da carteira > 365 dias (benefício fiscal IR) |
| IE | Investimento no Exterior | Fundo autorizado a investir > 20% no exterior |
| RF | Renda Fixa | Carteira predominantemente em renda fixa |
| RV | Renda Variável | Carteira com exposição a renda variável |
| NP | Não Padronizado | FIDC com ativos fora do padrão CVM (ex.: precatórios) |

### Estrutura / características do fundo

| Sigla / Termo | Significado completo | Obs. |
|---|---|---|
| CIC | Classe de Investimento em Cotas | Classe dentro de um FIF que investe em cotas |
| CI | Classe de Investimento | Subdivisão de um FIF (Res. CVM 175/2022) |
| MULT / MULTI | Multimercado | Estratégia multimercado |
| MM | Multimercado | Variante mais curta de MULT (preferível no beehusName) |
| CRED / CRÉD | Crédito | Geralmente combinado: CRED PRIV |
| PRIV | Privado | Crédito privado (combinado: CRED PRIV = Crédito Privado) |
| CRED PRIV / CRÉD PRIV | Crédito Privado | Abreviação composta |
| CP | Crédito Privado | Abreviação composta mais curta (preferível) |
| INV | Investimento | Abreviação de "Investimento" |
| DEB | Debêntures | Fundo com foco em debêntures |
| INFRA | Infraestrutura | Fundo de infraestrutura (debêntures incentivadas) |
| PREV | Previdência | Fundo destinado a planos de previdência |
| SEG | Seguros | Associado a seguradora (ex.: XP Seguros, Porto Seguro) |
| FEEDER | Fundo Alimentador | Fundo que investe em um único master fund |
| MASTER | Fundo Master / Principal | Fundo que recebe investimentos de feeders |
| PVT | Privado / Private | Segmento de gestão de patrimônio privado |
| ADVISORY | Consultoria / Advisory | Versão do fundo para clientes via assessoria |
| SELEÇÃO | Seleção | Versão do fundo via plataforma de seleção (ex.: XP Seleção) |
| LONG BIASED / LB | Long Biased | Estratégia com viés comprado em ações |
| LONG SHORT / LS | Long & Short | Estratégia com posições compradas e vendidas em ações |
| TOTAL RETURN / TR | Total Return | Estratégia que busca retorno absoluto |
| HEDGE | Hedge | Proteção cambial ou de mercado |
| HIGH GRADE | High Grade | Crédito de alta qualidade (grau de investimento) |

### Previdência

| Sigla / Termo | Significado completo | Obs. |
|---|---|---|
| PGBL | Plano Gerador de Benefício Livre | Previdência com dedução IR (IR na retirada total) |
| VGBL | Vida Gerador de Benefício Livre | Previdência sem dedução IR (IR só sobre rendimentos) |
| FIFE | Fundo de Investimento Financeiro Específico | Fundo de previdência — Res. CVM 175/2022 |
| FIE TIPO I | Fundo de Investimento Específico Tipo I | Fundo de previdência aberta (PGBL/VGBL) |
| FIE TIPO II | Fundo de Investimento Específico Tipo II | Fundo de previdência fechada (EFPC) |
| FLEXPREV | FlexPrev | Produto de previdência da XP |
| ICATU | Icatu Seguros | Seguradora parceira para previdência |
| BRASILPREV | BrasilPrev | Seguradora de previdência do Banco do Brasil |

### Benchmarks / indexadores

| Sigla | Significado completo | Obs. |
|---|---|---|
| CDI | Certificado de Depósito Interbancário | Principal benchmark de renda fixa |
| DI | Depósito Interbancário | Equivalente ao CDI |
| REF DI | Referenciado DI | Categoria ANBIMA: ≥ 95% da carteira em ativos atrelados ao DI/Selic |
| IPCA | Índice Nacional de Preços ao Consumidor Amplo | Inflação oficial brasileira (IBGE) |
| IGPM / IGP-M | Índice Geral de Preços - Mercado | Inflação FGV |
| IMA-B | Índice de Mercado ANBIMA - Série B | Índice de NTN-Bs (IPCA + juros) |
| IMA-B5 | IMA-B subíndice até 5 anos | NTN-Bs com vencimento até 5 anos |
| PRÉ | Pré-Fixado | Taxa de juros prefixada |
| IBOVESPA | Índice Bovespa | Principal índice de ações da B3 |

### Instrumentos financeiros

| Sigla | Significado completo | Obs. |
|---|---|---|
| BDR | Brazilian Depositary Receipt | Certificado de ação estrangeira negociado na B3 |
| NTN-B | Nota do Tesouro Nacional - Série B | Tesouro IPCA+ |
| LFT | Letra Financeira do Tesouro | Tesouro Selic |
| LTN | Letra do Tesouro Nacional | Tesouro Prefixado |
| CRI | Certificado de Recebíveis Imobiliários | Renda fixa imobiliário |
| CRA | Certificado de Recebíveis do Agronegócio | Renda fixa agronegócio |
| LCI | Letra de Crédito Imobiliário | Isento de IR para pessoa física |
| LCA | Letra de Crédito do Agronegócio | Isento de IR para pessoa física |
| DEB | Debêntures | Títulos de dívida corporativa |

### Regras de interpretação de nomes de fundos

1. **Ordem típica**: `[GESTOR/NOME PRÓPRIO] [ESTRATÉGIA] [TIPO DO FUNDO] [QUALIFICADORES]`
   - Ex.: `ABSOLUTE VERTEX FIC DE FIF MULTIMERCADO RL` → Gestor: Absolute | Estratégia: Vertex | FIC de FIF: Fundo de Cotas de FIF | Multimercado | Responsabilidade Limitada
2. **FIF vs FI**: fundos registrados após a Res. CVM 175/2022 usam FIF; os anteriores usam FI/FIM/FIA/FIRF.
3. **RL ao final** indica que os cotistas têm responsabilidade limitada ao valor das cotas subscritas.
4. **CP + IE juntos** indicam fundo com crédito privado e exposição no exterior.
5. **MASTER vs FEEDER**: Master é o fundo principal onde a carteira fica; Feeder/FIC/FC investe apenas no master.
6. **Prev / PREV ao final** (às vezes minúsculo): indica que o fundo é exclusivo ou tem versão para previdência.
7. **CIC** aparece como qualificador da classe dentro do FIF que investe em cotas de outros fundos.

---

## Apêndice B — Domínio completo `securityType` → `type`

Para referência ao classificar corretamente o ativo antes de aplicar as regras de `beehusName`:

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
| `bond` | `lig` | LIG |
| `bond` | `lc` | LC (Letra de Câmbio) |
| `bond` | `ccb` | CCB |
| `bond` | `cd` | CD / CDCA |
| `bond` | `debenture` | Debênture simples |
| `bond` | `infrastructureDebenture` | Debênture incentivada (infraestrutura) |
| `bond` | `inflation` | Bond indexado à inflação |
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
| `sovereignBonds` | `inflation` | Soberano indexado à inflação |
| `sovereignBonds` | `treasuryNote` | Treasury Note / T-Bill |
| `sovereignBonds` | `munis` | Municipals |
| `brazilianFund` | *(vazio)* | Fundo brasileiro (FIM, FIC, FIP, FIDC, FII não listado) |
| `fund` | `mutualFund` | Fundo de investimento offshore |
| `fund` | `hedgeFund` | Hedge fund offshore |
| `fund` | `money-market` | Money market offshore |
| `fund` | `privateEquity` | Private equity offshore |
| `fund` | `ventureCapital` | Venture capital offshore |
| `fund` | `reits` | REITs offshore |
| `futures` | *(vazio)* | Contrato futuro (dólar, índice, commodities) |
| `privateMarket` | *(vazio)* | Mercado privado (fundo não classificado) |
| `options` | `call` | Opção de compra |
| `options` | `put` | Opção de venda |
| `otc` | `structuredNote` | Nota estruturada / COE |
| `otc` | `swap` | Swap |
| `otc` | `forward` | Forward |
| `otc` | `leverage` | Alavancagem / adiantamento |
| `otc` | `brazilianRepo` | Repo/compromissada brasileira |
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
| `realAssets` | `afac` | AFAC |
| `realAssets` | `other` | Outros ativos patrimoniais |
| `poc` | *(vazio)* | Placeholder — ativo sem classificação definitiva |
| `benchmark` | `pu` | Benchmark de PU (uso interno) |
| `benchmark` | `return` | Benchmark de retorno — CDI, IPCA (uso interno) |

**Casos de atenção frequentes:**
- Debênture **incentivada** (infraestrutura) → `infrastructureDebenture`, não `debenture`
- Letra Financeira Subordinada → `lf-sub`, não `lf`
- CDCA → `cd` (mesmo `securityType` `bond`)
- Fundo offshore de Money Market → `money-market` (não `mutualFund`)
- Fundo offshore de REITs → `reits` (não `mutualFund`)
- `stockEtf` e `brazilianFund` → `type` **vazio**
- Compromissada por NTN-B → `brazilianRepo` / `ntnb` (não `bond`)

---

## Apêndice C — Campo derivado `beehusNameXP` (contexto adicional, não é o foco principal)

Existe um campo secundário, `beehusNameXP`, calculado **a partir do `beehusName` já pronto** (não a partir do nome bruto), usado como versão ainda mais simplificada para um parceiro específico (XP). As regras são análogas às da seção 2, mas mais agressivas na remoção de indexador/taxa e específicas de abreviação de fundos. Principais diferenças em relação ao `beehusName`:
- Ativos offshore: `beehusNameXP` = `beehusName` sem alteração adicional (ambos já removem taxa formatada, ISIN etc.).
- `bond` onshore: mesma lógica de remoção de indexador/taxa da seção 2.5, aplicada em cima do `beehusName`.
- `brazilianFund`: mesmas abreviações da seção 2.7.
- Demais tipos (`mutualFund`, `sovereignBonds`, `fund`, `privateMarket`): copiar sem alteração.

Se for necessário implementar esse campo também, ele reaproveita majoritariamente a mesma lógica documentada acima — a diferença é apenas o campo de entrada (`beehusName` já processado, em vez do `symbol` bruto).