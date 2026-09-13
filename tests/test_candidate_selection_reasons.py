from itertools import product
from unittest.mock import patch

import pytest

from strategy import betting


@pytest.mark.parametrize("ev_pass,probability_pass,stake_pass", product([False, True], repeat=3))
def test_canonical_eligibility_reports_all_failures_without_changing_gates(
    ev_pass, probability_pass, stake_pass
):
    ticket = {
        "race_id": "r_reasons",
        "bet_type": "win",
        "horse_number": "1",
        "horse_numbers": ["1"],
        "ev_current": 1.5,
        "robust_ev": 1.2 if ev_pass else 1.1999,
        "hit_prob": 0.2,
        "robust_hit_prob": 0.04 if probability_pass else 0.0399,
        "stake": 100 if stake_pass else 99.99,
    }
    source = {
        "key": "r_reasons|win|1",
        "race_id": "r_reasons",
        "bet_type": "win",
        "canonical_combination": "1",
        "hit_prob": 0.2,
        "official_odds": 7.5,
        "ev": 1.5,
    }
    expected_failures = [
        reason
        for passed, reason in (
            (ev_pass, "below_minimum_ev"),
            (probability_pass, "below_minimum_hit_probability"),
            (stake_pass, "below_minimum_stake"),
        )
        if not passed
    ]
    with (
        patch.object(betting, "_canonical_ticket_from_evaluation", return_value=ticket),
        patch.object(betting, "_select_optimized_tickets", return_value=[]) as select,
    ):
        plan = betting.generate_tickets(
            [{"race_id": "r_reasons", "horse_number": "1", "win_prob": 0.2}],
            candidate_evaluations=[source],
            candidate_validation={"status": "OK"},
            min_ev=1.2,
        )

    candidate = plan["candidate_evaluations"][0]
    assert bool(select.call_args.args[0]) == all((ev_pass, probability_pass, stake_pass))
    assert candidate["non_selection_reasons"] == (
        expected_failures or ["no_safe_robust_portfolio"]
    )
    assert candidate["non_selection_reason"] == candidate["non_selection_reasons"][0]
    assert candidate["eligibility_checks"] == [
        {
            "reason": "below_minimum_ev",
            "metric": "robust_ev",
            "value": ticket["robust_ev"],
            "minimum": 1.2,
            "passed": ev_pass,
        },
        {
            "reason": "below_minimum_hit_probability",
            "metric": "decision_hit_prob",
            "value": ticket["robust_hit_prob"],
            "minimum": 0.04,
            "passed": probability_pass,
        },
        {
            "reason": "below_minimum_stake",
            "metric": "stake",
            "value": int(ticket["stake"]),
            "minimum": 100,
            "passed": stake_pass,
        },
    ]
    assert "eligibility_checks" not in ticket


def test_reannotation_clears_old_rejection_reasons_for_selected_candidate():
    candidate = {
        "bet_type": "win",
        "horse_number": "1",
        "non_selection_reason": "below_minimum_stake",
        "non_selection_reasons": ["below_minimum_stake"],
    }
    annotated = betting._annotate_candidate_selection(
        [candidate],
        selected_tickets=[candidate],
        eligible_tickets=[candidate],
        selection_pool=[candidate],
    )[0]
    assert annotated["selected"]
    assert annotated["non_selection_reason"] == ""
    assert annotated["non_selection_reasons"] == []
    assert candidate["non_selection_reasons"] == ["below_minimum_stake"]


@pytest.mark.parametrize(
    "eligible,in_pool,portfolio_failure,expected",
    [
        (False, False, "", "below_minimum_ev"),
        (True, False, "", "bet_type_limit"),
        (True, True, "", "portfolio_optimization"),
        (True, True, "no_safe_portfolio", "no_safe_portfolio"),
    ],
)
def test_legacy_and_portfolio_rejections_keep_compatible_primary_reason(
    eligible, in_pool, portfolio_failure, expected
):
    candidate = {"bet_type": "win", "horse_number": "1"}
    annotated = betting._annotate_candidate_selection(
        [candidate],
        selected_tickets=[],
        eligible_tickets=[candidate] if eligible else [],
        selection_pool=[candidate] if in_pool else [],
        portfolio_failure_reason=portfolio_failure,
    )[0]
    assert annotated["non_selection_reason"] == expected
    assert annotated["non_selection_reasons"] == [expected]
