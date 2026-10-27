"""Synthetic paired trials check dependency preservation, not predictive skill."""
from copy import deepcopy
import math

import pytest

from analysis.joint_monte_carlo import simulate_joint_trials


def inputs():
    return dict(horse_ids=["A", "B"], current_odds=[2., 8.], seed=17, iterations=20,
                manifest={"purpose": "research", "lineage": {"analyzer": "synthetic"}},
                draws=[dict(trial_id="high", horse_ids=["A", "B"], base_probabilities=[.8, .2],
                            environment_logits=[0., 0.], market_log_odds_response=[math.log(2), 0.]),
                       dict(trial_id="low", horse_ids=["A", "B"], base_probabilities=[.2, .8],
                            environment_logits=[0., 0.], market_log_odds_response=[0., 0.])])


def test_seeded_whole_trial_bootstrap_and_paired_ev():
    arguments = inputs()
    original = deepcopy(arguments)
    result = simulate_joint_trials(**arguments)
    assert result == simulate_joint_trials(**arguments)
    other = simulate_joint_trials(**{**arguments, "seed": 18})
    assert result["trials"] != other["trials"]
    assert arguments == original
    assert len({t["trial_id"] for t in result["trials"]}) == 20
    for trial in result["trials"]:
        assert trial["win_probabilities"] == pytest.approx(
            [.8, .2] if trial["source_trial_id"] == "high" else [.2, .8])
        assert sum(trial["win_probabilities"]) == pytest.approx(1)
        assert trial["ev"] == pytest.approx([p * o for p, o in zip(trial["win_probabilities"], trial["odds"])])
    a = result["horses"][0]
    assert a["ev"]["mean"] != pytest.approx(a["win_probability"]["mean"] * a["odds"]["mean"])
    assert result["win_probability_rank"] != result["ev_rank"]
    assert a["ev"]["quantiles"]["0.05"] <= a["ev"]["quantiles"]["0.95"]


def test_environment_second_stage_and_price_do_not_change_structure():
    arguments = inputs()
    arguments["draws"] = [arguments["draws"][0]]
    arguments["draws"][0]["environment_logits"] = [0., math.log(4)]
    result = simulate_joint_trials(**arguments)
    assert result["trials"][0]["win_probabilities"] == pytest.approx([.5, .5])
    arguments["draws"][0]["market_log_odds_response"] = [0., 1.]
    changed = simulate_joint_trials(**arguments)
    assert result["trials"][0]["win_probabilities"] == changed["trials"][0]["win_probabilities"]
    assert result["trials"][0]["odds"] != changed["trials"][0]["odds"]


@pytest.mark.parametrize("field,value", [
    ("horse_ids", []), ("horse_ids", ["A", "A"]), ("horse_ids", ["A", ""]),
    ("seed", True), ("seed", 1.2), ("iterations", 0), ("iterations", True),
    ("manifest", {}), ("manifest", {"purpose": "production", "lineage": {"a": 1}}),
    ("manifest", {"purpose": "research", "lineage": {}}),
    ("draws", []), ("draws", None), ("draws", [None]),
    ("current_odds", [2]), ("current_odds", [True, 3]),
    ("current_odds", [float("nan"), 3]), ("current_odds", [1, 3]),
])
def test_invalid_top_level_inputs_rejected(field, value):
    arguments = inputs()
    arguments[field] = value
    with pytest.raises(ValueError):
        simulate_joint_trials(**arguments)


@pytest.mark.parametrize("field,value", [
    ("trial_id", ""), ("trial_id", "low"), ("horse_ids", ["B", "A"]),
    ("base_probabilities", [.2, .2]), ("base_probabilities", [0, 1]),
    ("base_probabilities", [float("inf"), .2]), ("base_probabilities", [.5]),
    ("environment_logits", None), ("environment_logits", [0, "1"]),
    ("market_log_odds_response", [1000, 0]),
    ("market_log_odds_response", [-1000, 0]),
    ("market_log_odds_response", [709.7, 0]),
])
def test_invalid_draws_and_price_ranges_rejected(field, value):
    arguments = inputs()
    arguments["draws"][0][field] = value
    with pytest.raises(ValueError):
        simulate_joint_trials(**arguments)
