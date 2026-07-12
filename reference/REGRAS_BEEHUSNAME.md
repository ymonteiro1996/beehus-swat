# Regras de Nomenclatura Beehus — `beehusName` por Tipo de Ativo

> Documento de referência autocontido. Extraído do sistema de cadastro de securities do Beehus
> (plataforma de controladoria de investimentos). Descreve como derivar o campo `beehusName`
> (nome de exibição do ativo) a partir de dados brutos de um extrato de custodiante, dado o tipo
> do ativo (`securityType`/`type`). Inclui também os campos correlatos (`ticker`, `indexer`,
> `yield`, `indexerPercentual`, `maturityDate`) porque a montagem do nome depende deles.

---

## 1. Convenções gerais

### 1.1 Meses em português (usados em TODO `beehusName` com data)
```
Jan Fev Mar Abr Mai Jun Jul Ago Set Out Nov Dez
```
Mapeamento completo: jan=1, fev=2, mar=3, abr=4, mai=5, jun=6, jul=7, ago=8, set=9, out=10, nov=11, dez=12.

### 1.2 Formato de data no nome
- Data completa conhecida → `DD/Mmm/AAAA` (ex.: `27/Out/2033`).
- Só mês/ano conhecido (comum quando o custodiante não informa o dia exato, ex. CRI/CRA) →
  `Mmm/AAAA` (ex.: `Ago/2029`).
- Bonds USD usam meses em **inglês**: `Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec`
  (ex.: `15/Dec/2031`).
- Quando não há data de vencimento real (ex.: compromissada), usa-se o placeholder
  `2099-01-01` no campo `maturityDate` (a API não aceita `null`), mas **não** aparece no
  `beehusName` desses casos.

### 1.3 Capitalização de nomes de emissor/issuer (`title_br`)
Regra palavra a palavra:
1. Se a palavra (lowercase) está na lista de siglas conhecidas → fica **toda maiúscula**.
   Lista: `fii fim fic fif fidc rl cp cic cdi ipca xp btg jgp man map maxp ms cs rf sa s.a. s/a
   esg gld etf cri cra cdb lf lci lca ccb np lcd cd lc cdca lfsn pre coe glg spy mchi espo ntnf
   1x1 bndes bb cef`
2. Se a palavra (lowercase) é artigo/preposição → fica **minúscula**.
   Lista: `de do da dos das e em ou com para`
3. Se a palavra já é "mixed case" (tem maiúscula E minúscula, ex. `JPMorgan`, `BofA`) → preserva como está.
4. Se a palavra começa com dígito (ex. `3G`, `10X`) → preserva como está.
5. Se tem hífen (ex. `ITAU-UNIBANCO`) → capitaliza cada pedaço separadamente (aplicando as regras
   1-2 por pedaço) → `Itau-Unibanco`.
6. Caso contrário → `Capitaliza()` só a primeira letra (`BANCO` → `Banco`).

Exemplo: `"BANCO XP S.A."` → `"Banco XP S.A."` (BANCO capitaliza, XP fica sigla maiúscula, S.A. é sigla maiúscula).

### 1.4 Placeholders e defaults obrigatórios (a API Beehus rejeita `null` nesses campos)
```
subscriptionSettlementDays = 0   (bonds BR / fundos quando não há regra específica)
subscriptionNAVDays        = 0
redemptionNAVDays          = 0
redemptionSettlementDays   = 0
maturityDate = "2099-01-01"      quando o ativo não tem vencimento real (ex. compromissada)
```

---

## 2. `securityType` — valores válidos

| Valor | Quando usar |
|---|---|
| `bond` | CDB, LCI, LCA, CRI, CRA, Debênture, LF, LIG, NP, bonds USD |
| `fund` | Fundos offshore (ISIN, LU/IE/etc.) |
| `brazilianFund` | Fundos brasileiros (CNPJ) |
| `stockEtf` | Ações, BDRs, ETFs, FIIs (BR ou USD) |
| `otc` | Compromissadas, COE, derivativos OTC |
| `brazilianGovernmentBond` | NTN-B, LFT, LTN |
| `sovereignBonds` | Títulos soberanos USD |
| `futures` | Contratos futuros |
| `options` | Opções |
| `privateMarket` | Private equity, crédito privado |

