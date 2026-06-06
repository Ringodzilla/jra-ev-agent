from __future__ import annotations

from collections import defaultdict
from itertools import product
import math


WIN5_MODES = {"win5_under_10", "win5_compact", "win5_balanced", "win5_value"}


def generate_win5_plan(
    ev_rows: list[dict[str, object]],
    *,
    mode: str = "win5_compact",
    max_points: int | None = None,
    stake_yen_per_point: int = 100,
) -> dict[str, object]:
    race_groups = _ordered_race_groups(ev_rows)
    if len(race_groups) != 5:
        return {
            "status": "NG",
            "reason": f"WIN5 requires exactly 5 races; got {len(race_groups)}",
            "fix": "provide five race configs in WIN5 order",
            "bet_type": "win5",
            "tickets": [],
            "legs": [],
            "points": 0,
        }

    point_limit = max_points or _default_max_points(mode)
    race_summaries = [_race_summary(race_id, rows) for race_id, rows in race_groups]
    allocations = _candidate_allocations(mode, point_limit)
    best_allocation = max(
        allocations,
        key=lambda counts: _allocation_score(race_summaries, counts, mode=mode, point_limit=point_limit),
    )

    legs = [
        _build_leg(index + 1, race_summaries[index], best_allocation[index])
        for index in range(5)
    ]
    points = math.prod(len(leg["horses"]) for leg in legs)
    tickets = _build_tickets(legs, stake_yen_per_point)
    hit_prob = _formation_hit_prob(legs)
    popularity_mix = _popularity_mix(legs)

    return {
        "status": "OK",
        "reason": "WIN5 formation generated from win probabilities under point constraints",
        "fix": "",
        "bet_type": "win5",
        "mode": mode,
        "max_points": point_limit,
        "stake_yen_per_point": stake_yen_per_point,
        "points": points,
        "total_stake": points * stake_yen_per_point,
        "estimated_hit_prob": _fmt(hit_prob),
        "estimated_fair_odds": _fmt((1.0 / hit_prob) if hit_prob > 0 else 0.0),
        "legs": legs,
        "tickets": tickets,
        "single": [leg["horses"][0] for leg in legs],
        "fixed_legs": [leg for leg in legs if len(leg["horses"]) == 1],
        "spread_legs": [leg for leg in legs if len(leg["horses"]) > 1],
        "candidate_counts": {"win5": points},
        "bet_types_considered": ["win5"],
        "portfolio_summary": {
            "total_stake": points * stake_yen_per_point,
            "total_points": points,
            "estimated_hit_prob": _fmt(hit_prob),
            "estimated_fair_odds": _fmt((1.0 / hit_prob) if hit_prob > 0 else 0.0),
            "favorite_count": popularity_mix["favorite"],
            "mid_count": popularity_mix["mid"],
            "long_count": popularity_mix["long"],
        },
    }


def is_win5_mode(mode: str) -> bool:
    return mode in WIN5_MODES


def _default_max_points(mode: str) -> int:
    if mode == "win5_under_10":
        return 10
    if mode == "win5_balanced":
        return 500
    if mode == "win5_value":
        return 120
    return 60


def _ordered_race_groups(ev_rows: list[dict[str, object]]) -> list[tuple[str, list[dict[str, object]]]]:
    by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    order: list[str] = []
    for row in ev_rows:
        race_id = str(row.get("race_id", "")).strip()
        if not race_id:
            continue
        if race_id not in by_race:
            order.append(race_id)
        by_race[race_id].append(row)
    return [(race_id, by_race[race_id]) for race_id in order]


