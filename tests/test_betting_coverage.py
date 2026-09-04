from __future__ import annotations

from unittest.mock import patch

import pytest

import strategy.betting as betting


def _row(number: int, *, win: float = 0.20, market: float = 0.15) -> dict[str, object]:
    return {
        "race_id": "r_coverage",
        "horse_id": f"h{number}",
        "horse_name": f"H{number}",
        "horse_number": str(number),
        "win_prob": str(win),
        "market_prob": str(market),
        "place_prob": "0.45",
        "market_place_prob": "0.35",
        "current_odds": "10",
        "predicted_odds": "10",
        "ev": str(win * 10),
        "ev_current": str(win * 10),
        "consistency": "0.6",
        "history_count": "5",
    }


def _ticket(
    combo: str = "1-2",
    *,
    bet_type: str = "wide",
    stake: int = 100,
    ev: float = 1.2,
    odds: float = 10.0,
) -> dict[str, object]:
    numbers = combo.split("-")
    return {
        "race_id": "r_coverage",
        "bet_type": bet_type,
        "horse_number": combo,
        "horse_numbers": numbers,
        "stake": stake,
        "hit_prob": "0.1",
        "win_prob": "0.1",
        "win_odds": str(odds),
        "predicted_odds": str(odds),
        "ev": str(ev),
        "ev_current": str(ev),
        "confidence": "1",
    }


def test_ticket_builder_rejection_and_flat_stake_edges() -> None:
    assert betting._build_win_ticket({"win_prob": "0", "current_odds": "10"}, bankroll_per_race=1000, kelly_fraction=0.3) is None
    assert betting._build_win_ticket({"win_prob": "0.1", "current_odds": "10.6"}, bankroll_per_race=100, kelly_fraction=0.01) is None

    left, right = _row(1), _row(2)
    with (
        patch.object(betting, "_lookup_live_odds", return_value={"odds": "10"}),
        patch.object(betting, "_live_odds_value", return_value=10.0),
        patch.object(betting, "_kelly_stake", return_value=0),
    ):
        assert betting._build_wide_ticket(
            left,
            right,
            field_size=10,
            bankroll_per_race=1000,
            kelly_fraction=0.1,
            min_wide_ev=0.0,
            live_odds={},
        ) is None
        flat = betting._build_wide_ticket(
            left,
            right,
            field_size=10,
            bankroll_per_race=50,
            kelly_fraction=0.1,
            min_wide_ev=0.0,
            live_odds={},
            allow_flat_stake=True,
        )
        assert flat is not None and flat["stake"] == 50


def test_wakuren_candidate_and_zero_stake_edges() -> None:
    sparse = [_row(1)] + [{"horse_number": ""} for _ in range(8)]
    assert betting._build_wakuren_candidates(
        sparse,
        bankroll_per_race=1000,
        min_wakuren_ev=0.0,
        kelly_fraction=0.1,
        live_odds={},
    ) == []

    with (
        patch.object(betting, "_lookup_live_odds", return_value={"odds": "10"}),
        patch.object(betting, "_live_odds_value", return_value=10.0),
        patch.object(betting, "_kelly_stake", return_value=0),
    ):
        assert betting._build_wakuren_ticket(
            "1",
            [_row(1)],
            "2",
            [_row(2)],
            bankroll_per_race=1000,
            kelly_fraction=0.1,
            min_wakuren_ev=0.0,
            live_odds={},
        ) is None


def test_formation_candidate_and_point_rejections() -> None:
    assert betting._build_sanrentan_formation_candidates(
        [_row(1), _row(2)],
        bankroll_per_race=1000,
        min_sanrentan_ev=0.0,
        live_odds={},
    ) == []

    points = [
        {"hit_prob": "0.1", "market_prob": "0.1", "odds": "20", "odds_source": "estimated"},
        {"hit_prob": "0.1", "market_prob": "0.1", "odds": "20", "odds_source": "estimated"},
    ]
    with patch.object(betting, "_formation_points", return_value=points):
        assert betting._build_sanrentan_formation_candidates(
            [_row(1), _row(2), _row(3)],
            bankroll_per_race=100,
            min_sanrentan_ev=0.0,
            live_odds={},
        ) == []

    invalid = [_row(1, win=0, market=0), _row(2), _row(3)]
    assert betting._formation_points(invalid[:1], invalid[1:2], invalid[2:], live_odds={}) == []
    market_invalid = [_row(1, market=0), _row(2), _row(3)]
    assert betting._formation_points(
        market_invalid[:1], market_invalid[1:2], market_invalid[2:], live_odds={}
    ) == []
    with (
        patch.object(betting, "_lookup_live_odds", return_value={"odds": "1"}),
        patch.object(betting, "_live_odds_value", return_value=1.0),
    ):
        assert betting._formation_points([_row(1)], [_row(2)], [_row(3)], live_odds={}) == []


