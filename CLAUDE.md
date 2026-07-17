# Instruções de Desenvolvimento — Beehus SWAT

> Este projeto é desenvolvido **sempre com o auxílio do Claude**, por um time que nem
> sempre tem base em programação. Por isso estas instruções são **obrigatórias** e têm
> prioridade sobre qualquer padrão default. O objetivo é manter o código **saudável,
> legível e fácil de manter em equipe**, evitando os problemas atuais: arquivos gigantes,
> funcionalidades empilhadas na mesma página e conflitos de merge constantes.
>
> **Regra número 1 (leia antes de tudo):** se um requisito futuro exigir quebrar qualquer
> padrão de arquitetura descrito aqui, **NÃO implemente direto. Pare e confirme com o
> desenvolvedor primeiro**, explicando o que seria quebrado e por quê. Ver seção
> [Quebra de arquitetura](#7-quebra-de-arquitetura-confirmar-sempre-antes).
>
> **⛔ Regra número 2 (CRÍTICA — dados): o Claude NÃO está autorizado a alterar nada no
> MongoDB por acesso direto.** O acesso direto ao Mongo é permitido **apenas para LEITURA,
> e somente como fallback**. **Qualquer alteração de dados (criar, editar ou remover) deve
> ser feita exclusivamente pelas rotas homologadas da API Beehus.** Nunca escreva direto
> no banco. Ver seção [Acesso a dados](#8-acesso-a-dados--api-first-mongo-é-somente-leitura).

---

## 1. Nomenclatura de variáveis e funções

- **Sempre `snake_case`** para variáveis, funções e nomes de arquivos Python.
  (`total_liquido`, `calcular_nav`, `carteira_selecionada` — nunca `totalLiquido`,
  `calcNav`, `x`, `tmp`, `aux`, `data2`.)
- **Nomes representativos e por extenso.** O nome deve dizer o que a coisa é ou faz,
  sem precisar de comentário para explicar. Prefira clareza a brevidade.
- Evite abreviações obscuras. `qtd`, `pu`, `nav` são aceitáveis por serem termos de
  domínio já consolidados no projeto; `q`, `p`, `n` não são.
- Constantes em `MAIÚSCULO_COM_UNDERSCORE` (`DATA_BASE_PADRAO`, `LIMITE_LINHAS`).
- Booleanos com prefixo que revele intenção: `is_`, `has_`, `deve_`, `tem_`
  (`is_carteira_valida`, `tem_provisao`, `deve_recalcular`).

```python
# RUIM
def calc(d, w):
    r = []
    for x in d:
        if x['w'] == w: r.append(x)
    return r

# BOM
def filtrar_posicoes_por_carteira(posicoes, carteira_id):
    posicoes_da_carteira = []
    for posicao in posicoes:
        if posicao["carteira_id"] == carteira_id:
            posicoes_da_carteira.append(posicao)
    return posicoes_da_carteira
```

> Em JavaScript dentro dos templates, siga a convenção nativa da linguagem
> (`camelCase` para variáveis/funções), mas mantenha o mesmo rigor: nomes
> representativos e por extenso.

---

## 2. Comentário obrigatório acima de TODA função

Toda função — Python ou JavaScript — deve ter, imediatamente acima dela, um comentário
no formato **"contexto + pseudocódigo claro"**:

1. **Contexto:** 1–2 frases dizendo *para que serve* a função, *quando* é chamada e
   *o que ela retorna*. (O "porquê", não o "como".)
2. **Pseudocódigo claro:** os passos principais da função em linguagem simples,
   numerados, para que qualquer pessoa entenda a lógica sem ler a implementação.

Em Python, use **docstring** (padrão do projeto). Em JavaScript, use bloco `/* */`.

```python
def calcular_nav_projetado(posicoes, transacoes, data_alvo):
    """Contexto:
    Calcula o NAV simulado de uma carteira projetada até `data_alvo`. Usado na tela
    de Conciliação (Movimentação) quando o usuário clica em "Movimentar".
    Retorna um dict {nav, nav_por_cota, gap}.

    Pseudocódigo:
      1. Parte das posições de origem agregadas por ativo.
      2. Aplica as transações da janela (origem, alvo] ajustando quantidade e caixa.
      3. Busca o PU oficial de cada ativo na data-alvo (com fallbacks).
      4. Soma provisões de liquidação e de dividendos/JCP.
      5. Calcula nav, nav_por_cota e gap e retorna.
    """
    ...
```

```javascript
/* Contexto:
   Monta a linha da tabela de conciliação a partir de um registro de posição.
   Chamada ao renderizar a grade principal. Retorna um elemento <tr>.

   Pseudocódigo:
     1. Cria o <tr> e aplica a classe de destaque se houver divergência.
     2. Para cada coluna configurada, cria a célula formatada.
     3. Anexa o handler de clique para abrir o drill-down.
     4. Retorna a linha pronta. */
function montarLinhaConciliacao(posicao) {
  ...
}
```

- O comentário descreve a **intenção e a lógica**, nunca repete o código linha a linha.
- Ao **alterar** uma função, **atualize o comentário** junto. Comentário desatualizado
  é pior que ausência de comentário.

---

## 3. Funções pequenas, com um único contexto

- Cada função faz **uma coisa só** e tem um nome que descreve exatamente essa coisa.
- Se você precisar de "e"/"também" para descrever a função, ela provavelmente deve
  virar duas.
- **Prefira várias funções pequenas e reaproveitáveis a uma função grande.** Se um
  trecho de lógica pode fazer sentido isolado (um cálculo, uma formatação, uma
  validação, uma consulta), **extraia numa função própria** com nome claro.
- Sinais de que uma função está grande demais e precisa ser dividida:
  - passa de ~40–50 linhas;
  - tem mais de um nível profundo de `if`/`for` aninhado;
  - mistura buscar dados + calcular + formatar + responder na mesma função.
- Separe as **camadas**: buscar dados, aplicar regra de negócio e formatar a resposta
  devem ficar em funções distintas sempre que possível.

---

## 4. Divisão clara das páginas (evitar os monólitos atuais)

Este é o ponto mais importante para acabar com os conflitos de merge. Hoje várias
telas têm todo o código empilhado num único arquivo/bloco gigante. **Não repita esse
padrão em nada novo.**

### Backend (blueprints em `pages/`)

- Cada tela é um **blueprint** com **responsabilidade única**.
- Dentro do arquivo, mantenha uma **ordem e divisão explícitas**, marcadas com
  cabeçalhos de comentário, na seguinte sequência:

```python
# ─────────────────────────────────────────────────────────────
# 1. IMPORTS E CONFIGURAÇÃO DO BLUEPRINT
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# 2. HELPERS / REGRAS DE NEGÓCIO (funções puras, sem Flask)
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# 3. ROTAS (@bp.route) — só orquestram: chamam helpers e respondem
# ─────────────────────────────────────────────────────────────
```

- **Rotas devem ser finas:** validam entrada, chamam funções de negócio e devolvem a
  resposta. A lógica pesada mora nos helpers (seção 2), não dentro da rota.
- Se um arquivo de página passar de **~800 linhas** ou acumular responsabilidades
  distintas, **pare e proponha dividi-lo** (ex.: mover as regras de negócio para um
  módulo próprio) — confirmando com o desenvolvedor antes (ver seção 7).

### Frontend (templates HTML)

- **Não** crie novos HTMLs com milhares de linhas de JS/CSS embutidos num único
  `<script>`/`<style>`. Foi isso que gerou os conflitos atuais.
- Separe por tipo de conteúdo:
  - **CSS** → `static/css/<tela>.css`.
  - **JavaScript** → `static/js/…` (ver a regra de pastas abaixo).
  - **HTML/estrutura** → o template da tela, o mais enxuto possível (só marcação +
    `<link>`/`<script src>`).

#### Quebra obrigatória em pasta por tela (vale também nas REFATORAÇÕES)

- **Nunca deixe um único `.js` gigante por tela.** Toda tela com JS não-trivial tem a
  sua **própria pasta** `static/js/<tela>/`, com **um arquivo por funcionalidade/contexto**.
  Isso vale tanto para código novo quanto para **toda refatoração**: ao refatorar uma
  tela, **sempre** quebre o script dela nessa estrutura de pasta — não pare em "tirei o
  JS de dentro do HTML".
- Estrutura típica de `static/js/<tela>/` (adapte os nomes ao domínio da tela):
  - `state.js`   — declara o objeto único da tela (estado + helpers finos).
  - `filtros.js` — filtros / seleção / montagem de payload.
  - `resultados.js` — busca e renderização dos resultados.
  - `edicao.js`  — modo de edição (quando houver).
  - `modais.js`  — modais / fluxos de confirmação e envio.
  - `index.js`   — bootstrap (`init` + `DOMContentLoaded`).
- **Padrão sem build (mantém um objeto só):** `state.js` declara `const Tela = { … }`
  (global, para os `onclick` inline do template); cada arquivo seguinte **acrescenta**
  seus métodos com `Object.assign(Tela, { … })`. Assim `this` e os handlers continuam
  funcionando e cada funcionalidade fica isolada num arquivo.
- **Carregue na ordem** no template: utils compartilhados → `state.js` → demais pedaços
  → `index.js` por último. Ex.:
  ```html
  <script src="/static/js/utils/format.js"></script>
  <script src="/static/js/carteira/state.js"></script>
  <script src="/static/js/carteira/filtros.js"></script>
  <script src="/static/js/carteira/resultados.js"></script>
  <script src="/static/js/carteira/edicao.js"></script>
  <script src="/static/js/carteira/modais.js"></script>
  <script src="/static/js/carteira/index.js"></script>
  ```
- **Objetivo:** dois desenvolvedores mexendo em funcionalidades diferentes da mesma tela
  editam **arquivos diferentes** — sem conflito de merge. Se um pedaço crescer demais,
  quebre-o de novo (ex.: `modais.js` → `modais_busca.js` + `modais_envio.js`).
- A tela **`carteira`** é a referência viva desse padrão — use-a como modelo.

---

## 5. Pasta `utils/` — funções de uso comum

Para simplificar o reaproveitamento e evitar código duplicado:

- Crie e use uma pasta **`utils/`** na raiz do projeto para **funções genéricas e
  reutilizáveis** — aquelas que servem a mais de uma tela ou não pertencem à regra de
  negócio de nenhuma página específica.
- Exemplos do que vai em `utils/`: formatação de datas/moeda/número, parsing,
  validações genéricas, helpers de resposta HTTP, conversões, cálculos utilitários.
- Organize por tema, um arquivo por assunto:
  `utils/datas.py`, `utils/formatacao.py`, `utils/validacao.py`, etc.
- Para JavaScript reutilizável, use `static/js/utils/` da mesma forma.
- **Antes de escrever um helper novo, verifique se ele já existe em `utils/`.**
  Se algo parecido existir, reutilize ou generalize o existente em vez de duplicar.
- Quando notar a **mesma lógica repetida em duas ou mais páginas**, extraia para
  `utils/` e faça as páginas passarem a importar de lá.

```python
# utils/formatacao.py

def formatar_moeda_brl(valor):
    """Contexto:
    Formata um número como moeda brasileira (R$ 1.234,56). Usado em qualquer tela
    que exiba valores financeiros. Retorna string.

    Pseudocódigo:
      1. Arredonda para 2 casas.
      2. Aplica separador de milhar "." e decimal ",".
      3. Prefixa "R$ " e retorna.
    """
    ...
```

---

## 6. Reaproveitamento antes de escrever código novo

- Antes de implementar, **procure se já existe** função/helper que resolva o problema
  (em `utils/`, no blueprint da tela, ou em módulos como `beehus_api/`, `db.py`,
  `beehus_catalog.py`).
- Prefira **reutilizar e generalizar** a **copiar e colar**. Código duplicado é uma das
  principais fontes de bug e de divergência entre telas.
- Se for reutilizar algo que está "preso" dentro de uma página, **promova para `utils/`**
  em vez de duplicar — confirmando antes se a mudança afeta outras telas.

---

## 7. Quebra de arquitetura: confirmar SEMPRE antes

Se, para atender um requisito, a solução mais direta exigir **quebrar qualquer padrão
deste documento** — por exemplo: colocar muita lógica dentro de uma rota, inflar um
template já grande, duplicar código em vez de extrair para `utils/`, misturar
responsabilidades num único arquivo, ou criar um novo monólito —, então:

1. **NÃO implemente a versão que quebra o padrão.**
2. **Explique ao desenvolvedor**, em português claro:
   - qual padrão seria quebrado;
   - por que o requisito parece empurrar para essa quebra;
   - qual seria a alternativa saudável (mesmo que dê mais trabalho);
   - o trade-off entre as opções.
3. **Aguarde a decisão do desenvolvedor** antes de escrever o código.

O objetivo é que nenhuma decisão de arquitetura seja tomada silenciosamente no meio de
uma implementação. Padrão saudável primeiro; exceções só com aval explícito.

---

## 8. Acesso a dados — API-first, Mongo é SOMENTE LEITURA

O projeto tem uma camada de dados bem definida: **API-first, com o Mongo apenas como
fallback de leitura**. Todo acesso a dados passa por [beehus_api/](beehus_api/),
[db.py](db.py) e [beehus_catalog.py](beehus_catalog.py).

- **Nenhuma tela nova acessa o banco direto nem faz HTTP cru.** Sempre use as camadas
  acima. Isso evita que cada página reinvente o acesso a dados (fonte clássica de bug e
  de divergência entre telas).

### ⛔ Proibição de escrita direta no MongoDB (regra crítica)

**O Claude NÃO está autorizado a executar nenhuma operação de escrita/alteração direta no
MongoDB.** Isso é inegociável e vale para qualquer requisito.

- **PROIBIDO** (acesso direto ao banco): `insert_one`/`insert_many`, `update_one`/
  `update_many`, `replace_one`, `delete_one`/`delete_many`, `drop`, `bulk_write`,
  `find_one_and_*`, criação/remoção de índices, ou qualquer comando que **modifique**
  dados ou estrutura no Mongo.
- **PERMITIDO** apenas: **leitura** (`find`, `aggregate`) e **somente como fallback**,
  quando a API Beehus não cobre aquele dado.
- **Toda alteração de dados** (criar, editar ou remover) **deve passar exclusivamente
  pelas rotas homologadas da API Beehus**, através dos módulos de [beehus_api/](beehus_api/)
  (ex.: `securities`, `transactions`, `positions`, `provisions`).
- Se a operação de escrita necessária **não existir** na API homologada: **PARE e
  confirme com o desenvolvedor**. **Nunca** improvise gravando direto no banco, nem crie
  conexões de escrita, credenciais elevadas ou scripts que gravem no Mongo.

> Resumindo: **Mongo = leitura de emergência. Escrita = só via API homologada.**

---

## 9. Segredos e dados de cliente (segurança)

- **Segredos só via variável de ambiente** (`SWAT_MONGO_URI`, `.env`, etc.).
  **Nunca** deixe credenciais, tokens, URIs ou senhas hardcoded no código, e **nunca**
  as comite (o `.env` e `user_connections.json` já estão no `.gitignore`).
- **Nunca logue nem exponha credenciais** em mensagens de erro ou telas. O projeto já
  faz isso (ver `_scrub` em [app.py](app.py), que remove usuário/senha de URIs do Mongo
  antes de qualquer log ou resposta) — mantenha esse cuidado em código novo.
- A pasta [data/](data/) contém **dados reais de cliente**. Vários desses arquivos estão
  no `.gitignore` justamente por isso — **nunca comite dados de cliente** e confira o
  `.gitignore` antes de adicionar arquivos novos em `data/`.

---

## 10. Configuração em `data/*.json`, não no código

- O projeto externaliza regras e parâmetros em JSON (`transaction_type_rules.json`,
  `precificacao_config.json`, `conciliacao_config.json`, etc.). **Parâmetros
  configuráveis vão para `data/*.json`**, não hardcoded no meio da lógica.
- Atenção a quais arquivos são versionados e quais são locais/sensíveis: o `.gitignore`
  faz essa distinção (ex.: `data/config.json`, caches e pastas de dados são ignorados).

---

## 11. Manter a documentação (`docs/`) atualizada

- O projeto tem documentação por tela/fluxo em [docs/](docs/) (ex.: `CONCILIACAO_MOV.md`,
  `PRECIFICACAO.md`, `CONTROLPANEL.md`).
- **Ao criar ou alterar uma funcionalidade, atualize o doc correspondente** em `docs/`
  na mesma tarefa. Documentação desatualizada engana quem for manter depois.

---

## 12. Higiene de Git (para reduzir conflitos de merge)

Complementa a divisão de código da seção 4 — juntas atacam a causa dos conflitos atuais:

- **Uma branch por funcionalidade.** Faça `git pull` da `main` **antes de começar**.
- **Commits pequenos e focados**, com mensagem clara do que mudou e por quê.
- **Uma funcionalidade = um arquivo/partial próprio** sempre que possível, para que dois
  desenvolvedores mexendo em coisas diferentes **não editem o mesmo trecho**.
- Não misture, no mesmo commit, mudanças de funcionalidades diferentes.

---

## 13. Dependências novas só com confirmação

- O [requirements.txt](requirements.txt) é enxuto e com versões fixadas. **Não adicione
  biblioteca nova** (Python ou JS) **sem confirmar com o desenvolvedor** — isso conta
  como quebra de padrão (seção 7). Prefira resolver com o que já existe no projeto.

---

## 14. Python, encoding e ambiente (Windows / OneDrive)

- Ao rodar dashboards Python (Dash/Flask), sempre use `use_reloader=False` em
  desenvolvimento e evite caminhos com espaços. Se o projeto estiver no OneDrive ou em um
  caminho com espaços, avise proativamente e configure de acordo.
- O ambiente é **Windows** e o conteúdo tem **acentos em português**. **Salve os arquivos
  em UTF-8** e tome cuidado com acentuação no terminal (houve correção recente salvando
  `start.ps1` como UTF-8 com BOM justamente por causa disso).

---

## Checklist rápido (antes de considerar uma tarefa pronta)

- [ ] Variáveis e funções em `snake_case` (Python) e com nomes representativos.
- [ ] Toda função tem comentário acima no formato **contexto + pseudocódigo**.
- [ ] Funções pequenas, cada uma com um único contexto.
- [ ] Nada de lógica pesada dentro de rotas; nada de novo monólito de HTML/JS.
- [ ] Página dividida em seções claras (imports → helpers → rotas; CSS/JS separados).
- [ ] JS da tela quebrado em `static/js/<tela>/` (um arquivo por funcionalidade) —
      inclusive ao refatorar; nada de `.js` único gigante por tela.
- [ ] Funções genéricas foram para `utils/` e o que já existia foi reaproveitado.
- [ ] Acesso a dados só via `beehus_api/` / `db.py` / `beehus_catalog.py`.
- [ ] **Nenhuma escrita direta no Mongo** — alterações só por rotas homologadas da API.
- [ ] Nenhum segredo hardcoded ou dado de cliente comitado.
- [ ] Doc em `docs/` atualizada; nenhuma dependência nova adicionada sem confirmar.
- [ ] Nenhum padrão de arquitetura foi quebrado sem confirmação do desenvolvedor.
