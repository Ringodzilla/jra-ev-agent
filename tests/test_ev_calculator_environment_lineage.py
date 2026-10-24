from __future__ import annotations

from copy import deepcopy

import pytest

from src.agents.ev_calculator import (
    ENVIRONMENT_PROBABILITY_FORMULA,
    ENVIRONMENT_PROBABILITY_TOLERANCE,
    EVCalculatorAgent,
    _row_identity,
)


def _scenario_rows(*, promoted: bool = False) -> list[dict[str, object]]:
    scores = (0.2, -0.2) if promoted else (0.0, 0.0)
    rows: list[dict[str, object]] = []
    for number, score in enumerate(scores, start=1):
        rows.append(
            {
                "race_id": "R1",
                "horse_id": f"horse-{number}",
                "horse_number": str(number),
                "horse_name": f"Horse {number}",
                "frame_number": str(number),
                "current_odds": str(number + 1),
                "ability_score": 0.7 if number == 1 else 0.3,
                "course_score": 0.5,
                "pace_score": 0.5,
                "weight_score": 0.0,
                "jockey_score": 0.5,
                "consistency": 0.5,
                "market_support": 0.5,
                "environment_score": score,
                "environment_adjustment_applied": promoted,
                "environment_model_status": "promoted" if promoted else "not_promoted",
                "environment_model_id": "model-1" if promoted else "",
                "environment_observation_id": "observation-1",
                "environment_observation_sha256": "a" * 64,
                "target_environment_captured_at": "2026-10-18T15:00:00+09:00",
            }
        )
    return rows


def test_ev_calculator_saves_and_validates_neutral_environment_lineage() -> None:
    scenario_rows = _scenario_rows()
    original = deepcopy(scenario_rows)
    combo_odds = [
        {
            "race_id": "R1",
            "bet_type": "win",
            "combination": str(number),
            "odds": "2.0",
            "snapshot_id": "snapshot-1",
            "captured_at": "2026-10-18T06:00:00+00:00",
        }
        for number in (1, 2)
    ]

    result = EVCalculatorAgent().run(scenario_rows, combo_odds=combo_odds)

    validation = result["environment_probability_validation"]
    assert validation["status"] == "OK"
    assert validation["formula"] == ENVIRONMENT_PROBABILITY_FORMULA
    assert validation["tolerance"] == ENVIRONMENT_PROBABILITY_TOLERANCE
    assert validation["row_count"] == 2
    assert len(validation["checks"]) == 2
    assert result["validation"]["status"] == "OK"
    assert len(result["candidate_evaluations"]) == 2
    for row, check in zip(
        sorted(result["ev_rows"], key=lambda item: item["horse_number"]),
        validation["checks"],
    ):
        assert row["p_base"] == row["p_final"] == row["win_prob"]
        assert row["environment_probability_formula"] == ENVIRONMENT_PROBABILITY_FORMULA
        assert row["environment_observation_sha256"] == "a" * 64
        assert check["p_base"] == check["p_final"] == check["expected_p_final"]
        assert check["environment_score"] == 0.0
        assert check["environment_adjustment_applied"] is False
        assert check["absolute_difference"] == 0.0
        assert check["valid"] is True
    assert scenario_rows == original


def test_ev_calculator_validates_promoted_environment_formula() -> None:
    result = EVCalculatorAgent().run(_scenario_rows(promoted=True))
    rows = sorted(result["ev_rows"], key=lambda item: item["horse_number"])
    checks = result["environment_probability_validation"]["checks"]

    assert float(rows[0]["p_final"]) > float(rows[0]["p_base"])
    assert float(rows[1]["p_final"]) < float(rows[1]["p_base"])
    assert sum(float(row["p_final"]) for row in rows) == pytest.approx(1.0, abs=2e-6)
    assert all(check["absolute_difference"] <= ENVIRONMENT_PROBABILITY_TOLERANCE for check in checks)


