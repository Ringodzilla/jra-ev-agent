from __future__ import annotations

from datetime import datetime, timezone
import math
from statistics import pstdev
from typing import Iterable, Mapping, Sequence


SCENARIO_WEIGHTS = {
    "stable": 0.45,
    "favorite_concentration": 0.25,
    "longshot_outflow": 0.10,
    "late_local_inflow": 0.20,
}

DEFAULT_MARKET_SHRINK = {
    "win": 0.35,
    "place": 0.45,
    "wide": 0.55,
    "wakuren": 0.60,
    "umaren": 0.62,
    "umatan": 0.70,
    "sanrenpuku": 0.76,
    "sanrentan": 0.82,
}

_FORBIDDEN_INPUT_KEYS = {
    "actual_finish",
    "actual_result",
    "closing_odds",
    "finish",
    "final_odds",
    "final_win_odds",
    "payout",
    "payout_per_100",
    "payout_yen",
    "payout_yen_per_100",
    "result",
    "result_label",
}

_PRIOR_RANGE_BY_TYPE = {
    "win": 0.10,
    "place": 0.08,
    "wide": 0.12,
    "wakuren": 0.14,
    "umaren": 0.14,
    "umatan": 0.17,
    "sanrenpuku": 0.18,
    "sanrentan": 0.22,
}


