# MB Onboarding — Ingestão dos relatórios EMR (XP MB)

> **Objetivo.** Esta spec descreve como ler os relatórios mensais `.xlsm` da carteira **EMR**
> (instituição **XP MB**) e gerar o output estruturado de **securities** (posições) e
> **transactions** (movimentações) para a plataforma de conciliação.
>
> Use este documento como referência para **analisar** e **gerar output** a partir desses arquivos.

---

## 1. Arquivo de origem

| Item | Valor |
|---|---|
| Padrão de nome | `EMR_<AAAAMM>.xlsm` (ex.: `EMR_202507.xlsm` = referência **jul/2025**) |
| Local (exemplo) | `data/.tmp/EMR_202507.xlsm` |
| Formato | Excel com macros (`.xlsm`) — **contém VBA** (`vbaProject.bin`) |
| Nº de abas | 14 |
| Leitura recomendada | `openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)` |

> ⚠️ **Caminho com espaços / OneDrive.** O projeto vive numa pasta do OneDrive com espaços no
> caminho. Sempre passe o caminho entre aspas e não dependa de reload automático.

### Período de referência
O período é declarado na aba `Controle`:

| Campo | Célula | Valor (202507) |
|---|---|---|
| Início | `Controle!C2` | `2025-06-30` |
| Final  | `Controle!C3` | `2025-07-31` |

`Início` = fim do mês anterior (base de `PosAnterior`); `Final` = data da posição (base de `Pos`).

---

## 2. Abas — o que usar

Apenas **duas** abas são fonte de ingestão. As demais são relatórios agregados/derivados.

| Aba | Papel | Usar? |
|---|---|---|
| **DataWM** | Posições por security (com mês anterior e mês atual) | ✅ **fonte de `securities`** |
| **MovWM** | Movimentações / transações | ✅ **fonte de `transactions`** |
| ReportWM | Relatório agregado (resumo por banco, pivots por classe/indexador/veículo, bloco "Ativos") | ❌ derivado |
| DataWM* / ReportTIR / BancoTIR / ClasseTIR / QuadroRet / Graficos / CotaIndice / HistValores / Tabs / Draft / Apoio | Cálculos de TIR, gráficos, índices, históricos, parâmetros | ❌ não-fonte |

> 🔴 **Atenção (erro comum):** o **ReportWM NÃO é a fonte de securities**. Ele é um relatório
> agregado de múltiplos blocos. O bloco "Ativos" (r687) é por security mas só tem **Posição do
> mês atual** (sem `posAnterior`, sem `date`, sem código-wallet/security separados) e ainda
> **duplica** ativos entre veículos (a soma de Posição dá ~2× o patrimônio). Os campos
> `date / wallet / security / posAnterior / pos` por ativo estão na **DataWM**.

---

## 3. DataWM → `securities`

- **Cabeçalho:** linha **4**. **Dados:** a partir da linha **5**.
- **Referência 202507:** 149 linhas, todas `Report = EMR`, `Date = 2025-07-31`.

### Mapeamento de colunas

| Campo destino | Coluna origem | Letra | Tipo | Observação |
|---|---|---|---|---|
| `positionDate` | `Date` | **C** | datetime | 100% datetime (sem formato misto). Todas = data Final do período |
| `wallet` | `Report` | **D** | string | Sempre `EMR` neste arquivo |
| `entity` | `Banco` | **F** | string | `XP MB`, `XP MB 2`..`XP MB 5`, `Safra`, `Safra 2` |
| `security` | `Código` | **E** | string | Nome descritivo do ativo (ex.: `CDCA BTG PRE 11,69% 16/07/29`). **Não há ISIN/ticker dedicado** |
| `balancePrev` (posAnterior) | `PosAnterior` | **M** | float | Saldo do mês anterior |
| `balance` (pos) | `Pos` | **N** | float | Saldo do mês atual |

Colunas auxiliares disponíveis (não obrigatórias): `Cod1`(A), `DataF`(B), `Asset`(G), `AssetNovo`(H),
`Ativo`(I), `Índice`(J), `Taxa`(K), `D_Vcto`(L = vencimento), `Classe`(O), `Indexador`(P),
`Sub Classe`(Q), `Tipo`(R), `Veículo`(S), `CNPJ`(T), `Liquidação`(U), `Gross`(V), `TIR`(W),
`Rendimento`(Z), `Ingressos`(AB), `Retiradas`(AC). As colunas a partir de `AF` são valores diários
da posição ao longo do mês (uma coluna por dia).

