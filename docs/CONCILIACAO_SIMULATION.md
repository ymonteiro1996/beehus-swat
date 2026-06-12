# Conciliation Engine — Simulation Report
**Engine:** `pages/conciliacao.py` — `diagnose()` route
**Report date:** 2026-03-26
**Author:** SWAT diagnostic team

---

> ## ⚠ Status deste documento (última revisão: 2026-04-24)
>
> Este é um **documento de design/simulação**, não referência canônica. Tem
> partes válidas, partes históricas e propostas nunca implementadas.
>
> **Onde confiar:**
> - **§1 Carteira sintética "Fundo ALPHA"** — baseline pedagogica, válida
> - **§2-4 Cenários S01-S15** — comportamento conceitualmente correto, mas
>   **nomes de flags estão desatualizados** (veja tabela abaixo)
> - **§7 Captura Automatizada de Cenário** — feature implementada, válida
>
> **Onde NÃO confiar sem verificação:**
> - **§6 "New Flags Required"** — tabela de flags propostos. Vários **nunca
>   foram implementados** (`WRONG_DAILY_CONTRIBUTION`,
>   `WRONG_INTRADAY_CONTRIBUTION`, `TOTAL_CONTRIBUTION_ARITHMETIC_ERROR`,
>   `DUPLICATE_TRANSACTION`, `ORPHAN_PROVISION`, `WALLET_CASH_TRANSACTION`).
>   Consulte o código em [pages/conciliacao.py](../pages/conciliacao.py) e
>   [pages/bayesian.py](../pages/bayesian.py) para a lista atual.
>
> **Renomeações aplicadas (nomes neste doc → nomes em uso hoje):**
>
> | Neste doc | Código atual |
> |---|---|
> | `MISSING_BUYSELL_TRANSACTION` | `MISSING_TRANSACTION` |
> | `MISSING_EVENT_TRANSACTION` | `MISSING_EVENT` |
> | `WRONG_TRANSACTION_BALANCE` | `WRONG_TRANSACTION_VALUE` |
> | `WRONG_EVENT_TRANSACTION` | `WRONG_EVENT_BALANCE` |
>
> **Flags ausentes deste doc** (introduzidos depois): `WRONG_SECURITY`,
> `MISCLASSIFIED`, `MISCLASSIFIED_EVENT_TYPE`, `CASH_MISMATCH`,
> `OFFSET_OR_SETTLEMENT_DRIFT`, `DATA_QUALITY_ERROR`, `LEGITIMATE_*`. Veja
> [CONCILIACAO_DIAGNOSTICO.md](CONCILIACAO_DIAGNOSTICO.md) e
> [CONCILIACAO_BAYESIAN.md](CONCILIACAO_BAYESIAN.md) para a referência
> canônica de flags atuais.

---

## 1. Synthetic Portfolio "Fundo ALPHA"

### 1.1 Former position (T-1 = 2024-01-12)

| Security | Type | qty | PU (R$) | Balance (R$) |
|---|---|---|---|---|
| PETR4 | equity (offset=0) | 2,000 | 38.50 | 77,000.00 |
| NTNB052035 | fixed-income (offset=0) | 100 | 3,420.00 | 342,000.00 |
| LFT | fixed-income (offset=0) | 50 | 10,120.00 | 506,000.00 |
| FII_XPML11 | fii (offset=0) | 1,000 | 102.50 | 102,500.00 |
| CRI_SECURIT | cri (subscriptionOffset=3) | 200 | 1,200.00 | 240,000.00 |
| Cash | — | — | — | 180,000.00 |
| **formerNAV** | | | | **1,447,500.00** |

### 1.2 Current position (T = 2024-01-15) — CLEAN baseline

| Security | qty | PU | exec_price | daily_c | intraday_c | event_c | total_c |
|---|---|---|---|---|---|---|---|
| PETR4 | 1,800 | 39.20 | 38.90 | 1,400.00 | -60.00 | 0 | 1,340.00 |
| NTNB052035 | 100 | 3,426.50 | — | 650.00 | 0 | 0 | 650.00 |
| LFT | 50 | 10,135.60 | — | 780.00 | 0 | 0 | 780.00 |
| FII_XPML11 | 1,000 | 100.80 | — | -1,700.00 | 0 | 1,800.00 | 100.00 |
| CRI_SECURIT | 250 | 1,201.80 | 1,200.00 | 360.00 | 90.00 | 0 | 450.00 |

**Calculation notes:**
- PETR4: amountDiff = 1800 - 2000 = −200; daily = 2000 × (39.20 − 38.50) = 1,400; intraday = −200 × (39.20 − 38.90) = −60
- FII_XPML11: amountDiff = 0; daily = 1000 × (100.80 − 102.50) = −1,700; eventContribution = 1,800 (dividend received)
- CRI_SECURIT: amountDiff = 250 − 200 = +50; daily = 200 × (1,201.80 − 1,200.00) = 360; intraday = 50 × (1,201.80 − 1,200.00) = 90

### 1.3 Transactions on T

| Security | Type | Balance (R$) | Notes |
|---|---|---|---|
| PETR4 | buySell | +7,780.00 | 200 shares × 38.90 sold |
| FII_XPML11 | dividend | +1,800.00 | received |
| CRI_SECURIT | provision | −60,000.00 | 50 × 1,200; liquidationDate = T+3 |

**Cash(T) = 180,000 + 7,780 + 1,800 = 189,580.00**

### 1.4 NAV(T) reconstruction

