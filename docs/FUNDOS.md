# Fundos — Upload XML Anbima

Página: `/fundos` (sidebar: *Upload correções > Fundos*).

Permite operador subir múltiplos XMLs no padrão Anbima (4.0 e 5.0 ISO 20022),
extrair posições, caixa e provisões, e enviar para a Beehus reproduzindo o
mesmo fluxo de `aplicar → confirmar e enviar` da Carteira.

---

## Layouts aceitos

### 4.0 (`arquivoposicao_4_01`)

Estrutura: um `<fundo>` por arquivo com `<header>` + N blocos de posição.

| Bloco | Significado | Vira |
|---|---|---|
| `<titpublico>` | Título público (LFT, NTN-B etc.) | Linha de posição. O bloco `<compromisso>`, quando presente, é ignorado para fins de nome/qty/PU — o snapshot da RAVELLO 2 mantém apenas a linha "host" do título. |
| `<cotas>` | Cota de fundo | Linha de posição |
| `<opcoesderiv>` | Opção sobre derivativo | Linha de posição (qty e PU recebem o sinal de `classeoperacao` — `V`/venda ⇒ qty negativo) |
| `<caixa>` | Saldo de caixa | Linha de caixa (`Caixa=Sim` no XLSX) |
| `<provisao>` | Provisão | Cadastrada via `POST /beehus/provisions` após o upload |

### 5.0 (`PosicaoAtivosCarteira` / ISO 20022)

Estrutura: envelope `SctiesBalAcctgRpt` com `BalForAcct` (cabeçalho do fundo)
e `SubAcctDtls > BalForSubAcct[]` (posições).

* `ShrtLngInd=SHOR` → quantidade negativa, saldo negativado por `Sgn=false`.
* Não há `<caixa>` nem `<provisao>` no envelope BTG conhecido — uploads 5.0
  enviam apenas posições.

---

## Convenção de nomes

O padrão de nomenclatura espelha as snapshots em produção das wallets de
referência: `680a9ce43b2296d8612711e0` (RAVELLO 2 — layout 4.0) e
`680a9ce43b2296d8612711ee` (JAVA 95 — layout 5.0). Assim novos uploads
caem nos mesmos `unprocessedId` que o operador já tem cadastrados.

### Layout 4.0

Blocos **validados** (padrão extraído do snapshot da wallet `680a9ce43b2296d8612711e0`):

| Origem | Padrão | Exemplo |
|---|---|---|
| `titpublico` | `<isin>_<cusip>_<dtemissao>_<dtvencimento>` | `BRSTNCLF1RK7_STNCLF1RK_2022-04-06_2028-09-01` |
| `cotas` | `<isin>_<cnpjfundo>` | `BR0MGUCTF005_57897771000140` |
| `opcoesderiv` | `<isin>_<ativo>_<serie>_<C\|P>_<strike>_<dtvencimento>` | `BRBMEFVDR563_DOL_NVD1_P_4500_2026-07-01` |
| `caixa` | linha de caixa (`Caixa=Sim`) com `cashAccounts.unprocessedId` da wallet | `Caixa - COMPROMISSADA BTG PACTUAL 99,90%CDI` |

Blocos **best-effort** (padrões derivados por analogia — confirme contra um snapshot real quando o XML aparecer):

| Origem | Padrão | Exemplo sintético | Análogo |
|---|---|---|---|
| `acoes` | `<isin>_<codativo>` | `BRPETRACNPR6_PETR4` | 5.0 `EQUI` |
| `debenture` | `<isin>_<codativo>_<dtemissao>_<dtvencimento>` | `BRABCDDBS001_ABCD11_2023-01-10_2030-01-10` | 4.0 `titpublico` |
| `futuro` | `<isin>_<ativo>_<serie>_<dtvencimento>` | `BRBMEFD1IF35_DI1_F35_2035-01-02` | 4.0 `opcoesderiv` − strike/cp |
| `termo` | `<isin>_<codativo>_<dtoperacao>_<dtvencimento>` | `BRVALEACNOR0_VALE3_2026-04-20_2026-05-20` | 4.0 `debenture` (com `dtoperacao`) |
| `aluguel` | `<isin>_<codativo>` (sinal flipa em `tipotomadordoador=T`) | `BRPETRACNPR6_PETR4` qty negativa | 4.0 `acoes` |
| `exterior` | `<isincomp>_<descrcomp>` | `US0378331005_AAPL` | 5.0 `EQUI` |
| `swap` | `swap_<identificador>_<dtoperacao>_<dtvencimento>` | `swap_SWAP-001_2026-01-01_2026-06-01` | — |

