from __future__ import annotations

import pytest

from strategy.betting import (
    _build_top5_wide_box_candidate,
    _candidate_eligibility_checks,
    _limit_eligible_selection_pool,
    _select_optimized_tickets,
    generate_tickets,
)
from strategy.portfolio import (
    portfolio_ev,
    portfolio_no_gami,
    portfolio_robust_ev,
    portfolio_total_points,
    portfolio_total_stake,
    ticket_max_return_if_hit,
    with_adjusted_stake,
)


WIN_PROBABILITIES = {
    1: 0.055064,
    2: 0.065802,
    3: 0.060102,
    4: 0.060224,
    5: 0.056273,
    6: 0.066388,
    7: 0.049836,
    8: 0.076875,
    9: 0.052153,
    10: 0.048819,
    11: 0.052956,
    12: 0.068903,
    13: 0.059231,
    14: 0.058402,
    15: 0.050570,
    16: 0.067538,
    17: 0.050863,
}

WIDE_POINTS = {
    "2-6": (0.027844, 99.9, 80.226522),
    "2-8": (0.032196, 25.8, 22.717154),
    "2-12": (0.028890, 115.2, 93.980180),
    "2-16": (0.028322, 176.9, 137.116356),
    "6-8": (0.032481, 3.6, 3.174384),
    "6-12": (0.029145, 11.5, 10.333317),
    "6-16": (0.028572, 25.6, 22.865255),
    "8-12": (0.033699, 4.2, 3.454095),
    "8-16": (0.033038, 9.9, 8.871922),
    "12-16": (0.029645, 32.3, 27.889200),
}


def _race_rows() -> list[dict[str, object]]:
    return [
        {
            "race_id": "20261018_京都_11",
            "horse_number": str(number),
            "horse_name": f"H{number}",
            "win_prob": str(probability),
        }
        for number, probability in WIN_PROBABILITIES.items()
    ]


def _wide_candidates() -> list[dict[str, object]]:
    candidates = []
    for combination, (probability, current_odds, robust_odds) in WIDE_POINTS.items():
        numbers = combination.split("-")
        candidates.append(
            {
                "race_id": "20261018_京都_11",
                "bet_type": "wide",
                "horse_number": combination,
                "horse_numbers": numbers,
                "horse_names": [f"H{number}" for number in numbers],
                "raw_hit_prob": probability,
                "hit_prob": str(probability),
                "official_odds_current": current_odds,
                "robust_odds": str(robust_odds),
                "odds_source": "jra_live",
                "source_candidate_key": f"20261018_京都_11|wide|{combination}",
            }
        )
    return candidates


def test_top5_wide_box_uses_joint_probability_and_total_stake_ev() -> None:
    box = _build_top5_wide_box_candidate(
        _wide_candidates(),
        _race_rows(),
        bankroll_per_race=1000,
    )

    assert box is not None
    assert box["ticket_shape"] == "wide_box"
    assert box["horse_numbers"] == ["2", "6", "8", "12", "16"]
    assert box["point_count"] == 10
    assert len(box["points"]) == 10
    assert box["stake_per_point"] == 100
    assert box["stake"] == 1000
    assert all(point["odds_source"] == "jra_live" for point in box["points"])
    assert float(box["hit_prob"]) == pytest.approx(0.254578, abs=1e-6)
    assert float(box["three_hit_probability"]) == pytest.approx(0.024627, abs=1e-6)
    assert float(box["expected_hit_count"]) == pytest.approx(0.303832, abs=1e-6)
    assert float(box["ev_current"]) == pytest.approx(1.456, abs=1e-3)
    assert float(box["robust_ev"]) == pytest.approx(1.186, abs=1e-3)
    assert float(box["gami_probability_current"]) > 0.0
    assert box["strict_no_gami"] is False

    checks = _candidate_eligibility_checks(
        box,
        minimum_ev=1.01,
        minimum_hit_probability=0.10,
    )
    assert all(check["passed"] for check in checks)


def test_wide_box_portfolio_math_uses_ten_points_as_one_candidate() -> None:
    box = _build_top5_wide_box_candidate(
        _wide_candidates(),
        _race_rows(),
        bankroll_per_race=1000,
    )

    assert box is not None
    assert portfolio_total_points([box]) == 10
    assert portfolio_total_stake([box]) == 1000
    assert portfolio_ev([box]) == pytest.approx(1.456, abs=1e-3)
    assert portfolio_robust_ev([box]) == pytest.approx(1.186, abs=1e-3)
    assert portfolio_no_gami([box]) is False


def test_top5_wide_box_requires_every_official_point() -> None:
    incomplete = _wide_candidates()[:-1]
    non_official = _wide_candidates()
    non_official[0]["odds_source"] = "estimated"

    assert (
        _build_top5_wide_box_candidate(
            incomplete,
            _race_rows(),
            bankroll_per_race=1000,
        )
        is None
    )
    assert (
        _build_top5_wide_box_candidate(
            non_official,
            _race_rows(),
            bankroll_per_race=1000,
        )
        is None
    )


