"""Source-agnostic conciliação diagnostic engine.

The 6-step sequential funnel from `pages/conciliacao.diagnose`, extracted into a
pure function that runs off a pre-loaded *context* dict instead of reading the
database directly. This lets the same logic run on:

  • processedPosition + navPackage data (the original page), and
  • unprocessedSecurityPositions + computed contribution/gap (the
    Conciliação Não Processado page).

The funnel body is a faithful copy of conciliacao.diagnose so both pages emit
the same flag types, tolerances and output shape. The ONLY differences are that
data arrives via `ctx` and a couple of inputs are optional (history for the
Step-6 3-sigma test, pending corrections) so a caller that can't supply them
still gets a valid result.

`ctx` keys (all required unless noted):
    wallet_id, date, former_date
    gap_pct, gap_cash, former_nav, return_nav_ps, return_contrib
    corrections_count (int), corrections_impact (float),
    recalc_gap_cash, recalc_gap_pct            # may equal gap_* when no recalc
    current_secs: [{securityId, pu, quantity, executionPrice,
                    totalContribution, eventContribution, beehusName}]
    former_map: {sid: {pu, quantity}}
    all_txns_flat: [{type, balance, securityId, txnId, pending?}]
    sec_info: {sid: {redemptionNAVDays, redemptionSettlementDays,
                     subscriptionNAVDays, subscriptionSettlementDays,
                     securityType, beehusName}}
    prov_map: {sid: total_active_amount}
    prov_lifecycle_sids: set(sid)
    history_returns: [float]   # optional, for Step 6.1 (empty disables it)
    thresholds: {sid: {lowerBound, upperBound, mean, stdDev}}  # optional
"""
import math
import statistics
from datetime import date as _date, timedelta

_EVENT_TYPES = {"amortization", "coupon"}
_TOLERANCE_ABS = 0.01
_TOLERANCE_REL = 0.05       # 5%
_TOLERANCE_REL_TXN = 0.10   # 10% — Step 3.3


def _approx(a, b):
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOLERANCE_ABS or diff <= abs(b) * _TOLERANCE_REL


def _approx_txn(a, b):
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOLERANCE_ABS or diff <= abs(b) * _TOLERANCE_REL_TXN


