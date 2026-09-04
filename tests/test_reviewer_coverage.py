from __future__ import annotations

from src.agents.reviewer import (
    ReviewerAgent,
    _build_ticket_repair_action,
    _find_divergent_rows,
    _find_missing_eligible_win_candidates,
    _longshot_odds_threshold,
    _longshot_stake_threshold,
    _max_horse_stake_dependency_ratio,
    _probability_lineage_errors,
    _race_id_from_config,
    _race_order_from_configs,
    _repair_action_name,
    _repair_portfolio_safe,
    _review_max_ticket_stake,
    _ticket_min_ev,
    _ticket_min_prob,
    _ticket_value_integrity_errors,
    _to_float,
    _win_candidate_tokens,
)
from src.agents.settings import WorkflowSettings


def _safe_win(*, horse_number: str = "1", stake: int = 100) -> dict[str, object]:
    return {
        "race_id": "R",
        "bet_type": "win",
        "horse_number": horse_number,
        "horse_name": f"Horse {horse_number}",
        "win_prob": "0.5",
        "hit_prob": "0.5",
        "win_odds": "3",
        "ev": "1.5",
        "stake": stake,
    }


def test_reviewer_win5_quality_and_order_failures() -> None:
    settings = WorkflowSettings(max_repair_attempts=1, win5_max_points=2)
    result = ReviewerAgent(settings).run(
        {
            "quality_report": {"issues_by_severity": {"high": 1}, "missing_current_odds_entries": 2},
            "entries": [{}, {}],
            "race_configs": [{"race_id": "A"}],
        },
        [],
        [],
        {"bet_type": "win5", "points": 3, "legs": [], "race_order": ["B"], "tickets": []},
        attempt=0,
    )
    assert result["status"] == "NG"
    assert "high severity" in result["reason"]
    assert "missing for every entry" in result["reason"]
    assert "exceeds max point" in result["reason"]
    assert "exactly five legs" in result["reason"]
    assert "race order" in result["reason"]
    assert result["repair_actions"] == ["retry_aggressive_parse", "retry_aggressive_parse"]

    empty = ReviewerAgent(settings).run(
        {"quality_report": {}, "race_configs": []},
        [],
        [],
        {"bet_type": "win5", "points": 0, "legs": []},
        attempt=0,
    )
    assert "no valid points" in empty["reason"]


def test_reviewer_low_portfolio_ev_and_longshot_overweight() -> None:
    ticket = dict(_safe_win(stake=200), ev="0.9", win_odds="30")
    result = ReviewerAgent(WorkflowSettings(min_portfolio_ev=1.0, max_horse_ticket_dependency_ratio=1.0)).run(
        {"quality_report": {}, "entries": []},
        [],
        [{"race_id": "R", "horse_number": "1", "win_prob": "1"}],
        {"tickets": [ticket]},
        attempt=0,
    )
    assert "portfolio EV" in result["reason"]
    assert "overweights extreme longshots" in result["reason"]


def test_reviewer_fails_closed_on_canonical_probability_lineage_error() -> None:
    invalid_plan = {
        "optimization_mode": "canonical_probability_robust_odds",
        "decision_code": "NO_GO_PROBABILITY_LINEAGE_INVALID",
        "probability_lineage": {"status": "NG"},
        "tickets": [],
    }
    result = ReviewerAgent(WorkflowSettings()).run(
        {"quality_report": {}, "entries": []},
        [],
        [],
        invalid_plan,
        attempt=0,
    )
    assert result["status"] == "NG"
    assert "probability lineage invalid" in result["reason"]
    assert result["probability_lineage"]["status"] == "NG"
    assert result["repair_actions"] == []


