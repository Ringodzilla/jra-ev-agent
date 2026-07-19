from __future__ import annotations

import math
import random
from hashlib import blake2b
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class ModelWeights:
    ability: float = 0.42
    course: float = 0.14
    pace: float = 0.16
    weight: float = 0.08
    jockey: float = 0.08
    market: float = 0.12
    temperature: float = 1.15
    market_shrink: float = 0.25
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

            out.append(
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
                    "win_prob_std": _fmt(probability_std),
                    "monte_carlo_iterations": str(max(1, int(weights.monte_carlo_iterations))),
                    "monte_carlo_seed": str(int(weights.monte_carlo_seed)),
                    "luck_score_std": _fmt(max(0.0, weights.luck_score_std)),
                    "fair_odds": _fmt(fair_odds),
                    "market_prob": _fmt(meta["market_prob"]),
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
                }
            )

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
    market_score = _to_float(out.get("market_support"), 0.0)

    out["model_score"] = _fmt(
        (weights.ability * ability_score)
        + (weights.course * course_score)
        + (weights.pace * pace_score)
        + (weights.weight * weight_score)
        + (weights.jockey * jockey_score)
        + (weights.market * market_score)
    )
    return out


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

    if not market_probs:
        normalized = _normalize_probs(raw_probs)
        return normalized, [
            {
                "market_prob": 0.0,
                "market_shrink": 0.0,
                "probability_cap": 1.0,
                "probability_band": _probability_band(row),
            }
            for row in rows
        ]

    blended_probs: list[float] = []
    caps: list[float] = []
    calibration: list[dict[str, float | str]] = []
    for row, raw_prob, market_prob in zip(rows, raw_probs, market_probs):
        band = _probability_band(row)
        shrink = _dynamic_market_shrink(row, base_shrink=weights.market_shrink, band=band)
        shrink = _market_divergence_shrink(raw_prob, market_prob, shrink, band=band)
        blended = ((1.0 - shrink) * raw_prob) + (shrink * market_prob)
        cap = _probability_cap(row, market_prob=market_prob, band=band)
        blended_probs.append(blended)
        caps.append(cap)
        calibration.append(
            {
                "market_prob": market_prob,
                "market_shrink": shrink,
                "probability_cap": cap,
                "probability_band": band,
            }
        )

    normalized = _normalize_probs(blended_probs)
    return _apply_probability_caps(normalized, caps), calibration


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


def _dynamic_market_shrink(row: dict, *, base_shrink: float, band: str) -> float:
    live_conf = _live_confidence(
        _to_float(row.get("odds_snapshot_count"), 0.0),
        _to_float(row.get("odds_span_minutes"), 0.0),
    )
    band_target = {
        "favorite": 0.42,
        "contender": 0.64,
        "outsider": 0.78,
        "longshot": 0.90,
    }.get(band, base_shrink)
    return _clamp(max(base_shrink, band_target + (0.06 * live_conf)), 0.0, 0.96)


def _market_divergence_shrink(raw_prob: float, market_prob: float, shrink: float, *, band: str) -> float:
    if raw_prob <= 0 or market_prob <= 0:
        return shrink

    ratio = raw_prob / market_prob
    if ratio <= 1.35:
        return shrink

    band_sensitivity = {
        "favorite": 0.08,
        "contender": 0.14,
        "outsider": 0.18,
        "longshot": 0.22,
    }.get(band, 0.12)
    extra = min(0.18, (ratio - 1.35) * band_sensitivity)
    return _clamp(shrink + extra, 0.0, 0.97)


def _probability_cap(row: dict, *, market_prob: float, band: str) -> float:
    if market_prob <= 0:
        return 1.0

    ability = _to_float(row.get("ability_score"), 0.0)
    course = _to_float(row.get("course_score"), 0.0)
    pace = _to_float(row.get("pace_score"), 0.0)
    history = min(_to_float(row.get("history_count"), 0.0) / 5.0, 1.0)
    live_conf = _live_confidence(
        _to_float(row.get("odds_snapshot_count"), 0.0),
        _to_float(row.get("odds_span_minutes"), 0.0),
    )
    model_conf = _clamp((0.45 * ability) + (0.25 * course) + (0.20 * pace) + (0.10 * history), 0.0, 1.0)

    if band == "favorite":
        multiplier = 1.24 + (0.08 * model_conf) - (0.02 * live_conf)
        additive = 0.020
    elif band == "contender":
        multiplier = 1.08 + (0.06 * model_conf) - (0.03 * live_conf)
        additive = 0.010
    elif band == "outsider":
        multiplier = 1.03 + (0.04 * model_conf) - (0.05 * live_conf)
        additive = 0.003
    else:
        multiplier = 1.01 + (0.03 * model_conf) - (0.05 * live_conf)
        additive = 0.0005

    multiplier = max(1.0, multiplier)
    additive = max(0.0, additive * (1.0 - (0.40 * live_conf)))
    return min(1.0, (market_prob * multiplier) + additive)


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
