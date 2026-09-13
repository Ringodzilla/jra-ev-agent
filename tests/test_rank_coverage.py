from __future__ import annotations

from unittest.mock import patch

from src.agents.reviewer import ReviewerAgent, _rank_coverage_errors
from src.agents.settings import WorkflowSettings
from strategy import betting


def _ev_rows() -> list[dict[str, object]]:
    probabilities = [0.40, 0.22, 0.18, 0.10, 0.10]
    return [
        {
            "race_id": "R",
            "horse_number": str(number),
            "horse_name": f"Horse {number}",
            "win_prob": probability,
            "current_odds": 2.0 if number == 1 else 8.0,
            "predicted_odds": 3.0 if number == 1 else 8.0,
            "ev_current": probability * (2.0 if number == 1 else 8.0),
            "ev_predicted": probability * (3.0 if number == 1 else 8.0),
        }
        for number, probability in enumerate(probabilities, start=1)
    ]


def _source(bet_type: str, combination: str, probability: float, odds: float) -> dict[str, object]:
    return {
        "key": f"R|{bet_type}|{combination}",
        "race_id": "R",
        "bet_type": bet_type,
        "canonical_combination": combination,
        "hit_prob": probability,
        "official_odds": odds,
        "ev": probability * odds,
        "snapshot_id": "S",
        "captured_at": "2026-09-13T06:20:00+00:00",
    }


def _canonical_ticket(evaluation: dict[str, object], **_kwargs: object) -> dict[str, object]:
    combination = str(evaluation["canonical_combination"])
    numbers = combination.replace(">", "-").split("-")
    probability = float(evaluation["hit_prob"])
    odds = float(evaluation["official_odds"])
    current_ev = probability * odds
    return {
        "race_id": "R",
        "bet_type": evaluation["bet_type"],
        "horse_number": combination,
        "horse_numbers": numbers,
        "horse_names": [f"Horse {number}" for number in numbers],
        "horse_name": " - ".join(f"Horse {number}" for number in numbers),
        "raw_hit_prob": probability,
        "hit_prob": probability,
        "robust_hit_prob": probability,
        "official_odds_current": odds,
        "predicted_odds": odds,
        "win_odds": odds,
        "odds_source": "jra_live",
        "probability_source": "04_ev_calculator",
        "source_candidate_key": evaluation["key"],
        "source_snapshot_id": "S",
        "source_captured_at": evaluation["captured_at"],
        "robust_odds": odds,
        "robust_ev": current_ev,
        "ev": current_ev,
        "ev_current": current_ev,
        "ev_predicted": current_ev,
        "stake": 0,
        "confidence": 1.0,
        "probability_lineage": {
            "source": "04_ev_calculator",
            "candidate_key": evaluation["key"],
            "snapshot_id": "S",
            "captured_at": evaluation["captured_at"],
            "raw_hit_prob": probability,
        },
    }


def test_clear_top3_promotes_small_trio_and_winner_fixed_trifectas() -> None:
    sources = [
        _source("sanrenpuku", "1-2-3", 0.10, 9.5),
        _source("sanrentan", "1>2>3", 0.03, 31.0),
        _source("sanrentan", "1>3>2", 0.035, 26.5),
    ]
    with patch.object(betting, "_canonical_ticket_from_evaluation", side_effect=_canonical_ticket):
        plan = betting.generate_tickets(
            _ev_rows(),
            candidate_evaluations=sources,
            candidate_validation={"status": "OK"},
        )

    assert [(ticket["bet_type"], ticket["horse_number"]) for ticket in plan["tickets"]] == [
        ("sanrenpuku", "1-2-3"),
        ("sanrentan", "1>2>3"),
        ("sanrentan", "1>3>2"),
    ]
    assert all(ticket["stake"] == 100 for ticket in plan["tickets"])
    assert all(ticket["coverage_reason"] == "top3_rank_coverage" for ticket in plan["tickets"])
    assert plan["rank_coverage"] == {
        "enabled": True,
        "budget_yen": 300,
        "ticket_count": 3,
        "stake": 300,
    }

    review = ReviewerAgent(WorkflowSettings()).run(
        {"quality_report": {}, "entries": []},
        _ev_rows(),
        _ev_rows(),
        plan,
        attempt=0,
    )
    assert review["status"] == "OK"
    assert review["rank_coverage"] == {"ticket_count": 3, "stake": 300, "errors": []}
    assert review["divergent_rows"]
    assert review["selected_divergent_rows"] == []


