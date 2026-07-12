"""
Security-type classifier for unprocessed securities.

Two responsibilities:
  1. rebuild_mapping()  – regenerate the labelled JSON from the security-mappings
     cache + securities catalog (API; no MongoDB)
  2. SecurityTypeClassifier – train a TF-IDF + Logistic Regression model from
     the labelled JSON and predict securityType for new unprocessedId strings.
"""
import json, os, re, logging
from bson import ObjectId
import beehus_catalog

log = logging.getLogger(__name__)

DATA_DIR  = os.path.join(os.path.dirname(__file__), "data")
JSON_PATH = os.path.join(DATA_DIR, "unprocessed_security_types.json")


# ── 1. Rebuild the labelled mapping from the security-mappings catalog (API) ─

def rebuild_mapping(db=None):
    """
    Regenerate data/unprocessed_security_types.json from the security-mappings
    cache (API-backed) + the securities catalog. **No longer reads MongoDB.**

    The distinct unprocessedIds are exactly the mapping `from` values, served by
    `MappingCache` (GET /beehus/financial/security-mappings), so the old
    all-company `db.unprocessedSecurityPositions` scan is unnecessary. `db` is
    accepted for backward compatibility but is no longer used.

    Returns (total, mapped) counts — `total` = distinct mapped unprocessedIds
    (the `from` values), `mapped` = how many resolve to a security with a known
    securityType.
    """
    # Distinct unprocessedId → securityId from the security-mappings catalog.
    from security_matcher import get_mapping_cache
    cache = get_mapping_cache()
    cache.ensure_loaded()                  # today's file → API → persist
    mapping = cache.as_dict()              # {unprocessedId: securityId}
    unprocessed_ids = set(mapping.keys())
    log.info("unprocessedId values (from security-mappings): %d", len(unprocessed_ids))

    # securityId → securityType via securities catalog
    oid_list = []
    for s in set(mapping.values()):
        try:
            oid_list.append(ObjectId(s))
        except Exception:
            pass

    sec_type_map = {}
    if oid_list:
        for doc in beehus_catalog.securities_by_ids(oid_list).values():
            sec_type_map[str(doc["_id"])] = doc.get("securityType", "")

    # Build rows – keep only mapped entries with a securityType
    rows = []
    for uid in sorted(unprocessed_ids):
        sid = mapping.get(uid, "")
        stype = sec_type_map.get(sid, "") if sid else ""
        if sid and stype:
            rows.append({
                "unprocessedId": uid,
                "securityId": sid,
                "securityType": stype,
            })

    from db import atomic_write_json
    atomic_write_json(JSON_PATH, rows)

    log.info("Wrote %d mapped rows to %s", len(rows), JSON_PATH)
    return len(unprocessed_ids), len(rows)


# ── 2. Classifier ────────────────────────────────────────────────────────────

def _tokenise(text):
    """
    Normalise and tokenise an unprocessedId string.

    Extracts meaningful tokens:
      - Words (alphabetic)
      - CNPJ-like patterns  (kept as-is so the model can learn issuer)
      - Numeric codes       (fund numbers, ticker suffixes)
    """
    text = text.upper()
    # Replace common separators with space
    text = re.sub(r"[/\-–—.,:;()]+", " ", text)
    tokens = text.split()
    return " ".join(tokens)