def build_ticket_odds_scenarios(
    ticket: Mapping[str, object],
    current_official_odds: float,
    combo_odds_history: Sequence[Mapping[str, object]],
    ev_rows: Sequence[Mapping[str, object]],
    seconds_to_post: float,
) -> dict[str, object]:
    """Return auditable pre-post odds scenarios and a downside-only robust EV.

    The function deliberately accepts no result or closing-odds argument. Historical
    rows containing outcome labels are rejected so post-race information cannot leak
    into ticket construction.
    """

    _reject_outcome_fields(ticket, combo_odds_history, ev_rows)
    current = _positive_float(current_official_odds, "current_official_odds")
    remaining_seconds = max(0.0, _to_float(seconds_to_post))
    bet_type = str(ticket.get("bet_type", "")).strip()
    raw_prob = _raw_hit_probability(ticket)
    shrink = _clamp(
        _to_float(ticket.get("bet_type_market_shrink"), DEFAULT_MARKET_SHRINK.get(bet_type, 0.65)),
        0.0,
        0.97,
    )

    history = _matching_history(ticket, combo_odds_history)
    history_features = _history_features(history, current=current)
    history_quality = _history_quality(
        history_features["point_count"], history_features["span_minutes"]
    )
    prior_range = _PRIOR_RANGE_BY_TYPE.get(bet_type, 0.16)
    effective_range = max(
        history_features["range_ratio"], prior_range * (1.0 - history_quality)
    )
    effective_volatility = max(
        history_features["log_return_volatility"], (prior_range * 0.55) * (1.0 - history_quality)
    )

    horizon_hours = min(1.0, remaining_seconds / 3600.0)
    trend_log_per_hour = _clamp(
        (0.70 * history_features["recent_log_slope_per_hour"])
        + (0.30 * history_features["full_log_slope_per_hour"]),
        -0.80,
        0.80,
    )
    trend_damping = 0.30 + (0.70 * history_quality)
    projected_trend_log = _clamp(
        trend_log_per_hour * horizon_hours * trend_damping,
        math.log(0.70),
        math.log(1.35),
    )

    market_prob = min(1.0, 1.0 / current)
    model_vs_market_ratio = raw_prob / market_prob
    constituent_ratio, constituent_count = _constituent_odds_ratio(ticket, ev_rows)
    model_gap = abs(math.log(max(model_vs_market_ratio, 1e-9)))
    constituent_gap = abs(math.log(max(constituent_ratio, 1e-9)))
    urgency = _clamp(1.0 - (remaining_seconds / 1800.0), 0.0, 1.0)
    band = _odds_band(current)

    stable = current * math.exp(projected_trend_log)
    concentration_factor = {
        "favorite": 0.84,
        "contender": 0.93,
        "outsider": 1.06,
        "longshot": 1.15,
    }[band]
    concentration_strength = 0.55 + (0.30 * urgency) + (0.15 * effective_range)
    favorite_concentration = stable * (
        1.0 + ((concentration_factor - 1.0) * concentration_strength)
    )

    outflow_factor = {
        "favorite": 0.95,
        "contender": 0.99,
        "outsider": 1.13,
        "longshot": 1.28,
    }[band]
    outflow_strength = 0.50 + (0.35 * urgency) + (0.15 * effective_range)
    longshot_outflow = stable * (1.0 + ((outflow_factor - 1.0) * outflow_strength))

    band_shock = {
        "favorite": 0.03,
        "contender": 0.05,
        "outsider": 0.08,
        "longshot": 0.12,
    }[band]
    local_inflow_shock = _clamp(
        band_shock
        + (0.30 * effective_range)
        + (0.25 * effective_volatility)
        + (0.035 * min(model_gap, 2.0))
        + (0.025 * min(constituent_gap, 1.5))
        + (0.05 * urgency)
        + (0.06 * (1.0 - history_quality)),
        0.04,
        0.32,
    )
    late_local_inflow = min(current, stable) * (1.0 - local_inflow_shock)

    raw_odds = {
        "stable": stable,
        "favorite_concentration": favorite_concentration,
        "longshot_outflow": longshot_outflow,
        "late_local_inflow": late_local_inflow,
    }
    scenario_rows: list[dict[str, object]] = []
    for name, weight in SCENARIO_WEIGHTS.items():
        odds = max(1.01, raw_odds[name])
        scenario_market_prob = min(1.0, 1.0 / odds)
        calibrated_prob = ((1.0 - shrink) * raw_prob) + (shrink * scenario_market_prob)
        scenario_rows.append(
            {
                "name": name,
                "weight": weight,
                "odds": odds,
                "odds_multiplier": odds / current,
                "market_prob": scenario_market_prob,
                "calibrated_hit_prob": calibrated_prob,
                "ev": calibrated_prob * odds,
            }
        )

    robust_odds_quantile = _weighted_quantile(
        [(float(row["odds"]), float(row["weight"])) for row in scenario_rows], 0.20
    )
    robust_odds = min(current, robust_odds_quantile)
    robust_market_prob = min(1.0, 1.0 / robust_odds)
    robust_hit_prob = ((1.0 - shrink) * raw_prob) + (shrink * robust_market_prob)
    robust_ev = _weighted_lower_tail_mean(
        [(float(row["ev"]), float(row["weight"])) for row in scenario_rows], 0.20
    )

    return {
        "current_official_odds": current,
        "raw_hit_prob": raw_prob,
        "market_shrink": shrink,
        "scenario_weights": dict(SCENARIO_WEIGHTS),
        "scenarios": scenario_rows,
        "robust_quantile": 0.20,
        "robust_odds": robust_odds,
        "robust_hit_prob": robust_hit_prob,
        "robust_ev": robust_ev,
        "cvar20_ev": robust_ev,
        "audit": {
            "bet_type": bet_type,
            "combination": _ticket_combination(ticket),
            "seconds_to_post": remaining_seconds,
            "urgency": urgency,
            "history_points_used": history_features["point_count"],
            "history_span_minutes": history_features["span_minutes"],
            "history_quality": history_quality,
            "fallback_prior_used": history_quality < 1.0,
            "observed_range_ratio": history_features["range_ratio"],
            "effective_range_ratio": effective_range,
            "observed_log_return_volatility": history_features["log_return_volatility"],
            "effective_log_return_volatility": effective_volatility,
            "recent_log_slope_per_hour": history_features["recent_log_slope_per_hour"],
            "full_log_slope_per_hour": history_features["full_log_slope_per_hour"],
            "projected_trend_log": projected_trend_log,
            "model_vs_market_probability_ratio": model_vs_market_ratio,
            "constituent_predicted_to_current_odds_ratio": constituent_ratio,
            "constituent_rows_used": constituent_count,
            "odds_band": band,
            "late_local_inflow_shock": local_inflow_shock,
            "upside_capped_at_current": robust_odds_quantile > current,
        },
    }


