"""Bounded sequential sampling preserves one reproducible empirical prefix."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from analysis.adaptive_pace import simulate_until_pace_advantage


def arguments():
    return dict(horse_ids=["A", "B"], current_odds=[2., 4.], seed=17,
                manifest={"purpose": "research", "lineage": {"source": "synthetic"}},
                initial_iterations=10, batch_iterations=10, max_iterations=100,
                alpha=.05, min_gap=.01,
                draws=[dict(trial_id="H", horse_ids=["A", "B"], pace="high",
                            base_probabilities=[.8, .2], environment_logits=[0., 0.],
                            market_log_odds_response=[0., 0.])])


def test_clear_advantage_and_same_seed_final_prefix():
    args = arguments()
    before = deepcopy(args)
    result = simulate_until_pace_advantage(**args)
    assert result == simulate_until_pace_advantage(**args)
    assert result["mainline"]["pace"] == "high"
    assert result["adaptive"]["termination"] == "CONFIRMED"
    assert result["adaptive"]["prefix_reproduced"]
    assert result["adaptive"]["iterations"] <= args["max_iterations"]
    assert args == before


def test_inconclusive_budget_does_not_force_requested_mainline():
    args = arguments()
    args.update(initial_iterations=1, max_iterations=1, selected_pace="high")
    result = simulate_until_pace_advantage(**args)
    assert result["mainline"]["pace"] is None
    assert result["mainline"]["requested_pace"] == "high"
    assert result["adaptive"]["termination"] == "MAX_ITERATIONS"
    assert result["conditional"]["high"]["summary"] is not None


def test_confirmed_other_pace_is_not_requested_mainline():
    result = simulate_until_pace_advantage(**arguments(), selected_pace="slow")
    assert result["mainline"]["pace"] is None
    assert result["adaptive"]["confirmed_pace"] == "high"
    assert result["mainline"]["reason"] == "REQUESTED_PACE_DIFFERS_FROM_CONFIRMED_PACE"


def test_batches_extend_same_prefix_and_integrate_once(monkeypatch):
    import analysis.adaptive_pace as module

    args = arguments()
    args["max_iterations"] = 25
    args["draws"].append({**deepcopy(args["draws"][0]), "trial_id": "S", "pace": "slow"})
    monkeypatch.setattr(module, "compare_pace_counts", lambda *a, **kw: {"confirmed_pace": None})
    integrator = Mock(wraps=module.simulate_pace_conditioned)
    monkeypatch.setattr(module, "simulate_pace_conditioned", integrator)
    result = simulate_until_pace_advantage(**args)
    assert integrator.call_count == 1
    assert [look["iterations"] for look in result["adaptive"]["looks"]] == [10, 20, 25]
    assert result["adaptive"]["additional_iterations"] == 15
    previous = {p: 0 for p in ("high", "mid", "slow")}
    for look in result["adaptive"]["looks"]:
        assert all(look["counts"][p] >= previous[p] for p in previous)
        previous = look["counts"]


@pytest.mark.parametrize("field,value", [("initial_iterations", 0), ("batch_iterations", True),
    ("max_iterations", 1.5), ("max_iterations", 1), ("seed", False),
    ("selected_pace", "unknown"), ("draws", []), ("draws", [{}]), ("draws", [None])])
def test_invalid_adaptive_inputs_rejected(field, value):
    args = arguments()
    args[field] = value
    with pytest.raises(ValueError):
        simulate_until_pace_advantage(**args)


def test_final_validation_rejects_bad_numeric_source_even_if_not_sampled():
    args = arguments()
    args["draws"][0]["base_probabilities"] = [.8, .8]
    with pytest.raises(ValueError):
        simulate_until_pace_advantage(**args)


def test_integrator_prefix_corruption_rejected(monkeypatch):
    import analysis.adaptive_pace as module

    original = module.simulate_pace_conditioned

    def corrupted(**kw):
        result = original(**kw)
        result["marginal"]["trials"][0]["source_trial_id"] = "not_the_sampled_source"
        return result

    monkeypatch.setattr(module, "simulate_pace_conditioned", corrupted)
    with pytest.raises(ValueError, match="prefix"):
        simulate_until_pace_advantage(**arguments())