Notas 4.0:

* **`<compromisso>` não vira linha separada.** O snapshot existente mantém
  o `titpublico` host (qty = `qtdisponivel + qtgarantia`, pu = `puposicao`)
  e ignora o bloco `<compromisso>` para fins de posição.
* **Derivativos com `classeoperacao`** (`opcoesderiv`, `futuro`, `termo`):
  qty negativa em `V` (venda) e PU = `valorfinanceiro / qty_signed`, para
  `qty * pu = valorfinanceiro` (validado em NVD1 P com `qty=-1100,
  pu=-3.798, balance=+4178.05`).
* **`aluguel`** flipa o sinal de qty quando `tipotomadordoador=T` (tomador
  → short).
* **`swap`** sem ISIN: usa `<identificador>` + datas. Qty/PU ficam `0` e o
  saldo carrega `valorcurva` (fallback para `valorbase`).

### Layout 5.0

O discriminador é o `<OthrId>` cujo `<Tp><Prtry>` é `TABELA NIVEL 1`:

Classes **validadas** (snapshot da wallet `680a9ce43b2296d8612711ee`):

| Classe (`OthrId.Id`) | Padrão | Exemplo |
|---|---|---|
| `EQUI` (ações/ETFs) | `<isin>_<desc>` | `BRBOVACTF003_BOVA11` |
| `SHAR` (cotas de fundo) | `<isin>_<cnpj>_<desc>` | `BRCSH4CTF005_09215250000113_TESOURO SELIC FI RF` |
| `GOVE` (títulos públicos) | `<isin>_<desc>_<isseDt>_<mtrtyDt>` | `BRSTNCLF1RI1_LFT REF_2022-01-05_2028-03-01` |
| `FUTU` (futuros) | `<isin>_<desc>` | `BRBMEFD1I6J9_DI1FF35` |

Classes **best-effort** (analogia com os builders 4.0 — confirme com snapshot real):

| Classe (`OthrId.Id`) | Padrão | Análogo |
|---|---|---|
| `OPCO` / `OPTI` (opções) | `<isin>_<desc>_<C\|P>_<strike>_<mtrtyDt>` | 4.0 `opcoesderiv` |
| `TERM` (termo) | `<isin>_<desc>_<mtrtyDt>` | 4.0 `termo` |
| `DEBE` (debêntures) | `<isin>_<desc>_<isseDt>_<mtrtyDt>` | 5.0 `GOVE` |
| `CDB` / `LCI` / `LCA` / `LF` / `LCRE` / `AGRO` | `<isin>_<desc>_<isseDt>_<mtrtyDt>` | 5.0 `GOVE` |
| `SWAP` | `swap_<isin>_<desc>_<mtrtyDt>` | 4.0 `swap` |
| `REPO` / `COMP` (compromissadas) | `<isin>_<desc>_<mtrtyDt>` | — |
| outros / sem tag | `<isin>_<desc>` (fallback seguro) | — |

Para `OPCO`/`OPTI`, o parser extrai:
* **strike**: `FinInstrmAttrbts/ConvsPric/Val/Amt`
* **call/put**: tenta `FinInstrmAttrbts/OptnTp/Cd` → `PutOrCallInd` →
  `CallOrPut` (primeira letra de cada, normalizada para `C`/`P`).

Notas 5.0:

