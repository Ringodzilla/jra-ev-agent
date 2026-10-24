from __future__ import annotations

import math
import random
from hashlib import blake2b
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class ModelWeights:
    # Structural probability must be independent from the betting market.  The
    # five weights are the former non-market weights normalized to sum to one.
    ability: float = 0.4772727273
    course: float = 0.1590909091
    pace: float = 0.1818181818
    weight: float = 0.0909090909
    jockey: float = 0.0909090909
    market: float = 0.0
    temperature: float = 1.15
    market_shrink: float = 0.0
    monte_carlo_iterations: int = 2000
    monte_carlo_seed: int = 731
    luck_score_std: float = 0.16
    consistency_noise_scale: float = 0.75


def estimate_win_probs(rows: list[dict], weights: ModelWeights | None = None) -> list[dict]:
    weights = weights or ModelWeights()
    by_race: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_race[str(row["race_id"])].append(row)

    out: list[dict] = []
    for race_id, group in by_race.items():
        scored_group = [_score_row(row, weights) for row in group]
        market_probs = _market_probs(scored_group)
        blended_probs, probability_stds, calibration = _monte_carlo_win_probs(
            scored_group,
            market_probs,
            weights,
            race_id=race_id,
        )
        blended_probs = _apply_environment_offsets(scored_group, blended_probs)

        race_rows: list[dict] = []
        for row, prob, probability_std, meta in zip(scored_group, blended_probs, probability_stds, calibration):
            odds = _to_float(row.get("current_odds") or row.get("win_odds"), 0.0)
            fair_odds = (1.0 / prob) if prob > 0 else 0.0
            predicted_structural = _predict_structural_odds(row, current_odds=odds, fair_odds=fair_odds)
            predicted_live = _predict_live_odds(row, current_odds=odds, fallback_odds=predicted_structural)
            predicted_odds, prediction_source = _select_predicted_odds(
                row,
                structural_odds=predicted_structural,
                live_odds=predicted_live,
            )

            ev_current = prob * odds if odds > 0 else 0.0
            ev_predicted_structural = prob * predicted_structural if predicted_structural > 0 else 0.0
            ev_predicted_live = prob * predicted_live if predicted_live > 0 else 0.0
            ev_predicted = prob * predicted_odds if predicted_odds > 0 else 0.0
            odds_gap = predicted_odds - odds if odds > 0 else 0.0
            odds_gap_ratio = (odds_gap / odds) if odds > 0 else 0.0
            ev_gap = ev_predicted - ev_current if ev_current > 0 else 0.0
            ev_gap_ratio = (ev_gap / ev_current) if ev_current > 0 else 0.0
            market_prob = _to_float(meta["market_prob"], 0.0)
            model_to_market_ratio = prob / market_prob if market_prob > 0 else 0.0
            front_value = _front_value_audit(
                row,
                structural_win_prob=prob,
                market_prob=market_prob,
                current_odds=odds,
            )

            race_rows.append(
                {
                    **row,
                    "current_odds": _fmt(odds),
                    "win_odds": _fmt(odds),
                    "predicted_odds_structural": _fmt(predicted_structural),
                    "predicted_odds_live": _fmt(predicted_live),
                    "predicted_odds": _fmt(predicted_odds),
                    "predicted_odds_source": prediction_source,
                    "win_prob": _fmt(prob),
                    "win_prob_mean": _fmt(prob),
                    "structural_model_score": str(row.get("model_score", "")),
                    "structural_win_prob": _fmt(prob),
                    "structural_fair_odds": _fmt(fair_odds),
                    "structural_ev_current": _fmt(ev_current),
                    "structural_probability_formula_version": "structural_ev_v2_environment",
                    "win_prob_std": _fmt(probability_std),
                    "monte_carlo_iterations": str(max(1, int(weights.monte_carlo_iterations))),
                    "monte_carlo_seed": str(int(weights.monte_carlo_seed)),
                    "luck_score_std": _fmt(max(0.0, weights.luck_score_std)),
                    "fair_odds": _fmt(fair_odds),
                    "market_prob": _fmt(meta["market_prob"]),
                    "market_implied_prob": _fmt(meta["market_prob"]),
                    "model_to_market_ratio": _fmt(model_to_market_ratio),
                    "market_shrink_used": _fmt(meta["market_shrink"]),
                    "probability_cap": _fmt(meta["probability_cap"]),
                    "probability_band": meta["probability_band"],
                    "ev": _fmt(ev_current),
                    "ev_win": _fmt(ev_current),
                    "ev_current": _fmt(ev_current),
                    "ev_predicted_structural": _fmt(ev_predicted_structural),
                    "ev_predicted_live": _fmt(ev_predicted_live),
                    "ev_predicted": _fmt(ev_predicted),
                    "odds_gap": _fmt(odds_gap),
                    "odds_gap_ratio": _fmt(odds_gap_ratio),
                    "ev_delta": _fmt(ev_gap),
                    "ev_delta_ratio": _fmt(ev_gap_ratio),
                    **front_value,
                }
            )

        for row in race_rows:
            row["front_value_candidate"] = bool(row.pop("_front_value_eligible", False))
        out.extend(race_rows)

    out.sort(
        key=lambda row: (
            str(row.get("race_id", "")),
            -_to_float(row.get("ev"), 0.0),
            -_to_float(row.get("win_prob"), 0.0),
        )
    )
    return out


