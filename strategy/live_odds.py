from __future__ import annotations

from collections import defaultdict


LiveOddsRow = dict[str, object]
RaceOddsLookup = dict[tuple[str, str], LiveOddsRow]


def build_live_odds_lookup(
    odds_rows: list[dict[str, object]] | list[dict[str, str]],
) -> dict[str, RaceOddsLookup]:
    """Index the latest odds snapshot by race, bet type, and combination."""
    by_race: dict[str, RaceOddsLookup] = defaultdict(dict)
    for row in odds_rows:
        race_id = str(row.get("race_id", "")).strip()
        bet_type = str(row.get("bet_type", "")).strip()
        combination = str(row.get("combination", "")).strip()
        if not race_id or not bet_type or not combination:
            continue

        key = (bet_type, combination)
        current = by_race[race_id].get(key)
        if current and str(current.get("captured_at", "")) > str(row.get("captured_at", "")):
            continue
        by_race[race_id][key] = dict(row)
    return dict(by_race)


def lookup_live_odds(live_odds: RaceOddsLookup, bet_type: str, values: list[str]) -> LiveOddsRow:
    return dict(live_odds.get((bet_type, live_combo_key(bet_type, values))) or {})


def live_combo_key(bet_type: str, values: list[str]) -> str:
    cleaned = [str(int(_to_float(value))) for value in values if _to_float(value) > 0]
    if not cleaned:
        return ""
    if bet_type in {"umatan", "sanrentan"}:
        return ">".join(cleaned)
    if bet_type in {"wide", "wakuren", "umaren", "sanrenpuku"}:
        return "-".join(sorted(cleaned, key=int))
    return cleaned[0]


def live_odds_value(row: LiveOddsRow) -> float:
    if not row:
        return 0.0
    return _to_float(row.get("odds_min") or row.get("odds"))


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
