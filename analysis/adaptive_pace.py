"""Bounded research bootstrap with sequential evidence for a mainline pace."""
from __future__ import annotations

import random
from copy import deepcopy

from analysis.pace_conditioning import PACES, simulate_pace_conditioned
from analysis.pace_advantage import compare_pace_counts


def simulate_until_pace_advantage(*, horse_ids: list[str], current_odds: list[float],
                                  draws: list[dict], seed: int, manifest: dict,
                                  initial_iterations: int, batch_iterations: int,
                                  max_iterations: int, alpha: float, min_gap: float,
                                  selected_pace: str | None = None) -> dict:
    """Extend the same seed's prefix, then integrate its final prefix once.

    This is uncertainty of the supplied empirical draw distribution, not proof
    that its forecast is calibrated against real races. No production GO gate
    or learned coefficient is added.
    """
    for name, value in (("initial_iterations", initial_iterations),
                        ("batch_iterations", batch_iterations), ("max_iterations", max_iterations)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if max_iterations < initial_iterations:
        raise ValueError("max_iterations must be at least initial_iterations")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if selected_pace is not None and selected_pace not in PACES:
        raise ValueError("selected_pace must be high, mid, slow or None")
    if not isinstance(draws, list) or not draws or any(
            not isinstance(d, dict) or d.get("pace") not in PACES for d in draws):
        raise ValueError("explicit pace-labelled draws required")
    rng = random.Random(seed)
    counts = {pace: 0 for pace in PACES}
    source_indices, looks = [], []
    target = initial_iterations
    while True:
        while len(source_indices) < target:
            index = rng.randrange(len(draws))
            source_indices.append(index)
            counts[draws[index]["pace"]] += 1
        comparison = compare_pace_counts(counts, look_index=len(looks) + 1, alpha=alpha, min_gap=min_gap)
        looks.append({"iterations": target, "counts": dict(counts), "comparison": deepcopy(comparison)})
        confirmed = comparison.get("confirmed_pace")
        if confirmed is not None or target == max_iterations:
            break
        target = min(target + batch_iterations, max_iterations)
    result = simulate_pace_conditioned(horse_ids=horse_ids, current_odds=current_odds,
                                      draws=draws, seed=seed, iterations=target,
                                      manifest=manifest, selected_pace=selected_pace)
    actual_ids = [trial["source_trial_id"] for trial in result["marginal"]["trials"]]
    if (actual_ids != [draws[index]["trial_id"] for index in source_indices]
            or any(result["pace_forecast"][pace]["trial_count"] != count for pace, count in counts.items())):
        raise ValueError("final integration does not reproduce the sampled prefix and pace counts")
    if confirmed is None:
        main_pace, reason = None, "MAX_ITERATIONS_WITHOUT_CONFIRMED_ADVANTAGE"
    elif selected_pace is not None and selected_pace != confirmed:
        main_pace, reason = None, "REQUESTED_PACE_DIFFERS_FROM_CONFIRMED_PACE"
    else:
        main_pace, reason = confirmed, "CONFIRMED_EMPIRICAL_PACE_ADVANTAGE"
    result["mainline"] = {"status": "CONFIRMED" if main_pace else "UNCONFIRMED",
                          "fixing_status": "CONFIRMED" if main_pace else "UNCONFIRMED",
                          "statistical_confirmation": main_pace is not None,
                          "pace": main_pace, "requested_pace": selected_pace,
                          "selection_method": "sequential_pace_advantage_only",
                          "reason": reason,
                          "summary": result["conditional"][main_pace]["summary"] if main_pace else None}
    result["adaptive"] = {"looks": looks, "look_count": len(looks), "iterations": target,
                          "initial_iterations": initial_iterations, "batch_iterations": batch_iterations,
                          "max_iterations": max_iterations, "additional_iterations": target - initial_iterations,
                          "alpha": alpha, "min_gap": min_gap, "confirmed_pace": confirmed,
                          "termination": "CONFIRMED" if confirmed else "MAX_ITERATIONS",
                          "prefix_reproduced": True}
    result["kind"] = "RESEARCH_ADAPTIVE_PACE_BOOTSTRAP_NOT_PRODUCTION_PREDICTION"
    result["limitations"].append("Statistical confirmation concerns Monte Carlo sampling uncertainty, not real-world forecast accuracy.")
    return result
