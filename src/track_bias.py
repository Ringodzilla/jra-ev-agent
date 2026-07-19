from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


BIAS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "track_biases.json"
COURSE_LEARNING_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "course_learning.json"


def track_bias_adjustment(
    *,
    track: str,
    surface: str,
    distance: float,
    track_condition: str,
    frame_number: str,
    front_rate: float,
    learning_config: dict[str, Any] | None = None,
) -> dict[str, object]:
    style = _running_style(front_rate)
    learned_frame = learned_frame_adjustment(
        track=track,
        surface=surface,
        distance=distance,
        frame_number=frame_number,
        learning_config=learning_config,
    )
    learned_frame_bias = _to_float(learned_frame.get("learned_frame_bias"), 0.0)
    learned_frame_scope = str(learned_frame.get("learned_frame_scope", ""))
    profile = _find_profile(track=track, surface=surface, distance=distance)
    if not profile:
        if not learned_frame_scope and learned_frame_bias == 0.0:
            return {
                "track_bias_score": 0.0,
                "track_bias_style": "neutral",
                "track_bias_strength": 0.0,
                "track_bias_frame": 0.0,
                "track_bias_learned_frame": 0.0,
                "track_bias_learned_scope": "",
            }
        return {
            "track_bias_score": round(learned_frame_bias, 4),
            "track_bias_style": style,
            "track_bias_strength": round(_to_float(learned_frame.get("learned_frame_confidence"), 0.0), 4),
            "track_bias_frame": round(learned_frame_bias, 4),
            "track_bias_learned_frame": round(learned_frame_bias, 4),
            "track_bias_learned_scope": learned_frame_scope,
        }

    style_bias = _to_float(profile.get("running_style_bias", {}).get(style), 0.0)
    condition_bias = _to_float(
        profile.get("condition_adjustments", {})
        .get(track_condition, {})
        .get(style),
        0.0,
    )
    base_frame_bias = _to_float(profile.get("frame_bias", {}).get(str(frame_number).strip()), 0.0)
    frame_bias = base_frame_bias + learned_frame_bias
    score = _clamp(style_bias + condition_bias + frame_bias, -0.6, 0.6)

    return {
        "track_bias_score": round(score, 4),
        "track_bias_style": style,
        "track_bias_strength": round(
            _clamp(
                _to_float(profile.get("bias_strength"), 0.0)
                + _to_float(learned_frame.get("learned_frame_confidence"), 0.0) * 0.25,
                0.0,
                1.0,
            ),
            4,
        ),
        "track_bias_frame": round(frame_bias, 4),
        "track_bias_learned_frame": round(learned_frame_bias, 4),
        "track_bias_learned_scope": str(learned_frame.get("learned_frame_scope", "")),
    }


def learned_frame_adjustment(
    *,
    track: str,
    surface: str,
    distance: float,
    frame_number: str,
    learning_config: dict[str, Any] | None = None,
) -> dict[str, object]:
    config = learning_config if learning_config is not None else _load_course_learning_config()
    frame = str(frame_number).strip()
    if not frame:
        return {"learned_frame_bias": 0.0, "learned_frame_scope": "", "learned_frame_confidence": 0.0}

    matches = [
        (profile, _profile_specificity(profile))
        for profile in list(config.get("frame_adjustments") or [])
        if _frame_profile_matches(profile, track=track, surface=surface, distance=distance)
    ]
    weighted_bias = 0.0
    weight_total = 0.0
    scopes: list[str] = []
    for profile, specificity in matches:
        confidence = _clamp(_to_float(profile.get("confidence"), 0.0), 0.0, 1.0)
        sample_size = max(0.0, _to_float(profile.get("sample_size"), 0.0))
        evidence_weight = confidence * (1.0 + min(sample_size, 200.0) / 50.0) * (1.0 + specificity * 0.18)
        if evidence_weight <= 0:
            continue
        frame_bias = _to_float(dict(profile.get("frame_bias") or {}).get(frame), 0.0)
        weighted_bias += frame_bias * evidence_weight
        weight_total += evidence_weight
        scopes.append(str(profile.get("scope") or profile.get("id") or "learned").strip())

    if weight_total <= 0:
        return {"learned_frame_bias": 0.0, "learned_frame_scope": "", "learned_frame_confidence": 0.0}
    return {
        "learned_frame_bias": round(_clamp(weighted_bias / weight_total, -0.12, 0.12), 4),
        "learned_frame_scope": "+".join(dict.fromkeys(scope for scope in scopes if scope)),
        "learned_frame_confidence": round(min(1.0, weight_total / 5.0), 4),
    }


