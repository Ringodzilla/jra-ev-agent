"""Explicit synthetic pace cases preserve paired dependencies and mixtures."""
from copy import deepcopy

import pytest

from analysis.pace_conditioning import simulate_pace_conditioned


def arguments():
    return dict(horse_ids=["A", "B"], current_odds=[2., 4.], seed=4, iterations=2,
                manifest={"purpose": "research", "lineage": {"source": "synthetic"}},
                draws=[dict(trial_id="H", horse_ids=["A", "B"], pace="high",
                            base_probabilities=[.8, .2], environment_logits=[0., 0.],
                            market_log_odds_response=[0., 0.]),
                       dict(trial_id="S", horse_ids=["A", "B"], pace="slow",
                            base_probabilities=[.2, .8], environment_logits=[0., 0.],
                            market_log_odds_response=[0., 0.])])


def test_tied_forecast_and_conditional_rank_reversal():
    args = arguments()
    before = deepcopy(args)
    result = simulate_pace_conditioned(**args)
    assert result["mainline"]["status"] == "AMBIGUOUS"
    assert result["mainline"]["pace"] is None
    assert result["mainline"]["summary"] is None
    high, slow = [result["conditional"][p]["summary"] for p in ["high", "slow"]]
    assert high["win_probability_rank"] == ["A", "B"]
    assert slow["win_probability_rank"] == ["B", "A"]
    assert high["ev_rank"] == ["A", "B"]
    assert slow["ev_rank"] == ["B", "A"]
    assert result["conditional"]["mid"]["summary"] is None
    assert result["pace_forecast"]["mid"]["frequency"] == 0
    assert result["mixture_validation"]["weighted_probabilities"] == pytest.approx([.5, .5])
    assert args == before
    for trial in result["marginal"]["trials"]:
        assert trial["pace"] == {"H": "high", "S": "slow"}[trial["source_trial_id"]]
    for summary in [high, slow, result["marginal"]]:
        assert sum(h["win_probability"]["mean"] for h in summary["horses"]) == pytest.approx(1)


def test_fixed_selection_does_not_rewrite_forecast_or_marginal():
    automatic = simulate_pace_conditioned(**arguments())
    fixed = simulate_pace_conditioned(**arguments(), selected_pace="slow")
    assert fixed["mainline"]["status"] == "UNCONFIRMED"
    assert fixed["mainline"]["pace"] is None
    assert fixed["mainline"]["summary"] is None
    assert fixed["mainline"]["requested_pace"] == "slow"
    assert fixed["mainline"]["hypothesis_pace"] == "slow"
    assert fixed["mainline"]["hypothesis_summary"] == fixed["conditional"]["slow"]["summary"]
    assert fixed["mainline"]["statistical_confirmation"] is False
    assert fixed["mainline"]["selection_method"] == "caller_requested_hypothesis"
    assert fixed["marginal"] == automatic["marginal"]
    absent = simulate_pace_conditioned(**arguments(), selected_pace="mid")
    assert absent["mainline"]["status"] == "UNAVAILABLE"
    assert absent["mainline"]["summary"] is None


def test_seed_reproducibility_and_frequency_only_mainline():
    args = {**arguments(), "iterations": 11}
    result = simulate_pace_conditioned(**args)
    assert result == simulate_pace_conditioned(**args)
    other = simulate_pace_conditioned(**{**args, "seed": 15})
    assert result["marginal"]["trials"] != other["marginal"]["trials"]
    counts = result["pace_forecast"]
    expected = max(counts, key=lambda p: counts[p]["trial_count"])
    assert result["mainline"]["pace"] is None
    assert result["mainline"]["suggested_pace"] == expected
    assert result["mainline"]["summary"] is None
    assert result["mainline"]["statistical_confirmation"] is False
    assert result["mainline"]["status"] == "UNCONFIRMED"
    altered = deepcopy(args)
    altered["current_odds"] = [20., 2.]
    assert simulate_pace_conditioned(**altered)["mainline"]["suggested_pace"] == expected


@pytest.mark.parametrize("field,value", [("selected_pace", "unknown"), ("draws", []),
                                          ("draws", None), ("draws", [{}]), ("draws", [None])])
def test_explicit_pace_contract_rejects_missing_information(field, value):
    args = arguments()
    args[field] = value
    with pytest.raises(ValueError):
        simulate_pace_conditioned(**args)


def test_marginal_corruption_is_detected(monkeypatch):
    import analysis.pace_conditioning as module

    original = module.simulate_joint_trials

    def corrupted(**kwargs):
        output = original(**kwargs)
        output["horses"][0]["win_probability"]["mean"] = .9
        return output

    monkeypatch.setattr(module, "simulate_joint_trials", corrupted)
    with pytest.raises(ValueError, match="mixture"):
        simulate_pace_conditioned(**arguments())
