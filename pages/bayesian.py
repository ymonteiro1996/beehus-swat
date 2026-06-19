"""Bayesian optimization for conciliação diagnostics.

Extracts factors from the 6-step diagnostic output, assigns confidence
priors and impact distributions, then finds the flag combination whose
summed impact best closes the gap.
"""

import json
import logging
import math
import os
import random
from itertools import combinations

logger = logging.getLogger(__name__)

# ── Default configuration ─────────────────────────────────────────────────────

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "bayesian_config.json")

_MAX_FLAGS_CEILING = 15  # Hard ceiling — never enumerate more than 2^15 subsets

_DEFAULTS = {
    "tolerance": 0.01,
    "gaussian_sigma": 0.001,
    "confidence_overrides": {
        "MISSING_TRANSACTION":     0.95,
        "MISSING_PROVISION":       0.90,
        "MISSING_EVENT":           0.85,
        "WRONG_EVENT_BALANCE":     0.85,
        "WRONG_PROVISION_AMOUNT":  0.80,
        "MISSING_EXECUTION_PRICE": 0.70,
        "WITHHOLDING_TAX":         0.75,
        "WRONG_TRANSACTION_VALUE": 0.65,
        "UNCLASSIFIED_TRANSACTION": 0.85,
        "CASH_MISMATCH":           0.90,
        "MISCLASSIFIED":           0.40,
        "ANOMALY":                 0.20,
        "WRONG_SECURITY":          0.30,
        "DATA_QUALITY_ERROR":      0.99,
    },
    "data_quality_confidence": 0.99,
    "monte_carlo_samples": 200,
    "exec_price_margin": 0.05,
    "withholding_tax_rates": {"15.0": 0.6, "22.5": 0.4},
    "wrong_txn_relative_error": 0.02,
}


def _load_config():
    cfg = dict(_DEFAULTS)
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("bayesian_config.json invalid: %s — using defaults", e)
            return cfg
        for k, v in user.items():
            if k == "confidence_overrides" and isinstance(v, dict):
                cfg["confidence_overrides"] = {**cfg["confidence_overrides"], **v}
            else:
                cfg[k] = v
    return cfg


# ── Factor extraction ─────────────────────────────────────────────────────────

def extract_factors(diag: dict, cfg: dict = None) -> dict:
    """Extract Bayesian factors from a /api/conciliacao/diagnose response.

    Returns a dict with:
      - gap_cash: the absolute gap in BRL
      - flags: list of {flag, impact, securityId, securityName, tier, confidence,
                        distribution, provisionData?}
      Each flag is one "candidate fix" for the Bayesian engine.
    """
    if cfg is None:
        cfg = _load_config()
    confidences = cfg["confidence_overrides"]

    gap_cash = diag.get("step1", {}).get("gapCash")
    if gap_cash is None:
        logger.warning("extract_factors: no gapCash in step1")
        return {"gap_cash": 0, "flags": [], "error": "no gapCash in step1"}

    flags = []
    flags.extend(_extract_step3_flags(diag, gap_cash, confidences, cfg))
    flags.extend(_extract_step4_flags(diag, gap_cash, confidences))
    flags.extend(_extract_step5_flags(diag, gap_cash, confidences))
    flags.extend(_extract_step6_flags(diag, confidences))

    return {
        "gap_cash": gap_cash,
        "flags":    flags,
    }


def _check_data_quality(suspect, former_nav):
    """Check a suspect security for data quality issues.

    Returns a list of issue dicts (may be empty). Each issue has:
      - check: which check fired
      - detail: human-readable explanation
      - estimatedCorrectQty: what the quantity probably should be
    """
    issues = []
    if not suspect:
        return issues

    fq = suspect.get("formerQuantity")
    q  = suspect.get("quantity")
    if not fq or not q or fq == 0:
        return issues

    ratio = abs(q / fq)

    # Check 1: Decimal shift — digits of formerQuantity (without decimal) appear
    # as prefix of quantity (without decimals). Classic BR comma misinterpretation.
    if ratio > 1000:
        fq_digits = str(fq).replace(".", "").rstrip("0")
        q_digits  = str(int(abs(q)))
        if q_digits.startswith(fq_digits) and len(fq_digits) >= 3:
            issues.append({
                "check": "decimal_shift",
                "detail": f"Provavel erro de virgula: quantity {q:,.0f} parece ser {fq:,.5f} "
                          f"(mesmos digitos '{fq_digits}' sem o ponto decimal)",
                "estimatedCorrectQty": fq,
                "ratio": ratio,
            })

    # Check 2: Absurd quantity ratio without decimal shift pattern
    if ratio > 10000 and not issues:
        issues.append({
            "check": "absurd_ratio",
            "detail": f"Quantidade mudou {ratio:,.0f}x em um dia "
                      f"({fq:,.2f} -> {q:,.0f})",
            "estimatedCorrectQty": fq,
            "ratio": ratio,
        })

    # Check 3: Impact vs NAV sanity
    if former_nav and former_nav > 0:
        impact = abs(suspect.get("amountDiff", 0) or 0) * abs(suspect.get("pu") or suspect.get("executionPrice") or 0)
        if impact > 0 and impact / former_nav > 100:
            # Only add if not already covered by decimal shift
            if not any(i["check"] == "decimal_shift" for i in issues):
                issues.append({
                    "check": "impact_vs_nav",
                    "detail": f"Impacto R$ {impact:,.2f} e {impact/former_nav:,.0f}x o NAV "
                              f"(R$ {former_nav:,.2f})",
                    "ratio": impact / former_nav,
                })

    return issues