## 3. `type` — valores válidos por `securityType`

**`bond`**: `cdb`, `lci`, `lca`, `lf`, `lf-sub`, `cri`, `cra`, `debenture`, `infrastructureDebenture`,
`ccb`, `np`, `lc`, `lcd`, `cd`, `fixed` (bond USD pré), `floating` (bond USD pós).

**`fund`**: `mutualFund` (fundo offshore aberto), `hedgeFund`, `privateEquity`.

**`stockEtf`**: `type = null` sempre.

**`otc`**: `brazilianRepo` (compromissada), `structuredNote` (COE), `swap`, `forward`.

**`brazilianGovernmentBond`**: `ntnb` (IPCA), `lft` (Selic), `ltn` (Pré), `ntnb-p` (NTN-B Principal).

**`brazilianFund`**: `type` fica vazio/null.

> ⚠️ `"compromissada"` e `"privateMarket"` (como valor de `type`) **NÃO** são enums válidos — erro comum.
> ⚠️ `"coe"` (como valor de `type`) **NÃO** é válido — o correto é `structuredNote`.

---

## 4. Regras de `beehusName` por família — o núcleo do documento

### 4.1 CDB, LCA, LCI, LCD, CD, CCB, LC
```
Fórmula:  [TIPO] [EMISSOR] [TAXA/INDEXADOR] [DD/Mmm/AAAA]
```
**Sem** o label "Pós-fixado"/"Pré-fixado" no nome — a taxa já indica a modalidade.

| Modalidade | indexer | yield | indexerPercentual | Formato da taxa no nome | Exemplo completo |
|---|---|---|---|---|---|
| CDI% | `"CDI"` | vazio/0 | % do CDI (ex. 100, 84, 95) | `{pct:.2f}%CDI` | `CDB Banco XP 105.00%CDI 05/Abr/2027` |
| CDI+ spread | `"CDI"` | spread | 100 | `CDI + {spread:.2f}%` | `LF Banco XP CDI + 2.50% 05/Abr/2027`* |
| IPCA+ spread | `"IPCA"` | spread | 100 | `IPCA + {spread:.2f}%` | `CDB Banco Fibra IPCA + 8.35% 07/Abr/2027` |
| Pré-fixado | `"PRE"` | taxa | null/vazio | `{taxa:.2f}%` (sem label "Pré-fixado") | `CDB Banco Agibank 15.05% 09/Abr/2029` |

\* CDI+spread com label "Pós-fixado" no nome só se aplica à família LF (ver 4.2) — para CDB/LCA/LCI a taxa basta.

Mais exemplos reais: `LCA BTG Pactual 95.00%CDI 10/Out/2027`, `LCI Itaú 95.00%CDI 02/Jul/2028`,
`LCD BTG Pactual IPCA + 6.20% 08/Fev/2027`, `LCA Bradesco 13.97% 02/Mar/2029`.

**Ticker sugerido** (quando não há código CETIP disponível): concatenar `TIPO + EMISSOR (sem espaços) +
código-da-taxa + INDEXADOR + MMM + AAAA`, tudo maiúsculo sem pontuação/espaço.
Ex.: `CDBBTG10000CDI15122025` (padrão observado: `{tipo}{emissor}{taxa_sem_ponto}{indexer}{mes}{ano}`).
Prefira sempre o código CETIP real do extrato quando disponível — é o `ticker`/`mainId` preferencial.

### 4.2 LF / LF-SUB (Letra Financeira / Subordinada)
```
Fórmula:  LF [EMISSOR] [Pós-fixado|Pré-fixado] [DD/Mmm/AAAA]
```
**Sem taxa no nome** — só o rótulo da modalidade.
- `indexer` = `"CDI"` ou `"IPCA"` → label = `"Pós-fixado"`.
- `indexer` = `"PRE"` → label = `"Pré-fixado"`.

