from __future__ import annotations

from collections import defaultdict


LiveOddsRow = dict[str, object]
RaceOddsLookup = dict[tuple[str, str], LiveOddsRow]


def build_live_odds_lookup(
    odds_rows: list[dict[str, object]] | list[dict[str, str]],
) -> dict[str, RaceOddsLookup]:
    """Index live odds without combining rows from different complete snapshots.

    Snapshot-aware rows are restricted to the latest complete snapshot for each
    race. Legacy rows without snapshot metadata retain the previous behavior of
    selecting the latest row for each bet type and combination.
    """
    rows_by_race: dict[str, list[LiveOddsRow]] = defaultdict(list)
    for row in odds_rows:
        race_id = str(row.get("race_id", "")).strip()
        if race_id:
            rows_by_race[race_id].append(dict(row))

    by_race: dict[str, RaceOddsLookup] = defaultdict(dict)
    for race_id, race_rows in rows_by_race.items():
        selected_rows = _latest_complete_snapshot_rows(race_rows)
        for row in selected_rows:
            bet_type = str(row.get("bet_type", "")).strip()
            combination = str(row.get("combination", "")).strip()
            if not bet_type or not combination:
                continue

            key = (bet_type, combination)
            current = by_race[race_id].get(key)
            if current and str(current.get("captured_at", "")) > str(row.get("captured_at", "")):
                continue
            by_race[race_id][key] = dict(row)
    return dict(by_race)


def _latest_complete_snapshot_rows(race_rows: list[LiveOddsRow]) -> list[LiveOddsRow]:
    snapshot_rows = [
        row
        for row in race_rows
        if str(row.get("snapshot_id", "")).strip() and "snapshot_complete" in row
    ]
    if not snapshot_rows:
        return race_rows

    complete_snapshot_ids = {
        str(row.get("snapshot_id", "")).strip()
        for row in snapshot_rows
        if _is_true(row.get("snapshot_complete"))
    }
    if not complete_snapshot_ids:
        return []

    latest_snapshot_id = max(
        complete_snapshot_ids,
        key=lambda snapshot_id: max(
            (str(row.get("captured_at", "")), index)
            for index, row in enumerate(snapshot_rows)
            if str(row.get("snapshot_id", "")).strip() == snapshot_id
        ),
    )
    return [
        row
        for row in snapshot_rows
        if str(row.get("snapshot_id", "")).strip() == latest_snapshot_id
    ]


def _is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "complete"}


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
