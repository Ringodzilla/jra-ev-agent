"""Learn race-conditional environmental effects without changing the baseline.

Race-wide covariates alone cancel out of a softmax. Interactions with each
horse's pre-race features let weather, cushion and moisture change relative
probabilities. Coefficients and all hyperparameters use chronological blocks.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp


HORSE_FEATURES = ("front_rate", "closing_strength", "speed_score", "track_condition_score")
ENV_FEATURES = ("rain", "cloud", "snow", "cushion", "moisture_goal", "moisture_corner4", "condition")
CONDITIONS = {"良": 0.0, "稍重": 1 / 3, "重": 2 / 3, "不良": 1.0}


def environment_vector(environment: dict, family: str) -> tuple[list[float], list[str]]:
    if family not in {"linear", "quadratic"}:
        raise ValueError("Invalid feature family")
    weather = environment["weather"]
    if weather not in {"晴", "曇", "雨", "小雨", "雪", "小雪"}:
        raise ValueError("Observed weather is required")
    turf_condition = CONDITIONS[environment["track_condition"]]
    cushion = float(environment["cushion"])
    goal = float(environment["turf_moisture_goal"])
    corner4 = float(environment["turf_moisture_corner4"])
    if not (math.isfinite(cushion) and 0 < cushion <= 20 and 0 <= goal <= 100 and 0 <= corner4 <= 100):
        raise ValueError("Invalid measured environment")
    values = [float(weather in {"雨", "小雨"}), float(weather == "曇"),
              float(weather in {"雪", "小雪"}), cushion - 10, (goal - 8) / 4,
              (corner4 - 8) / 4, turf_condition]
    names = list(ENV_FEATURES)
    if family == "quadratic":
        values += [values[3] ** 2, values[4] ** 2, values[5] ** 2]
        names += ["cushion_squared", "moisture_goal_squared", "moisture_corner4_squared"]
    return values, names


def design_matrix(rows: list[dict], environment: dict, family: str = "linear") -> tuple[np.ndarray, list[str]]:
    if len(rows) < 2:
        raise ValueError("Invalid field size")
    values, names = environment_vector(environment, family)
    horse = np.array([[float(row[key]) for key in HORSE_FEATURES] for row in rows])
    if not np.isfinite(horse).all():
        raise ValueError("Non-finite horse feature")
    horse -= horse.mean(axis=0)
    matrix = (horse[:, :, None] * np.array(values)[None, None, :]).reshape(len(rows), -1)
    return matrix, [f"{h}*{e}" for h in HORSE_FEATURES for e in names]


def probabilities(race: dict, model: dict | None = None) -> np.ndarray:
    base = np.array([float(row["win_prob"]) for row in race["rows"]])
    if not np.isfinite(base).all() or (base <= 0).any() or not np.isclose(base.sum(), 1, atol=2e-6, rtol=0):
        raise ValueError("Baseline probabilities must be positive and sum to one")
    score = np.log(base)
    if model is not None:
        matrix, names = design_matrix(race["rows"], race["environment"], model["family"])
        if names != model["feature_names"]:
            raise ValueError("Model feature schema mismatch")
        score += (matrix / np.array(model["scales"])) @ np.array(model["coefficients"])
    return np.exp(score - logsumexp(score))


def chronological_blocks(races: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    dates = sorted({race["date"] for race in races})
    if len(dates) < 3 or len({race["race_id"] for race in races}) != len(races):
        raise ValueError("Three distinct dates and unique race IDs are required")
    first = max(1, min(len(dates) - 2, int(len(dates) * 0.6)))
    second = max(first + 1, min(len(dates) - 1, int(len(dates) * 0.8)))
    train = [race for race in races if race["date"] < dates[first]]
    validation = [race for race in races if dates[first] <= race["date"] < dates[second]]
    test = [race for race in races if race["date"] >= dates[second]]
    return train, validation, test


def _targets(race: dict) -> np.ndarray:
    winners = {int(number) for number in race["winners"]}
    numbers = [int(row["horse_number"]) for row in race["rows"]]
    if not winners or not winners.issubset(numbers) or len(numbers) != len(set(numbers)):
        raise ValueError("Winner labels or field universe are invalid")
    return np.array([1 / len(winners) if number in winners else 0 for number in numbers])


def fit_model(races: list[dict], family: str, ridge: float) -> dict:
    if not races or not math.isfinite(ridge) or ridge <= 0:
        raise ValueError("Training races and positive ridge are required")
    matrices = [design_matrix(race["rows"], race["environment"], family)[0] for race in races]
    names = design_matrix(races[0]["rows"], races[0]["environment"], family)[1]
    environment_values = np.array([environment_vector(race['environment'], family)[0] for race in races])
    environment_names = environment_vector(races[0]['environment'], family)[1]
    varying = np.ptp(environment_values, axis=0) > 1e-10
    # A constant weather category cannot identify a weather effect separately
    # from recalibration of the horse feature. Freeze those coefficients at zero.
    active = np.tile(varying, len(HORSE_FEATURES))
    matrices = [matrix * active for matrix in matrices]
    scales = np.sqrt(np.mean(np.vstack(matrices) ** 2, axis=0))
    scales[scales < 1e-10] = 1.0
    matrices = [matrix / scales for matrix in matrices]
    offsets = [np.log(probabilities(race)) for race in races]
    targets = [_targets(race) for race in races]

    def objective(beta):
        value = ridge * float(beta @ beta) / 2
        gradient = ridge * beta
        for matrix, offset, target in zip(matrices, offsets, targets):
            score = offset + matrix @ beta
            normalizer = logsumexp(score)
            probability = np.exp(score - normalizer)
            value += float(normalizer - target @ score) / len(races)
            gradient = gradient + matrix.T @ (probability - target) / len(races)
        return value, gradient

    optimized = minimize(objective, np.zeros(len(names)), jac=True, method="L-BFGS-B",
                         options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8})
    if not optimized.success or not np.isfinite(optimized.x).all():
        raise ValueError("Environmental coefficient fit failed")
    return {"schema_version": 1, "family": family, "ridge": ridge,
            "feature_names": names, "scales": scales.tolist(),
            "coefficients": optimized.x.tolist(), "training_races": len(races),
            "training_end_date": max(race["date"] for race in races),
            "unsupported_environment_terms": [name for name, flag in zip(environment_names, varying) if not flag],
            "training_environment_ranges": {name: [float(min(values)), float(max(values))]
                                             for name, values in zip(environment_names, environment_values.T)},
            "status": "candidate_not_promoted"}


def evaluate_model(races: list[dict], model: dict | None = None) -> dict:
    if not races:
        raise ValueError("Evaluation races are required")
    losses, briers, hits, returns = [], [], [], []
    for race in races:
        p = probabilities(race, model)
        target = _targets(race)
        losses.append(-float(target @ np.log(np.maximum(p, 1e-300))))
        briers.append(float(((p - target) ** 2).sum()))
        # Ties are deterministic by horse number for both baseline and candidate.
        leader = min(range(len(p)), key=lambda i: (-p[i], int(race["rows"][i]["horse_number"])))
        number = int(race["rows"][leader]["horse_number"])
        hits.append(number in {int(n) for n in race["winners"]})
        returns.append(float(race["win_payouts"].get(str(number), 0)))
    return {"race_count": len(races), "log_loss": float(np.mean(losses)),
            "brier": float(np.mean(briers)), "top1_hit_rate": float(np.mean(hits)),
            "win_top1_policy_roi": sum(returns) / (100 * len(races))}


def train_and_compare(races: list[dict], *, min_train: int = 100, min_validation: int = 20,
                      min_test: int = 20) -> dict:
    train, validation, test = chronological_blocks(races)
    trials = []
    for family in ("linear", "quadratic"):
        for ridge in (0.1, 1.0, 10.0, 100.0):
            model = fit_model(train, family, ridge)
            trials.append({"model": model, "validation": evaluate_model(validation, model)})
    # Test labels are never consulted during candidate selection.
    selected = min(trials, key=lambda trial: trial["validation"]["log_loss"])
    baseline = {key: evaluate_model(block) for key, block in
                (("train", train), ("validation", validation), ("test", test))}
    candidate = {key: evaluate_model(block, selected["model"]) for key, block in
                 (("train", train), ("validation", validation), ("test", test))}
    enough = len(train) >= min_train and len(validation) >= min_validation and len(test) >= min_test
    essential = {'rain', 'cushion', 'moisture_goal', 'moisture_corner4', 'condition'}
    identifiable = not essential.intersection(selected['model']['unsupported_environment_terms'])
    improved = (candidate["validation"]["log_loss"] < baseline["validation"]["log_loss"]
                and candidate["test"]["log_loss"] < baseline["test"]["log_loss"]
                and candidate["test"]["brier"] <= baseline["test"]["brier"]
                and candidate["validation"]["win_top1_policy_roi"] >= baseline["validation"]["win_top1_policy_roi"]
                and candidate["test"]["win_top1_policy_roi"] >= baseline["test"]["win_top1_policy_roi"])
    status = ("INSUFFICIENT_DATA" if not enough else
              "INSUFFICIENT_ENVIRONMENT_COVERAGE" if not identifiable else
              "ELIGIBLE_FOR_REVIEW" if improved else "REJECTED")
    return {"status": status, "production_model_changed": False, "selected_model": selected["model"],
            "formula": "p_i=softmax(log(p_baseline_i)+sum_j beta_j * horse_environment_interaction_ij / train_scale_j)",
            "minimum_races": {"train": min_train, "validation": min_validation, "test": min_test},
            "blocks": {key: [r["race_id"] for r in block] for key, block in
                       (("train", train), ("validation", validation), ("test", test))},
            "baseline": baseline, "candidate": candidate,
            "training_weather_counts": {weather: sum(r['environment']['weather'] == weather for r in train)
                                        for weather in sorted({r['environment']['weather'] for r in races})},
            "trials": [{"family": t["model"]["family"], "ridge": t["model"]["ridge"],
                        "validation": t["validation"]} for t in trials],
            "evaluation_policy": "Separate research evaluation; frozen existing evaluator and splits are unchanged. "
                                 "ROI: one 100-yen win ticket on the top predicted horse in each held-out race."}
