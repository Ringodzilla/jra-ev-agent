"""Research summaries conditional on explicit caller-supplied pace labels."""
from __future__ import annotations

import math

from analysis.joint_monte_carlo import _summary, simulate_joint_trials

PACES = ("high", "mid", "slow")


def _conditional(horse_ids: list[str], trials: list[dict]) -> dict:
    horses = [{"horse_id": horse, "win_probability": _summary([t["win_probabilities"][i] for t in trials]),
               "odds": _summary([t["odds"][i] for t in trials]),
               "ev": _summary([t["ev"][i] for t in trials])} for i, horse in enumerate(horse_ids)]
    return {"trial_count": len(trials), "horses": horses,
            "win_probability_rank": [h["horse_id"] for h in sorted(horses, key=lambda h: -h["win_probability"]["mean"])],
            "ev_rank": [h["horse_id"] for h in sorted(horses, key=lambda h: -h["ev"]["mean"])]}


def simulate_pace_conditioned(*, horse_ids: list[str], current_odds: list[float],
                              draws: list[dict], seed: int, iterations: int,
                              manifest: dict, selected_pace: str | None = None) -> dict:
    """Summarize one joint bootstrap, never optimize pace using EV or outcomes.

    Explicit selection requests a conditional hypothesis from the same sampled
    race trials. This raw API never confirms or fixes a mainline pace.
    """
    if selected_pace is not None and selected_pace not in PACES:
        raise ValueError("selected_pace must be high, mid, slow or None")
    if not isinstance(draws, list) or not draws:
        raise ValueError("explicit pace-labelled source draws required")
    if any(not isinstance(d, dict) or d.get("pace") not in PACES for d in draws):
        raise ValueError("every source draw requires an explicit high/mid/slow pace")
    marginal = simulate_joint_trials(horse_ids=horse_ids, current_odds=current_odds,
                                     draws=draws, seed=seed, iterations=iterations, manifest=manifest)
    source_pace = {d["trial_id"]: d["pace"] for d in draws}
    groups = {pace: [] for pace in PACES}
    for trial in marginal["trials"]:
        pace = source_pace[trial["source_trial_id"]]
        trial["pace"] = pace
        groups[pace].append(trial)
    forecast = {pace: {"trial_count": len(groups[pace]), "frequency": len(groups[pace]) / iterations}
                for pace in PACES}
    conditional = {pace: {"summary": _conditional(horse_ids, groups[pace]) if groups[pace] else None,
                          "reason": None if groups[pace] else "NO_SAMPLED_TRIALS_FOR_PACE"} for pace in PACES}
    maximum = max(len(group) for group in groups.values())
    leaders = [pace for pace in PACES if len(groups[pace]) == maximum]
    main_pace = selected_pace if selected_pace is not None else leaders[0] if len(leaders) == 1 else None
    status = "AMBIGUOUS" if main_pace is None else "UNCONFIRMED" if groups[main_pace] else "UNAVAILABLE"
    mixtures = [math.fsum(forecast[pace]["frequency"] * conditional[pace]["summary"]["horses"][i]["win_probability"]["mean"]
                         for pace in PACES if groups[pace]) for i in range(len(horse_ids))]
    consistent = (abs(math.fsum(f["frequency"] for f in forecast.values()) - 1) <= 1e-12
                  and abs(math.fsum(mixtures) - 1) <= 1e-12
                  and all(abs(p - h["win_probability"]["mean"]) <= 1e-12
                          for p, h in zip(mixtures, marginal["horses"])))
    if not consistent:
        raise ValueError("pace mixture does not reproduce normalized marginal probabilities")
    return {"kind": "RESEARCH_PACE_CONDITIONAL_SUMMARY_NOT_PRODUCTION_PREDICTION",
            "marginal": marginal, "pace_forecast": forecast, "conditional": conditional,
            "mainline": {"status": status, "pace": None,
                         "suggested_pace": leaders[0] if len(leaders) == 1 else None,
                         "requested_pace": selected_pace,
                         "fixing_status": "UNCONFIRMED", "statistical_confirmation": False,
                         "selection_method": "caller_requested_hypothesis" if selected_pace is not None else "most_frequent_sampled_hypothesis",
                         "summary": None,
                         "hypothesis_pace": main_pace,
                         "hypothesis_summary": conditional[main_pace]["summary"] if main_pace else None},
            "mixture_validation": {"status": "PASS", "weighted_probabilities": mixtures},
            "limitations": ["Pace labels and source draw calibration are caller responsibilities.",
                            "Zero sampled pace frequency does not establish physical impossibility.",
                            "Raw frequency and requested hypotheses never confirm a mainline; statistical comparison is required."]}