def _next_biz_day(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _prov_dates(date_str, offset):
    """Provision window anchored on `date_str`, spanning |offset| days, with a
    minimum liquidation of navDate + 1 business day. Copy of
    conciliacao._prov_dates."""
    try:
        offset = int(offset or 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        base = _date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return date_str, date_str
    if offset > 0:
        init_d, liq_d = base, base + timedelta(days=offset)
    elif offset < 0:
        init_d, liq_d = base + timedelta(days=offset), base
    else:
        init_d, liq_d = base, base
    floor = _next_biz_day(base)
    if liq_d < floor:
        liq_d = floor
    return init_d.isoformat(), liq_d.isoformat()


def _fmt_brl(v):
    if v is None:
        return "—"
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def run_funnel(ctx):
    """Run Steps 1–7 over `ctx`. Returns {step1..step7} (plain dict).

    Caller is responsible for jsonify/_sanitize and for the early
    out-of-scope checks (no former position, etc.)."""
    wallet_id = ctx["wallet_id"]
    date = ctx["date"]
    former_date = ctx.get("former_date")
    return_nav_ps = ctx.get("return_nav_ps", 0) or 0
    return_contrib = ctx.get("return_contrib", 0) or 0
    gap_pct = ctx.get("gap_pct", 0) or 0
    gap_cash = ctx.get("gap_cash")
    former_nav = ctx.get("former_nav")

    current_secs = ctx.get("current_secs", [])
    former_map = ctx.get("former_map", {})
    all_txns_flat = ctx.get("all_txns_flat", [])
    sec_info = ctx.get("sec_info", {})
    prov_map = ctx.get("prov_map", {})
    prov_lifecycle_sids = ctx.get("prov_lifecycle_sids", set())
    history_returns = ctx.get("history_returns", [])
    thresholds = ctx.get("thresholds", {}) or {}

    # Derived txn groupings
    txns_by_security = {}
    for t in all_txns_flat:
        sid = t.get("securityId") or ""
        if sid:
            txns_by_security.setdefault(sid, []).append(t)
    event_txns_by_sec = {}
    for t in all_txns_flat:
        if t.get("type") in _EVENT_TYPES and t.get("securityId"):
            event_txns_by_sec.setdefault(t["securityId"], []).append(t)

    current_sec_ids = {str(s.get("securityId", "")) for s in current_secs}

    # ── STEP 1 ──────────────────────────────────────────────────────────────
    step1 = {
        "status": "gap" if abs(gap_pct) > 1e-10 else "ok",
        "returnNavPerShare": return_nav_ps,
        "returnContribution": return_contrib,
        "gapPct": gap_pct,
        "gapCash": gap_cash,
        "formerNav": former_nav,
        "correctionsCount": ctx.get("corrections_count", 0),
        "correctionsImpact": round(ctx.get("corrections_impact", 0) or 0, 2),
        "recalculatedGapCash": ctx.get("recalc_gap_cash", gap_cash),
        "recalculatedGapPct": ctx.get("recalc_gap_pct", gap_pct),
    }

    _skipped = {"status": "skipped"}
    if step1["status"] == "ok":
        return {
            "walletId": wallet_id, "date": date,
            "step1": step1, "step2": _skipped, "step3": _skipped,
            "step4": _skipped, "step5": _skipped, "step6": _skipped,
            "step7": {
                "status": "ok", "verdict": "NO_GAP",
                "detail": "Sem divergência detectada.",
                "formerNav": former_nav, "formerDate": None,
                "gapCash": gap_cash, "gapPct": gap_pct,
                "signals": {
                    "step3HasFlags": False, "step4HasIssues": False,
                    "step5Consistent": True, "step6WalletAnomaly": False,
                    "allSuspectsMissingContribution": False,
                },
            },
        }

    # ── STEP 2 — Eliminate ────────────────────────────────────────────────────
    eliminated = []
    suspects = []
    for sec in current_secs:
        sid = str(sec.get("securityId", ""))
        pu = sec.get("pu")
        qty = sec.get("quantity")
        f = former_map.get(sid, {})
        f_pu = f.get("pu")
        f_qty = f.get("quantity")
        f_bal = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None
        amt_diff = round(qty - (f_qty or 0), 6) if qty is not None else None

        exec_price = sec.get("executionPrice")
        event_c = sec.get("eventContribution") or 0
        total_c = sec.get("totalContribution")

        try:
            ret_pu = round(pu / f_pu - 1, 8) if (pu and f_pu) else None
        except (TypeError, ZeroDivisionError):
            ret_pu = None
        try:
            ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
        except (TypeError, ZeroDivisionError):
            ret_c = None
        diff_rent = round(ret_pu - ret_c, 8) if (ret_pu is not None and ret_c is not None) else None

        sec_txns = txns_by_security.get(sid, [])
        cond_a = amt_diff is not None and amt_diff == 0
        cond_b = not sec_txns
        cond_c = sid not in prov_lifecycle_sids
        cond_d = diff_rent is not None and diff_rent == 0

        if not cond_d and diff_rent and f_bal:
            ev_txns = event_txns_by_sec.get(sid, [])
            if ev_txns:
                ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)
                expected_event_cash = round(-diff_rent * f_bal, 2)
                if _approx(ev_total, expected_event_cash):
                    cond_d = True
                    non_event_txns = [t for t in sec_txns if t["type"] not in _EVENT_TYPES]
                    cond_b = not non_event_txns

        sec_entry = {
            "securityId": sid, "name": sec.get("beehusName", ""),
            "amountDiff": amt_diff, "diffRent": diff_rent, "formerBalance": f_bal,
            "pu": pu, "formerPu": f_pu, "executionPrice": exec_price,
            "quantity": qty, "formerQuantity": f_qty,
            "eventContribution": event_c, "totalContribution": total_c,
            "securityType": sec_info.get(sid, {}).get("securityType", ""),
            "failedConditions": [],
        }
        if cond_a and cond_b and cond_c and cond_d:
            eliminated.append(sec_entry)
        else:
            if not cond_a:
                sec_entry["failedConditions"].append("amountDifference")
            if not cond_b:
                sec_entry["failedConditions"].append("hasTransactions")
            if not cond_c:
                sec_entry["failedConditions"].append("provisionLifecycle")
            if not cond_d:
                sec_entry["failedConditions"].append("rentabilityDifference")
            suspects.append(sec_entry)

    step2 = {"status": "done", "eliminatedCount": len(eliminated),
             "suspectCount": len(suspects), "suspects": suspects}

    # ── STEP 3 — Diagnose Securities ──────────────────────────────────────────
    step3_securities = []
    for sec_entry in suspects:
        sid = sec_entry["securityId"]
        amt_diff = sec_entry["amountDiff"]
        diff_rent = sec_entry["diffRent"]
        f_bal = sec_entry["formerBalance"]
        pu = sec_entry["pu"]
        exec_price = sec_entry["executionPrice"]
        price = exec_price or pu or 0
        sec_type = sec_entry["securityType"]
        sec_txns = txns_by_security.get(sid, [])

        diag = {"securityId": sid, "name": sec_entry["name"],
                "amountDiff": amt_diff, "diffRent": diff_rent, "eliminated": False,
                "step3_1": None, "step3_2": None, "step3_3": None, "step3_4": None}

        # 3.1 Amount Difference
        if amt_diff:
            info = sec_info.get(sid, {})
            settle = info.get("redemptionSettlementDays" if amt_diff < 0 else "subscriptionSettlementDays") or 0
            nav_d = info.get("redemptionNAVDays" if amt_diff < 0 else "subscriptionNAVDays") or 0
            offset = settle - nav_d
            if offset == 0:
                has_buysell = any(t.get("type") == "buySell" for t in sec_txns)
                if has_buysell:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Transação buySell encontrada"}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_TRANSACTION",
                                       "offset": offset, "impact": impact,
                                       "detail": "Liquidação imediata mas transação buySell não encontrada"}
            else:
                if sid in prov_map:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Provisão ativa encontrada",
                                       "provisionAmount": round(float(prov_map[sid]), 2)}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    flag_detail = "Liquidação futura" if offset > 0 else "Nav futuro"
                    prov_initial, prov_liquidation = _prov_dates(date, offset)
                    if offset > 0:
                        prov_balance = -impact if amt_diff > 0 else impact
                    else:
                        prov_balance = impact if amt_diff > 0 else -impact
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_PROVISION",
                                       "offset": offset, "impact": impact,
                                       "detail": f"{flag_detail} (offset={offset}) mas provisão não encontrada",
                                       "provisionData": {
                                           "initialDate": prov_initial,
                                           "liquidationDate": prov_liquidation,
                                           "provisionType": "buySell",
                                           "balance": prov_balance, "offset": offset}}

        # 3.2 Rentability Difference
        if diff_rent and f_bal:
            expected_event_cash = round(-diff_rent * f_bal, 2)
            ev_txns = event_txns_by_sec.get(sid, [])
            ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)
            if ev_txns and _approx(ev_total, expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por transação de evento",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total}
                diag["eliminated"] = True
            elif ev_txns:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_EVENT_BALANCE",
                                   "detail": "Transação de evento existe mas valor diverge",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total,
                                   "impact": round(abs(ev_total - expected_event_cash), 2)}
            elif sid in prov_map and _approx(float(prov_map[sid]), expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por provisão (provável evento anunciado)",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2)}
                diag["eliminated"] = True
            elif sid in prov_map:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_PROVISION_AMOUNT",
                                   "detail": "Provisão existe mas valor diverge do evento esperado",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2),
                                   "impact": round(abs(float(prov_map[sid]) - expected_event_cash), 2)}
            else:
                diag["step3_2"] = {"status": "flag", "flag": "MISSING_EVENT",
                                   "detail": "Sem transação de evento e sem provisão",
                                   "expectedEventCash": expected_event_cash,
                                   "impact": abs(expected_event_cash)}

        # 3.3 Withholding Tax / Execution Price
        if amt_diff and not diag["eliminated"]:
            buysell_txns = [t for t in sec_txns if t.get("type") == "buySell"]
            if buysell_txns:
                actual_bal = round(sum(float(t.get("balance", 0) or 0) for t in buysell_txns), 2)
                expected_val = round(-amt_diff * price, 2)
                if _approx_txn(expected_val, actual_bal):
                    diff_val = round(abs(expected_val - actual_bal), 2)
                    implied_exec = round(-actual_bal / amt_diff, 6) if amt_diff else None
                    if (sec_type == "brazilianFund" and amt_diff < 0
                            and actual_bal < expected_val and diff_val > _TOLERANCE_ABS):
                        diag["step3_3"] = {"status": "flag", "flag": "WITHHOLDING_TAX",
                                           "detail": "Provável IR retido na fonte (brazilianFunds)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2), "impact": diff_val}
                    elif (price and implied_exec is not None
                          and abs(implied_exec - price) > abs(price) * 0.005):
                        diag["step3_3"] = {"status": "flag", "flag": "MISSING_EXECUTION_PRICE",
                                           "detail": "Preço de execução divergente do preço usado (provável fallback de PU)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "pu": pu, "executionPrice": exec_price,
                                           "expectedExecPrice": implied_exec, "impact": diff_val}
                    else:
                        diag["step3_3"] = {"status": "ok",
                                           "detail": "Valor da transação confere com esperado",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2)}
                else:
                    diff_val = round(abs(expected_val - actual_bal), 2)
                    if sec_type == "brazilianFund" and amt_diff < 0:
                        diag["step3_3"] = {"status": "flag", "flag": "WITHHOLDING_TAX",
                                           "detail": "Provável IR retido na fonte (brazilianFunds)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2), "impact": diff_val}
                    elif exec_price is None or (pu is not None and exec_price == pu):
                        expected_exec_price = round(-actual_bal / amt_diff, 6) if amt_diff else None
                        diag["step3_3"] = {"status": "flag", "flag": "MISSING_EXECUTION_PRICE",
                                           "detail": "Preço de execução ausente (sistema usou PU como fallback)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "pu": pu, "executionPrice": exec_price,
                                           "expectedExecPrice": expected_exec_price, "impact": diff_val}
                    else:
                        diag["step3_3"] = {"status": "flag", "flag": "WRONG_TRANSACTION_VALUE",
                                           "detail": "Valor da transação diverge do esperado",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2), "impact": diff_val}

        # 3.4 Misclassified event type
        no_amt_change = (amt_diff is not None and amt_diff == 0)
        no_rent_signal = (diff_rent is None or diff_rent == 0)
        if (no_amt_change and no_rent_signal and sec_txns
                and gap_cash is not None and not diag["step3_3"]):
            txn_sum = round(sum(float(t.get("balance", 0) or 0) for t in sec_txns), 2)
            if abs(txn_sum) > 0.01 and _approx(abs(txn_sum), abs(gap_cash)):
                types_found = sorted({(t.get("type") or "—") for t in sec_txns})
                diag["step3_4"] = {
                    "status": "flag", "flag": "MISCLASSIFIED_EVENT_TYPE",
                    "impact": abs(txn_sum), "transactionTotal": txn_sum,
                    "transactionTypes": types_found, "transactionCount": len(sec_txns),
                    "detail": (f"Transações neste security somam R$ {txn_sum:.2f}, valor idêntico ao gap. "
                               f"Tipo(s) presente(s): {', '.join(types_found)}. "
                               "Provável erro de classificação — o tipo atual não é reconhecido como evento "
                               "(coupon/amortization) e por isso não impacta eventContribution.")}

        if diag["step3_1"] or diag["step3_2"] or diag["step3_3"] or diag["step3_4"]:
            step3_securities.append(diag)

    step3 = {"status": "done", "securities": step3_securities}

    # ── STEP 4 — Diagnose Transactions ────────────────────────────────────────
    unclassified = [{"securityId": t["securityId"], "balance": t.get("balance")}
                    for t in all_txns_flat if not t.get("type")]

    wrong_security = []
    for sid, txns in txns_by_security.items():
        if sid in current_sec_ids:
            continue
        info = sec_info.get(sid, {})
        has_provision = sid in prov_map
        sub_nav = info.get("subscriptionNAVDays") or 0
        if sub_nav > 0 and has_provision:
            verdict = "LEGITIMATE_NEW_PURCHASE"
            reason = f"Compra nova com subscriptionNAVDays={sub_nav} e provisão existente"
        else:
            red_settle = info.get("redemptionSettlementDays") or 0
            red_nav = info.get("redemptionNAVDays") or 0
            offset = red_settle - red_nav
            if offset > 0 and has_provision:
                verdict = "LEGITIMATE_POST_SALE"
                reason = f"Venda com offset={offset} e provisão existente"
            elif has_provision:
                verdict = "LEGITIMATE_WITH_PROVISION"
                reason = "Security não está na posição mas provisão existe"
            else:
                verdict = "WRONG_SECURITY"
                reason = "Security não encontrado na posição e sem provisão correspondente"
        total_bal = round(sum(float(t.get("balance", 0) or 0) for t in txns), 2)
        wrong_security.append({"securityId": sid, "securityName": info.get("beehusName", ""),
                               "balance": total_bal, "txnCount": len(txns),
                               "verdict": verdict, "reason": reason})

    misclassified = []
    step3_missing = {}
    for diag in step3_securities:
        sid = diag["securityId"]
        name = diag["name"]
        for key in ("step3_1", "step3_2", "step3_3"):
            s = diag.get(key)
            if s and s.get("status") == "flag" and s.get("impact"):
                step3_missing.setdefault(sid, []).append((name, round(float(s["impact"]), 2), s["flag"]))
    if step3_missing:
        for t in all_txns_flat:
            t_bal = round(abs(float(t.get("balance", 0) or 0)), 2)
            if not t_bal:
                continue
            t_sid = t.get("securityId", "")
            matches = []
            for miss_sid, entries in step3_missing.items():
                if miss_sid == t_sid:
                    continue
                for miss_name, miss_impact, miss_flag in entries:
                    if _approx(t_bal, miss_impact):
                        matches.append({"securityId": miss_sid, "securityName": miss_name,
                                        "flag": miss_flag, "expectedValue": miss_impact})
            if matches:
                misclassified.append({"txnId": t.get("txnId") or None,
                                      "txnBalance": round(float(t.get("balance", 0) or 0), 2),
                                      "txnSecurityId": t_sid or None, "txnType": t.get("type"),
                                      "matches": matches})

    step4 = {"status": "done", "unclassified": unclassified,
             "wrongSecurity": wrong_security, "misclassified": misclassified}

    # ── STEP 5 — Cash Validation ──────────────────────────────────────────────
    former_cash = ctx.get("former_cash")
    current_cash = ctx.get("current_cash")
    total_txn_balance = round(sum(float(t.get("balance", 0) or 0) for t in all_txns_flat), 2)
    projected_cash = round(former_cash + total_txn_balance, 2) if former_cash is not None else None
    cash_diff = round(projected_cash - current_cash, 2) if (projected_cash is not None and current_cash is not None) else None

    suspect_txns = []
    if cash_diff is not None and abs(cash_diff) > _TOLERANCE_ABS:
        tol = max(_TOLERANCE_ABS, abs(cash_diff) * 0.001)
        candidates = []
        for t in all_txns_flat:
            bal = float(t.get("balance") or 0)
            delta = abs(bal - cash_diff)
            if delta <= tol:
                sid = t.get("securityId") or None
                sec_name = sec_info.get(str(sid), {}).get("beehusName") if sid else None
                candidates.append((delta, {"txnId": t.get("txnId") or None, "balance": round(bal, 2),
                                           "type": t.get("type"), "securityId": sid,
                                           "securityName": sec_name, "pending": bool(t.get("pending"))}))
        candidates.sort(key=lambda x: x[0])
        suspect_txns = [c[1] for c in candidates]

    if cash_diff is not None and round(cash_diff, 2) == 0:
        cash_diagnosis, cash_status = "consistent", "ok"
    elif unclassified:
        cash_diagnosis, cash_status = "unclassified_txns", "warning"
    elif not all_txns_flat:
        cash_diagnosis, cash_status = "missing_cash_txn", "warning"
    elif suspect_txns:
        cash_diagnosis, cash_status = "likely_wrong_txn", "warning"
    elif cash_diff is not None:
        cash_diagnosis, cash_status = "value_error", "warning"
    else:
        cash_diagnosis, cash_status = "no_data", "ok"

    step5 = {"status": cash_status, "formerCash": former_cash, "currentCash": current_cash,
             "totalTransactions": total_txn_balance, "projectedCash": projected_cash,
             "cashDiff": cash_diff, "diagnosis": cash_diagnosis, "suspectTxns": suspect_txns}

    # ── STEP 6 — Rentability Anomalies ────────────────────────────────────────
    wallet_anomaly = None
    returns = [r for r in history_returns if r is not None]
    if len(returns) >= 3:
        mean = statistics.mean(returns)
        stddev = statistics.stdev(returns)
        lower = mean - 3 * stddev
        upper = mean + 3 * stddev
        wallet_anomaly = {"isAnomaly": return_nav_ps < lower or return_nav_ps > upper,
                          "currentReturn": return_nav_ps, "mean": round(mean, 8),
                          "stdDev": round(stddev, 8), "lowerBound": round(lower, 8),
                          "upperBound": round(upper, 8), "sampleSize": len(returns)}

    security_anomalies = []
    if thresholds:
        for sec_entry in suspects:
            sid = sec_entry["securityId"]
            th = thresholds.get(sid)
            pu = sec_entry.get("pu")
            f_pu = sec_entry.get("formerPu")
            try:
                ret = pu / f_pu - 1 if (pu and f_pu) else None
            except (TypeError, ZeroDivisionError):
                ret = None
            if th and ret is not None:
                lb, ub = th.get("lowerBound"), th.get("upperBound")
                if (lb is not None and ub is not None) and (ret < lb or ret > ub):
                    security_anomalies.append({"securityId": sid, "securityName": sec_entry.get("name", ""),
                                               "currentReturn": round(ret, 8), "mean": th.get("mean"),
                                               "stdDev": th.get("stdDev"), "isAnomaly": True})

    step6_status = "ok"
    if (wallet_anomaly and wallet_anomaly["isAnomaly"]) or any(a["isAnomaly"] for a in security_anomalies):
        step6_status = "warning"
    step6 = {"status": step6_status, "walletAnomaly": wallet_anomaly,
             "securityAnomalies": security_anomalies}

    # ── STEP 7 — Causa Provável ───────────────────────────────────────────────
    step3_has_flags = any(
        (s.get("step3_1") or {}).get("status") == "flag" or
        (s.get("step3_2") or {}).get("status") == "flag" or
        (s.get("step3_3") or {}).get("status") == "flag" or
        (s.get("step3_4") or {}).get("status") == "flag"
        for s in step3_securities)
    step4_has_issues = (bool(unclassified)
                        or any(t.get("verdict") == "WRONG_SECURITY" for t in wrong_security)
                        or bool(misclassified))
    step5_consistent = step5["diagnosis"] == "consistent"
    all_suspects_missing_contrib = (len(suspects) > 0 and all(s.get("diffRent") is None for s in suspects))
    step6_wallet_anomaly = bool(wallet_anomaly and wallet_anomaly.get("isAnomaly"))

    if step3_has_flags:
        verdict, step7_status = "SECURITY_ISSUES", "warning"
        step7_detail = "Há flags acionáveis em securities. Ver Step 3."
    elif step4_has_issues:
        verdict, step7_status = "TRANSACTION_ISSUES", "warning"
        step7_detail = "Há transações não identificadas, com security divergente ou mal classificadas. Ver Step 4."
    elif not step5_consistent:
        verdict, step7_status = "CASH_ISSUES", "warning"
        step7_detail = "Caixa projetado diverge do caixa real. Ver Step 5."
    else:
        verdict, step7_status = "LIKELY_WRONG_FORMER_NAV", "warning"
        step7_detail = ("Securities, transações e caixa estão consistentes, mas o gap persiste. "
                        f"Causa mais provável: o NAV anterior ({_fmt_brl(former_nav)} em "
                        f"{former_date or '(data desconhecida)'}) está incorreto. "
                        "Verifique a posição anterior.")

    step7 = {"status": step7_status, "verdict": verdict, "detail": step7_detail,
             "formerNav": former_nav, "formerDate": former_date,
             "gapCash": gap_cash, "gapPct": gap_pct,
             "signals": {"step3HasFlags": step3_has_flags, "step4HasIssues": step4_has_issues,
                         "step5Consistent": step5_consistent, "step6WalletAnomaly": step6_wallet_anomaly,
                         "allSuspectsMissingContribution": all_suspects_missing_contrib}}

    return {"walletId": wallet_id, "date": date, "step1": step1, "step2": step2,
            "step3": step3, "step4": step4, "step5": step5, "step6": step6, "step7": step7}