### Sanidade (202507)
- `Pos` (N) soma = **16.627.343,55** → **deve bater** com o patrimônio total do relatório.
- `PosAnterior` (M) soma = **16.566.676,54**.
- `Código` (E) ausente em **0** linhas.
- Distribuição por `entity`: Safra 45, XP MB 2 = 24, XP MB 3 = 24, XP MB = 13, XP MB 5 = 13, Safra 2 = 24, XP MB 4 = 6.

---

## 4. MovWM → `transactions`

- **Cabeçalho:** linha **1**. **Dados:** a partir da linha **2**.
- **Referência 202507:** 323 lançamentos, todos `Report = EMR`.

### Mapeamento de colunas

| Campo destino | Coluna origem | Letra | Tipo | Observação |
|---|---|---|---|---|
| `liquidationDate` | `Data` | **C** | datetime **ou** string | ⚠️ **formato misto** — ver §5 |
| `wallet` | `Report` | **D** | string | Sempre `EMR` |
| `entity` | `Banco` | **E** | string | `XP MB`, `XP MB 2`..`XP MB 5`, `Safra`, `Safra 2` |
| `description` | `Descrição` | **H** | string | Texto livre do extrato |
| `balance` (valor) | `VALOR` | **I** | float | **Com sinal**: `+` crédito / `−` débito |

Colunas auxiliares: `cod1`(A), `cod2`(B), `Cod. Banco`(F), `Código`(G = ativo/CNPJ/`AÇÕES`),
`Asset`(J), `Descr2`(K), `Itaú`(L), `Tratamento`(M), `aa`(N).

### Sanidade (202507)
- `VALOR` (I): soma líquida = **69.770,79** · 145 créditos (+404.502,10) · 178 débitos (−334.731,31).
- Faixa: −118.622,66 a +111.591,15.
- Distribuição por `entity`: XP MB 4 = 222, XP MB 2 = 32, XP MB 5 = 31, Safra = 19, XP MB 3 = 8, Safra 2 = 8, XP MB = 3.

---

## 5. Regras de normalização

1. **Datas do MovWM (`Data`, col C) — formato misto.**
   No 202507: **32 linhas** são `datetime` e **291 linhas** são **texto** `"dd/mm/aaaa"`.
   Regra de parse:
   ```python
   def parse_mov_date(v):
       if isinstance(v, datetime): return v.date()
       return datetime.strptime(v.strip(), "%d/%m/%Y").date()  # texto pt-BR
   ```
   (As datas da DataWM **não** têm esse problema — são todas `datetime`.)

2. **Locale pt-BR.** Números no arquivo usam vírgula decimal só na exibição;
   `openpyxl` retorna `float` nativo. Ao **gerar output para exibição**, formatar em pt-BR
   (vírgula decimal, ponto de milhar).

3. **Sinal de `VALOR`.** Não usar `abs()`. Crédito (+) e débito (−) devem ser preservados;
   a soma líquida é uma checagem de sanidade.

4. **`wallet` vs `entity`.** Nestes arquivos `wallet = EMR` (a carteira/report) e
   `entity = Banco` (XP MB / Safra + sufixo da conta). Não confundir os dois.

5. **Identidade da security.** O ativo é identificado pelo **nome descritivo** (`Código`/E na DataWM),
   não por ISIN. O casamento com o cadastro da plataforma deve ser por nome (ver §7).

---

## 6. Output

**Fluxo (com planilha auxiliar de-para):**

```
# 1. gera o template de-para já pré-preenchido com o que resolve via Mongo
python scripts/mb_aux.py        data/.tmp/EMR_202507.xlsm            # -> EMR_202507_aux.xlsx
# 1b. preenche o PU automaticamente (securityPrices na positionDate do mês)
python scripts/mb_fill_pu.py    "data/.tmp"                          # preenche aba PU de todos os _aux
# 2. >>> usuário preenche o aux: walletId, securityId faltantes (PU dos não-identificados) <<<
# 3. gera positions/transactions usando o aux como fonte de verdade (sem Mongo)
python scripts/mb_generate.py   data/.tmp/EMR_202507.xlsm --aux data/.tmp/EMR_202507_aux.xlsx
# 4. converte positions p/ o formato de upload da Beehus
python scripts/mb_to_beehus.py  data/.tmp/EMR_202507_positions.json
```

