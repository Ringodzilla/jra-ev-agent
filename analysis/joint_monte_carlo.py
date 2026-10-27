"""Research-only paired-trial integration; callers supply every calibrated draw.

The empirical bootstrap samples whole race draws, trains no model, and confers
no production promotion or purchase status.
"""
from __future__ import annotations

import math
import random
from copy import deepcopy


def _vector(value, size: int, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{name}: one explicit value per horse is required")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f"{name}: finite numeric values required")
    return [float(v) for v in value]


def _summary(values: list[float]) -> dict:
    ordered = sorted(values)
    quantiles = {}
    for q in (0.05, 0.5, 0.95):
        position = (len(ordered) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        quantiles[str(q)] = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"mean": math.fsum(v / len(values) for v in values), "quantiles": quantiles}


def simulate_joint_trials(*, horse_ids: list[str], current_odds: list[float],
                          draws: list[dict], seed: int, iterations: int, manifest: dict) -> dict:
    """Bootstrap complete paired analyzer/environment/price race draws.

    Every draw must explicitly repeat the same ordered horse IDs and a unique
    trial_id. Market response is log(price/current price), never a probability
    input. ``base_probabilities`` are the analyzer's normalized first-stage
    probabilities; the second stage is softmax(log(p_base)+environment_logits).
    """
    if (not isinstance(horse_ids, list) or len(horse_ids) < 2
            or any(not isinstance(h, str) or not h.strip() for h in horse_ids)
            or len(set(horse_ids)) != len(horse_ids)):
        raise ValueError("unique ordered horse_ids for a complete race required")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must identify caller-generated draws as an integer")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if (not isinstance(manifest, dict) or manifest.get("purpose") != "research"
            or not isinstance(manifest.get("lineage"), dict) or not manifest["lineage"]):
        raise ValueError("research purpose and nonempty caller lineage manifest required")
    if not isinstance(draws, list) or not draws:
        raise ValueError("explicit paired draws required; no noise or neutral fallback is supplied")
    size = len(horse_ids)
    odds = _vector(current_odds, size, "current_odds")
    if any(o <= 1 for o in odds):
        raise ValueError("current decimal odds must exceed one")
    trials, seen = [], set()
    for draw in draws:
        if not isinstance(draw, dict):
            raise ValueError("each draw must be an explicit trial object")
        trial_id = draw.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id.strip() or trial_id in seen:
            raise ValueError("unique nonempty trial_id required")
        seen.add(trial_id)
        if draw.get("horse_ids") != horse_ids:
            raise ValueError("every trial must preserve the complete ordered horse set")
        base = _vector(draw.get("base_probabilities"), size, "base_probabilities")
        environment = _vector(draw.get("environment_logits"), size, "environment_logits")
        response = _vector(draw.get("market_log_odds_response"), size, "market_log_odds_response")
        if any(p <= 0 or p > 1 for p in base) or abs(math.fsum(base) - 1) > 1e-9:
            raise ValueError("base probabilities must be strictly positive and sum to one")
        logits = [math.log(p) + e for p, e in zip(base, environment)]
        peak = max(logits)
        weights = [math.exp(v - peak) for v in logits]
        total = math.fsum(weights)
        final = [v / total for v in weights]
        try:
            prices = [o * math.exp(r) for o, r in zip(odds, response)]
        except OverflowError as exc:
            raise ValueError("market response exceeds finite price range") from exc
        if any(not math.isfinite(o) or o <= 1 for o in prices):
            raise ValueError("trial prices must be finite decimal odds exceeding one")
        ev = [p * o for p, o in zip(final, prices)]
        trials.append({"trial_id": trial_id, "horse_ids": list(horse_ids),
                       "base_probabilities": base, "environment_logits": environment,
                       "market_log_odds_response": response, "win_probabilities": final,
                       "odds": prices, "ev": ev})
    source_trials = trials
    rng = random.Random(seed)
    trials = [{**deepcopy(source_trials[index]), "source_trial_id": source_trials[index]["trial_id"],
               "trial_id": str(number)} for number, index in enumerate(
                   rng.randrange(len(source_trials)) for _ in range(iterations))]
    horses = [{"horse_id": horse, "win_probability": _summary([t["win_probabilities"][i] for t in trials]),
               "odds": _summary([t["odds"][i] for t in trials]),
               "ev": _summary([t["ev"][i] for t in trials])} for i, horse in enumerate(horse_ids)]
    return {"kind": "RESEARCH_PAIRED_TRIAL_INTEGRATION_NOT_PRODUCTION_PREDICTION",
            "seed": seed, "seed_usage": "uniform_whole_race_empirical_bootstrap",
            "manifest": deepcopy(manifest), "horse_ids": list(horse_ids),
            "current_odds": odds, "source_draw_count": len(source_trials),
            "trial_count": len(trials), "trials": trials, "horses": horses,
            "win_probability_rank": [h["horse_id"] for h in sorted(horses, key=lambda h: -h["win_probability"]["mean"])],
            "ev_rank": [h["horse_id"] for h in sorted(horses, key=lambda h: -h["ev"]["mean"])],
            "limitations": ["Caller lineage is recorded, not independently authenticated or promoted.",
                            "Joint draw validity and calibration belong to the supplying roles.",
                            "Mean EV is the mean of paired trial products, not a product of means."]}