def build_odds_scenarios(
    ticket: Mapping[str, object],
    current_official_odds: float,
    combo_odds_history: Sequence[Mapping[str, object]],
    ev_rows: Sequence[Mapping[str, object]],
    seconds_to_post: float,
) -> dict[str, object]:
    """Public shorthand matching the module's name."""

    return build_ticket_odds_scenarios(
        ticket,
        current_official_odds,
        combo_odds_history,
        ev_rows,
        seconds_to_post,
    )


def _matching_history(
    ticket: Mapping[str, object], history: Sequence[Mapping[str, object]]
) -> list[tuple[datetime, float]]:
    race_id = str(ticket.get("race_id", "")).strip()
    bet_type = str(ticket.get("bet_type", "")).strip()
    combination = _ticket_combination(ticket)
    points: dict[datetime, float] = {}
    for row in history:
        if race_id and str(row.get("race_id", "")).strip() != race_id:
            continue
        if str(row.get("bet_type", "")).strip() not in {"", bet_type}:
            continue
        row_combo = _canonical_combination(bet_type, row.get("combination", ""))
        if row_combo and combination and row_combo != combination:
            continue
        if "snapshot_complete" in row and not _is_true(row.get("snapshot_complete")):
            continue
        odds = _to_float(row.get("odds_min") or row.get("odds"))
        captured = _parse_timestamp(row.get("captured_at"))
        if odds > 1.0 and captured is not None:
            points[captured] = odds
    return sorted(points.items())


def _history_features(points: Sequence[tuple[datetime, float]], *, current: float) -> dict[str, float]:
    augmented = list(points)
    if not augmented or abs(augmented[-1][1] - current) > 1e-12:
        timestamp = augmented[-1][0] if augmented else datetime.min.replace(tzinfo=timezone.utc)
        augmented.append((timestamp, current))
    values = [odds for _, odds in augmented]
    log_returns = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]
    return {
        "point_count": float(len(points)),
        "span_minutes": _span_minutes(points),
        "range_ratio": (max(values) - min(values)) / current,
        "log_return_volatility": pstdev(log_returns) if len(log_returns) >= 2 else 0.0,
        "recent_log_slope_per_hour": _log_slope(augmented[-3:]),
        "full_log_slope_per_hour": _log_slope(augmented),
    }


def _constituent_odds_ratio(
    ticket: Mapping[str, object], ev_rows: Sequence[Mapping[str, object]]
) -> tuple[float, int]:
    wanted = set(_ticket_numbers(ticket))
    ratios: list[float] = []
    for row in ev_rows:
        number = str(row.get("horse_number", "")).strip()
        if wanted and number not in wanted:
            continue
        current = _to_float(row.get("current_odds") or row.get("win_odds"))
        predicted = _to_float(row.get("predicted_odds"))
        if current > 0 and predicted > 0:
            ratios.append(predicted / current)
    if not ratios:
        return 1.0, 0
    return math.prod(ratios) ** (1.0 / len(ratios)), len(ratios)


def _raw_hit_probability(ticket: Mapping[str, object]) -> float:
    value = ticket.get("raw_hit_prob")
    if value in (None, "", "None"):
        value = ticket.get("hit_prob") or ticket.get("win_prob")
    probability = _to_float(value)
    if probability <= 0 or probability > 1:
        raise ValueError("ticket must contain a raw hit probability in (0, 1]")
    return probability