Exemplo: `LF Banco Omega Pós-fixado 20/Set/2028`.

### 4.3 LIG, NP (Letra Imobiliária Garantida / Nota Promissória)
```
Fórmula:  [TIPO] [TAXA] [DD/Mmm/AAAA]
```
**Sem emissor no nome.** A taxa segue a mesma formatação da seção 4.1 (`{pct:.2f}%CDI`,
`CDI + {spread:.2f}%`, `IPCA + {spread:.2f}%` ou `{taxa:.2f}%` para pré).

Exemplos: `LIG 11.25% 10/Jul/2029`, `NP 13.00% 30/Mar/2026`.

### 4.4 CRI, CRA
```
Fórmula:  [TIPO] [EMISSOR] [Pós-fixado|Pré-fixado] [DD/Mmm/AAAA  ou  Mmm/AAAA]
```
**Sem taxa no nome** (igual à lógica de LF, mas mantém o emissor). Usa data completa se
disponível, ou só mês/ano quando o extrato não traz o dia exato.

Exemplos: `CRI Raia Drogasil Pós-fixado 08/Mar/2027`, `CRI Trinity II Pós-fixado Jul/2027`.

### 4.5 Debênture / Debênture de Infraestrutura
```
Fórmula "canônica" (Manual Nomenclatura v2, mesma lógica de CDB/LCA):
  [TIPO] [EMISSOR] [TAXA/INDEXADOR] [DD/Mmm/AAAA]
```
> ⚠️ **Inconsistência conhecida**: um exemplo documentado no manual mostra
> `"Debênture Copel Geração e Transmissão IPCA + 5.7138% Pós-fixado 15/Out/2031"` — ou seja,
> **taxa E label "Pós-fixado" juntos**, diferente da regra de CDB (só taxa) e diferente da regra
> de CRI/CRA (só label, sem taxa). Isso reflete nomenclatura já existente na base de dados, não
> necessariamente a regra a seguir para ativos novos. **Recomendação**: antes de nomear uma
> debênture nova, consultar `Beehus.securities` por debêntures existentes do mesmo emissor/tipo
> e replicar o padrão encontrado; na ausência de precedente, usar a fórmula canônica (taxa, sem
> label Pós/Pré-fixado), igual a CDB.

### 4.6 Compromissada (repo)
```
securityType      = "otc"
type              = "brazilianRepo"
currency          = "BRL", country = "BR"
maturityDate      = "2099-01-01"   (obrigatório, placeholder)
indexer           = "CDI"
indexerPercentual = % do CDI (ex. 90, 80, 70)
yield             = 0
```
```
Fórmula:  Compromissada - {pct}% CDI - {código}
```
- `{pct}` sem casas decimais quando inteiro (`90`, não `90.0`); com casas se fracionário (`90.5`).
- Espaço antes e depois de "CDI".
- `{código}` = código do ativo subjacente (ex. `CRA024006N4`).

Exemplo: `Compromissada - 90% CDI - CRA024006N4`.

**Ticker**: `COMP{pct}CDI{código}` (sem espaços/pontuação), ex. `COMP90CDICRA024006N4` — este é o
identificador canônico usado para matching; **nunca** usar o código bruto do custodiante como
`ticker` para compromissada (pode colidir com o mesmo código em taxas diferentes).

> ⚠️ Nota de dados legados: consultas ao banco mostram compromissadas antigas cadastradas com
> combinações inconsistentes de `securityType`/`type` (ex. `brazilianRepo`/`ntnb`, `otc`/`others`,
> `bond`/`cra`, `privateMarket`/null). A regra acima é a que deve ser usada para **novos**
> cadastros; ao mapear/comparar com existentes, considerar essas variações.

