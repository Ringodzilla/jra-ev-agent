from __future__ import annotations

from collections import defaultdict
from itertools import permutations
import re


BET_TYPES = (
    "win",
    "place",
    "wide",
    "wakuren",
    "umaren",
    "umatan",
    "sanrenpuku",
    "sanrentan",
)
UNORDERED_BET_TYPES = {"wide", "wakuren", "umaren", "sanrenpuku"}
ARITY = {
    "win": 1,
    "place": 1,
    "wide": 2,
    "wakuren": 2,
    "umaren": 2,
    "umatan": 2,
    "sanrenpuku": 3,
    "sanrentan": 3,
}


def build_candidate_evaluations(
    ev_rows: list[dict[str, object]],
    combo_odds: list[dict[str, object]] | list[dict[str, str]],
) -> dict[str, object]:
    """Evaluate every supplied official combination with one canonical probability lineage."""
    errors: list[str] = []
    duplicate_keys: list[str] = []
    seen_keys: set[str] = set()
    snapshots_by_race: dict[str, set[str]] = defaultdict(set)
    captured_times_by_race: dict[str, set[str]] = defaultdict(set)
    for row in combo_odds:
        race_id = str(row.get("race_id", "")).strip()
        snapshot_id = str(row.get("snapshot_id", "")).strip()
        captured_at = str(row.get("captured_at", "")).strip()
        if snapshot_id:
            snapshots_by_race[race_id].add(snapshot_id)
        if captured_at:
            captured_times_by_race[race_id].add(captured_at)
    for race_id, snapshots in snapshots_by_race.items():
        if len(snapshots) > 1:
            errors.append(f"combo_odds contains multiple snapshot_id values: {race_id}")
    for race_id, captured_times in captured_times_by_race.items():
        if len(captured_times) > 1:
            errors.append(f"combo_odds contains multiple captured_at values: {race_id}")

    probability_rows: dict[str, dict[int, float]] = defaultdict(dict)
    frame_rows: dict[str, dict[int, int]] = defaultdict(dict)
    for row in ev_rows:
        race_id = str(row.get("race_id", "")).strip()
        horse_number = _to_int(row.get("horse_number"))
        probability = _to_float(row.get("win_prob", row.get("final")))
        if not race_id or horse_number <= 0 or probability <= 0.0:
            errors.append("ev_rows contains an invalid race_id, horse_number, or win probability")
            continue
        if horse_number in probability_rows[race_id]:
            errors.append(f"duplicate probability row: {race_id}|{horse_number}")
            continue
        probability_rows[race_id][horse_number] = probability

    for race_id, probabilities in probability_rows.items():
        field_size = len(probabilities)
        for row in ev_rows:
            if str(row.get("race_id", "")).strip() != race_id:
                continue
            horse_number = _to_int(row.get("horse_number"))
            if horse_number in probabilities:
                frame_rows[race_id][horse_number] = _resolve_frame_number(row, field_size=field_size)
        if abs(sum(probabilities.values()) - 1.0) > 1e-9:
            errors.append(f"win probabilities do not sum to 1: {race_id}")

    probability_cache = {
        race_id: _probability_tables(probabilities, frame_rows[race_id])
        for race_id, probabilities in probability_rows.items()
    }
    candidates: list[dict[str, object]] = []
    for odds_row in combo_odds:
        race_id = str(odds_row.get("race_id", "")).strip()
        bet_type = str(odds_row.get("bet_type", "")).strip()
        try:
            combination = canonical_combination(bet_type, odds_row.get("combination"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        key = f"{race_id}|{bet_type}|{combination}"
        if key in seen_keys:
            duplicate_keys.append(key)
            continue
        seen_keys.add(key)
        if race_id not in probability_cache:
            errors.append(f"official odds has no probability race: {race_id}")
            continue
        official_odds = _to_float(odds_row.get("odds_min") or odds_row.get("odds"))
        if official_odds <= 0.0:
            errors.append(f"official odds is invalid: {key}")
            continue
        hit_prob = _lookup_probability(
            probability_cache[race_id],
            bet_type=bet_type,
            combination=combination,
        )
        if hit_prob is None:
            errors.append(f"combination is not in the probability universe: {key}")
            continue
        candidates.append(
            {
                "key": key,
                "race_id": race_id,
                "bet_type": bet_type,
                "canonical_combination": combination,
                "snapshot_id": str(odds_row.get("snapshot_id", "")).strip(),
                "captured_at": str(odds_row.get("captured_at", "")).strip(),
                "hit_prob": hit_prob,
                "official_odds": official_odds,
                "official_odds_max": _to_float(odds_row.get("odds_max")) or official_odds,
                "ev": hit_prob * official_odds,
            }
        )

    if duplicate_keys:
        errors.append("duplicate canonical candidate keys")
    status = "OK" if not errors else "NG"
    return {
        "candidate_evaluations": candidates if status == "OK" else [],
        "validation": {
            "status": status,
            "input_odds_count": len(combo_odds),
            "output_candidate_count": len(candidates) if status == "OK" else 0,
            "bet_types": sorted({str(row.get("bet_type", "")).strip() for row in combo_odds}),
            "snapshot_ids_by_race": {
                race_id: sorted(values) for race_id, values in sorted(snapshots_by_race.items())
            },
            "captured_at_values_by_race": {
                race_id: sorted(values) for race_id, values in sorted(captured_times_by_race.items())
            },
            "duplicate_keys": sorted(set(duplicate_keys)),
            "errors": errors,
        },
    }


def canonical_combination(bet_type: str, value: object) -> str:
    if bet_type not in ARITY:
        raise ValueError(f"unsupported bet type: {bet_type}")
    numbers = tuple(int(part) for part in re.findall(r"\d+", str(value)))
    if len(numbers) != ARITY[bet_type] or any(number <= 0 for number in numbers):
        raise ValueError(f"invalid combination for {bet_type}: {value}")
    if len(set(numbers)) != len(numbers) and bet_type != "wakuren":
        raise ValueError(f"duplicate leg in combination for {bet_type}: {value}")
    if bet_type in UNORDERED_BET_TYPES:
        numbers = tuple(sorted(numbers))
    separator = ">" if bet_type in {"umatan", "sanrentan"} else "-"
    return separator.join(str(number) for number in numbers)


def _probability_tables(
    probabilities: dict[int, float],
    frames: dict[int, int],
) -> dict[str, dict[str, float]]:
    numbers = tuple(sorted(probabilities))
    place_slots = 2 if len(numbers) <= 7 else 3
    top2_orders = {
        order: _ordered_probability(order, probabilities)
        for order in permutations(numbers, 2)
    }
    top3_orders = {
        order: _ordered_probability(order, probabilities)
        for order in permutations(numbers, 3)
    }
    place_orders = top2_orders if place_slots == 2 else top3_orders
    place = {
        str(number): sum(probability for order, probability in place_orders.items() if number in order)
        for number in numbers
    }
    wide: dict[str, float] = {}
    for left_index, left in enumerate(numbers):
        for right in numbers[left_index + 1 :]:
            key = f"{left}-{right}"
            wide[key] = sum(
                probability
                for order, probability in place_orders.items()
                if left in order and right in order
            )
    umaren: dict[str, float] = {}
    umatan: dict[str, float] = {}
    wakuren: dict[str, float] = defaultdict(float)
    for order, probability in top2_orders.items():
        umatan[f"{order[0]}>{order[1]}"] = probability
        pair = tuple(sorted(order))
        pair_key = f"{pair[0]}-{pair[1]}"
        umaren[pair_key] = umaren.get(pair_key, 0.0) + probability
        frame_pair = tuple(sorted((frames[order[0]], frames[order[1]])))
        wakuren[f"{frame_pair[0]}-{frame_pair[1]}"] += probability
    sanrenpuku: dict[str, float] = defaultdict(float)
    sanrentan: dict[str, float] = {}
    for order, probability in top3_orders.items():
        sanrentan[f"{order[0]}>{order[1]}>{order[2]}"] = probability
        combo = tuple(sorted(order))
        sanrenpuku[f"{combo[0]}-{combo[1]}-{combo[2]}"] += probability
    return {
        "win": {str(number): probabilities[number] for number in numbers},
        "place": place,
        "wide": wide,
        "wakuren": dict(wakuren),
        "umaren": umaren,
        "umatan": umatan,
        "sanrenpuku": dict(sanrenpuku),
        "sanrentan": sanrentan,
    }


def _lookup_probability(
    tables: dict[str, dict[str, float]],
    *,
    bet_type: str,
    combination: str,
) -> float | None:
    return tables.get(bet_type, {}).get(combination)


def _ordered_probability(order: tuple[int, ...], probabilities: dict[int, float]) -> float:
    remaining = 1.0
    probability = 1.0
    for horse_number in order:
        horse_probability = probabilities[horse_number]
        if remaining <= 0.0 or horse_probability >= remaining:
            return 0.0
        probability *= horse_probability / remaining
        remaining -= horse_probability
    return probability


def _resolve_frame_number(row: dict[str, object], *, field_size: int) -> int:
    explicit = _to_int(row.get("frame_number"))
    if explicit > 0:
        return explicit
    horse_number = _to_int(row.get("horse_number"))
    if field_size <= 8:
        return min(horse_number, 8)
    base, remainder = divmod(field_size, 8)
    slots = [base for _ in range(8)]
    for index in range(8 - remainder, 8):
        slots[index] += 1
    current = 1
    for frame_number, slot_count in enumerate(slots, start=1):
        if current <= horse_number < current + slot_count:
            return frame_number
        current += slot_count
    return min(horse_number, 8)


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