> Sem `--aux`, `mb_generate.py` consulta o Mongo e auto-resolve (walletId fica como
> rótulo placeholder, PU/Quant = 0). Com `--aux`, a planilha manda e o Mongo não é tocado.

### Planilha auxiliar (de-para) — `scripts/mb_aux.py`

XLSX com 3 abas (o código lê colunas **A** e **B** de cada uma):

| Aba | A (chave) | B (valor) | Colunas de referência |
|---|---|---|---|
| `Wallets` | `walletName` (rótulo Excel) | `walletId` | — |
| `Securities` | `ativo` (Código/Asset) | `securityId` | beehusName, status |
| `PU` | `securityId` | `PU` | beehusName, ativo |

Pré-preenchido via Mongo: `securityId` por CNPJ/ticker exato (status `revisar` = usuário completa).
`walletId` nasce vazio. **`PU` é preenchido automaticamente** por `scripts/mb_fill_pu.py`
(busca `securityPrices.historyPrice[].value` na positionDate do mês — data exata, ou último
preço útil ≤ data) para todos os securityId já identificados; o restante o usuário completa. No `mb_generate --aux`: `walletId`←Wallets · `securityId`←Securities
(positions por `ativo`=Código, transactions por `ativo`=Asset) · `PU`←PU(securityId) e
**`Quant = SaldoBruto ÷ PU`**. Linhas com valor vazio no aux mantêm o fallback (rótulo / sem PU).

### `mb_generate.py` — 4 arquivos ao lado do `.xlsm` (`<stem>_…`):