| Component | Value (R$) |
|---|---|
| PETR4: 1,800 × 39.20 | 70,560.00 |
| NTNB052035: 100 × 3,426.50 | 342,650.00 |
| LFT: 50 × 10,135.60 | 506,780.00 |
| FII_XPML11: 1,000 × 100.80 | 100,800.00 |
| CRI_SECURIT: 250 × 1,201.80 | 300,450.00 |
| Cash | 189,580.00 |
| CRI provision (liability offset) | −60,000.00 |
| **NAV(T)** | **1,450,820.00** |

### 1.5 Baseline verification

```
totalContribution = 1,340 + 650 + 780 + 100 + 450 = 3,320.00
NAV change        = 1,450,820 − 1,447,500 = 3,320.00  ✓

returnNavPerShare  = (1,450,820 / 1,447,500) − 1 = 0.22936%
returnContribution = 3,320 / 1,447,500             = 0.22936%
gap_pct  = 0.00000%
gap_cash = 0.00
```

**Clean baseline confirmed: zero gap.**

---

## 2. Issue Scenarios

---

### S01: Missing buySell (immediate settlement)

**What changes:** The PETR4 buySell transaction is removed from the transaction register.

**Modified values:**
- PETR4 buySell transaction: +7,780.00 → removed entirely

**Effect on indicators:**
- Cash(T) remains 189,580 (actual cash account unchanged — real money moved)
- projected_cash = 180,000 + 0 + 1,800 = 181,800 (engine sees no buySell)
- returnContribution = 3,320 / 1,447,500 = 0.22936% (unchanged — contributions correct)
- returnNavPerShare = unchanged (NAV still 1,450,820 — actual cash is there)
- gap_pct = 0.000%
- gap_cash = 0.00
- cash_diff = projected_cash − actual_cash = 181,800 − 189,580 = **−7,780**

**Diagnostic signature:** NAV ratios agree perfectly but cash reconciliation fails. The missing transaction is invisible at the portfolio return level.

**Existing flag fires?** YES — `MISSING_BUYSELL_TRANSACTION`
Condition: amountDiff = −200, offset = 0, no buySell transaction found.
impact = |−200| × 38.90 = 7,780.

**Gap caught?** PARTIAL — cash_diff alert fires (−7,780), but gap_cash = 0 so `_best_scenarios` returns nothing. The flag exists but cannot be ranked in the scenario scoring engine.

**Notes:** This is the key insight: `MISSING_BUYSELL_TRANSACTION` is a **cash alert**, not a NAV-gap alert. When actual cash is correct (money moved externally), the NAV is fine and gap_pct = 0. The flag correctly identifies the missing record for audit purposes but does not contribute to scenario scoring. An analyst must look at cash_diff separately.

---

### S02: Missing provision (deferred settlement)

**What changes:** The CRI_SECURIT provision is removed entirely.

**Modified values:**
- CRI provision: −60,000.00 → removed

**Effect on indicators:**
- Cash(T) = 189,580 (unchanged — no cash has moved yet at D+3)
- NAV(T) without provision offset = 1,450,820 + 60,000 = **1,510,820**
- returnNavPerShare = (1,510,820 − 1,447,500) / 1,447,500 = **4.3752%**
- returnContribution = 3,320 / 1,447,500 = 0.2294%
- gap_pct = 4.3752% − 0.2294% = **+4.1458%**
- gap_cash = 4.1458% × 1,447,500 = **+60,000**

**Diagnostic signature:** Massive positive gap equal to the missing provision value.

**Existing flag fires?** YES — `MISSING_PROVISION`
Condition: amountDiff = +50, offset = 3 (subscriptionSettlementDays − subscriptionNavDays), sid not in prov_map.
impact = 50 × 1,200.00 = 60,000.

**Gap caught?** YES — 100% coverage (impact = 60,000 = gap_cash).

**Notes:** The most common and cleanest failure mode. The provision is the liability that offsets the inflated NAV from assets that were subscribed but not yet settled.

---

### S03: Wrong provision amount (fat-finger entry)

**What changes:** CRI provision amount recorded as −30,000 instead of the correct −60,000.

**Modified values:**
- CRI provision amount: −60,000.00 → −30,000.00

**Effect on indicators:**
- NAV(T) = 1,450,820 − 30,000 = **1,420,820** (provision only partially offsets)

  Wait — correct NAV assumes full −60,000 offset. Stored provision is −30,000, so:
  NAV(T) as computed by the system = 1,450,820 − 30,000 = **1,420,820**

  Actually the correct NAV (with full provision) is 1,450,820. The stored NAV uses the stored provision, so:
  - Stored NAV(T) = base_assets + cash − stored_provision = (1,450,820 + 60,000 − 60,000) using stored_provision=30,000 → = 1,450,820 − 30,000 + 0 ...

  To clarify: asset values (equity, bonds, cash) sum to 1,510,820 gross. Provision of 30,000 is subtracted → stored NAV = 1,510,820 − 30,000 = **1,480,820**.

- returnNavPerShare = (1,480,820 − 1,447,500) / 1,447,500 = **2.3025%**
- returnContribution = 0.2294%
- gap_pct = 2.3025% − 0.2294% = +2.0731%
- gap_cash = 2.0731% × 1,447,500 = **+30,000**

**Diagnostic signature:** Positive gap exactly equal to the shortfall in provision (correct − stored = 60,000 − 30,000 = 30,000).

**Existing flag fires?** YES — `WRONG_PROVISION_AMOUNT`
expected = |50| × 1,200 = 60,000; stored = 30,000; impact = |30,000 − 60,000| = 30,000.

**Gap caught?** YES — 100% coverage.

**Notes:** Requires executionPrice to be populated. If exec_price is null, the flag is silently skipped (condition `if prov_stored is not None and exec_price`).

---

### S04: Transaction balance sign flip (sold but recorded as bought)

**What changes:** PETR4 buySell balance sign inverted: the fund paid instead of received.