def _extract_step3_flags(diag, gap_cash, confidences, cfg):
    """Security-level flags from step 3.

    Also checks each security for data quality issues using suspect data
    from step 2. If found, emits an additional DATA_QUALITY_ERROR flag
    alongside the original flag (no suppression).
    """
    flags = []

    # Build suspect lookup from step2
    suspect_map = {}
    for s in diag.get("step2", {}).get("suspects", []):
        sid = s.get("securityId", "")
        if sid:
            suspect_map[sid] = s

    former_nav = diag.get("step1", {}).get("formerNav", 0)
    dq_confidence = cfg.get("data_quality_confidence", 0.99)

    for sec in diag.get("step3", {}).get("securities", []):
        sid   = sec.get("securityId", "")
        sname = sec.get("name", "")

        # Check data quality for this security
        suspect = suspect_map.get(sid)
        dq_issues = _check_data_quality(suspect, former_nav)

        for sub_key in ("step3_1", "step3_2", "step3_3"):
            sub = sec.get(sub_key)
            if not sub or sub.get("status") != "flag":
                continue

            flag_type = sub.get("flag", "UNKNOWN")
            signed_impact = _compute_signed_impact(flag_type, sub, sec, gap_cash)

            conf = confidences.get(flag_type, 0.50)
            tier, dist = _classify_tier(flag_type, sub, sec, cfg)

            # Emit the original flag (unchanged confidence)
            entry = {
                "flag":         flag_type,
                "impact":       signed_impact,
                "absImpact":    abs(signed_impact),
                "securityId":   sid,
                "securityName": sname,
                "tier":         tier,
                "confidence":   conf,
                "distribution": dist,
            }
            if sub.get("provisionData"):
                entry["provisionData"] = sub["provisionData"]
            flags.append(entry)

            # Emit DATA_QUALITY_ERROR alongside if issues found
            if dq_issues:
                dq_detail = "; ".join(i["detail"] for i in dq_issues)
                dq_checks = [i["check"] for i in dq_issues]
                estimated_qty = dq_issues[0].get("estimatedCorrectQty")
                flags.append({
                    "flag":         "DATA_QUALITY_ERROR",
                    # 0 scoring impact: this flag EXPLAINS the same discrepancy the
                    # original flag above already accounts for — it is NOT an
                    # additional, independent gap. score_combinations sums
                    # f["impact"] over the full power set with no exclusivity
                    # check (and the MC path uses f["impact"] for tier-1 flags),
                    # so a non-zero value here let any combo containing BOTH
                    # flags double-count one discrepancy (2× signed_impact).
                    # absImpact stays non-zero so the flag remains in `actionable`
                    # and visible/selectable as a tier-1 data-quality annotation.
                    "impact":       0,
                    "absImpact":    abs(signed_impact),
                    "securityId":   sid,
                    "securityName": sname,
                    "tier":         1,
                    "confidence":   dq_confidence,
                    "distribution": {"type": "delta", "value": abs(signed_impact)},
                    "dataQuality":  {
                        "checks":              dq_checks,
                        "detail":              dq_detail,
                        "estimatedCorrectQty": estimated_qty,
                        "currentQuantity":     suspect.get("quantity") if suspect else None,
                        "formerQuantity":      suspect.get("formerQuantity") if suspect else None,
                        "originalFlag":        flag_type,
                    },
                })
                logger.info("DATA_QUALITY_ERROR for %s (%s): %s", sname, sid, dq_checks)

    return flags


def _extract_step4_flags(diag, gap_cash, confidences):
    """Transaction-level flags from step 4."""
    flags = []
    s4 = diag.get("step4", {})

    # 4.1 Unclassified transactions
    # These use raw balance — the sign reflects the actual cash flow direction
    # and should NOT be flipped to match gap direction.
    for txn in s4.get("unclassified", []):
        bal = float(txn.get("balance", 0) or 0)
        if bal:
            flags.append({
                "flag":         "UNCLASSIFIED_TRANSACTION",
                "impact":       bal,
                "absImpact":    abs(bal),
                "securityId":   txn.get("securityId", ""),
                "securityName": "",
                "tier":         1,
                "confidence":   confidences.get("UNCLASSIFIED_TRANSACTION", 0.85),
                "distribution": {"type": "delta", "value": bal},
            })

    # 4.2 Wrong security — raw balance (already signed from transactions)
    for ws in s4.get("wrongSecurity", []):
        if ws.get("verdict") == "WRONG_SECURITY":
            bal = float(ws.get("balance", 0) or 0)
            flags.append({
                "flag":         "WRONG_SECURITY",
                "impact":       bal,
                "absImpact":    abs(bal),
                "securityId":   ws.get("securityId", ""),
                "securityName": ws.get("securityName", ""),
                "tier":         3,
                "confidence":   confidences.get("WRONG_SECURITY", 0.30),
                "distribution": {"type": "delta", "value": bal},
            })

    # 4.3 Misclassified — raw balance (represents the existing transaction
    # that would be reclassified, so its sign is the actual cash flow).
    # We carry enough original-txn context to let the acceptance handler
    # emit a deletion row for the original alongside the replacement txn.
    for mc in s4.get("misclassified", []):
        bal = float(mc.get("txnBalance", 0) or 0)
        if not bal or not mc.get("txnId"):
            # Need txnId to emit a deletion marker; skip if absent (e.g.
            # the misclassified entry came from a pending correction).
            continue
        top_match = (mc.get("matches") or [{}])[0]
        flags.append({
            "flag":         "MISCLASSIFIED",
            "impact":       bal,
            "absImpact":    abs(bal),
            # `securityId` / `securityName` = the TARGET security (where
            # the txn should have been classified). This matches how
            # downstream acceptance code treats other flags.
            "securityId":   top_match.get("securityId", ""),
            "securityName": top_match.get("securityName", ""),
            "tier":         3,
            "confidence":   confidences.get("MISCLASSIFIED", 0.40),
            "distribution": {"type": "delta", "value": bal},
            # Original-txn context for deletion marker:
            "originalId":         str(mc.get("txnId")),
            "originalSecurityId": mc.get("txnSecurityId") or "",
            "originalBalance":    bal,
            "originalType":       mc.get("txnType") or "",
            "matchFlag":          top_match.get("flag", ""),
        })
    return flags


def _extract_step5_flags(diag, gap_cash, confidences):
    """Cash mismatch flag from step 5."""
    step5 = diag.get("step5", {})
    cash_diff = step5.get("cashDiff")
    if cash_diff is None or round(cash_diff, 2) == 0:
        return []

    unclassified_count = len(diag.get("step4", {}).get("unclassified", []))
    conf = confidences.get("CASH_MISMATCH", 0.90)
    if unclassified_count > 1:
        conf = min(conf, 0.60)
    # cashDiff = projectedCash − currentCash.
    # Positive cashDiff → transactions overstated → fix is negative.
    signed_cash = round(-float(cash_diff), 2)
    return [{
        "flag":         "CASH_MISMATCH",
        "impact":       signed_cash,
        "absImpact":    abs(signed_cash),
        "securityId":   "",
        "securityName": "(caixa)",
        "tier":         1 if unclassified_count <= 1 else 2,
        "confidence":   conf,
        "distribution": {"type": "delta", "value": signed_cash},
    }]


