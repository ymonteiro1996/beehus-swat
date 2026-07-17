/* Conciliação (Mov.) — movimentar, plano de implementação e modais.
   Escopo global compartilhado; ordem importa. */
function _targetDate() { return document.getElementById("target-date-input").value; }
function _post(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin" }, body: JSON.stringify(body) }).then(r => r.json());
}
/* Dispara o download de um Blob (cria <a> temporário, clica e revoga a URL). */
function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function _movProgress(t) { const el = document.getElementById("mov-progress"); if (el) el.textContent = t ? `movimentando ${t}` : ""; }
/* Status no cabeçalho do detalhamento p/ o usuário acompanhar ações que levam
   alguns segundos (reprojeção, criação) e não achar que travou.
   kind: "" = em andamento (com spinner) · "ok" = sucesso · "error" = falha. */
function _detStatus(msg, kind) {
  const el = document.getElementById("det-status"); if (!el) return;
  if (!msg) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.style.color = kind === "error" ? "#b91c1c" : kind === "ok" ? "#166534" : "#6b7280";
  const spin = (kind === "ok" || kind === "error") ? "" : `<span class="spin" style="display:inline-block;vertical-align:-2px;margin-right:4px"></span>`;
  el.innerHTML = spin + esc(msg);
  el.classList.remove("hidden");
}

/* Reprojeta EM LOTE as carteiras dadas (mesma rota da grade) e atualiza _movResults +
   as linhas. Usado pelo botão Movimentar E p/ refrescar o preview após uma escrita
   (envio de provisões/IRRF/transações/preços/upload) — a projeção reflete o que acabou
   de ser gravado. Resultados marcados `_lite` (sem fluxos-alvo do navPackage, só
   exibição); o detalhe recarrega a carteira completa ao abrir (openDetail).
   UMA requisição p/ TODO o conjunto: o servidor faz o prefetch em LOTE (1 chamada por
   endpoint Beehus em vez de ~10 por carteira). Sempre resolve (erros viram state:error
   por carteira) p/ poder encadear `.then`. */
function _reprojectWids(wids) {
  if (!wids.length) return Promise.resolve();
  return _post("/api/conciliacao-mov/movimentar-batch", { companyId: _company, walletIds: wids, sourceDate: _date, targetDate: _targetDate() })
    .then(resp => {
      const byId = {};
      ((resp && resp.results) || []).forEach(d => { if (d && d.walletId) byId[d.walletId] = d; });
      wids.forEach(w => {
        const d = byId[w];
        if (d && !d.error) _movResults[w] = { state: "done", _lite: true, ...d };
        else _movResults[w] = { ..._movResults[w], state: "error",
                                error: (d && d.error) || (resp && resp.error) || "falha ao projetar" };
        _updateRow(w);
      });
    })
    .catch(() => { wids.forEach(w => { _movResults[w] = { ..._movResults[w], state: "error", error: "falha ao projetar" }; _updateRow(w); }); });
}

function movimentarSelected() {
  const td = _targetDate();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(td) || !(td > _date)) { alert("Data-alvo inválida (deve ser posterior à origem)."); return; }
  const wids = _renderedRows.filter(r => _selWallets.has(r.walletId)).map(r => r.walletId);
  if (!wids.length) return;
  const btn = document.getElementById("movimentar-btn");
  btn.disabled = true; btn.textContent = "Movimentando...";
  _movProgress(`0/${wids.length}`);
  wids.forEach(w => { _movResults[w] = { state: "loading" }; _updateRow(w); });
  _reprojectWids(wids)
    .then(() => { btn.textContent = "Movimentar"; _movProgress(""); _syncSelHeader(); });
}
function _movimentarOne(wid, td) {
  return _post("/api/conciliacao-mov/movimentar", { companyId: _company, walletId: wid, sourceDate: _date, targetDate: td })
    .then(d => {
      // Em erro, preserva os dados anteriores (targetDate etc.) — uma reprojeção que
      // falha (ex.: 429) não deve apagar o detalhe já carregado.
      if (d && d.error) _movResults[wid] = { ..._movResults[wid], state: "error", error: d.error };
      else _movResults[wid] = { state: "done", ...d };
    })
    .catch(() => { _movResults[wid] = { ..._movResults[wid], state: "error", error: "falha ao projetar" }; })
    .finally(() => _updateRow(wid));
}

function _selectedDoneWids() {
  return _renderedRows.filter(r => _selWallets.has(r.walletId) && (_movResults[r.walletId] || {}).state === "done").map(r => r.walletId);
}

/* ══════════ Implementar — modal de preview + aplicação em LOTE ══════════
   Um único botão substitui os antigos envios verdes + "Baixar .xlsx". O modal mostra o
   resumo do que será aplicado (5 grupos) + o preview do upload; "Confirmar tudo" aplica
   na ORDEM: preços de execução → IRRF → transações de ajuste → provisões → upload da
   unprocessed. Cada rota reprojeta no servidor (vê o passo anterior); ao final reprojeta
   a grade. Erro de backend num passo é registrado e segue; falha de rede aborta o resto. */
