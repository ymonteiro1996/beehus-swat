"""Probe securities_cache para entender match keys de bonds/fundos."""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

cache_path = Path(__file__).resolve().parent.parent / "data" / "securities_cache.json"
d = json.load(open(cache_path, encoding="utf-8"))
secs = d.get("securities", [])
bonds = [s for s in secs if s.get("securityType") == "bond"]
print(f"total bonds: {len(bonds)}")

print("--- search for PETR27 (Cetip code):")
for s in bonds:
    blob = " ".join(
        str(s.get(k, "")) for k in ("mainId", "ticker", "isIn", "beehusName")
    ).upper()
    if "PETR27" in blob:
        print(f"  mainId={s.get('mainId')!r}  ticker={s.get('ticker')!r}  isIn={s.get('isIn')!r}  name={s.get('beehusName')!r}")

print("--- search for CDB222OO4MD:")
for s in bonds:
    blob = " ".join(
        str(s.get(k, "")) for k in ("mainId", "ticker", "isIn", "beehusName")
    ).upper()
    if "CDB222OO4MD" in blob:
        print(f"  mainId={s.get('mainId')!r}  ticker={s.get('ticker')!r}  isIn={s.get('isIn')!r}  name={s.get('beehusName')!r}")

print("--- 10 bonds com isIn:")
i = 0
for s in bonds:
    if s.get("isIn"):
        print(f"  mainId={s.get('mainId')!r:30s}  isIn={s.get('isIn')!r:18s}  name={s.get('beehusName')!r}")
        i += 1
        if i >= 10:
            break

print("--- 10 bonds com taxId:")
i = 0
for s in bonds:
    if s.get("taxId"):
        print(f"  taxId={s.get('taxId')!r:20s}  mainId={s.get('mainId')!r:30s}  name={s.get('beehusName')!r}")
        i += 1
        if i >= 10:
            break

# Fundos brasileiros: como o mainId/taxId encaixa?
print("\n--- 10 brazilianFund:")
i = 0
for s in secs:
    if s.get("securityType") == "brazilianFund":
        print(f"  taxId={s.get('taxId')!r:20s}  mainId={s.get('mainId')!r:30s}  name={s.get('beehusName')!r}")
        i += 1
        if i >= 10:
            break

# COE: procurar "COE" em beehusName
print("\n--- 10 COE-like:")
i = 0
for s in secs:
    n = (s.get("beehusName") or "").upper()
    if n.startswith("COE ") or "COE-" in (s.get("ticker") or "").upper():
        print(f"  type={s.get('securityType')!r:18s}  mainId={s.get('mainId')!r:30s}  ticker={s.get('ticker')!r:20s}  name={s.get('beehusName')!r}")
        i += 1
        if i >= 10:
            break

# Cetip de COE específicos do BTG
print("\n--- procurar XP0122D4TG5 / BNPPARIBASBM / KPC0929:")
for code in ["XP0122D4TG5", "BNPPARIBASBM", "KPC0929", "BT5324EBV3P"]:
    for s in secs:
        blob = " ".join(str(s.get(k, "")) for k in ("mainId", "ticker", "isIn", "beehusName")).upper()
        if code in blob:
            print(f"  [{code}] type={s.get('securityType')!r}  mainId={s.get('mainId')!r}  ticker={s.get('ticker')!r}  name={s.get('beehusName')!r}")

# Pension: previdência aparece como brazilianFund?
print("\n--- procurar fundos VGBL/PGBL conhecidos:")
for cnpj in ["47564734000167", "41498482000139", "53847333000117"]:
    for s in secs:
        if s.get("taxId") == cnpj or s.get("mainId") == cnpj:
            print(f"  [{cnpj}] type={s.get('securityType')!r}  mainId={s.get('mainId')!r}  name={s.get('beehusName')!r}")

# Bitcoin/crypto?
print("\n--- crypto-like:")
for s in secs:
    n = (s.get("beehusName") or "").upper()
    if "BITCOIN" in n or "ETHEREUM" in n or "BTC" in (s.get("ticker") or "").upper():
        print(f"  type={s.get('securityType')!r}  mainId={s.get('mainId')!r}  ticker={s.get('ticker')!r}  name={s.get('beehusName')!r}")