### 4.7 COE (Certificado de Operações Estruturadas)
```
securityType = "otc"              (NÃO usar "bond")
type         = "structuredNote"   (NÃO usar "coe")
currency     = "BRL", country = "BR"
```
```
Fórmula:  COE [EMISSOR] [DD/Mmm/AAAA]
```
Exemplo: `COE Itaú Unibanco 25/Mar/2031`.

Para COEs com produto/estratégia nomeados (ex. distribuidor XP), pode-se incluir o nome do
produto: `COE {EMISSOR} {NOME DO PRODUTO} {DD/Mmm/AAAA}`. O `ticker` sintético combina emissor +
abreviação do produto + abreviação da estratégia + data (`DDMMYYYY`), ex.: `COEXPBOLSAAMERBD16062026`.
Se duas linhas gerarem o mesmo ticker (produtos emitidos em datas de captação diferentes mas
mesmo vencimento), desambiguar acrescentando a data de emissão como sufixo ao ticker e ao nome
(`[DD/Mmm/AAAA]` de emissão entre colchetes no fim do `beehusName`).

### 4.8 Fundo Brasileiro (`brazilianFund`, CNPJ)
```
securityType = "brazilianFund"   (type fica vazio)
currency     = "BRL"
taxId/ticker = CNPJ (com pontuação: XX.XXX.XXX/XXXX-XX)
```
```
Fórmula:  [Nome do Fundo conforme CVM/ANBIMA]   — SEM prefixo "Fund"/"Fundo"
```
Exemplos: `BTG Pactual SP 500 BRL FIM`, `Itau Estrategia SP500 USD Mult FIIF CIC Resp Limitada`.

- O nome oficial deve vir do cadastro CVM (campo `DENOM_SOCIAL` do `cad_fi.csv`, o cadastro
  público de fundos da CVM) ou da lâmina/ANBIMA — não inventar nem abreviar livremente.
- Aplicar `title_br` (seção 1.3) sobre o nome oficial (que normalmente vem todo em maiúsculas no
  cadastro CVM) para chegar no casing usado no Beehus.
- Se o fundo tiver "CD" (classe/cotas de outro fundo, feeder), adicionar
  `feederIds = [{"id": "<CNPJ com pontuação>", "feederName": "CD"}]`.
- `redemptionNAVDays`/`redemptionSettlementDays`: se o fundo é **condomínio fechado** (FII, FIP,
  FIDC e similares, ou campo CVM `CONDOM = "Fechado"`) → `999`/`999`. Caso contrário, usar o
  prazo de conversão/pagamento de resgate da lâmina do fundo (ou de fonte de referência de
  mercado, ex. Economatica: campos `prazo_conversao`→NAV, `prazo_pgto`→Settlement).

### 4.9 Fundo Offshore (ISIN)
```
securityType = "fund", type = "mutualFund"
currency = "USD", ticker = null
isIn = ISIN (12 caracteres)
feederIds = [{"id": "XX:ISIN", "feederName": "CD"}]   (XX = 2 primeiras letras do ISIN = país)
subscriptionNAVDays = 1, subscriptionSettlementDays = 3
redemptionNAVDays = 1, redemptionSettlementDays = 3
```
```
Fórmula:  [Nome oficial do fundo/classe]   — SEM prefixo "Fund"
```
Exemplo: `JPM Global Bond Opportunities A Accumulating USD`.
O sistema deriva o `mainId` a partir do `isIn` — nunca setar `mainId` diretamente.

### 4.10 Título Público Brasileiro (`brazilianGovernmentBond`)
```
type = "ntnb" (IPCA/NTN-B) | "lft" (Selic/LFT) | "ltn" (Pré/LTN) | "ntnb-p" (NTN-B Principal)
ticker = null (sempre)
isIn = ISIN do título (ex. "BRSTNCNTB674")  → campo correto para o ID principal
selicCode = código numérico Selic (ex. "760198")  → campo separado, pode ficar vazio
currency = "BRL", country = null
yield = null, indexer = null, indexerPercentual = null   (nunca preencher taxa)
```
```
Fórmula:  [TIPO] Mmm/AAAA     — SEM taxa no nome
```
Exemplos: `NTN-B Mai/2031`, `NTN-B Ago/2032`, `LFT Dez/2030`.

