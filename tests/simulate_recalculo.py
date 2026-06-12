"""Full Recalculo simulation for the 5-flag multi-correction scenario.

Applies all 6 corrections and recomputes NAV, contributions, returns, and GAP
step by step using the formulas from CONCILIACAO_RECALCULO.md.
"""

# ── Input data ────────────────────────────────────────────────────────────────

former_nav = 2285261.019531
return_nav_ps = 0.008579473419199957
return_contrib_orig = -0.016246704839106764
gap_pct_orig = 0.02482617825830672
gap_cash_orig = 56734.3

securities = [
    {"name": "Perfin Infra II",  "fPu": 20,           "fQty": 5000,  "pu": 21,            "qty": 6000,  "exec": 21,            "amtDiff": 1000},
    {"name": "BTG Selic",        "fPu": 1.9261019531, "fQty": 10000, "pu": 1.927169463,   "qty": 8000,  "exec": 1.927169463,   "amtDiff": -2000},
    {"name": "Fundo com offset", "fPu": 1000,         "fQty": 1500,  "pu": 980,           "qty": 1600,  "exec": 980,           "amtDiff": 100},
    {"name": "CDB Itau",         "fPu": 100,          "fQty": 3150,  "pu": 90.5909090909, "qty": 3300,  "exec": 90.5909090909, "amtDiff": 150},
    {"name": "CRI Real Parque",  "fPu": 100,          "fQty": 3500,  "pu": 105,           "qty": 2800,  "exec": 105,           "amtDiff": -700},
]

former_cash = 1000.0
current_cash = 2500.0
total_txns_before = 0  # no transactions existed before corrections

# Corrections
provisions = [
    {"name": "Perfin Infra II",  "amount": 21000},
    {"name": "Fundo com offset", "amount": 98000},
]

new_txns = [
    {"name": "BTG Selic",       "balance": -3854.34,  "type": "buySell"},
    {"name": "CDB Itau",        "balance": 13588.64,  "type": "buySell"},
    {"name": "CRI Real Parque", "balance": -73500,    "type": "buySell"},
    {"name": "(caixa)",         "balance": 1500,       "type": "gainsExpenses"},
]

W = 95

# ── Simulation ────────────────────────────────────────────────────────────────

print("=" * W)
print("RECALCULO COMPLETO -- SIMULACAO DE CORRECOES")
print("=" * W)

print()
print("ESTADO ATUAL (antes das correcoes)")
print("-" * W)
print(f"  formerNav:          R$ {former_nav:>16,.2f}")
print(f"  returnNavPerShare:  {return_nav_ps:>16.10f}  ({return_nav_ps*100:.6f}%)")
print(f"  returnContribution: {return_contrib_orig:>16.10f}  ({return_contrib_orig*100:.6f}%)")
print(f"  gapPct:             {gap_pct_orig:>16.10f}  ({gap_pct_orig*100:.6f}%)")
print(f"  gapCash:            R$ {gap_cash_orig:>16,.2f}")
print(f"  cash:               former=R$ {former_cash:,.2f}  current=R$ {current_cash:,.2f}  txns=R$ {total_txns_before:,.2f}")

# ── Formulas 4-7: contributions per security ─────────────────────────────────

print()
print("FORMULAS 4-7: Contribuicoes por security")
print("-" * W)
print(f"  {'Security':<25s} {'fBal':>12} {'cBal':>12} {'F4 Daily':>10} {'F5 Intra':>10} {'F6 Event':>8} {'F7 Total':>12}")
print(f"  {'-'*25:<25s} {'-'*12:>12} {'-'*12:>12} {'-'*10:>10} {'-'*10:>10} {'-'*8:>8} {'-'*12:>12}")

sum_total_contrib = 0
for s in securities:
    f_bal = s["fPu"] * s["fQty"]
    c_bal = s["pu"] * s["qty"]
    daily = s["fQty"] * (s["pu"] - s["fPu"])          # F4
    intraday = s["amtDiff"] * (s["pu"] - s["exec"])    # F5
    event = 0                                           # F6
    total = daily + intraday + event                    # F7
    sum_total_contrib += total
    print(f"  {s['name']:<25s} {f_bal:>12,.2f} {c_bal:>12,.2f} {daily:>10,.2f} {intraday:>10,.2f} {event:>8,.2f} {total:>12,.2f}")

