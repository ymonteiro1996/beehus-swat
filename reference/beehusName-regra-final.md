# beehusName — Regra final consolidada (v1, 2026-07-03)

## 0. Fontes e precedência

Este documento consolida, para uso na feature de **criação de ativos sem match** (Controle de
Cargas → Mapeamento), a regra de sugestão do campo `beehusName` a partir do nome/símbolo bruto do
ativo (`uid`/`symbol`) e do tipo detectado (`securityType`/`type`).

Ordem de precedência definida pelo usuário (2026-07-03):
1. **`reference/beehusName-regras.md`** — fonte primária. Onde este arquivo cobre um caso, sua
   regra vale, mesmo quando diverge de (2) ou do código já existente.
2. **`reference/REGRAS_BEEHUSNAME.md`** — usado apenas para **completar lacunas** não cobertas por
   (1): campos técnicos correlatos (`indexer`/`yield`/`indexerPercentual`), geração de ticker
   sintético, defaults de settlement/NAV, tabela de erros conhecidos da API, e tipos que (1) não
   aborda (compromissada/`otc`/`brazilianRepo`).
3. **Implementação atual** em `templates/controlpanel.html` (`_parseUnprocessedId`,
   `_parseCriCraDeb` e variantes, `_buildRegRow`) — reflete uma regra anterior (spec referenciada
   nos comentários como `PARSER_SECURITIES.md`/`docs/CADASTRO_ATIVOS.md §12-§16`, que **não existe
   mais no repositório**). Onde ela diverge da regra final abaixo, **o código precisa ser
   atualizado** — está desatualizado, não é fonte de verdade. Também não é fonte de verdade para
   canonicalização de emissor/devedor (seção 3) — aí o código já está correto e vira parte da
   regra final.

### Conflitos identificados e decisão do usuário

Quatro pontos genuínos de conflito entre (1) e (2)/código foram levantados e decididos, todos a
favor do arquivo (1):

| Caso | (1) beehusName-regras.md | (2)/código atual | Decisão |
|---|---|---|---|
| CDB/LCA/LCI/LCD/LC/CCB | sem taxa/indexador no nome | com taxa formatada | **(1) — sem taxa** |
| CRI/CRA pós-fixado | sem rótulo | rótulo "Pós-fixado" sempre | **(1) — sem rótulo** |
| Debênture | sem taxa, sem rótulo, prefixo `DEB` | com taxa e/ou rótulo, prefixo `Debênture` (código) | **(1) — sem taxa, sem rótulo, `DEB`** |
| Título público, dia conhecido | `DD/Mmm/AAAA` quando `maturityDate` existe | sempre `Mmm/AAAA` | ~~(1) — dia completo~~ **revertido em 2026-07-03 → sempre `Mmm/AAAA`** (ver §4.7 — 94% dos ativos reais no cache não mostram o dia) |

Por consistência com essas decisões, **LF** (também listado na família "bonds onshore" da seção
2.5 do arquivo 1) segue a mesma regra geral: sem taxa, sem rótulo "Pós-fixado", só "Pré" quando
pré-fixado (ver §4.2 abaixo — não havia exemplo explícito de LF no arquivo 1, mas está incluído na
mesma tabela de tipos da seção 2.5, então a regra geral se aplica por extensão).

---

## 1. Contexto — `securityType` / `type`

Ver tabela completa em `beehusName-regras.md §0` e Apêndice B de `REGRAS_BEEHUSNAME.md` (idênticas
em essência). Resumo:

| securityType | type | Instrumento |
|---|---|---|
| `stockEtf` | *(vazio)* | Ação, ETF, FII/FIDC/FIP listado em bolsa |
| `bond` | `cdb`,`cra`,`cri`,`lca`,`lci`,`lcd`,`lf`,`lf-sub`,`lig`,`lc`,`ccb`,`cd`,`debenture`,`infrastructureDebenture`,`inflation`,`over`,`np`,`precatorio` | Crédito privado onshore |
| `bond` | `fixed`,`floating` | Bond internacional (offshore) |
| `brazilianGovernmentBond` | `lft`,`ltn`,`ntnb`,`ntnb-p`,`ntnc` | Títulos públicos federais BR |
| `sovereignBonds` | `fixed`,`eurobonds`,`tips`,`inflation`,`treasuryNote`,`munis` | Soberanos internacionais |
| `brazilianFund` | *(vazio)* | Fundo brasileiro não listado |
| `fund` | `mutualFund`,`hedgeFund`,`money-market`,`privateEquity`,`ventureCapital`,`reits` | Fundo offshore |
| `otc` | `structuredNote`(COE),`swap`,`forward`,`leverage`,`brazilianRepo`,`brazilianTerm`,`others` | OTC / estruturados |
| `privateMarket`,`realAssets` | diversos | Mercado privado / patrimonial |

**Offshore** = `currency != 'BRL'` OU `type in ('fixed','floating')` OU `securityType == 'sovereignBonds'`.