def course_pace_adjustment(
    *,
    track: str,
    surface: str,
    distance: float,
    track_condition: str,
    pace_mix_high: float,
    front_rate: float,
    closing_strength: float,
    front_competitor_count: int,
    learning_config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Return learned pace/style adjustment for the target race context.

    The learning config can contain broad distance rules and narrow
    track/surface/distance rules. Matching rules are blended by confidence,
    sample size, and specificity so accumulated race evidence can decide
    whether an effect is global or venue-specific.
    """
    style = _running_style(front_rate)
    config = learning_config if learning_config is not None else _load_course_learning_config()
    matches = [
        (profile, _profile_specificity(profile))
        for profile in list(config.get("pace_adjustments") or [])
        if _pace_profile_matches(
            profile,
            track=track,
            surface=surface,
            distance=distance,
            track_condition=track_condition,
            pace_mix_high=pace_mix_high,
            front_competitor_count=front_competitor_count,
        )
    ]
    if not matches:
        return {
            "course_pace_adjustment": 0.0,
            "course_pace_scope": "",
            "course_pace_style": style,
            "course_pace_confidence": 0.0,
        }

    weighted_adjustment = 0.0
    weight_total = 0.0
    scopes: list[str] = []
    for profile, specificity in matches:
        confidence = _clamp(_to_float(profile.get("confidence"), 0.0), 0.0, 1.0)
        sample_size = max(0.0, _to_float(profile.get("sample_size"), 0.0))
        evidence_weight = confidence * (1.0 + min(sample_size, 200.0) / 50.0) * (1.0 + specificity * 0.18)
        if evidence_weight <= 0:
            continue

        style_bias = _to_float(dict(profile.get("style_bias") or {}).get(style), 0.0)
        closing_penalty = _closing_deficiency_adjustment(profile, style=style, closing_strength=closing_strength)
        adjustment = _clamp(style_bias + closing_penalty, -0.30, 0.18)
        weighted_adjustment += adjustment * evidence_weight
        weight_total += evidence_weight
        scopes.append(str(profile.get("scope") or profile.get("id") or "learned").strip())

    if weight_total <= 0:
        return {
            "course_pace_adjustment": 0.0,
            "course_pace_scope": "",
            "course_pace_style": style,
            "course_pace_confidence": 0.0,
        }

    return {
        "course_pace_adjustment": round(_clamp(weighted_adjustment / weight_total, -0.30, 0.18), 4),
        "course_pace_scope": "+".join(dict.fromkeys(scope for scope in scopes if scope)),
        "course_pace_style": style,
        "course_pace_confidence": round(min(1.0, weight_total / 5.0), 4),
    }


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    with BIAS_CONFIG_PATH.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


@lru_cache(maxsize=1)
def _load_course_learning_config() -> dict[str, Any]:
    if not COURSE_LEARNING_CONFIG_PATH.exists():
        return {"version": "", "pace_adjustments": []}
    with COURSE_LEARNING_CONFIG_PATH.open("r", encoding="utf-8") as file_obj:
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


def _pace_profile_matches(
    profile: dict[str, Any],
    *,
    track: str,
    surface: str,
    distance: float,
    track_condition: str,
    pace_mix_high: float,
    front_competitor_count: int,
) -> bool:
    if profile.get("enabled") is False:
        return False
    profile_track = str(profile.get("track", "")).strip()
    if profile_track and profile_track != track:
        return False
    profile_surface = str(profile.get("surface", "")).strip()
    if profile_surface and profile_surface != surface:
        return False
    profile_distance = _to_float(profile.get("distance"), 0.0)
    if profile_distance > 0:
        tolerance = max(0.0, _to_float(profile.get("distance_tolerance"), 50.0))
        if abs(profile_distance - distance) > tolerance:
            return False
    condition = str(profile.get("track_condition", "*")).strip()
    if condition not in {"", "*"} and condition != track_condition:
        return False
    if pace_mix_high < _to_float(profile.get("high_pace_min"), 0.0):
        return False
    if front_competitor_count < int(_to_float(profile.get("front_competitor_min"), 0.0)):
        return False
    return True


def _frame_profile_matches(
    profile: dict[str, Any],
    *,
    track: str,
    surface: str,
    distance: float,
) -> bool:
    if profile.get("enabled") is False:
        return False
    profile_track = str(profile.get("track", "")).strip()
    if profile_track and profile_track != track:
        return False
    profile_surface = str(profile.get("surface", "")).strip()
    if profile_surface and profile_surface != surface:
        return False
    profile_distance = _to_float(profile.get("distance"), 0.0)
    if profile_distance > 0:
        tolerance = max(0.0, _to_float(profile.get("distance_tolerance"), 50.0))
        if abs(profile_distance - distance) > tolerance:
            return False
    return True


def _profile_specificity(profile: dict[str, Any]) -> int:
    score = 0
    if str(profile.get("track", "")).strip():
        score += 3
    if str(profile.get("surface", "")).strip():
        score += 2
    if _to_float(profile.get("distance"), 0.0) > 0:
        score += 2
    if str(profile.get("track_condition", "*")).strip() not in {"", "*"}:
        score += 1
    return score


def _closing_deficiency_adjustment(profile: dict[str, Any], *, style: str, closing_strength: float) -> float:
    rule = profile.get("closing_deficiency_penalty")
    if not isinstance(rule, dict):
        return 0.0
    if closing_strength >= _to_float(rule.get("threshold"), 0.0):
        return 0.0
    return _to_float(dict(rule.get("style_bias") or {}).get(style), 0.0)


def _to_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "None"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
