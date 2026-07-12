"""Transaction-type classifier (hybrid: rules + ML fallback).

Used by funcoes > identificar transactions to predict beehusTransactionType
from a transaction description.

Pipeline:
    description -> normalize -> rule cascade -> [ML fallback if no rule] -> result

Rule definitions live in data/transaction_type_rules.json (validated dictionary,
governs prefix overrides and Brazilian/English transaction conventions).
Training data lives in data/transactions_base.json (labelled rows only).

Two responsibilities:
  1. rebuild_training_data(db)  — query MongoDB and refresh data/transactions_base.json
  2. TransactionTypeClassifier  — apply rules; train/predict ML for residuals
"""
import json
import logging
import os
import re
import unicodedata

log = logging.getLogger(__name__)

DATA_DIR      = os.path.join(os.path.dirname(__file__), "data")
RULES_PATH    = os.path.join(DATA_DIR, "transaction_type_rules.json")
TRAINING_PATH = os.path.join(DATA_DIR, "transactions_base.json")


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
    """Apply the normalisation contract from data/transaction_type_rules.json _normalization."""
    if raw is None:
        return ""
    text = _fix_mojibake(raw)
    text = _strip_accents(text)
    text = text.upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Rule cascade ──────────────────────────────────────────────────────────────

def _compile_rules(rules_doc):
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


# ── Rebuild training data from MongoDB ────────────────────────────────────────

def rebuild_training_data(db):
    """
    Pull (description, beehusTransactionType) pairs from MongoDB and write
    data/transactions_base.json. Used by the daily routine to refresh the
    model's training data with newly-labelled transactions.

    Returns (total_rows, labelled_rows).
    """
    rows = []
    cursor = db.transactions.find(
        {"description": {"$exists": True, "$ne": ""}},
        {"description": 1, "beehusTransactionType": 1},
    )
    for doc in cursor:
        rows.append({
            "description":            doc.get("description", "") or "",
            "beehusTransactionType":  doc.get("beehusTransactionType"),
        })

    from db import atomic_write_json
    atomic_write_json(TRAINING_PATH, rows)

    n_total    = len(rows)
    n_labelled = sum(1 for r in rows if r.get("beehusTransactionType"))
    log.info("Wrote %d transaction rows (%d labelled) to %s", n_total, n_labelled, TRAINING_PATH)
    return n_total, n_labelled


# ── Classifier ────────────────────────────────────────────────────────────────

