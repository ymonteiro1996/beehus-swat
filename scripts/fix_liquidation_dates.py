"""Ajusta liquidationDate da company 00000000000002:
- 2025-08-31 -> 2025-08-29
- Datas posteriores a 2025-08-31 -> ultimo dia util do respectivo mes
"""
import sys
import calendar
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, r"c:\Users\ymonteiro\Beehus Tecnologia Ltda\Beehus Tecnologia Ltda - Documentos\SWAT\Controle de cargas")
from db import db

TOKEN   = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2OTJlZTk2MWE2ZDdhMjVkZjE5ZDZhODIiLCJ1c2VyTmFtZSI6Ill1cmkgTW9udGVpcm8gLSBCZWVodXMiLCJ0eXBlIjoiYmVlaHVzIiwiY29tcGFueUlkIjoiNTIzODc0MzIwMDAxMDkiLCJjb21wYW55TmFtZSI6IkJlZWh1cyBUZWNub2xvZ2lhIEx0ZGEuIiwiYWRtaW4iOnRydWUsInNob3VsZEFwcHJvdmVNb250aGx5UmVwb3J0cyI6ZmFsc2UsImlhdCI6MTc4MDQxOTE5OSwiZXhwIjoxNzgwNTA1NTk5fQ.fqi9Z0fTbqsZm5_lUypuj0RD8LW1x51KfTtI4gnUTGw"
BASE    = "https://controladoria.beehus.com.br"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
WORKERS = 4

CUTOFF = "2025-08-31"  # inclusive — esta data recebe tratamento especial


def last_business_day(year, month):
    """Último dia útil do mês (sem fins de semana)."""
    last = calendar.monthrange(year, month)[1]
    d = datetime.date(year, month, last)
    while d.weekday() >= 5:  # 5=sábado, 6=domingo
        d -= datetime.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def target_date(ld):
    """Calcula a data alvo para uma liquidationDate."""
    if ld == CUTOFF:
        return "2025-08-29"
    if ld > CUTOFF:
        y, m, _ = map(int, ld.split("-"))
        return last_business_day(y, m)
    return None  # datas anteriores ao cutoff: não alterar


txns = list(db.transactions.find(
    {"companyId": "00000000000002"},
    {"_id": 1, "liquidationDate": 1},
))

work = []
for t in txns:
    ld = (t.get("liquidationDate") or "")[:10]
    if not ld or len(ld) < 10:
        continue
    new = target_date(ld)
    if new and new != ld:
        work.append({"id": str(t["_id"]), "old_date": ld, "new_date": new})

print(f"Total transações: {len(txns)}")
print(f"Precisam atualizar: {len(work)}")
if work:
    samples = {}
    for item in work:
        key = f"{item['old_date']} -> {item['new_date']}"
        samples[key] = samples.get(key, 0) + 1
    print("Conversões:")
    for k, v in sorted(samples.items()):
        print(f"  {k}  ({v} transações)")
print()


def patch_one(item):
    txn_id   = item["id"]
    new_date = item["new_date"]
    url = f"{BASE}/beehus/financial/transactions/{txn_id}"
    try:
        r = requests.patch(url, json={"liquidationDate": new_date}, headers=HEADERS, timeout=60)
    except Exception as exc:
        return False, txn_id, f"timeout/network: {exc}"
    if r.ok:
        return True, txn_id, None
    return False, txn_id, f"{r.status_code}: {r.text[:120]}"


ok_count = err_count = 0
errors = []

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futs = {pool.submit(patch_one, item): item for item in work}
    for i, fut in enumerate(as_completed(futs), 1):
        success, txn_id, err = fut.result()
        if success:
            ok_count += 1
        else:
            err_count += 1
            errors.append((txn_id, err))
        if i % 100 == 0 or i == len(work):
            print(f"  {i}/{len(work)} | ok={ok_count} err={err_count}")

print()
print(f"Concluído: {ok_count} atualizadas, {err_count} erros")
if errors:
    print("Erros:")
    for txn_id, e in errors[:10]:
        print(f"  {txn_id}: {e}")