def test_probability_lineage_checks_selected_raw_probability_against_stage_04() -> None:
    candidate = {
        "source_candidate_key": "R|wide|1-9",
        "probability_source": "04_ev_calculator",
        "raw_hit_prob": 0.0876,
        "probability_lineage": {
            "source": "04_ev_calculator",
            "candidate_key": "R|wide|1-9",
            "raw_hit_prob": 0.0876,
        },
    }
    plan = {
        "optimization_mode": "canonical_probability_robust_odds",
        "probability_lineage": {"status": "OK"},
        "candidate_evaluations": [candidate],
        "tickets": [dict(candidate, raw_hit_prob=0.1799)],
    }
    assert _probability_lineage_errors(plan) == [
        "selected ticket raw probability mismatch: R|wide|1-9"
    ]


def test_probability_lineage_rejects_bad_candidates_and_missing_ticket_source() -> None:
    bad = {
        "source_candidate_key": "R|win|1",
        "probability_source": "wrong",
        "raw_hit_prob": 0.5,
        "probability_lineage": {"source": "wrong", "candidate_key": "R|win|1", "raw_hit_prob": 0.4},
    }
    plan = {
        "optimization_mode": "canonical_probability_robust_odds",
        "probability_lineage": {"status": "OK"},
        "candidate_evaluations": [{}, bad, dict(bad)],
        "tickets": [{"source_candidate_key": "missing"}],
    }

    errors = _probability_lineage_errors(plan)

    assert "missing or duplicate candidate key" in errors
    assert "candidate raw probability mismatch: R|win|1" in errors
    assert "selected ticket has no canonical candidate: missing" in errors


def test_live_value_integrity_rejects_missing_and_mismatched_displayed_ev() -> None:
    base = {
        "bet_type": "win",
        "horse_number": "1",
        "odds_source": "jra_live",
        "hit_prob": "0.5",
        "win_odds": "3",
        "ev_current": "1.5",
    }

    missing = _ticket_value_integrity_errors([base], {})
    mismatched = _ticket_value_integrity_errors([dict(base, ev="1.6")], {})

    assert "win:1 missing displayed EV" in missing
    assert "win:1 ev=1.6 != ev_current=1.5" in mismatched


def test_ticket_repair_action_none_paths() -> None:
    settings = WorkflowSettings(max_horse_ticket_dependency_ratio=1.0, min_top3_ticket_coverage=1, bankroll_per_race=100)
    unsafe = dict(_safe_win(), ev="0.1")
    assert _build_ticket_repair_action([unsafe], ev_rows=[], divergent_rows=[], settings=settings) is None

    safe = _safe_win()
    assert _build_ticket_repair_action(
        [safe],
        ev_rows=[{"race_id": "R", "horse_number": "2", "win_prob": "1"}],
        divergent_rows=[],
        settings=settings,
    ) is None
    assert _build_ticket_repair_action(
        [safe],
        ev_rows=[{"race_id": "R", "horse_number": "1", "win_prob": "1"}],
        divergent_rows=[],
        settings=settings,
    ) is None
    assert not _repair_portfolio_safe([], ev_rows=[], settings=settings)
    assert not _repair_portfolio_safe([unsafe], ev_rows=[], settings=settings)

    mixed = [_safe_win(horse_number="1"), dict(_safe_win(horse_number="2"), ev="0.5")]
    assert not _repair_portfolio_safe(
        mixed,
        ev_rows=[
            {"horse_number": "1", "win_prob": "0.6"},
            {"horse_number": "2", "win_prob": "0.4"},
        ],
        settings=WorkflowSettings(
            min_portfolio_ev=1.0,
            max_horse_ticket_dependency_ratio=1.0,
            max_horse_stake_dependency_ratio=1.0,
        ),
    )


