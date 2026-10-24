"""Apply verified race-day environment observations to prediction features.

Collection and inference are deliberately separate.  Every observed value is
carried into the prediction artifact, while a non-zero score is produced only
by an explicitly promoted, versioned model.  Missing or unpromoted models never
invent coefficients.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTION_MODEL_PATH = ROOT / "data/models/environment_model.json"
HORSE_FEATURES = ("front_rate", "closing_strength", "speed_score", "track_condition_score")
CONDITIONS = {"良": 0.0, "稍重": 1 / 3, "重": 2 / 3, "不良": 1.0}
ENVIRONMENT_SCHEMA_VERSION = 2


def apply_environment_features(
    feature_rows: list[dict[str, object]],
    environment_snapshots: list[dict[str, object]] | None,
    *,
    production_model_path: Path | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = [dict(row) for row in feature_rows]
    snapshots = {
        str(snapshot.get("race_id", "")): dict(snapshot)
        for snapshot in list(environment_snapshots or [])
        if str(snapshot.get("race_id", ""))
    }
    model_path = production_model_path or DEFAULT_PRODUCTION_MODEL_PATH
    model_artifact, model_reason = _load_promoted_model(model_path)
    model_status = _environment_model_status(model_artifact, model_reason)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("race_id", ""))].append(row)

    race_audits = []
    for race_id, group in grouped.items():
        snapshot = snapshots.get(race_id)
        provenance = _observation_provenance(snapshot)
        audit = {
            "race_id": race_id,
            "environment_policy": "collect_all_apply_promoted_model",
            **provenance,
            "environment_model_path": str(model_path),
            "environment_model_sha256": (
                hashlib.sha256(model_path.read_bytes()).hexdigest()
                if model_artifact is not None else ""
            ),
            "environment_model_status": model_status,
            "environment_adjustment_applied": False,
            "environment_not_applied_reason": "",
            "environment_fallback_reason": "",
        }
        if snapshot is None:
            reason = "ENVIRONMENT_OBSERVATION_MISSING"
            _neutralize(group, None, reason, model_status=model_status)
            audit["status"] = "MISSING_OBSERVATION"
            audit["environment_not_applied_reason"] = _normalized_reason(reason)
            audit["environment_fallback_reason"] = reason
        elif not _snapshot_complete(snapshot, str(group[0].get("target_surface", ""))):
            reason = "ENVIRONMENT_OBSERVATION_INCOMPLETE"
            _neutralize(group, snapshot, reason, model_status=model_status)
            audit["status"] = "INCOMPLETE_OBSERVATION"
            audit["environment_not_applied_reason"] = _normalized_reason(reason)
            audit["environment_fallback_reason"] = reason
        elif model_artifact is None:
            _neutralize(group, snapshot, model_reason, model_status=model_status)
            audit["status"] = "COLLECTED_PENDING_PROMOTION"
            audit["environment_not_applied_reason"] = _normalized_reason(model_reason)
            audit["environment_fallback_reason"] = model_reason
        else:
            try:
                offsets = _environment_offsets(group, snapshot, model_artifact["model"])
                model_id = str(model_artifact.get("model_id", ""))
                for row, offset in zip(group, offsets):
                    _attach_observation(row, snapshot)
                    row["environment_score"] = round(offset, 8)
                    row["environment_adjustment_applied"] = True
                    row["environment_application_status"] = "PROMOTED_MODEL_APPLIED"
                    row["environment_model_status"] = "promoted"
                    row["environment_not_applied_reason"] = ""
                    row["environment_model_id"] = model_id
                    row["environment_model_sha256"] = audit["environment_model_sha256"]
                audit.update(
                    status="PROMOTED_MODEL_APPLIED",
                    environment_adjustment_applied=True,
                    environment_model_id=model_id,
                )
            except ValueError as exc:
                reason = f"ENVIRONMENT_MODEL_NOT_APPLIED: {exc}"
                _neutralize(group, snapshot, reason, model_status=model_status)
                audit["status"] = "MODEL_REJECTED_FOR_RACE"
                audit["environment_not_applied_reason"] = _normalized_reason(reason)
                audit["environment_fallback_reason"] = reason
        race_audits.append(audit)

    return rows, {
        "policy": "All obtainable official environment values are prediction inputs and learning evidence. "
                  "Only an explicitly promoted model may create a non-zero adjustment.",
        "production_model_path": str(model_path),
        "production_model_available": model_artifact is not None,
        "races": race_audits,
    }


def _load_promoted_model(path: Path) -> tuple[dict[str, object] | None, str]:
    if not path.exists():
        return None, "NO_PROMOTED_ENVIRONMENT_MODEL"
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "INVALID_ENVIRONMENT_MODEL_ARTIFACT"
    if artifact.get("status") != "PROMOTED" or artifact.get("production_model_changed") is not True:
        return None, "ENVIRONMENT_MODEL_NOT_PROMOTED"
    model = artifact.get("model")
    if not isinstance(model, dict) or model.get("schema_version") != 1:
        return None, "ENVIRONMENT_MODEL_SCHEMA_MISMATCH"
    return artifact, ""


def _snapshot_complete(snapshot: dict[str, object], surface: str) -> bool:
    if snapshot.get("environment_schema_version") != ENVIRONMENT_SCHEMA_VERSION:
        return False
    if snapshot.get("value_type") != "observed":
        return False
    if snapshot.get("collection_complete") is False:
        return False
    required = ["weather", "track_condition", "weather_captured_at", "moisture_measured_at"]
    if surface in {"芝", "障害"}:
        required += ["cushion", "cushion_measured_at", "turf_moisture_goal", "turf_moisture_corner4"]
    elif surface in {"ダ", "ダート"}:
        required += ["dirt_moisture_goal", "dirt_moisture_corner4"]
    else:
        return False
    if any(snapshot.get(key) in (None, "") for key in required):
        return False
    required_sources = ["weather", "moisture"]
    if surface in {"芝", "障害"}:
        required_sources.append("cushion")
    source_sha256s = _source_sha256s(snapshot)
    return all(_is_sha256(source_sha256s.get(source, "")) for source in required_sources)


def _neutralize(
    group: list[dict[str, object]],
    snapshot: dict[str, object] | None,
    reason: str,
    *,
    model_status: str,
) -> None:
    for row in group:
        if snapshot:
            _attach_observation(row, snapshot)
        else:
            _attach_observation_provenance(row, None)
        row["environment_score"] = 0.0
        row["environment_adjustment_applied"] = False
        row["environment_application_status"] = "NEUTRAL_PENDING_VALIDATED_MODEL"
        row["environment_model_status"] = model_status
        row["environment_not_applied_reason"] = _normalized_reason(reason)
        row["environment_fallback_reason"] = reason


def _attach_observation(row: dict[str, object], snapshot: dict[str, object]) -> None:
    for key in (
        "environment_schema_version", "captured_at", "cushion_measured_at",
        "moisture_measured_at", "cushion", "turf_moisture_goal",
        "turf_moisture_corner4", "dirt_moisture_goal", "dirt_moisture_corner4",
        "turf_moisture_goal_condition", "turf_moisture_corner4_condition",
        "dirt_moisture_goal_condition", "dirt_moisture_corner4_condition",
        "moisture_note",
    ):
        row[f"target_environment_{key}"] = snapshot.get(key, "")
    _attach_observation_provenance(row, snapshot)


def _attach_observation_provenance(
    row: dict[str, object], snapshot: dict[str, object] | None
) -> None:
    provenance = _observation_provenance(snapshot)
    row["environment_observation_id"] = provenance["observation_id"]
    row["environment_observation_sha256"] = provenance["observation_sha256"]
    row["environment_source_sha256s"] = provenance["source_sha256s"]


def _observation_provenance(snapshot: dict[str, object] | None) -> dict[str, object]:
    if not snapshot:
        return {"observation_id": "", "observation_sha256": "", "source_sha256s": {}}
    schema_version = snapshot.get("environment_schema_version", "")
    race_id = str(snapshot.get("race_id", ""))
    captured_at = str(snapshot.get("captured_at", ""))
    observation_id = str(snapshot.get("observation_id", "")).strip()
    if not observation_id and schema_version and race_id and captured_at:
        observation_id = f"environment-v{schema_version}:{race_id}:{captured_at}"
    return {
        "observation_id": observation_id,
        "observation_sha256": _canonical_sha256(snapshot),
        "source_sha256s": _source_sha256s(snapshot),
    }


def _source_sha256s(snapshot: dict[str, object]) -> dict[str, str]:
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        return {}
    hashes: dict[str, str] = {}
    for name, source in sorted(sources.items()):
        if not isinstance(source, dict):
            continue
        digest = str(source.get("sha256", "")).strip().lower()
        if digest:
            hashes[str(name)] = digest
    return hashes


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _environment_model_status(model_artifact: dict[str, object] | None, reason: str) -> str:
    if model_artifact is not None:
        return "promoted"
    return {
        "NO_PROMOTED_ENVIRONMENT_MODEL": "not_promoted",
        "ENVIRONMENT_MODEL_NOT_PROMOTED": "not_promoted",
        "INVALID_ENVIRONMENT_MODEL_ARTIFACT": "invalid_artifact",
        "ENVIRONMENT_MODEL_SCHEMA_MISMATCH": "schema_mismatch",
    }.get(reason, "unavailable")


def _normalized_reason(reason: str) -> str:
    if reason in {"NO_PROMOTED_ENVIRONMENT_MODEL", "ENVIRONMENT_MODEL_NOT_PROMOTED"}:
        return "no_promoted_environment_model"
    if reason.startswith("ENVIRONMENT_MODEL_NOT_APPLIED:"):
        return "environment_model_rejected_for_race"
    return reason.lower()


def _environment_offsets(
    rows: list[dict[str, object]], snapshot: dict[str, object], model: dict[str, object]
) -> list[float]:
    surface = str(rows[0].get("target_surface", ""))
    supported = set(model.get("supported_surfaces") or ["芝"])
    if surface not in supported:
        raise ValueError(f"unsupported surface {surface}")
    target_date = str(rows[0].get("target_race_date", ""))
    training_end = str(model.get("training_end_date", ""))
    if not target_date or not training_end or date.fromisoformat(training_end) >= date.fromisoformat(target_date):
        raise ValueError("training cutoff is not strictly before target race")
    family = str(model.get("family", ""))
    env_values = _environment_values(snapshot, surface, family)
    horse_values = [[float(row[name]) for name in HORSE_FEATURES] for row in rows]
    means = [sum(column) / len(column) for column in zip(*horse_values)]
    feature_names = list(model.get("feature_names") or [])
    scales = [float(value) for value in list(model.get("scales") or [])]
    coefficients = [float(value) for value in list(model.get("coefficients") or [])]
    expected_names = [f"{horse}*{env}" for horse in HORSE_FEATURES for env in env_values]
    if feature_names != expected_names or len(scales) != len(expected_names) or len(coefficients) != len(expected_names):
        raise ValueError("model feature schema mismatch")
    if any(not math.isfinite(value) or value <= 0 for value in scales + [1.0]):
        raise ValueError("invalid model scales")
    offsets = []
    for values in horse_values:
        design = [
            (value - mean) * env_values[env_name]
            for value, mean, _horse in zip(values, means, HORSE_FEATURES)
            for env_name in env_values
        ]
        offset = sum(value / scale * coefficient for value, scale, coefficient in zip(design, scales, coefficients))
        if not math.isfinite(offset):
            raise ValueError("non-finite environment score")
        offsets.append(offset)
    return offsets


def _environment_values(snapshot: dict[str, object], surface: str, family: str) -> dict[str, float]:
    weather = str(snapshot.get("weather", ""))
    condition = str(snapshot.get("track_condition", ""))
    if weather not in {"晴", "曇", "雨", "小雨", "雪", "小雪"} or condition not in CONDITIONS:
        raise ValueError("invalid weather or track condition")
    values = {
        "rain": float(weather in {"雨", "小雨"}),
        "cloud": float(weather == "曇"),
        "snow": float(weather in {"雪", "小雪"}),
    }
    if surface in {"芝", "障害"}:
        values.update(
            cushion=float(snapshot["cushion"]) - 10,
            moisture_goal=(float(snapshot["turf_moisture_goal"]) - 8) / 4,
            moisture_corner4=(float(snapshot["turf_moisture_corner4"]) - 8) / 4,
        )
    elif surface in {"ダ", "ダート"}:
        values.update(
            moisture_goal=(float(snapshot["dirt_moisture_goal"]) - 8) / 4,
            moisture_corner4=(float(snapshot["dirt_moisture_corner4"]) - 8) / 4,
        )
    else:
        raise ValueError(f"unsupported surface {surface}")
    values["condition"] = CONDITIONS[condition]
    if family == "quadratic":
        if "cushion" in values:
            values["cushion_squared"] = values["cushion"] ** 2
        values["moisture_goal_squared"] = values["moisture_goal"] ** 2
        values["moisture_corner4_squared"] = values["moisture_corner4"] ** 2
    elif family != "linear":
        raise ValueError("invalid environment feature family")
    return values


def _canonical_sha256(value: object) -> str:
    if value is None:
        return ""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