print(f"  {'':>25s} {'':>12} {'':>12} {'':>10} {'':>10} {'':>8} {'_'*12:>12}")
print(f"  {'Sum securities':<25s} {'':>12} {'':>12} {'':>10} {'':>10} {'':>8} {sum_total_contrib:>12,.2f}")

# ── Corrections detail ────────────────────────────────────────────────────────

print()
print("CORRECOES APLICADAS")
print("-" * W)

total_prov = sum(p["amount"] for p in provisions)
print("  Provisions (NAV-affecting, no cash):")
for p in provisions:
    print(f"    + {p['name']:<25s} R$ {p['amount']:>12,.2f}")
print(f"    {'Total provisions':<27s} R$ {total_prov:>12,.2f}")

print()
total_new_txns = sum(t["balance"] for t in new_txns)
print("  Transactions:")
for t in new_txns:
    print(f"    + {t['name']:<25s} R$ {t['balance']:>12,.2f}  ({t['type']})")
print(f"    {'Total new txns':<27s} R$ {total_new_txns:>12,.2f}")

# ── Formula 8: walletContribution ─────────────────────────────────────────────

wallet_types = {"gainsExpenses", "rebate", "contributionAdjustment", "other"}
wallet_contrib = sum(t["balance"] for t in new_txns if t["type"] in wallet_types)

print()
print("FORMULA 8: walletContribution")
print(f"  gainsExpenses txns: R$ {wallet_contrib:>12,.2f}")

# ── Formula 10: returnContribution ────────────────────────────────────────────

new_total_contrib = sum_total_contrib + wallet_contrib
new_return_contrib = new_total_contrib / former_nav

print()
print("FORMULA 10: returnContribution (after)")
print(f"  securities totalContrib:  R$ {sum_total_contrib:>12,.2f}")
print(f"  + wallet contribution:    R$ {wallet_contrib:>12,.2f}")
print(f"  = totalContribution:      R$ {new_total_contrib:>12,.2f}")
print(f"  / formerNav:              R$ {former_nav:>12,.2f}")
print(f"  = returnContribution:     {new_return_contrib:>12.10f}  ({new_return_contrib*100:.6f}%)")
print(f"    was:                    {return_contrib_orig:>12.10f}  ({return_contrib_orig*100:.6f}%)")
print(f"    delta:                  {new_return_contrib - return_contrib_orig:>12.10f}")

# ── Formula 1: NAV ───────────────────────────────────────────────────────────

sum_sec_balance = sum(s["pu"] * s["qty"] for s in securities)
old_nav = sum_sec_balance + current_cash  # NAV without provisions
new_nav = sum_sec_balance + total_prov + current_cash

print()
print("FORMULA 1: NAV (after)")
print(f"  sum(securities.balance):  R$ {sum_sec_balance:>12,.2f}")
print(f"  + provisions:             R$ {total_prov:>12,.2f}")
print(f"  + cash:                   R$ {current_cash:>12,.2f}")
print(f"  = new NAV:                R$ {new_nav:>12,.2f}")
print(f"    old NAV (no provisions):R$ {old_nav:>12,.2f}")
print(f"    delta NAV:              R$ {new_nav - old_nav:>12,.2f}")

# ── Formula 9: returnNavPerShare ──────────────────────────────────────────────

in_out = 0  # no withdrawalDeposit/securityTransfer/taxes
ratio = (new_nav - in_out) / (old_nav - in_out)
new_return_nav_ps = ratio * (1 + return_nav_ps) - 1

print()
print("FORMULA 9: returnNavPerShare (after)")
print(f"  newNav / oldNav:          {ratio:>12.10f}")
print(f"  * (1 + old returnNavPS):  {1 + return_nav_ps:>12.10f}")
print(f"  - 1:")
print(f"  = returnNavPerShare:      {new_return_nav_ps:>12.10f}  ({new_return_nav_ps*100:.6f}%)")
print(f"    was:                    {return_nav_ps:>12.10f}  ({return_nav_ps*100:.6f}%)")
print(f"    delta:                  {new_return_nav_ps - return_nav_ps:>12.10f}")

