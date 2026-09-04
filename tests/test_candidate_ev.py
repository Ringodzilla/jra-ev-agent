from __future__ import annotations

from itertools import combinations, permutations

import pytest

from analysis.candidate_ev import (
    _resolve_frame_number,
    _to_float,
    _to_int,
    build_candidate_evaluations,
    canonical_combination,
)
from src.agents.ev_calculator import EVCalculatorAgent


def _rows(count: int) -> list[dict[str, object]]:
    weights = list(range(1, count + 1))
    total = sum(weights)
    return [
        {
            "race_id": "R1",
            "horse_number": number,
            "horse_name": f"H{number}",
            "frame_number": number if count <= 8 else min(number, 8),
            "win_prob": weight / total,
        }
        for number, weight in enumerate(weights, start=1)
    ]


def _odds_row(bet_type: str, combination: str) -> dict[str, object]:
    return {
        "race_id": "R1",
        "bet_type": bet_type,
        "combination": combination,
        "odds": "10.0",
        "snapshot_id": "S1",
        "captured_at": "2026-08-30T03:51:06+00:00",
    }


def _complete_odds(count: int) -> list[dict[str, object]]:
    numbers = tuple(range(1, count + 1))
    rows = [_odds_row("win", str(number)) for number in numbers]
    rows += [_odds_row("place", str(number)) for number in numbers]
    rows += [_odds_row("wide", f"{left}-{right}") for left, right in combinations(numbers, 2)]
    rows += [_odds_row("umaren", f"{left}-{right}") for left, right in combinations(numbers, 2)]
    rows += [_odds_row("umatan", f"{left}>{right}") for left, right in permutations(numbers, 2)]
    rows += [_odds_row("sanrenpuku", "-".join(map(str, combo))) for combo in combinations(numbers, 3)]
    rows += [_odds_row("sanrentan", ">".join(map(str, order))) for order in permutations(numbers, 3)]
    frame_pairs = sorted(
        {
            tuple(sorted((min(left, 8), min(right, 8))))
            for left, right in combinations(numbers, 2)
        }
    )
    rows += [_odds_row("wakuren", f"{left}-{right}") for left, right in frame_pairs]
    return rows


def _by_type(result: dict[str, object], bet_type: str) -> list[dict[str, object]]:
    return [
        row
        for row in result["candidate_evaluations"]  # type: ignore[index]
        if row["bet_type"] == bet_type
    ]


def test_canonical_combination_sorts_only_unordered_bet_types() -> None:
    assert canonical_combination("wide", "9 - 4") == "4-9"
    assert canonical_combination("sanrenpuku", "9-4-7") == "4-7-9"
    assert canonical_combination("umatan", "9 → 4") == "9>4"
    assert canonical_combination("sanrentan", "9>4>7") == "9>4>7"


def test_all_eight_bet_types_use_one_plackett_luce_probability_universe_top2() -> None:
    result = build_candidate_evaluations(_rows(4), _complete_odds(4))

    assert result["validation"]["status"] == "OK"  # type: ignore[index]
    assert set(result["validation"]["bet_types"]) == {  # type: ignore[index]
        "win", "place", "wide", "wakuren", "umaren", "umatan", "sanrenpuku", "sanrentan"
    }
    expected_sums = {
        "win": 1.0,
        "place": 2.0,
        "wide": 1.0,
        "wakuren": 1.0,
        "umaren": 1.0,
        "umatan": 1.0,
        "sanrenpuku": 1.0,
        "sanrentan": 1.0,
    }
    for bet_type, expected in expected_sums.items():
        assert sum(row["hit_prob"] for row in _by_type(result, bet_type)) == pytest.approx(expected)
    umatan = {row["canonical_combination"]: row for row in _by_type(result, "umatan")}
    sanrentan = {row["canonical_combination"]: row for row in _by_type(result, "sanrentan")}
    assert umatan["4>3"]["hit_prob"] == pytest.approx(0.4 * (0.3 / 0.6))
    assert sanrentan["4>3>2"]["hit_prob"] == pytest.approx(0.4 * (0.3 / 0.6) * (0.2 / 0.3))
    assert all(row["key"] == f"R1|{row['bet_type']}|{row['canonical_combination']}" for row in result["candidate_evaluations"])
    assert all(row["ev"] == pytest.approx(row["hit_prob"] * row["official_odds"]) for row in result["candidate_evaluations"])
    assert all(row["snapshot_id"] == "S1" for row in result["candidate_evaluations"])
    assert all(row["captured_at"] == "2026-08-30T03:51:06+00:00" for row in result["candidate_evaluations"])


