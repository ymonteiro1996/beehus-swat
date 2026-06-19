"""Compare the live engine result vs an INDEPENDENT reproduction for the
CRI Balaroti payload (CDI + 3%, posPU on 2026-05-04).

- Engine  = pages.precificacao._calculate_curva_impl([payload])  (identical to
  what /api/precificacao/calcular returns in the browser).
- Indep.  = my own CDI value-ratio roll + my own coupon/amort degrau, rebuilt
  from the DB and the documented spec (NOT reusing the engine's helpers).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from bson import ObjectId
from db import db
from pages.precificacao import _calculate_curva_impl

SEC_ID  = "67feefbf56efc7ce22754d9f"
WAL_ID  = "680a9ce53b2296d861271302"
BM_ID   = "66fc4a71e88f2f542b805639"   # CDI
YIELD   = 3.0
BASE_PU = 253.90572864000004
BASE_DT = "2026-05-04"

PAYLOAD = {"id": SEC_ID, "beehusName": "CRI Balaroti", "calcType": "inflacao_curva",
           "benchmarkId": BM_ID, "benchmarkName": "CDI", "indexerPercentual": 100,
           "transactions": [{"date": "2000-01-01", "quantity": 199, "yield": 3}],
           "walletId": WAL_ID, "walletName": "MAR PF BTG BRL", "pricingType": "C3",
           "initialPU": None, "initialPUDate": "", "posPU": BASE_PU,
           "positionDate": BASE_DT}


def bm_series(sec_id):
    docs = []
    try:
        docs = list(db.securityPrices.find({"securityId": ObjectId(sec_id)}, {"historyPrice": 1}))
    except Exception:
        pass
    if not docs:
        docs = list(db.securityPrices.find({"securityId": sec_id}, {"historyPrice": 1}))
    hps = []
    for d in docs:
        raw = d.get("historyPrice")
        if isinstance(raw, dict):  hps.append(raw)
        elif isinstance(raw, list): hps.extend(raw)
    seen, series = set(), []
    for hp in sorted(hps, key=lambda x: str(x.get("date", ""))):
        dt = str(hp.get("date", ""))[:10]
        if dt and dt not in seen and hp.get("value") is not None:
            seen.add(dt); series.append((dt, float(hp["value"])))
    return series


def indep_degrau(emitted_sorted):
    """Independent coupon/amort degrau, rebuilt from db.transactions per the
    documented spec. Returns {cal_date: impacto_por_unidade}."""
    sid_q = [{"securityId": SEC_ID}]
    try: sid_q.append({"securityId": ObjectId(SEC_ID)})
    except Exception: pass
    wid_q = [{"walletId": WAL_ID}]
    try: wid_q.append({"walletId": ObjectId(WAL_ID)})
    except Exception: pass
    cur = db.transactions.find(
        {"$or": sid_q, "$and": [{"$or": wid_q}],
         "beehusTransactionType": {"$in": ["coupon", "amortization", "taxes"]},
         "trashed": {"$ne": True}},
        {"liquidationDate": 1, "beehusTransactionType": 1, "balance": 1})
    by_date = {}
    raw = []
    for t in cur:
        dt = str(t.get("liquidationDate") or "")[:10]
        if not dt: continue
        try: bal = float(t.get("balance"))
        except (TypeError, ValueError): continue
        raw.append((dt, t.get("beehusTransactionType"), bal))
        slot = by_date.setdefault(dt, {"event": 0.0, "taxes": 0.0})
        if t.get("beehusTransactionType") == "taxes": slot["taxes"] += bal
        else: slot["event"] += bal
    if raw:
        print("  transações coupon/amort/taxes encontradas:")
        for dt, tp, bal in sorted(raw):
            print(f"    {dt}  {tp:<13} balance={bal:.6f}")
    else:
        print("  (nenhuma transação coupon/amort/taxes p/ este ativo+carteira)")
    # qty_before via processedPosition
    def qty_before(date_str):
        or_q = [{"walletId": WAL_ID}]
        try: or_q.append({"walletId": ObjectId(WAL_ID)})
        except Exception: pass
        doc = next(iter(db.processedPosition.find(
            {"$or": or_q, "positionDate": {"$lt": date_str}},
            {"securities": 1}).sort("positionDate", -1).limit(1)), None)
        if not doc: return None
        for ps in doc.get("securities", []):
            if str(ps.get("securityId", "")) == SEC_ID:
                q = ps.get("quantity")
                try: return float(q) if q is not None else None
                except (TypeError, ValueError): return None
        return None
    impacts = {}
    for dt, slot in by_date.items():
        if slot["event"] == 0.0: continue
        qp = qty_before(dt)
        if not qp: continue
        impacts[dt] = (slot["event"] + slot["taxes"]) / qp
    # snap to emitted calendar, drop <= base
    out = {}
    for liq in sorted(impacts):
        if liq <= BASE_DT: continue
        cal = next((d for d in emitted_sorted if d >= liq), None)
        if cal is None: continue
        out[cal] = out.get(cal, 0.0) + impacts[liq]
        print(f"    -> degrau {impacts[liq]:.8f}/un  (liq {liq} → calendário {cal})")
    return out


def main():
    if not db._ready():
        print(">>> DB não conectado."); sys.exit(1)

    # last historyPrice date of the security (= global_start no motor)
    sp = bm_series(SEC_ID)
    print(f"Último historyPrice do ativo: {sp[-1][0] if sp else '(nenhum)'}  "
          f"(define global_start do motor)\n")

    # ── Engine result ──
    eng = _calculate_curva_impl([PAYLOAD])
    eng_rows = [r for r in eng if r.get("pu") is not None]
    if not eng_rows:
        print("ENGINE retornou erro:", eng); sys.exit(1)
    eng_map = {r["date"]: r for r in eng_rows}

    # ── Independent reproduction ──
    series = bm_series(BM_ID)
    dates  = [d for d, _ in series]
    valmap = dict(series)
    if BASE_DT in valmap:
        base = BASE_DT
    else:
        base = next((d for d in dates if d >= BASE_DT), None)
    base_i = dates.index(base)
    daily_factor = (1 + YIELD / 100) ** (1 / 252)
    emitted = [d for d in dates if d > base]
    print("Degrau (reprodução independente):")
    degrau = indep_degrau(emitted)
    print()

    pu = BASE_PU
    indep = {}
    for i in range(base_i + 1, len(dates)):
        dt = dates[i]
        bmd = valmap[dt] / valmap[dates[i-1]] - 1
        pu *= daily_factor * (1 + bmd)
        if dt in degrau:
            pu -= degrau[dt]
        indep[dt] = pu

    # ── Compare ──
    common = sorted(set(eng_map) & set(indep))
    maxdiff = 0.0; worst = None
    for dt in common:
        d = abs(eng_map[dt]["pu"] - indep[dt])
        if d > maxdiff: maxdiff, worst = d, dt
    print(f"Datas: engine={len(eng_rows)}  indep={len(indep)}  comuns={len(common)}")
    print(f"base={base}  PU_base={BASE_PU}  yield={YIELD}%  daily_factor={daily_factor:.10f}")
    print(f"\nMaior |diferença| de PU: {maxdiff:.2e}  na data {worst}\n")

    print(f"{'Data':<12}{'PU engine':>16}{'PU indep':>16}{'diff':>11}{'fatorComb':>13}{'eventImpact':>14}")
    print("-" * 78)
    for dt in common:
        e = eng_map[dt]; ip = indep[dt]
        ev = e.get("eventImpact")
        evs = f"{ev:.6f}" if ev else ""
        print(f"{dt:<12}{e['pu']:>16.8f}{ip:>16.8f}{e['pu']-ip:>11.1e}"
              f"{e.get('benchmarkFactor', 0):>13.8f}{evs:>14}")

    print(f"\nFINAL engine PU = {eng_rows[-1]['pu']:.8f}  ({eng_rows[-1]['date']})")
    print(f"FINAL indep  PU = {indep[common[-1]]:.8f}")


if __name__ == "__main__":
    main()
