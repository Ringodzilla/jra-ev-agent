from __future__ import annotations

from datetime import datetime, timezone
import math

import pytest

from strategy.odds_scenarios import (
    _constituent_odds_ratio,
    _is_true,
    _log_slope,
    _matching_history,
    _odds_band,
    _parse_timestamp,
    _raw_hit_probability,
    _ticket_combination,
    _ticket_numbers,
    _to_float,
    _weighted_quantile,
    build_odds_scenarios,
    build_ticket_odds_scenarios,
)


def _ticket(**overrides: object) -> dict[str, object]:
    ticket: dict[str, object] = {
        "bet_type": "wide",
        "horse_number": "4-9",
        "horse_numbers": ["4", "9"],
        "raw_hit_prob": "0.18",
        "bet_type_market_shrink": "0.55",
    }
    ticket.update(overrides)
    return ticket


def _ev_rows() -> list[dict[str, object]]:
    return [
        {"horse_number": "4", "current_odds": "10", "predicted_odds": "8"},
        {"horse_number": "9", "current_odds": "2", "predicted_odds": "1.8"},
        {"horse_number": "7", "current_odds": "4", "predicted_odds": "5"},
    ]


def test_sparse_history_uses_conservative_prior_and_four_distinct_scenarios() -> None:
    result = build_ticket_odds_scenarios(_ticket(), 8.0, [], _ev_rows(), 300)

    assert [row["name"] for row in result["scenarios"]] == [
        "stable",
        "favorite_concentration",
        "longshot_outflow",
        "late_local_inflow",
    ]
    assert result["audit"]["fallback_prior_used"] is True
    assert result["audit"]["effective_range_ratio"] > 0
    assert len({round(float(row["odds"]), 8) for row in result["scenarios"]}) == 4
    assert 1.0 < result["robust_odds"] < 8.0
    assert result["robust_ev"] == result["cvar20_ev"]


def test_falling_history_and_time_to_post_are_auditable() -> None:
    history = [
        {"bet_type": "wide", "combination": "4-9", "odds_min": "12", "captured_at": "2026-08-30T06:00:00Z"},
        {"bet_type": "wide", "combination": "9-4", "odds_min": "10", "captured_at": "2026-08-30T06:10:00Z"},
        {"bet_type": "wide", "combination": "4-9", "odds_min": "8", "captured_at": "2026-08-30T06:20:00Z"},
    ]
    result = build_ticket_odds_scenarios(_ticket(), 8.0, history, _ev_rows(), 600)
    stable = next(row for row in result["scenarios"] if row["name"] == "stable")

    assert stable["odds"] < 8.0
    assert result["audit"]["history_points_used"] == 3
    assert result["audit"]["history_span_minutes"] == 20
    assert result["audit"]["recent_log_slope_per_hour"] < 0
    assert result["audit"]["projected_trend_log"] < 0
    assert result["audit"]["seconds_to_post"] == 600
    assert result["audit"]["constituent_rows_used"] == 2
    assert result["audit"]["constituent_predicted_to_current_odds_ratio"] == pytest.approx(math.sqrt(0.8 * 0.9))


def test_each_scenario_reuses_raw_probability_and_market_shrink_formula() -> None:
    result = build_ticket_odds_scenarios(_ticket(), 8.0, [], _ev_rows(), 900)

    for row in result["scenarios"]:
        odds = float(row["odds"])
        expected_probability = (0.45 * 0.18) + (0.55 * (1.0 / odds))
        assert row["calibrated_hit_prob"] == pytest.approx(expected_probability)
        assert row["ev"] == pytest.approx(expected_probability * odds)


