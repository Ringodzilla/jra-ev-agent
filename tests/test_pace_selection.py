from copy import deepcopy
import pytest

from strategy.pace_selection import select_pace_candidates


def candidate(key="R|win|1", kind="win", horses=None):
    return dict(candidate_key=key, bet_type=kind, horse_numbers=[1] if horses is None else horses,
        pace_metrics={"high": {"hit_probability": .1, "odds": 20, "ev": 2},
                      "mid": {"hit_probability": .05, "odds": 20, "ev": 1},
                      "slow": {"hit_probability": .02, "odds": 20, "ev": .4}})


def select(candidates=None, pace="high", probability=.08, ev=1.1):
    return select_pace_candidates([candidate()] if candidates is None else candidates, pace, probability, ev)


def test_only_selected_pace_thresholds_apply_while_other_sensitivity_remains():
    c = candidate()
    original = deepcopy(c)
    assert len(select([c])["eligible_candidates"]) == 1
    output = select([c], "slow")
    assert output["eligible_candidates"] == []
    assert output["evaluations"][0]["reason"] == "BELOW_EXPLICIT_THRESHOLDS"
    output["evaluations"][0]["pace_sensitivity"]["high"]["ev"] = 999
    assert c == original
    assert output["formal_tickets"] == []
    assert output["formal_go_issued"] is False


def test_selected_pace_missing_and_explicit_unmeasured_do_not_invent_zero():
    output = select(pace=None)
    assert output["status"] == "PACE_NOT_SELECTED"
    assert output["eligible_candidates"] == []
    assert output["evaluations"][0]["eligible"] is None
    c = candidate()
    c["pace_metrics"]["high"] = {"status": "NOT_EVALUATED", "reason": "NO_CONDITIONAL_DATA"}
    output = select([c])
    assert output["evaluations"][0]["reason"] == "NO_CONDITIONAL_DATA"
    assert output["evaluations"][0]["eligible"] is None
    assert output["eligible_candidates"] == []


def test_win_and_combination_probabilities_are_separate_and_no_marginal_fallback():
    win = candidate()
    wide = candidate("R|wide|1-2", "wide", [1, 2])
    wide["pace_metrics"]["high"] = dict(hit_probability=.4, odds=3, ev=1.2)
    win["marginal_hit_probability"] = .9
    output = select([win, wide], probability=.3)
    assert [r["candidate_key"] for r in output["eligible_candidates"]] == [wide["candidate_key"]]
    assert output["evaluations"][0]["selected_metrics"]["hit_probability"] == .1


def test_exact_thresholds_and_high_odds_six_decimal_rounding():
    assert len(select(probability=.1, ev=2)["eligible_candidates"]) == 1
    c = candidate()
    c["pace_metrics"]["high"] = dict(hit_probability=.046002, odds=224.5, ev=10.327426)
    assert len(select([c], probability=0)["eligible_candidates"]) == 1
    c["pace_metrics"]["high"]["ev"] = 10.328
    with pytest.raises(ValueError, match="does not match"):
        select([c])


@pytest.mark.parametrize("probability,ev", [(True, 1), ("bad", 1), (float("nan"), 1), (-.1, 1), (1.1, 1), (0, -1)])
def test_invalid_explicit_thresholds_rejected(probability, ev):
    with pytest.raises(ValueError):
        select(probability=probability, ev=ev)


@pytest.mark.parametrize("items,pace", [(None, "unknown"), ({}, "high"), ([None], "high"),
    ([candidate(), candidate()], "high")])
def test_invalid_container_pace_and_duplicate_keys_rejected(items, pace):
    with pytest.raises(ValueError):
        select(items, pace)


@pytest.mark.parametrize("field,value", [("candidate_key", ""), ("bet_type", "unknown"),
    ("horse_numbers", [1, 2]), ("horse_numbers", [False]), ("horse_numbers", [0])])
def test_invalid_candidate_identity_and_bet_shape(field, value):
    c = candidate()
    c[field] = value
    with pytest.raises(ValueError):
        select([c])


def test_duplicate_runner_ids_and_wakuren_same_frame_semantics():
    with pytest.raises(ValueError):
        select([candidate(kind="wide", horses=["01", 1])])
    assert select([candidate(kind="wakuren", horses=[1, 1])])["eligible_candidates"]