class SecurityTypeClassifier:
    """
    TF-IDF + Logistic Regression classifier for securityType prediction.

    Workflow:
        clf = SecurityTypeClassifier()
        clf.train()                         # loads JSON + fits model
        result = clf.predict("ABCB4 ...")   # -> {"type": "stockEtf", "confidence": 0.91}
    """

    def __init__(self, json_path=None):
        self._json_path = json_path or JSON_PATH
        self._vectoriser = None
        self._model = None
        self._labels = []           # ordered class list
        self._trained = False

    # ── training ──────────────────────────────────────────────────────────

    def train(self):
        """Load the labelled JSON and fit the model.  Returns self."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        with open(self._json_path, encoding="utf-8") as f:
            rows = json.load(f)

        texts  = [_tokenise(r["unprocessedId"]) for r in rows]
        labels = [r["securityType"] for r in rows]

        self._vectoriser = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),      # unigrams + bigrams
            max_features=20_000,
            sublinear_tf=True,
        )
        X = self._vectoriser.fit_transform(texts)

        self._model = LogisticRegression(
            max_iter=1000,
            solver="lbfgs",
            C=5.0,
        )
        self._model.fit(X, labels)
        self._labels = list(self._model.classes_)
        self._trained = True
        log.info("Classifier trained on %d samples, %d classes", len(rows), len(self._labels))
        return self

    # ── prediction ────────────────────────────────────────────────────────

    def predict(self, unprocessed_id):
        """
        Predict securityType for a single unprocessedId string.

        Returns {"type": str, "confidence": float, "top3": [(type, conf), ...]}
        """
        if not self._trained:
            raise RuntimeError("Classifier not trained – call .train() first.")

        vec = self._vectoriser.transform([_tokenise(unprocessed_id)])
        proba = self._model.predict_proba(vec)[0]
        ranked = sorted(zip(self._labels, proba), key=lambda x: -x[1])

        return {
            "type":       ranked[0][0],
            "confidence": round(float(ranked[0][1]), 4),
            "top3":       [(t, round(float(c), 4)) for t, c in ranked[:3]],
        }

    def predict_batch(self, ids):
        """Predict for a list of unprocessedId strings.  Returns list of dicts."""
        if not self._trained:
            raise RuntimeError("Classifier not trained – call .train() first.")

        vecs = self._vectoriser.transform([_tokenise(uid) for uid in ids])
        probas = self._model.predict_proba(vecs)
        results = []
        for uid, proba in zip(ids, probas):
            ranked = sorted(zip(self._labels, proba), key=lambda x: -x[1])
            results.append({
                "unprocessedId": uid,
                "type":          ranked[0][0],
                "confidence":    round(float(ranked[0][1]), 4),
                "top3":          [(t, round(float(c), 4)) for t, c in ranked[:3]],
            })
        return results

    # ── evaluation ────────────────────────────────────────────────────────

    def evaluate(self):
        """
        5-fold cross-validation on the labelled data.
        Returns {"accuracy": float, "per_class": {type: {"precision", "recall", "f1"}}}.
        """
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_predict
        from sklearn.metrics import classification_report

        with open(self._json_path, encoding="utf-8") as f:
            rows = json.load(f)

        texts  = [_tokenise(r["unprocessedId"]) for r in rows]
        labels = [r["securityType"] for r in rows]

        vec = TfidfVectorizer(
            analyzer="word", ngram_range=(1, 2),
            max_features=20_000, sublinear_tf=True,
        )
        X = vec.fit_transform(texts)

        model = LogisticRegression(max_iter=1000, solver="lbfgs", C=5.0)
        preds = cross_val_predict(model, X, labels, cv=5)

        report = classification_report(labels, preds, output_dict=True, zero_division=0)
        accuracy = report.pop("accuracy")
        per_class = {
            k: {m: round(v, 4) for m, v in metrics.items() if m != "support"}
            for k, v in report.items()
            if k not in ("macro avg", "weighted avg")
            for metrics in [v]
        }
        return {"accuracy": round(accuracy, 4), "per_class": per_class}


# ── Quick CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if "--rebuild" in sys.argv:
        sys.path.insert(0, os.path.dirname(__file__))
        total, mapped = rebuild_mapping()
        print(f"Rebuild done: {total} mapped unprocessedIds, {mapped} with securityType.")

    if "--eval" in sys.argv:
        clf = SecurityTypeClassifier()
        clf.train()
        result = clf.evaluate()
        print(f"\n5-fold CV accuracy: {result['accuracy']:.2%}\n")
        print(f"{'securityType':35s} {'precision':>10s} {'recall':>10s} {'f1':>10s}")
        print("-" * 67)
        for cls, m in sorted(result["per_class"].items()):
            print(f"{cls:35s} {m['precision']:10.2%} {m['recall']:10.2%} {m['f1-score']:10.2%}")

    if "--predict" in sys.argv:
        idx = sys.argv.index("--predict")
        test_ids = sys.argv[idx + 1:]
        if not test_ids:
            print("Usage: --predict 'SOME UNPROCESSED ID'")
        else:
            clf = SecurityTypeClassifier()
            clf.train()
            for uid in test_ids:
                r = clf.predict(uid)
                print(f"\n  Input:      {uid}")
                print(f"  Predicted:  {r['type']}  (confidence {r['confidence']:.1%})")
                print(f"  Top 3:      {r['top3']}")
