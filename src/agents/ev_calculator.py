from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math

from analysis.candidate_ev import build_candidate_evaluations
from analysis.ev import EVWeights, compute_ev


ENVIRONMENT_PROBABILITY_FORMULA = "p_final=softmax(log(p_base)+environment_score)"
ENVIRONMENT_PROBABILITY_TOLERANCE = 3e-6


class EVCalculatorAgent:
    def __init__(self, weights: EVWeights | None = None) -> None:
        self.weights = weights or EVWeights()

    def run(
        self,
        scenario_rows: list[dict[str, object]],
        *,
        combo_odds: list[dict[str, object]] | list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        if not _has_environment_lineage(scenario_rows):
            ev_rows = compute_ev(scenario_rows, weights=self.weights)
            output: dict[str, object] = {"ev_rows": ev_rows}
            if combo_odds is not None:
                output.update(build_candidate_evaluations(ev_rows, combo_odds))
            return output

        _validate_environment_policy(scenario_rows)
        baseline_rows = _neutralize_environment_adjustment(scenario_rows)
        baseline_ev_rows = compute_ev(baseline_rows, weights=self.weights)
        ev_rows = compute_ev(scenario_rows, weights=self.weights)
        validation = _attach_and_validate_environment_probabilities(
            scenario_rows,
            baseline_ev_rows,
            ev_rows,
        )
        output = {
            "ev_rows": ev_rows,
            "environment_probability_validation": validation,
        }
        if combo_odds is not None:
            output.update(build_candidate_evaluations(ev_rows, combo_odds))
        return output


def _has_environment_lineage(rows: list[dict[str, object]]) -> bool:
    return any(
        key.startswith("environment_") or key.startswith("target_environment_")
        for row in rows
        for key in row
    )


def _validate_environment_policy(rows: list[dict[str, object]]) -> None:
    by_race: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_race[str(row.get("race_id", "")).strip()].append(row)

    for race_id, race_rows in by_race.items():
        applied_values: list[bool] = []
        for row in race_rows:
            score = _strict_float(row.get("environment_score", 0.0), field="environment_score")
            applied = row.get("environment_adjustment_applied") is True
            model_status = str(row.get("environment_model_status", "")).strip().lower()
            if model_status != "promoted" and (score != 0.0 or applied):
                raise ValueError(
                    "unpromoted environment model cannot alter EV probability: "
                    f"{_row_label(row)}"
                )
            if not applied and score != 0.0:
                raise ValueError(
                    "environment_score must be zero when adjustment is not applied: "
                    f"{_row_label(row)}"
                )
            applied_values.append(applied)
        if any(applied_values) and not all(applied_values):
            raise ValueError(
                "environment adjustment must cover the complete race field: "
                f"{race_id}"
            )


def _neutralize_environment_adjustment(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    neutral_rows = deepcopy(rows)
    for row in neutral_rows:
        row["environment_score"] = 0.0
        row["environment_adjustment_applied"] = False
    return neutral_rows


def _attach_and_validate_environment_probabilities(
    scenario_rows: list[dict[str, object]],
    baseline_ev_rows: list[dict[str, object]],
    final_ev_rows: list[dict[str, object]],
) -> dict[str, object]:
    scenario_by_identity = _unique_rows_by_identity(scenario_rows, source="simulator")
    baseline_by_identity = _unique_rows_by_identity(baseline_ev_rows, source="baseline EV")
    final_by_identity = _unique_rows_by_identity(final_ev_rows, source="final EV")
    identities = set(scenario_by_identity)
    if identities != set(baseline_by_identity) or identities != set(final_by_identity):
        raise ValueError("EV probability rows do not match simulator row identities")

    expected_by_identity: dict[tuple[str, str, str], float] = {}
    by_race: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for identity in identities:
        by_race[identity[0]].append(identity)
    for race_identities in by_race.values():
        ordered = sorted(race_identities)
        bases = [
            _strict_probability(baseline_by_identity[identity].get("win_prob"), field="p_base")
            for identity in ordered
        ]
        scores = [
            _strict_float(
                scenario_by_identity[identity].get("environment_score", 0.0),
                field="environment_score",
            )
            for identity in ordered
        ]
        applied = [
            scenario_by_identity[identity].get("environment_adjustment_applied") is True
            for identity in ordered
        ]
        expected = _environment_adjusted_probabilities(bases, scores) if all(applied) else bases
        expected_by_identity.update(zip(ordered, expected))

    check_rows: list[dict[str, object]] = []
    for identity in sorted(identities):
        scenario_row = scenario_by_identity[identity]
        baseline_row = baseline_by_identity[identity]
        final_row = final_by_identity[identity]
        _validate_lineage_preserved(scenario_row, final_row)
        p_base = _strict_probability(baseline_row.get("win_prob"), field="p_base")
        p_final = _strict_probability(final_row.get("win_prob"), field="p_final")
        score = _strict_float(scenario_row.get("environment_score", 0.0), field="environment_score")
        applied = scenario_row.get("environment_adjustment_applied") is True
        expected = expected_by_identity[identity]
        if score == 0.0 and not applied:
            if p_final != p_base:
                raise ValueError(
                    "neutral environment probability invariant failed: "
                    f"{_row_label(scenario_row)} p_base={p_base} p_final={p_final}"
                )
        elif abs(p_final - expected) > ENVIRONMENT_PROBABILITY_TOLERANCE:
            raise ValueError(
                "environment probability formula validation failed: "
                f"{_row_label(scenario_row)} expected={expected} p_final={p_final}"
            )

        final_row["p_base"] = _format_probability(p_base)
        final_row["p_final"] = _format_probability(p_final)
        final_row["environment_probability_formula"] = ENVIRONMENT_PROBABILITY_FORMULA
        check_rows.append(
            {
                "race_id": identity[0],
                "row_key": f"{identity[1]}:{identity[2]}",
                "p_base": p_base,
                "environment_score": score,
                "p_final": p_final,
                "expected_p_final": expected,
                "environment_adjustment_applied": applied,
                "absolute_difference": abs(p_final - expected),
                "valid": True,
            }
        )

    return {
        "status": "OK",
        "formula": ENVIRONMENT_PROBABILITY_FORMULA,
        "tolerance": ENVIRONMENT_PROBABILITY_TOLERANCE,
        "row_count": len(check_rows),
        "checks": check_rows,
    }


def _environment_adjusted_probabilities(
    baseline_probabilities: list[float],
    environment_scores: list[float],
) -> list[float]:
    logits = [
        math.log(max(probability, 1e-300)) + score
        for probability, score in zip(baseline_probabilities, environment_scores)
    ]
    maximum = max(logits)
    exponentials = [math.exp(logit - maximum) for logit in logits]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _unique_rows_by_identity(
    rows: list[dict[str, object]],
    *,
    source: str,
) -> dict[tuple[str, str, str], dict[str, object]]:
    indexed: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, row in enumerate(rows):
        identity = _row_identity(row, index)
        if identity in indexed:
            raise ValueError(f"duplicate {source} row identity: {identity}")
        indexed[identity] = row
    return indexed


def _row_identity(row: dict[str, object], index: int) -> tuple[str, str, str]:
    race_id = str(row.get("race_id", "")).strip()
    for field in ("horse_id", "horse_number", "horse_name"):
        value = str(row.get(field, "")).strip()
        if value:
            return race_id, field, value
    return race_id, "row_index", str(index)


def _validate_lineage_preserved(
    scenario_row: dict[str, object],
    final_row: dict[str, object],
) -> None:
    expected = {
        key: value
        for key, value in scenario_row.items()
        if key.startswith("environment_") or key.startswith("target_environment_")
    }
    actual = {key: final_row.get(key) for key in expected}
    if actual != expected:
        raise ValueError(f"EV output lost simulator environment lineage: {_row_label(scenario_row)}")


def _strict_probability(value: object, *, field: str) -> float:
    probability = _strict_float(value, field=field)
    if probability <= 0.0 or probability > 1.0:
        raise ValueError(f"{field} must be in (0, 1]")
    return probability


def _strict_float(value: object, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _format_probability(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _row_label(row: dict[str, object]) -> str:
    return "|".join(
        str(row.get(field, "")).strip()
        for field in ("race_id", "horse_number", "horse_name")
    )