> ⚠️ Enums inválidos conhecidos (fonte: REGRAS_BEEHUSNAME.md §3/§7): `type` NÃO pode ser
> `"compromissada"` (correto: `otc`/`brazilianRepo`) nem `"coe"` (correto: `otc`/`structuredNote`).

---

## 2. Capitalização geral (aplicar por último, a todos os tipos onshore)

Base: `beehusName-regras.md §1` (Title Case modificado). Regras:

1. **Siglas — ALL CAPS**: `FIDC FIC FII FIP FICFIP FIM FIF ETF BDR UCITS`, `CDB CRI CRA LCI LCA LCD
   LF DEB COE LTN LFT NTN-B NTN-F CDCA CCB CCE`, `RL RF CP IE MM IQ FI`, `XP BTG CEF BB BNDES
   ANBIMA`, `CDI IPCA IGPM SELIC DI`, `MSCI ACWI SSAC TIPS S&P NASDAQ`.
   **Complemento** (gestoras/produtos não listados no arquivo 1, presentes no arquivo 2 — sem
   conflito, apenas mais itens): `JGP MAN MAP MAXP MS CS GLG SPY MCHI ESPO 1X1 LFSN`.
2. **Preposições/artigos minúsculos no meio**: `de do da dos das e a o em com para no na por`
   (a primeira palavra do nome nunca fica minúscula, mesmo sendo preposição).
3. **Romanos/letras de série maiúsculas**: `I II III IV V VI`, letras de classe isoladas `A B C D`.
4. **Marcas com casing especial, preservar exatamente**: `iShares`, `Itaú`, `Bradesco`.
5. **Tickers/mainId**: sempre ALL CAPS (`KNCE11`, `XPML11`).
6. **Datas**: nunca alterar depois de formatadas (`01/Mar/2027`).
7. Palavras já mixed-case na fonte (`JPMorgan`, `BofA`) e palavras começadas por dígito (`3G`,
   `10X`) são preservadas como estão (fonte: REGRAS_BEEHUSNAME.md §1.3, não conflita).
8. **Fallback usado quando o emissor/devedor não está na tabela de canonicalização da seção 3**
   (fonte: código — `_titleCaseEmissor`, não documentado em nenhum dos dois arquivos de
   referência):
   - Palavras com **≤ 2 caracteres** viram maiúsculas automaticamente (tratadas como sigla), mesmo
     sem estar na lista de siglas do item 1.
   - Lista de conectores minúsculos usada neste fallback: `do de da dos das no na nos nas e o a os
     as em para`. Diverge da lista "oficial" do item 2 (que tem `com`/`por` mas não `nos`/`nas`).
     **Unificar** ao implementar: `de do da dos das no na nos nas e a o os as em com para por`.
   - `S/A` e `S.A.` (isolados) viram exatamente `S/A` / `S.A.`.

---

## 3. Canonicalização de emissor / devedor

> Fonte: código (`_BANK_CANONICALS`, `_DEVEDOR_CANONICALS`, `_getShortEmissor`,
> `_getDevedorCanonical` em `templates/controlpanel.html:2385-2684`). **Não coberto por nenhum dos
> dois arquivos de referência** — ambos descrevem só capitalização genérica do nome do emissor; o
> código já resolve isso com um de-para curado, com prioridade sobre a capitalização genérica da
> seção 2. Aplicar esta etapa **antes** das fórmulas da seção 4 (é assim que o token `EMISSOR`
> daquelas fórmulas é obtido a partir do texto bruto do custodiante).

**Mecanismo:** lookup case-insensitive por substring contra uma lista ordenada de padrões — a
primeira entrada cujo padrão aparece no texto bruto vence (ordem por especificidade/frequência de
uso decrescente, já curada na tabela). Duas tabelas, usadas por famílias diferentes:

- **`_BANK_CANONICALS`** (~140 entradas) — usada para o **banco emissor** das famílias CDB, LCA,
  LCI, LCD, LF, CD, CCB, LC (seção 4.2 deste documento).
- **`_DEVEDOR_CANONICALS`** (~80 entradas) — usada para o **devedor/lastro/securitizadora** de
  CRI, CRA e Debênture (seções 4.3 e 4.4).

**Fallback** quando nenhum padrão casa: strip do prefixo `"BANCO "` e do sufixo `" S.A."`/`" S/A"`,
depois aplicar o title-case da seção 2 (incluindo o item 8, específico deste fallback).

**Origem dos dados**: o comentário no código referencia `data/parser_securities_data.json` (fonte
curada) e um gerador `_gen_js_maps.py`, dizendo para não editar as tabelas em JS à mão — **porém
nenhum dos dois arquivos existe hoje no repositório**. Na prática, as tabelas embutidas em
`controlpanel.html` são a única cópia sobrevivente e, até que a fonte JSON/gerador sejam
recriados, edições devem ser feitas diretamente nelas.

#### Exemplos (tabela completa em `controlpanel.html:2385-2655`, não reproduzida aqui)

