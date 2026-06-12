"""
Generate report JSON from real MongoDB data for 4 wallets.
Each top-level key = one MongoDB collection used to populate the report.
"""

import json
import os
import sys
import statistics

# Script sits in reports/; make the project root importable so `from db import db`
# resolves regardless of the caller's CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from db import db
from bson import ObjectId

WALLET_IDS = [
    "68cabc8f62a3c4412e8d2121",
    "68cabd0162a3c4412e8d2145",
    "6931fe0c3cb026c5483de086",
    "68cb072562a3c4412e8d5741",
]
REFERENCE_MONTH = "2025-12"
POSITION_DATE = "2025-12-31"
FX_RATE = 6.19


# ── NAV-series helpers ─────────────────────────────────────────────────────────
# Normalize positionDate (mixed BSON Date / ISO string) and resample a
# possibly-daily navPackages series to one entry per month, so monthly metrics
# compute against true monthly anchors instead of the last daily row.

def _to_ym(d):
    if d is None:
        return ""
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m")
    return str(d)[:7]


def _month_end_navs(nav_list):
    by_month = {}
    for n in nav_list:
        k = _to_ym(n.get("positionDate"))
        if k:
            by_month[k] = n
    return [by_month[k] for k in sorted(by_month.keys())]


def _prev_month_end(month_navs, ref_month):
    for n in reversed(month_navs):
        if _to_ym(n.get("positionDate")) < ref_month:
            return n
    return None


def _prev_year_end(month_navs, ref_year):
    target = f"{int(ref_year) - 1}-12"
    for n in reversed(month_navs):
        ym = _to_ym(n.get("positionDate"))
        if ym and ym <= target:
            return n
    return None


def _ret(cur, base):
    if cur is None or not base:
        return None
    return (cur / base) - 1


def _monthly_returns(month_navs):
    out = []
    for i in range(1, len(month_navs)):
        r = _ret(month_navs[i].get("navPerShare"), month_navs[i - 1].get("navPerShare"))
        if r is not None:
            out.append(r)
    return out


def load_entity_name(entity_id):
    try:
        ent = db.entities.find_one({"_id": ObjectId(entity_id)})
        return ent.get("name", str(entity_id)) if ent else str(entity_id)
    except Exception:
        return str(entity_id)


def classify_liquidity(sec):
    name = sec.get("beehusName", "").upper()
    var1 = sec.get("hierarchicalVariable", {}).get("variable1", "")
    if "SELIC" in name or "CASH" in name or "CDI" in name or "TESOURO" in name:
        return "Diaria"
    if "T BILL" in name:
        return "Ate 6 meses"
    if any(k in name for k in ["FII", "ETF", "PETR", "ITUB", "SPDR", "RUSSELL"]):
        return "Ate 1 Mes"
    if any(k in name for k in ["FICFI", "FIC", "LORD", "JPM"]):
        return "Ate 1 Mes"
    if "LCA" in name or "CDB" in name:
        return "Ate 6 meses"
    if any(k in name for k in ["CRI", "CRA", "DEB", "FIDC", "FIAGRO"]):
        return "Ate 2 anos"
    if "PRIVATE" in var1.upper() or "LEXINGTON" in name or "INSIGHT" in name:
        return "Acima de 2 anos"
    if "HEDGE" in var1.upper():
        return "Ate 1 ano"
    return "Ate 1 ano"


def infer_region(currency, sec):
    if currency == "BRL":
        return "Brasil"
    name = sec.get("beehusName", "").upper()
    var1 = sec.get("hierarchicalVariable", {}).get("variable1", "")
    if any(k in name for k in ["US", "SPDR", "RUSSELL", "JPM", "LORD"]):
        return "EUA"
    if "HEDGE" in var1.upper() or "PRIVATE" in var1.upper():
        return "Global"
    return "EUA"


