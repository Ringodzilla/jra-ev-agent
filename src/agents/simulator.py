from __future__ import annotations

from copy import deepcopy

from analysis.ev import simulate_race_scenarios


class SimulatorAgent:
    def run(self, feature_rows: list[dict[str, object]]) -> dict[str, object]:
        lineage_by_row = _capture_environment_lineage(feature_rows)
        scenario_rows = simulate_race_scenarios(feature_rows)
        _restore_environment_lineage(scenario_rows, lineage_by_row)
        return {"scenario_rows": scenario_rows}


def _capture_environment_lineage(
    feature_rows: list[dict[str, object]],
) -> dict[tuple[str, str, str], dict[str, object]]:
    lineage_by_row: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, row in enumerate(feature_rows):
        lineage = {
            key: deepcopy(value)
            for key, value in row.items()
            if _is_environment_lineage_key(key)
        }
        if not lineage:
            continue
        _validate_environment_policy(lineage)
        identity = _row_identity(row, index)
        if identity in lineage_by_row:
            raise ValueError(f"duplicate analyzer row identity for environment lineage: {identity}")
        lineage_by_row[identity] = lineage
    return lineage_by_row


def _restore_environment_lineage(
    scenario_rows: list[dict[str, object]],
    lineage_by_row: dict[tuple[str, str, str], dict[str, object]],
) -> None:
    if not lineage_by_row:
        return

    propagated: set[tuple[str, str, str]] = set()
    for index, row in enumerate(scenario_rows):
        identity = _row_identity(row, index)
        lineage = lineage_by_row.get(identity)
        if lineage is None:
            continue
        for key in list(row):
            if _is_environment_lineage_key(key):
                del row[key]
        row.update(deepcopy(lineage))
        propagated.add(identity)

    missing = set(lineage_by_row) - propagated
    if missing:
        raise ValueError(
            "simulator output lost analyzer environment lineage rows: "
            + ", ".join(str(identity) for identity in sorted(missing))
        )


def _is_environment_lineage_key(key: str) -> bool:
    return key.startswith("environment_") or key.startswith("target_environment_")


def _row_identity(row: dict[str, object], index: int) -> tuple[str, str, str]:
    race_id = str(row.get("race_id", "")).strip()
    for field in ("horse_id", "horse_number", "horse_name"):
        value = str(row.get(field, "")).strip()
        if value:
            return race_id, field, value
    return race_id, "row_index", str(index)


def _validate_environment_policy(lineage: dict[str, object]) -> None:
    model_status = str(lineage.get("environment_model_status", "")).strip().lower()
    if not model_status or model_status == "promoted":
        return
    score = _to_float(lineage.get("environment_score"))
    applied = lineage.get("environment_adjustment_applied") is True
    if applied or score != 0.0:
        raise ValueError("unpromoted environment model cannot be applied by simulator")


def _to_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