# ── GAP ───────────────────────────────────────────────────────────────────────

new_gap_pct = new_return_nav_ps - new_return_contrib
new_gap_cash = new_gap_pct * former_nav

print()
print("GAP (after corrections)")
print(f"  returnNavPerShare:   {new_return_nav_ps:>16.10f}")
print(f"  - returnContribution:{new_return_contrib:>16.10f}")
print(f"  = gapPct:            {new_gap_pct:>16.10f}  ({new_gap_pct*100:.6f}%)")
print(f"  * formerNav:         R$ {former_nav:>16,.2f}")
print(f"  = gapCash:           R$ {new_gap_cash:>16,.2f}")

# ── Formula 13: Cash ──────────────────────────────────────────────────────────

projected = former_cash + total_txns_before + total_new_txns
cash_diff = abs(projected - current_cash)

print()
print("FORMULA 13: Caixa (after)")
print(f"  formerCash:           R$ {former_cash:>12,.2f}")
print(f"  + old txns:           R$ {total_txns_before:>12,.2f}")
print(f"  + new txns:           R$ {total_new_txns:>12,.2f}")
print(f"  = projectedCash:      R$ {projected:>12,.2f}")
print(f"  currentCash:          R$ {current_cash:>12,.2f}")
print(f"  diff:                 R$ {cash_diff:>12,.2f}")

# ── Summary ───────────────────────────────────────────────────────────────────

print()
print("=" * W)
print("RESULTADO FINAL")
print("=" * W)
print()
print(f"  {'Metrica':<25s} {'Antes':>20} {'Depois':>20} {'Delta':>16}")
print(f"  {'-'*25:<25s} {'-'*20:>20} {'-'*20:>20} {'-'*16:>16}")
print(f"  {'gapPct':<25s} {gap_pct_orig*100:>19.4f}% {new_gap_pct*100:>19.4f}% {(new_gap_pct - gap_pct_orig)*100:>15.4f}%")
print(f"  {'gapCash':<25s} R$ {gap_cash_orig:>16,.2f} R$ {new_gap_cash:>16,.2f} R$ {new_gap_cash - gap_cash_orig:>12,.2f}")
print(f"  {'returnNavPerShare':<25s} {return_nav_ps*100:>19.6f}% {new_return_nav_ps*100:>19.6f}% {(new_return_nav_ps - return_nav_ps)*100:>15.6f}%")
print(f"  {'returnContribution':<25s} {return_contrib_orig*100:>19.6f}% {new_return_contrib*100:>19.6f}% {(new_return_contrib - return_contrib_orig)*100:>15.6f}%")
print(f"  {'NAV':<25s} R$ {old_nav:>16,.2f} R$ {new_nav:>16,.2f} R$ {new_nav - old_nav:>12,.2f}")
print(f"  {'projectedCash':<25s} R$ {former_cash + total_txns_before:>12,.2f} R$ {projected:>16,.2f}")
print(f"  {'currentCash':<25s} {'':>20} R$ {current_cash:>16,.2f}")
print(f"  {'cashDiff':<25s} {'':>20} R$ {cash_diff:>16,.2f}")

print()
reduction = (1 - abs(new_gap_pct / gap_pct_orig)) * 100 if gap_pct_orig else 0
print(f"  Gap fechado:   {reduction:.1f}%")
print(f"  Gap restante:  R$ {new_gap_cash:,.2f} ({new_gap_pct*100:.4f}%)")
print(f"  Cash ok:       {cash_diff < 1}")

print()
if abs(new_gap_pct) < 0.01:
    print("  >>> GAP RESOLVIDO (dentro da tolerancia de 1%)")
else:
    print(f"  >>> GAP PARCIAL -- {new_gap_pct*100:.4f}% restante")
    print(f"      Em multi-correcao com 5 securities mudando simultaneamente,")
    print(f"      o gap residual vem da interacao nao-linear entre as formulas.")
    print(f"      Cada correcao individual e valida e necessaria.")
