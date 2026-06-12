"""Ad-hoc: inspect how the IPCA benchmark series is stored in securityPrices.

Answers: is it stored DAILY (business-day cadence) or MONTHLY? Which fields
are present (rentability vs value)? How would precificacao's engine derive the
daily factor from it? Run from project root: python scripts/inspect_ipca_benchmark.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from collections import Counter
from bson import ObjectId
from db import db

IPCA_ID = "66fc4ab9e88f2f542b80563d"
CDI_ID  = "66fc4a71e88f2f542b805639"


def fetch_series(sec_id):
    """Mirror precificacao._find_all_prices + _extract_all_hp (ObjectId then str)."""
    docs = []
    try:
        docs = list(db.securityPrices.find(
            {"securityId": ObjectId(sec_id)}, {"historyPrice": 1}
        ).sort("historyPrice.date", 1))
    except Exception:
        pass
    if not docs:
        docs = list(db.securityPrices.find(
            {"securityId": sec_id}, {"historyPrice": 1}
        ).sort("historyPrice.date", 1))
    hps = []
    for d in docs:
        raw = d.get("historyPrice")
        if isinstance(raw, dict):
            hps.append(raw)
        elif isinstance(raw, list):
            hps.extend(raw)
    # de-dup by date[:10] keeping first (same as engine's `seen` logic)
    seen, series = set(), []
    for hp in sorted(hps, key=lambda x: str(x.get("date", ""))):
        dt = str(hp.get("date", ""))[:10]
        if dt and dt not in seen:
            seen.add(dt)
            series.append((dt, hp))
    return docs, series


def analyze(label, sec_id):
    print(f"\n{'='*70}\n{label}  (securityId={sec_id})\n{'='*70}")
    docs, series = fetch_series(sec_id)
    print(f"securityPrices docs encontrados : {len(docs)}")
    print(f"entradas historyPrice (únicas)  : {len(series)}")
    if not series:
        print("  >>> NENHUMA série encontrada.")
        return
    first_dt, last_dt = series[0][0], series[-1][0]
    print(f"intervalo de datas              : {first_dt}  ->  {last_dt}")

    # field presence
    keys = Counter()
    has_rent = 0
    for _, hp in series:
        for k in hp.keys():
            keys[k] += 1
        if hp.get("rentability") is not None:
            has_rent += 1
    print(f"campos presentes                : {dict(keys)}")
    print(f"entradas com 'rentability' != None: {has_rent} / {len(series)}")

    # cadence: gaps in CALENDAR days between consecutive entries
    gaps = Counter()
    for i in range(1, len(series)):
        try:
            d0 = date.fromisoformat(series[i-1][0])
            d1 = date.fromisoformat(series[i][0])
            gaps[(d1 - d0).days] += 1
        except ValueError:
            pass
    top = gaps.most_common(8)
    print(f"gaps (dias corridos entre entradas consecutivas), top: {top}")
    # heuristic verdict
    one_three = sum(c for g, c in gaps.items() if 1 <= g <= 4)
    monthly   = sum(c for g, c in gaps.items() if 27 <= g <= 32)
    print(f"  -> gaps de 1-4 dias  (cadência diária)  : {one_three}")
    print(f"  -> gaps de ~30 dias  (cadência mensal)  : {monthly}")

    # last 6 raw entries
    print("ultimas 6 entradas (raw):")
    for dt, hp in series[-6:]:
        slim = {k: hp.get(k) for k in ("date", "value", "rentability") if k in hp}
        print(f"    {dt}: {slim}")

    # How the engine would build the daily factor for the last few dates
    print("fator diário que o motor derivaria (rentability OU value(t)/value(t-1)-1):")
    for i in range(max(1, len(series)-6), len(series)):
        dt, hp = series[i]
        if hp.get("rentability") is not None:
            f = float(hp["rentability"]); src = "rentability"
        else:
            pv = series[i-1][1].get("value"); cv = hp.get("value")
            f = (float(cv)/float(pv) - 1) if (pv and cv) else None
            src = "value-ratio"
        print(f"    {dt}: fator={f}  (fonte={src})")


def monthly_factor_profile(label, sec_id):
    """For each calendar month, show the daily factor on its first entry and
    the annualized rate — reveals whether the daily factor is a flat global
    constant or steps month-to-month (pro-rata of each monthly IPCA print)."""
    print(f"\n{'-'*70}\n{label}: fator diário por mês (1ª entrada de cada mês)\n{'-'*70}")
    _, series = fetch_series(sec_id)
    by_month = {}
    for i in range(1, len(series)):
        dt = series[i][0]
        ym = dt[:7]
        if ym in by_month:
            continue
        pv = series[i-1][1].get("value"); cv = series[i][1].get("value")
        if pv and cv:
            f = float(cv)/float(pv) - 1
            ann = ((1 + f) ** 252 - 1) * 100
            by_month[ym] = (f, ann)
    # print last 26 months
    for ym in sorted(by_month)[-26:]:
        f, ann = by_month[ym]
        print(f"    {ym}: fator_dia={f:.10f}   ~{ann:6.2f}% a.a.")


if __name__ == "__main__":
    if not db._ready():
        print(">>> DB não conectado (sem user_connections.json para este usuário Windows).")
        sys.exit(1)
    analyze("IPCA", IPCA_ID)
    monthly_factor_profile("IPCA", IPCA_ID)
    monthly_factor_profile("CDI", CDI_ID)