def _monte_carlo_win_probs(
    rows: list[dict],
    market_probs: list[float],
    weights: ModelWeights,
    *,
    race_id: str,
) -> tuple[list[float], list[float], list[dict[str, float | str]]]:
    if not rows:
        return [], [], []

    iterations = max(1, int(weights.monte_carlo_iterations))
    base_scores = [_to_float(row.get("model_score"), 0.0) for row in rows]
    base_raw_probs = _softmax([score * weights.temperature for score in base_scores])
    base_probs, calibration = _blend_probabilities(rows, base_raw_probs, market_probs, weights)
    if iterations == 1 or weights.luck_score_std <= 0:
        return base_probs, [0.0 for _ in rows], calibration

    rng = random.Random(_stable_race_seed(race_id, weights.monte_carlo_seed))
    sums = [0.0 for _ in rows]
    squared_sums = [0.0 for _ in rows]
    noise_stds = [_luck_noise_std(row, weights) for row in rows]

    for _ in range(iterations):
        noisy_scores = [
            (score + rng.gauss(0.0, noise_std)) * weights.temperature
            for score, noise_std in zip(base_scores, noise_stds)
        ]
        raw_probs = _softmax(noisy_scores)
        trial_probs, _ = _blend_probabilities(rows, raw_probs, market_probs, weights)
        for index, probability in enumerate(trial_probs):
            sums[index] += probability
            squared_sums[index] += probability * probability

    means = _normalize_probs([value / iterations for value in sums])
    stds = [
        math.sqrt(max(0.0, (squared_sums[index] / iterations) - ((sums[index] / iterations) ** 2)))
        for index in range(len(rows))
    ]
    return means, stds, calibration


def _luck_noise_std(row: dict, weights: ModelWeights) -> float:
    consistency = _clamp(_to_float(row.get("consistency"), 0.5), 0.0, 1.0)
    uncertainty_multiplier = 1.0 + (max(0.0, weights.consistency_noise_scale) * (1.0 - consistency))
    return max(0.0, weights.luck_score_std) * uncertainty_multiplier


