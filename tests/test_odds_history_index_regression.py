from copy import deepcopy

from strategy.betting import _index_odds_history
from strategy.live_odds import build_live_odds_lookup


def test_history_index_preserves_observations_and_separates_races_and_bet_types():
    rows = [
        {"race_id": " R1 ", "bet_type": " wide ", "combination": "2-1", "captured_at": "2026-09-12T06:30:00Z", "odds": "4"},
        {"race_id": "R1", "bet_type": "wide", "combination": "1-2", "captured_at": "2026-09-12T06:30:00Z", "odds": "4.1"},
        {"race_id": "R1", "bet_type": "wide", "combination": "1-2", "captured_at": "2026-09-12T06:20:00Z", "odds": "5"},
        {"race_id": "R2", "bet_type": "wide", "combination": "1-2", "captured_at": "2026-09-12T06:30:00Z", "odds": "8"},
        {"race_id": "R1", "bet_type": "umatan", "combination": "2>1", "captured_at": "2026-09-12T06:30:00Z", "odds": "9"},
        {"race_id": "R1", "bet_type": "umatan", "combination": "1>2", "captured_at": "2026-09-12T06:30:00Z", "odds": "10"},
    ]
    original = deepcopy(rows)
    indexed = _index_odds_history(rows)
    assert indexed == {
        ("R1", "wide", "1-2"): rows[:3],
        ("R2", "wide", "1-2"): [rows[3]],
        ("R1", "umatan", "2>1"): [rows[4]],
        ("R1", "umatan", "1>2"): [rows[5]],
    }
    assert rows == original


def test_history_index_omits_missing_race_unsupported_type_and_invalid_combinations():
    valid = {"race_id": "R1", "bet_type": "win", "combination": "1", "odds": "3"}
    malformed = [
        {},
        dict(valid, race_id=" "),
        dict(valid, bet_type="unknown"),
        dict(valid, bet_type="wide", combination="1-1"),
        dict(valid, bet_type="wide", combination="1"),
        dict(valid, combination=None),
        dict(valid, combination="bad"),
    ]
    assert _index_odds_history(malformed + [valid]) == {("R1", "win", "1"): [valid]}


def test_live_lookup_keeps_latest_duplicate_row_within_selected_snapshot():
    latest = {
        "race_id": "R1", "bet_type": "win", "combination": "1", "odds": "3",
        "snapshot_id": "S1", "snapshot_complete": True,
        "captured_at": "2026-09-12T06:30:00Z",
    }
    older = dict(latest, captured_at="2026-09-12T06:29:00Z", odds="4")
    other_race = dict(latest, race_id="R2", snapshot_id="S2", odds="8")
    original = deepcopy([latest, older, other_race])
    lookup = build_live_odds_lookup([latest, older, other_race])
    assert lookup["R1"][("win", "1")] == latest
    assert lookup["R2"][("win", "1")] == other_race
    assert [latest, older, other_race] == original