def main():
    print("Loading data from MongoDB...")

    report = {
        "_metadata": {
            "referenceMonth": REFERENCE_MONTH,
            "positionDate": POSITION_DATE,
            "fxRate_BRL_USD": FX_RATE,
            "generatedAt": "2025-12-31T23:59:59Z",
        },
        "report_config": [],
        "performance_summary": [],
        "nav_timeseries": [],
        "asset_detail": [],
        "allocation": [],
        "alloc_consolidated": [],
        "alloc_by_institution": [],
        "geographic_exposure": [],
        "transactions": [],
        "risk_statistics": [],
        "liquidity": [],
    }

    # ── Load all data ──────────────────────────────────────────────────────

    wallets = {}
    positions = {}
    navs = {}
    txns = {}

    for wid in WALLET_IDS:
        w = db.wallets.find_one({"_id": ObjectId(wid)})
        if w:
            wallets[wid] = w

        pos = db.processedPosition.find_one(
            {"walletId": wid, "positionDate": POSITION_DATE}, {"_id": 0}
        )
        if pos:
            positions[wid] = pos

        nav_list = list(db.navPackages.find(
            {"walletId": wid, "trashed": {"$ne": True}},
            {"_id": 0, "positionDate": 1, "nav": 1, "navPerShare": 1, "returnNavPerShare": 1}
        ).sort("positionDate", 1))
        navs[wid] = nav_list

        txn_list = list(db.transactions.find(
            {"walletId": wid},
            {"_id": 0, "operationDate": 1, "liquidationDate": 1, "balance": 1,
             "description": 1, "beehusTransactionType": 1}
        ).sort("liquidationDate", -1))
        txns[wid] = txn_list

    print(f"Loaded {len(wallets)} wallets")

    # ── report_config ──────────────────────────────────────────────────────

    for wid, w in wallets.items():
        report["report_config"].append({
            "walletId": wid,
            "walletName": w.get("name", ""),
            "companyId": w.get("companyId", ""),
            "companyName": "One Wealth",
            "entityId": str(w.get("entityId", "")),
            "entityName": load_entity_name(w.get("entityId", "")),
            "currency": w.get("currency", ""),
            "referenceMonth": REFERENCE_MONTH,
            "reportDate": POSITION_DATE,
            "reportType": "mensal",
            "startDateConsolidation": w.get("startDateConsolidation", ""),
            "startDateReturn": w.get("startDateReturn", ""),
        })

    # ── performance_summary ────────────────────────────────────────────────

    ref_year = REFERENCE_MONTH[:4]
    for wid, w in wallets.items():
        month_navs = _month_end_navs(navs.get(wid, []))
        if not month_navs:
            continue

        latest     = month_navs[-1]
        first      = month_navs[0]
        prev_month = _prev_month_end(month_navs, REFERENCE_MONTH)
        year_end   = _prev_year_end(month_navs, ref_year)

        nps_latest = latest.get("navPerShare")
        nps_prev   = prev_month.get("navPerShare") if prev_month else None
        nps_year   = year_end.get("navPerShare")   if year_end   else None
        nps_first  = first.get("navPerShare")

        report["performance_summary"].append({
            "walletId": wid,
            "walletName": w.get("name", ""),
            "currency": w.get("currency", ""),
            "referenceMonth": REFERENCE_MONTH,
            "patrimonio_inicial":    prev_month["nav"] if prev_month else None,
            "patrimonio_final":      latest["nav"],
            "rentabilidade_mes":     _ret(nps_latest, nps_prev),
            "rentabilidade_ano":     _ret(nps_latest, nps_year),
            "rentabilidade_inicio":  _ret(nps_latest, nps_first),
        })

    # ── nav_timeseries ─────────────────────────────────────────────────────

    for wid, w in wallets.items():
        for n in navs.get(wid, []):
            report["nav_timeseries"].append({
                "walletId": wid,
                "walletName": w.get("name", ""),
                "currency": w.get("currency", ""),
                "positionDate": n["positionDate"],
                "nav": n["nav"],
                "navPerShare": n.get("navPerShare", 0),
                "returnNavPerShare": n.get("returnNavPerShare", 0),
            })

    # ── asset_detail ───────────────────────────────────────────────────────

    for wid, w in wallets.items():
        pos = positions.get(wid)
        if not pos:
            continue
        for s in pos.get("securities", []):
            hv = s.get("hierarchicalVariable", {})
            balance = s.get("quantity", 0) * s.get("pu", 0)
            report["asset_detail"].append({
                "walletId": wid,
                "walletName": w.get("name", ""),
                "currency": w.get("currency", ""),
                "positionDate": pos.get("positionDate", ""),
                "category_var1": hv.get("variable1", ""),
                "category_var2": hv.get("variable2", ""),
                "beehusName": s.get("beehusName", ""),
                "quantity": s.get("quantity", 0),
                "pu": s.get("pu", 0),
                "saldo_bruto": round(balance, 2),
                "formerPu": s.get("formerPu", 0),
                "formerQuantity": s.get("formerQuantity", 0),
                "dailyContribution": s.get("dailyContribution", 0),
                "totalContribution": s.get("totalContribution", 0),
            })

    # ── allocation (per wallet) ────────────────────────────────────────────

    for wid, w in wallets.items():
        pos = positions.get(wid)
        if not pos:
            continue
        alloc = {}
        total = 0
        for s in pos.get("securities", []):
            hv = s.get("hierarchicalVariable", {})
            var1 = hv.get("variable1", "Outros")
            var2 = hv.get("variable2", "") or ""
            balance = s.get("quantity", 0) * s.get("pu", 0)
            key = f"{var1}/{var2}" if var2 else var1
            alloc[key] = alloc.get(key, 0) + balance
            total += balance

        for cls, bal in sorted(alloc.items(), key=lambda x: -x[1]):
            parts = cls.split("/", 1)
            report["allocation"].append({
                "walletId": wid,
                "walletName": w.get("name", ""),
                "currency": w.get("currency", ""),
                "referenceMonth": REFERENCE_MONTH,
                "asset_class_var1": parts[0],
                "asset_class_var2": parts[1] if len(parts) > 1 else "",
                "total_balance": round(bal, 2),
                "pct_allocation": round(bal / total, 4) if total > 0 else 0,
            })

    # ── alloc_consolidated ─────────────────────────────────────────────────

    alloc_brl = {}
    alloc_usd = {}
    for wid, w in wallets.items():
        pos = positions.get(wid)
        if not pos:
            continue
        currency = w.get("currency", "BRL")
        for s in pos.get("securities", []):
            var1 = s.get("hierarchicalVariable", {}).get("variable1", "Outros")
            balance = s.get("quantity", 0) * s.get("pu", 0)
            target = alloc_brl if currency == "BRL" else alloc_usd
            target[var1] = target.get(var1, 0) + balance

    all_classes = sorted(set(list(alloc_brl.keys()) + list(alloc_usd.keys())))
    grand_total = sum(alloc_brl.values()) + sum(v * FX_RATE for v in alloc_usd.values())

    for cls in all_classes:
        brl_val = alloc_brl.get(cls, 0)
        usd_val = alloc_usd.get(cls, 0)
        total_brl = brl_val + (usd_val * FX_RATE)
        report["alloc_consolidated"].append({
            "referenceMonth": REFERENCE_MONTH,
            "asset_class_var1": cls,
            "total_BRL": round(brl_val, 2),
            "total_USD": round(usd_val, 2),
            "total_all_BRL": round(total_brl, 2),
            "pct_total": round(total_brl / grand_total, 4) if grand_total > 0 else 0,
        })

    # ── alloc_by_institution ───────────────────────────────────────────────

    total_inst_brl = 0
    for wid, w in wallets.items():
        nav_list = navs.get(wid, [])
        latest_nav = nav_list[-1]["nav"] if nav_list else 0
        currency = w.get("currency", "BRL")
        brl_val = latest_nav if currency == "BRL" else latest_nav * FX_RATE

        report["alloc_by_institution"].append({
            "referenceMonth": REFERENCE_MONTH,
            "institution": load_entity_name(w.get("entityId", "")),
            "walletId": wid,
            "walletName": w.get("name", ""),
            "currency": currency,
            "saldo_bruto": round(latest_nav, 2),
            "saldo_bruto_BRL": round(brl_val, 2),
        })
        total_inst_brl += brl_val

    report["alloc_by_institution"].append({
        "referenceMonth": REFERENCE_MONTH,
        "institution": "TOTAL",
        "walletId": None,
        "walletName": None,
        "currency": "BRL",
        "saldo_bruto": None,
        "saldo_bruto_BRL": round(total_inst_brl, 2),
    })

    # ── geographic_exposure ────────────────────────────────────────────────

    region_totals = {}
    geo_grand = 0
    for wid, w in wallets.items():
        pos = positions.get(wid)
        if not pos:
            continue
        currency = w.get("currency", "BRL")
        for s in pos.get("securities", []):
            balance = s.get("quantity", 0) * s.get("pu", 0)
            brl_val = balance if currency == "BRL" else balance * FX_RATE
            region = infer_region(currency, s)
            region_totals[region] = region_totals.get(region, 0) + brl_val
            geo_grand += brl_val

    for region, total in sorted(region_totals.items(), key=lambda x: -x[1]):
        report["geographic_exposure"].append({
            "referenceMonth": REFERENCE_MONTH,
            "region": region,
            "total_BRL": round(total, 2),
            "pct": round(total / geo_grand, 4) if geo_grand > 0 else 0,
        })

    # ── transactions (reference month only) ────────────────────────────────

    for wid, w in wallets.items():
        for t in txns.get(wid, []):
            liq_date = t.get("liquidationDate", "")
            if not liq_date.startswith("2025-12"):
                continue
            balance = t.get("balance", 0)
            report["transactions"].append({
                "walletId": wid,
                "walletName": w.get("name", ""),
                "currency": w.get("currency", ""),
                "direction": "entrada" if balance >= 0 else "saida",
                "liquidationDate": liq_date,
                "transactionType": t.get("beehusTransactionType", ""),
                "description": t.get("description", ""),
                "balance": balance,
            })

    # ── risk_statistics ────────────────────────────────────────────────────

    for wid, w in wallets.items():
        month_navs = _month_end_navs(navs.get(wid, []))
        returns = _monthly_returns(month_navs)
        if not returns:
            continue
        positive = sum(1 for r in returns if r > 0)
        negative = sum(1 for r in returns if r < 0)
        vol = statistics.stdev(returns) * (12 ** 0.5) if len(returns) > 1 else 0

        report["risk_statistics"].append({
            "walletId": wid,
            "walletName": w.get("name", ""),
            "currency": w.get("currency", ""),
            "referenceMonth": REFERENCE_MONTH,
            "meses_positivos": positive,
            "meses_negativos": negative,
            "rentabilidade_mensal_maxima": max(returns),
            "rentabilidade_mensal_minima": min(returns),
            "volatilidade_anualizada": round(vol, 6),
        })

    # ── liquidity ──────────────────────────────────────────────────────────

    for wid, w in wallets.items():
        pos = positions.get(wid)
        if not pos:
            continue
        buckets = {}
        total = 0
        for s in pos.get("securities", []):
            balance = s.get("quantity", 0) * s.get("pu", 0)
            bucket = classify_liquidity(s)
            buckets[bucket] = buckets.get(bucket, 0) + balance
            total += balance

        bucket_order = ["Diaria", "Ate 1 Mes", "Ate 6 meses",
                        "Ate 1 ano", "Ate 2 anos", "Acima de 2 anos"]
        for bucket in bucket_order:
            bal = buckets.get(bucket, 0)
            if bal == 0:
                continue
            report["liquidity"].append({
                "walletId": wid,
                "walletName": w.get("name", ""),
                "currency": w.get("currency", ""),
                "referenceMonth": REFERENCE_MONTH,
                "time_bucket": bucket,
                "total_balance": round(bal, 2),
                "pct_portfolio": round(bal / total, 4) if total > 0 else 0,
            })

    # ── Write JSON ─────────────────────────────────────────────────────────

    output_path = os.path.join(_PROJECT_ROOT, "data", "relatorio_patrimonial_from_mongo.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, output_path)

    print(f"\nJSON saved to: {output_path}")
    print(f"Collections: {[k for k in report if k != '_metadata']}")
    for k, v in report.items():
        if isinstance(v, list):
            print(f"  {k}: {len(v)} documents")


if __name__ == "__main__":
    main()
