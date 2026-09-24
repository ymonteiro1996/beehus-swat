"""Verifica se os endpoints upstream do Beehus continuam existindo.

Uso:
    python scripts/check_api_endpoints.py "<bearer token>"

Só faz chamadas SEM efeito colateral:
  - GETs reais (com params válidos, descobertos ao vivo);
  - PATCH/DELETE contra um _id inexistente (nada pra alterar/apagar);
  - OPTIONS nos endpoints de escrita/perigosos (process, delete, publish...).

Classificação por status: 404/405 = SUMIU/mudou de método; 2xx/4xx (400,
401, 403, 422, 500) = a rota EXISTE (o backend chegou a processar).
"""
import json
import os
import sys

import requests

BASE = os.environ.get("BEEHUS_BASE", "https://api.controladoria.beehus.com.br")
BOGUS = "000000000000000000000000"  # ObjectId válido em forma, inexistente
TIMEOUT = 60

tok = sys.argv[1] if len(sys.argv) > 1 else ""
S = requests.Session()
S.headers["Authorization"] = f"Bearer {tok}"

results = []


def call(method, path, *, params=None, json_body=None, note=""):
    """Classifica pelo CORPO, nao pelo status.

    Rota inexistente => Express devolve HTML `Cannot <METHOD> /path`.
    Rota existente   => JSON da aplicacao, mesmo em 400/404/500.
    (Um 404 com corpo JSON e "registro nao encontrado", NAO rota ausente.)
    """
    try:
        r = S.request(method, BASE + path, params=params, json=json_body, timeout=TIMEOUT)
        code, body = r.status_code, r.text
    except Exception as e:
        results.append(("ERRO-REDE", method, path, None, note, f"{type(e).__name__}: {e}"))
        return None, ""
    if code in (502, 503):
        verdict, detail = "BACKEND-FORA", ""
    elif "Cannot " in body and "<pre>" in body:
        verdict = "SUMIU"
        detail = body.split("<pre>")[-1].split("</pre>")[0]
    else:
        verdict = "EXISTE"
        detail = ""
        if body.lstrip().startswith("{"):
            try:
                msg = json.loads(body).get("message")
                detail = (" | ".join(msg) if isinstance(msg, list) else str(msg or ""))[:300]
            except Exception:
                pass
    results.append((verdict, method, path, code, note, detail))
    return code, body


def data(method, path, **kw):
    """Chamada cujo corpo a gente quer usar pra alimentar as próximas."""
    code, body = call(method, path, **kw)
    if code and 200 <= code < 300:
        try:
            return json.loads(body) if False else S.request(
                method, BASE + path, params=kw.get("params"), timeout=TIMEOUT).json()
        except Exception:
            return None
    return None


# ── 1. descoberta: empresa / carteira / ativo pra usar como parâmetro ───────
companies = data("GET", "/beehus/partners/companies")
company_id = ""
if isinstance(companies, list) and companies:
    company_id = str(companies[0].get("companyId") or companies[0].get("_id") or "")
company_id = company_id or "00000000000001"

wallets = data("GET", f"/beehus/partner-info/{company_id}/wallets")
wallet_id = ""
if isinstance(wallets, list) and wallets:
    wallet_id = str(wallets[0].get("_id") or wallets[0].get("walletId") or "")

secs = data("GET", "/beehus/securities")
security_id = ""
if isinstance(secs, list) and secs:
    security_id = str(secs[0].get("_id") or "")

D0, D1 = "2026-08-01", "2026-08-31"

# ── 2. GETs (leitura pura) ──────────────────────────────────────────────────
call("GET", "/beehus/entities")
call("GET", "/beehus/grouping", params={"companyId": company_id})
if security_id:
    call("GET", f"/beehus/securities/{security_id}")
    call("GET", "/beehus/security-events", params={"securities": security_id})
    call("GET", "/beehus/security-prices/filtered-security-price",
         params={"securityIds": [security_id], "pricingType": "B1,B2,C1,C2,C3"})
call("GET", "/beehus/financial/transactions",
     params={"companyId": company_id, "initialDate": D0, "finalDate": D1,
             "dateType": "liquidation", "walletIds": wallet_id})
call("GET", "/beehus/financial/execution-prices",
     params={"companyId": company_id, "initialDate": D0, "finalDate": D1})
call("GET", "/beehus/financial/security-mappings", params={"companyId": company_id})
call("GET", "/beehus/financial/positions/processed-position",
     params={"companyId": company_id, "date": D1, "walletIds": wallet_id})
