from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


BIAS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "track_biases.json"


def track_bias_adjustment(
    *,
    track: str,
    surface: str,
    distance: float,
    track_condition: str,
    frame_number: str,
    front_rate: float,
) -> dict[str, object]:
    profile = _find_profile(track=track, surface=surface, distance=distance)
    if not profile:
        return {
            "track_bias_score": 0.0,
            "track_bias_style": "neutral",
            "track_bias_strength": 0.0,
            "track_bias_frame": 0.0,
        }

    style = _running_style(front_rate)
    style_bias = _to_float(profile.get("running_style_bias", {}).get(style), 0.0)
    condition_bias = _to_float(
        profile.get("condition_adjustments", {})
        .get(track_condition, {})
        .get(style),
        0.0,
    )
    frame_bias = _to_float(profile.get("frame_bias", {}).get(str(frame_number).strip()), 0.0)
    score = _clamp(style_bias + condition_bias + frame_bias, -0.6, 0.6)

    return {
        "track_bias_score": round(score, 4),
        "track_bias_style": style,
        "track_bias_strength": round(_to_float(profile.get("bias_strength"), 0.0), 4),
        "track_bias_frame": round(frame_bias, 4),
    }


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    with BIAS_CONFIG_PATH.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _find_profile(*, track: str, surface: str, distance: float) -> dict[str, Any] | None:
    if not track or not surface or distance <= 0:
        return None
    for profile in _load_config().get("profiles", []):
        if str(profile.get("track", "")).strip() != track:
            continue
        if str(profile.get("surface", "")).strip() != surface:
            continue
        if abs(_to_float(profile.get("distance"), 0.0) - distance) > 50:
            continue
        return profile
    return None


def _running_style(front_rate: float) -> str:
    if front_rate >= 0.78:
        return "front"
    if front_rate >= 0.58:
        return "stalker"
    if front_rate >= 0.36:
        return "midpack"
    return "closer"


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
