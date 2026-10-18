"""Opt-in, uncalibrated environment/pace sensitivity analysis.

The production baseline is unchanged. Weather enters through the forecast of
track condition and cushion, rather than an invented direct sunshine bonus.
All joint bet probabilities are mixed *after* conditional race calculations.
"""
from __future__ import annotations

from collections import defaultdict
import math
import re

from analysis.candidate_ev import build_candidate_evaluations
from analysis.ev import EVWeights, build_feature_rows, compute_ev, simulate_race_scenarios


def date_key(value: object) -> str:
    parts = re.findall(r"\d+", str(value))
    if len(parts) != 3:
        raise ValueError(f"Invalid history date: {value}")
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def cushion_course_adjustment(
    histories: list[dict], measurements: list[dict], target_cushion: float,
    *, bandwidth: float = 0.7, prior_starts: float = 5.0, strength: float = 0.20,
) -> dict:
    """Shrink a horse's cushion-conditioned finish-strength contrast to zero.

    Bandwidth, prior strength and effect size are explicit research assumptions,
    not fitted coefficients. Unknown measurements never become invented values.
    """
    if not all(math.isfinite(x) for x in (target_cushion, bandwidth, prior_starts, strength)):
        raise ValueError("Non-finite cushion parameter")
    if target_cushion <= 0 or bandwidth <= 0 or prior_starts < 0 or strength < 0:
        raise ValueError("Invalid cushion parameter")
    lookup = {}
    for measurement in measurements:
        key = (measurement["track"], date_key(measurement["date"]))
        value = measurement.get("cushion")
        if value is None:
            continue
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError("Invalid observed cushion")
        if key in lookup and lookup[key] != float(value):
            raise ValueError("Conflicting observed cushion")
        lookup[key] = float(value)
    points = []
    for row in histories:
        if str(row.get("history_surface", "")) != "芝":
            continue
        key = (str(row["course"]), date_key(row["date"]))
        position = str(row.get("position", ""))
        if key not in lookup or not position.isdigit() or int(position) < 1:
            continue
        if date_key(row["date"]) >= date_key(row["target_race_date"]):
            raise ValueError("History is not before the target race")
        recency = (1.00, 0.85, 0.70, 0.55, 0.40)[min(max(int(row["run_index"]) - 1, 0), 4)]
        similarity = math.exp(-0.5 * ((lookup[key] - target_cushion) / bandwidth) ** 2)
        points.append((recency, similarity, 1.0 / int(position)))
    adjustment = 0.0
    effective_starts = 0.0
    if points:
        base_total = sum(w for w, _, _ in points)
        matched_total = sum(w * similarity for w, similarity, _ in points)
        if matched_total > 1e-12:
            base = sum(w * finish for w, _, finish in points) / base_total
            matched = sum(w * similarity * finish for w, similarity, finish in points) / matched_total
            # Kernel evidence decreases when the target is far from all observed starts.
            effective_starts = sum(similarity for _, similarity, _ in points)
            confidence = effective_starts / (effective_starts + prior_starts)
            adjustment = strength * confidence * (matched - base)
    return {"course_adjustment": adjustment, "observed_starts": len(points),
            "effective_starts": effective_starts}