def test_exotic_ticket_flat_and_rejection_paths() -> None:
    rows = [_row(1), _row(2)]
    common = {
        "bet_type": "umaren",
        "payout_rate": 0.775,
        "max_odds": 120.0,
        "bankroll_per_race": 50,
        "kelly_fraction": 0.0,
        "min_ev": 0.0,
        "min_prob": 0.0,
        "max_fraction": 0.2,
        "live_odds": {},
    }
    with patch.object(betting, "_kelly_stake", return_value=0):
        assert betting._build_exotic_ticket(rows, **common) is None
        flat = betting._build_exotic_ticket(rows, allow_flat_stake=True, **common)
        assert flat is not None and flat["stake"] == 50


def test_classification_and_probability_helper_edges() -> None:
    assert betting._build_race_longshots([], min_win_ev=1.1, limit=2) == []
    assert betting._model_score_longshots([]) == []
    assert betting._unique_horse_names([{}, {"horse_name": "A"}, {"horse_name": "A"}]) == ["A"]
    assert betting._enrich_rows_for_multi_bet([]) == []
    assert betting._normalize_to_target([], target=1.0, floors=[], caps=[]) == []
    reduced = betting._normalize_to_target(
        [9.0, 1.0, 0.0],
        target=1.0,
        floors=[0.1, 0.4, 0.4],
        caps=[1.0, 1.0, 1.0],
    )
    assert abs(sum(reduced) - 1.0) < 1e-6
    assert betting._normalize_to_target(
        [1.0, 1.0], target=1.0, floors=[0.6, 0.6], caps=[1.0, 1.0]
    ) == [0.6, 0.6]
    assert betting._scale_to_target([0.0, 0.0], 1.0) == [0.5, 0.5]

    core, partner, longs = betting._reconcile_ticket_classifications(
        [{"horse_number": "1"}],
        [],
        [],
        [{"horse_numbers": ["1", "9"]}],
        [_row(1)],
    )
    assert len(core) == 1 and partner == [] and longs == []
    assert betting._ticket_horse_numbers_for_classification({"legs": ["bad"]}) == set()


def test_odds_probability_and_pace_edges() -> None:
    assert betting._wide_pace_adjustment({"pace_mix_high": 0.5, "front_rate": 0.5}, {"front_rate": 0.8}) == 1.0
    assert betting._wide_pace_adjustment(
        {"pace_mix_high": 0.5, "front_rate": 0.8, "closing_strength": 0.4},
        {"front_rate": 0.8, "closing_strength": 0.4},
    ) == 0.84
    assert betting._wide_pace_adjustment(
        {"pace_mix_high": 0.5, "front_rate": 0.8, "closing_strength": 0.6},
        {"front_rate": 0.8, "closing_strength": 0.6},
    ) == 0.92
    assert betting._estimate_market_pair_odds(0) == 0.0
    assert betting._estimate_place_odds(0) == 0.0
    assert betting._combo_hit_prob([_row(1)], key="win_prob", bet_type="umaren") == 0.0
    assert betting._estimate_exotic_odds(0, payout_rate=0.8, max_odds=10) == 0.0