def test_rank_coverage_stays_closed_when_third_is_not_separated() -> None:
    rows = _ev_rows()
    for row, probability in zip(rows, [0.26, 0.25, 0.17, 0.16, 0.16]):
        row["win_prob"] = probability
    candidates = [_canonical_ticket(_source("sanrenpuku", "1-2-3", 0.10, 9.5))]

    summary = betting._promote_top3_rank_coverage_candidates(
        candidates,
        rows,
        enabled=True,
        budget_yen=300,
        min_robust_ev=0.90,
        min_top3_probability=0.55,
        min_leader_probability=0.25,
        min_third_vs_fourth_ratio=1.10,
    )

    assert not summary["triggered"]
    assert not summary["criteria"]["third_vs_fourth_ratio_passed"]
    assert candidates[0]["stake"] == 0


def test_reviewer_rejects_rank_coverage_above_the_small_stake_contract() -> None:
    sources = [
        _source("sanrenpuku", "1-2-3", 0.10, 9.5),
        _source("sanrentan", "1>2>3", 0.03, 31.0),
        _source("sanrentan", "1>3>2", 0.035, 26.5),
    ]
    with patch.object(betting, "_canonical_ticket_from_evaluation", side_effect=_canonical_ticket):
        plan = betting.generate_tickets(
            _ev_rows(),
            candidate_evaluations=sources,
            candidate_validation={"status": "OK"},
        )
    plan["tickets"][0]["stake"] = 200

    review = ReviewerAgent(WorkflowSettings()).run(
        {"quality_report": {}, "entries": []},
        _ev_rows(),
        _ev_rows(),
        plan,
        attempt=0,
    )

    assert review["status"] == "NG"
    assert "RANK_COVERAGE_INVALID" in review["reason_codes"]
    assert "stake is not 100 yen" in review["reason"]


def test_rank_coverage_skips_missing_target_candidates() -> None:
    unrelated = _canonical_ticket(_source("sanrentan", "2>1>3", 0.03, 31.0))

    summary = betting._promote_top3_rank_coverage_candidates(
        [unrelated],
        _ev_rows(),
        enabled=True,
        budget_yen=300,
        min_robust_ev=0.90,
        min_top3_probability=0.55,
        min_leader_probability=0.25,
        min_third_vs_fourth_ratio=1.10,
    )

    assert not summary["triggered"]
    assert summary["candidates"] == []


def test_rank_coverage_reviewer_reports_every_contract_violation() -> None:
    settings = WorkflowSettings(
        rank_coverage_enabled=False,
        rank_coverage_budget_yen=300,
        min_rank_coverage_ev=0.90,
        min_rank_coverage_top3_probability=0.90,
        min_rank_coverage_leader_probability=0.50,
        min_rank_coverage_third_vs_fourth_ratio=2.0,
    )
    malformed = {
        "bet_type": "wide",
        "horse_number": "1-2",
        "ticket_role": "value",
        "odds_source": "estimated",
        "stake": 200,
        "robust_ev": 0.50,
    }

    too_short = _rank_coverage_errors([malformed], _ev_rows()[:2], settings=settings)
    errors = _rank_coverage_errors([dict(malformed) for _ in range(4)], _ev_rows(), settings=settings)

    assert too_short == ["feature is disabled", "fewer than three ranked horses"]
    assert "top-three probability is below the configured minimum" in errors
    assert "leader probability is below the configured minimum" in errors
    assert "third-versus-fourth separation is below the configured minimum" in errors
    assert "unexpected combination: wide 1-2" in errors
    assert "duplicate combination: wide 1-2" in errors
    assert "ticket role is invalid: 1-2" in errors
    assert "official live odds are missing: 1-2" in errors
    assert "robust EV is below the coverage minimum: 1-2" in errors
    assert "coverage stake exceeds its budget" in errors
    assert "coverage ticket count exceeds three" in errors