**Modified values:**
- PETR4 buySell balance: +7,780.00 → −7,780.00

**Effect on indicators:**
- Cash(T) = 180,000 + (−7,780) + 1,800 = **174,020**
- NAV(T) = 70,560 + 342,650 + 506,780 + 100,800 + 300,450 + 174,020 − 60,000 = **1,435,260**
- returnNavPerShare = (1,435,260 − 1,447,500) / 1,447,500 = **−0.8459%**
- returnContribution = 0.2294%
- gap_pct = −0.8459% − 0.2294% = −1.0753%
- gap_cash = −1.0753% × 1,447,500 = **−15,564** (≈ −2 × 7,780 − rounding)

  Exact: correct balance = +7,780; stored balance = −7,780; impact = |−7,780 − 7,780| = 15,560.

**Diagnostic signature:** Large negative gap, approximately double the trade value.

**Existing flag fires?** YES — `WRONG_TRANSACTION_BALANCE`
correctBalance = −(−200) × 38.90 = +7,780; actualBalance = −7,780; impact = |−7,780 − 7,780| = 15,560.

**Gap caught?** YES — 100% coverage (impact = 15,560 ≈ gap_cash).

**Notes:** Classic sign-flip error. The gap is ~2× the trade value because cash moved in the wrong direction entirely. The engine correctly computes impact using the formula `−amountDiff × executionPrice`.

---

### S05: Missing event transaction (FII dividend received but not registered)

**What changes:** FII_XPML11 dividend transaction removed. Cash(T) decreases by 1,800.

**Modified values:**
- FII_XPML11 dividend transaction: +1,800.00 → removed

**Effect on indicators:**
- Cash(T) = 180,000 + 7,780 + 0 = **187,780**
- NAV(T) = 70,560 + 342,650 + 506,780 + 100,800 + 300,450 + 187,780 − 60,000 = **1,449,020**
- returnNavPerShare = (1,449,020 − 1,447,500) / 1,447,500 = **0.1050%**
- returnContribution = 3,320 / 1,447,500 = 0.2294%
- gap_pct = 0.1050% − 0.2294% = **−0.1244%**
- gap_cash = −0.1244% × 1,447,500 = **−1,800**
- FII diffRent: returnPU = (100.80 + 1800/1000) / 102.50 − 1 = 102.60/102.50 − 1 = 0.0976%; returnContrib = 100/(1000×102.50) = 0.0976% → diffRent = 0 (position is correct, but no cash txn)

  Re-check: totalContribution for FII = 100 (daily −1700 + event 1800). returnContrib = 100/102,500 = 0.0976%. returnPU = (100.80 + 1.80)/102.50 − 1 = 102.60/102.50 − 1 = 0.09756%. diffRent ≈ 0 — they match because eventContribution IS in total_c.

  The engine sees: diff_rent ≈ 0, sec_txns = [] (dividend removed). Since diff_rent = 0 and sec_txns empty, the security is INELIGIBLE. However gap_cash = −1,800 is real.

**Diagnostic signature:** Negative gap equal to dividend amount, but the responsible security is ineligible (diffRent = 0 hides it).

**Existing flag fires?** NO — FII is ineligible (diff_rent ≈ 0, no txns). The 7c MISSING_CASH_TRANSACTION requires `not has_any_txns` — but PETR4 buySell exists so has_any_txns = True.

**Gap caught?** NO — 0% coverage. The gap is structurally invisible to the engine.

**Notes:** This is a genuine coverage gap in the engine. When eventContribution is correctly stored in the position document but the corresponding cash transaction is absent, diffRent stays at 0 (the two sides of the equation balance against each other). The engine cannot distinguish "event happened, cash recorded" from "event happened, cash missing." A dedicated check comparing eventContribution to the sum of event-type transactions per security would be needed.

---

### S06: Wrong event transaction amount (dividend amount doubled)

**What changes:** FII_XPML11 dividend transaction amount doubled.

**Modified values:**
- FII_XPML11 dividend transaction: +1,800.00 → +3,600.00

**Effect on indicators:**
- Cash(T) = 180,000 + 7,780 + 3,600 = **191,380**
- NAV(T) = 70,560 + 342,650 + 506,780 + 100,800 + 300,450 + 191,380 − 60,000 = **1,452,620**
- returnNavPerShare = (1,452,620 − 1,447,500) / 1,447,500 = **0.3538%**
- returnContribution = 3,320 / 1,447,500 = 0.2294%
- gap_pct = 0.3538% − 0.2294% = **+0.1245%**
- gap_cash = 0.1245% × 1,447,500 = **+1,800**
- FII diffRent: same as S05 analysis — diffRent ≈ 0 (position contributions are internally consistent regardless of transaction amount)

  But wait: the transaction EXISTS, and diffRent ≈ 0. For `WRONG_EVENT_TRANSACTION` to fire we need `diff_rent != 0 AND evt_txns`. Since diffRent = 0, **the flag does not fire**.

**Diagnostic signature:** Positive gap of 1,800 with no flag fires. Similar to S05 but opposite direction.

**Existing flag fires?** NO — diffRent = 0 for FII, so `WRONG_EVENT_TRANSACTION` condition is not met.

**Gap caught?** NO — 0% coverage.

**Notes:** Both S05 and S06 reveal a structural limitation: when the position document's eventContribution is correct, the diffRent check is clean regardless of what happened to the cash. The engine's coverage of event transaction errors depends entirely on eventContribution being wrong in the position document.

---

### S07: Daily contribution computed with wrong former PU

**What changes:** PETR4 dailyContribution stored with incorrect formerPU (38.00 instead of 38.50).

