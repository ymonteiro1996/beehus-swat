"""Apply a security-mappings patch (custodian string -> security) in Beehus via API.

Mirrors create_securities.py: reads the same `mappings_patch_*.json` shape
already produced by the manual cadastro workflow —
    {"securityMappingId": "...", "mappings": {"mappingsToInclude": [{"from","to"}, ...],
                                               "mappingsToExclude": [...]}}
and PATCHes it in one call via beehus_api.security_mappings.update_security_mappings.
Requires a Beehus bearer token already loaded — paste it at /beehus in the
running app (persists to ~/.swat/beehus.token), or set it once via:
    python -c "from beehus_api import client; client.set_token('<jwt>')"

Usage:
    python scripts/apply_security_mapping.py <arquivo.json>
    python scripts/apply_security_mapping.py <arquivo.json> --dry-run
    python scripts/apply_security_mapping.py <arquivo.json> --log=resultado.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beehus_api import security_mappings
from beehus_api.client import get_token
from beehus_api.exceptions import BeehusAPIError, BeehusAuthError


def main(path: str, *, dry_run: bool = False, log_path: str | None = None) -> int:
    if not dry_run and not get_token():
        sys.exit(
            "ERROR: nenhum token Beehus carregado (~/.swat/beehus.token).\n"
            "Abra o app e cole o token do dia em /beehus, ou rode:\n"
            '  python -c "from beehus_api import client; client.set_token(\'<jwt>\')"'
        )

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    security_mapping_id = data.get("securityMappingId")
    mappings = data.get("mappings", {})
    to_include = mappings.get("mappingsToInclude", [])
    to_exclude = mappings.get("mappingsToExclude", [])

    if not security_mapping_id:
        sys.exit(f"ERROR: '{path}' não tem securityMappingId no topo do JSON.")

    print(f"[info] securityMappingId={security_mapping_id} | "
          f"{len(to_include)} to include, {len(to_exclude)} to exclude")

    if dry_run:
        for m in to_include:
            print(f"[dry ] include: {m['from']!r} -> {m['to']}")
        for m in to_exclude:
            print(f"[dry ] exclude: {m}")
        return 0

    try:
        result = security_mappings.update_security_mappings(
            security_mapping_id,
            mappings_to_include=to_include,
            mappings_to_exclude=to_exclude,
        )
    except (BeehusAPIError, BeehusAuthError) as exc:
        print(f"[err ] falha ao aplicar mapping: {exc}")
        if log_path:
            Path(log_path).write_text(
                json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return 1

    print(f"[ok  ] mapping aplicado: {len(to_include)} incluídos, {len(to_exclude)} excluídos")
    if log_path:
        Path(log_path).write_text(
            json.dumps({"applied": to_include, "excluded": to_exclude, "response": result},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[log] resultado salvo em {log_path}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        sys.exit(
            "uso: python scripts/apply_security_mapping.py <arquivo.json> "
            "[--dry-run] [--log=path]"
        )
    path = args[0]
    dry_run = "--dry-run" in args
    log_path = None
    for a in args[1:]:
        if a.startswith("--log="):
            log_path = a.split("=", 1)[1]
    sys.exit(main(path, dry_run=dry_run, log_path=log_path))