### 4.11 Ação / ETF / FII — Brasil (`stockEtf`)
```
securityType = "stockEtf", type = null
currency = "BRL", country = "BR"
exchange = "BVMF"
ticker = "BR:{TICKER_B3}"
feederIds = [{"id": "BR:{TICKER_B3}", "feederName": "CD"}]
subscriptionSettlementDays = redemptionSettlementDays = 2
subscriptionNAVDays = redemptionNAVDays = 0
```
```
Fórmula:  Stock {NOME DA EMPRESA}         (ações)
          {TICKER}                        (quando não há nome disponível — ex. FIIs simples)
```
Exemplo: `Stock Berkshire Hathaway Inc Class B` (padrão internacional); para ativos BR sem nome
disponível, usar o próprio ticker como nome provisório (ex. `VGRI11`).

### 4.12 Ação / ETF — EUA/Internacional (`stockEtf`)
```
securityType = "stockEtf", type = null
currency = "USD", country = "US"
exchange = MIC code (XNYS, XNAS, XLON, XPAR, XLUX, XTKS, XCME...)
ticker = "US:{SYMBOL}"
feederIds = [{"id": "US:{SYMBOL}", "feederName": "CD"}]
subscriptionSettlementDays = redemptionSettlementDays = 2
subscriptionNAVDays = redemptionNAVDays = 0
```
```
Fórmula:  Stock {Nome da Empresa}   (ações)
          {Nome do ETF}             (ETFs — sem prefixo "Stock"/"Fund")
```
Exemplo: `Stock Berkshire Hathaway Inc Class B`.

### 4.13 Bond USD (corporativo ou soberano)
```
securityType = "bond", type = "fixed" (pré) | "floating" (pós)
currency = "USD", country = "US" (emissor americano) | "BR" (emissor BR no exterior)
ticker = ISIN ou CUSIP
subscriptionSettlementDays = redemptionSettlementDays = 1
subscriptionNAVDays = redemptionNAVDays = 0
feederIds = []
```
```
Fórmula:  {Emissor} {Taxa%} {DD/Mmm EN/AAAA}      — SEM prefixo "Bond"
```
- Meses em **inglês** (Jan, Feb, Mar... Dec).
- Taxa com duas casas decimais e `%` (ex. `8.25%`).
- Emissor: se vier em CAIXA ALTA da fonte, aplicar `title_br`; se já vier em mixed-case
  (ex. "JPMorgan"), preservar como está.

Exemplos: `Petrobras 8.25% 15/Dec/2031`, `Apple Inc 4.50% 08/Aug/2029`.

---

## 5. Regras de `indexer` / `yield` / `indexerPercentual` (renda fixa BR — recapitulando)

| Modalidade | `indexer` | `yield` | `indexerPercentual` |
|---|---|---|---|
| % do CDI | `"CDI"` | `0` | percentual do CDI (ex. `100`, `84`, `95`) |
| CDI + spread | `"CDI"` | spread numérico (ex. `2.5`) | `100` |
| IPCA + spread | `"IPCA"` | spread numérico | `100` |
| Pré-fixado | `"PRE"` | taxa numérica | `null` |

Título público BR (NTN-B/LFT/LTN): todos os três campos ficam `null` — a taxa não é modelada
no cadastro do título público.

---

## 6. Geração de ticker sintético (quando não há código oficial do custodiante)

Usar como último recurso — sempre preferir o código real (CETIP, ISIN, CUSIP, código Selic) do
extrato do custodiante quando disponível.