def simulate_environment_mixture(
    histories: list[dict], environments: list[dict], measurements: list[dict],
    *, weights: EVWeights | None = None, cushion_parameters: dict | None = None,
) -> dict:
    """Return the weighted environment × pace probability components and marginal."""
    if not histories or not environments:
        raise ValueError("History and forecast scenarios are required")
    probabilities = [float(e["probability"]) for e in environments]
    if any(not math.isfinite(p) or p < 0 for p in probabilities) or not math.isclose(sum(probabilities), 1, abs_tol=1e-9):
        raise ValueError("Environment probabilities must sum to one")
    if len({e['id'] for e in environments}) != len(environments):
        raise ValueError("Duplicate environment id")
    if len({row['race_id'] for row in histories}) != 1:
        raise ValueError("One target race is required")
    grouped = defaultdict(list)
    for row in histories:
        grouped[str(row["horse_id"])].append(row)
    components = []
    pace_weights = defaultdict(float)
    for environment in environments:
        current = [dict(row, target_weather=environment["weather"],
                        target_track_condition=environment["track_condition"]) for row in histories]
        features = build_feature_rows(current)
        for feature in features:
            audit = cushion_course_adjustment(
                grouped[str(feature["horse_id"])], measurements, float(environment["cushion"]),
                **(cushion_parameters or {}),
            )
            feature.update(target_cushion=environment["cushion"], cushion_audit=audit)
            feature["course_score"] = float(feature["course_score"]) + audit["course_adjustment"]
        simulated = simulate_race_scenarios(features)
        for pace in ("high", "mid", "slow"):
            pace_probability = float(simulated[0][f"pace_mix_{pace}"])
            joint_weight = float(environment["probability"]) * pace_probability
            conditioned = [dict(row, pace_score=row[f"pace_{pace}"]) for row in simulated]
            calculated = compute_ev(conditioned, weights=weights)
            # Saved probabilities are rounded; normalize each component before mixing.
            total = sum(float(row["win_prob"]) for row in calculated)
            for row in calculated:
                row["win_prob"] = float(row["win_prob"]) / total
            components.append({"id": f"{environment['id']}:{pace}", "environment_id": environment["id"],
                               "pace": pace, "weight": joint_weight, "ev_rows": calculated})
            pace_weights[pace] += joint_weight
    aggregate = {}
    for component in components:
        for row in component["ev_rows"]:
            key = str(row["horse_number"])
            if key not in aggregate:
                aggregate[key] = {"race_id": row["race_id"], "horse_id": row["horse_id"],
                                  "horse_number": row["horse_number"], "horse_name": row["horse_name"],
                                  "frame_number": row["frame_number"], "win_prob": 0.0,
                                  "by_pace": {"high": 0.0, "mid": 0.0, "slow": 0.0}}
            mass = component["weight"] * float(row["win_prob"])
            aggregate[key]["win_prob"] += mass
            aggregate[key]["by_pace"][component["pace"]] += mass
    for row in aggregate.values():
        row["by_pace"] = {pace: mass / pace_weights[pace] for pace, mass in row["by_pace"].items()}
    return {"status": "research_only_uncalibrated", "environments": environments,
            "pace_weights": dict(pace_weights), "components": components,
            "ev_rows": sorted(aggregate.values(), key=lambda row: -row["win_prob"])}


def mix_candidate_evaluations(components: list[dict], odds_rows: list[dict]) -> dict:
    """Keep scenario correlation: sum_s w_s P(ticket | s), not P(ticket | mean p)."""
    if not components or not math.isclose(sum(float(c["weight"]) for c in components), 1, abs_tol=2e-6):
        raise ValueError("Component weights must sum to one")
    mixed = {}
    for component in components:
        result = build_candidate_evaluations(component["ev_rows"], odds_rows)
        if result["validation"]["status"] != "OK":
            raise ValueError(str(result["validation"]["errors"]))
        for candidate in result["candidate_evaluations"]:
            key = candidate["key"]
            if key not in mixed:
                mixed[key] = dict(candidate, hit_prob=0.0, ev=0.0, by_pace={})
            mass = float(component["weight"]) * candidate["hit_prob"]
            mixed[key]["hit_prob"] += mass
            pace = component["pace"]
            mixed[key]["by_pace"][pace] = mixed[key]["by_pace"].get(pace, 0.0) + mass
    for candidate in mixed.values():
        candidate["ev"] = candidate["hit_prob"] * candidate["official_odds"]
    return {"candidate_evaluations": list(mixed.values()),
            "validation": {"status": "OK", "probability_method": "environment_pace_mixture",
                           "component_count": len(components), "errors": []}}