let _implementWids = [];
function _provCount(r) { return ((r.provisions || []).length + (r.reconProvisions || []).length); }
// Nº de linhas de ativo no upload: um ativo com >1 lote (unprocessedId no mesmo
// securityId) vira N linhas SEPARADAS (não concatena).
function _rowUploadLines(rw) { return (rw.lots && rw.lots.length > 1) ? rw.lots.length : 1; }
function _implementPlan(wids) {
  let exec = 0, irrf = 0, recon = 0, prov = 0, uprows = 0;
  wids.forEach(w => {
    const r = _movResults[w] || {};
    exec += (r.executionPriceFixes || []).length;
    irrf += (r.irrf || []).filter(e => !e.covered).length;
    recon += (r.reconTransactions || []).length;
    prov += _provCount(r);
    uprows += (r.rows || []).reduce((n, rw) => n + _rowUploadLines(rw), 0) + 1;   // +1 = linha de caixa
  });
  return { exec, irrf, recon, prov, uprows };
}
function _implChecked(id) { const el = document.getElementById(id); return !!(el && el.checked && !el.disabled); }
function _implShiftAll(on) { document.querySelectorAll("#implement-body .impl-shift").forEach(cb => { cb.checked = on; }); }
function _renderImplementBody(wids) {
  const p = _implementPlan(wids);
  const td = _targetDate();
  // Linha COM checkbox (passo 2 — o usuário escolhe o que subir). Marcada por padrão
  // quando há algo a aplicar; desabilitada e "—" quando não há.
  const cbLine = (id, label, n, suffix) => {
    const on = n > 0;
    return `<label style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f3f4f6;${on ? "cursor:pointer" : ""}">
       <span style="display:flex;align-items:center;gap:8px">
         <input type="checkbox" id="${id}" ${on ? "checked" : "disabled"} style="width:14px;height:14px">
         <span class="${on ? "text-gray-700" : "text-gray-400"}">${esc(label)}</span></span>
       <strong class="${on ? "" : "text-gray-300"}">${on ? n + (suffix || "") : "—"}</strong></label>`;
  };
  // Passo 1 (upload da unprocessed) é SEMPRE aplicado — sem checkbox, com cadeado.
  const lockLine = `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f3f4f6">
       <span style="display:flex;align-items:center;gap:8px">
         <span title="Sempre aplicado" style="width:14px;text-align:center">🔒</span>
         <span class="text-gray-700">Upload da unprocessed (data-alvo)</span></span>
       <strong>${p.uprows} linha(s)</strong></div>`;
  const summary = `<div class="mb-3" style="border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px">
    <div class="text-[10px] uppercase tracking-widest text-gray-400 mb-1">Será aplicado no Beehus — nesta ordem</div>
    ${lockLine}
    ${cbLine("impl-opt-exec", "Preços de execução", p.exec, " a corrigir")}
    ${cbLine("impl-opt-irrf", "IRRF (taxes) ausente", p.irrf, " a criar")}
    ${cbLine("impl-opt-recon", "Transações de ajuste", p.recon, " a criar")}
    ${cbLine("impl-opt-prov", "Provisões geradas", p.prov, " a criar")}
  </div>`;
  // Passo 3 — deslocar as provisões que LIQUIDAM na data-alvo (cópia com data
  // deslocada). Seleção opcional, DESMARCADA por padrão (cria dado novo no sistema).
  const shiftRows = [];
  wids.forEach(w => {
    const r = _movResults[w] || {};
    (r.liquidatingProvisions || []).forEach(pv => { if (pv.id) shiftRows.push({ wid: w, wn: r.walletName || w, p: pv }); });
  });
  let shiftSection = "";
  if (shiftRows.length) {
    const rows = shiftRows.map(({ wid, wn, p: pv }) => `<tr>
      <td class="rp-tbl-left" style="width:28px"><input type="checkbox" class="impl-shift" data-wid="${esc(wid)}" data-id="${esc(pv.id)}" style="width:14px;height:14px"></td>
      <td class="rp-tbl-left text-[10px] text-gray-500">${esc(wn)}</td>
      <td class="rp-tbl-left">${esc(pv.securityName || pv.securityId || "—")}</td>
      <td class="rp-tbl-left text-[10px] text-gray-500">${esc(pv.provisionType || "—")}</td>
      <td class="${nz(pv.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(pv.balance)}</td>
      <td class="rp-tbl-left">${_liqMatchPill(pv)}</td></tr>`).join("");
    shiftSection = `<div class="mb-3" style="border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div class="text-[10px] uppercase tracking-widest text-gray-400">Provisões que liquidam na data — deslocar p/ a data-alvo</div>
        <button type="button" onclick="_implShiftAll(true)" class="fb-chip" data-kind="nav" style="margin-left:auto">Marcar todas</button>
        <button type="button" onclick="_implShiftAll(false)" class="fb-chip" data-kind="nav">Limpar</button>
      </div>
      <div class="text-[11px] text-gray-500 mb-1">Cria uma <b>cópia</b> de cada selecionada com <b>início = ${esc(td)}</b> e <b>liquidação = ${esc(td)} + 1 dia útil</b> (a original é mantida).</div>
      <div style="overflow:auto;max-height:200px"><table class="rp-tbl"><thead><tr>
        <th class="rp-tbl-left" style="width:28px"></th><th class="rp-tbl-left">Carteira</th><th class="rp-tbl-left">Ativo</th><th class="rp-tbl-left">Tipo</th><th style="text-align:right">Saldo</th><th class="rp-tbl-left">Caixa</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div>`;
  }
  const head = `<th class="rp-tbl-left">Carteira</th><th class="rp-tbl-left">Ativo</th><th style="text-align:right">Qtd</th><th style="text-align:right">PU</th><th style="text-align:right">Saldo</th>`;
  const body = [];
  wids.forEach(w => {
    const r = _movResults[w] || {};
    const wn = esc(r.walletName || w);
    (r.rows || []).forEach(rw => {
      // >1 lote (unprocessedId no mesmo securityId) → uma linha por lote (o que sobe).
      const lines = (rw.lots && rw.lots.length > 1)
        ? rw.lots.map(l => ({ ativo: l.unprocessedId, quantity: l.quantity, pu: l.pu, balance: l.balance }))
        : [{ ativo: rw.securityName || rw.unprocessedId, quantity: rw.quantity, pu: rw.pu, balance: rw.balance }];
      lines.forEach(ln => body.push(`<tr>
        <td class="rp-tbl-left text-[10px] text-gray-500">${wn}</td>
        <td class="rp-tbl-left">${esc(ln.ativo || "—")}</td>
        <td>${ln.quantity == null ? _DASH : fmtNum(ln.quantity, 4)}</td>
        <td>${ln.pu == null ? _DASH : fmtNum(ln.pu, 6)}</td>
        <td class="${nz(ln.balance) < 0 ? "rp-diff-neg" : ""}">${fmtMoney(ln.balance)}</td></tr>`));
    });
    body.push(`<tr style="background:#fafafa">
      <td class="rp-tbl-left text-[10px] text-gray-500">${wn}</td>
      <td class="rp-tbl-left">Caixa</td><td>${_DASH}</td><td>${_DASH}</td>
      <td>${fmtMoney((r.cash || {}).new)}</td></tr>`);
  });
  const preview = `<div class="text-[10px] uppercase tracking-widest text-gray-400 mb-1">Preview do upload — ${p.uprows} linha(s)</div>
    <div style="overflow:auto;max-height:340px"><table class="rp-tbl"><thead><tr>${head}</tr></thead><tbody>${body.join("")}</tbody></table></div>`;
  return summary + shiftSection + preview;
}
// Abre o modal Implementar para uma carteira específica (tela de detalhamento).
// Reusa exatamente o mesmo preview/modal em lote, escopado a 1 carteira.
function openImplementDetail() {
  const r = _movResults[_detWid];
  if (!_detWid || !r || r.state !== "done") { _detStatus("Projeção ainda não concluída para esta carteira.", "error"); return; }
  openImplementModal([_detWid]);
}
function openImplementModal(widsArg) {
  const wids = (widsArg && widsArg.length) ? widsArg : _selectedDoneWids();
  if (!wids.length) return;
  _implementWids = wids;
  const sub = wids.length === 1 ? (_movResults[wids[0]] || {}).walletName || wids[0] : `${wids.length} carteira(s)`;
  document.getElementById("implement-modal-sub").textContent = `${sub} · ${_date} → ${_targetDate()}`;
  document.getElementById("implement-body").innerHTML = _renderImplementBody(wids);
  document.getElementById("implement-status").textContent = "";
  const cb = document.getElementById("implement-confirm-btn");
  cb.textContent = "Confirmar seleção"; cb.disabled = false; cb.onclick = confirmImplement;
  const dl = document.getElementById("implement-dl-btn"); if (dl) dl.disabled = false;
  const m = document.getElementById("implement-modal");
  m.style.display = "flex"; m.classList.remove("hidden");
}
function closeImplementModal() {
  const m = document.getElementById("implement-modal");
  m.style.display = "none"; m.classList.add("hidden");
}

/* ══════════ Memória de cálculo — modal com a composição passo a passo ══════════
   Abre no detalhe (1 carteira). Lê _movResults[_detWid] e renderiza os 5 blocos
   (mov → amountDifference → caixa → outros → NAV/GAP) com regras/fórmulas e o estado
   "não calculado" explícito. _renderCalcMemo é definido mais abaixo. */
