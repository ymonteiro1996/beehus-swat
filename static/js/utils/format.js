/* Utilidades de formatação compartilhadas entre telas (window.Fmt).

   Contexto:
   Funções puras de formatação / escape / parse de números, reutilizadas por
   qualquer tela. Ficam num único arquivo, carregado ANTES dos scripts de página,
   para nenhuma tela duplicar essa lógica (regra do CLAUDE.md: comum -> utils/).
   Expõe o objeto global `window.Fmt`. */
(function () {
  "use strict";

  const Fmt = {
    /* Contexto: escapa uma string para inserção segura em HTML (evita quebra de
       markup / XSS ao interpolar dados do backend). Retorna string.
       Pseudocódigo: troca cada & < > " ' pela entidade HTML correspondente. */
    esc(value) {
      return String(value ?? "").replace(/[&<>"']/g,
        c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    },

    /* Contexto: formata um número em pt-BR com `frac` casas decimais, ou um
       traço cinza ("—") quando o valor é nulo/não-finito. Usado nas células
       numéricas das tabelas. Retorna HTML (string).
       Pseudocódigo:
         1. Valor nulo/indefinido -> span com "—".
         2. Valor não-finito -> span com "—".
         3. Caso contrário -> toLocaleString pt-BR com `frac` casas fixas. */
    numberOrDash(value, frac) {
      if (value == null) return '<span class="text-gray-300">—</span>';
      const n = Number(value);
      if (!isFinite(n)) return '<span class="text-gray-300">—</span>';
      return n.toLocaleString("pt-BR", { minimumFractionDigits: frac, maximumFractionDigits: frac });
    },

    /* Contexto: atalho de numberOrDash com 6 casas (quantidade). */
    qty(value) { return this.numberOrDash(value, 6); },
    /* Contexto: atalho de numberOrDash com 6 casas (PU). */
    pu(value) { return this.numberOrDash(value, 6); },
    /* Contexto: atalho de numberOrDash com 2 casas (valores monetários). */
    money(value) { return this.numberOrDash(value, 2); },

    /* Contexto: data de hoje em ISO 'YYYY-MM-DD' (hora local), para pré-preencher
       inputs <input type="date">. Retorna string.
       Pseudocódigo: lê ano/mês/dia locais e monta a string com zero-padding. */
    todayISO() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    },

    /* Contexto: formata um número para um input de edição em estilo pt-BR
       (vírgula decimal, sem zeros à direita). Retorna "" para nulo/não-finito.
       Pseudocódigo:
         1. Vazio/nulo -> "".
         2. Não-finito -> "".
         3. Fixa 8 casas, remove zeros à direita, troca "." por ",". */
    formatDecimalBR(value) {
      if (value == null || value === "") return "";
      const n = Number(value);
      if (!isFinite(n)) return "";
      const fixed = n.toFixed(8).replace(/\.?0+$/, "");
      return fixed.replace(".", ",");
    },

    /* Contexto: interpreta um decimal digitado em pt-BR (milhar "." e decimal
       ",") e devolve Number, ou null se vazio/inválido. Inverso de
       formatDecimalBR.
       Pseudocódigo:
         1. Vazio/nulo -> null.
         2. Remove os pontos de milhar, troca "," por "." e faz parseFloat.
         3. Retorna o número se finito, senão null. */
    parseDecimalBR(text) {
      if (text == null || text === "") return null;
      const v = parseFloat(String(text).replace(/\./g, "").replace(",", "."));
      return isFinite(v) ? v : null;
    },
  };

  window.Fmt = Fmt;
})();
