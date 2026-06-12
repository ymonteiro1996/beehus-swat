r"""Bulk-PATCH /partner/financial/security-classification/{securityId}.

Reads a JSON list (the export from the classification UI) where each item is:
    {"companyId": "...", "securityId": "...",
     "companyVariables": [...], "hierarchicalVariable": "<hvId>"}

For each item, sends:
    PATCH /partner/financial/security-classification/{securityId}
    body: {"companyVariables": [...], "hierarchicalVariable": "<hvId>"}

The company is taken from the bearer token (partner JWT), so companyId in the
file is informational only — every item must match the token's company.

Dry-run prints a readable current→target preview (resolving hierarchicalVariable
ids to "variable1 / variable2" via the company's `companyVariables` doc and
the current state in `securityClassifications`). Nothing is sent without --send.

Usage (PowerShell):
    $env:BEEHUS_PARTNER_TOKEN = "<paste partner token>"
    python scripts/bulk_classify_securities.py "C:\path\classification_next.json"          # dry-run
    python scripts/bulk_classify_securities.py "C:\path\classification_next.json" --send    # actually PATCH
    python scripts/bulk_classify_securities.py "..." --send --only-changed                  # skip no-ops
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partner_api.client import request, set_token  # noqa: E402
from partner_api.exceptions import PartnerAPIError  # noqa: E402


def load_items(path: Path) -> list[dict]:
    """Read the classification JSON, tolerating UTF-16/UTF-8(-BOM) encodings."""
    raw = path.read_bytes()
    for enc in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            data = json.loads(raw.decode(enc))
            break
        except (UnicodeError, json.JSONDecodeError):
            continue
    else:
        raise ValueError(f"Could not decode/parse {path}")
    if not isinstance(data, list):
        raise ValueError("Expected a JSON list at the top level")
    return data


def _company_maps(company_id: str):
    """Return (hvId -> 'v1 / v2', securityId -> current hvId, securityId -> classifId).

    All read from Mongo (read-only). The classification `_id` map is REQUIRED
    for --send: the PATCH route is keyed by the securityClassification `_id`,
    NOT by securityId. The label/current maps just make the preview readable.
    If the DB is unreachable everything falls back to {} (and --send will then
    have nothing to address, failing loudly per item)."""
    try:
        from db import db
    except Exception:
        return {}, {}, {}

    def label(hv: dict) -> str:
        parts = [hv.get(f"variable{i}") for i in range(1, 6)]
        parts = [p for p in parts if p]
        return " / ".join(parts) if parts else "(vazio)"

    hv_labels: dict[str, str] = {}
    try:
        cv = db.companyVariables.find_one({"companyId": company_id},
                                          {"hierarchicalVariables": 1})
        for hv in (cv or {}).get("hierarchicalVariables", []) or []:
            hid = str(hv.get("_id"))
            hv_labels[hid] = label(hv)
    except Exception:
        pass

    current: dict[str, str] = {}
    classif_id: dict[str, str] = {}
    try:
        for d in db.securityClassifications.find(
            {"companyId": company_id},
            {"securityId": 1, "hierarchicalVariable": 1},
        ):
            sid = str(d.get("securityId"))
            current[sid] = str(d.get("hierarchicalVariable") or "")
            classif_id[sid] = str(d["_id"])
    except Exception:
        pass

    return hv_labels, current, classif_id


def _security_names(security_ids):
    try:
        from db import db
        from bson import ObjectId
    except Exception:
        return {}
    oids = []
    for s in security_ids:
        try:
            oids.append(ObjectId(s))
        except Exception:
            pass
    if not oids:
        return {}
    return {str(d["_id"]): (d.get("beehusName") or "")
            for d in db.securities.find({"_id": {"$in": oids}}, {"beehusName": 1})}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("json", help="Path to classification_next.json")
    p.add_argument("--send", action="store_true",
                   help="Actually PATCH (otherwise dry-run)")
    p.add_argument("--only-changed", action="store_true",
                   help="Skip items whose current classification already equals the target")
    p.add_argument("--token",
                   help="Partner bearer token (else env BEEHUS_PARTNER_TOKEN)")
    p.add_argument("--throttle-seconds", type=float, default=0.2,
                   help="Sleep between PATCHes (default 0.2s)")
    args = p.parse_args()

    path = Path(args.json)
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 2

    items = load_items(path)
    if not items:
        print("No items to process.")
        return 0

    # Validate shape
    bad = [i for i, it in enumerate(items)
           if not it.get("securityId") or "hierarchicalVariable" not in it]
    if bad:
        print(f"ERROR: {len(bad)} item(s) missing securityId/hierarchicalVariable "
              f"(first at index {bad[0]}).", file=sys.stderr)
        return 2

    company_ids = {str(it.get("companyId") or "") for it in items}
    print(f"Loaded {len(items)} item(s) from {path.name}")
    print(f"companyId(s) in file: {', '.join(sorted(c for c in company_ids if c)) or '(none)'}\n")

    # Build readable preview maps (read-only Mongo; best-effort)
    company_id = next(iter(c for c in company_ids if c), "")
    hv_labels, current, classif_id = _company_maps(company_id)
    names = _security_names({str(it["securityId"]) for it in items})

    def lbl(hid: str) -> str:
        return hv_labels.get(hid, hid or "(vazio)")

    rows = []
    n_changed = n_same = n_new = 0
    for it in items:
        sid = str(it["securityId"])
        tgt = str(it["hierarchicalVariable"] or "")
        cur = current.get(sid)
        if cur is None:
            state = "NOVO"; n_new += 1
        elif cur == tgt:
            state = "igual"; n_same += 1
        else:
            state = "MUDA"; n_changed += 1
        rows.append({
            "securityId": sid,
            "classifId": classif_id.get(sid),  # _id usado na URL do PATCH
            "name": names.get(sid, ""),
            "cur": cur,
            "tgt": tgt,
            "state": state,
            "item": it,
        })

    # Preview table
    print(f"{'#':>3}  {'estado':<6} {'beehusName':<48} {'atual':<26} -> alvo")
    print("-" * 130)
    for i, r in enumerate(rows, 1):
        cur_lbl = lbl(r["cur"]) if r["cur"] is not None else "(sem classificação)"
        print(f"{i:>3}  {r['state']:<6} {(r['name'] or r['securityId'])[:48]:<48} "
              f"{cur_lbl[:26]:<26} -> {lbl(r['tgt'])}")
    print("-" * 130)
    print(f"Resumo: {n_changed} mudam · {n_same} já iguais · {n_new} sem classificação atual\n")

    work = [r for r in rows if (not args.only_changed) or r["state"] != "igual"]
    if args.only_changed:
        print(f"--only-changed: {len(work)} de {len(rows)} serão enviados "
              f"({n_same} 'igual' ignorados).\n")

    no_classif = [r for r in work if not r["classifId"]]
    if no_classif:
        print(f"AVISO: {len(no_classif)} item(ns) sem securityClassification._id "
              f"(serão pulados no envio — a rota é keyed por esse _id):")
        for r in no_classif:
            print(f"   - {r['securityId']}  {r['name']}")
        print()

    if not args.send:
        print("Dry-run. Re-execute com --send para aplicar via PATCH.")
        return 0

    token = (args.token or os.environ.get("BEEHUS_PARTNER_TOKEN", "")).strip()
    if not token:
        print("ERROR: token ausente (use --token ou BEEHUS_PARTNER_TOKEN).", file=sys.stderr)
        return 2
    set_token(token)

    send_list = [r for r in work if r["classifId"]]
    ok = 0
    failed: list[tuple[int, str, str]] = []
    for i, r in enumerate(send_list, 1):
        if args.throttle_seconds > 0 and i > 1:
            time.sleep(args.throttle_seconds)
        sid = r["securityId"]
        cid = r["classifId"]
        payload = {
            "companyVariables": r["item"].get("companyVariables", []),
            "hierarchicalVariable": r["tgt"],
        }
        try:
            request("PATCH", f"/partner/financial/security-classification/{cid}",
                    json=payload)
            ok += 1
            print(f"[{i:>3}/{len(send_list)}] OK   {r['name'][:40]:<40}  -> {lbl(r['tgt'])}",
                  flush=True)
        except PartnerAPIError as e:
            msg = (f"{e} (status={getattr(e, 'status', None)}) "
                   f"body={(getattr(e, 'body', '') or '')[:200]}")
            failed.append((i, sid, msg))
            print(f"[{i:>3}/{len(send_list)}] FAIL {sid}  {msg}", flush=True)

    print(f"\nConcluído. {ok} ok, {len(failed)} falharam.")
    if failed:
        print("\nFalhas:")
        for i, sid, msg in failed:
            print(f"  [{i}] {sid} -> {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