def _extract_step6_flags(diag, confidences):
    """Anomaly indicators from step 6 (no direct impact)."""
    flags = []
    for anom in diag.get("step6", {}).get("securityAnomalies", []):
        if anom.get("isAnomaly"):
            mean   = anom.get("mean", 0)
            stddev = anom.get("stdDev", 1) or 1
            ret    = anom.get("currentReturn", 0)
            z_score = abs(ret - mean) / stddev
            flags.append({
                "flag":         "ANOMALY",
                "impact":       0,
                "absImpact":    0,
                "securityId":   anom.get("securityId", ""),
                "securityName": anom.get("securityName", ""),
                "tier":         3,
                "confidence":   confidences.get("ANOMALY", 0.20),
                "distribution": {"type": "indicator", "zScore": round(z_score, 2)},
            })
    return flags


# ── Sign helpers ──────────────────────────────────────────────────────────────

def _sign_for_gap(value, gap_cash):
    """Return abs(value) with sign matching gap_cash direction."""
    if gap_cash == 0:
        return 0.0
    return abs(value) if gap_cash > 0 else -abs(value)


def _compute_signed_impact(flag_type, sub, sec, gap_cash):
    """Compute the signed impact so that Σ(impacts) ≈ gapCash closes the gap.

    residual = gapCash − Σ(impact_i)  →  we want residual ≈ 0.
    """
    raw = abs(sub.get("impact", 0) or 0)

    # Zero gap → no impact needed
    if gap_cash == 0:
        return 0.0

    # ── Tier 2: compute precise impact from parameters ────────────────────────

    if flag_type == "MISSING_EXECUTION_PRICE":
        pu        = sub.get("pu", 0) or 0
        true_exec = sub.get("expectedExecPrice") or pu
        amt_diff  = sec.get("amountDiff", 0) or 0
        computed  = round(amt_diff * (pu - true_exec), 2)
        # Fallback to raw if amountDiff absent
        if computed == 0 and raw != 0:
            return _sign_for_gap(raw, gap_cash)
        return computed

    if flag_type == "WITHHOLDING_TAX":
        actual   = sub.get("actualBalance", 0) or 0
        expected = sub.get("expectedValue", 0) or 0
        return round(actual - expected, 2)

    if flag_type == "WRONG_TRANSACTION_VALUE":
        actual   = sub.get("actualBalance", 0) or 0
        expected = sub.get("expectedValue", 0) or 0
        return round(actual - expected, 2)

    if flag_type == "WRONG_PROVISION_AMOUNT":
        # expectedEventCash − provisionAmount (from step3_2 sub-step)
        expected_event = sub.get("expectedEventCash", 0) or 0
        prov_amount    = sub.get("provisionAmount", 0) or 0
        if expected_event and prov_amount:
            return round(expected_event - prov_amount, 2)
        # Fallback to gap-signed raw
        return _sign_for_gap(raw, gap_cash)

    if flag_type == "WRONG_EVENT_BALANCE":
        expected_event = sub.get("expectedEventCash", 0) or 0
        event_total    = sub.get("eventTransactionTotal", 0) or 0
        if expected_event and event_total:
            return round(expected_event - event_total, 2)
        return _sign_for_gap(raw, gap_cash)

    # ── Tier 1: deterministic — sign matches gap ──────────────────────────────
    return _sign_for_gap(raw, gap_cash)


def _classify_tier(flag_type, sub, sec, cfg):
    """Return (tier, distribution_dict) for a flag."""

    # Tier 1 — deterministic
    if flag_type in ("MISSING_TRANSACTION", "MISSING_PROVISION", "MISSING_EVENT",
                     "WRONG_EVENT_BALANCE", "WRONG_PROVISION_AMOUNT"):
        return 1, {"type": "delta", "value": sub.get("impact", 0)}

    # Tier 2 — bounded
    if flag_type == "MISSING_EXECUTION_PRICE":
        pu       = sub.get("pu", 0) or 0
        former   = sec.get("formerPu") or pu
        margin   = cfg.get("exec_price_margin", 0.05)
        low      = min(pu, former) * (1 - margin)
        high     = max(pu, former) * (1 + margin)
        amt_diff = sec.get("amountDiff", 0) or 0
        return 2, {
            "type": "uniform",
            "param": "executionPrice",
            "low":  round(low, 6),
            "high": round(high, 6),
            # Keep amountDiff and pu as first-class fields; the Monte-Carlo
            # sampler reads them directly. The impactFormula string is
            # human-readable only — do not parse it back.
            "amountDiff": amt_diff,
            "pu":         pu,
            "impactFormula": f"amountDiff({amt_diff}) * (PU - trueExecPrice)",
        }

    if flag_type == "WITHHOLDING_TAX":
        rates = cfg.get("withholding_tax_rates", {"15.0": 0.6, "22.5": 0.4})
        return 2, {
            "type":    "discrete",
            "param":   "taxRate",
            "options":  {k: v for k, v in rates.items()},
            "impactFormula": "transactionBalance * (rate / 100)",
        }

    if flag_type == "WRONG_TRANSACTION_VALUE":
        expected = sub.get("expectedValue", 0) or 0
        actual   = sub.get("actualBalance", 0) or 0
        rel_err  = cfg.get("wrong_txn_relative_error", 0.02)
        sigma    = abs(expected) * rel_err
        return 2, {
            "type":  "gaussian",
            "mean":  round(expected - actual, 2),
            "sigma": round(sigma, 2),
        }

    # Tier 3 — default
    return 3, {"type": "delta", "value": sub.get("impact", 0)}


# ── Bayesian scoring ──────────────────────────────────────────────────────────