| Família | Padrão de ticker sintético | Exemplo |
|---|---|---|
| CDB/LCA/LCI/LF/LIG/NP/CRI/CRA (sem CETIP) | `{TIPO}{EMISSOR_SEM_ESPACO}{TAXA}{INDEXADOR}{MMM}{AAAA}` | `LIG1050PRE01072026` |
| Compromissada | `COMP{pct}CDI{código}` | `COMP90CDICRA024006N4` |
| COE | `COE{EMISSOR}{ABREV_PRODUTO}{ABREV_ESTRATEGIA}{DDMMAAAA}` | `COEXPBOLSAAMERBD16062026` |
| Fundo offshore (feeder) | `{ISIN[:2]}:{ISIN}` | `LU:LU1103307317` |
| Ação/ETF USD | `US:{SYMBOL}` | `US:AAPL` |
| Ação/FII BR | `BR:{TICKER_B3}` | `BR:VGRI11` |
| Bond USD | ISIN ou CUSIP direto (não sintético) | `US037833EN61` |
| Título público BR | `ticker = null`; usar `isIn` (ISIN) e opcionalmente `selicCode` | — |

---

## 7. Erros conhecidos da API Beehus e como evitá-los

| Erro | Causa | Solução |
|---|---|---|
| `Esperado (...) e recebido (null) no campo body.type` | `type = null` | Sempre definir `type` — ver tabela da seção 3 |
| `Esperado (string) e recebido (null) no campo body.maturityDate` | `maturityDate = null` | Usar `"2099-01-01"` como placeholder |
| `Selecione o subtipo do ativo` | `securityType` errado | Compromissada = `otc`/`brazilianRepo`; COE = `otc`/`structuredNote` |
| `Required` (aparece 4×) em bond/brazilianGovernmentBond | Campos de settlement ausentes | Setar `subscriptionSettlementDays`, `subscriptionNAVDays`, `redemptionNAVDays`, `redemptionSettlementDays` = `0` |
| `Invalid enum value... received 'coe'` | `type` inválido | COE → `type = "structuredNote"` |
| `Invalid enum value... received 'compromissada'` | `type` inválido | Compromissada → `type = "brazilianRepo"` |
| `O ID principal é obrigatório` (bond sem ticker) | `mainId` ausente | Gerar ticker sintético (seção 6) |
| `O ID principal é obrigatório` (brazilianGovernmentBond) | Código no campo errado | Código vai em `selicCode`, **não** em `ticker`; `mainId` é derivado de `isIn` |
| `O ID principal é obrigatório` (fundo offshore) | `isIn` vazio | Preencher `isIn` com o ISIN |
| `Invalid enum value... received 'NYSE'` | Exchange em formato errado | Usar código MIC: `"XNYS"` (não o nome da bolsa) |
| Ticker com aspas literais `"VALOR"` no campo | Dado colado com aspas incluídas no conteúdo | Remover as aspas do valor |

---

## 8. Resumo — passo a passo para nomear um ativo novo

1. Identifique a família do ativo (CDB? Fundo? COE? Bond USD? etc.) a partir do texto bruto do
   custodiante.
2. Determine `securityType`/`type` pela tabela das seções 2-3.
3. Extraia emissor, taxa/indexador e data de vencimento do texto bruto.
4. Aplique `title_br` (seção 1.3) sobre o nome do emissor/fundo, se vier em CAIXA ALTA.
5. Monte o `beehusName` usando a fórmula específica da família (seção 4) — preste atenção em
   quais famílias **omitem** taxa (LF, CRI/CRA) ou **omitem** emissor (LIG, NP) ou **omitem**
   o label Pós/Pré-fixado (CDB/LCA/LCI/LCD/CD/CCB/LC, LIG, NP).
6. Preencha `indexer`/`yield`/`indexerPercentual` conforme a seção 5.
7. Gere/obtenha o `ticker` (prefira código real; sintético só como último recurso — seção 6).
8. Preencha os campos obrigatórios de settlement/NAV (default `0`, exceto casos especiais como
   fundo fechado = `999` ou fundo offshore = `1`/`3`).
9. Confira contra a tabela de erros conhecidos (seção 7) antes de enviar.