def test_top5_wide_box_rejects_duplicate_horse_and_invalid_probability() -> None:
    duplicate_rows = _race_rows()[:5]
    duplicate_rows[1]["horse_number"] = duplicate_rows[0]["horse_number"]
    non_positive_pair = _wide_candidates()
    non_positive_pair[0]["raw_hit_prob"] = 0.0
    non_positive_pair[0]["hit_prob"] = "0"
    impossible_any_hit = _wide_candidates()
    for candidate in impossible_any_hit:
        candidate["raw_hit_prob"] = 1e-9
        candidate["hit_prob"] = "0.000000001"

    assert (
        _build_top5_wide_box_candidate(
            _wide_candidates(),
            duplicate_rows,
            bankroll_per_race=1000,
        )
        is None
    )
    assert (
        _build_top5_wide_box_candidate(
            non_positive_pair,
            _race_rows(),
            bankroll_per_race=1000,
        )
        is None
    )
    assert (
        _build_top5_wide_box_candidate(
            impossible_any_hit,
            _race_rows(),
            bankroll_per_race=1000,
        )
        is None
    )


def test_balanced_generation_adds_complete_top5_wide_box_candidate() -> None:
    race_rows = [
        {
            "race_id": "R",
            "horse_number": str(number),
            "horse_name": f"H{number}",
            "win_prob": probability,
            "history_count": "5",
            "consistency": "0.8",
        }
        for number, probability in enumerate((0.30, 0.25, 0.20, 0.15, 0.10), start=1)
    ]
    candidates = []
    for left in range(1, 6):
        for right in range(left + 1, 6):
            # With five runners, every top-three outcome contains exactly three
            # wide pairs, so the ten pair probabilities sum to three.
            probability = 0.30
            combination = f"{left}-{right}"
            candidates.append(
                {
                    "key": f"R|wide|{combination}",
                    "race_id": "R",
                    "bet_type": "wide",
                    "canonical_combination": combination,
                    "snapshot_id": "S",
                    "captured_at": "2026-10-18T06:30:00+00:00",
                    "hit_prob": probability,
                    "official_odds": 20.0,
                    "ev": probability * 20.0,
                }
            )

    plan = generate_tickets(
        race_rows,
        candidate_evaluations=candidates,
        candidate_validation={"status": "OK"},
        odds_history=[],
        bankroll_per_race=1000,
        mode="balanced",
        top5_wide_box_enabled=True,
    )

    boxes = [
        candidate
        for candidate in plan["candidate_evaluations"]
        if candidate.get("ticket_shape") == "wide_box"
    ]
    assert len(boxes) == 1
    assert boxes[0]["point_count"] == 10
    assert boxes[0]["stake"] == 1000


def test_wide_box_bypasses_wide_cap_and_safe_box_is_selected_atomically() -> None:
    box = _build_top5_wide_box_candidate(
        _wide_candidates(),
        _race_rows(),
        bankroll_per_race=1000,
    )
    assert box is not None
    regular_wide = [
        {
            "bet_type": "wide",
            "horse_number": combination,
            "horse_numbers": combination.split("-"),
            "robust_ev": robust_ev,
            "robust_hit_prob": 0.2,
            "confidence": 1.0,
        }
        for combination, robust_ev in (("1-2", 1.5), ("1-3", 1.2))
    ]

    limited = _limit_eligible_selection_pool(
        [box, *regular_wide],
        max_wide_tickets_per_race=1,
        max_exotic_tickets_per_race=0,
    )

    assert box in limited
    assert regular_wide[0] in limited
    assert regular_wide[1] not in limited

    safe_box = {
        **box,
        "strict_no_gami_current": True,
        "strict_no_gami_robust": True,
    }
    selected = _select_optimized_tickets(
        [regular_wide[0], safe_box],
        per_race_limit=5,
        prefer_wide=True,
        force_win_standout=False,
        min_portfolio_ev=1.0,
        required_horse_number="8",
    )
    assert selected == [safe_box]


def test_wide_box_stake_adjustment_recomputes_return_range() -> None:
    box = _build_top5_wide_box_candidate(
        _wide_candidates(),
        _race_rows(),
        bankroll_per_race=1000,
    )
    assert box is not None
    assert ticket_max_return_if_hit(box) == box["maximum_return"]

    adjusted = with_adjusted_stake(box, 1500)

    assert adjusted["stake"] == 1000
    assert adjusted["stake_per_point"] == 100
    assert adjusted["minimum_positive_return"] == int(
        100 * float(box["minimum_positive_odds_sum"])
    )
    assert adjusted["maximum_return"] == int(100 * float(box["maximum_odds_sum"]))