def test_place_and_wide_use_top3_for_eight_horse_field() -> None:
    odds = [row for row in _complete_odds(8) if row["bet_type"] in {"place", "wide"}]
    result = build_candidate_evaluations(_rows(8), odds)

    assert result["validation"]["status"] == "OK"  # type: ignore[index]
    assert sum(row["hit_prob"] for row in _by_type(result, "place")) == pytest.approx(3.0)
    assert sum(row["hit_prob"] for row in _by_type(result, "wide")) == pytest.approx(3.0)


def test_wakuren_aggregates_all_horse_pairs_in_the_same_frame() -> None:
    rows = _rows(9)
    odds = [_odds_row("wakuren", "8-8"), _odds_row("umaren", "8-9")]
    result = build_candidate_evaluations(rows, odds)
    evaluations = {row["bet_type"]: row for row in result["candidate_evaluations"]}

    assert result["validation"]["status"] == "OK"  # type: ignore[index]
    assert evaluations["wakuren"]["hit_prob"] == pytest.approx(evaluations["umaren"]["hit_prob"])


def test_duplicate_canonical_key_fails_closed() -> None:
    odds = [_odds_row("wide", "1-2"), _odds_row("wide", "2-1")]
    result = build_candidate_evaluations(_rows(4), odds)

    assert result["candidate_evaluations"] == []
    assert result["validation"]["status"] == "NG"  # type: ignore[index]
    assert result["validation"]["duplicate_keys"] == ["R1|wide|1-2"]  # type: ignore[index]


@pytest.mark.parametrize("field", ["snapshot_id", "captured_at"])
def test_mixed_official_odds_snapshots_fail_closed(field: str) -> None:
    odds = [_odds_row("win", "1"), _odds_row("win", "2")]
    odds[1][field] = "different"
    result = build_candidate_evaluations(_rows(4), odds)

    assert result["candidate_evaluations"] == []
    assert result["validation"]["status"] == "NG"  # type: ignore[index]
    assert any(field in error for error in result["validation"]["errors"])  # type: ignore[index]


def test_ev_calculator_run_remains_backward_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    ev_rows = _rows(4)
    monkeypatch.setattr("src.agents.ev_calculator.compute_ev", lambda rows, weights: ev_rows)
    agent = EVCalculatorAgent()

    assert agent.run([{"x": 1}]) == {"ev_rows": ev_rows}
    with_candidates = agent.run([{"x": 1}], combo_odds=[_odds_row("win", "4")])
    assert with_candidates["ev_rows"] == ev_rows
    assert with_candidates["validation"]["status"] == "OK"  # type: ignore[index]
    assert with_candidates["candidate_evaluations"][0]["key"] == "R1|win|4"  # type: ignore[index]


def test_range_markets_use_the_official_lower_bound_for_ev() -> None:
    odds = _odds_row("wide", "1-4")
    odds.update({"odds": "20.0", "odds_min": "12.5", "odds_max": "20.0"})
    result = build_candidate_evaluations(_rows(4), [odds])
    candidate = result["candidate_evaluations"][0]

    assert candidate["official_odds"] == 12.5
    assert candidate["official_odds_max"] == 20.0
    assert candidate["ev"] == pytest.approx(candidate["hit_prob"] * 12.5)


def test_candidate_validation_rejects_every_malformed_input_class() -> None:
    ev_rows = [
        {"race_id": "", "horse_number": "x", "win_prob": 0},
        {"race_id": "R1", "horse_number": 1, "win_prob": 0.6},
        {"race_id": "R1", "horse_number": 1, "win_prob": 0.4},
        {"race_id": "R2", "horse_number": 2, "win_prob": 0.5},
    ]
    odds = [
        _odds_row("unsupported", "1"),
        _odds_row("wide", "1-1"),
        dict(_odds_row("win", "1"), race_id="MISSING"),
        dict(_odds_row("win", "1"), odds="0"),
        _odds_row("win", "99"),
    ]

    result = build_candidate_evaluations(ev_rows, odds)
    errors = result["validation"]["errors"]

    assert result["candidate_evaluations"] == []
    assert any("invalid race_id" in error for error in errors)
    assert any("duplicate probability" in error for error in errors)
    assert any("do not sum" in error for error in errors)
    assert any("unsupported bet type" in error for error in errors)
    assert any("duplicate leg" in error for error in errors)
    assert any("no probability race" in error for error in errors)
    assert any("official odds is invalid" in error for error in errors)
    assert any("not in the probability universe" in error for error in errors)


def test_candidate_helpers_cover_invalid_combinations_frames_and_numbers() -> None:
    with pytest.raises(ValueError, match="invalid combination"):
        canonical_combination("win", "0")
    assert _resolve_frame_number({"horse_number": "3"}, field_size=7) == 3
    assert _resolve_frame_number({"horse_number": "9"}, field_size=9) == 8
    assert _resolve_frame_number({"horse_number": "99"}, field_size=9) == 8
    assert _to_float(object()) == 0.0
    assert _to_int(object()) == 0
