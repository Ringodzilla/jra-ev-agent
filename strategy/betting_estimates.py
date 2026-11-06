from __future__ import annotations

import math
from itertools import permutations
from strategy.betting_common import _clamp, _to_float


def _normalize_to_target(
    values: list[float],
    *,
    target: float,
    floors: list[float],
    caps: list[float],
) -> list[float]:
    if not values:
        return []

    normalized = _scale_to_target(values, target)
    out = [
        _clamp(value, minimum=max(0.0, floor), maximum=max(max(0.0, floor), cap))
        for value, floor, cap in zip(normalized, floors, caps)
    ]

    for _ in range(8):
        total = sum(out)
        delta = target - total
        if abs(delta) <= 1e-6:
            break

        if delta > 0:
            adjustable = [idx for idx, (value, cap) in enumerate(zip(out, caps)) if value < cap - 1e-9]
            if not adjustable:
                break
            weights = [max(values[idx], 1e-6) for idx in adjustable]
            weight_total = sum(weights) or float(len(adjustable))
            for idx, weight in zip(adjustable, weights):
                add = delta * (weight / weight_total)
                out[idx] = min(caps[idx], out[idx] + add)
        else:
            adjustable = [idx for idx, (value, floor) in enumerate(zip(out, floors)) if value > floor + 1e-9]
            if not adjustable:
                break
            weights = [max(out[idx] - floors[idx], 1e-6) for idx in adjustable]
            weight_total = sum(weights) or float(len(adjustable))
            remove = abs(delta)
            for idx, weight in zip(adjustable, weights):
                cut = remove * (weight / weight_total)
                out[idx] = max(floors[idx], out[idx] - cut)

    return out

def _scale_to_target(values: list[float], target: float) -> list[float]:
    total = sum(max(0.0, value) for value in values)
    if total <= 0:
        equal = target / max(len(values), 1)
        return [equal for _ in values]
    return [(max(0.0, value) / total) * target for value in values]

def _estimate_pair_hit_prob(
    left: dict[str, object],
    right: dict[str, object],
    *,
    field_size: int,
) -> float:
    slots = 2.0 if field_size <= 7 else 3.0
    left_place = _to_float(left.get("place_prob"))
    right_place = _to_float(right.get("place_prob"))
    avg_consistency = (_to_float(left.get("consistency"), 0.5) + _to_float(right.get("consistency"), 0.5)) / 2.0
    front_gap = abs(_to_float(left.get("front_rate"), 0.5) - _to_float(right.get("front_rate"), 0.5))
    complement = 1.0 - min(front_gap, 1.0)
    inflation = 1.14 + (0.55 * (slots / max(field_size, 1))) + (0.12 * avg_consistency) + (0.08 * complement)
    joint = left_place * right_place * inflation
    return _clamp(joint, minimum=0.0, maximum=min(left_place, right_place) * 0.97)

def _estimate_market_pair_prob(
    left: dict[str, object],
    right: dict[str, object],
    *,
    field_size: int,
) -> float:
    slots = 2.0 if field_size <= 7 else 3.0
    left_place = _to_float(left.get("market_place_prob"))
    right_place = _to_float(right.get("market_place_prob"))
    inflation = 1.10 + (0.45 * (slots / max(field_size, 1)))
    joint = left_place * right_place * inflation
    return _clamp(joint, minimum=0.0, maximum=min(left_place, right_place) * 0.98)

def _wide_pace_adjustment(left: dict[str, object], right: dict[str, object]) -> float:
    high_mix = max(_to_float(left.get("pace_mix_high")), _to_float(right.get("pace_mix_high")))
    if high_mix < 0.45:
        return 1.0

    left_front = _to_float(left.get("front_rate"), 0.5)
    right_front = _to_float(right.get("front_rate"), 0.5)
    if left_front < 0.62 or right_front < 0.62:
        return 1.0

    avg_closing = (_to_float(left.get("closing_strength")) + _to_float(right.get("closing_strength"))) / 2.0
    if avg_closing < 0.35:
        return 0.72
    if avg_closing < 0.50:
        return 0.84
    return 0.92