**Modified values:**
- PETR4 dailyContribution: 1,400.00 → 1,540.00 (as if formerPU = 38.00: 2000 × (39.20 − 38.00) = 2,400...

  Actually 2000 × (39.20 − 38.00) = 2,400 — but the prompt states stored as 1,540. Let's use the given value.
  Stored: daily_c = 1,540; total_c = 1,540 + (−60) + 0 = 1,480

**Effect on indicators:**
- portfolioTotalContrib = 1,480 + 650 + 780 + 100 + 450 = **3,460**
- returnContribution = 3,460 / 1,447,500 = **0.2390%**
- returnNavPerShare = 0.2294% (NAV uses actual prices — unchanged)
- gap_pct = 0.2294% − 0.2390% = **−0.0096%**
- gap_cash = −0.0096% × 1,447,500 = **−139** (≈ −140)
- PETR4 diffRent: returnPU = 39.20/38.50 − 1 = 1.8182%; returnContrib = 1,480/(2,000 × 38.50) = 1.9221%; diffRent = **−0.1039%**

  Since diffRent ≠ 0 and sec_txns = [buySell +7,780], `WRONG_EVENT_TRANSACTION` fires:
  evt_txns = [t for t in sec_txns if type in {coupon, amortization}] = [] (buySell is not an event type; dividend is not in _EVENT_TYPES)
  So `WRONG_EVENT_TRANSACTION` does NOT fire. `MISSING_EVENT_TRANSACTION` requires `not sec_txns` — also does not fire (buySell exists).

**Diagnostic signature:** Small negative gap (~140), diffRent ≠ 0 for PETR4, but no existing flag covers daily contribution arithmetic errors.

**Existing flag fires?** PARTIAL — PETR4 becomes eligible (diffRent ≠ 0), and `WRONG_TRANSACTION_BALANCE` fires because `correctBalance = −(−200) × 38.90 = +7,780` vs `actualBalance = +7,780` → impact = 0. So WRONG_TRANSACTION_BALANCE does NOT fire either (impact = 0).

**Gap caught?** NO — 0% coverage. No existing flag addresses incorrect dailyContribution values.

**New flag needed:** `WRONG_DAILY_CONTRIBUTION`
Expected: 2,000 × (39.20 − 38.50) = 1,400; stored: 1,540; impact: |1,540 − 1,400| = 140.

---

### S08: Intraday contribution computed at wrong execution price

**What changes:** PETR4 intradayContribution stored as −120 (as if executionPrice = 38.80 instead of 38.90).

**Modified values:**
- PETR4 intradayContribution: −60.00 → −120.00
- PETR4 totalContribution: 1,340.00 → 1,280.00 (= 1,400 − 120 + 0)

**Effect on indicators:**
- portfolioTotalContrib = 1,280 + 650 + 780 + 100 + 450 = **3,260**
- returnContribution = 3,260 / 1,447,500 = **0.2252%**
- returnNavPerShare = 0.2294%
- gap_pct = 0.2294% − 0.2252% = **+0.0041%**
- gap_cash = +0.0041% × 1,447,500 = **+60**
- PETR4 diffRent: returnPU = 39.20/38.50 − 1 = 1.8182%; returnContrib = 1,280/(77,000) = 1.6623%; diffRent = **+0.1559%**

  diffRent ≠ 0, buySell exists → WRONG_EVENT_TRANSACTION check: evt_txns = [] → does not fire.
  WRONG_TRANSACTION_BALANCE check: correctBalance = +7,780; actualBalance = +7,780 → impact = 0 → does not fire.

**Diagnostic signature:** Small positive gap of 60, diffRent ≠ 0 for PETR4, no flag fires.

**Existing flag fires?** NO — no existing flag covers wrong intradayContribution.

**Gap caught?** NO — 0% coverage.

**New flag needed:** `WRONG_INTRADAY_CONTRIBUTION`
Expected: −200 × (39.20 − 38.90) = −60; stored: −120; impact: |−120 − (−60)| = 60.

---

### S09: Arithmetic error in totalContribution (daily + intraday + event ≠ total)

**What changes:** PETR4 totalContribution stored as 1,200 (arithmetic error; correct sum is 1,400 + (−60) + 0 = 1,340).

**Modified values:**
- PETR4 totalContribution: 1,340.00 → 1,200.00 (components daily/intraday/event unchanged)

**Effect on indicators:**
- portfolioTotalContrib = 1,200 + 650 + 780 + 100 + 450 = **3,180**
- returnContribution = 3,180 / 1,447,500 = **0.2197%**
- returnNavPerShare = 0.2294%
- gap_pct = **+0.0097%**
- gap_cash = **+140** (= 1,340 − 1,200)
- PETR4 diffRent: returnPU = 39.20/38.50 − 1 = 1.8182%; returnContrib = 1,200/77,000 = 1.5584%; diffRent = **+0.2598%**

  diffRent ≠ 0, sec_txns = [buySell]. evt_txns = [] → WRONG_EVENT_TRANSACTION does not fire.
  MISSING_EVENT_TRANSACTION: sec_txns not empty → does not fire.
  WRONG_TRANSACTION_BALANCE: impact = 0 → does not fire.

**Diagnostic signature:** Small positive gap of 140, diffRent ≠ 0 for PETR4, no flag fires despite the error being purely arithmetic in the position document.

**Existing flag fires?** NO.

**Gap caught?** NO — 0% coverage.

**New flag needed:** `TOTAL_CONTRIBUTION_ARITHMETIC_ERROR`
Expected: daily_c + intraday_c + event_c = 1,400 + (−60) + 0 = 1,340; stored: 1,200; impact: |1,200 − 1,340| = 140.

---

### S10: Duplicate buySell transaction (trade recorded twice)

**What changes:** A second PETR4 buySell transaction is added with the same balance.

**Modified values:**
- PETR4 buySell transactions: [+7,780.00] → [+7,780.00, +7,780.00]

**Effect on indicators:**
- Cash(T) = 180,000 + 7,780 + 7,780 + 1,800 = **197,360**
- NAV(T) = 70,560 + 342,650 + 506,780 + 100,800 + 300,450 + 197,360 − 60,000 = **1,458,600**
- returnNavPerShare = (1,458,600 − 1,447,500) / 1,447,500 = **0.7671%**
- returnContribution = 0.2294%
- gap_pct = **+0.5377%**
- gap_cash = 0.5377% × 1,447,500 = **+7,780**
- PETR4 WRONG_TRANSACTION_BALANCE: loops over ALL buySell txns. First: actualBal = +7,780, correctBal = +7,780 → impact 0. Second: same → impact 0. **Does not fire for either individually.**

  However the sum of both = +15,560 vs correct +7,780 → net impact = +7,780 from double-recording.

**Diagnostic signature:** Positive gap equal to one trade value; two buySell transactions for the same security on the same date; existing per-transaction loop misses the duplication.

**Existing flag fires?** PARTIAL — `WRONG_TRANSACTION_BALANCE` checks each transaction individually; both are individually correct so neither fires. The duplication is missed.

**Gap caught?** NO — 0% coverage with existing flags.

**New flag needed:** `DUPLICATE_TRANSACTION`
Count of buySell transactions = 2 > 1; total buySell balance = +15,560; correct = +7,780; impact = |15,560 − 7,780| = 7,780.

---

### S11: Orphan provision (security liquidated but provision not cleared)

**What changes:** An active provision exists for VALE3 (not in current position — fully liquidated last week).

**Modified values:**
- New active provision: VALE3, amount = −25,000, initialDate = 2024-01-10, liquidationDate = 2024-01-18

**Effect on indicators:**
- NAV(T) includes −25,000 provision offset for a security no longer held
- NAV(T) = 1,450,820 − 25,000 = **1,425,820**
- returnNavPerShare = (1,425,820 − 1,447,500) / 1,447,500 = **−1.4981%**
- returnContribution = 0.2294%
- gap_pct = **−1.7275%**
- gap_cash = **−25,000**

**Diagnostic signature:** Large negative gap equal to stale provision amount.

**Existing flag fires?** NO — `prov_map` is built by iterating over provisions and keying by securityId, but the check only happens inside `if sid not in prov_map` for securities IN the current position. VALE3 is not in current_secs, so it is never checked. The stale provision is silently counted in NAV(T).

**Gap caught?** NO — 0% coverage.

**New flag needed:** `ORPHAN_PROVISION`
Active provision for VALE3 (not in current position); impact = 25,000.

---

### S12: Missing execution price (amountDiff known, executionPrice null)

**What changes:** PETR4 executionPrice removed from position document.

**Modified values:**
- PETR4 executionPrice: 38.90 → null

**Effect on indicators:**
- intradayContribution becomes uncomputable; stored as 0 if system silently defaults → total_c = 1,400 + 0 + 0 = 1,400 (wrong; should be 1,340)
- portfolioTotalContrib = 1,400 + 650 + 780 + 100 + 450 = **3,380**
- returnContribution = 3,380 / 1,447,500 = **0.2335%**
- returnNavPerShare = 0.2294%
- gap_pct = **−0.0041%**
- gap_cash = **−60**

**Diagnostic signature:** Small negative gap; all settlement flags skipped because exec_price is required for their computations.

**Existing flag fires?** PARTIALLY — `MISSING_BUYSELL_TRANSACTION` condition: `elif offset == 0` — the buySell DOES exist in transactions, so `has_buysell = True` → does NOT fire. `WRONG_TRANSACTION_BALANCE`: condition `if amt_diff and exec_price` — exec_price is None → **skipped entirely**. No flags fire for PETR4's settlement issue.

**Gap caught?** NO — 0% coverage. The missing executionPrice silences all downstream checks.

**New flag needed:** `MISSING_EXECUTION_PRICE`
Condition: amountDiff ≠ 0 AND executionPrice is null; impact = None (Tier 2 — we cannot quantify).

---

### S13: Orphan transaction (wrong securityId on buySell)

**What changes:** PETR4 buySell transaction has securityId changed to "UNKNOWN_SEC".

**Modified values:**
- PETR4 buySell securityId: "PETR4_ID" → "UNKNOWN_SEC"

**Effect on indicators:**
- txns_by_security: PETR4 has [] ; UNKNOWN_SEC has [buySell +7,780]
- PETR4: amountDiff = −200, offset = 0, no buySell → `MISSING_BUYSELL_TRANSACTION` fires (impact = 200 × 38.90 = 7,780)
- UNKNOWN_SEC not in current_sec_ids → promoted to wallet_txns → `ORPHAN_TRANSACTION` fires (impact = 7,780)
- projected_cash: includes UNKNOWN_SEC txn (orphans added to wallet_txns before total_txns is summed) → projected_cash = 180,000 + 7,780 + 1,800 = 189,580 = Cash(T) → cash_diff = 0 ✓
- NAV(T) unchanged (assets still there; cash accounts show 189,580)
- returnNavPerShare = 0.2294%; returnContribution = 0.2294%
- gap_pct = 0.0000%
- gap_cash = 0.00

**Diagnostic signature:** Zero NAV gap but two flags fire simultaneously pointing to the same 7,780 — double-counting. Cash reconciliation is clean.

**Existing flag fires?** YES — both `MISSING_BUYSELL_TRANSACTION` (PETR4) and `ORPHAN_TRANSACTION` (wallet-level).

**Gap caught?** N/A — gap_cash = 0, _best_scenarios returns nothing. The flags correctly identify the data quality issue.

**Notes:** The two flags together represent a double-count: one missing + one orphan = same trade misrouted. A mutual exclusion rule would improve diagnosis: if orphan buySell balance ≈ expected buySell for a security with MISSING_BUYSELL, collapse both into a single "MISROUTED_TRANSACTION" flag.

---

### S14: Wallet-level gainsExpenses transaction not in totalContribution

**What changes:** A gainsExpenses transaction of +2,000 is recorded at wallet level (no securityId) but was not incorporated into processedPosition.totalContribution.

**Modified values:**
- New wallet-level transaction: type=gainsExpenses, balance=+2,000, securityId=null
- processedPosition.totalContribution components: unchanged (does not include +2,000)

**Effect on indicators:**
- Cash(T) = 180,000 + 7,780 + 1,800 + 2,000 = **191,580**
- NAV(T) = 1,450,820 + 2,000 = **1,452,820**
- returnNavPerShare = (1,452,820 − 1,447,500) / 1,447,500 = **0.3673%**
- returnContribution = 3,320 / 1,447,500 = 0.2294%
- gap_pct = **+0.1379%**
- gap_cash = **+2,000**
- has_any_txns = True (wallet_txns now includes the gainsExpenses)

**Diagnostic signature:** Positive gap of 2,000; wallet-level classified transaction exists; no security is eligible; MISSING_CASH_TRANSACTION won't fire (requires `not has_any_txns`).

**Existing flag fires?** NO — 7c requires `not has_any_txns`, but gainsExpenses makes has_any_txns = True. No security-level flag covers wallet-level transactions. The gap is invisible.

**Gap caught?** NO — 0% coverage.

**New flag needed:** `WALLET_CASH_TRANSACTION`
Wallet-level transaction of type gainsExpenses (NOT in _INFLOW_TYPES) exists AND gap remains unexplained; impact = 2,000.

---

### S15: Round-trip trade same day (buy+sell netting to zero amountDiff)

**What changes:** NTNB052035 has two buySell transactions on the same day that net to zero quantity change — bought 20 at 3,424 then sold 20 at 3,427. A trading profit of +60 is realized in cash.

**Modified values:**
- NTNB buySell transactions added: buy 20 at 3,424 (balance = −68,480), sell 20 at 3,427 (balance = +68,540)
- NTNB amountDiff = 0 (still 100 units)

**Effect on indicators:**
- Cash(T) = 180,000 + 7,780 + 1,800 + (−68,480 + 68,540) = 189,580 + 60 = **189,640**
- NAV(T) = 1,450,820 + 60 = **1,450,880**
- returnNavPerShare = (1,450,880 − 1,447,500) / 1,447,500 = **0.2336%**
- returnContribution = 3,320 / 1,447,500 = 0.2294%
- gap_pct = **+0.0042%**
- gap_cash = **+60**
- NTNB diffRent: returnPU = (3,426.50 + 0) / 3,420 − 1 = 0.1901%; returnContrib = 650/(100 × 3,420) = 0.1901% → diffRent ≈ 0

  sec_txns for NTNB = [buySell −68,480, buySell +68,540] — NOT empty.
  Eligibility check: `(not amt_diff) and (not event_c) and (not sec_txns) and (not diff_rent)` → sec_txns is not empty → **eligible**.
  But all flags: diffRent = 0 so MISSING/WRONG_EVENT don't fire. amt_diff = 0 so settlement checks skip. WRONG_TRANSACTION_BALANCE: no loop (condition `if amt_diff and exec_price` — amt_diff = 0 → skipped).

**Diagnostic signature:** Small positive gap of 60; NTNB is technically eligible (has transactions) but no flag fires. The intraday trading profit from the round-trip is invisible to the contribution formula.

**Existing flag fires?** NO — the round-trip nets to zero amountDiff, bypassing all settlement checks. diffRent = 0 bypasses event checks. The +60 profit is structurally undetectable.

**Gap caught?** NO — 0% coverage.

**Notes:** This is an unsolvable scenario for the current deterministic engine (see U02 below). The only remedy would be to check whether the sum of buySell transaction balances for a security with amountDiff=0 is non-zero, and flag it as `INTRADAY_ROUNDTRIP_PNL`. However this would produce false positives for securities with legitimate zero-qty changes and event cash flows.

---

## 3. Scenarios Summary Table

| Scenario | Gap Source | gap_cash (R$) | Detected? | Flag | Coverage |
|---|---|---|---|---|---|
| S01 | Missing buySell txn | 0 (cash diff −7,780) | YES (cash alert only) | MISSING_BUYSELL_TRANSACTION | cash_diff only |
| S02 | Missing provision | +60,000 | YES | MISSING_PROVISION | 100% |
| S03 | Wrong provision amount | +30,000 | YES | WRONG_PROVISION_AMOUNT | 100% |
| S04 | buySell balance sign flip | −15,560 | YES | WRONG_TRANSACTION_BALANCE | 100% |
| S05 | Missing event transaction | −1,800 | NO | — | 0% |
| S06 | Wrong event transaction amount | +1,800 | NO | — | 0% |
| S07 | Wrong dailyContribution | −140 | NO | — | 0% |
| S08 | Wrong intradayContribution | +60 | NO | — | 0% |
| S09 | totalContribution arithmetic error | +140 | NO | — | 0% |
| S10 | Duplicate buySell transaction | +7,780 | NO | — | 0% |
| S11 | Orphan stale provision | −25,000 | NO | — | 0% |
| S12 | Missing executionPrice | −60 | NO (Tier 2 only) | MISSING_EXECUTION_PRICE* | 0% (Tier 2) |
| S13 | Misrouted buySell (wrong secId) | 0 | YES (data quality) | MISSING_BUYSELL + ORPHAN_TRANSACTION | N/A |
| S14 | Wallet gainsExpenses not in contrib | +2,000 | NO | — | 0% |
| S15 | Round-trip intraday P&L | +60 | NO | — | 0% |

*MISSING_EXECUTION_PRICE is documented but not yet implemented in code (Tier 2, no impact quantification).

**Overall coverage (gap_cash scenarios):** 3/13 quantifiable gap scenarios fully covered (S02, S03, S04). S01 detected as cash alert only. 9 scenarios entirely missed.

---

## 4. Unsolvable Scenarios

### U01: Pricing vendor error (PU systematically wrong)

All PUs shifted by +0.5% due to a vendor feed error. Both `returnNavPerShare` and `returnContribution` shift equally because both formulas use the same PUs. Gap = 0. Reconciliation passes. The error is invisible to the engine by design.

**Why unsolvable:** Without an independent reference price, the engine cannot detect a systematic bias. External price validation (benchmark comparison, cross-vendor check) is needed.

---

### U02: Round-trip trades netting zero quantity

As documented in S15. When a security is bought and sold on the same day in equal quantities, amountDiff = 0, and the intraday P&L (positive or negative) goes undetected. The contribution formula does not account for round-trip profits/losses.

**Why unsolvable:** The intradayContribution formula `amountDiff × (PU − executionPrice)` is defined only for net quantity changes. A round-trip requires knowing the individual leg prices, which are in transaction records but not in the position document.

---

### U03: Settlement date mismatch (transaction on wrong date)

A buySell is recorded with `liquidationDate = T+1` instead of T. On day T: no buySell found → `MISSING_BUYSELL_TRANSACTION` fires. On day T+1: the transaction appears as an orphan (the position has already moved on).

**Why partially solvable:** The engine correctly identifies the symptom (missing buySell on T) but cannot determine whether the root cause is a truly missing record or an incorrectly dated one. Distinguishing the two requires checking neighboring dates, which is outside the current engine scope.

---

### U04: Corporate action — stock split without PU adjustment

PETR4 splits 2:1. The position records qty = 4,000 and PU = 19.60, but formerPU remains 38.50 (not halved to 19.25). The daily contribution becomes: 2,000 × (19.60 − 38.50) = −37,800 (massively wrong, should be ≈ 0). The engine fires `WRONG_DAILY_CONTRIBUTION` (new flag) because stored daily ≠ expected daily — but the "expected" daily is itself wrong because formerPU was not adjusted.

**Why unsolvable:** The engine cannot distinguish a stock split from a genuine price crash. The corporate action adjustment must come from an external event registry. If formerPU is not correctly adjusted, all contribution formulas produce garbage.

---

### U05: FX conversion rate error (foreign security)

A USD-denominated bond is held. NAV conversion uses R$/USD = 5.00; contribution formula uses R$/USD = 4.95. Both ratios are internally self-consistent but diverge from each other due to the FX mismatch.

**Why unsolvable:** No deterministic flag can catch this without a single authoritative FX reference rate. The engine would need to compare the FX rate used in NAV calculation against the rate used in contribution calculation, information not currently stored in the position document.

---

## 5. Multi-Flag Combination Scenarios

### C01: Trade + provision same security (contradictory flags)

A security with offset=3 has a buySell transaction instead of a provision. `MISSING_PROVISION` fires (impact = expected provision value) AND `ORPHAN_TRANSACTION` fires for the misclassified buySell (impact = buySell balance). Together they cover 2× the gap.

**Engine behavior:** `_best_scenarios` MDL scoring picks 1 flag at ~100% coverage over 2 flags at ~200% coverage.

**Enhancement proposal:** Add a mutual exclusion rule: if `ORPHAN_TRANSACTION` balance ≈ `MISSING_PROVISION` expected amount for the same security, suppress `ORPHAN_TRANSACTION` and keep `MISSING_PROVISION`, setting a `rootCauseHint = "wrong transaction type recorded instead of provision"`.

---

### C02: Missing buySell + wrong daily contribution

Two independent errors occur simultaneously: the buySell is not recorded AND the daily contribution was computed with a wrong formerPU.

- `MISSING_BUYSELL_TRANSACTION` impact = |amountDiff × PU|
- `WRONG_DAILY_CONTRIBUTION` impact = |stored_daily − expected_daily|

**Engine behavior:** `_best_scenarios` considers all combinations. If `MISSING_BUYSELL` impact + `WRONG_DAILY` impact ≈ gap_cash, the two-flag scenario is returned with high coverage. This is the correct multi-error handling.

---

### C03: Duplicate transaction + wrong provision amount

CRI_SECURIT has two buySell transactions (duplication) AND the provision amount is halved. Two independent errors:

- `DUPLICATE_TRANSACTION` impact = duplicate buySell excess balance
- `WRONG_PROVISION_AMOUNT` impact = |correct_prov − stored_prov|

**Engine behavior:** Both flags appear as Tier 1 in flat_impacts. `_best_scenarios` finds the two-flag combination if their sum matches gap_cash. The combined scenario correctly identifies both data quality issues.

---

## 6. New Flags Required (Implementation Summary)

| Flag | Tier | Trigger Condition | Impact Formula |
|---|---|---|---|
| `MISSING_EXECUTION_PRICE` | 2 | amountDiff ≠ 0 AND executionPrice is null | None (unquantifiable) |
| `WRONG_DAILY_CONTRIBUTION` | 1 | f_qty, f_pu, pu all known | \|stored_daily − f_qty × (pu − f_pu)\| |
| `WRONG_INTRADAY_CONTRIBUTION` | 1 | amountDiff ≠ 0 AND exec_price known | \|stored_intra − amountDiff × (pu − exec_price)\| |
| `TOTAL_CONTRIBUTION_ARITHMETIC_ERROR` | 1 | total_c stored | \|total_c − (daily + intraday + event)\| |
| `DUPLICATE_TRANSACTION` | 1 | count(buySell txns for security) > 1 | \|Σbuysell_balances − correct_single_balance\| |
| `ORPHAN_PROVISION` | 1 | Active provision for security NOT in current position | Σ\|provision_amounts\| |
| `WALLET_CASH_TRANSACTION` | 1 | Wallet-level txn with non-inflow type + gap still exists | Σ\|balance\| of non-inflow wallet txns |

---

## 7. Automated Scenario Capture — **FEATURE ATIVA**

> Status: **em uso ativo.** Última captura: T17 em 2026-04-24. Pasta
> `tests/scenarios/T9..T17` contém 9 cenários capturados.

### Overview

Ao invés de copiar payloads manualmente para análise, o botão
**Capturar Cenário** (Step-2 do modal de Conciliação) automatiza um
pipeline de 3 fases: **captura → análise AI → implementação AI**.

### Como usar

1. Conciliação → selecione uma wallet com gap
2. Step-2: clique **Capturar Cenário** (botão verde, ao lado de Diagnosticar)
3. Adicione uma nota descrevendo o que observou (opcional)
4. Clique **Capturar**

O backend:
- Snapshota todos os documentos Mongo relevantes (10 arquivos — ver tabela)
- Roda o diagnóstico completo + otimização bayesiana
- Computa `coverage` (quanto do gap é explicado pelos flags existentes)
- Salva tudo em `tests/scenarios/T<N>/` (numeração automática)
- **Dispara em background** um slash-command AI (`/analyze-scenario`) que
  gera `analysis.md` na mesma pasta — o frontend faz polling em
  `/api/conciliacao/scenario-analysis?scenarioId=T<N>` enquanto
  `.analysis_status` vai de `pending` → `done`

Depois que `analysis.md` está pronto, a UI mostra a análise AI e um botão
**Implementar Sugestão** → dispara `/implement-scenario` → gera
`implementation.md` com o diff sugerido.

### Arquivos gravados por cenário

| Arquivo | Conteúdo | Gerado por |
|---------|----------|-----------|
| `wallet.json` | Metadata da carteira | Captura |
| `nav_packages.json` | navPackage anterior + atual | Captura |
| `positions.json` | processedPosition anterior + atual | Captura |
| `transactions.json` | Transações da data | Captura |
| `provisions.json` | Provisões ativas | Captura |
| `cash_accounts.json` | Contas de caixa (antes + atual) | Captura |
| `securities.json` | Metadata dos securities (settlementDays, type…) | Captura |
| `diagnose_output.json` | Resposta completa de `/diagnose` | Captura |
| `bayesian_output.json` | Resposta completa de `/bayesian` | Captura |
| `coverage.json` | `{coveragePct, explainedImpact, residualGap, flags}` | Captura |
| `expected_corrections.json` | *(opcional)* gabarito de correções esperadas, editado manualmente para regression | Usuário |
| `metadata.json` | walletId, date, capturedAt, nota, resumo de coverage | Captura |
| `analysis.md` | Análise estruturada gerada por slash-command AI | `/analyze-scenario` |
| `.analysis_status` | `pending` \| `done` \| `error` — sentinela de status | Background process |
| `implementation.md` | Diff sugerido para alterações de código | `/implement-scenario` |
| `.implementation_status` | sentinela de status da implementação | Background process |
| `user_choice.txt` | *(opcional)* escolha do usuário quando `analysis.md` tem múltiplas opções (A/B) | UI |

### API Endpoints

| Rota | Método | Função |
|------|--------|--------|
| `/api/conciliacao/capture-scenario` | POST | Captura e salva um cenário (dispara `/analyze-scenario` em background) |
| `/api/conciliacao/scenarios` | GET | Lista todos os cenários capturados + metadata + status |
| `/api/conciliacao/scenario-analysis?scenarioId=T<N>` | GET | Stream do `analysis.md` em andamento (polling até `.analysis_status == done`) |
| `/api/conciliacao/implement-suggestion` | POST | Precondição: `analysis.md` existe. Body: `{scenarioId, userChoice?}`. Dispara `/implement-scenario` em background. |
| `/api/conciliacao/scenario-implementation?scenarioId=T<N>` | GET | Stream do `implementation.md` em andamento |

### Coverage analysis

O arquivo `coverage.json` responde a pergunta chave: **o motor de diagnóstico
explica esse gap?**

```json
{
  "coveragePct": 62.0,
  "explainedImpact": 62000.0,
  "residualGap": 38000.0,
  "flags": [
    {"flag": "MISSING_PROVISION", "securityId": "s9", "impact": 60000},
    {"flag": "WITHHOLDING_TAX", "securityId": "s10", "impact": 2000}
  ]
}
```

- `coveragePct < 100%` → motor tem lacuna; provável que precise de novo flag
- `coveragePct == 100%` → flags existentes cobrem o gap; serve para regression

### Relação com tests/test_bayesian_scenarios.py

Os dois sistemas coexistem mas **não se cruzam**:

- `tests/scenarios/T<N>/` — capturas **reais** de produção usadas para
  análise manual + pipeline AI
- `tests/test_bayesian_scenarios.py` — cenários **sintéticos hardcoded**
  para testar o engine bayesiano. Mesmo nome (T9, T10…) mas dados
  independentes

Não há import/leitura cruzada. Renomear ou remover uma das duas
hierarquias não afeta a outra.

---

*End of report — generated by SWAT diagnostic team, 2026-03-26*
