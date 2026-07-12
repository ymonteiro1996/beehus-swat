"""Unit tests for the explosion-chain resolver (beehus_catalog).

Offline: monkeypatches the two data sources the resolver reads —
`company_wallets_index` (→ securitiesForExplosion) and the security lookups
(`security_doc` / `get_security` → correspondingWallet) — so no token or network
is needed. Covers every edge case in docs/EXPLOSION_CHAIN.md: single level,
multi-level chain, auto-reference, cycle, leaf, dedup, fallback GET, and the
`expand_wallets_with_explosion` union.

Run:  python scripts/test_explosion_chain.py   (or: pytest scripts/test_explosion_chain.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beehus_catalog as bc

CID = "company-1"


def _install(wallets, securities, *, get_security_only=None):
    """Patch the resolver's data sources.

    wallets:    {walletId: [securityId, ...]}  (securitiesForExplosion)
    securities: {securityId: walletId|None}    (correspondingWallet target,
                served via the cached catalog `security_doc`)
    get_security_only: {securityId: walletId}   (served ONLY by the per-id GET,
                absent from the catalog → exercises the fallback path)
    """
    bc.invalidate()  # drop per-security correspondingWallet cache between cases

    wallet_docs = {
        wid: {"_id": wid, "name": f"W-{wid}", "securitiesForExplosion": list(secs)}
        for wid, secs in wallets.items()
    }
    # Ensure every referenced target wallet exists in the index (for name lookup).
    for tgt in list(securities.values()) + list((get_security_only or {}).values()):
        if tgt and tgt not in wallet_docs:
            wallet_docs[tgt] = {"_id": tgt, "name": f"W-{tgt}",
                                "securitiesForExplosion": []}

    bc.company_wallets_index = lambda cid: wallet_docs if cid == CID else {}

    def _sec_doc(sid):
        sid = str(sid)
        if sid in securities:
            tgt = securities[sid]
            cw = {"_id": tgt, "name": f"W-{tgt}"} if tgt else None
            return {"_id": sid, "correspondingWallet": cw}
        return None  # not in catalog → forces the GET fallback

    def _get_sec(*, security_id, timeout=30):
        sid = str(security_id)
        if get_security_only and sid in get_security_only:
            tgt = get_security_only[sid]
            return {"_id": sid, "correspondingWallet": {"_id": tgt, "name": f"W-{tgt}"}}
        return None

    bc.security_doc = _sec_doc
    bc.get_security = _get_sec


def _chain_wallets(chain):
    return [p["walletId"] for p in chain]


def test_no_explosion():
    _install({"A": []}, {})
    assert bc.explosion_chain(CID, "A") == []
    assert bc.expand_wallets_with_explosion(CID, ["A"]) == ["A"]


def test_single_level_leaf():
    _install({"A": ["s1"]}, {"s1": "B"})
    chain = bc.explosion_chain(CID, "A")
    assert _chain_wallets(chain) == ["B"]
    assert chain[0]["level"] == 1
    assert chain[0]["viaWalletId"] == "A"
    assert chain[0]["securityId"] == "s1"
    assert bc.expand_wallets_with_explosion(CID, ["A"]) == ["A", "B"]


def test_multi_level_chain():
    _install({"A": ["s1"], "B": ["s2"]}, {"s1": "B", "s2": "C"})
    chain = bc.explosion_chain(CID, "A")
    assert _chain_wallets(chain) == ["B", "C"]
    by_wid = {p["walletId"]: p for p in chain}
    assert by_wid["B"]["level"] == 1 and by_wid["B"]["viaWalletId"] == "A"
    assert by_wid["C"]["level"] == 2 and by_wid["C"]["viaWalletId"] == "B"
    assert bc.expand_wallets_with_explosion(CID, ["A"]) == ["A", "B", "C"]


def test_auto_reference():
    # Ativo de explosão aponta para a própria carteira → bloqueado pelo seen.
    _install({"A": ["s1"]}, {"s1": "A"})
    assert bc.explosion_chain(CID, "A") == []
    assert bc.expand_wallets_with_explosion(CID, ["A"]) == ["A"]


def test_cycle_between_wallets():
    # A→B→A: B entra uma vez; A (raiz) nunca reentra.
    _install({"A": ["s1"], "B": ["s2"]}, {"s1": "B", "s2": "A"})
    chain = bc.explosion_chain(CID, "A")
    assert _chain_wallets(chain) == ["B"]
    assert bc.expand_wallets_with_explosion(CID, ["A"]) == ["A", "B"]


def test_multiple_securities_same_target():
    _install({"A": ["s1", "s2"]}, {"s1": "B", "s2": "B"})
    assert _chain_wallets(bc.explosion_chain(CID, "A")) == ["B"]


def test_missing_corresponding_wallet_skipped():
    _install({"A": ["s1", "s2"]}, {"s1": None, "s2": "B"})
    assert _chain_wallets(bc.explosion_chain(CID, "A")) == ["B"]


def test_fallback_get_security():
    # Catálogo não traz correspondingWallet do s1; o GET pontual traz.
    _install({"A": ["s1"]}, {}, get_security_only={"s1": "B"})
    assert _chain_wallets(bc.explosion_chain(CID, "A")) == ["B"]


def test_expand_dedup_across_roots():
    # Raízes [A, B]; A arrasta B. B não pode aparecer duplicado.
    _install({"A": ["s1"]}, {"s1": "B"})
    assert bc.expand_wallets_with_explosion(CID, ["A", "B"]) == ["A", "B"]


def test_expand_empty_is_passthrough():
    _install({"A": ["s1"]}, {"s1": "B"})
    # Lista vazia = "todas as carteiras" no contrato do /process → sem expansão.
    assert bc.expand_wallets_with_explosion(CID, []) == []


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"ERR   {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
