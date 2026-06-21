from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from src.feature_engineering import build_feature_row, summarize_history_rows, summarize_live_odds_rows
from src.model import ModelWeights, estimate_win_probs


@dataclass
class EVWeights:
    ability: float = 0.42
    course: float = 0.14
    pace: float = 0.16
    weight: float = 0.08
    jockey: float = 0.08
    market: float = 0.12
    temperature: float = 1.15
    market_shrink: float = 0.25

    def to_model_weights(self) -> ModelWeights:
        return ModelWeights(
            ability=self.ability,
            course=self.course,
            pace=self.pace,
            weight=self.weight,
            jockey=self.jockey,
            market=self.market,
            temperature=self.temperature,
            market_shrink=self.market_shrink,
        )


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def build_feature_rows(
    rows: list[dict[str, str]],
    *,
    odds_snapshots: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    if not rows:
        return []

    normalized_rows: list[dict[str, str]] = []
    for idx, row in enumerate(rows, start=1):
        normalized = dict(row)
        normalized.setdefault("race_id", str(row.get("race_id") or "race_default"))
        normalized.setdefault("horse_id", str(row.get("horse_id") or row.get("horse_name") or f"horse_{idx}"))
        normalized.setdefault("horse_name", str(row.get("horse_name") or normalized["horse_id"]))
        normalized.setdefault("current_odds", str(row.get("current_odds") or row.get("odds") or ""))
        normalized.setdefault("current_jockey", str(row.get("current_jockey") or row.get("jockey") or ""))
        normalized.setdefault("assigned_weight", str(row.get("assigned_weight") or row.get("weight") or ""))
        normalized.setdefault("run_index", str(row.get("run_index") or "1"))
        normalized_rows.append(normalized)

    grouped_rows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in normalized_rows:
        grouped_rows[(str(row.get("race_id", "")).strip(), str(row.get("horse_id", "")).strip())].append(row)

    summaries = summarize_history_rows(normalized_rows, group_keys=("race_id", "horse_id"))
    live_summaries = summarize_live_odds_rows(odds_snapshots or [])
    feature_rows: list[dict[str, object]] = []
    for key in sorted(grouped_rows.keys()):
        current_rows = grouped_rows[key]
        current = sorted(current_rows, key=lambda item: _safe_int(item.get("run_index", "")))[0]
        summary = summaries[key]
        live_summary = live_summaries.get(
            (
                str(current.get("race_id", "")).strip(),
                str(current.get("horse_number", "")).strip(),
            ),
            {},
        )
        feature_rows.append(build_feature_row(current, summary, live_summary=live_summary))

    return feature_rows


def simulate_race_scenarios(feature_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in feature_rows:
        by_race[str(row["race_id"])].append(dict(row))

    simulated: list[dict[str, object]] = []
    for race_id in sorted(by_race.keys()):
        race_rows = by_race[race_id]
        front_rates = [_to_float(row.get("front_rate"), 0.5) for row in race_rows]
        ordered_front_rates = sorted(front_rates, reverse=True)
        max_front_rate = ordered_front_rates[0] if ordered_front_rates else 0.5
        front_weights = [math.exp(6.0 * (value - max_front_rate)) for value in front_rates]
        front_weight_total = sum(front_weights) or 1.0
        front_competitor_count = sum(1 for value in front_rates if value >= 0.68)
        front_density = sum(_to_float(row.get("front_rate"), 0.5) for row in race_rows) / max(len(race_rows), 1)
        high = _clamp(0.18 + max(0.0, front_density - 0.48) * 1.2, 0.15, 0.55)
        slow = _clamp(0.18 + max(0.0, 0.42 - front_density) * 1.2, 0.15, 0.55)
        mid = max(0.10, 1.0 - high - slow)
        total = high + mid + slow
        high /= total
        mid /= total
        slow /= total

        for row_index, row in enumerate(race_rows):
            front_rate = _to_float(row.get("front_rate"), 0.5)
            closing_strength = _to_float(row.get("closing_strength"), 0.0)
            ability_score = _to_float(row.get("ability_score"), 0.0)
            course_score = _to_float(row.get("course_score"), 0.0)
            consistency = _to_float(row.get("consistency"), 0.5)
            rank_index = ordered_front_rates.index(front_rate) if ordered_front_rates else 0
            relative_front_rank = 1.0 - (rank_index / max(len(race_rows) - 1, 1))
            solo_lead_score = front_weights[row_index] / front_weight_total

            high_fit = _clamp((0.65 * closing_strength) + (0.35 * (1.0 - front_rate)), 0.0, 1.5)
            mid_fit = _clamp(
                (0.40 * ability_score)
                + (0.15 * front_rate)
                + (0.25 * consistency)
                + (0.20 * relative_front_rank),
                0.0,
                1.5,
            )
            slow_fit = _clamp(
                (0.40 * front_rate)
                + (0.25 * course_score)
                + (0.15 * consistency)
                + (0.20 * solo_lead_score),
                0.0,
                1.5,
            )
            blended_pace = (high * high_fit) + (mid * mid_fit) + (slow * slow_fit)

            row["pace_high"] = _fmt(high_fit)
            row["pace_mid"] = _fmt(mid_fit)
            row["pace_slow"] = _fmt(slow_fit)
            row["pace_mix_high"] = _fmt(high)
            row["pace_mix_mid"] = _fmt(mid)
            row["pace_mix_slow"] = _fmt(slow)
            row["relative_front_rank"] = _fmt(relative_front_rank)
            row["solo_lead_score"] = _fmt(solo_lead_score)
            row["front_competitor_count"] = str(front_competitor_count)
            row["pace_score"] = _fmt(blended_pace)
            simulated.append(row)

    return simulated


def compute_ev(
    rows: list[dict[str, str]] | list[dict[str, object]],
    weights: EVWeights | None = None,
) -> list[dict[str, object]]:
    weights = weights or EVWeights()
    if not rows:
        return []

    feature_rows: list[dict[str, object]]
    first_row = rows[0]
    if "ability_score" in first_row:
        feature_rows = [dict(row) for row in rows]  # type: ignore[arg-type]
    else:
        feature_rows = build_feature_rows(rows)  # type: ignore[arg-type]

    if "pace_mix_high" not in feature_rows[0]:
        feature_rows = simulate_race_scenarios(feature_rows)

    return estimate_win_probs(feature_rows, weights=weights.to_model_weights())


def save_ev(rows: list[dict[str, object]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text("", encoding="utf-8")
        return

    keys = list(rows[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


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