def _weighted_quantile(values: Sequence[tuple[float, float]], quantile: float) -> float:
    ordered = sorted((value, max(0.0, weight)) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        raise ValueError("scenario weights must have positive mass")
    target = _clamp(quantile, 0.0, 1.0) * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative + 1e-12 >= target:
            return value
    return ordered[-1][0]  # pragma: no cover - positive finite mass reaches the target


def _weighted_lower_tail_mean(values: Sequence[tuple[float, float]], alpha: float) -> float:
    ordered = sorted((value, max(0.0, weight)) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    tail_mass = _clamp(alpha, 1e-9, 1.0) * total
    remaining = tail_mass
    weighted_sum = 0.0
    for value, weight in ordered:
        take = min(weight, remaining)
        weighted_sum += value * take
        remaining -= take
        if remaining <= 1e-12:
            break
    return weighted_sum / tail_mass


def _history_quality(point_count: float, span_minutes: float) -> float:
    count_quality = _clamp((point_count - 1.0) / 3.0, 0.0, 1.0)
    span_quality = _clamp(span_minutes / 30.0, 0.0, 1.0)
    return (0.60 * count_quality) + (0.40 * span_quality)


def _log_slope(points: Sequence[tuple[datetime, float]]) -> float:
    if len(points) < 2 or points[0][1] <= 0 or points[-1][1] <= 0:
        return 0.0
    hours = (points[-1][0] - points[0][0]).total_seconds() / 3600.0
    if hours <= 1e-6:
        return 0.0
    return _clamp(math.log(points[-1][1] / points[0][1]) / hours, -1.5, 1.5)


def _span_minutes(points: Sequence[tuple[datetime, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return max(0.0, (points[-1][0] - points[0][0]).total_seconds() / 60.0)


def _ticket_combination(ticket: Mapping[str, object]) -> str:
    bet_type = str(ticket.get("bet_type", "")).strip()
    values = ticket.get("frame_numbers") if bet_type == "wakuren" else ticket.get("horse_numbers")
    if isinstance(values, Iterable) and not isinstance(values, (str, bytes, Mapping)):
        cleaned = [str(value) for value in values]
        if cleaned:
            return _canonical_combination(bet_type, cleaned)
    return _canonical_combination(bet_type, ticket.get("horse_number", ""))


def _ticket_numbers(ticket: Mapping[str, object]) -> list[str]:
    value = ticket.get("horse_numbers")
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return [str(int(_to_float(item))) for item in value if _to_float(item) > 0]
    return _split_combination(ticket.get("horse_number", ""))


def _canonical_combination(bet_type: str, value: object) -> str:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        parts = [str(int(_to_float(item))) for item in value if _to_float(item) > 0]
    else:
        parts = _split_combination(value)
    if bet_type not in {"umatan", "sanrentan"}:
        parts.sort(key=int)
    separator = ">" if bet_type in {"umatan", "sanrentan"} else "-"
    return separator.join(parts)


def _split_combination(value: object) -> list[str]:
    normalized = str(value or "").replace("→", "-").replace(">", "-").replace(" ", "")
    return [str(int(_to_float(part))) for part in normalized.split("-") if _to_float(part) > 0]


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _odds_band(odds: float) -> str:
    if odds <= 6.0:
        return "favorite"
    if odds <= 15.0:
        return "contender"
    if odds <= 30.0:
        return "outsider"
    return "longshot"


def _reject_outcome_fields(
    ticket: Mapping[str, object],
    history: Sequence[Mapping[str, object]],
    ev_rows: Sequence[Mapping[str, object]],
) -> None:
    for row in [ticket, *history, *ev_rows]:
        forbidden = _FORBIDDEN_INPUT_KEYS.intersection(str(key).lower() for key in row.keys())
        if forbidden:
            raise ValueError(f"post-race fields are not allowed: {sorted(forbidden)}")


def _is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "complete"}


def _positive_float(value: object, name: str) -> float:
    result = _to_float(value)
    if result <= 1.0 or not math.isfinite(result):
        raise ValueError(f"{name} must be finite and greater than 1.0")
    return result


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