def _estimate_market_pair_odds(market_pair_prob: float) -> float:
    if market_pair_prob <= 0:
        return 0.0
    return _clamp(0.82 / market_pair_prob, minimum=1.1, maximum=75.0)

def _estimate_place_odds(market_place_prob: float) -> float:
    if market_place_prob <= 0:
        return 0.0
    return _clamp(0.80 / market_place_prob, minimum=1.1, maximum=18.0)

def _estimate_predicted_pair_odds(
    left: dict[str, object],
    right: dict[str, object],
    *,
    current_odds_est: float,
) -> float:
    return _estimate_predicted_combo_odds([left, right], current_odds_est=current_odds_est, max_odds=max(1.1, current_odds_est * 1.18))

def _estimate_predicted_combo_odds(
    rows: list[dict[str, object]],
    *,
    current_odds_est: float,
    max_odds: float,
) -> float:
    ratios: list[float] = []
    for row in rows:
        current = _to_float(row.get("current_odds"))
        predicted = _to_float(row.get("predicted_odds"))
        if current > 0 and predicted > 0:
            ratios.append(predicted / current)

    trend_ratio = math.prod(ratios) ** (1.0 / len(ratios)) if ratios else 1.0
    trend_ratio = _clamp(trend_ratio, minimum=0.88, maximum=1.16)
    return _clamp(current_odds_est * trend_ratio, minimum=1.1, maximum=min(max_odds, max(1.1, current_odds_est * 1.18)))

def _combo_hit_prob(rows: list[dict[str, object]], *, key: str, bet_type: str) -> float:
    if len(rows) < 2:
        return 0.0
    if bet_type in {"umatan", "sanrentan"}:
        return _ordered_finish_prob(rows, key=key)
    return sum(_ordered_finish_prob(list(order), key=key) for order in permutations(rows, len(rows)))

def _ordered_finish_prob(rows: list[dict[str, object]], *, key: str) -> float:
    remaining = 1.0
    probability = 1.0
    for row in rows:
        horse_prob = _to_float(row.get(key))
        if horse_prob <= 0 or remaining <= 0 or horse_prob >= remaining:
            return 0.0
        probability *= horse_prob / remaining
        remaining -= horse_prob
    return probability

def _estimate_exotic_odds(market_prob: float, *, payout_rate: float, max_odds: float) -> float:
    if market_prob <= 0:
        return 0.0
    return _clamp(payout_rate / market_prob, minimum=1.1, maximum=max_odds)

def _frame_combo_hit_prob(
    left_rows: list[dict[str, object]],
    right_rows: list[dict[str, object]],
    *,
    same_frame: bool,
    key: str,
) -> float:
    total = 0.0
    if same_frame:
        for order in permutations(left_rows, 2):
            total += _ordered_finish_prob(list(order), key=key)
        return total

    for left in left_rows:
        for right in right_rows:
            total += _ordered_finish_prob([left, right], key=key)
            total += _ordered_finish_prob([right, left], key=key)
    return total

def _frame_quality_adjustment(
    left_rows: list[dict[str, object]],
    right_rows: list[dict[str, object]],
    *,
    same_frame: bool,
) -> float:
    if same_frame:
        return _single_frame_quality(left_rows) ** 2
    return _single_frame_quality(left_rows) * _single_frame_quality(right_rows)

def _single_frame_quality(rows: list[dict[str, object]]) -> float:
    if len(rows) <= 1:
        return 1.0
    probs = sorted((_to_float(row.get("win_prob")) for row in rows), reverse=True)
    top = probs[0] if probs else 0.0
    if top <= 0:
        return 0.80
    second = probs[1] if len(probs) > 1 else 0.0
    depth_ratio = second / top
    return _clamp(0.82 + (0.18 * depth_ratio), minimum=0.82, maximum=1.0)
