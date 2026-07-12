# Parsing do `unprocessedId` bruto — notas (2026-07-03)

> Isto **não é regra de nomenclatura** (ver `reference/beehusName-regra-final.md` para isso) — é a
> etapa anterior: como o código hoje extrai emissor/taxa/indexador/data de dentro da string bruta
> do custodiante antes de aplicar a fórmula de `beehusName`. Documentado à parte porque nenhum dos
> dois arquivos de referência (`beehusName-regras.md`, `REGRAS_BEEHUSNAME.md`) trata desse passo —
> ambos assumem que os campos discretos já existem.

Fonte: `templates/controlpanel.html:2372-3249`. O spec original (referenciado nos comentários como
`docs/CADASTRO_ATIVOS.md §14-§16` e `PARSER_SECURITIES.md §5.5-§5.7`) não existe mais no
repositório — o que segue foi reconstruído lendo o código.

## Famílias reconhecidas

Só CDB, LCA, LCI, LCD, LF, CCB, LC, CRI, CRA e Debênture têm parser dedicado (`_parseUnprocessedId`
+ `_parseCriCraDeb`, que tenta 6 variantes em sequência). Os demais tipos (fundos, ações, títulos
públicos, COE, compromissada) não têm parser de string bruta aqui — chegam com campos já
resolvidos por outro caminho (classificador + `security_matcher.py`).

`_parseCriCraDeb(raw)` tenta, nesta ordem, a primeira que casar:
1. `_parseCriCraDebLong` — formato longo com 6-7 partes separadas por `" - "`, taxa em `%`
   explícito e indexador em parte separada. Ex.: `CDB ... - 2027-03-24 - 5.55% - IPCA`.
2. `_parseCriCraShort` — CRI/CRA em 4 partes, sem taxa inline (assume 100% CDI pós-fixado quando
   não há `PRE` no header — ver seção "Defaults assumidos" abaixo).
3. `_parseDebCompact` — Debênture compacta: `DEB <EMISSOR> <INDEXER>+<SPREAD> - <DD/MM/AAAA>`.
4. `_parseXpCodePrefix` — formato específico da XP: `<L|C|B><5-6 dígitos><TIPO> <emissor>
   <DD/MM/AAAA> <taxa>`. Reconhece também `DEBI` como alias de debênture incentivada.
5. `_parsePreformattedBeehusname` — quando o `uid` já vem parecido com um `beehusName` pronto
   (`TIPO - TIPO emissor taxa DD/Mmm/AAAA [- meta]`).
6. `_parseBeehusnameInline` — variante sem o prefixo duplicado (`TIPO emissor taxa DD/Mmm/AAAA
   [- meta]`).

`_parseUnprocessedId` (separado, só para CDB/LCA/LCI/LCD/LF/CCB/LC) espera exatamente 5+ partes
separadas por `" - "`: header (tipo + ticker), tipo+emissor, ..., taxa, vencimento.

## Defaults assumidos quando o dado não vem explícito

- **CRI/CRA formato curto** (`_parseCriCraShort`): se o header não contém a palavra `PRE`, assume
  `indexer="CDI"`, `yield=0`, `indexerPercentual=100` — ou seja, **100% do CDI, pós-fixado**. Não
  há como saber, só pelo formato curto, se o percentual real é diferente de 100%; isso é uma
  aproximação do parser, não um dado confirmado pelo custodiante.

## Detecção de debênture incentivada (`_isInfraDebenture`)

`type = infrastructureDebenture` quando o texto bruto contém, case-insensitive, qualquer uma das
palavras `INFRA`, `INCENT` ou `INCENTIVADA` (regex `\bINFRA\b|\bINCENT\b|INCENTIVADA`). Caso
contrário, `type = debenture`. No parser XP (`_parseXpCodePrefix`), o código `DEBI` no prefixo
também força `infrastructureDebenture`, independentemente desse teste de texto.

## Parsing de taxa (`_parseRate`)

Reconhece, em ordem: `IPCA`/`IPC-A` (extrai spread após `+`, `indexerPercentual=100`); `CDI` com
duas variantes (`CDI + N%` → spread com `indexerPercentual=100`; `N% CDI[+ M%]` → percentual
explícito, spread opcional); pré-fixado (`+N%` ou `N%` isolado, sem CDI/IPCA no texto). Fallback
final: extrai qualquer `%` encontrado e assume `indexer="PRE"` se nada mais casar.

## Parsing de data

`_ddmmyyyyToIso`/`_dateToPtAbbr` só aceitam `DD/MM/YYYY` (ano de 4 dígitos, separador `/`). Datas
já em `DD/Mmm/YYYY` (mês por extenso abreviado) são reconhecidas separadamente nos parsers que
lidam com formato "pré-formatado" (`_parsePreformattedBeehusname`, `_parseBeehusnameInline`), via
lookup na tabela `_PT_MONTH_ABBR`.

## Relação com `security_matcher.py`

Esses parsers em JS (`templates/controlpanel.html`) atuam **só no cliente**, para pré-preencher o
modal de cadastro (`_buildRegRow`) quando o `uid` bruto casa com um dos formatos conhecidos. São
independentes do extrator Python `extract_features`/`security_matcher.py` usado no fluxo de
match/complemento (ver memória de projeto `project-security-matching`) — não compartilham código,
apenas o mesmo `uid` de entrada. Divergências de comportamento entre os dois (ex. um reconhece um
padrão que o outro não) não foram auditadas nesta sessão.