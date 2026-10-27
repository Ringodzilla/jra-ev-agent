import math

import pytest

from analysis.pace_advantage import compare_pace_counts


def compare(counts=None, look=1, alpha=.05, gap=.05):
    return compare_pace_counts({"high": 600, "mid": 250, "slow": 150} if counts is None else counts,
                               look, alpha, gap)


def test_clear_leader_beats_every_competitor_with_joint_confidence():
    result = compare()
    assert result["status"] == "CONFIRMED"
    assert result["confirmed_pace"] == "high"
    assert result["draw_count"] == 1000
    assert result["pair_alpha_spending"] == pytest.approx(.05 / 12)
    assert result["look_alpha_spending"] == pytest.approx(.05 / 2)
    assert result["confidence_family"] == .95
    assert len(result["pairwise_comparisons"]) == 6
    assert all(p["lower"] > .05 for p in result["pairwise_comparisons"] if p["pace"] == "high")
    assert "fixed_iid_ensemble" in result["confidence_scope"]


@pytest.mark.parametrize("counts", [{"high": 100, "mid": 100, "slow": 100},
                                     {"high": 101, "mid": 100, "slow": 99},
                                     {"high": 6, "mid": 3, "slow": 1}])
def test_tie_near_tie_and_insufficient_trials_do_not_force_a_pace(counts):
    result = compare(counts)
    assert result["confirmed_pace"] is None
    assert result["status"] == "UNCONFIRMED"
    assert result["next_action"] == "ADD_DRAWS_OR_REMAIN_UNCONFIRMED"


def test_more_draws_can_establish_advantage_without_changing_empirical_proportions():
    small = compare({"high": 6, "mid": 3, "slow": 1})
    large = compare({"high": 6000, "mid": 3000, "slow": 1000}, look=2)
    assert small["confirmed_pace"] is None
    assert large["confirmed_pace"] == "high"
    assert large["radius"] < small["radius"]


def test_reverse_pairs_define_upper_bounds_and_spending_decreases_over_looks():
    first, later = compare(), compare(look=4)
    pairs = {(p["pace"], p["competitor"]): p for p in first["pairwise_comparisons"]}
    for (left, right), p in pairs.items():
        assert p["upper"] == pytest.approx(-pairs[right, left]["lower"])
        assert p["empirical_gap"] == pytest.approx(-pairs[right, left]["empirical_gap"])
    assert later["pair_alpha_spending"] == pytest.approx(.05 / (4 * 5 * 6))
    assert later["radius"] > first["radius"]


def test_tiny_sample_bounds_are_clipped_and_min_gap_is_strict():
    result = compare({"high": 1, "mid": 0, "slow": 0}, gap=1)
    assert result["confirmed_pace"] is None
    assert all(-1 <= p["lower"] <= p["upper"] <= 1 for p in result["pairwise_comparisons"])
    # Even a degenerate empirical leader cannot meet a required true gap of 1.
    assert compare({"high": 1000000, "mid": 0, "slow": 0}, gap=1)["confirmed_pace"] is None


@pytest.mark.parametrize("counts", [None, {}, {"high": 1, "mid": 0},
    {"high": 1, "mid": 0, "slow": 0, "other": 1},
    {"high": True, "mid": 0, "slow": 0}, {"high": 1.0, "mid": 0, "slow": 0},
    {"high": -1, "mid": 1, "slow": 1}, {"high": 0, "mid": 0, "slow": 0}])
def test_bad_count_schema_rejected(counts):
    with pytest.raises(ValueError):
        compare_pace_counts(counts, 1, .05, .05)


@pytest.mark.parametrize("look", [0, -1, True, 1.0, "1"])
def test_look_index_is_a_strict_positive_integer(look):
    with pytest.raises(ValueError):
        compare(look=look)


@pytest.mark.parametrize("alpha,gap", [(0, 0), (1, 0), (-1, 0), (float("nan"), 0),
    (.05, float("inf")), (.05, -.01), (.05, 1.1), (True, 0), (.05, False), (None, 0), ("bad", 0)])
def test_invalid_confidence_or_gap_rejected(alpha, gap):
    with pytest.raises(ValueError):
        compare(alpha=alpha, gap=gap)


def test_spending_telescopes_and_log_domain_preserves_tiny_delta():
    alpha = .05
    spent = sum(6 * compare(look=k)["pair_alpha_spending"] for k in range(1, 101))
    assert spent == pytest.approx(alpha * (1 - 1 / 101))
    result = compare(look=10**100, alpha=1e-300)
    assert math.isfinite(result["radius"])
    assert result["log_inverse_pair_delta"] > 1000
    assert result["confirmed_pace"] is None