| Texto bruto do custodiante | Canônico |
|---|---|
| `BANCO BTG PACTUAL S.A` | `BTG Pactual` |
| `BANCO BRADESCO S.A.` | `Bradesco` |
| `PETROBRAS` / `PETRÓLEO BRASILEIRO S.A` | `Petrobras` (sem acento — nome oficial da companhia) |
| `REDE D'OR` | `Rede D'Or` |
| `CAIXA ECONOMICA FEDERAL` | `Caixa Economica Federal` |

> ⚠️ **Correção aplicada em 2026-07-03**: a tabela `_DEVEDOR_CANONICALS` tinha `"Petrobrás"` (com
> acento) como canônico em duas entradas (padrões `PETROBRAS` e `PETRÓLEO BRASILEIRO S.A`) —
> corrigido para `"Petrobras"` (sem acento) diretamente no código.

---

## 4. Regras por tipo

### 4.1 Ativos offshore (`bond` com `type in (fixed,floating)`, `sovereignBonds`, `fund`/`stockEtf` com `currency != BRL`)

Transformações nesta ordem (fonte: arquivo 1 §2.1):
1. Ponto decimal → vírgula em taxas: `(\d+)\.(\d+)` → `\1,\2`
2. Remover `" - "` antes de taxa: `\s+-\s+(?=\d+[.,]\d+)` → ` `
3. Remover ISIN no final: `\s*-\s*[A-Z]{2}[A-Z0-9]{10}\s*$` → ``
4. Converter data para `dd/Mmm/yyyy` em **inglês** (`Jan..Dec`)

Exemplos: `Bombardier 6,00% 15-02-2028` → `Bombardier 6,00% 15/Feb/2028`;
`Ford Motor Company 4.346% 08/Dec/2026 - US345370CR99` → `Ford Motor Company 4,346% 08/Dec/2026`.

**Quando não há symbol pré-existente e sim campos discretos** (emissor/taxa/data separados — gap
não coberto pelo arquivo 1, complementado por REGRAS_BEEHUSNAME.md §4.13): montar
`{Emissor} {Taxa%} {DD/Mmm EN/AAAA}` (sem prefixo "Bond"), taxa com 2 casas decimais e vírgula,
depois aplicar as transformações acima. Ex.: `Petrobras 8,25% 15/Dec/2031`.

`ticker` = ISIN ou CUSIP direto (nunca sintético). Emissor em CAIXA ALTA na fonte → aplicar
capitalização (seção 2); já mixed-case → preservar.

### 4.2 Bonds onshore — CDB, LCA, LCI, LCD, CD, CCB, LC, LF, LIG, NP, CDCA (`securityType == 'bond'`, não offshore)

**Fórmula:** `[TIPO] [EMISSOR] [DD/Mmm/AAAA]` — **sem** taxa, **sem** indexador, **sem** rótulo
Pós/Pré-fixado. Exceção: se pré-fixado, inserir a palavra **"Pré"** antes da data.

Fonte: arquivo 1 §2.5 (decisão do usuário). `EMISSOR` é obtido primeiro via canonicalização
(seção 3), depois passo a passo:
1. Detectar pré-fixado: indexer == `"PRE"` OU nome contém "Pré-fixado"/"Prefixado"/"% Pré" (e NÃO
   contém "Pós-fixado").
2. Remover de forma robusta (nessa ordem — regexes completos em `beehusName-regras.md §2.5 Passo 2`):
   índice+spread (`IPCA+5,55%`), taxa%+índice (`104% CDI`), taxa%+Pré, rótulo "Pós-fixado", rótulo
   "Pré-fixado"/"Prefixado", indexador isolado remanescente, taxa isolada remanescente.
3. Se pré-fixado, inserir "Pré" antes da data (só se ainda não inserido pelo passo 2).
4. Limpar `" - "` residual e espaços múltiplos.

> Hifens que fazem parte do nome do emissor (`MG-050`, `D'Or`) são preservados — os regex usam
> word boundaries.

**LIG e NP** não têm emissor no nome (fonte: REGRAS_BEEHUSNAME.md §4.3 — não conflita, é um caso
que o arquivo 1 não exemplifica): fórmula `[TIPO] [DD/Mmm/AAAA]` (sem taxa, mesma lógica de
remoção acima aplicada só sobre tipo+data).

#### Exemplos (fonte arquivo 1, já validados)
| symbol / nome bruto | beehusName |
|---|---|
| `CDB Pan IPCA+5.55% 24/Mar/2027` | `CDB Pan 24/Mar/2027` |
| `CDB BTG Pactual 100.72%CDI 16/Jul/2029` | `CDB BTG Pactual 16/Jul/2029` |
| `CDB Banco Master Pré-fixado 22/Fev/2029` | `CDB Banco Master Pré 22/Fev/2029` |
| `LCI CEF 96,50%CDI - 15/Mar/2027` | `LCI CEF 15/Mar/2027` |
| `LCA Itaú CDI 21/Out/2027` | `LCA Itaú 21/Out/2027` |
| `CDCA Vamos IPCA + 7.91% 15/Set/2031` | `CDCA Vamos 15/Set/2031` |