def test_upward_history_never_makes_robust_odds_exceed_current() -> None:
    history = [
        {"bet_type": "wide", "combination": "4-9", "odds": "5", "captured_at": "2026-08-30T06:00:00Z"},
        {"bet_type": "wide", "combination": "4-9", "odds": "6", "captured_at": "2026-08-30T06:10:00Z"},
        {"bet_type": "wide", "combination": "4-9", "odds": "8", "captured_at": "2026-08-30T06:20:00Z"},
    ]
    result = build_ticket_odds_scenarios(_ticket(), 8.0, history, _ev_rows(), 600)

    assert next(row for row in result["scenarios"] if row["name"] == "stable")["odds"] > 8.0
    assert result["robust_odds"] <= 8.0


def test_unmatched_and_incomplete_history_is_not_used() -> None:
    history = [
        {"bet_type": "umaren", "combination": "4-9", "odds": "2", "captured_at": "2026-08-30T06:00:00Z"},
        {"bet_type": "wide", "combination": "4-7", "odds": "3", "captured_at": "2026-08-30T06:05:00Z"},
        {"bet_type": "wide", "combination": "4-9", "odds": "4", "captured_at": "2026-08-30T06:10:00Z", "snapshot_complete": False},
    ]
    result = build_ticket_odds_scenarios(_ticket(), 8.0, history, _ev_rows(), 300)

    assert result["audit"]["history_points_used"] == 0
    assert result["audit"]["fallback_prior_used"] is True


@pytest.mark.parametrize("forbidden_key", ["final_odds", "payout_yen_per_100", "result_label"])
def test_post_race_fields_are_rejected(forbidden_key: str) -> None:
    history = [{"bet_type": "wide", "combination": "4-9", "odds": "8", "captured_at": "2026-08-30T06:00:00Z", forbidden_key: "1"}]

    with pytest.raises(ValueError, match="post-race fields"):
        build_ticket_odds_scenarios(_ticket(), 8.0, history, _ev_rows(), 300)


def test_invalid_official_odds_and_probability_are_rejected() -> None:
    with pytest.raises(ValueError, match="current_official_odds"):
        build_ticket_odds_scenarios(_ticket(), 1.0, [], _ev_rows(), 300)
    with pytest.raises(ValueError, match="raw hit probability"):
        build_ticket_odds_scenarios(_ticket(raw_hit_prob="0"), 8.0, [], _ev_rows(), 300)


def test_public_shorthand_has_the_same_pure_result() -> None:
    args = (_ticket(), 8.0, [], _ev_rows(), 300)

    assert build_odds_scenarios(*args) == build_ticket_odds_scenarios(*args)


def test_odds_scenario_helpers_cover_fallback_and_validation_edges() -> None:
    history = [
        {"race_id": "OTHER", "bet_type": "wide", "combination": "4-9", "odds": 8,
         "captured_at": "2026-08-30T06:00:00Z"},
        {"race_id": "R", "bet_type": "", "combination": "4-9", "odds": 8,
         "captured_at": "2026-08-30T06:00:00"},
    ]
    assert len(_matching_history(dict(_ticket(), race_id="R"), history)) == 1
    assert _constituent_odds_ratio(_ticket(horse_numbers=["99"]), _ev_rows()) == (1.0, 0)
    assert _raw_hit_probability({"hit_prob": "0.2"}) == 0.2
    with pytest.raises(ValueError, match="positive mass"):
        _weighted_quantile([(1.0, 0.0)], 0.5)
    assert _weighted_quantile([(1.0, 1.0), (2.0, 1.0)], float("nan")) == 2.0
    same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _log_slope([(same_time, 2.0), (same_time, 3.0)]) == 0.0
    assert _ticket_combination({"bet_type": "wide", "horse_number": "9-4"}) == "4-9"
    assert _ticket_numbers({"horse_number": "9-4"}) == ["9", "4"]
    assert _parse_timestamp("") is None
    assert _parse_timestamp("invalid") is None
    assert _parse_timestamp("2026-01-01T00:00:00").tzinfo == timezone.utc
    assert _odds_band(2.0) == "favorite"
    assert _odds_band(31.0) == "longshot"
    assert _is_true("complete")
    assert _to_float(None, 7.0) == 7.0
    assert _to_float(object(), 7.0) == 7.0