def score_combinations(factors: dict, cfg: dict = None,
                       max_flags: int = 10, top_k: int = 5) -> dict:
    """Run the Bayesian power-set enumeration.

    Returns top-K fix combinations ranked by posterior probability.
    Hard ceiling of 15 flags to prevent combinatorial explosion.
    """
    if cfg is None:
        cfg = _load_config()
    gap_cash  = factors.get("gap_cash", 0)
    all_flags = factors.get("flags", [])
    sigma     = cfg.get("gaussian_sigma", 0.001)
    tolerance = cfg.get("tolerance", 0.01)

    # Hard ceiling on max_flags
    max_flags = min(max_flags, _MAX_FLAGS_CEILING)

    # Filter out purely indicative flags (zero impact)
    actionable = [f for f in all_flags if f["absImpact"] > 0]

    # Cap at max_flags to keep power set manageable
    if len(actionable) > max_flags:
        actionable.sort(key=lambda f: (-f["confidence"], -f["absImpact"]))
        actionable = actionable[:max_flags]

    n = len(actionable)

    # effective_sigma with floor to prevent collapsed posteriors on small gaps
    if gap_cash != 0 and sigma > 0:
        effective_sigma = max(abs(gap_cash) * sigma, tolerance)
    else:
        effective_sigma = max(tolerance, 1.0)

    def _likelihood(residual):
        if sigma > 0:
            return math.exp(-0.5 * (residual / effective_sigma) ** 2)
        return 1.0 if abs(residual) < tolerance else 0.0

    # Compute no-fix scenario FIRST (before normalization)
    no_fix_prior = 1.0
    for f in actionable:
        no_fix_prior *= (1.0 - f["confidence"])
    no_fix_score = no_fix_prior * _likelihood(gap_cash)

    # Enumerate all non-empty subsets
    scored = []
    for size in range(1, n + 1):
        for combo in combinations(range(n), size):
            combo_flags = [actionable[i] for i in combo]
            excluded    = [actionable[i] for i in range(n) if i not in combo]

            total_impact = sum(f["impact"] for f in combo_flags)
            residual     = gap_cash - total_impact

            likelihood = _likelihood(residual)

            prior = 1.0
            for f in combo_flags:
                prior *= f["confidence"]
            for f in excluded:
                prior *= (1.0 - f["confidence"])

            raw_posterior = likelihood * prior

            gap_resolved = (abs(residual) <= abs(gap_cash) * tolerance
                           if gap_cash != 0 else abs(residual) <= tolerance)

            scored.append({
                "flags":        [f["flag"] for f in combo_flags],
                "flagDetails":  [
                    {"flag": f["flag"], "securityId": f["securityId"],
                     "securityName": f["securityName"], "impact": f["impact"],
                     "confidence": f["confidence"], "tier": f["tier"]}
                    for f in combo_flags
                ],
                "totalImpact":  round(total_impact, 2),
                "residualGap":  round(residual, 2),
                "likelihood":   round(likelihood, 8),
                "prior":        round(prior, 8),
                "posterior":    raw_posterior,
                "gapResolved":  gap_resolved,
            })

    # Normalize posteriors INCLUDING the no-fix scenario
    total_post = no_fix_score + sum(s["posterior"] for s in scored)
    if total_post > 0:
        for s in scored:
            s["posterior"] = round(s["posterior"] / total_post, 8)
        no_fix_normalized = round(no_fix_score / total_post, 8)
    else:
        no_fix_normalized = 1.0

    # Sort by posterior descending
    scored.sort(key=lambda s: -s["posterior"])

    best = scored[0] if scored else None

    return {
        "bestFix":        best,
        "alternatives":   scored[1:top_k] if len(scored) > 1 else [],
        "totalScenarios": len(scored),
        "noFixPosterior": no_fix_normalized,
        "indicativeFlags": [f for f in all_flags if f["absImpact"] == 0],
    }


# ── Summary factors (for dashboards / ML) ────────────────────────────────────

def compute_summary(diag: dict) -> dict:
    """Compute high-level numerical summary from diagnostic output."""
    s1 = diag.get("step1", {})
    s2 = diag.get("step2", {})
    s3 = diag.get("step3", {})
    s5 = diag.get("step5", {})
    s6 = diag.get("step6", {})

    former_nav = s1.get("formerNav") or 1
    gap_cash   = s1.get("gapCash") or 0

    flag_counts = {}
    total_impact = 0
    for sec in s3.get("securities", []):
        for key in ("step3_1", "step3_2", "step3_3"):
            sub = sec.get(key)
            if sub and sub.get("status") == "flag":
                ft = sub.get("flag", "UNKNOWN")
                flag_counts[ft] = flag_counts.get(ft, 0) + 1
                total_impact += abs(sub.get("impact", 0) or 0)

    z_scores = []
    for a in s6.get("securityAnomalies", []):
        m = a.get("mean", 0)
        sd = a.get("stdDev", 1) or 1
        z_scores.append(abs((a.get("currentReturn", 0) - m) / sd))

    suspect_count = s2.get("suspectCount", 0)
    eliminated_count = s2.get("eliminatedCount", 0)
    total_secs = suspect_count + eliminated_count

    abs_former_nav = abs(former_nav)

    return {
        # Step 1
        "gapPctAbs":       abs(s1.get("gapPct", 0)),
        "gapCashAbs":      abs(gap_cash),
        "gapToNavRatio":   round(abs(gap_cash) / abs_former_nav, 8) if abs_former_nav > 0 else 0,

        # Step 2
        "suspectCount":    suspect_count,
        "eliminatedCount": eliminated_count,
        "suspectRatio":    round(suspect_count / total_secs, 4) if total_secs else 0,

        # Step 3
        "flagCount":       sum(flag_counts.values()),
        "flagTypes":       flag_counts,
        "totalImpact":     round(total_impact, 2),
        "impactCoverage":  round(total_impact / abs(gap_cash), 4) if gap_cash else 0,

        # Step 4
        "unclassifiedTxns":  len(diag.get("step4", {}).get("unclassified", [])),
        "wrongSecurityCount": len([w for w in diag.get("step4", {}).get("wrongSecurity", [])
                                   if w.get("verdict") == "WRONG_SECURITY"]),
        "misclassifiedCount": len(diag.get("step4", {}).get("misclassified", [])),

        # Step 5
        "cashConsistent":  1 if s5.get("diagnosis") == "consistent" else 0,
        "cashDiffAbs":     abs(s5.get("cashDiff") or 0),

        # Step 6
        "walletAnomaly":       1 if (s6.get("walletAnomaly") or {}).get("isAnomaly") else 0,
        "securityAnomalyCount": len(s6.get("securityAnomalies", [])),
        "maxZScore":           round(max(z_scores), 2) if z_scores else 0,

        # Composite
        "complexityScore": _complexity_score(flag_counts, s5, s6),
    }