#### LF — aplicado por extensão da mesma regra (não exemplificado no arquivo 1)
| symbol / nome bruto | beehusName |
|---|---|
| `LF Banco Omega Pós-fixado 20/Set/2028` | `LF Banco Omega 20/Set/2028` |
| `LF Banco XP Pré-fixado 15/Jul/2029` | `LF Banco XP Pré 15/Jul/2029` |

### 4.3 CRI, CRA (`securityType == 'bond'`, `type in (cri,cra)`)

Mesma fórmula e mesmo algoritmo da seção 4.2 — sem taxa, sem rótulo no pós-fixado, "Pré" no
pré-fixado. `EMISSOR` aqui é o **devedor/lastro**, não o banco custodiante — canonicalizado via
`_DEVEDOR_CANONICALS` (seção 3).

| symbol / nome bruto | beehusName |
|---|---|
| `CRI Allos 105%CDI - 16/Abr/2029` | `CRI Allos 16/Abr/2029` |
| `CRI Corp Log III Pós-fixado 15/Set/2028` | `CRI Corp Log III 15/Set/2028` |
| `CRI Solfacil Pré-fixado - 07/Jun/2032` | `CRI Solfacil Pré 07/Jun/2032` |
| `CRA SLC CDI+0,60% - 15/Jul/2031` | `CRA SLC 15/Jul/2031` |

### 4.4 Debênture / Debênture de infraestrutura (`securityType == 'bond'`, `type in (debenture,infrastructureDebenture)`)

Mesma fórmula/algoritmo da 4.2, com prefixo normalizado para **`DEB`** (não "Debênture" por
extenso): `Deb(?:[eê]nture)?\s+` → `DEB `. `EMISSOR` = devedor, canonicalizado via
`_DEVEDOR_CANONICALS` (seção 3).

| symbol / nome bruto | beehusName |
|---|---|
| `Debênture Vamos IPCA - 15/Out/2031` | `DEB Vamos 15/Out/2031` |
| `Debênture UHE São Simão IPCA - 15/Out/2036` | `DEB UHE São Simão 15/Out/2036` |
| `Debênture Engie Pré-fixado - 15/Ago/2029` | `DEB Engie Pré 15/Ago/2029` |
| `DEB Concessionária Rodovia MG-050 IPCA 15/Dez/2030` | `DEB Concessionária Rodovia MG-050 15/Dez/2030` |

`type` = `infrastructureDebenture` se o texto bruto contiver `INFRA`/`INCENT`/`INCENTIVADA`, senão
`debenture`.

> **Nota de dados legados** (REGRAS_BEEHUSNAME.md §4.5): a base tem debêntures antigas cadastradas
> com taxa E rótulo juntos (`"... IPCA + 5.71% Pós-fixado 15/Out/2031"`) — é o padrão que o código
> atual (`_buildDebentureBeehusName`) ainda gera. Não é o padrão a seguir para ativos **novos**;
> não precisa ser corrigido retroativamente, só não replicar daqui pra frente.

### 4.5 Ações, FIIs e ETFs brasileiros (`securityType == 'stockEtf'`, não offshore)

**Fórmula:** `<TICKER> - <nome limpo> <sufixo>` — ticker no início.
1. Remover o ticker do meio do nome, se aparecer lá.
2. `"Nome - SUFIXO"` inline → mover sufixo pro final (`"Kinea High Yield - FII"` → `"Kinea High
   Yield FII"`).
3. `"FII"` no início → mover pro final.
4. Sufixos reconhecidos (`FII`,`ETF`,`PN`,`ON`,`BDR`) vão para o final, maiúsculos.
5. `FIDC`,`FIP`,`FIAGRO` mantêm a posição original (não mover).

| symbol | ticker | beehusName |
|---|---|---|
| `Bradesco - PN` | `BBDC4` | `BBDC4 - Bradesco PN` |
| `Kinea High Yield - FII` | `KNHY11` | `KNHY11 - Kinea High Yield FII` |
| `Kinea Crédito Estruturado FIDC` | `KNCE11` | `KNCE11 - Kinea Crédito Estruturado FIDC` |

Campos técnicos: `exchange = "BVMF"`, `ticker = "BR:{TICKER_B3}"`,
`subscription/redemptionSettlementDays = 2`, `subscription/redemptionNAVDays = 0`.

> ⚠️ **Correção (2026-07-03), decisão do usuário**: REGRAS_BEEHUSNAME.md §4.11 dizia que `country`
> e `feederIds = [{"id":"BR:{TICKER}","feederName":"CD"}]` eram obrigatórios. O cache real
> (`data/securities_cache.json`) mostra que os 1.243 registros `stockEtf` existentes (BR +
> internacional) **não têm campo `country` nem qualquer campo de feeder** no schema observado —
> `country` vazio em 100% dos casos, nenhuma chave parecida com `feederIds`/`feederName` existe.
> Decisão: **não incluir `country` nem `feederIds`/`feederName` na aba de criação de `stockEtf`.**

### 4.6 Ações/ETFs internacionais (`securityType == 'stockEtf'`, offshore)

**Fórmula:** `<Nome oficial> - <TICKER>` — ticker no final. Nome oficial sem sufixos societários
(`Inc.`,`Corp.`,`Ltd.`,`plc`,`N.V.`). Buscar `"TICKER equity"` para obter o nome quando não for
amplamente conhecido.