def test_selection_calibration_and_annotation_edges() -> None:
    coverage = _ticket(ev=0.5)
    coverage["ticket_role"] = "coverage"
    coverage["coverage_reason"] = "marked_core_pair_real_odds"
    assert betting._select_optimized_tickets(
        [coverage],
        per_race_limit=2,
        prefer_wide=True,
        force_win_standout=False,
        min_portfolio_ev=1.0,
    ) == []

    selected = [_ticket()]
    keys = {("wide", "1-2")}
    betting._append_ticket_if_new(selected, keys, _ticket("3-4"), per_race_limit=1)
    assert len(selected) == 1
    invalid = {"bet_type": "win", "win_prob": "0", "win_odds": "0"}
    assert betting._calibrate_ticket_probabilities([invalid]) == [invalid]
    assert betting._top_win_probability_horse_number([]) == ""
    assert betting._max_horse_stake_dependency_ratio([_ticket(stake=0), _ticket("3-4", stake=0)]) == 0.0
    assert not betting._can_add_coverage_ticket([], _ticket(ev=0.5), min_portfolio_ev=1.0)

    formation = _ticket("1-2-3", bet_type="sanrentan", stake=600, odds=20)
    formation.update(
        ticket_shape="formation",
        point_count=6,
        stake_per_point=100,
        trifecta_odds_min="20",
        trifecta_odds_max="50",
    )
    annotated = betting._annotate_portfolio_tickets([formation])
    assert annotated[0]["return_if_hit_min"] == 2000
    assert annotated[0]["return_if_hit_max"] == 5000
    assert betting._ticket_type_rank("unknown", prefer_wide=False) == 8


def test_frame_and_portfolio_optimizer_edges() -> None:
    assert betting._single_frame_quality([{"win_prob": "0"}, {"win_prob": "0"}]) == 0.8
    assert betting._resolve_frame_number({"horse_number": "99"}, field_size=9) == "8"
    assert not betting._has_win_standout([])

    formation = _ticket("1-2", bet_type="sanrentan", stake=200)
    formation.update(ticket_shape="formation", point_count=2, stake_per_point=100)
    assert betting._optimize_portfolio_stakes(
        [formation], bankroll_per_race=50, min_portfolio_ev=0.0
    ) == []

    rebalanced = betting._optimize_portfolio_stakes(
        [_ticket("1-2", stake=100), _ticket("3-4", stake=100)],
        bankroll_per_race=100,
        min_portfolio_ev=0.0,
        max_horse_stake_dependency_ratio=1.0,
    )
    assert sum(int(ticket["stake"]) for ticket in rebalanced) <= 100

    assert betting._optimize_portfolio_stakes(
        [formation],
        bankroll_per_race=300,
        min_portfolio_ev=0.0,
        max_horse_stake_dependency_ratio=1.0,
    )
    assert betting._optimize_portfolio_stakes(
        [_ticket(ev=0.5)],
        bankroll_per_race=1000,
        min_portfolio_ev=1.0,
        max_horse_stake_dependency_ratio=1.0,
    )

    assert betting._ticket_max_portfolio_stake(formation, bankroll_per_race=1000) == 600
    assert betting._ticket_max_portfolio_stake(_ticket(bet_type="sanrentan"), bankroll_per_race=1000) == 200
    assert betting._ticket_max_portfolio_stake(_ticket(bet_type="other"), bankroll_per_race=1000) == 200


def test_rebalance_kelly_and_conversion_edges() -> None:
    scaled = betting._rebalance_race_stakes(
        [_ticket("1-2", stake=200), _ticket("3-4", stake=200)],
        bankroll_per_race=200,
    )
    assert [ticket["stake"] for ticket in scaled] == [100, 100]
    assert betting._rebalance_race_stakes([_ticket(stake=100)], bankroll_per_race=0) == []
    assert betting._kelly_stake(
        probability=0.0,
        odds=2.0,
        bankroll_per_race=1000,
        kelly_fraction=0.3,
        min_ev=1.0,
        max_fraction=0.2,
    ) == 0
    assert betting._to_float(object(), 7.0) == 7.0