| Arquivo | Conteúdo |
|---|---|
| `*_positions.json` | formato **unprocessedSecurities** (FILE_GENERATION.md #3) |
| `*_transactions.json` | formato **transactions** (FILE_GENERATION.md #1) |
| `*_positions.xlsx` | idem + colunas de revisão (`resolvedSecurityId`, `matchStatus`, `posAnterior`, classe/tipo) |
| `*_transactions.xlsx` | idem + colunas de revisão (`matchStatus`, `typeReason`) |

### Constantes resolvidas via Mongo (read-only, jun/2026)
- `companyId` = `10000000000000` (**Blue3**)
- `entityId`: XP = `67cf6a5c71f5e8c88f760505` · Safra = `67c0a80471f5e8c88f7604a1`
- `currencyId` = `BRL`

### DataWM → `unprocessedSecurities`
`date`←Date · `walletId`←**Banco (PLACEHOLDER = rótulo Excel)** · `security`←Código (nome; upstream resolve)
· `balance`←Pos · `currencyId`=BRL · `cashAccount`=Sim p/ linhas de caixa.
**`quantity`/`pu` = `null`** (não existem na DataWM).

### MovWM → `transactions`
`liquidationDate`/`operationDate`←Data (normalizada) · `walletId`←**Banco (PLACEHOLDER)** ·
`entityId`←XP/Safra · `balance`←VALOR (com sinal) · `description`←Descrição ·
`securityId`←match exato CNPJ/ticker (omitido se não casar/caixa) ·
`beehusTransactionType`←heurística de palavra-chave (`""` quando incerto) ·
`inputType`="sheets" · `hide`=false.

### Resolução de security (rascunho = só identificador EXATO)
- Fundos → `cache.lookup('taxId'|'mainId', <CNPJ>)`.
- CRA/CRI/DEB/FII com código → `cache.lookup('ticker'|'mainId'|'selicCode'|'isIn', <cod>)`.
- Bonds só-nome (CDCA/CDB/NTN-B…) → **não resolvidos** (`matchStatus='revisar'`); usar `security_matcher` fuzzy numa 2ª passada.

### Sanidade (validada em 202507)
- positions: 149 linhas, Σ`balance` = 16.627.343,55 (= patrimônio). match: ticker 45 · cnpj 7 · caixa 14 · **revisar 83**.
- transactions: 323 linhas, Σ`balance` = 69.770,79. match: ticker 13 · caixa 43 · **revisar 267**.
  tipos: dividend 112 · gainsExpenses 101 · buySell 69 · withdrawalDeposit 1 · **vazio 40**.

### Formato de upload da Beehus — positions (`scripts/mb_to_beehus.py`)

```
python scripts/mb_to_beehus.py data/.tmp/EMR_202507_positions.json [--split]
```

Converte o `*_positions.json` no XLSX que a Beehus ingere — **idêntico** a
`pages/excecoes._build_xlsx` (sheet `Posicoes`), enviado via
`POST /beehus/financial/positions/unprocessed-security-positions/file`
(multipart, `companyId` no **form**, não como coluna).

| Coluna | Valor | Obs |
|---|---|---|
| `Data` | positionDate (`2025-07-31`) | |
| `Carteira` | **walletId** | rascunho usa o rótulo placeholder |
| `Ativo` | identificador → vira `unprocessedId` upstream | Código/nome (bond) · CNPJ (fundo) · código (CRA/CRI/DEB) |
| `Quant` | `0` | **DataWM não tem quantidade** |
| `PU` | `0` | **DataWM não tem PU** |
| `SaldoBruto` | balance (Pos) | |
| `Caixa` | `Sim`/`Não` | linhas de caixa = `Sim`, `Ativo`="Caixa XP"/"Caixa" |
| `Moeda` | `BRL` | |

- Sem `--split`: 1 workbook com todas as carteiras empilhadas (validação).
- Com `--split`: 1 XLSX por `Carteira` em `<stem>_beehus/` (como no upload real, 1 POST por carteira).

### Lote — `scripts/mb_batch.py`

```
python scripts/mb_batch.py "data/.tmp"                  # todos os EMR_*.xlsm legíveis
python scripts/mb_batch.py "data/.tmp" --password SENHA # inclui os criptografados
```

Carrega o SecurityCache do Mongo **uma vez** e roda o pipeline completo (aux +
positions/transactions + positions_beehus) por arquivo. Lida com dois formatos:
- **OOXML normal** (`PK…`) — lido direto.
- **xlsx criptografado** (OLE2 `D0CF11E0`, streams `EncryptedPackage`/`EncryptionInfo`)
  — descriptografado em memória via `msoffcrypto-tool` com `--password` (senha vazia e
  `VelvetSweatshop` **não** funcionam; é senha real).

Layout DataWM/MovWM confirmado estável entre meses (em 202511 o portfólio cresceu:
+Safra 3–7, 12 carteiras). Dependências novas: `xlrd`*, `olefile`, `msoffcrypto-tool`.
*(xlrd não lê estes arquivos — são OOXML criptografado, não .xls BIFF.)*

### Consolidado (1 arquivo, todos os meses) — `scripts/mb_consolidate.py`

```
python scripts/mb_consolidate.py "data/.tmp"   # -> EMR_consolidado.xlsx
```

Empilha todos os `EMR_*_positions.json`/`_transactions.json` (exclui o próprio
`consolidado`) e gera:
- **`EMR_consolidado.xlsx`** — aba **`Posicoes`** (formato Beehus exato, 8 colunas, todos
  os meses; `Data` distingue; válido p/ upload pois Beehus indexa por companyId+walletId+positionDate)
  e aba **`Transacoes`** (revisão).
- **`EMR_consolidado_transactions.json`** — formato de **upload da Beehus** para transactions
  (FILE_GENERATION #1 / `beehus_api.create_transaction`: companyId+transactions[]), todos os meses.

Reexecutar após processar novos meses. (Transactions na Beehus sobem via **JSON/POST**, não
via arquivo multipart — por isso o artefato de upload é JSON, e não XLSX como as posições.)

---

## 7. Pontos em aberto / TODO

- [ ] **walletId placeholder** — trocar os rótulos (`XP MB`, `Safra`…) pelos walletId reais antes do upload.
      Só existem 2 wallets EMR (XP) no Mongo; Safra/Safra 2 ainda não têm wallet.
- [ ] **`quantity`/`pu` das posições** — DataWM só tem o financeiro; decidir se o upstream exige unidades.
- [ ] **Securities `revisar`** — 83 posições (bonds só-nome) e 267 transações sem `securityId`;
      rodar `security_matcher` fuzzy ou registrar os ausentes no cadastro.
- [ ] **18 fundos com CNPJ não encontrados** no `db.securities` — cadastrar.
- [ ] **901 transações sem tipo** (de 9.313, ~10%) — convenção p/ ambíguos (ajuste day-trade/futuros, "Investback").
- [ ] **startDateConsolidation** das wallets = 2026-03-11 vs dados de jul/2025 (confirmar backfill).
- [x] **Generalização** — layout estável confirmado nos 10 meses (202507→202604).
- [x] **10 meses processados** — os 6 criptografados (`202510, 202512, 202601-202604`) abriram
      com a senha `Report` (`mb_batch.py --password`).

---

*Referência usada nesta spec: `data/.tmp/EMR_202507.xlsm` (jul/2025). Atualize os números de
sanidade ao validar novos meses.*
