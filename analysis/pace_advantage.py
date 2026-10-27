"""Sequential Monte Carlo pace-count comparisons for a fixed ensemble."""
from __future__ import annotations

import math


PACES = ("high", "mid", "slow")


def compare_pace_counts(counts: dict, look_index: int, alpha: float, min_gap: float) -> dict:
    """Confirm only a leader separated from both competitors at a sequential look.

    For each ordered pair a,b, Y=I(a)-I(b) is in [-1,1]. Hoeffding gives
    P(E[Y] < mean(Y)-sqrt(2*log(1/delta)/n)) <= delta. At look k,
    delta=alpha/(6*k*(k+1)) for all six ordered pairs. Since
    sum_{k>=1} 1/(k*(k+1))=1, union bounds cover all pairs and looks with
    family confidence at least 1-alpha, conditional on iid draws from the SAME
    fixed MC ensemble, with predeclared cumulative draw counts at each look.
    Stopping may depend on data; changing look sizes based on data is not covered.
    Changing ensemble invalidates accumulated look evidence.
    This controls MC sampling error, not model error, future racing accuracy,
    training-data adequacy, or dependence among correlated simulation draws.
    No p-values or unconditional race-performance guarantees are produced.
    """
    if not isinstance(counts, dict) or set(counts) != set(PACES):
        raise ValueError("counts must contain exactly high, mid and slow")
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("counts must be nonnegative strict integers")
    if type(look_index) is not int or look_index < 1:
        raise ValueError("look_index must be a positive strict integer")
    if isinstance(alpha, bool) or isinstance(min_gap, bool):
        raise ValueError("boolean is not a numeric confidence parameter")
    try:
        level, gap = float(alpha), float(min_gap)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence parameters must be numeric") from exc
    if not math.isfinite(level) or not 0 < level < 1 or not math.isfinite(gap) or not 0 <= gap <= 1:
        raise ValueError("require 0<alpha<1 and 0<=min_gap<=1")
    n = sum(counts.values())
    if n <= 0:
        raise ValueError("at least one draw is required")
    # Log-domain arithmetic avoids underflow for small alpha or late looks.
    log_inverse_delta = math.log(6) + math.log(look_index) + math.log(look_index + 1) - math.log(level)
    radius = math.sqrt(2 * log_inverse_delta / n)
    pairs = []
    for left in PACES:
        for right in PACES:
            if left == right:
                continue
            difference = (counts[left] - counts[right]) / n
            lower, upper = max(-1.0, difference - radius), min(1.0, difference + radius)
            pairs.append(dict(pace=left, competitor=right, empirical_gap=difference,
                              lower=lower, upper=upper, exceeds_min_gap=lower > gap))
    winners = [pace for pace in PACES if all(pair["exceeds_min_gap"] for pair in pairs if pair["pace"] == pace)]
    confirmed = winners[0] if winners else None
    return dict(schema_version=1, method="sequential_ordered_pair_hoeffding_alpha_spending",
                status="CONFIRMED" if confirmed is not None else "UNCONFIRMED", confirmed_pace=confirmed,
                counts=dict(counts), draw_count=n, look_index=look_index, alpha=level, min_gap=gap,
                confidence_family=1 - level, log_inverse_pair_delta=log_inverse_delta,
                pair_alpha_spending=math.exp(-log_inverse_delta),
                look_alpha_spending=math.exp(math.log(level) - math.log(look_index) - math.log(look_index + 1)),
                spending_rule="delta_pair(k)=alpha/(6*k*(k+1)); sum_k 6*delta_pair(k)=alpha",
                confidence_scope="MC_sampling_error_conditional_on_fixed_iid_ensemble",
                look_schedule_requirement="predeclared_cumulative_draw_counts",
                limitations=["Not a guarantee of future race accuracy or model calibration.",
                             "Does not measure training sample adequacy.",
                             "Caller must retain fixed ensemble and correct sequential look index."],
                radius=radius, pairwise_comparisons=pairs,
                next_action="USE_CONFIRMED_PACE" if confirmed is not None else "ADD_DRAWS_OR_REMAIN_UNCONFIRMED")
