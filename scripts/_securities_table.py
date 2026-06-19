# -*- coding: utf-8 -*-
"""Extração de verificação: securities da company na processedPosition de uma
data, com a classificação ATUAL (live, de securityClassifications) ao lado da
classificação do SNAPSHOT (embutida na processedPosition daquela data).

A processedPosition é congelada na data; as alterações via
/partner/financial/security-classification só aparecem em securityClassifications.
Esta tabela cruza as duas para conferência.
"""
import sys, csv
sys.path.insert(0, ".")
from db import db, invalidate_cache
from bson import ObjectId

CID = "58454495000109"
DATE = "2026-05-29"

invalidate_cache()  # garante leitura fresca dos caches de 5min

# ── 1) universo: securities distintas na processedPosition (company, data) ───
cur = db.processedPosition.find(
    {"companyId": CID, "positionDate": DATE, "trashed": {"$ne": True}},
    {"walletId": 1, "securities.securityId": 1, "securities.beehusName": 1,
     "securities.hierarchicalVariable": 1, "securities.mainId": 1},
)
rows = {}
wallets_per_sid = {}
n_docs = 0
for doc in cur:
    n_docs += 1
    wid = str(doc.get("walletId"))
    for s in doc.get("securities", []) or []:
        sid = str(s.get("securityId") or "")
        if not sid:
            continue
        hv = s.get("hierarchicalVariable") or {}
        snap_v1 = hv.get("variable1") if isinstance(hv, dict) else None
        snap_v2 = hv.get("variable2") if isinstance(hv, dict) else None
        if sid not in rows:
            rows[sid] = {
                "securityId": sid,
                "beehusName": s.get("beehusName") or "",
                "snap_v1": snap_v1 or "",
                "snap_v2": snap_v2 or "",
                "mainId_pp": str(s.get("mainId") or ""),
            }
            wallets_per_sid[sid] = set()
        wallets_per_sid[sid].add(wid)

# ── 2) mainId oficial (collection securities) ────────────────────────────────
oids = []
for sid in rows:
    try:
        oids.append(ObjectId(sid))
    except Exception:
        pass
sec_mainid = {str(d["_id"]): str(d.get("mainId") or "")
              for d in db.securities.find({"_id": {"$in": oids}}, {"mainId": 1})}

# ── 3) classificação LIVE: securityId -> hvId (securityClassifications) ───────
live_hv = {}
for d in db.securityClassifications.find(
        {"companyId": CID}, {"securityId": 1, "hierarchicalVariable": 1}):
    live_hv[str(d.get("securityId"))] = str(d.get("hierarchicalVariable") or "")

# hvId -> (variable1, variable2) via companyVariables
hv_map = {}
cv = db.companyVariables.find_one({"companyId": CID}, {"hierarchicalVariables": 1})
for hv in (cv or {}).get("hierarchicalVariables", []) or []:
    hv_map[str(hv.get("_id"))] = (hv.get("variable1") or "", hv.get("variable2") or "")

# ── 4) montar linhas ─────────────────────────────────────────────────────────
for sid, r in rows.items():
    r["nWallets"] = len(wallets_per_sid[sid])
    r["mainId"] = sec_mainid.get(sid, "")
    hid = live_hv.get(sid)
    if hid is None:
        r["live_v1"], r["live_v2"], r["has_live"] = "", "", False
    else:
        v1, v2 = hv_map.get(hid, ("", ""))
        r["live_v1"], r["live_v2"], r["has_live"] = v1, v2, True
    r["changed"] = (r["has_live"]
                    and (r["live_v1"], r["live_v2"]) != (r["snap_v1"], r["snap_v2"]))

ordered = sorted(rows.values(),
                 key=lambda r: (r["live_v1"], r["live_v2"], r["beehusName"]))

changed = [r for r in ordered if r["changed"]]
no_live = [r for r in ordered if not r["has_live"]]

print(f"# processedPosition docs (não-trashed): {n_docs}")
print(f"# securities distintas: {len(ordered)}")
print(f"# com classificação live divergente do snapshot 2026-05-29: {len(changed)}")
print(f"# sem securityClassification (só snapshot): {len(no_live)}\n")

# markdown table (classificação LIVE/atual)
print("| # | securityId | beehusName | variable1 (atual) | variable2 (atual) | mainId | snapshot 29/05 | #w |")
print("|---|------------|------------|-------------------|-------------------|--------|----------------|----|")
for i, r in enumerate(ordered, 1):
    snap = f"{r['snap_v1']} / {r['snap_v2']}" if (r['snap_v1'] or r['snap_v2']) else "—"
    mark = " ✏️" if r["changed"] else ""
    live2 = r["live_v2"] if r["has_live"] else "(sem classif.)"
    print(f"| {i} | {r['securityId']} | {r['beehusName']} | {r['live_v1']} | {live2}{mark} | {r['mainId']} | {snap} | {r['nWallets']} |")

# CSV
out = "data/securities_company_58454495000109_2026-05-29_verificacao.csv"
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["securityId", "beehusName", "mainId",
                "variable1_atual", "variable2_atual",
                "variable1_snapshot", "variable2_snapshot",
                "mudou", "tem_classificacao_live", "nWallets"])
    for r in ordered:
        w.writerow([r["securityId"], r["beehusName"], r["mainId"],
                    r["live_v1"], r["live_v2"], r["snap_v1"], r["snap_v2"],
                    "sim" if r["changed"] else "", "sim" if r["has_live"] else "nao",
                    r["nWallets"]])
print(f"\nCSV salvo em: {out}")
