from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from statistics import pstdev
from typing import Iterable, Sequence

from src.track_bias import track_bias_adjustment


DEFAULT_LAST3F = 36.0
DEFAULT_WEIGHT = 55.0
DEFAULT_RECENCY_WEIGHTS = (1.00, 0.85, 0.70, 0.55, 0.40)
DOMESTIC_TRACKS = {"札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"}


def summarize_history_rows(
    rows: Iterable[dict[str, str]],
    *,
    group_keys: Sequence[str],
) -> dict[tuple[str, ...], dict[str, float | str]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(key, "")).strip() for key in group_keys)
        grouped[key].append(row)

    summaries: dict[tuple[str, ...], dict[str, float | str]] = {}
    for key, group_rows in grouped.items():
        ordered = sorted(group_rows, key=lambda item: _safe_int(item.get("run_index", "")))
        weights = _recency_weights(len(ordered))

        finish_values = [_to_float(row.get("position"), 10.0) for row in ordered]
        last3f_values = [_to_float(row.get("last_3f"), DEFAULT_LAST3F) for row in ordered]
        popularity_values = [_to_float(row.get("popularity"), 10.0) for row in ordered]
        weight_values = [_to_float(row.get("weight"), DEFAULT_WEIGHT) for row in ordered]
        distance_values = [_to_float(row.get("distance"), 0.0) for row in ordered]
        time_values = [_distance_speed_score(row) for row in ordered]
        front_values = [_front_running_score(row.get("passing_order", "")) for row in ordered]
        odds_values = [_to_float(row.get("odds"), 0.0) for row in ordered]

        avg_finish = _weighted_mean(finish_values, weights, default=10.0)
        avg_last3f = _weighted_mean(last3f_values, weights, default=DEFAULT_LAST3F)
        avg_popularity = _weighted_mean(popularity_values, weights, default=10.0)
        avg_weight = _weighted_mean(weight_values, weights, default=DEFAULT_WEIGHT)
        avg_distance = _weighted_mean(distance_values, weights, default=0.0)
        speed_score = _weighted_mean(time_values, weights, default=0.0)
        front_rate = _weighted_mean(front_values, weights, default=0.5)
        avg_odds = _weighted_positive_mean(odds_values, weights)
        recent_odds = _weighted_positive_mean(odds_values[:2], weights[:2]) if odds_values[:2] else 0.0
        prior_odds = _weighted_positive_mean(odds_values[2:], weights[2:]) if odds_values[2:] else avg_odds
        odds_trend = _odds_trend(odds_values)
        odds_volatility = _odds_volatility(odds_values)
        win_rate = _weighted_mean([1.0 if value <= 1.0 else 0.0 for value in finish_values], weights, default=0.0)
        top3_rate = _weighted_mean([1.0 if value <= 3.0 else 0.0 for value in finish_values], weights, default=0.0)
        consistency = 1.0 / (1.0 + pstdev(finish_values)) if len(finish_values) >= 2 else 1.0
        venue_map = _weighted_counts((str(row.get("course", "")).strip() for row in ordered), weights)
        surface_map = _weighted_counts((_surface_from_distance_field(str(row.get("distance", ""))) for row in ordered), weights)
        jockey_map = _weighted_counts((str(row.get("jockey", "")).strip() for row in ordered), weights)
        track_condition_map = _weighted_counts(
            (str(row.get("track_condition", "")).strip() for row in ordered),
            weights,
        )
        latest_history_date = str(ordered[0].get("date", "")).strip() if ordered else ""

        summaries[key] = {
            "avg_finish": avg_finish,
            "avg_last3f": avg_last3f,
            "avg_popularity": avg_popularity,
            "avg_weight": avg_weight,
            "avg_distance": avg_distance,
            "avg_odds": avg_odds,
            "recent_odds": recent_odds or avg_odds,
            "prior_odds": prior_odds or avg_odds,
            "odds_trend": odds_trend,
            "odds_volatility": odds_volatility,
            "speed_score": speed_score,
            "front_rate": front_rate,
            "win_rate": win_rate,
            "top3_rate": top3_rate,
            "consistency": consistency,
            "history_count": float(len(ordered)),
            "best_finish": min(finish_values) if finish_values else 10.0,
            "venue_map": venue_map,
            "surface_map": surface_map,
            "jockey_map": jockey_map,
            "track_condition_map": track_condition_map,
            "latest_history_date": latest_history_date,
        }
    return summaries


