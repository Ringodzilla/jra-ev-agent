from __future__ import annotations

from src.agents.reviewer import ReviewerAgent
from src.agents.settings import WorkflowSettings
from strategy.betting import _calibrate_ticket_probabilities
from strategy.portfolio import canonical_ticket_ev


def test_ticket_calibration_uses_current_odds_for_live_wide_ev() -> None:
    ticket = {
        "bet_type": "wide",
        "hit_prob": "0.30",
        "win_odds": "3.0",
        "wide_odds_min": "3.0",
        "wide_odds_max": "3.4",
        "predicted_odds": "4.0",
        "odds_source": "jra_live",
    }

    calibrated = _calibrate_ticket_probabilities([ticket])[0]

    hit_prob = float(calibrated["hit_prob"])
    assert abs(float(calibrated["market_prob"]) - (1.0 / 3.0)) < 0.000001
    assert abs(hit_prob - (((1.0 - 0.55) * 0.30) + (0.55 / 3.0))) < 0.000001
    assert abs(float(calibrated["ev_current"]) - (hit_prob * 3.0)) < 0.00001
    assert abs(float(calibrated["ev"]) - (hit_prob * 3.0)) < 0.00001
    assert abs(float(calibrated["ev_predicted"]) - (hit_prob * 4.0)) < 0.00001


def test_reviewer_fails_closed_on_official_live_ev_arithmetic_mismatch() -> None:
    ticket = {
        "race_id": "R",
        "bet_type": "win",
        "horse_number": "1",
        "horse_name": "Horse 1",
        "win_prob": "0.5",
        "hit_prob": "0.5",
        "win_odds": "3",
        "odds_source": "jra_live",
        "ev": "1.8",
        "ev_current": "1.8",
        "stake": 100,
    }
    result = ReviewerAgent(WorkflowSettings(max_horse_ticket_dependency_ratio=1.0)).run(
        {"quality_report": {}, "entries": []},
        [],
        [{"race_id": "R", "horse_number": "1", "win_prob": "1"}],
        {
            "tickets": [ticket],
            "portfolio_summary": {
                "total_stake": 100,
                "total_points": 1,
                "expected_return": 180,
                "expected_profit": 80,
                "portfolio_ev": "1.8",
            },
        },
        attempt=0,
    )

    assert result["status"] == "NG"
    assert result["value_integrity"]["status"] == "NG"
    assert "ev_current=1.8 != hit_prob*odds=1.5" in result["reason"]
    assert "portfolio expected_return" in result["reason"]


def test_reviewer_rejects_non_finite_or_missing_canonical_live_values() -> None:
    ticket = {
        "race_id": "R",
        "bet_type": "wide",
        "horse_number": "1-2",
        "odds_source": "jra_live",
        "hit_prob": "nan",
        "win_odds": "3",
        "ev": "99",
        "stake": 100,
    }
    result = ReviewerAgent(WorkflowSettings(max_horse_ticket_dependency_ratio=1.0)).run(
        {"quality_report": {}, "entries": []},
        [],
        [],
        {"tickets": [ticket]},
        attempt=0,
    )

    assert result["status"] == "NG"
    assert result["value_integrity"]["status"] == "NG"
    assert "missing canonical probability, odds, or EV" in result["reason"]


def test_reviewer_recomputes_official_live_formation_ev_from_points() -> None:
    ticket = {
        "race_id": "R",
        "bet_type": "sanrentan",
        "ticket_shape": "formation",
        "horse_number": "1 → 2,3 → 2,3",
        "odds_source": "jra_live",
        "point_count": 2,
        "points": [
            {"hit_prob": "0.02", "odds": "40", "odds_source": "jra_live"},
            {"hit_prob": "0.01", "odds": "80", "odds_source": "jra_live"},
        ],
        "ev": "1.2",
        "ev_current": "1.2",
        "stake": 200,
    }
    assert canonical_ticket_ev(ticket) == 0.8

    result = ReviewerAgent(WorkflowSettings(max_horse_ticket_dependency_ratio=1.0)).run(
        {"quality_report": {}, "entries": []},
        [],
        [],
        {"tickets": [ticket]},
        attempt=0,
    )

    assert result["status"] == "NG"
    assert result["value_integrity"]["status"] == "NG"
    assert "hit_prob*odds=0.8" in result["reason"]
