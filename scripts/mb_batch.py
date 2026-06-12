"""Processa em LOTE todos os EMR_*.xlsm de uma pasta — mesmo pipeline do
202507: aux + positions/transactions (JSON+XLSX) + positions_beehus.xlsx.

Carrega o SecurityCache do Mongo UMA vez e reusa em todos os arquivos.
Arquivos .xlsx OOXML normais são lidos direto; arquivos OLE2 (xlsx
criptografado) são descriptografados em memória com --password.

Uso:
  python scripts/mb_batch.py "data/.tmp"
  python scripts/mb_batch.py "data/.tmp" --password SENHA       # inclui os criptografados
  python scripts/mb_batch.py "data/.tmp" --only EMR_202508.xlsm
"""
import sys, os, io, glob, argparse, tempfile, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")

from mb_generate import build, write_xlsx, Resolver, COMPANY_ID
import mb_aux
import mb_to_beehus


def classify(path):
    """('plain'|'encrypted'|'unknown', head_bytes)."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head[:2] == b"PK":
        return "plain"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "encrypted"
    return "unknown"


def decrypt_to_temp(path, password):
    """Descriptografa um xlsx OLE2 para um arquivo temporário .xlsx. Retorna
    (temp_path, None) ou (None, motivo)."""
    import msoffcrypto
    try:
        of = msoffcrypto.OfficeFile(open(path, "rb"))
        of.load_key(password=password)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        with os.fdopen(fd, "wb") as out:
            of.decrypt(out)
        return tmp, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:50]}"


def process(path, read_path, resolver, atomic_write_json):
    stem = os.path.splitext(os.path.basename(path))[0]
    outdir = os.path.dirname(path)
    j = lambda s: os.path.join(outdir, f"{stem}_{s}")

    aux_stats = mb_aux.write_aux(read_path, resolver, j("aux.xlsx"))
    positions, pos_review, tx, tx_review = build(read_path, resolver=resolver)
    atomic_write_json(j("positions.json"),
                      {"companyId": COMPANY_ID, "unprocessedSecurities": positions})
    atomic_write_json(j("transactions.json"),
                      {"companyId": COMPANY_ID, "transactions": tx})
    write_xlsx(j("positions.xlsx"), pos_review)
    write_xlsx(j("transactions.xlsx"), tx_review)
    mb_to_beehus.write_wb(j("positions_beehus.xlsx"), positions)
    return len(positions), len(tx), aux_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--password", default=None, help="senha dos xlsx criptografados")
    ap.add_argument("--only", default=None, help="processar só este arquivo (basename)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.folder, "EMR_*.xlsm")))
    if args.only:
        files = [f for f in files if os.path.basename(f) == args.only]
    if not files:
        sys.exit("Nenhum EMR_*.xlsm encontrado.")

    from db import db, atomic_write_json
    from security_matcher import SecurityCache
    if not db._ready():
        sys.exit("DB não conectado (registre em /setup).")
    cache = SecurityCache(); cache.ensure_loaded(db)
    print(f"SecurityCache: {cache.count} securities | {len(files)} arquivos\n")
    resolver = Resolver(cache)

    done, skipped = [], []
    for f in files:
        name = os.path.basename(f)
        kind = classify(f)
        tmp = None
        try:
            if kind == "plain":
                read_path = f
            elif kind == "encrypted":
                if not args.password:
                    skipped.append((name, "criptografado (sem senha)"));
                    print(f"  SKIP {name}: criptografado — passe --password")
                    continue
                tmp, err = decrypt_to_temp(f, args.password)
                if err:
                    skipped.append((name, err)); print(f"  SKIP {name}: {err}"); continue
                read_path = tmp
            else:
                skipped.append((name, "formato desconhecido")); print(f"  SKIP {name}: formato desconhecido"); continue

            npos, ntx, st = process(f, read_path, resolver, atomic_write_json)
            done.append(name)
            print(f"  OK   {name}: positions={npos} tx={ntx} | aux wallets={len(st['wallets'])} "
                  f"sec={st['securities']}(resolv {st['resolved']}) PUids={st['pu_ids']}")
        except Exception as e:
            import traceback; traceback.print_exc()
            skipped.append((name, f"erro: {type(e).__name__}"))
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    print(f"\nConcluídos: {len(done)} | Pulados: {len(skipped)}")
    for n, why in skipped:
        print(f"  - {n}: {why}")


if __name__ == "__main__":
    main()