* **`SHAR` preserva ISIN genérico.** Fundos sem ISIN real
  (`BR0000000000`) entram como `BR0000000000_<cnpj>_<desc>` —
  o CNPJ é o discriminador real.
* **`SHOR` flipa apenas a quantidade.** PU permanece positivo, balance
  carrega o sinal via `HldgVal.Sgn=false`. O nome do ativo não muda
  entre legs long e short — o snapshot mostra `LFT REF` aparecendo 6×
  (1 long + 5 shorts) sob o mesmo `unprocessedId`.
* **Mesmo nome em múltiplas linhas é permitido.** O parser do upstream
  aceita várias linhas com o mesmo `Ativo` dentro do mesmo
  `(Data, Carteira)` e a tela de preview também não deduplica.

---

## Fluxo da página

1. **Empresa** — operador escolhe `companyId` (mesma fonte das demais páginas:
   `company_filter` em `settings.json`).
2. **Upload** — drag-and-drop ou clique no dropzone. Múltiplos XMLs aceitos
   simultaneamente. Servidor parseia cada arquivo via `POST /api/fundos/parse`
   e devolve um payload de preview por arquivo.
3. **Preview por arquivo** — mostra:
   * Cabeçalho (nome do fundo, CNPJ, versão do layout, data, totais).
   * Tabela de linhas (ativo + qty + PU + saldo + origem). Linhas
     desabilitadas via checkbox são excluídas do envio.
   * Tabela de provisões (apenas 4.0) com tipo editável (default vem de
     `codprov` quando reconhecido — caso contrário `other`).
   * Wallet sugerida automaticamente por match de nome (case-insensitive,
     substring); operador pode trocar manualmente.
   * Data base vem do XML mas pode ser sobrescrita.
4. **Confirmar e enviar** — `POST /api/fundos/apply` envia um upload
   `unprocessedSecurityPositions` por arquivo (chave `(walletId, positionDate)`)
   e cria cada provisão individualmente. Resultados aparecem com status
   per-arquivo (OK / IGNORADO / ERRO) + erros granulares por provisão.

---

## Endpoints

| Método + rota | Descrição |
|---|---|
| `GET /fundos` | Página HTML (renderizada dentro do shell iframe). |
| `GET /api/fundos/filters/companies` | Lista de empresas visíveis. |
| `GET /api/fundos/filters/wallets?companyId=...` | Wallets da empresa (id, nome, currencyId). |
| `POST /api/fundos/parse` | Multipart `files[]` + `companyId` → JSON de preview por arquivo. |
| `POST /api/fundos/apply` | JSON com arquivos confirmados → upload XLSX + criação de provisões. |

---

## Mapeamento `codprov` → `provisionType` (4.0)

Tabela base (pode ser sobrescrita pelo operador no preview):

| codprov | provisionType padrão |
|---:|---|
| 2 | dividend |
| 12 | interestOnEquity |
| 13 | managementFee |
| 14 | couponInterest |
| 16 | adjustment |
| 34 | other |
| qualquer outro | `other` (com descrição capturando `codprov` e `credeb`) |

Crédito (`credeb=C`) preserva o sinal positivo; débito (`credeb=D`)
inverte para negativo antes do `POST /beehus/provisions`.

---

## Limitações conhecidas

* **Match de wallet por nome** é heurístico. O XML 4.0 traz só `cnpj` (sem
  campo correspondente em `wallets`), por isso a página propõe e o operador
  confirma.
* **5.0 sem caixa/provisões**: o sample BTG não tem campos para caixa nem
  provisões. Se o custodiante começar a emitir, será necessário estender
  `_parse_v50` para extrair `BalBrkdwn`/`AddtlBalBrkdwn` no nível do fundo.
* **`<compromisso>` ignorado**: o bloco `<compromisso>` dentro de
  `titpublico` (4.0) não gera linha extra — o snapshot de referência
  mantém só o host disponível/garantia. Se em algum momento for preciso
  separar a perna repo, é só inverter a lógica em `_parse_v40` na seção
  `titpublico`.