| ticker | beehusName |
|---|---|
| `PFE` | `Pfizer - PFE` |
| `EEM` | `iShares MSCI Emerging Markets - EEM` |
| `META` | `Meta Platforms - META` |

Campos técnicos (REGRAS_BEEHUSNAME.md §4.12, mesma correção da seção 4.5 sobre `country`/
`feederIds`): `exchange` = código MIC (`XNYS`,`XNAS`... nunca o nome da bolsa),
`ticker = "US:{SYMBOL}"`, mesmos defaults de settlement/NAV da seção 4.5.

### 4.7 Títulos públicos brasileiros (`securityType == 'brazilianGovernmentBond'`)

**Fórmula:** manter o nome (`LFT`,`LTN`,`NTN-B`,`NTN-C`), sempre no formato `TIPO Mmm/AAAA` —
**nunca incluir o dia**, mesmo quando `maturityDate` tem o dia real preenchido.

> ⚠️ **Correção (2026-07-03), decisão do usuário**: a versão anterior desta regra (baseada num
> exemplo hipotético do `beehusName-regras.md`) mandava incluir o dia quando `maturityDate`
> estivesse disponível. O cache real (88 ativos `brazilianGovernmentBond`) mostra que **94% deles
> (83/88)** usam só `Mmm/AAAA` no `beehusName`, mesmo quando `maturityDate` tem o dia certo (ex.:
> `beehusName="NTN-B Ago/2028"` com `maturityDate="2028-08-15"`). Revertido para bater com o
> padrão real observado — só 5/88 (legado) mostravam o dia.

| symbol | maturityDate | beehusName |
|---|---|---|
| `LFT MAR-27` | `2027-03-01` | `LFT Mar/2027` |
| `NTN-B AGO-26` | `2026-08-15` | `NTN-B Ago/2026` |
| `LTN JAN-26` | *(ausente)* | `LTN Jan/2026` |

Campos técnicos (REGRAS_BEEHUSNAME.md §4.10, sem conflito): `ticker = null` sempre; `isIn` é o
campo do ID principal (`mainId` é derivado dele); `selicCode` separado, pode ficar vazio;
`yield`/`indexer`/`indexerPercentual` = `null` (taxa não é modelada no título público). Nota: no
cache real, `maturityDate` em si também nem sempre está preenchida (vários registros com
`maturityDate=""` mesmo tendo `beehusName` válido) — não depender dela estar sempre presente.

### 4.8 COE / produtos estruturados (`securityType == 'otc'`, `type == 'structuredNote'` na documentação; `'others'` observado na prática — ver nota)

**Fórmula** (arquivo 1 §2.6): remover só o `" - "` imediatamente antes da data de vencimento — o
restante do nome (estratégia, ativo-referência, banco emissor) é preservado integralmente. Aplicar
também a normalização de data da seção 5 abaixo.

| symbol / nome bruto | beehusName |
|---|---|
| `COE XP 1X1 Índice Ações Globais - 16/Abr/2027` | `COE XP 1X1 Índice Ações Globais 16/Abr/2027` |
| `COE XP 1X1 MAN MULTIMERCADO 16/04/27` | `COE XP 1X1 MAN MULTIMERCADO 16/Abr/2027` |

Ambos os exemplos acima foram confirmados batendo exatamente com dados reais de cadastro em
`reference/Cadastros em lote v17.xlsm` (aba `sec-otc`).

**Ticker sintético** (gap — só em REGRAS_BEEHUSNAME.md §4.7): `COE{EMISSOR}{ABREV_PRODUTO}
{ABREV_ESTRATEGIA}{DDMMAAAA}` (ex. `COEXPBOLSAAMERBD16062026`). Se colidir (mesmo vencimento,
captações diferentes), desambiguar acrescentando a data de emissão como sufixo ao ticker e ao nome.
Nos exemplos reais observados, o ticker é obtido de forma mais simples: o próprio `beehusName`
com espaços, `" - "` e acentuação removidos, e a data sem barras (ex. `"COE XP 1X1 Índice Ações
Globais 16/Abr/2027"` → `"COEXP1X1ÍNDICEAÇÕESGLOBAIS16042027"`).

**Campos de settlement/NAV**: `subscriptionNAVDays = subscriptionSettlementDays =
redemptionNAVDays = redemptionSettlementDays = 999` (não `0`) — confirmado em 5 exemplos reais do
Excel e faz sentido: COE tipicamente não permite resgate antecipado. Isso substitui o default
genérico de `0` da seção 6.3 para este tipo especificamente.

> ⚠️ **Conflito aberto, ainda não resolvido**: `REGRAS_BEEHUSNAME.md`/`REGRAS_CAMPOS_CADASTRO.md`
> afirmam que `type` correto é `"structuredNote"` (e que `"coe"` seria inválido). Porém os 5
> exemplos reais observados em `Cadastros em lote v17.xlsm` usam `type = "others"` para COE, nunca
> `"structuredNote"`. O cache de securities (`data/securities_cache.json`) não tem o campo `type`
> preenchido em nenhum registro, então não dá pra confirmar por ali qual enum a API realmente
> aceita/espera hoje. Decisão pendente do usuário: seguir a documentação (`structuredNote`) ou o
> padrão real observado (`others`).