def _complexity_score(flag_counts, step5, step6):
    """Weighted count of distinct issue types — higher = harder to resolve."""
    weights = {
        "MISSING_TRANSACTION": 1, "MISSING_PROVISION": 1, "MISSING_EVENT": 2,
        "WRONG_EVENT_BALANCE": 2, "WRONG_PROVISION_AMOUNT": 2,
        "MISSING_EXECUTION_PRICE": 3, "WITHHOLDING_TAX": 3,
        "WRONG_TRANSACTION_VALUE": 3,
    }
    score = sum(weights.get(ft, 1) * count for ft, count in flag_counts.items())
    if step5.get("diagnosis") != "consistent":
        score += 2
    if any(a.get("isAnomaly") for a in step6.get("securityAnomalies", [])):
        score += 1
    return score


# ── Monte Carlo sampling for Tier 2 ──────────────────────────────────────────

def _sample_tier2_impact(flag, cfg):
    """Draw one random impact sample from a Tier 2 flag's distribution."""
    dist = flag.get("distribution", {})
    dtype = dist.get("type", "delta")

    if dtype == "uniform":
        # MISSING_EXECUTION_PRICE: impact = amountDiff × (PU − sampledExecPrice)
        low  = dist.get("low", 0)
        high = dist.get("high", 0)
        if low == high:
            return flag["impact"]
        sampled_price = random.uniform(low, high)
        # Read amountDiff/pu directly off the distribution; falling back to
        # parsing impactFormula here silently degraded to the deterministic
        # path whenever the human-readable string drifted.
        amt_diff = dist.get("amountDiff", 0) or 0
        pu = dist.get("pu")
        if pu is None:
            # Legacy distributions without explicit pu — keep the historical
            # estimate as a fallback rather than crashing.
            pu = high / (1 + cfg.get("exec_price_margin", 0.05))
        if amt_diff != 0:
            return round(amt_diff * (pu - sampled_price), 2)
        return flag["impact"]

    if dtype == "discrete":
        # WITHHOLDING_TAX: sample a tax rate from weighted options
        options = dist.get("options", {})
        if not options:
            return flag["impact"]
        rates  = [float(k) for k in options.keys()]
        weights = list(options.values())
        chosen = random.choices(rates, weights=weights, k=1)[0]
        # impact ≈ actual − expected, scale by rate ratio
        base_impact = flag["impact"]
        if base_impact == 0:
            return 0
        # The default impact used 100% of the diff; scale by chosen rate ratio
        # For WT: impact = txnBalance × (rate/100), base was computed with actual−expected
        # We approximate by scaling the base impact
        base_rate = 15.0  # typical fallback
        return round(base_impact * (chosen / base_rate), 2) if base_rate else base_impact

    if dtype == "gaussian":
        # WRONG_TRANSACTION_VALUE: sample from N(mean, sigma²)
        mean  = dist.get("mean", 0)
        sigma = dist.get("sigma", 0)
        if sigma == 0:
            return flag["impact"]
        return round(random.gauss(mean, sigma), 2)

    # delta / indicator — deterministic
    return flag["impact"]


def score_combinations_mc(factors: dict, cfg: dict = None,
                          max_flags: int = 10, top_k: int = 5) -> dict:
    """Score combinations with Monte Carlo sampling for Tier 2 uncertainty.

    Runs N iterations. Each iteration samples Tier 2 impacts, then runs
    the full power-set scoring. Posteriors are averaged across iterations.
    """
    if cfg is None:
        cfg = _load_config()

    n_samples = cfg.get("monte_carlo_samples", 200)
    all_flags = factors.get("flags", [])

    # Check if any Tier 2 flags exist — if not, fall back to deterministic
    has_tier2 = any(f["tier"] == 2 and f["absImpact"] > 0 for f in all_flags)
    if not has_tier2 or n_samples <= 1:
        return score_combinations(factors, cfg=cfg, max_flags=max_flags, top_k=top_k)

    gap_cash  = factors.get("gap_cash", 0)
    sigma     = cfg.get("gaussian_sigma", 0.001)
    tolerance = cfg.get("tolerance", 0.01)
    max_flags = min(max_flags, _MAX_FLAGS_CEILING)

    actionable = [f for f in all_flags if f["absImpact"] > 0]
    if len(actionable) > max_flags:
        actionable.sort(key=lambda f: (-f["confidence"], -f["absImpact"]))
        actionable = actionable[:max_flags]

    n = len(actionable)
    if n == 0:
        return score_combinations(factors, cfg=cfg, max_flags=max_flags, top_k=top_k)

    if gap_cash != 0 and sigma > 0:
        effective_sigma = max(abs(gap_cash) * sigma, tolerance)
    else:
        effective_sigma = max(tolerance, 1.0)

    def _likelihood(residual):
        if sigma > 0:
            return math.exp(-0.5 * (residual / effective_sigma) ** 2)
        return 1.0 if abs(residual) < tolerance else 0.0

    # Pre-compute all combos (indices only)
    all_combos = []
    for size in range(1, n + 1):
        for combo in combinations(range(n), size):
            all_combos.append(combo)

    # Accumulate posteriors across MC iterations
    # Key = combo tuple → accumulated raw posterior
    combo_accum = {combo: 0.0 for combo in all_combos}
    no_fix_accum = 0.0

    logger.info("Running %d Monte Carlo iterations over %d combos", n_samples, len(all_combos))

    for _ in range(n_samples):
        # Sample Tier 2 impacts for this iteration
        sampled_impacts = []
        for f in actionable:
            if f["tier"] == 2:
                sampled_impacts.append(_sample_tier2_impact(f, cfg))
            else:
                sampled_impacts.append(f["impact"])

        # No-fix scenario
        no_fix_prior = 1.0
        for f in actionable:
            no_fix_prior *= (1.0 - f["confidence"])
        no_fix_score = no_fix_prior * _likelihood(gap_cash)

        # Score each combo
        iteration_total = no_fix_score
        combo_scores = {}
        for combo in all_combos:
            total_impact = sum(sampled_impacts[i] for i in combo)
            residual = gap_cash - total_impact

            likelihood = _likelihood(residual)

            prior = 1.0
            for i in range(n):
                if i in combo:
                    prior *= actionable[i]["confidence"]
                else:
                    prior *= (1.0 - actionable[i]["confidence"])

            raw = likelihood * prior
            combo_scores[combo] = raw
            iteration_total += raw

        # Normalize this iteration and accumulate
        if iteration_total > 0:
            for combo in all_combos:
                combo_accum[combo] += combo_scores[combo] / iteration_total
            no_fix_accum += no_fix_score / iteration_total
        else:
            no_fix_accum += 1.0

    # Average across iterations
    scored = []
    for combo in all_combos:
        avg_posterior = combo_accum[combo] / n_samples
        combo_flags = [actionable[i] for i in combo]

        total_impact = sum(f["impact"] for f in combo_flags)  # deterministic for display
        residual = gap_cash - total_impact

        gap_resolved = (abs(residual) <= abs(gap_cash) * tolerance
                       if gap_cash != 0 else abs(residual) <= tolerance)

        scored.append({
            "flags":       [f["flag"] for f in combo_flags],
            "flagDetails": [
                {"flag": f["flag"], "securityId": f["securityId"],
                 "securityName": f["securityName"], "impact": f["impact"],
                 "confidence": f["confidence"], "tier": f["tier"]}
                for f in combo_flags
            ],
            "totalImpact":  round(total_impact, 2),
            "residualGap":  round(residual, 2),
            "posterior":    round(avg_posterior, 8),
            "gapResolved":  gap_resolved,
        })

    scored.sort(key=lambda s: -s["posterior"])
    best = scored[0] if scored else None

    return {
        "bestFix":         best,
        "alternatives":    scored[1:top_k] if len(scored) > 1 else [],
        "totalScenarios":  len(scored),
        "noFixPosterior":  round(no_fix_accum / n_samples, 8),
        "indicativeFlags": [f for f in all_flags if f["absImpact"] == 0],
        "monteCarlo":      {"samples": n_samples, "tier2Count": sum(1 for f in actionable if f["tier"] == 2)},
    }


