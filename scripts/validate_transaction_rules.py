"""Coverage validator for data/transaction_type_rules.json.

Runs the rule cascade against data/transactions_base.json and reports:
  1. Coverage  – how many rows hit a rule vs unmatched
  2. Agreement – rule prediction vs existing label (where labelled)
  3. Rule usage – hit count per rule (zero-fire rules are dead weight)
  4. Conflicts – descriptions where rule disagrees with ground-truth label
  5. Sample unmatched descriptions for null + labelled rows

This is a one-shot diagnostic, not part of the runtime classifier.
"""
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(ROOT, "data", "transaction_type_rules.json")
DATA_PATH  = os.path.join(ROOT, "data", "transactions_base.json")


# ── Normalisation ─────────────────────────────────────────────────────────────

def _fix_mojibake(text):
    """Repair latin-1 ↔ utf-8 corruption (e.g. 'Ã§' → 'ç'). Idempotent on clean text."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize(raw):
    if raw is None:
        return ""
    text = _fix_mojibake(raw)
    text = _strip_accents(text)
    text = text.upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Rule cascade ──────────────────────────────────────────────────────────────

def compile_rules(rules_doc):
    compiled = []
    for rule in rules_doc["rules"]:
        match_type = rule.get("match", "regex")
        pat        = rule["pattern"]
        if match_type == "regex":
            matcher = re.compile(pat)
            test    = matcher.search
        elif match_type == "contains":
            test = lambda txt, p=pat.upper(): p in txt
        elif match_type == "startswith":
            test = lambda txt, p=pat.upper(): txt.startswith(p)
        else:
            raise ValueError(f"Unknown match type {match_type!r} on rule {rule['id']}")
        compiled.append({"id": rule["id"], "type": rule["type"], "test": test})
    return compiled


def classify(normalized_desc, compiled_rules):
    """Return (rule_id, predicted_type) of first matching rule, else (None, None)."""
    for r in compiled_rules:
        if r["test"](normalized_desc):
            return r["id"], r["type"]
    return None, None


# ── Validation ────────────────────────────────────────────────────────────────

def main():
    with open(RULES_PATH, encoding="utf-8") as f:
        rules_doc = json.load(f)
    with open(DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    compiled = compile_rules(rules_doc)
    classes  = set(rules_doc["classes"])

    n_total       = len(rows)
    n_labelled    = sum(1 for r in rows if r.get("beehusTransactionType"))
    n_unlabelled  = n_total - n_labelled

    rule_hits     = Counter()                       # rule_id  -> count
    pred_dist     = Counter()                       # type     -> count
    agree         = 0                               # rule == truth
    disagree      = 0                               # rule != truth
    no_rule_lab   = 0                               # labelled but no rule fired
    no_rule_null  = 0                               # null and no rule fired
    conflicts     = defaultdict(list)               # (truth, pred) -> [(desc, rule_id), ...]
    unmatched     = []                              # [(desc, truth)]

    for row in rows:
        raw   = row.get("description", "") or ""
        truth = row.get("beehusTransactionType")
        norm  = normalize(raw)
        rid, pred = classify(norm, compiled)

        if rid is not None:
            rule_hits[rid] += 1
            pred_dist[pred] += 1
            if truth:
                if pred == truth:
                    agree += 1
                else:
                    disagree += 1
                    if len(conflicts[(truth, pred)]) < 5:
                        conflicts[(truth, pred)].append((raw, rid))
        else:
            if truth:
                no_rule_lab += 1
            else:
                no_rule_null += 1
            if len(unmatched) < 30 or truth is not None:
                unmatched.append((raw, truth))

    # ── Report ────────────────────────────────────────────────────────────────
    print("=" * 78)
    print(f"Coverage validation – data/transaction_type_rules.json")
    print("=" * 78)
    print(f"Total rows               : {n_total}")
    print(f"Labelled (ground truth)  : {n_labelled}")
    print(f"Unlabelled (null)        : {n_unlabelled}")
    print()

    n_rule_fired = sum(rule_hits.values())
    print(f"Rule fired               : {n_rule_fired}  ({n_rule_fired/n_total:.1%})")
    print(f"Unmatched (labelled)     : {no_rule_lab}   ({no_rule_lab/max(1,n_labelled):.1%} of labelled)")
    print(f"Unmatched (null)         : {no_rule_null}   ({no_rule_null/max(1,n_unlabelled):.1%} of null)")
    print()

    if n_labelled:
        denom = agree + disagree + no_rule_lab
        print(f"On labelled rows ({denom}):")
        print(f"  rule predicts truth    : {agree}     ({agree/denom:.1%})")
        print(f"  rule disagrees w/truth : {disagree}  ({disagree/denom:.1%})")
        print(f"  no rule fired          : {no_rule_lab}  ({no_rule_lab/denom:.1%})")
        print()

    print("-" * 78)
    print("Rule hit counts (top 25, then zero-fire):")
    print("-" * 78)
    rule_lookup = {r["id"]: r for r in rules_doc["rules"]}
    for rid, n in rule_hits.most_common(25):
        print(f"  {rid:32s} {n:>5}   -> {rule_lookup[rid]['type']}")
    zeros = [r["id"] for r in rules_doc["rules"] if rule_hits[r["id"]] == 0]
    if zeros:
        print(f"\n  Zero-fire rules ({len(zeros)}): {', '.join(zeros)}")
    print()

    print("-" * 78)
    print("Top conflicts (rule vs ground truth) - samples:")
    print("-" * 78)
    sorted_conf = sorted(conflicts.items(), key=lambda kv: -len(kv[1]))
    for (truth, pred), samples in sorted_conf[:15]:
        n_pair = sum(1 for r in rows if r.get("beehusTransactionType") == truth
                     and classify(normalize(r.get("description", "")), compiled)[1] == pred)
        print(f"\n  truth={truth!r}  rule={pred!r}  ({n_pair} rows)")
        for desc, rid in samples[:3]:
            print(f"    [{rid}]  {desc[:120]}")
    print()

    print("-" * 78)
    print("Sample unmatched descriptions (up to 25):")
    print("-" * 78)
    for desc, truth in unmatched[:25]:
        tag = f"truth={truth!r}" if truth else "null"
        print(f"  [{tag:42s}] {desc[:120]}")
    print()

    # Predicted-type distribution
    print("-" * 78)
    print("Predicted-type distribution (rule output only):")
    print("-" * 78)
    for t, n in sorted(pred_dist.items(), key=lambda kv: -kv[1]):
        flag = "" if t in classes else "  !! NOT IN classes!"
        print(f"  {t:35s} {n:>5}{flag}")


if __name__ == "__main__":
    sys.exit(main() or 0)