class TransactionTypeClassifier:
    """
    Hybrid rule + ML classifier for beehusTransactionType.

    Workflow:
        clf = TransactionTypeClassifier()
        clf.train()                                # load rules + training data, fit ML model
        result = clf.predict("AQUISIÇÃO DE COTAS") # -> {type, source, confidence, needs_review, top3}
    """

    def __init__(self, rules_path=None, training_path=None):
        self._rules_path     = rules_path    or RULES_PATH
        self._training_path  = training_path or TRAINING_PATH
        self._rules_doc      = None
        self._compiled_rules = None
        self._classes        = None
        self._thresholds     = None
        self._vectoriser     = None
        self._model          = None
        self._labels         = []
        self._trained        = False

    # ── training ──────────────────────────────────────────────────────────────

    def train(self):
        """Load rules + labelled training rows and fit the ML model. Returns self."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        with open(self._rules_path, encoding="utf-8") as f:
            self._rules_doc = json.load(f)
        self._compiled_rules = _compile_rules(self._rules_doc)
        self._classes        = self._rules_doc["classes"]
        self._thresholds     = self._rules_doc["_thresholds"]

        with open(self._training_path, encoding="utf-8") as f:
            rows = json.load(f)
        labelled = [r for r in rows if r.get("beehusTransactionType")]
        if len(labelled) < 50:
            log.warning("Only %d labelled rows — ML fallback will be unreliable", len(labelled))

        texts  = [normalize(r["description"]) for r in labelled]
        labels = [r["beehusTransactionType"] for r in labelled]

        # Char n-grams (within word boundaries) are robust to mojibake artifacts,
        # PT/EN mixing, and the structured pipe-delimited descriptions used by
        # international custodians.
        self._vectoriser = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            max_features=30_000,
            sublinear_tf=True,
            min_df=2,
        )
        X = self._vectoriser.fit_transform(texts)

        self._model = LogisticRegression(
            max_iter=2000,
            solver="lbfgs",
            C=4.0,
            class_weight="balanced",  # rare classes (managementFee=6, securityTransfer=5)
        )
        self._model.fit(X, labels)
        self._labels  = list(self._model.classes_)
        self._trained = True
        log.info(
            "Classifier trained: %d labelled samples, %d classes, %d rules",
            len(labelled), len(self._labels), len(self._compiled_rules),
        )
        return self

    # ── prediction ────────────────────────────────────────────────────────────

    def _classify_rule(self, normalized):
        for r in self._compiled_rules:
            if r["test"](normalized):
                return r["id"], r["type"]
        return None, None

    def _classify_ml(self, normalized):
        vec    = self._vectoriser.transform([normalized])
        proba  = self._model.predict_proba(vec)[0]
        ranked = sorted(zip(self._labels, proba), key=lambda x: -x[1])
        top3   = [(t, float(c)) for t, c in ranked[:3]]
        top_type, top_conf = ranked[0]
        return top_type, float(top_conf), top3

    def predict(self, description):
        """
        Classify a single description.

        Returns a dict:
            {
              "type":         "buySell" | ... | None,
              "source":       rule_id | "ml" | "empty",
              "confidence":   float in [0, 1],
              "needs_review": bool,    # True if ML confidence below review threshold
              "top3":         [(type, confidence), ...]  # ML top-3 candidates
            }
        """
        if not self._trained:
            raise RuntimeError("Classifier not trained — call .train() first.")

        normalized = normalize(description)
        if not normalized:
            return {
                "type":         None,
                "source":       "empty",
                "confidence":   0.0,
                "needs_review": True,
                "top3":         [],
            }

        # Step 1: rule cascade
        rule_id, rule_type = self._classify_rule(normalized)
        if rule_id is not None:
            return {
                "type":         rule_type,
                "source":       rule_id,
                "confidence":   self._thresholds["rule_match_confidence"],
                "needs_review": False,
                "top3":         [(rule_type, 1.0)],
            }

        # Step 2: ML fallback
        ml_type, ml_conf, top3 = self._classify_ml(normalized)
        review_below   = self._thresholds["ml_review_below"]
        critical_below = self._thresholds["ml_review_critical_below"]

        if ml_conf < critical_below:
            return {
                "type":         None,                    # below critical → no prediction
                "source":       "ml",
                "confidence":   round(ml_conf, 4),
                "needs_review": True,
                "top3":         [(t, round(c, 4)) for t, c in top3],
            }

        return {
            "type":         ml_type,
            "source":       "ml",
            "confidence":   round(ml_conf, 4),
            "needs_review": ml_conf < review_below,
            "top3":         [(t, round(c, 4)) for t, c in top3],
        }

    def predict_batch(self, descriptions):
        """Classify a list of descriptions. Returns list of dicts in input order."""
        return [self.predict(d) for d in descriptions]

    # ── evaluation ────────────────────────────────────────────────────────────

    def evaluate(self):
        """5-fold CV on the ML stage only (ignores rules). Returns accuracy + per-class metrics."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report
        from sklearn.model_selection import cross_val_predict

        with open(self._training_path, encoding="utf-8") as f:
            rows = json.load(f)
        labelled = [r for r in rows if r.get("beehusTransactionType")]
        texts    = [normalize(r["description"]) for r in labelled]
        labels   = [r["beehusTransactionType"] for r in labelled]

        vec = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5),
            max_features=30_000, sublinear_tf=True, min_df=2,
        )
        X = vec.fit_transform(texts)
        model = LogisticRegression(
            max_iter=2000, solver="lbfgs", C=4.0, class_weight="balanced",
        )
        preds = cross_val_predict(model, X, labels, cv=5)

        report   = classification_report(labels, preds, output_dict=True, zero_division=0)
        accuracy = report.pop("accuracy")
        per_class = {
            k: {m: round(v, 4) for m, v in metrics.items() if m != "support"}
            for k, metrics in report.items()
            if k not in ("macro avg", "weighted avg")
        }
        return {"accuracy": round(accuracy, 4), "per_class": per_class}

    def evaluate_hybrid(self):
        """
        Run the full pipeline (rules + ML) on the labelled training set and
        report end-to-end accuracy. This is the metric that matters in production.
        """
        with open(self._training_path, encoding="utf-8") as f:
            rows = json.load(f)
        labelled = [r for r in rows if r.get("beehusTransactionType")]

        n_total = len(labelled)
        n_correct = n_rule_correct = n_ml_correct = 0
        n_rule_fired = n_ml_fired = 0
        n_review = n_unclassified = 0

        for r in labelled:
            res = self.predict(r["description"])
            truth = r["beehusTransactionType"]

            if res["source"] == "ml":
                n_ml_fired += 1
                if res["type"] == truth:
                    n_correct      += 1
                    n_ml_correct   += 1
            elif res["source"] not in ("empty",):
                n_rule_fired += 1
                if res["type"] == truth:
                    n_correct       += 1
                    n_rule_correct  += 1

            if res["needs_review"]:    n_review        += 1
            if res["type"] is None:    n_unclassified  += 1

        return {
            "total":            n_total,
            "rule_fired":       n_rule_fired,
            "ml_fired":         n_ml_fired,
            "rule_accuracy":    round(n_rule_correct / max(1, n_rule_fired), 4),
            "ml_accuracy":      round(n_ml_correct  / max(1, n_ml_fired),   4),
            "overall_accuracy": round(n_correct      / n_total,              4),
            "needs_review":     n_review,
            "unclassified":     n_unclassified,
        }