def _stable_race_seed(race_id: str, base_seed: int) -> int:
    digest = blake2b(f"{base_seed}:{race_id}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def _score_row(row: dict, weights: ModelWeights) -> dict:
    out = dict(row)
    ability_score = _to_float(out.get("ability_score"), 0.0)
    course_score = _to_float(out.get("course_score"), 0.0)
    pace_score = _to_float(out.get("pace_score"), 0.0)
    weight_score = _to_float(out.get("weight_score"), 0.0)
    jockey_score = _to_float(out.get("jockey_score"), 0.0)
    out["model_score"] = _fmt(
        (weights.ability * ability_score)
        + (weights.course * course_score)
        + (weights.pace * pace_score)
        + (weights.weight * weight_score)
        + (weights.jockey * jockey_score)
    )
    return out


def _apply_environment_offsets(rows: list[dict], baseline_probs: list[float]) -> list[float]:
    """Apply the promoted model exactly as trained: softmax(log(p0) + offset)."""
    if not baseline_probs or not any(bool(row.get("environment_adjustment_applied")) for row in rows):
        return baseline_probs
    if not all(bool(row.get("environment_adjustment_applied")) for row in rows):
        raise ValueError("Environment adjustment must cover the complete race field")
    scores = [
        math.log(max(probability, 1e-300)) + _to_float(row.get("environment_score"), 0.0)
        for row, probability in zip(rows, baseline_probs)
    ]
    return _softmax(scores)


def _market_probs(rows: list[dict]) -> list[float]:
    implied = []
    for row in rows:
        odds = _to_float(row.get("current_odds") or row.get("win_odds"), 0.0)
        implied.append((1.0 / odds) if odds > 0 else 0.0)
    total = sum(implied)
    if total <= 0:
        return []
    return [value / total for value in implied]


def _blend_probabilities(
    rows: list[dict],
    raw_probs: list[float],
    market_probs: list[float],
    weights: ModelWeights,
) -> tuple[list[float], list[dict[str, float | str]]]:
    if not raw_probs:
        return [], []

    normalized = _normalize_probs(raw_probs)
    effective_market_probs = market_probs if len(market_probs) == len(rows) else [0.0 for _ in rows]
    return normalized, [
        {
            "market_prob": market_prob,
            "market_shrink": 0.0,
            "probability_cap": 1.0,
            "probability_band": _probability_band(row),
        }
        for row, market_prob in zip(rows, effective_market_probs)
    ]


def _front_value_audit(
    row: dict,
    *,
    structural_win_prob: float,
    market_prob: float,
    current_odds: float,
) -> dict[str, object]:
    """Audit an evidence-backed, market-overlooked front-running candidate.

    This flag is diagnostic only.  Ticket selection continues to require the
    normal EV, robust-EV, probability and portfolio checks.
    """

    required = {
        "front_rate": 0.68,
        "relative_front_rank": 0.75,
        "history_count": 4.0,
        "current_odds": 10.0,
        "model_to_market_ratio": 1.25,
        "structural_ev_current": 1.05,
        "mid_slow_fit": 0.45,
    }
    front_rate = _to_float(row.get("front_rate"), 0.0)
    relative_front_rank = _to_float(row.get("relative_front_rank"), 0.0)
    history_count = _to_float(row.get("history_count"), 0.0)
    pace_mid = _to_float(row.get("pace_mid"), 0.0)
    pace_slow = _to_float(row.get("pace_slow"), 0.0)
    mid_slow_fit = max(pace_mid, pace_slow)
    ratio = structural_win_prob / market_prob if market_prob > 0 else 0.0
    structural_ev = structural_win_prob * current_odds if current_odds > 0 else 0.0
    values = {
        "front_rate": front_rate,
        "relative_front_rank": relative_front_rank,
        "history_count": history_count,
        "current_odds": current_odds,
        "model_to_market_ratio": ratio,
        "structural_ev_current": structural_ev,
        "mid_slow_fit": mid_slow_fit,
    }
    source_keys = {
        "front_rate": ("front_rate",),
        "relative_front_rank": ("relative_front_rank",),
        "history_count": ("history_count",),
        "current_odds": ("current_odds", "win_odds"),
        "model_to_market_ratio": ("current_odds", "win_odds"),
        "structural_ev_current": ("current_odds", "win_odds"),
        "mid_slow_fit": ("pace_mid", "pace_slow"),
    }
    checks: dict[str, dict[str, object]] = {}
    failure_reasons: list[str] = []
    for name, threshold in required.items():
        present = any(str(row.get(key, "")).strip() not in {"", "None"} for key in source_keys[name])
        if name == "model_to_market_ratio" and market_prob <= 0:
            present = False
        passed = present and values[name] >= threshold
        checks[name] = {
            "value": _fmt(values[name]),
            "threshold": _fmt(threshold),
            "passed": passed,
        }
        if not present:
            failure_reasons.append(f"missing_{name}")
        elif not passed:
            failure_reasons.append(f"below_{name}")

    score = (
        (0.25 * front_rate)
        + (0.20 * relative_front_rank)
        + (0.15 * mid_slow_fit)
        + (0.20 * min(ratio / 3.0, 1.0))
        + (0.20 * min(structural_ev / 3.0, 1.0))
    )
    return {
        "mid_slow_fit": _fmt(mid_slow_fit),
        "front_value_score": _fmt(score),
        "front_value_checks": checks,
        "front_value_reasons": [name for name, check in checks.items() if check["passed"]],
        "front_value_failure_reasons": failure_reasons,
        "_front_value_eligible": not failure_reasons,
    }


def _predict_structural_odds(row: dict, *, current_odds: float, fair_odds: float) -> float:
    if current_odds <= 0:
        return 0.0

    avg_popularity = _to_float(row.get("avg_popularity"), 0.0)
    current_popularity = _to_float(
        row.get("popularity_latest") or row.get("current_popularity"),
        avg_popularity or 0.0,
    )
    popularity_gap = _to_float(row.get("popularity_gap"), 0.0)
    if popularity_gap == 0.0 and avg_popularity > 0 and current_popularity > 0:
        popularity_gap = current_popularity - avg_popularity
    band = _probability_band(row, current_odds=current_odds, popularity=current_popularity)
    popularity_factor = _clamp(1.0 + (0.030 * popularity_gap), 0.84, 1.16)
    if band == "outsider":
        popularity_factor = _clamp(popularity_factor, 0.92, 1.08)
    elif band == "longshot":
        popularity_factor = _clamp(popularity_factor, 0.96, 1.04)

    days_since_last_run = _to_float(row.get("days_since_last_run"), 35.0)
    time_signal = (days_since_last_run - 35.0) / 120.0
    time_factor = _clamp(1.0 + (0.5 * time_signal), 0.94, 1.10)

    avg_odds = _to_float(row.get("avg_odds"), current_odds)
    volatility = _to_float(row.get("odds_volatility"), 0.0)
    history_count = _to_float(row.get("history_count"), 0.0)
    anchor_weight = _clamp(0.16 + (0.04 * min(history_count, 5.0)) + (0.10 * volatility), 0.14, 0.40)
    if band == "outsider":
        anchor_weight = max(anchor_weight, 0.30)
    elif band == "longshot":
        anchor_weight = max(anchor_weight, 0.52)

    adjusted = current_odds * popularity_factor * time_factor
    if fair_odds > 0:
        adjusted = ((1.0 - anchor_weight) * adjusted) + (anchor_weight * fair_odds)
    if avg_odds > 0:
        adjusted = (0.82 * adjusted) + (0.18 * avg_odds)

    if band == "favorite":
        lower_multiplier = 0.72
        upper_multiplier = 1.18
    elif band == "contender":
        lower_multiplier = 0.70
        upper_multiplier = 1.22
    elif band == "outsider":
        lower_multiplier = 0.76
        upper_multiplier = 1.12
    else:
        lower_multiplier = 0.82
        upper_multiplier = 1.05

    lower = max(1.05, min(current_odds, fair_odds if fair_odds > 0 else current_odds) * lower_multiplier)
    upper_anchor = max(current_odds, fair_odds if fair_odds > 0 else current_odds)
    upper = max(lower + 0.01, upper_anchor * upper_multiplier)
    return _clamp(adjusted, lower, upper)


def _predict_live_odds(row: dict, *, current_odds: float, fallback_odds: float) -> float:
    latest_odds = _to_float(row.get("odds_latest"), current_odds or fallback_odds)
    if latest_odds <= 0:
        return fallback_odds

    sample_count = _to_float(row.get("odds_snapshot_count"), 0.0)
    span_minutes = _to_float(row.get("odds_span_minutes"), 0.0)
    range_ratio = _to_float(row.get("odds_range_ratio"), 0.0)
    slope_recent = _to_float(row.get("odds_slope_recent"), 0.0)
    slope_full = _to_float(row.get("odds_slope_full"), 0.0)
    popularity_change = _to_float(row.get("popularity_change"), 0.0)
    odds_min = _to_float(row.get("odds_min"), latest_odds)
    odds_max = _to_float(row.get("odds_max"), latest_odds)

    confidence = _live_confidence(sample_count, span_minutes)
    trend_projection = _clamp((0.65 * slope_recent) + (0.35 * slope_full), -0.25, 0.25)
    popularity_factor = _clamp(1.0 + (0.025 * popularity_change), 0.88, 1.18)
    range_factor = _clamp(1.0 + (0.18 * range_ratio), 0.92, 1.15)

    projected = latest_odds * (1.0 + (trend_projection * confidence)) * popularity_factor * range_factor
    projected = ((1.0 - (0.20 * confidence)) * projected) + ((0.20 * confidence) * fallback_odds)

    lower = max(1.05, min(latest_odds, odds_min) * 0.88)
    upper = max(lower + 0.01, max(latest_odds, odds_max, fallback_odds) * 1.12)
    return _clamp(projected, lower, upper)


def _select_predicted_odds(
    row: dict,
    *,
    structural_odds: float,
    live_odds: float,
) -> tuple[float, str]:
    sample_count = _to_float(row.get("odds_snapshot_count"), 0.0)
    span_minutes = _to_float(row.get("odds_span_minutes"), 0.0)
    latest_odds = _to_float(row.get("odds_latest"), 0.0)
    if latest_odds > 0 and sample_count >= 3 and span_minutes >= 5.0:
        return live_odds, "live"
    return structural_odds, "structural"


def _normalize_probs(values: list[float]) -> list[float]:
    total = sum(max(0.0, value) for value in values)
    if total <= 0:
        return [0.0 for _ in values]
    return [max(0.0, value) / total for value in values]


def _apply_probability_caps(values: list[float], caps: list[float]) -> list[float]:
    if not values or len(values) != len(caps):
        return values

    safe_caps = [max(0.0, min(1.0, cap)) for cap in caps]
    total_cap = sum(safe_caps)
    if total_cap <= 0:
        return [0.0 for _ in values]
    if total_cap < 1.0:
        return _normalize_probs(safe_caps)

    base = [max(0.0, value) for value in values]
    assigned = [0.0 for _ in values]
    active = set(range(len(base)))
    remaining_total = 1.0

    while active:
        base_total = sum(base[idx] for idx in active)
        if base_total <= 0 or remaining_total <= 0:
            break

        violated: list[int] = []
        for idx in active:
            candidate = remaining_total * (base[idx] / base_total)
            if candidate > safe_caps[idx]:
                assigned[idx] = safe_caps[idx]
                remaining_total -= safe_caps[idx]
                violated.append(idx)

        if not violated:
            for idx in active:
                assigned[idx] = remaining_total * (base[idx] / base_total)
            remaining_total = 0.0
            break

        for idx in violated:
            active.remove(idx)
            base[idx] = 0.0

    if remaining_total > 1e-9:
        for idx in range(len(assigned)):
            slack = max(0.0, safe_caps[idx] - assigned[idx])
            if slack <= 0:
                continue
            take = min(slack, remaining_total)
            assigned[idx] += take
            remaining_total -= take
            if remaining_total <= 1e-9:
                break

    return _normalize_probs(assigned)


def _probability_band(
    row: dict,
    *,
    current_odds: float | None = None,
    popularity: float | None = None,
) -> str:
    odds = current_odds if current_odds is not None else _to_float(row.get("current_odds") or row.get("win_odds"), 0.0)
    pop = popularity if popularity is not None else _to_float(
        row.get("popularity_latest") or row.get("current_popularity"),
        0.0,
    )
    if (pop > 0 and pop <= 3) or (odds > 0 and odds <= 6.0):
        return "favorite"
    if (pop > 0 and pop <= 8) or (odds > 0 and odds <= 15.0):
        return "contender"
    if (pop > 0 and pop <= 12) or (odds > 0 and odds <= 30.0):
        return "outsider"
    return "longshot"


def _live_confidence(sample_count: float, span_minutes: float) -> float:
    sample_term = _clamp((sample_count - 2.0) / 4.0, 0.0, 1.0)
    span_term = _clamp(span_minutes / 45.0, 0.0, 1.0)
    return _clamp((0.55 * sample_term) + (0.45 * span_term), 0.0, 1.0)


def _softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    maximum = max(scores)
    exps = [math.exp(score - maximum) for score in scores]
    total = sum(exps)
    return [value / total for value in exps] if total > 0 else [0.0 for _ in scores]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