@pytest.mark.parametrize("observation", [None, {"status": "INVALID"},
    {"status": "NOT_EVALUATED"}, {"status": "NOT_EVALUATED", "reason": "missing", "hit_probability": 0},
    {"hit_probability": float("inf"), "odds": 20, "ev": 1},
    {"hit_probability": -.1, "odds": 20, "ev": 1},
    {"hit_probability": .1, "odds": .9, "ev": .09},
    {"hit_probability": .1, "odds": 20, "ev": -1}])
def test_bad_metrics_rejected_even_when_in_unselected_pace(observation):
    c = candidate()
    c["pace_metrics"]["slow"] = observation
    with pytest.raises(ValueError):
        select([c], pace=None)


def test_missing_pace_key_cannot_silently_borrow_other_pace():
    c = candidate()
    c["pace_metrics"].pop("slow")
    with pytest.raises(ValueError, match="three pace"):
        select([c])


def test_inconsistent_numeric_representation_is_rejected():
    class MisleadingNumber:
        def __float__(self):
            return .1
        def __str__(self):
            return "not decimal"
    c = candidate()
    c["pace_metrics"]["high"]["hit_probability"] = MisleadingNumber()
    with pytest.raises(ValueError, match="decimal"):
        select([c])


def test_all_paces_can_be_explicitly_unmeasured_without_becoming_zero():
    c = candidate()
    c["pace_metrics"] = {pace: {"status": "NOT_EVALUATED", "reason": "NO_OBSERVATION"}
                         for pace in ("high", "mid", "slow")}
    result = select([c])
    assert result["evaluations"][0]["status"] == "NOT_EVALUATED"
    assert result["evaluations"][0]["selected_metrics"] == {"status": "NOT_EVALUATED", "reason": "NO_OBSERVATION"}


@pytest.mark.parametrize("kind,horses", [("place", [1]), ("wakuren", [1, 1])])
def test_official_odds_one_is_legal_and_only_explicit_ev_threshold_filters(kind, horses):
    c = candidate(kind=kind, horses=horses)
    c["pace_metrics"]["high"] = dict(hit_probability=.8, odds=1.0, ev=.8)
    result = select([c], probability=.5, ev=1.1)
    assert result["evaluations"][0]["status"] == "EVALUATED"
    assert result["evaluations"][0]["reason"] == "BELOW_EXPLICIT_THRESHOLDS"
    assert result["eligible_candidates"] == []
    assert select([c], probability=.5, ev=.8)["eligible_candidates"]


@pytest.mark.parametrize("explicit_frames", [False, True])
def test_wakuren_numbers_are_typed_as_frames_for_new_and_legacy_inputs(explicit_frames):
    c = candidate(kind="wakuren", horses=[1, 1])
    if explicit_frames:
        c["frame_numbers"] = [2, 3]
        c["horse_numbers"] = []
    result = select([c])["eligible_candidates"][0]
    assert result["number_kind"] == "frame"
    assert result["horse_numbers"] == []
    assert result["frame_numbers"] == ([2, 3] if explicit_frames else [1, 1])
    assert result["source_snapshot_id"] is None
    assert result["source_captured_at"] is None


def test_candidate_quote_provenance_is_retained_without_aliasing_or_invention():
    c = candidate()
    c["source_snapshot_id"] = {"id": "SNAPSHOT-A"}
    c["source_captured_at"] = {"time": "2026-10-25T15:20:00+09:00"}
    result = select([c])["eligible_candidates"][0]
    assert result["number_kind"] == "horse"
    assert result["horse_numbers"] == [1]
    assert result["frame_numbers"] == []
    assert result["source_snapshot_id"] == c["source_snapshot_id"]
    assert result["source_captured_at"] == c["source_captured_at"]
    result["source_snapshot_id"]["id"] = "changed"
    result["source_captured_at"]["time"] = "changed"
    assert c["source_snapshot_id"]["id"] == "SNAPSHOT-A"
    assert c["source_captured_at"]["time"] == "2026-10-25T15:20:00+09:00"


def adaptive_result(**updates):
    from analysis.adaptive_pace import simulate_until_pace_advantage
    return simulate_until_pace_advantage(**dict(dict(horse_ids=["A", "B"], current_odds=[2., 4.],
        seed=17, manifest={"purpose": "research", "lineage": {"source": "synthetic"}},
        initial_iterations=10, batch_iterations=10, max_iterations=100, alpha=.05, min_gap=.01,
        draws=[dict(trial_id="H", horse_ids=["A", "B"], pace="high", base_probabilities=[.8, .2],
                    environment_logits=[0., 0.], market_log_odds_response=[0., 0.])]), **updates))