### 4.9 Compromissada / repo (`securityType == 'otc'`, `type == 'brazilianRepo'`) — uso temporário

**Decisão do usuário (2026-07-03)**: enquanto não existir uma aba dedicada para `securityType =
"brazilianRepo"` (que o catálogo real confirma ser uma categoria própria, com 174 ativos reais —
ver conversa anterior), compromissadas continuam sendo cadastradas na aba `otc`. Quando o texto
bruto do ativo permitir identificar com segurança que é uma compromissada, pré-selecionar
`type = "brazilianRepo"` no dropdown; caso contrário, deixar em branco para o usuário escolher.

**Não coberto pelo arquivo 1** — regra de nome vem inteira de REGRAS_BEEHUSNAME.md §4.6 (gap-fill,
sem conflito):

```
Fórmula:  Compromissada - {pct}% CDI - {código}
```
`{pct}` sem casas decimais se inteiro (`90`), com casas se fracionário (`90.5`); espaço antes/depois
de "CDI"; `{código}` = código do ativo subjacente.

Exemplo: `Compromissada - 90% CDI - CRA024006N4`.

Campos obrigatórios: `currency="BRL"`, `country="BR"`, `maturityDate="2099-01-01"` (placeholder,
API não aceita null), `indexer="CDI"`, `indexerPercentual`=% do CDI, `yield=0`.

**Ticker**: `COMP{pct}CDI{código}` — nunca usar o código bruto do custodiante como `ticker`
(pode colidir com o mesmo código em taxas diferentes).

> ⚠️ Dados legados: compromissadas antigas aparecem com combinações inconsistentes de
> `securityType`/`type` (`brazilianRepo`/`ntnb`, `otc`/`others`, `bond`/`cra`,
> `privateMarket`/null). A regra acima vale só para cadastros **novos**.

### 4.10 Fundos onshore (`securityType == 'brazilianFund'`)

Aplicar as abreviações abaixo, nesta ordem (mais específico primeiro), depois a capitalização
(seção 2). Fonte: arquivo 1 §2.7.

| Padrão no nome | Substituição |
|---|---|
| `FICFIP` | `FIC FIP` |
| `FICFI`/`FICFIF`/outros `FICFI*` | `FIC` |
| `Crédito Privado`/`Créd Priv` | `CP` |
| `FIC de FIRF`/`FIC FIRF` | `FIC RF` |
| `Multimercado` | `MM` |
| `Mult` (isolado) | `MM` |
| `Multestratégia` (typo) | `Multiestratégia` |
| `Fip`/`fip` | `FIP` |
| `Btg` | `BTG` |

> `FICFIP` deve ser aplicado **antes** de `FICFI*` para preservar a informação de que é FIP.

Exemplos: `Angá Crédito Estruturado FICFI MM CP` → `Angá Crédito Estruturado FIC MM CP`;
`Spectra VI Latam Pro FICFIP Multiestratégia` → `Spectra VI Latam Pro FIC FIP Multiestratégia`.

**Complemento** (REGRAS_BEEHUSNAME.md §4.8, sem conflito — cobre proveniência e campos técnicos que
o arquivo 1 não aborda):
- Nome oficial deve vir do cadastro CVM (`DENOM_SOCIAL` do `cad_fi.csv`) ou lâmina/ANBIMA — não
  inventar; aplicar a capitalização (seção 2) sobre o nome oficial (normalmente todo em maiúsculas
  no cadastro CVM).
- Se o fundo for classe/cotas de outro fundo (feeder): `feederIds = [{"id":"<CNPJ>",
  "feederName":"CD"}]`.
- `redemptionNAVDays`/`redemptionSettlementDays`: fundo fechado (FII/FIP/FIDC ou `CONDOM =
  "Fechado"` na CVM) → `999`/`999`; caso contrário, usar o prazo de conversão/pagamento da lâmina.

### 4.11 Fundos offshore e demais tipos (`fund`, `sovereignBonds` fora da regra de data,
`privateMarket`, `realAssets`)

**Fórmula:** copiar o nome oficial sem alteração adicional (fonte: arquivo 1 §2.8). Para fundos
offshore, nome oficial em inglês, sem sufixos societários redundantes, sem prefixo "Fund".

Exemplo: `JPM Global Bond Opportunities A Accumulating USD`.

Campos técnicos (REGRAS_BEEHUSNAME.md §4.9, sem conflito): `currency="USD"`, `ticker=null`,
`isIn` = ISIN (12 caracteres) — **campo que define o `mainId`, nunca setar `mainId` direto**;
`feederIds=[{"id":"XX:ISIN","feederName":"CD"}]` (XX = 2 primeiras letras do ISIN = país);
`subscriptionNAVDays=1, subscriptionSettlementDays=3` (mesmo para redemption).

