"""Synthetic portfolio scenarios for Bayesian optimization stress testing.

Run: python tests/test_bayesian_scenarios.py
No DB required — all payloads are constructed in-memory.
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pages.bayesian import optimize_with_validation, extract_factors, _load_config


# ── Payload builder ───────────────────────────────────────────────────────────

def _make_diag(gap_cash, gap_pct, former_nav, return_nav, return_contrib,
               securities=None, unclassified=None, wrong_security=None,
               misclassified=None, cash_diff=0, current_cash=5000,
               former_cash=5000, total_txns=0, cash_diagnosis="consistent",
               anomalies=None):
    """Build a synthetic diagnostic payload."""
    return {
        "date": "2026-02-10",
        "walletId": "test-wallet",
        "step1": {
            "formerNav": former_nav,
            "gapCash": gap_cash,
            "gapPct": gap_pct,
            "returnContribution": return_contrib,
            "returnNavPerShare": return_nav,
            "status": "gap" if abs(gap_pct) > 1e-10 else "ok",
        },
        "step2": {
            "eliminatedCount": 2,
            "suspectCount": len(securities or []),
            "status": "done",
        },
        "step3": {
            "securities": securities or [],
            "status": "done",
        },
        "step4": {
            "unclassified": unclassified or [],
            "wrongSecurity": wrong_security or [],
            "misclassified": misclassified or [],
            "status": "done",
        },
        "step5": {
            "cashDiff": cash_diff,
            "currentCash": current_cash,
            "formerCash": former_cash,
            "projectedCash": former_cash + total_txns,
            "totalTransactions": total_txns,
            "diagnosis": cash_diagnosis,
            "status": "ok" if cash_diagnosis == "consistent" else "warning",
        },
        "step6": {
            "securityAnomalies": anomalies or [],
            "walletAnomaly": None,
            "status": "warning" if anomalies else "ok",
        },
    }


def _sec(name, sid, amt_diff=0, pu=50, former_pu=50, qty=1000, former_qty=1000,
         exec_price=None, step3_1=None, step3_2=None, step3_3=None):
    """Build a security entry for step3."""
    return {
        "securityId": sid,
        "name": name,
        "amountDiff": amt_diff,
        "diffRent": 0.0,
        "eliminated": False,
        "pu": pu,
        "formerPu": former_pu,
        "quantity": qty,
        "formerQuantity": former_qty,
        "executionPrice": exec_price or pu,
        "step3_1": step3_1,
        "step3_2": step3_2,
        "step3_3": step3_3,
    }


# ── Scenarios ─────────────────────────────────────────────────────────────────

SCENARIOS = []

# T1: Missing buySell, cash OK
SCENARIOS.append(("T1: MISSING_TRANSACTION only", {
    "expected_flags": ["MISSING_TRANSACTION"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-50000, gap_pct=-0.05, former_nav=1000000,
        return_nav=-0.04, return_contrib=0.01,
        securities=[_sec("Bond A", "s1", amt_diff=-1000, pu=55, former_pu=50,
                         qty=4000, former_qty=5000,
                         step3_1={"flag": "MISSING_TRANSACTION", "impact": 50000,
                                  "offset": 0, "status": "flag"})],
    ),
}))

# T2: Missing provision, cash OK
SCENARIOS.append(("T2: MISSING_PROVISION only", {
    "expected_flags": ["MISSING_PROVISION"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=200000, gap_pct=0.10, former_nav=2000000,
        return_nav=0.11, return_contrib=0.01,
        securities=[_sec("Fund B", "s2", amt_diff=5000, pu=40, former_pu=40,
                         qty=10000, former_qty=5000,
                         step3_1={"flag": "MISSING_PROVISION", "impact": 200000,
                                  "offset": 30, "status": "flag",
                                  "provisionData": {"balance": 200000,
                                                    "initialDate": "2026-02-10",
                                                    "liquidationDate": "2026-03-12",
                                                    "provisionType": "buySell"}})],
    ),
}))

# T3: Pure cash gap
SCENARIOS.append(("T3: CASH_MISMATCH only", {
    "expected_flags": ["CASH_MISMATCH"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=12000, gap_pct=0.008, former_nav=1500000,
        return_nav=0.009, return_contrib=0.001,
        cash_diff=-12000, current_cash=17000, former_cash=5000,
        total_txns=0, cash_diagnosis="missing_cash_txn",
    ),
}))

# T4: MISSING_EXECUTION_PRICE + CASH_MISMATCH (Tier 2 MC)
SCENARIOS.append(("T4: EXEC_PRICE + CASH (Tier 2 MC)", {
    "expected_flags_contains": ["CASH_MISMATCH"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-80000, gap_pct=-0.08, former_nav=1000000,
        return_nav=-0.07, return_contrib=0.01,
        securities=[_sec("CRI C", "s3", amt_diff=-2000, pu=40, former_pu=39.75,
                         qty=3000, former_qty=5000, exec_price=40,
                         step3_1={"offset": 0, "status": "ok"},
                         step3_3={"flag": "MISSING_EXECUTION_PRICE", "impact": 500,
                                  "pu": 40, "executionPrice": 40,
                                  "expectedExecPrice": 39.75,
                                  "expectedValue": -79500, "actualBalance": 79000,
                                  "status": "flag"})],
        cash_diff=79500, current_cash=1000, former_cash=1000,
        total_txns=79500, cash_diagnosis="value_error",
    ),
}))

# T5: WITHHOLDING_TAX + CASH_MISMATCH (discrete MC)
SCENARIOS.append(("T5: WITHHOLDING_TAX + CASH (Tier 2 MC)", {
    "expected_flags_contains": ["CASH_MISMATCH"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-45000, gap_pct=-0.03, former_nav=1500000,
        return_nav=-0.02, return_contrib=0.01,
        securities=[_sec("Fund D", "s4", amt_diff=-10000, pu=5, former_pu=5,
                         qty=90000, former_qty=100000,
                         step3_1={"offset": 0, "status": "ok"},
                         step3_3={"flag": "WITHHOLDING_TAX", "impact": 3000,
                                  "actualBalance": 47000, "expectedValue": 50000,
                                  "status": "flag"})],
        cash_diff=42000, current_cash=1000, former_cash=1000,
        total_txns=42000, cash_diagnosis="value_error",
    ),
}))

# T6: WRONG_TRANSACTION_VALUE only (gaussian MC)
# actualBalance=7500 but expectedValue=5000 → txn overstated by 2500.
# Cash: txn of 7500 already in totalTransactions. The fix corrects it to 5000,
# so cashFix = actual − expected = 7500 − 5000 = +2500.
# Wait — signed impact = actual − expected = 7500 − 5000 = +2500, but gap is -2500.
# That means the fix impact is +2500 which doesn't close a -2500 gap...
# Let's flip: actual=2500, expected=5000 → impact = 2500 − 5000 = -2500. Gap = -2500. Closes.
# Cash: totalTransactions includes the wrong txn (2500). Fix adds cashFix = -2500.
# projectedCash = 5000 + 2500 + (-2500) = 5000 = currentCash. OK.
SCENARIOS.append(("T6: WRONG_TXN_VALUE only (Tier 2 MC)", {
    "expected_flags": ["WRONG_TRANSACTION_VALUE"],
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-2500, gap_pct=-0.005, former_nav=500000,
        return_nav=-0.003, return_contrib=0.002,
        securities=[_sec("Bond E", "s5", amt_diff=-500, pu=10, former_pu=10,
                         qty=4500, former_qty=5000,
                         step3_1={"offset": 0, "status": "ok"},
                         step3_3={"flag": "WRONG_TRANSACTION_VALUE", "impact": 2500,
                                  "actualBalance": 2500, "expectedValue": 5000,
                                  "status": "flag"})],
        cash_diff=-2500, current_cash=5000, former_cash=5000,
        total_txns=2500, cash_diagnosis="value_error",
    ),
}))

# T7: 3 flags, only 2 needed (Tier 3 decoy)
# Gap = -30k. MISSING_TRANSACTION = -25k (cash conditional, cash IS broken).
# CASH_MISMATCH = -5k. Together = -30k = gap. WRONG_SECURITY = -30k (decoy).
# Cash: totalTransactions=30000 (wrong txns inflating cash).
# MISSING_TRANSACTION with cash broken → cashFix includes it: -25000.
# CASH_MISMATCH cashFix: -5000. Total cashFix = -30000.
# projectedCash = 1000 + 30000 + (-30000) = 1000 = currentCash. OK.
SCENARIOS.append(("T7: 3 flags, 2 needed (Tier 3 decoy)", {
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-30000, gap_pct=-0.03, former_nav=1000000,
        return_nav=-0.02, return_contrib=0.01,
        securities=[_sec("Bond F", "s6", amt_diff=-500, pu=50, former_pu=50,
                         qty=4500, former_qty=5000,
                         step3_1={"flag": "MISSING_TRANSACTION", "impact": 25000,
                                  "offset": 0, "status": "flag"})],
        wrong_security=[{"securityId": "s7", "securityName": "Ghost G",
                         "balance": -30000, "txnCount": 1,
                         "verdict": "WRONG_SECURITY", "reason": "Not in position"}],
        cash_diff=30000, current_cash=1000, former_cash=1000,
        total_txns=30000, cash_diagnosis="value_error",
    ),
}))

# T8: Overlapping flags (coverage > 100%)
SCENARIOS.append(("T8: Overlapping flags (coverage > 100%)", {
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-10000, gap_pct=-0.006, former_nav=1600000,
        return_nav=0.008, return_contrib=0.014,
        securities=[_sec("Bond H", "s8", amt_diff=1000, pu=40, former_pu=40,
                         qty=3000, former_qty=2000,
                         step3_1={"flag": "MISSING_TRANSACTION", "impact": 40000,
                                  "offset": 0, "status": "flag"})],
        misclassified=[{"txnBalance": 40000, "txnSecurityId": None,
                        "txnType": "withdrawalDeposit", "matches": []}],
        cash_diff=50000, current_cash=5000, former_cash=5000,
        total_txns=50000, cash_diagnosis="value_error",
    ),
}))

# T9: All tiers present (Tier 1 + 2 + 3 + anomaly)
# Gap = -100k. MISSING_PROVISION = -60k (not cash-affecting).
# WITHHOLDING_TAX: actual=3000, expected=5000 → impact = 3000-5000 = -2000.
# CASH_MISMATCH = −cashDiff = -38000.
# Total = -60000 + -2000 + -38000 = -100000 = gap. OK.
# Cash: WT is cash-always: -2000. CASH_MISMATCH: -38000. PROVISION: not cash.
# projectedCash = 1000 + 38000 + (-2000 + -38000) = -1000? No.
# The totalTransactions INCLUDES the wrong WT txn (3000 instead of 5000).
# So totalTransactions = 3000 (the WT txn) + 35000 (other txns) = 38000.
# Fix: WT cashFix = -2000 (makes the txn 5000 instead of 3000 → net +2000 more cash)
# Wait, WT impact = actual − expected = 3000 − 5000 = -2000. This means the txn
# brought in LESS cash than expected. The fix ADDS the missing 2000.
# But in cash terms, cashFix for WT = -2000 (the signed impact). That subtracts more.
# Let me rethink: the cash already has the wrong WT txn (3000). The fix would correct
# it to 5000, so cash goes UP by 2000. But the signed impact is -2000 (matching gap).
# The issue: cash fix direction ≠ gap fix direction for WT.
# Solution: make the test consistent. Set totalTransactions=40000 (includes proper txns).
# Cash_MISMATCH = -(40000 - cash needed). cashDiff = projected - current = 41000 - 1000 = 40000.
# Let me simplify: just make cash consistent with the flags.
# totalTransactions = 40000. cashDiff = (1000 + 40000) - 1000 = 40000.
# cashFix: WT(-2000) + CASH(-38000) = -40000. projected = 1000 + 40000 + (-40000) = 1000. OK!
SCENARIOS.append(("T9: All tiers (1+2+3+anomaly)", {
    "expected_valid": True,
    "diag": _make_diag(
        gap_cash=-100000, gap_pct=-0.05, former_nav=2000000,
        return_nav=-0.04, return_contrib=0.01,
        securities=[
            _sec("Fund I", "s9", amt_diff=3000, pu=20, former_pu=20,
                 qty=8000, former_qty=5000,
                 step3_1={"flag": "MISSING_PROVISION", "impact": 60000,
                          "offset": 15, "status": "flag",
                          "provisionData": {"balance": 60000,
                                            "initialDate": "2026-02-10",
                                            "liquidationDate": "2026-02-25",
                                            "provisionType": "buySell"}}),
            _sec("Fund J", "s10", amt_diff=-1000, pu=5, former_pu=5,
                 qty=9000, former_qty=10000,
                 step3_1={"offset": 0, "status": "ok"},
                 step3_3={"flag": "WITHHOLDING_TAX", "impact": 2000,
                          "actualBalance": 3000, "expectedValue": 5000,
                          "status": "flag"}),
        ],
        cash_diff=40000, current_cash=1000, former_cash=1000,
        total_txns=40000, cash_diagnosis="value_error",
        anomalies=[{"securityId": "s9", "securityName": "Fund I",
                    "currentReturn": -0.50, "mean": 0.001, "stdDev": 0.02,
                    "isAnomaly": True}],
    ),
}))

# T10: No flag closes the gap (insufficient coverage)
SCENARIOS.append(("T10: Insufficient coverage (gap >> flags)", {
    "expected_valid": False,
    "diag": _make_diag(
        gap_cash=-500000, gap_pct=-0.25, former_nav=2000000,
        return_nav=-0.24, return_contrib=0.01,
        securities=[_sec("Bond K", "s11", amt_diff=-2000, pu=50, former_pu=50,
                         qty=3000, former_qty=5000,
                         step3_1={"flag": "MISSING_TRANSACTION", "impact": 100000,
                                  "offset": 0, "status": "flag"})],
        cash_diff=50000, current_cash=1000, former_cash=1000,
        total_txns=50000, cash_diagnosis="value_error",
    ),
}))


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    cfg = _load_config()
    results = []
    passed = 0
    failed = 0

    print("=" * 110)
    print(f"{'#':<6} {'Scenario':<45} {'Flags':<40} {'Valid':>6} {'Gap':>10} {'Cash':>6} {'Post':>8} {'Result':>6}")
    print("-" * 110)

    for name, spec in SCENARIOS:
        diag = spec["diag"]
        r = optimize_with_validation(diag, cfg=cfg)
        b = r["bestFix"]
        v = b.get("validation", {}) if b else {}

        flags = b["flags"] if b else []
        valid = v.get("valid", False)
        gap_ok = v.get("gapResolved", False)
        cash_ok = v.get("cashConsistent", False)
        posterior = b.get("posterior", 0) if b else 0
        new_gap = v.get("newGapCash", "?")
        mc = r.get("monteCarlo")

        # Check expectations
        ok = True
        if "expected_flags" in spec:
            if sorted(flags) != sorted(spec["expected_flags"]):
                ok = False
        if "expected_flags_contains" in spec:
            for ef in spec["expected_flags_contains"]:
                if ef not in flags:
                    ok = False
        if "expected_valid" in spec:
            if valid != spec["expected_valid"]:
                ok = False

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        flags_str = " + ".join(flags) if flags else "(none)"
        if len(flags_str) > 38:
            flags_str = flags_str[:35] + "..."
        mc_tag = " [MC]" if mc else ""

        print(f"{name[:5]:<6} {name[4:]:<45} {flags_str:<40} {str(valid):>6} {str(new_gap):>10} {str(cash_ok):>6} {posterior:>7.1%} {status:>6}{mc_tag}")

        results.append({
            "name": name,
            "flags": flags,
            "valid": valid,
            "gapResolved": gap_ok,
            "cashConsistent": cash_ok,
            "posterior": posterior,
            "newGapCash": new_gap,
            "monteCarlo": mc is not None,
            "status": status,
            "validation": v,
        })

    print("-" * 110)
    print(f"{'TOTAL':<52} {passed} passed, {failed} failed out of {len(SCENARIOS)}")
    print("=" * 110)

    # Detail on failures
    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        print("\n--- FAILURES ---")
        for r in failures:
            print(f"\n{r['name']}:")
            print(f"  Flags: {r['flags']}")
            print(f"  Validation: {json.dumps(r['validation'], indent=4)}")

    return results


if __name__ == "__main__":
    run_all()
