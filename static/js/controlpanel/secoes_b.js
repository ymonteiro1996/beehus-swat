/* Painel de Controle — seções de detalhe (parte B).
   Escopo global; usa consts do bootstrap; ordem importa. */
  function _lookupCanonical(rawEmissor, table) {
    if (!rawEmissor) return null;
    const up = String(rawEmissor).toUpperCase().trim();
    for (const entry of table) {
      for (const pat of entry.patterns) {
        if (up.indexOf(pat) !== -1) return entry.canonical;
      }
    }
    return null;
  }

  function _getShortEmissor(rawEmissor) {
    const c = _lookupCanonical(rawEmissor, _BANK_CANONICALS);
    if (c) return c;
    // Fallback: strip "BANCO " prefix and " S.A."/" S/A" suffix, title-case.
    let s = String(rawEmissor || "").trim();
    s = s.replace(/^BANCO\s+/i, "");
    s = s.replace(/\s+S\.?A\.?\s*$/i, "");
    s = s.replace(/\s+S\/A\s*$/i, "");
    return _titleCaseEmissor(s.trim());
  }

  function _getDevedorCanonical(rawEmissor) {
    const c = _lookupCanonical(rawEmissor, _DEVEDOR_CANONICALS);
    if (c) return c;
    return _titleCaseEmissor(rawEmissor);
  }

  // Converte DD/MM/YYYY → DD/Mmm/YYYY (mês abreviado em pt).
  function _dateToPtAbbr(ddmmyyyy) {
    const m = String(ddmmyyyy || "").match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (!m) return ddmmyyyy || "";
    const mon = _PT_MONTH_ABBR[parseInt(m[2], 10)] || m[2];
    return `${m[1]}/${mon}/${m[3]}`;
  }

  function _ddmmyyyyToIso(ddmmyyyy) {
    const m = String(ddmmyyyy || "").match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (!m) return "";
    return `${m[3]}-${m[2]}-${m[1]}`;
  }

  // Parse da string de taxa conforme §15.1 — comma decimal normalizado.
  // Retorna { indexer, yield, indexerPercentual } no formato Beehus (MD).
  function _parseRate(rateInfo) {
    const clean = String(rateInfo || "").trim().replace(/,/g, ".");
    const up = clean.toUpperCase();

    // IPCA (aceita "IPC-A" ou "IPCA")
    if (up.indexOf("IPC-A") !== -1 || up.indexOf("IPCA") !== -1) {
      const m = clean.match(/[+]?\s*(\d+(?:\.\d+)?)\s*%/);
      const y = m ? Number(m[1]) : null;
      return { indexer: "IPCA", yield: y, indexerPercentual: 100 };
    }

    // CDI
    if (up.indexOf("CDI") !== -1) {
      // "CDI +N.NN%" (sem percentual explícito antes) → 100% CDI + spread
      let m = clean.match(/^CDI\s*\+\s*(\d+(?:\.\d+)?)\s*%/i);
      if (m) return { indexer: "CDI", yield: Number(m[1]), indexerPercentual: 100 };

      // "N.NN% CDI" ou "N.NN% CDI + M.MM%"
      m = clean.match(/^(\d+(?:\.\d+)?)\s*%\s*CDI/i);
      if (m) {
        const pct = Number(m[1]);
        const sp = clean.match(/CDI\s*\+\s*(\d+(?:\.\d+)?)\s*%/i);
        if (sp) return { indexer: "CDI", yield: Number(sp[1]), indexerPercentual: pct };
        return { indexer: "CDI", yield: 0, indexerPercentual: pct };
      }
    }

    // Pré-fixado: "+N.NN%" ou "N.NN%"
    if (clean.startsWith("+") || /^\d+(\.\d+)?%$/.test(clean)) {
      const m = clean.match(/[+]?\s*(\d+(?:\.\d+)?)\s*%/);
      const y = m ? Number(m[1]) : null;
      return { indexer: "PRE", yield: y, indexerPercentual: null };
    }

    // Fallback: tenta extrair um % qualquer e marca como PRE.
    const m = clean.match(/(\d+(?:\.\d+)?)\s*%/);
    return { indexer: "PRE", yield: m ? Number(m[1]) : null, indexerPercentual: null };
  }

  // Formata número como taxa preservando precisão da fonte (mínimo 2 casas).
  // Ex.: 7.4588 → "7.4588", 7.33 → "7.33", 5.5 → "5.50", 100 → "100.00".
  function _fmtRateNum(n, minDecimals) {
    if (minDecimals === undefined) minDecimals = 2;
    const num = Number(n);
    if (!Number.isFinite(num)) return "";
    let s = String(num);
    if (!s.includes(".")) s += ".";
    const [intPart, fracPart = ""] = s.split(".");
    let f = fracPart;
    while (f.length < minDecimals) f += "0";
    return `${intPart}.${f}`;
  }

  // Formata a taxa para aparecer no beehusName conforme §12/§15.2/§16.5.
  function _formatRateForBeehusName(indexer, yieldVal, idxPct) {
    if (indexer === "PRE")  return `${_fmtRateNum(yieldVal)}%`;
    if (indexer === "IPCA") return `IPCA + ${_fmtRateNum(yieldVal)}%`;
    if (indexer === "CDI") {
      if (yieldVal && Number(yieldVal) > 0) return `CDI + ${_fmtRateNum(yieldVal)}%`;
      return `${_fmtRateNum(idxPct)}%CDI`;
    }
    return "";
  }

  // Parser completo — retorna {type, emissorRaw, emissorShort, ticker,
  //   maturityIso, indexer, yield, indexerPercentual, beehusName} ou null.
  function _parseUnprocessedId(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    if (!s) return null;
    const parts = s.split(" - ").map(p => p.trim());
    if (parts.length < 5) return null;

    const header    = parts[0];
    const typeEmiss = parts[1];
    const rateInfo  = parts[parts.length - 2];
    const maturity  = parts[parts.length - 1];

    const tokens = header.split(/\s+/);
    const assetType = (tokens[0] || "").toUpperCase();
    if (!["CDB", "LCA", "LCI", "LCD", "LF", "CCB", "LC"].includes(assetType)) return null;
    const ticker = tokens[tokens.length - 1];

    // Strip leading type prefix from emissor: "CDB BANCO C6…" → "BANCO C6…"
    const emissorRaw = typeEmiss.replace(/^(CDB|LCA|LCI|LCD|CD|LF|LIG|NP|LC|CCB|CRI|CRA)\s+/i, "").trim();
    const emissorShort = _getShortEmissor(emissorRaw);

    const rate = _parseRate(rateInfo);
    if (!rate || rate.yield === null) return null;

    const maturityIso = _ddmmyyyyToIso(maturity);
    if (!maturityIso) return null;

    const datePt = _dateToPtAbbr(maturity);
    let beehusName;
    if (assetType === "LF") {
      // LF: nome sem taxa, apenas label Pós/Pré-fixado (MD §4.2).
      const label = (rate.indexer === "PRE") ? "Pré-fixado" : "Pós-fixado";
      beehusName = `LF ${emissorShort} ${label} ${datePt}`.replace(/\s+/g, " ").trim();
    } else {
      const rateFmt = _formatRateForBeehusName(rate.indexer, rate.yield, rate.indexerPercentual);
      beehusName = `${assetType} ${emissorShort} ${rateFmt} ${datePt}`.replace(/\s+/g, " ").trim();
    }

    return {
      type: assetType.toLowerCase(),
      emissorRaw,
      emissorShort,
      ticker,
      maturityIso,
      indexer: rate.indexer,
      yield: rate.yield,
      indexerPercentual: rate.indexerPercentual,
      beehusName,
    };
  }

  // ── Parser para CRI/CRA/Debêntures — spec: docs/CADASTRO_ATIVOS.md §16 ────

  // Conectores em pt-BR que ficam minúsculos quando NO MEIO de um nome.
  const _PT_CONNECTORS = new Set([
    "do","de","da","dos","das","no","na","nos","nas",
    "e","o","a","os","as","em","para",
  ]);

  // Title-case simples para nomes de emissores (mantém tokens 1-2 chars upper,
  // conectores pt minúsculos no meio).
  function _titleCaseEmissor(s) {
    const words = String(s || "").toLowerCase().split(/\s+/);
    return words.map((w, i) => {
      if (!w) return w;
      if (_PT_CONNECTORS.has(w)) {
        return i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w;
      }
      if (w.length <= 2) return w.toUpperCase();
      if (/^s\/a$/.test(w))    return "S/A";
      if (/^s\.?a\.?$/.test(w)) return "S.A.";
      return w.charAt(0).toUpperCase() + w.slice(1);
    }).join(" ").trim();
  }

  // Detecta debênture incentivada (infraestrutura) por palavras-chave (§16.3).
  function _isInfraDebenture(rawText) {
    const T = String(rawText || "").toUpperCase();
    return /\bINFRA\b|\bINCENT\b|INCENTIVADA/.test(T);
  }

  // Normaliza indexer bruto → forma MD-conformant.
  function _normalizeIndexerRaw(s) {
    const u = String(s || "").toUpperCase().trim();
    if (u === "IPC-A" || u === "IPCA") return "IPCA";
    if (u === "CDI") return "CDI";
    if (u === "PRE" || u === "PRÉ" || u === "FIXEDRATE") return "PRE";
    return u;
  }

  // beehusName CRI/CRA conforme §16.5 (sem taxa, com label Pós/Pré-fixado).
  function _buildCriCraBeehusName(typeUpper, emissorClean, indexer, ddmmyyyy) {
    const label = (indexer === "PRE") ? "Pré-fixado" : "Pós-fixado";
    const datePt = _dateToPtAbbr(ddmmyyyy);
    return `${typeUpper} ${emissorClean} ${label} ${datePt}`.replace(/\s+/g, " ").trim();
  }

  // beehusName Debênture conforme §16.5 (com taxa e label).
  function _buildDebentureBeehusName(emissorClean, indexer, yieldVal, idxPct, ddmmyyyy) {
    const label = (indexer === "PRE") ? "Pré-fixado" : "Pós-fixado";
    const datePt = _dateToPtAbbr(ddmmyyyy);
    const rateFmt = _formatRateForBeehusName(indexer, yieldVal, idxPct);
    return `Debênture ${emissorClean} ${rateFmt} ${label} ${datePt}`.replace(/\s+/g, " ").trim();
  }

  // Remove prefixo de tipo redundante (CRI/CRA/DEB) do nome do emissor curto.
  function _stripTypePrefix(s) {
    return String(s || "").replace(/^(CRI|CRA|DEB)\s+/i, "").trim();
  }

  // Converte ISO YYYY-MM-DD → DD/MM/YYYY para reuso com _dateToPtAbbr.
  function _isoToDdmmyyyy(iso) {
    const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return "";
    return `${m[3]}/${m[2]}/${m[1]}`;
  }

  // Formato longo §16.1 — 6 ou 7 partes com pct% explícito + indexer separados.
  // Aceita CDB, LCA, LCI, CRI, CRA, DEB. O beehusName varia por tipo:
  //   - CDB/LCA/LCI: §15.2 (com taxa, sem label)
  //   - CRI/CRA:     §16.5 (sem taxa, com label Pós/Pré-fixado)
  //   - DEB:         §16.5 (com taxa e label)
  function _parseCriCraDebLong(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    const parts = s.split(" - ").map(p => p.trim());
    if (parts.length < 6) return null;

    // Acha o índice da parte que casa com pct (aceita "." ou "," como decimal).
    let pctIdx = -1;
    for (let i = 0; i < parts.length; i++) {
      if (/^\d+([.,]\d+)?%$/.test(parts[i])) { pctIdx = i; break; }
    }
    if (pctIdx < 3 || pctIdx >= parts.length - 1) return null;

    const header   = parts[0];
    const tokens   = header.split(/\s+/);
    const typeUp   = (tokens[0] || "").toUpperCase();
    if (!["CDB", "LCA", "LCI", "CRI", "CRA", "DEB"].includes(typeUp)) return null;
    const ticker   = tokens[tokens.length - 1];

    // emissor = todas as partes entre [1] e [pctIdx-2] (date está em pctIdx-1).
    const emissorRaw = parts.slice(1, pctIdx - 1).join(" - ").trim();
    const dateIso    = parts[pctIdx - 1];
    const pct        = Number(parts[pctIdx].replace("%", "").replace(",", "."));
    const indexerRaw = parts[pctIdx + 1] || "";
    const indexer    = _normalizeIndexerRaw(indexerRaw);

    // spread opcional: parts[pctIdx+2] se for número (não código alfanumérico).
    let spread = null;
    if (pctIdx + 2 < parts.length) {
      const cand = parts[pctIdx + 2].replace(",", ".");
      if (/^\d+(\.\d+)?$/.test(cand)) spread = Number(cand);
    }

    // Mapa de campos Beehus a partir do indexer.
    let yieldVal, idxPct;
    if (indexer === "PRE") {
      yieldVal = (spread !== null) ? spread : pct;
      idxPct = null;
    } else {
      idxPct = pct;
      yieldVal = (spread !== null) ? spread : 0;
    }

    const ddmmyyyy = _isoToDdmmyyyy(dateIso);
    if (!ddmmyyyy) return null;

    let emissorClean, beehusName, jsonType;

    if (typeUp === "CDB" || typeUp === "LCA" || typeUp === "LCI") {
      // Tenta a tabela canônica §14; se não casar, faz titlecase.
      const canonical = _getShortEmissor(emissorRaw);
      // _getShortEmissor sempre retorna algo (fallback strip+titlecase) — useful enough.
      emissorClean = canonical || _titleCaseEmissor(emissorRaw);
      jsonType = typeUp.toLowerCase();
      const datePt = _dateToPtAbbr(ddmmyyyy);
      const rateFmt = _formatRateForBeehusName(indexer, yieldVal, idxPct);
      beehusName = `${typeUp} ${emissorClean} ${rateFmt} ${datePt}`.replace(/\s+/g, " ").trim();
    } else if (typeUp === "CRI" || typeUp === "CRA") {
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = typeUp.toLowerCase();
      beehusName = _buildCriCraBeehusName(typeUp, emissorClean, indexer, ddmmyyyy);
    } else { // DEB
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = _isInfraDebenture(s) ? "infrastructureDebenture" : "debenture";
      beehusName = _buildDebentureBeehusName(emissorClean, indexer, yieldVal, idxPct, ddmmyyyy);
    }

    return {
      type: jsonType,
      emissorRaw,
      emissorShort: emissorClean,
      ticker,
      maturityIso: dateIso,
      indexer,
      yield: yieldVal,
      indexerPercentual: idxPct,
      beehusName,
    };
  }

  // Formato curto §16.2 — CRI/CRA com 4 partes, sem rate inline.
  function _parseCriCraShort(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    const parts = s.split(" - ").map(p => p.trim());
    if (parts.length < 4) return null;

    const header = parts[0];
    const tokens = header.split(/\s+/);
    const typeUp = (tokens[0] || "").toUpperCase();
    if (!["CRI", "CRA"].includes(typeUp)) return null;

    // PRE no header indica pré-fixado.
    const isPre = tokens.slice(1).some(t => t.toUpperCase() === "PRE");

    // Ticker = primeiro token após TIPO que não é "PRE" (ex.: "CRI PRE 23J..." → "23J...")
    const codeTokens = tokens.slice(1).filter(t => t.toUpperCase() !== "PRE");
    const ticker = codeTokens.length ? codeTokens[codeTokens.length - 1] : "";

    const emissorRaw  = _stripTypePrefix(parts[1]);
    const ddmmyyyy    = parts[parts.length - 1];
    if (!/^\d{2}\/\d{2}\/\d{4}$/.test(ddmmyyyy)) return null;

    const dateIso = _ddmmyyyyToIso(ddmmyyyy);
    if (!dateIso) return null;

    // Defaults §16.2: PRE → indexer=PRE, yield=null; senão Pós-fixado CDI 100%.
    const indexer = isPre ? "PRE" : "CDI";
    const yieldVal = isPre ? null : 0;
    const idxPct   = isPre ? null : 100;

    const emissorClean = _getDevedorCanonical(emissorRaw);
    const beehusName = _buildCriCraBeehusName(typeUp, emissorClean, indexer, ddmmyyyy);

    return {
      type: typeUp.toLowerCase(),
      emissorRaw,
      emissorShort: emissorClean,
      ticker,
      maturityIso: dateIso,
      indexer,
      yield: yieldVal,
      indexerPercentual: idxPct,
      beehusName,
    };
  }

  // Formato compacto de Debênture (§16) — `DEB <EMISSOR...> <INDEXER>+<SPREAD> - <DD/MM/AAAA>`.
  // Ex.: "DEB AESB IPCA+7,4588 - 15/04/2035", "DEB ALPARGAT CDI+1,059219 - 12/12/2029".
  function _parseDebCompact(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    const parts = s.split(" - ").map(p => p.trim());
    if (parts.length < 2) return null;

    const headerTokens = parts[0].split(/\s+/);
    if ((headerTokens[0] || "").toUpperCase() !== "DEB") return null;
    if (headerTokens.length < 3) return null;  // DEB + emissor (≥1 tok) + rate

    const lastTok = headerTokens[headerTokens.length - 1];
    const m = lastTok.match(/^(CDI|IPCA|IPC-A|PRE)\s*[+\-]?\s*(\d+(?:[.,]\d+)?)$/i);
    if (!m) return null;

    const indexer = _normalizeIndexerRaw(m[1]);
    const spread  = Number(m[2].replace(",", "."));
    if (!Number.isFinite(spread)) return null;

    const emissorRaw = headerTokens.slice(1, -1).join(" ").trim();
    const ddmmyyyy   = parts[1];
    if (!/^\d{2}\/\d{2}\/\d{4}$/.test(ddmmyyyy)) return null;
    const dateIso = _ddmmyyyyToIso(ddmmyyyy);
    if (!dateIso) return null;

    let yieldVal, idxPct;
    if (indexer === "PRE") { yieldVal = spread; idxPct = null; }
    else                   { yieldVal = spread; idxPct = 100; }

    const emissorClean = _getDevedorCanonical(emissorRaw);
    const jsonType = _isInfraDebenture(s) ? "infrastructureDebenture" : "debenture";
    const beehusName = _buildDebentureBeehusName(emissorClean, indexer, yieldVal, idxPct, ddmmyyyy);

    return {
      type: jsonType,
      emissorRaw,
      emissorShort: emissorClean,
      ticker: "",
      maturityIso: dateIso,
      indexer,
      yield: yieldVal,
      indexerPercentual: idxPct,
      beehusName,
    };
  }

  // ── Parser XP code prefix (PARSER_SECURITIES.md §5.5) ────────────────────
  // Format: `<L|C|B><5-6 digits><TIPO> <emissor> <DD/MM/YYYY> <rate>`
  function _parseXpCodePrefix(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    if (!s) return null;
    const m = s.match(/^([A-Z])(\d{5,6})(CDB|LCA|LCI|LCD|LF|CCB|LC|CRI|CRA|DEBI|DEB)\s+(.+?)\s+(\d{2}\/\d{2}\/\d{4})\s+(.+)$/);
    if (!m) return null;

    const [, codeLetter, codeDigits, typeToken, emissorRaw, ddmmyyyy, rateStr] = m;
    const typeUpOrig = typeToken.toUpperCase();
    const isDebi = typeUpOrig === "DEBI";
    const typeUp = isDebi ? "DEB" : typeUpOrig;
    const ticker = `${codeLetter}${codeDigits}`;

    const rate = _parseRate(rateStr);
    if (!rate || rate.yield === null) return null;
    const dateIso = _ddmmyyyyToIso(ddmmyyyy);
    if (!dateIso) return null;

    let emissorClean, beehusName, jsonType;
    if (["CDB","LCA","LCI","LCD","CCB","LC"].includes(typeUp)) {
      emissorClean = _getShortEmissor(emissorRaw);
      jsonType = typeUp.toLowerCase();
      const datePt = _dateToPtAbbr(ddmmyyyy);
      const rateFmt = _formatRateForBeehusName(rate.indexer, rate.yield, rate.indexerPercentual);
      beehusName = `${typeUp} ${emissorClean} ${rateFmt} ${datePt}`.replace(/\s+/g, " ").trim();
    } else if (typeUp === "LF") {
      emissorClean = _getShortEmissor(emissorRaw);
      jsonType = "lf";
      const label = (rate.indexer === "PRE") ? "Pré-fixado" : "Pós-fixado";
      beehusName = `LF ${emissorClean} ${label} ${_dateToPtAbbr(ddmmyyyy)}`.trim();
    } else if (typeUp === "CRI" || typeUp === "CRA") {
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = typeUp.toLowerCase();
      beehusName = _buildCriCraBeehusName(typeUp, emissorClean, rate.indexer, ddmmyyyy);
    } else { // DEB / DEBI
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = (isDebi || _isInfraDebenture(s)) ? "infrastructureDebenture" : "debenture";
      beehusName = _buildDebentureBeehusName(emissorClean, rate.indexer, rate.yield, rate.indexerPercentual, ddmmyyyy);
    }

    return {
      type: jsonType, emissorRaw, emissorShort: emissorClean, ticker,
      maturityIso: dateIso, indexer: rate.indexer, yield: rate.yield,
      indexerPercentual: rate.indexerPercentual, beehusName,
    };
  }

  // ── Parser preformatted beehusName (PARSER_SECURITIES.md §5.6) ───────────
  // Format: `<TIPO> - <TIPO> <emissor> <rate-token> <DD/Mmm/YYYY> [- meta...]`
  // Aceita também prefixos compostos `CRI/CRA - ...` e `Debênture - ...`.
  function _parsePreformattedBeehusname(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    const splitFirst = s.split(" - ");
    if (splitFirst.length < 2) return null;
    let prefix = splitFirst[0].trim();
    let body = splitFirst.slice(1).join(" - ").trim();
    let prefixUp = prefix.toUpperCase();

    let typeUp;
    if (prefixUp === "CRI/CRA") {
      const bt = body.split(/\s+/, 2);
      if (!bt.length || !["CRI","CRA"].includes(bt[0].toUpperCase())) return null;
      typeUp = bt[0].toUpperCase();
    } else if (prefixUp === "DEBÊNTURE" || prefixUp === "DEBENTURE") {
      const bt = body.split(/\s+/, 2);
      if (!bt.length || !["DEB","DEB.","DEBÊNTURE","DEBENTURE"].includes(bt[0].toUpperCase())) return null;
      typeUp = "DEB";
      body = body.replace(/^DEB\.?\s+/i, "DEB ");
    } else if (["CDB","LCA","LCI","LCD","LF","CCB","LC","CRI","CRA","DEB"].includes(prefixUp)) {
      const bt = body.split(/\s+/, 2);
      if (!bt.length || bt[0].toUpperCase() !== prefixUp) return null;
      typeUp = prefixUp;
    } else {
      return null;
    }

    // Take only the FIRST chunk of body (drop trailing metadata)
    const bodyTokens = body.split(/\s+/);
    let inner = bodyTokens.slice(1).join(" ");
    inner = inner.split(" - ", 1)[0].trim();

    // Match: <emissor> <rate-token> <date-token>
    const datePtRe = /\d{1,2}\/[A-Za-z]{3}\/\d{4}/;
    const dateBrRe = /\d{2}\/\d{2}\/\d{4}/;
    const m = inner.match(/^(.+?)\s+(\S+)\s+(\d{1,2}\/[A-Za-z]{3}\/\d{4}|\d{2}\/\d{2}\/\d{4})\s*$/);
    if (!m) return null;
    const [, emissorRaw, rateStr, dateStr] = m;

    let ddmmyyyy;
    if (datePtRe.test(dateStr) && /[A-Za-z]/.test(dateStr)) {
      const dm = dateStr.match(/(\d{1,2})\/([A-Za-z]{3})\/(\d{4})/);
      const monAbbr = dm[2].charAt(0).toUpperCase() + dm[2].slice(1).toLowerCase();
      const monIdx = _PT_MONTH_ABBR.indexOf(monAbbr);
      if (monIdx <= 0) return null;
      ddmmyyyy = `${dm[1].padStart(2,"0")}/${String(monIdx).padStart(2,"0")}/${dm[3]}`;
    } else {
      ddmmyyyy = dateStr;
    }
    const dateIso = _ddmmyyyyToIso(ddmmyyyy);
    if (!dateIso) return null;

    const rate = _parseRate(rateStr);
    if (!rate || rate.yield === null) return null;

    return _buildBondPayload(typeUp, emissorRaw, rate, ddmmyyyy, "", s, dateIso);
  }

  // ── Parser beehusName-inline (PARSER_SECURITIES.md §5.7) ─────────────────
  // Format: `<TIPO> <emissor...> <rate-token> <DD/Mmm/YYYY> [- meta...]`
  function _parseBeehusnameInline(raw) {
    const s = String(raw || "").trim().replace(/^"|"$/g, "");
    if (!s) return null;
    const first = s.split(" - ", 1)[0];
    const tokens = first.split(/\s+/);
    if (tokens.length < 4) return null;
    const typeUp = tokens[0].toUpperCase();
    if (!["CDB","LCA","LCI","LCD","LF","CCB","LC","CRI","CRA","DEB"].includes(typeUp)) return null;

    const dateToken = tokens[tokens.length - 1];
    const dm = dateToken.match(/^(\d{1,2})\/([A-Za-z]{3})\/(\d{4})$/);
    if (!dm) return null;
    const monAbbr = dm[2].charAt(0).toUpperCase() + dm[2].slice(1).toLowerCase();
    const monIdx = _PT_MONTH_ABBR.indexOf(monAbbr);
    if (monIdx <= 0) return null;
    const ddmmyyyy = `${dm[1].padStart(2,"0")}/${String(monIdx).padStart(2,"0")}/${dm[3]}`;
    const dateIso = _ddmmyyyyToIso(ddmmyyyy);
    if (!dateIso) return null;

    // Detect rate span: try multi-token (IPCA + N% / CDI + N%) first, then single.
    let rateStr, rateStartIdx;
    if (tokens.length >= 6
        && ["IPCA","IPC-A","CDI"].includes(tokens[tokens.length - 4].toUpperCase())
        && tokens[tokens.length - 3] === "+") {
      rateStr = `${tokens[tokens.length - 4]} ${tokens[tokens.length - 3]} ${tokens[tokens.length - 2]}`;
      rateStartIdx = tokens.length - 4;
    } else {
      rateStr = tokens[tokens.length - 2];
      if (rateStr.indexOf("%") === -1) return null;
      rateStartIdx = tokens.length - 2;
    }
    const rate = _parseRate(rateStr);
    if (!rate || rate.yield === null) return null;

    const emissorTokens = tokens.slice(1, rateStartIdx);
    if (!emissorTokens.length) return null;
    const emissorRaw = emissorTokens.join(" ");

    return _buildBondPayload(typeUp, emissorRaw, rate, ddmmyyyy, "", s, dateIso);
  }

  // Helper compartilhado: monta o payload final dado type + dados parseados.
  function _buildBondPayload(typeUp, emissorRaw, rate, ddmmyyyy, ticker, sourceText, dateIso) {
    let emissorClean, beehusName, jsonType;
    if (["CDB","LCA","LCI","LCD","CCB","LC"].includes(typeUp)) {
      emissorClean = _getShortEmissor(emissorRaw);
      jsonType = typeUp.toLowerCase();
      const datePt = _dateToPtAbbr(ddmmyyyy);
      const rateFmt = _formatRateForBeehusName(rate.indexer, rate.yield, rate.indexerPercentual);
      beehusName = `${typeUp} ${emissorClean} ${rateFmt} ${datePt}`.replace(/\s+/g, " ").trim();
    } else if (typeUp === "LF") {
      emissorClean = _getShortEmissor(emissorRaw);
      jsonType = "lf";
      const label = (rate.indexer === "PRE") ? "Pré-fixado" : "Pós-fixado";
      beehusName = `LF ${emissorClean} ${label} ${_dateToPtAbbr(ddmmyyyy)}`.trim();
    } else if (typeUp === "CRI" || typeUp === "CRA") {
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = typeUp.toLowerCase();
      beehusName = _buildCriCraBeehusName(typeUp, emissorClean, rate.indexer, ddmmyyyy);
    } else { // DEB
      emissorClean = _getDevedorCanonical(emissorRaw);
      jsonType = _isInfraDebenture(sourceText) ? "infrastructureDebenture" : "debenture";
      beehusName = _buildDebentureBeehusName(emissorClean, rate.indexer, rate.yield, rate.indexerPercentual, ddmmyyyy);
    }
    return {
      type: jsonType, emissorRaw, emissorShort: emissorClean, ticker,
      maturityIso: dateIso, indexer: rate.indexer, yield: rate.yield,
      indexerPercentual: rate.indexerPercentual, beehusName,
    };
  }

  // Tenta os parsers CRI/CRA/DEB + XP + preformatted + inline em sequência.
  function _parseCriCraDeb(raw) {
    return _parseCriCraDebLong(raw)
        || _parseCriCraShort(raw)
        || _parseDebCompact(raw)
        || _parseXpCodePrefix(raw)
        || _parsePreformattedBeehusname(raw)
        || _parseBeehusnameInline(raw);
  }

  // ── Fallbacks (quando o parser completo não encaixa) ─────────────────────
  function _extractMaturity(text) {
    if (!text) return "";
    const t = String(text);
    let m = t.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = t.match(/(\d{1,2})[\/\-]([A-Za-z]{3})[\/\-](\d{4})/);
    if (m) {
      const mon = _PT_MONTHS[m[2].toLowerCase()];
      if (mon) return `${m[3]}-${String(mon).padStart(2,"0")}-${String(m[1]).padStart(2,"0")}`;
    }
    m = t.match(/(\d{2})\/(\d{2})\/(\d{4})/);
    if (m) return `${m[3]}-${m[2]}-${m[1]}`;
    return "";
  }

  function _extractIndexerPct(text) {
    if (!text) return { indexer: "", pct: null };
    const t = String(text).toUpperCase();
    const m = t.match(/(\d+(?:[.,]\d+)?)\s*%\s*(?:DO\s*|DA\s*)?CDI/);
    if (m) return { indexer: "CDI", pct: Number(m[1].replace(",", ".")) };
    if (/\bCDI\b/.test(t))  return { indexer: "CDI",  pct: 100 };
    if (/\bIPCA\b/.test(t) || /\bIPC-A\b/.test(t)) return { indexer: "IPCA", pct: 100 };
    if (/\bSELIC\b/.test(t))return { indexer: "SELIC",pct: null };
    return { indexer: "", pct: null };
  }

  function _inferType(stype, text) {
    const T = String(text || "").toUpperCase();
    if (/\bCDB\b/.test(T))  return "cdb";
    if (/\bLCI\b/.test(T))  return "lci";
    if (/\bLCA\b/.test(T))  return "lca";
    if (/\bLF\b/.test(T))   return "lf";
    if (/\bDEB\b/.test(T) || /DEBENTURE/.test(T)) return "debenture";
    if (/\bFII\b/.test(T))  return "fii";
    if (/\bETF\b/.test(T))  return "etf";
    return "";
  }

  // Build a registration working-row from a mapping row. Shape:
  //   { uid, include,
  //     asset:  {unprocessedId, securityType, isin, ticker, type, taxId},  // top (info) line, read-only
  //     securityType,                                                       // editable "tipo de ativo"
  //     beehusName,                                                         // editable
  //     values: { <fieldKey>: value } }                                     // editable, keyed by config
  // `values` keys are the field `key`s from data/security_type_fields.json
  // (which are also the registration-JSON keys).
  function _buildRegRow(r) {
    const uid    = r.unprocessedSecurityId || "";
    const cand   = r.candidate || {};
    const ex     = r.extracted || {};
    const detected = r.type || "";  // classifier securityType (bond/brazilianFund/…)

    // Cadeia de parsers MD-conformantes: CDB/LCA/LCI (§15) → CRI/CRA/DEB (§16).
    const p = _parseUnprocessedId(uid) || _parseCriCraDeb(uid);

    const asset = {
      unprocessedId: uid,
      securityType:  detected,
      isin:          ex.isin || "",
      ticker:        (p && p.ticker) || ex.ticker || "",
      type:          (p && p.type)  || ex.type   || "",
      taxId:         ex.taxId || "",
    };

    // securityType inicial da linha de cadastro: o parser reconhece RF BR como
    // bond; caso contrário usa o tipo detectado pelo classificador.
    const securityType = p ? "bond" : detected;

    let beehusName, values;
    let fallbackEmissorShort = "";
    if (p) {
      beehusName = p.beehusName;
      values = {
        type: p.type,
        ticker: p.ticker || cand.mainId || "",
        maturityDate: p.maturityIso,
        indexer: p.indexer,
        indexerPercentual: p.indexerPercentual,
        yield: p.yield,
        currency: "BRL",
        country: "BR",
        subscriptionSettlementDays: 0,
        subscriptionNAVDays: 0,
        redemptionNAVDays: 0,
        redemptionSettlementDays: 0,
      };
    } else {
      // Fallback: quando o parser rígido (grade de formatos §15/§16) não casa,
      // prioriza os campos já extraídos pelo matcher (extract_features, no
      // security_matcher.py — mesma origem do tooltip de diagnóstico), que são
      // mais confiáveis do que reprocessar o uid do zero. Heurísticas antigas
      // (_extractMaturity/_extractIndexerPct/_inferType) só cobrem o que `ex`
      // não tiver.
      const instrumentType = _EXTRACTED_INSTRUMENT_TO_TYPE[String(ex.instrument || "").toUpperCase()];
      const isDeb = ex.instrument === "DEB" || ex.instrument === "DEBENTURE";
      const inferredType = instrumentType
        ? (isDeb ? (_isInfraDebenture(uid) ? "infrastructureDebenture" : "debenture") : instrumentType)
        : _inferType(detected, uid);

      const emissorRaw = ex.issuer || "";
      fallbackEmissorShort = emissorRaw
        ? ((isDeb || ex.instrument === "CRI" || ex.instrument === "CRA")
            ? _getDevedorCanonical(emissorRaw) : _getShortEmissor(emissorRaw))
        : "";

      const ix = _extractIndexerPct(uid);
      const indexerVal = ex.indexer || ix.indexer;

      beehusName = cand.beehusName || "";
      values = {
        type: inferredType,
        ticker: cand.mainId || "",
        maturityDate: ex.maturity_date || _extractMaturity(uid),
        indexer: indexerVal,
        indexerPercentual: ix.pct,
        // ex.rate não distingue spread de percentual do indexador — só dá pra
        // usar sem ambiguidade quando pré-fixado (o rate É o yield).
        yield: (indexerVal === "PRE" && ex.rate) ? Number(ex.rate) : null,
        currency: "BRL",
        country: "BR",
        subscriptionSettlementDays: 0,
        subscriptionNAVDays: 0,
        redemptionNAVDays: 0,
        redemptionSettlementDays: 0,
      };
    }

    const row = { uid, include: true, asset, securityType, beehusName, values };
    // Guardados para o recálculo do beehusName ao trocar o `type` no dropdown
    // (_recomputeBondBeehusName) — evita reprocessar o texto bruto do zero.
    row._rawUid = uid;
    row._emissorShort = (p && p.emissorShort) || fallbackEmissorShort || "";
    if (securityType === "bond" && values.type) {
      // A partir daqui a fórmula de nome vem sempre de _recomputeBondBeehusName
      // (beehusName-regra-final.md §4.2-4.4), não do `p.beehusName` do parser —
      // que ainda reflete a regra antiga (com taxa/rótulo).
      row.beehusName = _recomputeBondBeehusName(row);
    }
    _regEnsureDefaults(row);
    return row;
  }

  // Ensure `values` has an entry for every field configured for the row's
  // securityType (so newly-shown fields after a type change start from the
  // config default). Never clobbers a value already present.
  function _regEnsureDefaults(row) {
    _regFieldsFor(row.securityType).forEach(f => {
      if (row.values[f.key] === undefined) {
        row.values[f.key] = (f.default !== undefined) ? f.default
                          : (f.input === "number" ? null : "");
      }
    });
  }

  function _openRegistrationModal() {
    _renderRegistrationTable();
    document.getElementById("registration-modal").style.display = "flex";
  }

  // rebuild=true reconstrói _regRows a partir das linhas marcadas no Mapeamento;
  // rebuild=false apenas re-renderiza os cards preservando os _regRows atuais
  // (usado após trocar o tipo ou aplicar valor em lote, sem perder edições).
  // Tipos para as abas: todos os tipos configurados (ordem do JSON) MAIS
  // qualquer tipo presente nos ativos que não esteja configurado (anexado ao
  // fim) — assim o tipo de um card sempre tem uma aba e nunca some da tela.
  function _regAllTypes() {
    const cfg = Object.keys(_secTypeFields || {});
    const present = [...new Set(_regRows.map(r => r.securityType).filter(Boolean))];
    if (!cfg.length) return present;
    return cfg.concat(present.filter(t => !cfg.includes(t)));
  }

  // Aba padrão = primeira aba com ao menos um ativo (senão a primeira).
  function _regDefaultTab(types) {
    const withRows = types.find(t => _regRows.some(r => r.securityType === t));
    return withRows || types[0] || null;
  }

  function _regSetTab(t) {
    _regActiveTab = t;
    _renderRegistrationTable(false);
  }

  // Barra de abas: TODOS os tipos configurados; abas sem ativos ficam
  // desabilitadas (cinza, não clicáveis). A aba ativa fica destacada.
  function _regRenderTabs(types) {
    const tabsEl = document.getElementById("reg-tabs");
    if (!tabsEl) return;
    tabsEl.innerHTML = types.map(t => {
      const count  = _regRows.filter(r => r.securityType === t).length;
      const label  = (_secTypeFields[t] && _secTypeFields[t].label) || t;
      const active = t === _regActiveTab;
      if (!count) {
        return `<span class="px-2.5 py-1 text-[11px] rounded-t text-gray-400 cursor-default"
          title="Sem ativos deste tipo">${_escHtml(label)} (0)</span>`;
      }
      const cls = active
        ? "bg-white border border-b-white border-gray-300 text-blue-700 font-semibold -mb-px"
        : "bg-gray-50 border border-transparent text-gray-600 hover:text-blue-600 hover:bg-white";
      return `<button onclick="_regSetTab('${_escHtml(t).replace(/'/g,"\\'")}')"
        class="px-2.5 py-1 text-[11px] rounded-t ${cls}">${_escHtml(label)} (${count})</button>`;
    }).join("");
  }

  function _renderRegistrationTable(rebuild = true) {
    if (rebuild) {
      // Only rows whose mapping checkbox is checked AND that are currently
      // visible in the main Mapeamento table (respects Match mínimo (%) / Tipo /
      // Identificado filters).
      const checkedUids = new Set();
      _visibleCheckedMappingRows().forEach(tr => {
        if (tr?.dataset?.uid) checkedUids.add(tr.dataset.uid);
      });
      const picked = _mappingRows.filter(r => checkedUids.has(r.unprocessedSecurityId));
      _regRows   = picked.map(_buildRegRow);
      _regRowSrc = picked;  // keep original mapping rows for tooltip lookup
      _regActiveTab = null;  // recomputa a aba padrão para o novo conjunto
    }

    const types = _regAllTypes();
    if (!_regActiveTab || !types.includes(_regActiveTab)) {
      _regActiveTab = _regDefaultTab(types);
    }
    _regRenderTabs(types);

    const container = document.getElementById("reg-cards");
    const msg     = document.getElementById("reg-msg");
    const countEl = document.getElementById("reg-count");
    countEl.textContent = `${_regRows.length} linha${_regRows.length !== 1 ? "s" : ""}`;
    if (!_regRows.length) {
      container.innerHTML = "";
      msg.style.display = "block";
      return;
    }
    msg.style.display = "none";
    // Renderiza só os cards da aba ativa, preservando o índice GLOBAL em
    // _regRows (os handlers — _regSetVal, _regSetType… — usam esse índice).
    const cards = _regRows
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.securityType === _regActiveTab)
      .map(({ r, i }) => _buildRegCardHtml(r, i));
    container.innerHTML = cards.length
      ? cards.join("")
      : `<p class="text-center text-gray-400 py-6 text-xs">Nenhum ativo nesta aba.</p>`;
    _regUpdateCount();
  }

  // Renderiza um card (3 seções) para o ativo i:
  //   linha 1 = info do ativo (leitura) + seletor de tipo de ativo;
  //   linha 2 = beehusName;
  //   seção 3 = campos específicos do tipo (da aba ativa), ou aviso quando o
  //             tipo ainda não tem campos configurados.
  function _buildRegCardHtml(r, i) {
    const a = r.asset || {};
    const info = (label, val) =>
      `<span class="whitespace-nowrap"><span class="text-gray-400">${label}:</span> ` +
      `<span class="text-gray-700">${val ? _escHtml(val) : "—"}</span></span>`;

    // Dropdown "tipo de ativo" (securityType). Usa a lista global; se ainda não
    // carregou, mostra ao menos o tipo atual para não esvaziar o select.
    const stypeList = _securityTypes.length ? _securityTypes : [r.securityType];
    const typeOpts = stypeList
      .map(t => `<option value="${_escHtml(t)}"${t === r.securityType ? " selected" : ""}>${_escHtml(t)}</option>`)
      .join("");

    const base = "border rounded px-1.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-blue-400";
    const fieldsHtml = _regFieldsFor(r.securityType).map(f => {
      const v = r.values[f.key];
      const w = f.width ? ` style="min-width:${f.width}px"` : "";
      const titleAttr = f.title ? ` title="${_escHtml(f.title)}"` : "";
      let input;
      if (f.input === "select") {
        const opts = (f.options || []).map(o =>
          `<option value="${_escHtml(o.value)}"${v === o.value ? " selected" : ""}>${_escHtml(o.label)}</option>`
        ).join("");
        input = `<select${w} class="${base}" onchange="_regOnFieldChange(${i},'${f.key}',this.value)">
          <option value=""${!v ? " selected" : ""}></option>
          ${opts}
        </select>`;
      } else if (f.input === "number") {
        input = `<input type="number" step="any" value="${v ?? ""}"${w}
          class="${base} text-right"
          oninput="_regSetVal(${i},'${f.key}',this.value===''?null:Number(this.value))" />`;
      } else if (f.input === "date") {
        input = `<input type="date" value="${_escHtml(v || "")}"${w}
          class="${base}" oninput="_regSetVal(${i},'${f.key}',this.value)" />`;
      } else {
        input = `<input type="text" value="${_escHtml(v ?? "")}"${w}
          class="${base}" oninput="_regSetVal(${i},'${f.key}',this.value)" />`;
      }
      const reqMark = f.required ? '<span class="text-red-500"> *</span>' : "";
      return `<label class="flex flex-col gap-0.5"${titleAttr}>
        <span class="text-[9px] uppercase tracking-wide text-gray-400">${_escHtml(f.label)}${reqMark}</span>
        ${input}
      </label>`;
    }).join("");

    const typeLabel = (_secTypeFields[r.securityType] && _secTypeFields[r.securityType].label) || r.securityType;
    const fieldsSection = fieldsHtml
      ? `<div class="flex items-end gap-2 px-3 py-2 flex-wrap border-t border-gray-100 bg-gray-50/60">
           ${fieldsHtml}
         </div>`
      : `<div class="px-3 py-2 border-t border-gray-100 bg-gray-50/60 text-[10px] text-gray-400 italic">
           Campos de "${_escHtml(typeLabel)}" ainda não definidos.
         </div>`;

    return `
    <div class="border border-gray-200 rounded-lg mb-2 bg-white">
      <!-- Linha 1: informações do ativo (leitura) + seletor de tipo -->
      <div class="flex items-center gap-3 px-3 py-1.5 border-b border-gray-100 bg-gray-50">
        <input type="checkbox" class="reg-include" ${r.include ? "checked" : ""}
          onchange="_regRows[${i}].include=this.checked;_regUpdateCount()" />
        <span class="font-mono text-[10px] text-gray-500 truncate max-w-[280px] cursor-help"
          onmouseenter="_regShowTip(event, ${i})" onmousemove="_regMoveTip(event)" onmouseleave="_regHideTip()"
        >${_escHtml(a.unprocessedId)}</span>
        <label class="flex items-center gap-1 whitespace-nowrap">
          <span class="text-[9px] uppercase tracking-wide text-blue-500 font-semibold">tipo</span>
          <select class="${base} font-semibold bg-white" onchange="_regSetType(${i}, this.value)">${typeOpts}</select>
        </label>
        <span class="flex items-center gap-3 text-[10px] ml-auto flex-wrap justify-end">
          ${info("type", a.type)}
          ${info("ISIN", a.isin)}
          ${info("ticker", a.ticker)}
          ${info("taxId", a.taxId)}
        </span>
      </div>
      <!-- Linha 2: beehusName -->
      <div class="px-3 py-2">
        <label class="flex flex-col gap-0.5">
          <span class="text-[9px] uppercase tracking-wide text-gray-400">beehusName</span>
          <input type="text" value="${_escHtml(r.beehusName ?? "")}"
            class="${base} w-full" oninput="_regRows[${i}].beehusName=this.value" />
        </label>
      </div>
      <!-- Seção 3: campos do tipo (variam conforme a aba) -->
      ${fieldsSection}
    </div>`;
  }

  function _regSetVal(i, key, value) {
    if (_regRows[i]) _regRows[i].values[key] = value;
  }

  // Setter para campos com efeito colateral no beehusName sugerido (hoje: `type`
  // em bond). Sempre sobrescreve o beehusName atual, mesmo se editado manualmente
  // pelo usuário — decisão explícita (2026-07-03): trocar o subtipo sempre
  // recalcula a sugestão, não preserva edição anterior.
  function _regOnFieldChange(i, key, value) {
    const row = _regRows[i];
    if (!row) return;
    row.values[key] = value;
    if (key === "type" && row.securityType === "bond") {
      row.beehusName = _recomputeBondBeehusName(row);
    }
    _renderRegistrationTable(false);
  }

  // Prefixo de exibição por `type` de bond — beehusName-regra-final.md §4.2-4.4.
  const _BOND_TYPE_PREFIX = {
    cdb: "CDB", cra: "CRA", cri: "CRI", lca: "LCA", lci: "LCI", lcd: "LCD",
    lf: "LF", "lf-sub": "LF", lig: "LIG", lc: "LC", ccb: "CCB",
    debenture: "DEB", infrastructureDebenture: "DEB", np: "NP",
  };
  // Tipos sem emissor no nome (§4.2, nota LIG/NP).
  const _BOND_TYPE_NO_EMISSOR = new Set(["lig", "np"]);
  // Mapa do `instrument` já extraído por extract_features (security_matcher.py,
  // via _BOND_INSTRUMENT_RE) para o `type` do dropdown — usado para pré-
  // selecionar o subtipo quando o parser rígido (§15/§16) não reconhece o
  // formato bruto, mas o matcher já identificou o instrumento com confiança.
  // CPRF, FIDC, LFSN, LFS, FND não têm correspondência confiável no domínio de
  // 20 tipos — ficam de fora de propósito (dropdown fica em branco nesses casos).
  const _EXTRACTED_INSTRUMENT_TO_TYPE = {
    CDB: "cdb", CRA: "cra", CRI: "cri", LCA: "lca", LCI: "lci", LCD: "lcd",
    CCB: "ccb", LF: "lf", LIG: "lig", CDCA: "cd",
    DEB: "debenture", DEBENTURE: "debenture",  // desambiguado p/ infrastructureDebenture em _buildRegRow
  };
  // Tipos sem fórmula definida (gap documentado) — beehusName = texto bruto do
  // ativo, sem transformação (decisão do usuário, 2026-07-03).
  const _BOND_TYPE_PASSTHROUGH = new Set(["inflation", "precatorio", "over"]);

  // Limpeza de nome para bond internacional (fixed/floating) — §4.1: ponto→vírgula
  // em taxas, remove " - " antes de taxa, remove sufixo ISIN, data → inglês.
  function _cleanOffshoreBondName(raw) {
    let s = String(raw || "");
    s = s.replace(/(\d+)\.(\d+)/g, "$1,$2");
    s = s.replace(/\s+-\s+(?=\d+[.,]\d+)/g, " ");
    s = s.replace(/\s*-\s*[A-Z]{2}[A-Z0-9]{10}\s*$/, "");
    const EN = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    s = s.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/, (_, y, m, d) => `${d}/${EN[parseInt(m, 10)]}/${y}`);
    s = s.replace(/\b(\d{1,2})-(\d{2})-(\d{4})\b/, (_, d, m, y) => `${d}/${EN[parseInt(m, 10)]}/${y}`);
    return s.trim();
  }

  // Recalcula o beehusName sugerido de uma linha `bond` para o `type` selecionado
  // no dropdown. Usa o emissor já extraído na montagem da linha (`row._emissorShort`)
  // e a data/indexador já em `row.values` — não reprocessa o texto bruto do zero.
  function _recomputeBondBeehusName(row) {
    const type = row.values.type;
    const rawUid = row._rawUid || row.uid || "";
    if (!type) return rawUid;
    if (_BOND_TYPE_PASSTHROUGH.has(type)) return rawUid;
    if (type === "fixed" || type === "floating") return _cleanOffshoreBondName(rawUid);

    const emissorShort = row._emissorShort || "";
    const ddmmyyyy = _isoToDdmmyyyy(row.values.maturityDate || "");
    const datePt = ddmmyyyy ? _dateToPtAbbr(ddmmyyyy) : "";
    const isPre = row.values.indexer === "PRE";

    let prefix;
    if (type === "cd") {
      prefix = /\bCDCA\b/i.test(rawUid) ? "CDCA" : "CD";
    } else {
      prefix = _BOND_TYPE_PREFIX[type] || type.toUpperCase();
    }

    const parts = [prefix];
    if (!_BOND_TYPE_NO_EMISSOR.has(type) && emissorShort) parts.push(emissorShort);
    if (isPre) parts.push("Pré");
    if (datePt) parts.push(datePt);
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }

  // Troca o securityType (tipo de ativo) de um card e re-renderiza para exibir
  // os campos daquele tipo (preservando valores das chaves em comum).
  function _regSetType(i, newType) {
    const row = _regRows[i];
    if (!row) return;
    row.securityType = newType;
    _regEnsureDefaults(row);
    // Segue o card para a aba do novo tipo (senão ele "sumiria" da aba atual).
    _regActiveTab = newType;
    _renderRegistrationTable(false);
  }

  function _regSelectAll(checked) {
    _regRows.forEach(r => r.include = checked);
    document.querySelectorAll("#reg-cards input.reg-include").forEach(cb => cb.checked = checked);
    _regUpdateCount();
  }

  function _regUpdateCount() {
    const n = _regRows.filter(r => r.include).length;
    document.getElementById("reg-count").textContent = `${n} de ${_regRows.length} selecionado${n !== 1 ? "s" : ""}`;
  }

  // Aplica um valor a todas as linhas marcadas. `securityType` troca o tipo de
  // ativo (e materializa os defaults do novo tipo); os demais campos escrevem em
  // `values[field]` (afetam só as linhas cujo tipo expõe aquele campo).
  function _regBulkSet(field) {
    const v = prompt(`Aplicar valor de "${field}" a todas as linhas marcadas:`);
    if (v === null) return;
    _regRows.forEach(r => {
      if (!r.include) return;
      if (field === "securityType") { r.securityType = v; _regEnsureDefaults(r); }
      else { r.values[field] = v; }
    });
    // Mudança de tipo em lote move os cards para a aba do novo tipo.
    if (field === "securityType" && v) _regActiveTab = v;
    _renderRegistrationTable(false);
  }

  function _generateRegistrationJSON() {
    const selected = _regRows.filter(r => r.include);
    if (!selected.length) { alert("Nenhuma linha selecionada."); return; }

    const payload = selected.map(r => {
      // Estruturais (sempre presentes), depois os campos configurados para o
      // securityType — cada campo controla o próprio envio via `include`:
      //   always    → sempre envia (usa default quando vazio);
      //   ifPresent → só envia quando preenchido.
      // `transform: upperIndexer` normaliza indexer p/ MAIÚSCULAS (IPC-A→IPCA).
      const entry = {
        beehusName:   r.beehusName || "",
        securityType: r.securityType || "",
      };
      _regFieldsFor(r.securityType).forEach(f => {
        let v = r.values[f.key];
        if (f.transform === "upperIndexer") {
          v = String(v || "").toUpperCase().replace("IPC-A", "IPCA");
        }
        const isEmpty = (v === null || v === undefined || v === "");
        if (f.input === "number") {
          if (f.include === "always")  entry[f.key] = isEmpty ? (f.default ?? 0) : Number(v);
          else if (!isEmpty)           entry[f.key] = Number(v);
        } else {  // text | date
          if (f.include === "always")  entry[f.key] = isEmpty ? (f.default ?? "") : v;
          else if (!isEmpty)           entry[f.key] = v;
        }
      });
      entry.walletIds  = [];
      entry.companyIds = [];
      entry.feederIds  = [];
      return entry;
    });

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `registration_${_currentCompanyId}_${_currentDate}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Colunas Carteira/Empresa (só no modo cross-empresa) — substituem PU/Preço,
  // que ficam sempre vazios ali (o /match roda sem companyId quando cruza
  // empresas, então não há preço/PU pra resolver). Uma linha pode ter várias
  // carteiras/empresas (mesmo ativo pendente em mais de uma) — mostra o nome
  // quando é só uma, ou "N empresas"/"N carteiras" quando é mais.
  function _walletCompanyCells(r) {
    const wallets = r.wallets || [];
    const companies = r.companies || [];
    const walletCell = wallets.length === 1
      ? `<span class="text-[10px] text-gray-600 truncate" title="${_escHtml(wallets[0].name)}">${_escHtml(wallets[0].name || wallets[0].id)}</span>`
      : `<span class="text-[10px] text-gray-500">${wallets.length} carteiras</span>`;
    const companyCell = companies.length === 1
      ? `<span class="text-[10px] text-gray-700 truncate" title="${_escHtml(companies[0].name)}">${_escHtml(companies[0].name)}</span>`
      : companies.length > 1
        ? `<span class="text-[10px] text-gray-700" title="${_escHtml(companies.map(c => c.name).join(', '))}">${companies.length} empresas</span>`
        : `<span class="text-gray-400 text-[10px]">—</span>`;
    return `
      <td class="px-2 py-1.5 truncate">${companyCell}</td>
      <td class="px-2 py-1.5 truncate">${walletCell}</td>`;
  }

  // ── Render mapeamento ─────────────────────────────────────────────────────
  // Single row renderer used both for the immediate skeleton (candidate=null,
  // type="") and for the post-match render. Keeping one source of truth means
  // a manual selection made while the matcher is still running uses the same
  // markup as the server-suggested row that replaces it.
  function _uidTooltip(r) {
    const f = r.extracted || {};
    const t = r.type || '';
    const conf = r.typeConfidence ? ' (' + (r.typeConfidence * 100).toFixed(0) + '%)' : '';

    function fmtIso(s) {
      if (!s) return '';
      const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? m[3] + '/' + m[2] + '/' + m[1] : s;
    }

    // Per-type feature schema: [label, extracted_key, optional_formatter]
    const SCHEMAS = {
      brazilianFund:          [['CNPJ',        'taxId'],
                               ['Cód. fundo',  'fund_code'],
                               ['ISIN',        'isin'],
                               ['Nome',        'name']],
      stockEtf:               [['Ticker',      'ticker'],
                               ['ISIN',        'isin'],
                               ['Nome',        'name']],
      bond:                   [['Instrumento', 'instrument'],
                               ['Sub-tipo',    'bond_type'],
                               ['CETIP',       'cetip_code'],
                               ['Cód. interno','internal_code'],
                               ['ISIN',        'isin'],
                               ['Emissor',     'issuer'],
                               ['Indexador',   'indexer'],
                               ['Taxa',        'rate'],
                               ['Vencimento',  'maturity_date', fmtIso]],
      brazilianGovernmentBond:[['Tipo',        'bond_type'],
                               ['Cupom',       'coupon'],
                               ['Cód. Selic',  'selic_code'],
                               ['Indexador',   'indexer'],
                               ['ISIN',        'isin'],
                               ['Vencimento',  'maturity_date', fmtIso]],
      fund:                   [['ISIN',        'isin'],
                               ['Cód. externo','external_code'],
                               ['Nome',        'name']],
      futures:                [['Ticker',      'ticker'],
                               ['Contrato',    'contract'],
                               ['ISIN',        'isin']],
      options:                [['Ticker',      'ticker'],
                               ['Subjacente',  'underlying'],
                               ['Tipo',        'option_type'],
                               ['Strike',      'strike'],
                               ['Vencimento',  'expiry', fmtIso],
                               ['Mês venc.',   'expiry_month'],
                               ['Ano venc.',   'expiry_year'],
                               ['ISIN',        'isin']],
      otc:                    [['Cód. externo','external_code'],
                               ['ISIN',        'isin'],
                               ['Emissor',     'issuer'],
                               ['Nome',        'name']],
      brazilianRepo:          [['ISIN',        'isin'],
                               ['Emissor',     'issuer'],
                               ['Nome',        'name']],
    };

    const lines = [r.unprocessedSecurityId, '', 'Tipo: ' + (t || '—') + conf];
    const fields = SCHEMAS[t] || [];
    fields.forEach(function(spec) {
      const label = spec[0], key = spec[1], fmt = spec[2];
      let val = f[key] || '';
      if (val && fmt) val = fmt(val);
      lines.push(label + ': ' + (val || '—'));
    });

    // Generic codes — auto-extracted from the uid (shown for all types when present)
    [1, 2, 3].forEach(function(i) {
      const v = f['generic_code_' + i];
      if (v) lines.push('Cód. genérico ' + i + ': ' + v);
    });

    // Complement tokens (shown for all types when present)
    [1, 2, 3].forEach(function(i) {
      const v = f['complement_' + i];
      if (v) lines.push('Complemento ' + i + ': ' + v);
    });

    // Fallback for unrecognised types — show whatever was extracted
    if (!fields.length) {
      [['ISIN','isin'],['CNPJ','taxId'],['Ticker','ticker'],
       ['Instrumento','instrument'],['Nome','name']].forEach(function(s) {
        if (f[s[1]]) lines.push(s[0] + ': ' + f[s[1]]);
      });
    }

    return lines.join('\n');
  }