def test_reviewer_private_type_thresholds_and_stakes() -> None:
    settings = WorkflowSettings()
    assert _review_max_ticket_stake({"ticket_shape": "formation", "point_count": 2}, 1000) == 600
    assert _review_max_ticket_stake({"bet_type": "win"}, 1000) == 300
    assert _review_max_ticket_stake({"bet_type": "umaren"}, 1000) == 300
    assert _review_max_ticket_stake({"bet_type": "umatan"}, 1000) == 200
    assert _review_max_ticket_stake({"bet_type": "sanrentan"}, 1000) == 200
    assert _repair_action_name("retry") == "retry"

    expected_probs = {
        "place": 0.16,
        "wide": 0.10,
        "wakuren": 0.035,
        "umaren": 0.035,
        "umatan": 0.018,
        "sanrenpuku": 0.018,
        "sanrentan": 0.006,
    }
    for bet_type, expected in expected_probs.items():
        assert _ticket_min_prob({"bet_type": bet_type}) == expected
        assert _ticket_min_ev({"bet_type": bet_type}, settings) == getattr(
            settings,
            {
                "place": "min_place_ev", "wide": "min_wide_ev", "wakuren": "min_wakuren_ev",
                "umaren": "min_umaren_ev", "umatan": "min_umatan_ev", "sanrenpuku": "min_sanrenpuku_ev",
                "sanrentan": "min_sanrentan_ev",
            }[bet_type],
        )

    expected_odds = {
        "place": 8.0, "wide": 16.0, "wakuren": 35.0, "umaren": 35.0,
        "umatan": 70.0, "sanrenpuku": 60.0, "sanrentan": 120.0,
    }
    for bet_type, expected in expected_odds.items():
        assert _longshot_odds_threshold({"bet_type": bet_type}) == expected
    assert _longshot_stake_threshold({"bet_type": "place"}) == 300
    assert _longshot_stake_threshold({"bet_type": "umaren"}) == 100


def test_candidate_metadata_race_id_and_divergence_skip_edges() -> None:
    assert _find_missing_eligible_win_candidates(
        {}, [], {"races": [{"race_id": "R", "candidates": [], "candidate_evaluations": []}]}, minimum_win_ev=1.05
    ) == []
    assert _find_missing_eligible_win_candidates(
        {}, [], {"candidate_evaluations": [{"race_id": ""}]}, minimum_win_ev=1.05
    ) == []
    assert _find_missing_eligible_win_candidates(
        {
            "combo_odds": [
                {"race_id": "R", "bet_type": "win", "combination": "1", "odds": "2"}
            ]
        },
        [{"race_id": "OTHER", "horse_number": "1", "win_prob": "1"}],
        {"races": [{"race_id": "R", "candidates": []}]},
        minimum_win_ev=1.05,
    ) == []
    assert _find_missing_eligible_win_candidates(
        {
            "combo_odds": [
                {"race_id": "R", "bet_type": "win", "combination": "1", "odds": "2"}
            ]
        },
        [{"race_id": "R", "horse_number": "", "win_prob": "1"}],
        {"races": [{"race_id": "R", "candidates": []}]},
        minimum_win_ev=1.05,
    ) == []
    assert _win_candidate_tokens(["bad", {"race_id": "OTHER", "bet_type": "win"}, {"bet_type": "place"}], race_id="R") == set()
    assert _win_candidate_tokens([{"bet_type": "win", "horse_id": "H"}], race_id="R") == {"horse_id:H"}

    assert _race_id_from_config({"race_date": "2026-08-29", "track": "札幌", "race_number": "7"}) == "20260829_札幌_07"
    assert _race_id_from_config({}) == ""
    assert _race_order_from_configs([{"race_id": "R"}, {}]) == ["R"]
    assert _find_divergent_rows(
        [{"race_id": "R", "current_odds": "0"}],
        max_ev_delta_abs=0.1,
        max_ev_delta_ratio=0.1,
        max_odds_gap_ratio=0.1,
    ) == []
    assert _find_divergent_rows(
        [{"current_odds": "10", "predicted_odds": "10", "ev_current": "1", "ev_predicted": "0"}],
        max_ev_delta_abs=0.1,
        max_ev_delta_ratio=0.1,
        max_odds_gap_ratio=0.1,
    ) == []
    assert _max_horse_stake_dependency_ratio([_safe_win()]) == 0.0
    assert _max_horse_stake_dependency_ratio([dict(_safe_win(), stake=0), dict(_safe_win(horse_number="2"), stake=0)]) == 0.0
    assert _to_float(object(), 9.0) == 9.0