# ── Validation against Recálculo formulas ─────────────────────────────────────

def validate_fix(diag: dict, fix: dict, cfg: dict = None) -> dict:
    """Validate a proposed fix by re-running GAP formulas from CONCILIACAO_RECALCULO.md.

    This is a pure computation — no DB access. It simulates applying the
    fix's flag impacts as corrections to the diagnostic data and checks if
    the resulting gapPct ≈ 0 and cash is consistent.

    Key insight: each flag impact is already computed so that Σ(impacts) ≈ gapCash.
    The gap = (returnNavPerShare − returnContribution) × formerNav.
    Flags split into two categories:

    - NAV-affecting (MISSING_PROVISION, MISSING_TRANSACTION, MISSING_EVENT):
      change both NAV and contribution by the same amount → net gap change = 0.
      Wait — they change NAV (via balance/provision) which changes navPerShare,
      AND they change contribution (missing txn = missing contribution).
      Both sides move by impact/formerNav → gap closes.

    - Contribution-only (WITHHOLDING_TAX, EXEC_PRICE, CASH_MISMATCH, etc.):
      only affect contribution side → gap closes by impact/formerNav.

    Formula: newGapCash = oldGapCash − totalImpact

    Args:
        diag: raw diagnostic JSON (from /api/conciliacao/diagnose)
        fix:  a scored combination dict (bestFix or alternative)
        cfg:  bayesian config (optional)

    Returns:
        dict with validation results: newGapPct, newGapCash, cashConsistent, valid
    """
    if cfg is None:
        cfg = _load_config()
    tolerance = cfg.get("tolerance", 0.01)

    s1 = diag.get("step1", {})
    s5 = diag.get("step5", {})

    former_nav       = s1.get("formerNav") or 0
    return_nav_ps    = s1.get("returnNavPerShare", 0) or 0
    return_contrib   = s1.get("returnContribution", 0) or 0
    gap_cash_orig    = s1.get("gapCash", 0) or 0
    current_cash     = s5.get("currentCash", 0) or 0
    former_cash      = s5.get("formerCash", 0) or 0
    total_txns       = s5.get("totalTransactions", 0) or 0

    if not former_nav:
        return {"valid": False, "reason": "formerNav is 0",
                "gapResolved": False, "cashConsistent": False,
                "newGapPct": None, "newGapCash": None,
                "newProjectedCash": None, "currentCash": current_cash}

    total_impact = fix.get("totalImpact", 0)

    # ── GAP validation ────────────────────────────────────────────────────────
    # The fix is designed so totalImpact ≈ gapCash.
    # newGapCash = gapCash − totalImpact
    new_gap_cash = round(gap_cash_orig - total_impact, 2)
    new_gap_pct  = new_gap_cash / former_nav if former_nav != 0 else 0

    # Recompute new returns for reporting
    # For contribution-only flags: returnNavPerShare stays, returnContribution changes
    # For NAV-affecting flags: both change by impact/formerNav
    # In both cases: newGapPct = oldGapPct − totalImpact/formerNav
    new_return_contrib = return_contrib + (total_impact / former_nav)
    new_return_nav_ps  = return_nav_ps  # stays same for contribution-only fixes

    # For NAV-affecting flags, returnNavPerShare also shifts
    nav_affecting_flags = {"MISSING_PROVISION", "MISSING_TRANSACTION", "MISSING_EVENT"}
    nav_impact = sum(fd["impact"] for fd in fix.get("flagDetails", [])
                     if fd["flag"] in nav_affecting_flags)
    if nav_impact != 0:
        new_return_nav_ps = return_nav_ps + (nav_impact / former_nav)

    gap_resolved = abs(new_gap_pct) <= tolerance

    # ── Cash validation (Formula 13) ──────────────────────────────────────────
    # Only flags that represent actual cash movements affect projected cash.
    # MISSING_TRANSACTION / MISSING_PROVISION affect NAV (position) but may not
    # involve actual cash movement (e.g. position quantity changed but no cash
    # flow occurred yet). Only include them if cash was already inconsistent.
    orig_cash_consistent = (s5.get("diagnosis") == "consistent")

    # These flags always affect cash (they represent missing/wrong cash flows)
    cash_always_flags = {"CASH_MISMATCH", "UNCLASSIFIED_TRANSACTION",
                        "WITHHOLDING_TAX", "WRONG_TRANSACTION_VALUE"}
    # These flags affect cash only if cash was already broken
    # Note: MISCLASSIFIED reclassifies existing txns — no net cash change.
    cash_conditional_flags = {"MISSING_TRANSACTION"}

    cash_fix = 0
    for fd in fix.get("flagDetails", []):
        if fd["flag"] in cash_always_flags:
            cash_fix += fd["impact"]
        elif fd["flag"] in cash_conditional_flags and not orig_cash_consistent:
            cash_fix += fd["impact"]

    new_projected_cash = round(former_cash + total_txns + cash_fix, 2)
    cash_consistent = abs(new_projected_cash - current_cash) <= max(abs(current_cash) * tolerance, 0.01)

    # If cash was already consistent and no cash-affecting flags are in the fix,
    # cash is trivially still consistent
    if orig_cash_consistent and cash_fix == 0:
        cash_consistent = True

    return {
        "valid":            gap_resolved and cash_consistent,
        "gapResolved":      gap_resolved,
        "cashConsistent":   cash_consistent,
        "newGapPct":        round(new_gap_pct, 10),
        "newGapCash":       new_gap_cash,
        "newReturnNavPS":   round(new_return_nav_ps, 10),
        "newReturnContrib": round(new_return_contrib, 10),
        "newProjectedCash": new_projected_cash,
        "currentCash":      current_cash,
    }