def test_generate_tickets_reports_portfolio_failure_reasons() -> None:
    row = _row(1, win=0.5, market=1 / 3)
    selected = _ticket("1", bet_type="win", stake=100, ev=1.3, odds=3)

    with (
        patch.object(betting, "_select_optimized_tickets", return_value=[selected]),
        patch.object(betting, "_optimize_portfolio_stakes", return_value=[selected]),
        patch.object(betting, "_max_horse_stake_dependency_ratio", return_value=0.9),
    ):
        dependency = betting.generate_tickets([row], max_horse_stake_dependency_ratio=0.6)
    assert dependency["races"][0]["selection_reason"] == "horse_stake_dependency_limit_exceeded"

    with (
        patch.object(betting, "_select_optimized_tickets", return_value=[selected]),
        patch.object(betting, "_optimize_portfolio_stakes", return_value=[]),
    ):
        empty = betting.generate_tickets([row])
    assert empty["races"][0]["selection_reason"] == "no_safe_portfolio"


@pytest.mark.parametrize(
    ("candidate_update", "error_fragment"),
    [
        ({"bet_type": "unsupported"}, "unsupported bet type"),
        ({"key": "wrong"}, "invalid or duplicate candidate key"),
        ({"hit_prob": 0.0}, "invalid candidate probability or odds"),
        ({"ev": 99.0}, "candidate EV mismatch"),
    ],
)
def test_canonical_ticket_generation_fails_closed_on_invalid_lineage(
    candidate_update: dict[str, object], error_fragment: str
) -> None:
    candidate = {
        "key": "r_coverage|win|1",
        "race_id": "r_coverage",
        "bet_type": "win",
        "canonical_combination": "1",
        "hit_prob": 0.5,
        "official_odds": 3.0,
        "ev": 1.5,
    }
    candidate.update(candidate_update)

    plan = betting.generate_tickets(
        [_row(1, win=0.5)],
        candidate_evaluations=[candidate],
        candidate_validation={"status": "OK"},
    )

    assert plan["decision"] == "NO_GO"
    assert any(error_fragment in error for error in plan["probability_lineage"]["validation"]["errors"])


def test_canonical_wakuren_expands_frames_and_drops_selection_missing_top_horse() -> None:
    rows = [dict(_row(1, win=0.6), frame_number="1"), dict(_row(2, win=0.4), frame_number="2")]
    candidate = {
        "key": "r_coverage|wakuren|1-2",
        "race_id": "r_coverage",
        "bet_type": "wakuren",
        "canonical_combination": "1-2",
        "hit_prob": 0.4,
        "official_odds": 10.0,
        "ev": 4.0,
    }
    plan = betting.generate_tickets(
        rows,
        candidate_evaluations=[candidate],
        candidate_validation={"status": "OK"},
    )
    assert plan["candidate_evaluations"][0]["frame_numbers"] == ["1", "2"]
    assert plan["candidate_evaluations"][0]["horse_numbers"] == ["1", "2"]

    wrong_top = _ticket("2", bet_type="win", stake=100, ev=1.5, odds=3)
    win_candidate = dict(candidate, key="r_coverage|win|1", bet_type="win", canonical_combination="1")
    with (
        patch.object(betting, "_select_optimized_tickets", return_value=[wrong_top]),
        patch.object(betting, "_optimize_portfolio_stakes", return_value=[wrong_top]),
    ):
        dropped = betting.generate_tickets(
            rows,
            candidate_evaluations=[win_candidate],
            candidate_validation={"status": "OK"},
        )
    assert dropped["tickets"] == []


def test_preserved_safety_guards_and_optimizer_no_allocation_path() -> None:
    assert betting._build_sanrentan_formation_candidates(
        [], bankroll_per_race=1000, min_sanrentan_ev=0.0, live_odds={}
    ) == []
    two_rows = [_row(1), _row(2)]
    assert betting._build_coverage_candidates(
        two_rows, bankroll_per_race=1000, min_coverage_ev=1.0, live_odds={}
    ) == []
    assert betting._build_model_consistency_candidates(
        two_rows, bankroll_per_race=1000, min_coverage_ev=1.0, live_odds={}
    ) == []
    assert betting._build_marked_top5_coverage_candidates(
        two_rows, bankroll_per_race=1000, min_coverage_ev=1.0, live_odds={}
    ) == []
    with patch.object(betting, "_stake_allocation_score", return_value=0.0):
        allocated = betting._optimize_portfolio_stakes(
            [_ticket(ev=1.2)],
            bankroll_per_race=1000,
            min_portfolio_ev=0.0,
            max_horse_stake_dependency_ratio=1.0,
        )
    assert allocated