def summarize_live_odds_rows(
    rows: Iterable[dict[str, str]],
) -> dict[tuple[str, str], dict[str, float | str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        race_id = str(row.get("race_id", "")).strip()
        horse_number = str(row.get("horse_number", "")).strip()
        if not race_id or not horse_number:
            continue
        grouped[(race_id, horse_number)].append(row)

    summaries: dict[tuple[str, str], dict[str, float | str]] = {}
    for key, group_rows in grouped.items():
        ordered = sorted(group_rows, key=lambda item: _parse_timestamp(str(item.get("captured_at", "")).strip()))
        odds_points = [
            (
                _to_float(row.get("current_odds"), 0.0),
                _parse_timestamp(str(row.get("captured_at", "")).strip()),
            )
            for row in ordered
            if _to_float(row.get("current_odds"), 0.0) > 0
        ]
        popularity_points = [
            _to_float(row.get("current_popularity"), 0.0)
            for row in ordered
            if _to_float(row.get("current_popularity"), 0.0) > 0
        ]

        odds_values = [value for value, _ in odds_points]
        odds_first = odds_values[0] if odds_values else 0.0
        odds_latest = odds_values[-1] if odds_values else 0.0
        odds_min = min(odds_values) if odds_values else 0.0
        odds_max = max(odds_values) if odds_values else 0.0
        odds_range_ratio = ((odds_max - odds_min) / odds_latest) if odds_latest > 0 else 0.0
        odds_slope_full = _relative_slope(odds_points)
        odds_slope_recent = _relative_slope(odds_points[-3:])
        popularity_latest = popularity_points[-1] if popularity_points else 0.0
        popularity_first = popularity_points[0] if popularity_points else popularity_latest
        popularity_change = popularity_latest - popularity_first if popularity_latest > 0 else 0.0
        span_minutes = _minutes_span(odds_points)

        summaries[key] = {
            "odds_snapshot_count": float(len(odds_points)),
            "odds_first": odds_first,
            "odds_latest": odds_latest,
            "odds_min": odds_min,
            "odds_max": odds_max,
            "odds_range_ratio": max(0.0, odds_range_ratio),
            "odds_slope_recent": odds_slope_recent,
            "odds_slope_full": odds_slope_full,
            "popularity_latest": popularity_latest,
            "popularity_change": popularity_change,
            "odds_span_minutes": span_minutes,
            "captured_at_latest": ordered[-1].get("captured_at", "") if ordered else "",
        }
    return summaries


def build_feature_row(
    current: dict[str, str],
    history_summary: dict[str, float | str],
    live_summary: dict[str, float | str] | None = None,
) -> dict[str, object]:
    live_summary = live_summary or {}
    target_track = str(current.get("target_track", "")).strip()
    target_surface = str(current.get("target_surface", "")).strip()
    target_distance = _to_float(current.get("target_distance"), 0.0)
    target_weather = str(current.get("target_weather", "")).strip()
    target_track_condition = str(current.get("target_track_condition", "")).strip()
    target_conditions_captured_at = str(current.get("target_conditions_captured_at", "")).strip()
    current_jockey = str(current.get("current_jockey", "")).strip()
    horse_country = _normalize_country_code(str(current.get("horse_country", "")).strip())
    assigned_weight = _to_float(current.get("assigned_weight"), DEFAULT_WEIGHT)
    current_odds = _to_float(current.get("current_odds") or current.get("win_odds"), 0.0)

    venue_match = _map_score(history_summary.get("venue_map"), target_track)
    surface_match = _map_score(history_summary.get("surface_map"), target_surface)
    jockey_match = _map_score(history_summary.get("jockey_map"), current_jockey)
    distance_fit = _distance_fit_score(history_summary.get("avg_distance", 0.0), target_distance)
    track_condition_match = _condition_match_score(
        history_summary.get("track_condition_map"),
        target_track_condition,
    )

    avg_finish = _to_float(history_summary.get("avg_finish"), 10.0)
    avg_last3f = _to_float(history_summary.get("avg_last3f"), DEFAULT_LAST3F)
    avg_popularity = _to_float(history_summary.get("avg_popularity"), 10.0)
    avg_weight = _to_float(history_summary.get("avg_weight"), DEFAULT_WEIGHT)
    avg_odds = _to_float(history_summary.get("avg_odds"), 0.0)
    odds_trend = _to_float(history_summary.get("odds_trend"), 0.0)
    odds_volatility = _to_float(history_summary.get("odds_volatility"), 0.0)
    speed_score = _to_float(history_summary.get("speed_score"), 0.0)
    win_rate = _to_float(history_summary.get("win_rate"), 0.0)
    top3_rate = _to_float(history_summary.get("top3_rate"), 0.0)
    front_rate = _to_float(history_summary.get("front_rate"), 0.5)
    consistency = _to_float(history_summary.get("consistency"), 0.5)
    history_count = _to_float(history_summary.get("history_count"), 0.0)
    current_popularity = _to_float(current.get("current_popularity"), avg_popularity if avg_popularity > 0 else 10.0)
    popularity_gap = (current_popularity - avg_popularity) if avg_popularity > 0 else 0.0
    days_since_last_run = _days_between(
        str(history_summary.get("latest_history_date", "")).strip(),
        str(current.get("target_race_date", "")).strip(),
    )
    odds_first = _to_float(live_summary.get("odds_first"), current_odds)
    odds_latest = _to_float(live_summary.get("odds_latest"), current_odds)
    odds_min = _to_float(live_summary.get("odds_min"), current_odds)
    odds_max = _to_float(live_summary.get("odds_max"), current_odds)
    odds_range_ratio = _to_float(live_summary.get("odds_range_ratio"), 0.0)
    odds_slope_recent = _to_float(live_summary.get("odds_slope_recent"), 0.0)
    odds_slope_full = _to_float(live_summary.get("odds_slope_full"), 0.0)
    popularity_latest = _to_float(live_summary.get("popularity_latest"), current_popularity)
    popularity_change = _to_float(live_summary.get("popularity_change"), 0.0)
    odds_snapshot_count = _to_float(live_summary.get("odds_snapshot_count"), 0.0)
    odds_span_minutes = _to_float(live_summary.get("odds_span_minutes"), 0.0)

    finish_strength = 1.0 / max(avg_finish, 1.0)
    closing_strength = max(0.0, DEFAULT_LAST3F - avg_last3f) / 3.0
    is_overseas_race = _is_overseas_track(target_track)
    country_value_score = _country_value_score(horse_country, is_overseas=is_overseas_race)
    market_support_base = (1.0 / current_odds) if current_odds > 0 else 0.0
    market_support = _clamp(market_support_base * (1.0 + country_value_score), 0.0, 1.0)
    weight_delta = assigned_weight - avg_weight

    ability_score = (
        0.34 * finish_strength
        + 0.18 * win_rate
        + 0.18 * top3_rate
        + 0.18 * speed_score
        + 0.12 * consistency
    )
    course_score = (
        0.40 * venue_match
        + 0.20 * surface_match
        + 0.20 * distance_fit
        + 0.20 * track_condition_match
    )
    track_bias = track_bias_adjustment(
        track=target_track,
        surface=target_surface,
        distance=target_distance,
        track_condition=target_track_condition,
        frame_number=str(current.get("frame_number", "")).strip(),
        front_rate=front_rate,
    )
    track_bias_score = _to_float(track_bias.get("track_bias_score"), 0.0)
    track_bias_strength = _to_float(track_bias.get("track_bias_strength"), 0.0)
    pace_score = _clamp(
        (0.55 * front_rate + 0.45 * closing_strength)
        + (track_bias_strength * track_bias_score),
        0.0,
        1.5,
    )
    weight_score = max(-1.5, min(1.5, -weight_delta / 3.0))
    jockey_score = 0.65 * jockey_match + 0.35 * win_rate

    return {
        "race_id": str(current.get("race_id", "")).strip(),
        "horse_id": str(current.get("horse_id", "")).strip(),
        "horse_name": str(current.get("horse_name", "")).strip(),
        "frame_number": str(current.get("frame_number", "")).strip(),
        "horse_number": str(current.get("horse_number", "")).strip(),
        "current_jockey": current_jockey,
        "assigned_weight": _fmt_float(assigned_weight),
        "current_odds": _fmt_float(current_odds),
        "current_popularity": str(current.get("current_popularity", "")).strip(),
        "target_track": target_track,
        "target_race_date": str(current.get("target_race_date", "")).strip(),
        "target_race_number": str(current.get("target_race_number", "")).strip(),
        "target_surface": target_surface,
        "target_distance": _fmt_float(target_distance),
        "target_weather": target_weather,
        "target_track_condition": target_track_condition,
        "target_conditions_captured_at": target_conditions_captured_at,
        "horse_country": horse_country,
        "is_overseas_race": int(is_overseas_race),
        "country_value_score": round(country_value_score, 4),
        "history_count": int(history_count),
        "avg_finish": _fmt_float(avg_finish),
        "avg_last3f": _fmt_float(avg_last3f),
        "avg_popularity": _fmt_float(avg_popularity),
        "avg_weight": _fmt_float(avg_weight),
        "avg_odds": _fmt_float(avg_odds),
        "current_popularity_score": _fmt_float(current_popularity),
        "popularity_gap": _fmt_float(popularity_gap),
        "days_since_last_run": str(days_since_last_run),
        "odds_history_trend": _fmt_float(odds_trend),
        "odds_volatility": _fmt_float(odds_volatility),
        "odds_snapshot_count": str(int(odds_snapshot_count)),
        "odds_first": _fmt_float(odds_first),
        "odds_latest": _fmt_float(odds_latest),
        "odds_min": _fmt_float(odds_min),
        "odds_max": _fmt_float(odds_max),
        "odds_range_ratio": _fmt_float(odds_range_ratio),
        "odds_slope_recent": _fmt_float(odds_slope_recent),
        "odds_slope_full": _fmt_float(odds_slope_full),
        "popularity_latest": _fmt_float(popularity_latest),
        "popularity_change": _fmt_float(popularity_change),
        "odds_span_minutes": _fmt_float(odds_span_minutes),
        "speed_score": round(speed_score, 4),
        "front_rate": round(front_rate, 4),
        "closing_strength": round(closing_strength, 4),
        "consistency": round(consistency, 4),
        "track_bias_score": round(track_bias_score, 4),
        "track_bias_style": str(track_bias.get("track_bias_style", "neutral")),
        "track_bias_strength": round(track_bias_strength, 4),
        "track_bias_frame": round(_to_float(track_bias.get("track_bias_frame"), 0.0), 4),
        "ability_score": round(ability_score, 4),
        "course_score": round(course_score, 4),
        "track_condition_score": round(track_condition_match, 4),
        "pace_score": round(pace_score, 4),
        "weight_score": round(weight_score, 4),
        "jockey_score": round(jockey_score, 4),
        "market_support": round(market_support, 4),
        "market_support_base": round(market_support_base, 4),
    }


def _weighted_mean(values: Sequence[float], weights: Sequence[float], *, default: float) -> float:
    total_weight = 0.0
    total = 0.0
    for value, weight in zip(values, weights):
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return default
    return total / total_weight


def _weighted_positive_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    filtered_values: list[float] = []
    filtered_weights: list[float] = []
    for value, weight in zip(values, weights):
        if value <= 0:
            continue
        filtered_values.append(value)
        filtered_weights.append(weight)
    if not filtered_weights:
        return 0.0
    return _weighted_mean(filtered_values, filtered_weights, default=0.0)


def _weighted_counts(values: Iterable[str], weights: Sequence[float]) -> dict[str, float]:
    counts: dict[str, float] = defaultdict(float)
    for value, weight in zip(values, weights):
        normalized = value.strip()
        if not normalized:
            continue
        counts[normalized] += weight
    return dict(counts)


def _distance_speed_score(row: dict[str, str]) -> float:
    distance = _to_float(row.get("distance"), 0.0)
    time_value = _to_float(row.get("time"), 0.0)
    if distance <= 0 or time_value <= 0:
        return 0.0
    meters_per_second = distance / time_value
    return max(0.0, (meters_per_second - 14.0) / 3.0)


def _distance_fit_score(avg_distance: float | str, target_distance: float) -> float:
    avg = _to_float(avg_distance, 0.0)
    if avg <= 0 or target_distance <= 0:
        return 0.5
    gap = abs(avg - target_distance)
    return max(0.0, 1.0 - (gap / 1200.0))


def _front_running_score(passing_order: str) -> float:
    if not passing_order:
        return 0.5
    numbers = [int(part) for part in passing_order.replace("→", "-").split("-") if part.strip().isdigit()]
    if not numbers:
        return 0.5
    last_corner = max(1, min(numbers[-1], 18))
    return max(0.0, min(1.0, 1.0 - ((last_corner - 1) / 17.0)))


def _surface_from_distance_field(value: str) -> str:
    if "ダ" in value:
        return "ダート"
    if "障" in value:
        return "障害"
    if "芝" in value:
        return "芝"
    return ""


def _map_score(value: object, key: str) -> float:
    if not isinstance(value, dict) or not key:
        return 0.5
    total = sum(float(v) for v in value.values()) or 1.0
    return float(value.get(key, 0.0)) / total


def _condition_match_score(value: object, key: str) -> float:
    if not isinstance(value, dict) or not value or not key:
        return 0.5
    condition_order = {"良": 0, "稍重": 1, "重": 2, "不良": 3}
    target_index = condition_order.get(key)
    if target_index is None:
        return 0.5

    similarity_by_gap = {0: 1.0, 1: 0.70, 2: 0.35, 3: 0.10}
    weighted_similarity = 0.0
    total = 0.0
    for condition, count_value in value.items():
        condition_index = condition_order.get(str(condition))
        if condition_index is None:
            continue
        count = float(count_value)
        weighted_similarity += similarity_by_gap[abs(target_index - condition_index)] * count
        total += count
    return weighted_similarity / total if total > 0 else 0.5


def _odds_trend(odds_values: Sequence[float]) -> float:
    positive = [value for value in odds_values if value > 0]
    if len(positive) < 2:
        return 0.0
    recent = sum(positive[:2]) / min(len(positive[:2]), 2)
    prior_slice = positive[2:] or positive[1:]
    prior = sum(prior_slice) / max(len(prior_slice), 1)
    if prior <= 0:
        return 0.0
    return max(-0.4, min(0.4, (recent - prior) / prior))


def _odds_volatility(odds_values: Sequence[float]) -> float:
    positive = [value for value in odds_values if value > 0]
    if len(positive) < 2:
        return 0.0
    average = sum(positive) / len(positive)
    if average <= 0:
        return 0.0
    return max(0.0, min(1.0, pstdev(positive) / average))


def _days_between(start: str, end: str) -> int:
    try:
        start_date = _dt.date.fromisoformat(start)
        end_date = _dt.date.fromisoformat(end)
    except ValueError:
        return 35
    return max(0, (end_date - start_date).days)


def _parse_timestamp(value: str) -> _dt.datetime:
    if not value:
        return _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(normalized)
    except ValueError:
        return _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _minutes_span(odds_points: Sequence[tuple[float, _dt.datetime]]) -> float:
    if len(odds_points) < 2:
        return 0.0
    start = odds_points[0][1]
    end = odds_points[-1][1]
    return max(0.0, (end - start).total_seconds() / 60.0)


def _relative_slope(points: Sequence[tuple[float, _dt.datetime]]) -> float:
    if len(points) < 2:
        return 0.0
    start_value, start_time = points[0]
    end_value, end_time = points[-1]
    if start_value <= 0:
        return 0.0
    hours = max((end_time - start_time).total_seconds() / 3600.0, 1e-6)
    relative_change = (end_value - start_value) / start_value
    return max(-0.8, min(0.8, relative_change / hours))


def _recency_weights(length: int) -> list[float]:
    weights = list(DEFAULT_RECENCY_WEIGHTS[:length])
    if len(weights) < length:
        weights.extend([DEFAULT_RECENCY_WEIGHTS[-1]] * (length - len(weights)))
    return weights


def _fmt_float(value: float) -> str:
    if math.isfinite(value):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return "0"


def _normalize_country_code(value: str) -> str:
    normalized = "".join(ch for ch in value.upper() if "A" <= ch <= "Z")
    return normalized[:3] if normalized else ""


def _is_overseas_track(track: str) -> bool:
    normalized = track.strip()
    if not normalized:
        return False
    return normalized not in DOMESTIC_TRACKS


def _country_value_score(horse_country: str, *, is_overseas: bool) -> float:
    if not is_overseas:
        return 0.0

    if horse_country in {"JPN", "JRA", "JP"}:
        return -0.14
    if horse_country in {"HK"}:
        return 0.08
    if horse_country in {"AUS", "NZ", "GB", "IRE", "USA", "FR", "GER", "UAE", "CAN", "SAF", "ITY"}:
        return 0.06
    if horse_country:
        return 0.03
    return 0.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 999


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