# ── Quick CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--rebuild" in sys.argv:
        sys.path.insert(0, os.path.dirname(__file__))
        # Offline CLI: connect to Mongo explicitly (the web app never does).
        # URI from $SWAT_MONGO_URI or the saved user_connections.json entry.
        import db as _dbmod
        mongo_client = _dbmod.connect_for_cli()
        try:
            total, mapped = rebuild_training_data(_dbmod.db)
        finally:
            mongo_client.close()
        print(f"Rebuild done: {total} rows, {mapped} labelled.")

    if "--eval" in sys.argv:
        clf = TransactionTypeClassifier()
        clf.train()
        result = clf.evaluate()
        print(f"\nML 5-fold CV accuracy (ignoring rules): {result['accuracy']:.2%}\n")
        print(f"{'class':35s} {'precision':>10s} {'recall':>10s} {'f1':>10s}")
        print("-" * 67)
        for cls, m in sorted(result["per_class"].items()):
            print(f"{cls:35s} {m['precision']:10.2%} {m['recall']:10.2%} {m['f1-score']:10.2%}")

    if "--eval-hybrid" in sys.argv:
        clf = TransactionTypeClassifier()
        clf.train()
        result = clf.evaluate_hybrid()
        print(f"\nHybrid pipeline on labelled training set:")
        print(f"  Total           : {result['total']}")
        print(f"  Rule fired      : {result['rule_fired']:>5}  ({result['rule_fired']/result['total']:.1%})")
        print(f"  ML fired        : {result['ml_fired']:>5}  ({result['ml_fired']/result['total']:.1%})")
        print(f"  Rule accuracy   : {result['rule_accuracy']:.1%}  (rule fires == label)")
        print(f"  ML accuracy     : {result['ml_accuracy']:.1%}    (ML predicts == label)")
        print(f"  Overall         : {result['overall_accuracy']:.1%}")
        print(f"  Needs review    : {result['needs_review']}  (low ML confidence)")
        print(f"  Unclassified    : {result['unclassified']}  (below critical threshold)")

    if "--predict" in sys.argv:
        idx = sys.argv.index("--predict")
        descs = sys.argv[idx + 1:]
        if not descs:
            print("Usage: --predict 'SOME DESCRIPTION'")
        else:
            clf = TransactionTypeClassifier()
            clf.train()
            for d in descs:
                r = clf.predict(d)
                print(f"\n  Input:    {d}")
                print(f"  Type:     {r['type']}  (confidence {r['confidence']:.1%})")
                print(f"  Source:   {r['source']}")
                print(f"  Review?   {r['needs_review']}")
                print(f"  Top 3:    {r['top3']}")