def _race_summary(race_id: str, rows: list[dict[str, object]]) -> dict[str, object]:
    ranked = sorted(rows, key=_horse_score, reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row["win5_rank"] = rank
    top_probs = [_to_float(row.get("win_prob")) for row in ranked[:5]]
    top1 = top_probs[0] if top_probs else 0.0
    top2 = top_probs[1] if len(top_probs) > 1 else 0.0
    top3 = top_probs[2] if len(top_probs) > 2 else 0.0
    entropy = _normalized_entropy([_to_float(row.get("win_prob")) for row in ranked])
    chaos = _clamp((entropy * 0.58) + ((1.0 - top1) * 0.32) + (min(top2 / max(top1, 1e-6), 1.0) * 0.10), 0.0, 1.0)
    return {
        "race_id": race_id,
        "race_label": _race_label(ranked[0] if ranked else {}),
        "ranked": ranked,
        "top1_prob": top1,
        "top2_prob": top2,
        "top3_prob": top3,
        "top3_coverage": top1 + top2 + top3,
        "entropy": entropy,
        "chaos": chaos,
    }


def _candidate_allocations(mode: str, point_limit: int) -> list[tuple[int, int, int, int, int]]:
    if mode == "win5_under_10":
        max_per_leg = 2
    elif mode == "win5_compact":
        max_per_leg = 3
    elif mode == "win5_balanced":
        max_per_leg = 5
    else:
        max_per_leg = 4

    allocations: list[tuple[int, int, int, int, int]] = []
    for counts in product(range(1, max_per_leg + 1), repeat=5):
        points = math.prod(counts)
        if points <= point_limit:
            allocations.append(tuple(int(count) for count in counts))
    return allocations or [(1, 1, 1, 1, 1)]


def _allocation_score(
    summaries: list[dict[str, object]],
    counts: tuple[int, int, int, int, int],
    *,
    mode: str,
    point_limit: int,
) -> float:
    coverages = []
    chaos_fit = 0.0
    value_bonus = 0.0
    for summary, count in zip(summaries, counts):
        ranked = list(summary["ranked"])
        selected = ranked[:count]
        coverage = sum(_to_float(row.get("win_prob")) for row in selected)
        coverages.append(max(coverage, 1e-9))
        chaos_fit += _to_float(summary["chaos"]) * math.log(count + 1.0)
        value_bonus += sum(max(0.0, _to_float(row.get("ev")) - 1.0) * 0.015 for row in selected)

    hit_prob = math.prod(coverages)
    points = math.prod(counts)
    point_efficiency = 1.0 - (points / max(point_limit, 1)) * 0.04
    fixed_bonus = counts.count(1) * (0.012 if mode == "win5_under_10" else 0.004)
    value_weight = 1.0 if mode == "win5_value" else 0.35
    return math.log(hit_prob) + (0.12 * chaos_fit) + (value_weight * value_bonus) + point_efficiency + fixed_bonus


def _build_leg(leg_number: int, summary: dict[str, object], count: int) -> dict[str, object]:
    ranked = list(summary["ranked"])
    selected = ranked[:count]
    coverage = sum(_to_float(row.get("win_prob")) for row in selected)
    return {
        "leg": leg_number,
        "race_id": str(summary["race_id"]),
        "race_label": str(summary["race_label"]),
        "chaos": _fmt(summary["chaos"]),
        "coverage": _fmt(coverage),
        "fixed": len(selected) == 1,
        "horses": [_horse_payload(row) for row in selected],
        "alternates": [_horse_payload(row) for row in ranked[count : count + 3]],
    }


def _build_tickets(legs: list[dict[str, object]], stake_yen_per_point: int) -> list[dict[str, object]]:
    combinations = product(*[[horse for horse in leg["horses"]] for leg in legs])
    tickets: list[dict[str, object]] = []
    for combo in combinations:
        hit_prob = math.prod(_to_float(horse.get("win_prob")) for horse in combo)
        tickets.append(
            {
                "bet_type": "win5",
                "horse_number": "-".join(str(horse.get("horse_number", "")) for horse in combo),
                "horse_name": " - ".join(str(horse.get("horse_name", "")) for horse in combo),
                "hit_prob": _fmt(hit_prob),
                "stake": stake_yen_per_point,
            }
        )
    return tickets


def _horse_payload(row: dict[str, object]) -> dict[str, object]:
    prob = _to_float(row.get("win_prob"))
    odds = _to_float(row.get("current_odds") or row.get("win_odds"))
    return {
        "horse_number": str(row.get("horse_number", "")),
        "horse_name": str(row.get("horse_name", "")),
        "win_prob": _fmt(prob),
        "current_odds": _fmt(odds),
        "ev": _fmt(_to_float(row.get("ev"))),
        "rank": int(_to_float(row.get("win5_rank"), 0.0)),
        "popularity": str(row.get("current_popularity") or row.get("popularity_latest") or ""),
    }


def _horse_score(row: dict[str, object]) -> tuple[float, float, float, float]:
    prob = _to_float(row.get("win_prob"))
    ev = _to_float(row.get("ev"))
    popularity = _to_float(row.get("current_popularity") or row.get("popularity_latest"), 99.0)
    odds_gap_ratio = abs(_to_float(row.get("odds_gap_ratio")))
    stability = 1.0 / (1.0 + odds_gap_ratio)
    return (prob * stability, prob, min(ev, 1.25), -popularity)


def _race_label(row: dict[str, object]) -> str:
    track = str(row.get("target_track", "")).strip()
    race_number = str(row.get("target_race_number", "")).strip()
    race_name = str(row.get("race_name", "")).strip()
    label = f"{track}{int(_to_float(race_number, 0.0))}R" if track and race_number else str(row.get("race_id", ""))
    return f"{label} {race_name}".strip() if race_name else label


def _formation_hit_prob(legs: list[dict[str, object]]) -> float:
    return math.prod(_to_float(leg.get("coverage")) for leg in legs)


def _popularity_mix(legs: list[dict[str, object]]) -> dict[str, int]:
    mix = {"favorite": 0, "mid": 0, "long": 0}
    for leg in legs:
        for horse in leg["horses"]:
            popularity = _to_float(horse.get("popularity"), 99.0)
            if popularity <= 3:
                mix["favorite"] += 1
            elif popularity <= 8:
                mix["mid"] += 1
            else:
                mix["long"] += 1
    return mix


def _normalized_entropy(probs: list[float]) -> float:
    values = [prob for prob in probs if prob > 0]
    if len(values) <= 1:
        return 0.0
    entropy = -sum(prob * math.log(prob) for prob in values)
    return _clamp(entropy / math.log(len(values)), 0.0, 1.0)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: object) -> str:
    return f"{_to_float(value):.6f}".rstrip("0").rstrip(".")