def confirmed_select(result):
    from strategy.pace_selection import select_confirmed_pace_candidates
    return select_confirmed_pace_candidates([candidate()], result, .08, 1.1)


def test_hypothesis_api_never_claims_statistical_confirmation():
    result = select()
    assert result["statistical_confirmation"] is False
    assert result["hypothesis_only"] is True


def test_real_adaptive_evidence_allows_confirmed_filter_and_preserves_conditional_candidates():
    result = confirmed_select(adaptive_result())
    assert result["statistical_confirmation"] is True
    assert result["hypothesis_only"] is False
    assert result["selected_pace"] == "high"
    assert result["eligible_candidates"]
    assert result["conditional_candidates"] == [candidate()]
    assert result["formal_go_issued"] is False


@pytest.mark.parametrize("result", [{}, {"mainline": {"pace": "high", "statistical_confirmation": False}},
    {"mainline": {"statistical_confirmation": False}, "conditional": {"high": {"summary": "manual"}}}])
def test_raw_or_manual_hypothesis_cannot_masquerade_as_confirmed_selection(result):
    output = confirmed_select(result)
    assert output["selected_pace"] is None
    assert output["eligible_candidates"] == []
    assert output["statistical_confirmation"] is False


def test_unconfirmed_budget_or_requested_pace_mismatch_yields_no_candidates():
    for result in (adaptive_result(initial_iterations=1, max_iterations=1), adaptive_result(selected_pace="slow")):
        output = confirmed_select(result)
        assert output["selected_pace"] is None
        assert output["eligible_candidates"] == []
        assert output["conditional_candidates"]


@pytest.mark.parametrize("mutation", ["no_adaptive", "non_object", "bad_mainline", "flag_type", "bad_adaptive",
    "schedule", "look_count", "look_iterations", "counts", "comparison", "final_state", "forecast",
    "requested", "flags", "missing_field", "after_confirmation", "decreased_counts", "early_stop"])
def test_spoofed_or_inconsistent_adaptive_evidence_is_rejected(mutation):
    result = adaptive_result()
    if mutation == "no_adaptive":
        result.pop("adaptive")
    elif mutation == "non_object":
        result = []
    elif mutation == "bad_mainline":
        result["mainline"] = ["bad"]
    elif mutation == "flag_type":
        result["mainline"]["statistical_confirmation"] = 1
    elif mutation == "bad_adaptive":
        result["adaptive"] = []
    elif mutation == "schedule":
        result["adaptive"]["batch_iterations"] = True
    elif mutation == "look_count":
        result["adaptive"]["look_count"] += 1
    elif mutation == "look_iterations":
        result["adaptive"]["looks"][0]["iterations"] += 1
    elif mutation == "counts":
        result["adaptive"]["looks"][0]["counts"]["high"] += 1
    elif mutation == "comparison":
        result["adaptive"]["looks"][0]["comparison"]["confirmed_pace"] = "slow"
    elif mutation == "final_state":
        result["adaptive"]["iterations"] += 1
    elif mutation == "forecast":
        result["pace_forecast"]["high"]["trial_count"] += 1
    elif mutation == "requested":
        result["mainline"]["requested_pace"] = "invalid"
    elif mutation == "flags":
        result["mainline"]["pace"] = "slow"
    elif mutation == "missing_field":
        result["adaptive"].pop("alpha")
    elif mutation == "after_confirmation":
        result["adaptive"]["looks"].append(deepcopy(result["adaptive"]["looks"][-1]))
        result["adaptive"]["look_count"] += 1
    elif mutation == "decreased_counts":
        result["adaptive"]["looks"][0]["counts"] = {"high": 0, "mid": 10, "slow": 0}
        from analysis.pace_advantage import compare_pace_counts
        result["adaptive"]["looks"][0]["comparison"] = compare_pace_counts(
            result["adaptive"]["looks"][0]["counts"], 1, .05, .01)
    else:
        result["adaptive"]["looks"] = result["adaptive"]["looks"][:1]
        result["adaptive"]["look_count"] = 1
        result["adaptive"]["iterations"] = 10
        result["adaptive"]["additional_iterations"] = 0
        result["adaptive"]["confirmed_pace"] = None
        result["adaptive"]["termination"] = "MAX_ITERATIONS"
    with pytest.raises(ValueError):
        confirmed_select(result)
