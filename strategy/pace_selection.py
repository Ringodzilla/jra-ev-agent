"""Research-only candidate filtering under one explicitly selected pace."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, DecimalException
import math


PACES = ("high", "mid", "slow")
BET_SIZES = {"win": {1}, "place": {1}, "wide": {2}, "umaren": {2},
             "umatan": {2}, "sanrenpuku": {3}, "sanrentan": {3}, "wakuren": {2}}


def _finite(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric observation")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid numeric observation") from exc
    if not math.isfinite(result):
        raise ValueError("nonfinite numeric observation")
    return result


def select_pace_candidates(
    candidates: list[dict], selected_pace: str | None, min_hit_probability: float, min_ev: float,
) -> dict:
    """Compare a caller-specified pace hypothesis, without statistical confirmation.

    This lower-level API cannot establish a mainline pace. Use
    select_confirmed_pace_candidates for evidence-checked adaptive selection.
    No stakes or formal GO are produced.

    Every candidate must carry all three conditional pace observations, or mark
    a pace as {status: 'NOT_EVALUATED', reason: '...'} without numeric fields.
    EV consistency tolerates ONLY independent rounding to six decimal places:
    eps*(1+abs(odds)+abs(probability))+eps**2, eps=0.5e-6.
    Missing a selected pace observation yields NOT_EVALUATED, never probability 0.
    All candidates are validated even when no pace has been selected.
    """
    minimum_probability, minimum_ev = _finite(min_hit_probability), _finite(min_ev)
    if not 0 <= minimum_probability <= 1 or minimum_ev < 0:
        raise ValueError("invalid explicit thresholds")
    if selected_pace is not None and selected_pace not in PACES:
        raise ValueError("unknown selected pace")
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    keys = set()
    evaluations = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        key, kind = candidate.get("candidate_key"), candidate.get("bet_type")
        horses = candidate.get("frame_numbers", candidate.get("horse_numbers")) if kind == "wakuren" else candidate.get("horse_numbers")
        if not isinstance(key, str) or not key.strip() or key in keys:
            raise ValueError("missing or duplicate candidate key")
        keys.add(key)
        if not isinstance(kind, str) or kind not in BET_SIZES or not isinstance(horses, list) or len(horses) not in BET_SIZES[kind]:
            raise ValueError("invalid bet type or runner count")
        if any(isinstance(n, bool) or not str(n).isdigit() or int(n) <= 0 for n in horses) or (kind != "wakuren" and len({int(n) for n in horses}) != len(horses)):
            raise ValueError("invalid or duplicate horse numbers")
        metrics = candidate.get("pace_metrics")
        if not isinstance(metrics, dict) or set(metrics) != set(PACES):
            raise ValueError("all three pace states must be supplied explicitly")
        for pace in PACES:
            observation = metrics[pace]
            if not isinstance(observation, dict):
                raise ValueError("pace observation must be an object")
            if observation.get("status") == "NOT_EVALUATED":
                if not isinstance(observation.get("reason"), str) or not observation["reason"].strip() or any(
                        name in observation for name in ("hit_probability", "odds", "ev")):
                    raise ValueError("unmeasured pace needs reason and no invented numeric values")
            else:
                if observation.get("status", "EVALUATED") != "EVALUATED":
                    raise ValueError("invalid pace observation status")
                probability, odds, ev = (_finite(observation.get(field)) for field in ("hit_probability", "odds", "ev"))
                if not 0 <= probability <= 1 or odds < 1 or ev < 0:
                    raise ValueError("invalid conditional probability, odds or EV")
                try:
                    p, o, e = (Decimal(str(observation[field])) for field in ("hit_probability", "odds", "ev"))
                    epsilon = Decimal("0.0000005")
                    bound = epsilon * (1 + abs(o) + abs(p)) + epsilon * epsilon
                    if abs(e - p * o) > bound:
                        raise ValueError("conditional EV does not match probability times odds")
                except DecimalException as exc:
                    raise ValueError("invalid decimal observation") from exc
        selected = metrics.get(selected_pace) if selected_pace is not None else None
        if selected is None:
            status, reason, eligible = "NOT_EVALUATED", "PACE_NOT_SELECTED", None
        elif selected.get("status") == "NOT_EVALUATED":
            status, reason, eligible = "NOT_EVALUATED", selected["reason"], None
        else:
            eligible = (_finite(selected["hit_probability"]) >= minimum_probability
                        and _finite(selected["ev"]) >= minimum_ev)
            status, reason = "EVALUATED", "MEETS_EXPLICIT_THRESHOLDS" if eligible else "BELOW_EXPLICIT_THRESHOLDS"
        evaluations.append(dict(candidate_key=key, bet_type=kind,
            number_kind="frame" if kind == "wakuren" else "horse",
            horse_numbers=[] if kind == "wakuren" else deepcopy(horses),
            frame_numbers=deepcopy(horses) if kind == "wakuren" else [],
            source_snapshot_id=deepcopy(candidate.get("source_snapshot_id")),
            source_captured_at=deepcopy(candidate.get("source_captured_at")),
            status=status, reason=reason, eligible=eligible, selected_pace=selected_pace,
            selected_metrics=deepcopy(selected), pace_sensitivity=deepcopy(metrics)))
    return dict(schema_version=1, scope="research_conditional_pace_selection", selected_pace=selected_pace,
                status="PACE_NOT_SELECTED" if selected_pace is None else "EVALUATED",
                thresholds={"min_hit_probability": minimum_probability, "min_ev": minimum_ev},
                ev_rounding_contract="independent_6dp_error_bound_epsilon_0.0000005",
                eligible_candidates=[deepcopy(row) for row in evaluations if row["eligible"] is True],
                evaluations=evaluations, statistical_confirmation=False, hypothesis_only=True,
                formal_tickets=[], formal_go_issued=False)


def select_confirmed_pace_candidates(
    candidates: list[dict], pace_result: dict, min_hit_probability: float, min_ev: float,
) -> dict:
    """Recheck adaptive statistical evidence before filtering a mainline pace.

    This verifies internal consistency, not authenticity of input JSON, hashes,
    source draws or their iid/ensemble provenance. It issues no production GO.
    """
    from analysis.pace_advantage import compare_pace_counts
    if not isinstance(pace_result, dict):
        raise ValueError("pace_result must be an object")
    mainline = pace_result.get("mainline") or {}
    if not isinstance(mainline, dict) or type(mainline.get("statistical_confirmation", False)) is not bool:
        raise ValueError("invalid statistical confirmation flag")
    flagged = mainline.get("statistical_confirmation", False)
    adaptive = pace_result.get("adaptive")
    confirmed = None
    if adaptive is not None:
        try:
            if not isinstance(adaptive, dict):
                raise ValueError("adaptive evidence must be an object")
            initial, batch, maximum = (adaptive[k] for k in ("initial_iterations", "batch_iterations", "max_iterations"))
            if any(type(value) is not int or value <= 0 for value in (initial, batch, maximum)) or initial > maximum:
                raise ValueError("invalid adaptive schedule")
            looks = adaptive["looks"]
            if not isinstance(looks, list) or not looks or type(adaptive["look_count"]) is not int or adaptive["look_count"] != len(looks):
                raise ValueError("invalid adaptive look count")
            previous = {pace: 0 for pace in PACES}
            target = initial
            comparison = None
            for index, look in enumerate(looks, 1):
                if comparison is not None and (comparison["confirmed_pace"] is not None or target == maximum):
                    raise ValueError("sampling continued after termination")
                if index > 1:
                    target = min(target + batch, maximum)
                if type(look["iterations"]) is not int or look["iterations"] != target:
                    raise ValueError("look does not match predeclared sample schedule")
                comparison = compare_pace_counts(look["counts"], index, adaptive["alpha"], adaptive["min_gap"])
                if comparison["draw_count"] != target or any(look["counts"][pace] < previous[pace] for pace in PACES):
                    raise ValueError("counts are not cumulative or do not match iterations")
                if look["comparison"] != comparison:
                    raise ValueError("saved statistical comparison differs from recomputation")
                previous = dict(look["counts"])
            confirmed = comparison["confirmed_pace"]
            expected_termination = "CONFIRMED" if confirmed is not None else "MAX_ITERATIONS"
            if (adaptive["iterations"] != target or type(adaptive["iterations"]) is not int
                    or adaptive["additional_iterations"] != target - initial
                    or type(adaptive["additional_iterations"]) is not int
                    or adaptive["confirmed_pace"] != confirmed or adaptive["termination"] != expected_termination
                    or adaptive["prefix_reproduced"] is not True or (confirmed is None and target != maximum)):
                raise ValueError("invalid final adaptive state")
            forecast = pace_result["pace_forecast"]
            if set(forecast) != set(PACES) or any(type(forecast[pace]["trial_count"]) is not int
                    or forecast[pace]["trial_count"] != previous[pace]
                    or abs(_finite(forecast[pace]["frequency"]) - previous[pace] / target) > 1e-12 for pace in PACES):
                raise ValueError("final forecast does not match cumulative counts")
            requested = mainline.get("requested_pace")
            if requested is not None and requested not in PACES:
                raise ValueError("invalid requested pace")
            selected = confirmed if confirmed is not None and requested in (None, confirmed) else None
            if (flagged != (selected is not None) or mainline.get("pace") != selected
                    or mainline.get("status") != ("CONFIRMED" if selected else "UNCONFIRMED")
                    or mainline.get("fixing_status") != ("CONFIRMED" if selected else "UNCONFIRMED")
                    or mainline.get("selection_method") != "sequential_pace_advantage_only"):
                raise ValueError("mainline flags do not match statistical evidence")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("incomplete or malformed adaptive evidence") from exc
    elif flagged:
        raise ValueError("statistical confirmation requires adaptive evidence")
    result = select_pace_candidates(candidates, confirmed if flagged else None, min_hit_probability, min_ev)
    result.update(statistical_confirmation=flagged, hypothesis_only=not flagged,
                  confirmation_validation="internal_consistency_only_not_input_authentication",
                  conditional_candidates=deepcopy(candidates),
                  confirmation_reason="CONFIRMED_ADAPTIVE_EVIDENCE" if flagged else "NO_CONFIRMED_MATCHING_PACE")
    return result
