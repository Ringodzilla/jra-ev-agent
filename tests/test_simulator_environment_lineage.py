from __future__ import annotations

from copy import deepcopy

import pytest

from src.agents.simulator import SimulatorAgent


def _feature_row(horse_id: str, horse_number: str, front_rate: float) -> dict[str, object]:
    digest = horse_number * 64
    return {
        "race_id": "20261018_京都_11",
        "horse_id": horse_id,
        "horse_number": horse_number,
        "horse_name": f"Horse {horse_number}",
        "front_rate": front_rate,
        "closing_strength": 1.0 - front_rate,
        "ability_score": 0.5,
        "course_score": 0.5,
        "consistency": 0.5,
        "environment_score": 0.0,
        "environment_adjustment_applied": False,
        "environment_model_status": "not_promoted",
        "environment_not_applied_reason": "no_promoted_environment_model",
        "environment_observation_id": f"environment-v2:20261018_京都_11:{horse_number}",
        "environment_observation_sha256": digest,
        "environment_source_sha256s": {
            "weather": "a" * 64,
            "moisture": "b" * 64,
            "cushion": "c" * 64,
        },
        "target_environment_captured_at": "2026-10-18T14:30:00+09:00",
    }


def test_simulator_preserves_environment_lineage_and_probability_mix() -> None:
    feature_rows = [
        _feature_row("horse-1", "1", 0.8),
        _feature_row("horse-2", "2", 0.2),
    ]
    original = deepcopy(feature_rows)

    scenario_rows = SimulatorAgent().run(feature_rows)["scenario_rows"]

    assert len(scenario_rows) == len(feature_rows)
    for analyzer_row, scenario_row in zip(original, scenario_rows):
        expected_lineage = {
            key: value
            for key, value in analyzer_row.items()
            if key.startswith("environment_") or key.startswith("target_environment_")
        }
        actual_lineage = {
            key: value
            for key, value in scenario_row.items()
            if key.startswith("environment_") or key.startswith("target_environment_")
        }
        assert actual_lineage == expected_lineage
        assert scenario_row["environment_score"] == 0.0
        assert scenario_row["environment_adjustment_applied"] is False
        assert sum(float(scenario_row[key]) for key in (
            "pace_mix_high",
            "pace_mix_mid",
            "pace_mix_slow",
        )) == pytest.approx(1.0, abs=1e-12)

    scenario_rows[0]["environment_source_sha256s"]["weather"] = "changed"
    assert feature_rows == original


def test_simulator_rejects_applied_unpromoted_environment_model() -> None:
    feature_row = _feature_row("horse-1", "1", 0.5)
    feature_row["environment_score"] = 0.2
    feature_row["environment_adjustment_applied"] = True

    with pytest.raises(ValueError, match="unpromoted environment model"):
        SimulatorAgent().run([feature_row])


def test_simulator_accepts_rows_without_environment_lineage() -> None:
    feature_row = _feature_row("horse-1", "1", 0.5)
    feature_row = {
        key: value
        for key, value in feature_row.items()
        if not key.startswith("environment_") and not key.startswith("target_environment_")
    }

    scenario_row = SimulatorAgent().run([feature_row])["scenario_rows"][0]

    assert scenario_row["horse_id"] == "horse-1"
    assert not any(key.startswith("environment_") for key in scenario_row)


def test_simulator_rejects_duplicate_analyzer_lineage_identity() -> None:
    feature_row = _feature_row("duplicate", "1", 0.5)

    with pytest.raises(ValueError, match="duplicate analyzer row identity"):
        SimulatorAgent().run([feature_row, deepcopy(feature_row)])


def test_simulator_rejects_lineage_row_lost_by_scenario_output(monkeypatch) -> None:
    feature_row = _feature_row("horse-1", "1", 0.5)
    unrelated_scenario_row = {
        "race_id": feature_row["race_id"],
        "horse_id": "other-horse",
    }
    monkeypatch.setattr(
        "src.agents.simulator.simulate_race_scenarios",
        lambda _: [unrelated_scenario_row],
    )

    with pytest.raises(ValueError, match="lost analyzer environment lineage rows"):
        SimulatorAgent().run([feature_row])


def test_simulator_uses_row_index_when_horse_identity_is_unavailable(monkeypatch) -> None:
    feature_row = {
        "race_id": "race-without-horse-identity",
        "environment_score": 0.0,
        "environment_adjustment_applied": False,
        "environment_model_status": "not_promoted",
        "environment_not_applied_reason": "no_promoted_environment_model",
        "environment_observation_id": "observation-1",
        "environment_observation_sha256": "d" * 64,
        "environment_source_sha256s": {"weather": "e" * 64},
    }
    monkeypatch.setattr(
        "src.agents.simulator.simulate_race_scenarios",
        lambda rows: [dict(row) for row in rows],
    )

    scenario_row = SimulatorAgent().run([feature_row])["scenario_rows"][0]

    assert scenario_row == feature_row
    assert scenario_row is not feature_row
    assert scenario_row["environment_source_sha256s"] is not feature_row["environment_source_sha256s"]


def test_simulator_preserves_promoted_environment_adjustment() -> None:
    feature_row = _feature_row("horse-1", "1", 0.5)
    feature_row.update(
        environment_score=0.125,
        environment_adjustment_applied=True,
        environment_model_status="promoted",
        environment_not_applied_reason="",
    )

    scenario_row = SimulatorAgent().run([feature_row])["scenario_rows"][0]

    assert scenario_row["environment_score"] == 0.125
    assert scenario_row["environment_adjustment_applied"] is True
    assert scenario_row["environment_model_status"] == "promoted"


def test_simulator_preserves_invalid_neutral_score_without_applying_it() -> None:
    feature_row = _feature_row("horse-1", "1", 0.5)
    feature_row["environment_score"] = "not-numeric"

    scenario_row = SimulatorAgent().run([feature_row])["scenario_rows"][0]

    assert scenario_row["environment_score"] == "not-numeric"
    assert scenario_row["environment_adjustment_applied"] is False
