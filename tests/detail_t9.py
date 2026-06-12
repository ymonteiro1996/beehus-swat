"""Detailed walkthrough of T9 (all tiers) scenario."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pages.bayesian import extract_factors, score_combinations, score_combinations_mc, validate_fix, optimize_with_validation, _load_config

cfg = _load_config()

diag = {
    "date": "2026-02-10", "walletId": "T9-all-tiers",
    "step1": {
        "formerNav": 2000000, "gapCash": -100000, "gapPct": -0.05,
        "returnContribution": 0.01, "returnNavPerShare": -0.04, "status": "gap"
    },
    "step2": {"eliminatedCount": 2, "suspectCount": 2, "status": "done"},
    "step3": {"securities": [
        {
            "securityId": "s9", "name": "Fund I", "amountDiff": 3000, "diffRent": 0.0,
            "eliminated": False, "pu": 20, "formerPu": 20, "quantity": 8000, "formerQuantity": 5000,
            "executionPrice": 20,
            "step3_1": {"flag": "MISSING_PROVISION", "impact": 60000, "offset": 15, "status": "flag",
                        "provisionData": {"balance": 60000, "initialDate": "2026-02-10",
                                          "liquidationDate": "2026-02-25", "provisionType": "buySell"}},
            "step3_2": None, "step3_3": None
        },
        {
            "securityId": "s10", "name": "Fund J", "amountDiff": -1000, "diffRent": 0.0,
            "eliminated": False, "pu": 5, "formerPu": 5, "quantity": 9000, "formerQuantity": 10000,
            "executionPrice": 5,
            "step3_1": {"offset": 0, "status": "ok"}, "step3_2": None,
            "step3_3": {"flag": "WITHHOLDING_TAX", "impact": 2000,
                        "actualBalance": 3000, "expectedValue": 5000, "status": "flag"}
        }
    ], "status": "done"},
    "step4": {"misclassified": [], "unclassified": [], "wrongSecurity": [], "status": "done"},
    "step5": {"cashDiff": 40000, "currentCash": 1000, "formerCash": 1000,
              "projectedCash": 41000, "totalTransactions": 40000,
              "diagnosis": "value_error", "status": "warning"},
    "step6": {"securityAnomalies": [
        {"securityId": "s9", "securityName": "Fund I",
         "currentReturn": -0.50, "mean": 0.001, "stdDev": 0.02, "isAnomaly": True}
    ], "walletAnomaly": None, "status": "warning"}
}

W = 90

print("=" * W)
print("T9: ALL TIERS --- DETAILED WALKTHROUGH")
print("=" * W)

# ── Portfolio ────────────────────────────────────────────────────────────────
s1 = diag["step1"]
print()
print("PORTFOLIO STATE")
print("-" * W)
print(f"  formerNav:          R$ {s1['formerNav']:>14,.2f}")
print(f"  returnNavPerShare:  {s1['returnNavPerShare']:>14.8f}  ({s1['returnNavPerShare']*100:.4f}%)")
print(f"  returnContribution: {s1['returnContribution']:>14.8f}  ({s1['returnContribution']*100:.4f}%)")
print(f"  gapPct:             {s1['gapPct']:>14.8f}  ({s1['gapPct']*100:.4f}%)")
print(f"  gapCash:            R$ {s1['gapCash']:>14,.2f}")
s5 = diag["step5"]
print()
print(f"  Cash: former=R$ {s5['formerCash']:,.2f}  txns=R$ {s5['totalTransactions']:,.2f}"
      f"  projected=R$ {s5['projectedCash']:,.2f}  current=R$ {s5['currentCash']:,.2f}"
      f"  diff=R$ {s5['cashDiff']:,.2f}  ({s5['diagnosis']})")

# ── Securities ───────────────────────────────────────────────────────────────
print()
print("SECURITIES (Step 2: 2 eliminated, 2 suspects)")
print("-" * W)
for sec in diag["step3"]["securities"]:
    bal = sec["pu"] * sec["quantity"]
    f_bal = sec["formerPu"] * sec["formerQuantity"]
    print(f"  {sec['name']} ({sec['securityId']})")
    print(f"    PU:      {sec['formerPu']} -> {sec['pu']}")
    print(f"    Qty:     {sec['formerQuantity']:,} -> {sec['quantity']:,}  (delta: {sec['amountDiff']:+,})")
    print(f"    Balance: R$ {f_bal:,.2f} -> R$ {bal:,.2f}")
    for key in ("step3_1", "step3_2", "step3_3"):
        sub = sec.get(key)
        if not sub:
            continue
        if sub.get("status") == "flag":
            print(f"    {key}: FLAG  {sub['flag']}")
            print(f"           impact = R$ {sub.get('impact', 0):,.2f}  offset = {sub.get('offset', '-')}")
            if sub.get("provisionData"):
                pd = sub["provisionData"]
                print(f"           provision: {pd.get('initialDate')} -> {pd.get('liquidationDate')}  type={pd.get('provisionType')}  balance=R$ {pd.get('balance',0):,.2f}")
            if sub.get("actualBalance") is not None:
                print(f"           actualBalance = R$ {sub['actualBalance']:,.2f}  expectedValue = R$ {sub['expectedValue']:,.2f}")
        elif sub.get("status") == "ok":
            print(f"    {key}: OK  ({sub.get('detail', sub.get('offset', ''))})")
    print()

# ── Anomalies ────────────────────────────────────────────────────────────────
print("ANOMALIES (Step 6)")
print("-" * W)
for a in diag["step6"]["securityAnomalies"]:
    z = abs(a["currentReturn"] - a["mean"]) / a["stdDev"]
    print(f"  {a['securityName']}: return={a['currentReturn']:.4f}  mean={a['mean']:.6f}"
          f"  stdDev={a['stdDev']:.6f}  z-score={z:.1f}")
    print(f"  -> {z:.0f} standard deviations from the mean. Extreme anomaly.")

# ── Factor Extraction ────────────────────────────────────────────────────────
print()
print("FACTOR EXTRACTION")
print("-" * W)
factors = extract_factors(diag, cfg=cfg)
print(f"  gap_cash: R$ {factors['gap_cash']:,.2f}")
print(f"  Flags extracted: {len(factors['flags'])}")
print()

for i, f in enumerate(factors["flags"]):
    tier_label = {1: "Deterministic", 2: "Bounded (MC)", 3: "Indicative"}[f["tier"]]
    print(f"  Flag [{i+1}]: {f['flag']}")
    print(f"    Security:     {f['securityName'] or '(wallet-level)'}")
    print(f"    Signed impact: R$ {f['impact']:>12,.2f}")
    print(f"    Abs impact:    R$ {f['absImpact']:>12,.2f}")
    print(f"    Tier:          {f['tier']} ({tier_label})")
    print(f"    Confidence:    {f['confidence']*100:.0f}%")
    dt = f["distribution"]
    print(f"    Distribution:  {dt['type']}", end="")
    if dt["type"] == "delta":
        print(f"  value=R$ {dt.get('value',0):,.2f}")
    elif dt["type"] == "discrete":
        print(f"  options={dt.get('options',{})}")
    elif dt["type"] == "indicator":
        print(f"  zScore={dt.get('zScore',0)}")
    else:
        print()
    print()

# ── Actionable vs indicative ─────────────────────────────────────────────────
actionable = [f for f in factors["flags"] if f["absImpact"] > 0]
indicative = [f for f in factors["flags"] if f["absImpact"] == 0]
print(f"  Actionable flags: {len(actionable)}  (enter power set)")
for f in actionable:
    print(f"    {f['flag']:30s}  impact=R$ {f['impact']:>12,.2f}  conf={f['confidence']*100:.0f}%")
print(f"  Indicative flags: {len(indicative)}  (excluded from scoring)")
for f in indicative:
    print(f"    {f['flag']:30s}  z={f['distribution'].get('zScore','?')}")

# ── All combinations (deterministic) ─────────────────────────────────────────
print()
print("ALL COMBINATIONS (deterministic, no MC)")
print("-" * W)
det = score_combinations(factors, cfg=cfg, top_k=100)
print(f"  Total scenarios: {det['totalScenarios']} (2^{len(actionable)} - 1 = {2**len(actionable)-1})")
print(f"  noFixPosterior:  {det['noFixPosterior']*100:.6f}%")
print()

all_scored = []
if det["bestFix"]:
    all_scored.append(det["bestFix"])
all_scored.extend(det.get("alternatives", []))

hdr = f"  {'#':<4} {'Flags':<50} {'Total':>10} {'Residual':>10} {'Prior':>8} {'L.hood':>10} {'Poster.':>8} {'Res?':<5}"
print(hdr)
print("  " + "-" * (len(hdr) - 2))
for i, s in enumerate(all_scored):
    flags_str = " + ".join(s["flags"])
    if len(flags_str) > 48:
        flags_str = flags_str[:45] + "..."
    print(f"  {i+1:<4} {flags_str:<50} {s['totalImpact']:>10,.0f} {s['residualGap']:>10,.0f}"
          f" {s.get('prior',0):>8.5f} {s.get('likelihood',0):>10.6f} {s['posterior']*100:>7.3f}% {str(s['gapResolved']):<5}")

# ── Monte Carlo ──────────────────────────────────────────────────────────────
print()
print("MONTE CARLO SCORING (200 samples, 1 Tier-2 flag)")
print("-" * W)
mc = score_combinations_mc(factors, cfg=cfg, top_k=100)
mc_info = mc.get("monteCarlo", {})
print(f"  Samples:       {mc_info.get('samples', '?')}")
print(f"  Tier-2 count:  {mc_info.get('tier2Count', '?')}")
print(f"  noFixPosterior: {mc['noFixPosterior']*100:.6f}%")
print()

mc_all = []
if mc["bestFix"]:
    mc_all.append(mc["bestFix"])
mc_all.extend(mc.get("alternatives", []))

print(f"  {'#':<4} {'Flags':<50} {'Total':>10} {'Residual':>10} {'Poster.':>8} {'Res?':<5}")
print("  " + "-" * 88)
for i, s in enumerate(mc_all):
    flags_str = " + ".join(s["flags"])
    if len(flags_str) > 48:
        flags_str = flags_str[:45] + "..."
    print(f"  {i+1:<4} {flags_str:<50} {s['totalImpact']:>10,.0f} {s['residualGap']:>10,.0f}"
          f" {s['posterior']*100:>7.3f}% {str(s['gapResolved']):<5}")

print()
print("  MC Effect: WITHHOLDING_TAX impact varies across samples")
print(f"  Deterministic WT impact:  R$ {[f['impact'] for f in actionable if f['flag']=='WITHHOLDING_TAX'][0]:,.2f}")
print(f"  MC samples tax rates from: {[f['distribution']['options'] for f in actionable if f['flag']=='WITHHOLDING_TAX'][0]}")

# ── Validation ───────────────────────────────────────────────────────────────
print()
print("VALIDATION")
print("-" * W)
best = mc["bestFix"]
v = validate_fix(diag, best, cfg=cfg)

print(f"  Fix applied: {best['flags']}")
print(f"  totalImpact: R$ {best['totalImpact']:,.2f}")
print()

print("  GAP check:")
print(f"    original gapCash:    R$ {s1['gapCash']:>12,.2f}")
print(f"    - totalImpact:       R$ {best['totalImpact']:>12,.2f}")
print(f"    = newGapCash:        R$ {v['newGapCash']:>12,.2f}")
print(f"    newGapPct:           {v['newGapPct']:>12.8f}  ({v['newGapPct']*100:.6f}%)")
print(f"    tolerance:           {cfg['tolerance']:>12.8f}  ({cfg['tolerance']*100:.6f}%)")
print(f"    |newGapPct| <= tol?  {v['gapResolved']}  {'PASS' if v['gapResolved'] else 'FAIL'}")
print()

print("  CASH check:")
nav_flags = {"MISSING_PROVISION", "MISSING_TRANSACTION", "MISSING_EVENT"}
cash_always = {"CASH_MISMATCH", "UNCLASSIFIED_TRANSACTION", "WITHHOLDING_TAX", "WRONG_TRANSACTION_VALUE"}
cash_conditional = {"MISSING_TRANSACTION"}
for fd in best.get("flagDetails", []):
    if fd["flag"] in cash_always:
        label = "cash-always"
    elif fd["flag"] in cash_conditional:
        label = "cash-conditional (cash broken)"
    elif fd["flag"] in nav_flags:
        label = "NAV-only (no cash effect)"
    else:
        label = "no cash effect"
    print(f"    {fd['flag']:30s}  impact=R$ {fd['impact']:>10,.2f}  -> {label}")

print(f"    formerCash:          R$ {s5['formerCash']:>12,.2f}")
print(f"    + totalTransactions: R$ {s5['totalTransactions']:>12,.2f}")
cash_fix = sum(fd["impact"] for fd in best.get("flagDetails", []) if fd["flag"] in cash_always)
print(f"    + cashFix:           R$ {cash_fix:>12,.2f}  (sum of cash-always flags)")
print(f"    = newProjectedCash:  R$ {v['newProjectedCash']:>12,.2f}")
print(f"    currentCash:         R$ {v['currentCash']:>12,.2f}")
print(f"    |projected-current|: R$ {abs(v['newProjectedCash'] - v['currentCash']):>12,.2f}")
print(f"    cashConsistent?      {v['cashConsistent']}  {'PASS' if v['cashConsistent'] else 'FAIL'}")

print()
print(f"  FINAL: valid = gapResolved AND cashConsistent = {v['valid']}")

# ── Full pipeline result ─────────────────────────────────────────────────────
print()
print("FULL PIPELINE RESULT")
print("-" * W)
full = optimize_with_validation(diag, cfg=cfg)
fb = full["bestFix"]
fv = fb.get("validation", {})
print(f"  Best fix:       {fb['flags']}")
print(f"  Posterior:      {fb['posterior']*100:.4f}%")
print(f"  Valid:          {fv.get('valid')}")
print(f"  Alternatives:   {len(full.get('alternatives', []))}")
print(f"  Indicative:     {[f['flag'] + ' (z=' + str(f['distribution'].get('zScore','?')) + ')' for f in full.get('indicativeFlags', [])]}")
print(f"  Monte Carlo:    {full.get('monteCarlo')}")
print(f"  Validation:     {full.get('validationUsed')}")

print()
print("=" * W)