### 4.12 Opções (`securityType == 'options'`, `type in (call,put)`)

> Fonte: dado real observado em `reference/Cadastros em lote v17.xlsm` (aba `sec-options`) —
> **não coberto por nenhum dos dois arquivos de regra de nomenclatura** (`beehusName-regras.md`,
> `REGRAS_BEEHUSNAME.md`). Era uma lacuna total até este ponto; a fórmula abaixo foi reconstruída
> a partir dos únicos 2 exemplos reais disponíveis — vale tratar como preliminar até termos mais
> exemplos, principalmente para confirmar o idioma do mês em datas não-ambíguas (`Jan`/`Fev` vs
> `Jan`/`Feb`).

**Fórmula:** `{Call|Put} {ATIVO-OBJETO} @{STRIKE} {DD/Mmm/AAAA}`

| symbol / dado bruto | beehusName | ticker |
|---|---|---|
| Put, SPY, strike 660, venc. 31/03/2026 | `Put SPY @660 31/Mar/2026` | `Put_Spy_660_31032026` |
| Put, SPY, strike 630, venc. 31/03/2026 | `Put Spy @630 31/Mar/2026` | `Put_Spy_630_31032026` |

Notas observadas nos exemplos reais:
- `{Call|Put}` capitalizado só a primeira letra (Title Case), não `ALL CAPS`.
- O ativo-objeto aparece em `ALL CAPS` no `beehusName` (`SPY`) mas em Title Case no `ticker`
  (`Spy`) no primeiro exemplo — inconsistência observada nos próprios dados reais, não uma regra
  confirmada; ao implementar, preferir manter o ativo-objeto em `ALL CAPS` em ambos os campos por
  consistência com a seção 2 (tickers/mainId sempre maiúsculos), e usar Title Case só se aparecer
  mais evidência de que é o padrão real.
- `strike` e `underlying` como campos **dedicados** do schema ficam vazios — a informação só
  aparece embutida no `beehusName`/`ticker` (confirma a ressalva já registrada em
  `REGRAS_CAMPOS_CADASTRO.md §4.14`).
- `type` (`call`/`put`) — pré-selecionar no dropdown detectando a palavra `CALL`/`PUT` no nome
  bruto do ativo (decisão de UI do usuário, 2026-07-03); deixar em branco se não detectar.
- Campos técnicos observados: `currency="USD"`, `exchange="NYSE"` (exemplos eram equities
  americanas — não há exemplo real de opção sobre ativo BR/B3 ainda; escopo a confirmar),
  `maturityDate` = data de expiração, settlement/NAV = `0`/`1`/`0`/`1` nos exemplos.

---

## 5. Regra de data e limpeza (ativos nacionais com vencimento no nome)

Aplica-se a: bonds onshore (4.2-4.4), título público (4.7), COE (4.8), compromissada (4.9).

**Passo A** — converter data numérica para `dd/Mmm/yyyy` em **português**:
- `DD/MM/YY` (ano 2 dígitos) → `DD/Mmm/20YY`
- `DD/MM/YYYY` (ano 4 dígitos) → `DD/Mmm/YYYY`

Tabela de meses PT: `Jan Fev Mar Abr Mai Jun Jul Ago Set Out Nov Dez`.
Tabela de meses EN (só para ativos offshore, seção 4.1): `Jan Feb Mar Apr May Jun Jul Aug Sep Oct
Nov Dec`.

**Passo B** — remover tudo que vier depois da data já formatada (espaços residuais, apóstrofes,
algarismos romanos soltos, taxas/indexadores remanescentes):
regex `(\d{1,2}/[A-Za-z]{3}/\d{4})\s*.*$` → manter só o grupo 1.

| original | final |
|---|---|
| `LFSN BRB 10/08/29` | `LFSN BRB 10/Ago/2029` |
| `NTN-B 01/Mai/2055 IPCA + 5,72%` | `NTN-B 01/Mai/2055` |
| `NTN-F 01/Jan/2031 11,31%'` | `NTN-F 01/Jan/2031` |

Quando não há data de vencimento real (ex. compromissada): usar placeholder `"2099-01-01"` no
campo `maturityDate` (API não aceita `null`), mas **isso não aparece** no `beehusName`.

---

## 6. Campos correlatos (gap-fill — só em REGRAS_BEEHUSNAME.md, sem conflito com o arquivo 1)

Necessários para a criação do ativo além do `beehusName` em si:

### 6.1 `indexer`/`yield`/`indexerPercentual` (renda fixa BR)

| Modalidade | `indexer` | `yield` | `indexerPercentual` |
|---|---|---|---|
| % do CDI | `"CDI"` | `0` | percentual do CDI (`100`,`84`,`95`) |
| CDI + spread | `"CDI"` | spread numérico | `100` |
| IPCA + spread | `"IPCA"` | spread numérico | `100` |
| Pré-fixado | `"PRE"` | taxa numérica | `null` |
| Título público BR | `null` | `null` | `null` |

### 6.2 Ticker sintético (só quando não há código oficial do custodiante — preferir sempre CETIP/ISIN/CUSIP/Selic reais)