def _build_all_flags_scenario(actionable):
    """Build a scenario dict that includes ALL actionable flags."""
    total_impact = sum(f["impact"] for f in actionable)
    return {
        "flags":       [f["flag"] for f in actionable],
        "flagDetails": [
            {"flag": f["flag"], "securityId": f["securityId"],
             "securityName": f["securityName"], "impact": f["impact"],
             "confidence": f["confidence"], "tier": f["tier"]}
            for f in actionable
        ],
        "totalImpact":  round(total_impact, 2),
        "residualGap":  None,
        "posterior":    None,
        "gapResolved":  None,
    }


def validate_multi_correction(diag: dict, fix: dict, cfg: dict = None) -> dict:
    """Validate a multi-correction scenario.

    In multi-correction mode, Σ(impacts) ≠ gapCash because the gap formula
    is non-linear across multiple securities. Instead, we validate:

    1. Each flag individually is a real problem (high confidence, Tier 1/2)
    2. The weighted confidence of all flags together is above threshold
    3. Cash is internally consistent after cash-affecting corrections
    4. The set covers all suspect securities (no suspect left unaddressed)

    This is a qualitative validation — not a precise Recálculo.
    """
    if cfg is None:
        cfg = _load_config()
    tolerance = cfg.get("tolerance", 0.01)

    s1 = diag.get("step1", {})
    s5 = diag.get("step5", {})
    former_nav = s1.get("formerNav") or 1
    current_cash = s5.get("currentCash", 0) or 0
    former_cash = s5.get("formerCash", 0) or 0
    total_txns = s5.get("totalTransactions", 0) or 0

    details = fix.get("flagDetails", [])

    # 1. Individual flag quality
    per_flag = []
    min_confidence = 1.0
    for fd in details:
        conf = fd.get("confidence", 0)
        tier = fd.get("tier", 3)
        min_confidence = min(min_confidence, conf)
        per_flag.append({
            "flag": fd["flag"],
            "securityName": fd.get("securityName", ""),
            "confidence": conf,
            "tier": tier,
            "quality": "high" if conf >= 0.8 else "medium" if conf >= 0.5 else "low",
        })

    # 2. Combined confidence: geometric mean
    if details:
        combined_conf = 1.0
        for fd in details:
            combined_conf *= fd.get("confidence", 0.5)
        combined_conf = combined_conf ** (1.0 / len(details))
    else:
        combined_conf = 0

    # 3. Cash validation
    # In multi-correction mode, we're applying ALL corrections at once.
    # MISSING_TRANSACTION creates NEW transactions that don't exist yet —
    # they don't affect the current cash projection. Only flags that fix
    # EXISTING cash flows are relevant here.
    # CASH_MISMATCH is the dedicated cash correction.
    orig_cash_consistent = (s5.get("diagnosis") == "consistent")
    cash_always_flags = {"CASH_MISMATCH", "UNCLASSIFIED_TRANSACTION",
                        "WITHHOLDING_TAX", "WRONG_TRANSACTION_VALUE"}

    cash_fix = 0
    for fd in details:
        if fd["flag"] in cash_always_flags:
            cash_fix += fd["impact"]

    new_projected_cash = round(former_cash + total_txns + cash_fix, 2)
    cash_consistent = abs(new_projected_cash - current_cash) <= max(abs(current_cash) * tolerance, 0.01)
    if orig_cash_consistent and cash_fix == 0:
        cash_consistent = True

    # 4. Suspect coverage — check all step2 suspects have a flag
    suspects = diag.get("step2", {}).get("suspects", [])
    suspect_sids = {s.get("securityId", "") for s in suspects}
    addressed_sids = {fd.get("securityId", "") for fd in details if fd.get("securityId")}
    unaddressed = suspect_sids - addressed_sids - {""}
    coverage_complete = len(unaddressed) == 0

    # Valid if: all flags are at least medium confidence AND cash is OK AND coverage complete
    all_quality_ok = all(pf["quality"] in ("high", "medium") for pf in per_flag)
    valid = all_quality_ok and cash_consistent and coverage_complete

    return {
        "valid":              valid,
        "mode":               "multi-correction",
        "gapResolved":        valid,  # in multi-correction, "resolved" = all corrections are real
        "cashConsistent":     cash_consistent,
        "combinedConfidence": round(combined_conf, 4),
        "minConfidence":      round(min_confidence, 4),
        "flagCount":          len(details),
        "perFlag":            per_flag,
        "coverageComplete":   coverage_complete,
        "unaddressedSuspects": list(unaddressed),
        "newProjectedCash":   new_projected_cash,
        "currentCash":        current_cash,
    }


def _is_multi_correction(factors, result, cfg):
    """Detect when multi-correction mode should activate.

    Triggers when:
    1. impactCoverage > 1.5 (total flags >> gap), AND
    2. Best posterior is near zero (no subset closes the gap), AND
    3. There are 3+ actionable flags
    """
    gap_cash = factors.get("gap_cash", 0)
    if gap_cash == 0:
        return False

    actionable = [f for f in factors.get("flags", []) if f["absImpact"] > 0]
    if len(actionable) < 3:
        return False

    total_impact = sum(f["absImpact"] for f in actionable)
    coverage = total_impact / abs(gap_cash)
    if coverage < 1.5:
        return False

    best = result.get("bestFix")
    if best and best.get("posterior", 0) > 0.01:
        return False  # a subset works — no need for multi-correction

    return True