call("GET", "/beehus/financial/positions/unprocessed-security-positions",
     params={"companyId": company_id, "initialDate": D0, "finalDate": D1,
             "walletIds": wallet_id})
call("GET", "/beehus/financial/positions/processed-position/pre-processing",
     params={"positionDate": D1, "companyId": company_id})
call("GET", "/beehus/provisions",
     params={"companyId": company_id, "initialDate": "2000-01-01", "finalDate": D1})
call("GET", "/beehus/consolidation/company-variables", params={"companyId": ""})
call("GET", "/beehus/consolidation/nav-contribution-calculation",
     params={"id": wallet_id, "companyId": company_id, "type": "wallet",
             "initialDate": "2000-01-01", "finalDate": D1})
call("GET", "/beehus/consolidation/nav-contribution-calculation/results",
     params={"positionDate": D1, "companyId": company_id})

# ── 3. escrita por _id inexistente (não altera nada) ────────────────────────
call("PATCH", f"/beehus/securities/{BOGUS}", json_body={}, note="id inexistente")
call("PATCH", f"/beehus/financial/transactions/{BOGUS}", json_body={}, note="id inexistente")
call("DELETE", f"/beehus/financial/transactions/{BOGUS}", note="id inexistente")
call("PATCH", f"/beehus/provisions/{BOGUS}", json_body={}, note="id inexistente")
call("DELETE", f"/beehus/provisions/{BOGUS}", note="id inexistente")
call("PATCH", f"/beehus/financial/execution-prices/{BOGUS}", json_body={}, note="id inexistente")
call("DELETE", f"/beehus/financial/execution-prices/{BOGUS}", note="id inexistente")
call("PATCH", f"/beehus/financial/security-mappings/{BOGUS}", json_body={}, note="id inexistente")

# duplicate-check é leitura na prática
call("POST", "/beehus/securities/check-similar-securities",
     json_body={"name": "__probe__zzz", "companyId": company_id}, note="dup-check (leitura)")

# ── 4. endpoints de escrita: corpo VAZIO ────────────────────────────────────
# Seguro: a API tem ValidationPipe global com whitelist — `{}` e rejeitado com
# 400 ANTES de o handler rodar, entao nada e criado, processado ou publicado.
for method, p in (("POST",   "/beehus/securities"),
                  ("POST",   "/beehus/financial/transactions"),
                  ("POST",   "/beehus/provisions"),
                  ("POST",   "/beehus/financial/execution-prices"),
                  ("POST",   "/beehus/financial/positions/processed-position/process"),
                  ("DELETE", "/beehus/financial/positions/processed-position/delete"),
                  ("POST",   "/beehus/financial/positions/unprocessed-security-positions/file"),
                  ("POST",   "/beehus/consolidation/nav-contribution-calculation/wallets"),
                  ("POST",   "/beehus/consolidation/nav-contribution-calculation/groupings"),
                  ("POST",   "/beehus/consolidation/nav-contribution-calculation/explosion-proportions"),
                  ("PATCH",  "/beehus/consolidation/nav-contribution-calculation/publish"),
                  ("PATCH",  "/beehus/consolidation/nav-contribution-calculation/unpublish")):
    call(method, p, json_body={}, note="corpo vazio (handler nao executa)")

# ── relatório ───────────────────────────────────────────────────────────────
print(f"\ncompanyId={company_id}  walletId={wallet_id or '-'}  securityId={security_id or '-'}\n")
print(f"{'VEREDITO':<13}{'MET':<8}{'STATUS':<8}ENDPOINT")
print("-" * 100)
for verdict, method, path, code, note, detail in results:
    print(f"{verdict:<13}{method:<8}{str(code):<8}{path}" + (f"   [{note}]" if note else ""))
    if detail:
        print(f"{'':<29}-> {detail}")
bad  = [r for r in results if r[0] in ("SUMIU", "ERRO-REDE")]
down = [r for r in results if r[0] == "BACKEND-FORA"]
if down:
    print(f"\nINCONCLUSIVO: {len(down)}/{len(results)} chamadas voltaram 502/503 — o upstream "
          "atras do nginx esta fora. Nada foi verificado; rode de novo quando a API responder.")
elif bad:
    print(f"\n{len(bad)} endpoint(s) com problema.")
else:
    print("\nNenhum endpoint sumiu.")