| Família | Padrão | Exemplo |
|---|---|---|
| CDB/LCA/LCI/LF/LIG/NP/CRI/CRA sem CETIP | `{TIPO}{EMISSOR_SEM_ESPACO}{TAXA}{INDEXADOR}{MMM}{AAAA}` | `LIG1050PRE01072026` |
| Compromissada | `COMP{pct}CDI{código}` | `COMP90CDICRA024006N4` |
| COE | `COE{EMISSOR}{ABREV_PRODUTO}{ABREV_ESTRATEGIA}{DDMMAAAA}` | `COEXPBOLSAAMERBD16062026` |
| Fundo offshore (feeder) | `{ISIN[:2]}:{ISIN}` | `LU:LU1103307317` |
| Ação/ETF USD | `US:{SYMBOL}` | `US:AAPL` |
| Ação/FII BR | `BR:{TICKER_B3}` | `BR:VGRI11` |
| Bond USD | ISIN/CUSIP direto (não sintético) | `US037833EN61` |
| Título público BR | `ticker=null`; usar `isIn` (+ `selicCode` opcional) | — |

### 6.3 Defaults obrigatórios (API rejeita `null`)

```
subscriptionSettlementDays = 0   (bonds BR / fundos, salvo regra específica acima)
subscriptionNAVDays        = 0
redemptionNAVDays          = 0
redemptionSettlementDays   = 0
maturityDate = "2099-01-01"      quando não há vencimento real (ex. compromissada)
```

### 6.4 Erros conhecidos da API Beehus

| Erro | Causa | Solução |
|---|---|---|
| `Esperado (...) e recebido (null) no campo body.type` | `type=null` | sempre definir `type` |
| `Esperado (string) e recebido (null) no campo body.maturityDate` | `maturityDate=null` | usar `"2099-01-01"` |
| `Selecione o subtipo do ativo` | `securityType` errado | compromissada=`otc`/`brazilianRepo`; COE=`otc`/`structuredNote` |
| `Required` (4×) em bond/brazilianGovernmentBond | faltam campos de settlement | setar os 4 campos da seção 6.3 |
| `Invalid enum value... 'coe'` | `type` inválido | usar `structuredNote` |
| `Invalid enum value... 'compromissada'` | `type` inválido | usar `brazilianRepo` |
| `O ID principal é obrigatório` (bond sem ticker) | `mainId` ausente | gerar ticker sintético (6.2) |
| `O ID principal é obrigatório` (brazilianGovernmentBond) | código no campo errado | código vai em `selicCode`, não em `ticker`; `mainId` deriva de `isIn` |
| `O ID principal é obrigatório` (fundo offshore) | `isIn` vazio | preencher `isIn` |
| `Invalid enum value... 'NYSE'` | exchange em formato errado | usar código MIC (`"XNYS"`) |
| Ticker com aspas literais no campo | dado colado com aspas incluídas | remover as aspas |

---

## 7. Checklist de decisão

1. Offshore? (`currency != BRL` ou `type in (fixed,floating)`) ou `sovereignBonds` → seção 4.1.
2. `stockEtf` não offshore → seção 4.5. `stockEtf` offshore → seção 4.6.
3. `brazilianGovernmentBond` → seção 4.7.
4. `bond` não offshore (CDB/LCA/LCI/LCD/LC/CCB/LF/LIG/NP/CDCA) → seção 4.2 (emissor via
   canonicalização da seção 3).
5. `bond` CRI/CRA → seção 4.3. `bond` Debênture → seção 4.4 (devedor via canonicalização da
   seção 3).
6. `otc`/`structuredNote` (COE) → seção 4.8. `otc`/`brazilianRepo` (compromissada) → seção 4.9.
7. `brazilianFund` → seção 4.10.
8. Demais (`fund` offshore, `privateMarket`, `realAssets`) → seção 4.11.
9. Sempre por último: capitalização geral (seção 2), exceto offshore (mantém casing do emissor de
   origem, só aplica as transformações da seção 4.1).
10. Preencher campos correlatos (seção 6) e conferir contra a tabela de erros (6.4) antes de
    enviar.

---

## 8. Próximo passo (implementação — ainda não feito)

O código atual (`_parseUnprocessedId`, `_parseCriCraDeb` e as funções `_build*BeehusName` em
`templates/controlpanel.html`) **ainda gera taxa/rótulo** para CDB/LCA/LCI/CRI/CRA/Debênture,
divergindo desta regra final nos 4 pontos da tabela da seção 0. Precisa ser atualizado para
refletir as seções 4.2-4.4 antes de ser usado na tela de criação de ativos sem match. A
canonicalização de emissor/devedor (seção 3) e a correção do acento de "Petrobras" já foram
aplicadas diretamente no código em 2026-07-03.

Itens de **parsing/tokenização** (como interpretar a gramática do texto bruto do custodiante antes
de chegar nos campos emissor/taxa/indexador/data usados pelas fórmulas acima) foram documentados à
parte em `reference/parsing-unprocessedId-notas.md` — não fazem parte da regra de nomenclatura em
si.