def optimize_with_validation(diag: dict, cfg: dict = None,
                             max_flags: int = 10, top_k: int = 5) -> dict:
    """Full pipeline: extract factors → score (with MC) → validate → fallback.

    Includes multi-correction mode: when no subset of flags closes the gap
    (all posteriors ≈ 0%) but multiple flags are clearly real, the system
    proposes ALL flags as corrections and validates with the Recálculo.

    Returns the same structure as score_combinations, plus:
    - 'multiCorrection': True if multi-correction mode was used
    - 'validation' key on bestFix and alternatives
    """
    if cfg is None:
        cfg = _load_config()

    factors = extract_factors(diag, cfg=cfg)
    result  = score_combinations_mc(factors, cfg=cfg, max_flags=max_flags, top_k=top_k)

    # ── Check for multi-correction mode ───────────────────────────────────────
    if _is_multi_correction(factors, result, cfg):
        actionable = [f for f in factors.get("flags", []) if f["absImpact"] > 0]
        logger.info("Multi-correction mode: %d flags, coverage=%.1fx, no subset closes gap",
                    len(actionable),
                    sum(f["absImpact"] for f in actionable) / abs(factors["gap_cash"]))

        all_fix = _build_all_flags_scenario(actionable)
        v = validate_multi_correction(diag, all_fix, cfg=cfg)
        all_fix["validation"] = v
        all_fix["gapResolved"] = v.get("gapResolved", False)

        # Also try subsets excluding one flag at a time (leave-one-out)
        alternatives = []
        for skip_idx in range(len(actionable)):
            subset = [f for i, f in enumerate(actionable) if i != skip_idx]
            sub_fix = _build_all_flags_scenario(subset)
            sub_v = validate_multi_correction(diag, sub_fix, cfg=cfg)
            sub_fix["validation"] = sub_v
            sub_fix["gapResolved"] = sub_v.get("gapResolved", False)
            sub_fix["excluded"] = actionable[skip_idx]["flag"] + " (" + actionable[skip_idx]["securityName"] + ")"
            alternatives.append(sub_fix)

        # Sort alternatives: validated first, then by absolute residual
        alternatives.sort(key=lambda a: (
            0 if a["validation"].get("valid") else 1,
            abs(a.get("residualGap") or 999999)
        ))

        return {
            "bestFix":          all_fix,
            "alternatives":     alternatives[:top_k],
            "totalScenarios":   result["totalScenarios"],
            "noFixPosterior":   result["noFixPosterior"],
            "indicativeFlags":  result["indicativeFlags"],
            "monteCarlo":       result.get("monteCarlo"),
            "validationUsed":   True,
            "multiCorrection":  True,
            "closingTransaction": _build_closing_transaction(diag),
        }

    # ── Standard mode: validate bestFix and alternatives ──────────────────────
    gap_cash = factors.get("gap_cash", 0)
    tolerance = cfg.get("tolerance", 0.01)
    candidates = []
    if result["bestFix"]:
        candidates.append(result["bestFix"])
    candidates.extend(result.get("alternatives", []))

    validated_best = None
    validated_alts = []

    for scenario in candidates:
        v = validate_fix(diag, scenario, cfg=cfg)
        scenario["validation"] = v

        if v["valid"] and validated_best is None:
            validated_best = scenario
            logger.info("Validated fix: %s (gapPct=%.8f, cash=%s)",
                       scenario["flags"], v["newGapPct"], v["cashConsistent"])
        else:
            validated_alts.append(scenario)
            if not v["valid"]:
                logger.info("Fix %s failed validation: gapResolved=%s cashConsistent=%s gapPct=%.8f",
                           scenario["flags"], v["gapResolved"], v["cashConsistent"], v["newGapPct"] or 0)

    # If no validated fix, fall back to the highest-posterior regardless
    if validated_best is None and candidates:
        validated_best = candidates[0]
        validated_alts = candidates[1:]
        logger.warning("No fix passed validation — using highest-posterior as fallback")

    # Include closing transaction if bestFix has residual gap
    closing_txn = None
    if validated_best:
        v = validated_best.get("validation", {})
        residual = v.get("newGapCash") or validated_best.get("residualGap")
        if residual and abs(residual) > abs(gap_cash) * tolerance:
            closing_txn = _build_closing_transaction(diag, residual)

    return {
        "bestFix":              validated_best,
        "alternatives":         validated_alts[:top_k - 1],
        "totalScenarios":       result["totalScenarios"],
        "noFixPosterior":       result["noFixPosterior"],
        "indicativeFlags":      result["indicativeFlags"],
        "monteCarlo":           result.get("monteCarlo"),
        "validationUsed":       True,
        "multiCorrection":      False,
        "closingTransaction":   closing_txn,
    }


def _build_closing_transaction(diag, residual_gap=None):
    """Build a withdrawalDeposit transaction that closes the residual gap.

    When a best-fix correction has been identified, the closing WD should
    only cover the residual gap (gapCash − bestFix.totalImpact), not the
    full gapCash.  If no residual is provided, falls back to the full gap.

    Math: WD enters inAndOutFlows (Formula 2), reducing returnNavPerShare
    without affecting returnContribution. When WD = residual, gap → 0.
    """
    s1 = diag.get("step1", {})
    gap_cash = s1.get("gapCash", 0)
    wd_amount = residual_gap if residual_gap is not None else gap_cash

    if not wd_amount:
        return None

    return {
        "type": "withdrawalDeposit",
        "balance": round(wd_amount, 2),
        "description": "Ajuste de passivo — fecha gap residual de conciliação",
        "rationale": f"WD = gap residual ({round(wd_amount, 2):,.2f}). Entra em inAndOutFlows (F2), reduz returnNavPerShare sem alterar returnContribution.",
        "instruction": "Aplicar APÓS as correções da melhor combinação serem processadas.",
        "formula": f"WD = gapCash({gap_cash:,.2f}) − totalImpact({round(gap_cash - wd_amount, 2):,.2f}) = R$ {round(wd_amount, 2):,.2f}",
    }
