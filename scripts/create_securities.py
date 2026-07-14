"""Bulk-create securities (ativos) in Beehus from a JSON file.

Mirrors the browser flow captured from the controlpanel "Cadastrar ativos"
modal: for each item, POST check-similar-securities (skip on any match) then
POST securities. Requires a Beehus bearer token already loaded — paste it at
/beehus in the running app (persists to ~/.swat/beehus.token), or set it once
via:
    python -c "from beehus_api import client; client.set_token('<jwt>')"

Usage:
    python scripts/create_securities.py <arquivo.json>
    python scripts/create_securities.py <arquivo.json> --dry-run
    python scripts/create_securities.py <arquivo.json> --delay-ms=300 --limit=10 --offset=20
    python scripts/create_securities.py <arquivo.json> --log=resultado.json
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beehus_api import securities
from beehus_api.client import get_token
from beehus_api.exceptions import BeehusAPIError, BeehusAuthError


def _label(item: dict) -> str:
    return (item.get("beehusName") or item.get("ticker") or item.get("isIn")
            or item.get("taxId") or "?")


def _identifier(item: dict) -> str | None:
    return item.get("ticker") or item.get("isIn") or item.get("taxId") or None


def main(path: str, *, delay_ms: int = 300, limit: int | None = None,
         offset: int = 0, dry_run: bool = False, log_path: str | None = None) -> int:
    if not dry_run and not get_token():
        sys.exit(
            "ERROR: nenhum token Beehus carregado (~/.swat/beehus.token).\n"
            "Abra o app e cole o token do dia em /beehus, ou rode:\n"
            '  python -c "from beehus_api import client; client.set_token(\'<jwt>\')"'
        )

    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if offset:
        items = items[offset:]
    if limit is not None:
        items = items[:limit]

    total = len(items)
    print(f"[info] {total} ativos a processar (delay={delay_ms}ms, dry_run={dry_run})")

    created, skipped, failed = [], [], []

    for idx, payload in enumerate(items, start=1):
        label = _label(payload)
        if dry_run:
            print(f"[dry ] {idx}/{total} {payload.get('securityType')}: {label}")
            continue

        try:
            similar = securities.check_similar_securities(payload)
        except (BeehusAPIError, BeehusAuthError) as exc:
            failed.append({"beehusName": label, "stage": "check-similar", "error": str(exc)})
            print(f"[err ] {idx}/{total} {label}: erro ao checar similares — {exc}")
            time.sleep(delay_ms / 1000)
            continue

        # `check-similar-securities` faz um match "parecido" (issuer/type/indexer/
        # nome), NÃO por identificador exato — confirmado ao vivo: apontou 3
        # debêntures do MESMO emissor com tickers e vencimentos diferentes como
        # "similares" de uma acabada de criar. Só tratamos como duplicata REAL
        # quando o identificador (ticker/isIn/taxId) bate exatamente; senão é só
        # um aviso e a criação segue.
        ident = _identifier(payload)
        exact = [s for s in similar if ident and _identifier(s) == ident]
        if exact:
            names = [s.get("beehusName") or s.get("mainId") for s in exact]
            skipped.append({"beehusName": label, "similar": names})
            print(f"[dup ] {idx}/{total} {label}: identificador já existe — {names} — pulado")
            time.sleep(delay_ms / 1000)
            continue
        if similar:
            names = [s.get("beehusName") or s.get("mainId") for s in similar]
            print(f"[warn] {idx}/{total} {label}: {len(similar)} similar(es) por nome/emissor "
                  f"(identificador diferente) — {names} — criando mesmo assim")

        try:
            result = securities.create_security_raw(payload)
        except (BeehusAPIError, BeehusAuthError) as exc:
            # `check-similar-securities` só pega parecidos por nome/emissor — um
            # ticker/isIn/taxId idêntico sob um beehusName bem diferente passa
            # batido por ela, mas o índice único do Mongo pega no create (visto
            # ao vivo: EAGL/DFNS/KBWB já existiam sob outro nome). Esse erro
            # específico é duplicata real, não falha.
            body = getattr(exc, "body", "") or ""
            if "mesmo securityType e mainId" in body:
                skipped.append({"beehusName": label, "similar": ["(mainId já existe na base)"]})
                print(f"[dup ] {idx}/{total} {label}: mainId já existe na base — pulado")
            else:
                failed.append({"beehusName": label, "stage": "create", "error": str(exc)})
                print(f"[err ] {idx}/{total} {label}: erro ao criar — {exc}")
            time.sleep(delay_ms / 1000)
            continue

        created.append({"beehusName": label, "_id": result.get("_id"), "mainId": result.get("mainId")})
        print(f"[ok  ] {idx}/{total} {label}: criado (_id={result.get('_id')})")
        time.sleep(delay_ms / 1000)

    print(f"\n[summary] criados={len(created)} pulados(duplicata)={len(skipped)} "
          f"falhas={len(failed)} total={total}")
    if failed:
        print("[failures]")
        for f in failed:
            print(f"  {f['beehusName']} ({f['stage']}): {f['error']}")

    if log_path:
        Path(log_path).write_text(
            json.dumps({"created": created, "skipped": skipped, "failed": failed},
                      ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[log] resultado salvo em {log_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        sys.exit(
            "uso: python scripts/create_securities.py <arquivo.json> "
            "[--delay-ms=300] [--dry-run] [--limit=N] [--offset=N] [--log=path]"
        )
    path = args[0]
    delay_ms = 300
    limit = None
    offset = 0
    dry_run = "--dry-run" in args
    log_path = None
    for a in args[1:]:
        if a.startswith("--delay-ms="):
            delay_ms = int(a.split("=", 1)[1])
        elif a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
        elif a.startswith("--offset="):
            offset = int(a.split("=", 1)[1])
        elif a.startswith("--log="):
            log_path = a.split("=", 1)[1]
    sys.exit(main(path, delay_ms=delay_ms, limit=limit, offset=offset,
                 dry_run=dry_run, log_path=log_path))