import re
import json

html_path = r"c:\Users\afilh\OneDrive\Desktop\Github beehus\beehus-swat\templates\controlpanel.html"
text = open(html_path, encoding="utf-8").read()

def extract_table(var_name):
    m = re.search(re.escape(var_name) + r"\s*=\s*\[(.*?)\n  \];", text, re.DOTALL)
    if not m:
        raise SystemExit(f"table {var_name} not found")
    body = m.group(1)
    entries = []
    for entry_m in re.finditer(r'\{\s*canonical:\s*"((?:[^"\\]|\\.)*)"\s*,\s*patterns:\s*\[(.*?)\]\s*\}', body, re.DOTALL):
        canonical = entry_m.group(1).replace('\\"', '"')
        patterns_raw = entry_m.group(2)
        patterns = re.findall(r'"((?:[^"\\]|\\.)*)"', patterns_raw)
        patterns = [p.replace('\\"', '"') for p in patterns]
        entries.append((canonical, patterns))
    return entries

bank = extract_table("_BANK_CANONICALS")
devedor = extract_table("_DEVEDOR_CANONICALS")

print(f"bank entries: {len(bank)}")
print(f"devedor entries: {len(devedor)}")

out_path = r"c:\Users\afilh\OneDrive\Desktop\Github beehus\beehus-swat\beehusname_canonicals.py"
with open(out_path, "w", encoding="utf-8") as f:
    f.write('"""Curated issuer/devedor canonicalization tables.\n\n')
    f.write('Ported verbatim from `templates/controlpanel.html` (`_BANK_CANONICALS` /\n')
    f.write('`_DEVEDOR_CANONICALS`, ~templates/controlpanel.html:3431-3701) via\n')
    f.write('`scripts/port_canonicals.py` at ')
    f.write('registration-rules build time -- both this file and the JS tables are the\n')
    f.write('SAME curated data (no separate JSON source survives, per\n')
    f.write('`reference/beehusName-regra-final.md` §3). Keep them in sync by re-running\n')
    f.write('the port script after editing the JS tables, or vice-versa; do not hand-edit\n')
    f.write('one without the other drifting.\n"""\n\n')
    f.write("BANK_CANONICALS = [\n")
    for canonical, patterns in bank:
        f.write(f"    ({canonical!r}, {patterns!r}),\n")
    f.write("]\n\n")
    f.write("DEVEDOR_CANONICALS = [\n")
    for canonical, patterns in devedor:
        f.write(f"    ({canonical!r}, {patterns!r}),\n")
    f.write("]\n")

print(f"written to {out_path}")