@pytest.mark.parametrize(
    ("score", "applied", "status", "message"),
    [
        (0.1, False, "not_promoted", "unpromoted environment model"),
        (0.0, True, "not_promoted", "unpromoted environment model"),
        (0.1, False, "promoted", "environment_score must be zero"),
    ],
)
def test_ev_calculator_rejects_invalid_environment_policy(
    score: float,
    applied: bool,
    status: str,
    message: str,
) -> None:
    rows = _scenario_rows()
    rows[0].update(
        environment_score=score,
        environment_adjustment_applied=applied,
        environment_model_status=status,
    )

    with pytest.raises(ValueError, match=message):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_rejects_partial_race_adjustment() -> None:
    rows = _scenario_rows(promoted=True)
    rows[1].update(environment_score=0.0, environment_adjustment_applied=False)

    with pytest.raises(ValueError, match="complete race field"):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_fails_neutral_probability_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _scenario_rows()
    baseline = [dict(row, win_prob=probability) for row, probability in zip(rows, ("0.4", "0.6"))]
    final = [dict(row, win_prob=probability) for row, probability in zip(rows, ("0.41", "0.59"))]
    outputs = iter((baseline, final))
    monkeypatch.setattr(
        "src.agents.ev_calculator.compute_ev",
        lambda scenario_rows, weights: next(outputs),
    )

    with pytest.raises(ValueError, match="neutral environment probability invariant failed"):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_fails_promoted_probability_formula_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _scenario_rows(promoted=True)
    baseline = [dict(row, win_prob=probability) for row, probability in zip(rows, ("0.4", "0.6"))]
    final = [dict(row, win_prob=probability) for row, probability in zip(rows, ("0.4", "0.6"))]
    outputs = iter((baseline, final))
    monkeypatch.setattr(
        "src.agents.ev_calculator.compute_ev",
        lambda scenario_rows, weights: next(outputs),
    )

    with pytest.raises(ValueError, match="environment probability formula validation failed"):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_rejects_lost_lineage_and_row_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _scenario_rows()
    baseline = [dict(row, win_prob="0.5") for row in rows]
    final = [dict(row, win_prob="0.5") for row in rows]
    final[0].pop("environment_observation_sha256")
    outputs = iter((baseline, final))
    monkeypatch.setattr(
        "src.agents.ev_calculator.compute_ev",
        lambda scenario_rows, weights: next(outputs),
    )

    with pytest.raises(ValueError, match="lost simulator environment lineage"):
        EVCalculatorAgent().run(rows)

    mismatched = [dict(row, win_prob="0.5") for row in rows]
    mismatched[0]["horse_id"] = "different"
    outputs = iter((baseline, mismatched))
    with pytest.raises(ValueError, match="do not match simulator row identities"):
        EVCalculatorAgent().run(rows)


@pytest.mark.parametrize("value", ["bad", float("nan"), 0.0, 1.1])
def test_ev_calculator_rejects_invalid_environment_numbers(
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _scenario_rows()
    if value in (0.0, 1.1):
        outputs = iter(
            (
                [dict(row, win_prob=value) for row in rows],
                [dict(row, win_prob="0.5") for row in rows],
            )
        )
        monkeypatch.setattr(
            "src.agents.ev_calculator.compute_ev",
            lambda scenario_rows, weights: next(outputs),
        )
    else:
        rows[0]["environment_score"] = value

    with pytest.raises(ValueError, match="must be"):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_rejects_duplicate_row_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _scenario_rows()
    baseline = [dict(rows[0], win_prob="0.5"), dict(rows[0], win_prob="0.5")]
    final = [dict(row, win_prob="0.5") for row in rows]
    outputs = iter((baseline, final))
    monkeypatch.setattr(
        "src.agents.ev_calculator.compute_ev",
        lambda scenario_rows, weights: next(outputs),
    )

    with pytest.raises(ValueError, match="duplicate baseline EV row identity"):
        EVCalculatorAgent().run(rows)


def test_ev_calculator_row_identity_falls_back_to_index() -> None:
    assert _row_identity({"race_id": "R1"}, 3) == ("R1", "row_index", "3")
