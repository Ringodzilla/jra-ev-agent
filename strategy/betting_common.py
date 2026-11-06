from __future__ import annotations

from collections import defaultdict


MIN_WIN_EV = 1.05

MIN_ACTIONABLE_WIN_EV = 1.08

BET_TYPE_MARKET_SHRINK = {
    "win": 0.0,
    "place": 0.0,
    "wide": 0.0,
    "wakuren": 0.0,
    "umaren": 0.0,
    "umatan": 0.0,
    "sanrenpuku": 0.0,
    "sanrentan": 0.0,
}

def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")

def _combo_name(values: list[str], *, bet_type: str) -> str:
    separator = " → " if bet_type in {"umatan", "sanrentan"} else " - "
    return separator.join(values)

def _ticket_horse_numbers(ticket: dict[str, object]) -> set[str]:
    explicit = {str(value) for value in list(ticket.get("horse_numbers") or []) if str(value).strip()}
    if explicit:
        return explicit
    value = str(ticket.get("horse_number", "")).strip()
    normalized = value.replace(">", "-").replace("→", "-")
    return {part for part in normalized.split("-") if part.isdigit()}

def _dedupe_rows_by_horse_number(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for row in rows:
        horse_number = str(row.get("horse_number", "")).strip()
        if not horse_number or horse_number in seen:
            continue
        seen.add(horse_number)
        out.append(row)
    return out

def _axis_values(rows: list[dict[str, object]], key: str) -> str:
    values = [str(row.get(key, "")).strip() for row in rows if str(row.get(key, "")).strip()]
    return ",".join(values)

def _formation_axis_rows(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    return [
        {
            "horse_id": str(row.get("horse_id", "")),
            "horse_name": str(row.get("horse_name", "")),
            "horse_number": str(row.get("horse_number", "")),
        }
        for row in rows
    ]

def _pair_strings(names: list[str]) -> list[str]:
    cleaned = [name for name in names if name]
    out: list[str] = []
    for i in range(len(cleaned)):
        for j in range(i + 1, len(cleaned)):
            out.append(f"{cleaned[i]} - {cleaned[j]}")
    return out

def _unique_horse_names(rows: list[dict[str, object]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        name = str(row.get("horse_name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out

def _kelly_stake(
    *,
    probability: float,
    odds: float,
    bankroll_per_race: int,
    kelly_fraction: float,
    min_ev: float,
    max_fraction: float,
) -> int:
    if probability <= 0 or odds <= 1.0:
        return 0
    ev = probability * odds
    if ev <= 1.0:
        return 0

    full_kelly = ((odds * probability) - 1.0) / max(odds - 1.0, 1e-6)
    recommended_fraction = max(0.0, min(max_fraction, full_kelly * kelly_fraction))
    stake = int((bankroll_per_race * recommended_fraction) / 100) * 100
    if stake < 100:
        stake = 100 if ev >= min_ev else 0
    return stake

def _ticket_market_shrink(ticket: dict[str, object], *, bet_type: str, odds: float) -> float:
    """Keep model hit probability independent from market price."""
    _ = ticket, bet_type, odds
    return 0.0

def _ticket_selection_key(ticket: dict[str, object]) -> tuple[str, str]:
    return (
        str(ticket.get("bet_type", "")),
        str(ticket.get("horse_number", "")),
    )

def _selection_type_order(*, prefer_wide: bool) -> tuple[str, ...]:
    if prefer_wide:
        return ("wide", "place", "wakuren", "win", "umaren", "umatan", "sanrenpuku", "sanrentan")
    return ("win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan")

def _is_coverage_ticket(ticket: dict[str, object]) -> bool:
    return str(ticket.get("ticket_role", "")) == "coverage"

def _is_model_pair_coverage_ticket(ticket: dict[str, object]) -> bool:
    return str(ticket.get("coverage_reason", "")) in {
        "marked_core_pair_real_odds",
        "marked_core_trio_real_odds",
        "top_model_pair_real_odds",
        "marked_top5_pair_real_odds",
        "marked_top5_trio_real_odds",
        "win_ev_longshot_place_translation",
        "win_ev_longshot_wide_translation",
        "top3_rank_coverage",
    }

def _coverage_priority(ticket: dict[str, object]) -> int:
    reason = str(ticket.get("coverage_reason", ""))
    if reason in {"marked_core_pair_real_odds", "marked_core_trio_real_odds"}:
        return 4
    if reason in {
        "top_model_pair_real_odds",
        "top_model_combo_real_odds",
        "win_ev_longshot_place_translation",
        "win_ev_longshot_wide_translation",
    }:
        return 3
    if reason in {"marked_top5_pair_real_odds", "marked_top5_trio_real_odds"}:
        return 2
    return 0

def _prioritize_exotic_types(tickets: list[dict[str, object]]) -> list[dict[str, object]]:
    by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ticket in tickets:
        by_type[str(ticket.get("bet_type", ""))].append(ticket)

    out: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for bet_type in ("umaren", "umatan", "sanrenpuku", "sanrentan"):
        if by_type[bet_type]:
            ticket = by_type[bet_type][0]
            out.append(ticket)
            seen_ids.add(id(ticket))

    for ticket in tickets:
        if id(ticket) in seen_ids:
            continue
        out.append(ticket)
    return out

def _probability_band_from_row(row: dict[str, object]) -> str:
    odds = _to_float(row.get("current_odds"))
    popularity = _to_float(row.get("current_popularity") or row.get("popularity_latest"))
    if (popularity > 0 and popularity <= 3) or (odds > 0 and odds <= 6.0):
        return "favorite"
    if (popularity > 0 and popularity <= 8) or (odds > 0 and odds <= 15.0):
        return "contender"
    if (popularity > 0 and popularity <= 12) or (odds > 0 and odds <= 30.0):
        return "outsider"
    return "longshot"

def _frame_horse_summaries(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "horse_id": str(row.get("horse_id", "")),
            "horse_name": str(row.get("horse_name", "")),
            "horse_number": str(row.get("horse_number", "")),
            "win_prob": str(row.get("win_prob", "")),
        }
        for row in rows
    ]

def _resolve_frame_number(row: dict[str, object], *, field_size: int) -> str:
    frame_number = str(row.get("frame_number", "")).strip()
    if frame_number:
        return frame_number

    horse_number = int(_to_float(row.get("horse_number"), 0.0))
    if horse_number <= 0:
        return ""
    if field_size <= 8:
        return str(min(horse_number, 8))

    base = field_size // 8
    remainder = field_size % 8
    slots = [base for _ in range(8)]
    for idx in range(8 - remainder, 8):
        if 0 <= idx < 8:
            slots[idx] += 1

    current = 1
    for frame_idx, slot_count in enumerate(slots, start=1):
        if current <= horse_number < current + max(slot_count, 0):
            return str(frame_idx)
        current += max(slot_count, 0)
    return str(min(8, horse_number))

def _frame_sort_key(value: str) -> tuple[int, str]:
    number = int(_to_float(value, 999.0))
    return (number, value)

def _is_top_two_model_pair(ticket: dict[str, object], core: list[dict[str, object]]) -> bool:
    top_two = {str(row.get("horse_number", "")) for row in core[:2]}
    ticket_numbers = {str(number) for number in list(ticket.get("horse_numbers") or [])}
    return ticket_numbers == top_two

def _with_long_reason(row: dict[str, object], reason: str) -> dict[str, object]:
    out = dict(row)
    out["long_reason"] = reason
    return out

def _horse_summary(row: dict[str, object]) -> dict[str, object]:
    summary = {
        "race_id": str(row.get("race_id", "")),
        "horse_id": str(row.get("horse_id", "")),
        "horse_name": str(row.get("horse_name", "")),
        "horse_number": str(row.get("horse_number", "")),
        "win_prob": str(row.get("win_prob", "")),
        "place_prob": str(row.get("place_prob", "")),
        "ev": str(row.get("ev", "")),
        "ev_predicted": str(row.get("ev_predicted", "")),
        "current_odds": str(row.get("current_odds", "")),
        "predicted_odds": str(row.get("predicted_odds", "")),
        "predicted_odds_source": str(row.get("predicted_odds_source", "")),
        "model_score": str(row.get("model_score", "")),
        "probability_band": str(row.get("probability_band") or _probability_band_from_row(row)),
        "long_reason": str(row.get("long_reason", "")),
    }
    for key in (
        "front_value_candidate",
        "front_value_score",
        "front_value_reasons",
        "front_value_failure_reasons",
        "structural_win_prob",
        "structural_ev_current",
        "model_to_market_ratio",
        "market_prob",
    ):
        if key in row:
            summary[key] = row[key]
    return summary
